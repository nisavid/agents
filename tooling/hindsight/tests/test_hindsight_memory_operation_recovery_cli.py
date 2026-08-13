import asyncio
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
    four_codex_policy_data,
    policy_data,
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
    ops_target = target.parent / "db" / "ops_postgresql.py"
    ops_target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    shutil.copyfile(ops_source, ops_target)
    ops_target.chmod(0o600)
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
        reference = fixtures.drain_plan()
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
                "--output",
                "/private/tmp/plan.json",
            ]
        )
        self.assertIs(
            plan.run,
            self.controller["operation_recovery_drain_plan_command"],
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
                            b"4242 /candidate/bin/"
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
                clock=lambda: 1000.0,
            )
            active_request = recorder.provider_started(
                "work-codex",
                retry_attempt=1,
                scope="retain_extract_facts",
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
                terminal_body = {
                    "schema_version": 1,
                    "kind": "operation-recovery-exact-drain-status",
                    "plan_digest": plan["plan_digest"],
                    "generation_before": "systalyze:public:200",
                    "generation_after": "systalyze:public:200",
                    "selected_operation_count": 43,
                    "selected_status_counts": {"completed": 43},
                    "preserved_status_counts": {"completed": 5},
                    "outside_nonterminal_counts": [],
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
                    time=lambda: authorization["authorized_at"] + 86_400
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
        self.assertEqual(captured["selected_status_counts"], {"pending": 43})
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
            terminal_captured["terminal_status_digest"],
            terminal["status_digest"],
        )

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
                observed_time[0] = authorization["authorized_at"] + 86_400
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
            authorization["authorized_at"] + 86_400,
        )
        self.assertEqual(
            authorized["execution_lease_remaining_seconds"],
            86_398,
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
                    lambda: "6" * 64
                ),
                "_operation_recovery_exact_runtime_digest": (
                    lambda _args, *, schema_version=4: (
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
            self.assertEqual(runtime_schemas, [5])
            plan, create_only = written[args.output]
            self.assertIs(create_only, True)
            self.assertIs(plan["mutation_authorized"], False)
            self.assertEqual(plan["schema_version"], 5)
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
                    "query-batch repair snapshot is required",
                ):
                    command(args)
            finally:
                globals_.update(originals)
            self.assertEqual(writes, [])

    def test_post_abort_plan_command_emits_exact_current_v5_subset(self):
        command = self.controller[
            "operation_recovery_post_abort_plan_command"
        ]
        globals_ = command.__globals__
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        reference = fixtures.drain_plan()
        snapshot = fixtures.post_abort_v5_snapshot(
            reference,
            observed_at=int(time.time()),
        )
        with tempfile.TemporaryDirectory(
            dir="/private/tmp",
            prefix="post-abort-v2-plan-",
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
                    lambda _path, *, plan_digest: dict(progress_value)
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
            self.assertEqual(result["selected_operation_count"], 5)
            plan, create_only = written[args.output]
            self.assertIs(create_only, True)
            self.assertEqual(plan["schema_version"], 5)
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
                {"failed": 4, "processing": 1},
            )
            self.assertEqual(
                plan["selected_type_counts"],
                {"retain": 5},
            )
            self.assertEqual(
                plan["preserved_status_counts"],
                {"completed": 6, "pending": 37},
            )
            serialized = json.dumps(plan, sort_keys=True)
            self.assertNotIn('"task_payload":', serialized)
            self.assertNotIn('"worker_id":', serialized)
            self.assertNotIn('"error_message":', serialized)

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
            environment["HINDSIGHT_API_WORKER_MAX_SLOTS"], "2"
        )
        self.assertEqual(
            environment[
                "HINDSIGHT_API_WORKER_CONSOLIDATION_RESERVED_SLOTS"
            ],
            "1",
        )
        for operation_type in (
            "RETAIN",
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
            self.assertEqual(snapshot["schema_version"], 4)
            patched_resolver = resolver.read_text(encoding="utf-8")
            self.assertNotEqual(resolver.read_bytes(), original_resolver)
            trigram_source = patched_resolver.split(
                "    async def _resolve_entities_batch_trigram(",
                1,
            )[1].split(
                "    async def _resolve_entities_batch_oracle_fuzzy(",
                1,
            )[0]
            self.assertNotIn(
                "SELECT e.id, e.canonical_name, e.metadata, e.last_seen, e.mention_count,",
                trigram_source,
            )
            self.assertIn(
                "WHERE ec.entity_id_1 = ANY($1::uuid[])\n"
                "                   AND ec.entity_id_2 = ANY($1::uuid[])",
                trigram_source,
            )
            self.assertIn(
                "retain.phase1.candidates.exact.", trigram_source
            )
            self.assertIn(
                "retain.phase1.candidates.fuzzy.", trigram_source
            )
            self.assertIn("retain.phase1.cooccurrence", trigram_source)
            self.assertIn("retain.phase1.scoring", trigram_source)
            self.assertIn("timeout=120.0", trigram_source)
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
            self.assertEqual(value["schema_version"], 4)
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
            self.assertEqual(recovered["schema_version"], 4)
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
            self.assertEqual(recovered["schema_version"], 4)

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
                self.assertEqual(recovered["schema_version"], 4)
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
            self.assertEqual(recovered["schema_version"], 4)
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
            self.assertEqual(recovered["schema_version"], 4)
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
                    self.assertEqual(recovered["schema_version"], 4)
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
                self.assertEqual(recovered["schema_version"], 4)
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
                from hindsight_api.engine.entity_resolver import EntityResolver

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

                class ExternalEdge:
                    def __getitem__(self, _key):
                        raise AssertionError("noncandidate edge decoded")

                class Connection:
                    def __init__(self, now):
                        self.now = now
                        self.execute_calls = []
                        self.query_batch_sizes = []
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
                        if timeout != 120.0:
                            raise AssertionError("client deadline differs")
                        if "entity_cooccurrences" in query:
                            if " OR " in query:
                                return [ExternalEdge()]
                            if " AND " not in query:
                                raise AssertionError("cooccurrence scope differs")
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

                def projection(values):
                    return [
                        (item.entity_id, item.canonical_name, item.entity_kind)
                        for item in values
                    ]

                async def exercise():
                    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
                    entities = [
                        {{"text": "Alicee", "nearby_entities": [{{"text": "Bob"}}]}},
                        {{"text": "Bob", "nearby_entities": [{{"text": "Alicee"}}]}},
                    ] + [
                        {{"text": f"Entity{{position:02d}}x", "nearby_entities": []}}
                        for position in range(23)
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
                    return {{
                        "expected": projection(expected),
                        "actual": projection(actual),
                        "expected_stats": expected_stats,
                        "actual_stats": actual_stats,
                        "execute_calls": actual_connection.execute_calls,
                        "query_batch_sizes": actual_connection.query_batch_sizes,
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
            self.assertEqual(
                observed["actual_stats"],
                observed["expected_stats"],
            )
            self.assertEqual(
                observed["execute_calls"],
                [
                    "SET TRANSACTION READ ONLY",
                    "SET LOCAL statement_timeout = '120s'",
                ]
                * 4,
            )
            self.assertEqual(observed["query_batch_sizes"], [10, 10, 5])

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
        body = policy_path.read_bytes()
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

    def test_exact_drain_provider_policy_requires_four_dedicated_codex_homes(self):
        validate = self.controller[
            "_operation_recovery_validate_exact_provider_policy"
        ]
        policy_value = four_codex_policy_data()
        renamed = {
            "work": "work-codex",
            "personal": "personal-codex",
            "alt1": "alt1-codex",
            "alt2": "alt2-codex",
            "fallback": "hatchery",
        }
        for member in policy_value["members"]:
            member_id = renamed[member["id"]]
            member["id"] = member_id
            if member_id == "hatchery":
                member["identity"]["base_url"] = (
                    "http://hatchery.komodo-vector.ts.net:13305/v1"
                )
            else:
                member["identity"]["credential_marker"] = (
                    f"provider-policy:{member_id}"
                )
        policy_value["failover_order"] = [
            "work-codex",
            "personal-codex",
            "alt1-codex",
            "alt2-codex",
            "hatchery",
        ]
        policy = self.controller["ProviderRuntimePolicy"].load(
            policy_value
        )
        validate(policy)
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
        bounded_hatchery = replace(
            hatchery,
            timeout_seconds=1200,
            max_retries=0,
        )
        bounded_policy = replace(
            policy,
            members=tuple(
                bounded_hatchery if member.id == hatchery.id else member
                for member in policy.members
            ),
        )
        validate(bounded_policy)
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
                            timeout_seconds=300,
                            max_retries=1,
                        )
                        if member.id == hatchery.id
                        else member
                        for member in bounded_policy.members
                    ),
                )
            )

    def test_exact_drain_effective_profile_requires_the_policy_projection(self):
        policy_path = (
            Path.home()
            / ".config/hindsight-control-plane/provider-runtime-policy.json"
        )
        _policy_digest, policy = self.controller[
            "_operation_recovery_exact_provider_policy_evidence"
        ](policy_path)
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
                    lambda _policy: None
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

    def test_expired_execution_lease_rejects_before_provider_activation(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        planned_at = int(time.time()) - 86_401
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
            "schema_version": 1,
            "kind": "operation-recovery-exact-drain-status",
            "plan_digest": plan["plan_digest"],
            "generation_before": "systalyze:public:200",
            "generation_after": "systalyze:public:200",
            "selected_operation_count": 43,
            "selected_status_counts": {"completed": 43},
            "preserved_status_counts": {"completed": 5},
            "outside_nonterminal_counts": [],
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
        planned_at = now - 86_401
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
            observed_time = [authorization["authorized_at"] + 86_399]

            def write_status(*_arguments, **_keywords):
                observed_time[0] = authorization["authorized_at"] + 86_400

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
                "schema_version": 1,
                "kind": "operation-recovery-exact-drain-status",
                "plan_digest": plan["plan_digest"],
                "generation_before": "systalyze:public:200",
                "generation_after": "systalyze:public:200",
                "selected_operation_count": 43,
                "selected_status_counts": {"completed": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
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

    def test_exact_drain_apply_does_not_launch_at_the_execution_lease_boundary(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        authorized_at = now - 86_400
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
                with self.assertRaisesRegex(
                    Exception,
                    "execution lease expired",
                ):
                    command(args)
            finally:
                globals_.update(originals)
            self.assertEqual(provider_activation, [])

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
                    time=lambda: authorization["authorized_at"] + 86_400
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
        self.assertEqual(len(wait_timeouts), 2)
        self.assertEqual(wait_timeouts[1], 120)
        self.assertEqual(
            journal["kind"],
            "operation-recovery-exact-drain-application-journal",
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
                "schema_version": 1,
                "kind": "operation-recovery-exact-drain-status",
                "plan_digest": plan["plan_digest"],
                "generation_before": "systalyze:public:250",
                "generation_after": "systalyze:public:250",
                "selected_operation_count": 43,
                "selected_status_counts": {"completed": 43},
                "preserved_status_counts": {"completed": 5},
                "outside_nonterminal_counts": [],
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
                    time=lambda: authorization["authorized_at"] + 86_401
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
                    time=lambda: authorization["authorized_at"] + 86_400
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
