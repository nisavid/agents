# Hindsight Journal Compatibility and Cutover Design

Status: historical compatibility and cutover behavior selected in
[#75](https://github.com/nisavid/agents/issues/75). Ivan approved the integrated
design on 2026-09-01. The PostgreSQL publication architecture selected in
[#73](https://github.com/nisavid/agents/issues/73) and the interrupted
publication and restart contract selected in
[#74](https://github.com/nisavid/agents/issues/74) remain fixed. The accepted
evidence bar is recorded in
[`journal-acceptance-evidence.md`](journal-acceptance-evidence.md) through
[#76](https://github.com/nisavid/agents/issues/76). Independent assessment and
final design acceptance remain open in
[#77](https://github.com/nisavid/agents/issues/77) and
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
new successor rollback may create and verify that chain's exact encrypted
preimage binding and digest-and-length-keyed protected PostgreSQL ciphertext
row as nonauthorizing input before its separate plan and approval; the matching
`J` then atomically adopts both before proceeding through ordinary successor
`J -> P -> R -> M -> V`. The bridge creates no
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
  protocol_version,
  artifact_kind,
  authenticated_dependency_role,
  artifact_schema_version,
  reference_plan_schema_version,
  artifact_or_reference_plan_variant,
  wire_canonicalization_contract
)
```

This tuple is exactly `ReaderSelector`; dispatch, `reader_contract_id`, member
ordering, the member-vector digest, and the registry-body digest all derive
from that one complete selector projection. Every component must match a
registered reader exactly. Dispatch has no
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

The registry and its member vector are exact. Each fully expanded table tuple
is projected as the normative `ReaderRegistryMember` below, including source
revision, protocol family and version, exact kind or authenticated dependency
role, artifact and reference-plan schema, variant, wire contract, and derived
reader contract ID. The eight fields in `ReaderSelector` are its complete
selector identity.

For selector `S`, canonicalize the complete `ReaderSelector` under the
compatibility contract with one LF and compute `d = SHA-256(bytes(S))`.
`reader_contract_id` is exactly
`hindsight-private-file-operation-recovery-reader/v1/sha256/` followed by the
64 lowercase hexadecimal characters of `d`. There is no separately chosen
reader name or reference. This function is the complete selector-to-reader
contract mapping: two equal selectors have one contract ID, and any different
selector has a different required preimage and ID. The frozen implementation
dispatch table must expose exactly that ID at the pinned source revision and
must implement the selected input grammar and the registered success/failure
output grammars; sharing executable code between IDs does not merge them.

The kindless requeue plan is itself this exact authenticated-dependency member:

| Member field | Exact value |
| --- | --- |
| `protocol_family` | `hindsight-private-file-operation-recovery` |
| `protocol_version` | `1` |
| `artifact_kind` | `NONE` |
| `authenticated_dependency_role` | `requeue-plan` |
| `artifact_schema_version` | `1` |
| `reference_plan_schema_version` | `NONE` |
| `artifact_or_reference_plan_variant` | `NONE` |
| `wire_canonicalization_contract` | `hindsight-operation-recovery-canonical-json-lf-sha256/7b165b3` |
| `reader_contract_id` | `hindsight-private-file-operation-recovery-reader/v1/sha256/3f2089bacd91e4591d7a5939cc274d7ca7ae6600466718504b1a6c5102b58245` |
| `source_revision` | `7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab` |

Its contract ID is SHA-256 over the complete compatibility-canonical
`ReaderSelector` bytes, including the LF, under the derivation above. The
member accepts only a kindless schema-1 object with the frozen key set and
literal `action="requeue-operation-cohort"`, and only when its parent reader
has authenticated the exact `requeue-plan` edge. A discovery root, another
dependency role, a kindful copy, or a changed action is not this member.

Encode every complete member independently under the compatibility contract,
including its LF, reject duplicate selector identities and same-selector
different bytes, and sort by unsigned lexicographic member bytes.
`member_vector_digest` is SHA-256 over their separator-free concatenation.
`FrozenReaderRegistry/v1.members` is exactly that sequence, including the one
kindless requeue-plan member above, and carries the computed member count,
vector digest, and literal source revision.
`reader_registry_digest` is SHA-256 over the complete canonical registry body,
including its LF. A range expression, reordered or omitted tuple, wrong
member count, contract ID, changed wire contract, vector mismatch, or
equivalent but differently projected implementation table has a different or
invalid digest.
The acceptance contract's `HistoricalCorpusPlan.historical_registry_digest`
is this registry-body digest and its `historical_registry_vector_digest` is the
member-vector digest, not a second implementation-selected domain or a label
for a listed subset. Corpus-plan acceptance, campaign registration, every
historical run registration, and tier evaluation independently expand every
table below, derive every contract ID, reconstruct the registry body, and
require both values. Zero, stale, narrowed, reordered, differently expanded,
or successor-canonicalized values are invalid.

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
own exact frozen reader. This includes dispatching a shared-lifecycle
`requeue-plan` edge through the exact authenticated-dependency member above
before any shared receipt, journal, bundle, or verification result can pass.
A final journal that names an unavailable, unregistered, unrecognized,
malformed, role-mismatched, or digest-mismatched dependency is not partially
accepted.

The historical two-field pending marker has exactly `kind` and
`schema_version` and a dedicated `PENDING_UNAUTHENTICATED` classifier. Its
presence cannot be interpreted as final journal durability, proof durability,
receipt durability, mutation time, commit time, or successor deadline
evidence. It is never passed to a final-journal reader after a parse failure.

### Reader output

A selected reader returns exactly one registered body. Success is
`LegacyReaderSuccess/v1`; post-dispatch failure is
`LegacyReaderFailure/v1`. Neither contract has an extension field, optional
diagnostic bag, or partial form.

The success body carries the complete raw identity, historical identity,
selected registry member and its digest, deterministic reader contract ID,
exact successor `HistoricalReaderExecutionBinding/v1`, target surface, action,
disposition, dependency identities, optional exact `LegacyRestoreContent/v1`,
literal `authority=NONE`, and the complete permitted action sequence.
Permitted actions use the displayed enum order with no
duplicates and are derived from the disposition: `PRESERVE` is always first;
`INSPECT` follows for authenticated readable evidence; `OBSERVE_CLOSURE`
appears only for `CLOSURE_CANDIDATE`; and
`PREPARE_SEPARATELY_APPROVED_ROLLBACK` appears only for `COMPLETE_APPLY` with
the exact restore content. No other sequence is valid. The protected successor
publication and mutation interfaces do not accept a reader success as proof of
`J`, `P`, `R`, `M`, or `V`.

`LegacyRestoreContent/v1` is the reader's typed semantic projection of the
historical selected-row plaintext. It uses the complete target-surface
relation order, numeric column order, canonical row-identity byte order, and
the closed null, boolean, signed-integer, UTF-8 text, bytea, and UTC-instant
value encodings in the grammar. Each relation, column, row, and key digest is
recomputed from its complete standalone compatibility-canonical body including
the LF. The body contains every selected row exactly once and no preserved
row. The successor `FIELDWISE_TARGET_RESTORE_V1` conversion resolves this
exact body; it never converts the historical plaintext wire bytes directly.

The failure body carries the same selected member, contract ID, and exact
successor reader-execution binding, exact raw identity, one closed failure
category, typed failure evidence, target-overlap result, literal
`authority=NONE`, and the exact empty permitted-action array.
It has no historical identity, disposition, dependency-success projection, or
restore content. A pre-dispatch unreadable, drifted, unknown, or unselectable
artifact has no selected reader and therefore no reader-output body; its
closed inventory failure remains the separately typed failure evidence already
required by the classification matrix.

## Historical disposition matrix

The complete decoded legacy chain and live target observation determine one
of these dispositions:

| Legacy condition | Disposition | Permitted treatment |
| --- | --- | --- |
| Complete authenticated apply chain with matching historical verification | `COMPLETE_APPLY` | Preserve and inspect. It may be selected by one cutover manifest as `LEGACY_COMPLETE_APPLY` for the narrow genesis-only rollback bridge. |
| Complete authenticated rollback chain with matching historical verification | `COMPLETE_ROLLBACK` | Preserve and inspect as terminal historical evidence. It cannot become a successor predecessor. |
| Historical pending marker only | `PENDING_UNAUTHENTICATED` | Freeze as nonauthorizing. A new successor plan, approval, and authorization receipt are required. |
| Authenticated journal exists and exact target mutation is absent | `FROZEN_TARGET_ABSENT` | Freeze as nonauthorizing. Never resume the historical mutation. |
| Target mutation is present but the historical receipt or verification chain is incomplete | `CLOSURE_CANDIDATE` | Perform evidence-only exact target comparison. Never call a legacy mutation routine merely to check. |
| Closure comparison proves the exact expected postimage | `CLOSURE_MATCH` | Append a nonauthorizing closure observation and preserve the historical chain. It cannot qualify for the rollback bridge. |
| Closure comparison proves a different postimage | `CLOSURE_MISMATCH` | Append sticky mismatch evidence and block ordinary cutover for the affected surface pending separately approved remediation. |
| Closure comparison cannot reach a conclusion below its case ceiling | `CLOSURE_UNABLE_TO_VERIFY` | Retain retryable evidence-only status. It creates no authority and does not satisfy cutover. |
| The last allowed comparison is unable to conclude or is durably abandoned | `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY` | Record terminal remediation-only exhaustion. It creates no authority, admits no further attempt, and does not satisfy cutover. |
| Exact encrypted restore source, required decryption capability, registered typed source, deterministic conversion, or restore-payload recomputation is missing | `ROLLBACK_UNAVAILABLE` | Report rollback unavailable. Never synthesize restore content from target rows. |
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
  observed_generation,
  closure_policy_limits
)
```

The source observation identity is the deterministic locator-and-raw-state
identity defined below. It distinguishes byte-identical chains discovered at
different locators while remaining stable for an exact rescan of the same
source instance. The case binds that exact source, expected postimage, target
and cohort identities, one exact current deployment attestation, a finite case
expiry no later than that attestation's expiry, and an initially empty
terminal-outcome slot. It also binds the deployment attestation's exact typed
`ClosurePolicyLimits/v1` reference and copies that body's positive maximum
attempt count; case, reservation, post-expiry resolution, observation-call,
and observer-lease durations; lock, statement, whole-transaction, and
idle-in-transaction timeouts; and adapter-enforced connection lifetime. Every
copied value must equal the referenced body. Zero, infinity, omission, and a database zero value
that disables a timeout are invalid. The stable case key permits only one such
binding for the source, surface, observed generation, and closure-policy
identity. Each
immutable observation instead has key:

```text
(closure_case_digest, attempt_ordinal)
unique (closure_case_digest, observation_request_id)
```

Every closure reference below to a current deployment attestation also locks
and revalidates its typed deployment-policy reference against the protected
current policy, its complete deployment-tier result partition against the
protected current `PASS` partition, and its qualification receipt's complete
design-, implementation-, and release-tier partitions against their protected
current `PASS` pointers. It resolves every tier result's complete ordered
prerequisite-result references and requires each to remain the protected
current `PASS` result for that claim and prerequisite tier. It also resolves
the support profile, qualification plan and receipt, and exact closure-policy
body; requires all four closure-policy references and every copied case value
to match; and requires its controller-host, PostgreSQL-host,
PostgreSQL-endpoint, and topology references to equal the attestation and
protected live deployment bindings. `OPEN`, `FAIL`, `STALE`,
`PREREQUISITE_BLOCKED`, superseded, invalidated, omitted, extra, duplicated,
reordered, policy-noncurrent, remote, managed, or endpoint-drifted evidence
makes the case nonconsumable and applies the remediation-blocker behavior
below.

The verifier also resolves the attestation's complete deciding
`DeploymentEvidenceAcquisition/v1` sequence and checks every acquisition,
projection, procedure, run, oracle, clock-envelope, and boot-identity equality
used by the admission finalizer. A completion, registration, aggregation,
signature, or later retry cannot replace an acquisition reference or move its
protected lower bound.

`attempt_ordinal` is the next positive integer allocated by a protected
reservation transaction while it locks the case; callers cannot choose or skip
an ordinal. Callers likewise cannot supply, extend, or renew a reservation
deadline. While holding the case and qualified-clock rows, the server samples
the exact case-bound deployment-attestation row, proves that it is current,
unrevoked, unexpired, and unchanged in target, adapter, and required guard
capabilities, and then samples the qualified monotonic clock. It derives the
deadline by the exact checked arithmetic below as the earliest of the
reservation-duration, protected observation-call, connection-lifetime,
clock-validity, case-expiry, and deployment-attestation-expiry candidates. The
last three candidates leave the complete policy-bound maximum resolution
duration. A sample at or after any bound, insufficient
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
and statement timeouts to the lesser of the exact closure-policy values and
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
adapter forcibly closes the exact tagged observer session and proves that a
fresh qualified sample's lower bound is at or after the old reservation
deadline. It derives a new positive
server-owned resolution deadline from the exact policy operands below, capped
by `maximum_resolution_duration_ms`, `observation_call_timeout_ms`,
`connection_lifetime_ms`, case expiry, exact deployment-attestation expiry,
and clock-envelope validity, and enforces the same lock, statement,
whole-transaction, idle-in-transaction, and connection-termination guards
against that new bound. It can only fence the old lease generation and append a
nonauthorizing abandonment result; it cannot observe the target or finalize a
successful result. If one bounded resolution attempt times out, the tagged
session is closed and a later protected resolver may exact-retry the same
abandonment transition; it never revives the old observer.

An authenticated observer-incarnation invalidation before reservation expiry
can only authorize same-ordinal takeover. The takeover transaction requires a
fresh qualified sample strictly before the reservation deadline, locks the
case and current lease, authenticates the invalidation against that lease's
generation, tagged session, and incarnation, advances the lease generation,
and installs the replacement claim on the same reservation and ordinal. It
cannot append abandonment or exhaustion. Only after the qualified lower bound
proves reservation expiry does the deadline-qualified abandonment branch above
apply; invalidation never
substitutes for proof of expiry.

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
reservation and ordinal; it never allocates another attempt. Pre-expiry
invalidation always takes this path. A resolver may record abandonment only
after a qualified sample's lower bound proves reservation expiry and it
atomically fences the prior generation, so a
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
  observation identity, exactly bound to its protected timing evidence; and
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
the exact current deployment attestation, its support profile, qualification
plan and receipt, exact `ClosurePolicyLimits/v1`, and qualified clock envelope,
it requires the reference chain and every copied policy value to match. It
records the exact creation sample and trusted upper bound and derives—not
accepts from the caller—the nanosecond case expiry by the formula below as the
earlier of the attestation expiry and that upper bound plus the policy's checked
case lifetime. Before occupying the stable key, a qualified sample and
conservative error model must prove enough remaining time for at least one
complete maximum reservation duration, the complete maximum resolution
duration, and the required clock-error margin. Every finite transaction and
connection guard is then capped to the applicable derived deadline. Case
creation fails on an unreadable member, registry drift, projection mismatch, a caller-chosen
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
Only after a fresh qualified sample's lower bound is at or beyond the
reservation deadline does the protected resolver use the separate bounded
resolution protocol,
locks the case and reservation after the tagged observer session is gone,
advances the observer-lease generation to fence every prior token, and only then
finalizes it as `ABANDONED_UNABLE_TO_VERIFY` with
`observed_postimage=NONE`. If that
ordinal reaches the ceiling, the case becomes
`CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY`; otherwise the next protected reservation
may allocate the next ordinal. Reusing a request identity or ordinal with a
different binding is `CONFLICT`; no observation is converted in place or given
a newly chosen outcome after lost acknowledgement.
Before that lower-bound proof, an expired lease or proof that the claimed
observer incarnation cannot continue permits only the same-ordinal takeover
protocol; it never permits abandonment, exhaustion, or allocation of a new
ordinal.

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
realized-fence-evidence, persistent legacy-fence evidence, origin-fence
binding, and active-fence adoption bodies all use
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
| Frozen reader registry | `hindsight-compatibility-frozen-reader-registry` | `1` |
| Legacy reader success | `hindsight-compatibility-legacy-reader-success` | `1` |
| Legacy reader failure | `hindsight-compatibility-legacy-reader-failure` | `1` |
| Legacy restore content | `hindsight-compatibility-legacy-restore-content` | `1` |
| Final manifest | `hindsight-compatibility-final-manifest` | `1` |
| Artifact exclusion | `hindsight-compatibility-artifact-exclusion` | `1` |
| Approval | `hindsight-compatibility-approval` | `1` |
| Closure case binding | `hindsight-compatibility-closure-case-binding` | `1` |
| Closure observation | `hindsight-compatibility-closure-observation` | `1` |
| Closure qualified-sample evidence | `hindsight-compatibility-closure-qualified-sample-evidence` | `1` |
| Closure attested-invalidation evidence | `hindsight-compatibility-closure-attested-invalidation-evidence` | `1` |
| Closure comparison evidence | `hindsight-compatibility-closure-comparison-evidence` | `1` |
| Closure failure evidence | `hindsight-compatibility-closure-failure-evidence` | `1` |
| Realized admission evidence | `hindsight-compatibility-realized-admission-evidence` | `1` |
| Realized ACL evidence | `hindsight-compatibility-realized-acl-evidence` | `1` |
| Zero-live-writer evidence | `hindsight-compatibility-zero-live-writer-evidence` | `1` |
| Service-disable evidence | `hindsight-compatibility-service-disable-evidence` | `1` |
| Persistent legacy-fence evidence | `hindsight-compatibility-persistent-legacy-fence-evidence` | `1` |
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
denote JSON arrays ordered by the contracts above, `|` denotes an exact value
union, and `&` merges the disjoint keys of two displayed object fragments into
one flat exact object. These marks are schema notation and never appear in
encoded JSON.

The scalar types are:

- `SafeInteger`: a JSON integer from 0 through `9007199254740991`;
- `TargetGeneration`: exactly the same JSON type and range as the successor
  acceptance contract's `TargetGeneration`; it admits no quoted number,
  fraction, exponent, sign, leading zero, negative value, overflow, alias, or
  coercion;
- `PositiveSafeInteger`: a JSON integer from 1 through `9007199254740991`;
- `Digest`: exactly 64 lowercase hexadecimal characters naming SHA-256;
- `GitObjectId`: exactly 40 lowercase hexadecimal characters naming a Git
  SHA-1 object in this v1 registry;
- `Id`: a lowercase canonical UUID string;
- `DecimalString`: `0` or a nonzero ASCII decimal digit followed by zero or
  more ASCII decimal digits;
- `SignedInt64String`: the shortest ASCII decimal spelling from
  `-9223372036854775808` through `9223372036854775807`, with `0` as the only
  zero spelling and no plus sign or leading zero;
- `UInt128String`: the shortest ASCII decimal string for a value from `0`
  through `340282366920938463463374607431768211455`;
- `Token`: a nonempty ASCII string matching
  `[A-Za-z0-9][A-Za-z0-9._:-]{0,255}`;
- `ContractId`: a nonempty ASCII string matching
  `[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}`;
- `Text`: a JSON string of Unicode scalar values with no NUL or unpaired
  surrogate; v1 performs no Unicode normalization, so distinct scalar
  sequences remain distinct values;
- `DatabaseName`: the shared successor/compatibility database-name domain: a
  nonempty `Text` occupying at most 4,096 UTF-8 bytes; and
- `Base64Url`: canonical unpadded RFC 4648 base64url text using only ASCII
  letters, digits, `-`, and `_`, with zero unused tail bits; where an adjacent
  byte length exists, the decoded length must equal it and the string is empty
  if and only if that length is zero.

Manifest, exclusion, and approval creation, issue, observation,
freshness, and expiry fields end in `_unix_ns` and use checked
`UInt128String` nanoseconds. Their protected authority sample derives
conservative `U` by the successor clock contract and requires `U` strictly
below every applicable deadline; equality is late. Issue or creation time must
also be strictly below the matching expiry. No whole-second projection,
rounding, saturation, or unchecked conversion is admitted for an authority
deadline. `ClosureCaseBinding.created_at_unix_ns` and `.expires_at_unix_ns`
use the same exact `UInt128String` nanosecond domain; they are not rounded to
whole seconds or interchangeable with a seconds-valued timestamp.

Reusable exact objects are:

```text
EvidenceRef := {
  "contract_kind": Token,
  "contract_version": SafeInteger,
  "body_digest": Digest
}

LegacyReaderSuccessEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-compatibility-legacy-reader-success",
  "contract_version": 1
}

LegacyReaderFailureEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-compatibility-legacy-reader-failure",
  "contract_version": 1
}

LegacyReaderOutputEvidenceRef := LegacyReaderSuccessEvidenceRef |
                                 LegacyReaderFailureEvidenceRef

HistoricalReaderExecutionBindingEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-postgresql-historical-reader-execution-binding",
  "contract_version": 1
}

LegacyRestoreContentEvidenceRef := {
  "body_digest": Digest,
  "contract_kind": "hindsight-compatibility-legacy-restore-content",
  "contract_version": 1
}

TargetDatabaseIdentity := {
  "postgres_system_identifier": DecimalString,
  "database_oid": SafeInteger,
  "database_name": DatabaseName
}

RelationIdentity := {
  "relation_oid": SafeInteger,
  "schema_name": Text,
  "relation_name": Text,
  "relkind": "r" | "p"
}

CompatibilityTargetColumnIdentity := {
  "attnum": PositiveSafeInteger,
  "collation_oid": SafeInteger,
  "column_name": Text,
  "nullable": false | true,
  "postgresql_type_oid": SafeInteger,
  "postgresql_typmod": SignedInt64String,
  "postgresql_value_type": "BOOLEAN" | "SIGNED_INT64" | "UTF8_TEXT" |
                           "BYTEA" | "UTC_INSTANT_MICROSECONDS_2000",
  "relation_identity_digest": Digest
}

TargetSurfaceRelation := {
  "columns": sequence<CompatibilityTargetColumnIdentity>,
  "key_column_identity_digests": sequence<Digest>,
  "relation_identity": RelationIdentity,
  "relation_identity_digest": Digest
}

TargetSurface := {
  "relations": set<TargetSurfaceRelation>,
  "target_surface_digest": Digest
}

CanonicalLineageKeyBody := {
  "protocol_family": "hindsight-postgresql-publication",
  "protocol_version": 1,
  "target_database_identity": EvidenceRef,
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
  "reader_contract_id": ContractId,
  "source_revision": GitObjectId
}

ReaderSelector := {
  "artifact_kind": Token | "NONE",
  "authenticated_dependency_role": Token | "NONE",
  "artifact_schema_version": PositiveSafeInteger,
  "reference_plan_schema_version": PositiveSafeInteger | "NONE",
  "artifact_or_reference_plan_variant": Token | "NONE",
  "protocol_family": "hindsight-private-file-operation-recovery",
  "protocol_version": 1,
  "wire_canonicalization_contract": ContractId
}

FrozenReaderRegistry := {
  "kind": "hindsight-compatibility-frozen-reader-registry",
  "member_count": PositiveSafeInteger,
  "member_vector_digest": Digest,
  "members": set<ReaderRegistryMember>,
  "schema_version": 1,
  "source_revision": "7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab"
}

LegacyRawIdentity := {
  "byte_length": SafeInteger,
  "source_sha256": Digest
}

LegacyHistoricalIdentity := {
  "artifact_kind": Token | "NONE",
  "artifact_or_reference_plan_variant": Token | "NONE",
  "artifact_schema_version": PositiveSafeInteger,
  "historical_digests": set<DigestBinding>,
  "wire_canonicalization_contract": ContractId
}

LegacyDependencyIdentity := {
  "authenticated_dependency_role": Token,
  "historical_identity": LegacyHistoricalIdentity | "NONE",
  "inventory_observation_id": Id,
  "raw_identity": LegacyRawIdentity
}

LegacyRestoreNullValue := {
  "value_kind": "NULL"
}

LegacyRestoreBooleanValue := {
  "value": false | true,
  "value_kind": "BOOLEAN"
}

LegacyRestoreSignedInt64Value := {
  "value": SignedInt64String,
  "value_kind": "SIGNED_INT64"
}

LegacyRestoreTextValue := {
  "value": Text,
  "value_kind": "UTF8_TEXT"
}

LegacyRestoreByteaValue := {
  "byte_length": SafeInteger,
  "value_base64url": Base64Url,
  "value_kind": "BYTEA"
}

LegacyRestoreUtcInstantValue := {
  "value": SignedInt64String,
  "value_kind": "UTC_INSTANT_MICROSECONDS_2000"
}

LegacyRestoreValue := LegacyRestoreNullValue |
                      LegacyRestoreBooleanValue |
                      LegacyRestoreSignedInt64Value |
                      LegacyRestoreTextValue |
                      LegacyRestoreByteaValue |
                      LegacyRestoreUtcInstantValue

LegacyRestoreKeyColumnValue := {
  "column_identity": CompatibilityTargetColumnIdentity,
  "column_identity_digest": Digest,
  "value": LegacyRestoreBooleanValue |
           LegacyRestoreSignedInt64Value |
           LegacyRestoreTextValue |
           LegacyRestoreByteaValue |
           LegacyRestoreUtcInstantValue
}

LegacyRestoreRowIdentity := {
  "key_columns": sequence<LegacyRestoreKeyColumnValue>,
  "relation_identity_digest": Digest
}

LegacyRestoreColumnProjection := {
  "column_identity": CompatibilityTargetColumnIdentity,
  "column_identity_digest": Digest,
  "value": LegacyRestoreValue
}

LegacyRestoreRowProjection := {
  "columns": sequence<LegacyRestoreColumnProjection>,
  "row_identity": LegacyRestoreRowIdentity,
  "row_identity_digest": Digest
}

LegacyRestoreRelationProjection := {
  "relation_identity": RelationIdentity,
  "relation_identity_digest": Digest,
  "rows": sequence<LegacyRestoreRowProjection>
}

LegacyRestoreContent := {
  "kind": "hindsight-compatibility-legacy-restore-content",
  "lineage_key_digest": Digest,
  "relations": sequence<LegacyRestoreRelationProjection>,
  "schema_version": 1,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest
}

HistoricalClassification := "COMPLETE_APPLY" | "COMPLETE_ROLLBACK" |
                            "PENDING_UNAUTHENTICATED" |
                            "FROZEN_TARGET_ABSENT" | "CLOSURE_CANDIDATE" |
                            "CLOSURE_MATCH" | "CLOSURE_MISMATCH" |
                            "CLOSURE_UNABLE_TO_VERIFY" |
                            "CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY" |
                            "ROLLBACK_UNAVAILABLE" |
                            "AUTHENTICATED_DEPENDENCY" |
                            "OPAQUE_DEPENDENCY" | "UNKNOWN_ARTIFACT" |
                            "INVALID_ARTIFACT" | "KNOWN_UNDISPATCHABLE" |
                            "DRIFTED_ARTIFACT" | "UNREADABLE_ARTIFACT"

LegacyReaderSuccess := {
  "authority": "NONE",
  "dependency_identities": set<LegacyDependencyIdentity>,
  "historical_identity": LegacyHistoricalIdentity,
  "kind": "hindsight-compatibility-legacy-reader-success",
  "legacy_action": "APPLY" | "ROLLBACK" | "PENDING" |
                   "AUTHENTICATED_DEPENDENCY",
  "legacy_disposition": HistoricalClassification,
  "permitted_actions": sequence<"PRESERVE" | "INSPECT" |
                       "OBSERVE_CLOSURE" |
                       "PREPARE_SEPARATELY_APPROVED_ROLLBACK">,
  "raw_identity": LegacyRawIdentity,
  "reader_contract_id": ContractId,
  "reader_execution_binding": HistoricalReaderExecutionBindingEvidenceRef,
  "reader_registry_member": ReaderRegistryMember,
  "reader_registry_member_digest": Digest,
  "restore_content": LegacyRestoreContentEvidenceRef | "NONE",
  "schema_version": 1,
  "target_surface": TargetSurface
}

LegacyReaderFailure := {
  "authority": "NONE",
  "failure_category": "CANONICALIZATION_MISMATCH" |
                      "DEPENDENCY_CYCLE" | "DEPENDENCY_DIGEST_MISMATCH" |
                      "DEPENDENCY_MISSING" | "DEPENDENCY_ROLE_MISMATCH" |
                      "HISTORICAL_DIGEST_MISMATCH" | "MALFORMED_BYTES" |
                      "REFERENCE_PLAN_UNSUPPORTED" |
                      "REQUIRED_FIELD_INVALID" | "TARGET_OVERLAP_UNPROVEN",
  "failure_evidence": EvidenceRef,
  "kind": "hindsight-compatibility-legacy-reader-failure",
  "permitted_actions": [],
  "raw_identity": LegacyRawIdentity,
  "reader_contract_id": ContractId,
  "reader_execution_binding": HistoricalReaderExecutionBindingEvidenceRef,
  "reader_registry_member": ReaderRegistryMember,
  "reader_registry_member_digest": Digest,
  "schema_version": 1,
  "target_overlap": "EXACT_TARGET" | "DISJOINT" | "INDETERMINATE"
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
`FrozenReaderRegistry.member_count` is exactly the length of `members` after
complete table expansion, duplicate rejection, and canonical member sorting.
The kindless `requeue-plan` member contributes one to that count and to the
member-vector preimage. Omitting it, counting a range expression, or retaining
a duplicate makes the count, vector digest, and registry digest invalid.
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
independently compatibility-canonicalized `TargetSurfaceRelation` member bytes
in set order; the digest field itself is not an input. Each member contains the
complete relation identity, ordered column identities, and ordered key-column
identity digests. Relation identity digests are over standalone
`RelationIdentity` bytes including the LF; column identity digests are over
standalone `CompatibilityTargetColumnIdentity` bytes including the LF.
Relations use member-byte order, columns use increasing numeric `attnum`, and
key-column digests use their columns' increasing numeric `attnum`. Duplicate or
missing relations, columns, keys, names, or ordinals are invalid.
Relation and schema names are nonempty exact server-returned scalar sequences;
v1 admits only ordinary and partitioned tables with PostgreSQL `relkind`
`"r"` and `"p"`, respectively.

`TargetDatabaseIdentity` is the shared target body at the compatibility
boundary. A target that can enter successor activation must encode
`postgres_system_identifier` within the successor UInt128 range. The successor
`TARGET_DATABASE` `EvidenceIdentity/v1` must have `release_digest="NONE"` and
an `APPLICATION_JSON` descriptor containing exactly these three fields under
the successor canonical-byte contract, including its LF. Resolving that
descriptor and projecting its fields is the acceptance contract's bijective
`compatibility_target_identity` mapping. Both sides use the same nonempty,
at-most-4,096-byte UTF-8 `DatabaseName` domain, so every admitted triple has
exactly one encoding on each side. A database name, OID, system
identifier, descriptor digest, or identity digest alone cannot bridge the two
contracts.

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
  "reader_contract": ContractId | "NONE",
  "reader_output": LegacyReaderOutputEvidenceRef | "NONE",
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
| `INVALID_ARTIFACT` after dispatch | `READABLE_STABLE` / R | `DISPATCH_FAILED` | K or D | R / R | E | R | `COMPLETE` or `INCOMPLETE` / R | N | F |
| `KNOWN_UNDISPATCHABLE` | `READABLE_STABLE` / R | `UNRECOGNIZED` | kind and role `"NONE"`; schema `SafeInteger` or `"NONE"` | N / N | E | R | `NOT_APPLICABLE` / N | N | I |
| `DRIFTED_ARTIFACT` | `DRIFTED` / R | `NOT_APPLICABLE` | N | N / N | E | R | `NOT_APPLICABLE` / N | N | I |
| `UNREADABLE_ARTIFACT` | `UNREADABLE` / N | `NOT_APPLICABLE` | N | N / N | E | R | `NOT_APPLICABLE` / N | N | I |

Raw identity means both numeric `byte_length` and `source_sha256`; N means both
are `"NONE"`. A drifted entry's raw identity describes its last complete stable
read and `failure_evidence` proves the later drift, so it cannot support an
exclusion. Every decoded dependency root covers its complete observed edge set;
the `INCOMPLETE` root also includes the exact missing, unreadable, or unresolved
edge state. No field combination outside this table is a valid v1 body.
Every `DECODED` row's `reader_contract` equals the selected member's derived
contract ID and `reader_output` names exactly one
`LegacyReaderSuccess/v1`; the output repeats that member, digest, contract ID,
reader-execution binding, raw identity, target surface, and disposition. Every after-dispatch
`INVALID_ARTIFACT` instead names exactly one `LegacyReaderFailure/v1` under
that same member, contract ID, and execution binding, and its separate
`failure_evidence` equals the failure body's reference byte for byte. The
binding resolves under the successor acceptance contract and must map that
member's complete selector, wire contract, derived contract ID, and pinned
source revision to the exact executed reader tool and immutable
implementation. No other `reader_output` kind or version is admitted.
For an after-dispatch `INVALID_ARTIFACT`, `COMPLETE` means every dependency edge
resolved but the selected reader rejected the artifact's authenticated content;
`INCOMPLETE` means the exact dependency-root preimage records at least one
missing, unreadable, or unresolved edge. Neither state produces historical
digests or partial success. Only the selected frozen reader's registered
failure output and its authenticated failure evidence may give an
after-dispatch failure determinate target overlap; every failure path without
that exact output is indeterminate.

The remaining shared compatibility objects are:

```text
ServiceIdentity := {
  "service_id": Id,
  "adapter_kind": Token,
  "service_locator": Text,
  "target_surface_digest": Digest
}

RoleIdentity := {
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
  "role_grant_set": EvidenceRef,
  "fence_proposal_id": Id,
  "services": set<ServiceIdentity>,
  "database_roles": set<RoleIdentity>,
  "target_partition_proof": EvidenceRef,
  "quiescence_predicates": set<QuiescencePredicate>,
  "realization_policy": EvidenceRef,
  "writer_inventory": EvidenceRef
}

EpochActivationProposal := {
  "continuity_session_id": Id,
  "reserved_publication_epoch": SafeInteger,
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
  "observed_value_digest": Digest
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

PersistentLegacyFenceEvidence := {
  "authority": "NONE",
  "drain_observation_generation": Text,
  "fence_generation": PositiveSafeInteger,
  "kind": "hindsight-compatibility-persistent-legacy-fence-evidence",
  "realization_policy": EvidenceRef,
  "realized_acl_digest": Digest,
  "realized_admission_digest": Digest,
  "schema_version": 1,
  "service_disable_evidence_set_digest": Digest,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest,
  "writer_fence_proposal_digest": Digest,
  "zero_live_writer_evidence_digest": Digest
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
  "persistent_fence_evidence_digest": Digest,
  "continuity_session_id": Id,
  "reserved_publication_epoch": SafeInteger,
  "incarnation_capability_digest": Digest,
  "deployment_attestation": EvidenceRef,
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
  "persistent_fence_evidence_digest": Digest,
  "continuity_session_id": Id,
  "reserved_publication_epoch": SafeInteger,
  "incarnation_capability_digest": Digest,
  "deployment_attestation": EvidenceRef,
  "authority": "NONE"
}

TargetState := {
  "generation": TargetGeneration,
  "lineage_key_digest": Digest,
  "selected_cohort_digest": Digest,
  "preserved_cohort_digest": Digest,
  "snapshot_digest": Digest
}

SuccessorDeploymentAttestationRef := {
  "contract_kind": "hindsight-postgresql-deployment-attestation",
  "contract_version": 1,
  "body_digest": Digest
}

SuccessorClockEnvelopeRef := {
  "contract_kind": "hindsight-postgresql-clock-envelope",
  "contract_version": 1,
  "body_digest": Digest
}

SuccessorClosurePolicyLimitsRef := {
  "contract_kind": "hindsight-postgresql-closure-policy-limits",
  "contract_version": 1,
  "body_digest": Digest
}

ClosureQualifiedSampleEvidenceRef := {
  "contract_kind": "hindsight-compatibility-closure-qualified-sample-evidence",
  "contract_version": 1,
  "body_digest": Digest
}

ClosureAttestedInvalidationEvidenceRef := {
  "contract_kind": "hindsight-compatibility-closure-attested-invalidation-evidence",
  "contract_version": 1,
  "body_digest": Digest
}

ClosureComparisonEvidenceRef := {
  "contract_kind": "hindsight-compatibility-closure-comparison-evidence",
  "contract_version": 1,
  "body_digest": Digest
}

ClosureFailureEvidenceRef := {
  "contract_kind": "hindsight-compatibility-closure-failure-evidence",
  "contract_version": 1,
  "body_digest": Digest
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
  "observed_generation": TargetGeneration,
  "selected_cohort_digest": Digest,
  "preserved_cohort_digest": Digest,
  "expected_postimage_digest": Digest,
  "deployment_attestation": SuccessorDeploymentAttestationRef,
  "qualified_clock_envelope": SuccessorClockEnvelopeRef,
  "closure_policy_limits": SuccessorClosurePolicyLimitsRef,
  "creation_monotonic_sample_upper_ns": UInt128String,
  "creation_trusted_upper_bound_unix_ns": UInt128String,
  "maximum_case_lifetime_ms": PositiveSafeInteger,
  "maximum_observation_attempts": PositiveSafeInteger,
  "maximum_reservation_duration_ms": PositiveSafeInteger,
  "maximum_resolution_duration_ms": PositiveSafeInteger,
  "observation_call_timeout_ms": PositiveSafeInteger,
  "observer_lease_duration_ms": PositiveSafeInteger,
  "lock_timeout_ms": PositiveSafeInteger,
  "statement_timeout_ms": PositiveSafeInteger,
  "transaction_timeout_ms": PositiveSafeInteger,
  "idle_in_transaction_timeout_ms": PositiveSafeInteger,
  "connection_lifetime_ms": PositiveSafeInteger,
  "created_at_unix_ns": UInt128String,
  "expires_at_unix_ns": UInt128String
}

ClosureObservationIdentity := {
  "closure_case_digest": Digest,
  "observation_request_id": Text,
  "attempt_ordinal": PositiveSafeInteger
}

ClosureQualifiedSampleEvidence := {
  "authority": "NONE",
  "closure_case_digest": Digest,
  "deployment_attestation": SuccessorDeploymentAttestationRef,
  "kind": "hindsight-compatibility-closure-qualified-sample-evidence",
  "monotonic_sample_lower_ns": UInt128String,
  "monotonic_sample_upper_ns": UInt128String,
  "qualified_clock_envelope": SuccessorClockEnvelopeRef,
  "sample_phase": "RESERVATION" | "CLAIM" | "OBSERVATION" |
                  "FINALIZATION" | "TAKEOVER" |
                  "ABANDONMENT_RESOLUTION",
  "schema_version": 1,
  "trusted_upper_bound_unix_ns": UInt128String
}

ClosureAttestedInvalidationEvidence := {
  "authority": "NONE",
  "closure_case_digest": Digest,
  "deployment_attestation": SuccessorDeploymentAttestationRef,
  "fresh_qualified_sample": ClosureQualifiedSampleEvidenceRef,
  "invalidated_observer_incarnation_digest": Digest,
  "invalidation_reason": "OBSERVER_INCARNATION_ENDED" |
                         "TAGGED_SESSION_CLOSED",
  "kind": "hindsight-compatibility-closure-attested-invalidation-evidence",
  "observer_lease_generation": PositiveSafeInteger,
  "schema_version": 1,
  "tagged_session_identity_digest": Digest
}

ClosureComparisonEvidence := {
  "attempt_ordinal": PositiveSafeInteger,
  "authority": "NONE",
  "closure_case_digest": Digest,
  "comparison_result": "EXACT_MATCH" | "MISMATCH",
  "expected_postimage_digest": Digest,
  "kind": "hindsight-compatibility-closure-comparison-evidence",
  "observed_generation": TargetGeneration,
  "observed_postimage_digest": Digest,
  "preserved_cohort_digest": Digest,
  "schema_version": 1,
  "selected_cohort_digest": Digest,
  "target_database_identity": TargetDatabaseIdentity,
  "target_surface_digest": Digest
}

ClosureFailureEvidenceCommon := {
  "attempt_ordinal": PositiveSafeInteger,
  "authority": "NONE",
  "closure_case_digest": Digest,
  "failure_detail_digest": Digest,
  "kind": "hindsight-compatibility-closure-failure-evidence",
  "schema_version": 1
}

ClosureUnableFailureBranch := {
  "failure_branch": "UNABLE_TO_VERIFY",
  "failure_category": "EXPECTED_STATE_UNAVAILABLE" |
                      "TARGET_READ_UNAVAILABLE" | "TIMEOUT" |
                      "OBSERVER_INTERNAL_ERROR",
  "source_component": "EXPECTED_STATE_READER" | "TARGET_READER" |
                      "CLOSURE_ADAPTER",
  "timing_evidence": ClosureQualifiedSampleEvidenceRef
}

ClosureDeadlineFailureBranch := {
  "failure_branch": "ABANDONED_DEADLINE",
  "failure_category": "RESERVATION_DEADLINE_REACHED",
  "source_component": "RESERVATION_CLOCK",
  "timing_evidence": ClosureQualifiedSampleEvidenceRef
}

ClosureFailureEvidence := ClosureFailureEvidenceCommon &
                          (ClosureUnableFailureBranch |
                           ClosureDeadlineFailureBranch)

ClosureObservationCommon := {
  "kind": "hindsight-compatibility-closure-observation",
  "schema_version": 1,
  "closure_observation_id": Id,
  "closure_case_digest": Digest,
  "observation_request_id": Text,
  "attempt_ordinal": PositiveSafeInteger,
  "observer_lease_generation": PositiveSafeInteger,
  "reservation_deadline_monotonic_ns": UInt128String,
  "authority": "NONE",
  "permitted_action": "DISPLAY_AND_CLASSIFY_ONLY"
}

ClosureMatchObservation := {
  "timing_evidence_mode": "QUALIFIED_SAMPLE",
  "timing_evidence": ClosureQualifiedSampleEvidenceRef,
  "result": "EXACT_MATCH",
  "case_outcome_after": "CLOSURE_MATCH",
  "observed_postimage_digest": Digest,
  "comparison_evidence": ClosureComparisonEvidenceRef,
  "failure_category": "NONE",
  "failure_evidence": "NONE"
}

ClosureMismatchObservation := {
  "timing_evidence_mode": "QUALIFIED_SAMPLE",
  "timing_evidence": ClosureQualifiedSampleEvidenceRef,
  "result": "MISMATCH",
  "case_outcome_after": "CLOSURE_MISMATCH",
  "observed_postimage_digest": Digest,
  "comparison_evidence": ClosureComparisonEvidenceRef,
  "failure_category": "NONE",
  "failure_evidence": "NONE"
}

ClosureUnableObservation := {
  "timing_evidence_mode": "QUALIFIED_SAMPLE",
  "timing_evidence": ClosureQualifiedSampleEvidenceRef,
  "result": "UNABLE_TO_VERIFY",
  "case_outcome_after": "OPEN" |
                        "CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY",
  "observed_postimage_digest": "NONE",
  "comparison_evidence": "NONE",
  "failure_category": "EXPECTED_STATE_UNAVAILABLE" |
                      "TARGET_READ_UNAVAILABLE" | "TIMEOUT" |
                      "OBSERVER_INTERNAL_ERROR",
  "failure_evidence": ClosureFailureEvidenceRef
}

ClosureDeadlineAbandonmentObservation := {
  "timing_evidence_mode": "QUALIFIED_SAMPLE",
  "timing_evidence": ClosureQualifiedSampleEvidenceRef,
  "result": "ABANDONED_UNABLE_TO_VERIFY",
  "case_outcome_after": "OPEN" |
                        "CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY",
  "observed_postimage_digest": "NONE",
  "comparison_evidence": "NONE",
  "failure_category": "RESERVATION_DEADLINE_REACHED",
  "failure_evidence": ClosureFailureEvidenceRef
}

ClosureObservation := ClosureObservationCommon &
                      (ClosureMatchObservation |
                       ClosureMismatchObservation |
                       ClosureUnableObservation |
                       ClosureDeadlineAbandonmentObservation)
```

The `CanonicalLineageKeyBody` projection is the one exception to the
compatibility canonicalizer in this grammar: it uses the successor publication
contract's exact canonical JSON bytes with one trailing LF. Its four keys and
values are the closed lineage-key preimage, and `lineage_key_digest` is SHA-256
over exactly those LF-inclusive bytes. Combined activation resolves the
manifest target through the bijective successor target-identity projection and
uses that exact `EvidenceRef`. The same digest is used by deployment
attestation, compatibility genesis and rollback, every publication stage, and
verification. No digest of the compatibility target object, alternate key
order, or alternate canonicalizer is an alias.

The successor target-state projection is the same kind of explicit exception.
The protected interface reconstructs the acceptance contract's complete
`TargetSurfaceContract/v1`, `TargetCohortMembership/v1`,
`TargetCohortProjection/v1`, and `TargetMutationImage/v1` bodies. It uses the
closed relation, column, primary-key, row, PostgreSQL-value, cohort-membership,
and ordering grammars and the exact compatibility-to-successor target identity
mapping. The selected and preserved memberships are disjoint and their union
is the complete target surface; each projection contains all and only its
membership's rows and permitted columns. The interface cannot provide an
opaque projection object or omit a comparison-relevant value.

Each state body uses successor canonical JSON with exactly one LF.
Compatibility `snapshot_digest`, expected postimage, and observed postimage
fields are SHA-256 over the independently reconstructed complete
`TargetMutationImage/v1` bytes for that generation. Retained restore content is
not one of those state images. It is an independently canonical
`TargetRestorePayload/v1` containing the selected content and no generation or
preserved cohort. The historical reader emits one exact no-LF
`LegacyRestoreContent/v1`; `RestorePayloadConversion/v1` maps its closed
relation, row, column, and value fields to the successor payload without loss
or inference. A compatibility body may carry these exact digests, but it
cannot select another projection, canonicalizer, ordering, wire terminator,
lineage key, or digest preimage.

Successor apply content is a separate exact
`TargetApplyPayload/v1`: a generation-free, complete selected-cohort desired
postimage bound only by the apply plan. It uses the same closed projection and
canonical-byte rules, but it is neither compatibility evidence nor an input to
a `LEGACY_COMPLETE_APPLY` rollback. Compatibility cannot synthesize, narrow,
or replace that payload.

For closure case `C`, deployment attestation `D`, qualification plan `Q`,
qualification receipt `Q_R`, support profile `S`, closure policy `L`, and clock
envelope `E = body(C.qualified_clock_envelope)`, the typed references and copied
limits are exact:

For a successor body `X`, `ref(X)` is its exact typed `EvidenceRef` with
`contract_kind = X.kind`, `contract_version = X.schema_version`, and
`body_digest = digest(X)`.

```text
C.deployment_attestation = ref(D)
C.qualified_clock_envelope = D.clock_envelope
C.closure_policy_limits = D.closure_policy_limits
D.qualification_plan = ref(Q)
D.qualification_receipt = ref(Q_R)
D.support_profile = ref(S)
D.closure_policy_limits = Q_R.closure_policy_limits
Q_R.plan = ref(Q)
Q_R.support_profile = ref(S)
Q_R.closure_policy_limits = Q.closure_policy_limits
Q.support_profile = ref(S)
Q.closure_policy_limits = S.closure_policy_limits
C.closure_policy_limits = ref(L)

for field in {
    maximum_case_lifetime_ms,
    maximum_observation_attempts,
    maximum_reservation_duration_ms,
    maximum_resolution_duration_ms,
    observation_call_timeout_ms,
    observer_lease_duration_ms,
    lock_timeout_ms,
    statement_timeout_ms,
    transaction_timeout_ms,
    idle_in_transaction_timeout_ms,
    connection_lifetime_ms
}:
    C[field] = L[field]
```

Every listed value is a positive finite `PositiveSafeInteger`. Policy
validation also requires `maximum_resolution_duration_ms <=
transaction_timeout_ms <= connection_lifetime_ms <=
observation_call_timeout_ms`; `observer_lease_duration_ms <=
maximum_reservation_duration_ms`; each database timeout is no greater than
`connection_lifetime_ms`; and checked
`maximum_reservation_duration_ms + maximum_resolution_duration_ms <
maximum_case_lifetime_ms`. A deployment that cannot implement any named guard
with those semantics is unqualified; it cannot replace a value with a local
default or a disabling zero.

Closure converts each policy millisecond value with exactly this operation:

```text
ms_to_ns(x) = checked_mul_u128(UInt128(x), 1000000)
```

The multiplication is exact; a product above the UInt128 maximum rejects the
policy, case, or transition. No floating point, saturating arithmetic, unit
guess, truncation, or implementation-native duration conversion is admitted.
Every `checked_add`, `checked_sub`, and named result below likewise rejects
UInt128 overflow or underflow. Products used by `ceil_mul_div` and
`safe_measured_horizon` are evaluated over exact unbounded nonnegative
integers, and their named results must fit UInt128.

At case creation, let `s0` be
`C.creation_monotonic_sample_upper_ns`, let `u0` be
`C.creation_trusted_upper_bound_unix_ns`, and let `m` be
`E.monotonic_validity_deadline_lower_ns`.
The protected interface requires

```text
E.monotonic_anchor_lower_ns <= s0 < m
u0 = qualified_trusted_upper_bound(C.qualified_clock_envelope, s0)
C.created_at_unix_ns = u0
C.expires_at_unix_ns = min(
    D.valid_until_unix_ns,
    checked_add(u0, ms_to_ns(L.maximum_case_lifetime_ms))
)
C.created_at_unix_ns < C.expires_at_unix_ns
```

Let `r = ms_to_ns(L.maximum_reservation_duration_ms)` and
`q = ms_to_ns(L.maximum_resolution_duration_ms)`. Creation additionally
defines the separately rounded wall-time resolution margin and total split
wall-time horizon:

```text
q_rate = ceil_mul_div(q, n, d)
q_margin = checked_add(q, q_rate)
h_measured = checked_add(r, q)
h_split_wall = checked_add(
    r,
    ceil_mul_div(r, n, d),
    q_margin
)
checked_add(s0, h_measured) < m
checked_add(u0, h_split_wall)
    < C.expires_at_unix_ns
```

Here `n` and `d` are the envelope's reduced forward-rate numerator and positive
denominator. The separate reservation and resolution roundings are deliberate;
their sum can exceed one rounding over the combined duration by one
nanosecond. The case cannot occupy its stable key unless one complete maximum
reservation and the separately rounded full maximum resolution interval fit
under the qualified clock and earliest wall expiry. Equality is expired.

For every later qualified sample, `l` and `s` are the protected producer's
recorded `monotonic_sample_lower_ns` and `monotonic_sample_upper_ns`, including
sampling and conversion uncertainty, and `u` is its recorded
`trusted_upper_bound_unix_ns`. A caller supplies none of them, and the protected
slot exact-replays them, so an observation cannot be backdated. The verifier
requires `anchor_lower <= l <= s < m` and recomputes `u` from the exact
envelope, elapsed value, reduced forward-rate fraction, and upward-rounded rate
error. Ordinary freshness and pre-expiry decisions use conservative upper
sample `s`; abandonment alone uses lower sample `l` to prove that no instant in
the sample interval precedes reservation expiry.
Here `n = E.forward_rate_error_numerator` and
`d = E.forward_rate_error_denominator`, with `d > 0`. For exclusive wall expiry
`e`, define:

```text
safe_measured_horizon(e, u, n, d):
    require u < e
    w = e - u
    return floor(((w - 1) * d) / (d + n))
```

where `n` and positive `d` are the envelope's rate numerator and denominator.
This is the greatest measured nanosecond interval `x` for which
`u + x + ceil_mul_div(x, n, d) < e`; the subtraction of one makes equality
late.

For reservation, compute:

```text
call = ms_to_ns(L.observation_call_timeout_ms)
conn = ms_to_ns(L.connection_lifetime_ms)
h_case = safe_measured_horizon(C.expires_at_unix_ns, u, n, d)
h_att = safe_measured_horizon(D.valid_until_unix_ns, u, n, d)
q_rate = ceil_mul_div(q, n, d)
q_margin = checked_add(q, q_rate)

require h_case > q_margin
require h_att > q_margin
require checked_add(s, q) < m

reservation_deadline_monotonic_ns = min(
    checked_add(s, r),
    checked_add(s, call),
    checked_add(s, conn),
    checked_add(s, checked_sub(h_case, q_margin)),
    checked_add(s, checked_sub(h_att, q_margin)),
    checked_sub(checked_sub(m, q), 1)
)

require s < reservation_deadline_monotonic_ns
```

The stored deadline must equal that earliest candidate byte for byte. The
case- and attestation-expiry candidates reserve `q_margin`; the monotonic
clock-validity candidate reserves the raw measured `q`. Using a later candidate, omitting an
operand, overflow, underflow, or equality consumes no ordinal. The protected
observation-call timer starts at this sample, and the tagged observer session
must be forcibly closed no later than both its connection candidate and the
stored reservation deadline.

At each phase, a fresh qualified sample `p` and its recorded trusted upper
bound `v` must satisfy
`E.monotonic_anchor_lower_ns <= p < m`,
`v = qualified_trusted_upper_bound(C.qualified_clock_envelope, p)`, and
`p <` every applicable exclusive deadline. For claim and takeover, let
`lease = ms_to_ns(L.observer_lease_duration_ms)` and derive exactly:

```text
observer_lease_deadline_monotonic_ns = min(
    reservation_deadline_monotonic_ns,
    checked_add(p, lease)
)
require p < observer_lease_deadline_monotonic_ns
```

The stored lease deadline must equal that result; a takeover advances the
lease generation before installing this newly derived deadline and cannot
extend the reservation. The positive database setting for each of the lock,
statement, transaction, and idle-in-transaction guards is
`min(policy_value_ms, floor((deadline - p) / 1000000))`; a zero result rejects
before the transaction begins. The adapter computes
`connection_deadline = min(deadline,
checked_add(p, ms_to_ns(L.connection_lifetime_ms)))`. Lock, statement,
transaction, idle-in-transaction, and adapter connection expiry each roll the
transaction back; none preserves a finalization right. Equality at a lease,
reservation, case, attestation, connection, call, or clock-validity deadline is
expired.

Post-reservation resolution takes a fresh qualified sample after the tagged
session is closed. Its new deadline is the earliest of `s + q`, `s + call`,
`s + conn`, `m - 1`, and the case- and attestation-expiry candidates computed
as `s + safe_measured_horizon(e, u, n, d)`. All operands use the same checked
rules, the result must be greater than `s`, and the same effective finite guards
apply. Because reservation admitted only after reserving the full `q` margin,
a deadline-triggered resolution beginning at the stored reservation deadline
has the complete policy interval available; an attestation or clock
invalidation instead produces no synthetic deadline or abandonment.

`TargetGeneration` is the one target-generation type shared with successor
deployment, plan, `J`, `M`, `V`, and verification-observation bodies. A frozen
historical reader may project a source string only by accepting the exact ASCII
grammar `0|[1-9][0-9]*`, parsing it over unbounded nonnegative integers, and
rejecting a value above `9007199254740991`. A historical JSON number must
already be a nonnegative safe integer. Negative values, whitespace, a plus
sign, leading zeros, fractions, exponents, quoted aliases outside that exact
conversion, overflow, and implementation-native numeric coercions are invalid.
The projection emits the canonical JSON integer; no source spelling survives
as successor authority.

The final manifest's `target_state` copies the exact generation and selected
and preserved cohort digests observed after the fence barrier. Its
`lineage_key_digest` is SHA-256 over the exact successor
`CanonicalLineageKeyBody` bytes described above. The protected finalizer
constructs the one `TargetMutationImage/v1` from those values, the manifest's
bridged target identity and surface, and the complete protected target
projection, then requires
`target_state.snapshot_digest` to equal SHA-256 over the complete canonical
image bytes including their LF. The manifest carries only the digest and
non-content image fields; revalidation reconstructs the protected image from
the still-held source descriptors and live target rather than persisting
another plaintext copy. Combined activation initializes the protected
successor target-generation slot to that same value atomically with manifest,
lineage genesis, and epoch activation. Lineage genesis generation zero is a
different counter and does not reset target generation. A
`LEGACY_COMPLETE_APPLY` rollback plan, its `J.expected_target_generation`, and
the still-current live target image all carry this initialized generation. The
rollback-preimage binding instead carries no generation or mutation-image
digest. It resolves the frozen reader's exact historical no-LF plaintext, the
registered `LegacyRestoreContent/v1`, the deterministic
`FIELDWISE_TARGET_RESTORE_V1` conversion, and the resulting successor
`TargetRestorePayload/v1`. The payload's selected membership equals the
manifest image's selected membership while its selected row values are the
historical content to restore. Any target, surface, lineage, membership,
reader, source-byte, conversion, payload, or live-image mismatch refuses before
`J`. Rollback `M` derives its after `TargetMutationImage/v1` by combining that
payload with the locked unchanged preserved cohort and checked next generation;
it increments the common generation type exactly once and rejects overflow.

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

`WriterFenceProposal.role_grant_set` and `.writer_inventory` name the exact
successor-canonical `RoleGrantSet/v1` and `WriterInventory/v1` references in
the current deployment attestation. The inventory names that grant set, target
database, and surface byte for byte. Its typed target-surface preimage projects
bijectively to this contract's complete relation, column, and key contract, its OID set
equals the inventory's exact relation-OID sequence, and its digest is recomputed
only by this contract's separator-free member-byte formula. A different
preimage, OID projection, or path outside that set blocks the proposal.
`database_roles` is the complete
duplicate-free compatibility projection of every effective actor role in its
direct, administrative, routine, and service writer paths; each
`RoleIdentity` copies the exact OID and catalog name from the grant set and the
inventory's target surface. `services` is the complete duplicate-free
projection of every `WriterServiceIdentity/v1` in the inventory, and those
references equal exactly the distinct service identities used by all
`SERVICE` writer paths. It copies
`service_id`, `adapter_kind`, `service_locator`, and target surface. The
compatibility canonicalizer independently resolves the successor bodies,
recomputes both projections, and requires exact set equality. No caller may
drop, add, rename, collapse, or reorder a role or service. `RoleIdentity` has
no caller-supplied UUID; its closed stable identity is its exact catalog OID and
target surface, and an extra `role_id` field is rejected as unknown. An omitted
service-path identity or unreferenced extra service identity blocks the
proposal before any `SERVICE_DISABLED` predicate can be accepted.

The successor profiler and admission finalizer independently enumerate and
successor-canonicalize every role attribute, recursive membership and
assumable-role edge, `PUBLIC` grant, ownership edge, direct ACL, grant option,
default privilege, mutation-capable invoker/definer routine or dynamic call
path, and direct-SQL, scheduled, background, prepared, replication, adapter,
or service writer path. Compatibility treats an unenumerated, unclassifiable,
unresolved, unattributed, duplicate, extra, OID/name-conflicting, inherited,
`PUBLIC`, owner, default-privilege, function-mediated, or service path as a
hard blocker. A role or service spanning surfaces remains a blocker for every
affected v1 activation; it cannot be assigned to a convenient subset.

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
conflict. `drain_observation_generation` in the persistent fence-evidence body
must equal the same field in the exact locked `ZeroLiveWriterEvidence` body.

`PersistentLegacyFenceEvidence/v1` is the epoch-independent proof that the
legacy writer has remained closed. Its digest is SHA-256 over its complete
canonical body including the LF. Every realized-evidence digest, realization
policy, proposal digest, drain generation, and service-set digest is recomputed
from the exact locked protected bodies above. It contains no cutover, manifest,
approval, continuity session, capability, deployment attestation, publication
epoch, or admission generation. It is immutable for one target, surface, and
fence generation; refreshing successor authority never repeats an external
fence effect and never edits this body.

An origin or adoption body is the atomic per-epoch handoff from that persistent
fact to one exact manifest, approval, continuity session, capability,
deployment attestation, and reserved publication epoch. Its
`persistent_fence_evidence_digest` resolves the exact current persistent body
for the same target, surface, and fence generation. The handoff-body digest is
SHA-256 over the complete canonical body including the LF and is absent from
its own preimage. The protected origin row is unique
on `(target_database_identity, target_surface_digest, fence_generation, 0)`;
each adoption row is unique on the same first three values plus its positive
`adoption_generation`. The origin body is generation zero with no prior
binding. Every adoption body names generation `n-1`'s exact body digest and is
accepted only as generation `n`, so the binding chain is contiguous and
acyclic. A caller-supplied projection or equivalent value cannot substitute
for the persistent evidence or handoff rows.

The protected current-binding pointer occupies one fixed slot keyed only by
the target database identity and canonical target surface, not by publication
epoch. While that slot has an active origin or adoption binding, successor
stage admission must use `COMPATIBILITY`; it cannot use `FRESH` merely because
the proposed epoch differs. The compatibility verifier resolves the exact
current binding and separately requires its `reserved_publication_epoch` to
equal the protected epoch that the combined transaction activates and every
later stage epoch. Only an unoccupied fixed slot can satisfy `FRESH`.

The stable identities for the three proposal sets are `service_id`,
`(postgres_role_oid, target_surface_digest)`, and `predicate_id`; the realized predicate-observation sets use that same
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
`case_outcome_after=CLOSURE_MISMATCH`. Each requires
`timing_evidence_mode=QUALIFIED_SAMPLE`, a timing reference to the exact
protected `ClosureQualifiedSampleEvidence/v1` body for this case with
`sample_phase=FINALIZATION`, a postimage digest, and a comparison reference to
the exact protected `ClosureComparisonEvidence/v1` body for this case and
ordinal. The comparison body copies the case target, surface, expected
postimage, observed generation, selected cohort, and preserved cohort exactly;
copies the observation's postimage; and records `EXACT_MATCH` exactly when the
two postimages are byte-equal or `MISMATCH` exactly when they differ. Both
failure fields are `"NONE"`.

The expected-state reader and live-target reader each emit the complete closed
`TargetMutationImage/v1` required above. The protected comparison independently
successor-canonicalizes each body with one LF and recomputes its SHA-256; the
case and observation digest fields must equal those results. Comparing stored
digest placeholders, differently ordered projections, compatibility-canonical
bytes, or a body with another target, surface, lineage key, generation, or
cohort field is invalid.

`UNABLE_TO_VERIFY` also uses an exact finalization qualified sample, requires
both postimage and comparison fields to be `"NONE"`, and references the exact
protected `ClosureFailureEvidence/v1` body for this case and ordinal with
`failure_branch=UNABLE_TO_VERIFY`. Its category and source are a closed pair:
`EXPECTED_STATE_UNAVAILABLE/EXPECTED_STATE_READER`,
`TARGET_READ_UNAVAILABLE/TARGET_READER`, `TIMEOUT/CLOSURE_ADAPTER`, or
`OBSERVER_INTERNAL_ERROR/CLOSURE_ADAPTER`. The failure body's timing reference
is the same exact qualified-sample body referenced by the observation.

`ABANDONED_UNABLE_TO_VERIFY` requires both postimage and comparison fields to
be `"NONE"` and uses exactly the deadline branch. It uses
`timing_evidence_mode=QUALIFIED_SAMPLE`, an exact
`ClosureQualifiedSampleEvidence/v1` with
`sample_phase=ABANDONMENT_RESOLUTION`, and an exact
`ClosureFailureEvidence/v1` with
`failure_branch=ABANDONED_DEADLINE`,
`failure_category=RESERVATION_DEADLINE_REACHED`,
`source_component=RESERVATION_CLOCK`, and the same timing reference. The
sample proves `monotonic_sample_lower_ns >=
reservation_deadline_monotonic_ns`; equality is late. An exact
`ClosureAttestedInvalidationEvidence/v1` identifies a fenced lease generation,
tagged session, and observer incarnation only for a pre-expiry same-ordinal
takeover. It references a fresh `TAKEOVER` qualified sample strictly before the
reservation deadline and never appears in an abandonment observation or
failure body.

Every qualified-sample body references the case's exact
`hindsight-postgresql-deployment-attestation/v1` and
`hindsight-postgresql-clock-envelope/v1` bodies. The protected producer
requires both references and their complete bindings to equal the case,
revalidates them as current, and recomputes the acceptance design's exact lower
and upper monotonic samples, UInt128 elapsed, upward-rounded rate-error, and
trusted-upper-bound arithmetic; all encoded operands and results must match,
including `monotonic_sample_lower_ns <= monotonic_sample_upper_ns`. A negative subtraction, overflow,
invalid continuity, changed clock or deployment policy, equality with the
monotonic validity deadline, or any other unqualified sample creates no body.
For reservation, claim, observation, finalization, and takeover phases the
trusted upper bound must be strictly below every applicable case and
deployment expiry and the monotonic sample must be strictly before the
reservation or lease deadline required by that phase. Equality is late. The
abandonment-resolution phase is nonauthorizing and follows the exact
deadline-qualified predicates above.

Qualified-sample identity is
`(closure_case_digest, sample_phase, monotonic_sample_lower_ns,
monotonic_sample_upper_ns)`; attested-
invalidation identity is
`(closure_case_digest, observer_lease_generation,
invalidated_observer_incarnation_digest)`; comparison identity is
`(closure_case_digest, attempt_ordinal)`; and failure identity is
`(closure_case_digest, attempt_ordinal, failure_branch)`. Exact replay returns
the protected canonical body and digest. The same identity with different
bytes is `CONFLICT`; a matching unattached body, a wrong kind or version, a
reconstructed projection, or an unrestricted `EvidenceRef` is invalid.

An unable or abandoned observation has `case_outcome_after=OPEN` below the
attempt ceiling and `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY` exactly at the
ceiling. No other result, category, source, timing mode, evidence kind/version,
or field combination is valid. Positive attempt ordinals are contiguous
within a case, and the request identity is unique within that case. Reboot,
suspend uncertainty, clock-envelope loss, rollback, drift, or excessive error
instead makes the case a remediation blocker.

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
  "created_at_unix_ns": UInt128String,
  "observed_at_unix_ns": UInt128String,
  "freshness_deadline_unix_ns": UInt128String,
  "expires_at_unix_ns": UInt128String
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
  "issued_at_unix_ns": UInt128String,
  "expires_at_unix_ns": UInt128String,
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
  "issued_at_unix_ns": UInt128String,
  "expires_at_unix_ns": UInt128String
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
  "reader_contract": ContractId | "NONE"
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
- live target generation, cohort, canonical lineage key, and snapshot digest
  recomputed from the protected successor `TargetMutationImage/v1` body;
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

Before the proposal can enter an approved manifest, the attestation finalizer
must have atomically created exactly one protected epoch row and its bound
deployment attestation and installed the row in the current-reserved-activation
selector keyed by target database and canonical target surface. The row is
also keyed by its newly allocated numeric epoch. Its state is
`RESERVED_FENCED`, its predecessor is the exact current active epoch or `NONE`
for genesis, and its immutable binding names the continuity session,
incarnation-capability digest, target-generation slot, deployment attestation,
support profile, and all four controller-host, PostgreSQL-host, endpoint, and
topology references. The proposal's `reserved_publication_epoch`, the
deployment attestation's `proposed_publication_epoch`, and the protected row's
epoch are exactly equal. The proposal's other fields and the manifest target
are byte-equal to that row. Reservation neither changes the current-active-
epoch pointer nor permits `J`, `P`, `R`, `M`, or `V`. An unselected, abandoned,
already active, previously activated, differently bound, or reused epoch is
invalid; another body cannot adopt or renumber it.

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
typed role-grant set, typed writer inventory, adapter, validity interval, and
revocation state still match,
requires `issued_at_unix_ns < valid_until_unix_ns`, and resolves its exact
current v1 support-profile, qualification-plan, plan-acceptance, and `PASS`
qualification-receipt references, the exact current deployment-admission
policy reference, and the complete exact deployment-tier partition. The
receipt's exact `EV-CLK`, `EV-PHY`, and `EV-CAP` results must match that profile
and installed release. The deployment partition and the receipt's complete
design-, implementation-, and release-tier partitions must equal every exact
protected current-result pointer and resolve to `PASS`. The gate holds the
policy and all of those protected records and pointers through invocation
issuance. A missing, extra, duplicate, conflicting, unlinked, policy-noncurrent,
or invalid body, digest, approval, receipt, or principal fails before mutation.

The same gate resolves the support profile's exact controller-host,
PostgreSQL-host, PostgreSQL-endpoint, and deployment-topology bodies and
requires the attestation and protected live deployment binding to name those
same four references. For `macos-local-postgresql-v1`, locality is exactly
`SAME_HOST_LOCAL`; controller and PostgreSQL host identities are equal; both
stable host boot-configuration references equal the profile's top-level boot
configuration; the live projection's actual boot identity equals the
attestation and its clock envelope; both host operating-system references equal the profile's top-level
operating-system component; the PostgreSQL host's PostgreSQL and storage
references equal the corresponding top-level components; the exact qualified
closure-policy reference equals the qualification plan, receipt, attestation,
and live deployment; the endpoint targets this database and uses the exact
complete effective canonical Unix-socket-directory sequence from
`PostgresqlComponentConfiguration/v1`. The initial profile has exactly one
member; its configured path, symlink-resolved absolute path, and device/file
identity equal the live projection and endpoint's embedded member, and the
endpoint address is derived from its resolved path and ends in
`.s.PGSQL.<configured-port>`, with the separate port field `NONE`; and no
network path exists. Every TCP endpoint,
remote or managed PostgreSQL, a hostname or address, proxy or tunnel,
controller-host drift, PostgreSQL-host drift, target drift, configured or
resolved socket-path drift, directory-identity, count, or order drift,
complete-address drift, port drift, or transport drift
fails before any fence effect. Equivalent reachability is not deployment
equality.

The gate then locks and validates the bound qualified clock envelope, samples
its monotonic clock, and derives `U_prefence` with the same conservative
formula. It requires `U_prefence` to be strictly below the deployment
attestation expiry, manifest expiry and freshness deadline, manifest approval
expiry, and every exclusion-body and exclusion-approval expiry. The gate
parses each manifest, exclusion, and approval time as a checked UInt128
nanosecond value and also requires
`created_at_unix_ns <= observed_at_unix_ns <
freshness_deadline_unix_ns <= expires_at_unix_ns` for the manifest basis and
`issued_at_unix_ns < expires_at_unix_ns` for each approval and exclusion. The
protected `U_prefence`, `U_consume`, `U_fence_start`, `U_fence_commit`,
`U_adopt`, and `U_cutover` comparisons use those exact fields with strict
`U < deadline`; equality is late. Overflow, an unrepresentable operand, or a
seconds-to-nanoseconds projection refuses before authority or an effect. The gate
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
attestation identity, target, typed role-grant set and writer inventory,
adapter, validity, and unrevoked state,
its protected current policy reference, its complete deployment-tier
partition, and its qualification receipt's complete design-, implementation-,
and release-tier partitions against every protected current `PASS` pointer,
then samples the same qualified monotonic source. It requires that sample to
remain inside the attested monotonic range and rejects attestation replacement,
revocation, or expiry, policy or result noncurrency, clock identity loss,
rollback, reboot or suspend uncertainty, envelope drift, or excessive error.
Only then does it
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
the fixed target fence slot and the exact protected deployment-attestation,
current-policy, complete deployment-partition,
qualification-receipt-partition, and proposal prerequisites; under those
locks, the adapter revalidates the exact
consumed invocation digest, nonce, incarnation, proposal, target partition,
qualified clock envelope, deployment attestation, current policy, every
protected current `PASS` result pointer, and every validity and freshness
bound. It takes a fresh qualified monotonic sample and derives a
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
clock envelope, attestation, current policy, current result pointers, proposal,
and bounds, takes another qualified sample, and requires conservative
`U_fence_commit` to remain strictly below every bound. Failure rolls the whole
transaction back. The transaction either
commits that complete reconnect barrier and resumable state or changes nothing.
An aborted transaction permanently spends the local invocation and releases
the effect-attempt lock; a retry requires a fresh pre-fence gate and invocation,
not the old `CONSUMED` record. A lost commit acknowledgement resolves only by
locking and exact-querying the fence row's consumed-invocation digest and the
live realized admission and ACL state. `ACCESS_REVOKED` does not claim
quiescence: a transaction that passed its privilege check before commit may
still finish until its backend is fenced.

The consumed origin invocation authorizes only that first access-revocation
transaction. It is not continuation authority for drain or service steps.
Immediately before every later external observation, cancellation,
termination, drain wait, or service-disable effect, the adapter opens a
protected transition transaction; locks the exact
fence row, manifest binding, proposal, current deployment-policy and
attestation slots, complete current deployment and qualification result
partitions, qualified clock envelope, admission and ACL state, and the
specific pending step; takes a fresh qualified sample; and requires the
conservative upper bound through that step's finite timeout to remain strictly
below every attestation, policy, manifest, approval, exclusion, freshness, and
clock bound. It holds those locks across the external call and its exact
outcome recording. Revocation, replacement, expiry, evidence noncurrency,
clock uncertainty, scope drift, or inability to hold the locks fails closed
before the effect. No prior sample, invocation, pending row, or progress record
is a bounded continuation receipt.

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
pending operation only while the fresh per-effect authority and time gate above
passes, repeats the complete drain, and later activation reobserves
the target generation, cohort, and snapshot after the barrier; any intervening
legacy mutation therefore causes the approved manifest to fail revalidation.

With the database-side barrier continuously held, the adapter disables each
exact named service under the fence operation identity and fresh per-effect
gate above. After each externally
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
conflicting, or mismatched member aborts. It first constructs the exact
epoch-independent `PersistentLegacyFenceEvidence/v1` from the protected
realized evidence, then constructs the exact `OriginFenceManifestBinding/v1`
from the origin manifest and epoch fields plus that persistent body digest. It
canonicalizes and appends both immutable bodies, sets the current
manifest-binding pointer to the origin-body digest, and advances the same
generation to `FENCE_ACTIVE` in one synchronous commit.
No `FENCE_ACTIVE` row has a missing, caller-chosen, or noncanonical current
binding.

`FENCE_PENDING` and partial progress create no successor epoch, manifest,
genesis, stage, or mutation authority. Activation requires the exact
`FENCE_ACTIVE` row. Any crash after `SESSIONS_DRAINED` leaves the target-wide
database write barrier in force and resumes only the named fence operation;
removing that barrier or restoring a service requires separate authorization
under a separately accepted fence-removal design.

After the row reaches `FENCE_ACTIVE`, a manifest refresh for the current
reserved epoch or a later attested epoch never reuses the origin invocation.
It uses the distinct protected `ADOPT_ACTIVE_FENCE` branch. The branch performs
no external fence effect, never reopens a service or role, and uses this
compare-and-swap key:

```text
(
  target_database_identity,
  target_surface_digest,
  fence_generation,
  expected_adoption_generation,
  expected_current_manifest_binding_digest
)
```

The fresh read-only pre-fence gate authenticates the complete recursive
manifest envelope, approval, target, proposal, deployment attestation, clock,
inventory, and target state. One synchronous protected PostgreSQL transaction
then locks the fixed fence slot, exact `FENCE_ACTIVE` row, current handoff,
persistent fence evidence, admission and ACL rows, complete drain and service
evidence, epoch selectors, proposed epoch row, deployment attestation, current
policy, complete deployment- and qualification-result partitions, clock
envelope, and proposal binding. It recomputes every persistent-evidence digest
and live fence predicate and requires the legacy barrier to have remained
continuously closed.

For a same-epoch manifest refresh, the transaction requires the existing exact
`RESERVED_FENCED` row and attestation and changes neither. For a later epoch,
only the protected deployment-admission finalizer may enter the branch: under
the same locks it allocates the checked-next admission generation and
publication epoch, constructs the new deployment attestation and
`RESERVED_FENCED` row, installs their current selectors, constructs the
per-epoch adoption handoff, and advances the current-handoff pointer in one
transaction. No intermediate state exposes a new attestation or reserved epoch
with the prior epoch's handoff. The prior active epoch may remain historical or
current until combined activation, but the proposed new epoch itself must have
no manifest, genesis, lineage fact, stage, or mutation receipt.

Both modes require exact equality of every protected current `PASS` result
pointer, target, continuity session, reserved epoch, capability digest,
canonical writer-fence-proposal digest, fence generation, realized admission
and ACL digests, zero-live-writer evidence and drain generation, and
service-disable evidence. The transaction revalidates the attestation and
qualified time and requires conservative `U_adopt` strictly below every fresh
manifest, approval, exclusion, attestation, and clock bound. Any mismatch,
overflow, or concurrent revocation, replacement, fence change, regrant,
writer, service enablement, or proposed-epoch authority aborts the complete
transaction.

On success, the transaction constructs the exact
`ActiveFenceManifestAdoption` body from the complete compare-and-swap key, the
next `adoption_generation`, the prior handoff digest, the fresh manifest and
approval, the proposed epoch authority fields, and the exact protected
`persistent_fence_evidence_digest`. It canonicalizes and appends that immutable body, atomically
advances the fence row's current manifest-binding digest to its body digest,
and commits with `synchronous_commit=on`. It changes no legacy service,
legacy admission, ACL, target, genesis, or publication-lineage state; the later-epoch mode makes
only the atomic attestation, reservation, selector, and handoff changes named
above. The exact same new cutover,
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

After exact acknowledgement or recovery of that durable fence, the
continuity-client adapter alone consumes the activation proposal and calls one
combined transaction on the proposed continuity session. That transaction:

1. authenticates the exact manifest and approval; locks the active fence's
   current manifest-binding pointer and the protected origin and adoption
   rows; replays the contiguous body-digest chain from generation zero through
   the row's exact `adoption_generation`; canonicalizes and recomputes every
   body digest; requires the current body to name this manifest, approval,
   proposal, target, fence generation, session, epoch, capability, attestation,
   and exact persistent fence-evidence digest; resolves that digest and
   recomputes its complete realized fence evidence;
2. derives the canonical target database identity and surface on the server,
   resolves the deployment attestation's successor `TARGET_DATABASE`
   `EvidenceIdentity/v1`, applies the bijective
   `compatibility_target_identity` projection, and requires byte equality with
   the manifest and live target identity; independently canonicalizes the exact
   relation members and recomputes the bound `target_surface_digest`;
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
   canonical v1 body digests, resolves and recomputes every branch-required
   exact v1 qualified-sample, attested-invalidation, comparison, and failure
   evidence body, rejects any generic or wrong-kind/version reference, and
   revalidates the exact reader registry and source
   raw/historical/reader-contract projection-set digests plus the complete
   chain root, case, tagged result, terminal-slot, target, generation, cohort,
   postimage, phase, deadline inequality, category, source, and evidence
   linkage;
6. reconstructs the live `TargetMutationImage/v1` from the bridged target,
   surface, canonical lineage key, generation, cohorts, and complete protected
   target projection; hashes its complete
   successor-canonical bytes including the LF; and proves that every image
   field and the manifest snapshot digest still match;
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
   unchanged in target, typed role-grant set, typed writer inventory, adapter,
   validity, guard capabilities,
   exact current deployment-admission policy reference, exact accepted
   qualification plan, exact passing qualification receipt, and complete exact
   deployment-tier result partition; resolves that deployment partition and
   the receipt's complete design-, implementation-, and release-tier
   partitions, requires every entry to equal the protected current `PASS`
   result, and locks the policy and every result pointer through commit;
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
10. resolves the support profile and all four exact controller-host,
    PostgreSQL-host, endpoint, and topology bindings; proves their byte equality
    with the attestation, protected reserved epoch row, and live deployment;
    rechecks both host operating-system references, the PostgreSQL and storage
    host references, and both stable boot-configuration references against
    their exact top-level profile components; rechecks the live actual boot
    identity against the attestation and clock envelope; proves the qualification plan, receipt, attestation,
    and live deployment closure-policy references are exact and, for every
    closure-derived disposition, proves that its case closure-policy reference
    and copied values are exact; rechecks the initial same-host absolute
    Unix-domain-socket boundary; and rejects every TCP, remote, managed,
    aliased, or drifted binding;
11. locks the exact protected epoch row, current-reserved-activation selector,
    and current-active-epoch pointer; requires the selector to name the row and
    the row to remain `RESERVED_FENCED`, unused, and byte-equal to the
    proposal, attestation's `proposed_publication_epoch`, current manifest
    binding's `reserved_publication_epoch`, target, surface, session,
    capability, deployment, and exact predecessor active epoch; proves no
    stage or lineage fact exists for it; and proves the adapter holds its
    capability preimage on this exact session; and
12. installs the session-local witness and atomically stores the authenticated
    manifest, initializes the successor target-generation slot from the exact
    manifest `TargetState.generation`, creates the unique lineage genesis under
    its exact `TargetState.lineage_key_digest`, and binds the manifest's exact
    selected and preserved cohort digests and recomputed snapshot digest;
    changes that one row from `RESERVED_FENCED` to `ACTIVE`, compare-and-swaps
    the current-active-epoch pointer from its exact predecessor to this epoch,
    and clears the current-reserved-activation selector. The protected
    interface permits that transition once and has no active-to-reserved or
    second activation path.

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
`R`, and `M` transaction locks the current deployment-policy and attestation
slots, revalidates the attestation's complete deployment-tier partition and
qualification receipt's complete design-, implementation-, and release-tier
partitions against their protected current `PASS` pointers, locks both
admission and fence rows, and recomputes the
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
manifest and genesis, or a manifest/genesis whose epoch is not active. An
uncertain abort leaves the exact `RESERVED_FENCED` row selected and blocks
another reservation until transaction recovery resolves it. Once recovery
proves noncommit and the proposal is no longer admissible, one protected
compare-and-set changes that selected row to permanent `ABANDONED_FENCED`,
clears the selector, makes its attestation noncurrent, and leaves the prior
active epoch unchanged. The epoch high-water mark never moves backward, so the
abandoned value cannot be reused, activated, renumbered, or reopened. Neither
abort nor abandonment restores a service or database role. Lost acknowledgement
resolves the combined activation by exact query; it never replays one component
independently.

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
through `ADOPT_ACTIVE_FENCE` while no manifest, genesis, stage, lineage fact,
or mutation receipt exists for that proposed epoch. The pre-fence adapter issues no fence
invocation and performs no external effect in that branch; the protected
adoption compare-and-swap durably selects the fresh manifest binding. A changed
proposal or fence, a stale expected binding, a second concurrent candidate, or
any already-created authority for the proposed epoch conflicts. Fresh inventory, target
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
- the exact encrypted rollback plaintext, ciphertext identity, registered
  legacy restore-content output, deterministic successor restore-payload
  conversion, and required decryption capability remain available and
  recomputable;
- the live target generation equals both the manifest's canonical
  `TargetState.generation` and the protected successor target-generation slot
  initialized at activation; its canonical lineage key and selected and
  preserved cohorts equal `TargetState`; and its complete reconstructed
  `TargetMutationImage/v1` digest equals `TargetState.snapshot_digest` and the
  verified legacy apply's exact postimage;
- the activated manifest's complete `TargetDatabaseIdentity` equals both the
  live target and `compatibility_target_identity` of the exact successor
  target-database reference bound by the activation, rollback plan, preimage,
  and `J`;
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

The rollback plan, its separate approval, and its authorization receipt bind:

- the exact closed `LEGACY_COMPLETE_APPLY` action binding and a new successor
  aggregate identity;
- `hindsight-postgresql-publication/v1`;
- the exact cutover manifest and activation;
- predecessor class `LEGACY_COMPLETE_APPLY`;
- the complete legacy chain root, raw artifact hashes, historical digests, and
  frozen reader-contract digests;
- the exact encrypted plaintext, ciphertext, legacy restore-content,
  deterministic conversion, successor restore-payload, payload digest, and
  decryption bindings;
- expected current postimage and the exact initialized successor
  `TargetGeneration`;
- the exact successor target-database reference whose canonical compatibility
  projection equals the manifest target identity;
- selected and preserved cohorts;
- successor genesis head and canonical lineage key;
- publication epoch, grant evidence, limits, and the one shared grant, plan,
  approval, authorization-receipt, `J`, and `R` expiry; and
- all ordinary rollback plan and budget ceilings.

Before that rollback plan is issued, the protected preimage-binding constructor
revalidates the selected frozen-reader chain and creates the complete immutable
successor `RollbackPreimageBinding/v1`, including the exact
`ProtectedRollbackCiphertext/v1`, decryption, target, surface, canonical
lineage key, legacy source plaintext digest and length, selected reader and
restore content, conversion, selected membership, and
`TargetRestorePayload/v1` digest. It
copies the exact manifest-selected ciphertext octets into
a protected PostgreSQL candidate byte row keyed by their digest and length,
computes both values from the stored bytes, and requires equality with the
typed body and its nested immutable-artifact descriptor. It stores both bodies
in the protected candidate registry with `authority=NONE`. The plan copies the
exact preimage-binding reference only after rechecking the byte row. This is
not cutover-time ingestion, a general historical byte capsule, publication
authority, or mutation authority; a descriptor, external file, body, or byte
row created after the plan cannot satisfy the action binding.

The approval names the exact plan, the authorization receipt names both, and
`J.action_binding` equals the plan action binding byte for byte. The grant's
issuance precedes plan creation, and its deadline exactly equals the plan,
approval, authorization-receipt, `J`, and `R` deadline. Their stable keys,
replay/conflict behavior, and timely-`R` rule are the successor acceptance
contract's ordinary operation-authority rules. Once this rollback has a durable
timely `R`, its `M` checks the grant, plan, approval, and authorization receipt
for exact identity, current selector, and nonrevocation without resampling that
shared deadline; independently timed deployment, evidence, clock, capability,
identity, and epoch gates retain their current-time checks. The legacy manifest
and preimage references are therefore authenticated transitively through that
same exact action binding; no compatibility approval or later independent
deadline can substitute.

The new rollback `J` transaction revalidates every predicate, independently
reconstructs the selected predecessor's complete authenticated dependency
closure, canonicalizes every raw-identity, historical-identity, and
reader-contract projection member, recomputes all three predecessor set
digests and the frozen reader's chain root, and proves that the selected
inventory observation still has `target_overlap=EXACT_TARGET` with exact
reader-derived target surface equality. It atomically proves that the approved
final-manifest body selected this exact predecessor, target, and class and that
the plan's preimage reference equals the still-valid pre-plan candidate body
and that the protected ciphertext bytes still verify by digest and length. The
same transaction creates `J`, its protected journal-preimage adoption, and the
protected-byte adoption; all become durable or all remain absent. The
adoptions store no general historical capsule and cannot exist without the
matching `J`. Only the complete
`J` chain makes the binding authoritative journal state. The executor never
reads an unbound legacy file or the nonauthorizing candidate as mutation
authority. The adopted PostgreSQL byte row is the authoritative ciphertext and
remains retained through the rollback's matching `M` and `V` or a separately
authorized permanent retirement. A private file survives only as a
nonauthoritative export or backup; its presence or loss cannot change bridge
eligibility.

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

The initial PostgreSQL compatibility state stores compatibility metadata plus
only the action-scoped rollback ciphertext required below:

- exact raw and historical digests;
- reader-contract and dependency identities;
- closure observations;
- cutover manifests and exclusion approvals;
- successor activation and lineage bindings; and
- an exact immutable, nonauthorizing pre-plan encrypted-preimage binding and
  digest-and-length-verified protected PostgreSQL ciphertext row and, only
  after matching `J`, their action-scoped journal adoptions for an admitted
  successor rollback.

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
  the exact named services, and append the exact persistent fence evidence and
  origin handoff and set the handoff's current pointer only in the transaction that marks the same generation
  `FENCE_ACTIVE` after the complete drain predicates hold. It cannot mutate
  target rows, create or advance successor stages, lineage, genesis, epoch, or
  successor manifest beyond that fixed origin binding,
  issue approvals, read or decrypt retained content, change the cohort,
  regrant a role, re-enable a service, or bypass manifest authentication or
  anti-replay. Ordinary runtime and admission roles cannot invoke its external
  fence operations directly;
- #73's sole admission role may author the exact activation proposal and call
  the metadata-only `ADOPT_ACTIVE_FENCE` interface while the occupied fence
  remains continuously closed. Adoption cannot invoke a fence effect, bypass
  the protected compare-and-swap, or write target, service, admission, ACL,
  manifest, genesis, stage, or lineage state. Its later-epoch mode may create
  only the exact deployment attestation, `RESERVED_FENCED` row, selectors, and
  per-epoch handoff atomically under the admission finalizer. The admission role cannot
  receive the capability, consume the proposal, invoke combined activation,
  create `R`, execute `M`, or alter historical bytes. The fence-adapter,
  closure, inventory, ordinary runtime, publication, mutation, and
  verification roles cannot call the adoption interface;
- the continuity-client adapter alone may consume the exact activation
  proposal and call the combined-cutover activation interface on the proposed
  continuity session. That one transaction validates the current approved
  manifest, installs the capability witness, creates genesis, and activates
  the bound publication epoch. The continuity client cannot author or adopt a
  proposal, issue an approval, call a fence-adapter operation, or bypass any
  manifest, fence, attestation, or clock predicate;
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
- exact closure-policy identity and copied case limits;
- closure attempt count and ceiling, next server-derived ordinal, terminal
  outcome, reserved-attempt deadline and recovery state, and explicit
  `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY` state;
- every blocker, exact exclusion, and exclusion validity;
- old-writer fence and live target snapshot identities;
- whether a unique `LEGACY_COMPLETE_APPLY` predecessor is selected;
- rollback-source availability, typed conversion and restore-payload status,
  and current mutation-image verification status;
- whether the genesis-only rollback bridge is eligible, ineligible, consumed,
  or permanently unavailable;
- the exact evidence-only action, separately approved successor action, or
  terminal refusal permitted next; and
- `authority=NONE` for every legacy and closure result.

Status takes no authority-bearing lock, creates no observation or manifest,
decrypts no preimage, activates no cutover, and imports no mutation-capable
runtime merely to inspect it.

## Acceptance obligations

The accepted
[acceptance-evidence contract](journal-acceptance-evidence.md) from
[#76](https://github.com/nisavid/agents/issues/76) requires proof of:

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
  to a successor stage interface;
- every corpus-plan acceptance, campaign registration, historical run
  registration, and tier evaluation binds
  `HistoricalCorpusPlan.historical_registry_digest` to the recomputed frozen
  `reader_registry_digest`, including the exact kindless schema-1
  `requeue-plan` member, member count, and member-vector digest;
- every registry member has one exact reader-execution binding from its full
  selector, wire contract, derived reader contract ID, and pinned source
  revision to the immutable `READER` tool implementation and its input,
  invocation, and output contracts; plans, cells, runs, results, reader
  outputs, `OR-LEG`, and tier evaluation all revalidate that same binding; and
- each required real-evidence run carries one typed real-artifact binding from
  plan cell and stimulus through run, private package, bounded mode-bearing
  projection, and independent review, including the governing policies and
  authenticated controlled-private acquisition or sanitized-real provenance.
  Public synthetic bytes cannot satisfy that partition.

### Historical dispositions and closure

- complete apply and rollback chains remain preserved and queryable historical
  evidence;
- pending and target-absent journals cannot resume or mutate;
- exact already-applied comparison appends one `EXACT_MATCH` closure
  observation without invoking a legacy mutation path;
- the first conclusive case outcome is terminal, mismatch is sticky,
  unable-to-verify remains nonauthorizing, and a lost closure acknowledgement
  returns the exact existing observation; every success output and inventory
  projection can represent the terminal
  `CLOSURE_EXHAUSTED_UNABLE_TO_VERIFY` disposition without aliasing it to a
  retryable unable result;
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
  qualified monotonic sample and exact policy. Tests independently recompute
  every checked millisecond-to-nanosecond conversion and the reservation,
  observation-call, connection, clock-validity, case-expiry, and attestation-
  expiry candidates, require the stored deadline to equal the earliest one,
  and prove that the latter three reserve the complete maximum resolution
  duration;
  a caller-selected, extendable, or arbitrarily distant deadline is rejected,
  clock uncertainty cannot create a reservation, and an unresolved slot cannot
  remain live past the resulting finite bound; every pre-expiry transaction's
  lock and statement timeout and its hard whole-transaction, idle-transaction,
  and adapter-enforced connection lifetime are capped by the conservatively
  measured remaining time and cannot preserve a late finalization right; a test
  that stalls after lock acquisition and another that idles between statements
  prove forced rollback releases the locks and lets the protected resolver
  finish after the bound;
- after reservation expiry while the same case-bound clock remains valid,
  abandonment uses a separately derived positive resolution deadline rather
  than the expired reservation remainder; it first closes the tagged observer
  session, requires a fresh qualified sample at or after that deadline, and
  its own finite transaction and connection guards let it fence the old
  generation and clear the unresolved slot without reviving target observation
  or successful finalization; pre-expiry observer invalidation instead permits
  only same-ordinal takeover; reboot, suspend uncertainty, clock loss, rollback,
  drift, or excessive error makes the case a nonauthorizing remediation blocker
  and cannot fabricate a resolution interval;
- match and mismatch require complete protected expected and observed
  `TargetMutationImage/v1` bodies and independently recomputed LF-inclusive
  SHA-256 postimage digests; unable and abandoned outcomes require
  `observed_postimage=NONE` and exact failure evidence, so no unavailable
  digest or image is fabricated;
- closure case and observation bodies accept only their exact v1 kinds,
  complete key sets, canonical bytes, tagged result unions, and protected-row
  identities; an absent row, unknown contract or version, malformed result,
  unattached equivalent bytes, body-digest mismatch, wrong case link, wrong
  terminal slot, or case/source/target/generation/cohort/postimage mismatch
  prevents the closure-derived disposition at pre-fence and activation;
- every exact-match and mismatch fixture supplies the exact v1 finalization
  sample and comparison bodies; every unable fixture supplies the exact v1
  finalization sample and failure body with one admitted category/source pair;
  and the abandonment fixture supplies its exact post-expiry v1 resolution
  sample and failure body. Pre-expiry invalidation fixtures instead supply the
  exact attested-invalidation and takeover sample and prove same-ordinal
  takeover without an abandonment body. Tests reject
  each wrong kind, version, case, ordinal, phase, deadline inequality,
  category/source pairing, comparison result, evidence link, unattached body,
  omitted body, extra body, or unrestricted generic reference both when the
  manifest disposition is prepared and when activation revalidates it;
- case creation accepts only the exact typed closure policy already bound
  through the support profile, qualification plan and receipt, and deployment
  attestation; copies every policy value exactly; and rejects zero or
  out-of-range observation-attempt, case-, reservation-, resolution-, or
  call-duration, observer-lease, lock, statement, whole-transaction,
  idle-in-transaction, or connection-lifetime limits, so no JSON or database
  zero convention can disable a finite guard or create a permanently unusable
  case;
- case creation requires `created_at_unix_ns < expires_at_unix_ns`, records the
  exact qualified creation sample and trusted upper bound, and derives that
  server-owned expiry with checked UInt128 arithmetic as the earlier of the
  exact current deployment attestation's expiry and the qualified policy case
  lifetime; it rejects before occupying the policy-bound stable key
  unless conservative time remaining covers at least one complete maximum
  reservation, maximum resolution, and clock-error margin; every finite
  transaction and connection guard is capped to its derived deadline;
  reservation,
  claim, observation, finalization, takeover, and post-reservation-expiry
  resolution each lock and revalidate that attestation and cap their deadlines
  by the case, attestation, and clock bounds, while expiry, revocation,
  replacement, target/adapter drift, or guard-capability drift makes the case a
  nonauthorizing remediation blocker that cannot admit a closure disposition;
- reservation-margin tests prove that no ordinal is consumed unless the
  exact protected deadline equals the earliest duration, call, connection,
  clock, case, and attestation candidate; every positive millisecond operand is
  multiplied exactly by 1,000,000 with checked UInt128 overflow rejection; and
  the conservatively bounded full maximum resolution duration still fits before
  the case, attestation, and clock-validity expiries. Equality with any bound is
  expired, and attestation invalidation before each phase
  produces no target observation, final result, new attempt, synthetic terminal
  observation, or successor authority;
- observer-lease tests prove that claim and takeover use a fresh explicit
  qualified sample, checked millisecond conversion and addition, the earlier
  of sample-plus-policy lease duration and reservation deadline, and a strict
  positive remainder; equality, overflow, a later stored deadline, or any
  attempt to extend the reservation is rejected;
- same-ordinal different-binding closure attempts conflict while the next
  server-derived ordinal remains possible below the case ceiling and before a
  terminal outcome;
- crash or deadline resolution records abandonment only after a fresh qualified
  sample's lower bound proves reservation expiry and fences the prior observer generation
  under the case and reservation locks, so a late observer cannot finalize;
  pre-expiry observer-incarnation invalidation permits only same-ordinal
  takeover and cannot record abandonment or exhaustion;
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
- every fence proposal's typed role-grant-set and writer-inventory references
  equal the deployment attestation, and its role and service sets are exactly
  the independently recomputed compatibility projections; inherited,
  assumable, `PUBLIC`, ownership, default-privilege, function-mediated,
  dynamic, background, replication, or service paths that are missing, extra,
  duplicate, unresolved, unclassifiable, or cross-surface block activation;
- identical manifest activation replays exactly and a different binding
  conflicts;
- deployment-attestation finalization for an unoccupied fence slot atomically
  allocates the epoch, inserts its `RESERVED_FENCED` row and attestation, and
  installs the exact current selector; for an occupied persistent fence, a
  later-epoch finalizer atomically adds those records and the exact per-epoch
  adoption handoff without repeating or reopening the legacy fence; the epoch,
  manifest, and genesis then activate atomically on the
  exact continuity session and clear that selector; an uncertain abort leaves
  the selected row fenced and blocks another reservation, while conclusive
  noncommit changes it to permanent `ABANDONED_FENCED` without epoch reuse;
  lost acknowledgement resolves without partial replay;
- inventory, reader, writer-fence, target generation, cohort, canonical target
  projection, snapshot, admission, freshness, or approval drift prevents
  activation; the oracle reconstructs `TargetMutationImage/v1` and recomputes
  the snapshot digest from its successor-canonical bytes including the LF;
- delayed registration, completion, aggregation, signing, or retry cannot
  refresh deployment evidence: every deciding projection retains its exact
  protected acquisition observation, and a missing, substituted, cross-boot,
  wrong-envelope, or over-age acquisition blocks every compatibility gate;
- before any external fence step, the read-only pre-fence gate canonicalizes
  and recomputes the basis and final bodies and digests; proves their link;
  independently recomputes `target_surface_digest`, `inventory_digest`, and
  every selected predecessor projection-set digest from their exact canonical
  member preimages;
  recomputes every exclusion and approval body and digest; authenticates every
  manifest and exclusion receipt and principal; rejects any missing, extra,
  duplicate, conflicting, or unlinked envelope member; validates the exact
  target and proposal from that graph; locks and validates the exact
  manifest-bound deployment attestation, typed role-grant set, typed writer
  inventory, validity, and revocation
  state; and requires conservative `U_prefence` below its expiry and every
  manifest, freshness, exclusion, and approval expiry;
- the gate's trusted-adapter-local invocation binds a fresh adapter incarnation
  and nonce, caps its deadline at the conservative monotonic equivalent of the
  earliest bound, and becomes invalid across adapter restart; consumption takes
  one exclusive effect-attempt lock through the first PostgreSQL transaction,
  rejects concurrent or repeated use, and revalidates the exact clock envelope,
  deployment attestation, proposal, target, typed role-grant set, typed writer
  inventory, and adapter before any
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
  constructs the exact canonical `PersistentLegacyFenceEvidence` and origin
  handoff at fence completion; each `ActiveFenceManifestAdoption` references
  that epoch-independent evidence, advances a contiguous acyclic handoff chain,
  and durably selects only the fresh cutover/manifest/epoch binding;
  exact-replays after lost acknowledgement only while
  that body remains current; returns `SUPERSEDED_BINDING` when generation
  `n+1` is current and generation `n` retries; and rejects stale bindings,
  concurrent candidates,
  changed proposal/generation/evidence, restored writers or services,
  attestation drift, and any authority for the proposed epoch;
  after `ACCESS_REVOKED`, new legacy login, connection, and write acquisition
  are blocked but already-authorized work is not treated as quiescent;
- crash and lost-acknowledgement tests at every drain step prove that all
  sessions, statements, transactions, prepared transactions, replication
  paths, background writers, and inheritable grants are either exactly fenced
  or block progress; only zero-live-writer evidence under unchanged ACLs may
  record `SESSIONS_DRAINED`, and the target is reobserved afterward;
- pause, revocation, expiry, and drift tests before every external observation,
  cancellation, termination, drain wait, and service disable prove that each
  effect requires a fresh qualified sample and current locked authority through
  durable outcome recording; no earlier invocation, sample, pending row, or
  progress record authorizes continuation;
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
- separate persistent-evidence vectors change each of `realized_admission_digest`,
  `realized_acl_digest`, `zero_live_writer_evidence_digest`,
  `drain_observation_generation`, and
  `service_disable_evidence_set_digest`, recompute the persistent body digest,
  and prove every origin or adoption handoff and activation reject the mismatch
  against the exact locked evidence; an implementation-native projection or
  equivalent caller testimony is never accepted;
- activation atomically binds one protected legacy-writer fence generation to
  admission; `J`, `P`, `R`, and `M` each lock and revalidate its live service,
  admission, ACL, and complete writer-set drain evidence, and restored login or
  connection admission, a write regrant, a newly live writer path, fence loss,
  or evidence-generation drift refuses every stage;
- activation and legacy-rollback vectors perturb each database name, database
  OID, and PostgreSQL system-identifier field and require exact equality
  between the manifest `TargetDatabaseIdentity`, live target, and bijective
  successor `TARGET_DATABASE` projection; a partial or digest-only match is
  rejected;
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
  `ADOPT_ACTIVE_FENCE`; that the interface performs no external fence effect
  and exposes no partial later-epoch authority outside its atomic finalizer;
  and that fence-adapter, closure, inventory,
  ordinary runtime, publication, mutation, and verification roles are denied;
- origin/adoption canonicalization vectors prove that the digest field is
  absent from its own preimage, generation zero has no prior digest, every
  positive generation names the exact prior body digest, changed persistent
  fence evidence changes its digest and every valid handoff reference, skipped
  generations and cycles are rejected, and activation recomputes the complete
  protected chain and exact current record-to-manifest linkage;
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
- privilege tests prove that the continuity-client adapter is the sole caller
  of combined activation and consumes the exact proposal on the proposed
  session; the admission role can author the proposal and perform only the
  metadata handoff and exact later-epoch reservation effects admitted by
  `ADOPT_ACTIVE_FENCE`, and cannot install the witness, create genesis, or
  activate an epoch;
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
- `ROLLBACK_UNAVAILABLE` and any missing restore source, decryption, ciphertext
  verification, typed legacy content, deterministic payload conversion, or
  restore-payload recomputation block activation and cannot be converted to an
  exclusion;
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
  include the exact kindless `requeue-plan` member and its
  `3f2089bacd91e4591d7a5939cc274d7ca7ae6600466718504b1a6c5102b58245`
  selector digest, reject that member as a discovery root or under another
  role, recompute the complete member count, member-vector digest, and
  registry-body digest, and distinguish the shared schema-1 `legacy-requeue`
  and `post-abort`
  reference-plan variants; they also accept each exact schema-12
  `phase-repair-v8`/`phase-repair-v9`, schema-15
  `provider-capability`/`legacy-hatchery-capability`, and pending-marker
  `two-field` artifact variant with reference-plan `NONE`, and reject a missing,
  swapped, or invented variant; they separately expand every shared-lifecycle
  plan tuple before computing the registry digest and reject a missing,
  narrowed, reordered, or outer-schema-equals-reference-schema table;
- recursive dependency vectors require every shared-lifecycle and claim-release
  path that names the kindless requeue plan to select that member, execute its
  exact bound reader, and cover its success and each registered failure in the
  historical corpus; omission, role substitution, action drift, or a parent
  reader that validates the dependency inline is rejected;
- reader-execution vectors change the binding, tool, immutable implementation,
  member selector, wire contract, derived contract ID, and source revision one
  at a time, recompute the containing output, and require inventory,
  corpus-plan, run-result, and `OR-LEG` validation to reject every mismatch;
- acceptance-binding tests require `historical_registry_digest` to equal that
  recomputed frozen `reader_registry_digest` at corpus-plan acceptance,
  campaign registration, every historical run registration, and tier
  evaluation, and reject zero, stale, narrowed, reordered, differently
  expanded, or successor-canonicalized values;
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
  restore source or payload, invalid source or conversion recomputation, fence loss,
  activated-manifest mismatch, bound evidence drift, or non-genesis successor
  lineage prevents rollback `J`;
- the new approval binds every legacy, manifest, preimage, target, cohort,
  genesis, epoch, grant, deadline, and budget input;
- the exact encrypted action-preimage binding is created and verified with
  `authority=NONE` before rollback-plan issuance; its exact ciphertext bytes
  occupy the digest-and-length-verified protected PostgreSQL candidate row;
  rollback `J` independently reconstructs the authenticated predecessor
  closure, recomputes its raw, historical, and reader-contract set digests and
  frozen chain root, and atomically adopts the binding and byte row without
  creating a general historical byte capsule or synthetic predecessor `M` or
  `V`;
- a descriptor-only, missing, truncated, digest- or length-mismatched,
  private-file-only, replaced, or retired protected ciphertext prevents plan
  issuance or rollback `J`; an intact adopted PostgreSQL byte row remains
  eligible despite private-file loss;
- interruption before and after each new `J`, `P`, `R`, `M`, and `V`
  commit follows the restart matrix;
- lost acknowledgement never repeats a committed stage or target mutation;
- exactly one rollback restores the preimage and advances generation once;
- two rollbacks racing from genesis yield one `M` and one terminal
  `LINEAGE_HEAD_DRIFT`;
- any successor `M` permanently closes legacy predecessor admission, even if
  later target bytes coincidentally match the historical postimage; and
- preserved completed, failed, and out-of-cohort rows remain unchanged.

### Deployment endpoint binding

- the initial support profile carries exactly one complete canonical effective
  PostgreSQL socket-directory member through configuration, live projection,
  endpoint, attestation, and compatibility gate;
- the complete endpoint path derives only from the member's symlink-resolved
  absolute path and configured port; and
- a relative path, dotted component, repeated separator, noncanonical trailing
  separator, symlink retarget, missing or nondirectory target, alternate
  spelling, added, removed, duplicated, reordered, or device/file-identity-
  drifted member refuses before a fence effect.

### Preservation and authority separation

- compatibility writes no historical file and preserves every source byte and
  frozen reader contract;
- PostgreSQL contains no general historical byte capsule;
- only closure metadata, cutover and exclusion metadata, successor state, and
  an approved rollback's action-scoped protected ciphertext and encrypted
  preimage are added;
- private files remain nonauthoritative source artifacts, exports, or backups;
  rollback after `J` resolves only the adopted protected PostgreSQL bytes;
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
- The accepted [#76 evidence record](journal-acceptance-evidence.md) defines
  falsifiable design, implementation, release, and deployment evidence
  obligations.
- [#77](https://github.com/nisavid/agents/issues/77) owns independent assessment
  of the integrated design.
- [#78](https://github.com/nisavid/agents/issues/78) owns Ivan's final design
  acceptance and the gate to a separate implementation-planning map.

Only after those gates are satisfied may a separately authorized effort
translate this record into successor schemas, protected interfaces, frozen
reader packaging, deployment admission, cutover sequencing, tests, candidate
assembly, and live recovery procedures.
