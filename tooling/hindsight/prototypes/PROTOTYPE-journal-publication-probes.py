#!/usr/bin/env python3
"""Throwaway scratch probes for journal publication backend semantics.

The probe never discovers a PostgreSQL DSN. SQLite uses a disposable temporary
directory. PostgreSQL runs only when the operator supplies constrained scratch
conninfo, explicitly confirms it, and has created the required marker. Output
is canonical JSON Lines.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Any, Iterable, Mapping
import uuid


APPLY_BYTES = b'{"kind":"synthetic-stopped-application-journal","schema_version":99}'
ROLLBACK_BYTES = b'{"kind":"synthetic-stopped-rollback-journal","schema_version":99}'
ALTERED_BYTES = (
    b'{"kind":"synthetic-stopped-application-journal",'
    b'"schema_version":99,"changed":true}'
)
DEADLINE = 100.0
POSTGRES_MARKER_TABLE = "hindsight_journal_publication_probe_scratch_marker"
SCRATCH_DATABASE_PATTERN = re.compile(
    r"(?:^|[_-])(?:prototype|scratch|test|temp|tmp)(?:[_-]|$)",
    re.IGNORECASE,
)


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def emit(candidate: str, disposition: str, event: str, **fields: Any) -> None:
    print(
        canonical_json(
            {
                "candidate": candidate,
                "disposition": disposition,
                "event": event,
                **fields,
            }
        ),
        flush=True,
    )


def digest_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def canonical_receipt_bytes(receipt: Mapping[str, Any]) -> bytes:
    return canonical_json(receipt).encode("utf-8")


def verified_stored_receipt(
    receipt_bytes: bytes,
    receipt_sha256: str,
    *,
    expected_key: str,
) -> dict[str, Any]:
    if digest_bytes(receipt_bytes) != receipt_sha256:
        raise RuntimeError("stored receipt digest verification failed")
    try:
        decoded = json.loads(receipt_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("stored receipt bytes are not canonical JSON") from error
    if not isinstance(decoded, dict) or canonical_receipt_bytes(decoded) != receipt_bytes:
        raise RuntimeError("stored receipt bytes are not canonical JSON")
    if decoded.get("idempotency_key") != expected_key:
        raise RuntimeError("stored receipt does not bind the queried key")
    required = {
        "action",
        "approval_deadline",
        "approval_digest",
        "backend_fault_domain",
        "backend_identity",
        "claimed_trusted_post_durability_upper_bound",
        "clock_rollback_detected",
        "exact_byte_digest",
        "idempotency_key",
        "journal_durable_completion",
        "prerequisites_attested",
        "proof_durable_completion",
        "reason",
        "result",
    }
    if not required.issubset(decoded):
        raise RuntimeError("stored receipt is missing required fields")
    return {
        "receipt": decoded,
        "receipt_canonical_json": receipt_bytes.decode("utf-8"),
        "receipt_sha256": receipt_sha256,
    }


def request(
    *,
    action: str,
    key: str,
    exact_bytes: bytes,
    approval: str,
    deadline: float = DEADLINE,
) -> dict[str, Any]:
    return {
        "action": action,
        "idempotency_key": key,
        "exact_bytes": exact_bytes,
        "exact_byte_digest": digest_bytes(exact_bytes),
        "approval_digest": digest_bytes(approval.encode("utf-8")),
        "approval_deadline": deadline,
    }


def public_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action": value["action"],
        "idempotency_key": value["idempotency_key"],
        "exact_byte_digest": value["exact_byte_digest"],
        "approval_digest": value["approval_digest"],
        "approval_deadline": value["approval_deadline"],
    }


def classify_receipt(
    value: Mapping[str, Any],
    *,
    backend_identity: str,
    fault_domain: str,
    durable_completion: float | None,
    trusted_upper_bound: float | None,
    proof_completion: float | None,
    prerequisites_attested: bool,
    clock_rollback_detected: bool = False,
    conflict: bool = False,
) -> dict[str, Any]:
    if conflict:
        result = "CONFLICT"
        reason = "same key has a different action, approval, deadline, or byte digest"
    elif (
        durable_completion is not None
        and durable_completion >= value["approval_deadline"]
    ) or (
        trusted_upper_bound is not None
        and trusted_upper_bound >= value["approval_deadline"]
    ) or (
        proof_completion is not None
        and proof_completion >= value["approval_deadline"]
    ):
        result = "LATE"
        reason = "journal completion, proof completion, or trusted upper bound reached expiry"
    elif not prerequisites_attested or clock_rollback_detected:
        result = "UNPROVEN"
        reason = "prerequisite attestation is missing or clock rollback was detected"
    elif (
        durable_completion is None
        or trusted_upper_bound is None
        or proof_completion is None
        or not all(
            math.isfinite(value)
            for value in (durable_completion, proof_completion, trusted_upper_bound)
        )
        or durable_completion > proof_completion
        or proof_completion > trusted_upper_bound
    ):
        result = "UNPROVEN"
        reason = "the required journal, proof, and trusted-time ordering is incomplete or inconsistent"
    else:
        result = "VALID"
        reason = "modeled prerequisites establish the required ordering before expiry"

    return {
        **public_binding(value),
        "backend_identity": backend_identity,
        "backend_fault_domain": fault_domain,
        "claimed_trusted_post_durability_upper_bound": trusted_upper_bound,
        "journal_durable_completion": durable_completion,
        "proof_durable_completion": proof_completion,
        "prerequisites_attested": prerequisites_attested,
        "clock_rollback_detected": clock_rollback_detected,
        "reason": reason,
        "result": result,
    }


def open_sqlite_connection(database: Path, *, timeout: float) -> sqlite3.Connection:
    connection = sqlite3.connect(database, isolation_level=None, timeout=timeout)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = EXTRA")
        connection.execute("PRAGMA fullfsync = ON")
    except BaseException:
        connection.close()
        raise
    return connection


def sqlite_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE journals (
            idempotency_key TEXT PRIMARY KEY,
            action TEXT NOT NULL CHECK (action IN ('APPLY', 'ROLLBACK')),
            approval_digest TEXT NOT NULL,
            approval_deadline REAL NOT NULL,
            exact_bytes BLOB NOT NULL,
            exact_byte_digest TEXT NOT NULL,
            observed_commit_at REAL NOT NULL
        ) STRICT;
        CREATE TABLE receipts (
            idempotency_key TEXT PRIMARY KEY REFERENCES journals(idempotency_key),
            receipt_bytes BLOB NOT NULL,
            receipt_sha256 TEXT NOT NULL
        ) STRICT;
        """
    )
    connection.commit()


