"""Closed-schema, digest-bound authorization for data-identity re-adoption."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
import hashlib
import hmac
import os
from pathlib import Path
import stat
import time
from typing import Any

from .canonical import canonical_bytes, digest, strict_json_loads


class DataIdentityRebindError(RuntimeError):
    """The data-identity re-adoption contract was not satisfied."""


SHA256_LENGTH = 64
MAX_LIFETIME_SECONDS = 3600
REQUIRED_BANK_IDS = frozenset({"codex", "engineering"})
AGE_HEADER = b"age-encryption.org/v1\n"

EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "profile_id",
        "collected_at",
        "expires_at",
        "postgres",
        "database",
        "backup",
        "restore",
        "safety",
    }
)
POSTGRES_KEYS = frozenset(
    {
        "system_identifier",
        "data_root",
        "data_root_device",
        "data_root_inode",
        "postgres_data_root",
        "postgres_data_device",
        "postgres_data_inode",
        "postmaster_pid",
        "postmaster_start_time",
        "connection_identity_digest",
    }
)
DATABASE_KEYS = frozenset(
    {
        "observed_at",
        "generation_before",
        "generation_after",
        "bank_ids",
        "bank_set_digest",
        "codex_document_count",
        "codex_manifest_digest",
        "pending_operation_count",
        "generic_import_receipt_count",
        "schema_digest",
        "snapshot_digest",
    }
)
BACKUP_KEYS = frozenset(
    {
        "artifact_root",
        "artifact_root_device",
        "artifact_root_inode",
        "artifact_path",
        "artifact_sha256",
        "ciphertext_size",
        "full_schema",
        "encryption_format",
        "encryption_recipient_digest",
        "ciphertext_header_digest",
        "plaintext_disposed",
        "created_at",
    }
)
RESTORE_KEYS = frozenset(
    {
        "artifact_sha256",
        "schema_digest",
        "bank_set_digest",
        "codex_document_count",
        "codex_manifest_digest",
        "restore_identity_digest",
        "decryption_recipient_digest",
        "dropped",
        "restored_at",
    }
)
SAFETY_KEYS = frozenset(
    {
        "hooks_disabled",
        "controller_authority_disabled",
        "no_serena_import_authority",
        "target_bank_inspected",
        "database_mutation_performed",
    }
)
PLAN_KEYS = frozenset(
    {
        "schema_version",
        "action",
        "consumer_id",
        "profile_id",
        "installation_state_digest",
        "expected_post_state_digest",
        "old_data_identity_digest",
        "new_data_identity_digest",
        "current_release_digest",
        "binding_generation_digest",
        "evidence_digest",
        "database_continuity_digest",
        "postgres_system_identifier",
        "backup_artifact_digest",
        "backup",
        "rollback_bundle_path",
        "authorization_receipt_path",
        "application_receipt_path",
        "verification_receipt_path",
        "created_at",
        "expires_at",
        "plan_digest",
    }
)


def _normalized(value: object) -> Any:
    try:
        return strict_json_loads(canonical_bytes(value))
    except (RecursionError, TypeError, ValueError) as error:
        raise DataIdentityRebindError(
            "data-identity value is not canonical JSON"
        ) from error


def _closed(
    value: object,
    keys: Set[str],
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise DataIdentityRebindError(f"{label} is invalid")
    return value


def _text(value: object, label: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(
            ord(character) <= 0x1F or 0x7F <= ord(character) <= 0x9F
            for character in value
        )
    ):
        raise DataIdentityRebindError(f"{label} is invalid")
    return value


def _decimal_identifier(value: object, label: str) -> str:
    text = _text(value, label, maximum=127)
    if not text.isascii() or not text.isdecimal():
        raise DataIdentityRebindError(f"{label} is invalid")
    return text


def _sha(value: object, label: str) -> str:
    text = _text(value, label, maximum=SHA256_LENGTH)
    if len(text) != SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise DataIdentityRebindError(f"{label} is invalid")
    return text


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise DataIdentityRebindError(f"{label} is invalid")
    return value


def _literal_bool(value: object, label: str, *, expected: bool) -> None:
    if type(value) is not bool or value is not expected:
        raise DataIdentityRebindError(f"{label} is invalid")


def _absolute_path(value: object, label: str) -> Path:
    text = _text(value, label)
    path = Path(text)
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DataIdentityRebindError(f"{label} is invalid")
    return path


def _confined_parts(path: Path, trusted_root: Path) -> tuple[str, ...]:
    try:
        relative = path.relative_to(trusted_root)
    except ValueError:
        raise DataIdentityRebindError("backup artifact is unavailable") from None
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise DataIdentityRebindError("backup artifact is unavailable")
    return relative.parts


def _open_confined(
    path: Path,
    *,
    trusted_root: Path,
    trusted_device: int,
    trusted_inode: int,
) -> int:
    if (
        not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NONBLOCK")
    ):
        raise DataIdentityRebindError("backup artifact is unavailable")
    parts = _confined_parts(path, trusted_root)
    descriptor = -1
    try:
        descriptor = os.open(
            trusted_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        root_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_dev != trusted_device
            or root_metadata.st_ino != trusted_inode
        ):
            raise DataIdentityRebindError("backup artifact is unavailable")
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            if final:
                flags |= os.O_NONBLOCK
            else:
                flags |= os.O_DIRECTORY
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            metadata = os.fstat(next_descriptor)
            expected_type = stat.S_ISREG if final else stat.S_ISDIR
            if metadata.st_dev != trusted_device or not expected_type(metadata.st_mode):
                os.close(next_descriptor)
                raise DataIdentityRebindError("backup artifact is unavailable")
            os.close(descriptor)
            descriptor = next_descriptor
        result = descriptor
        descriptor = -1
        return result
    except OSError as error:
        raise DataIdentityRebindError("backup artifact is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _artifact_sha256(
    path: Path,
    declared_size: int,
    *,
    trusted_root: Path,
    trusted_device: int,
    trusted_inode: int,
) -> tuple[str, int, str]:
    hasher = hashlib.sha256()
    size = 0
    prefix = b""
    descriptor = -1
    try:
        descriptor = _open_confined(
            path,
            trusted_root=trusted_root,
            trusted_device=trusted_device,
            trusted_inode=trusted_inode,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != trusted_device
            or metadata.st_size != declared_size
        ):
            raise DataIdentityRebindError("backup artifact is unavailable")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > declared_size:
                raise DataIdentityRebindError("backup artifact is unavailable")
            hasher.update(chunk)
            if len(prefix) < len(AGE_HEADER):
                prefix += chunk[: len(AGE_HEADER) - len(prefix)]
    except OSError as error:
        raise DataIdentityRebindError("backup artifact is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if prefix != AGE_HEADER:
        raise DataIdentityRebindError("backup artifact is not age ciphertext")
    return hasher.hexdigest(), size, hashlib.sha256(prefix).hexdigest()


def _verify_postgres_evidence(value: object) -> Mapping[str, Any]:
    postgres = _closed(value, POSTGRES_KEYS, "PostgreSQL evidence")
    _decimal_identifier(
        postgres.get("system_identifier"), "PostgreSQL system identifier"
    )
    data_root = _absolute_path(postgres.get("data_root"), "data root")
    postgres_root = _absolute_path(
        postgres.get("postgres_data_root"), "PostgreSQL data root"
    )
    if data_root not in postgres_root.parents:
        raise DataIdentityRebindError("PostgreSQL data root is not bound to data root")
    for field in (
        "data_root_device",
        "data_root_inode",
        "postgres_data_device",
        "postgres_data_inode",
        "postmaster_pid",
        "postmaster_start_time",
    ):
        _integer(postgres.get(field), field, minimum=1)
    connection_identity_digest = _sha(
        postgres.get("connection_identity_digest"), "connection identity digest"
    )
    expected_connection_identity = digest(
        {
            field: postgres[field]
            for field in (
                "system_identifier",
                "data_root",
                "data_root_device",
                "data_root_inode",
                "postgres_data_root",
                "postgres_data_device",
                "postgres_data_inode",
                "postmaster_pid",
                "postmaster_start_time",
            )
        }
    )
    if connection_identity_digest != expected_connection_identity:
        raise DataIdentityRebindError("connection identity digest differs")
    return postgres


def _verify_database_evidence(value: object) -> Mapping[str, Any]:
    database = _closed(value, DATABASE_KEYS, "database evidence")
    _integer(database.get("observed_at"), "database observation time")
    before = _text(database.get("generation_before"), "pre-snapshot generation")
    after = _text(database.get("generation_after"), "post-snapshot generation")
    if not hmac.compare_digest(before.encode(), after.encode()):
        raise DataIdentityRebindError(
            "database generation changed during evidence read"
        )
    bank_ids = database.get("bank_ids")
    if (
        not isinstance(bank_ids, Sequence)
        or isinstance(bank_ids, (str, bytes))
        or any(not isinstance(bank, str) or not bank for bank in bank_ids)
        or list(bank_ids) != sorted(set(bank_ids))
        or not REQUIRED_BANK_IDS.issubset(bank_ids)
    ):
        raise DataIdentityRebindError("database bank inventory is invalid")
    bank_set_digest = _sha(database.get("bank_set_digest"), "bank-set digest")
    if bank_set_digest != digest({"bank_ids": list(bank_ids)}):
        raise DataIdentityRebindError("database bank-set digest differs")
    _integer(database.get("codex_document_count"), "codex document count", minimum=1)
    _sha(database.get("codex_manifest_digest"), "codex manifest digest")
    if _integer(database.get("pending_operation_count"), "pending operation count"):
        raise DataIdentityRebindError("database has pending operations")
    if _integer(
        database.get("generic_import_receipt_count"),
        "generic-import receipt count",
    ):
        raise DataIdentityRebindError("generic import authority is already present")
    _sha(database.get("schema_digest"), "database schema digest")
    _sha(database.get("snapshot_digest"), "database snapshot digest")
    return database


def _verify_backup_evidence(
    value: object,
    *,
    collected_at: int,
    current_time: int,
    verify_artifact: bool,
) -> Mapping[str, Any]:
    backup = _closed(value, BACKUP_KEYS, "backup evidence")
    artifact_root = _absolute_path(backup.get("artifact_root"), "backup artifact root")
    artifact_root_device = _integer(
        backup.get("artifact_root_device"),
        "backup artifact root device",
        minimum=1,
    )
    artifact_root_inode = _integer(
        backup.get("artifact_root_inode"),
        "backup artifact root inode",
        minimum=1,
    )
    artifact_path = _absolute_path(backup.get("artifact_path"), "backup artifact path")
    _confined_parts(artifact_path, artifact_root)
    artifact_digest = _sha(backup.get("artifact_sha256"), "backup artifact digest")
    artifact_size = _integer(
        backup.get("ciphertext_size"), "backup artifact size", minimum=1
    )
    _literal_bool(
        backup.get("full_schema"),
        "full-schema backup marker",
        expected=True,
    )
    if backup.get("encryption_format") != "age":
        raise DataIdentityRebindError("backup encryption format is invalid")
    _sha(
        backup.get("encryption_recipient_digest"),
        "backup encryption recipient digest",
    )
    expected_header_digest = hashlib.sha256(AGE_HEADER).hexdigest()
    if (
        _sha(
            backup.get("ciphertext_header_digest"),
            "backup ciphertext header digest",
        )
        != expected_header_digest
    ):
        raise DataIdentityRebindError("backup ciphertext header differs")
    _literal_bool(
        backup.get("plaintext_disposed"),
        "backup plaintext disposal marker",
        expected=True,
    )
    backup_created_at = _integer(backup.get("created_at"), "backup creation time")
    if not collected_at <= backup_created_at <= current_time:
        raise DataIdentityRebindError("backup creation time is outside evidence window")
    if verify_artifact:
        observed_digest, observed_size, observed_header_digest = _artifact_sha256(
            artifact_path,
            artifact_size,
            trusted_root=artifact_root,
            trusted_device=artifact_root_device,
            trusted_inode=artifact_root_inode,
        )
        if (
            observed_digest != artifact_digest
            or observed_size != artifact_size
            or observed_header_digest != expected_header_digest
        ):
            raise DataIdentityRebindError("backup artifact digest differs")
    return backup


def verify_rebind_backup_artifact(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Re-open and hash the confined encrypted backup named by a plan."""
    backup = _normalized(value)
    created_at = _integer(backup.get("created_at"), "backup creation time")
    return _verify_backup_evidence(
        backup,
        collected_at=created_at,
        current_time=created_at,
        verify_artifact=True,
    )


