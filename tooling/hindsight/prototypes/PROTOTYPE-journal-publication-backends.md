# Hindsight journal publication backend prototype

No candidate is presently shown to establish

```text
journal durable completion
    <= proof durable completion
    <= trusted post-proof upper bound
    < approval_expiry
```

for both the journal and its durable proof under a stated production fault model. The useful next qualification targets are a protocol over the existing PostgreSQL datastore and, only if independence from the target datastore is worth a managed dependency, Cloud Spanner. The PostgreSQL protocol remains to be specified. This is a throwaway viability spike for issue #73. It neither selects an architecture nor contributes production code.

## Question

Which concrete backend can establish the required ordering with enough benefit over the existing storage backends to justify its development, operations, debugging, and maintenance burden?

The prototype applies one domain operation to every candidate:

```text
append_once(action, idempotency_key, exact_bytes, approval_deadline)
    -> durable_receipt
```

A receipt binds the action, approval digest, exact-byte SHA-256 digest, idempotency key, backend identity and fault domain, prerequisite attestation, all three timing values, rollback detection, reason, and result. Its result is exactly one of `VALID`, `UNPROVEN`, `LATE`, or `CONFLICT`. Apply and rollback use separate approvals, keys, and chains. A later exact and idempotent mutation is admitted only by `VALID`.

## Prototype starting point

Base commit `44ee979cdae1d47f2ef3fdc713eaa6f04adf9892` identifies the source from which branch `ivan/prototype-hindsight-journal-backends` started. The three uncommitted prototype artifacts are mutable working material; neither the base commit nor a self-referential Markdown hash identifies their current contents.

## Immutable sources

| Input | Immutable identity | Material consulted |
| --- | --- | --- |
| Repaired stopped-run source | `7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab` | The stopped-journal writer, stopped apply/rollback dispatch, recovery handoff, and their focused tests |
| Durability research | `97e4a2d7b075f79d980657e2b584fa33abdfe9f8` | `tooling/hindsight/docs/research/journal-durability.md` |
| Consumer inventory | `a0ee372aaebfc88c35588474af61f710cf57f6ff` | `tooling/hindsight/docs/research/journal-contracts.md` |
| Private Daybreak architecture analysis | private immutable input; SHA-256 `1abe3ca3a18e779b10a716626f9cf94f181f8e7159a10e4f4f8c4c377f3ee009` | Existing backend and proof-chain analysis; machine-local path intentionally omitted |

The repaired source and research inputs were read-only and remained unpublished. Only the exact stopped-publication seam and its focused tests were inspected.

## Run it

Open `PROTOTYPE-journal-publication-backends.html` directly in a browser. It has no network, server, framework, or persistence dependency. For example, from the repository root on macOS:

```sh
open tooling/hindsight/prototypes/PROTOTYPE-journal-publication-backends.html
```

The candidate selector keeps the guided scenario semantics constant. Free-play controls expose the same state transitions. Every action renders the complete readable and JSON state. All times, storage acknowledgements, clock bounds, crashes, and outages in the page are synthetic model inputs; the page cannot attest production durability.

Run the safe probe with no datastore arguments:

```sh
python3 tooling/hindsight/prototypes/PROTOTYPE-journal-publication-probes.py
```

It creates an obviously disposable SQLite database in a fresh temporary directory, emits canonical JSON Lines, closes the database, and removes the directory. It does not inspect Hindsight configuration or discover a PostgreSQL default.

PostgreSQL is deliberately opt-in and destructive only within a generated scratch schema. The operator must first create this marker in the disposable database; the probe verifies its row before creating its own schema and never removes the operator-owned marker:

```sql
CREATE TABLE public.hindsight_journal_publication_probe_scratch_marker (
    database_name text PRIMARY KEY,
    server_address inet NOT NULL,
    server_port integer NOT NULL,
    database_user text NOT NULL
);
INSERT INTO public.hindsight_journal_publication_probe_scratch_marker (
    database_name, server_address, server_port, database_user
)
SELECT current_database(), inet_server_addr(), inet_server_port(), current_user;
```

After preparing the marker, give both arguments:

