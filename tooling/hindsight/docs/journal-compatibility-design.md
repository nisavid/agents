# Hindsight Journal Compatibility and Cutover Design

Status: historical compatibility and cutover behavior selected in
[#75](https://github.com/nisavid/agents/issues/75). Ivan approved the integrated
design on 2026-09-01. The PostgreSQL publication architecture selected in
[#73](https://github.com/nisavid/agents/issues/73) and the interrupted
publication and restart contract selected in
[#74](https://github.com/nisavid/agents/issues/74) remain fixed. Acceptance
evidence, independent assessment, and final design acceptance remain open in
[#76](https://github.com/nisavid/agents/issues/76) through
[#78](https://github.com/nisavid/agents/issues/78). Implementation, deployment,
cutover execution, candidate assembly, and live recovery remain separately
authorized work.

## Decision

Historical private-file journals and successor PostgreSQL publications are
separate protocol families. Historical bytes retain their original schemas,
canonicalization, timestamp meanings, and frozen read-only decoders. The
successor protocol begins a new family,
`hindsight-postgresql-publication/v1`; it does not
continue a historical schema-number sequence or reinterpret a legacy field.

Legacy readers produce authenticated evidence with `authority=NONE`. Their
outputs may classify and display historical state, bind a cutover declaration,
or support a nonauthorizing closure observation. They cannot create or imply a
successor `J`, `P`, `R`, `M`, or `V`, authorize target mutation, or
become a fallback authority when PostgreSQL state is absent.

Cutover is declared independently for each canonical target surface. An
immutable authenticated manifest binds the exact legacy inventory, frozen
reader contracts, evidence dispositions, live target snapshot, old-writer
fence, exact approved exclusions, and successor genesis. Unknown or corrupt
artifacts and known but undispatchable artifacts block cutover unless each
entry's safely readable, stable, current exact bytes are covered by a separate
exact exclusion approval. An exclusion never validates the artifact,
authorizes mutation, or permits deletion.

The only legacy-to-successor mutation bridge is a narrow rollback admission. A
complete, verified legacy apply chain may be named as
`LEGACY_COMPLETE_APPLY` while the successor lineage remains at genesis. A
new, separately approved successor rollback publication may ingest that
chain's exact encrypted preimage as action-scoped input and then proceed
through ordinary successor `J -> P -> R -> M -> V`. The bridge creates no
synthetic apply `M` or `V`, and closure-only evidence cannot qualify.

## Scope and fixed invariants

This record defines:

- protocol-family and version dispatch;
- exact preservation and frozen decoding of historical artifacts;
- the authority-free legacy evidence model;
- historical state dispositions;
- metadata-only closure observations for an already-mutated target;
- authenticated per-target cutover declarations and exact exclusions;
- admission of a complete legacy apply as the predecessor of one new
  successor rollback publication;
- preservation, access, status, and refusal boundaries; and
- the compatibility cases that later acceptance evidence must exercise.

The following invariants apply throughout:

1. PostgreSQL is the sole authority for successor publication and mutation
   state.
2. Historical files are immutable evidence. Their existence, absence,
   timestamp, path, or decoded content never independently authorizes a
   successor mutation.
3. A protocol family and version determine semantics. A bare numeric schema
   version does not.
4. Every accepted legacy artifact is identified by its exact bytes before its
   semantic content is trusted.
5. An unknown, malformed, incomplete, ambiguous, or drifted input fails closed.
   No compatibility path guesses a version, repairs a chain, or manufactures a
   missing stage.
6. Evidence closure records what the target already proves. It does not
   complete a historical publication or create successor authority.
7. A successor rollback receives authority only from its new approval and its
   own `J`, `P`, `R`, `M`, and `V`.
8. No migration or compatibility operation backfills successor stages into a
   historical chain.

## Protocol families and versions

The compatibility boundary recognizes two disjoint namespaces:

| Family | Storage owner | Mutability | Authority |
| --- | --- | --- | --- |
| Historical private-file families | Preserved private files | Frozen and read-only | Historical semantics only |
| `hindsight-postgresql-publication/v1` | Protected target PostgreSQL schema | Append-only through protected interfaces | Successor publication and mutation |

Historical schema numbers remain scoped to the exact artifact kind and
historical family that originally defined them. The same integer under another
artifact kind or family is unrelated. The successor protocol therefore does
not adopt a name such as “legacy schema 3”, and a legacy reader never treats a
newer integer as a compatible extension.

The successor aggregate binding records both `protocol_family` and
`protocol_version`. A future successor version is admitted only through an
explicit reader and transition contract. Unknown future versions remain
opaque; an older implementation never selects the nearest known version or
assumes forward compatibility.

Reader implementations may receive bug fixes only when those fixes preserve
the accepted byte grammar and historical semantics. A change that accepts new
bytes, rejects previously valid bytes, changes canonicalization, changes a
timestamp meaning, or changes a digest requires a new reader-contract identity
and an explicit compatibility decision. It does not silently replace the
frozen reader used by an existing cutover manifest.

## Exact legacy inventory

Inventory begins with a safe, read-only open of each candidate artifact. The
implementation must reject symbolic-link substitution, descriptor identity
change, non-regular inputs where regular files are required, size change, or
any other inability to prove that the bytes hashed are the bytes decoded. It
computes `source_sha256` and byte length over the exact source bytes before
semantic parsing.

Each inventory entry binds:

- an opaque artifact locator and stable opened-file identity;
- exact byte length and `source_sha256`;
- artifact kind and exact historical schema version, when safely readable;
- the frozen reader-contract identity and digest;
- historical canonical-byte and embedded digest identities;
- every referenced plan, journal, receipt, preimage, or verification
  dependency and its exact identity;
- the canonical server-derived target surface, or an explicit result that the
  surface cannot be determined;
- the decoded legacy disposition;
- any closure-observation identity;
- the exact failure category and target-overlap evidence needed to decide
  whether a separately approved exclusion could later apply; and
- `authority=NONE`.

The locator is diagnostic, not identity. Renaming a byte-identical artifact
does not make it a different historical fact, but it changes the inventory and
therefore invalidates a previously prepared cutover manifest. Two locators
with the same exact bytes always remain two inventory observations; v1 has no
locator, content, artifact-kind, hard-link, or semantic deduplication rule.

The inventory is complete for the declared target surface and an exact,
approved discovery-root set. That set enumerates every preserved plan root
named by the operator-owned recovery record and every shared artifact path
reached through a frozen reader's dependency graph. The manifest binds the
root-set contract and each root identity; neither an implicit default nor a
best-effort directory walk establishes completeness. A discovered artifact
whose target surface is unknown is treated as overlapping until a frozen
reader determines otherwise. A separate exact exclusion may accept that
conservative overlap for one activation, but it does not determine the
artifact's target. Absence from a scan is never evidence that no historical
publication exists.

## Frozen reader dispatch

Dispatch uses an exact registry key:

```text
(
  protocol_family,
  artifact_kind_or_authenticated_dependency_role,
  artifact_schema_version,
  reference_plan_schema_version when the historical contract requires it,
  artifact_or_reference_plan_variant when one schema version has multiple
    accepted byte grammars,
  canonicalization_contract_id
)
```

Every component must match a registered reader exactly. Dispatch has no
numeric ranges, aliases, coercions, “compatible” default, nearest-version
selection, generic JSON fallback, or mapping from a legacy schema number to a
successor version.

`authenticated_dependency_role` is available only when an already selected
frozen reader declares that exact dependency edge and the historical grammar
forbids a `kind` field. It is not inferred from a path or arbitrary content.
A kindless discovery-root artifact cannot self-dispatch and therefore blocks
cutover as `KNOWN_UNDISPATCHABLE` unless a separate exact exclusion covers
its currently readable, stable, exact bytes.

### Closed historical reader registry

The accepted historical registry is pinned to reviewed source revision
[`7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab`](https://github.com/nisavid/agents/commit/7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab).
#75 assigns the source-external protocol-family identifier
`hindsight-private-file-operation-recovery/v1` and revision-pinned wire
canonicalization identifiers to these rows. None of these identifiers is
claimed to be a decoded legacy field.

The default
`hindsight-operation-recovery-canonical-json-lf-sha256/7b165b3` contract is
strict UTF-8 JSON with duplicate keys,
non-finite numbers, and unsafe integers rejected; object keys are ordered by
their UTF-16BE representation; values use the historical safe-number JSON
encoding; semantic digests are SHA-256 over canonical bytes; and persisted
JSON is those canonical bytes followed by exactly one LF. Raw source identity
remains a separate SHA-256 over the exact persisted bytes.

The registry digest is itself exact. Each fully expanded tuple is projected as
the normative `ReaderRegistryMember` defined below, including its source
revision, protocol family and version, exact kind or authenticated dependency
role, artifact and reference-plan schema, variant, wire-canonicalization
contract, and frozen reader contract. `reader_registry_digest` is SHA-256 over
the separator-free concatenation of those independently canonicalized members
in compatibility set order. The member's first five selector fields form its
stable identity. A range expression, reordered or omitted tuple, changed
contract, or equivalent implementation table has a different digest.

Exact-drain progress rows instead use
`hindsight-operation-recovery-progress-compact-ascii-json-no-lf/7b165b3`.
Their persisted bytes are UTF-8 output from the pinned compact JSON writer with
ASCII escaping, comma-and-colon separators, sorted keys, and no trailing LF.
Their embedded `progress_digest` still uses the historical semantic
canonical-JSON SHA-256 contract. The frozen reader binds both the progress wire
bytes and semantic digest; it never normalizes one representation into the
other before computing `raw_identity`.

Decrypted preimage envelopes use
`hindsight-operation-recovery-encrypted-preimage-canonical-json-no-lf/7b165b3`:
the exact canonical JSON plaintext is encrypted without a trailing LF. The
ciphertext has its own exact raw identity, and the plaintext reader validates
the post-decryption kind, schema, plan digest, row grammar, and canonical
plaintext identity.

In the following table, each comma-separated reference-plan value expands to
one registry tuple. The implementation descriptor must enumerate those tuples;
it must not retain a range expression. A tuple whose reference plan is schema
12 or 15 also binds the exact plan variant listed in the first row. `NONE`
means that the artifact has no reference-plan component.

| Artifact kind | Artifact schema | Reference exact-drain plan schemas | Exact variant or constraint |
| --- | --- | --- | --- |
| `operation-recovery-exact-drain-plan` | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17 | `NONE` | Schema 12 is separately `phase-repair-v8` or `phase-repair-v9`; schema 15 is separately `provider-capability` or `legacy-hatchery-capability`; every other schema has one grammar. |
| `operation-recovery-exact-drain-authorization-receipt` | 1 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 | Exact referenced-plan variant when applicable. |
| `operation-recovery-exact-drain-authorization-receipt` | 2 | 15 | Exact referenced-plan variant. |
| `operation-recovery-exact-drain-authorization-receipt` | 3 | 16, 17 | One grammar per referenced plan. |
| `operation-recovery-exact-drain-application-journal` | 1 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 | Exact referenced-plan variant when applicable. |
| `operation-recovery-exact-drain-application-journal` | 2 | 15, 16, 17 | Exact referenced-plan variant when applicable. |
| `operation-recovery-exact-drain-progress` | 1 | 1, 2, 3, 4, 5 | One grammar per referenced plan. |
| `operation-recovery-exact-drain-progress` | 2 | 6, 7 | One grammar per referenced plan. |
| `operation-recovery-exact-drain-progress` | 3 | 8, 9, 10 | One grammar per referenced plan. |
| `operation-recovery-exact-drain-progress` | 4 | 11 | One grammar. |
| `operation-recovery-exact-drain-progress` | 5 | 12, 13, 14 | Exact referenced-plan variant when applicable. |
| `operation-recovery-exact-drain-progress` | 6 | 15 | Exact referenced-plan variant. |
| `operation-recovery-exact-drain-progress` | 7 | 16, 17 | One grammar per referenced plan. |
| `operation-recovery-exact-drain-status` | 1 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 15 | Exact referenced-plan variant when applicable. |
| `operation-recovery-exact-drain-status` | 2 | 12, 13, 14, 16, 17 | Exact referenced-plan variant when applicable. |
| `operation-recovery-exact-drain-application-receipt` | 1 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 | Exact referenced-plan variant when applicable. |
| `operation-recovery-exact-drain-application-receipt` | 2 | 15, 16, 17 | Exact referenced-plan variant when applicable. |
| `operation-recovery-exact-drain-verification-receipt` | 1 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 | Exact referenced-plan variant when applicable. |
| `operation-recovery-exact-drain-verification-receipt` | 2 | 15, 16, 17 | Exact referenced-plan variant when applicable. |

Every `operation-recovery-exact-drain-progress` row in this table uses the
progress-specific wire canonicalization identifier. Every other JSON row uses
the default canonical-JSON-plus-LF identifier unless this registry explicitly
assigns another contract. Decrypted preimage envelopes described below use the
preimage-specific no-LF identifier.

Stopped-run reconciliation uses the counterintuitive mapping fixed by the
source: stopped artifact schema 1 binds stopped plan schema 1 and exact-drain
reference plan schema 16; stopped artifact schema 2 binds stopped plan schema
2 and exact-drain reference plan schema 15. Every kind listed below expands to
exactly these three registry members:

| Artifact schema | Reference exact-drain plan schema | Registry variant |
| --- | --- | --- |
| 1 | 16 | `NONE` |
| 2 | 15 | `provider-capability` |
| 2 | 15 | `legacy-hatchery-capability` |

- `operation-recovery-exact-drain-stopped-run-reconciliation-plan`;
- `operation-recovery-exact-drain-stopped-recovery-handoff`;
- `operation-recovery-stopped-run-reconciliation-authorization-receipt`;
- `operation-recovery-stopped-run-reconciliation-encrypted-rollback-bundle`;
- `operation-recovery-stopped-run-reconciliation-application-journal`;
- `operation-recovery-exact-drain-interrupted-attempts-receipt`;
- `operation-recovery-exact-drain-stopped-run-receipt`;
- `operation-recovery-stopped-run-reconciliation-application-receipt`;
- `operation-recovery-stopped-run-reconciliation-verification-receipt`;
- `operation-recovery-stopped-run-reconciliation-rollback-authorization-receipt`;
- `operation-recovery-stopped-run-reconciliation-rollback-journal`; and
- `operation-recovery-stopped-run-reconciliation-rollback-receipt`.

The CLI's transient encryption-helper input uses the shorter
`operation-recovery-stopped-run-encrypted-rollback-bundle` label, but the
helper returns only ciphertext fields. The protected constructor wraps those
fields in the durable reconciliation bundle kind listed above. The transient
label is therefore not a discovery-root reader tuple or an alias for the
durable record.

Exact-drain reference plan schema 17 requires the caller-named, kindful
`operation-recovery-exact-drain-stopped-recovery-handoff` dependency. Its
frozen reader exact-validates the embedded stopped plan, stopped outcome,
cleanup snapshot, prior retry recovery, and every downstream source inventory
and digest under those artifacts' own contracts. An omitted, unregistered,
unresolved, or substituted handoff blocks dispatch and cannot be inferred from
the surrounding schema-17 plan.

The embedded stopped-run postimage follows the same schema mapping but is a
dependency, not a discovery-root file. The stopped-run process-quiescence,
provider-quiescence, terminal-absence, backup-attestation, and embedded
datastore-identity inputs are exact schema-1 dependency contracts.
After authorized decryption, the
`operation-recovery-stopped-run-selected-row-preimage` is also an exact
schema-1 dependency contract bound to the stopped plan and selected rows. Its
ciphertext remains `OPAQUE_DEPENDENCY` bytes until the exact parent plan and
encrypted bundle authorize decryption and validate the digest; its decrypted
bytes use the preimage-specific no-LF contract.

Grant retirement also follows the stopped-plan mapping. The
`operation-recovery-exact-drain-grant-retirement-plan` is schema 1 when it
binds stopped plan schema 1 and exact-drain reference plan schema 16, and
schema 2 when it binds stopped plan schema 2 and exact-drain reference plan
schema 15. Its authorization receipt, complete-preimage archive,
revoked-ledger archive, retirement journal, and retirement receipt are each
schema 1 only, with these exact kinds:

- `operation-recovery-exact-drain-grant-retirement-authorization-receipt`;
- `operation-recovery-exact-drain-grant-complete-preimage-archive`;
- `operation-recovery-exact-drain-grant-revoked-ledger-archive`;
- `operation-recovery-exact-drain-grant-retirement-journal`; and
- `operation-recovery-exact-drain-grant-retirement-receipt`.

Each schema-1 child expands against both retirement-plan schemas. The
persisted `operation-recovery-exact-drain-grant-history-resolution`, schema 1,
has two exact variants: `retired-archive`, which binds the retirement plan and
complete artifact chain, and `current-fixed-slot`, which requires every
retirement field to be `null` and binds the currently installed ledger. Each
retirement child and resolution must validate its exact ledger, plan, and
predecessor chain. A matching kind and schema without that chain is not
readable historical evidence.

The complete source-authenticated grant lineage has these exact contracts:

- `operation-recovery-exact-drain-authorization-grant-plan`, schema 1,
  references exact-drain plan schema 12 variant `phase-repair-v9`, schema 13,
  schema 14, or schemas 16 and 17 under their exact current-grant constraints;
- `operation-recovery-exact-drain-authorization-grant`, schema 1, embeds the
  exact grant plan and its approval;
- `operation-recovery-exact-drain-authorization-grant-ledger`, schema 1,
  embeds that exact grant;
- `operation-recovery-exact-drain-authorization-grant-claim`, schema 1 for
  exact-drain plan schema 15 and schema 2 for exact-drain plan schemas 16 and
  17;
- `operation-recovery-exact-drain-authorization-grant-close`, schema 1; and
- the optional
  `operation-recovery-exact-drain-authorization-grant-revocation`, schema 1.

Grant, claim, close, and revocation values embedded in the ledger dispatch
only over their authenticated dependency roles and exact sequence and digest
chain. The containing ledger and plan contracts, not a bare embedded schema
number, establish those roles.

Claim release has three accepted immediate plan variants:

| Plan kind and schema | Immediate reference | Variant |
| --- | --- | --- |
| `operation-recovery-claim-release-plan`, schema 2 | kindless requeue plan schema 1 | `legacy-requeue` |
| `operation-recovery-exact-drain-claim-release-plan`, schema 3 | exact-drain plan schema 15 | `retry-claim` |
| `operation-recovery-exact-drain-claim-release-plan`, schema 4 | exact-drain plan schema 15 | `terminal-claim` |

Each row expands to a schema-1 tuple for every one of these exact artifact
kinds:

- `operation-recovery-claim-release-authorization-receipt`;
- `operation-recovery-claim-release-encrypted-rollback-bundle`;
- `operation-recovery-claim-release-application-journal`;
- `operation-recovery-claim-release-application-receipt`;
- `operation-recovery-claim-release-verification-receipt`;
- `operation-recovery-claim-release-rollback-journal`; and
- `operation-recovery-claim-release-rollback-receipt`.

The encrypted schema-1 `operation-recovery-claim-release-preimage` is not a
discovery-root file. Its ciphertext remains opaque until the exact parent plan
and bundle authorize decryption and validate its digest. Its decrypted bytes
use the preimage-specific no-LF contract. Claim-release
application and rollback paths each change kind from journal to receipt, so a
locator never selects the reader.

The three shared-lifecycle plan artifacts are themselves kindful registry
members, separately from their child receipts, journals, and bundles. The
following is the complete outer-plan to embedded exact-drain-plan dispatch
matrix from the pinned verifier. Every outer schema in a comma-separated cell
pairs with every embedded schema in that row, and every resulting pair is one
independently canonicalized `ReaderRegistryMember`; the list syntax is not a
range member or a runtime selector. `NONE` means the literal registry variant
`"NONE"`.

| Outer plan kind | Outer schemas | Embedded reference-plan schemas | Registry variant |
| --- | --- | --- | --- |
| `operation-recovery-exact-drain-post-abort-plan` | 1, 2, 3, 4, 5, 6, 7, 8, and 9 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 14, 16, and 17 | `NONE` |
| `operation-recovery-exact-drain-post-abort-plan` | 10 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 16, and 17 | `NONE` |
| `operation-recovery-exact-drain-post-abort-plan` | 11 | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 16, and 17 | `NONE` |
| `operation-recovery-exact-drain-post-abort-plan` | 11 | 12 | `phase-repair-v8` |
| `operation-recovery-exact-drain-post-abort-plan` | 11 | 12 | `phase-repair-v9` |
| `operation-recovery-exact-drain-post-abort-plan` | 11 | 15 | `provider-capability` |
| `operation-recovery-exact-drain-post-abort-plan` | 11 | 15 | `legacy-hatchery-capability` |
| `operation-recovery-exact-drain-post-abort-plan` | 12 | 11 | `NONE` |
| `operation-recovery-exact-drain-post-abort-plan` | 12 | 12 | `phase-repair-v8` |
| `operation-recovery-exact-drain-post-abort-plan` | 12 | 12 | `phase-repair-v9` |
| `operation-recovery-exact-drain-post-abort-plan` | 12 | 15 | `provider-capability` |
| `operation-recovery-exact-drain-post-abort-plan` | 12 | 15 | `legacy-hatchery-capability` |
| `operation-recovery-exact-drain-post-terminal-reconciliation-plan` | 13 | 12 | `phase-repair-v8` |
| `operation-recovery-exact-drain-post-terminal-reconciliation-plan` | 13 | 12 | `phase-repair-v9` |
| `operation-recovery-exact-drain-post-terminal-reconciliation-plan` | 13 | 15 | `provider-capability` |
| `operation-recovery-exact-drain-post-terminal-reconciliation-plan` | 13 | 15 | `legacy-hatchery-capability` |
| `operation-recovery-exact-drain-stopped-failed-reset-plan` | 14 | 16 | `NONE` |
| `operation-recovery-exact-drain-stopped-failed-reset-plan` | 14 | 15 | `provider-capability` |
| `operation-recovery-exact-drain-stopped-failed-reset-plan` | 14 | 15 | `legacy-hatchery-capability` |

The broad outer-schema-11 union and the legacy outer-schema allowance for
embedded schemas 14, 16, and 17 are literal accepted verifier behavior, not an
inference about ordinary emitted lineage. The registry preserves those exact
accepted byte grammars. It does not narrow them to historically observed
instances or assume that outer and embedded schema numbers match.

The shared requeue/post-abort lifecycle has schema-1 authorization receipt,
encrypted rollback bundle, application journal, application receipt, rollback
journal, and rollback receipt kinds:

- `operation-recovery-authorization-receipt`;
- `operation-recovery-encrypted-rollback-bundle`;
- `operation-recovery-application-journal`;
- `operation-recovery-application-receipt`;
- `operation-recovery-rollback-journal`; and
- `operation-recovery-rollback-receipt`.

Each shared kind expands against these exact immediate plan rows and exact
source-external registry variants:

| Immediate plan selector | Reference schema | Registry variant |
| --- | --- | --- |
| authenticated dependency role `requeue-plan`, kindless schema 1, exact action `requeue-operation-cohort` | 1 | `legacy-requeue` |
| `operation-recovery-exact-drain-post-abort-plan`, schemas 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, and 12 | the exact plan schema | `post-abort` |
| `operation-recovery-exact-drain-post-terminal-reconciliation-plan`, schema 13 | 13 | `post-terminal-reconciliation` |
| `operation-recovery-exact-drain-stopped-failed-reset-plan`, schema 14 | 14 | `stopped-failed-reset` |

The shared artifact's schema-1 row with reference schema 1 therefore has two
different registry members: `legacy-requeue` and `post-abort`. The exact
variant is part of the stable registry identity and is selected only after the
parent reader authenticates the immediate plan kind or kindless dependency
role. `NONE`, a plan-schema-only lookup, or an implementation-chosen label is
not accepted for these rows.

The base requeue row uses
`operation-recovery-verification-receipt`, schema 1. Every post-abort,
post-terminal, and stopped-failed-reset row instead uses
`operation-recovery-post-abort-verification-receipt`, schema 1. The frozen
post-abort plan reader retains the complete exact-drain reference-plan and
recovery-context variant graph from the pinned source revision; schema number
alone never selects a partial generic reader. The encrypted schema-1
`operation-recovery-selected-row-preimage` is an opaque dependency until its
exact parent plan and bundle authorize decryption and validate it; its
decrypted bytes use the preimage-specific no-LF contract.

The kindless requeue plan is admitted only over the authenticated
`requeue-plan` dependency edge with its closed schema-1 key set and exact
action. It is `KNOWN_UNDISPATCHABLE` when encountered as a discovery root or
through any other role. Shared application and rollback paths change kind from
journal to receipt and therefore also dispatch by authenticated bytes, never
by path.

The classifier-only pending row is
`operation-recovery-stopped-run-durable-start-pending`, schema 1,
reference-plan `NONE`, variant `two-field`. Raw encrypted preimages and backup
ciphertext have no independently dispatchable kind or schema. They are
`OPAQUE_DEPENDENCY` bytes whose identity is verified only through the exact
parent plan or encrypted-bundle contract; they are never passed to a generic
reader.

Several historical paths change artifact kind after a committed step. The
exact-drain application path changes from application journal to application
receipt, in addition to the claim-release and shared lifecycle paths described
above. Dispatch therefore uses authenticated artifact kind and exact bytes,
not the locator. Stopped-run application and rollback journal paths remain
dedicated journal paths.

The source has no global historical catalog. Registry closure does not imply
discovery completeness: the cutover manifest must still bind the exact
operator-approved discovery roots and the recursively reached dependencies
described above.

Each frozen reader contract fixes:

- the exact accepted top-level key set and value types;
- required and forbidden fields;
- canonical encoding and digest algorithms;
- newline and timestamp semantics;
- the historical meaning of pending and final artifacts;
- the exact dependency graph and permitted reference-plan versions;
- validation of embedded and sibling digests;
- the expected target and cohort bindings;
- the reader's typed output and failure categories; and
- the reader-contract identity and digest.

A reader recursively validates every referenced artifact with that artifact's
own exact frozen reader. A final journal that names an unavailable,
unrecognized, malformed, or digest-mismatched dependency is not partially
accepted.

The historical two-field pending marker has exactly `kind` and
`schema_version` and a dedicated `PENDING_UNAUTHENTICATED` classifier. Its
presence cannot be interpreted as final journal durability, proof durability,
receipt durability, mutation time, commit time, or successor deadline
evidence. It is never passed to a final-journal reader after a parse failure.

### Reader output

A successful reader returns a typed `LegacyEvidence` value with at least:

```text
raw_identity
historical_identity
reader_contract_identity
target_surface
legacy_action
legacy_disposition
dependency_identities
preimage_identity, when present
authority = NONE
permitted_actions
```

`raw_identity` binds the exact source bytes. `historical_identity` binds
the canonical bytes and historical digests defined by the frozen protocol.
Both are retained because byte identity and historical semantic identity answer
different audit questions.

`permitted_actions` is a closed set of evidence-only outcomes, such as
`PRESERVE`, `INSPECT`, `OBSERVE_CLOSURE`,
`PREPARE_SEPARATELY_APPROVED_ROLLBACK`. It never contains an
authority-bearing stage transition. The protected successor publication and
mutation interfaces do not accept `LegacyEvidence` as proof of `J`, `P`,
`R`, `M`, or `V`.

An unsuccessful reader returns an opaque inventory descriptor rather than a
partially populated `LegacyEvidence`. The descriptor retains the exact raw
identity when bytes were safely readable, the failure category, and the basis
for target overlap. It carries no permitted successor action.

## Historical disposition matrix

The complete decoded legacy chain and live target observation determine one
of these dispositions:

| Legacy condition | Disposition | Permitted treatment |
| --- | --- | --- |
| Complete authenticated apply chain with matching historical verification | `COMPLETE_APPLY` | Preserve and inspect. It may be selected by one cutover manifest as `LEGACY_COMPLETE_APPLY` for the narrow genesis-only rollback bridge. |
| Complete authenticated rollback chain with matching historical verification | `COMPLETE_ROLLBACK` | Preserve and inspect as terminal historical evidence. It cannot become a successor predecessor. |
| Historical pending marker only | `PENDING_UNAUTHENTICATED` | Freeze as nonauthorizing. A new successor plan and approval are required. |
| Authenticated journal exists and exact target mutation is absent | `FROZEN_TARGET_ABSENT` | Freeze as nonauthorizing. Never resume the historical mutation. |
| Target mutation is present but the historical receipt or verification chain is incomplete | `CLOSURE_CANDIDATE` | Perform evidence-only exact target comparison. Never call a legacy mutation routine merely to check. |
| Closure comparison proves the exact expected postimage | `CLOSURE_MATCH` | Append a nonauthorizing closure observation and preserve the historical chain. It cannot qualify for the rollback bridge. |
| Closure comparison proves a different postimage | `CLOSURE_MISMATCH` | Append sticky mismatch evidence and block ordinary cutover for the affected surface pending separately approved remediation. |
| Closure comparison cannot reach a conclusion below its case ceiling | `CLOSURE_UNABLE_TO_VERIFY` | Retain retryable evidence-only status. It creates no authority and does not satisfy cutover. |
| The last allowed comparison is unable to conclude or is durably abandoned | `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY` | Record terminal remediation-only exhaustion. It creates no authority, admits no further attempt, and does not satisfy cutover. |
| Exact encrypted preimage or required decryption and integrity evidence is missing | `ROLLBACK_UNAVAILABLE` | Report rollback unavailable. Never synthesize a preimage from target rows. |
| Kindful or authenticated-role dependency validated only as a member of an exact parent chain | `AUTHENTICATED_DEPENDENCY` | Preserve as a nonauthorizing chain member. It has no standalone finality or successor meaning. |
| Kindless ciphertext or other opaque bytes authenticated only by an exact parent dependency role | `OPAQUE_DEPENDENCY` | Preserve exact raw identity as a nonauthorizing chain member. Never self-dispatch or infer semantics from content. |
| Unknown future schema or artifact kind | `UNKNOWN_ARTIFACT` | Preserve as opaque and block cutover unless separately and exactly excluded. |
| Malformed, corrupt, digest-mismatched, or semantically dependency-incomplete artifact whose current exact bytes remain safely readable and stable | `INVALID_ARTIFACT` | Preserve available raw identity and block cutover unless that exact artifact is separately excluded. An exclusion does not cover a missing or unreadable dependency. |
| Known kindless artifact encountered at a discovery root or any unauthenticated dependency role | `KNOWN_UNDISPATCHABLE` | Preserve as opaque and block cutover unless its currently readable, stable, exact bytes are separately excluded. Never infer its role from its path or content. |
| Artifact changes while it is inventoried, decoded, approved, or revalidated | `DRIFTED_ARTIFACT` | Block cutover. Regenerate the inventory from a stable read; a prior or partial identity cannot be excluded. |
| Artifact cannot be safely opened or its current exact bytes cannot be established | `UNREADABLE_ARTIFACT` | Block cutover. Historical locator, remembered digest, or backup attestation cannot substitute for current exact identity. |

No disposition converts a historical prefix into a successor prefix. A
historical state that is unsafe to continue remains unsafe even if its plan or
approval would not yet have expired under a reconstructed clock.

The closed exclusion-eligible failure set is exactly `UNKNOWN_ARTIFACT`,
`INVALID_ARTIFACT`, and `KNOWN_UNDISPATCHABLE`. Eligibility additionally
requires safely readable, stable, current exact bytes. An indeterminate target
surface does not add another failure category: an otherwise eligible entry may
be excluded only by binding explicit conservative overlap with the manifest's
target surface. `DRIFTED_ARTIFACT`, `UNREADABLE_ARTIFACT`,
`ROLLBACK_UNAVAILABLE`, every closure disposition, and every absent,
unreadable, or unresolved dependency are not exclusion-eligible. An exclusion
for a parent artifact never covers a separate dependency or another inventory
entry.

## Nonauthorizing closure observations

A closure observation answers only whether an already-mutated target exactly
matches the postimage required by an incomplete historical publication. It is
metadata-only evidence stored append-only in protected PostgreSQL. It is not a
general byte capsule and does not copy raw historical journals into the
database.

Closure uses one aggregate-level case with stable key:

```text
(
  target_surface,
  source_inventory_observation_id,
  source_chain_root_digest,
  observed_generation
)
```

The source observation identity is the deterministic locator-and-raw-state
identity defined below. It distinguishes byte-identical chains discovered at
different locators while remaining stable for an exact rescan of the same
source instance. The case binds that exact source, expected postimage, target
and cohort identities, one exact current deployment attestation, a finite case
expiry no later than that attestation's expiry, and an initially empty
terminal-outcome slot. It also binds one server-selected positive
`maximum_observation_attempts` no greater than the hard ceiling in the
deployment attestation, plus the attested per-attempt lock and statement
timeouts, whole-transaction timeout, idle-in-transaction termination timeout,
adapter-enforced connection lifetime, maximum reservation duration, and
post-reservation-expiry resolution duration. Every ceiling is positive; zero
cannot mean disabled or unbounded. The stable case key permits only one such
binding for the source, surface, and observed generation. Each
immutable observation instead has key:

```text
(closure_case_digest, attempt_ordinal)
unique (closure_case_digest, observation_request_id)
```

`attempt_ordinal` is the next positive integer allocated by a protected
reservation transaction while it locks the case; callers cannot choose or skip
an ordinal. Callers likewise cannot supply, extend, or renew a reservation
deadline. While holding the case and qualified-clock rows, the server samples
the exact case-bound deployment-attestation row, proves that it is current,
unrevoked, unexpired, and unchanged in target, adapter, and required guard
capabilities, and then samples the qualified monotonic clock. It derives the
deadline as the earliest of the sample plus the attested maximum reservation
duration, the remaining server-owned call bound, the clock envelope's attested
monotonic validity limit, and a conservative bound early enough to leave the
complete maximum resolution duration before both the case and
deployment-attestation expiries and before the bound clock envelope's
monotonic validity limit. A sample at or after any bound, insufficient
remaining resolution margin, an unbounded or arbitrarily distant caller value,
or any inability to derive a conservative deadline rejects the reservation
without consuming an ordinal. Equality is expired. The caller does
supply one stable, opaque
`observation_request_id` known before the call. The reservation binds that
request identity, the complete attempt input, qualified-clock identity,
server-derived monotonic deadline, observation identity derived from the case
digest, request identity, and ordinal, and a server-owned observer-lease
generation initially without an owner, then commits with
`synchronous_commit=on` before target observation begins. Allocation therefore
consumes one attempt even if the response is lost or the observer later crashes
or times out. Repeating the same request identity and binding returns its exact
result, or its reservation state subject to the exclusive observer claim below;
using that identity with another binding is `CONFLICT`.

Every reservation, claim, observation, finalization, pre-expiry takeover, and
post-reservation-expiry resolution transaction locks and revalidates the exact
case-bound deployment attestation as current, unrevoked, unexpired, and
unchanged in target, adapter, and guard capabilities. It sets its database lock
and statement timeouts to the lesser of the attested per-attempt ceilings and
the conservatively measured time remaining before the applicable reservation,
case, deployment-attestation, and clock-envelope limits. The dedicated closure adapter
also enforces that remainder as a hard whole-transaction and connection bound:
it uses an attested server-supported transaction timeout, an
idle-in-transaction termination timeout, and an independent adapter deadline
that forcibly closes the exact tagged database session if either server guard
fails to end it. A deployment that cannot prove all three guards cannot enable
closure observation. No transaction may begin when the remainder is
nonpositive or continue as a valid finalizer after it expires; timeout or forced
session closure rolls back its locks and returns control to protected resolution
under the same immutable reservation.

Post-reservation-expiry abandonment resolution is a separate bounded
transaction and does
not inherit a nonpositive old-reservation remainder. Before it starts, the
adapter forcibly closes the exact tagged observer session and proves either a
fresh qualified sample at or after the old deadline, or an authenticated
invalidation event for an observer incarnation that cannot continue while the
same qualified clock envelope remains valid. In either mode it takes a fresh
qualified sample under that envelope to derive a new positive
server-owned resolution deadline capped by the case's attested
`maximum_resolution_duration_ms`, case expiry, exact deployment-attestation
expiry, and clock-envelope validity, and enforces the same lock, statement,
whole-transaction, idle-in-transaction, and connection-termination guards
against that new bound. It can only fence the old lease generation and append a
nonauthorizing abandonment result; it cannot observe the target or finalize a
successful result. If one bounded resolution attempt times out, the tagged
session is closed and a later protected resolver may exact-retry the same
abandonment transition; it never revives the old observer.

Expiry, revocation, replacement, target drift, adapter drift, or
guard-capability drift of the case-bound deployment attestation, and reboot,
suspend uncertainty, clock-envelope loss, rollback, drift, or excessive error
in the case-bound qualified clock, reject every
new reservation, claim, observation, finalization, takeover, and resolution.
The case is then a protected remediation blocker: it cannot be treated as a
closure-derived disposition, replaced under the occupied key, or given a
synthetic terminal observation. An open ordinal may remain unresolved after
such invalidation, but it creates no authority and no manifest can admit it. A
separately accepted future remediation design is required to retire or migrate
that stale case; v1 does not infer continuing adapter capability from an
expired attestation.

At most one reservation per case may lack a durable result. A partial unique
constraint and the case lock enforce that rule. While one request is open, a
different request returns `OBSERVATION_IN_PROGRESS` with the existing request
and ordinal identities and creates nothing. The next ordinal can be reserved
only after the prior one has a durable unable or abandoned result and the case
has no conclusive or exhausted terminal outcome. Completion order therefore
cannot let an exhausted later ordinal overtake an earlier conclusive result.

Before observing the target, a protected claim transaction locks the case and
reservation. If no live observer owns it, the transaction advances the
server-owned observer-lease generation, records an unguessable fencing token,
adapter-incarnation identity, and qualified monotonic lease deadline, and
commits before returning the claim. The lease deadline is strictly capped at
the reservation deadline. While that lease is live, every other
execution—including the same request identity and binding—returns
`OBSERVATION_IN_PROGRESS` and cannot read or finalize the attempt. Finalization
locks the same rows and requires the exact current generation, token,
incarnation, and a qualified monotonic sample strictly before both the lease
and reservation deadlines. Equality is expired. A stale or late observer
therefore cannot commit any result.

Takeover is possible only when a protected claim transaction proves the prior
lease expired or its observer incarnation cannot continue, atomically advances
the generation to fence that observer, and installs the replacement claim
under the same locks. The replacement exact-resumes the same immutable
reservation and ordinal; it never allocates another attempt. A resolver may
record abandonment only after it atomically fences the prior generation, so a
late conclusive observation cannot race an irreversible unable or exhausted
result.

Its immutable binding includes:

- exact source artifact raw hashes and historical digests;
- frozen reader-registry and reader-contract digests;
- the complete reference-chain root;
- target database identity and canonical target surface;
- observed target generation and selected and preserved cohort digests;
- expected canonical postimage digest;
- an exact result union: `EXACT_MATCH` or `MISMATCH` requires the complete
  observed canonical postimage digest and comparison evidence, while
  `UNABLE_TO_VERIFY` or `ABANDONED_UNABLE_TO_VERIFY` requires
  `observed_postimage=NONE` plus an exact failure category and evidence digest;
- the result, caller-known request identity, server-derived attempt ordinal and
  observation identity, and observation time; and
- `authority=NONE` with its exact permitted evidence action.

The case binding and each observation use the exact
`hindsight-compatibility-closure-case-binding/v1` and
`hindsight-compatibility-closure-observation/v1` bodies defined below under the
same compatibility canonicalization contract. The protected transaction stores
their complete canonical bytes and recomputed digests, not only parsed columns.
Every `InventoryObservation.closure_observation` is `"NONE"` unless the
classification is closure-derived; otherwise it must be an `EvidenceRef` with
exact contract kind `hindsight-compatibility-closure-observation`, version `1`,
and body digest equal to the protected attempt row. A generic evidence kind,
unknown version, or unattached matching bytes are invalid.

At case creation, the protected interface holds the exact source descriptors
and frozen reader-registry descriptor, reconstructs the complete authenticated
dependency closure, and independently canonicalizes its raw-identity,
historical-identity, and reader-contract projections. It recomputes and stores
all three set digests, the exact `reader_registry_digest`, and the frozen
reader's `source_chain_root_digest` in the immutable case body. While locking
the exact current deployment attestation and qualified clock envelope, it also
derives—not accepts from the caller—the case expiry as the earlier of the
attestation expiry and the attested server-owned case-lifetime bound. Before
occupying the stable key, a qualified sample and conservative error model must
prove enough remaining time for at least one complete maximum reservation
duration, the complete maximum resolution duration, every finite transaction
and connection guard, and the required clock-error margin. Case creation fails
on an unreadable member, registry drift, projection mismatch, a caller-chosen
or unusably short expiry, insufficient headroom, or any digest supplied rather
than derived by that interface.

After durable reservation, the closure interface reads and locks the target
generation and selected rows only long enough to make one exact observation
and finalize that reservation. It never invokes a legacy idempotent apply or
rollback routine: a legacy routine may mutate the target when it does not find
the expected postimage, so it is not an evidence-only check.

Every conclusive observation transaction locks the case and target generation.
The first `EXACT_MATCH` or `MISMATCH` fills the terminal slot; database
constraints prevent both outcomes for one case. `MISMATCH` is therefore
immutable and sticky, while an exact replay returns the existing terminal
outcome. `UNABLE_TO_VERIFY` appends an attempt and leaves the slot empty, so a
later attempt may receive the next ordinal while the count remains below the
bound. An `UNABLE_TO_VERIFY` at the maximum fills the terminal slot with
`CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY`; later calls exact-replay that
nonauthorizing terminal state. Every attempt is bounded by the attested
transaction timeouts.

Crash recovery exact-queries the caller-known request identity and resolves
any ambiguous reservation, observer claim, or observation transaction before
acting. A reservation with no durable result may exact-resume only the same
binding through the exclusive claim protocol before its monotonic deadline.
At the deadline, or after proof that the claimed observer incarnation cannot
continue while the same qualified clock remains valid, the protected resolver
uses the separate bounded resolution protocol,
locks the case and reservation after the tagged observer session is gone,
advances the observer-lease generation to fence every prior token, and only then
finalizes it as `ABANDONED_UNABLE_TO_VERIFY` with
`observed_postimage=NONE`. If that
ordinal reaches the ceiling, the case becomes
`CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY`; otherwise the next protected reservation
may allocate the next ordinal. Reusing a request identity or ordinal with a
different binding is `CONFLICT`; no observation is converted in place or given
a newly chosen outcome after lost acknowledgement.

A closure observation may be named in a cutover manifest and displayed in
status. Before a closure-derived disposition can enter a manifest or survive
activation, the verifier exact-queries the protected case and attempt rows,
recanonicalizes both bodies, recomputes both digests, proves the observation's
case link and result tagged union, and matches the case's source chain, target,
generation, cohorts, expected postimage, attempt ceiling, and terminal slot to
the inventory and live target. The observation result and
`case_outcome_after` must map exactly to the claimed closure disposition.
Protected `J`, `P`, `R`, and `M` functions have no foreign-key,
predicate, or adapter path that can consume it as authority. It never rewrites
the legacy receipt, creates a successor stage, aliases historical completion
to `V`, or admits the legacy rollback bridge.

## Authenticated cutover manifest

Cutover uses one immutable manifest per canonical server-derived target
surface and successor activation. A multi-target operation therefore has
multiple independently authenticated manifests; one target's clean inventory
cannot mask another target's blocker.

The initial v1 profile requires every legacy writer fence to be mechanically
partitioned to exactly one canonical target surface. The manifest names each
service and database role and binds the server-derived proof that neither can
write another surface. A writer or role spanning two surfaces blocks both
per-target activations; v1 has no activation group and never partially
activates surfaces under one shared fence. Group activation requires a
separately accepted protocol.

The manifest-basis, final-manifest, exclusion, approval, closure,
realized-fence-evidence, origin-fence binding, and active-fence adoption bodies
all use
`hindsight-postgresql-compatibility-canonical-json/v1`. Its exact bytes are
strict UTF-8 JSON with duplicate keys, non-finite numbers, and unsafe integers
rejected. Nonnegative safe integers use their shortest ASCII decimal form,
with `0` as the sole zero spelling. Arrays retain their contract-defined order;
objects order keys by unsigned lexicographic comparison of the keys' UTF-16BE
code-unit bytes. Arrays and objects use only `,` and `:` delimiters with no
whitespace. String serialization emits `\"` and `\\` for quote and reverse
solidus; emits `\b`, `\t`, `\n`, `\f`, and `\r` for U+0008, U+0009, U+000A,
U+000C, and U+000D; emits every other U+0000 through U+001F scalar as lowercase
`\u00xx`; and emits every other Unicode scalar, including `/`, U+2028, U+2029,
and non-ASCII text, literally as its shortest UTF-8 sequence. It never emits
`\/`, an optional `\u` escape, uppercase hex, or Unicode normalization. The
complete JSON value has exactly one trailing LF. Each body carries its own kind
and schema version, and every
digest in this section is SHA-256 over those complete exact bytes, including
the LF. A parsed JSON value, implementation-native serialization, or digest
over bytes from another canonicalization contract is not interchangeable.

The exact v1 body identities are closed:

| Body | Exact `kind` | Exact `schema_version` |
| --- | --- | --- |
| Manifest basis | `hindsight-compatibility-manifest-basis` | `1` |
| Final manifest | `hindsight-compatibility-final-manifest` | `1` |
| Artifact exclusion | `hindsight-compatibility-artifact-exclusion` | `1` |
| Approval | `hindsight-compatibility-approval` | `1` |
| Closure case binding | `hindsight-compatibility-closure-case-binding` | `1` |
| Closure observation | `hindsight-compatibility-closure-observation` | `1` |
| Realized admission evidence | `hindsight-compatibility-realized-admission-evidence` | `1` |
| Realized ACL evidence | `hindsight-compatibility-realized-acl-evidence` | `1` |
| Zero-live-writer evidence | `hindsight-compatibility-zero-live-writer-evidence` | `1` |
| Service-disable evidence | `hindsight-compatibility-service-disable-evidence` | `1` |
| Origin fence manifest binding | `hindsight-compatibility-origin-fence-manifest-binding` | `1` |
| Active fence manifest adoption | `hindsight-compatibility-active-fence-manifest-adoption` | `1` |

An approval subject kind is exactly
`hindsight-compatibility-final-manifest` or
`hindsight-compatibility-artifact-exclusion`; a manifest-basis body is never
approved directly, and closure bodies are never approval subjects. No alias,
combined `kind/version` string, unknown schema, or fallback dispatch is
accepted.

Set-valued collections in these bodies use the v1 compatibility ordering
contract: encode every complete member independently under the same canonical
JSON rules, including its trailing LF, then sort members by unsigned
lexicographic comparison of those exact bytes. Discovery roots, inventory
observations, dependency edges, and exclusion bindings are all set-valued. An
exclusion binding contains only its canonical exclusion body,
`exclusion_body_digest`, one scalar canonical approval body, and
`approval_digest`; the manifest approval is likewise one scalar approval record
in the outer envelope. Neither approval body is independently set-valued.
The external authenticated channel receipt is verified from the envelope but
is not a member of any hashed or ordered body. An exact duplicate member
appears once. Members with the same
declared stable identity but different canonical bytes are `CONFLICT`, not two
entries. Final dispositions appear in the exact inventory-observation order
and name that observation's identity; exclusion applicability names both its
inventory-observation and exclusion identities. A semantically ordered
sequence must carry an explicit ordinal and sorts first by the canonical
nonnegative ordinal's ascending integer value, then by canonical member bytes;
duplicate integer ordinals conflict. No other array ordering is admitted in
v1.

### Normative v1 value grammar

The following grammar closes the JSON value before canonicalization. Every
named object has exactly the listed keys, every key is required, and unknown
keys or `null` are invalid. `schema_version` is the JSON integer `1`, not a
string. `NONE` is always the JSON string `"NONE"`, never an absent key, empty
object, empty string, zero, or `null`.

In the grammar, `{...}` denotes a JSON object, `set<T>` and `sequence<T>`
denote JSON arrays ordered by the contracts above, and `|` denotes an exact
value union. These marks are schema notation and never appear in encoded JSON.

The scalar types are:

- `SafeInteger`: a JSON integer from 0 through `9007199254740991`;
- `PositiveSafeInteger`: a JSON integer from 1 through `9007199254740991`;
- `UnixSecond`: a `SafeInteger` counting whole seconds since the Unix epoch;
- `Digest`: exactly 64 lowercase hexadecimal characters naming SHA-256;
- `GitObjectId`: exactly 40 lowercase hexadecimal characters naming a Git
  SHA-1 object in this v1 registry;
- `Id`: a lowercase canonical UUID string;
- `DecimalString`: `0` or a nonzero ASCII decimal digit followed by zero or
  more ASCII decimal digits;
- `Token`: a nonempty ASCII string matching
  `[A-Za-z0-9][A-Za-z0-9._:-]{0,255}`;
- `ContractId`: a nonempty ASCII string matching
  `[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}`;
- `Text`: a JSON string of Unicode scalar values with no NUL or unpaired
  surrogate; v1 performs no Unicode normalization, so distinct scalar
  sequences remain distinct values; and
- `Base64Url`: unpadded RFC 4648 base64url text using only ASCII letters,
  digits, `-`, and `_`.

Every `issued_at_unix_s`, `created_at_unix_s`, `observed_at_unix_s`,
`freshness_deadline_unix_s`, and `expires_at_unix_s` uses `UnixSecond`.
Issue and creation times must be strictly below their body's expiry, while all
admission comparisons remain strict as specified below.

Reusable exact objects are:

```text
EvidenceRef := {
  "contract_kind": Token,
  "contract_version": SafeInteger,
  "body_digest": Digest
}

TargetDatabaseIdentity := {
  "postgres_system_identifier": DecimalString,
  "database_oid": SafeInteger,
  "database_name": Text
}

RelationIdentity := {
  "relation_oid": SafeInteger,
  "schema_name": Text,
  "relation_name": Text,
  "relkind": Token
}

TargetSurface := {
  "relations": set<RelationIdentity>,
  "target_surface_digest": Digest
}

LocatorObservation := {
  "discovery_root_id": Id,
  "relative_locator": Text,
  "descriptor": EvidenceRef
}

DiscoveryRoot := {
  "discovery_root_id": Id,
  "root_locator": Text,
  "derivation_contract": EvidenceRef
}

InventoryObservationIdentity := {
  "root_locator": Text,
  "root_derivation_contract": EvidenceRef,
  "relative_locator": Text,
  "byte_state": "READABLE_STABLE" | "DRIFTED" | "UNREADABLE",
  "byte_length": SafeInteger | "NONE",
  "source_sha256": Digest | "NONE"
}

DigestBinding := {
  "name": Token,
  "digest": Digest
}

ReaderRegistryMember := {
  "artifact_kind": Token | "NONE",
  "authenticated_dependency_role": Token | "NONE",
  "artifact_schema_version": PositiveSafeInteger,
  "reference_plan_schema_version": PositiveSafeInteger | "NONE",
  "artifact_or_reference_plan_variant": Token | "NONE",
  "protocol_family": "hindsight-private-file-operation-recovery",
  "protocol_version": 1,
  "wire_canonicalization_contract": ContractId,
  "reader_contract": EvidenceRef,
  "source_revision": GitObjectId
}

DependencyEdge := {
  "dependency_edge_id": Id,
  "source_inventory_observation_id": Id,
  "dependency_role": Token,
  "dependency_locator": LocatorObservation,
  "state": "RESOLVED" | "MISSING" | "UNREADABLE" | "UNRESOLVED",
  "dependency_inventory_observation_id": Id | "NONE"
}
```

`RelationIdentity`, `DiscoveryRoot`, `DigestBinding`, and `DependencyEdge`
sets have stable identities `relation_oid`, `discovery_root_id`, `name`, and
`dependency_edge_id`, respectively. `dependency_inventory_observation_id` is
an `Id` exactly when `state=RESOLVED`; otherwise it is `"NONE"`.
Each `ReaderRegistryMember` has exactly one selector identity: either
`artifact_kind` is a token and `authenticated_dependency_role="NONE"`, or
`artifact_kind="NONE"` and `authenticated_dependency_role` is a token. A
kindful authenticated dependency is represented by its artifact-kind selector;
the dependency role remains part of the inventory and projection evidence, not
a second registry selector. `artifact_or_reference_plan_variant` is the exact
closed-registry token when either the artifact schema or its immediate
reference-plan schema has multiple accepted grammars; it is `"NONE"` only when
that complete row assigns no variant. A `"NONE"` reference-plan schema can
therefore coexist with an artifact-owned variant such as `phase-repair-v8`,
`provider-capability`, or `two-field`. A populated reference-plan schema uses
the exact assigned reference-plan variant, including the shared lifecycle
variants above. A row that would require two independent variants but has no
single exact combined token in this closed registry is unrepresentable and
must block rather than dropping either selector. Every v1
member binds the exact 40-hex reviewed source object named by the closed
registry; a SHA-256 digest, abbreviated object name, or transformed snapshot
digest cannot substitute for that object identity. Its
`wire_canonicalization_contract` is the complete exact slash-bearing identifier
assigned above, including the `7b165b3` suffix; an `EvidenceRef`, split
kind/version projection, alias, or normalized spelling cannot substitute for
that scalar.
`target_surface_digest` is SHA-256 over the separator-free concatenation of the
independently canonicalized `RelationIdentity` member bytes in set order; the
digest field itself is not an input.

An inventory member has exact shape:

```text
InventoryObservation := {
  "inventory_observation_id": Id,
  "locator": LocatorObservation,
  "byte_state": "READABLE_STABLE" | "DRIFTED" | "UNREADABLE",
  "byte_length": SafeInteger | "NONE",
  "source_sha256": Digest | "NONE",
  "artifact_kind": Token | "NONE",
  "authenticated_dependency_role": Token | "NONE",
  "artifact_schema_version": SafeInteger | "NONE",
  "historical_digests": set<DigestBinding>,
  "dispatch_state": "DECODED" | "DISPATCH_FAILED" |
                    "INVALID_BEFORE_DISPATCH" | "UNRECOGNIZED" |
                    "NOT_APPLICABLE",
  "reader_contract": EvidenceRef | "NONE",
  "reader_output": EvidenceRef | "NONE",
  "classification": HistoricalClassification,
  "failure_evidence": EvidenceRef | "NONE",
  "dependency_state": "COMPLETE" | "INCOMPLETE" | "NOT_APPLICABLE",
  "dependency_root_digest": Digest | "NONE",
  "closure_observation": EvidenceRef | "NONE",
  "target_overlap": "EXACT_TARGET" | "DISJOINT" | "INDETERMINATE"
}
```

`HistoricalClassification` is exactly one of `COMPLETE_APPLY`,
`COMPLETE_ROLLBACK`, `PENDING_UNAUTHENTICATED`, `FROZEN_TARGET_ABSENT`,
`CLOSURE_CANDIDATE`, `CLOSURE_MATCH`, `CLOSURE_MISMATCH`,
`CLOSURE_UNABLE_TO_VERIFY`, `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY`,
`ROLLBACK_UNAVAILABLE`, `AUTHENTICATED_DEPENDENCY`, `OPAQUE_DEPENDENCY`,
`UNKNOWN_ARTIFACT`, `INVALID_ARTIFACT`, `KNOWN_UNDISPATCHABLE`,
`DRIFTED_ARTIFACT`, or `UNREADABLE_ARTIFACT`; no implementation extension is
accepted. `InventoryObservation` has stable identity
`inventory_observation_id`. That identity is deterministic across scans. The
implementation resolves `locator.discovery_root_id` to exactly one
`DiscoveryRoot`, constructs `InventoryObservationIdentity` from that root's
locator and derivation contract plus the observation's relative locator and raw
byte state, and canonicalizes the identity object including its LF. It hashes
those bytes with SHA-256, then computes lowercase RFC 4122 UUIDv5 in the
standard URL namespace `6ba7b811-9dad-11d1-80b4-00c04fd430c8` using the exact
ASCII name
`https://github.com/nisavid/agents/tooling/hindsight/inventory-observation/v1#sha256=`
followed by the 64 lowercase digest characters. The resulting UUID is the only
valid `inventory_observation_id`; callers never choose it. Locator descriptors,
scan time, classification, and reader output are deliberately outside this
identity, so an exact rescan of the same root, locator, and byte state retains
the identity while its newly authenticated observation body remains distinct.
A changed root locator or derivation contract, relative locator, byte state,
byte length, or raw digest produces another identity. The raw fields are both
populated for `READABLE_STABLE` and `DRIFTED` and both `"NONE"` for
`UNREADABLE`, matching the closed union below.

The following classification table is a closed tagged-union constraint.
`R` means a non-`NONE` value is required; `N` means the literal `"NONE"` is
required. For `historical_digests`, `A` means the exact set independently
authenticated by the frozen reader output—including an empty set only when
that output authenticates it—and `E` means the empty set. For target overlap,
`T` means the exact value authenticated by the frozen reader output, `F` means
the exact value authenticated by the selected frozen reader's failure evidence,
and `I` means literal `INDETERMINATE`. `selector` is the triple `{artifact_kind,
authenticated_dependency_role, artifact_schema_version}`. `K` requires
`{Token, "NONE", SafeInteger}`; `D` requires an authenticated dependency role,
a schema version, and either a recognized artifact kind or `"NONE"`, namely
`{Token or "NONE", Token, SafeInteger}`; `O` requires
`{"NONE", Token, "NONE"}`; `N` requires all three to be `"NONE"`.

| Classification | Byte state / raw identity | Dispatch state | Selector | Reader contract / output | Historical digests | Failure evidence | Dependency state / root | Closure observation | Target overlap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `COMPLETE_APPLY`, `COMPLETE_ROLLBACK`, `PENDING_UNAUTHENTICATED`, `FROZEN_TARGET_ABSENT`, `CLOSURE_CANDIDATE` | `READABLE_STABLE` / R | `DECODED` | K | R / R | A | N | `COMPLETE` / R | N | T |
| `CLOSURE_MATCH`, `CLOSURE_MISMATCH`, `CLOSURE_UNABLE_TO_VERIFY`, `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY` | `READABLE_STABLE` / R | `DECODED` | K | R / R | A | N | `COMPLETE` / R | R | T |
| `ROLLBACK_UNAVAILABLE` | `READABLE_STABLE` / R | `DECODED` | K | R / R | A | N | `INCOMPLETE` / R | N | T |
| `AUTHENTICATED_DEPENDENCY` | `READABLE_STABLE` / R | `DECODED` | D | R / R | A | N | `COMPLETE` / R | N | T |
| `OPAQUE_DEPENDENCY` | `READABLE_STABLE` / R | `NOT_APPLICABLE` | O | N / N | E | N | `NOT_APPLICABLE` / N | N | I |
| `UNKNOWN_ARTIFACT` | `READABLE_STABLE` / R | `UNRECOGNIZED` | K | N / N | E | R | `NOT_APPLICABLE` / N | N | I |
| `INVALID_ARTIFACT` before dispatch | `READABLE_STABLE` / R | `INVALID_BEFORE_DISPATCH` | N | N / N | E | R | `NOT_APPLICABLE` / N | N | I |
| `INVALID_ARTIFACT` after dispatch | `READABLE_STABLE` / R | `DISPATCH_FAILED` | K or D | R / N | E | R | `COMPLETE` or `INCOMPLETE` / R | N | F |
| `KNOWN_UNDISPATCHABLE` | `READABLE_STABLE` / R | `UNRECOGNIZED` | kind and role `"NONE"`; schema `SafeInteger` or `"NONE"` | N / N | E | R | `NOT_APPLICABLE` / N | N | I |
| `DRIFTED_ARTIFACT` | `DRIFTED` / R | `NOT_APPLICABLE` | N | N / N | E | R | `NOT_APPLICABLE` / N | N | I |
| `UNREADABLE_ARTIFACT` | `UNREADABLE` / N | `NOT_APPLICABLE` | N | N / N | E | R | `NOT_APPLICABLE` / N | N | I |

Raw identity means both numeric `byte_length` and `source_sha256`; N means both
are `"NONE"`. A drifted entry's raw identity describes its last complete stable
read and `failure_evidence` proves the later drift, so it cannot support an
exclusion. Every decoded dependency root covers its complete observed edge set;
the `INCOMPLETE` root also includes the exact missing, unreadable, or unresolved
edge state. No field combination outside this table is a valid v1 body.
For an after-dispatch `INVALID_ARTIFACT`, `COMPLETE` means every dependency edge
resolved but the selected reader rejected the artifact's authenticated content;
`INCOMPLETE` means the exact dependency-root preimage records at least one
missing, unreadable, or unresolved edge. Neither state produces reader output
or historical digests. Only the selected frozen reader's authenticated failure
evidence may give an after-dispatch failure determinate target overlap; every
failure path without reader output or such failure evidence is indeterminate.

The remaining shared compatibility objects are:

```text
ServiceIdentity := {
  "service_id": Id,
  "adapter_kind": Token,
  "service_locator": Text,
  "target_surface_digest": Digest
}

RoleIdentity := {
  "role_id": Id,
  "postgres_role_oid": SafeInteger,
  "role_name": Text,
  "target_surface_digest": Digest
}

QuiescencePredicate := {
  "predicate_id": Id,
  "predicate_kind": "LOGIN_ADMISSION_DENIED" |
                    "CONNECTION_ADMISSION_DENIED" |
                    "TARGET_WRITE_ACL_DENIED" |
                    "ZERO_LIVE_WRITER" |
                    "SERVICE_DISABLED",
  "subject_identity_digest": Digest,
  "derivation_contract": EvidenceRef,
  "expected_value_digest": Digest
}

WriterFenceProposal := {
  "fence_proposal_id": Id,
  "services": set<ServiceIdentity>,
  "database_roles": set<RoleIdentity>,
  "target_partition_proof": EvidenceRef,
  "quiescence_predicates": set<QuiescencePredicate>,
  "realization_policy": EvidenceRef
}

EpochActivationProposal := {
  "continuity_session_id": Id,
  "publication_epoch": SafeInteger,
  "deployment_attestation": EvidenceRef,
  "incarnation_capability_digest": Digest
}

FencePredicateObservation := {
  "predicate_id": Id,
  "predicate_kind": "LOGIN_ADMISSION_DENIED" |
                    "CONNECTION_ADMISSION_DENIED" |
                    "TARGET_WRITE_ACL_DENIED" |
                    "ZERO_LIVE_WRITER" |
                    "SERVICE_DISABLED",
  "subject_identity_digest": Digest,
  "derivation_contract": EvidenceRef,
  "expected_value_digest": Digest,
  "observed_value_digest": Digest,
  "observed_at_unix_s": UnixSecond
}

RealizedAdmissionEvidence := {
  "kind": "hindsight-compatibility-realized-admission-evidence",
  "schema_version": 1,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest,
  "fence_generation": PositiveSafeInteger,
  "writer_fence_proposal_digest": Digest,
  "realization_policy": EvidenceRef,
  "observations": set<FencePredicateObservation>,
  "authority": "NONE"
}

RealizedAclEvidence := {
  "kind": "hindsight-compatibility-realized-acl-evidence",
  "schema_version": 1,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest,
  "fence_generation": PositiveSafeInteger,
  "writer_fence_proposal_digest": Digest,
  "realization_policy": EvidenceRef,
  "observations": set<FencePredicateObservation>,
  "authority": "NONE"
}

ZeroLiveWriterEvidence := {
  "kind": "hindsight-compatibility-zero-live-writer-evidence",
  "schema_version": 1,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest,
  "fence_generation": PositiveSafeInteger,
  "writer_fence_proposal_digest": Digest,
  "realization_policy": EvidenceRef,
  "drain_observation_generation": Text,
  "active_writer_count": 0,
  "observations": set<FencePredicateObservation>,
  "authority": "NONE"
}

ServiceDisableEvidence := {
  "kind": "hindsight-compatibility-service-disable-evidence",
  "schema_version": 1,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest,
  "fence_generation": PositiveSafeInteger,
  "writer_fence_proposal_digest": Digest,
  "realization_policy": EvidenceRef,
  "service": ServiceIdentity,
  "predicate_observation": FencePredicateObservation,
  "disable_attestation": EvidenceRef,
  "authority": "NONE"
}

OriginFenceManifestBinding := {
  "kind": "hindsight-compatibility-origin-fence-manifest-binding",
  "schema_version": 1,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest,
  "fence_generation": PositiveSafeInteger,
  "adoption_generation": 0,
  "prior_manifest_binding_digest": "NONE",
  "cutover_id": Id,
  "manifest_body_digest": Digest,
  "manifest_approval_digest": Digest,
  "writer_fence_proposal_digest": Digest,
  "continuity_session_id": Id,
  "publication_epoch": SafeInteger,
  "incarnation_capability_digest": Digest,
  "deployment_attestation": EvidenceRef,
  "realized_admission_digest": Digest,
  "realized_acl_digest": Digest,
  "zero_live_writer_evidence_digest": Digest,
  "drain_observation_generation": Text,
  "service_disable_evidence_set_digest": Digest,
  "observed_at_unix_s": UnixSecond,
  "authority": "NONE"
}

ActiveFenceManifestAdoption := {
  "kind": "hindsight-compatibility-active-fence-manifest-adoption",
  "schema_version": 1,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest,
  "fence_generation": PositiveSafeInteger,
  "adoption_generation": PositiveSafeInteger,
  "prior_manifest_binding_digest": Digest,
  "cutover_id": Id,
  "manifest_body_digest": Digest,
  "manifest_approval_digest": Digest,
  "writer_fence_proposal_digest": Digest,
  "continuity_session_id": Id,
  "publication_epoch": SafeInteger,
  "incarnation_capability_digest": Digest,
  "deployment_attestation": EvidenceRef,
  "realized_admission_digest": Digest,
  "realized_acl_digest": Digest,
  "zero_live_writer_evidence_digest": Digest,
  "drain_observation_generation": Text,
  "service_disable_evidence_set_digest": Digest,
  "observed_at_unix_s": UnixSecond,
  "authority": "NONE"
}

TargetState := {
  "generation": Text,
  "selected_cohort_digest": Digest,
  "preserved_cohort_digest": Digest,
  "snapshot_digest": Digest
}

ClosureCaseBinding := {
  "kind": "hindsight-compatibility-closure-case-binding",
  "schema_version": 1,
  "closure_case_id": Id,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest,
  "source_inventory_observation_id": Id,
  "source_chain_root_digest": Digest,
  "raw_identity_set_digest": Digest,
  "historical_identity_set_digest": Digest,
  "reader_registry_digest": Digest,
  "reader_contract_set_digest": Digest,
  "observed_generation": Text,
  "selected_cohort_digest": Digest,
  "preserved_cohort_digest": Digest,
  "expected_postimage_digest": Digest,
  "deployment_attestation": EvidenceRef,
  "qualified_clock_envelope": EvidenceRef,
  "maximum_observation_attempts": PositiveSafeInteger,
  "maximum_reservation_duration_ms": PositiveSafeInteger,
  "maximum_resolution_duration_ms": PositiveSafeInteger,
  "lock_timeout_ms": PositiveSafeInteger,
  "statement_timeout_ms": PositiveSafeInteger,
  "transaction_timeout_ms": PositiveSafeInteger,
  "idle_in_transaction_timeout_ms": PositiveSafeInteger,
  "connection_lifetime_ms": PositiveSafeInteger,
  "created_at_unix_s": UnixSecond,
  "expires_at_unix_s": UnixSecond
}

ClosureObservationIdentity := {
  "closure_case_digest": Digest,
  "observation_request_id": Text,
  "attempt_ordinal": PositiveSafeInteger
}

ClosureObservation := {
  "kind": "hindsight-compatibility-closure-observation",
  "schema_version": 1,
  "closure_observation_id": Id,
  "closure_case_digest": Digest,
  "observation_request_id": Text,
  "attempt_ordinal": PositiveSafeInteger,
  "observer_lease_generation": PositiveSafeInteger,
  "timing_evidence_mode": "QUALIFIED_SAMPLE" | "ATTESTED_INVALIDATION",
  "timing_evidence": EvidenceRef,
  "reservation_deadline_monotonic_ticks": DecimalString,
  "result": "EXACT_MATCH" | "MISMATCH" | "UNABLE_TO_VERIFY" |
            "ABANDONED_UNABLE_TO_VERIFY",
  "case_outcome_after": "OPEN" | "CLOSURE_MATCH" |
                        "CLOSURE_MISMATCH" |
                        "CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY",
  "observed_postimage_digest": Digest | "NONE",
  "comparison_evidence": EvidenceRef | "NONE",
  "failure_category": Token | "NONE",
  "failure_evidence": EvidenceRef | "NONE",
  "observed_at_unix_s": UnixSecond,
  "authority": "NONE",
  "permitted_action": "DISPLAY_AND_CLASSIFY_ONLY"
}
```

`writer_fence_proposal_digest` is SHA-256 over the complete canonical
`WriterFenceProposal` bytes including the LF. The proposal has exactly one
`LOGIN_ADMISSION_DENIED`, one `CONNECTION_ADMISSION_DENIED`, and one
`TARGET_WRITE_ACL_DENIED` predicate for every member of `database_roles`;
exactly one `ZERO_LIVE_WRITER` predicate for the target surface; and exactly
one `SERVICE_DISABLED` predicate for every member of `services`. The subject
identity is SHA-256 over the complete canonical `RoleIdentity` or
`ServiceIdentity` bytes including the LF for a role or service predicate, and
is the exact `target_surface_digest` for `ZERO_LIVE_WRITER`. A missing, extra,
duplicated, wrong-kind, or wrong-subject predicate makes the proposal invalid.

Every `FencePredicateObservation` has stable identity `predicate_id` and must
match exactly one proposal predicate on `predicate_id`, kind, subject,
derivation contract, and expected digest. Its `observed_value_digest` must
equal that predicate's `expected_value_digest`; equivalent caller testimony
does not satisfy it. `RealizedAdmissionEvidence.observations` is exactly the
proposal's login- and connection-admission predicate set,
`RealizedAclEvidence.observations` is exactly its target-write-ACL predicate
set, and `ZeroLiveWriterEvidence.observations` contains exactly its sole
zero-live-writer predicate. Each body binds the same target, surface, fence
generation, proposal digest, and realization-policy reference. Its named
evidence digest is SHA-256 over that complete canonical body including the LF;
the digest is absent from its own body.

Every `ServiceDisableEvidence` matches exactly one proposed service by
`service_id`, exact canonical service bytes, and
`predicate_observation.subject_identity_digest`; embeds that service's exact
`SERVICE_DISABLED` observation; and binds one protected adapter attestation.
The stable identity of the service evidence is `service.service_id`.
`service_disable_evidence_set_digest` is SHA-256 over the separator-free
concatenation of every complete canonical `ServiceDisableEvidence` body,
including each LF, in compatibility set order. The set has exactly one member
for every proposed service and no other member; same-identity different bytes
conflict. `drain_observation_generation` in either binding body must equal the
same field in the exact locked `ZeroLiveWriterEvidence` body.

The origin/adoption binding-body digest is likewise SHA-256 over the complete
canonical body including the LF. The digest is not a field of either body and
therefore is not part of its own preimage. The protected origin row is unique
on `(target_database_identity, target_surface_digest, fence_generation, 0)`;
each adoption row is unique on the same first three values plus its positive
`adoption_generation`. The origin body is generation zero with no prior
binding. Every adoption body names generation `n-1`'s exact body digest and is
accepted only as generation `n`, so the binding chain is contiguous and
acyclic. Every realized-evidence digest in either binding is recomputed from
the exact locked protected body or set above. A caller-supplied projection or
equivalent value cannot substitute for those rows.

The stable identities for the three proposal sets are `service_id`, `role_id`,
and `predicate_id`; the realized predicate-observation sets use that same
`predicate_id` identity. A closure case has stable identity `closure_case_id`, with a
uniqueness constraint over the exact case key defined above, including the
deterministic source observation identity; its
`closure_case_digest` is SHA-256 over the complete canonical
`ClosureCaseBinding` bytes including the LF. A closure observation has stable
identity `closure_observation_id`. The protected reservation transaction
constructs `ClosureObservationIdentity` with exactly the three declared fields
and no others, canonicalizes it under the same compatibility JSON contract—so
that contract's UTF-8 encoding and sorted-key order fix the preimage—including
its LF, and hashes those bytes with SHA-256. It then computes lowercase RFC
4122 UUIDv5 in the standard URL namespace
`6ba7b811-9dad-11d1-80b4-00c04fd430c8` using the exact ASCII name
`https://github.com/nisavid/agents/tooling/hindsight/closure-observation/v1#sha256=`
followed by the 64 lowercase digest characters. That UUID is the only valid
`closure_observation_id`; callers never choose it. A changed case digest,
request identity, or ordinal produces another identity. The
`closure_observation_digest` is SHA-256 over the complete canonical
`ClosureObservation` bytes including the LF.

The observation result is a closed tagged union. `EXACT_MATCH` requires
`case_outcome_after=CLOSURE_MATCH`, while `MISMATCH` requires
`case_outcome_after=CLOSURE_MISMATCH`; each requires a postimage digest and
comparison evidence, both failure fields `"NONE"`, and
`timing_evidence_mode=QUALIFIED_SAMPLE` whose exact evidence proves a sample
strictly before both deadlines. `UNABLE_TO_VERIFY` likewise requires
`QUALIFIED_SAMPLE`; it and `ABANDONED_UNABLE_TO_VERIFY` require a failure
category and failure evidence and require both postimage and comparison fields
to be `"NONE"`. An abandonment uses `QUALIFIED_SAMPLE` only when its timing
evidence proves the old deadline reached, or `ATTESTED_INVALIDATION` when the
evidence proves a dead observer incarnation and includes the fresh qualified
sample under the still-valid case-bound clock envelope without pretending that
the old observer supplied it. Reboot, suspend uncertainty, clock-envelope loss,
rollback, drift, or excessive error instead makes the case a remediation
blocker. Their case outcome is `OPEN` below the attempt ceiling and
`CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY` exactly at the ceiling. No other
combination is valid. Positive attempt ordinals are contiguous within a case,
and the request identity is unique within that case.

The protected closure-case row stores the exact immutable case body and digest
plus a mutable terminal-observation digest initially `"NONE"`. Each protected
attempt row stores the exact immutable observation body and digest. A terminal
observation must be the case row's exact terminal digest; an `OPEN` observation
must not occupy that slot. Generic referenced evidence, a matching content
digest without the protected row, or reconstructed equivalent values cannot
substitute for either row.

The exact manifest-basis body is:

```text
ManifestBasis := {
  "kind": "hindsight-compatibility-manifest-basis",
  "schema_version": 1,
  "cutover_id": Id,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface": TargetSurface,
  "successor_protocol_family": "hindsight-postgresql-publication",
  "successor_protocol_version": 1,
  "lineage_generation": 0,
  "genesis_head": "NONE",
  "epoch_activation_proposal": EpochActivationProposal,
  "qualified_clock_envelope": EvidenceRef,
  "upper_bound_derivation_contract": EvidenceRef,
  "reader_registry_digest": Digest,
  "discovery_roots": set<DiscoveryRoot>,
  "inventory_observations": set<InventoryObservation>,
  "dependency_edges": set<DependencyEdge>,
  "inventory_digest": Digest,
  "writer_fence_proposal": WriterFenceProposal,
  "target_state": TargetState,
  "created_at_unix_s": UnixSecond,
  "observed_at_unix_s": UnixSecond,
  "freshness_deadline_unix_s": UnixSecond,
  "expires_at_unix_s": UnixSecond
}
```

`inventory_digest` is SHA-256 over the concatenation, with no separator, of
the independently canonicalized `InventoryObservation` member bytes in their
set order. `manifest_basis_digest` is deliberately absent from this body.

The artifact-exclusion body is:

```text
ArtifactExclusion := {
  "kind": "hindsight-compatibility-artifact-exclusion",
  "schema_version": 1,
  "exclusion_id": Id,
  "cutover_id": Id,
  "manifest_basis_digest": Digest,
  "inventory_observation_id": Id,
  "locator": LocatorObservation,
  "byte_length": SafeInteger,
  "source_sha256": Digest,
  "target_scope_mode": "DETERMINATE" | "CONSERVATIVE_OVERLAP",
  "target_surface_digest": Digest,
  "target_overlap_evidence": EvidenceRef | "NONE",
  "failure_category": "UNKNOWN_ARTIFACT" | "INVALID_ARTIFACT" |
                      "KNOWN_UNDISPATCHABLE",
  "supporting_evidence": EvidenceRef,
  "issued_at_unix_s": UnixSecond,
  "expires_at_unix_s": UnixSecond,
  "authority": "NONE"
}
```

`target_overlap_evidence` is `"NONE"` exactly for `DETERMINATE` and an
`EvidenceRef` exactly for `CONSERVATIVE_OVERLAP`. The stable exclusion identity
is `exclusion_id`; `exclusion_body_digest` is absent from this body.

The approval body is:

```text
Approval := {
  "kind": "hindsight-compatibility-approval",
  "schema_version": 1,
  "approval_id": Id,
  "decision": "APPROVE",
  "domain": "CUTOVER_MANIFEST" | "ARTIFACT_EXCLUSION",
  "subject_kind": "hindsight-compatibility-final-manifest" |
                  "hindsight-compatibility-artifact-exclusion",
  "subject_digest": Digest,
  "cutover_id": Id,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest,
  "operator_principal": Text,
  "issued_at_unix_s": UnixSecond,
  "expires_at_unix_s": UnixSecond
}
```

`CUTOVER_MANIFEST` requires final-manifest `subject_kind` and the exact
`manifest_body_digest`; `ARTIFACT_EXCLUSION` requires artifact-exclusion
`subject_kind` and the exact `exclusion_body_digest`. The stable approval
identity is `approval_id`; `approval_digest` and channel receipt are absent
from this body.

The final body uses these exact members:

```text
ExclusionBinding := {
  "exclusion_body": ArtifactExclusion,
  "exclusion_body_digest": Digest,
  "approval_body": Approval,
  "approval_digest": Digest
}

FinalDisposition := {
  "ordinal": SafeInteger,
  "inventory_observation_id": Id,
  "disposition": HistoricalClassification | "EXCLUDED_OPAQUE" |
                 "PRESERVED_DISJOINT",
  "exclusion_id": Id | "NONE"
}

LegacyPredecessor := {
  "class": "LEGACY_COMPLETE_APPLY",
  "inventory_observation_id": Id,
  "target_surface_digest": Digest,
  "source_chain_root_digest": Digest,
  "raw_identity_set_digest": Digest,
  "historical_identity_set_digest": Digest,
  "reader_contract_set_digest": Digest,
  "encrypted_preimage_identity": EvidenceRef
}

FinalManifest := {
  "kind": "hindsight-compatibility-final-manifest",
  "schema_version": 1,
  "cutover_id": Id,
  "manifest_basis_digest": Digest,
  "exclusion_bindings": set<ExclusionBinding>,
  "final_dispositions": sequence<FinalDisposition>,
  "predecessor_selection": "NONE" | LegacyPredecessor
}
```

The predecessor selection and every `ClosureCaseBinding` use the same three
projection-set contracts. For a predecessor, the member-id set is exactly the
complete authenticated dependency closure of the selected `COMPLETE_APPLY`
inventory member. For a closure case, it is exactly the complete authenticated
dependency closure of that case's `CLOSURE_CANDIDATE` source at case creation.
Their preimages use these exact objects:

```text
RawIdentityMember := {
  "inventory_observation_id": Id,
  "source_sha256": Digest,
  "byte_length": SafeInteger
}

HistoricalIdentityMember := {
  "inventory_observation_id": Id,
  "artifact_kind": Token | "NONE",
  "authenticated_dependency_role": Token | "NONE",
  "artifact_schema_version": SafeInteger | "NONE",
  "historical_digests": set<DigestBinding>
}

ReaderContractMember := {
  "inventory_observation_id": Id,
  "reader_contract": EvidenceRef | "NONE"
}
```

Each set has stable identity `inventory_observation_id`. Its corresponding
`*_set_digest` is SHA-256 over the separator-free concatenation of its
independently canonicalized `RawIdentityMember`, `HistoricalIdentityMember`,
or `ReaderContractMember` bytes in set order. The exact member-id set must
equal the applicable frozen reader's authenticated dependency closure—selected
predecessor or closure-case source—and the three sets must have the same
member-id set. An omitted, extra, unreadable, or differently ordered member
conflicts. Each projected field equals the same inventory observation's value.
A kindless or opaque dependency therefore appears in
`HistoricalIdentityMember` with
`artifact_kind="NONE"`, its exact authenticated dependency role,
`artifact_schema_version="NONE"`, and an empty digest set when it has no
independent historical identity, and it appears in `ReaderContractMember` with
`reader_contract="NONE"` when its authenticity is supplied only by its parent
role. A recognized value cannot be replaced by `"NONE"`, and an opaque
`"NONE"` cannot be populated from path or content inference.
`RawIdentityMember` still covers every closure node, so these explicit
projections cannot hide an omitted dependency. The
`source_chain_root_digest` remains the applicable frozen historical reader's
root over that same closure and must match it exactly. No predecessor or
closure case may reuse projection digests derived from another source,
generation, inventory, or dependency closure.

An `ExclusionBinding` has stable identity
`exclusion_body.exclusion_id`. Its approval must have domain
`ARTIFACT_EXCLUSION` and subject equal to that exclusion body and digest.
`FinalDisposition.ordinal` starts at zero without gaps and names the
inventory member at that position in canonical set order. `exclusion_id` is
present exactly for `EXCLUDED_OPAQUE`; otherwise it is `"NONE"`. No inventory
identity may appear twice or be omitted. `PRESERVED_DISJOINT` is valid only for
an after-dispatch `INVALID_ARTIFACT` whose selected frozen reader's exact
failure evidence authenticates `target_overlap=DISJOINT` for this manifest's
target surface. It preserves that observation without an exclusion and has no
predecessor, dependency-root, closure, or successor authority. No unknown,
undispatchable, no-reader, before-dispatch-invalid, drifted, unreadable, or
indeterminate observation can use this disposition. `manifest_body_digest` is
absent from the final body.

For completeness, the unhashed authenticated envelope is also exact:

```text
ApprovalChannelReceipt := {
  "boundary_id": Token,
  "receipt_format": Token,
  "receipt_bytes_base64url": Base64Url,
  "receipt_sha256": Digest
}

ApprovalRecord := {
  "approval_body": Approval,
  "approval_digest": Digest,
  "approval_channel_receipt": ApprovalChannelReceipt
}

ReferencedEvidence := {
  "contract_kind": Token,
  "contract_version": SafeInteger,
  "body_bytes_base64url": Base64Url,
  "body_digest": Digest
}

ManifestEnvelope := {
  "manifest_basis_body": ManifestBasis,
  "manifest_basis_digest": Digest,
  "final_manifest_body": FinalManifest,
  "manifest_body_digest": Digest,
  "referenced_evidence": set<ReferencedEvidence>,
  "exclusion_approval_receipts": set<{
    "approval_digest": Digest,
    "approval_channel_receipt": ApprovalChannelReceipt
  }>,
  "manifest_approval_record": ApprovalRecord
}
```

`ReferencedEvidence` has stable identity
`{contract_kind, contract_version, body_digest}`; exclusion receipt entries
have stable identity `approval_digest`. The required evidence set is the exact
recursive reachability closure rooted at every `EvidenceRef` in the manifest
basis, final manifest—including nested exclusion and approval bodies—and
manifest approval body. Each reachable reference must resolve to exactly one
referenced-evidence member whose decoded exact bytes hash to `body_digest` under
the exact named contract and version. The verifier decodes that member only
under that registered contract, enumerates every nested `EvidenceRef` in the
decoded body, and repeats until no new edge remains. An unknown contract or
version, missing node, wrong digest, duplicate identity with different bytes,
cycle, or unreachable referenced-evidence member is invalid; a body cannot hide
a dependency behind an opaque generic decoder. The manifest approval record
must have domain `CUTOVER_MANIFEST` and subject equal to the exact final body
and digest. Extra referenced evidence or receipts are invalid. The
approval boundary owns receipt interpretation; this envelope adds no new
receipt cryptography. `receipt_sha256` hashes only the exact bytes decoded from
`receipt_bytes_base64url`, and `ReferencedEvidence.body_digest` hashes only its
decoded `body_bytes_base64url`; neither digest includes its containing object.

Preparation first assigns an immutable `cutover_id` and constructs a canonical
manifest-basis body. The basis contains the complete raw and decoded evidence
set below but omits exclusion bodies, exclusion digests, exclusion approvals,
exclusion applicability, final resolved dispositions, the
`manifest_basis_digest` itself, the final-manifest body and digest, and
manifest approval. `manifest_basis_digest` is SHA-256 over the canonical basis
body and is stored beside, never inside, that body.

After all exclusions are approved, the canonical final-manifest body binds the
basis digest, the deterministically ordered exclusion bindings—each exact
canonical exclusion body, `exclusion_body_digest`, canonical approval body,
and `approval_digest`—and the resulting complete final dispositions. It omits
`manifest_body_digest` and manifest approval.
`manifest_body_digest` is SHA-256 over that canonical final body, is stored
beside it, and is the exact subject of manifest approval. The authenticated
manifest envelope contains the basis body and digest, final body and digest,
every exclusion approval-channel receipt keyed to its exact approval digest,
and the exact manifest approval record, including its approval-channel
receipt. Approval-channel receipts are verified from the envelope but remain
outside all hashed bodies and ordering. No object approves or hashes a digest
that transitively contains itself.

Cutover and exclusion authorization reuse the same configured,
operator-owned authenticated approval boundary that issues exact plan and
digest approvals. They do not introduce a signing key, detached signature
format, or separate cryptographic authority. That boundary emits one immutable
`hindsight-compatibility-approval` schema-1 record whose body binds:

- `decision=APPROVE` and exactly one domain,
  `CUTOVER_MANIFEST` or `ARTIFACT_EXCLUSION`;
- the subject kind and exact `manifest_body_digest` or
  `exclusion_body_digest`;
- the exact `cutover_id`, server-derived target database identity, and
  canonical target-surface digest;
- the operator principal asserted by the authenticated approval-channel
  receipt; and
- issue time and expiry.

Approval-body bytes use that same compatibility canonicalization contract.
`approval_digest` is stored outside the body. The complete record stores the
body, digest, and authenticated approval-channel receipt; the receipt remains
outside the hashed body so receipt issuance cannot form a digest cycle. The
digest supplies immutable
content binding, not authentication. Authentication comes from the configured
operator approval boundary and its receipt. The protected cutover-admission
role is the sole verifier: it authenticates that receipt as approval of the
exact canonical body and principal, recomputes the bytes and digest, and
requires exact decision, domain, subject, cutover, target, surface, and
validity matches. An approval in one domain or for one subject, target,
surface, or cutover cannot be replayed in another.

The manifest basis and final body together bind:

- basis and manifest schema versions, unique `cutover_id`, and
  `manifest_basis_digest`;
- server-derived target database identity and canonical relation set;
- successor protocol family and version;
- successor lineage generation zero and explicit genesis head;
- one fenced epoch-activation proposal containing the reserved successor
  publication epoch, deployment-attestation identity, and incarnation
  capability digest;
- one qualified clock-health envelope identity and conservative upper-bound
  derivation contract for cutover admission;
- the exact frozen reader-registry digest;
- the exact approved discovery-root set and its derivation contract;
- the complete deterministically ordered legacy inventory;
- for each basis entry, its locator observation, exact raw hash and size, kind,
  schema, historical digests, reader-contract digest, decoded reader result or
  exact failure category, dependency root, closure observation, and target
  overlap, but no exclusion applicability or final cutover disposition;
- a digest over the complete inventory;
- in the final body only, every exact approved exclusion and the deterministic
  final disposition for every basis entry;
- in the final body only, an explicit predecessor selection of `NONE` or one
  exact `{class=LEGACY_COMPLETE_APPLY, inventory_observation_id,
  target_surface_digest, source_chain_root_digest, raw_identity_set_digest,
  historical_identity_set_digest, reader_contract_set_digest,
  encrypted_preimage_identity}` value; no second predecessor may be selected;
- the proposed legacy-writer fence identity, exact services and database
  roles, target-surface partition proof, expected quiescence predicates, and
  policy used to realize and validate the fence, but not the later realized
  service-disable, login/connection/write-admission, ACL, zero-live-writer
  drain, drain-observation-generation, or fence-generation evidence;
- live target generation, cohort, and snapshot digest;
- creation and observation times, freshness bound, and expiry;
- outside the final body, `manifest_body_digest`; and
- outside the final body, the exact manifest approval record and its
  `approval_digest`, whose subject is that body digest.

The manifest is prepared from read-only evidence. Its fenced epoch-activation
proposal reserves identity only: it cannot satisfy a stage predicate, install
the session-local witness, activate mutation traffic, or create a lineage.
Its legacy-writer fence proposal likewise fixes identities, scope, and
predicates without claiming that the fence is installed. Preparation does not
fence a writer or create publication authority. Manifest approval is distinct
from apply and rollback approval.

### Activation

Initial cutover is a specialization of #73's publication-epoch activation, not
a second activation owner. Before the first external fence step, a read-only
pre-fence gate closes the complete authenticated envelope graph. It canonicalizes
the exact basis body, recomputes `manifest_basis_digest`, canonicalizes the
exact final body, recomputes `manifest_body_digest`, and proves that the final
body names that basis digest. It also independently canonicalizes the exact
relation members, inventory observations, and, when a legacy predecessor is
selected, every raw-identity, historical-identity, and reader-contract
projection member; it independently expands and canonicalizes the frozen
reader-registry members; and it recomputes `target_surface_digest`,
`inventory_digest`, `reader_registry_digest`, `raw_identity_set_digest`,
`historical_identity_set_digest`, and `reader_contract_set_digest` from those
preimages and rejects any mismatch.
For every closure-derived inventory member, it exact-queries the protected case
and attempt rows, recanonicalizes and recomputes their exact v1 bodies and
digests, reconstructs that source's complete inventory dependency closure and
all three identity projections, and proves the reader-registry, identity-set,
chain-root, case binding, observation union, terminal-slot, and claimed
disposition linkage.
For every final-body exclusion binding, it
canonicalizes and recomputes the exclusion body and digest and approval body
and digest, proves their exact linkage and subject, and authenticates the
corresponding envelope receipt and asserted principal. It likewise
authenticates the manifest approval receipt, principal, subject, and digest;
then it validates the exact target and fence proposal derived from the verified
basis. It locks and authenticates the exact deployment attestation bound by the
epoch-activation proposal and fence realization policy, proves that its target,
writer set, adapter, validity interval, and revocation state still match, and
holds those protected records through invocation issuance. A missing, extra,
duplicate, conflicting, unlinked, or invalid body,
digest, approval, receipt, or principal fails before mutation.

The gate then locks and validates the bound qualified clock envelope, samples
its monotonic clock, and derives `U_prefence` with the same conservative
formula. It requires `U_prefence` to be strictly below the deployment
attestation expiry, manifest expiry and freshness deadline, manifest approval
expiry, and every exclusion-body and exclusion-approval expiry. The gate
performs no durable compatibility,
publication, or target write, but it does register one short-lived invocation
in the trusted adapter's local anti-replay state. That invocation binds the
exact manifest and fence proposal, an adapter-incarnation identity, an
unguessable invocation nonce, and a qualified monotonic deadline capped by
both the adapter's short-lived invocation limit and the conservative monotonic
equivalent of the earliest deployment-attestation expiry, manifest expiry,
freshness deadline, manifest-approval expiry, exclusion-body expiry, or
exclusion-approval expiry under the bound clock envelope and error model.

Before the first external fence effect, the adapter takes an exclusive local
effect-attempt lock and atomically changes that exact local invocation from
`ISSUED` to `CONSUMED`. It retains the lock until the first PostgreSQL fence
transaction has committed or rolled back. A missing, already consumed,
concurrently consumed, mismatched, or expired invocation is rejected before
any external effect. Consumption also locks and revalidates the exact qualified
clock-envelope identity and its current validity and the exact deployment
attestation identity, target, writer set, adapter, validity, and unrevoked state,
then samples the same qualified monotonic source. It requires that sample to
remain inside the attested monotonic range and rejects attestation replacement,
revocation, or expiry, clock identity loss, rollback, reboot or suspend
uncertainty, envelope drift, or excessive error. Only then does it
derive conservative `U_consume` from that revalidated envelope and error model
and reject when `U_consume` reaches the deployment-attestation expiry or any
other validity or freshness bound. Each
adapter start chooses a fresh incarnation and begins with no issued
invocations, so a restart invalidates every token issued by the prior
incarnation rather than making it reusable. The gate and first fence effect
therefore run through the same live adapter incarnation. An invalid,
substituted, stale, expired, replayed, or concurrently reused object cannot
disable a writer.

After that gate succeeds, the trusted fence adapter revalidates the proposal's
exact target-surface partition proof while holding the same effect-attempt
lock. Its first durable or external fence effect is one synchronous PostgreSQL
transaction. Before that transaction changes admission or ACL state, it locks
the fixed target fence slot and the exact protected deployment-attestation and
proposal prerequisites; under those locks, the adapter revalidates the exact
consumed invocation digest, nonce, incarnation, proposal, target partition,
qualified clock envelope, deployment attestation, and every validity and
freshness bound. It takes a fresh qualified monotonic sample and derives a
conservative `U_fence_start` through the transaction's attested finite maximum
duration. Any expiry, revocation, replacement, scope drift, clock uncertainty,
or `U_fence_start` reaching a bound rolls back before the first fence change.
The transaction then allocates the monotonic fence generation, creates a
protected `FENCE_PENDING` row binding the origin `cutover_id`,
`manifest_body_digest`, manifest-approval digest, canonical proposal digest,
consumed invocation digest, exact role, session, epoch, capability,
deployment-attestation identity, and service step set, and initializes
`adoption_generation=0` with `current_manifest_binding_digest="NONE"`. It
removes login and connection admission for every named legacy principal,
revokes its target write capabilities, constructs the exact
`RealizedAdmissionEvidence` and `RealizedAclEvidence` bodies from the complete
matching proposal-predicate partitions observed under those locks,
canonicalizes and stores those bodies and their digests, and advances the row
to `ACCESS_REVOKED`. A missing, extra, duplicated, wrong-subject, or mismatched
predicate observation rolls the transaction back. Immediately before commit,
while every database and local
effect-attempt lock remains held, the adapter revalidates the same invocation,
clock envelope, attestation, proposal, and bounds, takes another qualified
sample, and requires conservative `U_fence_commit` to remain strictly below
every bound. Failure rolls the whole transaction back. The transaction either
commits that complete reconnect barrier and resumable state or changes nothing.
An aborted transaction permanently spends the local invocation and releases
the effect-attempt lock; a retry requires a fresh pre-fence gate and invocation,
not the old `CONSUMED` record. A lost commit acknowledgement resolves only by
locking and exact-querying the fence row's consumed-invocation digest and the
live realized admission and ACL state. `ACCESS_REVOKED` does not claim
quiescence: a transaction that passed its privilege check before commit may
still finish until its backend is fenced.

Recovery then establishes the database-side quiescence barrier. With new
legacy connections and privilege acquisition blocked, the trusted database
adapter enumerates, cancels or terminates, and waits out every session,
statement, transaction, prepared transaction, replication path, and background
writer admitted by any named principal or inheritable grant closure. The
deployment attestation and target-partition proof define that complete writer
set. A prepared or background writer that cannot be attributed and fenced is a
hard blocker. Only an exact zero-live-writer observation followed by a
synchronous transaction that revalidates the unchanged admission and ACL
digests may construct and store the exact canonical
`ZeroLiveWriterEvidence` body and digest and advance the row to
`SESSIONS_DRAINED`. That body must contain the complete matching
zero-live-writer predicate set, `active_writer_count=0`, and the protected
drain observation generation under the same proposal, fence, target, and
realization policy; any mismatch rolls the transaction back.
That commit, not `ACCESS_REVOKED`, establishes the target-wide write barrier.

A crash before `SESSIONS_DRAINED` may permit already-authorized legacy work to
finish, but creates no successor authority. Recovery exact-replays the same
pending operation, repeats the complete drain, and later activation reobserves
the target generation, cohort, and snapshot after the barrier; any intervening
legacy mutation therefore causes the approved manifest to fail revalidation.

With the database-side barrier continuously held, the adapter disables each
exact named service under the fence operation identity. After each externally
durable disable, a protected compare-and-swap appends that service's
exact canonical `ServiceDisableEvidence` body and digest, attestation, and step
completion to the pending row. A crash between an external disable and its
progress append is resolved by exact-querying both states:
recovery adopts only an exact expected disable attestation or idempotently
repeats that same disable, and never re-enables a service. Once every service
step is durably accounted for, one final transaction locks the row, admission,
and role ACLs and revalidates the complete drain and service-attestation
evidence. It reconstructs the exact one-member-per-proposed-service evidence
set, canonicalizes every member in compatibility set order, and recomputes the
complete `service_disable_evidence_set_digest`; a missing, extra, duplicated,
conflicting, or mismatched member aborts. It constructs the exact
`OriginFenceManifestBinding` from the
protected origin fields and realized evidence, canonicalizes and appends that
immutable body, sets the current manifest-binding pointer to its body digest,
and advances the same generation to `FENCE_ACTIVE` in one synchronous commit.
No `FENCE_ACTIVE` row has a missing, caller-chosen, or noncanonical current
binding.

`FENCE_PENDING` and partial progress create no successor epoch, manifest,
genesis, stage, or mutation authority. Activation requires the exact
`FENCE_ACTIVE` row. Any crash after `SESSIONS_DRAINED` leaves the target-wide
database write barrier in force and resumes only the named fence operation;
removing that barrier or restoring a service requires separate authorization
under a separately accepted fence-removal design.

If manifest drift is found after the row reaches `FENCE_ACTIVE` but before any
successor authority exists, a fresh manifest never reuses the origin
invocation. It uses the distinct protected `ADOPT_ACTIVE_FENCE` branch, whose
compare-and-swap key is:

```text
(
  target_database_identity,
  target_surface_digest,
  fence_generation,
  expected_adoption_generation,
  expected_current_manifest_binding_digest
)
```

The fresh read-only pre-fence gate still authenticates the complete recursive
manifest envelope, approval, target, proposal, deployment attestation, clock,
inventory, and target state, but in this branch it issues no fence invocation
and performs no external effect. One synchronous protected PostgreSQL
transaction then locks the fixed fence slot, exact `FENCE_ACTIVE` row, current
manifest binding, admission and ACL rows, complete drain and service evidence,
epoch slot, manifest slot, genesis slot, deployment attestation, clock envelope,
and proposal binding. It requires no successor manifest, genesis, active epoch,
stage, or mutation receipt; exact equality of target, continuity session,
reserved epoch, capability digest, canonical writer-fence-proposal digest,
fence generation, realized admission and ACL digests, zero-live-writer drain
evidence and generation, and service-disable evidence; and a fresh live
recomputation of every fence predicate. It revalidates the attestation and
qualified time under those locks and requires conservative `U_adopt` strictly
below every fresh-manifest, approval, exclusion, attestation, and clock bound
through commit. Any mismatch or concurrent revocation, replacement, fence
change, regrant, writer, service enablement, or successor authority aborts
without changing the binding.

On success, the transaction constructs the exact
`ActiveFenceManifestAdoption` body from the complete compare-and-swap key, the
next `adoption_generation`, the prior binding digest, the fresh manifest and
approval, and the exact protected origin and realized fence evidence listed in
the schema above. It canonicalizes and appends that immutable body, atomically
advances the fence row's current manifest-binding digest to its body digest,
and commits with
`synchronous_commit=on`; it changes no service, admission, ACL, target, epoch,
manifest, genesis, or publication-lineage state. The exact same new cutover,
manifest, approval, prior binding, and generation replays by returning that
body and digest only while the current pointer still equals that body digest.
If a later adoption is current, the old exact request is
`SUPERSEDED_BINDING`, not successful replay. A changed input, otherwise stale
prior binding, reused cutover with another manifest, skipped generation, or
second concurrent candidate is `CONFLICT`.
Lost acknowledgement resolves only by locking and exact-querying the adoption
body and current pointer. All prior origin and adoption bodies remain
immutable evidence, but only the current binding can enter the combined
activation transaction. A later fresh manifest repeats this same compare-and-
swap from the then-current binding; it never edits or deletes a prior record.

After exact acknowledgement or recovery of that durable fence, one combined
transaction on the proposed continuity session:

1. authenticates the exact manifest and approval; locks the active fence's
   current manifest-binding pointer and the protected origin and adoption
   rows; replays the contiguous body-digest chain from generation zero through
   the row's exact `adoption_generation`; canonicalizes and recomputes every
   body digest; and requires the current body to name this manifest, approval,
   proposal, target, fence generation, session, epoch, capability, attestation,
   and exact realized fence evidence;
2. derives the canonical target database identity and surface on the server,
   independently canonicalizes its exact relation members, and recomputes the
   bound `target_surface_digest`;
3. locks and revalidates the manifest-bound enforceable service and
   database-role fence for every relevant legacy writer, proves that every
   realized identity and predicate exactly satisfies the approved proposal,
   and holds it without a gap through durable commit; apparent process absence
   is insufficient;
4. while that fence is held, reopens or reuses safely held descriptors, holds
   them through the remaining checks, proves the complete inventory is
   unchanged, and independently canonicalizes every observation to recompute
   the bound `inventory_digest`;
5. revalidates every frozen reader-contract digest and decoded disposition
   from those exact held bytes and, for every closure-derived disposition,
   locks and exact-queries its protected case and attempt rows, recomputes their
   canonical v1 body digests and the exact reader registry and source
   raw/historical/reader-contract projection-set digests, and revalidates the
   complete chain root, case, result, terminal-slot, target, generation, cohort,
   and postimage linkage;
6. proves that the live target generation, cohort, and snapshot still match;
7. derives every final disposition and admits only `COMPLETE_APPLY`,
   `COMPLETE_ROLLBACK`, `PENDING_UNAUTHENTICATED`,
   `FROZEN_TARGET_ABSENT`, `CLOSURE_MATCH`, `AUTHENTICATED_DEPENDENCY`,
   `OPAQUE_DEPENDENCY`, `EXCLUDED_OPAQUE`, or `PRESERVED_DISJOINT`, where an
   admitted dependency
   disposition must be reachable only through exact authenticated dependency
   edges in the complete closure of one or more admitted nondependency roots,
   must retain its exact role and raw identity, and must not also be a
   discovery root or standalone predecessor; and where
   `EXCLUDED_OPAQUE` applies only to one safely readable, stable, current exact
   entry in the closed exclusion-eligible failure set with its exact valid
   exclusion, and an indeterminate target requires explicit conservative
   overlap with this target surface; and `PRESERVED_DISJOINT` applies only to an
   after-dispatch `INVALID_ARTIFACT` with exact selected-reader failure evidence
   authenticating `DISJOINT` from this target and creates no authority; it rejects
   `CLOSURE_CANDIDATE`, `CLOSURE_MISMATCH`,
   `CLOSURE_UNABLE_TO_VERIFY`,
   `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY`, `ROLLBACK_UNAVAILABLE`,
   `DRIFTED_ARTIFACT`, and `UNREADABLE_ARTIFACT`, every unclassified,
   ambiguous, missing, or unresolved-dependency artifact, every entry in the
   closed exclusion-eligible set without an exact valid exclusion or exact
   `PRESERVED_DISJOINT` proof, and every
   otherwise unexplained overlapping artifact;
8. validates that predecessor selection is `NONE` or names exactly one
   admitted `COMPLETE_APPLY` entry whose inventory identity and
   `target_surface_digest` match this manifest, whose frozen reader output says
   `target_overlap=EXACT_TARGET`, and whose exact reader-derived target surface
   equals the manifest surface—`DISJOINT`, `INDETERMINATE`, or conservative
   overlap can never qualify—with the exact class and preimage binding;
   for a selected predecessor, independently projects and canonicalizes every
   member of its authenticated dependency closure and recomputes
   `raw_identity_set_digest`, `historical_identity_set_digest`, and
   `reader_contract_set_digest`; and rejects a digest mismatch, closure
   mismatch, or competing terminal predecessor;
9. locks and validates the exact manifest-bound qualified clock-health
   envelope, deployment-attestation row, and epoch-activation proposal binding;
   proves that the attestation remains current, unrevoked, unexpired, and
   unchanged in target, writer set, adapter, validity, and guard capabilities;
   holds those records through synchronous commit so attestation revocation or
   replacement serializes against activation; then samples the monotonic clock
   after every preceding revalidation and derives `U_cutover` with #73's
   conservative upper-bound formula; the sample must remain within the
   envelope's monotonic validity, and
   `U_cutover` must be strictly less than the deployment-attestation expiry,
   manifest expiry and freshness deadline, manifest approval expiry, and every
   exclusion-body and exclusion-approval expiry—equality, attestation
   replacement, revocation, or expiry, clock rollback or loss, reboot or
   suspend uncertainty, envelope drift, or excessive error refuses
   activation;
10. proves that the exact epoch-activation proposal remains fenced and the
    adapter holds its capability preimage on this exact session; and
11. installs the session-local witness and atomically stores the authenticated
    manifest, creates the unique successor genesis, and makes the reserved
    publication epoch active for the target surface.

The writer fence and held evidence descriptors remain effective through the
transaction's synchronous durable commit. The fence remains installed after
activation; losing it fences successor mutation instead of reopening a legacy
writer path. The stored cutover observation records the exact clock-envelope
digest, monotonic sample, error terms, and `U_cutover`. Like #73's timely `R`,
it may durably commit after an already-established pre-expiry sample, but the
manifest itself authorizes no target mutation and every successor action still
requires its own approval and `J -> P -> R -> M -> V` chain.

The activation transaction binds the pre-existing protected monotonic
legacy-writer fence row to the manifest, target surface, publication epoch,
service-disable-attestation digest, login/connection/write-admission digest,
database-role ACL digest, zero-live-writer drain-evidence digest and
observation generation, and fence generation. The activation's admission state
names that exact generation and state `FENCE_ACTIVE`. Every successor `J`, `P`,
`R`, and `M` transaction locks both admission and fence rows, recomputes the
live service-disable, admission, ACL, and complete writer-set drain evidence
through the trusted adapters, and requires the exact bound generation,
zero-live-writer observation generation, and `FENCE_ACTIVE` state. Each
aggregate and stage binding carries that generation and every evidence digest.
Restored login or connection admission, a write regrant, a newly live writer
path, or any evidence drift after `R` therefore makes `M` refuse rather than
consume the old receipt.

This record authorizes no legacy-writer re-enable or successor recutover. Any
future fence-removal design must first lock both rows and synchronously commit
an epoch fence plus a fence-generation advance before it can consider enabling
a service or regranting a role. A crash before any later authorized external
step must leave the successor epoch fenced. Actual legacy reactivation must
also prove that no frozen pending publication can mutate without new authority;
non-genesis successor recutover requires its own transition and lineage design.
Direct service enablement or role grants invalidate deployment attestation;
ordinary runtime roles lack those capabilities.

No committed state may expose an active successor epoch without its exact
manifest and genesis, or a manifest/genesis whose epoch is not active. An abort
leaves the epoch proposal and legacy writers fenced and creates none of those
three facts. It never automatically restores the service or database role.
Lost acknowledgement resolves the combined activation by exact query; it never
replays one component independently.

The same manifest and exact binding replay idempotently. A different manifest,
target binding, inventory, or genesis under an occupied activation key is
`CONFLICT`. Any byte, locator, inventory, reader-contract, writer-fence,
target, generation, cohort, admission, or approval drift invalidates the
prepared manifest. The implementation regenerates and reapproves a new
manifest; it never patches an approved one. If drift is detected after the
fence operation reached `FENCE_ACTIVE` but before successor activation, the
rejected manifest remains immutable evidence and the fresh manifest receives a
new `cutover_id`. It may adopt the same still-live target, fence-proposal
identity, reserved epoch, fence generation, and realized fence evidence only
through `ADOPT_ACTIVE_FENCE` while no successor manifest, genesis, active
epoch, stage, or mutation receipt exists. The pre-fence adapter issues no fence
invocation and performs no external effect in that branch; the protected
adoption compare-and-swap durably selects the fresh manifest binding. A changed
proposal or fence, a stale expected binding, a second concurrent candidate, or
any already-created successor authority conflicts. Fresh inventory, target
state, expiries, exclusions, and approvals are still required.

Manifest freshness and approval expiry gate activation. Once exact activation
commits, the stored manifest remains the immutable historical cutover basis;
elapsed time does not erase that fact or create another activation right. A
later legacy-predecessor rollback may reference the activated manifest only
under its own live approval and only after revalidating the exact legacy
artifacts, frozen readers, current target, writer fence, preimage, and genesis.
It cannot reuse an expired cutover approval or bypass any rollback predicate.

The activated manifest is an admission and audit prerequisite, not a mutation
receipt. Each successor action still requires its own plan, approval,
publication aggregate, and `J -> P -> R -> M -> V` chain.

## Exact artifact exclusions

An artifact in the closed exclusion-eligible failure set may be excluded from
one cutover inventory only through a separate approval for its safely readable,
stable, current exact bytes. Its canonical exclusion body binds:

- one currently readable artifact's locator observation, exact byte length,
  and `source_sha256`;
- the target surface or, when the frozen reader cannot determine one,
  explicit conservative overlap with the manifest's target surface;
- failure category and supporting evidence;
- the exact `manifest_basis_digest` and `cutover_id`;
- issue time and expiry; and
- `authority=NONE`.

The exclusion body omits `exclusion_body_digest` and approval.
`exclusion_body_digest` is SHA-256 over the canonical exclusion body and is
stored beside it. Its `ARTIFACT_EXCLUSION` approval record names that exact
digest as its subject. The final manifest body then binds the exact exclusion
body, its digest, the canonical approval body, and `approval_digest`; the
authenticated manifest envelope supplies that approval's channel receipt. A
basis change invalidates the exclusion before a new final manifest is
constructed.

No wildcard, directory-wide, filename-pattern, schema-range, or “all unknown”
exclusion exists. A drifted, missing, or unreadable artifact, or a missing,
unreadable, or unresolved dependency, cannot be excluded by a remembered
digest, prior inventory, backup attestation, parent-artifact exclusion, or
locator alone. Any change to bytes, path observation, target overlap, or
manifest invalidates the exclusion.

An exclusion permits only the cutover classifier to treat the named opaque
entry as reviewed for that exact activation. It does not assert that the bytes
are valid, declare a historical outcome, authorize a successor plan or
mutation, satisfy rollback admission, or permit the artifact to be moved,
rewritten, or deleted.

## Legacy predecessor rollback bridge

The bridge is an explicit compatibility exception to the ordinary successor
rule that rollback `J` names a predecessor apply `M` and matching `V`.
It does not weaken any later `P`, `R`, `M`, `V`, deadline,
continuity, cohort, or lineage requirement.

### Eligibility

A legacy predecessor is eligible only when all of these predicates hold:

- its frozen reader returns one fully authenticated `COMPLETE_APPLY` chain
  with matching historical verification and an exact reader-derived target
  surface equal to the activated manifest surface;
- the cutover manifest selects it as the unique terminal apply predecessor for
  the target surface, binds its inventory identity and exact surface digest,
  records `target_overlap=EXACT_TARGET`, and records predecessor class
  `LEGACY_COMPLETE_APPLY`; neither disjoint nor indeterminate evidence can
  qualify;
- no completed or ambiguous legacy rollback, competing terminal apply, or
  overlapping unexplained artifact exists;
- the exact encrypted rollback preimage, ciphertext identity, integrity
  evidence, and required decryption capability remain available;
- the live target generation, selected and preserved cohorts, and exact
  postimage match the verified legacy apply;
- legacy writers remain fenced, the activated cutover manifest remains the
  exact stored historical basis, and every bound artifact and reader still
  revalidates;
- the successor lineage is still at generation zero with the manifest's
  genesis head; and
- no successor `M` has ever advanced that lineage.

A `CLOSURE_MATCH`, incomplete receipt chain, pending marker, unverified apply,
verification mismatch, unavailable preimage, excluded artifact, or reconstructed
legacy plan cannot satisfy eligibility. Returning the target to coincidentally
matching bytes after a successor mutation does not revive the bridge.

### New rollback authority

The rollback plan and its separate approval bind:

- action `rollback` and a new successor aggregate identity;
- `hindsight-postgresql-publication/v1`;
- the exact cutover manifest and activation;
- predecessor class `LEGACY_COMPLETE_APPLY`;
- the complete legacy chain root, raw artifact hashes, historical digests, and
  frozen reader-contract digests;
- the exact encrypted preimage, ciphertext, integrity, and decryption
  bindings;
- expected current postimage and target generation;
- selected and preserved cohorts;
- successor genesis head and canonical lineage key;
- publication epoch, grant evidence, limits, and approval expiry; and
- all ordinary rollback plan and budget ceilings.

The new rollback `J` transaction revalidates every predicate, independently
reconstructs the selected predecessor's complete authenticated dependency
closure, canonicalizes every raw-identity, historical-identity, and
reader-contract projection member, recomputes all three predecessor set
digests and the frozen reader's chain root, and proves that the selected
inventory observation still has `target_overlap=EXACT_TARGET` with exact
reader-derived target surface equality. It atomically proves that the approved
final-manifest body selected this exact predecessor, target, and class. It then
stores the exact encrypted legacy preimage and integrity binding in the
protected action-scoped preimage record. This ingest is not a general
historical byte capsule: it stores only the immutable mutation input required
by this newly approved rollback. Once `J` commits, PostgreSQL owns the
preimage binding and continued availability exactly as it does for an ordinary
successor rollback. The executor never reads an unbound legacy file as
mutation authority.

The rollback then advances through ordinary successor `P`, `R`, `M`, and
`V`. Its `M` serializably restores the exact selected preimage once,
advances the target generation once, and atomically replaces successor genesis
with its own lineage head. It preserves completed and failed rows and every
out-of-cohort row.

No compatibility operation creates a synthetic predecessor apply `M` or
`V`. The rollback's own `M` and `V` describe only the new rollback.
Historical files remain evidence; authority derives from the new approval and
new successor stages.

Two rollback publications may be prepared against genesis, but the canonical
lineage compare-and-swap permits exactly one `M`. A loser observes
`LINEAGE_HEAD_DRIFT`, cannot rebase, and cannot reuse its approval. Once any
successor `M` advances the lineage, no legacy predecessor may be admitted
again.

## Preservation and access boundaries

Historical artifacts, their directory context, encrypted preimages, frozen
reader code, canonicalization dependencies, and reader-contract descriptors
remain preserved and queryable under their original semantics. Compatibility
does not rewrite, rename, normalize, re-encrypt, delete, or garbage-collect
them. An exact exclusion likewise grants no preservation exception.

The initial PostgreSQL compatibility state stores metadata only:

- exact raw and historical digests;
- reader-contract and dependency identities;
- closure observations;
- cutover manifests and exclusion approvals;
- successor activation and lineage bindings; and
- the one action-scoped encrypted preimage required by an admitted successor
  rollback.

It does not ingest general historical journal bytes or maintain a PostgreSQL
capsule archive. A future capsule design requires a separate cost, authority,
retention, and migration decision.

Access is divided by capability:

- the inventory reader may safely read historical roots and target metadata
  but cannot append PostgreSQL state or mutate the target;
- the closure-evidence role alone may use the narrow protected closure
  interface to lock and read target state; create one exact immutable
  `ClosureCaseBinding` under its stable key; allocate the next bounded attempt
  ordinal; reserve, claim, fence, or take over that attempt's observer lease;
  append its exact `ClosureObservation`; and compare-and-swap the case terminal
  slot, abandonment, or exhausted state exactly as specified above. These are
  metadata-only transitions in the protected compatibility schema. The role
  cannot write target rows, lineage, publication stages, admission state,
  manifests, or successor authority, and inventory, fence-adapter, admission,
  ordinary runtime, publication, mutation, and verification roles cannot call
  the closure-transition interface;
- one dedicated trusted fence-adapter capability, separate from #73's
  admission role, may authenticate and consume only the exact short-lived
  invocation, lock the fixed target fence slot, remove and revalidate the
  manifest-bound login, connection, and write admission, enumerate and fence
  every attested writer path, append compare-and-swap fence progress, disable
  the exact named services, and append the exact origin binding and set its
  current pointer only in the transaction that marks the same generation
  `FENCE_ACTIVE` after the complete drain predicates hold. It cannot mutate
  target rows, create or advance successor stages, lineage, genesis, epoch, or
  successor manifest beyond that fixed origin binding,
  issue approvals, read or decrypt retained content, change the cohort,
  regrant a role, re-enable a service, or bypass manifest authentication or
  anti-replay. Ordinary runtime and admission roles cannot invoke its external
  fence operations directly;
- #73's sole admission role may call the metadata-only
  `ADOPT_ACTIVE_FENCE` interface while the occupied fence has no successor
  authority, and may use its narrow combined-cutover activation interface to
  validate the current approved manifest, create genesis, and activate the
  bound publication epoch in one transaction. Adoption cannot invoke a fence
  effect, bypass the protected compare-and-swap, or write target, service,
  admission, ACL, epoch, manifest, genesis, stage, or lineage state. This is
  not a second activation owner, and the role cannot create `R`, execute `M`,
  or alter historical bytes. The fence-adapter, closure, inventory, ordinary
  runtime, publication, mutation, and verification roles cannot call the
  adoption interface;
- publication, mutation, and verification roles retain the boundaries fixed in
  the publication and restart designs; and
- no ordinary runtime receives legacy decryption material, database role
  credentials, the activation-bound incarnation capability, or an interface
  that converts evidence into authority.

Status and audit surfaces report identities, digests, dispositions, and
failure categories. They do not emit raw retained content, decrypted
preimages, credentials, or an inference that a writer is fenced because no
process was observed.

## Observable compatibility status

Read-only status reports, per canonical target surface:

- successor protocol family, version, activation, publication epoch, lineage
  generation, and head;
- cutover manifest identity, approval, freshness, and activation state;
- inventory root, entry count, and frozen reader-registry digest;
- each legacy disposition and raw, historical, reader, dependency, and closure
  identities;
- closure attempt count and ceiling, next server-derived ordinal, terminal
  outcome, reserved-attempt deadline and recovery state, and explicit
  `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY` state;
- every blocker, exact exclusion, and exclusion validity;
- old-writer fence and live target snapshot identities;
- whether a unique `LEGACY_COMPLETE_APPLY` predecessor is selected;
- rollback-preimage availability and integrity status;
- whether the genesis-only rollback bridge is eligible, ineligible, consumed,
  or permanently unavailable;
- the exact evidence-only action, separately approved successor action, or
  terminal refusal permitted next; and
- `authority=NONE` for every legacy and closure result.

Status takes no authority-bearing lock, creates no observation or manifest,
decrypts no preimage, activates no cutover, and imports no mutation-capable
runtime merely to inspect it.

## Acceptance obligations

Issue [#76](https://github.com/nisavid/agents/issues/76) owns executable
acceptance evidence. At minimum it must prove:

### Reader and version behavior

- every supported historical artifact dispatches only to its exact frozen
  reader tuple;
- progress raw bytes use the compact ASCII-escaped, sorted-key, no-LF wire
  contract while their semantic digest retains canonical JSON, and neither
  representation is accepted under the other's wire identifier;
- grant plan, grant, ledger, claim, close, optional revocation, both history
  resolution variants, retirement chain, and decrypted stopped-row preimage
  each dispatch through their exact source-pinned contract and dependency
  role;
- the three shared-lifecycle plan kinds dispatch through every and only the
  explicit outer-schema, embedded-reference-schema, and reference-plan-variant
  tuple in the closed matrix above; tests preserve the literal outer-schema-11
  union and the accepted legacy references to schemas 14, 16, and 17 rather
  than assuming that outer and embedded schemas match;
- the stopped-run durable encrypted-bundle record accepts only exact kind
  `operation-recovery-stopped-run-reconciliation-encrypted-rollback-bundle`
  and rejects the CLI encryption helper's transient
  `operation-recovery-stopped-run-encrypted-rollback-bundle` label as a
  discovery-root artifact;
- every stopped-run reconciliation kind expands to exactly one
  schema-1/reference-16 member and two schema-2/reference-15 members, one for
  each accepted schema-15 capability variant;
- an exact-drain schema-17 plan closes only through its caller-named
  `operation-recovery-exact-drain-stopped-recovery-handoff` and that handoff's
  complete stopped-plan, stopped-outcome, cleanup-snapshot, prior-retry, source
  inventory, and digest graph; omission, an unregistered kind, or a substituted
  handoff is rejected;
- an unknown artifact kind, unknown future schema, unknown reference-plan
  version, or canonicalization mismatch has no fallback reader;
- the pending marker is classified without acquiring final-journal semantics;
- historical canonical bytes, digests, timestamp meanings, and complete-chain
  outcomes remain byte-identical to the frozen implementation;
- missing, corrupt, mismatched, cyclic, or substituted dependencies fail
  closed;
- descriptor, size, path, or byte drift between inventory and decode is
  detected; and
- every successful reader output has `authority=NONE` and cannot be submitted
  to a successor stage interface.

### Historical dispositions and closure

- complete apply and rollback chains remain preserved and queryable historical
  evidence;
- pending and target-absent journals cannot resume or mutate;
- exact already-applied comparison appends one `EXACT_MATCH` closure
  observation without invoking a legacy mutation path;
- the first conclusive case outcome is terminal, mismatch is sticky,
  unable-to-verify remains nonauthorizing, and a lost closure acknowledgement
  returns the exact existing observation;
- a deployment-attested hard ceiling and per-attempt transaction timeouts bound
  every case; a synchronous reservation consumes each contiguous ordinal before
  observation, abandoned reservations resolve nonauthoritatively, the last
  unable or abandoned attempt records terminal
  `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY`, and no later append is possible;
- a caller-known stable request identity maps uniquely to one server-derived
  ordinal; same-request same-binding replay recovers the exact result after
  lost acknowledgement, while a live observer makes even that replay return
  `OBSERVATION_IN_PROGRESS` and changed binding conflicts;
- closure-observation identity vectors independently change the exact case
  digest, request identity, and ordinal and prove that each changes the
  canonical identity preimage and resulting UUIDv5; only the UUID derived from
  the unchanged three-field preimage is accepted, while a caller-supplied,
  differently encoded, differently ordered, wrong-namespace, or wrong-domain
  ID is rejected;
- one case permits only one unresolved reservation and one live observer claim;
  concurrent requests expose the existing request, ordinal, and claim state
  without allocating another, and takeover atomically advances a server-owned
  lease generation before observing, so stale completion cannot make
  exhaustion overtake a conclusive observation;
- two concurrent executions of the same request and binding yield one live
  observer claim and one `OBSERVATION_IN_PROGRESS`; finalization under a token
  fenced by takeover is rejected, and a resolver racing a late conclusive
  observer can commit abandoned or exhausted only after advancing the
  generation that makes that observer unable to finalize;
- every observer lease is capped at its reservation deadline; finalization at
  or after either deadline is rejected, including exact equality, and the
  resolver-versus-finalizer race under the case and reservation locks admits
  only the current generation's before-deadline result or the fenced abandoned
  result;
- each reservation deadline is derived only by the protected server from a
  qualified monotonic sample, the attested maximum reservation duration, the
  remaining server-owned call bound, and a bound that reserves the complete
  maximum resolution duration before the case expiry, deployment-attestation
  expiry, and clock envelope's validity limit;
  a caller-selected, extendable, or arbitrarily distant deadline is rejected,
  clock uncertainty cannot create a reservation, and an unresolved slot cannot
  remain live past the resulting finite bound; every pre-expiry transaction's
  lock and statement timeout and its hard whole-transaction, idle-transaction,
  and adapter-enforced connection lifetime are capped by the conservatively
  measured remaining time and cannot preserve a late finalization right; a test
  that stalls after lock acquisition and another that idles between statements
  prove forced rollback releases the locks and lets the protected resolver
  finish after the bound;
- after reservation expiry or attested observer-incarnation invalidation while
  the same case-bound clock remains valid, abandonment uses a separately
  derived positive resolution deadline rather than the expired reservation
  remainder; it first closes the tagged observer session, requires a fresh
  qualified sample under that envelope, and its own finite transaction and
  connection guards let it fence the old generation and clear the unresolved
  slot without reviving target observation or successful finalization; reboot,
  suspend uncertainty, clock loss, rollback, drift, or excessive error instead
  makes the case a nonauthorizing remediation blocker and cannot fabricate a
  resolution interval;
- match and mismatch require a complete observed postimage digest; unable and
  abandoned outcomes require `observed_postimage=NONE` and exact failure
  evidence, so no unavailable digest is fabricated;
- closure case and observation bodies accept only their exact v1 kinds,
  complete key sets, canonical bytes, tagged result unions, and protected-row
  identities; an absent row, unknown contract or version, malformed result,
  unattached equivalent bytes, body-digest mismatch, wrong case link, wrong
  terminal slot, or case/source/target/generation/cohort/postimage mismatch
  prevents the closure-derived disposition at pre-fence and activation;
- case creation rejects zero or out-of-range observation-attempt,
  reservation-duration, resolution-duration, lock, statement, whole-transaction,
  idle-in-transaction, or connection-lifetime ceilings, so no JSON or database
  zero convention can disable a finite guard or create a permanently unusable
  case;
- case creation requires `created_at_unix_s < expires_at_unix_s` and caps that
  server-derived expiry at the exact current deployment attestation's expiry
  and attested case-lifetime bound; it rejects before occupying the stable key
  unless conservative time remaining covers at least one complete maximum
  reservation, maximum resolution, transaction/connection guards, and clock
  error margin; reservation,
  claim, observation, finalization, takeover, and post-reservation-expiry
  resolution each lock and revalidate that attestation and cap their deadlines
  by the case, attestation, and clock bounds, while expiry, revocation,
  replacement, target/adapter drift, or guard-capability drift makes the case a
  nonauthorizing remediation blocker that cannot admit a closure disposition;
- reservation-margin tests prove that no ordinal is consumed unless the
  conservatively bounded maximum resolution duration still fits before the
  case and attestation expiries, and attestation invalidation before each phase
  produces no target observation, final result, new attempt, synthetic terminal
  observation, or successor authority;
- same-ordinal different-binding closure attempts conflict while the next
  server-derived ordinal remains possible below the case ceiling and before a
  terminal outcome;
- crash, deadline, or observer-incarnation recovery fences the prior observer
  generation under the case and reservation locks before recording abandoned
  or exhausted, so a late observer cannot finalize;
- privilege tests prove that only the closure-evidence role can create the
  exact case row, allocate and reserve an attempt, claim or fence an observer
  lease, append its exact observation, and advance the terminal, abandonment,
  or exhausted metadata under the protected interface; every other role is
  denied, and the closure role cannot mutate the target, lineage, admission,
  manifest, or successor stages; and
- closure-only evidence cannot satisfy rollback-bridge eligibility.

### Cutover and exclusions

- manifests are independently complete and authenticated for each target
  surface;
- every fence is proven mechanically partitioned to one target surface; a
  service or role spanning surfaces blocks all affected v1 manifests, and no
  partial activation or implicit activation group exists;
- identical manifest activation replays exactly and a different binding
  conflicts;
- the epoch, manifest, and genesis activate atomically on the exact continuity
  session, while an abort leaves no partial active triple, keeps the epoch
  proposal and any already disabled legacy writers fenced, and lost
  acknowledgement resolves without partial replay;
- inventory, reader, writer-fence, target generation, cohort, snapshot,
  admission, freshness, or approval drift prevents activation;
- before any external fence step, the read-only pre-fence gate canonicalizes
  and recomputes the basis and final bodies and digests; proves their link;
  independently recomputes `target_surface_digest`, `inventory_digest`, and
  every selected predecessor projection-set digest from their exact canonical
  member preimages;
  recomputes every exclusion and approval body and digest; authenticates every
  manifest and exclusion receipt and principal; rejects any missing, extra,
  duplicate, conflicting, or unlinked envelope member; validates the exact
  target and proposal from that graph; locks and validates the exact
  manifest-bound deployment attestation, writer set, validity, and revocation
  state; and requires conservative `U_prefence` below its expiry and every
  manifest, freshness, exclusion, and approval expiry;
- the gate's trusted-adapter-local invocation binds a fresh adapter incarnation
  and nonce, caps its deadline at the conservative monotonic equivalent of the
  earliest bound, and becomes invalid across adapter restart; consumption takes
  one exclusive effect-attempt lock through the first PostgreSQL transaction,
  rejects concurrent or repeated use, and revalidates the exact clock envelope,
  deployment attestation, proposal, target, writer set, and adapter before any
  transactional fence change and again before commit;
- lock-wait and pause tests expire or revoke the attestation after local
  consumption but before the database transaction can change admission, and
  prove that the transaction's fresh `U_fence_start` and `U_fence_commit`
  checks roll back without a fence effect; the consumed invocation remains
  terminal and cannot authorize a later retry, while a lost commit
  acknowledgement resolves only through the fence row's exact invocation
  digest and realized state;
- activation establishes and continuously holds the enforceable legacy-writer
  fence before rehashing or decoding artifacts or revalidating the target and
  through synchronous durable commit; a fence gap or descriptor drift refuses
  activation;
- interruption before the atomic access-revocation transaction commits changes
  no durable compatibility, publication, or target state and performs no
  external fence effect, even if adapter-local invocation state was issued or
  consumed; every such abort spends that invocation and requires a fresh
  pre-fence issue;
- after `FENCE_ACTIVE`, manifest drift plus fresh inventory and approval uses
  `ADOPT_ACTIVE_FENCE`: tests prove its protected compare-and-swap locks the
  occupied fence, current binding, admission, proposal, attestation, clock,
  epoch, manifest, and genesis state; revalidates the exact live generation and
  evidence without issuing a fence invocation or changing any external state;
  constructs the exact canonical `ActiveFenceManifestAdoption` body, advances
  a contiguous acyclic binding chain, durably selects only the fresh
  cutover/manifest binding; exact-replays after lost acknowledgement only while
  that body remains current; returns `SUPERSEDED_BINDING` when generation
  `n+1` is current and generation `n` retries; and rejects stale bindings,
  concurrent candidates,
  changed proposal/generation/evidence, restored writers or services,
  attestation drift, and any successor authority;
  after `ACCESS_REVOKED`, new legacy login, connection, and write acquisition
  are blocked but already-authorized work is not treated as quiescent;
- crash and lost-acknowledgement tests at every drain step prove that all
  sessions, statements, transactions, prepared transactions, replication
  paths, background writers, and inheritable grants are either exactly fenced
  or block progress; only zero-live-writer evidence under unchanged ACLs may
  record `SESSIONS_DRAINED`, and the target is reobserved afterward;
- after `SESSIONS_DRAINED`, the target-wide database barrier remains held while
  every service-disable and progress compare-and-swap exact-replays or resumes,
  and no partial pending row can activate successor authority or automatically
  restore a service or role;
- manifest approval binds the exact fence proposal, identities, partitioned
  scope, and predicates—not nonexistent realized evidence—and activation
  accepts only server-derived service-disable, login/connection/write-admission,
  ACL, zero-live-writer drain, drain-observation-generation, and fence-generation
  evidence that exactly satisfies that proposal;
- independent canonicalization vectors mutate every field and set member in
  `FencePredicateObservation`, `RealizedAdmissionEvidence`,
  `RealizedAclEvidence`, `ZeroLiveWriterEvidence`, and
  `ServiceDisableEvidence`; missing, extra, duplicate, wrong-kind,
  wrong-subject, wrong-derivation, wrong-expected, wrong-observed,
  wrong-attestation, wrong-service, or wrong-order evidence is rejected even
  when the outer body is rehashed;
- separate binding vectors change each of `realized_admission_digest`,
  `realized_acl_digest`, `zero_live_writer_evidence_digest`,
  `drain_observation_generation`, and
  `service_disable_evidence_set_digest`, recompute the outer origin or adoption
  body digest, and prove activation still rejects the mismatch against the
  exact locked evidence; an implementation-native projection or equivalent
  caller testimony is never accepted;
- activation atomically binds one protected legacy-writer fence generation to
  admission; `J`, `P`, `R`, and `M` each lock and revalidate its live service,
  admission, ACL, and complete writer-set drain evidence, and restored login or
  connection admission, a write regrant, a newly live writer path, fence loss,
  or evidence-generation drift refuses every stage;
- privilege tests prove that only the dedicated fence-adapter capability can
  consume the authenticated invocation, lock and advance the fixed fence slot,
  revoke and revalidate admission, drain attributed writers, append fence
  progress, disable named services, and append exactly the generation-zero
  origin binding while atomically marking the fence active; it cannot read
  retained content, mutate target rows, adopt a later manifest, create
  successor authority, change a cohort, issue approval, regrant, re-enable, or
  bypass manifest and anti-replay checks, and neither an admission role nor
  ordinary runtime can invoke those operations;
- privilege tests separately prove that only the admission role can call
  `ADOPT_ACTIVE_FENCE`; that the interface performs no external fence or
  successor-authority effect; and that fence-adapter, closure, inventory,
  ordinary runtime, publication, mutation, and verification roles are denied;
- origin/adoption canonicalization vectors prove that the digest field is
  absent from its own preimage, generation zero has no prior digest, every
  positive generation names the exact prior body digest, changed realized
  fence evidence changes the body digest, skipped generations and cycles are
  rejected, and activation recomputes the complete protected chain and exact
  current record-to-manifest linkage;
- this record exposes no legacy-writer re-enable or successor-recutover
  operation; a future fence-removal design must durably fence the epoch and
  advance the fence generation before any external step, and must separately
  authorize legacy mutation and non-genesis recutover; crashes remain fenced,
  an old `R` cannot reach `M`, and direct bypass invalidates deployment
  attestation;
- the exact qualified clock envelope and post-revalidation monotonic sample
  produce a conservative `U_cutover`; deployment-attestation replacement,
  revocation, expiry, target/writer-set/adapter drift, clock rollback or loss,
  reboot or suspend uncertainty, envelope drift, excessive error, or
  `U_cutover >=` the deployment-attestation expiry, manifest expiry or freshness
  deadline, manifest approval expiry, or any exclusion-body or
  exclusion-approval expiry refuses activation;
- the combined activation transaction locks the exact deployment attestation,
  epoch proposal, and clock envelope before its final sample and holds them
  through synchronous commit, so concurrent attestation revocation or
  replacement serializes and refuses activation rather than racing the stored
  manifest/genesis/epoch triple;
- an omitted, implicit, or changed discovery-root set prevents activation;
- a safely readable, stable, current exact `UNKNOWN_ARTIFACT`,
  `INVALID_ARTIFACT`, or `KNOWN_UNDISPATCHABLE` entry maps to
  `EXCLUDED_OPAQUE` only under its exact valid exclusion; an indeterminate
  target additionally requires explicit conservative overlap with the
  manifest's target surface; the sole no-exclusion alternative is
  `PRESERVED_DISJOINT` for an after-dispatch invalid artifact whose selected
  frozen reader's exact failure evidence authenticates `DISJOINT`;
- every other unexcluded entry in that closed set and every drifted, unreadable,
  missing, or unresolved-dependency artifact blocks cutover;
- `ROLLBACK_UNAVAILABLE` and any missing preimage, decryption, or integrity
  dependency block activation and cannot be converted to an exclusion;
- one currently readable exact artifact can be excluded only by its separate
  exact approval for the manifest basis; the final body binds the exact
  exclusion body and digest plus canonical approval body and digest, while the
  outer envelope supplies that exclusion approval's authenticated channel
  receipt without putting a receipt into a hash or ordering cycle;
- approval records are accepted only through the existing authenticated
  operator-approval boundary; exact canonical bytes, receipt, principal,
  decision, domain, subject, target, surface, cutover, and validity must all
  match, and no new signing key or cryptographic authority exists;
- manifest-basis, final-manifest, artifact-exclusion, and approval bodies
  accept only their exact v1 kind and schema literals, and approval subject
  kind accepts only final manifest or artifact exclusion;
- every authenticated body rejects missing or extra keys, wrong scalar or
  union types, noncanonical UUIDs, out-of-range integers, ambiguous time units,
  and any absent, `null`, empty, or zero substitute for the literal `"NONE"`;
  each collection follows its declared stable identity and ordering, with
  duplicate identity/different bytes conflicting;
- every inventory classification accepts only its exact tagged-union field
  combination; a missing closure reference, reader, dependency role, raw
  identity, or failure evidence and every forbidden populated field fail;
  after-dispatch invalid artifacts represent both complete-dependency content
  rejection and incomplete dependency closure, and any no-reader, drifted, or
  unreadable observation that forges `EXACT_TARGET` or `DISJOINT` fails, as does
  an after-dispatch determinate scope not authenticated by the selected frozen
  reader's failure evidence; and
  authenticated and opaque dependency nodes are admitted only through exact
  parent dependency closures and never as discovery roots, standalone
  predecessors, or independently inferred artifacts;
- digest-subject tests construct each complete body under the normative value
  grammar, canonicalize it including the trailing LF, and prove that changing
  any key, nested value, collection member, type, or subject changes or rejects
  the exact digest; the verifier walks the exact transitive `EvidenceRef`
  closure from every root body, decodes each member only under its registered
  contract and version, and rejects nested missing nodes, wrong digests,
  unknown versions, cycles, duplicate-identity conflicts, and unreachable extra
  evidence before admission;
- canonicalization vectors cover quote, reverse solidus, every control escape,
  `/`, U+2028, U+2029, non-ASCII scalar text, UTF-16BE key ordering, safe-integer
  boundaries, and the trailing LF; equivalent JSON spellings such as `\/`,
  optional `\u` escapes, uppercase escape hex, whitespace, or another key order
  are rejected rather than hashed as alternate authority bytes;
- derived-digest tests independently alter each of `target_surface_digest`,
  `inventory_digest`, `reader_registry_digest`, `raw_identity_set_digest`,
  `historical_identity_set_digest`, and `reader_contract_set_digest` while
  keeping the containing body digest otherwise internally recomputed, and prove
  that pre-fence admission and activation reject every mismatch;
- reader-registry tests accept only the exact 40-hex pinned Git object ID and
  complete slash-bearing wire-contract IDs, reject a 64-hex SHA-256 value or
  split `EvidenceRef` substitute in either field, enumerate every closed tuple,
  and distinguish the shared schema-1 `legacy-requeue` and `post-abort`
  reference-plan variants; they also accept each exact schema-12
  `phase-repair-v8`/`phase-repair-v9`, schema-15
  `provider-capability`/`legacy-hatchery-capability`, and pending-marker
  `two-field` artifact variant with reference-plan `NONE`, and reject a missing,
  swapped, or invented variant; they separately expand every shared-lifecycle
  plan tuple before computing the registry digest and reject a missing,
  narrowed, reordered, or outer-schema-equals-reference-schema table;
- inventory-identity tests prove that an exact rescan of the same root,
  relative locator, and raw byte state derives the same
  `inventory_observation_id` and projection-set digests despite new descriptor
  and scan evidence, while a changed root contract, locator, byte state,
  length, or raw digest derives another identity; byte-identical closure
  candidates at two locators derive distinct source identities and independent
  inventory members and case keys, no deduplication removes either member from
  the inventory digest, and a refreshed manifest may reuse only the exact
  matching source-instance case;
- closure-specific drift tests change one source raw identity, historical
  identity, reader contract, reader-registry member, or dependency-closure
  member after case creation and prove that case linkage, pre-fence admission,
  and activation reject the old observation even when its outer body digest is
  internally recomputed;
- a manifest approval cannot authorize an exclusion, an exclusion approval
  cannot authorize a manifest, and neither can be replayed across a different
  subject, target surface, or cutover;
- wildcard, directory, schema-range, stale-byte, moved-artifact,
  missing-artifact, and remembered-digest exclusions are rejected;
- an after-dispatch invalid artifact with exact selected-reader failure evidence
  authenticating `DISJOINT` maps to nonauthorizing `PRESERVED_DISJOINT` and does
  not block the wrong target, while the same artifact with `EXACT_TARGET` or
  `INDETERMINATE` blocks without its exact target-scoped or
  conservative-overlap exclusion; `UNKNOWN_ARTIFACT` and
  `KNOWN_UNDISPATCHABLE` cannot manufacture a disjoint disposition;
- an exclusion of one inventory entry cannot cover its missing or unreadable
  dependency, a sibling entry, or bytes that drift during revalidation;
- an exclusion cannot authorize mutation, establish an outcome, satisfy
  rollback admission, or permit deletion;
- apparent process absence cannot substitute for an enforceable old-writer
  fence;
- one target's manifest or exclusion cannot admit another target; and
- the approved final-manifest body binds `NONE` or one exact
  `LEGACY_COMPLETE_APPLY` predecessor and attempts to add, remove, replace, or
  duplicate that selection conflict before activation or rollback `J`.

### Legacy predecessor rollback

- only a complete apply with matching historical verification can be selected
  as `LEGACY_COMPLETE_APPLY`, and its selected inventory identity, exact target
  surface digest, frozen reader output, and `target_overlap=EXACT_TARGET` must
  all name this manifest surface; a disjoint, indeterminate, or conservatively
  overlapping complete apply is rejected at activation and rollback `J`;
- ambiguous siblings, prior rollback, target drift, cohort drift, unavailable
  preimage, invalid integrity, fence loss, activated-manifest mismatch, bound
  evidence drift, or non-genesis successor lineage prevents rollback `J`;
- the new approval binds every legacy, manifest, preimage, target, cohort,
  genesis, epoch, grant, deadline, and budget input;
- rollback `J` atomically ingests only the exact encrypted action preimage
  after independently reconstructing the authenticated predecessor closure and
  recomputing its raw, historical, and reader-contract set digests and frozen
  chain root; it creates no general byte capsule or synthetic predecessor `M`
  or `V`;
- interruption before and after each new `J`, `P`, `R`, `M`, and `V`
  commit follows the restart matrix;
- lost acknowledgement never repeats a committed stage or target mutation;
- exactly one rollback restores the preimage and advances generation once;
- two rollbacks racing from genesis yield one `M` and one terminal
  `LINEAGE_HEAD_DRIFT`;
- any successor `M` permanently closes legacy predecessor admission, even if
  later target bytes coincidentally match the historical postimage; and
- preserved completed, failed, and out-of-cohort rows remain unchanged.

### Preservation and authority separation

- compatibility writes no historical file and preserves every source byte and
  frozen reader contract;
- PostgreSQL contains no general historical byte capsule;
- only closure metadata, cutover and exclusion metadata, successor state, and
  an approved rollback's action-scoped encrypted preimage are added;
- deletion or loss of a reproducible export cannot change successor authority;
- deletion, corruption, or unreadability of required legacy evidence fails
  closed rather than being inferred around; and
- database privileges mechanically prevent evidence, admission, publication,
  mutation, and verification roles from crossing their defined boundaries.

The test oracle must assert exact artifact and reader identities, legacy
disposition, manifest and exclusion bindings, successor lineage and durable
prefix, target generation and postimage, immutable evidence set, authority
classification, and exact permitted next action. A successful command exit,
present file, matching PID state, or matching target rows alone is not
acceptance evidence.

## Non-goals and implementation boundary

This record does not:

- select SQL relation, column, index, CLI, or package names;
- implement readers, schemas, protected interfaces, roles, or migration code;
- define a general PostgreSQL archive or byte-capsule format;
- repair, normalize, rewrite, or backfill a historical artifact;
- make closure evidence eligible for rollback;
- allow a missing or unreadable artifact to be excluded;
- support rollback from a legacy predecessor after successor genesis advances;
- define permanent preimage retirement or destruction proof;
- expand the local PostgreSQL durability and clock support matrix;
- deploy or activate a cutover;
- alter a live grant, claim, cohort, row, worker, provider, or recovery state;
  or
- qualify a candidate or authorize live recovery.

Implementation sequencing must retain the frozen legacy readers before any
writer cutover, establish role and service fencing before manifest activation,
and make successor mutation impossible until its protected PostgreSQL
authority is complete. Those sequencing details require a separately accepted
implementation plan after the remaining design gates.

## Related records and remaining gates

- [#73](https://github.com/nisavid/agents/issues/73) selects target PostgreSQL
  as the successor publication owner and records the
  [publication design](journal-publication-design.md).
- [#74](https://github.com/nisavid/agents/issues/74) fixes interrupted
  publication, lineage, restart, rollback, and preservation behavior in the
  [restart design](journal-restart-design.md).
- [#75](https://github.com/nisavid/agents/issues/75) selects the compatibility
  and cutover contract recorded here.
- [#76](https://github.com/nisavid/agents/issues/76) owns falsifiable design and
  implementation evidence obligations.
- [#77](https://github.com/nisavid/agents/issues/77) owns independent assessment
  of the integrated design.
- [#78](https://github.com/nisavid/agents/issues/78) owns Ivan's final design
  acceptance and the gate to a separate implementation-planning map.

Only after those gates are satisfied may a separately authorized effort
translate this record into successor schemas, protected interfaces, frozen
reader packaging, deployment admission, cutover sequencing, tests, candidate
assembly, and live recovery procedures.
