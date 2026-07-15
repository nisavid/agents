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
