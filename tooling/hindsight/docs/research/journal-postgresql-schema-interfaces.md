# Protected PostgreSQL schema and interfaces

This source-only plan records the [accepted schema boundaries](https://github.com/nisavid/agents/issues/106#issuecomment-5565174781), [accepted storage and adapter choices](https://github.com/nisavid/agents/issues/106#issuecomment-5565270427), and [accepted body/reference interfaces](https://github.com/nisavid/agents/issues/105#issuecomment-5564764189). It defines the logical schema and protected interfaces. It authorizes no implementation, migration, role change, deployment, qualification, or live operation.

Logical SQL identifiers may change during implementation. Relation identities, keys, value and reference domains, callable behavior, authority separation, migration states, and evidence obligations may not.

## 1. Source and database boundary

The source package retains four separate contracts:

1. Structural codec: validates the closed kind/version registry and produces exact canonical bytes with one trailing LF.
2. Reference resolver: verifies typed transitive closure, missing bodies, kinds, versions, digests, cycles, and identity conflicts.
3. Current-state verifier: consumes only exact typed current-slot projections.
4. Historical-reader dispatcher: authenticates an exact selector and invokes one revision-pinned nonauthorizing reader.

PostgreSQL stores exact bytes, recomputes `sha256(bytea)` and `octet_length(bytea)`, and enforces relational identity, references, currentness, transitions, uniqueness, and concurrency. It does not implement a second complete decoder. No extension is required.

Every protected call receives source-decoded types plus exact bytes or exact references. PostgreSQL independently derives all current database operands. Storing a body alone creates no authority.

## 2. Closed source inventory and shared types

### 2.1 Body registry

The accepted source defines 126 successor body kinds and 21 compatibility kinds.

Successor members, in source order:

```text
BS001 publication-journal
BS002 publication-proof
BS003 publication-deadline-receipt
BS004 publication-mutation-receipt
BS005 publication-verification-receipt
BS006 publication-verification-mismatch-observation
BS007 publication-terminal-verification-failure
BS008 publication-verification-unable-observation
BS009 support-profile
BS010 controller-host-binding
BS011 host-binding
BS012 endpoint-binding
BS013 deployment-topology-binding
BS014 clock-envelope
BS015 protected-time-observation
BS016 qualification-plan
BS017 qualification-run-stimulus
BS018 qualification-plan-acceptance
BS019 qualification-class-result
BS020 qualification-receipt
BS021 deployment-attestation
BS022 evidence-campaign
BS023 evidence-campaign-plan
BS024 evidence-campaign-plan-acceptance
BS025 canonical-claim-registry
BS026 canonical-claim-definition
BS027 canonical-claim-predicate
BS028 canonical-deployment-matrix
BS029 authority-gate-conformance-prestate
BS030 authority-gate-fixture-state
BS031 canonical-oracle-registry
BS032 oracle-definition
BS033 oracle-contract
BS034 oracle-projection
BS035 evidence-record
BS036 evidence-run-failure
BS037 evidence-run-result
BS038 evidence-invalidity-finding
BS039 evidence-tier-result
BS040 historical-corpus-plan
BS041 historical-corpus-plan-acceptance
BS042 historical-corpus-coverage-projection
BS043 historical-reader-execution-binding
BS044 failed-deployment-result
BS045 evidence-disposition-authorization-receipt
BS046 evidence-record-invalidation
BS047 evidence-campaign-supersession
BS048 deployment-admission-policy
BS049 immutable-artifact
BS050 evidence-identity
BS051 profile-component
BS052 boot-environment-configuration
BS053 clock-configuration
BS054 filesystem-configuration
BS055 hardware-configuration
BS056 operating-system-configuration
BS057 postgresql-component-configuration
BS058 storage-configuration
BS059 virtualization-configuration
BS060 macos-local-live-projection
BS061 role-grant-set
BS062 writer-service-identity
BS063 writer-inventory
BS064 deployment-evidence-acquisition
BS065 contract-body
BS066 procedure-contract
BS067 tool-contract
BS068 evidence-limits
BS069 closure-policy-limits
BS070 evidence-stimulus
BS071 evidence-case-matrix
BS072 randomized-schedule
BS073 qualification-acceptance-thresholds
BS074 qualification-abort-policy
BS075 evidence-retention-policy
BS076 private-artifact-policy
BS077 public-projection-policy
BS078 private-artifact-provenance
BS079 real-artifact-binding
BS080 controlled-private-evidence-package
BS081 bounded-public-evidence-projection
BS082 independent-evidence-review-receipt
BS083 postgresql-settings
BS084 historical-fixture
BS085 historical-generator
BS086 successor-projection-contract
BS087 failure-evidence
BS088 operation-plan
BS089 operation-grant
BS090 operation-grant-revocation
BS091 operation-retry-limits
BS092 operation-reconciliation-limits
BS093 operation-budget-limits
BS094 operation-work-request
BS095 operation-work-preflight-result
BS096 operation-work-pre-reservation-refusal
BS097 operation-work-reservation
BS098 operation-work-start
BS099 operation-work-committed-result
BS100 operation-work-transaction-resolution-outcome
BS101 operation-work-ambiguity-query-outcome
BS102 operation-work-conclusive-noncommit-result
BS103 transaction-identity
BS104 reconciliation-subject
BS105 operation-approval
BS106 operation-authorization-receipt
BS107 operation-authority-revocation
BS108 pre-stage-expiry-observation
BS109 qualification-clock-epoch
BS110 protected-rollback-ciphertext
BS111 target-relation-identity
BS112 target-column-identity
BS113 target-row-identity
BS114 target-surface-contract
BS115 target-cohort-membership
BS116 target-cohort-projection
BS117 target-mutation-image
BS118 target-apply-payload
BS119 target-restore-payload
BS120 restore-payload-conversion
BS121 rollback-preimage-binding
BS122 recovery-refusal-observation
BS123 recovery-ambiguity-observation
BS124 recovery-fence-observation
BS125 recovery-advancement-observation
BS126 recovery-unproven-observation
```

Every `BS` member has the exact `hindsight-postgresql-…/1` kind shown in the pinned [body registry](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L104-L233).

Compatibility members are:

```text
BC001 manifest-basis
BC002 frozen-reader-registry
BC003 legacy-reader-success
BC004 legacy-reader-failure
BC005 legacy-restore-content
BC006 final-manifest
BC007 artifact-exclusion
BC008 approval
BC009 closure-case-binding
BC010 closure-observation
BC011 closure-qualified-sample-evidence
BC012 closure-attested-invalidation-evidence
BC013 closure-comparison-evidence
BC014 closure-failure-evidence
BC015 realized-admission-evidence
BC016 realized-acl-evidence
BC017 zero-live-writer-evidence
BC018 service-disable-evidence
BC019 persistent-legacy-fence-evidence
BC020 origin-fence-manifest-binding
BC021 active-fence-manifest-adoption
```

These map exactly to the pinned [compatibility registry](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L973-L1000).

Storage classification is closed:

- `BS080` uses private relation `RB06`, never ordinary storage.
- `BS110` and its ciphertext octets use `RB02`, never ordinary storage.
- `BS095` is a transaction-local, nonpersisted preflight response.
- The other 123 successor kinds and all 21 compatibility kinds are admitted by `RB01`. A protected operation stores a body there only when its accepted durable fact or dependency requires it.

### 2.2 Source-derived obligation manifests

The left-hand members below are normalized accepted-source identities, extracted before assigning proposal IDs. They are not defined by §§3 or 5. Their anchors are the immutable [successor body and selector definitions](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L104-L233), [exact current-slot registry](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2096-L2138), [operation-work contract](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2390-L2764), [evidence and qualification interfaces](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4400-L4614), [admission contract](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4623-L4930), [access model](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4932-L5117), [publication protocol](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md#L620-L944), [restart state model](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-restart-design.md#L62-L479), and [compatibility contract](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L160-L1026). These mappings establish identity and navigation membership only. Semantic closure is established separately in §2.3.

#### 2.2.1 Source-member disposition ledger

The sibling [source-member inventory](./journal-postgresql-source-member-inventory.md), with UTF-8 SHA-256
`c4f8ca867f0fff9c7576c9e5ed076095bbaa25f704952243b51f2ce5821ba84f`, is the
sole expected-member roster. Its complete expansion is the set `I`. Each member
retains the inventory identity
`(accepted_revision,source_file,source_contract_or_operation,exact_member_path,branch_or_variant)`.
No SQL identifier, BM/MRR/MRE/DLI/DRP/PFC identity, prospective check, relation
count, or row emitted by this plan participates in `I`.

Every member in `I` has exactly one primary disposition:

- `RELATION`: a production stable key, uniqueness rule, immutable fact, or
  protected equality stored under the relation named below;
- `CURRENT`: one of the 21 production selector classes, stored by the
  corresponding exact §3.8 anchor;
- `CALLABLE_PREDICATE`: a typed reference, scalar copy, branch predicate, or
  equality consumed by the named protected callable without requiring a
  separately relationalized scalar;
- `ORDINARY_BODY`: a nonauthorizing member whose exact closed grammar remains
  in RB01 and the source codec/resolver;
- `PRIVATE` or `CIPHERTEXT`: a separately protected RB06 or RB02/RB05 member;
- `SOURCE_INTERFACE`: a source-owned codec, resolver, historical-reader, or
  conformance-preimage member that PostgreSQL does not reinterpret; or
- `SOURCE_GAP`: an exact accepted-source absence that this record must preserve
  instead of filling with an issue-106 invention.

The disposition function is independent of the proposal crosswalk and is
evaluated in this order:

1. Inventory members explicitly marked `PRIVATE`, `CIPHERTEXT`,
   `SOURCE_ONLY`, `MATRIX_BODY_GAP`, or `SOURCE_UNASSIGNED` retain the
   corresponding disposition.
2. Each row of the inventory's 21-class production selector table is
   `CURRENT`. Each of its six AuthorityGate key-preimage families, including
   the added literal `slot_class`, is `SOURCE_INTERFACE` and is never a
   production key.
3. Each production stable-key row is `RELATION` except the exact-body and
   source-interface exceptions enumerated below.
4. Every ordinary `REF` member not already classified is
   `CALLABLE_PREDICATE`. Its indivisible kind/version/digest identity and
   recursive body closure use RB01 plus FB01/FB02; its owning protected
   operation still compares the exact source-decoded reference on applied,
   exact-replay, and refusal/no-effect branches. This does not create a SQL
   decoder or a column for every nested reference.
5. Every PRD, QEQ, DEQ, PLV, PLR, DFR, NMR, evidence-operation, and
   publication/private/manifest/activation copy member is
   `CALLABLE_PREDICATE` unless a preceding rule gives it a relation/current or
   nonordinary disposition.
6. Every remaining ordinary scalar or closed-union member is
   `ORDINARY_BODY`. Its canonical source bytes remain authoritative.

The production stable-key exceptions are closed:

| Inventory stable-key family | Primary disposition and mapping |
|---|---|
| `ContractBody`; `RelationIdentity`; `DiscoveryRoot`; `DigestBinding`; `DependencyEdge`; compatibility `RoleIdentity`; `WriterServiceIdentity` body members; `FencePredicateObservation`; `RawIdentityMember`; `HistoricalIdentityMember`; `ReaderContractMember`; `ExclusionBinding` | `ORDINARY_BODY`. The source resolver enforces the nested key; only separately listed relation projections are relational. |
| `HistoricalReaderExecutionBinding` | `SOURCE_INTERFACE`. The revision-pinned dispatcher resolves the exact member digest; RC04 records the nonauthorizing execution outcome. |
| accounting, work, transaction, and recovery keys | `RELATION` at RS10, RW01–RW15, and RS13–RS15 as mapped by contract in this section. |
| operation-grant and operation-authority keys | `RELATION` at RA01–RA08 and RS11–RS12. |
| evidence-plan, campaign, record, result, disposition, qualification, policy, clock, attestation, and failed-result keys | `RELATION` at RE01–RE25 and RS04, RS06–RS07, RS16–RS17. |
| private package, projection, and review keys | `PRIVATE` for RB06; otherwise `RELATION` at RB07–RB12. |
| publication, ciphertext/preimage adoption, stage, verification, target-generation, and lineage keys | `RELATION` at RB03–RB05, RP01–RP15, RS09, and RS19–RS21; ciphertext bytes retain `CIPHERTEXT`. |
| `InventoryObservation` | `RELATION` at RC02 with the complete deterministic source identity. |
| `ClosureCaseBinding` | `RELATION` at RC05: stable `closure_case_id` plus the separately unique complete case key. |
| `ClosureObservation` | `RELATION` at RC13: stable deterministic `closure_observation_id` plus the separately unique attempt and request keys. |
| `ServiceDisableEvidence`, `PersistentLegacyFenceEvidence`, origin/adoption bindings, and current legacy-fence binding | `RELATION` at RC26–RC29 and RS08. |
| `ReferencedEvidence` | `RELATION` at RC18 under stable `(contract_kind,contract_version,body_digest)`. |
| exclusion approval receipt | `RELATION` at RC21, RC28, and RC29 under stable `approval_digest`. |
| reserved-activation immutable binding and selected current value | `RELATION` for RX01 and `CURRENT` for the distinct RS02/RS03/RS18 selector members. Each source member has one primary disposition under the inventory's target-surface key and unique numeric epoch. |

The remaining independently named families map as follows:

| Inventory family | Complete primary mapping |
|---|---|
| 21 production current selectors | `CURRENT`, one-for-one with the same-named §3.8 row; exact key and present-value arms come from the inventory, not the anchor definition. |
| 22 tagged oracle-projection value arms, the eight AuthorityGate prestate optionals, ten gate tags, and finite target-state/value unions | `ORDINARY_BODY` for each nonreference grammar member; each typed `REF` descendant independently receives the earlier `CALLABLE_PREDICATE` disposition. Closed tags, branches, order, and cardinality remain exact. |
| six AuthorityGate base preimages and all 21 concrete `slot_class` additions | `SOURCE_INTERFACE`; IA02–IA04 compare the source-canonical bytes and digest without changing production keys. |
| PRD001–PRD065 and QEQ001–QEQ042 | `CALLABLE_PREDICATE` in FQ01, FQ02, and FAD02 according to each inventory row's named role position and equality. |
| DEQ001–DEQ063, PLV001–PLV078, and PLR001–PLR098 | `CALLABLE_PREDICATE` in FAD02, with IA10 supplying only the locked current schema/build projection. |
| DFR001–DFR042 and NMR001–NMR035 | `CALLABLE_PREDICATE` in FAD02's exact refusal/failure branch. NMR semantics remain fixed even where a concrete matrix row is `SOURCE_GAP`. |
| the eleven evidence-operation source members | `CALLABLE_PREDICATE` at FE01–FE11, one source operation per protected callable. |
| publication, private-evidence, manifest-envelope, closure, fence, adoption, and activation copy members | `CALLABLE_PREDICATE` at the exact protected callable named by the inventory row, with a DRP or PFC only when the destination is separately stored. |
| total historical-reader constructor, finite selectors, all 14 immediate grant-lineage selectors, and their enumerated recursive parent edges | `SOURCE_INTERFACE`. Exact reader bodies and RC04 outcomes are protected, but PostgreSQL does not choose or reinterpret a historical selector. |
| private package members and retained ciphertext octets | `PRIVATE` at RB06 or `CIPHERTEXT` at RB02/RB05; neither can enter RB01. |

Every inventory `REF` therefore emits one
`SourceReferenceProjectionMember` identified by the inventory identity, even
when its primary mapping is an RB01-backed protected predicate rather than a
distinct relation column. Every inventory predicate/copy row emits one
`ProtectedSourcePredicate`; its `predicate_id` is the literal inventory row ID
when the inventory names one and otherwise the complete source-member identity.
It retains the exact source path, branch, origin, owner,
applied/replay/refusal or no-effect outcomes, positive access cell, reciprocal
denial, and forward/reverse equality. Exact-body members emit an RB01
kind/version/path disposition; private, ciphertext, and source-interface
members emit their named boundary and no SQL decoding claim.

Two source exceptions remain explicit. `DeploymentAttestation.target_generation`
has primary disposition `SOURCE_GAP` and remains present in exact BS021 bytes because the accepted source assigns no
database-derived value or current-slot equality. `DeploymentAttestation.postgresql_settings`
is a typed exact-body reference whose eventual exact BS021 member may be mirrored at RE24,
but the accepted finalizer does not assign it from a live binding or equate it
to a current PostgreSQL-settings slot. That value-source decision remains
`SOURCE_UNASSIGNED`; the RE24 mirror is an issue-106 relational detail only. The missing
deployment-matrix body and all concrete row/extraction carriers listed as
`MATRIX_BODY_GAP` remain `SOURCE_GAP`; the fixed PRD/QEQ/DEQ/PLV/PLR/DFR/NMR
semantics are not gaps.

Let `D` be the disposition rows produced by these rules and `P` the schema and
callable mappings in §§3–5. The required equalities are `domain(D)=I` and,
for every `RELATION`, `CURRENT`, or `CALLABLE_PREDICATE` member, exactly one
source-owned mapping group in `P` with all of its branch outcomes. Deleting an
inventory member's BM/MRR/MRE/DLI/DRP/PFC mapping and its prospective check
does not change `I` or `D`; the missing `P` member remains a set difference.
Conversely, a proposal member without one inventory identity is an explicit
issue-106 implementation detail and cannot masquerade as accepted source.

The accepted PostgreSQL fact mapping is:

```text
ExactCanonicalBody(BS/BC member enumerated in §2.1) -> RB01
ProtectedRollbackCiphertext descriptor and octets -> RB02
RollbackPreimageBinding admitted candidate -> RB03
PublicationJournal preimage adoption -> RB04
PublicationJournal ciphertext adoption -> RB05
ControlledPrivateEvidencePackage -> RB06
private package-to-public mapping -> RB07
private reviewer authorization fact -> RB08
current private reviewer authorization -> RB09
BoundedPublicEvidenceProjection -> RB10
IndependentEvidenceReviewReceipt -> RB11
current private review receipt -> RB12

OperationGrant -> RA01
OperationGrantRevocation -> RA02
OperationPlan -> RA03
OperationApproval -> RA04
OperationAuthorizationReceipt -> RA05
OperationAuthorityRevocation -> RA06
operation-authority lifecycle state -> RA07
operation-grant lifecycle state -> RA08

EvidenceCampaignPlanAcceptance -> RE01
HistoricalCorpusPlanAcceptance -> RE02
QualificationPlanAcceptance -> RE03
EvidenceCampaign -> RE04
ProtectedTimeObservation -> RE05
DeploymentEvidenceAcquisition -> RE06
EvidenceRecord -> RE07
EvidenceRunFailure -> RE08
EvidenceRunResult -> RE09
EvidenceInvalidityFinding -> RE10
EvidenceRecordInvalidation -> RE11
EvidenceDispositionAuthorizationReceipt -> RE12
EvidenceCampaignSupersession -> RE13
evidence-subject assignment history -> RE14
current evidence subject -> RE15
EvidenceTierResult -> RE16
QualificationClassResult -> RE17
QualificationReceipt -> RE18
DeploymentAdmissionPolicy -> RE19
retired deployment-policy slot/reference pair -> RE20
ClockEnvelope -> RE21
RoleGrantSet -> RE22
WriterInventory -> RE23
DeploymentAttestation -> RE24
FailedDeploymentResult -> RE25

OperationWorkRequest -> RW01
OperationWorkPreReservationRefusal -> RW02
OperationWorkReservation -> RW03
TransactionIdentity -> RW04
OperationWorkStart -> RW05
OperationWorkCommittedResult -> RW06
RecoveryRefusalObservation -> RW07
RecoveryAmbiguityObservation -> RW08
RecoveryFenceObservation -> RW09
RecoveryAdvancementObservation -> RW10
RecoveryUnprovenObservation -> RW11
OperationWorkTransactionResolutionOutcome -> RW12
OperationWorkAmbiguityQueryOutcome -> RW13
OperationWorkConclusiveNoncommitResult -> RW14
ReconciliationSubject -> RW15

PublicationAggregateIdentity -> RP01
PublicationJournal -> RP02
PreStageExpiryObservation -> RP03
PublicationProof -> RP04
PublicationDeadlineReceipt -> RP05
PublicationMutationReceipt -> RP06
target-generation transition -> RP07
lineage genesis -> RP08
lineage successor -> RP09
verification-attempt identity -> RP10
PublicationVerificationUnableObservation -> RP11
PublicationVerificationMismatchObservation -> RP12
PublicationTerminalVerificationFailure -> RP13
current terminal verification state -> RP14
PublicationVerificationReceipt -> RP15

legacy inventory root -> RC01
legacy inventory observation -> RC02
legacy inventory dependency edge -> RC03
historical-reader execution outcome -> RC04
ClosureCaseBinding -> RC05
current closure terminal state -> RC06
closure-attempt reservation -> RC07
current closure-observer lease -> RC08
ClosureQualifiedSampleEvidence -> RC09
ClosureAttestedInvalidationEvidence -> RC10
ClosureComparisonEvidence -> RC11
ClosureFailureEvidence -> RC12
ClosureObservation -> RC13
ManifestBasis -> RC14
ArtifactExclusion -> RC15
CompatibilityApproval -> RC16
FinalManifest -> RC17
manifest referenced-evidence member -> RC18
manifest inventory disposition -> RC19
manifest predecessor selection -> RC20
legacy-fence generation binding -> RC21
current legacy-fence progression -> RC22
RealizedAdmissionEvidence -> RC23
RealizedAclEvidence -> RC24
ZeroLiveWriterEvidence -> RC25
ServiceDisableEvidence -> RC26
PersistentLegacyFenceEvidence -> RC27
OriginFenceManifestBinding -> RC28
ActiveFenceManifestAdoption -> RC29

publication-epoch immutable binding -> RX01
current reserved-epoch state -> RX02
activation commit -> RX03
activation abandonment -> RX04
session-local activation witness -> RX05

ACTIVE_EPOCH -> RS01
ACTIVATION_CAPABILITY -> RS02
ACTIVATION_PROPOSAL -> RS03
CLOCK_ENVELOPE -> RS04
DEPLOYMENT_ATTESTATION -> RS05
DEPLOYMENT_POLICY -> RS06
EVIDENCE_TIER_RESULT -> RS07
LEGACY_FENCE -> RS08
LINEAGE_HEAD -> RS09
OPERATION_ACCOUNTING -> RS10
OPERATION_AUTHORITY -> RS11
OPERATION_GRANT -> RS12
OPERATION_WORK_RESERVATION -> RS13
OPERATION_WORK_START -> RS14
OPERATION_WORK_COMMITTED_RESULT -> RS15
PUBLICATION_EPOCH_HIGH_WATER -> RS16
QUALIFICATION_RECEIPT -> RS17
RESERVED_ACTIVATION -> RS18
ROLE_GRANT_SET -> RS19
TARGET_GENERATION -> RS20
WRITER_INVENTORY -> RS21

Issue106 explicit schema generation -> RM01
Issue106 current deployment state -> RM02
Issue106 immutable deployment transition -> RM03
Issue106 immutable failed-preparation result -> RM04
Issue106 immutable schema manifest -> RM05
Issue106 append-only preparation remediation -> RM06
```

The accepted or directly derived protected callable mapping is:

```text
ensure exact ordinary canonical body -> FB01
resolve exact ordinary canonical body -> FB02
register protected rollback candidate -> FB03
ISSUE_OPERATION_GRANT -> FA01
REVOKE_OPERATION_GRANT -> FA02
ISSUE_OPERATION_PLAN -> FA03
APPROVE_OPERATION_PLAN -> FA04
AUTHORIZE_OPERATION -> FA05
REVOKE_OPERATION_AUTHORITY -> FA06
READ_CURRENT_OPERATION_AUTHORITY -> FA07
ACCEPT_EVIDENCE_CAMPAIGN_PLAN -> FE01
ACCEPT_HISTORICAL_CORPUS_PLAN -> FE02
ACCEPT_QUALIFICATION_PLAN -> FE03
REGISTER_EVIDENCE_CAMPAIGN -> FE04
ACQUIRE_DEPLOYMENT_EVIDENCE -> FE05
OBSERVE_EVIDENCE_TIME -> FE06
REGISTER_EVIDENCE_RUN_RESULT -> FE07
REGISTER_EVIDENCE_INVALIDITY_FINDING -> FE08
SET_CURRENT_EVIDENCE_SUBJECT -> FE09
APPLY_EVIDENCE_DISPOSITION -> FE10
READ_CURRENT_EVIDENCE_TIER_RESULT -> FE11
finalize QualificationClassResult -> FQ01
finalize QualificationReceipt -> FQ02
REGISTER_CONTROLLED_PRIVATE_PACKAGE -> FPV01
READ_CONTROLLED_PRIVATE_PACKAGE_FOR_REVIEW -> FPV02
REGISTER_CONTROLLED_PRIVATE_REVIEW -> FPV03
EXPORT_CURRENT_REVIEWED_EVIDENCE -> FPV04
COMPARE_AND_SET_CURRENT_DEPLOYMENT_ADMISSION_POLICY -> FDP01
register current ClockEnvelope -> FAD01
finalize DeploymentAttestation and reserve epoch -> FAD02
revoke or fence current DeploymentAttestation -> FAD03
publish activation proposal -> FAD04
operation-work committed-result preflight -> FW01
operation-work reservation or pre-reservation refusal -> FW02
operation-work start and TransactionIdentity -> FW03
commit typed ordinary work result -> FW04
resolve committed transaction -> FW05
query transaction ambiguity -> FW06
record conclusive noncommit and close two slots -> FW07
read exact operation-work outcome -> FW08
create or exact-read J -> FJ01
create or exact-read P -> FJ02
create or exact-read R -> FJ03
apply or restore target and commit M -> FM01
verify M and commit one terminal V outcome -> FV01
create closure case -> FC01
reserve closure attempt -> FC02
claim closure attempt -> FC03
take over same closure ordinal -> FC04
finalize closure attempt -> FC05
resolve expired closure attempt -> FC06
begin legacy fence -> FF01
record writer drain -> FF02
record service disable -> FF03
finalize legacy fence -> FF04
ADOPT_ACTIVE_FENCE -> FF05
combined reserved-epoch activation -> FX01
abandon reserved epoch after conclusive noncommit -> FX02
read publication status -> FS01
read compatibility status -> FS02
read schema deployment status -> FS03
```

These are 61 protected callable records. `FB01`, `FB02`, and `FW04` are owner-internal rather than adapter-facing; §7 enumerates every external adapter method. Cross-owner protected dependencies use this separate closed issue106-derived internal-routine registry:

```text
initialize operation accounting -> IA01
issue qualification-receipt time observation -> IA02
issue deployment-attestation time observation -> IA03
issue operation-work reservation observation -> IA04
install reserved publication epoch -> IA05
install activation proposal -> IA06
install compatibility activation metadata -> IA07
initialize successor target and lineage -> IA08
clear abandoned attestation -> IA09
read active protected-schema admission identity -> IA10
```

No adapter or login may invoke an `IA` routine. Each is reachable from exactly the outer entrypoint named in §5.2.2 or §5.4–5.5, shares that entrypoint’s transaction, returns only a provisional transaction-local result, and cannot commit independently.

The closed functional-role mapping is:

```text
schema deployment owner -> O01        ordinary-body owner -> O02
protected-material owner -> O03       operation-authority owner -> O04
evidence owner -> O05                 qualification-finalizer owner -> O06
private-evidence owner -> O07         deployment-policy owner -> O08
admission owner -> O09                operation-work owner -> O10
publication owner -> O11              mutation owner -> O12
verification owner -> O13             closure owner -> O14
legacy-fence owner -> O15             activation owner -> O16
status owner -> O17                   compatibility owner -> O18
preimage constructor -> C01           grant issuer -> C02
plan issuer -> C03                    operation approver -> C04
operation authorizer -> C05           operation revoker -> C06
plan authority -> C07                 evidence producer -> C08
evidence authority -> C09             qualification submitter -> C10
private registrar -> C11              private reviewer -> C12
public evidence exporter -> C13       deployment-policy authority -> C14
admission author -> C15               publication adapter -> C16
continuity client -> C17              verification adapter -> C18
recovery adapter -> C19               closure adapter -> C20
fence adapter -> C21                 status observer -> C22
deployment principal -> C23           ordinary runtime -> C24
historical inventory reader -> C25    unauthenticated/public principal -> PUBLIC
```

The source-derived state-family navigation is:

```text
immutable exact-body insert/replay/conflict -> FB01, RB01
private register/review/export CAS -> FPV01–FPV04, RB06–RB12
grant ACTIVE-to-REVOKED and plan ISSUED-to-APPROVED-to-AUTHORIZED-to-REVOKED -> FA01–FA06, RA07–RA08
accepted evidence plan, campaign, acquisition, run, and nonauthorizing finding registration -> FE01–FE08, RE01–RE10
evidence subject/disposition/current-result replacement -> FE09–FE11, RE05, RE11–RE16, RS07
nonrenewing class and receipt finalization -> FQ01–FQ02, RE17–RE18
deployment-policy expected-current replacement/clear and retirement -> FDP01, RE19–RE20, RS06
request preflight, request-keyed refusal, reservation-keyed uniqueness, full-identity work-slot anchors, start and terminal close -> FW01–FW08, RW01–RW15, RS10, RS13–RS15
J-to-P-to-R-to-M-to-V progression -> FJ01–FJ03, FM01, FV01, RP01–RP15
closure reservation, lease takeover, close, abandonment and exhaustion -> FC01–FC06, RC05–RC13
legacy-fence progression and immutable handoff -> FF01–FF05, RC21–RC29, RS08
reserved epoch to ACTIVE or ABANDONED_FENCED -> FAD02–FAD04, FX01–FX02, RX01–RX05
read-only publication, compatibility and schema status -> FS01–FS03
explicit preparation, append-only remediation, reversible routing, rollback and irreversible finalization -> MT01–MT11
```

The accepted non-PostgreSQL classifications are also closed: structural decoding and canonical encoding, transitive reference resolution, exact current-projection verification, and authenticated historical-reader dispatch remain source-owned; adapter memory holds transient clear capability bytes while `RX05` is the session-local database witness; legacy source artifacts remain read-only inputs to revision-pinned readers; target business rows and external fence/service effects remain outside the journal schema but only their named protected functions may affect them; connection orchestration and commit acknowledgement remain adapter-owned. The C21 adapter’s read-only `U_prefence` gate, local invocation issuance/anti-replay state, exclusive consumption, and `U_consume` gate are likewise adapter-local and nonauthorizing. They cannot substitute for FF01’s independently database-derived `U_fence_start` and `U_fence_commit`, later per-effect gates, or FF05’s `U_adopt`. None is an omitted journal relation or generic database callable.

### 2.3 Source-derived semantic obligation crosswalk

The identity catalogs above are not semantic completeness evidence. The closed obligation registry below records accepted propositions independently of the proposal records that satisfy them. Each row binds an immutable source span, a concrete proposition, its proposal clauses, and a named later check. The source-side identity of an obligation is `(revision, path, line span, proposition kind, normalized proposition)`, never a proposal ID or advertised count.

Source anchors:

- `CAN`: [canonical bytes and digest](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L74-L102).
- `BODY`: [closed successor grammars](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L333-L4261).
- `REF`: [reference resolution](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L310-L326).
- `BODY-ATOMIC`: [complete referenced bytes](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L320-L326) and [atomic run-body/fact insertion](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4987-L5003).
- `SLOT`: [current-slot classes](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2096-L2138) and [exact key grammars](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L3327-L3410).
- `WORK`: [work grammars](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L1369-L1626), [work protocol](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2390-L2675), and [work fault contract](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L6946-L6947).
- `WORK-INIT`: [atomic plan/accounting initialization](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2405-L2408).
- `AUTH`: [operation authority](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2679-L2764).
- `EVAL`: [evidence interfaces](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4932-L5225) and [fault/concurrency matrix](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L6930-L6955).
- `VERDICT`: [protected current-subject ownership and campaign admission](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5136-L5149), [tier-result construction and prerequisite propagation](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5632-L5720), [result-changing race outcomes](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L6935-L6945), and [disposition result replacement](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7132-L7195).
- `DISPOSITION`: [complete authority-subject, receipt, application-time, invalidation, and supersession contract](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7041-L7202).
- `QUAL`: [qualification derivation and ACL](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4400-L4614).
- `ADMIT`: [admission derivation](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4623-L4930) and [failed result](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5610-L5630).
- `PRIVATE`: [private interface and reciprocal ACL](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5052-L5098).
- `ACL`: [protected access model](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4932-L5134).
- `CLAIM`: [accepted falsifiable obligations](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L6865-L6888).
- `PUB`: [publication protocol](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md#L620-L944).
- `RESTART`: [restart state model](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-restart-design.md#L210-L479).
- `HREAD`: [closed historical readers](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L160-L612).
- `CLOSE`: [closure protocol](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L2280-L2445).
- `CLOSE-OPS`: [closure reservation, claim, takeover, and finalization](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L720-L935).
- `FENCE`: [fence and adoption protocol](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L2827-L3150).
- `FENCE-TIME`: [pre-fence, first-transaction, and per-effect temporal authorization](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L2896-L3005) and [active-fence adoption temporal authorization](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L3078-L3110).
- `POLICY-TIME`: [deployment-policy fields](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L997-L1009), [per-effect policy bound](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L2992-L3001), and [cutover policy comparison](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md#L1166-L1171).
- `ROOT-PROJECTION`: [closed body/reference grammars](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L333-L4261), [committed-result binding](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L1578-L1594), [atomic result transition](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2594-L2605), and [activation graph installation](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L3253-L3260).
- `PROJECTION`: [clock, qualification, and attestation grammars](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2846-L2988), [qualified-clock bindings](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5374-L5407), [admission equalities](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4648-L4801), [failed admission](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5610-L5630), [stage grammars](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5732-L6055), [closure bindings](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L1767-L1822), [manifest grammar](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L2462-L2586), and [origin binding](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L3040-L3053).
- `ACTIVATE`: [combined activation effects](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L3134-L3271).
- `CLOCK-MATH`: [exact conservative clock arithmetic](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L6597-L6678).
- `SUPPORT`: [support-profile identity](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7525-L7562) and [deployment admission evidence](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7725-L7779).
- `R1` and `R2`: the [accepted schema boundaries](https://github.com/nisavid/agents/issues/106#issuecomment-5565174781) and [accepted storage and adapter choices](https://github.com/nisavid/agents/issues/106#issuecomment-5565270427).

| ID / source | Accepted proposition | Proposal mapping | Prospective check |
|---|---|---|---|
| SO001 / CAN,BODY | Every admitted body uses its complete closed field/type/union/reference grammar; exact UTF-8 canonical bytes contain exactly one terminal LF, and that LF participates in digest and length. | §1; §2.1; RB01; FB01–FB02 | `EV106-SO001` |
| SO002 / REF | Every nested reference resolves exact kind, version, digest, and bytes through the closed source resolver; missing, aliased, cyclic, conflicting, or wrong-typed closure is rejected before mutation. | §1; every §3 reference; FB02 | `EV106-SO002` |
| SO003 / BODY,R2 | `BS080` is private, `BS110` plus ciphertext octets is protected material, `BS095` is transaction-local, and the other 123 successor plus 21 compatibility kinds use ordinary storage. No private or ciphertext bytes enter RB01. | §2.1; RB01–RB12 | `EV106-SO003` |
| SO004 / R2,CLAIM | An ordinary canonical body is nonauthorizing; authority requires the corresponding protected typed fact and current-state predicates. | RB01; §§3.2–3.8; FB01 | `EV106-SO004` |
| SO005 / PRIVATE | Private identity is `(package_id, deciding_run_result_ref)` with one deciding result, random public ID, explicit package-to-public mapping, independent authorization, current receipt CAS, and no content-derived public identifier. | RB06–RB12; FPV01–FPV04 | `EV106-SO005` |
| SO006 / BODY,CLAIM | Ciphertext identity is exact digest plus byte length; the candidate binds the distinct `BS121` identity and all typed payload/conversion references; J adopts both candidate and ciphertext in its own transaction. | RB02–RB05; RB03; FB03; FJ01 | `EV106-SO006` |
| SO007 / SLOT,R1 | Each current selector is a persistent typed anchor with exact canonical key bytes and key digest, explicit `ABSENT` or typed `PRESENT`, serialized concurrent first use, stale-CAS refusal, and no deletion. | `Anchor`; RS01–RS21; §4 | `EV106-SO007` |
| SO008 / SLOT | The exact selector roster contains the 21 classes listed in §3.8, each with its accepted key grammar and value variant; no generic slot or extra key component is admitted. | RS01–RS21 | `EV106-SO008` |
| SO009 / SLOT | `OPERATION_GRANT` is keyed only by `grant_id`; `EVIDENCE_TIER_RESULT` is keyed only by `(claim_id,tier)`; target, publication-epoch, plan, and work classes retain their distinct key domains. | RS01–RS21; RA08; RE15–RE16 | `EV106-SO009` |
| SO010 / SLOT,WORK | Each production operation-work selector key is exactly `(plan,work_identity_digest)`. Its present referenced body retains the complete typed `work_identity`, whose exact canonical bytes must recompute that digest. Only the AuthorityGate conformance preimage embeds both identity and digest in its key object. | `WorkSlotKey`; `WorkIdentityBinding`; RS13–RS15; RW03–RW06 | `EV106-SO010` |
| SO011 / REF,HREAD,R1 | Codec, resolver, current verifier, and authenticated revision-pinned historical dispatch remain separate source-owned interfaces; historical readers are nonauthorizing and PostgreSQL receives only their exact typed outcome facts. | §1; RC01–RC04; source/non-PG classification | `EV106-SO011` |
| SO012 / AUTH,EVAL | Grant, plan, approval, authorization, and revocation use distinct authenticated principals, stable keys, exact expected-current operands, one shared nonextendable deadline, and closed lifecycle states. | RA01–RA08; RS11–RS12; FA01–FA07 | `EV106-SO012` |
| SO013 / AUTH | Plan issuance resolves the complete typed action binding, candidate, ciphertext, source/plaintext and restore/conversion references, target, cohort, epoch, and finite retry/reconciliation/budget limits. | RA03; RB02–RB03; FA03 | `EV106-SO013` |
| SO014 / AUTH,EVAL | Lifecycle mutation exact-replays identical bytes and rejects skipped state, stale expectation, changed binding, mixed principal, reinstatement, or second revocation without partial current-state change. | RA01–RA08; FA01–FA06; §4 | `EV106-SO014` |
| SO015 / AUTH,PUB | J, P, R, and M lock and revalidate both current unrevoked authority selectors through commit; durable timely R fixes the shared-deadline decision and M does not renew it. | FA07; FJ01–FJ03; FM01; RS11–RS12 | `EV106-SO015` |
| SO016 / EVAL | Campaign-plan, historical-corpus-plan, and qualification-plan acceptance are three distinct two-reference authenticated interfaces, each keyed by exact plan and exact-replaying only identical bytes. | RE01–RE03; FE01–FE03 | `EV106-SO016` |
| SO017 / EVAL,VERDICT | Campaign and deployment-acquisition registration derive accepted plans, complete ordered runs, procedure, projection, acquisition identity, protected time, clock, and boot values. Campaign registration locks the protected current subject for the campaign tier and every affected result tier; RE04 and each new RE16 project the corresponding protected subject. Callers cannot supply verdicts, result subjects, projection, acquisition time, role-grant set, or writer inventory. | RE04–RE06,RE15–RE16; FE04–FE06; VR001 | `EV106-SO017`; `EV106-VR001` |
| SO018 / EVAL,VERDICT,BODY-ATOMIC | Run registration accepts only the closed registration input, derives completion observations, records, optional failure, run result, affected tier results, and every affected current pointer. The evaluator locks the current RE15 subject for each affected result key and copies it into that RE16’s `subject_revision_ref`. Every newly constructed ordinary body and all corresponding facts, subject projections, and pointers commit in one transaction or none do. | RB01; RE05,RE07–RE09,RE15–RE16; RS07; FB01; FE07; VR002 | `EV106-SO018`; `EV106-VR002` |
| SO019 / EVAL,VERDICT | Subject replacement preserves immutable history, atomically installs the replacement as the protected current subject, and recomputes every affected result and `(claim_id,tier)` pointer. Each result projects the protected post-transition subject for its own tier; callers never choose a verdict, result subject, or pointer. | RE14–RE16; RS07; FE09; VR003 | `EV106-SO019`; `EV106-VR003` |
| SO020 / QUAL | Class finalization caller operands are exact plan, acceptance, one class tag, and the complete ordered run-result references; plan cells, records, oracles, profile, release, campaign, interval, and result are database-derived. | RE17; FQ01 | `EV106-SO020` |
| SO021 / QUAL | Receipt finalization caller operands are exact plan, acceptance, support profile, three precisely typed class-result refs, and complete design, implementation, and release tier sequences; current/prerequisite pointers, PASS, first protected issue observation, validity, and body are database-derived and nonrenewing. The observation and receipt commit together. | RE05,RE18; FQ02; IA02 | `EV106-SO021` |
| SO022 / PRIVATE,ACL | Registrar, reviewer, and exporter have the exact one-way FPV surfaces and reciprocal denials; review atomically installs projection, receipt, and current pointer, while export returns the current public pair or neither. | RB06–RB12; FPV01–FPV04; §6 | `EV106-SO022` |
| SO023 / ADMIT,ACL | Deployment policy mutation is target-surface-keyed expected-current replacement or clear; it preserves bodies, retires displaced slot/reference pairs, forbids reinstatement, and affects no other slot. | RE19–RE20; RS06; FDP01 | `EV106-SO023` |
| SO024 / ADMIT | `FailedDeploymentResult` has stable key `(campaign,deployment_attempt_id)`, complete candidate projection, nonempty ordered failures, optional exact qualification refs, literal FAIL, and `authority=NONE`; exact bytes replay, changed bytes conflict, and success emits none. | RE25; FAD02 failure result | `EV106-SO024` |
| SO025 / ADMIT,SUPPORT | Attestation finalization consumes campaign/attempt/candidate identity, target, exact receipt and CAS operands; derives policy, profile, three class refs, complete qualification/deployment partitions, acquisitions, settings/schema identity, live topology, role grants, writer inventory, clock/time, and next epoch. | RE18–RE24; RS04–RS21; FAD02; IA03,IA05,IA10 | `EV106-SO025` |
| SO026 / ADMIT | With an absent fence slot, finalization records FRESH and atomically writes issue observation, attestation, checked-next reserved epoch, high-water mark, qualification receipt, role-grant set, writer inventory, and attestation/reservation selectors. It does not change target generation or create RC29. | RE05,RE22–RE24; RX01–RX02; RS05,RS16–RS19,RS21; FAD02 FRESH; IA03,IA05 | `EV106-SO026` |
| SO027 / ADMIT,FENCE | With an occupied fence, the same finalizer transaction holds the adoption CAS and atomically adds RC29 and advances RS08 together with the SO026 effects; no separately committing adoption call or intermediate old handoff is admitted. | FAD02 COMPATIBILITY; FF05 LATER_EPOCH; RC27–RC29; RX01–RX02; RS05,RS08,RS16–RS19,RS21 | `EV106-SO027` |
| SO028 / ADMIT | An attestation is immutable and nonauthorizing by itself; policy, receipt, result partitions, clock, topology, grant/writer inventories, current attestation, and active epoch remain exact through every consuming commit. | RE24; RS05; FAD02–FAD04; FX01; stage functions | `EV106-SO028` |
| SO029 / ADMIT,FENCE | Proposal publication binds the exact reserved epoch, attestation, manifest/handoff, continuity session, and capability digest; activation or conclusive abandonment alone consumes the reservation. | FAD04; RX01–RX05; RS02–RS03,RS18 | `EV106-SO029` |
| SO030 / WORK | `OperationWorkIdentity` is the exact five-member union with every member’s complete fields and closed invocation-mode/recovery-mode combinations. | `WorkSlotKey`; RW01–RW06; FW01–FW08 | `EV106-SO030` |
| SO031 / WORK | `RequestKey=(plan,request_key_digest)`; reservation/result uniqueness and each production work-current selector use `(plan,work_identity_digest)`. The complete `work_identity` remains a protected value equality and appears in the distinct AuthorityGate conformance preimage; each digest is recomputed over its own complete LF-terminated source object. | `RequestKey`; `ReservationKey`; `WorkSlotKey`; `WorkIdentityBinding`; RW01–RW06; RS13–RS15 | `EV106-SO031` |
| SO032 / WORK | Preflight reads only the reservation/result key; byte-identical request plus complete chain returns COMMITTED_REPLAY, absence returns UNRESOLVED, a valid result under another request is also UNRESOLVED, and a digest/body or result-chain conflict fails without a preflight body. | FW01; RW01,RW03–RW06; RS15 | `EV106-SO032` |
| SO033 / WORK | Only the same unresolved request enters reservation. It either exact-replays its prior reservation/refusal, commits one reservation plus full accounting, or commits one request-keyed refusal and no accounting. Same request ID with changed bytes is REQUEST_CONFLICT; another request for an existing reservation is WORK_ALREADY_RESERVED. | RW01–RW03; RS10,RS13; FW02 | `EV106-SO033` |
| SO034 / WORK | Accounting derives checked-next contiguous ordinal, full finite class charge, counters, protected reservation observation, live clock/envelope/boot, and deadline; successful reservation commits RE05, RW03, the complete RS10 advance, and RS13 together. Overflow, exhaustion, stale ordinal, clock failure, or zero remaining interval uses the request-keyed refusal path with no observation or accounting change. | RA03; RE05; RW02–RW03; RS10,RS13; FW02; IA04 | `EV106-SO034` |
| SO035 / WORK | Start locks and rederives complete request, reservation, full identity, digest, incarnation, and server transaction identity, then atomically writes one transaction identity, start, and STARTED selector. | RW03–RW05; RS14; FW03 | `EV106-SO035` |
| SO036 / WORK | Ordinary completion locks the exact STARTED chain and inserts exactly one permitted typed result, committed-result body repeating the complete identity chain, and COMMITTED selector in one transaction. | RW04–RW13; RP/RX typed results; RW06; RS15; FW04 | `EV106-SO036` |
| SO037 / WORK | Committed transaction resolution and ambiguity query close only their own resolver reservations with their exact typed outcomes referencing the original committed result; they never duplicate or rebind the original stage. | RW08,RW12–RW13; FW05–FW06 | `EV106-SO037` |
| SO038 / WORK | Conclusive noncommit is the exceptional six-effect atomic bundle: terminal result, original committed mapping and slot close, resolver recovery observation, resolver committed mapping and slot close, with both complete started chains revalidated. | RW10,RW14–RW15; RW06; RS15; FW07 | `EV106-SO038` |
| SO039 / WORK,RESTART | Recovery observations are reservation-keyed, immutable, typed, nonauthorizing sole results; publication-qualification UNPROVEN requires the preexisting original-R conclusive-noncommit chain and closes only its own separately charged reconciliation. | RW07–RW15; FW04–FW08 | `EV106-SO039` |
| SO040 / WORK,CLAIM | A protected function result is provisional; only adapter acknowledgement of COMMIT is durable success, and lost acknowledgement resolves through exact protected read/replay without repeating an effect. | §7; FW08; stage/status reads | `EV106-SO040` |
| SO041 / PUB | J binds the exact authority, admission, candidate, ciphertext, target, lineage predecessor, and work identity, and atomically creates J with both preimage and ciphertext adoptions. | RB04–RB05; RP01–RP03; FJ01 | `EV106-SO041` |
| SO042 / PUB | P and R preserve the J chain and shared deadline; their protected current/late observations and equality-late predicates are exact, and only a valid R can enter M. | RP03–RP05; FJ02–FJ03 | `EV106-SO042` |
| SO043 / PUB,CLAIM | M derives target selection and postimage only from the action’s typed payload, mutates target, increments generation, appends RP06/RP07/RP09, and advances RS09/RS20 atomically; it never creates or rewrites the activation-time RP08 genesis. Apply and rollback authorities remain distinct. | RP06,RP07,RP09; RS09,RS20; FM01 | `EV106-SO043` |
| SO044 / PUB,RESTART | V records each stable attempt. UNABLE writes RP10/RP11 and leaves RP14 absent; MATCH writes RP10/RP15 and fills RP14; MISMATCH writes RP10/RP12 and fills RP14; TERMINAL_FAILURE writes RP10/RP13 and fills RP14. Mutually exclusive terminal states cannot reopen. | RP10–RP15; FV01 | `EV106-SO044` |
| SO045 / HREAD | Historical dispatch is authenticated, closed, revision-pinned, exact-byte, and nonauthorizing; each reader outcome repeats selector, member, contract, tool binding, and source revision. | RC01–RC04; source historical dispatcher | `EV106-SO045` |
| SO046 / CLOSE,CLOSE-OPS,CLAIM | Case creation initializes its terminal anchor as explicit ABSENT. Reservation atomically creates RC07, a generation-zero UNCLAIMED RC08 anchor, and its RESERVATION sample; claim/takeover advances only that lease generation. Finalization and expiry resolution append their exact sample/evidence/observation, close RC08, and conditionally fill RC06. Expiry resolution never observes the target. | RC05–RC13; FC01–FC06 | `EV106-SO046` |
| SO047 / FENCE,FENCE-TIME,POLICY-TIME,CLOCK-MATH | Initial fencing creates RC21 as an immutable pending envelope with adoption generation zero and literal `current_manifest_binding_digest=NONE`, then progresses monotonically through access revocation, writer drain, and service disablement. FF01 takes a fresh protected sample only after locking and revalidating the consumed invocation and every current authority/bound operand, including the current BS048 policy and its `valid_until_unix_ns`; its `U_fence_start` includes the separately rounded margin for the attested maximum transaction duration. Immediately before commit it revalidates the same operands and takes a second fresh sample for `U_fence_commit`. Both must be strictly below every FD001–FD007 member, and failure of either changes no database or external fence state, although the adapter-local invocation remains spent. Every later external observation, cancellation, termination, drain wait, or service-disable step has its own fresh protected sample and finite-timeout margin below those same seven deadline classes under the locked authority through exact outcome recording. Only FF04 atomically creates BC019/RC27 and the completed BC020/RC28 origin binding, installs RS08, and reaches FENCE_ACTIVE; partial progress creates no completed manifest binding or successor authority. | `FenceTemporalAuthorization`; FD001–FD007; RC21–RC28; RS08; FF01–FF04 | `EV106-SO047`; `EV106-FENCE-TIME-START`; `EV106-FENCE-TIME-COMMIT`; `EV106-FENCE-TIME-STEP` |
| SO048 / FENCE,FENCE-TIME,POLICY-TIME,CLOCK-MATH | Adoption uses the exact five-part CAS, contiguous immutable handoff chain, current-pointer advancement, exact replay only while current, SUPERSEDED_BINDING after replacement, and no external fence effect. A new adoption samples only after every locked fence, handoff, epoch, attestation, policy, result, clock, manifest, approval, exclusion, writer, and service predicate is revalidated; exact CLOCK-MATH produces `U_adopt`, which must be strictly below every FD001–FD007 member, including the locked current BS048 policy expiry. Equality, overflow, an unrepresentable operand, clock uncertainty, or any concurrent drift aborts with no adoption or outer success effect. Exact-current replay reads the existing immutable adoption and pointer without resampling or creating fresh authority. | `FenceTemporalAuthorization`; FD001–FD007; RC27–RC29; RS08; FF05 | `EV106-SO048`; `EV106-FENCE-TIME-ADOPT` |
| SO049 / FENCE,ADMIT | Same-epoch adoption leaves the existing attestation and RESERVED_FENCED row unchanged; later-epoch adoption is available only within FAD02’s atomic admission transaction. | FF05 mode boundary; FAD02; RX01–RX02 | `EV106-SO049` |
| SO050 / FENCE,ACTIVATE,PUB | Combined activation derives the manifest graph only from the locked current handoff and its protected graph binding, then revalidates proposal, capability witness, attestation, complete handoff and fence evidence, manifest, target, and selectors. After all preceding revalidation it samples the protected monotonic clock and validates strict cutover bounds. It atomically stores RC01–RC04 and RC14–RC20, creates RP08, initializes RS20 while leaving RS09 absent, writes RX03 including the cutover observation and RX05, changes RX02 to ACTIVE, advances RS01, and clears RS02/RS03/RS18. Conclusive noncommit instead writes RX04, changes RX02 to ABANDONED_FENCED, clears RS02/RS03/RS18 and RS05, and creates no manifest, cutover observation, genesis, or target-generation state. | RC01–RC04,RC14–RC20; RP08; RX01–RX05; RS01–RS03,RS05,RS18,RS20; FX01–FX02; IA07–IA09 | `EV106-SO050` |
| SO051 / ACL | Every distinct accepted operation has one exact positive caller/owner cell; caller-supplied and database-derived operands, rejection owner, touched facts, atomic result, and mode-specific exception are fixed by §§5–6. The 61 protected callable records include the two issue106-derived shared-body routines; their internal-only call sites are closed separately. | All 61 §5 callable IDs; all 44 principals | `EV106-SO051` |
| SO052 / ACL,PRIVATE,FENCE | The complement of every positive cell is denied; callers have no relation bypass, role membership, SET ROLE, PUBLIC/default-function execution, or cross-owner authority. FF05 specifically admits C15 only for SAME_EPOCH and O09 only for nested LATER_EPOCH. | §5.1; §6; FF05 | `EV106-SO052` |
| SO053 / ACL,FENCE | Status and audit surfaces return only safe typed identities, digests, dispositions, and categories and mutate no relation, clock, target, observation, authority, or private content. | FS01–FS03; O17 | `EV106-SO053` |
| SO054 / SUPPORT | Support profile, qualification receipt, deployment evidence, attestation, and admission bind one reproducible exact current `protected_schema_digest`; behavior-changing route-active schema or ACL drift invalidates that equality. | RM01–RM06; §8; FAD02; startup check | `EV106-SO054` |
| SO055 / R1 | Schema changes occur only through explicit deployment migrations; runtime never auto-migrates, migration metadata is not authority, and immutable authority/history bytes are never rewritten or backfilled. | RM01–RM06; MT01–MT11; §8 | `EV106-SO055` |
| SO056 / CLAIM,SUPPORT | Logical SQL, concurrency, ACL, and manifest checks are distinct from admitted-profile restart and power-loss durability qualification; neither evidence tier substitutes for the other. | §9 evidence tiers | `EV106-SO056` |
| SO057 / WORK-INIT,AUTH | New plan issuance atomically creates RS10 keyed only by the exact plan. Its value is the plan plus zero for every attempt counter, row charge, elapsed charge, and reconciliation charge, with `next_reservation_ordinal=1`. Exact plan replay never resets an advanced accounting row; a committed plan without RS10 is an invariant conflict, not repair authority. | RA03,RA07,RS10; FA03; IA01 | `EV106-SO057` |
| SO058 / EVAL,DISPOSITION,ACL | Finding registration is nonauthorizing. C08 supplies only an exact registered `BS038`; O05 independently recomputes the named defect and inserts RE10. It creates no RE11 or RE13, takes no disposition authority, and changes no RE16 or RS07 result or pointer. C09 cannot register, alter, or select the finding. | RB01; RE10; FE08; §§5–6 | `EV106-SO058` |
| SO059 / DISPOSITION,VERDICT,ACL | C09’s disposition call supplies exactly one closed authority subject and its exact `BS045` authorization receipt. O05 verifies the receipt/subject digest and matching fields, locks all affected evidence, current results, and the protected current subject for each affected result tier, and requires the authority subject’s revision to equal the corresponding protected subject. It derives a fresh qualified `RE05` with phase `EVIDENCE_DISPOSITION_APPLY`, requires its upper bound strictly before validity, constructs exactly `BS046` or `BS047`, and atomically writes RE12, RE05, the matching RE11-or-RE13 branch, every affected RE16 with its protected subject projection, and every affected RS07 pointer. Any mismatch, stale branch, invalid `FAIL` invalidation, clock failure, overflow, equality, or late bound writes none. | RB01; RE05,RE10–RE16; RS07; FE10; VR004 | `EV106-SO059`; `EV106-VR004` |
| SO060 / REF,BODY-ATOMIC,R2 | Ordinary-body insertion and resolution are owner-internal shared operations. BM001–BM062 identify atomic sites; the closed MRR direct-root, MRE transitive-member, DLT/DLI digest-link, and BREQ requiring-edge records in §5.2.1 independently identify every supplied, derived, and database root, source field path, branch, cardinality, body edge, requiring relation/fact/effect, and atomic group. Private packages, ciphertext, transient preflight bytes, missing members, and extras are rejected. No login or adapter can call FB01 or FB02. | `OrdinaryBodySiteUse`; `BodyRequirementEdge`; `ResolvedOrdinaryBodyClosure`; RB01; FB01–FB02; BM/MRR/MRE/DLT/DLI/BREQ registries; §7 | `EV106-SO060` |
| SO061 / SUPPORT,ADMIT,ACL | Attestation finalization must bind the exact currently admitted protected-schema digest and PostgreSQL build while that identity remains locked through its transaction. O09 obtains only the exact non-byte projection through an O01-owned accessor; it receives no RM relation privilege, and every other runtime principal is denied the accessor. | RM01,RM02,RM05; FAD02; IA10; §§5.2.2,6 | `EV106-SO061` |
| SO062 / ACTIVATE,POLICY-TIME,CLOCK-MATH | Combined activation samples the protected monotonic clock only after every preceding manifest, fence, attestation, policy, result, target, and selector revalidation. It locks RS06, resolves the exact current RE19/BS048, and derives FD001 from that body rather than relying on attestation-deadline domination. It derives `elapsed_upper_ns`, `forward_rate_error_upper_ns`, and `U_cutover` by the exact unbounded-integer formula, requires the sample inside the envelope and strict `U_cutover` inequality against every FD001–FD007 member, and stores the envelope digest, sample, error terms, all seven deadline classes, and `U_cutover` in RX03. Equality with policy expiry or any other deadline, overflow, clock loss/rollback, reboot/suspend uncertainty, policy drift, envelope drift, or excessive error writes nothing. | `CutoverClockObservation`; FD001–FD007; RX03; FX01 | `EV106-SO062`; `EV106-FENCE-TIME-CUTOVER` |
| SO063 / FENCE,ACTIVATE,REF | The authenticated pre-fence graph is preserved in RC21 only as a pending envelope whose current-binding digest is `NONE`. FF04 combines that envelope with the newly constructed BC019 fence evidence to create the completed BC020/RC28 origin binding; later adoption input creates a completed BC021/RC29 binding. RS08 selects exactly one RC28-or-RC29 handoff, and only that completed binding reaches FX01 and IA07 as a database-derived projection. FX01 has no caller-supplied graph operand; IA07 receives the same transaction-local projection, and all ordinary bodies are resolved through FB02 with DATABASE origin. | `PendingFenceEnvelope`; `ProtectedManifestGraphBinding`; `ActivationManifestGraphProjection`; RC21,RC27–RC29,RS08; BM055,BM058–BM060,BM062; FX01; IA07 | `EV106-SO063` |
| SO064 / ROOT-PROJECTION,PROJECTION,VERDICT,REF,BODY-ATOMIC | Every accepted reference copied into a distinct durable relation field has one independently source-derived SRP member under a pinned SPA source span, an exact MRR/MRE/DLI, protected-field, or decoded-bridge carrier, and a destination BREQ/DRP edge. A database or protected-field carrier retains its `READ/NO_WRITE` edge; supplied or derived nested members retain FB01/FB02 and BODY_PARENT provenance. One-to-many members expand by accepted ordinal or stable key. VR001–VR004 independently require the protected current-subject projection for every result-producing operation. Reference-valued RC21/BC020/BC021 copies use typed DRPs; PFC is restricted to exact non-reference scalar copies and equality checks. An enclosing body, BODY_PARENT edge, family label, or validation-only classification cannot replace a copied destination. | §3 projected fields; §5.2.1 SPA/SRP/VR/DRP/PFC/MRR/MRE/DLI/BREQ records | `EV106-SO064`; every `EV106-SRP-*`, `EV106-VR*`, `EV106-DRP-*`, and `EV106-PFC-*` check |

The accepted source independently fixes this complete result-subject projection roster. These source obligations are not inferred from BM or DRP membership:

```text
VR001 / VERDICT,EVAL / REGISTER_EVIDENCE_CAMPAIGN
  source = protected RE15 subject selected by each affected result key's tier
  additional campaign source = protected RE15 subject selected by campaign tier
  destination = every affected BS039.subject_revision and
                RE16[result_key].subject_revision_ref;
                RE04.subject_revision_ref for the campaign source
  proposal = BM012.R03, BM012.R06 and their DRP/BREQ edges in AG-BM012
  outcomes = applied write or byte-identical replay verification;
             absence, changed current subject, or mismatch writes none
  check = EV106-VR001
VR002 / VERDICT,EVAL / REGISTER_EVIDENCE_RUN_RESULT
  source = protected RE15 subject selected by each affected result key's tier
  destination = every affected BS039.subject_revision and
                RE16[result_key].subject_revision_ref
  proposal = BM015.R03 and its DRP/BREQ edges in AG-BM015
  outcomes = applied write or byte-identical replay verification;
             validation, clock, subject, or pointer refusal writes none
  check = EV106-VR002
VR003 / VERDICT,EVAL / SET_CURRENT_EVIDENCE_SUBJECT
  source = protected post-transition RE15 subject selected by each affected
           result key's tier; for the replaced tier it is the exact caller
           replacement only after that replacement is staged in RE15
  destination = every affected BS039.subject_revision and
                RE16[result_key].subject_revision_ref
  proposal = BM017.S01, BM017.R05 and their BREQ/DRP edges in AG-BM017
  outcomes = RE14 history, RE15 replacement, result writes, and RS07 changes
             all apply or exact-replay together; refusal writes none
  check = EV106-VR003
VR004 / VERDICT,DISPOSITION / APPLY_EVIDENCE_DISPOSITION
  source = protected RE15 subject selected by each affected result key's tier
  predicate = the branch authority subject revision equals the protected
              current subject for its claim and tier
  destination = every affected BS039.subject_revision and
                RE16[result_key].subject_revision_ref
  proposal = BM018.R05 and its DRP/BREQ edges in AG-BM018 plus SC020
  outcomes = the complete invalidation or supersession atomic branch applies,
             exact-replays, or writes none
  check = EV106-VR004
```

The accepted source-reference roster is closed by the source-span and member registries below. A source-span row is only an immutable anchor and operation membership list; it cannot stand in for a reference member.

```text
SPA001 / CANDIDATE,AUTH
  source = journal-acceptance-evidence.md lines 2679–2764
  sites = SC003/BM001, SC004/BM002, SC005/BM003, SC006/BM004,
          SC007/BM005, SC008/BM006, SC009/BM007
SPA002 / EVAL-PLAN
  source = journal-acceptance-evidence.md lines 3715–3724,
           4400–4614, 5147–5176, and 5563–5578
  sites = SC011/BM009, SC012/BM010, SC013/BM011
SPA003 / EVAL,VERDICT,DISPOSITION
  source = journal-acceptance-evidence.md lines 3726–3737,
           4932–5225, and 7039–7202
  sites = SC014/BM012, SC015/BM013, SC016/BM014, SC017/BM015,
          SC018/BM016, SC019/BM017, SC020/BM018
SPA004 / QUAL
  source = journal-acceptance-evidence.md lines 2878–2948 and 4400–4614
  sites = SC023/BM020, SC022/BM023, SC023/BM024
SPA005 / PRIVATE
  source = journal-acceptance-evidence.md lines 5054–5098 and 7358–7493
  sites = SC026/BM027
SPA006 / POLICY,ADMIT
  source = journal-acceptance-evidence.md lines 4623–4930 and 5100–5116
  sites = SC030/BM021, SC028/BM029, SC029/BM030, SC030/BM031
SPA007 / RESERVED-ACTIVATION
  source = journal-acceptance-evidence.md lines 4860–4930 and
           journal-compatibility-design.md lines 3062–3133 and 3278–3317
  sites = SC032/BM033, SC058/BM034
SPA008 / WORK
  source = journal-acceptance-evidence.md lines 1578–1626 and 2390–2660
  sites = SC034/BM022, SC034/BM036, SC035/BM037, SC036/BM038,
          SC037/BM039, SC038/BM040, SC039/BM041
SPA009 / PUB,RESTART
  source = journal-publication-design.md lines 330–378 and 620–944
  sites = SC041/BM043, SC042/BM044, SC043/BM045, SC044/BM046,
          SC045/BM048
SPA010 / CLOSE
  source = journal-compatibility-design.md lines 654–936
  sites = SC046/BM049, SC047/BM050, SC048/BM051, SC049/BM052,
          SC050/BM053, SC051/BM054
SPA011 / FENCE
  source = journal-compatibility-design.md lines 2658–2710 and 2947–3133
  sites = SC052/BM055, SC053/BM056, SC054/BM057, SC055/BM058,
          SC056/BM059
SPA012 / ACTIVATE
  source = journal-compatibility-design.md lines 3134–3269 and 3302–3317,
           plus journal-publication-design.md lines 250–275
  sites = SC057/BM047, SC057/BM060, SC058/BM061, SC057/BM062
```

The immutable operation anchors are:

- SPA001: [authority](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2679-L2764).
- SPA002: [campaign plan body](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L3715-L3724), [qualification](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4400-L4614), [campaign acceptance](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5147-L5176), and [historical acceptance](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5563-L5578).
- SPA003: [campaign shape](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L3726-L3737), [evidence operations](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4932-L5225), and [dispositions](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7039-L7202).
- SPA004: [qualification plans](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2878-L2948) and [qualification evidence](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4400-L4614).
- SPA005: [private access](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5054-L5098) and [private evidence](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7358-L7493).
- SPA006: [policy and admission](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4623-L4930) and [admission access](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5100-L5116).
- SPA007: [reserved activation selectors](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4860-L4930), [fence adoption](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L3062-L3133), and [reserved-state transitions](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L3278-L3317).
- SPA008: [work bodies](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L1578-L1626) and [work operations](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2390-L2660).
- SPA009: [publication identities](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md#L330-L378) and [publication protocol](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md#L620-L944).
- SPA010: [closure](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L654-L936).
- SPA011: [manifest receipts](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L2658-L2710) and [fencing](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L2947-L3133).
- SPA012: [activation](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L3134-L3269), [abandonment](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L3302-L3317), and [publication genesis](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md#L250-L275).

The source-reference roster is exactly the `REF` expansion in the
[source-member inventory](./journal-postgresql-source-member-inventory.md),
not an emission of the proposal crosswalk. Each inventory `REF` emits one
`SourceReferenceProjectionMember` with identity:

```text
(accepted revision,
 source file,
 accepted contract or operation,
 exact member path,
 closed branch or variant)
```

The SRP retains the inventory's reference domain, cardinality, stable key or
source ordinal, owning operation, and declared branch. Proposal-side relation,
BM, MRR/MRE/DLI, DRP, PFC, and check identities remain comparison values only.
A direct durable destination is mapped by the printed DRP or relation field.
A nested reference without a separate column is still a
`CALLABLE_PREDICATE` carried by its exact MRR/MRE/DLI and BODY_PARENT chain.
Sequences expand by source ordinal, sets by the inventory's stable key,
keyed results by complete canonical key, and first-use sequences by their
source-defined first-use ordinal. Repeated equal references under distinct
keys remain distinct members.

Every protected SRP has three branch outcomes unless its inventory operation
defines a stricter readback rule: `APPLIED` requires the exact protected
write or predicate equality, `EXACT_REPLAY` requires byte-identical protected
presence or equality without DML, and refusal requires no destination or
current-state write. FF01's lost-acknowledgement path uses exact protected
readback without reusing its spent invocation. Multiple source members naming
one destination are independent equality operands. A body-parent edge,
digest-only relationship, family label, or enclosing aggregate cannot replace
an SRP.

The SPA rows below are immutable navigation anchors for the owning operations;
they neither define nor narrow inventory membership. VR001–VR004 remain the
independent protected result-subject roster.

The accepted fence-time obligations are independently enumerable rather than inferred from FF rows:

| ID / source | Accepted temporal proposition | Proposal mapping | Prospective check |
|---|---|---|---|
| FT001 / FENCE-TIME,POLICY-TIME 2896–2945 | The read-only adapter gate derives `U_prefence`; exclusive local invocation consumption revalidates the same current graph, including the current policy body and expiry, and derives `U_consume`. Both use exact CLOCK-MATH and strict comparison with FD001–FD007. Issuance and consumption are adapter-local, restart-invalidated, single-use, and create no PostgreSQL authority or durable journal fact. | source/non-PG classification; FD001–FD007; §7 | `EV106-FENCE-TIME-PREFENCE` |
| FT002 / FENCE-TIME,POLICY-TIME 2947–2961 | After locking the fixed fence slot and exact current prerequisites, including RS06 and the selected RE19/BS048, and before any admission, ACL, or external fence change, FF01 takes a fresh protected sample. `U_fence_start` includes the exact separately rounded wall-time margin for the authenticated finite transaction-duration bound, remains inside the monotonic envelope, and is strictly below every FD001–FD007 member. | `FenceTemporalAuthorization(FENCE_START)`; FD001–FD007; SC052; FF01 | `EV106-FENCE-TIME-START` |
| FT003 / FENCE-TIME,POLICY-TIME 2974–2985 | Immediately before FF01 commit, while all database and local effect-attempt locks remain held, it revalidates the invocation, envelope, attestation, current BS048 policy and its expiry, results, proposal, and bounds and takes a distinct fresh sample for `U_fence_commit`. `U_fence_commit` is strictly below every FD001–FD007 member. Failure rolls back the complete database and external fence effect; the local invocation remains spent, and lost acknowledgement resolves only by exact protected read. | `FenceTemporalAuthorization(FENCE_COMMIT)`; FD001–FD007; SC052; FF01 | `EV106-FENCE-TIME-COMMIT` |
| FT004 / FENCE-TIME,POLICY-TIME 2990–3005 | Every later external observation, cancellation, termination, drain wait, or service-disable effect obtains a fresh sample after locking its exact current authority, RS06/RE19 policy, and pending step. Its conservative upper bound includes that step’s finite-timeout margin, remains strictly inside the clock horizon and below every FD001–FD007 member, and is held through exact outcome recording. No prior invocation, sample, pending row, or progress row is continuation authority. | `FenceTemporalAuthorization(FENCE_STEP)`; FD001–FD007; SC053–SC054; FF02–FF03 | `EV106-FENCE-TIME-STEP` |
| FT005 / FENCE-TIME,POLICY-TIME 3078–3110 | Both adoption modes lock and revalidate the complete active-fence, epoch, proposal, attestation, current BS048 policy, result, manifest, approval, exclusion, writer, service, and clock state before a fresh sample produces `U_adopt`. Strict inequality against every FD001–FD007 member is required; equality, overflow, drift, or concurrent change aborts the complete transaction. | `FenceTemporalAuthorization(ADOPTION)`; FD001–FD007; SC056; FF05 | `EV106-FENCE-TIME-ADOPT` |
| FT006 / ACTIVATE,POLICY-TIME 3134–3269; publication 1166–1171 | Combined activation locks the exact current policy and attestation, revalidates the completed graph and all selectors, then samples once. `U_cutover` is strictly below the policy and attestation expiries and every manifest, approval, exclusion, freshness, and clock bound. Equality, missing policy, changed policy, overflow, or uncertainty leaves the reserved epoch and every current selector unchanged. | `CutoverClockObservation`; FD001–FD007; SC057; FX01 | `EV106-FENCE-TIME-CUTOVER` |

The deadline operand inventory is source-derived and closed. Every member is `(deadline_class, source_path, protected_path, subject_digest, deadline_unix_ns, cardinality, applicable_gate_set)`. Every value is parsed as a checked `UInt128String`; every comparison is strict `U < deadline`, so equality is late. The source path identifies the accepted field independently of its proposal mapping:

| ID | Exact source and protected operand | Cardinality and applicable gates |
|---|---|---|
| FD001 `POLICY_EXPIRY` | `DeploymentAdmissionPolicy.valid_until_unix_ns`; locked `RS06.value → RE19.body_ref/BS048`, subject digest = BS048 body digest | exactly one; `U_prefence`,`U_consume`,`U_fence_start`,`U_fence_commit`, every per-effect upper, `U_adopt`,`U_cutover` |
| FD002 `ATTESTATION_EXPIRY` | `DeploymentAttestation.valid_until_unix_ns`; locked `RS05.value → RE24.body_ref/BS021`, subject digest = BS021 body digest | exactly one; every gate named for FD001 |
| FD003 `MANIFEST_FRESHNESS` | `ManifestBasis.freshness_deadline_unix_ns`; selected completed RC28-or-RC29 graph → BC001 | exactly one; every gate named for FD001 |
| FD004 `MANIFEST_EXPIRY` | `ManifestBasis.expires_at_unix_ns`; selected completed RC28-or-RC29 graph → BC001 | exactly one; every gate named for FD001 |
| FD005 `MANIFEST_APPROVAL_EXPIRY` | exact `CUTOVER_MANIFEST` BC008 `expires_at_unix_ns` linked to the selected BC006 | exactly one; every gate named for FD001 |
| FD006 `EXCLUSION_BODY_EXPIRY` | each selected BC007 `expires_at_unix_ns` named by BC006 | one per exclusion identity; every gate named for FD001 |
| FD007 `EXCLUSION_APPROVAL_EXPIRY` | each corresponding `ARTIFACT_EXCLUSION` BC008 `expires_at_unix_ns` | one per FD006 member with the same exclusion identity; every gate named for FD001 |

The cardinalities, subject links, and gate sets above are normative. A missing, extra, duplicate, cross-linked, expired, unrepresentable, or differently current member rejects before an effect. Attestation validity being capped by policy validity does not remove FD001 or its comparison. The adapter-local FT001 gates resolve the same seven source fields without creating PostgreSQL state; FF01–FF05 and FX01 derive them from locked database rows. No caller supplies a deadline scalar.

#### 2.3.1 Paragraph-level callable comparison

`SC003–SC061` are the accepted operation fingerprints mapped to §5. `SC001–SC002` are the shared-body operations fixed by R2, CAN, REF, and BODY-ATOMIC. Each fingerprint includes the complete signature, caller operand, database-derived operand, result-subject source, branch predicate, refusal, durable effect, current-pointer effect, atomic group, and positive/default-deny ACL edge. Every supplied, derived, and database-resident ordinary-body root and requiring relation/fact/effect has an MRR/BREQ record; every typed recursive occurrence and inherited root requirement has an MRE/BODY_PARENT record; every database-root output copy has a DRP; and every compatibility digest-only occurrence is an instance of DLT/DLI.

`IN` is caller-supplied, `DB` is derived or locked by the database, `EFFECT` is the complete durable non-RB01 relation, current-state, or external delta, and `ALLOW` is the sole positive caller-to-owner cell. The matching BM row supplies the exact RB01 body delta and dependency set; their union is the complete durable delta. Every row also inherits source decoding rejection, database relational rejection, exact replay where applicable, provisional return, and the complete default-deny complement.

| ID / source operation | Source-derived semantic fingerprint | Proposal/check |
|---|---|---|
| SC001 / R2,CAN,BODY-ATOMIC ordinary-body insertion | `IN=one exact OrdinaryBodySiteUse and ResolvedOrdinaryBodyItem named by an MRR or MRE insertion record`; `DB=BM site,SC anchor,owner,root path,branch,ordinal,member edge,complete nonempty BREQ set,digest,length,existing row and atomic-group validity`; `EFFECT=RB01 insert or exact replay in the outer transaction`; `ALLOW=O03–O15,O18→O02`; no login, adapter, O16, or O17 grant. | FB01 / `EV106-SC001` |
| SC002 / R2,REF ordinary-body resolution | `IN=one exact OrdinaryBodySiteUse and typed BodyRef named by an MRR, MRE, or DLI resolution record`; `DB=BM site,SC anchor,owner,root path,branch,ordinal,member edge,complete nonempty BREQ set,stored bytes,digest,length and atomic-group validity`; `EFFECT=NONE`; no listing; `ALLOW=O03–O16,O18→O02`; no login, adapter, or O17 grant. | FB02 / `EV106-SC002` |
| SC003 / BODY,AUTH rollback-candidate registration | `IN=BS121,BS110 descriptor,ciphertext,payload/conversion refs`; `DB=hash,length,target,lineage,membership`; `EFFECT=RB02+RB03`; `ALLOW=C01→O03`. | FB03 / `EV106-SC003` |
| SC004 / AUTH ISSUE_OPERATION_GRANT | `IN=BS089`; `DB=session principal,absent grant`; `EFFECT=RA01+RA08+RS12`; `ALLOW=C02→O04`. | FA01 / `EV106-SC004` |
| SC005 / AUTH REVOKE_OPERATION_GRANT | `IN=grant_id,expected grant,BS090`; `DB=current lifecycle`; `EFFECT=RA02+RA08+RS12 clear`; `ALLOW=C06→O04`. | FA02 / `EV106-SC005` |
| SC006 / AUTH,WORK-INIT ISSUE_OPERATION_PLAN | `IN=BS088`; `DB=session principal,current grant,complete candidate/ciphertext/limits`; `EFFECT=new path RA03+RA07+zero RS10 atomically; replay path validates existing RS10 without resetting it`; `ALLOW=C03→O04`. | FA03+IA01 / `EV106-SC006` |
| SC007 / AUTH APPROVE_OPERATION_PLAN | `IN=plan,expected approval,BS105`; `DB=principal,lifecycle`; `EFFECT=RA04+RA07`; `ALLOW=C04→O04`. | FA04 / `EV106-SC007` |
| SC008 / AUTH AUTHORIZE_OPERATION | `IN=plan,approval,expected authorization,BS106`; `DB=principal,deadline,lifecycle`; `EFFECT=RA05+RA07+RS11`; `ALLOW=C05→O04`. | FA05 / `EV106-SC008` |
| SC009 / AUTH REVOKE_OPERATION_AUTHORITY | `IN=plan,both expected refs,BS107`; `DB=lifecycle`; `EFFECT=RA06+RA07+RS11 clear`; `ALLOW=C06→O04`. | FA06 / `EV106-SC009` |
| SC010 / AUTH READ_CURRENT_OPERATION_AUTHORITY | `IN=stage-bound plan`; `DB=RA07,RA08,RS11,RS12`; `EFFECT=NONE`; `ALLOW=O11,O12→O04`. | FA07 / `EV106-SC010` |
| SC011 / EVAL ACCEPT_EVIDENCE_CAMPAIGN_PLAN | `IN=BS023,BS024`; `DB=principal,complete expansion`; `EFFECT=RE01`; `ALLOW=C07→O05`. | FE01 / `EV106-SC011` |
| SC012 / EVAL ACCEPT_HISTORICAL_CORPUS_PLAN | `IN=BS040,BS041`; `DB=principal,closed reader coverage`; `EFFECT=RE02`; `ALLOW=C07→O05`. | FE02 / `EV106-SC012` |
| SC013 / EVAL ACCEPT_QUALIFICATION_PLAN | `IN=BS016,BS018`; `DB=principal,complete qualification expansion`; `EFFECT=RE03`; `ALLOW=C07→O05`. | FE03 / `EV106-SC013` |
| SC014 / EVAL,VERDICT REGISTER_EVIDENCE_CAMPAIGN | `IN=BS022`; `DB=accepted basis,complete planned runs,locked RE15 campaign-tier subject and locked RE15 subject for every affected result key`; `EFFECT=RE04 whose subject revision equals the campaign-tier protected subject plus every affected RE16 whose subject revision equals its key-tier protected subject and every RS07 pointer`; `REFUSE=missing or changed subject,subject mismatch,or any result/pointer failure writes none`; `ALLOW=C08→O05`. | FE04; VR001 / `EV106-SC014`; `EV106-VR001` |
| SC015 / EVAL ACQUIRE_DEPLOYMENT_EVIDENCE | `IN=campaign,run_id,oracle_id`; `DB=policy/profile/clock,procedure,projection,identity,time`; `BODY=BM013 exactly one BS034 observed projection, one BS015 acquisition observation, and one BS064 acquisition`; `EFFECT=those three bodies+RE05+RE06 atomically`; `ALLOW=C08→O05`. | FE05 / `EV106-SC015` |
| SC016 / EVAL OBSERVE_EVIDENCE_TIME | `IN=CAMPAIGN_START\|EVIDENCE_START,subject digest,envelope-or-NONE`; `DB=protected time`; `EFFECT=RE05`; `ALLOW=C08→O05`. | FE06 / `EV106-SC016` |
| SC017 / EVAL,VERDICT,BODY-ATOMIC REGISTER_EVIDENCE_RUN_RESULT | `IN=closed registration input and its exact ResolvedOrdinaryBodyClosure`; `DB=records,completion,failure,verdict,all derived ordinary bytes,and the locked RE15 subject for every affected result key`; `EFFECT=RB01+RE05+RE07+RE08-if-failing+RE09+all affected RE16 with their key-tier protected subjects+all RS07 pointers as one transaction`; `REFUSE=any body,validation,clock,subject,or pointer failure writes none`; `ALLOW=C08→O05`. | FE07+FB01; VR002 / `EV106-SC017`; `EV106-VR002` |
| SC018 / EVAL,DISPOSITION REGISTER_EVIDENCE_INVALIDITY_FINDING | `IN=exact BS038 ref and closure`; `DB=referenced evidence,oracle contract,expected/observed projections and independently recomputed named defect`; `EFFECT=RB01+RE10 only`; `NO_EFFECT=RE11,RE13,RE16,RS07`; `ALLOW=C08→O05`; C09 and every other principal denied. | FE08 / `EV106-SC018` |
| SC019 / EVAL,VERDICT SET_CURRENT_EVIDENCE_SUBJECT | `IN=tier,replacement`; `DB=prior current subject,affected claims,and the protected post-transition RE15 subject for every affected result key`; `EFFECT=RE14 history+RE15 replacement+every recomputed RE16 whose subject revision equals its key-tier post-transition protected subject+every RS07 pointer atomically`; `REFUSE=stale replacement,currentness mismatch,or any result/pointer failure writes none`; `ALLOW=C09→O05`. | FE09; VR003 / `EV106-SC019`; `EV106-VR003` |
| SC020 / DISPOSITION,VERDICT APPLY_EVIDENCE_DISPOSITION | `IN=exact EvidenceDispositionAuthoritySubject and exact BS045 authorization receipt, never a final disposition`; `DB=subject canonical bytes/digest,authenticated C09 principal,matching receipt fields,affected evidence/current results,locked RE15 subject for every affected result key,authority-subject equality to the protected claim-tier subject,fresh qualified clock,RE05 phase EVIDENCE_DISPOSITION_APPLY,strict U<valid_until,and final BS046-or-BS047`; `EFFECT=RB01+RE12+RE05+(RE11 invalidation of the complete ordered claim set or RE13 contiguous supersession)+all affected RE16 with their key-tier protected subjects+all RS07 pointers atomically`; `REFUSE=no rows or pointer changes on subject mismatch,stale branch,cycle,second edge,invalid FAIL invalidation,clock failure,overflow,equality,or late bound`; `ALLOW=C09→O05`; C08 and every other principal denied. | FE10; VR004 / `EV106-SC020`; `EV106-VR004` |
| SC021 / EVAL READ_CURRENT_EVIDENCE_TIER_RESULT | `IN=claim_id,tier`; `DB=RS07`; `EFFECT=NONE`; `ALLOW=O06,O09,O11,O12,O16→O05`. | FE11 / `EV106-SC021` |
| SC022 / QUAL class finalization | `IN=plan,acceptance,class,complete ordered run refs`; `DB=cells,records,oracles,profile,release,campaign,interval,result`; `EFFECT=RE17`; `ALLOW=C10→O06`. | FQ01 / `EV106-SC022` |
| SC023 / QUAL receipt finalization | `IN=plan,acceptance,profile,three typed class refs,three complete tier sequences`; `DB=current pointers,PASS,first issue sample,validity,receipt`; `EFFECT=RE05+RE18`; `ALLOW=C10→O06`. | FQ02+IA02 / `EV106-SC023` |
| SC024 / PRIVATE package registration | `IN=complete private package`; `DB=bounds,deciding evidence,commitment,random public ID`; `EFFECT=RB06+RB07`; `ALLOW=C11→O07`. | FPV01 / `EV106-SC024` |
| SC025 / PRIVATE package review read | `IN=public ID`; `DB=mapping,current reviewer authorization`; `EFFECT=NONE`; `ALLOW=C12→O07`. | FPV02 / `EV106-SC025` |
| SC026 / PRIVATE review registration | `IN=public ID,expected receipt,projection,receipt`; `DB=package,evidence,subject,policies,authorization,commitment`; `EFFECT=RB10+RB11+RB12`; `ALLOW=C12→O07`. | FPV03 / `EV106-SC026` |
| SC027 / PRIVATE public export | `IN=public ID,expected receipt`; `DB=all currentness/disclosure predicates`; `EFFECT=NONE`; `ALLOW=C13→O07`. | FPV04 / `EV106-SC027` |
| SC028 / ADMIT policy CAS | `IN=TargetSurfaceKey,expected,replacement-or-NONE`; `DB=slot,target sets,retirement`; `EFFECT=RE19+optional RE20+RS06 only`; `ALLOW=C14→O08`. | FDP01 / `EV106-SC028` |
| SC029 / ADMIT clock registration | `IN=TargetSurfaceKey,expected,BS014`; `DB=live host/boot,clock predicates`; `EFFECT=RE21+RS04`; `ALLOW=C15→O09`. | FAD01 / `EV106-SC029` |
| SC030 / ADMIT attestation finalization | `IN=campaign,attempt,candidate,TargetSurfaceKey,receipt/expected,request,attestation/reservation expectations,fence input`; `DB=all SO025 operands plus IA10's exact locked RM01/RM02/RM05 SchemaAdmissionProjection`; `EFFECT=RE05+RE22+RE23+RE24+RX01+RX02+RS05+RS16+RS17+RS18+RS19+RS21, plus RC29+RS08 only for COMPATIBILITY; failure=RE25 alone`; `ALLOW=C15→O09`. | FAD02+IA03+IA05+IA10+FF05 / `EV106-SC030` |
| SC031 / ADMIT attestation currentness revocation | `IN=epoch,expected current,reason`; `DB=policy,evidence,incarnation`; `EFFECT=RS05 clear only`; `ALLOW=C15→O09`. | FAD03 / `EV106-SC031` |
| SC032 / ADMIT activation proposal | `IN=epoch,expected reserved row,proposal`; `DB=RX01,attestation,fence`; `EFFECT=RS02+RS03; RX01 remains immutable`; `ALLOW=C15→O09`. | FAD04+IA06 / `EV106-SC032` |
| SC033 / WORK preflight | `IN=BS094`; `DB=RequestKey,ReservationKey,production WorkSlotKey,complete WorkIdentityBinding and committed chain`; `EFFECT=NONE`; `COMMITTED_REPLAY` only for exact same request, otherwise accepted UNRESOLVED/conflict branches; `ALLOW=C16,C17,C18,C19→O10`. | FW01 / `EV106-SC033` |
| SC034 / WORK reservation | `IN=the same unresolved BS094`; `DB=keys,RS10,ordinal,limits,clock,deadline`; `EFFECT=RW01+(RW02 only, no accounting) or RW01+RE05+RW03+advanced RS10+RS13`; `ALLOW=C16,C17,C18,C19→O10`. | FW02+IA04 / `EV106-SC034` |
| SC035 / WORK start | `IN=reservation,incarnation proof`; `DB=keys,RS10,server transaction identity`; `EFFECT=RW04+RW05+RS14`; `ALLOW=C16,C17,C18,C19→O10`. | FW03 / `EV106-SC035` |
| SC036 / WORK ordinary result | `IN=reservation,permitted result kind/result`; `DB=complete STARTED chain`; `EFFECT=one typed result+RW06+RS15`; `ALLOW=O10,O11,O12,O13→O10`. | FW04 / `EV106-SC036` |
| SC037 / WORK committed-transaction resolution | `IN=resolver and original chains`; `DB=original committed result`; `EFFECT=RW12+resolver RW06+resolver RS15`; `ALLOW=C19→O10`. | FW05 / `EV106-SC037` |
| SC038 / WORK ambiguity query | `IN=query and original chains`; `DB=protected transaction state`; `EFFECT=(RW13 or RW08)+query RW06+query RS15`; `ALLOW=C19→O10`. | FW06 / `EV106-SC038` |
| SC039 / WORK conclusive noncommit | `IN=both started chains,subject,BS087`; `DB=no committed original`; `EFFECT=RW14+original RW06+original RS15+RW10+resolver RW06+resolver RS15`; `ALLOW=C19→O10`. | FW07 / `EV106-SC039` |
| SC040 / WORK exact outcome read | `IN=RequestKey or production WorkSlotKey`; `DB=RW01–RW15,RS13–RS15`; `EFFECT=NONE`; `ALLOW=C16,C17,C18,C19→O10`. | FW08 / `EV106-SC040` |
| SC041 / PUB J | `IN=plan/action/target/preimage request,observation request ID`; `DB=authority,admission,lineage,clock,candidate,ciphertext,J`; `EFFECT=CURRENT: RP01+RP02+RP03+RB04+RB05+FW04 result; LATE: RP03+FW04 result only`; `ALLOW=C16→O11`. | FJ01 / `EV106-SC041` |
| SC042 / PUB P | `IN=aggregate,J,request ID`; `DB=authority,admission,lineage,time`; `EFFECT=CURRENT: RP03+RP04+FW04 result; LATE: RP03+FW04 result only`; `ALLOW=C16→O11`. | FJ02 / `EV106-SC042` |
| SC043 / PUB R | `IN=aggregate,P,qualified sample`; `DB=envelope,U,authority,admission,lineage`; `EFFECT=RP05 VALID or LATE plus FW04 result; unproven creates no RP05`; `ALLOW=C16→O11`. | FJ03 / `EV106-SC043` |
| SC044 / PUB M | `IN=aggregate,valid R`; `DB=witness,target images,ciphertext,conversion,payload,generation,lineage`; `EFFECT=target mutation+RP06+RP07+RP09+RS09+RS20+FW04 result`; `ALLOW=C17→O12`. | FM01 / `EV106-SC044` |
| SC045 / PUB V | `IN=M,attempt identity`; `DB=target image,generation,lineage,terminal state`; `EFFECT=SO044 branch plus FW04 result`; `ALLOW=C18→O13`. | FV01 / `EV106-SC045` |
| SC046 / CLOSE case creation | `IN=source descriptors,reader binding,target`; `DB=closure,attestation,policy,clock,expiry`; `EFFECT=RC05+explicit-ABSENT RC06`; `ALLOW=C20→O14`. | FC01 / `EV106-SC046` |
| SC047 / CLOSE attempt reservation | `IN=case,request ID`; `DB=next ordinal,limits,qualified reservation sample,deadline`; `EFFECT=RC07+RC08 UNCLAIMED(0)+RC09 RESERVATION`; `ALLOW=C20→O14`. | FC02 / `EV106-SC047` |
| SC048 / CLOSE attempt claim | `IN=case,ordinal,incarnation`; `DB=claim sample,next lease generation/token/deadline`; `EFFECT=RC08 CLAIMED+RC09 CLAIM`; `ALLOW=C20→O14`. | FC03 / `EV106-SC048` |
| SC049 / CLOSE same-ordinal takeover | `IN=case,ordinal,invalidation`; `DB=fresh TAKEOVER sample`; `EFFECT=RC10+RC09 TAKEOVER+advanced RC08 CLAIMED`; `ALLOW=C20→O14`. | FC04 / `EV106-SC049` |
| SC050 / CLOSE finalization | `IN=case,ordinal,lease,typed observation input`; `DB=target image,comparison,finalization sample,deadlines`; `EFFECT=RC09 FINALIZATION+(RC11 or RC12)+RC13+RC08 CLOSED+optional terminal RC06`; `ALLOW=C20→O14`. | FC05 / `EV106-SC050` |
| SC051 / CLOSE expiry resolution | `IN=case,ordinal`; `DB=session closure,expiry sample`; `EFFECT=RC09+RC12+RC13+RC08 CLOSED+optional exhausted RC06`; no target read; `ALLOW=C20→O14`. | FC06 / `EV106-SC051` |
| SC052 / FENCE,FENCE-TIME,CLOCK-MATH begin | `IN=exact consumed adapter-local invocation and authenticated pre-fence graph, including the canonical target bridge and approval-digest-keyed exclusion receipt entries, never a clock sample, U value, duration, or deadline scalar`; `DB=fixed fence slot,proposal,current policy and attestation,current qualification/deployment partitions,role-grant set,writer inventory,clock envelope,complete FenceAuthorityDeadlineSet,and authenticated finite transaction-duration bound`; `PRE_EFFECT=after all locks and revalidation,one fresh FenceTemporalAuthorization(FENCE_START) with strict U_fence_start bounds`; `PRECOMMIT=under the same locks,complete revalidation and a distinct fresh FenceTemporalAuthorization(FENCE_COMMIT) with strict U_fence_commit bounds`; `EFFECT=access revocation+RC21 PendingFenceEnvelope with exact typed target/approval/exclusion references, complete receipt scalars, generation zero, and binding NONE+RC22 ACCESS_REVOKED+RC23+RC24`; `REFUSE=either gate failure,expiry,equality,overflow,unrepresentable input,revocation,replacement,scope/result/clock drift,or graph/receipt/predicate mismatch rolls back every database and external effect;the local invocation remains spent`; `REPLAY=lost acknowledgement exact-reads the consumed-invocation digest and every typed pending binding without reusing the invocation or resampling`; `ALLOW=C21→O15`. | FF01 / `EV106-SC052`; `EV106-FENCE-TIME-START`; `EV106-FENCE-TIME-COMMIT` |
| SC053 / FENCE,FENCE-TIME writer drain | `IN=generation and exact requested drain observation step,never a sample,U value,timeout,deadline,or authoritative zero-writer result`; `DB=RC21/RC22,current handoff or pending manifest binding,current policy/attestation,qualification/deployment partitions,clock envelope,admission/ACL state,writer inventory,specific pending step,complete deadlines,and step timeout`; `PRE_EFFECT=one fresh FenceTemporalAuthorization(FENCE_STEP) before each external observation,cancellation,termination,or drain wait,with locks retained through exact outcome recording`; `EFFECT=only an exact zero-live-writer outcome writes RC25+RC22 SESSIONS_DRAINED`; `REFUSE=gate failure,nonzero/unattributed writer,drift,equality,overflow,or timeout writes no RC25/RC22 change and performs no newly authorized step`; `ALLOW=C21→O15`. | FF02 / `EV106-SC053`; `EV106-FENCE-TIME-STEP` |
| SC054 / FENCE,FENCE-TIME service disable | `IN=generation,exact selected service step,and exact external disable-attestation body/closure,never a sample,U value,timeout,or deadline`; `DB=RC21/RC22,current policy/attestation,qualification/deployment partitions,clock envelope,admission/ACL and continuously closed drain state,writer inventory,specific pending service,complete deadlines,and step timeout`; `PRE_EFFECT=one fresh FenceTemporalAuthorization(FENCE_STEP) before the external disable,with locks retained through exact attestation validation and RC26 outcome recording`; `EFFECT=external disable+RC26`; `REFUSE=gate failure,wrong service/outcome,drift,equality,overflow,or timeout writes no RC26 and authorizes no new external disable`; `ALLOW=C21→O15`. | FF03 / `EV106-SC054`; `EV106-FENCE-TIME-STEP` |
| SC055 / FENCE finalization | `IN=generation`; `DB=RC21 PendingFenceEnvelope plus complete admission/ACL/drain/service evidence`; `EFFECT=RC27 BC019+field-complete RC28 typed-reference and scalar binding+BC020+RS08+RC22 FENCE_ACTIVE atomically`; `REFUSE=any missing,extra,wrong-domain,wrong-key,or unequal target,attestation,approval,exclusion,receipt,handoff,or scalar member writes none`; `ALLOW=C21→O15`. | FF04 / `EV106-SC055` |
| SC056 / FENCE,FENCE-TIME,CLOCK-MATH adoption | `IN=mode,CAS,complete manifest graph with exact target bridge and approval-digest-keyed receipt bindings,approval,and epoch binding,never a clock sample,U value,or deadline scalar`; `DB=fixed fence slot,FENCE_ACTIVE,current handoff and persistent fence,admission/ACL/drain/service evidence,epoch selectors,proposal,attestation,current policy,current qualification/deployment PASS partitions,clock envelope,complete FenceAuthorityDeadlineSet,and continuously closed barrier`; `PRE_EFFECT=after complete lock/currentness/digest/reference/receipt revalidation,one fresh FenceTemporalAuthorization(ADOPTION) deriving U_adopt with strict bounds`; `EFFECT=field-complete RC29 typed-reference and scalar binding+BC021+RS08,plus only FAD02 outer success effects for LATER_EPOCH`; `REPLAY=an identical adoption exact-verifies every immutable RC29 field only while RS08 still selects it,without resampling;an older exact adoption is SUPERSEDED_BINDING`; `REFUSE=equality,overflow,unrepresentable input,clock uncertainty,missing or mismatched typed member,revocation,replacement,regrant,writer/service/fence drift,or proposed-epoch authority changes no RC/RS or outer success member;FAD02 may then take only its separately specified RE25 failure branch`; `ALLOW=C15 SAME_EPOCH,O09 nested LATER_EPOCH→O15`. | FF05 / `EV106-SC056`; `EV106-FENCE-TIME-ADOPT` |
| SC057 / ACTIVATE,CLOCK-MATH combined activation | `IN=proposal,clear capability only`; `DB=locked RS08, its tag-selected RC28-or-RC29 completed binding, the contiguous RC28/RC29 handoff chain, and supporting RC21/RC23–RC27 fence rows; database-derived ActivationManifestGraphProjection; attestation/policy/result partitions, live target and bindings, epoch selectors, BS014 envelope and every applicable deadline`; `POST_REVALIDATION=protected monotonic sample and exact CutoverClockObservation`; `EFFECT=RC01–RC04+RC14–RC20+RP08+RX03 including the observation+RX05+RS20+RX02 ACTIVE+RS01 advance+RS02/RS03/RS18 clear; RS09 remains ABSENT`; `REPLAY=returns the original RX03 observation without resampling only for the identical already-current activation`; `REFUSE=no row or selector change on missing completed binding, pending-only fence, stale graph/binding, failed strict deadline, equality, overflow, clock uncertainty, or any revalidation mismatch`; `ALLOW=C17→O16`. | FX01+IA07+IA08 / `EV106-SC057` |
| SC058 / ACTIVATE abandonment | `IN=epoch,transaction identity`; `DB=reserved row,conclusive noncommit/inadmissibility,selectors`; `EFFECT=RX04+RX02 ABANDONED_FENCED+RS02/RS03/RS18/RS05 clear`; `ALLOW=C19→O16`. | FX02+IA09 / `EV106-SC058` |
| SC059 / R2 status publication | `IN=aggregate/work/epoch key`; `DB=safe RA/RW/RP/RX/RS projections`; `EFFECT=NONE`; `ALLOW=C22→O17`. | FS01 / `EV106-SC059` |
| SC060 / FENCE status compatibility | `IN=cutover/case/fence key`; `DB=safe RC projections`; `EFFECT=NONE`; `ALLOW=C22→O17`. | FS02 / `EV106-SC060` |
| SC061 / R1 schema status | `IN=NONE`; `DB=safe RM01–RM06 projections`; `EFFECT=NONE`; `ALLOW=C22→O17`. | FS03 / `EV106-SC061` |

The source leaves the concrete schema-manifest and upgrade metadata representation to issue106. These derived obligations satisfy the accepted propositions without becoming journal authority:

| ID | Issue106-derived obligation | Proposal mapping | Prospective check |
|---|---|---|---|
| D106-01 | `ActiveSurfaceManifest/v1` has the exact roots, closed object classes and tagged-union wrappers, transitive dependency closure, canonical fields, total collection ordering, duplicate rules, serialization, exclusions, and invalidation rules in §8. Its exact bytes live only in RM05. | RM01,RM05; `ActiveSurfaceDigest`; §8 | `EV106-D106-01` |
| D106-02 | `CatalogManifest/v1` records every protected-schema object and associated ownership/ACL dependency for each exact physical phase, including dormant compatibility objects, without relation data. Its exact bytes live only in RM05. Unclassifiable preparation states use the explicit nonmanifest RM04/RM06 branch until exact classification succeeds. | RM02–RM06; `CatalogDigest`; §8 | `EV106-D106-02` |
| D106-03 | Deployment state admits exactly MT01–MT11, including append-only classification remediation, failed preparation, retry/abandonment, reversible cutover/rollback, irreversible finalization, and next-generation preparation. | RM01–RM06; MT01–MT11 | `EV106-D106-03` |
| D106-04 | Every cross-owner ordinary-body insert/read uses exactly FB01/FB02 and an explicit MRR, MRE, or DLI member record with its complete BREQ set under one BM001–BM062 atomic site. Every other cross-owner durable effect uses exactly IA01–IA09, FW04, or FF05. Non-body cross-owner reads are limited to IA10’s O01-owned schema projection, FX01’s enumerated O15-owned completed-fence projection, and O15’s exact FF01–FF05 read/lock projections over the current attestation, policy, qualification/deployment result, clock, role/writer, epoch/proposal, and admission state required by `FenceTemporalAuthorization`. The callee owner retains relation access; the outer owner receives only the listed EXECUTE or column-limited read/lock cell, and every effect or currentness lock remains in the outer transaction through commit acknowledgement. | FB01–FB02; BM/MRR/MRE/DLT/DLI/BREQ registries; IA01–IA10; FF01–FF05; FX01; §§5–7 | `EV106-D106-04` |

Every source fact, callable, role/ACL cell, selector, branch outcome, and atomic bundle in §2.2 must have at least one edge to this registry. Every proposal relation, callable, principal, slot, and transition must have at least one reverse edge to an `SO` or `D106` obligation. An explicit `NOT_POSTGRESQL` edge must name the source-owned boundary and source anchor; omission is not a classification.

### 2.4 Shared logical types

- `Digest`: exactly 32 bytes internally and 64 lowercase hexadecimal characters at the source boundary.
- `BodyRef<K>`: `(kind=K, version=1, digest)`.
- `TargetSurfaceKey`: `(target_database_identity: BodyRef<BS050 TARGET_DATABASE>, target_surface_digest: Digest)`.
- `ApprovalChannelReceiptProjection`: `(boundary_id: Token, receipt_format: Token, receipt_bytes_base64url: Text, receipt_bytes: bytea, receipt_sha256: Digest)`. The text is the exact canonical unpadded base64url value, `receipt_bytes` is its unique decode, and `receipt_sha256=sha256(receipt_bytes)`.
- `ExclusionApprovalReceiptBinding`: `(approval_digest: Digest, approval_ref: BodyRef<BC008>, exclusion_ref: BodyRef<BC007>, receipt: ApprovalChannelReceiptProjection)`. Its stable key is exactly `approval_digest`; `approval_ref.digest=approval_digest`; decoded BC008 has domain `ARTIFACT_EXCLUSION`, `subject_kind=hindsight-compatibility-artifact-exclusion`, and `subject_digest=exclusion_ref.digest`; and that exact BC007 is a member of the selected final manifest. Missing, extra, duplicate-keyed, differently keyed, or mismatched bindings reject.
- `PublicationEpochKey`: `TargetSurfaceKey + publication_epoch`.
- `RequestKey`: `(plan: BodyRef<BS088>, request_key_digest: Digest)`, where the digest is recomputed over the complete exact `BS094` bytes including LF. `request_id` is a distinct field inside those bytes.
- `ReservationKey`: `(plan: BodyRef<BS088>, work_identity_digest: Digest)`. It is the immutable reservation/result uniqueness key and is recomputed from the complete standalone canonical `OperationWorkIdentity` bytes including LF.
- `WorkSlotKey`: `(plan: BodyRef<BS088>, work_identity_digest: Digest)`. It is the exact production key for each operation-work current selector. It has the same tuple shape as `ReservationKey` but a distinct selector role.
- `WorkIdentityBinding`: `(work_identity: OperationWorkIdentity, work_identity_digest: Digest)`. The complete typed identity's canonical LF-terminated bytes must hash to the digest, and every RW03–RW06/RS13–RS15 value must agree. It is protected value equality, not a production key field.
- `AuthorityGateOperationWorkSlotPreimage`: `{key_kind="OPERATION_WORK",plan,work_identity,work_identity_digest,slot_class}`. This source-canonical conformance object alone embeds the complete identity in its key preimage; it is `SOURCE_INTERFACE` and never a production relation key.
- `WorkOutcomeLookupKey`: the closed union `REQUEST(RequestKey) | WORK(WorkSlotKey)`. A `WORK` lookup finds the production key, resolves the stored referenced bodies, recomputes their complete `WorkIdentityBinding`, and refuses any body/digest inconsistency before returning the chain.
- `FenceAdoptionCAS`: `(target_database_identity, target_surface_digest, fence_generation, expected_adoption_generation, expected_current_manifest_binding_digest)`.
- `AdmissionFenceInput`: exactly `FRESH(expected_current_fence=ABSENT)` or `COMPATIBILITY(expected_current_fence=PRESENT(BodyRef<BC020>|BodyRef<BC021>), FenceAdoptionCAS, final_manifest: BodyRef<BC006>, approval: BodyRef<BC008>)`. Its target and surface equal the outer admission key. The checked-next epoch, admission generation, adoption generation, attestation, and reserved row remain database-derived.
- `ResolvedOrdinaryBodyItem`: `(ref: BodyRef<K>, exact_bytes: bytea, projection: ClosedOrdinaryBodyProjection<K>)`, where `K` is exactly one of the 123 successor or 21 compatibility kinds assigned to RB01 in §2.1. The bytes, projection, kind, version, digest, and recursive references must agree.
- `OrdinaryBodyKind`: exactly `BS001–BS079`, `BS081–BS094`, `BS096–BS109`, `BS111–BS126`, or `BC001–BC021`. `BS080`, `BS095`, and `BS110` are respectively private, transient, and ciphertext classes and are not members.
- `BodyRequirementEdge`: exactly `RELATION(relation_id,field_or_effect_path,mode: READ|WRITE|VERIFY_PRESENT,effect_outcome: NO_WRITE|APPLIED|EXACT_REPLAY) | PROTECTED_EFFECT(source_semantic_id,effect_path) | BODY_PARENT(parent_member_record_id,reference_path)`. `READ` pairs only with `NO_WRITE`; `WRITE` pairs only with `APPLIED`; and `VERIFY_PRESENT` pairs only with `EXACT_REPLAY`. `WRITE` is the one semantic durable-relation effect mode: the owning relation and SC branch, not a BREQ encoder, determine whether PostgreSQL physically inserts an immutable fact, initializes or replaces an anchor, or clears an anchor. `VERIFY_PRESENT` requires the exact already-stored relation field and performs no DML. A `PROTECTED_EFFECT` is a named source operation predicate or nonrelation effect, never independent authority. Its exact identifiers and expansion rules are fixed in §5.2.1.
- `OrdinaryBodySiteUse`: `(member_record_id: MRR|MRE|DLI identity from §5.2.1, site_id: BM001–BM062, source_semantic_id: SC001–SC061, owner, origin: SUPPLIED|DERIVED|DATABASE, branch, root_path, root_ordinal, occurrence_path, root_ref, member_ref, edge: FB01|FB02, requiring_edges: nonempty set<BodyRequirementEdge>, atomic_group_id)`. Singleton roots use ordinal zero; sequence members use their source ordinal; set members use their digest order; keyed members use their zero-based canonical-key order and include the complete key in `member_record_id`. O02 verifies every field and every requiring edge against the closed member record rather than accepting a site-level claim.
- `SourceMemberInventoryIdentity`: exactly `(accepted_revision,source_file,source_contract_or_operation,exact_member_path,branch_or_variant)` from the sibling inventory. Proposal identifiers are forbidden.
- `SourceMemberDisposition`: exactly `RELATION(relation_or_anchor_id) | CURRENT(slot_class,anchor_id) | CALLABLE_PREDICATE(predicate_id,owner,callable_id) | ORDINARY_BODY(kind,version,path) | PRIVATE(boundary_id) | CIPHERTEXT(boundary_id) | SOURCE_INTERFACE(interface_id) | SOURCE_GAP(gap_id)`. Its total source-derived construction is §2.2.1.
- `ProtectedSourcePredicate`: `(predicate_id,source_member_identity,accepted_operation,exact_source_path,branch_or_variant,cardinality,stable_member_key_or_ordinal,input_origin,protected_owner,protected_callable,comparison_or_effect,applied_outcome,exact_replay_outcome,refusal_or_no_effect_outcome,positive_access_cell,reciprocal_denial_set)`. PRD/QEQ/DEQ/PLV/PLR/DFR/NMR and the remaining inventory predicate/copy families use this current finite registry.

- `HistoricalReaderSourceInterfaceMember`: one exact inventory-generated
  `R(kind,role,artifact_schema,reference_schema,variant,wire)` member. It
  contains exactly
  `artifact_kind,authenticated_dependency_role,artifact_schema_version,reference_plan_schema_version,artifact_or_reference_plan_variant,protocol_family,protocol_version,wire_canonicalization_contract,reader_contract_id,source_revision`;
  exactly one of kind and role is non-`NONE`. `reader_contract_id` hashes the
  complete LF-terminated selector of the preceding eight fields under the
  inventory's CID contract, and `source_revision` is exactly
  `7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab`. The inventory's literal D/P/N
  wires, V/STOP/RELEASE/shared-lifecycle finite expansions, exact-drain matrix,
  kindless requeue member, stopped-run and claim-release members, five
  grant-plan plus nine immediate grant-lineage members, and every enumerated
  parent-role edge are all separate `SOURCE_INTERFACE` identities. No range,
  deeper reference schema, descriptive lineage label, or opaque ciphertext
  creates another selector.
- `HistoricalReaderExecutionProjection`: the exact inventory-listed registry
  binding, execution binding, selected member, member digest, contract ID,
  wire, source revision, raw identity, and complete recursive authenticated
  dependency closure. Registry members, runs, outputs, and corpus coverage
  preserve the same unsigned-byte canonical order. PostgreSQL stores exact
  bodies and RC04 outcomes but never reconstructs a selector, substitutes a
  tool, flattens an immediate parent, or dispatches ciphertext.
- `SourceReferenceProjectionMember`: `(source_member_id: SRP identity, source_span_id: SPA001–SPA012, accepted_revision, accepted_operation, accepted_source_field_path, branch, cardinality, stable_member_key_or_ordinal, reference_domain, accepted_destination_semantic, outcome: APPLIED|EXACT_REPLAY|REFUSAL_NO_WRITE)`. It is source-owned evidence membership, not a proposal relation or test-derived set. Its complete expansion is exactly the sibling inventory's `REF` identity set; §5.2.1 maps but never defines that set.
- `DurableReferenceProjection`: `(projection_id: DRP identity, source_projection_member_id: SRP identity, source_carrier: BODY_MEMBER(MRR|MRE|DLI identity)|PROTECTED_FIELD(relation_id,field_path)|DECODED_BRIDGE(member_record_id,accepted_field_path), branch, cardinality, reference_domain, destination_relation_id, destination_field_path, disposition: MATERIALIZE|EQUALITY_ONLY, atomic_group_id)`. A direct database or protected-field source retains an exact `READ/NO_WRITE` edge. A nested source uses its exact MRE or DLI identity and parent chain. A decoded bridge names the exact source field and closed compatibility-to-successor projection. `MATERIALIZE` adds the destination’s `WRITE/APPLIED` or `VERIFY_PRESENT/EXACT_REPLAY` edge. `EQUALITY_ONLY` compares with a separately named materializer and adds no second write. Two source members mapped to one destination must be byte-identical; neither may be discarded as validation-only.
- `ProtectedFieldCopy`: `(copy_id: PFC identity, source_relation_or_derived_body, source_field_path, branch, cardinality, destination_relation_id, destination_field_path, atomic_group_id, source_mode: READ|DERIVED|SUPPLIED, disposition: MATERIALIZE|EQUALITY_ONLY, outcome: APPLIED|EXACT_REPLAY|REFUSAL_NO_WRITE)`. It is used only for a non-reference scalar or a sequence/set of non-reference scalar members copied into or compared with RC21, RC28, or RC29. A BodyRef, a composite containing a BodyRef, or an untyped identity is forbidden and uses DRP instead. `READ` preserves an explicit source-relation read; `DERIVED` names one exact decoded BC020/BC021 scalar; `SUPPLIED` names one authenticated-envelope scalar. `MATERIALIZE` writes or replay-verifies its exact destination. `EQUALITY_ONLY` names its exact destination comparator and performs no second write. `REFUSAL_NO_WRITE` emits no copy or destination change.
- `ResolvedOrdinaryBodyClosure`: duplicate-free sequence of `(OrdinaryBodySiteUse,ResolvedOrdinaryBodyItem)`, sorted by `(member_record_id,root_ordinal,occurrence_path,kind ASCII,version,digest)`. For every SUPPLIED root record it contains exactly that root and every MRE member reachable under the closed source grammar. It contains no DATABASE root, server-derived root, private package, ciphertext, transient preflight body, hidden member, or unrelated body. Each external signature with SUPPLIED records is normatively extended with this nonauthorizing operand.
- `PendingFenceEnvelope`: `(target_surface: TargetSurfaceKey, fence_generation, cutover_id, manifest_body_digest, manifest_approval_digest, canonical_proposal_digest, consumed_invocation_digest, continuity_role_identity, continuity_session_id, reserved_publication_epoch, incarnation_capability_digest, deployment_attestation_ref: BodyRef<BS021>, service_step_set, manifest_basis_ref: BodyRef<BC001>, frozen_reader_registry_ref: BodyRef<BC002>, reader_outcome_refs: set<BodyRef<BC003>|BodyRef<BC004>>, final_manifest_ref: BodyRef<BC006>, manifest_approval_ref: BodyRef<BC008>, artifact_exclusion_refs: set<BodyRef<BC007>>, approval_refs: set<BodyRef<BC008>>, referenced_evidence_refs: set<BodyRef<OrdinaryBodyKind>>, manifest_approval_receipt: ApprovalChannelReceiptProjection, exclusion_approval_receipts: set<ExclusionApprovalReceiptBinding>, adoption_generation: literal 0, current_manifest_binding_digest: literal NONE)`. `manifest_approval_ref` is the unique approval-set member whose digest is `manifest_approval_digest`. The exclusion receipt set is keyed only by approval digest and contains exactly one binding for each selected exclusion approval and no other member. It contains neither BC019 nor BC020/BC021 and cannot be selected by RS08.
- `ProtectedManifestGraphBinding`: `(binding_relation: RC28|RC29, target_surface: TargetSurfaceKey, fence_generation, adoption_generation, current_handoff_ref: BodyRef<BC020>|BodyRef<BC021>, prior_handoff_ref_or_NONE: BodyRef<BC020>|BodyRef<BC021>|NONE, cutover_id, manifest_body_digest, manifest_approval_digest, persistent_fence_evidence_digest, canonical_proposal_digest, consumed_invocation_digest, continuity_role_identity, continuity_session_id, reserved_publication_epoch, incarnation_capability_digest, deployment_attestation_ref: BodyRef<BS021>, service_step_set, manifest_basis_ref: BodyRef<BC001>, frozen_reader_registry_ref: BodyRef<BC002>, reader_outcome_refs: set<BodyRef<BC003>|BodyRef<BC004>>, final_manifest_ref: BodyRef<BC006>, manifest_approval_ref: BodyRef<BC008>, artifact_exclusion_refs: set<BodyRef<BC007>>, approval_refs: set<BodyRef<BC008>>, referenced_evidence_refs: set<BodyRef<OrdinaryBodyKind>>, manifest_approval_receipt: ApprovalChannelReceiptProjection, exclusion_approval_receipts: set<ExclusionApprovalReceiptBinding>, persistent_fence_ref: BodyRef<BC019>, authority: literal NONE)`. RC28 requires BC020, adoption generation zero, prior handoff `NONE`, and exact field-for-field reconstruction from RC21 plus BC019. RC29 requires BC021, a positive contiguous adoption generation, the immediately prior RC28-or-RC29 handoff, the newly authenticated graph and receipts, and unchanged pre-fence proposal/invocation/role/service members inherited from that prior binding. Every duplicated BC020/BC021 field must equal the protected relation projection. Target identity uses the exact compatibility-to-successor bridge. Exclusion receipt bindings use `approval_digest` as their sole stable key. The sets use canonical body-reference bytes and reject duplicate identities with changed bytes.
- `ActivationManifestGraphProjection`: the transaction-local, nonserializable projection consisting of locked RS08, the tag-selected RC28-or-RC29 `ProtectedManifestGraphBinding`, its contiguous BC020/BC021 handoff chain, RC21 pending envelope, BC019/RC27 persistent fence, its BC015/BC016/BC017/BC018 members, all bodies named by the completed binding, decoded final dispositions and predecessor, RC23–RC29 supporting rows, and the source-derived target/surface projection. O16 derives it by following RS08 `BC020→RC28` or `BC021→RC29`, never RC21; it separately locks RC21 and RC23–RC27 as supporting fence evidence, resolves every ordinary member through FB02, and passes the same projection to IA07. It is never an adapter operand.
- `FenceAuthorityDeadlineSet`: the duplicate-free FD001–FD007 set: exactly one `POLICY_EXPIRY` from locked RS06→RE19/BS048, exactly one `ATTESTATION_EXPIRY` from locked RS05→RE24/BS021, exactly one `MANIFEST_FRESHNESS`, `MANIFEST_EXPIRY`, and `MANIFEST_APPROVAL_EXPIRY` from the selected completed graph, and one paired `EXCLUSION_BODY_EXPIRY` and `EXCLUSION_APPROVAL_EXPIRY` for every selected exclusion. Each member is `(deadline_class,subject_digest,deadline_unix_ns)`. The function derives all members from locked current rows and the canonical graph; callers cannot supply scalar members. It enforces policy `effective_from_unix_ns < valid_until_unix_ns`, attestation `issued_at_unix_ns < valid_until_unix_ns`, manifest `created_at_unix_ns <= observed_at_unix_ns < freshness_deadline_unix_ns <= expires_at_unix_ns`, and `issued_at_unix_ns < expires_at_unix_ns` for every exclusion and approval. Missing, extra, duplicate, differently linked, noncurrent, or unrepresentable members reject.
- `FenceTemporalAuthorization`: a transaction-local, nonserializable value `(phase: FENCE_START|FENCE_COMMIT|FENCE_STEP(step_kind,step_identity)|ADOPTION, clock_envelope_ref, clock_envelope_digest, boot_identity_ref, synchronization_epoch_ref, monotonic_sample_upper_ns, monotonic_anchor_lower_ns, monotonic_validity_deadline_lower_ns, wall_upper_at_anchor_unix_ns, forward_rate_error_numerator, forward_rate_error_denominator, elapsed_upper_ns, sample_trusted_upper_unix_ns, finite_duration_ns, duration_rate_error_upper_ns, authorization_upper_unix_ns, deadlines: FenceAuthorityDeadlineSet)`. It uses exact CLOCK-MATH over unbounded nonnegative integers:

  ```text
  elapsed_upper_ns =
      monotonic_sample_upper_ns - monotonic_anchor_lower_ns
  sample_rate_error_upper_ns =
      ceil_mul_div(elapsed_upper_ns,n,d)
  sample_trusted_upper_unix_ns =
      wall_upper_at_anchor_unix_ns
      + elapsed_upper_ns
      + sample_rate_error_upper_ns
  duration_rate_error_upper_ns =
      ceil_mul_div(finite_duration_ns,n,d)
  authorization_upper_unix_ns =
      sample_trusted_upper_unix_ns
      + finite_duration_ns
      + duration_rate_error_upper_ns
  ```

  `FENCE_START` obtains `finite_duration_ns` by checked conversion of the exact maximum transaction duration from the authenticated fence realization-policy projection; `FENCE_STEP` obtains it from that exact pending step’s finite timeout. `FENCE_COMMIT` and `ADOPTION` use literal zero. The sample must satisfy `monotonic_sample_upper_ns >= monotonic_anchor_lower_ns` and `monotonic_sample_upper_ns + finite_duration_ns < monotonic_validity_deadline_lower_ns`; `authorization_upper_unix_ns` must be strictly less than every deadline member. The named authorization upper is respectively `U_fence_start`, `U_fence_commit`, the per-step upper, or `U_adopt`. Equality is late. Negative subtraction, zero denominator, checked conversion failure, overflow of any named UInt128 result, rollback/loss/reboot/suspend uncertainty, boot/synchronization/envelope drift, or excessive error rejects before the protected effect. No `FenceTemporalAuthorization` is durable evidence or reusable authority.
- `CutoverClockObservation`: `(clock_envelope_ref: BodyRef<BS014>, clock_envelope_digest: Digest, boot_identity_ref, synchronization_epoch_ref, monotonic_sample_lower_ns: UInt128String, monotonic_sample_upper_ns: UInt128String, monotonic_anchor_lower_ns: UInt128String, monotonic_validity_deadline_lower_ns: UInt128String, wall_upper_at_anchor_unix_ns: UInt128String, forward_rate_error_numerator: UInt128String, forward_rate_error_denominator: UInt128String, elapsed_upper_ns: UInt128String, forward_rate_error_upper_ns: UInt128String, trusted_upper_bound_unix_ns: UInt128String, evaluated_deadlines: set<(deadline_class,subject_digest,deadline_unix_ns)>)`. Here `trusted_upper_bound_unix_ns` is `U_cutover`; arithmetic and range checks are exactly CLOCK-MATH. `evaluated_deadlines` is exactly FD001–FD007, including one `POLICY_EXPIRY`; its policy subject digest equals the locked current BS048 body digest. Replay returns this stored set unchanged and never substitutes the then-current policy.
- `EvidenceDispositionAuthoritySubject`: exactly one of:
  - `INVALIDATION(affected_claim_ids: sequence<ClaimId>, campaign: BodyRef<BS022>, disposition_id: Id, evidence: BodyRef<BS035>, invalidity_evidence: BodyRef<BS038>, issued_at_unix_ns: UInt128String, reason: Text, subject_kind: literal hindsight-postgresql-evidence-record-invalidation, subject_revision: EvidenceRef, tier: DESIGN|IMPLEMENTATION|RELEASE|DEPLOYMENT, valid_until_unix_ns: UInt128String)`;
  - `SUPERSESSION(campaign: BodyRef<BS022>, claim_id: ClaimId, disposition_id: Id, issued_at_unix_ns: UInt128String, prior_result: BodyRef<BS039>, reason: Text, replacement_campaign: BodyRef<BS022>, subject_kind: literal hindsight-postgresql-evidence-campaign-supersession, subject_revision: EvidenceRef, tier: DESIGN|IMPLEMENTATION|RELEASE|DEPLOYMENT, valid_until_unix_ns: UInt128String)`.
- `ActiveSurfaceDigest`: SHA-256 of the exact `ActiveSurfaceManifest/v1` bytes defined in §8.
- `CatalogDigest`: SHA-256 of the exact `CatalogManifest/v1` bytes defined in §8.
- `SchemaManifestRef<K>`: `(manifest_kind=K,manifest_version=1,digest)`, resolving exactly one RM05 row whose stored bytes recompute to that digest.
- `SchemaAdmissionProjection`: `(rm02_state_digest, active_generation, route_generation, target_database_identity, accepted_contract_revision, catalog_manifest_ref, active_surface_manifest_ref, protected_schema_digest, postgresql_version_num, postgresql_build_digest, compatible_adapter_generations_and_digests)`. Every reference resolves the exact locked RM01/RM02/RM05 rows; it contains no manifest bytes.
- `PhysicalSchemaIdentity`: `(interface_generation, phase_instance_id, catalog_digest, active_surface_digest)`.
- `InstalledSchemaState`: exactly `EXACT(identity: PhysicalSchemaIdentity, catalog_manifest_ref: SchemaManifestRef<CATALOG>, active_surface_manifest_ref: SchemaManifestRef<ACTIVE_SURFACE>)` or `UNCLASSIFIED_PREPARATION(attempt_id, last_exact_identity: PhysicalSchemaIdentity, last_exact_catalog_manifest_ref: SchemaManifestRef<CATALOG>, active_surface_manifest_ref: SchemaManifestRef<ACTIVE_SURFACE>, current_fingerprint_set_digest: Digest, latest_failure_or_remediation_ref: RM04|RM06)`.
- `Expected<T>`: exactly `ABSENT` or `PRESENT(T)`.
- `Provisional<T>`: `APPLIED(T) | EXACT_REPLAY(T) | REFUSED(code) | CONFLICT`.
- `ReadResult<T>`: `ABSENT | PRESENT(T) | UNRESOLVED(code)`.
- `Immutable<K>`: `body_ref: BodyRef<K>` plus the listed key/projection columns. It is insert-only.
- `Anchor<K,V>`: exact LF-terminated key bytes, their recomputed digest, projected natural key, `presence`, and the exact `V`. Anchors are never deleted.
- An absent anchor has no hidden value. First use inserts-or-finds the one natural-key row, locks it, and evaluates the caller’s exact expectation against that row.

Constraint identities are `PK-<relation>`, `UQ-<relation>-n`, `FK-<relation>-n`, and `CK-<relation>-n`. Secondary index identities are `IX-<relation>-n`.

Evidence profiles used below are:

- `EI`: immutable identity, exact replay/conflict, references, append-only enforcement, and ACL.
- `EC`: absence/presence, exact and stale CAS, concurrent first use, reference domain, no deletion, and ACL.
- `EP`: private identity, replay/conflict, non-disclosure, references, and ACL.
- `EY`: ciphertext byte/hash/length identity, replay/conflict, adoption, retention, and ACL.
- `ES`: session isolation and restart absence; durable replay and immutability are explicitly not applicable.
- `ED`: deployment-state transition, replay/conflict, immutable provenance, and deployment ACL.

## 3. Closed relation catalog

Every `Immutable` row references `RB01` unless its record says otherwise. Every stored reference has an exact kind/version check. Union references use explicit tagged columns and checks; no generic writable reference column exists.

Every destination field in the closed DRP registry in §5.2.1 is a required typed projection column of its named §3 relation, not descriptive shorthand. Its kind/version domain, branch presence, sequence ordinal or set-member key, and source relation FK are those printed in the DRP row. A DRP field is nonnull exactly on its admitted branch; an optional or branch-absent source produces no destination row or field value. These columns may not be replaced by an enclosing-body FK.

### 3.1 Bodies and protected material

| ID / logical relation / owner | Key; fields and references; restrictions | Evidence |
|---|---|---|
| RB01 `ordinary_body` / O02 | PK `(kind,version,digest)`; canonical contract, byte length, exact bytes. Checks hash, length, admitted kind/version. Immutable. | EI |
| RB02 `protected_rollback_ciphertext` / O03 | PK `(ciphertext_digest,byte_length)`; `BS110` descriptor bytes/ref and exact ciphertext bytes. Both hashes and lengths recomputed; no FK to RB01. Immutable and retained. | EY |
| RB03 `rollback_candidate` / O03 | PK `BS121 ref`; target/surface, lineage, membership, source, conversion, apply/restore payload, and `RB02` ciphertext. `authority=NONE`; immutable. | EI |
| RB04 `journal_preimage_adoption` / O11 | PK `journal_ref`; FK `RP02`, `RB03`. Exists iff matching journal exists. Immutable. | EI |
| RB05 `journal_ciphertext_adoption` / O11 | PK `journal_ref`; FK `RP02`, `RB02`. Exists iff matching journal exists. Immutable. | EI |
| RB06 `controlled_private_package` / O07 | PK `(package_id,deciding_run_result_ref)`; unique deciding result and random public ID; private exact bytes, digest, length; FK `RE09`. No ordinary-body FK. Immutable. | EP |
| RB07 `private_public_mapping` / O07 | PK `public_record_id`; unique FK to `RB06`. No private digest or content-derived public field. Immutable. | EP |
| RB08 `private_reviewer_authorization_fact` / O07 | PK `authorization_id`; reviewer identity/principal, exact scope, policy refs, validity and predecessor. Deployment-installed, immutable. | EP |
| RB09 `private_reviewer_authorization_current` / O07 | `Anchor<(reviewer,scope),authorization_id>`; FK `RB08`. No v1 runtime administrator. | EC |
| RB10 `bounded_public_projection` / O07 | PK `public_record_id`; unique review ID; `BS081` ref; FK `RB07`. Immutable ordinary public body. | EI |
| RB11 `private_review_receipt` / O07 | PK `(review_id,public_projection_ref)`; `BS082` ref; FKs `RB10`,`RB08`. Immutable ordinary public body. | EI |
| RB12 `private_review_current` / O07 | `Anchor<public_record_id,receipt_ref>`; FK `RB11`. | EC |

### 3.2 Operation authority

| ID / relation / owner | Key; fields and references; restrictions | Evidence |
|---|---|---|
| RA01 `operation_grant` / O04 | PK `grant_id`; `BS089` ref, principal, action scope, limits, shared deadline. Immutable. | EI |
| RA02 `operation_grant_revocation` / O04 | PK `grant_id`; unique `BS090` ref; FK `RA01`, exact prior state. Immutable terminal fact. | EI |
| RA03 `operation_plan` / O04 | PK `BS088 ref`; grant, action binding, retry/reconciliation/budget limits, candidate and deadline; FKs `RA01`,`RB03`. Immutable. | EI |
| RA04 `operation_approval` / O04 | PK `plan_ref`; `BS105` ref, authenticated approver, exact shared deadline; FK `RA03`. Immutable. | EI |
| RA05 `operation_authorization_receipt` / O04 | PK `(plan_ref,approval_ref)`; `BS106` ref, authenticated authorizer, exact shared deadline; FKs `RA03`,`RA04`. Immutable. | EI |
| RA06 `operation_authority_revocation` / O04 | PK `plan_ref`; `BS107` ref; exact approval/authorization or `NONE`; FKs `RA03–RA05`. Immutable terminal fact. | EI |
| RA07 `operation_authority_lifecycle_current` / O04 | `Anchor<plan_ref, PLAN_ISSUED \| APPROVED \| AUTHORIZED \| REVOKED + exact refs>`; FKs `RA03–RA06`. | EC |
| RA08 `operation_grant_lifecycle_current` / O04 | `Anchor<grant_id, ACTIVE(grant_ref) \| REVOKED(revocation_ref)>`; FKs `RA01`,`RA02`. | EC |

### 3.3 Evidence, qualification, policy, and admission

| ID / relation / owner | Key; fields and references; restrictions | Evidence |
|---|---|---|
| RE01 `evidence_campaign_plan_acceptance` / O05 | PK `plan_ref`; `BS023`,`BS024` refs and authenticated principal. Immutable. | EI |
| RE02 `historical_corpus_plan_acceptance` / O05 | PK `plan_ref`; `BS040`,`BS041` refs and authenticated principal. Immutable. | EI |
| RE03 `qualification_plan_acceptance` / O05 | PK `plan_ref`; `BS016`,`BS018` refs and authenticated principal. Immutable. | EI |
| RE04 `evidence_campaign` / O05 | PK `campaign_id`; `BS022` ref, tier, subject, accepted basis, ordered planned runs. Immutable. | EI |
| RE05 `protected_time_observation` / O05 | PK `(phase,subject_key_digest)`; `BS015` ref; `clock_envelope_ref: BodyRef<BS014> \| NONE`, `boot_identity_ref: BodyRef<BS050> \| NONE`, and derived bound. Both projections are present exactly for `QUALIFIED_CLOCK`, equal the resolved envelope and its boot member, and are both absent when the body carries `clock_envelope=NONE`. Immutable; no caller timestamp. | EI |
| RE06 `deployment_evidence_acquisition` / O05 | PK `acquisition_identity`; unique `(campaign,run_id,oracle)`; `BS064` ref, campaign, observed projection, procedure, acquired-at RE05, `clock_envelope_ref: BodyRef<BS014>`, and `boot_identity_ref: BodyRef<BS050>`. Immutable. | EI |
| RE07 `evidence_record` / O05 | PK `evidence_ref`; unique `(campaign_ref,run_id,oracle_id)`; `BS035` ref, campaign/run, claims, observations, artifacts, oracle projections. Immutable. | EI |
| RE08 `evidence_run_failure` / O05 | PK `(campaign,run_id)`; optional `BS036` ref associated with the one run result. Immutable. | EI |
| RE09 `evidence_run_result` / O05 | PK `(campaign,run_id)`; `BS037` ref; FKs `RE04–RE08`; exact completion observations. Immutable. | EI |
| RE10 `evidence_invalidity_finding` / O05 | PK `finding_ref`; `BS038` ref, evidence ref, defect class, acquisition procedure, oracle contract, complete expected and observed projections, and independent evaluator result. Immutable and nonauthorizing; no FK or trigger creates RE11 or changes RE16/RS07. | EI |
| RE11 `evidence_record_invalidation` / O05 | PK `evidence_ref`; unique disposition ID; exact `BS046` ref, complete ordered affected claim IDs, campaign, subject revision, tier, issue/validity bounds, and FKs to RE10 invalidity evidence, RE12 authorization, and the `EVIDENCE_DISPOSITION_APPLY` RE05 observation. One record-wide invalidation; immutable. | EI |
| RE12 `evidence_disposition_authorization` / O05 | PK `authorization_ref`; unique `(subject_kind,subject_digest)`; exact `BS045` ref, authenticated authority principal, literal `AUTHORIZE`, issue/validity bounds, and exact canonical authority-subject bytes whose recomputed digest and fields equal the receipt and final disposition. Immutable. | EI |
| RE13 `evidence_campaign_supersession` / O05 | PK `(campaign_ref,claim_id,tier,prior_result_ref)`; unique disposition ID; exact `BS047` ref and FKs to RE12, RE05, prior RE16, replacement RE04, and subject revision. At most one successor for each PK; chain is contiguous and acyclic. Immutable. | EI |
| RE14 `evidence_subject_assignment` / O05 | PK `(tier,assignment_ref)`; subject ref and predecessor assignment. Immutable history. | EI |
| RE15 `evidence_subject_current` / O05 | `Anchor<tier,subject_ref>`; FK `RE14`. | EC |
| RE16 `evidence_tier_result` / O05 | PK `BS039 ref`; unique `(claim_id,tier,subject_revision_ref,evidence_state_digest)`; claim, tier, nonnull `subject_revision_ref: BodyRef<OrdinaryBodyKind>`, verdict, selected campaign and ordered prerequisites. On insertion the subject field must equal the protected post-transition RE15 subject selected by that row’s tier under VR001–VR004; it remains immutable if RE15 later changes. | EI |
| RE17 `qualification_class_result` / O06 | PK `(qualification_plan_ref,evidence_class)`; `BS019` ref, exact plan acceptance, `support_profile_ref: BodyRef<BS009>`, complete caller-named `BS037` run-result sequence, and `clock_epoch_refs: sequence<BodyRef<BS109>>` equal to the duplicate-free first-use sequence from those run results. Recomputed cells, class/profile/release/campaign equality, and run-derived interval are mandatory. Immutable. | EI |
| RE18 `qualification_receipt` / O06 | PK `qualification_plan_ref`; `BS020` ref, exact plan acceptance and support profile, three individually typed RE17 references, complete ordered design/implementation/release tier-result sequences, first issuance observation and derived validity. Immutable. | EI |
| RE19 `deployment_admission_policy` / O08 | PK `BS048 ref`; target/surface sets, profile, evidence, clock, closure and validity rules. Immutable. | EI |
| RE20 `retired_policy_slot_pair` / O08 | PK `(policy_slot_key_digest,policy_ref)`; prevents reinstatement. Immutable. | EI |
| RE21 `clock_envelope` / O09 | PK `BS014 ref`; exact target/surface context and `target_database_identity_ref: BodyRef<BS050>`, `boot_identity_ref: BodyRef<BS050>`, `clock_profile_ref: BodyRef<BS051>`, `host_identity_ref: BodyRef<BS050>`, `synchronization_epoch_ref: BodyRef<BS050>`, support profile, controller/PostgreSQL host, endpoint, topology, and live-projection refs. Every identity equals the corresponding BS014 or protected-live member. Includes the monotonic anchor/error/validity fields. Immutable. | EI |
| RE22 `role_grant_set` / O09 | PK `BS061 ref`; target/surface and exact enumerated role grants. Immutable. | EI |
| RE23 `writer_inventory` / O09 | PK `BS063 ref`; target/surface and exact classified services/writer paths. Immutable. | EI |
| RE24 `deployment_attestation` / O09 | PK `PublicationEpochKey`; `BS021` ref; nonnull typed projections `qualification_plan_ref: BodyRef<BS016>`, `qualification_plan_acceptance_ref: BodyRef<BS018>`, `qualification_receipt_ref: BodyRef<BS020>`, `closure_policy_limits_ref: BodyRef<BS069>`, `boot_identity_ref: BodyRef<BS050>`, `clock_envelope_ref: BodyRef<BS014>`, `controller_host_ref: BodyRef<BS010>`, `postgresql_host_ref: BodyRef<BS011>`, `postgresql_endpoint_ref: BodyRef<BS012>`, `deployment_topology_ref: BodyRef<BS013>`, `host_identity_ref: BodyRef<BS050>`, `endpoint_identity_ref: BodyRef<BS050>`, `storage_identity_ref: BodyRef<BS050>`, `support_profile_ref: BodyRef<BS009>`, `target_database_identity_ref: BodyRef<BS050>`, `deployment_admission_policy_ref: BodyRef<BS048>`, `deployment_campaign_ref: BodyRef<BS022>`, ordered `deployment_tier_result_refs: sequence<BodyRef<BS039>>`, `postgresql_settings_ref: BodyRef<BS083>`, `role_grant_set_ref: BodyRef<BS061>`, `writer_inventory_ref: BodyRef<BS063>`, and `issue_observation_ref: BodyRef<BS015>`. Every source-assigned SPA006/SRP duplicate carrier must agree. `postgresql_settings_ref` only mirrors the resolved exact BS021 member; this record assigns no live-binding source or current-slot equality for it, and `target_generation` remains inside exact BS021 bytes without an RE24 scalar projection. Immutable. | EI |
| RE25 `failed_deployment_result` / O09 | PK `(campaign_ref,deployment_attempt_id)`; `BS044` ref, exact `candidate_projection_ref: BodyRef<BS034>`, `campaign_ref: BodyRef<BS022>`, `subject_revision_ref: BodyRef<OrdinaryBodyKind>`, nonempty duplicate-free ordered failures, and independently optional `qualification_plan_ref: BodyRef<BS016> \| NONE`, `qualification_plan_acceptance_ref: BodyRef<BS018> \| NONE`, `qualification_receipt_ref: BodyRef<BS020> \| NONE`, and `support_profile_ref: BodyRef<BS009> \| NONE`. Every non-NONE field is the exact body observed by the attempt. Literal `FAIL`, `authority=NONE`; exact bytes replay verifies every projected field and changed bytes conflict. A successful attempt has no row. Immutable. | EI |

### 3.4 Work and transaction resolution

| ID / relation / owner | Key; fields and references; restrictions | Evidence |
|---|---|---|
| RW01 `operation_work_request` / O10 | PK `RequestKey`; `BS094` ref, distinct request ID, complete typed work identity and recomputed work-identity digest. Multiple RequestKeys may share `(plan,request_id)` only as immutable conflict/refusal evidence, and multiple request IDs may derive the same ReservationKey; the functions enforce the exact branch predicates below. Immutable. | EI |
| RW02 `work_pre_reservation_refusal` / O10 | PK and FK `RequestKey` to RW01; `BS096` ref, checked failed limit/counter/deadline and accounting-state digest. It has no ReservationKey, consumes no charge or ordinal, and cannot satisfy a reservation/result chain. Immutable. | EI |
| RW03 `operation_work_reservation` / O10 | PK `ReservationKey`; unique FK `RequestKey` to RW01; projected `(plan_ref,request_id)` is unique among reservations. `BS097` ref, complete `WorkIdentityBinding`, contiguous ordinal, full charge, `clock_envelope_ref: BodyRef<BS014>`, `boot_identity_ref: BodyRef<BS050>`, reservation observation, and deadline. Its `(plan_ref,work_identity_digest)` WorkSlotKey must equal RS13, while the complete identity remains a value equality. Immutable. | EI |
| RW04 `transaction_identity` / O10 | PK and FK `ReservationKey` to RW03; unique server transaction ID; `BS103` ref, authenticated incarnation, transaction mode, and complete work identity/digest equal to RW03. Immutable. | EI |
| RW05 `operation_work_start` / O10 | PK and FK `ReservationKey` to RW03; `BS098` ref and FK `RW04`; complete `WorkIdentityBinding` equal to RW03 and its `(plan_ref,work_identity_digest)` WorkSlotKey equal to RS14. Immutable. | EI |
| RW06 `operation_work_committed_result` / O10 | PK and FK `ReservationKey` to RW03; `BS099` ref and nonnull `plan_ref: BodyRef<BS088>`, `request_ref: BodyRef<BS094>`, `reservation_ref: BodyRef<BS097>`, `start_ref: BodyRef<BS098>`, `transaction_identity_ref: BodyRef<BS103>`, and branch-typed `typed_result_ref`. The plan is copied from the protected reservation and must equal the transaction plan. Its complete `WorkIdentityBinding` equals the reservation and its `(plan_ref,work_identity_digest)` WorkSlotKey equals RS15. Immutable terminal mapping. | EI |
| RW07 `recovery_refusal_observation` / O10 | PK `reservation_ref`; `BS122` ref; sole result for that reservation when selected. Immutable, `authority=NONE`. | EI |
| RW08 `recovery_ambiguity_observation` / O10 | PK `reservation_ref`; `BS123` ref; exact transaction/subject chain. Immutable, `authority=NONE`. | EI |
| RW09 `recovery_fence_observation` / O10 | PK `reservation_ref`; `BS124` ref; exact fence subject. Immutable, `authority=NONE`. | EI |
| RW10 `recovery_advancement_observation` / O10 | PK `reservation_ref`; `BS125` ref and exact result body created in the same transaction. Immutable, `authority=NONE`. | EI |
| RW11 `recovery_unproven_observation` / O10 | PK `reservation_ref`; `BS126` ref and preexisting conclusive-noncommit chain. Immutable, `authority=NONE`. | EI |
| RW12 `transaction_resolution_outcome` / O10 | PK `resolution_reservation_ref`; `BS100` ref and exact original/resolver chains. Immutable. | EI |
| RW13 `ambiguity_query_outcome` / O10 | PK `query_reservation_ref`; `BS101` ref and exact original/query chains. Immutable. | EI |
| RW14 `conclusive_noncommit_result` / O10 | PK `original_reservation_ref`; unique resolver reservation; `BS102` ref, both work chains, subject and `BS087` failure evidence. Immutable. | EI |
| RW15 `reconciliation_subject` / O10 | PK `subject_identity_digest`; `BS104` ref and exact tagged transaction/fence/qualification/terminal subject. Immutable. | EI |

### 3.5 Publication, lineage, and verification

| ID / relation / owner | Key; fields and references; restrictions | Evidence |
|---|---|---|
| RP01 `publication_aggregate` / O11 | PK `(operation_identity,action,plan_digest,publication_epoch)`; journal ref, `target_database_identity_ref: BodyRef<BS050>`, and exact surface/lineage identity. Immutable. | EI |
| RP02 `publication_journal` / O11 | PK aggregate key; `BS001` ref; FKs `RA03–RA05`,`RE24`,`RB04`,`RB05`. Immutable. | EI |
| RP03 `pre_stage_expiry_observation` / O11 | PK `(stage,plan_ref,approval_ref,authorization_ref,epoch,predecessor-or-NONE,request_id)`; typed `plan_ref: BodyRef<BS088>`, `approval_ref: BodyRef<BS105>`, `authorization_ref: BodyRef<BS106>`, `BS108` ref and `CURRENT\|LATE`. Immutable. | EI |
| RP04 `publication_proof` / O11 | PK `aggregate_ref`; `BS002` ref and exact `J`; immutable. | EI |
| RP05 `publication_deadline_receipt` / O11 | PK `aggregate_ref`; `BS003` ref, exact `P`, qualified sample and `VALID\|LATE`; immutable. | EI |
| RP06 `publication_mutation_receipt` / O12 | PK `aggregate_ref`; `BS004` ref, exact valid `R`, before/after images, generation and lineage transition. Immutable. | EI |
| RP07 `target_generation_transition` / O12 | PK `(TargetSurfaceKey,new_generation)`; unique predecessor; FK `RP06`; checked increment. Immutable. | EI |
| RP08 `lineage_genesis` / O12 | PK `(lineage_key,0)`; manifest/activation/genesis digest. Immutable. | EI |
| RP09 `lineage_successor` / O12 | PK `(lineage_key,new_generation)`; prior head/generation, predecessor `V`, and new `M`; checked increment. Immutable. | EI |
| RP10 `verification_attempt` / O13 | PK `(m_ref,verification_attempt_id)`; nonnull `request_ref: BodyRef<BS094>`, `reservation_ref: BodyRef<BS097>`, `start_ref: BodyRef<BS098>`, `transaction_identity_ref: BodyRef<BS103>`, and `target_database_identity_ref: BodyRef<BS050>`. These are the exact verification request/incarnation binding. Immutable. | EI |
| RP11 `verification_unable_observation` / O13 | PK `(m_ref,verification_attempt_id)`; `BS008` ref and closed retryable category. Immutable. | EI |
| RP12 `verification_mismatch_observation` / O13 | PK `(m_ref,verification_attempt_id)`; `BS006` ref and observed image. The terminal anchor separately permits at most one terminal outcome for `m_ref`. Immutable terminal fact. | EI |
| RP13 `verification_terminal_failure` / O13 | PK `(m_ref,verification_attempt_id)`; `BS007` ref and exact terminal category. The terminal anchor separately permits at most one terminal outcome for `m_ref`. Immutable terminal fact. | EI |
| RP14 `verification_terminal_current` / O13 | `Anchor<m_ref, MATCH(verification_attempt_id,v_ref) \| MISMATCH(verification_attempt_id,ref) \| TERMINAL_FAILURE(verification_attempt_id,ref)>`; FKs `RP12`,`RP13`,`RP15`. | EC |
| RP15 `publication_verification_receipt` / O13 | PK `(m_ref,verification_attempt_id)`; `BS005` ref, `target_database_identity_ref: BodyRef<BS050>`, and exact generation/cohort/postimage. The terminal anchor separately permits at most one terminal outcome for `m_ref`. Immutable. | EI |

### 3.6 Compatibility, closure, fence, and manifest

| ID / relation / owner | Key; fields and references; restrictions | Evidence |
|---|---|---|
| RC01 `inventory_root` / O18 | PK `cutover_id`; exact discovery-root set, target surface and frozen registry ref. Immutable. | EI |
| RC02 `inventory_observation` / O18 | PK `(cutover_id,ordinal)`; unique observation ID; locator identity, bytes/hashes, reader outcome, target overlap/disposition. Immutable. | EI |
| RC03 `inventory_dependency_edge` / O18 | PK `(cutover_id,parent_observation,edge_ordinal)`; exact dependency role and child identity. Immutable. | EI |
| RC04 `historical_reader_execution_outcome` / O18 | PK `(inventory_observation_id,BS043 ref)`; tagged `BC003 success \| BC004 failure`, exact reader member and dependency closure. Immutable, `authority=NONE`. | EI |
| RC05 `closure_case` / O14 | PK `closure_case_id`; unique `(target_surface,source_inventory_observation_id,source_chain_root_digest,observed_generation,closure_policy_ref)`; the source fixes the stable ID but no assignment or derivation rule; `BC009` ref, `deployment_attestation_ref: BodyRef<BS021>`, `deployment_policy_ref: BodyRef<BS048>`, `clock_envelope_ref: BodyRef<BS014>`, `closure_policy_ref: BodyRef<BS069>`, expected image/cohorts/deadlines, and exact discovery/inventory/reader bindings. Immutable. | EI |
| RC06 `closure_terminal_current` / O14 | `Anchor<case_digest, EXACT_MATCH(ref) \| MISMATCH(ref) \| EXHAUSTED(ref)>`. | EC |
| RC07 `closure_attempt` / O14 | PK `(case_digest,attempt_ordinal)`; unique `(case_digest,observation_request_id)`; reservation identity/deadline and immutable input. | EI |
| RC08 `closure_observer_lease_current` / O14 | `Anchor<(case_digest,ordinal), UNCLAIMED(generation=0) \| CLAIMED(positive_generation,token,incarnation,deadline) \| CLOSED(last_generation)>`; FC02 creates generation zero, each claim/takeover increments exactly once, and close preserves the last generation. One unresolved attempt per case. | EC |
| RC09 `closure_qualified_sample_evidence` / O14 | PK `(case_digest,ordinal,phase,sample_digest)`; `BC011` ref, exact RC05/RC07 attempt binding where applicable, `clock_envelope_ref: BodyRef<BS014>`, and `deployment_attestation_ref: BodyRef<BS021>` copied from the locked case. Immutable. | EI |
| RC10 `closure_attested_invalidation_evidence` / O14 | PK `(case_digest,ordinal,lease_generation)`; `BC012` ref. Immutable. | EI |
| RC11 `closure_comparison_evidence` / O14 | PK `(case_digest,ordinal)`; `BC013` ref and observed image digest. Immutable. | EI |
| RC12 `closure_failure_evidence` / O14 | PK `(case_digest,ordinal)`; `BC014` ref and exact unable/abandonment category. Immutable. | EI |
| RC13 `closure_observation` / O14 | PK `closure_observation_id`; unique `(case_digest,attempt_ordinal)` and `(case_digest,observation_request_id)`; the ID is the lowercase RFC 4122 UUIDv5 in URL namespace `6ba7b811-9dad-11d1-80b4-00c04fd430c8` over the exact LF-terminated source-canonical `{closure_case_digest,observation_request_id,attempt_ordinal}` preimage, using ASCII name `https://github.com/nisavid/agents/tooling/hindsight/closure-observation/v1#sha256=` followed by its 64-character lowercase SHA-256; `BC010` ref and exact tagged evidence link. Immutable, `authority=NONE`. | EI |
| RC14 `manifest_basis` / O18 | PK `cutover_id`; `BC001` ref, inventory root/digest and target; `qualified_clock_envelope_ref: BodyRef<OrdinaryBodyKind>`, `upper_bound_derivation_contract_ref: BodyRef<OrdinaryBodyKind>`, `deployment_attestation_ref: BodyRef<BS021>`, `frozen_reader_registry_ref: BodyRef<BC002>`, `role_grant_set_ref: BodyRef<OrdinaryBodyKind>`, `target_partition_proof_ref: BodyRef<OrdinaryBodyKind>`, `realization_policy_ref: BodyRef<OrdinaryBodyKind>`, `writer_inventory_ref: BodyRef<OrdinaryBodyKind>`, and keyed `quiescence_derivation_contract_refs`. Immutable. | EI |
| RC15 `artifact_exclusion` / O18 | PK `(cutover_id,exclusion_id)`; `BC007` ref, exact current bytes/locator/overlap/failure/basis. Immutable, `authority=NONE`. | EI |
| RC16 `compatibility_approval` / O18 | PK `(subject_kind,subject_digest)`; `BC008` ref plus authenticated channel receipt bytes/digest. Subject only `RC15` or `RC17`. Immutable. | EI |
| RC17 `final_manifest` / O18 | PK `cutover_id`; `BC006` ref, target state, inventory and complete disposition set. Immutable. | EI |
| RC18 `manifest_referenced_evidence` / O18 | PK `(manifest_ref,member_ordinal)`; unique `(manifest_ref,contract_kind,contract_version,body_digest)`; exact typed member ref, inventory/exclusion/closure/reader/fence class and digest. Immutable. | EI |
| RC19 `final_manifest_disposition` / O18 | PK `(manifest_ref,inventory_ordinal)`; exact admitted disposition and supporting refs. Immutable. | EI |
| RC20 `manifest_predecessor_selection` / O18 | PK `(manifest_ref,target_surface)`; unique selected predecessor class/root or `NONE`. Immutable. | EI |
| RC21 `legacy_fence_generation` / O15 | PK `(TargetSurfaceKey,fence_generation)`; one exact `PendingFenceEnvelope`, including the authenticated pre-fence graph, bridged typed target, proposal/invocation/attestation/service-step bindings, the singleton manifest receipt keyed by the parent plus `manifest_approval_digest`, approval-digest-keyed typed exclusion receipt bindings whose stored member key is exactly `.approval_digest`, literal adoption generation zero, and literal current-binding digest `NONE`. It contains no BC019, BC020, BC021, or completed `ProtectedManifestGraphBinding`. Immutable pending fact. | EI |
| RC22 `legacy_fence_state_current` / O15 | `Anchor<(TargetSurfaceKey,fence_generation), FENCE_PENDING \| ACCESS_REVOKED \| SESSIONS_DRAINED \| FENCE_ACTIVE>`; monotonic only. | EC |
| RC23 `realized_admission_evidence` / O15 | PK `fence_generation_ref`; `BC015` ref. Immutable. | EI |
| RC24 `realized_acl_evidence` / O15 | PK `fence_generation_ref`; `BC016` ref. Immutable. | EI |
| RC25 `zero_live_writer_evidence` / O15 | PK `(fence_generation_ref,observation_generation)`; `BC017` ref. Immutable. | EI |
| RC26 `service_disable_evidence` / O15 | PK `(fence_generation_ref,service_identity)`; `BC018` ref. Immutable. | EI |
| RC27 `persistent_legacy_fence_evidence` / O15 | PK `fence_generation_ref`; `BC019` ref and complete supporting evidence. Immutable. | EI |
| RC28 `origin_fence_manifest_binding` / O15 | PK `(TargetSurfaceKey,fence_generation,0)`; `BC020` ref, FK RC21 and RC27, and every field of one completed `ProtectedManifestGraphBinding`: current BC020 handoff, prior `NONE`, bridged target, deployment attestation, persistent fence, manifest basis, registry, reader outcomes, final manifest and its unique approval, exclusions, complete approval and referenced-evidence sets, complete manifest receipt, approval-digest-keyed typed exclusion receipt bindings with their explicit `.approval_digest` destination keys, and every scalar listed by PFC-RC28. Every reference is materialized or equality-checked by a named DRP and every scalar by a named PFC from locked RC21 plus newly derived BC019/BC020 in one transaction. Immutable. | EI |
| RC29 `active_fence_manifest_adoption` / O15 | PK `(TargetSurfaceKey,fence_generation,adoption_generation)`; `BC021` ref, predecessor binding, fresh reserved epoch, and every field of one completed `ProtectedManifestGraphBinding`, including bridged target, deployment attestation, persistent fence, complete graph, complete manifest receipt, approval-digest-keyed typed exclusion receipt bindings with their explicit `.approval_digest` destination keys, and every scalar listed by PFC-RC29. Every reference is materialized or equality-checked by a named DRP and every scalar by a named PFC. The generation is contiguous and the inherited pre-fence members equal the prior binding. Immutable. | EI |

### 3.7 Epoch activation

| ID / relation / owner | Key; fields and references; restrictions | Evidence |
|---|---|---|
| RX01 `publication_epoch_binding` / O16 | PK `PublicationEpochKey`; predecessor active epoch, attestation, proposal, capability digest, manifest/fence binding. Immutable. | EI |
| RX02 `publication_epoch_state_current` / O16 | `Anchor<PublicationEpochKey, RESERVED_FENCED \| ACTIVE \| ABANDONED_FENCED>`; only the two terminal transitions are admitted. | EC |
| RX03 `activation_commit` / O16 | PK `PublicationEpochKey`; exact proposal, witness session, current handoff ref/digest, final manifest, genesis, prior active pointer, and one exact `CutoverClockObservation` whose `clock_envelope_ref: BodyRef<BS014>`, `boot_identity_ref: BodyRef<BS050>`, and `synchronization_epoch_ref: BodyRef<BS050>` are separate nonnull projections. Immutable; replay returns this stored observation and never resamples. | EI |
| RX04 `activation_abandonment` / O16 | PK `PublicationEpochKey`; exact conclusive noncommit/inadmissibility and transaction identity. Immutable. | EI |
| RX05 `activation_session_witness` / O16 | PK `(backend_session_identity,PublicationEpochKey)`; clear capability bytes. Non-WAL, nonbackup, session-local, deleted by session loss. | ES |

### 3.8 Exact 21 current-slot relations

Every row uses the pinned exact key preimage and present-value domain. `subject_revision` is not part of `RS07`’s key.

| ID / slot class / owner | Natural key and exact value | References |
|---|---|---|
| RS01 `ACTIVE_EPOCH` / O16 | `TargetSurfaceKey`; `SafeInteger` | RX01–RX03 |
| RS02 `ACTIVATION_CAPABILITY` / O16 | `PublicationEpochKey`; capability digest | RX01 |
| RS03 `ACTIVATION_PROPOSAL` / O16 | `PublicationEpochKey`; epoch, attestation, manifest digest | RE24,RX01 |
| RS04 `CLOCK_ENVELOPE` / O09 | `TargetSurfaceKey`; `BS014 ref` | RE21 |
| RS05 `DEPLOYMENT_ATTESTATION` / O09 | `PublicationEpochKey`; `BS021 ref` | RE24 |
| RS06 `DEPLOYMENT_POLICY` / O08 | `TargetSurfaceKey`; `BS048 ref` | RE19 |
| RS07 `EVIDENCE_TIER_RESULT` / O05 | exactly `(claim_id,tier)`; `BS039 ref` | RE16 |
| RS08 `LEGACY_FENCE` / O15 | `TargetSurfaceKey`; exactly `BC020` or `BC021` ref | RC28,RC29 |
| RS09 `LINEAGE_HEAD` / O12 | `TargetSurfaceKey`; exact `M` digest | RP06,RP09 |
| RS10 `OPERATION_ACCOUNTING` / O10 | `plan_ref`; exact `OperationAccountingState`: matching plan, `j_attempts`, `p_attempts`, `r_attempts`, `m_attempts`, `verification_attempts`, `reconciliation_attempts`, `charged_selected_rows`, `charged_preserved_rows`, `charged_mutated_rows`, `charged_elapsed_ns`, `charged_reconciliation_ns`, and `next_reservation_ordinal`. FA03 initializes every charge/counter to zero and the ordinal to one. | RA03,RW01–RW03 |
| RS11 `OPERATION_AUTHORITY` / O04 | `plan_ref`; exact current `BS106 ref` when authorized | RA05,RA07 |
| RS12 `OPERATION_GRANT` / O04 | exactly `grant_id`; current `BS089 ref` | RA01,RA08 |
| RS13 `OPERATION_WORK_RESERVATION` / O10 | exact `WorkSlotKey`; `BS097 ref` | RW03 |
| RS14 `OPERATION_WORK_START` / O10 | exact `WorkSlotKey`; `BS098 ref` | RW05 |
| RS15 `OPERATION_WORK_COMMITTED_RESULT` / O10 | exact `WorkSlotKey`; `BS099 ref` | RW06 |
| RS16 `PUBLICATION_EPOCH_HIGH_WATER` / O16 | `TargetSurfaceKey`; `SafeInteger` | RX01 |
| RS17 `QUALIFICATION_RECEIPT` / O09 | `TargetSurfaceKey`; `BS020 ref` | RE18 |
| RS18 `RESERVED_ACTIVATION` / O16 | `TargetSurfaceKey`; epoch, attestation, literal `RESERVED_FENCED` | RE24,RX01,RX02 |
| RS19 `ROLE_GRANT_SET` / O09 | `TargetSurfaceKey`; `BS061 ref` | RE22 |
| RS20 `TARGET_GENERATION` / O12 | `TargetSurfaceKey`; `SafeInteger` | RP07 |
| RS21 `WRITER_INVENTORY` / O09 | `TargetSurfaceKey`; `BS063 ref` | RE23 |

All 21 are `EC`. Cross-class values, wrong keys, wrong kinds/versions, hidden values under `ABSENT`, and values whose internal key fields differ from the anchor are rejected.

### 3.9 Deployment metadata

| ID / relation / owner | Key; fields and restrictions | Evidence |
|---|---|---|
| RM01 `schema_generation` / O01 | PK `generation`; predecessor, migration ID/digest, accepted-contract revision, exact FK `active_surface_manifest_ref: SchemaManifestRef<ACTIVE_SURFACE>` used as `protected_schema_digest`, and compatible adapter generations/digests. Inserted for G0 at bootstrap or for a candidate only after preparation succeeds. Immutable. | ED |
| RM02 `schema_state_current` / O01 | Singleton persistent anchor containing phase, active/candidate/rollback/route generations, attempt ID, rollback eligibility, exact `InstalledSchemaState`, and the RM03 transition that admitted it. `EXACT` carries both RM05 FKs and matching digests. `UNCLASSIFIED_PREPARATION` is admitted only in PREPARING, preserves the last exact identity and active-surface ref, and names the current immutable RM04/RM06 fingerprint record. | ED |
| RM03 `schema_transition` / O01 | PK `transition_id`; event, complete before/after RM02 state digests, exact before/after `InstalledSchemaState`, route-active-manifest FK, migration digest, optional exact RM04/RM06 event-record ref, and authenticated deployment principal. Metadata-only transitions reuse identical exact-state refs. Every admitted physical, classification, or metadata phase change appends one row; immutable. | ED |
| RM04 `schema_preparation_failure` / O01 | PK `attempt_id`; base/proposed generation, migration digest, failed step/category, `observed_catalog_manifest_ref: SchemaManifestRef<CATALOG>\|NONE`, unclassified-object fingerprint-set digest when that ref is NONE, residue classification, and proof whether the prior active surface remains exact. Immutable. A NONE result moves RM02 to the explicit `UNCLASSIFIED_PREPARATION` representation and cannot enter retry or abandonment until MT11 classifies it. | ED |
| RM05 `schema_manifest` / O01 | PK `(manifest_kind,manifest_version,digest)` where kind is `ACTIVE_SURFACE\|CATALOG` and version is exactly 1; extraction-contract and canonical-encoding IDs, accepted-contract revision, exact `PostgresqlBuildIdentity/v1` and matching build digest, target database identity, route generation or `NONE`, phase instance or `NONE`, byte length, and exact canonical bytes. No FK to RB01. Source decoding accepts only the exact §8 grammar; PostgreSQL recomputes `octet_length` and `sha256`. Immutable; exact replay only. | ED |
| RM06 `schema_preparation_remediation` / O01 | PK `(attempt_id,remediation_ordinal)`; unique `(attempt_id,remediation_request_digest)` and at most one `CLASSIFIED` result per attempt. FK to RM04; prior and observed fingerprint-set digests, exact remediation/migration artifact digest, authenticated deployment principal, and outcome `STILL_UNCLASSIFIABLE(fingerprint)` or `CLASSIFIED(catalog_manifest_ref,active_surface_manifest_ref,active_surface_exact)`. Ordinals are checked-next under the RM02 attempt lock. Every row is immutable; a failed classification only advances the explicit unclassified fingerprint, while `CLASSIFIED` participates atomically in MT11. | ED |

## 4. Constraint and index responsibilities

The database must enforce:

- Every `Immutable` body’s exact kind/version and body FK.
- Every newly inserted RB01 body at a §5.2.1 site commits in the same outer transaction as the fact that first requires it; rollback leaves neither. FB01/FB02 accept no private, ciphertext, transient, missing, or extra closure member.
- Every BREQ relation edge uses exactly one semantic mode/outcome pair: `READ/NO_WRITE`, `WRITE/APPLIED`, or `VERIFY_PRESENT/EXACT_REPLAY`. Physical INSERT/UPDATE/CLEAR selection is never encoded by a body-member generator. A no-replacement anchor clear has no supplied or derived write-body edge; its old current body is a `READ/NO_WRITE` root and the SC relation delta defines the clear.
- `RequestKey` equality uses complete `BS094` bytes. `ReservationKey` and production `WorkSlotKey` each use exactly `(plan,work_identity_digest)` in distinct roles; `WorkIdentityBinding` separately requires the complete identity bytes to recompute and equal that digest. The AuthorityGate operation-work preimage adds `key_kind`, complete identity, and `slot_class` only in the source conformance interface.
- One immutable request per RequestKey, one refusal per RequestKey, at most one reservation request for `(plan,request_id)`, one reservation/result chain per ReservationKey, and exact production WorkSlotKey plus complete WorkIdentityBinding equality across RW03–RW06 and RS13–RS15.
- New FA03 issuance, RA03/RA07 publication, and complete zero-state RS10 initialization are one atomic outcome. Replay never resets accounting.
- FE08 can insert only RB01/RE10 and cannot create a disposition or change a result. FE10 alone verifies RE12, creates the fresh RE05, writes exactly RE11 or RE13, and replaces every affected RE16/RS07 atomically.
- Every new RE16 row projects exactly the protected post-transition RE15 subject for its own tier. FE04, FE07, and FE10 derive that value only from locked current state. FE09 first stages its authorized RE15 replacement and then invokes the same evaluator rule. Each applied or exact-replay branch verifies the complete VR001–VR004 source-read, subject-field, result-body, and RS07 atomic group; a subject mismatch writes none.
- `P→J`, `R→P`, `M→VALID R`, and `V→M`.
- Both `RB04` and `RB05` in the same transaction as `RP02`.
- One start and one terminal result per ReservationKey, with the production WorkSlotKey and complete WorkIdentityBinding repeated and revalidated.
- Exact result-kind-to-relation closure for `RW06`.
- The six-row/two-slot atomic outcome required by `RW14`.
- Checked target, lineage, fence, adoption, closure-attempt, and epoch increments.
- Mutual exclusion among `RP12`, `RP13`, and `RP15`.
- One unresolved closure attempt per case.
- RC05's stable `closure_case_id`, separately unique complete case key, and explicit-ABSENT RC06 initialize together; RC13 admits only the exact deterministic `closure_observation_id` plus its unique attempt and request keys; RC07, initial RC08, and its reservation sample initialize together; every closure result closes RC08 in the same transaction.
- Monotonic fence and epoch-state transitions.
- RC21 always retains pending `current_manifest_binding_digest=NONE`; it can never satisfy RS08 or activation. FF04 alone creates RC27 and RC28 and installs RS08 atomically. Each later RS08 value resolves exactly one contiguous RC29 successor.
- FF01 must pass both fresh `FENCE_START` and immediately-precommit `FENCE_COMMIT` gates under its currentness locks. FF02 and FF03 must pass one fresh finite-timeout `FENCE_STEP` gate before every external step and retain the locks through exact outcome recording. A new FF05 adoption must pass one post-revalidation `ADOPTION` gate. FX01 must pass the post-revalidation `CUTOVER` gate. Every gate uses the complete seven-class FD001–FD007 set, including the locked current BS048 policy expiry, strict inequality, equality-late handling, and no-effect failure rules; only adapter-local invocation consumption can remain changed after an FF01 rollback.
- Activation stores the complete compatibility metadata set, RP08, and RS20 atomically with RX02/RS01 and selector clearing; RS09 remains absent until the first M.
- In occupied-fence admission, RC29 and RS08 commit in the same transaction as RE24, RX01, RX02, and their selectors.
- `FailedDeploymentResult` uniqueness by `(campaign,deployment_attempt_id)` and exclusion from every authority/current-attestation domain.
- Retired policy-pair non-reinstatement.
- The private composite identity, one package per deciding run, random public ID, and non-disclosure boundary.
- No updates, deletes, or truncation of immutable facts by runtime roles.
- Every RM01–RM04 and RM06 manifest reference resolves one exact immutable RM05 row with matching kind, version, bytes, length, and digest. An RM02 `UNCLASSIFIED_PREPARATION` state contains no invented catalog manifest.

Primary and unique keys provide their own indexes. Required additional indexes are:

| Index identity | Lookup path |
|---|---|
| `IX-RB03-1(target,surface,lineage_key)` | Candidate revalidation and plan issuance |
| `IX-RA03-1(grant_id)` | Grant revocation and stage authority |
| `IX-RE06-1(campaign,run_id,oracle)` | Run-result registration |
| `IX-RE07-1(campaign,run_id)` | Run evaluation |
| `IX-RE11-1(finding_ref)` | Invalidation validation |
| `IX-RE13-1(successor_campaign)` | Supersession-cycle detection |
| `IX-RW01-1(plan,request_id)` | REQUEST_CONFLICT lookup across exact request bodies |
| `IX-RW01-2(plan,work_identity_digest)` | ReservationKey preflight and different-request detection |
| `IX-RW03-1(plan,attempt_ordinal)` | Checked-next accounting |
| `IX-RW04-1(transaction_identity_id)` | Ambiguity resolution |
| `IX-RW06-1(result_kind,result_ref)` | Exact result recovery |
| `IX-RP02-1(body_digest)` | Exact stage recovery |
| `IX-RP03-1(plan,stage,predecessor)` | Pre-stage replay/conflict |
| `IX-RP09-1(lineage_key,prior_generation,prior_head)` | Lineage revalidation |
| `IX-RP10-1(m_ref,verification_attempt_id)` | Verification replay |
| `IX-RC02-1(cutover_id,observation_id)` | Inventory resolution |
| `IX-RC03-1(child_observation)` | Dependency closure |
| `IX-RC07-1(case_digest,observation_request_id)` | Closure replay |
| `IX-RC07-2(case_digest) WHERE unresolved` | Single unresolved closure attempt |
| `IX-RC18-1(typed_member_ref)` | Manifest-closure validation |
| `IX-RC21-1(target,surface,fence_generation)` | Fence recovery |
| `IX-RC26-1(fence_generation,service_identity)` | Service-step recovery |
| `IX-RX01-1(target,surface,epoch)` | Activation/recovery |
| `IX-RM03-1(active_generation,event)` | Deployment-state audit |
| `IX-RM06-1(attempt_id) UNIQUE WHERE outcome=CLASSIFIED` | One terminal classification per failed preparation |

Every anchor also has a unique natural key and unique key digest. Referencing-side FK indexes are added only where listed or where later measured protected lookup evidence demonstrates a need. No blanket FK-index rule or exclusion constraint is implied.

## 5. Closed protected callable registry

### 5.1 Roles and result rules

Owners:

```text
O01 schema-deployment  O02 body  O03 protected-material
O04 operation-authority  O05 evidence  O06 qualification
O07 private-evidence  O08 deployment-policy  O09 admission
O10 work  O11 publication  O12 mutation  O13 verification
O14 closure  O15 fence  O16 activation  O17 status
O18 compatibility
```

Callers:

```text
C01 preimage-constructor  C02 grant-issuer  C03 plan-issuer
C04 approver  C05 authorizer  C06 revoker  C07 plan-authority
C08 evidence-producer  C09 evidence-authority
C10 qualification-submitter  C11 private-registrar
C12 private-reviewer  C13 public-exporter  C14 policy-authority
C15 admission-author  C16 publication-adapter
C17 continuity-client  C18 verification-adapter
C19 recovery-adapter  C20 closure-adapter  C21 fence-adapter
C22 status-observer  C23 deployment-principal
C24 ordinary-runtime  C25 historical-inventory-reader
```

`PUBLIC` is the forty-fourth principal class. `ALLOW(F)` is exactly the caller set in the table. `DENY(F)` is the complete closed role set minus `ALLOW(F)`. Thus every positive grant has enumerable reciprocal negative cells.

Rejection ownership:

- `S`: source codec or resolver rejects malformed, unknown, missing, mismatched, cyclic, or noncanonical input.
- `D`: the function rejects stale current state, expiry, transition, identity, reference, constraint, or relational conflict.
- `A`: PostgreSQL ACL denies a caller before execution.
- `T`: the adapter owns transaction setup, unsupported schema generation, connection failure, and commit acknowledgement.

Function evidence profiles:

- `FM`: positive, exact replay, changed-binding conflict, stale/current refusal, ACL, rollback.
- `FX`: `FM` plus multi-relation atomicity.
- `FR`: positive/absent/error/ACL and proof of no mutation; CAS and commit atomicity are not applicable.
- `FT`: read-only transaction-local response; durable replay and mutation are not applicable.
- `FN`: exact two-slot noncommit outcome.
- `FE`: external effect authorization plus atomic durable effect evidence.

### 5.2 Bodies and authority

| ID / logical function | Allow → owner | Caller input; database-derived operands; effects/result |
|---|---|---|
| FB01 `ensure_ordinary_body` | `O03–O15,O18` → O02 | One exact `OrdinaryBodySiteUse` and `ResolvedOrdinaryBodyItem` admitted by an MRR or MRE insertion record; derives the BM/SC/owner/path/branch/ordinal/member, complete BREQ set, atomic group, hash, length and existing identity. Inserts or exact-replays RB01 in the outer transaction only when every requiring edge is present in that transaction, returns only its typed ref, and cannot commit independently. `FM`. |
| FB02 `resolve_ordinary_body` | `O03–O16,O18` → O02 | One exact `OrdinaryBodySiteUse` and `BodyRef` admitted by an MRR, MRE, or DLI resolution record; derives the BM/SC/owner/path/branch/ordinal/member, complete BREQ set, atomic group and stored bytes/hash/length. Reads RB01 and returns exact bytes to the calling owner only; absence, mismatch, or an unbound requiring edge fails the outer operation. No listing or login result. `FR`. |
| FB03 `register_rollback_candidate` | C01 → O03 | `BS121`, exact `BS110` descriptor/ciphertext bytes and complete payload/conversion refs; derives hashes, target, lineage and membership equality. Atomically writes RB02,RB03. `FX`. |
| FA01 `ISSUE_OPERATION_GRANT(grant)` | C02 → O04 | Exact `BS089`; derives session principal and absent grant key. Writes RA01,RA08,RS12. `FX`. |
| FA02 `REVOKE_OPERATION_GRANT(grant_id,expected_current_grant,revocation)` | C06 → O04 | Exact CAS and `BS090`; derives current lifecycle. Writes RA02,RA08 and clears RS12. `FX`. |
| FA03 `ISSUE_OPERATION_PLAN(plan)` | C03 → O04 | Exact `BS088`; derives principal, current grant, RB03/RB02, limits and shared deadline. On new issuance it writes RA03 and RA07 and invokes IA01, so complete zero-state RS10 commits in the same transaction. Exact replay validates an existing RS10 belonging to the plan but never requires or restores zero after reservations. Missing RS10 under a committed plan is an invariant conflict. `FX`. |
| FA04 `APPROVE_OPERATION_PLAN(plan,expected_current_approval,approval)` | C04 → O04 | Exact plan, expected absence/ref and `BS105`; derives principal and lifecycle. Writes RA04,RA07. `FX`. |
| FA05 `AUTHORIZE_OPERATION(plan,approval,expected_current_authorization,receipt)` | C05 → O04 | Exact chain/CAS and `BS106`; derives principal and deadline equality. Writes RA05,RA07,RS11. `FX`. |
| FA06 `REVOKE_OPERATION_AUTHORITY(plan,expected_current_approval,expected_current_authorization,revocation)` | C06 → O04 | Both exact expected operands and `BS107`; derives lifecycle. Writes RA06,RA07 and clears RS11. `FX`. |
| FA07 `READ_CURRENT_OPERATION_AUTHORITY(plan)` | O11,O12 → O04 | Plan already bound by stage request; derives RA07/RA08/RS11/RS12 chain. Returns exact current authority or refusal; no write. `FR`. |

### 5.2.1 Shared ordinary-body call graph

The BM table is the closed atomic-site and non-RB01-delta catalog. Its root columns are navigation summaries, not the member definition. `S[x]` denotes a source-supplied ordinary root, `D[x]` a server-derived root, and `R[x]` a database-resident root. Exact direct-root membership is the MRR registry below; recursive membership is MRE; digest-only compatibility linkage is DL.

Each BM source identity is `(SC source span, operation, source field path, S|D|R, root domain, branch, cardinality)`. The accepted operation paragraph and body grammar, not this proposal's presence or count, determine membership.

| BM / protected site / executing owner / source | Exact FB01 roots | Exact additional FB02 roots | Complete non-RB01 atomic delta |
|---|---|---|---|
| BM001 / FB03 / O03 / SC003 | `S[BS121 rollback_preimage_binding]` | `NONE`; BS110 descriptor and ciphertext are RB02-only | RB02+RB03 |
| BM002 / FA01 / O04 / SC004 | `S[BS089 grant]` | `NONE` | RA01+RA08+RS12 |
| BM003 / FA02 / O04 / SC005 | `S[BS090 revocation]` | `R[RA01.BS089]` | RA02+RA08+RS12 clear |
| BM004 / FA03 / O04 / SC006 | `S[BS088 plan]` | `R[RA01.BS089,RB03.BS121]`; RB02 is resolved separately | RA03+RA07+IA01's RS10 |
| BM005 / FA04 / O04 / SC007 | `S[BS105 approval]` | `R[RA03.BS088]` | RA04+RA07 |
| BM006 / FA05 / O04 / SC008 | `S[BS106 authorization]` | `R[RA03.BS088,RA04.BS105]` | RA05+RA07+RS11 |
| BM007 / FA06 / O04 / SC009 | `S[BS107 revocation]` | `R[RA01.BS089,RA03.BS088,RA04.BS105?,RA05.BS106?]` | RA06+RA07+RS11 clear |
| BM008 / FA07 / O04 / SC010 | `NONE` | `R[RA01.BS089,RA02.BS090?,RA03.BS088,RA04.BS105?,RA05.BS106?,RA06.BS107?]` | NONE |
| BM009 / FE01 / O05 / SC011 | `S[BS023 plan,BS024 acceptance]` | `NONE` | RE01 |
| BM010 / FE02 / O05 / SC012 | `S[BS040 plan,BS041 acceptance]` | `NONE` | RE02 |
| BM011 / FE03 / O05 / SC013 | `S[BS016 plan,BS018 acceptance]` | `NONE` | RE03 |
| BM012 / FE04 / O05 / SC014 | `S[BS022 campaign]+D[BS039* affected tier results]` | `R[RE01 plan/acceptance,RE15 campaign-tier subject,RE15 subject for every affected result key,RE16 prerequisite/current BS039*]` | RE04+RE16*+RS07* |
| BM013 / FE05 / O05 / SC015 | `D[one BS034 observed projection,one BS015 DEPLOYMENT_EVIDENCE_ACQUIRE observation,one BS064 acquisition]` | `R[RE04.BS022,RE01.BS023/BS024,planned BS032/BS033/BS034/BS066,RS06.BS048,support BS009,RS04.BS014]` | all three bodies+RE05+RE06 |
| BM014 / FE06 / O05 / SC016 | `S[BS014 envelope?]+D[one BS015 observation]` | `R[RS04.BS014 when selected]` | RE05 |
| BM015 / FE07 / O05 / SC017 | `S[BS022 campaign,BS015 start,BS049* retained artifacts,BS034* expected/observed projections,BS033* oracle contracts,BS064* acquisitions]+D[BS015* completion observations,BS035* records,BS036? failure,one BS037 result,BS039* affected tier results]` | `R[RE01 accepted basis,RE15 subject for every affected result key,RE16 prerequisite/current BS039*,current BS014]` | RE05*+RE07*+RE08?+RE09+RE16*+RS07* |
| BM016 / FE08 / O05 / SC018 | `S[BS038 finding]` | `R[RE07.BS035,RE06.BS064?,exact BS033/BS034 finding dependencies]` | RE10 only |
| BM017 / FE09 / O05 / SC019 | `S[replacement EvidenceRef in the accepted subject domain]+D[BS039* affected recomputed results]` | `R[RE15 prior subject,post-transition RE15 subject for every affected result key,RE04 selected campaigns,RE16 current/prerequisite BS039*]` | RE14+RE15+RE16*+RS07* |
| BM018 / FE10 / O05 / SC020 | `S[BS045 receipt; INVALIDATION BS022/BS035/BS038 or SUPERSESSION BS022*/BS039]+D[one BS015 apply observation,exactly BS046 or BS047,BS039* affected results]` | `R[RE10 finding for invalidation,RE04 campaigns,RE15 subject for every affected result key,RE16 current/prerequisite BS039*,current BS014]` | RE12+RE05+(RE11 or RE13)+RE16*+RS07* |
| BM019 / FE11 / O05 / SC021 | `NONE` | `R[RS07.BS039]` | NONE |
| BM020 / IA02 / O05 / SC023 | `D[one BS015 QUALIFICATION_RECEIPT_ISSUE observation]` | `R[RE03.BS016/BS018,BS009,RE17.BS019 exactly three,RS07.BS039*,RS04.BS014]` | RE05 |
| BM021 / IA03 / O05 / SC030 | `D[one BS015 DEPLOYMENT_ATTESTATION_ISSUE observation]` | `R[RE18.BS020,RE19.BS048,RE04.BS022,RE06.BS064*,RE17.BS019*,RE16.BS039*,RE21.BS014,exact support/live-binding refs]` | RE05 |
| BM022 / IA04 / O05 / SC034 | `D[one BS015 OPERATION_WORK_RESERVE observation]` | `R[RA03.BS088,RA01.BS089,RW01.BS094,RE21.BS014]` | RE05 |
| BM023 / FQ01 / O06 / SC022 | `S[BS016,BS018,BS037* complete run sequence]+D[one BS019 class result]` | `R[RE03 accepted plan,RE07–RE09 facts for every run]` | RE17 |
| BM024 / FQ02 / O06 / SC023 | `S[BS016,BS018,BS009,BS019 exactly three,BS039* three complete tier sequences]+D[one BS020 receipt]` | `R[RE03,RE17 exactly three,RS07 current/prerequisite BS039*]` | RE18+BM020's RE05 |
| BM025 / FPV01 / O07 / SC024 | `NONE`; BS080 is private | `R[BS037 deciding result,BS049* artifacts,BS068 limits,BS079 real binding,accepted subject ref and their ordinary closure]` | RB06+RB07 |
| BM026 / FPV02 / O07 / SC025 | `NONE` | `R[BM025 package dependencies,RB08 authorization refs]` | NONE |
| BM027 / FPV03 / O07 / SC026 | `S[BS081 projection,BS082 receipt]` | `R[BM025 package dependencies,current evidence/policies,RB08 authorization]` | RB10+RB11+RB12 |
| BM028 / FPV04 / O07 / SC027 | `NONE` | `R[RB10.BS081,RB11.BS082,BM025 package dependencies,current evidence/policies]` | NONE |
| BM029 / FDP01 / O08 / SC028 | `S[BS048 replacement?]` | `R[RS06 expected/current BS048?]` | RE19+RE20?+RS06 |
| BM030 / FAD01 / O09 / SC029 | `S[BS014 clock envelope]` | `R[exact target BS050 plus support/live host and boot refs]` | RE21+RS04 |
| BM031 / FAD02 / O09 / SC030 | `S[BS034 candidate,BS020 receipt,COMPATIBILITY BC006/BC008]+D[success BS061/BS063/BS021 or failure BS044]` | `R[exact target BS050,BS009,BS014,BS019*,BS022,BS039*,BS048,BS060,BS064*,BS083,current BC020\|BC021 and exact accepted closures]` | failure RE25 only; success RE22–RE24+RS05+RS17+RS19+RS21+IA03+IA05; COMPATIBILITY also BM059 |
| BM032 / FAD03 / O09 / SC031 | `NONE` | `R[RE24.BS021,current BS048/BS020/BS039* bindings]` | RS05 clear |
| BM033 / FAD04 / O09 / SC032 | `NONE` | `R[RE24.BS021,current BC020\|BC021 and BC006 binding]` | IA06's RS02+RS03 |
| BM034 / IA09 / O09 / SC058 | `NONE` | `R[RE24.BS021,RW14.BS102 or other exact conclusive proof,RW04.BS103]` | RS05 clear |
| BM035 / FW01 / O10 / SC033 | `NONE` | `R[matching existing BS094/BS097/BS098/BS103/BS099 and its exact typed result]` | NONE |
| BM036 / FW02 / O10 / SC034 | `S[BS094 request]+D[refusal BS096 or reservation BS097]` | `R[RA03.BS088 and limit closure,existing request/reservation chain,RE21.BS014]` | refusal RW01+RW02; success RW01+RW03+RS10+RS13+BM022 |
| BM037 / FW03 / O10 / SC035 | `D[one BS103 transaction identity,one BS098 start]` | `R[RW01.BS094,RW03.BS097,RA03.BS088,RE21.BS014]` | RW04+RW05+RS14 |
| BM038 / FW04 / O10 / SC036 | `D[one BS099 committed-result mapping]` | `R[RW01.BS094,RW03.BS097,RW04.BS103,RW05.BS098,caller-owner's staged permitted typed result]` | RW06+RS15; the typed result fact is staged by its owning outer site |
| BM039 / FW05 / O10 / SC037 | `D[one BS100 resolution outcome]` | `R[complete original/resolver BS094/BS097/BS098/BS103/BS099 chains and original typed result]` | RW12+one BM038 |
| BM040 / FW06 / O10 / SC038 | `D[committed branch BS101 or inconclusive branch BS123]` | `R[complete original/query chains and protected transaction state]` | RW13 or RW08+one BM038 |
| BM041 / FW07 / O10 / SC039 | `S[BS104 subject,BS087 failure evidence]+D[one BS102 conclusive result,one BS125 advancement]` | `R[complete original/resolver BS094/BS097/BS098/BS103 chains]` | RW14+RW10+two BM038 mappings/slot closes |
| BM042 / FW08 / O10 / SC040 | `NONE` | `R[exact selected RW01–RW15 body roots and RS13–RS15 values]` | NONE |
| BM043 / FJ01 / O11 / SC041 | `D[one BS108 observation and, on CURRENT only,one BS001 J]` | `R[BS088/BS089/BS105/BS106,BS021,BS121,BS094/BS097/BS098/BS103,current lineage and exact payload refs]` | LATE RP03+BM038; CURRENT RP01+RP02+RP03+RB04+RB05+BM038 |
| BM044 / FJ02 / O11 / SC042 | `D[one BS108 observation and, on CURRENT only,one BS002 P]` | `R[RP02.BS001,authority/admission/work and lineage roots]` | LATE RP03+BM038; CURRENT RP03+RP04+BM038 |
| BM045 / FJ03 / O11 / SC043 | `D[one BS003 VALID or LATE receipt]` | `R[RP02.BS001,RP04.BS002,BS014,authority/admission/work and lineage roots]` | RP05+BM038 when a typed result is admitted |
| BM046 / FM01 / O12 / SC044 | `D[BS117 before image,BS117 after image,one BS004 M]` | `R[BS001/BS002/BS003,BS021,BS094/BS097/BS098/BS103,BS118\|BS119/BS120/BS121 and exact lineage refs]`; BS110 remains RB02-only | target mutation+RP06+RP07+RP09+RS09+RS20+BM038 |
| BM047 / IA08 / O12 / SC057 | `NONE` | `R[BC006 target state,BC005? restore content,BS021,current BC020\|BC021 activation binding]` | RP08+RS20 |
| BM048 / FV01 / O13 / SC045 | `D[UNABLE BS008 or MATCH BS005 or MISMATCH BS006 or TERMINAL_FAILURE BS007]` | `R[RP06.BS004,its BS117 images,work/admission/lineage roots]` | selected RP10–RP15 branch+BM038 |
| BM049 / FC01 / O14 / SC046 | `S[BS043 reader binding and referenced BC003\|BC004 reader outcomes]+D[one BC009 case binding]` | `R[BS021,BS048,BS014 and exact inventory/source descriptors]` | RC05+explicit-ABSENT RC06 |
| BM050 / FC02 / O14 / SC047 | `D[one BC011 RESERVATION sample]` | `R[RC05.BC009,BS014,closure-limit/policy refs]` | RC07+RC08 UNCLAIMED(0)+RC09 |
| BM051 / FC03 / O14 / SC048 | `D[one BC011 CLAIM sample]` | `R[RC05.BC009,RC07,BS014]` | RC08 CLAIMED+RC09 |
| BM052 / FC04 / O14 / SC049 | `S[BC012 invalidation]+D[one BC011 TAKEOVER sample]` | `R[RC05.BC009,RC07,current RC08,BS014]` | RC10+RC09+advanced RC08 |
| BM053 / FC05 / O14 / SC050 | `D[one BC011 FINALIZATION sample,exactly BC013 comparison or BC014 failure,one BC010 observation]` | `R[RC05.BC009,RC07,current RC08,BS014 and typed target input roots]` | RC09+(RC11 or RC12)+RC13+RC08 CLOSED+RC06? |
| BM054 / FC06 / O14 / SC051 | `D[one BC011 expiry sample,one BC014 failure,one BC010 observation]` | `R[RC05.BC009,RC07,current RC08,BS014]` | RC09+RC12+RC13+RC08 CLOSED+RC06? |
| BM055 / FF01 / O15 / SC052 | `S[authenticated pre-fence manifest graph,manifest approval receipt,and approval-digest-keyed exclusion receipts]+D[one BC015 admission evidence,one BC016 ACL evidence]` | `R[BS021,BS048,BS061,BS063,BS014,BS020,BS039* current PASS partitions]` | two transaction-local time gates+external access revocation+RC21 pending envelope with binding NONE+RC22–RC24 |
| BM056 / FF02 / O15 / SC053 | `D[one BC017 zero-writer result]` | `R[BS021,BS048,BS061,BS063,BS014,BS020,BS039* current PASS partitions,BC015,BC016]` | per-external-step time gates+RC25+RC22 SESSIONS_DRAINED |
| BM057 / FF03 / O15 / SC054 | `S[exact disable-attestation ordinary body]+D[one BC018 service-disable result]` | `R[BS021,BS048,BS063,BS014,BS020,BS039* current PASS partitions,BC015,BC016]` | per-service time gate+external disable+RC26 |
| BM058 / FF04 / O15 / SC055 | `D[one BC019 persistent-fence evidence,one BC020 origin binding]` | `R[every reference-bearing RC21 pending-envelope member,BC015/BC016/BC017*/BC018*]` | RC27+completed RC28 binding+RS08+RC22 FENCE_ACTIVE |
| BM059 / FF05 / O15 / SC056 | `S[BC006 final manifest,BC008 approval]+D[one BC021 adoption]` | `R[BC019,current BC020\|BC021,BS021,BS048,BS014,BS020,BS039* current partitions]` | one post-revalidation adoption gate+RC29+RS08; nested mode retains FAD02's outer group |
| BM060 / FX01 / O16 / SC057 | `NONE` | `R[database-derived ActivationManifestGraphProjection,BS021,BS014,BS039*]` | IA07+IA08+RX03 including CutoverClockObservation+RX05+RX02+RS01–RS03/RS18 transitions |
| BM061 / FX02 / O16 / SC058 | `NONE` | `R[BS021,BS100\|BS102,BS103 and exact reserved/proposal roots]` | RX04+RX02+RS02/RS03/RS18 clear+IA09 |
| BM062 / IA07 / O18 / SC057 | `NONE` | `R[the same database-derived ActivationManifestGraphProjection passed by FX01]` | RC01–RC04+RC14–RC20 |

The requiring non-RB01 delta in each BM row is part of the same outer transaction. Exact body membership is defined by the following present, source-derived records.

An MRR token `Xnn` on row `BMmmm` has stable pattern identity `MRR-BMmmm-Xnn`; `S`, `D`, and `R` mean SUPPLIED/FB01, DERIVED/FB01, and DATABASE/FB02. `one(path:K)` emits one root record at ordinal zero. `opt[p](path:K)` emits zero or one under predicate `p`. `seq(q[i],path:K)` emits one record for every contiguous source sequence ordinal. `set(q{d},path:K)` emits one record for every canonical-set member, identified by its body digest. `keyed(q{key},path:K)` emits one record for every member of a closed stable-key set, sorts by complete canonical key bytes, assigns that order’s zero-based root ordinal, includes the complete key in the record identity, and retains equal body references under distinct keys. A missing, duplicate, or extra key rejects. `branch[p]` emits only for that closed branch. `ORD` is exactly `OrdinaryBodyKind`. Every emitted record carries its BM row’s SC source anchor, executing owner, `AG-BMmmm` atomic-group identity, and the exact nonempty BREQ set defined after the registry. It does not inherit the BM row’s whole delta.

```text
BM001 S01=one(input.rollback_preimage_binding:BS121)
BM002 S01=one(input.grant:BS089)
BM003 S01=one(input.revocation:BS090); R01=one(RA01.grant_ref:BS089)
BM004 S01=one(input.plan:BS088); R01=one(RA01.grant_ref:BS089); R02=one(RB03.rollback_preimage_binding_ref:BS121)
BM005 S01=one(input.approval:BS105); R01=one(RA03.plan_ref:BS088)
BM006 S01=one(input.authorization_receipt:BS106); R01=one(RA03.plan_ref:BS088); R02=one(RA04.approval_ref:BS105)
BM007 S01=one(input.revocation:BS107); R01=one(RA01.grant_ref:BS089); R02=one(RA03.plan_ref:BS088); R03=opt[approval-present](RA04.approval_ref:BS105); R04=opt[authorization-present](RA05.authorization_ref:BS106)
BM008 R01=one(RA01.grant_ref:BS089); R02=opt[grant-revoked](RA02.revocation_ref:BS090); R03=one(RA03.plan_ref:BS088); R04=opt[approved-or-later](RA04.approval_ref:BS105); R05=opt[authorized-or-later](RA05.authorization_ref:BS106); R06=opt[authority-revoked](RA06.revocation_ref:BS107)
BM009 S01=one(input.campaign_plan:BS023); S02=one(input.acceptance:BS024)
BM010 S01=one(input.corpus_plan:BS040); S02=one(input.acceptance:BS041)
BM011 S01=one(input.qualification_plan:BS016); S02=one(input.acceptance:BS018)
BM012 S01=one(input.campaign:BS022); D01=set(derived.affected_tier_results{digest}:BS039); R01=one(RE01.plan_ref:BS023); R02=one(RE01.acceptance_ref:BS024); R03=opt[current-subject-present](RE15[input.campaign.tier].subject_ref:ORD); R04=set(affected_RE16{key}.current_result_ref:BS039); R05=seq(affected_RE16{key}.prerequisite_result_refs[i],item:BS039); R06=keyed(affected_RE16{key},RE15[affected_RE16{key}.tier].subject_ref:ORD)
BM013 D01=one(derived.observed_projection:BS034); D02=one(derived.acquire_time_observation:BS015); D03=one(derived.acquisition:BS064); R01=one(RE04.campaign_ref:BS022); R02=one(RE01.plan_ref:BS023); R03=one(RE01.acceptance_ref:BS024); R04=one(RE04.run_requirement.acquisition_procedure:BS066); R05=seq(RE04.run_requirement.oracle_requirements[i],expected_projection:BS034); R06=seq(RE04.run_requirement.oracle_requirements[i],oracle_contract:BS033); R07=one(RE04.run_requirement.tool:BS067); R08=one(RE04.run_requirement.limits:BS068); R09=one(RS06.policy_ref:BS048); R10=one(RE19.support_profile_ref:BS009); R11=one(RS04.clock_envelope_ref:BS014)
BM014 S01=opt[caller-envelope](input.clock_envelope:BS014); D01=one(derived.time_observation:BS015); R01=opt[current-envelope-selected](RS04.clock_envelope_ref:BS014)
BM015 S01=one(input.campaign:BS022); S02=one(input.start_time_observation:BS015); S03=seq(input.records[i],start_time_observation:BS015); S04=opt[input.records[i].deployment-acquisition-present](input.records[i].deployment_evidence_acquisition:BS064); S05=seq(input.records[i],expected_projection:BS034); S06=seq(input.records[i],observed_projection:BS034); S07=seq(input.records[i],oracle_contract:BS033); S08=opt[input.failure.deployment-acquisition-present](input.failure.deployment_evidence_acquisition:BS064); S09=opt[input.failure-present](input.failure.expected_projection:BS034); S10=opt[input.failure.observed-projection-present](input.failure.observed_projection:BS034); S11=opt[input.failure-present](input.failure.oracle_contract:BS033); S12=seq(input.retained_artifacts[i],item:BS049); D01=one(derived.completion_time_observation:BS015); D02=seq(derived.records[i],item:BS035); D03=opt[failure-present](derived.failure:BS036); D04=one(derived.run_result:BS037); D05=set(derived.affected_tier_results{digest}:BS039); R01=one(RE01.plan_ref:BS023); R02=one(RE01.acceptance_ref:BS024); R03=keyed(affected_RE16{key},RE15[affected_RE16{key}.tier].subject_ref:ORD); R04=set(affected_RE16{key}.current_result_ref:BS039); R05=seq(affected_RE16{key}.prerequisite_result_refs[i],item:BS039); R06=one(RS04.clock_envelope_ref:BS014)
BM016 S01=one(input.finding:BS038); R01=one(RE07.evidence_record_ref:BS035); R02=opt[record-has-acquisition](RE06.acquisition_ref:BS064); R03=one(RE07.expected_projection_ref:BS034); R04=one(RE07.observed_projection_ref:BS034); R05=one(RE07.oracle_contract_ref:BS033)
BM017 S01=one(input.replacement_subject:ORD); D01=set(derived.affected_tier_results{digest}:BS039); R01=opt[current-subject-present](RE15[input.tier].subject_ref:ORD); R02=set(selected_campaigns{key}.campaign_ref:BS022); R03=set(affected_RE16{key}.current_result_ref:BS039); R04=seq(affected_RE16{key}.prerequisite_result_refs[i],item:BS039); R05=keyed(affected_RE16{key},RE15[post-transition,affected_RE16{key}.tier].subject_ref:ORD)
BM018 S01=one(input.authorization_receipt:BS045); S02=branch[INVALIDATION](input.subject.campaign:BS022); S03=branch[INVALIDATION](input.subject.evidence:BS035); S04=branch[INVALIDATION](input.subject.invalidity_evidence:BS038); S05=branch[INVALIDATION](input.subject.subject_revision:ORD); S06=branch[SUPERSESSION](input.subject.campaign:BS022); S07=branch[SUPERSESSION](input.subject.replacement_campaign:BS022); S08=branch[SUPERSESSION](input.subject.prior_result:BS039); S09=branch[SUPERSESSION](input.subject.subject_revision:ORD); D01=one(derived.apply_time_observation:BS015); D02=branch[INVALIDATION](derived.disposition:BS046); D03=branch[SUPERSESSION](derived.disposition:BS047); D04=set(derived.affected_tier_results{digest}:BS039); R01=branch[INVALIDATION](RE10.finding_ref:BS038); R02=set(affected_campaigns{key}.campaign_ref:BS022); R03=set(affected_RE16{key}.current_result_ref:BS039); R04=seq(affected_RE16{key}.prerequisite_result_refs[i],item:BS039); R05=keyed(affected_RE16{key},RE15[affected_RE16{key}.tier].subject_ref:ORD); R06=one(RS04.clock_envelope_ref:BS014)
BM019 R01=one(RS07.tier_result_ref:BS039)
BM020 D01=one(derived.qualification_receipt_time_observation:BS015); R01=one(RE03.plan_ref:BS016); R02=one(RE03.acceptance_ref:BS018); R03=one(RE18.support_profile_ref:BS009); R04=seq(RE17.class_results[i=EV-CLK,EV-PHY,EV-CAP],item:BS019); R05=seq(RE18.tier_result_refs[i],item:BS039); R06=one(RS04.clock_envelope_ref:BS014)
BM021 D01=one(derived.deployment_attestation_time_observation:BS015); R01=one(RE18.receipt_ref:BS020); R02=one(RE19.policy_ref:BS048); R03=one(RE04.campaign_ref:BS022); R04=seq(RE06.acquisitions[i],item:BS064); R05=seq(RE17.class_results[i],item:BS019); R06=seq(RE16.tier_results[i],item:BS039); R07=one(RE21.clock_envelope_ref:BS014); R08=one(RE19.support_profile_ref:BS009); R09=one(live_binding.controller_host_ref:BS010); R10=one(live_binding.postgresql_host_ref:BS011); R11=one(live_binding.postgresql_endpoint_ref:BS012); R12=one(live_binding.deployment_topology_ref:BS013); R13=one(live_binding.live_projection_ref:BS060)
BM022 D01=one(derived.work_reservation_time_observation:BS015); R01=one(RA03.plan_ref:BS088); R02=one(RA01.grant_ref:BS089); R03=one(RW01.request_ref:BS094); R04=one(RE21.clock_envelope_ref:BS014)
BM023 S01=one(input.plan:BS016); S02=one(input.acceptance:BS018); S03=seq(input.run_result_refs[i],item:BS037); D01=one(derived.class_result:BS019); R01=one(RE03.plan_ref:BS016); R02=one(RE03.acceptance_ref:BS018); R03=seq(selected_RE09[i],run_result_ref:BS037); R04=seq(selected_RE07[i],evidence_record_ref:BS035); R05=seq(selected_RE08[i],failure_ref:BS036)
BM024 S01=one(input.plan:BS016); S02=one(input.acceptance:BS018); S03=one(input.support_profile:BS009); S04=seq(input.class_results[i=EV-CLK,EV-PHY,EV-CAP],item:BS019); S05=seq(input.design_tier_results[i],item:BS039); S06=seq(input.implementation_tier_results[i],item:BS039); S07=seq(input.release_tier_results[i],item:BS039); D01=one(derived.receipt:BS020); R01=one(RE03.plan_ref:BS016); R02=one(RE03.acceptance_ref:BS018); R03=seq(RE17.class_results[i=EV-CLK,EV-PHY,EV-CAP],item:BS019); R04=seq(current_tier_partitions[i],item:BS039)
BM025 R01=one(RB06.private_package.deciding_run_result_ref:BS037); R02=seq(RB06.private_package.artifacts[i],artifact_ref:BS049); R03=one(RB06.private_package.limits_ref:BS068); R04=one(RB06.private_package.real_artifact_binding_ref:BS079); R05=one(RB06.private_package.subject_revision_ref:ORD)
BM026 R01=one(RB06.private_package.deciding_run_result_ref:BS037); R02=seq(RB06.private_package.artifacts[i],artifact_ref:BS049); R03=one(RB06.private_package.limits_ref:BS068); R04=one(RB06.private_package.real_artifact_binding_ref:BS079); R05=one(RB06.private_package.subject_revision_ref:ORD); R06=one(RB08.authorization_subject_ref:ORD)
BM027 S01=one(input.public_projection:BS081); S02=one(input.review_receipt:BS082); R01=one(RB06.private_package.deciding_run_result_ref:BS037); R02=seq(RB06.private_package.artifacts[i],artifact_ref:BS049); R03=one(RB06.private_package.limits_ref:BS068); R04=one(RB06.private_package.real_artifact_binding_ref:BS079); R05=one(RB06.private_package.subject_revision_ref:ORD); R06=one(RB08.authorization_subject_ref:ORD); R07=set(current_evidence_bindings{key}.body_ref:ORD); R08=set(current_disclosure_policies{key}.body_ref:ORD)
BM028 R01=one(RB10.public_projection_ref:BS081); R02=one(RB11.review_receipt_ref:BS082); R03=one(RB06.private_package.deciding_run_result_ref:BS037); R04=seq(RB06.private_package.artifacts[i],artifact_ref:BS049); R05=one(RB06.private_package.limits_ref:BS068); R06=one(RB06.private_package.real_artifact_binding_ref:BS079); R07=one(RB06.private_package.subject_revision_ref:ORD); R08=set(current_evidence_bindings{key}.body_ref:ORD); R09=set(current_disclosure_policies{key}.body_ref:ORD)
BM029 S01=opt[replacement-present](input.replacement_policy:BS048); R01=opt[expected-present](input.expected_policy:BS048); R02=opt[current-present](RS06.policy_ref:BS048)
BM030 S01=one(input.clock_envelope:BS014); R01=one(RE19.support_profile_ref:BS009); R02=one(live_binding.controller_host_ref:BS010); R03=one(live_binding.postgresql_host_ref:BS011); R04=one(live_binding.postgresql_endpoint_ref:BS012); R05=one(live_binding.deployment_topology_ref:BS013); R06=one(live_binding.live_projection_ref:BS060); R07=one(RB01[input.target_surface.target_database_identity].body_ref:BS050)
BM031 S01=one(input.candidate_projection:BS034); S02=one(input.qualification_receipt:BS020); S03=branch[COMPATIBILITY](input.final_manifest:BC006); S04=branch[COMPATIBILITY](input.manifest_approval:BC008); D01=branch[SUCCESS](derived.role_grant_set:BS061); D02=branch[SUCCESS](derived.writer_inventory:BS063); D03=branch[SUCCESS](derived.deployment_attestation:BS021); D04=branch[FAILURE](derived.failed_deployment_result:BS044); R01=one(RE19.support_profile_ref:BS009); R02=one(RS04.clock_envelope_ref:BS014); R03=seq(RE17.class_results[i],item:BS019); R04=one(RE04.campaign_ref:BS022); R05=seq(RE16.tier_results[i],item:BS039); R06=one(RS06.policy_ref:BS048); R07=one(live_binding.live_projection_ref:BS060); R08=seq(RE06.acquisitions[i],item:BS064); R09=opt[current-handoff-present](RS08.handoff_ref:BC020|BC021); R10=one(RB01[input.target_surface.target_database_identity].body_ref:BS050)
BM032 R01=one(RE24.attestation_ref:BS021); R02=one(RS06.policy_ref:BS048); R03=one(RE18.receipt_ref:BS020); R04=seq(RE16.tier_results[i],item:BS039)
BM033 R01=one(RE24.attestation_ref:BS021); R02=one(RS08.handoff_ref:BC020|BC021); R03=one(RC28_or_RC29.graph.final_manifest_ref:BC006)
BM034 R01=one(RE24.attestation_ref:BS021); R02=one(conclusive_noncommit_or_resolution_ref:BS102|BS100); R03=one(RW04.transaction_identity_ref:BS103)
BM035 R01=one(RW01.request_ref:BS094); R02=opt[reserved-or-later](RW03.reservation_ref:BS097); R03=opt[started-or-later](RW05.start_ref:BS098); R04=opt[started-or-later](RW04.transaction_identity_ref:BS103); R05=opt[committed](RW06.committed_result_ref:BS099); R06=opt[committed](RW06.typed_result_ref:WORK_RESULT)
BM036 S01=one(input.request:BS094); D01=branch[REFUSAL](derived.refusal:BS096); D02=branch[RESERVATION](derived.reservation:BS097); R01=one(RA03.plan_ref:BS088); R02=one(RA01.grant_ref:BS089); R03=opt[request-existing](RW01.request_ref:BS094); R04=opt[reservation-existing](RW03.reservation_ref:BS097); R05=one(RE21.clock_envelope_ref:BS014)
BM037 D01=one(derived.transaction_identity:BS103); D02=one(derived.work_start:BS098); R01=one(RW01.request_ref:BS094); R02=one(RW03.reservation_ref:BS097); R03=one(RA03.plan_ref:BS088); R04=one(RE21.clock_envelope_ref:BS014)
BM038 D01=one(derived.committed_result_mapping:BS099); R01=one(RW01.request_ref:BS094); R02=one(RW03.reservation_ref:BS097); R03=one(RW04.transaction_identity_ref:BS103); R04=one(RW05.start_ref:BS098); R05=one(caller_staged.typed_result_ref:WORK_RESULT)
BM039 D01=one(derived.transaction_resolution_outcome:BS100); R01=seq(original_and_resolver_chains[j=original,resolver],request_ref:BS094); R02=seq(original_and_resolver_chains[j],reservation_ref:BS097); R03=seq(original_and_resolver_chains[j],start_ref:BS098); R04=seq(original_and_resolver_chains[j],transaction_identity_ref:BS103); R05=one(original.committed_result_ref:BS099); R06=one(original.typed_result_ref:WORK_RESULT)
BM040 D01=branch[COMMITTED](derived.ambiguity_query_outcome:BS101); D02=branch[INCONCLUSIVE](derived.recovery_ambiguity_observation:BS123); R01=seq(original_and_query_chains[j=original,query],request_ref:BS094); R02=seq(original_and_query_chains[j],reservation_ref:BS097); R03=seq(original_and_query_chains[j],start_ref:BS098); R04=seq(original_and_query_chains[j],transaction_identity_ref:BS103); R05=opt[original-committed](original.committed_result_ref:BS099); R06=opt[original-committed](original.typed_result_ref:WORK_RESULT)
BM041 S01=one(input.reconciliation_subject:BS104); S02=one(input.failure_evidence:BS087); D01=one(derived.conclusive_noncommit_result:BS102); D02=one(derived.recovery_advancement_observation:BS125); R01=seq(original_and_resolver_chains[j=original,resolver],request_ref:BS094); R02=seq(original_and_resolver_chains[j],reservation_ref:BS097); R03=seq(original_and_resolver_chains[j],start_ref:BS098); R04=seq(original_and_resolver_chains[j],transaction_identity_ref:BS103)
BM042 R01=one(selected.RW01.request_ref:BS094); R02=opt[selected-refusal](selected.RW02.refusal_ref:BS096); R03=opt[selected-reservation](selected.RW03.reservation_ref:BS097); R04=opt[selected-started](selected.RW04.transaction_identity_ref:BS103); R05=opt[selected-started](selected.RW05.start_ref:BS098); R06=opt[selected-committed](selected.RW06.committed_result_ref:BS099); R07=opt[selected-recovery-refusal](selected.RW07.ref:BS122); R08=opt[selected-recovery-ambiguity](selected.RW08.ref:BS123); R09=opt[selected-recovery-fence](selected.RW09.ref:BS124); R10=opt[selected-recovery-advancement](selected.RW10.ref:BS125); R11=opt[selected-recovery-unproven](selected.RW11.ref:BS126); R12=opt[selected-resolution](selected.RW12.ref:BS100); R13=opt[selected-query](selected.RW13.ref:BS101); R14=opt[selected-noncommit](selected.RW14.ref:BS102); R15=opt[selected-subject](selected.RW15.ref:BS104); R16=opt[selected-committed](selected.RW06.typed_result_ref:WORK_RESULT)
BM043 D01=one(derived.pre_stage_observation:BS108); D02=branch[CURRENT](derived.journal:BS001); R01=one(RA03.plan_ref:BS088); R02=one(RA01.grant_ref:BS089); R03=one(RA04.approval_ref:BS105); R04=one(RA05.authorization_ref:BS106); R05=one(RE24.attestation_ref:BS021); R06=one(RB03.rollback_preimage_binding_ref:BS121); R07=one(RW01.request_ref:BS094); R08=one(RW03.reservation_ref:BS097); R09=one(RW05.start_ref:BS098); R10=one(RW04.transaction_identity_ref:BS103)
BM044 D01=one(derived.pre_stage_observation:BS108); D02=branch[CURRENT](derived.publication_proof:BS002); R01=one(RP02.journal_ref:BS001); R02=one(RA03.plan_ref:BS088); R03=one(RA01.grant_ref:BS089); R04=one(RA04.approval_ref:BS105); R05=one(RA05.authorization_ref:BS106); R06=one(RE24.attestation_ref:BS021); R07=one(RW01.request_ref:BS094); R08=one(RW03.reservation_ref:BS097); R09=one(RW05.start_ref:BS098); R10=one(RW04.transaction_identity_ref:BS103)
BM045 D01=branch[VALID](derived.deadline_receipt:BS003); D02=branch[LATE](derived.deadline_receipt:BS003); R01=one(RP02.journal_ref:BS001); R02=one(RP04.proof_ref:BS002); R03=one(RS04.clock_envelope_ref:BS014); R04=one(RA03.plan_ref:BS088); R05=one(RE24.attestation_ref:BS021); R06=one(RW03.reservation_ref:BS097); R07=one(RW05.start_ref:BS098); R08=one(RW04.transaction_identity_ref:BS103)
BM046 D01=one(derived.before_image:BS117); D02=one(derived.after_image:BS117); D03=one(derived.mutation_receipt:BS004); R01=one(RP02.journal_ref:BS001); R02=one(RP04.proof_ref:BS002); R03=one(RP05.deadline_receipt_ref:BS003); R04=one(RE24.attestation_ref:BS021); R05=one(RW01.request_ref:BS094); R06=one(RW03.reservation_ref:BS097); R07=one(RW05.start_ref:BS098); R08=one(RW04.transaction_identity_ref:BS103); R09=branch[APPLY](RA03.payload_ref:BS118); R10=branch[ROLLBACK](RB03.restore_payload_ref:BS119); R11=branch[ROLLBACK](RB03.conversion_ref:BS120); R12=branch[ROLLBACK](RB03.rollback_preimage_binding_ref:BS121)
BM047 R01=one(RC28_or_RC29.graph.final_manifest_ref:BC006); R02=opt[legacy-restore-selected](RC04.restore_content_ref:BC005); R03=one(RE24.attestation_ref:BS021); R04=one(RS08.handoff_ref:BC020|BC021)
BM048 D01=branch[UNABLE](derived.verification_result:BS008); D02=branch[MATCH](derived.verification_result:BS005); D03=branch[MISMATCH](derived.verification_result:BS006); D04=branch[TERMINAL_FAILURE](derived.verification_result:BS007); R01=one(RP06.mutation_receipt_ref:BS004); R02=one(RP06.before_image_ref:BS117); R03=one(RP06.after_image_ref:BS117); R04=one(RE24.attestation_ref:BS021); R05=one(RW01.request_ref:BS094); R06=one(RW03.reservation_ref:BS097); R07=one(RW05.start_ref:BS098); R08=one(RW04.transaction_identity_ref:BS103)
BM049 S01=one(input.reader_binding:BS043); S02=seq(input.reader_outcomes[i],item:BC003|BC004); D01=one(derived.closure_case_binding:BC009); R01=one(RE24.attestation_ref:BS021); R02=one(RS06.policy_ref:BS048); R03=one(RS04.clock_envelope_ref:BS014); R04=set(RC01.discovery_roots{identity}.derivation_contract_ref:ORD); R05=set(RC02.inventory_observations{identity}.descriptor_ref:ORD); R06=set(RC04.reader_outcomes{identity}.execution_binding_ref:BS043)
BM050 D01=one(derived.reservation_sample:BC011); R01=one(RC05.case_binding_ref:BC009); R02=one(RS04.clock_envelope_ref:BS014); R03=one(RC05.closure_policy_ref:BS069); R04=one(RS06.policy_ref:BS048)
BM051 D01=one(derived.claim_sample:BC011); R01=one(RC05.case_binding_ref:BC009); R02=one(RC07.attempt_binding_ref:BC009); R03=one(RS04.clock_envelope_ref:BS014)
BM052 S01=one(input.invalidation:BC012); D01=one(derived.takeover_sample:BC011); R01=one(RC05.case_binding_ref:BC009); R02=one(RC07.attempt_binding_ref:BC009); R03=one(RS04.clock_envelope_ref:BS014)
BM053 D01=one(derived.finalization_sample:BC011); D02=branch[MATCH|MISMATCH](derived.comparison:BC013); D03=branch[UNABLE](derived.failure:BC014); D04=one(derived.observation:BC010); R01=one(RC05.case_binding_ref:BC009); R02=one(RS04.clock_envelope_ref:BS014)
BM054 D01=one(derived.expiry_sample:BC011); D02=one(derived.deadline_failure:BC014); D03=one(derived.observation:BC010); R01=one(RC05.case_binding_ref:BC009); R02=one(RS04.clock_envelope_ref:BS014)
BM055 S01=one(input.pre_fence_graph.manifest_basis:BC001); S02=one(input.pre_fence_graph.frozen_reader_registry:BC002); S03=set(input.pre_fence_graph.reader_outcomes{digest}:BC003|BC004); S04=one(input.pre_fence_graph.final_manifest:BC006); S05=set(input.pre_fence_graph.artifact_exclusions{digest}:BC007); S06=set(input.pre_fence_graph.approvals{digest}:BC008); S07=set(input.pre_fence_graph.referenced_evidence{digest}:ORD); D01=one(derived.realized_admission:BC015); D02=one(derived.realized_acl:BC016); R01=one(RE24.attestation_ref:BS021); R02=one(RS06.policy_ref:BS048); R03=one(RE22.role_grant_set_ref:BS061); R04=one(RE23.writer_inventory_ref:BS063); R05=one(RS04.clock_envelope_ref:BS014); R06=one(RS17.qualification_receipt_ref:BS020); R07=seq(RE16.current_pass_partition[i],item:BS039)
BM056 D01=one(derived.zero_live_writer_evidence:BC017); R01=one(RE23.writer_inventory_ref:BS063); R02=one(RE24.attestation_ref:BS021); R03=one(RS06.policy_ref:BS048); R04=one(RS04.clock_envelope_ref:BS014); R05=one(RS17.qualification_receipt_ref:BS020); R06=seq(RE16.current_pass_partition[i],item:BS039); R07=one(RC23.realized_admission_ref:BC015); R08=one(RC24.realized_acl_ref:BC016)
BM057 S01=one(input.disable_attestation:ORD); D01=one(derived.service_disable_evidence:BC018); R01=one(RE23.writer_inventory_ref:BS063); R02=one(RE24.attestation_ref:BS021); R03=one(RS06.policy_ref:BS048); R04=one(RS04.clock_envelope_ref:BS014); R05=one(RS17.qualification_receipt_ref:BS020); R06=seq(RE16.current_pass_partition[i],item:BS039); R07=one(RC23.realized_admission_ref:BC015); R08=one(RC24.realized_acl_ref:BC016)
BM058 D01=one(derived.persistent_fence_evidence:BC019); D02=one(derived.origin_binding:BC020); R01=one(RC23.realized_admission_ref:BC015); R02=one(RC24.realized_acl_ref:BC016); R03=seq(RC25.zero_writer_evidence[i],item:BC017); R04=set(RC26.service_disable_evidence{service_identity},item:BC018); R05=one(RC21.pending_envelope.final_manifest_ref:BC006); R06=one(RC21.pending_envelope.deployment_attestation_ref:BS021); R07=one(RC21.pending_envelope.manifest_basis_ref:BC001); R08=one(RC21.pending_envelope.frozen_reader_registry_ref:BC002); R09=set(RC21.pending_envelope.reader_outcome_refs{digest},item:BC003|BC004); R10=set(RC21.pending_envelope.artifact_exclusion_refs{digest},item:BC007); R11=set(RC21.pending_envelope.approval_refs{digest},item:BC008); R12=set(RC21.pending_envelope.referenced_evidence_refs{digest},item:ORD)
BM059 S01=one(input.adoption_graph.manifest_basis:BC001); S02=one(input.adoption_graph.frozen_reader_registry:BC002); S03=set(input.adoption_graph.reader_outcomes{digest}:BC003|BC004); S04=one(input.adoption_graph.final_manifest:BC006); S05=set(input.adoption_graph.artifact_exclusions{digest}:BC007); S06=set(input.adoption_graph.approvals{digest}:BC008); S07=set(input.adoption_graph.referenced_evidence{digest}:ORD); D01=one(derived.adoption:BC021); R01=one(RC27.persistent_fence_ref:BC019); R02=one(RS08.current_handoff_ref:BC020|BC021); R03=one(RE24.attestation_ref:BS021); R04=one(RS04.clock_envelope_ref:BS014); R05=seq(RE16.current_pass_partition[i],item:BS039); R06=one(RS06.policy_ref:BS048); R07=one(RS17.qualification_receipt_ref:BS020)
BM060 R01=one(ActivationManifestGraphProjection.current_handoff_ref:BC020|BC021); R02=seq(ActivationManifestGraphProjection.handoff_chain[i],item:BC020|BC021); R03=one(ActivationManifestGraphProjection.persistent_fence_ref:BC019); R04=one(ActivationManifestGraphProjection.realized_admission_ref:BC015); R05=one(ActivationManifestGraphProjection.realized_acl_ref:BC016); R06=seq(ActivationManifestGraphProjection.zero_writer_refs[i],item:BC017); R07=set(ActivationManifestGraphProjection.service_disable_refs{service_identity},item:BC018); R08=one(ActivationManifestGraphProjection.binding.manifest_basis_ref:BC001); R09=one(ActivationManifestGraphProjection.binding.frozen_reader_registry_ref:BC002); R10=set(ActivationManifestGraphProjection.binding.reader_outcome_refs{digest},item:BC003|BC004); R11=one(ActivationManifestGraphProjection.binding.final_manifest_ref:BC006); R12=set(ActivationManifestGraphProjection.binding.artifact_exclusion_refs{digest},item:BC007); R13=set(ActivationManifestGraphProjection.binding.approval_refs{digest},item:BC008); R14=set(ActivationManifestGraphProjection.binding.referenced_evidence_refs{digest},item:ORD); R15=one(RE24.attestation_ref:BS021); R16=one(RS04.clock_envelope_ref:BS014); R17=seq(RE16.current_pass_partition[i],item:BS039)
BM061 R01=one(RE24.attestation_ref:BS021); R02=one(conclusive_noncommit_or_resolution_ref:BS102|BS100); R03=one(RW04.transaction_identity_ref:BS103)
BM062 R01=one(ActivationManifestGraphProjection.current_handoff_ref:BC020|BC021); R02=seq(ActivationManifestGraphProjection.handoff_chain[i],item:BC020|BC021); R03=one(ActivationManifestGraphProjection.persistent_fence_ref:BC019); R04=one(ActivationManifestGraphProjection.realized_admission_ref:BC015); R05=one(ActivationManifestGraphProjection.realized_acl_ref:BC016); R06=seq(ActivationManifestGraphProjection.zero_writer_refs[i],item:BC017); R07=set(ActivationManifestGraphProjection.service_disable_refs{service_identity},item:BC018); R08=one(ActivationManifestGraphProjection.binding.manifest_basis_ref:BC001); R09=one(ActivationManifestGraphProjection.binding.frozen_reader_registry_ref:BC002); R10=set(ActivationManifestGraphProjection.binding.reader_outcome_refs{digest},item:BC003|BC004); R11=one(ActivationManifestGraphProjection.binding.final_manifest_ref:BC006); R12=set(ActivationManifestGraphProjection.binding.artifact_exclusion_refs{digest},item:BC007); R13=set(ActivationManifestGraphProjection.binding.approval_refs{digest},item:BC008); R14=set(ActivationManifestGraphProjection.binding.referenced_evidence_refs{digest},item:ORD)
```

#### Direct-root requiring-edge records

`W(R.f)` is a closed outcome-qualified template. On the accepted `APPLIED` branch it emits `RELATION(R,f,WRITE,APPLIED)`; on an admitted byte-identical replay it emits `RELATION(R,f,VERIFY_PRESENT,EXACT_REPLAY)`. Those are separate BREQ identities and never coexist in one site use. A callable without an exact-replay branch emits only the first. `R(R.f)` always emits `RELATION(R,f,READ,NO_WRITE)`. `E(SC.p)` means `PROTECTED_EFFECT(SC,p)`.

No BREQ encoder chooses INSERT, UPDATE, or CLEAR. For an anchor, `WRITE/APPLIED` covers both first transition from explicit ABSENT and replacement from PRESENT; its exact expected-current predicate remains in the SC/function contract. A clear with no replacement body emits no S/D write-body member: the displaced body is a database `READ/NO_WRITE` root, while the SC/BM delta specifies the anchor clear and retirement fact. A refusal or conflict durably emits no `W` record; supplied bytes may be source-validated and database roots may be read, but FB01 and every outer mutation roll back. For one member mapped to several relation fields, expansion emits one independently identifiable BREQ per field with the same outcome.

Each expanded S/D-root destination is also one present SRP crosswalk member under its BM site’s SPA. Its source side is the accepted operation, MRR root path, closed branch, root cardinality/key or ordinal, exact body domain, and accepted output-fact field; its proposal side is the MRR carrier and outcome-qualified BREQ destination. The SRP identity excludes the BM/MRR/BREQ names. A direct body reference stored in two fields therefore yields two SRPs even though both use one root. Deleting the whole line removes a required SRP and fails the independent SPA source comparison.

Each expansion below creates one stable edge identity `BREQ:<MRR identity>:<complete outcome-qualified edge encoding>`. A set- or sequence-valued MRR instance substitutes its exact member key or ordinal into every displayed `[*]`. Branch-qualified edges exist only in that branch. Each emitted S/D MRR must match exactly one left-hand token below and carry exactly the displayed edge set:

```text
BM001 S01=>W(RB03.rollback_preimage_binding_ref)
BM002 S01=>W(RA01.body_ref)
BM003 S01=>W(RA02.body_ref)
BM004 S01=>W(RA03.body_ref)
BM005 S01=>W(RA04.body_ref)
BM006 S01=>W(RA05.body_ref)
BM007 S01=>W(RA06.body_ref)
BM009 S01=>W(RE01.plan_ref); S02=>W(RE01.acceptance_ref)
BM010 S01=>W(RE02.plan_ref); S02=>W(RE02.acceptance_ref)
BM011 S01=>W(RE03.plan_ref); S02=>W(RE03.acceptance_ref)
BM012 S01=>W(RE04.body_ref); D01=>W(RE16[*].body_ref)+W(RS07[*].value)
BM013 D01=>W(RE06.observed_projection_ref); D02=>W(RE05.body_ref)+W(RE06.time_observation_ref); D03=>W(RE06.body_ref)
BM014 S01=>W(RE05.clock_envelope_ref); D01=>W(RE05.body_ref)
BM015 S01=>W(RE09.campaign_ref); S02=>W(RE09.start_observation_ref); S03=>W(RE07[*].start_observation_ref); S04=>W(RE07[*].acquisition_ref); S05=>W(RE07[*].expected_projection_ref); S06=>W(RE07[*].observed_projection_ref); S07=>W(RE07[*].oracle_contract_ref); S08=>W(RE08.acquisition_ref); S09=>W(RE08.expected_projection_ref); S10=>W(RE08.observed_projection_ref); S11=>W(RE08.oracle_contract_ref); S12=>W(RE07[*].retained_artifact_refs[*]); D01=>W(RE05.body_ref)+W(RE09.completion_observation_ref); D02=>W(RE07[*].body_ref); D03=>W(RE08.body_ref); D04=>W(RE09.body_ref); D05=>W(RE16[*].body_ref)+W(RS07[*].value)
BM016 S01=>W(RE10.body_ref)
BM017 S01=>W(RE14.subject_ref)+W(RE15.subject_ref); D01=>W(RE16[*].body_ref)+W(RS07[*].value)
BM018 S01=>W(RE12.body_ref); S02=>W(RE12.subject_bytes)+W(RE11.campaign_ref); S03=>W(RE12.subject_bytes)+W(RE11.evidence_ref); S04=>W(RE12.subject_bytes)+W(RE11.invalidity_finding_ref); S05=>W(RE12.subject_bytes)+W(RE11.subject_revision); S06=>W(RE12.subject_bytes)+W(RE13.campaign_ref); S07=>W(RE12.subject_bytes)+W(RE13.replacement_campaign_ref); S08=>W(RE12.subject_bytes)+W(RE13.prior_result_ref); S09=>W(RE12.subject_bytes)+W(RE13.subject_revision); D01=>W(RE05.body_ref)+branch[INVALIDATION]W(RE11.application_observation_ref)+branch[SUPERSESSION]W(RE13.application_observation_ref); D02=>W(RE11.body_ref); D03=>W(RE13.body_ref); D04=>W(RE16[*].body_ref)+W(RS07[*].value)
BM020 D01=>W(RE05.body_ref)+W(RE18.issue_observation_ref)
BM021 D01=>W(RE05.body_ref)+W(RE24.issue_observation_ref)
BM022 D01=>W(RE05.body_ref)+W(RW03.reservation_observation_ref)
BM023 S01=>W(RE17.plan_ref); S02=>W(RE17.plan_acceptance_ref); S03=>W(RE17.run_result_refs[*]); D01=>W(RE17.body_ref)
BM024 S01=>W(RE18.plan_ref); S02=>W(RE18.plan_acceptance_ref); S03=>W(RE18.support_profile_ref); S04=>W(RE18.class_result_refs[*]); S05=>W(RE18.design_tier_result_refs[*]); S06=>W(RE18.implementation_tier_result_refs[*]); S07=>W(RE18.release_tier_result_refs[*]); D01=>W(RE18.body_ref)
BM027 S01=>W(RB10.body_ref); S02=>W(RB11.body_ref)
BM029 S01=>W(RE19.body_ref)+W(RS06.value)
BM030 S01=>W(RE21.body_ref)+W(RS04.value)
BM031 S01=>E(SC030.candidate_projection_validation)+branch[FAILURE]W(RE25.candidate_projection_ref); S02=>branch[SUCCESS]W(RE24.qualification_receipt_ref)+branch[SUCCESS]W(RS17.value)+branch[FAILURE]W(RE25.qualification_receipt_ref); S03=>branch[COMPATIBILITY]W(RC29.binding.final_manifest_ref); S04=>branch[COMPATIBILITY]W(RC29.binding.manifest_approval_ref); D01=>W(RE22.body_ref)+W(RE24.role_grant_set_ref)+W(RS19.value); D02=>W(RE23.body_ref)+W(RE24.writer_inventory_ref)+W(RS21.value); D03=>W(RE24.body_ref)+W(RS05.value); D04=>W(RE25.body_ref)
BM036 S01=>W(RW01.body_ref); D01=>W(RW02.body_ref); D02=>W(RW03.body_ref)+W(RS13.value)
BM037 D01=>W(RW04.body_ref); D02=>W(RW05.body_ref)+W(RS14.value)
BM038 D01=>W(RW06.body_ref)+W(RS15.value)
BM039 D01=>W(RW12.body_ref)+W(RW06[resolver].typed_result_ref)
BM040 D01=>branch[COMMITTED]W(RW13.body_ref)+branch[COMMITTED]W(RW06[query].typed_result_ref); D02=>branch[INCONCLUSIVE]W(RW08.body_ref)+branch[INCONCLUSIVE]W(RW06[query].typed_result_ref)
BM041 S01=>W(RW15.body_ref); S02=>W(RW14.failure_evidence_ref); D01=>W(RW14.body_ref)+W(RW06[original].typed_result_ref); D02=>W(RW10.body_ref)+W(RW06[resolver].typed_result_ref)
BM043 D01=>W(RP03.body_ref); D02=>branch[CURRENT]W(RP02.body_ref)+branch[CURRENT]W(RW06.typed_result_ref)
BM044 D01=>W(RP03.body_ref); D02=>branch[CURRENT]W(RP04.body_ref)+branch[CURRENT]W(RW06.typed_result_ref)
BM045 D01=>branch[VALID]W(RP05.body_ref)+branch[VALID]W(RW06.typed_result_ref); D02=>branch[LATE]W(RP05.body_ref)+branch[LATE]W(RW06.typed_result_ref)
BM046 D01=>W(RP06.before_image_ref); D02=>W(RP06.after_image_ref); D03=>W(RP06.body_ref)+W(RW06.typed_result_ref)
BM048 D01=>branch[UNABLE]W(RP11.body_ref)+branch[UNABLE]W(RW06.typed_result_ref); D02=>branch[MATCH]W(RP15.body_ref)+branch[MATCH]W(RW06.typed_result_ref); D03=>branch[MISMATCH]W(RP12.body_ref)+branch[MISMATCH]W(RW06.typed_result_ref); D04=>branch[TERMINAL_FAILURE]W(RP13.body_ref)+branch[TERMINAL_FAILURE]W(RW06.typed_result_ref)
BM049 S01=>W(RC05.reader_binding_ref); S02=>W(RC05.reader_outcome_refs[*]); D01=>W(RC05.body_ref)
BM050 D01=>W(RC09.body_ref)
BM051 D01=>W(RC09.body_ref)
BM052 S01=>W(RC10.body_ref); D01=>W(RC09.body_ref)
BM053 D01=>W(RC09.body_ref); D02=>branch[MATCH|MISMATCH]W(RC11.body_ref); D03=>branch[UNABLE]W(RC12.body_ref); D04=>W(RC13.body_ref)
BM054 D01=>W(RC09.body_ref); D02=>W(RC12.body_ref); D03=>W(RC13.body_ref)
BM055 S01=>W(RC21.pending_envelope.manifest_basis_ref); S02=>W(RC21.pending_envelope.frozen_reader_registry_ref); S03=>W(RC21.pending_envelope.reader_outcome_refs[*]); S04=>W(RC21.pending_envelope.final_manifest_ref); S05=>W(RC21.pending_envelope.artifact_exclusion_refs[*]); S06=>W(RC21.pending_envelope.approval_refs[*]); S07=>W(RC21.pending_envelope.referenced_evidence_refs[*]); PFC-RC21-GRAPH=>W(RC21.pending_envelope.manifest_approval_digest,manifest_approval_receipt.*,exclusion_approval_receipts{approval_digest}.approval_digest,exclusion_approval_receipts{approval_digest}.receipt.*); D01=>W(RC23.body_ref); D02=>W(RC24.body_ref)
BM056 D01=>W(RC25.body_ref)
BM057 S01=>W(RC26.disable_attestation_ref); D01=>W(RC26.body_ref)
BM058 D01=>W(RC27.body_ref); D02=>W(RC28.body_ref)+W(RC28.binding)+W(RS08.value)
BM059 S01=>W(RC29.binding.manifest_basis_ref); S02=>W(RC29.binding.frozen_reader_registry_ref); S03=>W(RC29.binding.reader_outcome_refs[*]); S04=>W(RC29.binding.final_manifest_ref); S05=>W(RC29.binding.artifact_exclusion_refs[*]); S06=>W(RC29.binding.approval_refs[*]); S07=>W(RC29.binding.referenced_evidence_refs[*]); D01=>W(RC29.body_ref)+W(RC29.binding)+W(RS08.value)
```

BM029’s policy-CAS expansion is therefore total. With a non-`NONE` replacement and an accepted apply branch, S01 emits `WRITE/APPLIED` for both RE19.body_ref and RS06.value whether the anchor was ABSENT or PRESENT. Its exact replay emits `VERIFY_PRESENT/EXACT_REPLAY` for both fields. With replacement `NONE`, S01 is absent; expected/current policy roots are `READ/NO_WRITE`, and SC028 alone specifies optional RE20 plus RS06 clear. Stale expected state, a retired pair, target mismatch, conflict, or any other refusal emits no S01 BREQ and changes no body, fact, or anchor. The same universal rules determine every other current-relation body edge.

Every R token whose path contains exactly one concrete relation ID receives `R(<that relation>.<remaining exact path>)`. The following table is the exhaustive exception registry; any R path matching neither the direct rule nor one row below is invalid:

```text
affected_RE16/current_tier_partitions/RE16.current_pass_partition
  =>R(RE16.selected member)+R(RS07.corresponding current value)
selected_campaigns/affected_campaigns=>R(RE04.selected campaign)
selected_RE07=>R(RE07.selected record)
selected_RE08=>R(RE08.selected failure)
selected_RE09=>R(RE09.selected run result)
current_evidence_bindings
  =>R(RE15.current subject)+R(RE16.current result)+R(RS07.current value)
current_disclosure_policies
  =>R(RE19.current policy)+R(RS06.current value)
     +E(<record SC>.current_private_or_public_policy_validation)
live_binding.*=>E(<record SC>.protected_live_binding_observation)
original_and_resolver_chains[*].request_ref/original_and_query_chains[*].request_ref
  =>R(RW01.selected request)
original_and_resolver_chains[*].reservation_ref/original_and_query_chains[*].reservation_ref
  =>R(RW03.selected reservation)
original_and_resolver_chains[*].start_ref/original_and_query_chains[*].start_ref
  =>R(RW05.selected start)
original_and_resolver_chains[*].transaction_identity_ref/original_and_query_chains[*].transaction_identity_ref
  =>R(RW04.selected transaction identity)
original.committed_result_ref=>R(RW06.committed_result_ref)
original.typed_result_ref/caller_staged.typed_result_ref
  =>R(the exact WORK_RESULT carrier relation selected below)
conclusive_noncommit_or_resolution_ref=>R(RW14.body_ref)|R(RW12.body_ref)
RC28_or_RC29.graph.*=>R(RC28.graph.*)|R(RC29.graph.*), selected by the RS08 handoff tag
ActivationManifestGraphProjection.current_handoff_ref
  =>R(RS08.value)+R(RC28.body_ref)|R(RC29.body_ref)
ActivationManifestGraphProjection.handoff_chain[*]
  =>R(RC28.body_ref)|R(RC29.body_ref), selected separately for each chain member
ActivationManifestGraphProjection.persistent_fence_ref=>R(RC27.body_ref)
ActivationManifestGraphProjection.realized_admission_ref=>R(RC23.body_ref)
ActivationManifestGraphProjection.realized_acl_ref=>R(RC24.body_ref)
ActivationManifestGraphProjection.zero_writer_refs[*]=>R(RC25[*].body_ref)
ActivationManifestGraphProjection.service_disable_refs[*]=>R(RC26[*].body_ref)
ActivationManifestGraphProjection.binding.*=>R(RC28.binding.*)|R(RC29.binding.*), selected by RS08
```

#### Durable reference destination-projection records

The direct `R(...)` edge proves a database body is read and not written. FB01/FB02 plus BODY_PARENT prove the origin of a supplied or derived nested reference. Neither proves that a distinct output field received that reference. The mappings below are the complete present member-level source roster and destination crosswalk for those copies.

For every printed mapping, its source side emits an SRP using the site’s SPA/SC operation, the accepted source field path printed after `/`, its branch and cardinality from the MRR/MRE/DLI expansion, its exact reference domain, and the accepted destination semantic printed after `=>`. Its proposal side emits `DRP:<source member identity>:<accepted source path>:<relation.field>:<branch/member key>`. The BM/MRR/MRE/DLI token is only the carrier crosswalk and is not part of the SRP identity. Database and protected-field sources retain independent `READ/NO_WRITE` edges. Body sources retain FB01/FB02 and BODY_PARENT. A decoded bridge names the exact embedded compatibility field and the fixed successor projection. `MATERIALIZE` adds the destination’s outcome-qualified `WRITE/APPLIED` or `VERIFY_PRESENT/EXACT_REPLAY` edge; `EQUALITY_ONLY` compares against a named materializer. Refusals emit no destination edge. Sequence, set, keyed, and first-use mappings emit one identity per accepted ordinal or stable member key. Direct S/D-root BREQ mappings need no duplicate DRP unless a nested member becomes a separate destination.

```text
# AUTH
BM003.R01 / grant-revocation.grant => RA02.grant_ref
BM004.R01 / protected-plan.grant => RA03.grant_ref
BM004.R02 / protected-plan.rollback-preimage-binding => RA03.rollback_preimage_binding_ref
BM005.R01 / BS105.plan => RA04.plan_ref
BM006.R01 / BS106.plan => RA05.plan_ref
BM006.R02 / BS106.approval => RA05.approval_ref
BM007.R01 / BS107.grant => RA06.grant_ref
BM007.R02 / BS107.plan => RA06.plan_ref
BM007.R03[approval-present] / BS107.approval => RA06.approval_ref
BM007.R04[authorization-present] / BS107.authorization_receipt => RA06.authorization_ref

# EVAL, QUAL, PRIVATE, ADMIT
BM012.R01 / BS022.campaign_plan => RE04.campaign_plan_ref
BM012.R02 / BS022.campaign_plan_acceptance => RE04.campaign_plan_acceptance_ref
BM012.R03[current-subject-present] / BS022.subject_revision => RE04.subject_revision_ref
BM012.R04{key} / BS039.predecessor_result => RE16{key}.predecessor_result_ref
BM012.R05{key}[i] / BS039.prerequisite_results[i] => RE16{key}.prerequisite_result_refs[i]
BM012.R06{key} / BS039.subject_revision => RE16{key}.subject_revision_ref
BM013.R01 / BS064.campaign => RE06.campaign_ref
BM013.R04 / BS064.acquisition_procedure => RE06.acquisition_procedure_ref
BM013.R11 / BS015.clock_envelope => RE05.clock_envelope_ref + RE06.clock_envelope_ref
BM013.R11#/boot_identity / qualified acquisition boot => RE05.boot_identity_ref + RE06.boot_identity_ref
BM014.R01[current-envelope-selected] / BS015.clock_envelope => RE05.clock_envelope_ref
BM014.R01[current-envelope-selected]#/boot_identity => RE05.boot_identity_ref
BM015.R03{key} / BS039.subject_revision => RE16{key}.subject_revision_ref
BM015.R04{key} / BS039.predecessor_result => RE16{key}.predecessor_result_ref
BM015.R05{key}[i] / BS039.prerequisite_results[i] => RE16{key}.prerequisite_result_refs[i]
BM015.R06 / BS015.clock_envelope => RE05.clock_envelope_ref
BM015.R06#/boot_identity => RE05.boot_identity_ref
BM016.R01 / BS038.evidence_record => RE10.evidence_ref
BM016.R02[record-has-acquisition] / BS038.deployment_evidence_acquisition => RE10.acquisition_ref
BM016.R03 / BS038.expected_projection => RE10.expected_projection_ref
BM016.R04 / BS038.observed_projection => RE10.observed_projection_ref
BM016.R05 / BS038.oracle_contract => RE10.oracle_contract_ref
BM017.R01[current-subject-present] / subject-assignment.predecessor => RE14.predecessor_subject_ref
BM017.R02{key} / BS039.campaign => RE16{key}.selected_campaign_ref
BM017.R03{key} / BS039.predecessor_result => RE16{key}.predecessor_result_ref
BM017.R04{key}[i] / BS039.prerequisite_results[i] => RE16{key}.prerequisite_result_refs[i]
BM017.R05{key} / BS039.subject_revision => RE16{key}.subject_revision_ref
BM018.R01[INVALIDATION] / BS046.invalidity_evidence => RE11.invalidity_finding_ref
BM018.R02{key} / BS039.campaign => RE16{key}.selected_campaign_ref
BM018.R03{key} / BS039.predecessor_result => RE16{key}.predecessor_result_ref
BM018.R04{key}[i] / BS039.prerequisite_results[i] => RE16{key}.prerequisite_result_refs[i]
BM018.R05{key} / BS039.subject_revision => RE16{key}.subject_revision_ref
BM018.R06 / BS015.clock_envelope => RE05.clock_envelope_ref
BM018.R06#/boot_identity => RE05.boot_identity_ref
BM020.R01 / BS020.plan => RE18.plan_ref
BM020.R02 / BS020.plan_acceptance => RE18.plan_acceptance_ref
BM020.R03 / BS020.support_profile => RE18.support_profile_ref
BM020.R04[i] / BS020.clock_result|physical_durability_result|capability_result => RE18.class_result_refs[i]
BM020.R05[i] / BS020.tier_results[i] => RE18.tier_result_refs[i]
BM020.R06 / BS015.clock_envelope => RE05.clock_envelope_ref
BM020.R06#/boot_identity => RE05.boot_identity_ref
BM021.R01 / BS021.qualification_receipt => RE24.qualification_receipt_ref
BM021.R01#/plan => RE24.qualification_plan_ref
BM021.R01#/plan_acceptance => RE24.qualification_plan_acceptance_ref
BM021.R01#/closure_policy_limits => RE24.closure_policy_limits_ref
BM021.R02 / BS021.deployment_admission_policy => RE24.deployment_admission_policy_ref
BM021.R03 / BS021.deployment_campaign => RE24.deployment_campaign_ref
BM021.R06[i] / BS021.deployment_tier_results[i] => RE24.deployment_tier_result_refs[i]
BM021.R07 / BS015.clock_envelope and BS021.clock_envelope => RE05.clock_envelope_ref + RE24.clock_envelope_ref
BM021.R07#/boot_identity => RE05.boot_identity_ref + RE24.boot_identity_ref
BM021.R08 / BS021.support_profile => RE24.support_profile_ref
BM021.R08#/closure_policy_limits => RE24.closure_policy_limits_ref
BM021.R08#/storage_profile#/identity => RE24.storage_identity_ref
BM021.R09 / BS021.controller_host => RE24.controller_host_ref
BM021.R09#/host_identity => RE24.host_identity_ref
BM021.R10 / BS021.postgresql_host => RE24.postgresql_host_ref
BM021.R10#/host_identity => RE24.host_identity_ref
BM021.R11 / BS021.postgresql_endpoint => RE24.postgresql_endpoint_ref
BM021.R11#/endpoint_identity => RE24.endpoint_identity_ref
BM021.R11#/target_database_identity => RE24.target_database_identity_ref
BM021.R12 / BS021.deployment_topology => RE24.deployment_topology_ref
BM021.R13#/boot_identity => RE24.boot_identity_ref
BM021.R13#/target_database_identity => RE24.target_database_identity_ref
BM022.R01 / BS097.plan => RW03.plan_ref
BM022.R03 / BS097.request => RW03.request_ref
BM022.R04 / BS015.clock_envelope and BS097.clock_envelope => RE05.clock_envelope_ref + RW03.clock_envelope_ref
BM022.R04#/boot_identity => RE05.boot_identity_ref + RW03.boot_identity_ref
BM023.R01 / protected-class-result.plan => RE17.plan_ref
BM023.R02 / protected-class-result.plan-acceptance => RE17.plan_acceptance_ref
BM023.R03[i] / protected-class-result.run-result => RE17.run_result_refs[i]
BM023.R01#/support_profile => RE17.support_profile_ref
BM023.R03[i]#/clock_bindings[j]/clock_epoch[non-NONE,first-use k] => RE17.clock_epoch_refs[k]
BM024.R01 / BS020.plan => RE18.plan_ref
BM024.R02 / BS020.plan_acceptance => RE18.plan_acceptance_ref
BM024.R03[i] / BS020 class-result fields => RE18.class_result_refs[i]
BM024.R04[i] / BS020.tier_results[i] => RE18.tier_result_refs[i]
BM027.R03 / reviewed-projection-and-receipt.limits => RB10.limits_ref + RB11.limits_ref
BM027.R05 / reviewed-projection-and-receipt.subject_revision => RB10.public_subject_identity_ref + RB11.public_subject_identity_ref
BM027.R06 / BS082.reviewer_authorization => RB11.reviewer_authorization_ref
BM029.R02[current-displaced] / retired-policy-pair.policy => RE20.policy_ref
BM030.R01 / clock-envelope.support_profile => RE21.support_profile_ref
BM030.R02 / clock-envelope.controller_host => RE21.controller_host_ref
BM030.R03 / clock-envelope.postgresql_host => RE21.postgresql_host_ref
BM030.R04 / clock-envelope.postgresql_endpoint => RE21.postgresql_endpoint_ref
BM030.R05 / clock-envelope.deployment_topology => RE21.deployment_topology_ref
BM030.R06 / clock-envelope.live_projection => RE21.live_projection_ref
BM030.R07 / clock-envelope target key => RE21.target_database_identity_ref
BM030.S01#/boot_identity => RE21.boot_identity_ref
BM030.S01#/clock_profile => RE21.clock_profile_ref
BM030.S01#/host_identity => RE21.host_identity_ref
BM030.S01#/synchronization_epoch => RE21.synchronization_epoch_ref
BM031.R01[SUCCESS] / BS021.support_profile => RE24.support_profile_ref
BM031.R02[SUCCESS] / BS021.clock_envelope => RE24.clock_envelope_ref
BM031.R04[SUCCESS] / BS021.deployment_campaign => RE24.deployment_campaign_ref
BM031.R05[SUCCESS][i] / BS021.deployment_tier_results[i] => RE24.deployment_tier_result_refs[i]
BM031.R06[SUCCESS] / BS021.deployment_admission_policy => RE24.deployment_admission_policy_ref
ISSUE106-DETAIL-PGSETTINGS / decoded BM031.D03#/postgresql_settings => RE24.postgresql_settings_ref; exact-body mirror only, assignment source=SOURCE_UNASSIGNED, no accepted live-binding equality
BM031.S02[SUCCESS]#/plan => RE24.qualification_plan_ref
BM031.S02[SUCCESS]#/plan_acceptance => RE24.qualification_plan_acceptance_ref
BM031.S02[SUCCESS]#/closure_policy_limits => RE24.closure_policy_limits_ref
BM031.R01[SUCCESS]#/closure_policy_limits => RE24.closure_policy_limits_ref
BM031.R01[SUCCESS]#/controller_host => RE24.controller_host_ref
BM031.R01[SUCCESS]#/postgresql_host => RE24.postgresql_host_ref
BM031.R01[SUCCESS]#/postgresql_endpoint => RE24.postgresql_endpoint_ref
BM031.R01[SUCCESS]#/deployment_topology => RE24.deployment_topology_ref
BM031.R01[SUCCESS]#/controller_host#/host_identity => RE24.host_identity_ref
BM031.R01[SUCCESS]#/postgresql_host#/host_identity => RE24.host_identity_ref
BM031.R01[SUCCESS]#/postgresql_endpoint#/endpoint_identity => RE24.endpoint_identity_ref
BM031.R01[SUCCESS]#/storage_profile#/identity => RE24.storage_identity_ref
BM031.R02[SUCCESS]#/boot_identity => RE24.boot_identity_ref
BM031.R07[SUCCESS]#/boot_identity => RE24.boot_identity_ref
BM031.R10[SUCCESS] => RE24.target_database_identity_ref
BM031.R04[FAILURE] => RE25.campaign_ref
BM031.R04[FAILURE]#/subject_revision => RE25.subject_revision_ref
BM031.S02[FAILURE]#/plan[non-NONE] => RE25.qualification_plan_ref
BM031.S02[FAILURE]#/plan_acceptance[non-NONE] => RE25.qualification_plan_acceptance_ref
BM031.S02[FAILURE] => RE25.qualification_receipt_ref
BM031.R01[FAILURE,profile-observed] => RE25.support_profile_ref
BM033.R01 / activation-proposal.attestation => RS03.value.attestation_ref
BM033.R02 / activation-proposal.manifest-binding => RS03.value.manifest_binding_ref
BM033.R03 / activation-proposal.final-manifest => RS03.value.final_manifest_ref
BM034.R01 / activation-abandonment.attestation => RX04.attestation_ref
BM034.R02 / activation-abandonment.noncommit-or-resolution => RX04.noncommit_or_resolution_ref
BM034.R03 / activation-abandonment.transaction-identity => RX04.transaction_identity_ref

# WORK
BM036.R01[RESERVATION] / BS097.plan => RW03.plan_ref
BM036.R03[RESERVATION] / BS097.request => RW03.request_ref
BM036.R05[RESERVATION] / BS097.clock_envelope => RW03.clock_envelope_ref
BM036.R05[RESERVATION]#/boot_identity => RW03.boot_identity_ref
BM037.R02 / BS103+BS098.reservation => RW04.reservation_ref + RW05.reservation_ref
BM037.R03 / BS103.plan => RW04.plan_ref
BM038.R01 / committed-result protected request binding => RW06.request_ref
BM038.R02 / BS099.reservation => RW06.reservation_ref
BM038.R03 / BS099.transaction_identity => RW06.transaction_identity_ref
BM038.R04 / BS099.start => RW06.start_ref
BM038.R05 / BS099.result => RW06.typed_result_ref
BM038.R02#/plan / BS099.plan => RW06.plan_ref
BM039.R02[j] / BS100 original|resolution_reservation => RW12.original_reservation_ref|resolution_reservation_ref
BM039.R03[j] / BS100 original|resolution_start => RW12.original_start_ref|resolution_start_ref
BM039.R04[j] / BS100 original|resolution_transaction_identity => RW12.original_transaction_identity_ref|resolution_transaction_identity_ref
BM039.R05 / BS100.original_committed_result => RW12.original_committed_result_ref
BM039.R06 / BS100.original_result => RW12.original_result_ref
BM040.R02[COMMITTED][j] / BS101 original|query_reservation => RW13.original_reservation_ref|query_reservation_ref
BM040.R03[COMMITTED][j] / BS101 original|query_start => RW13.original_start_ref|query_start_ref
BM040.R04[COMMITTED][j] / BS101 original|query_transaction_identity => RW13.original_transaction_identity_ref|query_transaction_identity_ref
BM040.R05[COMMITTED] / BS101.original_committed_result => RW13.original_committed_result_ref
BM040.R06[COMMITTED] / BS101.original_result => RW13.original_result_ref
BM040.R02[INCONCLUSIVE][query] / BS123.reservation => RW08.reservation_ref
BM040.R04[INCONCLUSIVE][original] / BS123.subject_transaction_identity => RW08.subject_transaction_identity_ref
BM040.R04[INCONCLUSIVE][query] / BS123.transaction_identity => RW08.transaction_identity_ref
BM041.R02[j] / BS102 original|resolution_reservation => RW14.original_reservation_ref|resolution_reservation_ref
BM041.R03[j] / BS102 original|resolution_start => RW14.original_start_ref|resolution_start_ref
BM041.R04[j] / BS102 original|resolution_transaction_identity => RW14.original_transaction_identity_ref|resolution_transaction_identity_ref

# PUB
BM043.R01[CURRENT] / BS001.plan => RP02.plan_ref
BM043.R03[CURRENT] / BS001.approval => RP02.approval_ref
BM043.R04[CURRENT] / BS001.authorization_receipt => RP02.authorization_ref
BM043.R05[CURRENT] / BS001.admission.attestation => RP02.attestation_ref
BM043.R06[CURRENT,ROLLBACK] / BS001.action_binding.rollback_preimage_binding => RP02.rollback_preimage_binding_ref
BM043.R01[CURRENT|LATE] / BS108.plan => RP03.plan_ref
BM043.R03[CURRENT|LATE] / BS108.approval => RP03.approval_ref
BM043.R04[CURRENT|LATE] / BS108.authorization_receipt => RP03.authorization_ref
BM043.R01[CURRENT]#/target_database_identity => RP01.target_database_identity_ref
BM044.R01[CURRENT] / BS002.journal_digest carrier => RP04.journal_ref
BM044.R06[CURRENT] / BS002.admission.attestation => RP04.attestation_ref
BM044.R02[CURRENT|LATE] / BS108.plan => RP03.plan_ref
BM044.R04[CURRENT|LATE] / BS108.approval => RP03.approval_ref
BM044.R05[CURRENT|LATE] / BS108.authorization_receipt => RP03.authorization_ref
BM045.R01 / BS003.journal_digest carrier => RP05.journal_ref
BM045.R02 / BS003.proof_digest carrier => RP05.proof_ref
BM045.R03 / BS003.clock_envelope => RP05.clock_envelope_ref
BM045.R05 / BS003.admission.attestation => RP05.attestation_ref
BM046.R01 / BS004.journal_digest carrier => RP06.journal_ref
BM046.R02 / BS004.proof_digest carrier => RP06.proof_ref
BM046.R03 / BS004.deadline_receipt_digest carrier => RP06.deadline_receipt_ref
BM046.R04 / BS004.admission.attestation => RP06.attestation_ref
BM047.R01 / lineage-genesis.manifest => RP08.manifest_ref
BM047.R02[legacy-restore-selected] / lineage-genesis.restore-content => RP08.legacy_restore_content_ref
BM047.R03 / lineage-genesis.attestation => RP08.attestation_ref
BM047.R04 / lineage-genesis.fence-binding => RP08.fence_binding_ref
BM048.R01 / verification-attempt-and-result.mutation-receipt => RP10.m_ref + branch[UNABLE]RP11.m_ref + branch[MISMATCH]RP12.m_ref + branch[TERMINAL_FAILURE]RP13.m_ref + branch[MATCH]RP15.m_ref
BM048.R05 / verification request => RP10.request_ref
BM048.R06 / verification reservation => RP10.reservation_ref
BM048.R07 / verification start => RP10.start_ref
BM048.R08 / verification transaction => RP10.transaction_identity_ref
BM048.R03#/target_database_identity => RP10.target_database_identity_ref + branch[MATCH]RP15.target_database_identity_ref

# CLOSE, FENCE, ACTIVATE
BM049.R01 / BC009.deployment_attestation => RC05.deployment_attestation_ref
BM049.R02 / locked current DeploymentAdmissionPolicy required by closure-case creation => RC05.deployment_policy_ref
BM049.R03 / BC009.qualified_clock_envelope => RC05.clock_envelope_ref
BM049.R01#/closure_policy_limits => RC05.closure_policy_ref
BM049.R04{identity} / BC009 discovery-root derivation => RC05.discovery_root_refs{identity}
BM049.R05{identity} / BC009 inventory descriptor => RC05.inventory_descriptor_refs{identity}
BM049.R06{identity} / BC009 reader-execution binding => RC05.reader_execution_binding_refs{identity}
BM050.R01 / BC011.case_binding => RC09.case_ref
BM050.R02 / BC011.clock_envelope => RC09.clock_envelope_ref
BM050.R01#/deployment_attestation => RC09.deployment_attestation_ref
BM051.R01 / BC011.case_binding => RC09.case_ref
BM051.R02 / claim-attempt binding => RC09.attempt_ref
BM051.R03 / BC011.clock_envelope => RC09.clock_envelope_ref
BM051.R01#/deployment_attestation => RC09.deployment_attestation_ref
BM052.R01 / BC011.case_binding => RC09.case_ref
BM052.R02 / takeover-attempt binding => RC09.attempt_ref
BM052.R03 / BC011.clock_envelope => RC09.clock_envelope_ref
BM052.R01#/deployment_attestation => RC09.deployment_attestation_ref
BM053.R01 / finalization case => RC09.case_ref + RC11.case_ref|RC12.case_ref + RC13.case_ref
BM053.R02 / finalization clock => RC09.clock_envelope_ref
BM053.R01#/deployment_attestation => RC09.deployment_attestation_ref
BM054.R01 / expiry case => RC09.case_ref + RC12.case_ref + RC13.case_ref
BM054.R02 / expiry clock => RC09.clock_envelope_ref
BM054.R01#/deployment_attestation => RC09.deployment_attestation_ref
BM055.S06{manifest-approval} / approval whose digest equals manifest_approval_digest => RC21.pending_envelope.manifest_approval_ref
BM055.R01 / pending-fence deployment-attestation => RC21.pending_envelope.deployment_attestation_ref
BM056.R01 / BC017.writer_inventory => RC25.writer_inventory_ref
BM056.R02 / BC017.deployment_attestation => RC25.deployment_attestation_ref
BM056.R03 / BC017.deployment_policy => RC25.deployment_policy_ref
BM056.R04 / BC017.clock_envelope => RC25.clock_envelope_ref
BM056.R05 / BC017.qualification_receipt => RC25.qualification_receipt_ref
BM056.R06[i] / BC017.current_pass_results[i] => RC25.current_pass_result_refs[i]
BM056.R07 / BC017.realized_admission => RC25.realized_admission_ref
BM056.R08 / BC017.realized_acl => RC25.realized_acl_ref
BM057.R01 / BC018.writer_inventory => RC26.writer_inventory_ref
BM057.R02 / BC018.deployment_attestation => RC26.deployment_attestation_ref
BM057.R03 / BC018.deployment_policy => RC26.deployment_policy_ref
BM057.R04 / BC018.clock_envelope => RC26.clock_envelope_ref
BM057.R05 / BC018.qualification_receipt => RC26.qualification_receipt_ref
BM057.R06[i] / BC018.current_pass_results[i] => RC26.current_pass_result_refs[i]
BM057.R07 / BC018.realized_admission => RC26.realized_admission_ref
BM057.R08 / BC018.realized_acl => RC26.realized_acl_ref
BM058.R01 / BC019.realized_admission => RC27.realized_admission_ref
BM058.R02 / BC019.realized_acl => RC27.realized_acl_ref
BM058.R03[i] / BC019.zero-live-writer member => RC27.zero_writer_refs[i]
BM058.R04{service} / BC019.service-disable member => RC27.service_disable_refs{service}
BM058.R05 / BC020.manifest carrier => RC28.binding.final_manifest_ref
BM058.R06 / RC21 deployment attestation => RC28.binding.deployment_attestation_ref
BM058.R07 / RC21 manifest basis => RC28.binding.manifest_basis_ref
BM058.R08 / RC21 frozen registry => RC28.binding.frozen_reader_registry_ref
BM058.R09{digest} / RC21 reader outcome => RC28.binding.reader_outcome_refs{digest}
BM058.R10{digest} / RC21 artifact exclusion => RC28.binding.artifact_exclusion_refs{digest}
BM058.R11{digest} / RC21 approval => RC28.binding.approval_refs{digest}
BM058.R11{manifest-approval} / unique approval matching manifest_approval_digest => RC28.binding.manifest_approval_ref
BM058.R12{digest} / RC21 referenced evidence => RC28.binding.referenced_evidence_refs{digest}
BM058.D01 / newly derived persistent fence => RC28.binding.persistent_fence_ref
BM058.D02 / ref(BC020) => RC28.binding.current_handoff_ref
BM059.R01 / BC021.persistent_fence => RC29.binding.persistent_fence_ref
BM059.R02 / BC021.prior_handoff => RC29.binding.prior_handoff_ref
BM059.R03 / BC021.deployment_attestation => RC29.binding.deployment_attestation_ref
BM059.S01 / adoption manifest basis => RC29.binding.manifest_basis_ref
BM059.S02 / adoption frozen registry => RC29.binding.frozen_reader_registry_ref
BM059.S03{digest} / adoption reader outcome => RC29.binding.reader_outcome_refs{digest}
BM059.S04 / adoption final manifest => RC29.binding.final_manifest_ref
BM059.S05{digest} / adoption exclusion => RC29.binding.artifact_exclusion_refs{digest}
BM059.S06{digest} / adoption approval => RC29.binding.approval_refs{digest}
BM059.S06{manifest-approval} / unique approval matching manifest_approval_digest => RC29.binding.manifest_approval_ref
BM059.S07{digest} / adoption referenced evidence => RC29.binding.referenced_evidence_refs{digest}
BM059.D01 / ref(BC021) => RC29.binding.current_handoff_ref
BM060.R01 / activation.current-handoff => RX03.current_handoff_ref
BM060.R11 / activation.final-manifest => RX03.final_manifest_ref
BM060.R15 / activation.attestation => RX03.attestation_ref
BM060.R16 / CutoverClockObservation.clock_envelope_ref => RX03.cutover_observation.clock_envelope_ref
BM060.R16#/boot_identity => RX03.cutover_observation.boot_identity_ref
BM060.R16#/synchronization_epoch => RX03.cutover_observation.synchronization_epoch_ref
BM061.R01 / abandonment.attestation => RX04.attestation_ref
BM061.R02 / abandonment.noncommit-or-resolution => RX04.noncommit_or_resolution_ref
BM061.R03 / abandonment.transaction-identity => RX04.transaction_identity_ref
BM062.R08 / ManifestBasis source carrier => RC01.manifest_basis_ref + RC02[*].manifest_basis_ref + RC03[*].manifest_basis_ref + RC04[*].manifest_basis_ref + RC14.body_ref
BM062.R09 / ManifestBasis.reader_registry_digest resolved carrier => RC01.frozen_reader_registry_ref
BM062.R09 / ManifestBasis.reader_registry_digest resolved carrier => RC14.frozen_reader_registry_ref
BM062.R08#/qualified_clock_envelope => RC14.qualified_clock_envelope_ref
BM062.R08#/upper_bound_derivation_contract => RC14.upper_bound_derivation_contract_ref
BM062.R08#/epoch_activation_proposal/deployment_attestation => RC14.deployment_attestation_ref
BM062.R08#/writer_fence_proposal/role_grant_set => RC14.role_grant_set_ref
BM062.R08#/writer_fence_proposal/target_partition_proof => RC14.target_partition_proof_ref
BM062.R08#/writer_fence_proposal/realization_policy => RC14.realization_policy_ref
BM062.R08#/writer_fence_proposal/writer_inventory => RC14.writer_inventory_ref
BM062.R08#/writer_fence_proposal/quiescence_predicates{predicate_id}/derivation_contract => RC14.quiescence_derivation_contract_refs{predicate_id}
BM062.R10{digest} / InventoryObservation.reader_output => RC04{digest}.body_ref
BM062.R11 / FinalManifest source carrier => RC15[*].final_manifest_ref + RC16[*].final_manifest_ref + RC17.body_ref + RC18[*].final_manifest_ref + RC19[*].final_manifest_ref + RC20.final_manifest_ref
BM062.R12{digest} / FinalManifest.exclusion_bindings[*].exclusion_body => RC15{digest}.body_ref
BM062.R13{digest} / FinalManifest approval members => RC16{digest}.body_ref
BM062.R14{digest} / ManifestEnvelope.referenced_evidence[*] => RC18{digest}.typed_member_ref

# FENCE typed protected-field and decoded-bridge reference projections
DRP-RC21-TARGET-GRAPH
  source_member = SPA011/SC052/ManifestBasis.target_database_identity
  carrier = DECODED_BRIDGE(BM055.S01,BC001.target_database_identity)
  destination = RC21.pending_envelope.target_surface.target_database_identity
  domain = BodyRef<BS050>; disposition = MATERIALIZE
DRP-RC21-TARGET-ATTESTATION
  source_member = SPA011/SC052/DeploymentAttestation.target_database_identity
  carrier = BODY_MEMBER(MRE:BM055.R01:/target_database_identity)
  destination = RC21.pending_envelope.target_surface.target_database_identity
  domain = BodyRef<BS050>; disposition = EQUALITY_ONLY
DRP-RC21-EXCLUSION-APPROVAL{approval_digest}
  source_member = SPA011/SC052/ManifestEnvelope.exclusion_approval_receipts{approval_digest}.approval_digest
  carrier = BODY_MEMBER(BM055.S06{approval_digest}[ARTIFACT_EXCLUSION])
  destination = RC21.pending_envelope.exclusion_approval_receipts{approval_digest}.approval_ref
  domain = BodyRef<BC008>; disposition = MATERIALIZE
DRP-RC21-EXCLUSION-SUBJECT{approval_digest}
  source_member = SPA011/SC052/Approval.subject_digest[ARTIFACT_EXCLUSION]
  carrier = BODY_MEMBER(DLI:BM055.S06{approval_digest}:DL006)
  destination = RC21.pending_envelope.exclusion_approval_receipts{approval_digest}.exclusion_ref
  domain = BodyRef<BC007>; disposition = MATERIALIZE

DRP-RC28-TARGET-PENDING
  source_member = SPA011/SC055/PendingFenceEnvelope.target_surface.target_database_identity
  carrier = PROTECTED_FIELD(RC21.pending_envelope.target_surface.target_database_identity), READ/NO_WRITE
  destination = RC28.binding.target_surface.target_database_identity
  domain = BodyRef<BS050>; disposition = MATERIALIZE
DRP-RC28-TARGET-HANDOFF
  source_member = SPA011/SC055/OriginFenceManifestBinding.target_database_identity
  carrier = DECODED_BRIDGE(BM058.D02,BC020.target_database_identity)
  destination = RC28.binding.target_surface.target_database_identity
  domain = BodyRef<BS050>; disposition = EQUALITY_ONLY
DRP-RC28-ATTESTATION-HANDOFF
  source_member = SPA011/SC055/OriginFenceManifestBinding.deployment_attestation
  carrier = BODY_MEMBER(MRE:BM058.D02:/deployment_attestation)
  destination = RC28.binding.deployment_attestation_ref
  domain = BodyRef<BS021>; disposition = EQUALITY_ONLY
DRP-RC28-EXCLUSION-APPROVAL{approval_digest}
  source_member = SPA011/SC055/PendingFenceEnvelope.exclusion_approval_receipts{approval_digest}.approval_ref
  carrier = PROTECTED_FIELD(RC21.pending_envelope.exclusion_approval_receipts{approval_digest}.approval_ref), READ/NO_WRITE
  destination = RC28.binding.exclusion_approval_receipts{approval_digest}.approval_ref
  domain = BodyRef<BC008>; disposition = MATERIALIZE
DRP-RC28-EXCLUSION-SUBJECT{approval_digest}
  source_member = SPA011/SC055/PendingFenceEnvelope.exclusion_approval_receipts{approval_digest}.exclusion_ref
  carrier = PROTECTED_FIELD(RC21.pending_envelope.exclusion_approval_receipts{approval_digest}.exclusion_ref), READ/NO_WRITE
  destination = RC28.binding.exclusion_approval_receipts{approval_digest}.exclusion_ref
  domain = BodyRef<BC007>; disposition = MATERIALIZE

DRP-RC29-TARGET-PRIOR
  source_member = SPA011/SC056/prior-completed-binding.target_surface.target_database_identity
  carrier = PROTECTED_FIELD(prior_RC28_or_RC29.binding.target_surface.target_database_identity), READ/NO_WRITE
  destination = RC29.binding.target_surface.target_database_identity
  domain = BodyRef<BS050>; disposition = MATERIALIZE
DRP-RC29-TARGET-GRAPH
  source_member = SPA011/SC056/ManifestBasis.target_database_identity
  carrier = DECODED_BRIDGE(BM059.S01,BC001.target_database_identity)
  destination = RC29.binding.target_surface.target_database_identity
  domain = BodyRef<BS050>; disposition = EQUALITY_ONLY
DRP-RC29-TARGET-HANDOFF
  source_member = SPA011/SC056/ActiveFenceManifestAdoption.target_database_identity
  carrier = DECODED_BRIDGE(BM059.D01,BC021.target_database_identity)
  destination = RC29.binding.target_surface.target_database_identity
  domain = BodyRef<BS050>; disposition = EQUALITY_ONLY
DRP-RC29-ATTESTATION-HANDOFF
  source_member = SPA011/SC056/ActiveFenceManifestAdoption.deployment_attestation
  carrier = BODY_MEMBER(MRE:BM059.D01:/deployment_attestation)
  destination = RC29.binding.deployment_attestation_ref
  domain = BodyRef<BS021>; disposition = EQUALITY_ONLY
DRP-RC29-EXCLUSION-APPROVAL{approval_digest}
  source_member = SPA011/SC056/ManifestEnvelope.exclusion_approval_receipts{approval_digest}.approval_digest
  carrier = BODY_MEMBER(BM059.S06{approval_digest}[ARTIFACT_EXCLUSION])
  destination = RC29.binding.exclusion_approval_receipts{approval_digest}.approval_ref
  domain = BodyRef<BC008>; disposition = MATERIALIZE
DRP-RC29-EXCLUSION-SUBJECT{approval_digest}
  source_member = SPA011/SC056/Approval.subject_digest[ARTIFACT_EXCLUSION]
  carrier = BODY_MEMBER(DLI:BM059.S06{approval_digest}:DL006)
  destination = RC29.binding.exclusion_approval_receipts{approval_digest}.exclusion_ref
  domain = BodyRef<BC007>; disposition = MATERIALIZE
```

The protected field-copy registry is scalar-only. Every displayed pair emits one concrete PFC identity containing its full source path, destination path, stable member key or ordinal, and outcome:

```text
PFC-RC21-GRAPH / source=authenticated pre-fence ManifestEnvelope, SUPPLIED
  disposition = MATERIALIZE
  cardinality = one manifest record and exactly one exclusion-receipt member per source approval_digest
  access = C21 invokes FF01→O15; every principal is denied direct RC21 receipt/key DML
  pairs =
    manifest_approval_record.approval_digest -> RC21.pending_envelope.manifest_approval_digest
    manifest_approval_record.approval_channel_receipt.boundary_id -> RC21.pending_envelope.manifest_approval_receipt.boundary_id
    manifest_approval_record.approval_channel_receipt.receipt_format -> RC21.pending_envelope.manifest_approval_receipt.receipt_format
    manifest_approval_record.approval_channel_receipt.receipt_bytes_base64url -> RC21.pending_envelope.manifest_approval_receipt.receipt_bytes_base64url
    decoded(manifest_approval_record.approval_channel_receipt.receipt_bytes_base64url) -> RC21.pending_envelope.manifest_approval_receipt.receipt_bytes
    manifest_approval_record.approval_channel_receipt.receipt_sha256 -> RC21.pending_envelope.manifest_approval_receipt.receipt_sha256
    exclusion_approval_receipts{approval_digest}.approval_digest -> RC21.pending_envelope.exclusion_approval_receipts{approval_digest}.approval_digest
    exclusion_approval_receipts{approval_digest}.approval_channel_receipt.boundary_id -> RC21.pending_envelope.exclusion_approval_receipts{approval_digest}.receipt.boundary_id
    exclusion_approval_receipts{approval_digest}.approval_channel_receipt.receipt_format -> RC21.pending_envelope.exclusion_approval_receipts{approval_digest}.receipt.receipt_format
    exclusion_approval_receipts{approval_digest}.approval_channel_receipt.receipt_bytes_base64url -> RC21.pending_envelope.exclusion_approval_receipts{approval_digest}.receipt.receipt_bytes_base64url
    decoded(exclusion_approval_receipts{approval_digest}.approval_channel_receipt.receipt_bytes_base64url) -> RC21.pending_envelope.exclusion_approval_receipts{approval_digest}.receipt.receipt_bytes
    exclusion_approval_receipts{approval_digest}.approval_channel_receipt.receipt_sha256 -> RC21.pending_envelope.exclusion_approval_receipts{approval_digest}.receipt.receipt_sha256
  outcomes =
    APPLIED writes every pair in AG-BM055 with RC21 and the typed DRP members;
    EXACT_REPLAY reverse-compares every stored key, source scalar, decoded byte
    sequence, and digest without DML; REFUSAL_NO_WRITE writes no RC21 member

PFC-RC28-PENDING / source=RC21.pending_envelope, READ/NO_WRITE
  disposition = MATERIALIZE
  pairs =
    target_surface.target_surface_digest -> RC28.binding.target_surface.target_surface_digest
    fence_generation -> RC28.binding.fence_generation
    cutover_id -> RC28.binding.cutover_id
    manifest_body_digest -> RC28.binding.manifest_body_digest
    manifest_approval_digest -> RC28.binding.manifest_approval_digest
    canonical_proposal_digest -> RC28.binding.canonical_proposal_digest
    consumed_invocation_digest -> RC28.binding.consumed_invocation_digest
    continuity_role_identity -> RC28.binding.continuity_role_identity
    continuity_session_id -> RC28.binding.continuity_session_id
    reserved_publication_epoch -> RC28.binding.reserved_publication_epoch
    incarnation_capability_digest -> RC28.binding.incarnation_capability_digest
    adoption_generation -> RC28.binding.adoption_generation
    service_step_set{service_identity} -> RC28.binding.service_step_set{service_identity}
    exclusion_approval_receipts{approval_digest}.approval_digest -> RC28.binding.exclusion_approval_receipts{approval_digest}.approval_digest
    manifest_approval_receipt.boundary_id -> RC28.binding.manifest_approval_receipt.boundary_id
    manifest_approval_receipt.receipt_format -> RC28.binding.manifest_approval_receipt.receipt_format
    manifest_approval_receipt.receipt_bytes_base64url -> RC28.binding.manifest_approval_receipt.receipt_bytes_base64url
    manifest_approval_receipt.receipt_bytes -> RC28.binding.manifest_approval_receipt.receipt_bytes
    manifest_approval_receipt.receipt_sha256 -> RC28.binding.manifest_approval_receipt.receipt_sha256
    exclusion_approval_receipts{approval_digest}.receipt.boundary_id -> RC28.binding.exclusion_approval_receipts{approval_digest}.receipt.boundary_id
    exclusion_approval_receipts{approval_digest}.receipt.receipt_format -> RC28.binding.exclusion_approval_receipts{approval_digest}.receipt.receipt_format
    exclusion_approval_receipts{approval_digest}.receipt.receipt_bytes_base64url -> RC28.binding.exclusion_approval_receipts{approval_digest}.receipt.receipt_bytes_base64url
    exclusion_approval_receipts{approval_digest}.receipt.receipt_bytes -> RC28.binding.exclusion_approval_receipts{approval_digest}.receipt.receipt_bytes
    exclusion_approval_receipts{approval_digest}.receipt.receipt_sha256 -> RC28.binding.exclusion_approval_receipts{approval_digest}.receipt.receipt_sha256

PFC-RC28-HANDOFF / source=decoded BM058.D02 BC020, DERIVED
  disposition = EQUALITY_ONLY
  pairs =
    target_surface_digest -> RC28.binding.target_surface.target_surface_digest
    fence_generation -> RC28.binding.fence_generation
    adoption_generation -> RC28.binding.adoption_generation
    prior_manifest_binding_digest -> RC28.binding.prior_handoff_ref_or_NONE
      comparator = source is literal NONE and destination is literal NONE
    cutover_id -> RC28.binding.cutover_id
    manifest_body_digest -> RC28.binding.manifest_body_digest
    manifest_approval_digest -> RC28.binding.manifest_approval_digest
    persistent_fence_evidence_digest -> RC28.binding.persistent_fence_evidence_digest
    continuity_session_id -> RC28.binding.continuity_session_id
    reserved_publication_epoch -> RC28.binding.reserved_publication_epoch
    incarnation_capability_digest -> RC28.binding.incarnation_capability_digest
    authority -> RC28.binding.authority
      comparator = both are literal NONE

PFC-RC29-HANDOFF / source=decoded BM059.D01 BC021, DERIVED
  disposition = EQUALITY_ONLY
  pairs =
    target_surface_digest -> RC29.binding.target_surface.target_surface_digest
    fence_generation -> RC29.binding.fence_generation
    adoption_generation -> RC29.binding.adoption_generation
    prior_manifest_binding_digest -> RC29.binding.prior_handoff_ref_or_NONE.digest
    cutover_id -> RC29.binding.cutover_id
    manifest_body_digest -> RC29.binding.manifest_body_digest
    manifest_approval_digest -> RC29.binding.manifest_approval_digest
    persistent_fence_evidence_digest -> RC29.binding.persistent_fence_evidence_digest
    continuity_session_id -> RC29.binding.continuity_session_id
    reserved_publication_epoch -> RC29.binding.reserved_publication_epoch
    incarnation_capability_digest -> RC29.binding.incarnation_capability_digest
    authority -> RC29.binding.authority
      comparator = both are literal NONE

PFC-RC29-INHERITED-MATERIALIZE
  source = locked immediately prior RC28-or-RC29 binding, READ/NO_WRITE
  disposition = MATERIALIZE
  pairs =
    canonical_proposal_digest -> RC29.binding.canonical_proposal_digest
    consumed_invocation_digest -> RC29.binding.consumed_invocation_digest
    continuity_role_identity -> RC29.binding.continuity_role_identity
    service_step_set{service_identity} -> RC29.binding.service_step_set{service_identity}

PFC-RC29-INHERITED-EQUALITY
  source = locked immediately prior RC28-or-RC29 binding, READ/NO_WRITE
  disposition = EQUALITY_ONLY
  pairs =
    target_surface.target_surface_digest -> RC29.binding.target_surface.target_surface_digest
    fence_generation -> RC29.binding.fence_generation
    persistent_fence_evidence_digest -> RC29.binding.persistent_fence_evidence_digest
    continuity_session_id -> RC29.binding.continuity_session_id
    incarnation_capability_digest -> RC29.binding.incarnation_capability_digest

PFC-RC29-GRAPH-MATERIALIZE
  source = authenticated adoption ManifestEnvelope, SUPPLIED
  disposition = MATERIALIZE
  pairs =
    manifest_approval_record.approval_channel_receipt.boundary_id -> RC29.binding.manifest_approval_receipt.boundary_id
    manifest_approval_record.approval_channel_receipt.receipt_format -> RC29.binding.manifest_approval_receipt.receipt_format
    manifest_approval_record.approval_channel_receipt.receipt_bytes_base64url -> RC29.binding.manifest_approval_receipt.receipt_bytes_base64url
    decoded(manifest_approval_record.approval_channel_receipt.receipt_bytes_base64url) -> RC29.binding.manifest_approval_receipt.receipt_bytes
    manifest_approval_record.approval_channel_receipt.receipt_sha256 -> RC29.binding.manifest_approval_receipt.receipt_sha256
    exclusion_approval_receipts{approval_digest}.approval_digest -> RC29.binding.exclusion_approval_receipts{approval_digest}.approval_digest
    exclusion_approval_receipts{approval_digest}.approval_channel_receipt.boundary_id -> RC29.binding.exclusion_approval_receipts{approval_digest}.receipt.boundary_id
    exclusion_approval_receipts{approval_digest}.approval_channel_receipt.receipt_format -> RC29.binding.exclusion_approval_receipts{approval_digest}.receipt.receipt_format
    exclusion_approval_receipts{approval_digest}.approval_channel_receipt.receipt_bytes_base64url -> RC29.binding.exclusion_approval_receipts{approval_digest}.receipt.receipt_bytes_base64url
    decoded(exclusion_approval_receipts{approval_digest}.approval_channel_receipt.receipt_bytes_base64url) -> RC29.binding.exclusion_approval_receipts{approval_digest}.receipt.receipt_bytes
    exclusion_approval_receipts{approval_digest}.approval_channel_receipt.receipt_sha256 -> RC29.binding.exclusion_approval_receipts{approval_digest}.receipt.receipt_sha256

PFC-RC29-GRAPH-EQUALITY
  source = authenticated adoption graph, SUPPLIED
  disposition = EQUALITY_ONLY
  pairs =
    manifest_basis_body.target_surface_digest -> RC29.binding.target_surface.target_surface_digest
    manifest_body_digest -> RC29.binding.manifest_body_digest
    manifest_approval_record.approval_digest -> RC29.binding.manifest_approval_digest
```

PFC-RC21-GRAPH is the complete current manifest-receipt and exclusion-receipt scalar/key projection. Its APPLIED, EXACT_REPLAY, and REFUSAL_NO_WRITE rows are bidirectional and are part of AG-BM055; a missing `.approval_digest`, source scalar, decoded-byte equality, or reverse comparison invalidates the entire pending envelope. No PFC pair contains `target_database_identity`, `deployment_attestation`, `approval_ref`, `exclusion_ref`, or another BodyRef. Those members are handled only by the typed DRPs above. Every PFC identity includes its complete source and destination field paths and set-member key. RC28 requires equality between every duplicated RC21 and BC020 value. RC29 requires equality among BC021, the supplied graph, and the inherited prior binding wherever their domains overlap. Applied pending/origin/adoption emits every MATERIALIZE write and every EQUALITY_ONLY check in its BM atomic group; exact-current replay verifies every materialized destination and repeats every equality check without DML; partial construction, superseded binding, or refusal emits none.

The BM062 structural expansion is also closed: each `ManifestBasis.discovery_roots` member creates its RC01 member projection; each `inventory_observations` member creates one RC02 row; each `dependency_edges` member creates one RC03 row; each referenced BC003/BC004 creates one RC04 row; each exclusion and approval creates RC15 and RC16; each referenced-evidence member creates RC18; every `FinalDisposition.ordinal` creates RC19; and the one `predecessor_selection` value, including literal `NONE`, creates RC20. The corresponding manifest-basis or final-manifest source-carrier DRP above is present on every such row. Cardinality, ordinal continuity, stable member identity, and branch presence come from the accepted BC001/BC006 grammar, not from rows that happen to be emitted.

Every database-origin MRR and protected-field DRP retains its original `R(...)=READ/NO_WRITE` edge and gains exactly the destination edges printed above. A nested DRP attaches to its exact MRE/DLI source and parent chain; supplied/derived nested sources retain their FB01/FB02 requirements. The SPA001–SPA012/SRP and VR001–VR004 rosters independently require their carriers and forbid validation-only classification. A root or nested member absent from DRP is validation/currentness-only only when the independently anchored source member has no durable copied field. An unlisted destination, missing listed destination, altered field, collapsed member, destination without source provenance, or SRP/VR member without its destination is invalid. Prospective checks are `EV106-SRP-<source member identity>`, `EV106-DRP-<complete DRP identity>`, `EV106-PFC-<complete PFC identity>`, `EV106-VR001`–`EV106-VR004`, and `EV106-MEMBER-<member identity>-REQ-<complete destination edge>`.

For `WORK_RESULT`, the carrier relation is fixed together with the body kind: `J→RP02`, `P→RP04`, `R_VALID|R_LATE→RP05`, `M→RP06`, `V_MATCH→RP15`, `V_MISMATCH→RP12`, `V_TERMINAL_FAILURE→RP13`, `V_UNABLE→RP11`, `TRANSACTION_RESOLUTION_OUTCOME→RW12`, `AMBIGUITY_QUERY_OUTCOME→RW13`, `CONCLUSIVE_NONCOMMIT→RW14`, and `RECOVERY_REFUSAL|RECOVERY_AMBIGUITY|RECOVERY_FENCE|RECOVERY_ADVANCEMENT|RECOVERY_UNPROVEN→RW07|RW08|RW09|RW10|RW11`. The branch-selected relation is part of the BREQ identity.

The SPA/SRP roster, S/D table, direct R rule, R exception table, VR roster, DRP registry, and scalar-only PFC registry are set-equal to their emitted source members and requiring edges: each source member has one exact proposal carrier and destination, each emitted instance has at least one edge, each listed edge has one source instance, and a source member, carrier, source read, destination projection, field copy, or edge cannot be added, removed, or reassigned independently. BM012.R06, BM015.R03, BM017.R05, and BM018.R05 retain their keyed result-subject behavior. Qualified-clock members expand every clock and boot destination; success and failure admission expand separately; fence members expand every RC21/RC28/RC29 target, attestation, approval, exclusion, receipt, handoff, graph, and scalar binding. BM013 retains its exact three-body group, BM038 separately binds plan and typed result, and BM062 expands every graph source and RC destination printed above.

`WORK_RESULT` is not open-ended. The result-kind mapping is `J→BS001`, `P→BS002`, `R_VALID|R_LATE→BS003`, `M→BS004`, `V_MATCH→BS005`, `V_MISMATCH→BS006`, `V_TERMINAL_FAILURE→BS007`, `V_UNABLE→BS008`, `TRANSACTION_RESOLUTION_OUTCOME→BS100`, `AMBIGUITY_QUERY_OUTCOME→BS101`, `CONCLUSIVE_NONCOMMIT→BS102`, and `RECOVERY_REFUSAL|RECOVERY_AMBIGUITY|RECOVERY_FENCE|RECOVERY_ADVANCEMENT|RECOVERY_UNPROVEN→BS122|BS123|BS124|BS125|BS126` respectively.

The MRE registry is a closed parameterized representation of every transitive body member, not a promise to generate a registry later. For every emitted MRR root, parse its exact pinned BS or BC grammar and emit one record with identity `MRE:<MRR-pattern-id>:<branch-qualified-canonical-JSON-pointer>` for every typed body-reference occurrence. Sequence occurrences append their source ordinal; set occurrences append the referenced body digest; optional and union occurrences append the exact active branch. Recurse until no body-reference field remains. A repeated reference at two paths produces two records; a cycle, missing body, wrong kind/version, or path outside the closed grammar rejects the operation.

For a SUPPLIED root, its MRE records use FB01 and must occur in `ResolvedOrdinaryBodyClosure`. For a DERIVED root, its root uses FB01 and each referenced preexisting body uses FB02 unless another explicit DERIVED root in the same atomic group supplies that body. For a DATABASE root, both root and members use FB02. Every MRE record carries `BODY_PARENT(immediate parent member record,exact reference path)` plus every BREQ edge of its MRR root. Deeper recursion names the immediately preceding MRE as parent while retaining the root edges. This is an exact one-to-many expansion, so a transitive member cannot remain after its parent or requiring relation/effect is removed. A generic `EvidenceRef` occurrence has domain `OrdinaryBodyKind`; a source field with a narrower named reference type retains that one exact kind. No MRE rule can admit BS080, BS095, BS110, an unknown kind, or an implementation-selected reference domain.

Compatibility fields that bind bodies by digest or embedded canonical body rather than a typed reference use these closed templates:

```text
DL001 BC001.reader_registry_digest -> BC002
DL002 BC006.manifest_basis_digest -> BC001
DL003 BC006.exclusion_bindings[i].exclusion_body + exclusion_body_digest -> BC007
DL004 BC006.exclusion_bindings[i].approval_body + approval_digest -> BC008
DL005 BC008.subject_digest[CUTOVER_MANIFEST] -> BC006
DL006 BC008.subject_digest[ARTIFACT_EXCLUSION] -> BC007
DL007 BC019.realized_admission_digest -> BC015
DL008 BC019.realized_acl_digest -> BC016
DL009 BC019.zero_live_writer_evidence_digest -> BC017
DL010 BC019.service_disable_evidence_set_digest -> canonical complete RC26 BC018 member set
DL011 BC020|BC021.prior_manifest_binding_digest -> NONE for BC020 or the immediately prior BC020|BC021
DL012 BC020|BC021.manifest_body_digest -> BC006 and manifest_approval_digest -> BC008
DL013 BC020|BC021.persistent_fence_evidence_digest -> BC019
```

Each `DLnnn` above is a DLT template. For every MRR or MRE instance whose member kind and exact field path match its left side, emit one concrete record `DLI:<left-member-record-id>:DLnnn:<branch-qualified-field-path>`. A DLI resolves the target through FB02, validates the complete canonical target bytes and digest, and carries `BODY_PARENT(left-member-record-id,field-path)` plus every BREQ edge carried by that left member. DL010 emits one DLI for the set digest and one child DLI for each exact RC26/BC018 set member; it validates the separator-free canonical member-set preimage as well as every member. A matching left-hand occurrence without exactly one DLI, or a DLI without a matching occurrence, is invalid. The source anchors are the pinned BC001/BC006–BC008 and BC019–BC021 definitions; each prospective check is `EV106-MEMBER-<DLI identity>`.

For every emitted MRR, MRE, or DLI record, the proposal clause is its BM row, exact BREQ set, and FB01 or FB02. Its prospective check is `EV106-MEMBER-<record identity>` plus `EV106-MEMBER-<record identity>-REQ-<complete edge encoding>` for every edge. Forward and reverse equality covers identities, all member fields, and each relation/fact/effect or parent edge. The only canonical-body exclusions are `NBR01 BM001 BS110→RB02`, `NBR02 BM025 BS080→RB06`, and `NBR03 every BS095 occurrence→transaction-local`; their storage classifications are checked instead of invoking FB01/FB02.

### 5.2.2 Owner-internal cross-owner routines

Each routine below is `SECURITY DEFINER`, has `PUBLIC` revoked, is executable only by the listed outer owner, and rejects calls whose staged outer facts, locks, or exact source-derived operands are absent. The active manifest contains exactly one outer-call dependency for each routine. IA01–IA09 perform the listed atomic effect; IA10 is read-only and retains its currentness locks for the outer transaction. No routine commits, opens a transaction, accepts a login principal, or has an adapter method.

| ID / logical routine | Allow → owner | Exact transaction-local contract |
|---|---|---|
| IA01 `initialize_operation_accounting(plan)` | O04 → O10 | Requires new exact RA03 and RA07=`PLAN_ISSUED` staged in this transaction and RS10 `ABSENT`. Writes RS10 `PRESENT` with matching plan; all eleven counters/charges zero and `next_reservation_ordinal=1`. Returns that state provisionally. It rejects existing work and is not called on FA03 replay. |
| IA02 `issue_qualification_receipt_observation(plan,acceptance,profile)` | O06 → O05 | Requires FQ02’s locked exact plan, acceptance, profile, class results, and complete current tier partitions. Derives the first `QUALIFICATION_RECEIPT_ISSUE` subject key, qualified clock sample, and RE05 body; writes or exact-replays RE05 and returns its ref/bound to FQ02. |
| IA03 `issue_deployment_attestation_observation(target,surface,admission_generation,clock_envelope)` | O09 → O05 | Requires FAD02’s locked policy, receipt, partitions, live bindings, exact checked-next generation, and current envelope. Derives and writes the one `DEPLOYMENT_ATTESTATION_ISSUE` RE05 and returns its ref/bound. |
| IA04 `issue_operation_work_reservation_observation(plan,ordinal,work_identity_digest)` | O10 → O05 | Requires FW02’s locked RS10, exact limits, checked-next ordinal, and admissible clock binding. Derives and writes the `OPERATION_WORK_RESERVE` RE05 and returns its envelope, boot, upper bound, and ref. It runs only on the successful reservation branch; refusal writes no observation. |
| IA05 `install_reserved_publication_epoch(attestation,expected_active,expected_high_water,expected_reserved,fence_binding)` | O09 → O16 | Requires FAD02’s uncommitted exact RE24 plus its locked RS01/RS16/RS18 projections. Recomputes checked-next epoch and all RX01 fields, then atomically writes RX01,RX02=`RESERVED_FENCED`, advances RS16, and installs RS18. It returns the exact epoch/binding provisionally. |
| IA06 `install_activation_proposal(epoch,expected_reserved,proposal,capability_digest)` | O09 → O16 | Requires exact immutable RX01 and current RX02/RS18 plus current attestation/fence. Writes RS02 and RS03 only. It never changes RX01. |
| IA07 `install_compatibility_activation_metadata(database_graph_projection)` | O16 → O18 | Accepts only FX01’s transaction-local `ActivationManifestGraphProjection`, never caller or adapter bytes. It verifies the current-handoff identity, all BM062/MRE/DL database-origin records, inventory, reader outcomes, basis, exclusions, approvals, final manifest, dispositions, and predecessor selection against the locked projection. Atomically writes or exact-replays RC01–RC04 and RC14–RC20. A missing lock, supplied-origin member, changed graph, or unresolved member rejects the outer activation. |
| IA08 `initialize_successor_target_and_lineage(target_state,activation)` | O16 → O12 | Requires FX01’s exact manifest target state, RS09=`ABSENT`, and RS20=`ABSENT`. Atomically creates RP08 and initializes RS20 to the manifest generation. It does not populate RS09. |
| IA09 `clear_abandoned_attestation(epoch,expected_attestation)` | O16 → O09 | Requires FX02’s staged RX04 and RX02=`ABANDONED_FENCED`, exact current RS05, and the same conclusive noncommit/inadmissibility proof. Clears RS05 by exact CAS. |
| IA10 `read_active_schema_admission_identity(target_database_identity)` | O09 → O01 | Available only inside FAD02. It locks the singleton RM02 current projection and resolves the route generation's RM01 plus RM05 CATALOG and ACTIVE_SURFACE rows. It requires `InstalledSchemaState=EXACT`, matching target database, contract revision, route generation, active-surface ref/digest, build identity, and admitted adapter generation. It returns the exact `SchemaAdmissionProjection` without manifest bytes and retains the RM currentness locks through FAD02's transaction. Unclassified state, drift, mismatch, or unsupported route refuses. Lock ordering remains issue107 work. |

### 5.3 Evidence, qualification, private review, and policy

| ID / logical function | Allow → owner | Caller input; derived operands; effects/result |
|---|---|---|
| FE01 `ACCEPT_EVIDENCE_CAMPAIGN_PLAN(plan,acceptance)` | C07 → O05 | Two exact refs; derives authenticated principal and complete plan predicates. Writes RE01. `FM`. |
| FE02 `ACCEPT_HISTORICAL_CORPUS_PLAN(plan,acceptance)` | C07 → O05 | Two exact refs; derives closed reader-registry coverage. Writes RE02. `FM`. |
| FE03 `ACCEPT_QUALIFICATION_PLAN(plan,acceptance)` | C07 → O05 | Two exact refs; derives complete qualification expansion. Writes RE03. `FM`. |
| FE04 `REGISTER_EVIDENCE_CAMPAIGN(campaign)` | C08 → O05 | Exact `BS022`; derives accepted basis and planned runs, locks the campaign-tier RE15 subject and the RE15 subject for every affected result key, and requires the campaign subject to equal the campaign-tier protected value. It writes RE04 and each affected RE16 with the corresponding protected subject plus every RS07 pointer atomically. Missing, changed, or mismatched subject state writes none. `FX`. |
| FE05 `ACQUIRE_DEPLOYMENT_EVIDENCE(campaign,run_id,oracle_id)` | C08 → O05 | Only three identifiers; derives current policy/profile/clock, acquisition identity, planned procedure and observed projection. It constructs exactly one BS034 observed projection, one BS015 `DEPLOYMENT_EVIDENCE_ACQUIRE` observation, and one BS064 acquisition, then atomically inserts all three through BM013 with RE05 and RE06. No body or fact may appear alone. `FX`. |
| FE06 `OBSERVE_EVIDENCE_TIME(phase,subject_key_digest,envelope_or_NONE)` | C08 → O05 | Start phase only; no timestamp. Derives protected time. Writes RE05. `FM`. |
| FE07 `REGISTER_EVIDENCE_RUN_RESULT(input)` | C08 → O05 | Closed registration input and exact ordinary closure, never a result/verdict/time/subject; derives acquisitions, observations, records, completion, verdict, and their canonical bodies. It locks the RE15 subject for every affected result key, calls FB01 for every newly constructed ordinary body, and atomically writes RB01,RE05,RE07–RE09, each RE16 with its protected subject, and RS07. Any body, validation, clock, subject, fact, or pointer failure rolls back the complete set. `FX`. |
| FE08 `REGISTER_EVIDENCE_INVALIDITY_FINDING(finding_ref)` | C08 → O05 | Exact `BodyRef<BS038>` and closure; derives the referenced evidence, oracle contract, complete expected/observed projections, and independently recomputes the one named acquisition-procedure, oracle-independence, required-input, tool, limits, or retained-evidence defect. It writes only RB01 and RE10. It creates no RE11/RE13, derives no disposition, and changes no RE16/RS07. A valid deciding `oracle_result=FAIL` is not thereby invalidated. `FX`. |
| FE09 `SET_CURRENT_EVIDENCE_SUBJECT(tier,replacement)` | C09 → O05 | Tier and subject ref; derives and locks the prior subject and affected claims, writes immutable RE14 history, stages the replacement in RE15, then evaluates every affected result against the protected post-transition RE15 subject for that result’s tier. RE14, RE15, every recomputed RE16 subject/body, and every RS07 pointer commit or exact-replay together. The caller supplies no result subject or verdict. `FX`. |
| FE10 `APPLY_EVIDENCE_DISPOSITION(authority_subject,authorization_receipt)` | C09 → O05 | Caller supplies exactly one `EvidenceDispositionAuthoritySubject`, exact `BodyRef<BS045>`, and their ordinary closure; never a caller-finalized disposition, result subject, or application time. It authenticates C09 and the receipt principal, recomputes the complete LF-bearing subject digest, requires every matching receipt/subject field and `issued_at<valid_until`, locks all affected evidence/current results and the RE15 subject for every affected result key, and requires the authority subject revision to equal the protected claim-tier subject. It derives a fresh qualified-clock RE05 with phase `EVIDENCE_DISPOSITION_APPLY` and subject projection `(disposition_id,subject_digest,EVIDENCE_DISPOSITION)`. It requires `trusted_upper_bound<valid_until`; equality is late. INVALIDATION additionally requires the complete ordered record claim set, exact registered RE10, matching campaign/subject/tier/oracle contract, and a nonfailing record, then constructs BS046 and atomically writes RB01,RE12,RE05,RE11, every affected RE16 with its key-tier protected subject, and every RS07 pointer. SUPERSESSION requires the exact current claim-local prior result and a distinct registered current-subject replacement campaign, constructs BS047, and atomically writes RB01,RE12,RE05,RE13 and those affected RE16/RS07 members; the chain must be contiguous, acyclic, and single-successor. Every refusal writes none. `FX`. |
| FE11 `READ_CURRENT_EVIDENCE_TIER_RESULT(claim_id,tier)` | O06,O09,O11,O12,O16 → O05 | Exact key only; derives RS07. Returns one current ref; no write. `FR`. |
| FQ01 `finalize_qualification_class(plan,plan_acceptance,evidence_class,run_results)` | C10 → O06 | Exact `BS016`, `BS018`, one closed class tag, and complete ordered `BS037` references. Derives RE03, nested records, cells, oracles, profile/release/campaign equality and run-derived interval. Inserts only RE17 or returns its exact replay; it has no target or current-slot effect. `FM`. |
| FQ02 `finalize_qualification_receipt(plan,plan_acceptance,support_profile,clock_result,physical_durability_result,capability_result,design_tier_results,implementation_tier_results,release_tier_results)` | C10 → O06 | Exact `BS016`, `BS018`, `BS009`, three individually typed RE17 refs, and three complete ordered `BS039` sequences. Derives and locks every protected current tier/prerequisite pointer, all `PASS` equalities and RE03, invokes IA02 for the first protected issuance observation, and derives validity. New finalization atomically writes RE05 and RE18; exact replay returns their first committed bytes. It accepts no target, CAS, issue time or proposed receipt. `FX`. |
| FPV01 `REGISTER_CONTROLLED_PRIVATE_PACKAGE(package)` | C11 → O07 | Complete private package; derives bounds, deciding evidence, commitment and random public ID. Writes RB06,RB07 only. `FX`. |
| FPV02 `READ_CONTROLLED_PRIVATE_PACKAGE_FOR_REVIEW(public_record_id)` | C12 → O07 | Public ID; derives mapping and current reviewer authorization. Returns exact private package or refusal; no mutation. `FR`. |
| FPV03 `REGISTER_CONTROLLED_PRIVATE_REVIEW(public_id,expected_receipt,projection,receipt)` | C12 → O07 | Exact CAS plus public bodies; derives package/evidence/subject/policies/authorization and commitment. Atomically writes RB10–RB12. `FX`. |
| FPV04 `EXPORT_CURRENT_REVIEWED_EVIDENCE(public_id,expected_receipt)` | C13 → O07 | Public ID/CAS; rederives every currentness and disclosure predicate. Returns projection and receipt together or neither. `FR`. |
| FDP01 `COMPARE_AND_SET_CURRENT_DEPLOYMENT_ADMISSION_POLICY(target,surface,expected,replacement)` | C14 → O08 | Exact target/surface and `BodyRef\|NONE` operands; derives slot and target set. Writes RE19/RE20/RS06 and fences affected admission. `FX`. |

### 5.4 Admission, work, and stages

| ID / logical function | Allow → owner | Caller input; derived operands; effects/result |
|---|---|---|
| FAD01 `register_clock_envelope(target,surface,expected,envelope)` | C15 → O09 | Exact CAS and `BS014`; derives live host/boot and qualified clock predicates. Writes RE21,RS04. `FX`. |
| FAD02 `finalize_deployment_attestation(campaign,deployment_attempt_id,candidate_projection,target,surface,qualification_receipt,expected_current_qualification_receipt,request,expected_attestation,expected_reserved,fence_input)` | C15 → O09 | Caller supplies exact campaign, attempt ID, candidate `BS034` projection, TargetSurfaceKey, `BS020` ref, `Expected<BodyRef<BS020>>`, policy/profile/qualification and activation request, attestation/reservation expectations, and exact `AdmissionFenceInput`. It derives RE18, all three class refs, complete current qualification/deployment tier partitions, policy/clock/live bindings, acquisitions, role grants, writers, time, next epoch, admission generation, and adoption generation. It invokes IA10 with the target database identity and binds the returned locked RM01/RM02/RM05 active-surface/build projection as its current protected-schema identity; O09 never reads RM relations directly. It invokes IA03 for RE05 and IA05 for RX01/RX02/RS16/RS18. For FRESH it requires RS08 absent and atomically writes RE05,RE22–RE24,RX01,RX02,RS05,RS16,RS17,RS18,RS19,RS21 without RC29 or RS20. For COMPATIBILITY it invokes FF05’s LATER_EPOCH mode under the same locks and transaction, adding RC29 and advancing RS08 atomically with those same effects. A valid attempted admission that fails an accepted profile/evidence/schema/topology/fence predicate writes exactly RE25 and none of those effects; exact failed bytes replay, changed bytes conflict, and successful admission writes no RE25. Malformed or unresolved source input is rejected before a registered attempt. Returns provisional `ATTESTED(D,epoch,adoption-or-NONE)`, `FAILED(RE25)`, exact replay, or conflict. `FX`. |
| FAD03 `revoke_or_fence_deployment_attestation(epoch,expected_current,reason)` | C15 → O09 | Exact epoch/CAS and reason; derives current policy/evidence/incarnation. It clears RS05 only by exact CAS and never creates RE25 or edits RE24. A still-reserved epoch is closed only by FX02 after its required conclusive noncommit/inadmissibility proof; this call does not invent an RX02 transition. `FX`. |
| FAD04 `publish_activation_proposal(epoch,expected_current,proposal)` | C15 → O09 | Exact reserved epoch, manifest binding and capability digest; derives current attestation/fence and immutable RX01. Invokes IA06 to write RS02 and RS03 atomically. RX01 is never changed. `FX`. |
| FW01 `preflight_operation_work(request)` | C16,C17,C18,C19 → O10 | Exact `BS094`; independently derives RequestKey, ReservationKey, production WorkSlotKey, and complete WorkIdentityBinding. It reads RW06 by ReservationKey and RS15 by WorkSlotKey. No result returns canonical `BS095 UNRESOLVED`. A result returns COMMITTED_REPLAY only when request, reservation, start, transaction, complete identity, digest, committed mapping, and typed result are byte-identical. A valid result bound to a different request returns UNRESOLVED and proceeds only to FW02; it is not CONFLICT or free replay. A digest resolving different identity bytes or an inconsistent result chain is a database conflict and produces no preflight body. It writes nothing. `FT`. |
| FW02 `reserve_operation_work(request)` | C16,C17,C18,C19 → O10 | The exact unresolved `BS094`; derives RequestKey, ReservationKey, production WorkSlotKey, complete WorkIdentityBinding, RS10 counters, checked-next ordinal, limits, clock and deadline. Exact prior reservation or refusal for the same RequestKey replays without another charge. Otherwise it atomically writes RW01 and either request-keyed RW02 with no observation or accounting change, or invokes IA04 and writes RE05,RW03,the complete advanced RS10,and RS13 keyed by WorkSlotKey. Same `(plan,request_id)` with changed bytes produces request-keyed `REQUEST_CONFLICT`; another request for an existing ReservationKey produces request-keyed `WORK_ALREADY_RESERVED`; neither aliases the first request or changes accounting. A digest/body collision fails closed. `FX`. |
| FW03 `start_operation_work(reservation,incarnation_proof)` | C16,C17,C18,C19 → O10 | Exact reservation and authenticated incarnation; locks and revalidates RS10, rederives ReservationKey, production WorkSlotKey, and complete WorkIdentityBinding, and derives server transaction identity. Atomically writes RW04,RW05 and RS14 under that WorkSlotKey. `FX`. |
| FW04 `commit_ordinary_work_result(reservation,result_kind,result)` | O10,O11,O12,O13 → O10 | Owner-internal only inside the exact stage transaction; requires the caller owner to have staged the permitted typed result fact and its BM body members, then rederives ReservationKey, production WorkSlotKey, complete WorkIdentityBinding, and the start/transaction/result mapping. O10 constructs BS099 and writes RW06 and RS15 under the production WorkSlotKey with complete WorkIdentityBinding atomically with that staged result. FW04 cannot write another owner's result relation or commit separately. `FX`. |
| FW05 `resolve_committed_transaction(resolver_reservation,original_transaction)` | C19 → O10 | Exact resolver/original chains; derives existing original result. Writes RW12,RW06/RS15 for resolver only; no duplicate stage. `FX`. |
| FW06 `query_transaction_ambiguity(query_reservation,original_transaction)` | C19 → O10 | Exact query/original chains; derives protected transaction state. A committed original writes RW13 plus the query’s RW06/RS15; a bounded inconclusive result writes RW08 plus the query’s RW06/RS15. Neither branch duplicates the original stage. `FX`. |
| FW07 `record_conclusive_noncommit(original,resolver,subject,failure_evidence)` | C19 → O10 | Both exact started chains, `RW15`, and `BS087`; derives absence of committed original. Writes RW14, original RW06/RS15, resolver RW10/RW06/RS15, closing both slots atomically. `FN`. |
| FW08 `read_operation_work_outcome(lookup_key)` | C16,C17,C18,C19 → O10 | Exact `WorkOutcomeLookupKey`. `REQUEST` returns only that RequestKey’s refusal or its linked reservation/result state. `WORK` validates the production WorkSlotKey and recomputes the complete stored WorkIdentityBinding, derives ReservationKey, and returns its one reservation/start/result/recovery chain. It derives RW01–RW15 and RS13–RS15, never treats a refusal as a reservation result, and writes nothing. `FR`. |
| FJ01 `create_or_read_journal(request,observation_request_id)` | C16 → O11 | Exact nonclock plan/action/target/preimage inputs; derives authority, admission, current lineage, clock, candidate/ciphertext and canonical `J`. `CURRENT` atomically writes RP01,RP02,RP03,RB04,RB05 and the FW04 work result. `LATE` writes only RP03 and that work result; it creates no aggregate, J, or adoption. `FX`. |
| FJ02 `create_or_read_publication_proof(aggregate,j_ref,request_id)` | C16 → O11 | Exact aggregate/J; derives authority, admission, lineage and protected time. `CURRENT` writes RP03,RP04 and the FW04 work result. `LATE` writes only RP03 and that work result. `FX`. |
| FJ03 `create_or_read_deadline_receipt(aggregate,p_ref,qualified_sample)` | C16 → O11 | Exact aggregate/P and adapter sample; derives current envelope, conservative `U`, authority/admission/lineage. Writes RP05 and work result as `VALID\|LATE`, or no authorizing receipt. `FX`. |
| FM01 `apply_or_restore_target(aggregate,r_ref)` | C17 → O12 | Exact valid R on activation session; derives witness, target rows/images, ciphertext/decryption/conversion/payload, generation and lineage. Atomically mutates target and writes RP06,RP07,RP09,RS09,RS20 and the FW04 work result. It never writes RP08. `FX`. |
| FV01 `verify_mutation(m_ref,verification_attempt_id)` | C18 → O13 | Exact M/attempt; derives target image, generation, lineage and terminal state. UNABLE writes RP10/RP11 and leaves RP14 absent. MATCH writes RP10/RP15/RP14; MISMATCH writes RP10/RP12/RP14; TERMINAL_FAILURE writes RP10/RP13/RP14. Each branch writes its FW04 work result atomically. `FX`. |

### 5.5 Closure, fence, activation, and status

| ID / logical function | Allow → owner | Caller input; derived operands; effects/result |
|---|---|---|
| FC01 `create_closure_case(source_descriptors,reader_binding,target)` | C20 → O14 | Exact source/registry descriptors and expected image; derives authenticated closure, attestation, policy, clock and expiry. Atomically writes RC05 and initializes RC06 as explicit ABSENT. `FX`. |
| FC02 `reserve_closure_attempt(case,request_id)` | C20 → O14 | Case/request; derives next ordinal, limits, exact RESERVATION sample and deadline. Atomically writes RC07, RC08=`UNCLAIMED(0)`, and RC09, or returns in-progress/refusal with no ordinal consumed. `FX`. |
| FC03 `claim_closure_attempt(case,ordinal,incarnation)` | C20 → O14 | Exact attempt/incarnation; derives qualified sample, lease generation/token/deadline. Writes RC08,RC09. `FX`. |
| FC04 `take_over_closure_attempt(case,ordinal,invalidation)` | C20 → O14 | Exact current lease and `BC012`; derives fresh pre-expiry sample. Advances the same ordinal’s lease and writes RC09,RC10; never abandons. `FX`. |
| FC05 `finalize_closure_attempt(case,ordinal,lease,observation_input)` | C20 → O14 | Exact claim and typed observed input; derives target image, comparison, FINALIZATION sample, generation and deadlines. Atomically writes RC09, exactly RC11 or RC12, RC13, RC08=`CLOSED`, and optional terminal RC06. The caller cannot supply a comparison verdict. `FX`. |
| FC06 `resolve_expired_closure_attempt(case,ordinal)` | C20 → O14 | No target result; derives tagged-session closure and qualified lower-bound expiry. Atomically writes abandonment RC09/RC12/RC13, RC08=`CLOSED`, and optional exhaustion RC06. `FX`. |
| FF01 `begin_legacy_fence(invocation)` | C21 → O15 | Caller supplies the exact consumed adapter-local invocation and authenticated graph closure, including the exact manifest receipt and approval-digest-keyed exclusion receipts, never a clock sample, U value, duration, or deadline scalar. O15 locks the fixed fence slot and exact proposal, RE24/RS05 attestation, RE19/RS06 policy, RE18/RS17 receipt, RE16/RS07 qualification/deployment partitions, RE22/RS19 role grants, RE23/RS21 writers, and RE21/RS04 clock; derives the complete deadlines and finite transaction bound; revalidates the invocation nonce, incarnation, target, proposal, currentness, validity, graph, receipt keys, receipt byte digests, and compatibility target bridge; and constructs every typed RC21 reference through the named DRPs. After those checks and before any fence change it derives `FenceTemporalAuthorization(FENCE_START)`. It then performs access revocation and stages RC21’s exact `PendingFenceEnvelope` with adoption generation zero and current binding `NONE`, RC23, RC24 and RC22=`ACCESS_REVOKED`. Immediately before commit it revalidates the same locked operands and derives a distinct `FenceTemporalAuthorization(FENCE_COMMIT)`. Either temporal or relational failure rolls back the complete database and external effect, while the local invocation remains spent. Lost acknowledgement exact-reads the invocation digest, realized state, and complete typed pending envelope; it never retries the consumed invocation. FF01 cannot construct BC019, BC020, BC021, RC27, RC28, RC29, or RS08. `FE`. |
| FF02 `record_writer_drain(generation,observation)` | C21 → O15 | Caller supplies the exact generation and requested observation step, never a clock/deadline operand or authoritative zero-writer body. For each external enumeration, cancellation, termination, or drain wait, O15 locks RC21/RC22, the exact pending graph/current handoff state, current attestation/policy/receipt/result/clock/role/writer projections, RC23/RC24, and that pending step; derives one `FenceTemporalAuthorization(FENCE_STEP)` with the step’s exact finite timeout; and retains those locks through the external call and exact outcome recording. Only a complete derived BC017 with zero attributed writers writes RC25 and RC22=`SESSIONS_DRAINED`. Gate failure, a nonzero or unattributed writer, stale state, timeout, equality, overflow, or uncertain outcome performs no newly authorized external step and writes no RC25 or RC22 change. `FE`. |
| FF03 `record_service_disable(generation,service,result)` | C21 → O15 | Caller supplies the exact generation, selected service step, observed outcome, and disable-attestation ordinary body/closure, never a clock/deadline operand. O15 locks RC21/RC22, current attestation/policy/receipt/result/clock/writer projections, RC23/RC24, the continuously closed drain barrier, and that exact pending service; derives one `FenceTemporalAuthorization(FENCE_STEP)` using the service step’s finite timeout; and retains those locks through external disable and validation of the supplied attestation. A valid outcome atomically preserves the ordinary attestation body and writes derived BC018/RC26. Gate failure, wrong service or attestation, drift, timeout, equality, overflow, or uncertainty authorizes no new disable and writes no body or RC26. `FE`. |
| FF04 `finalize_legacy_fence(generation)` | C21 → O15 | Locks RC21 and derives its exact pending graph plus complete admission/ACL/drain/service evidence. It constructs BC019/RC27, then BC020 and the corresponding completed RC28 graph binding from RC21 plus RC27. Every reference-valued destination uses the named RC28 DRP and every scalar uses the named RC28 PFC; the decoded BC020 target, attestation, digests, generations, session, epoch, capability, and authority are exact equality operands rather than caller choices. It writes RC27,RC28,RS08 and RC22=`FENCE_ACTIVE` atomically; RC21 remains unchanged with binding `NONE`. `FX`. |
| FF05 `ADOPT_ACTIVE_FENCE(mode,cas,final_manifest,approval,epoch_binding)` | C15,O09 → O15 | Caller supplies exact `FenceAdoptionCAS`, complete canonical manifest graph/closure with its exact manifest receipt and approval-digest-keyed exclusion receipts, approval, and reserved epoch/attestation/proposal/capability binding, never a clock sample, U value, or deadline scalar. O15 locks the fixed fence slot, exact FENCE_ACTIVE row/current handoff/persistent fence and supporting admission/ACL/drain/service rows, epoch selectors/proposal, attestation, current policy, receipt and complete qualification/deployment PASS partitions, and clock envelope. It recomputes every graph/evidence/receipt digest, proves the compatibility target bridge and every typed reference equality, proves the barrier continuously closed, derives the complete deadlines, and only after all revalidation takes the fresh sample for `FenceTemporalAuthorization(ADOPTION)` and `U_adopt`. C15 may invoke only `SAME_EPOCH`: it requires the existing exact RESERVED_FENCED row and attestation and leaves both unchanged. O09 may invoke only `LATER_EPOCH`, only as a nested call from FAD02 while its new RE24/RX01/RX02 rows and selectors are still uncommitted; FF05 then writes the field-complete RC29 through its named DRP/PFC records and advances RS08 inside that outer transaction and cannot commit separately. Both modes construct the exact contiguous `BC021`, perform no external fence, stage, target, lineage, or activation effect, and return provisional applied, exact-current replay, `SUPERSEDED_BINDING`, or conflict. Exact-current replay verifies every existing RC29/RS08 member without resampling. Temporal, reference, receipt, or relational refusal writes no adoption or outer success effect; a nested FAD02 may select only its RE25-only failure branch afterward. `FX`. |
| FX01 `activate_reserved_epoch(proposal,capability)` | C17 → O16 | Exact proposal and clear capability on the bound session are the only caller operands. It locks RS08, follows `BC020→RC28` or `BC021→RC29`, locks the complete RC28/RC29 chain and supporting RC21/RC23–RC27 rows, and derives `ActivationManifestGraphProjection` only from that completed current binding. It resolves every BM060 member through FB02 and revalidates the complete handoff, fence, manifest, attestation, policy/results, live bindings, target, epoch, predecessor and selectors. Pending RC21 alone is an invariant refusal. Only after all those checks it samples the protected monotonic clock, derives and validates `CutoverClockObservation`, and invokes IA07 and IA08. It then atomically writes RC01–RC04,RC14–RC20,RP08,RX03 including that observation,RX05, initializes RS20, changes RX02 to ACTIVE, advances RS01, and clears RS02/RS03/RS18. RS09 remains ABSENT until the first M. Identical already-current replay returns RX03’s stored observation without sampling; any missing completed binding, changed binding, stale selector, time failure, equality or overflow writes nothing. `FX`. |
| FX02 `abandon_reserved_epoch(epoch,transaction_identity)` | C19 → O16 | Exact reserved epoch and conclusive noncommit/inadmissibility; derives selectors and attestation currentness. Atomically writes RX04, changes RX02 to `ABANDONED_FENCED`, clears RS02/RS03/RS18, and invokes IA09 to clear RS05. `FX`. |
| FS01 `read_publication_status(key)` | C22 → O17 | Exact aggregate/work/epoch key; reads RA,RW,RP,RX and RS projections. No writes, clocks, authority or private bytes. `FR`. |
| FS02 `read_compatibility_status(key)` | C22 → O17 | Exact cutover/case/fence key; reads RC projections and safe identities only. No target observation or private content. `FR`. |
| FS03 `read_schema_status()` | C22 → O17 | Reads safe RM01–RM06 identities, exact/unclassified state tags, refs, digests, remediation outcomes, and failure categories. It does not expose manifest bytes, write metadata, or perform runtime migration. `FR`. |

For FF01–FF05, “complete deadlines” means FD001–FD007 and always includes the locked RS06→RE19/BS048 `valid_until_unix_ns`; O15 has only the column-limited read/lock projection needed to derive that member. For FX01, O16 locks the same RS06/RE19 identity, includes FD001 in `CutoverClockObservation`, and retains that lock through the complete activation commit. C21, C15, C17, adapters, and nested owners cannot supply or replace the policy deadline. Equality with FD001, arithmetic failure, missing/noncurrent policy, or policy drift returns the operation’s specified no-effect refusal.

## 6. Authorization and ownership

O02–O18 are `NOLOGIN`, `NOINHERIT`, cannot be assumed, and own only the listed relations or functions. O01 is also `NOLOGIN` and `NOINHERIT`, but C23 may assume it only inside the separately controlled deployment path described in §8; no runtime, adapter, or other principal may do so. Caller roles otherwise have no owner membership or `SET ROLE` path and receive schema `USAGE` plus only the callable cells above.

Runtime callers receive no direct relation, sequence, large-object, type-creation, language, schema-creation, or generic body-listing privilege. `PUBLIC` receives no schema or function privileges, including default function execution.

Cross-owner access is limited to function-owner needs:

- O03–O15 and O18 may execute FB01 only at the insertion sites in §5.2.1. O03–O16 and O18 may execute FB02 only at its listed resolution sites. Every login, adapter, O17, and unlisted owner is denied; no caller receives RB01 DML or body-listing access.
- O04 may execute only IA01; O06 only IA02; O09 only IA03, IA05, IA06, IA10 and FF05 LATER_EPOCH; O10 only IA04; and O16 only IA07–IA09. Every other principal is denied each routine.
- O11 and O12 can execute FA07.
- O06, O09, O11, O12, and O16 can execute FE11.
- O10, O11, O12, and O13 can execute FW04.
- O06 alone inserts RE17 and RE18 and has no write path to RS17. O09 owns RS17 and may replace it only through FAD02’s TargetSurfaceKey and exact CAS; O09 has read-only access to the exact RE17/RE18 and tier-result records needed by that call and cannot create or alter a class result or receipt.
- C15 can execute FF05 only in SAME_EPOCH mode. O09 can execute it only as FAD02’s nested LATER_EPOCH step. FF05’s O15 owner retains all RC29/RS08 DML; neither C15 nor O09 receives direct DML on those relations. Its caller/mode check, complete CAS, and no-separate-commit behavior are protected-schema obligations.
- O09 receives exact read/lock access to RS01,RS16,RS18,RX01,RX02 needed to derive and recheck FAD02/FAD04 inputs, but no O16 DML. For FX01, O16 receives column-limited read/lock access to RS08, its tag-selected RC28-or-RC29 completed binding and chain, and RC21/RC23–RC27 supporting rows sufficient to construct `ActivationManifestGraphProjection`, plus the already listed attestation, policy/result, lineage and selector projections. It receives no O15/O18/O12/O09 DML, cannot list another fence or graph, cannot treat RC21 as a completed binding, and cannot invoke an adoption mutation.
- O15 receives column-limited read/lock access only to the RE18–RE24, RS04–RS08, RS17, RS19, RS21, RX01–RX02, RS02–RS03 and RS18 projections explicitly required by FF01–FF05, including exact `FenceAuthorityDeadlineSet` and `FenceTemporalAuthorization` derivation. O15 receives no evidence, policy, admission, epoch, or activation DML and cannot call their mutation interfaces. C21, C15, and O09 receive no direct projection access and cannot supply a protected clock sample, duration, deadline set, or U value.
- O09 has no RM01–RM06 relation privilege. It may execute IA10 only from FAD02 and receives only `SchemaAdmissionProjection`; O01 retains every RM lock and read. C15, all other owners, every login, and PUBLIC are denied IA10.
- FB01–FB02 and IA01–IA10 preserve relation ownership: only the callee owner reads or writes its relations. Their outer caller receives only the exact EXECUTE cell, not table DML, and each callee verifies the outer transaction’s exact staged facts or calling context before changing or projecting state.
- O11–O16 receive read/lock access only to the exact relation IDs listed as database-derived operands of their functions.
- O12 and O13 receive the exact target-column privileges needed by `FM01` and `FV01`; O14 receives target read/lock only for `FC05`; O15 receives only the external fence operation’s named role/service privileges.
- O17 receives safe read projections, never underlying private bytes or mutation access.
- C23 may assume O01 only through the separately controlled deployment path. C24 and C25 have no protected-schema mutation function.

Definer functions use a fixed safe search path and schema-qualified trusted objects.

The default-deny complement preserves the accepted reciprocal boundaries: authority principals cannot combine lifecycle roles; the evidence producer can register findings but cannot apply dispositions or change verdicts, while the evidence authority can apply a registered finding but cannot create, alter, or select it; qualification cannot self-accept; admission cannot qualify or activate; publication cannot mutate; mutation cannot create `J/P/R`; verification cannot mutate; closure cannot create authority or mutate target; fence cannot adopt or activate; private registrar/reviewer/exporter remain one-way; status is nonmutating.

## 7. Source adapters and commit acknowledgement

There is no generic operation dispatcher. External adapters expose exactly these C-role methods:

- C01 preimage constructor: `FB03`.
- C02 grant issuer: `FA01`; C03 plan issuer: `FA03`; C04 approver: `FA04`; C05 authorizer: `FA05`; C06 revoker: `FA02`,`FA06`.
- C07 plan authority: `FE01–FE03`; C08 evidence producer: `FE04–FE08`; C09 evidence authority: `FE09–FE10`.
- C10 qualification submitter: `FQ01–FQ02`.
- C11 private registrar: `FPV01`; C12 private reviewer: `FPV02–FPV03`; C13 public exporter: `FPV04`.
- C14 deployment-policy authority: `FDP01`.
- C15 admission author: `FAD01–FAD04` and FF05’s SAME_EPOCH form. An occupied-fence later-epoch admission is one FAD02 call; the adapter never performs a second FF05 transaction.
- C16 publication adapter: `FW01–FW03`,`FW08`,`FJ01–FJ03`.
- C17 continuity client: `FW01–FW03`,`FW08`,`FM01`,`FX01`.
- C18 verification adapter: `FW01–FW03`,`FW08`,`FV01`.
- C19 recovery adapter: `FW01–FW03`,`FW05–FW08`,`FX02`.
- C20 closure adapter: `FC01–FC06`; C21 fence adapter: `FF01–FF04`; C22 status observer: `FS01–FS03`.

`FB01`, `FB02`, `FA07`, `FE11`, `FW04`, FF05’s LATER_EPOCH form, and IA01–IA10 are owner-internal only. C23 uses the explicit deployment path in §8 rather than a runtime adapter. C24 and C25 have no protected mutation method.

The adapter passes source-derived values, exact bytes/references, and the exact `ResolvedOrdinaryBodyClosure` for the SUPPLIED MRR/MRE records required by §5.2.1. The function derives every DERIVED and DATABASE record. In particular FX01 accepts no manifest graph or cutover-clock sample: it constructs `ActivationManifestGraphProjection` from the locked protected binding and obtains the final sample after revalidation. The codec, resolver, current verifier, and historical dispatcher remain separate lower-level source interfaces.

Before FF01, the C21 adapter performs the source-owned read-only pre-fence gate, resolves FD001–FD007 including the current BS048 policy expiry, derives `U_prefence`, issues one local incarnation/nonce-bound invocation, exclusively consumes it, revalidates the same current authority/deadline identities, and derives `U_consume`; it retains the local effect-attempt lock through FF01 commit or rollback. Those local gates create no PostgreSQL fact and are not operands that FF01 trusts as its own time result. FF01 independently derives all seven deadline classes from locked rows, samples for `U_fence_start`, and repeats both derivation/currentness checks immediately before its distinct `U_fence_commit` sample. Each later FF02/FF03 external step obtains a new protected `FENCE_STEP` gate from a freshly locked FD001–FD007 set in the transaction that retains the currentness locks through its exact outcome recording; no earlier invocation or sample is a continuation receipt. A new FF05 adoption independently derives the same set and samples for `U_adopt` only after its complete locked revalidation. FX01 independently includes FD001–FD007 in its post-revalidation cutover observation. The adapter may provide canonical graph bodies and external outcome evidence, but never any protected sample, deadline set, duration projection, or U value.

A function return is provisional. The adapter owns connection acquisition, transaction setup, the single protected entrypoint call, durability-setting verification, `COMMIT`, and acknowledgement. An entrypoint may invoke only FB01/FB02, IA01–IA10, FW04, or FF05 at the exact sites above; every nested routine shares the transaction and cannot commit independently. No adapter exposes a shared-body, IA, FA07, FE11, or FW04 method or reports its provisional result as durable success. The adapter reports durable success only after acknowledgement. Lost acknowledgement yields an ambiguous adapter result and exact read through `FW08`, the stage function’s replay path, or the matching status/read function. It never promotes a provisional return or repeats one external effect. Transaction orchestration and lock order remain with [issue 107](https://github.com/nisavid/agents/issues/107).

## 8. Repeatable migration model

Deployment metadata is not journal authority, operational approval, evidence, or a bootstrap attestation. Runtime never auto-migrates.

The accepted `protected_schema_digest` is the `ActiveSurfaceDigest` recorded by RM01 for an interface generation. It is SHA-256 over exact `ActiveSurfaceManifest/v1` canonical bytes. This manifest is nonauthorizing deployment metadata, not an RB01 body, successor journal fact, bootstrap attestation, or migration approval.

`ActiveSurfaceManifest/v1` has this exact root set:

1. the locked protected schema and database binding;
2. every adapter entrypoint admitted for `route_generation`;
3. every owner-internal routine statically reachable from those entrypoints;
4. every relation, current anchor, type, constraint, index, trigger, rule, policy, sequence definition, and other object those routines read, lock, or write;
5. every owner, caller, role membership, object/column ACL, default privilege, language, extension, or server primitive required to make those calls possible; and
6. the transitive dependency closure of members 1–5.

The closed `schema_object_class` registry is:

```text
DATABASE_BINDING, SCHEMA, RELATION, COLUMN, SEQUENCE, TYPE,
CONSTRAINT, INDEX, ROUTINE, TRIGGER, RULE, ROW_SECURITY_POLICY,
OPERATOR, CAST, COLLATION, CONVERSION, ACCESS_METHOD,
OPERATOR_CLASS, OPERATOR_FAMILY, LANGUAGE, EXTENSION,
FOREIGN_DATA_WRAPPER, FOREIGN_SERVER, USER_MAPPING,
STATISTICS_OBJECT, TEXT_SEARCH_OBJECT, LARGE_OBJECT_ACL,
ROLE, ROLE_MEMBERSHIP, OBJECT_ACL, DEFAULT_PRIVILEGE,
SERVER_PRIMITIVE
```

An unclassified reachable object fails manifest construction. Dynamic SQL in a protected routine is inadmissible unless every possible referenced object is represented by a closed, exact dependency edge in the manifest.

RM05 stores this exact envelope:

```text
SchemaManifestEnvelope/v1 := {
  manifest_kind: ACTIVE_SURFACE | CATALOG,
  manifest_version: literal "1",
  canonical_encoding: literal
    "hindsight-postgresql-schema-manifest-json/v1",
  extraction_contract: literal
    "hindsight-postgresql-protected-schema-manifest/v1",
  accepted_contract_revision: literal
    "90108b516f5a1c460980a93670348f6e228124f2",
  postgresql_version_num: U,
  postgresql_build_identity: PostgresqlBuildIdentity/v1,
  postgresql_build_digest: Digest,
  target_database_identity: BodyRef<BS050>,
  interface_generation: U,
  route_generation: O<U>,
  phase_instance_id: O<B>,
  root_identities: S<SchemaObjectIdentity/v1>,
  objects: S<SchemaObjectDescriptor/v1>,
  dependency_edges: S<SchemaDependencyDescriptor/v1>
}

SchemaObjectDescriptor/v1 := {
  class: schema_object_class,
  logical_identity: SchemaObjectIdentity/v1,
  definition: SchemaObjectDefinition/v1,
  owner: O<RoleIdentity/v1>
}

SchemaDependencyDescriptor/v1 := {
  dependent: SchemaObjectIdentity/v1,
  dependent_subobject_ordinal: O<U>,
  referenced: SchemaObjectIdentity/v1,
  referenced_subobject_ordinal: O<U>,
  dependency_kind: NORMAL | AUTO | INTERNAL | EXTENSION |
                   PARTITION_PRIMARY | PARTITION_SECONDARY |
                   PIN | ACL | OWNERSHIP | EXECUTION
}

PostgresqlBuildIdentity/v1 := {
  server_version_num: U,
  server_version_setting_bytes: B,
  version_function_bytes: B,
  catalog_version_no: U,
  server_executable_byte_length: U,
  server_executable_sha256: Digest,
  compile_time_parameters: {
    block_size: U,
    integer_datetimes: true | false,
    max_function_args: U,
    max_identifier_length: U,
    max_index_keys: U,
    segment_size: U,
    wal_block_size: U
  }
}
```

`hindsight-postgresql-schema-manifest-json/v1` is a closed encoding, not an implementation choice:

- The envelope and every nested record are UTF-8 JSON with no insignificant whitespace. Object members occur in the grammar order printed here; missing, extra, duplicated, or reordered members are invalid.
- Every tagged union uses exactly `{"tag":"TAG","value":PAYLOAD}` with members in that order. An empty variant uses `{}` as `PAYLOAD`. No union may substitute `class`, `kind`, an unwrapped string, a single-key object, or another discriminator.
- Fixed tags, enum values, and member names are the displayed ASCII bytes. Simple enums are JSON strings, never tagged unions. Arbitrary server bytes use unpadded RFC 4648 base64url JSON strings. Digests are 64-character lowercase hexadecimal JSON strings.
- A nonnegative integer is the JSON string `"0"` or a string matching `[1-9][0-9]*`; a signed integer is `"0"` or `-?[1-9][0-9]*`. JSON numbers, exponent notation, leading zeroes, and negative zero are forbidden.
- Optional values encode only as `{"tag":"NONE","value":{}}` or `{"tag":"SOME","value":…}`. A `Q<T>` JSON array is an ordered multiset: it preserves every element in its exact source ordinal, including byte-identical values at distinct ordinals. A missing, repeated, negative, noncontiguous, or ambiguously derived ordinal is invalid. No `Q<T>` is sorted or deduplicated. The closed ordinal sources are listed below. A collection without a normative order is `S<T>`, rejects duplicate complete member bytes, and sorts by unsigned lexicographic comparison of each member's complete canonical JSON bytes.
- `root_identities`, `objects`, and `dependency_edges` are sets. `objects` additionally rejects two descriptors with the same complete logical-identity bytes even if their definitions differ. Dependency descriptors are ordered by their complete canonical bytes, which include both subobject ordinals; exact duplicate edges reject, while edges differing in either ordinal are distinct and cannot tie.
- All manifest strings are fixed ASCII tags, hexadecimal, decimal, or base64url, so no implementation-defined Unicode or escape normalization exists. The complete envelope ends with exactly one LF and contains no other trailing byte.

`postgresql_build_digest` is SHA-256 over the standalone JSON object `PostgresqlBuildIdentity/v1`, with exactly the displayed seven top-level members in order, the seven displayed `compile_time_parameters` members in order, the encodings above, and one terminal LF. It has no union wrapper or additional kind/version field. The nested copy inside a manifest has identical object bytes without an interior LF. The executable fields hash the byte-for-byte executable backing the running postmaster after symlink resolution; its path, ownership, timestamps, and signature metadata are excluded. The version strings are captured as exact server-encoding bytes. An implementation unable to bind the running executable or any field fails manifest construction. Extension and external provider binaries are bound separately by their object descriptors. This grammar identifies a build; admission of that version/build remains support-profile and qualification work.

The 32 class projections are summarized here; the closed typed grammar following the table is normative:

| Class | Stable logical identity | Complete definition projection |
|---|---|---|
| DATABASE_BINDING | database identity ref + database-name bytes | encoding, collation, ctype, locale provider, ICU locale/rules/version, default tablespace |
| SCHEMA | database identity + schema-name bytes | owner and schema ACL dependency |
| RELATION | schema identity + relation-name bytes + relation kind | persistence, access method, typed-table ref, partition parent/key/bound, replica identity, row-security flags, reloptions, view query tree or NONE |
| COLUMN | relation identity + physical ordinal | name bytes or dropped marker, type, typmod, collation, not-null, default tree, generated/identity mode, storage, compression, inheritance/local flags |
| SEQUENCE | relation identity | data type, start, increment, minimum, maximum, cache, cycle, ownership ref; current counter excluded |
| TYPE | schema + type-name bytes + type class | length/by-value/alignment/storage/category/preferred/delimiter, element/array/base/composite refs, domain default/not-null/constraints, ordered enum labels, range/multirange fields |
| CONSTRAINT | parent identity + constraint-name bytes + type | ordered columns, referenced relation/columns, match/update/delete actions, deferrable/deferred, validated/no-inherit, expression tree, exclusion operators |
| INDEX | schema + index-name bytes | parent relation, access method, ordered keys/expressions, collations, operator classes/options, include columns, predicate tree, unique/primary/exclusion/immediate/valid/ready/live/replica flags |
| ROUTINE | schema + name bytes + routine kind + identity argument types | return and argument types/modes/names/default trees, language, exact source-or-symbol bytes, binary identity, volatility, strict/security/leakproof/parallel flags, support routine, cost/rows, fixed configuration |
| TRIGGER | relation identity + trigger-name bytes | function, timing, events, orientation, enabled state, constraint target, deferrability, ordered columns, WHEN tree, exact argument bytes, transition names |
| RULE | relation identity + rule-name bytes | event, instead/enabled flags, qualifier tree, ordered action trees |
| ROW_SECURITY_POLICY | relation identity + policy-name bytes | command, permissive flag, sorted role identities, USING tree, WITH CHECK tree |
| OPERATOR | schema + name bytes + left/right type identities | owner, implementation routine, result type, commutator, negator, restriction/join estimators, hash/merge flags |
| CAST | source type + target type | function, context, method |
| COLLATION | schema + collation-name bytes | owner, provider, deterministic flag, encoding, locale fields, rules, recorded version |
| CONVERSION | schema + conversion-name bytes | owner, source/destination encoding, routine, default flag |
| ACCESS_METHOD | access-method-name bytes + method type | handler routine |
| OPERATOR_CLASS | access method + schema + class-name bytes | owner, input/key types, family, default flag, sorted operator and support-function members |
| OPERATOR_FAMILY | access method + schema + family-name bytes | owner, sorted operator and support-function members |
| LANGUAGE | language-name bytes | owner, trusted flag, handler, inline handler, validator |
| EXTENSION | extension-name bytes | owner, version bytes, schema, relocatable flag, sorted member identities and configuration relations/conditions |
| FOREIGN_DATA_WRAPPER | wrapper-name bytes | owner, handler, validator, sorted nonsecret options |
| FOREIGN_SERVER | server-name bytes | owner, wrapper, type/version bytes, sorted nonsecret options |
| USER_MAPPING | server identity + role/PUBLIC identity | sorted nonsecret options |
| STATISTICS_OBJECT | schema + statistics-name bytes | owner, relation, ordered columns/expressions, sorted kinds, target |
| TEXT_SEARCH_OBJECT | subtype + schema + object-name bytes | subtype-specific parser/template/dictionary/configuration routines/options and ordered mappings |
| LARGE_OBJECT_ACL | database identity + database-local large-object OID decimal | owner, content byte length and digest, sorted ACL entries; no bytes |
| ROLE | role-name bytes | superuser, inherit, create-role, create-database, login, replication, bypass-RLS, connection limit; credential verifier excluded |
| ROLE_MEMBERSHIP | granted role + member role + grantor role | inherit, SET, ADMIN options |
| OBJECT_ACL | object identity + grantor + grantee + privilege | grantable flag and exact column/object scope |
| DEFAULT_PRIVILEGE | definer + schema-or-NONE + object class + grantee + privilege | grantable flag |
| SERVER_PRIMITIVE | primitive class + schema/name/signature + provider | admitted PostgreSQL version/build and extension identity/version or BUILT_IN |

The manifest scalar aliases are `B=unpadded-base64url(exact bytes)`, `U=canonical nonnegative decimal string`, `I=canonical signed decimal string`, `P=positive U`, `O<T>=the exact optional wrapper above`, `Q<T>=ordered JSON array`, `S<T>=canonical JSON set array`, and `R<C>=the complete canonical SchemaObjectIdentity/v1 wrapper tagged C`. `BodyRef<BS050>` is exactly `{"kind":"hindsight-postgresql-evidence-identity","version":"1","digest":"<Digest>"}` in that member order.

`RoleIdentity/v1` is the unwrapped record `{"role_name":"<B>"}` with a nonempty role-name byte string. `GranteeIdentity/v1` is exactly `{"tag":"ROLE","value":<RoleIdentity/v1>}` or `{"tag":"PUBLIC","value":{}}`; PUBLIC is never encoded as a role name.

`AclTargetIdentity/v1` is one complete `SchemaObjectIdentity/v1` wrapper whose tag is exactly one of:

```text
DATABASE_BINDING, SCHEMA, RELATION, SEQUENCE, TYPE, ROUTINE, LANGUAGE,
FOREIGN_DATA_WRAPPER, FOREIGN_SERVER, LARGE_OBJECT_ACL
```

Column privilege scope is represented only by `AclScope/v1 COLUMN`; `COLUMN` is therefore not an ACL target tag.

`DependencyReferencedIdentity/v1` is one complete `SchemaObjectIdentity/v1` wrapper whose tag is exactly one of:

```text
DATABASE_BINDING, SCHEMA, RELATION, COLUMN, SEQUENCE, TYPE, CONSTRAINT,
INDEX, ROUTINE, TRIGGER, RULE, ROW_SECURITY_POLICY, OPERATOR, CAST,
COLLATION, CONVERSION, ACCESS_METHOD, OPERATOR_CLASS, OPERATOR_FAMILY,
LANGUAGE, EXTENSION, FOREIGN_DATA_WRAPPER, FOREIGN_SERVER, USER_MAPPING,
STATISTICS_OBJECT, TEXT_SEARCH_OBJECT, LARGE_OBJECT_ACL, ROLE,
ROLE_MEMBERSHIP, SERVER_PRIMITIVE
```

`OBJECT_ACL` and `DEFAULT_PRIVILEGE` are not dependency-reference targets. Both restricted identities use the selected class’s literal `{"tag":"TAG","value":PAYLOAD}` representation directly; they add no outer wrapper.

The complete `Q<T>` ordinal registry is:

| Ordered field | Exact source ordinal and validation |
|---|---|
| `ROUTINE` identity `identity_argument_types` | zero-based position in `proargtypes`; repeated type identities are retained |
| `IndexKey.operator_class_options` | zero-based option-array subscript for that key |
| `TextSearchMapping.dictionaries` | ascending `mapseqno`; repeated dictionary identities at distinct sequence numbers are retained |
| `RELATION.partition_key` | zero-based position `k` in `pg_partitioned_table.partattrs`; `partattrs`, `partclass`, and `partcollation` must each have `partnatts` entries. A nonzero `partattrs[k]` emits `PartitionKeySource/v1 COLUMN`; zero emits `EXPRESSION` whose `source_ordinal=k`, with the `j`th `partexprs` node aligned to the `j`th zero in `partattrs`. Missing, extra, or misaligned expression nodes reject. |
| `TYPE.enum_labels` | ascending numeric `enumsortorder`, then label bytes; duplicate numeric order is invalid |
| `CONSTRAINT.ordered_columns` | zero-based `conkey` subscript |
| `CONSTRAINT.referenced_columns` | zero-based `confkey` subscript aligned one-to-one with `conkey`; unequal lengths are invalid |
| `CONSTRAINT.exclusion_operators` | zero-based `conexclop` subscript aligned one-to-one with `conkey`; unequal lengths are invalid |
| `INDEX.keys` | zero-based key position before `indnkeyatts` |
| `INDEX.include_columns` | zero-based include position after `indnkeyatts` |
| `ROUTINE.arguments` | zero-based declared argument position from `proallargtypes` when present, otherwise `proargtypes`, with modes, names, and defaults aligned |
| `TRIGGER.columns` | zero-based `tgattr` subscript |
| `TRIGGER.arguments` | zero-based member position in the exact NUL-delimited `tgargs` bytes |
| `RULE.actions` | zero-based action position in the stored action node list |
| `EXTENSION.configuration_relations` | zero-based `extconfig` subscript |
| `EXTENSION.configuration_conditions` | the same zero-based subscript as `extconfig`; unequal array lengths are invalid |
| `STATISTICS_OBJECT.columns` | zero-based `stxkeys` subscript |
| `STATISTICS_OBJECT.expressions` | zero-based expression position in the stored expression node list |

Every `Q<T>` permits equal canonical values at different valid ordinals and preserves them. This table is exhaustive. `TYPE.domain_constraints` and text-search configuration mappings are unordered identity sets and therefore use `S<T>`, not `Q<T>`.

The complete manifest tagged-union registry is:

```text
Optional<T>              tags NONE {} | SOME T
SchemaObjectIdentity/v1  the 32 schema_object_class tags below
SchemaObjectDefinition/v1 the same 32 class tags below
GranteeIdentity/v1       tags ROLE RoleIdentity/v1 | PUBLIC {}
AclTargetIdentity/v1     the ten ACL-target tags listed above
DependencyReferencedIdentity/v1 the thirty dependency-target tags listed above
IndexKeySource/v1        tags COLUMN R<COLUMN> |
                              EXPRESSION CatalogExpression/v1
PartitionKeySource/v1    tags COLUMN R<COLUMN> |
                              EXPRESSION CatalogExpression/v1
AclScope/v1              tags OBJECT {} | COLUMN R<COLUMN>
TextSearchDefinition/v1  tags PARSER | TEMPLATE | DICTIONARY |
                              CONFIGURATION with the payloads below
```

No other value in the manifest is a tagged union. The descriptor's scalar `class`, identity tag, and definition tag must be byte-identical. For `TEXT_SEARCH_OBJECT`, its identity `subtype` must also equal the nested `TextSearchDefinition/v1` tag.

`SchemaObjectIdentity/v1` is the following closed tagged union. Each line `TAG {fields}` encodes literally as `{"tag":"TAG","value":{fields}}`; payload fields occur in the shown order:

```text
DATABASE_BINDING {database_identity:BodyRef<BS050>,database_name:B}
SCHEMA {database:R<DATABASE_BINDING>,schema_name:B}
RELATION {schema:R<SCHEMA>,relation_name:B,relation_kind:
          TABLE|PARTITIONED_TABLE|VIEW|MATERIALIZED_VIEW|
          FOREIGN_TABLE|COMPOSITE_RELATION|TOAST_TABLE}
COLUMN {relation:R<RELATION>,physical_ordinal:P}
SEQUENCE {schema:R<SCHEMA>,sequence_name:B}
TYPE {schema:R<SCHEMA>,type_name:B,type_class:
      BASE|COMPOSITE|DOMAIN|ENUM|PSEUDO|RANGE|MULTIRANGE}
CONSTRAINT {parent:R<RELATION>|R<TYPE>,constraint_name:B,constraint_type_code:B}
INDEX {schema:R<SCHEMA>,index_name:B}
ROUTINE {schema:R<SCHEMA>,routine_name:B,routine_kind:
         FUNCTION|PROCEDURE|AGGREGATE|WINDOW,
         identity_argument_types:Q<R<TYPE>>}
TRIGGER {relation:R<RELATION>,trigger_name:B}
RULE {relation:R<RELATION>,rule_name:B}
ROW_SECURITY_POLICY {relation:R<RELATION>,policy_name:B}
OPERATOR {schema:R<SCHEMA>,operator_name:B,
          left_type:O<R<TYPE>>,right_type:O<R<TYPE>>}
CAST {source_type:R<TYPE>,target_type:R<TYPE>}
COLLATION {schema:R<SCHEMA>,collation_name:B}
CONVERSION {schema:R<SCHEMA>,conversion_name:B}
ACCESS_METHOD {database:R<DATABASE_BINDING>,access_method_name:B,method_type_code:B}
OPERATOR_CLASS {access_method:R<ACCESS_METHOD>,schema:R<SCHEMA>,class_name:B}
OPERATOR_FAMILY {access_method:R<ACCESS_METHOD>,schema:R<SCHEMA>,family_name:B}
LANGUAGE {database:R<DATABASE_BINDING>,language_name:B}
EXTENSION {database:R<DATABASE_BINDING>,extension_name:B}
FOREIGN_DATA_WRAPPER {database:R<DATABASE_BINDING>,wrapper_name:B}
FOREIGN_SERVER {database:R<DATABASE_BINDING>,server_name:B}
USER_MAPPING {server:R<FOREIGN_SERVER>,grantee:GranteeIdentity/v1}
STATISTICS_OBJECT {schema:R<SCHEMA>,statistics_name:B}
TEXT_SEARCH_OBJECT {subtype:PARSER|TEMPLATE|DICTIONARY|CONFIGURATION,
                    schema:R<SCHEMA>,object_name:B}
LARGE_OBJECT_ACL {database:R<DATABASE_BINDING>,large_object_oid:U}
ROLE {role:RoleIdentity/v1}
ROLE_MEMBERSHIP {granted_role:RoleIdentity/v1,member_role:RoleIdentity/v1,
                 grantor_role:RoleIdentity/v1}
OBJECT_ACL {target:AclTargetIdentity/v1,grantor:RoleIdentity/v1,
            grantee:GranteeIdentity/v1,privilege_code:B,
            scope:AclScope/v1}
DEFAULT_PRIVILEGE {database:R<DATABASE_BINDING>,definer:RoleIdentity/v1,
                   schema:O<R<SCHEMA>>,object_class_code:B,
                   grantee:GranteeIdentity/v1,privilege_code:B}
SERVER_PRIMITIVE {database:R<DATABASE_BINDING>,primitive_class:B,
                  schema:O<R<SCHEMA>>,primitive_name:B,signature:B,provider:B}
```

Auxiliary definition records are:

```text
CatalogOption/v1 {name:B,value:B}
ProviderBinary/v1 {byte_length:U,sha256:Digest}
RoutineArgument/v1 {mode_code:B,name:O<B>,type:R<TYPE>,
                    default_expression:O<CatalogExpression/v1>}
IndexKey/v1 {
  source:IndexKeySource/v1,
  collation:O<R<COLLATION>>,operator_class:O<R<OPERATOR_CLASS>>,
  operator_class_options:Q<B>,ordering_code:B,nulls_order_code:B
}
PartitionKey/v1 {
  source:PartitionKeySource/v1,
  collation:O<R<COLLATION>>,
  operator_class:R<OPERATOR_CLASS>
}
OperatorMember/v1 {strategy_number:U,left_type:R<TYPE>,
                   right_type:R<TYPE>,operator:R<OPERATOR>}
SupportMember/v1 {support_number:U,left_type:R<TYPE>,
                  right_type:R<TYPE>,routine:R<ROUTINE>}
TextSearchMapping/v1 {token_type:U,dictionaries:Q<R<TEXT_SEARCH_OBJECT>>}
ExpressionDependencyRow/v1 {
  source_catalog:PG_DEPEND|PG_SHDEPEND,
  dependent_catalog_class_oid:U,dependent_object_oid:U,
  dependent_subobject_ordinal:U,
  referenced_catalog_class_oid:U,referenced_object_oid:U,
  referenced_subobject_ordinal:U,dependency_type_code:B,
  referenced_identity:DependencyReferencedIdentity/v1
}
```

`IndexKeySource/v1`, `PartitionKeySource/v1`, and `AclScope/v1` use the literal wrappers in the registry. Both source unions use the complete `R<COLUMN>` object for COLUMN and the complete `CatalogExpression/v1` object for EXPRESSION; AclScope OBJECT has `{}` payload.

`CatalogExpression/v1` is the selected exact alternative to an implementation-defined AST normalizer:

```text
CatalogExpression/v1 := {
  source:
    COLUMN_DEFAULT | COLUMN_GENERATION | DOMAIN_DEFAULT |
    CHECK_CONSTRAINT | EXCLUSION_CONSTRAINT |
    INDEX_EXPRESSION | INDEX_PREDICATE |
    PARTITION_KEY | PARTITION_BOUND | VIEW_QUERY |
    ROUTINE_ARGUMENT_DEFAULT | ROUTINE_SQL_BODY |
    TRIGGER_WHEN | RULE_QUALIFIER | RULE_ACTION |
    POLICY_USING | POLICY_WITH_CHECK | STATISTICS_EXPRESSION,
  owning_object: SchemaObjectIdentity/v1,
  source_ordinal: U,
  raw_pg_node_tree_bytes: B,
  dependency_rows: S<ExpressionDependencyRow/v1>
}
```

`raw_pg_node_tree_bytes` are the complete exact server-encoding bytes of the catalog datum named below. A list-valued datum is repeated byte-for-byte in each logical member record; `source_ordinal` selects the member and never authorizes slicing, deparsing, or re-emitting a node. Singleton sources use ordinal zero. The following 18-row registry is exhaustive:

| Source tag | Exact datum and presence predicate | Exact owning object | `source_ordinal` and alignment | Dependent address set |
|---|---|---|---|---|
| `COLUMN_DEFAULT` | `pg_attrdef.adbin`; owning `pg_attribute.attgenerated` is empty | `COLUMN(adrelid,adnum)` | `0` | `pg_attrdef(oid,0)` |
| `COLUMN_GENERATION` | `pg_attrdef.adbin`; owning `attgenerated` is nonempty | `COLUMN(adrelid,adnum)` | `0` | `pg_attrdef(oid,0)` |
| `DOMAIN_DEFAULT` | nonnull `pg_type.typdefaultbin` with `typtype='d'` | owning `TYPE` | `0` | `pg_type(oid,0)` |
| `CHECK_CONSTRAINT` | nonnull `pg_constraint.conbin` with `contype='c'` | owning `CONSTRAINT` | `0` | `pg_constraint(oid,0)` |
| `EXCLUSION_CONSTRAINT` | complete nonnull `pg_index.indexprs` of `pg_constraint.conindid` with `contype='x'` | owning `CONSTRAINT` | `0`; the datum is the complete expression list, whose nodes align with zero entries in the supporting index’s key portion of `indkey` | `pg_constraint(oid,0)` and `pg_class(conindid,0)` |
| `INDEX_EXPRESSION` | complete `pg_index.indexprs` | owning `INDEX(indexrelid)` | key position `k<indnkeyatts` where `indkey[k]=0`; the `j`th list node aligns with the `j`th zero | `pg_class(indexrelid,0)` |
| `INDEX_PREDICATE` | nonnull `pg_index.indpred` | owning `INDEX(indexrelid)` | `0` | `pg_class(indexrelid,0)` |
| `PARTITION_KEY` | complete `pg_partitioned_table.partexprs` | owning partitioned `RELATION(partrelid)` | partition-key position `k` where `partattrs[k]=0`; the `j`th list node aligns with the `j`th zero | `pg_class(partrelid,0)` |
| `PARTITION_BOUND` | nonnull `pg_class.relpartbound` for a partition | owning child `RELATION` | `0` | `pg_class(oid,0)` |
| `VIEW_QUERY` | complete `pg_rewrite.ev_action` from the unique `_RETURN` rule for the view or materialized view | owning `RELATION(ev_class)` | `0`; the complete action list is one relation-definition value | `pg_rewrite(oid,0)` |
| `ROUTINE_ARGUMENT_DEFAULT` | complete nonnull `pg_proc.proargdefaults` | owning `ROUTINE` | declared argument position: list member `j` maps first to input position `pronargs-pronargdefaults+j`, then to the corresponding declared position among modes `i`, `b`, and `v` in `proallargtypes`; without `proallargtypes`, it is that input position | `pg_proc(oid,0)` |
| `ROUTINE_SQL_BODY` | nonnull `pg_proc.prosqlbody` | owning `ROUTINE` | `0` | `pg_proc(oid,0)` |
| `TRIGGER_WHEN` | nonnull `pg_trigger.tgqual` | owning `TRIGGER` | `0` | `pg_trigger(oid,0)` |
| `RULE_QUALIFIER` | nonnull `pg_rewrite.ev_qual` | owning `RULE` | `0` | `pg_rewrite(oid,0)` |
| `RULE_ACTION` | complete `pg_rewrite.ev_action` | owning `RULE` | zero-based action-list position `j`; every record retains the complete action-list datum | `pg_rewrite(oid,0)` |
| `POLICY_USING` | nonnull `pg_policy.polqual` | owning `ROW_SECURITY_POLICY` | `0` | `pg_policy(oid,0)` |
| `POLICY_WITH_CHECK` | nonnull `pg_policy.polwithcheck` | owning `ROW_SECURITY_POLICY` | `0` | `pg_policy(oid,0)` |
| `STATISTICS_EXPRESSION` | complete nonnull `pg_statistic_ext.stxexprs` | owning `STATISTICS_OBJECT` | zero-based expression-list position `j`; every record retains the complete list datum | `pg_statistic_ext(oid,0)` |

These fields and alignments follow the PostgreSQL 18 catalogs for [`pg_attrdef`](https://www.postgresql.org/docs/18/catalog-pg-attrdef.html), [`pg_attribute`](https://www.postgresql.org/docs/18/catalog-pg-attribute.html), [`pg_type`](https://www.postgresql.org/docs/18/catalog-pg-type.html), [`pg_constraint`](https://www.postgresql.org/docs/18/catalog-pg-constraint.html), [`pg_index`](https://www.postgresql.org/docs/18/catalog-pg-index.html), [`pg_partitioned_table`](https://www.postgresql.org/docs/18/catalog-pg-partitioned-table.html), [`pg_class`](https://www.postgresql.org/docs/18/catalog-pg-class.html), [`pg_rewrite`](https://www.postgresql.org/docs/18/catalog-pg-rewrite.html), [`pg_proc`](https://www.postgresql.org/docs/18/catalog-pg-proc.html), [`pg_trigger`](https://www.postgresql.org/docs/18/catalog-pg-trigger.html), [`pg_policy`](https://www.postgresql.org/docs/18/catalog-pg-policy.html), and [`pg_statistic_ext`](https://www.postgresql.org/docs/18/catalog-pg-statistic-ext.html). A target build whose qualified catalog shape differs requires a new extraction-contract version; an extractor cannot reinterpret this version.

In the dependent-address column, `catalog(oid,subobject)` denotes one exact `pg_depend` dependent address. `dependency_rows` contains exactly every `pg_depend` row matching any listed address and every matching `pg_shdepend` row for the envelope’s target database; a `pg_shdepend` referenced-subobject ordinal is literal zero. Each row preserves its numeric catalog/object/subobject fields and dependency code and resolves its referenced address to exactly one permitted `DependencyReferencedIdentity/v1`. The target database binding makes the omitted `pg_shdepend.dbid` invariant. Missing or extra rows, an unresolved address, a dependency outside the closed identity domain, or disagreement between an expression record and the corresponding object descriptor rejects manifest construction.

Consumer tags are closed: `COLUMN.value_expression` accepts only `COLUMN_DEFAULT|COLUMN_GENERATION`; `TYPE.domain_default` only `DOMAIN_DEFAULT`; `CONSTRAINT.expression` only `CHECK_CONSTRAINT|EXCLUSION_CONSTRAINT`; `INDEX.keys[].source.EXPRESSION` only `INDEX_EXPRESSION`; `INDEX.predicate` only `INDEX_PREDICATE`; `RELATION.partition_key[].source.EXPRESSION` only `PARTITION_KEY`; `RELATION.partition_bound` only `PARTITION_BOUND`; `RELATION.view_query` only `VIEW_QUERY`; `ROUTINE.arguments[].default_expression` only `ROUTINE_ARGUMENT_DEFAULT`; `ROUTINE.sql_body` only `ROUTINE_SQL_BODY`; `TRIGGER.when_expression` only `TRIGGER_WHEN`; `RULE.qualifier` only `RULE_QUALIFIER`; `RULE.actions[]` only `RULE_ACTION`; policy expressions only their corresponding policy tags; and `STATISTICS_OBJECT.expressions[]` only `STATISTICS_EXPRESSION`. Any other tag/consumer pairing rejects.

This registry supplies one deterministic extraction for the exact schema identity required by the accepted [support-profile binding](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7525-L7536) and [deployment admission](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7725-L7742). Installation-local OIDs in raw node bytes and dependency rows are intentional identity inputs; OID churn changes the manifest.

`SchemaObjectDefinition/v1` is the following closed tagged union. Each line `TAG {fields}` uses the same literal tag/value wrapper as identities. `E` means `CatalogExpression/v1`; every field is required unless typed `O<…>`:

```text
DATABASE_BINDING {encoding_code:U,collation_bytes:B,ctype_bytes:B,
  locale_provider_code:B,icu_locale:O<B>,icu_rules:O<B>,
  collation_version:O<B>,default_tablespace_name:B}
SCHEMA {acl_refs:S<R<OBJECT_ACL>>}
RELATION {persistence_code:B,access_method:O<R<ACCESS_METHOD>>,
  tablespace_name:O<B>,typed_table:O<R<TYPE>>,
  partition_parent:O<R<RELATION>>,partition_key:Q<PartitionKey/v1>,
  partition_bound:O<E>,replica_identity_code:B,row_security:true|false,
  force_row_security:true|false,reloptions:S<CatalogOption/v1>,
  view_query:O<E>}
COLUMN {name:O<B>,dropped:true|false,type:R<TYPE>,typmod:I,
  collation:O<R<COLLATION>>,not_null:true|false,
  value_expression:O<E>,generated_code:B,identity_code:B,
  storage_code:B,compression_code:B,inheritance_count:U,
  is_local:true|false}
SEQUENCE {data_type:R<TYPE>,start:I,increment:I,minimum:I,maximum:I,
  cache:P,cycle:true|false,owned_by:O<R<COLUMN>>}
TYPE {length:I,by_value:true|false,alignment_code:B,storage_code:B,
  category_code:B,preferred:true|false,delimiter_bytes:B,
  collation:O<R<COLLATION>>,input_routine:O<R<ROUTINE>>,
  output_routine:O<R<ROUTINE>>,receive_routine:O<R<ROUTINE>>,
  send_routine:O<R<ROUTINE>>,typmod_in_routine:O<R<ROUTINE>>,
  typmod_out_routine:O<R<ROUTINE>>,analyze_routine:O<R<ROUTINE>>,
  subscript_routine:O<R<ROUTINE>>,element_type:O<R<TYPE>>,
  array_type:O<R<TYPE>>,base_type:O<R<TYPE>>,
  composite_relation:O<R<RELATION>>,domain_default:O<E>,
  domain_not_null:true|false,domain_constraints:S<R<CONSTRAINT>>,
  enum_labels:Q<{sort_order_bytes:B,label:B}>,
  range_subtype:O<R<TYPE>>,range_collation:O<R<COLLATION>>,
  range_canonical:O<R<ROUTINE>>,range_subdiff:O<R<ROUTINE>>,
  multirange_type:O<R<TYPE>>}
CONSTRAINT {ordered_columns:Q<R<COLUMN>>,
  referenced_relation:O<R<RELATION>>,referenced_columns:Q<R<COLUMN>>,
  match_code:B,update_action_code:B,delete_action_code:B,
  deferrable:true|false,initially_deferred:true|false,
  validated:true|false,no_inherit:true|false,
  expression:O<E>,exclusion_operators:Q<R<OPERATOR>>}
INDEX {parent_relation:R<RELATION>,access_method:R<ACCESS_METHOD>,
  tablespace_name:O<B>,keys:Q<IndexKey/v1>,include_columns:Q<R<COLUMN>>,
  predicate:O<E>,unique:true|false,primary:true|false,
  exclusion:true|false,immediate:true|false,valid:true|false,
  ready:true|false,live:true|false,replica_identity:true|false,
  reloptions:S<CatalogOption/v1>}
ROUTINE {return_type:R<TYPE>,returns_set:true|false,
  arguments:Q<RoutineArgument/v1>,language:R<LANGUAGE>,
  source_bytes:B,binary_reference_bytes:O<B>,
  provider_binary:O<ProviderBinary/v1>,sql_body:O<E>,
  volatility_code:B,strict:true|false,security_definer:true|false,
  leakproof:true|false,parallel_code:B,
  support_routine:O<R<ROUTINE>>,cost_bytes:B,rows_bytes:B,
  fixed_configuration:S<CatalogOption/v1>}
TRIGGER {routine:R<ROUTINE>,timing_code:B,event_mask:B,
  orientation_code:B,enabled_code:B,
  constraint_target:O<R<RELATION>>,deferrable:true|false,
  initially_deferred:true|false,columns:Q<R<COLUMN>>,
  when_expression:O<E>,arguments:Q<B>,
  old_transition_name:O<B>,new_transition_name:O<B>}
RULE {event_code:B,instead:true|false,enabled_code:B,
  qualifier:O<E>,actions:Q<E>}
ROW_SECURITY_POLICY {command_code:B,permissive:true|false,
  roles:S<RoleIdentity/v1>,using_expression:O<E>,
  with_check_expression:O<E>}
OPERATOR {implementation_routine:R<ROUTINE>,result_type:R<TYPE>,
  commutator:O<R<OPERATOR>>,negator:O<R<OPERATOR>>,
  restriction_estimator:O<R<ROUTINE>>,join_estimator:O<R<ROUTINE>>,
  hashable:true|false,mergeable:true|false}
CAST {function:O<R<ROUTINE>>,context_code:B,method_code:B}
COLLATION {provider_code:B,deterministic:true|false,encoding_code:I,
  locale_bytes:O<B>,lc_collate_bytes:O<B>,lc_ctype_bytes:O<B>,
  icu_rules:O<B>,recorded_version:O<B>}
CONVERSION {source_encoding_code:U,destination_encoding_code:U,
  routine:R<ROUTINE>,is_default:true|false}
ACCESS_METHOD {handler_routine:R<ROUTINE>}
OPERATOR_CLASS {input_type:R<TYPE>,key_type:O<R<TYPE>>,
  family:R<OPERATOR_FAMILY>,is_default:true|false,
  operators:S<OperatorMember/v1>,support:S<SupportMember/v1>}
OPERATOR_FAMILY {operators:S<OperatorMember/v1>,
  support:S<SupportMember/v1>}
LANGUAGE {trusted:true|false,handler:R<ROUTINE>,
  inline_handler:O<R<ROUTINE>>,validator:O<R<ROUTINE>>}
EXTENSION {version_bytes:B,schema:R<SCHEMA>,relocatable:true|false,
  members:S<SchemaObjectIdentity/v1>,
  configuration_relations:Q<R<RELATION>>,
  configuration_conditions:Q<B>}
FOREIGN_DATA_WRAPPER {handler:O<R<ROUTINE>>,
  validator:O<R<ROUTINE>>,options:S<CatalogOption/v1>}
FOREIGN_SERVER {wrapper:R<FOREIGN_DATA_WRAPPER>,type_bytes:O<B>,
  version_bytes:O<B>,options:S<CatalogOption/v1>}
USER_MAPPING {options:S<CatalogOption/v1>}
STATISTICS_OBJECT {relation:R<RELATION>,columns:Q<R<COLUMN>>,
  expressions:Q<E>,kinds:S<B>,target:I}
TEXT_SEARCH_OBJECT {definition:TextSearchDefinition/v1}
LARGE_OBJECT_ACL {content_byte_length:U,content_sha256:Digest,
  acl_refs:S<R<OBJECT_ACL>>}
ROLE {superuser:true|false,inherit:true|false,create_role:true|false,
  create_database:true|false,login:true|false,replication:true|false,
  bypass_rls:true|false,connection_limit:I}
ROLE_MEMBERSHIP {inherit_option:true|false,set_option:true|false,
  admin_option:true|false}
OBJECT_ACL {grantable:true|false}
DEFAULT_PRIVILEGE {grantable:true|false}
SERVER_PRIMITIVE {primitive_definition_bytes:B,
  provider_kind:BUILT_IN|EXTENSION|EXTERNAL,
  extension:O<R<EXTENSION>>,provider_binary:O<ProviderBinary/v1>,
  postgresql_build_digest:Digest}
```

`TextSearchDefinition/v1` has these exact wrapped variants:

```text
PARSER {start:R<ROUTINE>,get_token:R<ROUTINE>,finish:R<ROUTINE>,
        headline:O<R<ROUTINE>>,lex_types:R<ROUTINE>}
TEMPLATE {initialize:O<R<ROUTINE>>,lexize:R<ROUTINE>}
DICTIONARY {template:R<TEXT_SEARCH_OBJECT>,
            options:S<CatalogOption/v1>}
CONFIGURATION {parser:R<TEXT_SEARCH_OBJECT>,
               mappings:S<TextSearchMapping/v1>}
```

`TextSearchMapping/v1` has stable identity `token_type`. Exact duplicate mapping bytes reject as a duplicate set member; two mappings with the same token type and different dictionary sequences conflict.

Secret-valued foreign options and routine sources that cannot be represented without secret material are inadmissible dependencies of the protected surface; the extractor does not store, hash, or expose credentials. A named object normally resolves OIDs to the stable identities above before encoding. The database-local large-object identity is the sole OID-bearing exception because PostgreSQL exposes no schema/name identity for that object; it remains paired with the exact database identity.

Expression-bearing fields use exactly the raw-node-tree representation above. Routine bodies use exact stored source bytes for source-language routines, exact stored SQL-body trees where present, and exact binary reference bytes plus provider-binary length/digest for binary routines. No deparser text, whitespace normalization, path identity, or implementation-selected AST stands in for those bytes.

Manifest extraction is reproducible:

1. validate the exact admitted PostgreSQL version/build, UTF-8 database binding, and extraction-contract ID;
2. read one consistent catalog snapshot and enumerate the exact root set;
3. project every root through the exact typed record above, capture each expression’s raw catalog bytes and complete dependency rows, resolve every referenced dependency to a stable identity, and compute dependency closure to a fixed point;
4. reject an unknown class, field, expression source, restricted-union tag, missing or ambiguous `Q<T>` ordinal, misaligned paired arrays, duplicate identity, ambiguous identity, incomplete dependency projection, dangling edge, dynamic-SQL target outside its declared closed set, secret-bearing dependency, or unclassified object;
5. encode each root identity, object descriptor, and dependency descriptor independently, reject the duplicate and duplicate-identity cases above, then sort each set by unsigned lexicographic comparison of its complete canonical member bytes; dependency comparison therefore includes dependent identity, dependent subobject ordinal, referenced identity, referenced subobject ordinal, and dependency kind;
6. encode the complete envelope with `hindsight-postgresql-schema-manifest-json/v1` and exactly one LF; and
7. recompute byte length and SHA-256 before RM05 insertion.

`ACTIVE_SURFACE` requires non-NONE route generation and NONE phase instance. `CATALOG` requires a phase instance and records the route generation actually installed in that phase. Neither manifest contains its own digest.

A `SERVER_PRIMITIVE` record identifies a built-in or external provider by class, schema/name/signature, exact definition bytes, provider kind, admitted PostgreSQL build, extension identity, and provider-binary digest where applicable. This binds built-in `sha256(bytea)` and `octet_length(bytea)` to the exact server executable without adding an extension. It does not digest unrelated `pg_catalog` contents.

Relational OIDs are excluded from stable logical identities except the named large-object identity. Exact raw `pg_node_tree` bytes and their dependency-row projections retain installation-local OIDs by design; any change therefore changes the manifest. Relfilenodes, physical page placement, planner estimates, collected statistics values, plans, vacuum/freeze state, WAL position, relation data, current anchor values, sequence counters, and RM05 row values are excluded. Target business schema/rows, PostgreSQL settings, storage identity, service paths, and writer inventory remain separately bound by their accepted profile and attestation contracts.

Any addition, removal, definition change, owner/ACL/default-privilege change, dependency-edge change, invalid/unready participating index, disabled participating trigger, changed participating constraint state, role-membership change, or provider/version change inside the active closure changes `ActiveSurfaceDigest` and invalidates current profile/receipt/attestation equality. A retained compatibility object remains in the active manifest whenever an admitted route can reach it, reference it, or exercise a grant to it. It is dormant and excluded only when no active root or grant path reaches it.

Every reachable deployment phase also has a `PhysicalSchemaIdentity`. Its `CatalogDigest` is SHA-256 over exact `CatalogManifest/v1` bytes. `CatalogManifest/v1` uses the same record grammar and canonicalization but roots every object in the locked protected schema plus all associated owners, memberships, ACLs, default privileges, and dependency/provider descriptors, whether active or dormant. It excludes relation data and the physical/runtime fields excluded above. Thus preparation, grant cutover, rollback, cleanup, and final removal each acquire an immutable phase-specific identity even when the route’s ActiveSurfaceDigest is unchanged.

RM05 is the sole durable manifest-byte store. RM01 references one ACTIVE_SURFACE row. An exact RM02 state references the installed CATALOG and ACTIVE_SURFACE rows; an unclassified preparation state instead preserves the last exact refs plus its RM04/RM06 fingerprint. Every RM03 records exact before/after `InstalledSchemaState` values and the after-state route’s ACTIVE_SURFACE row. RM04 and a successful RM06 reference observed CATALOG rows when residue is classifiable. Adapter binary identity and route generation remain separately explicit; neither is hidden inside a catalog digest. RM05’s relation definition participates in both manifests, but its row values do not, so recording a digest does not require the journal to attest its own bootstrap.

`RM02` fields are:

```text
phase:
  UNINITIALIZED | STABLE | PREPARING | PREPARATION_FAILED |
  PREPARED | REVERSIBLE_CUTOVER | FINALIZING
active_generation
candidate_generation | NONE
rollback_generation | NONE
route_generation
preparation_attempt_id | NONE
rollback_eligible
installed_schema_state: InstalledSchemaState | NONE
admitting_transition_id | NONE
```

For every non-`UNINITIALIZED` state:

- `EXACT` means its catalog and active-surface refs resolve RM05 rows whose digests equal the `PhysicalSchemaIdentity` fields and whose bytes equal independent reconstruction of the actual catalog.
- `UNCLASSIFIED_PREPARATION` is legal only with phase PREPARING and route G. It names the last exact G identity, current fingerprint, and immutable RM04/RM06 record but makes no claim that the changed catalog equals a manifest. Startup and every runtime route fail closed in this state.
- The route’s adapter digest and generation are admitted by RM01 whenever the state is exact.
- `admitting_transition_id` names the unique RM03 row whose complete after-state equals RM02. An unrecorded catalog change discovered after a crash is treated as unclassified until MT04 records the failure state.
- Dormant residue is inaccessible to runtime roles. A preparation failure permits G startup only after exact classification proves the active surface remains G.

Permitted transitions are exactly:

| ID / event | Source → destination | Preconditions, effect, failure and rollback |
|---|---|---|
| MT01 `BOOTSTRAP` | `UNINITIALIZED → STABLE(G0)` | Explicit deployment installs G0, inserts exact G0 ACTIVE_SURFACE and CATALOG rows in RM05, and appends RM01/RM03. RM03’s after refs and identity name those rows; RM02 points to them. No historical authority backfill. Failure leaves `UNINITIALIZED`. |
| MT02 `BEGIN_PREPARATION` | `STABLE(active=G) → PREPARING(active=G,candidate=G+1,route=G)` | Requires no candidate or rollback and an exact predecessor/migration digest. Appends RM03 before additive DDL; its before/after catalog and active-surface digests are equal because this event changes metadata only. It works after any finalized generation, including G+1 before preparing G+2. |
| MT03 `PREPARATION_SUCCEEDED` | `PREPARING → PREPARED` | After additive objects, versioned functions and dormant grants are installed and classified, inserts the exact resulting CATALOG row and future G+1 ACTIVE_SURFACE row in RM05, proves the independently reconstructed active surface still equals G, inserts immutable RM01 for G+1 referencing that future row, appends RM03, and advances RM02. Active route remains G. |
| MT04 `PREPARATION_FAILED` | `PREPARING(EXACT or unrecorded drift) → PREPARATION_FAILED(EXACT) or PREPARING(UNCLASSIFIED_PREPARATION)` | For classifiable residue, inserts its exact CATALOG row in RM05 and atomically appends RM04/RM03 and advances to PREPARATION_FAILED while preserving active/route G. For unclassifiable residue, appends RM04 and RM03, changes RM02 to the explicit unclassified state with that fingerprint, retains the last exact G identity only as history, and fails startup closed. No adapter or active grant is switched. |
| MT11 `REMEDIATE_UNCLASSIFIED_PREPARATION` | `PREPARING(UNCLASSIFIED_PREPARATION) → PREPARING(UNCLASSIFIED_PREPARATION) or PREPARATION_FAILED(EXACT)` | Requires the exact current attempt, last fingerprint, expected RM02 state digest, and controlled remediation/migration artifact digest. Every attempt appends checked-next RM06. A still-unclassifiable result also appends RM03 and advances only the fingerprint/ref in the explicit unclassified state, permitting another remediation without editing history. A classifiable result inserts/exact-replays its CATALOG manifest, proves the route-active surface equals G, appends RM06/RM03, and advances to PREPARATION_FAILED. Changed request bytes conflict; a failed proof leaves no invented manifest or runtime authority. |
| MT05 `RETRY_FAILED_PREPARATION` | `PREPARATION_FAILED(EXACT) → PREPARING(EXACT)` | Requires residue to be proved compatible or removed and the resulting exact identity recorded. Appends RM03 with a new attempt ID and the same base/proposed generation and migration identity. Prior RM03/RM04/RM06 rows and final boundaries remain immutable. |
| MT06 `ABANDON_FAILED_PREPARATION` | `PREPARATION_FAILED → STABLE(G)` | Requires no active route/grant to residue and an exact catalog identity whose active surface is G. It appends RM03, clears candidate/attempt state, preserves failure history, and records the actual retained-or-cleaned catalog. A later MT02 may propose G+1 again. |
| MT07 `ACTIVATE_REVERSIBLY` | `PREPARED(G,G+1) → REVERSIBLE_CUTOVER(active=route=G+1,rollback=G)` | Explicitly switches adapter routing and grants, inserts the resulting CATALOG row in RM05, and appends RM03 referencing it and RM01(G+1)’s ACTIVE_SURFACE row. Independent reconstruction must equal both exact rows. Only representations readable and writable by both generations are admitted; G objects remain. |
| MT08 `ROLL_BACK_ROUTE` | `REVERSIBLE_CUTOVER → PREPARED(active=route=G,candidate=G+1)` | Restores G adapter routing and grants first, inserts or exact-replays the resulting CATALOG row, and appends RM03 referencing it and G’s ACTIVE_SURFACE row. G+1 objects remain dormant and the immutable G+1 generation and manifest remain prepared. |
| MT09 `CLOSE_ROLLBACK_WINDOW` | `REVERSIBLE_CUTOVER → FINALIZING(active=route=G+1)` | Appends RM03 and atomically records `rollback_eligible=false` before or with the first G+1-only authoritative representation. It need not alter catalog bytes; if not, its before/after catalog and active-surface digests are equal while its phase identity differs. Failure afterward is roll-forward-only. |
| MT10 `COMPLETE_FINALIZATION` | `FINALIZING → STABLE(active=route=G+1)` | Removes only G objects proved absent from the G+1 active closure or finalizes their inaccessibility, inserts the resulting phase-specific CATALOG row in RM05, proves the independently reconstructed ACTIVE_SURFACE bytes equal RM01(G+1)’s referenced RM05 row, appends RM03, and advances RM02. It never mutates RM01(G+1), RM05, or earlier identities. The resulting STABLE state admits MT02 for G+2. |

Startup performs no DDL. For an exact RM02 state it resolves RM03, RM01, and both RM05 rows, recomputes `PostgresqlBuildIdentity/v1`, reruns the exact §8 extraction algorithm against the actual catalog, and compares canonical bytes, lengths, and digests before verifying route/adapter compatibility and phase invariants. An `UNCLASSIFIED_PREPARATION` state fails before route admission. Any active member or dependency change invalidates current admission. Any unrecorded full-catalog change fails startup even when the active digest is unchanged. Unknown generation, unsupported build or adapter, unresolved or unclassified object, missing RM05 row, wrong manifest kind/version, byte/length/digest mismatch, illegal transition, partial unclassified preparation, or incomplete metadata fails closed.

No migration rewrites body bytes, immutable facts, digests, timestamps, deadlines, budgets, current-state history, historical-reader meaning, target history, or legacy artifacts. A new projection for an old fact is an additive side relation keyed to the unchanged fact. No migration creates synthetic publication, evidence, qualification, admission, manifest, lineage, or operation authority.

Deployment privileges comprise controlled C23→O01 assumption; relation/type/constraint/index/function DDL; ownership assignment; grants/revocations; manifest extraction; and RM01–RM06 writes under exact RM02 CAS. MT01–MT11 are deployment-only state transitions, not §5 runtime callables or adapter methods. Creating owner roles separately requires role-administration authority. Runtime roles receive none of these privileges.

## 9. Later evidence manifest

A later evidence artifact uses these exact source and proposal sets:

```text
SOURCE_SUCCESSOR_BODY_IDS = BS001–BS126
SOURCE_COMPATIBILITY_BODY_IDS = BC001–BC021
SOURCE_MEMBER_INVENTORY_SHA256 = c4f8ca867f0fff9c7576c9e5ed076095bbaa25f704952243b51f2ce5821ba84f
SOURCE_MEMBER_INVENTORY_IDS = every exact expanded identity in the sibling source-member inventory
SOURCE_MEMBER_DISPOSITION_IDS = one §2.2.1 disposition for every SOURCE_MEMBER_INVENTORY_IDS member
SOURCE_PROTECTED_PREDICATE_IDS = PRD001–PRD065, QEQ001–QEQ042, DEQ001–DEQ063, PLV001–PLV078, PLR001–PLR098, DFR001–DFR042, NMR001–NMR035, the eleven evidence-operation members, and every exact copy member in the inventory
SOURCE_CURRENT_SLOT_CLASSES =
  ACTIVE_EPOCH, ACTIVATION_CAPABILITY, ACTIVATION_PROPOSAL,
  CLOCK_ENVELOPE, DEPLOYMENT_ATTESTATION, DEPLOYMENT_POLICY,
  EVIDENCE_TIER_RESULT, LEGACY_FENCE, LINEAGE_HEAD,
  OPERATION_ACCOUNTING, OPERATION_AUTHORITY, OPERATION_GRANT,
  OPERATION_WORK_RESERVATION, OPERATION_WORK_START,
  OPERATION_WORK_COMMITTED_RESULT, PUBLICATION_EPOCH_HIGH_WATER,
  QUALIFICATION_RECEIPT, RESERVED_ACTIVATION, ROLE_GRANT_SET,
  TARGET_GENERATION, WRITER_INVENTORY
SOURCE_FACT_OBLIGATIONS = every distinct left-hand fact member in §2.2
SOURCE_CALLABLE_OBLIGATIONS = every distinct left-hand callable member in §2.2
SOURCE_ROLE_OBLIGATIONS = every distinct left-hand role member in §2.2
SOURCE_STATE_OBLIGATIONS = every distinct left-hand state member in §2.2
SOURCE_SEMANTIC_OBLIGATION_IDS = SO001–SO064, with every member defined in §2.3
SOURCE_FENCE_TIME_OBLIGATION_IDS = FT001–FT006, with every member defined in §2.3
SOURCE_FENCE_DEADLINE_OPERAND_IDS = FD001–FD007, with every member defined in §2.3
SOURCE_RESULT_SUBJECT_PROJECTION_IDS = VR001–VR004, with every member defined in §2.3
SOURCE_REFERENCE_PROJECTION_SPAN_IDS = SPA001–SPA012, with every site and immutable source span defined in §2.3
SOURCE_REFERENCE_PROJECTION_MEMBER_IDS = one SRP identity for every exact REF member in SOURCE_MEMBER_INVENTORY_IDS
SOURCE_CALLABLE_SEMANTIC_IDS = SC001–SC061, with every member defined in §2.3.1
SOURCE_BODY_SITE_IDS = BM001–BM062
SOURCE_BODY_ROOT_PATTERN_IDS = every literal MRR-BMmmm-Xnn pattern in §5.2.1
SOURCE_BODY_MEMBER_PATTERN_IDS = every MRE identity produced by the closed grammar expansion in §5.2.1
SOURCE_BODY_DIGEST_LINK_TEMPLATE_IDS = DLT-DL001–DLT-DL013
SOURCE_BODY_DIGEST_LINK_INSTANCE_IDS = every DLI identity emitted from those templates
SOURCE_BODY_REQUIREMENT_EDGE_IDS = every BREQ identity emitted by the direct-root and exception registries
SOURCE_BODY_PARENT_EDGE_IDS = every BODY_PARENT identity emitted for an MRE or DLI
PROPOSED_DURABLE_REFERENCE_PROJECTION_IDS = every DRP identity emitted by the closed §5.2.1 mapping
PROPOSED_PROTECTED_FIELD_COPY_IDS = every concrete PFC-RC21-*, PFC-RC28-*, and PFC-RC29-* identity
SOURCE_NONORDINARY_BODY_CLASSIFICATION_IDS = NBR01–NBR03
ISSUE106_DERIVED_OBLIGATION_IDS = D106-01, D106-02, D106-03, D106-04
PROPOSED_RELATION_IDS = all 136 IDs in §3
PROPOSED_CALLABLE_IDS = all 61 protected callable IDs in §5
PROPOSED_INTERNAL_ROUTINE_IDS = IA01–IA10
PROPOSED_ROLE_IDS = O01–O18, C01–C25, PUBLIC
MIGRATION_TRANSITION_IDS = MT01–MT11
```

The fact/callable/role/state sets in §2.2 are navigation inventories, not semantic closure. The present source expectation is the complete sibling inventory and §2.2.1 disposition function. Body-use semantics remain the explicit MRR roots, outcome-qualified BREQ edges, closed MRE/BODY_PARENT expansion, DLT templates, DLI instances, inventory-owned SRPs, DRP source-to-destination mappings, protected source predicates, and scalar-only PFC copies in §5.2.1; none defines inventory membership.

A later evidence extractor receives the immutable accepted files, accepted issue texts, and the sibling inventory, never §§2.2–5 as its expected set. It reads every exact inventory identity and disposition before reading the proposal. Only afterward does it compare the proposal carrier, destination field, BREQ/DRP/PFC or protected-predicate edge, atomic group, ACL cell, and source-text digest. Evidence requires set equality with `I` and `D`, then forward and reverse equality with the printed proposal mappings. Removing a source member together with all proposal and check rows therefore leaves an inventory/disposition member without a mapping.

The four D106 obligations are checked separately against the complete derived contracts in §§3, 5, 7, and 8; the extractor must not pretend the accepted source already chose their representation. A source member classified non-PostgreSQL must equal one explicit source-supported non-PG classification; it cannot disappear.

Static coverage fails on any source proposition, SPA site, SRP member, fence-time gate, FD001–FD007 member, VR001–VR004 result-subject projection, source carrier, destination field, branch, cardinality, stable key or ordinal, reference domain, applied/replay/refusal outcome, direct root, recursive body-reference occurrence, digest-only binding, protected-field read, decoded bridge, typed reference projection, scalar field copy, requiring relation/fact/effect, parent edge, or atomic edge omitted from SO/SPA/SRP/FT/FD/VR/SC/MRR/MRE/DLT/DLI/BREQ/BODY_PARENT/DRP/PFC or its mapped clauses. Reverse coverage requires every relation, callable, IA routine, BM site, MRR/MRE/DLI/BREQ/BODY_PARENT/DRP/PFC identity, SRP/VR mapping, principal, slot, and migration transition to map to at least one independently anchored SO, SRP, or D106 obligation.

For every body-use or protected-field site, evidence compares source-derived and proposal rows at member-and-edge level: `(SPA identity,SRP/VR identity,member identity,operation,accepted source field path,origin,root domain,branch,cardinality,root ordinal,stable output key,occurrence path,transitive member,FB01|FB02|PROTECTED_FIELD|DECODED_BRIDGE carrier,requirement-edge identity,relation/effect/parent target,semantic mode,effect outcome,atomic-group ID,DRP/PFC identity,accepted output member,destination field)`. Applied, exact replay/readback, and refusal are separate rows; sequences, sets, keyed outputs, first-use sequences, receipt sets, and PFC scalar members expand individually. Equality is bidirectional. Removing a source member and its proposal/evidence rows together still fails against the unchanged sibling inventory and §2.2.1 disposition set. Removing an RS10 field or its FA03 initialization, letting FE08 write RE11, deleting FE10’s receipt/time/branch operands, removing any of VR001–VR004, omitting one affected key’s RE15 read or RE16 subject projection, directing FE09’s replacement around RE15, splitting BM013’s BS034→RE06 projection, BS015→RE05/RE06 observation, or BS064→RE06 acquisition edges, omitting BM038 R05→RW06.typed_result_ref, omitting any BM062 graph destination, misnaming RC21’s deployment-attestation field, removing a target bridge or approval-digest-keyed receipt reference, moving a BodyRef into PFC, changing RC28 origin binding from DATABASE to SUPPLIED, omitting FD001 or any of `U_fence_start`, `U_fence_commit`, `U_adopt`, or `U_cutover`, collapsing WRITE and VERIFY_PRESENT, removing IA10’s O09→O01 read edge, deleting one FAD02/FF05 effect, omitting one terminal V selector update, or dropping a caller/derived operand fails even if all noun counts still match.

Every relation-ID evidence record must explicitly mark:

- positive insert/read;
- exact replay;
- changed-binding conflict;
- reference-kind/version enforcement;
- immutable/current/private/session/deployment behavior;
- authorized and unauthorized access;
- CAS/currentness/concurrency where applicable, otherwise `NOT_APPLICABLE` with the profile reason.

Every function-ID evidence record must explicitly mark:

- positive outcome;
- exact replay or read repetition;
- complete caller-supplied operand projection;
- complete database-derived operand projection;
- every branch predicate and its exact refusal/result;
- complete ordered atomic effect set;
- every applicable protected temporal gate, its database-derived envelope/deadline/duration operands, exact sampling position, strict comparison, and no-effect refusal, otherwise `NOT_APPLICABLE`;
- malformed or unresolved source input;
- operation-specific refusal;
- stale-current behavior;
- atomic rollback;
- lost acknowledgement;
- positive grant;
- every member of its default-deny complement;
- no-effect/read-only behavior where applicable.

Named suites must cover:

- `EV106-BODY`: canonical vectors, LF sensitivity, hash/length, closed registry, all four storage classifications, exact `ResolvedOrdinaryBodyClosure` vectors, and forward/reverse equality for every BM/MRR/MRE/DLT/DLI/BREQ/BODY_PARENT/DRP row. It includes every direct source path and parameterized sequence/set/keyed expansion; missing/extra root, requirement, parent, destination projection and transitive-member refusal; every DLT-DL001–DLT-DL013 template and emitted DLI target; private/cipher/transient rejection; owner/site/origin/branch/ordinal/key mismatch; owner-only FB grants; exact `READ/NO_WRITE`, `WRITE/APPLIED`, and `VERIFY_PRESENT/EXACT_REPLAY` vectors with no INSERT/UPDATE/CLEAR encoder discretion; BM029 replacement-from-ABSENT, replacement-from-PRESENT, exact replay, clear, stale and retired-pair branches; BM012.R06, BM015.R03, BM017.R05, and BM018.R05 key-by-key RE15 reads and RE16.subject_revision_ref write/replay edges, including repeated subject refs under distinct keys; BM017.S01’s RE14 and RE15 edges; BM013’s exact BS034→RE06 projection, BS015→RE05 observation plus RE06 time observation, BS064→RE06 acquisition, and shared atomic group; BM038 R05’s carrier read plus RW06.typed_result_ref write/replay edge; BM057’s supplied disable-attestation→RC26 edge; every BM062 RC01–RC04/RC14–RC20 projection; every FE07 body/relation/effect/pointer edge; every `WORK_RESULT` mapping; every stage-result/FW04 grouping; and rollback proving each newly inserted body and every required relation/fact/effect appear together or neither does.
- `EV106-PROJECTION`: reads every inventory-owned SRP and disposition before reading the proposal, verifies the corrected SPA001–SPA012 operation anchors, then requires exact forward/reverse equality with every direct destination, relation field, BREQ, DRP and scalar-only PFC edge. It covers authority; evidence, verdict and disposition; qualification, private review and admission; work and recovery; publication and restart; closure and fencing; and activation. It includes all qualified-clock and boot destinations; RE17 support profile and first-use clock epochs; every source-assigned RE24 and RE25 reference, plus exact-body type/mirror handling without an invented source equality for `DeploymentAttestation.postgresql_settings` or scalar projection for its `target_generation`; RW06 plan and chain; both RP03 branches; RP01/RP10/RP15 target/request bindings; the locked-policy RC05 projection and every RC09 attestation; every RC14 manifest reference; complete RC21-to-RC28 and adoption graph fan-out; the target identity bridge; every manifest and exclusion approval receipt keyed by approval digest; every typed approval/exclusion reference; every scalar, sequence and set member; exact replay/readback; and RX03 boot/synchronization projections. Deleting a source member and its destination from both the proposal and test plan, changing a carrier, collapsing repeated members, replacing a typed DRP with PFC, or retaining only BODY_PARENT fails.
- `EV106-AUTH`: all exact expected-current operands, lifecycle transitions, revocation and nonrenewing deadline; FA03/IA01’s complete zero RS10 vector; exact replay after accounting advances; and committed-plan/missing-RS10 refusal.
- `EV106-EVIDENCE`: eleven exact evidence callables; source-derived VR001–VR004 equality against the four and only four result-producing operation paths; FE04’s campaign-tier equality plus one protected current-subject read and RE16 destination per affected key; FE07’s corresponding per-key subject/result/pointer atomic group; FE09’s immutable prior subject, staged RE15 replacement, post-transition evaluator reads, cascaded result projections, and replay; FE10’s authority-subject/current-subject equality plus per-key result projection on both branches; absence, mismatch, stale-current, applied, exact-replay, and rollback vectors for every such member; FE05's exact three derived bodies and RE05/RE06 atomic group; FE08’s independent finding validation and proof that it changes no RE11/RE13/RE16/RS07 member; FE10 vectors for every authority-subject and BS045 field, LF-bearing subject digest, authenticated principal, fresh `EVIDENCE_DISPOSITION_APPLY` RE05, strict upper-bound inequality, complete invalidation claim set, valid-FAIL refusal, contiguous supersession, both exact atomic branches, and every no-effect refusal; FE07’s complete member-level body/fact/pointer transaction; FQ01’s exact plan, acceptance, class and run-result operands; FQ02’s exact plan, acceptance, profile, three class refs and three complete tier sequences; IA02/IA03/IA04 protected observations; and nonrenewing exact replay.
- `EV106-PRIVATE`: private composite identity, mapping, reviewer authorization, commitment vectors, CAS and all-or-neither export.
- `EV106-WORK`: distinct RequestKey and ReservationKey roles, production WorkSlotKey vectors, separate complete WorkIdentityBinding vectors, and the source-only AuthorityGate operation-work preimage; field-by-field mutation of all five work-identity variants; multiple RequestKeys sharing one ReservationKey; same-request exact replay; changed `(plan,request_id)` REQUEST_CONFLICT; different-request WORK_ALREADY_RESERVED; FW01’s different-request UNRESOLVED branch; request-keyed refusal with no RE05, reservation, or accounting change; successful reservation’s atomic RE05/RW03/RS10/RS13 effect; ReservationKey result uniqueness; exact production WorkSlotKey and complete WorkIdentityBinding equality across RW03–RW06 and RS13–RS15; every result relation, resolver chain, and FW07’s complete two-slot close.
- `EV106-PUBLICATION`: `J→P→R→M→V`; both J adoptions; CURRENT/LATE J and P effect sets; M’s RP06/RP07/RP09 delta with no RP08 write; lineage/generation CAS; and all four exact V/RP14 branches.
- `EV106-CLOSURE`: six distinct callables; atomic RC05/RC06 and RC07/RC08/RC09 initialization; generation-zero first claim; same-ordinal takeover; finalization/expiry RC08 close; deadlines, abandonment, exhaustion, and sticky terminal result.
- `EV106-CUTOVER`: inventory/reader closure; RC21’s immutable pending envelope with binding `NONE`; FF04’s atomic BC019/RC27 plus field-complete BC020/RC28 origin binding and RS08 installation; field-complete RC29 adoption bindings; every SPA011/SRP typed DRP and scalar-only PFC member on applied/replay/readback/refusal branches; exact target bridging; exact approval-digest-keyed exclusion receipt references and scalar receipt fields; database-only BM060/BM062 graph origin; contiguous DLI-linked handoff replay; every RC01–RC04/RC14–RC20 destination; RX03’s separate clock, boot, and synchronization-epoch projections; atomic compatibility-metadata persistence; and fence progression. `EV106-FENCE-TIME-PREFENCE` checks source-owned `U_prefence`, exclusive invocation consumption, `U_consume`, exact FD001–FD007 inputs, restart invalidation, and no PostgreSQL authority. `EV106-FENCE-TIME-START` checks FF01’s post-lock/pre-effect sample, exact finite-transaction margin, monotonic horizon, complete seven-class deadline set, explicit current BS048 policy expiry, and no-effect refusal. `EV106-FENCE-TIME-COMMIT` checks the distinct immediate-precommit sample and revalidation under all retained locks, including policy identity/expiry, complete rollback, spent invocation, and exact lost-acknowledgement read. `EV106-FENCE-TIME-STEP` checks a new sample and separately rounded finite-timeout margin before every observation, cancellation, termination, drain wait and service disable, strict comparison with FD001–FD007, authority locks retained through exact outcome recording, and no continuation from prior state. `EV106-FENCE-TIME-ADOPT` checks both adoption modes’ post-revalidation sample, exact `U_adopt`, FD001–FD007, strict inequality, equality/overflow/uncertainty/drift refusal, no adoption or outer success effect, exact-current replay without resampling, and superseded replay. `EV106-FENCE-TIME-CUTOVER` checks post-revalidation sampling, exact CLOCK-MATH and split-rounding vectors, every `CutoverClockObservation` field, one locked-current FD001 policy member plus every FD002–FD007 member, strict inequality with all seven classes, equality/overflow/rollback/loss/reboot/suspend/policy-drift/envelope-drift refusal with no effect, and replay returning the original observation without resampling. The suite also covers direct SAME_EPOCH adoption; FAD02-only LATER_EPOCH adoption; activation’s RP08/RS20 initialization with RS09 absent; and abandonment’s RS05 clear. A fault or visibility probe between any member of an accepted atomic set must observe either the complete prior state or complete new state.
- `EV106-ACL`: all 44 principal classes, every positive function cell, every complement denial, relation denial, ownership, membership, defaults and safe search paths; exact MRR/MRE/DLI/BREQ FB01/FB02 owner/site grants and denial to all logins/adapters/O17; all ten IA positive cells and their complements; IA10’s sole O09→O01 EXECUTE cell, retained RM locks, non-byte result, and denial of direct RM access to O09; O16’s exact read/lock projection over RS08, its selected RC28-or-RC29 completed binding and chain, and supporting RC21/RC23–RC27 rows, with denial of O15 DML, pending-RC21-as-binding use, graph listing and adoption mutation; O15’s exact FF01–FF05 column-limited read/lock projection and denial of every evidence/policy/admission/epoch mutation path; denial to C21/C15/O09 of direct clock, deadline, result and relation access; absence of other cross-owner DML; FE08 denial to C09 and FE10 denial to C08; O06 denial on RS17; O09 denial on RE17/RE18 mutation and FQ01/FQ02; C15 denial on FF05 LATER_EPOCH; O09 denial on FF05 SAME_EPOCH or direct RC29/RS08 DML; denial of FF05 to every other principal; and proof that C23→O01 is the sole controlled owner-assumption exception while O02–O18 remain unassumable.
- `EV106-ACK`: provisional return, precommit failure, acknowledged commit, lost acknowledgement and exact durable readback.
- `EV106-STATUS`: before/after snapshots proving all three status functions mutate no relation, clock, target, observation, authority, or RM05/RM06 row and expose no manifest bytes.
- `EV106-MIGRATION`: every MT01–MT11 transition and branch; RM05 exact insert/replay and every RM01–RM04/RM06 FK; byte vectors for the exact JSON encoding, both Optional wrappers, every SchemaObjectIdentity and SchemaObjectDefinition tag/value wrapper, both GranteeIdentity variants, all ten AclTargetIdentity tags, all thirty DependencyReferencedIdentity tags, both IndexKeySource variants, both PartitionKeySource variants and complete PartitionKey record, both AclScope variants, all four TextSearchDefinition variants, every simple enum, and the complete seven-top-level-field standalone PostgresqlBuildIdentity preimage; one vector per each of the 18 CatalogExpression source rules proving exact catalog field, presence predicate, owner, source ordinal, list alignment, raw complete-datum retention, dependent-address set, dependency-row equality and consumer-tag restriction; vectors for all 18 `Q<T>` fields proving exact ordinal derivation, repeated-value preservation, missing/duplicate/ambiguous ordinal refusal and paired-array alignment; canonical-set vectors for domain constraints and text-search mappings; root/object/dependency set ordering, duplicate rules, object-identity collision refusal, and dependency edges differing only in either subobject ordinal; dependency closure, retained-compatibility and exclusion vectors; independent CATALOG and ACTIVE_SURFACE byte/length/digest recomputation for every exact phase; build, raw-OID, definition, owner, ACL and dependency invalidation; valid G startup with classified preparation residue; explicit unclassified startup refusal; repeated RM06 `STILL_UNCLASSIFIABLE` history; successful MT11 classification followed by MT05 retry and MT06 abandonment; reversible grant/adapter rollback; invariant attested `protected_schema_digest`; phase-specific identities across MT09/MT10 object removal; G+2 preparation; and unchanged prior RM01/RM03–RM06 and authority history.
- `EV106-QUALIFICATION-INSTALL`: FQ02 proves no target or RS17 effect; FAD02 proves exact campaign/attempt/candidate identity, TargetSurfaceKey, expected absent/present RS17 CAS, exact receipt/profile/release/schema binding, IA10's locked RM01/RM02/RM05 projection and refusal on drift/unclassified state, no direct O09 RM privilege, accepted RE25 failure identity and no-authority effect, FRESH behavior, occupied-fence FAD02/FF05 atomicity, stale-CAS refusal, atomic RS17/attestation/reservation installation, and no qualification-body mutation.
- `EV106-SOURCE-CLOSURE`: reads the complete sibling inventory and §2.2.1 disposition set first, then independently extracts every SO001–SO064 proposition, every corrected SPA001–SPA012 source span and inventory-owned SRP member, every FT001–FT006 temporal proposition, every FD001–FD007 deadline operand, every VR001–VR004 result-subject projection, every SC001–SC061 fingerprint, every direct root pattern, typed-reference path, requirement edge, parent edge, protected-field read, decoded target bridge, destination projection, scalar field copy, and compatibility digest-link occurrence from the pinned sources and accepted decisions, then requires equality with SO/SPA/SRP/FT/FD/VR/SC and MRR/MRE/DLT/DLI/BREQ/BODY_PARENT/DRP/PFC proposal mappings. It covers exact fields, keys, references, caller/derived operands, branch predicates, refusal outcomes, current effects, ACL cells, source paths, origins, ordinals, stable output keys, cardinalities, transitive members, requiring relations/facts/effects, copied destination fields, semantic edge modes/outcomes, parent edges and atomic groups. It reads inventory identities and dispositions before proposal relation IDs or advertised evidence, so deleting one accepted source member together with its relation, protected predicate, DRP/PFC, and test row still fails. For evidence results it independently emits the four source operation paths, each affected `(claim_id,tier)` key, its protected pre- or post-transition RE15 carrier, BS039 subject member, RE16.subject_revision_ref destination, RS07 companion effect, applied/replay/refusal outcome, and atomic group before comparing with BM012/BM015/BM017/BM018 and their DRP/BREQ records. For fencing it emits every pending, origin and adoption reference, exact target bridge, approval-digest-keyed receipt binding, scalar receipt member, BC020/BC021 equality operand, adapter-local `U_prefence`/`U_consume` non-PG classification, FF01’s post-lock `U_fence_start` and distinct immediate-precommit `U_fence_commit`, every later finite-timeout per-effect gate, FF05’s post-revalidation `U_adopt`, the BS048 policy expiry and every other FD member, every exact clock/formula/duration operand, strict comparison, sampling position, retained lock, no-effect refusal, spent-invocation exception, and replay/readback rule. For activation it emits RC21 as pending with binding `NONE`, FF04’s field-complete RC28 origin binding, field-complete RC29 adoption, the database-derived protected graph path, every graph member, every BM062 destination in RC01–RC04/RC14–RC20, post-revalidation `U_cutover` ordering, every cutover observation field, FD001–FD007, formula operand/result, strict deadline and atomic effect. For work results it emits the selected carrier read and the distinct RW06.typed_result_ref destination. For disposition it emits every field of both authority subjects, BS045, BS046, BS047, the protected current-subject equality and per-result projection, the RE05 subject projection, strict time predicate, finding-only restriction, both branches and reciprocal C08/C09 denials. For evidence acquisition it emits BS034→RE06 projection, BS015→RE05/RE06 observation and BS064→RE06 acquisition under one atomic group. For policy CAS it independently emits replacement-present applied/replay, replacement-NONE clear, expected/current-present read, stale and retired-pair branches and compares them with BM029’s exact BREQ outcomes. For schema admission it emits the current protected-schema/build binding and IA10’s precise ACL edge. It separately checks D106-01–D106-04 and every IA routine. Deliberately deleting an SRP or VR member, keyed current-subject root, RE16 subject destination, MRR root, MRE field path, DLI, BREQ, BODY_PARENT, DRP or PFC edge, changing a semantic BREQ mode/outcome, moving a reference into PFC, removing FD001 from any applicable gate, dropping a completed-binding graph read or destination, omitting any fence/adoption/cutover time gate or observation field, removing IA10, RS10 initialization, FE10 application observation, an invalidation branch, atomic cross-owner effect, terminal selector update, or other semantic member from both proposal and advertised evidence must fail this suite.
- `EV106-INDEX`: catalog identity and representative plan evidence for every §4 index.
- `EV106-PHYSICAL`: separately authorized admitted-profile restart and power-loss qualification. Logical SQL/concurrency checks do not establish physical durability.

## 10. Remaining ticket boundaries

This resolution does not choose transaction orchestration, recovery loops, or lock ordering ([issue 107](https://github.com/nisavid/agents/issues/107)); application mutation/compatibility integration ([issue 108](https://github.com/nisavid/agents/issues/108)); implementation sequence and evidence gates ([issue 109](https://github.com/nisavid/agents/issues/109)); implementation base or historical-source incorporation ([issue 110](https://github.com/nisavid/agents/issues/110)); or outside-repository target-release investigation ([issue 111](https://github.com/nisavid/agents/issues/111)).

Those tickets may choose implementation sequencing and orchestration, but may not erase a relation, callable, expected-current operand, specialized result, authority boundary, or evidence obligation defined here.

No permanent-retirement callable exists. Protected preimages and ciphertext remain retained until matching rollback `M` and `V`, or until a separately accepted retirement design defines its authority and interruption contract.