```sh
python3 tooling/hindsight/prototypes/PROTOTYPE-journal-publication-probes.py \
  --postgres-scratch-dsn 'host=127.0.0.1 port=55432 user=probe dbname=candidate129_scratch' \
  --confirm-postgres-scratch
```

Only libpq keyword/value conninfo containing one explicit loopback IP literal, numeric port, user, and clearly scratch-named database is accepted. URI forms, duplicate keys, service expansion, other defaults, credentials, and credential-file parameters are rejected. Do not embed a password or any other credential. An accepted DSN exists transiently in process memory and the child `psql` argument vector; the probe does not emit it or persist it as an artifact. It also suppresses default password and service-file lookup. Before mutation, the probe verifies the effective client endpoint against the explicit conninfo and verifies the connected database, user, and server-reported address and port against the operator marker. Keeping endpoint and server identity separate supports a loopback port forwarded to a disposable container without weakening either check. The probe sets `synchronous_commit = on` per transaction, then drops the generated schema and queries `pg_namespace` to verify absence. It remains scratch evidence, not a production durability test.

## Evidence boundaries

### Observed in disposable scratch state

The no-DSN probe exercised SQLite with `journal_mode=DELETE`, `synchronous=EXTRA`, and `fullfsync=ON` as reported by that runtime. It observed:

- one stored row for first append and the same row for same-key/same-binding replay;
- `CONFLICT` for the same key with different exact bytes;
- one insert and three exact replays from four concurrent same-key/same-binding writers;
- query recovery of a committed row after the client outcome was deliberately treated as indeterminate;
- separate apply and rollback keys and approval digests;
- committed/queryable storage of a modeled `LATE` classification as canonical receipt bytes, including digest-verified exact replay and a fresh-connection query; and
- removal of both the scratch database and its temporary directory.

These observations demonstrate the prototype's API and recovery mechanics only. They do not establish what the host, filesystem, device, VFS clock, or power-loss path made durable.

A separately preserved post-correction transcript (private immutable input; SHA-256 `cc25ba34e5a9ed3585f063ae8758d0bf11887435ada11641e2d5462ddae55b85`) records an opt-in run against a new local `postgres:16` container created from an already-present image. No default or live DSN was discovered. The final server reported PostgreSQL `16.14 (Debian 16.14-1.pgdg13+1)`, `fsync=on`, `full_page_writes=on`, `synchronous_commit=on` by default and per transaction, and `wal_sync_method=fdatasync`. The scratch boundary verified the explicit loopback endpoint and operator-created datastore marker before mutation. Observed behavior included first insert, exact replay, different-byte conflict, new-connection recovery after an indeterminate client outcome, distinct apply and rollback bindings, committed/queryable storage and byte-identical replay of canonical modeled `LATE` receipt bytes, four concurrent same-key appends classified as one insert and three exact replays, and verified generated-schema absence. The operator diagnosed an earlier failed acceptance run as a readiness-harness race between the image's temporary initialization server and final server; waiting for the final entrypoint-ready phase removed that failure. The transcript also records zero generated schemas after the probe and removal of the prototype container. This is auditable disposable-scratch behavior, not production durability proof.

etcd and Spanner remain `NOT_RUN`. No etcd or Spanner image, emulator, or dependency was downloaded; no paid resource or cloud credential was used.

### Modeled in the HTML and JSON events

The model makes normally hidden facts explicit: journal completion, proof completion, a trusted post-proof upper bound and its uncertainty, receipt loss, adjustable-clock rollback, datastore availability, and attestation of the backend prerequisites. A modeled timely run reaches `VALID` only with prerequisite attestation, no detected clock rollback, and this complete ordering:

```text
journal durable completion <= proof durable completion
proof durable completion <= trusted post-proof upper bound
trusted post-proof upper bound < approval deadline
```

Missing or internally inconsistent timing evidence is `UNPROVEN`. A journal completion, proof completion, or trusted upper bound at or beyond the deadline is `LATE`. This conditional result is a model branch, not evidence that a candidate supplies those assertions.

The same twelve cases are available for every candidate:

