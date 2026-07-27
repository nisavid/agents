from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hindsight_memory_control_plane.accidental_replay import (  # noqa: E402
    BACKUP_ARTIFACT_NAMES,
    ReplayError,
    _document_descriptor,
    _replay_item,
    apply_replay_closeout,
    apply_replay_plan as _apply_replay_plan,
    create_replay_closeout_plan,
    create_replay_plan,
    replay_receipt_status,
    replay_apply_authorization_digest,
    verify_replay_plan,
    verify_replay_receipts,
)
from hindsight_memory_control_plane.canonical import digest  # noqa: E402
from hindsight_memory_control_plane.model import BankRef  # noqa: E402


def backup_evidence_fixture():
    return {
        "schema_version": 1,
        "artifacts": {
            name: {
                "artifact_digest": str(index) * 64,
                "encrypted": True,
                "restore_evidence_digest": str(index + 3) * 64,
                "restore_tested": True,
            }
            for index, name in enumerate(
                sorted(BACKUP_ARTIFACT_NAMES),
                start=1,
            )
        },
    }


def apply_replay_plan(adapter, plan, **kwargs):
    backup_evidence = backup_evidence_fixture()
    return _apply_replay_plan(
        adapter,
        plan,
        approval_digest=replay_apply_authorization_digest(
            plan,
            backup_evidence,
        ),
        backup_evidence=backup_evidence,
        **kwargs,
    )


