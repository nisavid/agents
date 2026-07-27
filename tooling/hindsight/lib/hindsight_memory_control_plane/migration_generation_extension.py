"""Authenticated, database-backed migration generation for Hindsight."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from hindsight_api.extensions.tenant import AuthenticationError
from pydantic import BaseModel, ConfigDict, Field

from hindsight_api.extensions import HttpExtension
from hindsight_api.models import RequestContext

from .accidental_replay import ReplayError, _document_descriptor


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
BANK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
GENERATION_TABLE = "hindsight_migration_generation"
TRIGGER_FUNCTION = "hindsight_bump_migration_generation"
TRIGGER_NAME = "hindsight_migration_generation_bump"
MAX_SNAPSHOT_ITEMS = 10_000
PLANNING_STATE_TABLES = (
    "async_operations",
    "banks",
    "directives",
    "documents",
    "entities",
    "invalidated_memory_units",
    "memory_units",
    "mental_models",
    "webhooks",
)


class ControllerState(BaseModel):
    """Digest-only controller state bound into the migration generation."""

    model_config = ConfigDict(extra="forbid")

    configuration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    hook_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    schedule_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReplayCloseoutDocument(BaseModel):
    """Digest-only frozen source document authority."""

    model_config = ConfigDict(extra="forbid")

    source_document_id: str = Field(min_length=1, max_length=4096)
    record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReplayCloseoutAuthority(BaseModel):
    """Generation- and manifest-bound authority for deleting literal codex."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1, le=1)
    expected_generation: str = Field(min_length=1, max_length=256)
    expected_bank_ids: list[str] = Field(min_length=2, max_length=10_000)
    source_documents: list[ReplayCloseoutDocument] = Field(
        min_length=1,
        max_length=MAX_SNAPSHOT_ITEMS,
    )
    replay_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    backup_evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    closeout_plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def _quoted_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise RuntimeError(f"{label} is invalid")
    return f'"{value}"'


def _bearer_token(authorization: str | None) -> str:
    if (
        not isinstance(authorization, str)
        or not authorization.lower().startswith("bearer ")
    ):
        raise HTTPException(status_code=401, detail="authentication required")
    token = authorization[7:].strip()
    if not token or any(character in token for character in "\r\n\0"):
        raise HTTPException(status_code=401, detail="authentication required")
    return token


