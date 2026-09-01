# Durability and clock guarantees for approval-gated Hindsight journals

This evidence note answers one question: which guarantees can the supported
Hindsight environments provide for an authenticated journal that must become
durable before a nonrenewing approval expires? It does not select a storage
design or authorize a deployment change.

## Scope and result

The repository declares managed operation on macOS LaunchAgents and Linux
systemd-user, with Python 3.11 or newer, and pins managed Hindsight commands to
`hindsight-embed==0.8.4` (`tooling/hindsight/README.md` lines 120–146 and
`tooling/hindsight/docs/adoption.md` lines 1–26 at `18c921e4`).
The declared configurations do not constrain the journal or database roots to
a particular filesystem, mount type, storage medium, or cache policy. The
examples use a local pg0 data root, but that is a path convention rather than a
durability contract
(`tooling/hindsight/examples/portable-consumer/launchd-installation.json` lines
1–74 and
`tooling/hindsight/examples/portable-consumer/systemd-user-installation.json`
lines 1–63 at `18c921e4`).
Consequently, no repository-wide filesystem-specific namespace or durability
guarantee can be attributed to the declared environments.

**Result:** the existing primitives do not establish the fixed contract across
the declared environments. They can establish useful conditional guarantees,
especially against process termination and on a correctly configured Linux
filesystem/storage stack, but at least five material gaps remain:

1. The repaired writer treats an `fsync`ed unauthenticated pending marker as the
   durable deadline boundary, then creates, syncs, and publishes the final
   authenticated journal without another clock check. The stored timestamp is
   therefore earlier than the actual final publication boundary.
2. The macOS implementation calls `fsync`; Apple's own documentation says a
   successful `fsync` can still leave some or none of the data after an OS crash
   or power loss.
3. The host clock is `time.time()`, which Python documents as adjustable and
   able to move backward. There is no trusted or rollback-resistant clock
   binding.
4. PostgreSQL commit durability depends on server settings and the underlying
   storage. The repaired path neither sets nor validates those durability
   settings.
5. The repository declares no filesystem or mount support matrix. In
   particular, the repaired source requests macOS `RENAME_EXCL` and
   `RENAME_SWAP`, but the reviewed Apple documentation makes support a volume
   property and does not establish the exact atomic semantics across every
   declared macOS environment.

This note evaluates the repository declaration at `18c921e4b2715dd35ae954c907a2fe62c5dc07ad`
and the repaired Hindsight source and contracts at
`7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab`. It does not use conclusions from
the earlier independent assessment pinned to
`e30ee145efab8aeb766a6b2bec02c5e0c3f6a82e`; no comparison to that assessment is
made.

The repaired candidate retains Embed 0.8.4 but additionally defaults and
allowlists its nested Hindsight API runtime to 0.9.2
(`tooling/hindsight/README.md` lines 158–167 and
`tooling/hindsight/bin/hindsight-embed-uvx` lines 76–88 at `7b165b3e`). This
version difference matters when identifying datastore dependencies; neither
source revision declares a concrete PostgreSQL engine version or live
durability configuration.

For this note, “authenticated” means that a journal satisfies the repaired
closed-schema verifier and its approval/evidence digest chain. The
cryptographic strength of that predicate is outside this durability-and-clock
ticket.

## What the repaired journal flow actually commits

The repaired writer at `7b165b3e` performs this sequence:

1. Reject an already-expired wall-clock sample.
2. Write and `fsync` a fixed pending marker, create it at the final path with a
   conditional rename, then `fsync` the parent directory.
3. Sample `time.time()` again; call that sample `durable_at` and reject it when
   it is at or after expiry or earlier than the first sample.
4. Build the final authenticated journal using `int(durable_at)`, write and
   `fsync` those bytes, exchange them with the pending marker, and `fsync` the
   parent directory.
5. Return the journal. Only then does the caller verify it and enter the
   PostgreSQL mutation path.

