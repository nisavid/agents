from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class _PostgresError(Exception):
    pass


asyncpg = types.ModuleType("asyncpg")
asyncpg.Connection = object
asyncpg.PostgresError = _PostgresError

MODULE_PATH = Path(__file__).parents[1] / "libexec" / "hindsight-embed-single-bank-migrate.py"
SPEC = importlib.util.spec_from_file_location("hindsight_embed_single_bank_migrate", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
_previous_asyncpg = sys.modules.get("asyncpg")
_previous_migration = sys.modules.get(SPEC.name)
try:
    sys.modules["asyncpg"] = asyncpg
    sys.modules[SPEC.name] = migration
    SPEC.loader.exec_module(migration)
finally:
    for _name, _previous in (
        ("asyncpg", _previous_asyncpg),
        (SPEC.name, _previous_migration),
    ):
        if _previous is None:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _previous


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False


class _Connection:
    def transaction(self, **_options) -> _Transaction:
        return _Transaction()

    async def execute(self, *_args) -> str:
        return "OK"

    async def close(self) -> None:
        return None


class SingleBankMigrationLockTests(unittest.IsolatedAsyncioTestCase):
    def _pg0_instance(self, home: Path, *, profile: str = "profile") -> Path:
        registration = home / ".pg0" / "instances" / f"hindsight-embed-{profile}"
        data = registration / "data"
        data.mkdir(parents=True)
        manifest = {
            "pid": 1234,
            "port": 5544,
            "data_dir": str(data),
            "installation_dir": str(home / ".pg0" / "installation"),
            "username": "hindsight",
            "password": "hindsight",
            "database": "hindsight",
            "version": "18.1.0",
        }
        (registration / "instance.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (data / "postmaster.pid").write_text(
            f"1234\n{data}\n1700000000\n5544\n/tmp\nlocalhost\n",
            encoding="ascii",
        )
        os.chmod(registration / "instance.json", 0o600)
        os.chmod(data / "postmaster.pid", 0o600)
        return registration

    def test_pg0_binding_authenticates_registration_and_postmaster_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            registration = self._pg0_instance(home)
            with patch.object(Path, "home", return_value=home):
                binding = migration.pg0_binding_for_profile("profile")
            self.assertEqual(binding.instance, "hindsight-embed-profile")
            self.assertEqual(binding.data_dir, (registration / "data").resolve())
            self.assertEqual(binding.port, 5544)
            self.assertEqual(
                binding.database_url,
                "postgresql://hindsight:hindsight@127.0.0.1:5544/hindsight",
            )

            os.chmod(registration / "data" / "postmaster.pid", 0o666)
            with (
                patch.object(Path, "home", return_value=home),
                self.assertRaisesRegex(migration.MigrationError, "untrusted pg0 postmaster"),
            ):
                migration.pg0_binding_for_profile("profile")

    async def test_connected_pg0_identity_must_match_authenticated_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            registration = self._pg0_instance(home)
            values = iter(
                (
                    "hindsight",
                    "hindsight",
                    str(registration / "data"),
                    5544,
                    "127.0.0.1",
                    "123456789",
                )
            )
            connection = _Connection()
            connection.fetchval = AsyncMock(side_effect=lambda *_args: next(values))
            with patch.object(Path, "home", return_value=home):
                await migration.assert_connected_pg0_instance(connection, "profile")

            values = iter(
                (
                    "other",
                    "hindsight",
                    str(registration / "data"),
                    5544,
                    "127.0.0.1",
                    "123456789",
                )
            )
            connection.fetchval = AsyncMock(side_effect=lambda *_args: next(values))
            with (
                patch.object(Path, "home", return_value=home),
                self.assertRaisesRegex(migration.MigrationError, "intended pg0 instance"),
            ):
                await migration.assert_connected_pg0_instance(connection, "profile")

    async def test_source_bank_delete_is_verified(self) -> None:
        connection = _Connection()
        connection.fetchval = AsyncMock(return_value=1)
        with self.assertRaisesRegex(migration.MigrationError, "remain after delete"):
            await migration.assert_source_bank_rows_deleted(connection, ["legacy"])

    async def test_every_bank_schema_ddl_reacquires_cleanup_lock(self) -> None:
        events: list[str] = []
        connection = _Connection()

        async def execute(sql, *_args) -> str:
            if sql == migration.CLEANUP_ADVISORY_LOCK_SQL:
                events.append("lock")
            else:
                events.append(sql)
            return "OK"

        connection.execute = execute
        await migration.execute_bank_schema_ddl(
            connection, 'ALTER TABLE "public"."chunks" DROP CONSTRAINT "fk"'
        )
        await migration.execute_bank_schema_ddl(
            connection,
            'ALTER TABLE "public"."chunks" ADD CONSTRAINT "fk" FOREIGN KEY (bank_id) REFERENCES banks(bank_id)',
        )
        self.assertEqual(
            events,
            [
                "lock",
                'ALTER TABLE "public"."chunks" DROP CONSTRAINT "fk"',
                "lock",
                'ALTER TABLE "public"."chunks" ADD CONSTRAINT "fk" FOREIGN KEY (bank_id) REFERENCES banks(bank_id)',
            ],
        )

    def test_unselected_banks_fail_closed_outside_approved_set(self) -> None:
        with self.assertRaisesRegex(
            migration.MigrationError, "preserved, unrelated"
        ):
            migration.validate_distinct_bank_ids(
                {
                    "public.banks": ["canonical", "legacy", "preserved"],
                    "public.chunks": ["legacy", "unrelated"],
                },
                ["legacy"],
                "canonical",
            )

    def test_non_public_bank_tables_and_foreign_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            migration.MigrationError, "unreviewed bank_id tables"
        ):
            migration.validate_bank_tables(
                [
                    *(
                        migration.BankTable(schema, table)
                        for schema, table in migration.EXPECTED_BANK_ID_TABLES
                    ),
                    migration.BankTable("private", "shadow_banks"),
                ]
            )
        for foreign_key in (
            migration.ForeignKey(
                "private", "chunks", "fk_private", "public", "FOREIGN KEY"
            ),
            migration.ForeignKey(
                "public", "chunks", "fk_private_ref", "private", "FOREIGN KEY"
            ),
        ):
            with self.subTest(foreign_key=foreign_key), self.assertRaisesRegex(
                migration.MigrationError, "non-public bank foreign keys"
            ):
                migration.validate_foreign_keys([foreign_key])

    async def test_schema_discovery_scans_every_non_system_schema(self) -> None:
        queries = []
        connection = _Connection()

        async def fetch(sql):
            queries.append(sql)
            return []

        connection.fetch = fetch
        await migration.fetch_bank_tables(connection)
        await migration.fetch_unhandled_bank_columns(connection)
        await migration.fetch_foreign_keys(connection)

        self.assertEqual(len(queries), 3)
        for query in queries:
            self.assertNotIn("= 'public'", query)
            self.assertIn("<> 'information_schema'", query)
            self.assertIn("!~ '^pg_'", query)

    def test_timeout_arguments_are_positive_and_configurable(self) -> None:
        args = migration.parse_args(
            [
                "--mode", "apply",
                "--profile", "profile",
                "--source-bank", "legacy",
                "--target-bank", "canonical",
                "--statement-timeout-seconds", "7",
                "--transaction-timeout-seconds", "11",
            ]
        )
        self.assertEqual(args.statement_timeout_seconds, 7)
        self.assertEqual(args.transaction_timeout_seconds, 11)
        for option in ("--statement-timeout-seconds", "--transaction-timeout-seconds"):
            with self.subTest(option=option):
                with self.assertRaisesRegex(migration.MigrationError, "greater than zero"):
                    migration.parse_args(
                        [
                            "--profile", "profile",
                            "--source-bank", "legacy",
                            "--target-bank", "canonical",
                            option, "0",
                        ]
                    )

    def test_profile_sanitization_preserves_default_and_rejects_rewrites(self) -> None:
        self.assertEqual(migration.sanitize_profile(None), "default")
        self.assertEqual(migration.sanitize_profile(""), "default")
        self.assertEqual(migration.sanitize_profile("profile_2"), "profile_2")
        self.assertEqual(migration.sanitize_profile("team.prod"), "team.prod")
        for profile in ("profile two", "-profile", "profile/two"):
            with self.subTest(profile=profile):
                with self.assertRaisesRegex(migration.MigrationError, "canonical"):
                    migration.sanitize_profile(profile)

    async def test_reasserts_target_bank_after_table_locks(self) -> None:
        events: list[str] = []
        connection = _Connection()
        tables = [migration.BankTable("public", "banks"), migration.BankTable("public", "chunks")]
        args = argparse.Namespace(
            mode="apply",
            profile="profile",
            source_bank=["legacy"],
            target_bank="canonical",
            allow_nonempty_target=False,
            statement_timeout_seconds=30,
            transaction_timeout_seconds=300,
        )
        counts = {
            "public.banks": {"legacy": 1, "canonical": 1},
            "public.chunks": {"legacy": 1, "canonical": 0},
        }
        remaining = {"public.chunks": {"legacy": 0}}
        verify_deleted = AsyncMock()

        async def execute(sql, *_args) -> str:
            if "pg_advisory_xact_lock" in sql:
                events.append("advisory-lock")
            return "OK"

        connection.execute = execute

        async def fetch_tables(_connection):
            events.append("discover-schema")
            return tables

        async def assert_target(_connection, _target) -> None:
            events.append("assert-target")

        async def lock(_connection, _tables) -> None:
            events.append("lock-tables")

        asyncpg.connect = AsyncMock(return_value=connection)
        with (
            patch.object(migration, "database_url_for_profile", return_value="postgresql://example"),
            patch.object(migration, "assert_connected_pg0_instance", AsyncMock()),
            patch.object(migration, "assert_source_bank_rows_deleted", verify_deleted),
            patch.object(migration, "fetch_bank_tables", side_effect=fetch_tables),
            patch.object(migration, "validate_bank_tables"),
            patch.object(migration, "fetch_bank_counts", AsyncMock(side_effect=[counts, remaining])),
            patch.object(
                migration,
                "fetch_distinct_bank_ids",
                AsyncMock(return_value={"public.banks": ["canonical", "legacy"]}),
            ),
            patch.object(migration, "fetch_foreign_keys", AsyncMock(return_value=[])),
            patch.object(migration, "fetch_unhandled_bank_columns", AsyncMock(return_value=[])),
            patch.object(migration, "assert_target_bank", side_effect=assert_target),
            patch.object(migration, "lock_tables", side_effect=lock),
        ):
            await migration.migrate(args)

        self.assertEqual(
            events,
            [
                "advisory-lock",
                "discover-schema",
                "lock-tables",
                "discover-schema",
                "assert-target",
            ],
        )
        verify_deleted.assert_awaited_once_with(connection, ["legacy"])

    async def test_bank_only_delete_is_verified_before_success(self) -> None:
        connection = _Connection()
        tables = [migration.BankTable("public", "banks")]
        args = argparse.Namespace(
            mode="apply",
            profile="profile",
            source_bank=["legacy"],
            target_bank="canonical",
            allow_nonempty_target=False,
            statement_timeout_seconds=30,
            transaction_timeout_seconds=300,
        )
        counts = {
            "public.banks": {"legacy": 1, "canonical": 1},
        }
        verify_deleted = AsyncMock()
        asyncpg.connect = AsyncMock(return_value=connection)
        with (
            patch.object(migration, "database_url_for_profile", return_value="postgresql://example"),
            patch.object(migration, "assert_connected_pg0_instance", AsyncMock()),
            patch.object(migration, "fetch_bank_tables", AsyncMock(return_value=tables)),
            patch.object(migration, "validate_bank_tables"),
            patch.object(migration, "lock_tables", AsyncMock()),
            patch.object(migration, "fetch_bank_counts", AsyncMock(return_value=counts)),
            patch.object(
                migration,
                "fetch_distinct_bank_ids",
                AsyncMock(return_value={"public.banks": ["canonical", "legacy"]}),
            ),
            patch.object(migration, "fetch_foreign_keys", AsyncMock(return_value=[])),
            patch.object(migration, "fetch_unhandled_bank_columns", AsyncMock(return_value=[])),
            patch.object(migration, "assert_target_bank", AsyncMock()),
            patch.object(migration, "assert_source_bank_rows_deleted", verify_deleted),
        ):
            await migration.migrate(args)
        verify_deleted.assert_awaited_once_with(connection, ["legacy"])

    async def test_transaction_timeout_rolls_back_the_whole_mutation(self) -> None:
        transaction_exit_types: list[type[BaseException] | None] = []
        context_events: list[str] = []

        class RecordingTransaction:
            async def __aenter__(self):
                context_events.append("transaction-enter")
                return None

            async def __aexit__(self, exc_type, _exc, _traceback):
                context_events.append("transaction-exit")
                transaction_exit_types.append(exc_type)
                return False

        class RecordingConnection(_Connection):
            closed = False

            def transaction(self):
                return RecordingTransaction()

            async def close(self):
                self.closed = True

        connection = RecordingConnection()
        statements: list[tuple[str, tuple[object, ...]]] = []

        async def execute(sql, *values):
            statements.append((sql, values))
            return "OK"

        async def never_returns(_connection):
            await asyncio.Event().wait()

        original_timeout = migration.mutation_timeout

        @asynccontextmanager
        async def recording_timeout(seconds):
            context_events.append("timeout-enter")
            try:
                async with original_timeout(seconds):
                    yield
            finally:
                context_events.append("timeout-exit")

        connection.execute = execute
        args = argparse.Namespace(
            mode="apply",
            profile="profile",
            source_bank=["legacy"],
            target_bank="canonical",
            allow_nonempty_target=False,
            statement_timeout_seconds=2,
            transaction_timeout_seconds=0.01,
        )
        asyncpg.connect = AsyncMock(return_value=connection)
        with (
            patch.object(migration, "database_url_for_profile", return_value="postgresql://example"),
            patch.object(migration, "assert_connected_pg0_instance", AsyncMock()),
            patch.object(migration, "mutation_timeout", recording_timeout),
            patch.object(migration, "fetch_bank_tables", side_effect=never_returns),
        ):
            with self.assertRaises(migration.MigrationTransactionTimeout):
                await migration.migrate(args)

        self.assertEqual(
            statements[0],
            ("SELECT set_config('statement_timeout', $1, false)", ("2000ms",)),
        )
        self.assertEqual(
            transaction_exit_types,
            [migration.MigrationTransactionTimeout],
        )
        self.assertEqual(
            context_events,
            ["transaction-enter", "timeout-enter", "timeout-exit", "transaction-exit"],
        )
        self.assertTrue(connection.closed)

    async def test_transaction_timeout_does_not_cancel_commit_exit(self) -> None:
        commit_completed = asyncio.Event()

        class SlowCommit:
            async def __aenter__(self):
                return None

            async def __aexit__(self, exc_type, _exc, _traceback):
                if exc_type is None:
                    await asyncio.sleep(0.03)
                    commit_completed.set()
                return False

        connection = _Connection()
        connection.transaction = lambda: SlowCommit()
        async def complete_transaction() -> None:
            async with migration.mutation_transaction(connection, 0.01):
                pass

        transaction = asyncio.create_task(complete_transaction())
        try:
            await asyncio.wait_for(asyncio.shield(transaction), timeout=1)
        finally:
            if not transaction.done():
                transaction.cancel()
                await asyncio.gather(transaction, return_exceptions=True)
        self.assertTrue(commit_completed.is_set())

    def test_main_does_not_misreport_an_unrelated_timeout(self) -> None:
        with (
            patch.object(migration, "parse_args", return_value=argparse.Namespace()),
            patch.object(migration, "migrate", new=lambda _args: None),
            patch.object(migration.asyncio, "run", side_effect=TimeoutError("watchdog")),
            patch.object(migration.sys, "stderr", new_callable=io.StringIO) as stderr,
        ):
            self.assertEqual(migration.main([]), 1)
        self.assertIn("watchdog", stderr.getvalue())
        self.assertNotIn("was rolled back", stderr.getvalue())

    def test_main_reports_read_only_not_committed_status_distinctly(self) -> None:
        async def not_committed(_args: argparse.Namespace) -> None:
            raise migration.MigrationNotCommitted("source-bank references remain")

        with (
            patch.object(migration, "parse_args", return_value=argparse.Namespace()),
            patch.object(migration, "migrate", new=not_committed),
            patch.object(migration.sys, "stderr", new_callable=io.StringIO) as stderr,
        ):
            self.assertEqual(migration.main([]), 3)
        self.assertIn("source-bank references remain", stderr.getvalue())

    async def test_statement_timeout_is_installed_before_dry_run_inspection(self) -> None:
        transaction_options = []
        inspection_active = False

        class RecordingTransaction(_Transaction):
            async def __aenter__(self):
                nonlocal inspection_active
                inspection_active = True

            async def __aexit__(self, exc_type, exc, traceback):
                nonlocal inspection_active
                inspection_active = False
                return False

        class RecordingConnection(_Connection):
            def transaction(self, **options):
                transaction_options.append(options)
                return RecordingTransaction()

        connection = RecordingConnection()
        statements = []

        async def execute(sql, *values):
            statements.append((sql, values))
            return "OK"

        connection.execute = execute

        async def stop_after_identity(_connection, _profile):
            self.assertTrue(inspection_active)
            raise migration.MigrationError("stop after identity")

        args = argparse.Namespace(
            mode="dry-run",
            profile="profile",
            source_bank=["legacy"],
            target_bank="canonical",
            allow_nonempty_target=False,
            statement_timeout_seconds=7,
            transaction_timeout_seconds=11,
        )
        asyncpg.connect = AsyncMock(return_value=connection)
        with (
            patch.object(
                migration, "database_url_for_profile", return_value="postgresql://example"
            ),
            patch.object(
                migration,
                "assert_connected_pg0_instance",
                AsyncMock(side_effect=stop_after_identity),
            ),
            self.assertRaisesRegex(migration.MigrationError, "stop after identity"),
        ):
            await migration.migrate(args)
        self.assertEqual(
            statements,
            [("SELECT set_config('statement_timeout', $1, false)", ("7000ms",))],
        )
        self.assertEqual(
            transaction_options,
            [{"isolation": "repeatable_read", "readonly": True}],
        )
        self.assertFalse(inspection_active)


if __name__ == "__main__":
    unittest.main()
