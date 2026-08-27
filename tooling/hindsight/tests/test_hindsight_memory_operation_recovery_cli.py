import asyncio
import ast
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import json
import os
import runpy
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from contextlib import asynccontextmanager, ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from tooling.hindsight.tests import (
    test_hindsight_memory_operation_recovery as recovery_fixtures,
)
from tooling.hindsight.tests.test_hindsight_memory_provider_runtime import (
    four_codex_split_timeout_policy_data,
    policy_data,
    six_member_split_timeout_policy_data,
)
from tooling.hindsight.lib.hindsight_memory_control_plane import (
    operation_recovery_runtime,
)
from tooling.hindsight.lib.hindsight_memory_control_plane.operation_recovery_progress import (
    ExactDrainProgressRecorder,
    create_exact_drain_progress_recorder,
    read_exact_drain_progress,
)


ROOT = Path(__file__).resolve().parents[1]


def _rename_exact_policy_members(
    value: dict[str, object],
    *,
    include_luna: bool,
) -> dict[str, object]:
    renamed = {
        "work": "work-codex",
        "personal": "personal-codex",
        "alt1": "alt1-codex",
        "alt2": "alt2-codex",
        "fallback": "hatchery",
        "openai-luna": "openai-luna",
    }
    for member in value["members"]:
        member_id = renamed[member["id"]]
        member["id"] = member_id
        if member_id == "hatchery":
            member["identity"]["base_url"] = (
                "http://hatchery.komodo-vector.ts.net:13305/v1"
            )
            member["max_concurrent"] = 2
            member["execution_timeout_seconds"] = 3_600
        else:
            member["identity"]["credential_marker"] = (
                f"provider-policy:{member_id}"
            )
            if member_id.endswith("-codex"):
                member["identity"]["model"] = "gpt-5.3-codex-spark"
    value["failover_order"] = [
        "work-codex",
        "personal-codex",
        "alt1-codex",
        "alt2-codex",
        "hatchery",
    ]
    if include_luna:
        value["failover_order"].append("openai-luna")
    return value


def _legacy_exact_split_timeout_policy_data() -> dict[str, object]:
    return _rename_exact_policy_members(
        four_codex_split_timeout_policy_data(),
        include_luna=False,
    )


def _exact_split_timeout_policy_data() -> dict[str, object]:
    return _rename_exact_policy_members(
        six_member_split_timeout_policy_data(),
        include_luna=True,
    )


def _copy_patchable_entity_resolver(candidate_library: Path) -> Path:
    package_spec = importlib.util.find_spec("hindsight_api")
    if package_spec is None or package_spec.origin is None:
        raise unittest.SkipTest("hindsight_api candidate source is unavailable")
    source = Path(package_spec.origin).parent / "engine" / "entity_resolver.py"
    if not source.is_file():
        raise unittest.SkipTest("hindsight_api entity resolver is unavailable")
    target = (
        candidate_library
        / "hindsight_api"
        / "engine"
        / "entity_resolver.py"
    )
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o600)
    ops_source = source.parent / "db" / "ops_postgresql.py"
    if not ops_source.is_file():
        raise unittest.SkipTest("hindsight_api PostgreSQL ops source is unavailable")
    ops_target = target.parent / "db" / "ops_postgresql.py"
    ops_target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    shutil.copyfile(ops_source, ops_target)
    ops_target.chmod(0o600)
    for relative in ("engine/memory_engine.py", "worker/poller.py"):
        source_path = Path(package_spec.origin).parent / relative
        if not source_path.is_file():
            raise unittest.SkipTest(
                f"hindsight_api {relative} source is unavailable"
            )
        target_path = candidate_library / "hindsight_api" / relative
        target_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copyfile(source_path, target_path)
        target_path.chmod(0o600)
    return target


async def _locked_status(events, plan):
    if events[:2] != ["recovery-enter", "manager-enter"]:
        raise AssertionError("exact drain status ran outside its locks")
    return {
        "selected_status_counts": {"pending": 43},
        "preserved_status_counts": {"completed": 5},
        "outside_nonterminal_counts": [],
        "status_digest": "6" * 64,
        "generation_before": plan["pre_generation"],
    }


async def _immediate(value):
    return value


class OperationRecoveryCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = runpy.run_path(str(ROOT / "bin" / "hindsight-memory"))

    def test_operation_recovery_rebind_handoff_binds_verified_receipts(self):
        helper = self.controller[
            "_operation_recovery_verified_rebind_handoff"
        ]
        globals_ = helper.__globals__
        original = globals_["EXPECTED_OPERATION_RECOVERY_REBIND_HANDOFF"]
        self.assertEqual(
            original["reference_observed_data_identity_digest"],
            "1c7bcaca4c1fb01f7a2b4b44a7eea662b1f7180ba6097d3f8ed507c7f58bd5ce",
        )
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="operation-recovery-rebind-handoff-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan_path = root / "plan.json"
            rollback_path = root / "rollback.json"
            authorization_path = root / "authorization.json"
            application_path = root / "application.json"
            verification_path = root / "verification.json"
            plan_document = {
                "plan_digest": original["plan_digest"],
                "installation_state_digest": original[
                    "installation_state_digest_before"
                ],
                "expected_post_state_digest": original[
                    "installation_state_digest_after"
                ],
                "binding_generation_digest": original[
                    "binding_generation_digest"
                ],
                "current_release_digest": original[
                    "current_release_digest"
                ],
                "old_data_identity_digest": original[
                    "old_data_identity_digest"
                ],
                "new_data_identity_digest": original[
                    "new_data_identity_digest"
                ],
                "rollback_bundle_path": str(rollback_path),
                "authorization_receipt_path": str(authorization_path),
                "application_receipt_path": str(application_path),
                "verification_receipt_path": str(verification_path),
            }
            plan_path.write_text(
                json.dumps(plan_document) + "\n",
                encoding="utf-8",
            )
            plan_path.chmod(0o600)
            expected = deepcopy(original)
            expected["plan_path"] = str(plan_path)
            globals_["EXPECTED_OPERATION_RECOVERY_REBIND_HANDOFF"] = expected
            checked_plan = plan_document
            rollback_bundle = {
                "rollback_bundle_digest": expected[
                    "rollback_bundle_digest"
                ]
            }
            authorization = {
                "receipt_digest": expected[
                    "authorization_receipt_digest"
                ]
            }
            application = {
                "application_receipt_digest": expected[
                    "application_receipt_digest"
                ]
            }
            verification = {
                "verification_receipt_digest": expected[
                    "verification_receipt_digest"
                ],
                "postgres_system_identifier": expected[
                    "postgres_system_identifier"
                ],
                "database_continuity_digest": expected[
                    "database_continuity_digest"
                ],
                "post_evidence_digest": expected[
                    "post_evidence_digest"
                ],
                "verified_at": expected["verified_at"],
            }
            for path, value in (
                (rollback_path, rollback_bundle),
                (authorization_path, authorization),
                (application_path, application),
                (verification_path, verification),
            ):
                path.write_text(json.dumps(value) + "\n", encoding="utf-8")
                path.chmod(0o600)

            def read_receipt(path):
                return json.loads(path.read_text(encoding="utf-8"))

            def validate_handoff(value, state):
                if value != plan_document or state != {}:
                    raise self.controller["PortableInstallError"](
                        "verified rebind plan drifted"
                    )
                return checked_plan

            def validate_rollback(value):
                if value is not checked_plan:
                    raise self.controller["PortableInstallError"](
                        "verified rebind rollback chain differs"
                    )
                receipt = read_receipt(rollback_path)
                if receipt != rollback_bundle:
                    raise self.controller["PortableInstallError"](
                        "verified rebind rollback drifted"
                    )
                return rollback_bundle, {}

            def validate_authorization(value, *, allow_expired=False):
                if not allow_expired:
                    raise AssertionError(
                        "historical rebind validation must allow expired receipts"
                    )
                if value is not checked_plan:
                    raise self.controller["PortableInstallError"](
                        "verified rebind plan chain differs"
                    )
                receipt = read_receipt(authorization_path)
                if receipt != authorization:
                    raise self.controller["PortableInstallError"](
                        "verified rebind authorization drifted"
                    )
                return authorization

            def validate_application(value, checked_authorization):
                if (
                    value is not checked_plan
                    or checked_authorization is not authorization
                ):
                    raise self.controller["PortableInstallError"](
                        "verified rebind application chain differs"
                    )
                receipt = read_receipt(application_path)
                if receipt != application:
                    raise self.controller["PortableInstallError"](
                        "verified rebind application drifted"
                    )
                return application

            def validate_verification(value, checked_application):
                if (
                    value is not checked_plan
                    or checked_application is not application
                ):
                    raise self.controller["PortableInstallError"](
                        "verified rebind verification chain differs"
                    )
                receipt = read_receipt(verification_path)
                if receipt != verification:
                    raise self.controller["PortableInstallError"](
                        "verified rebind verification drifted"
                    )
                return verification

            manager = Mock()
            manager._validate_verified_rebind_upgrade_handoff.side_effect = (
                validate_handoff
            )
            manager._validate_rebind_rollback_bundle.side_effect = (
                validate_rollback
            )
            manager._validate_rebind_authorization_receipt.side_effect = (
                validate_authorization
            )
            manager._validate_rebind_application_receipt.side_effect = (
                validate_application
            )
            manager._validate_rebind_verification_receipt.side_effect = (
                validate_verification
            )
            try:
                handoff = helper(manager, {})
                body = {
                    key: value
                    for key, value in expected.items()
                    if key != "plan_path"
                }
                self.assertEqual(handoff, {
                    **body,
                    "handoff_digest": self.controller["digest"](body),
                })
                self.assertEqual(
                    manager._validate_verified_rebind_upgrade_handoff.call_args_list,
                    [call(plan_document, {}), call(plan_document, {})],
                )
                self.assertEqual(
                    manager._validate_rebind_rollback_bundle.call_args_list,
                    [call(checked_plan), call(checked_plan)],
                )
                self.assertEqual(
                    manager._validate_rebind_authorization_receipt.call_args_list,
                    [
                        call(checked_plan, allow_expired=True),
                        call(checked_plan, allow_expired=True),
                    ],
                )
                self.assertEqual(
                    manager._validate_rebind_application_receipt.call_args_list,
                    [
                        call(checked_plan, authorization),
                        call(checked_plan, authorization),
                    ],
                )
                self.assertEqual(
                    manager._validate_rebind_verification_receipt.call_args_list,
                    [
                        call(checked_plan, application),
                        call(checked_plan, application),
                    ],
                )
                authorization_path.write_text(
                    json.dumps({"receipt_digest": "0" * 64}) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    self.controller["OperationRecoveryError"],
                    "data-identity rebind handoff is invalid",
                ):
                    helper(manager, {})

                def reset_sources():
                    for path, value in (
                        (plan_path, plan_document),
                        (rollback_path, rollback_bundle),
                        (authorization_path, authorization),
                        (application_path, application),
                        (verification_path, verification),
                    ):
                        path.write_text(
                            json.dumps(value) + "\n",
                            encoding="utf-8",
                        )

                invocations = {}

                def mutate_on_second(name, path, value):
                    invocations[name] = invocations.get(name, 0) + 1
                    if invocations[name] == 2:
                        path.write_text(
                            json.dumps(value) + "\n",
                            encoding="utf-8",
                        )

                def validate_handoff_with_plan_drift(value, state):
                    checked = validate_handoff(value, state)
                    mutate_on_second(
                        "plan",
                        plan_path,
                        {"plan_digest": "0" * 64},
                    )
                    return checked

                def validate_rollback_with_drift(value):
                    checked = validate_rollback(value)
                    mutate_on_second(
                        "rollback",
                        rollback_path,
                        {"rollback_bundle_digest": "0" * 64},
                    )
                    return checked

                def validate_authorization_with_drift(
                    value,
                    *,
                    allow_expired=False,
                ):
                    checked = validate_authorization(
                        value,
                        allow_expired=allow_expired,
                    )
                    mutate_on_second(
                        "authorization",
                        authorization_path,
                        {"receipt_digest": "0" * 64},
                    )
                    return checked

                def validate_application_with_drift(
                    value,
                    checked_authorization,
                ):
                    checked = validate_application(
                        value,
                        checked_authorization,
                    )
                    mutate_on_second(
                        "application",
                        application_path,
                        {"application_receipt_digest": "0" * 64},
                    )
                    return checked

                def validate_verification_with_drift(
                    value,
                    checked_application,
                ):
                    checked = validate_verification(
                        value,
                        checked_application,
                    )
                    mutate_on_second(
                        "verification",
                        verification_path,
                        {"verification_receipt_digest": "0" * 64},
                    )
                    return checked

                drift_cases = (
                    (
                        "rollback",
                        manager._validate_rebind_rollback_bundle,
                        validate_rollback_with_drift,
                    ),
                    (
                        "authorization",
                        manager._validate_rebind_authorization_receipt,
                        validate_authorization_with_drift,
                    ),
                    (
                        "application",
                        manager._validate_rebind_application_receipt,
                        validate_application_with_drift,
                    ),
                    (
                        "verification",
                        manager._validate_rebind_verification_receipt,
                        validate_verification_with_drift,
                    ),
                    (
                        "plan",
                        manager._validate_verified_rebind_upgrade_handoff,
                        validate_handoff_with_plan_drift,
                    ),
                )
                for name, mocked_validator, callback in drift_cases:
                    with self.subTest(drift=name):
                        reset_sources()
                        invocations.clear()
                        manager._validate_verified_rebind_upgrade_handoff.side_effect = (
                            validate_handoff
                        )
                        manager._validate_rebind_rollback_bundle.side_effect = (
                            validate_rollback
                        )
                        manager._validate_rebind_authorization_receipt.side_effect = (
                            validate_authorization
                        )
                        manager._validate_rebind_application_receipt.side_effect = (
                            validate_application
                        )
                        manager._validate_rebind_verification_receipt.side_effect = (
                            validate_verification
                        )
                        mocked_validator.side_effect = callback
                        with self.assertRaisesRegex(
                            self.controller["OperationRecoveryError"],
                            "changed while pinned",
                        ):
                            helper(manager, {})
            finally:
                globals_["EXPECTED_OPERATION_RECOVERY_REBIND_HANDOFF"] = (
                    original
                )

    def test_exact_drain_status_rejects_non_object_plan_roots(self):
        command = self.controller["operation_recovery_drain_status_command"]
        globals_ = command.__globals__
        original_candidate = globals_["_operation_recovery_candidate"]
        globals_["_operation_recovery_candidate"] = lambda _args: {}
        try:
            with tempfile.TemporaryDirectory(
                dir="/private/tmp",
                prefix="exact-drain-invalid-plan-root-",
            ) as directory:
                root = Path(directory)
                root.chmod(0o700)
                for index, value in enumerate(
                    ([], None, 17, "exact-drain-plan")
                ):
                    with self.subTest(value=value):
                        plan_path = root / f"plan-{index}.json"
                        plan_path.write_text(json.dumps(value), encoding="utf-8")
                        plan_path.chmod(0o600)
                        with self.assertRaisesRegex(
                            self.controller["OperationRecoveryError"],
                            "exact drain plan is invalid",
                        ):
                            command(SimpleNamespace(plan=str(plan_path)))
        finally:
            globals_["_operation_recovery_candidate"] = original_candidate

    def _post_abort_plan(self, root: Path) -> dict:
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        reference = fixtures.legacy_drain_plan()
        snapshot = fixtures.post_abort_snapshot(
            reference,
            interrupted_operation_types=("retain", "consolidation"),
            observed_at=int(time.time()),
        )
        backup = recovery_fixtures.rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = snapshot[
            "generation_before"
        ]
        backup["source_authority"]["generation_after"] = snapshot[
            "generation_after"
        ]
        backup["source_authority_digest"] = self.controller["digest"](
            backup["source_authority"]
        )
        return self.controller["create_post_abort_recovery_plan"](
            reference,
            snapshot,
            candidate_release={
                "source_commit": "4" * 40,
                "version": "2026.08.10+4444444.operation-recovery.17",
                "release_digest": "5" * 64,
            },
            rollback_backup=backup,
            rollback_encryption=recovery_fixtures.rollback_encryption(),
            rollback_backup_path=str(root / "backup.age"),
            rollback_bundle_path=str(root / "bundle.age"),
            authorization_receipt_path=str(root / "authorization.json"),
            application_receipt_path=str(root / "application.json"),
            verification_receipt_path=str(root / "verification.json"),
            rollback_receipt_path=str(root / "rollback.json"),
            reference_application_authorization=(
                recovery_fixtures.exact_drain_authorization(reference)
            ),
            reference_application_journal=(
                recovery_fixtures.exact_drain_application_journal(reference)
            ),
            reference_application_progress_digest="c" * 64,
            schema_version=4,
            created_at=snapshot["observed_at"],
        )

    def _schema_10_recovery_handoff(
        self,
        root: Path,
        *,
        schema_version: int = 10,
    ) -> tuple[dict, dict, dict, Path]:
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        reference = fixtures.drain_plan(
            snapshot=fixtures.drain_snapshot(
                completed_positions=set(range(7)),
                observed_at=now - 100,
            ),
            created_at=now - 99,
        )
        interrupted = fixtures.post_abort_v10_snapshot(
            reference,
            observed_at=now - 80,
        )
        reference_rows = {
            item["operation_id"]: item
            for item in reference["live_snapshot"]["operations"]
        }
        interrupted["operations"] = [
            deepcopy(reference_rows[item["operation_id"]])
            if item["current_status"] == "pending"
            else item
            for item in interrupted["operations"]
        ]
        interrupted["status_counts"] = {
            status: sum(
                item["current_status"] == status
                for item in interrupted["operations"]
            )
            for status in recovery_fixtures.recovery_contract.OPERATION_STATUSES
        }
        interrupted["snapshot_digest"] = self.controller["digest"](
            {
                key: value
                for key, value in interrupted.items()
                if key != "snapshot_digest"
            }
        )
        if schema_version == 11:
            interrupted["installation_authority"] = (
                recovery_fixtures.rebound_installation_authority()
            )
            interrupted["snapshot_digest"] = self.controller["digest"](
                {
                    key: value
                    for key, value in interrupted.items()
                    if key != "snapshot_digest"
                }
            )
        recovery_backup_path = root / "recovery-backup.age"
        recovery_backup_path.write_bytes(b"synthetic-recovery-backup")
        recovery_backup_path.chmod(0o600)
        recovery_backup = recovery_fixtures.rollback_backup_evidence()
        recovery_backup["artifact_sha256"] = hashlib.sha256(
            recovery_backup_path.read_bytes()
        ).hexdigest()
        for key in ("generation_before", "generation_after"):
            recovery_backup["source_authority"][key] = interrupted[
                "generation_before"
            ]
        recovery_backup["source_authority"]["data_identity_digest"] = (
            interrupted["installation_authority"][
                "observed_data_identity_digest"
            ]
        )
        recovery_backup["source_authority_digest"] = self.controller[
            "digest"
        ](recovery_backup["source_authority"])
        recovery_plan_path = root / "recovery-plan.json"
        recovery_plan = self.controller[
            "create_post_abort_recovery_plan"
        ](
            reference,
            interrupted,
            candidate_release=recovery_fixtures.release_identity(),
            rollback_backup=recovery_backup,
            rollback_encryption=recovery_fixtures.rollback_encryption(),
            rollback_backup_path=str(recovery_backup_path),
            rollback_bundle_path=str(root / "recovery-bundle.age"),
            authorization_receipt_path=str(
                root / "fresh-progress.attempt-1.json"
            ),
            application_receipt_path=str(root / "recovery-application.json"),
            verification_receipt_path=str(
                root / "recovery-verification.json"
            ),
            rollback_receipt_path=str(root / "recovery-rollback.json"),
            reference_application_authorization=(
                recovery_fixtures.exact_drain_authorization(reference)
            ),
            reference_application_journal=(
                recovery_fixtures.exact_drain_application_journal(reference)
            ),
            reference_application_progress_digest="f" * 64,
            schema_version=schema_version,
            created_at=now - 79,
        )
        selected_ids = {
            item["operation_id"]
            for item in recovery_plan["selected_operations"]
        }
        retry_after = {
            item["operation_id"]: item["retry_count_after"]
            for item in recovery_plan["retry_recovery"]["operations"]
        }
        recovered_rows = []
        for item in interrupted["operations"]:
            row = {
                "operation_id": item["operation_id"],
                "bank_id": "engineering",
                "operation_type": item["operation_type"],
                "status": item["current_status"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "completed_at": item["completed_at"],
                "retry_count": item["retry_count"],
                "next_retry_at": item["next_retry_at"],
                "worker_id_present": item["worker_id_present"],
                "worker_id_digest": item["worker_id_digest"],
                "claimed_at": item["claimed_at"],
                "task_payload_present": item["task_payload_present"],
                "task_payload_digest": item["task_payload_digest"],
                "result_metadata_digest": item["result_metadata_digest"],
                "error_category": item["error_category"],
                "error_digest": item["error_digest"],
            }
            if item["operation_id"] in selected_ids:
                was_failed = row["status"] == "failed"
                row.update(
                    status="pending",
                    updated_at="2026-08-15T21:00:00.000000Z",
                    completed_at=None if was_failed else row["completed_at"],
                    retry_count=retry_after[item["operation_id"]],
                    next_retry_at=None if was_failed else row["next_retry_at"],
                    worker_id_present=False,
                    worker_id_digest=None,
                    claimed_at=None,
                    error_category=(
                        "none" if was_failed else row["error_category"]
                    ),
                    error_digest=None if was_failed else row["error_digest"],
                )
            recovered_rows.append(row)
        post_generation = self.controller[
            "_operation_recovery_next_generation"
        ](recovery_plan["pre_generation"])
        recovered_snapshot = dict(
            recovery_fixtures.create_live_snapshot(
                fixtures.cohort(),
                recovered_rows,
                generation_before=post_generation,
                generation_after=post_generation,
                installation_authority=(
                    recovery_fixtures.rebound_installation_authority()
                    if schema_version == 11
                    else recovery_fixtures.installation_authority()
                ),
                observed_at=now - 60,
            )
        )
        authorization_body = {
            "schema_version": 1,
            "kind": "operation-recovery-authorization-receipt",
            "plan_digest": recovery_plan["plan_digest"],
            "approval_digest": recovery_plan["plan_digest"],
            "candidate_release_digest": recovery_plan["candidate_release"][
                "release_digest"
            ],
            "authorized_at": now - 78,
        }
        authorization = {
            **authorization_body,
            "receipt_digest": self.controller["digest"](authorization_body),
        }
        ciphertext = b"synthetic-recovery-ciphertext"
        bundle = {
            "schema_version": 1,
            "kind": "operation-recovery-encrypted-rollback-bundle",
            "plan_digest": recovery_plan["plan_digest"],
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "ciphertext_base64": base64.b64encode(ciphertext).decode("ascii"),
        }
        application_body = {
            "schema_version": 1,
            "kind": "operation-recovery-application-receipt",
            "plan_digest": recovery_plan["plan_digest"],
            "authorization_receipt_digest": authorization["receipt_digest"],
            "rollback_bundle_digest": bundle["ciphertext_sha256"],
            "rollback_backup_digest": recovery_backup["artifact_sha256"],
            "pre_generation": recovery_plan["pre_generation"],
            "post_generation": post_generation,
            "selected_operation_count": recovery_plan[
                "selected_operation_count"
            ],
            "installation_authority_digest": self.controller["digest"](
                recovery_plan["installation_authority"]
            ),
            "applied_at": now - 77,
        }
        application = {
            **application_body,
            "receipt_digest": self.controller["digest"](application_body),
        }
        evidence = {
            "generation": post_generation,
            "selected_operation_count": recovery_plan[
                "selected_operation_count"
            ],
            "selected_status_counts": {
                "pending": recovery_plan["selected_operation_count"]
            },
            "cohort_operation_count": 48,
        }
        verification_body = {
            "schema_version": 1,
            "kind": "operation-recovery-post-abort-verification-receipt",
            "plan_digest": recovery_plan["plan_digest"],
            "application_receipt_digest": application["receipt_digest"],
            "installation_authority_digest": self.controller["digest"](
                recovery_plan["installation_authority"]
            ),
            "evidence": evidence,
            "verified_at": now - 76,
        }
        verification = {
            **verification_body,
            "receipt_digest": self.controller["digest"](verification_body),
        }
        documents = {
            str(recovery_plan_path): recovery_plan,
            recovery_plan["authorization_receipt_path"]: authorization,
            recovery_plan["rollback_bundle_path"]: bundle,
            recovery_plan["application_receipt_path"]: application,
            recovery_plan["verification_receipt_path"]: verification,
        }
        return (
            recovery_plan,
            recovered_snapshot,
            documents,
            recovery_plan_path,
        )

    def test_exact_drain_cli_exposes_plan_apply_monitor_status_and_verify(self):
        parser = self.controller["parser"]()
        authority = [
            "--config",
            "/private/tmp/config.json",
            "--candidate-release-root",
            "/private/tmp/candidate",
            "--candidate-release-identity",
            "/private/tmp/candidate.json",
        ]
        plan = parser.parse_args(
            [
                "operation-recovery",
                "drain",
                "plan",
                *authority,
                "--cohort",
                "/private/tmp/cohort.json",
                "--snapshot",
                "/private/tmp/snapshot.json",
                "--rollback-backup-evidence",
                "/private/tmp/backup.json",
                "--rollback-backup",
                "/private/tmp/backup.dump.age",
                "--provider-policy",
                "/private/tmp/providers.json",
                "--provider-runtime-root",
                "/private/tmp/provider-runtime",
                "--worker-runtime",
                "/private/tmp/hindsight-worker",
                "--authorization-receipt",
                "/private/tmp/authorization.json",
                "--application-receipt",
                "/private/tmp/application.json",
                "--status-artifact",
                "/private/tmp/status.json",
                "--verification-receipt",
                "/private/tmp/verification.json",
                "--checkpoint-continuation-handoff",
                "/private/tmp/checkpoint-continuation.json",
                "--output",
                "/private/tmp/plan.json",
            ]
        )
        self.assertIs(
            plan.run,
            self.controller["operation_recovery_drain_plan_command"],
        )
        self.assertEqual(
            plan.checkpoint_continuation_handoff,
            "/private/tmp/checkpoint-continuation.json",
        )
        grant_plan = parser.parse_args(
            [
                "operation-recovery",
                "drain",
                "grant",
                "plan",
                *authority,
                "--reference-plan",
                "/private/tmp/reference.json",
                "--grant-id",
                "44444444-4444-4444-8444-444444444444",
                "--maximum-recovery-epoch",
                "3",
                "--maximum-reconciliation-cycle",
                "1",
                "--maximum-plan-claims",
                "3",
                "--maximum-worker-attempts",
                "6",
                "--maximum-execution-seconds",
                "3000",
                "--maximum-concurrent-drains",
                "1",
                "--expires-at",
                "1785634800",
                "--output",
                "/private/tmp/grant-plan.json",
            ]
        )
        self.assertIs(
            grant_plan.run,
            self.controller[
                "operation_recovery_drain_grant_plan_command"
            ],
        )
        grant_approve = parser.parse_args(
            [
                "operation-recovery",
                "drain",
                "grant",
                "approve",
                *authority,
                "--plan",
                "/private/tmp/grant-plan.json",
                "--approval-digest",
                "a" * 64,
            ]
        )
        self.assertIs(
            grant_approve.run,
            self.controller[
                "operation_recovery_drain_grant_approve_command"
            ],
        )
        grant_status = parser.parse_args(
            [
                "operation-recovery",
                "drain",
                "grant",
                "status",
                "--config",
                "/private/tmp/config.json",
            ]
        )
        self.assertIs(
            grant_status.run,
            self.controller[
                "operation_recovery_drain_grant_status_command"
            ],
        )
        grant_revoke = parser.parse_args(
            [
                "operation-recovery",
                "drain",
                "grant",
                "revoke",
                "--config",
                "/private/tmp/config.json",
                "--approval-digest",
                "b" * 64,
            ]
        )
        self.assertIs(
            grant_revoke.run,
            self.controller[
                "operation_recovery_drain_grant_revoke_command"
            ],
        )
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "operation-recovery",
                    "drain",
                    "plan",
                    *authority,
                    "--cohort",
                    "/private/tmp/cohort.json",
                    "--snapshot",
                    "/private/tmp/snapshot.json",
                    "--rollback-backup-evidence",
                    "/private/tmp/backup.json",
                    "--rollback-backup",
                    "/private/tmp/backup.dump.age",
                    "--provider-policy",
                    "/private/tmp/providers.json",
                    "--provider-runtime-root",
                    "/private/tmp/provider-runtime",
                    "--worker-runtime",
                    "/private/tmp/hindsight-worker",
                    "--authorization-receipt",
                    "/private/tmp/authorization.json",
                    "--application-receipt",
                    "/private/tmp/application.json",
                    "--status-artifact",
                    "/private/tmp/status.json",
                    "--verification-receipt",
                    "/private/tmp/verification.json",
                    "--recovery-plan",
                    "/private/tmp/recovery-plan.json",
                    "--checkpoint-continuation-handoff",
                    "/private/tmp/checkpoint-continuation.json",
                    "--output",
                    "/private/tmp/plan.json",
                ]
            )
        for command, extra, function in (
            (
                "apply",
                [
                    "--approval-digest",
                    "a" * 64,
                    "--provider-policy",
                    "/private/tmp/providers.json",
                    "--provider-runtime-root",
                    "/private/tmp/provider-runtime",
                    "--worker-runtime",
                    "/private/tmp/hindsight-worker",
                ],
                "operation_recovery_drain_apply_command",
            ),
            ("monitor", [], "operation_recovery_drain_monitor_command"),
            ("status", [], "operation_recovery_drain_status_command"),
            ("verify", [], "operation_recovery_drain_verify_command"),
        ):
            parsed = parser.parse_args(
                [
                    "operation-recovery",
                    "drain",
                    command,
                    *authority,
                    "--plan",
                    "/private/tmp/plan.json",
                    *extra,
                ]
            )
            self.assertIs(parsed.run, self.controller[function])
        grant_apply = parser.parse_args(
            [
                "operation-recovery",
                "drain",
                "apply",
                *authority,
                "--plan",
                "/private/tmp/plan.json",
                "--provider-policy",
                "/private/tmp/providers.json",
                "--provider-runtime-root",
                "/private/tmp/provider-runtime",
                "--worker-runtime",
                "/private/tmp/hindsight-worker",
            ]
        )
        self.assertIsNone(grant_apply.approval_digest)

    def test_exact_drain_plan_command_builds_schema14_from_checkpoint_handoff(
        self,
    ):
        command = self.controller["operation_recovery_drain_plan_command"]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-checkpoint-plan-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            now = int(time.time())
            snapshot = fixtures.drain_snapshot(observed_at=now - 10)
            cohort = fixtures.cohort()
            handoff = fixtures._checkpoint_continuation_handoff(
                snapshot=snapshot
            )
            candidate = handoff["candidate_release"]
            handoff_path = root / "checkpoint-continuation.json"
            handoff_path.write_text(
                json.dumps(handoff) + "\n",
                encoding="utf-8",
            )
            handoff_path.chmod(0o600)
            rollback_path = root / "rollback.age"
            rollback_bytes = b"synthetic-backup"
            rollback_path.write_bytes(rollback_bytes)
            rollback_path.chmod(0o600)
            backup = recovery_fixtures.drain_backup_evidence()
            backup["artifact_sha256"] = hashlib.sha256(
                rollback_bytes
            ).hexdigest()
            backup["source_authority"]["generation_before"] = snapshot[
                "generation_before"
            ]
            backup["source_authority"]["generation_after"] = snapshot[
                "generation_after"
            ]
            backup["source_authority_digest"] = self.controller["digest"](
                backup["source_authority"]
            )
            documents = {
                "cohort": cohort,
                "snapshot": snapshot,
                "backup": backup,
                str(handoff_path): handoff,
            }
            captured = {}
            written = {}
            plan = {
                "authority": {"installation": "test"},
                "plan_digest": "c" * 64,
                "expires_at": now + 300,
                "selected_operation_count": 1,
                "selected_type_counts": {"retain": 1},
                "execution_window": {
                    "calculated_seconds": 60,
                    "maximum_seconds": 60,
                },
                "recovery_context": handoff["continuation_context"],
                "progress_artifact_path": str(root / "progress.json"),
                "status_artifact_path": str(root / "status.json"),
                "worker_max_attempts": 1,
            }
            replacements = {
                "_operation_recovery_candidate": lambda _args: candidate,
                "verify_cohort_manifest": lambda _value: cohort,
                "verify_live_snapshot": lambda _value: snapshot,
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "verify_checkpoint_continuation_handoff": (
                    lambda _value, **_kwargs: handoff
                ),
                "_operation_recovery_exact_phase_repair_snapshot": (
                    lambda **_kwargs: "7" * 64
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    lambda _path: ("9" * 64, object())
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "_operation_recovery_profile_environment": dict,
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: "8" * 64
                ),
                "_operation_recovery_validate_exact_provider_policy": (
                    lambda _policy, **_kwargs: None
                ),
                "_operation_recovery_hatchery_capability_receipt": (
                    lambda _policy, **_kwargs: {
                        "schema_version": 1,
                        "kind": "operation-recovery-hatchery-capability-receipt",
                        "provider_id": "hatchery",
                        "provider_policy_digest": "9" * 64,
                        "provider_identity_digest": "1" * 64,
                        "model_digest": "2" * 64,
                        "observed_at": now,
                        "successful": True,
                        "receipt_digest": "3" * 64,
                    }
                ),
                "_operation_recovery_provider_capability_receipt": (
                    lambda _policy, **_kwargs: {
                        "schema_version": 1,
                        "kind": "operation-recovery-provider-capability-receipt",
                        "provider_id": "openai-luna",
                        "attempted_provider_ids": [
                            "hatchery",
                            "openai-luna",
                        ],
                        "provider_policy_digest": "9" * 64,
                        "provider_identity_digest": "1" * 64,
                        "model_digest": "2" * 64,
                        "observed_at": now,
                        "successful": True,
                        "receipt_digest": "4" * 64,
                    }
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, **_kwargs: "a" * 64
                ),
                "create_exact_drain_plan": (
                    lambda *_args, **kwargs: captured.update(kwargs) or plan
                ),
                "write_private": (
                    lambda path, value, **kwargs: written.update(
                        {str(path): (value, kwargs)}
                    )
                ),
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                cohort="cohort",
                snapshot="snapshot",
                rollback_backup_evidence="backup",
                rollback_backup=str(rollback_path),
                provider_policy=str(root / "providers.json"),
                provider_runtime_root=str(root / "provider-runtime"),
                worker_runtime=str(root / "worker-runtime"),
                authorization_receipt=str(root / "authorization.json"),
                application_receipt=str(root / "application.json"),
                status_artifact=str(root / "status.json"),
                verification_receipt=str(root / "verification.json"),
                recovery_plan=None,
                checkpoint_continuation_handoff=str(handoff_path),
                output=str(root / "plan.json"),
            )
            try:
                result = command(args)
            finally:
                globals_.update(originals)

            self.assertEqual(result["status"], "planned")
            self.assertEqual(captured["schema_version"], 14)
            self.assertEqual(
                captured["checkpoint_continuation_handoff"], handoff
            )
            self.assertEqual(
                captured["recovery_context"], handoff["continuation_context"]
            )
            self.assertIs(written[args.output][1]["create_only"], True)

            state_root = root / "state"
            ledger_path = (
                state_root
                / "operation-recovery"
                / "exact-drain-grant-ledger.json"
            )
            ledger_path.parent.mkdir(parents=True, mode=0o700)
            ledger_path.write_text("{}\n", encoding="utf-8")
            ledger_path.chmod(0o600)
            grant = {
                "grant_id": "44444444-4444-4444-8444-444444444444",
                "grant_digest": "4" * 64,
                "scope": {"initial_reference_plan_digest": "5" * 64},
            }
            ledger = {
                "grant": grant,
                "grant_id": grant["grant_id"],
                "grant_digest": grant["grant_digest"],
                "ledger_digest": "6" * 64,
                "use_records": [],
            }
            manager = SimpleNamespace(
                config=SimpleNamespace(state_root=state_root)
            )
            grant_replacements = {
                **replacements,
                "_portable_manager": lambda _args: manager,
                "_operation_recovery_exact_grant_ledger": (
                    lambda _manager: ledger
                ),
            }
            grant_originals = {
                key: globals_[key] for key in grant_replacements
            }
            captured.clear()
            written.clear()
            grant_args = SimpleNamespace(**vars(args), config="config")
            globals_.update(grant_replacements)
            try:
                command(grant_args)
            finally:
                globals_.update(grant_originals)
            self.assertEqual(captured["schema_version"], 15)
            self.assertIs(captured["authorization_grant"], grant)
            self.assertEqual(
                captured["provider_capability_receipt"]["provider_id"],
                "openai-luna",
            )
            self.assertEqual(
                captured["grant_predecessor_plan_digest"],
                grant["scope"]["initial_reference_plan_digest"],
            )

    def test_post_abort_cli_exposes_plan_apply_status_verify_and_rollback(self):
        parser = self.controller["parser"]()
        authority = [
            "--config",
            "/private/tmp/config.json",
            "--candidate-release-root",
            "/private/tmp/candidate",
            "--candidate-release-identity",
            "/private/tmp/candidate.json",
        ]
        plan = parser.parse_args(
            [
                "operation-recovery",
                "drain",
                "post-abort",
                "plan",
                *authority,
                "--reference-plan",
                "/private/tmp/reference-plan.json",
                "--prior-recovery-plan",
                "/private/tmp/prior-recovery-plan.json",
                "--snapshot",
                "/private/tmp/live-snapshot.json",
                "--rollback-backup-evidence",
                "/private/tmp/backup.json",
                "--rollback-backup",
                "/private/tmp/backup.dump.age",
                "--age",
                "/private/tmp/age",
                "--rollback-recipient",
                "age1example",
                "--rollback-bundle",
                "/private/tmp/bundle.age",
                "--authorization-receipt",
                "/private/tmp/authorization.json",
                "--application-receipt",
                "/private/tmp/application.json",
                "--verification-receipt",
                "/private/tmp/verification.json",
                "--rollback-receipt",
                "/private/tmp/rollback.json",
                "--output",
                "/private/tmp/plan.json",
            ]
        )
        self.assertIs(
            plan.run,
            self.controller["operation_recovery_post_abort_plan_command"],
        )
        self.assertEqual(
            plan.prior_recovery_plan,
            "/private/tmp/prior-recovery-plan.json",
        )
        for command, extra, function in (
            (
                "apply",
                [
                    "--approval-digest",
                    "a" * 64,
                    "--age",
                    "/private/tmp/age",
                    "--age-identity",
                    "/private/tmp/age-key.txt",
                    "--rollback-recipient",
                    "age1example",
                ],
                "operation_recovery_post_abort_apply_command",
            ),
            ("status", [], "operation_recovery_post_abort_status_command"),
            ("verify", [], "operation_recovery_post_abort_verify_command"),
            (
                "rollback",
                [
                    "--approval-digest",
                    "b" * 64,
                    "--age",
                    "/private/tmp/age",
                    "--age-identity",
                    "/private/tmp/age-key.txt",
                ],
                "operation_recovery_post_abort_rollback_command",
            ),
        ):
            parsed = parser.parse_args(
                [
                    "operation-recovery",
                    "drain",
                    "post-abort",
                    command,
                    *authority,
                    "--plan",
                    "/private/tmp/plan.json",
                    *extra,
                ]
            )
            self.assertIs(parsed.run, self.controller[function])

    def test_post_abort_and_exact_drain_commands_share_process_lock_order(self):
        script = r'''
import asyncio
from pathlib import Path
import runpy
import sys
import time
from types import SimpleNamespace

controller = runpy.run_path(sys.argv[1])
mode = sys.argv[2]
root = Path(sys.argv[3])
candidate = {"source_commit": "4" * 40, "version": "test", "release_digest": "5" * 64}
expires_at = int(time.time()) + 5

class Manager:
    config = SimpleNamespace(state_root=root)
    def _lock(self):
        return controller["_operation_recovery_install_lock"](
            self,
            expires_at=expires_at,
        )

manager = Manager()
if mode == "exact":
    plan = {
        "candidate_release": candidate,
        "plan_digest": "a" * 64,
        "expires_at": expires_at,
        "pre_generation": "systalyze:public:1",
        "selected_operation_count": 43,
        "application_receipt_path": str(root / "missing-application.json"),
        "status_artifact_path": str(root / "status.json"),
    }
    command = controller["operation_recovery_drain_status_command"]
    globals_ = command.__globals__
    async def status(_args, _plan):
        await asyncio.sleep(0.25)
        return {
            "selected_status_counts": {"pending": 43},
            "preserved_status_counts": {"completed": 5},
            "outside_nonterminal_counts": [],
            "status_digest": "b" * 64,
            "generation_before": plan["pre_generation"],
        }
    globals_.update({
        "_operation_recovery_candidate": lambda _args: candidate,
        "verify_exact_drain_plan": lambda _value, **_kwargs: plan,
        "_operation_recovery_read_private_json": lambda _path, _label: plan,
        "_portable_manager": lambda _args: manager,
        "_operation_recovery_read_exact_drain_status": status,
        "write_private": lambda *_args, **_kwargs: None,
        "_print_result": lambda _value: 0,
    })
else:
    plan = {
        "candidate_release": candidate,
        "plan_digest": "c" * 64,
        "transaction_timeout_seconds": 5,
        "installation_authority": {"digest": "test"},
        "application_receipt_path": str(root / "application.json"),
        "verification_receipt_path": str(root / "verification.json"),
        "selected_operation_count": 15,
    }
    application = {
        "kind": "operation-recovery-application-receipt",
        "post_generation": "systalyze:public:2",
        "receipt_digest": "d" * 64,
        "applied_at": 1,
    }
    command = controller["operation_recovery_post_abort_verify_command"]
    globals_ = command.__globals__
    async def verify_live(*_args):
        await asyncio.sleep(0.05)
        return {
            "generation": application["post_generation"],
            "selected_operation_count": 15,
            "selected_status_counts": {"pending": 15},
            "cohort_operation_count": 48,
        }
    globals_.update({
        "_operation_recovery_candidate": lambda _args: candidate,
        "verify_post_abort_recovery_plan": lambda _value, **_kwargs: plan,
        "_operation_recovery_read_private_json": lambda _path, _label: application,
        "_operation_recovery_validate_application": lambda _value, **_kwargs: application,
        "_portable_manager": lambda _args: manager,
        "_operation_recovery_authority": lambda _args, **_kwargs: plan["installation_authority"],
        "_operation_recovery_post_abort_verify_live": verify_live,
        "write_private": lambda *_args, **_kwargs: None,
        "_print_result": lambda _value: 0,
    })

raise SystemExit(command(SimpleNamespace(plan="plan.json")))
'''
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-process-locks-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            controller_path = str(ROOT / "bin" / "hindsight-memory")
            exact = subprocess.Popen(
                [sys.executable, "-c", script, controller_path, "exact", directory],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(0.05)
            post_abort = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    controller_path,
                    "post-abort",
                    directory,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            exact_output = exact.communicate(timeout=4)
            post_abort_output = post_abort.communicate(timeout=4)

        self.assertEqual(exact.returncode, 0, exact_output)
        self.assertEqual(post_abort.returncode, 0, post_abort_output)

    def test_post_abort_apply_replays_final_without_historical_sources(self):
        command = self.controller["operation_recovery_post_abort_apply_command"]
        globals_ = command.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-apply-resume-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self._post_abort_plan(root)
            application_path = Path(plan["application_receipt_path"])
            application_path.touch(mode=0o600)
            application = {
                "kind": "operation-recovery-application-receipt",
                "pre_generation": plan["pre_generation"],
                "post_generation": self.controller[
                    "_operation_recovery_next_generation"
                ](plan["pre_generation"]),
                "receipt_digest": "a" * 64,
            }
            journal = {
                **application,
                "kind": "operation-recovery-application-journal",
                "receipt_digest": "b" * 64,
            }
            current = {"application": application}
            reference_sources_available = {"value": False}
            writes = []
            lock_events = []

            class Manager:
                pass

            class Tracked:
                def __init__(self, name):
                    self.name = name

                def __enter__(self):
                    lock_events.append(f"{self.name}-enter")

                def __exit__(self, *_arguments):
                    lock_events.append(f"{self.name}-exit")

            async def fail_prepare(*_args, **_kwargs):
                self.fail("idempotent apply must not recapture the preimage")

            async def fail_apply(*_args, **_kwargs):
                self.fail("idempotent apply must not mutate the database")

            async def verify_live(*_args, **_kwargs):
                return {
                    "generation": application["post_generation"],
                    "selected_operation_count": plan[
                        "selected_operation_count"
                    ],
                    "selected_status_counts": {
                        "pending": plan["selected_operation_count"]
                    },
                    "cohort_operation_count": 48,
                }

            def read(path, _label):
                path = str(path)
                if path == "plan.json":
                    return plan
                if path == plan["reference_plan"][
                    "authorization_receipt_path"
                ]:
                    if not reference_sources_available["value"]:
                        raise FileNotFoundError(
                            "reference source unavailable"
                        )
                    return plan["reference_application_authorization"]
                if path == plan["reference_plan"][
                    "application_receipt_path"
                ]:
                    if not reference_sources_available["value"]:
                        raise FileNotFoundError(
                            "reference source unavailable"
                        )
                    return plan["reference_application_journal"]
                return current["application"]

            replacements = {
                "verify_post_abort_recovery_plan": (
                    lambda _value, **_kwargs: plan
                ),
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_read_private_json": read,
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_precommit_artifacts": (
                    lambda: nullcontext(
                        {"mutation_attempted": False, "created": []}
                    )
                ),
                "_operation_recovery_install_lock": (
                    lambda _manager, **_kwargs: Tracked("installer")
                ),
                "_operation_recovery_lock": (
                    lambda _manager, **_kwargs: Tracked("recovery")
                ),
                "_operation_recovery_authority": (
                    lambda _args, **_kwargs: plan["installation_authority"]
                ),
                "_operation_recovery_validate_application": (
                    lambda _value, **_kwargs: application
                ),
                "_operation_recovery_validate_journal": (
                    lambda _value, *, plan: journal
                ),
                "_operation_recovery_post_abort_verify_live": verify_live,
                "_operation_recovery_post_abort_reference_progress_digest": (
                    lambda _reference_plan, _reference_journal: "c" * 64
                ),
                "_operation_recovery_prepare_apply": fail_prepare,
                "_operation_recovery_post_abort_apply": fail_apply,
                "_operation_recovery_finalize_journal": (
                    lambda _value: application
                ),
                "write_private": (
                    lambda path, value, **_kwargs: writes.append(
                        (str(path), value)
                    )
                ),
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                plan="plan.json",
                approval_digest=plan["plan_digest"],
            )
            try:
                result = command(args)
                self.assertEqual(writes, [])
                current["application"] = journal
                with self.assertRaisesRegex(
                    FileNotFoundError,
                    "reference source unavailable",
                ):
                    command(args)
                reference_sources_available["value"] = True
                recovered = command(args)
            finally:
                globals_.update(originals)

        self.assertEqual(result["status"], "already-applied")
        self.assertEqual(recovered["status"], "recovered-applied")
        self.assertEqual(writes, [(str(application_path), application)])
        self.assertEqual(
            lock_events,
            [
                "recovery-enter",
                "installer-enter",
                "installer-exit",
                "recovery-exit",
            ]
            * 3,
        )

    def test_post_abort_apply_rechecks_candidate_under_lock(self):
        command = self.controller["operation_recovery_post_abort_apply_command"]
        globals_ = command.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-apply-candidate-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self._post_abort_plan(root)
            drifted = {**plan["candidate_release"], "release_digest": "0" * 64}
            candidates = iter((plan["candidate_release"], drifted))

            class Manager:
                pass

            replacements = {
                "verify_post_abort_recovery_plan": (
                    lambda _value, **_kwargs: plan
                ),
                "_operation_recovery_candidate": lambda _args: next(candidates),
                "_operation_recovery_read_private_json": (
                    lambda _path, _label: plan
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_precommit_artifacts": (
                    lambda: nullcontext(
                        {"mutation_attempted": False, "created": []}
                    )
                ),
                "_operation_recovery_install_lock": (
                    lambda _manager, **_kwargs: nullcontext()
                ),
                "_operation_recovery_lock": (
                    lambda _manager, **_kwargs: nullcontext()
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(Exception, "candidate drifted"):
                    command(
                        SimpleNamespace(
                            plan="plan.json",
                            approval_digest=plan["plan_digest"],
                        )
                    )
            finally:
                globals_.update(originals)

    def test_post_abort_rollback_rechecks_candidate_under_lock(self):
        command = self.controller[
            "operation_recovery_post_abort_rollback_command"
        ]
        globals_ = command.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-rollback-candidate-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self._post_abort_plan(root)
            application = {
                "receipt_digest": "a" * 64,
                "post_generation": "systalyze:public:81679",
            }
            drifted = {**plan["candidate_release"], "release_digest": "0" * 64}
            candidates = iter((plan["candidate_release"], drifted))
            bundle = {
                "ciphertext_base64": base64.b64encode(b"ciphertext").decode(
                    "ascii"
                )
            }
            preimage = {
                "schema_version": 1,
                "kind": "operation-recovery-selected-row-preimage",
                "plan_digest": plan["plan_digest"],
                "rows": [],
            }

            class Manager:
                def _lock(self):
                    return nullcontext()

            async def fail_connect(*_args, **_kwargs):
                self.fail("candidate drift must prevent database rollback")

            documents = {
                "plan.json": plan,
                plan["application_receipt_path"]: application,
            }
            replacements = {
                "verify_post_abort_recovery_plan": (
                    lambda _value, **_kwargs: plan
                ),
                "_operation_recovery_candidate": lambda _args: next(candidates),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_validate_application": (
                    lambda _value, **_kwargs: application
                ),
                "_operation_recovery_rollback_approval": (
                    lambda _plan, _application: "b" * 64
                ),
                "_operation_recovery_validate_bundle": lambda _plan: bundle,
                "_operation_recovery_tool": lambda _path, _key: Path("/dev/null"),
                "_operation_recovery_rollback_identity_path": (
                    lambda _path: Path("/dev/null")
                ),
                "_age_decrypt_ciphertext": (
                    lambda **_kwargs: json.dumps(preimage).encode("utf-8")
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": (
                    lambda _manager, **_kwargs: nullcontext()
                ),
                "_operation_recovery_install_lock": (
                    lambda _manager, **_kwargs: nullcontext()
                ),
                "_operation_recovery_connect_live": fail_connect,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(Exception, "candidate drifted"):
                    command(
                        SimpleNamespace(
                            plan="plan.json",
                            approval_digest="b" * 64,
                            age="/dev/null",
                            age_identity="/dev/null",
                        )
                    )
            finally:
                globals_.update(originals)

    def test_post_abort_rollback_receipt_revalidates_without_decryption(self):
        command = self.controller[
            "operation_recovery_post_abort_rollback_command"
        ]
        globals_ = command.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-rollback-repeat-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self._post_abort_plan(root)
            application = {
                "receipt_digest": "a" * 64,
                "post_generation": "systalyze:public:81679",
            }
            receipt = {
                "kind": "operation-recovery-rollback-receipt",
                "pre_generation": "systalyze:public:81679",
                "post_generation": "systalyze:public:81680",
                "receipt_digest": "c" * 64,
            }
            Path(plan["rollback_receipt_path"]).touch(mode=0o600)
            documents = {
                "plan.json": plan,
                plan["application_receipt_path"]: application,
                plan["rollback_receipt_path"]: receipt,
            }

            class Manager:
                pass

            class Connection:
                async def close(self):
                    return None

            async def connect(*_args, **_kwargs):
                return Connection()

            async def verify_transaction(*_args, **kwargs):
                self.assertIsNone(kwargs["preimage"])
                return receipt["pre_generation"], receipt["post_generation"]

            def fail_decrypt(*_args, **_kwargs):
                self.fail("final rollback verification must not decrypt preimage")

            replacements = {
                "verify_post_abort_recovery_plan": (
                    lambda _value, **_kwargs: plan
                ),
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_validate_application": (
                    lambda _value, **_kwargs: application
                ),
                "_operation_recovery_rollback_approval": (
                    lambda _plan, _application: "b" * 64
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": (
                    lambda _manager, **_kwargs: nullcontext()
                ),
                "_operation_recovery_install_lock": (
                    lambda _manager, **_kwargs: nullcontext()
                ),
                "_operation_recovery_authority": (
                    lambda _args, **_kwargs: plan["installation_authority"]
                ),
                "_operation_recovery_validate_rollback_receipt": (
                    lambda _value, **_kwargs: receipt
                ),
                "_operation_recovery_validate_bundle": fail_decrypt,
                "_age_decrypt_ciphertext": fail_decrypt,
                "_operation_recovery_connect_live": connect,
                "rollback_post_abort_recovery_transaction": verify_transaction,
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                result = command(
                    SimpleNamespace(
                        plan="plan.json",
                        approval_digest="b" * 64,
                    )
                )
            finally:
                globals_.update(originals)

        self.assertEqual(result["status"], "already-rolled-back")
        self.assertEqual(result["receipt_digest"], "c" * 64)

    def test_post_abort_status_validates_verification_and_reports_rollback(self):
        command = self.controller["operation_recovery_post_abort_status_command"]
        globals_ = command.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-status-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self._post_abort_plan(root)
            Path(plan["application_receipt_path"]).touch(mode=0o600)
            Path(plan["verification_receipt_path"]).touch(mode=0o600)
            application = {
                "receipt_digest": "a" * 64,
                "pre_generation": plan["pre_generation"],
                "post_generation": "systalyze:public:81679",
            }
            documents = {
                "plan.json": plan,
                plan["application_receipt_path"]: {
                    "kind": "operation-recovery-application-receipt"
                },
                plan["verification_receipt_path"]: {},
            }
            replacements = {
                "verify_post_abort_recovery_plan": (
                    lambda _value, **_kwargs: plan
                ),
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_validate_application": (
                    lambda _value, **_kwargs: application
                ),
                "_operation_recovery_post_abort_validate_verification": (
                    Mock(side_effect=self.controller["OperationRecoveryError"](
                        "invalid verification"
                    ))
                ),
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(Exception, "invalid verification"):
                    command(SimpleNamespace(plan="plan.json"))
            finally:
                globals_.update(originals)

            Path(plan["verification_receipt_path"]).unlink()
            Path(plan["rollback_receipt_path"]).touch(mode=0o600)
            documents[plan["rollback_receipt_path"]] = {
                "kind": "operation-recovery-rollback-receipt"
            }
            rollback = {"receipt_digest": "c" * 64}
            replacements[
                "_operation_recovery_validate_rollback_receipt"
            ] = lambda _value, **_kwargs: rollback
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                result = command(SimpleNamespace(plan="plan.json"))
            finally:
                globals_.update(originals)

        self.assertEqual(result["status"], "rolled-back")
        self.assertEqual(result["rollback_receipt_digest"], "c" * 64)

    def test_post_abort_verify_command_seals_v4_plan_bound_evidence(self):
        command = self.controller[
            "operation_recovery_post_abort_verify_command"
        ]
        globals_ = command.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-v2-verify-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self._post_abort_plan(root)
            application = {
                "kind": "operation-recovery-application-receipt",
                "receipt_digest": "a" * 64,
                "pre_generation": plan["pre_generation"],
                "post_generation": self.controller[
                    "_operation_recovery_next_generation"
                ](plan["pre_generation"]),
                "applied_at": plan["created_at"],
            }
            evidence = {
                "generation": application["post_generation"],
                "selected_operation_count": 2,
                "selected_status_counts": {"pending": 2},
                "cohort_operation_count": 48,
            }
            documents = {
                "plan.json": plan,
                plan["application_receipt_path"]: application,
            }
            written = {}

            class Manager:
                pass

            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_validate_application": (
                    lambda _value, **_kwargs: application
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": (
                    lambda _manager, **_kwargs: nullcontext()
                ),
                "_operation_recovery_install_lock": (
                    lambda _manager, **_kwargs: nullcontext()
                ),
                "_operation_recovery_authority": (
                    lambda _args, **_kwargs: plan["installation_authority"]
                ),
                "_operation_recovery_post_abort_verify_live": (
                    lambda *_args: asyncio.sleep(0, result=evidence)
                ),
                "write_private": (
                    lambda path, value, *, create_only: written.update(
                        {str(path): (value, create_only)}
                    )
                ),
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                result = command(SimpleNamespace(plan="plan.json"))
            finally:
                globals_.update(originals)

            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["selected_operation_count"], 2)
            receipt, create_only = written[plan["verification_receipt_path"]]
            self.assertIs(create_only, True)
            self.assertEqual(receipt["plan_digest"], plan["plan_digest"])
            self.assertEqual(receipt["evidence"], evidence)

    def test_frozen_post_abort_v1_status_and_verify_are_idempotent(self):
        plan = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "legacy_exact_drain_plans.json"
            ).read_text(encoding="utf-8")
        )["post_abort"]
        authorization_digest = "a" * 64
        bundle_digest = "b" * 64
        application_body = {
            "schema_version": 1,
            "kind": "operation-recovery-application-receipt",
            "plan_digest": plan["plan_digest"],
            "authorization_receipt_digest": authorization_digest,
            "rollback_bundle_digest": bundle_digest,
            "rollback_backup_digest": plan["rollback_backup"][
                "artifact_sha256"
            ],
            "pre_generation": plan["pre_generation"],
            "post_generation": self.controller[
                "_operation_recovery_next_generation"
            ](plan["pre_generation"]),
            "selected_operation_count": 15,
            "installation_authority_digest": self.controller["digest"](
                plan["installation_authority"]
            ),
            "applied_at": plan["created_at"],
        }
        application = {
            **application_body,
            "receipt_digest": self.controller["digest"](application_body),
        }
        evidence = {
            "generation": application["post_generation"],
            "selected_operation_count": 15,
            "selected_status_counts": {"pending": 15},
            "cohort_operation_count": 48,
        }
        verification_body = {
            "schema_version": 1,
            "kind": "operation-recovery-post-abort-verification-receipt",
            "plan_digest": plan["plan_digest"],
            "application_receipt_digest": application["receipt_digest"],
            "installation_authority_digest": self.controller["digest"](
                plan["installation_authority"]
            ),
            "evidence": evidence,
            "verified_at": plan["created_at"],
        }
        verification = {
            **verification_body,
            "receipt_digest": self.controller["digest"](verification_body),
        }
        documents = {
            "plan.json": plan,
            plan["application_receipt_path"]: application,
            plan["verification_receipt_path"]: verification,
        }
        present = {
            plan["application_receipt_path"],
            plan["verification_receipt_path"],
        }
        original_exists = Path.exists

        def fixture_exists(path):
            return str(path) in present or original_exists(path)

        class Manager:
            pass

        writes = Mock()
        replacements = {
            "_operation_recovery_candidate": (
                lambda _args: plan["candidate_release"]
            ),
            "_operation_recovery_read_private_json": (
                lambda path, _label: documents[str(path)]
            ),
            "_operation_recovery_validate_authorization": (
                lambda _plan: {"receipt_digest": authorization_digest}
            ),
            "_operation_recovery_validate_bundle": (
                lambda _plan: {"ciphertext_sha256": bundle_digest}
            ),
            "_operation_recovery_planned_backup_digest": (
                lambda _plan: plan["rollback_backup"]["artifact_sha256"]
            ),
            "_portable_manager": lambda _args: Manager(),
            "_operation_recovery_lock": (
                lambda _manager, **_kwargs: nullcontext()
            ),
            "_operation_recovery_install_lock": (
                lambda _manager, **_kwargs: nullcontext()
            ),
            "_operation_recovery_authority": (
                lambda _args, **_kwargs: plan["installation_authority"]
            ),
            "_operation_recovery_post_abort_verify_live": (
                lambda *_args: _immediate(evidence)
            ),
            "write_private": writes,
            "_print_result": lambda value: value,
        }
        status_command = self.controller[
            "operation_recovery_post_abort_status_command"
        ]
        verify_command = self.controller[
            "operation_recovery_post_abort_verify_command"
        ]
        globals_ = status_command.__globals__
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            with patch.object(Path, "exists", fixture_exists):
                status = status_command(SimpleNamespace(plan="plan.json"))
                first = verify_command(SimpleNamespace(plan="plan.json"))
                second = verify_command(SimpleNamespace(plan="plan.json"))
        finally:
            globals_.update(originals)

        self.assertEqual(status["status"], "verified")
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "verified")
        self.assertEqual(
            first["receipt_digest"],
            verification["receipt_digest"],
        )
        writes.assert_not_called()

    def test_post_abort_status_rejects_non_object_lifecycle_artifacts(self):
        command = self.controller["operation_recovery_post_abort_status_command"]
        globals_ = command.__globals__
        error = self.controller["OperationRecoveryError"]
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-non-object-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self._post_abort_plan(root)
            application_path = Path(plan["application_receipt_path"])
            application_path.touch(mode=0o600)
            documents = {"plan.json": plan, str(application_path): []}
            application = {"receipt_digest": "a" * 64}
            replacements = {
                "verify_post_abort_recovery_plan": (
                    lambda _value, **_kwargs: plan
                ),
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_validate_journal": (
                    Mock(side_effect=error("closed application artifact"))
                ),
                "_operation_recovery_validate_application": (
                    lambda _value, **_kwargs: application
                ),
                "_operation_recovery_validate_rollback_journal": (
                    Mock(side_effect=error("closed rollback artifact"))
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(error, "closed application"):
                    command(SimpleNamespace(plan="plan.json"))

                documents[str(application_path)] = {
                    "kind": "operation-recovery-application-receipt"
                }
                rollback_path = Path(plan["rollback_receipt_path"])
                rollback_path.touch(mode=0o600)
                documents[str(rollback_path)] = []
                with self.assertRaisesRegex(error, "closed rollback"):
                    command(SimpleNamespace(plan="plan.json"))
            finally:
                globals_.update(originals)

    def test_exact_drain_journal_tracks_the_live_child_identity(self):
        start_time = self.controller["_process_start_time"](os.getpid())
        self.assertIsNotNone(start_time)
        journal = {
            "worker_pid": os.getpid(),
            "worker_start_time": start_time,
        }
        active = self.controller[
            "_operation_recovery_exact_journal_worker_active"
        ]
        self.assertTrue(active(journal))
        self.assertFalse(
            active({**journal, "worker_start_time": "identity-mismatch"})
        )

    def test_recovery_process_census_rejects_exact_drain_worker(self):
        check = self.controller["_assert_recovery_services_stopped"]
        globals_ = check.__globals__

        class Probe:
            def settimeout(self, _timeout):
                return None

            def connect_ex(self, _address):
                return 1

            def close(self):
                return None

        replacements = {
            "socket": SimpleNamespace(
                AF_INET=2,
                SOCK_STREAM=1,
                socket=lambda *_arguments: Probe(),
            ),
            "subprocess": SimpleNamespace(
                DEVNULL=-3,
                PIPE=-1,
                run=Mock(
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout=(
                            b"4242 999 /candidate/bin/"
                            b"hindsight-exact-drain-worker --resume\n"
                        ),
                    )
                ),
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            with self.assertRaisesRegex(
                Exception,
                "manual Hindsight process remains active",
            ):
                check(
                    SimpleNamespace(
                        config=SimpleNamespace(services=[]),
                    )
                )
        finally:
            globals_.update(originals)

    def test_exact_drain_bootstrap_resume_reuses_the_journaled_attempt(self):
        choose = self.controller[
            "_operation_recovery_exact_next_worker_attempt"
        ]
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-bootstrap-resume-",
        ) as directory:
            progress_path = Path(directory) / "progress.json"
            plan = {
                "plan_digest": "a" * 64,
                "progress_artifact_path": str(progress_path),
                "worker_max_attempts": 4,
            }
            self.assertEqual(choose(plan, {"worker_attempt": 1}), 1)
            ExactDrainProgressRecorder(
                path=progress_path,
                plan_digest=plan["plan_digest"],
                worker_pid=os.getpid(),
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                clock=lambda: 1000.0,
            )
            self.assertEqual(choose(plan, {"worker_attempt": 1}), 2)
            self.assertEqual(choose(plan, {"worker_attempt": 2}), 2)
            with self.assertRaisesRegex(Exception, "attempt differs"):
                choose(plan, {"worker_attempt": 3})


    def test_exact_drain_monitor_does_not_enter_apply_or_manager_locks(self):
        command = self.controller["operation_recovery_drain_monitor_command"]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-monitor-",
        ) as directory:
            root = Path(directory)
            now = int(time.time())
            plan = recovery_fixtures.recovery_contract.create_exact_drain_plan(
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "rollback.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verification.json"),
                created_at=now,
            )
            start_time = self.controller["_process_start_time"](os.getpid())
            authorization = self.controller[
                "_operation_recovery_exact_receipt"
            ](
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-authorization-receipt"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "approval_digest": plan["plan_digest"],
                    "candidate_release": plan["candidate_release"],
                    "provider_policy_digest": plan[
                        "provider_policy_digest"
                    ],
                    "worker_runtime_digest": plan["worker_runtime_digest"],
                    "authorized_at": plan["created_at"],
                }
            )
            journal = self.controller["_operation_recovery_exact_receipt"](
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-application-journal"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "authorization_receipt_digest": authorization[
                        "receipt_digest"
                    ],
                    "started_at": authorization["authorized_at"],
                    "worker_pid": os.getpid(),
                    "worker_start_time": start_time,
                    "worker_attempt": 1,
                }
            )
            recorder = ExactDrainProgressRecorder(
                path=Path(plan["progress_artifact_path"]),
                plan_digest=plan["plan_digest"],
                worker_pid=os.getpid(),
                worker_start_time=start_time,
                worker_attempt=1,
                selected_operations=plan["selected_operations"],
                progress_schema_version=plan.get("progress_schema_version", 1),
                clock=lambda: 1000.0,
            )
            active_request = recorder.provider_started(
                "work-codex",
                retry_attempt=1,
                scope="retain_extract_facts",
            )
            if plan.get("progress_schema_version") in {4, 5}:
                recorder.provider_executing(active_request)
            diagnostic_operation = plan["selected_operations"][0]["operation_id"]
            recorder.task_stage(
                diagnostic_operation,
                status="processing",
                stage="retain.phase1.candidates.fuzzy.1",
            )
            recorder.task_outcome(
                diagnostic_operation,
                status="pending",
                stage="retrying",
                failure={
                    "category": "phase_one_timeout",
                    "retryable": True,
                    "http_status": None,
                    "error_digest": "e" * 64,
                },
                checkpoint={
                    "facts_committed": True,
                    "committed_document_count": 1,
                    "unit_ids_count": 29,
                    "stage": "storing",
                    "processed": 14,
                    "total": 14,
                },
            )
            captured = {}
            documents = {
                str(root / "plan.json"): plan,
                plan["authorization_receipt_path"]: authorization,
                plan["application_receipt_path"]: journal,
            }
            Path(plan["application_receipt_path"]).touch()
            replacements = {
                "_operation_recovery_candidate": lambda _args: plan[
                    "candidate_release"
                ],
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_lock": lambda _manager: (_ for _ in ()).throw(
                    AssertionError("monitor entered the apply lock")
                ),
                "_portable_manager": lambda _args: (_ for _ in ()).throw(
                    AssertionError("monitor constructed the manager")
                ),
                "_print_result": lambda value: captured.update(value) or 0,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                manager = object.__new__(globals_["PortableInstallationManager"])
                manager.config = SimpleNamespace(state_root=root)
                with self.controller["_operation_recovery_lock"](manager):
                    with manager._lock():
                        result = command(
                            SimpleNamespace(plan=str(root / "plan.json"))
                        )
                interrupted_captured = {}
                original_worker_active = globals_[
                    "_operation_recovery_exact_journal_worker_active"
                ]
                globals_[
                    "_operation_recovery_exact_journal_worker_active"
                ] = lambda _journal: False
                globals_["_print_result"] = (
                    lambda value: interrupted_captured.update(value) or 0
                )
                interrupted_result = command(
                    SimpleNamespace(plan=str(root / "plan.json"))
                )
                globals_[
                    "_operation_recovery_exact_journal_worker_active"
                ] = original_worker_active
                globals_["_print_result"] = replacements["_print_result"]
                terminal_body = {
                    "schema_version": 2,
                    "kind": "operation-recovery-exact-drain-status",
                    "plan_digest": plan["plan_digest"],
                    "generation_before": "systalyze:public:200",
                    "generation_after": "systalyze:public:200",
                    "selected_operation_count": 43,
                    "selected_status_counts": {"completed": 43},
                    "preserved_status_counts": {"completed": 5},
                    "outside_nonterminal_counts": [],
                    "failure_classifications": [],
                    "observed_at": plan["created_at"] + 1,
                }
                terminal = {
                    **terminal_body,
                    "status_digest": self.controller["digest"](terminal_body),
                }
                application = self.controller[
                    "_operation_recovery_exact_receipt"
                ](
                    {
                        "schema_version": 1,
                        "kind": (
                            "operation-recovery-exact-drain-application-receipt"
                        ),
                        "plan_digest": plan["plan_digest"],
                        "candidate_release": plan["candidate_release"],
                        "authorization_receipt_digest": authorization[
                            "receipt_digest"
                        ],
                        "application_journal_digest": journal["receipt_digest"],
                        "worker_runtime_digest": plan["worker_runtime_digest"],
                        "provider_policy_digest": plan["provider_policy_digest"],
                        "terminal_status_digest": terminal["status_digest"],
                        "terminal_progress_digest": read_exact_drain_progress(
                            Path(plan["progress_artifact_path"]),
                            plan_digest=plan["plan_digest"],
                            progress_schema_version=plan.get(
                                "progress_schema_version", 1
                            ),
                        )["progress_digest"],
                        "selected_status_counts": {"completed": 43},
                        "outside_nonterminal_counts": [],
                        "worker_pid": journal["worker_pid"],
                        "worker_start_time": journal["worker_start_time"],
                        "worker_attempt": journal["worker_attempt"],
                        "started_at": journal["started_at"],
                        "completed_at": plan["created_at"] + 1,
                    }
                )
                documents[plan["status_artifact_path"]] = terminal
                documents[plan["application_receipt_path"]] = application
                with self.assertRaisesRegex(
                    Exception,
                    "terminal progress differs",
                ):
                    command(SimpleNamespace(plan=str(root / "plan.json")))
                recorder.provider_finished(
                    active_request,
                    outcome="succeeded",
                )
                recorder.task_stage(
                    diagnostic_operation,
                    status="processing",
                    stage="claimed",
                )
                for item in plan["selected_operations"]:
                    recorder.task_stage(
                        item["operation_id"],
                        status="completed",
                        stage="completed",
                    )
                application = self.controller[
                    "_operation_recovery_exact_receipt"
                ](
                    {
                        **{
                            key: value
                            for key, value in application.items()
                            if key != "receipt_digest"
                        },
                        "terminal_progress_digest": read_exact_drain_progress(
                            Path(plan["progress_artifact_path"]),
                            plan_digest=plan["plan_digest"],
                            progress_schema_version=plan.get(
                                "progress_schema_version", 1
                            ),
                        )["progress_digest"],
                    }
                )
                documents[plan["application_receipt_path"]] = application
                terminal_captured = {}
                replacements["_print_result"] = (
                    lambda value: terminal_captured.update(value) or 0
                )
                globals_["_print_result"] = replacements["_print_result"]
                original_time = globals_["time"]
                globals_["time"] = SimpleNamespace(
                    time=lambda: authorization["authorized_at"]
                    + plan["execution_window"]["calculated_seconds"]
                )
                try:
                    terminal_result = command(
                        SimpleNamespace(plan=str(root / "plan.json"))
                    )
                finally:
                    globals_["time"] = original_time
            finally:
                globals_.update(originals)

        self.assertEqual(result, 0)
        self.assertEqual(captured["status"], "running")
        self.assertEqual(interrupted_result, 0)
        self.assertEqual(interrupted_captured["status"], "interrupted")
        self.assertEqual(
            interrupted_captured["active_provider_requests"][0]["stale"],
            True,
        )
        self.assertEqual(
            interrupted_captured["active_provider_requests"][0][
                "execution_age_seconds"
            ],
            0.0,
        )
        self.assertEqual(captured["worker_status"], "starting")
        self.assertEqual(captured["worker_stage"], "progress.created")
        self.assertIsNone(captured["worker_failure_stage"])
        self.assertIsNone(captured["worker_failure"])
        self.assertIsNone(captured["worker_exit_code"])
        self.assertEqual(
            captured["selected_status_counts"],
            {"pending": 42, "retrying": 1},
        )
        diagnostic = next(
            item
            for item in captured["tasks"]
            if item["operation_id"] == diagnostic_operation
        )
        self.assertEqual(
            diagnostic["failure"],
            {
                "category": "phase_one_timeout",
                "retryable": True,
                "http_status": None,
                "error_digest": "e" * 64,
            },
        )
        self.assertEqual(
            diagnostic["checkpoint"]["committed_document_count"],
            1,
        )
        self.assertEqual(diagnostic["checkpoint"]["unit_ids_count"], 29)
        self.assertEqual(diagnostic["checkpoint"]["processed"], 14)
        self.assertEqual(diagnostic["checkpoint"]["total"], 14)
        diagnostic_output = json.dumps(captured, sort_keys=True)
        self.assertNotIn("raw provider response", diagnostic_output)
        self.assertNotIn("task payload", diagnostic_output)
        self.assertNotIn("error_message", diagnostic_output)
        self.assertEqual(
            captured["active_provider_requests"][0]["provider_id"],
            "work-codex",
        )
        self.assertRegex(captured["progress_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(captured["execution_lease_status"], "active")
        self.assertEqual(captured["expires_at"], plan["expires_at"])
        self.assertEqual(terminal_result, 0)
        self.assertEqual(terminal_captured["status"], "terminal")
        self.assertEqual(
            terminal_captured["execution_lease_status"],
            "expired",
        )
        self.assertEqual(
            terminal_captured["execution_lease_remaining_seconds"],
            0,
        )
        self.assertEqual(
            terminal_captured["selected_status_counts"],
            {"completed": 43},
        )
        self.assertEqual(
            terminal_captured["failure_classifications"],
            [],
        )
        self.assertEqual(
            terminal_captured["terminal_status_digest"],
            terminal["status_digest"],
        )

    def test_schema_eleven_interrupted_monitor_preserves_dynamic_legacy_projection(self):
        command = self.controller["operation_recovery_drain_monitor_command"]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-legacy-monitor-",
        ) as directory:
            root = Path(directory)
            now = int(time.time())
            plan = recovery_fixtures.recovery_contract.create_exact_drain_plan(
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "rollback.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verification.json"),
                created_at=now,
                schema_version=11,
            )
            start_time = self.controller["_process_start_time"](os.getpid())
            authorization = self.controller[
                "_operation_recovery_exact_receipt"
            ](
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-authorization-receipt"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "approval_digest": plan["plan_digest"],
                    "candidate_release": plan["candidate_release"],
                    "provider_policy_digest": plan[
                        "provider_policy_digest"
                    ],
                    "worker_runtime_digest": plan["worker_runtime_digest"],
                    "authorized_at": plan["created_at"],
                }
            )
            journal = self.controller["_operation_recovery_exact_receipt"](
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-application-journal"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "authorization_receipt_digest": authorization[
                        "receipt_digest"
                    ],
                    "started_at": authorization["authorized_at"],
                    "worker_pid": os.getpid(),
                    "worker_start_time": start_time,
                    "worker_attempt": 1,
                }
            )
            recorder = ExactDrainProgressRecorder(
                path=Path(plan["progress_artifact_path"]),
                plan_digest=plan["plan_digest"],
                worker_pid=os.getpid(),
                worker_start_time=start_time,
                worker_attempt=1,
                selected_operations=plan["selected_operations"],
                progress_schema_version=4,
                clock=lambda: 1000.0,
            )
            recorder.provider_started(
                "hatchery",
                retry_attempt=1,
                scope="retain_extract_facts",
            )
            progress = read_exact_drain_progress(
                Path(plan["progress_artifact_path"]),
                plan_digest=plan["plan_digest"],
                progress_schema_version=4,
                now=1001.0,
            )
            Path(plan["application_receipt_path"]).touch()
            documents = {
                str(root / "plan.json"): plan,
                plan["authorization_receipt_path"]: authorization,
                plan["application_receipt_path"]: journal,
            }
            freeze_arguments = []
            captured = {}

            def read_progress(_plan, *, freeze_ages_at_observed_at=False):
                freeze_arguments.append(freeze_ages_at_observed_at)
                return progress

            replacements = {
                "_operation_recovery_candidate": lambda _args: plan[
                    "candidate_release"
                ],
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_read_monitor_progress": read_progress,
                "_operation_recovery_exact_journal_worker_active": (
                    lambda _journal: False
                ),
                "_print_result": lambda value: captured.update(value) or 0,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                result = command(SimpleNamespace(plan=str(root / "plan.json")))
            finally:
                globals_.update(originals)

        self.assertEqual(result, 0)
        self.assertEqual(captured["status"], "interrupted")
        self.assertEqual(freeze_arguments, [False])
        self.assertNotIn("stale", captured["active_provider_requests"][0])

    def test_exact_drain_monitor_reports_not_started_without_run_artifacts(self):
        command = self.controller["operation_recovery_drain_monitor_command"]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-not-started-",
        ) as directory:
            root = Path(directory)
            now = int(time.time())
            plan = recovery_fixtures.recovery_contract.create_exact_drain_plan(
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "rollback.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verification.json"),
                created_at=now,
            )
            captured = {}
            plan_path = root / "plan.json"
            authorization = self.controller[
                "_operation_recovery_exact_receipt"
            ](
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-authorization-receipt"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "approval_digest": plan["plan_digest"],
                    "candidate_release": plan["candidate_release"],
                    "provider_policy_digest": plan[
                        "provider_policy_digest"
                    ],
                    "worker_runtime_digest": plan["worker_runtime_digest"],
                    "authorized_at": plan["expires_at"] - 1,
                }
            )
            journal = self.controller["_operation_recovery_exact_receipt"](
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-application-journal"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "authorization_receipt_digest": authorization[
                        "receipt_digest"
                    ],
                    "started_at": authorization["authorized_at"],
                    "worker_pid": os.getpid(),
                    "worker_start_time": "starting-worker-token",
                    "worker_attempt": 1,
                }
            )
            documents = {
                str(plan_path): plan,
                plan["authorization_receipt_path"]: authorization,
                plan["application_receipt_path"]: journal,
            }
            observed_time = [plan["expires_at"] + 1]

            def worker_active(_journal):
                observed_time[0] = (
                    authorization["authorized_at"]
                    + plan["execution_window"]["calculated_seconds"]
                )
                return True

            replacements = {
                "_operation_recovery_candidate": lambda _args: plan[
                    "candidate_release"
                ],
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_exact_journal_worker_active": (
                    worker_active
                ),
                "_print_result": lambda value: captured.update(value) or 0,
                "time": SimpleNamespace(time=lambda: observed_time[0]),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                result = command(SimpleNamespace(plan=str(plan_path)))
                Path(plan["authorization_receipt_path"]).touch()
                authorized = {}
                globals_["_print_result"] = (
                    lambda value: authorized.update(value) or 0
                )
                authorized_result = command(
                    SimpleNamespace(plan=str(plan_path))
                )
                Path(plan["application_receipt_path"]).touch()
                starting = {}
                globals_["_print_result"] = (
                    lambda value: starting.update(value) or 0
                )
                starting_result = command(SimpleNamespace(plan=str(plan_path)))
            finally:
                globals_.update(originals)

        self.assertEqual(result, 0)
        self.assertEqual(captured["status"], "not-started")
        self.assertEqual(captured["plan_digest"], plan["plan_digest"])
        self.assertEqual(captured["execution_lease_status"], "not-authorized")
        self.assertIsNone(captured["execution_lease_remaining_seconds"])
        self.assertEqual(authorized_result, 0)
        self.assertEqual(authorized["status"], "authorization-only")
        self.assertEqual(authorized["execution_lease_status"], "active")
        self.assertEqual(
            authorized["execution_lease_started_at"],
            authorization["authorized_at"],
        )
        self.assertEqual(
            authorized["execution_lease_expires_at"],
            authorization["authorized_at"]
            + plan["execution_window"]["calculated_seconds"],
        )
        self.assertEqual(
            authorized["execution_lease_remaining_seconds"],
            plan["execution_window"]["calculated_seconds"] - 2,
        )
        self.assertEqual(starting_result, 0)
        self.assertEqual(starting["status"], "starting")
        self.assertEqual(starting["worker_attempt"], 1)
        self.assertEqual(starting["execution_lease_status"], "expired")
        self.assertEqual(starting["execution_lease_remaining_seconds"], 0)

    def test_exact_drain_monitor_retries_atomic_progress_replacement(self):
        read = self.controller["_operation_recovery_read_monitor_progress"]
        globals_ = read.__globals__
        error = self.controller["OperationRecoveryError"](
            "exact drain progress changed while reading"
        )
        expected = {"progress_digest": "a" * 64}
        progress_reader = Mock(side_effect=[error, error, expected])
        sleep = Mock()
        originals = {
            "read_exact_drain_progress": globals_["read_exact_drain_progress"],
            "time": globals_["time"],
        }
        globals_["read_exact_drain_progress"] = progress_reader
        globals_["time"] = SimpleNamespace(sleep=sleep)
        try:
            observed = read(
                {
                    "plan_digest": "b" * 64,
                    "progress_artifact_path": "/private/tmp/progress.json",
                }
            )
        finally:
            globals_.update(originals)

        self.assertEqual(observed, expected)
        self.assertEqual(progress_reader.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.01)] * 2)

    def test_recovery_process_census_ignores_marker_in_ancestor_command(self):
        check = self.controller["_assert_recovery_services_stopped"]
        globals_ = check.__globals__

        class Probe:
            def settimeout(self, _timeout):
                return None

            def connect_ex(self, _address):
                return 1

            def close(self):
                return None

        replacements = {
            "socket": SimpleNamespace(
                AF_INET=2,
                SOCK_STREAM=1,
                socket=lambda *_arguments: Probe(),
            ),
            "subprocess": SimpleNamespace(
                DEVNULL=-3,
                PIPE=-1,
                run=Mock(
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout=(
                            b"100 99 /bin/zsh -c hindsight-exact-drain-worker\n"
                            b"99 1 /bin/zsh hindsight-exact-drain-worker\n"
                        ),
                    )
                ),
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            with patch.object(os, "getpid", return_value=100):
                check(SimpleNamespace(config=SimpleNamespace(services=[])))
        finally:
            globals_.update(originals)

    def test_exact_drain_plan_command_is_unapproved_and_payload_free(self):
        command = self.controller["operation_recovery_drain_plan_command"]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-plan-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            rollback_path = root / "rollback.dump.age"
            rollback_path.write_bytes(b"synthetic-encrypted-backup")
            rollback_path.chmod(0o600)
            backup = recovery_fixtures.drain_backup_evidence()
            backup["artifact_sha256"] = hashlib.sha256(
                rollback_path.read_bytes()
            ).hexdigest()
            documents = {
                "cohort": fixtures.cohort(),
                "snapshot": fixtures.drain_snapshot(
                    observed_at=int(time.time())
                ),
                "backup": backup,
            }
            written = {}
            runtime_schemas = []
            candidate = recovery_fixtures.release_identity()
            replacements = {
                "_operation_recovery_candidate": lambda _args: candidate,
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    lambda _path: ("9" * 64, object())
                ),
                "_operation_recovery_profile_environment": lambda: {},
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: "7" * 64
                ),
                "_operation_recovery_exact_phase_repair_snapshot": (
                    lambda: "7" * 64
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, *, schema_version=10: (
                        runtime_schemas.append(schema_version) or "8" * 64
                    )
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "write_private": (
                    lambda path, value, *, create_only: written.update(
                        {str(path): (value, create_only)}
                    )
                ),
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                cohort="cohort",
                snapshot="snapshot",
                rollback_backup_evidence="backup",
                rollback_backup=str(rollback_path),
                provider_policy=str(root / "providers.json"),
                provider_runtime_root=str(root / "provider-runtime"),
                worker_runtime=str(root / "hindsight-worker"),
                authorization_receipt=str(root / "authorization.json"),
                application_receipt=str(root / "application.json"),
                status_artifact=str(root / "status.json"),
                verification_receipt=str(root / "verification.json"),
                output=str(root / "plan.json"),
            )
            try:
                result = command(args)
            finally:
                globals_.update(originals)

            self.assertEqual(result["status"], "planned")
            self.assertEqual(result["authority"], "unapproved-plan")
            self.assertEqual(result["selected_operation_count"], 43)
            self.assertEqual(runtime_schemas, [12])
            plan, create_only = written[args.output]
            self.assertIs(create_only, True)
            self.assertIs(plan["mutation_authorized"], False)
            self.assertEqual(plan["schema_version"], 12)
            self.assertEqual(plan["progress_schema_version"], 5)
            serialized = json.dumps(plan, sort_keys=True)
            self.assertNotIn('"task_payload":', serialized)
            self.assertNotIn('"worker_id":', serialized)
            self.assertNotIn('"error_message":', serialized)

            alias_args = SimpleNamespace(**vars(args))
            alias_args.output = str(root / "progress.json")
            written.clear()
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "plan path aliases an artifact",
                ):
                    command(alias_args)
            finally:
                globals_.update(originals)
            self.assertEqual(written, {})

            artifact_alias_args = SimpleNamespace(**vars(args))
            artifact_alias_args.authorization_receipt = str(
                root / "progress.attempt-1.json"
            )
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "artifact paths must be distinct",
                ):
                    command(artifact_alias_args)
            finally:
                globals_.update(originals)
            self.assertEqual(written, {})

            alias_args.output = str(root / "progress.attempt-1.json")
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "plan path aliases an artifact",
                ):
                    command(alias_args)
            finally:
                globals_.update(originals)
            self.assertEqual(written, {})

    def test_post_abort_plan_command_authenticates_epoch_two_lineage(self):
        command = self.controller[
            "operation_recovery_post_abort_plan_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-v11-epoch-two-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            (
                prior_recovery,
                recovered_snapshot,
                documents,
                prior_recovery_path,
            ) = self._schema_10_recovery_handoff(
                root,
                schema_version=11,
            )
            prior_application = documents[
                prior_recovery["application_receipt_path"]
            ]
            prior_verification = documents[
                prior_recovery["verification_receipt_path"]
            ]
            selected_ids = {
                item["operation_id"]
                for item in prior_recovery["selected_operations"]
            }
            fresh_selected_ids = {
                item["operation_id"]
                for item in recovered_snapshot["operations"]
                if item["current_status"] == "pending"
            }
            self.assertEqual(len(selected_ids), 23)
            self.assertEqual(len(fresh_selected_ids), 39)
            recovery_context = {
                "schema_version": 1,
                "kind": "operation-recovery-exact-drain-recovery-context",
                "origin": "post-abort",
                "generation": recovered_snapshot["generation_before"],
                "recovery_epoch": 1,
                "candidate_release_digest": prior_recovery[
                    "candidate_release"
                ]["release_digest"],
                "selected_operation_ids_digest": self.controller["digest"](
                    sorted(fresh_selected_ids)
                ),
                "initial_origin_digest": None,
                "post_abort_selected_operation_ids_digest": self.controller[
                    "digest"
                ](sorted(selected_ids)),
                "post_abort_plan_digest": prior_recovery["plan_digest"],
                "post_abort_application_receipt_digest": prior_application[
                    "receipt_digest"
                ],
                "post_abort_verification_receipt_digest": prior_verification[
                    "receipt_digest"
                ],
                "retry_recovery_digest": prior_recovery[
                    "retry_recovery_digest"
                ],
                "selected_checkpoint_set_digest": prior_recovery[
                    "selected_checkpoint_set_digest"
                ],
                "preserved_row_set_digest": prior_recovery[
                    "preserved_row_set_digest"
                ],
            }
            exact_backup = recovery_fixtures.drain_backup_evidence()
            exact_backup["source_authority"]["data_identity_digest"] = (
                recovered_snapshot["installation_authority"][
                    "observed_data_identity_digest"
                ]
            )
            for key in ("generation_before", "generation_after"):
                exact_backup["source_authority"][key] = recovered_snapshot[key]
            exact_backup["source_authority_digest"] = self.controller[
                "digest"
            ](exact_backup["source_authority"])
            reference = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                recovered_snapshot,
                candidate_release=prior_recovery["candidate_release"],
                rollback_backup=exact_backup,
                rollback_backup_path=str(root / "exact-backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "exact-auth.json"),
                application_receipt_path=str(root / "exact-app.json"),
                status_artifact_path=str(root / "exact-status.json"),
                verification_receipt_path=str(root / "exact-verify.json"),
                recovery_context=recovery_context,
                created_at=now - 59,
                schema_version=11,
            )
            worker_digest = hashlib.sha256(
                (
                    "operation-recovery-exact-drain-"
                    f"{reference['plan_digest'][:12]}"
                ).encode()
            ).hexdigest()
            interrupted_rows = []
            for item in recovered_snapshot["operations"]:
                row = {
                    "operation_id": item["operation_id"],
                    "bank_id": "engineering",
                    "operation_type": item["operation_type"],
                    "status": item["current_status"],
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                    "completed_at": item["completed_at"],
                    "retry_count": item["retry_count"],
                    "next_retry_at": item["next_retry_at"],
                    "worker_id_present": item["worker_id_present"],
                    "worker_id_digest": item["worker_id_digest"],
                    "claimed_at": item["claimed_at"],
                    "task_payload_present": item["task_payload_present"],
                    "task_payload_digest": item["task_payload_digest"],
                    "result_metadata_digest": item[
                        "result_metadata_digest"
                    ],
                    "error_category": item["error_category"],
                    "error_digest": item["error_digest"],
                }
                if item["operation_id"] in fresh_selected_ids:
                    row.update(
                        status="failed",
                        updated_at="2026-08-20T14:10:00.000000Z",
                        completed_at="2026-08-20T14:10:00.000000Z",
                        retry_count=3,
                        next_retry_at=None,
                        worker_id_present=True,
                        worker_id_digest=worker_digest,
                        claimed_at="2026-08-20T14:05:00.000000Z",
                        error_category="provider_transport",
                        error_digest="c" * 64,
                    )
                interrupted_rows.append(row)
            interrupted = dict(
                recovery_fixtures.create_live_snapshot(
                    fixtures.cohort(),
                    interrupted_rows,
                    generation_before="systalyze:public:81701",
                    generation_after="systalyze:public:81701",
                    installation_authority=(
                        recovery_fixtures.rebound_installation_authority()
                    ),
                    observed_at=now - 20,
                )
            )
            rollback_path = root / "epoch-two-backup.age"
            rollback_path.write_bytes(b"synthetic-epoch-two-backup")
            rollback_path.chmod(0o600)
            backup = recovery_fixtures.rollback_backup_evidence()
            backup["artifact_sha256"] = hashlib.sha256(
                rollback_path.read_bytes()
            ).hexdigest()
            backup["source_authority"]["data_identity_digest"] = interrupted[
                "installation_authority"
            ]["observed_data_identity_digest"]
            for key in ("generation_before", "generation_after"):
                backup["source_authority"][key] = interrupted[key]
            backup["source_authority_digest"] = self.controller["digest"](
                backup["source_authority"]
            )
            authorization = recovery_fixtures.exact_drain_authorization(
                reference
            )
            journal = recovery_fixtures.exact_drain_application_journal(
                reference
            )
            documents.update(
                {
                    "reference": reference,
                    "snapshot": interrupted,
                    "backup": backup,
                    reference["authorization_receipt_path"]: authorization,
                    reference["application_receipt_path"]: journal,
                }
            )
            encryption = recovery_fixtures.rollback_encryption()
            registration = {
                **backup["source_authority"]["binding"],
                "_password": "not-observable",
            }
            progress = {
                "plan_digest": reference["plan_digest"],
                "progress_digest": "d" * 64,
                "worker_pid": journal["worker_pid"],
                "worker_start_time": journal["worker_start_time"],
                "worker_attempt": journal["worker_attempt"],
                "tasks": [
                    {
                        "operation_id": item["operation_id"],
                        "operation_type": item["operation_type"],
                        "row_digest": item["row_digest"],
                    }
                    for item in reference["selected_operations"]
                ],
            }
            written = {}
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: prior_recovery["candidate_release"]
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_tool": lambda path, _name: Path(path),
                "_operation_recovery_rollback_encryption": (
                    lambda _recipient: encryption
                ),
                "_operation_recovery_toolchain_digest": (
                    lambda: backup["toolchain_digest"]
                ),
                "_operation_recovery_exact_journal_worker_active": (
                    lambda _journal: False
                ),
                "read_exact_drain_progress": (
                    lambda _path, *, plan_digest, progress_schema_version=1: dict(
                        progress
                    )
                ),
                "read_pg0_registration": lambda _profile: dict(registration),
                "write_private": (
                    lambda path, value, *, create_only: written.update(
                        {str(path): (value, create_only)}
                    )
                ),
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                reference_plan="reference",
                prior_recovery_plan=str(prior_recovery_path),
                snapshot="snapshot",
                rollback_backup_evidence="backup",
                rollback_backup=str(rollback_path),
                age=encryption["age_path"],
                rollback_recipient=encryption["recipient"],
                rollback_bundle=str(root / "epoch-two-bundle.age"),
                authorization_receipt=str(root / "epoch-two-auth.json"),
                application_receipt=str(root / "epoch-two-app.json"),
                verification_receipt=str(root / "epoch-two-verify.json"),
                rollback_receipt=str(root / "epoch-two-rollback.json"),
                output=str(root / "epoch-two-plan.json"),
            )
            try:
                result = command(args)
            finally:
                globals_.update(originals)

            plan, create_only = written[args.output]
            self.assertTrue(create_only)
            self.assertEqual(result["recovery_epoch"], 2)
            self.assertEqual(plan["schema_version"], 11)
            self.assertEqual(plan["selected_status_counts"], {"failed": 39})
            self.assertEqual(plan["retry_recovery"]["schema_version"], 2)
            self.assertEqual(
                plan["retry_recovery"]["prior_retry_recovery_digest"],
                prior_recovery["retry_recovery_digest"],
            )

            missing_prior_args = SimpleNamespace(**vars(args))
            missing_prior_args.prior_recovery_plan = None
            written.clear()
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "prior recovery handoff differs",
                ):
                    command(missing_prior_args)
            finally:
                globals_.update(originals)
            self.assertEqual(written, {})

            alias_args = SimpleNamespace(**vars(args))
            alias_args.output = str(prior_recovery_path)
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "plan path aliases an artifact",
                ):
                    command(alias_args)
            finally:
                globals_.update(originals)
            self.assertEqual(written, {})

            prior_reads = 0

            def read_with_prior_drift(path, _label):
                nonlocal prior_reads
                if str(path) == str(prior_recovery_path):
                    prior_reads += 1
                    if prior_reads == 2:
                        drifted = deepcopy(prior_recovery)
                        drifted["retry_recovery_digest"] = "0" * 64
                        return drifted
                return documents[str(path)]

            globals_.update(replacements)
            globals_["_operation_recovery_read_private_json"] = (
                read_with_prior_drift
            )
            try:
                with self.assertRaisesRegex(Exception, "invalid|differs"):
                    command(args)
            finally:
                globals_.update(originals)
            self.assertEqual(prior_reads, 2)
            self.assertEqual(written, {})

    def test_exact_drain_recovery_handoff_projects_epoch_two_context(self):
        helper = self.controller[
            "_operation_recovery_exact_recovery_context"
        ]
        globals_ = helper.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-epoch-two-handoff-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            recovery, snapshot, documents, recovery_path = (
                self._schema_10_recovery_handoff(root)
            )
            recovery = deepcopy(recovery)
            recovery["schema_version"] = 11
            recovery["retry_recovery"] = {
                **recovery["retry_recovery"],
                "schema_version": 2,
                "recovery_epoch_before": 1,
                "recovery_epoch_after": 2,
                "recovery_epoch_ceiling": 2,
            }
            selected_ids = {
                item["operation_id"]
                for item in recovery["selected_operations"]
            }
            preserved_row = next(
                item
                for item in snapshot["operations"]
                if item["operation_id"] not in selected_ids
                and item["current_status"] == "pending"
            )
            retry_template = recovery["retry_recovery"]["operations"][0]
            recovery["retry_recovery"]["operations"].append(
                {
                    **retry_template,
                    "operation_id": preserved_row["operation_id"],
                    "expected_status": preserved_row["current_status"],
                    "retry_count_before": preserved_row["retry_count"],
                    "retry_count_after": preserved_row["retry_count"],
                    "reset_applied": False,
                }
            )
            application = documents[recovery["application_receipt_path"]]
            verification = documents[
                recovery["verification_receipt_path"]
            ]
            successor_candidate = {
                **recovery["candidate_release"],
                "source_commit": "6" * 40,
                "version": "2026.08.27+6666666.operation-recovery.104",
                "release_digest": "7" * 64,
            }
            replacements = {
                "verify_post_abort_recovery_plan": (
                    lambda _value, *, allow_expired: recovery
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_validate_application": (
                    lambda _value, *, plan: application
                ),
                "_operation_recovery_post_abort_validate_verification": (
                    lambda _value, *, plan, application, authority: verification
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                context = helper(
                    SimpleNamespace(recovery_plan=str(recovery_path)),
                    snapshot=snapshot,
                    candidate_release=recovery["candidate_release"],
                )
                successor_context = helper(
                    SimpleNamespace(recovery_plan=str(recovery_path)),
                    snapshot=snapshot,
                    candidate_release=successor_candidate,
                )
                rebound_generation_snapshot = deepcopy(snapshot)
                rebound_generation_snapshot["generation_before"] = (
                    "systalyze:public:999"
                )
                rebound_generation_snapshot["generation_after"] = (
                    "systalyze:public:999"
                )
                with self.assertRaisesRegex(
                    Exception,
                    "recovery handoff differs",
                ):
                    helper(
                        SimpleNamespace(recovery_plan=str(recovery_path)),
                        snapshot=rebound_generation_snapshot,
                        candidate_release=successor_candidate,
                    )
                drifted_snapshot = deepcopy(snapshot)
                selected_row = next(
                    item
                    for item in drifted_snapshot["operations"]
                    if item["operation_id"] in selected_ids
                )
                selected_row["task_payload_digest"] = "8" * 64
                with self.assertRaisesRegex(
                    Exception,
                    "recovery handoff differs",
                ):
                    helper(
                        SimpleNamespace(recovery_plan=str(recovery_path)),
                        snapshot=drifted_snapshot,
                        candidate_release=successor_candidate,
                    )
                missing_selected_retry = recovery["retry_recovery"][
                    "operations"
                ].pop(0)
                try:
                    with self.assertRaisesRegex(
                        Exception,
                        "recovery handoff differs",
                    ):
                        helper(
                            SimpleNamespace(
                                recovery_plan=str(recovery_path)
                            ),
                            snapshot=snapshot,
                            candidate_release=recovery["candidate_release"],
                        )
                finally:
                    recovery["retry_recovery"]["operations"].insert(
                        0,
                        missing_selected_retry,
                    )
                drifted_lineage_snapshot = deepcopy(snapshot)
                drifted_lineage_row = next(
                    item
                    for item in drifted_lineage_snapshot["operations"]
                    if item["operation_id"]
                    == preserved_row["operation_id"]
                )
                drifted_lineage_row["retry_count"] += 1
                with self.assertRaisesRegex(
                    Exception,
                    "recovery handoff differs",
                ):
                    helper(
                        SimpleNamespace(recovery_plan=str(recovery_path)),
                        snapshot=drifted_lineage_snapshot,
                        candidate_release=recovery["candidate_release"],
                    )
            finally:
                globals_.update(originals)

            self.assertEqual(context["schema_version"], 2)
            self.assertEqual(context["recovery_epoch"], 2)
            self.assertEqual(
                context["post_abort_plan_digest"],
                recovery["plan_digest"],
            )
            self.assertEqual(
                successor_context["candidate_release_digest"],
                successor_candidate["release_digest"],
            )

    def test_post_terminal_handoff_allows_approved_candidate_repair(self):
        helper = self.controller[
            "_operation_recovery_exact_recovery_context"
        ]
        globals_ = helper.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-candidate-repair-handoff-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            recovery, snapshot, documents, recovery_path = (
                self._schema_10_recovery_handoff(root)
            )
            recovery = deepcopy(recovery)
            recovery.update(
                schema_version=13,
                reference_application_receipt_digest="1" * 64,
                reference_terminal_status_digest="2" * 64,
            )
            snapshot = deepcopy(snapshot)
            snapshot["generation_before"] = "systalyze:public:999"
            snapshot["generation_after"] = "systalyze:public:999"
            application = documents[recovery["application_receipt_path"]]
            verification = documents[
                recovery["verification_receipt_path"]
            ]
            repaired_candidate = {
                **recovery["candidate_release"],
                "source_commit": "6" * 40,
                "version": "2026.08.21+6666666.operation-recovery.55",
                "release_digest": "7" * 64,
            }
            replacements = {
                "verify_post_abort_recovery_plan": (
                    lambda _value, *, allow_expired: recovery
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_validate_application": (
                    lambda _value, *, plan: application
                ),
                "_operation_recovery_post_abort_validate_verification": (
                    lambda _value, *, plan, application, authority: verification
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                context = helper(
                    SimpleNamespace(recovery_plan=str(recovery_path)),
                    snapshot=snapshot,
                    candidate_release=repaired_candidate,
                )
                drifted_snapshot = deepcopy(snapshot)
                selected_id = recovery["selected_operations"][0][
                    "operation_id"
                ]
                selected_row = next(
                    item
                    for item in drifted_snapshot["operations"]
                    if item["operation_id"] == selected_id
                )
                selected_row["task_payload_digest"] = "8" * 64
                with self.assertRaisesRegex(
                    Exception,
                    "recovery handoff differs",
                ):
                    helper(
                        SimpleNamespace(recovery_plan=str(recovery_path)),
                        snapshot=drifted_snapshot,
                        candidate_release=repaired_candidate,
                    )
                recovery["schema_version"] = 12
                with self.assertRaisesRegex(
                    Exception,
                    "recovery handoff differs",
                ):
                    helper(
                        SimpleNamespace(recovery_plan=str(recovery_path)),
                        snapshot=snapshot,
                        candidate_release=repaired_candidate,
                    )
            finally:
                globals_.update(originals)

            self.assertEqual(context["schema_version"], 4)
            self.assertEqual(context["recovery_epoch"], 3)
            self.assertEqual(context["reconciliation_cycle"], 1)
            self.assertEqual(
                context["candidate_release_digest"],
                repaired_candidate["release_digest"],
            )
            self.assertEqual(
                context["post_terminal_reconciliation_plan_digest"],
                recovery["plan_digest"],
            )

    def test_preexecution_candidate_repair_reuses_epoch_without_row_activity(self):
        helper = self.controller[
            "_operation_recovery_exact_candidate_repair_context"
        ]
        globals_ = helper.__globals__
        old_candidate = {
            "source_commit": "1" * 40,
            "version": "2026.08.24+1111111.operation-recovery.70",
            "release_digest": "2" * 64,
        }
        new_candidate = {
            "source_commit": "3" * 40,
            "version": "2026.08.24+3333333.operation-recovery.71",
            "release_digest": "4" * 64,
        }
        row = {
            "operation_id": "retain-1",
            "operation_type": "retain",
            "row_digest": "5" * 64,
            "current_status": "pending",
        }
        snapshot = {
            "cohort_digest": "6" * 64,
            "installation_authority": {"authority": "stable"},
            "generation_before": "systalyze:public:7",
            "generation_after": "systalyze:public:7",
            "status_counts": {"pending": 1},
            "operations": [row],
        }
        context = {
            "schema_version": 2,
            "origin": "post-abort",
            "recovery_epoch": 2,
            "candidate_release_digest": old_candidate["release_digest"],
            "post_abort_selected_operation_ids_digest": "8" * 64,
            "post_abort_plan_digest": "9" * 64,
            "post_abort_application_receipt_digest": "a" * 64,
            "post_abort_verification_receipt_digest": "b" * 64,
            "retry_recovery_digest": "c" * 64,
            "selected_checkpoint_set_digest": "d" * 64,
            "preserved_row_set_digest": "e" * 64,
        }
        reference_plan = {
            "schema_version": 12,
            "candidate_release": old_candidate,
            "recovery_context": context,
            "authorization_receipt_path": "authorization.json",
            "application_receipt_path": "application.json",
            "progress_artifact_path": "progress.json",
            "selected_operations": [
                {
                    "operation_id": row["operation_id"],
                    "operation_type": row["operation_type"],
                    "row_digest": row["row_digest"],
                }
            ],
            "live_snapshot": snapshot,
            "cohort_digest": snapshot["cohort_digest"],
            "installation_authority": snapshot[
                "installation_authority"
            ],
            "pre_generation": snapshot["generation_before"],
            "selected_operation_count": 1,
            "worker_max_attempts": 1,
            "plan_digest": "f" * 64,
        }
        journal = {
            "worker_pid": 123,
            "worker_start_time": "darwin:7:8",
            "worker_attempt": 1,
        }
        progress = {
            "worker_pid": 123,
            "worker_start_time": "darwin:7:8",
            "worker_attempt": 1,
            "worker_status": "failed",
            "worker_stage": "failed",
            "worker_failure_stage": "worker.imports",
            "worker_failure": {
                "category": "worker_initialization",
                "retryable": False,
            },
            "worker_exit_code": 2,
            "active_provider_requests": [],
            "provider_counters": [],
            "prior_attempts": [],
            "cooldowns": [],
            "selected_status_counts": {"pending": 1},
            "tasks": [
                {
                    "operation_id": row["operation_id"],
                    "operation_type": row["operation_type"],
                    "row_digest": row["row_digest"],
                    "status": "pending",
                    "stage": "queued",
                    "checkpoint": None,
                    "failure": None,
                    "failure_stage": None,
                }
            ],
        }
        replacements = {
            "verify_exact_drain_plan": (
                lambda _value, *, allow_expired: reference_plan
            ),
            "_operation_recovery_read_private_json": (
                lambda _path, _label: reference_plan
            ),
            "_operation_recovery_post_abort_reference_artifacts": (
                lambda _plan: ({}, journal)
            ),
            "_operation_recovery_exact_journal_worker_active": (
                lambda _journal: False
            ),
            "read_exact_drain_progress": (
                lambda _path, *, plan_digest, progress_schema_version: progress
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            repaired = helper(
                snapshot=snapshot,
                candidate_release=new_candidate,
                reference_plan_path="reference.json",
            )
            self.assertEqual(repaired["schema_version"], 2)
            self.assertEqual(repaired["recovery_epoch"], 2)
            self.assertEqual(
                repaired["candidate_release_digest"],
                new_candidate["release_digest"],
            )
            progress["active_provider_requests"] = [{"request": "x"}]
            with self.assertRaisesRegex(
                Exception,
                "candidate-repair execution evidence differs",
            ):
                helper(
                    snapshot=snapshot,
                    candidate_release=new_candidate,
                    reference_plan_path="reference.json",
                )
        finally:
            globals_.update(originals)

    def test_schema_15_candidate_repair_accepts_quiescent_claim_release(self):
        helper = self.controller[
            "_operation_recovery_exact_candidate_repair_context"
        ]
        globals_ = helper.__globals__
        old_candidate = {
            "source_commit": "1" * 40,
            "version": "2026.08.25+1111111.operation-recovery.85",
            "release_digest": "2" * 64,
        }
        new_candidate = {
            "source_commit": "3" * 40,
            "version": "2026.08.25+3333333.operation-recovery.86",
            "release_digest": "4" * 64,
        }
        before = {
            "operation_id": "retain-1",
            "operation_type": "retain",
            "row_digest": "5" * 64,
            "task_payload_digest": "6" * 64,
            "result_metadata_digest": "7" * 64,
            "current_status": "pending",
            "retry_count": 0,
            "worker_id_present": False,
            "worker_id_digest": None,
            "claimed_at": None,
            "next_retry_at": None,
            "error_category": "none",
            "error_digest": None,
        }
        after = {
            **before,
            "row_digest": "8" * 64,
            "retry_count": 1,
            "next_retry_at": "2026-08-25T18:40:00+00:00",
            "error_category": "provider_transport",
            "error_digest": "a" * 64,
        }
        unchanged_pending = {
            **before,
            "operation_id": "retain-2",
            "row_digest": "b" * 64,
            "retry_count": 2,
            "next_retry_at": "2026-08-25T17:40:00+00:00",
            "error_category": "unknown",
            "error_digest": "c" * 64,
        }
        snapshot = {
            "cohort_digest": "9" * 64,
            "installation_authority": {"authority": "stable"},
            "generation_before": "systalyze:public:12",
            "generation_after": "systalyze:public:12",
            "status_counts": {"pending": 2},
            "operations": [after, unchanged_pending],
        }
        context = {
            "schema_version": 4,
            "origin": "post-terminal-reconciliation",
            "recovery_epoch": 3,
            "reconciliation_cycle": 1,
            "candidate_release_digest": old_candidate["release_digest"],
            "post_terminal_reconciliation_plan_digest": "a" * 64,
            "post_terminal_reconciliation_application_receipt_digest": (
                "b" * 64
            ),
            "post_terminal_reconciliation_verification_receipt_digest": (
                "c" * 64
            ),
            "terminal_plan_digest": "d" * 64,
            "terminal_authorization_receipt_digest": "e" * 64,
            "terminal_application_receipt_digest": "f" * 64,
            "terminal_progress_digest": "0" * 64,
            "terminal_status_digest": "1" * 64,
            "retry_recovery_digest": "2" * 64,
            "selected_checkpoint_set_digest": "3" * 64,
            "preserved_row_set_digest": "4" * 64,
            "initial_origin_digest": None,
        }
        reference_plan = {
            "schema_version": 15,
            "candidate_release": old_candidate,
            "recovery_context": context,
            "authorization_receipt_path": "authorization.json",
            "application_receipt_path": "application.json",
            "progress_artifact_path": "progress.json",
            "selected_operations": [
                {
                    "operation_id": before["operation_id"],
                    "operation_type": before["operation_type"],
                    "row_digest": before["row_digest"],
                },
                {
                    "operation_id": unchanged_pending["operation_id"],
                    "operation_type": unchanged_pending["operation_type"],
                    "row_digest": unchanged_pending["row_digest"],
                },
            ],
            "live_snapshot": {
                **snapshot,
                "generation_before": "systalyze:public:10",
                "generation_after": "systalyze:public:10",
                "operations": [before, unchanged_pending],
            },
            "cohort_digest": snapshot["cohort_digest"],
            "installation_authority": snapshot["installation_authority"],
            "pre_generation": "systalyze:public:10",
            "selected_operation_count": 2,
            "worker_max_attempts": 1,
            "progress_schema_version": 6,
            "plan_digest": "5" * 64,
        }
        journal = {
            "worker_pid": 123,
            "worker_start_time": "darwin:7:8",
            "worker_attempt": 1,
        }
        progress = {
            **journal,
            "worker_status": "running",
            "worker_stage": "worker.main",
            "worker_failure_stage": None,
            "worker_failure": None,
            "worker_exit_code": None,
            "active_provider_requests": [],
            "provider_counters": [],
            "prior_attempts": [],
            "cooldowns": [],
            "selected_status_counts": {"pending": 1, "processing": 1},
            "tasks": [
                {
                    "operation_id": before["operation_id"],
                    "operation_type": before["operation_type"],
                    "row_digest": before["row_digest"],
                    "status": "processing",
                    "stage": "batch_retain.sub_batch.1",
                    "checkpoint": None,
                    "failure": None,
                    "failure_stage": None,
                },
                {
                    "operation_id": unchanged_pending["operation_id"],
                    "operation_type": unchanged_pending["operation_type"],
                    "row_digest": unchanged_pending["row_digest"],
                    "status": "pending",
                    "stage": "queued",
                    "checkpoint": None,
                    "failure": None,
                    "failure_stage": None,
                },
            ],
        }
        replacements = {
            "verify_exact_drain_plan": (
                lambda _value, *, allow_expired: reference_plan
            ),
            "_operation_recovery_read_private_json": (
                lambda _path, _label: reference_plan
            ),
            "_operation_recovery_post_abort_reference_artifacts": (
                lambda _plan: ({}, journal)
            ),
            "_operation_recovery_exact_journal_worker_active": (
                lambda _journal: False
            ),
            "read_exact_drain_progress": (
                lambda _path, *, plan_digest, progress_schema_version: progress
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            repaired = helper(
                snapshot=snapshot,
                candidate_release=new_candidate,
                reference_plan_path="reference.json",
            )
            self.assertEqual(repaired["schema_version"], 4)
            self.assertEqual(repaired["recovery_epoch"], 3)
            self.assertEqual(repaired["reconciliation_cycle"], 1)
            self.assertEqual(
                repaired["candidate_release_digest"],
                new_candidate["release_digest"],
            )
            progress["provider_counters"] = [{"provider": "hatchery"}]
            with self.assertRaisesRegex(
                Exception,
                "candidate-repair execution evidence differs",
            ):
                helper(
                    snapshot=snapshot,
                    candidate_release=new_candidate,
                    reference_plan_path="reference.json",
                )
        finally:
            globals_.update(originals)

    def test_schema_15_candidate_repair_accepts_interrupted_idle_poller(self):
        helper = self.controller[
            "_operation_recovery_exact_candidate_repair_context"
        ]
        globals_ = helper.__globals__
        old_candidate = {
            "source_commit": "1" * 40,
            "version": "2026.08.27+1111111.operation-recovery.107",
            "release_digest": "2" * 64,
        }
        new_candidate = {
            "source_commit": "3" * 40,
            "version": "2026.08.27+3333333.operation-recovery.110",
            "release_digest": "4" * 64,
        }
        row = {
            "operation_id": "retain-1",
            "operation_type": "retain",
            "row_digest": "5" * 64,
            "current_status": "pending",
        }
        reference_snapshot = {
            "cohort_digest": "6" * 64,
            "installation_authority": {"authority": "stable"},
            "generation_before": "systalyze:public:10",
            "generation_after": "systalyze:public:10",
            "status_counts": {"pending": 1},
            "operations": [row],
        }
        snapshot = {
            **reference_snapshot,
            "generation_before": "systalyze:public:11",
            "generation_after": "systalyze:public:11",
        }
        context = {
            "schema_version": 2,
            "origin": "post-abort",
            "recovery_epoch": 2,
            "candidate_release_digest": old_candidate["release_digest"],
        }
        reference_plan = {
            "schema_version": 15,
            "candidate_release": old_candidate,
            "recovery_context": context,
            "authorization_receipt_path": "authorization.json",
            "application_receipt_path": "application.json",
            "progress_artifact_path": "progress.json",
            "status_artifact_path": "status.json",
            "selected_operations": [
                {
                    "operation_id": row["operation_id"],
                    "operation_type": row["operation_type"],
                    "row_digest": row["row_digest"],
                }
            ],
            "live_snapshot": reference_snapshot,
            "cohort_digest": reference_snapshot["cohort_digest"],
            "installation_authority": reference_snapshot[
                "installation_authority"
            ],
            "pre_generation": reference_snapshot["generation_before"],
            "preserved_status_counts": {"completed": 13, "failed": 3},
            "selected_operation_count": 1,
            "worker_max_attempts": 1,
            "progress_schema_version": 6,
            "plan_digest": "7" * 64,
        }
        journal = {
            "worker_pid": 123,
            "worker_start_time": "darwin:7:8",
            "worker_attempt": 1,
        }
        progress = {
            **journal,
            "worker_status": "running",
            "worker_stage": "worker.poller.running",
            "worker_failure_stage": None,
            "worker_failure": None,
            "worker_exit_code": None,
            "active_provider_requests": [],
            "provider_counters": [],
            "prior_attempts": [],
            "cooldowns": [],
            "selected_status_counts": {"pending": 1},
            "tasks": [
                {
                    "operation_id": row["operation_id"],
                    "operation_type": row["operation_type"],
                    "row_digest": row["row_digest"],
                    "status": "pending",
                    "stage": "queued",
                    "checkpoint": None,
                    "failure": None,
                    "failure_stage": None,
                }
            ],
        }
        interrupted_status = {
            "generation_before": snapshot["generation_before"],
            "generation_after": snapshot["generation_after"],
            "selected_status_counts": {"pending": 1},
            "preserved_status_counts": reference_plan[
                "preserved_status_counts"
            ],
            "outside_nonterminal_counts": [],
        }
        replacements = {
            "verify_exact_drain_plan": (
                lambda _value, *, allow_expired: reference_plan
            ),
            "_operation_recovery_read_private_json": (
                lambda _path, _label: reference_plan
            ),
            "_operation_recovery_post_abort_reference_artifacts": (
                lambda _plan: ({}, journal)
            ),
            "_operation_recovery_exact_journal_worker_active": (
                lambda _journal: False
            ),
            "read_exact_drain_progress": (
                lambda _path, *, plan_digest, progress_schema_version: progress
            ),
            "verify_exact_drain_status": (
                lambda _value, *, plan: interrupted_status
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            repaired = helper(
                snapshot=snapshot,
                candidate_release=new_candidate,
                reference_plan_path="reference.json",
            )
            self.assertEqual(repaired["schema_version"], 2)
            self.assertEqual(repaired["recovery_epoch"], 2)
            self.assertEqual(
                repaired["candidate_release_digest"],
                new_candidate["release_digest"],
            )
            progress["active_provider_requests"] = [{"request": "x"}]
            with self.assertRaisesRegex(
                Exception,
                "candidate-repair execution evidence differs",
            ):
                helper(
                    snapshot=snapshot,
                    candidate_release=new_candidate,
                    reference_plan_path="reference.json",
                )
            progress["active_provider_requests"] = []
            interrupted_status["generation_before"] = "systalyze:public:12"
            with self.assertRaisesRegex(
                Exception,
                "candidate-repair row evidence differs",
            ):
                helper(
                    snapshot=snapshot,
                    candidate_release=new_candidate,
                    reference_plan_path="reference.json",
                )
        finally:
            globals_.update(originals)

    def test_exact_drain_plan_command_hands_off_verified_recovery_sources(self):
        command = self.controller["operation_recovery_drain_plan_command"]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-recovery-handoff-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            (
                recovery_plan,
                recovered_snapshot,
                documents,
                recovery_plan_source,
            ) = self._schema_10_recovery_handoff(root)
            self.assertEqual(
                recovery_plan["selected_status_counts"],
                {"failed": 22, "processing": 1},
            )
            rollback_path = root / "fresh-backup.age"
            rollback_path.write_bytes(b"synthetic-fresh-backup")
            rollback_path.chmod(0o600)
            backup = recovery_fixtures.drain_backup_evidence()
            backup["artifact_sha256"] = hashlib.sha256(
                rollback_path.read_bytes()
            ).hexdigest()
            for key in ("generation_before", "generation_after"):
                backup["source_authority"][key] = recovered_snapshot[
                    "generation_before"
                ]
            backup["source_authority_digest"] = self.controller["digest"](
                backup["source_authority"]
            )
            documents.update(
                {
                    "cohort": fixtures.cohort(),
                    "snapshot": recovered_snapshot,
                    "backup": backup,
                }
            )
            written = {}
            candidate = recovery_fixtures.release_identity()
            replacements = {
                "_operation_recovery_candidate": lambda _args: candidate,
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    lambda _path: ("9" * 64, object())
                ),
                "_operation_recovery_profile_environment": lambda: {},
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: "7" * 64
                ),
                "_operation_recovery_exact_phase_repair_snapshot": (
                    lambda: "7" * 64
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, *, schema_version=10: "8" * 64
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "write_private": (
                    lambda path, value, *, create_only: written.update(
                        {str(path): (value, create_only)}
                    )
                ),
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                cohort="cohort",
                snapshot="snapshot",
                rollback_backup_evidence="backup",
                rollback_backup=str(rollback_path),
                provider_policy=str(root / "providers.json"),
                provider_runtime_root=str(root / "provider-runtime"),
                worker_runtime=str(root / "hindsight-worker"),
                authorization_receipt=str(root / "fresh-authorization.json"),
                application_receipt=str(root / "fresh-application.json"),
                status_artifact=str(root / "fresh-exact-status.json"),
                verification_receipt=str(root / "fresh-verification.json"),
                recovery_plan=str(recovery_plan_source),
                output=str(root / "fresh-plan.json"),
            )
            try:
                result = command(args)
                self.assertEqual(result["status"], "planned")
                self.assertEqual(result["recovery_origin"], "post-abort")
                self.assertEqual(result["recovery_epoch"], 1)
                fresh_plan, create_only = written[args.output]
                self.assertIs(create_only, True)
                self.assertEqual(
                    fresh_plan["recovery_context"]["post_abort_plan_digest"],
                    recovery_plan["plan_digest"],
                )
                self.assertEqual(
                    fresh_plan["recovery_context"][
                        "post_abort_application_receipt_digest"
                    ],
                    documents[recovery_plan["application_receipt_path"]][
                        "receipt_digest"
                    ],
                )
                self.assertEqual(
                    fresh_plan["recovery_context"][
                        "post_abort_verification_receipt_digest"
                    ],
                    documents[recovery_plan["verification_receipt_path"]][
                        "receipt_digest"
                    ],
                )

                recovery_sources = [
                    args.recovery_plan,
                    *(
                        value
                        for key, value in recovery_plan.items()
                        if key.endswith("_path") and isinstance(value, str)
                    ),
                    *(
                        value
                        for key, value in recovery_plan[
                            "reference_plan"
                        ].items()
                        if key.endswith("_path") and isinstance(value, str)
                    ),
                    *(
                        str(
                            self.controller[
                                "exact_drain_progress_archive_path"
                            ](
                                Path(
                                    recovery_plan["reference_plan"][
                                        "progress_artifact_path"
                                    ]
                                ),
                                attempt,
                            )
                        )
                        for attempt in range(
                            1,
                            recovery_plan["reference_plan"][
                                "worker_max_attempts"
                            ]
                            + 1,
                        )
                    ),
                ]
                for source_path in recovery_sources:
                    with self.subTest(source_path=source_path):
                        alias_args = SimpleNamespace(**vars(args))
                        alias_args.output = source_path
                        written.clear()
                        with self.assertRaisesRegex(
                            Exception,
                            "plan path aliases an artifact",
                        ):
                            command(alias_args)
                        self.assertEqual(written, {})

                derived_alias_args = SimpleNamespace(**vars(args))
                derived_alias_args.status_artifact = str(
                    root / "fresh-status.json"
                )
                derived_alias_args.output = str(root / "derived-plan.json")
                written.clear()
                with self.assertRaisesRegex(
                    Exception,
                    "plan path aliases an artifact",
                ):
                    command(derived_alias_args)
                self.assertEqual(written, {})

                drifted_recovery_plan = deepcopy(recovery_plan)
                drifted_recovery_plan["rollback_receipt_path"] = str(
                    root / "drifted-recovery-rollback.json"
                )
                drifted_recovery_plan["plan_digest"] = self.controller[
                    "digest"
                ](
                    {
                        key: value
                        for key, value in drifted_recovery_plan.items()
                        if key != "plan_digest"
                    }
                )
                recovery_plan_reads = 0

                def read_with_recovery_plan_drift(path, _label):
                    nonlocal recovery_plan_reads
                    if str(path) == str(recovery_plan_source):
                        recovery_plan_reads += 1
                        return (
                            recovery_plan
                            if recovery_plan_reads <= 2
                            else drifted_recovery_plan
                        )
                    return documents[str(path)]

                globals_["_operation_recovery_read_private_json"] = (
                    read_with_recovery_plan_drift
                )
                written.clear()
                with self.assertRaisesRegex(
                    Exception,
                    "recovery handoff differs",
                ):
                    command(args)
                self.assertEqual(recovery_plan_reads, 3)
                self.assertEqual(written, {})

                application_path = recovery_plan[
                    "application_receipt_path"
                ]
                verification_path = recovery_plan[
                    "verification_receipt_path"
                ]
                drifted_application = deepcopy(documents[application_path])
                drifted_application["applied_at"] += 1
                drifted_application["receipt_digest"] = self.controller[
                    "_operation_recovery_receipt_digest"
                ](drifted_application)
                drifted_verification = deepcopy(
                    documents[verification_path]
                )
                drifted_verification["application_receipt_digest"] = (
                    drifted_application["receipt_digest"]
                )
                drifted_verification["receipt_digest"] = self.controller[
                    "_operation_recovery_receipt_digest"
                ](drifted_verification)
                application_reads = 0
                verification_reads = 0

                def read_with_receipt_drift(path, _label):
                    nonlocal application_reads, verification_reads
                    path_value = str(path)
                    if path_value == application_path:
                        application_reads += 1
                        return (
                            documents[application_path]
                            if application_reads == 1
                            else drifted_application
                        )
                    if path_value == verification_path:
                        verification_reads += 1
                        return (
                            documents[verification_path]
                            if verification_reads == 1
                            else drifted_verification
                        )
                    return documents[path_value]

                globals_["_operation_recovery_read_private_json"] = (
                    read_with_receipt_drift
                )
                written.clear()
                with self.assertRaisesRegex(
                    Exception,
                    "recovery handoff drifted during planning",
                ):
                    command(args)
                self.assertEqual(application_reads, 2)
                self.assertEqual(verification_reads, 2)
                self.assertEqual(written, {})
            finally:
                globals_.update(originals)

    def test_exact_drain_plan_requires_current_cooccurrence_snapshot(self):
        helper = self.controller[
            "_operation_recovery_exact_phase_repair_snapshot"
        ]
        globals_ = helper.__globals__
        original = globals_["verify_exact_drain_candidate_runtime_snapshot"]
        try:
            globals_["verify_exact_drain_candidate_runtime_snapshot"] = (
                lambda _library: (
                    {
                        "schema_version": 7,
                        "snapshot_digest": "a" * 64,
                    },
                    {},
                )
            )
            self.assertEqual(helper(), "a" * 64)
            globals_["verify_exact_drain_candidate_runtime_snapshot"] = (
                lambda _library: (
                    {
                        "schema_version": 5,
                        "snapshot_digest": "b" * 64,
                    },
                    {},
                )
            )
            with self.assertRaisesRegex(
                Exception,
                "full-query repair snapshot is required",
            ):
                helper()
        finally:
            globals_["verify_exact_drain_candidate_runtime_snapshot"] = original

    def test_exact_drain_plan_rejects_unpatched_legacy_candidate_snapshot(self):
        command = self.controller["operation_recovery_drain_plan_command"]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-legacy-snapshot-plan-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            candidate_library = root / "candidate-lib"
            provider_root = (
                candidate_library / "exact_drain_runtime" / "provider"
            )
            provider_root.mkdir(parents=True, mode=0o700)
            sources = []
            for name in ("sitecustomize.py", "hindsight_llm_failover.py"):
                body = f"# legacy {name}\n".encode()
                path = provider_root / name
                path.write_bytes(body)
                path.chmod(0o600)
                sources.append(
                    {
                        "path": f"provider/{name}",
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "size": len(body),
                    }
                )
            manifest = {
                "schema_version": 1,
                "kind": "exact-drain-candidate-runtime-snapshot",
                "sources": sources,
            }
            manifest_path = provider_root.parent / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
            manifest_path.chmod(0o600)
            documents = {
                "cohort": fixtures.cohort(),
                "snapshot": fixtures.drain_snapshot(
                    observed_at=int(time.time())
                ),
                "backup": recovery_fixtures.drain_backup_evidence(),
            }
            writes = []
            replacements = {
                "LIB": candidate_library,
                "_operation_recovery_candidate": (
                    lambda _args: recovery_fixtures.release_identity()
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    lambda _path: self.fail("provider policy was activated")
                ),
                "write_private": lambda *_args, **_kwargs: writes.append(True),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                cohort="cohort",
                snapshot="snapshot",
                rollback_backup_evidence="backup",
                rollback_backup=str(root / "rollback.age"),
                provider_policy=str(root / "providers.json"),
                provider_runtime_root=str(root / "provider-runtime"),
                worker_runtime=str(root / "hindsight-worker"),
                authorization_receipt=str(root / "authorization.json"),
                application_receipt=str(root / "application.json"),
                status_artifact=str(root / "status.json"),
                verification_receipt=str(root / "verification.json"),
                output=str(root / "plan.json"),
            )
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "full-query repair snapshot is required",
                ):
                    command(args)
            finally:
                globals_.update(originals)
            self.assertEqual(writes, [])

    def test_post_abort_plan_command_emits_invariant_v10_subset(self):
        command = self.controller[
            "operation_recovery_post_abort_plan_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        reference = fixtures.drain_plan(
            snapshot=fixtures.drain_snapshot(
                completed_positions={0, 1, 42, 43, 46, 47},
                observed_at=1_786_390_000,
            ),
            created_at=1_786_390_001,
        )
        snapshot = fixtures.post_abort_v9_snapshot(
            reference,
            observed_at=int(time.time()),
        )
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-v10-plan-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            rollback_path = root / "rollback.dump.age"
            rollback_path.write_bytes(b"synthetic-encrypted-backup")
            rollback_path.chmod(0o600)
            backup = recovery_fixtures.rollback_backup_evidence()
            backup["artifact_sha256"] = hashlib.sha256(
                rollback_path.read_bytes()
            ).hexdigest()
            backup["source_authority"]["generation_before"] = snapshot[
                "generation_before"
            ]
            backup["source_authority"]["generation_after"] = snapshot[
                "generation_after"
            ]
            backup["source_authority_digest"] = self.controller["digest"](
                backup["source_authority"]
            )
            authorization = recovery_fixtures.exact_drain_authorization(
                reference
            )
            journal = self.controller[
                "_operation_recovery_exact_receipt"
            ](
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-application-journal"
                    ),
                    "plan_digest": reference["plan_digest"],
                    "authorization_receipt_digest": authorization[
                        "receipt_digest"
                    ],
                    "started_at": authorization["authorized_at"],
                    "worker_pid": 4242,
                    "worker_start_time": "dead-exact-drain-worker",
                    "worker_attempt": 1,
                }
            )
            documents = {
                "reference": reference,
                "snapshot": snapshot,
                "backup": backup,
                reference["authorization_receipt_path"]: authorization,
                reference["application_receipt_path"]: journal,
            }
            candidate = recovery_fixtures.release_identity()
            encryption = recovery_fixtures.rollback_encryption()
            written = {}
            registration = {
                **backup["source_authority"]["binding"],
                "_password": "not-observable",
            }
            worker_active = Mock(return_value=False)
            progress_value = {
                "plan_digest": reference["plan_digest"],
                "progress_digest": "c" * 64,
                "worker_pid": journal["worker_pid"],
                "worker_start_time": journal["worker_start_time"],
                "worker_attempt": journal["worker_attempt"],
                "tasks": [
                    {
                        "operation_id": item["operation_id"],
                        "operation_type": item["operation_type"],
                        "row_digest": item["row_digest"],
                    }
                    for item in reference["selected_operations"]
                ],
            }
            replacements = {
                "_operation_recovery_candidate": lambda _args: candidate,
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_operation_recovery_tool": (
                    lambda path, _name: Path(path)
                ),
                "_operation_recovery_rollback_encryption": (
                    lambda _recipient: encryption
                ),
                "_operation_recovery_toolchain_digest": (
                    lambda: backup["toolchain_digest"]
                ),
                "_operation_recovery_exact_journal_worker_active": (
                    worker_active
                ),
                "read_exact_drain_progress": (
                    lambda _path, *, plan_digest, progress_schema_version=1: dict(
                        progress_value
                    )
                ),
                "read_pg0_registration": lambda _profile: dict(registration),
                "write_private": (
                    lambda path, value, *, create_only: written.update(
                        {str(path): (value, create_only)}
                    )
                ),
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                reference_plan="reference",
                snapshot="snapshot",
                rollback_backup_evidence="backup",
                rollback_backup=str(rollback_path),
                age=encryption["age_path"],
                rollback_recipient=encryption["recipient"],
                rollback_bundle=str(root / "bundle.age"),
                authorization_receipt=str(root / "authorization.json"),
                application_receipt=str(root / "application.json"),
                verification_receipt=str(root / "verification.json"),
                rollback_receipt=str(root / "rollback.json"),
                output=str(root / "plan.json"),
            )
            try:
                result = command(args)
            finally:
                globals_.update(originals)

            self.assertEqual(result["status"], "planned")
            self.assertEqual(result["authority"], "unapproved-plan")
            self.assertEqual(result["selected_operation_count"], 3)
            plan, create_only = written[args.output]
            self.assertIs(create_only, True)
            self.assertEqual(plan["schema_version"], 10)
            self.assertEqual(
                plan["selection_contract_digest"],
                recovery_fixtures.recovery_contract.POST_ABORT_V10_SELECTION_CONTRACT_DIGEST,
            )
            self.assertEqual(
                plan["preserved_status_counts"],
                {"completed": 7, "pending": 38},
            )
            self.assertEqual(
                plan["reference_application_authorization"],
                authorization,
            )
            self.assertEqual(
                plan["reference_application_authorization_digest"],
                authorization["receipt_digest"],
            )
            self.assertEqual(plan["reference_application_journal"], journal)
            self.assertEqual(
                plan["reference_application_journal_digest"],
                journal["receipt_digest"],
            )
            self.assertEqual(
                plan["reference_application_progress_digest"],
                "c" * 64,
            )
            self.assertEqual(
                plan["selected_status_counts"],
                {"failed": 1, "pending": 1, "processing": 1},
            )
            self.assertEqual(
                plan["selected_type_counts"],
                {"retain": 3},
            )
            self.assertEqual(
                plan["preserved_status_counts"],
                {"completed": 7, "pending": 38},
            )
            serialized = json.dumps(plan, sort_keys=True)
            self.assertNotIn('"task_payload":', serialized)
            self.assertNotIn('"worker_id":', serialized)
            self.assertNotIn('"error_message":', serialized)

            rebound_snapshot = deepcopy(snapshot)
            rebound_snapshot["installation_authority"] = (
                recovery_fixtures.rebound_installation_authority()
            )
            rebound_snapshot["snapshot_digest"] = self.controller["digest"](
                {
                    key: value
                    for key, value in rebound_snapshot.items()
                    if key != "snapshot_digest"
                }
            )
            rebound_backup = deepcopy(backup)
            rebound_backup["source_authority"]["data_identity_digest"] = (
                rebound_snapshot["installation_authority"][
                    "observed_data_identity_digest"
                ]
            )
            rebound_backup["source_authority_digest"] = self.controller[
                "digest"
            ](rebound_backup["source_authority"])
            documents["snapshot"] = rebound_snapshot
            documents["backup"] = rebound_backup
            written.clear()
            globals_.update(replacements)
            try:
                rebound_result = command(args)
            finally:
                globals_.update(originals)
                documents["snapshot"] = snapshot
                documents["backup"] = backup
            self.assertEqual(rebound_result["status"], "planned")
            rebound_plan, create_only = written[args.output]
            self.assertIs(create_only, True)
            self.assertEqual(rebound_plan["schema_version"], 11)
            self.assertEqual(
                rebound_plan["retry_recovery"]["schema_version"],
                1,
            )

            reference_sources = [
                args.reference_plan,
                *(
                    value
                    for key, value in reference.items()
                    if key.endswith("_path") and isinstance(value, str)
                ),
                *(
                    str(
                        self.controller["exact_drain_progress_archive_path"](
                            Path(reference["progress_artifact_path"]),
                            attempt,
                        )
                    )
                    for attempt in range(
                        1,
                        reference["worker_max_attempts"] + 1,
                    )
                ),
            ]
            for target, source in (
                ("output", reference_sources[0]),
                ("rollback_bundle", reference["verification_receipt_path"]),
                ("authorization_receipt", reference["status_artifact_path"]),
                ("application_receipt", reference_sources[-1]),
                ("verification_receipt", reference["rollback_backup_path"]),
                ("rollback_receipt", reference["progress_artifact_path"]),
            ):
                with self.subTest(reference_path_alias=target):
                    alias_args = SimpleNamespace(**vars(args))
                    setattr(alias_args, target, source)
                    written.clear()
                    globals_.update(replacements)
                    try:
                        with self.assertRaisesRegex(
                            Exception,
                            "post-abort plan path aliases an artifact",
                        ):
                            command(alias_args)
                    finally:
                        globals_.update(originals)
                    self.assertEqual(written, {})

            drifted_reference = deepcopy(reference)
            drifted_reference["verification_receipt_path"] = str(
                root / "drifted-reference-verification.json"
            )
            drifted_reference["plan_digest"] = self.controller["digest"](
                {
                    key: value
                    for key, value in drifted_reference.items()
                    if key != "plan_digest"
                }
            )
            reference_reads = 0

            def read_with_reference_drift(path, _label):
                nonlocal reference_reads
                if str(path) == args.reference_plan:
                    reference_reads += 1
                    return (
                        reference
                        if reference_reads == 1
                        else drifted_reference
                    )
                return documents[str(path)]

            written.clear()
            globals_.update(replacements)
            globals_["_operation_recovery_read_private_json"] = (
                read_with_reference_drift
            )
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "reference exact drain plan drifted",
                ):
                    command(args)
            finally:
                globals_.update(originals)
            self.assertEqual(reference_reads, 2)
            self.assertEqual(written, {})

            for label, changed_tasks in (
                (
                    "foreign",
                    [
                        {
                            **progress_value["tasks"][0],
                            "operation_id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
                        },
                        *progress_value["tasks"][1:],
                    ],
                ),
                ("depleted", progress_value["tasks"][:-1]),
            ):
                with self.subTest(progress=label):
                    progress_value["tasks"] = changed_tasks
                    progress_value["progress_digest"] = self.controller[
                        "digest"
                    ](
                        {
                            key: value
                            for key, value in progress_value.items()
                            if key != "progress_digest"
                        }
                    )
                    written.clear()
                    globals_.update(replacements)
                    try:
                        with self.assertRaisesRegex(
                            Exception,
                            "reference exact drain progress differs",
                        ):
                            command(args)
                    finally:
                        globals_.update(originals)
                    self.assertEqual(written, {})

            progress_value["tasks"] = [
                {
                    "operation_id": item["operation_id"],
                    "operation_type": item["operation_type"],
                    "row_digest": item["row_digest"],
                }
                for item in reference["selected_operations"]
            ]
            for field, value in (
                ("worker_pid", journal["worker_pid"] + 1),
                ("worker_start_time", "foreign-worker-start"),
                ("worker_attempt", journal["worker_attempt"] + 1),
            ):
                original = progress_value[field]
                progress_value[field] = value
                written.clear()
                globals_.update(replacements)
                try:
                    with (
                        self.subTest(progress_identity=field),
                        self.assertRaisesRegex(
                            Exception,
                            "reference exact drain progress differs",
                        ),
                    ):
                        command(args)
                finally:
                    globals_.update(originals)
                    progress_value[field] = original
                self.assertEqual(written, {})

            worker_active.return_value = True
            written.clear()
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "exact drain worker remains active",
                ):
                    command(args)
            finally:
                globals_.update(originals)
            self.assertEqual(written, {})

    def test_exact_drain_worker_environment_disables_global_maintenance(self):
        build = self.controller[
            "_operation_recovery_exact_worker_environment"
        ]
        inherited = {
            "KEEP_ME": "yes",
            "HOME": "/private/tmp/untrusted-home",
            "CODEX_HOME": "/private/tmp/codex-home",
            "HINDSIGHT_API_LLM_API_KEY": "provider-secret",
            "HINDSIGHT_API_LLM_1_BASE_URL": "https://provider.invalid",
            "HINDSIGHT_API_RETAIN_BATCH_ENABLED": "1",
            "HINDSIGHT_API_WORKER_CONSOLIDATION_MAX_SLOTS": "9",
            "HINDSIGHT_API_WORKER_FILE_CONVERT_RETAIN_MAX_SLOTS": "8",
            "HINDSIGHT_API_WORKER_GRAPH_MAINTENANCE_RESERVED_SLOTS": "7",
            "HINDSIGHT_API_OPERATION_RETENTION_DAYS": "7",
            "HINDSIGHT_API_AUDIT_LOG_RETENTION_DAYS": "7",
            "HINDSIGHT_API_LLM_TRACE_RETENTION_DAYS": "7",
            "HINDSIGHT_API_CONSOLIDATION_RECONCILE_INTERVAL_SECONDS": "60",
            "HINDSIGHT_API_MENTAL_MODEL_REFRESH_TICK_SECONDS": "60",
        }

        environment = build(
            inherited,
            database_url=(
                "postgresql://hindsight:secret@localhost/hindsight"
                "?host=%2Fprivate%2Ftmp&port=54329"
            ),
            start_gate_descriptor=9,
            plan_digest="a" * 64,
            authorization_receipt_digest="b" * 64,
        )

        self.assertNotIn("KEEP_ME", environment)
        self.assertEqual(environment["HOME"], str(Path.home()))
        self.assertFalse(
            any(
                key.endswith("_MAX_SLOTS")
                and key != "HINDSIGHT_API_WORKER_MAX_SLOTS"
                for key in environment
            )
        )
        self.assertEqual(
            environment["HINDSIGHT_API_WORKER_MAX_SLOTS"], "3"
        )
        self.assertEqual(
            environment[
                "HINDSIGHT_API_WORKER_CONSOLIDATION_RESERVED_SLOTS"
            ],
            "1",
        )
        self.assertEqual(
            environment["HINDSIGHT_API_WORKER_RETAIN_RESERVED_SLOTS"],
            "2",
        )
        for operation_type in (
            "FILE_CONVERT_RETAIN",
            "REFRESH_MENTAL_MODEL",
            "GRAPH_MAINTENANCE",
            "IMPORT_DOCUMENTS",
        ):
            self.assertEqual(
                environment[
                    f"HINDSIGHT_API_WORKER_{operation_type}_RESERVED_SLOTS"
                ],
                "0",
            )
        for key in (
            "HINDSIGHT_API_OPERATION_RETENTION_DAYS",
            "HINDSIGHT_API_AUDIT_LOG_RETENTION_DAYS",
            "HINDSIGHT_API_LLM_TRACE_RETENTION_DAYS",
            "HINDSIGHT_API_CONSOLIDATION_RECONCILE_INTERVAL_SECONDS",
            "HINDSIGHT_API_MENTAL_MODEL_REFRESH_TICK_SECONDS",
        ):
            self.assertEqual(environment[key], "0")
        self.assertEqual(environment["HINDSIGHT_API_RETAIN_LLM_TIMEOUT"], "3600")
        self.assertEqual(
            environment["HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT"], "2"
        )
        self.assertEqual(
            environment["HINDSIGHT_API_RETAIN_MAX_CONCURRENT"], "2"
        )
        self.assertEqual(
            environment["HINDSIGHT_API_RETAIN_BATCH_ENABLED"], "0"
        )
        self.assertEqual(
            environment["HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS"],
            "32768",
        )

        self.assertEqual(
            environment["HINDSIGHT_API_DATABASE_URL"],
            (
                "postgresql://hindsight:secret@localhost/hindsight"
                "?host=%2Fprivate%2Ftmp&port=54329"
            ),
        )
        self.assertEqual(
            environment["HINDSIGHT_EXACT_DRAIN_START_FD"],
            "9",
        )
        self.assertEqual(
            environment["HINDSIGHT_EXACT_DRAIN_PLAN_DIGEST"],
            "a" * 64,
        )
        self.assertEqual(
            environment[
                "HINDSIGHT_EXACT_DRAIN_AUTHORIZATION_RECEIPT_DIGEST"
            ],
            "b" * 64,
        )
        expected_text_encoding = f"0x{os.geteuid():X}:0x0:0x0"
        self.assertEqual(
            environment.get("__CF_USER_TEXT_ENCODING"),
            expected_text_encoding,
        )
        observed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os; print(os.environ['__CF_USER_TEXT_ENCODING'])",
            ],
            check=False,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        self.assertEqual(observed.returncode, 0, observed.stderr)
        self.assertEqual(observed.stdout.strip(), expected_text_encoding)

    def test_exact_drain_database_url_selects_verified_unix_socket(self):
        build = self.controller["_operation_recovery_exact_database_url"]
        globals_ = build.__globals__
        binding = {
            "pid": 123,
            "socket_dir": "/private/tmp/hindsight socket",
            "socket_path": (
                "/private/tmp/hindsight socket/.s.PGSQL.54329"
            ),
            "port": 54329,
            "user": "hindsight user",
            "database": "hindsight/database",
        }
        plan = {
            "rollback_backup": {
                "source_authority": {"binding": binding}
            }
        }
        replacements = {
            "read_pg0_registration": lambda _name: {
                **binding,
                "_password": "secret/word",
            },
            "normalize_pg0_binding": lambda registration, _label: dict(
                registration
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            observed = build(plan)
        finally:
            globals_.update(originals)

        self.assertEqual(
            observed,
            (
                "postgresql://hindsight%20user:secret%2Fword@/"
                "hindsight%2Fdatabase?"
                "host=%2Fprivate%2Ftmp%2Fhindsight%20socket&port=54329"
            ),
        )

    def test_exact_drain_worker_uses_the_venv_interpreter_path(self):
        resolve = self.controller[
            "_operation_recovery_exact_worker_interpreter"
        ]
        worker = (
            Path.home()
            / ".local/share/uv/tools/hindsight-api/bin/hindsight-worker"
        )
        interpreter = resolve(worker)
        self.assertEqual(interpreter, worker.parent / "python3")
        self.assertNotEqual(interpreter, interpreter.resolve(strict=True))
        result = subprocess.run(
            [str(interpreter), "-c", "import hindsight_api"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_drain_candidate_import_path_ignores_external_startup_hooks(self):
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-isolated-import-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            bin_dir = root / "bin"
            bin_dir.mkdir(mode=0o700)
            interpreter = bin_dir / "python3"
            interpreter.write_text("synthetic interpreter\n", encoding="utf-8")
            interpreter.chmod(0o700)
            worker = bin_dir / "hindsight-worker"
            worker.write_text(f"#!{interpreter}\n", encoding="utf-8")
            worker.chmod(0o700)
            (root / "pyvenv.cfg").write_text("home = /private/tmp\n")
            (root / "pyvenv.cfg").chmod(0o600)
            external = root / "lib" / "python3.13" / "site-packages"
            external.mkdir(parents=True, mode=0o700)
            marker = root / "startup-hook-ran"
            external_marker = root / "external-hindsight-loaded"
            (external / "sitecustomize.py").write_text(
                f"from pathlib import Path; Path({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            (external / "hostile.pth").write_text(
                f"import pathlib; pathlib.Path({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            external_package = external / "hindsight_api"
            external_package.mkdir(mode=0o700)
            (external_package / "__init__.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(external_marker)!r}).touch()\n",
                encoding="utf-8",
            )
            candidate = root / "candidate-lib"
            candidate_package = candidate / "hindsight_api"
            candidate_package.mkdir(parents=True, mode=0o700)
            (candidate_package / "__init__.py").write_text(
                "CANDIDATE = True\n",
                encoding="utf-8",
            )
            script = (
                "import importlib, pathlib; "
                "from tooling.hindsight.lib.hindsight_memory_control_plane "
                "import operation_recovery_runtime as runtime; "
                f"runtime.install_exact_drain_candidate_imports({str(worker)!r}, "
                f"{str(candidate)!r}); "
                "module = importlib.import_module('hindsight_api'); "
                "print(pathlib.Path(module.__file__).resolve())"
            )
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, "-S", "-c", script],
                check=False,
                cwd=str(Path(__file__).resolve().parents[3]),
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                Path(result.stdout.strip()),
                candidate_package / "__init__.py",
            )
            self.assertFalse(marker.exists())
            self.assertFalse(external_marker.exists())

    def test_exact_drain_candidate_import_path_rejects_preloaded_hindsight(self):
        install = getattr(
            operation_recovery_runtime,
            "install_exact_drain_candidate_imports",
        )
        previous = sys.modules.get("hindsight_api")
        sys.modules["hindsight_api"] = SimpleNamespace()
        try:
            with self.assertRaisesRegex(
                Exception,
                "Hindsight module was preloaded",
            ):
                install("/private/tmp/hindsight-worker", "/private/tmp/lib")
        finally:
            if previous is None:
                sys.modules.pop("hindsight_api", None)
            else:
                sys.modules["hindsight_api"] = previous

    def test_exact_drain_candidate_imports_reject_foreign_fallback_and_shadows(self):
        install = operation_recovery_runtime.install_exact_drain_candidate_imports
        validate_spec = (
            operation_recovery_runtime.validate_exact_drain_dependency_spec
        )
        validate_origins = (
            operation_recovery_runtime.validate_exact_drain_import_origins
        )
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-import-roots-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            bin_dir = root / "bin"
            bin_dir.mkdir(mode=0o700)
            interpreter = bin_dir / "python3"
            interpreter.write_text("synthetic interpreter\n", encoding="utf-8")
            interpreter.chmod(0o700)
            worker = bin_dir / "hindsight-worker"
            worker.write_text(f"#!{interpreter}\n", encoding="utf-8")
            worker.chmod(0o700)
            exact_worker = bin_dir / "hindsight-exact-drain-worker"
            exact_worker.write_text("synthetic exact worker\n", encoding="utf-8")
            exact_worker.chmod(0o700)
            (root / "pyvenv.cfg").write_text("home = /private/tmp\n")
            site_packages = root / "lib" / "python3.13" / "site-packages"
            dependency = site_packages / "exact_dependency"
            dependency.mkdir(parents=True, mode=0o700)
            (dependency / "__init__.py").write_text(
                "SOURCE = 'trusted'\n",
                encoding="utf-8",
            )
            candidate = root / "candidate-lib"
            (candidate / "hindsight_api").mkdir(parents=True, mode=0o700)
            (candidate / "hindsight_api" / "__init__.py").write_text(
                "",
                encoding="utf-8",
            )
            foreign = root / "foreign"
            (foreign / "exact_dependency").mkdir(parents=True, mode=0o700)
            foreign_source = foreign / "exact_dependency" / "__init__.py"
            foreign_source.write_text("SOURCE = 'foreign'\n", encoding="utf-8")
            original_path = list(sys.path)
            previous_foreign = sys.modules.get("foreign_exact_dependency")
            try:
                sys.path[:] = [str(foreign), *original_path]
                self.assertEqual(install(worker, candidate), site_packages)
                self.assertNotIn(str(foreign), sys.path)
                validate_spec("exact_dependency", worker)
                candidate_shadow = candidate / "exact_dependency.py"
                candidate_shadow.write_text(
                    "SOURCE = 'candidate-shadow'\n",
                    encoding="utf-8",
                )
                importlib.invalidate_caches()
                with self.assertRaisesRegex(Exception, "origin differs"):
                    validate_spec("exact_dependency", worker)
                sys.modules["foreign_exact_dependency"] = SimpleNamespace(
                    __file__=str(foreign_source)
                )
                with self.assertRaisesRegex(
                    Exception,
                    "loaded module origin differs",
                ):
                    validate_origins(worker, candidate)
            finally:
                sys.path[:] = original_path
                sys.modules.pop("exact_dependency", None)
                if previous_foreign is None:
                    sys.modules.pop("foreign_exact_dependency", None)
                else:
                    sys.modules["foreign_exact_dependency"] = previous_foreign

    def test_exact_drain_import_origins_accepts_only_real_mp_main_alias(self):
        worker = (
            Path.home()
            / ".local/share/uv/tools/hindsight-api/bin/hindsight-worker"
        )
        interpreter = worker.parent / "python3"
        if not worker.is_file() or not interpreter.is_file():
            self.skipTest("hindsight worker runtime is unavailable")
        candidate = ROOT / "lib"
        entrypoint = ROOT / "bin" / "hindsight-exact-drain-worker"

        def run(mode: str) -> subprocess.CompletedProcess[str]:
            script = textwrap.dedent(
                f"""
                import sys
                from types import SimpleNamespace
                sys.path.insert(0, {str(candidate)!r})
                from hindsight_memory_control_plane.operation_recovery_runtime import validate_exact_drain_import_origins
                main = sys.modules["__main__"]
                main.__file__ = {str(entrypoint)!r}
                if {mode!r} == "real":
                    import multiprocessing
                    assert sys.modules["__mp_main__"] is main
                elif {mode!r} == "forged":
                    sys.modules["__mp_main__"] = SimpleNamespace(
                        __file__={str(entrypoint)!r}
                    )
                else:
                    main.__file__ = {str(ROOT / "bin" / "hindsight-memory")!r}
                    sys.modules["__mp_main__"] = main
                validate_exact_drain_import_origins(
                    {str(worker)!r},
                    {str(candidate)!r},
                )
                """
            )
            return subprocess.run(
                [str(interpreter), "-S", "-c", script],
                check=False,
                capture_output=True,
                text=True,
                env={
                    "HOME": str(Path.home()),
                    "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )

        accepted = run("real")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        forged = run("forged")
        self.assertNotEqual(forged.returncode, 0)
        self.assertIn("loaded module origin differs", forged.stderr)
        wrong_entrypoint = run("wrong-entrypoint")
        self.assertNotEqual(wrong_entrypoint.returncode, 0)
        self.assertIn("loaded module origin differs", wrong_entrypoint.stderr)

    def test_exact_drain_policy_version_matches_the_worker_runtime(self):
        validate = self.controller[
            "_operation_recovery_validate_exact_worker_provider_runtime"
        ]
        worker = (
            Path.home()
            / ".local/share/uv/tools/hindsight-api/bin/hindsight-worker"
        )
        runtime_package = (
            Path.home()
            / ".local/share/uv/tools/hindsight-api/lib/python3.13/"
            "site-packages/hindsight_api"
        )
        policy_path = (
            Path.home()
            / ".config/hindsight-control-plane/provider-runtime-policy.json"
        )
        policy = self.controller["ProviderRuntimePolicy"].load(
            self.controller["strict_json_loads"](
                policy_path.read_text(encoding="utf-8")
            )
        )
        actual_version = self.controller[
            "exact_drain_worker_hindsight_version"
        ](worker, runtime_package)
        mismatched_version = (
            "0.0.0"
            if actual_version == policy.hindsight_version
            else policy.hindsight_version
        )

        globals_ = validate.__globals__
        original = globals_["exact_drain_worker_hindsight_version"]
        globals_["exact_drain_worker_hindsight_version"] = (
            lambda _worker, _runtime_package: actual_version
        )
        try:
            with self.assertRaisesRegex(
                Exception,
                "provider policy version differs from worker runtime",
            ):
                validate(
                    replace(policy, hindsight_version=mismatched_version),
                    worker,
                )
            validate(replace(policy, hindsight_version=actual_version), worker)
            globals_["exact_drain_worker_hindsight_version"] = (
                lambda _worker, _runtime_package: "99.0.0"
            )
            with self.assertRaisesRegex(
                Exception,
                "worker Hindsight version is unsupported",
            ):
                validate(
                    replace(policy, hindsight_version="99.0.0"),
                    worker,
                )
        finally:
            globals_["exact_drain_worker_hindsight_version"] = original

    def test_exact_drain_version_evidence_does_not_execute_the_worker(self):
        read_version = self.controller[
            "exact_drain_worker_hindsight_version"
        ]
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-version-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            bin_dir = root / "bin"
            bin_dir.mkdir(mode=0o700)
            worker = bin_dir / "hindsight-worker"
            interpreter = bin_dir / "python3"
            marker = root / "interpreter-ran"
            interpreter.write_text(
                "#!/bin/sh\n"
                f"touch {marker}\n"
                "printf '0.9.0\\n'\n",
                encoding="utf-8",
            )
            interpreter.chmod(0o700)
            worker.write_text(
                f"#!{interpreter}\n",
                encoding="utf-8",
            )
            worker.chmod(0o700)
            (root / "pyvenv.cfg").write_text("home = /private/tmp\n")
            (root / "pyvenv.cfg").chmod(0o600)
            metadata = (
                root
                / "lib"
                / "python3.13"
                / "site-packages"
                / "hindsight_api-0.9.0.dist-info"
                / "METADATA"
            )
            metadata.parent.mkdir(parents=True, mode=0o700)
            metadata.write_text(
                "Metadata-Version: 2.4\n"
                "Name: hindsight-api\n"
                "Version: 0.9.0\n",
                encoding="utf-8",
            )
            metadata.chmod(0o600)
            (metadata.parent.parent / "hindsight_api").mkdir(mode=0o700)

            runtime_package = metadata.parent.parent / "hindsight_api"
            self.assertEqual(
                read_version(worker, runtime_package),
                "0.9.0",
            )
            self.assertFalse(marker.exists())

    def test_exact_drain_runtime_binds_the_immutable_candidate_package_tree(self):
        runtime_digest = self.controller["exact_drain_runtime_digest"]
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-runtime-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            bin_dir = root / "bin"
            bin_dir.mkdir(mode=0o700)
            worker = bin_dir / "hindsight-worker"
            interpreter = bin_dir / "python3"
            interpreter.write_text("synthetic interpreter\n", encoding="utf-8")
            interpreter.chmod(0o700)
            worker.write_text(f"#!{interpreter}\n", encoding="utf-8")
            worker.chmod(0o700)
            (root / "pyvenv.cfg").write_text("home = /private/tmp\n")
            (root / "pyvenv.cfg").chmod(0o600)
            site_packages = root / "candidate-lib"
            metadata = (
                site_packages
                / "hindsight_api-0.9.0.dist-info"
                / "METADATA"
            )
            metadata.parent.mkdir(parents=True, mode=0o700)
            metadata.write_text(
                "Metadata-Version: 2.4\n"
                "Name: hindsight-api\n"
                "Version: 0.9.0\n",
                encoding="utf-8",
            )
            metadata.chmod(0o600)
            package = site_packages / "hindsight_api"
            package.mkdir(mode=0o700)
            source = package / "__init__.py"
            source.write_text("VERSION = 1\n", encoding="utf-8")
            source.chmod(0o600)
            resolver = _copy_patchable_entity_resolver(site_packages)
            original_resolver = resolver.read_bytes()
            provider_root = root / "provider-runtime"
            provider_root.mkdir(mode=0o700)
            for name in ("sitecustomize.py", "hindsight_llm_failover.py"):
                path = provider_root / name
                path.write_text(f"# synthetic {name}\n", encoding="utf-8")
                path.chmod(0o600)
            snapshot = (
                operation_recovery_runtime.
                assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    site_packages,
                )
            )
            self.assertRegex(snapshot["snapshot_digest"], r"^[0-9a-f]{64}$")
            self.assertEqual(snapshot["schema_version"], 8)
            patched_resolver = resolver.read_text(encoding="utf-8")
            self.assertNotEqual(resolver.read_bytes(), original_resolver)
            trigram_source = patched_resolver.split(
                "    async def _resolve_entities_batch_trigram(",
                1,
            )[1].split(
                "    async def _resolve_entities_batch_oracle_fuzzy(",
                1,
            )[0]
            full_source = patched_resolver.split(
                "    async def _resolve_entities_batch_full(",
                1,
            )[1].split(
                "    async def _resolve_entities_batch_trigram(",
                1,
            )[0]
            self.assertNotIn("metadata", full_source)
            self.assertIn(
                "SELECT id, canonical_name, last_seen, mention_count",
                full_source,
            )
            self.assertNotIn(" OR ", full_source)
            self.assertNotIn(
                "SELECT e.id, e.canonical_name, e.metadata, e.last_seen, e.mention_count,",
                trigram_source,
            )
            self.assertIn(
                "WHERE ec.entity_id_1 = ANY($1::uuid[])\n"
                "                           AND ec.entity_id_2 = ANY($2::uuid[])",
                trigram_source,
            )
            self.assertIn("cooccurrence_batch_size = 128", trigram_source)
            self.assertIn(
                "retain.phase1.candidates.exact.", trigram_source
            )
            self.assertIn(
                "retain.phase1.candidates.fuzzy.", trigram_source
            )
            self.assertIn("retain.phase1.cooccurrence", trigram_source)
            self.assertIn("retain.phase1.scoring", trigram_source)
            self.assertIn("timeout=125.0", trigram_source)
            memory_engine_source = (
                site_packages / "hindsight_api" / "engine" / "memory_engine.py"
            ).read_text(encoding="utf-8")
            poller_source = (
                site_packages / "hindsight_api" / "worker" / "poller.py"
            ).read_text(encoding="utf-8")
            self.assertIn(
                "_operation_recovery_http_status(e) == 400",
                memory_engine_source,
            )
            self.assertIn(
                "target is already absent; completing idempotently",
                memory_engine_source,
            )
            self.assertNotIn(
                "        if refreshed is None:\n"
                "            raise ValueError(f\"Mental model {mental_model_id} not found in bank {bank_id}\")",
                memory_engine_source,
            )
            self.assertTrue(
                "_operation_recovery_task_error_message(e)"
                in memory_engine_source
                or "error_message = format_task_error(e)"
                in memory_engine_source
            )
            self.assertTrue(
                "_operation_recovery_task_error_message(e)"
                in poller_source
                or "_schedule_retry_all(\n"
                "                task, e.retry_at, format_task_error(e)\n"
                "            )"
                in poller_source
            )
            self.assertIn(
                "logger.error(f\"Worker {self._worker_id} error in polling loop: "
                "{format_task_error(e)}\", exc_info=True)\n"
                "                raise",
                poller_source,
            )
            self.assertNotIn(
                "# Backoff on error\n"
                "                await asyncio.sleep(1)",
                poller_source,
            )
            memory_tree = ast.parse(memory_engine_source)
            diagnostic_functions = ast.Module(
                body=[
                    node
                    for node in memory_tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name
                    in {
                        "_operation_recovery_task_error_message",
                        "_operation_recovery_http_status",
                        "_is_non_retryable_task_error",
                    }
                ],
                type_ignores=[],
            )
            httpx = importlib.import_module("httpx")
            asyncpg = importlib.import_module("asyncpg")
            diagnostic_namespace = {
                "httpx": httpx,
                "asyncpg": asyncpg,
                "_is_oracledb_integrity_error": lambda _error: False,
                "_is_invalid_embedding_dimension_error": lambda _error: False,
            }
            exec(
                compile(
                    diagnostic_functions,
                    "candidate-memory-engine-diagnostics",
                    "exec",
                ),
                diagnostic_namespace,
            )
            request = httpx.Request("POST", "https://provider.invalid/v1")
            response = httpx.Response(400, request=request)
            bad_request = httpx.HTTPStatusError(
                "provider rejected request",
                request=request,
                response=response,
            )
            self.assertTrue(
                diagnostic_namespace["_is_non_retryable_task_error"](
                    bad_request
                )
            )
            class OpenAIStyleBadRequest(Exception):
                def __init__(self):
                    self.response = SimpleNamespace(status_code=400)

            self.assertTrue(
                diagnostic_namespace["_is_non_retryable_task_error"](
                    OpenAIStyleBadRequest()
                )
            )
            typed_error = diagnostic_namespace.get(
                "_operation_recovery_task_error_message"
            )
            if typed_error is not None:
                self.assertEqual(typed_error(TimeoutError()), "TimeoutError")
            else:
                self.assertIn(
                    "from ..worker.exceptions import DeferOperation, "
                    "RetryTaskAt, format_task_error",
                    memory_engine_source,
                )
            candidate_provider_root = (
                site_packages / "exact_drain_runtime" / "provider"
            )
            external_package = (
                root
                / "lib"
                / "python3.13"
                / "site-packages"
                / "hindsight_api"
            )
            external_package.mkdir(parents=True, mode=0o700)
            external_source = external_package / "__init__.py"
            external_source.write_text("EXTERNAL = 1\n", encoding="utf-8")
            external_source.chmod(0o600)
            dependency_root = external_package.parent
            asyncpg_package = dependency_root / "asyncpg"
            asyncpg_package.mkdir(mode=0o700)
            asyncpg_source = asyncpg_package / "__init__.py"
            asyncpg_source.write_text("ASYNC_DB = 1\n", encoding="utf-8")
            asyncpg_source.chmod(0o600)
            asyncpg_metadata = (
                dependency_root / "asyncpg-0.30.0.dist-info" / "METADATA"
            )
            asyncpg_metadata.parent.mkdir(mode=0o700)
            asyncpg_metadata.write_text(
                "Metadata-Version: 2.4\n"
                "Name: asyncpg\n"
                "Version: 0.30.0\n",
                encoding="utf-8",
            )
            after_restored_dependency = runtime_digest(
                worker,
                provider_root,
                package,
            )
            asyncpg_source.chmod(0o700)
            after_dependency_mode_change = runtime_digest(
                worker,
                provider_root,
                package,
            )
            asyncpg_source.chmod(0o600)
            asyncpg_metadata.chmod(0o600)

            before = runtime_digest(worker, provider_root, package)
            legacy_before = runtime_digest(
                worker,
                provider_root,
                package,
                schema_version=1,
            )
            external_source.write_text("EXTERNAL = 2\n", encoding="utf-8")
            after_external_drift = runtime_digest(
                worker,
                provider_root,
                package,
            )
            asyncpg_source.write_text("ASYNC_DB = 2\n", encoding="utf-8")
            after_asyncpg_drift = runtime_digest(
                worker,
                provider_root,
                package,
            )
            legacy_after_asyncpg_drift = runtime_digest(
                worker,
                provider_root,
                package,
                schema_version=1,
            )
            provider_root.joinpath("sitecustomize.py").write_text(
                "# changed external provider\n",
                encoding="utf-8",
            )
            after_external_provider_drift = runtime_digest(
                worker,
                provider_root,
                package,
            )
            legacy_after_external_provider_drift = runtime_digest(
                worker,
                provider_root,
                package,
                schema_version=1,
            )
            candidate_provider_source = candidate_provider_root / "sitecustomize.py"
            sealed_provider_bytes = candidate_provider_source.read_bytes()
            candidate_provider_source.write_text(
                "# changed sealed provider\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(Exception, "snapshot differs"):
                runtime_digest(worker, provider_root, package)
            candidate_provider_source.write_bytes(sealed_provider_bytes)
            added_dependency = dependency_root / "added_dependency.py"
            added_dependency.write_text("ADDED = True\n", encoding="utf-8")
            after_added_dependency = runtime_digest(
                worker,
                provider_root,
                package,
            )
            added_dependency.unlink()
            asyncpg_metadata.unlink()
            after_missing_dependency = runtime_digest(
                worker,
                provider_root,
                package,
            )
            asyncpg_metadata.write_text(
                "Metadata-Version: 2.4\n"
                "Name: asyncpg\n"
                "Version: 0.30.0\n",
                encoding="utf-8",
            )
            inert_pth = dependency_root / "inert-under-dash-S.pth"
            inert_pth.write_text("/private/tmp/foreign-shadow\n", encoding="utf-8")
            after_inert_pth = runtime_digest(worker, provider_root, package)
            inert_pth.unlink()
            dependency_link = dependency_root / "dependency-link.py"
            dependency_link.symlink_to(asyncpg_source)
            with self.assertRaisesRegex(Exception, "contains a symlink"):
                runtime_digest(worker, provider_root, package)
            dependency_link.unlink()
            unsupported = dependency_root / "dependency-fifo"
            os.mkfifo(unsupported, mode=0o600)
            with self.assertRaisesRegex(Exception, "unsupported entry"):
                runtime_digest(worker, provider_root, package)
            unsupported.unlink()
            bytecode = package / "__pycache__" / "extra.cpython-313.pyc"
            bytecode.parent.mkdir(mode=0o700)
            bytecode.write_bytes(b"synthetic importable bytecode")
            bytecode.chmod(0o600)
            after = runtime_digest(worker, provider_root, package)

            self.assertNotEqual(before, after_external_drift)
            self.assertNotEqual(before, after_asyncpg_drift)
            self.assertEqual(legacy_before, legacy_after_asyncpg_drift)
            self.assertEqual(
                after_asyncpg_drift,
                after_external_provider_drift,
            )
            self.assertNotEqual(
                legacy_after_asyncpg_drift,
                legacy_after_external_provider_drift,
            )
            self.assertNotEqual(
                after_external_provider_drift,
                after_added_dependency,
            )
            self.assertNotEqual(
                after_added_dependency,
                after_missing_dependency,
            )
            self.assertNotEqual(
                after_restored_dependency,
                after_inert_pth,
            )
            self.assertNotEqual(
                after_restored_dependency,
                after_dependency_mode_change,
            )
            self.assertNotEqual(before, after)
            for position in range(2048):
                entry = package / f"entry-{position:04d}.py"
                entry.touch(mode=0o600)
            with self.assertRaisesRegex(Exception, "too many entries"):
                runtime_digest(worker, provider_root, package)

    def test_exact_drain_snapshot_cli_assembles_closed_provider_sources(self):
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-snapshot-cli-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            provider_root = root / "provider-runtime"
            provider_root.mkdir(mode=0o700)
            candidate_library = root / "candidate-lib"
            candidate_library.mkdir(mode=0o700)
            resolver = _copy_patchable_entity_resolver(candidate_library)
            for name in ("sitecustomize.py", "hindsight_llm_failover.py"):
                source = provider_root / name
                source.write_text(f"# exact {name}\n", encoding="utf-8")
                source.chmod(0o600)
            command = [
                str(ROOT / "bin" / "hindsight-exact-drain-snapshot"),
                "--provider-runtime-root",
                str(provider_root),
                "--candidate-library",
                str(candidate_library),
            ]
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(
                value["kind"],
                "exact-drain-candidate-runtime-snapshot",
            )
            self.assertRegex(value["snapshot_digest"], r"^[0-9a-f]{64}$")
            self.assertEqual(value["schema_version"], 8)
            self.assertNotIn(str(provider_root), result.stdout)
            verified, sources = (
                operation_recovery_runtime.
                verify_exact_drain_candidate_runtime_snapshot(
                    candidate_library
                )
            )
            self.assertEqual(
                verified["snapshot_digest"],
                value["snapshot_digest"],
            )
            self.assertEqual(set(sources), {"sitecustomize.py", "hindsight_llm_failover.py"})
            resolver_bytes = resolver.read_bytes()
            resolver.write_bytes(resolver_bytes + b"# drift\n")
            with self.assertRaisesRegex(Exception, "snapshot differs"):
                operation_recovery_runtime.verify_exact_drain_candidate_runtime_snapshot(
                    candidate_library
                )
            resolver.write_bytes(resolver_bytes)
            repeated = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(repeated.returncode, 2)
            self.assertIn("already exists", repeated.stderr)
            extra = (
                candidate_library
                / "exact_drain_runtime"
                / "provider"
                / "shadow.py"
            )
            extra.write_text("SHADOW = True\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "snapshot differs"):
                operation_recovery_runtime.verify_exact_drain_candidate_runtime_snapshot(
                    candidate_library
                )

    def test_exact_drain_snapshot_recovers_source_commit_after_manifest(self):
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-snapshot-recovery-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            provider_root = root / "provider-runtime"
            provider_root.mkdir(mode=0o700)
            candidate_library = root / "candidate-lib"
            candidate_library.mkdir(mode=0o700)
            resolver = _copy_patchable_entity_resolver(candidate_library)
            original = resolver.read_bytes()
            for name in ("sitecustomize.py", "hindsight_llm_failover.py"):
                source = provider_root / name
                source.write_text(f"# exact {name}\n", encoding="utf-8")
                source.chmod(0o600)

            with (
                patch.object(
                    operation_recovery_runtime.os,
                    "replace",
                    side_effect=OSError("simulated source commit interruption"),
                ),
                self.assertRaisesRegex(Exception, "snapshot is unavailable"),
            ):
                operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )

            self.assertEqual(resolver.read_bytes(), original)
            self.assertTrue(
                (candidate_library / "exact_drain_runtime" / "manifest.json").is_file()
            )
            recovered = (
                operation_recovery_runtime.
                assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )
            )
            self.assertEqual(recovered["schema_version"], 8)
            self.assertNotEqual(resolver.read_bytes(), original)
            with self.assertRaisesRegex(Exception, "already exists"):
                operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )

    def test_exact_drain_snapshot_file_failure_never_publishes_partial_root(self):
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-snapshot-atomic-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            provider_root = root / "provider-runtime"
            provider_root.mkdir(mode=0o700)
            candidate_library = root / "candidate-lib"
            candidate_library.mkdir(mode=0o700)
            resolver = _copy_patchable_entity_resolver(candidate_library)
            original = resolver.read_bytes()
            for name in ("sitecustomize.py", "hindsight_llm_failover.py"):
                source = provider_root / name
                source.write_text(f"# exact {name}\n", encoding="utf-8")
                source.chmod(0o600)

            write_snapshot_file = (
                operation_recovery_runtime._write_exact_drain_snapshot_file
            )
            failed = False

            def fail_first_provider_file(path, body):
                nonlocal failed
                if not failed and Path(path).name == "sitecustomize.py":
                    failed = True
                    raise operation_recovery_runtime.OperationRecoveryError(
                        "exact drain candidate runtime snapshot is unavailable"
                    )
                return write_snapshot_file(path, body)

            with (
                patch.object(
                    operation_recovery_runtime,
                    "_write_exact_drain_snapshot_file",
                    side_effect=fail_first_provider_file,
                ),
                self.assertRaisesRegex(Exception, "snapshot is unavailable"),
            ):
                operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )

            self.assertFalse(
                (candidate_library / "exact_drain_runtime").exists()
            )
            self.assertEqual(resolver.read_bytes(), original)
            recovered = (
                operation_recovery_runtime.
                assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )
            )
            self.assertEqual(recovered["schema_version"], 8)

    def test_exact_drain_snapshot_failure_boundaries_are_retryable(self):
        for boundary in (
            "mkdir",
            "manifest",
            "publish",
            "publish-fsync",
            "source-fsync",
        ):
            with (
                self.subTest(boundary=boundary),
                tempfile.TemporaryDirectory(
                    dir="/private/tmp",
                    prefix=f"exact-drain-snapshot-{boundary}-",
                ) as directory,
            ):
                root = Path(directory)
                root.chmod(0o700)
                provider_root = root / "provider-runtime"
                provider_root.mkdir(mode=0o700)
                candidate_library = root / "candidate-lib"
                candidate_library.mkdir(mode=0o700)
                resolver = _copy_patchable_entity_resolver(candidate_library)
                original = resolver.read_bytes()
                for name in (
                    "sitecustomize.py",
                    "hindsight_llm_failover.py",
                ):
                    source = provider_root / name
                    source.write_text(
                        f"# exact {name}\n",
                        encoding="utf-8",
                    )
                    source.chmod(0o600)

                failed = False
                with ExitStack() as stack:
                    if boundary == "mkdir":
                        mkdir = Path.mkdir

                        def fault_mkdir(
                            path,
                            *args,
                            _mkdir=mkdir,
                            **kwargs,
                        ):
                            nonlocal failed
                            if (
                                not failed
                                and path.name
                                == ".exact_drain_runtime.staging"
                            ):
                                failed = True
                                raise OSError("simulated mkdir interruption")
                            return _mkdir(path, *args, **kwargs)

                        stack.enter_context(
                            patch.object(Path, "mkdir", new=fault_mkdir)
                        )
                    elif boundary == "manifest":
                        writer = operation_recovery_runtime._write_exact_drain_snapshot_file

                        def fault_manifest(path, body, _writer=writer):
                            nonlocal failed
                            if not failed and Path(path).name == "manifest.json":
                                failed = True
                                raise operation_recovery_runtime.OperationRecoveryError(
                                    "exact drain candidate runtime snapshot is unavailable"
                                )
                            return _writer(path, body)

                        stack.enter_context(
                            patch.object(
                                operation_recovery_runtime,
                                "_write_exact_drain_snapshot_file",
                                side_effect=fault_manifest,
                            )
                        )
                    elif boundary == "publish":
                        stack.enter_context(
                            patch.object(
                                operation_recovery_runtime,
                                "_publish_exact_drain_snapshot_directory",
                                side_effect=operation_recovery_runtime.OperationRecoveryError(
                                    "exact drain candidate runtime snapshot is unavailable"
                                ),
                            )
                        )
                    elif boundary == "publish-fsync":
                        fsync_directory = (
                            operation_recovery_runtime._fsync_exact_drain_directory
                        )

                        def fault_publish_fsync(
                            path,
                            _candidate_library=candidate_library,
                            _fsync_directory=fsync_directory,
                        ):
                            nonlocal failed
                            if (
                                not failed
                                and Path(path) == _candidate_library
                            ):
                                failed = True
                                raise operation_recovery_runtime.OperationRecoveryError(
                                    "exact drain candidate runtime snapshot is unavailable"
                                )
                            return _fsync_directory(path)

                        stack.enter_context(
                            patch.object(
                                operation_recovery_runtime,
                                "_fsync_exact_drain_directory",
                                side_effect=fault_publish_fsync,
                            )
                        )
                    else:
                        fsync = operation_recovery_runtime.os.fsync

                        def fault_source_fsync(descriptor, _fsync=fsync):
                            nonlocal failed
                            if (
                                not failed
                                and stat.S_ISDIR(
                                    os.fstat(descriptor).st_mode
                                )
                            ):
                                failed = True
                                raise OSError("simulated source fsync interruption")
                            return _fsync(descriptor)

                        stack.enter_context(
                            patch.object(
                                operation_recovery_runtime,
                                "_fsync_exact_drain_directory",
                                return_value=None,
                            )
                        )
                        stack.enter_context(
                            patch.object(
                                operation_recovery_runtime.os,
                                "fsync",
                                side_effect=fault_source_fsync,
                            )
                        )

                    with self.assertRaisesRegex(
                        Exception,
                        "snapshot is unavailable",
                    ):
                        operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                            provider_root,
                            candidate_library,
                        )

                recovered = operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )
                self.assertEqual(recovered["schema_version"], 8)
                self.assertNotEqual(resolver.read_bytes(), original)
                self.assertFalse(
                    (
                        candidate_library
                        / ".exact_drain_runtime.recovery"
                    ).exists()
                )
                with self.assertRaisesRegex(Exception, "already exists"):
                    operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                        provider_root,
                        candidate_library,
                    )

    def test_exact_drain_snapshot_recovers_after_final_marker_unlink_fsync(self):
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-snapshot-finalize-fsync-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            provider_root = root / "provider-runtime"
            provider_root.mkdir(mode=0o700)
            candidate_library = root / "candidate-lib"
            candidate_library.mkdir(mode=0o700)
            resolver = _copy_patchable_entity_resolver(candidate_library)
            for name in ("sitecustomize.py", "hindsight_llm_failover.py"):
                source = provider_root / name
                source.write_text(f"# exact {name}\n", encoding="utf-8")
                source.chmod(0o600)

            fsync_directory = (
                operation_recovery_runtime._fsync_exact_drain_directory
            )
            library_fsyncs = 0

            def fail_final_library_fsync(path):
                nonlocal library_fsyncs
                if Path(path) == candidate_library:
                    library_fsyncs += 1
                    if library_fsyncs == 3:
                        raise operation_recovery_runtime.OperationRecoveryError(
                            "exact drain candidate runtime snapshot is unavailable"
                        )
                return fsync_directory(path)

            with (
                patch.object(
                    operation_recovery_runtime,
                    "_fsync_exact_drain_directory",
                    side_effect=fail_final_library_fsync,
                ),
                self.assertRaisesRegex(Exception, "snapshot is unavailable"),
            ):
                operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )

            recovery_path = (
                candidate_library / ".exact_drain_runtime.recovery"
            )
            self.assertTrue(recovery_path.is_file())
            patched = resolver.read_bytes()

            provider_source = provider_root / "sitecustomize.py"
            provider_body = provider_source.read_bytes()
            provider_source.write_bytes(provider_body + b"# foreign\n")
            with self.assertRaisesRegex(Exception, "snapshot differs"):
                operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )
            provider_source.write_bytes(provider_body)

            resolver.write_bytes(patched + b"# drift\n")
            with self.assertRaisesRegex(Exception, "snapshot differs"):
                operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )
            resolver.write_bytes(patched)

            recovered = operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                provider_root,
                candidate_library,
            )
            self.assertEqual(recovered["schema_version"], 8)
            self.assertFalse(recovery_path.exists())

    def test_exact_drain_recovery_marker_partial_write_is_retryable(self):
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-marker-partial-write-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            provider_root = root / "provider-runtime"
            provider_root.mkdir(mode=0o700)
            candidate_library = root / "candidate-lib"
            candidate_library.mkdir(mode=0o700)
            resolver = _copy_patchable_entity_resolver(candidate_library)
            original = resolver.read_bytes()
            for name in ("sitecustomize.py", "hindsight_llm_failover.py"):
                source = provider_root / name
                source.write_text(f"# exact {name}\n", encoding="utf-8")
                source.chmod(0o600)

            recovery_path = (
                candidate_library / ".exact_drain_runtime.recovery"
            )
            recovery_staging = recovery_path.with_name(
                f".{recovery_path.name}.staging"
            )
            original_open = operation_recovery_runtime.os.open
            original_write = operation_recovery_runtime.os.write
            marker_descriptor = None
            wrote_partial = False

            def observe_marker_open(path, flags, *arguments):
                nonlocal marker_descriptor
                descriptor = original_open(path, flags, *arguments)
                if Path(path) in {recovery_path, recovery_staging}:
                    marker_descriptor = descriptor
                return descriptor

            def fault_marker_write(descriptor, body):
                nonlocal wrote_partial
                if descriptor == marker_descriptor:
                    if wrote_partial:
                        raise OSError("simulated partial marker write")
                    wrote_partial = True
                    return original_write(
                        descriptor,
                        body[: max(1, len(body) // 2)],
                    )
                return original_write(descriptor, body)

            with (
                patch.object(
                    operation_recovery_runtime.os,
                    "open",
                    side_effect=observe_marker_open,
                ),
                patch.object(
                    operation_recovery_runtime.os,
                    "write",
                    side_effect=fault_marker_write,
                ),
                self.assertRaisesRegex(Exception, "snapshot is unavailable"),
            ):
                operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )

            self.assertTrue(wrote_partial)
            self.assertFalse(recovery_path.exists())
            self.assertEqual(resolver.read_bytes(), original)
            recovered = operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                provider_root,
                candidate_library,
            )
            self.assertEqual(recovered["schema_version"], 8)
            self.assertNotEqual(resolver.read_bytes(), original)

    def test_exact_drain_recovery_marker_fault_matrix_is_retryable(self):
        for branch in ("fresh", "existing-runtime"):
            for fault in ("after-create", "partial-write", "fsync"):
                with (
                    self.subTest(branch=branch, fault=fault),
                    tempfile.TemporaryDirectory(
                        dir="/private/tmp",
                        prefix=f"exact-drain-marker-{branch}-{fault}-",
                    ) as directory,
                ):
                    root = Path(directory)
                    root.chmod(0o700)
                    provider_root = root / "provider-runtime"
                    provider_root.mkdir(mode=0o700)
                    candidate_library = root / "candidate-lib"
                    candidate_library.mkdir(mode=0o700)
                    resolver = _copy_patchable_entity_resolver(
                        candidate_library
                    )
                    original = resolver.read_bytes()
                    for name in (
                        "sitecustomize.py",
                        "hindsight_llm_failover.py",
                    ):
                        source = provider_root / name
                        source.write_text(
                            f"# exact {name}\n",
                            encoding="utf-8",
                        )
                        source.chmod(0o600)

                    recovery_path = (
                        candidate_library
                        / ".exact_drain_runtime.recovery"
                    )
                    recovery_staging = recovery_path.with_name(
                        f".{recovery_path.name}.staging"
                    )
                    if branch == "existing-runtime":
                        with (
                            patch.object(
                                operation_recovery_runtime,
                                "_write_exact_drain_snapshot_recovery_file",
                                side_effect=operation_recovery_runtime.OperationRecoveryError(
                                    "exact drain candidate runtime snapshot is unavailable"
                                ),
                            ),
                            self.assertRaisesRegex(
                                Exception,
                                "snapshot is unavailable",
                            ),
                        ):
                            operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                                provider_root,
                                candidate_library,
                            )
                        self.assertTrue(
                            (
                                candidate_library / "exact_drain_runtime"
                            ).is_dir()
                        )

                    original_open = operation_recovery_runtime.os.open
                    original_write = operation_recovery_runtime.os.write
                    original_fsync = operation_recovery_runtime.os.fsync
                    marker_state = {"descriptor": None}
                    partial_written = False
                    faulted = False

                    def fault_marker_open(
                        path,
                        flags,
                        *arguments,
                        _original_open=original_open,
                        _recovery_staging=recovery_staging,
                        _fault=fault,
                        _marker_state=marker_state,
                    ):
                        nonlocal faulted
                        descriptor = _original_open(path, flags, *arguments)
                        if Path(path) == _recovery_staging:
                            _marker_state["descriptor"] = descriptor
                            if _fault == "after-create" and not faulted:
                                faulted = True
                                os.close(descriptor)
                                raise OSError(
                                    "simulated marker create interruption"
                                )
                        return descriptor

                    def fault_marker_write(
                        descriptor,
                        body,
                        _fault=fault,
                        _original_write=original_write,
                        _marker_state=marker_state,
                    ):
                        nonlocal partial_written, faulted
                        if (
                            _fault == "partial-write"
                            and descriptor == _marker_state["descriptor"]
                        ):
                            if partial_written and not faulted:
                                faulted = True
                                raise OSError(
                                    "simulated partial marker write"
                                )
                            partial_written = True
                            return _original_write(
                                descriptor,
                                body[: max(1, len(body) // 2)],
                            )
                        return _original_write(descriptor, body)

                    def fault_marker_fsync(
                        descriptor,
                        _fault=fault,
                        _original_fsync=original_fsync,
                        _marker_state=marker_state,
                    ):
                        nonlocal faulted
                        if (
                            _fault == "fsync"
                            and descriptor == _marker_state["descriptor"]
                            and not faulted
                        ):
                            faulted = True
                            raise OSError("simulated marker fsync interruption")
                        return _original_fsync(descriptor)

                    with (
                        patch.object(
                            operation_recovery_runtime.os,
                            "open",
                            side_effect=fault_marker_open,
                        ),
                        patch.object(
                            operation_recovery_runtime.os,
                            "write",
                            side_effect=fault_marker_write,
                        ),
                        patch.object(
                            operation_recovery_runtime.os,
                            "fsync",
                            side_effect=fault_marker_fsync,
                        ),
                        self.assertRaisesRegex(
                            Exception,
                            "snapshot is unavailable",
                        ),
                    ):
                        operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                            provider_root,
                            candidate_library,
                        )

                    self.assertTrue(faulted)
                    self.assertFalse(recovery_path.exists())
                    self.assertFalse(recovery_staging.exists())
                    self.assertEqual(resolver.read_bytes(), original)
                    recovered = operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                        provider_root,
                        candidate_library,
                    )
                    self.assertEqual(recovered["schema_version"], 8)
                    self.assertNotEqual(resolver.read_bytes(), original)

    def test_exact_drain_recovery_marker_restoration_faults_are_retryable(self):
        for fault in ("after-create", "partial-write", "fsync"):
            with (
                self.subTest(fault=fault),
                tempfile.TemporaryDirectory(
                    dir="/private/tmp",
                    prefix=f"exact-drain-marker-restore-{fault}-",
                ) as directory,
            ):
                root = Path(directory)
                root.chmod(0o700)
                provider_root = root / "provider-runtime"
                provider_root.mkdir(mode=0o700)
                candidate_library = root / "candidate-lib"
                candidate_library.mkdir(mode=0o700)
                resolver = _copy_patchable_entity_resolver(candidate_library)
                original = resolver.read_bytes()
                for name in (
                    "sitecustomize.py",
                    "hindsight_llm_failover.py",
                ):
                    source = provider_root / name
                    source.write_text(
                        f"# exact {name}\n",
                        encoding="utf-8",
                    )
                    source.chmod(0o600)

                with (
                    patch.object(
                        operation_recovery_runtime,
                        "_finalize_exact_drain_snapshot_recovery",
                        side_effect=operation_recovery_runtime.OperationRecoveryError(
                            "exact drain candidate runtime snapshot is unavailable"
                        ),
                    ),
                    self.assertRaisesRegex(
                        Exception,
                        "snapshot is unavailable",
                    ),
                ):
                    operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                        provider_root,
                        candidate_library,
                    )

                self.assertNotEqual(resolver.read_bytes(), original)
                recovery_path = (
                    candidate_library / ".exact_drain_runtime.recovery"
                )
                recovery_staging = recovery_path.with_name(
                    f".{recovery_path.name}.staging"
                )
                self.assertTrue(recovery_path.is_file())

                original_open = operation_recovery_runtime.os.open
                original_write = operation_recovery_runtime.os.write
                original_fsync = operation_recovery_runtime.os.fsync
                fsync_directory = (
                    operation_recovery_runtime._fsync_exact_drain_directory
                )
                marker_state = {"descriptor": None}
                partial_written = False
                marker_faulted = False
                finalize_faulted = False

                def fault_marker_open(
                    path,
                    flags,
                    *arguments,
                    _original_open=original_open,
                    _recovery_staging=recovery_staging,
                    _fault=fault,
                    _marker_state=marker_state,
                ):
                    nonlocal marker_faulted
                    descriptor = _original_open(path, flags, *arguments)
                    if Path(path) == _recovery_staging:
                        _marker_state["descriptor"] = descriptor
                        if _fault == "after-create" and not marker_faulted:
                            marker_faulted = True
                            os.close(descriptor)
                            raise OSError(
                                "simulated restoration create interruption"
                            )
                    return descriptor

                def fault_marker_write(
                    descriptor,
                    body,
                    _fault=fault,
                    _original_write=original_write,
                    _marker_state=marker_state,
                ):
                    nonlocal partial_written, marker_faulted
                    if (
                        _fault == "partial-write"
                        and descriptor == _marker_state["descriptor"]
                    ):
                        if partial_written and not marker_faulted:
                            marker_faulted = True
                            raise OSError(
                                "simulated partial restoration marker"
                            )
                        partial_written = True
                        return _original_write(
                            descriptor,
                            body[: max(1, len(body) // 2)],
                        )
                    return _original_write(descriptor, body)

                def fault_marker_fsync(
                    descriptor,
                    _fault=fault,
                    _original_fsync=original_fsync,
                    _marker_state=marker_state,
                ):
                    nonlocal marker_faulted
                    if (
                        _fault == "fsync"
                        and descriptor == _marker_state["descriptor"]
                        and not marker_faulted
                    ):
                        marker_faulted = True
                        raise OSError("simulated restoration marker fsync")
                    return _original_fsync(descriptor)

                def fault_finalize_fsync(
                    path,
                    _candidate_library=candidate_library,
                    _fsync_directory=fsync_directory,
                ):
                    nonlocal finalize_faulted
                    if (
                        Path(path) == _candidate_library
                        and not finalize_faulted
                    ):
                        finalize_faulted = True
                        raise operation_recovery_runtime.OperationRecoveryError(
                            "exact drain candidate runtime snapshot is unavailable"
                        )
                    return _fsync_directory(path)

                with (
                    patch.object(
                        operation_recovery_runtime.os,
                        "open",
                        side_effect=fault_marker_open,
                    ),
                    patch.object(
                        operation_recovery_runtime.os,
                        "write",
                        side_effect=fault_marker_write,
                    ),
                    patch.object(
                        operation_recovery_runtime.os,
                        "fsync",
                        side_effect=fault_marker_fsync,
                    ),
                    patch.object(
                        operation_recovery_runtime,
                        "_fsync_exact_drain_directory",
                        side_effect=fault_finalize_fsync,
                    ),
                    self.assertRaisesRegex(
                        Exception,
                        "snapshot is unavailable",
                    ),
                ):
                    operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                        provider_root,
                        candidate_library,
                    )

                self.assertTrue(finalize_faulted)
                self.assertTrue(marker_faulted)
                self.assertFalse(recovery_staging.exists())
                self.assertTrue(recovery_path.is_file())
                recovery_body = recovery_path.read_bytes()
                recovery_path.write_bytes(b"{}\n")
                with self.assertRaisesRegex(Exception, "snapshot differs"):
                    operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                        provider_root,
                        candidate_library,
                    )
                recovery_path.write_bytes(recovery_body)
                recovered = operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                    provider_root,
                    candidate_library,
                )
                self.assertEqual(recovered["schema_version"], 8)
                self.assertFalse(recovery_path.exists())

    def test_exact_drain_phase_repair_preserves_trigram_resolution_behavior(self):
        package_spec = importlib.util.find_spec("hindsight_api")
        if package_spec is None or package_spec.origin is None:
            raise unittest.SkipTest("hindsight_api candidate source is unavailable")
        installed_package = Path(package_spec.origin).parent
        dependency_root = installed_package.parent
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-resolver-behavior-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            candidate_library = root / "candidate-lib"
            candidate_library.mkdir(mode=0o700)
            shutil.copytree(
                installed_package,
                candidate_library / "hindsight_api",
            )
            provider_root = root / "provider-runtime"
            provider_root.mkdir(mode=0o700)
            for name in ("sitecustomize.py", "hindsight_llm_failover.py"):
                source = provider_root / name
                source.write_text(f"# exact {name}\n", encoding="utf-8")
                source.chmod(0o600)
            operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                provider_root,
                candidate_library,
            )
            script = textwrap.dedent(
                f"""
                import asyncio
                import json
                import sys
                from datetime import UTC, datetime

                sys.path[:0] = [{str(candidate_library)!r}, {str(dependency_root)!r}]
                from hindsight_api.engine import entity_resolver as resolver_module
                from hindsight_api.engine.entity_resolver import (
                    EntityResolver,
                    _EntityToCreate,
                )

                class Transaction:
                    async def __aenter__(self):
                        return self
                    async def __aexit__(self, *_arguments):
                        return False

                class CandidateRow(dict):
                    def __getitem__(self, key):
                        if key in {{"metadata", "mention_count"}}:
                            raise AssertionError("unused candidate field decoded")
                        return super().__getitem__(key)

                class FullCandidateRow(dict):
                    def __getitem__(self, key):
                        if key == "metadata":
                            raise AssertionError("candidate metadata decoded")
                        return super().__getitem__(key)

                class ExternalEdge:
                    def __getitem__(self, _key):
                        raise AssertionError("noncandidate edge decoded")

                class Connection:
                    def __init__(self, now):
                        self.now = now
                        self.execute_calls = []
                        self.query_batch_sizes = []
                        self.cooccurrence_batch_sizes = []
                    def transaction(self):
                        return Transaction()
                    async def execute(self, query):
                        if query not in {{
                            "SET TRANSACTION READ ONLY",
                            "SET LOCAL statement_timeout = '120s'",
                        }}:
                            raise AssertionError("server transaction guard differs")
                        self.execute_calls.append(query)
                    async def fetch(self, query, *arguments, timeout):
                        if timeout != 125.0:
                            raise AssertionError("client deadline differs")
                        if "entity_cooccurrences" in query:
                            if " OR " in query:
                                return [ExternalEdge()]
                            if " AND " not in query:
                                raise AssertionError("cooccurrence scope differs")
                            self.cooccurrence_batch_sizes.append(
                                len(arguments[0])
                            )
                            if len(arguments) != 2:
                                raise AssertionError(
                                    "cooccurrence candidate authority is unbounded"
                                )
                            if len(arguments[1]) != 257:
                                raise AssertionError(
                                    "cooccurrence candidate set differs"
                                )
                            return [
                                {{"entity_id_1": "alice-id", "entity_id_2": "bob-id"}}
                            ]
                        if "metadata" in query or "mention_count" in query:
                            raise AssertionError("unused candidate projection fetched")
                        self.query_batch_sizes.append(len(arguments[1]))
                        return [
                            CandidateRow(
                                id=(
                                    "alice-id"
                                    if text == "Alicee"
                                    else "bob-id"
                                    if text == "Bob"
                                    else f"entity-{{text}}"
                                ),
                                canonical_name=(
                                    "Alice"
                                    if text == "Alicee"
                                    else "Bob"
                                    if text == "Bob"
                                    else text.removesuffix("x")
                                ),
                                last_seen=self.now,
                                query_text=text,
                            )
                            for text in arguments[1]
                        ]

                class FullConnection(Connection):
                    def __init__(self, now, entity_rows):
                        super().__init__(now)
                        self.entity_rows = entity_rows

                    async def fetch(self, query, *arguments, timeout):
                        if timeout != 125.0:
                            raise AssertionError("client deadline differs")
                        if "entity_cooccurrences" in query:
                            if " OR " in query or " AND " not in query:
                                raise AssertionError(
                                    "full cooccurrence scope differs"
                                )
                            return [
                                {{
                                    "entity_id_1": "alice-id",
                                    "entity_id_2": "bob-id",
                                    "cooccurrence_count": 1,
                                }}
                            ]
                        if "metadata" in query:
                            raise AssertionError(
                                "unused full candidate projection fetched"
                            )
                        return [
                            FullCandidateRow(
                                id=(
                                    "alice-id"
                                    if entity["text"] == "Alicee"
                                    else "bob-id"
                                    if entity["text"] == "Bob"
                                    else f"entity-{{entity['text']}}"
                                ),
                                canonical_name=(
                                    "Alice"
                                    if entity["text"] == "Alicee"
                                    else "Bob"
                                    if entity["text"] == "Bob"
                                    else entity["text"].removesuffix("x")
                                ),
                                last_seen=self.now,
                                mention_count=99,
                            )
                            for entity in self.entity_rows
                        ] + [
                            FullCandidateRow(
                                id=f"bob-overflow-{{position}}",
                                canonical_name=f"Bob extra {{position}}",
                                last_seen=self.now,
                                mention_count=position,
                            )
                            for position in range(201)
                        ]

                class FaultConnection(Connection):
                    def __init__(self, now, error):
                        super().__init__(now)
                        self.error = error

                    async def fetch(self, _query, *_arguments, timeout):
                        if timeout != 125.0:
                            raise AssertionError("client deadline differs")
                        raise self.error

                def projection(values):
                    return [
                        (item.entity_id, item.canonical_name, item.entity_kind)
                        for item in values
                    ]

                class GuardedTrigramPattern:
                    def __init__(self, upstream):
                        self.upstream = upstream
                        self.max_input_length = 0

                    def findall(self, text):
                        self.max_input_length = max(
                            self.max_input_length,
                            len(text),
                        )
                        if len(text) > 4096:
                            raise AssertionError(
                                "unbounded entity name reached trigram scan"
                            )
                        return self.upstream.findall(text)

                async def exercise():
                    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
                    entities = [
                        {{"text": "Alicee", "nearby_entities": [{{"text": "Bob"}}]}},
                        {{"text": "Bob", "nearby_entities": [{{"text": "Alicee"}}]}},
                    ] + [
                        {{"text": f"Entity{{position:02d}}x", "nearby_entities": []}}
                        for position in range(255)
                    ]
                    candidates = {{
                        entity["text"]: [
                            (
                                "alice-id"
                                if entity["text"] == "Alicee"
                                else "bob-id"
                                if entity["text"] == "Bob"
                                else f"entity-{{entity['text']}}",
                                "Alice"
                                if entity["text"] == "Alicee"
                                else "Bob"
                                if entity["text"] == "Bob"
                                else entity["text"].removesuffix("x"),
                                {{"large": "x" * 1000}},
                                now,
                                99,
                            )
                        ]
                        for entity in entities
                    }}
                    expected_resolver = EntityResolver(pool=None)
                    expected = await expected_resolver._resolve_from_candidates(
                        Connection(now),
                        "engineering",
                        entities,
                        now,
                        candidates,
                        {{"alice-id": {{"bob"}}, "bob-id": {{"alicee"}}}},
                    )
                    expected_stats = [
                        (item.entity_id, item.event_date.isoformat())
                        for item in expected_resolver._pending_stats[
                            expected_resolver._task_key()
                        ]
                    ]
                    resolver = EntityResolver(pool=None)
                    actual_connection = Connection(now)
                    actual = await resolver._resolve_entities_batch_trigram(
                        actual_connection,
                        "engineering",
                        entities,
                        now,
                    )
                    actual_stats = [
                        (item.entity_id, item.event_date.isoformat())
                        for item in resolver._pending_stats[resolver._task_key()]
                    ]
                    full_resolver = EntityResolver(pool=None)
                    full_connection = FullConnection(now, entities)
                    full = await full_resolver._resolve_entities_batch_full(
                        full_connection,
                        "engineering",
                        entities,
                        now,
                    )
                    full_stats = [
                        (item.entity_id, item.event_date.isoformat())
                        for item in full_resolver._pending_stats[
                            full_resolver._task_key()
                        ]
                    ]

                    async def capture_timeout(strategy, error):
                        timeout_resolver = EntityResolver(pool=None)
                        resolve = getattr(
                            timeout_resolver,
                            f"_resolve_entities_batch_{{strategy}}",
                        )
                        try:
                            await resolve(
                                FaultConnection(now, error),
                                "engineering",
                                entities[:1],
                                now,
                            )
                        except BaseException as observed:
                            return {{
                                "type": type(observed).__name__,
                                "message": str(observed),
                                "cause_type": type(observed.__cause__).__name__,
                            }}
                        raise AssertionError("phase-one timeout was swallowed")

                    timeouts = {{}}
                    for strategy in ("trigram", "full"):
                        sqlstate_timeout_error = RuntimeError("query cancelled")
                        sqlstate_timeout_error.sqlstate = "57014"
                        timeouts[strategy] = {{
                            "client": await capture_timeout(
                                strategy,
                                TimeoutError(),
                            ),
                            "statement": await capture_timeout(
                                strategy,
                                RuntimeError(
                                    "canceling statement due to statement timeout"
                                ),
                            ),
                            "sqlstate": await capture_timeout(
                                strategy,
                                sqlstate_timeout_error,
                            ),
                        }}
                    guarded_pattern = GuardedTrigramPattern(
                        resolver_module._TRGM_WORD
                    )
                    resolver_module._TRGM_WORD = guarded_pattern
                    try:
                        intrabatch = full_resolver._intrabatch_canonical_map(
                            [
                                _EntityToCreate(0, "Alpha", now),
                                _EntityToCreate(1, "Alphaa", now),
                                _EntityToCreate(2, "x" * 4097, now),
                            ]
                        )
                        name_bound_max_input_length = (
                            guarded_pattern.max_input_length
                        )
                        guarded_pattern.max_input_length = 0
                        over_budget = (
                            full_resolver._intrabatch_canonical_map(
                                [
                                    _EntityToCreate(
                                        position,
                                        f"{{position:02d}}" + "y" * 3998,
                                        now,
                                    )
                                    for position in range(17)
                                ]
                            )
                        )
                        batch_bound_max_input_length = (
                            guarded_pattern.max_input_length
                        )
                    finally:
                        resolver_module._TRGM_WORD = (
                            guarded_pattern.upstream
                        )
                    return {{
                        "expected": projection(expected),
                        "actual": projection(actual),
                        "full": projection(full),
                        "expected_stats": expected_stats,
                        "actual_stats": actual_stats,
                        "full_stats": full_stats,
                        "full_execute_calls": full_connection.execute_calls,
                        "execute_calls": actual_connection.execute_calls,
                        "query_batch_sizes": actual_connection.query_batch_sizes,
                        "cooccurrence_batch_sizes": (
                            actual_connection.cooccurrence_batch_sizes
                        ),
                        "max_trigram_input_length": (
                            name_bound_max_input_length
                        ),
                        "intrabatch_normal_keys": sorted(
                            key for key in intrabatch if len(key) <= 4096
                        ),
                        "intrabatch_overlong_preserved": (
                            intrabatch.get("x" * 4097) == "x" * 4097
                        ),
                        "batch_bound_max_trigram_input_length": (
                            batch_bound_max_input_length
                        ),
                        "over_budget_empty": over_budget == {{}},
                        "timeouts": timeouts,
                    }}

                print(json.dumps(asyncio.run(exercise()), sort_keys=True))
                """
            )
            result = subprocess.run(
                [sys.executable, "-S", "-c", script],
                check=False,
                cwd="/",
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(result.stdout)
            self.assertEqual(observed["actual"], observed["expected"])
            self.assertEqual(observed["full"], observed["expected"])
            self.assertEqual(
                observed["actual_stats"],
                observed["expected_stats"],
            )
            self.assertEqual(
                observed["full_stats"],
                observed["expected_stats"],
            )
            self.assertEqual(
                observed["full_execute_calls"],
                [
                    "SET TRANSACTION READ ONLY",
                    "SET LOCAL statement_timeout = '120s'",
                ]
                * 2,
            )
            self.assertEqual(
                observed["execute_calls"],
                [
                    "SET TRANSACTION READ ONLY",
                    "SET LOCAL statement_timeout = '120s'",
                ]
                * 29,
            )
            self.assertEqual(
                observed["query_batch_sizes"],
                [10] * 25 + [7],
            )
            self.assertEqual(
                observed["cooccurrence_batch_sizes"],
                [128, 128, 1],
            )
            self.assertLessEqual(observed["max_trigram_input_length"], 4096)
            self.assertEqual(
                observed["intrabatch_normal_keys"],
                ["alpha", "alphaa"],
            )
            self.assertTrue(observed["intrabatch_overlong_preserved"])
            self.assertEqual(
                observed["batch_bound_max_trigram_input_length"],
                0,
            )
            self.assertTrue(observed["over_budget_empty"])
            for strategy, stage in (
                ("trigram", r"fuzzy\.1/1"),
                ("full", "full"),
            ):
                for key, cause_type in (
                    ("client", "TimeoutError"),
                    ("statement", "RuntimeError"),
                    ("sqlstate", "RuntimeError"),
                ):
                    with self.subTest(strategy=strategy, fault=key):
                        timeout = observed["timeouts"][strategy][key]
                        self.assertEqual(timeout["type"], "TimeoutError")
                        self.assertEqual(timeout["cause_type"], cause_type)
                        self.assertRegex(
                            timeout["message"],
                            r"^operation-recovery exact drain phase-one query "
                            rf"timed out at retain\.phase1\.candidates\.{stage}$",
                        )

    def test_exact_drain_metadata_ceiling_precedes_file_read(self):
        read_version = self.controller[
            "exact_drain_worker_hindsight_version"
        ]
        read_file = read_version.__globals__["_exact_drain_file_bytes"]
        runtime_globals = read_file.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-metadata-",
        ) as directory:
            metadata = Path(directory) / "METADATA"
            metadata.touch(mode=0o600)
            with metadata.open("r+b") as stream:
                stream.truncate(1024 * 1024 + 1)
            original_read = runtime_globals["os"].read

            def reject_read(*_args, **_kwargs):
                raise AssertionError("oversized metadata was read")

            runtime_globals["os"].read = reject_read
            try:
                with self.assertRaisesRegex(Exception, "too large"):
                    read_file(
                        metadata,
                        "exact drain worker Hindsight metadata",
                        max_bytes=1024 * 1024,
                    )
            finally:
                runtime_globals["os"].read = original_read

    def test_exact_drain_streams_sparse_package_artifact_digest(self):
        runtime_digest = self.controller["exact_drain_runtime_digest"]
        runtime_globals = runtime_digest.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-sparse-runtime-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            bin_dir = root / "bin"
            bin_dir.mkdir(mode=0o700)
            worker = bin_dir / "hindsight-worker"
            interpreter = bin_dir / "python3"
            interpreter.write_text("synthetic interpreter\n", encoding="utf-8")
            interpreter.chmod(0o700)
            worker.write_text(f"#!{interpreter}\n", encoding="utf-8")
            worker.chmod(0o700)
            (root / "pyvenv.cfg").write_text("home = /private/tmp\n")
            (root / "pyvenv.cfg").chmod(0o600)
            site_packages = root / "lib" / "python3.13" / "site-packages"
            metadata = (
                site_packages
                / "hindsight_api-0.9.0.dist-info"
                / "METADATA"
            )
            metadata.parent.mkdir(parents=True, mode=0o700)
            metadata.write_text(
                "Metadata-Version: 2.4\n"
                "Name: hindsight-api\n"
                "Version: 0.9.0\n",
                encoding="utf-8",
            )
            metadata.chmod(0o600)
            package = site_packages / "hindsight_api"
            package.mkdir(mode=0o700)
            _copy_patchable_entity_resolver(site_packages)
            sparse = package / "native.so"
            sparse.touch(mode=0o600)
            with sparse.open("r+b") as stream:
                stream.truncate(16 * 1024 * 1024)
            asyncpg_package = site_packages / "asyncpg"
            asyncpg_package.mkdir(mode=0o700)
            (asyncpg_package / "__init__.py").write_text(
                "ASYNC_DB = 1\n",
                encoding="utf-8",
            )
            asyncpg_metadata = (
                site_packages / "asyncpg-0.30.0.dist-info" / "METADATA"
            )
            asyncpg_metadata.parent.mkdir(mode=0o700)
            asyncpg_metadata.write_text(
                "Metadata-Version: 2.4\n"
                "Name: asyncpg\n"
                "Version: 0.30.0\n",
                encoding="utf-8",
            )
            asyncpg_metadata.chmod(0o600)
            provider_root = root / "provider-runtime"
            provider_root.mkdir(mode=0o700)
            for name in ("sitecustomize.py", "hindsight_llm_failover.py"):
                path = provider_root / name
                path.write_text(f"# synthetic {name}\n", encoding="utf-8")
                path.chmod(0o600)
            operation_recovery_runtime.assemble_exact_drain_candidate_runtime_snapshot(
                provider_root,
                site_packages,
            )
            original_file_bytes = runtime_globals["_exact_drain_file_bytes"]

            def reject_sparse_materialization(path, label, **kwargs):
                if Path(path) == sparse:
                    raise AssertionError("sparse package artifact was materialized")
                return original_file_bytes(path, label, **kwargs)

            runtime_globals["_exact_drain_file_bytes"] = (
                reject_sparse_materialization
            )
            try:
                observed = runtime_digest(worker, provider_root, package)
            finally:
                runtime_globals["_exact_drain_file_bytes"] = original_file_bytes

            self.assertRegex(observed, r"^[0-9a-f]{64}$")
            original_total_ceiling = runtime_globals[
                "EXACT_DRAIN_MAX_PACKAGE_TOTAL_BYTES"
            ]
            original_file_ceiling = runtime_globals[
                "EXACT_DRAIN_MAX_PACKAGE_FILE_BYTES"
            ]
            runtime_globals["EXACT_DRAIN_MAX_PACKAGE_TOTAL_BYTES"] = (
                8 * 1024 * 1024
            )
            runtime_globals["EXACT_DRAIN_MAX_PACKAGE_FILE_BYTES"] = (
                32 * 1024 * 1024
            )
            try:
                with self.assertRaisesRegex(Exception, "too large"):
                    runtime_digest(worker, provider_root, package)
            finally:
                runtime_globals["EXACT_DRAIN_MAX_PACKAGE_TOTAL_BYTES"] = (
                    original_total_ceiling
                )
                runtime_globals["EXACT_DRAIN_MAX_PACKAGE_FILE_BYTES"] = (
                    original_file_ceiling
                )
            with sparse.open("r+b") as stream:
                stream.truncate(16 * 1024 * 1024 + 1)
            with self.assertRaisesRegex(Exception, "too large"):
                runtime_digest(worker, provider_root, package)

    def test_exact_drain_provider_policy_must_be_the_canonical_home_policy(self):
        validate = self.controller[
            "_operation_recovery_exact_provider_policy"
        ]
        with self.assertRaisesRegex(
            Exception,
            "provider policy path differs",
        ):
            validate("/private/tmp/not-the-canonical-provider-policy.json")

    def test_exact_drain_provider_policy_hashes_the_validated_bytes(self):
        evidence = self.controller[
            "_operation_recovery_exact_provider_policy_evidence"
        ]
        globals_ = evidence.__globals__
        policy_path = (
            Path.home()
            / ".config/hindsight-control-plane/provider-runtime-policy.json"
        )
        body = json.dumps(
            _exact_split_timeout_policy_data(),
            separators=(",", ":"),
        ).encode("utf-8")
        reads = []
        original = globals_["_operation_recovery_read_private_bytes"]
        globals_["_operation_recovery_read_private_bytes"] = (
            lambda _path, _label: reads.append(True) or body
        )
        try:
            observed, _policy = evidence(policy_path)
        finally:
            globals_["_operation_recovery_read_private_bytes"] = original
        self.assertEqual(reads, [True])
        self.assertEqual(observed, hashlib.sha256(body).hexdigest())

    def test_exact_drain_provider_policy_requires_codex_spark_models(self):
        validate = self.controller[
            "_operation_recovery_validate_exact_provider_policy"
        ]
        policy = self.controller["ProviderRuntimePolicy"].load(
            _exact_split_timeout_policy_data()
        )
        for member_id in (
            "work-codex",
            "personal-codex",
            "alt1-codex",
            "alt2-codex",
        ):
            member = policy.member(member_id)
            self.assertEqual(member.identity.model, "gpt-5.3-codex-spark")
            changed_member = replace(
                member,
                identity=replace(
                    member.identity,
                    model="gpt-5.3-codex-spark-drifted",
                ),
            )
            with (
                self.subTest(member_id=member_id),
                self.assertRaisesRegex(Exception, "provider policy differs"),
            ):
                validate(
                    replace(
                        policy,
                        members=tuple(
                            changed_member
                            if candidate.id == member_id
                            else candidate
                            for candidate in policy.members
                        ),
                    )
                )

    def test_exact_drain_provider_policy_requires_canonical_failover_members(self):
        validate = self.controller[
            "_operation_recovery_validate_exact_provider_policy"
        ]
        policy_value = _exact_split_timeout_policy_data()
        policy = self.controller["ProviderRuntimePolicy"].load(
            policy_value
        )
        self.assertEqual(policy.member("hatchery").max_concurrent, 2)
        luna = policy.member("openai-luna")
        self.assertEqual(luna.identity.provider, "openai-responses")
        self.assertEqual(luna.identity.model, "gpt-5.6-luna")
        self.assertEqual(
            luna.identity.credential_marker,
            "provider-policy:openai-luna",
        )
        self.assertEqual(luna.credential_mode, "api-key")
        self.assertEqual(
            luna.credential_locator,
            "api-key:hindsight-openai",
        )
        for changed_luna in (
            replace(
                luna,
                identity=replace(
                    luna.identity,
                    model="gpt-5.6-luna-drifted",
                ),
            ),
            replace(luna, credential_locator="api-key:other"),
            replace(luna, quota_cooldown=False),
            replace(luna, max_retries=1),
        ):
            with (
                self.subTest(changed_luna=changed_luna),
                self.assertRaisesRegex(Exception, "provider policy differs"),
            ):
                validate(
                    replace(
                        policy,
                        members=tuple(
                            changed_luna
                            if member.id == luna.id
                            else member
                            for member in policy.members
                        ),
                    )
                )
        with self.assertRaisesRegex(Exception, "provider policy differs"):
            validate(
                replace(
                    policy,
                    default_usage_limit_cooldown_seconds=301,
                )
            )
        work = policy.member("work-codex")
        changed_identity = replace(
            work.identity,
            provider="openai-compatible",
        )
        changed_member = replace(work, identity=changed_identity)
        changed = replace(
            policy,
            members=tuple(
                changed_member if member.id == work.id else member
                for member in policy.members
            ),
        )
        with self.assertRaisesRegex(
            Exception,
            "provider policy differs",
        ):
            validate(changed)
        for changed_member in (
            replace(work, quota_cooldown=False),
            replace(work, max_retries=None),
        ):
            with self.subTest(changed_member=changed_member), self.assertRaisesRegex(
                Exception,
                "provider policy differs",
            ):
                validate(
                    replace(
                        policy,
                        members=tuple(
                            changed_member if member.id == work.id else member
                            for member in policy.members
                        ),
                    )
                )
        hatchery = policy.member("hatchery")
        with self.assertRaisesRegex(
            Exception,
            "provider policy differs",
        ):
            validate(
                replace(
                    policy,
                    members=tuple(
                        replace(hatchery, max_concurrent=1)
                        if member.id == hatchery.id
                        else member
                        for member in policy.members
                    ),
                )
            )
        bounded_hatchery = replace(hatchery, max_retries=0)
        bounded_policy = replace(
            policy,
            members=tuple(
                bounded_hatchery if member.id == hatchery.id else member
                for member in policy.members
            ),
        )

        repair_policy = policy
        validate(repair_policy)
        old_timeout_policy = replace(
            policy,
            members=tuple(
                replace(hatchery, execution_timeout_seconds=1_200)
                if member.id == hatchery.id
                else member
                for member in policy.members
            ),
        )
        with self.assertRaisesRegex(Exception, "provider policy differs"):
            validate(old_timeout_policy)
        with self.assertRaisesRegex(Exception, "provider policy differs"):
            validate(
                replace(
                    policy,
                    failover_order=tuple(
                        member_id
                        for member_id in policy.failover_order
                        if member_id != "openai-luna"
                    ),
                )
            )
        with self.assertRaisesRegex(Exception, "provider policy differs"):
            operation_recovery_runtime.validate_exact_drain_provider_policy(
                repair_policy,
                plan_schema_version=14,
            )
        operation_recovery_runtime.validate_exact_drain_provider_policy(
            repair_policy,
            plan_schema_version=15,
        )
        with self.assertRaisesRegex(Exception, "provider policy differs"):
            operation_recovery_runtime.validate_exact_drain_provider_policy(
                old_timeout_policy,
                plan_schema_version=15,
            )

        historical_policy = self.controller["ProviderRuntimePolicy"].load(
            _legacy_exact_split_timeout_policy_data()
        )
        operation_recovery_runtime.validate_exact_drain_provider_policy(
            historical_policy,
            plan_schema_version=14,
        )
        operation_recovery_runtime.validate_exact_drain_provider_policy(
            historical_policy,
            plan_schema_version=15,
        )

        legacy_value = _legacy_exact_split_timeout_policy_data()
        legacy_value["schema_version"] = 1
        for member in legacy_value["members"]:
            member.pop("execution_timeout_seconds")
            member.pop("queue_timeout_seconds")
            member["timeout_seconds"] = (
                1_200 if member["id"] == "hatchery" else None
            )
            if member["id"] == "hatchery":
                member["max_concurrent"] = 1
        legacy_policy = self.controller["ProviderRuntimePolicy"].load(
            legacy_value
        )
        operation_recovery_runtime.validate_exact_drain_provider_policy(
            legacy_policy,
            plan_schema_version=10,
        )
        with self.assertRaisesRegex(
            Exception,
            "provider policy differs",
        ):
            validate(
                replace(
                    bounded_policy,
                    members=tuple(
                        replace(
                            hatchery,
                            execution_timeout_seconds=300,
                            max_retries=1,
                        )
                        if member.id == hatchery.id
                        else member
                        for member in bounded_policy.members
                    ),
                )
            )

    def test_schema_fifteen_capability_probe_falls_back_to_luna(self):
        probe = self.controller[
            "_operation_recovery_provider_capability_receipt"
        ]
        globals_ = probe.__globals__
        policy = self.controller["ProviderRuntimePolicy"].load(
            _exact_split_timeout_policy_data()
        )
        expected = {"provider_id": "openai-luna"}
        calls = []
        replacements = {
            "_operation_recovery_hatchery_capability_receipt": (
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    self.controller["OperationRecoveryError"](
                        "closed Hatchery failure"
                    )
                )
            ),
            "_operation_recovery_luna_capability_receipt": (
                lambda *_args, **_kwargs: calls.append("openai-luna")
                or expected
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            observed = probe(
                policy,
                provider_policy_digest="9" * 64,
                manager=object(),
            )
        finally:
            globals_.update(originals)
        self.assertIs(observed, expected)
        self.assertEqual(calls, ["openai-luna"])

    def test_luna_capability_probe_uses_medium_reasoning_without_retaining_key(self):
        probe = self.controller["_operation_recovery_luna_capability_receipt"]
        globals_ = probe.__globals__
        policy = self.controller["ProviderRuntimePolicy"].load(
            _exact_split_timeout_policy_data()
        )
        requests = []

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://api.openai.com/v1/responses"

            def read(self, _limit):
                return b'{"id":"resp_test","status":"completed"}'

        replacements = {
            "_operation_recovery_resolve_provider_api_key": (
                lambda *_args, **_kwargs: "test-project-key"
            ),
            "urlopen": (
                lambda request, **_kwargs: requests.append(request)
                or Response()
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            receipt = probe(
                policy,
                provider_policy_digest="9" * 64,
                manager=object(),
            )
        finally:
            globals_.update(originals)
        self.assertEqual(receipt["provider_id"], "openai-luna")
        self.assertNotIn("test-project-key", json.dumps(receipt))
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer test-project-key",
        )
        body = json.loads(request.data)
        self.assertEqual(body["model"], "gpt-5.6-luna")
        self.assertEqual(body["reasoning"], {"effort": "medium"})
        self.assertIs(body["store"], False)

    def test_planner_resolves_luna_key_from_runtime_bound_private_file(self):
        resolve = self.controller[
            "_operation_recovery_resolve_provider_api_key"
        ]
        globals_ = resolve.__globals__
        observed = {}

        manager = SimpleNamespace(
            config=SimpleNamespace(
                state_root=Path("/Users/test/.local/state/hindsight-control-plane")
            )
        )
        def read_private(path, label):
            observed["path"] = path
            observed["label"] = label
            return b"HINDSIGHT_OPENAI_API_KEY=test-project-key\n"

        replacements = {"_operation_recovery_read_private_bytes": read_private}
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            key = resolve(
                manager,
                locator="api-key:hindsight-openai",
                marker="provider-policy:openai-luna",
            )
        finally:
            globals_.update(originals)
        self.assertEqual(key, "test-project-key")
        self.assertEqual(
            observed["path"],
            manager.config.state_root / ".hindsight-openai.env",
        )
        self.assertEqual(observed["label"], "Hindsight OpenAI API key")

    def test_planner_rejects_unbound_luna_key_locator(self):
        resolve = self.controller[
            "_operation_recovery_resolve_provider_api_key"
        ]
        manager = SimpleNamespace(
            config=SimpleNamespace(
                state_root=Path("/Users/test/.local/state/hindsight-control-plane")
            )
        )
        with self.assertRaisesRegex(
            self.controller["OperationRecoveryError"],
            "credential resolution failed",
        ):
            resolve(
                manager,
                locator="api-key:other-project",
                marker="provider-policy:openai-luna",
            )

    def test_exact_drain_effective_profile_requires_the_policy_projection(self):
        policy_path = (
            Path.home()
            / ".config/hindsight-control-plane/provider-runtime-policy.json"
        )
        evidence = self.controller[
            "_operation_recovery_exact_provider_policy_evidence"
        ]
        globals_ = evidence.__globals__
        body = json.dumps(
            _exact_split_timeout_policy_data(),
            separators=(",", ":"),
        ).encode("utf-8")
        original = globals_["_operation_recovery_read_private_bytes"]
        globals_["_operation_recovery_read_private_bytes"] = (
            lambda _path, _label: body
        )
        try:
            _policy_digest, policy = evidence(policy_path)
        finally:
            globals_["_operation_recovery_read_private_bytes"] = original
        profile = {
            "HINDSIGHT_API_LLM_STRATEGY": '{"mode":"round-robin"}',
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "openai",
            "HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL": (
                "text-embedding-3-small"
            ),
            "HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY": (
                "provider-policy:openai-luna"
            ),
            "HINDSIGHT_API_RERANKER_PROVIDER": "jina-mlx",
        }
        for position, member_id in enumerate(policy.failover_order):
            member = policy.member(member_id)
            prefix = (
                "HINDSIGHT_API_LLM"
                if position == 0
                else f"HINDSIGHT_API_LLM_{position}"
            )
            profile[f"{prefix}_PROVIDER"] = member.identity.provider
            profile[f"{prefix}_MODEL"] = member.identity.model
            if member.identity.base_url:
                profile[f"{prefix}_BASE_URL"] = member.identity.base_url
            if member.identity.credential_marker is not None:
                profile[f"{prefix}_API_KEY"] = (
                    member.identity.credential_marker
                )
            if member_id == "openai-luna":
                profile[f"{prefix}_REASONING_EFFORT"] = "medium"
        bind = self.controller["exact_drain_effective_profile_digest"]
        initial = bind(policy, profile)
        changed_effort = dict(profile)
        changed_effort["HINDSIGHT_API_LLM_REASONING_EFFORT"] = "high"
        self.assertNotEqual(bind(policy, changed_effort), initial)
        changed_provider = dict(profile)
        changed_provider["HINDSIGHT_API_LLM_PROVIDER"] = "claude-code"
        with self.assertRaisesRegex(
            Exception,
            "LLM profile differs",
        ):
            bind(policy, changed_provider)
        changed_embeddings = dict(profile)
        changed_embeddings["HINDSIGHT_API_EMBEDDINGS_PROVIDER"] = "local"
        with self.assertRaisesRegex(
            Exception,
            "LLM profile differs",
        ):
            bind(policy, changed_embeddings)
        changed_embeddings_model = dict(profile)
        changed_embeddings_model[
            "HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL"
        ] = "text-embedding-3-large"
        with self.assertRaisesRegex(
            Exception,
            "LLM profile differs",
        ):
            bind(policy, changed_embeddings_model)
        changed_embeddings_key = dict(profile)
        changed_embeddings_key[
            "HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY"
        ] = "provider-policy:other"
        with self.assertRaisesRegex(
            Exception,
            "LLM profile differs",
        ):
            bind(policy, changed_embeddings_key)
        changed_luna_effort = dict(profile)
        changed_luna_effort[
            "HINDSIGHT_API_LLM_5_REASONING_EFFORT"
        ] = "high"
        with self.assertRaisesRegex(
            Exception,
            "LLM profile differs",
        ):
            bind(policy, changed_luna_effort)
        changed_trace = dict(profile)
        changed_trace["HINDSIGHT_API_LLM_TRACE_ENABLED"] = "false"
        self.assertNotEqual(bind(policy, changed_trace), initial)
        changed_extraction_policy = dict(profile)
        changed_extraction_policy[
            "HINDSIGHT_API_FAIL_ON_EXTRACTION_ERRORS"
        ] = "true"
        with self.assertRaisesRegex(
            Exception,
            "LLM profile differs",
        ):
            bind(policy, changed_extraction_policy)

    def test_five_member_spark_profile_digest_remains_stable(self):
        policy = self.controller["ProviderRuntimePolicy"].load(
            _legacy_exact_split_timeout_policy_data()
        )
        profile = {
            "HINDSIGHT_API_LLM_STRATEGY": '{"mode":"round-robin"}',
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "openai-codex",
            "HINDSIGHT_API_RERANKER_PROVIDER": "jina-mlx",
        }
        for position, member_id in enumerate(policy.failover_order):
            member = policy.member(member_id)
            prefix = (
                "HINDSIGHT_API_LLM"
                if position == 0
                else f"HINDSIGHT_API_LLM_{position}"
            )
            profile[f"{prefix}_PROVIDER"] = member.identity.provider
            profile[f"{prefix}_MODEL"] = member.identity.model
            if member.identity.base_url:
                profile[f"{prefix}_BASE_URL"] = member.identity.base_url
            if member.identity.credential_marker is not None:
                profile[f"{prefix}_API_KEY"] = (
                    member.identity.credential_marker
                )

        observed = self.controller["exact_drain_effective_profile_digest"](
            policy,
            profile,
            plan_schema_version=15,
        )

        self.assertEqual(
            observed,
            "f20eb2683864403ee0717dd4cc74c52da51fa5fa73140a1cb606898d189bf214",
        )

    def test_exact_drain_apply_rechecks_the_rollback_backup_digest(self):
        verify = self.controller[
            "_operation_recovery_assert_exact_backup"
        ]
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-backup-",
        ) as directory:
            path = Path(directory) / "rollback.dump.age"
            path.write_bytes(b"approved-backup")
            path.chmod(0o600)
            plan = {
                "rollback_backup_path": str(path),
                "rollback_backup": {
                    "artifact_sha256": hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                },
            }
            verify(plan)
            path.write_bytes(b"changed-backup")
            with self.assertRaisesRegex(
                Exception,
                "rollback backup differs",
            ):
                verify(plan)

    def test_exact_drain_wrapper_adopts_candidate_modules_after_provider_bootstrap(self):
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-bootstrap-",
        ) as directory:
            fake_root = Path(directory) / "active-lib"
            package = fake_root / "hindsight_memory_control_plane"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            wrapper = ROOT / "bin" / "hindsight-exact-drain-worker"
            bootstrap = (
                "import runpy, sys; "
                f"sys.path.insert(0, {str(fake_root)!r}); "
                "import hindsight_memory_control_plane; "
                f"sys.argv = [{str(wrapper)!r}, '--help']; "
                f"runpy.run_path({str(wrapper)!r}, run_name='__main__')"
            )
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, "-S", "-c", bootstrap],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("hindsight-exact-drain-worker", result.stdout)

    def test_exact_drain_worker_records_preclaim_failure_without_raw_error(self):
        worker = runpy.run_path(
            str(ROOT / "bin" / "hindsight-exact-drain-worker")
        )
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-worker-failure-",
        ) as directory:
            path = Path(directory) / "progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": (
                            "00000000-0000-4000-8000-000000000001"
                        ),
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                progress_schema_version=3,
                clock=lambda: 1000.0,
            )
            globals_ = worker["run"].__globals__
            globals_["_WORKER_PROGRESS_RECORDER"] = recorder
            recorder.worker_stage(
                status="starting",
                stage="worker.memory.initialize",
            )

            def fail_before_claim():
                raise TimeoutError("provider socket closed secret-value")

            globals_["main"] = fail_before_claim
            self.assertEqual(worker["run"](), 2)
            progress = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(progress["worker_status"], "failed")
        self.assertEqual(
            progress["worker_failure_stage"],
            "worker.memory.initialize",
        )
        self.assertEqual(progress["worker_exit_code"], 2)
        self.assertEqual(
            progress["worker_failure"]["category"],
            "worker_initialization_timeout",
        )
        self.assertTrue(progress["worker_failure"]["retryable"])
        self.assertNotIn("secret-value", json.dumps(progress))

    def test_exact_drain_worker_uses_bound_legacy_failure_schema(self):
        worker = runpy.run_path(
            str(ROOT / "bin" / "hindsight-exact-drain-worker")
        )
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-worker-legacy-failure-",
        ) as directory:
            path = Path(directory) / "progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": (
                            "00000000-0000-4000-8000-000000000001"
                        ),
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                progress_schema_version=4,
                clock=lambda: 1000.0,
            )
            globals_ = worker["run"].__globals__
            globals_["_WORKER_PROGRESS_RECORDER"] = recorder
            recorder.worker_stage(
                status="starting",
                stage="worker.memory.initialize",
            )

            def fail_before_claim():
                raise RuntimeError("request timeout secret-value")

            globals_["main"] = fail_before_claim
            self.assertEqual(worker["run"](), 2)
            progress = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(progress["worker_status"], "failed")
        self.assertEqual(
            progress["worker_failure"]["category"],
            "provider_transport",
        )
        self.assertTrue(progress["worker_failure"]["retryable"])
        self.assertNotIn("secret-value", json.dumps(progress))

    def test_exact_drain_worker_gate_binds_parent_artifacts_before_activation(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-worker-gate-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verification.json"),
                created_at=now,
            )
            authorization = recovery_fixtures.exact_drain_authorization(
                plan,
                authorized_at=now,
            )
            worker_start_time = self.controller["_process_start_time"](
                os.getpid()
            )
            self.assertIsNotNone(worker_start_time)
            journal = self.controller["_operation_recovery_exact_receipt"](
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-application-journal"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "authorization_receipt_digest": authorization[
                        "receipt_digest"
                    ],
                    "started_at": authorization["authorized_at"],
                    "worker_pid": os.getpid(),
                    "worker_start_time": worker_start_time,
                    "worker_attempt": 1,
                }
            )
            plan_path = root / "plan.json"
            for path, value in (
                (plan_path, plan),
                (Path(plan["authorization_receipt_path"]), authorization),
                (Path(plan["application_receipt_path"]), journal),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                path.chmod(0o600)

            worker = runpy.run_path(
                str(ROOT / "bin" / "hindsight-exact-drain-worker")
            )
            activations = []

            def activate_runtime(*_arguments, **_keywords):
                activations.append("runtime")
                raise worker["OperationRecoveryError"]("runtime activated")

            worker["main"].__globals__["exact_drain_runtime_evidence"] = (
                activate_runtime
            )
            arguments = [
                "hindsight-exact-drain-worker",
                "--plan",
                str(plan_path),
                "--provider-policy",
                str(root / "provider-policy.json"),
                "--provider-runtime-root",
                str(root / "provider-runtime"),
                "--worker-runtime",
                str(root / "worker-runtime"),
            ]
            gate_prefix = b"exact-drain-start-v1 "

            def invoke(
                *,
                plan_digest=plan["plan_digest"],
                authorization_digest=authorization["receipt_digest"],
                journal_digest=journal["receipt_digest"],
                raw_message=None,
            ):
                start_read, start_write = os.pipe()
                environment = self.controller[
                    "_operation_recovery_exact_worker_environment"
                ](
                    {},
                    database_url="postgresql://local",
                    start_gate_descriptor=start_read,
                    plan_digest=plan_digest,
                    authorization_receipt_digest=authorization_digest,
                )
                message = raw_message or (
                    gate_prefix
                    + plan["plan_digest"].encode("ascii")
                    + b" "
                    + authorization["receipt_digest"].encode("ascii")
                    + b" "
                    + journal_digest.encode("ascii")
                    + b"\n"
                )
                os.write(start_write, message)
                os.close(start_write)
                try:
                    with (
                        patch.object(sys, "argv", arguments),
                        patch.dict(os.environ, environment, clear=True),
                    ):
                        worker["main"]()
                finally:
                    try:
                        os.close(start_read)
                    except OSError:
                        pass

            for changed, message in (
                ({"plan_digest": "0" * 64}, "environment differs"),
                (
                    {"authorization_digest": "1" * 64},
                    "environment differs",
                ),
                (
                    {"raw_message": b"exact-drain-start-v1 truncated\n"},
                    "start was not authorized",
                ),
                (
                    {"journal_digest": "2" * 64},
                    "application journal is invalid",
                ),
            ):
                with self.subTest(changed=changed), self.assertRaisesRegex(
                    worker["OperationRecoveryError"],
                    message,
                ):
                    invoke(**changed)
                self.assertEqual(activations, [])

            mismatched_journal = self.controller[
                "_operation_recovery_exact_receipt"
            ](
                {
                    key: value
                    for key, value in journal.items()
                    if key != "receipt_digest"
                }
                | {"worker_start_time": f"{worker_start_time}-reused"}
            )
            application_path = Path(plan["application_receipt_path"])
            application_path.write_text(
                json.dumps(mismatched_journal),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                worker["OperationRecoveryError"],
                "application journal is invalid",
            ):
                invoke(journal_digest=mismatched_journal["receipt_digest"])
            self.assertEqual(activations, [])
            application_path.write_text(json.dumps(journal), encoding="utf-8")

            with self.assertRaisesRegex(
                worker["OperationRecoveryError"],
                "runtime activated",
            ):
                invoke()
            self.assertEqual(activations, ["runtime"])

    def test_exact_drain_worker_uses_consumed_v2_authorization_after_approval_expiry(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        planned_at = now - 86_401
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-expired-approval-worker-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            authorization_path = root / "authorization.json"
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=planned_at),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(authorization_path),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verification.json"),
                created_at=planned_at,
            )
            authorization = recovery_fixtures.exact_drain_authorization(
                plan,
                authorized_at=plan["expires_at"] - 1,
            )
            authorization_bytes = json.dumps(
                authorization,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            plan_path.chmod(0o600)
            authorization_path.write_bytes(authorization_bytes)
            authorization_path.chmod(0o600)
            environment = dict(os.environ)
            environment["HOME"] = str(root)
            environment.pop("PYTHONPATH", None)
            wrapper = ROOT / "bin" / "hindsight-exact-drain-worker"

            result = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(wrapper),
                    "--plan",
                    str(plan_path),
                    "--provider-policy",
                    str(root / "provider-policy.json"),
                    "--provider-runtime-root",
                    str(root / "provider-runtime"),
                    "--worker-runtime",
                    str(root / "worker-runtime"),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("plan expired", result.stderr)
            self.assertNotIn("authorization receipt is invalid", result.stderr)
            self.assertIn("start gate is unavailable", result.stderr)
            self.assertEqual(authorization_path.read_bytes(), authorization_bytes)
            self.assertEqual(
                json.loads(authorization_path.read_bytes())["authorized_at"],
                plan["expires_at"] - 1,
            )

    def test_exact_drain_worker_bootstrap_imports_only_detached_candidate(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-bootstrap-candidate-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            candidate_release = root / "candidate-release"
            candidate_lib = candidate_release / "lib"
            candidate_package = candidate_lib / "hindsight_api"
            candidate_package.mkdir(parents=True, mode=0o700)
            active_release = root / "normal-active-release"
            active_package = active_release / "lib" / "hindsight_api"
            active_package.mkdir(parents=True, mode=0o700)
            candidate_sentinel = root / "candidate-imported"
            active_sentinel = root / "active-imported"
            resolver_sentinel = root / "normal-resolver-ran"
            (candidate_package / "__init__.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(candidate_sentinel)!r}).touch()\n",
                encoding="utf-8",
            )
            (active_package / "__init__.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(active_sentinel)!r}).touch()\n",
                encoding="utf-8",
            )
            worker_root = root / "worker-runtime"
            worker_bin = worker_root / "bin"
            worker_bin.mkdir(parents=True, mode=0o700)
            interpreter = worker_bin / "python3"
            interpreter.write_text("synthetic interpreter\n", encoding="utf-8")
            interpreter.chmod(0o700)
            worker_runtime = worker_bin / "hindsight-worker"
            worker_runtime.write_text(f"#!{interpreter}\n", encoding="utf-8")
            worker_runtime.chmod(0o700)
            (worker_root / "pyvenv.cfg").write_text("home = /private/tmp\n")
            dependency_root = (
                worker_root / "lib" / "python3.13" / "site-packages"
            )
            dependency_root.mkdir(parents=True, mode=0o700)
            (dependency_root / "trusted_dependency.py").write_text(
                "TRUSTED = True\n",
                encoding="utf-8",
            )
            policy_path = (
                root
                / ".config"
                / "hindsight-control-plane"
                / "provider-runtime-policy.json"
            )
            policy_path.parent.mkdir(parents=True, mode=0o700)
            policy_bytes = json.dumps(
                policy_data(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            policy_path.write_bytes(policy_bytes)
            policy_path.chmod(0o600)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest=hashlib.sha256(policy_bytes).hexdigest(),
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verification.json"),
                created_at=now,
            )
            authorization = recovery_fixtures.exact_drain_authorization(
                plan,
                authorized_at=now,
            )
            start_time = self.controller["_process_start_time"](os.getpid())
            self.assertIsNotNone(start_time)
            journal = self.controller["_operation_recovery_exact_receipt"](
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-application-journal"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "authorization_receipt_digest": authorization[
                        "receipt_digest"
                    ],
                    "started_at": authorization["authorized_at"],
                    "worker_pid": os.getpid(),
                    "worker_start_time": start_time,
                    "worker_attempt": 1,
                }
            )
            plan_path = root / "plan.json"
            for path, value in (
                (plan_path, plan),
                (Path(plan["authorization_receipt_path"]), authorization),
                (Path(plan["application_receipt_path"]), journal),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                path.chmod(0o600)
            bootstrap = (
                "import importlib.util\n"
                "import sys\n"
                "from pathlib import Path\n"
                "def _read_protected_file(path, label):\n"
                "    return b''\n"
                "def _resolve_active_release(install_root):\n"
                f"    Path({str(resolver_sentinel)!r}).touch()\n"
                f"    return Path({str(active_release)!r})\n"
                "if importlib.util.find_spec('hindsight_api') is not None:\n"
                "    release = _resolve_active_release(Path.home())\n"
                "    sys.path.insert(0, str(release / 'lib'))\n"
                "    import hindsight_api\n"
                "    raise RuntimeError('bootstrap sentinel complete')\n"
            ).encode("utf-8")
            start_read, start_write = os.pipe()
            environment = self.controller[
                "_operation_recovery_exact_worker_environment"
            ](
                {},
                database_url="postgresql://local",
                start_gate_descriptor=start_read,
                plan_digest=plan["plan_digest"],
                authorization_receipt_digest=authorization["receipt_digest"],
            )
            environment["HOME"] = str(root)
            environment["PYTHONPATH"] = str(candidate_lib)
            os.write(
                start_write,
                b"exact-drain-start-v1 "
                + plan["plan_digest"].encode("ascii")
                + b" "
                + authorization["receipt_digest"].encode("ascii")
                + b" "
                + journal["receipt_digest"].encode("ascii")
                + b"\n",
            )
            os.close(start_write)
            worker = runpy.run_path(
                str(ROOT / "bin" / "hindsight-exact-drain-worker")
            )
            globals_ = worker["main"].__globals__
            replacements = {
                "LIB": candidate_lib,
                "exact_drain_runtime_evidence": (
                    lambda *_arguments, **_keywords: ("8" * 64, bootstrap)
                ),
                "validate_exact_drain_provider_policy": (
                    lambda _policy, **_keywords: None
                ),
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: "7" * 64
                ),
                "validate_exact_drain_dependency_spec": (
                    lambda *_arguments: None
                ),
                "validate_exact_drain_import_origins": (
                    lambda *_arguments: None
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            previous_path = list(sys.path)
            previous_hindsight = {
                name: module
                for name, module in sys.modules.items()
                if name == "hindsight_api" or name.startswith("hindsight_api.")
            }
            for name in previous_hindsight:
                sys.modules.pop(name, None)
            globals_.update(replacements)
            arguments = [
                "hindsight-exact-drain-worker",
                "--plan",
                str(plan_path),
                "--provider-policy",
                str(policy_path),
                "--provider-runtime-root",
                str(candidate_lib / "exact_drain_runtime" / "provider"),
                "--worker-runtime",
                str(worker_runtime),
            ]
            try:
                with (
                    patch.object(sys, "argv", arguments),
                    patch.dict(os.environ, environment, clear=True),
                    self.assertRaisesRegex(
                        RuntimeError,
                        "bootstrap sentinel complete",
                    ),
                ):
                    worker["main"]()
            finally:
                globals_.update(originals)
                sys.path[:] = previous_path
                for name in tuple(sys.modules):
                    if name == "hindsight_api" or name.startswith(
                        "hindsight_api."
                    ):
                        sys.modules.pop(name, None)
                sys.modules.update(previous_hindsight)
                try:
                    os.close(start_read)
                except OSError:
                    pass
                from hindsight_memory_control_plane import provider_runtime

                provider_runtime.set_exact_drain_progress_recorder(None)

            self.assertTrue(candidate_sentinel.exists())
            self.assertFalse(active_sentinel.exists())
            self.assertFalse(resolver_sentinel.exists())

    def test_schema_thirteen_keeps_schema_twelve_runtime_safety_gates(self):
        worker = runpy.run_path(
            str(ROOT / "bin" / "hindsight-exact-drain-worker")
        )

        self.assertIn(13, worker["EXECUTION_LEASE_SCHEMA_VERSIONS"])
        self.assertIn(13, worker["DETACHED_RUNTIME_SCHEMA_VERSIONS"])
        self.assertIn(13, worker["UVICORN_SIGNAL_GUARD_SCHEMA_VERSIONS"])
        self.assertIn(
            13,
            self.controller["EXECUTION_LEASE_SCHEMA_VERSIONS"],
        )

    def test_terminal_reconciliation_worker_interface_is_unavailable(self):
        worker = runpy.run_path(
            str(ROOT / "bin" / "hindsight-exact-drain-worker")
        )
        with (
            patch.object(
                sys,
                "argv",
                [
                    "hindsight-exact-drain-worker",
                    "--plan",
                    "/private/tmp/plan.json",
                    "--provider-policy",
                    "/private/tmp/policy.json",
                    "--provider-runtime-root",
                    "/private/tmp/provider-runtime",
                    "--worker-runtime",
                    "/private/tmp/hindsight-worker",
                    "--resume",
                    "--terminal-reconciliation",
                ],
            ),
            self.assertRaises(SystemExit),
        ):
            worker["parser"]().parse_args()

    def test_schema_eleven_resume_worker_interface_is_unavailable(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="schema-eleven-worker-resume-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verification.json"),
                created_at=now,
                schema_version=11,
            )
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            plan_path.chmod(0o600)
            worker = runpy.run_path(
                str(ROOT / "bin" / "hindsight-exact-drain-worker")
            )
            arguments = [
                "hindsight-exact-drain-worker",
                "--plan",
                str(plan_path),
                "--provider-policy",
                str(root / "invalid-policy"),
                "--provider-runtime-root",
                str(root / "invalid-provider-runtime"),
                "--worker-runtime",
                str(root / "invalid-worker-runtime"),
                "--resume",
            ]
            with (
                patch.object(sys, "argv", arguments),
                self.assertRaisesRegex(
                    worker["OperationRecoveryError"],
                    "schema-11 interrupted exact drain requires post-abort recovery",
                ),
            ):
                worker["main"]()

    def test_expired_execution_lease_rejects_before_provider_activation(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        execution_window_seconds = fixtures.drain_plan()["execution_window"][
            "calculated_seconds"
        ]
        planned_at = int(time.time()) - execution_window_seconds - 1
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-expired-no-provider-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=planned_at),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verification.json"),
                created_at=planned_at,
            )
            authorization = recovery_fixtures.exact_drain_authorization(
                plan,
                authorized_at=planned_at,
            )
            plan_path = root / "plan.json"
            authorization_path = Path(plan["authorization_receipt_path"])
            authorization_bytes = json.dumps(
                authorization,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            plan_path.chmod(0o600)
            authorization_path.write_bytes(authorization_bytes)
            authorization_path.chmod(0o600)

            worker = runpy.run_path(
                str(ROOT / "bin" / "hindsight-exact-drain-worker")
            )
            provider_activation = []

            def fail_provider_activation(*_args, **_kwargs):
                provider_activation.append("provider-activation")
                raise AssertionError("provider activation executed")

            worker["main"].__globals__["exact_drain_runtime_evidence"] = (
                fail_provider_activation
            )
            arguments = [
                "hindsight-exact-drain-worker",
                "--plan",
                str(plan_path),
                "--provider-policy",
                str(root / "invalid-policy"),
                "--provider-runtime-root",
                str(root / "invalid-provider-runtime"),
                "--worker-runtime",
                str(root / "invalid-worker-runtime"),
                "--resume",
            ]
            with (
                patch.object(sys, "argv", arguments),
                self.assertRaisesRegex(
                    worker["OperationRecoveryError"],
                    "execution lease expired",
                ),
            ):
                worker["main"]()

            self.assertEqual(provider_activation, [])
            self.assertEqual(authorization_path.read_bytes(), authorization_bytes)

    def test_exact_drain_application_receipt_binds_authorization_and_status(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        plan = self.controller["create_exact_drain_plan"](
            fixtures.cohort(),
            fixtures.drain_snapshot(observed_at=now),
            candidate_release=recovery_fixtures.release_identity(),
            rollback_backup=recovery_fixtures.drain_backup_evidence(),
            rollback_backup_path="/private/tmp/drain-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path="/private/tmp/drain-auth.json",
            application_receipt_path="/private/tmp/drain-application.json",
            status_artifact_path="/private/tmp/drain-status.json",
            verification_receipt_path="/private/tmp/drain-verify.json",
            created_at=now,
        )
        make_receipt = self.controller["_operation_recovery_exact_receipt"]
        authorization = make_receipt(
            {
                "schema_version": 1,
                "kind": (
                    "operation-recovery-exact-drain-authorization-receipt"
                ),
                "plan_digest": plan["plan_digest"],
                "approval_digest": plan["plan_digest"],
                "candidate_release": plan["candidate_release"],
                "provider_policy_digest": plan["provider_policy_digest"],
                "worker_runtime_digest": plan["worker_runtime_digest"],
                "authorized_at": now,
            }
        )
        status_body = {
            "schema_version": 2,
            "kind": "operation-recovery-exact-drain-status",
            "plan_digest": plan["plan_digest"],
            "generation_before": "systalyze:public:200",
            "generation_after": "systalyze:public:200",
            "selected_operation_count": 43,
            "selected_status_counts": {"completed": 43},
            "preserved_status_counts": {"completed": 5},
            "outside_nonterminal_counts": [],
            "failure_classifications": [],
            "observed_at": now + 1,
        }
        terminal_status = {
            **status_body,
            "status_digest": self.controller["digest"](status_body),
        }
        journal_body = {
            "schema_version": 1,
            "kind": "operation-recovery-exact-drain-application-journal",
            "plan_digest": plan["plan_digest"],
            "authorization_receipt_digest": authorization[
                "receipt_digest"
            ],
            "started_at": now,
            "worker_pid": 12345,
            "worker_start_time": "2026-08-09T21:00:00.000000Z",
            "worker_attempt": 2,
        }
        application = make_receipt(
            {
                "schema_version": 1,
                "kind": "operation-recovery-exact-drain-application-receipt",
                "plan_digest": plan["plan_digest"],
                "candidate_release": plan["candidate_release"],
                "authorization_receipt_digest": authorization[
                    "receipt_digest"
                ],
                "application_journal_digest": self.controller["digest"](
                    journal_body
                ),
                "worker_runtime_digest": plan["worker_runtime_digest"],
                "provider_policy_digest": plan["provider_policy_digest"],
                "terminal_status_digest": terminal_status["status_digest"],
                "terminal_progress_digest": "d" * 64,
                "selected_status_counts": {"completed": 43},
                "outside_nonterminal_counts": [],
                "worker_pid": journal_body["worker_pid"],
                "worker_start_time": journal_body[
                    "worker_start_time"
                ],
                "worker_attempt": journal_body["worker_attempt"],
                "started_at": now,
                "completed_at": now + 2,
            }
        )
        validate = self.controller["_operation_recovery_exact_application"]
        self.assertEqual(
            validate(
                application,
                plan=plan,
                authorization=authorization,
                terminal_status=terminal_status,
            ),
            application,
        )
        journal = make_receipt(journal_body)
        terminal_progress = {
            "plan_digest": plan["plan_digest"],
            "progress_digest": "d" * 64,
            "worker_pid": journal["worker_pid"],
            "worker_start_time": journal["worker_start_time"],
            "worker_attempt": journal["worker_attempt"],
            "observed_at": now + 2,
            "selected_status_counts": {"completed": 43},
            "active_provider_requests": [],
            "tasks": [
                {
                    "operation_id": item["operation_id"],
                    "operation_type": item["operation_type"],
                    "row_digest": item["row_digest"],
                }
                for item in plan["selected_operations"]
            ],
        }
        derive_terminal = self.controller[
            "_operation_recovery_terminal_application_evidence"
        ]
        with patch.dict(
            derive_terminal.__globals__,
            {
                "_operation_recovery_exact_journal_worker_active": (
                    lambda _journal: False
                ),
                "read_exact_drain_progress": (
                    lambda _path, *, plan_digest, progress_schema_version=1: (
                        dict(terminal_progress)
                    )
                ),
            },
        ):
            derived = derive_terminal(
                journal,
                plan=plan,
                authorization=authorization,
                terminal_status=terminal_status,
            )
        self.assertEqual(
            derived["kind"],
            "operation-recovery-exact-drain-application-receipt",
        )
        self.assertEqual(
            derived["application_journal_digest"],
            journal["receipt_digest"],
        )
        self.assertEqual(
            derived["terminal_progress_digest"],
            terminal_progress["progress_digest"],
        )
        self.assertEqual(derived["completed_at"], now + 2)
        forged = dict(application)
        forged["authorization_receipt_digest"] = "0" * 64
        forged["receipt_digest"] = self.controller["digest"](
            {
                key: value
                for key, value in forged.items()
                if key != "receipt_digest"
            }
        )
        with self.assertRaisesRegex(
            Exception,
            "application receipt is invalid",
        ):
            validate(
                forged,
                plan=plan,
                authorization=authorization,
                terminal_status=terminal_status,
            )
        reconciliation_journal_body = {
            **journal_body,
            "worker_attempt": plan["worker_max_attempts"] + 1,
        }
        reconciliation_journal = {
            **reconciliation_journal_body,
            "receipt_digest": self.controller["digest"](
                reconciliation_journal_body
            ),
        }
        self.assertEqual(
            self.controller["_operation_recovery_exact_journal"](
                reconciliation_journal,
                plan=plan,
                authorization=authorization,
            ),
            reconciliation_journal,
        )
        excessive_journal_body = {
            **journal_body,
            "worker_attempt": plan["worker_max_attempts"] + 2,
        }
        excessive_journal = {
            **excessive_journal_body,
            "receipt_digest": self.controller["digest"](
                excessive_journal_body
            ),
        }
        with self.assertRaisesRegex(
            Exception,
            "application journal is invalid",
        ):
            self.controller["_operation_recovery_exact_journal"](
                excessive_journal,
                plan=plan,
                authorization=authorization,
            )
        outside_processing = dict(application)
        outside_processing["outside_nonterminal_counts"] = [
            {
                "bank_id": "engineering",
                "operation_type": "retain",
                "status": "processing",
                "operation_count": 1,
            }
        ]
        outside_processing["receipt_digest"] = self.controller["digest"](
            {
                key: value
                for key, value in outside_processing.items()
                if key != "receipt_digest"
            }
        )
        with self.assertRaisesRegex(
            Exception,
            "application receipt is invalid",
        ):
            validate(
                outside_processing,
                plan=plan,
                authorization=authorization,
                terminal_status={
                    **terminal_status,
                    "outside_nonterminal_counts": outside_processing[
                        "outside_nonterminal_counts"
                    ],
                },
            )
        excessive_attempt = dict(application)
        excessive_attempt["worker_attempt"] = plan["worker_max_attempts"] + 1
        excessive_attempt["receipt_digest"] = self.controller["digest"](
            {
                key: value
                for key, value in excessive_attempt.items()
                if key != "receipt_digest"
            }
        )
        with self.assertRaisesRegex(
            Exception,
            "application receipt is invalid",
        ):
            validate(
                excessive_attempt,
                plan=plan,
                authorization=authorization,
                terminal_status=terminal_status,
            )

    def test_schema_fifteen_artifacts_carry_the_standing_grant_chain(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = 1_785_462_000
        _reference, grant, plan, _create_plan = fixtures.standing_grant_fixture()
        ledger = self.controller["create_exact_drain_grant_ledger"](
            grant,
            ledger_nonce="1" * 64,
            created_at=now,
        )
        _ledger, use = self.controller["claim_exact_drain_grant"](
            ledger,
            plan,
            expected_ledger_digest=ledger["ledger_digest"],
            claim_nonce="2" * 64,
            ledger_nonce="3" * 64,
            claimed_at=now,
        )
        authorization = self.controller[
            "create_exact_drain_grant_authorization_receipt"
        ](plan, use)
        make_receipt = self.controller["_operation_recovery_exact_receipt"]
        journal_body = {
            "schema_version": 2,
            "kind": "operation-recovery-exact-drain-application-journal",
            "plan_digest": plan["plan_digest"],
            "authorization_receipt_digest": authorization["receipt_digest"],
            "grant_id": grant["grant_id"],
            "grant_digest": grant["grant_digest"],
            "started_at": authorization["authorized_at"],
            "worker_pid": 12345,
            "worker_start_time": "2026-08-23T18:00:00.000000Z",
            "worker_attempt": 1,
        }
        journal = make_receipt(journal_body)
        self.assertEqual(
            self.controller["_operation_recovery_exact_journal"](
                journal,
                plan=plan,
                authorization=authorization,
            ),
            journal,
        )
        terminal_status = {
            "selected_status_counts": {
                "completed": plan["selected_operation_count"]
            },
            "preserved_status_counts": plan["preserved_status_counts"],
            "outside_nonterminal_counts": [],
            "status_digest": "4" * 64,
        }
        application = make_receipt(
            {
                "schema_version": 2,
                "kind": "operation-recovery-exact-drain-application-receipt",
                "plan_digest": plan["plan_digest"],
                "candidate_release": plan["candidate_release"],
                "authorization_receipt_digest": authorization[
                    "receipt_digest"
                ],
                "application_journal_digest": journal["receipt_digest"],
                "worker_runtime_digest": plan["worker_runtime_digest"],
                "provider_policy_digest": plan["provider_policy_digest"],
                "grant_id": grant["grant_id"],
                "grant_digest": grant["grant_digest"],
                "terminal_status_digest": terminal_status["status_digest"],
                "terminal_progress_digest": "5" * 64,
                "selected_status_counts": terminal_status[
                    "selected_status_counts"
                ],
                "outside_nonterminal_counts": [],
                "worker_pid": journal["worker_pid"],
                "worker_start_time": journal["worker_start_time"],
                "worker_attempt": journal["worker_attempt"],
                "started_at": authorization["authorized_at"],
                "completed_at": authorization["authorized_at"] + 1,
            }
        )
        checked_application = self.controller[
            "_operation_recovery_exact_application"
        ](
            application,
            plan=plan,
            authorization=authorization,
            terminal_status=terminal_status,
        )
        expired_application = dict(application)
        expired_application["completed_at"] = authorization["expires_at"]
        expired_application["receipt_digest"] = self.controller["digest"](
            {
                key: value
                for key, value in expired_application.items()
                if key != "receipt_digest"
            }
        )
        with self.assertRaisesRegex(
            Exception,
            "exact drain application receipt is invalid",
        ):
            self.controller["_operation_recovery_exact_application"](
                expired_application,
                plan=plan,
                authorization=authorization,
                terminal_status=terminal_status,
            )
        live = {
            "selected_status_counts": terminal_status[
                "selected_status_counts"
            ],
            "preserved_status_counts": plan["preserved_status_counts"],
            "outside_nonterminal_counts": [],
        }
        verification = make_receipt(
            {
                "schema_version": 2,
                "kind": "operation-recovery-exact-drain-verification-receipt",
                "plan_digest": plan["plan_digest"],
                "application_receipt_digest": application["receipt_digest"],
                "grant_id": grant["grant_id"],
                "grant_digest": grant["grant_digest"],
                "terminal_status_digest": application[
                    "terminal_status_digest"
                ],
                "terminal_progress_digest": application[
                    "terminal_progress_digest"
                ],
                "selected_status_counts": live["selected_status_counts"],
                "preserved_status_counts": live["preserved_status_counts"],
                "outside_nonterminal_counts": [],
                "successful": True,
                "verified_at": application["completed_at"],
            }
        )
        self.assertEqual(
            self.controller["_operation_recovery_exact_verification"](
                verification,
                plan=plan,
                application=checked_application,
                live=live,
            ),
            verification,
        )

    def test_schema_fifteen_claim_recovers_after_ledger_receipt_crash(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-grant-crash-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            state_root = root / "state"
            ledger_parent = state_root / "operation-recovery"
            ledger_parent.mkdir(parents=True, mode=0o700)
            now = int(time.time()) - 1
            _reference, grant, plan, _create_plan = (
                fixtures.standing_grant_fixture(
                    created_at=now,
                    artifact_root=str(root),
                )
            )
            manager = SimpleNamespace(
                config=SimpleNamespace(state_root=state_root)
            )
            ledger_path = self.controller[
                "_operation_recovery_exact_grant_ledger_path"
            ](manager)
            ledger = self.controller["create_exact_drain_grant_ledger"](
                grant,
                ledger_nonce="1" * 64,
                created_at=now,
            )
            real_write = self.controller["write_private"]
            real_write(ledger_path, ledger, create_only=True)
            claim = self.controller[
                "_operation_recovery_claim_exact_grant"
            ]
            globals_ = claim.__globals__

            def crash_before_receipt(path, value, *, create_only=False):
                if Path(path) == Path(plan["authorization_receipt_path"]):
                    raise OSError("synthetic receipt crash")
                return real_write(path, value, create_only=create_only)

            original_write = globals_["write_private"]
            globals_["write_private"] = crash_before_receipt
            try:
                with self.assertRaisesRegex(OSError, "synthetic receipt crash"):
                    claim(manager, plan)
            finally:
                globals_["write_private"] = original_write

            claimed = self.controller["verify_exact_drain_grant_ledger"](
                self.controller["_operation_recovery_read_private_json"](
                    ledger_path,
                    "exact drain authorization grant ledger",
                )
            )
            self.assertEqual(claimed["revision"], 1)
            self.assertFalse(Path(plan["authorization_receipt_path"]).exists())

            authorization = claim(manager, plan)
            recovered = self.controller["verify_exact_drain_grant_ledger"](
                self.controller["_operation_recovery_read_private_json"](
                    ledger_path,
                    "exact drain authorization grant ledger",
                )
            )
            self.assertEqual(recovered["revision"], 1)
            self.assertEqual(
                authorization["grant_use_record_digest"],
                recovered["use_records"][0]["record_digest"],
            )
            revoked, _revocation = self.controller[
                "revoke_exact_drain_grant"
            ](
                recovered,
                approval_digest=grant["grant_digest"],
                expected_ledger_digest=recovered["ledger_digest"],
                revocation_nonce="4" * 64,
                ledger_nonce="5" * 64,
            )
            real_write(ledger_path, revoked)
            read_authorization = self.controller[
                "_operation_recovery_exact_authorization"
            ]
            with self.assertRaisesRegex(Exception, "grant revoked"):
                read_authorization(plan, manager=manager)
            self.assertEqual(
                read_authorization(
                    plan,
                    manager=manager,
                    require_active_grant=False,
                ),
                authorization,
            )

    def test_schema_fifteen_replaces_only_the_exact_plan_prompt(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        legacy, _grant, granted, _create_plan = (
            fixtures.standing_grant_fixture(created_at=now)
        )
        command = self.controller["operation_recovery_drain_apply_command"]
        globals_ = command.__globals__
        originals = {
            "_operation_recovery_candidate": globals_[
                "_operation_recovery_candidate"
            ],
            "_operation_recovery_read_private_json": globals_[
                "_operation_recovery_read_private_json"
            ],
        }
        selected = granted
        globals_["_operation_recovery_candidate"] = (
            lambda _args: selected["candidate_release"]
        )
        globals_["_operation_recovery_read_private_json"] = (
            lambda _path, _label: selected
        )
        try:
            with self.assertRaisesRegex(
                Exception,
                "schema-15 exact drain uses its standing grant",
            ):
                command(
                    SimpleNamespace(
                        plan="granted.json",
                        approval_digest=granted["plan_digest"],
                    )
                )
            selected = legacy
            with self.assertRaisesRegex(
                Exception,
                "exact drain approval differs",
            ):
                command(
                    SimpleNamespace(
                        plan="legacy.json",
                        approval_digest=None,
                    )
                )
        finally:
            globals_.update(originals)

    def test_exact_drain_grant_commands_activate_status_and_revoke(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-grant-commands-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            now = int(time.time())
            reference, _grant, _plan, _create_plan = (
                fixtures.standing_grant_fixture(
                    created_at=now,
                    artifact_root=str(root),
                )
            )
            reference_path = root / "reference.json"
            grant_plan_path = root / "grant-plan.json"
            real_write = self.controller["write_private"]
            real_write(reference_path, reference, create_only=True)

            class Manager:
                config = SimpleNamespace(state_root=root / "state")

                def _lock(self):
                    return nullcontext()

            manager = Manager()
            candidate = reference["candidate_release"]
            commands = [
                self.controller["operation_recovery_drain_grant_plan_command"],
                self.controller[
                    "operation_recovery_drain_grant_approve_command"
                ],
                self.controller[
                    "operation_recovery_drain_grant_status_command"
                ],
                self.controller[
                    "operation_recovery_drain_grant_revoke_command"
                ],
            ]
            globals_ = commands[0].__globals__
            originals = {
                "_operation_recovery_candidate": globals_[
                    "_operation_recovery_candidate"
                ],
                "_portable_manager": globals_["_portable_manager"],
                "_print_result": globals_["_print_result"],
            }
            globals_["_operation_recovery_candidate"] = lambda _args: candidate
            globals_["_portable_manager"] = lambda _args: manager
            globals_["_print_result"] = lambda value: value
            try:
                planned = commands[0](
                    SimpleNamespace(
                        reference_plan=str(reference_path),
                        grant_id="77777777-7777-4777-8777-777777777777",
                        maximum_recovery_epoch=3,
                        maximum_reconciliation_cycle=1,
                        maximum_plan_claims=2,
                        maximum_worker_attempts=(
                            (reference["worker_max_attempts"] + 1) * 2
                        ),
                        maximum_execution_seconds=(
                            reference["execution_window"][
                                "calculated_seconds"
                            ]
                            * 2
                        ),
                        maximum_concurrent_drains=1,
                        expires_at=now + 3_600,
                        output=str(grant_plan_path),
                    )
                )
                approved = commands[1](
                    SimpleNamespace(
                        plan=str(grant_plan_path),
                        approval_digest=planned["grant_plan_digest"],
                    )
                )
                active = commands[2](SimpleNamespace())
                revoked = commands[3](
                    SimpleNamespace(
                        approval_digest=approved["grant_digest"]
                    )
                )
                terminal = commands[2](SimpleNamespace())
            finally:
                globals_.update(originals)

            ledger_path = Path(approved["ledger"])
            self.assertEqual(active["status"], "active")
            self.assertEqual(revoked["status"], "revoked")
            self.assertEqual(terminal["status"], "revoked")
            self.assertEqual(stat.S_IMODE(ledger_path.stat().st_mode), 0o600)

    def test_exact_drain_status_and_verify_share_the_recovery_lock(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-command-locks-",
        ) as directory:
            root = Path(directory)
            now = int(time.time())
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=now,
            )
            events = []

            class Tracked:
                def __init__(self, name):
                    self.name = name

                def __enter__(self):
                    events.append(f"{self.name}-enter")

                def __exit__(self, *_arguments):
                    events.append(f"{self.name}-exit")

            class Manager:
                def _lock(self):
                    return Tracked("manager")

            args = SimpleNamespace(plan="plan.json")
            commands = (
                self.controller["operation_recovery_drain_status_command"],
                self.controller["operation_recovery_drain_verify_command"],
            )
            for command in commands:
                globals_ = command.__globals__
                replacements = {
                    "_operation_recovery_candidate": (
                        lambda _args: plan["candidate_release"]
                    ),
                    "_operation_recovery_read_private_json": (
                        lambda _path, _label: plan
                    ),
                    "_portable_manager": lambda _args: Manager(),
                    "_operation_recovery_lock": (
                        lambda _manager: Tracked("recovery")
                    ),
                    "_operation_recovery_drain_verify_locked": (
                        lambda _args, _plan: events.append("verify-body") or 0
                    ),
                    "_operation_recovery_read_exact_drain_status": (
                        lambda _args, _plan: _locked_status(events, plan)
                    ),
                    "write_private": (
                        lambda *_arguments, **_keywords: events.append(
                            "status-write"
                        )
                    ),
                    "_print_result": lambda value: value,
                }
                originals = {
                    key: globals_[key]
                    for key in replacements
                }
                globals_.update(replacements)
                events.clear()
                try:
                    command(args)
                finally:
                    globals_.update(originals)
                self.assertEqual(events[:2], [
                    "recovery-enter",
                    "manager-enter",
                ])
                self.assertEqual(events[-2:], [
                    "manager-exit",
                    "recovery-exit",
                ])
                self.assertTrue(
                    "verify-body" in events or "status-write" in events
                )

    def test_interrupted_status_rejects_a_missing_progress_artifact(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        plan = fixtures.drain_plan(schema_version=12)
        journal = {
            "kind": "operation-recovery-exact-drain-application-journal",
            "worker_pid": 4242,
            "worker_start_time": "inactive-worker",
            "worker_attempt": 2,
        }
        reader = self.controller[
            "_operation_recovery_read_exact_drain_status"
        ]
        globals_ = reader.__globals__
        replacements = {
            "_operation_recovery_read_private_json": (
                lambda _path, _label: journal
            ),
            "_operation_recovery_exact_authorization": (
                lambda *_arguments, **_keywords: {"authorized": True}
            ),
            "_operation_recovery_exact_journal": (
                lambda *_arguments, **_keywords: journal
            ),
            "_operation_recovery_exact_journal_worker_active": (
                lambda _journal: False
            ),
            "_operation_recovery_connect_live": (
                lambda _args: (_ for _ in ()).throw(
                    AssertionError("database opened without progress evidence")
                )
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        original_exists = Path.exists

        def fixture_exists(path):
            if str(path) == plan["application_receipt_path"]:
                return True
            if str(path) == plan["progress_artifact_path"]:
                return False
            return original_exists(path)

        try:
            with (
                patch.object(Path, "exists", fixture_exists),
                self.assertRaisesRegex(
                    self.controller["OperationRecoveryError"],
                    "interrupted progress is unavailable",
                ),
            ):
                asyncio.run(reader(SimpleNamespace(), plan))
        finally:
            globals_.update(originals)

    def test_post_abort_descendant_materializes_missing_reference_status(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        plan = fixtures.drain_plan(schema_version=12)
        live = {
            "plan_digest": plan["plan_digest"],
            "selected_status_counts": {"failed": 1, "pending": 42},
            "status_digest": "6" * 64,
        }
        helper = self.controller[
            "_operation_recovery_post_abort_reference_status"
        ]
        globals_ = helper.__globals__
        writes = []
        replacements = {
            "_operation_recovery_read_exact_drain_status": (
                lambda _args, _plan: _immediate(live)
            ),
            "verify_exact_drain_status": (
                lambda value, *, plan: (
                    value
                    if plan["plan_digest"] == value["plan_digest"]
                    else (_ for _ in ()).throw(AssertionError("wrong plan"))
                )
            ),
            "write_private": (
                lambda path, value, *, create_only=False: writes.append(
                    (str(path), value, create_only)
                )
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            with patch.object(Path, "exists", lambda _path: False):
                observed = helper(SimpleNamespace(), plan)
                globals_["write_private"] = (
                    lambda *_arguments, **_keywords: (
                        _ for _ in ()
                    ).throw(FileExistsError("concurrent status winner"))
                )
                with self.assertRaises(FileExistsError):
                    helper(SimpleNamespace(), plan)
        finally:
            globals_.update(originals)

        self.assertEqual(observed, live)
        self.assertEqual(
            writes,
            [(plan["status_artifact_path"], live, True)],
        )

    def test_exact_drain_status_reuses_the_outer_installer_lock(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        plan = self.controller["create_exact_drain_plan"](
            fixtures.cohort(),
            fixtures.drain_snapshot(observed_at=now),
            candidate_release=recovery_fixtures.release_identity(),
            rollback_backup=recovery_fixtures.drain_backup_evidence(),
            rollback_backup_path="/private/tmp/backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path="/private/tmp/auth.json",
            application_receipt_path="/private/tmp/application.json",
            status_artifact_path="/private/tmp/status.json",
            verification_receipt_path="/private/tmp/verify.json",
            created_at=now,
        )
        installer_lock_active = False
        installer_lock_entries = 0
        authority_lock_modes = []

        class InstallerLock:
            def __enter__(self):
                nonlocal installer_lock_active, installer_lock_entries
                if installer_lock_active:
                    raise AssertionError("recursive installer lock")
                installer_lock_active = True
                installer_lock_entries += 1

            def __exit__(self, *_arguments):
                nonlocal installer_lock_active
                installer_lock_active = False

        class Manager:
            def _lock(self):
                return InstallerLock()

        class Connection:
            async def fetchval(self, _query):
                return plan["installation_authority"][
                    "postgres_system_identifier"
                ]

            async def close(self):
                return None

        async def connect(_args):
            return Connection()

        async def read_status(*_arguments, **_keywords):
            return {
                "generation_before": plan["pre_generation"],
                "selected_status_counts": {"pending": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
                "status_digest": "6" * 64,
            }

        manager = Manager()

        def authority(
            _args,
            *,
            postgres_system_identifier,
            lock_installer=True,
        ):
            self.assertEqual(
                postgres_system_identifier,
                plan["installation_authority"][
                    "postgres_system_identifier"
            ],
        )

            authority_lock_modes.append(lock_installer)
            if lock_installer:
                with manager._lock():
                    pass
            return plan["installation_authority"]

        command = self.controller[
            "operation_recovery_drain_status_command"
        ]
        globals_ = command.__globals__
        replacements = {
            "_operation_recovery_candidate": (
                lambda _args: plan["candidate_release"]
            ),
            "_operation_recovery_read_private_json": (
                lambda _path, _label: plan
            ),
            "_portable_manager": lambda _args: manager,
            "_operation_recovery_lock": lambda _manager: nullcontext(),
            "_operation_recovery_connect_live": connect,
            "_operation_recovery_authority": authority,
            "read_exact_drain_status": read_status,
            "write_private": lambda *_arguments, **_keywords: None,
            "_print_result": lambda value: value,
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            result = command(SimpleNamespace(plan="plan.json"))
        finally:
            globals_.update(originals)

        self.assertEqual(result["status"], "awaiting-approval")
        self.assertEqual(result["execution_lease_status"], "not-authorized")
        self.assertIsNone(result["execution_lease_started_at"])
        self.assertIsNone(result["execution_lease_expires_at"])
        self.assertIsNone(result["execution_lease_remaining_seconds"])
        self.assertEqual(installer_lock_entries, 1)
        self.assertEqual(authority_lock_modes, [False, False])

    def test_post_abort_apply_rejects_active_reference_worker_under_both_locks(self):
        command = self.controller["operation_recovery_post_abort_apply_command"]
        globals_ = command.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-active-reference-worker-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self._post_abort_plan(root)
            documents = {
                "plan.json": plan,
                plan["reference_plan"]["authorization_receipt_path"]: plan[
                    "reference_application_authorization"
                ],
                plan["reference_plan"]["application_receipt_path"]: plan[
                    "reference_application_journal"
                ],
            }
            lock_state = {"recovery": False, "install": False}
            lock_events = []

            class TrackedLock:
                def __init__(self, name):
                    self.name = name

                def __enter__(self):
                    lock_state[self.name] = True
                    lock_events.append(f"{self.name}-enter")

                def __exit__(self, *_arguments):
                    lock_events.append(f"{self.name}-exit")
                    lock_state[self.name] = False

            class Manager:
                pass

            def worker_active(_journal):
                self.assertTrue(lock_state["recovery"])
                self.assertTrue(lock_state["install"])
                return True

            forbidden = Mock(
                side_effect=AssertionError(
                    "active reference worker gate ran too late"
                )
            )
            authority = Mock(return_value=plan["installation_authority"])
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_precommit_artifacts": (
                    lambda: nullcontext(
                        {"mutation_attempted": False, "created": []}
                    )
                ),
                "_operation_recovery_lock": (
                    lambda _manager, **_kwargs: TrackedLock("recovery")
                ),
                "_operation_recovery_install_lock": (
                    lambda _manager, **_kwargs: TrackedLock("install")
                ),
                "_operation_recovery_exact_journal_worker_active": (
                    worker_active
                ),
                "_operation_recovery_post_abort_reference_progress_digest": (
                    lambda _reference_plan, _reference_journal: "c" * 64
                ),
                "_operation_recovery_authority": authority,
                "_operation_recovery_prepare_apply": forbidden,
                "_operation_recovery_post_abort_apply": forbidden,
                "write_private": forbidden,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "exact drain worker remains active",
                ):
                    command(
                        SimpleNamespace(
                            plan="plan.json",
                            approval_digest=plan["plan_digest"],
                        )
                    )
            finally:
                globals_.update(originals)

            self.assertEqual(
                lock_events,
                [
                    "recovery-enter",
                    "install-enter",
                    "install-exit",
                    "recovery-exit",
                ],
            )
            authority.assert_called_once()
            forbidden.assert_not_called()

    def test_post_abort_apply_rejects_reference_source_drift_under_both_locks(self):
        command = self.controller["operation_recovery_post_abort_apply_command"]
        globals_ = command.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-reference-source-drift-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self._post_abort_plan(root)
            source_authorization = deepcopy(
                plan["reference_application_authorization"]
            )
            source_authorization["authorized_at"] += 1
            source_authorization["receipt_digest"] = self.controller["digest"](
                {
                    key: value
                    for key, value in source_authorization.items()
                    if key != "receipt_digest"
                }
            )
            authorization_journal = deepcopy(
                plan["reference_application_journal"]
            )
            authorization_journal["authorization_receipt_digest"] = (
                source_authorization["receipt_digest"]
            )
            authorization_journal["started_at"] = source_authorization[
                "authorized_at"
            ]
            authorization_journal["receipt_digest"] = self.controller[
                "digest"
            ](
                {
                    key: value
                    for key, value in authorization_journal.items()
                    if key != "receipt_digest"
                }
            )
            source_journal = deepcopy(plan["reference_application_journal"])
            source_journal["worker_start_time"] = "different-dead-worker"
            source_journal["receipt_digest"] = self.controller["digest"](
                {
                    key: value
                    for key, value in source_journal.items()
                    if key != "receipt_digest"
                }
            )
            cases = {
                "authorization": (
                    source_authorization,
                    authorization_journal,
                    "c" * 64,
                ),
                "journal": (
                    plan["reference_application_authorization"],
                    source_journal,
                    "c" * 64,
                ),
                "progress": (
                    plan["reference_application_authorization"],
                    plan["reference_application_journal"],
                    "d" * 64,
                ),
            }
            for label, (authorization, journal, progress_digest) in cases.items():
                lock_state = {"recovery": False, "install": False}

                class TrackedLock:
                    def __init__(self, name, state=lock_state):
                        self.name = name
                        self.state = state

                    def __enter__(self):
                        self.state[self.name] = True

                    def __exit__(self, *_arguments):
                        self.state[self.name] = False

                class Manager:
                    pass

                forbidden = Mock(
                    side_effect=AssertionError(
                        "reference source gate ran too late"
                    )
                )
                authority = Mock(
                    return_value=plan["installation_authority"]
                )
                documents = {
                    "plan.json": plan,
                    plan["reference_plan"][
                        "authorization_receipt_path"
                    ]: authorization,
                    plan["reference_plan"][
                        "application_receipt_path"
                    ]: journal,
                }
                replacements = {
                    "_operation_recovery_candidate": (
                        lambda _args: plan["candidate_release"]
                    ),
                    "_operation_recovery_read_private_json": (
                        lambda path, _label, documents=documents: documents[
                            str(path)
                        ]
                    ),
                    "_portable_manager": lambda _args: Manager(),
                    "_operation_recovery_precommit_artifacts": (
                        lambda: nullcontext(
                            {"mutation_attempted": False, "created": []}
                        )
                    ),
                    "_operation_recovery_lock": (
                        lambda _manager, **_kwargs: TrackedLock("recovery")
                    ),
                    "_operation_recovery_install_lock": (
                        lambda _manager, **_kwargs: TrackedLock("install")
                    ),
                    "_operation_recovery_exact_journal_worker_active": (
                        lambda _journal: False
                    ),
                    "_operation_recovery_post_abort_reference_progress_digest": (
                        lambda _reference_plan, _reference_journal,
                        value=progress_digest: value
                    ),
                    "_operation_recovery_authority": authority,
                    "_operation_recovery_prepare_apply": forbidden,
                    "_operation_recovery_post_abort_apply": forbidden,
                    "write_private": forbidden,
                }
                originals = {key: globals_[key] for key in replacements}
                globals_.update(replacements)
                try:
                    with (
                        self.subTest(source=label),
                        self.assertRaisesRegex(
                            Exception,
                            "reference exact drain artifacts drifted",
                        ),
                    ):
                        command(
                            SimpleNamespace(
                                plan="plan.json",
                                approval_digest=plan["plan_digest"],
                            )
                        )
                finally:
                    globals_.update(originals)
                self.assertEqual(
                    lock_state,
                    {"recovery": False, "install": False},
                )
                authority.assert_called_once()
                forbidden.assert_not_called()

    def test_post_abort_apply_rechecks_reference_source_before_mutation(self):
        command = self.controller["operation_recovery_post_abort_apply_command"]
        globals_ = command.__globals__
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-reference-pre-mutation-drift-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self._post_abort_plan(root)
            drifted_journal = deepcopy(
                plan["reference_application_journal"]
            )
            drifted_journal["worker_start_time"] = "later-dead-worker"
            drifted_journal["receipt_digest"] = self.controller["digest"](
                {
                    key: value
                    for key, value in drifted_journal.items()
                    if key != "receipt_digest"
                }
            )
            journal_reads = 0
            written = {}
            mutation = Mock(
                side_effect=AssertionError("source recheck ran too late")
            )

            def read(path, _label):
                nonlocal journal_reads
                path = str(path)
                if path == "plan.json":
                    return plan
                if path == plan["reference_plan"][
                    "authorization_receipt_path"
                ]:
                    return plan["reference_application_authorization"]
                if path == plan["reference_plan"][
                    "application_receipt_path"
                ]:
                    journal_reads += 1
                    return (
                        plan["reference_application_journal"]
                        if journal_reads == 1
                        else drifted_journal
                    )
                raise AssertionError(f"unexpected read: {path}")

            class Manager:
                pass

            class Precommit:
                def __enter__(self):
                    return {"mutation_attempted": False, "created": []}

                def __exit__(self, exception_type, *_arguments):
                    if exception_type is not None:
                        written.clear()

            async def prepare(_args, _plan):
                return {"ciphertext_sha256": "7" * 64}

            async def apply(*_arguments):
                mutation()

            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_read_private_json": read,
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_precommit_artifacts": Precommit,
                "_operation_recovery_lock": (
                    lambda _manager, **_kwargs: nullcontext()
                ),
                "_operation_recovery_install_lock": (
                    lambda _manager, **_kwargs: nullcontext()
                ),
                "_operation_recovery_exact_journal_worker_active": (
                    lambda _journal: False
                ),
                "_operation_recovery_post_abort_reference_progress_digest": (
                    lambda _reference_plan, _reference_journal: "c" * 64
                ),
                "_operation_recovery_authority": (
                    lambda _args, **_kwargs: plan[
                        "installation_authority"
                    ]
                ),
                "_operation_recovery_prepare_apply": prepare,
                "_operation_recovery_planned_backup_digest": (
                    lambda value: value["rollback_backup"][
                        "artifact_sha256"
                    ]
                ),
                "_operation_recovery_artifact_identity": lambda path: str(
                    path
                ),
                "_operation_recovery_post_abort_apply": apply,
                "write_private": (
                    lambda path, value, **_kwargs: written.update(
                        {str(path): value}
                    )
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "reference exact drain artifacts drifted",
                ):
                    command(
                        SimpleNamespace(
                            plan="plan.json",
                            approval_digest=plan["plan_digest"],
                        )
                    )
            finally:
                globals_.update(originals)

            self.assertEqual(journal_reads, 2)
            self.assertEqual(written, {})
            mutation.assert_not_called()

    def test_exact_drain_status_reports_authorization_only_after_approval_expiry(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        planned_at = now - 1_119_241
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-status-authorization-only-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=planned_at),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verification.json"),
                created_at=planned_at,
            )
            authorization = recovery_fixtures.exact_drain_authorization(
                plan,
                authorized_at=plan["expires_at"] - 1,
            )
            plan_path = root / "plan.json"
            for path, value in (
                (plan_path, plan),
                (Path(plan["authorization_receipt_path"]), authorization),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                path.chmod(0o600)

            lock_depth = 0

            class StateLock:
                def __enter__(self):
                    nonlocal lock_depth
                    lock_depth += 1

                def __exit__(self, *_arguments):
                    nonlocal lock_depth
                    lock_depth -= 1

            class Manager:
                def _lock(self):
                    return StateLock()

            live = {
                "generation_before": plan["pre_generation"],
                "selected_status_counts": {"pending": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
                "status_digest": "6" * 64,
            }
            observed_time = [
                authorization["authorized_at"]
                + plan["execution_window"]["calculated_seconds"]
                - 1
            ]

            def write_status(*_arguments, **_keywords):
                observed_time[0] = (
                    authorization["authorized_at"]
                    + plan["execution_window"]["calculated_seconds"]
                )

            command = self.controller[
                "operation_recovery_drain_status_command"
            ]
            globals_ = command.__globals__
            project_lease = globals_[
                "_operation_recovery_exact_execution_lease_projection"
            ]

            def project_inside_locks(*arguments, **keywords):
                self.assertEqual(lock_depth, 2)
                return project_lease(*arguments, **keywords)

            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": lambda _manager: StateLock(),
                "_operation_recovery_read_exact_drain_status": (
                    lambda _args, _plan: _immediate(live)
                ),
                "write_private": write_status,
                "_print_result": lambda value: value,
                "time": SimpleNamespace(time=lambda: observed_time[0]),
                "_operation_recovery_exact_execution_lease_projection": (
                    project_inside_locks
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                result = command(SimpleNamespace(plan=str(plan_path)))
            finally:
                globals_.update(originals)

        self.assertEqual(result["status"], "authorization-only")
        self.assertEqual(result["expires_at"], plan["expires_at"])
        self.assertTrue(result["expired"])
        self.assertEqual(result["execution_lease_status"], "expired")
        self.assertEqual(
            result["execution_lease_started_at"],
            authorization["authorized_at"],
        )
        self.assertEqual(result["execution_lease_remaining_seconds"], 0)

    def test_exact_drain_verification_receipt_is_idempotent(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-idempotent-verify-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=now,
            )
            make_receipt = self.controller[
                "_operation_recovery_exact_receipt"
            ]
            authorization = make_receipt(
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-"
                        "authorization-receipt"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "approval_digest": plan["plan_digest"],
                    "candidate_release": plan["candidate_release"],
                    "provider_policy_digest": plan[
                        "provider_policy_digest"
                    ],
                    "worker_runtime_digest": plan[
                        "worker_runtime_digest"
                    ],
                    "authorized_at": now,
                }
            )
            status_body = {
                "schema_version": 2,
                "kind": "operation-recovery-exact-drain-status",
                "plan_digest": plan["plan_digest"],
                "generation_before": "systalyze:public:200",
                "generation_after": "systalyze:public:200",
                "selected_operation_count": 43,
                "selected_status_counts": {"completed": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
                "failure_classifications": [],
                "observed_at": now + 1,
            }
            terminal_status = {
                **status_body,
                "status_digest": self.controller["digest"](status_body),
            }
            journal_body = {
                "schema_version": 1,
                "kind": "operation-recovery-exact-drain-application-journal",
                "plan_digest": plan["plan_digest"],
                "authorization_receipt_digest": authorization[
                    "receipt_digest"
                ],
                "started_at": now,
                "worker_pid": 12345,
                "worker_start_time": "2026-08-09T21:00:00.000000Z",
                "worker_attempt": 1,
            }
            recorder = ExactDrainProgressRecorder(
                path=Path(plan["progress_artifact_path"]),
                plan_digest=plan["plan_digest"],
                worker_pid=journal_body["worker_pid"],
                worker_start_time=journal_body["worker_start_time"],
                worker_attempt=journal_body["worker_attempt"],
                selected_operations=plan["selected_operations"],
                progress_schema_version=plan.get("progress_schema_version", 1),
                clock=lambda: float(now),
            )
            for item in plan["selected_operations"]:
                recorder.task_stage(
                    item["operation_id"],
                    status="completed",
                    stage="completed",
                )
            terminal_progress_digest = read_exact_drain_progress(
                Path(plan["progress_artifact_path"]),
                plan_digest=plan["plan_digest"],
                progress_schema_version=plan.get("progress_schema_version", 1),
            )["progress_digest"]
            application = make_receipt(
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-application-receipt"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "candidate_release": plan["candidate_release"],
                    "authorization_receipt_digest": authorization[
                        "receipt_digest"
                    ],
                    "application_journal_digest": self.controller["digest"](
                        journal_body
                    ),
                    "worker_runtime_digest": plan[
                        "worker_runtime_digest"
                    ],
                    "provider_policy_digest": plan[
                        "provider_policy_digest"
                    ],
                    "terminal_status_digest": terminal_status[
                        "status_digest"
                    ],
                    "terminal_progress_digest": terminal_progress_digest,
                    "selected_status_counts": {"completed": 43},
                    "outside_nonterminal_counts": [],
                    "worker_pid": journal_body["worker_pid"],
                    "worker_start_time": journal_body[
                        "worker_start_time"
                    ],
                    "worker_attempt": 1,
                    "started_at": now,
                    "completed_at": now,
                }
            )
            for path, value in (
                (plan["authorization_receipt_path"], authorization),
                (plan["status_artifact_path"], terminal_status),
                (plan["application_receipt_path"], application),
            ):
                Path(path).write_text(json.dumps(value), encoding="utf-8")
                Path(path).chmod(0o600)
            verify = self.controller[
                "_operation_recovery_drain_verify_locked"
            ]
            globals_ = verify.__globals__

            async def live(_args, _plan):
                return terminal_status

            replacements = {
                "_operation_recovery_read_exact_drain_status": live,
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                first = verify(SimpleNamespace(), plan)
                second = verify(SimpleNamespace(), plan)
            finally:
                globals_.update(originals)
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "verified")
            self.assertTrue(Path(plan["verification_receipt_path"]).is_file())

    def test_exact_drain_authorization_only_resume_survives_plan_expiry(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        plan_created_at = now - 90_000
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-expired-authorization-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=plan_created_at),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=plan_created_at,
            )
            make_receipt = self.controller[
                "_operation_recovery_exact_receipt"
            ]
            authorization = make_receipt(
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-"
                        "authorization-receipt"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "approval_digest": plan["plan_digest"],
                    "candidate_release": plan["candidate_release"],
                    "provider_policy_digest": plan[
                        "provider_policy_digest"
                    ],
                    "worker_runtime_digest": plan[
                        "worker_runtime_digest"
                    ],
                    "authorized_at": plan["created_at"] + 86_000,
                }
            )
            plan_path = root / "plan.json"
            for path, value in (
                (plan_path, plan),
                (Path(plan["authorization_receipt_path"]), authorization),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                path.chmod(0o600)

            class StopHere(RuntimeError):
                pass

            class Manager:
                def _lock(self):
                    return nullcontext()

            provider_activation = []

            def fail_provider_activation(*_arguments, **_keywords):
                provider_activation.append("provider-activation")
                raise AssertionError("expired exact drain activated providers")

            command = self.controller[
                "operation_recovery_drain_apply_command"
            ]
            globals_ = command.__globals__
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    fail_provider_activation
                ),
                "_operation_recovery_profile_environment": lambda: {},
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: plan[
                        "effective_profile_digest"
                    ]
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, *, schema_version=2: plan[
                        "worker_runtime_digest"
                    ]
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": lambda _manager: nullcontext(),
                "_assert_recovery_services_stopped": (
                    lambda _manager: (_ for _ in ()).throw(StopHere())
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                plan=str(plan_path),
                approval_digest=plan["plan_digest"],
                provider_policy="providers.json",
                provider_runtime_root="provider-runtime",
                worker_runtime="worker-runtime",
            )
            try:
                with self.assertRaises(StopHere):
                    command(args)
                retained_authorization = json.loads(
                    Path(plan["authorization_receipt_path"]).read_text(
                        encoding="utf-8"
                    )
                )
            finally:
                globals_.update(originals)
            self.assertEqual(provider_activation, [])

            self.assertEqual(
                retained_authorization["authorized_at"],
                authorization["authorized_at"],
            )

    def test_schema_eleven_interrupted_worker_requires_post_abort_recovery(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="schema-eleven-interrupted-drain-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=now,
                schema_version=11,
            )
            authorization = recovery_fixtures.exact_drain_authorization(plan)
            journal = recovery_fixtures.exact_drain_application_journal(plan)
            plan_path = root / "plan.json"
            for path, value in (
                (plan_path, plan),
                (Path(plan["authorization_receipt_path"]), authorization),
                (Path(plan["application_receipt_path"]), journal),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                path.chmod(0o600)
            live = {
                "generation_before": plan["pre_generation"],
                "selected_status_counts": {
                    "processing": plan["selected_operation_count"]
                },
                "preserved_status_counts": plan["preserved_status_counts"],
                "outside_nonterminal_counts": [],
                "status_digest": "6" * 64,
            }

            class Manager:
                def _lock(self):
                    return nullcontext()

            command = self.controller["operation_recovery_drain_apply_command"]
            globals_ = command.__globals__
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": lambda _manager: nullcontext(),
                "_operation_recovery_exact_journal_worker_active": (
                    lambda _journal: False
                ),
                "_assert_recovery_services_stopped": lambda _manager: None,
                "_operation_recovery_assert_exact_backup": lambda _plan: None,
                "_operation_recovery_read_exact_drain_status": (
                    lambda _args, _plan: _immediate(live)
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                plan=str(plan_path),
                approval_digest=plan["plan_digest"],
                provider_policy="providers.json",
                provider_runtime_root="provider-runtime",
                worker_runtime="worker-runtime",
            )
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "schema-11 interrupted exact drain requires post-abort recovery",
                ):
                    command(args)
            finally:
                globals_.update(originals)

    def test_exact_drain_apply_does_not_launch_at_the_execution_lease_boundary(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        execution_window_seconds = fixtures.drain_plan()["execution_window"][
            "calculated_seconds"
        ]
        authorized_at = now - execution_window_seconds
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-expired-lease-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=authorized_at),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=authorized_at,
            )
            authorization = self.controller[
                "_operation_recovery_exact_receipt"
            ](
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-authorization-receipt"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "approval_digest": plan["plan_digest"],
                    "candidate_release": plan["candidate_release"],
                    "provider_policy_digest": plan["provider_policy_digest"],
                    "worker_runtime_digest": plan["worker_runtime_digest"],
                    "authorized_at": authorized_at,
                }
            )
            plan_path = root / "plan.json"
            for path, value in (
                (plan_path, plan),
                (Path(plan["authorization_receipt_path"]), authorization),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                path.chmod(0o600)

            live = {
                "generation_before": plan["pre_generation"],
                "selected_status_counts": {"pending": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
                "status_digest": "6" * 64,
            }

            class Manager:
                def _lock(self):
                    return nullcontext()

            provider_activation = []

            def reject_provider_activation(*_arguments, **_keywords):
                provider_activation.append("provider-activation")
                raise AssertionError("expired exact drain activated providers")

            command = self.controller[
                "operation_recovery_drain_apply_command"
            ]
            globals_ = command.__globals__
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    reject_provider_activation
                ),
                "_operation_recovery_profile_environment": dict,
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: plan[
                        "effective_profile_digest"
                    ]
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, *, schema_version=2: plan[
                        "worker_runtime_digest"
                    ]
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": lambda _manager: nullcontext(),
                "_assert_recovery_services_stopped": lambda _manager: None,
                "_operation_recovery_assert_exact_backup": lambda _plan: None,
                "_operation_recovery_read_exact_drain_status": (
                    lambda _args, _plan: _immediate(live)
                ),
                "subprocess": SimpleNamespace(
                    Popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("expired exact drain launched a child")
                    ),
                    DEVNULL=-3,
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                plan=str(plan_path),
                approval_digest=plan["plan_digest"],
                provider_policy="providers.json",
                provider_runtime_root="provider-runtime",
                worker_runtime="worker-runtime",
            )
            try:
                with (
                    patch.object(globals_["time"], "time", return_value=now),
                    self.assertRaisesRegex(
                        Exception,
                        "execution lease expired",
                    ),
                ):
                    command(args)
            finally:
                globals_.update(originals)
            self.assertEqual(provider_activation, [])

    def test_exact_drain_controller_signal_scope_forwards_to_exact_child(self):
        scope = self.controller[
            "_operation_recovery_exact_child_signal_scope"
        ]
        globals_ = scope.__wrapped__.__globals__
        installed = {}
        restored = []
        forwarded = []

        class Process:
            pid = 4242

            @staticmethod
            def poll():
                return None

            @staticmethod
            def send_signal(signum):
                forwarded.append(signum)

        def install(signum, handler):
            if handler in {"prior-int", "prior-term"}:
                restored.append((signum, handler))
            else:
                installed[signum] = handler

        previous_match = globals_["_process_identity_matches"]
        globals_["_process_identity_matches"] = lambda identity: (
            identity.pid == 4242 and identity.start_time == "start-token"
        )
        try:
            with (
                patch.object(
                    signal,
                    "getsignal",
                    side_effect=lambda signum: {
                        signal.SIGINT: "prior-int",
                        signal.SIGTERM: "prior-term",
                    }[signum],
                ),
                patch.object(signal, "signal", side_effect=install),
                scope(Process(), "start-token") as state,
                self.assertRaisesRegex(Exception, "interrupted"),
            ):
                installed[signal.SIGTERM](signal.SIGTERM, None)
        finally:
            globals_["_process_identity_matches"] = previous_match

        self.assertTrue(state.forwarded)
        self.assertEqual(forwarded, [signal.SIGTERM])
        self.assertEqual(
            restored,
            [
                (signal.SIGINT, "prior-int"),
                (signal.SIGTERM, "prior-term"),
            ],
        )

    def test_exact_drain_controller_blocks_signals_until_child_scope_is_ready(self):
        block = self.controller[
            "_operation_recovery_exact_child_signal_block"
        ]
        calls = []
        prior = {signal.SIGUSR1}

        with (
            patch.object(
                signal,
                "pthread_sigmask",
                side_effect=lambda how, signals: (
                    calls.append((how, set(signals))) or prior
                ),
            ),
            block(),
        ):
            calls.append(("child-scope-ready", set()))

        self.assertEqual(
            calls,
            [
                (signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}),
                ("child-scope-ready", set()),
                (signal.SIG_SETMASK, prior),
            ],
        )

    def test_exact_drain_zero_exit_keeps_nonterminal_journal_resumable(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-premature-exit-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            backup = root / "backup.age"
            backup.write_bytes(b"synthetic-backup")
            backup.chmod(0o600)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(backup),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=now,
            )
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            plan_path.chmod(0o600)
            provider_policy_path = root / "providers.json"
            provider_policy_path.write_text("{}", encoding="utf-8")
            provider_policy_path.chmod(0o600)
            provider_runtime_root = root / "provider-runtime"
            provider_runtime_root.mkdir()
            worker_runtime = root / "hindsight-worker"
            worker_runtime.write_text("#!/bin/sh\n", encoding="utf-8")
            worker_runtime.chmod(0o700)
            live = {
                "generation_before": plan["pre_generation"],
                "selected_status_counts": {"pending": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
                "status_digest": "6" * 64,
            }

            observed_commands = []
            observed_environments = []
            observed_start_gates = []

            class Process:
                pid = 4242

                def __init__(self, arguments, **keywords):
                    observed_commands.append(arguments)
                    observed_environments.append(keywords["env"])
                    self._gate = os.dup(keywords["pass_fds"][0])
                    self._returncode = None

                def poll(self):
                    return self._returncode

                def wait(self, timeout=None):
                    del timeout
                    if self._gate >= 0:
                        observed_start_gates.append(
                            os.read(self._gate, 256)
                        )
                        os.close(self._gate)
                        self._gate = -1
                    self._returncode = 0
                    return 0

                def kill(self):
                    raise AssertionError("completed process was killed")

            class Manager:
                def _lock(self):
                    return nullcontext()

            command = self.controller[
                "operation_recovery_drain_apply_command"
            ]
            globals_ = command.__globals__
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    lambda _path: (plan["provider_policy_digest"], object())
                ),
                "_operation_recovery_profile_environment": lambda: {},
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: plan[
                        "effective_profile_digest"
                    ]
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, *, schema_version=2: plan[
                        "worker_runtime_digest"
                    ]
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": lambda _manager: nullcontext(),
                "_assert_recovery_services_stopped": lambda _manager: None,
                "_operation_recovery_assert_exact_backup": lambda _plan: None,
                "_operation_recovery_read_exact_drain_status": (
                    lambda _args, _plan: _immediate(live)
                ),
                "_operation_recovery_exact_worker_interpreter": (
                    lambda _path: Path("/private/tmp/python")
                ),
                "_operation_recovery_exact_database_url": (
                    lambda _plan: "postgresql://local"
                ),
                "_process_start_time": lambda _pid: "start-token",
                "_operation_recovery_exact_journal_worker_active": (
                    lambda _journal: False
                ),
                "subprocess": SimpleNamespace(
                    Popen=Process,
                    DEVNULL=-3,
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                plan=str(plan_path),
                approval_digest=plan["plan_digest"],
                provider_policy=str(provider_policy_path),
                provider_runtime_root=str(provider_runtime_root),
                worker_runtime=str(worker_runtime),
            )
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "stopped before terminal state",
                ):
                    command(args)
                authorization = json.loads(
                    Path(plan["authorization_receipt_path"]).read_text(
                        encoding="utf-8"
                    )
                )
                original_time = globals_["time"]
                globals_["time"] = SimpleNamespace(
                    time=lambda: authorization["authorized_at"]
                    + plan["execution_window"]["calculated_seconds"]
                )
                globals_["subprocess"] = SimpleNamespace(
                    Popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("expired journal resume launched a child")
                    ),
                    DEVNULL=-3,
                )
                try:
                    with self.assertRaisesRegex(
                        Exception,
                        "execution lease expired",
                    ):
                        command(args)
                finally:
                    globals_["time"] = original_time
            finally:
                globals_.update(originals)
            journal = json.loads(
                Path(plan["application_receipt_path"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                journal["kind"],
                "operation-recovery-exact-drain-application-journal",
            )
            self.assertEqual(
                observed_commands[0][:2],
                ["/private/tmp/python", "-S"],
            )
            self.assertEqual(
                observed_environments[0][
                    "HINDSIGHT_EXACT_DRAIN_PLAN_DIGEST"
                ],
                plan["plan_digest"],
            )
            self.assertEqual(
                observed_environments[0][
                    "HINDSIGHT_EXACT_DRAIN_AUTHORIZATION_RECEIPT_DIGEST"
                ],
                authorization["receipt_digest"],
            )
            self.assertEqual(
                observed_start_gates,
                [
                    b"exact-drain-start-v1 "
                    + plan["plan_digest"].encode("ascii")
                    + b" "
                    + authorization["receipt_digest"].encode("ascii")
                    + b" "
                    + journal["receipt_digest"].encode("ascii")
                    + b"\n"
                ],
            )
            self.assertFalse(Path(plan["status_artifact_path"]).exists())

    def test_exact_drain_live_child_receives_only_sigterm_at_lease_expiry(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-child-lease-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            backup = root / "backup.age"
            backup.write_bytes(b"synthetic-backup")
            backup.chmod(0o600)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(backup),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=now,
            )
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            plan_path.chmod(0o600)
            provider_policy_path = root / "providers.json"
            provider_policy_path.write_text("{}", encoding="utf-8")
            provider_policy_path.chmod(0o600)
            provider_runtime_root = root / "provider-runtime"
            provider_runtime_root.mkdir()
            worker_runtime = root / "hindsight-worker"
            worker_runtime.write_text("#!/bin/sh\n", encoding="utf-8")
            worker_runtime.chmod(0o700)
            signals = []
            wait_timeouts = []

            class Process:
                pid = 4242

                def __init__(self, _arguments, **keywords):
                    self._gate = os.dup(keywords["pass_fds"][0])
                    self._returncode = None
                    self._wait_count = 0

                def poll(self):
                    return self._returncode

                def wait(self, timeout=None):
                    self._wait_count += 1
                    wait_timeouts.append(timeout)
                    if self._gate >= 0:
                        os.close(self._gate)
                        self._gate = -1
                    raise subprocess.TimeoutExpired("worker", timeout)

                def send_signal(self, value):
                    signals.append(value)

                def kill(self):
                    raise AssertionError("exact drain used SIGKILL")

            class Manager:
                def _lock(self):
                    return nullcontext()

            live = {
                "generation_before": plan["pre_generation"],
                "selected_status_counts": {"pending": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
                "status_digest": "6" * 64,
            }
            command = self.controller[
                "operation_recovery_drain_apply_command"
            ]
            globals_ = command.__globals__
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    lambda _path: (plan["provider_policy_digest"], object())
                ),
                "_operation_recovery_profile_environment": dict,
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: plan[
                        "effective_profile_digest"
                    ]
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, *, schema_version=2: plan[
                        "worker_runtime_digest"
                    ]
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": lambda _manager: nullcontext(),
                "_assert_recovery_services_stopped": lambda _manager: None,
                "_operation_recovery_assert_exact_backup": lambda _plan: None,
                "_operation_recovery_read_exact_drain_status": (
                    lambda _args, _plan: _immediate(live)
                ),
                "_operation_recovery_exact_worker_interpreter": (
                    lambda _path: Path("/private/tmp/python")
                ),
                "_operation_recovery_exact_database_url": (
                    lambda _plan: "postgresql://local"
                ),
                "_operation_recovery_exact_worker_environment": (
                    lambda *_arguments, **_keywords: {}
                ),
                "_process_start_time": lambda _pid: "start-token",
                "_process_identity_matches": lambda identity: (
                    identity.pid == Process.pid
                    and identity.start_time == "start-token"
                ),
                "subprocess": SimpleNamespace(
                    Popen=Process,
                    DEVNULL=-3,
                    TimeoutExpired=subprocess.TimeoutExpired,
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                plan=str(plan_path),
                approval_digest=plan["plan_digest"],
                provider_policy=str(provider_policy_path),
                provider_runtime_root=str(provider_runtime_root),
                worker_runtime=str(worker_runtime),
            )
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "interrupted but remained active after lease expiry",
                ):
                    command(args)
                journal = json.loads(
                    Path(plan["application_receipt_path"]).read_text(
                        encoding="utf-8"
                    )
                )
            finally:
                globals_.update(originals)

        self.assertEqual(signals, [signal.SIGTERM])
        self.assertEqual(len(wait_timeouts), 3)
        self.assertEqual(wait_timeouts[1:], [120, 120])
        self.assertEqual(
            journal["kind"],
            "operation-recovery-exact-drain-application-journal",
        )

    def test_exact_drain_child_closed_lease_failure_reconciles_as_interrupted(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-child-closed-lease-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            backup = root / "backup.age"
            backup.write_bytes(b"synthetic-backup")
            backup.chmod(0o600)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(backup),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=now,
            )
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            plan_path.chmod(0o600)
            provider_policy_path = root / "providers.json"
            provider_policy_path.write_text("{}", encoding="utf-8")
            provider_policy_path.chmod(0o600)
            provider_runtime_root = root / "provider-runtime"
            provider_runtime_root.mkdir()
            worker_runtime = root / "hindsight-worker"
            worker_runtime.write_text("#!/bin/sh\n", encoding="utf-8")
            worker_runtime.chmod(0o700)
            signals = []
            lease_error_digest = self.controller[
                "EXACT_DRAIN_EXECUTION_LEASE_ERROR_DIGEST"
            ]

            class Process:
                pid = 4242

                def __init__(self, _arguments, **keywords):
                    self._gate = os.dup(keywords["pass_fds"][0])
                    self._returncode = None

                def poll(self):
                    return self._returncode

                def wait(self, timeout=None):
                    del timeout
                    if self._gate >= 0:
                        os.read(self._gate, 256)
                        os.close(self._gate)
                        self._gate = -1
                        recorder = ExactDrainProgressRecorder(
                            path=Path(plan["progress_artifact_path"]),
                            plan_digest=plan["plan_digest"],
                            worker_pid=self.pid,
                            worker_start_time="start-token",
                            worker_attempt=1,
                            selected_operations=plan["selected_operations"],
                            progress_schema_version=plan[
                                "progress_schema_version"
                            ],
                            clock=lambda: float(now),
                        )
                        recorder.worker_stage(
                            status="running",
                            stage="worker.poller.running",
                        )
                        recorder.worker_failure(
                            exit_code=2,
                            failure={
                                "category": "execution_lease_expired",
                                "retryable": False,
                                "http_status": None,
                                "error_digest": lease_error_digest,
                            },
                        )
                    self._returncode = 2
                    return self._returncode

                def send_signal(self, value):
                    signals.append(value)

                def kill(self):
                    raise AssertionError("exact drain used SIGKILL")

            class Manager:
                def _lock(self):
                    return nullcontext()

            live = {
                "generation_before": plan["pre_generation"],
                "selected_status_counts": {"pending": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
                "status_digest": "7" * 64,
            }
            command = self.controller[
                "operation_recovery_drain_apply_command"
            ]
            globals_ = command.__globals__
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    lambda _path: (plan["provider_policy_digest"], object())
                ),
                "_operation_recovery_profile_environment": dict,
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: plan[
                        "effective_profile_digest"
                    ]
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, *, schema_version=2: plan[
                        "worker_runtime_digest"
                    ]
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": lambda _manager: nullcontext(),
                "_assert_recovery_services_stopped": lambda _manager: None,
                "_operation_recovery_assert_exact_backup": lambda _plan: None,
                "_operation_recovery_read_exact_drain_status": (
                    lambda _args, _plan: _immediate(live)
                ),
                "_operation_recovery_exact_worker_interpreter": (
                    lambda _path: Path("/private/tmp/python")
                ),
                "_operation_recovery_exact_database_url": (
                    lambda _plan: "postgresql://local"
                ),
                "_operation_recovery_exact_worker_environment": (
                    lambda *_arguments, **_keywords: {}
                ),
                "_process_start_time": lambda _pid: "start-token",
                "subprocess": SimpleNamespace(
                    Popen=Process,
                    DEVNULL=-3,
                    TimeoutExpired=subprocess.TimeoutExpired,
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                plan=str(plan_path),
                approval_digest=plan["plan_digest"],
                provider_policy=str(provider_policy_path),
                provider_runtime_root=str(provider_runtime_root),
                worker_runtime=str(worker_runtime),
            )
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "interrupted at execution lease expiry",
                ):
                    command(args)
                progress = json.loads(
                    Path(plan["progress_artifact_path"]).read_text(
                        encoding="utf-8"
                    )
                )
            finally:
                globals_.update(originals)

        self.assertEqual(signals, [])
        self.assertEqual(
            progress["worker_failure"]["category"],
            "execution_lease_expired",
        )
        self.assertFalse(progress["worker_failure"]["retryable"])
        self.assertNotIn("execution lease expired", json.dumps(progress))

    def test_closed_lease_failure_requires_exact_child_exit_and_evidence(self):
        helper = self.controller[
            "_operation_recovery_exact_progress_closed_lease_expiry"
        ]
        now = int(time.time())
        expected_failure = {
            "category": "execution_lease_expired",
            "retryable": False,
            "http_status": None,
            "error_digest": self.controller[
                "EXACT_DRAIN_EXECUTION_LEASE_ERROR_DIGEST"
            ],
        }
        for label, progress_exit, child_exit, failure in (
            ("valid", 2, 2, expected_failure),
            ("wrong-progress-exit", 17, 2, expected_failure),
            ("wrong-child-exit", 2, 17, expected_failure),
            (
                "wrong-error-digest",
                2,
                2,
                {**expected_failure, "error_digest": "f" * 64},
            ),
        ):
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory(
                    dir="/private/tmp",
                    prefix="exact-drain-closed-lease-evidence-",
                ) as directory:
                    path = Path(directory) / "progress.json"
                    recorder = ExactDrainProgressRecorder(
                        path=path,
                        plan_digest="a" * 64,
                        worker_pid=4242,
                        worker_start_time="start-token",
                        worker_attempt=1,
                        selected_operations=[],
                        progress_schema_version=3,
                        clock=lambda: float(now),
                    )
                    recorder.worker_stage(
                        status="running",
                        stage="worker.poller.running",
                    )
                    recorder.worker_failure(
                        exit_code=progress_exit,
                        failure=failure,
                    )
                    plan = {
                        "progress_schema_version": 3,
                        "progress_artifact_path": str(path),
                        "plan_digest": "a" * 64,
                    }
                    journal = {
                        "worker_pid": 4242,
                        "worker_start_time": "start-token",
                        "worker_attempt": 1,
                    }
                    if label == "valid":
                        self.assertTrue(
                            helper(
                                plan,
                                journal,
                                child_returncode=child_exit,
                            )
                        )
                    else:
                        with self.assertRaisesRegex(
                            self.controller["OperationRecoveryError"],
                            "lease expiry evidence differs",
                        ):
                            helper(
                                plan,
                                journal,
                                child_returncode=child_exit,
                            )

    def test_exact_drain_lease_timeout_with_pid_mismatch_preserves_journal(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-child-identity-drift-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            backup = root / "backup.age"
            backup.write_bytes(b"synthetic-backup")
            backup.chmod(0o600)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(backup),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=now,
            )
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            plan_path.chmod(0o600)
            provider_policy_path = root / "providers.json"
            provider_policy_path.write_text("{}", encoding="utf-8")
            provider_policy_path.chmod(0o600)
            provider_runtime_root = root / "provider-runtime"
            provider_runtime_root.mkdir()
            worker_runtime = root / "hindsight-worker"
            worker_runtime.write_text("#!/bin/sh\n", encoding="utf-8")
            worker_runtime.chmod(0o700)
            signals = []
            kills = []
            wait_timeouts = []

            class Process:
                pid = 4242

                def __init__(self, _arguments, **keywords):
                    self._gate = os.dup(keywords["pass_fds"][0])
                    self._returncode = None

                def poll(self):
                    return self._returncode

                def wait(self, timeout=None):
                    wait_timeouts.append(timeout)
                    if self._gate >= 0:
                        os.close(self._gate)
                        self._gate = -1
                    raise subprocess.TimeoutExpired("worker", timeout)

                def send_signal(self, value):
                    signals.append(value)

                def kill(self):
                    kills.append(True)

            class Manager:
                def _lock(self):
                    return nullcontext()

            live = {
                "generation_before": plan["pre_generation"],
                "selected_status_counts": {"pending": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
                "status_digest": "6" * 64,
            }
            command = self.controller[
                "operation_recovery_drain_apply_command"
            ]
            globals_ = command.__globals__
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    lambda _path: (plan["provider_policy_digest"], object())
                ),
                "_operation_recovery_profile_environment": dict,
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: plan[
                        "effective_profile_digest"
                    ]
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, *, schema_version=2: plan[
                        "worker_runtime_digest"
                    ]
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": lambda _manager: nullcontext(),
                "_assert_recovery_services_stopped": lambda _manager: None,
                "_operation_recovery_assert_exact_backup": lambda _plan: None,
                "_operation_recovery_read_exact_drain_status": (
                    lambda _args, _plan: _immediate(live)
                ),
                "_operation_recovery_exact_worker_interpreter": (
                    lambda _path: Path("/private/tmp/python")
                ),
                "_operation_recovery_exact_database_url": (
                    lambda _plan: "postgresql://local"
                ),
                "_operation_recovery_exact_worker_environment": (
                    lambda *_arguments, **_keywords: {}
                ),
                "_process_start_time": lambda _pid: "start-token",
                "_process_identity_matches": lambda _identity: False,
                "subprocess": SimpleNamespace(
                    Popen=Process,
                    DEVNULL=-3,
                    TimeoutExpired=subprocess.TimeoutExpired,
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                plan=str(plan_path),
                approval_digest=plan["plan_digest"],
                provider_policy=str(provider_policy_path),
                provider_runtime_root=str(provider_runtime_root),
                worker_runtime=str(worker_runtime),
            )
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "worker identity drifted at lease expiry",
                ):
                    command(args)
                journal = json.loads(
                    Path(plan["application_receipt_path"]).read_text(
                        encoding="utf-8"
                    )
                )
            finally:
                globals_.update(originals)

        self.assertEqual(signals, [])
        self.assertEqual(kills, [])
        self.assertEqual(len(wait_timeouts), 1)
        self.assertEqual(
            journal["kind"],
            "operation-recovery-exact-drain-application-journal",
        )

    def test_exact_drain_dead_terminal_journal_reconciles_after_lease_expiry(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-terminal-journal-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=now,
            )
            make_receipt = self.controller[
                "_operation_recovery_exact_receipt"
            ]
            authorization = make_receipt(
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-"
                        "authorization-receipt"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "approval_digest": plan["plan_digest"],
                    "candidate_release": plan["candidate_release"],
                    "provider_policy_digest": plan[
                        "provider_policy_digest"
                    ],
                    "worker_runtime_digest": plan[
                        "worker_runtime_digest"
                    ],
                    "authorized_at": now,
                }
            )
            journals = []
            for attempt in range(1, plan["worker_max_attempts"] + 1):
                journal = make_receipt(
                    {
                        "schema_version": 1,
                        "kind": (
                            "operation-recovery-exact-drain-application-journal"
                        ),
                        "plan_digest": plan["plan_digest"],
                        "authorization_receipt_digest": authorization[
                            "receipt_digest"
                        ],
                        "started_at": now,
                        "worker_pid": os.getpid(),
                        "worker_start_time": f"dead-worker-token-{attempt}",
                        "worker_attempt": attempt,
                    }
                )
                journals.append(journal)
                create_exact_drain_progress_recorder(
                    plan=plan,
                    authorization=authorization,
                    journal=journal,
                    clock=lambda: float(now),
                )
            journal = journals[-1]
            status_body = {
                "schema_version": 2,
                "kind": "operation-recovery-exact-drain-status",
                "plan_digest": plan["plan_digest"],
                "generation_before": "systalyze:public:250",
                "generation_after": "systalyze:public:250",
                "selected_operation_count": 43,
                "selected_status_counts": {"completed": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
                "failure_classifications": [],
                "observed_at": now + 1,
            }
            terminal = {
                **status_body,
                "status_digest": self.controller["digest"](status_body),
            }
            plan_path = root / "plan.json"
            for path, value in (
                (plan_path, plan),
                (Path(plan["authorization_receipt_path"]), authorization),
                (Path(plan["application_receipt_path"]), journal),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                path.chmod(0o600)
            provider_policy_path = root / "providers.json"
            provider_policy_path.write_text("{}", encoding="utf-8")
            provider_policy_path.chmod(0o600)
            provider_runtime_root = root / "provider-runtime"
            provider_runtime_root.mkdir()
            worker_runtime = root / "hindsight-worker"
            worker_runtime.write_text("#!/bin/sh\n", encoding="utf-8")
            worker_runtime.chmod(0o700)

            transaction_calls = []
            adapter_evidence = []

            class Connection:
                @asynccontextmanager
                async def transaction(self, **keywords):
                    transaction_calls.append(keywords)
                    yield

                async def close(self):
                    return None

            async def connect_live(_args, *, readonly=True):
                self.assertIs(readonly, True)
                return Connection()

            class Adapter:
                def __init__(self, _plan, **keywords):
                    adapter_evidence.append(keywords)
                    self._recorder = None

                async def claim_tasks(self, *_arguments, **_keywords):
                    return []

                def bind_terminal_progress_recorder(self, recorder):
                    self._recorder = recorder

                def claim_committed(self, tasks):
                    if tasks:
                        raise AssertionError("terminal controller claimed tasks")
                    for item in plan["selected_operations"]:
                        self._recorder.task_stage(
                            item["operation_id"],
                            status="completed",
                            stage="resume-completed",
                        )

            class Manager:
                def _lock(self):
                    return nullcontext()

            command = self.controller[
                "operation_recovery_drain_apply_command"
            ]
            globals_ = command.__globals__
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    lambda _path: (plan["provider_policy_digest"], object())
                ),
                "_operation_recovery_profile_environment": lambda: {},
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: plan[
                        "effective_profile_digest"
                    ]
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, *, schema_version=2: plan[
                        "worker_runtime_digest"
                    ]
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": lambda _manager: nullcontext(),
                "_operation_recovery_exact_journal_worker_active": (
                    lambda _journal: False
                ),
                "_assert_recovery_services_stopped": lambda _manager: None,
                "_operation_recovery_assert_exact_backup": lambda _plan: None,
                "_operation_recovery_read_exact_drain_status": (
                    lambda _args, _plan: _immediate(terminal)
                ),
                "_operation_recovery_connect_live": connect_live,
                "ExactDrainClaimAdapter": Adapter,
                "_process_start_time": lambda _pid: "resumed-worker-token",
                "_print_result": lambda value: value,
                "subprocess": SimpleNamespace(
                    Popen=lambda *_arguments, **_keywords: (
                        _ for _ in ()
                    ).throw(AssertionError("terminal child launched")),
                    DEVNULL=-3,
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                plan=str(plan_path),
                approval_digest=plan["plan_digest"],
                provider_policy=str(provider_policy_path),
                provider_runtime_root=str(provider_runtime_root),
                worker_runtime=str(worker_runtime),
            )
            try:
                original_time = globals_["time"]
                globals_["time"] = SimpleNamespace(
                    time=lambda: authorization["authorized_at"]
                    + plan["execution_window"]["calculated_seconds"]
                    + 1
                )
                try:
                    result = command(args)
                finally:
                    globals_["time"] = original_time
                first_application = json.loads(
                    Path(plan["application_receipt_path"]).read_text(
                        encoding="utf-8"
                    )
                )
                reconciliation_journal = self.controller[
                    "_operation_recovery_exact_receipt"
                ](
                    {
                        "schema_version": 1,
                        "kind": (
                            "operation-recovery-exact-drain-application-journal"
                        ),
                        "plan_digest": plan["plan_digest"],
                        "authorization_receipt_digest": authorization[
                            "receipt_digest"
                        ],
                        "started_at": first_application["started_at"],
                        "worker_pid": first_application["worker_pid"],
                        "worker_start_time": first_application[
                            "worker_start_time"
                        ],
                        "worker_attempt": first_application[
                            "worker_attempt"
                        ],
                    }
                )
                Path(plan["application_receipt_path"]).write_text(
                    json.dumps(reconciliation_journal),
                    encoding="utf-8",
                )
                original_time = globals_["time"]
                globals_["time"] = SimpleNamespace(
                    time=lambda: authorization["authorized_at"]
                    + plan["execution_window"]["calculated_seconds"]
                )
                try:
                    resumed_result = command(args)
                finally:
                    globals_["time"] = original_time
            finally:
                globals_.update(originals)
            self.assertEqual(result["status"], "terminal")
            self.assertEqual(resumed_result["status"], "terminal")
            application = json.loads(
                Path(plan["application_receipt_path"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                application["worker_attempt"],
                plan["worker_max_attempts"] + 1,
            )
            self.assertEqual(application["worker_pid"], os.getpid())
            self.assertEqual(
                transaction_calls,
                [{"isolation": "serializable", "readonly": True}],
            )
            self.assertEqual(
                adapter_evidence[0]["terminal_status_evidence"],
                {
                    "generation": terminal["generation_before"],
                    "observed_at": terminal["observed_at"],
                    "status_digest": terminal["status_digest"],
                },
            )
            self.assertTrue(Path(plan["status_artifact_path"]).is_file())

    def test_early_terminal_resume_uses_no_alternate_runtime_or_child(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="exact-drain-early-terminal-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_exact_drain_plan"](
                fixtures.cohort(),
                fixtures.drain_snapshot(observed_at=now),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=recovery_fixtures.drain_backup_evidence(),
                rollback_backup_path=str(root / "backup.age"),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=str(root / "auth.json"),
                application_receipt_path=str(root / "application.json"),
                status_artifact_path=str(root / "status.json"),
                verification_receipt_path=str(root / "verify.json"),
                created_at=now,
            )
            receipt = self.controller["_operation_recovery_exact_receipt"]
            authorization = receipt(
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-authorization-receipt"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "approval_digest": plan["plan_digest"],
                    "candidate_release": plan["candidate_release"],
                    "provider_policy_digest": plan[
                        "provider_policy_digest"
                    ],
                    "worker_runtime_digest": plan["worker_runtime_digest"],
                    "authorized_at": now,
                }
            )
            journal = receipt(
                {
                    "schema_version": 1,
                    "kind": (
                        "operation-recovery-exact-drain-application-journal"
                    ),
                    "plan_digest": plan["plan_digest"],
                    "authorization_receipt_digest": authorization[
                        "receipt_digest"
                    ],
                    "started_at": now,
                    "worker_pid": os.getpid(),
                    "worker_start_time": "dead-early-worker",
                    "worker_attempt": 1,
                }
            )
            plan_path = root / "plan.json"
            for path, value in (
                (plan_path, plan),
                (Path(plan["authorization_receipt_path"]), authorization),
                (Path(plan["application_receipt_path"]), journal),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                path.chmod(0o600)
            ExactDrainProgressRecorder(
                path=Path(plan["progress_artifact_path"]),
                plan_digest=plan["plan_digest"],
                worker_pid=journal["worker_pid"],
                worker_start_time=journal["worker_start_time"],
                worker_attempt=1,
                selected_operations=plan["selected_operations"],
                progress_schema_version=plan.get("progress_schema_version", 1),
                clock=lambda: float(now),
            )
            provider_policy_path = root / "providers.json"
            provider_policy_path.write_text("{}", encoding="utf-8")
            provider_runtime_root = root / "provider-runtime"
            provider_runtime_root.mkdir()
            worker_runtime = root / "hindsight-worker"
            worker_runtime.write_text("#!/bin/sh\n", encoding="utf-8")
            worker_runtime.chmod(0o700)
            terminal = {
                "generation_before": "systalyze:public:250",
                "generation_after": "systalyze:public:250",
                "selected_status_counts": {"completed": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
                "status_digest": "6" * 64,
            }
            provider_activation = []

            class StopHere(RuntimeError):
                pass

            def fail_provider_activation(*_arguments, **_keywords):
                provider_activation.append("provider-activation")
                raise AssertionError("terminal reconciliation activated providers")

            async def stop_at_verified_database(*_arguments, **_keywords):
                raise StopHere

            class Manager:
                def _lock(self):
                    return nullcontext()

            command = self.controller[
                "operation_recovery_drain_apply_command"
            ]
            globals_ = command.__globals__
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_exact_provider_policy_evidence": (
                    fail_provider_activation
                ),
                "_operation_recovery_profile_environment": (
                    fail_provider_activation
                ),
                "exact_drain_effective_profile_digest": (
                    lambda _policy, _environment: plan[
                        "effective_profile_digest"
                    ]
                ),
                "_operation_recovery_exact_runtime_digest": (
                    fail_provider_activation
                ),
                "_operation_recovery_validate_exact_worker_provider_runtime": (
                    lambda _policy, _worker_runtime: None
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": lambda _manager: nullcontext(),
                "_operation_recovery_exact_journal_worker_active": (
                    lambda _journal: False
                ),
                "_assert_recovery_services_stopped": lambda _manager: None,
                "_operation_recovery_assert_exact_backup": lambda _plan: None,
                "_operation_recovery_read_exact_drain_status": (
                    lambda _args, _plan: _immediate(terminal)
                ),
                "_operation_recovery_connect_live": (
                    stop_at_verified_database
                ),
                "subprocess": SimpleNamespace(
                    Popen=lambda *_arguments, **_keywords: (
                        _ for _ in ()
                    ).throw(AssertionError("terminal child launched")),
                    DEVNULL=-3,
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            args = SimpleNamespace(
                plan=str(plan_path),
                approval_digest=plan["plan_digest"],
                provider_policy=str(provider_policy_path),
                provider_runtime_root=str(provider_runtime_root),
                worker_runtime=str(worker_runtime),
            )
            try:
                with self.assertRaises(StopHere):
                    command(args)
            finally:
                globals_.update(originals)

        self.assertEqual(provider_activation, [])

    def test_terminal_reconciliation_parent_rejects_prelaunch_status_race(self):
        assert_snapshot = self.controller[
            "_operation_recovery_assert_terminal_snapshot"
        ]
        prelaunch = {
            "generation_before": "systalyze:public:123",
            "generation_after": "systalyze:public:123",
            "selected_operation_count": 1,
            "selected_status_counts": {"completed": 1},
            "preserved_status_counts": {"completed": 5},
            "outside_nonterminal_counts": [],
            "observed_at": 1_000,
            "status_digest": "6" * 64,
        }
        observed = {**prelaunch, "observed_at": 1_001, "status_digest": "7" * 64}
        assert_snapshot(prelaunch, observed)

        with self.assertRaisesRegex(
            self.controller["OperationRecoveryError"],
            "terminal generation evidence differs",
        ):
            assert_snapshot(
                prelaunch,
                {
                    **observed,
                    "generation_before": "systalyze:public:124",
                    "generation_after": "systalyze:public:124",
                },
            )
        with self.assertRaisesRegex(
            self.controller["OperationRecoveryError"],
            "terminal status evidence differs",
        ):
            assert_snapshot(
                prelaunch,
                {
                    **observed,
                    "selected_status_counts": {"failed": 1},
                },
            )

    def test_frozen_legacy_terminal_reconciliation_uses_controller_database(self):
        plan = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "legacy_exact_drain_plans.json"
            ).read_text(encoding="utf-8")
        )["exact"]
        authorization = recovery_fixtures.exact_drain_authorization(plan)
        receipt = self.controller["_operation_recovery_exact_receipt"]
        journal = receipt(
            {
                "schema_version": 1,
                "kind": "operation-recovery-exact-drain-application-journal",
                "plan_digest": plan["plan_digest"],
                "authorization_receipt_digest": authorization[
                    "receipt_digest"
                ],
                "started_at": authorization["authorized_at"],
                "worker_pid": 4242,
                "worker_start_time": "frozen-legacy-worker",
                "worker_attempt": 1,
            }
        )
        live = {
            "generation_before": plan["pre_generation"],
            "generation_after": plan["pre_generation"],
            "selected_operation_count": plan["selected_operation_count"],
            "selected_status_counts": {
                "completed": plan["selected_operation_count"]
            },
            "preserved_status_counts": plan["preserved_status_counts"],
            "outside_nonterminal_counts": [],
            "observed_at": 1_000,
            "status_digest": "6" * 64,
        }

        class Manager:
            def _lock(self):
                return nullcontext()

        class StopHere(RuntimeError):
            pass

        database_calls = []

        async def stop_at_verified_database(_args, *, readonly=True):
            database_calls.append(readonly)
            raise StopHere

        command = self.controller["operation_recovery_drain_apply_command"]
        globals_ = command.__globals__
        documents = {
            plan["authorization_receipt_path"]: authorization,
            plan["application_receipt_path"]: journal,
        }
        present = set(documents) | {plan["progress_artifact_path"]}
        original_exists = Path.exists

        def fixture_exists(path):
            return str(path) in present or original_exists(path)

        replacements = {
            "_operation_recovery_candidate": (
                lambda _args: plan["candidate_release"]
            ),
            "_operation_recovery_read_private_json": (
                lambda path, _label: documents.get(str(path), plan)
            ),
            "_portable_manager": lambda _args: Manager(),
            "_operation_recovery_lock": lambda _manager: nullcontext(),
            "_operation_recovery_exact_journal_worker_active": (
                lambda _journal: False
            ),
            "_assert_recovery_services_stopped": lambda _manager: None,
            "_operation_recovery_assert_exact_backup": lambda _plan: None,
            "_operation_recovery_read_exact_drain_status": (
                lambda _args, _plan: _immediate(live)
            ),
            "_operation_recovery_assert_terminal_progress": (
                lambda **_keywords: (_ for _ in ()).throw(
                    self.controller["OperationRecoveryError"](
                        "terminal progress unavailable"
                    )
                )
            ),
            "_operation_recovery_exact_next_worker_attempt": (
                lambda *_arguments, **_keywords: 2
            ),
            "_operation_recovery_connect_live": stop_at_verified_database,
            "_operation_recovery_exact_worker_interpreter": (
                lambda _path: Path(sys.executable)
            ),
            "_operation_recovery_exact_database_url": (
                lambda _plan: "postgresql://local"
            ),
            "_operation_recovery_exact_worker_environment": (
                lambda *_arguments, **_keywords: {}
            ),
            "_process_start_time": lambda _pid: "legacy-terminal-worker",
            "_process_identity_matches": lambda _identity: True,
            "write_private": lambda *_arguments, **_keywords: None,
            "subprocess": SimpleNamespace(
                Popen=lambda *_arguments, **_keywords: (
                    _ for _ in ()
                ).throw(AssertionError("legacy terminal child launched")),
                DEVNULL=-3,
            ),
            "time": SimpleNamespace(time=lambda: 1_000.0),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        args = SimpleNamespace(
            plan=str(ROOT / "tests" / "fixtures" / "legacy_exact_drain_plans.json"),
            approval_digest=plan["plan_digest"],
            provider_policy="/invalid/provider-policy",
            provider_runtime_root="/invalid/provider-runtime",
            worker_runtime=sys.executable,
        )
        try:
            with (
                patch.object(Path, "exists", fixture_exists),
                self.assertRaises(StopHere),
            ):
                command(args)
        finally:
            globals_.update(originals)

        self.assertEqual(database_calls, [True])

    def test_frozen_legacy_nonterminal_resume_uses_v1_runtime_evidence(self):
        plan = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "legacy_exact_drain_plans.json"
            ).read_text(encoding="utf-8")
        )["exact"]
        authorization = recovery_fixtures.exact_drain_authorization(plan)
        receipt = self.controller["_operation_recovery_exact_receipt"]
        journal = receipt(
            {
                "schema_version": 1,
                "kind": "operation-recovery-exact-drain-application-journal",
                "plan_digest": plan["plan_digest"],
                "authorization_receipt_digest": authorization[
                    "receipt_digest"
                ],
                "started_at": authorization["authorized_at"],
                "worker_pid": 4242,
                "worker_start_time": "frozen-legacy-worker",
                "worker_attempt": 1,
            }
        )
        live = {
            "generation_before": plan["pre_generation"],
            "selected_status_counts": {
                "processing": plan["selected_operation_count"]
            },
            "preserved_status_counts": plan["preserved_status_counts"],
            "outside_nonterminal_counts": [],
            "status_digest": "6" * 64,
        }
        documents = {
            plan["authorization_receipt_path"]: authorization,
            plan["application_receipt_path"]: journal,
        }
        present = set(documents) | {plan["progress_artifact_path"]}
        original_exists = Path.exists
        runtime_versions = []

        def fixture_exists(path):
            return str(path) in present or original_exists(path)

        def exact_runtime(_args, *, schema_version):
            runtime_versions.append(schema_version)
            return plan["worker_runtime_digest"]

        class Manager:
            def _lock(self):
                return nullcontext()

        class StopHere(RuntimeError):
            pass

        command = self.controller["operation_recovery_drain_apply_command"]
        globals_ = command.__globals__
        replacements = {
            "_operation_recovery_candidate": (
                lambda _args: plan["candidate_release"]
            ),
            "_operation_recovery_read_private_json": (
                lambda path, _label: documents.get(str(path), plan)
            ),
            "_portable_manager": lambda _args: Manager(),
            "_operation_recovery_lock": lambda _manager: nullcontext(),
            "_operation_recovery_exact_journal_worker_active": (
                lambda _journal: False
            ),
            "_assert_recovery_services_stopped": lambda _manager: None,
            "_operation_recovery_assert_exact_backup": lambda _plan: None,
            "_operation_recovery_read_exact_drain_status": (
                lambda _args, _plan: _immediate(live)
            ),
            "_operation_recovery_exact_provider_policy_evidence": (
                lambda _path: (plan["provider_policy_digest"], object())
            ),
            "_operation_recovery_validate_exact_worker_provider_runtime": (
                lambda _policy, _runtime: None
            ),
            "_operation_recovery_profile_environment": dict,
            "exact_drain_effective_profile_digest": (
                lambda _policy, _environment: plan[
                    "effective_profile_digest"
                ]
            ),
            "_operation_recovery_exact_runtime_digest": exact_runtime,
            "_operation_recovery_exact_worker_interpreter": (
                lambda _path: Path(sys.executable)
            ),
            "_operation_recovery_exact_database_url": (
                lambda _plan: "postgresql://local"
            ),
            "_operation_recovery_exact_worker_environment": (
                lambda *_arguments, **_keywords: {}
            ),
            "_operation_recovery_exact_next_worker_attempt": (
                lambda *_arguments, **_keywords: 2
            ),
            "subprocess": SimpleNamespace(
                Popen=lambda *_arguments, **_keywords: (
                    _ for _ in ()
                ).throw(StopHere()),
                DEVNULL=-3,
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        args = SimpleNamespace(
            plan=str(ROOT / "tests" / "fixtures" / "legacy_exact_drain_plans.json"),
            approval_digest=plan["plan_digest"],
            provider_policy=str(
                ROOT / "tests" / "fixtures" / "legacy_exact_drain_plans.json"
            ),
            provider_runtime_root=str(ROOT),
            worker_runtime=sys.executable,
        )
        try:
            with (
                patch.object(Path, "exists", fixture_exists),
                self.assertRaises(StopHere),
            ):
                command(args)
        finally:
            globals_.update(originals)

        self.assertEqual(runtime_versions, [1])

    def test_private_json_reader_rejects_symlink(self):
        reader = self.controller["_operation_recovery_read_private_json"]
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="hindsight-private-json-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            target = root / "target.json"
            target.write_text(json.dumps({"ok": True}), encoding="utf-8")
            target.chmod(0o600)
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(Exception, "unreadable"):
                reader(link, "test recovery artifact")

    def test_pinned_root_tracks_rename_by_inode(self):
        create = self.controller["_create_pinned_recovery_root"]
        current_path = self.controller["_pinned_recovery_root_path"]
        parent = Path(
            tempfile.mkdtemp(
                dir="/private/tmp",
                prefix="hindsight-pinned-parent-",
            )
        )
        parent.chmod(0o700)
        original = parent / f"hindsight-operation-recovery-{'a' * 32}"
        moved = parent / "moved"
        descriptor = -1
        try:
            descriptor, identity = create(original)
            original.rename(moved)
            self.assertEqual(current_path(descriptor, identity), moved)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if moved.exists():
                moved.rmdir()

    def test_rollback_approval_is_distinct_from_apply_approval(self):
        rollback_approval = self.controller[
            "_operation_recovery_rollback_approval"
        ]
        plan = {"plan_digest": "a" * 64}
        application = {"receipt_digest": "b" * 64}
        self.assertNotEqual(
            rollback_approval(plan, application),
            plan["plan_digest"],
        )

    def test_rollback_rejects_existing_destination_before_mutation(self):
        command = self.controller["operation_recovery_rollback_command"]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="hindsight-rollback-prestate-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            receipt_path = root / "rollback-receipt.json"
            receipt_path.write_text(
                json.dumps({"sentinel": True}),
                encoding="utf-8",
            )
            receipt_path.chmod(0o600)
            identity_path = root / "identity.txt"
            identity_path.write_text("test-only", encoding="utf-8")
            identity_path.chmod(0o600)
            plan = self.controller["create_requeue_plan"](
                fixtures.cohort(),
                fixtures.live_snapshot(),
                candidate_release=recovery_fixtures.release_identity(),
                rollback_backup=(
                    recovery_fixtures.rollback_backup_evidence()
                ),
                rollback_encryption=(
                    recovery_fixtures.rollback_encryption()
                ),
                rollback_backup_path=str(root / "rollback.dump.age"),
                rollback_bundle_path=str(root / "rollback-bundle.json"),
                authorization_receipt_path=str(
                    root / "authorization.json"
                ),
                application_receipt_path=str(root / "application.json"),
                verification_receipt_path=str(
                    root / "verification.json"
                ),
                rollback_receipt_path=str(receipt_path),
                created_at=int(time.time()),
            )
            ciphertext = b"bounded-test-ciphertext"
            ciphertext_digest = hashlib.sha256(ciphertext).hexdigest()
            application = {
                "receipt_digest": "b" * 64,
                "rollback_bundle_digest": ciphertext_digest,
                "post_generation": self.controller[
                    "_operation_recovery_next_generation"
                ](plan["pre_generation"]),
            }
            envelope = {
                "schema_version": 1,
                "kind": "operation-recovery-encrypted-rollback-bundle",
                "plan_digest": plan["plan_digest"],
                "ciphertext_sha256": ciphertext_digest,
                "ciphertext_base64": base64.b64encode(ciphertext).decode(
                    "ascii"
                ),
            }
            preimage = {
                "schema_version": 1,
                "kind": "operation-recovery-selected-row-preimage",
                "plan_digest": plan["plan_digest"],
                "rows": [],
            }
            documents = {
                "plan.json": plan,
                plan["application_receipt_path"]: application,
                plan["rollback_bundle_path"]: envelope,
            }
            original_reader = globals_[
                "_operation_recovery_read_private_json"
            ]
            original_writer = globals_["write_private"]
            state = {
                "mutation_count": 0,
                "rollback_calls": 0,
                "fail_finalize": True,
            }

            async def rollback(_connection, **arguments):
                state["rollback_calls"] += 1
                if state["mutation_count"] == 0:
                    state["mutation_count"] += 1
                record = arguments["rollback_record"]
                return (
                    record["pre_generation"],
                    record["post_generation"],
                )

            async def connect(_args, *, readonly):
                self.assertFalse(readonly)
                return Connection()

            def write(path, value, *, create_only=False):
                if not create_only and state["fail_finalize"]:
                    state["fail_finalize"] = False
                    raise OSError("simulated receipt finalization failure")
                return original_writer(
                    path,
                    value,
                    create_only=create_only,
                )

            class Connection:
                async def close(self):
                    return None

            class Manager:
                def _lock(self):
                    return nullcontext()

            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: plan["candidate_release"]
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, label: documents[str(path)]
                    if str(path) in documents
                    else original_reader(path, label)
                ),
                "_operation_recovery_validate_application": (
                    lambda _value, *, plan: application
                ),
                "_operation_recovery_tool": (
                    lambda path, _key: Path(path)
                ),
                "_private_identity_descriptor": (
                    lambda _path: os.open(os.devnull, os.O_RDONLY)
                ),
                "_stage_operation_recovery_tools": (
                    lambda _tools: (
                        object(),
                        {
                            "age": SimpleNamespace(
                                path=Path(
                                    plan["rollback_encryption"]["age_path"]
                                )
                            )
                        },
                    )
                ),
                "_run_private_subprocess": (
                    lambda *_arguments, **_keywords: SimpleNamespace(
                        stdout=json.dumps(preimage).encode("utf-8")
                    )
                ),
                "_remove_operation_recovery_tools": lambda _root: None,
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": (
                    lambda _manager: nullcontext()
                ),
                "_operation_recovery_authority": (
                    lambda _args, **_keywords: plan[
                        "installation_authority"
                    ]
                ),
                "_operation_recovery_connect_live": connect,
                "_operation_recovery_rollback_identity_path": (
                    lambda _value: identity_path
                ),
                "rollback_requeue_transaction": rollback,
                "write_private": write,
                "_print_result": lambda value: value,
                "EXPECTED_OPERATION_RECOVERY_TOOLCHAIN": {
                    "age": {
                        "sha256": plan["rollback_encryption"][
                            "age_sha256"
                        ]
                    }
                },
            }
            originals = {
                key: globals_[key]
                for key in replacements
            }
            globals_.update(replacements)
            args = SimpleNamespace(
                plan="plan.json",
                approval_digest=self.controller[
                    "_operation_recovery_rollback_approval"
                ](plan, application),
                age=plan["rollback_encryption"]["age_path"],
                age_identity=str(identity_path),
            )
            try:
                with self.assertRaisesRegex(
                    Exception,
                    "rollback journal is invalid",
                ):
                    command(args)
                self.assertEqual(state["mutation_count"], 0)
                self.assertEqual(
                    json.loads(
                        receipt_path.read_text(encoding="utf-8")
                    ),
                    {"sentinel": True},
                )

                receipt_path.unlink()
                with self.assertRaisesRegex(
                    OSError,
                    "simulated receipt finalization failure",
                ):
                    command(args)
                self.assertEqual(state["mutation_count"], 1)
                self.assertEqual(state["rollback_calls"], 1)
                self.assertEqual(
                    json.loads(
                        receipt_path.read_text(encoding="utf-8")
                    )["kind"],
                    "operation-recovery-rollback-journal",
                )

                result = command(args)
            finally:
                globals_.update(originals)

            self.assertEqual(
                result["status"],
                "rolled-back",
            )
            self.assertEqual(state["mutation_count"], 1)
            self.assertEqual(state["rollback_calls"], 2)
            self.assertEqual(
                json.loads(
                    receipt_path.read_text(encoding="utf-8")
                )["kind"],
                "operation-recovery-rollback-receipt",
            )

    def test_rollback_journal_finalizes_as_valid_receipt(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        plan = self.controller["create_requeue_plan"](
            fixtures.cohort(),
            fixtures.live_snapshot(),
            candidate_release=recovery_fixtures.release_identity(),
            rollback_backup=recovery_fixtures.rollback_backup_evidence(),
            rollback_encryption=recovery_fixtures.rollback_encryption(),
            rollback_backup_path="/private/tmp/rollback.dump.age",
            rollback_bundle_path="/private/tmp/rollback-bundle.json",
            authorization_receipt_path="/private/tmp/authorization.json",
            application_receipt_path="/private/tmp/application.json",
            verification_receipt_path="/private/tmp/verification.json",
            rollback_receipt_path="/private/tmp/rollback-receipt.json",
            created_at=int(time.time()),
        )
        application = {
            "receipt_digest": "b" * 64,
            "post_generation": self.controller[
                "_operation_recovery_next_generation"
            ](plan["pre_generation"]),
        }
        body = {
            "schema_version": 1,
            "kind": "operation-recovery-rollback-journal",
            "plan_digest": plan["plan_digest"],
            "application_receipt_digest": application[
                "receipt_digest"
            ],
            "pre_generation": application["post_generation"],
            "post_generation": self.controller[
                "_operation_recovery_next_generation"
            ](application["post_generation"]),
            "selected_operation_count": plan["selected_operation_count"],
            "installation_authority_digest": self.controller["digest"](
                plan["installation_authority"]
            ),
            "recorded_at": int(time.time()),
        }
        journal = {
            **body,
            "receipt_digest": self.controller["digest"](body),
        }
        self.controller["_operation_recovery_validate_rollback_journal"](
            journal,
            plan=plan,
            application=application,
        )
        receipt = self.controller[
            "_operation_recovery_finalize_rollback_journal"
        ](journal)
        self.controller["_operation_recovery_validate_rollback_receipt"](
            receipt,
            plan=plan,
            application=application,
        )

    def test_age_encrypt_preimage_requires_exact_native_age_header(self):
        encrypt = self.controller["_age_encrypt_preimage"]
        globals_ = encrypt.__globals__
        subprocess_calls = []

        def run_private_subprocess(*arguments, **_keywords):
            subprocess_calls.append(arguments[0])
            return SimpleNamespace(
                stdout=b"age-encryption.org/v2\nciphertext"
            )

        replacements = {
            "_operation_recovery_validate_recipient": (
                lambda _value, _label: None
            ),
            "_operation_recovery_preimage_bytes": (
                lambda *_arguments, **_keywords: b"{}"
            ),
            "_private_identity_descriptor": (
                lambda _path: os.open(os.devnull, os.O_RDONLY)
            ),
            "_stage_operation_recovery_tools": (
                lambda _tools: (
                    object(),
                    {
                        "age": SimpleNamespace(
                            path=Path("/private/tmp/pinned-age")
                        )
                    },
                )
            ),
            "_run_private_subprocess": run_private_subprocess,
            "_remove_operation_recovery_tools": lambda _root: None,
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            with tempfile.TemporaryDirectory(
                dir="/private/tmp",
                prefix="hindsight-age-header-",
            ) as directory:
                identity_path = Path(directory) / "identity.txt"
                identity_path.write_text("test-only", encoding="utf-8")
                identity_path.chmod(0o600)
                with self.assertRaisesRegex(
                    Exception,
                    "rollback bundle encryption failed",
                ):
                    encrypt(
                        age_path=Path("/private/tmp/age"),
                        age_identity_path=identity_path,
                        recipient="age1test",
                        preimage=[],
                        plan_digest="a" * 64,
                    )
        finally:
            globals_.update(originals)

        self.assertEqual(
            subprocess_calls,
            [
                (
                    "/private/tmp/pinned-age",
                    "--encrypt",
                    "--recipient",
                    "age1test",
                )
            ],
        )

    def test_toolchain_rejects_a_caller_selected_executable(self):
        resolver = self.controller["_operation_recovery_tool"]
        with self.assertRaisesRegex(Exception, "path differs"):
            resolver(Path("/bin/echo"), "age")

    def test_toolchain_executes_private_content_pinned_copy(self):
        stage = self.controller["_stage_operation_recovery_tools"]
        run = self.controller["_run_private_subprocess"]
        remove = self.controller["_remove_operation_recovery_tools"]
        expected = self.controller["EXPECTED_OPERATION_RECOVERY_TOOLCHAIN"]
        root, tools = stage({"age": Path(expected["age"]["path"])})
        try:
            pinned = tools["age"]
            self.assertNotEqual(pinned.path, Path(expected["age"]["path"]))
            self.assertEqual(pinned.sha256, expected["age"]["sha256"])
            run(
                (str(pinned.path), "--version"),
                pinned_tool=pinned,
                tool_key="age",
            )
        finally:
            remove(root)
        self.assertFalse(root.exists())

    def test_private_subprocess_accepts_one_anonymous_stdin_descriptor(self):
        stage = self.controller["_stage_operation_recovery_tools"]
        run = self.controller["_run_private_subprocess"]
        remove = self.controller["_remove_operation_recovery_tools"]
        expected = self.controller["EXPECTED_OPERATION_RECOVERY_TOOLCHAIN"]
        subprocess_module = run.__globals__["subprocess"]
        original_run = subprocess_module.run
        seen = {}

        def capture(*arguments, **keywords):
            seen.update(keywords)
            return subprocess.CompletedProcess(arguments[0], 0)

        root, tools = stage({"age": Path(expected["age"]["path"])})
        descriptor, path = tempfile.mkstemp(
            dir="/private/tmp",
            prefix="hindsight-anonymous-stdin-",
        )
        os.unlink(path)
        try:
            subprocess_module.run = capture
            run(
                (str(tools["age"].path), "--version"),
                pinned_tool=tools["age"],
                tool_key="age",
                stdin=descriptor,
            )
            self.assertEqual(seen["stdin"], descriptor)
            with self.assertRaisesRegex(
                Exception,
                "input sources are ambiguous",
            ):
                run(
                    (str(tools["age"].path), "--version"),
                    pinned_tool=tools["age"],
                    tool_key="age",
                    stdin=descriptor,
                    input_value=b"ambiguous",
                )
        finally:
            subprocess_module.run = original_run
            os.close(descriptor)
            remove(root)
        self.assertFalse(root.exists())

    def test_postgres_toolchain_executes_private_prefix_copy(self):
        stage = self.controller["_stage_operation_recovery_tools"]
        run = self.controller["_run_private_subprocess"]
        remove = self.controller["_remove_operation_recovery_tools"]
        expected = self.controller["EXPECTED_OPERATION_RECOVERY_TOOLCHAIN"]
        root, tools = stage(
            {"pg_dump": Path(expected["pg_dump"]["path"])}
        )
        try:
            pinned = tools["pg_dump"]
            dependencies = {
                "libssl": tools["libssl"],
                "libcrypto": tools["libcrypto"],
            }
            self.assertTrue(
                str(pinned.path).startswith(str(root / "postgres" / "bin"))
            )
            run(
                (str(pinned.path), "--version"),
                pinned_tool=pinned,
                tool_key="pg_dump",
                pinned_dependencies=dependencies,
            )
            postgres = tools["postgres"]
            result = run(
                (str(postgres.path), "--version"),
                pinned_tool=postgres,
                tool_key="postgres",
                pinned_dependencies=dependencies,
                environment={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "DYLD_PRINT_LIBRARIES": "1",
                },
                stderr=subprocess.PIPE,
            )
            loaded = result.stderr.decode("utf-8", errors="replace")
            self.assertIn(
                str(root / "openssl" / "libssl.3.dylib"),
                loaded,
            )
            self.assertIn(
                str(root / "openssl" / "libcrypto.3.dylib"),
                loaded,
            )
        finally:
            remove(root)
        self.assertFalse(root.exists())

    def test_historical_backup_authority_rejects_another_artifact(self):
        authority = self.controller[
            "_operation_recovery_historical_source_authority"
        ]
        with self.assertRaisesRegex(Exception, "authority differs"):
            authority(Path("/bin/echo"), "0" * 64)

    def test_recipient_validation_uses_canonical_keygen_output(self):
        validate = self.controller[
            "_operation_recovery_validate_recipient"
        ]
        globals_ = validate.__globals__
        original_digest = globals_[
            "EXPECTED_OPERATION_RECOVERY_RECIPIENT_SHA256"
        ]
        recipient = "age1canonicalrecipient"
        globals_["EXPECTED_OPERATION_RECOVERY_RECIPIENT_SHA256"] = (
            hashlib.sha256(f"{recipient}\n".encode("ascii")).hexdigest()
        )
        try:
            self.assertEqual(
                validate(recipient, "backup recipient"),
                recipient,
            )
            with self.assertRaisesRegex(Exception, "backup recipient"):
                validate(f"{recipient}\n", "backup recipient")
        finally:
            globals_[
                "EXPECTED_OPERATION_RECOVERY_RECIPIENT_SHA256"
            ] = original_digest

    def test_rollback_identity_rejects_alternate_private_key(self):
        pin_identity = self.controller[
            "_operation_recovery_rollback_identity_path"
        ]
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="hindsight-recovery-alternate-key-",
        ) as directory:
            alternate = Path(directory) / "key.txt"
            alternate.write_text("AGE-SECRET-KEY-TEST-ONLY\n", encoding="ascii")
            alternate.chmod(0o600)
            with self.assertRaisesRegex(
                Exception,
                "rollback identity path differs",
            ):
                pin_identity(alternate)

    def test_plan_normalizes_live_binding_before_drift_check(self):
        command = self.controller["operation_recovery_plan_command"]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        cohort = fixtures.cohort()
        snapshot = fixtures.live_snapshot()
        backup = recovery_fixtures.rollback_backup_evidence()
        raw_binding = dict(backup["source_authority"]["binding"])
        raw_binding["socket_dir"] = "/tmp"
        raw_binding["socket_path"] = "/tmp/.s.PGSQL.54329"
        normalized = self.controller["normalize_pg0_binding"](
            raw_binding,
            "operation-recovery live backup pg0 binding",
        )
        backup["source_authority"]["binding"] = normalized
        backup["source_authority_digest"] = self.controller["digest"](
            backup["source_authority"]
        )
        documents = {
            "cohort.json": cohort,
            "snapshot.json": snapshot,
            "rollback-backup.json": backup,
        }
        written = {}
        replacements = {
            "_operation_recovery_candidate": (
                lambda _args: recovery_fixtures.release_identity()
            ),
            "_operation_recovery_tool": lambda path, _key: path,
            "_operation_recovery_read_private_json": (
                lambda path, _label: documents[path]
            ),
            "_operation_recovery_toolchain_digest": (
                lambda: cohort["backup"]["toolchain_digest"]
            ),
            "_operation_recovery_rollback_encryption": (
                lambda _recipient: recovery_fixtures.rollback_encryption()
            ),
            "read_pg0_registration": lambda _profile: {
                **raw_binding,
                "_password": "local-capability",
            },
            "write_private": (
                lambda path, value, *, create_only: written.update(
                    {path: (value, create_only)}
                )
            ),
            "_print_result": lambda value: value,
            "EXPECTED_OPERATION_RECOVERY_SOURCE_BACKUP": {
                "path": cohort["backup"]["source_authority"][
                    "artifact_path"
                ],
                "sha256": cohort["backup"]["artifact_sha256"],
            },
        }
        originals = {
            key: globals_[key]
            for key in replacements
        }
        globals_.update(replacements)
        args = SimpleNamespace(
            age=recovery_fixtures.rollback_encryption()["age_path"],
            cohort="cohort.json",
            snapshot="snapshot.json",
            rollback_backup_evidence="rollback-backup.json",
            rollback_recipient=(
                recovery_fixtures.rollback_encryption()["recipient"]
            ),
            rollback_backup="/private/tmp/rollback.dump.age",
            rollback_bundle="/private/tmp/rollback.json",
            authorization_receipt="/private/tmp/authorization.json",
            application_receipt="/private/tmp/application.json",
            verification_receipt="/private/tmp/verification.json",
            rollback_receipt="/private/tmp/rollback-receipt.json",
            output="plan.json",
        )
        try:
            result = command(args)
        finally:
            globals_.update(originals)

        self.assertEqual(result["status"], "planned")
        plan, create_only = written["plan.json"]
        self.assertTrue(create_only)
        self.assertEqual(
            plan["rollback_backup"]["source_authority"]["binding"][
                "socket_dir"
            ],
            "/private/tmp",
        )

    def test_queue_blocker_command_writes_only_read_only_safe_evidence(self):
        command = self.controller[
            "operation_recovery_classify_queue_blockers_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        reference_plan = fixtures.requeue_plan()
        blocker = {
            **fixtures.queue_blocker_row(),
            "bank_id": "outside-bank",
            "operation_type": "outside-type",
            "retry_count": 1,
        }
        classification = self.controller[
            "create_global_queue_blocker_classification"
        ](
            [blocker],
            classifier_candidate_release={
                "source_commit": "9" * 40,
                "version": "2026.07.31+9999999.operation-recovery.6",
                "release_digest": "8" * 64,
            },
            reference_plan=reference_plan,
            installation_authority=recovery_fixtures.installation_authority(),
            generation_before="systalyze:public:123",
            generation_after="systalyze:public:123",
            guard_contract_version=1,
            guard_contract_digest="a" * 64,
            observed_at=reference_plan["expires_at"] + 1,
        )
        written = {}

        async def classify(_args, _plan, _candidate):
            return classification

        replacements = {
            "_operation_recovery_candidate": (
                lambda _args: classification["classifier_candidate_release"]
            ),
            "_operation_recovery_read_private_json": (
                lambda _path, _label: reference_plan
            ),
            "_classify_global_queue_blockers": (
                classify
            ),
            "write_private": (
                lambda path, value, *, create_only: written.update(
                    {path: (value, create_only)}
                )
            ),
            "_print_result": lambda value: value,
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        args = SimpleNamespace(
            reference_plan="reference-plan.json",
            output="global-queue-blockers.json",
        )
        try:
            result = command(args)
        finally:
            globals_.update(originals)

        self.assertEqual(
            set(result),
            {
                "status",
                "classification_digest",
                "blocker_count",
                "expires_at",
                "output",
            },
        )
        self.assertEqual(result["status"], "classified")
        self.assertEqual(result["blocker_count"], 1)
        self.assertNotIn("operation_id", result)
        self.assertNotIn("bank_counts", result)
        self.assertNotIn("operation_type_counts", result)
        stored, create_only = written["global-queue-blockers.json"]
        self.assertIs(create_only, True)
        self.assertEqual(stored, classification)

    def test_claim_release_plan_command_is_read_only_and_digest_bound(self):
        command = self.controller[
            "operation_recovery_claim_release_plan_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        predecessor, live, nonclaim_digests = fixtures.claim_release_inputs(
            planned_at=int(time.time())
        )
        reference_plan = fixtures.requeue_plan()
        permitted_rows = fixtures.permitted_blocker_rows(reference_plan)
        for classification in (predecessor, live):
            body = {
                **{
                    key: value
                    for key, value in classification.items()
                    if key != "classification_digest"
                },
                "guard_contract_version": self.controller[
                    "QUEUE_BLOCKER_GUARD_CONTRACT_VERSION"
                ],
                "guard_contract_digest": self.controller[
                    "QUEUE_BLOCKER_GUARD_CONTRACT_DIGEST"
                ],
            }
            classification.clear()
            classification.update(
                {**body, "classification_digest": self.controller["digest"](body)}
            )
        candidate = recovery_fixtures.release_identity()
        authority = recovery_fixtures.installation_authority()
        encryption = recovery_fixtures.rollback_encryption()
        documents = {
            "predecessor.json": predecessor,
            "live.json": live,
            "reference.json": reference_plan,
        }
        written = {}

        async def evidence(_args, classification, reference):
            self.assertEqual(classification, live)
            self.assertEqual(reference, reference_plan)
            return authority, nonclaim_digests, permitted_rows

        replacements = {
            "_operation_recovery_candidate": lambda _args: candidate,
            "_operation_recovery_tool": (
                lambda _path, _key: Path(encryption["age_path"])
            ),
            "_operation_recovery_read_private_json": (
                lambda path, _label: documents[str(path)]
            ),
            "_claim_release_plan_evidence": evidence,
            "_operation_recovery_rollback_encryption": (
                lambda recipient: {
                    "recipient": recipient,
                    "age_sha256": encryption["age_sha256"],
                }
            ),
            "write_private": (
                lambda path, value, *, create_only: written.update(
                    {str(path): (value, create_only)}
                )
            ),
            "_print_result": lambda value: value,
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        args = SimpleNamespace(
            predecessor_classification="predecessor.json",
            predecessor_classification_digest=(
                predecessor["classification_digest"]
            ),
            live_classification="live.json",
            reference_plan="reference.json",
            age=encryption["age_path"],
            rollback_recipient=encryption["recipient"],
            rollback_bundle="/private/tmp/claim-release.bundle.json",
            authorization_receipt=(
                "/private/tmp/claim-release.authorization.json"
            ),
            application_receipt=(
                "/private/tmp/claim-release.application.json"
            ),
            verification_receipt=(
                "/private/tmp/claim-release.verification.json"
            ),
            rollback_receipt=(
                "/private/tmp/claim-release.rollback.json"
            ),
            output="/private/tmp/claim-release.plan.json",
        )
        try:
            result = command(args)
        finally:
            globals_.update(originals)

        self.assertEqual(
            set(result),
            {
                "status",
                "plan_digest",
                "expires_at",
                "selected_row_count",
                "permitted_blocker_count",
                "output",
            },
        )
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["selected_row_count"], 43)
        self.assertEqual(
            result["permitted_blocker_count"],
            len(recovery_fixtures.PERMITTED_POSITIONS),
        )
        plan, create_only = written[args.output]
        self.assertIs(create_only, True)
        self.assertEqual(plan["authority"], "unapproved-plan")
        self.assertIs(plan["mutation_authorized"], False)
        self.assertEqual(
            plan["predecessor_classification_digest"],
            predecessor["classification_digest"],
        )
        self.assertEqual(
            plan["live_classification_digest"],
            live["classification_digest"],
        )
        serialized = json.dumps(plan, sort_keys=True)
        self.assertNotIn('"task_payload":', serialized)
        self.assertNotIn('"worker_id":', serialized)
        self.assertNotIn('"error_message":', serialized)

        alias_args = SimpleNamespace(**vars(args))
        alias_args.output = args.authorization_receipt.upper()
        written.clear()
        globals_.update(replacements)
        try:
            with self.assertRaisesRegex(
                Exception,
                "plan path aliases an artifact",
            ):
                command(alias_args)
        finally:
            globals_.update(originals)
        self.assertEqual(written, {})

        drifted_candidate = {**candidate, "release_digest": "0" * 64}
        candidates = iter((candidate, drifted_candidate))
        drift_replacements = {
            **replacements,
            "_operation_recovery_candidate": lambda _args: next(candidates),
            "write_private": lambda *_args, **_kwargs: self.fail(
                "drifted candidate must not write a plan"
            ),
        }
        globals_.update(drift_replacements)
        try:
            with self.assertRaisesRegex(
                Exception,
                "candidate drifted during claim-release planning",
            ):
                command(args)
        finally:
            globals_.update(originals)

    def test_claim_release_already_applied_skips_preimage_recapture(self):
        command = self.controller[
            "operation_recovery_claim_release_apply_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        planned_at = int(time.time())
        predecessor, live, nonclaim_digests = fixtures.claim_release_inputs(
            planned_at=planned_at,
            live_generation="systalyze:public:123",
        )
        candidate = recovery_fixtures.release_identity()
        authority = recovery_fixtures.installation_authority()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="claim-release-applied-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_claim_release_plan"](
                predecessor,
                live,
                reference_plan=fixtures.requeue_plan(),
                permitted_blocker_rows=fixtures.permitted_blocker_rows(),
                nonclaim_state_digests=nonclaim_digests,
                candidate_release=candidate,
                installation_authority=authority,
                rollback_encryption=(
                    recovery_fixtures.rollback_encryption()
                ),
                rollback_bundle_path=str(root / "rollback-bundle.json"),
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                verification_receipt_path=str(root / "verification.json"),
                rollback_receipt_path=str(root / "rollback.json"),
                created_at=planned_at,
            )
            application = {
                "kind": (
                    "operation-recovery-claim-release-application-receipt"
                ),
                "pre_generation": plan["pre_generation"],
                "post_generation": self.controller[
                    "_operation_recovery_next_generation"
                ](plan["pre_generation"]),
                "receipt_digest": "b" * 64,
            }
            for path in (
                Path(plan["authorization_receipt_path"]),
                Path(plan["application_receipt_path"]),
            ):
                path.write_text("{}", encoding="utf-8")
                path.chmod(0o600)

            async def recapture(*_args, **_kwargs):
                self.fail("already-applied retries must not recapture preimage")

            class Manager:
                pass

            documents = {
                "plan.json": plan,
                plan["application_receipt_path"]: application,
            }
            replacements = {
                "_operation_recovery_candidate": lambda _args: candidate,
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_claim_release_assert_guard": lambda _plan: None,
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_install_lock": (
                    lambda _manager, *, expires_at: nullcontext()
                ),
                "_operation_recovery_lock": (
                    lambda _manager, *, expires_at: nullcontext()
                ),
                "_operation_recovery_authority": (
                    lambda _args, **_kwargs: authority
                ),
                "_claim_release_validate_authorization": (
                    lambda _plan: {"receipt_digest": "a" * 64}
                ),
                "_claim_release_validate_application": (
                    lambda _value, *, plan: application
                ),
                "_claim_release_prepare_apply": recapture,
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                result = command(
                    SimpleNamespace(
                        plan="plan.json",
                        approval_digest=plan["plan_digest"],
                    )
                )
            finally:
                globals_.update(originals)

            journal = {
                **application,
                "kind": (
                    "operation-recovery-claim-release-application-journal"
                ),
            }
            documents[plan["application_receipt_path"]] = journal

            async def current_generation(_args):
                return journal["post_generation"]

            journal_replacements = {
                **replacements,
                "_claim_release_validate_journal": (
                    lambda _value, *, plan: journal
                ),
                "_claim_release_current_generation": current_generation,
            }
            journal_originals = {
                key: globals_[key] for key in journal_replacements
            }
            globals_.update(journal_replacements)
            try:
                journal_result = command(
                    SimpleNamespace(
                        plan="plan.json",
                        approval_digest=plan["plan_digest"],
                    )
                )
            finally:
                globals_.update(journal_originals)

        self.assertEqual(result["status"], "already-applied")
        self.assertEqual(result["plan_digest"], plan["plan_digest"])
        self.assertEqual(journal_result["status"], "verification-required")
        self.assertEqual(
            journal_result["post_generation"],
            journal["post_generation"],
        )

    def test_claim_release_apply_rechecks_candidate_before_transaction(self):
        command = self.controller[
            "operation_recovery_claim_release_apply_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        planned_at = int(time.time())
        predecessor, live, nonclaim_digests = fixtures.claim_release_inputs(
            planned_at=planned_at,
            live_generation="systalyze:public:123",
        )
        candidate = recovery_fixtures.release_identity()
        authority = recovery_fixtures.installation_authority()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="claim-release-apply-candidate-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_claim_release_plan"](
                predecessor,
                live,
                reference_plan=fixtures.requeue_plan(),
                permitted_blocker_rows=fixtures.permitted_blocker_rows(),
                nonclaim_state_digests=nonclaim_digests,
                candidate_release=candidate,
                installation_authority=authority,
                rollback_encryption=(
                    recovery_fixtures.rollback_encryption()
                ),
                rollback_bundle_path=str(root / "rollback-bundle.json"),
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                verification_receipt_path=str(root / "verification.json"),
                rollback_receipt_path=str(root / "rollback.json"),
                created_at=planned_at,
            )
            drifted_candidate = {
                **candidate,
                "release_digest": "0" * 64,
            }
            candidates = iter((candidate, drifted_candidate))
            written = {}

            async def prepare(_args, _plan):
                return {"ciphertext_sha256": "c" * 64}

            async def apply(*_args, **_kwargs):
                self.fail("candidate drift must prevent database apply")

            class Manager:
                pass

            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: next(candidates)
                ),
                "_operation_recovery_read_private_json": (
                    lambda _path, _label: plan
                ),
                "_claim_release_assert_guard": lambda _plan: None,
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_precommit_artifacts": (
                    lambda: nullcontext(
                        {"mutation_attempted": False, "created": []}
                    )
                ),
                "_operation_recovery_install_lock": (
                    lambda _manager, *, expires_at: nullcontext()
                ),
                "_operation_recovery_lock": (
                    lambda _manager, *, expires_at: nullcontext()
                ),
                "_operation_recovery_authority": (
                    lambda _args, **_kwargs: authority
                ),
                "_claim_release_prepare_apply": prepare,
                "_claim_release_apply": apply,
                "_operation_recovery_artifact_identity": (
                    lambda path: (path, "test")
                ),
                "write_private": (
                    lambda path, value, **_kwargs: written.update(
                        {str(path): value}
                    )
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(Exception, "candidate drifted"):
                    command(
                        SimpleNamespace(
                            plan="plan.json",
                            approval_digest=plan["plan_digest"],
                        )
                    )
            finally:
                globals_.update(originals)

        self.assertIn(plan["application_receipt_path"], written)

    def test_claim_release_rollback_rechecks_candidate_before_transaction(self):
        command = self.controller[
            "operation_recovery_claim_release_rollback_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        planned_at = int(time.time())
        predecessor, live, nonclaim_digests = fixtures.claim_release_inputs(
            planned_at=planned_at,
            live_generation="systalyze:public:123",
        )
        candidate = recovery_fixtures.release_identity()
        authority = recovery_fixtures.installation_authority()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="claim-release-rollback-candidate-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_claim_release_plan"](
                predecessor,
                live,
                reference_plan=fixtures.requeue_plan(),
                permitted_blocker_rows=fixtures.permitted_blocker_rows(),
                nonclaim_state_digests=nonclaim_digests,
                candidate_release=candidate,
                installation_authority=authority,
                rollback_encryption=(
                    recovery_fixtures.rollback_encryption()
                ),
                rollback_bundle_path=str(root / "rollback-bundle.json"),
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                verification_receipt_path=str(root / "verification.json"),
                rollback_receipt_path=str(root / "rollback.json"),
                created_at=planned_at,
            )
            application = {
                "kind": (
                    "operation-recovery-claim-release-application-receipt"
                ),
                "pre_generation": plan["pre_generation"],
                "post_generation": self.controller[
                    "_operation_recovery_next_generation"
                ](plan["pre_generation"]),
                "receipt_digest": "b" * 64,
            }
            drifted_candidate = {
                **candidate,
                "release_digest": "0" * 64,
            }
            candidates = iter((candidate, drifted_candidate))
            bundle = {
                "ciphertext_base64": base64.b64encode(b"ciphertext").decode(
                    "ascii"
                )
            }
            preimage = {
                "schema_version": 1,
                "kind": self.controller["CLAIM_RELEASE_PREIMAGE_KIND"],
                "plan_digest": plan["plan_digest"],
                "rows": [],
            }

            class Manager:
                def _lock(self):
                    return nullcontext()

            async def connect(*_args, **_kwargs):
                self.fail("candidate drift must prevent database rollback")

            documents = {
                "plan.json": plan,
                plan["application_receipt_path"]: application,
            }
            replacements = {
                "_operation_recovery_candidate": (
                    lambda _args: next(candidates)
                ),
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_claim_release_assert_guard": lambda _plan: None,
                "_claim_release_validate_application": (
                    lambda _value, *, plan: application
                ),
                "_claim_release_validate_bundle": lambda _plan: bundle,
                "_operation_recovery_tool": (
                    lambda _path, _key: Path(
                        plan["rollback_encryption"]["age_path"]
                    )
                ),
                "EXPECTED_OPERATION_RECOVERY_TOOLCHAIN": {
                    "age": {
                        "sha256": plan["rollback_encryption"]["age_sha256"]
                    }
                },
                "_operation_recovery_rollback_identity_path": (
                    lambda _path: Path("/dev/null")
                ),
                "_age_decrypt_ciphertext": (
                    lambda **_kwargs: json.dumps(preimage).encode("utf-8")
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": (
                    lambda _manager: nullcontext()
                ),
                "_operation_recovery_authority": (
                    lambda _args, **_kwargs: authority
                ),
                "_operation_recovery_connect_live": connect,
                "write_private": lambda *_args, **_kwargs: None,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(Exception, "candidate drifted"):
                    command(
                        SimpleNamespace(
                            plan="plan.json",
                            approval_digest=self.controller[
                                "_claim_release_rollback_approval"
                            ](plan, application),
                            age=plan["rollback_encryption"]["age_path"],
                            age_identity="/dev/null",
                        )
                    )
            finally:
                globals_.update(originals)

    def test_claim_release_verify_rechecks_authority_after_live_evidence(self):
        command = self.controller[
            "operation_recovery_claim_release_verify_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        planned_at = int(time.time())
        predecessor, live, nonclaim_digests = fixtures.claim_release_inputs(
            planned_at=planned_at,
            live_generation="systalyze:public:123",
        )
        authority = recovery_fixtures.installation_authority()
        candidate = recovery_fixtures.release_identity()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="claim-release-verify-authority-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_claim_release_plan"](
                predecessor,
                live,
                reference_plan=fixtures.requeue_plan(),
                permitted_blocker_rows=fixtures.permitted_blocker_rows(),
                nonclaim_state_digests=nonclaim_digests,
                candidate_release=candidate,
                installation_authority=authority,
                rollback_encryption=(
                    recovery_fixtures.rollback_encryption()
                ),
                rollback_bundle_path=str(root / "rollback-bundle.json"),
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                verification_receipt_path=str(root / "verification.json"),
                rollback_receipt_path=str(root / "rollback.json"),
                created_at=planned_at,
            )
            application = {
                "kind": (
                    "operation-recovery-claim-release-application-receipt"
                ),
                "pre_generation": plan["pre_generation"],
                "post_generation": self.controller[
                    "_operation_recovery_next_generation"
                ](plan["pre_generation"]),
                "receipt_digest": "b" * 64,
            }
            documents = {
                "plan.json": plan,
                plan["application_receipt_path"]: application,
            }
            drifted_authority = {
                **authority,
                "install_state_digest": "0" * 64,
            }
            authorities = iter((authority, drifted_authority))
            authority_calls = []

            def read_authority(_args, **_kwargs):
                value = next(authorities)
                authority_calls.append(value)
                return value

            async def verify_live(_args, _plan, _application):
                return {
                    "generation": application["post_generation"],
                    "selected_row_count": 43,
                    "selected_row_set_digest": plan[
                        "selected_row_set_digest"
                    ],
                }

            class Manager:
                def _lock(self):
                    return nullcontext()

            replacements = {
                "_operation_recovery_candidate": lambda _args: candidate,
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_claim_release_assert_guard": lambda _plan: None,
                "_claim_release_validate_application": (
                    lambda _value, *, plan: application
                ),
                "_portable_manager": lambda _args: Manager(),
                "_operation_recovery_lock": (
                    lambda _manager: nullcontext()
                ),
                "_operation_recovery_authority": read_authority,
                "_claim_release_verify_live": verify_live,
                "write_private": lambda *_args, **_kwargs: self.fail(
                    "authority drift must not write a receipt"
                ),
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                with self.assertRaisesRegex(Exception, "authority drifted"):
                    command(SimpleNamespace(plan="plan.json"))
            finally:
                globals_.update(originals)

        self.assertEqual(authority_calls, [authority, drifted_authority])

    def test_claim_release_status_reports_valid_rollback_journal(self):
        command = self.controller[
            "operation_recovery_claim_release_status_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        planned_at = int(time.time())
        predecessor, live, nonclaim_digests = fixtures.claim_release_inputs(
            planned_at=planned_at,
            live_generation="systalyze:public:123",
        )
        candidate = recovery_fixtures.release_identity()
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="claim-release-status-journal-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            plan = self.controller["create_claim_release_plan"](
                predecessor,
                live,
                reference_plan=fixtures.requeue_plan(),
                permitted_blocker_rows=fixtures.permitted_blocker_rows(),
                nonclaim_state_digests=nonclaim_digests,
                candidate_release=candidate,
                installation_authority=(
                    recovery_fixtures.installation_authority()
                ),
                rollback_encryption=(
                    recovery_fixtures.rollback_encryption()
                ),
                rollback_bundle_path=str(root / "rollback-bundle.json"),
                authorization_receipt_path=str(root / "authorization.json"),
                application_receipt_path=str(root / "application.json"),
                verification_receipt_path=str(root / "verification.json"),
                rollback_receipt_path=str(root / "rollback.json"),
                created_at=planned_at,
            )
            application = {"receipt_digest": "b" * 64}
            rollback_journal = {
                "kind": (
                    "operation-recovery-claim-release-rollback-journal"
                ),
                "receipt_digest": "c" * 64,
            }
            for path in (
                Path(plan["application_receipt_path"]),
                Path(plan["rollback_receipt_path"]),
            ):
                path.write_text("{}", encoding="utf-8")
                path.chmod(0o600)
            documents = {
                "plan.json": plan,
                plan["application_receipt_path"]: {
                    "kind": (
                        "operation-recovery-claim-release-application-receipt"
                    )
                },
                plan["rollback_receipt_path"]: rollback_journal,
            }
            rollback_kinds = []

            def validate_rollback(
                value,
                *,
                plan,
                application,
                kind,
            ):
                self.assertEqual(value, rollback_journal)
                rollback_kinds.append(kind)
                return rollback_journal

            replacements = {
                "_operation_recovery_candidate": lambda _args: candidate,
                "_operation_recovery_read_private_json": (
                    lambda path, _label: documents[str(path)]
                ),
                "_claim_release_assert_guard": lambda _plan: None,
                "_claim_release_validate_application": (
                    lambda _value, *, plan: application
                ),
                "_claim_release_rollback_approval": (
                    lambda _plan, _application: "d" * 64
                ),
                "_claim_release_validate_rollback_record": (
                    validate_rollback
                ),
                "_print_result": lambda value: value,
            }
            originals = {key: globals_[key] for key in replacements}
            globals_.update(replacements)
            try:
                result = command(SimpleNamespace(plan="plan.json"))
            finally:
                globals_.update(originals)

        self.assertEqual(result["status"], "rollback-journal-present")
        self.assertEqual(
            result["rollback_journal_digest"],
            rollback_journal["receipt_digest"],
        )
        self.assertNotIn("rolled_back", result)
        self.assertEqual(
            rollback_kinds,
            ["operation-recovery-claim-release-rollback-journal"],
        )

    def test_queue_blocker_command_rejects_candidate_drift(self):
        command = self.controller[
            "operation_recovery_classify_queue_blockers_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        reference_plan = fixtures.requeue_plan()
        initial_candidate = {
            "source_commit": "9" * 40,
            "version": "2026.07.31+9999999.operation-recovery.6",
            "release_digest": "8" * 64,
        }
        drifted_candidate = {
            **initial_candidate,
            "release_digest": "7" * 64,
        }
        classification = self.controller[
            "create_global_queue_blocker_classification"
        ](
            [fixtures.queue_blocker_row()],
            classifier_candidate_release=initial_candidate,
            reference_plan=reference_plan,
            installation_authority=recovery_fixtures.installation_authority(),
            generation_before="systalyze:public:123",
            generation_after="systalyze:public:123",
            guard_contract_version=1,
            guard_contract_digest="a" * 64,
            observed_at=reference_plan["expires_at"] + 1,
        )
        candidates = iter((initial_candidate, drifted_candidate))

        async def classify(_args, _plan, _candidate):
            return classification

        replacements = {
            "_operation_recovery_candidate": lambda _args: next(candidates),
            "_operation_recovery_read_private_json": (
                lambda _path, _label: reference_plan
            ),
            "_classify_global_queue_blockers": classify,
            "write_private": lambda *_args, **_kwargs: self.fail(
                "drifted candidate must not write an artifact"
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            with self.assertRaisesRegex(
                self.controller["OperationRecoveryError"],
                "candidate drifted during queue blocker classification",
            ):
                command(
                    SimpleNamespace(
                        reference_plan="reference-plan.json",
                        output="global-queue-blockers.json",
                    )
                )
        finally:
            globals_.update(originals)

    def _assert_queue_blocker_authority_drift(self, authorities):
        classify = self.controller["_classify_global_queue_blockers"]
        globals_ = classify.__globals__
        reference_plan = (
            recovery_fixtures.OperationRecoveryContractTest().requeue_plan()
        )
        candidate = {
            "source_commit": "9" * 40,
            "version": "2026.07.31+9999999.operation-recovery.6",
            "release_digest": "8" * 64,
        }
        connection_closed = []

        class Connection:
            async def fetchval(self, _query):
                return "7659746962107358086"

            async def close(self):
                connection_closed.append(True)

        async def connect(_args):
            return Connection()

        async def read_blockers(_connection, **_arguments):
            return "systalyze:public:123", "systalyze:public:123", []

        authority_calls = []
        authority_values = iter(authorities)

        def authority(_args, *, postgres_system_identifier):
            self.assertEqual(
                postgres_system_identifier,
                "7659746962107358086",
            )
            value = dict(next(authority_values))
            authority_calls.append(value)
            return value

        replacements = {
            "_operation_recovery_connect_live": connect,
            "read_global_queue_blockers": read_blockers,
            "_operation_recovery_authority": authority,
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            with self.assertRaisesRegex(Exception, "authority drifted"):
                asyncio.run(
                    classify(
                        SimpleNamespace(),
                        reference_plan,
                        candidate,
                    )
                )
        finally:
            globals_.update(originals)

        self.assertEqual(len(authority_calls), 2)
        self.assertEqual(connection_closed, [True])

    def test_queue_blocker_live_classification_rechecks_authority(self):
        reference_plan = (
            recovery_fixtures.OperationRecoveryContractTest().requeue_plan()
        )
        authority = reference_plan["installation_authority"]
        drifted = {**authority, "install_state_digest": "0" * 64}
        self._assert_queue_blocker_authority_drift([authority, drifted])

    def test_queue_blocker_live_classification_rejects_reference_authority_drift(self):
        reference_plan = (
            recovery_fixtures.OperationRecoveryContractTest().requeue_plan()
        )
        drifted = {
            **reference_plan["installation_authority"],
            "install_state_digest": "0" * 64,
        }
        self._assert_queue_blocker_authority_drift([drifted, drifted])

    def test_queue_blocker_live_classification_returns_bound_evidence(self):
        classify = self.controller["_classify_global_queue_blockers"]
        globals_ = classify.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        reference_plan = fixtures.requeue_plan()
        authority = reference_plan["installation_authority"]
        candidate = {
            "source_commit": "9" * 40,
            "version": "2026.07.31+9999999.operation-recovery.6",
            "release_digest": "8" * 64,
        }
        blocker = fixtures.queue_blocker_row()
        closed = []

        class Connection:
            async def fetchval(self, _query):
                return "7659746962107358086"

            async def close(self):
                closed.append(True)

        async def connect(_args):
            return Connection()

        async def read_blockers(_connection, **_arguments):
            return (
                "systalyze:public:123",
                "systalyze:public:123",
                [blocker],
            )

        replacements = {
            "_operation_recovery_connect_live": connect,
            "read_global_queue_blockers": read_blockers,
            "_operation_recovery_authority": (
                lambda _args, *, postgres_system_identifier: authority
            ),
        }
        originals = {key: globals_[key] for key in replacements}
        globals_.update(replacements)
        try:
            result = asyncio.run(
                classify(SimpleNamespace(), reference_plan, candidate)
            )
        finally:
            globals_.update(originals)

        expected = self.controller[
            "create_global_queue_blocker_classification"
        ](
            [blocker],
            classifier_candidate_release=candidate,
            reference_plan=reference_plan,
            installation_authority=authority,
            generation_before="systalyze:public:123",
            generation_after="systalyze:public:123",
            guard_contract_version=self.controller[
                "QUEUE_BLOCKER_GUARD_CONTRACT_VERSION"
            ],
            guard_contract_digest=self.controller[
                "QUEUE_BLOCKER_GUARD_CONTRACT_DIGEST"
            ],
            observed_at=result["observed_at"],
        )
        self.assertEqual(result, expected)
        self.assertEqual(closed, [True])

    def test_precommit_failure_removes_only_run_created_artifacts(self):
        cleanup = self.controller[
            "_operation_recovery_precommit_artifacts"
        ]
        identity = self.controller["_operation_recovery_artifact_identity"]
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="hindsight-precommit-",
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            created = root / "created.json"
            preserved = root / "preserved.json"
            created.write_text("{}", encoding="utf-8")
            preserved.write_text("{}", encoding="utf-8")
            created.chmod(0o600)
            preserved.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, "deadline"):
                with cleanup() as state:
                    state["created"].append(
                        (str(created), identity(str(created)))
                    )
                    raise RuntimeError("deadline")
            self.assertFalse(created.exists())
            self.assertTrue(preserved.exists())

    def test_disposable_classification_uses_verified_peer_connector(self):
        classify = self.controller["_classify_disposable_restore"]
        original_loader = classify.__globals__["_load_asyncpg"]
        original_connector = classify.__globals__[
            "connect_verified_local_postgres"
        ]
        seen = {}

        async def connector(
            asyncpg,
            binding,
            *,
            password,
            readonly,
        ):
            seen.update(
                {
                    "asyncpg": asyncpg,
                    "binding": binding,
                    "password": password,
                    "readonly": readonly,
                }
            )
            raise RuntimeError("verified connector reached")

        marker = object()
        classify.__globals__["_load_asyncpg"] = lambda: marker
        classify.__globals__["connect_verified_local_postgres"] = connector
        binding = {
            "pid": 123,
            "socket_dir": "/private/tmp/socket",
            "socket_path": "/private/tmp/socket/.s.PGSQL.55440",
            "port": 55440,
            "user": "hindsight",
            "database": "hindsight",
        }
        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "verified connector reached",
            ):
                asyncio.run(classify(binding=binding))
        finally:
            classify.__globals__["_load_asyncpg"] = original_loader
            classify.__globals__[
                "connect_verified_local_postgres"
            ] = original_connector
        self.assertIs(seen["asyncpg"], marker)
        self.assertEqual(seen["binding"], binding)
        self.assertTrue(seen["readonly"])
        self.assertEqual(
            seen["password"],
            "disposable-trust-auth-not-a-secret",
        )

    def test_pg_dump_proxy_uses_pid_verified_upstream_socket(self):
        create = self.controller["_create_pinned_recovery_root"]
        proxy = self.controller["_verified_postgres_proxy"]
        run_root = (
            Path("/private/tmp")
            / f"hindsight-operation-recovery-{os.getpid():032x}"
        )
        upstream_root = Path(f"/private/tmp/hpru-{os.getpid()}")
        upstream_root.mkdir(mode=0o700)
        upstream_path = upstream_root / ".s.PGSQL.55440"
        upstream_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        upstream_listener.bind(str(upstream_path))
        upstream_listener.listen(1)

        def echo_upstream():
            client, _address = upstream_listener.accept()
            try:
                body = client.recv(4096)
                client.sendall(body[::-1])
            finally:
                client.close()

        upstream_thread = threading.Thread(target=echo_upstream)
        upstream_thread.start()
        root_descriptor = -1
        try:
            root_descriptor, root_identity = create(run_root)
            with proxy(
                root_descriptor,
                root_identity,
                {
                    "port": 55440,
                    "pid": os.getpid(),
                    "socket_path": str(upstream_path),
                },
            ) as host:
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    client.connect(f"{host}/.s.PGSQL.55440")
                    client.sendall(b"capsule")
                    self.assertEqual(client.recv(4096), b"eluspac")
                finally:
                    client.close()
        finally:
            if root_descriptor >= 0:
                os.close(root_descriptor)
            upstream_listener.close()
            upstream_thread.join(timeout=5)
            if run_root.exists():
                shutil.rmtree(run_root)
            shutil.rmtree(upstream_root)


if __name__ == "__main__":
    unittest.main()