class MigrationGenerationHttpExtension(HttpExtension):
    """Expose the controller's opaque migration generation at the API root."""

    def __init__(self, config: dict[str, str]):
        super().__init__(config)
        profile_id = config.get("profile_id")
        if (
            not isinstance(profile_id, str)
            or PROFILE_ID.fullmatch(profile_id) is None
        ):
            raise RuntimeError("migration-generation profile ID is invalid")
        self.profile_id = profile_id
        self._memory: Any | None = None

    def get_router(self, memory: Any) -> APIRouter:
        self._memory = memory
        return APIRouter()

    def get_root_router(self, memory: Any) -> APIRouter:
        self._memory = memory
        router = APIRouter()

        @router.get("/v1/migration/generation")
        async def migration_generation(
            authorization: str | None = Header(default=None),
        ) -> dict[str, str]:
            schema = await self._authenticated_schema(authorization)
            generation = await self._read_generation(schema)
            return {"generation": self._opaque_generation(schema, generation)}

        @router.post("/v1/migration/controller-state")
        async def migration_controller_state(
            state: ControllerState,
            authorization: str | None = Header(default=None),
        ) -> dict[str, str | bool]:
            schema = await self._authenticated_schema(authorization)
            generation, changed = await self._record_controller_state(
                schema,
                state,
            )
            return {
                "generation": self._opaque_generation(schema, generation),
                "changed": changed,
            }

        @router.get("/v1/migration/snapshot")
        async def migration_snapshot(
            source_bank: str = Query(min_length=1, max_length=256),
            candidate_bank: str = Query(min_length=1, max_length=256),
            authorization: str | None = Header(default=None),
        ) -> dict[str, Any]:
            if (
                BANK_ID.fullmatch(source_bank) is None
                or BANK_ID.fullmatch(candidate_bank) is None
                or source_bank == candidate_bank
            ):
                raise HTTPException(
                    status_code=422,
                    detail="migration bank selection is invalid",
                )
            schema = await self._authenticated_schema(authorization)
            return await self._read_snapshot(
                schema,
                source_bank,
                candidate_bank,
            )

        @router.post("/v1/migration/replay-closeout")
        async def migration_replay_closeout(
            authority: ReplayCloseoutAuthority,
            authorization: str | None = Header(default=None),
        ) -> dict[str, Any]:
            schema = await self._authenticated_schema(authorization)
            return await self._conditional_replay_closeout(
                schema,
                authority,
            )

        return router

    async def on_startup(self) -> None:
        memory = self._required_memory()
        tenant_extension = getattr(memory, "tenant_extension", None)
        if tenant_extension is None:
            schemas = ("public",)
        else:
            tenants = await tenant_extension.list_tenants()
            schemas = tuple(
                sorted({self._schema_name(tenant.schema) for tenant in tenants})
            )
        if not schemas:
            raise RuntimeError("migration-generation tenant set is empty")
        for schema in schemas:
            await self._install_schema(schema)

    def _required_memory(self) -> Any:
        if self._memory is None:
            raise RuntimeError("migration-generation memory engine is unavailable")
        return self._memory

    @staticmethod
    def _schema_name(value: str) -> str:
        _quoted_identifier(value, "tenant schema")
        return value

    async def _authenticated_schema(
        self,
        authorization: str | None,
    ) -> str:
        token = _bearer_token(authorization)
        memory = self._required_memory()
        tenant_extension = getattr(memory, "tenant_extension", None)
        if tenant_extension is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            tenant = await tenant_extension.authenticate(
                RequestContext(api_key=token)
            )
        except AuthenticationError:
            raise HTTPException(
                status_code=401,
                detail="authentication failed",
            ) from None
        return self._schema_name(tenant.schema_name)

    def _opaque_generation(self, schema: str, generation: int) -> str:
        if type(generation) is not int or generation < 1:
            raise RuntimeError("migration generation is invalid")
        return f"{self.profile_id}:{schema}:{generation}"

    async def _connection(self):
        pool = await self._required_memory()._get_pool()
        return pool.acquire()

    async def _install_schema(self, schema: str) -> None:
        quoted_schema = _quoted_identifier(schema, "tenant schema")
        quoted_table = _quoted_identifier(GENERATION_TABLE, "generation table")
        quoted_function = _quoted_identifier(
            TRIGGER_FUNCTION,
            "generation trigger function",
        )
        connection_context = await self._connection()
        async with connection_context as connection:
            async with connection.transaction():
                await connection.execute(
                    f"""
                CREATE TABLE IF NOT EXISTS {quoted_schema}.{quoted_table} (
                    singleton boolean PRIMARY KEY DEFAULT true
                        CHECK (singleton),
                    generation bigint NOT NULL CHECK (generation >= 1),
                    controller_state jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    replay_closeout_receipts jsonb NOT NULL
                        DEFAULT '{{}}'::jsonb,
                    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
                )
                    """
                )
                await connection.execute(
                    f"""
                ALTER TABLE {quoted_schema}.{quoted_table}
                ADD COLUMN IF NOT EXISTS replay_closeout_receipts jsonb
                    NOT NULL DEFAULT '{{}}'::jsonb
                    """
                )
                await connection.execute(
                    f"""
                INSERT INTO {quoted_schema}.{quoted_table}
                    (singleton, generation)
                VALUES (true, 1)
                ON CONFLICT (singleton) DO NOTHING
                    """
                )
                await connection.execute(
                    f"""
                CREATE OR REPLACE FUNCTION
                    {quoted_schema}.{quoted_function}()
                RETURNS trigger
                LANGUAGE plpgsql
                SECURITY INVOKER
                SET search_path = pg_catalog
                AS $$
                BEGIN
                    UPDATE {quoted_schema}.{quoted_table}
                    SET generation = generation + 1,
                        updated_at = clock_timestamp()
                    WHERE singleton;
                    RETURN NULL;
                END
                $$
                    """
                )
                tables = await connection.fetch(
                    """
                SELECT c.relname AS table_name
                FROM pg_catalog.pg_class AS c
                JOIN pg_catalog.pg_namespace AS n
                  ON n.oid = c.relnamespace
                WHERE n.nspname = $1
                  AND c.relkind IN ('r', 'p')
                  AND c.relname <> $2
                  AND c.relname <> 'alembic_version'
                  AND c.relname = ANY($3::text[])
                ORDER BY c.relname
                """,
                    schema,
                    GENERATION_TABLE,
                    list(PLANNING_STATE_TABLES),
                )
            # Commit generation-table and function locks before acquiring an
            # AccessExclusiveLock for any trigger DDL. A concurrent worker
            # write holds its planning table before the trigger updates the
            # generation row; holding those locks in the opposite order here
            # would deadlock API startup.
            for entry in tables:
                table_name = _quoted_identifier(
                    entry["table_name"],
                    "planning-state table",
                )
                trigger_name = _quoted_identifier(
                    TRIGGER_NAME,
                    "generation trigger",
                )
                async with connection.transaction():
                    await connection.execute(
                        f"""
                    DROP TRIGGER IF EXISTS {trigger_name}
                    ON {quoted_schema}.{table_name}
                        """
                    )
                    await connection.execute(
                        f"""
                    CREATE TRIGGER {trigger_name}
                    AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE
                    ON {quoted_schema}.{table_name}
                    FOR EACH STATEMENT
                    EXECUTE FUNCTION {quoted_schema}.{quoted_function}()
                        """
                    )

    async def _read_generation(self, schema: str) -> int:
        connection_context = await self._connection()
        async with connection_context as connection:
            return await self._read_generation_on_connection(
                connection,
                schema,
            )

    async def _read_generation_on_connection(
        self,
        connection: Any,
        schema: str,
    ) -> int:
        quoted_schema = _quoted_identifier(schema, "tenant schema")
        quoted_table = _quoted_identifier(GENERATION_TABLE, "generation table")
        row = await connection.fetchrow(
            f"""
                SELECT generation,
                       controller_state,
                       (
                           SELECT count(*)
                           FROM pg_catalog.pg_class AS c
                           JOIN pg_catalog.pg_namespace AS n
                             ON n.oid = c.relnamespace
                           WHERE n.nspname = $1
                             AND c.relkind IN ('r', 'p')
                             AND c.relname <> $2
                             AND c.relname <> 'alembic_version'
                             AND c.relname = ANY($4::text[])
                             AND NOT EXISTS (
                                 SELECT 1
                                 FROM pg_catalog.pg_trigger AS t
                                 WHERE t.tgrelid = c.oid
                                   AND t.tgname = $3
                                   AND t.tgenabled <> 'D'
                             )
                       ) AS missing_trigger_count
                FROM {quoted_schema}.{quoted_table}
                WHERE singleton
                """,
                schema,
                GENERATION_TABLE,
                TRIGGER_NAME,
                list(PLANNING_STATE_TABLES),
        )
        if row is None:
            raise RuntimeError("migration generation is unavailable")
        generation = row["generation"]
        missing_trigger_count = row["missing_trigger_count"]
        if (
            type(generation) is not int
            or generation < 1
            or type(missing_trigger_count) is not int
            or missing_trigger_count < 0
        ):
            raise RuntimeError("migration generation is invalid")
        if missing_trigger_count:
            raise RuntimeError(
                "migration generation trigger coverage is incomplete"
            )
        return generation

    @staticmethod
    def _mapping(value: Any, label: str) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise RuntimeError(f"{label} is invalid") from None
        if not isinstance(value, dict):
            raise RuntimeError(f"{label} is invalid")
        return value

    @staticmethod
    def _list(value: Any, label: str) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise RuntimeError(f"{label} is invalid") from None
        if not isinstance(value, (list, tuple)):
            raise RuntimeError(f"{label} is invalid")
        return list(value)

    async def _snapshot_bank(
        self,
        connection: Any,
        schema: str,
        bank_id: str,
    ) -> dict[str, Any]:
        quoted_schema = _quoted_identifier(schema, "tenant schema")
        bank = await connection.fetchrow(
            f"""
            SELECT bank_id, config
            FROM {quoted_schema}.banks
            WHERE bank_id = $1
            """,
            bank_id,
        )
        if bank is None:
            raise RuntimeError(f"migration bank {bank_id!r} is unavailable")
        config = self._mapping(bank["config"], "bank configuration")

        stats = await connection.fetchrow(
            f"""
            SELECT
                $1::text AS bank_id,
                (SELECT count(*) FROM {quoted_schema}.documents
                 WHERE bank_id = $1)::bigint AS total_documents,
                (SELECT count(*) FROM {quoted_schema}.memory_units
                 WHERE bank_id = $1)::bigint AS total_memories,
                (SELECT count(*) FROM {quoted_schema}.memory_units
                 WHERE bank_id = $1
                   AND fact_type = 'observation')::bigint
                    AS total_observations,
                (SELECT count(*) FROM {quoted_schema}.entities
                 WHERE bank_id = $1)::bigint AS total_entities,
                (SELECT count(*) FROM {quoted_schema}.mental_models
                 WHERE bank_id = $1)::bigint AS total_mental_models,
                (SELECT count(*) FROM {quoted_schema}.directives
                 WHERE bank_id = $1)::bigint AS total_directives,
                (SELECT count(*) FROM {quoted_schema}.invalidated_memory_units
                 WHERE bank_id = $1)::bigint AS total_invalidated_memories
            """,
            bank_id,
        )
        if stats is None:
            raise RuntimeError("migration bank statistics are unavailable")

        scope_rows = await connection.fetch(
            f"""
            SELECT DISTINCT scope
            FROM {quoted_schema}.memory_units AS memory,
                 LATERAL unnest(memory.tags) AS scope
            WHERE memory.bank_id = $1
              AND memory.fact_type = 'observation'
            ORDER BY scope
            LIMIT {MAX_SNAPSHOT_ITEMS + 1}
            """,
            bank_id,
        )
        tag_rows = await connection.fetch(
            f"""
            SELECT DISTINCT tag
            FROM (
                SELECT unnest(tags) AS tag
                FROM {quoted_schema}.documents WHERE bank_id = $1
                UNION
                SELECT unnest(tags) AS tag
                FROM {quoted_schema}.memory_units WHERE bank_id = $1
                UNION
                SELECT unnest(tags) AS tag
                FROM {quoted_schema}.mental_models WHERE bank_id = $1
                UNION
                SELECT unnest(tags) AS tag
                FROM {quoted_schema}.directives WHERE bank_id = $1
            ) AS bank_tags
            WHERE tag IS NOT NULL
            ORDER BY tag
            LIMIT {MAX_SNAPSHOT_ITEMS + 1}
            """,
            bank_id,
        )
        documents = await connection.fetch(
            f"""
            SELECT document.id,
                   document.bank_id,
                   document.content_hash,
                   document.created_at,
                   document.updated_at,
                   char_length(document.original_text) AS text_length,
                   document.tags,
                   encode(
                       sha256(
                           convert_to(
                               COALESCE(
                                   document.retain_params->'metadata',
                                   '{{}}'::jsonb
                               )::text,
                               'UTF8'
                           )
                       ),
                       'hex'
                   ) AS document_metadata_digest,
                   encode(
                       sha256(
                           convert_to(
                               COALESCE(
                                   document.retain_params,
                                   '{{}}'::jsonb
                               )::text,
                               'UTF8'
                           )
                       ),
                       'hex'
                   ) AS retain_params_digest,
                   (
                       SELECT count(*)
                       FROM {quoted_schema}.memory_units AS memory
                       WHERE memory.bank_id = document.bank_id
                         AND memory.document_id = document.id
                   )::bigint AS memory_unit_count,
                   (
                       SELECT count(*)
                       FROM {quoted_schema}.memory_units AS memory
                       WHERE memory.bank_id = document.bank_id
                         AND memory.document_id = document.id
                         AND memory.embedding IS NOT NULL
                   )::bigint AS embedded_memory_unit_count
            FROM {quoted_schema}.documents AS document
            WHERE document.bank_id = $1
            ORDER BY document.created_at, document.id
            LIMIT {MAX_SNAPSHOT_ITEMS + 1}
            """,
            bank_id,
        )
        models = await connection.fetch(
            f"""
            SELECT model.id::text AS model_id,
                   encode(
                       sha256(
                           convert_to(
                               (
                                   to_jsonb(model) - 'embedding'
                               )::text,
                               'UTF8'
                           )
                       ),
                       'hex'
                   ) AS content_digest,
                   model.trigger
            FROM {quoted_schema}.mental_models AS model
            WHERE model.bank_id = $1
            ORDER BY model.id
            LIMIT {MAX_SNAPSHOT_ITEMS + 1}
            """,
            bank_id,
        )
        directives = await connection.fetch(
            f"""
            SELECT directive.id::text AS directive_id,
                   encode(
                       sha256(
                           convert_to(
                               to_jsonb(directive)::text,
                               'UTF8'
                           )
                       ),
                       'hex'
                   ) AS content_digest
            FROM {quoted_schema}.directives AS directive
            WHERE directive.bank_id = $1
            ORDER BY directive.id
            LIMIT {MAX_SNAPSHOT_ITEMS + 1}
            """,
            bank_id,
        )
        invalidations = await connection.fetch(
            f"""
            SELECT id::text AS item_id,
                   document_id,
                   encode(
                       sha256(convert_to(text, 'UTF8')),
                       'hex'
                   ) AS content_digest,
                   encode(
                       sha256(
                           convert_to(
                               COALESCE(invalidation_reason, ''),
                               'UTF8'
                           )
                       ),
                       'hex'
                   ) AS reason_digest
            FROM {quoted_schema}.invalidated_memory_units
            WHERE bank_id = $1
            ORDER BY id
            LIMIT {MAX_SNAPSHOT_ITEMS + 1}
            """,
            bank_id,
        )
        webhooks = await connection.fetch(
            f"""
            SELECT id::text AS id,
                   event_types,
                   enabled,
                   encode(
                       sha256(convert_to(url, 'UTF8')),
                       'hex'
                   ) AS target_digest,
                   encode(
                       sha256(
                           convert_to(
                               COALESCE(http_config, '{{}}'::jsonb)::text,
                               'UTF8'
                           )
                       ),
                       'hex'
                   ) AS config_digest
            FROM {quoted_schema}.webhooks
            WHERE bank_id = $1
            ORDER BY id
            LIMIT {MAX_SNAPSHOT_ITEMS + 1}
            """,
            bank_id,
        )
        operations = await connection.fetch(
            f"""
            SELECT operation_id::text AS id,
                   status,
                   updated_at
            FROM {quoted_schema}.async_operations
            WHERE bank_id = $1
              AND status IN ('pending', 'processing')
            ORDER BY operation_id
            LIMIT {MAX_SNAPSHOT_ITEMS + 1}
            """,
            bank_id,
        )

        def records(rows: Any, label: str) -> list[dict[str, Any]]:
            if len(rows) > MAX_SNAPSHOT_ITEMS:
                raise RuntimeError(
                    f"migration {label} exceeds snapshot item limit"
                )
            return [dict(row) for row in rows]

        return {
            "config": {
                "bank_id": bank_id,
                "config": config,
                "overrides": {},
            },
            "stats": dict(stats),
            "scopes": {
                "scopes": [
                    row["scope"]
                    for row in records(scope_rows, "scope inventory")
                ]
            },
            "tags": [
                row["tag"] for row in records(tag_rows, "tag inventory")
            ],
            "documents": records(documents, "document inventory"),
            "models": records(models, "model inventory"),
            "directives": records(directives, "directive inventory"),
            "invalidated_memories": records(
                invalidations,
                "invalidated-memory inventory",
            ),
            "webhooks": records(webhooks, "webhook inventory"),
            "operations": records(operations, "operation inventory"),
        }

    async def _read_snapshot(
        self,
        schema: str,
        source_bank: str,
        candidate_bank: str,
    ) -> dict[str, Any]:
        quoted_schema = _quoted_identifier(schema, "tenant schema")
        quoted_table = _quoted_identifier(GENERATION_TABLE, "generation table")
        connection_context = await self._connection()
        async with connection_context as connection:
            async with connection.transaction(
                isolation="repeatable_read",
                readonly=True,
            ):
                before = await self._read_generation_on_connection(
                    connection,
                    schema,
                )
                row = await connection.fetchrow(
                    f"""
                    SELECT controller_state
                    FROM {quoted_schema}.{quoted_table}
                    WHERE singleton
                    """
                )
                if row is None:
                    raise RuntimeError("migration controller state is unavailable")
                controller_state = self._mapping(
                    row["controller_state"],
                    "migration controller state",
                )
                expected_state_keys = {
                    "configuration_digest",
                    "hook_digest",
                    "schedule_digest",
                }
                if (
                    set(controller_state) != expected_state_keys
                    or any(
                        not isinstance(controller_state[key], str)
                        or re.fullmatch(
                            r"[0-9a-f]{64}",
                            controller_state[key],
                        )
                        is None
                        for key in expected_state_keys
                    )
                ):
                    raise RuntimeError(
                        "migration controller state is incomplete"
                    )
                banks = {
                    "source": await self._snapshot_bank(
                        connection,
                        schema,
                        source_bank,
                    ),
                    "candidate": await self._snapshot_bank(
                        connection,
                        schema,
                        candidate_bank,
                    ),
                }
                # Repeatable-read provides the snapshot boundary. Equal reads
                # additionally attest that the generation row itself was
                # readable and stable throughout the complete collection pass.
                after = await self._read_generation_on_connection(
                    connection,
                    schema,
                )
                if before != after:
                    raise RuntimeError("migration generation changed during snapshot")
        opaque = self._opaque_generation(schema, before)
        return {
            "schema_version": 1,
            "generation_before": opaque,
            "generation_after": opaque,
            "controller_state": controller_state,
            "banks": banks,
        }

    async def _record_controller_state(
        self,
        schema: str,
        state: ControllerState,
    ) -> tuple[int, bool]:
        quoted_schema = _quoted_identifier(schema, "tenant schema")
        quoted_table = _quoted_identifier(GENERATION_TABLE, "generation table")
        state_json = json.dumps(
            state.model_dump(),
            sort_keys=True,
            separators=(",", ":"),
        )
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await connection.fetchrow(
                f"""
                WITH updated AS (
                    UPDATE {quoted_schema}.{quoted_table}
                    SET controller_state = $1::jsonb,
                        generation = generation + 1,
                        updated_at = clock_timestamp()
                    WHERE singleton
                      AND controller_state IS DISTINCT FROM $1::jsonb
                    RETURNING generation, true AS changed
                )
                SELECT generation, changed
                FROM updated
                UNION ALL
                SELECT generation, false AS changed
                FROM {quoted_schema}.{quoted_table}
                WHERE singleton
                  AND NOT EXISTS (SELECT 1 FROM updated)
                LIMIT 1
                """,
                state_json,
            )
        if row is None:
            raise RuntimeError("migration generation is unavailable")
        generation = row["generation"]
        changed = row["changed"]
        if type(generation) is not int or generation < 1 or type(changed) is not bool:
            raise RuntimeError("migration generation response is invalid")
        return generation, changed

    async def _conditional_replay_closeout(
        self,
        schema: str,
        authority: ReplayCloseoutAuthority,
    ) -> dict[str, Any]:
        """Delete literal codex under the same lock as generation changes."""

        quoted_schema = _quoted_identifier(schema, "tenant schema")
        quoted_table = _quoted_identifier(GENERATION_TABLE, "generation table")
        expected_banks = authority.expected_bank_ids
        if (
            expected_banks != sorted(expected_banks)
            or len(expected_banks) != len(set(expected_banks))
            or "codex" not in expected_banks
            or "engineering" not in expected_banks
            or any(BANK_ID.fullmatch(bank_id) is None for bank_id in expected_banks)
        ):
            raise HTTPException(
                status_code=409,
                detail="replay closeout bank authority is invalid",
            )
        expected_documents = {
            item.source_document_id: item.record_digest
            for item in authority.source_documents
        }
        if (
            len(expected_documents) != len(authority.source_documents)
            or any(
                not document_id
                or len(document_id.encode("utf-8")) > 4096
                or any(character in document_id for character in "\r\n\0")
                for document_id in expected_documents
            )
        ):
            raise HTTPException(
                status_code=409,
                detail="replay closeout source authority is invalid",
            )

        memory = self._required_memory()
        backend = await memory._get_backend()
        internal_id: str | None = None
        deleted_count = 0
        pre_generation = 0
        post_generation = 0
        remaining_banks: list[str] = []
        async with backend.acquire() as connection:
            async with connection.transaction(isolation="serializable"):
                generation_row = await connection.fetchrow(
                    f"""
                    SELECT generation, replay_closeout_receipts
                    FROM {quoted_schema}.{quoted_table}
                    WHERE singleton
                    FOR UPDATE
                    """
                )
                if generation_row is None:
                    raise RuntimeError("migration generation is unavailable")
                stored_receipts = self._mapping(
                    generation_row["replay_closeout_receipts"],
                    "replay closeout receipts",
                )
                stored = stored_receipts.get(
                    authority.closeout_plan_digest
                )
                authority_value = authority.model_dump()
                if stored is not None:
                    if (
                        not isinstance(stored, dict)
                        or stored.get("authority") != authority_value
                        or not isinstance(stored.get("result"), dict)
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "replay closeout digest was reused "
                                "with different authority"
                            ),
                        )
                    return stored["result"]
                pre_generation = generation_row["generation"]
                if (
                    type(pre_generation) is not int
                    or self._opaque_generation(schema, pre_generation)
                    != authority.expected_generation
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="replay closeout generation drifted",
                    )
                bank_rows = await connection.fetch(
                    f"""
                    SELECT bank_id
                    FROM {quoted_schema}.banks
                    ORDER BY bank_id
                    """
                )
                observed_banks = [row["bank_id"] for row in bank_rows]
                if observed_banks != expected_banks:
                    raise HTTPException(
                        status_code=409,
                        detail="replay closeout bank set drifted",
                    )
                source_rows = await connection.fetch(
                    f"""
                    SELECT id,
                           bank_id,
                           original_text,
                           content_hash,
                           created_at,
                           updated_at,
                           tags,
                           retain_params
                    FROM {quoted_schema}.documents
                    WHERE bank_id = 'codex'
                    ORDER BY created_at, id
                    """
                )
                observed_documents: dict[str, str] = {}
                try:
                    for row in source_rows:
                        retain_params = self._mapping(
                            row["retain_params"],
                            "replay source retain parameters",
                        )
                        document = {
                            "id": row["id"],
                            "bank_id": row["bank_id"],
                            "original_text": row["original_text"],
                            "content_hash": row["content_hash"],
                            "created_at": row["created_at"].isoformat(),
                            "updated_at": row["updated_at"].isoformat(),
                            "tags": self._list(
                                row["tags"],
                                "replay source tags",
                            ),
                            "document_metadata":
                                retain_params.get("metadata") or {},
                            "retain_params": retain_params,
                            "observation_scopes":
                                retain_params.get("observation_scopes"),
                        }
                        descriptor = _document_descriptor(
                            document,
                            source_bank_id="codex",
                        )
                        observed_documents[
                            descriptor["source_document_id"]
                        ] = descriptor["record_digest"]
                except (ReplayError, AttributeError, TypeError, ValueError):
                    raise HTTPException(
                        status_code=409,
                        detail="replay closeout source manifest is invalid",
                    ) from None
                if observed_documents != expected_documents:
                    raise HTTPException(
                        status_code=409,
                        detail="replay closeout source manifest drifted",
                    )
                counts = await connection.fetchrow(
                    f"""
                    SELECT
                        (SELECT count(*) FROM {quoted_schema}.memory_units
                         WHERE bank_id = 'codex')::bigint AS memory_units,
                        (SELECT count(*) FROM {quoted_schema}.entities
                         WHERE bank_id = 'codex')::bigint AS entities,
                        (SELECT count(*) FROM {quoted_schema}.documents
                         WHERE bank_id = 'codex')::bigint AS documents
                    """
                )
                if counts is None or any(
                    type(counts[key]) is not int or counts[key] < 0
                    for key in ("memory_units", "entities", "documents")
                ):
                    raise RuntimeError("replay closeout counts are unavailable")
                await connection.execute(
                    f"DELETE FROM {quoted_schema}.documents "
                    "WHERE bank_id = 'codex'"
                )
                await connection.execute(
                    f"DELETE FROM {quoted_schema}.memory_units "
                    "WHERE bank_id = 'codex'"
                )
                await connection.execute(
                    f"DELETE FROM {quoted_schema}.invalidated_memory_units "
                    "WHERE bank_id = 'codex'"
                )
                await connection.execute(
                    f"DELETE FROM {quoted_schema}.entities "
                    "WHERE bank_id = 'codex'"
                )
                internal_id_value = await connection.fetchval(
                    f"DELETE FROM {quoted_schema}.banks "
                    "WHERE bank_id = 'codex' RETURNING internal_id"
                )
                if internal_id_value is None:
                    raise HTTPException(
                        status_code=409,
                        detail="replay closeout source bank is unavailable",
                    )
                internal_id = str(internal_id_value)
                deleted_count = sum(
                    counts[key]
                    for key in ("memory_units", "entities", "documents")
                )
                post_generation_row = await connection.fetchrow(
                    f"""
                    SELECT generation
                    FROM {quoted_schema}.{quoted_table}
                    WHERE singleton
                    """
                )
                if post_generation_row is None:
                    raise RuntimeError("migration generation is unavailable")
                post_generation = post_generation_row["generation"]
                remaining_rows = await connection.fetch(
                    f"""
                    SELECT bank_id
                    FROM {quoted_schema}.banks
                    ORDER BY bank_id
                    """
                )
                remaining_banks = [
                    row["bank_id"] for row in remaining_rows
                ]
                if (
                    type(post_generation) is not int
                    or post_generation <= pre_generation
                    or remaining_banks
                    != [bank for bank in expected_banks if bank != "codex"]
                ):
                    raise RuntimeError(
                        "replay closeout deletion attestation failed"
                    )
                result = {
                    "schema_version": 1,
                    "status": "deleted",
                    "deleted_bank_id": "codex",
                    "deleted_count": deleted_count,
                    "pre_delete_generation":
                        self._opaque_generation(schema, pre_generation),
                    "post_delete_generation":
                        self._opaque_generation(schema, post_generation),
                    "remaining_bank_ids": remaining_banks,
                    # Index/cache cleanup is deliberately outside the
                    # deletion receipt authority and transaction.
                    "cleanup_status": "deferred",
                    "replay_plan_digest": authority.replay_plan_digest,
                    "verification_digest": authority.verification_digest,
                    "backup_evidence_digest":
                        authority.backup_evidence_digest,
                    "closeout_plan_digest":
                        authority.closeout_plan_digest,
                }
                receipt_value = {
                    "authority": authority_value,
                    "result": result,
                }
                await connection.execute(
                    f"""
                    UPDATE {quoted_schema}.{quoted_table}
                    SET replay_closeout_receipts =
                        replay_closeout_receipts
                        || jsonb_build_object($1, $2::jsonb),
                        updated_at = clock_timestamp()
                    WHERE singleton
                    """,
                    authority.closeout_plan_digest,
                    json.dumps(
                        receipt_value,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            try:
                from hindsight_api.engine.retain import bank_utils

                await bank_utils.drop_bank_vector_indexes(
                    connection,
                    internal_id,
                    ops=backend.ops,
                )
            except Exception:
                logging.exception(
                    "replay closeout vector-index cleanup failed"
                )
        try:
            await memory._bank_stats_cache.invalidate(schema, "codex")
        except Exception:
            logging.exception("replay closeout bank-cache cleanup failed")
        return result
