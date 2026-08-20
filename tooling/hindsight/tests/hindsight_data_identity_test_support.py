"""Shared payload-free evidence fixture for data-identity contract tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from hindsight_memory_control_plane.canonical import digest
from hindsight_memory_control_plane.data_identity_rebind import AGE_HEADER


def build_rebind_evidence(
    *,
    artifact: Path,
    data_root: Path,
    data_root_device: int,
    data_root_inode: int,
    postgres_data_root: Path,
    postgres_data_device: int,
    postgres_data_inode: int,
    collected_at: int,
    expires_at: int,
    backup_created_at: int,
    restored_at: int,
    postmaster_pid: int,
    postmaster_start_time: int,
) -> dict[str, Any]:
    postgres = {
        "system_identifier": "7659746962107358086",
        "data_root": str(data_root),
        "data_root_device": data_root_device,
        "data_root_inode": data_root_inode,
        "postgres_data_root": str(postgres_data_root),
        "postgres_data_device": postgres_data_device,
        "postgres_data_inode": postgres_data_inode,
        "postmaster_pid": postmaster_pid,
        "postmaster_start_time": postmaster_start_time,
    }
    postgres["connection_identity_digest"] = "0" * 64
    bank_ids = ["codex", "engineering"]
    database = {
        "observed_at": backup_created_at,
        "generation_before": "generation-1",
        "generation_after": "generation-1",
        "bank_ids": bank_ids,
        "bank_set_digest": digest({"bank_ids": bank_ids}),
        "codex_document_count": 5,
        "codex_manifest_digest": "a" * 64,
        "pending_operation_count": 0,
        "generic_import_receipt_count": 0,
        "schema_digest": "b" * 64,
    }
    database["snapshot_digest"] = "0" * 64
    artifact_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    evidence = {
        "schema_version": 1,
        "profile_id": "systalyze",
        "collected_at": collected_at,
        "expires_at": expires_at,
        "postgres": postgres,
        "database": database,
        "backup": {
            "artifact_root": str(artifact.parent),
            "artifact_root_device": artifact.parent.lstat().st_dev,
            "artifact_root_inode": artifact.parent.lstat().st_ino,
            "artifact_path": str(artifact),
            "artifact_sha256": artifact_digest,
            "ciphertext_size": artifact.stat().st_size,
            "full_schema": True,
            "encryption_format": "age",
            "encryption_recipient_digest": "d" * 64,
            "ciphertext_header_digest": hashlib.sha256(AGE_HEADER).hexdigest(),
            "plaintext_disposed": True,
            "created_at": backup_created_at,
        },
        "restore": {
            "artifact_sha256": artifact_digest,
            "schema_digest": database["schema_digest"],
            "bank_set_digest": database["bank_set_digest"],
            "codex_document_count": database["codex_document_count"],
            "codex_manifest_digest": database["codex_manifest_digest"],
            "restore_identity_digest": "c" * 64,
            "decryption_recipient_digest": "d" * 64,
            "dropped": True,
            "restored_at": restored_at,
        },
        "safety": {
            "hooks_disabled": True,
            "controller_authority_disabled": True,
            "no_serena_import_authority": True,
            "target_bank_inspected": False,
            "database_mutation_performed": False,
        },
    }
    return reseal_rebind_evidence(evidence)


def reseal_rebind_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    evidence["postgres"]["connection_identity_digest"] = digest(
        {
            key: value
            for key, value in evidence["postgres"].items()
            if key != "connection_identity_digest"
        }
    )
    snapshot = {
        "postgres": evidence["postgres"],
        "generation": evidence["database"]["generation_before"],
        "observed_at": evidence["database"]["observed_at"],
        "bank_set_digest": evidence["database"]["bank_set_digest"],
        "codex_document_count": evidence["database"]["codex_document_count"],
        "codex_manifest_digest": evidence["database"]["codex_manifest_digest"],
        "schema_digest": evidence["database"]["schema_digest"],
    }
    if "pending_operation_set_digest" in evidence["database"]:
        snapshot["pending_operation_set_digest"] = evidence["database"][
            "pending_operation_set_digest"
        ]
    evidence["database"]["snapshot_digest"] = digest(snapshot)
    return evidence
