#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import re
import stat
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import asyncpg


DEFAULT_DB_AUTH = ("hindsight", "hindsight")
DEFAULT_DB_NAME = "hindsight"
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 30
DEFAULT_TRANSACTION_TIMEOUT_SECONDS = 300
CLEANUP_ADVISORY_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtext('hindsight-single-bank-cleanup'))"
)
EXPECTED_BANK_ID_TABLES = {
    ("public", "async_operations"),
    ("public", "audit_log"),
    ("public", "bank_stats_cache"),
    ("public", "banks"),
    ("public", "chunks"),
    ("public", "directives"),
    ("public", "documents"),
    ("public", "entities"),
    ("public", "graph_maintenance_queue"),
    ("public", "invalidated_memory_units"),
    ("public", "llm_requests"),
    ("public", "memory_links"),
    ("public", "memory_units"),
    ("public", "mental_model_history"),
    ("public", "mental_models"),
    ("public", "observation_history"),
    ("public", "webhooks"),
}


class MigrationError(RuntimeError):
    pass


class MigrationTransactionTimeout(TimeoutError):
    """Only the cleanup transaction's own deadline expired."""


class MigrationNotCommitted(MigrationError):
    """The read-only reconciliation found source-bank rows."""


@asynccontextmanager
async def mutation_timeout(seconds: float):
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is None:
        raise MigrationError("mutation transaction has no active asyncio task")
    expired = False

    def cancel_transaction() -> None:
        nonlocal expired
        expired = True
        task.cancel()

    timer = loop.call_later(seconds, cancel_transaction)
    try:
        yield
    except asyncio.CancelledError:
        if expired:
            raise MigrationTransactionTimeout from None
        raise
    finally:
        timer.cancel()


@asynccontextmanager
async def mutation_transaction(conn: asyncpg.Connection, seconds: float):
    async with conn.transaction():
        async with mutation_timeout(seconds):
            yield


@asynccontextmanager
async def inspection_transaction(conn: asyncpg.Connection):
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        yield


@dataclass(frozen=True)
class BankTable:
    schema: str
    table: str

    @property
    def qualified(self) -> str:
        return f"{quote_ident(self.schema)}.{quote_ident(self.table)}"


@dataclass(frozen=True)
class ForeignKey:
    schema: str
    table: str
    name: str
    referenced_schema: str
    definition: str

    @property
    def table_qualified(self) -> str:
        return f"{quote_ident(self.schema)}.{quote_ident(self.table)}"


@dataclass(frozen=True)
class Pg0Binding:
    instance: str
    data_dir: Path
    port: int
    database: str
    user: str

    @property
    def database_url(self) -> str:
        _, password = DEFAULT_DB_AUTH
        return (
            f"postgresql://{self.user}:{password}@127.0.0.1:{self.port}/"
            f"{self.database}"
        )


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sanitize_profile(profile: str | None) -> str:
    if not profile:
        return "default"
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", profile) is None:
        raise MigrationError("profile name is not canonical for pg0")
    return profile


def _trusted_metadata(path: Path, label: str, *, directory: bool) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise MigrationError(f"missing {label}: {path}") from error
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or (not directory and metadata.st_nlink != 1)
    ):
        raise MigrationError(f"untrusted {label}: {path}")
    return metadata


def _read_trusted_file(path: Path, label: str, *, max_bytes: int) -> bytes:
    before = _trusted_metadata(path, label, directory=False)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(opened.st_mode) & 0o022
                or opened.st_nlink != 1
                or opened.st_size > max_bytes
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise MigrationError(f"untrusted {label}: {path}")
            payload = os.read(descriptor, max_bytes + 1)
            if len(payload) > max_bytes or os.read(descriptor, 1):
                raise MigrationError(f"oversized {label}: {path}")
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = path.lstat()
    except MigrationError:
        raise
    except OSError as error:
        raise MigrationError(f"cannot read {label}: {path}") from error
    identities = {
        (metadata.st_dev, metadata.st_ino)
        for metadata in (before, opened, after, current)
    }
    if len(identities) != 1:
        raise MigrationError(f"changed {label}: {path}")
    return payload


