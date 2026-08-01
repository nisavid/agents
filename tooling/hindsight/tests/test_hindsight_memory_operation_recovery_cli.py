import asyncio
import base64
import hashlib
import json
import os
import runpy
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from tooling.hindsight.tests import (
    test_hindsight_memory_operation_recovery as recovery_fixtures,
)


ROOT = Path(__file__).resolve().parents[1]


class OperationRecoveryCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = runpy.run_path(str(ROOT / "bin" / "hindsight-memory"))

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
        }
        written = {}

        async def evidence(_args, classification):
            self.assertEqual(classification, live)
            return authority, nonclaim_digests

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
                "output",
            },
        )
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["selected_row_count"], 43)
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