def _verify_restore_evidence(
    value: object,
    *,
    postgres: Mapping[str, Any],
    database: Mapping[str, Any],
    backup: Mapping[str, Any],
    current_time: int,
) -> Mapping[str, Any]:
    restore = _closed(value, RESTORE_KEYS, "restore evidence")
    if (
        _sha(restore.get("artifact_sha256"), "restored artifact digest")
        != backup["artifact_sha256"]
        or _sha(restore.get("schema_digest"), "restored schema digest")
        != database["schema_digest"]
        or _sha(restore.get("bank_set_digest"), "restored bank-set digest")
        != database["bank_set_digest"]
        or _integer(
            restore.get("codex_document_count"),
            "restored codex document count",
            minimum=1,
        )
        != database["codex_document_count"]
        or _sha(
            restore.get("codex_manifest_digest"),
            "restored codex manifest digest",
        )
        != database["codex_manifest_digest"]
        or _sha(
            restore.get("decryption_recipient_digest"),
            "restore decryption recipient digest",
        )
        != backup["encryption_recipient_digest"]
    ):
        raise DataIdentityRebindError("disposable restore differs from live snapshot")
    restore_identity_digest = _sha(
        restore.get("restore_identity_digest"), "restore identity digest"
    )
    if hmac.compare_digest(
        restore_identity_digest.encode("ascii"),
        postgres["connection_identity_digest"].encode("ascii"),
    ):
        raise DataIdentityRebindError(
            "disposable restore identity matches live PostgreSQL identity"
        )
    _literal_bool(
        restore.get("dropped"),
        "disposable restore drop marker",
        expected=True,
    )
    restored_at = _integer(restore.get("restored_at"), "restore verification time")
    if not backup["created_at"] <= restored_at <= current_time:
        raise DataIdentityRebindError("restore verification is outside evidence window")
    return restore


