from pathlib import Path
import asyncio
from datetime import datetime
import getpass
import os
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.operation_recovery import (  # noqa: E402
    OperationRecoveryError,
)
from hindsight_memory_control_plane.operation_recovery_runtime import (  # noqa: E402
    apply_requeue_transaction,
    live_row_digest,
    read_safe_operation_rows,
    rollback_requeue_transaction,
)


POSTGRES_BIN_ENV = "HINDSIGHT_OPERATION_RECOVERY_POSTGRES_BIN"
SELECTED_ID = "00000000-0000-4000-8000-000000000001"
NONSELECTED_ID = "00000000-0000-4000-8000-000000000002"
CLAIMED_AT = "2026-07-29T12:30:00.000000Z"
UPDATED_AT = "2026-07-29T13:00:00.000000Z"
COMPLETED_AT = "2026-07-29T13:00:00.000000Z"


class OperationRecoveryPostgresTest(unittest.TestCase):
    """Exercise the capsule's mutation policy against disposable PostgreSQL."""

    @classmethod
    def setUpClass(cls):
        postgres_bin_value = os.environ.get(POSTGRES_BIN_ENV)
        if not postgres_bin_value:
            raise unittest.SkipTest(
                f"set {POSTGRES_BIN_ENV} to run disposable PostgreSQL tests"
            )
        cls.postgres_bin = Path(postgres_bin_value).resolve()
        required = ("initdb", "pg_ctl")
        if any(not (cls.postgres_bin / name).is_file() for name in required):
            raise unittest.SkipTest("disposable PostgreSQL binaries are absent")
        try:
            import asyncpg  # noqa: F401
        except ImportError as error:
            raise unittest.SkipTest("asyncpg is unavailable") from error

        cls.temporary = tempfile.TemporaryDirectory(
            prefix="horpg.",
            dir="/private/tmp",
        )
        cls.root = Path(cls.temporary.name)
        cls.data_dir = cls.root / "data"
        cls.socket_dir = cls.root / "socket"
        cls.socket_dir.mkdir(mode=0o700)
        cls.log_path = cls.root / "postgres.log"
        cls.user = getpass.getuser()
        cls.port = 55449
        environment = {**os.environ, "LC_ALL": "C", "HOME": str(cls.root)}
        subprocess.run(
            [
                str(cls.postgres_bin / "initdb"),
                "-D",
                str(cls.data_dir),
                "--auth-local=trust",
                "--auth-host=reject",
                "--username",
                cls.user,
                "--no-instructions",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        try:
            subprocess.run(
                [
                    str(cls.postgres_bin / "pg_ctl"),
                    "-D",
                    str(cls.data_dir),
                    "-l",
                    str(cls.log_path),
                    "-o",
                    f"-F -p {cls.port} -c listen_addresses= "
                    f"-c unix_socket_directories={cls.socket_dir}",
                    "-w",
                    "start",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
        except subprocess.CalledProcessError as error:
            detail = (
                cls.log_path.read_text(encoding="utf-8")
                if cls.log_path.is_file()
                else error.stderr
            )
            raise RuntimeError(
                f"disposable PostgreSQL failed to start: {detail.strip()}"
            ) from error
        try:
            asyncio.run(cls._install_schema())
        except Exception:
            cls._stop_postgres()
            cls.temporary.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls._stop_postgres()
        cls.temporary.cleanup()

    @classmethod
    def _stop_postgres(cls):
        subprocess.run(
            [
                str(cls.postgres_bin / "pg_ctl"),
                "-D",
                str(cls.data_dir),
                "-m",
                "immediate",
                "-w",
                "stop",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    @classmethod
    async def _connect(cls):
        import asyncpg

        return await asyncpg.connect(
            host=str(cls.socket_dir),
            port=cls.port,
            user=cls.user,
            database="postgres",
            timeout=5,
        )

    @classmethod
    async def _install_schema(cls):
        connection = await cls._connect()
        try:
            await connection.execute(
                """
                CREATE TABLE public.hindsight_migration_generation (
                    singleton boolean PRIMARY KEY DEFAULT true,
                    generation bigint NOT NULL
                );
                INSERT INTO public.hindsight_migration_generation
                    (singleton, generation) VALUES (true, 1);
                CREATE TABLE public.async_operations (
                    operation_id uuid PRIMARY KEY,
                    bank_id text NOT NULL,
                    operation_type text NOT NULL,
                    status text NOT NULL,
                    created_at timestamptz NOT NULL,
                    updated_at timestamptz NOT NULL,
                    completed_at timestamptz,
                    retry_count integer NOT NULL,
                    next_retry_at timestamptz,
                    worker_id text,
                    claimed_at timestamptz,
                    task_payload jsonb,
                    result_metadata jsonb,
                    error_message text
                );
                CREATE FUNCTION public.bump_hindsight_migration_generation()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    UPDATE public.hindsight_migration_generation
                    SET generation = generation + 1 WHERE singleton;
                    RETURN NULL;
                END
                $$;
                CREATE TRIGGER hindsight_migration_generation_bump
                AFTER INSERT OR UPDATE OR DELETE ON public.async_operations
                FOR EACH STATEMENT
                EXECUTE FUNCTION public.bump_hindsight_migration_generation();
                """
            )
        finally:
            await connection.close()

    @staticmethod
    async def _insert_operation(
        connection,
        operation_id,
        *,
        status,
        worker_id=None,
        claimed_at=None,
    ):
        await connection.execute(
            """
            INSERT INTO public.async_operations (
                operation_id, bank_id, operation_type, status,
                created_at, updated_at, completed_at, retry_count,
                next_retry_at, worker_id, claimed_at, task_payload,
                result_metadata, error_message
            ) VALUES (
                $1::uuid, 'engineering', 'retain', $2,
                '2026-07-29T12:00:00Z', $3::timestamptz,
                CASE WHEN $2 IN ('failed', 'cancelled')
                     THEN $4::timestamptz ELSE NULL END,
                CASE WHEN $2 IN ('failed', 'cancelled') THEN 2 ELSE 0 END,
                NULL, $5, $6::timestamptz,
                '{"memory": "synthetic disposable payload"}'::jsonb,
                '{}'::jsonb,
                CASE WHEN $2 IN ('failed', 'cancelled')
                     THEN 'provider capacity exhausted' ELSE NULL END
            )
            """,
            operation_id,
            status,
            datetime.fromisoformat(UPDATED_AT.replace("Z", "+00:00")),
            datetime.fromisoformat(COMPLETED_AT.replace("Z", "+00:00")),
            worker_id,
            (
                None
                if claimed_at is None
                else datetime.fromisoformat(
                    claimed_at.replace("Z", "+00:00")
                )
            ),
        )

    @staticmethod
    async def _reset(connection):
        await connection.execute("DELETE FROM public.async_operations")
        await connection.execute(
            "UPDATE public.hindsight_migration_generation "
            "SET generation = 123 WHERE singleton"
        )

    @staticmethod
    def _plan(row):
        return {
            "pre_generation": "systalyze:public:123",
            "selected_operations": [
                {
                    "operation_id": row["operation_id"],
                    "operation_type": row["operation_type"],
                    "expected_status": row["status"],
                    "row_digest": live_row_digest(row),
                    "task_payload_digest": row["task_payload_digest"],
                }
            ],
            "expires_at": int(time.time()) + 300,
        }

    def test_selected_terminal_claim_applies_and_rollback_restores_it(self):
        async def exercise():
            connection = await self._connect()
            try:
                await self._reset(connection)
                await self._insert_operation(
                    connection,
                    SELECTED_ID,
                    status="failed",
                    worker_id="orphaned-worker",
                    claimed_at=CLAIMED_AT,
                )
                await connection.execute(
                    "UPDATE public.hindsight_migration_generation "
                    "SET generation = 123 WHERE singleton"
                )
                selected = (
                    await read_safe_operation_rows(
                        connection,
                        schema="public",
                        bank_id="engineering",
                        operation_ids=[SELECTED_ID],
                    )
                )[0]
                plan = self._plan(selected)
                preimage = {
                    "operation_id": SELECTED_ID,
                    "status": "failed",
                    "task_payload_digest": selected["task_payload_digest"],
                    "error_message": "provider capacity exhausted",
                    "completed_at": selected["completed_at"],
                    "next_retry_at": selected["next_retry_at"],
                    "worker_id": "orphaned-worker",
                    "claimed_at": selected["claimed_at"],
                    "retry_count": selected["retry_count"],
                    "updated_at": selected["updated_at"],
                }

                self.assertEqual(
                    await apply_requeue_transaction(
                        connection,
                        profile_id="systalyze",
                        schema="public",
                        bank_id="engineering",
                        plan=plan,
                    ),
                    ("systalyze:public:123", "systalyze:public:124"),
                )
                applied = await connection.fetchrow(
                    "SELECT status, worker_id, claimed_at, retry_count "
                    "FROM public.async_operations WHERE operation_id = $1::uuid",
                    SELECTED_ID,
                )
                self.assertEqual(dict(applied), {
                    "status": "pending",
                    "worker_id": None,
                    "claimed_at": None,
                    "retry_count": 0,
                })

                self.assertEqual(
                    await rollback_requeue_transaction(
                        connection,
                        profile_id="systalyze",
                        schema="public",
                        bank_id="engineering",
                        plan=plan,
                        application={
                            "post_generation": "systalyze:public:124"
                        },
                        rollback_record={
                            "pre_generation": "systalyze:public:124",
                            "post_generation": "systalyze:public:125",
                        },
                        preimage=[preimage],
                    ),
                    ("systalyze:public:124", "systalyze:public:125"),
                )
                restored = await connection.fetchrow(
                    "SELECT status, worker_id, "
                    "to_char(claimed_at AT TIME ZONE 'UTC', "
                    "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS claimed_at, "
                    "retry_count FROM public.async_operations "
                    "WHERE operation_id = $1::uuid",
                    SELECTED_ID,
                )
                self.assertEqual(dict(restored), {
                    "status": "failed",
                    "worker_id": "orphaned-worker",
                    "claimed_at": CLAIMED_AT,
                    "retry_count": 2,
                })
                self.assertEqual(
                    await connection.fetchval(
                        "SELECT generation FROM "
                        "public.hindsight_migration_generation WHERE singleton"
                    ),
                    125,
                )
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_processing_and_nonselected_terminal_claims_fail_closed(self):
        async def exercise(blocker_status, blocker_id):
            connection = await self._connect()
            try:
                await self._reset(connection)
                selected_status = (
                    "processing" if blocker_id == SELECTED_ID else "failed"
                )
                await self._insert_operation(
                    connection,
                    SELECTED_ID,
                    status=selected_status,
                    worker_id=(
                        "processing-worker"
                        if selected_status == "processing"
                        else None
                    ),
                    claimed_at=(
                        CLAIMED_AT if selected_status == "processing" else None
                    ),
                )
                if blocker_id == NONSELECTED_ID:
                    await self._insert_operation(
                        connection,
                        NONSELECTED_ID,
                        status=blocker_status,
                        worker_id="other-worker",
                        claimed_at=CLAIMED_AT,
                    )
                await connection.execute(
                    "UPDATE public.hindsight_migration_generation "
                    "SET generation = 123 WHERE singleton"
                )
                selected = (
                    await read_safe_operation_rows(
                        connection,
                        schema="public",
                        bank_id="engineering",
                        operation_ids=[SELECTED_ID],
                    )
                )[0]
                plan = self._plan(selected)
                rows_before = [
                    dict(row)
                    for row in await connection.fetch(
                        "SELECT operation_id::text AS operation_id, status, "
                        "worker_id, claimed_at IS NOT NULL AS claimed "
                        "FROM public.async_operations ORDER BY operation_id"
                    )
                ]
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "requires an unclaimed queue",
                ):
                    await apply_requeue_transaction(
                        connection,
                        profile_id="systalyze",
                        schema="public",
                        bank_id="engineering",
                        plan=plan,
                    )
                self.assertEqual(
                    await connection.fetchval(
                        "SELECT generation FROM "
                        "public.hindsight_migration_generation WHERE singleton"
                    ),
                    123,
                )
                rows_after = [
                    dict(row)
                    for row in await connection.fetch(
                        "SELECT operation_id::text AS operation_id, status, "
                        "worker_id, claimed_at IS NOT NULL AS claimed "
                        "FROM public.async_operations ORDER BY operation_id"
                    )
                ]
                self.assertEqual(rows_after, rows_before)
            finally:
                await connection.close()

        cases = (
            ("processing", SELECTED_ID),
            ("pending", NONSELECTED_ID),
            ("failed", NONSELECTED_ID),
            ("cancelled", NONSELECTED_ID),
        )
        for blocker_status, blocker_id in cases:
            with self.subTest(status=blocker_status, blocker=blocker_id):
                asyncio.run(exercise(blocker_status, blocker_id))


if __name__ == "__main__":
    unittest.main()