The namespace helper selects Darwin `renameatx_np` or Linux `renameat2`, requests
exclusive creation for the pending marker, requests exchange for final
publication, verifies the displaced marker's inode, and attempts a reverse
exchange if that verification fails (`tooling/hindsight/bin/hindsight-memory`
lines 705–768 at
`7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab`). The writer is in the same file at
lines 839–1013. The journal-before-database ordering is in
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py`
lines 17261–17288 at that full commit.

The final journal is a closed, digest-bound record. It binds the plan,
authorization receipt, rollback bundle, historical reference plan and journal,
frozen progress, grant ledger, pre/post generations, and selected and preserved
row sets
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py`
lines 16136–16195 at `7b165b3e`).
Rollback derives a distinct approval digest from the action, plan, verified
application chain, preimage, postimage, and grant evidence, and writes a
separate rollback journal with those bindings
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py`
lines 17594–17774 at `7b165b3e`).
The command adapter sends both application and rollback journals through the
same deadline-aware file writer (`tooling/hindsight/bin/hindsight-memory` lines
15115–15134 at `7b165b3e`), so the persistence and clock gaps apply to both.
Those are valuable authentication and replay properties. They do not make the
bytes durable or the timestamp trustworthy.

The deadline mismatch is explicit rather than hypothetical. The source test
advances its fake clock to expiry during the second rename, then expects the
writer to return a journal whose `applied_at` is the earlier pending-marker
sample
(`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_cli.py`
lines 450–503 at `7b165b3e`).
The accompanying document likewise permits finalization to cross expiry after
the pending marker's “durable start”
(`tooling/hindsight/docs/exact-drain-stopped-run-reconciliation.md` lines
98–115 at `7b165b3e`).
This is source-shape evidence only. The test does not prove filesystem
durability, crash recovery, clock integrity, or deployment behavior.

Under the fixed requirement, an unauthenticated marker is not publication of
the authenticated journal, and its timestamp cannot substitute for the later
publication. The current flow therefore fails the deadline condition even on a
storage stack where every sync works perfectly.

This boundary matters because execution rejects an expired plan when no
application journal exists, but an exact existing journal permits continuation
of the already-started sequence; the database mutation follows that check
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py`
lines 17131–17157 and 17261–17288 at `7b165b3e`). A late or non-durable first
journal would therefore convert expired approval into replay authority that the
fixed contract does not grant.

## Observable completion is not durable persistence