def _verify_safety_evidence(value: object) -> Mapping[str, Any]:
    safety = _closed(value, SAFETY_KEYS, "safety evidence")
    _literal_bool(safety.get("hooks_disabled"), "hook disablement", expected=True)
    _literal_bool(
        safety.get("controller_authority_disabled"),
        "controller authority disablement",
        expected=True,
    )
    _literal_bool(
        safety.get("no_serena_import_authority"),
        "Serena import authority absence",
        expected=True,
    )
    _literal_bool(
        safety.get("target_bank_inspected"),
        "target-bank inspection marker",
        expected=False,
    )
    _literal_bool(
        safety.get("database_mutation_performed"),
        "database mutation marker",
        expected=False,
    )
    return safety


def verify_rebind_evidence(
    value: Mapping[str, Any],
    *,
    now: int | None = None,
    verify_artifact: bool = True,
) -> Mapping[str, Any]:
    """Validate payload-free live, backup, restore, and safety evidence."""
    evidence = _normalized(value)
    _closed(evidence, EVIDENCE_KEYS, "data-identity evidence")
    if (
        type(evidence.get("schema_version")) is not int
        or evidence.get("schema_version") != 1
    ):
        raise DataIdentityRebindError("data-identity evidence is invalid")
    _text(evidence.get("profile_id"), "profile ID", maximum=127)
    collected_at = _integer(evidence.get("collected_at"), "collection time")
    expires_at = _integer(evidence.get("expires_at"), "evidence expiry")
    current_time = int(time.time()) if now is None else _integer(now, "current time")
    if (
        expires_at <= collected_at
        or expires_at - collected_at > MAX_LIFETIME_SECONDS
        or current_time < collected_at
        or current_time >= expires_at
    ):
        raise DataIdentityRebindError("data-identity evidence is expired")

    postgres = _verify_postgres_evidence(evidence.get("postgres"))
    database = _verify_database_evidence(evidence.get("database"))
    if not collected_at <= database["observed_at"] <= current_time:
        raise DataIdentityRebindError(
            "database observation time is outside evidence window"
        )
    backup = _verify_backup_evidence(
        evidence.get("backup"),
        collected_at=collected_at,
        current_time=current_time,
        verify_artifact=verify_artifact,
    )
    _verify_restore_evidence(
        evidence.get("restore"),
        postgres=postgres,
        database=database,
        backup=backup,
        current_time=current_time,
    )
    _verify_safety_evidence(evidence.get("safety"))

    expected_snapshot = digest(
        {
            "postgres": postgres,
            "generation": database["generation_before"],
            "observed_at": database["observed_at"],
            "bank_set_digest": database["bank_set_digest"],
            "codex_document_count": database["codex_document_count"],
            "codex_manifest_digest": database["codex_manifest_digest"],
            "schema_digest": database["schema_digest"],
        }
    )
    if database["snapshot_digest"] != expected_snapshot:
        raise DataIdentityRebindError("database snapshot digest differs")
    return evidence