def pg0_binding_for_profile(profile: str) -> Pg0Binding:
    instance = f"hindsight-embed-{sanitize_profile(profile)}"
    home = Path.home()
    instances = home / ".pg0" / "instances"
    registration = instances / instance
    data_dir = registration / "data"
    manifest_file = registration / "instance.json"
    pid_file = data_dir / "postmaster.pid"
    for path, label in (
        (home, "home directory"),
        (home / ".pg0", "pg0 directory"),
        (instances, "pg0 instances directory"),
        (registration, "pg0 instance registration"),
        (data_dir, "pg0 data directory"),
    ):
        _trusted_metadata(path, label, directory=True)

    try:
        manifest = json.loads(
            _read_trusted_file(
                manifest_file, "pg0 instance manifest", max_bytes=65536
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"malformed pg0 instance manifest: {manifest_file}") from error
    if not isinstance(manifest, dict):
        raise MigrationError(f"malformed pg0 instance manifest: {manifest_file}")
    try:
        lines = _read_trusted_file(
            pid_file, "pg0 postmaster file", max_bytes=4096
        ).decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise MigrationError(f"malformed pg0 postmaster file: {pid_file}") from error
    if len(lines) < 4:
        raise MigrationError(f"malformed pg0 postmaster file: {pid_file}")

    pid = lines[0].strip()
    registered_pid = manifest.get("pid")
    port = lines[3].strip()
    if not pid.isdigit() or int(pid) <= 0 or registered_pid != int(pid):
        raise MigrationError(f"pg0 postmaster file has invalid PID: {pid_file}")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise MigrationError(f"pg0 postmaster file has invalid port: {pid_file}")
    user, _ = DEFAULT_DB_AUTH
    try:
        registered_data_dir = Path(manifest["data_dir"]).resolve(strict=True)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise MigrationError(f"malformed pg0 instance manifest: {manifest_file}") from error
    if (
        lines[1] != str(data_dir)
        or registered_data_dir != data_dir.resolve(strict=True)
        or manifest.get("port") != int(port)
        or manifest.get("username") != user
        or manifest.get("password") != DEFAULT_DB_AUTH[1]
        or manifest.get("database") != DEFAULT_DB_NAME
    ):
        raise MigrationError(
            f"pg0 registration does not match intended instance: {instance}"
        )
    return Pg0Binding(
        instance=instance,
        data_dir=data_dir.resolve(strict=True),
        port=int(port),
        database=DEFAULT_DB_NAME,
        user=user,
    )


def database_url_for_profile(profile: str) -> str:
    return pg0_binding_for_profile(profile).database_url


async def assert_connected_pg0_instance(
    conn: asyncpg.Connection, profile: str
) -> None:
    binding = pg0_binding_for_profile(profile)
    database = await conn.fetchval("SELECT current_database()")
    database_user = await conn.fetchval("SELECT current_user")
    data_directory = await conn.fetchval("SELECT current_setting('data_directory')")
    port = await conn.fetchval("SELECT inet_server_port()")
    address = await conn.fetchval("SELECT inet_server_addr()::text")
    system_identifier = await conn.fetchval(
        "SELECT system_identifier::text FROM pg_control_system()"
    )
    try:
        connected_data_dir = Path(data_directory).resolve(strict=True)
        loopback = ipaddress.ip_interface(address).ip.is_loopback
    except (OSError, TypeError, ValueError):
        connected_data_dir = None
        loopback = False
    if (
        database != binding.database
        or database_user != binding.user
        or connected_data_dir != binding.data_dir
        or port != binding.port
        or not loopback
        or not isinstance(system_identifier, str)
        or not system_identifier.isdigit()
    ):
        raise MigrationError(
            f"connected database does not match intended pg0 instance: {binding.instance}"
        )


async def fetch_bank_tables(conn: asyncpg.Connection) -> list[BankTable]:
    rows = await conn.fetch(
        """
        SELECT columns.table_schema, columns.table_name
        FROM information_schema.columns
        JOIN information_schema.tables
          ON tables.table_schema = columns.table_schema
         AND tables.table_name = columns.table_name
        WHERE columns.table_schema <> 'information_schema'
          AND columns.table_schema !~ '^pg_'
          AND columns.column_name = 'bank_id'
          AND tables.table_type = 'BASE TABLE'
        ORDER BY columns.table_schema, columns.table_name
        """
    )
    return [BankTable(row["table_schema"], row["table_name"]) for row in rows]


def validate_bank_tables(tables: list[BankTable]) -> None:
    actual = {(table.schema, table.table) for table in tables}
    missing = sorted(EXPECTED_BANK_ID_TABLES - actual)
    extra = sorted(actual - EXPECTED_BANK_ID_TABLES)
    if extra:
        raise MigrationError(
            "unreviewed bank_id tables: " + ", ".join(f"{schema}.{table}" for schema, table in extra)
        )
    if missing:
        message = "missing reviewed bank_id tables: " + ", ".join(f"{schema}.{table}" for schema, table in missing)
        if os.getenv("HINDSIGHT_EMBED_MIGRATION_STRICT_SCHEMA"):
            raise MigrationError(message)
        print(f"warning: {message}", file=sys.stderr)


async def fetch_unhandled_bank_columns(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT columns.table_schema, columns.table_name, columns.column_name
        FROM information_schema.columns
        JOIN information_schema.tables
          ON tables.table_schema = columns.table_schema
         AND tables.table_name = columns.table_name
        WHERE columns.table_schema <> 'information_schema'
          AND columns.table_schema !~ '^pg_'
          AND lower(columns.column_name) LIKE '%bank%'
          AND columns.column_name <> 'bank_id'
          AND tables.table_type = 'BASE TABLE'
        ORDER BY columns.table_schema, columns.table_name, columns.column_name
        """
    )
    return [f"{row['table_schema']}.{row['table_name']}.{row['column_name']}" for row in rows]


async def fetch_bank_counts(
    conn: asyncpg.Connection,
    tables: list[BankTable],
    bank_ids: list[str],
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for table in tables:
        table_counts: dict[str, int] = {}
        for bank_id in bank_ids:
            count = await conn.fetchval(
                f"SELECT count(*) FROM {table.qualified} WHERE bank_id = $1",
                bank_id,
            )
            table_counts[bank_id] = int(count or 0)
        counts[f"{table.schema}.{table.table}"] = table_counts
    return counts


async def fetch_distinct_bank_ids(conn: asyncpg.Connection, tables: list[BankTable]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for table in tables:
        rows = await conn.fetch(
            f"SELECT DISTINCT bank_id FROM {table.qualified} WHERE bank_id IS NOT NULL ORDER BY bank_id"
        )
        bank_ids = [row["bank_id"] for row in rows]
        if bank_ids:
            values[f"{table.schema}.{table.table}"] = bank_ids
    return values


async def fetch_foreign_keys(conn: asyncpg.Connection) -> list[ForeignKey]:
    rows = await conn.fetch(
        """
        SELECT ns.nspname AS schema_name,
               rel.relname AS table_name,
               con.conname AS constraint_name,
               ref_ns.nspname AS referenced_schema_name,
               pg_get_constraintdef(con.oid) AS definition
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        JOIN pg_class ref_rel ON ref_rel.oid = con.confrelid
        JOIN pg_namespace ref_ns ON ref_ns.oid = ref_rel.relnamespace
        WHERE ns.nspname <> 'information_schema'
          AND ns.nspname !~ '^pg_'
          AND con.contype = 'f'
          AND (
            EXISTS (
              SELECT 1
              FROM unnest(con.conkey) AS key(attnum)
              JOIN pg_attribute attr ON attr.attrelid = con.conrelid AND attr.attnum = key.attnum
              WHERE attr.attname = 'bank_id'
            )
            OR EXISTS (
              SELECT 1
              FROM unnest(con.confkey) AS key(attnum)
              JOIN pg_attribute attr ON attr.attrelid = con.confrelid AND attr.attnum = key.attnum
              WHERE attr.attname = 'bank_id'
            )
          )
        ORDER BY ns.nspname, rel.relname, con.conname
        """
    )
    return [
        ForeignKey(
            row["schema_name"],
            row["table_name"],
            row["constraint_name"],
            row["referenced_schema_name"],
            row["definition"],
        )
        for row in rows
    ]


def validate_foreign_keys(foreign_keys: list[ForeignKey]) -> None:
    non_public = sorted(
        f"{foreign_key.schema}.{foreign_key.table}.{foreign_key.name}"
        for foreign_key in foreign_keys
        if (
            foreign_key.schema != "public"
            or foreign_key.referenced_schema != "public"
        )
    )
    if non_public:
        raise MigrationError(
            "unreviewed non-public bank foreign keys: "
            + ", ".join(non_public)
        )


async def lock_tables(conn: asyncpg.Connection, tables: list[BankTable]) -> None:
    if not tables:
        return

    qualified_tables = ", ".join(table.qualified for table in tables)
    await conn.execute(f"LOCK TABLE {qualified_tables} IN SHARE ROW EXCLUSIVE MODE")


async def acquire_cleanup_advisory_lock(conn: asyncpg.Connection) -> None:
    await conn.execute(CLEANUP_ADVISORY_LOCK_SQL)


async def execute_bank_schema_ddl(conn: asyncpg.Connection, sql: str) -> None:
    # PostgreSQL transaction-level advisory locks are reentrant. Reacquiring the
    # same cleanup lock at every DDL boundary makes the serialization contract
    # local to the operation instead of relying on distant call ordering.
    await acquire_cleanup_advisory_lock(conn)
    await conn.execute(sql)


def print_counts(title: str, counts: dict[str, dict[str, int]]) -> None:
    print(title)
    for table, table_counts in counts.items():
        nonzero = {bank_id: count for bank_id, count in table_counts.items() if count}
        if not nonzero:
            continue
        rendered = ", ".join(f"{bank_id}={count}" for bank_id, count in nonzero.items())
        print(f"  {table}: {rendered}")


def source_reference_counts(
    counts: dict[str, dict[str, int]],
    source_banks: list[str],
    *,
    include_banks_table: bool = True,
) -> dict[str, int]:
    return {
        bank_id: sum(
            table_counts.get(bank_id, 0)
            for table, table_counts in counts.items()
            if include_banks_table or table != "public.banks"
        )
        for bank_id in source_banks
    }


def validate_distinct_bank_ids(
    distinct_bank_ids: dict[str, list[str]],
    source_banks: list[str],
    target_bank: str,
) -> None:
    expected = {*source_banks, target_bank}
    unexpected = sorted(
        {
            value
            for values in distinct_bank_ids.values()
            for value in values
            if value not in expected
        }
    )
    if unexpected:
        raise MigrationError(
            "unexpected bank IDs outside the approved migration set: "
            + ", ".join(unexpected)
        )


async def assert_target_bank(
    conn: asyncpg.Connection,
    target_bank: str,
) -> None:
    target_exists = await conn.fetchval('SELECT EXISTS (SELECT 1 FROM "public"."banks" WHERE bank_id = $1)', target_bank)
    if not target_exists:
        raise MigrationError(f"target bank does not exist: {target_bank}")


async def assert_source_bank_rows_deleted(
    conn: asyncpg.Connection, source_banks: list[str]
) -> None:
    remaining = await conn.fetchval(
        'SELECT count(*) FROM "public"."banks" '
        "WHERE bank_id = ANY($1::text[])",
        source_banks,
    )
    if int(remaining or 0):
        raise MigrationError(
            f"{int(remaining)} source bank rows remain after delete"
        )


async def migrate(args: argparse.Namespace) -> None:
    database_url = database_url_for_profile(args.profile)
    statement_timeout_seconds = getattr(
        args, "statement_timeout_seconds", DEFAULT_STATEMENT_TIMEOUT_SECONDS
    )
    transaction_timeout_seconds = getattr(
        args, "transaction_timeout_seconds", DEFAULT_TRANSACTION_TIMEOUT_SECONDS
    )
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "SELECT set_config('statement_timeout', $1, false)",
            f"{statement_timeout_seconds * 1000}ms",
        )
        bank_ids_to_count = [*args.source_bank, args.target_bank]
        if args.mode in {"dry-run", "verify-committed"}:
            async with inspection_transaction(conn):
                await assert_connected_pg0_instance(conn, args.profile)
                tables = await fetch_bank_tables(conn)
                validate_bank_tables(tables)
                update_tables = [
                    table for table in tables if table.table != "banks"
                ]
                counts = await fetch_bank_counts(
                    conn, tables, bank_ids_to_count
                )
                distinct_bank_ids = await fetch_distinct_bank_ids(conn, tables)
                foreign_keys = await fetch_foreign_keys(conn)
                validate_foreign_keys(foreign_keys)
                unhandled_columns = await fetch_unhandled_bank_columns(conn)
                validate_distinct_bank_ids(
                    distinct_bank_ids, args.source_bank, args.target_bank
                )
                if unhandled_columns:
                    raise MigrationError(
                        "schema has bank-like columns that are not handled by this migration: "
                        + ", ".join(unhandled_columns)
                    )
                await assert_target_bank(conn, args.target_bank)

                if args.mode == "verify-committed":
                    source_refs = source_reference_counts(
                        counts, args.source_bank, include_banks_table=False
                    )
                    source_non_bank_total = sum(source_refs.values())
                    source_bank_row_total = sum(
                        counts.get("public.banks", {}).get(bank_id, 0)
                        for bank_id in args.source_bank
                    )
                    if source_non_bank_total or source_bank_row_total:
                        raise MigrationNotCommitted(
                            "migration is not committed: source-bank references remain"
                        )
                    print("verify-committed: no source-bank references remain")
                    return

                print(f"profile: {args.profile}")
                print(f"target bank: {args.target_bank}")
                print(f"source banks: {', '.join(args.source_bank)}")
                print(
                    f"bank-scoped tables: {len(update_tables)} updatable, "
                    "banks row kept for target"
                )
                print(
                    f"foreign keys to cycle transactionally: {len(foreign_keys)}"
                )
                print_counts("current rows by planned bank:", counts)

                source_refs = source_reference_counts(
                    counts, args.source_bank, include_banks_table=False
                )
                source_non_bank_total = sum(source_refs.values())
                source_bank_row_total = sum(
                    counts.get("public.banks", {}).get(bank_id, 0)
                    for bank_id in args.source_bank
                )
                if source_non_bank_total == 0 and source_bank_row_total == 0:
                    print("already clean: no source-bank references remain")
                    return
                if source_non_bank_total == 0:
                    print(
                        f"dry-run: would delete {source_bank_row_total} source bank row(s); "
                        "no non-bank references remain"
                    )
                    return
                target_nonempty = sum(
                    table_counts.get(args.target_bank, 0)
                    for table, table_counts in counts.items()
                    if table != "public.banks"
                )
                if target_nonempty and not args.allow_nonempty_target:
                    raise MigrationError(
                        f"target bank has {target_nonempty} non-bank rows; rerun with "
                        "--allow-nonempty-target only after confirming they are safe to merge"
                    )
                if target_nonempty:
                    print(
                        "nonempty target override: "
                        f"{target_nonempty} existing non-bank rows"
                    )
                print("dry-run: no database changes made")
            return

        await assert_connected_pg0_instance(conn, args.profile)
        async with mutation_transaction(conn, transaction_timeout_seconds):
            await conn.execute(
                "SELECT set_config('statement_timeout', $1, true)",
                f"{statement_timeout_seconds * 1000}ms",
            )
            await conn.execute("SET LOCAL lock_timeout = '10s'")
            await acquire_cleanup_advisory_lock(conn)
            tables = await fetch_bank_tables(conn)
            validate_bank_tables(tables)
            await lock_tables(conn, tables)

            locked_tables = await fetch_bank_tables(conn)
            validate_bank_tables(locked_tables)
            if locked_tables != tables:
                raise MigrationError("bank-scoped schema changed while migration locks were acquired")
            tables = locked_tables
            update_tables = [table for table in tables if table.table != "banks"]
            foreign_keys = await fetch_foreign_keys(conn)
            validate_foreign_keys(foreign_keys)
            unhandled_columns = await fetch_unhandled_bank_columns(conn)
            if unhandled_columns:
                raise MigrationError(
                    "schema has bank-like columns that are not handled by this migration: "
                    + ", ".join(unhandled_columns)
                )
            await assert_target_bank(conn, args.target_bank)

            locked_distinct_bank_ids = await fetch_distinct_bank_ids(conn, tables)
            validate_distinct_bank_ids(
                locked_distinct_bank_ids, args.source_bank, args.target_bank
            )
            locked_counts = await fetch_bank_counts(conn, tables, bank_ids_to_count)
            print(f"profile: {args.profile}")
            print(f"target bank: {args.target_bank}")
            print(f"source banks: {', '.join(args.source_bank)}")
            print(f"bank-scoped tables: {len(update_tables)} updatable, banks row kept for target")
            print(f"foreign keys to cycle transactionally: {len(foreign_keys)}")
            print_counts("current rows by planned bank:", locked_counts)
            locked_source_refs = source_reference_counts(locked_counts, args.source_bank, include_banks_table=False)
            locked_source_non_bank_total = sum(locked_source_refs.values())
            locked_source_bank_row_total = sum(
                locked_counts.get("public.banks", {}).get(bank_id, 0) for bank_id in args.source_bank
            )
            if locked_source_non_bank_total == 0 and locked_source_bank_row_total == 0:
                print("already clean: no source-bank references remain")
                return
            if locked_source_non_bank_total == 0:
                status = await conn.execute(
                    'DELETE FROM "public"."banks" WHERE bank_id = ANY($1::text[])',
                    args.source_bank,
                )
                await assert_source_bank_rows_deleted(conn, args.source_bank)
                print(f"public.banks: {status}")
                print("apply: source bank rows removed")
                return

            target_nonempty = sum(
                table_counts.get(args.target_bank, 0)
                for table, table_counts in locked_counts.items()
                if table != "public.banks"
            )
            if target_nonempty and not args.allow_nonempty_target:
                raise MigrationError(
                    f"target bank has {target_nonempty} non-bank rows; rerun with --allow-nonempty-target "
                    "only after confirming they are safe to merge"
                )
            if target_nonempty:
                print(f"nonempty target override: {target_nonempty} existing non-bank rows")

            for fk in foreign_keys:
                await execute_bank_schema_ddl(
                    conn,
                    f"ALTER TABLE {fk.table_qualified} DROP CONSTRAINT {quote_ident(fk.name)}"
                )

            for table in update_tables:
                status = await conn.execute(
                    f"UPDATE {table.qualified} SET bank_id = $1 WHERE bank_id = ANY($2::text[])",
                    args.target_bank,
                    args.source_bank,
                )
                print(f"{table.schema}.{table.table}: {status}")

            remaining = await fetch_bank_counts(conn, update_tables, args.source_bank)
            remaining_total = sum(sum(table_counts.values()) for table_counts in remaining.values())
            if remaining_total:
                raise MigrationError(f"{remaining_total} source-bank references remain after update")

            await conn.execute(
                'DELETE FROM "public"."banks" WHERE bank_id = ANY($1::text[])',
                args.source_bank,
            )
            await assert_source_bank_rows_deleted(conn, args.source_bank)

            for fk in foreign_keys:
                await execute_bank_schema_ddl(
                    conn,
                    f"ALTER TABLE {fk.table_qualified} ADD CONSTRAINT {quote_ident(fk.name)} {fk.definition}"
                )

        print("apply: migration committed")
    finally:
        await conn.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate Hindsight rows into a single canonical bank.")
    parser.add_argument(
        "--mode",
        choices=("dry-run", "apply", "verify-committed"),
        default="dry-run",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--source-bank", action="append", required=True)
    parser.add_argument("--target-bank", required=True)
    parser.add_argument("--allow-nonempty-target", action="store_true")
    parser.add_argument(
        "--statement-timeout-seconds",
        type=int,
        default=DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--transaction-timeout-seconds",
        type=int,
        default=DEFAULT_TRANSACTION_TIMEOUT_SECONDS,
    )
    args = parser.parse_args(argv)
    if args.statement_timeout_seconds <= 0:
        raise MigrationError("statement timeout must be greater than zero")
    if args.transaction_timeout_seconds <= 0:
        raise MigrationError("transaction timeout must be greater than zero")
    seen: set[str] = set()
    args.source_bank = [bank for bank in args.source_bank if not (bank in seen or seen.add(bank))]
    if args.target_bank in args.source_bank:
        raise MigrationError("target bank cannot also be a source bank")
    return args


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        asyncio.run(migrate(args))
    except MigrationTransactionTimeout:
        print(
            "hindsight-embed-single-bank-migrate: mutation transaction timed out and was rolled back",
            file=sys.stderr,
        )
        return 1
    except MigrationNotCommitted as exc:
        print(f"hindsight-embed-single-bank-migrate: {exc}", file=sys.stderr)
        return 3
    except (MigrationError, OSError, asyncpg.PostgresError) as exc:
        print(f"hindsight-embed-single-bank-migrate: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