1. timely happy path;
2. same-key/same-bytes replay;
3. same-key/different-bytes conflict;
4. proof stored atomically while its response is lost before client observation;
5. crash after journal commit but before proof commit;
6. journal completion after expiry;
7. trusted post-proof bound or proof completion after expiry;
8. adjustable clock rollback;
9. clock uncertainty larger than the remaining headroom;
10. restart and query recovery after an indeterminate result;
11. distinct apply and rollback authority; and
12. target PostgreSQL unavailable while the publication owner is queried.

In every candidate model, visibility, an intent or pending record, a pre-flush timestamp, a request timestamp, or an emulator outcome leaves the result `UNPROVEN`. A conflicting binding is `CONFLICT`; a journal, proof, or trusted upper-bound value at or after expiry is `LATE`.

### Established only by primary documentation

The following are vendor contracts and prerequisites, not observations from this spike:

- On Linux, `fsync()` flushes file data and metadata needed for retrieval, but a directory entry needs a separate directory `fsync()`; failures may be reported against other descriptors. Apple documents `F_FULLFSYNC` as asking the drive to flush buffered data to permanent storage. These guarantees remain conditional on the operating system, filesystem, device, and support matrix. [Linux `fsync(2)`](https://man7.org/linux/man-pages/man2/fsync.2.html), [Apple `fsync(2)`](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fsync.2.html), [Apple `fcntl(2)`](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fcntl.2.html)
- PostgreSQL documents that `fsync` attempts durable recovery after a crash and that `synchronous_commit=on` waits for local WAL flush before reporting success. `clock_timestamp()` returns the changing wall-clock time; the documentation does not make it a trusted, monotonic, bounded-uncertainty attestation. [WAL settings](https://www.postgresql.org/docs/18/runtime-config-wal.html), [date/time functions](https://www.postgresql.org/docs/18/functions-datetime.html)
- SQLite documents its atomic-commit assumptions and describes `synchronous=EXTRA` in rollback mode and `FULL` in WAL mode. Its `now` value comes from the VFS `xCurrentTime` method, so the storage engine does not add a trusted clock contract. [Atomic commit](https://www.sqlite.org/atomiccommit.html), [`PRAGMA synchronous`](https://www.sqlite.org/pragma.html#pragma_synchronous), [date/time functions](https://www.sqlite.org/lang_datefunc.html)
- etcd documents strict serializability for completed key-value operations and says completion includes consensus and permanent storage. Transactions provide atomic compare/then/else behavior. Its revision is a logical clock, while lease expiry is measured in server-chosen wall-clock time; neither is a documented trusted epoch-time upper bound for this receipt. [`etcd` API guarantees](https://etcd.io/docs/v3.6/learning/api_guarantees/), [`etcd` transactions](https://etcd.io/docs/v3.6/learning/api/)
- Spanner documents ACID durability, external consistency, TrueTime-backed commit timestamps, and majority-replica logging during a write. That is a meaningfully stronger relevant managed contract. The write lifecycle still chooses the commit timestamp before replication completes, and the reviewed documentation does not state that the returned timestamp upper-bounds majority durable completion. [Transactions](https://cloud.google.com/spanner/docs/transactions), [TrueTime and external consistency](https://cloud.google.com/spanner/docs/true-time-external-consistency), [commit timestamps](https://cloud.google.com/spanner/docs/commit-timestamp), [write lifecycle](https://cloud.google.com/spanner/docs/whitepapers/life-of-reads-and-writes)

## Source seam observed

The repaired stopped-journal writer issues calls to write and sync a pending marker, samples `durable_at`, creates the final authenticated journal, invokes the final exchange, and syncs the directory. Those calls attempt durable staging and publication; source inspection does not establish what reached stable storage. A focused test demonstrates that finalization may happen after approval expiry while the journal retains the earlier `applied_at`. That timestamp therefore cannot establish the required post-final-durability ordering.

Stopped application and stopped rollback both use this publication seam, with distinct authority, before a later mutation. Recovery consumes the exact stored journal. Any replacement protocol must preserve those replay bindings, fixed expiry, separate apply and rollback chains, retry/cohort/budget ceilings, historical formats, and the boundary between source reconstruction and live authority.

## Candidate comparison

| Candidate | Semantic fit | Trusted time fit | Publication-owner availability when target PostgreSQL is down | Burden and likely disposition |
| --- | --- | --- | --- | --- |
| Plan-bound private files | Exact bytes, rename/exchange, file and directory sync fit the current seam, but the current pre-final sample is not proof | No native trusted bound; host-clock and storage attestations remain external | Yes, while the owner host and journal volume remain available | Lowest change burden and the negative/control baseline; retain for comparison, not as a demonstrated `VALID` path |
| Existing PostgreSQL | A proposed protocol can use a unique idempotency key, byte/digest binding, journal commit configured for durability, causally later proof, and separate mutation | Database wall clock has no documented trust or uncertainty bound; the proposed proof protocol still needs an attested post-durability bound | No; publication and query recovery share the failed target datastore | Lowest new-system burden; specify and qualify the protocol first if coupling to target PostgreSQL is acceptable |
| SQLite | Unique key, transaction, exact BLOB, and query recovery fit in one small embedded store | VFS wall clock is not a trusted bound | Yes, if its owner host and volume are independent of target PostgreSQL | Lowest dedicated-store implementation burden, but it mostly repackages the file candidate's host, clock, and storage fault domain |
| etcd | Compare-and-put and strict-serializable durable completion fit idempotency and recovery | Revision is logical, not epoch time; leases do not provide the required documented bound | Yes, while quorum and its network remain available | Adds a self-hosted quorum, certificates, compaction, alarms, snapshots, upgrades, and incident surface without solving trusted time |
| Cloud Spanner | Transactional exact bytes, idempotency, durable quorum replication, external consistency, and commit timestamps are strong fits | TrueTime is relevant, but the exposed commit timestamp is not documented as a post-durability upper bound | Yes, subject to independent network, project, IAM, service, and regional availability | The only new candidate with a material native time-and-fault-domain benefit; qualify narrowly before accepting the largest dependency surface |

### Implementation and debugging surface

Files require careful temporary-file creation, full writes, file flush, atomic exchange, directory flush, permissions, cleanup, and per-platform error handling. Their state is directly inspectable, but partial and pending states multiply recovery paths.

A protocol over the existing PostgreSQL datastore could add two small tables or equivalent records and a boundary around journal commit, proof, and mutation. The protocol remains to be specified. SQL constraints can make conflicts and recovery inspectable. Its chief cost is reasoning about ambiguous client outcomes and preserving the exact synchronous settings and storage guarantees in every supported deployment.

SQLite reduces file-protocol code to transactions and queries, but production correctness still depends on journal mode, synchronous mode, VFS behavior, locking, filesystem semantics, and device honesty. Debugging is local and familiar; multi-process ownership and copied database files become new sharp edges.

etcd has a compact key-value protocol but a large system surface: quorum membership, network partitions, TLS identities, disk alarms, compaction, defragmentation, upgrades, snapshots, and restore discipline. A logical revision helps ordering and diagnosis but cannot be compared with approval expiry.

Spanner removes quorum operation from Hindsight but introduces schema/API integration, cloud project and IAM policy, quotas, network paths, regional configuration, emulator/production differences, and vendor-specific incident diagnosis. A support case or explicit contract clarification may be needed for the decisive time inequality.

### Operations, backup, restore, and maintenance

| Candidate | Backup and restore concern | Availability and maintenance concern |
| --- | --- | --- |
| Files | Back up journal bytes, metadata, directory structure, and keys consistently; a file's existence after restore still does not prove its original completion time | Tied to one owner host/volume unless another replication system is introduced; OS and filesystem support matrix must be maintained |
| PostgreSQL | If an existing backup/PITR regime is available, integrate with it and prove that journal and proof rows restore consistently while preserving uniqueness and exact bytes. Otherwise, qualification must define such a regime. [PostgreSQL backup](https://www.postgresql.org/docs/18/backup.html) | Shares target maintenance and outages; no new datastore on-call surface |
| SQLite | Online backup is available, but restore must preserve journal mode expectations, ownership, and the journal/proof relationship. [SQLite backup API](https://www.sqlite.org/backup.html) | One local writer domain; application packaging, library/VFS versions, locking, and volume health become Hindsight responsibilities |
| etcd | Snapshot restore creates a new logical cluster and needs documented revision handling; test clients against restored revision behavior. [`etcd` recovery](https://etcd.io/docs/v3.6/op-guide/recovery/) | Quorum tolerates a bounded member loss, but Hindsight would own the cluster lifecycle and capacity. [`etcd` recovery](https://etcd.io/docs/v3.6/op-guide/recovery/) |
| Spanner | Managed backups exist, but qualification must cover journal/proof consistency and recovery-time behavior. [Spanner backups](https://cloud.google.com/spanner/docs/backup) | Managed replication improves independence from the target datastore, but depends on network, IAM, service quotas, selected instance configuration, and vendor availability. [Spanner replication](https://cloud.google.com/spanner/docs/replication) |

### Latency and expiry headroom

The contract consumes headroom across exact-byte construction, journal durability, durable proof, a causally later trusted post-proof bound, receipt persistence, response loss and recovery, and the final exact mutation. No candidate can be accepted from median latency alone. Qualification needs a deadline budget and tail distributions under the stated fault model.

Files and SQLite avoid a network hop but can stall on local flush, contention, or unhealthy storage. PostgreSQL adds database scheduling and WAL flush but reuses an existing connection and operational path. etcd adds quorum network and disk latency. Spanner adds managed-service and network latency plus commit-wait behavior. The prototype uses synthetic times so it supplies no comparative latency evidence.

## Decision-ready recommendation

Advance two bounded questions, without selecting a production architecture:

1. **Existing PostgreSQL:** specify the smallest journal-commit, causally later durable-proof, and separate-mutation protocol; then determine which trusted time source and post-proof observation can establish the complete inequality chain. This is the best no-new-datastore control and should be rejected explicitly if target-datastore independence is mandatory.
2. **Cloud Spanner:** request or test against a written production contract for a value that upper-bounds majority durable completion, not merely transaction serialization, and bind that value to the exact bytes and expiry. Measure tail headroom only after the contract question is answered.

Do not advance etcd: its strongest documented property solves durable ordering but not trusted deadline time, at high operational cost. Keep SQLite as the lowest-burden dedicated-store control, but do not advance it absent a host/storage/clock attestation that files could not use directly. Keep private files as the negative baseline.

Spanner supplies a meaningfully stronger relevant contract than the other new candidates, but its contract is still insufficiently explicit for `VALID`; no new dependency is justified yet. If target-PostgreSQL independence is not a hard requirement, the likely benefit does not currently outweigh a second datastore's lifecycle cost.

## Unresolved facts

These facts are not discoverable from the immutable inputs and are qualification questions, not blockers to this prototype:

- the authoritative clock source, uncertainty bound, rollback behavior, and signature or attestation chain available to the publication owner;
- whether the approval issuer's clock contract can be compared safely with that bound;
- the supported production OS, filesystem, device, PostgreSQL settings, topology, and fault domains;
- whether independence from target PostgreSQL is required during publication, during recovery queries, or both;
- the available expiry headroom and tail latency budget across commit, proof, retries, and mutation;
- a primary Spanner statement that a client-observable value upper-bounds durable majority completion, or an atomic server-side condition that rejects completion at or after approval expiry;
- backup and restore objectives, including how restored proof state retains its original meaning; and
- exact authentication-key custody and backend identity binding for each deployment.

## Local-runtime and download boundary

### Observed host and tool inventory

The host exposed Python 3.14.7 with SQLite 3.53.4, SQLite CLI 3.51.0, PostgreSQL client/configuration tools 18.6, Docker 29.4, Podman 5.8.3, and `gcloud` 582.0.0. `postgres`, `etcd`, and `etcdctl` were not on `PATH`. The required current-documentation helper was not locally installed. No package was downloaded.

### Supplied local-image and execution context

The supplied context states that the PostgreSQL 16 image was already local and that no local etcd or Spanner emulator image was present. Root's sanitized observation records the disposable PostgreSQL run described above and a follow-up inventory with no remaining prototype container. This correction pass did not start or inspect a service or container.

### Vendor-derived emulator limitation

Google documents that the Spanner emulator is in-memory, lacks production security controls, and is not suitable for performance comparison. Even a local emulator run would remain behavioral evidence only. [Spanner emulator](https://cloud.google.com/spanner/docs/emulator)

Official vendor documentation was read directly when the documentation helper was unavailable. Nothing was installed, pulled, provisioned, or connected to a live/default datastore.