def create_rebind_plan(
    *,
    consumer_id: str,
    profile_id: str,
    installation_state_digest: str,
    expected_post_state_digest: str,
    old_data_identity_digest: str,
    new_data_identity_digest: str,
    current_release_digest: str,
    binding_generation_digest: str,
    evidence: Mapping[str, Any],
    rollback_bundle_path: str,
    authorization_receipt_path: str,
    application_receipt_path: str,
    verification_receipt_path: str,
    now: int | None = None,
) -> Mapping[str, Any]:
    current_time = int(time.time()) if now is None else _integer(now, "current time")
    checked = verify_rebind_evidence(evidence, now=current_time)
    if checked["profile_id"] != profile_id:
        raise DataIdentityRebindError("evidence profile differs")
    old_digest = _sha(old_data_identity_digest, "old data identity digest")
    new_digest = _sha(new_data_identity_digest, "new data identity digest")
    if hmac.compare_digest(old_digest.encode(), new_digest.encode()):
        raise DataIdentityRebindError("data identity has not changed")
    body = {
        "schema_version": 1,
        "action": "rebind-data-identity",
        "consumer_id": _text(consumer_id, "consumer ID", maximum=127),
        "profile_id": _text(profile_id, "profile ID", maximum=127),
        "installation_state_digest": _sha(
            installation_state_digest, "installation state digest"
        ),
        "expected_post_state_digest": _sha(
            expected_post_state_digest, "expected post-state digest"
        ),
        "old_data_identity_digest": old_digest,
        "new_data_identity_digest": new_digest,
        "current_release_digest": _sha(
            current_release_digest, "current release digest"
        ),
        "binding_generation_digest": _sha(
            binding_generation_digest, "binding generation digest"
        ),
        "evidence_digest": digest(checked),
        "database_continuity_digest": digest(
            {
                field: checked["database"][field]
                for field in (
                    "generation_before",
                    "bank_set_digest",
                    "codex_document_count",
                    "codex_manifest_digest",
                    "schema_digest",
                )
            }
        ),
        "postgres_system_identifier": checked["postgres"]["system_identifier"],
        "backup_artifact_digest": checked["backup"]["artifact_sha256"],
        "backup": checked["backup"],
        "rollback_bundle_path": str(
            _absolute_path(rollback_bundle_path, "rollback bundle path")
        ),
        "authorization_receipt_path": str(
            _absolute_path(authorization_receipt_path, "authorization receipt path")
        ),
        "application_receipt_path": str(
            _absolute_path(application_receipt_path, "application receipt path")
        ),
        "verification_receipt_path": str(
            _absolute_path(verification_receipt_path, "verification receipt path")
        ),
        "created_at": current_time,
        "expires_at": checked["expires_at"],
    }
    return _normalized({**body, "plan_digest": digest(body)})


