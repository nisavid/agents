from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[4]
SCRIPT = REPOSITORY / "plugins/mergecraft/skills/graphite/scripts/submit_draft_stack.py"
spec = importlib.util.spec_from_file_location("submit_draft_stack", SCRIPT)
assert spec and spec.loader
GRAPHITE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(GRAPHITE)


class GraphiteTransportTests(unittest.TestCase):
    repository = "acme/app"
    base_oid = "a" * 40
    head_oid = "b" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.template = Path(self.temporary.name) / "body.md"
        self.template.write_text(
            "PR __PUBLISHING_REVIEWABLE_PRS_PR_NUMBER__\n", encoding="utf-8"
        )
        self.review_input = Path(self.temporary.name) / "review-input.json"
        self.review_input.write_text("{}\n", encoding="utf-8")
        self.entry = {
            "base": "main",
            "base_oid": self.base_oid,
            "head": "acme:feature",
            "head_oid": self.head_oid,
            "head_owner": "acme",
            "head_repository": self.repository,
            "title": "feat: feature",
            "body_source": {"mode": "template", "path": str(self.template)},
            "review_input": str(self.review_input),
            "review_mode": "not-required",
            "review_bundle": None,
            "selected_specialists": [],
        }
        self.candidate = {
            **self.entry,
            "local_branch": "feature",
            "body_source_sha256": GRAPHITE._sha_text(self.template.read_text()),
            "review_input_raw_sha256": GRAPHITE._sha_bytes(
                self.review_input.read_bytes()
            ),
            "review_input_sha256": "c" * 64,
            "review_input_pr": GRAPHITE.PR_NUMBER_TOKEN,
        }
        candidate_builder = mock.patch.object(
            GRAPHITE,
            "build_publication_candidate",
            return_value=mock.Mock(content_sha256="f" * 64),
        )
        candidate_builder.start()
        self.addCleanup(candidate_builder.stop)

    def stored(
        self,
        *,
        number: int = 42,
        title: str = "feat: feature",
        body: str = "Graphite transport\n",
        draft: bool = True,
    ) -> dict[str, object]:
        return {
            "number": number,
            "url": f"https://github.com/acme/app/pull/{number}",
            "title": title,
            "body": body,
            "baseRefName": "main",
            "baseRefOid": self.base_oid,
            "headRefName": "feature",
            "headRefOid": self.head_oid,
            "headRepositoryOwner": {"login": "acme"},
            "headRepository": {"nameWithOwner": self.repository},
            "isDraft": draft,
            "state": "OPEN",
        }

    def request(self) -> dict[str, object]:
        return {
            "schema_version": GRAPHITE.SCHEMA_VERSION,
            "repository": self.repository,
            "repository_root": str(self.root),
            "current_branch": "feature",
            "stack": [self.entry],
        }

    def audit_for(
        self,
        item: dict[str, object],
        *,
        receipt_id: str = "receipt-one",
        sequence: int = 1,
    ) -> dict[str, object]:
        return {
            "status": "verified",
            "receipt_id": receipt_id,
            "provenance": "canonical",
            "sequence": sequence,
            "identity_epoch": item["target_identity_epoch"],
            "final": {
                "is_draft": item["target_is_draft"],
                "title_sha256": item["target_title_sha256"],
                "body_sha256": item["target_body_sha256"],
            },
            "review_input_sha256": item["target_review_input_sha256"],
            "review": {
                "mode": item["target_review_mode"],
                "publication_candidate_sha256": item[
                    "target_publication_candidate_sha256"
                ],
                "observation": (
                    {"contract": "mergecraft-required-review-observation-v1"}
                    if item["target_review_mode"] == "required"
                    else None
                ),
            },
        }

    def required_item(self, *, body: str = "Graphite transport\n") -> dict[str, object]:
        bundle = Path(self.temporary.name) / "review-bundle"
        entry = {
            **self.candidate,
            "review_mode": "required",
            "review_bundle": str(bundle),
            "selected_specialists": ["security"],
        }
        return GRAPHITE._handoff_entry(
            entry,
            None,
            self.stored(body=body),
            self.repository,
        )

    def plan(self) -> dict[str, object]:
        unsigned = {
            "schema_version": GRAPHITE.SCHEMA_VERSION,
            "request": self.request(),
            "candidates": [self.candidate],
            "snapshot": {
                "repository_root": str(self.root),
                "current_branch": "feature",
                "clean_status_sha256": GRAPHITE._sha_text(""),
                "gt_log_short_sha256": "d" * 64,
                "gt_trunk_sha256": "e" * 64,
            },
            "preimages": [None],
        }
        return {
            **unsigned,
            "content_sha256": GRAPHITE._sha_bytes(GRAPHITE._canonical(unsigned)),
        }

    def test_build_plan_binds_exact_local_refs_and_separates_candidates(self) -> None:
        def completed(value: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, value, "")

        with (
            mock.patch.object(GRAPHITE, "_candidate", return_value=self.candidate),
            mock.patch.object(
                GRAPHITE,
                "_run",
                side_effect=[
                    completed(self.head_oid + "\n"),
                    completed(self.base_oid + "\n"),
                ],
            ),
            mock.patch.object(
                GRAPHITE,
                "_git_graphite_snapshot",
                return_value={"snapshot": "bound"},
            ),
            mock.patch.object(GRAPHITE, "_live_preimage", return_value=None),
        ):
            plan = GRAPHITE.build_plan(self.request())

        self.assertEqual(plan["request"], self.request())
        self.assertEqual(plan["candidates"], [self.candidate])
        self.assertNotEqual(plan["request"]["stack"], plan["candidates"])

    def test_stack_entry_requires_an_explicit_specialist_selection(self) -> None:
        entry = dict(self.entry)
        del entry["selected_specialists"]
        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError, "unsupported or missing fields"
        ):
            GRAPHITE._candidate(entry, self.repository)

    def test_schema_v1_requests_and_handoffs_are_not_silently_upgraded(self) -> None:
        request = {**self.request(), "schema_version": 1}
        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError, "schema_version must be 2"
        ):
            GRAPHITE.build_plan(request)

        unsigned = {
            "schema_version": 1,
            "status": "transport-complete-repair-required",
            "plan_sha256": "a" * 64,
            "repository_root": str(self.root),
            "transport_command_error": None,
            "pull_requests": [],
            "failures": [],
        }
        handoff = {
            **unsigned,
            "content_sha256": GRAPHITE._sha_bytes(GRAPHITE._canonical(unsigned)),
        }
        path = Path(self.temporary.name) / "legacy-handoff.json"
        path.write_text(json.dumps(handoff), encoding="utf-8")
        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError, "schema_version must be 2"
        ):
            GRAPHITE._load_handoff(path)

    def test_execute_runs_one_exact_transport_and_emits_publisher_handoff(self) -> None:
        plan = self.plan()
        output = Path(self.temporary.name) / "handoff.json"
        receipt_root = Path(self.temporary.name) / "receipts"
        completed = subprocess.CompletedProcess([], 0, "submitted\n", "")
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(
                GRAPHITE, "prepare_receipt_store", return_value=receipt_root
            ),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ) as transaction_lock,
            mock.patch.object(GRAPHITE, "_run", return_value=completed) as run,
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            result = GRAPHITE.execute(plan, output)

        self.assertEqual(result["status"], "transport-complete-repair-required")
        run.assert_called_once_with(
            [
                "gt",
                "submit",
                "--stack",
                "--draft",
                "--no-edit",
                "--no-ai",
                "--no-interactive",
            ],
            cwd=self.root,
            timeout=GRAPHITE.MUTATION_TIMEOUT_SECONDS,
        )
        transaction_lock.assert_called_once_with(
            receipt_root,
            repository=self.repository,
            base="main",
            head="acme:feature",
            head_owner="acme",
            head_repository=self.repository,
        )
        handoff = result["pull_requests"][0]
        self.assertEqual(handoff["pr"], 42)
        self.assertEqual(len(handoff["publisher_commands"]), 1)
        command = handoff["publisher_commands"][0]
        self.assertIn("--body-template", command)
        self.assertIn("--expected-title-sha256", command)
        self.assertEqual(command[command.index("--review-mode") + 1], "not-required")
        self.assertEqual(command[command.index("--selected-specialists") + 1], "[]")
        self.assertNotIn("--review-bundle", command)
        self.assertNotIn("gh", command)
        stored = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(stored["content_sha256"], result["content_sha256"])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_required_handoff_preserves_review_profile_and_candidate_digest(
        self,
    ) -> None:
        with mock.patch.object(
            GRAPHITE,
            "build_publication_candidate",
            return_value=mock.Mock(content_sha256="9" * 64),
        ) as build_candidate:
            item = self.required_item()

        command = item["publisher_commands"][0]
        self.assertEqual(command[command.index("--review-mode") + 1], "required")
        self.assertEqual(
            command[command.index("--review-bundle") + 1],
            item["target_review_bundle"],
        )
        self.assertEqual(
            command[command.index("--selected-specialists") + 1],
            '["security"]',
        )
        self.assertEqual(item["target_publication_candidate_sha256"], "9" * 64)
        build_candidate.assert_called_once()
        arguments = build_candidate.call_args.kwargs
        self.assertEqual(arguments["operation"], "update-text")
        self.assertEqual(arguments["review_mode"], "required")
        self.assertEqual(arguments["selected_specialists"], ["security"])

        bypass = list(command)
        bundle_index = bypass.index("--review-bundle")
        del bypass[bundle_index : bundle_index + 2]
        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError, "review bundle drifted"
        ):
            GRAPHITE._checked_publisher_command(bypass, item)

    def test_required_audit_accepts_only_exact_canonical_v3_review(self) -> None:
        item = self.required_item()
        exact = self.audit_for(item)
        self.assertTrue(GRAPHITE._audit_matches_target(exact, item))

        cases = {
            "legacy-v2": {
                **exact,
                "review": {"state": "legacy-unrecorded"},
            },
            "reconciliation": {
                **exact,
                "provenance": "reconciled-unreceipted",
                "review": {"state": "unwitnessed-reconciliation"},
            },
            "v3-not-required": {
                **exact,
                "review": {
                    **exact["review"],
                    "mode": "not-required",
                    "observation": None,
                },
            },
            "different-candidate": {
                **exact,
                "review": {
                    **exact["review"],
                    "publication_candidate_sha256": "0" * 64,
                },
            },
            "missing-observation": {
                **exact,
                "review": {**exact["review"], "observation": None},
            },
        }
        for label, audit in cases.items():
            with self.subTest(label=label):
                self.assertFalse(GRAPHITE._audit_matches_target(audit, item))

    def test_matching_live_target_is_not_upgraded_without_a_transition(
        self,
    ) -> None:
        item = self.required_item(body="PR 42\n")
        self.assertEqual(item["publisher_commands"], [])
        self.assertNotIn("no_transition_reconcile_command", item)
        handoff = {
            "repository_root": str(self.root),
            "content_sha256": "d" * 64,
            "pull_requests": [item],
        }
        legacy = {
            **self.audit_for(item),
            "review": {"state": "legacy-unrecorded"},
        }
        with mock.patch.object(
            GRAPHITE, "_run_json_command", return_value=legacy
        ) as run:
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError, "exact handoff target"
            ):
                GRAPHITE.repair(
                    handoff, Path(self.temporary.name) / "repair-no-upgrade.json"
                )
        run.assert_called_once_with(
            item["final_audit_command"], cwd=self.root, operation="audit"
        )

    def test_matching_live_target_accepts_existing_exact_required_receipt(
        self,
    ) -> None:
        item = self.required_item(body="PR 42\n")
        handoff = {
            "repository_root": str(self.root),
            "content_sha256": "d" * 64,
            "pull_requests": [item],
        }
        exact = self.audit_for(item)
        with mock.patch.object(
            GRAPHITE, "_run_json_command", return_value=exact
        ) as run:
            result = GRAPHITE.repair(
                handoff, Path(self.temporary.name) / "repair-exact.json"
            )
        self.assertEqual(result["status"], "canonical-repair-complete")
        self.assertEqual(result["pull_requests"][0]["publisher_result_sha256"], [])
        run.assert_called_once()

    def test_checkpoint_cannot_rebind_the_review_candidate(self) -> None:
        item = self.required_item(body="PR 42\n")
        handoff = {
            "repository_root": str(self.root),
            "content_sha256": "d" * 64,
            "pull_requests": [item],
        }
        exact = self.audit_for(item)
        output = Path(self.temporary.name) / "repair-checkpoint.json"
        with mock.patch.object(GRAPHITE, "_run_json_command", return_value=exact):
            GRAPHITE.repair(handoff, output)

        checkpoint_path = output.with_name(f".{output.name}.checkpoint.json")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint["completed"][0]["target_publication_candidate_sha256"] = "0" * 64
        GRAPHITE._write_private_json(checkpoint_path, checkpoint)
        with (
            mock.patch.object(GRAPHITE, "_run_json_command", return_value=exact),
            self.assertRaisesRegex(GRAPHITE.GraphiteTransportError, "checkpointed PR"),
        ):
            GRAPHITE.repair(handoff, output)

    def test_existing_ready_pr_is_never_drafted_by_handoff(self) -> None:
        body = Path(self.temporary.name) / "existing.md"
        body.write_text("Canonical body\n", encoding="utf-8")
        candidate = {
            **self.candidate,
            "body_source": {"mode": "file", "path": str(body)},
            "review_input_pr": 42,
        }
        preimage = {
            "number": 42,
            "url": "https://github.com/acme/app/pull/42",
            "title_sha256": "1" * 64,
            "body_sha256": "2" * 64,
            "is_draft": False,
        }
        handoff = GRAPHITE._handoff_entry(
            candidate,
            preimage,
            self.stored(body="Old body\n", draft=False),
            self.repository,
        )
        commands = handoff["publisher_commands"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(
            commands[0][commands[0].index("--expected-state") + 1], "ready"
        )
        self.assertNotIn("ready", commands[0][:3])

    def test_already_exact_ready_target_binds_mark_ready_candidate(self) -> None:
        body = Path(self.temporary.name) / "existing-ready.md"
        body.write_text("Canonical body\n", encoding="utf-8")
        candidate = {
            **self.candidate,
            "body_source": {"mode": "file", "path": str(body)},
            "review_input_pr": 42,
        }
        preimage = {
            "number": 42,
            "url": "https://github.com/acme/app/pull/42",
            "title_sha256": "1" * 64,
            "body_sha256": "2" * 64,
            "is_draft": False,
        }
        with mock.patch.object(
            GRAPHITE,
            "build_publication_candidate",
            return_value=mock.Mock(content_sha256="8" * 64),
        ) as build_candidate:
            handoff = GRAPHITE._handoff_entry(
                candidate,
                preimage,
                self.stored(body="Canonical body\n", draft=False),
                self.repository,
            )

        self.assertEqual(handoff["publisher_commands"], [])
        self.assertEqual(build_candidate.call_args.kwargs["operation"], "mark-ready")
        self.assertEqual(
            build_candidate.call_args.kwargs["body_source_kind"], "stored-body"
        )

    def test_existing_ready_pr_drafted_by_transport_is_restored_and_audited(
        self,
    ) -> None:
        body = Path(self.temporary.name) / "existing.md"
        body.write_text("Canonical body\n", encoding="utf-8")
        candidate = {
            **self.candidate,
            "body_source": {"mode": "file", "path": str(body)},
            "review_input_pr": 42,
        }
        preimage = {
            "number": 42,
            "url": "https://github.com/acme/app/pull/42",
            "title_sha256": "1" * 64,
            "body_sha256": "2" * 64,
            "is_draft": False,
        }
        item = GRAPHITE._handoff_entry(
            candidate,
            preimage,
            self.stored(body="Old body\n", draft=True),
            self.repository,
        )
        self.assertTrue(item["is_draft"])
        self.assertFalse(item["target_is_draft"])
        self.assertEqual(
            [command[2] for command in item["publisher_commands"]],
            ["text", "ready"],
        )
        handoff = {
            "schema_version": GRAPHITE.SCHEMA_VERSION,
            "repository_root": str(self.root),
            "pull_requests": [item],
            "content_sha256": "d" * 64,
        }
        output = Path(self.temporary.name) / "repair.json"
        final_audit = self.audit_for(item)
        calls = iter(
            [
                {"status": "unavailable"},
                {"status": "updated"},
                {"status": "ready"},
                final_audit,
            ]
        )
        with mock.patch.object(
            GRAPHITE,
            "_run_json_command",
            side_effect=lambda *_args, **_kwargs: next(calls),
        ):
            result = GRAPHITE.repair(handoff, output)

        self.assertEqual(result["status"], "canonical-repair-complete")
        self.assertFalse(result["pull_requests"][0]["target_is_draft"])
        self.assertEqual(
            result["pull_requests"][0]["audit"]["final"]["is_draft"], False
        )

    def test_ready_restoration_targets_the_final_mark_ready_candidate(self) -> None:
        body = Path(self.temporary.name) / "existing-crlf.md"
        body.write_bytes(b"Canonical body\r\n")
        candidate = {
            **self.candidate,
            "body_source": {"mode": "file", "path": str(body)},
            "review_input_pr": 42,
        }
        preimage = {
            "number": 42,
            "url": "https://github.com/acme/app/pull/42",
            "title_sha256": "1" * 64,
            "body_sha256": "2" * 64,
            "is_draft": False,
        }
        with mock.patch.object(
            GRAPHITE,
            "build_publication_candidate",
            return_value=mock.Mock(content_sha256="8" * 64),
        ) as build_candidate:
            item = GRAPHITE._handoff_entry(
                candidate,
                preimage,
                self.stored(body="Old body\n", draft=True),
                self.repository,
            )

        self.assertEqual(
            [command[2] for command in item["publisher_commands"]],
            ["text", "ready"],
        )
        self.assertEqual(item["target_publication_candidate_sha256"], "8" * 64)
        arguments = build_candidate.call_args.kwargs
        self.assertEqual(arguments["operation"], "mark-ready")
        self.assertEqual(arguments["body_source_kind"], "stored-body")
        self.assertEqual(arguments["body_source_raw"], b"Canonical body\r\n")
        self.assertEqual(arguments["published_body"], "Canonical body\r\n")

    def test_plan_drift_stops_before_transport(self) -> None:
        plan = self.plan()
        drifted = {**plan, "content_sha256": "0" * 64}
        output = Path(self.temporary.name) / "handoff.json"
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=drifted),
            mock.patch.object(GRAPHITE, "_run") as run,
        ):
            with self.assertRaisesRegex(GRAPHITE.GraphiteTransportError, "drifted"):
                GRAPHITE.execute(plan, output)
        run.assert_not_called()

    def test_partial_transport_writes_ambiguous_handoff_without_retry(self) -> None:
        plan = self.plan()
        output = Path(self.temporary.name) / "handoff.json"
        completed = subprocess.CompletedProcess([], 0, "submitted\n", "")
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(GRAPHITE, "_run", return_value=completed) as run,
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[]),
        ):
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError, "inspect the private handoff"
            ):
                GRAPHITE.execute(plan, output)
        self.assertEqual(run.call_count, 1)
        handoff = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(handoff["status"], "transport-ambiguous-inspection-required")
        self.assertEqual(len(handoff["failures"]), 1)

    def test_repair_executes_only_owned_publisher_commands_and_audits(self) -> None:
        plan = self.plan()
        transport_output = Path(self.temporary.name) / "handoff.json"
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                GRAPHITE,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, "submitted\n", ""),
            ),
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            handoff = GRAPHITE.execute(plan, transport_output)
        repair_output = Path(self.temporary.name) / "repair.json"
        publisher = json.dumps(
            {
                "status": "verified",
                "receipt_id": "receipt-one",
                "provenance": "canonical",
                "sequence": 1,
            }
        )
        audit = json.dumps(self.audit_for(handoff["pull_requests"][0]))
        with mock.patch.object(
            GRAPHITE,
            "_run",
            side_effect=[
                subprocess.CompletedProcess([], 0, json.dumps({"status": "drift"}), ""),
                subprocess.CompletedProcess([], 0, publisher, ""),
                subprocess.CompletedProcess([], 0, audit, ""),
            ],
        ) as run:
            result = GRAPHITE.repair(handoff, repair_output)
        self.assertEqual(result["status"], "canonical-repair-complete")
        self.assertEqual(run.call_count, 3)
        self.assertEqual(result["pull_requests"][0]["audit"]["provenance"], "canonical")

    def test_repair_rejects_latest_receipt_drift_after_older_match(self) -> None:
        plan = self.plan()
        transport_output = Path(self.temporary.name) / "handoff.json"
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                GRAPHITE,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, "submitted\n", ""),
            ),
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            handoff = GRAPHITE.execute(plan, transport_output)

        publisher = json.dumps(
            {
                "status": "verified",
                "receipt_id": "older-matching-receipt",
                "provenance": "canonical",
                "sequence": 3,
            }
        )
        latest_audit = json.dumps(
            {
                "status": "drift",
                "receipt_id": "authoritative-latest-receipt",
                "provenance": "canonical",
                "sequence": 4,
                "reason": (
                    "live PR state does not match the authoritative latest receipt"
                ),
            }
        )
        with mock.patch.object(
            GRAPHITE,
            "_run",
            side_effect=[
                subprocess.CompletedProcess([], 0, latest_audit, ""),
                subprocess.CompletedProcess([], 0, publisher, ""),
                subprocess.CompletedProcess([], 0, latest_audit, ""),
            ],
        ) as run:
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError,
                "audit did not verify the exact handoff target",
            ):
                GRAPHITE.repair(
                    handoff,
                    Path(self.temporary.name) / "repair.json",
                )

        self.assertEqual(run.call_count, 3)

    def test_two_pr_repair_resumes_after_first_checkpoint_without_duplicate_mutation(
        self,
    ) -> None:
        first = self.plan()
        transport_output = Path(self.temporary.name) / "handoff.json"
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=first),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                GRAPHITE,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, "submitted\n", ""),
            ),
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            handoff = GRAPHITE.execute(first, transport_output)
        second = json.loads(json.dumps(handoff["pull_requests"][0]))
        second["pr"] = 43
        second["url"] = "https://github.com/acme/app/pull/43"
        handoff["pull_requests"].append(second)
        unsigned = {
            key: value for key, value in handoff.items() if key != "content_sha256"
        }
        handoff["content_sha256"] = GRAPHITE._sha_bytes(GRAPHITE._canonical(unsigned))
        output = Path(self.temporary.name) / "repair.json"
        drift = {"status": "drift"}
        verified_one = self.audit_for(handoff["pull_requests"][0])
        verified_two = self.audit_for(
            handoff["pull_requests"][1], receipt_id="receipt-two"
        )
        publisher = {"status": "verified", "provenance": "canonical"}
        first_run = [
            drift,
            publisher,
            verified_one,
            drift,
            GRAPHITE.GraphiteTransportError("fail after first PR"),
        ]
        with mock.patch.object(
            GRAPHITE, "_run_json_command", side_effect=first_run
        ) as run:
            with self.assertRaisesRegex(GRAPHITE.GraphiteTransportError, "fail after"):
                GRAPHITE.repair(handoff, output)
        self.assertEqual(run.call_count, 5)
        checkpoint = output.with_name(f".{output.name}.checkpoint.json")
        self.assertTrue(checkpoint.is_file())

        second_run = [verified_one, drift, publisher, verified_two]
        with mock.patch.object(
            GRAPHITE, "_run_json_command", side_effect=second_run
        ) as run:
            result = GRAPHITE.repair(handoff, output)
        self.assertEqual(result["status"], "canonical-repair-complete")
        self.assertEqual(run.call_count, 4)
        self.assertEqual([item["pr"] for item in result["pull_requests"]], [42, 43])

    def test_restart_checkpoints_exact_target_audit_without_replaying_publisher(
        self,
    ) -> None:
        plan = self.plan()
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                GRAPHITE,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, "submitted\n", ""),
            ),
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            handoff = GRAPHITE.execute(
                plan, Path(self.temporary.name) / "handoff-interrupted.json"
            )
        output = Path(self.temporary.name) / "repair-interrupted.json"
        verified = self.audit_for(handoff["pull_requests"][0])
        publisher = {"status": "verified", "provenance": "canonical"}
        with (
            mock.patch.object(
                GRAPHITE,
                "_run_json_command",
                side_effect=[{"status": "drift"}, publisher, verified],
            ),
            mock.patch.object(
                GRAPHITE,
                "_write_private_json",
                side_effect=GRAPHITE.GraphiteTransportError(
                    "interrupted before checkpoint persistence"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError,
                "interrupted before checkpoint persistence",
            ):
                GRAPHITE.repair(handoff, output)

        with mock.patch.object(
            GRAPHITE, "_run_json_command", return_value=verified
        ) as restarted_run:
            result = GRAPHITE.repair(handoff, output)

        self.assertEqual(result["status"], "canonical-repair-complete")
        restarted_run.assert_called_once_with(
            handoff["pull_requests"][0]["final_audit_command"],
            cwd=Path(handoff["repository_root"]),
            operation="audit",
        )
        checkpoint_path = output.with_name(f".{output.name}.checkpoint.json")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["completed"][0]["audit"], verified)
        self.assertEqual(checkpoint["completed"][0]["publisher_result_sha256"], [])
        self.assertEqual(
            checkpoint["completed"][0]["target_review_mode"],
            handoff["pull_requests"][0]["target_review_mode"],
        )
        self.assertEqual(
            checkpoint["completed"][0]["target_publication_candidate_sha256"],
            handoff["pull_requests"][0]["target_publication_candidate_sha256"],
        )

    def test_repair_rejects_newer_verified_receipt_for_a_different_body_target(
        self,
    ) -> None:
        plan = self.plan()
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                GRAPHITE,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, "submitted\n", ""),
            ),
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            handoff = GRAPHITE.execute(
                plan, Path(self.temporary.name) / "handoff-race.json"
            )
        item = handoff["pull_requests"][0]
        newer = self.audit_for(item, receipt_id="newer", sequence=2)
        newer["final"] = {
            **newer["final"],
            "body_sha256": "f" * 64,
        }
        with mock.patch.object(GRAPHITE, "_run_json_command", return_value=newer):
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError, "exact handoff target"
            ):
                GRAPHITE.repair(handoff, Path(self.temporary.name) / "repair-race.json")

    def test_repair_rejects_unowned_command_without_execution(self) -> None:
        handoff = {
            "repository_root": str(self.root),
            "content_sha256": "f" * 64,
            "pull_requests": [
                {
                    "repository": self.repository,
                    "pr": 42,
                    "url": "https://github.com/acme/app/pull/42",
                    "target_review_mode": "not-required",
                    "target_publication_candidate_sha256": "f" * 64,
                    "target_review_bundle": None,
                    "target_selected_specialists": [],
                    "publisher_commands": [["sh", "-c", "unexpected"]],
                    "final_audit_command": [
                        sys.executable,
                        str(GRAPHITE.AUDIT),
                        "audit",
                    ],
                }
            ],
        }
        with mock.patch.object(GRAPHITE, "_run") as run:
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError, "executable drifted"
            ):
                GRAPHITE.repair(handoff, Path(self.temporary.name) / "repair.json")
        run.assert_not_called()

    def test_publisher_output_rejects_duplicate_json_keys(self) -> None:
        command = [sys.executable, str(GRAPHITE.AUDIT), "audit"]
        duplicate = '{"status":"verified","status":"drift"}'
        with mock.patch.object(
            GRAPHITE,
            "_run",
            return_value=subprocess.CompletedProcess([], 0, duplicate, ""),
        ):
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError, "duplicate JSON key"
            ):
                GRAPHITE._run_json_command(
                    command,
                    cwd=self.root,
                    operation="audit",
                )


if __name__ == "__main__":
    unittest.main()
