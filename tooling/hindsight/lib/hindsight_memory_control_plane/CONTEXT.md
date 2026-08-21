# Hindsight Memory Control Plane

This context separates desired memory policy, observed live state, migration evidence, and mutation authority so inspection cannot silently become activation or cutover.

## Language

**Validated inventory**:
The closed, digest-bound desired state that identifies profiles, providers, banks, harnesses, and policy. It is authoritative for declared provider identity, not for observed live bank state.
_Avoid_: Configuration file, live config

**Live bank snapshot**:
A complete read-only observation of a named source and candidate bank through documented Hindsight API reads. It contains no adapter watermark or mutation authority.
_Avoid_: Inventory, migration export

**Adapter watermark snapshot**:
A read-only observation of adapter retain progress captured independently before and after live bank discovery. Equality detects observed watermark drift but is not the atomic consistency boundary.
_Avoid_: Bank watermark, import checkpoint

**Adapter discovery generation**:
An opaque, monotonic adapter revision captured before and after every live-bank discovery read. It changes for every committed mutation that can affect the source or candidate banks, invalidation archive, hooks, schedules, operation state, or retain watermark. Equality therefore proves that the multi-read snapshot did not span a relevant committed write. An adapter that cannot guarantee complete coverage for every one of those surfaces must not expose a generation; discovery fails closed when the generation is absent, incomplete, or changes.
_Avoid_: Inventory digest, adapter watermark

**Offline package manifest**:
An approved, immutable description of projected migration content and its coverage, provenance, curation, and artifact digests. The manifest binds an external package without copying that package into Git.
_Avoid_: Shadow plan, live inventory

**High-water coverage manifest**:
A controller-authored disposition of every document observed in a stable live bank snapshot. Read-only discovery derives it independently from the approved offline package.
_Avoid_: Offline package manifest, curation manifest

**Shadow plan**:
A digest-bound migration proposal assembled from validated inventory, approved offline evidence, and live observations read within one unchanged opaque, server-backed adapter discovery generation. That generation is the atomic source-consistency contract; adapter watermarks are secondary drift evidence only. The plan is always unapproved and carries no mutation authority.
_Avoid_: Apply plan, migration approval

**Execution window**:
A fixed, nonrenewing duration calculated from the exact selected operation IDs, their persisted retry counts, the effective worker concurrency, bounded task timeouts, retry waits, shutdown attempts, and transaction margins. Authorization time anchors the absolute deadline. Resume and progress do not move it.
Retry and defer timestamps are accepted only when they are timezone-aware, no farther ahead than the plan-bound retry delay, and strictly before that deadline.
_Avoid_: Approval lifetime, renewable lease

**Recovery epoch**:
A monotonic transition consumed by a verified post-abort recovery transaction. That transaction may release exact-worker-owned pending or processing rows and may reset exact-worker-owned failed rows for one fresh attempt cycle. Schema 10 permits zero to one under unchanged legacy installation authority. Schema 11 carries verified data-identity rebind authority and permits either the first zero-to-one transition or one additional one-to-two transition when the prior retry lineage is authenticated. Schema 12 permits the final two-to-three transition only when both earlier transitions and their receipts are authenticated. No transition can be replayed, renewed, or advanced beyond epoch three.
_Avoid_: Retry count, worker attempt

**Retry lineage**:
The digest-bound record of each selected row's persisted retry count, consumed attempts, available attempts after each permitted recovery, cumulative ceiling, and whether that recovery reset the failed row.
_Avoid_: Provider retry policy, execution window

**Recovery handoff**:
The closed link from a verified post-abort plan and its application and verification receipts into a fresh exact-drain plan. It binds the recovery epoch, exact recovered IDs, checkpoint digests, preserved rows, generation, and candidate release without carrying task payloads or raw errors.
_Avoid_: Resume token, renewed authorization

**Failure classification**:
A closed, payload-free database projection that groups selected operation failures by a bounded cause family and error digest. It carries only the family, digest, and occurrence count; raw exception text and task payloads remain outside the monitor surface.
_Avoid_: Error message, stack trace

**Verified data-identity rebind handoff**:
The closed proof that an approved rebind moved one installation from an exact prior state and durable data identity to an exact verified post-state and durable data identity while preserving the reference plan's observed legacy identity, binding generation, release, PostgreSQL system identifier, and database continuity. Only schema 11 recovery may bridge those two installation authorities.
_Avoid_: Ordinary installation verification, dual identity acceptance