def verify_rebind_plan(
    value: Mapping[str, Any],
    *,
    now: int | None = None,
    allow_expired: bool = False,
) -> Mapping[str, Any]:
    """Validate a plan, optionally retaining expired recovery evidence.

    ``allow_expired`` is reserved for non-mutating status and exact-state
    rollback. Those recovery operations must remain available indefinitely;
    mutating apply never enables it.
    """
    plan = _normalized(value)
    _closed(plan, PLAN_KEYS, "data-identity rebind plan")
    if (
        type(plan.get("schema_version")) is not int
        or plan.get("schema_version") != 1
        or plan.get("action") != "rebind-data-identity"
    ):
        raise DataIdentityRebindError("data-identity rebind plan is invalid")
    for field in (
        "installation_state_digest",
        "expected_post_state_digest",
        "old_data_identity_digest",
        "new_data_identity_digest",
        "current_release_digest",
        "binding_generation_digest",
        "evidence_digest",
        "database_continuity_digest",
        "backup_artifact_digest",
        "plan_digest",
    ):
        _sha(plan.get(field), field.replace("_", " "))
    for field in ("consumer_id", "profile_id"):
        _text(plan.get(field), field.replace("_", " "), maximum=127)
    _decimal_identifier(
        plan.get("postgres_system_identifier"), "PostgreSQL system identifier"
    )
    if hmac.compare_digest(
        plan["old_data_identity_digest"].encode("ascii"),
        plan["new_data_identity_digest"].encode("ascii"),
    ):
        raise DataIdentityRebindError("data identity has not changed")
    for field in (
        "rollback_bundle_path",
        "authorization_receipt_path",
        "application_receipt_path",
        "verification_receipt_path",
    ):
        _absolute_path(plan.get(field), field.replace("_", " "))
    created_at = _integer(plan.get("created_at"), "plan creation time")
    expires_at = _integer(plan.get("expires_at"), "plan expiry")
    backup = _verify_backup_evidence(
        plan.get("backup"),
        collected_at=0,
        current_time=created_at,
        verify_artifact=False,
    )
    if backup["artifact_sha256"] != plan["backup_artifact_digest"]:
        raise DataIdentityRebindError("plan backup artifact digest differs")
    current_time = int(time.time()) if now is None else _integer(now, "current time")
    if (
        expires_at <= created_at
        or expires_at - created_at > MAX_LIFETIME_SECONDS
        or (
            not allow_expired
            and (current_time < created_at or current_time >= expires_at)
        )
    ):
        raise DataIdentityRebindError("data-identity rebind plan is expired")
    body = {key: plan[key] for key in PLAN_KEYS - {"plan_digest"}}
    if not hmac.compare_digest(
        digest(body).encode("ascii"),
        plan["plan_digest"].encode("ascii"),
    ):
        raise DataIdentityRebindError("data-identity rebind plan digest differs")
    return plan