class FakeReplayAdapter:
    def __init__(self):
        self.generation = 1
        self.source = {
            "session-a": {
                "id": "session-a",
                "bank_id": "codex",
                "original_text": "first complete transcript",
                "content_hash": "upstream-a",
                "created_at": "2026-07-13T11:00:00Z",
                "updated_at": "2026-07-13T12:00:00Z",
                "memory_unit_count": 0,
                "tags": ["agent:codex"],
                "document_metadata": {"source": "codex-hook"},
                "retain_params": {
                    "context": "Codex session",
                    "observation_scopes": "combined",
                },
            },
            "session-b": {
                "id": "session-b",
                "bank_id": "codex",
                "original_text": "second complete transcript",
                "content_hash": "upstream-b",
                "created_at": "2026-07-13T13:00:00Z",
                "updated_at": "2026-07-13T14:00:00Z",
                "memory_unit_count": 0,
                "tags": ["agent:codex"],
                "document_metadata": {"source": "codex-hook"},
                "retain_params": {"context": "Codex session"},
            },
        }
        self.target = {}
        self.operations = {}
        self.status_script = {}
        self.next_status_script = []
        self.on_operation_read = None
        self.on_conditional_closeout = None
        self.closeout_results = {}
        self.submissions = []
        self.bump_generation_on_list = False
        self.banks = {
            "codex",
            "engineering",
            "codex-memory-migration-v1-20260708",
        }

    def read_migration_generation(self):
        return f"systalyze:public:{self.generation}"

    def list_replay_document_ids(self, bank):
        assert bank.bank_id == "codex"
        if self.bump_generation_on_list:
            self.generation += 1
        return tuple(reversed(tuple(self.source)))

    def read_replay_document(self, bank, document_id):
        records = self.source if bank.bank_id == "codex" else self.target
        return dict(records[document_id])

    def find_replay_document(self, bank, document_id):
        assert bank.bank_id == "engineering"
        document = self.target.get(document_id)
        return None if document is None else dict(document)

    def submit_replay_document(self, bank, item):
        assert bank.bank_id == "engineering"
        operation_id = f"00000000-0000-4000-8000-{len(self.operations) + 1:012d}"
        self.submissions.append(dict(item))
        self.target[item["document_id"]] = {
            "id": item["document_id"],
            "bank_id": "engineering",
            "original_text": item["content"],
            "content_hash": None,
            "created_at": item["timestamp"],
            "updated_at": item["timestamp"],
            "memory_unit_count": 1,
            "tags": list(item["tags"]),
            "document_metadata": dict(item["metadata"]),
            "retain_params": {
                "context": item.get("context"),
                "event_date": item["timestamp"],
                "observation_scopes": item.get("observation_scopes"),
            },
            "observation_scopes": item.get("observation_scopes"),
        }
        self.operations[operation_id] = "completed"
        if self.next_status_script:
            self.status_script[operation_id] = list(self.next_status_script)
            self.next_status_script = []
        self.generation += 1
        return {"operation_id": operation_id}

    def read_replay_operation(self, bank, operation_id):
        assert bank.bank_id == "engineering"
        if self.on_operation_read is not None:
            callback = self.on_operation_read
            self.on_operation_read = None
            callback()
        scripted = self.status_script.get(operation_id)
        if scripted:
            return {"status": scripted.pop(0)}
        return {"status": self.operations[operation_id]}

    def read_replay_processing_evidence(
        self,
        source_bank,
        target_bank,
        descriptors,
    ):
        assert source_bank.bank_id == "codex"
        assert target_bank.bank_id == "engineering"
        documents = [
            {
                "target_document_id": descriptor["target_document_id"],
                "memory_unit_count": self.target[
                    descriptor["target_document_id"]
                ]["memory_unit_count"],
                "embedded_memory_unit_count": self.target[
                    descriptor["target_document_id"]
                ].get("embedded_memory_unit_count", 1),
            }
            for descriptor in descriptors
        ]
        body = {
            "schema_version": 1,
            "snapshot_generation": self.read_migration_generation(),
            "documents": documents,
            "representative_recall": {
                "target_document_id": descriptors[0][
                    "target_document_id"
                ],
                "query_digest": "a" * 64,
                "result_count": 1,
                "result_projection_digest": "b" * 64,
            },
        }
        return {
            **body,
            "processing_evidence_digest": digest(body),
        }

    def list_replay_bank_ids(self):
        return sorted(self.banks)

    def delete_replay_source_bank(self, bank):
        assert bank.bank_id == "codex"
        self.banks.remove("codex")
        self.generation += 1
        return {"bank_id": "codex", "deleted_count": 9}

    def conditional_replay_closeout(self, authority):
        stored = self.closeout_results.get(
            authority["closeout_plan_digest"]
        )
        if stored is not None:
            if stored["authority"] != authority:
                raise ReplayError("conditional closeout authority conflict")
            return copy.deepcopy(stored["result"])
        if self.on_conditional_closeout is not None:
            callback = self.on_conditional_closeout
            self.on_conditional_closeout = None
            callback()
        if (
            authority["expected_generation"]
            != self.read_migration_generation()
            or authority["expected_bank_ids"] != sorted(self.banks)
        ):
            raise ReplayError("conditional closeout authority drifted")
        expected_documents = {
            item["source_document_id"]: item["record_digest"]
            for item in authority["source_documents"]
        }
        observed_documents = {
            document_id: _document_descriptor(
                document,
                source_bank_id="codex",
            )["record_digest"]
            for document_id, document in self.source.items()
        }
        if observed_documents != expected_documents:
            raise ReplayError("conditional closeout source drifted")
        pre_generation = self.read_migration_generation()
        self.banks.remove("codex")
        self.generation += 1
        result = {
            "schema_version": 1,
            "status": "deleted",
            "deleted_bank_id": "codex",
            "deleted_count": 9,
            "pre_delete_generation": pre_generation,
            "post_delete_generation":
                self.read_migration_generation(),
            "remaining_bank_ids": sorted(self.banks),
            "cleanup_status": "completed",
            "replay_plan_digest": authority["replay_plan_digest"],
            "verification_digest": authority["verification_digest"],
            "backup_evidence_digest":
                authority["backup_evidence_digest"],
            "closeout_plan_digest":
                authority["closeout_plan_digest"],
        }
        self.closeout_results[authority["closeout_plan_digest"]] = {
            "authority": copy.deepcopy(authority),
            "result": copy.deepcopy(result),
        }
        return result