The source requests create-if-absent and exchange operations through different
extended rename APIs on the two declared operating systems. On Linux,
`renameat2` documents atomic replacement and atomic `RENAME_EXCHANGE`, while
also documenting filesystem-specific flag support and NFS ambiguity ([Linux
`rename(2)`](https://man7.org/linux/man-pages/man2/rename.2.html)). That supports
a conditional Linux namespace-atomicity claim, not a cross-platform one.

On macOS, Apple's public XNU header declares `renameatx_np`, defines
`RENAME_SWAP` as `0x00000002`, and defines `RENAME_EXCL` as `0x00000004`
([Apple XNU
`stdio.h`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/stdio.h#L32-L50)).
Apple's archived APFS guide lists `renamex_np` and `renameatx_np` as safe-save
APIs, and current Foundation documentation separately exposes whether a volume
supports `RENAME_SWAP` or `RENAME_EXCL` ([Apple APFS Tools and
APIs](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/APFS_Guide/ToolsandAPIs/ToolsandAPIs.html),
[Apple
`volumeSupportsSwapRenaming`](https://developer.apple.com/documentation/foundation/urlresourcevalues/volumesupportsswaprenaming),
[Apple
`volumeSupportsExclusiveRenaming`](https://developer.apple.com/documentation/foundation/urlresourcevalues/volumesupportsexclusiverenaming)).
Those sources establish the API and flags, but do not specify their exact atomic
semantics for every filesystem admitted by the repository. The source does not
validate the volume properties. Exact macOS extended-rename semantics are
therefore unverified here. On either platform, namespace atomicity would still
say nothing by itself about survival after a crash.

The strongest local completion signal in the repaired writer is return from
the final parent-directory `fsync`. Its meaning differs by environment:

| Failure boundary | What the current filesystem flow can establish | What it cannot establish |
| --- | --- | --- |
| Application/client process termination after writer return | The final file write, file `fsync`, requested exchange, and directory `fsync` all returned. On a Linux filesystem supporting the requested flag, the documented exchange is atomic. On macOS, the source observed successful calls, but the exact extended-rename atomicity remains unverified. | Successful system calls and observable completion do not prove that the bytes or namespace update reached nonvolatile media. |
| Application/client process termination before final publication | Before pending publication there is no journal. Between pending publication and final exchange, the fixed marker may remain and the journal verifier rejects it. The database mutation has not started. After the final exchange request but before writer return, final bytes may be visible, but the caller still has not entered the database path. | A visible file after an interrupted call is not by itself evidence that the final directory update is crash-durable. On macOS, this research also cannot attribute the Linux API's atomic visibility guarantee to the extended rename. |
| Linux OS crash or reboot after writer return | Conditional on a local filesystem, kernel, and device that implement successful `fsync` as documented, file `fsync` plus directory `fsync` is the available primitive for recovering both the bytes and their name. Linux says `fsync` flushes file data and metadata, including a disk cache, blocks until the device reports completion, and requires a separate directory `fsync` for the directory entry ([Linux `fsync(2)`](https://man7.org/linux/man-pages/man2/fsync.2.html)). | The repository does not exclude filesystems or devices that do not honor those semantics. Linux notes that older kernels and lesser-used filesystems might not flush disk caches. A device can also misreport completion. |
| macOS OS crash or reboot after writer return | The final bytes and name were submitted through the source's extended-rename and `fsync` sequence, and the function observed successful returns. | Apple says `fsync` flushes host buffers to the drive, but after an OS crash the application may find only some or none of its data. The source never invokes `F_FULLFSYNC`; its exact extended-rename guarantee is also unverified ([Apple `fsync(2)`](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html), [Apple `fcntl(2)`](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fcntl.2.html)). |
| Sudden power loss after writer return | On a verified Linux stack, the documented `fsync` protocol can cover this case to the point where the storage device truthfully acknowledges a cache flush. PostgreSQL can likewise be configured to wait for durable WAL. | The current macOS file protocol does not establish this guarantee. Neither platform declaration attests the filesystem, controller, drive cache, SSD flush behavior, or power-loss protection. PostgreSQL explicitly assigns storage-integrity assumptions to the administrator ([PostgreSQL 18 WAL reliability](https://www.postgresql.org/docs/18/wal-reliability.html)). |

Application/client termination is distinct from a PostgreSQL server-process,
OS, or power failure. A client can terminate after PostgreSQL commits but before
it observes the response. The repaired path's generation and exact-postimage
checks support idempotent replay of that ambiguous outcome, but they do not
decide whether the committed rows survived any later failure. That remains a
PostgreSQL durability question.

## Clock guarantees

The journal writer uses `time.time()` for both deadline samples. Python 3.11
defines it as epoch time, but warns that it can return a lower value after the
system clock is set backward and that some systems provide no better than
one-second precision ([Python 3.11 `time.time`](https://docs.python.org/3.11/library/time.html#time.time)).
Python's clock metadata expressly distinguishes an adjustable clock—changed by
NTP or an administrator—from a monotonic clock ([Python 3.11
`get_clock_info`](https://docs.python.org/3.11/library/time.html#time.get_clock_info)).

The writer detects only one narrow rollback: the second wall-clock sample being
earlier than the first. It cannot detect a rollback before the first sample, a
rollback that still leaves the second value no earlier than the first, or any
clock movement after the second sample while final bytes are created and
synced. It also does not bind the host clock to the clock that issued the
approval.

Python provides `time.monotonic()`, which cannot go backward and is unaffected
by system-clock updates, but its reference point is undefined and only
differences between calls are valid ([Python 3.11
`time.monotonic`](https://docs.python.org/3.11/library/time.html#time.monotonic)).
It can bound elapsed time between calls while the host is executing within one
boot, without wall-clock rollback. The cited Python contract does not state
whether it advances while each supported host is suspended, and the repository
declares no platform-specific clock support matrix; suspend inclusion is
therefore an unresolved support input. By itself the clock also cannot compare
to an epoch expiry, prove or bound the issuer/host offset, or supply a durable
time basis across reboot. The supported runtimes therefore provide no
source-declared trusted, rollback-resistant clock adequate for the fixed expiry
contract.

## Existing datastore primitive

The repaired mutation path uses an asyncpg PostgreSQL connection and a
`SERIALIZABLE` transaction with exact row and generation compare-and-swap
checks
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py`
lines 9043–9314 at `7b165b3e`).
Serializable isolation orders concurrent effects as if transactions ran one at
a time; it is an isolation guarantee, not a persistence guarantee
([PostgreSQL 18 transaction isolation](https://www.postgresql.org/docs/18/transaction-iso.html#XACT-SERIALIZABLE)).

PostgreSQL can provide a local durable commit when all of the relevant
conditions hold:

- `fsync=on`, so PostgreSQL forces updates to disk and can recover after an OS
  or hardware crash;
- `synchronous_commit` is not `off` for the transaction, so success waits for a
  local WAL flush;
- `full_page_writes=on`, so crash recovery can repair partial page writes;
- `wal_sync_method` is appropriate to the operating system and storage—current
  PostgreSQL documentation specifically identifies `fsync_writethrough` for
  macOS write caching; and
- the filesystem and storage stack honor flushes and protect nonvolatile data.

These conditions and their failure modes are documented in [PostgreSQL 18 WAL
settings](https://www.postgresql.org/docs/18/runtime-config-wal.html#RUNTIME-CONFIG-WAL-SETTINGS)
and [PostgreSQL 18 WAL reliability](https://www.postgresql.org/docs/18/wal-reliability.html).
In particular, PostgreSQL says `synchronous_commit=off` can report success
before a transaction is safe from a server crash, and `fsync=off` can lead to
unrecoverable corruption after a system crash or power loss.

The distinctions below are PostgreSQL capabilities under the documented
conditions, not evidence of any configured Hindsight installation:

| Failure boundary | Conditional PostgreSQL guarantee | Remaining limit |
| --- | --- | --- |
| Application/client process termination or disconnect | The server may already have committed even when the client never observes success. If the rows survive, the repaired exact-generation and postimage checks can resolve the logical outcome on retry. | Client-observed completion is not a durability acknowledgment, and replay checks do not prove persistence. |
| PostgreSQL server-process termination or database-server crash while the OS and storage remain available | With non-`off` `synchronous_commit`, `fsync=on`, an appropriate `wal_sync_method`, `full_page_writes=on`, and storage that honors the operations, success follows a local WAL flush from which recovery can preserve the commit. PostgreSQL expressly says `synchronous_commit=off` can leave a reported commit unsafe from a server crash. | The guarantee is conditional on the settings in effect for that transaction and on correct recovery and storage behavior. With `synchronous_commit=off`, recent acknowledged transactions can be lost even though the recovered database remains consistent. |
| OS crash or reboot | Under those same conditions, PostgreSQL's configured WAL synchronization supplies the recovery boundary and `full_page_writes` protects against partial page writes during crash recovery. | No repository declaration or inspected source establishes the settings, WAL method, filesystem behavior, or actual recovery result. |
| Sudden power loss | The same settings can preserve an acknowledged commit only when the filesystem, controller, drive, and every write cache truthfully honor flushes or retain their contents without power. | PostgreSQL assigns verification of storage-component integrity to the administrator; volatile or dishonest caches can defeat the guarantee. |

The repaired connector sets only `application_name` and
`default_transaction_read_only`; its deadline helper sets transaction, lock,
and statement timeouts. It does not set or validate `fsync`,
`synchronous_commit`, `full_page_writes`, or `wal_sync_method`
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py`
lines 1705–1729 and 10118–10149 at `7b165b3e`).
`synchronous_commit` can change per transaction, so a documented default alone
would not close this gap.

Upstream `hindsight-api==0.9.2` depends on
`hindsight-api-slim[all]==0.9.2`, whose `embedded-db` extra declares only the
lower bound `pg0-embedded>=0.15.0` ([upstream v0.9.2 API
metadata](https://github.com/vectorize-io/hindsight/blob/v0.9.2/hindsight-api/pyproject.toml#L5-L13),
[upstream v0.9.2 slim API
metadata](https://github.com/vectorize-io/hindsight/blob/v0.9.2/hindsight-api-slim/pyproject.toml#L134-L142)).
No actual embedded-database version or durability configuration was observed in
this research. A live/default configuration must not be inferred from the
version floor. The PostgreSQL 18 documentation cited here describes available
database semantics; it is not evidence that a particular installation runs
PostgreSQL 18 or uses its durable settings.

PostgreSQL is thus a potentially stronger existing durability primitive than
the current macOS file writer, but only under attested server and storage
conditions. It does not rescue the present journal because that journal is
committed to a separate filesystem path before the database transaction.

## Contract fit

| Fixed requirement | Finding at `7b165b3e` |
| --- | --- |
| Genuine durable authenticated publication before nonrenewing expiry | **Not established.** The pre-expiry durable boundary belongs to a non-authorizing marker; final authenticated bytes are published and synced later. macOS `fsync` also lacks the required crash/power-loss guarantee, and no repository-wide filesystem or namespace guarantee is declared. |
| No earlier timestamp substituted for actual publication | **Not met.** `applied_at`/`recorded_at` is the sample after pending-marker sync, not a time at or after final authenticated publication. |
| Separate apply and rollback approvals | **Represented in the source contract.** Rollback has a distinct action-bound approval digest and journal. This is independent of storage and clock adequacy. |
| Exact historical evidence and replay bindings | **Represented in the source contract.** The journal and rollback chain bind the exact referenced artifacts, row-set digests, generations, and grant evidence. |
| No retry, cohort, or budget expansion | **Represented by the planned exact-CAS/replay shape, not proved as a deployment guarantee here.** The durability research found no storage primitive that would justify expanding those bounds, and any later component must preserve them unchanged. |

## What ordinary source tests cannot prove

The inspected tests use temporary directories, injected clocks, patched
functions, and a Python `BaseException` as a simulated crash
(`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_cli.py`
lines 255–503 at `7b165b3e`).
They can test sequencing, cleanup, create-only behavior, rejection of the
pending marker, digest binding, and replay decisions. They cannot establish:

- that a kernel, filesystem, hypervisor, controller, or drive honored a flush;
- post-crash or post-power-loss recovery on any supported filesystem;
- that a device truthfully reported nonvolatile completion;
- actual PostgreSQL version, settings, WAL method, or storage behavior;
- host/issuer clock agreement, NTP behavior, administrator clock changes, or
  clock continuity across suspend and reboot; or
- the physical persistence time of the final authenticated bytes.

A passing source test must not be used as durability or deployment evidence.
Power-cut and crash-recovery testing can falsify a claimed configuration, but
even repeated success does not replace a documented support matrix and verified
runtime/storage configuration.

## Capability missing from the fixed contract

If no existing, attested configuration can meet the contract, a later durable
storage component would need to expose one semantic capability: an atomic,
deadline-conditioned, create-only commit of the **final canonical authenticated
journal**, with a trustworthy commit time and a durability acknowledgment.
Specifically, it would need to:

- make the expiry check part of one storage-controlled durable commit point for
  the final bytes, so no earlier marker timestamp or pre-flush check stands in
  for actual publication;
- use a time source whose relationship to the approval issuer and resistance to
  rollback are specified, including behavior across suspend, restart, and
  offline operation;
- acknowledge success only after the exact bytes and their committed identity
  meet documented OS-crash and power-loss semantics on an explicitly supported
  storage configuration;
- provide create-if-absent/idempotent lookup so an ambiguous client outcome can
  be resolved by reading and authenticating the exact committed record;
- keep apply and rollback as distinct action/approval records and preserve every
  historical, cohort, generation, grant, and replay binding; and
- refuse unsupported or unverifiable filesystem, database, cache, and clock
  configurations rather than weakening the guarantee.

This capability description is not a decision to build, select, or provision a
new component. In particular, merely moving the existing pre-commit clock check
into a database transaction would not prove that durable WAL completed before
expiry; the deadline condition and the defined durable commit point must be
joined by the storage contract itself.

## Unknowns and later human decisions

The following facts are not declared or were not verifiable within this source
research:

- the supported macOS versions and filesystems, each volume's support for
  `RENAME_EXCL` and `RENAME_SWAP`, the exact atomic semantics of those operations,
  and a current Apple primary specification for a complete file-plus-directory
  protocol that survives both OS crash and power loss; Apple's archived `fsync`
  page directly conflicts with treating the current calls as sufficient;
- the supported Linux filesystems, mount options, local-versus-network rule,
  virtualization layer, controller, and drive-cache behavior;
- the exact pg0/PostgreSQL version resolved in each installation and the live
  values and provenance of every durability setting;
- whether clock rollback by a privileged local actor is in the threat model, the
  maximum permitted issuer/host clock error, whether the selected elapsed-time
  clock includes host suspend on each platform, and what must happen across
  reboot or loss of time synchronization;
- whether journal and database mutation must share one atomic durable
  transaction or whether a durable journal followed by exact idempotent replay
  remains acceptable;
- how an indeterminate client outcome is classified when the storage commit may
  have succeeded but no acknowledgment was observed; and
- which instant “publication” means for the fixed contract: namespace
  visibility, the storage serialization point, or acknowledged nonvolatile
  persistence. The implementation and approval issuer must use the same
  definition.

Those inputs are required before a later architecture decision can claim the
fixed guarantee. The present evidence supports neither a cross-platform
durability claim nor a trusted pre-expiry publication claim.
