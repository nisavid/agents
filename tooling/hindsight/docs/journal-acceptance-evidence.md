# Hindsight Durable Journal Acceptance Evidence

Status: evidence contract selected in
[#76](https://github.com/nisavid/agents/issues/76). Ivan approved the evidence
boundaries, successor-byte contract, initial support profile, and historical
fixture policy on 2026-09-01. This record defines what can make the integrated
durable-journal design acceptable for implementation planning. It does not
implement, qualify, deploy, or authorize the design.

The architecture is fixed by the
[publication design](journal-publication-design.md), interrupted behavior by
the [restart design](journal-restart-design.md), and historical transition by
the [compatibility design](journal-compatibility-design.md). This record makes
their claims falsifiable without weakening them.

## Decision

Acceptance is tiered. A proof from one tier cannot stand in for a stronger
tier:

1. **Design evidence** closes the claim registry, exact successor-byte rules,
   clock arithmetic, state matrices, oracles, historical fixture policy, and
   qualification protocol. Together with the independent assessment in
   [#77](https://github.com/nisavid/agents/issues/77), it can support Ivan's
   design decision in [#78](https://github.com/nisavid/agents/issues/78).
2. **Implementation evidence** exercises the implemented schemas, protected
   interfaces, roles, adapter, frozen readers, and deterministic failure
   points against a real disposable PostgreSQL instance. It can show logical
   correctness, transaction boundaries, and restart behavior. It cannot show
   survival of host power loss.
3. **Release qualification** exercises one exact operating-system,
   PostgreSQL, clock, filesystem, storage, and controller profile through cold
   recovery and physical durability tests. Qualification applies only to the
   exact profile and release identities in its receipt.
4. **Deployment admission** proves that one installed target matches a
   qualified profile and release and is currently healthy. It neither expands
   the qualified support matrix nor authorizes a Hindsight operation.
5. **Live-operation authorization** remains a separate, exact plan and
   approval boundary. No design, test, qualification, or deployment receipt is
   live mutation authority.

The first release profile eligible for qualification is a locally bound macOS
controller and PostgreSQL deployment. Mutation stays fenced unless that
deployment's actual clock and storage stack pass the qualification and
deployment-admission contracts below. Linux and remote or managed PostgreSQL
are separate profiles; similar behavior is not evidence of qualification.

## Acceptance vocabulary

- **Claim** is one falsifiable statement from the accepted publication,
  restart, or compatibility contract. Each claim has a stable identifier in
  this record.
- **Evidence record** is an immutable result that names the claim, subject
  revision, environment, stimulus, tool, acquisition procedure, limits,
  oracle, observations, and result. A log without those bindings is diagnostic
  material, not acceptance evidence.
- **Evidence class** is the kind of experiment or analysis that can support a
  claim. Classes are not interchangeable merely because they report the same
  outcome.
- **Oracle** is the independent comparison that decides whether an observation
  satisfies a claim. A process exit code, present file, visible row, or
  matching selected row count is never a complete oracle.
- **Subject revision** is the immutable source, schema, migration, release, or
  deployment identity that the evidence actually exercised. Evidence for
  another revision is historical context only.
- **Support profile** is the complete host, PostgreSQL, clock, filesystem,
  storage, and controller configuration for which physical durability was
  qualified.
- **Unaffected evidence** is every target row outside the exact allowed
  mutation, every historical artifact and successor prefix, all completed and
  failed rows, all grants and limits, and every protected receipt not created
  by the exact transition under test.

## Successor canonical bytes

Every complete successor `J`, `P`, `R`, `M`, and `V` body uses
`hindsight-postgresql-publication-canonical-json/v1`. The same contract applies
to the immutable verification observations that can precede or exclude `V`.

The exact bytes are strict UTF-8 JSON:

- duplicate keys, non-finite numbers, unsafe JSON integers, lone surrogates,
  unknown fields, missing required fields, and `null` where the schema does not
  permit it are rejected;
- nonnegative safe integers use their shortest ASCII decimal form, with `0` as
  the only zero spelling;
- arrays retain their contract-defined order;
- objects order keys by unsigned lexicographic comparison of the keys' UTF-16BE
  code-unit bytes;
- arrays and objects use only `,` and `:` delimiters with no whitespace;
- string serialization emits `\"` and `\\` for quote and reverse solidus,
  the short JSON escapes for U+0008, U+0009, U+000A, U+000C, and U+000D,
  lowercase `\u00xx` for every other U+0000 through U+001F scalar, and the
  shortest literal UTF-8 sequence for every other Unicode scalar;
- `/`, U+2028, U+2029, and non-ASCII text are not optionally escaped or
  normalized; and
- the complete JSON value has exactly one trailing LF.

Every digest named as a successor body digest is SHA-256 over those complete
bytes, including the LF. A parsed JSON value, a digest over the value without
the LF, the repository's existing no-LF `canonical_bytes` helper, or bytes from
any historical contract are not interchangeable.

The exact v1 body identities are:

| Stage or observation | Exact `kind` | Exact `schema_version` |
| --- | --- | --- |
| Journal | `hindsight-postgresql-publication-journal` | `1` |
| Durable proof | `hindsight-postgresql-publication-proof` | `1` |
| Deadline receipt | `hindsight-postgresql-publication-deadline-receipt` | `1` |
| Mutation receipt | `hindsight-postgresql-publication-mutation-receipt` | `1` |
| Successful verification | `hindsight-postgresql-publication-verification-receipt` | `1` |
| Mismatch observation | `hindsight-postgresql-publication-verification-mismatch-observation` | `1` |
| Terminal verification failure | `hindsight-postgresql-publication-terminal-verification-failure` | `1` |
| Unable observation | `hindsight-postgresql-publication-verification-unable-observation` | `1` |
| Support profile | `hindsight-postgresql-support-profile` | `1` |
| Controller-host binding | `hindsight-postgresql-controller-host-binding` | `1` |
| PostgreSQL-host binding | `hindsight-postgresql-host-binding` | `1` |
| PostgreSQL-endpoint binding | `hindsight-postgresql-endpoint-binding` | `1` |
| Deployment-topology binding | `hindsight-postgresql-deployment-topology-binding` | `1` |
| Clock envelope | `hindsight-postgresql-clock-envelope` | `1` |
| Protected time observation | `hindsight-postgresql-protected-time-observation` | `1` |
| Qualification plan | `hindsight-postgresql-qualification-plan` | `1` |
| Qualification-run stimulus | `hindsight-postgresql-qualification-run-stimulus` | `1` |
| Qualification-plan acceptance | `hindsight-postgresql-qualification-plan-acceptance` | `1` |
| Qualification class result | `hindsight-postgresql-qualification-class-result` | `1` |
| Qualification receipt | `hindsight-postgresql-qualification-receipt` | `1` |
| Deployment attestation | `hindsight-postgresql-deployment-attestation` | `1` |
| Evidence campaign | `hindsight-postgresql-evidence-campaign` | `1` |
| Evidence campaign plan | `hindsight-postgresql-evidence-campaign-plan` | `1` |
| Evidence campaign-plan acceptance | `hindsight-postgresql-evidence-campaign-plan-acceptance` | `1` |
| Canonical claim registry | `hindsight-postgresql-canonical-claim-registry` | `1` |
| Canonical claim definition | `hindsight-postgresql-canonical-claim-definition` | `1` |
| Canonical claim predicate | `hindsight-postgresql-canonical-claim-predicate` | `1` |
| Canonical deployment matrix | `hindsight-postgresql-canonical-deployment-matrix` | `1` |
| Authority-gate conformance prestate | `hindsight-postgresql-authority-gate-conformance-prestate` | `1` |
| Authority-gate fixture state | `hindsight-postgresql-authority-gate-fixture-state` | `1` |
| Canonical oracle registry | `hindsight-postgresql-canonical-oracle-registry` | `1` |
| Oracle definition | `hindsight-postgresql-oracle-definition` | `1` |
| Oracle contract | `hindsight-postgresql-oracle-contract` | `1` |
| Oracle projection | `hindsight-postgresql-oracle-projection` | `1` |
| Evidence record | `hindsight-postgresql-evidence-record` | `1` |
| Evidence run failure | `hindsight-postgresql-evidence-run-failure` | `1` |
| Evidence run result | `hindsight-postgresql-evidence-run-result` | `1` |
| Evidence invalidity finding | `hindsight-postgresql-evidence-invalidity-finding` | `1` |
| Evidence tier result | `hindsight-postgresql-evidence-tier-result` | `1` |
| Historical corpus plan | `hindsight-postgresql-historical-corpus-plan` | `1` |
| Historical corpus-plan acceptance | `hindsight-postgresql-historical-corpus-plan-acceptance` | `1` |
| Historical corpus coverage projection | `hindsight-postgresql-historical-corpus-coverage-projection` | `1` |
| Historical reader execution binding | `hindsight-postgresql-historical-reader-execution-binding` | `1` |
| Failed deployment result | `hindsight-postgresql-failed-deployment-result` | `1` |
| Evidence-disposition authorization receipt | `hindsight-postgresql-evidence-disposition-authorization-receipt` | `1` |
| Evidence-record invalidation | `hindsight-postgresql-evidence-record-invalidation` | `1` |
| Evidence-campaign supersession | `hindsight-postgresql-evidence-campaign-supersession` | `1` |
| Deployment-admission policy | `hindsight-postgresql-deployment-admission-policy` | `1` |
| Immutable artifact | `hindsight-postgresql-immutable-artifact` | `1` |
| Evidence identity | `hindsight-postgresql-evidence-identity` | `1` |
| Profile component | `hindsight-postgresql-profile-component` | `1` |
| Boot-environment configuration | `hindsight-postgresql-boot-environment-configuration` | `1` |
| Clock configuration | `hindsight-postgresql-clock-configuration` | `1` |
| Filesystem configuration | `hindsight-postgresql-filesystem-configuration` | `1` |
| Hardware configuration | `hindsight-postgresql-hardware-configuration` | `1` |
| Operating-system configuration | `hindsight-postgresql-operating-system-configuration` | `1` |
| PostgreSQL component configuration | `hindsight-postgresql-component-configuration` | `1` |
| Storage configuration | `hindsight-postgresql-storage-configuration` | `1` |
| Virtualization configuration | `hindsight-postgresql-virtualization-configuration` | `1` |
| macOS local PostgreSQL live projection | `hindsight-postgresql-macos-local-live-projection` | `1` |
| Role-grant set | `hindsight-postgresql-role-grant-set` | `1` |
| Writer service identity | `hindsight-postgresql-writer-service-identity` | `1` |
| Writer inventory | `hindsight-postgresql-writer-inventory` | `1` |
| Deployment-evidence acquisition | `hindsight-postgresql-deployment-evidence-acquisition` | `1` |
| Contract body | `hindsight-postgresql-contract-body` | `1` |
| Procedure contract | `hindsight-postgresql-procedure-contract` | `1` |
| Tool contract | `hindsight-postgresql-tool-contract` | `1` |
| Evidence limits | `hindsight-postgresql-evidence-limits` | `1` |
| Closure policy limits | `hindsight-postgresql-closure-policy-limits` | `1` |
| Evidence stimulus | `hindsight-postgresql-evidence-stimulus` | `1` |
| Evidence case matrix | `hindsight-postgresql-evidence-case-matrix` | `1` |
| Randomized schedule | `hindsight-postgresql-randomized-schedule` | `1` |
| Qualification acceptance thresholds | `hindsight-postgresql-qualification-acceptance-thresholds` | `1` |
| Qualification abort policy | `hindsight-postgresql-qualification-abort-policy` | `1` |
| Evidence-retention policy | `hindsight-postgresql-evidence-retention-policy` | `1` |
| Private-artifact policy | `hindsight-postgresql-private-artifact-policy` | `1` |
| Public-projection policy | `hindsight-postgresql-public-projection-policy` | `1` |
| Private-artifact provenance | `hindsight-postgresql-private-artifact-provenance` | `1` |
| Real-artifact binding | `hindsight-postgresql-real-artifact-binding` | `1` |
| Controlled private evidence package | `hindsight-postgresql-controlled-private-evidence-package` | `1` |
| Bounded public evidence projection | `hindsight-postgresql-bounded-public-evidence-projection` | `1` |
| Independent evidence-review receipt | `hindsight-postgresql-independent-evidence-review-receipt` | `1` |
| PostgreSQL settings | `hindsight-postgresql-settings` | `1` |
| Historical fixture | `hindsight-postgresql-historical-fixture` | `1` |
| Historical generator | `hindsight-postgresql-historical-generator` | `1` |
| Successor-projection contract | `hindsight-postgresql-successor-projection-contract` | `1` |
| Failure evidence | `hindsight-postgresql-failure-evidence` | `1` |
| Operation plan | `hindsight-postgresql-operation-plan` | `1` |
| Operation grant | `hindsight-postgresql-operation-grant` | `1` |
| Operation grant revocation | `hindsight-postgresql-operation-grant-revocation` | `1` |
| Operation retry limits | `hindsight-postgresql-operation-retry-limits` | `1` |
| Operation reconciliation limits | `hindsight-postgresql-operation-reconciliation-limits` | `1` |
| Operation budget limits | `hindsight-postgresql-operation-budget-limits` | `1` |
| Operation-work request | `hindsight-postgresql-operation-work-request` | `1` |
| Operation-work preflight result | `hindsight-postgresql-operation-work-preflight-result` | `1` |
| Operation-work pre-reservation refusal | `hindsight-postgresql-operation-work-pre-reservation-refusal` | `1` |
| Operation-work reservation | `hindsight-postgresql-operation-work-reservation` | `1` |
| Operation-work start | `hindsight-postgresql-operation-work-start` | `1` |
| Operation-work committed result | `hindsight-postgresql-operation-work-committed-result` | `1` |
| Operation-work transaction-resolution outcome | `hindsight-postgresql-operation-work-transaction-resolution-outcome` | `1` |
| Operation-work ambiguity-query outcome | `hindsight-postgresql-operation-work-ambiguity-query-outcome` | `1` |
| Operation-work conclusive-noncommit result | `hindsight-postgresql-operation-work-conclusive-noncommit-result` | `1` |
| Transaction identity | `hindsight-postgresql-transaction-identity` | `1` |
| Reconciliation subject | `hindsight-postgresql-reconciliation-subject` | `1` |
| Operation approval | `hindsight-postgresql-operation-approval` | `1` |
| Operation-authorization receipt | `hindsight-postgresql-operation-authorization-receipt` | `1` |
| Operation-authority revocation | `hindsight-postgresql-operation-authority-revocation` | `1` |
| Pre-stage expiry observation | `hindsight-postgresql-pre-stage-expiry-observation` | `1` |
| Qualification clock epoch | `hindsight-postgresql-qualification-clock-epoch` | `1` |
| Protected rollback ciphertext | `hindsight-postgresql-protected-rollback-ciphertext` | `1` |
| Target relation identity | `hindsight-postgresql-target-relation-identity` | `1` |
| Target column identity | `hindsight-postgresql-target-column-identity` | `1` |
| Target row identity | `hindsight-postgresql-target-row-identity` | `1` |
| Target surface contract | `hindsight-postgresql-target-surface-contract` | `1` |
| Target cohort membership | `hindsight-postgresql-target-cohort-membership` | `1` |
| Target cohort projection | `hindsight-postgresql-target-cohort-projection` | `1` |
| Target mutation image | `hindsight-postgresql-target-mutation-image` | `1` |
| Target apply payload | `hindsight-postgresql-target-apply-payload` | `1` |
| Target restore payload | `hindsight-postgresql-target-restore-payload` | `1` |
| Restore-payload conversion | `hindsight-postgresql-restore-payload-conversion` | `1` |
| Rollback-preimage binding | `hindsight-postgresql-rollback-preimage-binding` | `1` |
| Recovery refusal observation | `hindsight-postgresql-recovery-refusal-observation` | `1` |
| Recovery ambiguity observation | `hindsight-postgresql-recovery-ambiguity-observation` | `1` |
| Recovery fence observation | `hindsight-postgresql-recovery-fence-observation` | `1` |
| Recovery advancement observation | `hindsight-postgresql-recovery-advancement-observation` | `1` |
| Recovery unproven observation | `hindsight-postgresql-recovery-unproven-observation` | `1` |

Every object below has exactly the listed keys. Every key is required. Unknown
keys and `null` are invalid. `Digest` is 64 lowercase hexadecimal SHA-256,
`SafeInteger` is an integer from 0 through 9,007,199,254,740,991, `Id` is a
lowercase canonical UUID, `ContractId` matches
`^[a-z][a-z0-9-]{0,127}$`, and `UInt128String` is the shortest ASCII decimal
string for a value from 0 through
340,282,366,920,938,463,463,374,607,431,768,211,455. `PositiveSafeInteger`
is a `SafeInteger` greater than zero. `Text` is a nonempty JSON string of
Unicode scalar values, contains no NUL, has at most 4,096 UTF-8 bytes, and is
not normalized.

`CompatibilityToken` matches the compatibility contract's exact
`[A-Za-z0-9][A-Za-z0-9._:-]{0,255}` grammar;
`CompatibilityContractId` matches its exact
`[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}` grammar; and `GitObjectId` is exactly 40
lowercase hexadecimal characters. These scalar aliases preserve the exact
`ReaderRegistryMember/v1` field domains when that compatibility body is
projected into successor-canonical coverage evidence.

`DatabaseName` is the one database-name domain shared with the compatibility
contract: a nonempty JSON string of Unicode scalar values, containing no NUL,
occupying at most 4,096 UTF-8 bytes, and receiving no Unicode normalization.
It is a strict subtype of `Text`; no empty, oversized, normalized, or
replacement-decoded name is admitted by either contract.

`PostgresqlPort` is a JSON integer from 1 through 65,535. It is neither a
string nor an operating-system default; the protected live projection must
read the exact effective server port.

`TargetGeneration` is exactly `SafeInteger`. It is one common successor type
in compatibility projections, deployment attestations, operation plans,
`J`, `M`, `V`, and verification observations. It is a JSON integer, not a
decimal string. A negative value, exponent, fraction, quoted number, leading
zero, plus sign, value above 9,007,199,254,740,991, or other spelling is
invalid rather than an alias. Increment uses unbounded arithmetic and rejects
the transition unless the result is also a `TargetGeneration`.

`TargetProjectionTextValue` is a JSON string of Unicode scalar values, may be
empty, contains no NUL, and receives no Unicode normalization. It is distinct
from nonempty `Text` so the protected target projection can preserve an empty
database text value.

`SignedInt64String` is the shortest ASCII decimal spelling of an integer from
-9,223,372,036,854,775,808 through 9,223,372,036,854,775,807. Zero is exactly
`"0"`; every nonzero value has an optional `-` followed by a nonzero digit and
then zero or more digits. A plus sign, leading zero, exponent, fraction,
negative zero, JSON number, or out-of-range value is invalid. `Base64UrlBytes`
is unpadded RFC 4648 base64url text. Its decoded octet length must equal the
adjacent `byte_length`; nonzero unused tail bits, padding, alternate alphabets,
or a second spelling of the same octets are invalid. The encoded string is
empty if and only if `byte_length=0`.

`PostgresqlValueType` is exactly `BOOLEAN`, `SIGNED_INT64`, `UTF8_TEXT`,
`BYTEA`, or `UTC_INSTANT_MICROSECONDS_2000`. These are the only nonnull column
value types admitted by the v1 target-surface contract. A selected target
surface containing any other PostgreSQL type, a non-UTF-8 database text
encoding, a timestamp without a fixed UTC interpretation, or a PostgreSQL
infinity value is unsupported and refuses plan construction. An implementation
must add a later accepted contract version before such a value can enter a
mutation image or restore payload.

`IdentityClass` is exactly one of `SUBJECT_REVISION`, `BOOT_CONFIGURATION`,
`BOOT_ENVIRONMENT`,
`CLOCK`, `FILESYSTEM`, `HARDWARE`, `HOST`, `OPERATING_SYSTEM`, `POSTGRESQL`,
`STORAGE`, `VIRTUALIZATION`, `ENDPOINT`, `TARGET_DATABASE`, `TARGET_SURFACE`,
`SYNCHRONIZATION_EPOCH`, `READER`, `FIXTURE`, `GENERATOR`, or
`EVIDENCE_REVIEWER`.

`ClaimId` matches `^JAC-[A-Z]{2,3}-[0-9]{2}$`. `FieldName` matches
`^[a-z][a-z0-9_]{0,127}$`. `OracleId` is exactly one of `OR-TRACE`, `OR-ID`,
`OR-PFX`, `OR-NEXT`, `OR-TIME`, `OR-TGT`, `OR-LIN`, `OR-FENCE`, `OR-EVID`,
`OR-EVAL`, `OR-LEG`, `OR-ACL`, `OR-CAP`, or `OR-PHY`.
`OracleEnumToken` matches `^[A-Z][A-Z0-9_]{0,127}$`; an optional enum uses
that exact string or the literal `"NONE"`.

An authority-bearing cross-contract reference is never a bare digest:

```text
EvidenceRef := {
  "body_digest": Digest,
  "contract_kind": ContractId,
  "contract_version": SafeInteger
}
```

The exact complete body bytes named by an `EvidenceRef` must be available to
the protected verifier. It decodes those bytes only under the exact registered
contract kind and version, hashes the complete contract-defined body bytes,
and recursively verifies every nested reference. An unknown contract or
version, missing body, digest mismatch, cycle, hidden dependency, or
same-identity different body is invalid. A caller projection or an equivalent
decoded value cannot substitute for the referenced bytes.

The following closed bodies supply every nested profile, identity, procedure,
tool, limit, stimulus, projection-support, and policy reference used by this
contract. They use the successor canonical-byte contract. An implementation
artifact is identified by immutable bytes and media type; this contract does
not select a package, module, executable, or SQL object name.

```text
ImmutableArtifact := {
  "artifact_digest": Digest,
  "byte_length": SafeInteger,
  "kind": "hindsight-postgresql-immutable-artifact",
  "media_type": "APPLICATION_OCTET_STREAM" | "APPLICATION_JSON" |
                "TEXT_PLAIN" | "SOURCE_TREE",
  "schema_version": 1
}

EvidenceIdentity := {
  "descriptor": EvidenceRef,
  "identity_class": IdentityClass,
  "identity_digest": Digest,
  "kind": "hindsight-postgresql-evidence-identity",
  "release_digest": Digest | "NONE",
  "schema_version": 1
}

TargetDatabaseIdentityBody := {
  "database_name": DatabaseName,
  "database_oid": SafeInteger,
  "postgres_system_identifier": UInt128String
}

ProfileComponent := {
  "component_class": "BOOT_ENVIRONMENT" | "CLOCK" | "FILESYSTEM" |
                     "HARDWARE" | "OPERATING_SYSTEM" | "POSTGRESQL" |
                     "STORAGE" | "VIRTUALIZATION",
  "configuration": EvidenceRef,
  "identity": EvidenceRef,
  "kind": "hindsight-postgresql-profile-component",
  "schema_version": 1
}

BootEnvironmentConfiguration := {
  "kind": "hindsight-postgresql-boot-environment-configuration",
  "required_boot_mode": "NORMAL" | "SAFE" | "RECOVERY",
  "schema_version": 1
}

ClockConfiguration := {
  "clock_source": "MACH_CONTINUOUS_TIME",
  "continuous_across_sleep": true,
  "forward_rate_error_denominator": UInt128String,
  "forward_rate_error_numerator": UInt128String,
  "kind": "hindsight-postgresql-clock-configuration",
  "nanoseconds_per_tick_denominator": UInt128String,
  "nanoseconds_per_tick_numerator": UInt128String,
  "regression_action": "FENCE",
  "schema_version": 1
}

FilesystemConfiguration := {
  "case_sensitivity": "CASE_SENSITIVE" | "CASE_INSENSITIVE",
  "filesystem_type": "APFS",
  "filesystem_version": Text,
  "kind": "hindsight-postgresql-filesystem-configuration",
  "mount_options": sequence<ContractId>,
  "mount_path": Text,
  "schema_version": 1,
  "volume_uuid": Text
}

HardwareConfiguration := {
  "architecture": "ARM64" | "X86_64",
  "hardware_model": Text,
  "kind": "hindsight-postgresql-hardware-configuration",
  "machine_identifier": Text,
  "schema_version": 1
}

OperatingSystemConfiguration := {
  "family": "MACOS",
  "kernel_release": Text,
  "kind": "hindsight-postgresql-operating-system-configuration",
  "product_build_version": Text,
  "product_version": Text,
  "schema_version": 1
}

PostgresqlUnixSocketDirectory := {
  "configured_path": Text,
  "directory_device_id": UInt128String,
  "directory_file_id": UInt128String,
  "resolved_path": Text
}

PostgresqlComponentConfiguration := {
  "data_directory": Text,
  "kind": "hindsight-postgresql-component-configuration",
  "postgresql_build_digest": Digest,
  "postgresql_port": PostgresqlPort,
  "postgresql_settings": EvidenceRef,
  "postgresql_version": Text,
  "schema_version": 1,
  "server_version_num": PositiveSafeInteger,
  "unix_socket_directories": sequence<PostgresqlUnixSocketDirectory>
}

StorageConfiguration := {
  "device_model": Text,
  "encryption": "ENABLED" | "DISABLED",
  "firmware_version": Text,
  "kind": "hindsight-postgresql-storage-configuration",
  "power_loss_protection": "PRESENT" | "ABSENT" | "UNKNOWN",
  "redundancy": "NONE" | "MIRROR" | "OTHER",
  "schema_version": 1,
  "storage_controller": Text,
  "volatile_write_cache": "DISABLED" | "ENABLED_PROTECTED" |
                          "ENABLED_UNPROTECTED" | "UNKNOWN",
  "volume_manager": Text
}

VirtualizationConfiguration := {
  "hypervisor_identity": Text | "NONE",
  "kind": "hindsight-postgresql-virtualization-configuration",
  "mode": "BARE_METAL" | "VIRTUAL_MACHINE",
  "schema_version": 1
}

MacosLocalPostgresqlLiveProjection := {
  "boot_environment_configuration": EvidenceRef,
  "boot_identity": EvidenceRef,
  "clock_configuration": EvidenceRef,
  "collected_at": EvidenceRef,
  "controller_host": EvidenceRef,
  "filesystem_configuration": EvidenceRef,
  "hardware_configuration": EvidenceRef,
  "kind": "hindsight-postgresql-macos-local-live-projection",
  "operating_system_configuration": EvidenceRef,
  "postgresql_configuration": EvidenceRef,
  "postgresql_endpoint": EvidenceRef,
  "postgresql_host": EvidenceRef,
  "schema_version": 1,
  "storage_configuration": EvidenceRef,
  "support_profile": EvidenceRef,
  "target_database_identity": EvidenceRef,
  "unix_socket_directories": sequence<PostgresqlUnixSocketDirectory>,
  "virtualization_configuration": EvidenceRef
}

RoleGrantee := {
  "grantee_kind": "PUBLIC" | "ROLE",
  "role_oid": SafeInteger | "NONE"
}

RoleAttributeGrantPath := {
  "attribute": "BYPASSRLS" | "CREATEDB" | "CREATEROLE" | "LOGIN" |
               "REPLICATION" | "SUPERUSER",
  "path_class": "ROLE_ATTRIBUTE",
  "role_oid": SafeInteger
}

RoleMembershipGrantPath := {
  "admin_option": true | false,
  "grantor_role_oid": SafeInteger,
  "inherit_option": true | false,
  "member_role_oid": SafeInteger,
  "path_class": "ROLE_MEMBERSHIP",
  "set_option": true | false,
  "target_role_oid": SafeInteger
}

OwnershipGrantPath := {
  "object_class": "DATABASE" | "EXTENSION" | "FOREIGN_DATA_WRAPPER" |
                  "FOREIGN_SERVER" | "LANGUAGE" | "LARGE_OBJECT" | "RELATION" |
                  "ROUTINE" | "SCHEMA" | "SEQUENCE" | "TYPE",
  "object_oid": SafeInteger,
  "owner_role_oid": SafeInteger,
  "path_class": "OWNERSHIP",
  "subobject_id": SafeInteger
}

AclGrantPath := {
  "grant_option": true | false,
  "grantee": RoleGrantee,
  "grantor_role_oid": SafeInteger,
  "object_class": "COLUMN" | "DATABASE" | "FOREIGN_DATA_WRAPPER" |
                  "FOREIGN_SERVER" | "LANGUAGE" | "LARGE_OBJECT" | "RELATION" |
                  "ROUTINE" | "SCHEMA" | "SEQUENCE" | "TYPE",
  "object_oid": SafeInteger,
  "path_class": "ACL",
  "privilege": "CONNECT" | "CREATE" | "DELETE" | "EXECUTE" |
               "INSERT" | "REFERENCES" | "SELECT" | "SET" |
               "TEMPORARY" | "TRIGGER" | "TRUNCATE" | "UPDATE" |
               "USAGE",
  "subobject_id": SafeInteger
}

DefaultPrivilegeGrantPath := {
  "definer_role_oid": SafeInteger,
  "grant_option": true | false,
  "grantee": RoleGrantee,
  "namespace_oid": SafeInteger | "ALL_SCHEMAS",
  "object_class": "RELATION" | "ROUTINE" | "SCHEMA" | "SEQUENCE" |
                  "TYPE",
  "path_class": "DEFAULT_PRIVILEGE",
  "privilege": "DELETE" | "EXECUTE" | "INSERT" | "REFERENCES" |
               "SELECT" | "TRIGGER" | "TRUNCATE" | "UPDATE" |
               "USAGE"
}

RoleGrantPath := RoleAttributeGrantPath | RoleMembershipGrantPath |
                 OwnershipGrantPath | AclGrantPath |
                 DefaultPrivilegeGrantPath

DatabaseRoleIdentity := {
  "role_name": Text,
  "role_oid": SafeInteger
}

RoleGrantSet := {
  "grant_paths": sequence<RoleGrantPath>,
  "kind": "hindsight-postgresql-role-grant-set",
  "roles": sequence<DatabaseRoleIdentity>,
  "schema_version": 1,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

WriterServiceIdentity := {
  "adapter_kind": ContractId,
  "kind": "hindsight-postgresql-writer-service-identity",
  "schema_version": 1,
  "service_id": Id,
  "service_locator": Text,
  "target_surface_digest": Digest
}

RoleGrantPathRef := {
  "member_digest": Digest,
  "member_ordinal": SafeInteger
}

DirectRelationWriterPath := {
  "actor_role_oid": SafeInteger,
  "grant_path_chain": sequence<RoleGrantPathRef>,
  "path_class": "DIRECT_RELATION",
  "privilege": "DELETE" | "INSERT" | "TRIGGER" | "TRUNCATE" |
               "UPDATE",
  "target_relation_oid": SafeInteger
}

AdministrativeWriterPath := {
  "actor_role_oid": SafeInteger,
  "grant_path_chain": sequence<RoleGrantPathRef>,
  "path_class": "ADMINISTRATIVE",
  "target_relation_oid": SafeInteger,
  "writer_capability": "OWNERSHIP" | "SCHEMA_CREATE" | "SUPERUSER"
}

CatalogWriterCallNode := {
  "node_kind": "EXTENSION" | "ROUTINE" | "RULE" | "TRIGGER",
  "object_oid": SafeInteger
}

DynamicSqlWriterCallNode := {
  "node_kind": "DYNAMIC_SQL",
  "resolved_target_relation_oid": SafeInteger,
  "source_routine_oid": SafeInteger,
  "statement_digest": Digest
}

RelationWriterCallNode := {
  "node_kind": "RELATION",
  "object_oid": SafeInteger
}

WriterCallNode := CatalogWriterCallNode | DynamicSqlWriterCallNode |
                  RelationWriterCallNode

RoutineWriterCallEdge := {
  "edge_kind": "DIRECT_RELATION_MUTATION" | "DYNAMIC_SQL_RESOLUTION" |
               "EXTENSION_DISPATCH" | "ROUTINE_CALL" | "RULE_REWRITE" |
               "TRIGGER_DISPATCH",
  "from": WriterCallNode,
  "mutation_target_relation_oid": SafeInteger | "NONE",
  "to": WriterCallNode
}

RoutineWriterPath := {
  "actor": RoleGrantee,
  "call_chain": sequence<RoutineWriterCallEdge>,
  "effective_role_oid": SafeInteger,
  "entry_routine_oid": SafeInteger,
  "execute_grant_path_chain": sequence<RoleGrantPathRef>,
  "path_class": "ROUTINE",
  "security_mode": "DEFINER" | "INVOKER",
  "target_relation_oid": SafeInteger
}

ServiceWriterPath := {
  "database_login_role_oid": SafeInteger,
  "entry_routine_oid": SafeInteger | "DIRECT_SQL",
  "path_class": "SERVICE",
  "service_identity": EvidenceRef,
  "underlying_writer_path_digest": Digest
}

WriterPath := DirectRelationWriterPath | AdministrativeWriterPath |
              RoutineWriterPath | ServiceWriterPath

WriterInventory := {
  "kind": "hindsight-postgresql-writer-inventory",
  "role_grant_set": EvidenceRef,
  "schema_version": 1,
  "service_identities": sequence<EvidenceRef>,
  "target_database_identity": EvidenceRef,
  "target_relation_oids": sequence<SafeInteger>,
  "target_surface": WriterTargetSurfacePreimage,
  "target_surface_digest": Digest,
  "writer_paths": sequence<WriterPath>
}

WriterTargetSurfacePreimage := {
  "relations": sequence<TargetSurfaceRelationContract>
}

DeploymentEvidenceAcquisition := {
  "acquired_at": EvidenceRef,
  "acquisition_id": Id,
  "acquisition_procedure": EvidenceRef,
  "campaign": EvidenceRef,
  "kind": "hindsight-postgresql-deployment-evidence-acquisition",
  "observed_projection": EvidenceRef,
  "oracle_id": OracleId,
  "run_id": ContractId,
  "schema_version": 1
}

ContractClause := {
  "clause_id": ContractId,
  "requirement": Text
}

ContractBody := {
  "clauses": sequence<ContractClause>,
  "contract_role": "INPUT" | "INVOCATION" | "OUTPUT" |
                   "PROCEDURE_STEPS",
  "kind": "hindsight-postgresql-contract-body",
  "owner_class": "GENERATOR" | "PROCEDURE" | "TOOL",
  "owner_id": ContractId,
  "schema_version": 1
}

ProcedureContract := {
  "implementation": EvidenceRef,
  "input_contract": EvidenceRef,
  "kind": "hindsight-postgresql-procedure-contract",
  "output_contract": EvidenceRef,
  "procedure_class": "ACQUISITION" | "COLD_RECOVERY" | "DECRYPTION" |
                     "ENVIRONMENT_RESET" | "FAILURE_INJECTION" |
                     "SANITIZATION",
  "procedure_id": ContractId,
  "schema_version": 1,
  "step_contract": EvidenceRef
}

ToolContract := {
  "implementation": EvidenceRef,
  "input_contract": EvidenceRef,
  "invocation_contract": EvidenceRef,
  "kind": "hindsight-postgresql-tool-contract",
  "output_contract": EvidenceRef,
  "schema_version": 1,
  "tool_class": "RUNNER" | "ORACLE" | "READER" | "GENERATOR" |
                "FAILURE_INJECTOR",
  "tool_id": ContractId
}

EvidenceLimits := {
  "case_budget": SafeInteger,
  "connection_lifetime_ns": UInt128String,
  "execution_limit_ns": UInt128String,
  "kind": "hindsight-postgresql-evidence-limits",
  "lock_timeout_ns": UInt128String,
  "max_bytes": SafeInteger,
  "max_depth": SafeInteger,
  "max_edges": SafeInteger,
  "max_nodes": SafeInteger,
  "maximum_attempts": SafeInteger,
  "memory_limit_bytes": UInt128String,
  "schema_version": 1,
  "shrink_budget": SafeInteger,
  "statement_timeout_ns": UInt128String,
  "transaction_timeout_ns": UInt128String
}

ClosurePolicyLimits := {
  "connection_lifetime_ms": PositiveSafeInteger,
  "idle_in_transaction_timeout_ms": PositiveSafeInteger,
  "kind": "hindsight-postgresql-closure-policy-limits",
  "lock_timeout_ms": PositiveSafeInteger,
  "maximum_case_lifetime_ms": PositiveSafeInteger,
  "maximum_observation_attempts": PositiveSafeInteger,
  "maximum_reservation_duration_ms": PositiveSafeInteger,
  "maximum_resolution_duration_ms": PositiveSafeInteger,
  "observation_call_timeout_ms": PositiveSafeInteger,
  "observer_lease_duration_ms": PositiveSafeInteger,
  "schema_version": 1,
  "statement_timeout_ms": PositiveSafeInteger,
  "transaction_timeout_ms": PositiveSafeInteger
}

EvidenceStimulus := {
  "historical_fixture": EvidenceRef | "NONE",
  "input_artifact": EvidenceRef | "NONE",
  "kind": "hindsight-postgresql-evidence-stimulus",
  "parameter_bytes": EvidenceRef,
  "schema_version": 1,
  "stimulus_class": "DESIGN_TRACE" | "REFERENCE_MODEL" |
                    "CANONICAL_VECTOR" | "POSTGRESQL_CASE" |
                    "FAULT_CASE" | "HISTORICAL_FIXTURE" |
                    "HISTORICAL_GENERATED_CASE" |
                    "HISTORICAL_REAL_ARTIFACT" | "ACL_CASE" |
                    "CLOCK_CASE" | "PHYSICAL_CASE" |
                    "CAPABILITY_CASE" | "DEPLOYMENT_CASE",
  "stimulus_id": ContractId
}

QualificationRunStimulus := {
  "allocation_ordinal": PositiveSafeInteger | "NONE",
  "case_id": ContractId,
  "case_ordinal": PositiveSafeInteger,
  "case_stimulus": EvidenceRef,
  "cell_id": ContractId,
  "kind": "hindsight-postgresql-qualification-run-stimulus",
  "run_ordinal": PositiveSafeInteger,
  "schema_version": 1,
  "seed_ordinal": PositiveSafeInteger | "NONE",
  "seed_uint128": UInt128String | "NONE"
}

EvidenceCase := {
  "case_id": ContractId,
  "expected_projections": sequence<EvidenceRef>,
  "stimulus": EvidenceRef
}

EvidenceCaseMatrix := {
  "cases": sequence<EvidenceCase>,
  "kind": "hindsight-postgresql-evidence-case-matrix",
  "matrix_id": ContractId,
  "schema_version": 1
}

RandomizedSchedule := {
  "allocation": PositiveSafeInteger,
  "kind": "hindsight-postgresql-randomized-schedule",
  "schedule_id": ContractId,
  "schema_version": 1,
  "seeds": sequence<UInt128String>
}

ProtectedTimeObservation := {
  "clock_envelope": EvidenceRef | "NONE",
  "kind": "hindsight-postgresql-protected-time-observation",
  "mode": "PROTECTED_REGISTRATION" | "QUALIFIED_CLOCK",
  "monotonic_sample_lower_ns": UInt128String | "NONE",
  "monotonic_sample_upper_ns": UInt128String | "NONE",
  "phase": "CAMPAIGN_START" | "EVIDENCE_START" |
           "EVIDENCE_COMPLETE" | "QUALIFICATION_RECEIPT_ISSUE" |
           "DEPLOYMENT_ATTESTATION_ISSUE" |
           "DEPLOYMENT_EVIDENCE_ACQUIRE" |
           "EVIDENCE_DISPOSITION_APPLY" |
           "OPERATION_WORK_RESERVE" |
           "SUPPORT_PROFILE_PROJECTION",
  "schema_version": 1,
  "subject_key_digest": Digest,
  "trusted_upper_bound_unix_ns": UInt128String
}

QualificationAcceptanceThresholds := {
  "class_result_rule": "ALL_PLANNED_RUNS_PASS",
  "kind": "hindsight-postgresql-qualification-acceptance-thresholds",
  "qualification_receipt_validity_ns": UInt128String,
  "receipt_rule": "ALL_CLASSES_AND_REQUIRED_TIERS_PASS",
  "schema_version": 1
}

QualificationAbortPolicy := {
  "kind": "hindsight-postgresql-qualification-abort-policy",
  "on_explained_failure": "CONTINUE_AND_FAIL_CLASS",
  "on_unexplained_outcome": "ABORT_AND_FAIL_CAMPAIGN",
  "schema_version": 1
}

EvidenceRetentionPolicy := {
  "kind": "hindsight-postgresql-evidence-retention-policy",
  "retain_immutable_bodies": true,
  "retain_private_artifacts": "UNDER_PRIVATE_POLICY",
  "retain_public_artifacts": "UNCONDITIONALLY",
  "schema_version": 1
}

PrivateArtifactPolicy := {
  "artifact_modes": sequence<"CONTROLLED_PRIVATE" | "SANITIZED_REAL">,
  "deciding_record_location": "APPROVED_PRIVATE_EVIDENCE_STORE",
  "kind": "hindsight-postgresql-private-artifact-policy",
  "public_replacement_rule": "NEW_SYNTHETIC_IDENTITY",
  "schema_version": 1
}

PublicProjectionPolicy := {
  "allowed_fields": sequence<"PUBLIC_RECORD_ID" | "EVIDENCE_CLASS" |
                    "CLAIM_IDS" | "ORACLE_IDS" | "TIER" |
                    "PUBLIC_SUBJECT_IDENTITY" | "RESULT" | "LIMITS" |
                    "COMMITMENT_SCHEME" | "COMMITMENT_VALUE" |
                    "INDEPENDENT_REVIEW_ID" | "REAL_ARTIFACT_MODE">,
  "commitment_schemes": sequence<"RANDOMIZED_SHA256" | "KEYED_HMAC_SHA256">,
  "forbidden_fields": sequence<"ARTIFACT_BYTES" | "RETAINED_CONTENT" |
                      "RAW_INPUT_DIGEST" | "EXPECTED_OUTPUT_DIGEST" |
                      "OBSERVED_OUTPUT_DIGEST" |
                      "CONTENT_DERIVED_IDENTIFIER" | "PRIVATE_PATH" |
                      "PRIVATE_PROVENANCE" | "REAL_ARTIFACT_BINDING">,
  "kind": "hindsight-postgresql-public-projection-policy",
  "schema_version": 1
}

PrivateEvidenceArtifactMember := {
  "artifact": EvidenceRef,
  "artifact_class": "INPUT" | "EXPECTED_OUTPUT" | "OBSERVED_OUTPUT" |
                    "DIAGNOSTIC",
  "artifact_id": ContractId
}

PrivateArtifactProvenance := {
  "artifact": EvidenceRef,
  "authenticated_at_unix_ns": UInt128String,
  "authentication": "PROTECTED_PRIVATE_REGISTRAR_SESSION",
  "kind": "hindsight-postgresql-private-artifact-provenance",
  "mode": "CONTROLLED_PRIVATE" | "SANITIZED_REAL",
  "private_store_id": ContractId,
  "registrar_principal": Text,
  "sanitization_procedure": EvidenceRef | "NONE",
  "schema_version": 1,
  "source_acquisition": EvidenceRef | "NONE"
}

RealArtifactBinding := {
  "artifact": EvidenceRef,
  "artifact_mode": "CONTROLLED_PRIVATE" | "SANITIZED_REAL",
  "kind": "hindsight-postgresql-real-artifact-binding",
  "private_artifact_policy": EvidenceRef,
  "provenance": EvidenceRef,
  "public_projection_policy": EvidenceRef,
  "schema_version": 1
}

ControlledPrivateEvidencePackage := {
  "artifact_mode": "CONTROLLED_PRIVATE" | "SANITIZED_REAL",
  "artifacts": sequence<PrivateEvidenceArtifactMember>,
  "claim_ids": sequence<ClaimId>,
  "commitment_key_id": ContractId | "NONE",
  "commitment_nonce_base64url": Text,
  "commitment_scheme": "RANDOMIZED_SHA256" | "KEYED_HMAC_SHA256",
  "created_at_unix_ns": UInt128String,
  "deciding_run_result": EvidenceRef,
  "evidence_class": "EV-LEG",
  "kind": "hindsight-postgresql-controlled-private-evidence-package",
  "limits": EvidenceRef,
  "oracle_ids": sequence<OracleId>,
  "package_id": Id,
  "public_record_id": Id,
  "real_artifact_binding": EvidenceRef,
  "result": "PASS" | "FAIL",
  "schema_version": 1,
  "subject_revision": EvidenceRef,
  "tier": "IMPLEMENTATION"
}

BoundedPublicEvidenceProjection := {
  "claim_ids": sequence<ClaimId>,
  "commitment_scheme": "RANDOMIZED_SHA256" | "KEYED_HMAC_SHA256",
  "commitment_value": Digest,
  "evidence_class": "EV-LEG",
  "independent_review_id": Id,
  "kind": "hindsight-postgresql-bounded-public-evidence-projection",
  "limits": EvidenceRef,
  "oracle_ids": sequence<OracleId>,
  "public_record_id": Id,
  "public_subject_identity": EvidenceRef,
  "real_artifact_mode": "CONTROLLED_PRIVATE" | "SANITIZED_REAL",
  "result": "PASS" | "FAIL",
  "schema_version": 1,
  "tier": "IMPLEMENTATION"
}

IndependentEvidenceReviewReceipt := {
  "claim_ids": sequence<ClaimId>,
  "checks": sequence<"PACKAGE_REFERENCE" | "COMMITMENT" |
                     "REAL_ARTIFACT_BINDING" |
                     "REAL_ARTIFACT_PROVENANCE" |
                     "REAL_ARTIFACT_MODE_EQUALITY" |
                     "PROJECTION_ALLOWLIST" | "ORACLE_REPRODUCTION" |
                     "RESULT_EQUALITY" | "LIMITS_EQUALITY" |
                     "NO_PRIVATE_FIELD_DISCLOSURE">,
  "commitment_scheme": "RANDOMIZED_SHA256" | "KEYED_HMAC_SHA256",
  "commitment_value": Digest,
  "decision": "ACCEPT_PUBLICATION",
  "evidence_class": "EV-LEG",
  "issued_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-independent-evidence-review-receipt",
  "limits": EvidenceRef,
  "oracle_ids": sequence<OracleId>,
  "public_projection": EvidenceRef,
  "public_record_id": Id,
  "public_subject_identity": EvidenceRef,
  "real_artifact_mode": "CONTROLLED_PRIVATE" | "SANITIZED_REAL",
  "result": "PASS" | "FAIL",
  "review_id": Id,
  "reviewer_identity": EvidenceRef,
  "reviewer_principal": Text,
  "schema_version": 1,
  "tier": "IMPLEMENTATION"
}

PostgresqlSettings := {
  "fsync": "ON",
  "full_page_writes": "ON",
  "kind": "hindsight-postgresql-settings",
  "postgresql_build_digest": Digest,
  "postgresql_version": Text,
  "schema_version": 1,
  "synchronous_commit": "ON",
  "wal_sync_method": ContractId
}

HistoricalFixture := {
  "artifact_kind": ContractId,
  "artifact_schema_version": SafeInteger,
  "artifact_variant": ContractId | "NONE",
  "fixture_bytes": EvidenceRef,
  "fixture_id": ContractId,
  "kind": "hindsight-postgresql-historical-fixture",
  "schema_version": 1
}

HistoricalGenerator := {
  "artifact_kinds": sequence<ContractId>,
  "generator_id": ContractId,
  "implementation": EvidenceRef,
  "kind": "hindsight-postgresql-historical-generator",
  "output_contract": EvidenceRef,
  "schema_version": 1
}

SuccessorProjectionContract := {
  "field_requirements": sequence<OracleFieldRequirement>,
  "kind": "hindsight-postgresql-successor-projection-contract",
  "projection_id": ContractId,
  "schema_version": 1
}

FailureEvidence := {
  "evidence_artifact": EvidenceRef,
  "failure_code": ContractId,
  "kind": "hindsight-postgresql-failure-evidence",
  "schema_version": 1,
  "source_identity": EvidenceRef
}

DeploymentAdmissionPolicy := {
  "allowed_release_digests": sequence<Digest>,
  "allowed_support_profiles": sequence<EvidenceRef>,
  "attestation_validity_ns": UInt128String,
  "effective_from_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-deployment-admission-policy",
  "maximum_deployment_evidence_age_ns": UInt128String,
  "policy_id": Id,
  "required_deployment_claim_ids": sequence<ClaimId>,
  "schema_version": 1,
  "target_database_identities": sequence<EvidenceRef>,
  "target_surface_digests": sequence<Digest>,
  "valid_until_unix_ns": UInt128String
}

ProtectedRollbackCiphertext := {
  "artifact": EvidenceRef,
  "authority": "NONE",
  "byte_length": SafeInteger,
  "ciphertext_digest": Digest,
  "kind": "hindsight-postgresql-protected-rollback-ciphertext",
  "retention": "THROUGH_VERIFIED_ROLLBACK_OR_AUTHORIZED_RETIREMENT",
  "schema_version": 1,
  "storage_class": "PROTECTED_POSTGRESQL_BYTES"
}

PostgresqlNullValue := {
  "value_kind": "NULL"
}

PostgresqlBooleanValue := {
  "value": false | true,
  "value_kind": "BOOLEAN"
}

PostgresqlSignedInt64Value := {
  "value": SignedInt64String,
  "value_kind": "SIGNED_INT64"
}

PostgresqlTextValue := {
  "value": TargetProjectionTextValue,
  "value_kind": "UTF8_TEXT"
}

PostgresqlByteaValue := {
  "byte_length": SafeInteger,
  "value_base64url": Base64UrlBytes,
  "value_kind": "BYTEA"
}

PostgresqlUtcInstantValue := {
  "value": SignedInt64String,
  "value_kind": "UTC_INSTANT_MICROSECONDS_2000"
}

PostgresqlValue := PostgresqlNullValue |
                   PostgresqlBooleanValue |
                   PostgresqlSignedInt64Value |
                   PostgresqlTextValue |
                   PostgresqlByteaValue |
                   PostgresqlUtcInstantValue

TargetRelationIdentity := {
  "kind": "hindsight-postgresql-target-relation-identity",
  "relation_name": Text,
  "relation_oid": SafeInteger,
  "relkind": "r" | "p",
  "schema_name": Text,
  "schema_version": 1
}

TargetColumnIdentity := {
  "attnum": PositiveSafeInteger,
  "collation_oid": SafeInteger,
  "column_name": Text,
  "kind": "hindsight-postgresql-target-column-identity",
  "nullable": false | true,
  "postgresql_type_oid": SafeInteger,
  "postgresql_typmod": SignedInt64String,
  "postgresql_value_type": PostgresqlValueType,
  "relation_identity_digest": Digest,
  "schema_version": 1
}

TargetSurfaceRelationContract := {
  "columns": sequence<TargetColumnIdentity>,
  "key_column_identity_digests": sequence<Digest>,
  "relation_identity": TargetRelationIdentity,
  "relation_identity_digest": Digest
}

TargetSurfaceContract := {
  "kind": "hindsight-postgresql-target-surface-contract",
  "relations": sequence<TargetSurfaceRelationContract>,
  "schema_version": 1,
  "target_database_identity": EvidenceRef
}

TargetKeyColumnValue := {
  "column_identity": TargetColumnIdentity,
  "column_identity_digest": Digest,
  "value": PostgresqlBooleanValue |
           PostgresqlSignedInt64Value |
           PostgresqlTextValue |
           PostgresqlByteaValue |
           PostgresqlUtcInstantValue
}

TargetRowIdentity := {
  "key_columns": sequence<TargetKeyColumnValue>,
  "kind": "hindsight-postgresql-target-row-identity",
  "relation_identity_digest": Digest,
  "schema_version": 1
}

TargetColumnProjection := {
  "column_identity": TargetColumnIdentity,
  "column_identity_digest": Digest,
  "value": PostgresqlValue
}

TargetRowProjection := {
  "columns": sequence<TargetColumnProjection>,
  "row_identity": TargetRowIdentity,
  "row_identity_digest": Digest
}

TargetRelationProjection := {
  "relation_identity": TargetRelationIdentity,
  "relation_identity_digest": Digest,
  "rows": sequence<TargetRowProjection>
}

TargetCohortRowMembership := {
  "row_identity": TargetRowIdentity,
  "row_identity_digest": Digest
}

TargetCohortRelationMembership := {
  "relation_identity": TargetRelationIdentity,
  "relation_identity_digest": Digest,
  "rows": sequence<TargetCohortRowMembership>
}

TargetCohortMembershipCommon := {
  "kind": "hindsight-postgresql-target-cohort-membership",
  "relations": sequence<TargetCohortRelationMembership>,
  "schema_version": 1,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

SelectedTargetCohortMembership := TargetCohortMembershipCommon &
                                  {"cohort_kind": "SELECTED"}
PreservedTargetCohortMembership := TargetCohortMembershipCommon &
                                   {"cohort_kind": "PRESERVED"}
TargetCohortMembership := SelectedTargetCohortMembership |
                          PreservedTargetCohortMembership

TargetCohortProjectionCommon := {
  "kind": "hindsight-postgresql-target-cohort-projection",
  "membership_digest": Digest,
  "relations": sequence<TargetRelationProjection>,
  "schema_version": 1,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

SelectedTargetCohortProjection := TargetCohortProjectionCommon & {
  "cohort_kind": "SELECTED",
  "membership": SelectedTargetCohortMembership
}

PreservedTargetCohortProjection := TargetCohortProjectionCommon & {
  "cohort_kind": "PRESERVED",
  "membership": PreservedTargetCohortMembership
}

TargetCohortProjection := SelectedTargetCohortProjection |
                          PreservedTargetCohortProjection

TargetMutationImage := {
  "kind": "hindsight-postgresql-target-mutation-image",
  "lineage_key_digest": Digest,
  "preserved_cohort": PreservedTargetCohortProjection,
  "preserved_cohort_digest": Digest,
  "schema_version": 1,
  "selected_cohort": SelectedTargetCohortProjection,
  "selected_cohort_digest": Digest,
  "target_database_identity": EvidenceRef,
  "target_generation": TargetGeneration,
  "target_surface_digest": Digest
}

TargetAllowedDeltaCommon := {
  "generation_increment": 1,
  "preserved_membership": "UNCHANGED",
  "preserved_values": "UNCHANGED",
  "selected_membership": "UNCHANGED",
  "target_database_identity": "UNCHANGED",
  "target_surface": "UNCHANGED"
}

TargetAllowedApplyDelta := TargetAllowedDeltaCommon & {
  "action": "apply",
  "selected_value_source": "TARGET_APPLY_PAYLOAD"
}

TargetAllowedRollbackDelta := TargetAllowedDeltaCommon & {
  "action": "rollback",
  "selected_value_source": "TARGET_RESTORE_PAYLOAD"
}

TargetAllowedDelta := TargetAllowedApplyDelta | TargetAllowedRollbackDelta

TargetApplyPayload := {
  "kind": "hindsight-postgresql-target-apply-payload",
  "lineage_key_digest": Digest,
  "payload_role": "APPLY_SELECTED_CONTENT",
  "schema_version": 1,
  "selected_cohort": SelectedTargetCohortProjection,
  "selected_cohort_digest": Digest,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

TargetApplyPayloadEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-target-apply-payload",
  "contract_version": 1
}

TargetRestorePayload := {
  "kind": "hindsight-postgresql-target-restore-payload",
  "lineage_key_digest": Digest,
  "payload_role": "ROLLBACK_SELECTED_CONTENT",
  "schema_version": 1,
  "selected_cohort": SelectedTargetCohortProjection,
  "selected_cohort_digest": Digest,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

TargetRestorePayloadEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-target-restore-payload",
  "contract_version": 1
}

LegacyRestoreContentEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-compatibility-legacy-restore-content",
  "contract_version": 1
}

RestorePayloadConversionEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-restore-payload-conversion",
  "contract_version": 1
}

ProtectedRollbackCiphertextEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-protected-rollback-ciphertext",
  "contract_version": 1
}

SuccessorRestorePayloadSource := {
  "source_format": "SUCCESSOR_TARGET_RESTORE_PAYLOAD_V1",
  "source_reader_registry_member_digest": "NONE",
  "source_typed_body": TargetRestorePayloadEvidenceRef,
  "source_wire_canonicalization_contract":
    "hindsight-postgresql-publication-canonical-json/v1"
}

LegacyRestorePayloadSource := {
  "source_format": "LEGACY_SELECTED_ROW_PREIMAGE_V1",
  "source_reader_registry_member_digest": Digest,
  "source_typed_body": LegacyRestoreContentEvidenceRef,
  "source_wire_canonicalization_contract":
    "hindsight-operation-recovery-encrypted-preimage-canonical-json-no-lf/7b165b3"
}

RestorePayloadConversionCommon := {
  "conversion_algorithm": "FIELDWISE_TARGET_RESTORE_V1",
  "kind": "hindsight-postgresql-restore-payload-conversion",
  "restore_payload": TargetRestorePayloadEvidenceRef,
  "restore_payload_digest": Digest,
  "schema_version": 1,
  "source_byte_length": SafeInteger,
  "source_plaintext_digest": Digest
}

RestorePayloadConversion := RestorePayloadConversionCommon &
                            (SuccessorRestorePayloadSource |
                             LegacyRestorePayloadSource)

RollbackPreimageBinding := {
  "authority": "NONE",
  "ciphertext": ProtectedRollbackCiphertextEvidenceRef,
  "conversion": RestorePayloadConversionEvidenceRef,
  "decryption_procedure": EvidenceRef,
  "kind": "hindsight-postgresql-rollback-preimage-binding",
  "lineage_key_digest": Digest,
  "restore_payload": TargetRestorePayloadEvidenceRef,
  "restore_payload_digest": Digest,
  "schema_version": 1,
  "selected_cohort_digest": Digest,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

OperationGrant := {
  "action": "apply" | "rollback",
  "expected_target_generation": TargetGeneration,
  "grant_id": Id,
  "issued_at_unix_ns": UInt128String,
  "issuer_principal": Text,
  "kind": "hindsight-postgresql-operation-grant",
  "operation_identity": Id,
  "preserved_cohort_digest": Digest,
  "publication_epoch": SafeInteger,
  "schema_version": 1,
  "selected_cohort_digest": Digest,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest,
  "valid_until_unix_ns": UInt128String
}

OperationGrantRevocation := {
  "grant": EvidenceRef,
  "kind": "hindsight-postgresql-operation-grant-revocation",
  "reason": ContractId,
  "revocation_id": Id,
  "revoked_at_unix_ns": UInt128String,
  "revoker_principal": Text,
  "schema_version": 1
}

OperationRetryLimits := {
  "kind": "hindsight-postgresql-operation-retry-limits",
  "maximum_j_attempts": PositiveSafeInteger,
  "maximum_m_attempts": PositiveSafeInteger,
  "maximum_p_attempts": PositiveSafeInteger,
  "maximum_r_attempts": PositiveSafeInteger,
  "maximum_verification_attempts": PositiveSafeInteger,
  "schema_version": 1
}

OperationReconciliationLimits := {
  "kind": "hindsight-postgresql-operation-reconciliation-limits",
  "maximum_ambiguity_resolution_ns": UInt128String,
  "maximum_reconciliation_attempts": PositiveSafeInteger,
  "maximum_reconciliation_duration_ns": UInt128String,
  "schema_version": 1
}

OperationBudgetLimits := {
  "j_attempt_duration_ns": UInt128String,
  "kind": "hindsight-postgresql-operation-budget-limits",
  "m_attempt_duration_ns": UInt128String,
  "maximum_elapsed_ns": UInt128String,
  "maximum_mutated_rows": PositiveSafeInteger,
  "maximum_preserved_rows": SafeInteger,
  "maximum_selected_rows": PositiveSafeInteger,
  "p_attempt_duration_ns": UInt128String,
  "r_attempt_duration_ns": UInt128String,
  "schema_version": 1,
  "verification_attempt_duration_ns": UInt128String
}

PlannedWorkAggregateIdentity := {
  "identity_kind": "PLANNED_REQUEST",
  "operation_identity": Id,
  "publication_epoch": SafeInteger
}

CommittedWorkAggregateIdentity := {
  "identity_kind": "COMMITTED_J",
  "journal_digest": Digest
}

OperationWorkAggregateIdentity := PlannedWorkAggregateIdentity |
                                  CommittedWorkAggregateIdentity

OperationInvocationContext := {
  "invocation_mode": "FORWARD" | "RECOVERY",
  "recovery_mode": "NONE" | "ADVANCE_STAGE" | "VERIFY_STAGE" |
                   "RESOLVE_TRANSACTION" | "QUERY_AMBIGUITY" |
                   "RECONCILE_SUBJECT",
  "recovery_request_id": Id | "NONE"
}

StageAttemptWorkIdentity := {
  "aggregate_identity": OperationWorkAggregateIdentity,
  "attempt_ordinal": PositiveSafeInteger,
  "identity_kind": "STAGE_ATTEMPT",
  "invocation": OperationInvocationContext,
  "lineage_predecessor_digest": Digest | "GENESIS",
  "predecessor_stage_digest": Digest | "NONE",
  "stage": "J" | "P" | "R" | "M",
  "stage_attempt_id": Id
}

VerificationAttemptWorkIdentity := {
  "attempt_ordinal": PositiveSafeInteger,
  "identity_kind": "VERIFICATION_ATTEMPT",
  "invocation": OperationInvocationContext,
  "mutation_receipt_digest": Digest,
  "verification_attempt_id": Id
}

TransactionResolutionWorkIdentity := {
  "attempt_ordinal": PositiveSafeInteger,
  "identity_kind": "TRANSACTION_RESOLUTION",
  "invocation": OperationInvocationContext,
  "original_work_identity_digest": Digest,
  "stage": "J" | "P" | "R" | "M" | "V" | "RECONCILIATION",
  "transaction_identity": TransactionIdentityEvidenceRef,
  "transaction_resolution_id": Id
}

AmbiguityQueryWorkIdentity := {
  "ambiguity_query_id": Id,
  "attempt_ordinal": PositiveSafeInteger,
  "identity_kind": "AMBIGUITY_QUERY",
  "invocation": OperationInvocationContext,
  "original_work_identity_digest": Digest,
  "stage": "J" | "P" | "R" | "M" | "V" | "RECONCILIATION",
  "transaction_identity": TransactionIdentityEvidenceRef
}

ReconciliationWorkIdentity := {
  "attempt_ordinal": PositiveSafeInteger,
  "identity_kind": "RECONCILIATION",
  "invocation": OperationInvocationContext,
  "reconciliation_id": Id,
  "reconciliation_kind": "CONCLUSIVE_NONCOMMIT" |
                         "FENCE_STATE_RECONCILIATION" |
                         "PUBLICATION_QUALIFICATION_ATTEMPT_RECONCILIATION" |
                         "TERMINAL_OUTCOME_RECONCILIATION",
  "original_work_identity_digest": Digest,
  "subject": EvidenceRef,
  "subject_identity_digest": Digest
}

OperationWorkIdentity := StageAttemptWorkIdentity |
                         VerificationAttemptWorkIdentity |
                         TransactionResolutionWorkIdentity |
                         AmbiguityQueryWorkIdentity |
                         ReconciliationWorkIdentity

TransactionReconciliationSubject := {
  "original_reservation": OperationWorkReservationEvidenceRef,
  "original_start": OperationWorkStartEvidenceRef,
  "original_transaction_identity": TransactionIdentityEvidenceRef,
  "original_work_identity_digest": Digest,
  "stage": "J" | "P" | "R" | "M" | "V" | "RECONCILIATION",
  "subject_kind": "TRANSACTION"
}

FenceReconciliationSubject := {
  "active_fence_binding": EvidenceRef,
  "admission_generation": SafeInteger,
  "publication_epoch": SafeInteger,
  "subject_kind": "FENCE_STATE",
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

PublicationQualificationAttemptReconciliationSubject := {
  "aggregate_identity": CommittedWorkAggregateIdentity,
  "original_reservation": OperationWorkReservationEvidenceRef,
  "original_start": OperationWorkStartEvidenceRef,
  "original_transaction_identity": TransactionIdentityEvidenceRef,
  "original_work_identity": StageAttemptWorkIdentity,
  "original_work_identity_digest": Digest,
  "stage": "R",
  "subject_kind": "PUBLICATION_QUALIFICATION_ATTEMPT"
}

TerminalOutcomeReconciliationSubject := {
  "aggregate_identity": OperationWorkAggregateIdentity,
  "subject_kind": "TERMINAL_OUTCOME",
  "terminal_result": EvidenceRef,
  "terminal_result_digest": Digest
}

ReconciliationSubjectValue := TransactionReconciliationSubject |
                              FenceReconciliationSubject |
                              PublicationQualificationAttemptReconciliationSubject |
                              TerminalOutcomeReconciliationSubject

ReconciliationSubject := {
  "kind": "hindsight-postgresql-reconciliation-subject",
  "schema_version": 1,
  "subject": ReconciliationSubjectValue
}

OperationWorkRequest := {
  "kind": "hindsight-postgresql-operation-work-request",
  "plan": EvidenceRef,
  "request_id": Id,
  "schema_version": 1,
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest
}

OperationWorkPreflightResult := {
  "committed_result": EvidenceRef | "NONE",
  "kind": "hindsight-postgresql-operation-work-preflight-result",
  "outcome": "COMMITTED_REPLAY" | "UNRESOLVED",
  "request": EvidenceRef,
  "request_key_digest": Digest,
  "schema_version": 1
}

OperationWorkPreReservationRefusal := {
  "accounting_state_digest": Digest,
  "authority": "NONE",
  "evidence": EvidenceRef,
  "kind": "hindsight-postgresql-operation-work-pre-reservation-refusal",
  "refusal_code": "ARITHMETIC_OVERFLOW" | "ATTEMPT_LIMIT_EXHAUSTED" |
                  "BUDGET_EXHAUSTED" | "CLOCK_BINDING_INVALID" |
                  "DEADLINE_UNREPRESENTABLE" | "REQUEST_CONFLICT" |
                  "ROW_LIMIT_EXCEEDED" | "WORK_ALREADY_RESERVED",
  "request": EvidenceRef,
  "request_key_digest": Digest,
  "schema_version": 1
}

OperationWorkReservation := {
  "ambiguity_resolution_deadline_monotonic_ns": UInt128String | "NONE",
  "attempt_ordinal": PositiveSafeInteger,
  "boot_identity": EvidenceRef,
  "charged_elapsed_ns": UInt128String,
  "charged_mutated_rows": SafeInteger,
  "charged_preserved_rows": SafeInteger,
  "charged_reconciliation_ns": UInt128String,
  "charged_selected_rows": SafeInteger,
  "clock_envelope": EvidenceRef,
  "kind": "hindsight-postgresql-operation-work-reservation",
  "plan": EvidenceRef,
  "request": EvidenceRef,
  "reservation_ordinal": PositiveSafeInteger,
  "reserved_at": EvidenceRef,
  "reserved_at_monotonic_upper_ns": UInt128String,
  "schema_version": 1,
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest,
  "work_class": "J" | "P" | "R" | "M" | "V" | "RECONCILIATION"
}

TransactionIdentity := {
  "adapter_incarnation_id": Id,
  "aggregate_identity": OperationWorkAggregateIdentity,
  "kind": "hindsight-postgresql-transaction-identity",
  "plan": EvidenceRef,
  "schema_version": 1,
  "stage": "J" | "P" | "R" | "M" | "V" | "RECONCILIATION",
  "target_database_identity": EvidenceRef,
  "transaction_identity_id": Id,
  "transaction_mode": "STAGE_EFFECT" | "VERIFICATION" |
                      "TRANSACTION_RESOLUTION" | "AMBIGUITY_QUERY" |
                      "SUBJECT_RECONCILIATION",
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest
}

OperationWorkStart := {
  "adapter_incarnation_id": Id,
  "kind": "hindsight-postgresql-operation-work-start",
  "reservation": EvidenceRef,
  "schema_version": 1,
  "start_nonce": Id,
  "transaction_identity": EvidenceRef,
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest
}

OperationWorkCommittedResult := {
  "kind": "hindsight-postgresql-operation-work-committed-result",
  "plan": EvidenceRef,
  "reservation": EvidenceRef,
  "result": EvidenceRef,
  "result_kind": "J" | "P" | "R_VALID" | "R_LATE" | "M" | "V" |
                 "CONCLUSIVE_NONCOMMIT" |
                 "TRANSACTION_RESOLUTION_OUTCOME" |
                 "AMBIGUITY_QUERY_OUTCOME" |
                 "VERIFICATION_MISMATCH" |
                 "VERIFICATION_TERMINAL_FAILURE" |
                 "VERIFICATION_UNABLE" | "RECOVERY_OBSERVATION",
  "schema_version": 1,
  "start": EvidenceRef,
  "transaction_identity": EvidenceRef,
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest
}

OperationWorkTransactionResolutionOutcome := {
  "authority": "NONE",
  "kind": "hindsight-postgresql-operation-work-transaction-resolution-outcome",
  "original_committed_result": OperationWorkCommittedResultEvidenceRef,
  "original_reservation": OperationWorkReservationEvidenceRef,
  "original_result": EvidenceRef,
  "original_start": OperationWorkStartEvidenceRef,
  "original_transaction_identity": TransactionIdentityEvidenceRef,
  "original_work_identity_digest": Digest,
  "outcome": "ORIGINAL_COMMITTED",
  "resolution_reservation": OperationWorkReservationEvidenceRef,
  "resolution_start": OperationWorkStartEvidenceRef,
  "resolution_transaction_identity": TransactionIdentityEvidenceRef,
  "resolution_work_identity": TransactionResolutionWorkIdentity,
  "resolution_work_identity_digest": Digest,
  "schema_version": 1
}

OperationWorkAmbiguityQueryOutcome := {
  "authority": "NONE",
  "kind": "hindsight-postgresql-operation-work-ambiguity-query-outcome",
  "original_committed_result": OperationWorkCommittedResultEvidenceRef,
  "original_reservation": OperationWorkReservationEvidenceRef,
  "original_result": EvidenceRef,
  "original_start": OperationWorkStartEvidenceRef,
  "original_transaction_identity": TransactionIdentityEvidenceRef,
  "original_work_identity_digest": Digest,
  "outcome": "ORIGINAL_COMMITTED",
  "query_reservation": OperationWorkReservationEvidenceRef,
  "query_start": OperationWorkStartEvidenceRef,
  "query_transaction_identity": TransactionIdentityEvidenceRef,
  "query_work_identity": AmbiguityQueryWorkIdentity,
  "query_work_identity_digest": Digest,
  "schema_version": 1
}

OperationWorkConclusiveNoncommitResult := {
  "authority": "NONE",
  "kind": "hindsight-postgresql-operation-work-conclusive-noncommit-result",
  "original_reservation": EvidenceRef,
  "original_start": EvidenceRef,
  "original_transaction_identity": EvidenceRef,
  "original_work_identity": OperationWorkIdentity,
  "original_work_identity_digest": Digest,
  "outcome": "CONCLUSIVE_NONCOMMIT",
  "reconciliation_subject": EvidenceRef,
  "recovery_request_id": Id,
  "resolution_evidence": EvidenceRef,
  "resolution_reservation": EvidenceRef,
  "resolution_start": EvidenceRef,
  "resolution_transaction_identity": EvidenceRef,
  "resolution_work_identity": ReconciliationWorkIdentity,
  "resolution_work_identity_digest": Digest,
  "schema_version": 1
}

OperationAccountingState := {
  "charged_elapsed_ns": UInt128String,
  "charged_mutated_rows": SafeInteger,
  "charged_preserved_rows": SafeInteger,
  "charged_reconciliation_ns": UInt128String,
  "charged_selected_rows": SafeInteger,
  "j_attempts": SafeInteger,
  "m_attempts": SafeInteger,
  "next_reservation_ordinal": PositiveSafeInteger,
  "p_attempts": SafeInteger,
  "plan": EvidenceRef,
  "r_attempts": SafeInteger,
  "reconciliation_attempts": SafeInteger,
  "verification_attempts": SafeInteger
}

OperationPlan := {
  "action_binding": ApplyBinding | SuccessorRollbackBinding |
                    LegacyRollbackBinding,
  "created_at_unix_ns": UInt128String,
  "expected_target_generation": TargetGeneration,
  "kind": "hindsight-postgresql-operation-plan",
  "operation_identity": Id,
  "plan_issuer_principal": Text,
  "preserved_cohort_digest": Digest,
  "publication_epoch": SafeInteger,
  "schema_version": 1,
  "selected_cohort_digest": Digest,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest,
  "valid_until_unix_ns": UInt128String
}

OperationApproval := {
  "decision": "APPROVE",
  "issued_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-operation-approval",
  "operator_principal": Text,
  "plan": EvidenceRef,
  "schema_version": 1,
  "valid_until_unix_ns": UInt128String
}

OperationAuthorizationReceipt := {
  "approval": EvidenceRef,
  "authorization_principal": Text,
  "decision": "AUTHORIZE",
  "issued_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-operation-authorization-receipt",
  "plan": EvidenceRef,
  "schema_version": 1,
  "valid_until_unix_ns": UInt128String
}

OperationAuthorityRevocation := {
  "approval": EvidenceRef | "NONE",
  "authorization_receipt": EvidenceRef | "NONE",
  "expected_state": "PLAN_ISSUED" | "APPROVED" | "AUTHORIZED",
  "grant": EvidenceRef,
  "kind": "hindsight-postgresql-operation-authority-revocation",
  "plan": EvidenceRef,
  "reason": ContractId,
  "revocation_id": Id,
  "revoked_at_unix_ns": UInt128String,
  "revoker_principal": Text,
  "schema_version": 1
}

PreStageExpiryObservation := {
  "admission": StageAdmission,
  "approval": EvidenceRef,
  "approval_expiry_unix_ns": UInt128String,
  "authority": "NONE",
  "authorization_receipt": EvidenceRef,
  "clock_envelope": EvidenceRef,
  "elapsed_upper_ns": UInt128String,
  "forward_rate_error_denominator": UInt128String,
  "forward_rate_error_numerator": UInt128String,
  "forward_rate_error_upper_ns": UInt128String,
  "kind": "hindsight-postgresql-pre-stage-expiry-observation",
  "monotonic_anchor_lower_ns": UInt128String,
  "monotonic_sample_upper_ns": UInt128String,
  "monotonic_validity_deadline_lower_ns": UInt128String,
  "observation_request_id": Id,
  "plan": EvidenceRef,
  "qualification": "CURRENT" | "LATE",
  "schema_version": 1,
  "stage": "J" | "P",
  "stage_predecessor_digest": Digest | "NONE",
  "trusted_upper_bound_unix_ns": UInt128String,
  "wall_upper_at_anchor_unix_ns": UInt128String
}
```

`ClosurePolicyLimits/v1` is a closed, immutable support-profile component, not
an extension bag. Every attempt count, lifetime, duration, and timeout is the
displayed positive finite `PositiveSafeInteger` in milliseconds; zero,
`NONE`, omission, an implementation default, infinity, and a database value
whose zero spelling disables a guard are invalid. The exact body reference is
the closure-policy identity. Qualification must prove that the named
PostgreSQL release, controller adapter, and operating-system profile implement
every guard with the specified finite semantics; deployment admission must
observe the same body and those capabilities. A different value or body digest
is a different, unqualified policy.

Every collection above is duplicate-free and ordered by its displayed stable
identity: artifact digest, identity digest, component class, clause ID,
procedure ID, tool ID, case ID, numeric seed, displayed enum order for every enum-valued
sequence, support-profile body digest, release digest, claim ID, target-identity
body digest, or target-surface digest. An omitted, extra, duplicated, aliased,
or reordered member is invalid. `ImmutableArtifact.artifact_digest` is
SHA-256 over exactly `byte_length` bytes. An `EvidenceIdentity.descriptor`, a
profile configuration, procedure or tool implementation, stimulus parameter
bytes, fixture bytes, generator implementation, failure artifact, real-artifact
payload, or decryption implementation must name `ImmutableArtifact/v1`.
`RollbackPreimageBinding.ciphertext` instead names one
`ProtectedRollbackCiphertext/v1`; that body's `artifact` names the exact
`ImmutableArtifact/v1` descriptor for the retained bytes.

Every `ContractBody/v1` has a nonempty `clauses` sequence with unique,
strictly ASCII-ascending `clause_id` values. Its owner class and ID equal the
body that references it. Its immutable stable key is
`(owner_class, owner_id, contract_role)`; exact replay requires byte equality,
and changed bytes conflict. A `ProcedureContract/v1` names three distinct
`ContractBody/v1` bodies with owner class `PROCEDURE`, its exact
`procedure_id`, and roles `INPUT`, `OUTPUT`, and `PROCEDURE_STEPS`. A
`ToolContract/v1` names three distinct bodies with owner class `TOOL`, its
exact `tool_id`, and roles `INPUT`, `INVOCATION`, and `OUTPUT`. A
`HistoricalGenerator/v1` names one body with owner class `GENERATOR`, its
exact `generator_id`, and role `OUTPUT`. These references and every nested
body resolve through `EvidenceRef`; the registrar recomputes each complete
successor-canonical body digest, including its LF. A bare digest, missing
body, caller-supplied clause set, or contract body registered after plan
acceptance is invalid.

`TargetSurfaceContract/v1` is the complete target-state projection contract.
Its `target_surface_digest` is the compatibility contract's SHA-256 over the
separator-free sequence of complete compatibility-canonical
`TargetSurfaceRelation` member bytes, including each member LF. The successor
body must project bijectively to those same relation, column, and key fields;
its own body digest is distinct and cannot substitute for the surface digest.
Every relation in the protected surface appears once. Relations are ordered by
the standalone compatibility-canonical relation-contract member bytes. Within
a relation, columns are nonempty and strictly ordered by numeric
`attnum`; the relation digest in every column equals the enclosing relation;
and no two columns share an `attnum`, name, or identity digest. The nonempty
`key_column_identity_digests` sequence is the numeric-`attnum` order of an
exact subset of those columns. Every key column is nonnullable. A different
relation, column, key, type OID, typmod, collation, nullability, or projected
value type is a different surface and cannot reuse the digest.
Relation and schema names are nonempty exact server-returned scalar sequences;
v1 admits only ordinary and partitioned tables with PostgreSQL `relkind`
`"r"` and `"p"`, respectively.

The canonical identity digest for a relation, column, or row is SHA-256 over
the complete standalone successor-canonical bytes of its displayed typed body,
including the LF. A row identity repeats the enclosing relation digest and has
exactly one nonnull key value for every surface key column in the same numeric
`attnum` order. Each key value's column body and digest equal that surface
column. Within a row projection, `columns` contains every surface column once
in numeric `attnum` order, including the key columns; every column body and
digest equals the surface contract. A nonnull value's tag must equal its
column's `postgresql_value_type`; `NULL` is permitted exactly when
`nullable=true`. A row cannot be identified by physical tuple position,
iteration order, a formatted SQL value, or a digest whose body is unavailable.

`SIGNED_INT64` carries the exact mathematical PostgreSQL integer in
`SignedInt64String`. `UTF8_TEXT` carries the exact decoded database scalar
sequence without normalization. `BYTEA` carries the exact octets in canonical
unpadded base64url and their decoded length. `UTC_INSTANT_MICROSECONDS_2000`
carries the signed count of microseconds from `2000-01-01T00:00:00Z` on
PostgreSQL's timestamp timeline; the v1 surface admits neither infinity nor a
session-time-zone interpretation. `BOOLEAN` is a JSON boolean, and `NULL` has
no `value` member. SQL rendering, locale, driver-native values, floating-point
timestamps, and JSON numbers for signed values are never canonical values.

Each `TargetCohortMembership/v1` contains every target-surface relation exactly
once in surface order, including a relation with no members, and only complete
row identities. Rows are strictly ordered by their standalone canonical
`TargetRowIdentity/v1` bytes, including the LF, and duplicate row identities
conflict. `selected_cohort_digest` and `preserved_cohort_digest` are SHA-256
over the respective complete standalone membership bodies including the LF.
They identify membership, not mutable row content.

Each `TargetCohortProjection/v1` embeds the exact matching membership body and
digest, then supplies every projected column value for precisely those rows in
the same relation and row order. Under target locks, the plan's exact selection
predicate places every row in the target surface in exactly one membership:
`SELECTED` or `PRESERVED`. Selected is nonempty; preserved may be empty. The
two memberships use the same target and surface and cannot share a row
identity. Their union must equal the complete protected surface read;
omission, addition, overlap, a relation outside the surface, a projection row
outside its membership, or a digest-only row is invalid. Production and
independent oracle readers must reconstruct these same complete membership,
relation, row, column, and value bodies from separately acquired PostgreSQL
values; neither may consume a body or digest produced by the other.

`TargetMutationImage/v1` is the only mutation-state image. Its two embedded
cohort bodies and digests obey the preceding contract, and its target, surface,
lineage key, generation, and complete cohorts are read together under the
target and lineage locks. `M.before_image_digest`, `M.after_image_digest`, and
every compatibility `snapshot_digest` are SHA-256 over the applicable complete
mutation-image bytes, including one LF. The before and after images are always
distinct bodies because a valid `M` increments `target_generation` exactly
once, even when every projected value is unchanged.

`TargetApplyPayload/v1` is the plan-bound desired selected-cohort postimage,
not mutation state or authority. It contains no generation, preserved cohort,
or mutation-image digest. Its `apply_payload_digest` is SHA-256 over its
complete standalone successor-canonical bytes including one LF. Its target,
surface, lineage key, selected membership, membership digest, relations, rows,
columns, and PostgreSQL values use the exact closed target-projection bodies
above. The apply plan's selected membership must equal the independently
reconstructed locked before-image membership; the payload supplies all and
only the selected values after apply. No procedure name, caller-side query,
implementation default, or separately fetched desired state may supplement or
transform it.

`TargetRestorePayload/v1` is content, not mutation state. It contains only the
selected cohort to restore plus its target, surface, lineage key, and cohort
digest. It contains no generation, preserved cohort, or mutation-image digest.
Its `restore_payload_digest` is SHA-256 over its complete standalone
successor-canonical bytes including the LF. For successor apply, the payload's
selected cohort is byte-identical to the selected cohort embedded in the apply
before image. The protected ciphertext plaintext is exactly those payload
bytes, never the before-image bytes.

`RestorePayloadConversion/v1` authenticates the retained plaintext and its one
typed output. `SUCCESSOR_TARGET_RESTORE_PAYLOAD_V1` requires the source bytes
to be the exact referenced `TargetRestorePayload/v1` bytes with their LF,
`source_reader_registry_member_digest="NONE"`, and the successor canonical
wire-contract identifier. `LEGACY_SELECTED_ROW_PREIMAGE_V1` requires the exact
historical no-LF plaintext bytes, their SHA-256 and length, the selected frozen
reader member, and `source_typed_body` equal to that reader's exact registered
`LegacyRestoreContent/v1` output. Its fieldwise conversion copies target,
surface, relation, column, row-key, cohort membership, and values into the
corresponding successor bodies; converts historical signed integers and time
counts to the same bounded shortest decimal strings; re-encodes byte strings as
canonical unpadded base64url; preserves text scalar sequences; and rejects any
unsupported type, normalization, missing column, extra row, duplicate identity,
or lossy conversion. It then independently canonicalizes the complete
`TargetRestorePayload/v1` with one LF and requires both its body reference and
digest to match. No historical wire byte is reinterpreted as successor JSON.

`RollbackPreimageBinding/v1` resolves that exact conversion, restore payload,
decryption procedure, protected ciphertext body, and retained ciphertext bytes.
Its target, surface, lineage key, selected-cohort digest, payload reference,
and payload digest equal the conversion output. Ciphertext digest and length
authenticate the retained encrypted bytes; decryption authenticates the exact
source plaintext digest and length; the frozen reader when required and the
deterministic conversion authenticate the successor restore payload. None of
these values is a mutation-image digest.

For rollback, derive the after image only inside `M`: lock and reconstruct the
complete rollback before image at generation `g`; require its selected row
identity set to equal the payload's set and its preserved cohort to remain
unchanged; replace each selected row with the payload row; merge selected and
preserved rows in the canonical surface order; recompute both complete cohort
bodies and digests; set generation to checked `g + 1`; and construct the after
`TargetMutationImage/v1` with the same target, surface, and lineage key. The
after selected cohort equals the restore payload's selected cohort, and the
after preserved cohort equals the rollback before image's preserved cohort.
`M.after_image_digest` hashes that newly generated state image. Requiring it to
equal an earlier-generation payload or image digest is invalid.

For apply, derive the after image only inside `M`: lock and reconstruct the
complete apply before image at generation `g`; require its selected row
identity set to equal the `TargetApplyPayload/v1` selected set and its
preserved cohort to remain unchanged; substitute each selected row's complete
projected values from the payload without changing row or column membership;
merge selected and preserved rows in canonical surface order; recompute both
complete cohort bodies and digests; set generation to checked `g + 1`; and
construct the after `TargetMutationImage/v1` with the same target, surface, and
lineage key. The after selected cohort equals the apply payload's selected
cohort, and the after preserved cohort equals the apply before image's
preserved cohort. `M.after_image_digest` hashes that derived state image.

`RoleGrantSet.grant_paths` and `WriterInventory.writer_paths` are sets encoded
as sequences: encode each complete member independently under the successor
canonical-byte contract including its LF, reject duplicate bytes and duplicate
stable tuples, and sort by unsigned lexicographic comparison of those bytes.
Role identities are strictly increasing by numeric role OID with unique OIDs
and names. Role, object, and subobject OIDs; grantor and grantee; privilege; option bits;
and path class are all part of a grant's stable tuple. Actor, target relation,
complete grant-path member chain, every tagged routine call node and edge,
effective role, service identity, login role, entry mode, and underlying-path
digest are all part of a writer path's stable tuple. Target-relation OIDs are
strictly increasing numeric values; service
identities are duplicate-free and ordered by complete referenced body bytes.
An omitted, extra, reordered, duplicated, or differently canonicalized member
invalidates the whole body.

`WriterInventory.target_surface` is the exact typed bridge to the compatibility
contract's `TargetSurface.relations`. Its nonempty relation members project
bijectively to the compatibility relation, column, and key-column fields and
are ordered by their complete compatibility-canonical member bytes.
`target_surface_digest` is SHA-256 over the separator-free concatenation of
those independently canonicalized member bytes in that order; the digest is
never an input to its own preimage. The
inventory's strictly increasing `target_relation_oids` is exactly the numeric
OID set projected from those members. The role-grant set, every writer service
identity, and every deployment or compatibility projection uses that same
digest. Every direct, administrative, routine, and service-underlying writer
path names a relation in that exact set. A same digest with a different
preimage or OID projection, duplicate relation or OID, out-of-surface relation,
empty surface, or cross-database path invalidates the body.

The protected rollback-ciphertext constructor stores the exact ciphertext
octets in PostgreSQL together with the immutable typed body. Before plan
issuance it computes SHA-256 and octet length from the stored bytes, requires
them to equal `ciphertext_digest`, `byte_length`, and the nested artifact
descriptor, and keys the protected byte row by `(ciphertext_digest,
byte_length)`. A descriptor, digest, length, private path, or readable external
copy without that verified protected byte row is unavailable. Plan issuance
locks and rechecks the typed candidate and byte row. The matching `J`
transaction atomically adopts the exact preimage binding and protected
ciphertext row into journal-owned state; neither adoption may commit without
the other or without `J`. The bytes remain protected PostgreSQL state through
the matching verified rollback or a separately authorized permanent
retirement. Private files are nonauthoritative exports or backups, and their
presence or loss does not change rollback eligibility.

`EvidenceIdentity.identity_digest` is derived rather than caller-chosen. It is
SHA-256 over the successor-canonical bytes, including their LF, of this exact
closed projection: `{"descriptor": EvidenceRef, "identity_class": IdentityClass,
"release_digest": Digest | "NONE"}`. The values equal the corresponding
identity-body fields. A different digest, alternate projection, or digest-only
identity is invalid.

For `identity_class=TARGET_DATABASE`, `release_digest` is exactly `"NONE"`
and `descriptor` names an `ImmutableArtifact/v1` with
`media_type=APPLICATION_JSON`. Its bytes are exactly one successor-canonical
`TargetDatabaseIdentityBody`, including the LF, and its byte length and digest
match those bytes. The `postgres_system_identifier` is therefore the shortest
UInt128 decimal string, while the OID is a safe JSON integer. Define
`compatibility_target_identity(X)` by resolving that descriptor and projecting
the three same-named fields to the compatibility contract's
`TargetDatabaseIdentity`. This projection is bijective: a compatibility target
identity is admissible only when its system identifier fits UInt128, and its
unique successor identity is the exact `TARGET_DATABASE` body whose descriptor
contains those canonical bytes. Activation and legacy rollback compare this
complete projection, not an opaque identity digest or database name alone.

`PostgresqlComponentConfiguration.unix_socket_directories` is the complete
ordered effective PostgreSQL Unix-socket-directory sequence, not a selected
directory or a comma-delimited setting string. For each member, lexical
normalization requires an absolute path with one leading separator, no repeated
separator, and no `.` or `..` component. Input trailing separators are removed
before body construction except that `/` remains `/`; a body containing a
trailing separator on any other path is noncanonical. The protected profiler
resolves every symlink in the configured path while holding the opened final
directory, requires an existing directory, and records its absolute resolved
path plus unsigned device and file identifiers. It rechecks that handle and
identity while deriving the live projection and endpoint. A resolution race,
missing directory, non-directory, symlink retarget, or changed configured path,
resolved path, device, file identifier, member count, or member order is
profile and endpoint drift.

The initial `macos-local-postgresql-v1` profile requires exactly one sequence
member. Its endpoint has `transport=UNIX_DOMAIN_SOCKET`, `port="NONE"`, and
`unix_socket_directory` equal to that member. Its one complete canonical
pathname is `resolved_path || "/.s.PGSQL." || decimal(postgresql_port)`, except
that the separator is not doubled when `resolved_path` is `/`. The live
projection copies the complete directory sequence byte for byte from the
PostgreSQL configuration and rederives the same endpoint. A directory alone,
an address derived from the configured path instead of the bound resolved
path, an alternate path spelling, or any additional, missing, reordered, or
changed directory is unsupported rather than an equivalent endpoint.
Every non-Unix endpoint has `unix_socket_directory="NONE"`; it cannot retain a
directory binding while selecting another transport.

Each reference position has one exact admitted kind. Support-profile component
fields name `ProfileComponent/v1` with the matching `component_class`;
each component's identity names `EvidenceIdentity/v1` with the same
`IdentityClass`, except that the `BOOT_ENVIRONMENT` component uses
`BOOT_CONFIGURATION`. That identity covers only the stable configuration;
actual boots use `BOOT_ENVIRONMENT`. Its `configuration` names, by component class, exactly
`BootEnvironmentConfiguration/v1`, `ClockConfiguration/v1`,
`FilesystemConfiguration/v1`, `HardwareConfiguration/v1`,
`OperatingSystemConfiguration/v1`, `PostgresqlComponentConfiguration/v1`,
`StorageConfiguration/v1`, or `VirtualizationConfiguration/v1`. No generic
configuration object or implementation-defined field bag is admitted. A clock
envelope names the same exact `CLOCK` component.
`SupportProfile.controller_host`, `.postgresql_host`, `.postgresql_endpoint`,
and `.deployment_topology` name `ControllerHostBinding/v1`,
`PostgresqlHostBinding/v1`, `PostgresqlEndpointBinding/v1`, and
`DeploymentTopologyBinding/v1`. Their nested references name only the exact
binding kinds shown in their grammars. Host identities use
`EvidenceIdentity/v1` with `HOST`; endpoint and target identities use
`ENDPOINT` and `TARGET_DATABASE`; profile fields use the matching
`ProfileComponent/v1`; and a non-`NONE` network path uses
`EvidenceIdentity/v1` with `ENDPOINT`. The two host bodies'
`boot_configuration` fields name the exact stable
`BootEnvironmentConfiguration/v1` body in the support profile. They never
name a boot-session identity.
`MacosLocalPostgresqlLiveProjection/v1` names the exact support profile and its
target, host, endpoint, and eight configuration bodies. Each configuration
reference must equal the matching component's `configuration` reference byte
for byte. Its `unix_socket_directories` equals the PostgreSQL configuration's
complete sequence, and its endpoint embeds the sole initial-profile member.
Its `boot_identity` names `EvidenceIdentity/v1` with `BOOT_ENVIRONMENT` and
equals the boot identity in the exact clock envelope named by `collected_at`.
Its `collected_at` names a `ProtectedTimeObservation/v1` with
`mode=QUALIFIED_CLOCK`, `phase=SUPPORT_PROFILE_PROJECTION`, and the support
profile's exact clock envelope; the protected profiler derives that observation
and the complete projection in one transaction.
`SupportProfile.cold_recovery_procedure` names
`ProcedureContract/v1` with `COLD_RECOVERY`, and
`SupportProfile.failure_injector` names `ToolContract/v1` with
`FAILURE_INJECTOR`. `SupportProfile.closure_policy_limits` names
`ClosurePolicyLimits/v1`. Qualification-plan `abort_policy`,
`acceptance_thresholds`, `closure_policy_limits`, `cold_recovery_procedure`,
`environment_reset_procedure`, `evidence_retention_policy`,
`failure_injector`, `subject_revision`, and `support_profile` fields name,
respectively, `QualificationAbortPolicy/v1`,
`QualificationAcceptanceThresholds/v1`, `ClosurePolicyLimits/v1`,
`ProcedureContract/v1` with
`COLD_RECOVERY`, `ProcedureContract/v1` with `ENVIRONMENT_RESET`,
`EvidenceRetentionPolicy/v1`, `ToolContract/v1` with `FAILURE_INJECTOR`,
`EvidenceIdentity/v1` with `SUBJECT_REVISION`, and `SupportProfile/v1`.
Qualification cells name `EvidenceCaseMatrix/v1`, optional
`RandomizedSchedule/v1`, `EvidenceLimits/v1`, `ToolContract/v1` with `RUNNER`,
`ProcedureContract/v1` with `ACQUISITION`, and `OracleContract/v1`.
Every qualification cell's `conformance_prestate` is `"NONE"` unless the cell
executes an authority-gated transition. An authority-gated cell names exact
`AuthorityGateConformancePrestate/v1` bytes for that cell and gate. Every
derived qualification run instead names
`QualificationRunStimulus/v1`; its nested `case_stimulus` names the exact
`EvidenceStimulus/v1` selected by the case matrix.
An authority-gate prestate's `fixture_state` names exactly one
`AuthorityGateFixtureState/v1`. Its seeded bodies, current slots, explicit
absence markers, lineage values, clock values and envelopes, capability and
session-witness values, revocation states, and plan accounting states are the
complete reachable pre-execution state for that disposable schema. Its tier
results, policy, attestation, receipt, support profile, operation plan,
approval, authorization receipt, and optional compatibility fence binding name
only the exact v1 kinds admitted for those positions elsewhere in this
contract and must appear at the corresponding exact seeded row and current
slot, or at an explicit absence marker when the prestate field is `"NONE"`.
A wrong kind, unresolved seeded reference, duplicate key, or
reachable row, slot, value, or absence not enumerated by that exact body
invalidates plan acceptance before setup or execution.

Each current-slot variant is independently typed. `slot_key_digest` is
SHA-256 over the complete standalone successor-canonical bytes of that
variant's displayed `slot_key` body, including the LF. The key body is copied
into the fixture and recomputed by setup and oracle readers; a digest without
the body is invalid. `ABSENT` is the only absent spelling and has no hidden
value. `PRESENT` admits only the value type and, for a reference, the exact
kind/version in this table:

| Slot class | Exact key preimage | Present value |
| --- | --- | --- |
| `ACTIVE_EPOCH` | `AuthorityGateActiveEpochSlotKey` | `AuthorityGateActiveEpochValue` |
| `ACTIVATION_CAPABILITY` | `AuthorityGateActivationCapabilitySlotKey` | `AuthorityGateActivationCapabilityValue` |
| `ACTIVATION_PROPOSAL` | `AuthorityGateActivationProposalSlotKey` | `ACTIVATION_PROPOSAL`; its attestation is `hindsight-postgresql-deployment-attestation/1` |
| `CLOCK_ENVELOPE` | `AuthorityGateClockEnvelopeSlotKey` | `AuthorityGateClockEnvelopeValue`; exact `hindsight-postgresql-clock-envelope/1` reference |
| `DEPLOYMENT_ATTESTATION` | `AuthorityGateDeploymentAttestationSlotKey` | `AuthorityGateDeploymentAttestationValue`; exact `hindsight-postgresql-deployment-attestation/1` reference |
| `DEPLOYMENT_POLICY` | `AuthorityGateDeploymentPolicySlotKey` | `AuthorityGateDeploymentPolicyValue`; exact `hindsight-postgresql-deployment-admission-policy/1` reference |
| `EVIDENCE_TIER_RESULT` | `AuthorityGateEvidenceTierResultSlotKey` | `AuthorityGateEvidenceTierResultValue`; exact `hindsight-postgresql-evidence-tier-result/1` reference |
| `LEGACY_FENCE` | `AuthorityGateLegacyFenceSlotKey` | `AuthorityGateLegacyFenceValue`; exact `hindsight-compatibility-origin-fence-manifest-binding/1` or `hindsight-compatibility-active-fence-manifest-adoption/1` reference |
| `LINEAGE_HEAD` | `AuthorityGateLineageHeadSlotKey` | `AuthorityGateLineageHeadValue`; digest of exact `M` bytes |
| `OPERATION_ACCOUNTING` | `AuthorityGateOperationAccountingSlotKey` | `OPERATION_ACCOUNTING_STATE` whose plan equals the key |
| `OPERATION_AUTHORITY` | `AuthorityGateOperationAuthoritySlotKey` | `AuthorityGateOperationAuthorityValue`; exact `hindsight-postgresql-operation-authorization-receipt/1` reference |
| `OPERATION_GRANT` | `AuthorityGateOperationGrantSlotKey` | `AuthorityGateOperationGrantValue`; exact `hindsight-postgresql-operation-grant/1` reference |
| `OPERATION_WORK_RESERVATION` | `AuthorityGateOperationWorkReservationSlotKey` | `AuthorityGateOperationWorkReservationValue`; exact `hindsight-postgresql-operation-work-reservation/1` reference |
| `OPERATION_WORK_START` | `AuthorityGateOperationWorkStartSlotKey` | `AuthorityGateOperationWorkStartValue`; exact `hindsight-postgresql-operation-work-start/1` reference |
| `OPERATION_WORK_COMMITTED_RESULT` | `AuthorityGateOperationWorkCommittedResultSlotKey` | `AuthorityGateOperationWorkCommittedResultValue`; exact `hindsight-postgresql-operation-work-committed-result/1` reference |
| `PUBLICATION_EPOCH_HIGH_WATER` | `AuthorityGatePublicationEpochHighWaterSlotKey` | `AuthorityGatePublicationEpochHighWaterValue` |
| `QUALIFICATION_RECEIPT` | `AuthorityGateQualificationReceiptSlotKey` | `AuthorityGateQualificationReceiptValue`; exact `hindsight-postgresql-qualification-receipt/1` reference |
| `RESERVED_ACTIVATION` | `AuthorityGateReservedActivationSlotKey` | `RESERVED_ACTIVATION`; its attestation is `hindsight-postgresql-deployment-attestation/1` |
| `ROLE_GRANT_SET` | `AuthorityGateRoleGrantSetSlotKey` | `AuthorityGateRoleGrantSetValue`; exact `hindsight-postgresql-role-grant-set/1` reference |
| `TARGET_GENERATION` | `AuthorityGateTargetGenerationSlotKey` | `AuthorityGateTargetGenerationValue` |
| `WRITER_INVENTORY` | `AuthorityGateWriterInventorySlotKey` | `AuthorityGateWriterInventoryValue`; exact `hindsight-postgresql-writer-inventory/1` reference |

For every present reference, the complete body resolves and its internal plan,
work identity, target, surface, epoch, claim, tier, or predecessor fields equal
the slot key. A grammar-valid value from another slot class, a reference with
another kind or version, a mismatched key body, a hidden value under `ABSENT`,
or the right value under a recomputed key for another class invalidates the
fixture. Fixture setup, production projection, and the independent oracle each
reconstruct the same per-class variant; none decodes a generic slot bag.

Campaign and evidence-record stimuli, tools, procedures, limits, oracle
contracts, and projections name, respectively, `EvidenceStimulus/v1`,
`ToolContract/v1`, `ProcedureContract/v1` with `ACQUISITION`,
`EvidenceLimits/v1`, `OracleContract/v1`, and `OracleProjection/v1`. An oracle
contract's independent implementation names `ToolContract/v1` with
`tool_class=ORACLE`. Before plan acceptance, campaign registration, run
registration, or oracle evaluation uses one of those contracts, the protected
registrar resolves the complete procedure or tool body and all three of its
typed `ContractBody/v1` references, recomputes every body digest from canonical
bytes, and requires the owner and role bindings above. Planning and evaluation
never accept a caller-supplied input, output, invocation, or step-contract
digest. Historical fixture, generator, frozen-reader,
successor-projection, private-policy, and public-policy positions name their
exact v1 bodies above; a frozen reader executes only through
`HistoricalReaderExecutionBinding/v1`. The binding's stable key is
`reader.reader_registry_member_digest`; exact bytes replay and any second body
for that member conflicts. Its `reader_tool` resolves `ToolContract/v1` with
`tool_class=READER`; `reader_tool_id`, `implementation`, `input_contract`,
`invocation_contract`, and `output_contract` equal that tool body byte for
byte. Its reader contract ID, wire contract, and implementation source revision
equal the complete selected reader member, while its input, invocation, and
output contract bodies name that selector, accept only that wire contract, and
emit only the registered compatibility success or failure body. The binding's
complete successor-canonical bytes and digest are the one canonical
member-to-tool execution identity. A shared executable artifact may appear in
multiple bindings, but it cannot collapse their selectors or contract IDs. A
`HISTORICAL_FIXTURE` stimulus's
`historical_fixture` names `HistoricalFixture/v1`; every other stimulus uses
literal `"NONE"`. Coverage sources name only `HistoricalFixture/v1` or
`HistoricalGenerator/v1`; a generator's output-contract reference resolves
the exact `GENERATOR`/`OUTPUT` `ContractBody/v1` before its plan can be
accepted. Every historical real-evidence stimulus has
`stimulus_class=HISTORICAL_REAL_ARTIFACT`, `historical_fixture="NONE"`, and a
non-`NONE` `input_artifact` naming `RealArtifactBinding/v1`. The binding names
the exact `ImmutableArtifact/v1` payload, declares its mode, names the corpus
plan's exact private-artifact and public-projection policies, and names one
authenticated `PrivateArtifactProvenance/v1` from the approved private store.
For `CONTROLLED_PRIVATE`, the provenance names that payload and has both
`source_acquisition` and `sanitization_procedure` equal to `"NONE"`. For
`SANITIZED_REAL`, it names the sanitized payload, its `source_acquisition`
names an authenticated `CONTROLLED_PRIVATE` provenance body for the real
source bytes, and its `sanitization_procedure` names a
`ProcedureContract/v1` with `procedure_class=SANITIZATION`. In both modes the
private registrar authenticates its own session principal and verifies the
named bytes, store, procedure chain, and exact binding before the body is
eligible for a plan.

Each real-evidence plan cell lists the exact bindings in stimulus order, and
each stimulus names its matching binding. The corresponding
`EvidenceRecord/v1`, `EvidenceRunFailure/v1`, and `EvidenceRunResult/v1` copy
that reference; every non-real run uses literal `"NONE"`. Each controlled
private package artifact names `ImmutableArtifact/v1`; its
`real_artifact_binding` and `artifact_mode` equal the deciding run and plan
cell, its deciding run names `EvidenceRunResult/v1`, its limits name
`EvidenceLimits/v1`, and its subject names `EvidenceIdentity/v1` with
`SUBJECT_REVISION`. A public
projection's limits name `EvidenceLimits/v1`, and its public subject names a
public `EvidenceIdentity/v1` whose descriptor contains no private or
content-derived identifier. An independent review receipt's projection names
`BoundedPublicEvidenceProjection/v1`, its limits and public-subject references
equal the projection's exact typed references, and its reviewer names
`EvidenceIdentity/v1` with `EVIDENCE_REVIEWER`. The projection and receipt copy
the declared real-artifact mode, and their commitment authenticates the
complete private package containing the exact binding reference; neither
public body discloses that reference or a private package digest. A public
synthetic artifact has no authenticated private provenance chain and cannot
satisfy a real-evidence cell. Deployment settings,
policy, matrix, failure, and candidate projection positions name
`PostgresqlSettings/v1`, `DeploymentAdmissionPolicy/v1`,
`CanonicalDeploymentMatrix/v1`, `FailureEvidence/v1`, and
`OracleProjection/v1`. A deployment policy's `allowed_support_profiles` name
only `SupportProfile/v1`, and its `target_database_identities` name only
`EvidenceIdentity/v1` with `TARGET_DATABASE`.
`FailureEvidence.source_identity` names `EvidenceIdentity/v1`. Verification
and recovery failure evidence also names `FailureEvidence/v1`.

`RoleGrantSet/v1` and `WriterInventory/v1` name the exact target database and
surface. A writer inventory's `role_grant_set` names only
`RoleGrantSet/v1`; every `RoleGrantPathRef` selects one exact canonical member
of that body by zero-based ordinal and SHA-256 over that member's standalone
successor-canonical bytes including its LF. Every service identity names an
exact `WriterServiceIdentity/v1` body whose closed compatibility projection is
admitted for the deployment's writer-fence proposal. The inventory's
`service_identities` sequence is exactly the duplicate-free set of service
identity references used by its `SERVICE` writer paths: every such path appears
once through its reference, and no unreferenced extra identity is admitted. A service writer's
`underlying_writer_path_digest`
selects one exact non-service member of the same inventory by that standalone
canonical digest. Its service identity and underlying path have the same target
surface as the inventory, and the underlying target relation is one of the
inventory's target relations. `entry_routine_oid=DIRECT_SQL` permits only a
direct or administrative underlying path whose actor role is exactly
`database_login_role_oid`. A numeric entry routine permits only a routine path
with that exact `entry_routine_oid`; its root actor is either that login role or
`PUBLIC` through the path's first exact execute-grant edge, and every intervening
membership or assumption edge begins from that login. The terminal grant or
call edge must derive the same effective actor and target relation recorded by
the underlying member. An unresolved reference, wrong kind or version, wrong
ordinal, digest mismatch, recursive service path, unrelated login or routine,
entry-mode mismatch, broken grant or call reachability, or cross-target member
is invalid.

`RoleGrantee` uses `(PUBLIC, NONE)` or `(ROLE, SafeInteger)` and no other
combination. Object and subobject OIDs are the exact catalog identities; zero
is used only for a whole object, never as an unknown. `ALL_SCHEMAS` is admitted
only for a catalog default-privilege row whose namespace is absent. Every
writer grant-path chain is nonempty, duplicate-free, and ordered from the
actor through each exact membership or assumption edge to the terminal
attribute, ownership, ACL, or default-privilege edge that creates the
capability. Every routine call chain is nonempty and ordered from the callable
entry point to the mutation-capable routine. Adjacent edges have byte-identical
`to` and `from` nodes. `ROUTINE_CALL`, `TRIGGER_DISPATCH`, `RULE_REWRITE`, and
`EXTENSION_DISPATCH` carry the corresponding tagged catalog identities;
`DYNAMIC_SQL_RESOLUTION` ends in a `DYNAMIC_SQL` node whose source routine,
statement digest, resolved target relation, and mutation target are exact and
whose target relation equals the path's target. A statically mutating routine
ends with `DIRECT_RELATION_MUTATION` to a `RELATION` node whose object OID and
mutation target both equal the path's target relation. Other edges use
`mutation_target_relation_oid=NONE`; exactly the terminal direct or dynamic
mutation edge carries the target relation. The first `from` node is the exact
entry `ROUTINE`, and the terminal edge resolves the path's exact target
relation. A path with an unknown catalog object,
ambiguous effective role, unresolved call edge, unbounded dynamic target, or
missing service-to-login attribution is unclassifiable and invalidates the
entire inventory.

`DeploymentEvidenceAcquisition/v1.observed_projection` names the exact live
projection stored for its planned deployment run and oracle;
`acquisition_procedure` equals the run requirement's exact
`ProcedureContract/v1`, and `acquired_at` names
`ProtectedTimeObservation/v1` with `mode=QUALIFIED_CLOCK` and
`phase=DEPLOYMENT_EVIDENCE_ACQUIRE`. The observation subject is the exact
successor-canonical projection of `(campaign, run_id, oracle_id,
acquisition_id)`. The protected profiler constructs the observation,
projection, and acquisition body from one live collection under one clock
envelope before returning any reference. A caller or evidence registrar cannot
construct, replace, or reissue it.

Clock-envelope boot, host, and synchronization-epoch fields name
`EvidenceIdentity/v1` with the matching identity class. Deployment-attestation
boot, endpoint, host, storage, and target-database fields do the same. Every
operation-plan, preimage, `J`, `V`, mismatch, unable, and terminal-failure
target-database field names the exact `TARGET_DATABASE` identity body. Equal
identity means byte-identical `EvidenceRef`, not only an equal nested digest.
Qualification-receipt and deployment-attestation `closure_policy_limits`
fields name `ClosurePolicyLimits/v1`.

Every start, completion, acquisition, work-reservation, receipt-issuance, attestation-issuance,
support-profile-projection, and disposition-application observation
field names `ProtectedTimeObservation/v1`. `QUALIFIED_CLOCK` names the exact
current `ClockEnvelope/v1` and carries its protected monotonic sample and
recomputed conservative upper bound. Its lower and upper sample bounds are
server-derived and satisfy `lower <= upper`. `PROTECTED_REGISTRATION` has
`clock_envelope="NONE"`, `monotonic_sample_lower_ns="NONE"`, and
`monotonic_sample_upper_ns="NONE"`; the protected
registrar, never a producer, supplies its server observation. Release
qualification runs, qualification-receipt issuance, and deployment-attestation
issuance require `QUALIFIED_CLOCK`. Deployment-run completion and disposition
application also require `QUALIFIED_CLOCK`.

Each `subject_key_digest` is SHA-256 over the successor-canonical bytes,
including their one trailing LF, of exactly one closed projection below. No
caller may add, omit, or normalize a field:

```text
CAMPAIGN_START := {
  "campaign_id": Id,
  "campaign_plan": EvidenceRef,
  "subject_type": "CAMPAIGN"
}
EVIDENCE_RUN := {
  "campaign": EvidenceRef,
  "run_id": ContractId,
  "subject_type": "EVIDENCE_RUN"
}
EVIDENCE_RECORD := {
  "campaign": EvidenceRef,
  "oracle_id": OracleId,
  "run_id": ContractId,
  "subject_type": "EVIDENCE_RECORD"
}
DEPLOYMENT_EVIDENCE_ACQUISITION := {
  "acquisition_id": Id,
  "campaign": EvidenceRef,
  "oracle_id": OracleId,
  "run_id": ContractId,
  "subject_type": "DEPLOYMENT_EVIDENCE_ACQUISITION"
}
OPERATION_WORK_RESERVATION := {
  "plan": EvidenceRef,
  "reservation_ordinal": PositiveSafeInteger,
  "subject_type": "OPERATION_WORK_RESERVATION",
  "work_identity_digest": Digest
}
QUALIFICATION_RECEIPT := {
  "qualification_plan": EvidenceRef,
  "subject_type": "QUALIFICATION_RECEIPT"
}
DEPLOYMENT_ATTESTATION := {
  "admission_generation": SafeInteger,
  "subject_type": "DEPLOYMENT_ATTESTATION",
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}
EVIDENCE_DISPOSITION := {
  "disposition_id": Id,
  "subject_digest": Digest,
  "subject_type": "EVIDENCE_DISPOSITION"
}
SUPPORT_PROFILE_PROJECTION := {
  "support_profile": EvidenceRef,
  "subject_type": "SUPPORT_PROFILE_PROJECTION",
  "target_database_identity": EvidenceRef
}
```

`qualification_receipt_key_digest(ref(Q))` and
`deployment_attestation_key_digest(target, surface, generation)` denote only
the matching projections above. `EVIDENCE_START` and `EVIDENCE_COMPLETE` use
the same subject projection; `phase` remains part of the protected observation
slot key and distinguishes the two immutable observations.

`J.plan`, `.approval`, and `.authorization_receipt` name `OperationPlan/v1`,
`OperationApproval/v1`, and `OperationAuthorizationReceipt/v1` and must form
the exact linked plan-approval-receipt chain. Every rollback-preimage position
names `RollbackPreimageBinding/v1`; the body has `authority=NONE`, its
`ciphertext` names the exact `ProtectedRollbackCiphertext/v1` whose nested
artifact descriptor, digest, length, and protected PostgreSQL bytes agree, its
decryption procedure names
`ProcedureContract/v1` with `DECRYPTION`, and its target identity names
`EvidenceIdentity/v1` with `TARGET_DATABASE`. Compatibility manifest, approval, and
fence-binding positions retain the exact compatibility-family bodies and
grammars registered in the compatibility design. No other contract kind or
version is accepted in any of these positions.
`J.pre_stage_expiry_observation` and `P.pre_stage_expiry_observation` each name
`PreStageExpiryObservation/v1`; the first has `stage=J` and predecessor
`"NONE"`, and the second has `stage=P` and predecessor `digest(J)`. The
observation's plan, approval, authorization receipt, admission, clock, and
expiry equal the stage chain exactly.

The action-binding definitions below are reusable closed objects. The
`OperationPlan.action_binding` and `J.action_binding` values must be
byte-identical after successor canonicalization. No separate `action` or
content-payload, transformation, or preimage field can override them. The
plan's target, expected generation, cohorts, epoch, manifest, predecessor,
apply payload, and rollback preimage fields therefore bind the same exact
action-specific inputs that `J` later authenticates.

Every action binding's `grant`, `retry_limits`, `reconciliation_limits`, and
`budget_limits` fields name exact `OperationGrant/v1`,
`OperationRetryLimits/v1`, `OperationReconciliationLimits/v1`, and
`OperationBudgetLimits/v1` bodies. The grant action, operation, target,
surface, epoch, expected generation, and cohort digests equal the plan and
`J`; its validity equals the plan validity exactly. Positive retry counts are
ceilings on newly attempted work; exact replay of an already committed stable
identity consumes no additional attempt. Reconciliation counts and elapsed
duration are independently enforced ceilings. The row-count and elapsed
budgets cover the exact selected, preserved, and mutated projections and may
not be renewed by restart or replacement of a worker.
Every duration field in `OperationReconciliationLimits/v1` and
`OperationBudgetLimits/v1` is a strictly positive `UInt128String`; zero,
overflow, an implementation default, or an unbounded spelling is invalid. The
five attempt-duration fields are the exact full duration charged before the
corresponding work class starts, not estimates revised after execution.

Plan issuance atomically creates one durable protected
`OperationAccountingState` keyed only by the exact plan reference, with zero
counters and charges and `next_reservation_ordinal=1`. Every invocation then
crosses exactly two boundaries in order.

First, the protected interface performs one uncharged, side-effect-free
committed-result preflight. It constructs the complete
`OperationWorkRequest/v1` and `OperationWorkIdentity`, using the protected
checked-next attempt ordinal for new work or the previously returned exact
identity for a retry, and derives `work_identity_digest` and
`request_key_digest` as SHA-256 over their respective complete standalone
successor-canonical bytes including the LF. It reads only the immutable result
key `(plan, work_identity_digest)`. `COMMITTED_REPLAY` is valid only when
`committed_result` resolves the byte-identical request, reservation, start,
transaction identity, work identity, and typed result. The interface returns
that existing result and performs no reservation, charge, observation, clock
sample, dispatch, or write. `UNRESOLVED` requires
`committed_result="NONE"` and authorizes no work; it only permits the exact same
request bytes to enter the second boundary. A malformed request, changed body
under one digest, or result conflict fails closed without a preflight-result
body.

Second, only `UNRESOLVED` enters one protected reservation transaction. It
locks the accounting row, request body, and exact limit bodies; requires the
request attempt ordinal to equal the still-current checked-next ordinal;
recomputes both digests; checks every resulting counter and cumulative charge
with unbounded arithmetic and in-range encoded results; and either commits one
reservation plus the complete accounting advance or commits one separately
keyed refusal and no accounting change. A successful reservation copies the
exact request reference and work identity, and `work_class` is derived from
the identity union member. It commits before attempted work. A later abort,
timeout, lost acknowledgement, or caller rollback never refunds it.

When reservation cannot commit because the attempt, row, elapsed, or
reconciliation ceiling is exhausted; checked arithmetic cannot produce an
in-range value or deadline; the clock binding is invalid; another reservation
has consumed the ordinal; or the request conflicts, the same transaction may
insert exactly one `OperationWorkPreReservationRefusal/v1`. Its stable key is
`(plan, request_key_digest)`, its accounting-state digest is SHA-256 over the
complete locked `OperationAccountingState` projection including the LF, and
its evidence names the exact failed predicate. Exact bytes replay; changed
bytes conflict. This nonauthorizing record is not a `RecoveryObservation/v1`,
has no reservation key, consumes no charge or ordinal, starts no work, and
cannot satisfy or replace a stage result. Reservation-keyed observation and
terminal-result guarantees begin only after a reservation commits. Thus every
overflow, exhaustion, race, replay, and refusal has exactly one path: free
committed replay, uncharged pre-reservation refusal, or charged reserved work.

The reservation transaction also creates one protected
`OPERATION_WORK_RESERVE` observation whose closed subject is the reservation's
plan, ordinal, and work-identity digest. `reserved_at` names that observation;
its mode is `QUALIFIED_CLOCK`, its envelope equals the reservation's
`clock_envelope`, its envelope boot identity equals `boot_identity`, and its
monotonic upper bound equals `reserved_at_monotonic_upper_ns`. The envelope and
boot identity must equal the current live qualified-clock binding under the
same lock. For `J`, `P`, `R`, and `M`, they also equal the current
authority-bearing deployment attestation. For `V` and evidence-only
transaction resolution, ambiguity query, or reconciliation, they are the fresh
current clock and boot and need not equal an expired or fenced aggregate
attestation; that work may create evidence only and cannot revive admission,
publication authority, or a stage. The caller supplies none of these values.

Transaction-resolution and ambiguity-query work both use
`work_class=RECONCILIATION`; no unmetered resolution class exists.

For `J`, `P`, `R`, `M`, and `V`, the protected function increments only the
matching attempt counter and charges the full plan-bound finite transaction
duration to both the attempt's `charged_elapsed_ns` and the cumulative elapsed
counter. An `M` reservation also charges the exact predeclared selected,
preserved, and maximum mutated row counts; the other work classes charge zero
rows. A reconciliation reservation increments the reconciliation counter and
charges its full derived interval to the reconciliation and elapsed counters.
It derives that positive interval as the minimum of the plan's
`maximum_ambiguity_resolution_ns`, remaining reconciliation-duration budget,
and remaining elapsed budget after all prior charges. Its ambiguity deadline
is `checked_add(reserved_at_monotonic_upper_ns, charged_reconciliation_ns)`.
Equality with that deadline is late. Zero remaining duration, a counter or row
ceiling breach, subtraction underflow, addition overflow, or an out-of-range
named result follows the exact pre-reservation-refusal path above.

Every comparison with that deadline uses a fresh qualified sample under the
same exact clock envelope and boot identity. Envelope replacement, reboot,
clock uncertainty, or cross-boot comparison makes the old deadline
incomparable and rejects the start or resolution. Recovery may then fence or
perform only separately identified and charged reconciliation under the new
envelope; it cannot reuse or reinterpret the old reservation or its deadline.

The immutable reservation key is `(plan, work_identity_digest)`, and its
request must be the unique `OperationWorkRequest/v1` carrying those exact
values. The complete work identity, not a caller idempotency key, binds the
exact attempted stage, aggregate and predecessor; the exact `M` plus
verification-attempt ID; or the exact original attempt and typed
transaction-resolution, ambiguity-query, or reconciliation operation. A
repeated work-identity digest must resolve to byte-identical identity, request,
and reservation bytes; changed bytes conflict. A distinct stage attempt,
verification attempt, resolution, query, or reconciliation uses a distinct
server-derived attempt ordinal and identity and consumes a distinct
reservation. Reservation and attempt ordinals are contiguous and checked.
Restart, worker replacement, recovery, a new request ID, superseding evidence,
or a new aggregate that still names the same plan cannot lower a counter,
charge, or deadline.

`OperationInvocationContext` is closed. `FORWARD` requires
`recovery_mode="NONE"` and `recovery_request_id="NONE"`. `RECOVERY` requires a
non-`NONE` request ID and exactly one mode: a stage attempt uses
`ADVANCE_STAGE`, a verification attempt uses `VERIFY_STAGE`, a transaction
resolution uses `RESOLVE_TRANSACTION`, an ambiguity query uses
`QUERY_AMBIGUITY`, and reconciliation uses `RECONCILE_SUBJECT`. No reserved
identity represents exact replay; exact committed replay terminates at the
uncharged preflight. A changed mode or recovery request changes the
work-identity digest and cannot reuse another reservation or result key.

A `J` stage identity uses `PLANNED_REQUEST`; its operation and epoch equal the
resolved plan, its predecessor-stage digest is `NONE`, and its lineage
predecessor is the exact locked head digest or `GENESIS`. `P`, `R`, and `M` use
`COMMITTED_J` with the exact journal digest and name, respectively, the exact
`J`, `P`, or `R` predecessor digest plus the same lineage predecessor bound by
`J`. A verification identity names the exact `M` and
`verification_attempt_id`. Every transaction-resolution and ambiguity-query
identity resolves one exact `TransactionIdentity/v1`, and every reconciliation
identity resolves one exact `ReconciliationSubject/v1`;
`subject_identity_digest` is the referenced subject body's digest. The subject
variant must agree with `reconciliation_kind`: `CONCLUSIVE_NONCOMMIT` uses
`TRANSACTION`, fence-state uses `FENCE_STATE`, qualification-attempt uses
`PUBLICATION_QUALIFICATION_ATTEMPT`, and terminal-outcome uses
`TERMINAL_OUTCOME`. The publication-qualification subject resolves the exact
original `R` reservation, start, transaction identity, complete stage work
identity, digest, and committed-`J` aggregate; every field must form one
protected chain and the work identity must have literal `stage="R"`. Each
nested reference has the exact kind shown in its grammar: the original
reservation, start, and transaction identity form one chain; the active fence,
publication-qualification attempt, or terminal result is the complete
immutable body being reconciled.
The original work digest, stage, aggregate, target, recovery request, and
resolution kind must equal protected recovery state. A digest without its
body, a body selected by the resolver, or a cross-plan, cross-stage,
cross-predecessor, cross-lineage, cross-subject, or cross-attempt binding is
invalid.
For a crashed reconciliation, the next transaction-resolution,
ambiguity-query, and `TRANSACTION` subject all use literal
`stage=RECONCILIATION`, resolve that resolver's exact transaction identity,
and remain within the same finite reconciliation counters and duration.

Each reservation has one protected state slot, initially `RESERVED`. The only
start transition locks the reservation and accounting row, rederives and
compares the complete request, work identity, and digest, and atomically
inserts one `TransactionIdentity/v1`, the unique `OperationWorkStart/v1`
marker, and the `STARTED` state. The transaction identity copies the plan,
aggregate, target, complete work identity and digest, authenticated adapter
incarnation, derived stage, and exact transaction mode. Its server-generated
`transaction_identity_id` is unique; its stable key is
`(plan, work_identity_digest)`. The start's `transaction_identity` names those
exact bytes, while its reservation, work identity, and digest equal the
reservation byte for byte. The fresh start nonce and transaction identity are
server-authenticated and cannot be supplied on behalf of another session. The
winning transition durably binds the one dispatch and the one logical
PostgreSQL transaction subject before invocation. The invoked transaction and
every protected ambiguity or reconciliation query lock and compare that exact
body, not a process-local handle or reconstructed tuple. A second or concurrent
start, a different incarnation, a changed binding, or an attempt to return
`STARTED` to `RESERVED` is rejected before work. A crash after reservation or
after the start marker never makes either reusable. Recovery may exact-query
or perform separately reserved resolution, but it cannot reserve a replacement
attempt while the original slot remains `STARTED`.

The ordinary result transition locks the `STARTED` reservation, its exact
start and transaction identity, and the protected result key
`(plan, work_identity_digest)`. A `FORWARD` stage identity commits its exact
stage body; a `FORWARD` or `RECOVERY/VERIFY_STAGE` verification identity
commits its exact verification body. A `RECOVERY/ADVANCE_STAGE` identity
instead commits exactly one `RecoveryAdvancementObservation/v1` as the
reservation's sole result. That observation's `result_body` names the exact
newly committed stage body, and both bodies are inserted in the same
transaction; no second committed-result row may point directly to the stage.
When a transaction-resolution or ambiguity-query identity obtains a conclusive
answer that the original transaction committed, it commits, respectively, one
`OperationWorkTransactionResolutionOutcome/v1` or
`OperationWorkAmbiguityQueryOutcome/v1`. The outcome references the original
committed-result binding and its exact result; it never inserts, copies, or
rebinds the original stage or observation. A bounded inconclusive query commits
only its exact `RecoveryAmbiguityObservation/v1`. Other reconciliation
identities commit only the exact permitted `RecoveryObservation/v1`. A
`PUBLICATION_QUALIFICATION_ATTEMPT_RECONCILIATION` identity may commit
`RecoveryUnprovenObservation/v1` only after the original `R` reservation is
already `COMMITTED` with its exact `result_kind=CONCLUSIVE_NONCOMMIT` binding
from the sole conclusive-close transaction below. Its ordinary result
transition closes only the publication-qualification reconciliation
reservation and never supplies or changes the original result.

The transition inserts the selected result and one
`OperationWorkCommittedResult/v1` and changes the slot to `COMMITTED` in the
same transaction. The committed-result body repeats the plan, reservation,
start, transaction identity, complete work identity, and digest byte for byte;
`result_kind` selects the exact referenced body kind. Every recovery
observation repeats that same current transaction identity and resolves the
original transaction or reconciliation subject already fixed by its work
identity. A transaction-resolution outcome repeats its resolution reservation,
start, transaction identity, complete `TransactionResolutionWorkIdentity`, and
digest, while an ambiguity-query outcome repeats the analogous query chain.
In both bodies, the original reservation, start, transaction identity, work
digest, committed result, and referenced result must resolve to one exact
protected chain. The original committed result's `result` equals
`original_result`, and its plan, reservation, start, transaction identity,
work identity, and digest equal the outcome's original fields. One result key
admits one byte sequence and can never be reopened,
replaced, or attached to another reservation, request, transaction, or
attempt. Recovery of a newly committed stage follows the observation's
`result_body`; exact retry returns the observation through preflight and never
creates another recovery observation.

The transaction-resolution outcome has stable key `resolution_reservation`;
the ambiguity-query outcome has stable key `query_reservation`. Each key is
the same reservation named by its enclosing `OperationWorkCommittedResult/v1`.
The committed result uses, respectively,
`result_kind=TRANSACTION_RESOLUTION_OUTCOME` or
`result_kind=AMBIGUITY_QUERY_OUTCOME`, and its `result` names that exact
outcome. Exact bytes replay through preflight; changed outcome bytes, a
different original result, a mismatched query or resolution identity, or a
stage body under the resolver's result key conflicts. Thus discovery that the
original transaction committed closes the resolver's `STARTED` slot as
`COMMITTED` without duplicating the stage already committed with the original
transaction.

A conclusive-noncommit close is the sole exception to the rule that the
started invocation writes its own result. It requires a distinct, already
charged `ReconciliationWorkIdentity` with
`reconciliation_kind=CONCLUSIVE_NONCOMMIT`, its exact `TRANSACTION`
`ReconciliationSubject/v1`, its own reservation, start, and transaction
identity, and typed `FailureEvidence/v1` proving that the original transaction
cannot commit and has no committed result. One transaction locks both
`STARTED` slots, both transaction identities, the subject, and their result
keys; revalidates the original and resolution identities; inserts
one `OperationWorkConclusiveNoncommitResult/v1`; inserts the original
`OperationWorkCommittedResult/v1` with
`result_kind=CONCLUSIVE_NONCOMMIT`; changes the original slot to `COMMITTED`;
inserts the resolution reservation's `RecoveryAdvancementObservation/v1` with
`transition=CONCLUSIVE_NONCOMMIT_RECORDED` and `result_body` equal to that
terminal result; inserts the resolution's
`OperationWorkCommittedResult/v1` with `result_kind=RECOVERY_OBSERVATION`; and
changes the resolution slot to `COMMITTED`. All bodies and both slot changes
commit or abort together. The terminal result repeats both reservations,
starts, transaction identities, work identities, digests, subject, and
recovery request exactly. It records a terminal non-effect, never a stage
result or authority.

Only that committed close permits either a replacement attempt with a new
identity, ordinal, reservation, and full charge or, when no authorizing `R`
can still commit, a separately charged publication-qualification
reconciliation to reserve work that may record `UNPROVEN`. Before terminal
conditions, the close therefore preserves the ordinary replacement-`R` path;
it does not itself choose `UNPROVEN`. Exact retry of either closed identity
returns its byte-identical committed binding without charge. A crash while the
resolution itself is `STARTED` leaves both attempts unavailable; another
distinct charged reconciliation must first resolve and close the unresolved
resolution, subject to the same finite reconciliation counters and duration.
If that recursive transaction-resolution or ambiguity query discovers that
the resolver committed, it uses the typed original-committed outcome above;
if it proves noncommit, the conclusive-noncommit close applies. Every started
resolver therefore has one terminal committed-result mapping: its own typed
outcome or recovery observation, or the terminal noncommit binding written by
a later charged resolver. None of those mappings creates the original stage a
second time.

The mandatory preflight above is the only free path. Only an existing
byte-identical `OperationWorkCommittedResult/v1` and its exact referenced
result are a free replay. A conclusive-noncommit replay returns that terminal
non-effect and never dispatches the original operation. An existing
reservation, start marker, uncommitted or ambiguous attempt, caller retry,
reused verification-attempt ID, recovery observation without its
committed-result binding, or result under a different request, transaction, or
work identity is unresolved and cannot start work for free. A genuinely new
plan has its own separately approved limits and never inherits work authority
from the exhausted plan.

Before an `OperationPlan/v1` may be issued, its action binding's exact
`RollbackPreimageBinding/v1` body and every recursively referenced body must
already exist in the immutable protected candidate registry and pass the
complete target, surface, lineage key, ciphertext, restore-payload, conversion,
and decryption
predicates for that action. The constructor must also have stored the exact
ciphertext bytes in the protected PostgreSQL candidate byte row and verified
their digest and length. The plan issuer locks and rechecks that row, resolves
the stored bodies, and copies the exact preimage `EvidenceRef`; it cannot
accept caller-projected fields, an external-file locator, or a body or byte row
registered after plan insertion. Candidate registration and plan issuance
leave the binding and bytes nonauthorizing. For `SUCCESSOR_APPLY` rollback,
the reference and protected byte row are those already adopted by the
predecessor apply `J`; no new preimage body or ciphertext is published.

The operation authority chain has one deadline and strict issuance order. For
grant `G`, plan `O`, approval `A_O`, and authorization receipt `Z_O`:

```text
G = body(O.action_binding.grant)
A_O.plan = ref(O)
Z_O.plan = ref(O)
Z_O.approval = ref(A_O)

G.issued_at_unix_ns
    < O.created_at_unix_ns
    < A_O.issued_at_unix_ns
    < Z_O.issued_at_unix_ns
    < O.valid_until_unix_ns

G.valid_until_unix_ns
    = A_O.valid_until_unix_ns
    = Z_O.valid_until_unix_ns
    = O.valid_until_unix_ns
    = J.approval_expiry_unix_ns
    = R.approval_expiry_unix_ns
```

The grant, plan, approval, and receipt therefore have one shared,
nonextendable deadline; none has an independent expiry that can extend or
shorten it. The protected approval
stable key is `plan`. The protected authorization stable key is
`(plan, approval)`. Each exact retry returns byte-identical bytes, and any
changed body under the same key conflicts. Both boundaries authenticate the
complete referenced plan, including its exact action binding. They store the
exact current, unrevoked body; a content digest alone is not approval or
authorization.

Operation authority has one closed keyed lifecycle and no ambient issuer
power. The operation-authority owner exposes only these authenticated
`SECURITY DEFINER` transitions:

```text
ISSUE_OPERATION_GRANT(grant)
REVOKE_OPERATION_GRANT(grant_id, expected_current_grant, revocation)
ISSUE_OPERATION_PLAN(plan)
APPROVE_OPERATION_PLAN(plan, expected_current_approval, approval)
AUTHORIZE_OPERATION(plan, approval, expected_current_authorization, receipt)
REVOKE_OPERATION_AUTHORITY(
    plan,
    expected_current_approval,
    expected_current_authorization,
    revocation
)
READ_CURRENT_OPERATION_AUTHORITY(plan)
```

The grant slot is keyed by `grant_id`; the authority slot is keyed by the exact
plan reference. Issuance moves the latter only through `ABSENT -> PLAN_ISSUED
-> APPROVED -> AUTHORIZED`. Each transition authenticates the session
principal against the matching body principal, resolves every complete typed
body, checks the expected current references, and commits the immutable body
and current pointer atomically. The approval's `operator_principal`, the
receipt's `authorization_principal`, and the plan's
`plan_issuer_principal` cannot be supplied on behalf of another session.

Grant or authority revocation is a terminal compare-and-set transition. Its
body names the exact grant and, for authority revocation, the plan plus the
approval and authorization references present at `expected_state`; absent
later references are literal `"NONE"`. The revocation interface requires
byte-equality with the locked current slot, authenticates
`revoker_principal`, inserts the immutable revocation, and changes the slot to
`REVOKED` in one transaction. Exact replay returns that body. A stale expected
reference, different body, skipped state, later issuance, reinstatement, or
second revocation conflicts. Every `J`, `P`, `R`, and `M` transaction locks the
grant and authority slots and requires the exact plan, approval, and
authorization receipt still be current and unrevoked through commit. `J`, `P`,
and `R` enforce the shared grant/plan/approval/authorization deadline at their
specified samples. After a durable timely `R`, `M` verifies exact identities,
current selectors, and nonrevocation under those locks but does not compare the
current time with that shared deadline.

These authority-bearing successor evidence bodies also use the successor
canonical-byte contract, including its trailing LF:

```text
ControllerHostBinding := {
  "boot_configuration": EvidenceRef,
  "host_identity": EvidenceRef,
  "kind": "hindsight-postgresql-controller-host-binding",
  "operating_system_profile": EvidenceRef,
  "schema_version": 1
}

PostgresqlHostBinding := {
  "boot_configuration": EvidenceRef,
  "host_identity": EvidenceRef,
  "kind": "hindsight-postgresql-host-binding",
  "operating_system_profile": EvidenceRef,
  "postgresql_profile": EvidenceRef,
  "schema_version": 1,
  "storage_profile": EvidenceRef
}

PostgresqlEndpointBinding := {
  "address": Text,
  "endpoint_identity": EvidenceRef,
  "kind": "hindsight-postgresql-endpoint-binding",
  "port": SafeInteger | "NONE",
  "schema_version": 1,
  "target_database_identity": EvidenceRef,
  "transport": "UNIX_DOMAIN_SOCKET" | "TCP_LITERAL_LOOPBACK" |
               "TCP_REMOTE" | "MANAGED_SERVICE",
  "unix_socket_directory": PostgresqlUnixSocketDirectory | "NONE"
}

DeploymentTopologyBinding := {
  "controller_host": EvidenceRef,
  "kind": "hindsight-postgresql-deployment-topology-binding",
  "locality": "SAME_HOST_LOCAL" | "REMOTE" | "MANAGED",
  "network_path_identity": EvidenceRef | "NONE",
  "postgresql_endpoint": EvidenceRef,
  "postgresql_host": EvidenceRef,
  "schema_version": 1
}

SupportProfile := {
  "adapter_release_digest": Digest,
  "admission_controller_release_digest": Digest,
  "boot_configuration": EvidenceRef,
  "clock_profile": EvidenceRef,
  "closure_policy_limits": EvidenceRef,
  "cold_recovery_procedure": EvidenceRef,
  "controller_host": EvidenceRef,
  "deployment_topology": EvidenceRef,
  "failure_injector": EvidenceRef,
  "filesystem_profile": EvidenceRef,
  "hardware_profile": EvidenceRef,
  "kind": "hindsight-postgresql-support-profile",
  "migration_digest": Digest,
  "operating_system_profile": EvidenceRef,
  "postgresql_endpoint": EvidenceRef,
  "postgresql_host": EvidenceRef,
  "postgresql_profile": EvidenceRef,
  "profile_id": Id,
  "profile_name": ContractId,
  "protected_schema_digest": Digest,
  "release_digest": Digest,
  "schema_version": 1,
  "storage_profile": EvidenceRef,
  "unsupported_variations_digest": Digest,
  "virtualization_profile": EvidenceRef
}

ClockEnvelope := {
  "boot_identity": EvidenceRef,
  "clock_profile": EvidenceRef,
  "envelope_id": Id,
  "forward_rate_error_denominator": UInt128String,
  "forward_rate_error_numerator": UInt128String,
  "host_identity": EvidenceRef,
  "issued_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-clock-envelope",
  "monotonic_anchor_lower_ns": UInt128String,
  "monotonic_validity_deadline_lower_ns": UInt128String,
  "schema_version": 1,
  "synchronization_epoch": EvidenceRef,
  "wall_upper_at_anchor_unix_ns": UInt128String
}

QualificationPlanCell := {
  "acquisition_procedure": EvidenceRef,
  "case_matrix": EvidenceRef,
  "cell_id": ContractId,
  "claim_ids": sequence<ClaimId>,
  "conformance_prestate": EvidenceRef | "NONE",
  "condition": ContractId,
  "evidence_class": "EV-CLK" | "EV-PHY" | "EV-CAP",
  "independent_oracles": sequence<EvidenceRef>,
  "limits": EvidenceRef,
  "randomized_schedule": EvidenceRef | "NONE",
  "required_runs": PositiveSafeInteger,
  "tier": "RELEASE",
  "tool": EvidenceRef
}

QualificationPlan := {
  "abort_policy": EvidenceRef,
  "acceptance_thresholds": EvidenceRef,
  "campaign_id": Id,
  "cells": sequence<QualificationPlanCell>,
  "closure_policy_limits": EvidenceRef,
  "cold_recovery_procedure": EvidenceRef,
  "created_at_unix_ns": UInt128String,
  "environment_reset_procedure": EvidenceRef,
  "evidence_retention_policy": EvidenceRef,
  "failure_injector": EvidenceRef,
  "kind": "hindsight-postgresql-qualification-plan",
  "planned_runs": sequence<CampaignRunRequirement>,
  "release_digest": Digest,
  "schema_version": 1,
  "subject_revision": EvidenceRef,
  "support_profile": EvidenceRef
}

QualificationPlanAcceptance := {
  "acceptance_id": Id,
  "decision": "ACCEPT",
  "issued_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-qualification-plan-acceptance",
  "operator_principal": Text,
  "plan": EvidenceRef,
  "release_digest": Digest,
  "schema_version": 1,
  "support_profile": EvidenceRef,
  "valid_until_unix_ns": UInt128String
}

QualificationCellResult := {
  "cell_id": ContractId,
  "run_results": sequence<EvidenceRef>,
  "oracle_result": "PASS" | "FAIL"
}

QualificationClassResult := {
  "campaign_id": Id,
  "cell_results": sequence<QualificationCellResult>,
  "clock_epochs": sequence<EvidenceRef>,
  "completed_at_unix_ns": UInt128String,
  "evidence_class": "EV-CLK" | "EV-PHY" | "EV-CAP",
  "kind": "hindsight-postgresql-qualification-class-result",
  "plan": EvidenceRef,
  "release_digest": Digest,
  "result": "PASS" | "FAIL",
  "schema_version": 1,
  "started_at_unix_ns": UInt128String,
  "support_profile": EvidenceRef
}

QualificationReceipt := {
  "capability_result": EvidenceRef,
  "clock_result": EvidenceRef,
  "closure_policy_limits": EvidenceRef,
  "issued_at_unix_ns": UInt128String,
  "issuance_time_observation": EvidenceRef,
  "kind": "hindsight-postgresql-qualification-receipt",
  "physical_durability_result": EvidenceRef,
  "plan": EvidenceRef,
  "plan_acceptance": EvidenceRef,
  "qualification_id": Id,
  "release_digest": Digest,
  "result": "PASS",
  "schema_version": 1,
  "support_profile": EvidenceRef,
  "tier_results": sequence<EvidenceRef>,
  "valid_until_unix_ns": UInt128String
}

DeploymentAttestation := {
  "admission_generation": SafeInteger,
  "boot_identity": EvidenceRef,
  "clock_envelope": EvidenceRef,
  "closure_policy_limits": EvidenceRef,
  "controller_host": EvidenceRef,
  "deployment_admission_policy": EvidenceRef,
  "deployment_campaign": EvidenceRef,
  "deployment_topology": EvidenceRef,
  "deployment_tier_results": sequence<EvidenceRef>,
  "endpoint_identity": EvidenceRef,
  "health": "PASS",
  "host_identity": EvidenceRef,
  "installed_adapter_digest": Digest,
  "installed_admission_controller_digest": Digest,
  "installed_release_digest": Digest,
  "issued_at_unix_ns": UInt128String,
  "issuance_time_observation": EvidenceRef,
  "kind": "hindsight-postgresql-deployment-attestation",
  "lineage_key_digest": Digest,
  "migration_digest": Digest,
  "postgresql_endpoint": EvidenceRef,
  "postgresql_host": EvidenceRef,
  "postgresql_settings": EvidenceRef,
  "protected_schema_digest": Digest,
  "proposed_publication_epoch": SafeInteger,
  "qualification_plan": EvidenceRef,
  "qualification_plan_acceptance": EvidenceRef,
  "qualification_receipt": EvidenceRef,
  "role_grant_set": EvidenceRef,
  "schema_version": 1,
  "storage_identity": EvidenceRef,
  "support_profile": EvidenceRef,
  "target_database_identity": EvidenceRef,
  "target_generation": TargetGeneration,
  "target_surface_digest": Digest,
  "valid_until_unix_ns": UInt128String,
  "writer_inventory": EvidenceRef
}

CampaignOracleRequirement := {
  "claim_predicates": sequence<CanonicalClaimPredicateEvidenceRef>,
  "expected_projection": EvidenceRef,
  "oracle_contract": EvidenceRef,
  "oracle_id": OracleId
}

CampaignRunRequirement := {
  "acquisition_procedure": EvidenceRef,
  "cell_id": ContractId,
  "claim_definitions": sequence<CanonicalClaimDefinitionEvidenceRef>,
  "claim_ids": sequence<ClaimId>,
  "claim_predicates": sequence<CanonicalClaimPredicateEvidenceRef>,
  "conformance_prestate": EvidenceRef | "NONE",
  "evidence_class": "EV-DES" | "EV-REF" | "EV-VEC" | "EV-PG" |
                    "EV-FLT" | "EV-LEG" | "EV-ACL" | "EV-CLK" |
                    "EV-PHY" | "EV-CAP" | "EV-DEP",
  "limits": EvidenceRef,
  "oracles": sequence<CampaignOracleRequirement>,
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef | "NONE",
  "run_id": ContractId,
  "stimulus": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT",
  "tool": EvidenceRef
}

ClaimPredicateOracleRequirement := {
  "expected_fields": sequence<ProjectionField>,
  "oracle_id": OracleId
}

ClaimPredicateRun := {
  "evidence_class": "EV-DES" | "EV-REF" | "EV-VEC" | "EV-PG" |
                    "EV-FLT" | "EV-LEG" | "EV-ACL" | "EV-CLK" |
                    "EV-PHY" | "EV-CAP" | "EV-DEP",
  "oracle_requirements": sequence<ClaimPredicateOracleRequirement>,
  "run_id": ContractId
}

CanonicalClaimPredicate := {
  "claim_id": ClaimId,
  "kind": "hindsight-postgresql-canonical-claim-predicate",
  "pass_rule": "ALL_REQUIRED_RUNS_AND_ORACLE_FIELDS_PASS",
  "run_predicates": sequence<ClaimPredicateRun>,
  "schema_version": 1,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT"
}

CanonicalClaimPredicateEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-canonical-claim-predicate",
  "contract_version": 1
}

CanonicalClaimDefinition := {
  "claim_id": ClaimId,
  "kind": "hindsight-postgresql-canonical-claim-definition",
  "obligation": Text,
  "predicates": sequence<CanonicalClaimPredicateEvidenceRef>,
  "required_tiers": sequence<"DESIGN" | "IMPLEMENTATION" | "RELEASE" |
                             "DEPLOYMENT">,
  "schema_version": 1
}

CanonicalClaimDefinitionEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-canonical-claim-definition",
  "contract_version": 1
}

CanonicalClaimRegistry := {
  "claim_definitions": sequence<CanonicalClaimDefinitionEvidenceRef>,
  "claim_ids": sequence<ClaimId>,
  "claim_predicates": sequence<CanonicalClaimPredicateEvidenceRef>,
  "kind": "hindsight-postgresql-canonical-claim-registry",
  "planned_runs": sequence<CampaignRunRequirement>,
  "profile_scope_mode": "PROFILE_INDEPENDENT" | "EXACT_PROFILES",
  "schema_version": 1,
  "support_profiles": sequence<EvidenceRef>,
  "target_database_identities": sequence<EvidenceRef>,
  "target_scope_mode": "TARGET_INDEPENDENT" | "EXACT_TARGETS",
  "target_surface_digests": sequence<Digest>,
  "tier": "DESIGN" | "IMPLEMENTATION"
}

CanonicalDeploymentMatrix := {
  "claim_definitions": sequence<CanonicalClaimDefinitionEvidenceRef>,
  "claim_predicates": sequence<CanonicalClaimPredicateEvidenceRef>,
  "deployment_policy": EvidenceRef,
  "kind": "hindsight-postgresql-canonical-deployment-matrix",
  "planned_runs": sequence<CampaignRunRequirement>,
  "required_claim_ids": sequence<ClaimId>,
  "schema_version": 1,
  "support_profiles": sequence<EvidenceRef>,
  "target_database_identities": sequence<EvidenceRef>,
  "target_surface_digests": sequence<Digest>
}

AuthorityGateAbsentValue := {
  "presence": "ABSENT"
}

AuthorityGateActivationCapabilityValue := {
  "presence": "PRESENT",
  "value": Digest,
  "value_kind": "ACTIVATION_CAPABILITY_DIGEST"
}

AuthorityGateLineageHeadValue := {
  "presence": "PRESENT",
  "value": Digest,
  "value_kind": "LINEAGE_HEAD_DIGEST"
}

ClockEnvelopeEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-clock-envelope",
  "contract_version": 1
}

OperationPlanEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-operation-plan",
  "contract_version": 1
}

DeploymentAttestationEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-deployment-attestation",
  "contract_version": 1
}

DeploymentPolicyEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-deployment-admission-policy",
  "contract_version": 1
}

EvidenceTierResultEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-evidence-tier-result",
  "contract_version": 1
}

OriginFenceBindingEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-compatibility-origin-fence-manifest-binding",
  "contract_version": 1
}

ActiveFenceAdoptionEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-compatibility-active-fence-manifest-adoption",
  "contract_version": 1
}

LegacyFenceEvidenceRef := OriginFenceBindingEvidenceRef |
                          ActiveFenceAdoptionEvidenceRef

OperationAuthorizationReceiptEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-operation-authorization-receipt",
  "contract_version": 1
}

OperationGrantEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-operation-grant",
  "contract_version": 1
}

OperationWorkReservationEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-operation-work-reservation",
  "contract_version": 1
}

OperationWorkStartEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-operation-work-start",
  "contract_version": 1
}

OperationWorkCommittedResultEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-operation-work-committed-result",
  "contract_version": 1
}

OperationWorkConclusiveNoncommitResultEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-operation-work-conclusive-noncommit-result",
  "contract_version": 1
}

TransactionIdentityEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-transaction-identity",
  "contract_version": 1
}

QualificationReceiptEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-qualification-receipt",
  "contract_version": 1
}

RoleGrantSetEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-role-grant-set",
  "contract_version": 1
}

WriterInventoryEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-writer-inventory",
  "contract_version": 1
}

AuthorityGateClockEnvelopeValue := {
  "presence": "PRESENT",
  "value": ClockEnvelopeEvidenceRef,
  "value_kind": "CLOCK_ENVELOPE_REF"
}

AuthorityGateDeploymentAttestationValue := {
  "presence": "PRESENT",
  "value": DeploymentAttestationEvidenceRef,
  "value_kind": "DEPLOYMENT_ATTESTATION_REF"
}

AuthorityGateDeploymentPolicyValue := {
  "presence": "PRESENT",
  "value": DeploymentPolicyEvidenceRef,
  "value_kind": "DEPLOYMENT_POLICY_REF"
}

AuthorityGateEvidenceTierResultValue := {
  "presence": "PRESENT",
  "value": EvidenceTierResultEvidenceRef,
  "value_kind": "EVIDENCE_TIER_RESULT_REF"
}

AuthorityGateLegacyFenceValue := {
  "presence": "PRESENT",
  "value": LegacyFenceEvidenceRef,
  "value_kind": "LEGACY_FENCE_REF"
}

AuthorityGateOperationAuthorityValue := {
  "presence": "PRESENT",
  "value": OperationAuthorizationReceiptEvidenceRef,
  "value_kind": "OPERATION_AUTHORIZATION_RECEIPT_REF"
}

AuthorityGateOperationGrantValue := {
  "presence": "PRESENT",
  "value": OperationGrantEvidenceRef,
  "value_kind": "OPERATION_GRANT_REF"
}

AuthorityGateOperationWorkReservationValue := {
  "presence": "PRESENT",
  "value": OperationWorkReservationEvidenceRef,
  "value_kind": "OPERATION_WORK_RESERVATION_REF"
}

AuthorityGateOperationWorkStartValue := {
  "presence": "PRESENT",
  "value": OperationWorkStartEvidenceRef,
  "value_kind": "OPERATION_WORK_START_REF"
}

AuthorityGateOperationWorkCommittedResultValue := {
  "presence": "PRESENT",
  "value": OperationWorkCommittedResultEvidenceRef,
  "value_kind": "OPERATION_WORK_COMMITTED_RESULT_REF"
}

AuthorityGateQualificationReceiptValue := {
  "presence": "PRESENT",
  "value": QualificationReceiptEvidenceRef,
  "value_kind": "QUALIFICATION_RECEIPT_REF"
}

AuthorityGateRoleGrantSetValue := {
  "presence": "PRESENT",
  "value": RoleGrantSetEvidenceRef,
  "value_kind": "ROLE_GRANT_SET_REF"
}

AuthorityGateWriterInventoryValue := {
  "presence": "PRESENT",
  "value": WriterInventoryEvidenceRef,
  "value_kind": "WRITER_INVENTORY_REF"
}

AuthorityGateActiveEpochValue := {
  "presence": "PRESENT",
  "value": SafeInteger,
  "value_kind": "ACTIVE_EPOCH"
}

AuthorityGatePublicationEpochHighWaterValue := {
  "presence": "PRESENT",
  "value": SafeInteger,
  "value_kind": "PUBLICATION_EPOCH_HIGH_WATER"
}

AuthorityGateTargetGenerationValue := {
  "presence": "PRESENT",
  "value": SafeInteger,
  "value_kind": "TARGET_GENERATION"
}

AuthorityGateAccountingSlotValue := {
  "presence": "PRESENT",
  "value": OperationAccountingState,
  "value_kind": "OPERATION_ACCOUNTING_STATE"
}

AuthorityGateActivationProposalValue := {
  "deployment_attestation": DeploymentAttestationEvidenceRef,
  "manifest_body_digest": Digest,
  "presence": "PRESENT",
  "publication_epoch": SafeInteger,
  "value_kind": "ACTIVATION_PROPOSAL"
}

AuthorityGateReservedActivationValue := {
  "deployment_attestation": DeploymentAttestationEvidenceRef,
  "presence": "PRESENT",
  "publication_epoch": SafeInteger,
  "reservation_state": "RESERVED_FENCED",
  "value_kind": "RESERVED_ACTIVATION"
}

AuthorityGateTargetSurfaceSlotKey := {
  "key_kind": "TARGET_SURFACE",
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

AuthorityGatePublicationEpochSlotKey := {
  "key_kind": "PUBLICATION_EPOCH",
  "publication_epoch": SafeInteger,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

AuthorityGateClaimTierSlotKey := {
  "claim_id": ClaimId,
  "key_kind": "CLAIM_TIER",
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT"
}

AuthorityGatePlanSlotKey := {
  "key_kind": "PLAN",
  "plan": OperationPlanEvidenceRef
}

AuthorityGateOperationWorkSlotKey := {
  "key_kind": "OPERATION_WORK",
  "plan": OperationPlanEvidenceRef,
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest
}

AuthorityGateActiveEpochSlotKey := AuthorityGateTargetSurfaceSlotKey &
                                   {"slot_class": "ACTIVE_EPOCH"}
AuthorityGateActivationCapabilitySlotKey :=
  AuthorityGatePublicationEpochSlotKey &
  {"slot_class": "ACTIVATION_CAPABILITY"}
AuthorityGateActivationProposalSlotKey :=
  AuthorityGatePublicationEpochSlotKey &
  {"slot_class": "ACTIVATION_PROPOSAL"}
AuthorityGateClockEnvelopeSlotKey := AuthorityGateTargetSurfaceSlotKey &
                                     {"slot_class": "CLOCK_ENVELOPE"}
AuthorityGateDeploymentAttestationSlotKey :=
  AuthorityGatePublicationEpochSlotKey &
  {"slot_class": "DEPLOYMENT_ATTESTATION"}
AuthorityGateDeploymentPolicySlotKey := AuthorityGateTargetSurfaceSlotKey &
                                        {"slot_class": "DEPLOYMENT_POLICY"}
AuthorityGateEvidenceTierResultSlotKey := AuthorityGateClaimTierSlotKey &
                                          {"slot_class": "EVIDENCE_TIER_RESULT"}
AuthorityGateLegacyFenceSlotKey := AuthorityGateTargetSurfaceSlotKey &
                                   {"slot_class": "LEGACY_FENCE"}
AuthorityGateLineageHeadSlotKey := AuthorityGateTargetSurfaceSlotKey &
                                   {"slot_class": "LINEAGE_HEAD"}
AuthorityGateOperationAccountingSlotKey := AuthorityGatePlanSlotKey &
                                           {"slot_class": "OPERATION_ACCOUNTING"}
AuthorityGateOperationAuthoritySlotKey := AuthorityGatePlanSlotKey &
                                          {"slot_class": "OPERATION_AUTHORITY"}
AuthorityGateOperationGrantSlotKey := AuthorityGatePlanSlotKey &
                                      {"slot_class": "OPERATION_GRANT"}
AuthorityGateOperationWorkReservationSlotKey :=
  AuthorityGateOperationWorkSlotKey &
  {"slot_class": "OPERATION_WORK_RESERVATION"}
AuthorityGateOperationWorkStartSlotKey := AuthorityGateOperationWorkSlotKey &
                                          {"slot_class": "OPERATION_WORK_START"}
AuthorityGateOperationWorkCommittedResultSlotKey :=
  AuthorityGateOperationWorkSlotKey &
  {"slot_class": "OPERATION_WORK_COMMITTED_RESULT"}
AuthorityGatePublicationEpochHighWaterSlotKey :=
  AuthorityGateTargetSurfaceSlotKey &
  {"slot_class": "PUBLICATION_EPOCH_HIGH_WATER"}
AuthorityGateQualificationReceiptSlotKey :=
  AuthorityGateTargetSurfaceSlotKey &
  {"slot_class": "QUALIFICATION_RECEIPT"}
AuthorityGateReservedActivationSlotKey := AuthorityGateTargetSurfaceSlotKey &
                                          {"slot_class": "RESERVED_ACTIVATION"}
AuthorityGateRoleGrantSetSlotKey := AuthorityGateTargetSurfaceSlotKey &
                                    {"slot_class": "ROLE_GRANT_SET"}
AuthorityGateTargetGenerationSlotKey := AuthorityGateTargetSurfaceSlotKey &
                                        {"slot_class": "TARGET_GENERATION"}
AuthorityGateWriterInventorySlotKey := AuthorityGateTargetSurfaceSlotKey &
                                       {"slot_class": "WRITER_INVENTORY"}

AuthorityGateActiveEpochSlot := {
  "slot_class": "ACTIVE_EPOCH",
  "slot_key": AuthorityGateActiveEpochSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateActiveEpochValue
}

AuthorityGateActivationCapabilitySlot := {
  "slot_class": "ACTIVATION_CAPABILITY",
  "slot_key": AuthorityGateActivationCapabilitySlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateActivationCapabilityValue
}

AuthorityGateActivationProposalSlot := {
  "slot_class": "ACTIVATION_PROPOSAL",
  "slot_key": AuthorityGateActivationProposalSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateActivationProposalValue
}

AuthorityGateClockEnvelopeSlot := {
  "slot_class": "CLOCK_ENVELOPE",
  "slot_key": AuthorityGateClockEnvelopeSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateClockEnvelopeValue
}

AuthorityGateDeploymentAttestationSlot := {
  "slot_class": "DEPLOYMENT_ATTESTATION",
  "slot_key": AuthorityGateDeploymentAttestationSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateDeploymentAttestationValue
}

AuthorityGateDeploymentPolicySlot := {
  "slot_class": "DEPLOYMENT_POLICY",
  "slot_key": AuthorityGateDeploymentPolicySlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateDeploymentPolicyValue
}

AuthorityGateEvidenceTierResultSlot := {
  "slot_class": "EVIDENCE_TIER_RESULT",
  "slot_key": AuthorityGateEvidenceTierResultSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateEvidenceTierResultValue
}

AuthorityGateLegacyFenceSlot := {
  "slot_class": "LEGACY_FENCE",
  "slot_key": AuthorityGateLegacyFenceSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateLegacyFenceValue
}

AuthorityGateLineageHeadSlot := {
  "slot_class": "LINEAGE_HEAD",
  "slot_key": AuthorityGateLineageHeadSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateLineageHeadValue
}

AuthorityGateOperationAccountingSlot := {
  "slot_class": "OPERATION_ACCOUNTING",
  "slot_key": AuthorityGateOperationAccountingSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateAccountingSlotValue
}

AuthorityGateOperationAuthoritySlot := {
  "slot_class": "OPERATION_AUTHORITY",
  "slot_key": AuthorityGateOperationAuthoritySlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateOperationAuthorityValue
}

AuthorityGateOperationGrantSlot := {
  "slot_class": "OPERATION_GRANT",
  "slot_key": AuthorityGateOperationGrantSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateOperationGrantValue
}

AuthorityGateOperationWorkReservationSlot := {
  "slot_class": "OPERATION_WORK_RESERVATION",
  "slot_key": AuthorityGateOperationWorkReservationSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue |
           AuthorityGateOperationWorkReservationValue
}

AuthorityGateOperationWorkStartSlot := {
  "slot_class": "OPERATION_WORK_START",
  "slot_key": AuthorityGateOperationWorkStartSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateOperationWorkStartValue
}

AuthorityGateOperationWorkCommittedResultSlot := {
  "slot_class": "OPERATION_WORK_COMMITTED_RESULT",
  "slot_key": AuthorityGateOperationWorkCommittedResultSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue |
           AuthorityGateOperationWorkCommittedResultValue
}

AuthorityGatePublicationEpochHighWaterSlot := {
  "slot_class": "PUBLICATION_EPOCH_HIGH_WATER",
  "slot_key": AuthorityGatePublicationEpochHighWaterSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue |
           AuthorityGatePublicationEpochHighWaterValue
}

AuthorityGateQualificationReceiptSlot := {
  "slot_class": "QUALIFICATION_RECEIPT",
  "slot_key": AuthorityGateQualificationReceiptSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateQualificationReceiptValue
}

AuthorityGateReservedActivationSlot := {
  "slot_class": "RESERVED_ACTIVATION",
  "slot_key": AuthorityGateReservedActivationSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateReservedActivationValue
}

AuthorityGateRoleGrantSetSlot := {
  "slot_class": "ROLE_GRANT_SET",
  "slot_key": AuthorityGateRoleGrantSetSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateRoleGrantSetValue
}

AuthorityGateTargetGenerationSlot := {
  "slot_class": "TARGET_GENERATION",
  "slot_key": AuthorityGateTargetGenerationSlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateTargetGenerationValue
}

AuthorityGateWriterInventorySlot := {
  "slot_class": "WRITER_INVENTORY",
  "slot_key": AuthorityGateWriterInventorySlotKey,
  "slot_key_digest": Digest,
  "value": AuthorityGateAbsentValue | AuthorityGateWriterInventoryValue
}

AuthorityGateSeededRow := {
  "body": EvidenceRef,
  "relation": ContractId,
  "row_key_digest": Digest
}

AuthorityGateCurrentSlot := AuthorityGateActiveEpochSlot |
                            AuthorityGateActivationCapabilitySlot |
                            AuthorityGateActivationProposalSlot |
                            AuthorityGateClockEnvelopeSlot |
                            AuthorityGateDeploymentAttestationSlot |
                            AuthorityGateDeploymentPolicySlot |
                            AuthorityGateEvidenceTierResultSlot |
                            AuthorityGateLegacyFenceSlot |
                            AuthorityGateLineageHeadSlot |
                            AuthorityGateOperationAccountingSlot |
                            AuthorityGateOperationAuthoritySlot |
                            AuthorityGateOperationGrantSlot |
                            AuthorityGateOperationWorkReservationSlot |
                            AuthorityGateOperationWorkStartSlot |
                            AuthorityGateOperationWorkCommittedResultSlot |
                            AuthorityGatePublicationEpochHighWaterSlot |
                            AuthorityGateQualificationReceiptSlot |
                            AuthorityGateReservedActivationSlot |
                            AuthorityGateRoleGrantSetSlot |
                            AuthorityGateTargetGenerationSlot |
                            AuthorityGateWriterInventorySlot

AuthorityGateAbsenceMarker := {
  "relation": ContractId,
  "row_key_digest": Digest,
  "state": "ABSENT"
}

AuthorityGateLineageValue := {
  "lineage": LineageBinding,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

AuthorityGateClockValue := {
  "clock_envelope": EvidenceRef,
  "monotonic_sample_lower_ns": UInt128String,
  "monotonic_sample_upper_ns": UInt128String,
  "trusted_upper_bound_unix_ns": UInt128String,
  "value_id": ContractId
}

AuthorityGateCapabilityValue := {
  "capability_bytes_base64url": Text,
  "capability_digest": Digest,
  "session_witness_bytes_base64url": Text,
  "value_id": ContractId
}

AuthorityGateRevocationValue := {
  "current_state": "CURRENT" | "REVOKED",
  "revocation": EvidenceRef | "NONE",
  "subject": EvidenceRef
}

AuthorityGateAccountingValue := {
  "plan": EvidenceRef,
  "state": OperationAccountingState
}

AuthorityGateFixtureState := {
  "absence_markers": sequence<AuthorityGateAbsenceMarker>,
  "accounting_values": sequence<AuthorityGateAccountingValue>,
  "capability_values": sequence<AuthorityGateCapabilityValue>,
  "clock_values": sequence<AuthorityGateClockValue>,
  "current_slots": sequence<AuthorityGateCurrentSlot>,
  "kind": "hindsight-postgresql-authority-gate-fixture-state",
  "lineage_values": sequence<AuthorityGateLineageValue>,
  "revocation_values": sequence<AuthorityGateRevocationValue>,
  "schema_version": 1,
  "seeded_rows": sequence<AuthorityGateSeededRow>
}

AuthorityGateConformancePrestate := {
  "active_fence_binding": EvidenceRef | "NONE",
  "authority": "NONE",
  "cell_id": ContractId,
  "current_tier_results": sequence<EvidenceRef>,
  "deployment_attestation": EvidenceRef | "NONE",
  "deployment_policy": EvidenceRef | "NONE",
  "fixture_state": EvidenceRef,
  "gate": "J" | "P" | "R" | "M" | "V" | "COMBINED_ACTIVATION" |
          "QUALIFICATION_FINALIZER" | "DEPLOYMENT_FINALIZER" |
          "LEGACY_FENCE" | "OPERATION_RECONCILIATION",
  "isolation": "DISPOSABLE_CONFORMANCE_SCHEMA",
  "kind": "hindsight-postgresql-authority-gate-conformance-prestate",
  "operation_approval": EvidenceRef | "NONE",
  "operation_authorization_receipt": EvidenceRef | "NONE",
  "operation_plan": EvidenceRef | "NONE",
  "publication_epoch": SafeInteger | "NONE",
  "qualification_receipt": EvidenceRef | "NONE",
  "schema_version": 1,
  "support_profile": EvidenceRef,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

ClaimRegistryPlanBasis := {
  "basis_kind": "CLAIM_REGISTRY",
  "registry": EvidenceRef,
  "registry_digest": Digest
}

QualificationPlanBasis := {
  "basis_kind": "QUALIFICATION_PLAN",
  "plan": EvidenceRef,
  "plan_acceptance": EvidenceRef
}

HistoricalCorpusPlanBasis := {
  "basis_kind": "HISTORICAL_CORPUS_PLAN",
  "coverage_projection": EvidenceRef,
  "plan": EvidenceRef,
  "plan_acceptance": EvidenceRef
}

DeploymentPolicyPlanBasis := {
  "basis_kind": "DEPLOYMENT_ADMISSION_POLICY",
  "deployment_matrix": EvidenceRef,
  "policy": EvidenceRef
}

AtomicCampaignPlanBasis := ClaimRegistryPlanBasis | QualificationPlanBasis |
                           HistoricalCorpusPlanBasis |
                           DeploymentPolicyPlanBasis

CompositeCampaignPlanBasis := {
  "basis_kind": "COMPOSITE",
  "members": sequence<AtomicCampaignPlanBasis>
}

CampaignPlanBasis := AtomicCampaignPlanBasis | CompositeCampaignPlanBasis

EvidenceCampaignPlan := {
  "basis": CampaignPlanBasis,
  "campaign_id": Id,
  "claim_ids": sequence<ClaimId>,
  "created_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-evidence-campaign-plan",
  "plan_id": Id,
  "planned_runs": sequence<CampaignRunRequirement>,
  "schema_version": 1,
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT"
}

EvidenceCampaignPlanAcceptance := {
  "acceptance_id": Id,
  "decision": "ACCEPT",
  "issued_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-evidence-campaign-plan-acceptance",
  "operator_principal": Text,
  "plan": EvidenceRef,
  "schema_version": 1,
  "valid_until_unix_ns": UInt128String
}

EvidenceCampaign := {
  "campaign_id": Id,
  "campaign_plan": EvidenceRef,
  "campaign_plan_acceptance": EvidenceRef,
  "claim_ids": sequence<ClaimId>,
  "kind": "hindsight-postgresql-evidence-campaign",
  "planned_runs": sequence<CampaignRunRequirement>,
  "schema_version": 1,
  "start_time_observation": EvidenceRef,
  "started_at_unix_ns": UInt128String,
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT"
}

ProjectionField := {
  "name": FieldName,
  "value_kind": "BOOLEAN" | "SAFE_INTEGER" | "UINT128" | "DIGEST" |
                "ID" | "CONTRACT_ID" | "TEXT" | "ENUM_TOKEN" |
                "ENUM_TOKEN_OR_NONE" | "DIGEST_OR_NONE" |
                "EVIDENCE_REF" | "EVIDENCE_REF_OR_NONE" |
                "LEGACY_READER_OUTPUT_REF" |
                "TARGET_APPLY_PAYLOAD_REF_OR_NONE" |
                "RECOVERY_AGGREGATE_IDENTITY" | "TARGET_ALLOWED_DELTA" |
                "SAFE_INTEGER_SEQUENCE" | "DIGEST_SEQUENCE" |
                "CONTRACT_ID_SEQUENCE" | "TEXT_SEQUENCE" |
                "EVIDENCE_REF_SEQUENCE" | "NONE",
  "value": false | true | SafeInteger | UInt128String | Digest | Id |
           ContractId | Text | OracleEnumToken | EvidenceRef |
           TargetApplyPayloadEvidenceRef | RecoveryAggregateIdentity |
           TargetAllowedDelta |
           sequence<SafeInteger> |
           sequence<Digest> | sequence<ContractId> | sequence<Text> |
           sequence<EvidenceRef> | "NONE"
}

OracleFieldRequirement := {
  "name": FieldName,
  "value_kind": "BOOLEAN" | "SAFE_INTEGER" | "UINT128" | "DIGEST" |
                "ID" | "CONTRACT_ID" | "TEXT" | "ENUM_TOKEN" |
                "ENUM_TOKEN_OR_NONE" | "DIGEST_OR_NONE" |
                "EVIDENCE_REF" | "EVIDENCE_REF_OR_NONE" |
                "LEGACY_READER_OUTPUT_REF" |
                "TARGET_APPLY_PAYLOAD_REF_OR_NONE" |
                "RECOVERY_AGGREGATE_IDENTITY" | "TARGET_ALLOWED_DELTA" |
                "SAFE_INTEGER_SEQUENCE" | "DIGEST_SEQUENCE" |
                "CONTRACT_ID_SEQUENCE" | "TEXT_SEQUENCE" |
                "EVIDENCE_REF_SEQUENCE" | "NONE"
}

OracleClaimObligation := {
  "claim_definition": CanonicalClaimDefinitionEvidenceRef,
  "claim_id": ClaimId,
  "claim_predicates": sequence<CanonicalClaimPredicateEvidenceRef>,
  "failure_rule": "ANY_REQUIRED_FIELD_MISMATCH_FAILS",
  "pass_rule": "ALL_REQUIRED_FIELDS_AND_CLAIM_PREDICATES_PASS"
}

OracleDefinition := {
  "claim_obligations": sequence<OracleClaimObligation>,
  "definition_version": 1,
  "fields": sequence<OracleFieldRequirement>,
  "kind": "hindsight-postgresql-oracle-definition",
  "oracle_id": OracleId,
  "schema_version": 1
}

OracleDefinitionEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-oracle-definition",
  "contract_version": 1
}

CanonicalOracleRegistryEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-canonical-oracle-registry",
  "contract_version": 1
}

OracleContractEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-oracle-contract",
  "contract_version": 1
}

CanonicalOracleRegistry := {
  "definitions": sequence<OracleDefinitionEvidenceRef>,
  "kind": "hindsight-postgresql-canonical-oracle-registry",
  "registry_version": 1,
  "schema_version": 1
}

OracleContract := {
  "comparison": "EXACT_FIELD_EQUALITY",
  "definition": OracleDefinitionEvidenceRef,
  "independent_implementation": EvidenceRef,
  "kind": "hindsight-postgresql-oracle-contract",
  "oracle_id": OracleId,
  "oracle_registry": CanonicalOracleRegistryEvidenceRef,
  "schema_version": 1
}

OracleProjection := {
  "claim_predicates": sequence<CanonicalClaimPredicateEvidenceRef>,
  "fields": sequence<ProjectionField>,
  "kind": "hindsight-postgresql-oracle-projection",
  "oracle_contract": OracleContractEvidenceRef,
  "oracle_definition": OracleDefinitionEvidenceRef,
  "oracle_id": OracleId,
  "schema_version": 1
}

EvidenceRecord := {
  "acquisition_procedure": EvidenceRef,
  "campaign": EvidenceRef,
  "claim_definitions": sequence<CanonicalClaimDefinitionEvidenceRef>,
  "claim_ids": sequence<ClaimId>,
  "claim_predicates": sequence<CanonicalClaimPredicateEvidenceRef>,
  "completed_at_unix_ns": UInt128String,
  "completion_time_observation": EvidenceRef,
  "conformance_prestate": EvidenceRef | "NONE",
  "deployment_evidence_acquisition": EvidenceRef | "NONE",
  "evidence_class": "EV-DES" | "EV-REF" | "EV-VEC" | "EV-PG" |
                    "EV-FLT" | "EV-LEG" | "EV-ACL" | "EV-CLK" |
                    "EV-PHY" | "EV-CAP" | "EV-DEP",
  "expected_projection": EvidenceRef,
  "kind": "hindsight-postgresql-evidence-record",
  "limits": EvidenceRef,
  "observed_projection": EvidenceRef,
  "oracle_contract": EvidenceRef,
  "oracle_id": OracleId,
  "oracle_result": "PASS" | "FAIL",
  "real_artifact_binding": EvidenceRef | "NONE",
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef | "NONE",
  "record_id": Id,
  "run_id": ContractId,
  "schema_version": 1,
  "start_time_observation": EvidenceRef,
  "started_at_unix_ns": UInt128String,
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT",
  "tool": EvidenceRef
}

EvidenceRunFailure := {
  "campaign": EvidenceRef,
  "completed_at_unix_ns": UInt128String,
  "completion_time_observation": EvidenceRef,
  "conformance_prestate": EvidenceRef | "NONE",
  "deployment_evidence_acquisition": EvidenceRef | "NONE",
  "execution": "SKIPPED" | "ABORTED" | "SHORTENED_BUDGET" |
               "OVER_BUDGET" | "GENERATOR_FAILURE" |
               "UNREPRODUCIBLE_FAILURE" | "UNEXPLAINED",
  "expected_projection": EvidenceRef,
  "kind": "hindsight-postgresql-evidence-run-failure",
  "observed_projection": EvidenceRef | "NO_LIVE_PROJECTION",
  "oracle_contract": EvidenceRef,
  "oracle_id": "OR-EVID",
  "real_artifact_binding": EvidenceRef | "NONE",
  "result": "CONFIRMED",
  "run_id": ContractId,
  "schema_version": 1,
  "start_time_observation": EvidenceRef,
  "started_at_unix_ns": UInt128String
}

QualificationClockEpoch := {
  "boot_identity": EvidenceRef,
  "boot_ordinal": PositiveSafeInteger,
  "clock_envelope": EvidenceRef,
  "kind": "hindsight-postgresql-qualification-clock-epoch",
  "plan": EvidenceRef,
  "predecessor_clock_epoch": EvidenceRef | "NONE",
  "schema_version": 1
}

EvidencePhaseClockBinding := {
  "boot_identity": EvidenceRef,
  "clock_envelope": EvidenceRef,
  "clock_epoch": EvidenceRef | "NONE",
  "phase": "DEPLOYMENT_EVIDENCE_ACQUIRE" | "EVIDENCE_START" |
           "EVIDENCE_COMPLETE",
  "subject_key_digest": Digest,
  "time_observation": EvidenceRef
}

EvidenceRecordRegistrationInput := {
  "deployment_evidence_acquisition": EvidenceRef | "NONE",
  "expected_projection": EvidenceRef,
  "observed_projection": EvidenceRef,
  "oracle_contract": EvidenceRef,
  "oracle_id": OracleId,
  "record_id": Id,
  "start_time_observation": EvidenceRef
}

EvidenceRunFailureRegistrationInput := {
  "deployment_evidence_acquisition": EvidenceRef | "NONE",
  "execution": "SKIPPED" | "ABORTED" | "SHORTENED_BUDGET" |
               "OVER_BUDGET" | "GENERATOR_FAILURE" |
               "UNREPRODUCIBLE_FAILURE" | "UNEXPLAINED",
  "expected_projection": EvidenceRef,
  "observed_projection": EvidenceRef | "NO_LIVE_PROJECTION",
  "oracle_contract": EvidenceRef
}

EvidenceRunRegistrationInput := {
  "campaign": EvidenceRef,
  "execution": "EXECUTED" | "SKIPPED" | "ABORTED" |
               "SHORTENED_BUDGET" | "OVER_BUDGET" |
               "GENERATOR_FAILURE" | "UNREPRODUCIBLE_FAILURE" |
               "UNEXPLAINED",
  "failure": EvidenceRunFailureRegistrationInput | "NONE",
  "records": sequence<EvidenceRecordRegistrationInput>,
  "retained_artifacts": sequence<EvidenceRef>,
  "run_id": ContractId,
  "start_time_observation": EvidenceRef
}

EvidenceRunResult := {
  "campaign": EvidenceRef,
  "claim_definitions": sequence<CanonicalClaimDefinitionEvidenceRef>,
  "claim_ids": sequence<ClaimId>,
  "claim_predicates": sequence<CanonicalClaimPredicateEvidenceRef>,
  "clock_bindings": sequence<EvidencePhaseClockBinding>,
  "completed_at_unix_ns": UInt128String,
  "completion_time_observation": EvidenceRef,
  "conformance_prestate": EvidenceRef | "NONE",
  "evidence_class": "EV-DES" | "EV-REF" | "EV-VEC" | "EV-PG" |
                    "EV-FLT" | "EV-LEG" | "EV-ACL" | "EV-CLK" |
                    "EV-PHY" | "EV-CAP" | "EV-DEP",
  "evidence_records": sequence<EvidenceRef>,
  "execution": "EXECUTED" | "SKIPPED" | "ABORTED" |
               "SHORTENED_BUDGET" | "OVER_BUDGET" | "GENERATOR_FAILURE" |
               "UNREPRODUCIBLE_FAILURE" | "UNEXPLAINED",
  "failure_evidence": EvidenceRef | "NONE",
  "kind": "hindsight-postgresql-evidence-run-result",
  "oracle_result": "PASS" | "FAIL",
  "real_artifact_binding": EvidenceRef | "NONE",
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef | "NONE",
  "run_id": ContractId,
  "schema_version": 1,
  "start_time_observation": EvidenceRef,
  "started_at_unix_ns": UInt128String,
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT"
}

EvidenceInvalidityFinding := {
  "evidence": EvidenceRef,
  "expected_projection": EvidenceRef,
  "finding_code": "ACQUISITION_PROCEDURE_INVALID" |
                  "ORACLE_IMPLEMENTATION_NOT_INDEPENDENT" |
                  "REQUIRED_INPUT_INVALID" | "TOOL_IDENTITY_INVALID" |
                  "LIMITS_MISREPORTED" | "RETAINED_EVIDENCE_CORRUPT",
  "kind": "hindsight-postgresql-evidence-invalidity-finding",
  "observed_projection": EvidenceRef,
  "oracle_contract": EvidenceRef,
  "oracle_id": "OR-ID" | "OR-EVID" | "OR-ACL",
  "result": "CONFIRMED",
  "schema_version": 1
}

EvidenceTierState := {
  "campaign": EvidenceRef | "NONE",
  "claim_definition": CanonicalClaimDefinitionEvidenceRef | "NONE",
  "claim_id": ClaimId,
  "claim_predicate": CanonicalClaimPredicateEvidenceRef | "NONE",
  "invalidations": sequence<EvidenceRef>,
  "predecessor_result": EvidenceRef | "NONE",
  "prerequisite_results": sequence<EvidenceRef>,
  "run_results": sequence<EvidenceRef>,
  "selection_disposition": EvidenceRef | "NONE",
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT"
}

EvidenceTierResult := {
  "campaign": EvidenceRef | "NONE",
  "claim_definition": CanonicalClaimDefinitionEvidenceRef | "NONE",
  "claim_id": ClaimId,
  "claim_predicate": CanonicalClaimPredicateEvidenceRef | "NONE",
  "evidence_state_digest": Digest,
  "invalidations": sequence<EvidenceRef>,
  "kind": "hindsight-postgresql-evidence-tier-result",
  "predecessor_result": EvidenceRef | "NONE",
  "prerequisite_results": sequence<EvidenceRef>,
  "result": "NOT_REQUIRED" | "OPEN" | "PREREQUISITE_BLOCKED" |
            "PASS" | "FAIL" | "STALE",
  "run_results": sequence<EvidenceRef>,
  "schema_version": 1,
  "selection_disposition": EvidenceRef | "NONE",
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT"
}

HistoricalFixturePlanCell := {
  "acquisition_procedure": EvidenceRef,
  "cell_id": ContractId,
  "claim_ids": sequence<ClaimId>,
  "expected_projection": EvidenceRef,
  "fixture": EvidenceRef,
  "fixture_id": ContractId,
  "limits": EvidenceRef,
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef,
  "successor_projection_contract": EvidenceRef
}

HistoricalGeneratorPlanCell := {
  "acquisition_procedure": EvidenceRef,
  "case_budget": PositiveSafeInteger,
  "cell_id": ContractId,
  "claim_ids": sequence<ClaimId>,
  "execution_limit_ns": UInt128String,
  "expected_result_oracle": EvidenceRef,
  "generator": EvidenceRef,
  "limits": EvidenceRef,
  "max_bytes": PositiveSafeInteger,
  "max_depth": PositiveSafeInteger,
  "max_edges": PositiveSafeInteger,
  "max_nodes": PositiveSafeInteger,
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef,
  "seeds": sequence<UInt128String>,
  "shrink_budget": PositiveSafeInteger,
  "successor_projection_contract": EvidenceRef
}

HistoricalRealEvidencePlanCell := {
  "acquisition_procedure": EvidenceRef,
  "artifact_mode": "CONTROLLED_PRIVATE" | "SANITIZED_REAL",
  "cell_id": ContractId,
  "claim_ids": sequence<ClaimId>,
  "expected_projections": sequence<EvidenceRef>,
  "limits": EvidenceRef,
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef,
  "real_artifact_bindings": sequence<EvidenceRef>,
  "required_categories": sequence<ContractId>,
  "stimuli": sequence<EvidenceRef>,
  "successor_projection_contract": EvidenceRef
}

HistoricalReaderRegistryBinding := {
  "artifact_kind": CompatibilityToken | "NONE",
  "artifact_or_reference_plan_variant": CompatibilityToken | "NONE",
  "artifact_schema_version": PositiveSafeInteger,
  "authenticated_dependency_role": CompatibilityToken | "NONE",
  "protocol_family": "hindsight-private-file-operation-recovery",
  "protocol_version": 1,
  "reader_contract_id": CompatibilityContractId,
  "reader_registry_member_digest": Digest,
  "reference_plan_schema_version": PositiveSafeInteger | "NONE",
  "source_revision": GitObjectId,
  "wire_canonicalization_contract": CompatibilityContractId
}

HistoricalReaderExecutionBinding := {
  "implementation": EvidenceRef,
  "implementation_source_revision": GitObjectId,
  "input_contract": EvidenceRef,
  "invocation_contract": EvidenceRef,
  "kind": "hindsight-postgresql-historical-reader-execution-binding",
  "output_contract": EvidenceRef,
  "reader": HistoricalReaderRegistryBinding,
  "reader_contract_id": CompatibilityContractId,
  "reader_tool": EvidenceRef,
  "reader_tool_id": ContractId,
  "schema_version": 1,
  "wire_canonicalization_contract": CompatibilityContractId
}

HistoricalReaderExecutionBindingEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-historical-reader-execution-binding",
  "contract_version": 1
}

FrozenReaderRegistryEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-compatibility-frozen-reader-registry",
  "contract_version": 1
}

HistoricalArtifactCoverage := {
  "coverage_source": EvidenceRef,
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef
}

HistoricalBoundaryCoverage := {
  "boundary": "MINIMUM" | "MAXIMUM" | "BELOW_MINIMUM" |
              "ABOVE_MAXIMUM" | "EMPTY" | "DUPLICATE" |
              "REORDERED" | "MALFORMED",
  "field_path": Text,
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef,
  "source_cell_id": ContractId
}

HistoricalGeneratorCoverage := {
  "cell_id": ContractId,
  "generator": EvidenceRef,
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef,
  "required_category": ContractId
}

HistoricalSeedCoverage := {
  "cell_id": ContractId,
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef,
  "seed": UInt128String
}

HistoricalBudgetCoverage := {
  "case_budget": PositiveSafeInteger,
  "cell_id": ContractId,
  "execution_limit_ns": UInt128String,
  "max_bytes": PositiveSafeInteger,
  "max_depth": PositiveSafeInteger,
  "max_edges": PositiveSafeInteger,
  "max_nodes": PositiveSafeInteger,
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef
}

HistoricalShrinkCoverage := {
  "cell_id": ContractId,
  "reader_execution": HistoricalReaderExecutionBindingEvidenceRef,
  "retention": "SEED_AND_MINIMIZED_CASE",
  "shrink_budget": PositiveSafeInteger
}

HistoricalPolicyCoverage := {
  "policy": EvidenceRef,
  "policy_class": "PRIVATE_OR_SANITIZED_ARTIFACT" | "PUBLIC_PROJECTION"
}

HistoricalRequiredCategoryCoverage := {
  "required_category": ContractId,
  "source_cell_ids": sequence<ContractId>
}

HistoricalCorpusCoverageProjection := {
  "artifact_variants": sequence<HistoricalArtifactCoverage>,
  "boundaries": sequence<HistoricalBoundaryCoverage>,
  "budgets": sequence<HistoricalBudgetCoverage>,
  "generators": sequence<HistoricalGeneratorCoverage>,
  "kind": "hindsight-postgresql-historical-corpus-coverage-projection",
  "policies": sequence<HistoricalPolicyCoverage>,
  "reader_executions": sequence<HistoricalReaderExecutionBindingEvidenceRef>,
  "required_categories": sequence<HistoricalRequiredCategoryCoverage>,
  "schema_version": 1,
  "seeds": sequence<HistoricalSeedCoverage>,
  "shrinks": sequence<HistoricalShrinkCoverage>
}

HistoricalCorpusPlan := {
  "corpus_id": Id,
  "coverage_projection": EvidenceRef,
  "created_at_unix_ns": UInt128String,
  "expected_result_oracle": EvidenceRef,
  "fixture_cells": sequence<HistoricalFixturePlanCell>,
  "generator_cells": sequence<HistoricalGeneratorPlanCell>,
  "historical_registry_digest": Digest,
  "historical_registry_vector_digest": Digest,
  "kind": "hindsight-postgresql-historical-corpus-plan",
  "private_or_sanitized_artifact_policy": EvidenceRef,
  "public_projection_policy": EvidenceRef,
  "reader_executions": sequence<HistoricalReaderExecutionBindingEvidenceRef>,
  "reader_registry": sequence<HistoricalReaderRegistryBinding>,
  "reader_registry_body": FrozenReaderRegistryEvidenceRef,
  "real_evidence_cells": sequence<HistoricalRealEvidencePlanCell>,
  "schema_version": 1
}

HistoricalCorpusPlanAcceptance := {
  "acceptance_id": Id,
  "coverage_projection": EvidenceRef,
  "decision": "ACCEPT",
  "issued_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-historical-corpus-plan-acceptance",
  "operator_principal": Text,
  "plan": EvidenceRef,
  "schema_version": 1,
  "valid_until_unix_ns": UInt128String
}

DeploymentFailure := {
  "evidence": EvidenceRef,
  "failure_code": "UNKNOWN_CONTRACT" | "DEPLOYMENT_POLICY_MISMATCH" |
                  "DEPLOYMENT_POLICY_NONCURRENT" |
                  "DEPLOYMENT_POLICY_REVOKED" |
                  "DEPLOYMENT_POLICY_EXPIRED" |
                  "DEPLOYMENT_PARTITION_INVALID" |
                  "DEPLOYMENT_EVIDENCE_OPEN" |
                  "DEPLOYMENT_EVIDENCE_FAIL" |
                  "DEPLOYMENT_EVIDENCE_STALE" |
                  "DEPLOYMENT_EVIDENCE_SUPERSEDED" |
                  "DEPLOYMENT_EVIDENCE_INVALIDATED" |
                  "DEPLOYMENT_EVIDENCE_ACQUISITION_INVALID" |
                  "INVALID_QUALIFICATION_RECEIPT" |
                  "EXPIRED_QUALIFICATION_RECEIPT" |
                  "QUALIFICATION_RESULT_NOT_PASS" | "PROFILE_MISMATCH" |
                  "QUALIFICATION_EVIDENCE_INVALIDATED" |
                  "QUALIFICATION_EVIDENCE_STALE" |
                  "QUALIFICATION_CAMPAIGN_SUPERSEDED" |
                  "PLAN_MISMATCH" | "PLAN_ACCEPTANCE_MISMATCH" |
                  "RELEASE_MISMATCH" | "CLOCK_EVIDENCE_STALE" |
                  "STORAGE_EVIDENCE_STALE" |
                  "ROLE_GRANT_SET_MISMATCH" |
                  "ROLE_INVENTORY_INCOMPLETE" |
                  "WRITER_INVENTORY_MISMATCH" |
                  "BOOT_CONFIGURATION_DRIFT" | "BOOT_IDENTITY_CHANGED" |
                  "CONTROLLER_HOST_MISMATCH" |
                  "POSTGRESQL_HOST_MISMATCH" |
                  "ENDPOINT_IDENTITY_CHANGED" | "ENDPOINT_DRIFT" |
                  "ENDPOINT_TOPOLOGY_MISMATCH" |
                  "CLOSURE_POLICY_MISMATCH" |
                  "CLOSURE_GUARD_UNSUPPORTED" |
                  "REMOTE_POSTGRESQL_UNSUPPORTED" |
                  "MANAGED_POSTGRESQL_UNSUPPORTED" |
                  "FENCE_RESULT_INVALID" |
                  "PROFILE_UNQUALIFIED" | "HEALTH_CHECK_FAILED" |
                  "ATTESTATION_CONSTRUCTION_INVALID",
  "oracle_id": OracleId
}

FailedDeploymentResult := {
  "authority": "NONE",
  "candidate_projection": EvidenceRef,
  "campaign": EvidenceRef,
  "deployment_attempt_id": Id,
  "failures": sequence<DeploymentFailure>,
  "kind": "hindsight-postgresql-failed-deployment-result",
  "qualification_plan": EvidenceRef | "NONE",
  "qualification_plan_acceptance": EvidenceRef | "NONE",
  "qualification_receipt": EvidenceRef | "NONE",
  "result": "FAIL",
  "schema_version": 1,
  "subject_revision": EvidenceRef,
  "support_profile": EvidenceRef | "NONE"
}
```

`sequence<T>` is a JSON array. Plan cells are strictly ascending by ASCII
`cell_id`, and duplicate IDs are invalid. Every `claim_ids` sequence is
nonempty, duplicate-free, and strictly ascending by ASCII claim ID. Each
same-body `claim_definitions` and `claim_predicates` sequence has the same
cardinality and order as `claim_ids`; member `i` resolves the canonical
definition for claim `i` and that definition's exact predicate for the body's
tier. Every `CampaignOracleRequirement.claim_predicates` is the duplicate-free
claim-registry-order subsequence whose canonical predicate contains that
run-and-oracle obligation. It cannot carry a predicate for another claim,
tier, run, oracle, expected projection, or definition. Each
`RandomizedSchedule.seeds` sequence is nonempty, duplicate-free, and strictly
increasing by the numeric UInt128 value; its array position is its seed ordinal.
Each cell's `independent_oracles` is the nonempty exact oracle-contract sequence
derived from the claim registry for that cell's class and claims, in `OracleId`
registry order. It cannot omit `OR-FENCE` or `OR-ACL` when the registry requires
either one. Each case's `expected_projections` has the same cardinality and
order; member `i` names `OracleProjection/v1` under oracle contract `i`.

`CanonicalClaimRegistry/v1` and `CanonicalDeploymentMatrix/v1` are the two
closed sources that replace an implementation-defined registry or deployment
matrix. Each body is successor-canonicalized with its LF, and its digest is
SHA-256 over exactly those bytes. A claim-registry basis names the exact typed
registry reference and requires `registry_digest` to equal that body digest. A
deployment basis names both the exact current policy and the exact matrix whose
`deployment_policy` equals that policy. The protected plan validator resolves
the body and independently expands its complete `planned_runs`; a table in this
document, code constant, caller-projected list, or digest without the matching
typed body cannot supply the expansion.

The claim-registry body has one literal `DESIGN` or `IMPLEMENTATION` tier. Its
claim sequence is the complete ASCII-ordered required claim set for that tier;
`claim_definitions` and `claim_predicates` have the same cardinality and order.
Each definition
resolves one immutable obligation, the complete required-tier sequence in
`DESIGN`, `IMPLEMENTATION`, `RELEASE`, `DEPLOYMENT` order with inapplicable
tiers omitted, and exactly one predicate per required tier in that order. The
registry's predicate at the same position must be that selected predicate; an
implementation cannot choose another predicate from the definition or
construct one at expansion time. That predicate's
`run_predicates` is the registry-order subsequence of all and only planned runs
that name the claim. Each run predicate copies the run ID and evidence class
and contains every one of that run's oracle requirements for the claim in
canonical oracle-registry order. Each oracle requirement resolves the exact
oracle ID and embeds that claim's complete expected field sequence. Those
tagged values are the canonical claim-predicate preimage; no
implementation-supplied expression,
field subset, expected value, or Boolean can replace it. The literal pass rule
requires every listed run to execute and every listed oracle field to match.
Every registry run has that tier and a nonempty claim subset.

With `PROFILE_INDEPENDENT`, `support_profiles` is empty; with
`EXACT_PROFILES`, it is nonempty and ordered by canonical reference bytes. With
`TARGET_INDEPENDENT`, both target sequences are empty; with `EXACT_TARGETS`,
both are nonempty and ordered by canonical identity-reference bytes and
target-surface digest. The deployment matrix copies the policy's complete
ordered required claims, their canonical definitions and `DEPLOYMENT`
predicates, support profiles, target identities, and target surfaces, and
every run has literal `DEPLOYMENT`. Each deployment stimulus prebinds exactly
one admitted profile/target/surface tuple. The matrix runs sort by profile
reference, target reference, target-surface digest, ASCII `cell_id`, and ASCII
`run_id`; a claim-registry body sorts by ASCII `run_id`. Within every run, the
fields have the displayed `CampaignRunRequirement` types, `claim_ids` is ASCII
ascending, the definition and predicate sequences align one for one, and
`oracles` is in registry `OracleId` order. These rules make the two plan
families independently and exactly expandable. A missing body, changed
obligation, predicate, run, oracle, definition, expected projection, order, or
digest rejects the registry or matrix before campaign acceptance.

Every `CampaignRunRequirement.reader_execution` is literal `"NONE"` unless
`evidence_class=EV-LEG`. An `EV-LEG` run carries the one exact
`HistoricalReaderExecutionBinding/v1` selected by its corpus-plan cell. The
run's `tool` equals that binding's `reader_tool`; no run can name the member
from one binding and execute the tool from another.

The protected qualification-plan validator expands every cell before accepting
the plan. Every cell has literal `tier=RELEASE`. It visits cells in plan order
and cases in case-matrix order. The one-based `case_ordinal` is the case's
position in that closed matrix. A cell whose `randomized_schedule` is `"NONE"`
contributes one run per case. Its derived `QualificationRunStimulus/v1` copies
the cell ID, case ID, case ordinal, case stimulus, and one-based cell run
ordinal and sets `seed_ordinal`, `seed_uint128`, and `allocation_ordinal` to
literal `"NONE"`.

A scheduled cell visits the schedule's seeds in numeric order, records each
one-based seed position, and contributes exactly `allocation` runs for each
`(case, seed)` pair in ascending one-based allocation order. Its derived
stimulus copies the same closed case fields and run ordinal and records that
exact seed position, UInt128 seed spelling, and allocation ordinal. The run ID
is `cell_id || "-run-" || run_ordinal` using the shortest decimal ordinal. Each
run copies the cell's claims, their exact canonical definitions and `RELEASE`
predicates, class, literal `RELEASE` tier, tool, acquisition procedure, limits,
and exact `conformance_prestate`; sets `reader_execution` to literal `"NONE"`;
sets `stimulus` to the
exact typed reference for that
derived stimulus body; and carries the case's complete ordered oracle
requirements. The stimulus body is materialized and stored before plan
acceptance, and its typed reference is therefore byte-derivable from only the
cell, case matrix, schedule, and ordinal.

`required_runs` must equal the computed cardinality: case count without a
schedule, or `case_count * seed_count * allocation` under checked unbounded
arithmetic with a required `PositiveSafeInteger` result. It is a redundant
check, not an allocation choice.

`QualificationPlan.planned_runs` is exactly the concatenation of those expanded
runs, in that order. The validator independently recomputes the sequence from
the cells, case bodies, schedules, oracle contracts, and projections and rejects
an omitted, extra, duplicate, reordered, or changed member; a wrong cardinality,
run ID, tier, case ordinal, seed position, seed value, allocation ordinal, or
derived stimulus body; a conformance-prestate mismatch; an oracle-list or
projection mismatch; or missing
`OR-FENCE` or `OR-ACL` coverage. No later campaign may infer or expand a run that is absent
from these accepted bytes. A class result contains exactly the plan cells for
its `evidence_class`, in plan order, with no omission or extra result. Each cell
result's `run_results` contains exactly the `EvidenceRunResult/v1` references
for that cell's ordered `planned_runs` subsequence and has
`oracle_result=PASS` exactly when every referenced run result is `PASS`. An
absent or duplicate terminal run result prevents class-result and receipt
creation; the tier evaluator still derives `FAIL` for that incomplete campaign.
A present skipped, unexplained, aborted,
shortened-budget, over-budget, generator-failed, or unreproducible run result
makes the cell and class `FAIL`. An expected negative test has
`oracle_result=PASS` when the
observed refusal matches its oracle; it is not a failed result that needs
disposition. A class result is `PASS` exactly when every cell result is
`PASS`. `started_at_unix_ns <= completed_at_unix_ns` is required. A run result
copies its exact `(campaign, run_id)` start and completion observations and
timestamp values; it cannot derive another interval from caller-authored
record fields.

The plan's `support_profile` must name
`hindsight-postgresql-support-profile/v1`. Its cells bind the complete
deterministic case matrix, positive run allocation, exact tool, acquisition
procedure, limits, complete ordered independent-oracle list, and any randomized
schedule; its `planned_runs` binds the complete ordered expansion.
`randomized_schedule="NONE"` means that the cell is deterministic only; it
does not permit omission of a randomized allocation required below. A plan
acceptance must name
`hindsight-postgresql-qualification-plan/v1` and the same profile, release,
and campaign. The protected plan-acceptance boundary must authenticate the exact
acceptance body, plan bytes, decision, principal, issue time, and validity
bound before storing the immutable protected acceptance record. That protected
record, not the content digest alone, proves authentication. The acceptance is
valid only when `issued_at_unix_ns < valid_until_unix_ns`, and the plan must
have been created before issuance. The protected stable key is `plan`; exactly
one authenticated acceptance body may occupy it, an exact retry is
byte-identical, and any changed body conflicts.

A qualification receipt's `clock_result`, `physical_durability_result`, and
`capability_result` must each name
`hindsight-postgresql-qualification-class-result/v1` and must have,
respectively, `evidence_class=EV-CLK`, `EV-PHY`, and `EV-CAP`. Its `plan`,
`plan_acceptance`, and `support_profile` must name the exact v1 kinds above.
All references must carry compatible bindings. For a body `X`, define
`ref(X)` as the exact `EvidenceRef` whose body digest is `digest(X)`, contract
kind is `X.kind`, and contract version is `X.schema_version`. For support
profile `S`, plan `Q`, acceptance `A`, class results `C_CLK`, `C_PHY`, and
`C_CAP`, the complete current-result partitions `T_DESIGN`,
`T_IMPLEMENTATION`, and `T_RELEASE`, and receipt `Q_R`, these exact predicates
apply:

```text
body(S.controller_host).operating_system_profile =
    S.operating_system_profile
body(S.postgresql_host).operating_system_profile =
    S.operating_system_profile
body(S.postgresql_host).postgresql_profile = S.postgresql_profile
body(S.postgresql_host).storage_profile = S.storage_profile
body(S.controller_host).boot_configuration =
    body(S.boot_configuration).configuration
body(S.postgresql_host).boot_configuration =
    body(S.boot_configuration).configuration
body(S.deployment_topology).controller_host = S.controller_host
body(S.deployment_topology).postgresql_host = S.postgresql_host
body(S.deployment_topology).postgresql_endpoint = S.postgresql_endpoint

Q.support_profile = ref(S)
Q.release_digest = S.release_digest
Q.subject_revision = current_subject("RELEASE")
body(Q.subject_revision).release_digest = Q.release_digest
Q.closure_policy_limits = S.closure_policy_limits
A.plan = ref(Q)
A.support_profile = ref(S)
A.release_digest = Q.release_digest

for (C, class) in {(C_CLK, "EV-CLK"), (C_PHY, "EV-PHY"), (C_CAP, "EV-CAP")}:
    C.evidence_class = class
    C.plan = ref(Q)
    C.campaign_id = Q.campaign_id
    C.support_profile = ref(S)
    C.release_digest = Q.release_digest

Q_R.clock_result = ref(C_CLK)
Q_R.physical_durability_result = ref(C_PHY)
Q_R.capability_result = ref(C_CAP)
Q_R.plan = ref(Q)
Q_R.plan_acceptance = ref(A)
Q_R.support_profile = ref(S)
Q_R.release_digest = Q.release_digest
Q_R.closure_policy_limits = Q.closure_policy_limits
Q_R.result = "PASS"

[T.claim_id for T in T_DESIGN] = [
    "JAC-ARC-01", "JAC-CAN-01"
]
[T.claim_id for T in T_IMPLEMENTATION] = [
    "JAC-ACL-01", "JAC-AMB-01", "JAC-CAN-01", "JAC-CAP-01",
    "JAC-CLK-01", "JAC-CLO-01", "JAC-CUT-01", "JAC-EFX-01",
    "JAC-EVL-01", "JAC-FEN-01", "JAC-ID-01", "JAC-LEG-01",
    "JAC-LIN-01", "JAC-ORD-01", "JAC-PG-01", "JAC-PRS-01",
    "JAC-RBK-01", "JAC-RST-01", "JAC-TIM-01", "JAC-VER-01"
]
[T.claim_id for T in T_RELEASE] = [
    "JAC-CAP-01", "JAC-CLK-01", "JAC-CLO-01", "JAC-DUR-01",
    "JAC-EFX-01", "JAC-PRS-01"
]
T_REQUIRED = concat(T_DESIGN, T_IMPLEMENTATION, T_RELEASE)
Q_R.tier_results = [ref(T) for T in T_REQUIRED]

for (tier, partition) in [
    ("DESIGN", T_DESIGN),
    ("IMPLEMENTATION", T_IMPLEMENTATION),
    ("RELEASE", T_RELEASE)
]:
    for T in partition:
        T.tier = tier
        T.subject_revision = current_subject(tier)
        T.result = "PASS"
        protected_current_tier_result(T.claim_id, tier) = ref(T)
        for P in T.prerequisite_results:
            P = protected_current_tier_result(
                body(P).claim_id,
                body(P).tier
            )
            body(P).result = "PASS"

current_subject("RELEASE") = Q.subject_revision
```

The acceptance must authenticate that plan, and every class result must cover
its exact complete plan-cell partition. The three tier partitions contain
every registry-required
design, implementation, and release pair exactly once, contain no other pair,
and order tiers as `DESIGN`, `IMPLEMENTATION`, then `RELEASE`, with ASCII claim
order inside each tier. Duplicate `(tier, claim_id)` pairs are invalid. Receipt
finalization locks every protected current-result pointer and every
prerequisite-result pointer reachable from it, requires exact reference
equality and `PASS` for every member of all three complete
partitions, and holds those locks through receipt insertion. It also requires
all three class results to be `PASS` and the plan acceptance to cover every
class interval. A failed class or prerequisite tier produces its exact failing
result but no qualification receipt.

For each class result `C`, plan `Q`, plan acceptance `A`, and aggregate receipt
`Q_R`, these exact time predicates apply:

```text
Q.created_at_unix_ns < A.issued_at_unix_ns
A.issued_at_unix_ns < C.started_at_unix_ns
C.started_at_unix_ns <= C.completed_at_unix_ns
C.completed_at_unix_ns < A.valid_until_unix_ns

C.started_at_unix_ns = min(
    run.started_at_unix_ns for run in all_runs(C)
)
C.completed_at_unix_ns = max(
    run.completed_at_unix_ns for run in all_runs(C)
)

Q_R.qualification_id = Q.campaign_id
max_class_completion_unix_ns = max(
    C_CLK.completed_at_unix_ns,
    C_PHY.completed_at_unix_ns,
    C_CAP.completed_at_unix_ns
)
body(Q_R.issuance_time_observation).phase =
    "QUALIFICATION_RECEIPT_ISSUE"
body(Q_R.issuance_time_observation).mode = "QUALIFIED_CLOCK"
body(Q_R.issuance_time_observation).subject_key_digest =
    qualification_receipt_key_digest(ref(Q))
Q_R.issued_at_unix_ns =
    body(Q_R.issuance_time_observation).trusted_upper_bound_unix_ns
max_class_completion_unix_ns <= Q_R.issued_at_unix_ns
Q_R.valid_until_unix_ns = checked_add(
    Q_R.issued_at_unix_ns,
    body(Q.acceptance_thresholds).qualification_receipt_validity_ns
)
```

The inequalities must hold independently for the clock, physical-durability,
and capability class results. The accepted threshold duration is positive;
overflow or an out-of-range named result prevents receipt creation. Recorded
timestamps are binding metadata for campaign admission and ordering; they do
not replace any class oracle. The receipt finalizer derives the fresh protected
qualified-clock observation and final canonical receipt in one transaction.
The caller supplies neither its issue time nor a future value. An observation
later than the fresh conservative bound, under another subject key, or under a
replaced or invalid clock prevents receipt creation.

Qualification outputs are nonrenewing deterministic projections. A class
result's stable key is `(plan, evidence_class)`, and its cell sequence, result,
campaign, profile, release, and interval derive only from that plan and its
complete referenced run results. A receipt's stable key is `plan`; its body
derives only from the exact plan, the plan's one authenticated acceptance, the
three class results, the complete locked tier partitions, and the accepted
threshold duration plus the first committed issuance observation. Exact
retries therefore return byte-identical bodies and
digests regardless of retry time. A different body under either key conflicts.
The finalizer never reads an unqualified wall time into either body and never
issues a later validity interval from unchanged evidence. Renewed qualification requires
a new campaign identity in a new qualification plan, a separately
authenticated acceptance of that plan, a new accepted universal campaign plan,
and newly executed evidence.

One dedicated qualification-finalizer owner is the sole issuer of
`QualificationClassResult/v1` and `QualificationReceipt/v1`. It is a distinct
`NOLOGIN`, `NOINHERIT` database owner, cannot be assumed by any login, and owns
two narrow `SECURITY DEFINER` interfaces. The class-finalization interface
accepts only exact plan, plan-acceptance, evidence-class, and
`EvidenceRunResult/v1` references; resolves their complete canonical bytes and
nested `EvidenceRecord/v1` references; recomputes every cell, run, oracle
result, profile, release, campaign, and time predicate; and inserts the one
canonical class result. The receipt-finalization
interface accepts only the exact plan, acceptance, profile, three protected
class-result references, and complete current design-, implementation-, and
release-tier result sequence; locks and recomputes their complete ordered
partitions, protected current-result and prerequisite pointers, `PASS` results,
and equalities;
and holds those locks while inserting the one canonical aggregate receipt.
Exact retries return the same body and digest; a changed input under the same
class or qualification identity conflicts.

One nonruntime qualification-submitter login has `EXECUTE` on those two
interfaces and no direct relation privilege. It can request finalization only
after the evidence registrar has accepted the referenced campaign results; it
cannot choose a stored verdict because the finalizer resolves the protected
records and recomputes the complete result. No other login has `EXECUTE`.

The qualification-finalizer owner cannot create or accept a plan, run a test,
author or revoke a deployment attestation or clock envelope, create an
activation proposal, activate or fence an epoch, install a witness, advance a
publication stage, mutate a target, or verify `M`. The admission owner cannot
call either qualification-finalization interface and cannot write a class
result or receipt directly. Implementation evidence proves that only the
qualification submitter can invoke the two finalization interfaces and includes
`EV-ACL` denial cells for admission, activation, continuity-client,
publication, mutation, verification, closure, fence-adapter, ordinary runtime,
and qualification-runner principals against both interfaces. Reciprocal cells
deny every admission, activation, publication, mutation, verification,
closure, fence, and direct relation interface to the submitter and finalizer.
Admission therefore cannot self-qualify a release or deployment.

`QualificationPlanAcceptance.valid_until_unix_ns` bounds campaign execution:
every class must complete strictly before it. After successful finalization,
the acceptance remains an immutable historical binding and need not remain
unexpired. The qualification receipt must be unexpired when deployment
admission consumes it; both the receipt and deployment attestation must remain
unexpired when stage admission consumes that attestation.

The deployment-policy reference must name the exact protected current
`hindsight-postgresql-deployment-admission-policy/v1` for the target database
and surface. The policy's target, surface, support-profile, release, and
required deployment-claim sets must contain the attestation's exact values,
and its issue time must fall within the policy interval. Only the authenticated
policy-administration boundary may compare-and-set the protected slot keyed by
that exact target database and surface. Its call carries the key, the expected
current reference or `"NONE"`, and the replacement reference or `"NONE"`.
Replacement or revocation never edits an old body; it
makes every attestation naming the old reference noncurrent immediately and
fences its admission generation for that slot. It also marks the displaced
`(slot, reference)` pair retired: the immutable body remains queryable for
audit and may remain current in another covered slot, but it cannot be
installed in this slot again. The admission author cannot select, create,
replace, revoke, or reinstate that policy.

Policy registration requires nonempty `allowed_release_digests`,
`allowed_support_profiles`, `required_deployment_claim_ids`,
`target_database_identities`, and `target_surface_digests` sequences;
`attestation_validity_ns > 0`;
`maximum_deployment_evidence_age_ns > 0`; and
`effective_from_unix_ns < valid_until_unix_ns`. The registrar applies the exact
collection-order rule above and rejects duplicates, aliases, omissions, extras,
reordering, and unauthenticated or wrongly typed nested references.

The support-profile reference in a deployment attestation names the exact
current `hindsight-postgresql-support-profile/v1`; the clock-envelope reference
names the exact current, unexpired `hindsight-postgresql-clock-envelope/v1` for
that profile, host, boot, and synchronization epoch. Its qualification plan,
plan acceptance, and receipt name the exact v1 bodies above. They have
byte-identical profile and release bindings, and the receipt is `PASS` and
unexpired. The attestation's installed release, adapter, admission controller,
migration, and protected-schema identities equal the support profile and
receipt; its issue time follows receipt issuance; and its validity outlives
neither the policy nor receipt. The clock envelope must also remain current and
within its monotonic validity bound when sampled. An old, aliased, unknown, or
merely schema-compatible kind or version is noncurrent and cannot be consumed.
The protected admission finalizer takes a fresh qualified-clock sample while
those records are locked, derives the conservative upper bound, constructs the
`ProtectedTimeObservation/v1` with
`phase=DEPLOYMENT_ATTESTATION_ISSUE`, and finalizes `D` in the same transaction.
The caller supplies neither `issued_at_unix_ns` nor an observation body.

The attestation's `controller_host`, `postgresql_host`,
`postgresql_endpoint`, and `deployment_topology` references equal the support
profile's four stable references byte for byte and equal the protected live
deployment bindings at issuance. Its `host_identity`, `endpoint_identity`, and
`storage_identity` projections equal the identities inside those exact bodies;
its per-boot `boot_identity` instead equals the current live projection and
issuance clock envelope. It cannot select another topology or copy a boot
identity from the stable profile. For
`macos-local-postgresql-v1`, issuance requires the exact
`SAME_HOST_LOCAL` and absolute Unix-domain-socket predicates above. The
endpoint has `transport=UNIX_DOMAIN_SOCKET`, `port="NONE"`, and no TCP copy. A
remote or managed topology, distinct controller and database host, endpoint
alias, proxy, hostname, transport change, socket-path change, port change, or
target mismatch emits the exact failed-deployment result and no attestation.

For deployment attestation `D`, policy `P_D`, deployment matrix `M_D`,
deployment campaign `C_D`, and qualification receipt `Q_R`, exact reference
equality is mandatory:

```text
D.deployment_admission_policy = ref(P_D)
protected_current_deployment_policy(
    D.target_database_identity,
    D.target_surface_digest
) = ref(P_D)

D.qualification_receipt = ref(Q_R)
D.qualification_plan = Q_R.plan
D.qualification_plan_acceptance = Q_R.plan_acceptance
D.support_profile = Q_R.support_profile
D.closure_policy_limits = Q_R.closure_policy_limits
D.closure_policy_limits = body(D.support_profile).closure_policy_limits
D.controller_host = body(D.support_profile).controller_host
D.postgresql_host = body(D.support_profile).postgresql_host
D.postgresql_endpoint = body(D.support_profile).postgresql_endpoint
D.deployment_topology = body(D.support_profile).deployment_topology
D.host_identity = body(D.controller_host).host_identity
D.host_identity = body(D.postgresql_host).host_identity
D.boot_identity = body(D.clock_envelope).boot_identity
D.boot_identity = body(current_live_projection(D)).boot_identity
body(current_live_projection(D)).boot_environment_configuration =
    body(body(D.support_profile).boot_configuration).configuration
D.endpoint_identity = body(D.postgresql_endpoint).endpoint_identity
D.storage_identity = body(body(D.support_profile).storage_profile).identity
D.role_grant_set = ref(RG)
D.writer_inventory = ref(WI)
body(WI).role_grant_set = ref(RG)
body(RG).target_database_identity = D.target_database_identity
body(WI).target_database_identity = D.target_database_identity
body(RG).target_surface_digest = D.target_surface_digest
body(WI).target_surface_digest = D.target_surface_digest
protected_profiled_role_grant_set(D) = ref(RG)
protected_profiled_writer_inventory(D) = ref(WI)
admission_finalizer_recomputed_role_grant_set(D) = ref(RG)
admission_finalizer_recomputed_writer_inventory(D) = ref(WI)
D.lineage_key_digest = sha256_lf_canonical(CanonicalLineageKeyBody {
    "protocol_family": "hindsight-postgresql-publication",
    "protocol_version": 1,
    "target_database_identity": D.target_database_identity,
    "target_surface_digest": D.target_surface_digest
})
D.proposed_publication_epoch =
    protected_reserved_epoch(D.target_database_identity,
                             D.target_surface_digest).publication_epoch
D.admission_generation = D.proposed_publication_epoch
D.installed_release_digest = Q_R.release_digest
Q_R.result = "PASS"
body(D.issuance_time_observation).phase =
    "DEPLOYMENT_ATTESTATION_ISSUE"
body(D.issuance_time_observation).mode = "QUALIFIED_CLOCK"
body(D.issuance_time_observation).clock_envelope = D.clock_envelope
body(D.issuance_time_observation).subject_key_digest =
    deployment_attestation_key_digest(
        D.target_database_identity,
        D.target_surface_digest,
        D.admission_generation
    )
D.issued_at_unix_ns =
    body(D.issuance_time_observation).trusted_upper_bound_unix_ns
Q_R.issued_at_unix_ns <= D.issued_at_unix_ns
P_D.effective_from_unix_ns <= D.issued_at_unix_ns
D.issued_at_unix_ns < P_D.valid_until_unix_ns
D.valid_until_unix_ns = min(
    P_D.valid_until_unix_ns,
    Q_R.valid_until_unix_ns,
    checked_add(D.issued_at_unix_ns, P_D.attestation_validity_ns)
)

for A_D in all_deciding_deployment_acquisitions(C_D):
    E_D = deployment_record_for(A_D.campaign, A_D.run_id, A_D.oracle_id)
    E_D.deployment_evidence_acquisition = ref(A_D)
    A_D.observed_projection = E_D.observed_projection
    A_D.acquisition_procedure = E_D.acquisition_procedure
    O_A = body(A_D.acquired_at)
    O_I = body(D.issuance_time_observation)
    O_A.phase = "DEPLOYMENT_EVIDENCE_ACQUIRE"
    O_A.mode = "QUALIFIED_CLOCK"
    O_A.clock_envelope = D.clock_envelope = O_I.clock_envelope
    body(O_A.clock_envelope).boot_identity = D.boot_identity
    O_A.monotonic_sample_lower_ns <= O_I.monotonic_sample_upper_ns
    acquisition_age_upper_ns(A_D) = checked_add(
        O_I.monotonic_sample_upper_ns - O_A.monotonic_sample_lower_ns,
        ceil_mul_div(
            O_I.monotonic_sample_upper_ns - O_A.monotonic_sample_lower_ns,
            body(D.clock_envelope).forward_rate_error_numerator,
            body(D.clock_envelope).forward_rate_error_denominator
        )
    )

deployment_evidence_age_upper_ns = max(
    acquisition_age_upper_ns(A_D)
    for A_D in all_deciding_deployment_acquisitions(C_D)
)
deployment_evidence_age_upper_ns < P_D.maximum_deployment_evidence_age_ns

D.deployment_campaign = ref(C_D)
body(C_D.campaign_plan).basis = {
    "basis_kind": "DEPLOYMENT_ADMISSION_POLICY",
    "deployment_matrix": ref(M_D),
    "policy": ref(P_D)
}
M_D.deployment_policy = ref(P_D)
C_D.planned_runs = M_D.planned_runs
[T.claim_id for T in T_DEPLOYMENT] = [
    "JAC-ACL-01", "JAC-CAP-01", "JAC-CLK-01",
    "JAC-CLO-01", "JAC-CUT-01", "JAC-DUR-01", "JAC-PG-01"
]
D.deployment_tier_results = [ref(T) for T in T_DEPLOYMENT]

for T in T_DEPLOYMENT:
    T.tier = "DEPLOYMENT"
    T.campaign = ref(C_D)
    T.subject_revision = current_subject("DEPLOYMENT")
    T.result = "PASS"
    protected_current_tier_result(T.claim_id, "DEPLOYMENT") = ref(T)
```

The deployment partition contains every registry-required deployment pair
exactly once in ASCII claim order and no other pair. The policy's
`required_deployment_claim_ids` equals that same sequence. Deployment campaign
records and their authenticated plan bind that exact policy and describe the
immutable pre-attestation target and controller observations; they are
nonauthorizing evidence and do not require an attestation as their own input.
This ordering avoids a self-verdict: the evaluator first derives the complete
deployment `PASS` partition, then the admission finalizer may consume it.

`all_deciding_deployment_acquisitions` is the nonempty complete sequence of one
exact `DeploymentEvidenceAcquisition/v1` for every observed projection in
every deciding deployment record, in planned-run and oracle order. The
registrar requires each record's acquisition reference and exact projection,
procedure, campaign, run, and oracle equalities but creates only the later
registration start and completion observations. It cannot create, replace, or
reissue an acquisition observation. Run completion, result registration, tier
aggregation, review, signing, retry, and attestation finalization retain the
same acquisition references and never refresh evidence age.

The admission finalizer recomputes the conservative elapsed upper bound above
for every acquisition and uses the maximum, which is the age of the oldest
deciding live projection. A missing, extra, duplicate, reordered, unresolved,
wrong-kind, cross-run, wrong-procedure, or projection-mismatched acquisition;
a different clock envelope, boot identity, or synchronization epoch; a missing
lower bound; negative subtraction; overflow; or age greater than or equal to
the policy maximum is stale and emits no attestation. Equality is late. No
caller can supply an acquisition time or age, and no cross-boot arithmetic is
admitted without a separately qualified conservative bridge.

The protected deployment profiler and admission finalizer independently
enumerate the complete live role/grant and writer graphs and independently
successor-canonicalize `RG` and `WI`. Enumeration covers every database role
and security-relevant attribute; recursive membership with `INHERIT`, `SET`,
and `ADMIN` options; `PUBLIC`; ownership; database, schema, relation, column,
sequence, routine, type, language, large-object, extension, foreign-wrapper,
and foreign-server ACL or ownership paths; default privileges by definer,
schema, object class, grantee, privilege, and grant option; invoker- and
definer-rights routine, trigger, rule, extension, and dynamic-SQL paths that
can reach the target; and every service, login role, direct-SQL, scheduled,
background, prepared, replication, or adapter path that can mutate the target
or invoke mutation-capable code. Catalog and service-registry locks serialize
that enumeration with finalization. Any unenumerated, unclassifiable,
unresolved, unattributed, duplicated, extra, OID/name-conflicting, inherited,
`PUBLIC`, owner, default-privilege, function-mediated, or service path, or any
profiler/finalizer byte mismatch, emits no attestation.

Attestation finalization is the only reservation operation. It locks the
target-surface epoch high-water mark, current-active pointer, current
attestation slot, exact current-reserved-activation selector, and fixed
legacy-fence slot and requires the reserved selector to be `"NONE"`. It
allocates the next unused epoch, constructs
`D.proposed_publication_epoch` from that value, sets
`D.admission_generation` to that same checked-next server-derived value, and
inserts the immutable row in
state `RESERVED_FENCED`, inserts the final attestation, installs `ref(D)` as
current, and sets the selector to that exact row in one transaction. If the
legacy-fence slot is unoccupied, the row records `FRESH`. If it is occupied,
the finalizer must use the compatibility contract's later-epoch
`ADOPT_ACTIVE_FENCE` branch under the same locks: it resolves and revalidates
the epoch-independent persistent fence evidence and continuously closed live
barrier, constructs the checked-next per-epoch handoff naming `D` and the new
epoch, advances the current-handoff pointer, and binds that handoff digest into
the reserved row in this same transaction. No new attestation, epoch row,
selector, or handoff is visible alone, and the branch performs no external
fence effect or legacy-writer reopen. A failure or abort creates none of those
facts. Neither generation is caller-supplied;
an overflow refuses the transaction, and an exact retry returns the one
committed value without allocating again. The row immutably binds target, surface,
epoch, predecessor-active epoch, attestation, and every proposal input. It is
neither the current active epoch nor stage authority. The activation proposal,
manifest, continuity session, and capability must bind that same row and epoch.

Combined activation locks the reserved row, current-active-epoch pointer, and
current-reserved-activation selector; requires the selector to name that exact
`RESERVED_FENCED` row; requires exact equality among the row, attestation,
activation proposal, manifest when present, and continuity-session binding;
and consumes the proposal once. In the same synchronous transaction that
installs the witness and creates required genesis and manifest state, it
changes that row once to `ACTIVE`, installs the value as the protected current
active epoch, and clears the selector by exact compare-and-set. Exact replay
returns the committed activation. A second consumption, another binding under
the reserved value, an already-active value, or either pointer mismatch
conflicts.

An uncertain activation outcome leaves the exact row selected and blocks a new
reservation until recovery resolves the transaction. Once recovery proves the
activation did not commit and the exact proposal can no longer be admitted,
the protected abandonment transition locks the same selector, row,
attestation, and transaction identity; changes the row once to
`ABANDONED_FENCED`; clears the selector; and makes the attestation noncurrent in
one transaction. The epoch high-water mark never moves backward, and neither
an abandoned row nor its epoch can be reused, activated, renumbered, or
reopened. A `J`, `P`, `R`, or `M` stage accepts the attestation only after the
`ACTIVE` transition and only when its `StageAdmission.publication_epoch`
equals both `D.proposed_publication_epoch` and the protected current active
epoch.

Attestation issuance locks the current policy pointer, current attestation
slot, the complete deployment partition, every `Q_R.tier_results` design,
implementation, and release pointer, every exact prerequisite-result pointer
inside those results, the epoch high-water and selector slots, and all named
profile, topology, host, endpoint, receipt, clock, target, and role records. It
rejects `OPEN`,
`PREREQUISITE_BLOCKED`, `FAIL`, `STALE`, superseded,
invalidated, missing, extra, duplicate, reordered, or policy-noncurrent
evidence. In one transaction it allocates and inserts the immutable reserved
row, inserts the immutable attestation, and installs both protected selectors;
no row or body is current or consumable before every effect commits.
It also requires the observation's monotonic sample to be fresh, strictly
inside the current envelope, and no later than its recomputed conservative
upper bound. A future caller value, caller-supplied issue time, later
observation under the same key, or changed clock body is rejected. Exact retry
returns the first committed observation and attestation byte for byte.

Combined activation and every `J`, `P`, `R`, or `M` stage repeat exact current
policy-reference equality, current-attestation-reference equality, the complete
deployment partition, and the receipt's complete design, implementation, and
release partitions, including each result's complete ordered prerequisite
references. They also require the attestation's four support bindings to equal
the protected live deployment and, for stages, its proposed epoch to equal the
active epoch. They hold all current pointers and bindings through commit. A policy
replacement or revocation, record-wide invalidation, subject change, campaign
supersession, or other pointer change preserves every immutable body but makes
the old receipt or attestation noncurrent as applicable. Activation and stages
then refuse and fence consumption. The exact failed-deployment code identifies
policy mismatch, revocation, expiry, invalid partition, or the deployment
evidence state; qualification evidence changes retain the three exact
qualification failure codes above.

### Evidence registrar and authority access model

The exact protected owner role is `hindsight_journal_evidence_owner`, a
`NOLOGIN`, `NOINHERIT` role that no principal may assume. It alone owns the
evidence relations and these `SECURITY DEFINER` interfaces:

- `ACCEPT_EVIDENCE_CAMPAIGN_PLAN(EvidenceRef, EvidenceRef)`;
- `ACCEPT_HISTORICAL_CORPUS_PLAN(EvidenceRef, EvidenceRef)`;
- `ACCEPT_QUALIFICATION_PLAN(EvidenceRef, EvidenceRef)`;
- `REGISTER_EVIDENCE_CAMPAIGN(EvidenceRef)`;
- `ACQUIRE_DEPLOYMENT_EVIDENCE(campaign, run_id, oracle_id)`;
- `OBSERVE_EVIDENCE_TIME(CAMPAIGN_START | EVIDENCE_START,
  subject_key_digest, EvidenceRef | "NONE")`;
- `REGISTER_EVIDENCE_RUN_RESULT(EvidenceRunRegistrationInput)`;
- `REGISTER_EVIDENCE_INVALIDITY_FINDING(EvidenceRef)`;
- `SET_CURRENT_EVIDENCE_SUBJECT(tier, EvidenceRef)`;
- `APPLY_EVIDENCE_DISPOSITION(authority_subject, EvidenceRef)`; and
- `READ_CURRENT_EVIDENCE_TIER_RESULT(claim_id, tier)`.

Each interface fixes a safe `search_path`, revokes `PUBLIC`, resolves complete
canonical bodies, and exposes only its named operation. The three acceptance
interfaces authenticate their session principal, recompute the complete
plan-specific registration predicates, and store the exact plan and acceptance
under the plan's unique protected acceptance key. The registration, subject,
and disposition interfaces call one owner-internal evaluator routine where a
result can change; it recomputes affected tier results and replaces current
pointers atomically. No login receives `EXECUTE` on that routine, and no
callable interface accepts a proposed `EvidenceTierResult`, verdict, or
replacement current-result pointer. The read interface takes only the exact
claim and tier key and returns the one protected current reference; its caller
cannot choose among stored results.
`OBSERVE_EVIDENCE_TIME` accepts no timestamp and cannot issue
`EVIDENCE_COMPLETE`. For either admitted start phase, it derives the protected
registration time or conservative qualified-clock upper bound, stores one
immutable observation under `(phase, subject_key_digest)`, and exact-replays
it. Changed mode, clock, or bytes under that key conflict. Receipt,
attestation, disposition, and run-completion observations are owner-internal
steps of their matching atomic finalizers and have no separately callable
issuance path.

`ACQUIRE_DEPLOYMENT_EVIDENCE` resolves the accepted deployment campaign and
planned run, authenticates the isolated producer, and locks the current policy,
support profile, qualified clock envelope, catalog scope, and service-registry
scope. It accepts no projection, timestamp, role-grant set, writer inventory,
or acquisition identity from its caller. The owner derives a fresh acquisition
identity, invokes only the run's planned acquisition procedure, and stores the
observed projection, its `DEPLOYMENT_EVIDENCE_ACQUIRE` observation, and the
closed `DeploymentEvidenceAcquisition/v1` body in one protected operation. The
three bodies carry the same run, oracle, clock envelope, and actual boot
identity; no later interface may replace or reissue any of them under that
acquisition identity.

`REGISTER_EVIDENCE_RUN_RESULT` accepts only the closed registration input, not
a caller-completed result or timestamp. It resolves the accepted plan and run,
validates every start observation, retained immutable artifact, oracle
contract, expected and observed projection, status-specific failure input, and
every required deployment-acquisition body before sampling completion time. It
requires the registered observed projection to equal the acquisition body's
projection byte for byte and rejects a missing, duplicate, extra, later-issued,
wrong-run, wrong-oracle, wrong-clock, or cross-boot acquisition. Only after all
references validate does it derive the server-owned `EVIDENCE_COMPLETE`
observations, construct the final `EvidenceRecord/v1`, optional
`EvidenceRunFailure/v1`, and `EvidenceRunResult/v1` bodies and references, and
insert those bodies, observations, and affected current tier-result pointers
in one transaction. Any validation, clock, canonicalization, or pointer failure
inserts none of them. An `EVIDENCE_COMPLETE` observation therefore never exists
without its exact registered result, and no result can point to a completion
observation issued in advance. Its time never replaces, advances, or otherwise
refreshes the deployment acquisition time.

The exact nonruntime plan-authority login is
`hindsight_journal_plan_authority_login`. It has `NOINHERIT`, no role
memberships or `SET ROLE` path, and no direct relation or sequence privilege.
It has `EXECUTE` only on the three `ACCEPT_*_PLAN` interfaces. Each acceptance
body's `operator_principal` must equal this authenticated session principal.
The login cannot execute a campaign, register evidence, set a subject, apply a
disposition, compute or select a verdict, finalize qualification, author
admission, activate an epoch, advance publication, or mutate or verify a
target.

Live operation authority uses the distinct
`hindsight_journal_operation_authority_owner`, a `NOLOGIN`, `NOINHERIT` role
that cannot be assumed and owns only the grant, plan, approval, authorization,
revocation, and current-authority relations. The isolated nonruntime logins
`hindsight_journal_operation_grant_issuer_login`,
`hindsight_journal_operation_plan_issuer_login`,
`hindsight_journal_operation_approver_login`,
`hindsight_journal_operation_authorizer_login`, and
`hindsight_journal_operation_revoker_login` may call, respectively, only
`ISSUE_OPERATION_GRANT`; `ISSUE_OPERATION_PLAN`;
`APPROVE_OPERATION_PLAN`; `AUTHORIZE_OPERATION`; and the two `REVOKE_*`
interfaces. They have `NOINHERIT`, no memberships or `SET ROLE` path, and no
direct relation or sequence privilege. Only protected `J`, `P`, `R`, and `M`
function owners may call `READ_CURRENT_OPERATION_AUTHORITY`, and they may only
read the exact plan already bound by the stage request. No login can combine
grant issuance, planning, approval, authorization, or revocation, and every
operation-authority identity is denied evidence, policy, qualification,
admission, activation, publication-stage, mutation, verification, closure,
fence, target, and runtime interfaces. Those identities and owners have
reciprocal denials against every operation-authority mutation interface.

The exact nonruntime authority login is
`hindsight_journal_evidence_authority_login`. It has `NOINHERIT`, no role
memberships or `SET ROLE` path, no direct relation or sequence privilege, and
`EXECUTE` only on `SET_CURRENT_EVIDENCE_SUBJECT` and
`APPLY_EVIDENCE_DISPOSITION`. The exact nonruntime producer login is
`hindsight_journal_evidence_producer_login`; it has the same isolation and
`EXECUTE` only on `ACQUIRE_DEPLOYMENT_EVIDENCE`, `OBSERVE_EVIDENCE_TIME`, and
the three `REGISTER_*` interfaces. The authority login cannot
register or alter evidence, run a producer, finalize qualification, author
admission, create or consume activation, advance publication, mutate or verify
a target, perform closure or fencing, act as ordinary runtime, or call the
current-result read interface. The producer login cannot select subjects,
apply dispositions, read or select stored verdicts, or exercise any of those
qualification, admission, activation, publication, mutation, verification,
closure, fencing, or runtime powers.

Private evidence uses a separate `hindsight_journal_private_evidence_owner`
that is also `NOLOGIN`, `NOINHERIT`, cannot be assumed, and alone owns the
private package, package-to-public mapping, reviewer-authorization, and current
review-pointer relations. It exposes exactly four fixed-search-path,
`PUBLIC`-revoked interfaces:

```text
REGISTER_CONTROLLED_PRIVATE_PACKAGE(package)
READ_CONTROLLED_PRIVATE_PACKAGE_FOR_REVIEW(public_record_id)
REGISTER_CONTROLLED_PRIVATE_REVIEW(
    public_record_id,
    expected_current_receipt,
    public_projection,
    receipt
)
EXPORT_CURRENT_REVIEWED_EVIDENCE(
    public_record_id,
    expected_current_receipt
)
```

The isolated `hindsight_journal_private_registrar_login` may call only the
first interface. The isolated `hindsight_journal_private_reviewer_login` may
call only the second and third. The isolated
`hindsight_journal_public_evidence_exporter_login` may call only the fourth.
Each has `NOINHERIT`, no memberships or `SET ROLE` path, and no direct
relation, sequence, large-object, filesystem, or other interface privilege.
The registrar cannot read, review, or export; the reviewer cannot register,
replace, or export; and the exporter can receive only the exact public
projection and receipt, never a package, mapping, private reference, nonce,
key, retained byte, or content-derived identifier.

Review registration locks the exact package mapping, deciding run and records,
current subject and selected campaign result, private and public policies,
configured reviewer authorization, and current receipt pointer. It installs
the exact projection, immutable receipt, and current pointer atomically by
compare-and-set. A receipt is current only while all those references remain
byte-identical and current and `expected_current_receipt` matches the protected
pointer. Record invalidation, campaign supersession, subject replacement,
package-mapping replacement, policy replacement, reviewer deauthorization, or
a later reviewed projection preserves the old bodies but makes the receipt
noncurrent. Export repeats those checks under lock and returns both public
bodies or neither. The three logins, their owner, and all four interfaces have
reciprocal denials against general evidence administration, qualification,
admission, activation, publication, mutation, verification, closure, fence,
operation-authority, and runtime powers; those principals and owners in turn
have no private-package, review, or export path.

The exact nonruntime deployment-policy authority login is
`hindsight_journal_deployment_policy_authority_login`. It has `NOINHERIT`, no
role memberships, `SET ROLE` path, or direct relation privilege, and may call
only the protected
`COMPARE_AND_SET_CURRENT_DEPLOYMENT_ADMISSION_POLICY(target_database_identity,
target_surface_digest, expected_current, replacement)` interface, where both
reference operands are `EvidenceRef | "NONE"`. That interface derives and
locks exactly the named target-surface slot; requires byte equality with
`expected_current`; authenticates a non-`NONE` replacement whose target and
surface sets contain that key; preserves prior bodies; and atomically replaces
or clears only that pointer, retires the displaced slot-reference pair, and
fences only the affected admission generations. A `"NONE"` replacement cannot
clear any other slot. It rejects a stale expected reference or a retired
slot-reference pair rather than racing or reinstating it. The login cannot register evidence, finalize
qualification, author an attestation or clock envelope, create or consume
activation, advance a stage, or mutate or verify a target. The admission owner
may read the exact current policy only inside its protected finalizer and
cannot call the policy-mutation interface.

Qualification-finalizer, admission-authoring, combined-activation-function,
and stage-function owners receive only the
`READ_CURRENT_EVIDENCE_TIER_RESULT` calls their exact predicates require; they
receive no evidence mutation interface or direct relation access. Publication,
mutation, verification, closure, fence-adapter, ordinary-runtime,
qualification-runner, and every other login receive none of the evidence
interfaces. The activation login itself receives none; only its protected
function owner can perform the required deterministic read. Conversely,
`hindsight_journal_evidence_owner`, the plan-authority,
deployment-policy-authority, evidence-authority, and producer logins, and every
evidence or policy-administration interface have no membership, grant, or
callable path into qualification, admission, activation, publication,
mutation, verification, closure, or fence interfaces. `JAC-ACL-01` derives its
deterministic positive and reciprocal denial cases from this exact matrix and
tests each identity without combining owner, plan-authority,
deployment-policy-authority, evidence-authority, producer, or consumer powers.

### Registered evidence and verdict computation

The `hindsight_journal_evidence_owner` registrar holds exactly one current
`subject_revision` `EvidenceRef` for each tier. Its evaluator reads that
protected value; a caller cannot supply it to evaluation or replace it outside
`SET_CURRENT_EVIDENCE_SUBJECT`. Through that interface, the evidence-authority
login can atomically replace the value while preserving its prior value as
immutable history; the interface cannot create or alter a campaign, run,
evidence record, verdict, or result. An evidence campaign begins when
`REGISTER_EVIDENCE_CAMPAIGN` accepts one complete canonical
`EvidenceCampaign/v1` whose tier and subject equal those current protected
values. Before any campaign body exists for a required claim and tier, its
verdict is `OPEN`. The registrar validates every claim and the tier against the
registry, expands every required matrix cell and accepted plan cell into
`planned_runs`, and rejects an empty schedule, duplicate run, missing evidence
class, missing oracle, or extra requirement.
`planned_runs` uses its authenticated basis plan's exact semantic order:
ordinary claim-registry plans use their canonical registry body's ASCII
`run_id` order; qualification plans use the complete cell/case/seed/allocation
expansion order above; historical plans use their fixture, generator, and real-
evidence expansion order; and deployment plans use their canonical matrix
order. Each run's `claim_ids` is a
nonempty subset of the campaign claim set, and each run's `oracles` uses exact
`OracleId` registry order. Every run names the exact immutable
pre-execution stimulus, tool, acquisition procedure, limits, and literal
`"NONE"` or exact authority-gate conformance prestate. Every oracle
requirement names its exact contract and the expected projection fixed before
any run starts. The campaign body is immutable after the registrar accepts it;
none of those references may be supplied or changed by a later evidence
record. Every campaign names one `EvidenceCampaignPlan/v1` and its exact
authenticated `EvidenceCampaignPlanAcceptance/v1`; `"NONE"` is invalid for
either field. The plan fixes the campaign's identity and complete run sequence
before execution. Its campaign ID, claim set, tier, subject, and `planned_runs`
must equal the campaign fields byte for byte, and each run's tier must equal
the plan tier.
The acceptance names that exact plan and is authenticated and stored by the
protected plan-acceptance boundary before campaign registration. Its stable key
is `plan`: an exact retry is byte-identical, while a changed body or second
acceptance identity conflicts. These strict time
relations apply:

```text
campaign_plan.created_at_unix_ns
    < campaign_plan_acceptance.issued_at_unix_ns
    < campaign.started_at_unix_ns
    < campaign_plan_acceptance.valid_until_unix_ns
```

`campaign.start_time_observation` is the exact protected observation whose
`phase=CAMPAIGN_START`, whose subject-key digest covers the campaign plan and
campaign ID, and whose trusted upper bound equals
`campaign.started_at_unix_ns`. A qualification-plan campaign requires
`mode=QUALIFIED_CLOCK`; no producer timestamp can start it earlier or extend
its accepted execution interval.

An atomic basis permits only the runs derived by that basis: the exact typed
canonical claim-registry body for
design and ordinary implementation runs, qualification-plan for release runs,
historical-corpus-plan for `EV-LEG`, and the exact typed canonical deployment
matrix bound to the current deployment policy for deployment runs.
When one implementation claim requires `EV-LEG` plus any ordinary class, the
campaign uses the closed `CompositeCampaignPlanBasis` with exactly two members
in this order: its exact claim-registry basis, then its exact historical-corpus
basis. No other composite member set is valid. The validator expands the first
member completely, expands the second completely, filters each expansion to
the campaign's claim set, and concatenates them in member order. Every
`EV-LEG` run must come from the historical member; every other run must come
from the claim-registry member. A duplicate run ID across members invalidates
the plan. This closed composite lets one selected campaign satisfy mixed-class
claims such as `JAC-LEG-01` and `JAC-CUT-01`; it never merges results from
separately selected campaigns.

For release qualification the atomic basis is the exact
`QualificationPlan/v1` and `QualificationPlanAcceptance/v1`. For deployment it
is the exact protected current `DeploymentAdmissionPolicy/v1` plus the exact
`CanonicalDeploymentMatrix/v1` named by the basis. A protected
acceptance record, not a digest or caller assertion, proves each plan was
accepted before execution. The first
accepted campaign occupies the unique protected root slot for each named claim
and tier. The slot is not partitioned by subject: changing the current subject
makes that selected campaign `STALE` rather than making its evidence disappear.
A later campaign remains an unselected candidate until one valid supersession
links it to the current selected result.

For a qualification plan, the evidence campaign ID equals the plan campaign
ID. `EvidenceCampaignPlan.planned_runs` and `EvidenceCampaign.planned_runs`
both equal `QualificationPlan.planned_runs` byte for byte. The registrar also
repeats the independent expansion above and requires the stored sequence to
equal its result. Every campaign run therefore retains its plan cell's exact
claim set, canonical definitions, predicates, and evidence class; exact tool,
acquisition procedure, limits, and optional reader-execution binding;
exact case and optional schedule stimulus; complete ordered oracle list and
expected projections; cardinality; and deterministic run ID. A campaign-plan
or campaign difference conflicts rather than selecting another expansion.

A historical fixture cell has run ID `cell_id || "-fixture"` and derives one
deterministic `EvidenceStimulus/v1`. Its `historical_fixture` equals the cell's
exact `HistoricalFixture/v1` reference, `input_artifact` and `parameter_bytes`
both equal that fixture body's exact `fixture_bytes`, `stimulus_class` is
`HISTORICAL_FIXTURE`, and `stimulus_id` equals the run ID. That wrapper, not the
fixture body itself, is the run's `stimulus`. The run resolves the cell's
reader-execution binding, copies its reader tool, and copies the cell's
acquisition procedure and limits references byte for byte. A historical
generator cell has one run for every
seed and case-budget position; before campaign-plan acceptance and campaign
start, its registered generator materializes the exact case stimulus and the
independent oracle materializes the expected projection. Each generated run
resolves the cell's reader-execution binding, copies its reader tool, and
copies the cell's acquisition procedure and limits references byte for byte.
The referenced
limits body must encode the cell's exact case, execution, byte, depth, node,
edge, and shrink bounds. Runs are ordered first by seed position and then by
case position, with run ID
`cell_id || "-seed-" || seed_ordinal || "-case-" || case_ordinal`. Both
ordinals are one-based shortest decimal integers. A plan is invalid if a
derived run ID is not a `ContractId` or collides with another derived ID.

Each historical corpus plan also contains a nonempty finite
`real_evidence_cells` sequence. Cells are ASCII-ordered by `cell_id`, their IDs
are disjoint from fixture and generator cells, and their `stimuli` and
`real_artifact_bindings` and `expected_projections` are nonempty and all three
sequences have equal cardinality. Each stimulus is an exact pre-execution
`HISTORICAL_REAL_ARTIFACT` wrapper whose non-`NONE` `input_artifact` equals the
same-ordinal `RealArtifactBinding/v1`. The binding's declared mode equals the
cell's exact private or sanitized-real mode, and its two governing policies
equal the plan. Each such run has ID
`cell_id || "-real-" || stimulus_ordinal`, with a one-based shortest decimal
ordinal, and copies the cell's claims, canonical definitions and predicates,
reader-execution binding, bound reader tool, projection contract, procedure,
and limits. Real runs follow all public fixture and generator runs,
with cells and then stimulus ordinals ascending. Every real cell names a
nonempty ASCII-ordered subset of the closed required-category IDs, but it
never contributes a row to the public synthetic coverage projection.

Every design or ordinary implementation plan resolves and expands its exact
`CanonicalClaimRegistry/v1` body before authenticated acceptance. Every
deployment plan resolves its exact `CanonicalDeploymentMatrix/v1`, requires
its policy reference and four scope sequences to equal the then-current policy,
and expands one prebound run for each planned admission case.
Execution later yields the exact observed admission projection or the exact
failed-deployment result required by that case; only the later admission
finalizer can issue an attestation. The registrar rejects a campaign plan or
campaign whose planned runs omit, add, duplicate, reorder, or change a run ID,
claim, tier, evidence class, stimulus, real-artifact binding, tool, acquisition
procedure, limit, oracle, or expected projection. No campaign may infer any of
those values from evidence produced after execution.

An `OracleContract/v1` binds the one canonical registry, that oracle's exact
immutable definition, and its independent implementation. The definition's
field requirements are nonempty, uniquely named, and remain in the registry's
displayed semantic order. Its claim obligations are exact and cannot be
replaced by campaign claims. Each obligation resolves its canonical claim
definition and the complete registry-order predicate sequence for every tier
at which this oracle serves that claim. The obligation's claim ID must equal
the definition and every predicate. Each campaign expected
projection and both projection references in an `EvidenceRecord` must name
`OracleProjection/v1`. Their `oracle_id` and `oracle_contract` must equal the
matching campaign requirement byte for byte, and the record's
`expected_projection` must equal that requirement's reference byte for byte.
Their `oracle_definition` must equal the contract's definition, and their
`claim_predicates` must equal the campaign oracle requirement's exact sequence.
Each predicate must contain this run and oracle. All predicates in that
sequence must carry the same complete expected field sequence; the expected
projection is valid only when its fields equal that sequence byte for byte.
The observed projection repeats the same predicate references but reconstructs
the field values independently from retained evidence. Projection
fields have the definition's exact names, order, and value kinds;
`value` has exactly the selected type, and `NONE` requires literal `"NONE"`.
The independent implementation recomputes the observed projection from the
retained evidence. `oracle_result` is `PASS` exactly when every observed tagged
value equals the corresponding canonical predicate field; otherwise it is
`FAIL`. A predicate oracle records its derived predicate outcome as an
ordinary canonical expected and independently observed field, so it does not
introduce another comparison mode. The oracle contract's independent
implementation cannot equal the run
tool reference or call the production serializer, reader, or mutation path it
decides. A missing field, extra field, wrong type, unresolved reference,
different oracle result, or production-only projection makes the record
invalid.

An `EvidenceRunFailure/v1` is valid only for one planned run and one
non-`EXECUTED` status. Its campaign and run ID equal that campaign
requirement. Its projection references name `OracleProjection/v1` bodies under
the exact referenced `OR-EVID` contract and have that contract's complete
ordered field set. The registered contract for each execution status binds the
planned requirement, including its exact tool, acquisition procedure, and
limits, the observed work or absence, and the exact status-specific cause. The
protected evaluator sets `result=CONFIRMED` only
when its independent oracle recomputes those fields and proves that exact
status; a generic log, free text, process exit, or caller-selected label is
invalid. Its interval starts no earlier than the campaign and satisfies
`started_at_unix_ns <= completed_at_unix_ns`.

Its start and completion observations have phases `EVIDENCE_START` and
`EVIDENCE_COMPLETE`, use the exact `EVIDENCE_RUN` subject projection of
`(campaign, run_id)`, and have trusted upper bounds equal to the same-named
timestamp fields. The completion observation is not earlier than the start
observation.

Each planned run has exactly one `EvidenceRunResult/v1`, uniquely keyed by
`(campaign, run_id)`. Its campaign, claim set, tier, subject, run ID, and
evidence class equal its campaign requirement. Its claim definitions and
predicates equal the requirement's exact same-order canonical references, and
each evidence record carries the subset required by its oracle. Its
`conformance_prestate`
equals the planned requirement's exact value. Its `real_artifact_binding`
equals the binding transitively named by a `HISTORICAL_REAL_ARTIFACT`
stimulus, and is `"NONE"` for every other stimulus. A failure and every
evidence record copy those plan bindings from the same planned run; the
registrar does not accept them from the producer. For `EV-LEG`, the result and
every record copy the exact non-`NONE` reader-execution binding and the record's
`tool` equals its bound reader tool. Every other class uses
`reader_execution="NONE"`. Each evidence record is unique
on `(campaign, run_id, oracle_id)`, has the same claim set, starts no earlier
than the campaign, has `tool`, `acquisition_procedure`, and `limits` equal to
the planned-run requirement byte for byte, and satisfies
`started_at_unix_ns <= completed_at_unix_ns`.
Every record's two protected time observations use its exact
`(campaign, run_id, oracle_id)` subject-key digest and equal its timestamp
fields. For qualification-plan campaigns they use `QUALIFIED_CLOCK`, name the
exact protected qualification clock epoch current for that individual phase,
and recompute under that epoch's exact envelope; ordinary design and
implementation evidence may use protected registration
observations. A producer-supplied, future-dated, cross-run, cross-oracle,
wrong-phase, later-reissued, or unqualified observation is invalid.
Every deployment record has one non-`NONE`
`deployment_evidence_acquisition` naming the exact protected acquisition body
for its observed projection; every nondeployment record uses literal `NONE`.
A deployment failure with an observed projection follows the same rule: it
must carry the non-`NONE` acquisition that names those exact bytes. A failure
before live acquisition uses `observed_projection=NO_LIVE_PROJECTION` and
`deployment_evidence_acquisition=NONE`; that literal is admitted only for an
independently confirmed pre-acquisition failure. An actual projection paired
with `NONE`, the literal paired with an acquisition, or a nondeployment use of
the literal is invalid. A deployment failure can never contribute usable
evidence to a `PASS` partition.
The run result's own start and completion observations likewise use the exact
`EVIDENCE_RUN` subject projection, carry the two phases, and equal its timestamp
fields. Qualification-plan campaigns require `QUALIFIED_CLOCK` for the run,
failure, and record observations. Their `clock_bindings` contains every run and
record observation for this run and, for deployment, every acquisition
observation, ordered by `DEPLOYMENT_EVIDENCE_ACQUIRE`, `EVIDENCE_START`, then
`EVIDENCE_COMPLETE`, and then by `subject_key_digest`; each member copies the
observation reference, phase, subject digest, and exact
`QualificationClockEpoch/v1` whose envelope equals the observation envelope;
its `boot_identity` and `clock_envelope` fields equal that epoch and envelope.
Deployment-run bindings use `clock_epoch="NONE"` but bind the exact deployment
clock envelope and its actual boot identity directly, including every
acquisition observation. Other nonqualification campaigns have an empty
`clock_bindings` sequence. Ordinary design and implementation campaigns use
only protected registration observations. The
protected observation slots exact-replay, so a retry cannot move either
endpoint.

The qualification clock-epoch sequence is protected and plan-scoped. Epoch one
has `boot_ordinal=1` and `predecessor_clock_epoch="NONE"`. A later epoch is
inserted only after a qualified reboot has changed the boot identity; it uses
the checked-next ordinal, names the exact preceding epoch, and names a newly
issued current `ClockEnvelope/v1` for the new boot. Insertion and advancement of
the plan's current-clock-epoch pointer are atomic. The chain is contiguous,
append-only, and nonrenewing: an ordinal cannot be reused, an earlier envelope
cannot become current again, and an envelope cannot be copied across a reboot.
Every epoch's `boot_identity` equals its clock envelope's exact boot identity,
and that envelope binds the plan's stable boot configuration. Every
qualification phase binds the exact epoch, envelope, and boot identity current
under lock when that phase is observed. A class result's `clock_epochs` is the duplicate-free
first-use sequence obtained from its ordered run results. Reordering, omission,
backdating, reuse of a boot identity after a qualified reboot, boot change
without a checked-next epoch, cross-epoch run binding, stable boot-configuration
drift, or a gap prevents class-result and receipt creation.
For `execution=EXECUTED`, `failure_evidence` is `"NONE"` and
`evidence_records` contains exactly one valid `EvidenceRecord/v1` for every
required oracle in oracle order. The run's `oracle_result` is `PASS` exactly
when every record's recomputed result is `PASS`; otherwise it is `FAIL`. Every
other execution value requires `failure_evidence` to name the exact matching
`EvidenceRunFailure/v1`, an empty `evidence_records` sequence, and
`oracle_result=FAIL`. An executed run's exact protected run-boundary
observations define its start and completion; every record starts no earlier
than the run and completes no later than it. A nonexecuted run copies its
failure body's exact interval and observation references. No caller supplies
either bound. The registrar issues every completion observation only inside
the result-registration transaction after validating all immutable result and
artifact references.
Once a campaign exists, a missing run-result body is an
omitted run and therefore also `FAIL`. A missing campaign is `OPEN`; a started
campaign can never regain `OPEN` by skipping, deleting, invalidating, or
failing to explain required work.

`HistoricalCorpusPlan/v1` closes the exhaustive finite public synthetic corpus
before execution. Fixture and generator cells are each strictly ascending by
ASCII `cell_id`; cell IDs are unique across both sequences, fixture IDs are
unique, and every generator seed sequence is nonempty, duplicate-free, and
ascending by numeric value. Each fixture reference resolves to complete public
synthetic `HistoricalFixture/v1` bytes. Each generator cell fixes positive
limits for cases, shrinking, execution nanoseconds, bytes, depth, nodes, and
edges. The plan's `reader_registry` is the complete duplicate-free sequence of
canonical `HistoricalReaderRegistryBinding` projections in the compatibility
registry's member order. Every binding copies the complete selector, derived
reader contract ID, wire contract, and source revision from exactly one
`ReaderRegistryMember`; its `reader_registry_member_digest` is SHA-256 over
that complete member's compatibility-canonical bytes, including the LF. The
plan's `reader_executions` has the same cardinality and member order; member
`i` resolves the unique `HistoricalReaderExecutionBinding/v1` whose embedded
reader equals registry projection `i`. Every fixture, generator, or real cell
names one of those exact execution bindings, one
`SuccessorProjectionContract/v1`, its acquisition procedure and limits, and
the exact plan-wide independent oracle as applicable. Its expanded run's tool
comes only from that binding.

The registry and corpus include the exact kindless schema-1 authenticated
dependency member with role `requeue-plan`, literal
`action="requeue-operation-cohort"`, default compatibility LF wire contract,
pinned source revision, and reader contract ID ending in
`3f2089bacd91e4591d7a5939cc274d7ca7ae6600466718504b1a6c5102b58245`.
The frozen registry's `member_count` equals the complete member sequence length
including that member. Public fixture and generator cells cover its successful
authenticated-role decode, every registered failure, discovery-root refusal,
wrong-role refusal, changed-action refusal, and every shared-lifecycle parent
that recursively depends on it. A parent tool cannot claim coverage by parsing
the kindless body inline.

The coverage projection's `required_categories` has exactly one row for each
of these category IDs, in the displayed order:

```text
artifact-integrity-byte-drift
artifact-integrity-disappearance
artifact-integrity-non-regular-file
artifact-integrity-path-replacement
artifact-integrity-read-failure
artifact-integrity-regular-file
artifact-integrity-size-drift
artifact-integrity-symlink
compatibility-authority-none
compatibility-permitted-next-action-refusal
compatibility-raw-identity-preservation
compatibility-rollback-bridge-eligibility
compatibility-rollback-bridge-ineligibility
compatibility-semantic-projection
dependency-authenticated-role
dependency-cycle
dependency-digest-mismatch
dependency-duplicate-edge
dependency-missing
dependency-substituted-role
dependency-unsupported-predecessor
lifecycle-closure-only
lifecycle-complete
lifecycle-corrupt
lifecycle-excluded
lifecycle-failed
lifecycle-known-undispatchable
lifecycle-pending
lifecycle-unknown
lifecycle-unreadable
representation-collection-boundary
representation-duplicate-field
representation-malformed-encoding
representation-required-field-omission
representation-scalar-boundary
representation-unknown-field
representation-valid-exact-bytes
representation-wrong-type
version-each-registered-boundary
version-representative-higher
version-representative-lower
version-unknown-future
```

Every required-category row has a nonempty, duplicate-free ASCII-ascending
`source_cell_ids` sequence, and each named cell is an exact fixture or
generator cell that exercises that category. The protected registrar derives
`HistoricalCorpusCoverageProjection/v1`
without caller choices. `reader_executions` equals the plan's complete
canonical `reader_executions`. `artifact_variants` contains every exact
execution binding and its fixture or generator source. `boundaries` contains every scalar,
collection, encoding, dependency, lifecycle, integrity, projection, and
version boundary required by each exact reader grammar. `generators`, `seeds`,
`budgets`, and `shrinks` copy that cell's same exact reader-execution binding and are
exact projections of every generator cell.
`policies` contains exactly the plan's private-or-sanitized-artifact and public
projection policy references in that order. Every reader, artifact kind,
schema, variant, boundary, generator, seed, budget, shrink allocation, policy,
and required category must appear exactly once at its stable composite key and
in the grammar-defined order. Category rows use the displayed order. Every
reader-specific row sorts first by the complete compatibility
`ReaderRegistryMember` canonical bytes represented by its execution binding; artifact
rows then use `coverage_source`, boundary rows use `(field_path, boundary,
source_cell_id)`, generator rows use `(cell_id, required_category)`, seed rows
use `(cell_id, numeric seed)`, and budget and shrink rows use `cell_id`. Policy
rows use the displayed enum order.
Every inner cell-ID sequence is ASCII-ascending. These are the only admitted
orders; an alias is not an equal key.

The registrar resolves every `reader_registry_member_digest` to exactly one
current closed-registry member and requires byte equality for protocol family,
protocol version, artifact kind, authenticated dependency role, artifact schema,
reference-plan schema, artifact-or-reference-plan variant, wire contract,
derived reader contract ID, and source revision. It also requires each fixture and generator
cell and every coverage row derived from that cell to carry the one execution
binding whose embedded reader is that projection. It resolves the bound tool
and immutable implementation and revalidates the exact selector, wire
contract, reader contract ID, and source revision before plan acceptance,
campaign registration, each run registration, and tier evaluation.
Two registry members that share executable reader code or artifact kind but
differ in any selector remain two rows and two stable keys. Dropping a dependency
role, reference-plan schema, protocol, variant, wire contract, or member digest;
collapsing two tuples; or attaching a row to another tuple rejects the plan and
its recomputed projection.

The registrar also independently expands the complete frozen compatibility
reader registry in compatibility set order, derives every reader contract ID,
recomputes the separator-free canonical member-vector digest, constructs the
exact `FrozenReaderRegistry/v1` body, and hashes that complete body under the
compatibility contract. `reader_registry_body` names exactly that registered
compatibility body. `HistoricalCorpusPlan.historical_registry_digest` equals
the registry body digest, `historical_registry_vector_digest` equals its
`member_vector_digest`, `reader_registry` is the complete order-preserving
successor projection of those members, and `reader_executions` is its
one-to-one execution-binding projection. An all-zero or stale value, unresolved
registry body, wrong member count, or digest computed from a narrowed,
reordered, differently expanded, wrong-contract-ID, unbound-tool, or
successor-canonicalized member set is invalid even if every listed member
resolves individually.

The protected plan-acceptance boundary authenticates one exact
`HistoricalCorpusPlanAcceptance/v1` naming the plan and recomputed coverage
projection. It requires
`plan.created_at_unix_ns < acceptance.issued_at_unix_ns <
acceptance.valid_until_unix_ns`. The protected acceptance key is `plan`; an
exact retry returns the byte-identical acceptance, while any changed body or a
second acceptance identity conflicts. At plan acceptance, campaign
registration, every historical run registration, and tier-result evaluation,
the registrar reconstructs the frozen compatibility registry body, member
count, and member vector; requires exact equality with both plan digests, the
body reference, and every reader projection; resolves every execution binding;
and recomputes the projection from that exact reader registry, its reader
grammars, its member-to-tool mappings, and the plan. Any zero, stale, narrowed,
reordered, differently expanded, omitted, extra, duplicated, aliased,
independently selected tool, or otherwise changed registry, execution binding,
digest, or projection rejects the body; no historical campaign can become
`PASS`. The registrar then
recomputes the finite public fixture and generator
expansion and the separate finite real-evidence expansion. It accepts an
`EV-LEG` campaign only when its accepted campaign plan and planned runs equal
their concatenation exactly.

Passing `EV-LEG` requires both exhaustive public synthetic coverage and at
least one valid planned `CONTROLLED_PRIVATE` or `SANITIZED_REAL` run. The real-
evidence sequence is finite and fixed before execution; each run authenticates
its exact `RealArtifactBinding/v1`, including payload, declared mode, governing
policies, and authenticated acquisition or sanitization provenance, and
produces the controlled private package, bounded projection, and current
independent-review receipt required below. A valid real run cannot fill a
public fixture, generator, boundary, or
required-category row. A valid public run cannot fill a real-evidence cell.
Failure, omission, or invalidity in either partition makes the campaign fail.
A historical tool invocation receives only the selector and bytes fixed by its
run's execution binding. Every resulting `LegacyReaderSuccess/v1` or
`LegacyReaderFailure/v1` repeats that exact binding reference, selected member,
member digest, derived contract ID, wire contract, and pinned source revision.
The registrar rejects an output produced by another tool or binding even when
its decoded disposition and member fields otherwise match.
A sanitized-real input may also produce a separately identified public
synthetic fixture, but the derivative has new bytes and identity and cannot
stand in for the authenticated real run unless its own binding carries the
required authenticated sanitization chain from controlled-private source
bytes. Private evidence can reveal missing
categories and force a new accepted corpus and campaign plan, but it cannot
satisfy or remove a required public category. The public projection policy
remains the bounded disclosure contract below.

A `FailedDeploymentResult/v1` is the only registered result for an admission
attempt that cannot produce `DeploymentAttestation/v1`. Its candidate
projection names an exact `OracleProjection/v1` under `OR-ID` and contains the
complete attempted deployment binding, with every unavailable value represented
by a field whose tagged value is `NONE`. `failures` is nonempty,
duplicate-free, and ordered by ASCII `failure_code`, then by canonical
evidence-reference bytes; each entry's evidence proves that exact code through
its named oracle. Its optional
qualification references are either literal `"NONE"` or the exact bodies
observed by the attempt. The result is immutable, has `authority=NONE` by
contract, and cannot appear in `StageAdmission`. Its literal `result=FAIL`
describes the attempted admission, not the test verdict. When a planned
negative row expects that exact refusal and every code and projection matches,
its `EV-DEP` evidence record has `oracle_result=PASS`. Only an unexpected
refusal, unexpected success, missing or extra failure, or projection mismatch
has `oracle_result=FAIL` and fails the campaign. The stable result key is `(campaign,
deployment_attempt_id)`; exact bytes replay, and changed bytes conflict. A
successful attempt emits no failed-deployment result. Its observations may
produce the complete deployment `PASS` partition; only then may the admission
finalizer issue and atomically install an attestation after every predicate
passes.

The protected evaluator constructs `EvidenceTierResult/v1`; a caller cannot
choose its result. For its `claim_id`, `run_results` is the complete sequence
of valid run-result references whose campaign requirements name that claim, in
planned-run order. For a required pair, `claim_definition` and
`claim_predicate` resolve the exact bodies selected by the governing canonical
registry or deployment matrix, and every run result and record must carry
those same references. The evaluator resolves the complete predicate, requires
its run IDs, classes, oracle IDs, and expected fields to equal the planned runs
and canonical expected projections, and executes its literal all-runs,
all-fields rule. `invalidations` is the complete sequence of valid
`EvidenceRecordInvalidation/v1` bodies for those records, ordered by evidence
body digest and then disposition ID. The same invalidation reference appears
in every result bound by that record. `predecessor_result` and
`selection_disposition` are
both `"NONE"` for the first campaign selected for that claim; otherwise they
name the immediately prior tier result and the exact campaign supersession
whose claim, prior result, and replacement campaign match those fields. The
evaluator walks that claim-local contiguous chain from its unique root to its
unique leaf. A branch, cycle, skipped link, cross-claim edge, or cross-tier edge
selects no campaign. Construct `EvidenceTierState` by copying all of the
result's same-named state fields exactly, including `claim_definition`,
`claim_predicate`, and `prerequisite_results`. For required
pair `(claim_id, tier)`, that sequence contains every registry-required earlier
tier result for the same claim in the fixed order `DESIGN`, `IMPLEMENTATION`,
then `RELEASE`, stopping before `tier`. Each member equals the protected
current-result reference for that prerequisite pair. A design result and a
result whose claim has no required earlier tier use the empty sequence. The
evaluator creates and maintains a canonical current result, including `OPEN`,
for every required prerequisite pair, so omission cannot masquerade as an empty
prerequisite set. The result is valid only when
`evidence_state_digest` equals SHA-256 over that state object's successor
canonical bytes, including its LF. The result body is unique on
`(claim_id, tier, subject_revision, evidence_state_digest)`; exact bytes
replay, and changed bytes conflict. Acceptance of a run result or supersession
atomically recomputes every affected result and replaces its protected current
pointer without altering an earlier body. Record invalidation and subject
change do the same for their complete affected pointer sets in one transaction.

For claim `K`, tier `T`, protected current subject `S`, unique selected
campaign `C`, and exact ordered prerequisite sequence `P`, the result body's
`subject_revision` is exactly `S`, its
`campaign` is exactly `ref(C)` or `"NONE"` when no unique `C` exists, and its
verdict is the first matching rule in this total order:

1. `NOT_REQUIRED` if the claim registry has no `K/T` requirement. Definition,
   predicate, campaign, run, invalidation, predecessor, selection, and
   prerequisite fields are empty or `"NONE"`.
2. `STALE` if `C` exists and `C.subject_revision != S`. The result retains the
   exact historical campaign and its intrinsic run results, while the result
   body's `subject_revision` equals `S`.
3. `OPEN` if no campaign has ever been selected for `K/T`, or if the
   supersession graph has no unique leaf.
4. `FAIL` if any planned run is missing or invalid, any nonexecuted outcome is
   recorded, any valid deciding record recomputes to `FAIL`, or any required
   class, oracle, cell, or run is omitted, unexplained, aborted, shortened,
   over budget, generator-failed, or unreproducible after
   `C.started_at_unix_ns`.
5. `PREREQUISITE_BLOCKED` if the claim-local evidence would otherwise pass but
   any member of `P` is absent from its protected current pointer or has a
   result other than `PASS`.
6. `PASS` only if the exact canonical claim predicate resolves and passes,
   every planned run has one valid `PASS` result, every required evidence
   class, oracle, cell, and run is covered exactly once, and every member of
   `P` remains the exact protected current `PASS` result.

The body is valid only when its stored `result` equals that recomputation. A
valid invalidation removes only a record proved procedurally or evidentially
invalid; the now-missing required record still makes its started campaign
`FAIL` for every claim bound by that record. It can never remove a valid
`oracle_result=FAIL`. Supersession
preserves the prior result and selects a separately started replacement
campaign; it never merges runs or evidence across campaigns.

Prerequisite enforcement never rewrites claim-local evidence. The evaluator
first derives the claim-local `OPEN`, `STALE`, `FAIL`, or would-be `PASS`, then
applies the prerequisite rule above. Whenever a prerequisite current pointer
changes—even from one `PASS` body to another `PASS` body—the evaluator
recomputes every dependent tier state in the same transaction, installs the new
dependent result, and makes the prior dependent result noncurrent. If the new
prerequisite is not current `PASS`, the dependent result becomes
`PREREQUISITE_BLOCKED`. Because the exact ordered prerequisite references are
inside `EvidenceTierState`, its digest and the result's stable key change even
when all claim-local bytes are unchanged; this is a new deterministic state,
not a conflict under an old key. Receipt, attestation, activation, and stage
gates accept only the recomputed current `PASS` result and recheck every bound
prerequisite reference under the same locks.

The body registry above is closed for acceptance. Every authority-bearing edge
used to decide a campaign, record, tier result, qualification result, receipt,
deployment attestation, stage, or recovery outcome names one exact registered
kind and version with a complete grammar in this record or the compatibility
record. No implementation-defined nested profile, identity, plan, procedure,
tool, limit, projection, policy, or equivalent decoded value may fill an edge.

The reusable stage objects are:

```text
FreshStageAdmission := {
  "active_fence_manifest_binding": "NONE",
  "admission_generation": SafeInteger,
  "deployment_attestation": EvidenceRef,
  "mode": "FRESH",
  "publication_epoch": SafeInteger
}

CompatibilityStageAdmission := {
  "active_fence_manifest_binding": EvidenceRef,
  "admission_generation": SafeInteger,
  "deployment_attestation": EvidenceRef,
  "mode": "COMPATIBILITY",
  "publication_epoch": SafeInteger
}

StageAdmission := FreshStageAdmission | CompatibilityStageAdmission

CanonicalLineageKeyBody := {
  "protocol_family": "hindsight-postgresql-publication",
  "protocol_version": 1,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

LineageBinding := {
  "lineage_generation": SafeInteger,
  "lineage_key_digest": Digest,
  "predecessor_m_digest": Digest | "GENESIS",
  "predecessor_v_digest": Digest | "GENESIS"
}
```

The lineage-key body above is the only lineage-key preimage. It is serialized
under `hindsight-postgresql-publication-canonical-json/v1` with exactly one
trailing LF, and `lineage_key_digest` is SHA-256 over those complete bytes. The
target reference and surface digest equal the stage's exact target and the
canonical protected relation set selected by that surface. Deployment
attestations, compatibility activation and rollback, every `LineageBinding`,
`M`, `V`, mismatch, and status projection recompute this same body. No prefix,
tuple encoding, digest concatenation, database-name shortcut, or family-local
preimage is equivalent.

`FRESH` is valid only when the protected fixed target-surface fence slot is
unoccupied: no active legacy-fence binding exists for that target surface under
any epoch. A caller's omitted reference, process observation, or comparison
only with the proposed epoch is insufficient. Whenever that slot is occupied,
`COMPATIBILITY` is required, including for an epoch activated through the
compatibility cutover or active-fence-adoption protocol and for every
`LegacyRollbackBinding`. Its `active_fence_manifest_binding` must name exactly
`hindsight-compatibility-origin-fence-manifest-binding/v1` or
`hindsight-compatibility-active-fence-manifest-adoption/v1` under the
compatibility canonical-byte contract and must equal the protected current
binding. The protected verifier separately requires that current binding's
publication epoch equal `StageAdmission.publication_epoch`. Every stage in one
aggregate retains the same admission mode and
exact `"NONE"` marker or reference bytes; no later stage may add, remove, or
switch compatibility authority.

The two lineage predecessor fields are either both `GENESIS` or both digests.
The exact action binding is one of:

```text
ApplyBinding := {
  "action": "apply",
  "apply_payload": TargetApplyPayloadEvidenceRef,
  "apply_payload_digest": Digest,
  "budget_limits": EvidenceRef,
  "grant": EvidenceRef,
  "reconciliation_limits": EvidenceRef,
  "retry_limits": EvidenceRef,
  "rollback_preimage_binding": EvidenceRef
}

SuccessorRollbackBinding := {
  "action": "rollback",
  "budget_limits": EvidenceRef,
  "grant": EvidenceRef,
  "predecessor_apply_m_digest": Digest,
  "predecessor_apply_v_digest": Digest,
  "predecessor_variant": "SUCCESSOR_APPLY",
  "reconciliation_limits": EvidenceRef,
  "retry_limits": EvidenceRef,
  "rollback_preimage_binding": EvidenceRef
}

LegacyRollbackBinding := {
  "action": "rollback",
  "budget_limits": EvidenceRef,
  "final_manifest": EvidenceRef,
  "grant": EvidenceRef,
  "legacy_predecessor_selection_digest": Digest,
  "manifest_approval": EvidenceRef,
  "predecessor_variant": "LEGACY_COMPLETE_APPLY",
  "reconciliation_limits": EvidenceRef,
  "retry_limits": EvidenceRef,
  "rollback_preimage_binding": EvidenceRef
}
```

`ApplyBinding.apply_payload` names exactly `TargetApplyPayload/v1`, and
`.apply_payload_digest` equals its `body_digest`. The body is the complete
desired selected-cohort postimage; its target, surface, lineage key, and
selected membership digest equal the plan, `J`, and rollback-preimage binding.
It is immutable and nonauthorizing before and after plan issuance. No rollback
variant has an apply-payload field, and no other body kind or version may
occupy this position.

The rollback-preimage reference in every action binding names the exact
immutable, nonauthorizing pre-plan preimage-binding kind and version; it cannot
be a digest of caller-selected bytes. `LegacyRollbackBinding.final_manifest` and
`.manifest_approval` must name
`hindsight-compatibility-final-manifest/v1` and
`hindsight-compatibility-approval/v1`. The protected verifier requires the
approval's exact authenticated channel receipt from the compatibility
envelope, its `CUTOVER_MANIFEST` domain, and its subject linkage to the final
manifest. It then requires that manifest's exact approved
`predecessor_selection=LEGACY_COMPLETE_APPLY`, and computes
`legacy_predecessor_selection_digest` as SHA-256 over that complete nested
`LegacyPredecessor` object serialized under
`hindsight-postgresql-compatibility-canonical-json/v1` with exactly one LF.
This digest is an intrinsic projection, not a cross-contract authority
reference. Its complete preimage is exactly the `LegacyPredecessor` object
listed in the compatibility grammar, with all and only its `class`,
`inventory_observation_id`, `target_surface_digest`,
`source_chain_root_digest`, `raw_identity_set_digest`,
`historical_identity_set_digest`, `reader_contract_set_digest`, and
`encrypted_preimage_identity` fields. The selected object's reader-contract
set and encrypted-preimage references therefore bind the frozen readers and
preimage without an opaque alias. `predecessor_apply_m_digest` and
`predecessor_apply_v_digest` remain bare only because their exact body kinds
and versions are fixed by this stage grammar; they are stage-to-stage body
links, not cross-contract authority references.

For apply, the apply-payload and preimage-binding references name the exact
immutable desired postimage and encrypted rollback preimage created and
verified before plan issuance. The apply payload selected membership equals
the restore payload selected membership, while the apply payload's selected
values are the sole desired apply values and the restore payload's selected
values are the sole rollback values. For
`SUCCESSOR_APPLY` rollback, it equals the retained preimage-binding reference
in the named predecessor apply aggregate. For `LEGACY_COMPLETE_APPLY`
rollback, the pre-plan body is constructed from the selected frozen-reader
preimage and its protected ciphertext body's `artifact` reference equals
`LegacyPredecessor.encrypted_preimage_identity` byte for byte. The exact bytes
must already occupy the verified protected PostgreSQL candidate row; a digest
of any body, an equivalent ciphertext projection, an external-file copy, or a
reference under another kind or version is invalid.

The `J` transaction atomically inserts the exact journal and one protected
journal-preimage adoption keyed by `digest(J)` whose values are
`J.action_binding.rollback_preimage_binding` and its exact
`ProtectedRollbackCiphertext/v1` reference. A deferred exact foreign-key and
uniqueness constraint plus a commit-time totality constraint makes the journal,
binding adoption, and protected-byte adoption all durable or all absent: no
adoption may exist without its matching `J`, and no `J` may exist without both
adoptions and the verified digest-and-length-keyed PostgreSQL byte row. The
adoption is the first authoritative protected journal state for the binding
and ciphertext bytes. The pre-plan bodies remain immutable and
`authority=NONE`; neither candidate presence nor adoption alone authorizes
`P`, `R`, or mutation outside the matching `J` chain.

Before that transaction, the caller may construct only the non-clock journal
inputs. The protected interface locks and resolves the current operation,
admission, lineage, and qualified-clock state, derives the exact
`PreStageExpiryObservation/v1`, and only then inserts its reference into the
closed `J` body, successor-canonicalizes the body, appends its one LF, and
computes `digest(J)`. No complete `J`, journal digest, or digest-keyed adoption
exists before the protected observation is derived. The transaction commits
the observation, finalized journal, binding adoption, and protected-byte
adoption together.

The exact stage bodies are:

```text
J := {
  "action_binding": ApplyBinding
                    | SuccessorRollbackBinding
                    | LegacyRollbackBinding,
  "admission": StageAdmission,
  "approval": EvidenceRef,
  "approval_expiry_unix_ns": UInt128String,
  "authorization_receipt": EvidenceRef,
  "expected_target_generation": TargetGeneration,
  "kind": "hindsight-postgresql-publication-journal",
  "lineage": LineageBinding,
  "operation_identity": Id,
  "plan": EvidenceRef,
  "pre_stage_expiry_observation": EvidenceRef,
  "preserved_cohort_digest": Digest,
  "protocol_family": "hindsight-postgresql-publication",
  "protocol_version": 1,
  "schema_version": 1,
  "selected_cohort_digest": Digest,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}

P := {
  "admission": StageAdmission,
  "journal_digest": Digest,
  "kind": "hindsight-postgresql-publication-proof",
  "lineage": LineageBinding,
  "pre_stage_expiry_observation": EvidenceRef,
  "schema_version": 1
}

R := {
  "admission": StageAdmission,
  "approval_expiry_unix_ns": UInt128String,
  "clock_envelope": EvidenceRef,
  "elapsed_upper_ns": UInt128String,
  "forward_rate_error_denominator": UInt128String,
  "forward_rate_error_numerator": UInt128String,
  "forward_rate_error_upper_ns": UInt128String,
  "journal_digest": Digest,
  "kind": "hindsight-postgresql-publication-deadline-receipt",
  "lineage": LineageBinding,
  "monotonic_anchor_lower_ns": UInt128String,
  "monotonic_sample_upper_ns": UInt128String,
  "monotonic_validity_deadline_lower_ns": UInt128String,
  "proof_digest": Digest,
  "qualification": "VALID" | "LATE",
  "schema_version": 1,
  "trusted_upper_bound_unix_ns": UInt128String,
  "wall_upper_at_anchor_unix_ns": UInt128String
}

M := {
  "admission": StageAdmission,
  "after_image_digest": Digest,
  "before_image_digest": Digest,
  "deadline_receipt_digest": Digest,
  "incarnation_capability_digest": Digest,
  "journal_digest": Digest,
  "kind": "hindsight-postgresql-publication-mutation-receipt",
  "lineage_before": LineageBinding,
  "logical_mutation_unix_ns": UInt128String,
  "new_lineage_generation": SafeInteger,
  "post_target_generation": TargetGeneration,
  "pre_target_generation": TargetGeneration,
  "preserved_cohort_digest": Digest,
  "proof_digest": Digest,
  "schema_version": 1,
  "selected_cohort_digest": Digest
}

V := {
  "deadline_receipt_digest": Digest,
  "expected_postimage_digest": Digest,
  "expected_preserved_cohort_digest": Digest,
  "expected_selected_cohort_digest": Digest,
  "expected_target_generation": TargetGeneration,
  "journal_digest": Digest,
  "kind": "hindsight-postgresql-publication-verification-receipt",
  "mutation_receipt_digest": Digest,
  "observed_lineage_generation": SafeInteger,
  "observed_lineage_head_m_digest": Digest,
  "observed_lineage_key_digest": Digest,
  "observed_postimage_digest": Digest,
  "observed_preserved_cohort_digest": Digest,
  "observed_selected_cohort_digest": Digest,
  "observed_target_generation": TargetGeneration,
  "outcome": "MATCH",
  "proof_digest": Digest,
  "schema_version": 1,
  "target_database_identity": EvidenceRef,
  "verification_attempt_id": Id
}

MismatchObservation := {
  "deadline_receipt_digest": Digest,
  "expected_postimage_digest": Digest,
  "expected_preserved_cohort_digest": Digest,
  "expected_selected_cohort_digest": Digest,
  "expected_target_generation": TargetGeneration,
  "journal_digest": Digest,
  "kind": "hindsight-postgresql-publication-verification-mismatch-observation",
  "mutation_receipt_digest": Digest,
  "observed_lineage_generation": SafeInteger,
  "observed_lineage_head_m_digest": Digest,
  "observed_lineage_key_digest": Digest,
  "observed_postimage_digest": Digest,
  "observed_preserved_cohort_digest": Digest,
  "observed_selected_cohort_digest": Digest,
  "observed_target_generation": TargetGeneration,
  "outcome": "MISMATCH",
  "proof_digest": Digest,
  "schema_version": 1,
  "target_database_identity": EvidenceRef,
  "verification_attempt_id": Id
}

UnableObservation := {
  "deadline_receipt_digest": Digest,
  "failure_category": "EXPECTED_STATE_UNAVAILABLE"
                      | "TARGET_READ_UNAVAILABLE"
                      | "TIMEOUT"
                      | "VERIFIER_INTERNAL_ERROR",
  "failure_evidence": EvidenceRef,
  "journal_digest": Digest,
  "kind": "hindsight-postgresql-publication-verification-unable-observation",
  "mutation_receipt_digest": Digest,
  "outcome": "UNABLE_TO_VERIFY",
  "proof_digest": Digest,
  "schema_version": 1,
  "target_database_identity": EvidenceRef,
  "verification_attempt_id": Id
}

TerminalVerificationFailure := {
  "deadline_receipt_digest": Digest,
  "failure_category": "INVARIANT_VIOLATION"
                      | "TARGET_IDENTITY_UNPROVEN",
  "failure_evidence": EvidenceRef,
  "journal_digest": Digest,
  "kind": "hindsight-postgresql-publication-terminal-verification-failure",
  "mutation_receipt_digest": Digest,
  "outcome": "TERMINAL_FAILURE",
  "proof_digest": Digest,
  "schema_version": 1,
  "target_database_identity": EvidenceRef,
  "verification_attempt_id": Id
}

PresentRecoveryAggregateIdentity := {
  "identity_kind": "COMMITTED_J",
  "journal_digest": Digest
}

AbsentRecoveryAggregateIdentity := {
  "identity_kind": "ABSENT_REQUEST",
  "request_key_digest": Digest
}

RecoveryAggregateIdentity := PresentRecoveryAggregateIdentity |
                             AbsentRecoveryAggregateIdentity

RecoveryRefusalObservation := {
  "action": "apply" | "rollback",
  "aggregate_identity": RecoveryAggregateIdentity,
  "authority": "NONE",
  "evidence": EvidenceRef,
  "kind": "hindsight-postgresql-recovery-refusal-observation",
  "observed_prefix": "ABSENT" | "JOURNALED" | "PROVEN" | "VALID" |
                     "LATE" | "MUTATED" | "VERIFIED",
  "reconciliation_subject": EvidenceRef | "NONE",
  "recovery_request_id": Id,
  "reservation": EvidenceRef,
  "refusal_code": "APPROVAL_EXPIRED" | "CLOCK_INVALID" | "CONFLICT" |
                  "DEPLOYMENT_ADMISSION_INVALID" |
                  "DEPLOYMENT_EVIDENCE_NONCURRENT" |
                  "DEPLOYMENT_POLICY_NONCURRENT" |
                  "FENCE_BINDING_INVALID" | "INVARIANT_VIOLATION" |
                  "LEGACY_PREDECESSOR_INELIGIBLE" |
                  "LATE" | "LINEAGE_HEAD_DRIFT" | "NO_SAFE_SUCCESSOR" |
                  "PREDECESSOR_VERIFICATION_MISMATCH" |
                  "PREDECESSOR_VERIFICATION_PENDING" |
                  "RESTORE_PAYLOAD_UNAVAILABLE" | "ROLLBACK_UNAVAILABLE" |
                  "TARGET_DRIFT" |
                  "TARGET_IDENTITY_UNPROVEN" |
                  "TERMINAL_VERIFICATION_FAILURE" |
                  "VERIFICATION_BLOCKED" | "VERIFICATION_MISMATCH" |
                  "WRONG_PREFIX",
  "schema_version": 1,
  "transaction_identity": EvidenceRef,
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest
}

RecoveryAmbiguityObservation := {
  "aggregate_identity": RecoveryAggregateIdentity,
  "ambiguity_code": "STAGE_AMBIGUOUS" | "QUALIFICATION_AMBIGUOUS",
  "authority": "NONE",
  "kind": "hindsight-postgresql-recovery-ambiguity-observation",
  "recovery_request_id": Id,
  "reservation": EvidenceRef,
  "resolution_deadline_monotonic_ns": UInt128String,
  "schema_version": 1,
  "stage": "J" | "P" | "R" | "M" | "V" | "RECONCILIATION",
  "subject_transaction_identity": EvidenceRef,
  "transaction_identity": EvidenceRef,
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest
}

RecoveryFenceObservation := {
  "admission_generation": SafeInteger,
  "aggregate_identity": RecoveryAggregateIdentity,
  "authority": "NONE",
  "deployment_attestation": EvidenceRef,
  "fence_reason": "ACTIVATION_SESSION_LOST" | "ADAPTER_RESTART" |
                  "BOOT_IDENTITY_CHANGED" | "CLOCK_ENVELOPE_INVALID" |
                  "DATABASE_CLONE" | "DEPLOYMENT_ATTESTATION_NONCURRENT" |
                  "DEPLOYMENT_EVIDENCE_NONCURRENT" |
                  "DEPLOYMENT_POLICY_NONCURRENT" |
                  "ENDPOINT_IDENTITY_CHANGED" | "HOST_REBOOT" |
                  "INCARNATION_CAPABILITY_LOST" | "PITR" |
                  "POSTGRESQL_RESTART" | "PRIMARY_PROMOTION" |
                  "UNKNOWN_CONTINUITY",
  "kind": "hindsight-postgresql-recovery-fence-observation",
  "publication_epoch": SafeInteger,
  "reconciliation_subject": EvidenceRef,
  "recovery_request_id": Id,
  "reservation": EvidenceRef,
  "schema_version": 1,
  "transaction_identity": EvidenceRef,
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest
}

RecoveryAdvancementObservation := {
  "aggregate_identity": RecoveryAggregateIdentity,
  "authority": "NONE",
  "from_prefix": "ABSENT" | "JOURNALED" | "PROVEN" | "VALID" |
                 "LATE" | "MUTATED" | "VERIFIED",
  "kind": "hindsight-postgresql-recovery-advancement-observation",
  "reconciliation_subject": EvidenceRef | "NONE",
  "recovery_request_id": Id,
  "reservation": EvidenceRef,
  "result_body": EvidenceRef,
  "schema_version": 1,
  "to_prefix": "ABSENT" | "JOURNALED" | "PROVEN" | "VALID" |
               "LATE" | "MUTATED" | "VERIFIED",
  "transition": "J_CREATED" | "P_CREATED" | "R_VALID_CREATED" |
                "R_LATE_CREATED" | "M_CREATED" | "V_CREATED" |
                "CONCLUSIVE_NONCOMMIT_RECORDED",
  "transaction_identity": EvidenceRef,
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest
}

RecoveryUnprovenObservation := {
  "aggregate_identity": RecoveryAggregateIdentity,
  "authority": "NONE",
  "kind": "hindsight-postgresql-recovery-unproven-observation",
  "original_r_committed_result": OperationWorkCommittedResultEvidenceRef,
  "original_r_conclusive_noncommit_result": OperationWorkConclusiveNoncommitResultEvidenceRef,
  "original_r_reservation": OperationWorkReservationEvidenceRef,
  "original_r_start": OperationWorkStartEvidenceRef,
  "original_r_transaction_identity": TransactionIdentityEvidenceRef,
  "original_r_work_identity": StageAttemptWorkIdentity,
  "original_r_work_identity_digest": Digest,
  "reconciliation_subject": EvidenceRef,
  "recovery_request_id": Id,
  "reservation": EvidenceRef,
  "resolution_evidence": EvidenceRef,
  "result": "UNPROVEN",
  "schema_version": 1,
  "transaction_identity": EvidenceRef,
  "work_identity": OperationWorkIdentity,
  "work_identity_digest": Digest
}

RecoveryObservation := RecoveryRefusalObservation |
                       RecoveryAmbiguityObservation |
                       RecoveryFenceObservation |
                       RecoveryAdvancementObservation |
                       RecoveryUnprovenObservation
```

Every recovery observation's reservation resolves one
`OperationWorkReservation/v1`, and its complete work identity and digest equal
that reservation byte for byte. Its `transaction_identity` resolves the exact
`TransactionIdentity/v1` named by that reservation's start and committed
result. An ambiguity observation's `subject_transaction_identity` equals the
one fixed by its `AmbiguityQueryWorkIdentity`. A fence or unproven observation
requires the exact `ReconciliationSubject/v1` fixed by its work identity. An
advancement or refusal uses that subject exactly for reconciliation work and
literal `"NONE"` for a stage attempt. Every comparison with protected recovery
state resolves and recomputes these complete bodies and rejects a digest-only,
reconstructed, cross-request, or cross-subject value.

An unproven observation's work identity has
`reconciliation_kind=PUBLICATION_QUALIFICATION_ATTEMPT_RECONCILIATION` and its
subject has literal `subject_kind=PUBLICATION_QUALIFICATION_ATTEMPT`. The
subject's reservation, start, transaction identity, complete stage-attempt
work identity, work-identity digest, committed-`J` aggregate, and literal `R`
stage equal the observation's `original_r_reservation`, `original_r_start`,
`original_r_transaction_identity`, `original_r_work_identity`,
`original_r_work_identity_digest`, and aggregate byte for byte. Its
`original_r_committed_result` resolves the already committed result for that
exact original reservation and has `result_kind=CONCLUSIVE_NONCOMMIT`; its
`result` equals `original_r_conclusive_noncommit_result`. The referenced
`OperationWorkConclusiveNoncommitResult/v1` is the exact terminal result
created by the sole conclusive-close transaction: its original reservation,
start, transaction, work identity, digest, subject, and recovery request equal
this same `R` chain. The committed-result binding's plan, reservation, start,
transaction, work identity, and digest also equal that chain. The original
slot must already be `COMMITTED`, and both referenced bodies must already be
immutable, before the publication-qualification reconciliation can reserve or
start. The
observation's work identity then repeats that digest in
`original_work_identity_digest`. Campaign, run, fixture, or evidence-tier
coordinates cannot occupy this publication-attempt subject.

The authoritative primary stable key for every union member is `reservation`;
a second kind or changed body for that reservation conflicts. The reservation
relation's unique `(plan, work_identity_digest)` constraint is a secondary
integrity constraint, not another observation identity. Distinct charged
reservations therefore store distinct observations even when their aggregate,
recovery request, code, stage, or transition fields are equal. These guarantees
apply only after reservation. Pre-reservation refusal is exclusively the
separately keyed `OperationWorkPreReservationRefusal/v1` contract.

The observation becomes replayable only when the same transaction stores it as
the `result` of the unique `OperationWorkCommittedResult/v1` for that
reservation. For `J_CREATED` through `V_CREATED`, `result_body` names the exact
stage body inserted with the observation and the committed result names only
the observation. `CONCLUSIVE_NONCOMMIT_RECORDED` names the exact terminal
noncommit body. Exact stage replay is returned by the read-only preflight and
creates no observation. A bare observation, a committed result for another
identity, or an observation whose kind is not permitted by its exact
transaction-resolution, ambiguity-query, or reconciliation identity is never
a free replay.

`RecoveryAggregateIdentity` is discriminated by the observed durable prefix.
When `J` exists it is exactly `COMMITTED_J` with
`journal_digest=digest(J)`. When the prefix is `ABSENT`, it is exactly
`ABSENT_REQUEST`; `request_key_digest` is SHA-256 over the successor-canonical
bytes, including the LF, of this closed projection:

```text
{
  "action": body(plan).action_binding.action,
  "operation_identity": body(plan).operation_identity,
  "plan_body_digest": plan.body_digest,
  "publication_epoch": body(plan).publication_epoch
}
```

An `ABSENT` observation cannot carry a journal digest, and an observation for
any later prefix cannot carry a request-key identity. `J_CREATED` retains the
absent request identity for its `from_prefix=ABSENT` observation and names the
new journal through `result_body`; subsequent observations use its committed
journal identity.

No body contains its own digest. `digest(J)` is also the aggregate-binding
digest. Each later body's digest is its stage-binding digest. Exact predecessor
fields form this digest graph:

```text
J: no predecessor digest
P: journal_digest
R: journal_digest, proof_digest
M: journal_digest, proof_digest, deadline_receipt_digest
V, mismatch, unable observation, or terminal failure:
    journal_digest, proof_digest,
    deadline_receipt_digest, mutation_receipt_digest
```

These predecessor digest fields are intrinsic stage-to-stage links. This
grammar fixes the exact kind and version of every named predecessor and the
complete digest preimage is that predecessor's canonical body, including its
LF. They therefore remain digests rather than cross-contract `EvidenceRef`
values.

The protected `M` transaction canonicalizes the complete body, computes its
digest, inserts those exact bytes and digest, and atomically installs that
digest as the new lineage head. Parsed columns are constrained projections
only. Exact replay requires byte equality; the same stable stage identity with
different canonical bytes is `CONFLICT`.

The journal's `plan`, `approval`, and `authorization_receipt` are typed
authority references; every action binding's `rollback_preimage_binding` is a
typed nonauthorizing input reference. Their complete current bodies, nested
references, and applicable protected authority or candidate records must verify
under the exact `OperationPlan/v1`, `OperationApproval/v1`,
`OperationAuthorizationReceipt/v1`, and `RollbackPreimageBinding/v1` grammars
registered here. The preimage reference must also equal the journal's atomic
protected adoption. The
approval and authorization receipt
must name the exact plan, and that plan's operation identity, target, proposed
epoch, cohorts, expected generation, and complete discriminated action binding
must equal `J` byte for byte. The grant, plan, approval, and authorization
deadlines, `J.approval_expiry_unix_ns`, every pre-stage observation, and
`R.approval_expiry_unix_ns` are one exact value. An alias, obsolete version,
digest-only lookup, equivalent caller projection, independently later
deadline, or action-specific predecessor supplied outside the plan is invalid.

The journal stable conflict key is exactly `(operation_identity,
action_binding.action, plan.body_digest, admission.publication_epoch)`. The
same key and byte-identical `J` replay the existing aggregate; the same key
with any different canonical `J` bytes is `CONFLICT`. Changing the plan
reference's body digest or publication epoch creates a different key and still
requires a new approval.

Canonical syntax alone does not make a chain valid. These cross-stage
relationships are normative:

| Relationship | Required invariant |
| --- | --- |
| Digest graph | Every predecessor field equals the digest of the exact named body. A repeated or substituted digest is invalid. |
| Aggregate | Every later stage transitively binds the exact `J`. Any repeated operation, action, plan reference, approval reference, authorization-receipt reference, target, surface, cohort, or preimage reference equals `J` byte for byte. |
| Admission | `J`, `P`, `R`, and `M` retain one admission mode and publication epoch. Every deployment-attestation reference names exact complete bytes for the locked, current, unexpired `DeploymentAttestation/v1`; its policy reference equals the protected current deployment-policy reference; its complete deployment-tier partition and qualification receipt's complete design-, implementation-, and release-tier partitions and their prerequisite references equal the protected current `PASS` results; its controller-host, PostgreSQL-host, endpoint, and topology references equal the current support profile and live deployment; and its proposed epoch equals the protected active epoch and stage epoch. `FRESH` contains the exact `"NONE"` marker and the protected fixed target-surface slot proves no active legacy-fence binding under any epoch. Whenever that slot is occupied, `COMPATIBILITY` contains exactly one current `OriginFenceManifestBinding/v1` or `ActiveFenceManifestAdoption/v1` reference and separately proves that binding's `reserved_publication_epoch` is now the protected active stage epoch. The handoff's persistent-fence-evidence digest resolves the exact current epoch-independent body, whose target, surface, writer proposal, realized admission, realized ACL, zero-live-writer evidence, drain generation, and service-disable evidence all match the continuously closed live barrier. A reserved-but-not-active, stale, revoked, policy-noncurrent, evidence-noncurrent, topology-mismatched, lower, noncurrent-kind, noncurrent-version, cross-mode, or cross-epoch value is invalid. |
| Apply predecessor | An apply `J` binds genesis or the exact current verified lineage head. Its action binding contains no rollback predecessor. |
| Successor rollback predecessor | The action binding's predecessor apply `M` and `V` equal the non-genesis `J.lineage` head and verification. |
| Legacy rollback predecessor | `J.lineage` is exact genesis. The typed final-manifest and manifest-approval references name the authenticated compatibility manifest and its approval. Its selected complete `LegacyPredecessor` object recomputes to the intrinsic `legacy_predecessor_selection_digest`, including the reader-contract-set digest and encrypted-preimage reference. The rollback plan, `J.expected_target_generation`, protected initialized successor target-generation slot, and current live target all equal the manifest's canonical `TargetState.generation`; that state's snapshot digest recomputes from the one bridged `TargetMutationImage/v1`. The generation-free preimage binding independently recomputes the historical source, `LegacyRestoreContent/v1`, deterministic conversion, and selected-content `TargetRestorePayload/v1`. No successor `M` or `V` is synthesized. |
| Lineage before `M` | `J.lineage`, `P.lineage`, `R.lineage`, and `M.lineage_before` are identical. A sibling advance makes the pending body invalid rather than rebasing it. |
| Lineage after `M` | `M.new_lineage_generation` is exactly `M.lineage_before.lineage_generation + 1`. The transaction installs `digest(M)` as the new head. |
| Target generation | Every field uses `TargetGeneration`. `M.pre_target_generation` equals `J.expected_target_generation`; `M.post_target_generation` is exactly one greater under checked unbounded arithmetic and must remain in range. Selected and preserved cohort digests equal `J`. |
| Mutation images and content payloads | The before image is exactly `TargetMutationImage/v1` with `target_database_identity=J.target_database_identity`, `target_surface_digest=J.target_surface_digest`, `lineage_key_digest=M.lineage_before.lineage_key_digest`, `target_generation=M.pre_target_generation`, the selected and preserved membership digests from `M`, and both complete locked pre-mutation cohort projections. The after image uses the same target, surface, lineage key, and memberships with `target_generation=M.post_target_generation` and both complete locked post-mutation projections. `M.before_image_digest` and `M.after_image_digest` are SHA-256 over those respective successor-canonical bodies including their LF. The rollback binding has no generation or mutation-image digest. It resolves exact protected ciphertext bytes, decrypted source plaintext digest and length, one exact source typed body, deterministic `RestorePayloadConversion/v1`, and one `TargetRestorePayload/v1`; every payload and conversion digest is independently recomputed. Apply independently resolves its exact `TargetApplyPayload/v1`, requires both content payloads and the locked before image to have identical selected membership, preserves the locked preserved projection, substitutes only the apply payload's selected values, increments generation once, and derives the after image and digest. Rollback requires the locked before selected membership to equal the restore payload membership, leaves the before preserved projection byte-identical, substitutes the restore payload selected content, increments generation once, and derives the after image and digest. A successor rollback retains the predecessor apply rollback-preimage binding and byte adoption; a legacy rollback converts its historical no-LF wire plaintext through exact `LegacyRestoreContent/v1` and never treats those bytes as successor JSON. No earlier-generation image equality, alternate payload, unbound apply transformation, lossy conversion, descriptor-only value, or private-file fallback is accepted. |
| Deadline | The operation grant, plan, approval, authorization receipt, `J`, both pre-stage observations, and `R` carry one exact expiry. `J` and a newly created `P` each bind an exact same-transaction `PreStageExpiryObservation/v1` with `qualification=CURRENT`; its clock-envelope reference equals the exact reference in the locked deployment attestation, and every recorded arithmetic value recomputes exactly from those body bytes. `R` repeats the same expiry and arithmetic contract after `P`; equality is late at every comparison. After a durable timely `R`, `M` rechecks the grant, plan, approval, and authorization receipt only for exact identity, current-slot equality, and nonrevocation and does not compare current time with their shared deadline. |
| Incarnation capability | `M.incarnation_capability_digest` equals the digest in the locked current admission state and the digest recomputed from the witness on the exact activation-bound session. A proposal, state, witness, session, or `M` mismatch refuses before mutation. |
| Verification | `V`, mismatch, unable observations, and terminal failures bind the exact `M` chain and stable attempt. In `V` and mismatch, expected target generation, selected cohort, preserved cohort, and postimage equal `M`, and observed lineage key, generation, and head equal `J` and `M`. The verifier reconstructs the complete expected and observed `TargetMutationImage/v1` bodies, canonicalizes each with one LF, and requires both stored postimage digests to equal its independently recomputed SHA-256 values. `V` is valid only when every observed target and lineage projection equals its expected value. Mismatch is valid only when target identity and lineage are authenticated and at least one target projection differs. A retryable read or expected-state failure uses unable; invariant or target-identity failure uses the terminal body. Neither failure form carries a fabricated observation. |

A body that violates any relationship is invalid; it is not an alternative
canonical representation.

Let `C` be the exact capability octets generated for one activation, with
`length(C) >= 32`, and define `capability_digest(C) = SHA-256(C)`. Let `W` be
the exact octets in the session-local, non-WAL witness on continuity session
`S`, and let `A` be the durable admission-state row locked by combined
activation and later by `M`. Combined activation is valid only when:

```text
EpochActivationProposal.incarnation_capability_digest = capability_digest(C)
W = C
A.continuity_session_id = S
A.incarnation_capability_digest = capability_digest(W)
```

The protected `M` function must run on `S`, read `W` from that backend's
session-local witness, recompute the digest, and enforce:

```text
M.incarnation_capability_digest
    = A.incarnation_capability_digest
    = capability_digest(W)
    = capability_digest(C)
```

No caller-supplied digest or capability projection can satisfy the comparison.
Independent one-field negative vectors change, in turn, the proposal digest,
locked admission digest, one witness octet, continuity-session identity, and
`M.incarnation_capability_digest`, recompute every affected outer canonical
body and digest, and prove that `OR-ID`, `OR-FENCE`, and `OR-CAP` reject before
target, receipt, generation, or lineage mutation.

For `T` in `{V, MismatchObservation}`, these exact equalities are required:

```text
T.journal_digest = digest(J)
T.proof_digest = digest(P)
T.deadline_receipt_digest = digest(R)
T.mutation_receipt_digest = digest(M)
T.target_database_identity = J.target_database_identity
T.expected_target_generation = M.post_target_generation
T.expected_selected_cohort_digest = M.selected_cohort_digest
T.expected_preserved_cohort_digest = M.preserved_cohort_digest
T.expected_postimage_digest = M.after_image_digest
T.observed_lineage_key_digest = M.lineage_before.lineage_key_digest
T.observed_lineage_generation = M.new_lineage_generation
T.observed_lineage_head_m_digest = digest(M)
```

For `F` in `{UnableObservation, TerminalVerificationFailure}`, these exact
equalities are required:

```text
F.journal_digest = digest(J)
F.proof_digest = digest(P)
F.deadline_receipt_digest = digest(R)
F.mutation_receipt_digest = digest(M)
F.target_database_identity = J.target_database_identity
```

`V` is valid exactly when its four observed target projections equal their four
expected projections. `MismatchObservation` is valid exactly when target and
lineage identity have been authenticated and at least one observed target
projection differs. A mismatch whose four target projections all match is
invalid. `UnableObservation` is valid only for one listed retryable category
whose typed failure evidence proves that category without proving mismatch,
invariant failure, or target-identity failure. A different or unprovable
database identity uses terminal `TARGET_IDENTITY_UNPROVEN`; a different or
unprovable lineage key, lineage generation, lineage head, impossible stage
shape, or target/receipt split uses terminal `INVARIANT_VIOLATION`.

The stable attempt key is `(digest(M), verification_attempt_id)`: exact replay
requires byte equality, and different bytes under the same key are `CONFLICT`.
The protected aggregate terminal slot permits exactly one of `V`, mismatch, or
`TerminalVerificationFailure`; the first committed terminal body permanently
excludes the other two for that aggregate. Unable observations use the same
attempt key but never occupy the terminal slot. After a terminal body exists,
a new attempt returns that exact terminal outcome without reading a new result.

The recovery-observation union is closed and nonauthorizing. A refusal's
`evidence` names `FailureEvidence/v1`; its evidence `failure_code` equals the refusal
code after replacing every ASCII `_` with `-` and converting `A` through `Z`
to lowercase, and its source identity proves that exact refusal. An ambiguity
resolves the exact original and query `TransactionIdentity/v1` bodies and
bounded resolution deadline. A fence names the exact attestation and
reconciliation subject that lost continuity. An advancement's `result_body`
names the exact created `J`, `P`, `R`, `M`, or `V` selected by its transition.
The exact
created mappings are `J_CREATED: ABSENT->JOURNALED`,
`P_CREATED: JOURNALED->PROVEN`, `R_VALID_CREATED: PROVEN->VALID`,
`R_LATE_CREATED: PROVEN->LATE`, `M_CREATED: VALID->MUTATED`, and
`V_CREATED: MUTATED->VERIFIED`; the result kind is the named stage and an `R`
also has the named qualification. `CONCLUSIVE_NONCOMMIT_RECORDED` requires
equal prefixes; its `result_body`
names the exact `OperationWorkConclusiveNoncommitResult/v1` atomically bound as
the original started reservation's terminal result, and its own work identity,
transaction identity, and subject are the separately charged
`CONCLUSIVE_NONCOMMIT` reconciliation named by that body. Exact replay is the
preflight's byte-identical committed result and creates no observation. No
other pair or result kind is valid. An unproven observation's
`resolution_evidence` names `FailureEvidence/v1` with exact failure code
`qualification-attempt-conclusively-absent` and proves that no authorizing `R`
exists after the original attempt named by its exact
publication-qualification subject has already been closed. The observation
must carry that exact original reservation, start, transaction identity,
complete `StageAttemptWorkIdentity`, and digest, plus the exact preexisting
`OperationWorkCommittedResult/v1` whose `result_kind=CONCLUSIVE_NONCOMMIT` and
whose result is the carried
`OperationWorkConclusiveNoncommitResult/v1`. The protected reservation and
result transactions independently resolve and lock both bodies, validate the
complete original chain and prior `COMMITTED` state, and reject an empty result
key, a still-`STARTED` original, any other result kind, or a terminal body
created outside the sole conclusive-close mechanism. The conclusive close
commits before the `UNPROVEN` reservation; the latter transaction writes only
its own observation, committed-result binding, and slot close. Its
`original_r_work_identity_digest` equals the subject's
`original_work_identity_digest` and the separately charged reconciliation
identity's `original_work_identity_digest`; it never names an evidence
campaign or qualification run.

An original result of `R_VALID` or `R_LATE` selects the committed-`R` recovery
path and makes an `UNPROVEN` reservation invalid. After conclusive close and
before terminal conditions, a new fully charged `R` attempt remains eligible
when the ordinary authority and plan ceilings permit it. Once `UNPROVEN`
commits, its own reservation has exactly one `RECOVERY_OBSERVATION` result and
the original reservation retains exactly its one `CONCLUSIVE_NONCOMMIT`
result. Exact retries return those existing committed bindings. If the
publication-qualification reconciliation is itself left `STARTED`, another
separately charged resolver closes its exact `RECONCILIATION` transaction
subject through the existing result-resolution mechanism; it cannot alter the
closed original `R` or create a competing `UNPROVEN` result.

An `OperationWorkConclusiveNoncommitResult/v1` is valid only when its
`resolution_evidence` names `FailureEvidence/v1` with failure code
`operation-work-conclusively-not-committed`; the evidence proves that the
original transaction cannot commit and that its result key is empty under the
same locks. Its original fields equal the locked original reservation and
start, original transaction identity, and transaction reconciliation subject.
Its resolution fields equal the locked reconciliation reservation, start, and
transaction identity, whose work identity has
`reconciliation_kind=CONCLUSIVE_NONCOMMIT`, names that same subject and
original work-identity digest, and uses the same recovery request. Any
pre-existing original result, unresolved transaction outcome, mismatched plan,
identity, subject, start, request, or evidence refuses the entire close.

Every recovery-observation kind uses its `reservation` reference as its sole
primary stable key. Aggregate, request, refusal, stage, transaction, fence,
transition, and publication-qualification-attempt fields are validated
content, never
alternate keys or uniqueness constraints. Exact retry of one reservation
returns its byte-identical body; different bytes for that reservation
conflict, while distinct charged reservations remain distinct results. The
protected recovery interface stores each body immutably in the queryable
append-only evidence relation. A successful stage advancement stores its
observation in the same transaction as the named stage. A
`CONCLUSIVE_NONCOMMIT_RECORDED` advancement stores its observation, terminal
noncommit result, both committed-result bindings, and both slot closes in the
one transaction specified above. Refusal, ambiguity, fence, and `UNPROVEN`
observations store only after the protected verifier establishes their exact
result. Pre-reservation refusal uses its request-keyed body instead and never
enters this reservation-keyed relation.

Every union member has `authority="NONE"`. It may explain a refusal, report
ambiguity or a fence, or point to authority created elsewhere, but it cannot
replace or satisfy `J`, `P`, `R`, `M`, `V`, a deployment attestation, an active
epoch, a lineage head, a fence binding, or any stage-admission predicate. A
status projection, generic log, caller assertion, unknown observation kind, or
future schema has no recovery or mutation authority.

Before implementation begins, the implementation map must materialize these
bodies as versioned schemas and provide a field-by-field trace to the accepted
semantic requirements. The contract requires independent canonical vectors for
every body kind, action, boundary value, rejected alias, and one-field
perturbation. One vector producer may implement the canonical algorithm, but
the deciding vector oracle must be independently implemented and must not call
the production serializer.

Historical bytes do not migrate to this contract. Compatibility bodies keep
`hindsight-postgresql-compatibility-canonical-json/v1`; stopped progress keeps
its no-LF raw representation and separate semantic digest; every other frozen
reader retains its source-pinned representation. A successor implementation
must add a named serializer rather than silently changing the shared historical
helper.

`JAC-CAN-01` therefore has two independent vector families. The successor
family covers every `J`, `P`, `R`, `M`, `V`, verification-observation, evidence,
qualification, and deployment body registered here. The compatibility family
covers manifest basis and final manifest, artifact exclusion and approval,
closure case, observation, qualified-sample, attested-invalidation,
comparison, and failure evidence, realized admission and ACL evidence,
zero-live-writer and service-disable evidence, persistent legacy-fence
evidence, origin fence binding, active fence adoption, and every nested
set-member body that contributes to their
digests. Both families exercise every scalar boundary, set and sequence order,
escape rule, trailing LF, wrong-family serializer, and one-field perturbation
through `EV-VEC` and `OR-ID`.

The successor vectors include paired registries whose claim IDs and planned
runs are identical while one canonical definition obligation, required-tier
binding, predicate run, oracle requirement, or expected tagged field differs;
only the body with the exact registered definition and predicate sequence is
accepted. They execute that same predicate through plan expansion, expected
and observed projections, run-result registration, and tier evaluation and
reject an implementation-selected field subset or expression. Separate
vectors prove that a transaction-resolution and an ambiguity-query reservation
each close with its typed original-committed outcome and exact original result,
including recursive resolution of a resolver, without inserting the original
stage twice. Publication `UNPROVEN` vectors accept only the exact reserved and
started `R` attempt subject and its preexisting exact
`CONCLUSIVE_NONCOMMIT` committed-result and terminal-result pair from the sole
conclusive close. They reject a still-`STARTED` original, an empty or different
result, `R_VALID`, `R_LATE`, mismatched original identities, reversed commit
order, or any attempt to write the original result with `UNPROVEN`. They also
cover replacement `R` after conclusive close but before terminal conditions,
byte-identical replay of both reservations, and independent closure and retry
when the `UNPROVEN` reconciliation itself is left `STARTED`. Apply
vectors include unequal before and desired selected values with identical
membership and require the desired after projection to commit; they reject a
membership change or any after value that differs from the payload. Historical
vectors include the kindless `requeue-plan` member, exact member count and
digests, every member-to-tool execution binding, and the terminal
`CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY` success projection. Oracle-registry
vectors require `JAC-CUT-01` in `OR-ACL` at its displayed claim-registry
position and reject its omission or reordering.

## Exact clock arithmetic

Every successor wall and monotonic time value is an integer count of
nanoseconds encoded as `UInt128String`, never as a JSON number or floating
point. Arithmetic is defined over unbounded mathematical nonnegative integers.
The UInt128 range constrains canonical inputs and every named encoded result;
it does not constrain transient products, sums, quotients, or remainders.
Whole-second authenticated inputs convert by exact multiplication by
1,000,000,000, and the converted named result must fit UInt128. Any other unit
converts through exact rational arithmetic. Exact values map exactly; lower
bounds round downward, while upper bounds and uncertainty contributions round
upward. A negative result, zero denominator, noncanonical input, unbounded
conversion uncertainty, or named encoded result outside UInt128 is invalid and
cannot produce `VALID`.

The clock envelope records:

- `monotonic_anchor_lower_ns`;
- `monotonic_validity_deadline_lower_ns`;
- `wall_upper_at_anchor_unix_ns`, already including every fixed synchronization
  and conversion error known at issuance; and
- positive `forward_rate_error_denominator` and nonnegative
  `forward_rate_error_numerator`.

The qualified clock profile must establish all of these inequalities at
envelope issuance and preserve them for every admitted sample:

```text
true_wall_at_anchor_ns <= wall_upper_at_anchor_unix_ns

actual_elapsed_since_anchor_ns <=
    elapsed_upper_ns + forward_rate_error_upper_ns

true_wall_at_sample_ns <=
    true_wall_at_anchor_ns + actual_elapsed_since_anchor_ns
```

`monotonic_anchor_lower_ns` is a lower bound on the anchor reading and
`monotonic_sample_upper_ns` is an upper bound on the sample reading, including
sampling and unit-conversion uncertainty. The rate-error ratio is the
qualified maximum additional actual elapsed time per measured upper-bound
elapsed time, including the permitted slow-clock error. These inequalities
make the computed `trusted_upper_bound_unix_ns` a mathematical upper bound on
true wall time. If the selected source can regress or reset, its suspend
semantics are unknown, the synchronization epoch changes, a boot or host
identity changes, the rate bound is exceeded, or any required inequality is
uncertain, the envelope is invalid and the epoch is fenced.

The rate-error fraction is reduced to lowest terms. Zero is encoded only as
`0/1`; aliases such as `1/2` and `2/4` are not the same canonical evidence.

The exact computation is:

```text
require monotonic_sample_upper_ns >= monotonic_anchor_lower_ns
require monotonic_sample_upper_ns < monotonic_validity_deadline_lower_ns

elapsed_upper_ns =
    monotonic_sample_upper_ns - monotonic_anchor_lower_ns

forward_rate_error_upper_ns = ceil_mul_div(
    elapsed_upper_ns,
    forward_rate_error_numerator,
    forward_rate_error_denominator
)

trusted_upper_bound_unix_ns =
    wall_upper_at_anchor_unix_ns
    + elapsed_upper_ns
    + forward_rate_error_upper_ns
```

Native-to-nanosecond lower bounds round downward. Upper bounds and every
uncertainty or error contribution round upward. `ceil_mul_div(a, n, d)` is
evaluated exactly over unbounded integers: require `d > 0`, compute
`q = (a * n) div d` and `r = (a * n) mod d`, and return `q` when `r = 0` or
`q + 1` otherwise. The transient product may exceed UInt128. Every canonical
input and every named encoded result—including converted bounds,
`elapsed_upper_ns`, `forward_rate_error_upper_ns`, and
`trusted_upper_bound_unix_ns`—must fit UInt128. A negative subtraction or
out-of-range named result invalidates the clock evidence, creates no
authorizing `R`, and fences the epoch.

Closure reservation tests include these exact fractional split-rounding
vectors. `a` is measured reservation elapsed time and `q` is the measured
maximum resolution duration, both in nanoseconds:

| `a` | `q` | `n/d` | `ceil(a*n/d)` | `ceil(q*n/d)` | `ceil((a+q)*n/d)` | Required `q_margin` |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 1/2 | 1 | 1 | 1 | 2 |
| 1 | 2 | 2/3 | 1 | 2 | 2 | 4 |

In each vector, the two separately rounded rate errors exceed the combined
rounding by one nanosecond. The reservation candidate must therefore subtract
`q_margin = q + ceil_mul_div(q, n, d)` from each wall-expiry safe horizon;
subtracting raw `q` is invalid. The clock-validity candidate remains a
measured monotonic bound and subtracts raw `q`.

Only `trusted_upper_bound_unix_ns < approval_expiry_unix_ns` is `VALID`.
Equality is expired and records `LATE`; every greater value is also `LATE`.
Equality with the monotonic validity deadline is invalid. No wall-clock sample
after envelope issuance enters the authority calculation. An original timely
`R` transaction may commit or be recovered after expiry, but a missing or
aborted receipt is never recreated from an earlier diagnostic time.

Immediately before starting `J` or a new `P`, the protected stage transaction
records one closed `PreStageExpiryObservation/v1` from a fresh qualified sample.
It applies the same checked arithmetic above. `qualification=CURRENT` exactly
when `trusted_upper_bound_unix_ns < approval_expiry_unix_ns`; equality and every
greater value are `LATE`. This is a protected pre-commit start decision, not a
claim about macOS scheduling latency, PostgreSQL commit latency, or client
acknowledgement time. The profile carries no fictitious hard bound from sample
to acknowledged commit.

For `stage=J`, `stage_predecessor_digest` is `"NONE"`; for `stage=P`, it equals
the exact `digest(J)`. The observation's plan, approval, authorization receipt,
expiry, admission, and clock envelope equal the proposed stage byte for byte.
Its stable key is `(stage, plan, approval, authorization_receipt,
admission.publication_epoch, stage_predecessor_digest,
observation_request_id)`. An exact retry returns the existing canonical body;
changed bytes under that key conflict. A committed stage exact-replay returns
its already bound observation and does not resample.

Observation and stage creation share one transaction. It locks the current
grant, plan, approval, authorization receipt, deployment policy, deployment
attestation, clock envelope, active epoch, all four current tier partitions and
their prerequisite pointers, and the attestation's complete support-profile
topology bindings through commit. It requires every reference to remain current,
unrevoked, continuous, and exact at the protected sample and through commit.
With `CURRENT`, the transaction may insert the observation and `J` or `P`
atomically even if acknowledgement occurs after expiry. With `LATE`, it inserts only the
nonauthorizing observation and refuses the stage. An invalid clock, topology,
policy, attestation, evidence partition, authority record, active-epoch, or
continuity predicate inserts neither stage nor trusted expiry observation and
returns the exact existing refusal path. This observation has `authority=NONE`:
it cannot replace `J`, `P`, or `R`, cannot authorize `M`, and cannot be reused by
another stage or request.

After commit, exact query may recover and expose the committed stage. A later
expiry, revocation, policy change, evidence change, or attestation change cannot
erase or undo that visibility. It does block every later authority-consuming
stage whose live gates no longer pass. `R` remains the only durable stage that
decides `U < expiry` for mutation authority; `J` and `P` are ordered durable
preconditions and cannot by themselves admit `M`.

## Evidence classes

| ID | Evidence class | Can support | Cannot support |
| --- | --- | --- | --- |
| `EV-DES` | Revision-pinned design trace | Requirement coverage, architecture rationale, rejected alternatives, and explicit deferrals | Executable behavior, transaction safety, or physical durability |
| `EV-REF` | Executable reference model | State transitions, prefix and time arithmetic, refusal rules | PostgreSQL concurrency, WAL durability, deployed behavior |
| `EV-VEC` | Independent canonical vectors | Exact bytes, digests, identities, perturbation sensitivity | Transactional or physical durability |
| `EV-PG` | Real disposable PostgreSQL integration | Constraints, roles, locking, isolation, atomic logical effects, restart queries | Host power-loss survival when the fixture disables or does not qualify durability |
| `EV-FLT` | Deterministic failpoint and lost-ack injection | Before-commit, committed-before-ack, timeout, retry, and restart outcomes | Physical flush honesty unless combined with `EV-PHY` |
| `EV-LEG` | Frozen-reader differential corpus | Exact historical dispatch, bytes, semantics, dependency handling, `authority=NONE` | Successor durability or mutation safety |
| `EV-ACL` | Database role and protected-interface denial tests | Mechanical separation of plan acceptance, deployment-policy administration, qualification finalization, admission, activation, publication, mutation, verification, evidence ownership, evidence authority, evidence production, closure, and fencing roles | Host or superuser compromise |
| `EV-CLK` | Exact support-profile clock qualification | Clock-envelope bounds, invalidation, suspend, reboot, synchronization, and error behavior for one exact support profile | Another profile, PostgreSQL transaction behavior, storage durability, or deployed health |
| `EV-PHY` | Cold-recovery reboot and physical power-cut qualification | Survival of acknowledged stages and atomic target effects for one exact support profile | Any other profile, permanent primary-disk loss, or universal storage guarantees |
| `EV-CAP` | Exact support-profile capability qualification | Capability entropy, bounded cleartext domains, controllable zeroization, exclusion from durable or ambient surfaces, session witness, and fail-closed continuity behavior for one exact support profile and release | Another profile or release, forensic erasure, host or superuser compromise, or current deployed health |
| `EV-DEP` | Deployment attestation and admission exercise | Exact installed release/profile match and current health | Qualification of an untested profile or authorization of an operation |

## Oracle registry

Every evidence record names one or more immutable oracle definitions from the
one `CanonicalOracleRegistry/v1`. The following table is the complete registry
source. In `Fields`, each comma-delimited `name:VALUE_KIND` expands in displayed
order to exactly one `OracleFieldRequirement`; in `Claims`, each claim expands
in claim-registry order to one `OracleClaimObligation` with its exact canonical
claim-definition reference, complete required-tier predicate-reference
sequence, and the two literal rules in the grammar. Those expansions,
`definition_version=1`, the displayed
`OracleId`, `kind`, and `schema_version` are the complete
`OracleDefinition/v1` body. There are no implicit fields or obligations.

| ID | Exact ordered fields | Exact claim obligations |
| --- | --- | --- |
| `OR-TRACE` | `claim_registry:EVIDENCE_REF`, `requirement_entries:EVIDENCE_REF_SEQUENCE`, `matrix_rows:EVIDENCE_REF_SEQUENCE`, `tier_assignments:EVIDENCE_REF_SEQUENCE`, `limit_bindings:EVIDENCE_REF_SEQUENCE`, `unmapped_requirement_count:SAFE_INTEGER`, `contradiction_count:SAFE_INTEGER` | `JAC-ARC-01` |
| `OR-ID` | `canonical_bodies:EVIDENCE_REF_SEQUENCE`, `canonical_body_digests:DIGEST_SEQUENCE`, `canonical_byte_lengths:SAFE_INTEGER_SEQUENCE`, `stable_key_digests:DIGEST_SEQUENCE`, `nested_bindings:EVIDENCE_REF_SEQUENCE`, `transaction_identities:EVIDENCE_REF_SEQUENCE`, `reconciliation_subjects:EVIDENCE_REF_SEQUENCE`, `work_requests:EVIDENCE_REF_SEQUENCE`, `target_surface_contract:EVIDENCE_REF`, `target_state_bodies:EVIDENCE_REF_SEQUENCE`, `artifact_provenance:EVIDENCE_REF_SEQUENCE`, `socket_directory_bindings:EVIDENCE_REF_SEQUENCE` | `JAC-CAN-01`, `JAC-ID-01`, `JAC-PG-01`, `JAC-CLO-01`, `JAC-CAP-01`, `JAC-DUR-01` |
| `OR-PFX` | `aggregate_identity:RECOVERY_AGGREGATE_IDENTITY`, `ordered_stage_bodies:EVIDENCE_REF_SEQUENCE`, `durable_prefix:ENUM_TOKEN`, `terminal_observation:EVIDENCE_REF_OR_NONE`, `impossible_hole_count:SAFE_INTEGER`, `ambiguous_transactions:EVIDENCE_REF_SEQUENCE`, `committed_results:EVIDENCE_REF_SEQUENCE` | `JAC-ID-01`, `JAC-ORD-01`, `JAC-TIM-01`, `JAC-AMB-01`, `JAC-RST-01`, `JAC-VER-01` |
| `OR-NEXT` | `aggregate_identity:RECOVERY_AGGREGATE_IDENTITY`, `observed_prefix:ENUM_TOKEN`, `committed_replay:EVIDENCE_REF_OR_NONE`, `pre_reservation_refusal:EVIDENCE_REF_OR_NONE`, `permitted_transition:ENUM_TOKEN_OR_NONE`, `reconciliation_subject:EVIDENCE_REF_OR_NONE`, `replacement_permitted:BOOLEAN`, `terminal_refusal:ENUM_TOKEN_OR_NONE` | `JAC-ORD-01`, `JAC-AMB-01`, `JAC-RST-01`, `JAC-FEN-01`, `JAC-LEG-01`, `JAC-CLO-01` |
| `OR-TIME` | `clock_envelope:EVIDENCE_REF`, `protected_samples:EVIDENCE_REF_SEQUENCE`, `integer_inputs:TEXT_SEQUENCE`, `rounded_terms:TEXT_SEQUENCE`, `trusted_upper_bound_unix_ns:UINT128`, `validity_deadline_monotonic_ns:UINT128`, `expiry_unix_ns:UINT128`, `strict_result:ENUM_TOKEN` | `JAC-TIM-01`, `JAC-CLO-01`, `JAC-CLK-01` |
| `OR-TGT` | `target_database_identity:EVIDENCE_REF`, `target_surface_contract:EVIDENCE_REF`, `before_image:EVIDENCE_REF`, `before_image_digest:DIGEST`, `apply_payload:TARGET_APPLY_PAYLOAD_REF_OR_NONE`, `apply_payload_digest:DIGEST_OR_NONE`, `restore_payload:EVIDENCE_REF`, `restore_payload_digest:DIGEST`, `restore_conversion:EVIDENCE_REF`, `after_image:EVIDENCE_REF`, `after_image_digest:DIGEST`, `selected_cohort:EVIDENCE_REF`, `preserved_cohort:EVIDENCE_REF`, `allowed_delta:TARGET_ALLOWED_DELTA`, `observed_postimage:EVIDENCE_REF` | `JAC-RST-01`, `JAC-LIN-01`, `JAC-EFX-01`, `JAC-VER-01`, `JAC-RBK-01`, `JAC-PRS-01` |
| `OR-LIN` | `lineage_key_body:EVIDENCE_REF`, `lineage_key_digest:DIGEST`, `predecessor_generation:SAFE_INTEGER`, `predecessor_head:DIGEST_OR_NONE`, `predecessor_verification:EVIDENCE_REF_OR_NONE`, `new_generation:SAFE_INTEGER`, `new_head:DIGEST`, `drift_result:ENUM_TOKEN` | `JAC-LIN-01`, `JAC-EFX-01`, `JAC-VER-01` |
| `OR-FENCE` | `publication_epoch:SAFE_INTEGER`, `admission_generation:SAFE_INTEGER`, `deployment_attestation:EVIDENCE_REF`, `adapter_continuity:EVIDENCE_REF`, `session_witness:EVIDENCE_REF_OR_NONE`, `legacy_writer_fence:EVIDENCE_REF_OR_NONE`, `fence_state:EVIDENCE_REF`, `continuity_result:ENUM_TOKEN` | `JAC-PG-01`, `JAC-FEN-01`, `JAC-CLO-01`, `JAC-CUT-01`, `JAC-ACL-01`, `JAC-CAP-01`, `JAC-CLK-01`, `JAC-DUR-01` |
| `OR-EVID` | `fixture_prestate:EVIDENCE_REF_OR_NONE`, `before_evidence:EVIDENCE_REF_SEQUENCE`, `after_evidence:EVIDENCE_REF_SEQUENCE`, `allowed_new_evidence:EVIDENCE_REF_SEQUENCE`, `unaffected_identity_digests:DIGEST_SEQUENCE`, `original_committed_result:EVIDENCE_REF_OR_NONE`, `original_conclusive_noncommit_result:EVIDENCE_REF_OR_NONE`, `resolution_committed_result:EVIDENCE_REF_OR_NONE`, `protected_ciphertext:EVIDENCE_REF_OR_NONE`, `retention_state:ENUM_TOKEN` | `JAC-ORD-01`, `JAC-AMB-01`, `JAC-RST-01`, `JAC-EVL-01`, `JAC-VER-01`, `JAC-RBK-01`, `JAC-CLO-01`, `JAC-CUT-01`, `JAC-PRS-01` |
| `OR-EVAL` | `campaign_plan:EVIDENCE_REF`, `oracle_registry:EVIDENCE_REF`, `claim_registry:EVIDENCE_REF`, `claim_definitions:EVIDENCE_REF_SEQUENCE`, `claim_predicates:EVIDENCE_REF_SEQUENCE`, `resolved_contract_bodies:EVIDENCE_REF_SEQUENCE`, `expanded_required_runs:EVIDENCE_REF_SEQUENCE`, `registered_run_results:EVIDENCE_REF_SEQUENCE`, `omitted_run_ids:CONTRACT_ID_SEQUENCE`, `invalidations:EVIDENCE_REF_SEQUENCE`, `supersessions:EVIDENCE_REF_SEQUENCE`, `staleness_inputs:EVIDENCE_REF_SEQUENCE`, `current_pointer_before:EVIDENCE_REF_SEQUENCE`, `current_pointer_after:EVIDENCE_REF_SEQUENCE`, `derived_tier_results:EVIDENCE_REF_SEQUENCE`, `qualification_receipt_eligibility:BOOLEAN` | `JAC-EVL-01` |
| `OR-LEG` | `reader_registry:EVIDENCE_REF`, `reader_registry_digest:DIGEST`, `reader_registry_vector_digest:DIGEST`, `reader_registry_member_digest:DIGEST`, `reader_contract_id:TEXT`, `reader_execution_binding:EVIDENCE_REF`, `reader_output:LEGACY_READER_OUTPUT_REF`, `dependency_graph:EVIDENCE_REF_SEQUENCE`, `historical_disposition:ENUM_TOKEN`, `real_artifact_provenance:EVIDENCE_REF_OR_NONE`, `closure_result:EVIDENCE_REF_OR_NONE`, `rollback_eligibility:BOOLEAN`, `authority:ENUM_TOKEN` | `JAC-LEG-01`, `JAC-CUT-01` |
| `OR-ACL` | `principal_identity:EVIDENCE_REF`, `database_role:EVIDENCE_REF`, `allowed_calls:CONTRACT_ID_SEQUENCE`, `observed_allowed_results:EVIDENCE_REF_SEQUENCE`, `denied_calls:CONTRACT_ID_SEQUENCE`, `observed_denials:EVIDENCE_REF_SEQUENCE`, `reachable_grant_paths:EVIDENCE_REF_SEQUENCE`, `bypass_path_count:SAFE_INTEGER` | `JAC-PG-01`, `JAC-EVL-01`, `JAC-CLO-01`, `JAC-CUT-01`, `JAC-ACL-01`, `JAC-CAP-01` |
| `OR-CAP` | `capability_identity:EVIDENCE_REF`, `entropy_source:CONTRACT_ID`, `entropy_bit_count:SAFE_INTEGER`, `cleartext_residence:EVIDENCE_REF_SEQUENCE`, `transient_copy_domains:EVIDENCE_REF_SEQUENCE`, `excluded_surfaces:CONTRACT_ID_SEQUENCE`, `activation_session_witness:EVIDENCE_REF_OR_NONE`, `invalidation:EVIDENCE_REF_OR_NONE`, `continuity_loss_result:ENUM_TOKEN` | `JAC-CAP-01` |
| `OR-PHY` | `acknowledged_boundary:EVIDENCE_REF`, `power_cut_stimulus:EVIDENCE_REF`, `recovered_prefix:ENUM_TOKEN`, `pre_cut_target_image:EVIDENCE_REF`, `post_boot_target_image:EVIDENCE_REF`, `atomic_state_result:ENUM_TOKEN`, `post_boot_evidence:EVIDENCE_REF_SEQUENCE`, `evidence_integrity_result:ENUM_TOKEN` | `JAC-EFX-01`, `JAC-DUR-01` |

`LEGACY_READER_OUTPUT_REF` admits exactly an `EvidenceRef` to
`hindsight-compatibility-legacy-reader-success/1` or
`hindsight-compatibility-legacy-reader-failure/1`. The referenced complete body
must resolve and match the selected frozen-registry member digest and derived
reader contract ID and the exact reader-execution binding in the same
projection. The output's own execution-binding reference must equal that field
byte for byte. No other evidence reference is a value of this field kind.

`RECOVERY_AGGREGATE_IDENTITY` embeds the exact
`RecoveryAggregateIdentity` union value rather than an evidence reference.
For `ABSENT_REQUEST`, the oracle reconstructs every field and recomputes
`request_key_digest`; for `COMMITTED_J`, it resolves the exact journal body and
recomputes `journal_digest`. `TARGET_ALLOWED_DELTA` embeds exactly the displayed
`TargetAllowedDelta` variant selected by the operation action. The oracle
derives it from the plan and admits only one generation increment, unchanged
target and surface, unchanged selected membership, unchanged preserved
membership and values, and the variant's exact selected-value source. Neither
field kind accepts an `EvidenceRef`, caller-selected body, or digest-only
projection.

`TARGET_APPLY_PAYLOAD_REF_OR_NONE` is exactly the embedded
`TargetApplyPayloadEvidenceRef` from an apply plan or the literal `"NONE"` for
a rollback plan. The adjacent digest equals the embedded reference's
`body_digest` for apply and is `"NONE"` for rollback. For apply, the evaluator
resolves the complete payload, independently reconstructs the locked selected
membership, and requires the after selected projection to equal the payload;
for rollback, either field containing anything other than `"NONE"` fails.

The canonical registry's `definitions` sequence contains exactly the fourteen
definition references in this displayed order. Each reference must resolve the
exact expanded body above, and the registry digest is SHA-256 over the complete
`CanonicalOracleRegistry/v1` successor-canonical body including its LF. An
`OracleContract/v1` names that exact registry and its matching definition; its
`oracle_id` equals both. A campaign, cell, or run may select an oracle but
cannot supply fields, narrow obligations, replace a definition, or point to a
different registry.

Expected and observed `OracleProjection/v1` bodies copy the same definition and
the same exact claim-predicate reference sequence from the campaign
requirement, and contain exactly the definition's fields in displayed order.
The expected fields equal the canonical predicate's embedded expected fields;
the observed fields are independently executed against the predicate's bound
run and retained inputs. Each name and value kind must match, and every
sequence uses the source contract's complete deterministic order.
`ENUM_TOKEN` accepts only `OracleEnumToken`; `ENUM_TOKEN_OR_NONE`
accepts only that token or `"NONE"`. Every other value-kind label selects the
single same-named scalar, reference, optional, or sequence arm in
`ProjectionField`; a value accepted by another union arm is still invalid
under the wrong label. The evaluator independently reconstructs every field from protected
source bodies and reads; it does not project the producer's field list.
Omission, addition, duplication, reordering, value-kind drift, unresolved body,
or a field value or predicate from another run is `FAIL`. An oracle passes a
claim only when every required field compares exactly and the exact canonical
claim predicate in the claim registry passes; a campaign cannot use field
equality against an implementation-chosen predicate to discard a claim
obligation.

The production serializer, mutation path, or frozen reader cannot be its own
sole oracle. Independent means a different implementation or projection that
does not share the production mutation code or serialization routine. Shared
fixtures are allowed only when their exact bytes and expected outcomes were
fixed independently before the test run.

## Claim registry

The following table is the complete declarative source for
`CanonicalClaimDefinition/v1`: `Claim` supplies `claim_id`, `Falsifiable
obligation` supplies the exact `obligation` text, and `Required tiers` expands
in fixed tier order to `required_tiers`. For each required tier, the canonical
definition names exactly one `CanonicalClaimPredicate/v1`. Its run predicates
are the complete claim-filtered projection of that tier's canonical registry
or deployment matrix, and its expected fields come only from those canonical
planned-run oracle requirements. The `Tier requirements and oracles` column is
the closed class-and-oracle membership constraint on that expansion. A
definition, predicate, expected field, run, class, oracle, or tier not derived
from these sources is not part of the claim.

| Claim | Falsifiable obligation | Tier requirements and oracles | Required tiers |
| --- | --- | --- | --- |
| `JAC-ARC-01` | The selected PostgreSQL `J→P→R→M→V` architecture covers the fixed expiry, durability, restart, compatibility, and preservation requirements, and every rejected alternative or deferral remains explicit. | Design: `EV-DES`; `OR-TRACE`. | Design |
| `JAC-CAN-01` | Each complete successor body, nested referenced body, recovery observation, support/topology/closure-policy/socket-directory binding, deployment-evidence acquisition, contract body, role-grant set, writer inventory, closed target surface, relation, column, row, PostgreSQL value, cohort membership and projection, mutation image, restore payload and conversion, operation-work request, preflight or refusal, identity, transaction identity, reconciliation subject, reservation, start, committed result, transaction-resolution outcome, ambiguity-query outcome, conclusive-noncommit result, canonical claim definition and predicate, oracle definition and registry, historical reader-execution binding, per-class authority-gate fixture state, protected rollback ciphertext, real-artifact binding and provenance, private package, bounded public projection, independent review receipt, and compatibility registry, reader output, restore content, manifest, exclusion, closure branch-evidence, realized-fence-evidence, origin-fence-binding, or active-fence-adoption body has one accepted family-specific byte representation and digest, including exactly one LF. Role-grant vectors cover every closed object class, including `LANGUAGE`; writer vectors cover every direct, administrative, routine-mediated, and service-mediated path. | Design: `EV-DES`; `OR-ID`. Implementation: `EV-VEC`; `OR-ID`. | Design; implementation |
| `JAC-ID-01` | Same key and binding replays exactly; same key with different bytes conflicts; the grant, plan, approval, authorization receipt, and `J` bind one exact closed action and predecessor input; every start, transaction-resolution outcome, ambiguity-query outcome, reconciliation, observation, terminal result, and protected comparison resolves the same canonical transaction or reconciliation-subject body; publication `UNPROVEN` names the exact original `R` attempt and its preexisting exact conclusive-noncommit committed and terminal results; and apply and rollback never share authority. | Implementation: `EV-REF`, `EV-PG`; `OR-ID`, `OR-PFX`. | Implementation |
| `JAC-ORD-01` | Only ordered `J→P→R→M→V` prefixes exist; a hole or mixed chain fails closed. | Implementation: `EV-REF`, `EV-PG`, `EV-FLT`; `OR-PFX`, `OR-NEXT`, `OR-EVID`. | Implementation |
| `JAC-PG-01` | The trusted adapter owns `BEGIN` through acknowledged `COMMIT`, checks effective synchronous settings immediately before commit, binds the exact target PostgreSQL identity, and admits the initial profile only with exact same-host local controller, PostgreSQL host, endpoint, topology, and complete canonical one-member Unix-socket-directory bindings. | Implementation: `EV-PG`, `EV-ACL`; `OR-ID`, `OR-FENCE`, `OR-ACL`. Deployment: `EV-DEP`; the same oracles. | Implementation; deployment |
| `JAC-TIM-01` | Grant issuance, plan creation, approval, and authorization issuance have one strict order and one equal nonextendable expiry; the protected pre-commit start samples for `J` and `P` and the post-proof sample in `R` must have conservative `U` strictly below it, while equality and greater values are late. A durable timely `R` is the only shared-deadline decision for `M`; `M` rechecks those authority records for identity, currentness, and nonrevocation without resampling their deadline. No scheduling or acknowledgement bound is assumed. | Implementation: `EV-REF`, `EV-PG`; `OR-TIME`, `OR-PFX`. | Implementation |
| `JAC-AMB-01` | A lost acknowledgement recovers only the exact committed stage. Transaction-resolution and ambiguity-query work that discovers commit closes through its own typed outcome referencing the original committed result and never duplicates the stage; unresolved commit state remains ambiguous rather than being inferred. | Implementation: `EV-PG`, `EV-FLT`; `OR-PFX`, `OR-NEXT`, `OR-EVID`. | Implementation |
| `JAC-RST-01` | Restart advances only the one uniquely safe transition and otherwise replays, refuses, or fences. Every request first receives one uncharged side-effect-free committed-result preflight; only unresolved work can commit one charged reservation, while exhaustion, overflow, clock, ordinal, reservation, and conflict failures commit one separately request-keyed nonauthorizing refusal without a charge. Every reservation binds one exact forward or recovery identity and request, admits one durable start and transaction identity, and only a byte-identical committed result replays without charge. Recovered stage advancement commits its recovery observation as the reservation's sole result and points `result_body` to the stage committed in the same transaction. A resolver that discovers the original committed closes with its exact typed resolution or query outcome and original-result binding. Separately charged conclusive-noncommit resolution atomically commits the original terminal noncommit result and closes both started reservations. Only after that close may ordinary gates admit a charged replacement `R`, or terminal conditions admit a distinct charged publication-qualification reconciliation whose `UNPROVEN` result binds the preexisting original committed and conclusive-noncommit results. Each reservation keeps one terminal result, and a crashed `UNPROVEN` resolver is itself the exact `RECONCILIATION` transaction subject of the next separately charged resolver under the same finite limits. Canonical vectors cover both recursive result mappings, exact replay, and the committed-`R` alternative. Reservation-keyed recovery observations cover exactly post-reservation outcomes and record one exact immutable, queryable, nonauthorizing kind. | Implementation: `EV-REF`, `EV-PG`, `EV-FLT`; `OR-PFX`, `OR-NEXT`, `OR-TGT`, `OR-EVID`. | Implementation |
| `JAC-FEN-01` | Loss or uncertainty of activation-bound continuity fences every unconsumed old-epoch `R`. | Implementation: `EV-PG`, `EV-FLT`; `OR-FENCE`, `OR-NEXT`. | Implementation |
| `JAC-LIN-01` | One canonical lineage serializes all cohorts; one sibling advances and every loser observes immutable head drift. | Implementation: `EV-PG`; `OR-LIN`, `OR-TGT`. | Implementation |
| `JAC-EFX-01` | Target mutation, canonical before and after `TargetMutationImage/v1` digests, `M`, generation change, mutation receipt, and lineage advance occur exactly once and atomically. Apply derives its complete selected postimage only from the plan-bound `TargetApplyPayload/v1`; rollback derives it only from `TargetRestorePayload/v1`. | Implementation: `EV-PG`, `EV-FLT`; `OR-TGT`, `OR-LIN`. Release: `EV-PHY`; `OR-PHY`. | Implementation; release |
| `JAC-EVL-01` | The protected evidence registrar and evaluator accept only authenticated plans whose stored ordered runs exactly equal deterministic cell/case/seed/allocation/stimulus/oracle expansion against the immutable canonical claim definitions, complete executable predicate bodies, and canonical oracle registry. Every `OracleId` uses its exhaustive ordered typed fields and exact definition-and-predicate-bound claim obligations; a campaign cannot omit, add, reorder, retag, narrow, or replace them, and the evaluator independently reconstructs each field and executes the exact predicate. The registrar also binds campaign and run timing to protected observations, every deployment projection to its protected acquisition observation, every authority-gated run to one closed exhaustive per-class fixture state, and every historical coverage row to its complete member-to-tool reader-execution binding and exact compatibility registry and vector digests. It recomputes closed coverage and prerequisite-bound tier state, computes omission and failure dominance, replaces every affected current pointer atomically, derives nonrenewing qualification results and receipts from protected time, and admits an attestation only with the complete current deployment partition, prerequisite references, policy, oldest deciding acquisition within the maximum age, and fresh qualified issuance observation. Registration, signing, aggregation, completion, and retry never refresh acquisition age. | Implementation: `EV-PG`, `EV-FLT`, `EV-ACL`; `OR-EVAL`, `OR-EVID`, `OR-ACL`. | Implementation |
| `JAC-VER-01` | Exact `MATCH` creates `V`; each stable attempt is immutable and replayable; only a genuinely retryable `UNABLE_TO_VERIFY` permits a later stable attempt; mismatch, invariant failure, and unproven target identity are sticky terminal outcomes that exclude every later `V` or mismatch. | Implementation: `EV-PG`, `EV-FLT`; `OR-PFX`, `OR-EVID`, `OR-TGT`, `OR-LIN`. | Implementation |
| `JAC-RBK-01` | Rollback uses distinct authority and restores the exact selected content once. Before and after `TargetMutationImage/v1` bodies remain independently reconstructable state at their respective generations; the generation-free `TargetRestorePayload/v1` remains independently reconstructable content. Successor plaintext is exact LF-terminated payload bytes, while legacy no-LF plaintext passes through its registered reader output and deterministic conversion. Rollback preserves the locked preserved projection, substitutes only the payload's selected membership, increments generation once, and derives the after image and digest. Exact ciphertext bytes remain digest-and-length-verified protected PostgreSQL state from pre-plan construction through `J` adoption and verified rollback or authorized retirement. | Implementation: `EV-PG`, `EV-FLT`; `OR-TGT`, `OR-EVID`. | Implementation |
| `JAC-LEG-01` | Every historical result is one exact registered nonauthorizing reader-success or reader-failure body selected by the complete frozen reader registry, deterministic selector-to-contract mapping, and exact member-to-tool execution binding; the output repeats that binding. The registry includes the kindless authenticated `requeue-plan` dependency reader, and public synthetic coverage remains exhaustive for its incremented member count and the exact registry-body and member-vector digests. Each required real-evidence run binds an authenticated controlled-private acquisition or sanitized-real derivation under its governing policies; private deciding artifacts stay bounded and controlled; public disclosure is only the exact mode-bearing bounded projection plus authenticated independent-review receipt and package commitment; and only the manifest-selected complete apply at genesis may enter the one successor rollback bridge. | Implementation: `EV-LEG`, `EV-PG`; `OR-LEG`, `OR-NEXT`. | Implementation |
| `JAC-CLO-01` | Compatibility closure uses one exact qualified closure-policy identity, reserves bounded contiguous attempts with checked integer-nanosecond deadlines and separately rounded full resolution margin, admits one fenced observer lease, permits only same-ordinal takeover, rejects late or stale finalization, requires a qualified lower bound at or beyond reservation expiry for abandonment, resolves abandonment without target observation, closes exhaustion deterministically, and binds every branch to its exact closed v1 qualified-sample, takeover invalidation, comparison, and failure evidence bodies. | Implementation: `EV-PG`, `EV-FLT`, `EV-ACL`; `OR-ID`, `OR-TIME`, `OR-FENCE`, `OR-NEXT`, `OR-EVID`, `OR-ACL`. Release: `EV-CLK`, `EV-CAP`; `OR-TIME`, `OR-FENCE`, `OR-ACL`. Deployment: `EV-DEP`; `OR-ID`, `OR-TIME`, `OR-FENCE`. | Implementation; release; deployment |
| `JAC-CUT-01` | One transaction allocates the proposed epoch, inserts its protected `RESERVED_FENCED` row, issues the bound deployment attestation, and installs the exact current selector. The row remains selected while the writer fence is established; activation revalidates that exact fence and atomically commits the manifest, genesis, one-time active-pointer transition, and selector clear, or a conclusive abort changes the row to permanent `ABANDONED_FENCED`. Every legacy-fence external effect requires fresh current authority and time validation, and the profiler and admission finalizer independently enumerate and exactly match the complete typed role-grant set and writer inventory before the compatibility fence proposal may project them. No unclassified or extra writer has an admitted interface or grant. | Implementation: `EV-LEG`, `EV-PG`, `EV-FLT`, `EV-ACL`; `OR-LEG`, `OR-FENCE`, `OR-EVID`, `OR-ACL`. Deployment: `EV-DEP`; `OR-FENCE`, `OR-ACL`. | Implementation; deployment |
| `JAC-PRS-01` | Every unaffected row, limit, grant, historical artifact, prefix, and receipt remains byte- or value-identical. | Implementation: `EV-PG`, `EV-FLT`; `OR-TGT`, `OR-EVID`. Release: `EV-PHY`; the same oracles. Any later evidence class whose stimulus mutates a fixture repeats them. | Implementation; release |
| `JAC-ACL-01` | The exact qualification, plan-authority, operation grant/plan/approval/authorization/revocation, deployment-policy-authority, admission, activation, publication, mutation, verification, evidence-owner, evidence-authority, evidence-producer, private registrar, private reviewer, public exporter, closure, fence, and ordinary-runtime identities can call only their declared interfaces, cannot select stored verdicts, and have no cross-role or direct-relation bypass. The complete role graph includes inherited, assumable, `PUBLIC`, ownership, default-privilege, function-mediated, and service paths; an unenumerated, unclassifiable, unresolved, duplicate, or extra path fails admission. Private roles have reciprocal denials, and export succeeds only for the exact current independently reviewed receipt. | Implementation: `EV-ACL`; `OR-ACL`. Deployment: `EV-DEP`; `OR-ACL`, `OR-FENCE`. | Implementation; deployment |
| `JAC-CAP-01` | Each activation uses at least 256 OS-CSPRNG bits; cleartext persists only in the adapter's locked nondumpable allocation and the activation session's non-WAL relation; bounded transient copies remain inside the declared adapter, Unix-domain-socket protocol, and backend domains; only the digest is durable; continuity loss makes every later `M` fail without relying on forensic erasure. | Implementation: `EV-PG`, `EV-ACL`; `OR-CAP`, `OR-FENCE`, `OR-ACL`. Release: `EV-CAP`; `OR-CAP`, `OR-FENCE`, `OR-ACL`. Deployment: `EV-DEP`; `OR-ID`, `OR-ACL` verify the exact current passing `EV-CAP` qualification receipt and protected interface grants without observing a witness. | Implementation; release; deployment |
| `JAC-CLK-01` | Clock rollback, reboot, suspend uncertainty, excessive error, stale envelope, or arithmetic failure cannot lower `U` or create authority. | Implementation: `EV-REF`, `EV-PG`; `OR-TIME`, `OR-FENCE`. Release: `EV-CLK`; the same oracles. Deployment: `EV-DEP`; `OR-FENCE`. | Implementation; release; deployment |
| `JAC-DUR-01` | Within the exact profile's declared fault model, every acknowledged stage and atomic `M` effect exercised by the qualification campaign cold-recovers exactly; no unexplained run is accepted. | Release: `EV-PHY`; `OR-PHY`. Deployment: `EV-DEP`; `OR-ID`, `OR-FENCE` verify the exact current passing `EV-PHY` qualification receipt without repeating its campaign. | Release; deployment |

### Compatibility closure state and fault matrix

Every `JAC-CLO-01` campaign exercises each row through real protected
PostgreSQL interfaces. `EV-FLT` injects failure before commit, after commit
before acknowledgement, at each lock wait, and at each adapter-enforced
connection close. `EV-ACL` repeats every transition from each non-closure role
and proves denial. Every row applies `OR-ID`, `OR-TIME`, `OR-FENCE`,
`OR-NEXT`, and `OR-EVID`; role attempts also apply `OR-ACL`.

| Initial state or fault | Required result |
| --- | --- |
| No case exists | Under the current exact deployment attestation, its exact qualified `ClosurePolicyLimits/v1`, and qualified clock, create one byte-exact `ClosureCaseBinding/v1` whose copied values equal that policy or no row. The policy identity is part of the stable key and body digest. A same-key different binding conflicts. Failure preserves every source, target, and existing evidence byte. |
| Open case, no reservation | Reserve exactly the next contiguous ordinal only when the server derives the earliest admissible deadline from the explicit qualified sample, policy-duration, call-timeout, connection-lifetime, clock-validity, case-expiry, and attestation-expiry operands using checked UInt128 arithmetic. Each wall-expiry candidate leaves `q + ceil_mul_div(q,n,d)`; the monotonic clock-validity candidate leaves raw measured `q`. Exact millisecond conversion multiplies by 1,000,000; overflow rejects. Commit the immutable request, input, clock, deadline, and initial lease generation before observation. Equality with any bound rejects without consuming an ordinal. |
| Reserved, unclaimed attempt | From a fresh explicit qualified sample, one claim derives the lease deadline as the earlier of the checked sample-plus-policy observer-lease duration and the reservation deadline; requires the result to be strictly later than the sample; advances the server-owned lease generation; binds its token, observer incarnation, and deadline; and commits before target observation. A concurrent claim returns `OBSERVATION_IN_PROGRESS` and changes nothing. Takeover repeats that same derivation for the newly fenced generation and never extends the reservation. |
| Current live lease | Only the exact generation, token, incarnation, case, request, and ordinal may observe and finalize strictly before both lease and reservation deadlines. Same-request replay reports progress; a different request cannot allocate another ordinal. |
| Lease expired or observer incarnation invalidated before reservation expiry | Takeover fences the prior generation and installs one replacement claim for the same request and ordinal. It never allocates a new attempt. Any finalizer from the old generation is rejected. |
| Observer incarnation invalidated before reservation expiry attempts abandonment | Reject abandonment without consuming the ordinal or changing the case outcome. Only authenticated same-ordinal takeover may use that invalidation; abandonment still requires a qualified sample whose conservative lower bound is at or after reservation expiry. |
| Finalization at either deadline or after takeover | Equality is late. The transaction appends no observation, terminal result, target change, or successor authority and preserves the reservation for protected takeover or resolution. |
| Reservation expired under the same valid attestation and clock envelope | Only a qualified sample whose conservative lower bound is at or beyond the reservation deadline may enter the bounded resolver. It closes the tagged observer session, advances the lease generation, derives a fresh positive resolution deadline, and may append only `ABANDONED_UNABLE_TO_VERIFY`. It cannot observe the target or finalize a match. |
| Unable or abandoned result below the attempt ceiling | Append one exact nonterminal observation, clear the unresolved slot, and permit only the next contiguous reservation while the case, attestation, and clock remain valid. Lost acknowledgement exact-replays that observation. |
| Unable or abandoned result at the attempt ceiling | Atomically record `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY` in the terminal slot. Every later reservation, claim, takeover, observation, or finalization is refused. |
| Exact match or mismatch races a resolver | Locks admit either the current generation's before-deadline conclusive result or a generation-advancing abandonment, never both. Match and mismatch are terminal; a fenced late observer cannot overwrite either result. |
| Concurrent same or different request | Same request and binding converges on one reservation and result; changed binding conflicts. A different request sees the one unresolved reservation and receives `OBSERVATION_IN_PROGRESS` without consuming an ordinal. |
| Attestation or qualified-clock replacement, revocation, expiry, identity loss, target or adapter drift, reboot, suspend uncertainty, rollback, or excessive error before case creation, reservation, claim, observation, finalization, takeover, or resolution | Reject that transition. An existing case becomes a nonauthorizing remediation blocker; no synthetic observation, abandonment, exhaustion, closure disposition, target read, target mutation, or successor authority is created. |
| Crash, timeout, forced rollback, or lost acknowledgement at any transition | Exact protected query returns the committed state or preserves the prior state. Recovery takes only the one next action named above, fences every stale observer generation before resolution, and leaves source artifacts, target rows, publication state, and unrelated evidence unchanged. |

### Evidence evaluator fault and concurrency matrix

Every `JAC-EVL-01` implementation campaign exercises every row below through
the real protected PostgreSQL interfaces. `EV-FLT` injects failure before
commit, after commit before acknowledgement, and while each affected current
pointer is locked. Concurrency schedules race the two named operations in both
lock orders. `OR-EVAL` computes the expected expansion, validity, verdict,
selected campaign, current reference, and receipt eligibility directly from
the retained immutable bodies and predeclared registry. Its independent
implementation cannot call the protected registrar, evaluator, finalizer, or
admission code, and it cannot read a stored verdict or current pointer until
after it has fixed the expected projection. `OR-EVID` separately proves that
every body and pointer outside the allowed delta remained unchanged.

| Initial state or fault | Required result |
| --- | --- |
| Required claim and tier before any campaign | The independently derived result is `OPEN`, with no selected campaign or run result. A `NOT_REQUIRED` result is valid only for a registry pair with no requirement. |
| Campaign-plan registration | Accept only the exact authenticated `EvidenceCampaignPlan/v1` and acceptance whose claim, tier, subject, complete ordered run sequence, atomic or closed two-member composite basis, and acceptance interval recompute before execution. Resolve every canonical claim definition and complete predicate body from the selected registry or deployment matrix; require one definition and its exact tier predicate for every run claim and predicate-bound expected projection. For qualification, independently expand every exact cell/case/seed/allocation position and require literal `RELEASE`, byte equality of cardinality, deterministic run IDs, closed derived stimulus bodies, complete ordered oracle lists and expected projections, and stored `QualificationPlan.planned_runs`; reject an omitted `OR-FENCE` or `OR-ACL` requirement. For mixed `EV-LEG` claims, require the exact claim-registry-plus-historical composite and deterministic concatenation; reject an atomic historical basis carrying another class. Reject `NONE`, late acceptance, or any missing, extra, duplicate, reordered, aliased, substituted, or changed definition, predicate, run, or oracle field. |
| Historical-corpus-plan registration | Recompute the exact required-category, reader, artifact-kind/schema/variant, boundary, generator, seed, budget, shrink, and policy coverage projection from the closed registry and plan before accepting its authenticated plan-acceptance body. Each row carries one exact member-to-tool execution binding whose embedded member includes the dependency role, artifact kind, artifact schema, reference-plan schema, protocol family/version, variant, wire contract, derived reader contract ID, source revision, and member digest, and whose tool contracts and immutable implementation implement that member. At corpus-plan acceptance, campaign registration, every historical run registration, and tier evaluation, reconstruct the exact `FrozenReaderRegistry/v1` body, including the kindless `requeue-plan` member and exact member count, and require its digest, member-vector digest, body reference, complete member projection, and one-to-one execution-binding projection to equal the plan. Reject zero, stale, narrowed, reordered, differently expanded, dropped, or altered registry values; a wrong kind/version, member count, contract ID, tool, or source revision; a row attached to another member or execution; inline parent parsing of the requeue plan; or collapse of two tuples that differ in any selector. Any mismatch prevents registration and `PASS`. |
| First exact campaign registration | Accept one immutable root only when the subject is current and the complete planned-run expansion, including every tool, acquisition procedure, limit, oracle, and expected projection, equals the preaccepted plan or closed matrix. Recursively resolve every tool, procedure, generator, and nested typed contract body and recompute each digest from its complete canonical bytes before comparing the plan. Exact concurrent retries converge on one body; changed bytes conflict. |
| Campaign with an empty, omitted, extra, duplicated, reordered, late-bound, unresolved, digest-mismatched, or wrong-class run requirement or nested contract body | Reject campaign registration and preserve the prior root, current result, and every evidence body. A caller-supplied tool, procedure, input, output, invocation, or step contract discovered after execution cannot repair it. |
| Authority-gate fixture setup or execution | Resolve only the planned run's exact `AuthorityGateConformancePrestate/v1` and `AuthorityGateFixtureState/v1`. Before setup and again before execution, enumerate every gate-reachable seeded row, current slot, explicit absence, lineage value, clock/envelope value, capability, revocation, and accounting value and require exact equality with the canonical fixture body. Reject an opaque artifact, missing or extra state, duplicate key, tagged-value mismatch, cross-schema reference, or different prestate in any evidence record, failure, run result, or oracle projection. Create no live authority. |
| Exact run-result registration | Recompute every nested evidence record against its planned run. For deployment, require the complete ordered acquisition-body sequence and exact acquisition/projection/procedure/run/oracle equalities; the registrar creates no acquisition observation. Exact concurrent retries converge on one body; changed bytes conflict. Atomically append the accepted body, derive the claim-local tier result, and replace only the affected current pointers. |
| Campaign started with any required result missing, invalid, nonexecuted, omitted, unexplained, aborted, shortened, over budget, generator-failed, or unreproducible | Derive `FAIL`, never `OPEN` or `PASS`. Later passing results cannot hide that required outcome. |
| One or more valid deciding records recompute to `FAIL` while every other record passes | Derive `FAIL` regardless of record order, retry order, or number of passing records. No caller-supplied aggregate verdict can override failure dominance. |
| Record invalidation | Accept only an authenticated, unexpired `EvidenceRecordInvalidation/v1` backed by the exact independently confirmed invalidity finding for that nonfailing record. Retain every body; remove the record from validity for its complete ordered claim set; atomically recompute every affected result and current pointer; and derive `FAIL` wherever the required record is now missing. Refuse a subset invalidation, invalidation of a valid deciding `FAIL`, a mismatched reference, or an unproved defect. |
| Campaign supersession | Accept only an authenticated contiguous edge from the exact current claim-local result to a separately registered current-subject replacement campaign. Preserve the prior campaign and result. Recompute solely from the replacement; never merge evidence. Exact retries converge, while a stale, skipped, cyclic, cross-claim, cross-tier, or second concurrent edge conflicts and cannot select the proposed replacement. |
| Current subject replacement | Atomically preserve the prior subject and result, install the new subject, and replace every affected current pointer with the exact independently derived `STALE` result. A new-subject campaign remains unselected until valid supersession. |
| Run registration, invalidation, supersession, or subject replacement racing current-pointer recomputation | Commit either the complete old state or the complete new state. No observer may see a new accepted body with an old affected pointer, a new pointer without its exact body, a partial claim set, or a caller-selected result. |
| Prerequisite-result replacement | Recompute every dependent `EvidenceTierState/v1` with the complete exact ordered prerequisite references and atomically replace its current result pointer, even for `PASS` to a different current `PASS`. A non-`PASS` prerequisite derives `PREREQUISITE_BLOCKED`; unchanged claim-local evidence cannot retain the old dependent pointer, and the changed state digest is not a same-key conflict. |
| Operation grant, plan, approval, authorization, or revocation | Only the matching isolated authenticated principal may perform its keyed transition. Resolve the complete typed action binding, including exact grant, retry, reconciliation, budget, protected ciphertext, source plaintext, restore payload and conversion, cohort membership, target, and epoch inputs; require the grant, plan, approval, and authorization receipt to carry the same nonextendable deadline; require the expected current slot; advance only through the closed lifecycle; and exact-replay identical bytes. A skipped state, stale expected reference, mismatched deadline, cross-key body, mixed principal, ambient default, reinstatement, or second revocation conflicts. Every stage locks both current unrevoked slots through commit. After durable timely `R`, `M` does not resample the shared deadline. |
| Operation-work preflight, reservation, start, and committed result | Perform the exact read-only committed-result preflight first. A byte-identical committed result returns without charge or write; unresolved work proceeds with the same request to one accounting transaction. That transaction either consumes the checked-next ordinal and full charge and commits one reservation, or commits one request-keyed nonauthorizing pre-reservation refusal with no reservation or accounting change. Atomically permit only the reservation's one `RESERVED -> STARTED` transition, including its exact `TransactionIdentity/v1`, then one terminal `STARTED -> COMMITTED` result binding. Recovery stage advancement commits its `RecoveryAdvancementObservation/v1` as the sole result with `result_body` naming the stage created in that transaction. A transaction resolution or ambiguity query that proves the original committed instead closes its resolver reservation with the exact typed original-committed outcome, repeats both exact reservation/start/transaction/work chains and the original committed result, and inserts no duplicate stage or recovery observation. Changed outcome bytes or either identity conflict; a resolver for a resolver uses the earlier resolver's exact `RECONCILIATION` transaction subject and must reach the same closed result mapping. Conclusive noncommit requires a separately reserved, started, and charged reconciliation with one exact subject; one transaction verifies noncommit, inserts the typed terminal body, closes the original with its committed-result binding, records the resolution observation and binding, and closes the resolution. Only afterward may ordinary gates admit a replacement `R` or terminal conditions admit a separate publication-qualification reservation. Its `UNPROVEN` observation repeats the exact original `R` subject and complete identity chain and binds the preexisting original `CONCLUSIVE_NONCOMMIT` committed-result and terminal-result bodies; it writes only its own observation, binding, and slot close. An original still `STARTED`, an empty or different result, `R_VALID`, `R_LATE`, reversed ordering, or changed identity refuses `UNPROVEN`. A crashed `UNPROVEN` reconciliation is closed independently through its own exact `RECONCILIATION` transaction subject. Each reservation retains one terminal result, and exact retry returns only its byte-identical binding. Campaign or run coordinates cannot identify the original `R`. A crash before or after start, reused marker, changed mode, request, transaction, subject, predecessor or attempt ID, stale incarnation, same reservation for a distinct attempt, skipped ordinal, bare recovery observation, alternate observation key, or free replay without the byte-identical committed binding starts no work. |
| Qualification-receipt finalization | Under locks on all required current pointers, require the exact complete ordered design, implementation, and release partitions, each result's exact ordered current prerequisite references, exact pointer equality, and `PASS` for every referenced tier and class result. Any omission, extra, duplicate, reorder, stale reference, `OPEN`, `PREREQUISITE_BLOCKED`, `FAIL`, `STALE`, or pointer change refuses receipt insertion. The positive implementation cell uses only the isolated conformance prestate defined below. |
| Receipt finalization racing invalidation, supersession, or subject replacement | If finalization orders first, it may insert only the then-current exact passing receipt and the later change immediately makes that immutable receipt noncurrent. If the evidence change orders first, finalization refuses the old references. No mixed receipt can commit. |
| Private package registration, independent review, or public export | Only the isolated registrar can register the exact package; only the isolated reviewer can read it and compare-and-set its projection and receipt; only the isolated exporter can return the exact current public pair. Registration and review require the same exact real-artifact binding through stimulus, plan cell, run, package, declared public mode, and package commitment, including its governing policies and authenticated acquisition or sanitization provenance. Export locks and revalidates the package mapping, deciding evidence, selected campaign and subject, policies, reviewer authorization, projection, and current receipt. Public synthetic bytes, an opaque artifact plus a cell label, any stale expected receipt, or any changed input returns no public body. Reciprocal attempts and every direct private-relation read are denied. |
| Qualification class-result or receipt retry | Recompute the stable key, exact evidence-derived interval, class values, receipt issue time, and accepted-plan-derived validity bound. An exact retry is byte-identical. Unchanged evidence cannot produce a later issue or validity time; renewal requires a new accepted qualification and campaign plan with new evidence. |
| Deployment-attestation finalization | Lock the exact keyed current deployment-policy and attestation slots, epoch high-water mark, current-reserved-activation selector, complete deployment `PASS` partition, complete qualification-receipt partitions, every exact prerequisite-result pointer, qualified clock, all four live deployment bindings, and the catalog and service-registry scope. Independently re-enumerate and canonicalize the complete role-grant set and writer inventory and require exact equality with the profiler bodies. Recompute maximum evidence age from every protected acquisition lower bound, never registration or completion, then take a fresh protected issuance observation. Atomically allocate the epoch, insert its `RESERVED_FENCED` row and attestation, and install both selectors. Any policy, result, prerequisite, acquisition, time, boot, host, endpoint, topology, grant, writer, selector, or epoch mismatch, or any unenumerated, unclassifiable, unresolved, duplicate, extra, `OPEN`, `PREREQUISITE_BLOCKED`, `FAIL`, `STALE`, superseded, or invalidated member emits none of them. |
| Deployment-policy replacement or revocation, attestation issuance, activation, or stage admission racing a required current-pointer change | The policy compare-and-set locks only its exact target-surface slot and carries the expected reference and replacement or `NONE`; `NONE` clears only that slot. Under locks on that slot, the attestation slots, complete deployment partition, and complete receipt partitions, issue or consume authority only if every old reference remains current through attestation insertion, activation commit, or stage commit. Otherwise refuse and fence; a previously issued immutable receipt or attestation supplies no stale authority. |
| Evaluator or registrar failure, forced rollback, or lost acknowledgement at any mutation above | Exact protected query returns the one committed body, verdict, selection, and pointer set or the complete prior state. Retry recomputes from retained inputs; it never accepts a caller's remembered verdict or creates a second logical result. |
| Any evidence owner, plan authority, operation-authority principal, deployment-policy authority, evidence authority, producer, private registrar, private reviewer, public exporter, qualification, admission, activation, publication, mutation, verification, closure, fence, or ordinary-runtime principal attempts an undeclared interface or direct relation operation | `OR-ACL` proves denial under that identity. The attempt cannot accept a plan, issue or revoke operation authority, administer a deployment policy, register evidence, cross the private registrar/reviewer/exporter boundary, select a subject, apply a disposition, read or select a current verdict, finalize a receipt, replace a pointer, or change any protected relation beyond its exact declared grant. |

These rows are the closed accepted behavior set for the protected evidence
registrar, evaluator, and their current-pointer and qualification-finalization
seams. An implementation cannot replace `OR-EVAL` with its own stored result,
status code, selected pointer, or self-verdict.

No authority-gated conformance cell may depend on the authority that the cell
helps establish. Every such qualification or campaign cell therefore names one
exact `AuthorityGateConformancePrestate/v1` in its planned-run requirement.
This includes positive and negative cells for `J`, `P`, `R`, `M`, combined
activation, legacy fencing, the qualification finalizer, every authority-gated
release cell, and the deployment finalizer exercised by `JAC-CUT-01`.

The prestate is predeclared stimulus, has `authority=NONE`, and exists only in
its `DISPOSABLE_CONFORMANCE_SCHEMA`. Its `fixture_state` resolves only to the
closed `AuthorityGateFixtureState/v1` body above. Each sequence is
duplicate-free and canonical: seeded rows order by `(relation,
row_key_digest)`, current slots by `(slot_class, slot_key_digest)`, explicit
absences by `(relation, row_key_digest)`, lineage values by target-reference
bytes and surface digest, and clock, capability, revocation, and accounting
values by `value_id`, subject-reference bytes, or plan-reference bytes as
applicable. Every current slot uses its exact per-class key body, presence
state, value variant, and referenced kind/version. Every queried
missing row has one explicit absence marker, and no key may be both present and
absent. Each accounting value's outer plan equals `state.plan`; each revocation
state is `CURRENT` with `revocation="NONE"` or `REVOKED` with the exact typed
revocation body. Clock bounds satisfy lower at or below upper and resolve the
named envelope. Capability and session-witness strings are canonical unpadded
base64url, decode to the exact same synthetic octets of at least 32 bytes, and
their SHA-256 equals `capability_digest`.

Before setup, the conformance registrar resolves the complete transitive body
closure and statically enumerates every relation key and current selector that
the selected gate can reach. It rejects the plan if any reachable seeded row,
current slot, explicit absence, lineage value, clock or envelope value,
capability, revocation, or accounting value is missing from or extra to the
fixture body. The setup principal then loads only that exact enumeration.
Immediately before execution, a protected preflight query repeats the reachable
state comparison against the disposable schema and rejects any unenumerated,
changed, or newly reachable state. No setup or gate execution begins after
either mismatch.

The exact prestate reference is copied from the planned run into every
`EvidenceRecord/v1`, `EvidenceRunFailure/v1`, and `EvidenceRunResult/v1` for
the conformance run; nongated runs copy literal `"NONE"`. `OR-ID`, `OR-EVID`,
and the gate-specific oracle bind the exact fixture-state reference through
that prestate in their expected and observed projections. A result with a
different, omitted, or transitively changed fixture body is invalid even when
the transition output happens to match.

After production ACL installation, those bytes can exercise the unchanged
protected transition, but the prestate and its synthetic rows may occur only as
nonauthorizing planned test stimulus and evidence provenance. Neither a
prestate reference nor a synthetic row or selector may occupy a live protected
current pointer, a qualification-receipt authority field, a deployment
attestation, a reserved or active epoch, a lineage head, a legacy-fence
binding, a stage row, a mutation receipt, or a verification row. The protected
conformance registrar rejects any attempted cross-schema row reference and
every use of a prestate or seeded body as a live gate input. A tier result may
cite the completed conformance run as evidence, but its live finalizer resolves
only the run's independently recorded oracle outcome; it never installs or
resolves the fixture prestate as authority.

The independent oracle fixes its expected projection before setup. The cell's
only outputs are nonauthorizing conformance observations retained after the
disposable schema and every synthetic authority row have been destroyed. A
positive finalizer cell therefore proves that the unchanged gate would accept
its exact synthetic prestate; it does not issue a live qualification receipt,
deployment attestation, activation, stage, or epoch. Negative cells perturb
each exact partition, pointer, and authority input independently. Real release
and deployment decisions must later consume only actual current evidence and
authority produced outside conformance. In particular, the real qualification
receipt references the actual protected `JAC-EVL-01` implementation result, and
the real deployment finalizer references the actual release receipt and
deployment partition. A missing prestate on an authority-gated planned run, a
prestate on a nongated run, a gate mismatch, or any prestate or seeded body in
a live authority field is invalid.

### Evidence disposition and tier-local claim verdicts

Every disposition uses the successor canonical-byte contract. Its exact
authority subject, authorization receipt, and final body are:

```text
EvidenceRecordInvalidationAuthoritySubject := {
  "affected_claim_ids": sequence<ClaimId>,
  "campaign": EvidenceRef,
  "disposition_id": Id,
  "evidence": EvidenceRef,
  "invalidity_evidence": EvidenceRef,
  "issued_at_unix_ns": UInt128String,
  "reason": Text,
  "subject_kind": "hindsight-postgresql-evidence-record-invalidation",
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT",
  "valid_until_unix_ns": UInt128String
}

EvidenceCampaignSupersessionAuthoritySubject := {
  "campaign": EvidenceRef,
  "claim_id": ClaimId,
  "disposition_id": Id,
  "issued_at_unix_ns": UInt128String,
  "prior_result": EvidenceRef,
  "reason": Text,
  "replacement_campaign": EvidenceRef,
  "subject_kind": "hindsight-postgresql-evidence-campaign-supersession",
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT",
  "valid_until_unix_ns": UInt128String
}

EvidenceDispositionAuthorizationReceipt := {
  "authority_principal": Text,
  "decision": "AUTHORIZE",
  "issued_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-evidence-disposition-authorization-receipt",
  "schema_version": 1,
  "subject_digest": Digest,
  "subject_kind": "hindsight-postgresql-evidence-record-invalidation" |
                  "hindsight-postgresql-evidence-campaign-supersession",
  "valid_until_unix_ns": UInt128String
}

EvidenceRecordInvalidation := {
  "affected_claim_ids": sequence<ClaimId>,
  "application_time_observation": EvidenceRef,
  "authority_receipt": EvidenceRef,
  "campaign": EvidenceRef,
  "disposition_id": Id,
  "evidence": EvidenceRef,
  "invalidity_evidence": EvidenceRef,
  "issued_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-evidence-record-invalidation",
  "reason": Text,
  "schema_version": 1,
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT",
  "valid_until_unix_ns": UInt128String
}

EvidenceCampaignSupersession := {
  "application_time_observation": EvidenceRef,
  "authority_receipt": EvidenceRef,
  "campaign": EvidenceRef,
  "claim_id": ClaimId,
  "disposition_id": Id,
  "issued_at_unix_ns": UInt128String,
  "kind": "hindsight-postgresql-evidence-campaign-supersession",
  "prior_result": EvidenceRef,
  "reason": Text,
  "replacement_campaign": EvidenceRef,
  "schema_version": 1,
  "subject_revision": EvidenceRef,
  "tier": "DESIGN" | "IMPLEMENTATION" | "RELEASE" | "DEPLOYMENT",
  "valid_until_unix_ns": UInt128String
}

EvidenceDisposition := EvidenceRecordInvalidation |
                       EvidenceCampaignSupersession
```

`authority_receipt` must name the exact current
`hindsight-postgresql-evidence-disposition-authorization-receipt/v1`. Its
`subject_digest` is an intrinsic projection, not a cross-contract authority
reference: it is SHA-256 over the complete canonical matching authority-subject
bytes, including their LF. `subject_kind` selects exactly one subject grammar
above, and every value must equal the same field in the final body. The receipt
and disposition issue and validity times must match,
`issued_at_unix_ns < valid_until_unix_ns`, and the protected evidence authority
must authenticate the receipt and principal before the disposition can affect
a verdict. `APPLY_EVIDENCE_DISPOSITION` accepts the exact authority subject and
receipt, not a caller-finalized disposition. Under the affected evidence and
current-result locks, it takes a fresh qualified-clock sample, derives one
`ProtectedTimeObservation/v1` with
`phase=EVIDENCE_DISPOSITION_APPLY`, and sets its subject-key digest from the
closed `EVIDENCE_DISPOSITION` projection of the disposition ID and authority-
subject digest. It requires
`observation.trusted_upper_bound_unix_ns < valid_until_unix_ns`; equality is
late. It then inserts the observation, constructs the final disposition with
that exact `application_time_observation`, applies it, and replaces every
affected pointer in one transaction. A missing or replaced clock, mismatched
subject, arithmetic failure, overflow, equality, or later upper bound inserts
neither observation nor disposition and changes no verdict. The caller cannot
supply or backdate application time. A reason is explanatory text, not proof.

For record invalidation, `campaign` names `EvidenceCampaign/v1`; the campaign
subject and tier equal the invalidation; and `evidence` names one
`EvidenceRecord/v1` in that campaign. `affected_claim_ids` must equal that
record's complete ordered `claim_ids` byte for byte. A subset, superset, alias,
duplicate, or reordered sequence is invalid. `invalidity_evidence` names one
exact `EvidenceInvalidityFinding/v1` whose `evidence` equals the invalidation
evidence. The finding's expected and
observed projections must be exact `OracleProjection/v1` bodies under its
referenced `OracleContract/v1`; their oracle IDs, definition references, and
complete ordered fields must match that contract's canonical definition.
`REGISTER_EVIDENCE_INVALIDITY_FINDING` accepts the
finding only when the owner evaluator's independent oracle recomputes the
projections and confirms the one named acquisition-procedure,
oracle-independence, required-input, tool, limits, or retained-evidence defect.
The evidence-authority login may only reference that registered finding through
`APPLY_EVIDENCE_DISPOSITION`; it cannot submit, alter, or select the finding or
its verdict. Free text, an unregistered defect class, or a caller assertion
cannot invalidate a record. A wrong campaign, claim, subject, tier, run, or
oracle binding makes a record invalid during ordinary
evaluation and needs no disposition. In either case, a valid
`oracle_result=FAIL` cannot be invalidated.

The invalidation applies to the record as one atomic unit. The evaluator finds
every current or historical claim-tier result that references it, retains the
record, finding, authorization, and invalidation bodies, removes that record
from validity for every such result, recomputes every affected result, and
replaces all affected current pointers in one transaction. It cannot commit a
claim-local invalidation or leave the record valid for another bound claim.
Because an `EvidenceRecord/v1` has exactly one tier, any cross-tier reference
is itself invalid; if one is nevertheless present in retained state, the same
transaction fails closed and recomputes every referencing tier rather than
partially applying the disposition.

For campaign supersession, `campaign` names `EvidenceCampaign/v1`,
`prior_result` names the exact current claim-local `EvidenceTierResult/v1`, and
their claim, tier, campaign, and protected current subject equal the
supersession fields. `replacement_campaign` names another
`EvidenceCampaign/v1` with a different campaign identity for the same claim and
tier and the current protected subject. The replaced campaign may name that
subject or an earlier one; this is how new evidence replaces a `STALE`
campaign. Supersession changes only the selected campaign. It neither
invalidates nor changes any verdict in the prior campaign. No `WAIVE`,
`OVERRIDE`, or result-reclassification disposition exists.

The protected invalidation registry is unique on `evidence`; exact bytes
replay, and different bytes conflict. The protected supersession registry is
unique on `(campaign, claim_id, tier, prior_result)` with the same replay rule.
Supersession edges form one contiguous acyclic chain
with at most one successor from each campaign for that claim and tier. A
concurrent second replacement, skipped link, cycle, replacement for a
noncurrent subject, or changed prior body is `CONFLICT`; no campaign is
selected from an ambiguous branch. The current campaign is the unique leaf.
Authorization and the qualified application-time bound are checked before the
disposition is durably accepted; later
expiry of the authorization interval does not resurrect invalid evidence or
change the immutable supersession chain.

Each claim has one deterministic verdict at each tier: `NOT_REQUIRED`, `OPEN`,
`PREREQUISITE_BLOCKED`, `PASS`, `FAIL`, or `STALE`. The registered evidence and verdict computation
above is normative. In particular, absence before a campaign starts is
`OPEN`; after start, any missing, invalidated-without-replacement, skipped,
unexplained, aborted, omitted, shortened-budget, over-budget,
generator-failed, or unreproducible required run is `FAIL`. A valid deciding
`FAIL` dominates every passing record.

An expected negative result is classified by its oracle: the expected refusal
is `PASS`, and an unexpected outcome is `FAIL`. It is never invalidated or
superseded merely because the exercised operation returned an error.

Evidence classes belong to one deciding tier: `EV-DES` to design; `EV-REF`,
`EV-VEC`, `EV-PG`, `EV-FLT`, `EV-LEG`, and `EV-ACL` to implementation;
`EV-CLK`, `EV-PHY`, and `EV-CAP` to release qualification; and `EV-DEP` to
deployment admission. A registry row naming classes from more than one tier
defines separate tier requirements; it does not combine them into one minimum.

A tier passes only when every exact prerequisite-result reference remains the
protected current `PASS` result and every claim required at that tier is
`PASS`. A later `OPEN`, `PREREQUISITE_BLOCKED`, `FAIL`, or `STALE` verdict does not
rewrite an earlier tier's recorded verdict. In particular, `EV-DEP` is never
an implementation or release-qualification requirement. Deployment still
requires the exact passing release-qualification receipt named by its
`EV-DEP` record.

No claim is described as passing without a tier. A claim is complete only when
every required tier declared for it is `PASS`; until then, earlier passing
verdicts remain historical tier results, and the claim is incomplete. A
replacement campaign starts with its own immutable evidence and verdict and
cannot omit, edit, apply a disposition to, or rewrite the prior campaign. A
design decision may therefore be accepted while `JAC-DUR-01` remains open at
release and deployment, but it may not be described as physically proven.

## Apply state matrix

Each implementation evidence pack exercises every row at `U < expiry`,
`U == expiry`, and `U > expiry` where the row can sample time, plus before
commit, committed before acknowledgement, and acknowledged outcomes where the
row can commit.

| Boundary | Required result |
| --- | --- |
| `J` absent | The exact immutable `authority=NONE` preimage binding and its `ProtectedRollbackCiphertext/v1` must already have been created before plan issuance, and the protected PostgreSQL byte row must verify by its exact digest and length and equal the action binding approved in the plan. Construct only non-clock journal inputs before the protected transaction. Under its locks, record the exact `PreStageExpiryObservation/v1`, then finalize and canonicalize `J`, and atomically adopt the binding and protected byte row into journal-owned state only when the fresh pre-commit `U` is strictly below the one grant/plan/approval/authorization expiry. Equality or greater records only `LATE`; a descriptor without protected bytes or invalid policy, attestation, topology, active epoch, evidence prerequisite, continuity, or clock state records no trusted observation, `J`, or adoption. Same-key recovery returns only a committed exact `J`, both adoptions, and observation; an absent or aborted `J` is never recreated from an earlier time. Commit or acknowledgement may occur after expiry without erasing `J`, but every later stage repeats its own live gates. |
| `J` durable, `P` absent | Repeat the closed pre-stage procedure and atomically append one exact `P` only with `CURRENT`. Equality or greater records only `LATE` and no `P`. Exact committed `P` and its observation remain recoverable after expiry. |
| `J→P`, `R` absent | `U < expiry` records exact `VALID`; equality or greater records exact `LATE`. An original transaction may commit after expiry. An absent, aborted, or unresolvable transaction is never backdated; a bounded unresolved query returns `QUALIFICATION_AMBIGUOUS`. |
| Valid `J→P→R`, `M` absent | Mutation is allowed only while the exact grant, plan, approval, and authorization receipt remain the current unrevoked identities; the independently timed deployment policy, attestation, evidence, clock, capability, identity, and epoch gates remain unexpired; and the session witness, lineage, generation, target identity, selected cohort, preserved cohort, protected ciphertext, and every other live admission predicate still match. `M` recomputes the consumed receipt's recorded `U < expiry` but does not compare current time with the grant/plan/approval/authorization shared deadline; the durable timely `R` is the singular decision for that deadline. Lost acknowledgement resolves to all-old or the exact atomic all-new target, receipt, generation, and lineage state. |
| Exact `M`, terminal slot empty | Independent verification may run after the shared operation deadline or epoch fencing. Exact match creates one `V`. A closed-set retryable `UNABLE_TO_VERIFY` appends one immutable, nonauthorizing observation for that stable attempt; a later stable attempt may retry. Mismatch, invariant violation, and unproven target identity each fill the terminal slot permanently and exclude every later `V` or mismatch. `M` is never repeated. |
| Restart | Caller or worker restart with intact adapter session may continue only from the exact prefix. Adapter restart, connection loss, PostgreSQL restart, host reboot, endpoint change, clone, PITR, promotion, or continuity uncertainty fences every unconsumed `R`; verification after exact `M` remains evidence-only and allowed. |
| Lineage race | `J` binds genesis or the exact verified head. `P`, `R`, and `M` recheck it under the transaction-local lineage lock. Exactly one sibling advances; every loser returns `LINEAGE_HEAD_DRIFT`, appends no later authority, and cannot reuse its approval. |
| Historical input | Legacy reader outputs always have `authority=NONE`. Pending, excluded, corrupt, unknown, unreadable, incomplete, or closure-only artifacts cannot create successor apply authority. |
| Preservation | The allowed delta is the exact selected apply rows plus append-only successor evidence. Out-of-cohort rows, completed and failed rows, grants, limits, prior prefixes, historical bytes, and exports remain equal to the prestate. |

## Rollback state matrix

Rollback repeats the same timing, acknowledgement, restart, lineage, and
preservation dimensions under a distinct approval and aggregate.

| Boundary | Required result |
| --- | --- |
| Predecessor admission | `SUCCESSOR_APPLY` requires the exact predecessor `M` and matching `V`. `LEGACY_COMPLETE_APPLY` requires the authenticated manifest selection, complete frozen-reader chain, exact target and generation, explicit genesis, exact encrypted preimage, active writer fence, and no prior successor `M`. Every drift, omission, closure-only case, non-genesis state, or consumed bridge refuses before `J`. |
| Rollback `J` absent | Under the same exact pre-stage observation and strict commit-bound rule as apply, create one rollback `J` that atomically adopts the exact pre-plan binding and protected ciphertext row: the retained predecessor-apply adoptions for `SUCCESSOR_APPLY`, or the immutable nonauthorizing frozen-reader-verified candidates for `LEGACY_COMPLETE_APPLY`. `LATE`, missing protected bytes, digest or length drift, or another invalid observation creates neither `J` nor adoption. The plan and `J` carry the same action binding byte for byte. No private-file copy or apply approval is accepted, and no synthetic legacy `M` or `V` is created. |
| Rollback `J` durable, `P` absent | Repeat the closed pre-stage procedure and append one later exact proof only with `CURRENT`. `LATE` creates no `P`; exact committed proof and observation remain recoverable but cannot be newly qualified from a late or absent receipt. |
| Rollback `J→P`, `R` absent | Apply the same checked `U < expiry`, equality-late, lost-ack, bounded ambiguity, and no-backdating rules as apply, under the rollback approval. |
| Valid rollback `R`, `M` absent | Resolve and verify the adopted protected PostgreSQL ciphertext bytes by exact digest and length, decrypt and restore the selected preimage once, advance target generation once, write `M`, and advance lineage atomically. A private file cannot repair missing protected bytes. Lost acknowledgement resolves to all-old or the exact all-new restoration. The ciphertext remains retained through matching `V`; grants, limits, prior evidence, completed and failed rows, and out-of-cohort rows do not change. |
| Exact rollback `M`, terminal slot empty | Only an independent exact post-restoration match creates `V`. Verification may run after expiry or fencing. A closed-set retryable `UNABLE_TO_VERIFY` records one immutable, nonauthorizing observation for that stable attempt; a later stable attempt may retry. Mismatch, invariant violation, and unproven target identity are terminal and permanently exclude later `V` or mismatch. |
| Restart and race | Loss of continuity fences an unconsumed rollback `R`. A committed restoration never repeats. Two aggregates bound to one predecessor or genesis produce exactly one `M`; every loser reports lineage drift. |
| Preservation | The allowed delta is the exact selected restoration plus append-only successor evidence. A selected-row-only comparison is insufficient; `OR-EVID` must prove the complete unaffected evidence set. |

## Deterministic fault injection

Implementation evidence provides a named failpoint on both sides of every
decisive action:

- before the PostgreSQL statement that could create a stage or mutate target
  state;
- before commit;
- after server commit but before client acknowledgement;
- after acknowledgement but before the caller records progress;
- while an exact same-key recovery query is blocked; and
- after each recovered durable prefix before the next transition.

The matrix crosses those points with caller failure, adapter failure,
connection loss, PostgreSQL failure, deadline equality, admission replacement,
epoch fencing, lineage race, verification race, target drift, and same-key
retry. A deterministic test records which failpoint fired and proves that the
oracle inspected the recovered database rather than trusting the interrupted
caller.

Fault injection that mocks commit completion can prove controller branching;
only a real PostgreSQL transaction can prove constraints, locks, and atomic
logical effects. Killing a disposable server without a qualified storage
profile is still implementation evidence, not physical durability evidence.

## Historical fixture evidence

The public acceptance corpus is finite and closed by one immutable
`hindsight-postgresql-historical-corpus-plan/v1` accepted before execution.
The plan binds the historical-registry digest, frozen-reader identities, exact
member-to-tool execution bindings, fixture IDs, generator identities, seeds,
budgets, limits, and expected-result oracle. It contains one valid base fixture
for every supported artifact-kind, schema, reference-plan tuple, and the
kindless authenticated `requeue-plan` dependency member, then covers these
equivalence classes:

- representation: valid exact bytes, required-field omission, unknown or
  duplicate field, wrong type, malformed encoding, and scalar or collection
  boundary;
- dependency graph: each lifecycle-link role, missing dependency, substituted
  role, digest mismatch, duplicate edge, cycle, and unsupported predecessor;
- lifecycle and disposition: pending, complete, failed, closure-only, excluded,
  corrupt, unknown, unreadable, and known but undispatchable;
- artifact integrity: regular file, non-regular file, symlink, path
  replacement, size drift, byte drift, disappearance, and read failure;
- compatibility projection: raw-identity preservation, semantic projection,
  `authority=NONE`, permitted-next-action refusal, and rollback-bridge
  eligibility or ineligibility; and
- version handling: every registered version boundary plus representative
  lower, higher, and unknown-future versions.

The corpus includes every one-factor normative rejection, pairwise coverage
across independent class axes, and explicit higher-order interactions named by
the publication, restart, or compatibility contract. It does not claim
exhaustiveness over all byte strings, graph shapes, or cross-products.

For every generative class, the accepted plan fixes a positive case budget,
deterministic seed set, maximum bytes, depth, nodes, and edges, shrink budget,
and execution limit. Each failure retains its reproducible seed and minimized
case. Before its corresponding `EvidenceCampaign/v1` starts, absent execution
evidence is `OPEN`. Once that campaign starts, a skipped class, shortened
budget, unreproducible failure, generator failure, omitted run, unexplained
result, or abort is `FAIL`. Exact numeric budgets belong to the separately
accepted corpus plan, not this contract.

Each public fixture runs differentially through:

1. the exact immutable `READER` tool bound to its revision-pinned frozen
   reader member;
2. the successor discovery and compatibility projection; and
3. an independent expected-result oracle.

The frozen reader remains the authority for historical interpretation. The
successor projection passes only when `OR-LEG` proves identical raw identities
where the historical contract preserves raw bytes, identical semantic digests
and dispositions where it projects semantics, and fail-closed behavior for
every malformed or unknown input.

Representative real artifacts may reveal combinations absent from the public
corpus, but they can contain retained content and private deployment metadata.
They therefore run only in an approved private evidence store. The complete
deciding record, including every input and output identity required by its
oracle, remains private.

A controlled package is exactly one
`ControlledPrivateEvidencePackage/v1`. Its `deciding_run_result` names
`EvidenceRunResult/v1` for one `EV-LEG` implementation run and resolves its
complete deciding `EvidenceRecord/v1` set. Package claims, oracle IDs, subject,
tier, evidence class, result, and exact `EvidenceLimits/v1` reference equal that
run and its records. Its `real_artifact_binding` and `artifact_mode` equal the
exact binding and declared mode in the run, stimulus, and plan cell. The
binding resolves the payload, governing policies, and authenticated provenance
chain, and its payload appears as the package's `INPUT` member. Its
artifact sequence contains every private input, expected output, observed
output, and diagnostic artifact those records require and no unreachable
member. Members sort by displayed artifact-class order, then ASCII artifact ID,
then canonical artifact-reference bytes; IDs are unique.

The closed bounds are: 1–64 claims, 1–16 oracle IDs, and 1–64 artifact members;
at most 16,777,216 bytes per referenced artifact; at most 67,108,864 referenced
artifact bytes in total under checked unbounded addition; and at most 262,144
successor-canonical bytes, including the LF, for the package body. The complete
private deciding records remain subject to their stricter accepted
`EvidenceLimits/v1`. A missing member, unreachable member, duplicate, wrong
order, bound overflow, or incomplete deciding reference rejects package
registration.

Only `hindsight_journal_private_registrar_login`, through
`REGISTER_CONTROLLED_PRIVATE_PACKAGE`, may register the package.
Its stable key is `(package_id, deciding_run_result)`; `public_record_id` is a
fresh random UUID not derived from private content and is unique across
packages. Exact same-key bytes replay, while changed package bytes, a reused
public record ID, or a second package for the deciding run conflict. The
registrar stores the complete body only in the approved private evidence store
and exposes none of its references through a public query.

A public disclosure is the exact pair of one
`BoundedPublicEvidenceProjection/v1` and one
`IndependentEvidenceReviewReceipt/v1`. The projection contains only a random
public record ID, evidence class, claim and oracle IDs, tier, public subject
identity, declared real-artifact mode, result, limits, commitment scheme and
value, and independent review ID. It has 1–64 claims, 1–16 oracles, and at most
32,768 canonical bytes,
including the LF. Claim and oracle sequences use registry order. It publishes
no package reference or package digest, artifact bytes, retained content, raw
input digest, expected-output digest, observed-output digest, stable
content-derived identifier, private path, real-artifact binding, provenance,
nonce, commitment key identity, or other private field.

The projection's protected public stable key is `public_record_id`; its
independent review ID is unique. Exact bytes replay, while changed bytes under
either identity conflict. The public record ID must equal the private package's
random disclosure ID under the protected mapping, but public verification
cannot reverse that random identity into a package reference or content
identity.

The public-projection policy's `allowed_fields`, `commitment_schemes`, and
`forbidden_fields` sequences contain every displayed enum value in their schema
order exactly once. The projection's complete key set is exactly the twelve
allowed fields plus `kind` and `schema_version`; the policy enum
`INDEPENDENT_REVIEW_ID` maps to `independent_review_id`. No extension bag,
free-form metadata, optional field, or omitted allowed field exists in v1.

Let `B` be the complete successor-canonical package bytes, including the LF,
and let `L64(B)` be the unsigned 64-bit big-endian encoding of `length(B)`.
The commitment message is exactly:

```text
UTF8("hindsight-controlled-private-evidence-package/v1")
|| 0x00
|| L64(B)
|| B
```

For `RANDOMIZED_SHA256`, the private package has
`commitment_key_id="NONE"` and `commitment_nonce_base64url` is exactly 43
unpadded base64url characters decoding to 32 fresh OS-CSPRNG bytes. Those bytes
are already inside `B`; the commitment is `SHA-256(message)`. For
`KEYED_HMAC_SHA256`, the package has
`commitment_nonce_base64url="NONE"`, `commitment_key_id` names one dedicated
campaign-review key, and the commitment is `HMAC-SHA-256(key, message)` with an
unexported key of at least 32 OS-CSPRNG bytes that is never reused by another
campaign. The public projection and review receipt copy the package's exact
scheme and computed 64-lowercase-hex value. Neither scheme is a signature or
operation authority. Nonce and key material remain private.

The independent reviewer receives the exact registered package and, inside the
approved private evidence store, its nonce or keyed-HMAC capability. The
reviewer resolves every typed reference, checks all bounds and reachability,
authenticates the real-artifact binding and complete private-store acquisition
or sanitization provenance, recomputes the deciding records and named oracles,
recomputes the commitment, and derives the public projection field by field.
The receipt's `checks` sequence contains all ten enum values in displayed
order, exactly once. Its
`public_projection` names the exact projection body; `review_id` equals the
projection's `independent_review_id`; and its public record ID, evidence class,
claim IDs, oracle IDs, tier, public subject identity, real-artifact mode,
result, limits, scheme, and commitment equal the public projection field for
field. The evidence class, claims, oracles, tier, real-artifact mode, result,
and limits also equal the privately reviewed package. The package commitment
therefore binds the exact private real-artifact reference without disclosing
it; the random public record and public subject identities are review-derived
disclosure identities and never private package identities. The
receipt is at most 49,152 successor-canonical bytes including its LF, has
exactly one reviewer identity and nonempty principal, and contains no package
reference, package digest, nonce, key identity, or private artifact field.

Only `hindsight_journal_private_reviewer_login`, through
`REGISTER_CONTROLLED_PRIVATE_REVIEW`, may store a receipt. It authenticates the
configured reviewer identity and principal against the protected private
package-to-review mapping, and it gives the receipt stable key
`(review_id, public_projection)`. Exact retry returns byte-identical bytes; a
changed body conflicts. The receipt-currentness rule is the exact locked rule
in the access model: package mapping, deciding evidence, selected campaign and
subject, policies, reviewer authorization, projection, and current receipt
pointer must all remain exact. The protected public-export boundary, callable
only by `hindsight_journal_public_evidence_exporter_login`, publishes only when
the projection and current authenticated receipt both pass every check and
the projection is derivable exactly from the private package. An unknown kind
or version, missing or extra field, reference or commitment mismatch, stale or
conflicting receipt, private-field leak, or any exceeded bound fails closed and
publishes neither a partial projection nor a receipt. Public verification checks
the exact registered projection and receipt, their mutual IDs and fields, and
receipt authentication; it never needs access to the private package.

Finite authenticated real-artifact evidence is required in its separate
private partition. It cannot replace a required public equivalence class,
waive a failed public case, or broaden the declared registry, discovery roots,
or writer inventory. A sanitized payload can satisfy `SANITIZED_REAL` only
through its exact authenticated real-source acquisition and sanitization
provenance chain. Publishing the same or similar bytes as a public fixture
creates a new synthetic identity that cannot satisfy either real-artifact
mode.

Private-publication conformance uses synthetic placeholder packages, never
actual private data. It covers both commitment schemes with fixed package-byte,
length-prefix, nonce/key, and expected-commitment vectors; every zero, maximum,
maximum-plus-one, total-byte overflow, body-byte overflow, ordering, duplicate,
missing, extra, unreachable, wrong-kind, wrong-version, digest, and deciding-run
reference case; controlled-private acquisition and sanitized-real provenance
chains; public synthetic bytes substituted for either mode; policy, mode,
payload, source-acquisition, sanitization-procedure, and provenance
substitution; each allowed and forbidden projection field; every receipt field
and check ordering; reviewer identity and protected mapping
authentication; exact replay and changed-body conflict; and an export failure
at each validation step. Every negative case publishes neither a partial
projection nor a receipt.

## PostgreSQL implementation qualification

Implementation evidence uses an actual PostgreSQL server and the implemented
protected interfaces. It proves:

- exact uniqueness, foreign keys, append-only rules, terminal-slot exclusion,
  and impossible-hole rejection;
- serializable target mutation and lineage advancement;
- same-key replay and changed-binding conflict;
- transaction-local lineage races for identical, overlapping, merging, and
  disjoint cohorts;
- all-old or all-new recovery after every deterministic fault point;
- database-role denial for every cross-boundary protected operation;
- frozen-reader and manifest activation behavior; and
- full target and unaffected-evidence equality before and after apply and
  rollback.

The evidence receipt records the server version and build, effective
`synchronous_commit`, `fsync`, `full_page_writes`, and `wal_sync_method`, schema
digests, migration digest, extension set, role grants, isolation levels, and
test topology. A test fixture with `fsync=off`, an unrecorded setting, or a
nonqualified filesystem may support logical SQL behavior only. Its receipt
must say so and must not satisfy `EV-PHY` or `JAC-DUR-01`.

## Release support-profile qualification

`SupportProfile/v1` binds the exact:

- Hindsight release, protected-schema, adapter, admission-controller, and
  migration identities;
- typed controller-host, PostgreSQL-host, PostgreSQL-endpoint, and deployment-
  topology bodies, including the locality class and any network-path identity;
- macOS version and build, hardware model, stable required boot mode and boot
  configuration, and virtualization boundary, but no boot-session identity;
- PostgreSQL version and build plus effective `synchronous_commit`, `fsync`,
  `full_page_writes`, and `wal_sync_method`;
- filesystem type and version, mount options, volume manager, encryption,
  redundancy layer, storage controller, device model, firmware, volatile cache,
  and power-loss-protection claims;
- selected nondecreasing clock, suspend behavior, unit conversion, maximum
  error model, synchronization source, per-envelope boot binding, and
  invalidation rules;
- protected pre-commit clock sampling and start-decision behavior for `J` and
  `P`, including equality-late refusal and post-commit recovery when commit or
  acknowledgement occurs after the sampled bound;
- one exact closed `ClosurePolicyLimits/v1` body whose positive finite attempt,
  case, reservation, resolution, call, observer-lease, lock, statement,
  transaction, idle, and connection bounds the named PostgreSQL and controller
  releases enforce;
- failure injector, reboot or out-of-band power controller, and cold-recovery
  procedure; and
- explicitly unsupported variations.

The initial candidate profile is named `macos-local-postgresql-v1`. The name is
not a qualification result. Its eight component references must resolve to the
closed configuration bodies above with `family=MACOS`,
`filesystem_type=APFS`, and `clock_source=MACH_CONTINUOUS_TIME`; the
PostgreSQL configuration must resolve its exact `PostgresqlSettings/v1` and
positive configured port and contain the complete canonical effective
`unix_socket_directories` sequence. Its clock and storage fields remain
unqualified until the release campaign records and passes their exact behavior.
Any unknown or changed field is a different profile and leaves mutation fenced.

That profile has one representable deployment boundary. Its
`deployment_topology.locality` is exactly `SAME_HOST_LOCAL`; the topology's
controller-host, PostgreSQL-host, and endpoint references equal the three
references in `SupportProfile`; the two host bodies carry the same exact
`host_identity`; each host body's `operating_system_profile` equals the
profile's top-level operating-system component; the PostgreSQL host body's
`postgresql_profile` and `storage_profile` equal the corresponding top-level
components; and both host bodies' `boot_configuration` equals
`body(SupportProfile.boot_configuration).configuration`. The endpoint targets the
profile's exact target database and has `transport=UNIX_DOMAIN_SOCKET`,
`port="NONE"`, and an absolute, normalized, complete socket pathname in
`address`. The PostgreSQL configuration has exactly one socket-directory
member, the endpoint's `unix_socket_directory` equals it byte for byte, and
that member's configured and resolved paths and device/file identity satisfy
the normalization and stable-resolution rules above. The pathname is exactly
the member's resolved path joined with `/.s.PGSQL.` and the decimal
`body(body(SupportProfile.postgresql_profile).configuration).postgresql_port`;
a directory alone, a relative path, an alternate spelling, another member,
member reordering, or a TCP port is invalid.
`network_path_identity` is `"NONE"`. Every TCP transport,
`MANAGED_SERVICE`, `REMOTE`, `MANAGED`, a hostname or address, a controller and
PostgreSQL host mismatch, an endpoint target
mismatch, or endpoint identity/address/port/transport drift is another support
profile and is ineligible for `macos-local-postgresql-v1`.

The protected profiler constructs one
`MacosLocalPostgresqlLiveProjection/v1`; callers cannot submit it. It derives
the claimed OS product and kernel values from the host OS interfaces, the
PostgreSQL version, settings, data directory, configured port, target identity,
complete effective socket-directory sequence, each opened directory's resolved
path and identity, and derived socket pathname from the selected live server,
the APFS mount and volume
properties from the filesystem hosting the data directory, the hardware and
storage identities from the local host and backing device, the actual boot
identity and virtualization identity from the current host session, and the clock fields
from the qualified clock implementation. `collected_at` is the protected
qualified observation for that collection. Each referenced configuration body
must be byte-identical to its support-profile component configuration; the
projection's boot identity must equal its protected collection observation's
clock-envelope boot identity; the live sequence must equal the configuration
sequence and the endpoint's sole member;
and all live observations must describe one host, boot, server, target, data
volume, and collection transaction. An unavailable field, mixed collection,
generic placeholder, or nonexact projection is unqualified.

Qualification and deployment admission independently resolve and compare that
live projection, all four complete binding bodies, and every nested component
equality above. They also require the qualification plan, receipt, deployment
attestation, and live guard capabilities to bind the profile's exact
closure-policy reference and values. They reject remote PostgreSQL, managed
PostgreSQL, a proxy or tunnel that changes the endpoint or locality, and any
post-qualification endpoint drift with the exact failure code above. Similar
clock, storage, or server behavior cannot substitute for exact deployment
equality.

Before any qualifying run, Ivan separately accepts one immutable
`hindsight-postgresql-qualification-plan/v1` through the authenticated,
protected plan-acceptance record defined above. The plan binds the
exact support profile and release identities, tool and independent-oracle
identities, acquisition procedures, typed limits, deterministic case matrix,
randomized schedules, numeric run counts and budgets, environment-reset
procedure, cold-recovery procedure, evidence-retention rules, abort rules, and
acceptance thresholds. Every qualification result carries a typed reference to
those exact plan bytes. A pre-acceptance run cannot satisfy it. Changing the
plan creates a new body digest and campaign; prior runs and their verdict remain
historical and cannot be selected into the replacement campaign.

The accepted plan cannot omit any of these mandatory coverage classes:

- for both apply and rollback, every `J`, `P`, `R`, `M`, and `V` boundary before
  commit, committed before acknowledgement, and acknowledged before caller
  progress;
- caller, adapter, connection, and PostgreSQL failure, plus forced reboot and
  storage power interruption across durable-prefix transitions and both sides
  of the atomic `M` window;
- physical interruption under WAL, checkpoint, and volatile-write-cache
  pressure;
- `U` below, equal to, and above approval expiry, and the monotonic sample
  below, equal to, and above the envelope validity deadline;
- checked conversion, upward rounding, rate-error, underflow, overflow, and
  invalid-denominator cases;
- closure-policy millisecond conversion, case-expiry derivation, every
  reservation and resolution deadline candidate, earliest-bound selection,
  full-resolution margin, and equality-late behavior;
- wall-clock steps in both directions after envelope issuance, monotonic
  regression or reset, reboot or boot-identity change, supported and uncertain
  suspend/resume behavior, synchronization-epoch change, stale synchronization
  evidence, and excessive error; and
- exact cold recovery through an independent reader, including the durable
  prefix, all-old or all-new target and lineage state, no repeated effect,
  terminal verification state, and every unaffected evidence item.

The stage, interruption, pressure, and cold-recovery classes above must appear
in `EV-PHY` cells; the time arithmetic, clock step, monotonic, boot, suspend,
synchronization, and error classes must appear in `EV-CLK` cells. A class
result can cover only cells carrying its own `evidence_class`. Together, the
three class-result cell sequences must equal the plan's complete cell sequence
without omission, duplication, or reassignment.

The same accepted plan must contain exactly one `EV-CAP` plan cell for each of
these `condition` values. Each cell has a positive deterministic run count and
an exact case matrix that exercises every positive and negative state named by
the condition:

- `capability-os-csprng-entropy`: source identity, successful generation, and
  a lower bound of 256 fresh bits per activation;
- `capability-persistent-cleartext-domains`: exactly the locked nondumpable
  adapter allocation and session-local non-WAL relation, with every other
  persistent domain rejected;
- `capability-transient-copy-domains`: exactly the declared adapter, local
  Unix-domain transport, PostgreSQL protocol, and bound backend process;
- `capability-controllable-zeroization`: post-call and continuity-loss
  zeroization for every controllable buffer, without treating it as the safety
  predicate;
- `capability-sql-and-parameter-logs` and `capability-error-detail`: absence
  from statement text, parameter logs, diagnostics, and returned error detail;
- `capability-core-dumps` and `capability-swap`: qualified denial or exclusion
  from process dumps and swap;
- `capability-temporary-and-spill`, `capability-files`, `capability-wal`, and
  `capability-backups`: absence from each named temporary or durable surface;
- `capability-session-witness`: installation only on the exact activation
  session and successful same-session digest comparison;
- `capability-reconnect-restart-continuity-loss`: reconnect, adapter restart,
  PostgreSQL restart, host reboot, endpoint change, PITR, clone, promotion, and
  uncertain continuity all lose or invalidate the witness; and
- `capability-fail-closed-mutation-predicate`: absent, wrong, stale,
  cross-session, or uncertain witness state makes `M` fail before target,
  receipt, generation, or lineage mutation; and
- `closure-guard-capabilities`: every exact positive finite lock, statement,
  transaction, idle-in-transaction, observation-call, and connection-lifetime
  guard in the profile's closure policy is supported; claim and takeover derive
  the exact observer-lease deadline from its positive finite policy duration; a
  forced expiry releases locks; and no disabling zero or unbounded fallback is
  accepted.

These cells decide the profile-specific `EV-CAP` result through `OR-CAP`, with
`OR-FENCE` and `OR-ACL` checking the live-session and privilege boundaries.
Read-only deployment admission can confirm that the installed release,
protected activation interface, and capability policy match a passing receipt;
it cannot observe or validate an activation witness that does not yet exist,
and it cannot itself prove entropy, copy domains, zeroization, or exclusion
from dumps, swap, logs, files, WAL, or backups. Combined activation alone
installs and validates the session witness; each `M` recomputes and validates
the exact witness-to-admission equality before mutation.

This contract does not choose numeric run counts. The separately accepted plan
assigns a positive deterministic allocation to every mandatory cell and a
repeated randomized allocation across stage, failure timing, pressure state,
and clock state. It records the selection method and reproducible seeds before
execution; a single global smoke run or a zero-budget mandatory cell is
invalid.

The campaign fails on the first unexplained outcome, cannot omit or relabel a
failed run, and passes only after the complete accepted plan has executed. Any
changed profile or release identity requires a newly accepted plan and
requalification.

This profile does not claim survival of permanent primary-disk loss,
asynchronous archive loss, malicious firmware, privileged host or database
compromise, or a platform outside its exact bindings.

## Deployment admission evidence

Deployment admission independently reads the installed target and records the
pre-attestation observations in one accepted `EV-DEP` campaign. Only after the
evaluator derives its complete exact deployment-tier `PASS` partition may the
admission finalizer atomically issue and install one current immutable
`DeploymentAttestation/v1`. A stage supplies that body through the exact
`StageAdmission` union. Together they bind:

- the exact current typed `DeploymentAdmissionPolicy/v1` reference;
- the exact `PASS` qualification receipt, its exact accepted qualification
  plan, all three exact class results, support profile, release identities, and
  complete current design-, implementation-, and release-tier partitions;
- the exact accepted deployment campaign and its complete current
  deployment-tier partition and complete ordered protected acquisition bodies
  for every deciding live projection;
- current PostgreSQL settings and protected-schema digest;
- database, storage, clock-envelope, and synchronization-epoch identities plus
  the exact typed controller-host, PostgreSQL-host, endpoint, and deployment-
  topology bodies;
- the exact qualified `ClosurePolicyLimits/v1` reference and deployment proof
  that every positive finite database timeout and adapter connection guard is
  supported without a disabling zero or unbounded fallback;
- canonical target surface and mutation-lineage identity;
- the exact typed `RoleGrantSet/v1` and `WriterInventory/v1` bodies independently
  recomputed from the complete live catalog and service paths;
- the proposed reserved successor publication epoch and a conditional
  legacy-writer fence result: `FRESH` proves the fixed target-surface fence slot
  is unoccupied, while an occupied slot requires `COMPATIBILITY`, the exact
  protected current epoch-independent persistent fence evidence, and the exact
  current origin-or-adoption handoff atomically issued for this attestation
  whose `reserved_publication_epoch` equals the proposed epoch; and
- admission generation, issue time, validity deadline, and health result.

Admission fails closed on an unknown or noncurrent kind or version, changed,
revoked, or expired policy, invalid or expired receipt, `OPEN`, `FAIL`, `STALE`,
`PREREQUISITE_BLOCKED`, superseded, invalidated, incomplete, or reordered deployment evidence,
non-`PASS` class result, profile, plan, acceptance, or release mismatch, stale
clock or storage evidence, a missing, substituted, cross-boot, or over-age
acquisition, profiler/finalizer role-grant or writer-inventory inequality, any
unenumerated, unclassifiable, unresolved, duplicate, or extra writer/grant
path, wrong conditional fence result, closure-policy mismatch, unsupported closure guard, or unqualified
profile. It emits the exact controller-host,
PostgreSQL-host, endpoint-drift, endpoint-topology, remote-PostgreSQL, or
managed-PostgreSQL failure for each corresponding negative case. A hostname
alias, proxy, tunnel, or merely reachable database cannot satisfy the exact
local endpoint. The attestation is
consumable by activation, an authority-bearing stage, or a compatibility guard
only while its policy and protected current-attestation references remain
exact, every bound current-result reference remains exact, its health is
`PASS`, and
`issued_at_unix_ns < valid_until_unix_ns` with the current conservative sample
strictly before `valid_until_unix_ns`. A passing attestation is one required
input to a separately authorized activation decision. By itself it neither
activates an epoch nor authorizes an operation or target mutation.

Deployment admission does not repeat `EV-PHY` or invoke `OR-PHY`. `OR-ID` and
`OR-FENCE` resolve the exact protected `QualificationReceipt/v1`, prove its
`physical_durability_result` is the passing `EV-PHY` result for the installed
release and support profile, and bind that receipt into the attestation.
`OR-EVAL` independently verifies the complete deployment partition and current
policy reference without using the proposed attestation as an input. Any
admission attempt that refuses emits one exact nonauthorizing
`FailedDeploymentResult/v1` and no attestation. The `EV-DEP` record passes when
that result equals the row's prebound refusal oracle and fails only for an
unexpected or mismatched outcome.

The deployment matrix has explicit negative rows: `REMOTE`, `TCP_REMOTE`, or
`TCP_LITERAL_LOOPBACK`
must emit `REMOTE_POSTGRESQL_UNSUPPORTED`; `MANAGED` or `MANAGED_SERVICE` must
emit `MANAGED_POSTGRESQL_UNSUPPORTED`; and any changed endpoint identity,
target, socket path, socket-directory configured or resolved path,
device/file identity, sequence count or order, port, or transport under an
otherwise unchanged profile must emit `ENDPOINT_DRIFT` or
`ENDPOINT_TOPOLOGY_MISMATCH` as the schema dictates. A controller-host or
PostgreSQL-host change emits its corresponding host-mismatch code. Every row
produces the expected `FailedDeploymentResult/v1`, no attestation, and no
active or reserved epoch transition; exact oracle equality makes that negative
row pass.

The same matrix separately rejects a relative path, repeated separator,
embedded `.` or `..`, noncanonical trailing separator, missing or nondirectory
target, symlink retarget or resolution race, directory-only endpoint, address
derived from the configured rather than resolved path, zero or multiple
initial-profile members, and every omitted, added, duplicated, or reordered
member. Each case uses the exact configuration member, endpoint member, live
projection sequence, and derived address as one binding.

## Evidence record contract

The registered campaign, projection, evidence-record, run-result, tier-result,
historical-corpus-plan, qualification, deployment-attestation, and
failed-deployment schemas above are the normative result contracts. Every
deciding `EvidenceRecord/v1` is immutable. Its exact expected and observed
oracle projections together contain, when required by the named oracle:

- every relevant schema, migration, release, fixture,
  support-profile, and deployment digest;
- action, initial state, stimulus, failpoint, and concurrency schedule;
- observed exact prefix, target, lineage, fence, time, historical disposition,
  role, and evidence-set projections required by its oracles;
- expected result, observed result, and any unresolved ambiguity; and
- explicit scope limits.

The outer record binds those projections to its campaign, planned run, evidence
class, claim, oracle contract, subject revision, tool, acquisition procedure,
limits, exact conformance prestate or real-artifact binding when required,
diagnostic start and completion times, and recomputed `oracle_result`.
The run result and tier result supply the only accepted aggregate verdicts; a
log, report, or deployment status with a similar label is not a result body.

Evidence is invalid when its subject changed during the run, required inputs
are missing, the oracle depends on the production path it is deciding, a failed
run is omitted, or the complete private record lacks a value needed to
reproduce the result. Redaction creates only the public projection; it never
changes the deciding record. Sanitization creates a separately bound
`SANITIZED_REAL` artifact only with its authenticated source and procedure
provenance. Publishing those or similar bytes as a public fixture creates a
new synthetic identity and does not claim byte identity with the private
original.

## Existing seams and their limits

Existing controller canonicalization, broker replay, persistence, migration,
and adapter tests are useful starting seams. A revision-pinned earlier
operation-recovery suite is also useful historical source. They can be reused
only for the claims their actual fixtures observe:

| Existing seam | Reusable observation | Limit |
| --- | --- | --- |
| `tooling/hindsight/tests/test_hindsight_memory_controller.py:649` | Strict canonical JSON rejection and ordering cases | Exercises the existing no-LF helper, not the named successor serializer or independent LF-bearing vectors |
| `tooling/hindsight/tests/test_hindsight_memory_harness_persistence.py:64` and `:176` | Local compare-and-swap, exact prestate restoration, and prepared/committed restart behavior | Multi-file local model, not protected PostgreSQL stages or cold recovery |
| `tooling/hindsight/tests/test_hindsight_memory_adapters.py:5931` and `:6705` | Modeled commit-then-error recovery and post-restore verification | Fake adapter; no PostgreSQL commit ambiguity, durable prefix, or atomic `M` |
| `tooling/hindsight/tests/test_hindsight_memory_broker.py:3581`, `:3735`, and `:4045` | Local conditional append, replay/conflict, simulated crash, and restart | JSONL and modeled state, not target PostgreSQL or physical durability |
| [`test_hindsight_memory_operation_recovery_postgres.py` at `79b9071…`](https://github.com/nisavid/agents/blob/79b9071fd4a296df2064536cffe25d2cc8bc47d6/tooling/hindsight/tests/test_hindsight_memory_operation_recovery_postgres.py#L137-L154) | Real SQL locking, exact-cohort selection, generation drift, preservation, restoration, and idempotent rollback seams | Historical exact-drain protocol, not successor stages; the fixture starts PostgreSQL with `-F`, so it is logical SQL and concurrency evidence only |
| [`test_hindsight_memory_operation_recovery.py` at `79b9071…`](https://github.com/nisavid/agents/blob/79b9071fd4a296df2064536cffe25d2cc8bc47d6/tooling/hindsight/tests/test_hindsight_memory_operation_recovery.py#L716-L809) | Grant, deadline, progress, and legacy plan-verifier seams | Plan-verifier behavior and a small synthetic input set, not a complete historical corpus or successor durable authority |

The earlier real-PostgreSQL suite's `-F` switch disables `fsync`. No result
from that fixture can prove WAL durability, acknowledged-stage survival,
reboot or power-loss recovery, storage-cache honesty, or support-profile
admission. All operation-recovery tests preserve a historical contract and
cannot be treated as evidence that an unimplemented successor protocol already
works.

The implementation map must trace every reused test to a claim and oracle and
add the missing successor subject, fault point, or independent projection.
Historical green status alone is not acceptance evidence.

## Current evidence disposition

At the accepted #75 base, the repository has no executable successor reference
model, independent successor vectors, protected `J/P/R/M/V` PostgreSQL schema,
per-stage crash harness, complete historical differential corpus, qualified
clock, realized role matrix, physical or capability support-profile campaign,
or deployment attestation. The richer operation-recovery suite exists only in
the earlier revision named above and tests the historical exact-drain protocol.

Accordingly, this record closes the acceptance contract, not an acceptance
result. It closes `JAC-ARC-01` only as a traceable design claim after #77's
independent assessment. Every implementation, release, and deployment claim
remains open until its exact later-tier evidence exists.

## Gate results

### Design evidence complete

The #76 contract is complete only when:

- every accepted architecture, restart, and compatibility claim maps to the
  registry, a state-matrix row, or an explicit out-of-scope statement;
- the contract defines the required independent successor-vector and
  clock-arithmetic suites with no unresolved representation choice;
- each claim names its evidence classes, oracles, every required tier, and
  limits;
- apply and rollback separately cover expiry, ambiguity, restart, lineage,
  target effects, verification, historical input, and preservation;
- the macOS local PostgreSQL profile has a complete qualification protocol even
  though its results remain a release gate.

Passing this gate means only that the design has a falsifiable acceptance bar.
After #76 closes, #77 independently assesses the integrated record; #78 cannot
decide before that assessment.

### Design accepted for implementation planning

Ivan may accept the integrated design in #78 only after #77 reports the exact
revision it assessed, every unresolved claim or overclaim, and whether this
evidence contract is sufficient. Acceptance authorizes a separate
implementation-planning map. It does not authorize source changes, database
provisioning, deployment, candidate assembly, or live recovery.

### Later gates

The implementation map must preserve every deferred implementation, release,
and deployment obligation as a blocking requirement. It cannot mark a claim
complete until every required tier in that claim's registry row is `PASS`.
Release and deployment gates remain not passed until their exact receipts
exist and pass; live action remains unauthorized until its own exact plan and
approval exist.

## Non-goals and authorization boundary

This record does not:

- choose SQL relation, column, index, package, or CLI names;
- implement successor schemas, protected functions, roles, serializers,
  readers, adapters, failpoints, or qualification tools;
- qualify macOS, PostgreSQL, a clock, filesystem, device, controller, or
  release;
- attest or activate a deployment;
- normalize, rewrite, backfill, decrypt, publish, or retire historical
  evidence;
- create or migrate a journal, aggregate, lineage, fence, or rollback preimage;
- call a provider or inspect retained content;
- assemble or run a candidate; or
- change a live grant, claim, cohort, row, worker, provider, or recovery state.

Implementation and qualification require accepted later plans. Deployment and
live mutations also require exact authorization.

## Related records and remaining gates

- [#73](https://github.com/nisavid/agents/issues/73) selects target PostgreSQL
  as publication owner and records the
  [publication design](journal-publication-design.md).
- [#74](https://github.com/nisavid/agents/issues/74) records the
  [restart design](journal-restart-design.md).
- [#75](https://github.com/nisavid/agents/issues/75) records the
  [compatibility and cutover design](journal-compatibility-design.md).
- [#76](https://github.com/nisavid/agents/issues/76) records the acceptance bar
  defined here.
- [#77](https://github.com/nisavid/agents/issues/77) owns independent assessment
  of the integrated design and this evidence contract.
- [#78](https://github.com/nisavid/agents/issues/78) owns Ivan's final design
  acceptance and the gate to a separately authorized implementation-planning
  map.
