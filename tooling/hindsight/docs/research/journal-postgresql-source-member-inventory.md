# Hindsight PostgreSQL source-member inventory

This source-native planning inventory for
[issue #106](https://github.com/nisavid/agents/issues/106) fixes the accepted
member expectation for a later PostgreSQL relation, interface, access, and
evidence crosswalk. It is not implementation, execution evidence, or database
authority. Deleting a member from both a proposal and its test plan cannot
change this independent expectation.

A member's identity is
`(accepted_revision,source_file,source_contract_or_operation,exact_member_path,branch_or_variant)`;
no SQL identifier participates.

## Pinned inputs and notation

The accepted revision is
[`90108b516f5a1c460980a93670348f6e228124f2`](https://github.com/nisavid/agents/tree/90108b516f5a1c460980a93670348f6e228124f2).
Its files are
[`tooling/hindsight/README.md`](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/README.md) (`OV`),
[`journal-publication-design.md`](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md) (`PD`),
[`journal-restart-design.md`](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-restart-design.md) (`RD`),
[`journal-compatibility-design.md`](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md) (`CD`), and
[`journal-acceptance-evidence.md`](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md) (`AE`).
A citation such as `AE:2410–2424` identifies that immutable file and range.

Additional accepted inputs are the
[body/reference-interface resolution](https://github.com/nisavid/agents/issues/105#issuecomment-5564764189),
[first PostgreSQL round](https://github.com/nisavid/agents/issues/106#issuecomment-5565174781),
and
[second PostgreSQL round](https://github.com/nisavid/agents/issues/106#issuecomment-5565270427).
They fix source-owned strict decoding and reference closure, typed current and
historical interfaces, one locked PostgreSQL schema with separated owners,
explicit migrations, shared nonauthorizing ordinary bodies, separate
private/ciphertext domains, typed current selectors and immutable facts, and
narrow source adapters. These constraints are not proposal-derived members.

Notation:

- `REF` is exactly
  `{contract_kind,contract_version,body_digest}` (or the corresponding
  compatibility triple), with kind/version dispatch, exact bytes, digest
  recomputation, and recursive closure. A bare digest is not a `REF`.
- `KEY` is a stable, unique, or current key; `COPY` is a protected equality;
  `PRIVATE`, `CIPHERTEXT`, and `SOURCE_ONLY` name the three nonordinary
  dispositions.
- `X.{a,b}` means both exact paths `X.a` and `X.b`.
  `items[i]` expands once per ordered element; `items[k]` once per
  duplicate-free source-keyed member; `?` is the exact `NONE`/value union.
  These are closed expansion rules, not wildcards.

The codec/resolver owns structural validity and closure; protected consumers
own currentness, equality, and transitions. Evidence, deployment admission,
operation authority, private evidence, work/stages, recovery, compatibility
closure/fencing/adoption, and combined activation retain the functional owners
and database-derived inputs fixed at `AE:4803–5098`. Issue #107 owns later
orchestration and lock order, not these member identities.

## Reference-member roster

Each path is one source-native member. The type before the first dot is its owning contract; the section's owner/operation line names its source-native producer or consumer. An unmarked path is one required typed `REF`. Braces, sequences, keyed sets, and optionals use the closed notation above.

### Foundational identities, profiles, tools, and observations

Owner/operation: codec/resolver; protected profiler for live-derived values.

- `EvidenceIdentity.descriptor`.
- `ProfileComponent.configuration`.
- `ProfileComponent.identity`.
- `PostgresqlComponentConfiguration.postgresql_settings`.
- `MacosLocalPostgresqlLiveProjection.{boot_environment_configuration,boot_identity,clock_configuration,collected_at,controller_host,filesystem_configuration,hardware_configuration,operating_system_configuration,postgresql_configuration,postgresql_endpoint,postgresql_host,storage_configuration,support_profile,target_database_identity,virtualization_configuration}`.
- `RoleGrantSet.target_database_identity`.
- `ServiceWriterPath.service_identity`.
- `WriterInventory.role_grant_set`.
- `WriterInventory.service_identities[i]`.
- `WriterInventory.target_database_identity`.
- `DeploymentEvidenceAcquisition.{acquired_at,acquisition_procedure,campaign,observed_projection}`.
- `ProcedureContract.{implementation,input_contract,output_contract,step_contract}`.
- `ToolContract.{implementation,input_contract,invocation_contract,output_contract}`.
- `EvidenceStimulus.historical_fixture?`.
- `EvidenceStimulus.input_artifact?`.
- `EvidenceStimulus.parameter_bytes`.
- `QualificationRunStimulus.case_stimulus`.
- `EvidenceCase.expected_projections[i]`.
- `EvidenceCase.stimulus`.
- `ProtectedTimeObservation.clock_envelope?`.
- `HistoricalFixture.fixture_bytes`.
- `HistoricalGenerator.{implementation,output_contract}`.
- `FailureEvidence.{evidence_artifact,source_identity}`.
- `DeploymentAdmissionPolicy.allowed_support_profiles[i]`.
- `DeploymentAdmissionPolicy.target_database_identities[i]`.
- `ProtectedRollbackCiphertext.artifact`.

Source: `AE:328–1018`, `AE:2024–2081`. The `PRD` registry independently
enumerates only the role-constrained reference positions it names: support-
profile components and bindings, their identity/configuration/procedure/tool
roles, clock and live-projection roles, selected attestation identities and
bindings, the policy's profile and target members, and failed-result evidence
and candidate projection. Other references in this roster retain their base
typed-`REF` requirement or their independently enumerated `QEQ`/`DEQ` relation;
they are not advertised as `PRD` coverage. Structural `EvidenceRef` validity
never substitutes for a named semantic role check.

### Target, payload, ciphertext, and preimage members

Owner/operation: target profiler, candidate constructor, plan issuer, protected
journal constructor, mutator, verifier, and resolver.

Root references are:

- `TargetSurfaceContract.target_database_identity`;
- `TargetCohortMembership.target_database_identity` in each selected or
  preserved membership;
- `TargetCohortProjection.target_database_identity` in each selected or
  preserved projection;
- `TargetMutationImage.target_database_identity`;
- `TargetApplyPayload.target_database_identity`;
- `TargetRestorePayload.target_database_identity`; and
- `OperationGrant.target_database_identity`.

Each is exactly one typed target-database `REF`. The nested target structures,
values, ordering, and protected copies are enumerated later.

`RestorePayloadConversion` has common members
`{conversion_algorithm="FIELDWISE_TARGET_RESTORE_V1",kind,restore_payload,restore_payload_digest,schema_version=1,source_byte_length,source_plaintext_digest}`
and exactly one source branch:

- successor:
  `{source_format="SUCCESSOR_TARGET_RESTORE_PAYLOAD_V1",source_reader_registry_member_digest="NONE",source_typed_body:TargetRestorePayloadEvidenceRef,source_wire_canonicalization_contract="hindsight-postgresql-publication-canonical-json/v1"}`;
- legacy:
  `{source_format="LEGACY_SELECTED_ROW_PREIMAGE_V1",source_reader_registry_member_digest:Digest,source_typed_body:LegacyRestoreContentEvidenceRef,source_wire_canonicalization_contract="hindsight-operation-recovery-encrypted-preimage-canonical-json-no-lf/7b165b3"}`.

`restore_payload` is exactly one `TargetRestorePayload/v1` `REF` and
`restore_payload_digest=restore_payload.body_digest`. Successor source bytes
are the referenced body bytes including LF. Legacy source bytes are exact
no-LF historical plaintext selected by the frozen reader member. Both branches
bind exact byte length and plaintext digest. The fieldwise conversion copies
target, surface, relation, column, row-key, cohort membership, and values;
permitted integer/time/base64url encodings are deterministic, and unsupported,
normalized, missing, extra, duplicate, or lossy conversion refuses
(`AE:1265–1290`, `AE:1878–1904`).

`RollbackPreimageBinding` has exactly
`{authority="NONE",ciphertext,conversion,decryption_procedure,kind,lineage_key_digest,restore_payload,restore_payload_digest,schema_version=1,selected_cohort_digest,target_database_identity,target_surface_digest}`.
The reference domains are
`ciphertext:ProtectedRollbackCiphertext/v1` in the `CIPHERTEXT` domain,
`conversion:RestorePayloadConversion/v1`,
`decryption_procedure:ProcedureContract/v1` with `procedure_kind=DECRYPTION`,
`restore_payload:TargetRestorePayload/v1`, and
`target_database_identity:TARGET_DATABASE`. The binding's target, surface,
lineage key, selected-cohort digest, payload reference, and payload digest
equal the conversion output. The protected ciphertext digest and length bind
the retained encrypted bytes; decryption must reproduce the conversion's
source digest and length (`AE:1291–1308`, `AE:1905–1924`).

`ProtectedRollbackCiphertext` has exactly
`{artifact,authority="NONE",byte_length,ciphertext_digest,kind="hindsight-postgresql-protected-rollback-ciphertext",retention="THROUGH_VERIFIED_ROLLBACK_OR_AUTHORIZED_RETIREMENT",schema_version=1,storage_class="PROTECTED_POSTGRESQL_BYTES"}`.
Its `artifact` is an exact `ImmutableArtifact/v1` descriptor. The protected
byte row has separate digest-and-length identity and never enters ordinary
body storage. `OperationGrantRevocation.grant` is exactly one
`OperationGrant/v1` `REF`.

Source: `AE:997–1329`, `AE:1772–1924`, `AE:2679–2692`,
`AE:5800–5920`; `PD:503–566`.

### Operation authority and durable work members

Owner/operation: authority issuer/approver/authorizer/revoker and protected work boundaries.

- `OperationPlan.target_database_identity`.
- `OperationPlan.action_binding.apply_payload` in the apply branch.
- `OperationPlan.action_binding.{budget_limits,grant,reconciliation_limits,retry_limits,rollback_preimage_binding}` in every applicable action branch.
- `OperationPlan.action_binding.{final_manifest,manifest_approval}` only in
  `LEGACY_COMPLETE_APPLY`.
- `OperationApproval.plan`.
- `OperationAuthorizationReceipt.{approval,plan}`.
- `OperationAuthorityRevocation.{grant,plan}`.
- `OperationAuthorityRevocation.approval?` — `NONE` for `PLAN_ISSUED`; exact
  approval for `APPROVED` and `AUTHORIZED`.
- `OperationAuthorityRevocation.authorization_receipt?` — `NONE` for
  `PLAN_ISSUED` and `APPROVED`; the exact authorization receipt for
  `AUTHORIZED`.
- `PreStageExpiryObservation.{approval,authorization_receipt,clock_envelope,plan}`.
- `StageAttemptWorkIdentity.aggregate_identity` — embedded planned-request or
  committed-`J` identity; no generic reference.
- `TransactionResolutionWorkIdentity.transaction_identity`.
- `AmbiguityQueryWorkIdentity.transaction_identity`.
- `ReconciliationWorkIdentity.subject`.
- `TransactionReconciliationSubject.{original_reservation,original_start,original_transaction_identity}`.
- `FenceReconciliationSubject.{active_fence_binding,target_database_identity}`.
- `PublicationQualificationAttemptReconciliationSubject.{original_reservation,original_start,original_transaction_identity}`.
- `TerminalOutcomeReconciliationSubject.terminal_result`.
- `OperationWorkRequest.plan`.
- `OperationWorkPreflightResult.committed_result?`.
- `OperationWorkPreflightResult.request`.
- `OperationWorkPreReservationRefusal.{evidence,request}`.
- `OperationWorkReservation.{boot_identity,clock_envelope,plan,request,reserved_at}`.
- `TransactionIdentity.{plan,target_database_identity}`.
- `OperationWorkStart.{reservation,transaction_identity}`.
- `OperationWorkCommittedResult.{plan,reservation,result,start,transaction_identity}`.
- `OperationWorkTransactionResolutionOutcome.{original_committed_result,original_reservation,original_result,original_start,original_transaction_identity,resolution_reservation,resolution_start,resolution_transaction_identity}`.
- `OperationWorkAmbiguityQueryOutcome.{original_committed_result,original_reservation,original_result,original_start,original_transaction_identity,query_reservation,query_start,query_transaction_identity}`.
- `OperationWorkConclusiveNoncommitResult.{original_reservation,original_start,original_transaction_identity,reconciliation_subject,resolution_evidence,resolution_reservation,resolution_start,resolution_transaction_identity}`.
- `OperationAccountingState.plan`.

Source: `AE:1389–1745`, `AE:2410–2764`.

### Qualification and deployment-admission members

Owner/operation: plan acceptance, registrar, qualification finalizer, profiler, and admission finalizer.

- `ControllerHostBinding.{boot_configuration,host_identity,operating_system_profile}`.
- `PostgresqlHostBinding.{boot_configuration,host_identity,operating_system_profile,postgresql_profile,storage_profile}`.
- `PostgresqlEndpointBinding.{endpoint_identity,target_database_identity}`.
- `DeploymentTopologyBinding.{controller_host,postgresql_endpoint,postgresql_host}`.
- `DeploymentTopologyBinding.network_path_identity?`.
- `SupportProfile.{boot_configuration,clock_profile,closure_policy_limits,cold_recovery_procedure,controller_host,deployment_topology,failure_injector,filesystem_profile,hardware_profile,operating_system_profile,postgresql_endpoint,postgresql_host,postgresql_profile,storage_profile,virtualization_profile}`.
- `ClockEnvelope.{boot_identity,clock_profile,host_identity,synchronization_epoch}`.
- `QualificationPlan.cells[i].{acquisition_procedure,case_matrix,conformance_prestate?,limits,randomized_schedule?,tool}`.
- `QualificationPlan.cells[i].independent_oracles[j]`.
- `QualificationPlan.{abort_policy,acceptance_thresholds,closure_policy_limits,cold_recovery_procedure,environment_reset_procedure,evidence_retention_policy,failure_injector,subject_revision,support_profile}`.
- `QualificationPlan.planned_runs[i]` expands through every
  `CampaignRunRequirement` reference path listed below.
- `QualificationPlanAcceptance.{plan,support_profile}`.
- `QualificationClassResult.cell_results[i].run_results[j]`.
- `QualificationClassResult.clock_epochs[i]`.
- `QualificationClassResult.{plan,support_profile}`.
- `QualificationReceipt.{capability_result,clock_result,closure_policy_limits,issuance_time_observation,physical_durability_result,plan,plan_acceptance,support_profile}`.
- `QualificationReceipt.tier_results[i]`.
- `DeploymentAttestation.{boot_identity,clock_envelope,closure_policy_limits,controller_host,deployment_admission_policy,deployment_campaign,deployment_topology,endpoint_identity,host_identity,issuance_time_observation,postgresql_endpoint,postgresql_host,postgresql_settings,qualification_plan,qualification_plan_acceptance,qualification_receipt,role_grant_set,storage_identity,support_profile,target_database_identity,writer_inventory}`.
- `DeploymentAttestation.deployment_tier_results[i]`.

Source: `AE:2774–2988`, `AE:4380–4915`.

### Campaign, oracle, evidence, and verdict members

Owner/operation: plan acceptance, campaign/record registrar, evaluator, selector, and disposition applier.

- `CampaignOracleRequirement.claim_predicates[i]`.
- `CampaignOracleRequirement.{expected_projection,oracle_contract}`.
- `CampaignRunRequirement.{acquisition_procedure,conformance_prestate?,limits,reader_execution?,stimulus,tool}`.
- `CampaignRunRequirement.claim_definitions[i]`.
- `CampaignRunRequirement.claim_predicates[i]`.
- `CampaignRunRequirement.oracles[i].claim_predicates[j]`.
- `CampaignRunRequirement.oracles[i].{expected_projection,oracle_contract}`.

- `CanonicalClaimRegistry.planned_runs[i]`.
- `CanonicalDeploymentMatrix.planned_runs[i]`.
- `EvidenceCampaignPlan.planned_runs[i]`.
- `EvidenceCampaign.planned_runs[i]`.
- `QualificationPlan.planned_runs[i]`.

- `ClaimRegistryPlanBasis.registry`.
- `QualificationPlanBasis.{plan,plan_acceptance}`.
- `HistoricalCorpusPlanBasis.{coverage_projection,plan,plan_acceptance}`.
- `DeploymentPolicyPlanBasis.{deployment_matrix,policy}`.
- `CompositeCampaignPlanBasis.members[0]` — the complete claim-registry basis.
- `CompositeCampaignPlanBasis.members[1]` — the complete historical-corpus
  basis.
- `EvidenceCampaignPlan.subject_revision`.
- `EvidenceCampaignPlanAcceptance.plan`.
- `EvidenceCampaign.{campaign_plan,campaign_plan_acceptance,start_time_observation,subject_revision}`.

Source for those campaign-plan, acceptance, campaign, and registration paths:
`AE:3702–3738`, `AE:5147–5184`.

- `CanonicalClaimDefinition.predicates[i]`.
- `CanonicalClaimRegistry.claim_definitions[i]`.
- `CanonicalClaimRegistry.claim_predicates[i]`.
- `CanonicalClaimRegistry.support_profiles[i]`.
- `CanonicalClaimRegistry.target_database_identities[i]`.
- `CanonicalDeploymentMatrix.claim_definitions[i]`.
- `CanonicalDeploymentMatrix.claim_predicates[i]`.
- `CanonicalDeploymentMatrix.deployment_policy`.
- `CanonicalDeploymentMatrix.support_profiles[i]`.
- `CanonicalDeploymentMatrix.target_database_identities[i]`.
- `OracleClaimObligation.claim_definition`.
- `OracleClaimObligation.claim_predicates[i]`.
- `OracleDefinition.claim_obligations[i].claim_definition`.
- `OracleDefinition.claim_obligations[i].claim_predicates[j]`.
- `CanonicalOracleRegistry.definitions[i]`.
- `OracleContract.{definition,independent_implementation,oracle_registry}`.
- `OracleProjection.claim_predicates[i]`.
- `OracleProjection.{oracle_contract,oracle_definition}`.
- `OracleProjection.fields[i].value` when the exact field kind admits one
  `EvidenceRef`.
- `OracleProjection.fields[i].value[j]` when the exact field kind admits a
  sequence of `EvidenceRef`.

- `EvidenceRecord.{acquisition_procedure,campaign,completion_time_observation,conformance_prestate?,deployment_evidence_acquisition?,expected_projection,limits,observed_projection,oracle_contract,real_artifact_binding?,reader_execution?,start_time_observation,subject_revision,tool}`.
- `EvidenceRecord.claim_definitions[i]`.
- `EvidenceRecord.claim_predicates[i]`.
- `EvidenceRunFailure.{campaign,completion_time_observation,conformance_prestate?,deployment_evidence_acquisition?,expected_projection,oracle_contract,real_artifact_binding?,start_time_observation}`.
- `EvidenceRunFailure.observed_projection?` — one `REF` or the literal
  `NO_LIVE_PROJECTION`.
- `QualificationClockEpoch.{boot_identity,clock_envelope,plan,predecessor_clock_epoch?}`.
- `EvidencePhaseClockBinding.{boot_identity,clock_envelope,clock_epoch?,time_observation}`.
- `EvidenceRecordRegistrationInput.{deployment_evidence_acquisition?,expected_projection,observed_projection,oracle_contract,start_time_observation}`.
- `EvidenceRunFailureRegistrationInput.{deployment_evidence_acquisition?,expected_projection,oracle_contract}`.
- `EvidenceRunFailureRegistrationInput.observed_projection?` — one `REF` or
  `NO_LIVE_PROJECTION`.
- `EvidenceRunRegistrationInput.campaign`.
- `EvidenceRunRegistrationInput.records[i]` expands through every
  `EvidenceRecordRegistrationInput` member above.
- `EvidenceRunRegistrationInput.failure?` expands through every
  `EvidenceRunFailureRegistrationInput` member above.
- `EvidenceRunRegistrationInput.retained_artifacts[i]`.
- `EvidenceRunRegistrationInput.start_time_observation`.
- `EvidenceRunResult.{campaign,completion_time_observation,conformance_prestate?,failure_evidence?,real_artifact_binding?,reader_execution?,start_time_observation,subject_revision}`.
- `EvidenceRunResult.claim_definitions[i]`.
- `EvidenceRunResult.claim_predicates[i]`.
- `EvidenceRunResult.clock_bindings[i].{boot_identity,clock_envelope,clock_epoch?,time_observation}`.
- `EvidenceRunResult.evidence_records[i]`.
- `EvidenceInvalidityFinding.{evidence,expected_projection,observed_projection,oracle_contract}`.
- `EvidenceTierState.{campaign?,claim_definition?,claim_predicate?,predecessor_result?,selection_disposition?,subject_revision}`.
- `EvidenceTierState.invalidations[i]`.
- `EvidenceTierState.prerequisite_results[i]`.
- `EvidenceTierState.run_results[i]`.
- `EvidenceTierResult.{campaign?,claim_definition?,claim_predicate?,predecessor_result?,selection_disposition?,subject_revision}`.
- `EvidenceTierResult.invalidations[i]`.
- `EvidenceTierResult.prerequisite_results[i]`.
- `EvidenceTierResult.run_results[i]`.

Source: `AE:2990–4018`, `AE:4264–4311`, `AE:5136–5720`.

### Historical-plan and private-review members

Owner/operation: historical acceptance/registrar and isolated private-evidence owner.

- `PrivateEvidenceArtifactMember.artifact` — `PRIVATE`; one per package
  artifact.
- `PrivateArtifactProvenance.artifact` — `PRIVATE`; exactly one.
- `PrivateArtifactProvenance.sanitization_procedure?` — present only for the
  accepted sanitized-real branch.
- `PrivateArtifactProvenance.source_acquisition?` — present only for the
  accepted sanitized-real branch.
- `RealArtifactBinding.{artifact,private_artifact_policy,provenance,public_projection_policy}` — `PRIVATE`; exactly one each.
- `ControlledPrivateEvidencePackage.artifacts[i].artifact` — `PRIVATE`.
- `ControlledPrivateEvidencePackage.{deciding_run_result,limits,real_artifact_binding,subject_revision}` — `PRIVATE`.
- `BoundedPublicEvidenceProjection.{limits,public_subject_identity}` — public
  ordinary-body references; no private package reference or digest.
- `IndependentEvidenceReviewReceipt.{limits,public_projection,public_subject_identity,reviewer_identity}` — public ordinary-body references; no private
  package reference or digest.

- `HistoricalFixturePlanCell.{acquisition_procedure,expected_projection,fixture,limits,reader_execution,successor_projection_contract}`.
- `HistoricalGeneratorPlanCell.{acquisition_procedure,expected_result_oracle,generator,limits,reader_execution,successor_projection_contract}`.
- `HistoricalRealEvidencePlanCell.{acquisition_procedure,limits,reader_execution,successor_projection_contract}`.
- `HistoricalRealEvidencePlanCell.expected_projections[i]`.
- `HistoricalRealEvidencePlanCell.real_artifact_bindings[i]`.
- `HistoricalRealEvidencePlanCell.stimuli[i]`.
- `HistoricalReaderExecutionBinding.{implementation,input_contract,invocation_contract,output_contract,reader_tool}`.
- `HistoricalArtifactCoverage.{coverage_source,reader_execution}`.
- `HistoricalBoundaryCoverage.reader_execution`.
- `HistoricalGeneratorCoverage.{generator,reader_execution}`.
- `HistoricalSeedCoverage.reader_execution`.
- `HistoricalBudgetCoverage.reader_execution`.
- `HistoricalShrinkCoverage.reader_execution`.
- `HistoricalPolicyCoverage.policy`.
- `HistoricalCorpusCoverageProjection.reader_executions[i]`.
- Each coverage collection element expands through the exact corresponding
  coverage record above.
- `HistoricalCorpusPlan.{coverage_projection,expected_result_oracle,private_or_sanitized_artifact_policy,public_projection_policy,reader_registry_body}`.
- `HistoricalCorpusPlan.reader_executions[i]`.
- `HistoricalCorpusPlan.fixture_cells[i]` expands through
  `HistoricalFixturePlanCell`.
- `HistoricalCorpusPlan.generator_cells[i]` expands through
  `HistoricalGeneratorPlanCell`.
- `HistoricalCorpusPlan.real_evidence_cells[i]` expands through
  `HistoricalRealEvidencePlanCell`.
- `HistoricalCorpusPlanAcceptance.{coverage_projection,plan}`.
- `FailedDeploymentResult.{candidate_projection,campaign,subject_revision}`.
- `FailedDeploymentResult.failures[i].evidence`.
- `FailedDeploymentResult.{qualification_plan?,qualification_plan_acceptance?,qualification_receipt?,support_profile?}`.

Source: `AE:842–949`, `AE:2166–2205`, `AE:4022–4260`, `AE:5426–5578`.

#### Revision-pinned historical-reader inventory

Disposition is `SOURCE_ONLY`: the authenticated closed dispatcher owns
interpretation, but every selector member is fixed before that disposition.
Define exact literal prefixes
`H="operation-recovery-"` and
`E="operation-recovery-exact-drain-"`. `E·x` and `H·x` are literal
concatenation, never family patterns.

A registry member is generated only by this total constructor:

`R(kind,role,artifact_schema,reference_schema,variant,wire) := {
  artifact_kind: kind,
  authenticated_dependency_role: role,
  artifact_schema_version: artifact_schema,
  reference_plan_schema_version: reference_schema,
  artifact_or_reference_plan_variant: variant,
  protocol_family: "hindsight-private-file-operation-recovery",
  protocol_version: 1,
  wire_canonicalization_contract: wire,
  reader_contract_id: CID(selector of the preceding eight fields),
  source_revision: "7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab"
}`

Exactly one of `kind` and `role` is non-`NONE`. `K(k,a,r,v,w)` means
`R(k,NONE,a,r,v,w)`; `A(d,a,r,v,w)` means
`R(NONE,d,a,r,v,w)`. `CID(S)` is
`"hindsight-private-file-operation-recovery-reader/v1/sha256/"` followed by
the 64 lowercase hexadecimal characters of SHA-256 over complete
compatibility-canonical `ReaderSelector S` bytes including one LF. Equal
selectors have one ID; any changed field changes the preimage. These
constructors fully specify all ten fields of every member below
(`CD:160–227`, `CD:1027–1049`).

Wire constants are:

- `D="hindsight-operation-recovery-canonical-json-lf-sha256/7b165b3"`;
- `P="hindsight-operation-recovery-progress-compact-ascii-json-no-lf/7b165b3"`;
- `N="hindsight-operation-recovery-encrypted-preimage-canonical-json-no-lf/7b165b3"`.

`D` is strict historical canonical JSON plus one LF. `P` is the pinned
compact ASCII JSON wire with no LF, while its embedded semantic digest remains
historical canonical JSON. `N` is exact decrypted canonical plaintext with no
LF. Raw ciphertext has separate raw identity and is never passed to a generic
reader (`CD:184–194`, `CD:272–287`).

Define the closed plan-variant function:
`V(12)={phase-repair-v8,phase-repair-v9}`;
`V(15)={provider-capability,legacy-hatchery-capability}`; and
`V(s)={NONE}` for
`s∈{1,2,3,4,5,6,7,8,9,10,11,13,14,16,17}`.
A displayed finite set or comma-separated list expands to one `K` member for
each listed value and each member of `V(reference_schema)`; there are no
numeric ranges or implicit variants.

| Exact artifact kind | Artifact schema(s) | Reference schema(s) | Variant(s) | Wire |
| --- | --- | --- | --- | --- |
| `E·plan` | `1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17` | `NONE` | `V(artifact schema)` | `D` |
| `E·authorization-receipt` | `1` | `1,2,3,4,5,6,7,8,9,10,11,12,13,14` | `V(reference schema)` | `D` |
| `E·authorization-receipt` | `2` | `15` | `V(15)` | `D` |
| `E·authorization-receipt` | `3` | `16,17` | `NONE` | `D` |
| `E·application-journal` | `1` | `1,2,3,4,5,6,7,8,9,10,11,12,13,14` | `V(reference schema)` | `D` |
| `E·application-journal` | `2` | `15,16,17` | `V(reference schema)` | `D` |
| `E·progress` | `1` | `1,2,3,4,5` | `NONE` | `P` |
| `E·progress` | `2` | `6,7` | `NONE` | `P` |
| `E·progress` | `3` | `8,9,10` | `NONE` | `P` |
| `E·progress` | `4` | `11` | `NONE` | `P` |
| `E·progress` | `5` | `12,13,14` | `V(reference schema)` | `P` |
| `E·progress` | `6` | `15` | `V(15)` | `P` |
| `E·progress` | `7` | `16,17` | `NONE` | `P` |
| `E·status` | `1` | `1,2,3,4,5,6,7,8,9,10,11,15` | `V(reference schema)` | `D` |
| `E·status` | `2` | `12,13,14,16,17` | `V(reference schema)` | `D` |
| `E·application-receipt` | `1` | `1,2,3,4,5,6,7,8,9,10,11,12,13,14` | `V(reference schema)` | `D` |
| `E·application-receipt` | `2` | `15,16,17` | `V(reference schema)` | `D` |
| `E·verification-receipt` | `1` | `1,2,3,4,5,6,7,8,9,10,11,12,13,14` | `V(reference schema)` | `D` |
| `E·verification-receipt` | `2` | `15,16,17` | `V(reference schema)` | `D` |

For the `E·plan` row only, `reference_schema=NONE` and `variant` is selected
from `V(artifact_schema)`. Every other row uses its displayed reference
schema. This is the complete exact-drain matrix (`CD:288–320`).

The kindless requeue-plan reader is exactly
`A(requeue-plan,1,NONE,NONE,D)`. Its derived contract ID is
`hindsight-private-file-operation-recovery-reader/v1/sha256/3f2089bacd91e4591d7a5939cc274d7ca7ae6600466718504b1a6c5102b58245`.
It accepts only the parent-authenticated `requeue-plan` edge, the closed
schema-1 key set, and `action="requeue-operation-cohort"`. At a discovery
root or another role it is `KNOWN_UNDISPATCHABLE` (`CD:229–270`).

Let
`STOP={(1,16,NONE),(2,15,provider-capability),(2,15,legacy-hatchery-capability)}`.
For every exact kind below and every tuple `(a,r,v)∈STOP`, the registry
contains `K(kind,a,r,v,D)`:

- `E·stopped-run-reconciliation-plan`;
- `E·stopped-recovery-handoff`;
- `H·stopped-run-reconciliation-authorization-receipt`;
- `H·stopped-run-reconciliation-encrypted-rollback-bundle`;
- `H·stopped-run-reconciliation-application-journal`;
- `E·interrupted-attempts-receipt`;
- `E·stopped-run-receipt`;
- `H·stopped-run-reconciliation-application-receipt`;
- `H·stopped-run-reconciliation-verification-receipt`;
- `H·stopped-run-reconciliation-rollback-authorization-receipt`;
- `H·stopped-run-reconciliation-rollback-journal`; and
- `H·stopped-run-reconciliation-rollback-receipt`.

The transient `H·stopped-run-encrypted-rollback-bundle` helper label is not a
member. The accepted kindful stopped-run dependencies emit these selectors:

- for every `(a,r,v)∈STOP`,
  `K(H·stopped-run-reconciliation-postimage,a,r,v,D)`;
- `K(H·stopped-run-process-quiescence-evidence,1,NONE,NONE,D)`;
- for every `(_,r,v)∈STOP`,
  `K(H·stopped-run-provider-quiescence-evidence,1,r,v,D)` and
  `K(H·stopped-run-terminal-absence-evidence,1,r,v,D)`;
- `K(H·stopped-run-backup-attestation,1,NONE,NONE,D)`;
- `K(H·stopped-run-datastore-identity,1,NONE,NONE,D)`; and
- for every `(_,r,v)∈STOP`,
  `K(H·stopped-run-selected-row-preimage,1,r,v,N)`.

Their exact parent-role edges are:
`stopped-run-reconciliation-plan.process_quiescence`,
`.provider_quiescence`, `.terminal_absence`, `.backup_attestation`, and
`.datastore_identity` for one member each per plan;
the `postimage` reader-input role for the stopped-run receipt and
application/verification readers, whose bodies bind its
`postimage_digest`; and the `selected-row-preimage` post-decryption role
jointly authenticated by the stopped plan and reconciliation bundle's
`ciphertext_sha256` for the one successfully decrypted preimage. The plan's
`rollback_backup` and the encrypted preimage bytes remain kindless
`OPAQUE_DEPENDENCY` values with raw identity only. The full kind tokens,
schemas, and parent fields are fixed by the revision-pinned contracts selected
at `CD:322–371`; the decrypted preimage alone uses `N`, while every JSON body
uses `D`. The exact kind tokens and source-parent fields are pinned by
[the stopped-run contracts](https://github.com/nisavid/agents/blob/7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab/tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py#L14585-L15931);
this source anchor supplies grammar facts, not integration-base selection.

Grant retirement has these complete registry seeds:

- for `E·grant-retirement-plan`:
  `K(kind,1,16,NONE,D)`,
  `K(kind,2,15,provider-capability,D)`, and
  `K(kind,2,15,legacy-hatchery-capability,D)`;
- for each of `E·grant-retirement-authorization-receipt`,
  `E·grant-complete-preimage-archive`,
  `E·grant-revoked-ledger-archive`, `E·grant-retirement-journal`, and
  `E·grant-retirement-receipt`:
  `K(kind,1,1,NONE,D)`,
  `K(kind,1,2,provider-capability,D)`, and
  `K(kind,1,2,legacy-hatchery-capability,D)`; and
- for `E·grant-history-resolution`:
  `K(kind,1,NONE,retired-archive,D)` and
  `K(kind,1,NONE,current-fixed-slot,D)`.

The first child-reference schema is the exact retirement-plan artifact schema,
not its nested exact-drain reference schema. The registry also contains these
five kindful grant-plan members:
`K(E·authorization-grant-plan,1,12,phase-repair-v9,D)`,
`K(E·authorization-grant-plan,1,13,NONE,D)`,
`K(E·authorization-grant-plan,1,14,NONE,D)`,
`K(E·authorization-grant-plan,1,16,NONE,D)`, and
`K(E·authorization-grant-plan,1,17,NONE,D)`.

The five grant-plan members above are the complete grant-plan selector set.
The immediate-reference rule then adds exactly these nine selectors:

- `K(E·authorization-grant,1,1,phase-repair-v9,D)` and
  `K(E·authorization-grant,1,1,NONE,D)`. The populated reference schema is
  the immediate `grant_plan` artifact schema 1. The first row is selected only
  when that immediate grant-plan member carries its assigned
  `phase-repair-v9` variant; the other four admitted grant-plan members carry
  `NONE`. Their distinct nested exact-drain schemas 13, 14, 16, and 17 remain
  in the recursively resolved grant-plan selectors rather than being flattened
  into the grant selector.
- `K(E·authorization-grant-ledger,1,NONE,NONE,D)`. The ledger has no
  reference-plan component. Its embedded `grant` is a kindful authenticated
  dependency, not a value for `reference_plan_schema_version`.
- `K(E·authorization-grant-claim,1,15,provider-capability,D)` and
  `K(E·authorization-grant-claim,1,15,legacy-hatchery-capability,D)`. A
  schema-1 claim directly embeds one schema-15 exact-drain `plan`, whose two
  accepted grammars supply the displayed variant.
- `K(E·authorization-grant-claim,2,16,NONE,D)` and
  `K(E·authorization-grant-claim,2,17,NONE,D)`. A schema-2 claim directly
  embeds one schema-16 or schema-17 exact-drain `plan`; neither row has a
  second grammar.
- `K(E·authorization-grant-close,1,NONE,NONE,D)`. A close has
  `plan_digest` and `claim_record_digest` but no embedded reference-plan body.
- `K(E·authorization-grant-revocation,1,NONE,NONE,D)`. A revocation has
  `grant_digest` and the ledger-chain fields but no embedded reference-plan
  body.

Together with the five grant-plan rows, this family contributes exactly
14 selector identities and therefore 14 constructor-derived
`reader_contract_id` values. A transitive exact-drain plan schema does not
replace the immediate schema 1 in a grant selector or populate a ledger,
close, or revocation selector. No descriptive lineage label becomes an
artifact kind or variant.

The authenticated closure preserves the deeper contexts through these exact
parent-role edges:

- `authorization-grant-plan.reference_plan` resolves the exact-drain plan and
  selects the one matching grant-plan row above.
- `authorization-grant.grant_plan` resolves that exact schema-1 grant-plan
  member; `grant_plan_digest` and `approval_digest` equal its digest. The
  grant selector uses `phase-repair-v9` exactly for the immediate member that
  carries that variant and `NONE` for each other admitted member.
- `authorization-grant-ledger.grant` resolves the one grant member;
  `grant_digest` equals it.
- Each `authorization-grant-ledger.use_records[i]` is exactly one claim or
  close in strict sequence. A claim's embedded `plan` selects one of the four
  claim members above and its `plan_digest` equals that plan. A close selects
  the single close member and authenticates its source plan only transitively
  through the immediately preceding claim named by `plan_digest` and
  `claim_record_digest`.
- `authorization-grant-ledger.revocation?` is literal `NONE` or the single
  revocation member. A present revocation binds the ledger grant through
  `grant_id` and `grant_digest` and binds the immediately prior ledger record
  through `prior_record_digest`.
- Grant, claim, close, and revocation `grant_id`/`grant_digest` fields equal
  the ledger's embedded grant; every claim, close, and revocation record has
  the required next `sequence` and exact prior-record digest.

A historical registry binding, execution binding, success/failure body, and
corpus member for this family must use the `ReaderRegistryMember` derived from
the applicable one of those 14 selectors and retain the entire recursive
parent-role closure above. It must not substitute the deeper grant-plan
`reference_plan` schema or variant into another artifact's selector. These
assignments follow the selector's immediate-reference rule and kindful
dependency rule (`CD:396–415`, `CD:1395–1409`,
`CD:3643–3651`). The pinned bodies establish the immediate fields:
[grant plan and grant](https://github.com/nisavid/agents/blob/7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab/tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py#L7524-L7875),
[ledger and claim](https://github.com/nisavid/agents/blob/7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab/tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py#L7882-L8085), and
[close and revocation](https://github.com/nisavid/agents/blob/7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab/tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py#L8088-L8228).

Claim release has parent seed set:

`RELEASE={
  (H·claim-release-plan,2,1,legacy-requeue),
  (E·claim-release-plan,3,15,retry-claim),
  (E·claim-release-plan,4,15,terminal-claim)
}`.

Each tuple means `K(kind,artifact_schema,reference_schema,variant,D)`. For each
parent `(_,a,_,v)` and every child kind below, emit
`K(child,1,a,v,D)`; the child reference schema is the immediate parent
artifact schema `a∈{2,3,4}`:

- `H·claim-release-authorization-receipt`;
- `H·claim-release-encrypted-rollback-bundle`;
- `H·claim-release-application-journal`;
- `H·claim-release-application-receipt`;
- `H·claim-release-verification-receipt`;
- `H·claim-release-rollback-journal`; and
- `H·claim-release-rollback-receipt`.

The post-decryption body has three explicit members:
`K(H·claim-release-preimage,1,2,legacy-requeue,N)`,
`K(H·claim-release-preimage,1,3,retry-claim,N)`, and
`K(H·claim-release-preimage,1,4,terminal-claim,N)`. For each, the exact parent
post-decryption reader-input role `claim-release-preimage` is jointly
authenticated by the matching claim-release plan and encrypted rollback
bundle's `ciphertext_sha256`; cardinality is one plaintext
body for an admitted decryption branch. The schema-1 ciphertext remains
`OPAQUE_DEPENDENCY` and has no reader selector (`CD:417–441`).

Shared-lifecycle outer-plan members are the exact `K(kind,a,r,v,D)` Cartesian
expansion of this table:

| Exact outer plan kind | Artifact schemas | Reference schemas | Variants |
| --- | --- | --- | --- |
| `E·post-abort-plan` | `1,2,3,4,5,6,7,8,9` | `1,2,3,4,5,6,7,8,9,14,16,17` | `NONE` |
| `E·post-abort-plan` | `10` | `1,2,3,4,5,6,7,8,9,10,13,14,16,17` | `NONE` |
| `E·post-abort-plan` | `11` | `1,2,3,4,5,6,7,8,9,10,11,13,14,16,17` | `NONE` |
| `E·post-abort-plan` | `11` | `12` | `phase-repair-v8,phase-repair-v9` |
| `E·post-abort-plan` | `11` | `15` | `provider-capability,legacy-hatchery-capability` |
| `E·post-abort-plan` | `12` | `11` | `NONE` |
| `E·post-abort-plan` | `12` | `12` | `phase-repair-v8,phase-repair-v9` |
| `E·post-abort-plan` | `12` | `15` | `provider-capability,legacy-hatchery-capability` |
| `E·post-terminal-reconciliation-plan` | `13` | `12` | `phase-repair-v8,phase-repair-v9` |
| `E·post-terminal-reconciliation-plan` | `13` | `15` | `provider-capability,legacy-hatchery-capability` |
| `E·stopped-failed-reset-plan` | `14` | `16` | `NONE` |
| `E·stopped-failed-reset-plan` | `14` | `15` | `provider-capability,legacy-hatchery-capability` |

The multiplication in a row is literal; every artifact schema pairs with every
reference schema and every displayed variant.

Define these 15 immediate contexts as exact pairs
`(reference_schema,variant)`:
`(1,legacy-requeue)`;
`(s,post-abort)` for each
`s∈{1,2,3,4,5,6,7,8,9,10,11,12}`;
`(13,post-terminal-reconciliation)`; and
`(14,stopped-failed-reset)`.
For every context and each exact shared child
`H·authorization-receipt`, `H·encrypted-rollback-bundle`,
`H·application-journal`, `H·application-receipt`,
`H·rollback-journal`, and `H·rollback-receipt`, emit
`K(child,1,reference_schema,variant,D)`. Also emit
`K(H·verification-receipt,1,1,legacy-requeue,D)` for the requeue context and
`K(H·post-abort-verification-receipt,1,r,v,D)` for each of the other
14 contexts. The two schema-1 members at reference schema 1 and variants
`legacy-requeue` and `post-abort` remain distinct.

For the same 15 immediate contexts, the decrypted schema-1
`H·selected-row-preimage` has one explicit member
`K(H·selected-row-preimage,1,reference_schema,variant,N)`. Its exact parent
post-decryption reader-input role is `selected-row-preimage`, jointly
authenticated by the shared-lifecycle plan and encrypted rollback bundle's
`ciphertext_sha256`; one plaintext member exists when that branch
decrypts successfully. The ciphertext remains one kindless
`OPAQUE_DEPENDENCY` and is never dispatched. The classifier-only member is
exactly
`K(E·stopped-run-durable-start-pending,1,NONE,two-field,D)`
(`CD:443–532`).

For every generated member, encode complete bytes with one LF, reject duplicate
selectors and same-selector different bytes, sort by unsigned lexicographic
complete member bytes, and hash their separator-free concatenation for
`member_vector_digest`. `FrozenReaderRegistry.members` is that exact sequence;
its count, vector digest, source revision, and complete-body digest must match.
A range, omitted tuple, wrong wire, changed reference schema, altered variant,
or different `CID` changes or invalidates the registry (`CD:251–270`).

For every generated member, `HistoricalReaderRegistryBinding` contains exactly
`{artifact_kind,artifact_or_reference_plan_variant,artifact_schema_version,authenticated_dependency_role,protocol_family,protocol_version,reader_contract_id,reader_registry_member_digest,reference_plan_schema_version,source_revision,wire_canonicalization_contract}`.
`HistoricalReaderExecutionBinding` contains exactly
`{implementation,implementation_source_revision,input_contract,invocation_contract,kind,output_contract,reader,reader_contract_id,reader_tool,reader_tool_id,schema_version,wire_canonicalization_contract}` and is keyed by
`reader.reader_registry_member_digest`. Tool, implementation, four contract
references, reader ID, wire, and source revision must equal the selected
member. `LegacyReaderSuccess` and `LegacyReaderFailure` repeat that binding,
selected member, member digest, contract ID, wire, source revision, raw
identity, and complete authenticated dependency closure. Runs, outputs,
corpus coverage, and reader registry all preserve the same canonical member
order; the dispatcher receives only the exact selector and bytes
(`CD:184–270`, `CD:546–612`, `AE:6762–6872`).

### Evidence-disposition members

Owner/operation: finding registrar for nonauthorizing findings; disposition applier for authority effects.

- `EvidenceRecordInvalidationAuthoritySubject.{campaign,evidence,invalidity_evidence,subject_revision}`.
- `EvidenceCampaignSupersessionAuthoritySubject.{campaign,prior_result,replacement_campaign,subject_revision}`.
- `EvidenceRecordInvalidation.{application_time_observation,authority_receipt,campaign,evidence,invalidity_evidence,subject_revision}`.
- `EvidenceCampaignSupersession.{application_time_observation,authority_receipt,campaign,prior_result,replacement_campaign,subject_revision}`.

Source: `AE:7039–7202`.

### Publication-stage and recovery members

Owner/operation: stage, mutation, verification, terminal-close, and recovery boundaries.

- `FreshStageAdmission.deployment_attestation`.
- `CompatibilityStageAdmission.{active_fence_manifest_binding,deployment_attestation}`.
- `ApplyBinding.{apply_payload,budget_limits,grant,reconciliation_limits,retry_limits,rollback_preimage_binding}`.
- `SuccessorRollbackBinding.{budget_limits,grant,reconciliation_limits,retry_limits,rollback_preimage_binding}`.
- `LegacyRollbackBinding.{budget_limits,final_manifest,grant,manifest_approval,reconciliation_limits,retry_limits,rollback_preimage_binding}`.
- `J.{approval,authorization_receipt,plan,pre_stage_expiry_observation,target_database_identity}`.
- `J.admission` expands through the selected admission variant above.
- `J.action_binding` expands through exactly one action-binding variant above.
- `P.pre_stage_expiry_observation`.
- `P.admission` expands through the selected admission variant.
- `R.clock_envelope`.
- `R.admission` expands through the selected admission variant.
- `M.admission` expands through the selected admission variant.
- `V.target_database_identity`.
- `MismatchObservation.target_database_identity`.
- `UnableObservation.{failure_evidence,target_database_identity}`.
- `TerminalVerificationFailure.{failure_evidence,target_database_identity}`.
- `RecoveryRefusalObservation.{evidence,reservation,transaction_identity}`.
- `RecoveryRefusalObservation.reconciliation_subject?`.
- `RecoveryAmbiguityObservation.{reservation,subject_transaction_identity,transaction_identity}`.
- `RecoveryFenceObservation.{deployment_attestation,reconciliation_subject,reservation,transaction_identity}`.
- `RecoveryAdvancementObservation.{reservation,result_body,transaction_identity}`.
- `RecoveryAdvancementObservation.reconciliation_subject?`.
- `RecoveryUnprovenObservation.{original_r_committed_result,original_r_conclusive_noncommit_result,original_r_reservation,original_r_start,original_r_transaction_identity,reconciliation_subject,reservation,resolution_evidence,transaction_identity}`.

Source: `AE:5729–6272`.

### Compatibility discovery, closure, fence, and activation members

Owner/operation: compatibility inventory/closure, fence, adoption, admission, and activation boundaries.

- `CanonicalLineageKeyBody.target_database_identity`.
- `LocatorObservation.descriptor`.
- `DiscoveryRoot.derivation_contract`.
- `InventoryObservation.locator.descriptor`.
- `InventoryObservation.reader_output?`.
- `InventoryObservation.failure_evidence?`.
- `InventoryObservation.closure_observation?`.
- `DependencyEdge.dependency_locator.descriptor`.
- `LegacyReaderSuccess.reader_execution_binding`.
- `LegacyReaderSuccess.restore_content?`.
- `LegacyReaderFailure.{failure_evidence,reader_execution_binding}`.

- `QuiescencePredicate.derivation_contract`.
- `WriterFenceProposal.{role_grant_set,target_partition_proof,realization_policy,writer_inventory}`.
- `WriterFenceProposal.quiescence_predicates[i].derivation_contract`.
- `EpochActivationProposal.deployment_attestation`.
- `FencePredicateObservation.derivation_contract`.
- `RealizedAdmissionEvidence.realization_policy`.
- `RealizedAdmissionEvidence.observations[i].derivation_contract`.
- `RealizedAclEvidence.realization_policy`.
- `RealizedAclEvidence.observations[i].derivation_contract`.
- `ZeroLiveWriterEvidence.realization_policy`.
- `ZeroLiveWriterEvidence.observations[i].derivation_contract`.
- `ServiceDisableEvidence.{realization_policy,disable_attestation}`.
- `ServiceDisableEvidence.predicate_observation.derivation_contract`.
- `PersistentLegacyFenceEvidence.realization_policy`.
- `OriginFenceManifestBinding.deployment_attestation`.
- `ActiveFenceManifestAdoption.deployment_attestation`.

- `ClosureCaseBinding.{deployment_attestation,qualified_clock_envelope,closure_policy_limits}`.
- `ClosureQualifiedSampleEvidence.{deployment_attestation,qualified_clock_envelope}`.
- `ClosureAttestedInvalidationEvidence.{deployment_attestation,fresh_qualified_sample}`.
- `ClosureUnableFailureBranch.timing_evidence`.
- `ClosureDeadlineFailureBranch.timing_evidence`.
- `ClosureMatchObservation.{timing_evidence,comparison_evidence}`.
- `ClosureMismatchObservation.{timing_evidence,comparison_evidence}`.
- `ClosureUnableObservation.{timing_evidence,failure_evidence}`.
- `ClosureDeadlineAbandonmentObservation.{timing_evidence,failure_evidence}`.

- `ManifestBasis.qualified_clock_envelope`.
- `ManifestBasis.upper_bound_derivation_contract`.
- `ManifestBasis.epoch_activation_proposal.deployment_attestation`.
- `ManifestBasis.discovery_roots[i].derivation_contract`.
- `ManifestBasis.inventory_observations[i].locator.descriptor`.
- `ManifestBasis.inventory_observations[i].{reader_output?,failure_evidence?,closure_observation?}`.
- `ManifestBasis.dependency_edges[i].dependency_locator.descriptor`.
- `ManifestBasis.writer_fence_proposal.{role_grant_set,target_partition_proof,realization_policy,writer_inventory}`.
- `ManifestBasis.writer_fence_proposal.quiescence_predicates[i].derivation_contract`.
- `ArtifactExclusion.target_overlap_evidence?`.
- `ArtifactExclusion.supporting_evidence`.
- `FinalManifest.exclusion_bindings[i].exclusion_body.locator.descriptor`.
- `FinalManifest.exclusion_bindings[i].exclusion_body.target_overlap_evidence?`.
- `FinalManifest.exclusion_bindings[i].exclusion_body.supporting_evidence`.
- `FinalManifest.predecessor_selection.encrypted_preimage_identity` only in
  `LEGACY_COMPLETE_APPLY`; `NONE` has no hidden member.

Source: `CD:1027–1950`, `CD:2451–3344`.

## Relational keys, nested members, predicates, and copies

### Identity and nested source paths

Every ordinary body and every `REF` uses the indivisible typed identity
`{contract_kind,contract_version,body_digest}`; semantic keys below are
additional invariants. Private packages and ciphertext use distinct identities.

`PreStageExpiryObservation.admission` is exactly
`FreshStageAdmission.{active_fence_manifest_binding="NONE",admission_generation,deployment_attestation,mode="FRESH",publication_epoch}`
or
`CompatibilityStageAdmission.{active_fence_manifest_binding,admission_generation,deployment_attestation,mode="COMPATIBILITY",publication_epoch}`.
The observation's complete paths are
`{admission,approval,approval_expiry_unix_ns,authority="NONE",authorization_receipt,clock_envelope,elapsed_upper_ns,forward_rate_error_denominator,forward_rate_error_numerator,forward_rate_error_upper_ns,kind,monotonic_anchor_lower_ns,monotonic_sample_upper_ns,monotonic_validity_deadline_lower_ns,observation_request_id,plan,qualification,schema_version,stage,stage_predecessor_digest,trusted_upper_bound_unix_ns,wall_upper_at_anchor_unix_ns}`
(`AE:1721–1743`, `AE:5729–5748`).

For every
`CanonicalClaimPredicate.run_predicates[i].oracle_requirements[j].expected_fields[k]`
and `OracleProjection.fields[k]`, member identity includes exact `name`,
`value_kind`, and the branch-selected `value`. The 22-value closed mapping is:

| `value_kind` | Exact `value` domain and cardinality |
| --- | --- |
| `BOOLEAN` | one JSON boolean, `false` or `true` |
| `SAFE_INTEGER` | one `SafeInteger` |
| `UINT128` | one `UInt128String` |
| `DIGEST` | one `Digest` |
| `ID` | one `Id` |
| `CONTRACT_ID` | one `ContractId` |
| `TEXT` | one `Text` |
| `ENUM_TOKEN` | one `OracleEnumToken` |
| `ENUM_TOKEN_OR_NONE` | one `OracleEnumToken` or literal `"NONE"` |
| `DIGEST_OR_NONE` | one `Digest` or literal `"NONE"` |
| `EVIDENCE_REF` | one typed `EvidenceRef` |
| `EVIDENCE_REF_OR_NONE` | one typed `EvidenceRef` or literal `"NONE"` |
| `LEGACY_READER_OUTPUT_REF` | one typed `LegacyReaderSuccess/v1` or `LegacyReaderFailure/v1` `EvidenceRef` |
| `TARGET_APPLY_PAYLOAD_REF_OR_NONE` | one typed `TargetApplyPayload/v1` `EvidenceRef` or literal `"NONE"` |
| `RECOVERY_AGGREGATE_IDENTITY` | one embedded `RecoveryAggregateIdentity`, exactly `COMMITTED_J.{identity_kind,journal_digest}` or `ABSENT_REQUEST.{identity_kind,request_key_digest}` |
| `TARGET_ALLOWED_DELTA` | one embedded `TargetAllowedApplyDelta` or `TargetAllowedRollbackDelta` |
| `SAFE_INTEGER_SEQUENCE` | ordered `value[i]:SafeInteger`, including the source-permitted empty sequence |
| `DIGEST_SEQUENCE` | ordered `value[i]:Digest`, including the source-permitted empty sequence |
| `CONTRACT_ID_SEQUENCE` | ordered `value[i]:ContractId`, including the source-permitted empty sequence |
| `TEXT_SEQUENCE` | ordered `value[i]:Text`, including the source-permitted empty sequence |
| `EVIDENCE_REF_SEQUENCE` | ordered `value[i]:EvidenceRef`, including the source-permitted empty sequence |
| `NONE` | literal `"NONE"` only |

Exactly one tag and its corresponding arm are present; omission, another arm,
a wrong reference kind/version, a scalar substituted for a sequence, or
reordered sequence is invalid (`AE:3016–3035`, `AE:3740–3759`,
`AE:4264–4311`, `AE:6748–6850`).

`InventoryObservationIdentity` is exactly
`{root_locator,root_derivation_contract,relative_locator,byte_state,byte_length,source_sha256}`.
`root_derivation_contract` copies `DiscoveryRoot.derivation_contract`.
`byte_length` and `source_sha256` are present for `READABLE_STABLE` and
`DRIFTED` and both literal `"NONE"` for `UNREADABLE`. Its deterministic ID
does not omit the copied derivation-contract reference (`CD:1171–1184`,
`CD:1478–1495`).

`AuthorityGateFixtureState` expands these finite typed members:

- `absence_markers[i].{relation,row_key_digest,state="ABSENT"}`;
- `accounting_values[i].{plan,state}`, with
  `state.{charged_elapsed_ns,charged_mutated_rows,charged_preserved_rows,charged_reconciliation_ns,charged_selected_rows,j_attempts,m_attempts,next_reservation_ordinal,p_attempts,plan,r_attempts,reconciliation_attempts,verification_attempts}`;
- `capability_values[i].{capability_bytes_base64url,capability_digest,session_witness_bytes_base64url,value_id}`;
- `clock_values[i].{clock_envelope,monotonic_sample_lower_ns,monotonic_sample_upper_ns,trusted_upper_bound_unix_ns,value_id}`;
- `current_slots[i].{slot_class,slot_key,slot_key_digest,value}` through all
  21 exact slot variants below;
- `lineage_values[i].{target_database_identity,target_surface_digest,lineage}`,
  with `lineage.{lineage_generation,lineage_key_digest,predecessor_m_digest,predecessor_v_digest}`;
- `revocation_values[i].{current_state,revocation?,subject}`, where
  `current_state=CURRENT` requires `revocation="NONE"` and
  `current_state=REVOKED` requires the exact typed revocation reference; and
- `seeded_rows[i].{body,relation,row_key_digest}`.

`AuthorityGateConformancePrestate` has these exact members and branches:

- required scalars/references:
  `authority="NONE"`, `cell_id:ContractId`,
  `fixture_state:AuthorityGateFixtureState/v1 REF`,
  `isolation="DISPOSABLE_CONFORMANCE_SCHEMA"`,
  `kind="hindsight-postgresql-authority-gate-conformance-prestate"`,
  `schema_version=1`, `support_profile:SupportProfile/v1 REF`,
  `target_database_identity:TARGET_DATABASE REF`, and
  `target_surface_digest:Digest`;
- `current_tier_results[i]:EvidenceTierResult/v1 REF` is an ordered sequence,
  one member per required current tier-result binding in source order;
- eight independent optional paths, each exactly literal `"NONE"` or its typed
  value:
  `active_fence_binding?:OriginFenceManifestBinding/v1 or ActiveFenceManifestAdoption/v1 REF`,
  `deployment_attestation?:DeploymentAttestation/v1 REF`,
  `deployment_policy?:DeploymentAdmissionPolicy/v1 REF`,
  `operation_approval?:OperationApproval/v1 REF`,
  `operation_authorization_receipt?:OperationAuthorizationReceipt/v1 REF`,
  `operation_plan?:OperationPlan/v1 REF`,
  `publication_epoch?:SafeInteger`, and
  `qualification_receipt?:QualificationReceipt/v1 REF`; and
- `gate` is exactly one of
  `J`, `P`, `R`, `M`, `V`, `COMBINED_ACTIVATION`,
  `QUALIFICATION_FINALIZER`, `DEPLOYMENT_FINALIZER`, `LEGACY_FENCE`, or
  `OPERATION_RECONCILIATION`.

Every value branch has the corresponding seeded row/current slot or explicit
absence marker. An optional reference cannot be replaced by an empty body,
bare digest, or missing member (`AE:2082–2094`, `AE:3088–3663`).

### Production stable, unique, current, and conflict keys

These are production identities. They do not include the conformance fixture's
`key_kind` or `slot_class` unless the accepted production body itself does.

| Contract or operation | Exact production stable key, uniqueness, or preimage |
| --- | --- |
| `ContractBody` | stable `(owner_class,owner_id,contract_role)` |
| `HistoricalReaderExecutionBinding` | stable `reader.reader_registry_member_digest` |
| `OperationAccountingState` current row | current `plan` |
| work preflight/result lookup | `(plan,work_identity_digest)` |
| `OperationWorkPreReservationRefusal` | stable `(plan,request_key_digest)`; no reservation key |
| `OperationWorkReservation` | stable `(plan,work_identity_digest)` |
| `TransactionIdentity` | stable `(plan,work_identity_digest)`; `transaction_identity_id` separately unique |
| `OperationWorkStart` current marker | current `(plan,work_identity_digest)` |
| `OperationWorkCommittedResult` | stable `(plan,work_identity_digest)` |
| `OperationWorkTransactionResolutionOutcome` | stable `resolution_reservation` |
| `OperationWorkAmbiguityQueryOutcome` | stable `query_reservation` |
| `OperationGrant` current slot | current `grant_id` |
| operation-authority current slot | current `plan` |
| `OperationApproval` | stable `plan` |
| `OperationAuthorizationReceipt` | stable `(plan,approval)` |
| `EvidenceCampaignPlanAcceptance` | stable `plan` |
| `QualificationPlanAcceptance` | stable `plan` |
| `HistoricalCorpusPlanAcceptance` | stable `plan` |
| `QualificationClassResult` | stable `(plan,evidence_class)` |
| `QualificationReceipt` immutable body | stable `plan` |
| `EvidenceRunResult` | stable `(campaign,run_id)` |
| `EvidenceRecord` | stable `(campaign,run_id,oracle_id)` |
| `FailedDeploymentResult` | stable `(campaign,deployment_attempt_id)` |
| `EvidenceTierResult` immutable body | stable `(claim_id,tier,subject_revision,evidence_state_digest)` |
| evidence-tier current selector | current `(claim_id,tier)` |
| `EvidenceRecordInvalidation` | stable `evidence` |
| `EvidenceCampaignSupersession` | stable `(campaign,claim_id,tier,prior_result)` |
| each `RecoveryObservation` union body | stable `reservation` |
| verification attempt (`V`, mismatch, unable, or terminal failure) | stable `(digest(M),verification_attempt_id)` |
| `PreStageExpiryObservation` | stable `(stage,plan,approval,authorization_receipt,admission.publication_epoch,stage_predecessor_digest,observation_request_id)` |
| `ProtectedTimeObservation` start-phase slot | stable `(phase,subject_key_digest)` |
| `ControlledPrivateEvidencePackage` | stable `(package_id,deciding_run_result)` |
| `BoundedPublicEvidenceProjection` | stable `public_record_id`; `independent_review_id` separately unique |
| `IndependentEvidenceReviewReceipt` | stable `(review_id,public_projection)` |
| `J` aggregate | stable `(operation_identity,action_binding.action,plan.body_digest,admission.publication_epoch)` |
| journal-preimage adoption | stable `digest(J)`; value is exactly `(J.action_binding.rollback_preimage_binding, resolved ProtectedRollbackCiphertext/v1 REF)` |
| protected rollback ciphertext byte row | stable `(ciphertext_digest,byte_length)`; value is exactly those retained bytes |
| `RelationIdentity`, `DiscoveryRoot`, `DigestBinding`, `DependencyEdge` | respectively `relation_oid`, `discovery_root_id`, `name`, `dependency_edge_id` |
| `InventoryObservation` | deterministic `inventory_observation_id` over complete `InventoryObservationIdentity` |
| `ClosureCaseBinding` | stable `closure_case_id`; separately unique complete case key `(target_surface,source_inventory_observation_id,source_chain_root_digest,observed_generation,closure_policy_limits)` |
| `ClosureObservation` | stable deterministic `closure_observation_id`; stable attempt `(closure_case_digest,attempt_ordinal)`; separately unique request `(closure_case_digest,observation_request_id)` |
| compatibility `RoleIdentity` | stable `(postgres_role_oid,target_surface_digest)` |
| `WriterServiceIdentity` body set | stable `service_id` |
| `FencePredicateObservation` | stable `predicate_id` |
| `ServiceDisableEvidence` | stable `service.service_id` |
| `PersistentLegacyFenceEvidence` | stable `(target_database_identity,target_surface_digest,fence_generation)` |
| `OriginFenceManifestBinding` | stable `(target_database_identity,target_surface_digest,fence_generation,0)` |
| `ActiveFenceManifestAdoption` | stable `(target_database_identity,target_surface_digest,fence_generation,adoption_generation)` |
| current legacy-fence binding | current `(target_database_identity,target_surface_digest)` |
| `RawIdentityMember`, `HistoricalIdentityMember`, `ReaderContractMember` | each stable `inventory_observation_id` |
| `ExclusionBinding` | stable `exclusion_body.exclusion_id` |
| `ReferencedEvidence` | stable `(contract_kind,contract_version,body_digest)` |
| exclusion approval receipt | stable `approval_digest` |
| reserved activation | current `(target_database_identity,target_surface_digest)` plus unique numeric `publication_epoch` |

The `J` transaction inserts the finalized journal, the journal-preimage
adoption, and the verified digest-and-length ciphertext-byte adoption
atomically. A deferred exact reference/uniqueness constraint plus commit-time
totality makes all three durable or all absent. An adoption cannot exist
without its matching `J`, a `J` cannot exist without both adoptions, and a
ciphertext body cannot stand in for the exact byte row (`AE:5881–5902`).

`closure_case_digest` is SHA-256 over the complete canonical
`ClosureCaseBinding` bytes including LF. `ClosureObservationIdentity` is
exactly
`{closure_case_digest,observation_request_id,attempt_ordinal}`.
The protected reservation canonicalizes that three-field object including LF,
hashes it, and derives `closure_observation_id` as lowercase RFC 4122 UUIDv5
in URL namespace `6ba7b811-9dad-11d1-80b4-00c04fd430c8` with ASCII name
`https://github.com/nisavid/agents/tooling/hindsight/closure-observation/v1#sha256=`
plus the 64 lowercase digest characters. That UUID is the only valid
`closure_observation_id`, and callers never choose it. The source fixes
`closure_case_id` as a stable identity with the complete uniqueness key shown
above but does not fix who assigns it or how it is derived (`CD:2260–2365`).

Every immutable key admits only byte-identical replay; changed kind, bytes,
branch, nested value, or reference conflicts. Every current key locks one
complete typed value and uses its source-defined expected-current CAS;
value-internal identities equal the key. Source-defined exceptions remain
distinct: accounting initializes every counter to zero and next ordinal 1;
refusal has no reservation key; a different-request result is `UNRESOLVED`;
transaction ID is separately unique; tier subject revision is value, not
current key; `VERIFICATION_UNABLE` is nonterminal; committed-stage replay does
not resample pre-stage time; private/ciphertext identity never aliases
ordinary-body identity; compatibility origin/adoption and closure attempts
retain generation/ordinal rules. Evidence:
`AE:1772–1782`, `AE:2151–2165`, `AE:2405–2772`,
`AE:4394–4571`, `AE:5147–5720`, `AE:5800–5920`,
`AE:6057–6718`, `AE:7039–7476`; `CD:662–694`,
`CD:1386–1495`, `CD:2260–2365`, `CD:2617–2825`.

### Production current-selector classes

Exactly 21 typed current classes exist. `ABSENT` is always the sole
`{presence="ABSENT"}` arm. A present arm is exactly the domain shown:

| Slot class | Exact production selector | Exact present value |
| --- | --- | --- |
| `ACTIVE_EPOCH` | `(target_database_identity,target_surface_digest)` | `{presence="PRESENT",value:SafeInteger,value_kind="ACTIVE_EPOCH"}` |
| `ACTIVATION_CAPABILITY` | `(target_database_identity,target_surface_digest,publication_epoch)` | `{presence="PRESENT",value:Digest,value_kind="ACTIVATION_CAPABILITY_DIGEST"}` |
| `ACTIVATION_PROPOSAL` | `(target_database_identity,target_surface_digest,publication_epoch)` | `{deployment_attestation:DeploymentAttestation/v1 REF,manifest_body_digest:Digest,presence="PRESENT",publication_epoch:SafeInteger,value_kind="ACTIVATION_PROPOSAL"}` |
| `CLOCK_ENVELOPE` | `(target_database_identity,target_surface_digest)` | `{presence="PRESENT",value:ClockEnvelope/v1 REF,value_kind="CLOCK_ENVELOPE_REF"}` |
| `DEPLOYMENT_ATTESTATION` | `(target_database_identity,target_surface_digest,publication_epoch)` | `{presence="PRESENT",value:DeploymentAttestation/v1 REF,value_kind="DEPLOYMENT_ATTESTATION_REF"}` |
| `DEPLOYMENT_POLICY` | `(target_database_identity,target_surface_digest)` | `{presence="PRESENT",value:DeploymentAdmissionPolicy/v1 REF,value_kind="DEPLOYMENT_POLICY_REF"}` |
| `EVIDENCE_TIER_RESULT` | `(claim_id,tier)` | `{presence="PRESENT",value:EvidenceTierResult/v1 REF,value_kind="EVIDENCE_TIER_RESULT_REF"}` |
| `LEGACY_FENCE` | `(target_database_identity,target_surface_digest)` | `{presence="PRESENT",value:OriginFenceManifestBinding/v1 or ActiveFenceManifestAdoption/v1 REF,value_kind="LEGACY_FENCE_REF"}` |
| `LINEAGE_HEAD` | `(target_database_identity,target_surface_digest)` | `{presence="PRESENT",value:Digest,value_kind="LINEAGE_HEAD_DIGEST"}` |
| `OPERATION_ACCOUNTING` | `plan` | `{presence="PRESENT",value:OperationAccountingState,value_kind="OPERATION_ACCOUNTING_STATE"}` |
| `OPERATION_AUTHORITY` | `plan` | `{presence="PRESENT",value:OperationAuthorizationReceipt/v1 REF,value_kind="OPERATION_AUTHORIZATION_RECEIPT_REF"}` |
| `OPERATION_GRANT` | `grant_id` | `{presence="PRESENT",value:OperationGrant/v1 REF,value_kind="OPERATION_GRANT_REF"}` |
| `OPERATION_WORK_RESERVATION` | `(plan,work_identity_digest)` | `{presence="PRESENT",value:OperationWorkReservation/v1 REF,value_kind="OPERATION_WORK_RESERVATION_REF"}` |
| `OPERATION_WORK_START` | `(plan,work_identity_digest)` | `{presence="PRESENT",value:OperationWorkStart/v1 REF,value_kind="OPERATION_WORK_START_REF"}` |
| `OPERATION_WORK_COMMITTED_RESULT` | `(plan,work_identity_digest)` | `{presence="PRESENT",value:OperationWorkCommittedResult/v1 REF,value_kind="OPERATION_WORK_COMMITTED_RESULT_REF"}` |
| `PUBLICATION_EPOCH_HIGH_WATER` | `(target_database_identity,target_surface_digest)` | `{presence="PRESENT",value:SafeInteger,value_kind="PUBLICATION_EPOCH_HIGH_WATER"}` |
| `QUALIFICATION_RECEIPT` | `(target_database_identity,target_surface_digest)` | `{presence="PRESENT",value:QualificationReceipt/v1 REF,value_kind="QUALIFICATION_RECEIPT_REF"}` |
| `RESERVED_ACTIVATION` | `(target_database_identity,target_surface_digest)` | `{deployment_attestation:DeploymentAttestation/v1 REF,presence="PRESENT",publication_epoch:SafeInteger,reservation_state="RESERVED_FENCED",value_kind="RESERVED_ACTIVATION"}` |
| `ROLE_GRANT_SET` | `(target_database_identity,target_surface_digest)` | `{presence="PRESENT",value:RoleGrantSet/v1 REF,value_kind="ROLE_GRANT_SET_REF"}` |
| `TARGET_GENERATION` | `(target_database_identity,target_surface_digest)` | `{presence="PRESENT",value:SafeInteger,value_kind="TARGET_GENERATION"}` |
| `WRITER_INVENTORY` | `(target_database_identity,target_surface_digest)` | `{presence="PRESENT",value:WriterInventory/v1 REF,value_kind="WRITER_INVENTORY_REF"}` |

The embedded `OperationAccountingState` value contains exactly
`{charged_elapsed_ns,charged_mutated_rows,charged_preserved_rows,charged_reconciliation_ns,charged_selected_rows,j_attempts,m_attempts,next_reservation_ordinal,p_attempts,plan,r_attempts,reconciliation_attempts,verification_attempts}`.
Every present value's target, surface, epoch, plan, work, claim, tier, or grant
identity equals its selector. Work values retain the complete checked
`work_identity` in the referenced body; the selector digest is not a
replacement for that identity. Source: `AE:2096–2138`, `AE:2492–2572`,
`AE:3088–3561`.

### Conformance-slot preimages

AuthorityGate uses six distinct canonical base objects:

- `AuthorityGateTargetSurfaceSlotKey={key_kind="TARGET_SURFACE",target_database_identity,target_surface_digest}`;
- `AuthorityGatePublicationEpochSlotKey={key_kind="PUBLICATION_EPOCH",publication_epoch,target_database_identity,target_surface_digest}`;
- `AuthorityGateClaimTierSlotKey={claim_id,key_kind="CLAIM_TIER",tier}`;
- `AuthorityGateGrantSlotKey={grant_id,key_kind="GRANT"}`;
- `AuthorityGatePlanSlotKey={key_kind="PLAN",plan}`; and
- `AuthorityGateOperationWorkSlotKey={key_kind="OPERATION_WORK",plan,work_identity,work_identity_digest}`.

Each concrete preimage adds exactly one literal `slot_class`. Base membership is:

- target surface:
  `ACTIVE_EPOCH`, `CLOCK_ENVELOPE`, `DEPLOYMENT_POLICY`, `LEGACY_FENCE`,
  `LINEAGE_HEAD`, `PUBLICATION_EPOCH_HIGH_WATER`,
  `QUALIFICATION_RECEIPT`, `RESERVED_ACTIVATION`, `ROLE_GRANT_SET`,
  `TARGET_GENERATION`, and `WRITER_INVENTORY`;
- publication epoch:
  `ACTIVATION_CAPABILITY`, `ACTIVATION_PROPOSAL`, and
  `DEPLOYMENT_ATTESTATION`;
- claim tier: `EVIDENCE_TIER_RESULT`;
- grant: `OPERATION_GRANT`;
- plan: `OPERATION_ACCOUNTING` and `OPERATION_AUTHORITY`; and
- operation work: `OPERATION_WORK_RESERVATION`, `OPERATION_WORK_START`,
  and `OPERATION_WORK_COMMITTED_RESULT`.

`slot_key_digest` is SHA-256 over the complete successor-canonical concrete
preimage including one LF. The operation-work preimage retains both the full
`work_identity` and its digest; it is a conformance selector preimage, not the
production row key. For each slot class the `value` union is exactly
`AuthorityGateAbsentValue` or the corresponding present object in the
21-row table above. The source type mapping is:
`ACTIVE_EPOCH→AuthorityGateActiveEpochValue`,
`ACTIVATION_CAPABILITY→AuthorityGateActivationCapabilityValue`,
`ACTIVATION_PROPOSAL→AuthorityGateActivationProposalValue`,
`CLOCK_ENVELOPE→AuthorityGateClockEnvelopeValue`,
`DEPLOYMENT_ATTESTATION→AuthorityGateDeploymentAttestationValue`,
`DEPLOYMENT_POLICY→AuthorityGateDeploymentPolicyValue`,
`EVIDENCE_TIER_RESULT→AuthorityGateEvidenceTierResultValue`,
`LEGACY_FENCE→AuthorityGateLegacyFenceValue`,
`LINEAGE_HEAD→AuthorityGateLineageHeadValue`,
`OPERATION_ACCOUNTING→AuthorityGateAccountingSlotValue`,
`OPERATION_AUTHORITY→AuthorityGateOperationAuthorityValue`,
`OPERATION_GRANT→AuthorityGateOperationGrantValue`,
`OPERATION_WORK_RESERVATION→AuthorityGateOperationWorkReservationValue`,
`OPERATION_WORK_START→AuthorityGateOperationWorkStartValue`,
`OPERATION_WORK_COMMITTED_RESULT→AuthorityGateOperationWorkCommittedResultValue`,
`PUBLICATION_EPOCH_HIGH_WATER→AuthorityGatePublicationEpochHighWaterValue`,
`QUALIFICATION_RECEIPT→AuthorityGateQualificationReceiptValue`,
`RESERVED_ACTIVATION→AuthorityGateReservedActivationValue`,
`ROLE_GRANT_SET→AuthorityGateRoleGrantSetValue`,
`TARGET_GENERATION→AuthorityGateTargetGenerationValue`, and
`WRITER_INVENTORY→AuthorityGateWriterInventoryValue`.
No generic value or alternative key grammar exists (`AE:3088–3561`).

### Work, authority, qualification, and evidence predicates

Five work-identity branches are exact:

- stage:
  `{aggregate_identity,attempt_ordinal,identity_kind,invocation,lineage_predecessor_digest,predecessor_stage_digest,stage,stage_attempt_id}`,
  where aggregate is
  `PLANNED_REQUEST.{identity_kind,operation_identity,publication_epoch}` or
  `COMMITTED_J.{identity_kind,journal_digest}`;
- verification:
  `{attempt_ordinal,identity_kind,invocation,mutation_receipt_digest,verification_attempt_id}`;
- transaction resolution:
  `{attempt_ordinal,identity_kind,invocation,original_work_identity_digest,stage,transaction_identity,transaction_resolution_id}`;
- ambiguity query:
  `{ambiguity_query_id,attempt_ordinal,identity_kind,invocation,original_work_identity_digest,stage,transaction_identity}`; and
- reconciliation:
  `{attempt_ordinal,identity_kind,invocation,reconciliation_id,reconciliation_kind,original_work_identity_digest,subject,subject_identity_digest}`.

Every `invocation` is
`{invocation_mode,recovery_mode,recovery_request_id}`.
A reconciliation subject resolves
`ReconciliationSubject.{kind,schema_version,subject}`, whose value is exactly
one of
`TransactionReconciliationSubject.{original_reservation,original_start,original_transaction_identity,original_work_identity_digest,stage,subject_kind}`,
`FenceReconciliationSubject.{active_fence_binding,admission_generation,publication_epoch,subject_kind,target_database_identity,target_surface_digest}`,
`PublicationQualificationAttemptReconciliationSubject.{aggregate_identity,original_reservation,original_start,original_transaction_identity,original_work_identity,original_work_identity_digest,stage,subject_kind}`, or
`TerminalOutcomeReconciliationSubject.{aggregate_identity,subject_kind,terminal_result,terminal_result_digest}`
(`AE:1369–1495`, `AE:2517–2546`).

Together with their exact reference paths in the roster, work bodies expose
these protected scalar/embedded paths:

- `OperationWorkRequest.{request_id,work_identity,work_identity_digest}`;
- `OperationWorkPreflightResult.{outcome,request_key_digest}`;
- `OperationWorkPreReservationRefusal.{accounting_state_digest,authority,refusal_code,request_key_digest}`;
- `OperationWorkReservation.{ambiguity_resolution_deadline_monotonic_ns,attempt_ordinal,charged_elapsed_ns,charged_mutated_rows,charged_preserved_rows,charged_reconciliation_ns,charged_selected_rows,reservation_ordinal,reserved_at_monotonic_upper_ns,work_identity,work_identity_digest,work_class}`;
- `TransactionIdentity.{adapter_incarnation_id,aggregate_identity,stage,transaction_identity_id,transaction_mode,work_identity,work_identity_digest}`;
- `OperationWorkStart.{adapter_incarnation_id,start_nonce,work_identity,work_identity_digest}`;
- `OperationWorkCommittedResult.{result_kind,work_identity,work_identity_digest}`;
- transaction-resolution outcome
  `{authority,original_work_identity_digest,outcome,resolution_work_identity,resolution_work_identity_digest}`;
- ambiguity-query outcome
  `{authority,original_work_identity_digest,outcome,query_work_identity,query_work_identity_digest}`;
- conclusive-noncommit result
  `{authority,original_work_identity,original_work_identity_digest,outcome,recovery_request_id,resolution_work_identity,resolution_work_identity_digest}`; and
- `OperationAccountingState.{charged_elapsed_ns,charged_mutated_rows,charged_preserved_rows,charged_reconciliation_ns,charged_selected_rows,j_attempts,m_attempts,next_reservation_ordinal,p_attempts,plan,r_attempts,reconciliation_attempts,verification_attempts}`.

All omitted `kind`/`schema_version` scalars remain in the exact canonical
body and typed ordinary-body identity; none is a separate relational operand.
`request_key_digest` hashes the complete canonical request including LF;
`work_identity_digest` hashes the selected complete identity including LF.
Different-request committed results are `UNRESOLVED`, then pass through
request-keyed reservation/refusal. Reservation, start, transaction, and result
copy one exact request/plan/identity/digest chain. Accounting begins at zero
with next ordinal 1; reservation consumes that ordinal once. Resolution/query
outcomes are nonauthorizing `ORIGINAL_COMMITTED`; conclusive close is
nonauthorizing `CONCLUSIVE_NONCOMMIT` and atomically binds both work chains
(`AE:1497–1666`, `AE:2405–2677`).

Protected limits are
`OperationRetryLimits.{kind,maximum_j_attempts,maximum_m_attempts,maximum_p_attempts,maximum_r_attempts,maximum_verification_attempts,schema_version}`,
`OperationReconciliationLimits.{kind,maximum_ambiguity_resolution_ns,maximum_reconciliation_attempts,maximum_reconciliation_duration_ns,schema_version}`, and
`OperationBudgetLimits.{j_attempt_duration_ns,kind,m_attempt_duration_ns,maximum_elapsed_ns,maximum_mutated_rows,maximum_preserved_rows,maximum_selected_rows,p_attempt_duration_ns,r_attempt_duration_ns,schema_version,verification_attempt_duration_ns}`.
Checked limits refuse before accounting changes (`AE:1337–1365`,
`AE:2369–2515`).

Together with the recovery reference paths in the roster, closed branch
scalars/embeddings are:

- refusal
  `{action,aggregate_identity,authority,kind,observed_prefix,recovery_request_id,refusal_code,schema_version,work_identity,work_identity_digest}`;
- ambiguity
  `{aggregate_identity,ambiguity_code,authority,kind,recovery_request_id,resolution_deadline_monotonic_ns,schema_version,stage,work_identity,work_identity_digest}`;
- fence
  `{admission_generation,aggregate_identity,authority,fence_reason,kind,publication_epoch,recovery_request_id,schema_version,work_identity,work_identity_digest}`;
- advancement
  `{aggregate_identity,authority,from_prefix,kind,recovery_request_id,schema_version,to_prefix,transition,work_identity,work_identity_digest}`; and
- unproven
  `{aggregate_identity,authority,kind,original_r_work_identity,original_r_work_identity_digest,recovery_request_id,result,schema_version,work_identity,work_identity_digest}`.

Their aggregate is exactly
`COMMITTED_J.{identity_kind,journal_digest}` or
`ABSENT_REQUEST.{identity_kind,request_key_digest}`, selected by durable
prefix. Reservation is the stable key and binds the exact charged chain;
result-body/replay is valid only through that reservation's committed result.
The absent request digest covers
`{action,operation_identity,plan_body_digest,publication_epoch}`
(`AE:6057–6272`).

Protected subject-key projections are exactly:
`CAMPAIGN_START.{campaign_id,campaign_plan,subject_type}`,
`EVIDENCE_RUN.{campaign,run_id,subject_type}`,
`EVIDENCE_RECORD.{campaign,oracle_id,run_id,subject_type}`,
`DEPLOYMENT_EVIDENCE_ACQUISITION.{acquisition_id,campaign,oracle_id,run_id,subject_type}`,
`OPERATION_WORK_RESERVATION.{plan,reservation_ordinal,subject_type,work_identity_digest}`,
`QUALIFICATION_RECEIPT.{qualification_plan,subject_type}`,
`DEPLOYMENT_ATTESTATION.{admission_generation,subject_type,target_database_identity,target_surface_digest}`,
`EVIDENCE_DISPOSITION.{disposition_id,subject_digest,subject_type}`, and
`SUPPORT_PROFILE_PROJECTION.{support_profile,subject_type,target_database_identity}`.
Each digest covers the complete canonical projection including LF
(`AE:2287–2360`).

Authority bodies contribute all exact references in the roster plus:
`OperationGrant.{action,expected_target_generation,grant_id,issued_at_unix_ns,issuer_principal,operation_identity,preserved_cohort_digest,publication_epoch,selected_cohort_digest,target_surface_digest,valid_until_unix_ns}`;
`OperationGrantRevocation.{reason,revocation_id,revoked_at_unix_ns,revoker_principal}`;
`OperationPlan.{action_binding,created_at_unix_ns,expected_target_generation,operation_identity,plan_issuer_principal,preserved_cohort_digest,publication_epoch,selected_cohort_digest,target_surface_digest,valid_until_unix_ns}`;
`OperationApproval.{decision,issued_at_unix_ns,operator_principal,valid_until_unix_ns}`;
`OperationAuthorizationReceipt.{authorization_principal,decision,issued_at_unix_ns,valid_until_unix_ns}`; and
`OperationAuthorityRevocation.{expected_state,reason,revocation_id,revoked_at_unix_ns,revoker_principal}`.
Grant/plan copy action, operation, target, surface, epoch, generation, cohorts,
and one deadline. Issuance is strictly
`grant < plan < approval < authorization < deadline`; revocation branches
are `PLAN_ISSUED:(NONE,NONE)`, `APPROVED:(approval,NONE)`, and
`AUTHORIZED:(approval,authorization)`, atomically terminal
(`AE:1310–1335`, `AE:1669–1719`, `AE:2693–2772`).

`DeploymentAdmissionPolicy` is exactly
`{allowed_release_digests,allowed_support_profiles,attestation_validity_ns,effective_from_unix_ns,kind,maximum_deployment_evidence_age_ns,policy_id,required_deployment_claim_ids,schema_version,target_database_identities,target_surface_digests,valid_until_unix_ns}`.
Its identity, time bounds, allowed sets, required claims, evidence age, and
attestation duration are protected admission operands (`AE:997–1009`).

`QualificationClassResult` copies
`{plan,evidence_class,cell_results,campaign_id,support_profile,release_digest,clock_epochs,started_at_unix_ns,completed_at_unix_ns,result}`.
`campaign_id=QualificationPlan.campaign_id`. `cell_results` is the complete
plan-cell partition; every `QualificationCellResult.run_results` is the exact
ordered terminal run-result sequence for that cell. `clock_epochs` is the duplicate-free first-use sequence obtained from the
class's ordered run results, including their bound run, record, and acquisition
observations; it is not merely a reference-roster entry.
`QualificationReceipt` copies
`{plan,plan_acceptance,support_profile,clock_result,physical_durability_result,capability_result,tier_results,closure_policy_limits,issuance_time_observation,qualification_id,release_digest,issued_at_unix_ns,valid_until_unix_ns,result}`.
Its three class references are exactly `EV-CLK`, `EV-PHY`, and `EV-CAP`;
`qualification_id=QualificationPlan.campaign_id` and
`release_digest=QualificationPlan.release_digest`; and `tier_results` is the
complete design, implementation, and release partition. Every copy is derived
inside the protected class or receipt finalizer from the exact plan,
acceptance, run results, current tier results, and protected time observation
(`AE:2916–2947`, `AE:4380–4571`).

#### Protected qualification and attestation equality registry

Each `QEQ` or `DEQ` row is an independently source-derived equality member.
Its identity is `(operation,destination,source,expansion/branch)`. The arrow
points from the protected source carrier to the required destination. `ref(X)`
has the accepted meaning at `AE:4417–4419`. These rows are requirements on
resolved source bodies, not proposed SQL columns.

Carrier notation is closed here. `S` is the exact resolved
`SupportProfile/v1`; `Q`, `A`, and `Q_R` are its
`QualificationPlan/v1`, `QualificationPlanAcceptance/v1`, and
`QualificationReceipt/v1`; `C_CLK`, `C_PHY`, and `C_CAP` are the
three exact `QualificationClassResult/v1` bodies for `EV-CLK`, `EV-PHY`,
and `EV-CAP`; and `C` ranges over those three bodies paired with their
stated class. `P` is one prerequisite-result `EvidenceRef` occurring in
one enumerated tier result. `O_Q` is the fresh
`QUALIFICATION_RECEIPT_ISSUE` observation constructed by receipt
finalization. `CURRENT_SUBJECT(tier)` and
`CURRENT_TIER(claim_id,tier)` are the exact locked protected values in the
source pseudocode.

For deployment, `D`, `P_D`, `M_D`, and `C_D` are the exact resolved
`DeploymentAttestation/v1`, `DeploymentAdmissionPolicy/v1`,
`CanonicalDeploymentMatrix/v1`, and deployment
`EvidenceCampaign/v1`. `RG` and `WI` are the recomputed
`PostgresqlRoleGrantSet/v1` and `PostgresqlWriterInventory/v1`.
`A_D` and `E_D` range, in the source-prescribed planned-run/oracle order,
over each deciding `DeploymentEvidenceAcquisition/v1` and its matching
`EvidenceRecord/v1`. `O_A` is `body(A_D.acquired_at)`; `O_I` is
`body(D.issuance_time_observation)`. `L` is
`body(current_live_projection(D))`, the exact locked
`MacosLocalPostgresqlLiveProjection/v1` used by finalization.
`LOCKED_POLICY(target,surface)`, `LOCKED_PROFILE(D)`, and
`LOCKED_CLOCK(D)` are, without creating new bodies or callables, the
source-described exact current policy, profile, and unexpired clock envelope.
The live projection has no standalone topology reference, so `DEQ018`–
`DEQ020` express its three topology members rather than inventing one.

#### Protected profile and live reference-role domain registry

An `EvidenceRef` proves a typed body and its recursive closure, but does not by
itself prove that the body serves the semantic role required at a particular
path. Each `PRD` row below is therefore one independent source member. The
protected resolver must admit the exact contract kind/version and the stated
discriminant after resolution. `NONE` is admitted only where the row names an
optional branch. A wrong kind, wrong role discriminant, unknown version,
missing body, digest mismatch, or role substitution fails before any positive
profile, qualification, or admission predicate can pass.

~~~text
PRD001 | S.boot_configuration | ProfileComponent/v1 with component_class=BOOT_ENVIRONMENT; its identity is EvidenceIdentity/v1 with identity_class=BOOT_CONFIGURATION; its configuration is BootEnvironmentConfiguration/v1 | one | AE:2024-2033
PRD002 | S.clock_profile | ProfileComponent/v1 with component_class=CLOCK; its identity is EvidenceIdentity/v1 with identity_class=CLOCK; its configuration is ClockConfiguration/v1 | one | AE:2024-2035
PRD003 | S.filesystem_profile | ProfileComponent/v1 with component_class=FILESYSTEM; its identity is EvidenceIdentity/v1 with identity_class=FILESYSTEM; its configuration is FilesystemConfiguration/v1 | one | AE:2024-2033
PRD004 | S.hardware_profile | ProfileComponent/v1 with component_class=HARDWARE; its identity is EvidenceIdentity/v1 with identity_class=HARDWARE; its configuration is HardwareConfiguration/v1 | one | AE:2024-2033
PRD005 | S.operating_system_profile | ProfileComponent/v1 with component_class=OPERATING_SYSTEM; its identity is EvidenceIdentity/v1 with identity_class=OPERATING_SYSTEM; its configuration is OperatingSystemConfiguration/v1 | one | AE:2024-2033
PRD006 | S.postgresql_profile | ProfileComponent/v1 with component_class=POSTGRESQL; its identity is EvidenceIdentity/v1 with identity_class=POSTGRESQL; its configuration is PostgresqlComponentConfiguration/v1 | one | AE:2024-2033
PRD007 | S.storage_profile | ProfileComponent/v1 with component_class=STORAGE; its identity is EvidenceIdentity/v1 with identity_class=STORAGE; its configuration is StorageConfiguration/v1 | one | AE:2024-2033
PRD008 | S.virtualization_profile | ProfileComponent/v1 with component_class=VIRTUALIZATION; its identity is EvidenceIdentity/v1 with identity_class=VIRTUALIZATION; its configuration is VirtualizationConfiguration/v1 | one | AE:2024-2033
PRD009 | S.controller_host | ControllerHostBinding/v1 | one | AE:2036-2039
PRD010 | body(S.controller_host).host_identity | EvidenceIdentity/v1 with identity_class=HOST | one | AE:2039-2042
PRD011 | body(S.controller_host).boot_configuration | BootEnvironmentConfiguration/v1; never a boot-session identity | one | AE:2044-2047
PRD012 | body(S.controller_host).operating_system_profile | ProfileComponent/v1 with component_class=OPERATING_SYSTEM | one | AE:2039-2043
PRD013 | S.postgresql_host | PostgresqlHostBinding/v1 | one | AE:2036-2039
PRD014 | body(S.postgresql_host).host_identity | EvidenceIdentity/v1 with identity_class=HOST | one | AE:2039-2042
PRD015 | body(S.postgresql_host).boot_configuration | BootEnvironmentConfiguration/v1; never a boot-session identity | one | AE:2044-2047
PRD016 | body(S.postgresql_host).operating_system_profile | ProfileComponent/v1 with component_class=OPERATING_SYSTEM | one | AE:2039-2043
PRD017 | body(S.postgresql_host).postgresql_profile | ProfileComponent/v1 with component_class=POSTGRESQL | one | AE:2039-2043
PRD018 | body(S.postgresql_host).storage_profile | ProfileComponent/v1 with component_class=STORAGE | one | AE:2039-2043
PRD019 | S.postgresql_endpoint | PostgresqlEndpointBinding/v1 | one | AE:2036-2039
PRD020 | body(S.postgresql_endpoint).endpoint_identity | EvidenceIdentity/v1 with identity_class=ENDPOINT | one | AE:2039-2043
PRD021 | body(S.postgresql_endpoint).target_database_identity | EvidenceIdentity/v1 with identity_class=TARGET_DATABASE | one | AE:2039-2043
PRD022 | S.deployment_topology | DeploymentTopologyBinding/v1 | one | AE:2036-2039
PRD023 | body(S.deployment_topology).controller_host | ControllerHostBinding/v1 | one | AE:2039-2043
PRD024 | body(S.deployment_topology).postgresql_host | PostgresqlHostBinding/v1 | one | AE:2039-2043
PRD025 | body(S.deployment_topology).postgresql_endpoint | PostgresqlEndpointBinding/v1 | one | AE:2039-2043
PRD026 | body(S.deployment_topology).network_path_identity | literal NONE, or EvidenceIdentity/v1 with identity_class=ENDPOINT | exactly one of two branches | AE:2043-2044
PRD027 | S.cold_recovery_procedure | ProcedureContract/v1 with procedure_class=COLD_RECOVERY | one | AE:2059-2060
PRD028 | S.failure_injector | ToolContract/v1 with tool_class=FAILURE_INJECTOR | one | AE:2061-2062
PRD029 | S.closure_policy_limits | ClosurePolicyLimits/v1 | one | AE:2062-2063
PRD030 | body(D.clock_envelope).clock_profile | ProfileComponent/v1 with component_class=CLOCK and the exact profile clock component | one | AE:2034-2035; AE:2278-2285
PRD031 | body(D.clock_envelope).boot_identity | EvidenceIdentity/v1 with identity_class=BOOT_ENVIRONMENT | one per boot | AE:2278-2280
PRD032 | body(D.clock_envelope).host_identity | EvidenceIdentity/v1 with identity_class=HOST | one | AE:2278-2280
PRD033 | body(D.clock_envelope).synchronization_epoch | EvidenceIdentity/v1 with identity_class=SYNCHRONIZATION_EPOCH | one | AE:2278-2280
PRD034 | L.support_profile | SupportProfile/v1 | one | AE:2048-2050
PRD035 | L.boot_identity | EvidenceIdentity/v1 with identity_class=BOOT_ENVIRONMENT | one per boot | AE:2053-2054
PRD036 | L.target_database_identity | EvidenceIdentity/v1 with identity_class=TARGET_DATABASE | one | AE:2048-2050; AE:2278-2283
PRD037 | L.controller_host | ControllerHostBinding/v1 | one | AE:2048-2050
PRD038 | L.postgresql_host | PostgresqlHostBinding/v1 | one | AE:2048-2050
PRD039 | L.postgresql_endpoint | PostgresqlEndpointBinding/v1 | one | AE:2048-2050
PRD040 | L.boot_environment_configuration | BootEnvironmentConfiguration/v1 | one | AE:2048-2052
PRD041 | L.clock_configuration | ClockConfiguration/v1 | one | AE:2048-2052
PRD042 | L.filesystem_configuration | FilesystemConfiguration/v1 | one | AE:2048-2052
PRD043 | L.hardware_configuration | HardwareConfiguration/v1 | one | AE:2048-2052
PRD044 | L.operating_system_configuration | OperatingSystemConfiguration/v1 | one | AE:2048-2052
PRD045 | L.postgresql_configuration | PostgresqlComponentConfiguration/v1 | one | AE:2048-2052
PRD046 | L.storage_configuration | StorageConfiguration/v1 | one | AE:2048-2052
PRD047 | L.virtualization_configuration | VirtualizationConfiguration/v1 | one | AE:2048-2052
PRD048 | L.collected_at | ProtectedTimeObservation/v1 with mode=QUALIFIED_CLOCK, phase=SUPPORT_PROFILE_PROJECTION, and clock_envelope equal to the exact profile clock envelope | one owner-derived observation | AE:2053-2058
PRD049 | D.boot_identity | EvidenceIdentity/v1 with identity_class=BOOT_ENVIRONMENT | one per boot | AE:2278-2281
PRD050 | D.host_identity | EvidenceIdentity/v1 with identity_class=HOST | one | AE:2278-2281
PRD051 | D.endpoint_identity | EvidenceIdentity/v1 with identity_class=ENDPOINT | one | AE:2278-2281
PRD052 | D.storage_identity | EvidenceIdentity/v1 with identity_class=STORAGE | one | AE:2278-2281
PRD053 | D.target_database_identity | EvidenceIdentity/v1 with identity_class=TARGET_DATABASE | one | AE:2278-2283
PRD054 | D.clock_envelope | ClockEnvelope/v1 with the PRD030-PRD033 roles | one exact current unexpired envelope | AE:2278-2297; AE:4648-4664
PRD055 | D.closure_policy_limits | ClosurePolicyLimits/v1 | one | AE:2284-2285
PRD056 | D.postgresql_settings | PostgresqlSettings/v1 | one | AE:2206-2212
PRD057 | P_D.allowed_support_profiles[i] | SupportProfile/v1 | one per ordered duplicate-free policy member | AE:2206-2212
PRD058 | P_D.target_database_identities[i] | EvidenceIdentity/v1 with identity_class=TARGET_DATABASE | one per ordered duplicate-free policy member | AE:2206-2212
PRD059 | FailedDeploymentResult.failures[i].evidence | FailureEvidence/v1 whose failure_code remains an independent lowercase ContractId; its evidence artifact and source identity prove the sibling uppercase DeploymentFailure.failure_code through the sibling DeploymentFailure.oracle_id, without string equality or a source-defined conversion | one per duplicate-free failure member | AE:238-239; AE:989-995; AE:2206-2214; AE:4207-4261; AE:5610-5627
PRD060 | FailedDeploymentResult.candidate_projection | OracleProjection/v1 under OR-ID containing the complete attempted deployment binding and explicit NONE values for unavailable fields | one | AE:2206-2212; AE:5610-5617
PRD061 | D.support_profile | SupportProfile/v1 | one exact current profile | AE:4648-4653; AE:4692-4697
PRD062 | D.controller_host | ControllerHostBinding/v1 | one exact stable/live binding | AE:4666-4669; AE:4698
PRD063 | D.postgresql_host | PostgresqlHostBinding/v1 | one exact stable/live binding | AE:4666-4669; AE:4699
PRD064 | D.postgresql_endpoint | PostgresqlEndpointBinding/v1 | one exact stable/live binding | AE:4666-4669; AE:4700
PRD065 | D.deployment_topology | DeploymentTopologyBinding/v1 | one exact stable/live binding | AE:4666-4669; AE:4701
~~~

The eight component rows are total over the closed `ProfileComponent`
component-class union. Rows `PRD010`, `PRD014`, `PRD020`, `PRD021`,
`PRD026`, `PRD031`–`PRD033`, `PRD035`–`PRD036`, and
`PRD049`–`PRD053` make the identity-class domain part of the member
identity; equal nested digests under another class cannot satisfy them. The
procedure, tool, and closure-policy rows likewise require their exact semantic
role, not merely a structurally valid referenced body.

A tier symbol `T(tier,claim_id)` denotes the exact
`EvidenceTierResult/v1` body in that source-defined partition; its
`claim_id` and `tier` are the two literal arguments, and its reference is
the value installed in `CURRENT_TIER(claim_id,tier)`. The partitions are the
following literal ordered sequences, not aliases for a later proposal-defined
set:

~~~text
T_DESIGN = [
  T("DESIGN","JAC-ARC-01"),
  T("DESIGN","JAC-CAN-01")
]

T_IMPLEMENTATION = [
  T("IMPLEMENTATION","JAC-ACL-01"),
  T("IMPLEMENTATION","JAC-AMB-01"),
  T("IMPLEMENTATION","JAC-CAN-01"),
  T("IMPLEMENTATION","JAC-CAP-01"),
  T("IMPLEMENTATION","JAC-CLK-01"),
  T("IMPLEMENTATION","JAC-CLO-01"),
  T("IMPLEMENTATION","JAC-CUT-01"),
  T("IMPLEMENTATION","JAC-EFX-01"),
  T("IMPLEMENTATION","JAC-EVL-01"),
  T("IMPLEMENTATION","JAC-FEN-01"),
  T("IMPLEMENTATION","JAC-ID-01"),
  T("IMPLEMENTATION","JAC-LEG-01"),
  T("IMPLEMENTATION","JAC-LIN-01"),
  T("IMPLEMENTATION","JAC-ORD-01"),
  T("IMPLEMENTATION","JAC-PG-01"),
  T("IMPLEMENTATION","JAC-PRS-01"),
  T("IMPLEMENTATION","JAC-RBK-01"),
  T("IMPLEMENTATION","JAC-RST-01"),
  T("IMPLEMENTATION","JAC-TIM-01"),
  T("IMPLEMENTATION","JAC-VER-01")
]

T_RELEASE = [
  T("RELEASE","JAC-CAP-01"),
  T("RELEASE","JAC-CLK-01"),
  T("RELEASE","JAC-CLO-01"),
  T("RELEASE","JAC-DUR-01"),
  T("RELEASE","JAC-EFX-01"),
  T("RELEASE","JAC-PRS-01")
]

T_REQUIRED = concat(T_DESIGN,T_IMPLEMENTATION,T_RELEASE)

T_DEPLOYMENT = [
  T("DEPLOYMENT","JAC-ACL-01"),
  T("DEPLOYMENT","JAC-CAP-01"),
  T("DEPLOYMENT","JAC-CLK-01"),
  T("DEPLOYMENT","JAC-CLO-01"),
  T("DEPLOYMENT","JAC-CUT-01"),
  T("DEPLOYMENT","JAC-DUR-01"),
  T("DEPLOYMENT","JAC-PG-01")
]
~~~

Within a `QEQ` or `DEQ` row, bare `T` ranges over the literal
members of the partition named by that row. Thus the qualification partition
has exactly 28 members in `DESIGN`, `IMPLEMENTATION`, then `RELEASE`
order, with ASCII claim order inside each tier; the deployment partition has
exactly seven members in the shown ASCII claim order. A repeated `claim_id`
in different tiers remains a different `(tier,claim_id)` pair. Duplicate
pairs, omissions, additions, or reordering are invalid
(`AE:4466–4512`, `AE:4789–4805`).

Qualification profile, plan, class, receipt, and current-state equalities are:

```text
QEQ001 | ACCEPT_QUALIFICATION_PLAN / FINALIZE_QUALIFICATION_RECEIPT | body(S.controller_host).operating_system_profile <- S.operating_system_profile | one | AE:4426-4427
QEQ002 | ACCEPT_QUALIFICATION_PLAN / FINALIZE_QUALIFICATION_RECEIPT | body(S.postgresql_host).operating_system_profile <- S.operating_system_profile | one | AE:4428-4429
QEQ003 | ACCEPT_QUALIFICATION_PLAN / FINALIZE_QUALIFICATION_RECEIPT | body(S.postgresql_host).postgresql_profile <- S.postgresql_profile | one | AE:4430
QEQ004 | ACCEPT_QUALIFICATION_PLAN / FINALIZE_QUALIFICATION_RECEIPT | body(S.postgresql_host).storage_profile <- S.storage_profile | one | AE:4431
QEQ005 | ACCEPT_QUALIFICATION_PLAN / FINALIZE_QUALIFICATION_RECEIPT | body(S.controller_host).boot_configuration <- body(S.boot_configuration).configuration | one | AE:4432-4433
QEQ006 | ACCEPT_QUALIFICATION_PLAN / FINALIZE_QUALIFICATION_RECEIPT | body(S.postgresql_host).boot_configuration <- body(S.boot_configuration).configuration | one | AE:4434-4435
QEQ007 | ACCEPT_QUALIFICATION_PLAN / FINALIZE_QUALIFICATION_RECEIPT | body(S.deployment_topology).controller_host <- S.controller_host | one | AE:4436
QEQ008 | ACCEPT_QUALIFICATION_PLAN / FINALIZE_QUALIFICATION_RECEIPT | body(S.deployment_topology).postgresql_host <- S.postgresql_host | one | AE:4437
QEQ009 | ACCEPT_QUALIFICATION_PLAN / FINALIZE_QUALIFICATION_RECEIPT | body(S.deployment_topology).postgresql_endpoint <- S.postgresql_endpoint | one | AE:4438
QEQ010 | ACCEPT_QUALIFICATION_PLAN | Q.support_profile <- ref(S) | one exact current profile | AE:4440
QEQ011 | ACCEPT_QUALIFICATION_PLAN | Q.release_digest <- S.release_digest | one scalar copy | AE:4441
QEQ012 | ACCEPT_QUALIFICATION_PLAN | Q.subject_revision <- CURRENT_SUBJECT(RELEASE) | one locked typed reference | AE:4442
QEQ013 | ACCEPT_QUALIFICATION_PLAN | body(Q.subject_revision).release_digest <- Q.release_digest | one scalar cross-body copy | AE:4443
QEQ014 | ACCEPT_QUALIFICATION_PLAN | Q.closure_policy_limits <- S.closure_policy_limits | one typed reference | AE:4444
QEQ015 | ACCEPT_QUALIFICATION_PLAN | A.plan <- ref(Q) | one typed reference | AE:4445
QEQ016 | ACCEPT_QUALIFICATION_PLAN | A.support_profile <- ref(S) | one typed reference | AE:4446
QEQ017 | ACCEPT_QUALIFICATION_PLAN | A.release_digest <- Q.release_digest | one scalar copy | AE:4447
QEQ018 | FINALIZE_QUALIFICATION_CLASS | C.evidence_class <- class | once for each class in {EV-CLK,EV-PHY,EV-CAP} | AE:4449-4450
QEQ019 | FINALIZE_QUALIFICATION_CLASS | C.plan <- ref(Q) | once for each of the three classes | AE:4449-4452
QEQ020 | FINALIZE_QUALIFICATION_CLASS | C.campaign_id <- Q.campaign_id | once for each of the three classes | AE:4449-4452
QEQ021 | FINALIZE_QUALIFICATION_CLASS | C.support_profile <- ref(S) | once for each of the three classes | AE:4449-4453
QEQ022 | FINALIZE_QUALIFICATION_CLASS | C.release_digest <- Q.release_digest | once for each of the three classes | AE:4449-4454
QEQ023 | FINALIZE_QUALIFICATION_CLASS | C.cell_results[i].run_results[j] <- ref(protected terminal run result for the jth planned run of Q's ith cell in class C) | complete plan-cell and planned-run order | AE:4376-4382
QEQ024 | FINALIZE_QUALIFICATION_CLASS | C.clock_epochs[k] <- ref(kth duplicate-free first-use qualified clock epoch derived from C's ordered run, record, and acquisition observations) | complete derived sequence | AE:3889-3905; AE:5379-5410
QEQ025 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.clock_result <- ref(C_CLK) | exactly EV-CLK | AE:4456
QEQ026 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.physical_durability_result <- ref(C_PHY) | exactly EV-PHY | AE:4457
QEQ027 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.capability_result <- ref(C_CAP) | exactly EV-CAP | AE:4458
QEQ028 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.plan <- ref(Q) | one typed reference | AE:4459
QEQ029 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.plan_acceptance <- ref(A) | one authenticated typed reference | AE:4460
QEQ030 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.support_profile <- ref(S) | one exact current profile | AE:4461
QEQ031 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.release_digest <- Q.release_digest | one scalar copy | AE:4462
QEQ032 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.closure_policy_limits <- Q.closure_policy_limits | one typed reference | AE:4463
QEQ033 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.result <- PASS | all class/current/prerequisite predicates satisfied | AE:4464; AE:4503-4515
QEQ034 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.issuance_time_observation <- ref(O_Q) | one owner-derived fresh QUALIFICATION_RECEIPT_ISSUE observation in the same transaction | AE:4533-4558
QEQ035 | FINALIZE_QUALIFICATION_RECEIPT | Q_R.tier_results[i] <- ref(T_REQUIRED[i]) | exact concat of complete DESIGN, IMPLEMENTATION, RELEASE partitions | AE:4466-4481
QEQ036 | FINALIZE_QUALIFICATION_RECEIPT | T.tier <- partition tier | every T in the three complete partitions | AE:4483-4489
QEQ037 | FINALIZE_QUALIFICATION_RECEIPT | T.subject_revision <- CURRENT_SUBJECT(T.tier) | every T in the three complete partitions | AE:4488-4491
QEQ038 | FINALIZE_QUALIFICATION_RECEIPT | T.result <- PASS | every T in the three complete partitions | AE:4488-4491
QEQ039 | FINALIZE_QUALIFICATION_RECEIPT | CURRENT_TIER(T.claim_id,T.tier) <- ref(T) | every T; pointer locked through receipt insertion | AE:4492; AE:4509-4512
QEQ040 | FINALIZE_QUALIFICATION_RECEIPT | P <- CURRENT_TIER(body(P).claim_id,body(P).tier) | every P in every T.prerequisite_results, preserving list membership/order | AE:4493-4498
QEQ041 | FINALIZE_QUALIFICATION_RECEIPT | body(P).result <- PASS | every prerequisite P selected by QEQ040 | AE:4493-4498
QEQ042 | ACCEPT_QUALIFICATION_PLAN / FINALIZE_QUALIFICATION_RECEIPT | CURRENT_SUBJECT(RELEASE) <- Q.subject_revision | the same locked value used by QEQ012 | AE:4500
```

Attestation policy, qualification, profile, live, recomputed, acquisition, and
deployment-partition equalities are:

```text
DEQ001 | FINALIZE_DEPLOYMENT_ATTESTATION | D.deployment_admission_policy <- ref(P_D) | one typed reference | AE:4685-4686
DEQ002 | FINALIZE_DEPLOYMENT_ATTESTATION | LOCKED_POLICY(D.target_database_identity,D.target_surface_digest) <- ref(P_D) | exact current policy | AE:4687-4690
DEQ003 | FINALIZE_DEPLOYMENT_ATTESTATION | D.support_profile <- LOCKED_PROFILE(D) | exact current support profile | AE:4648-4653
DEQ004 | FINALIZE_DEPLOYMENT_ATTESTATION | D.clock_envelope <- LOCKED_CLOCK(D) | exact current unexpired profile/host/boot/synchronization envelope | AE:4648-4664
DEQ005 | FINALIZE_DEPLOYMENT_ATTESTATION | D.qualification_receipt <- ref(Q_R) | one current PASS and unexpired receipt | AE:4692
DEQ006 | FINALIZE_DEPLOYMENT_ATTESTATION | D.qualification_plan <- Q_R.plan | one typed reference | AE:4693
DEQ007 | FINALIZE_DEPLOYMENT_ATTESTATION | D.qualification_plan_acceptance <- Q_R.plan_acceptance | one authenticated typed reference | AE:4694
DEQ008 | FINALIZE_DEPLOYMENT_ATTESTATION | D.support_profile <- Q_R.support_profile | one typed reference, also DEQ003 | AE:4695
DEQ009 | FINALIZE_DEPLOYMENT_ATTESTATION | D.closure_policy_limits <- Q_R.closure_policy_limits | one typed reference | AE:4696
DEQ010 | FINALIZE_DEPLOYMENT_ATTESTATION | D.closure_policy_limits <- body(D.support_profile).closure_policy_limits | one typed reference | AE:4697
DEQ011 | FINALIZE_DEPLOYMENT_ATTESTATION | D.controller_host <- body(D.support_profile).controller_host | one typed reference | AE:4698
DEQ012 | FINALIZE_DEPLOYMENT_ATTESTATION | D.postgresql_host <- body(D.support_profile).postgresql_host | one typed reference | AE:4699
DEQ013 | FINALIZE_DEPLOYMENT_ATTESTATION | D.postgresql_endpoint <- body(D.support_profile).postgresql_endpoint | one typed reference | AE:4700
DEQ014 | FINALIZE_DEPLOYMENT_ATTESTATION | D.deployment_topology <- body(D.support_profile).deployment_topology | one typed reference | AE:4701
DEQ015 | FINALIZE_DEPLOYMENT_ATTESTATION | D.controller_host <- L.controller_host | protected live binding at issuance | AE:4666-4669
DEQ016 | FINALIZE_DEPLOYMENT_ATTESTATION | D.postgresql_host <- L.postgresql_host | protected live binding at issuance | AE:4666-4669
DEQ017 | FINALIZE_DEPLOYMENT_ATTESTATION | D.postgresql_endpoint <- L.postgresql_endpoint | protected live binding at issuance | AE:4666-4669
DEQ018 | FINALIZE_DEPLOYMENT_ATTESTATION | body(D.deployment_topology).controller_host <- L.controller_host | protected live binding reached through the exact topology body | AE:4666-4669
DEQ019 | FINALIZE_DEPLOYMENT_ATTESTATION | body(D.deployment_topology).postgresql_host <- L.postgresql_host | protected live binding reached through the exact topology body | AE:4666-4669
DEQ020 | FINALIZE_DEPLOYMENT_ATTESTATION | body(D.deployment_topology).postgresql_endpoint <- L.postgresql_endpoint | protected live binding reached through the exact topology body | AE:4666-4669
DEQ021 | FINALIZE_DEPLOYMENT_ATTESTATION | D.target_database_identity <- L.target_database_identity | locked live target and policy-covered target | AE:4623-4627; AE:4674-4679
DEQ022 | FINALIZE_DEPLOYMENT_ATTESTATION | D.host_identity <- body(D.controller_host).host_identity | one typed identity reference | AE:4702
DEQ023 | FINALIZE_DEPLOYMENT_ATTESTATION | D.host_identity <- body(D.postgresql_host).host_identity | same typed identity reference | AE:4703
DEQ024 | FINALIZE_DEPLOYMENT_ATTESTATION | D.boot_identity <- body(D.clock_envelope).boot_identity | one current per-boot identity | AE:4704
DEQ025 | FINALIZE_DEPLOYMENT_ATTESTATION | D.boot_identity <- L.boot_identity | protected current live projection | AE:4705
DEQ026 | FINALIZE_DEPLOYMENT_ATTESTATION | L.boot_environment_configuration <- body(body(D.support_profile).boot_configuration).configuration | one typed configuration reference | AE:4706-4707
DEQ027 | FINALIZE_DEPLOYMENT_ATTESTATION | D.endpoint_identity <- body(D.postgresql_endpoint).endpoint_identity | one typed identity reference | AE:4708
DEQ028 | FINALIZE_DEPLOYMENT_ATTESTATION | D.storage_identity <- body(body(D.support_profile).storage_profile).identity | one typed identity reference | AE:4709
DEQ029 | FINALIZE_DEPLOYMENT_ATTESTATION | D.role_grant_set <- ref(RG) | one recomputed canonical body | AE:4710
DEQ030 | FINALIZE_DEPLOYMENT_ATTESTATION | D.writer_inventory <- ref(WI) | one recomputed canonical body | AE:4711
DEQ031 | FINALIZE_DEPLOYMENT_ATTESTATION | body(WI).role_grant_set <- ref(RG) | one typed reference | AE:4712
DEQ032 | FINALIZE_DEPLOYMENT_ATTESTATION | body(RG).target_database_identity <- D.target_database_identity | one typed target reference | AE:4713
DEQ033 | FINALIZE_DEPLOYMENT_ATTESTATION | body(WI).target_database_identity <- D.target_database_identity | one typed target reference | AE:4714
DEQ034 | FINALIZE_DEPLOYMENT_ATTESTATION | body(RG).target_surface_digest <- D.target_surface_digest | one scalar copy | AE:4715
DEQ035 | FINALIZE_DEPLOYMENT_ATTESTATION | body(WI).target_surface_digest <- D.target_surface_digest | one scalar copy | AE:4716
DEQ036 | FINALIZE_DEPLOYMENT_ATTESTATION | protected profiler RG carrier <- ref(RG) | complete locked live graph | AE:4717
DEQ037 | FINALIZE_DEPLOYMENT_ATTESTATION | protected profiler WI carrier <- ref(WI) | complete locked live graph | AE:4718
DEQ038 | FINALIZE_DEPLOYMENT_ATTESTATION | admission-finalizer recomputed RG carrier <- ref(RG) | independently canonicalized complete graph | AE:4719
DEQ039 | FINALIZE_DEPLOYMENT_ATTESTATION | admission-finalizer recomputed WI carrier <- ref(WI) | independently canonicalized complete graph | AE:4720
DEQ040 | FINALIZE_DEPLOYMENT_ATTESTATION | D.issuance_time_observation <- ref(O_I) | one owner-derived fresh DEPLOYMENT_ATTESTATION_ISSUE observation in the same transaction | AE:4660-4664; AE:4733-4744
DEQ041 | FINALIZE_DEPLOYMENT_ATTESTATION | body(D.issuance_time_observation).clock_envelope <- D.clock_envelope | exact current envelope | AE:4733-4736
DEQ042 | FINALIZE_DEPLOYMENT_ATTESTATION | D.proposed_publication_epoch <- locked reserved checked-next publication epoch | one server-derived value | AE:4727-4730; AE:4849-4869
DEQ043 | FINALIZE_DEPLOYMENT_ATTESTATION | D.admission_generation <- D.proposed_publication_epoch | one scalar copy | AE:4727-4730
DEQ044 | FINALIZE_DEPLOYMENT_ATTESTATION | D.deployment_campaign <- ref(C_D) | one typed deployment campaign | AE:4781
DEQ045 | FINALIZE_DEPLOYMENT_ATTESTATION | body(C_D.campaign_plan).basis.deployment_matrix <- ref(M_D) | one typed matrix reference | AE:4782-4785
DEQ046 | FINALIZE_DEPLOYMENT_ATTESTATION | body(C_D.campaign_plan).basis.policy <- ref(P_D) | same policy as DEQ001-DEQ002 | AE:4782-4785
DEQ047 | FINALIZE_DEPLOYMENT_ATTESTATION | M_D.deployment_policy <- ref(P_D) | same policy as DEQ001-DEQ002 | AE:4787
DEQ048 | FINALIZE_DEPLOYMENT_ATTESTATION | C_D.planned_runs[i] <- M_D.planned_runs[i] | complete planned-run sequence and order | AE:4788
DEQ049 | FINALIZE_DEPLOYMENT_ATTESTATION | D.deployment_tier_results[i] <- ref(T_DEPLOYMENT[i]) | complete seven-claim ASCII-ordered deployment partition | AE:4789-4793
DEQ050 | FINALIZE_DEPLOYMENT_ATTESTATION | T.campaign <- ref(C_D) | every T in literal T_DEPLOYMENT | AE:4795-4798
DEQ051 | FINALIZE_DEPLOYMENT_ATTESTATION | T.subject_revision <- CURRENT_SUBJECT(DEPLOYMENT) | every T in literal T_DEPLOYMENT | AE:4795-4799
DEQ052 | FINALIZE_DEPLOYMENT_ATTESTATION | CURRENT_TIER(T.claim_id,DEPLOYMENT) <- ref(T) | every T in literal T_DEPLOYMENT; pointer locked through attestation insertion | AE:4795-4800
DEQ053 | FINALIZE_DEPLOYMENT_ATTESTATION | A_D.campaign <- ref(C_D) | every deciding acquisition in planned-run/oracle order | AE:4754-4758; AE:4812-4817
DEQ054 | FINALIZE_DEPLOYMENT_ATTESTATION | A_D.acquired_at <- ref(O_A) | one immutable acquisition observation per deciding acquisition | AE:4754-4761
DEQ055 | FINALIZE_DEPLOYMENT_ATTESTATION | E_D.campaign <- A_D.campaign | the matching record for the same campaign/run/oracle | AE:4754-4757; AE:4812-4817
DEQ056 | FINALIZE_DEPLOYMENT_ATTESTATION | E_D.deployment_evidence_acquisition <- ref(A_D) | every deciding record | AE:4754-4757
DEQ057 | FINALIZE_DEPLOYMENT_ATTESTATION | A_D.observed_projection <- E_D.observed_projection | every deciding record | AE:4754-4757
DEQ058 | FINALIZE_DEPLOYMENT_ATTESTATION | A_D.acquisition_procedure <- E_D.acquisition_procedure | every deciding record | AE:4754-4758
DEQ059 | FINALIZE_DEPLOYMENT_ATTESTATION | O_A.clock_envelope <- D.clock_envelope | every deciding acquisition | AE:4759-4764
DEQ060 | FINALIZE_DEPLOYMENT_ATTESTATION | O_I.clock_envelope <- D.clock_envelope | the one issuance observation | AE:4759-4764
DEQ061 | FINALIZE_DEPLOYMENT_ATTESTATION | body(O_A.clock_envelope).boot_identity <- D.boot_identity | every deciding acquisition, no cross-boot substitution | AE:4763-4765
DEQ062 | FINALIZE_DEPLOYMENT_ATTESTATION | T.result <- PASS | every T in literal T_DEPLOYMENT | AE:4795-4800
DEQ063 | FINALIZE_DEPLOYMENT_ATTESTATION | (D.target_database_identity,D.target_surface_digest) <- locked target-surface selector key | one exact protected slot | AE:4623-4637; AE:4849-4858
```

`DeploymentAttestation.target_generation` remains one required
`TargetGeneration` body member (`AE:2950–2987`). The accepted complete
attestation-equality block and finalization lock/allocation rule do not assign
that member, compare it with the `TARGET_GENERATION` current slot, or lock
that slot (`AE:4681–4801`, `AE:4849–4858`). Its source-native disposition
is therefore `SOURCE_UNASSIGNED`: canonical body validation requires the
typed member, while any database-derived value, currentness rule, or lock
binding is a later issue-106 proposal detail. This does not remove the
independently accepted `TARGET_GENERATION` current-selector class.

#### Protected macOS-local profile and live-binding predicate registry

The profile predicates are independently enumerable from the source rather
than inferred from body presence. Here `TOP=body(S.deployment_topology)`,
`CH=body(S.controller_host)`, `PH=body(S.postgresql_host)`,
`EP=body(S.postgresql_endpoint)`,
`PGC=body(body(S.postgresql_profile).configuration)`, and
`DIR0=PGC.unix_socket_directories[0]`. `COL=body(L.collected_at)` and
`ENV=body(COL.clock_envelope)`. The literal
`LIVE_COHERENCE_CARRIERS` set is
`{L.boot_environment_configuration,L.boot_identity,L.clock_configuration,L.collected_at,L.controller_host,L.filesystem_configuration,L.hardware_configuration,L.operating_system_configuration,L.postgresql_configuration,L.postgresql_endpoint,L.postgresql_host,L.storage_configuration,L.support_profile,L.target_database_identity,L.unix_socket_directories[i],L.virtualization_configuration}`;
the directory path expands once per ordered effective member. The notation `socket_address(DIR0,PGC.postgresql_port)` means exactly
`DIR0.resolved_path || "/.s.PGSQL." || decimal(PGC.postgresql_port)`, without
doubling the separator when the resolved path is `/`. These aliases are
notation only, not new source types or callables.

Each `PLV` row is one positive protected predicate or derivation. The
configuration rows enumerate all eight component members; the coherence rows
are six distinct predicates, not one “same deployment” assertion.

~~~text
PLV001 | PROFILE_SELECT | S.profile_name = "macos-local-postgresql-v1" | one profile name; not a qualification result | AE:7554-7562
PLV002 | PROFILE_RESOLVE | body(S.boot_configuration).component_class = BOOT_ENVIRONMENT, its identity is EvidenceIdentity/v1 with identity_class=BOOT_CONFIGURATION, and its configuration is BootEnvironmentConfiguration/v1 | one; PRD001 | AE:2024-2035; AE:7554-7558
PLV003 | PROFILE_RESOLVE | body(S.clock_profile).component_class = CLOCK, its identity is EvidenceIdentity/v1 with identity_class=CLOCK, and its configuration is ClockConfiguration/v1 | one; PRD002 | AE:2024-2035; AE:7554-7558
PLV004 | PROFILE_RESOLVE | body(S.filesystem_profile).component_class = FILESYSTEM, its identity is EvidenceIdentity/v1 with identity_class=FILESYSTEM, and its configuration is FilesystemConfiguration/v1 | one; PRD003 | AE:2024-2035; AE:7554-7558
PLV005 | PROFILE_RESOLVE | body(S.hardware_profile).component_class = HARDWARE, its identity is EvidenceIdentity/v1 with identity_class=HARDWARE, and its configuration is HardwareConfiguration/v1 | one; PRD004 | AE:2024-2035; AE:7554-7558
PLV006 | PROFILE_RESOLVE | body(S.operating_system_profile).component_class = OPERATING_SYSTEM, its identity is EvidenceIdentity/v1 with identity_class=OPERATING_SYSTEM, and its configuration is OperatingSystemConfiguration/v1 | one; PRD005 | AE:2024-2035; AE:7554-7558
PLV007 | PROFILE_RESOLVE | body(S.postgresql_profile).component_class = POSTGRESQL, its identity is EvidenceIdentity/v1 with identity_class=POSTGRESQL, and its configuration is PostgresqlComponentConfiguration/v1 | one; PRD006 | AE:2024-2035; AE:7554-7559
PLV008 | PROFILE_RESOLVE | body(S.storage_profile).component_class = STORAGE, its identity is EvidenceIdentity/v1 with identity_class=STORAGE, and its configuration is StorageConfiguration/v1 | one; PRD007 | AE:2024-2035; AE:7554-7558
PLV009 | PROFILE_RESOLVE | body(S.virtualization_profile).component_class = VIRTUALIZATION, its identity is EvidenceIdentity/v1 with identity_class=VIRTUALIZATION, and its configuration is VirtualizationConfiguration/v1 | one; PRD008 | AE:2024-2035; AE:7554-7558
PLV010 | PROFILE_RESOLVE | body(body(S.operating_system_profile).configuration).family = MACOS | one literal | AE:7554-7557
PLV011 | PROFILE_RESOLVE | body(body(S.filesystem_profile).configuration).filesystem_type = APFS | one literal | AE:7554-7557
PLV012 | PROFILE_RESOLVE | body(body(S.clock_profile).configuration).clock_source = MACH_CONTINUOUS_TIME | one literal | AE:7554-7557
PLV013 | PROFILE_RESOLVE | PGC.postgresql_settings resolves exact PostgresqlSettings/v1 | one typed reference | AE:7558-7559
PLV014 | PROFILE_RESOLVE | PGC.postgresql_port is positive | one PostgresqlPort | AE:7558-7559
PLV015 | PROFILE_RESOLVE | PGC.unix_socket_directories is the complete ordered effective sequence, not a selected member or setting string | one ordered sequence | AE:1996-1998; AE:7558-7560
PLV016 | PROFILE_BIND | TOP.locality = SAME_HOST_LOCAL | one literal | AE:7564-7567
PLV017 | PROFILE_BIND | TOP.network_path_identity = NONE | one literal | AE:7583
PLV018 | PROFILE_BIND | TOP.controller_host = S.controller_host | one reference equality | AE:7564-7567
PLV019 | PROFILE_BIND | TOP.postgresql_host = S.postgresql_host | one reference equality | AE:7564-7567
PLV020 | PROFILE_BIND | TOP.postgresql_endpoint = S.postgresql_endpoint | one reference equality | AE:7564-7567
PLV021 | PROFILE_BIND | CH.host_identity = PH.host_identity and both resolve as EvidenceIdentity/v1 with identity_class=HOST | one exact host identity; PRD010 and PRD014 | AE:2039-2042; AE:7567-7568
PLV022 | PROFILE_BIND | CH.operating_system_profile = S.operating_system_profile | one reference equality | AE:7568-7569
PLV023 | PROFILE_BIND | PH.operating_system_profile = S.operating_system_profile | one reference equality | AE:7568-7569
PLV024 | PROFILE_BIND | PH.postgresql_profile = S.postgresql_profile | one reference equality | AE:7569-7570
PLV025 | PROFILE_BIND | PH.storage_profile = S.storage_profile | one reference equality | AE:7569-7571
PLV026 | PROFILE_BIND | CH.boot_configuration = body(S.boot_configuration).configuration | one stable configuration reference | AE:7571-7572
PLV027 | PROFILE_BIND | PH.boot_configuration = body(S.boot_configuration).configuration | one stable configuration reference | AE:7571-7572
PLV028 | ADMISSION_BIND | EP.target_database_identity = L.target_database_identity = D.target_database_identity and all three resolve as EvidenceIdentity/v1 with identity_class=TARGET_DATABASE | one target-specific admission equality; qualification remains target-free; PRD021, PRD036, and PRD053 | AE:2039-2043; AE:2278-2283; AE:4674-4679; AE:7572-7574; AE:7590-7594
PLV029 | PROFILE_BIND | EP.transport = UNIX_DOMAIN_SOCKET | one literal | AE:2011-2013; AE:7572-7574
PLV030 | PROFILE_BIND | EP.port = NONE | one literal | AE:2011-2013; AE:7572-7574
PLV031 | PROFILE_BIND | length(PGC.unix_socket_directories) = 1 | exactly one member | AE:2011-2013; AE:7575-7576
PLV032 | PROFILE_BIND | EP.unix_socket_directory = DIR0 | exact embedded member equality | AE:2011-2013; AE:7575-7577
PLV033 | DIRECTORY_VALIDATE | DIR0.configured_path is absolute | one member | AE:1996-2002; AE:7577-7582
PLV034 | DIRECTORY_VALIDATE | DIR0.configured_path has exactly one leading separator | one member | AE:1998-2001
PLV035 | DIRECTORY_VALIDATE | DIR0.configured_path has no repeated separator | one member | AE:1998-2001
PLV036 | DIRECTORY_VALIDATE | DIR0.configured_path has no "." component | one member | AE:1998-2001
PLV037 | DIRECTORY_VALIDATE | DIR0.configured_path has no ".." component | one member | AE:1998-2001
PLV038 | DIRECTORY_VALIDATE | DIR0.configured_path has no trailing separator unless it is "/" | one member | AE:2000-2002
PLV039 | DIRECTORY_VALIDATE | DIR0.resolved_path is the absolute fully symlink-resolved path | one member | AE:2002-2005
PLV040 | DIRECTORY_VALIDATE | the opened final DIR0.resolved_path exists | one held directory | AE:2002-2005
PLV041 | DIRECTORY_VALIDATE | the opened final DIR0.resolved_path is a directory | one held directory | AE:2002-2005
PLV042 | DIRECTORY_VALIDATE | symlink resolution and endpoint derivation retain the opened final-directory handle | one held handle | AE:2002-2006
PLV043 | DIRECTORY_VALIDATE | DIR0.directory_device_id is the unsigned identity of that held directory | one UInt128String | AE:2003-2006
PLV044 | DIRECTORY_VALIDATE | DIR0.directory_file_id is the unsigned identity of that held directory | one UInt128String | AE:2003-2006
PLV045 | DIRECTORY_REVALIDATE | configured path, resolved path, handle, device ID, and file ID still identify the same directory when L and EP are derived | one recheck | AE:2003-2009
PLV046 | PROFILE_BIND | EP.address = socket_address(DIR0,PGC.postgresql_port) | one absolute normalized complete pathname | AE:2013-2020; AE:7573-7580
PLV047 | ENDPOINT_VALIDATE | if any endpoint transport is not UNIX_DOMAIN_SOCKET, its unix_socket_directory = NONE | every non-Unix endpoint | AE:2021-2022
PLV048 | LIVE_BIND | L.support_profile = ref(S) | one exact profile | AE:2048-2050
PLV049 | LIVE_BIND | L.boot_environment_configuration = body(S.boot_configuration).configuration | one reference equality | AE:2048-2052; AE:7600-7601
PLV050 | LIVE_BIND | L.clock_configuration = body(S.clock_profile).configuration | one reference equality | AE:2048-2052; AE:7600-7601
PLV051 | LIVE_BIND | L.filesystem_configuration = body(S.filesystem_profile).configuration | one reference equality | AE:2048-2052; AE:7600-7601
PLV052 | LIVE_BIND | L.hardware_configuration = body(S.hardware_profile).configuration | one reference equality | AE:2048-2052; AE:7600-7601
PLV053 | LIVE_BIND | L.operating_system_configuration = body(S.operating_system_profile).configuration | one reference equality | AE:2048-2052; AE:7600-7601
PLV054 | LIVE_BIND | L.postgresql_configuration = body(S.postgresql_profile).configuration | one reference equality | AE:2048-2052; AE:7600-7601
PLV055 | LIVE_BIND | L.storage_configuration = body(S.storage_profile).configuration | one reference equality | AE:2048-2052; AE:7600-7601
PLV056 | LIVE_BIND | L.virtualization_configuration = body(S.virtualization_profile).configuration | one reference equality | AE:2048-2052; AE:7600-7601
PLV057 | LIVE_BIND | L.controller_host = S.controller_host | one reference equality | AE:2048-2050; AE:4666-4669
PLV058 | LIVE_BIND | L.postgresql_host = S.postgresql_host | one reference equality | AE:2048-2050; AE:4666-4669
PLV059 | LIVE_BIND | L.postgresql_endpoint = S.postgresql_endpoint | one reference equality | AE:2048-2050; AE:4666-4669
PLV060 | LIVE_BIND | L.target_database_identity = EP.target_database_identity | one target equality | AE:2048-2050; AE:7590-7594
PLV061 | LIVE_BIND | L.unix_socket_directories = PGC.unix_socket_directories byte for byte, preserving complete order | one sequence equality | AE:2048-2053; AE:2015-2020; AE:7603-7604
PLV062 | LIVE_BIND | body(L.postgresql_endpoint).unix_socket_directory = L.unix_socket_directories[0] = DIR0 | exactly one member | AE:2048-2053; AE:7603-7604
PLV063 | LIVE_TIME | COL.mode = QUALIFIED_CLOCK | one protected observation | AE:2054-2058
PLV064 | LIVE_TIME | COL.phase = SUPPORT_PROFILE_PROJECTION | one protected observation | AE:2054-2058
PLV065 | LIVE_TIME | COL.clock_envelope is the exact qualified ClockEnvelope/v1 for S, including PRD030-PRD033 | one protected current envelope | AE:2034-2035; AE:2054-2058; AE:2278-2297
PLV066 | LIVE_TIME | L.boot_identity = ENV.boot_identity and both resolve as EvidenceIdentity/v1 with identity_class=BOOT_ENVIRONMENT | one per-boot reference equality; PRD031 and PRD035 | AE:2053-2058; AE:2278-2281; AE:7600-7603
PLV067 | LIVE_DERIVE | body(L.operating_system_configuration).{product_build_version,product_version,kernel_release} come from the selected host OS interfaces | three scalar paths | AE:7589-7592
PLV068 | LIVE_DERIVE | {body(L.postgresql_configuration).postgresql_version,body(L.postgresql_configuration).postgresql_settings,body(L.postgresql_configuration).data_directory,body(L.postgresql_configuration).postgresql_port,L.target_database_identity,L.unix_socket_directories[i].configured_path,L.unix_socket_directories[i].resolved_path,L.unix_socket_directories[i].directory_device_id,L.unix_socket_directories[i].directory_file_id,body(L.postgresql_endpoint).address} come from the selected live server and opened directory members | ten path families; [i] expands once per effective directory in source order | AE:7591-7595
PLV069 | LIVE_DERIVE | body(L.filesystem_configuration).{filesystem_type,filesystem_version,mount_options,mount_path,volume_uuid,case_sensitivity} come from the filesystem hosting body(L.postgresql_configuration).data_directory | six finite paths | AE:7595-7597
PLV070 | LIVE_DERIVE | body(S.hardware_profile).identity is EvidenceIdentity/v1 with identity_class=HARDWARE and body(S.storage_profile).identity is EvidenceIdentity/v1 with identity_class=STORAGE; those identities and their resolved configuration bodies come from the local host and backing device | two typed identity paths and two configuration paths; PRD004 and PRD007 | AE:2024-2033; AE:7596-7598
PLV071 | LIVE_DERIVE | L.boot_identity is EvidenceIdentity/v1 with identity_class=BOOT_ENVIRONMENT and body(S.virtualization_profile).identity is EvidenceIdentity/v1 with identity_class=VIRTUALIZATION; both come from the current host session | two typed identity paths; PRD008 and PRD035 | AE:2024-2033; AE:2053-2054; AE:7597-7599
PLV072 | LIVE_DERIVE | body(L.clock_configuration).{clock_source,continuous_across_sleep,forward_rate_error_denominator,forward_rate_error_numerator,nanoseconds_per_tick_denominator,nanoseconds_per_tick_numerator,regression_action} and COL come from the qualified clock implementation | seven scalar paths plus one protected observation | AE:7598-7600
PLV073 | LIVE_COHERENCE | every member of LIVE_COHERENCE_CARRIERS describes one host | the literal carrier set above, with ordered directory expansion | AE:7603-7606
PLV074 | LIVE_COHERENCE | every member of LIVE_COHERENCE_CARRIERS describes one boot | the literal carrier set above, with ordered directory expansion | AE:7603-7606
PLV075 | LIVE_COHERENCE | every member of LIVE_COHERENCE_CARRIERS describes one PostgreSQL server | the literal carrier set above, with ordered directory expansion | AE:7603-7606
PLV076 | LIVE_COHERENCE | every member of LIVE_COHERENCE_CARRIERS describes one target database | the literal carrier set above, with ordered directory expansion | AE:7603-7606
PLV077 | LIVE_COHERENCE | every member of LIVE_COHERENCE_CARRIERS describes one data volume | the literal carrier set above, with ordered directory expansion | AE:7603-7606
PLV078 | LIVE_COHERENCE | every member of LIVE_COHERENCE_CARRIERS is collected in one protected transaction | the literal carrier set above, with ordered directory expansion | AE:7603-7606
~~~

Every negative row below states only the behavior fixed by its cited source. A
profiler or qualification refusal yields no qualifying live projection or
receipt and leaves mutation fenced. An admission refusal emits one
nonauthorizing `FailedDeploymentResult/v1` and creates no attestation,
reservation, current pointer, or epoch transition. The expected complete
failure sequence for a planned admission case is fixed by its concrete
deployment-matrix row. It is not derived from all predicates that happen to be
true (`AE:5610–5627`, `AE:7781–7803`).

Matrix family requirements and concrete matrix members are distinct. Within
this subsection, `M` denotes one concrete `CanonicalDeploymentMatrix/v1`
body. A concrete row is enumerated only by the exact matrix identity `ref(M)`
and exact member path
`body(M).planned_runs[row_position]` in that body's source-fixed sequence.
`row_position` is the member's JSON-array position within that exact `M`;
`cell_id`, `run_id`, or an inferred parameter tuple cannot replace the
complete member.

The source separately requires each deployment stimulus to prebind exactly one
support-profile reference, target-database reference, and target-surface
digest, and requires matrix runs to sort by those values, then ASCII
`cell_id` and ASCII `run_id`. `CampaignRunRequirement.stimulus` resolves one
`EvidenceStimulus/v1`, but its `parameter_bytes` is a generic `EvidenceRef`.
The accepted grammar fixes neither a deployment-case parameter contract
kind/version nor exact member paths for extracting the three tuple values.
The semantic prebinding and sort order remain requirements, but no current
tuple expression or five-field row selector is computable
(`AE:739–752`, `AE:2139–2149`, `AE:2997–3014`,
`AE:3075–3085`, `AE:4314–4330`).

For each concrete negative row, its `OR-ID` oracle requirement names an
expected `OracleProjection/v1` before execution. The source requires the
actual `FailedDeploymentResult/v1` to satisfy that row's prebound refusal
oracle exactly. The closed `OR-ID` field registry has no dedicated
expected-refusal field. Using one of its sequence-valued reference fields
would require an exact field name, value kind, field position, sequence-member
position, and acyclic carrier; the accepted source specifies none of them
(`AE:3740–3759`, `AE:5610–5627`, `AE:6760–6774`,
`AE:7781–7803`).

The row's complete `FailedDeploymentResult/v1` cannot itself be referenced
from the matrix's expected-projection closure. That result references its
campaign, the campaign references its campaign plan, and a deployment campaign
plan's basis references the same matrix. Referencing that complete result from
the matrix would therefore return to `M`, and recursive reference resolution
rejects the cycle (`AE:310–326`, `AE:3666–3738`,
`AE:4207–4261`).

No concrete accepted `CanonicalDeploymentMatrix/v1` body, resolved
deployment-stimulus parameter body, OR-ID expected projection, or
failure-evidence body is present in the accepted source packet. The concrete
matrix reference; row positions, members, cell IDs, and run IDs;
deployment-stimulus parameter contract kind/version; tuple extraction paths
and values; OR-ID oracle position and expected projection; acyclic
expected-refusal carrier kind and version; exact
projection field and nested member path, including any sequence-member
position; runtime/template treatment of result fields; failure-evidence
references; oracle IDs; complete future failure sequence; and family
multiplicity are therefore `MATRIX_BODY_GAP`. This inventory instantiates no
concrete matrix row and no current expected-refusal extraction. It preserves
the concrete `ref(M)` plus `planned_runs[row_position]` enumeration rule,
the source-fixed semantic prebinding, ordering, and exact-comparison
obligations, and the following negative-family constraints. Deleting a family
from a later matrix and its check cannot delete the obligation.

`REQUIRED_MEMBER(C)` means the concrete row's exact failure sequence must
contain the source-named code `C`. `ONE_OF(A,B)` means the row's distinguished
endpoint classification is exactly one of those two codes as its matrix schema
dictates. `MATRIX_CODE_VALUE` means the source requires the negative family but
does not select a code without the concrete matrix body. None of these
constraints supplies the rest of a failure sequence, precedence among
simultaneous defects, or an all-true-predicates reporting rule.

~~~text
NMR001 | topology locality REMOTE | REQUIRED_MEMBER(REMOTE_POSTGRESQL_UNSUPPORTED) | AE:7792-7803
NMR002 | endpoint transport TCP_REMOTE | REQUIRED_MEMBER(REMOTE_POSTGRESQL_UNSUPPORTED) | AE:7792-7803
NMR003 | endpoint transport TCP_LITERAL_LOOPBACK | REQUIRED_MEMBER(REMOTE_POSTGRESQL_UNSUPPORTED) | AE:7792-7803
NMR004 | topology locality MANAGED | REQUIRED_MEMBER(MANAGED_POSTGRESQL_UNSUPPORTED) | AE:7792-7803
NMR005 | endpoint transport MANAGED_SERVICE | REQUIRED_MEMBER(MANAGED_POSTGRESQL_UNSUPPORTED) | AE:7792-7803
NMR006 | changed endpoint identity under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR007 | changed endpoint target under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR008 | changed endpoint socket path under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR009 | changed socket-directory configured path under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR010 | changed socket-directory resolved path under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR011 | changed socket-directory device identity under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR012 | changed socket-directory file identity under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR013 | changed socket-directory sequence count under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR014 | changed socket-directory sequence order under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR015 | changed endpoint port under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR016 | changed endpoint transport under an otherwise unchanged profile | ONE_OF(ENDPOINT_DRIFT,ENDPOINT_TOPOLOGY_MISMATCH) | AE:7795-7803
NMR017 | controller-host change | REQUIRED_MEMBER(CONTROLLER_HOST_MISMATCH) | AE:7799-7803
NMR018 | PostgreSQL-host change | REQUIRED_MEMBER(POSTGRESQL_HOST_MISMATCH) | AE:7799-7803
NMR019 | relative configured socket-directory path | MATRIX_CODE_VALUE | AE:7805-7811
NMR020 | repeated configured-path separator | MATRIX_CODE_VALUE | AE:7805-7811
NMR021 | embedded "." configured-path component | MATRIX_CODE_VALUE | AE:7805-7811
NMR022 | embedded ".." configured-path component | MATRIX_CODE_VALUE | AE:7805-7811
NMR023 | noncanonical trailing configured-path separator | MATRIX_CODE_VALUE | AE:7805-7811
NMR024 | missing resolved directory target | MATRIX_CODE_VALUE | AE:7805-7811
NMR025 | nondirectory resolved target | MATRIX_CODE_VALUE | AE:7805-7811
NMR026 | socket-directory symlink retarget | MATRIX_CODE_VALUE | AE:7805-7811
NMR027 | socket-directory resolution race | MATRIX_CODE_VALUE | AE:7805-7811
NMR028 | directory-only endpoint address | MATRIX_CODE_VALUE | AE:7805-7811
NMR029 | endpoint address derived from configured rather than resolved path | MATRIX_CODE_VALUE | AE:7805-7811
NMR030 | zero initial-profile socket-directory members | MATRIX_CODE_VALUE | AE:7805-7811
NMR031 | multiple initial-profile socket-directory members | MATRIX_CODE_VALUE | AE:7805-7811
NMR032 | configured member omitted from the live sequence | MATRIX_CODE_VALUE | AE:7805-7811
NMR033 | unconfigured member added to the live sequence | MATRIX_CODE_VALUE | AE:7805-7811
NMR034 | live sequence duplicates a member | MATRIX_CODE_VALUE | AE:7805-7811
NMR035 | live sequence reorders members | MATRIX_CODE_VALUE | AE:7805-7811
~~~

Rows such as a generic endpoint alias, proxy, tunnel, changed directory member,
or combined host inequality are coverage assertions over the applicable
literal `NMR` families, not additional concrete row identities. Each `PLR` row
below is a separate source branch unless its outcome expressly identifies it
as such a coverage assertion.

~~~text
PLR001 | PROFILE_RESOLVE | an unknown component or configuration field | different, ineligible profile; mutation remains fenced | AE:7554-7562
PLR002 | PROFILE_RESOLVE | any accepted component or configuration field changes | different, ineligible profile; mutation remains fenced | AE:7554-7562
PLR003 | DIRECTORY_VALIDATE | configured_path is relative | profile unqualified; NMR019 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:1998-2002; AE:7581-7582; AE:7805-7811
PLR004 | DIRECTORY_VALIDATE | configured_path has a repeated separator | profile unqualified; NMR020 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:1998-2002; AE:7805-7811
PLR005 | DIRECTORY_VALIDATE | configured_path has a "." component | profile unqualified; NMR021 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:1998-2002; AE:7805-7811
PLR006 | DIRECTORY_VALIDATE | configured_path has a ".." component | profile unqualified; NMR022 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:1998-2002; AE:7805-7811
PLR007 | DIRECTORY_VALIDATE | configured_path has a non-root trailing separator | profile unqualified; NMR023 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2000-2002; AE:7805-7811
PLR008 | DIRECTORY_REVALIDATE | directory resolution races | profile and endpoint drift; NMR027 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2003-2009; AE:7805-7811
PLR009 | DIRECTORY_REVALIDATE | the directory is missing | profile and endpoint drift; NMR024 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2003-2009; AE:7805-7811
PLR010 | DIRECTORY_REVALIDATE | the path is not a directory | profile and endpoint drift; NMR025 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2003-2009; AE:7805-7811
PLR011 | DIRECTORY_REVALIDATE | a symlink retargets | profile and endpoint drift; NMR026 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2003-2009; AE:7805-7811
PLR012 | DIRECTORY_REVALIDATE | configured_path changes | profile and endpoint drift; NMR009 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2003-2009; AE:7795-7803
PLR013 | DIRECTORY_REVALIDATE | resolved_path changes | profile and endpoint drift; NMR010 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2003-2009; AE:7795-7803
PLR014 | DIRECTORY_REVALIDATE | directory_device_id changes | profile and endpoint drift; NMR011 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2003-2009; AE:7795-7803
PLR015 | DIRECTORY_REVALIDATE | directory_file_id changes | profile and endpoint drift; NMR012 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2003-2009; AE:7795-7803
PLR016 | PROFILE_BIND | unix_socket_directories has zero members | unsupported profile; NMR030 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2011-2020; AE:7805-7811
PLR017 | PROFILE_BIND | unix_socket_directories has more than one member | unsupported profile; NMR031 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2011-2020; AE:7805-7811
PLR018 | LIVE_BIND | a configured directory member is omitted from L | unsupported profile; NMR032 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2015-2020; AE:7805-7811
PLR019 | LIVE_BIND | L adds an unconfigured directory member | unsupported profile; NMR033 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2015-2020; AE:7805-7811
PLR020 | LIVE_BIND | L duplicates a directory member | unsupported profile; NMR034 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2015-2020; AE:7805-7811
PLR021 | LIVE_BIND | L reorders directory members | unsupported profile; NMR035 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2015-2020; AE:7805-7811
PLR022 | LIVE_BIND | L changes a directory member | unsupported profile; coverage assertion over the applicable changed-field family NMR009-NMR012, with no additional row identity | AE:2015-2020; AE:7795-7803
PLR023 | PROFILE_BIND | EP.address is only the directory, without the socket filename and port | unsupported endpoint; NMR028 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2013-2020; AE:7805-7811
PLR024 | PROFILE_BIND | EP.address is derived from configured_path rather than resolved_path | unsupported endpoint; NMR029 is required and its expected-refusal carrier and complete failure sequence are MATRIX_BODY_GAP | AE:2017-2020; AE:7805-7811
PLR025 | PROFILE_BIND | EP.address uses an alternate path spelling | unsupported endpoint; the source fixes invalidity but no additional negative-matrix family or code beyond the applicable literal NMR field case | AE:2017-2020; AE:7581-7582
PLR026 | ENDPOINT_VALIDATE | a non-Unix EP retains a unix_socket_directory | invalid endpoint binding; no qualifying projection or attestation | AE:2021-2022
PLR027 | LIVE_DERIVE | a required live field is unavailable | L is unqualified; no receipt or attestation | AE:7589-7607
PLR028 | LIVE_COHERENCE | fields come from a mixed collection | L is unqualified; no receipt or attestation | AE:7603-7607
PLR029 | LIVE_DERIVE | a required field is a generic placeholder | L is unqualified; no receipt or attestation | AE:7605-7607
PLR030 | LIVE_DERIVE | the projection is nonexact | L is unqualified; no receipt or attestation | AE:7605-7607
PLR031 | LIVE_BIND | L.boot_environment_configuration differs from body(S.boot_configuration).configuration | L is unqualified; no receipt or attestation | AE:2048-2058; AE:7600-7607
PLR032 | LIVE_BIND | L.clock_configuration differs from body(S.clock_profile).configuration | L is unqualified; no receipt or attestation | AE:2048-2058; AE:7600-7607
PLR033 | LIVE_BIND | L.filesystem_configuration differs from body(S.filesystem_profile).configuration | L is unqualified; no receipt or attestation | AE:2048-2058; AE:7600-7607
PLR034 | LIVE_BIND | L.hardware_configuration differs from body(S.hardware_profile).configuration | L is unqualified; no receipt or attestation | AE:2048-2058; AE:7600-7607
PLR035 | LIVE_BIND | L.operating_system_configuration differs from body(S.operating_system_profile).configuration | L is unqualified; no receipt or attestation | AE:2048-2058; AE:7600-7607
PLR036 | LIVE_BIND | L.postgresql_configuration differs from body(S.postgresql_profile).configuration | L is unqualified; no receipt or attestation | AE:2048-2058; AE:7600-7607
PLR037 | LIVE_BIND | L.storage_configuration differs from body(S.storage_profile).configuration | L is unqualified; no receipt or attestation | AE:2048-2058; AE:7600-7607
PLR038 | LIVE_BIND | L.virtualization_configuration differs from body(S.virtualization_profile).configuration | L is unqualified; no receipt or attestation | AE:2048-2058; AE:7600-7607
PLR039 | LIVE_TIME | L.boot_identity differs from ENV.boot_identity | L is unqualified; the exact code and failure sequence for any planned admission case are MATRIX_BODY_GAP; no receipt or attestation | AE:2053-2058; AE:7600-7607
PLR040 | LIVE_COHERENCE | collected members do not describe one host | L is unqualified; no receipt or attestation | AE:7603-7607
PLR041 | LIVE_COHERENCE | collected members do not describe one boot | L is unqualified; no receipt or attestation | AE:7603-7607
PLR042 | LIVE_COHERENCE | collected members do not describe one PostgreSQL server | L is unqualified; no receipt or attestation | AE:7603-7607
PLR043 | LIVE_COHERENCE | collected members do not describe one target database | L is unqualified; no receipt or attestation | AE:7603-7607
PLR044 | LIVE_COHERENCE | collected members do not describe one data volume | L is unqualified; no receipt or attestation | AE:7603-7607
PLR045 | LIVE_COHERENCE | collected members do not come from one protected collection transaction | L is unqualified; no receipt or attestation | AE:7603-7607
PLR046 | ADMISSION_BIND | TOP.locality = REMOTE | NMR001 requires REMOTE_POSTGRESQL_UNSUPPORTED; concrete row identity (`ref(M)` plus exact `planned_runs[row_position]` member), expected-refusal carrier, and complete failure sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:4674-4679; AE:7583-7587; AE:7792-7803
PLR047 | ADMISSION_BIND | TOP.locality = MANAGED | NMR004 requires MANAGED_POSTGRESQL_UNSUPPORTED; concrete row identity (`ref(M)` plus exact `planned_runs[row_position]` member), expected-refusal carrier, and complete failure sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:4674-4679; AE:7583-7587; AE:7792-7803
PLR048 | ADMISSION_BIND | TOP.network_path_identity is not NONE | ineligible profile; no attestation; the source fixes no separate negative-matrix row or code for this predicate | AE:7583-7587; AE:7609-7617
PLR049 | ADMISSION_BIND | CH.host_identity differs from PH.host_identity | ineligible binding; coverage assertion over NMR017 or NMR018 according to which baseline host changed; no third row or arbitrary code choice | AE:4666-4679; AE:7567-7568; AE:7799-7803
PLR050 | ADMISSION_BIND | the protected live controller host differs from S.controller_host | NMR017 requires CONTROLLER_HOST_MISMATCH; concrete row identity (`ref(M)` plus exact `planned_runs[row_position]` member), expected-refusal carrier, and complete failure sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:4666-4679; AE:7799-7803
PLR051 | ADMISSION_BIND | the protected live PostgreSQL host differs from S.postgresql_host | NMR018 requires POSTGRESQL_HOST_MISMATCH; concrete row identity (`ref(M)` plus exact `planned_runs[row_position]` member), expected-refusal carrier, and complete failure sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:4666-4679; AE:7799-7803
PLR052 | ADMISSION_BIND | the live endpoint is an alias rather than S.postgresql_endpoint | coverage assertion over the applicable changed endpoint family NMR006-NMR016; no additional row identity or code-selection rule | AE:4666-4679; AE:7765-7771; AE:7795-7803
PLR053 | ADMISSION_BIND | a proxy changes endpoint or locality | refused; the concrete stimulus selects the applicable literal NMR001-NMR016 family and owns its exact code sequence; no generic proxy code is fixed | AE:4666-4679; AE:7609-7617; AE:7765-7771
PLR054 | ADMISSION_BIND | a tunnel changes endpoint or locality | refused; the concrete stimulus selects the applicable literal NMR001-NMR016 family and owns its exact code sequence; no generic tunnel code is fixed | AE:7609-7617; AE:7765-7771
PLR055 | ADMISSION_BIND | a hostname or merely reachable address substitutes for the exact local socket endpoint | refused; NMR008 applies when the socket path changes, otherwise the source fixes no additional row or generic hostname code | AE:7583-7587; AE:7765-7771
PLR056 | ADMISSION_BIND | EP.transport = TCP_LITERAL_LOOPBACK | NMR003 requires REMOTE_POSTGRESQL_UNSUPPORTED; concrete row identity (`ref(M)` plus exact `planned_runs[row_position]` member), expected-refusal carrier, and complete failure sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:7572-7587; AE:7792-7803
PLR057 | ADMISSION_BIND | EP.transport = TCP_REMOTE | NMR002 requires REMOTE_POSTGRESQL_UNSUPPORTED; concrete row identity (`ref(M)` plus exact `planned_runs[row_position]` member), expected-refusal carrier, and complete failure sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:7572-7587; AE:7792-7803
PLR058 | ADMISSION_BIND | EP.transport = MANAGED_SERVICE | NMR005 requires MANAGED_POSTGRESQL_UNSUPPORTED; concrete row identity (`ref(M)` plus exact `planned_runs[row_position]` member), expected-refusal carrier, and complete failure sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:7572-7587; AE:7792-7803
PLR059 | COVERAGE_PARTITION | EP.transport changes from the qualified UNIX_DOMAIN_SOCKET value | the closed alternate enum values are exactly PLR056-PLR058; NMR016 records the separately stated otherwise-unchanged endpoint-drift family; this row creates no extra failure demand | AE:2803-2804; AE:7792-7803
PLR060 | ADMISSION_BIND | EP.unix_socket_directory or its socket path changes | NMR008 is required; its endpoint classification and full sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:4666-4679; AE:7795-7803
PLR061 | ADMISSION_BIND | EP.port differs from NONE | NMR015 is required; its endpoint classification and full sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:4666-4679; AE:7795-7803
PLR062 | ADMISSION_BIND | EP.target_database_identity differs from L.target_database_identity or D.target_database_identity | NMR007 is required; its endpoint classification and full sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:4666-4679; AE:7795-7803
PLR063 | ADMISSION_BIND | EP.endpoint_identity changes | NMR006 requires exactly ENDPOINT_DRIFT or ENDPOINT_TOPOLOGY_MISMATCH as the matrix schema dictates; ENDPOINT_IDENTITY_CHANGED is not assigned by this clause; full sequence is MATRIX_BODY_GAP | AE:7583-7587; AE:7795-7803
PLR064 | ADMISSION_BIND | EP.address changes from socket_address(DIR0,PGC.postgresql_port) | refused; it expands to NMR008, NMR028, or NMR029 when one of those exact cases applies; no additional generic address row is fixed | AE:7583-7587; AE:7795-7811
PLR065 | ADMISSION_BIND | ref(S) is not the exact current support-profile reference bound by Q_R and D, or a known typed S reference is old, aliased, or merely schema-compatible rather than byte-identical | PROFILE_MISMATCH; no attestation, reservation, current pointer, or epoch transition | AE:4648-4659; AE:7609-7617
PLR066 | ADMISSION_BIND | a post-qualification endpoint member or binding drifts | coverage assertion over the applicable literal NMR006-NMR016 family; no additional row identity or failure sequence | AE:7609-7617; AE:7795-7803
PLR067 | ADMISSION_BIND | a live guard, plan, receipt, attestation, or L does not bind S.closure_policy_limits and its exact values | CLOSURE_POLICY_MISMATCH; no attestation | AE:7609-7617; AE:7765-7769
PLR068 | PROFILE_SELECT | S.profile_name differs from "macos-local-postgresql-v1" | not the admitted initial profile; no qualifying receipt or attestation | AE:7554-7562
PLR069 | PROFILE_RESOLVE | S.boot_configuration has the wrong component class, identity class, configuration kind, or unresolved member | different, ineligible profile; mutation remains fenced | AE:2024-2035; AE:7554-7562
PLR070 | PROFILE_RESOLVE | S.clock_profile has the wrong component class, identity class, configuration kind, or unresolved member | different, ineligible profile; mutation remains fenced | AE:2024-2035; AE:7554-7562
PLR071 | PROFILE_RESOLVE | S.filesystem_profile has the wrong component class, identity class, configuration kind, or unresolved member | different, ineligible profile; mutation remains fenced | AE:2024-2035; AE:7554-7562
PLR072 | PROFILE_RESOLVE | S.hardware_profile has the wrong component class, identity class, configuration kind, or unresolved member | different, ineligible profile; mutation remains fenced | AE:2024-2035; AE:7554-7562
PLR073 | PROFILE_RESOLVE | S.operating_system_profile has the wrong component class, identity class, configuration kind, or unresolved member | different, ineligible profile; mutation remains fenced | AE:2024-2035; AE:7554-7562
PLR074 | PROFILE_RESOLVE | S.postgresql_profile has the wrong component class, identity class, configuration kind, or unresolved member | different, ineligible profile; mutation remains fenced | AE:2024-2035; AE:7554-7562
PLR075 | PROFILE_RESOLVE | S.storage_profile has the wrong component class, identity class, configuration kind, or unresolved member | different, ineligible profile; mutation remains fenced | AE:2024-2035; AE:7554-7562
PLR076 | PROFILE_RESOLVE | S.virtualization_profile has the wrong component class, identity class, configuration kind, or unresolved member | different, ineligible profile; mutation remains fenced | AE:2024-2035; AE:7554-7562
PLR077 | PROFILE_RESOLVE | body(body(S.operating_system_profile).configuration).family differs from MACOS | different, ineligible profile; mutation remains fenced | AE:7554-7562
PLR078 | PROFILE_RESOLVE | body(body(S.filesystem_profile).configuration).filesystem_type differs from APFS | different, ineligible profile; mutation remains fenced | AE:7554-7562
PLR079 | PROFILE_RESOLVE | body(body(S.clock_profile).configuration).clock_source differs from MACH_CONTINUOUS_TIME | different, ineligible profile; mutation remains fenced | AE:7554-7562
PLR080 | PROFILE_RESOLVE | PGC.postgresql_settings is missing, unresolved, or not the exact PostgresqlSettings/v1 | different, ineligible profile; mutation remains fenced | AE:7554-7562
PLR081 | PROFILE_RESOLVE | PGC.postgresql_port is not positive | different, ineligible profile; mutation remains fenced | AE:7554-7562
PLR082 | PROFILE_BIND | TOP.controller_host differs from S.controller_host | PROFILE_MISMATCH; no qualifying receipt or attestation | AE:7564-7569; AE:7609-7617
PLR083 | PROFILE_BIND | TOP.postgresql_host differs from S.postgresql_host | PROFILE_MISMATCH; no qualifying receipt or attestation | AE:7564-7569; AE:7609-7617
PLR084 | PROFILE_BIND | TOP.postgresql_endpoint differs from S.postgresql_endpoint | PROFILE_MISMATCH; no qualifying receipt or attestation | AE:7564-7569; AE:7609-7617
PLR085 | PROFILE_BIND | CH.operating_system_profile differs from S.operating_system_profile | PROFILE_MISMATCH; no qualifying receipt or attestation | AE:7567-7572; AE:7609-7617
PLR086 | PROFILE_BIND | PH.operating_system_profile differs from S.operating_system_profile | PROFILE_MISMATCH; no qualifying receipt or attestation | AE:7567-7572; AE:7609-7617
PLR087 | PROFILE_BIND | PH.postgresql_profile differs from S.postgresql_profile | PROFILE_MISMATCH; no qualifying receipt or attestation | AE:7567-7572; AE:7609-7617
PLR088 | PROFILE_BIND | PH.storage_profile differs from S.storage_profile | PROFILE_MISMATCH; no qualifying receipt or attestation | AE:7567-7572; AE:7609-7617
PLR089 | PROFILE_BIND | CH.boot_configuration differs from body(S.boot_configuration).configuration | BOOT_CONFIGURATION_DRIFT; no qualifying receipt or attestation | AE:7567-7572; AE:7609-7617
PLR090 | PROFILE_BIND | PH.boot_configuration differs from body(S.boot_configuration).configuration | BOOT_CONFIGURATION_DRIFT; no qualifying receipt or attestation | AE:7567-7572; AE:7609-7617
PLR091 | LIVE_BIND | L.support_profile differs from ref(S) | PROFILE_MISMATCH; no qualifying receipt or attestation | AE:2048-2058; AE:7609-7617
PLR092 | LIVE_BIND | L.controller_host differs from S.controller_host | CONTROLLER_HOST_MISMATCH; no attestation | AE:2048-2058; AE:4666-4679
PLR093 | LIVE_BIND | L.postgresql_host differs from S.postgresql_host | POSTGRESQL_HOST_MISMATCH; no attestation | AE:2048-2058; AE:4666-4679
PLR094 | LIVE_BIND | L.postgresql_endpoint differs from S.postgresql_endpoint | coverage assertion over the applicable changed endpoint family NMR006-NMR016; the owning concrete row, not this alias, fixes the exact sequence | AE:2048-2058; AE:4666-4679; AE:7795-7803
PLR095 | LIVE_BIND | L.target_database_identity differs from EP.target_database_identity | NMR007 is the exact changed-target family; its concrete row identity (`ref(M)` plus exact `planned_runs[row_position]` member), expected-refusal carrier, and complete failure sequence are MATRIX_BODY_GAP; no attestation or epoch effect | AE:2048-2058; AE:7590-7594; AE:7795-7803
PLR096 | LIVE_TIME | COL.mode differs from QUALIFIED_CLOCK | PROFILE_UNQUALIFIED; no receipt or attestation | AE:2053-2058
PLR097 | LIVE_TIME | COL.phase differs from SUPPORT_PROFILE_PROJECTION | PROFILE_UNQUALIFIED; no receipt or attestation | AE:2053-2058
PLR098 | LIVE_TIME | COL has mode=QUALIFIED_CLOCK and phase=SUPPORT_PROFILE_PROJECTION but COL.clock_envelope is not the exact qualified current ClockEnvelope/v1 for S, or that envelope is stale, expired, replaced, or has the wrong host, boot, clock component, or synchronization epoch | CLOCK_EVIDENCE_STALE; no receipt, attestation, reservation, current pointer, or epoch transition | AE:2053-2058; AE:2278-2297; AE:4648-4659
~~~

#### Protected deployment-refusal code and matrix registry

The `DFR` registry preserves the complete closed
`DeploymentFailure.failure_code` scalar domain and the accepted-source boundary
of each named category. It is not a total code-selection algorithm.
`SOURCE_CATEGORY` means the source names the category but leaves the exact
failure member and complete sequence to the owning concrete matrix row.
`DIRECT_NMR` and `NMR_ALTERNATIVE` point to the only matrix-family code
constraints stated directly by the accepted prose. `CODE_TOKEN_ONLY` means the
token is present in the closed union but the accepted prose supplies no current
predicate that selects it. A category boundary never means “emit this code
whenever this predicate is true.”

~~~text
DFR001 | UNKNOWN_CONTRACT | unknown or unregistered kind/version at admission; missing bytes, digest mismatch, cycle, hidden dependency, and same-identity/different-body remain resolver invalidity with no source-selected deployment code | offending reference and candidate-projection position | SOURCE_CATEGORY | AE:310-325; AE:4658-4659; AE:7759-7761
DFR002 | DEPLOYMENT_POLICY_MISMATCH | policy target, surface, support-profile, release, or required-claim membership mismatch; issue-before-effective is outside this assigned boundary | P_D, candidate fields, policy sets, and locked target/profile/receipt carriers | SOURCE_CATEGORY | AE:4623-4646; AE:4745-4752; AE:4927-4930
DFR003 | DEPLOYMENT_POLICY_NONCURRENT | ref(P_D) is not the exact protected current policy, including displacement by a replacement; explicit revocation and expiry remain separate categories | locked policy slot and ref(P_D) | SOURCE_CATEGORY | AE:4623-4637; AE:4685-4690; AE:4917-4929
DFR004 | DEPLOYMENT_POLICY_REVOKED | the policy-administration boundary explicitly clears/revokes the current policy; replacement by another policy is noncurrent, and refused reinstall of a retired pair is an administration rule rather than this admission assignment | locked policy slot and ref(P_D) | SOURCE_CATEGORY | AE:4628-4637; AE:4923-4929
DFR005 | DEPLOYMENT_POLICY_EXPIRED | the protected conservative issue or consumption sample is not strictly before P_D.valid_until_unix_ns | P_D and owner-derived qualified-clock observation | SOURCE_CATEGORY | AE:4616-4621; AE:4648-4664; AE:4743-4752; AE:4927-4930
DFR006 | DEPLOYMENT_PARTITION_INVALID | the deployment partition or policy claim sequence is omitted, added, duplicated, reordered, or carries a wrong claim/tier/subject/campaign/prerequisite/current reference | P_D.required_deployment_claim_ids, D.deployment_tier_results, T_DEPLOYMENT, and locked current/prerequisite pointers | SOURCE_CATEGORY | AE:4789-4805; AE:4900-4910; AE:7759-7766
DFR007 | DEPLOYMENT_EVIDENCE_OPEN | a required current deployment-tier result is OPEN | offending T_DEPLOYMENT member and current pointer | SOURCE_CATEGORY | AE:4795-4800; AE:4900-4908; AE:7759-7762
DFR008 | DEPLOYMENT_EVIDENCE_FAIL | a required current deployment-tier result is FAIL; PREREQUISITE_BLOCKED has no independently selected code in the accepted prose | offending T_DEPLOYMENT member and current pointer | SOURCE_CATEGORY | AE:4795-4800; AE:4900-4908; AE:7759-7762
DFR009 | DEPLOYMENT_EVIDENCE_STALE | a required deployment-tier result is STALE; over-age acquisition is described as stale, but its exact code remains a concrete matrix value | offending T_DEPLOYMENT member, acquisition-age evidence, and current pointer | SOURCE_CATEGORY | AE:4795-4800; AE:4822-4830; AE:4900-4908; AE:7759-7764
DFR010 | DEPLOYMENT_EVIDENCE_SUPERSEDED | a required deployment campaign/result is superseded or its current pointer names the accepted replacement | C_D, affected T, supersession, and locked current pointer | SOURCE_CATEGORY | AE:4900-4908; AE:4923-4929; AE:5632-5661; AE:7759-7762
DFR011 | DEPLOYMENT_EVIDENCE_INVALIDATED | a deciding deployment record or required deployment result has a valid current invalidation | affected record/result, invalidation sequence, and locked current pointer | SOURCE_CATEGORY | AE:4900-4908; AE:4923-4929; AE:5632-5664; AE:7759-7762
DFR012 | DEPLOYMENT_EVIDENCE_ACQUISITION_INVALID | acquisition membership/order/run/procedure/projection shape is missing, extra, duplicated, reordered, cross-run, wrong-procedure, or projection-mismatched; unresolved/wrong-kind references remain structural invalidity, and cross-boot and stale/over-age conditions are excluded from this boundary | A_D, E_D, C_D planned-run/oracle order, and exact acquisition bindings | SOURCE_CATEGORY | AE:4754-4758; AE:4812-4830; AE:7761-7764
DFR013 | INVALID_QUALIFICATION_RECEIPT | Q_R is absent or a structurally valid resolved Q_R does not carry the required plan/profile/class/partition shape; malformed, wrong-kind, missing-body, and digest-invalid references remain structural invalidity without a source-selected code | D.qualification_receipt, Q_R, its class results, and qualification partitions | SOURCE_CATEGORY | AE:4412-4515; AE:4648-4659; AE:4692-4697; AE:7759-7763
DFR014 | EXPIRED_QUALIFICATION_RECEIPT | the protected conservative admission sample is not strictly before Q_R.valid_until_unix_ns | Q_R and attestation-issue observation | SOURCE_CATEGORY | AE:4616-4621; AE:4648-4664; AE:4748-4752; AE:7759-7763
DFR015 | QUALIFICATION_RESULT_NOT_PASS | Q_R or a bound EV-CLK, EV-PHY, or EV-CAP class result is not PASS | Q_R and its three exact class-result references | SOURCE_CATEGORY | AE:4412-4515; AE:4648-4657; AE:7759-7763
DFR016 | PROFILE_MISMATCH | the exact current ref(S), Q_R.support_profile, D.support_profile, or stable profile binding is not byte-identical; boot, host, endpoint, storage, closure, remote, and managed categories remain distinct | locked profile pointer, S, Q_R, D, and stable bindings | SOURCE_CATEGORY | AE:4394-4410; AE:4648-4679; AE:7564-7617
DFR017 | QUALIFICATION_EVIDENCE_INVALIDATED | a class, tier, or prerequisite bound by Q_R depends on a currently invalidated record | Q_R evidence closure and invalidation sequences | SOURCE_CATEGORY | AE:4412-4515; AE:4923-4930; AE:5632-5664; AE:7759-7763
DFR018 | QUALIFICATION_EVIDENCE_STALE | a class, tier, or prerequisite bound by Q_R is stale or no longer current, excluding supersession and invalidation | Q_R evidence closure and locked pointers | SOURCE_CATEGORY | AE:4412-4515; AE:4900-4930; AE:7759-7763
DFR019 | QUALIFICATION_CAMPAIGN_SUPERSEDED | the qualification campaign/result chain bound by Q_R was superseded | Q_R, plan/campaign results, selection disposition, and replacement pointers | SOURCE_CATEGORY | AE:4563-4576; AE:4923-4930; AE:5632-5661; AE:7759-7763
DFR020 | PLAN_MISMATCH | D.qualification_plan, Q_R.plan, or a class/result plan binding disagrees | D, Q_R, Q, and class results | SOURCE_CATEGORY | AE:4394-4454; AE:4692-4697; AE:7759-7763
DFR021 | PLAN_ACCEPTANCE_MISMATCH | D.qualification_plan_acceptance, Q_R.plan_acceptance, or the authenticated plan/profile/release/campaign acceptance binding disagrees | D, Q_R, A, Q, and protected acceptance | SOURCE_CATEGORY | AE:4394-4410; AE:4459-4463; AE:4692-4697; AE:7759-7763
DFR022 | RELEASE_MISMATCH | Q_R.release_digest, S.release_digest, D.installed_release_digest, or the allowed-release membership disagrees | Q_R, S, D, and P_D.allowed_release_digests | SOURCE_CATEGORY | AE:4394-4416; AE:4623-4627; AE:4648-4657; AE:4731-4732; AE:7759-7763
DFR023 | CLOCK_EVIDENCE_STALE | the required current clock envelope or qualified-clock observation is stale, replaced, expired, outside its monotonic bound, or carries stale synchronization evidence; profile, host, and boot mismatches are excluded | locked clock pointer, ClockEnvelope, O_I, COL, and affected observation | SOURCE_CATEGORY | AE:2053-2058; AE:2278-2297; AE:4648-4664; AE:4759-4779; AE:7759-7764
DFR024 | STORAGE_EVIDENCE_STALE | required current storage evidence or storage identity is stale; stable profile-component/configuration equality failures are excluded | D.storage_identity, locked live storage evidence, and deciding storage evidence | SOURCE_CATEGORY | AE:4666-4710; AE:7554-7617; AE:7759-7764
DFR025 | ROLE_GRANT_SET_MISMATCH | D.role_grant_set, body(WI).role_grant_set, profiler RG, and finalizer RG are not the same canonical ref(RG) | D, RG, WI, profiler carrier, and finalizer carrier | SOURCE_CATEGORY | AE:4710-4720; AE:4832-4847; AE:7763-7766
DFR026 | ROLE_INVENTORY_INCOMPLETE | the locked role/grant graph has an unenumerated, unclassifiable, unresolved, unattributed, duplicate, extra, OID/name-conflicting, inherited, PUBLIC, owner, default-privilege, or function-mediated path; overlap with writer inventory has no source-defined precedence | complete locked role/grant graph | SOURCE_CATEGORY | AE:4832-4847; AE:7763-7766
DFR027 | WRITER_INVENTORY_MISMATCH | D.writer_inventory, profiler WI, or finalizer WI differs, or the service/writer graph has a missing, extra, duplicated, unresolved, unattributed, or out-of-surface path; overlap with role inventory is resolved only by the concrete row | D, WI, and complete locked writer/service graph | SOURCE_CATEGORY | AE:4711-4720; AE:4832-4847; AE:7763-7766
DFR028 | BOOT_CONFIGURATION_DRIFT | CH.boot_configuration, PH.boot_configuration, L.boot_environment_configuration, and the stable profile boot configuration do not agree | S.boot_configuration, CH, PH, and L | SOURCE_CATEGORY | AE:2044-2052; AE:4666-4673; AE:7567-7572; AE:7761-7764
DFR029 | BOOT_IDENTITY_CHANGED | D, L, the issuance envelope, or a deciding acquisition crosses the exact per-boot identity | D, L, ClockEnvelope, O_I, and affected O_A | SOURCE_CATEGORY | AE:2053-2058; AE:2278-2281; AE:4670-4673; AE:4704-4707; AE:4759-4765
DFR030 | CONTROLLER_HOST_MISMATCH | controller-host change | exact NMR017 row carriers | DIRECT_NMR(NMR017) | AE:4666-4679; AE:7564-7572; AE:7799-7803
DFR031 | POSTGRESQL_HOST_MISMATCH | PostgreSQL-host change | exact NMR018 row carriers | DIRECT_NMR(NMR018) | AE:4666-4679; AE:7564-7572; AE:7799-7803
DFR032 | ENDPOINT_IDENTITY_CHANGED | no accepted prose predicate selects this token; changed endpoint identity is assigned to DFR033 or DFR034 as the matrix schema dictates | none beyond scalar-domain membership | CODE_TOKEN_ONLY | AE:4207-4244; AE:7795-7803
DFR033 | ENDPOINT_DRIFT | one allowed endpoint-family classification for NMR006-NMR016 | owning row's exact endpoint carriers | NMR_ALTERNATIVE(NMR006-NMR016) | AE:1996-2022; AE:4666-4679; AE:7795-7811
DFR034 | ENDPOINT_TOPOLOGY_MISMATCH | the other allowed endpoint-family classification for NMR006-NMR016 | owning row's exact endpoint/topology carriers | NMR_ALTERNATIVE(NMR006-NMR016) | AE:4666-4679; AE:7564-7587; AE:7795-7811
DFR035 | CLOSURE_POLICY_MISMATCH | Q, Q_R, D, S, L, or the live guard does not bind the same exact ClosurePolicyLimits/v1 reference and values | closure-policy carriers | SOURCE_CATEGORY | AE:2062-2072; AE:2284-2285; AE:4696-4697; AE:7609-7617; AE:7765-7767
DFR036 | CLOSURE_GUARD_UNSUPPORTED | a required positive finite database timeout or adapter connection guard is unsupported, disabled, zero, or replaced by an unbounded fallback | ClosurePolicyLimits/v1 and guard-capability evidence | SOURCE_CATEGORY | AE:7546-7549; AE:7610-7617; AE:7692-7697; AE:7745-7747; AE:7765-7767
DFR037 | REMOTE_POSTGRESQL_UNSUPPORTED | NMR001-NMR003 remote topology/transport families | exact owning row carriers | DIRECT_NMR(NMR001-NMR003) | AE:4674-4679; AE:7583-7587; AE:7792-7803
DFR038 | MANAGED_POSTGRESQL_UNSUPPORTED | NMR004-NMR005 managed topology/transport families | exact owning row carriers | DIRECT_NMR(NMR004-NMR005) | AE:4674-4679; AE:7583-7587; AE:7792-7803
DFR039 | FENCE_RESULT_INVALID | the conditional fence result or occupied-fence evidence/handoff does not equal the fixed slot and proposed epoch | fence slot, persistent evidence, handoff, reserved row, and D | SOURCE_CATEGORY | AE:4849-4867; AE:7751-7756; AE:7765-7767
DFR040 | PROFILE_UNQUALIFIED | the selected profile/live projection is unavailable, mixed, nonexact, generic, or has wrong qualified-observation mode/phase without a more specific source category | S, L, COL, live-coherence carriers, and profile evidence | SOURCE_CATEGORY | AE:2053-2058; AE:7554-7617; AE:7765-7767
DFR041 | HEALTH_CHECK_FAILED | the protected deployment health result is not PASS | candidate projection and deciding EV-DEP health evidence | SOURCE_CATEGORY | AE:2950-2987; AE:7757; AE:7771-7777
DFR042 | ATTESTATION_CONSTRUCTION_INVALID | checked validity addition or generation allocation overflows, or canonical D cannot be constructed after every separately named operand and equality has passed; it is not a catch-all for those equality failures | complete resolved finalizer operands, observation, checked arithmetic, and reservation state | SOURCE_CATEGORY | AE:4648-4664; AE:4743-4752; AE:4849-4870; AE:4900-4915
~~~

The forty-two rows occur once each in the source-union order. They preserve
the scalar domain even where current predicate membership is
`CODE_TOKEN_ONLY`; a generic alias or semantic similarity cannot create a
selection rule.

`DF_EFFECT` is common to every concrete failed admission result. Each exact
`DeploymentFailure` member is
`{failure_code:the uppercase enum,evidence:FailureEvidence/v1 REF,oracle_id}`.
The referenced `FailureEvidence.failure_code` remains its own lowercase
`ContractId`; the evidence proves the enum member through the named oracle
without string equality or the recovery-refusal conversion. The result has
`authority=NONE`, `result=FAIL`, exact candidate projection, campaign,
attempt ID, subject revision, and observed qualification/profile references or
literal `NONE`. Its `failures` sequence is nonempty, duplicate-free, and sorted
by ASCII enum code then canonical evidence-reference bytes. The stable key is
`(campaign,deployment_attempt_id)`; exact bytes replay and changed bytes
conflict. It creates no attestation, reservation, attestation/current-reserved
pointer, active-epoch pointer, or epoch transition. Success emits no failed
result (`AE:4207–4261`, `AE:4849–4915`, `AE:5610–5630`,
`AE:7781–7803`).

For a concrete planned negative member, `DF_EFFECT` additionally requires the
actual `FailedDeploymentResult/v1` to satisfy the row's prebound refusal
oracle exactly, including its candidate projection and complete ordered
`failures` sequence. An added, omitted, substituted, or reordered failure
makes the row's oracle fail. This is a semantic result-comparison obligation,
not a current matrix-to-result reference path. The source does not define
precedence, arbitrary choice, or an all-matching-categories sequence. Until a
concrete matrix supplies the acyclic expected-refusal carrier, exact
field/member path, runtime/template treatment, exact expected candidate
projection, and complete future failure sequence, this inventory makes no
present expected-failure-sequence instantiation and no
completeness claim over concrete deployment rows
(`AE:5610–5627`, `AE:7781–7803`).

The policy also requires `ref(S)`, `D.installed_release_digest`,
`D.target_database_identity`, and `D.target_surface_digest` in its exact
allowed sets and requires its claim sequence to equal the complete
`T_DEPLOYMENT` claim sequence (`AE:4623–4646`,
`AE:4789–4805`). Those are membership/sequence predicates rather than
singleton copy edges. `D.postgresql_settings` remains one required typed
canonical reference member, but the accepted finalizer clauses assign no
source-to-destination copy for it. This inventory therefore does not invent
one; any additional protected equality for that field must be identified as an
issue-106 proposal detail rather than an accepted source fact.

The scalar side of `DeploymentAttestation` is independently enumerable.
For attestation `D`, support profile `S=body(D.support_profile)`,
qualification receipt `Q_R=body(D.qualification_receipt)`, policy
`P_D=body(D.deployment_admission_policy)`, and the locked live target and
reservation, the protected finalizer requires:

- `D.kind="hindsight-postgresql-deployment-attestation"`,
  `D.schema_version=1`, and `D.health="PASS"`;
- `D.installed_adapter_digest=S.adapter_release_digest`;
- `D.installed_admission_controller_digest=
  S.admission_controller_release_digest`;
- `D.installed_release_digest=Q_R.release_digest=S.release_digest`;
- `D.migration_digest=S.migration_digest` and
  `D.protected_schema_digest=S.protected_schema_digest`;
- `D.target_database_identity` and `D.target_surface_digest` equal the locked
  target-surface slot, occur in `P_D`'s respective allowed sets, and equal the
  target/surface carried by the recomputed role-grant and writer inventories;
- `D.target_generation` is present and has the required
  `TargetGeneration` domain, but its value source is `SOURCE_UNASSIGNED` by
  the accepted finalizer clauses;
- `D.lineage_key_digest` equals the successor-canonical SHA-256, including LF,
  of `{protocol_family="hindsight-postgresql-publication",protocol_version=1,
  target_database_identity=D.target_database_identity,
  target_surface_digest=D.target_surface_digest}`;
- `D.proposed_publication_epoch` equals the checked-next epoch allocated in the
  locked reserved-activation row, and
  `D.admission_generation=D.proposed_publication_epoch`;
- `D.issued_at_unix_ns` equals the trusted upper bound of the owner-derived
  `DEPLOYMENT_ATTESTATION_ISSUE` observation; and
- `D.valid_until_unix_ns=min(P_D.valid_until_unix_ns,
  Q_R.valid_until_unix_ns,
  checked_add(D.issued_at_unix_ns,P_D.attestation_validity_ns))`.

The protected resolver, profiler, qualification boundaries, and admission
finalizer enforce every applicable named `PRD001`–`PRD065` role member. The
finalizer also enforces every `DEQ001`–`DEQ063` member and the upstream
`QEQ001`–`QEQ042` chain, the literal tier partitions, and every applicable
`PLV001`–`PLV078` predicate. Each `PLR001`–`PLR098` row retains its stated
source outcome; coverage-partition rows create no duplicate failure demand.
For a concrete planned admission member, the owning concrete `ref(M)` and
exact `planned_runs[row_position]` member govern the expected refusal. The
applicable `NMR` row and `DF_EFFECT` preserve its source-fixed constraints.
The acyclic expected-refusal carrier, exact field/member path,
runtime/template treatment, and complete future failure sequence remain
`MATRIX_BODY_GAP`; no present exact failure selection is enumerated. The
accepted source supplies no general subset, precedence, or
all-true-predicates rule and no present concrete matrix member.

The positive admission predicates still include the exact current
policy/profile/clock/live carriers, `Q_R.result="PASS"`, the complete
deployment `PASS` partition, and the oldest deciding acquisition age strictly
below the policy maximum. Equality at a deadline, stale currentness, a scalar
mismatch, missing member, overflow, or failed checked arithmetic produces no
attestation or reservation (`AE:2950–2987`, `AE:4623–4802`,
`AE:7554–7617`, `AE:7765–7811`).

#### Evidence-operation source members

The accepted evidence owner exposes exactly the following eleven protected
interfaces. `CS` means caller-supplied; `DB` means resolved, sampled, or
constructed by the protected owner. Every interface is
`SECURITY DEFINER` with fixed search path and `PUBLIC` revoked. The three plan
acceptances are callable only by the isolated plan-authority login; campaign,
acquisition, observation, run-result, and invalidity registration only by the
isolated producer login; subject and disposition mutation only by the isolated
evidence-authority login; and current-result reads only by the named protected
qualification, admission, activation, and stage-function owners. No caller has
direct relation access or the owner-internal evaluator (`AE:4932–5124`).

1. `ACCEPT_EVIDENCE_CAMPAIGN_PLAN(plan:EvidenceRef,
   acceptance:EvidenceRef)`. CS is exactly one
   `EvidenceCampaignPlan/v1` reference and one
   `EvidenceCampaignPlanAcceptance/v1` reference. DB authenticates the session
   principal against `acceptance.operator_principal`, resolves both canonical
   bodies and their recursive references, expands the exact basis, claim set,
   tier, subject, and ordered `planned_runs`, and verifies strict creation,
   issue, validity, and basis-equality predicates. Current-subject equality is
   checked later by campaign registration; acceptance neither supplies nor
   replaces that current value. In one transaction it
   preserves the plan body and inserts the authenticated acceptance under
   stable key `plan`. Exact bytes replay; another acceptance identity or
   changed bytes for that plan conflict. Any failed authentication, expansion,
   currentness, time, type, or equality check has no effect. It creates no
   campaign or current result (`AE:3702–3724`, `AE:5136–5220`).

2. `ACCEPT_HISTORICAL_CORPUS_PLAN(plan:EvidenceRef,
   acceptance:EvidenceRef)`. CS is one `HistoricalCorpusPlan/v1` and one
   `HistoricalCorpusPlanAcceptance/v1` reference. DB reconstructs the complete
   frozen reader registry, member vector, reader/execution bindings, public
   fixture/generator coverage, historical-real cell expansion, plan digests,
   acceptance principal, and strict times. It atomically inserts the
   authenticated acceptance under stable key `plan` while preserving every
   resolved immutable body. Exact bytes replay; a changed body, digest,
   expansion, or second identity conflicts; any unresolved reader, wrong
   ordering/cardinality, private/public boundary error, or failed
   authentication/time predicate has no effect. It creates no campaign or
   result (`AE:4022–4194`, `AE:5426–5595`).

3. `ACCEPT_QUALIFICATION_PLAN(plan:EvidenceRef,
   acceptance:EvidenceRef)`. CS is one `QualificationPlan/v1` and one
   `QualificationPlanAcceptance/v1` reference. DB resolves the exact referenced support
   profile and release-subject bodies, expands every cell/case/seed/allocation and
   `planned_runs` member, and checks the profile, release, closure limits,
   acceptance principal, strict times, cardinalities, and exact canonical
   equality. It atomically inserts the acceptance under stable key `plan` and
   preserves resolved bodies. Exact bytes replay; a second identity or changed
   plan/acceptance conflicts; any unresolved profile/subject, malformed
   expansion, authentication, time, or equality failure has no effect. It creates no
   class result, receipt, campaign, or current result (`AE:2861–2947`,
   `AE:4298–4410`).

4. `REGISTER_EVIDENCE_CAMPAIGN(campaign:EvidenceRef)`. CS is exactly one
   `EvidenceCampaign/v1` reference. DB resolves its already protected campaign
   plan and acceptance, locks the tier's current subject, independently expands
   the plan basis, validates every claim, run, oracle, stimulus, prestate,
   reader binding, tool, acquisition procedure, limit, expected projection,
   start observation, and strict campaign interval, and requires byte equality
   with the campaign's complete schedule. The immutable campaign is keyed by
   `campaign_id`. The first accepted campaign becomes the selected root for
   each named `(claim_id,tier)`; later campaigns remain unselected until an
   authorized supersession. Campaign insertion, initial tier-state/result
   construction, and all affected current-pointer replacements are one
   transaction. Exact campaign bytes replay; same identity with changed bytes,
   duplicate/omitted/reordered runs, stale subject, or any failed predicate
   conflicts or refuses with no partial effect (`AE:5136–5288`).

5. `ACQUIRE_DEPLOYMENT_EVIDENCE(campaign:EvidenceRef,run_id:ContractId,
   oracle_id:OracleId)`. CS is only the exact accepted campaign identity, run
   ID, and oracle ID. DB authenticates the isolated producer, resolves the
   accepted deployment run, locks current policy, support profile, qualified
   clock, catalog, and service-registry scope, derives a fresh acquisition
   identity, invokes only the planned acquisition procedure, and constructs
   the observed `OracleProjection/v1`, its
   `DEPLOYMENT_EVIDENCE_ACQUIRE` `ProtectedTimeObservation/v1`, and
   `DeploymentEvidenceAcquisition/v1`. Those three immutable bodies, their
   shared campaign/run/oracle/envelope/boot bindings, and their ordinary-body
   dependencies commit atomically. The caller supplies no identity,
   projection, timestamp, role-grant set, or writer inventory. No interface may
   replace or reissue bytes under an existing acquisition identity; an
   identity collision, stale binding, wrong run/oracle/procedure, clock loss,
   or failed live projection has no partial effect. The source exposes no
   caller-chosen retry key; replay means resolving the already committed
   server-generated identity, not issuing a second body (`AE:3919–3955`,
   `AE:4975–4985`).

6. `OBSERVE_EVIDENCE_TIME(phase:
   CAMPAIGN_START|EVIDENCE_START,subject_key_digest:Digest,
   clock_epoch:EvidenceRef|"NONE")`. CS is exactly those three operands and no
   timestamp. DB derives protected registration time or the conservative
   qualified-clock upper bound, validates the phase-specific subject
   projection and supplied epoch/`NONE` branch, and inserts one immutable
   observation under stable key `(phase,subject_key_digest)`. Exact bytes
   replay; changed mode, clock, epoch, or bytes conflict. Unsupported phase,
   stale/replaced clock, arithmetic failure, overflow, equality at a bound, or
   subject mismatch inserts nothing. This call cannot issue
   `EVIDENCE_COMPLETE`; completion, receipt, attestation, and disposition
   observations exist only inside their finalizers (`AE:2287–2360`,
   `AE:4938–4973`).

7. `REGISTER_EVIDENCE_RUN_RESULT(input:
   EvidenceRunRegistrationInput)`. CS is exactly the closed input
   `{campaign,execution,failure?,records[i],retained_artifacts[i],run_id,
   start_time_observation}` and its nested record/failure inputs; it contains
   no caller-completed result or timestamp. DB resolves the accepted plan and
   run, all start observations and retained bodies, expected and observed
   projections, oracle contracts, status-specific failure proof, transitive
   historical-real and reader bindings, and every required deployment
   acquisition. It then samples completion, constructs all required
   `EVIDENCE_COMPLETE` observations, `EvidenceRecord/v1` bodies, optional
   `EvidenceRunFailure/v1`, and the one `EvidenceRunResult/v1`, invokes the
   evaluator, and inserts those immutable bodies plus all affected tier
   results/current pointers in one transaction. Stable result key is
   `(campaign,run_id)` and record key is
   `(campaign,run_id,oracle_id)`. Exact bytes replay; changed bytes under
   either key conflict. Missing/extra/duplicate/reordered inputs, wrong branch,
   wrong clock/acquisition/private/reader binding, failed comparison, or any
   insertion/pointer failure inserts none of the operation's bodies or
   pointers (`AE:3836–3968`, `AE:4987–5003`, `AE:5289–5425`).

8. `REGISTER_EVIDENCE_INVALIDITY_FINDING(finding:EvidenceRef)`. CS is exactly
   one `EvidenceInvalidityFinding/v1` reference. DB resolves the named evidence,
   oracle contract, expected and observed projections, and independently
   recomputes the one closed defect code and `result="CONFIRMED"`. It
   preserves the immutable typed finding body. The accepted source assigns no
   finding-registry uniqueness key or same-evidence conflict rule; only the
   later authority-bearing `EvidenceRecordInvalidation` registry is unique on
   `evidence`. Exact repetition of the same typed body is ordinary-body
   identity replay, while any additional relation key is an issue-106 proposal
   detail. Unknown defect, free text, failed recomputation, wrong
   evidence/oracle/projection, or an attempt to
   invalidate a valid deciding `FAIL` refuses without effect. This operation is
   nonauthorizing: it creates no `EvidenceRecordInvalidation`, invokes no
   verdict-changing evaluator path, and changes no tier result or pointer
   (`AE:3957–3968`, `AE:7132–7167`).

9. `SET_CURRENT_EVIDENCE_SUBJECT(tier:DESIGN|IMPLEMENTATION|RELEASE|DEPLOYMENT,
   subject_revision:EvidenceRef)`. CS is one
   closed tier token and one typed subject reference. DB locks that tier's
   current-subject slot, resolves the old and replacement bodies, enumerates
   every affected claim, and supplies the locked subject to the internal
   evaluator; the caller supplies no result or pointer. In one transaction it
   preserves subject history, replaces the one tier current pointer, derives
   each affected immutable state/result, and replaces each affected
   `(claim_id,tier)` current-result pointer. Repeating the installed reference
   is a no-change exact replay; a valid different reference is a new authorized
   subject transition, not a revision-keyed parallel current slot. Wrong tier,
   type, scope, or unresolved reference refuses without a partial subject or
   result change (`AE:5136–5146`, `AE:5629–5720`).

10. `APPLY_EVIDENCE_DISPOSITION(authority_subject:
    EvidenceRecordInvalidationAuthoritySubject|
    EvidenceCampaignSupersessionAuthoritySubject,
    authority_receipt:EvidenceRef)`. CS is the complete selected authority
    subject and exact
    `EvidenceDispositionAuthorizationReceipt/v1` reference, never a finalized
    disposition. DB authenticates the authority principal; hashes the complete
    subject; locks its evidence, current subject, campaign chain, and affected
    results; samples the qualified application time; constructs the
    `EVIDENCE_DISPOSITION_APPLY` observation; and derives exactly one final
    invalidation or supersession. Observation, disposition, all newly derived
    immutable tier results, and every affected current-pointer replacement are
    one transaction. Invalidation is unique on `evidence`; supersession is
    unique on `(campaign,claim_id,tier,prior_result)`. Exact bytes replay;
    changed bytes, a stale/skipped/cyclic/ambiguous branch, subject or receipt
    mismatch, clock loss, arithmetic failure, overflow, or deadline equality
    conflicts or refuses with no effect. Later receipt expiry never reverses a
    committed disposition (`AE:7039–7202`).

11. `READ_CURRENT_EVIDENCE_TIER_RESULT(claim_id:ClaimId,
    tier:DESIGN|IMPLEMENTATION|RELEASE|DEPLOYMENT)`. CS is only the
    exact claim/tier key. DB resolves the single protected current slot
    for that key and returns its exact `EvidenceTierResult/v1` reference; the
    caller cannot name a stored candidate, subject revision, verdict, or
    replacement pointer. The call inserts, updates, and deletes nothing.
    Missing/unknown keys or a result that fails the consuming operation's
    currentness predicate refuse consumption; repeated reads of unchanged
    state return the same reference (`AE:4958–4965`, `AE:5119–5124`).

The owner-internal evaluator is not a twelfth callable. It receives no login
grant and is reached only inside campaign registration, run-result
registration, subject replacement, and disposition application when those
operations require result changes. Qualification-class and receipt finalizers
are separately owned protected interfaces and are not members of this
eleven-call evidence-owner roster.

`EvidenceRecordRegistrationInput` has exactly
`{deployment_evidence_acquisition?,expected_projection,observed_projection,oracle_contract,oracle_id,record_id,start_time_observation}`.
`deployment_evidence_acquisition?` is literal `"NONE"` or one typed
`DeploymentEvidenceAcquisition/v1` reference under the branch below.
`EvidenceRunFailureRegistrationInput` has exactly
`{deployment_evidence_acquisition?,execution,expected_projection,observed_projection,oracle_contract}`,
where `execution` is one of `SKIPPED`, `ABORTED`,
`SHORTENED_BUDGET`, `OVER_BUDGET`, `GENERATOR_FAILURE`,
`UNREPRODUCIBLE_FAILURE`, or `UNEXPLAINED`, and
`observed_projection` is a typed `OracleProjection/v1` reference or literal
`"NO_LIVE_PROJECTION"`. `EvidenceRunRegistrationInput` has exactly
`{campaign,execution,failure?,records[i],retained_artifacts[i],run_id,start_time_observation}`,
where `execution` adds `EXECUTED` and `failure?` is literal `"NONE"` or the
exact failure input (`AE:3866–3918`).

Campaign registration consumes one accepted `EvidenceCampaignPlan/v1` and
`EvidenceCampaignPlanAcceptance/v1`. It requires exact equality for
`campaign_id`, `claim_ids[i]`, `tier`, `subject_revision`, and
`planned_runs[i]`. Each planned-run member fixes
`{run_id,claim_ids[i],claim_definitions[i],claim_predicates[i],tier,evidence_class,conformance_prestate?,stimulus,tool,acquisition_procedure,limits,reader_execution?,oracles[i].{oracle_id,oracle_contract,expected_projection}}`.
For a historical-real member, its `stimulus` transitively binds the exact
same-ordinal `HistoricalRealEvidencePlanCell.real_artifact_bindings[i]` through
`EvidenceStimulus.input_artifact`; `CampaignRunRequirement` itself has no
`real_artifact_binding` field. The authenticated basis expands in its declared
semantic order; no later record may change, add, omit, or reorder those members
(`AE:2997–3014`, `AE:5136–5288`).

For each `EvidenceRecordRegistrationInput records[i]`, the protected registrar
constructs one `EvidenceRecord` with exactly:

- planned-run copies
  `{campaign,claim_definitions[i],claim_ids[i],claim_predicates[i],conformance_prestate?,evidence_class,limits,reader_execution?,run_id,subject_revision,tier,tool}`;
- the transitive historical-real copy
  `real_artifact_binding?`, derived by resolving the planned
  `CampaignRunRequirement.stimulus` and, only for
  `stimulus_class=HISTORICAL_REAL_ARTIFACT`, requiring
  `EvidenceStimulus.input_artifact` to equal the same-ordinal
  `HistoricalRealEvidencePlanCell.real_artifact_bindings[i]`; otherwise the
  field is literal `"NONE"`;
- input members
  `{deployment_evidence_acquisition?,expected_projection,observed_projection,oracle_contract,oracle_id,record_id,start_time_observation}`;
- database-derived completion members
  `{completed_at_unix_ns,completion_time_observation,started_at_unix_ns}`,
  where start time is the trusted upper bound of the exact start observation
  and completion is sampled inside result registration; and
- `acquisition_procedure` from the planned run and
  `oracle_result` recomputed as `PASS` exactly when every independently
  observed tagged field equals the prebound expected projection, otherwise
  `FAIL`.

Its stable key is `(campaign,run_id,oracle_id)`. `expected_projection` equals
the planned oracle requirement. Expected and observed projections name the
same oracle contract, definition, ordered claim-predicate references, field
names, field order, and the 22 exact value domains above
(`AE:3836–3864`, `AE:5289–5356`).

For a nonexecuted run, the registrar constructs one `EvidenceRunFailure` with
exactly
`{campaign,completed_at_unix_ns,completion_time_observation,conformance_prestate?,deployment_evidence_acquisition?,execution,expected_projection,kind,observed_projection,oracle_contract,oracle_id="OR-EVID",real_artifact_binding?,result="CONFIRMED",run_id,schema_version,start_time_observation,started_at_unix_ns}`.
Campaign, run, prestate, real binding, tool/procedure/limits expectations, and
time interval come from the same planned run and protected observations.
`CONFIRMED` is derived only when the independent `OR-EVID` contract proves the
exact status (`AE:3866–3887`, `AE:5357–5379`).

Every accepted run produces one `EvidenceRunResult` with exactly
`{campaign,claim_definitions[i],claim_ids[i],claim_predicates[i],clock_bindings[i],completed_at_unix_ns,completion_time_observation,conformance_prestate?,evidence_class,evidence_records[i],execution,failure_evidence?,kind,oracle_result,real_artifact_binding?,reader_execution?,run_id,schema_version,start_time_observation,started_at_unix_ns,subject_revision,tier}`.
The stable key is `(campaign,run_id)`. The common campaign/run/claim/tier/
subject/class/prestate/reader members copy the planned run exactly. The
real-artifact member instead follows the exact transitive stimulus binding
defined below.

The result branch is total:

- `execution=EXECUTED` requires `failure_evidence="NONE"` and exactly one
  `EvidenceRecord/v1` reference for every required oracle in oracle order;
  `oracle_result=PASS` iff all records recompute `PASS`, otherwise `FAIL`.
- Every nonexecuted status requires the matching
  `EvidenceRunFailure/v1` reference, empty `evidence_records`, and
  `oracle_result=FAIL`; result times and observations copy that failure.

`retained_artifacts[i]` is a registration input dependency, not a caller-made
result field. Every referenced artifact must resolve before the result,
records, and their ordinary-body dependencies are inserted atomically
(`AE:3889–3918`, `AE:5331–5425`).

Time and boot binding members are explicit.
`QualificationClockEpoch` is exactly
`{boot_identity,boot_ordinal,clock_envelope,kind,plan,predecessor_clock_epoch?,schema_version}`.
`EvidencePhaseClockBinding` is exactly
`{boot_identity,clock_envelope,clock_epoch?,phase,subject_key_digest,time_observation}`,
where `phase` is `DEPLOYMENT_EVIDENCE_ACQUIRE`, `EVIDENCE_START`, or
`EVIDENCE_COMPLETE`. Qualification campaigns include every run, record, and
applicable acquisition observation ordered by phase then subject digest and
bind the current qualified epoch. Deployment campaigns use
`clock_epoch="NONE"` but bind the exact deployment envelope and actual boot
identity. Other campaigns have an empty sequence. Epoch one has ordinal 1 and
no predecessor; later epochs are checked-next and atomically advance the
current pointer (`AE:3889–3905`, `AE:5379–5410`).

The acquisition/private/reader branches are exact:

- every deployment record with a live projection, and a deployment failure
  with a live projection, has the one acquisition body that names those exact
  observed bytes; pre-acquisition failure pairs
  `observed_projection="NO_LIVE_PROJECTION"` with acquisition `"NONE"`;
  every nondeployment member has acquisition `"NONE"`;
- `EV-LEG` result and records copy the planned non-`NONE`
  `reader_execution` and use its bound reader tool; every other evidence class
  has `reader_execution="NONE"`; and
- for a historical-real run, `CampaignRunRequirement.stimulus` resolves an
  `EvidenceStimulus/v1` whose non-`NONE` `input_artifact` is the same-ordinal
  `HistoricalRealEvidencePlanCell.real_artifact_bindings[i]` value.
  `EvidenceRecord.real_artifact_binding`,
  `EvidenceRunFailure.real_artifact_binding`, and
  `EvidenceRunResult.real_artifact_binding` copy that transitively resolved
  value. Every non-historical-real stimulus makes all three fields literal
  `"NONE"`. No `CampaignRunRequirement.real_artifact_binding` member exists.

Wrong pairings, cross-run or cross-oracle observations, and producer-selected
completion bounds refuse (`AE:5204–5288`, `AE:5331–5418`,
`AE:5580–5612`).

`EvidenceInvalidityFinding` has exactly
`{evidence,expected_projection,finding_code,kind,observed_projection,oracle_contract,oracle_id,result="CONFIRMED",schema_version}`.
`finding_code` is exactly one of
`ACQUISITION_PROCEDURE_INVALID`,
`ORACLE_IMPLEMENTATION_NOT_INDEPENDENT`,
`REQUIRED_INPUT_INVALID`, `TOOL_IDENTITY_INVALID`,
`LIMITS_MISREPORTED`, or `RETAINED_EVIDENCE_CORRUPT`; `oracle_id` is exactly
`OR-ID`, `OR-EVID`, or `OR-ACL`. Registration is nonauthorizing and succeeds
only after independent recomputation; it neither creates an invalidation nor
changes a verdict (`AE:3957–3968`, `AE:7132–7165`).

The protected evaluator constructs `EvidenceTierState` and
`EvidenceTierResult` from current subject and registered evidence. Their
same-named source members are
`{campaign?,claim_definition?,claim_id,claim_predicate?,invalidations[i],predecessor_result?,prerequisite_results[i],run_results[i],selection_disposition?,subject_revision,tier}`.
The result additionally has
`{evidence_state_digest,kind,result,schema_version}`.
`subject_revision` is read from the locked current subject, never caller
supplied. `run_results[i]` is the complete planned order for the selected
campaign; `invalidations[i]` is complete digest/disposition order;
`prerequisite_results[i]` is the complete current earlier-tier order;
predecessor and selection are both `"NONE"` at the root or both the immediate
prior result and exact supersession edge. `evidence_state_digest` hashes the
complete canonical state including LF. The ordered verdict branches are
`NOT_REQUIRED`, `STALE`, `OPEN`, `FAIL`, `PREREQUISITE_BLOCKED`, and
`PASS` with the exact total predicates at `AE:5629–5720`. Updating a run,
subject, invalidation, supersession, or prerequisite pointer atomically writes
all affected immutable results and current pointers.

Disposition authority uses these two exact authority subjects:

- `EvidenceRecordInvalidationAuthoritySubject.{affected_claim_ids[i],campaign,disposition_id,evidence,invalidity_evidence,issued_at_unix_ns,reason,subject_kind="hindsight-postgresql-evidence-record-invalidation",subject_revision,tier,valid_until_unix_ns}`;
- `EvidenceCampaignSupersessionAuthoritySubject.{campaign,claim_id,disposition_id,issued_at_unix_ns,prior_result,reason,replacement_campaign,subject_kind="hindsight-postgresql-evidence-campaign-supersession",subject_revision,tier,valid_until_unix_ns}`.

`EvidenceDispositionAuthorizationReceipt` has exactly
`{authority_principal,decision="AUTHORIZE",issued_at_unix_ns,kind,schema_version,subject_digest,subject_kind,valid_until_unix_ns}`.
`subject_digest` hashes the complete matching authority subject including LF.
Receipt and subject kind/times/values match exactly.

`APPLY_EVIDENCE_DISPOSITION` consumes the exact subject and receipt, locks
the affected evidence/current results, takes a new qualified sample, derives
`ProtectedTimeObservation` with `phase=EVIDENCE_DISPOSITION_APPLY`, requires
`trusted_upper_bound_unix_ns < valid_until_unix_ns`, and then constructs
exactly one final body:

- `EvidenceRecordInvalidation.{affected_claim_ids[i],application_time_observation,authority_receipt,campaign,disposition_id,evidence,invalidity_evidence,issued_at_unix_ns,kind,reason,schema_version,subject_revision,tier,valid_until_unix_ns}`; or
- `EvidenceCampaignSupersession.{application_time_observation,authority_receipt,campaign,claim_id,disposition_id,issued_at_unix_ns,kind,prior_result,reason,replacement_campaign,schema_version,subject_revision,tier,valid_until_unix_ns}`.

Invalidation requires the complete ordered record `claim_ids`, exact registered
finding, and one tier; it atomically recomputes every affected pointer.
Supersession requires the exact current `prior_result` and a different
replacement campaign for the same claim, tier, and current subject; it appends
one contiguous acyclic edge and atomically recomputes affected pointers.
Clock loss, mismatch, arithmetic failure, overflow, or deadline equality
inserts no observation or disposition and changes no result
(`AE:7039–7202`).

### Publication, private evidence, manifest, and activation copies

`PreStageExpiryObservation` and `R` use
`{clock_envelope,monotonic_anchor_lower_ns,monotonic_sample_upper_ns,monotonic_validity_deadline_lower_ns,wall_upper_at_anchor_unix_ns,forward_rate_error_numerator,forward_rate_error_denominator,elapsed_upper_ns,forward_rate_error_upper_ns,trusted_upper_bound_unix_ns,approval_expiry_unix_ns,qualification}`.
They require
`sample>=anchor`, `sample<monotonic_deadline`;
`elapsed=sample-anchor`;
`rate_error=ceil(elapsed*numerator/denominator)`;
`trusted_upper=wall_anchor+elapsed+rate_error`; and
`trusted_upper<approval_expiry` for current/valid, otherwise late.
All fields are UInt128, denominator positive, fraction reduced, and overflow or
deadline equality refuses. For stage J the predecessor is `NONE`; for P it is
`digest(J)`; plan, approval, authorization, admission, envelope, and expiry
copy the proposed stage (`AE:6597–6718`).

Together with stage references in the roster, the stage bodies expose these
complete protected paths:

- `J.{action_binding,admission,approval,approval_expiry_unix_ns,authorization_receipt,expected_target_generation,kind,lineage,operation_identity,plan,pre_stage_expiry_observation,preserved_cohort_digest,protocol_family,protocol_version,schema_version,selected_cohort_digest,target_database_identity,target_surface_digest}`;
- `P.{admission,journal_digest,kind,lineage,pre_stage_expiry_observation,schema_version}`;
- `R.{admission,approval_expiry_unix_ns,clock_envelope,elapsed_upper_ns,forward_rate_error_denominator,forward_rate_error_numerator,forward_rate_error_upper_ns,journal_digest,kind,lineage,monotonic_anchor_lower_ns,monotonic_sample_upper_ns,monotonic_validity_deadline_lower_ns,proof_digest,qualification,schema_version,trusted_upper_bound_unix_ns,wall_upper_at_anchor_unix_ns}`;
- `M.{admission,after_image_digest,before_image_digest,deadline_receipt_digest,incarnation_capability_digest,journal_digest,kind,lineage_before,logical_mutation_unix_ns,new_lineage_generation,post_target_generation,pre_target_generation,preserved_cohort_digest,proof_digest,schema_version,selected_cohort_digest}`; and
- `V.{deadline_receipt_digest,expected_postimage_digest,expected_preserved_cohort_digest,expected_selected_cohort_digest,expected_target_generation,journal_digest,kind,mutation_receipt_digest,observed_lineage_generation,observed_lineage_head_m_digest,observed_lineage_key_digest,observed_postimage_digest,observed_preserved_cohort_digest,observed_selected_cohort_digest,observed_target_generation,outcome,proof_digest,schema_version,target_database_identity,verification_attempt_id}`.

Action binding is exactly one of:

- `ApplyBinding.{action="apply",apply_payload,apply_payload_digest,budget_limits,grant,reconciliation_limits,retry_limits,rollback_preimage_binding}`;
- `SuccessorRollbackBinding.{action="rollback",budget_limits,grant,predecessor_apply_m_digest,predecessor_apply_v_digest,predecessor_variant="SUCCESSOR_APPLY",reconciliation_limits,retry_limits,rollback_preimage_binding}`; or
- `LegacyRollbackBinding.{action="rollback",budget_limits,final_manifest,grant,legacy_predecessor_selection_digest,manifest_approval,predecessor_variant="LEGACY_COMPLETE_APPLY",reconciliation_limits,retry_limits,rollback_preimage_binding}`.

The apply-payload reference and digest name the same
`TargetApplyPayload/v1`. Legacy `final_manifest` and `manifest_approval` name
the exact compatibility types and authenticated envelope, and the predecessor
selection digest hashes the complete nested `LegacyPredecessor` including LF
(`AE:5729–5920`).

The target-state grammar is a closed finite expansion:

- `PostgresqlValue` is exactly:
  `PostgresqlNullValue.{value_kind="NULL"}`;
  `PostgresqlBooleanValue.{value_kind="BOOLEAN",value:false or true}`;
  `PostgresqlSignedInt64Value.{value_kind="SIGNED_INT64",value:SignedInt64String}`;
  `PostgresqlTextValue.{value_kind="UTF8_TEXT",value:TargetProjectionTextValue}`;
  `PostgresqlByteaValue.{value_kind="BYTEA",value_base64url:Base64UrlBytes,byte_length:SafeInteger}`; or
  `PostgresqlUtcInstantValue.{value_kind="UTC_INSTANT_MICROSECONDS_2000",value:SignedInt64String}`.
  A key-column value admits the five non-`NULL` arms only.
- `TargetAllowedDelta` is exactly one branch over common members
  `{generation_increment=1,preserved_membership="UNCHANGED",preserved_values="UNCHANGED",selected_membership="UNCHANGED",target_database_identity="UNCHANGED",target_surface="UNCHANGED"}`:
  apply adds `{action="apply",selected_value_source="TARGET_APPLY_PAYLOAD"}`;
  rollback adds
  `{action="rollback",selected_value_source="TARGET_RESTORE_PAYLOAD"}`.
- `TargetRelationIdentity` has exactly
  `{kind,relation_name,relation_oid,relkind,schema_name,schema_version}`,
  with `relkind` `r` or `p`.
- `TargetColumnIdentity` has exactly
  `{attnum,collation_oid,column_name,kind,nullable,postgresql_type_oid,postgresql_typmod,postgresql_value_type,relation_identity_digest,schema_version}`.
- `TargetSurfaceRelationContract` has exactly
  `{columns[i],key_column_identity_digests[i],relation_identity,relation_identity_digest}`;
  `TargetSurfaceContract` has exactly
  `{kind,relations[i],schema_version,target_database_identity}`.
- `TargetKeyColumnValue` has exactly
  `{column_identity,column_identity_digest,value}`;
  `TargetRowIdentity` has exactly
  `{key_columns[i],kind,relation_identity_digest,schema_version}`.
- `TargetColumnProjection` has exactly
  `{column_identity,column_identity_digest,value}`;
  `TargetRowProjection` has exactly
  `{columns[i],row_identity,row_identity_digest}`; and
  `TargetRelationProjection` has exactly
  `{relation_identity,relation_identity_digest,rows[i]}`.
- `TargetCohortRowMembership` has exactly
  `{row_identity,row_identity_digest}`;
  `TargetCohortRelationMembership` has exactly
  `{relation_identity,relation_identity_digest,rows[i]}`; and each selected or
  preserved `TargetCohortMembership` has exactly
  `{cohort_kind,kind,relations[i],schema_version,target_database_identity,target_surface_digest}`.
- Each selected or preserved `TargetCohortProjection` has exactly
  `{cohort_kind,kind,membership,membership_digest,relations[i],schema_version,target_database_identity,target_surface_digest}`.
- `TargetMutationImage` has exactly
  `{kind,lineage_key_digest,preserved_cohort,preserved_cohort_digest,schema_version,selected_cohort,selected_cohort_digest,target_database_identity,target_generation,target_surface_digest}`.
- `TargetApplyPayload` and `TargetRestorePayload` each have exactly
  `{kind,lineage_key_digest,payload_role,schema_version,selected_cohort,selected_cohort_digest,target_database_identity,target_surface_digest}`,
  with roles `APPLY_SELECTED_CONTENT` and `ROLLBACK_SELECTED_CONTENT`,
  respectively.

This is a finite path expansion: every `relations[i]` emits its displayed
identity, digest, and every `rows[j]`; every row emits its complete row
identity/digest and every `columns[k]`; every key column emits its identity,
digest, and one nonnull typed value. No `i`, `j`, or `k` denotes an unnamed
subtree (`AE:1023–1190`).

The complete structural constraints are protected operands. Surface relations
are in standalone canonical-member order. The compatibility
`target_surface_digest` hashes the separator-free complete compatibility
relation-member bytes; the successor surface is a bijective projection of the
same relation, column, and key fields, while its ordinary body digest remains
distinct. Columns are nonempty, unique by `attnum`, name, and digest, and in
numeric `attnum` order. `postgresql_value_type` is exactly one of
`BOOLEAN`, `SIGNED_INT64`, `UTF8_TEXT`, `BYTEA`, or
`UTC_INSTANT_MICROSECONDS_2000`.
`key_column_identity_digests` is a nonempty exact numeric-order subset whose
columns are nonnullable. Relation, column, and row digests hash the displayed
standalone typed bodies including LF. A row identity supplies exactly one
nonnull key value per surface key column in that order. A row projection
supplies every surface column exactly once in that order. Value tag matches
column type, and `NULL` is allowed iff the column is nullable.

Signed integers and timestamp counts are bounded shortest decimal strings;
timestamps count microseconds from `2000-01-01T00:00:00Z` and admit neither
infinity nor session-time-zone interpretation. Text is the exact decoded
scalar sequence without normalization. `BYTEA` is canonical unpadded
base64url plus decoded length. Boolean is a JSON boolean, and `NULL` has no
`value` member. SQL rendering, locale, floating-point time, and JSON numbers
for signed values are not canonical values.

Each membership contains every surface relation once in surface order,
including empty relations; rows are ordered by complete canonical row-identity
bytes with duplicates rejected. Selected is nonempty, preserved may be empty,
they are disjoint, and their union is the complete locked surface.
A projection has exactly the same target, surface, relation, row, and column
order as its embedded membership and supplies one typed value for every
projected column. `selected_cohort_digest` and
`preserved_cohort_digest` hash their complete membership bodies, not mutable
content. `TargetMutationImage` is the only state image
(`AE:1772–1877`).

The publication copies are exact:

- `J` equals its locked plan for operation, target, surface, publication epoch,
  expected generation, cohort digests, action binding, approval, and
  authorization; `P.journal_digest=digest(J)`;
  `R.{journal_digest,proof_digest}={digest(J),digest(P)}`;
  `M.{journal_digest,proof_digest,deadline_receipt_digest}={digest(J),digest(P),digest(R)}`.
- `J.admission=P.admission=R.admission=M.admission` and
  `J.lineage=P.lineage=R.lineage=M.lineage_before`.
  `M.pre_target_generation=J.expected_target_generation`,
  `M.post_target_generation` is checked `pre_target_generation+1`,
  `M.new_lineage_generation` is checked
  `M.lineage_before.lineage_generation+1`, and M copies J's cohort digests.
- The protected mutator reconstructs one complete before
  `TargetMutationImage`. Apply substitutes exactly the apply payload's selected
  values; rollback substitutes exactly the restore payload's selected values.
  Row/column membership and preserved projection are unchanged. It recomputes
  complete selected/preserved projections and the after image at checked next
  generation. `M.before_image_digest` and `M.after_image_digest` hash those
  exact complete image bytes including LF, and
  `M.incarnation_capability_digest` equals the locked activation capability and
  session witness (`AE:6300–6354`).

For each `T` equal to `V` or `MismatchObservation`, the exact copies are:

| `T` field | Exact source |
| --- | --- |
| `journal_digest` | `digest(J)` |
| `proof_digest` | `digest(P)` |
| `deadline_receipt_digest` | `digest(R)` |
| `mutation_receipt_digest` | `digest(M)` |
| `target_database_identity` | `J.target_database_identity` |
| `expected_target_generation` | `M.post_target_generation` |
| `expected_selected_cohort_digest` | `M.selected_cohort_digest` |
| `expected_preserved_cohort_digest` | `M.preserved_cohort_digest` |
| `expected_postimage_digest` | `M.after_image_digest` |
| `observed_lineage_key_digest` | `M.lineage_before.lineage_key_digest` |
| `observed_lineage_generation` | `M.new_lineage_generation` |
| `observed_lineage_head_m_digest` | `digest(M)` |

The verifier independently reconstructs current target generation, complete
selected/preserved projections, postimage, and lineage. `V` has
`outcome="MATCH"` and exact equality for every observed/expected field.
`MismatchObservation` has `outcome="MISMATCH"` and at least one authenticated
target/lineage difference. Each contains
`{observed_postimage_digest,observed_preserved_cohort_digest,observed_selected_cohort_digest,observed_target_generation,verification_attempt_id}`
in addition to the table fields (`AE:6355–6370`).

`UnableObservation` has exactly
`{deadline_receipt_digest,failure_category,failure_evidence,journal_digest,kind,mutation_receipt_digest,outcome="UNABLE_TO_VERIFY",proof_digest,schema_version,target_database_identity,verification_attempt_id}`,
where `failure_category` is `EXPECTED_STATE_UNAVAILABLE`,
`TARGET_READ_UNAVAILABLE`, `TIMEOUT`, or
`VERIFIER_INTERNAL_ERROR`.
`TerminalVerificationFailure` has the same field shape with
`outcome="TERMINAL_FAILURE"` and category `INVARIANT_VIOLATION` or
`TARGET_IDENTITY_UNPROVEN`. In both bodies
`deadline_receipt_digest=digest(R)`,
`journal_digest=digest(J)`,
`mutation_receipt_digest=digest(M)`,
`proof_digest=digest(P)`, and
`target_database_identity=J.target_database_identity`;
`failure_evidence` is one typed exact cause.
All four verification outcomes share stable attempt key
`(digest(M),verification_attempt_id)`. `V`, mismatch, and terminal failure
are mutually exclusive terminal outcomes; unable is retryable and cannot
occupy terminal currentness (`AE:6002–6055`, `AE:6371–6416`).

Private-domain bodies are exactly:

- `PrivateEvidenceArtifactMember.{artifact,artifact_class,artifact_id}`;
- `PrivateArtifactProvenance.{artifact,authenticated_at_unix_ns,authentication,kind,mode,private_store_id,registrar_principal,sanitization_procedure,schema_version,source_acquisition}`;
- `RealArtifactBinding.{artifact,artifact_mode,kind,private_artifact_policy,provenance,public_projection_policy,schema_version}`;
- `ControlledPrivateEvidencePackage.{artifact_mode,artifacts,claim_ids,commitment_key_id,commitment_nonce_base64url,commitment_scheme,created_at_unix_ns,deciding_run_result,evidence_class,kind,limits,oracle_ids,package_id,public_record_id,real_artifact_binding,result,schema_version,subject_revision,tier}`;
- `BoundedPublicEvidenceProjection.{claim_ids,commitment_scheme,commitment_value,evidence_class,independent_review_id,kind,limits,oracle_ids,public_record_id,public_subject_identity,real_artifact_mode,result,schema_version,tier}`; and
- `IndependentEvidenceReviewReceipt.{claim_ids,checks,commitment_scheme,commitment_value,decision,evidence_class,issued_at_unix_ns,kind,limits,oracle_ids,public_projection,public_record_id,public_subject_identity,real_artifact_mode,result,review_id,reviewer_identity,reviewer_principal,schema_version,tier}`.

Artifacts expand per element, ordered by class/ID/reference with unique IDs.
Controlled-private provenance has both optional source references `NONE`;
sanitized-real has exact source acquisition and sanitization procedure.
Projection copies package public record, evidence class, claims, oracles, tier,
mode, result, limits, and scheme; commitment is recomputed over exact package
bytes. Receipt names the projection, has
`review_id=independent_review_id`, and copies every public field plus the
package-backed class/claims/oracles/tier/mode/result/limits. Its ten checks keep
schema order. Public bytes expose no package identity/digest, nonce, key,
artifact, provenance, or content (`AE:842–949`, `AE:7365–7476`).

Manifest embedded records are:

- `Approval.{kind,schema_version,approval_id,decision,domain,subject_kind,subject_digest,cutover_id,target_database_identity,target_surface_digest,operator_principal,issued_at_unix_ns,expires_at_unix_ns}`;
- `ArtifactExclusion.{kind,schema_version,exclusion_id,cutover_id,manifest_basis_digest,inventory_observation_id,locator,byte_length,source_sha256,target_scope_mode,target_surface_digest,target_overlap_evidence?,failure_category,supporting_evidence,issued_at_unix_ns,expires_at_unix_ns,authority="NONE"}`;
- `ExclusionBinding.{exclusion_body,exclusion_body_digest,approval_body,approval_digest}`;
- `ApprovalChannelReceipt.{boundary_id,receipt_format,receipt_bytes_base64url,receipt_sha256}`; and
- `ReferencedEvidence.{contract_kind,contract_version,body_bytes_base64url,body_digest}`.

`FinalManifest.final_dispositions[i]` is the complete canonical inventory
sequence. Each `FinalDisposition` has exactly
`{ordinal,inventory_observation_id,disposition,exclusion_id?}`.
`ordinal` starts at zero without gaps and names the observation at that
canonical inventory position; every observation appears exactly once.
`disposition` is one exact `HistoricalClassification`,
`EXCLUDED_OPAQUE`, or `PRESERVED_DISJOINT`.
`exclusion_id` is an `Id` exactly for `EXCLUDED_OPAQUE` and literal
`"NONE"` for every other branch. The ID resolves one bijective exclusion
binding. `PRESERVED_DISJOINT` is limited to after-dispatch
`INVALID_ARTIFACT` with authenticated disjoint overlap evidence and carries no
exclusion or predecessor authority (`CD:2559–2586`, `CD:2640–2652`).

`ManifestEnvelope` has exact root members
`{manifest_basis_body,manifest_basis_digest,final_manifest_body,manifest_body_digest,referenced_evidence[k],exclusion_approval_receipts[approval_digest],manifest_approval_record}`.
Each `referenced_evidence[k]` expands
`{contract_kind,contract_version,body_bytes_base64url,body_digest}` and is
keyed by that triple. Each exclusion receipt expands
`{approval_digest,approval_channel_receipt.{boundary_id,receipt_format,receipt_bytes_base64url,receipt_sha256}}`.
`manifest_approval_record` expands
`{approval_body,approval_digest,approval_channel_receipt.{boundary_id,receipt_format,receipt_bytes_base64url,receipt_sha256}}`.
Every final exclusion binding includes every exclusion and approval field plus
both digests; required referenced evidence is the exact recursive reachability
closure and extra members are invalid (`CD:2459–2710`).

Pending fence is exactly
`{cutover_id,manifest_body_digest,manifest_approval_digest,writer_fence_proposal_digest,consumed_invocation_digest,role_identity,continuity_session_id,reserved_publication_epoch,incarnation_capability_digest,deployment_attestation,service_steps,adoption_generation=0,current_manifest_binding_digest=NONE,state=FENCE_PENDING}`.
Origin/adoption each copy
`{target_database_identity,target_surface_digest,fence_generation,adoption_generation,prior_manifest_binding_digest,cutover_id,manifest_body_digest,manifest_approval_digest,persistent_fence_evidence_digest,continuity_session_id,reserved_publication_epoch,incarnation_capability_digest,deployment_attestation,authority=NONE}`;
origin has generation 0/prior `NONE`; adoption has checked-next positive
generation/prior digest. CAS operands are
`{target_database_identity,target_surface_digest,fence_generation,expected_adoption_generation,expected_current_manifest_binding_digest}`
(`CD:2947–3132`).

Compatibility time operands are
`{qualified_clock_envelope,monotonic_sample,forward_rate_error_numerator,forward_rate_error_denominator,forward_rate_error_upper_ns,U_prefence,U_consume,U_fence_start,U_fence_commit,U_adopt,U_cutover,deployment_policy.valid_until_unix_ns,deployment_attestation.valid_until_unix_ns,manifest_basis.freshness_deadline_unix_ns,manifest_basis.expires_at_unix_ns,manifest_approval.expires_at_unix_ns,exclusion_body[i].expires_at_unix_ns,exclusion_approval[i].expires_at_unix_ns}`.
Each applicable gate samples at its specified point and uses strict
`U<deadline`; equality, invalid state/envelope, clock loss, or overflow has no
effect. Cutover records exact envelope digest, sample, errors, and U after all
revalidation. Combined activation derives the graph from current
origin/adoption, compares every target/surface/generation/manifest/approval/
fence/session/epoch/capability/attestation member, then installs atomically.
Proven-noncommit abandonment sets `ABANDONED_FENCED`, clears reservation,
makes attestation noncurrent, preserves active epoch, and spends the epoch
(`CD:2888–2920`, `CD:3134–3317`, `PD:1158–1176`).

Every scalar not named here but contained in an admitted registered body
remains inside that body's exact canonical bytes; it is not a separately
invented SQL projection. Every reference, key, protected predicate, branch
decision, transition operand, and cross-body copy in the scoped families is
named above or in the reference roster.

## Branch, disposition, and later-coverage rules

The following source rules apply to every expanded member above:

- Optional paths have only their declared `NONE` or typed value branch.
  Ordered collections preserve source order; keyed sets use their stated key,
  canonical order, and duplicate rejection.
- Deployment attestation tier results are the complete current deployment
  partition; qualification receipt tier results are the complete design,
  implementation, and release partitions. Every prerequisite remains distinct.
- Deployment acquisition and real-artifact references occur only in their
  declared branches. Authority revocation uses the three exact presence
  branches. Conservative-overlap exclusion alone carries overlap evidence.
- Final-manifest predecessor is `NONE` or one exact
  `LEGACY_COMPLETE_APPLY`; origin and adoption are distinct; pending has no
  completed binding; adoption is checked-next and chains the prior digest.
- Recovery refusal, ambiguity, fence, advancement, and unproven outcomes remain
  distinct; optional reconciliation subjects obey their outcome branch.

The later crosswalk must map every expanded source member to exactly one direct
protected projection/key/equality, recursive ordinary-body dependency,
separately protected private/ciphertext dependency, or justified
`SOURCE_ONLY` member. Conversely, every proposed member must cite one
inventory identity or be marked as a new issue-106 implementation detail.
Direct-projection checks must name source path/branch, input origin,
destination/key, applied/replay/refusal/no-effect outcomes, owning atomic
operation, positive access, reciprocal denial, and forward/reverse equality.
Recursive checks resolve exact typed closure. Sequence/set checks reject
omission, addition, duplication, reordering, wrong key, or wrong branch.

The source comparison covered canonical grammar and foundational bodies
(`AE:74–1924`); reference positions, current slots, and subject keys
(`AE:2024–2369`); work and authority (`AE:2405–2772`); qualification,
campaign, evidence, historical, failed admission, and verdict computation
(`AE:2774–5720`); stages, recovery, target comparison, and time arithmetic
(`AE:5729–6728`); disposition (`AE:7039–7202`); private evidence,
the initial support-profile predicates, and the admission negative matrix
(`AE:7365–7811`); compatibility values, readers, closure, manifests,
fencing, and activation (`CD:160–710`, `CD:1027–1950`,
`CD:2260–3344`); publication currentness, stages, and ownership
(`PD:122–160`, `PD:330–944`, `PD:1259–1370`); restart state/recovery
(`RD:210–480`); and overview constraints (`OV:40–215`).

The independently fixed expectations include the journal-adoption and
ciphertext-byte totality identities; both closure stable identities, without
assigning a source-undefined construction rule to `closure_case_id`, and the
complete observation preimage; all eight prestate optionals, ten gate variants,
21 current slots, and 22 projection value domains; every typed target-image
descendant, conversion/binding equality, verification outcome, final
disposition, and protected evidence-operation branch; and every historical
selector generated by the total ten-field constructor and finite seed and
dependency sets above. Only genuinely kindless ciphertext, rollback-backup
bytes, and other source-declared opaque bytes remain
`OPAQUE_DEPENDENCY`; decrypted kindful preimages and every other accepted
kindful dependency have explicit selectors and parent-role edges.

The qualification/attestation expectation is independently enumerable as the
exact role positions named by `PRD001`–`PRD065`,
`QEQ001`–`QEQ042`, `DEQ001`–`DEQ063`, `PLV001`–`PLV078`,
and `PLR001`–`PLR098`; the 42 one-for-one `DFR` code tokens and their
source-supported category boundaries; the `NMR001`–`NMR035` negative-family
obligations; and the literal 28-member qualification and seven-member
deployment partitions. PRD does not advertise coverage of attestation,
campaign, plan, role-grant, writer-inventory, or other roster references it
does not name; those remain covered by the base roster and `QEQ`/`DEQ` rows.
Source member presence cannot stand in for a role domain, equality, predicate,
failure carrier/effect, concrete matrix-row identity, expected-refusal
representation, or partition member.

No concrete accepted deployment-matrix body is present. Its matrix reference;
exact `planned_runs[row_position]` positions and members; cell and run values;
deployment-stimulus parameter contract kind/version; tuple extraction paths
and values; OR-ID oracle position and expected projection; acyclic
expected-refusal carrier;
projection field and nested member path, including any sequence-member
position; runtime/template treatment; failure evidence; oracle IDs; complete
future failure sequence; and row multiplicity remain `MATRIX_BODY_GAP`. A
concrete row for matrix `M` is enumerated only by `ref(M)` plus its exact
`planned_runs[row_position]` member. Semantic tuple prebinding, source-fixed
sort order, exact refusal comparison, and the literal NMR family/code
constraints remain requirements; no tuple selector or failed-result extraction
constructor is claimed. `DeploymentAttestation.target_generation` remains
explicitly `SOURCE_UNASSIGNED`, so no database-derived value or current-slot
equality is reported as accepted source. The grant-lineage expectation remains
the exact 14-member immediate-reference selector set plus its enumerated
recursive parent-role edges.

Runtime value instances are intentionally absent; their typed paths,
cardinality, keys, and branches are present. Historical reader members are
enumerated before `SOURCE_ONLY` disposition. Private packages and ciphertext
remain separate.
