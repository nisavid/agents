from dataclasses import FrozenInstanceError, replace
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.importing import (
    ImportValidationError,
    SECRET_DETECTORS,
    SECRET_SCAN_POLICY_VERSION,
    apply_import_plan,
    build_import_plan,
    inspect_items,
    import_item_digest,
    inspect_source,
    parse_claude_memory,
    parse_codex_memory,
    parse_portable_jsonl,
    parse_portable_markdown,
    project_import,
    record_novelty_review,
    reconcile_import,
    validate_projection,
)
from hindsight_memory_control_plane.import_runner import (
    _inspection_checkpoint,
    _reserve_rate_limit_slot,
    run_import_inspection,
)
from hindsight_memory_control_plane.canonical import canonical_bytes, digest
from hindsight_memory_control_plane.model import BankRef, Action, FrozenDict, deep_freeze, deep_thaw


TARGET_BANK = BankRef("core", "engineering")
THREAD_TIMEOUT_SECONDS = 5.0


def record(native_id="m1", content="Prefer exact lease pushes.", **overrides):
    value = {
        "source_locator": "/private/tmp/hindsight-import/MEMORY.md",
        "source_native_id": native_id,
        "timestamp": "2026-07-01T12:34:56Z",
        "line_start": 10,
        "line_end": 12,
        "content": content,
        "kind": "rule",
        "intended_scope": "repo:dotfiles",
        "relationships": ["repo:dotfiles", "workflow:git-publication"],
        "coverage_disposition": "proposed_novel",
        "coverage_reason": "absent-from-target",
    }
    value.update(overrides)
    return value


def reviewed_items(source_kind, records):
    return tuple(
        record_novelty_review(item, review_evidence_digest="e" * 64)
        if item.coverage_disposition == "review_pending"
        else item
        for item in inspect_items(source_kind, records)
    )


class ImportProjectionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temporary.cleanup()

    def test_nested_frozen_dicts_are_recopied_and_action_identity_is_reserved(self):
        source = {"value": []}
        nested = FrozenDict(source)
        source["value"].append("changed-at-source")
        self.assertEqual(nested["value"], ())
        with self.assertRaises(AttributeError):
            nested["value"].append("changed-directly")
        frozen = deep_freeze(nested)
        self.assertEqual(frozen["value"], ())
        self.assertNotIsInstance(frozen, dict)
        with self.assertRaises(TypeError):
            dict.__setitem__(frozen, "value", ("bypassed",))
        with self.assertRaises(TypeError):
            frozen._FrozenDict__data["value"] = ("bypassed",)
        with self.assertRaises(AttributeError):
            frozen._FrozenDict__data = {}
        with self.assertRaises(AttributeError):
            del frozen._FrozenDict__data
        self.assertEqual(
            canonical_bytes(frozen),
            json.dumps(deep_thaw(frozen), sort_keys=True, separators=(",", ":")).encode(),
        )
        with self.assertRaisesRegex(ValueError, "action identity"):
            Action("real-id", "retain", {"id": "spoofed"})
        with self.assertRaises(TypeError):
            FrozenDict({"items": {"not", "json"}})

    def test_deep_freeze_rejects_non_json_values_and_mapping_keys(self):
        for value in (
            {"items": {"not", "json"}},
            {"items": bytearray(b"not-json")},
            {"items": object()},
            {1: "non-string-key"},
            {"value": float("nan")},
            {"value": float("inf")},
            {"value": (1 << 53)},
            {"value": "\ud800"},
        ):
            with self.subTest(value=value), self.assertRaises(TypeError):
                deep_freeze(value)
        self.assertEqual(deep_freeze({"value": -0.0})["value"], 0)

    def test_curated_codex_and_claude_markdown_adapters_emit_stable_records(self):
        timestamp = "2026-07-01T12:34:56Z"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex = root / "MEMORY.md"
            codex.write_text(
                "# Memory\n\n## Safe publication\n\n"
                "Use exact lease pushes with [[repo:dotfiles]].\n",
                encoding="utf-8",
            )
            claude = root / "CLAUDE.md"
            claude.write_text(
                "# Claude memory\n\n## Review posture\n\n"
                "Review the claimed behavior, not implementation narration.\n",
                encoding="utf-8",
            )

            codex_record = parse_codex_memory(codex, timestamp=timestamp)[0]
            claude_record = parse_claude_memory(claude, timestamp=timestamp)[0]
            self.assertTrue(
                codex_record["source_native_id"].startswith(
                    "memory--safe-publication-"
                )
            )
            self.assertEqual(codex_record["line_start"], 5)
            self.assertEqual(codex_record["line_end"], 5)
            self.assertEqual(
                codex_record["content"],
                "Use exact lease pushes with [[repo:dotfiles]].",
            )
            self.assertEqual(
                codex_record["relationships"],
                ["repo:dotfiles"],
            )
            self.assertEqual(codex_record["intended_scope"], "repo:dotfiles")
            self.assertTrue(
                claude_record["source_native_id"].startswith(
                    "claude-memory--review-posture-"
                )
            )

            first = inspect_source("codex", codex, timestamp=timestamp)[0]
            codex.write_text(
                "# Memory\n\n## Safe publication\n\n"
                "Updated guidance for [[repo:dotfiles]].\n",
                encoding="utf-8",
            )
            edited = inspect_source("codex", codex, timestamp=timestamp)[0]
            self.assertEqual(first.item_id, edited.item_id)
            self.assertNotEqual(first.content_digest, edited.content_digest)

    def test_curated_markdown_ignores_relationships_inside_fenced_code(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "MEMORY.md"
            source.write_text(
                "# Memory\n\n"
                "## Durable section\n\n"
                "[[repo:visible]]\n\n"
                "```markdown\n"
                "[[workflow:hidden]]\n"
                "```\n",
                encoding="utf-8",
            )
            records = parse_codex_memory(
                source, timestamp="2026-01-01T00:00:00Z"
            )
        self.assertEqual(records[0]["relationships"], ["repo:visible"])
        self.assertEqual(records[0]["intended_scope"], "repo:visible")

    def test_curated_adapters_use_embedded_dates_and_reject_ambiguous_headings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "MEMORY.md"
            source.write_text(
                "# Memory\n\n## 2026-07-12\n\n### Stable checkpoint\n\nFirst.\n\n"
                "## 2026-07-14\n\n### Current checkpoint\n\nSecond.\n",
                encoding="utf-8",
            )
            records = parse_codex_memory(source, timestamp="2026-01-01T00:00:00Z")
            self.assertEqual(
                [item["timestamp"] for item in records],
                ["2026-07-12T00:00:00Z", "2026-07-14T00:00:00Z"],
            )
            self.assertTrue(
                records[0]["source_native_id"].startswith(
                    "memory--2026-07-12--stable-checkpoint-"
                )
            )
            self.assertNotEqual(
                records[0]["source_native_id"], records[1]["source_native_id"]
            )

            source.write_text(
                "# Memory\n\n## Stable identity\n\nGuidance.\n",
                encoding="utf-8",
            )
            first_identity = parse_codex_memory(
                source, timestamp="2026-01-01T00:00:00Z"
            )[0]["source_native_id"]
            second_identity = parse_codex_memory(
                source, timestamp="2027-01-01T00:00:00Z"
            )[0]["source_native_id"]
            self.assertEqual(first_identity, second_identity)

            source.write_text(
                "# Memory\n\n## 2026-07-12\n\n### Dated child\n\nFirst.\n\n"
                "## Undated sibling\n\n### Undated child\n\nSecond.\n",
                encoding="utf-8",
            )
            records = parse_codex_memory(
                source, timestamp="2026-01-01T00:00:00Z"
            )
            self.assertEqual(
                [item["timestamp"] for item in records],
                ["2026-07-12T00:00:00Z", "2026-01-01T00:00:00Z"],
            )

            source.write_text(
                "# Memory\n\n## Same identity\n\nFirst.\n\n## Same identity\n\nSecond.\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ImportValidationError, "stable source identity"):
                parse_codex_memory(source, timestamp="2026-01-01T00:00:00Z")

            for relationships in (
                "[[repo:one]] and [[repo:two]]",
                "[[workflow:one]] and [[workflow:two]]",
                "[[repo:one]] and [[workflow:one]]",
            ):
                source.write_text(
                    "# Memory\n\n## Ambiguous scope\n\n" + relationships + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ImportValidationError, "ambiguous inferred scope"):
                    parse_codex_memory(
                        source, timestamp="2026-01-01T00:00:00Z"
                    )

    def test_curated_adapter_timestamp_uses_descriptor_bound_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "MEMORY.md"
            source.write_text(
                "# Memory\n\n## Durable\n\nGuidance.\n",
                encoding="utf-8",
            )
            original_stat = Path.stat

            def trust_boundary_stat(path, *args, **kwargs):
                if kwargs.get("follow_symlinks") is False:
                    return original_stat(path, *args, **kwargs)
                raise AssertionError("following pathname stat must not be used")

            with patch.object(Path, "stat", new=trust_boundary_stat):
                records = parse_codex_memory(source)
            self.assertRegex(
                records[0]["timestamp"],
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
            )

    def test_curated_adapters_reject_pre_heading_content_and_symlink_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "MEMORY.md"
            source.write_text(
                "unframed content\n# Memory\n\n## Durable\n\nGuidance.\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ImportValidationError, "before its first heading"):
                parse_codex_memory(source, timestamp="2026-01-01T00:00:00Z")

            source.write_text("# Memory\n\n## Durable\n\nGuidance.\n", encoding="utf-8")
            linked = root / "linked-memory.md"
            linked.symlink_to(source)
            with self.assertRaisesRegex(ImportValidationError, "unavailable"):
                parse_codex_memory(linked, timestamp="2026-01-01T00:00:00Z")

    def test_curated_markdown_keeps_headings_and_ignores_fenced_headings(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "MEMORY.md"
            source.write_text(
                "## Durable section\n\n"
                "Before.\n\n"
                "```markdown\n"
                "## Not a section\n"
                "```\n\n"
                "After.\n",
                encoding="utf-8",
            )

            records = parse_codex_memory(
                source, timestamp="2026-01-01T00:00:00Z"
            )

            self.assertEqual(len(records), 1)
            self.assertTrue(
                records[0]["source_native_id"].startswith("durable-section-")
            )
            self.assertEqual(records[0]["line_start"], 3)
            self.assertNotIn("## Durable section", records[0]["content"])
            self.assertIn("## Not a section", records[0]["content"])
            self.assertIn("After.", records[0]["content"])

    def test_portable_markdown_and_jsonl_adapters_preserve_manifest_metadata(self):
        metadata = {
            "id": "portable-1",
            "timestamp": "2026-07-02T03:04:05Z",
            "kind": "runbook",
            "scope": "workflow:release",
            "relationships": ["repo:dotfiles", "workflow:release"],
            "disposition": "proposed_conflict",
            "reason": "differs-from-target",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "portable.md"
            markdown.write_text(
                f"<!-- hindsight-memory: {json.dumps(metadata, sort_keys=True)} -->\n"
                "Verify the immutable release checkpoint.\n",
                encoding="utf-8",
            )
            jsonl = root / "portable.jsonl"
            jsonl.write_text(
                json.dumps({**metadata, "content": "Verify the immutable release checkpoint."})
                + "\n",
                encoding="utf-8",
            )

            markdown_record = parse_portable_markdown(markdown)[0]
            jsonl_record = parse_portable_jsonl(jsonl)[0]
            for value in (markdown_record, jsonl_record):
                self.assertEqual(value["source_native_id"], "portable-1")
                self.assertEqual(value["timestamp"], metadata["timestamp"])
                self.assertEqual(value["kind"], "runbook")
                self.assertEqual(value["intended_scope"], "workflow:release")
                self.assertEqual(value["coverage_disposition"], "proposed_conflict")
                self.assertEqual(value["relationships"], metadata["relationships"])
            self.assertEqual(markdown_record["line_start"], 2)
            self.assertEqual(jsonl_record["line_start"], 1)

    def test_portable_markdown_ignores_fenced_metadata_markers(self):
        metadata = {
            "id": "portable-1",
            "timestamp": "2026-07-02T03:04:05Z",
            "kind": "runbook",
            "scope": "global",
            "relationships": [],
            "disposition": "proposed_novel",
            "reason": "unreviewed",
        }
        forged = {**metadata, "id": "forged-item"}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "portable.md"
            source.write_text(
                f"<!-- hindsight-memory: {json.dumps(metadata)} -->\n"
                "~~~markdown\n"
                f"<!-- hindsight-memory: {json.dumps(forged)} -->\n"
                "~~~\n"
                "Durable content.\n",
                encoding="utf-8",
            )

            records = parse_portable_markdown(source)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source_native_id"], "portable-1")
            self.assertIn("forged-item", records[0]["content"])

    def test_portable_markdown_rejects_indented_metadata_markers(self):
        metadata = {
            "id": "portable-1",
            "timestamp": "2026-07-02T03:04:05Z",
            "kind": "runbook",
            "scope": "global",
            "relationships": [],
            "disposition": "proposed_novel",
            "reason": "unreviewed",
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "portable.md"
            source.write_text(
                f"    <!-- hindsight-memory: {json.dumps(metadata)} -->\n"
                "Durable content.\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ImportValidationError, "at most three leading spaces"
            ):
                parse_portable_markdown(source)

    def test_source_adapters_fail_closed_on_ambiguous_or_unknown_manifest_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown = root / "portable.md"
            markdown.write_text("unframed content\n", encoding="utf-8")
            with self.assertRaises(ImportValidationError):
                parse_portable_markdown(markdown)

            jsonl = root / "portable.jsonl"
            jsonl.write_text(
                json.dumps(
                    {
                        "id": "portable-1",
                        "timestamp": "2026-07-02T03:04:05Z",
                        "kind": "rule",
                        "scope": "global",
                        "relationships": [],
                        "disposition": "proposed_novel",
                        "reason": "unreviewed",
                        "content": "Durable guidance.",
                        "unknown": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ImportValidationError):
                parse_portable_jsonl(jsonl)

    def test_projects_stable_identity_time_provenance_tags_scope_and_relationships(self):
        item = inspect_items("codex", [record()])[0]
        self.assertEqual(len(item.item_id), 64)
        self.assertEqual(item.timestamp, "2026-07-01T12:34:56Z")
        self.assertEqual(
            item.provenance,
            {
                "source_locator": "/private/tmp/hindsight-import/MEMORY.md",
                "line_start": 10,
                "line_end": 12,
            },
        )
        self.assertEqual(item.tags, ("kind:rule", "repo:dotfiles", "scope:active", "source:codex-memory-archive"))
        self.assertEqual(item.intended_scope, "repo:dotfiles")
        self.assertEqual(item.relationships, ("repo:dotfiles", "workflow:git-publication"))
        self.assertEqual(item.coverage_disposition, "review_pending")
        self.assertEqual(item.coverage_reason, "coverage-review-required")
        with self.assertRaises(FrozenInstanceError):
            item.item_id = "different"

    def test_import_item_repr_is_payload_free(self):
        item = inspect_items(
            "codex",
            [record(content="private durable payload", coverage_reason="private-reason")],
        )[0]
        representation = repr(item)
        for private in (
            "private durable payload",
            "private-reason",
            "memories/MEMORY.md",
            "/private/tmp/hindsight-import/MEMORY.md",
            "repo:dotfiles",
            "workflow:git-publication",
        ):
            self.assertNotIn(private, representation)

    def test_review_pending_items_keep_reconciliation_incomplete(self):
        item = inspect_items(
            "codex", [record(coverage_disposition="review_pending")]
        )[0]
        projection = project_import((item,))
        plan = build_import_plan(
            projection,
            controller_plan_digest="a" * 64,
            target_bank=TARGET_BANK,
        )
        result = reconcile_import(
            projection, plan, (), approved_plan_digest=plan.plan_digest
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.missing_item_ids, (item.item_id,))

    def test_identity_uses_locator_and_native_id_not_content_or_order(self):
        first = inspect_items("codex", [record(), record("m2", "Use current evidence.")])
        edited = inspect_items("codex", [record("m1", "Updated wording."), record("m2", "Use current evidence.")])
        self.assertEqual(first[0].item_id, edited[0].item_id)
        left = project_import(first)
        right = project_import(tuple(reversed(first)))
        self.assertEqual(left.projection_digest, right.projection_digest)
        self.assertEqual(left.to_dict(), right.to_dict())

    def test_source_locators_must_already_be_canonical_absolute_paths(self):
        for locator in (
            "./memories/../memories/MEMORY.md",
            "/private/tmp/source/../MEMORY.md",
        ):
            with self.subTest(locator=locator), self.assertRaisesRegex(
                ImportValidationError, "already be canonical"
            ):
                inspect_items("codex", [record(source_locator=locator)])
        canonical = inspect_items(
            "codex",
            [record(source_locator="/private/tmp/MEMORY.md")],
        )[0]
        self.assertEqual(
            canonical.provenance["source_locator"],
            "/private/tmp/MEMORY.md",
        )

    def test_projection_orders_items_by_source_timestamp_then_stable_identity(self):
        items = inspect_items(
            "codex",
            [
                record("late", timestamp="2026-07-03T00:00:00Z"),
                record("early", timestamp="2026-07-01T00:00:00Z"),
            ],
        )
        projection = project_import(items)
        self.assertEqual(
            [item.source_native_id for item in projection.items],
            ["early", "late"],
        )

    def test_each_source_item_has_exactly_one_closed_coverage_disposition(self):
        for disposition in ("proposed_novel", "proposed_duplicate", "proposed_conflict", "omitted"):
            item = inspect_items("portable-jsonl", [record(coverage_disposition=disposition)])[0]
            self.assertEqual(
                item.coverage_disposition,
                "review_pending" if disposition.startswith("proposed_") else disposition,
            )
        for bad in (None, ["omitted", "proposed_novel"], "accepted"):
            with self.assertRaises(ImportValidationError):
                inspect_items("codex", [record(coverage_disposition=bad)])

    def test_omitted_items_never_enter_inspection_or_import_actions(self):
        items = inspect_items(
            "codex",
            [
                record(
                    "omitted",
                    coverage_disposition="omitted",
                    coverage_reason="operator-omitted",
                ),
                record("novel", "Use current evidence."),
            ],
        )
        items = tuple(
            record_novelty_review(item, review_evidence_digest="e" * 64)
            if item.source_native_id == "novel"
            else item
            for item in items
        )
        omitted = next(
            item for item in items if item.coverage_disposition == "omitted"
        )
        novel = next(
            item for item in items if item.coverage_disposition != "omitted"
        )
        projection = project_import(items)
        self.assertEqual(projection.skipped_item_ids, (omitted.item_id,))
        self.assertEqual(
            [dict(value) for value in projection.skip_evidence],
            [
                {
                    "item_id": omitted.item_id,
                    "reason": "operator-omitted",
                }
            ],
        )
        self.assertEqual(projection.pending_items, (novel,))

        calls = []
        result = run_import_inspection(
            projection,
            inspector=lambda item: calls.append(item.item_id),
            max_items=2,
            requests_per_window=2,
            window_seconds=1.0,
            clock=lambda: 0.0,
            sleep=lambda _seconds: None,
            rate_limit_state_path=(
                Path(self.temporary.name) / "omitted-rate-limit.json"
            ),
        )
        self.assertEqual(calls, [novel.item_id])
        self.assertIn(omitted.item_id, result.completed_item_ids)

        plan = build_import_plan(
            projection,
            controller_plan_digest="c" * 64,
            target_bank=TARGET_BANK,
        )
        self.assertEqual(
            [action["item_id"] for action in plan.actions], [novel.item_id]
        )

        malformed = replace(
            projection,
            pending_items=projection.items,
            skipped_item_ids=(),
            skip_evidence=(),
            projection_digest="0" * 64,
        )
        with self.assertRaisesRegex(
            ImportValidationError, "exclude omitted items"
        ):
            run_import_inspection(
                malformed,
                inspector=lambda _item: self.fail("inspector must not run"),
                rate_limit_state_path=(
                    Path(self.temporary.name)
                    / "malformed-omitted-rate-limit.json"
                ),
            )

    def test_curated_file_inspection_defers_unreviewed_novelty(self):
        source = Path(self.temporary.name) / "MEMORY.md"
        source.write_text(
            "## Durable\n\nUse exact evidence.\n", encoding="utf-8"
        )
        item = inspect_source(
            "codex", source, timestamp="2026-07-01T00:00:00Z"
        )[0]
        self.assertEqual(item.coverage_disposition, "review_pending")
        self.assertEqual(item.coverage_reason, "coverage-review-required")
        projection = project_import((item,))
        self.assertEqual(projection.skipped_item_ids, ())
        self.assertEqual(projection.pending_items, (item,))
        inspected = []
        result = run_import_inspection(
            projection,
            inspector=lambda pending: inspected.append(pending.item_id),
            max_items=1,
            requests_per_window=1,
            window_seconds=1.0,
            clock=lambda: 0.0,
            sleep=lambda _seconds: None,
            rate_limit_state_path=(
                Path(self.temporary.name) / "review-pending-rate-limit.json"
            ),
        )
        self.assertEqual(inspected, [item.item_id])
        self.assertEqual(result.completed_item_ids, (item.item_id,))

        untrusted = inspect_items(
                "codex",
                [
                    record(
                        coverage_disposition="proposed_novel",
                        coverage_reason="unreviewed-source-item",
                    )
                ],
            )[0]
        self.assertEqual(untrusted.coverage_disposition, "review_pending")


    def test_closed_records_malformed_time_provenance_tags_and_secret_like_content_fail(self):
        bad_records = [
            record(extra=True),
            record(timestamp="yesterday"),
            record(line_start=13, line_end=12),
            record(kind="credential"),
            record(intended_scope="branch:volatile"),
            record(content="password = hunter2"),
            record(content="-----BEGIN " + "PRIVATE KEY-----"),
        ]
        for value in bad_records:
            with self.subTest(value=value):
                with self.assertRaises(ImportValidationError):
                    inspect_items("claude", [value])

    def test_named_secret_scan_policy_covers_maintained_credential_classes(self):
        self.assertEqual(SECRET_SCAN_POLICY_VERSION, 1)
        self.assertEqual(
            {detector_id for detector_id, _pattern in SECRET_DETECTORS},
            {
                "private-key",
                "credential-assignment",
                "provider-token",
                "credential-url",
                "authorization-header",
                "jwt",
            },
        )
        secret_like = (
            "-----BEGIN ENCRYPTED " + "PRIVATE KEY-----",
            "-----BEGIN PGP PRIVATE " + "KEY BLOCK-----",
            "AK" + "IAABCDEFGHIJKLMNOP",
            "gh" + "p_abcdefghijklmnopqrstuvwxyz",
            "xo" + "xb-1234567890-abcdefghijklmnop",
            "postgresql://operator:private-value@127.0.0.1/db",
            "AWS_SECRET_ACCESS_KEY=private-value",
            "Authorization: " + "Bearer private-value",
            "eyJabcdefghi.abcdefghijkl.abcdefghijkl",
        )
        for content in secret_like:
            with self.subTest(content=content):
                with self.assertRaisesRegex(ImportValidationError, "secret-like"):
                    inspect_items("codex", [record(content=content)])

    def test_resume_skips_only_matching_identity_and_complete_item_digest(self):
        items = inspect_items("codex", [record(), record("m2", "Use current evidence.")])
        resume = {items[0].item_id: import_item_digest(items[0]), items[1].item_id: "0" * 64}
        projection = project_import(items, resume_state=resume)
        self.assertEqual(projection.skipped_item_ids, (items[0].item_id,))
        self.assertEqual(
            dict(projection.skip_evidence[0]),
            {
                "item_id": items[0].item_id,
                "item_digest": import_item_digest(items[0]),
            },
        )
        self.assertEqual([item.item_id for item in projection.pending_items], [items[1].item_id])

        legacy = project_import(
            items,
            resume_state={items[0].item_id: items[0].content_digest},
        )
        self.assertEqual(legacy.skipped_item_ids, ())
        self.assertEqual(legacy.pending_items, legacy.items)

        with self.assertRaisesRegex(
            ImportValidationError, "unknown projection item identities"
        ):
            project_import(
                items,
                resume_state={"f" * 64: import_item_digest(items[0])},
            )

    def test_projection_rejects_unsubstantiated_skipped_items(self):
        item = inspect_items("codex", [record()])[0]
        projection = project_import((item,))
        forged = replace(
            projection,
            pending_items=(),
            skipped_item_ids=(item.item_id,),
            skip_evidence=(
                {"item_id": item.item_id, "item_digest": "0" * 64},
            ),
        )
        forged = replace(forged, projection_digest=digest(forged.body()))
        with self.assertRaisesRegex(
            ImportValidationError, "canonical item digest"
        ):
            validate_projection(forged)

        omitted = inspect_items(
            "codex",
            [
                record(
                    coverage_disposition="omitted",
                    coverage_reason="operator-omitted",
                )
            ],
        )[0]
        projection = project_import((omitted,))
        forged = replace(
            projection,
            skip_evidence=(
                {"item_id": omitted.item_id, "reason": "different-reason"},
            ),
        )
        forged = replace(forged, projection_digest=digest(forged.body()))
        with self.assertRaisesRegex(
            ImportValidationError, "explicit omission reason"
        ):
            validate_projection(forged)

    def test_bounded_inspection_run_is_resumable_and_rate_limit_aware(self):
        items = inspect_items(
            "codex",
            [record("m1"), record("m2", "Use current evidence."), record("m3", "Keep logs content-free.")],
        )
        projection = project_import(items)
        current_time = [0.0]
        sleeps = []
        calls = []

        def clock():
            return current_time[0]

        def sleep(seconds):
            sleeps.append(seconds)
            current_time[0] += seconds

        result = run_import_inspection(
            projection,
            inspector=lambda item: calls.append(item.item_id),
            max_items=3,
            requests_per_window=2,
            window_seconds=10.0,
            clock=clock,
            sleep=sleep,
            rate_limit_state_path=Path(self.temporary.name) / "rate-limit.json",
        )
        self.assertEqual(tuple(calls), result.completed_item_ids)
        self.assertEqual(sleeps, [10.0])
        self.assertEqual(result.deferred_item_ids, ())
        self.assertEqual(
            result.resume_state,
            {item.item_id: import_item_digest(item) for item in projection.items},
        )
        self.assertTrue(all(set(event) == {"item_id", "status"} for event in result.events))

        resumed_calls = []
        resumed = run_import_inspection(
            projection,
            inspector=lambda item: resumed_calls.append(item.item_id),
            resume_state={
                items[0].item_id: import_item_digest(items[0]),
            },
            max_items=1,
            requests_per_window=1,
            window_seconds=1.0,
            clock=lambda: 0.0,
            sleep=lambda _seconds: None,
            rate_limit_state_path=Path(self.temporary.name) / "resume-rate-limit.json",
        )
        self.assertEqual(len(resumed_calls), 1)
        self.assertNotIn(items[0].item_id, resumed_calls)
        self.assertEqual(len(resumed.deferred_item_ids), 1)

    def test_inspection_failure_is_content_free_and_resumable(self):
        projection = project_import(
            inspect_items("codex", [record("m1"), record("m2", "Use current evidence.")])
        )
        call_count = [0]

        def fail_second(_item):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("private payload must not enter the run record")

        partial = run_import_inspection(
            projection,
            inspector=fail_second,
            max_items=2,
            rate_limit_state_path=Path(self.temporary.name) / "failure-rate-limit.json",
        )
        self.assertEqual([event["status"] for event in partial.events], ["inspected", "failed"])
        self.assertNotIn("private payload", repr(partial.events))
        failure = partial.events[-1]
        self.assertEqual(failure["error_type"], "RuntimeError")
        self.assertRegex(
            failure["error_message"],
            r"^inspector callback failed \([0-9a-f]{16}\)$",
        )
        self.assertLessEqual(len(failure["error_message"]), 128)
        self.assertEqual(len(partial.resume_state), 1)

        resumed_items = []
        completed = run_import_inspection(
            projection,
            inspector=lambda item: resumed_items.append(item.item_id),
            max_items=2,
            rate_limit_state_path=Path(self.temporary.name) / "failure-rate-limit.json",
        )
        self.assertEqual(resumed_items, [projection.pending_items[1].item_id])
        self.assertEqual(len(completed.completed_item_ids), 2)
        self.assertEqual(completed.deferred_item_ids, ())

    def test_inspection_checkpoint_rejects_conflicting_incoming_resume_state(self):
        projection = project_import(
            inspect_items("codex", [record("m1"), record("m2", "Use evidence.")])
        )
        state_path = Path(self.temporary.name) / "merge-rate-limit.json"
        first = run_import_inspection(
            projection,
            inspector=lambda _item: None,
            max_items=1,
            rate_limit_state_path=state_path,
        )
        completed_id = first.completed_item_ids[0]

        with self.assertRaisesRegex(
            ImportValidationError, "does not match|conflicts"
        ):
            run_import_inspection(
                projection,
                inspector=lambda _item: None,
                resume_state={completed_id: "0" * 64},
                max_items=1,
                rate_limit_state_path=state_path,
            )

    def test_checkpoint_and_resume_digests_must_match_projection_items(self):
        projection = project_import(inspect_items("codex", [record("m1")]))
        item = projection.items[0]
        state_path = Path(self.temporary.name) / "digest-bound-rate-limit.json"
        with self.assertRaisesRegex(ImportValidationError, "does not match"):
            run_import_inspection(
                projection,
                inspector=lambda _item: self.fail("inspector must not run"),
                resume_state={item.item_id: "0" * 64},
                rate_limit_state_path=state_path,
            )

        checkpoint = state_path.with_name(
            f"{state_path.name}.inspection-checkpoint."
            f"{projection.projection_digest}"
        )
        checkpoint.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "projection_digest": projection.projection_digest,
                    "completed": {item.item_id: "0" * 64},
                }
            ),
            encoding="utf-8",
        )
        checkpoint.chmod(0o600)
        with self.assertRaisesRegex(ImportValidationError, "does not match"):
            run_import_inspection(
                projection,
                inspector=lambda _item: self.fail("inspector must not run"),
                rate_limit_state_path=state_path,
            )

    def test_inspection_rejects_non_finite_window_and_clock_values(self):
        projection = project_import(inspect_items("codex", [record("m1")]))
        state_path = Path(self.temporary.name) / "finite-rate-limit.json"
        for window in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(window=window), self.assertRaisesRegex(
                ImportValidationError, "window_seconds"
            ):
                run_import_inspection(
                    projection,
                    inspector=lambda _item: None,
                    window_seconds=window,
                    rate_limit_state_path=state_path,
                )
        for observed in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(observed=observed), self.assertRaisesRegex(
                ImportValidationError, "finite number"
            ):
                run_import_inspection(
                    projection,
                    inspector=lambda _item: None,
                    clock=lambda observed=observed: observed,
                    rate_limit_state_path=state_path,
                )

    def test_rate_limit_is_shared_across_separate_invocations(self):
        projection = project_import(
            inspect_items("codex", [record("m1"), record("m2", "Use evidence.")])
        )
        state_path = Path(self.temporary.name) / "shared-rate-limit.json"
        current_time = [10.0]
        sleeps = []

        first = run_import_inspection(
            projection,
            inspector=lambda _item: None,
            max_items=1,
            requests_per_window=1,
            window_seconds=5,
            clock=lambda: current_time[0],
            sleep=lambda seconds: current_time.__setitem__(0, current_time[0] + seconds),
            rate_limit_state_path=state_path,
        )

        def advance(seconds):
            sleeps.append(seconds)
            current_time[0] += seconds

        second = run_import_inspection(
            projection,
            inspector=lambda _item: None,
            resume_state=first.resume_state,
            max_items=1,
            requests_per_window=1,
            window_seconds=5,
            clock=lambda: current_time[0],
            sleep=advance,
            rate_limit_state_path=state_path,
        )
        self.assertEqual(sleeps, [5.0])
        self.assertEqual(second.deferred_item_ids, ())

    def test_rate_limit_recovers_conservatively_after_clock_rollback(self):
        projection = project_import(inspect_items("codex", [record("m1")]))
        state_path = Path(self.temporary.name) / "rollback-rate-limit.json"
        current_time = [1000.0]
        run_import_inspection(
            projection,
            inspector=lambda _item: None,
            max_items=1,
            requests_per_window=1,
            window_seconds=5,
            clock=lambda: current_time[0],
            sleep=lambda seconds: current_time.__setitem__(
                0, current_time[0] + seconds
            ),
            rate_limit_state_path=state_path,
        )
        current_time[0] = 10.0
        state_path.with_name(
            f"{state_path.name}.inspection-checkpoint.{projection.projection_digest}"
        ).unlink()
        sleeps = []

        def advance(seconds):
            sleeps.append(seconds)
            current_time[0] += seconds

        result = run_import_inspection(
            projection,
            inspector=lambda _item: None,
            max_items=1,
            requests_per_window=1,
            window_seconds=5,
            clock=lambda: current_time[0],
            sleep=advance,
            rate_limit_state_path=state_path,
        )
        self.assertEqual(sleeps, [5.0])
        self.assertEqual(
            result.completed_item_ids, (projection.items[0].item_id,)
        )

    def test_inspection_checkpoint_name_is_namespaced_by_projection_digest(self):
        projection = project_import(inspect_items("codex", [record("m1")]))
        state_path = Path(self.temporary.name) / "rate-limit.json"
        run_import_inspection(
            projection,
            inspector=lambda _item: None,
            rate_limit_state_path=state_path,
        )
        checkpoint = state_path.with_name(
            f"{state_path.name}.inspection-checkpoint.{projection.projection_digest}"
        )
        self.assertTrue(checkpoint.exists())

    def test_inspection_checkpoint_uses_a_bounded_nonblocking_flock(self):
        projection = project_import(inspect_items("codex", [record("m1")]))
        state_path = Path(self.temporary.name) / "bounded-lock.json"
        real_flock = fcntl.flock
        operations = []

        def require_nonblocking(descriptor, operation):
            operations.append(operation)
            if operation & fcntl.LOCK_EX and not operation & fcntl.LOCK_NB:
                raise AssertionError("checkpoint flock may block indefinitely")
            return real_flock(descriptor, operation)

        with patch(
            "hindsight_memory_control_plane.import_runner.fcntl.flock",
            side_effect=require_nonblocking,
        ):
            run_import_inspection(
                projection,
                inspector=lambda _item: None,
                rate_limit_state_path=state_path,
            )
        self.assertTrue(any(operation & fcntl.LOCK_NB for operation in operations))

    def test_inspection_checkpoint_applies_one_bound_to_reads_and_writes(self):
        checkpoint = Path(self.temporary.name) / "bounded-checkpoint.json"
        projection_digest = "d" * 64
        with patch(
            "hindsight_memory_control_plane.import_runner.MAX_INSPECTION_CHECKPOINT_BYTES",
            128,
        ):
            with self.assertRaisesRegex(ImportValidationError, "unsafe"):
                _inspection_checkpoint(
                    checkpoint,
                    projection_digest,
                    completed={"item": "e" * 64},
                )
            self.assertFalse(checkpoint.exists())

            checkpoint.write_bytes(b"x" * 129)
            checkpoint.chmod(0o600)
            with self.assertRaisesRegex(ImportValidationError, "unsafe"):
                _inspection_checkpoint(checkpoint, projection_digest)

    def test_rate_limit_serializes_concurrent_invocations(self):
        state_path = Path(self.temporary.name) / "concurrent-rate-limit.json"
        window_seconds = 5.0
        barrier = threading.Barrier(3)
        current_time = [10.0]
        clock_lock = threading.Lock()
        grants = []
        delays = []
        failures = []

        def invoke():
            try:
                barrier.wait(timeout=THREAD_TIMEOUT_SECONDS)
                while True:
                    with clock_lock:
                        observed_time = current_time[0]
                    reservation = _reserve_rate_limit_slot(
                        state_path,
                        clock=lambda: observed_time,
                        requests_per_window=1,
                        window_seconds=window_seconds,
                    )
                    if reservation is None:
                        with clock_lock:
                            grants.append(observed_time)
                        break
                    delay, _observed = reservation
                    with clock_lock:
                        delays.append(delay)
                        current_time[0] = max(
                            current_time[0], observed_time + delay
                        )
            except Exception as error:
                failures.append(error)

        threads = [threading.Thread(target=invoke) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=THREAD_TIMEOUT_SECONDS)
        for thread in threads:
            thread.join(timeout=THREAD_TIMEOUT_SECONDS)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(sorted(grants), [10.0, 15.0])
        self.assertEqual(delays, [window_seconds])

    def test_rate_limit_update_never_exposes_a_partially_written_state(self):
        state_path = Path(self.temporary.name) / "atomic-rate-limit.json"
        self.assertIsNone(
            _reserve_rate_limit_slot(
                state_path,
                clock=lambda: 10.0,
                requests_per_window=2,
                window_seconds=5.0,
            )
        )
        original = state_path.read_bytes()
        entered_write = threading.Event()
        release_write = threading.Event()
        failures = []
        write_sizes = []
        real_write = __import__("os").write

        def pause_write(descriptor, body):
            written = real_write(descriptor, body[: max(1, len(body) // 2)])
            write_sizes.append(written)
            entered_write.set()
            if not release_write.wait(timeout=THREAD_TIMEOUT_SECONDS):
                raise TimeoutError("test did not release rate-limit write")
            return written

        def reserve():
            try:
                _reserve_rate_limit_slot(
                    state_path,
                    clock=lambda: 11.0,
                    requests_per_window=2,
                    window_seconds=5.0,
                )
            except Exception as error:
                failures.append(error)

        with patch(
            "hindsight_memory_control_plane.import_runner.os.write",
            side_effect=pause_write,
        ):
            thread = threading.Thread(target=reserve)
            thread.start()
            self.assertTrue(entered_write.wait(timeout=THREAD_TIMEOUT_SECONDS))
            self.assertEqual(state_path.read_bytes(), original)
            json.loads(state_path.read_text(encoding="utf-8"))
            release_write.set()
            thread.join(timeout=THREAD_TIMEOUT_SECONDS)

        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertGreaterEqual(len(write_sizes), 2)
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(final_state["request_times"], [10.0, 11.0])
        self.assertTrue((state_path.parent / f"{state_path.name}.lock").is_file())
        self.assertEqual(
            [path for path in state_path.parent.iterdir() if ".tmp-" in path.name],
            [],
        )

    def test_rate_limit_state_requires_an_absolute_private_file(self):
        projection = project_import(inspect_items("codex", [record("m1")]))
        with self.assertRaisesRegex(ImportValidationError, "path is invalid"):
            run_import_inspection(
                projection,
                inspector=lambda _item: self.fail("inspector must not run"),
                rate_limit_state_path=Path("relative-rate-limit.json"),
            )

        state_path = Path(self.temporary.name) / "loose-rate-limit.json"
        state_path.write_text("{}", encoding="utf-8")
        state_path.chmod(0o644)
        with self.assertRaisesRegex(ImportValidationError, "state is unsafe"):
            run_import_inspection(
                projection,
                inspector=lambda _item: self.fail("inspector must not run"),
                rate_limit_state_path=state_path,
            )
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o644)

    def test_rate_limit_fifo_paths_fail_without_blocking(self):
        root = Path(self.temporary.name)
        for fifo_kind in ("lock", "state"):
            with self.subTest(fifo_kind=fifo_kind):
                state_path = root / f"{fifo_kind}-fifo.json"
                fifo_path = (
                    root / f"{state_path.name}.lock"
                    if fifo_kind == "lock"
                    else state_path
                )
                os.mkfifo(fifo_path, 0o600)
                outcome = []

                def reserve():
                    try:
                        _reserve_rate_limit_slot(
                            state_path,
                            clock=lambda: 10.0,
                            requests_per_window=2,
                            window_seconds=5.0,
                        )
                    except BaseException as error:
                        outcome.append(error)

                worker = threading.Thread(target=reserve, daemon=True)
                worker.start()
                worker.join(timeout=THREAD_TIMEOUT_SECONDS)
                self.assertFalse(worker.is_alive(), "FIFO inspection blocked")
                self.assertEqual(len(outcome), 1)
                self.assertIsInstance(outcome[0], ImportValidationError)
                self.assertRegex(str(outcome[0]), "state is unsafe")

    def test_rate_limit_parent_creation_does_not_follow_symlinks(self):
        projection = project_import(inspect_items("codex", [record("m1")]))
        root = Path(self.temporary.name)
        outside = root / "outside"
        outside.mkdir(mode=0o700)
        linked = root / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        target = linked / "nested" / "rate-limit.json"
        with self.assertRaisesRegex(ImportValidationError, "is unavailable"):
            run_import_inspection(
                projection,
                inspector=lambda _item: self.fail("inspector must not run"),
                rate_limit_state_path=target,
            )
        self.assertFalse((outside / "nested").exists())

    def test_projection_validation_detects_tampering(self):
        projection = project_import(reviewed_items("codex", [record()]))
        validate_projection(projection)
        object.__setattr__(projection, "projection_digest", "0" * 64)
        with self.assertRaises(ImportValidationError):
            validate_projection(projection)

    def test_all_persisted_source_metadata_is_secret_scanned(self):
        cases = {
            "source native identity": {
                "source_native_id": "gh" + "p_abcdef"
            },
            "source locator": {
                "source_locator": "/private/tmp/gh" + "p_abcdef/MEMORY.md"
            },
            "coverage reason": {"coverage_reason": "gh" + "p_abcdef"},
            "relationship": {
                "relationships": ["item:gh" + "p_abcdef"]
            },
        }
        for label, overrides in cases.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                ImportValidationError, "secret-like import metadata",
            ):
                inspect_items("codex", [record(**overrides)])

    def test_projection_revalidates_every_import_item_field(self):
        item = inspect_items("codex", [record()])[0]
        forged = replace(item, source_native_id="different")
        projection = project_import((item,))
        object.__setattr__(projection, "items", (forged,))
        object.__setattr__(projection, "pending_items", (forged,))
        object.__setattr__(
            projection, "projection_digest", digest(projection.body())
        )
        with self.assertRaisesRegex(ImportValidationError, "item identity"):
            validate_projection(projection)

    def test_projection_rejects_malformed_metadata_with_import_error(self):
        item = inspect_items("codex", [record()])[0]
        for field, value in (
            ("coverage_reason", ["novel"]),
            ("relationships", ["item:other"]),
        ):
            with self.subTest(field=field):
                malformed = replace(item, **{field: value})
                with self.assertRaises(ImportValidationError):
                    project_import((malformed,))

    def test_plan_is_digest_bound_and_apply_requires_exact_later_approval(self):
        projection = project_import(reviewed_items("codex", [record()]))
        plan = build_import_plan(
            projection,
            controller_plan_digest="a" * 64,
            target_bank=TARGET_BANK,
        )
        self.assertEqual(
            plan.actions[0]["item_digest"],
            import_item_digest(projection.pending_items[0]),
        )
        self.assertNotIn("content_digest", plan.actions[0])
        calls = []
        with self.assertRaises(ImportValidationError):
            apply_import_plan(plan, projection=projection, approved_plan_digest=None, controller_apply=calls.append)
        with self.assertRaises(ImportValidationError):
            apply_import_plan(plan, projection=projection, approved_plan_digest="b" * 64, controller_apply=calls.append)
        result = apply_import_plan(plan, projection=projection, approved_plan_digest=plan.plan_digest, controller_apply=calls.append)
        self.assertEqual(result, plan.plan_digest)
        self.assertEqual(calls, [plan.to_dict()])

        object.__setattr__(plan, "actions", ())
        with self.assertRaisesRegex(ImportValidationError, "does not match its body"):
            apply_import_plan(plan, projection=projection, approved_plan_digest=result, controller_apply=calls.append)

    def test_apply_revalidates_every_directly_constructed_import_action(self):
        projection = project_import(reviewed_items("codex", [record()]))
        original = build_import_plan(
            projection,
            controller_plan_digest="a" * 64,
            target_bank=TARGET_BANK,
        )
        valid = dict(original.actions[0])
        cases = {
            "unknown": {**valid, "unexpected": True},
            "bad item identity": {**valid, "item_id": "invalid"},
            "bad item digest": {**valid, "item_digest": "invalid"},
            "wrong valid item digest": {**valid, "item_digest": "0" * 64},
            "wrong operation": {**valid, "operation": "delete"},
        }
        for label, action in cases.items():
            with self.subTest(label=label):
                forged = replace(original, actions=(action,))
                forged = replace(forged, plan_digest=digest(forged.body()))
                calls = []
                with self.assertRaises(ImportValidationError):
                    apply_import_plan(
                        forged,
                        projection=projection,
                        approved_plan_digest=forged.plan_digest,
                        controller_apply=calls.append,
                    )
                self.assertEqual(calls, [])

        forged = replace(original, actions=(valid, valid))
        forged = replace(forged, plan_digest=digest(forged.body()))
        calls = []
        with self.assertRaisesRegex(ImportValidationError, "duplicated"):
            apply_import_plan(
                forged,
                projection=projection,
                approved_plan_digest=forged.plan_digest,
                controller_apply=calls.append,
            )
        self.assertEqual(calls, [])

    def test_timestamps_are_canonicalized_to_utc(self):
        item = inspect_items(
            "codex", [record(timestamp="2026-07-01T14:34:56+02:00")]
        )[0]
        self.assertEqual(item.timestamp, "2026-07-01T12:34:56Z")

    def test_projection_validation_rejects_structurally_inconsistent_membership(self):
        projection = project_import(inspect_items("codex", [record()]))
        object.__setattr__(projection, "pending_items", ())
        with self.assertRaisesRegex(ImportValidationError, "skipped items"):
            validate_projection(projection)

    def test_portable_json_rejects_duplicate_keys_and_non_finite_values(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.jsonl"
            duplicate.write_text(
                '{"id":"a","id":"b","timestamp":"2026-07-01T00:00:00Z","kind":"rule","scope":"global","relationships":[],"disposition":"proposed_novel","reason":"new","content":"safe"}\n',
                encoding="utf-8",
            )
            with self.assertRaises(ImportValidationError):
                parse_portable_jsonl(duplicate)

            non_finite = Path(directory) / "non-finite.jsonl"
            non_finite.write_text('{"value":NaN}\n', encoding="utf-8")
            with self.assertRaises(ImportValidationError):
                parse_portable_jsonl(non_finite)

    def test_reconcile_is_complete_only_for_exact_item_and_digest_receipts(self):
        projection = project_import(reviewed_items("codex", [record(), record("m2", "Use current evidence.")]))
        receipts = [
            {
                "item_id": item.item_id,
                "item_digest": import_item_digest(item),
                "status": "imported",
            }
            for item in projection.pending_items
        ]
        plan = build_import_plan(
            projection,
            controller_plan_digest="a" * 64,
            target_bank=TARGET_BANK,
        )
        receipts = [
            {
                **receipt,
                "import_plan_digest": plan.plan_digest,
                "target_bank": TARGET_BANK.to_dict(),
            }
            for receipt in receipts
        ]
        result = reconcile_import(
            projection,
            plan,
            receipts,
            approved_plan_digest=plan.plan_digest,
        )
        self.assertTrue(result.complete)
        self.assertEqual(result.missing_item_ids, ())

        forged = replace(plan, coverage_digest="0" * 64)
        forged = replace(forged, plan_digest=digest(forged.body()))
        with self.assertRaisesRegex(
            ImportValidationError, "inspected projection"
        ):
            reconcile_import(
                projection,
                forged,
                (),
                approved_plan_digest=forged.plan_digest,
            )
        with self.assertRaises(ImportValidationError):
            reconcile_import(
                projection,
                plan,
                [{**receipts[0], "item_digest": "f" * 64}, receipts[1]],
                approved_plan_digest=plan.plan_digest,
            )
        with self.assertRaisesRegex(ImportValidationError, "exact approved"):
            reconcile_import(
                projection,
                plan,
                receipts,
                approved_plan_digest="f" * 64,
            )
        with self.assertRaisesRegex(ImportValidationError, "does not match"):
            reconcile_import(
                projection,
                plan,
                [
                    {**receipts[0], "import_plan_digest": "f" * 64},
                    receipts[1],
                ],
                approved_plan_digest=plan.plan_digest,
            )
        with self.assertRaisesRegex(ImportValidationError, "does not match"):
            reconcile_import(
                projection,
                plan,
                [
                    {
                        **receipts[0],
                        "target_bank": {
                            "profile_id": "core",
                            "bank_id": "personal",
                        },
                    },
                    receipts[1],
                ],
                approved_plan_digest=plan.plan_digest,
            )


if __name__ == "__main__":
    unittest.main()