def sqlite_row(connection: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT action, idempotency_key, exact_bytes, exact_byte_digest,
               approval_digest, approval_deadline, observed_commit_at
        FROM journals
        WHERE idempotency_key = ?
        """,
        (key,),
    ).fetchone()
    if row is None:
        return None
    return {
        "action": row[0],
        "idempotency_key": row[1],
        "exact_bytes": bytes(row[2]),
        "exact_byte_digest": row[3],
        "approval_digest": row[4],
        "approval_deadline": row[5],
        "observed_commit_at": row[6],
    }


def same_binding(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return public_binding(left) == public_binding(right)


def sqlite_append_once(
    connection: sqlite3.Connection,
    value: Mapping[str, Any],
    *,
    observed_commit_at: float,
) -> tuple[str, dict[str, Any]]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = sqlite_row(connection, str(value["idempotency_key"]))
        if existing is not None:
            connection.commit()
            return (
                "replay" if same_binding(existing, value) else "conflict",
                existing,
            )
        connection.execute(
            """
            INSERT INTO journals (
                idempotency_key, action, approval_digest, approval_deadline,
                exact_bytes, exact_byte_digest, observed_commit_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                value["idempotency_key"],
                value["action"],
                value["approval_digest"],
                value["approval_deadline"],
                value["exact_bytes"],
                value["exact_byte_digest"],
                observed_commit_at,
            ),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    stored = sqlite_row(connection, str(value["idempotency_key"]))
    if stored is None:
        raise RuntimeError("SQLite scratch commit was not queryable")
    return "inserted", stored


def sqlite_receipt(
    connection: sqlite3.Connection,
    key: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT receipt_bytes, receipt_sha256
        FROM receipts
        WHERE idempotency_key = ?
        """,
        (key,),
    ).fetchone()
    if row is None:
        return None
    return verified_stored_receipt(bytes(row[0]), row[1], expected_key=key)


def sqlite_store_receipt_once(
    connection: sqlite3.Connection,
    key: str,
    receipt: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    proposed_bytes = canonical_receipt_bytes(receipt)
    proposed_sha256 = digest_bytes(proposed_bytes)
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = sqlite_receipt(connection, key)
        if existing is not None:
            connection.commit()
            exact = (
                existing["receipt_canonical_json"].encode("utf-8") == proposed_bytes
                and existing["receipt_sha256"] == proposed_sha256
            )
            return ("replay" if exact else "conflict"), existing
        connection.execute(
            """
            INSERT INTO receipts (
                idempotency_key, receipt_bytes, receipt_sha256
            ) VALUES (?, ?, ?)
            """,
            (key, proposed_bytes, proposed_sha256),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    stored = sqlite_receipt(connection, key)
    if stored is None:
        raise RuntimeError("SQLite scratch receipt commit was not queryable")
    return "inserted", stored


def sqlite_concurrent_append_once(
    database: Path,
    value: Mapping[str, Any],
    *,
    workers: int = 4,
) -> dict[str, int]:
    def append(index: int) -> str:
        connection = open_sqlite_connection(database, timeout=10)
        try:
            outcome, _ = sqlite_append_once(
                connection,
                value,
                observed_commit_at=80.0 + index,
            )
            return outcome
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        outcomes = list(executor.map(append, range(workers)))
    return {outcome: outcomes.count(outcome) for outcome in ("inserted", "replay", "conflict")}


def run_sqlite_probe() -> None:
    candidate = "sqlite"
    backend_identity = "sqlite-scratch-probe"
    fault_domain = "temporary database file, SQLite VFS, host filesystem, and storage"
    scratch_path: Path | None = None
    scratch_parent: Path | None = None

    with tempfile.TemporaryDirectory(prefix="hindsight-journal-prototype-") as directory:
        scratch_parent = Path(directory)
        scratch_path = scratch_parent / "PROTOTYPE-JOURNAL-PUBLICATION-WIPE-ME.sqlite3"
        connection = open_sqlite_connection(scratch_path, timeout=5)
        try:
            journal_mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
            settings = {
                "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
                "fullfsync": connection.execute("PRAGMA fullfsync").fetchone()[0],
                "journal_mode": journal_mode,
                "sqlite_version": sqlite3.sqlite_version,
                "synchronous": connection.execute("PRAGMA synchronous").fetchone()[0],
            }
            sqlite_schema(connection)
            emit(
                candidate,
                "OBSERVED_SCRATCH",
                "runtime",
                settings=settings,
                production_durability_proof=False,
            )

            apply = request(
                action="APPLY",
                key="apply-key-001",
                exact_bytes=APPLY_BYTES,
                approval="synthetic-apply-approval",
            )
            outcome, stored = sqlite_append_once(
                connection,
                apply,
                observed_commit_at=72.0,
            )
            emit(
                candidate,
                "OBSERVED_SCRATCH",
                "journal_commit",
                binding=public_binding(stored),
                database_outcome=outcome,
                result="UNPROVEN",
                production_durability_proof=False,
            )

            replay_outcome, replayed = sqlite_append_once(
                connection,
                apply,
                observed_commit_at=74.0,
            )
            emit(
                candidate,
                "OBSERVED_SCRATCH",
                "same_key_same_bytes_replay",
                binding=public_binding(replayed),
                database_outcome=replay_outcome,
                result="UNPROVEN",
            )

            altered = {**apply, "exact_bytes": ALTERED_BYTES, "exact_byte_digest": digest_bytes(ALTERED_BYTES)}
            conflict_outcome, conflict_row = sqlite_append_once(
                connection,
                altered,
                observed_commit_at=75.0,
            )
            emit(
                candidate,
                "OBSERVED_SCRATCH",
                "same_key_different_bytes",
                attempted_binding=public_binding(altered),
                stored_binding=public_binding(conflict_row),
                database_outcome=conflict_outcome,
                result="CONFLICT",
            )

            concurrent_request = request(
                action="APPLY",
                key="apply-key-concurrent",
                exact_bytes=APPLY_BYTES,
                approval="synthetic-apply-approval",
            )
            concurrent_outcomes = sqlite_concurrent_append_once(
                scratch_path,
                concurrent_request,
            )
            concurrent_rows = connection.execute(
                "SELECT count(*) FROM journals WHERE idempotency_key = ?",
                (concurrent_request["idempotency_key"],),
            ).fetchone()[0]
            emit(
                candidate,
                "OBSERVED_SCRATCH",
                "concurrent_same_key_append",
                outcomes=concurrent_outcomes,
                row_count=concurrent_rows,
                workers=sum(concurrent_outcomes.values()),
                result="UNPROVEN",
                production_durability_proof=False,
            )

            indeterminate = request(
                action="APPLY",
                key="apply-key-indeterminate",
                exact_bytes=APPLY_BYTES,
                approval="synthetic-apply-approval",
            )
            sqlite_append_once(connection, indeterminate, observed_commit_at=76.0)
            emit(
                candidate,
                "MODELED",
                "client_lost_commit_response",
                idempotency_key=indeterminate["idempotency_key"],
                client_outcome="INDETERMINATE",
                result="UNPROVEN",
            )
            connection.close()
            connection = open_sqlite_connection(scratch_path, timeout=5)
            recovered = sqlite_row(connection, indeterminate["idempotency_key"])
            emit(
                candidate,
                "OBSERVED_SCRATCH",
                "restart_query_recovery",
                binding=public_binding(recovered) if recovered else None,
                row_found=recovered is not None,
                result="UNPROVEN",
                production_durability_proof=False,
            )

            rollback = request(
                action="ROLLBACK",
                key="rollback-key-001",
                exact_bytes=ROLLBACK_BYTES,
                approval="synthetic-rollback-approval",
            )
            sqlite_append_once(connection, rollback, observed_commit_at=78.0)
            emit(
                candidate,
                "OBSERVED_SCRATCH",
                "distinct_apply_and_rollback_authority",
                apply_binding=public_binding(apply),
                keys_distinct=apply["idempotency_key"] != rollback["idempotency_key"],
                approvals_distinct=apply["approval_digest"] != rollback["approval_digest"],
                rollback_binding=public_binding(rollback),
                result="UNPROVEN",
            )

            modeled_valid = classify_receipt(
                apply,
                backend_identity=backend_identity,
                fault_domain=fault_domain,
                durable_completion=72.0,
                proof_completion=74.0,
                trusted_upper_bound=75.0,
                prerequisites_attested=True,
            )
            emit(
                candidate,
                "MODELED",
                "conditional_timely_receipt",
                receipt=modeled_valid,
                scratch_observation_is_production_proof=False,
            )

            late = request(
                action="APPLY",
                key="apply-key-late-proof",
                exact_bytes=APPLY_BYTES,
                approval="synthetic-apply-approval",
            )
            sqlite_append_once(connection, late, observed_commit_at=96.0)
            late_receipt = classify_receipt(
                late,
                backend_identity=backend_identity,
                fault_domain=fault_domain,
                durable_completion=96.0,
                proof_completion=101.0,
                trusted_upper_bound=102.0,
                prerequisites_attested=True,
            )
            receipt_outcome, stored_late = sqlite_store_receipt_once(
                connection,
                late["idempotency_key"],
                late_receipt,
            )
            replay_outcome, replayed_late = sqlite_store_receipt_once(
                connection,
                late["idempotency_key"],
                late_receipt,
            )
            connection.close()
            connection = open_sqlite_connection(scratch_path, timeout=5)
            recovered_late = sqlite_receipt(connection, late["idempotency_key"])
            if recovered_late is None:
                raise RuntimeError("SQLite scratch receipt was not queryable after restart")
            emit(
                candidate,
                "OBSERVED_SCRATCH_AND_MODELED_CLASSIFICATION",
                "late_proof_recovery",
                modeled_receipt=late_receipt,
                first_store_outcome=receipt_outcome,
                exact_replay_outcome=replay_outcome,
                exact_replay_bytes_identical=(
                    stored_late["receipt_canonical_json"]
                    == replayed_late["receipt_canonical_json"]
                    == recovered_late["receipt_canonical_json"]
                ),
                stored_receipt=recovered_late,
            )

            emit(
                candidate,
                "MODELED",
                "target_postgresql_unavailable",
                publication_owner_queryable=True,
                mutation_admitted=False,
                result=modeled_valid["result"],
            )
        finally:
            connection.close()

    emit(
        candidate,
        "OBSERVED_SCRATCH",
        "cleanup",
        database_residue=scratch_path.exists() if scratch_path else None,
        directory_residue=scratch_parent.exists() if scratch_parent else None,
    )


def pg_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def pg_bytea(value: bytes) -> str:
    return f"decode('{value.hex()}','hex')"


def parse_postgres_scratch_conninfo(conninfo: str) -> dict[str, str]:
    if not isinstance(conninfo, str) or not conninfo.strip():
        raise RuntimeError("PostgreSQL scratch conninfo is empty")
    if re.match(r"^postgres(?:ql)?://", conninfo.lstrip(), re.IGNORECASE):
        raise RuntimeError("PostgreSQL URI conninfo is not accepted")

    parsed: dict[str, str] = {}
    index = 0
    length = len(conninfo)
    while index < length:
        while index < length and conninfo[index].isspace():
            index += 1
        if index == length:
            break
        key_start = index
        while index < length and not conninfo[index].isspace() and conninfo[index] != "=":
            index += 1
        key = conninfo[key_start:index].lower()
        while index < length and conninfo[index].isspace():
            index += 1
        if not key or index == length or conninfo[index] != "=":
            raise RuntimeError("PostgreSQL conninfo must use keyword=value fields")
        index += 1
        while index < length and conninfo[index].isspace():
            index += 1

        characters: list[str] = []
        if index < length and conninfo[index] == "'":
            index += 1
            while index < length and conninfo[index] != "'":
                if conninfo[index] == "\\":
                    index += 1
                    if index == length:
                        raise RuntimeError("PostgreSQL conninfo has a trailing escape")
                characters.append(conninfo[index])
                index += 1
            if index == length:
                raise RuntimeError("PostgreSQL conninfo has an unterminated quoted value")
            index += 1
            if index < length and not conninfo[index].isspace():
                raise RuntimeError("PostgreSQL conninfo has text after a quoted value")
        else:
            while index < length and not conninfo[index].isspace():
                if conninfo[index] == "\\":
                    index += 1
                    if index == length:
                        raise RuntimeError("PostgreSQL conninfo has a trailing escape")
                characters.append(conninfo[index])
                index += 1

        if key in parsed:
            raise RuntimeError(f"PostgreSQL conninfo repeats {key}")
        parsed[key] = "".join(characters)

    allowed = {"host", "port", "user", "dbname"}
    extra = sorted(set(parsed) - allowed)
    if extra:
        raise RuntimeError(
            "PostgreSQL scratch conninfo accepts only host, port, user, and dbname; "
            "service, credential, file, and default-expansion parameters are forbidden"
        )
    missing = sorted(allowed - set(parsed))
    if missing:
        raise RuntimeError("PostgreSQL scratch conninfo must explicitly set host, port, user, and dbname")

    try:
        host = ipaddress.ip_address(parsed["host"])
    except ValueError as error:
        raise RuntimeError("PostgreSQL scratch host must be a loopback IP literal") from error
    if not host.is_loopback:
        raise RuntimeError("PostgreSQL scratch host must be a loopback IP literal")
    port = parsed["port"]
    if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise RuntimeError("PostgreSQL scratch port must be an explicit numeric TCP port")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", parsed["user"]):
        raise RuntimeError("PostgreSQL scratch user must be an explicit simple role name")
    database = parsed["dbname"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", database) or not SCRATCH_DATABASE_PATTERN.search(database):
        raise RuntimeError("PostgreSQL database name is not clearly marked as scratch")
    return parsed


class ScratchPostgres:
    def __init__(self, dsn: str, boundary: Mapping[str, str]) -> None:
        executable = shutil.which("psql")
        if executable is None:
            raise RuntimeError("psql is not available")
        self.executable = executable
        self.dsn = dsn
        self.boundary = dict(boundary)
        self.schema = f"prototype_journal_{uuid.uuid4().hex}"
        self.boundary_verified = False
        self.cleanup_required = False
        self.created = False

    def query(self, sql: str) -> list[list[str]]:
        environment = {
            "LANG": "C",
            "LC_ALL": "C",
            "KRB5CCNAME": "/dev/null",
            "PATH": os.environ.get("PATH", ""),
            "PGPASSFILE": "/dev/null",
            "PGSERVICEFILE": "/dev/null",
            "PGGSSENCMODE": "disable",
            "PGSSLMODE": "disable",
        }
        result = subprocess.run(
            [
                self.executable,
                "--no-psqlrc",
                "--no-password",
                "--quiet",
                "--set=ON_ERROR_STOP=1",
                "--tuples-only",
                "--no-align",
                "--field-separator=\t",
                "--dbname",
                self.dsn,
                "--command",
                sql,
            ],
            check=False,
            capture_output=True,
            encoding="utf-8",
            env=environment,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"scratch psql command failed with exit {result.returncode}")
        return [line.split("\t") for line in result.stdout.splitlines() if line]

    def verify_scratch_boundary(self) -> None:
        rows = self.query(
            f"""
            SELECT current_database(),
                   {pg_literal(self.boundary["host"])},
                   {pg_literal(str(int(self.boundary["port"])))},
                   current_user,
                   host(inet_server_addr()), inet_server_port()::text,
                   host(marker.server_address), marker.server_port::text,
                   marker.database_user
            FROM public.{POSTGRES_MARKER_TABLE} AS marker
            WHERE marker.database_name = current_database()
            """
        )
        if len(rows) != 1 or len(rows[0]) != 9:
            raise RuntimeError("PostgreSQL scratch boundary query returned an unexpected shape")
        (
            database,
            connection_host,
            connection_port,
            user,
            server_address,
            server_port,
            marked_server_address,
            marked_server_port,
            marked_user,
        ) = rows[0]
        try:
            actual_connection_host = ipaddress.ip_address(connection_host)
            expected_connection_host = ipaddress.ip_address(self.boundary["host"])
            actual_server_address = ipaddress.ip_address(server_address)
            expected_server_address = ipaddress.ip_address(marked_server_address)
        except ValueError as error:
            raise RuntimeError("PostgreSQL did not report parseable connection and server addresses") from error
        if (
            database != self.boundary["dbname"]
            or actual_connection_host != expected_connection_host
            or connection_port != str(int(self.boundary["port"]))
            or user != self.boundary["user"]
            or actual_server_address != expected_server_address
            or server_port != marked_server_port
            or user != marked_user
        ):
            raise RuntimeError("PostgreSQL connection does not match the explicit scratch boundary")
        self.boundary_verified = True

    def create(self) -> None:
        if not self.boundary_verified:
            raise RuntimeError("PostgreSQL scratch boundary was not verified")
        self.cleanup_required = True
        self.query(
            f"""
            BEGIN;
            CREATE SCHEMA {self.schema};
            CREATE TABLE {self.schema}.journals (
                idempotency_key text PRIMARY KEY,
                action text NOT NULL CHECK (action IN ('APPLY', 'ROLLBACK')),
                approval_digest text NOT NULL,
                approval_deadline double precision NOT NULL,
                exact_bytes bytea NOT NULL,
                exact_byte_digest text NOT NULL,
                observed_commit_at double precision NOT NULL
            );
            CREATE TABLE {self.schema}.receipts (
                idempotency_key text PRIMARY KEY
                    REFERENCES {self.schema}.journals(idempotency_key),
                receipt_bytes bytea NOT NULL,
                receipt_sha256 text NOT NULL
            );
            COMMIT;
            """
        )
        self.created = True

    def cleanup(self) -> bool:
        if not self.cleanup_required:
            return True
        drop_succeeded = True
        try:
            self.query(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        except Exception:
            drop_succeeded = False
        try:
            rows = self.query(
                "SELECT count(*)::text FROM pg_namespace "
                f"WHERE nspname = {pg_literal(self.schema)}"
            )
        except Exception:
            return False
        absent = rows == [["0"]]
        if absent:
            self.created = False
            self.cleanup_required = False
        return drop_succeeded and absent

    def row(self, key: str) -> dict[str, Any] | None:
        rows = self.query(
            f"""
            SELECT action, idempotency_key, encode(exact_bytes, 'hex'),
                   exact_byte_digest, approval_digest, approval_deadline,
                   observed_commit_at
            FROM {self.schema}.journals
            WHERE idempotency_key = {pg_literal(key)}
            """
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "action": row[0],
            "idempotency_key": row[1],
            "exact_bytes": bytes.fromhex(row[2]),
            "exact_byte_digest": row[3],
            "approval_digest": row[4],
            "approval_deadline": float(row[5]),
            "observed_commit_at": float(row[6]),
        }

    def append_once(
        self,
        value: Mapping[str, Any],
        *,
        observed_commit_at: float,
    ) -> tuple[str, dict[str, Any]]:
        rows = self.query(
            f"""
            BEGIN;
            SET LOCAL synchronous_commit = on;
            INSERT INTO {self.schema}.journals (
                idempotency_key, action, approval_digest, approval_deadline,
                exact_bytes, exact_byte_digest, observed_commit_at
            ) VALUES (
                {pg_literal(str(value['idempotency_key']))},
                {pg_literal(str(value['action']))},
                {pg_literal(str(value['approval_digest']))},
                {float(value['approval_deadline'])},
                {pg_bytea(bytes(value['exact_bytes']))},
                {pg_literal(str(value['exact_byte_digest']))},
                {observed_commit_at}
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING 'inserted', action, idempotency_key,
                      encode(exact_bytes, 'hex'), exact_byte_digest,
                      approval_digest, approval_deadline, observed_commit_at;
            SELECT 'stored', action, idempotency_key,
                   encode(exact_bytes, 'hex'), exact_byte_digest,
                   approval_digest, approval_deadline, observed_commit_at
            FROM {self.schema}.journals
            WHERE idempotency_key = {pg_literal(str(value['idempotency_key']))};
            COMMIT;
            """
        )
        inserted = any(row and row[0] == "inserted" for row in rows)
        stored_rows = [row for row in rows if row and row[0] == "stored"]
        if len(stored_rows) != 1:
            raise RuntimeError("PostgreSQL scratch commit was not queryable")
        row = stored_rows[0]
        stored = {
            "action": row[1],
            "idempotency_key": row[2],
            "exact_bytes": bytes.fromhex(row[3]),
            "exact_byte_digest": row[4],
            "approval_digest": row[5],
            "approval_deadline": float(row[6]),
            "observed_commit_at": float(row[7]),
        }
        if inserted:
            return "inserted", stored
        return ("replay" if same_binding(stored, value) else "conflict", stored)

    def receipt(self, key: str) -> dict[str, Any] | None:
        rows = self.query(
            f"""
            SELECT encode(receipt_bytes, 'hex'), receipt_sha256
            FROM {self.schema}.receipts
            WHERE idempotency_key = {pg_literal(key)}
            """
        )
        if not rows:
            return None
        if len(rows) != 1 or len(rows[0]) != 2:
            raise RuntimeError("PostgreSQL scratch receipt query returned an unexpected shape")
        return verified_stored_receipt(
            bytes.fromhex(rows[0][0]),
            rows[0][1],
            expected_key=key,
        )

    def store_receipt_once(
        self,
        key: str,
        receipt: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        proposed_bytes = canonical_receipt_bytes(receipt)
        proposed_sha256 = digest_bytes(proposed_bytes)
        rows = self.query(
            f"""
            BEGIN;
            SET LOCAL synchronous_commit = on;
            INSERT INTO {self.schema}.receipts (
                idempotency_key, receipt_bytes, receipt_sha256
            ) VALUES (
                {pg_literal(key)},
                {pg_bytea(proposed_bytes)},
                {pg_literal(proposed_sha256)}
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING 'inserted', encode(receipt_bytes, 'hex'), receipt_sha256;
            SELECT 'stored', encode(receipt_bytes, 'hex'), receipt_sha256
            FROM {self.schema}.receipts
            WHERE idempotency_key = {pg_literal(key)};
            COMMIT;
            """
        )
        inserted = any(row and row[0] == "inserted" for row in rows)
        stored_rows = [row for row in rows if row and row[0] == "stored"]
        if len(stored_rows) != 1 or len(stored_rows[0]) != 3:
            raise RuntimeError("PostgreSQL scratch receipt commit was not queryable")
        receipt_bytes = bytes.fromhex(stored_rows[0][1])
        receipt_sha256 = stored_rows[0][2]
        stored = verified_stored_receipt(receipt_bytes, receipt_sha256, expected_key=key)
        if inserted:
            return "inserted", stored
        exact = receipt_bytes == proposed_bytes and receipt_sha256 == proposed_sha256
        return ("replay" if exact else "conflict"), stored


def run_postgres_probe(dsn: str) -> None:
    candidate = "postgresql"
    boundary = parse_postgres_scratch_conninfo(dsn)
    backend = ScratchPostgres(dsn, boundary)
    cleanup_succeeded = True
    probe_error: BaseException | None = None
    try:
        backend.verify_scratch_boundary()
        emit(
            candidate,
            "BOUNDARY",
            "scratch_boundary_verified",
            explicit_loopback_host=True,
            explicit_port_user_and_database=True,
            operator_created_marker_verified=True,
            credentials_or_dsn_printed=False,
        )
        backend.create()
        settings_rows = backend.query(
            """
            SELECT current_setting('server_version'), current_setting('fsync'),
                   current_setting('full_page_writes'),
                   current_setting('wal_sync_method'),
                   current_setting('synchronous_commit')
            """
        )
        settings = settings_rows[0] if settings_rows else []
        emit(
            candidate,
            "OBSERVED_SCRATCH",
            "runtime",
            settings={
                "fsync": settings[1] if len(settings) > 1 else None,
                "full_page_writes": settings[2] if len(settings) > 2 else None,
                "server_version": settings[0] if settings else None,
                "synchronous_commit_default": settings[4] if len(settings) > 4 else None,
                "wal_sync_method": settings[3] if len(settings) > 3 else None,
            },
            per_transaction_synchronous_commit="on",
            production_durability_proof=False,
        )

        apply = request(
            action="APPLY",
            key="apply-key-001",
            exact_bytes=APPLY_BYTES,
            approval="synthetic-apply-approval",
        )
        outcome, stored = backend.append_once(apply, observed_commit_at=72.0)
        emit(
            candidate,
            "OBSERVED_SCRATCH",
            "journal_commit",
            binding=public_binding(stored),
            database_outcome=outcome,
            result="UNPROVEN",
            production_durability_proof=False,
        )

        replay_outcome, replayed = backend.append_once(apply, observed_commit_at=74.0)
        emit(
            candidate,
            "OBSERVED_SCRATCH",
            "same_key_same_bytes_replay",
            binding=public_binding(replayed),
            database_outcome=replay_outcome,
            result="UNPROVEN",
        )

        altered = {**apply, "exact_bytes": ALTERED_BYTES, "exact_byte_digest": digest_bytes(ALTERED_BYTES)}
        conflict_outcome, conflict_row = backend.append_once(altered, observed_commit_at=75.0)
        emit(
            candidate,
            "OBSERVED_SCRATCH",
            "same_key_different_bytes",
            attempted_binding=public_binding(altered),
            stored_binding=public_binding(conflict_row),
            database_outcome=conflict_outcome,
            result="CONFLICT",
        )

        concurrent_request = request(
            action="APPLY",
            key="apply-key-concurrent",
            exact_bytes=APPLY_BYTES,
            approval="synthetic-apply-approval",
        )

        def append_concurrently(index: int) -> str:
            outcome, _ = backend.append_once(
                concurrent_request,
                observed_commit_at=80.0 + index,
            )
            return outcome

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            concurrent_outcomes = list(executor.map(append_concurrently, range(4)))
        emit(
            candidate,
            "OBSERVED_SCRATCH",
            "concurrent_same_key_append",
            outcomes={
                outcome: concurrent_outcomes.count(outcome)
                for outcome in ("inserted", "replay", "conflict")
            },
            workers=len(concurrent_outcomes),
            result="UNPROVEN",
            production_durability_proof=False,
        )

        indeterminate = request(
            action="APPLY",
            key="apply-key-indeterminate",
            exact_bytes=APPLY_BYTES,
            approval="synthetic-apply-approval",
        )
        backend.append_once(indeterminate, observed_commit_at=76.0)
        emit(
            candidate,
            "MODELED",
            "client_lost_commit_response",
            idempotency_key=indeterminate["idempotency_key"],
            client_outcome="INDETERMINATE",
            result="UNPROVEN",
        )
        recovered = backend.row(indeterminate["idempotency_key"])
        emit(
            candidate,
            "OBSERVED_SCRATCH",
            "restart_query_recovery",
            binding=public_binding(recovered) if recovered else None,
            row_found=recovered is not None,
            result="UNPROVEN",
            production_durability_proof=False,
        )

        rollback = request(
            action="ROLLBACK",
            key="rollback-key-001",
            exact_bytes=ROLLBACK_BYTES,
            approval="synthetic-rollback-approval",
        )
        backend.append_once(rollback, observed_commit_at=78.0)
        emit(
            candidate,
            "OBSERVED_SCRATCH",
            "distinct_apply_and_rollback_authority",
            apply_binding=public_binding(apply),
            rollback_binding=public_binding(rollback),
            keys_distinct=True,
            approvals_distinct=True,
            result="UNPROVEN",
        )

        modeled_valid = classify_receipt(
            apply,
            backend_identity="existing-target-postgresql/prototype-protocol",
            fault_domain="scratch PostgreSQL server, WAL, filesystem, and storage",
            durable_completion=72.0,
            proof_completion=74.0,
            trusted_upper_bound=75.0,
            prerequisites_attested=True,
        )
        emit(
            candidate,
            "MODELED",
            "conditional_timely_receipt",
            receipt=modeled_valid,
            scratch_observation_is_production_proof=False,
        )

        late = request(
            action="APPLY",
            key="apply-key-late-proof",
            exact_bytes=APPLY_BYTES,
            approval="synthetic-apply-approval",
        )
        backend.append_once(late, observed_commit_at=96.0)
        late_receipt = classify_receipt(
            late,
            backend_identity="existing-target-postgresql/prototype-protocol",
            fault_domain="scratch PostgreSQL server, WAL, filesystem, and storage",
            durable_completion=96.0,
            proof_completion=101.0,
            trusted_upper_bound=102.0,
            prerequisites_attested=True,
        )
        receipt_outcome, stored_late = backend.store_receipt_once(
            late["idempotency_key"],
            late_receipt,
        )
        replay_outcome, replayed_late = backend.store_receipt_once(
            late["idempotency_key"],
            late_receipt,
        )
        recovered_late = backend.receipt(late["idempotency_key"])
        if recovered_late is None:
            raise RuntimeError("PostgreSQL scratch receipt was not queryable through a fresh client")
        emit(
            candidate,
            "OBSERVED_SCRATCH_AND_MODELED_CLASSIFICATION",
            "late_proof_recovery",
            modeled_receipt=late_receipt,
            first_store_outcome=receipt_outcome,
            exact_replay_outcome=replay_outcome,
            exact_replay_bytes_identical=(
                stored_late["receipt_canonical_json"]
                == replayed_late["receipt_canonical_json"]
                == recovered_late["receipt_canonical_json"]
            ),
            stored_receipt=recovered_late,
        )

        emit(
            candidate,
            "MODELED",
            "target_postgresql_unavailable",
            publication_owner_queryable=False,
            mutation_admitted=False,
            result="UNPROVEN",
        )
    except BaseException as error:
        probe_error = error
    finally:
        cleanup_attempted = backend.cleanup_required
        cleanup_succeeded = backend.cleanup()
        emit(
            candidate,
            "OBSERVED_SCRATCH",
            "cleanup",
            cleanup_attempted=cleanup_attempted,
            generated_schema=backend.schema,
            schema_absence_verified=cleanup_succeeded if cleanup_attempted else None,
        )
    if probe_error is not None:
        raise probe_error
    if not cleanup_succeeded:
        raise RuntimeError("PostgreSQL scratch schema cleanup could not be verified")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run throwaway SQLite and opt-in PostgreSQL journal probes."
    )
    value.add_argument(
        "--postgres-scratch-dsn",
        help=(
            "Explicit host/port/user/dbname keyword conninfo for a marked disposable "
            "PostgreSQL database. URI, credential, service, and default parameters are rejected."
        ),
    )
    value.add_argument(
        "--confirm-postgres-scratch",
        action="store_true",
        help="Confirm the explicitly bounded, operator-marked disposable scratch database.",
    )
    return value


def main(argv: Iterable[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    emit(
        "probe",
        "BOUNDARY",
        "start",
        synthetic_payloads_only=True,
        live_or_default_datastore_discovery=False,
        production_durability_proof=False,
    )
    run_sqlite_probe()

    if arguments.postgres_scratch_dsn and arguments.confirm_postgres_scratch:
        try:
            run_postgres_probe(arguments.postgres_scratch_dsn)
        except Exception as error:
            emit(
                "postgresql",
                "FAILED_SCRATCH",
                "probe_error",
                error_type=type(error).__name__,
                credentials_or_dsn_printed=False,
                production_durability_proof=False,
            )
            return 1
    elif arguments.postgres_scratch_dsn:
        emit(
            "postgresql",
            "NOT_RUN",
            "disposition",
            reason="explicit --confirm-postgres-scratch was not supplied",
        )
    else:
        emit(
            "postgresql",
            "NOT_RUN",
            "disposition",
            reason="no explicit scratch DSN was supplied; defaults and live stores were not discovered",
        )

    emit(
        "etcd",
        "NOT_RUN",
        "disposition",
        reason="no already-local etcd emulator or runtime was supplied; nothing was downloaded",
        production_durability_proof=False,
    )
    emit(
        "spanner",
        "NOT_RUN",
        "disposition",
        reason="no already-local Cloud Spanner emulator or runtime was supplied; nothing was downloaded",
        production_durability_proof=False,
    )
    emit(
        "probe",
        "COMPLETE",
        "finish",
        observed_evidence_limited_to_disposable_scratch=True,
        production_durability_proof=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
