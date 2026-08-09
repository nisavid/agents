from pathlib import Path
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
import getpass
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
TESTS = ROOT / "tests"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from hindsight_memory_control_plane.operation_recovery import (  # noqa: E402
    OperationRecoveryError,
    create_claim_release_plan,
    create_cohort_manifest,
    create_exact_drain_plan,
    create_global_queue_blocker_classification,
    create_live_snapshot,
    create_requeue_plan,
    verify_global_queue_blocker_classification,
)
from hindsight_memory_control_plane.operation_recovery_runtime import (  # noqa: E402
    CLAIM_RELEASE_BLOCKER_KEYS,
    ExactDrainClaimAdapter,
    QUEUE_BLOCKER_GUARD_CONTRACT_DIGEST,
    QUEUE_BLOCKER_GUARD_CONTRACT_VERSION,
    apply_claim_release_transaction,
    apply_requeue_transaction,
    exact_drain_worker_id,
    live_row_digest,
    read_claim_release_evidence,
    read_claim_release_preimage,
    read_exact_drain_status,
    read_global_queue_blockers,
    read_safe_operation_rows,
    rollback_claim_release_transaction,
    rollback_requeue_transaction,
)
import test_hindsight_memory_operation_recovery as recovery_fixtures  # noqa: E402


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
        bank_id="engineering",
        operation_type="retain",
        worker_id=None,
        claimed_at=None,
        task_payload='{"memory": "synthetic disposable payload"}',
        result_metadata="{}",
        error_message=None,
    ):
        await connection.execute(
            """
            INSERT INTO public.async_operations (
                operation_id, bank_id, operation_type, status,
                created_at, updated_at, completed_at, retry_count,
                next_retry_at, worker_id, claimed_at, task_payload,
                result_metadata, error_message
            ) VALUES (
                $1::uuid, $2, $3, $4,
                '2026-07-29T12:00:00Z', $5::timestamptz,
                CASE WHEN $4 IN ('completed', 'failed', 'cancelled')
                     THEN $6::timestamptz ELSE NULL END,
                CASE WHEN $4 IN ('failed', 'cancelled') THEN 2 ELSE 0 END,
                NULL, $7, $8::timestamptz,
                $9::jsonb,
                $10::jsonb,
                COALESCE(
                    $11,
                    CASE WHEN $4 IN ('failed', 'cancelled')
                         THEN 'provider capacity exhausted' ELSE NULL END
                )
            )
            """,
            operation_id,
            bank_id,
            operation_type,
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
            task_payload,
            result_metadata,
            error_message,
        )

    @staticmethod
    async def _reset(connection):
        await connection.execute("DELETE FROM public.async_operations")
        await connection.execute(
            "UPDATE public.hindsight_migration_generation "
            "SET generation = 123 WHERE singleton"
        )

    async def _claim_release_case(self, connection):
        await self._reset(connection)
        operation_types = ["retain"] * 42 + ["refresh_mental_model"] * 4 + [
            "consolidation"
        ] * 2
        selected_positions = recovery_fixtures.PERMITTED_POSITIONS
        cohort_ids = []
        for position, operation_type in enumerate(operation_types):
            operation_id = f"00000000-0000-4000-8000-{position + 1:012d}"
            cohort_ids.append(operation_id)
            selected = position in selected_positions
            await self._insert_operation(
                connection,
                operation_id,
                status="failed" if selected else "pending",
                operation_type=operation_type,
                worker_id=(f"permitted-worker-{position}" if selected else None),
                claimed_at=CLAIMED_AT if selected else None,
                task_payload=json.dumps({"cohort": position}),
                result_metadata=json.dumps({"cohort-result": position}),
            )
        mutation_ids = []
        for position in range(43):
            operation_id = f"00000000-0000-4000-8000-{position + 1000:012x}"
            mutation_ids.append(operation_id)
            await self._insert_operation(
                connection,
                operation_id,
                status="failed",
                bank_id="codex" if position < 37 else "engineering",
                operation_type=(
                    "retain" if position < 37 else "refresh_mental_model"
                ),
                worker_id=f"orphaned-worker-{position}",
                claimed_at=CLAIMED_AT,
                task_payload=json.dumps({"memory": f"payload-secret-{position}"}),
                result_metadata=json.dumps(
                    {"result": f"result-secret-{position}"}
                ),
                error_message=f"error-secret-{position}",
            )
        await connection.execute(
            "UPDATE public.hindsight_migration_generation "
            "SET generation = 123 WHERE singleton"
        )
        live_rows = await read_safe_operation_rows(
            connection,
            schema="public",
            bank_id="engineering",
            operation_ids=cohort_ids,
        )
        baseline_rows = []
        for row in live_rows:
            baseline = deepcopy(row)
            baseline.update(
                {
                    "status": "pending",
                    "completed_at": None,
                    "retry_count": 0,
                    "next_retry_at": None,
                    "worker_id_present": False,
                    "worker_id_digest": None,
                    "claimed_at": None,
                    "error_category": "none",
                    "error_digest": None,
                }
            )
            baseline_rows.append(baseline)
        cohort = create_cohort_manifest(
            baseline_rows,
            profile_id="systalyze",
            schema="public",
            bank_id="engineering",
            generation="systalyze:public:90",
            backup=recovery_fixtures.backup_evidence(),
            created_at=int(time.time()) - 10_000,
        )
        snapshot = create_live_snapshot(
            cohort,
            live_rows,
            generation_before="systalyze:public:123",
            generation_after="systalyze:public:123",
            installation_authority=recovery_fixtures.installation_authority(),
            observed_at=int(time.time()) - 9000,
        )
        reference_plan = create_requeue_plan(
            cohort,
            snapshot,
            candidate_release=recovery_fixtures.release_identity(),
            rollback_backup=recovery_fixtures.rollback_backup_evidence(),
            rollback_encryption=recovery_fixtures.rollback_encryption(),
            rollback_backup_path="/private/tmp/reference-backup.age",
            rollback_bundle_path="/private/tmp/reference-bundle.age",
            authorization_receipt_path="/private/tmp/reference-authorization",
            application_receipt_path="/private/tmp/reference-application",
            verification_receipt_path="/private/tmp/reference-verification",
            rollback_receipt_path="/private/tmp/reference-rollback",
            created_at=int(time.time()) - 8000,
        )
        permitted_ids = [
            row["operation_id"] for row in reference_plan["selected_operations"]
        ]
        before_generation, after_generation, classifier_rows = (
            await read_global_queue_blockers(
                connection,
                profile_id="systalyze",
                schema="public",
                reference_cohort_operation_ids=cohort_ids,
                reference_selected_operation_ids=permitted_ids,
            )
        )
        self.assertEqual(len(classifier_rows), 43)
        predecessor = create_global_queue_blocker_classification(
            classifier_rows,
            classifier_candidate_release={
                "source_commit": "9" * 40,
                "version": "2026.08.01+9999999.operation-recovery.6",
                "release_digest": "8" * 64,
            },
            reference_plan=reference_plan,
            installation_authority=recovery_fixtures.installation_authority(),
            generation_before=before_generation,
            generation_after=after_generation,
            guard_contract_version=QUEUE_BLOCKER_GUARD_CONTRACT_VERSION,
            guard_contract_digest=QUEUE_BLOCKER_GUARD_CONTRACT_DIGEST,
            observed_at=int(time.time()) - 7200,
        )
        candidate = recovery_fixtures.release_identity()
        live = create_global_queue_blocker_classification(
            classifier_rows,
            classifier_candidate_release=candidate,
            reference_plan=reference_plan,
            installation_authority=recovery_fixtures.installation_authority(),
            generation_before=before_generation,
            generation_after=after_generation,
            guard_contract_version=QUEUE_BLOCKER_GUARD_CONTRACT_VERSION,
            guard_contract_digest=QUEUE_BLOCKER_GUARD_CONTRACT_DIGEST,
            observed_at=int(time.time()),
        )
        _, _, evidence = await read_claim_release_evidence(
            connection,
            profile_id="systalyze",
            schema="public",
            operation_ids=mutation_ids + permitted_ids,
            reference_cohort_operation_ids=[
                row["operation_id"]
                for row in reference_plan["cohort"]["operations"]
            ],
            reference_selected_operation_ids=permitted_ids,
            expected_generation="systalyze:public:123",
        )
        evidence_by_id = {row["operation_id"]: row for row in evidence}
        reference_by_id = {
            row["operation_id"]: row
            for row in reference_plan["selected_operations"]
        }
        permitted_rows = []
        for operation_id in permitted_ids:
            row = evidence_by_id[operation_id]
            body = {key: row[key] for key in CLAIM_RELEASE_BLOCKER_KEYS}
            permitted_rows.append(
                {
                    **body,
                    "row_digest": recovery_fixtures.digest(body),
                    "nonclaim_state_digest": row["nonclaim_state_digest"],
                    "reference_row_digest": reference_by_id[operation_id][
                        "row_digest"
                    ],
                }
            )
        permitted_rows.sort(key=lambda row: (row["created_at"], row["operation_id"]))
        plan = create_claim_release_plan(
            predecessor,
            live,
            reference_plan=reference_plan,
            permitted_blocker_rows=permitted_rows,
            nonclaim_state_digests={
                operation_id: evidence_by_id[operation_id][
                    "nonclaim_state_digest"
                ]
                for operation_id in mutation_ids
            },
            candidate_release=candidate,
            installation_authority=recovery_fixtures.installation_authority(),
            rollback_encryption=recovery_fixtures.rollback_encryption(),
            rollback_bundle_path="/private/tmp/claim-release.bundle",
            authorization_receipt_path="/private/tmp/claim-release.authorization",
            application_receipt_path="/private/tmp/claim-release.application",
            verification_receipt_path="/private/tmp/claim-release.verification",
            rollback_receipt_path="/private/tmp/claim-release.rollback",
            created_at=int(time.time()),
        )
        return plan, mutation_ids, permitted_ids, evidence

    async def _exact_drain_case(
        self,
        connection,
        *,
        embedded_operation_id_mismatch=False,
        embedded_bank_id_mismatch=False,
        embedded_type_mismatch=False,
        embedded_schema_mismatch=False,
        embedded_tenant_mismatch=False,
        embedded_api_key_mismatch=False,
    ):
        await self._reset(connection)
        operation_types = ["retain"] * 42 + ["refresh_mental_model"] * 4 + [
            "consolidation"
        ] * 2
        completed_positions = {0, 1, 42, 43, 46}
        cohort_ids = []
        for position, operation_type in enumerate(operation_types):
            operation_id = f"00000000-0000-4000-8000-{position + 1:012d}"
            cohort_ids.append(operation_id)
            await self._insert_operation(
                connection,
                operation_id,
                status=(
                    "completed" if position in completed_positions else "pending"
                ),
                operation_type=operation_type,
                task_payload=json.dumps(
                    {
                        "cohort": position,
                        "operation_id": (
                            "ffffffff-ffff-4fff-8fff-ffffffffffff"
                            if embedded_operation_id_mismatch and position == 2
                            else operation_id
                        ),
                        "bank_id": (
                            "codex"
                            if embedded_bank_id_mismatch and position == 2
                            else "engineering"
                        ),
                        "type": (
                            "graph_maintenance"
                            if embedded_type_mismatch and position == 2
                            else
                            "batch_retain"
                            if operation_type == "retain"
                            else operation_type
                        ),
                        **(
                            {"_schema": "foreign_schema"}
                            if embedded_schema_mismatch and position == 2
                            else {}
                        ),
                        **(
                            {"_tenant_id": "foreign-tenant"}
                            if embedded_tenant_mismatch and position == 2
                            else {}
                        ),
                        **(
                            {"_api_key_id": "foreign-api-key"}
                            if embedded_api_key_mismatch and position == 2
                            else {}
                        ),
                    }
                ),
                result_metadata=json.dumps({"cohort-result": position}),
            )
        unexpected_id = "ffffffff-ffff-4fff-8fff-fffffffffff0"
        await self._insert_operation(
            connection,
            unexpected_id,
            status="pending",
            task_payload='{"memory": "unexpected-44th"}',
        )
        await connection.execute(
            "UPDATE public.hindsight_migration_generation "
            "SET generation = 123 WHERE singleton"
        )
        live_rows = await read_safe_operation_rows(
            connection,
            schema="public",
            bank_id="engineering",
            operation_ids=cohort_ids,
        )
        baseline_rows = []
        for row in live_rows:
            baseline = deepcopy(row)
            baseline.update(
                {
                    "status": "pending",
                    "completed_at": None,
                    "retry_count": 0,
                    "next_retry_at": None,
                    "worker_id_present": False,
                    "worker_id_digest": None,
                    "claimed_at": None,
                    "error_category": "none",
                    "error_digest": None,
                }
            )
            baseline_rows.append(baseline)
        cohort = create_cohort_manifest(
            baseline_rows,
            profile_id="systalyze",
            schema="public",
            bank_id="engineering",
            generation="systalyze:public:90",
            backup=recovery_fixtures.backup_evidence(),
            created_at=int(time.time()) - 10_000,
        )
        snapshot = create_live_snapshot(
            cohort,
            live_rows,
            generation_before="systalyze:public:123",
            generation_after="systalyze:public:123",
            installation_authority=recovery_fixtures.installation_authority(),
            observed_at=int(time.time()) - 60,
        )
        rollback = recovery_fixtures.drain_backup_evidence()
        rollback["source_authority"]["generation_before"] = (
            "systalyze:public:123"
        )
        rollback["source_authority"]["generation_after"] = (
            "systalyze:public:123"
        )
        rollback["source_authority_digest"] = recovery_fixtures.digest(
            rollback["source_authority"]
        )
        plan = create_exact_drain_plan(
            cohort,
            snapshot,
            candidate_release=recovery_fixtures.release_identity(),
            rollback_backup=rollback,
            rollback_backup_path="/private/tmp/drain-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path="/private/tmp/drain-authorization.json",
            application_receipt_path="/private/tmp/drain-application.json",
            status_artifact_path="/private/tmp/drain-status.json",
            verification_receipt_path="/private/tmp/drain-verification.json",
            created_at=int(time.time()),
        )
        return plan, cohort_ids, unexpected_id

    @staticmethod
    async def _bind_disposable_exact_drain_identity(adapter, connection):
        identity = await connection.fetchrow(
            """
            SELECT current_database() AS database,
                   current_user AS database_user,
                   current_setting('data_directory') AS data_directory,
                   current_setting('port')::integer AS port,
                   (SELECT system_identifier::text
                    FROM pg_control_system()) AS system_identifier
            """
        )
        adapter._plan = deepcopy(adapter._plan)
        adapter._plan["installation_authority"][
            "postgres_system_identifier"
        ] = identity["system_identifier"]
        binding = adapter._plan["rollback_backup"]["source_authority"][
            "binding"
        ]
        binding.update(
            {
                "database": identity["database"],
                "user": identity["database_user"],
                "data_dir": identity["data_directory"],
                "port": identity["port"],
            }
        )

    def test_exact_drain_claims_only_bound_ids_and_ignores_derived_work(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, cohort_ids, unexpected_id = await self._exact_drain_case(
                    connection
                )
                completion_signals = 0

                def completed():
                    nonlocal completion_signals
                    completion_signals += 1

                adapter = ExactDrainClaimAdapter(
                    plan,
                    completion_callback=completed,
                )
                await self._bind_disposable_exact_drain_identity(
                    adapter,
                    connection,
                )
                worker_id = exact_drain_worker_id(plan["plan_digest"])
                claimed_ids = set()
                derived_id = "ffffffff-ffff-4fff-8fff-fffffffffff1"
                for attempt in range(30):
                    async with connection.transaction():
                        claimed = await adapter.claim_tasks(
                            connection,
                            '"public".async_operations',
                            worker_id,
                            {},
                            2,
                        )
                    claimed_ids.update(str(row["operation_id"]) for row in claimed)
                    if attempt == 0:
                        await self._insert_operation(
                            connection,
                            derived_id,
                            status="pending",
                            operation_type="consolidation",
                            task_payload='{"memory": "derived-after-start"}',
                        )
                    if len(claimed_ids) == 43:
                        break

                selected_ids = {
                    item["operation_id"] for item in plan["selected_operations"]
                }
                self.assertEqual(claimed_ids, selected_ids)
                self.assertEqual(len(claimed_ids), 43)
                rows = await connection.fetch(
                    """
                    SELECT operation_id::text AS operation_id,
                           status,
                           worker_id,
                           encode(
                               sha256(convert_to(task_payload::text, 'UTF8')),
                               'hex'
                           ) AS task_payload_digest
                    FROM public.async_operations
                    ORDER BY operation_id
                    """
                )
                rows_by_id = {row["operation_id"]: row for row in rows}
                for item in plan["selected_operations"]:
                    row = rows_by_id[item["operation_id"]]
                    self.assertEqual(row["status"], "processing")
                    self.assertEqual(row["worker_id"], worker_id)
                    self.assertEqual(
                        row["task_payload_digest"],
                        item["task_payload_digest"],
                    )
                for untouched_id in (unexpected_id, derived_id):
                    self.assertEqual(rows_by_id[untouched_id]["status"], "pending")
                    self.assertIsNone(rows_by_id[untouched_id]["worker_id"])
                for completed_id in set(cohort_ids) - selected_ids:
                    self.assertEqual(rows_by_id[completed_id]["status"], "completed")
                status = await read_exact_drain_status(
                    connection,
                    profile_id="systalyze",
                    schema="public",
                    plan=plan,
                )
                self.assertEqual(
                    status["selected_status_counts"],
                    {"processing": 43},
                )
                self.assertEqual(
                    status["preserved_status_counts"],
                    {"completed": 5},
                )
                self.assertEqual(
                    sum(
                        item["operation_count"]
                        for item in status["outside_nonterminal_counts"]
                    ),
                    2,
                )
                serialized = json.dumps(status, sort_keys=True)
                self.assertNotIn("unexpected-44th", serialized)
                self.assertNotIn("derived-after-start", serialized)
                self.assertNotIn('"task_payload":', serialized)
                await connection.execute(
                    """
                    UPDATE public.async_operations
                    SET status = 'completed',
                        completed_at = NOW(),
                        updated_at = NOW()
                    WHERE operation_id = ANY($1::uuid[])
                    """,
                    list(selected_ids),
                )
                async with connection.transaction():
                    self.assertEqual(
                        await adapter.claim_tasks(
                            connection,
                            '"public".async_operations',
                            worker_id,
                            {},
                            2,
                        ),
                        [],
                    )
                self.assertEqual(completion_signals, 1)
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_exact_drain_claim_rejects_mismatched_embedded_targets(self):
        async def exercise():
            connection = await self._connect()
            try:
                for mismatch in (
                    {"embedded_operation_id_mismatch": True},
                    {"embedded_bank_id_mismatch": True},
                    {"embedded_type_mismatch": True},
                    {"embedded_schema_mismatch": True},
                    {"embedded_tenant_mismatch": True},
                    {"embedded_api_key_mismatch": True},
                ):
                    with self.subTest(mismatch=mismatch):
                        plan, _cohort_ids, _unexpected_id = (
                            await self._exact_drain_case(
                                connection,
                                **mismatch,
                            )
                        )
                        adapter = ExactDrainClaimAdapter(plan)
                        await self._bind_disposable_exact_drain_identity(
                            adapter,
                            connection,
                        )
                        async with connection.transaction():
                            with self.assertRaisesRegex(
                                OperationRecoveryError,
                                "task payload target differs",
                            ):
                                await adapter.claim_tasks(
                                    connection,
                                    '"public".async_operations',
                                    exact_drain_worker_id(
                                        plan["plan_digest"]
                                    ),
                                    {},
                                    2,
                                )
                        status_counts = {
                            row["status"]: row["operation_count"]
                            for row in await connection.fetch(
                                "SELECT status, count(*)::bigint AS operation_count "
                                "FROM public.async_operations "
                                "WHERE operation_id = ANY($1::uuid[]) "
                                "GROUP BY status",
                                [
                                    item["operation_id"]
                                    for item in plan["selected_operations"]
                                ],
                            )
                        }
                        self.assertEqual(
                            status_counts,
                            {"pending": 43},
                        )
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_exact_drain_rejects_drift_on_an_unstarted_selected_row(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, _cohort_ids, _unexpected_id = (
                    await self._exact_drain_case(connection)
                )
                adapter = ExactDrainClaimAdapter(plan)
                await self._bind_disposable_exact_drain_identity(
                    adapter,
                    connection,
                )
                worker_id = exact_drain_worker_id(plan["plan_digest"])
                async with connection.transaction():
                    first = await adapter.claim_tasks(
                        connection,
                        '"public".async_operations',
                        worker_id,
                        {},
                        2,
                    )
                claimed = {str(row["operation_id"]) for row in first}
                unstarted = next(
                    item["operation_id"]
                    for item in plan["selected_operations"]
                    if item["operation_id"] not in claimed
                )
                await connection.execute(
                    "UPDATE public.async_operations "
                    "SET retry_count = retry_count + 1, updated_at = NOW() "
                    "WHERE operation_id = $1::uuid",
                    unstarted,
                )
                async with connection.transaction():
                    with self.assertRaisesRegex(
                        OperationRecoveryError,
                        "unstarted row drifted",
                    ):
                        await adapter.claim_tasks(
                            connection,
                            '"public".async_operations',
                            worker_id,
                            {},
                            2,
                        )
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_exact_drain_status_rejects_drift_on_a_preserved_row(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, cohort_ids, _unexpected_id = (
                    await self._exact_drain_case(connection)
                )
                selected = {
                    item["operation_id"]
                    for item in plan["selected_operations"]
                }
                preserved = next(
                    operation_id
                    for operation_id in cohort_ids
                    if operation_id not in selected
                )
                await connection.execute(
                    "UPDATE public.async_operations "
                    "SET retry_count = retry_count + 1, updated_at = NOW() "
                    "WHERE operation_id = $1::uuid",
                    preserved,
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "preserved row drifted",
                ):
                    await read_exact_drain_status(
                        connection,
                        profile_id="systalyze",
                        schema="public",
                        plan=plan,
                    )
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_exact_drain_status_rejects_terminal_selected_foreign_owner(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, _cohort_ids, _unexpected_id = (
                    await self._exact_drain_case(connection)
                )
                selected_id = plan["selected_operations"][0]["operation_id"]
                await connection.execute(
                    "UPDATE public.async_operations "
                    "SET status = 'completed', worker_id = 'foreign-worker', "
                    "claimed_at = NOW(), completed_at = NOW(), "
                    "updated_at = NOW() WHERE operation_id = $1::uuid",
                    selected_id,
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "selected row ownership drifted",
                ):
                    await read_exact_drain_status(
                        connection,
                        profile_id="systalyze",
                        schema="public",
                        plan=plan,
                    )
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_exact_drain_claim_rejects_drift_on_a_preserved_row(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, cohort_ids, _unexpected_id = (
                    await self._exact_drain_case(connection)
                )
                selected = {
                    item["operation_id"]
                    for item in plan["selected_operations"]
                }
                preserved = next(
                    operation_id
                    for operation_id in cohort_ids
                    if operation_id not in selected
                )
                adapter = ExactDrainClaimAdapter(plan)
                await self._bind_disposable_exact_drain_identity(
                    adapter,
                    connection,
                )
                worker_id = exact_drain_worker_id(plan["plan_digest"])
                async with connection.transaction():
                    await adapter.claim_tasks(
                        connection,
                        '"public".async_operations',
                        worker_id,
                        {},
                        2,
                    )
                await connection.execute(
                    "UPDATE public.async_operations "
                    "SET retry_count = retry_count + 1, updated_at = NOW() "
                    "WHERE operation_id = $1::uuid",
                    preserved,
                )
                async with connection.transaction():
                    with self.assertRaisesRegex(
                        OperationRecoveryError,
                        "preserved row drifted",
                    ):
                        await adapter.claim_tasks(
                            connection,
                            '"public".async_operations',
                            worker_id,
                            {},
                            2,
                        )
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_exact_drain_claim_locks_preserved_rows_until_selected_mutation(self):
        async def exercise():
            connection = await self._connect()
            concurrent = await self._connect()
            try:
                plan, cohort_ids, _unexpected_id = (
                    await self._exact_drain_case(connection)
                )
                selected = {
                    item["operation_id"]
                    for item in plan["selected_operations"]
                }
                preserved = next(
                    operation_id
                    for operation_id in cohort_ids
                    if operation_id not in selected
                )
                adapter = ExactDrainClaimAdapter(plan)
                await self._bind_disposable_exact_drain_identity(
                    adapter,
                    connection,
                )
                locked = asyncio.Event()
                release = asyncio.Event()

                class PausingConnection:
                    def __getattr__(self, name):
                        return getattr(connection, name)

                    async def fetch(self, query, *arguments):
                        rows = await connection.fetch(query, *arguments)
                        if "FOR SHARE" in query and not locked.is_set():
                            locked.set()
                            await release.wait()
                        return rows

                async with connection.transaction():
                    claim = asyncio.create_task(
                        adapter.claim_tasks(
                            PausingConnection(),
                            '"public".async_operations',
                            exact_drain_worker_id(plan["plan_digest"]),
                            {},
                            1,
                        )
                    )
                    await asyncio.wait_for(locked.wait(), timeout=2)
                    await concurrent.execute("SET lock_timeout = '100ms'")
                    with self.assertRaisesRegex(
                        Exception,
                        "lock timeout|canceling statement",
                    ):
                        await concurrent.execute(
                            "UPDATE public.async_operations "
                            "SET retry_count = retry_count + 1 "
                            "WHERE operation_id = $1::uuid",
                            preserved,
                        )
                    release.set()
                    self.assertEqual(len(await claim), 1)
            finally:
                await concurrent.close()
                await connection.close()

        asyncio.run(exercise())

    def test_exact_drain_retry_and_defer_remain_owned_and_bounded(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, _cohort_ids, _unexpected_id = (
                    await self._exact_drain_case(connection)
                )
                worker_id = exact_drain_worker_id(plan["plan_digest"])
                adapter = ExactDrainClaimAdapter(plan)
                await self._bind_disposable_exact_drain_identity(
                    adapter,
                    connection,
                )

                class Backend:
                    @asynccontextmanager
                    async def acquire(self):
                        yield connection

                async with connection.transaction():
                    claimed = await adapter.claim_tasks(
                        connection,
                        '"public".async_operations',
                        worker_id,
                        {"consolidation": 1},
                        0,
                    )
                self.assertEqual(len(claimed), 1)
                operation_id = str(claimed[0]["operation_id"])
                await adapter.schedule_retry(
                    Backend(),
                    operation_id,
                    datetime(2026, 1, 1),
                    "provider unavailable",
                    "public",
                )
                pending = await connection.fetchrow(
                    "SELECT status, worker_id, claimed_at, retry_count "
                    "FROM public.async_operations "
                    "WHERE operation_id = $1::uuid",
                    operation_id,
                )
                self.assertEqual(pending["status"], "pending")
                self.assertEqual(pending["worker_id"], worker_id)
                self.assertIsNotNone(pending["claimed_at"])
                self.assertEqual(pending["retry_count"], 1)

                resumed = ExactDrainClaimAdapter(plan, resume=True)
                await self._bind_disposable_exact_drain_identity(
                    resumed,
                    connection,
                )
                self.assertEqual(await resumed.recover_own_tasks(Backend()), 0)
                async with connection.transaction():
                    claimed_again = await resumed.claim_tasks(
                        connection,
                        '"public".async_operations',
                        worker_id,
                        {"consolidation": 1},
                        0,
                    )
                self.assertEqual(
                    [str(row["operation_id"]) for row in claimed_again],
                    [operation_id],
                )
                await resumed.defer_operation(
                    Backend(),
                    operation_id,
                    datetime(2026, 1, 1),
                    "dependency warming",
                    None,
                )
                await connection.execute(
                    "UPDATE public.async_operations "
                    "SET status = 'processing', retry_count = $2 "
                    "WHERE operation_id = $1::uuid",
                    operation_id,
                    plan["worker_max_retries"],
                )
                await resumed.schedule_retry(
                    Backend(),
                    operation_id,
                    datetime(2026, 1, 1),
                    "still unavailable",
                    None,
                )
                exhausted = await connection.fetchrow(
                    "SELECT status, worker_id, retry_count, completed_at "
                    "FROM public.async_operations "
                    "WHERE operation_id = $1::uuid",
                    operation_id,
                )
                self.assertEqual(exhausted["status"], "failed")
                self.assertEqual(exhausted["worker_id"], worker_id)
                self.assertEqual(
                    exhausted["retry_count"], plan["worker_max_retries"]
                )
                self.assertIsNotNone(exhausted["completed_at"])
            finally:
                await connection.close()

        asyncio.run(exercise())

    async def _exact_drain_terminal_transition_lock_case(self, *, failed):
        connection = await self._connect()
        concurrent = await self._connect()
        try:
            plan, cohort_ids, _unexpected_id = await self._exact_drain_case(
                connection
            )
            selected = {
                item["operation_id"] for item in plan["selected_operations"]
            }
            preserved = next(
                operation_id
                for operation_id in cohort_ids
                if operation_id not in selected
            )
            adapter = ExactDrainClaimAdapter(plan)
            await self._bind_disposable_exact_drain_identity(
                adapter,
                connection,
            )
            worker_id = exact_drain_worker_id(plan["plan_digest"])
            async with connection.transaction():
                claimed = await adapter.claim_tasks(
                    connection,
                    '"public".async_operations',
                    worker_id,
                    {},
                    1,
                )
            operation_id = str(claimed[0]["operation_id"])
            locked = asyncio.Event()
            release = asyncio.Event()

            class PausingConnection:
                def __getattr__(self, name):
                    return getattr(connection, name)

                async def fetch(self, query, *arguments):
                    rows = await connection.fetch(query, *arguments)
                    if "FOR SHARE" in query and not locked.is_set():
                        locked.set()
                        await release.wait()
                    return rows

            class Backend:
                @asynccontextmanager
                async def acquire(self):
                    yield PausingConnection()

            transition = (
                asyncio.create_task(
                    adapter.mark_failed(
                        Backend(),
                        operation_id,
                        "provider failure",
                        "public",
                    )
                )
                if failed
                else asyncio.create_task(
                    adapter.mark_completed(
                        Backend(),
                        operation_id,
                        "public",
                    )
                )
            )
            await asyncio.wait_for(locked.wait(), timeout=2)
            await concurrent.execute("SET lock_timeout = '100ms'")
            with self.assertRaisesRegex(
                Exception,
                "lock timeout|canceling statement",
            ):
                await concurrent.execute(
                    "UPDATE public.async_operations "
                    "SET retry_count = retry_count + 1 "
                    "WHERE operation_id = $1::uuid",
                    preserved,
                )
            release.set()
            await transition
            await adapter.mark_completed(
                Backend(),
                operation_id,
                "public",
            )
            terminal = await connection.fetchrow(
                "SELECT status, worker_id, completed_at "
                "FROM public.async_operations "
                "WHERE operation_id = $1::uuid",
                operation_id,
            )
            self.assertEqual(
                terminal["status"], "failed" if failed else "completed"
            )
            self.assertEqual(terminal["worker_id"], worker_id)
            self.assertIsNotNone(terminal["completed_at"])
        finally:
            await concurrent.close()
            await connection.close()

    def test_exact_drain_completion_locks_preserved_rows(self):
        asyncio.run(
            self._exact_drain_terminal_transition_lock_case(failed=False)
        )

    def test_exact_drain_failure_locks_preserved_rows(self):
        asyncio.run(
            self._exact_drain_terminal_transition_lock_case(failed=True)
        )

    def test_exact_drain_resume_recovers_only_its_owned_processing_rows(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, _cohort_ids, unexpected_id = (
                    await self._exact_drain_case(connection)
                )
                worker_id = exact_drain_worker_id(plan["plan_digest"])
                selected_id = plan["selected_operations"][0]["operation_id"]
                await connection.execute(
                    "UPDATE public.async_operations "
                    "SET status = 'processing', worker_id = $2, "
                    "claimed_at = NOW(), updated_at = NOW() "
                    "WHERE operation_id = $1::uuid",
                    selected_id,
                    worker_id,
                )
                adapter = ExactDrainClaimAdapter(plan, resume=True)
                await self._bind_disposable_exact_drain_identity(
                    adapter,
                    connection,
                )

                class Backend:
                    @asynccontextmanager
                    async def acquire(self):
                        yield connection

                self.assertEqual(await adapter.recover_own_tasks(Backend()), 1)
                recovered = await connection.fetchrow(
                    "SELECT status, worker_id, claimed_at, retry_count "
                    "FROM public.async_operations "
                    "WHERE operation_id = $1::uuid",
                    selected_id,
                )
                self.assertEqual(recovered["status"], "pending")
                self.assertEqual(recovered["worker_id"], worker_id)
                self.assertIsNotNone(recovered["claimed_at"])
                self.assertEqual(recovered["retry_count"], 1)
                outside = await connection.fetchrow(
                    "SELECT status, worker_id, retry_count "
                    "FROM public.async_operations "
                    "WHERE operation_id = $1::uuid",
                    unexpected_id,
                )
                self.assertEqual(outside["status"], "pending")
                self.assertIsNone(outside["worker_id"])
                self.assertEqual(outside["retry_count"], 0)
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_exact_drain_resume_rejects_a_terminal_row_owned_elsewhere(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, _cohort_ids, _unexpected_id = (
                    await self._exact_drain_case(connection)
                )
                selected_id = plan["selected_operations"][0]["operation_id"]
                await connection.execute(
                    "UPDATE public.async_operations "
                    "SET status = 'completed', worker_id = 'other-worker', "
                    "claimed_at = NOW(), completed_at = NOW(), "
                    "updated_at = NOW() WHERE operation_id = $1::uuid",
                    selected_id,
                )
                adapter = ExactDrainClaimAdapter(plan, resume=True)
                await self._bind_disposable_exact_drain_identity(
                    adapter,
                    connection,
                )

                class Backend:
                    @asynccontextmanager
                    async def acquire(self):
                        yield connection

                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "selected row drifted",
                ):
                    await adapter.recover_own_tasks(Backend())
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_exact_drain_resume_stops_at_the_bound_retry_ceiling(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, _cohort_ids, _unexpected_id = (
                    await self._exact_drain_case(connection)
                )
                worker_id = exact_drain_worker_id(plan["plan_digest"])
                selected_id = plan["selected_operations"][0]["operation_id"]
                await connection.execute(
                    "UPDATE public.async_operations "
                    "SET status = 'processing', worker_id = $2, "
                    "claimed_at = NOW(), retry_count = $3, updated_at = NOW() "
                    "WHERE operation_id = $1::uuid",
                    selected_id,
                    worker_id,
                    plan["worker_max_retries"],
                )
                adapter = ExactDrainClaimAdapter(plan, resume=True)
                await self._bind_disposable_exact_drain_identity(
                    adapter,
                    connection,
                )

                class Backend:
                    @asynccontextmanager
                    async def acquire(self):
                        yield connection

                self.assertEqual(await adapter.recover_own_tasks(Backend()), 1)
                state = await connection.fetchrow(
                    "SELECT status, worker_id, retry_count "
                    "FROM public.async_operations "
                    "WHERE operation_id = $1::uuid",
                    selected_id,
                )
                self.assertEqual(state["status"], "failed")
                self.assertEqual(state["worker_id"], worker_id)
                self.assertEqual(
                    state["retry_count"],
                    plan["worker_max_retries"],
                )
            finally:
                await connection.close()

        asyncio.run(exercise())

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

    def test_global_blocker_classification_matches_guard_without_payload_exposure(self):
        async def exercise():
            connection = await self._connect()
            try:
                await self._reset(connection)
                worker_sentinel = "worker-secret-sentinel-9d3e"
                payload_sentinel = "payload-secret-sentinel-41ba"
                error_sentinel = "error-secret-sentinel-c12f"
                result_sentinel = "result-secret-sentinel-e68a"
                outside_id = "00000000-0000-4000-8000-000000000099"
                reference_plan = (
                    recovery_fixtures.OperationRecoveryContractTest()
                    .requeue_plan()
                )
                self.assertEqual(
                    SELECTED_ID,
                    reference_plan["selected_operations"][0]["operation_id"],
                )
                selected_claimed_id = reference_plan["selected_operations"][1][
                    "operation_id"
                ]
                rows = (
                    (SELECTED_ID, "engineering", "retain", "processing", True),
                    (selected_claimed_id, "engineering", "retain", "failed", True),
                    (outside_id, "outside-bank", "outside-type", "pending", True),
                    ("00000000-0000-4000-8000-000000000098", "engineering", "retain", "completed", True),
                    ("00000000-0000-4000-8000-000000000097", "engineering", "retain", "pending", False),
                    ("00000000-0000-4000-8000-000000000096", "engineering", "retain", "failed", False),
                    ("00000000-0000-4000-8000-000000000095", "engineering", "retain", "cancelled", False),
                )
                for operation_id, bank_id, operation_type, status, claimed in rows:
                    await self._insert_operation(
                        connection,
                        operation_id,
                        status=status,
                        bank_id=bank_id,
                        operation_type=operation_type,
                        worker_id=worker_sentinel if claimed else None,
                        claimed_at=CLAIMED_AT if claimed else None,
                        task_payload=json.dumps({"memory": payload_sentinel}),
                        result_metadata=json.dumps({"secret": result_sentinel}),
                        error_message=error_sentinel,
                    )
                await connection.execute(
                    "UPDATE public.hindsight_migration_generation "
                    "SET generation = 123 WHERE singleton"
                )

                before, after, blockers = await read_global_queue_blockers(
                    connection,
                    profile_id="systalyze",
                    schema="public",
                    reference_cohort_operation_ids=[
                        item["operation_id"]
                        for item in reference_plan["cohort"]["operations"]
                    ],
                    reference_selected_operation_ids=[
                        item["operation_id"]
                        for item in reference_plan["selected_operations"]
                    ],
                )

                self.assertEqual((before, after), (
                    "systalyze:public:123",
                    "systalyze:public:123",
                ))
                self.assertEqual(
                    [row["operation_id"] for row in blockers],
                    [SELECTED_ID, outside_id],
                )
                self.assertNotIn(
                    selected_claimed_id,
                    [row["operation_id"] for row in blockers],
                )
                self.assertEqual(
                    [row["blocker_reason"] for row in blockers],
                    ["processing", "claimed_pending"],
                )
                self.assertTrue(blockers[0]["in_reference_selected_set"])
                self.assertFalse(blockers[1]["in_reference_cohort"])
                self.assertEqual(
                    blockers[1]["worker_id_digest"],
                    hashlib.sha256(worker_sentinel.encode()).hexdigest(),
                )
                serialized = json.dumps(blockers, sort_keys=True)
                self.assertNotIn(worker_sentinel, serialized)
                self.assertNotIn(payload_sentinel, serialized)
                self.assertNotIn(error_sentinel, serialized)
                self.assertNotIn(result_sentinel, serialized)
                self.assertNotIn("error_message", serialized)
                self.assertNotIn('"worker_id"', serialized)
                self.assertNotIn('"task_payload"', serialized)
                for blocker in blockers:
                    self.assertNotIn("result_metadata_digest", blocker)
                    self.assertNotIn("error_category", blocker)
                    self.assertNotIn("error_digest", blocker)

                classification = create_global_queue_blocker_classification(
                    blockers,
                    classifier_candidate_release={
                        "source_commit": "9" * 40,
                        "version": (
                            "2026.07.31+9999999.operation-recovery.6"
                        ),
                        "release_digest": "8" * 64,
                    },
                    reference_plan=reference_plan,
                    installation_authority=(
                        recovery_fixtures.installation_authority()
                    ),
                    generation_before=before,
                    generation_after=after,
                    guard_contract_version=(
                        QUEUE_BLOCKER_GUARD_CONTRACT_VERSION
                    ),
                    guard_contract_digest=(
                        QUEUE_BLOCKER_GUARD_CONTRACT_DIGEST
                    ),
                    observed_at=reference_plan["expires_at"] + 1,
                )
                self.assertEqual(
                    verify_global_queue_blocker_classification(
                        classification,
                        now=classification["observed_at"],
                    ),
                    classification,
                )
                self.assertEqual(classification["blocker_count"], 2)
                self.assertEqual(
                    classification["status_counts"],
                    {"pending": 1, "processing": 1},
                )
                self.assertEqual(
                    classification["bank_counts"],
                    {"engineering": 1, "outside-bank": 1},
                )
                self.assertEqual(
                    classification["operation_type_counts"],
                    {"outside-type": 1, "retain": 1},
                )
                serialized_artifact = json.dumps(
                    classification,
                    sort_keys=True,
                )
                self.assertNotIn(worker_sentinel, serialized_artifact)
                self.assertNotIn(payload_sentinel, serialized_artifact)
                self.assertNotIn(error_sentinel, serialized_artifact)
                self.assertNotIn(result_sentinel, serialized_artifact)
                self.assertNotIn("result_metadata_digest", serialized_artifact)
                self.assertNotIn("error_category", serialized_artifact)
                self.assertNotIn("error_digest", serialized_artifact)
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_global_blocker_classification_rejects_concurrent_queue_mutation(self):
        async def exercise():
            connection = await self._connect()
            mutator = await self._connect()
            try:
                await self._reset(connection)
                await self._insert_operation(
                    connection,
                    SELECTED_ID,
                    status="failed",
                )
                await connection.execute(
                    "UPDATE public.hindsight_migration_generation "
                    "SET generation = 123 WHERE singleton"
                )

                class InterleavingConnection:
                    def __init__(self, observed, writer):
                        self.observed = observed
                        self.writer = writer
                        self.mutated = False

                    def transaction(self, **arguments):
                        return self.observed.transaction(**arguments)

                    async def fetchrow(self, query, *arguments):
                        return await self.observed.fetchrow(query, *arguments)

                    async def fetch(self, query, *arguments):
                        rows = await self.observed.fetch(query, *arguments)
                        if not self.mutated:
                            self.mutated = True
                            await self._mutate_queue()
                        return rows

                    async def _mutate_queue(self):
                        await OperationRecoveryPostgresTest._insert_operation(
                            self.writer,
                            NONSELECTED_ID,
                            status="pending",
                            worker_id="concurrent-worker",
                            claimed_at=CLAIMED_AT,
                        )

                wrapped = InterleavingConnection(connection, mutator)
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "generation changed during queue blocker classification",
                ):
                    await read_global_queue_blockers(
                        wrapped,
                        profile_id="systalyze",
                        schema="public",
                        reference_cohort_operation_ids=[SELECTED_ID],
                        reference_selected_operation_ids=[SELECTED_ID],
                    )
                self.assertTrue(wrapped.mutated)
            finally:
                await mutator.close()
                await connection.close()

        asyncio.run(exercise())

    def test_claim_release_allows_exact_43_mutations_and_20_bound_blockers(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, mutation_ids, permitted_ids, before = (
                    await self._claim_release_case(connection)
                )
                mutation_attempts = 0

                def attempted():
                    nonlocal mutation_attempts
                    mutation_attempts += 1

                self.assertEqual(
                    await apply_claim_release_transaction(
                        connection,
                        profile_id="systalyze",
                        schema="public",
                        plan=plan,
                        on_mutation_attempt=attempted,
                    ),
                    ("systalyze:public:123", "systalyze:public:124"),
                )
                self.assertEqual(mutation_attempts, 1)
                _, _, after = await read_claim_release_evidence(
                    connection,
                    profile_id="systalyze",
                    schema="public",
                    operation_ids=mutation_ids + permitted_ids,
                    reference_cohort_operation_ids=plan[
                        "reference_cohort_operation_ids"
                    ],
                    reference_selected_operation_ids=permitted_ids,
                    expected_generation="systalyze:public:124",
                )
                before_by_id = {row["operation_id"]: row for row in before}
                after_by_id = {row["operation_id"]: row for row in after}
                self.assertEqual(set(after_by_id), set(mutation_ids + permitted_ids))
                for operation_id in mutation_ids:
                    self.assertIs(after_by_id[operation_id]["worker_id_present"], False)
                    self.assertIsNone(after_by_id[operation_id]["worker_id_digest"])
                    self.assertIsNone(after_by_id[operation_id]["claimed_at"])
                    for key in set(after_by_id[operation_id]) - {
                        "worker_id_present",
                        "worker_id_digest",
                        "claimed_at",
                    }:
                        self.assertEqual(
                            after_by_id[operation_id][key],
                            before_by_id[operation_id][key],
                        )
                for operation_id in permitted_ids:
                    self.assertEqual(
                        after_by_id[operation_id],
                        before_by_id[operation_id],
                    )
                serialized = json.dumps(after, sort_keys=True)
                self.assertNotIn("payload-secret-", serialized)
                self.assertNotIn("result-secret-", serialized)
                self.assertNotIn("error-secret-", serialized)
                self.assertNotIn("orphaned-worker-", serialized)
                self.assertNotIn("permitted-worker-", serialized)
                self.assertNotIn('"task_payload":', serialized)
                self.assertNotIn('"error_message":', serialized)
                self.assertNotIn('"worker_id":', serialized)
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_claim_release_rejects_a_row_outside_the_63_guarded_rows(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, mutation_ids, permitted_ids, before = (
                    await self._claim_release_case(connection)
                )
                unexpected_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
                await self._insert_operation(
                    connection,
                    unexpected_id,
                    status="failed",
                    worker_id="unexpected-worker",
                    claimed_at=CLAIMED_AT,
                )
                await connection.execute(
                    "UPDATE public.hindsight_migration_generation "
                    "SET generation = 123 WHERE singleton"
                )
                mutation_attempted = False

                def attempted():
                    nonlocal mutation_attempted
                    mutation_attempted = True

                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "claim-release queue guard differs",
                ):
                    await apply_claim_release_transaction(
                        connection,
                        profile_id="systalyze",
                        schema="public",
                        plan=plan,
                        on_mutation_attempt=attempted,
                    )
                self.assertIs(mutation_attempted, False)
                _, _, after = await read_claim_release_evidence(
                    connection,
                    profile_id="systalyze",
                    schema="public",
                    operation_ids=mutation_ids + permitted_ids + [unexpected_id],
                    reference_cohort_operation_ids=plan[
                        "reference_cohort_operation_ids"
                    ],
                    reference_selected_operation_ids=permitted_ids,
                    expected_generation="systalyze:public:123",
                )
                after_by_id = {row["operation_id"]: row for row in after}
                self.assertEqual(
                    {row["operation_id"]: row for row in before},
                    {
                        operation_id: after_by_id[operation_id]
                        for operation_id in mutation_ids + permitted_ids
                    },
                )
                self.assertIs(
                    after_by_id[unexpected_id]["worker_id_present"],
                    True,
                )
            finally:
                await connection.close()

        asyncio.run(exercise())

    def test_claim_release_clears_only_claims_and_rollback_restores_them(self):
        async def exercise():
            connection = await self._connect()
            try:
                plan, mutation_ids, permitted_ids, before = (
                    await self._claim_release_case(connection)
                )
                preimage = await read_claim_release_preimage(
                    connection,
                    schema="public",
                    plan=plan,
                )
                self.assertEqual(
                    await apply_claim_release_transaction(
                        connection,
                        profile_id="systalyze",
                        schema="public",
                        plan=plan,
                    ),
                    ("systalyze:public:123", "systalyze:public:124"),
                )
                application = {"post_generation": "systalyze:public:124"}
                rollback_record = {
                    "pre_generation": "systalyze:public:124",
                    "post_generation": "systalyze:public:125",
                }
                self.assertEqual(
                    await rollback_claim_release_transaction(
                        connection,
                        profile_id="systalyze",
                        schema="public",
                        plan=plan,
                        application=application,
                        rollback_record=rollback_record,
                        preimage=preimage,
                    ),
                    ("systalyze:public:124", "systalyze:public:125"),
                )
                generation_before_retry = await connection.fetchval(
                    "SELECT generation FROM "
                    "public.hindsight_migration_generation WHERE singleton"
                )
                self.assertEqual(
                    await rollback_claim_release_transaction(
                        connection,
                        profile_id="systalyze",
                        schema="public",
                        plan=plan,
                        application=application,
                        rollback_record=rollback_record,
                        preimage=preimage,
                    ),
                    ("systalyze:public:124", "systalyze:public:125"),
                )
                self.assertEqual(
                    await connection.fetchval(
                        "SELECT generation FROM "
                        "public.hindsight_migration_generation WHERE singleton"
                    ),
                    generation_before_retry,
                )
                _, _, restored = await read_claim_release_evidence(
                    connection,
                    profile_id="systalyze",
                    schema="public",
                    operation_ids=mutation_ids + permitted_ids,
                    reference_cohort_operation_ids=plan[
                        "reference_cohort_operation_ids"
                    ],
                    reference_selected_operation_ids=permitted_ids,
                    expected_generation="systalyze:public:125",
                )
                self.assertEqual(
                    {row["operation_id"]: row for row in restored},
                    {row["operation_id"]: row for row in before},
                )
            finally:
                await connection.close()

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