class AccidentalReplayTest(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeReplayAdapter()
        self.source = BankRef("systalyze", "codex")
        self.target = BankRef("systalyze", "engineering")

    @staticmethod
    def backup_evidence():
        return backup_evidence_fixture()

    def test_plan_is_payload_free_stable_and_chronological(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )

        self.assertEqual(
            [entry["source_document_id"] for entry in plan["documents"]],
            ["session-a", "session-b"],
        )
        self.assertEqual(
            [entry["chronological_position"] for entry in plan["documents"]],
            [1, 2],
        )
        self.assertEqual(plan["source_generation"], "systalyze:public:1")
        self.assertNotIn("first complete transcript", repr(plan))
        self.assertRegex(plan["plan_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            plan["documents"][0]["metadata"],
            {"source": "codex-hook"},
        )
        self.assertEqual(
            plan["documents"][0]["tags"],
            ["agent:codex"],
        )
        self.assertEqual(
            plan["documents"][0]["original_timestamp"],
            "2026-07-13T11:00:00Z",
        )
        self.assertTrue(
            all(
                entry["target_document_id"].startswith("misroute-codex-")
                for entry in plan["documents"]
            )
        )

    def test_plan_verification_rejects_digest_order_and_identity_tampering(self):
        original = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        tampered_digest = {**original, "plan_digest": "0" * 64}
        with self.assertRaisesRegex(ReplayError, "digest"):
            verify_replay_plan(tampered_digest)

        for mutate in (
            lambda documents: documents.reverse(),
            lambda documents: documents.__setitem__(
                1,
                {
                    **documents[0],
                    "chronological_position": 2,
                },
            ),
            lambda documents: documents[1].update(
                {
                    "target_document_id":
                        documents[0]["target_document_id"],
                }
            ),
        ):
            value = {
                key: (
                    [dict(document) for document in item]
                    if key == "documents"
                    else item
                )
                for key, item in original.items()
                if key != "plan_digest"
            }
            mutate(value["documents"])
            value["plan_digest"] = digest(value)
            with self.assertRaises(ReplayError):
                verify_replay_plan(value)

    def test_plan_rejects_generation_drift_during_discovery(self):
        self.adapter.bump_generation_on_list = True

        with self.assertRaisesRegex(
            ReplayError,
            "generation changed during replay planning",
        ):
            create_replay_plan(
                self.adapter,
                source_bank=self.source,
                target_bank=self.target,
            )

    def test_replay_item_validates_metadata_and_falls_back_to_created_at(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        descriptor = plan["documents"][0]
        document = dict(self.adapter.source["session-a"])
        document["retain_params"] = {
            **document["retain_params"],
            "event_date": None,
        }

        item = _replay_item(document, descriptor, plan["plan_digest"])
        self.assertEqual(item["timestamp"], document["created_at"])
        self.assertEqual(item["observation_scopes"], "combined")
        explicit_null = {**document, "observation_scopes": None}
        self.assertEqual(
            _replay_item(
                explicit_null,
                descriptor,
                plan["plan_digest"],
            )["observation_scopes"],
            "combined",
        )
        explicit_scopes = {
            **document,
            "observation_scopes": [["scope:one"], ["scope:two", "scope:three"]],
        }
        explicit_descriptor = _document_descriptor(
            explicit_scopes,
            source_bank_id="codex",
        )
        self.assertEqual(
            _replay_item(
                explicit_scopes,
                explicit_descriptor,
                plan["plan_digest"],
            )["observation_scopes"],
            [["scope:one"], ["scope:two", "scope:three"]],
        )

        for metadata in ({"invalid": 3},):
            with self.subTest(metadata=metadata):
                value = {**document, "document_metadata": metadata}
                with self.assertRaises(ReplayError):
                    _document_descriptor(value, source_bank_id="codex")
        conflicting = {
            **descriptor,
            "metadata": {"hindsight_replay_source_bank": "shadow"},
        }
        with self.assertRaises(ReplayError):
            _replay_item(document, conflicting, plan["plan_digest"])
        for observation_scopes in ([[]], [["valid", ""]], ["flat"]):
            with self.subTest(observation_scopes=observation_scopes):
                value = {
                    **document,
                    "observation_scopes": observation_scopes,
                }
                with self.assertRaises(ReplayError):
                    _document_descriptor(value, source_bank_id="codex")

    def test_apply_reprocesses_each_source_and_chains_generation_receipts(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        empty_status = replay_receipt_status(plan, [])
        self.assertEqual(empty_status["completed_document_count"], 0)
        self.assertFalse(empty_status["complete"])
        receipts = apply_replay_plan(
            self.adapter,
            plan,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )

        self.assertEqual(len(receipts), 2)
        self.assertEqual(
            [receipt["pre_generation"] for receipt in receipts],
            ["systalyze:public:1", "systalyze:public:2"],
        )
        self.assertEqual(
            [receipt["post_generation"] for receipt in receipts],
            ["systalyze:public:2", "systalyze:public:3"],
        )
        self.assertEqual(
            receipts[0]["post_generation"],
            receipts[1]["pre_generation"],
        )
        self.assertEqual(len(self.adapter.submissions), 2)

    def test_apply_requires_approved_restore_tested_backup_before_mutation(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        evidence = self.backup_evidence()
        invalid_backup = copy.deepcopy(evidence)
        invalid_backup["artifacts"]["engineering_export"][
            "restore_tested"
        ] = False
        for approval, backup in (
            ("0" * 64, evidence),
            (
                replay_apply_authorization_digest(plan, evidence),
                invalid_backup,
            ),
        ):
            with self.subTest(approval=approval):
                with self.assertRaises(ReplayError):
                    _apply_replay_plan(
                        self.adapter,
                        plan,
                        approval_digest=approval,
                        backup_evidence=backup,
                        poll_interval_seconds=0,
                    )
                self.assertEqual(self.adapter.submissions, [])
                self.assertEqual(self.adapter.target, {})
                self.assertEqual(self.adapter.generation, 1)

    def test_apply_waits_for_terminal_success_and_aborts_on_failure(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        self.adapter.next_status_script = ["pending", "processing"]
        receipts = apply_replay_plan(
            self.adapter,
            plan,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        self.assertEqual(len(receipts), 2)

        failing_adapter = FakeReplayAdapter()
        failing_plan = create_replay_plan(
            failing_adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        failing_adapter.next_status_script = ["pending", "failed"]
        with self.assertRaisesRegex(ReplayError, "operation failed"):
            apply_replay_plan(
                failing_adapter,
                failing_plan,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )
        invalid_status_adapter = FakeReplayAdapter()
        invalid_status_plan = create_replay_plan(
            invalid_status_adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        invalid_status_adapter.next_status_script = ["unknown"]
        with self.assertRaisesRegex(ReplayError, "status is invalid"):
            apply_replay_plan(
                invalid_status_adapter,
                invalid_status_plan,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )
        pending_adapter = FakeReplayAdapter()
        pending_plan = create_replay_plan(
            pending_adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        pending_adapter.next_status_script = ["pending"] * 100_000
        with self.assertRaisesRegex(ReplayError, "timed out"):
            apply_replay_plan(
                pending_adapter,
                pending_plan,
                timeout_seconds=0.001,
                poll_interval_seconds=0,
            )
        for timeout_seconds, poll_interval_seconds in (
            (0, 0),
            (1, -1),
            (True, 0),
        ):
            with self.subTest(
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            ), self.assertRaisesRegex(ReplayError, "wait policy is invalid"):
                apply_replay_plan(
                    FakeReplayAdapter(),
                    plan,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )
        with self.assertRaisesRegex(ReplayError, "receipt prefix is invalid"):
            apply_replay_plan(
                FakeReplayAdapter(),
                plan,
                existing_receipts="not-a-receipt-sequence",
                timeout_seconds=1,
                poll_interval_seconds=0,
            )
        self.assertEqual(len(failing_adapter.submissions), 1)
        self.assertTrue(
            all(item["update_mode"] == "replace" for item in self.adapter.submissions)
        )
        self.assertEqual(
            verify_replay_receipts(self.adapter, plan, receipts)["status"],
            "verified",
        )
        self.assertTrue(
            replay_receipt_status(plan, receipts)["complete"]
        )

    def test_apply_gives_each_document_a_fresh_timeout_budget(self):
        class PendingOnceAdapter(FakeReplayAdapter):
            def submit_replay_document(self, bank, item):
                submission = super().submit_replay_document(bank, item)
                self.status_script[submission["operation_id"]] = ["pending"]
                return submission

        adapter = PendingOnceAdapter()
        plan = create_replay_plan(
            adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        with patch(
            "hindsight_memory_control_plane.accidental_replay.time.monotonic",
            side_effect=(0.0, 0.9, 0.9, 1.8),
        ):
            receipts = apply_replay_plan(
                adapter,
                plan,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )
        self.assertEqual(len(receipts), 2)

    def test_apply_checkpoints_each_successful_receipt_batch(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        checkpoints = []

        receipts = apply_replay_plan(
            self.adapter,
            plan,
            timeout_seconds=1,
            poll_interval_seconds=0,
            receipt_writer=lambda value: checkpoints.append(list(value)),
        )

        self.assertEqual([len(value) for value in checkpoints], [1, 2])
        self.assertEqual(checkpoints[-1], receipts)

    def test_apply_aborts_before_mutation_when_source_text_drifted(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        self.adapter.source["session-b"]["original_text"] = "changed"

        with self.assertRaisesRegex(ReplayError, "source manifest changed"):
            apply_replay_plan(
                self.adapter,
                plan,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )

        self.assertEqual(self.adapter.submissions, [])

    def test_apply_revalidates_the_full_source_after_each_operation(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )

        def mutate_source():
            self.adapter.source["late-session"] = {
                **self.adapter.source["session-b"],
                "id": "late-session",
                "original_text": "late source submission",
            }
            self.adapter.generation += 1

        self.adapter.on_operation_read = mutate_source

        with self.assertRaisesRegex(ReplayError, "source manifest changed"):
            apply_replay_plan(
                self.adapter,
                plan,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )

        self.assertEqual(len(self.adapter.submissions), 1)

    def test_apply_rejects_non_sequence_source_document_id_results(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )

        for invalid_ids in (
            {"session-a", "session-b"},
            "session-a",
            b"session-a",
            [b"session-a", b"session-b"],
        ):
            with self.subTest(invalid_ids=invalid_ids):
                self.adapter.list_replay_document_ids = (
                    lambda _bank, value=invalid_ids: value
                )
                with self.assertRaisesRegex(
                    ReplayError,
                    "source manifest changed",
                ):
                    apply_replay_plan(
                        self.adapter,
                        plan,
                        timeout_seconds=1,
                        poll_interval_seconds=0,
                    )

        self.assertEqual(self.adapter.submissions, [])

    def test_apply_aborts_on_unexpected_target_collision(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        target_id = plan["documents"][0]["target_document_id"]
        self.adapter.target[target_id] = {
            "id": target_id,
            "bank_id": "engineering",
            "original_text": "unrelated existing content",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
            "memory_unit_count": 1,
            "tags": [],
        }

        with self.assertRaisesRegex(ReplayError, "target collision"):
            apply_replay_plan(
                self.adapter,
                plan,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )

        self.assertEqual(self.adapter.submissions, [])

    def test_resume_requires_matching_completed_receipt_and_target(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        receipts = apply_replay_plan(
            self.adapter,
            plan,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        bad_digest = copy.deepcopy(receipts)
        bad_digest[0]["receipt_digest"] = "0" * 64
        with self.assertRaisesRegex(ReplayError, "receipt digest"):
            apply_replay_plan(
                self.adapter,
                plan,
                existing_receipts=bad_digest,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )

        broken_chain = copy.deepcopy(receipts)
        broken_chain[1]["pre_generation"] = plan["source_generation"]
        broken_body = {
            key: value
            for key, value in broken_chain[1].items()
            if key != "receipt_digest"
        }
        broken_chain[1]["receipt_digest"] = digest(broken_body)
        with self.assertRaisesRegex(ReplayError, "generation chain"):
            apply_replay_plan(
                self.adapter,
                plan,
                existing_receipts=broken_chain,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )

        first_target_id = plan["documents"][0]["target_document_id"]
        self.adapter.target[first_target_id]["original_text"] = "tampered"

        with self.assertRaisesRegex(ReplayError, "receipt target"):
            apply_replay_plan(
                self.adapter,
                plan,
                existing_receipts=receipts,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )

    def test_resume_rejects_target_metadata_tag_and_timestamp_drift(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        receipts = apply_replay_plan(
            self.adapter,
            plan,
            poll_interval_seconds=0,
        )
        target_id = plan["documents"][0]["target_document_id"]
        original = copy.deepcopy(self.adapter.target[target_id])
        mutations = (
            lambda target: target["document_metadata"].update(
                {"source": "changed"}
            ),
            lambda target: target["tags"].append("unexpected"),
            lambda target: target["retain_params"].update(
                {"event_date": "2026-07-14T00:00:00Z"}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.adapter.target[target_id] = copy.deepcopy(original)
                mutate(self.adapter.target[target_id])
                with self.assertRaisesRegex(
                    ReplayError,
                    "receipt target does not match",
                ):
                    apply_replay_plan(
                        self.adapter,
                        plan,
                        existing_receipts=receipts,
                        poll_interval_seconds=0,
                    )

    def test_verification_revalidates_source_during_operation_checks(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        receipts = apply_replay_plan(
            self.adapter,
            plan,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )

        def mutate_source():
            self.adapter.source["session-a"]["original_text"] = "changed"
            self.adapter.generation += 1

        self.adapter.on_operation_read = mutate_source

        with self.assertRaisesRegex(
            ReplayError,
            "processing evidence|generation changed during verification",
        ):
            verify_replay_receipts(self.adapter, plan, receipts)

    def test_closeout_rejects_changed_bank_set(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        receipts = apply_replay_plan(
            self.adapter,
            plan,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        verification = verify_replay_receipts(
            self.adapter,
            plan,
            receipts,
        )
        evidence = self.backup_evidence()
        closeout_plan = create_replay_closeout_plan(
            self.adapter,
            plan,
            verification,
            evidence,
        )
        self.adapter.banks.add("unexpected-bank")

        with self.assertRaisesRegex(ReplayError, "authority drifted"):
            apply_replay_closeout(
                self.adapter,
                plan,
                receipts,
                verification,
                evidence,
                closeout_plan,
                approval_digest=closeout_plan[
                    "closeout_plan_digest"
                ],
            )

    def test_verification_requires_embeddings_and_representative_recall(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        receipts = apply_replay_plan(
            self.adapter,
            plan,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        first_target = plan["documents"][0]["target_document_id"]
        self.adapter.target[first_target][
            "embedded_memory_unit_count"
        ] = 0

        with self.assertRaisesRegex(
            ReplayError,
            "processing evidence",
        ):
            verify_replay_receipts(self.adapter, plan, receipts)

        self.adapter.target[first_target][
            "embedded_memory_unit_count"
        ] = 1
        original_evidence = (
            self.adapter.read_replay_processing_evidence
        )

        def invalid_recall(*args):
            value = original_evidence(*args)
            body = {
                key: item
                for key, item in value.items()
                if key != "processing_evidence_digest"
            }
            body["representative_recall"] = {
                **body["representative_recall"],
                "target_document_id": "unrelated-target",
            }
            return {
                **body,
                "processing_evidence_digest": digest(body),
            }

        self.adapter.read_replay_processing_evidence = invalid_recall
        with self.assertRaisesRegex(
            ReplayError,
            "processing evidence",
        ):
            verify_replay_receipts(self.adapter, plan, receipts)

    def test_closeout_is_separately_digest_bound_and_deletes_only_codex(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        receipts = apply_replay_plan(
            self.adapter,
            plan,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        verification = verify_replay_receipts(
            self.adapter,
            plan,
            receipts,
        )
        evidence = self.backup_evidence()
        closeout_plan = create_replay_closeout_plan(
            self.adapter,
            plan,
            verification,
            evidence,
        )
        self.assertNotIn("artifact_digest", closeout_plan)

        receipt = apply_replay_closeout(
            self.adapter,
            plan,
            receipts,
            verification,
            evidence,
            closeout_plan,
            approval_digest=closeout_plan["closeout_plan_digest"],
        )

        self.assertEqual(receipt["deleted_bank_id"], "codex")
        self.assertNotIn("codex", self.adapter.banks)
        self.assertIn(
            "codex-memory-migration-v1-20260708",
            self.adapter.banks,
        )
        self.assertEqual(
            receipt["remaining_bank_set_digest"],
            digest(sorted(self.adapter.banks)),
        )
        self.adapter.generation += 1
        self.adapter.banks.add("newly-created-after-closeout")
        retry_receipt = apply_replay_closeout(
            self.adapter,
            plan,
            receipts,
            verification,
            evidence,
            closeout_plan,
            approval_digest=closeout_plan["closeout_plan_digest"],
        )
        self.assertEqual(retry_receipt, receipt)

    def test_closeout_atomically_rejects_a_late_source_submission(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        receipts = apply_replay_plan(
            self.adapter,
            plan,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        verification = verify_replay_receipts(
            self.adapter,
            plan,
            receipts,
        )
        evidence = self.backup_evidence()
        closeout_plan = create_replay_closeout_plan(
            self.adapter,
            plan,
            verification,
            evidence,
        )

        def late_submission():
            self.adapter.source["late-session"] = {
                **self.adapter.source["session-b"],
                "id": "late-session",
                "original_text": "late source submission",
            }
            self.adapter.generation += 1

        self.adapter.on_conditional_closeout = late_submission

        with self.assertRaisesRegex(
            ReplayError,
            "conditional closeout authority drifted",
        ):
            apply_replay_closeout(
                self.adapter,
                plan,
                receipts,
                verification,
                evidence,
                closeout_plan,
                approval_digest=closeout_plan[
                    "closeout_plan_digest"
                ],
            )

        self.assertIn("codex", self.adapter.banks)

    def test_closeout_rejects_unapproved_or_incomplete_backup_evidence(self):
        plan = create_replay_plan(
            self.adapter,
            source_bank=self.source,
            target_bank=self.target,
        )
        receipts = apply_replay_plan(
            self.adapter,
            plan,
            timeout_seconds=1,
            poll_interval_seconds=0,
        )
        verification = verify_replay_receipts(
            self.adapter,
            plan,
            receipts,
        )
        incomplete = {
            "schema_version": 1,
            "artifacts": {},
        }
        with self.assertRaisesRegex(ReplayError, "backup evidence"):
            create_replay_closeout_plan(
                self.adapter,
                plan,
                verification,
                incomplete,
            )

        evidence = self.backup_evidence()
        closeout_plan = create_replay_closeout_plan(
            self.adapter,
            plan,
            verification,
            evidence,
        )
        with self.assertRaisesRegex(ReplayError, "approval digest"):
            apply_replay_closeout(
                self.adapter,
                plan,
                receipts,
                verification,
                evidence,
                closeout_plan,
                approval_digest="0" * 64,
            )
        self.assertIn("codex", self.adapter.banks)


if __name__ == "__main__":
    unittest.main()
