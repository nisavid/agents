from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from pathlib import Path
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY / "scripts" / "validate_source_skill_lineage.py"
REFRESHER = REPOSITORY / "scripts" / "refresh_source_skill_lineage.py"
LINEAGE_ROOT = Path("release/source-skill-lineage")
RESEARCH_REPORT = Path(
    "docs/superpowers/research/2026-08-18-source-skill-lineage-and-drift.md"
)
SOURCE_MANIFEST = LINEAGE_ROOT / "source-manifest.json"
CONTRIBUTION_LEDGER = LINEAGE_ROOT / "contribution-ledger.json"
HOST_MANIFESTS = (
    LINEAGE_ROOT / "installed-hosts/initial-personal-cachyos-v1.json",
    LINEAGE_ROOT / "installed-hosts/initial-work-macos-v1.json",
)
EXPECTED_UNRESOLVED_SOURCE_IDS = (
    "firecrawl-cli",
    "firecrawl-skill-set",
    "firecrawl-workflows",
    "heredotnow-here-now",
    "heygen-hyperframes",
    "obra-elements-of-style",
    "openai-github-specialists",
    "review-atlas-private",
    "vectorize-hindsight",
    "vercel-labs-skill-set",
    "withgraphite-agent-skills",
    "yeet",
)
EXPECTED_MAPPED_SEMANTIC_UNRESOLVED_CONTRIBUTION_IDS = (
    "openai-gh-address-comments-response-only",
    "openai-gh-address-comments-review-acquisition",
    "openai-gh-fix-ci-actions-specialist",
    "openai-github-direct-create-boundary",
    "openai-github-orientation-and-summary",
    "review-atlas-lineage",
    "yeet-checkpoint-lineage",
    "yeet-fill-create-boundary",
    "yeet-publication-lineage",
)
EXPECTED_DISCOVERY_PRECEDENCE = {
    "initial-work-macos-v1": (
        "not-observed",
        {
            "evidence_needed": ["active discovery precedence receipt"],
            "reason": (
                "Installed source presence was captured, but active discovery "
                "precedence was not."
            ),
            "status": "unresolved",
        },
    ),
    "initial-personal-cachyos-v1": (
        "transport-unavailable",
        {
            "evidence_needed": [
                "restored read-only transport",
                "active discovery precedence receipt",
            ],
            "reason": (
                "The configured read-only route was unavailable, so active "
                "discovery precedence was not observed."
            ),
            "status": "unresolved",
        },
    ),
}


def expected_unresolved_contribution_ids(root: Path) -> list[str]:
    ledger = json.loads((root / CONTRIBUTION_LEDGER).read_text(encoding="utf-8"))
    return sorted(
        contribution["id"]
        for contribution in ledger["contributions"]
        if contribution["mapping_status"] == "unresolved"
        or contribution["semantic_drift"]["status"] == "unresolved"
    )


def expected_unresolved_host_observation_ids(root: Path) -> list[str]:
    unresolved = []
    for relative in HOST_MANIFESTS:
        host = json.loads((root / relative).read_text(encoding="utf-8"))
        profile_id = host["profile_id"]
        unresolved.append(f"{profile_id}:discovery-precedence")
        for observation in host["source_observations"]:
            if observation["status"] == "unresolved" or (
                observation["status"] == "installed"
                and observation["unobserved_skill_ids"]
            ):
                unresolved.append(f"{profile_id}:{observation['source_id']}")
    return sorted(unresolved)


def load_validator():
    specification = importlib.util.spec_from_file_location(
        "validate_source_skill_lineage", VALIDATOR
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_refresher():
    specification = importlib.util.spec_from_file_location(
        "refresh_source_skill_lineage", REFRESHER
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CheckedInSourceSkillLineageTests(unittest.TestCase):
    def test_checked_in_lineage_is_complete_and_canonical(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(VALIDATOR), str(REPOSITORY)],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "source-skill-lineage-valid\n")

        manifest = json.loads(
            (REPOSITORY / SOURCE_MANIFEST).read_text(encoding="utf-8")
        )
        task_witness = next(
            item for item in manifest["sources"] if item["id"] == "ivan-task-witness"
        )
        self.assertEqual(task_witness["skill_ids"], [])
        graphite = next(
            item
            for item in manifest["sources"]
            if item["id"] == "withgraphite-agent-skills"
        )
        self.assertEqual(graphite["baseline"], graphite["current"])
        self.assertEqual(
            (
                graphite["baseline"]["commit_sha1"],
                graphite["baseline"]["tree_sha1"],
                graphite["baseline"]["skill_tree_sha256"],
                graphite["baseline"]["entry_count"],
                graphite["baseline"]["total_bytes"],
                graphite["byte_drift"]["status"],
            ),
            (
                "df3c9a36ec90af0b78df991608b3700b53e38ee5",
                "22a8c483deab5cbc33210f5b07e00a4312232426",
                "sha256:87adde7d78f8b972d1b0db6bacccb6a60bbe61853b1df91779acebea1a789066",
                1,
                8995,
                "unchanged",
            ),
        )
        self.assertEqual(graphite["baseline"]["license"]["status"], "unresolved")
        ledger = json.loads(
            (REPOSITORY / CONTRIBUTION_LEDGER).read_text(encoding="utf-8")
        )
        graphite_contribution = next(
            item
            for item in ledger["contributions"]
            if item["id"] == "withgraphite-agent-skills-candidate-contribution"
        )
        thermos_contribution = next(
            item
            for item in ledger["contributions"]
            if item["id"] == "cursor-thermos-rule-lineage"
        )
        self.assertEqual(
            graphite_contribution["semantic_drift"]["status"], "unresolved"
        )
        self.assertEqual(thermos_contribution["semantic_drift"]["status"], "unresolved")
        self.assertEqual(
            thermos_contribution["evidence_needed"],
            ["rule-level source-to-destination mapping"],
        )
        self.assertTrue(
            all(
                item["current"]["status"] == "resolved"
                for item in manifest["sources"]
                if item["authority"]["kind"] == "git"
            )
        )
        for relative in HOST_MANIFESTS:
            host = json.loads((REPOSITORY / relative).read_text(encoding="utf-8"))
            _, expected = EXPECTED_DISCOVERY_PRECEDENCE[host["profile_id"]]
            self.assertEqual(host.get("discovery_precedence"), expected)
        summary = load_validator().validate_lineage(REPOSITORY)
        self.assertTrue(
            {
                "mergecraft-installed-source-relationship-unresolved",
                "rolecasting-installed-source-relationship-unresolved",
                "versionkeeping-installed-source-relationship-unresolved",
            }
            <= set(summary["unresolved_contribution_ids"])
        )
        self.assertEqual(
            summary["unresolved_host_observation_ids"],
            expected_unresolved_host_observation_ids(REPOSITORY),
        )
        self.assertEqual(
            summary["unresolved_source_ids"], list(EXPECTED_UNRESOLVED_SOURCE_IDS)
        )
        report = (REPOSITORY / RESEARCH_REPORT).read_text(encoding="utf-8")
        for unresolved_id in (
            "mergecraft-installed-source-relationship-unresolved",
            "rolecasting-installed-source-relationship-unresolved",
            "versionkeeping-installed-source-relationship-unresolved",
            *EXPECTED_UNRESOLVED_SOURCE_IDS,
        ):
            self.assertIn(unresolved_id, report)

    def test_refresh_check_is_byte_clean(self) -> None:
        before = {
            relative: (REPOSITORY / relative).read_bytes()
            for relative in (
                SOURCE_MANIFEST,
                CONTRIBUTION_LEDGER,
                *HOST_MANIFESTS,
            )
        }
        completed = subprocess.run(
            [sys.executable, str(REFRESHER), "check", str(REPOSITORY)],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "source-skill-lineage-byte-clean\n")
        self.assertEqual(
            before,
            {relative: (REPOSITORY / relative).read_bytes() for relative in before},
        )


class ValidateSourceSkillLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_validator()

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.artifacts = Path(self.temporary_directory.name) / "artifacts"
        target = self.artifacts / LINEAGE_ROOT
        target.parent.mkdir(parents=True)
        shutil.copytree(REPOSITORY / LINEAGE_ROOT, target)
        report = self.artifacts / RESEARCH_REPORT
        report.parent.mkdir(parents=True)
        shutil.copy2(REPOSITORY / RESEARCH_REPORT, report)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def artifact(self, relative: Path) -> Path:
        return self.artifacts / relative

    def load(self, relative: Path) -> dict:
        return json.loads(self.artifact(relative).read_text(encoding="utf-8"))

    def write(self, relative: Path, value: object) -> None:
        self.artifact(relative).write_bytes(self.module.content_document(value))

    def rewrite_digest(self, value: dict) -> None:
        value["content_sha256"] = self.module.content_sha256(value)

    def write_source_generation(self, manifest: dict) -> None:
        self.rewrite_digest(manifest)
        raw = self.module.content_document(manifest)
        self.artifact(SOURCE_MANIFEST).write_bytes(raw)
        source_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
        for relative in (CONTRIBUTION_LEDGER, *HOST_MANIFESTS):
            document = self.load(relative)
            document["source_manifest"]["sha256"] = source_sha256
            self.rewrite_digest(document)
            self.write(relative, document)

    def run_validator(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                str(REPOSITORY),
                "--artifacts-root",
                str(self.artifacts),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )

    def assert_rejected(self, diagnostic: str) -> None:
        completed = self.run_validator()
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertIn(diagnostic, completed.stderr)

    def test_lineage_context_cleanup_preserves_primary_failure(self) -> None:
        stable_diagnostic = "source-lineage artifact tree drift"
        close_canary = "private-close-cleanup-canary"
        unlock_canary = "private-unlock-cleanup-canary"
        cases = (
            ("body-close", "failure", True, False),
            ("cancel-close", "cancellation", True, False),
            ("close-only", "success", True, False),
            ("body-unlock", "failure", False, True),
            ("cancel-unlock", "cancellation", False, True),
            ("unlock-only", "success", False, True),
        )
        real_close = self.module.os.close
        real_flock = self.module.fcntl.flock

        for case_name, body_result, fail_close, fail_unlock in cases:
            for entrypoint in ("api", "cli"):
                with self.subTest(case=case_name, entrypoint=entrypoint):
                    closed_descriptors = []
                    unlocked_descriptors = []
                    primary = (
                        self.module.LineageError("source-lineage schema drift")
                        if body_result == "failure"
                        else KeyboardInterrupt("validation cancelled")
                    )

                    def close_then_maybe_raise(
                        descriptor,
                        *,
                        closed=closed_descriptors,
                        selected=fail_close,
                    ):
                        closed.append(descriptor)
                        real_close(descriptor)
                        if selected:
                            raise OSError(close_canary)

                    def unlock_then_maybe_raise(
                        descriptor,
                        operation,
                        *,
                        unlocked=unlocked_descriptors,
                        selected=fail_unlock,
                    ):
                        result = real_flock(descriptor, operation)
                        if operation == self.module.fcntl.LOCK_UN:
                            unlocked.append(descriptor)
                            if selected:
                                raise OSError(unlock_canary)
                        return result

                    if body_result == "success":
                        body = mock.Mock(return_value={"status": "valid"})
                    else:
                        body = mock.Mock(side_effect=primary)
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    failure = None

                    with (
                        mock.patch.object(
                            self.module, "_validate_lineage_schema", body
                        ),
                        mock.patch.object(
                            self.module.os,
                            "close",
                            side_effect=close_then_maybe_raise,
                        ),
                        mock.patch.object(
                            self.module.fcntl,
                            "flock",
                            side_effect=unlock_then_maybe_raise,
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        if entrypoint == "api":
                            try:
                                self.module.validate_lineage(self.artifacts)
                            except (
                                self.module.LineageError,
                                KeyboardInterrupt,
                            ) as error:
                                failure = error
                        else:
                            with mock.patch.object(
                                sys,
                                "argv",
                                [str(VALIDATOR), str(self.artifacts)],
                            ):
                                if body_result == "cancellation":
                                    try:
                                        self.module.main()
                                    except (
                                        self.module.LineageError,
                                        KeyboardInterrupt,
                                    ) as error:
                                        failure = error
                                else:
                                    self.assertEqual(self.module.main(), 1)

                    body.assert_called_once()
                    self.assertEqual(len(closed_descriptors), 2)
                    self.assertEqual(len(set(closed_descriptors)), 2)
                    for descriptor in closed_descriptors:
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)
                    self.assertEqual(len(unlocked_descriptors), 1)
                    self.assertIn(unlocked_descriptors[0], closed_descriptors)

                    if body_result in {"failure", "cancellation"}:
                        if entrypoint == "api" or body_result == "cancellation":
                            self.assertIs(failure, primary)
                        else:
                            self.assertIsNone(failure)
                            self.assertEqual(
                                stderr.getvalue(),
                                "source-skill-lineage: source-lineage schema drift\n",
                            )
                    elif entrypoint == "api":
                        self.assertIsInstance(failure, self.module.LineageError)
                        self.assertEqual(str(failure), stable_diagnostic)
                        self.assertIsNone(failure.__cause__)
                    else:
                        self.assertIsNone(failure)
                        self.assertEqual(
                            stderr.getvalue(),
                            f"source-skill-lineage: {stable_diagnostic}\n",
                        )

                    self.assertEqual(stdout.getvalue(), "")
                    rendered = stdout.getvalue() + stderr.getvalue()
                    if failure is not primary and failure is not None:
                        rendered += str(failure)
                    for private_value in (
                        str(self.artifacts),
                        close_canary,
                        unlock_canary,
                        "Traceback",
                    ):
                        self.assertNotIn(private_value, rendered)

    def test_lineage_parent_setup_cleanup_preserves_primary_failure(self) -> None:
        stable_diagnostic = "source-lineage artifact tree drift"
        setup_canary = "private-setup-failure-canary"
        close_canary = "private-setup-close-canary"
        real_open = self.module.os.open
        real_close = self.module.os.close

        for failure_kind in ("os-error", "cancellation"):
            for entrypoint in ("api", "cli"):
                with self.subTest(failure=failure_kind, entrypoint=entrypoint):
                    opened_descriptors = []
                    closed_descriptors = []
                    primary = (
                        OSError(setup_canary, str(self.artifacts))
                        if failure_kind == "os-error"
                        else KeyboardInterrupt("validation cancelled")
                    )

                    def record_open(
                        path,
                        flags,
                        mode=0o777,
                        *,
                        dir_fd=None,
                        opened=opened_descriptors,
                    ):
                        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                        opened.append(descriptor)
                        return descriptor

                    def fail_after_open(descriptor, *, selected=primary):
                        raise selected

                    def close_then_raise(
                        descriptor,
                        *,
                        closed=closed_descriptors,
                    ):
                        closed.append(descriptor)
                        real_close(descriptor)
                        raise OSError(close_canary, str(self.artifacts))

                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    failure = None
                    with (
                        mock.patch.object(
                            self.module.os,
                            "open",
                            side_effect=record_open,
                        ),
                        mock.patch.object(
                            self.module.os,
                            "fstat",
                            side_effect=fail_after_open,
                        ),
                        mock.patch.object(
                            self.module.os,
                            "close",
                            side_effect=close_then_raise,
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        if entrypoint == "api":
                            try:
                                self.module.validate_lineage(self.artifacts)
                            except (
                                self.module.LineageError,
                                KeyboardInterrupt,
                            ) as error:
                                failure = error
                        else:
                            with mock.patch.object(
                                sys,
                                "argv",
                                [str(VALIDATOR), str(self.artifacts)],
                            ):
                                if failure_kind == "cancellation":
                                    try:
                                        self.module.main()
                                    except KeyboardInterrupt as error:
                                        failure = error
                                else:
                                    self.assertEqual(self.module.main(), 1)

                    self.assertEqual(len(opened_descriptors), 1)
                    self.assertEqual(closed_descriptors, opened_descriptors)
                    with self.assertRaises(OSError):
                        os.fstat(opened_descriptors[0])
                    if failure_kind == "cancellation":
                        self.assertIs(failure, primary)
                    elif entrypoint == "api":
                        self.assertIsInstance(failure, self.module.LineageError)
                        self.assertEqual(str(failure), stable_diagnostic)
                        self.assertIsNone(failure.__cause__)
                    else:
                        self.assertIsNone(failure)
                        self.assertEqual(
                            stderr.getvalue(),
                            f"source-skill-lineage: {stable_diagnostic}\n",
                        )
                    self.assertEqual(stdout.getvalue(), "")
                    rendered = stdout.getvalue() + stderr.getvalue()
                    if failure is not primary and failure is not None:
                        rendered += str(failure)
                    for private_value in (
                        str(self.artifacts),
                        setup_canary,
                        close_canary,
                        "Traceback",
                    ):
                        self.assertNotIn(private_value, rendered)

    def test_rejects_duplicate_and_nonfinite_json(self) -> None:
        path = self.artifact(SOURCE_MANIFEST)
        original = path.read_text(encoding="utf-8")
        duplicate_canary = "private-duplicate-key-canary"
        path.write_text(
            original.replace(
                '"schema_version": 1',
                f'"{duplicate_canary}": 1, "{duplicate_canary}": 2, '
                '"schema_version": 1',
                1,
            ),
            encoding="utf-8",
        )
        duplicate = self.run_validator()
        self.assertNotEqual(duplicate.returncode, 0, duplicate.stdout)
        self.assertIn("duplicate JSON key in source manifest", duplicate.stderr)
        self.assertNotIn(duplicate_canary, duplicate.stderr)

        path.write_text(
            original.replace(
                '"schema_version": 1', '"probe": NaN, "schema_version": 1', 1
            ),
            encoding="utf-8",
        )
        self.assert_rejected("non-finite JSON value")

    def test_public_document_schema_versions_require_exact_integers(self) -> None:
        public_artifacts = (
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        originals = {
            relative: self.artifact(relative).read_bytes()
            for relative in public_artifacts
        }
        cases = (
            (SOURCE_MANIFEST, "source manifest schema version drift"),
            (CONTRIBUTION_LEDGER, "contribution ledger schema version drift"),
            (HOST_MANIFESTS[0], "installed-host schema version drift"),
        )

        for relative, diagnostic in cases:
            for invalid in (True, 1.0):
                try:
                    for artifact, raw in originals.items():
                        self.artifact(artifact).write_bytes(raw)
                    value = self.load(relative)
                    value["schema_version"] = invalid
                    if relative == SOURCE_MANIFEST:
                        self.write_source_generation(value)
                    else:
                        self.rewrite_digest(value)
                        self.write(relative, value)

                    for entrypoint in ("api", "cli"):
                        with self.subTest(
                            relative=relative,
                            invalid=invalid,
                            entrypoint=entrypoint,
                        ):
                            before = {
                                artifact: self.artifact(artifact).read_bytes()
                                for artifact in public_artifacts
                            }
                            stdout = io.StringIO()
                            stderr = io.StringIO()
                            with (
                                contextlib.redirect_stdout(stdout),
                                contextlib.redirect_stderr(stderr),
                            ):
                                if entrypoint == "api":
                                    with self.assertRaises(
                                        self.module.LineageError
                                    ) as captured:
                                        self.module.validate_lineage(
                                            REPOSITORY,
                                            self.artifacts,
                                        )
                                    self.assertEqual(
                                        str(captured.exception), diagnostic
                                    )
                                    self.assertIsNone(captured.exception.__cause__)
                                else:
                                    with mock.patch.object(
                                        self.module.sys,
                                        "argv",
                                        [
                                            str(VALIDATOR),
                                            str(REPOSITORY),
                                            "--artifacts-root",
                                            str(self.artifacts),
                                        ],
                                    ):
                                        self.assertEqual(self.module.main(), 1)

                            self.assertEqual(stdout.getvalue(), "")
                            self.assertEqual(
                                stderr.getvalue(),
                                ""
                                if entrypoint == "api"
                                else f"source-skill-lineage: {diagnostic}\n",
                            )
                            rendered = stdout.getvalue() + stderr.getvalue()
                            for private_value in (
                                str(self.artifacts),
                                str(self.artifact(relative)),
                                "Traceback",
                            ):
                                self.assertNotIn(private_value, rendered)
                            self.assertEqual(
                                {
                                    artifact: self.artifact(artifact).read_bytes()
                                    for artifact in public_artifacts
                                },
                                before,
                            )
                finally:
                    for artifact, raw in originals.items():
                        self.artifact(artifact).write_bytes(raw)

    def test_strict_json_rejects_unpaired_surrogates_and_accepts_scalars(
        self,
    ) -> None:
        invalid = (
            b'{"\\ud800":"value"}',
            b'{"nested":["\\udc00"]}',
            b'{"value":"\\ud800x"}',
            b'{"value":"\\udc00\\ud800"}',
        )
        for raw in invalid:
            with (
                self.subTest(raw=raw),
                self.assertRaises(self.module.LineageError) as captured,
            ):
                self.module._strict_json(raw, "unicode fixture")
            self.assertEqual(
                str(captured.exception), "unicode fixture is not valid JSON"
            )
            self.assertIsNone(captured.exception.__cause__)

        valid = (
            (b'"\\ud83d\\ude00"', "😀"),
            ('"😀"'.encode(), "😀"),
            (b'"\\ud7ff\\ue000"', chr(0xD7FF) + chr(0xE000)),
            (b'"\\udbff\\udfff"', chr(0x10FFFF)),
        )
        for raw, expected in valid:
            with self.subTest(raw=raw):
                self.assertEqual(
                    self.module._strict_json(raw, "unicode fixture"), expected
                )

    def test_validation_normalizes_only_structural_document_exceptions(self) -> None:
        structural = (
            AttributeError,
            IndexError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
        )
        for exception_type in structural:
            canary = f"private-{exception_type.__name__}-canary"
            with (
                self.subTest(exception=exception_type.__name__),
                mock.patch.object(
                    self.module,
                    "_validate_lineage_view",
                    side_effect=exception_type(canary),
                ),
                self.assertRaises(self.module.LineageError) as captured,
            ):
                self.module._validate_lineage_schema(
                    REPOSITORY, mock.sentinel.lineage_view
                )
            self.assertEqual(str(captured.exception), "source-lineage schema drift")
            self.assertIsNone(captured.exception.__cause__)
            self.assertNotIn(canary, str(captured.exception))

        specific = self.module.LineageError("specific lineage diagnostic")
        with (
            mock.patch.object(
                self.module, "_validate_lineage_view", side_effect=specific
            ),
            self.assertRaises(self.module.LineageError) as captured,
        ):
            self.module._validate_lineage_schema(REPOSITORY, mock.sentinel.lineage_view)
        self.assertIs(captured.exception, specific)

        operational = OSError("private-operational-canary")
        with (
            mock.patch.object(
                self.module, "_validate_lineage_view", side_effect=operational
            ),
            self.assertRaises(OSError) as captured,
        ):
            self.module._validate_lineage_schema(REPOSITORY, mock.sentinel.lineage_view)
        self.assertIs(captured.exception, operational)

    def test_rejects_content_digest_or_unknown_field_drift(self) -> None:
        manifest = self.load(SOURCE_MANIFEST)
        manifest["content_sha256"] = "sha256:" + "0" * 64
        self.write(SOURCE_MANIFEST, manifest)
        self.assert_rejected("source manifest content digest")

        manifest = json.loads((REPOSITORY / SOURCE_MANIFEST).read_text())
        manifest["unexpected"] = True
        self.rewrite_digest(manifest)
        self.write(SOURCE_MANIFEST, manifest)
        self.assert_rejected("source manifest schema")

        shutil.copy2(REPOSITORY / SOURCE_MANIFEST, self.artifact(SOURCE_MANIFEST))
        extra = self.artifact(LINEAGE_ROOT / "private-canary-note.txt")
        extra.write_text("preserve me\n", encoding="utf-8")
        self.assert_rejected("source-lineage artifact tree drift")

    def test_rejects_calendar_invalid_utc_timestamps(self) -> None:
        self.assertEqual(
            self.module._utc("2024-02-29T23:59:59Z", "valid leap-day boundary"),
            "2024-02-29T23:59:59Z",
        )
        invalid_timestamps = (
            "2026-13-18T00:00:00Z",
            "2026-04-31T00:00:00Z",
            "2026-08-18T24:00:00Z",
            "2026-08-18T00:60:00Z",
            "2026-08-18T00:00:60Z",
            "2025-02-29T00:00:00Z",
        )
        documents = (
            (
                SOURCE_MANIFEST,
                "source manifest observation must be a UTC second timestamp",
            ),
            (
                HOST_MANIFESTS[0],
                "installed-host observation must be a UTC second timestamp",
            ),
        )
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        originals = {
            relative: self.artifact(relative).read_bytes()
            for relative in public_artifacts
        }

        for relative, diagnostic in documents:
            target = self.artifact(relative)
            for invalid_timestamp in invalid_timestamps:
                with self.subTest(document=relative, timestamp=invalid_timestamp):
                    document = json.loads(originals[relative])
                    document["observed_at_utc"] = invalid_timestamp
                    self.rewrite_digest(document)
                    self.write(relative, document)
                    injected = {
                        path: self.artifact(path).read_bytes()
                        for path in public_artifacts
                    }
                    try:
                        for entrypoint in ("api", "cli"):
                            with self.subTest(entrypoint=entrypoint):
                                if entrypoint == "api":
                                    stdout = io.StringIO()
                                    stderr = io.StringIO()
                                    with (
                                        contextlib.redirect_stdout(stdout),
                                        contextlib.redirect_stderr(stderr),
                                        self.assertRaises(
                                            self.module.LineageError
                                        ) as captured,
                                    ):
                                        self.module.validate_lineage(
                                            REPOSITORY,
                                            self.artifacts,
                                        )
                                    self.assertEqual(
                                        str(captured.exception),
                                        diagnostic,
                                    )
                                    self.assertIsNone(captured.exception.__cause__)
                                    rendered = stdout.getvalue() + stderr.getvalue()
                                    self.assertEqual(rendered, "")
                                else:
                                    completed = self.run_validator()
                                    self.assertEqual(completed.returncode, 1)
                                    self.assertEqual(completed.stdout, "")
                                    self.assertEqual(
                                        completed.stderr,
                                        f"source-skill-lineage: {diagnostic}\n",
                                    )
                                    rendered = completed.stdout + completed.stderr
                                for private_value in (
                                    str(self.artifacts),
                                    invalid_timestamp,
                                    "Traceback",
                                ):
                                    self.assertNotIn(private_value, rendered)
                                self.assertEqual(
                                    {
                                        path: self.artifact(path).read_bytes()
                                        for path in public_artifacts
                                    },
                                    injected,
                                )
                    finally:
                        target.write_bytes(originals[relative])

    def test_host_discovery_precedence_is_required_canonical_and_profile_bound(
        self,
    ) -> None:
        for relative in HOST_MANIFESTS:
            target = self.artifact(relative)
            original = target.read_bytes()
            profile_id = json.loads(original)["profile_id"]
            _, expected = EXPECTED_DISCOVERY_PRECEDENCE[profile_id]
            other_profile = next(
                candidate
                for candidate in EXPECTED_DISCOVERY_PRECEDENCE
                if candidate != profile_id
            )
            _, wrong_profile = EXPECTED_DISCOVERY_PRECEDENCE[other_profile]

            cases = (
                ("canonical", expected, None),
                ("missing", None, "installed-host manifest schema drift"),
                (
                    "extra-field",
                    {**expected, "private-extra-canary": True},
                    "installed-host discovery precedence drift",
                ),
                (
                    "resolved",
                    {"status": "resolved"},
                    "installed-host discovery precedence drift",
                ),
                (
                    "wrong-profile",
                    wrong_profile,
                    "installed-host discovery precedence drift",
                ),
            )
            for case, discovery_precedence, diagnostic in cases:
                with self.subTest(profile=profile_id, case=case):
                    host = json.loads(original)
                    if discovery_precedence is None:
                        host.pop("discovery_precedence", None)
                    else:
                        host["discovery_precedence"] = discovery_precedence
                    self.rewrite_digest(host)
                    self.write(relative, host)
                    injected = target.read_bytes()
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    try:
                        with (
                            contextlib.redirect_stdout(stdout),
                            contextlib.redirect_stderr(stderr),
                        ):
                            if diagnostic is None:
                                self.module.validate_lineage(
                                    REPOSITORY,
                                    self.artifacts,
                                )
                            else:
                                with self.assertRaises(
                                    self.module.LineageError
                                ) as captured:
                                    self.module.validate_lineage(
                                        REPOSITORY,
                                        self.artifacts,
                                    )
                                self.assertEqual(
                                    str(captured.exception),
                                    diagnostic,
                                )
                                self.assertIsNone(captured.exception.__cause__)
                        self.assertEqual(stdout.getvalue(), "")
                        self.assertEqual(stderr.getvalue(), "")
                        self.assertEqual(target.read_bytes(), injected)
                        rendered = stdout.getvalue() + stderr.getvalue()
                        for private_value in (
                            str(self.artifacts),
                            "private-extra-canary",
                            "Traceback",
                        ):
                            self.assertNotIn(private_value, rendered)
                    finally:
                        target.write_bytes(original)

    def test_rejects_missing_or_rewritten_research_report(self) -> None:
        report = self.artifact(RESEARCH_REPORT)
        report.unlink()
        self.assert_rejected("research report evidence drift")

        shutil.copy2(REPOSITORY / RESEARCH_REPORT, report)
        report.write_text(
            "# Source-skill lineage\n\nAll evidence is resolved and release eligible.\n",
            encoding="utf-8",
        )
        self.assert_rejected("research report evidence drift")

    def test_rejects_candidate_package_projection_drift(self) -> None:
        manifest = self.load(SOURCE_MANIFEST)
        package = manifest["candidate"]["packages"][0]
        package["package_tree_sha256"] = "sha256:" + "0" * 64
        manifest["candidate"]["packages_sha256"] = self.module.canonical_sha256(
            manifest["candidate"]["packages"]
        )
        self.rewrite_digest(manifest)
        self.write(SOURCE_MANIFEST, manifest)
        self.assert_rejected("candidate package tree identity")

        manifest = json.loads((REPOSITORY / SOURCE_MANIFEST).read_text())
        manifest["candidate"]["basis"]["commit_sha1"] = "0" * 40
        self.rewrite_digest(manifest)
        self.write(SOURCE_MANIFEST, manifest)
        self.assert_rejected("candidate basis identity")

        manifest = json.loads((REPOSITORY / SOURCE_MANIFEST).read_text())
        manifest["candidate"]["packages"][0]["git_tree_sha1"] = "0" * 40
        manifest["candidate"]["packages_sha256"] = self.module.canonical_sha256(
            manifest["candidate"]["packages"]
        )
        self.rewrite_digest(manifest)
        self.write(SOURCE_MANIFEST, manifest)
        self.assert_rejected("candidate package Git tree identity")

    def _assert_tree_capture_limit(
        self,
        root: Path,
        limits: dict[str, int],
        unread: Path | None = None,
    ) -> None:
        path_type = type(root)
        original_read_bytes = path_type.read_bytes

        def reject_unread(path):
            if unread is not None and path == unread:
                raise AssertionError("over-limit tree content was read unbounded")
            return original_read_bytes(path)

        with contextlib.ExitStack() as stack:
            for name, value in limits.items():
                stack.enter_context(
                    mock.patch.object(self.module, name, value, create=True)
                )
            if unread is not None:
                stack.enter_context(
                    mock.patch.object(
                        path_type,
                        "read_bytes",
                        autospec=True,
                        side_effect=reject_unread,
                    )
                )
            with self.assertRaises(self.module.LineageError) as captured:
                self.module.tree_identity(root)

        self.assertEqual(str(captured.exception), "tree exceeds capture limits")
        self.assertIsNone(captured.exception.__cause__)
        rendered = "".join(
            traceback.format_exception(
                type(captured.exception),
                captured.exception,
                captured.exception.__traceback__,
            )
        )
        self.assertNotIn(str(root), rendered)
        self.assertNotIn(root.name, rendered)

    def test_tree_identity_stops_lazy_enumeration_at_entry_limit(self) -> None:
        root = Path(self.temporary_directory.name) / "tree-entry-limit-canary"
        root.mkdir()
        first = root / "a"
        second = root / "b"
        first.write_bytes(b"a")
        second.write_bytes(b"b")
        entries = sorted(os.scandir(root), key=lambda item: item.name)
        root_metadata = root.stat()
        root_identity = (root_metadata.st_dev, root_metadata.st_ino)
        original_scandir = self.module.os.scandir
        original_open = self.module.os.open
        consumed = 0
        overreads = 0

        class GuardedScandir:
            def __init__(self):
                self.index = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return self

            def __next__(self):
                nonlocal consumed, overreads
                if self.index >= len(entries):
                    overreads += 1
                    raise AssertionError("tree enumeration exceeded limit plus one")
                entry = entries[self.index]
                self.index += 1
                consumed += 1
                return entry

        def guarded_scandir(path):
            metadata = os.fstat(path) if isinstance(path, int) else Path(path).stat()
            if (metadata.st_dev, metadata.st_ino) == root_identity:
                return GuardedScandir()
            return original_scandir(path)

        def reject_second_open(path, flags, *args, **kwargs):
            directory_descriptor = kwargs.get("dir_fd")
            if directory_descriptor is not None:
                directory = os.fstat(directory_descriptor)
                if (
                    path == second.name
                    and (directory.st_dev, directory.st_ino) == root_identity
                ):
                    raise AssertionError("over-limit tree entry was opened")
            return original_open(path, flags, *args, **kwargs)

        limits = {
            "MAX_TREE_ENTRIES": 1,
            "MAX_TREE_LISTING_BYTES": 64,
            "MAX_TREE_PATH_BYTES": 64,
            "MAX_TREE_COMPONENT_BYTES": 64,
            "MAX_TREE_COMPONENTS": 8,
            "MAX_TREE_TOTAL_COMPONENTS": 16,
            "MAX_BLOB_BYTES": 64,
            "MAX_MATERIALIZED_BYTES": 64,
            "MAX_SYMLINK_BYTES": 64,
        }
        with (
            mock.patch.object(
                self.module.os,
                "scandir",
                side_effect=guarded_scandir,
            ),
            mock.patch.object(
                self.module.os,
                "open",
                side_effect=reject_second_open,
            ),
        ):
            self._assert_tree_capture_limit(root, limits, unread=second)

        self.assertLessEqual(consumed, 2)
        self.assertEqual(overreads, 0)

    def test_tree_identity_enforces_structural_limits_before_read(self) -> None:
        base_limits = {
            "MAX_TREE_ENTRIES": 32,
            "MAX_TREE_LISTING_BYTES": 256,
            "MAX_TREE_PATH_BYTES": 128,
            "MAX_TREE_COMPONENT_BYTES": 64,
            "MAX_TREE_COMPONENTS": 8,
            "MAX_TREE_TOTAL_COMPONENTS": 128,
            "MAX_BLOB_BYTES": 64,
            "MAX_MATERIALIZED_BYTES": 64,
            "MAX_SYMLINK_BYTES": 64,
        }
        cases = (
            ("path", ("123456789",), "123456789", {"MAX_TREE_PATH_BYTES": 8}),
            (
                "component",
                ("123456789",),
                "123456789",
                {"MAX_TREE_COMPONENT_BYTES": 8},
            ),
            (
                "depth",
                ("a/b/c",),
                "a/b/c",
                {"MAX_TREE_COMPONENTS": 2},
            ),
            (
                "total-components",
                ("a", "b"),
                None,
                {"MAX_TREE_TOTAL_COMPONENTS": 1},
            ),
        )
        for label, relatives, unread_relative, override in cases:
            with self.subTest(limit=label):
                root = (
                    Path(self.temporary_directory.name) / f"tree-{label}-limit-canary"
                )
                root.mkdir()
                for relative in relatives:
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"x")
                limits = dict(base_limits)
                limits.update(override)
                unread = root / unread_relative if unread_relative is not None else None
                self._assert_tree_capture_limit(root, limits, unread)

    def test_tree_identity_enforces_file_aggregate_and_symlink_limits(self) -> None:
        base_limits = {
            "MAX_TREE_ENTRIES": 16,
            "MAX_TREE_LISTING_BYTES": 256,
            "MAX_TREE_PATH_BYTES": 128,
            "MAX_TREE_COMPONENT_BYTES": 64,
            "MAX_TREE_COMPONENTS": 8,
            "MAX_TREE_TOTAL_COMPONENTS": 64,
            "MAX_BLOB_BYTES": 64,
            "MAX_MATERIALIZED_BYTES": 64,
            "MAX_SYMLINK_BYTES": 64,
        }
        for label in ("file", "aggregate", "symlink"):
            with self.subTest(limit=label):
                root = (
                    Path(self.temporary_directory.name) / f"tree-{label}-limit-canary"
                )
                root.mkdir()
                limits = dict(base_limits)
                unread = None
                if label == "file":
                    unread = root / "payload"
                    unread.write_bytes(b"12345")
                    limits["MAX_BLOB_BYTES"] = 4
                elif label == "aggregate":
                    (root / "a").write_bytes(b"aaa")
                    (root / "b").write_bytes(b"bbb")
                    limits["MAX_BLOB_BYTES"] = 3
                    limits["MAX_MATERIALIZED_BYTES"] = 5
                else:
                    (root / "target").write_bytes(b"")
                    (root / "link").symlink_to("target")
                    limits["MAX_SYMLINK_BYTES"] = 5
                self._assert_tree_capture_limit(root, limits, unread)

    def test_tree_identity_streams_exact_limit_with_stable_identity(self) -> None:
        root = Path(self.temporary_directory.name) / "tree-exact-limit"
        root.mkdir()
        payload = root / "payload.bin"
        payload.write_bytes(b"abc")
        payload.chmod(0o644)
        metadata = payload.stat()
        identity = (metadata.st_dev, metadata.st_ino)
        path_type = type(root)
        original_read_bytes = path_type.read_bytes
        original_read = self.module.os.read
        reads = []

        def reject_read_bytes(path):
            if path == payload:
                raise AssertionError("exact-limit tree content was read unbounded")
            return original_read_bytes(path)

        def record_read(descriptor, count):
            observed = os.fstat(descriptor)
            if (observed.st_dev, observed.st_ino) == identity:
                reads.append(count)
            return original_read(descriptor, count)

        limits = {
            "MAX_TREE_ENTRIES": 1,
            "MAX_TREE_LISTING_BYTES": 11,
            "MAX_TREE_PATH_BYTES": 11,
            "MAX_TREE_COMPONENT_BYTES": 11,
            "MAX_TREE_COMPONENTS": 1,
            "MAX_TREE_TOTAL_COMPONENTS": 1,
            "MAX_BLOB_BYTES": 3,
            "MAX_MATERIALIZED_BYTES": 3,
            "MAX_SYMLINK_BYTES": 64,
            "MAX_PROCESS_READ_BYTES": 2,
        }
        with contextlib.ExitStack() as stack:
            for name, value in limits.items():
                stack.enter_context(
                    mock.patch.object(self.module, name, value, create=True)
                )
            stack.enter_context(
                mock.patch.object(
                    path_type,
                    "read_bytes",
                    autospec=True,
                    side_effect=reject_read_bytes,
                )
            )
            stack.enter_context(
                mock.patch.object(self.module.os, "read", side_effect=record_read)
            )
            captured = self.module.tree_identity(root)

        self.assertGreaterEqual(len(reads), 2)
        self.assertTrue(all(count <= 2 for count in reads))
        self.assertEqual(
            captured,
            {
                "entry_count": 1,
                "total_bytes": 3,
                "tree_sha256": (
                    "sha256:d0dcaefa677c8392b8cb0fe09bec3d4e105fa7f70fdf0f9f0f6a828bc72654d5"
                ),
            },
        )

    def test_tree_identity_uses_one_absolute_deadline(self) -> None:
        root = Path(self.temporary_directory.name) / "tree-deadline-canary"
        root.mkdir()
        payload = root / "payload"
        payload.write_bytes(b"x")
        metadata = payload.stat()
        identity = (metadata.st_dev, metadata.st_ino)
        path_type = type(root)
        original_read_bytes = path_type.read_bytes
        original_read = self.module.os.read
        clock = {"value": 0.0}
        reads = 0

        def reject_read_bytes(path):
            if path == payload:
                raise AssertionError("deadline-bound tree was read unbounded")
            return original_read_bytes(path)

        def expire_after_first_read(descriptor, count):
            nonlocal reads
            observed = os.fstat(descriptor)
            chunk = original_read(descriptor, count)
            if (observed.st_dev, observed.st_ino) == identity:
                reads += 1
                if reads > 1:
                    raise AssertionError("tree read continued after deadline")
                clock["value"] = 31.0
            return chunk

        with (
            mock.patch.object(
                self.module,
                "MATERIALIZE_TIMEOUT_SECONDS",
                30,
                create=True,
            ),
            mock.patch.object(time, "monotonic", side_effect=lambda: clock["value"]),
            mock.patch.object(
                path_type,
                "read_bytes",
                autospec=True,
                side_effect=reject_read_bytes,
            ),
            mock.patch.object(
                self.module.os,
                "read",
                side_effect=expire_after_first_read,
            ),
            self.assertRaises(self.module.LineageError) as captured,
        ):
            self.module.tree_identity(root)

        self.assertEqual(reads, 1)
        self.assertEqual(str(captured.exception), "tree exceeds capture limits")
        self.assertIsNone(captured.exception.__cause__)
        self.assertNotIn(str(root), str(captured.exception))
        self.assertNotIn(root.name, str(captured.exception))

    def test_tree_identity_rejects_intermediate_directory_replacement(self) -> None:
        temporary = Path(self.temporary_directory.name)
        root = temporary / "tree-directory-race"
        nested = root / "nested"
        nested.mkdir(parents=True)
        benign = nested / "benign.txt"
        benign.write_bytes(b"benign")
        canary = "outside-tree-race-canary"
        outside = temporary / "outside"
        outside.mkdir()
        outside_file = outside / benign.name
        outside_file.write_bytes(canary.encode("utf-8"))
        displaced = temporary / "displaced-nested"
        replacement = temporary / "replacement-link"
        replacement.symlink_to(outside, target_is_directory=True)
        root_metadata = root.stat()
        root_identity = (root_metadata.st_dev, root_metadata.st_ino)
        outside_metadata = outside.stat()
        outside_identity = (outside_metadata.st_dev, outside_metadata.st_ino)
        path_type = type(root)
        original_lstat = path_type.lstat
        original_read_bytes = path_type.read_bytes
        original_stat = self.module.os.stat
        original_open = self.module.os.open
        swapped = []

        def replace_nested():
            nested.rename(displaced)
            replacement.replace(nested)
            swapped.append(True)

        def swapping_lstat(path):
            observed = original_lstat(path)
            if path == nested and not swapped:
                replace_nested()
            return observed

        def swapping_stat(path, *args, **kwargs):
            observed = original_stat(path, *args, **kwargs)
            directory_descriptor = kwargs.get("dir_fd")
            if directory_descriptor is not None:
                directory = os.fstat(directory_descriptor)
            if (
                path == nested.name
                and directory_descriptor is not None
                and (directory.st_dev, directory.st_ino) == root_identity
                and not swapped
            ):
                replace_nested()
            return observed

        def reject_outside_read(path):
            if path == root / "nested" / benign.name:
                raise AssertionError("outside tree canary was read unbounded")
            return original_read_bytes(path)

        def reject_outside_open(path, flags, *args, **kwargs):
            directory_descriptor = kwargs.get("dir_fd")
            if directory_descriptor is not None:
                directory = os.fstat(directory_descriptor)
            if (
                Path(path).name == outside_file.name
                and directory_descriptor is not None
                and (directory.st_dev, directory.st_ino) == outside_identity
            ):
                raise AssertionError("outside tree canary was opened")
            return original_open(path, flags, *args, **kwargs)

        limits = {
            "MAX_TREE_ENTRIES": 16,
            "MAX_TREE_LISTING_BYTES": 256,
            "MAX_TREE_PATH_BYTES": 128,
            "MAX_TREE_COMPONENT_BYTES": 64,
            "MAX_TREE_COMPONENTS": 8,
            "MAX_TREE_TOTAL_COMPONENTS": 64,
            "MAX_BLOB_BYTES": 64,
            "MAX_MATERIALIZED_BYTES": 64,
            "MAX_SYMLINK_BYTES": 64,
        }
        with contextlib.ExitStack() as stack:
            for name, value in limits.items():
                stack.enter_context(
                    mock.patch.object(self.module, name, value, create=True)
                )
            stack.enter_context(
                mock.patch.object(
                    path_type,
                    "lstat",
                    autospec=True,
                    side_effect=swapping_lstat,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    path_type,
                    "read_bytes",
                    autospec=True,
                    side_effect=reject_outside_read,
                )
            )
            stack.enter_context(
                mock.patch.object(self.module.os, "stat", side_effect=swapping_stat)
            )
            stack.enter_context(
                mock.patch.object(
                    self.module.os,
                    "open",
                    side_effect=reject_outside_open,
                )
            )
            with self.assertRaises(self.module.LineageError) as captured:
                self.module.tree_identity(root)

        self.assertEqual(swapped, [True])
        self.assertEqual(str(captured.exception), "tree entry cannot be observed")
        self.assertIsNone(captured.exception.__cause__)
        rendered = "".join(
            traceback.format_exception(
                type(captured.exception),
                captured.exception,
                captured.exception.__traceback__,
            )
        )
        self.assertNotIn(str(temporary), rendered)
        self.assertNotIn(canary, rendered)

    def _assert_validation_capture_limit(self, operation, *private_values: str) -> None:
        with self.assertRaises(self.module.LineageError) as captured:
            operation()

        self.assertEqual(
            str(captured.exception), "source-lineage validation exceeds limits"
        )
        self.assertIsNone(captured.exception.__cause__)
        rendered = "".join(
            traceback.format_exception(
                type(captured.exception),
                captured.exception,
                captured.exception.__traceback__,
            )
        )
        for private_value in private_values:
            self.assertNotIn(private_value, rendered)

    def test_validation_deadline_covers_the_final_host_phase(self) -> None:
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        before = {
            relative: self.artifact(relative).read_bytes()
            for relative in public_artifacts
        }
        diagnostic = self.module.VALIDATION_LIMIT_DIAGNOSTIC
        final_profile = next(reversed(self.module.HOST_MANIFESTS))
        real_new_budget = self.module._new_validation_budget
        real_validate_host = self.module.validate_host_manifest
        canary = "private-final-host-deadline-canary"

        for entrypoint in ("api", "cli"):
            with self.subTest(entrypoint=entrypoint):
                clock = {"value": 0.0}
                created_budgets = []
                host_budgets = []

                def new_budget(*, created_budgets=created_budgets):
                    budget = real_new_budget()
                    created_budgets.append(budget)
                    return budget

                def validate_host_with_expiry(
                    value,
                    profile_id,
                    source_raw,
                    sources,
                    *,
                    budget=None,
                    clock=clock,
                    created_budgets=created_budgets,
                    host_budgets=host_budgets,
                ):
                    host_budgets.append(budget)
                    arguments = {} if budget is None else {"budget": budget}
                    result = real_validate_host(
                        value,
                        profile_id,
                        source_raw,
                        sources,
                        **arguments,
                    )
                    if profile_id == final_profile:
                        clock["value"] = created_budgets[0].deadline
                    return result

                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        self.module.time,
                        "monotonic",
                        side_effect=lambda clock=clock: clock["value"],
                    ),
                    mock.patch.object(
                        self.module,
                        "_new_validation_budget",
                        side_effect=new_budget,
                    ) as budget_factory,
                    mock.patch.object(
                        self.module,
                        "validate_host_manifest",
                        side_effect=validate_host_with_expiry,
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    if entrypoint == "api":
                        with self.assertRaises(self.module.LineageError) as captured:
                            self.module.validate_lineage(REPOSITORY, self.artifacts)
                        self.assertEqual(str(captured.exception), diagnostic)
                        self.assertIsNone(captured.exception.__cause__)
                    else:
                        with mock.patch.object(
                            self.module.sys,
                            "argv",
                            [
                                str(VALIDATOR),
                                str(REPOSITORY),
                                "--artifacts-root",
                                str(self.artifacts),
                            ],
                        ):
                            self.assertEqual(self.module.main(), 1)

                self.assertEqual(budget_factory.call_count, 1)
                self.assertEqual(len(created_budgets), 1)
                self.assertEqual(len(host_budgets), len(self.module.HOST_MANIFESTS))
                self.assertTrue(
                    all(
                        budget is created_budgets[0] and budget is not None
                        for budget in host_budgets
                    )
                )
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    ""
                    if entrypoint == "api"
                    else f"source-skill-lineage: {diagnostic}\n",
                )
                rendered = stdout.getvalue() + stderr.getvalue()
                for private_value in (
                    str(self.artifacts),
                    canary,
                    "Traceback",
                ):
                    self.assertNotIn(private_value, rendered)
                self.assertEqual(
                    {
                        relative: self.artifact(relative).read_bytes()
                        for relative in public_artifacts
                    },
                    before,
                )

    def test_unresolved_contribution_frontier_includes_semantic_drift(self) -> None:
        ledger = self.load(CONTRIBUTION_LEDGER)
        expected = expected_unresolved_contribution_ids(REPOSITORY)
        mapped_semantic_unresolved = tuple(
            contribution["id"]
            for contribution in ledger["contributions"]
            if contribution["mapping_status"] == "mapped"
            and contribution["semantic_drift"]["status"] == "unresolved"
        )
        self.assertEqual(
            mapped_semantic_unresolved,
            EXPECTED_MAPPED_SEMANTIC_UNRESOLVED_CONTRIBUTION_IDS,
        )

        summary = self.module.validate_lineage(REPOSITORY, self.artifacts)
        self.assertEqual(summary["unresolved_contribution_ids"], expected)

        source_raw = (REPOSITORY / SOURCE_MANIFEST).read_bytes()
        source = json.loads(source_raw)
        sources = {item["id"]: item for item in source["sources"]}
        target_id = EXPECTED_MAPPED_SEMANTIC_UNRESOLVED_CONTRIBUTION_IDS[0]
        target = next(
            contribution
            for contribution in ledger["contributions"]
            if contribution["id"] == target_id
        )
        target["semantic_drift"]["status"] = "unchanged"
        sources[target["source_id"]]["byte_drift"]["status"] = "unchanged"
        self.rewrite_digest(ledger)
        inventory_sha256 = self.module.canonical_sha256(ledger["contributions"])
        with mock.patch.object(
            self.module,
            "CONTRIBUTION_EVIDENCE_INVENTORY_SHA256",
            inventory_sha256,
        ):
            toggled = self.module.validate_contribution_ledger(
                REPOSITORY,
                ledger,
                source_raw,
                sources,
            )
        self.assertEqual(toggled, set(expected) - {target_id})

    def test_validation_rejects_oversize_resources_before_read(self) -> None:
        manifest = self.load(SOURCE_MANIFEST)
        candidate = manifest["candidate"]
        package = candidate["packages"][0]
        plugin_manifest = REPOSITORY / package["plugin_root"] / "plugin.json"
        identity_artifact = REPOSITORY / package["identity_artifacts"][0]["path"]
        cases = (
            (
                "report",
                self.artifact(RESEARCH_REPORT),
                lambda: self.module.validate_lineage(
                    REPOSITORY, self.artifacts, acquire_lock=False
                ),
                False,
            ),
            (
                "lineage-json",
                self.artifact(SOURCE_MANIFEST),
                lambda: self.module.validate_lineage(
                    REPOSITORY, self.artifacts, acquire_lock=False
                ),
                False,
            ),
            (
                "plugin-manifest",
                plugin_manifest,
                lambda: self.module._validate_candidate(REPOSITORY, candidate),
                True,
            ),
            (
                "identity-artifact",
                identity_artifact,
                lambda: self.module._validate_candidate(REPOSITORY, candidate),
                True,
            ),
        )
        path_type = type(REPOSITORY)
        original_read_bytes = path_type.read_bytes
        original_read = self.module.os.read

        def expected_tree(root, *_args, **_kwargs):
            relative = Path(root).relative_to(REPOSITORY).as_posix()
            expected = next(
                item
                for item in candidate["packages"]
                if item["plugin_root"] == relative
            )
            return {
                "entry_count": expected["entry_count"],
                "total_bytes": expected["total_bytes"],
                "tree_sha256": expected["package_tree_sha256"],
            }

        for label, target, operation, skip_tree in cases:
            with self.subTest(resource=label):
                metadata = target.stat()
                target_identity = (metadata.st_dev, metadata.st_ino)
                canary = f"private-{label}-read-canary"

                def reject_path_read(path, expected=target, message=canary):
                    if path == expected:
                        raise AssertionError(message)
                    return original_read_bytes(path)

                def reject_descriptor_read(
                    descriptor,
                    count,
                    expected=target_identity,
                    message=canary,
                ):
                    opened = os.fstat(descriptor)
                    if (opened.st_dev, opened.st_ino) == expected:
                        raise AssertionError(message)
                    return original_read(descriptor, count)

                with (
                    mock.patch.object(
                        self.module,
                        "MAX_VALIDATION_FILE_BYTES",
                        metadata.st_size - 1,
                        create=True,
                    ),
                    mock.patch.object(
                        path_type,
                        "read_bytes",
                        autospec=True,
                        side_effect=reject_path_read,
                    ),
                    mock.patch.object(
                        self.module.os,
                        "read",
                        side_effect=reject_descriptor_read,
                    ),
                    mock.patch.object(
                        self.module,
                        "tree_identity",
                        side_effect=expected_tree,
                    )
                    if skip_tree
                    else contextlib.nullcontext(),
                ):
                    self._assert_validation_capture_limit(
                        operation, str(target), target.name, canary
                    )

    def test_validation_streams_an_exact_limit_file(self) -> None:
        root = Path(self.temporary_directory.name) / "exact-validation-file"
        root.mkdir()
        payload = root / "payload"
        raw = b"abc"
        payload.write_bytes(raw)
        payload.chmod(0o644)
        metadata = payload.stat()
        payload_identity = (metadata.st_dev, metadata.st_ino)
        directory_descriptor = os.open(
            root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        original_read = self.module.os.read
        requests = []

        def record_bounded_read(descriptor, count):
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) == payload_identity:
                requests.append(count)
            return original_read(descriptor, count)

        try:
            with (
                mock.patch.object(
                    self.module,
                    "MAX_VALIDATION_FILE_BYTES",
                    len(raw),
                    create=True,
                ),
                mock.patch.object(
                    self.module, "MAX_PROCESS_READ_BYTES", 2, create=True
                ),
                mock.patch.object(
                    self.module.os, "read", side_effect=record_bounded_read
                ),
                mock.patch.object(
                    type(payload),
                    "read_bytes",
                    autospec=True,
                    side_effect=AssertionError(
                        "exact-limit validation file was read unbounded"
                    ),
                ),
            ):
                observed, mode = self.module._read_regular_file_at(
                    directory_descriptor, payload.name
                )
        finally:
            os.close(directory_descriptor)

        self.assertEqual((observed, mode), (raw, "100644"))
        self.assertGreaterEqual(len(requests), 2)
        self.assertTrue(all(count <= 2 for count in requests), requests)

    def test_validation_stops_wide_lineage_enumeration_at_entry_limit(self) -> None:
        root = Path(self.temporary_directory.name) / "wide-lineage-private-canary"
        root.mkdir()
        (root / "a").mkdir()
        (root / "b").mkdir()
        entries = sorted(os.scandir(root), key=lambda item: item.name)
        root_metadata = root.stat()
        root_identity = (root_metadata.st_dev, root_metadata.st_ino)
        original_scandir = self.module.os.scandir
        original_listdir = self.module.os.listdir
        consumed = 0
        overreads = 0

        class GuardedScandir:
            def __init__(self):
                self.index = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return self

            def __next__(self):
                nonlocal consumed, overreads
                if self.index >= len(entries):
                    overreads += 1
                    raise AssertionError("lineage enumeration exceeded limit plus one")
                entry = entries[self.index]
                self.index += 1
                consumed += 1
                return entry

        def guarded_scandir(path):
            metadata = os.fstat(path) if isinstance(path, int) else Path(path).stat()
            if (metadata.st_dev, metadata.st_ino) == root_identity:
                return GuardedScandir()
            return original_scandir(path)

        def reject_prebuffered_listdir(path):
            metadata = os.fstat(path) if isinstance(path, int) else Path(path).stat()
            if (metadata.st_dev, metadata.st_ino) == root_identity:
                raise AssertionError("lineage directory was prebuffered")
            return original_listdir(path)

        directory_descriptor = os.open(
            root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            with (
                mock.patch.object(
                    self.module,
                    "MAX_VALIDATION_TREE_ENTRIES",
                    1,
                    create=True,
                ),
                mock.patch.object(
                    self.module.os, "scandir", side_effect=guarded_scandir
                ),
                mock.patch.object(
                    self.module.os,
                    "listdir",
                    side_effect=reject_prebuffered_listdir,
                ),
            ):
                self._assert_validation_capture_limit(
                    lambda: self.module._lineage_snapshot_descriptor(
                        directory_descriptor
                    ),
                    str(root),
                    root.name,
                    "lineage enumeration exceeded limit plus one",
                )
        finally:
            os.close(directory_descriptor)

        self.assertLessEqual(consumed, 2)
        self.assertEqual(overreads, 0)

    def test_validation_accepts_exact_lineage_entry_limit(self) -> None:
        expected = self.module.validate_lineage(
            REPOSITORY, self.artifacts, acquire_lock=False
        )
        exact_entry_count = len(self.module.LINEAGE_TREE)

        with mock.patch.object(
            self.module,
            "MAX_VALIDATION_TREE_ENTRIES",
            exact_entry_count,
        ):
            observed = self.module.validate_lineage(
                REPOSITORY, self.artifacts, acquire_lock=False
            )

        self.assertEqual(observed, expected)

        with mock.patch.object(
            self.module,
            "MAX_VALIDATION_TREE_ENTRIES",
            exact_entry_count - 1,
        ):
            self._assert_validation_capture_limit(
                lambda: self.module.validate_lineage(
                    REPOSITORY, self.artifacts, acquire_lock=False
                )
            )

    def test_tree_identity_is_order_independent_and_mode_sensitive(self) -> None:
        first = Path(self.temporary_directory.name) / "first"
        second = Path(self.temporary_directory.name) / "second"
        first.mkdir()
        second.mkdir()
        for root in (first, second):
            (root / "nested").mkdir()
            (root / "nested" / "b").write_bytes(b"bravo")
            (root / "a").write_bytes(b"alpha")
        self.assertEqual(
            self.module.tree_identity(first), self.module.tree_identity(second)
        )

        (second / "a").chmod(0o755)
        self.assertNotEqual(
            self.module.tree_identity(first), self.module.tree_identity(second)
        )

    def test_tree_identity_binds_safe_symlink_and_rejects_escape_or_special(
        self,
    ) -> None:
        root = Path(self.temporary_directory.name) / "unsafe-tree"
        root.mkdir()
        (root / "target").write_text("target", encoding="utf-8")
        (root / "link").symlink_to("target")
        safe = self.module.tree_identity(root)
        (root / "link").unlink()
        (root / "link").symlink_to("./target")
        self.assertNotEqual(safe, self.module.tree_identity(root))
        (root / "link").unlink()
        (root / "link").symlink_to("../private-canary")
        with self.assertRaisesRegex(self.module.LineageError, "symlink escapes"):
            self.module.tree_identity(root)
        (root / "link").unlink()
        fifo = root / "fifo"
        os.mkfifo(fifo)
        self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode))
        with self.assertRaisesRegex(self.module.LineageError, "special"):
            self.module.tree_identity(root)

    def test_rejects_unsafe_or_noncanonical_source_authority(self) -> None:
        for repository_url in (
            "https://user@example.com/owner/repo",
            "https://example.com/owner/repo?token=private-canary",
            "https://example.com/owner/repo#private-canary",
            "ssh://git@example.com/owner/repo",
            "file:///private/private-canary",
        ):
            with self.subTest(repository_url=repository_url):
                manifest = self.load(SOURCE_MANIFEST)
                source = next(
                    item
                    for item in manifest["sources"]
                    if item["authority"]["kind"] == "git"
                )
                source["authority"]["repository_url"] = repository_url
                self.rewrite_digest(manifest)
                self.write(SOURCE_MANIFEST, manifest)
                self.assert_rejected("source repository URL")
                shutil.copy2(
                    REPOSITORY / SOURCE_MANIFEST, self.artifact(SOURCE_MANIFEST)
                )

    def test_rejects_byte_drift_claim_inconsistent_with_snapshots(self) -> None:
        manifest = self.load(SOURCE_MANIFEST)
        source = next(
            item
            for item in manifest["sources"]
            if item["baseline"]["status"] == "resolved"
            and item["current"]["status"] == "resolved"
        )
        expected = (
            "unchanged" if source["byte_drift"]["status"] == "changed" else "changed"
        )
        source["byte_drift"]["status"] = expected
        self.rewrite_digest(manifest)
        self.write(SOURCE_MANIFEST, manifest)
        self.assert_rejected("source byte drift")

    def test_rejects_source_evidence_inventory_drift(self) -> None:
        manifest = self.load(SOURCE_MANIFEST)
        source = next(item for item in manifest["sources"] if item["skill_ids"])
        source["skill_ids"][0] = "private-canary-skill"
        self.write_source_generation(manifest)
        self.assert_rejected("source evidence inventory drift")

        shutil.copytree(
            REPOSITORY / LINEAGE_ROOT,
            self.artifact(LINEAGE_ROOT),
            dirs_exist_ok=True,
        )
        manifest = self.load(SOURCE_MANIFEST)
        source = next(
            item for item in manifest["sources"] if item["authority"]["kind"] == "git"
        )
        source["authority"]["refresh_ref"] = "private-canary-ref"
        self.write_source_generation(manifest)
        self.assert_rejected("source evidence inventory drift")

        shutil.copytree(
            REPOSITORY / LINEAGE_ROOT,
            self.artifact(LINEAGE_ROOT),
            dirs_exist_ok=True,
        )
        manifest = self.load(SOURCE_MANIFEST)
        source = next(
            item
            for item in manifest["sources"]
            if item["baseline"]["status"] == "resolved"
            and item["authority"]["kind"] == "git"
        )
        source["baseline"]["commit_sha1"] = "0" * 40
        self.write_source_generation(manifest)
        self.assert_rejected("source evidence inventory drift")

        shutil.copytree(
            REPOSITORY / LINEAGE_ROOT,
            self.artifact(LINEAGE_ROOT),
            dirs_exist_ok=True,
        )
        manifest = self.load(SOURCE_MANIFEST)
        source = next(
            item
            for item in manifest["sources"]
            if item["baseline"]["status"] == "unresolved"
            and item["current"]["status"] == "resolved"
            and item["current"]["license"]["status"] == "resolved"
        )
        source["baseline"] = json.loads(json.dumps(source["current"]))
        source["byte_drift"] = {
            "status": "unchanged",
            "summary": "The fabricated baseline matches the current source bytes.",
        }
        self.write_source_generation(manifest)
        self.assert_rejected("source evidence inventory drift")

    def test_rejects_contribution_evidence_inventory_drift(self) -> None:
        ledger = self.load(CONTRIBUTION_LEDGER)
        ledger["contributions"] = [
            item
            for item in ledger["contributions"]
            if item["id"] != "mergecraft-installed-source-relationship-unresolved"
        ]
        self.rewrite_digest(ledger)
        self.write(CONTRIBUTION_LEDGER, ledger)
        self.assert_rejected("contribution evidence inventory drift")

        shutil.copy2(
            REPOSITORY / CONTRIBUTION_LEDGER, self.artifact(CONTRIBUTION_LEDGER)
        )
        ledger = self.load(CONTRIBUTION_LEDGER)
        added = dict(ledger["contributions"][0])
        added["id"] = "unexpected-contribution"
        ledger["contributions"].append(added)
        ledger["contributions"].sort(key=lambda item: item["id"])
        self.rewrite_digest(ledger)
        self.write(CONTRIBUTION_LEDGER, ledger)
        self.assert_rejected("contribution evidence inventory drift")

        shutil.copy2(
            REPOSITORY / CONTRIBUTION_LEDGER, self.artifact(CONTRIBUTION_LEDGER)
        )
        ledger = self.load(CONTRIBUTION_LEDGER)
        contribution = next(
            item
            for item in ledger["contributions"]
            if item["id"] == "mergecraft-installed-source-relationship-unresolved"
        )
        contribution.clear()
        contribution.update(
            {
                "behavior": "Mapped installed Mergecraft relationship",
                "destination": {
                    "distribution_id": "mergecraft",
                    "evidence_paths": ["plugins/mergecraft/plugin.json"],
                    "owner_id": "mergecraft",
                },
                "historical_relationship": "input",
                "id": "mergecraft-installed-source-relationship-unresolved",
                "mapping_status": "mapped",
                "semantic_drift": {
                    "status": "changed",
                    "summary": "A candidate mapping was asserted without evidence.",
                },
                "source_id": "ivan-mergecraft",
            }
        )
        self.rewrite_digest(ledger)
        self.write(CONTRIBUTION_LEDGER, ledger)
        self.assert_rejected("contribution evidence inventory drift")

        shutil.copy2(
            REPOSITORY / CONTRIBUTION_LEDGER, self.artifact(CONTRIBUTION_LEDGER)
        )
        ledger = self.load(CONTRIBUTION_LEDGER)
        contribution = next(
            item
            for item in ledger["contributions"]
            if item["id"] == "mergecraft-installed-source-relationship-unresolved"
        )
        contribution["source_id"] = "ivan-rolecasting"
        self.rewrite_digest(ledger)
        self.write(CONTRIBUTION_LEDGER, ledger)
        self.assert_rejected("contribution evidence inventory drift")

        shutil.copy2(
            REPOSITORY / CONTRIBUTION_LEDGER, self.artifact(CONTRIBUTION_LEDGER)
        )
        ledger = self.load(CONTRIBUTION_LEDGER)
        contribution = ledger["contributions"][0]
        contribution["behavior"] = "A fabricated contribution behavior."
        self.rewrite_digest(ledger)
        self.write(CONTRIBUTION_LEDGER, ledger)
        self.assert_rejected("contribution evidence inventory drift")

    def test_rejects_invalid_resolved_or_unresolved_license(self) -> None:
        manifest = self.load(SOURCE_MANIFEST)
        source = next(
            item
            for item in manifest["sources"]
            if item["current"]["status"] == "resolved"
            and item["current"]["license"]["status"] == "resolved"
        )
        source["current"]["license"]["spdx_expression"] = ""
        self.rewrite_digest(manifest)
        self.write(SOURCE_MANIFEST, manifest)
        self.assert_rejected("resolved license")

        manifest = json.loads((REPOSITORY / SOURCE_MANIFEST).read_text())
        source = next(
            item
            for item in manifest["sources"]
            if item["current"]["status"] == "unresolved"
        )
        source["current"]["evidence_needed"] = []
        self.rewrite_digest(manifest)
        self.write(SOURCE_MANIFEST, manifest)
        self.assert_rejected("unresolved snapshot")

    def test_rejects_disposition_or_missing_source_coverage(self) -> None:
        ledger = self.load(CONTRIBUTION_LEDGER)
        ledger["contributions"][0]["disposition"] = "supersede"
        self.rewrite_digest(ledger)
        self.write(CONTRIBUTION_LEDGER, ledger)
        self.assert_rejected("disposition")

        shutil.copy2(
            REPOSITORY / CONTRIBUTION_LEDGER, self.artifact(CONTRIBUTION_LEDGER)
        )
        ledger = self.load(CONTRIBUTION_LEDGER)
        source_id = ledger["contributions"][0]["source_id"]
        ledger["contributions"] = [
            item for item in ledger["contributions"] if item["source_id"] != source_id
        ]
        self.rewrite_digest(ledger)
        self.write(CONTRIBUTION_LEDGER, ledger)
        self.assert_rejected("contribution coverage")

    def test_rejects_missing_distribution_or_invalid_semantic_drift(self) -> None:
        ledger = self.load(CONTRIBUTION_LEDGER)
        retained = []
        for contribution in ledger["contributions"]:
            if contribution["mapping_status"] == "mapped":
                if contribution["destination"]["distribution_id"] == "rolecasting":
                    contribution["destination"]["distribution_id"] = "tricritical"
                    contribution["destination"]["evidence_paths"] = [
                        "plugins/tricritical/plugin.json"
                    ]
            else:
                if contribution["candidate_distribution_ids"] == ["rolecasting"]:
                    continue
                contribution["candidate_distribution_ids"] = [
                    item
                    for item in contribution["candidate_distribution_ids"]
                    if item != "rolecasting"
                ]
            retained.append(contribution)
        ledger["contributions"] = retained
        self.rewrite_digest(ledger)
        self.write(CONTRIBUTION_LEDGER, ledger)
        self.assert_rejected("distribution contribution coverage")

        shutil.copy2(
            REPOSITORY / CONTRIBUTION_LEDGER, self.artifact(CONTRIBUTION_LEDGER)
        )
        ledger = self.load(CONTRIBUTION_LEDGER)
        manifest = self.load(SOURCE_MANIFEST)
        unresolved = next(
            item
            for item in manifest["sources"]
            if item["byte_drift"]["status"] == "unresolved"
        )
        contribution = next(
            item
            for item in ledger["contributions"]
            if item["source_id"] == unresolved["id"]
        )
        contribution["semantic_drift"]["status"] = "changed"
        self.rewrite_digest(ledger)
        self.write(CONTRIBUTION_LEDGER, ledger)
        self.assert_rejected("semantic drift")

        shutil.copy2(
            REPOSITORY / CONTRIBUTION_LEDGER, self.artifact(CONTRIBUTION_LEDGER)
        )
        ledger = self.load(CONTRIBUTION_LEDGER)
        contribution = next(
            item
            for item in ledger["contributions"]
            if item["id"] == "withgraphite-agent-skills-candidate-contribution"
        )
        contribution["semantic_drift"]["status"] = "unchanged"
        self.rewrite_digest(ledger)
        self.write(CONTRIBUTION_LEDGER, ledger)
        self.assert_rejected("semantic drift")

    def test_rejects_host_source_coverage_and_matched_snapshot_drift(self) -> None:
        host_path = HOST_MANIFESTS[1]
        host = self.load(host_path)
        host["source_observations"].pop()
        self.rewrite_digest(host)
        self.write(host_path, host)
        self.assert_rejected("host source coverage")

        shutil.copy2(REPOSITORY / host_path, self.artifact(host_path))
        host = self.load(host_path)
        observation = next(
            item
            for item in host["source_observations"]
            if item["status"] == "installed"
        )
        observation["installations"][0]["matched_snapshots"] = ["baseline", "current"]
        self.rewrite_digest(host)
        self.write(host_path, host)
        self.assert_rejected("matched snapshots")

        shutil.copy2(REPOSITORY / host_path, self.artifact(host_path))
        host = self.load(host_path)
        observation = next(
            item
            for item in host["source_observations"]
            if item["source_id"] == "ivan-mergecraft"
        )
        observation["installations"][0]["skill_ids"].pop()
        self.rewrite_digest(host)
        self.write(host_path, host)
        self.assert_rejected("installed source skill coverage")

        shutil.copy2(REPOSITORY / host_path, self.artifact(host_path))
        host = self.load(host_path)
        observation = next(
            item
            for item in host["source_observations"]
            if item["source_id"] == "ivan-mergecraft"
        )
        observation["unobserved_skill_ids"].append(
            observation["installations"][0]["skill_ids"][0]
        )
        observation["unobserved_skill_ids"].sort()
        self.rewrite_digest(host)
        self.write(host_path, host)
        self.assert_rejected("installed source skill coverage")

    def test_rejects_checked_in_host_evidence_inventory_drift(self) -> None:
        for host_path in HOST_MANIFESTS:
            with self.subTest(host=host_path.name):
                host = self.load(host_path)
                observation = next(
                    item
                    for item in host["source_observations"]
                    if item["status"] in {"installed", "unresolved"}
                )
                source_id = observation["source_id"]
                observation.clear()
                observation.update({"source_id": source_id, "status": "absent"})
                self.rewrite_digest(host)
                self.write(host_path, host)
                self.assert_rejected("installed-host evidence inventory drift")
                shutil.copy2(REPOSITORY / host_path, self.artifact(host_path))

    def test_rejects_private_host_or_secret_shaped_material(self) -> None:
        host_path = HOST_MANIFESTS[1]
        for canary in (
            "standalone-source:ivans-work-macbook",
            "/Users/private-canary/skills",
            "/home/private-canary/skills",
            "file:///private/private-canary",
            "https://example.com/repo?token=private-canary",
            "Bearer private-canary",
        ):
            with self.subTest(canary=canary):
                host = self.load(host_path)
                observation = host["source_observations"][0]
                if observation["status"] == "installed":
                    observation["installations"][0]["installation_id"] = canary
                else:
                    observation["reason"] = canary
                self.rewrite_digest(host)
                self.write(host_path, host)
                self.assert_rejected(
                    "installed source route identifier must be opaque"
                    if canary == "standalone-source:ivans-work-macbook"
                    else "private material"
                )
                shutil.copy2(REPOSITORY / host_path, self.artifact(host_path))

        host_path = HOST_MANIFESTS[0]
        host = self.load(host_path)
        host["source_observations"][0]["reason"] = (
            "The route on ivans-work-macbook was unavailable."
        )
        self.rewrite_digest(host)
        self.write(host_path, host)
        self.assert_rejected("not a public template")

    def test_rejects_absolute_machine_paths_in_public_prose(self) -> None:
        def inject(relative: Path, document: dict, canary: str) -> None:
            if relative == SOURCE_MANIFEST:
                document["sources"][0]["byte_drift"]["summary"] = canary
            elif relative == CONTRIBUTION_LEDGER:
                document["contributions"][0]["behavior"] = canary
            else:
                observation = next(
                    item
                    for item in document["source_observations"]
                    if item["status"] == "unresolved"
                )
                observation["reason"] = canary

        for relative in (
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            HOST_MANIFESTS[0],
        ):
            for canary in (
                "/private/var/folders/private-canary",
                "/Volumes/private-canary/source",
                r"C:\Users\private-canary\source",
                r"\\private-canary-host\share\source",
                "//private-canary-host/share/source",
                "%2F%2Fprivate-canary-host%2Fshare%2Fsource",
                "roots,/private/var/folders/private-canary",
                "workspace:/Volumes/private-canary/source",
                r"root:C:\Users\private-canary\source",
                r"root;\\private-canary-host\share\source",
                r"\private\var\private-canary",
                r"root:\private\var\private-canary",
                "%5Cprivate%5Cvar%5Cprivate-canary",
                "root:%5Cprivate%5Cvar%5Cprivate-canary",
                "~ivan/.codex/private-canary",
                "%7Eivan%2F.codex%2Fprivate-canary",
                "root:~ivan/.codex/private-canary",
                "~ivan-d/.codex/private-canary",
                "file:/private/var/folders/private-canary",
                "FiLe:/private/var/folders/private-canary",
                "file:%2Fprivate%2Fvar%2Ffolders%2Fprivate-canary",
                "file:%252Fprivate%252Fvar%252Ffolders%252Fprivate-canary",
                "file:%2525252Fprivate%2525252Fprivate-canary",
                r"https://github.com/C:\private-canary\skill",
                "https://github.com/C:%5Cprivate-canary%5Cskill",
                r"https://github.com/\\private-canary-host\share",
                "https://github.com/nisavid/agents//private/var/folders/private-canary",
                (
                    "https://github.com/nisavid/agents/"
                    "%2Fprivate%2Fvar%2Ffolders%2Fprivate-canary"
                ),
                "https://alice@private-host.example/path/private-canary",
                "https://alice:secret@private-host.example/path/private-canary",
                "https://alice%3Asecret%40private-host.example/path/private-canary",
                "https://private-host/path/private-canary",
            ):
                with self.subTest(relative=relative, canary=canary):
                    document = self.load(relative)
                    inject(relative, document, canary)
                    self.rewrite_digest(document)
                    self.write(relative, document)
                    completed = self.run_validator()
                    self.assertNotEqual(completed.returncode, 0, completed.stdout)
                    self.assertEqual(
                        completed.stderr,
                        {
                            SOURCE_MANIFEST: (
                                "source-skill-lineage: source manifest contains "
                                "private material\n"
                            ),
                            CONTRIBUTION_LEDGER: (
                                "source-skill-lineage: contribution ledger contains "
                                "private material\n"
                            ),
                            HOST_MANIFESTS[0]: (
                                "source-skill-lineage: installed-host manifest contains "
                                "private material\n"
                            ),
                        }[relative],
                    )
                    shutil.copy2(REPOSITORY / relative, self.artifact(relative))

    def test_privacy_scan_accepts_safe_public_prose(self) -> None:
        for text in (
            "The selected profile: initial-work-macos-v1.",
            "The source file: source-manifest.json.",
            r"Escaped newline marker: \n.",
            "See https://github.com/nisavid/agents/issues/49.",
            "Repository-relative path: release/source-skill-lineage.",
        ):
            with self.subTest(text=text):
                self.module._privacy_scan(text, "public prose")


class RefreshSourceSkillLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_validator()
        cls.refresher = load_refresher()

    def test_refresh_document_boundaries_normalize_structural_exceptions(
        self,
    ) -> None:
        structural = (
            AttributeError,
            IndexError,
            KeyError,
            RecursionError,
            TypeError,
            ValueError,
        )
        for exception_type in structural:
            canary = f"private-{exception_type.__name__}-refresh-canary"
            with (
                self.subTest(boundary="render", exception=exception_type.__name__),
                mock.patch.object(
                    self.refresher,
                    "render_checked_in_documents",
                    side_effect=exception_type(canary),
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._stable_render(REPOSITORY, {})
            self.assertEqual(
                str(captured.exception), "source-lineage refresh input schema drift"
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertNotIn(canary, str(captured.exception))

            with (
                self.subTest(
                    boundary="capture-host", exception=exception_type.__name__
                ),
                mock.patch.object(
                    self.refresher,
                    "_capture_host_locked",
                    side_effect=exception_type(canary),
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.capture_host(
                    REPOSITORY, REPOSITORY / ".private-structural-input-canary"
                )
            self.assertEqual(str(captured.exception), "private host capture failed")
            self.assertIsNone(captured.exception.__cause__)
            self.assertNotIn(canary, str(captured.exception))

    def test_capture_host_rejects_noncanonical_source_before_private_capture(
        self,
    ) -> None:
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / SOURCE_MANIFEST
            target.write_bytes(target.read_bytes() + b"\n")
            private_input = clone / "private-host-input-canary.json"
            before = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }

            for entrypoint in ("api", "cli"):
                with self.subTest(entrypoint=entrypoint):
                    loader = mock.Mock(
                        side_effect=AssertionError("private-input-loader-canary")
                    )
                    routes = mock.Mock(
                        side_effect=AssertionError("private-route-capture-canary")
                    )
                    stdout_buffer = io.BytesIO()
                    stdout = io.TextIOWrapper(stdout_buffer, encoding="utf-8")
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(
                            self.refresher, "_load_private_host_input", loader
                        ),
                        mock.patch.object(
                            self.refresher, "_capture_host_observations", routes
                        ),
                        mock.patch.object(sys, "stdout", stdout),
                        mock.patch.object(sys, "stderr", stderr),
                    ):
                        if entrypoint == "api":
                            with self.assertRaises(
                                self.refresher.lineage.LineageError
                            ) as captured:
                                self.refresher.capture_host(clone, private_input)
                            self.assertEqual(
                                str(captured.exception),
                                "source manifest is not canonical JSON",
                            )
                            self.assertIsNone(captured.exception.__cause__)
                        else:
                            with mock.patch.object(
                                sys,
                                "argv",
                                [
                                    str(REFRESHER),
                                    "capture-host",
                                    str(clone),
                                    "--private-input",
                                    str(private_input),
                                ],
                            ):
                                self.assertEqual(self.refresher.main(), 1)
                            self.assertEqual(
                                stderr.getvalue(),
                                "source-skill-lineage-refresh: source manifest is not "
                                "canonical JSON\n",
                            )

                    stdout.flush()
                    self.assertEqual(stdout_buffer.getvalue(), b"")
                    if entrypoint == "api":
                        self.assertEqual(stderr.getvalue(), "")
                    loader.assert_not_called()
                    routes.assert_not_called()
                    rendered = stdout_buffer.getvalue().decode() + stderr.getvalue()
                    for private_value in (
                        str(clone),
                        str(target),
                        str(private_input),
                        target.name,
                        private_input.name,
                        "private-input-loader-canary",
                        "private-route-capture-canary",
                        "Traceback",
                    ):
                        self.assertNotIn(private_value, rendered)
                    self.assertEqual(
                        {
                            relative: (clone / relative).read_bytes()
                            for relative in public_artifacts
                        },
                        before,
                    )

    def clone_refresh_fixture(
        self, temporary_directory: str, *, include_tests: bool = False
    ) -> Path:
        clone = Path(temporary_directory) / "repository"
        subprocess.run(
            [
                "/usr/bin/git",
                "clone",
                "--quiet",
                "--no-local",
                str(REPOSITORY),
                str(clone),
            ],
            check=True,
            timeout=30,
        )
        relatives = [
            LINEAGE_ROOT,
            RESEARCH_REPORT,
            Path("scripts/validate_source_skill_lineage.py"),
            Path("scripts/refresh_source_skill_lineage.py"),
        ]
        if include_tests:
            relatives.append(Path("tests/test_validate_source_skill_lineage.py"))
        for relative in relatives:
            source = REPOSITORY / relative
            target = clone / relative
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        return clone

    def test_refresher_rejects_calendar_invalid_utc_inputs(self) -> None:
        invalid_timestamps = (
            "2026-13-18T00:00:00Z",
            "2026-04-31T00:00:00Z",
            "2026-08-18T24:00:00Z",
            "2026-08-18T00:60:00Z",
            "2026-08-18T00:00:60Z",
            "2025-02-29T00:00:00Z",
        )
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            clone = self.clone_refresh_fixture(temporary_directory)
            private_input = temporary / "private-host-time-canary.json"
            public_before = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }

            for invalid_timestamp in invalid_timestamps:
                private_input.write_text(
                    json.dumps(
                        {
                            "discovery_precedence": {
                                "reason_code": "not-observed",
                                "status": "unresolved",
                            },
                            "observed_at_utc": invalid_timestamp,
                            "profile_id": "initial-work-macos-v1",
                            "source_observations": [],
                        }
                    ),
                    encoding="utf-8",
                )
                private_before = private_input.read_bytes()
                capture_diagnostic = (
                    "private host observation must be a UTC second timestamp"
                )
                for entrypoint in ("api", "cli"):
                    with self.subTest(
                        boundary="capture-host",
                        entrypoint=entrypoint,
                        timestamp=invalid_timestamp,
                    ):
                        stdout_buffer = io.BytesIO()
                        stdout = io.TextIOWrapper(stdout_buffer, encoding="utf-8")
                        stderr = io.StringIO()
                        with (
                            mock.patch.object(sys, "stdout", stdout),
                            mock.patch.object(sys, "stderr", stderr),
                        ):
                            if entrypoint == "api":
                                with self.assertRaises(
                                    self.refresher.lineage.LineageError
                                ) as captured:
                                    self.refresher.capture_host(clone, private_input)
                                self.assertEqual(
                                    str(captured.exception),
                                    capture_diagnostic,
                                )
                                self.assertIsNone(captured.exception.__cause__)
                            else:
                                with mock.patch.object(
                                    sys,
                                    "argv",
                                    [
                                        str(REFRESHER),
                                        "capture-host",
                                        str(clone),
                                        "--private-input",
                                        str(private_input),
                                    ],
                                ):
                                    self.assertEqual(self.refresher.main(), 1)
                        stdout.flush()
                        self.assertEqual(stdout_buffer.getvalue(), b"")
                        self.assertEqual(
                            stderr.getvalue(),
                            (
                                ""
                                if entrypoint == "api"
                                else "source-skill-lineage-refresh: "
                                f"{capture_diagnostic}\n"
                            ),
                        )
                        rendered = stdout_buffer.getvalue().decode() + stderr.getvalue()
                        for private_value in (
                            str(clone),
                            str(private_input),
                            invalid_timestamp,
                            "Traceback",
                        ):
                            self.assertNotIn(private_value, rendered)
                        self.assertEqual(private_input.read_bytes(), private_before)
                        self.assertEqual(
                            {
                                relative: (clone / relative).read_bytes()
                                for relative in public_artifacts
                            },
                            public_before,
                        )

                receipt_diagnostic = "receipt capture must be a UTC second timestamp"
                for entrypoint in ("api", "cli"):
                    with self.subTest(
                        boundary="receipt",
                        entrypoint=entrypoint,
                        timestamp=invalid_timestamp,
                    ):
                        output = self.stable_receipt_output(f"invalid-utc-{entrypoint}")
                        publisher = mock.Mock()
                        stdout = io.StringIO()
                        stderr = io.StringIO()
                        with (
                            mock.patch.object(
                                self.refresher,
                                "_receipt_locked",
                                publisher,
                            ),
                            contextlib.redirect_stdout(stdout),
                            contextlib.redirect_stderr(stderr),
                        ):
                            if entrypoint == "api":
                                with self.assertRaises(
                                    self.refresher.lineage.LineageError
                                ) as captured:
                                    self.refresher.receipt(
                                        clone,
                                        output,
                                        invalid_timestamp,
                                    )
                                self.assertEqual(
                                    str(captured.exception),
                                    receipt_diagnostic,
                                )
                                self.assertIsNone(captured.exception.__cause__)
                            else:
                                with mock.patch.object(
                                    sys,
                                    "argv",
                                    [
                                        str(REFRESHER),
                                        "receipt",
                                        str(clone),
                                        "--output",
                                        str(output),
                                        "--captured-at-utc",
                                        invalid_timestamp,
                                    ],
                                ):
                                    self.assertEqual(self.refresher.main(), 1)
                        publisher.assert_not_called()
                        self.assertFalse(output.exists())
                        self.assertEqual(stdout.getvalue(), "")
                        self.assertEqual(
                            stderr.getvalue(),
                            (
                                ""
                                if entrypoint == "api"
                                else "source-skill-lineage-refresh: "
                                f"{receipt_diagnostic}\n"
                            ),
                        )
                        rendered = stdout.getvalue() + stderr.getvalue()
                        for private_value in (
                            str(clone),
                            str(output),
                            invalid_timestamp,
                            "Traceback",
                        ):
                            self.assertNotIn(private_value, rendered)
                        self.assertEqual(
                            {
                                relative: (clone / relative).read_bytes()
                                for relative in public_artifacts
                            },
                            public_before,
                        )

    def test_discovery_precedence_capture_mapping_and_render_are_profile_bound(
        self,
    ) -> None:
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            clone = self.clone_refresh_fixture(temporary_directory)
            private_input = temporary / "private-discovery-precedence-canary.json"
            public_before = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }
            hosts = {
                document["profile_id"]: document
                for document in (
                    json.loads((clone / relative).read_text(encoding="utf-8"))
                    for relative in HOST_MANIFESTS
                )
            }

            for profile_id, (
                reason_code,
                expected,
            ) in EXPECTED_DISCOVERY_PRECEDENCE.items():
                host = hosts[profile_id]
                private_document = {
                    "discovery_precedence": {
                        "reason_code": reason_code,
                        "status": "unresolved",
                    },
                    "observed_at_utc": host["observed_at_utc"],
                    "profile_id": profile_id,
                    "source_observations": [],
                }
                private_input.write_text(
                    json.dumps(private_document),
                    encoding="utf-8",
                )
                private_before = private_input.read_bytes()
                for entrypoint in ("api", "cli"):
                    with self.subTest(
                        boundary="mapping",
                        profile=profile_id,
                        entrypoint=entrypoint,
                    ):
                        observations = mock.Mock(
                            side_effect=(
                                host["source_observations"],
                                host["source_observations"],
                            )
                        )
                        stdout_buffer = io.BytesIO()
                        stdout = io.TextIOWrapper(stdout_buffer, encoding="utf-8")
                        stderr = io.StringIO()
                        with (
                            mock.patch.object(
                                self.refresher,
                                "_capture_host_observations",
                                observations,
                            ),
                            mock.patch.object(sys, "stdout", stdout),
                            mock.patch.object(sys, "stderr", stderr),
                        ):
                            if entrypoint == "api":
                                self.refresher.capture_host(clone, private_input)
                            else:
                                with mock.patch.object(
                                    sys,
                                    "argv",
                                    [
                                        str(REFRESHER),
                                        "capture-host",
                                        str(clone),
                                        "--private-input",
                                        str(private_input),
                                    ],
                                ):
                                    self.assertEqual(self.refresher.main(), 0)
                        stdout.flush()
                        captured = json.loads(stdout_buffer.getvalue())
                        self.assertEqual(captured["discovery_precedence"], expected)
                        self.assertEqual(captured["profile_id"], profile_id)
                        self.assertEqual(
                            captured["content_sha256"],
                            self.module.content_sha256(captured),
                        )
                        self.assertEqual(observations.call_count, 2)
                        self.assertEqual(stderr.getvalue(), "")
                        self.assertEqual(private_input.read_bytes(), private_before)
                        self.assertEqual(
                            {
                                relative: (clone / relative).read_bytes()
                                for relative in public_artifacts
                            },
                            public_before,
                        )
                        rendered = stdout_buffer.getvalue().decode() + stderr.getvalue()
                        for private_value in (
                            str(clone),
                            str(private_input),
                            reason_code,
                            "Traceback",
                        ):
                            self.assertNotIn(private_value, rendered)

            invalid_cases = (
                (
                    "initial-work-macos-v1",
                    None,
                    "private host capture schema drift",
                ),
                (
                    "initial-work-macos-v1",
                    {"reason_code": "not-observed", "status": "installed"},
                    "private discovery-precedence schema drift",
                ),
                (
                    "initial-work-macos-v1",
                    {
                        "reason_code": "transport-unavailable",
                        "status": "unresolved",
                    },
                    "private discovery-precedence reason is invalid",
                ),
                (
                    "initial-personal-cachyos-v1",
                    {"reason_code": "not-observed", "status": "unresolved"},
                    "private discovery-precedence reason is invalid",
                ),
                (
                    "initial-work-macos-v1",
                    {
                        "reason_code": "private-discovery-reason-canary",
                        "status": "unresolved",
                    },
                    "private discovery-precedence reason is invalid",
                ),
            )
            for profile_id, discovery_precedence, diagnostic in invalid_cases:
                with self.subTest(
                    boundary="rejection",
                    profile=profile_id,
                    discovery_precedence=discovery_precedence,
                ):
                    host = hosts[profile_id]
                    private_document = {
                        "observed_at_utc": host["observed_at_utc"],
                        "profile_id": profile_id,
                        "source_observations": [],
                    }
                    if discovery_precedence is not None:
                        private_document["discovery_precedence"] = discovery_precedence
                    private_input.write_text(
                        json.dumps(private_document),
                        encoding="utf-8",
                    )
                    private_before = private_input.read_bytes()
                    observations = mock.Mock(
                        side_effect=(
                            host["source_observations"],
                            host["source_observations"],
                        )
                    )
                    stdout_buffer = io.BytesIO()
                    stdout = io.TextIOWrapper(stdout_buffer, encoding="utf-8")
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(
                            self.refresher,
                            "_capture_host_observations",
                            observations,
                        ),
                        mock.patch.object(sys, "stdout", stdout),
                        mock.patch.object(sys, "stderr", stderr),
                        mock.patch.object(
                            sys,
                            "argv",
                            [
                                str(REFRESHER),
                                "capture-host",
                                str(clone),
                                "--private-input",
                                str(private_input),
                            ],
                        ),
                    ):
                        self.assertEqual(self.refresher.main(), 1)
                    stdout.flush()
                    observations.assert_not_called()
                    self.assertEqual(stdout_buffer.getvalue(), b"")
                    self.assertEqual(
                        stderr.getvalue(),
                        f"source-skill-lineage-refresh: {diagnostic}\n",
                    )
                    self.assertEqual(private_input.read_bytes(), private_before)
                    self.assertEqual(
                        {
                            relative: (clone / relative).read_bytes()
                            for relative in public_artifacts
                        },
                        public_before,
                    )
                    rendered = stdout_buffer.getvalue().decode() + stderr.getvalue()
                    for private_value in (
                        str(clone),
                        str(private_input),
                        "private-discovery-reason-canary",
                        "Traceback",
                    ):
                        self.assertNotIn(private_value, rendered)

            self.refresher.check(clone)
            captured = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }

            def preserve_package(_repository, package, _commit, *, deadline):
                self.assertGreater(deadline, 0)
                return dict(package)

            with mock.patch.object(
                self.refresher,
                "_package_projection",
                side_effect=preserve_package,
            ):
                rendered = self.refresher.render_checked_in_documents(
                    clone,
                    captured,
                )
            for profile_id, (_, expected) in EXPECTED_DISCOVERY_PRECEDENCE.items():
                relative = self.module.HOST_MANIFESTS[profile_id]
                host = json.loads(rendered[relative])
                self.assertEqual(host.get("discovery_precedence"), expected)
            self.assertEqual(
                {
                    relative: (clone / relative).read_bytes()
                    for relative in public_artifacts
                },
                public_before,
            )

    def test_local_license_evidence_is_descriptor_bound_and_digest_verified(
        self,
    ) -> None:
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            clone = self.clone_refresh_fixture(temporary_directory)
            license_path = clone / "LICENSE"
            original = license_path.read_bytes()
            expected_sha256 = "sha256:" + hashlib.sha256(original).hexdigest()
            source = json.loads((clone / SOURCE_MANIFEST).read_text(encoding="utf-8"))
            root_license_bindings = {
                snapshot["license"]["evidence_sha256"]
                for item in source["sources"]
                for snapshot in (item["baseline"], item["current"])
                if snapshot.get("status") == "resolved"
                and snapshot.get("license", {}).get("status") == "resolved"
                and snapshot["license"]["evidence_ref"] == "LICENSE"
            }
            self.assertEqual(root_license_bindings, {expected_sha256})
            self.module.validate_lineage(clone)
            self.refresher.check(clone)

            outside = temporary / "private-license-evidence-canary"
            outside.write_bytes(original)
            outside_identity = (outside.stat().st_dev, outside.stat().st_ino)
            path_type = type(license_path)
            real_path_open = path_type.open
            real_io_open = io.open
            real_os_open = os.open
            real_os_read = os.read

            def resolves_to_outside(path) -> bool:
                if isinstance(path, int) or not isinstance(path, (str, os.PathLike)):
                    return False
                try:
                    return Path(path).resolve() == outside
                except (OSError, RuntimeError):
                    return False

            cases = (
                ("missing", "source license evidence is missing"),
                ("byte-mutated", "source license evidence drift"),
                ("final-symlink", "source license evidence is missing"),
            )
            entrypoints = (
                ("validator", lambda: self.module.validate_lineage(clone)),
                ("check", lambda: self.refresher.check(clone)),
            )
            for case, diagnostic in cases:
                if license_path.exists() or license_path.is_symlink():
                    license_path.unlink()
                if case == "byte-mutated":
                    license_path.write_bytes(
                        original + b"private-license-byte-canary\n"
                    )
                elif case == "final-symlink":
                    license_path.symlink_to(outside)

                before = {
                    relative: (clone / relative).read_bytes()
                    for relative in public_artifacts
                }
                expected_license_state = (
                    ("symlink", os.readlink(license_path))
                    if license_path.is_symlink()
                    else ("missing",)
                    if not license_path.exists()
                    else ("file", license_path.read_bytes())
                )
                for entrypoint, invoke in entrypoints:
                    outside_opens = []
                    outside_reads = []
                    outside_high_level_opens = []

                    def reject_outside_path_open(
                        path, *args, observed=outside_high_level_opens, **kwargs
                    ):
                        if resolves_to_outside(path):
                            observed.append(("Path.open", os.fspath(path)))
                            raise AssertionError("outside-license-read-canary")
                        return real_path_open(path, *args, **kwargs)

                    def reject_outside_io_open(
                        path, *args, observed=outside_high_level_opens, **kwargs
                    ):
                        if resolves_to_outside(path):
                            observed.append(("io.open", os.fspath(path)))
                            raise AssertionError("outside-license-read-canary")
                        return real_io_open(path, *args, **kwargs)

                    def reject_outside_os_open(
                        path,
                        flags,
                        mode=0o777,
                        *,
                        dir_fd=None,
                        observed=outside_opens,
                    ):
                        descriptor = real_os_open(path, flags, mode, dir_fd=dir_fd)
                        metadata = os.fstat(descriptor)
                        if (metadata.st_dev, metadata.st_ino) == outside_identity:
                            observed.append(os.fspath(path))
                            os.close(descriptor)
                            raise AssertionError("outside-license-read-canary")
                        return descriptor

                    def reject_outside_os_read(
                        descriptor, count, observed=outside_reads
                    ):
                        metadata = os.fstat(descriptor)
                        if (metadata.st_dev, metadata.st_ino) == outside_identity:
                            observed.append(count)
                            raise AssertionError("outside-license-read-canary")
                        return real_os_read(descriptor, count)

                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with (
                        self.subTest(case=case, entrypoint=entrypoint),
                        mock.patch.object(
                            path_type,
                            "open",
                            autospec=True,
                            side_effect=reject_outside_path_open,
                        ),
                        mock.patch.object(
                            io, "open", side_effect=reject_outside_io_open
                        ),
                        mock.patch.object(
                            os, "open", side_effect=reject_outside_os_open
                        ),
                        mock.patch.object(
                            os, "read", side_effect=reject_outside_os_read
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                        self.assertRaises(
                            (
                                self.module.LineageError,
                                self.refresher.lineage.LineageError,
                            )
                        ) as captured,
                    ):
                        invoke()

                    self.assertEqual(str(captured.exception), diagnostic)
                    self.assertIsNone(captured.exception.__cause__)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(stderr.getvalue(), "")
                    self.assertEqual(outside_opens, [])
                    self.assertEqual(outside_reads, [])
                    self.assertEqual(outside_high_level_opens, [])
                    rendered = (
                        stdout.getvalue() + stderr.getvalue() + str(captured.exception)
                    )
                    for private_value in (
                        str(clone),
                        str(license_path),
                        str(outside),
                        outside.name,
                        "private-license-byte-canary",
                        "outside-license-read-canary",
                        "Traceback",
                    ):
                        self.assertNotIn(private_value, rendered)
                    self.assertEqual(
                        {
                            relative: (clone / relative).read_bytes()
                            for relative in public_artifacts
                        },
                        before,
                    )
                    observed_license_state = (
                        ("symlink", os.readlink(license_path))
                        if license_path.is_symlink()
                        else ("missing",)
                        if not license_path.exists()
                        else ("file", license_path.read_bytes())
                    )
                    self.assertEqual(observed_license_state, expected_license_state)
                    self.assertEqual(outside.read_bytes(), original)

    def test_mapped_contribution_evidence_requires_a_retained_regular_file(
        self,
    ) -> None:
        diagnostic = "contribution evidence path is missing"
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            clone = self.clone_refresh_fixture(temporary_directory)
            evidence_relative = Path(
                "plugins/artifact-customs/skills/"
                "adopting-third-party-components/SKILL.md"
            )
            self.assertIn(
                evidence_relative.as_posix(),
                self.module.CONTRIBUTION_EVIDENCE_RECEIPTS,
            )
            evidence_leaf = clone / evidence_relative
            evidence_bytes = evidence_leaf.read_bytes()
            evidence_mode = stat.S_IMODE(evidence_leaf.stat().st_mode)
            route_directory = evidence_leaf.parent
            route_backup = route_directory.with_name(route_directory.name + "-backup")
            leaf_backup = evidence_leaf.with_name(evidence_leaf.name + "-backup")
            outside = temporary / "private-contribution-evidence-canary"
            outside.mkdir()
            outside_file = outside / evidence_leaf.name
            outside_bytes = b"private-contribution-evidence-canary-bytes"
            outside_file.write_bytes(outside_bytes)
            outside_identity = (
                outside_file.stat().st_dev,
                outside_file.stat().st_ino,
            )
            real_validate_source_manifest = self.module.validate_source_manifest
            real_open = os.open
            real_read = os.read
            path_type = type(outside_file)
            real_path_open = path_type.open
            real_io_open = io.open
            high_level_read_canary = "private-contribution-evidence-read-canary"

            cases = ("intermediate-symlink", "directory-leaf")
            for name in cases:
                with self.subTest(name=name):
                    before = {
                        relative: (clone / relative).read_bytes()
                        for relative in public_artifacts
                    }
                    outside_opens = []
                    outside_reads = []
                    outside_high_level_opens = []

                    def resolves_to_outside(path) -> bool:
                        if isinstance(path, int):
                            return False
                        try:
                            metadata = os.stat(path)
                            return (
                                metadata.st_dev,
                                metadata.st_ino,
                            ) == outside_identity
                        except (OSError, RuntimeError, TypeError):
                            return False

                    def reject_outside_path_open(
                        path,
                        *args,
                        observed=outside_high_level_opens,
                        **kwargs,
                    ):
                        if resolves_to_outside(path):
                            observed.append(("Path.open", os.fspath(path)))
                            raise AssertionError(high_level_read_canary)
                        return real_path_open(path, *args, **kwargs)

                    def reject_outside_io_open(
                        path,
                        *args,
                        observed=outside_high_level_opens,
                        **kwargs,
                    ):
                        if resolves_to_outside(path):
                            observed.append(("io.open", os.fspath(path)))
                            raise AssertionError(high_level_read_canary)
                        return real_io_open(path, *args, **kwargs)

                    def record_open(
                        path,
                        flags,
                        mode=0o777,
                        *,
                        dir_fd=None,
                        observed=outside_opens,
                    ):
                        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                        metadata = os.fstat(descriptor)
                        if (metadata.st_dev, metadata.st_ino) == outside_identity:
                            observed.append(os.fspath(path))
                        return descriptor

                    def record_read(descriptor, count, observed=outside_reads):
                        metadata = os.fstat(descriptor)
                        if (metadata.st_dev, metadata.st_ino) == outside_identity:
                            observed.append(count)
                        return real_read(descriptor, count)

                    def validate_then_mutate_route(*args, route_kind=name, **kwargs):
                        sources = real_validate_source_manifest(*args, **kwargs)
                        if route_kind == "intermediate-symlink":
                            route_directory.rename(route_backup)
                            route_directory.symlink_to(
                                outside, target_is_directory=True
                            )
                        else:
                            evidence_leaf.rename(leaf_backup)
                            evidence_leaf.mkdir()
                        return sources

                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    try:
                        with (
                            mock.patch.object(
                                self.module,
                                "validate_source_manifest",
                                side_effect=validate_then_mutate_route,
                            ),
                            mock.patch.object(
                                self.module.os, "open", side_effect=record_open
                            ),
                            mock.patch.object(
                                self.module.os, "read", side_effect=record_read
                            ),
                            mock.patch.object(
                                path_type,
                                "open",
                                autospec=True,
                                side_effect=reject_outside_path_open,
                            ),
                            mock.patch.object(
                                io, "open", side_effect=reject_outside_io_open
                            ),
                            contextlib.redirect_stdout(stdout),
                            contextlib.redirect_stderr(stderr),
                            self.assertRaises(self.module.LineageError) as captured,
                        ):
                            self.module.validate_lineage(clone)

                        self.assertEqual(str(captured.exception), diagnostic)
                        self.assertIsNone(captured.exception.__cause__)
                        self.assertEqual(stdout.getvalue(), "")
                        self.assertEqual(stderr.getvalue(), "")
                        self.assertEqual(outside_opens, [])
                        self.assertEqual(outside_reads, [])
                        self.assertEqual(outside_high_level_opens, [])
                        rendered = (
                            stdout.getvalue()
                            + stderr.getvalue()
                            + str(captured.exception)
                        )
                        for private_value in (
                            str(clone),
                            str(outside),
                            outside.name,
                            evidence_relative.as_posix(),
                            outside_file.name,
                            "private-contribution-evidence-canary-bytes",
                            high_level_read_canary,
                            "Traceback",
                        ):
                            self.assertNotIn(private_value, rendered)
                        self.assertEqual(
                            {
                                relative: (clone / relative).read_bytes()
                                for relative in public_artifacts
                            },
                            before,
                        )
                        self.assertEqual(outside_file.read_bytes(), outside_bytes)
                        if name == "intermediate-symlink":
                            self.assertTrue(route_directory.is_symlink())
                            self.assertEqual(os.readlink(route_directory), str(outside))
                            escaped = evidence_leaf

                            def path_open_probe(path=escaped):
                                with path.open("rb"):
                                    pass

                            def io_open_probe(path=escaped):
                                # Exercise the direct io.open route intentionally.
                                with io.open(path, "rb"):  # noqa: UP020
                                    pass

                            def read_before_containment_probe(path=escaped):
                                path.read_bytes()

                            probes = (
                                ("Path.open", "Path.open", path_open_probe),
                                ("io.open", "io.open", io_open_probe),
                                (
                                    "Path.read_bytes",
                                    "Path.open",
                                    read_before_containment_probe,
                                ),
                            )
                            for probe_name, expected_route, probe in probes:
                                with (
                                    self.subTest(probe=probe_name),
                                    mock.patch.object(
                                        path_type,
                                        "open",
                                        autospec=True,
                                        side_effect=reject_outside_path_open,
                                    ),
                                    mock.patch.object(
                                        io,
                                        "open",
                                        side_effect=reject_outside_io_open,
                                    ),
                                    self.assertRaisesRegex(
                                        AssertionError,
                                        rf"\A{high_level_read_canary}\Z",
                                    ),
                                ):
                                    before_attempts = len(outside_high_level_opens)
                                    probe()
                                self.assertEqual(
                                    outside_high_level_opens[before_attempts][0],
                                    expected_route,
                                )
                                self.assertEqual(
                                    len(outside_high_level_opens), before_attempts + 1
                                )
                            self.assertEqual(outside_file.read_bytes(), outside_bytes)
                        else:
                            self.assertTrue(evidence_leaf.is_dir())
                    finally:
                        if route_directory.is_symlink():
                            route_directory.unlink()
                        if route_backup.exists():
                            route_backup.rename(route_directory)
                        if evidence_leaf.is_dir():
                            evidence_leaf.rmdir()
                        if leaf_backup.exists():
                            leaf_backup.rename(evidence_leaf)

                    self.assertEqual(evidence_leaf.read_bytes(), evidence_bytes)
                    self.assertEqual(
                        stat.S_IMODE(evidence_leaf.stat().st_mode), evidence_mode
                    )

    def test_mapped_contribution_evidence_content_is_digest_bound(self) -> None:
        diagnostic = "contribution evidence content drift"
        ledger = json.loads((REPOSITORY / CONTRIBUTION_LEDGER).read_bytes())
        mapped_paths = sorted(
            {
                evidence_path
                for contribution in ledger["contributions"]
                if contribution["mapping_status"] == "mapped"
                for evidence_path in contribution["destination"]["evidence_paths"]
            }
        )
        receipts = self.module.CONTRIBUTION_EVIDENCE_RECEIPTS
        self.assertEqual(len(receipts), 22)
        self.assertEqual(tuple(receipts), tuple(mapped_paths))
        self.assertEqual(
            self.refresher.lineage.CONTRIBUTION_EVIDENCE_RECEIPTS,
            receipts,
        )
        self.assertEqual(
            receipts,
            {
                relative: "sha256:"
                + hashlib.sha256((REPOSITORY / relative).read_bytes()).hexdigest()
                for relative in mapped_paths
            },
        )

        entrypoints = (
            (
                "validator",
                self.module,
                self.module.LineageError,
                self.module.validate_lineage,
            ),
            (
                "check",
                self.refresher.lineage,
                self.refresher.lineage.LineageError,
                self.refresher.check,
            ),
        )
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            public_before = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }
            for index, relative in enumerate(mapped_paths):
                target = clone / relative
                original = target.read_bytes()
                original_metadata = target.stat()
                original_mode = stat.S_IMODE(original_metadata.st_mode)
                self.assertTrue(stat.S_ISREG(original_metadata.st_mode))
                for entrypoint, lineage_module, error_type, invoke in entrypoints:
                    with self.subTest(relative=relative, entrypoint=entrypoint):
                        canary = (
                            f"private-mapped-evidence-content-canary-{index}-"
                            f"{entrypoint}"
                        )
                        mutated = canary.encode("ascii") + b"\n"
                        self.assertNotEqual(mutated, original)
                        self.assertLessEqual(
                            len(mutated), self.module.MAX_VALIDATION_FILE_BYTES
                        )
                        real_validate_candidate = lineage_module._validate_candidate
                        mutation_calls = []

                        def validate_candidate_then_mutate(
                            *args,
                            real_candidate=real_validate_candidate,
                            mutation_target=target,
                            mutation_bytes=mutated,
                            mutation_mode=original_mode,
                            observed=mutation_calls,
                            **kwargs,
                        ):
                            real_candidate(*args, **kwargs)
                            mutation_target.write_bytes(mutation_bytes)
                            os.chmod(mutation_target, mutation_mode)
                            observed.append(mutation_target)

                        stdout = io.StringIO()
                        stderr = io.StringIO()
                        try:
                            with (
                                mock.patch.object(
                                    lineage_module,
                                    "_validate_candidate",
                                    side_effect=validate_candidate_then_mutate,
                                ),
                                contextlib.redirect_stdout(stdout),
                                contextlib.redirect_stderr(stderr),
                                self.assertRaises(error_type) as captured,
                            ):
                                invoke(clone)

                            self.assertEqual(mutation_calls, [target])
                            self.assertEqual(str(captured.exception), diagnostic)
                            self.assertIsNone(captured.exception.__cause__)
                            self.assertEqual(stdout.getvalue(), "")
                            self.assertEqual(stderr.getvalue(), "")
                            rendered = (
                                stdout.getvalue()
                                + stderr.getvalue()
                                + str(captured.exception)
                            )
                            for private_value in (
                                str(clone),
                                str(target),
                                relative,
                                target.name,
                                canary,
                                "Traceback",
                            ):
                                self.assertNotIn(private_value, rendered)
                            self.assertEqual(target.read_bytes(), mutated)
                            self.assertEqual(
                                stat.S_IMODE(target.stat().st_mode), original_mode
                            )
                            self.assertEqual(
                                {
                                    path: (clone / path).read_bytes()
                                    for path in public_artifacts
                                },
                                public_before,
                            )
                        finally:
                            target.write_bytes(original)
                            os.chmod(target, original_mode)

                        restored_metadata = target.stat()
                        self.assertEqual(target.read_bytes(), original)
                        self.assertEqual(
                            stat.S_IMODE(restored_metadata.st_mode), original_mode
                        )
                        self.assertEqual(
                            (
                                restored_metadata.st_dev,
                                restored_metadata.st_ino,
                            ),
                            (
                                original_metadata.st_dev,
                                original_metadata.st_ino,
                            ),
                        )

    def stable_receipt_output(self, label: str) -> Path:
        private_tmp = Path("/private/tmp")
        stable_root = (
            private_tmp
            if sys.platform == "darwin" and private_tmp.is_dir()
            else Path("/tmp")
        )
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f"source-lineage-{label}-", suffix=".json", dir=stable_root
        )
        os.close(descriptor)
        output = Path(raw_path)
        output.unlink()
        self.addCleanup(output.unlink, missing_ok=True)
        return output

    def committed_receipt_fixture(self, temporary_directory: str) -> Path:
        clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
        subprocess.run(
            [
                "/usr/bin/git",
                "add",
                "--",
                str(LINEAGE_ROOT),
                str(RESEARCH_REPORT),
                "scripts",
                "tests",
            ],
            cwd=clone,
            check=True,
        )
        subprocess.run(
            [
                "/usr/bin/git",
                "-c",
                "user.name=Lineage Test",
                "-c",
                "user.email=lineage@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "test: bind receipt fixture",
            ],
            cwd=clone,
            check=True,
        )
        return clone

    def make_bare_git_repository(self, root: Path, name: str) -> Path:
        repository = root / name
        subprocess.run(
            ["/usr/bin/git", "init", "--bare", "--quiet", str(repository)],
            check=True,
        )
        return repository

    def write_git_blob(self, repository: Path, content: bytes) -> str:
        return (
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(repository),
                    "hash-object",
                    "-w",
                    "--stdin",
                ],
                input=content,
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )

    def write_git_tree(
        self, repository: Path, entries: list[tuple[str, str, str]]
    ) -> str:
        tree_input = b"".join(
            f"{mode} blob {object_id}\t{name}\n".encode()
            for mode, object_id, name in entries
        )
        return (
            subprocess.run(
                ["/usr/bin/git", "-C", str(repository), "mktree"],
                input=tree_input,
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )

    def write_git_commit(self, repository: Path, tree: str) -> str:
        return (
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(repository),
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit-tree",
                    tree,
                    "-m",
                    "test fixture",
                ],
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )

    def write_git_commit_with_path(
        self,
        repository: Path,
        ref: str,
        relative: str,
        content: bytes,
    ) -> str:
        stream = b"".join(
            (
                b"blob\nmark :1\ndata ",
                str(len(content)).encode("ascii"),
                b"\n",
                content,
                b"\ncommit refs/heads/",
                ref.encode("ascii"),
                b"\ncommitter Lineage Test <lineage@example.invalid> 0 +0000\n",
                b"data 4\ntest\nM 100644 :1 ",
                relative.encode("utf-8"),
                b"\n\n",
            )
        )
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "fast-import", "--quiet"],
            input=stream,
            capture_output=True,
            check=True,
        )
        return (
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(repository),
                    "rev-parse",
                    "--verify",
                    f"refs/heads/{ref}",
                ],
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )

    def test_trusted_validator_loader_is_descriptor_bound(self) -> None:
        real_open = os.open
        open_calls = []

        def record_open(path, flags, mode=0o777, *, dir_fd=None):
            open_calls.append((path, flags, dir_fd))
            if dir_fd is None:
                return real_open(path, flags, mode)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        pathname_metadata = VALIDATOR.lstat()
        with (
            mock.patch.object(self.refresher.os, "open", side_effect=record_open),
            mock.patch.object(
                Path,
                "lstat",
                return_value=pathname_metadata,
            ) as path_lstat,
            mock.patch.object(
                Path,
                "read_bytes",
                return_value=b"raise RuntimeError('private-canary-aba')\n",
            ) as path_read,
        ):
            module, raw = self.refresher._load_validator()

        self.assertTrue(callable(module.validate_lineage))
        self.assertEqual(raw, VALIDATOR.read_bytes())
        self.assertFalse(path_lstat.called)
        self.assertFalse(path_read.called)
        self.assertEqual(len(open_calls), 1)
        path, flags, dir_fd = open_calls[0]
        self.assertEqual(Path(path), VALIDATOR)
        self.assertIsNone(dir_fd)
        self.assertTrue(flags & os.O_CLOEXEC)
        self.assertTrue(flags & os.O_NOFOLLOW)
        self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)

    def test_trusted_validator_loader_rejects_unpinned_bytes_before_execution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            marker = Path(temporary_directory) / "private-canary-executed"
            validator = Path(temporary_directory) / "validator.py"
            prefix = (
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n".encode()
            )
            padding = self.refresher.TRUSTED_VALIDATOR_SIZE - len(prefix) - 2
            self.assertGreater(padding, 0)
            validator.write_bytes(prefix + b"#" + (b"x" * padding) + b"\n")
            with (
                mock.patch.object(self.refresher, "VALIDATOR", validator),
                self.assertRaisesRegex(
                    SystemExit, "source-skill-lineage validator cannot be loaded"
                ),
            ):
                self.refresher._load_validator()

            self.assertFalse(marker.exists())

    def test_trusted_validator_loader_rejects_final_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            validator = Path(temporary_directory) / "validator.py"
            moved = Path(temporary_directory) / "validator-opened.py"
            validator.write_bytes(VALIDATOR.read_bytes())
            real_stat = os.stat
            replaced = False

            def replace_before_visible_stat(path, *, dir_fd=None, follow_symlinks=True):
                nonlocal replaced
                if Path(path) == validator and not replaced:
                    replaced = True
                    validator.rename(moved)
                    validator.write_bytes(moved.read_bytes())
                return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

            with (
                mock.patch.object(self.refresher, "VALIDATOR", validator),
                mock.patch.object(
                    self.refresher.os,
                    "stat",
                    side_effect=replace_before_visible_stat,
                ),
                self.assertRaisesRegex(
                    SystemExit, "source-skill-lineage validator cannot be loaded"
                ),
            ):
                self.refresher._load_validator()

            self.assertTrue(replaced)

    def test_trusted_validator_loader_rejects_each_metadata_drift(self) -> None:
        descriptor = os.open(VALIDATOR, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        identity_fields = (
            "st_mode",
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        for field in identity_fields:
            with self.subTest(field=field):
                changed = mock.Mock()
                for name in identity_fields:
                    setattr(changed, name, getattr(metadata, name))
                setattr(changed, field, getattr(changed, field) + 1)
                with (
                    mock.patch.object(
                        self.refresher.os,
                        "fstat",
                        side_effect=(metadata, changed),
                    ),
                    self.assertRaisesRegex(
                        SystemExit, "source-skill-lineage validator cannot be loaded"
                    ),
                ):
                    self.refresher._load_validator()

    def test_trusted_validator_loader_closes_before_compilation(self) -> None:
        real_close = os.close
        real_compile = compile
        for body_error in (None, OSError, KeyboardInterrupt):
            for close_error in (OSError, KeyboardInterrupt):
                with self.subTest(
                    body_error=body_error,
                    close_error=close_error,
                ):
                    close_calls = []

                    def close_then_raise(
                        descriptor,
                        *,
                        recorded=close_calls,
                        error_type=close_error,
                    ):
                        recorded.append(descriptor)
                        real_close(descriptor)
                        raise error_type("/private/private-canary-close")

                    fstat_patch = (
                        mock.patch.object(
                            self.refresher.os,
                            "fstat",
                            side_effect=body_error("/private/private-canary-read"),
                        )
                        if body_error is not None
                        else contextlib.nullcontext()
                    )
                    with (
                        fstat_patch,
                        mock.patch.object(
                            self.refresher.os,
                            "close",
                            side_effect=close_then_raise,
                        ),
                        mock.patch(
                            "builtins.compile", wraps=real_compile
                        ) as compile_spy,
                        self.assertRaisesRegex(
                            SystemExit,
                            "source-skill-lineage validator cannot be loaded",
                        ) as captured,
                    ):
                        self.refresher._load_validator()

                    self.assertEqual(len(close_calls), 1)
                    compile_spy.assert_not_called()
                    self.assertIsNone(captured.exception.__cause__)
                    self.assertIsNone(captured.exception.__context__)
                    self.assertNotIn("private-canary", str(captured.exception))

    def test_trusted_validator_loader_normalizes_compile_and_exec_failures(
        self,
    ) -> None:
        for phase in ("compile", "exec"):
            for error_type in (RuntimeError, KeyboardInterrupt, SystemExit):
                with self.subTest(phase=phase, error_type=error_type):
                    target = f"builtins.{phase}"
                    with (
                        mock.patch(
                            target,
                            side_effect=error_type("/private/private-canary-execution"),
                        ),
                        self.assertRaisesRegex(
                            SystemExit,
                            "source-skill-lineage validator cannot be loaded",
                        ) as captured,
                    ):
                        self.refresher._load_validator()

                    self.assertIsNone(captured.exception.__cause__)
                    self.assertIsNone(captured.exception.__context__)
                    self.assertNotIn("private-canary", str(captured.exception))

    def test_rejects_absolute_machine_paths_in_public_research_report(self) -> None:
        for canary in (
            "/private/var/folders/private-canary",
            "/Volumes/private-canary/source",
            r"C:\Users\private-canary\source",
            r"\\private-canary-host\share\source",
            "//private-canary-host/share/source",
            "%2F%2Fprivate-canary-host%2Fshare%2Fsource",
            "roots,/private/var/folders/private-canary",
            "workspace:/Volumes/private-canary/source",
            r"root:C:\Users\private-canary\source",
            r"root;\\private-canary-host\share\source",
            r"\private\var\private-canary",
            r"root:\private\var\private-canary",
            "%5Cprivate%5Cvar%5Cprivate-canary",
            "root:%5Cprivate%5Cvar%5Cprivate-canary",
            "~ivan/.codex/private-canary",
            "%7Eivan%2F.codex%2Fprivate-canary",
            "root:~ivan/.codex/private-canary",
            "~ivan-d/.codex/private-canary",
            "file:/private/var/folders/private-canary",
            "FiLe:/private/var/folders/private-canary",
            "file:%2Fprivate%2Fvar%2Ffolders%2Fprivate-canary",
            "file:%252Fprivate%252Fvar%252Ffolders%252Fprivate-canary",
            "file:%2525252Fprivate%2525252Fprivate-canary",
            r"https://github.com/C:\private-canary\skill",
            "https://github.com/C:%5Cprivate-canary%5Cskill",
            r"https://github.com/\\private-canary-host\share",
            "https://github.com/nisavid/agents//private/var/folders/private-canary",
            (
                "https://github.com/nisavid/agents/"
                "%2Fprivate%2Fvar%2Ffolders%2Fprivate-canary"
            ),
            "https://alice@private-host.example/path/private-canary",
            "https://alice:secret@private-host.example/path/private-canary",
            "https://alice%3Asecret%40private-host.example/path/private-canary",
            "https://private-host/path/private-canary",
        ):
            with (
                self.subTest(canary=canary),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                clone = self.clone_refresh_fixture(temporary_directory)
                report = clone / RESEARCH_REPORT
                report.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPOSITORY / RESEARCH_REPORT, report)
                with report.open("a", encoding="utf-8") as output:
                    output.write(f"\n{canary}\n")
                with self.assertRaisesRegex(
                    self.module.LineageError, "private material"
                ) as raised:
                    self.module.validate_lineage(clone)
                self.assertEqual(
                    str(raised.exception),
                    "source-lineage research report contains private material",
                )

    def test_git_helpers_reject_option_shaped_revisions_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            outside = Path(temporary_directory) / "private-canary"
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "committed lineage commit must be a Git SHA-1 identity",
            ):
                self.refresher._git_blob(
                    REPOSITORY,
                    f"--output={outside}",
                    "plugins/artifact-customs/plugin.json",
                )
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "refresh Git identity is unavailable",
            ):
                self.refresher._git_tree_identity(REPOSITORY, "--help", ".")
            self.assertFalse(outside.exists())

    def test_refresher_reader_entrypoints_hold_a_shared_lock(self) -> None:
        state = {"locked": False}
        view = mock.Mock(root=REPOSITORY)

        class SharedLock:
            def __enter__(self):
                self.assertFalse(state["locked"])
                state["locked"] = True
                return view

            def __exit__(self, exception_type, exception, traceback):
                state["locked"] = False

            def assertFalse(self, value):
                self_test.assertFalse(value)

        self_test = self

        def lock(repository, *, exclusive, nonblocking=False):
            self.assertEqual(repository, REPOSITORY)
            self.assertFalse(exclusive)
            self.assertFalse(nonblocking)
            return SharedLock()

        def observe(*arguments):
            self.assertTrue(state["locked"])
            self.assertIs(arguments[-1], view)

        cases = (
            ("_check_locked", self.refresher.check, (REPOSITORY,)),
            (
                "_receipt_locked",
                self.refresher.receipt,
                (
                    REPOSITORY,
                    REPOSITORY / ".source-lineage-reader-lock-canary",
                    "2026-08-18T00:00:00Z",
                ),
            ),
            (
                "_capture_host_locked",
                self.refresher.capture_host,
                (REPOSITORY, REPOSITORY / ".source-lineage-private-canary"),
            ),
        )
        for helper, entrypoint, arguments in cases:
            with self.subTest(helper=helper):
                with (
                    mock.patch.object(
                        self.refresher.lineage, "_lineage_lock", side_effect=lock
                    ),
                    mock.patch.object(self.refresher, helper, side_effect=observe),
                ):
                    entrypoint(*arguments)
                self.assertFalse(state["locked"])

    def test_capture_git_accepts_a_repository_root_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "source"
            repository.mkdir()
            subprocess.run(
                ["/usr/bin/git", "init", "--quiet", str(repository)], check=True
            )
            (repository / "SKILL.md").write_text("# Root skill\n", encoding="utf-8")
            (repository / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (repository / "run.sh").chmod(0o755)
            subprocess.run(
                ["/usr/bin/git", "add", "--", "SKILL.md", "run.sh"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: root source",
                ],
                cwd=repository,
                check=True,
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REFRESHER),
                    "capture-git",
                    str(repository),
                    "--revision",
                    "HEAD",
                    "--root",
                    ".",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            captured = json.loads(completed.stdout)
            self.assertEqual(captured["entry_count"], 2)
            self.assertEqual(captured["total_bytes"], 30)
            self.assertRegex(captured["tree_sha1"], r"^[0-9a-f]{40}$")

            (repository / "private-link").symlink_to("SKILL.md")
            subprocess.run(
                ["/usr/bin/git", "add", "--", "private-link"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: safe symlink source",
                ],
                cwd=repository,
                check=True,
            )
            safe = subprocess.run(
                [
                    sys.executable,
                    str(REFRESHER),
                    "capture-git",
                    str(repository),
                    "--revision",
                    "HEAD",
                    "--root",
                    ".",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(safe.returncode, 0, safe.stderr)
            self.assertEqual(json.loads(safe.stdout)["entry_count"], 3)

            (repository / "private-link").unlink()
            (repository / "private-link").symlink_to("../private-canary")
            subprocess.run(
                ["/usr/bin/git", "add", "--", "private-link"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: escaping symlink source",
                ],
                cwd=repository,
                check=True,
            )
            unsafe = subprocess.run(
                [
                    sys.executable,
                    str(REFRESHER),
                    "capture-git",
                    str(repository),
                    "--revision",
                    "HEAD",
                    "--root",
                    ".",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(unsafe.returncode, 0)
            self.assertIn("symlink escapes", unsafe.stderr)
            self.assertNotIn(str(repository), unsafe.stderr)
            self.assertNotIn(str(repository), unsafe.stderr)

    def test_capture_host_is_complete_stable_and_pathless(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            skill_root = temporary / "private-host" / "find-skills"
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text("# Find skills\n", encoding="utf-8")
            rolecasting_root = temporary / "private-host" / "choosing-agent-models"
            rolecasting_root.mkdir(parents=True)
            (rolecasting_root / "SKILL.md").write_text(
                "# Choose agent models\n", encoding="utf-8"
            )
            source = json.loads(
                (REPOSITORY / SOURCE_MANIFEST).read_text(encoding="utf-8")
            )
            observations = []
            for item in source["sources"]:
                if item["id"] == "vercel-labs-skill-set":
                    observations.append(
                        {
                            "installations": [
                                {
                                    "skill_roots": [
                                        {
                                            "path": str(skill_root),
                                            "skill_id": "find-skills",
                                        }
                                    ],
                                }
                            ],
                            "source_id": item["id"],
                            "status": "installed",
                        }
                    )
                elif item["id"] == "ivan-rolecasting":
                    observations.append(
                        {
                            "installations": [
                                {
                                    "skill_roots": [
                                        {
                                            "path": str(rolecasting_root),
                                            "skill_id": "choosing-agent-models",
                                        }
                                    ],
                                }
                            ],
                            "source_id": item["id"],
                            "status": "installed",
                        }
                    )
                else:
                    observations.append({"source_id": item["id"], "status": "absent"})
            private_input = temporary / "host-input.json"
            private_input.write_text(
                json.dumps(
                    {
                        "discovery_precedence": {
                            "reason_code": "not-observed",
                            "status": "unresolved",
                        },
                        "observed_at_utc": "2026-08-18T00:00:00Z",
                        "profile_id": "initial-work-macos-v1",
                        "source_observations": observations,
                    }
                ),
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(REFRESHER),
                "capture-host",
                str(REPOSITORY),
                "--private-input",
                str(private_input),
            ]
            first = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            second = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout, second.stdout)
            self.assertNotIn(str(temporary), first.stdout)
            captured = json.loads(first.stdout)
            self.assertEqual(
                [item["source_id"] for item in captured["source_observations"]],
                sorted(item["id"] for item in source["sources"]),
            )
            installed = next(
                item
                for item in captured["source_observations"]
                if item["source_id"] == "vercel-labs-skill-set"
            )
            self.assertEqual(installed["installations"][0]["entry_count"], 1)
            self.assertEqual(
                installed["installations"][0]["skill_ids"], ["find-skills"]
            )
            self.assertEqual(installed["unobserved_skill_ids"], [])
            partial = next(
                item
                for item in captured["source_observations"]
                if item["source_id"] == "ivan-rolecasting"
            )
            self.assertEqual(
                partial["installations"][0]["skill_ids"],
                ["choosing-agent-models"],
            )
            self.assertEqual(
                partial["unobserved_skill_ids"], ["delegating-cross-agent-work"]
            )
            self.module.validate_host_manifest(
                captured,
                "initial-work-macos-v1",
                (REPOSITORY / SOURCE_MANIFEST).read_bytes(),
                {item["id"]: item for item in source["sources"]},
            )
            route = {
                key: value
                for key, value in installed["installations"][0].items()
                if key != "installation_id"
            }
            frame = json.dumps(
                {
                    "occurrence": 1,
                    "route": route,
                    "source_id": "vercel-labs-skill-set",
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            self.assertEqual(
                installed["installations"][0]["installation_id"],
                "route-sha256:"
                + hashlib.sha256(
                    b"coordinated-installed-source-skill-route-v1\0" + frame
                ).hexdigest(),
            )

            skill_root_metadata = skill_root.stat()
            skill_root_identity = (
                skill_root_metadata.st_dev,
                skill_root_metadata.st_ino,
            )
            original_host_scandir = self.refresher._host_scandir

            class FailingScanner:
                def __enter__(self):
                    return self

                def __exit__(self, _type, _value, _traceback):
                    return False

                def __iter__(self):
                    return self

                def __next__(self):
                    raise RuntimeError(f"private-canary-enumeration {skill_root}")

            def fail_private_enumeration(directory_descriptor):
                observed = os.fstat(directory_descriptor)
                if (observed.st_dev, observed.st_ino) == skill_root_identity:
                    return FailingScanner()
                return original_host_scandir(directory_descriptor)

            stderr = io.StringIO()
            with (
                mock.patch.object(sys, "argv", command[1:]),
                mock.patch.object(sys, "stderr", stderr),
                mock.patch.object(
                    self.refresher,
                    "_host_scandir",
                    autospec=True,
                    side_effect=fail_private_enumeration,
                ),
            ):
                self.assertEqual(self.refresher.main(), 1)
            self.assertEqual(
                stderr.getvalue(),
                "source-skill-lineage-refresh: private host capture failed\n",
            )
            self.assertNotIn(str(skill_root), stderr.getvalue())
            self.assertNotIn("private-canary", stderr.getvalue())

            private_document = json.loads(private_input.read_text(encoding="utf-8"))
            private_installation = next(
                item
                for item in private_document["source_observations"]
                if item["source_id"] == "vercel-labs-skill-set"
            )["installations"][0]
            private_installation["installation_id"] = "ivans-work-macbook"
            private_input.write_text(json.dumps(private_document), encoding="utf-8")
            private_identifier = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(private_identifier.returncode, 0)
            self.assertIn(
                "private host installation schema drift", private_identifier.stderr
            )
            self.assertNotIn("ivans-work-macbook", private_identifier.stderr)

            del private_installation["installation_id"]
            private_unresolved = next(
                item
                for item in private_document["source_observations"]
                if item["status"] == "absent"
            )
            private_unresolved["status"] = "unresolved"
            private_unresolved["reason_code"] = "transport-unavailable"
            private_input.write_text(json.dumps(private_document), encoding="utf-8")
            unresolved = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(unresolved.returncode, 0, unresolved.stderr)
            unresolved_observation = next(
                item
                for item in json.loads(unresolved.stdout)["source_observations"]
                if item["source_id"] == private_unresolved["source_id"]
            )
            self.assertEqual(
                unresolved_observation["reason"],
                self.module.HOST_UNRESOLVED_REASONS["transport-unavailable"][0],
            )
            private_unresolved["reason"] = "route on ivans-work-macbook stopped"
            private_input.write_text(json.dumps(private_document), encoding="utf-8")
            free_form = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(free_form.returncode, 0)
            self.assertIn("private unresolved-host schema drift", free_form.stderr)
            self.assertNotIn("ivans-work-macbook", free_form.stderr)
            private_input.write_text(
                json.dumps(
                    {
                        "discovery_precedence": {
                            "reason_code": "not-observed",
                            "status": "unresolved",
                        },
                        "observed_at_utc": "2026-08-18T00:00:00Z",
                        "profile_id": "initial-work-macos-v1",
                        "source_observations": observations,
                    }
                ),
                encoding="utf-8",
            )

            (skill_root / "private-link").symlink_to("SKILL.md")
            safe = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(safe.returncode, 0, safe.stderr)
            safe_captured = json.loads(safe.stdout)
            safe_installed = next(
                item
                for item in safe_captured["source_observations"]
                if item["source_id"] == "vercel-labs-skill-set"
            )
            self.assertEqual(safe_installed["installations"][0]["entry_count"], 2)

            (skill_root / "private-link").unlink()
            (skill_root / "private-link").symlink_to("../private-canary")
            unsafe = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(unsafe.returncode, 0)
            self.assertIn("contains an unsafe symlink", unsafe.stderr)
            self.assertNotIn(str(temporary), unsafe.stderr)

            (rolecasting_root / "SKILL.md").unlink()
            empty_skill = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(empty_skill.returncode, 0)
            self.assertIn("lacks a regular SKILL.md", empty_skill.stderr)
            self.assertNotIn(str(temporary), empty_skill.stderr)
            (rolecasting_root / "SKILL.md").write_text(
                "# Choose agent models\n", encoding="utf-8"
            )

            observations = json.loads(private_input.read_text(encoding="utf-8"))[
                "source_observations"
            ]
            installed_input = next(
                item
                for item in observations
                if item["source_id"] == "vercel-labs-skill-set"
            )
            installed_input["installations"][0]["skill_roots"][0]["skill_id"] = True
            private_input.write_text(
                json.dumps(
                    {
                        "discovery_precedence": {
                            "reason_code": "not-observed",
                            "status": "unresolved",
                        },
                        "observed_at_utc": "2026-08-18T00:00:00Z",
                        "profile_id": "initial-work-macos-v1",
                        "source_observations": observations,
                    }
                ),
                encoding="utf-8",
            )
            malformed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(malformed.returncode, 0)
            self.assertIn(
                "private host skill id must be a safe identifier", malformed.stderr
            )
            self.assertNotIn(str(temporary), malformed.stderr)

    def test_capture_host_redacts_private_input_io_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            private_input = (
                Path(temporary_directory) / "ivans-work-macbook-private-input.json"
            )
            private_input.write_text("{}\n", encoding="utf-8")
            private_input = private_input.resolve()
            arguments = [
                str(REFRESHER),
                "capture-host",
                str(REPOSITORY),
                "--private-input",
                str(private_input),
            ]
            path_type = type(private_input)
            original_lstat = path_type.lstat
            original_open = self.refresher.os.open

            def fail_lstat(path):
                if path == private_input:
                    raise FileNotFoundError(
                        2, "private-canary-missing", str(private_input)
                    )
                return original_lstat(path)

            def fail_open(path, flags, *args, **kwargs):
                if Path(path) == private_input:
                    raise PermissionError(
                        13, "private-canary-unreadable", str(private_input)
                    )
                return original_open(path, flags, *args, **kwargs)

            for owner, method, failure, message in (
                (
                    path_type,
                    "lstat",
                    fail_lstat,
                    "private host capture input is unavailable",
                ),
                (
                    self.refresher.os,
                    "open",
                    fail_open,
                    "private host capture input cannot be read",
                ),
            ):
                with self.subTest(method=method):
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(sys, "argv", arguments),
                        mock.patch.object(sys, "stderr", stderr),
                        mock.patch.object(
                            owner,
                            method,
                            autospec=owner is path_type,
                            side_effect=failure,
                        ),
                    ):
                        self.assertEqual(self.refresher.main(), 1)
                    self.assertEqual(
                        stderr.getvalue(),
                        f"source-skill-lineage-refresh: {message}\n",
                    )
                    self.assertNotIn(str(private_input), stderr.getvalue())
                    self.assertNotIn("ivans-work-macbook", stderr.getvalue())

            private_key = str(private_input.parent / "private-canary-key")
            private_input.write_text(
                json.dumps({"placeholder": True})[:-1]
                + f',"{private_key}":"one","{private_key}":"two"}}\n',
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with (
                mock.patch.object(sys, "argv", arguments),
                mock.patch.object(sys, "stderr", stderr),
            ):
                self.assertEqual(self.refresher.main(), 1)
            self.assertEqual(
                stderr.getvalue(),
                "source-skill-lineage-refresh: private host capture input is invalid\n",
            )
            self.assertNotIn(private_key, stderr.getvalue())
            self.assertNotIn("private-canary", stderr.getvalue())

            private_input.unlink()
            private_input.symlink_to(private_input.name)
            stderr = io.StringIO()
            with (
                mock.patch.object(sys, "argv", arguments),
                mock.patch.object(sys, "stderr", stderr),
            ):
                self.assertEqual(self.refresher.main(), 1)
            self.assertEqual(
                stderr.getvalue(),
                "source-skill-lineage-refresh: private host capture input must be a regular file\n",
            )
            self.assertNotIn(str(private_input), stderr.getvalue())
            self.assertNotIn("ivans-work-macbook", stderr.getvalue())

    def test_private_host_input_rejects_oversize_before_content_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-input-oversize-canary"
            private_input = temporary / f"{canary}.json"
            limit = 8
            private_input.write_bytes(b"x" * (limit + 1))
            metadata = private_input.stat()
            identity = (metadata.st_dev, metadata.st_ino)
            path_type = type(private_input)
            original_read_bytes = path_type.read_bytes
            original_read = self.refresher.os.read

            def reject_read_bytes(path):
                if path == private_input:
                    raise AssertionError("oversized private input was read unbounded")
                return original_read_bytes(path)

            def reject_content_read(descriptor, count):
                observed = os.fstat(descriptor)
                if (observed.st_dev, observed.st_ino) == identity:
                    raise AssertionError("oversized private input content was read")
                return original_read(descriptor, count)

            with (
                self.refresher.lineage._lineage_lock(
                    REPOSITORY, exclusive=False
                ) as view,
                mock.patch.object(
                    self.refresher,
                    "MAX_PRIVATE_HOST_INPUT_BYTES",
                    limit,
                    create=True,
                ),
                mock.patch.object(
                    path_type,
                    "read_bytes",
                    autospec=True,
                    side_effect=reject_read_bytes,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "read",
                    side_effect=reject_content_read,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._capture_host_locked(view.root, private_input, view)

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_input_streams_valid_json_at_exact_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            private_input = temporary / "private-host-input.json"
            source = json.loads(
                (REPOSITORY / SOURCE_MANIFEST).read_text(encoding="utf-8")
            )
            document = {
                "discovery_precedence": {
                    "reason_code": "not-observed",
                    "status": "unresolved",
                },
                "observed_at_utc": "2026-08-18T00:00:00Z",
                "profile_id": "initial-work-macos-v1",
                "source_observations": [
                    {"source_id": item["id"], "status": "absent"}
                    for item in source["sources"]
                ],
            }
            encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
            limit = 4096
            self.assertLess(len(encoded), limit)
            private_input.write_bytes(encoded + b" " * (limit - len(encoded)))
            metadata = private_input.stat()
            identity = (metadata.st_dev, metadata.st_ino)
            path_type = type(private_input)
            original_read_bytes = path_type.read_bytes
            original_read = self.refresher.os.read
            input_reads = []
            output = io.BytesIO()

            def reject_read_bytes(path):
                if path == private_input:
                    raise AssertionError("exact-limit private input was read unbounded")
                return original_read_bytes(path)

            def record_input_read(descriptor, count):
                observed = os.fstat(descriptor)
                if (observed.st_dev, observed.st_ino) == identity:
                    input_reads.append(count)
                return original_read(descriptor, count)

            with (
                self.refresher.lineage._lineage_lock(
                    REPOSITORY, exclusive=False
                ) as view,
                mock.patch.object(
                    self.refresher,
                    "MAX_PRIVATE_HOST_INPUT_BYTES",
                    limit,
                    create=True,
                ),
                mock.patch.object(
                    path_type,
                    "read_bytes",
                    autospec=True,
                    side_effect=reject_read_bytes,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "read",
                    side_effect=record_input_read,
                ),
                mock.patch.object(
                    self.refresher.sys,
                    "stdout",
                    mock.Mock(buffer=output),
                ),
            ):
                self.refresher._capture_host_locked(view.root, private_input, view)

            self.assertTrue(input_reads)
            self.assertEqual(
                json.loads(output.getvalue())["profile_id"],
                "initial-work-macos-v1",
            )

    def test_private_host_input_rejects_growth_after_stat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-input-growth-canary"
            private_input = temporary / f"{canary}.json"
            raw = b"{}\n"
            private_input.write_bytes(raw)
            metadata = private_input.stat()
            identity = (metadata.st_dev, metadata.st_ino)
            path_type = type(private_input)
            original_read_bytes = path_type.read_bytes
            original_read = self.refresher.os.read
            requests = []

            def reject_read_bytes(path):
                if path == private_input:
                    raise AssertionError("growing private input was read unbounded")
                return original_read_bytes(path)

            def growing_read(descriptor, count):
                observed = os.fstat(descriptor)
                if (observed.st_dev, observed.st_ino) != identity:
                    return original_read(descriptor, count)
                requests.append(count)
                if len(requests) > 1:
                    raise AssertionError("later grown input content was read")
                return raw + b" "

            with (
                self.refresher.lineage._lineage_lock(
                    REPOSITORY, exclusive=False
                ) as view,
                mock.patch.object(
                    self.refresher,
                    "MAX_PRIVATE_HOST_INPUT_BYTES",
                    64,
                    create=True,
                ),
                mock.patch.object(
                    path_type,
                    "read_bytes",
                    autospec=True,
                    side_effect=reject_read_bytes,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "read",
                    side_effect=growing_read,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._capture_host_locked(view.root, private_input, view)

            self.assertEqual(len(requests), 1)
            self.assertLessEqual(requests[0], len(raw) + 1)
            self.assertEqual(
                str(captured.exception),
                "private host capture input cannot be read",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_input_rejects_huge_decimal_without_runtime_guard(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-huge-integer-canary"
            private_input = temporary / f"{canary}.json"
            digits = "7" * 100_000
            raw = f'{{"value":{digits}}}'.encode()
            self.assertLessEqual(len(raw), self.refresher.MAX_PRIVATE_HOST_INPUT_BYTES)
            private_input.write_bytes(raw)
            parse_int_calls = []

            def reject_with_runtime_value_error(_value, *args, **kwargs):
                parse_int = kwargs.get("parse_int")
                if parse_int is None:
                    raise AssertionError("private integer parsing was unbounded")
                parse_int_calls.append(True)
                with self.assertRaises(
                    (self.refresher.lineage.LineageError, ValueError)
                ):
                    parse_int(digits)
                raise ValueError(f"{canary} simulated runtime digit guard")

            with (
                mock.patch.object(
                    self.refresher.lineage.json,
                    "loads",
                    side_effect=reject_with_runtime_value_error,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._load_private_host_input(
                    private_input,
                    self.refresher.time.monotonic() + 30,
                )

            self.assertEqual(parse_int_calls, [True])
            self.assertEqual(
                str(captured.exception), "private host capture input is invalid"
            )
            self.assertIsNone(captured.exception.__cause__)
            rendered = "".join(
                traceback.format_exception(
                    type(captured.exception),
                    captured.exception,
                    captured.exception.__traceback__,
                )
            )
            self.assertNotIn(str(temporary), rendered)
            self.assertNotIn(canary, rendered)

    def test_private_host_input_rejects_every_numeric_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            private_input = Path(temporary_directory) / "private-host-input.json"
            for token in ("0", "-1", "1.0", "1e3", "NaN", "Infinity", "-Infinity"):
                with self.subTest(token=token):
                    private_input.write_text(
                        f'{{"value":{token}}}',
                        encoding="utf-8",
                    )
                    with self.assertRaises(
                        self.refresher.lineage.LineageError
                    ) as captured:
                        self.refresher._load_private_host_input(
                            private_input,
                            self.refresher.time.monotonic() + 30,
                        )
                    self.assertEqual(
                        str(captured.exception),
                        "private host capture input is invalid",
                    )
                    self.assertIsNone(captured.exception.__cause__)

    def test_capture_host_rejects_duplicate_or_overlapping_skill_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "rolecasting"
            nested = root / "nested"
            nested.mkdir(parents=True)
            for skill_root in (root, nested):
                (skill_root / "SKILL.md").write_text(
                    "# Private skill\n", encoding="utf-8"
                )
            source = next(
                item
                for item in json.loads(
                    (REPOSITORY / SOURCE_MANIFEST).read_text(encoding="utf-8")
                )["sources"]
                if item["id"] == "ivan-rolecasting"
            )
            for second_root in (root, nested):
                with self.subTest(second_root=second_root.name):
                    private_installation = {
                        "skill_roots": [
                            {
                                "path": str(root),
                                "skill_id": "choosing-agent-models",
                            },
                            {
                                "path": str(second_root),
                                "skill_id": "delegating-cross-agent-work",
                            },
                        ]
                    }
                    with self.assertRaisesRegex(
                        self.refresher.lineage.LineageError,
                        "physically disjoint",
                    ) as captured:
                        self.refresher._host_installation_identity(
                            private_installation, source
                        )

                    self.assertNotIn(str(root), str(captured.exception))

            claimed_roots = []
            self.refresher._host_installation_identity(
                {
                    "skill_roots": [
                        {
                            "path": str(root),
                            "skill_id": "choosing-agent-models",
                        }
                    ]
                },
                source,
                claimed_roots,
            )
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError, "physically disjoint"
            ):
                self.refresher._host_installation_identity(
                    {
                        "skill_roots": [
                            {
                                "path": str(nested),
                                "skill_id": "delegating-cross-agent-work",
                            }
                        ]
                    },
                    source,
                    claimed_roots,
                )

    def test_private_host_route_failures_drop_path_bearing_causes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "ivans-work-macbook-private-canary"
            source = {"skill_ids": ["private-skill"]}

            def capture(action, message):
                with self.assertRaises(self.refresher.lineage.LineageError) as raised:
                    action()
                self.assertEqual(str(raised.exception), message)
                self.assertIsNone(raised.exception.__cause__)
                rendered = "".join(
                    traceback.format_exception(
                        type(raised.exception),
                        raised.exception,
                        raised.exception.__traceback__,
                    )
                )
                self.assertNotIn(str(temporary), rendered)
                self.assertNotIn(canary, rendered)

            missing = temporary / f"{canary}-missing"
            capture(
                lambda: self.refresher._host_installation_identity(
                    {
                        "skill_roots": [
                            {"path": str(missing), "skill_id": "private-skill"}
                        ]
                    },
                    source,
                ),
                "private host skill root is unavailable",
            )

            root = temporary / f"{canary}-root"
            root.mkdir()
            value = {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]}
            capture(
                lambda: self.refresher._host_installation_identity(value, source),
                "private host skill root lacks a regular SKILL.md",
            )

            (root / "SKILL.md").write_text("# Private skill\n", encoding="utf-8")
            nested = root / f"{canary}.txt"
            nested.write_text("private\n", encoding="utf-8")
            root_metadata = root.stat()
            root_identity = (root_metadata.st_dev, root_metadata.st_ino)
            original_stat = self.refresher.os.stat

            def fail_nested_stat(path, *args, **kwargs):
                directory_descriptor = kwargs.get("dir_fd")
                if directory_descriptor is not None:
                    observed = os.fstat(directory_descriptor)
                if (
                    path == nested.name
                    and directory_descriptor is not None
                    and (observed.st_dev, observed.st_ino) == root_identity
                ):
                    raise PermissionError(13, "private-canary", str(path))
                return original_stat(path, *args, **kwargs)

            with mock.patch.object(
                self.refresher.os, "stat", side_effect=fail_nested_stat
            ):
                capture(
                    lambda: self.refresher._host_installation_identity(value, source),
                    "private host route cannot be enumerated",
                )

            with mock.patch.object(
                self.refresher,
                "_host_regular_tree_entry",
                side_effect=self.refresher.lineage.LineageError(
                    f"cannot read {nested}"
                ),
            ):
                capture(
                    lambda: self.refresher._host_installation_identity(value, source),
                    "private host route cannot be read",
                )

    def test_private_host_capture_rejects_intermediate_directory_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            root = temporary / "private-skill"
            nested = root / "nested"
            nested.mkdir(parents=True)
            (root / "SKILL.md").write_text("#\n", encoding="utf-8")
            (nested / "benign.txt").write_bytes(b"benign")
            canary = "outside-private-canary.bin"
            outside = temporary / "outside"
            outside.mkdir()
            outside_canary = outside / canary
            outside_canary.write_bytes(b"outside-private-canary-bytes")
            outside_metadata = outside.stat()
            outside_identity = (outside_metadata.st_dev, outside_metadata.st_ino)
            displaced = temporary / "displaced-nested"
            replacement = temporary / "replacement-link"
            replacement.symlink_to(outside, target_is_directory=True)
            original_scandir = self.refresher.os.scandir
            original_open = self.refresher.os.open
            wrapped = []
            swapped = []

            class SwapAfterRootScan:
                def __init__(self, entries):
                    self.entries = entries

                def __enter__(self):
                    return self.entries.__enter__()

                def __exit__(self, exception_type, exception, traceback):
                    result = self.entries.__exit__(exception_type, exception, traceback)
                    nested.rename(displaced)
                    replacement.replace(nested)
                    swapped.append(True)
                    return result

            def swapping_scandir(directory):
                entries = original_scandir(directory)
                if not wrapped:
                    wrapped.append(directory)
                    return SwapAfterRootScan(entries)
                return entries

            def reject_outside_open(path, flags, *args, **kwargs):
                directory_descriptor = kwargs.get("dir_fd")
                if directory_descriptor is not None:
                    observed = os.fstat(directory_descriptor)
                if (
                    Path(path).name == canary
                    and flags == self.refresher._HOST_FILE_FLAGS
                    and directory_descriptor is not None
                    and (observed.st_dev, observed.st_ino) == outside_identity
                ):
                    raise AssertionError("outside canary content was opened")
                return original_open(path, flags, *args, **kwargs)

            with (
                mock.patch.object(
                    self.refresher.os,
                    "scandir",
                    side_effect=swapping_scandir,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=reject_outside_open,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._host_installation_identity(
                    {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]},
                    {"skill_ids": ["private-skill"]},
                )

            self.assertEqual(len(wrapped), 1)
            self.assertEqual(swapped, [True])
            self.assertEqual(
                str(captured.exception), "private host route cannot be enumerated"
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_rejects_oversized_blob_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve() / "private-skill"
            root.mkdir()
            (root / "SKILL.md").write_text("#\n", encoding="utf-8")
            oversized = root / "oversized.bin"
            oversized.write_bytes(b"123456")
            path_type = type(root)
            original_read_bytes = path_type.read_bytes

            def reject_oversized_read(path):
                if path == oversized:
                    raise AssertionError("oversized content was read")
                return original_read_bytes(path)

            with (
                mock.patch.object(self.refresher, "MAX_BLOB_BYTES", 5),
                mock.patch.object(
                    path_type,
                    "read_bytes",
                    autospec=True,
                    side_effect=reject_oversized_read,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "^private host capture exceeds limits$",
                ),
            ):
                self.refresher._host_installation_identity(
                    {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]},
                    {"skill_ids": ["private-skill"]},
                )

    def test_private_host_capture_streams_blob_at_exact_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve() / "private-skill"
            root.mkdir()
            (root / "SKILL.md").write_text("#\n", encoding="utf-8")
            payload = root / "payload.bin"
            payload.write_bytes(b"0123456789abcdef")
            path_type = type(root)
            original_read_bytes = path_type.read_bytes

            def reject_payload_read(path):
                if path == payload:
                    raise AssertionError("exact-limit content was read unbounded")
                return original_read_bytes(path)

            with (
                mock.patch.object(self.refresher, "MAX_BLOB_BYTES", 16),
                mock.patch.object(
                    path_type,
                    "read_bytes",
                    autospec=True,
                    side_effect=reject_payload_read,
                ),
            ):
                captured = self.refresher._host_installation_identity(
                    {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]},
                    {
                        "baseline": {"status": "unresolved"},
                        "current": {"status": "unresolved"},
                        "skill_ids": ["private-skill"],
                    },
                )

            self.assertEqual(
                captured,
                {
                    "entry_count": 2,
                    "matched_snapshots": [],
                    "skill_ids": ["private-skill"],
                    "skill_tree_sha256": (
                        "sha256:f4d49e0cb78acb85e1a3d46c4bc03c8e15051db5abccf0d6438c90866ee11d78"
                    ),
                    "total_bytes": 18,
                },
            )

    def test_private_host_capture_shares_materialized_budget_across_routes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-materialized-budget-canary"
            roots = []
            for skill_id in ("first-skill", "second-skill"):
                root = temporary / f"{canary}-{skill_id}"
                root.mkdir()
                (root / "SKILL.md").write_text("#\n", encoding="utf-8")
                (root / "entrypoint-link").symlink_to("SKILL.md")
                roots.append(root)
            sources = {
                "private-source": {
                    "baseline": {"status": "unresolved"},
                    "current": {"status": "unresolved"},
                    "skill_ids": ["first-skill", "second-skill"],
                }
            }
            observations = [
                {
                    "installations": [
                        {"skill_roots": [{"path": str(root), "skill_id": skill_id}]}
                        for root, skill_id in zip(
                            roots, ("first-skill", "second-skill")
                        )
                    ],
                    "source_id": "private-source",
                    "status": "installed",
                }
            ]

            with (
                mock.patch.object(self.refresher, "MAX_MATERIALIZED_BYTES", 19),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._capture_host_observations(observations, sources)

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_stops_enumeration_at_entry_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve() / "private-skill"
            root.mkdir()
            entrypoint = root / "SKILL.md"
            entrypoint.write_text("#\n", encoding="utf-8")
            extra = root / "payload.bin"
            extra.write_bytes(b"payload")
            with os.scandir(root) as directory:
                by_name = {entry.name: entry for entry in directory}
            root_metadata = root.stat()
            root_identity = (root_metadata.st_dev, root_metadata.st_ino)
            original_host_scandir = self.refresher._host_scandir
            original_open = self.refresher.os.open

            def bounded_entries():
                yield by_name[entrypoint.name]
                yield by_name[extra.name]
                raise AssertionError("private enumeration was over-consumed")

            def bounded_scandir(directory_descriptor):
                observed = os.fstat(directory_descriptor)
                if (observed.st_dev, observed.st_ino) == root_identity:
                    return contextlib.closing(bounded_entries())
                return original_host_scandir(directory_descriptor)

            def reject_extra_open(path, flags, *args, **kwargs):
                directory_descriptor = kwargs.get("dir_fd")
                if directory_descriptor is not None:
                    observed = os.fstat(directory_descriptor)
                if (
                    path == extra.name
                    and flags == self.refresher._HOST_FILE_FLAGS
                    and directory_descriptor is not None
                    and (observed.st_dev, observed.st_ino) == root_identity
                ):
                    raise AssertionError("over-limit entry was read")
                return original_open(path, flags, *args, **kwargs)

            with (
                mock.patch.object(self.refresher, "MAX_TREE_ENTRIES", 1),
                mock.patch.object(
                    self.refresher,
                    "_host_scandir",
                    autospec=True,
                    side_effect=bounded_scandir,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=reject_extra_open,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._host_installation_identity(
                    {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]},
                    {"skill_ids": ["private-skill"]},
                )

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )

    def test_private_host_capture_does_not_prebuffer_directory_past_entry_limit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-wide-directory-canary"
            root = temporary / canary
            root.mkdir()
            entrypoint = root / "SKILL.md"
            entrypoint.write_text("#\n", encoding="utf-8")
            payload = root / "payload.bin"
            payload.write_bytes(b"payload")
            original_scandir = self.refresher.os.scandir
            original_open = self.refresher.os.open
            with original_scandir(root) as directory:
                by_name = {entry.name: entry for entry in directory}
            ordered_entries = (by_name["SKILL.md"], by_name["payload.bin"])
            root_metadata = root.stat()
            root_identity = (root_metadata.st_dev, root_metadata.st_ino)
            scans = []

            class GuardedScandir:
                def __init__(self):
                    self.index = 0
                    self.overreads = 0

                def __enter__(self):
                    return self

                def __exit__(self, _type, _value, _traceback):
                    return False

                def __iter__(self):
                    return self

                def __next__(self):
                    if self.index < len(ordered_entries):
                        entry = ordered_entries[self.index]
                        self.index += 1
                        return entry
                    self.overreads += 1
                    raise AssertionError("private directory was prebuffered")

            def guarded_scandir(directory_descriptor):
                observed = os.fstat(directory_descriptor)
                if (observed.st_dev, observed.st_ino) == root_identity:
                    scan = GuardedScandir()
                    scans.append(scan)
                    return scan
                return original_scandir(directory_descriptor)

            def reject_payload_open(path, flags, *args, **kwargs):
                directory_descriptor = kwargs.get("dir_fd")
                if directory_descriptor is not None:
                    observed = os.fstat(directory_descriptor)
                if (
                    path == payload.name
                    and flags == self.refresher._HOST_FILE_FLAGS
                    and directory_descriptor is not None
                    and (observed.st_dev, observed.st_ino) == root_identity
                ):
                    raise AssertionError("over-limit payload was opened")
                return original_open(path, flags, *args, **kwargs)

            with (
                mock.patch.object(self.refresher, "MAX_TREE_ENTRIES", 1),
                mock.patch.object(
                    self.refresher.os, "scandir", side_effect=guarded_scandir
                ),
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=reject_payload_open,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._host_installation_identity(
                    {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]},
                    {"skill_ids": ["private-skill"]},
                )

            self.assertEqual(len(scans), 1)
            self.assertEqual((scans[0].index, scans[0].overreads), (2, 0))
            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(str(root), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_rejects_overlong_relative_path_before_read(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            root = temporary / "private-skill"
            root.mkdir()
            entrypoint = root / "SKILL.md"
            entrypoint.write_text("#\n", encoding="utf-8")
            canary = "private-path-canary"
            extra = root / canary
            extra.write_bytes(b"payload")
            with os.scandir(root) as directory:
                by_name = {entry.name: entry for entry in directory}
            ordered_entries = (by_name[entrypoint.name], by_name[extra.name])
            root_metadata = root.stat()
            root_identity = (root_metadata.st_dev, root_metadata.st_ino)
            original_host_scandir = self.refresher._host_scandir
            original_open = self.refresher.os.open

            def ordered_scandir(directory_descriptor):
                observed = os.fstat(directory_descriptor)
                if (observed.st_dev, observed.st_ino) == root_identity:
                    return contextlib.nullcontext(iter(ordered_entries))
                return original_host_scandir(directory_descriptor)

            def reject_extra_open(path, flags, *args, **kwargs):
                directory_descriptor = kwargs.get("dir_fd")
                if directory_descriptor is not None:
                    observed = os.fstat(directory_descriptor)
                if (
                    path == extra.name
                    and flags == self.refresher._HOST_FILE_FLAGS
                    and directory_descriptor is not None
                    and (observed.st_dev, observed.st_ino) == root_identity
                ):
                    raise AssertionError("overlong path content was read")
                return original_open(path, flags, *args, **kwargs)

            with (
                mock.patch.object(self.refresher, "MAX_TREE_PATH_BYTES", 8),
                mock.patch.object(
                    self.refresher,
                    "_host_scandir",
                    autospec=True,
                    side_effect=ordered_scandir,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=reject_extra_open,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._host_installation_identity(
                    {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]},
                    {"skill_ids": ["private-skill"]},
                )

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_rejects_overlong_component_before_read(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            root = temporary / "private-skill"
            root.mkdir()
            entrypoint = root / "SKILL.md"
            entrypoint.write_text("#\n", encoding="utf-8")
            canary = "canaryxyz"
            extra = root / canary
            extra.write_bytes(b"payload")
            with os.scandir(root) as directory:
                by_name = {entry.name: entry for entry in directory}
            ordered_entries = (by_name[entrypoint.name], by_name[extra.name])
            root_metadata = root.stat()
            root_identity = (root_metadata.st_dev, root_metadata.st_ino)
            original_host_scandir = self.refresher._host_scandir
            original_open = self.refresher.os.open

            def ordered_scandir(directory_descriptor):
                observed = os.fstat(directory_descriptor)
                if (observed.st_dev, observed.st_ino) == root_identity:
                    return contextlib.nullcontext(iter(ordered_entries))
                return original_host_scandir(directory_descriptor)

            def reject_extra_open(path, flags, *args, **kwargs):
                directory_descriptor = kwargs.get("dir_fd")
                if directory_descriptor is not None:
                    observed = os.fstat(directory_descriptor)
                if (
                    path == extra.name
                    and flags == self.refresher._HOST_FILE_FLAGS
                    and directory_descriptor is not None
                    and (observed.st_dev, observed.st_ino) == root_identity
                ):
                    raise AssertionError("overlong component content was read")
                return original_open(path, flags, *args, **kwargs)

            with (
                mock.patch.object(self.refresher, "MAX_TREE_PATH_BYTES", 64),
                mock.patch.object(self.refresher, "MAX_TREE_COMPONENT_BYTES", 8),
                mock.patch.object(
                    self.refresher,
                    "_host_scandir",
                    autospec=True,
                    side_effect=ordered_scandir,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=reject_extra_open,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._host_installation_identity(
                    {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]},
                    {"skill_ids": ["private-skill"]},
                )

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_rejects_overdeep_route_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-depth-canary"
            root = temporary / canary
            root.mkdir()
            entrypoint = root / "SKILL.md"
            entrypoint.write_text("#\n", encoding="utf-8")
            overdeep = root / "a" / "b" / "c"
            overdeep.parent.mkdir(parents=True)
            overdeep.write_bytes(b"payload")
            with os.scandir(root) as directory:
                by_name = {entry.name: entry for entry in directory}
            ordered_entries = (by_name[entrypoint.name], by_name["a"])
            root_metadata = root.stat()
            root_identity = (root_metadata.st_dev, root_metadata.st_ino)
            parent_metadata = overdeep.parent.stat()
            parent_identity = (parent_metadata.st_dev, parent_metadata.st_ino)
            original_host_scandir = self.refresher._host_scandir
            original_open = self.refresher.os.open

            def ordered_scandir(directory_descriptor):
                observed = os.fstat(directory_descriptor)
                if (observed.st_dev, observed.st_ino) == root_identity:
                    return contextlib.nullcontext(iter(ordered_entries))
                return original_host_scandir(directory_descriptor)

            def reject_overdeep_open(path, flags, *args, **kwargs):
                directory_descriptor = kwargs.get("dir_fd")
                if directory_descriptor is not None:
                    observed = os.fstat(directory_descriptor)
                if (
                    path == overdeep.name
                    and flags == self.refresher._HOST_FILE_FLAGS
                    and directory_descriptor is not None
                    and (observed.st_dev, observed.st_ino) == parent_identity
                ):
                    raise AssertionError("overdeep route content was read")
                return original_open(path, flags, *args, **kwargs)

            with (
                mock.patch.object(self.refresher, "MAX_TREE_PATH_BYTES", 64),
                mock.patch.object(self.refresher, "MAX_TREE_COMPONENT_BYTES", 8),
                mock.patch.object(self.refresher, "MAX_TREE_COMPONENTS", 2),
                mock.patch.object(
                    self.refresher,
                    "_host_scandir",
                    autospec=True,
                    side_effect=ordered_scandir,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=reject_overdeep_open,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._host_installation_identity(
                    {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]},
                    {"skill_ids": ["private-skill"]},
                )

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_shares_total_component_budget_across_routes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-total-components-canary"
            roots = tuple(temporary / f"{canary}-{index}" for index in (1, 2))
            for root in roots:
                root.mkdir()
                (root / "SKILL.md").write_text("#\n", encoding="utf-8")
            second_entrypoint = roots[1] / "SKILL.md"
            entries_by_identity = {}
            for root in roots:
                metadata = root.stat()
                identity = (metadata.st_dev, metadata.st_ino)
                with os.scandir(root) as directory:
                    entries_by_identity[identity] = next(iter(directory))
            second_metadata = roots[1].stat()
            second_identity = (second_metadata.st_dev, second_metadata.st_ino)
            original_host_scandir = self.refresher._host_scandir
            original_open = self.refresher.os.open

            def one_entry_per_root(directory_descriptor):
                observed = os.fstat(directory_descriptor)
                identity = (observed.st_dev, observed.st_ino)
                if identity in entries_by_identity:
                    return contextlib.nullcontext(
                        iter((entries_by_identity[identity],))
                    )
                return original_host_scandir(directory_descriptor)

            def reject_second_open(path, flags, *args, **kwargs):
                directory_descriptor = kwargs.get("dir_fd")
                if directory_descriptor is not None:
                    observed = os.fstat(directory_descriptor)
                if (
                    path == second_entrypoint.name
                    and flags == self.refresher._HOST_FILE_FLAGS
                    and directory_descriptor is not None
                    and (observed.st_dev, observed.st_ino) == second_identity
                ):
                    raise AssertionError("second route content was read")
                return original_open(path, flags, *args, **kwargs)

            sources = {
                "private-source": {
                    "baseline": {"status": "unresolved"},
                    "current": {"status": "unresolved"},
                    "skill_ids": ["first-skill", "second-skill"],
                }
            }
            observations = [
                {
                    "installations": [
                        {"skill_roots": [{"path": str(root), "skill_id": skill_id}]}
                        for root, skill_id in zip(
                            roots, ("first-skill", "second-skill")
                        )
                    ],
                    "source_id": "private-source",
                    "status": "installed",
                }
            ]

            with (
                mock.patch.object(self.refresher, "MAX_TREE_PATH_BYTES", 64),
                mock.patch.object(self.refresher, "MAX_TREE_COMPONENT_BYTES", 8),
                mock.patch.object(self.refresher, "MAX_TREE_COMPONENTS", 1),
                mock.patch.object(self.refresher, "MAX_TREE_TOTAL_COMPONENTS", 1),
                mock.patch.object(
                    self.refresher,
                    "_host_scandir",
                    autospec=True,
                    side_effect=one_entry_per_root,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=reject_second_open,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._capture_host_observations(observations, sources)

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_shares_listing_byte_budget_across_routes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-listing-bytes-canary"
            roots = tuple(temporary / f"{canary}-{index}" for index in (1, 2))
            for root in roots:
                root.mkdir()
                (root / "SKILL.md").write_text("#\n", encoding="utf-8")
            second_entrypoint = roots[1] / "SKILL.md"
            entries_by_identity = {}
            for root in roots:
                metadata = root.stat()
                identity = (metadata.st_dev, metadata.st_ino)
                with os.scandir(root) as directory:
                    entries_by_identity[identity] = next(iter(directory))
            second_metadata = roots[1].stat()
            second_identity = (second_metadata.st_dev, second_metadata.st_ino)
            original_host_scandir = self.refresher._host_scandir
            original_open = self.refresher.os.open

            def one_entry_per_root(directory_descriptor):
                observed = os.fstat(directory_descriptor)
                identity = (observed.st_dev, observed.st_ino)
                if identity in entries_by_identity:
                    return contextlib.nullcontext(
                        iter((entries_by_identity[identity],))
                    )
                return original_host_scandir(directory_descriptor)

            def reject_second_open(path, flags, *args, **kwargs):
                directory_descriptor = kwargs.get("dir_fd")
                if directory_descriptor is not None:
                    observed = os.fstat(directory_descriptor)
                if (
                    path == second_entrypoint.name
                    and flags == self.refresher._HOST_FILE_FLAGS
                    and directory_descriptor is not None
                    and (observed.st_dev, observed.st_ino) == second_identity
                ):
                    raise AssertionError("second listing entry was read")
                return original_open(path, flags, *args, **kwargs)

            sources = {
                "private-source": {
                    "baseline": {"status": "unresolved"},
                    "current": {"status": "unresolved"},
                    "skill_ids": ["first-skill", "second-skill"],
                }
            }
            observations = [
                {
                    "installations": [
                        {"skill_roots": [{"path": str(root), "skill_id": skill_id}]}
                        for root, skill_id in zip(
                            roots, ("first-skill", "second-skill")
                        )
                    ],
                    "source_id": "private-source",
                    "status": "installed",
                }
            ]

            with (
                mock.patch.object(self.refresher, "MAX_TREE_ENTRIES", 10),
                mock.patch.object(self.refresher, "MAX_TREE_LISTING_BYTES", 15),
                mock.patch.object(self.refresher, "MAX_TREE_PATH_BYTES", 64),
                mock.patch.object(self.refresher, "MAX_TREE_COMPONENT_BYTES", 64),
                mock.patch.object(self.refresher, "MAX_TREE_COMPONENTS", 4),
                mock.patch.object(self.refresher, "MAX_TREE_TOTAL_COMPONENTS", 10),
                mock.patch.object(self.refresher, "MAX_MATERIALIZED_BYTES", 64),
                mock.patch.object(
                    self.refresher,
                    "_host_scandir",
                    autospec=True,
                    side_effect=one_entry_per_root,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=reject_second_open,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._capture_host_observations(observations, sources)

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_bounds_safe_symlink_target_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-symlink-canary"
            root = temporary / canary
            root.mkdir()
            entrypoint = root / "SKILL.md"
            entrypoint.write_text("#\n", encoding="utf-8")
            link = root / "entrypoint-link"
            link.symlink_to("SKILL.md")
            with os.scandir(root) as directory:
                by_name = {entry.name: entry for entry in directory}
            ordered_entries = (by_name[entrypoint.name], by_name[link.name])
            root_metadata = root.stat()
            root_identity = (root_metadata.st_dev, root_metadata.st_ino)
            original_host_scandir = self.refresher._host_scandir

            def ordered_scandir(directory_descriptor):
                observed = os.fstat(directory_descriptor)
                if (observed.st_dev, observed.st_ino) == root_identity:
                    return contextlib.nullcontext(iter(ordered_entries))
                return original_host_scandir(directory_descriptor)

            source = {
                "baseline": {"status": "unresolved"},
                "current": {"status": "unresolved"},
                "skill_ids": ["private-skill"],
            }
            installation = {
                "skill_roots": [{"path": str(root), "skill_id": "private-skill"}]
            }

            with (
                mock.patch.object(self.refresher, "MAX_MATERIALIZED_BYTES", 64),
                mock.patch.object(
                    self.refresher,
                    "_host_scandir",
                    autospec=True,
                    side_effect=ordered_scandir,
                ),
            ):
                with mock.patch.object(self.refresher, "MAX_SYMLINK_BYTES", 8):
                    exact = self.refresher._host_installation_identity(
                        installation, source
                    )
                self.assertEqual((exact["entry_count"], exact["total_bytes"]), (2, 10))

                with (
                    mock.patch.object(self.refresher, "MAX_SYMLINK_BYTES", 7),
                    self.assertRaises(self.refresher.lineage.LineageError) as captured,
                ):
                    self.refresher._host_installation_identity(installation, source)

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_rejects_expired_deadline_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-deadline-canary"
            root = temporary / canary
            root.mkdir()
            (root / "SKILL.md").write_text("#\n", encoding="utf-8")
            sources = {
                "private-source": {
                    "baseline": {"status": "unresolved"},
                    "current": {"status": "unresolved"},
                    "skill_ids": ["private-skill"],
                }
            }
            observations = [
                {
                    "installations": [
                        {
                            "skill_roots": [
                                {
                                    "path": str(root),
                                    "skill_id": "private-skill",
                                }
                            ]
                        }
                    ],
                    "source_id": "private-source",
                    "status": "installed",
                }
            ]
            moments = iter((0.0, 31.0))

            def monotonic():
                return next(moments, 31.0)

            with (
                mock.patch.object(self.refresher, "MATERIALIZE_TIMEOUT_SECONDS", 30),
                mock.patch.object(
                    self.refresher.time, "monotonic", side_effect=monotonic
                ),
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=AssertionError("expired capture opened content"),
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._capture_host_observations(observations, sources)

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_rechecks_deadline_between_file_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-stream-deadline-canary"
            root = temporary / canary
            root.mkdir()
            (root / "SKILL.md").write_bytes(b"abcd")
            budget = self.refresher._HostCaptureBudget(10.0)
            original_read = self.refresher.os.read
            reads = 0

            def monotonic():
                return 11.0 if reads else 0.0

            def read_first_chunk(descriptor, size):
                nonlocal reads
                if reads:
                    raise AssertionError("later file chunk was consumed")
                chunk = original_read(descriptor, size)
                reads += 1
                return chunk

            with (
                mock.patch.object(self.refresher, "MAX_PROCESS_READ_BYTES", 2),
                mock.patch.object(
                    self.refresher.time, "monotonic", side_effect=monotonic
                ),
                mock.patch.object(
                    self.refresher.os, "read", side_effect=read_first_chunk
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._host_installation_identity(
                    {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]},
                    {"skill_ids": ["private-skill"]},
                    capture_budget=budget,
                )

            self.assertEqual(reads, 1)
            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_private_host_capture_rejects_file_growth_after_stat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-file-growth-canary"
            root = temporary / canary
            root.mkdir()
            (root / "SKILL.md").write_bytes(b"ab")
            requests = []

            def growing_read(_descriptor, count):
                requests.append(count)
                if len(requests) > 1:
                    raise AssertionError("later grown-file content was read")
                if count > 3:
                    raise AssertionError("grown-file read request was unbounded")
                return b"abc"

            with (
                mock.patch.object(self.refresher.os, "read", side_effect=growing_read),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._host_installation_identity(
                    {"skill_roots": [{"path": str(root), "skill_id": "private-skill"}]},
                    {"skill_ids": ["private-skill"]},
                )

            self.assertEqual(len(requests), 1)
            self.assertLessEqual(requests[0], 3)
            self.assertEqual(
                str(captured.exception), "private host route cannot be read"
            )
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_capture_host_locked_shares_deadline_with_input_and_route_passes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-two-pass-deadline-canary"
            skill_root = temporary / canary / "find-skills"
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text("#\n", encoding="utf-8")
            source = json.loads(
                (REPOSITORY / SOURCE_MANIFEST).read_text(encoding="utf-8")
            )
            observations = [
                {
                    "installations": [
                        {
                            "skill_roots": [
                                {
                                    "path": str(skill_root),
                                    "skill_id": "find-skills",
                                }
                            ]
                        }
                    ],
                    "source_id": item["id"],
                    "status": "installed",
                }
                if item["id"] == "vercel-labs-skill-set"
                else {"source_id": item["id"], "status": "absent"}
                for item in source["sources"]
            ]
            private_input = temporary / "private-host-input.json"
            private_input.write_text(
                json.dumps(
                    {
                        "discovery_precedence": {
                            "reason_code": "not-observed",
                            "status": "unresolved",
                        },
                        "observed_at_utc": "2026-08-18T00:00:00Z",
                        "profile_id": "initial-work-macos-v1",
                        "source_observations": observations,
                    }
                ),
                encoding="utf-8",
            )
            path_type = type(private_input)
            original_lstat = path_type.lstat
            original_open = self.refresher.os.open
            original_capture = self.refresher._capture_host_observations
            clock = {"passes": 0, "started": False, "value": 0.0}
            route_deadlines = []
            output = io.BytesIO()

            def monotonic():
                clock["started"] = True
                return clock["value"]

            def require_deadline_before_lstat(path):
                if path == private_input and not clock["started"]:
                    raise AssertionError("input metadata observed before deadline")
                return original_lstat(path)

            def require_deadline_before_open(path, flags, *args, **kwargs):
                if Path(path) == private_input and not clock["started"]:
                    raise AssertionError("input content opened before deadline")
                return original_open(path, flags, *args, **kwargs)

            def capture_with_shared_clock(*arguments, **kwargs):
                clock["passes"] += 1
                route_deadlines.append(
                    arguments[2] if len(arguments) > 2 else kwargs["deadline"]
                )
                clock["value"] = 0.0 if clock["passes"] == 1 else 31.0
                return original_capture(*arguments, **kwargs)

            with (
                self.refresher.lineage._lineage_lock(
                    REPOSITORY, exclusive=False
                ) as view,
                mock.patch.object(self.refresher, "MATERIALIZE_TIMEOUT_SECONDS", 30),
                mock.patch.object(
                    self.refresher.time,
                    "monotonic",
                    side_effect=monotonic,
                ),
                mock.patch.object(
                    path_type,
                    "lstat",
                    autospec=True,
                    side_effect=require_deadline_before_lstat,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=require_deadline_before_open,
                ),
                mock.patch.object(
                    self.refresher,
                    "_capture_host_observations",
                    side_effect=capture_with_shared_clock,
                ),
                mock.patch.object(
                    self.refresher.sys,
                    "stdout",
                    mock.Mock(buffer=output),
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._capture_host_locked(view.root, private_input, view)

            self.assertEqual(clock["passes"], 2)
            self.assertEqual(route_deadlines, [30.0, 30.0])
            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertEqual(output.getvalue(), b"")
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_capture_host_final_validation_reuses_the_private_deadline(self) -> None:
        diagnostic = "private host capture exceeds limits"
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        before = {
            relative: (REPOSITORY / relative).read_bytes()
            for relative in public_artifacts
        }
        source = json.loads((REPOSITORY / SOURCE_MANIFEST).read_text(encoding="utf-8"))
        real_load = self.refresher._load_private_host_input
        real_capture = self.refresher._capture_host_observations
        real_validate_host = self.refresher.lineage.validate_host_manifest
        real_new_budget = self.refresher.lineage._new_validation_budget
        real_privacy_scan = self.refresher.lineage._privacy_scan
        canary = "private-final-host-validation-deadline-canary"

        with tempfile.TemporaryDirectory() as temporary_directory:
            private_input = Path(temporary_directory) / f"{canary}.json"
            private_input.write_text(
                json.dumps(
                    {
                        "discovery_precedence": {
                            "reason_code": "not-observed",
                            "status": "unresolved",
                        },
                        "observed_at_utc": "2026-08-18T00:00:00Z",
                        "profile_id": "initial-work-macos-v1",
                        "source_observations": [
                            {"source_id": item["id"], "status": "absent"}
                            for item in source["sources"]
                        ],
                    }
                ),
                encoding="utf-8",
            )
            private_before = private_input.read_bytes()

            for entrypoint in ("api", "cli"):
                with self.subTest(entrypoint=entrypoint):
                    clock = {"value": 0.0}
                    capture_deadlines = []
                    host_budgets = []
                    host_validation_active = {"value": False}
                    host_budget_factory_calls = []
                    privacy_after_expiry = []

                    def load_with_deadline(
                        path,
                        deadline,
                        capture_deadlines=capture_deadlines,
                    ):
                        capture_deadlines.append(deadline)
                        return real_load(path, deadline)

                    def capture_with_deadline(
                        value,
                        sources,
                        deadline=None,
                        capture_deadlines=capture_deadlines,
                    ):
                        capture_deadlines.append(deadline)
                        return real_capture(value, sources, deadline)

                    def new_budget(
                        *,
                        host_validation_active=host_validation_active,
                        host_budget_factory_calls=host_budget_factory_calls,
                        clock=clock,
                    ):
                        if host_validation_active["value"]:
                            host_budget_factory_calls.append(clock["value"])
                        return real_new_budget()

                    def privacy_scan(
                        value,
                        label,
                        *,
                        capture_deadlines=capture_deadlines,
                        clock=clock,
                        privacy_after_expiry=privacy_after_expiry,
                    ):
                        if capture_deadlines and clock["value"] >= capture_deadlines[0]:
                            privacy_after_expiry.append(label)
                        return real_privacy_scan(value, label)

                    def validate_host_with_expired_deadline(
                        value,
                        profile_id,
                        source_raw,
                        sources,
                        *,
                        budget=None,
                        host_budgets=host_budgets,
                        clock=clock,
                        capture_deadlines=capture_deadlines,
                        host_validation_active=host_validation_active,
                    ):
                        host_budgets.append(budget)
                        clock["value"] = capture_deadlines[0]
                        host_validation_active["value"] = True
                        try:
                            arguments = {} if budget is None else {"budget": budget}
                            return real_validate_host(
                                value,
                                profile_id,
                                source_raw,
                                sources,
                                **arguments,
                            )
                        finally:
                            host_validation_active["value"] = False

                    stdout_buffer = io.BytesIO()
                    stdout = io.TextIOWrapper(stdout_buffer, encoding="utf-8")
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(
                            self.refresher, "MATERIALIZE_TIMEOUT_SECONDS", 30
                        ),
                        mock.patch.object(
                            self.refresher.time,
                            "monotonic",
                            side_effect=lambda clock=clock: clock["value"],
                        ),
                        mock.patch.object(
                            self.refresher,
                            "_load_private_host_input",
                            side_effect=load_with_deadline,
                        ),
                        mock.patch.object(
                            self.refresher,
                            "_capture_host_observations",
                            side_effect=capture_with_deadline,
                        ),
                        mock.patch.object(
                            self.refresher.lineage,
                            "_new_validation_budget",
                            side_effect=new_budget,
                        ),
                        mock.patch.object(
                            self.refresher.lineage,
                            "_privacy_scan",
                            side_effect=privacy_scan,
                        ),
                        mock.patch.object(
                            self.refresher.lineage,
                            "validate_host_manifest",
                            side_effect=validate_host_with_expired_deadline,
                        ),
                        mock.patch.object(sys, "stdout", stdout),
                        mock.patch.object(sys, "stderr", stderr),
                    ):
                        if entrypoint == "api":
                            with self.assertRaises(
                                self.refresher.lineage.LineageError
                            ) as captured:
                                self.refresher.capture_host(REPOSITORY, private_input)
                            self.assertEqual(str(captured.exception), diagnostic)
                            self.assertIsNone(captured.exception.__cause__)
                        else:
                            with mock.patch.object(
                                sys,
                                "argv",
                                [
                                    str(REFRESHER),
                                    "capture-host",
                                    str(REPOSITORY),
                                    "--private-input",
                                    str(private_input),
                                ],
                            ):
                                self.assertEqual(self.refresher.main(), 1)

                    stdout.flush()
                    self.assertEqual(capture_deadlines, [30.0, 30.0, 30.0])
                    self.assertEqual(len(host_budgets), 1)
                    budget = host_budgets[0]
                    self.assertIsNotNone(budget)
                    self.assertEqual(budget.deadline, capture_deadlines[0])
                    self.assertEqual(budget.diagnostic, diagnostic)
                    self.assertEqual(host_budget_factory_calls, [])
                    self.assertEqual(privacy_after_expiry, [])
                    self.assertEqual(stdout_buffer.getvalue(), b"")
                    self.assertEqual(
                        stderr.getvalue(),
                        ""
                        if entrypoint == "api"
                        else f"source-skill-lineage-refresh: {diagnostic}\n",
                    )
                    rendered = stdout_buffer.getvalue().decode() + stderr.getvalue()
                    for private_value in (
                        str(private_input),
                        temporary_directory,
                        canary,
                        "Traceback",
                    ):
                        self.assertNotIn(private_value, rendered)
                    self.assertEqual(private_input.read_bytes(), private_before)
                    self.assertEqual(
                        {
                            relative: (REPOSITORY / relative).read_bytes()
                            for relative in public_artifacts
                        },
                        before,
                    )

    def test_capture_host_final_symlink_rechecks_the_shared_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory).resolve()
            canary = "private-final-symlink-deadline-canary"
            skill_root = temporary / canary / "find-skills"
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text("#\n", encoding="utf-8")
            final_symlink = skill_root / "zz-final-link"
            final_symlink.symlink_to("SKILL.md")
            source = json.loads(
                (REPOSITORY / SOURCE_MANIFEST).read_text(encoding="utf-8")
            )
            observations = [
                {
                    "installations": [
                        {
                            "skill_roots": [
                                {
                                    "path": str(skill_root),
                                    "skill_id": "find-skills",
                                }
                            ]
                        }
                    ],
                    "source_id": item["id"],
                    "status": "installed",
                }
                if item["id"] == "vercel-labs-skill-set"
                else {"source_id": item["id"], "status": "absent"}
                for item in source["sources"]
            ]
            private_input = temporary / "private-host-input.json"
            private_input.write_text(
                json.dumps(
                    {
                        "discovery_precedence": {
                            "reason_code": "not-observed",
                            "status": "unresolved",
                        },
                        "observed_at_utc": "2026-08-18T00:00:00Z",
                        "profile_id": "initial-work-macos-v1",
                        "source_observations": observations,
                    }
                ),
                encoding="utf-8",
            )
            root_metadata = skill_root.stat()
            root_identity = (root_metadata.st_dev, root_metadata.st_ino)
            real_scandir = self.refresher._host_scandir
            real_readlink = self.refresher.os.readlink
            real_load = self.refresher._load_private_host_input
            real_capture = self.refresher._capture_host_observations
            real_budget_type = self.refresher._HostCaptureBudget
            clock = {"value": 0.0}
            input_deadlines = []
            route_deadlines = []
            pass_budgets = []
            root_scans = []
            readlink_passes = []
            second_pass_readlink_clock = []
            output = io.BytesIO()

            class FinalSymlinkScanner:
                def __init__(self, scanner):
                    self.scanner = scanner

                def __enter__(self):
                    entries = list(self.scanner.__enter__())
                    entries.sort(key=lambda item: item.name == final_symlink.name)
                    return iter(entries)

                def __exit__(self, exception_type, exception, traceback):
                    return self.scanner.__exit__(
                        exception_type,
                        exception,
                        traceback,
                    )

            def monotonic():
                return clock["value"]

            def ordered_scandir(descriptor):
                scanner = real_scandir(descriptor)
                metadata = os.fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) == root_identity:
                    root_scans.append(descriptor)
                    return FinalSymlinkScanner(scanner)
                return scanner

            def expire_during_second_pass_first_readlink(path, *args, **kwargs):
                target = real_readlink(path, *args, **kwargs)
                if Path(path).name == final_symlink.name:
                    capture_pass = len(root_scans)
                    readlink_passes.append(capture_pass)
                    if capture_pass == 2 and not second_pass_readlink_clock:
                        second_pass_readlink_clock.append(clock["value"])
                        clock["value"] = 31.0
                return target

            def load_with_shared_deadline(path, deadline):
                input_deadlines.append(deadline)
                return real_load(path, deadline)

            def capture_with_shared_deadline(value, sources, deadline=None):
                route_deadlines.append(deadline)
                return real_capture(value, sources, deadline)

            def pass_budget(deadline):
                budget = real_budget_type(deadline)
                pass_budgets.append(budget)
                return budget

            with (
                self.refresher.lineage._lineage_lock(
                    REPOSITORY, exclusive=False
                ) as view,
                mock.patch.object(self.refresher, "MATERIALIZE_TIMEOUT_SECONDS", 30),
                mock.patch.object(
                    self.refresher.time,
                    "monotonic",
                    side_effect=monotonic,
                ),
                mock.patch.object(
                    self.refresher,
                    "_host_scandir",
                    side_effect=ordered_scandir,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "readlink",
                    side_effect=expire_during_second_pass_first_readlink,
                ),
                mock.patch.object(
                    self.refresher,
                    "_load_private_host_input",
                    side_effect=load_with_shared_deadline,
                ),
                mock.patch.object(
                    self.refresher,
                    "_capture_host_observations",
                    side_effect=capture_with_shared_deadline,
                ),
                mock.patch.object(
                    self.refresher,
                    "_HostCaptureBudget",
                    side_effect=pass_budget,
                ),
                mock.patch.object(
                    self.refresher.sys,
                    "stdout",
                    mock.Mock(buffer=output),
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._capture_host_locked(view.root, private_input, view)

            self.assertEqual(
                str(captured.exception), "private host capture exceeds limits"
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(input_deadlines, [30.0])
            self.assertEqual(route_deadlines, [30.0, 30.0])
            self.assertEqual(len(pass_budgets), 2)
            self.assertEqual(
                [budget.deadline for budget in pass_budgets],
                [30.0, 30.0],
            )
            self.assertEqual(len(root_scans), 2)
            self.assertEqual(second_pass_readlink_clock, [0.0])
            self.assertEqual(
                [capture_pass for capture_pass in readlink_passes if capture_pass == 2],
                [2],
            )
            self.assertEqual(output.getvalue(), b"")
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(canary, str(captured.exception))

    def test_write_rejects_dirty_candidate_bytes_before_replacing_any_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            artifact_paths = (
                SOURCE_MANIFEST,
                CONTRIBUTION_LEDGER,
                *HOST_MANIFESTS,
            )
            before = {
                relative: (clone / relative).read_bytes() for relative in artifact_paths
            }
            package_file = clone / "plugins/artifact-customs/README.md"
            package_file.write_bytes(package_file.read_bytes() + b"private-canary\n")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(clone / "scripts/refresh_source_skill_lineage.py"),
                    "write",
                    str(clone),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=90,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("candidate package tree identity", completed.stderr)
            self.assertNotIn("private-canary", completed.stderr)
            self.assertEqual(
                before,
                {
                    relative: (clone / relative).read_bytes()
                    for relative in artifact_paths
                },
            )

    def test_write_and_recovery_reject_symlinked_release_parent_before_transaction(
        self,
    ) -> None:
        for operation in ("recovery", "write"):
            with (
                self.subTest(operation=operation),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                temporary = Path(temporary_directory)
                first_parent = temporary / "first"
                second_parent = temporary / "second"
                first_parent.mkdir()
                second_parent.mkdir()
                first = self.clone_refresh_fixture(str(first_parent))
                second = self.clone_refresh_fixture(str(second_parent))
                first_git = Path(
                    self.refresher._git(first, "rev-parse", "--absolute-git-dir")
                )
                second_git = Path(
                    self.refresher._git(second, "rev-parse", "--absolute-git-dir")
                )
                first_release = first / "release"
                shutil.rmtree(first_release)
                first_release.symlink_to(second / "release", target_is_directory=True)

                def state(
                    release_link: Path,
                    peer_release: Path,
                    first_git_directory: Path,
                    second_git_directory: Path,
                ) -> dict[str, object]:
                    metadata = release_link.lstat()
                    return {
                        "first_release": (
                            stat.S_IFMT(metadata.st_mode),
                            os.readlink(release_link),
                        ),
                        "second_release": self.module.tree_identity(peer_release),
                        "first_git": self.module.tree_identity(first_git_directory),
                        "second_git": self.module.tree_identity(second_git_directory),
                    }

                state_arguments = (
                    first_release,
                    second / "release",
                    first_git,
                    second_git,
                )
                before = state(*state_arguments)
                new_transaction = mock.Mock(
                    side_effect=self.refresher.lineage.LineageError(
                        "source-lineage transaction creation reached"
                    )
                )
                with (
                    mock.patch.object(
                        self.refresher,
                        "_new_transaction",
                        new_transaction,
                    ),
                    self.assertRaises(self.refresher.lineage.LineageError) as raised,
                ):
                    if operation == "recovery":
                        self.refresher._recover_interrupted_write(first)
                    else:
                        self.refresher.write(first)

                new_transaction.assert_not_called()
                self.assertEqual(state(*state_arguments), before)
                self.assertNotIn(str(temporary), str(raised.exception))

    def test_check_rejects_each_oversize_lineage_document_before_read(self) -> None:
        lineage_documents = (SOURCE_MANIFEST, CONTRIBUTION_LEDGER, *HOST_MANIFESTS)
        public_artifacts = (RESEARCH_REPORT, *lineage_documents)
        diagnostic = "source-lineage validation exceeds limits"
        cli_diagnostic = f"source-skill-lineage-refresh: {diagnostic}\n"

        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            originals = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }
            original_read_bytes = Path.read_bytes
            original_read = os.read

            for relative in lineage_documents:
                original = originals[relative]
                padding = (
                    self.refresher.lineage.MAX_VALIDATION_FILE_BYTES + 1 - len(original)
                )
                self.assertGreater(padding, 0)
                oversized = original + (b" " * padding)

                for entrypoint in ("check", "cli"):
                    with self.subTest(document=relative, entrypoint=entrypoint):
                        target = clone / relative
                        target.write_bytes(oversized)
                        metadata = target.stat()
                        target_identity = (metadata.st_dev, metadata.st_ino)
                        canary = f"private-{relative.name}-{entrypoint}-read-canary"

                        def reject_path_read(
                            path, *, expected=target_identity, message=canary
                        ):
                            opened = path.stat()
                            if (opened.st_dev, opened.st_ino) == expected:
                                raise self.refresher.lineage.LineageError(message)
                            return original_read_bytes(path)

                        def reject_descriptor_read(
                            descriptor,
                            count,
                            *,
                            expected=target_identity,
                            message=canary,
                        ):
                            opened = os.fstat(descriptor)
                            if (opened.st_dev, opened.st_ino) == expected:
                                raise self.refresher.lineage.LineageError(message)
                            return original_read(descriptor, count)

                        stdout = io.StringIO()
                        stderr = io.StringIO()
                        observed = None
                        try:
                            with (
                                mock.patch.object(
                                    type(target),
                                    "read_bytes",
                                    autospec=True,
                                    side_effect=reject_path_read,
                                ),
                                mock.patch.object(
                                    self.refresher.os,
                                    "read",
                                    side_effect=reject_descriptor_read,
                                ),
                                contextlib.redirect_stdout(stdout),
                                contextlib.redirect_stderr(stderr),
                            ):
                                if entrypoint == "check":
                                    with self.assertRaises(
                                        self.refresher.lineage.LineageError
                                    ) as captured:
                                        self.refresher.check(clone)
                                    self.assertEqual(
                                        str(captured.exception), diagnostic
                                    )
                                    self.assertIsNone(captured.exception.__cause__)
                                else:
                                    with mock.patch.object(
                                        self.refresher.sys,
                                        "argv",
                                        [str(REFRESHER), "check", str(clone)],
                                    ):
                                        returncode = self.refresher.main()
                                    self.assertEqual(returncode, 1)

                            self.assertEqual(stdout.getvalue(), "")
                            self.assertEqual(
                                stderr.getvalue(),
                                "" if entrypoint == "check" else cli_diagnostic,
                            )
                            rendered = stdout.getvalue() + stderr.getvalue()
                            for private_value in (
                                str(clone),
                                str(target),
                                target.name,
                                canary,
                                "Traceback",
                            ):
                                self.assertNotIn(private_value, rendered)
                        finally:
                            try:
                                observed = {
                                    path: (clone / path).read_bytes()
                                    for path in public_artifacts
                                }
                            finally:
                                target.write_bytes(original)

                        expected = dict(originals)
                        expected[relative] = oversized
                        self.assertEqual(observed, expected)

    def test_validator_and_check_normalize_unpaired_surrogate_documents(
        self,
    ) -> None:
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        documents = (
            (SOURCE_MANIFEST, "source manifest is not valid JSON"),
            (CONTRIBUTION_LEDGER, "contribution ledger is not valid JSON"),
            (
                HOST_MANIFESTS[0],
                "installed-host manifest: initial-personal-cachyos-v1 is not valid JSON",
            ),
            (
                HOST_MANIFESTS[1],
                "installed-host manifest: initial-work-macos-v1 is not valid JSON",
            ),
        )
        entrypoints = ("validator-api", "validator-cli", "check-api", "check-cli")
        malformed = b'{"private-surrogate-canary":"\\ud800"}\n'

        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            originals = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }

            for relative, diagnostic in documents:
                for entrypoint in entrypoints:
                    with self.subTest(document=relative, entrypoint=entrypoint):
                        target = clone / relative
                        target.write_bytes(malformed)
                        before = {
                            path: (clone / path).read_bytes()
                            for path in public_artifacts
                        }
                        stdout = io.StringIO()
                        stderr = io.StringIO()
                        blocked = mock.Mock(
                            side_effect=AssertionError("private-stable-render-canary")
                        )
                        observed = None
                        try:
                            if entrypoint == "validator-api":
                                with (
                                    contextlib.redirect_stdout(stdout),
                                    contextlib.redirect_stderr(stderr),
                                    self.assertRaises(
                                        self.module.LineageError
                                    ) as captured,
                                ):
                                    self.module.validate_lineage(clone)
                                self.assertEqual(str(captured.exception), diagnostic)
                                self.assertIsNone(captured.exception.__cause__)
                            elif entrypoint == "check-api":
                                with (
                                    mock.patch.object(
                                        self.refresher, "_stable_render", blocked
                                    ),
                                    contextlib.redirect_stdout(stdout),
                                    contextlib.redirect_stderr(stderr),
                                    self.assertRaises(
                                        self.refresher.lineage.LineageError
                                    ) as captured,
                                ):
                                    self.refresher.check(clone)
                                self.assertEqual(str(captured.exception), diagnostic)
                                self.assertIsNone(captured.exception.__cause__)
                                blocked.assert_not_called()
                            else:
                                script = (
                                    clone / "scripts/validate_source_skill_lineage.py"
                                    if entrypoint == "validator-cli"
                                    else clone
                                    / "scripts/refresh_source_skill_lineage.py"
                                )
                                command = [sys.executable, "-B", str(script)]
                                expected_stderr = (
                                    f"source-skill-lineage: {diagnostic}\n"
                                    if entrypoint == "validator-cli"
                                    else f"source-skill-lineage-refresh: {diagnostic}\n"
                                )
                                if entrypoint == "validator-cli":
                                    command.append(str(clone))
                                else:
                                    command.extend(("check", str(clone)))
                                completed = subprocess.run(
                                    command,
                                    text=True,
                                    capture_output=True,
                                    check=False,
                                    timeout=30,
                                )
                                self.assertEqual(completed.returncode, 1)
                                stdout.write(completed.stdout)
                                stderr.write(completed.stderr)
                                self.assertEqual(completed.stderr, expected_stderr)

                            self.assertEqual(stdout.getvalue(), "")
                            rendered = stdout.getvalue() + stderr.getvalue()
                            for private_value in (
                                str(clone),
                                str(target),
                                target.name,
                                "private-surrogate-canary",
                                "private-stable-render-canary",
                                "UnicodeEncodeError",
                                "Traceback",
                            ):
                                self.assertNotIn(private_value, rendered)
                        finally:
                            try:
                                observed = {
                                    path: (clone / path).read_bytes()
                                    for path in public_artifacts
                                }
                            finally:
                                target.write_bytes(originals[relative])

                        self.assertEqual(observed, before)

    def test_validator_and_check_normalize_nested_type_drift(self) -> None:
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        numeric_canary = 987_654_321
        package_canary = "private-non-object-package-canary"

        def numeric_distribution_scope(source):
            source["scope"]["distribution_ids"] = numeric_canary

        def non_object_candidate_package(source):
            source["candidate"]["packages"][0] = package_canary
            source["candidate"]["packages_sha256"] = self.module.canonical_sha256(
                source["candidate"]["packages"]
            )

        cases = (
            (
                "numeric-distribution-scope",
                numeric_distribution_scope,
                "source-lineage schema drift",
            ),
            (
                "non-object-candidate-package",
                non_object_candidate_package,
                "source-lineage schema drift",
            ),
        )
        entrypoints = ("validator-api", "validator-cli", "check-api", "check-cli")

        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / SOURCE_MANIFEST
            originals = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }

            for case_name, mutate, diagnostic in cases:
                source = json.loads(originals[SOURCE_MANIFEST])
                mutate(source)
                source["content_sha256"] = self.module.content_sha256(source)
                malformed = self.module.content_document(source)

                for entrypoint in entrypoints:
                    with self.subTest(case=case_name, entrypoint=entrypoint):
                        target.write_bytes(malformed)
                        before = {
                            path: (clone / path).read_bytes()
                            for path in public_artifacts
                        }
                        stdout = io.StringIO()
                        stderr = io.StringIO()
                        blocked = mock.Mock(
                            side_effect=AssertionError("private-stable-render-canary")
                        )
                        observed = None
                        try:
                            with contextlib.ExitStack() as stack:
                                stack.enter_context(contextlib.redirect_stdout(stdout))
                                stack.enter_context(contextlib.redirect_stderr(stderr))
                                if entrypoint.startswith("check"):
                                    stack.enter_context(
                                        mock.patch.object(
                                            self.refresher, "_stable_render", blocked
                                        )
                                    )

                                if entrypoint == "validator-api":
                                    with self.assertRaises(
                                        self.module.LineageError
                                    ) as captured:
                                        self.module.validate_lineage(clone)
                                    self.assertEqual(
                                        str(captured.exception), diagnostic
                                    )
                                    self.assertIsNone(captured.exception.__cause__)
                                elif entrypoint == "check-api":
                                    with self.assertRaises(
                                        self.refresher.lineage.LineageError
                                    ) as captured:
                                        self.refresher.check(clone)
                                    self.assertEqual(
                                        str(captured.exception), diagnostic
                                    )
                                    self.assertIsNone(captured.exception.__cause__)
                                else:
                                    module = (
                                        self.module
                                        if entrypoint == "validator-cli"
                                        else self.refresher
                                    )
                                    arguments = (
                                        [str(VALIDATOR), str(clone)]
                                        if entrypoint == "validator-cli"
                                        else [str(REFRESHER), "check", str(clone)]
                                    )
                                    prefix = (
                                        "source-skill-lineage"
                                        if entrypoint == "validator-cli"
                                        else "source-skill-lineage-refresh"
                                    )
                                    with mock.patch.object(
                                        module.sys, "argv", arguments
                                    ):
                                        returncode = module.main()
                                    self.assertEqual(returncode, 1)
                                    self.assertEqual(
                                        stderr.getvalue(), f"{prefix}: {diagnostic}\n"
                                    )

                            if entrypoint.startswith("check"):
                                blocked.assert_not_called()
                            self.assertEqual(stdout.getvalue(), "")
                            rendered = stdout.getvalue() + stderr.getvalue()
                            for private_value in (
                                str(clone),
                                str(target),
                                target.name,
                                str(numeric_canary),
                                package_canary,
                                "private-stable-render-canary",
                                "TypeError",
                                "AttributeError",
                                "Traceback",
                            ):
                                self.assertNotIn(private_value, rendered)
                        finally:
                            try:
                                observed = {
                                    path: (clone / path).read_bytes()
                                    for path in public_artifacts
                                }
                            finally:
                                target.write_bytes(originals[SOURCE_MANIFEST])

                        self.assertEqual(observed, before)

    def test_check_and_write_reject_canonical_empty_source_before_work(self) -> None:
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        cases = (
            (
                "check",
                self.refresher.check,
                "_stable_render",
                "source manifest schema drift",
            ),
            (
                "write",
                self.refresher.write,
                "_new_transaction",
                "source-lineage refresh input schema drift",
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / SOURCE_MANIFEST
            original = target.read_bytes()

            for operation_name, operation, blocked_name, diagnostic in cases:
                with self.subTest(operation=operation_name):
                    target.write_bytes(b"{}\n")
                    before = {
                        relative: (clone / relative).read_bytes()
                        for relative in public_artifacts
                    }
                    canary = f"private-{operation_name}-work-canary"
                    blocked = mock.Mock(side_effect=AssertionError(canary))
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    observed = None
                    try:
                        with (
                            mock.patch.object(self.refresher, blocked_name, blocked),
                            contextlib.redirect_stdout(stdout),
                            contextlib.redirect_stderr(stderr),
                            self.assertRaises(
                                self.refresher.lineage.LineageError
                            ) as captured,
                        ):
                            operation(clone)

                        self.assertEqual(str(captured.exception), diagnostic)
                        self.assertIsNone(captured.exception.__cause__)
                        blocked.assert_not_called()
                        self.assertEqual(stdout.getvalue(), "")
                        self.assertEqual(stderr.getvalue(), "")
                        rendered = str(captured.exception)
                        for private_value in (
                            str(clone),
                            str(target),
                            target.name,
                            canary,
                            "KeyError",
                            "TypeError",
                            "Traceback",
                        ):
                            self.assertNotIn(private_value, rendered)
                    finally:
                        try:
                            observed = {
                                relative: (clone / relative).read_bytes()
                                for relative in public_artifacts
                            }
                        finally:
                            target.write_bytes(original)

                    self.assertEqual(observed, before)

    def test_write_rejects_duplicate_candidate_packages_before_git_or_transaction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / SOURCE_MANIFEST
            source = json.loads(target.read_text(encoding="utf-8"))
            package = source["candidate"]["packages"][0]
            source["candidate"]["packages"] = [package] * 2_048
            source["candidate"]["packages_sha256"] = self.module.canonical_sha256(
                source["candidate"]["packages"]
            )
            source["content_sha256"] = self.module.content_sha256(source)
            malformed = self.module.content_document(source)
            self.assertLess(
                len(malformed), self.refresher.lineage.MAX_VALIDATION_FILE_BYTES
            )
            self.assertEqual(
                malformed,
                self.module.content_document(json.loads(malformed)),
            )
            target.write_bytes(malformed)
            before = self.module.tree_identity(clone)
            canary = "private-duplicate-package-work-canary"
            blocked = {
                name: mock.Mock(side_effect=AssertionError(f"{canary}:{name}"))
                for name in (
                    "_package_projection",
                    "_git",
                    "_git_tree_identity",
                    "_git_blob",
                    "_bounded_git_output",
                    "_new_transaction",
                )
            }
            popen = mock.Mock(side_effect=AssertionError(f"{canary}:Popen"))
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                contextlib.ExitStack() as stack,
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                for name, guard in blocked.items():
                    stack.enter_context(mock.patch.object(self.refresher, name, guard))
                stack.enter_context(
                    mock.patch.object(self.refresher.subprocess, "Popen", popen)
                )
                self.refresher.write(clone)

            self.assertEqual(
                str(captured.exception), "source-lineage refresh input schema drift"
            )
            self.assertIsNone(captured.exception.__cause__)
            for guard in (*blocked.values(), popen):
                guard.assert_not_called()
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            rendered = stdout.getvalue() + stderr.getvalue() + str(captured.exception)
            for private_value in (
                str(clone),
                str(target),
                target.name,
                canary,
                "Traceback",
            ):
                self.assertNotIn(private_value, rendered)
            self.assertEqual(target.read_bytes(), malformed)
            self.assertEqual(self.module.tree_identity(clone), before)

    def test_stable_render_shares_one_deadline_with_passes_and_git_helpers(
        self,
    ) -> None:
        captured = {
            relative: (REPOSITORY / relative).read_bytes()
            for relative in (
                RESEARCH_REPORT,
                SOURCE_MANIFEST,
                CONTRIBUTION_LEDGER,
                *HOST_MANIFESTS,
            )
        }
        source = json.loads(captured[SOURCE_MANIFEST])
        package = source["candidate"]["packages"][0]
        commit = source["candidate"]["basis"]["commit_sha1"]
        clock = {"value": 100.0}
        render_deadlines = []
        projection_deadlines = []
        real_render = self.refresher.render_checked_in_documents

        def monotonic():
            return clock["value"]

        def record_projection(repository, value, revision, *, deadline):
            projection_deadlines.append(deadline)
            return value

        def record_render(repository, values, *, deadline):
            render_deadlines.append(deadline)
            return real_render(repository, values, deadline=deadline)

        with (
            mock.patch.object(self.refresher, "MATERIALIZE_TIMEOUT_SECONDS", 30.0),
            mock.patch.object(
                self.refresher.time,
                "monotonic",
                side_effect=monotonic,
            ),
            mock.patch.object(
                self.refresher,
                "render_checked_in_documents",
                side_effect=record_render,
            ),
            mock.patch.object(
                self.refresher,
                "_package_projection",
                side_effect=record_projection,
            ),
        ):
            rendered = self.refresher._stable_render(REPOSITORY, captured)

        self.assertEqual(rendered, captured)
        self.assertEqual(render_deadlines, [130.0, 130.0])
        self.assertEqual(
            projection_deadlines,
            [130.0] * (2 * len(source["candidate"]["packages"])),
        )

        tree_deadlines = []
        blob_deadlines = []

        def tree_identity(repository, revision, relative, *, deadline):
            tree_deadlines.append(deadline)
            return {
                "entry_count": package["entry_count"],
                "skill_tree_sha256": package["package_tree_sha256"],
                "total_bytes": package["total_bytes"],
                "tree_sha1": package["git_tree_sha1"],
            }

        def git_blob(repository, revision, relative, *, deadline):
            blob_deadlines.append(deadline)
            if relative.endswith("/plugin.json"):
                return self.module.content_document({"version": package["version"]})
            return b"identity-artifact"

        with (
            mock.patch.object(
                self.refresher,
                "_git_tree_identity",
                side_effect=tree_identity,
            ),
            mock.patch.object(
                self.refresher,
                "_git_blob",
                side_effect=git_blob,
            ),
        ):
            self.refresher._package_projection(
                REPOSITORY,
                package,
                commit,
                deadline=130.0,
            )

        self.assertEqual(tree_deadlines, [130.0])
        self.assertEqual(
            blob_deadlines,
            [130.0] * (1 + len(package["identity_artifacts"])),
        )

        clock["value"] = 200.0
        expired_render_deadlines = []
        stdout = io.StringIO()
        stderr = io.StringIO()

        def expire_after_first_pass(repository, values, *, deadline):
            expired_render_deadlines.append(deadline)
            clock["value"] = 231.0
            return {}

        with (
            mock.patch.object(self.refresher, "MATERIALIZE_TIMEOUT_SECONDS", 30.0),
            mock.patch.object(
                self.refresher.time,
                "monotonic",
                side_effect=monotonic,
            ),
            mock.patch.object(
                self.refresher,
                "render_checked_in_documents",
                side_effect=expire_after_first_pass,
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(self.refresher.lineage.LineageError) as expired,
        ):
            self.refresher._stable_render(REPOSITORY, captured)

        self.assertEqual(
            str(expired.exception), "source-lineage refresh exceeds limits"
        )
        self.assertIsNone(expired.exception.__cause__)
        self.assertEqual(expired_render_deadlines, [230.0])
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(str(REPOSITORY), str(expired.exception))
        self.assertNotIn("Traceback", str(expired.exception))

    def test_git_projection_helpers_preserve_explicit_deadline_through_all_phases(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(
                temporary, "projection-deadline.git"
            )
            content = b"deadline-bound-content"
            blob = self.write_git_blob(repository, content)
            tree = self.write_git_tree(repository, [("100644", blob, "artifact")])
            commit = self.write_git_commit(repository, tree)
            deadline = time.monotonic() + 113.875

            with (
                mock.patch.object(
                    self.refresher,
                    "_git",
                    wraps=self.refresher._git,
                ) as git,
                mock.patch.object(
                    self.refresher,
                    "_bounded_git_output",
                    wraps=self.refresher._bounded_git_output,
                ) as bounded,
            ):
                identity = self.refresher._git_tree_identity(
                    repository,
                    commit,
                    ".",
                    deadline=deadline,
                )
                observed = self.refresher._git_blob(
                    repository,
                    commit,
                    "artifact",
                    deadline=deadline,
                )

            self.assertEqual(identity["tree_sha1"], tree)
            self.assertEqual(observed, content)
            self.assertEqual(
                [call.kwargs["deadline"] for call in git.call_args_list],
                [deadline] * 3,
            )
            self.assertEqual(
                [call.kwargs["deadline"] for call in bounded.call_args_list],
                [deadline] * 6,
            )

            expired_deadline = 150.125
            canary = "private-expired-render-subprocess-canary"
            invocations = (
                (
                    "tree",
                    lambda: self.refresher._git_tree_identity(
                        repository,
                        commit,
                        ".",
                        deadline=expired_deadline,
                    ),
                ),
                (
                    "blob",
                    lambda: self.refresher._git_blob(
                        repository,
                        commit,
                        "artifact",
                        deadline=expired_deadline,
                    ),
                ),
            )
            for label, invoke in invocations:
                with self.subTest(label=label):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    popen = mock.Mock(side_effect=AssertionError(canary))
                    with (
                        mock.patch.object(
                            self.refresher.time,
                            "monotonic",
                            return_value=expired_deadline + 0.5,
                        ),
                        mock.patch.object(
                            self.refresher,
                            "_git",
                            wraps=self.refresher._git,
                        ) as expired_git,
                        mock.patch.object(
                            self.refresher,
                            "_bounded_git_output",
                            wraps=self.refresher._bounded_git_output,
                        ) as expired_bounded,
                        mock.patch.object(
                            self.refresher.subprocess,
                            "Popen",
                            popen,
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                        self.assertRaises(
                            self.refresher.lineage.LineageError
                        ) as captured,
                    ):
                        invoke()

                    self.assertEqual(
                        str(captured.exception),
                        "refresh Git identity is unavailable",
                    )
                    self.assertIsNone(captured.exception.__cause__)
                    self.assertEqual(
                        [
                            call.kwargs["deadline"]
                            for call in expired_git.call_args_list
                        ],
                        [expired_deadline],
                    )
                    self.assertEqual(
                        [
                            call.kwargs["deadline"]
                            for call in expired_bounded.call_args_list
                        ],
                        [expired_deadline],
                    )
                    popen.assert_not_called()
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(stderr.getvalue(), "")
                    rendered = str(captured.exception)
                    for private_value in (
                        str(repository),
                        repository.name,
                        canary,
                        "Traceback",
                    ):
                        self.assertNotIn(private_value, rendered)

    def test_git_snapshot_post_batch_work_obeys_the_shared_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(
                temporary, "post-batch-deadline.git"
            )
            blob = self.write_git_blob(repository, b"deadline-bound-content")
            tree = self.write_git_tree(
                repository,
                [("100644", blob, "artifact")],
            )
            commit = self.write_git_commit(repository, tree)
            repository_before = self.module.tree_identity(repository)
            canary = "private-post-batch-deadline-canary"
            real_bounded = self.refresher._bounded_git_output
            real_git_blob_sha1 = self.refresher._git_blob_sha1
            real_tree_entry = self.refresher.lineage._tree_entry
            real_tree_identity = self.refresher.lineage._tree_entries_identity
            real_mkdir = Path.mkdir
            real_open = Path.open
            cases = (
                (
                    "capture-git",
                    lambda _destination: self.refresher.capture_git(
                        repository, commit, "."
                    ),
                    "source Git tree exceeds capture limits",
                    4,
                ),
                (
                    "receipt-materialization",
                    lambda destination: self.refresher._materialize_git_commit(
                        repository, commit, destination
                    ),
                    "committed repository snapshot exceeds materialization limits",
                    3,
                ),
            )

            for index, (label, invoke, diagnostic, phase_count) in enumerate(cases):
                with self.subTest(label=label):
                    destination = temporary / f"destination-{index}"
                    clock = {"value": 100.0}
                    deadlines = []
                    final_batches = []
                    post_batch_calls = []
                    destination_operations = []

                    def bounded_with_expiry(
                        *args,
                        _deadlines=deadlines,
                        _final_batches=final_batches,
                        _clock=clock,
                        **kwargs,
                    ):
                        _deadlines.append(kwargs["deadline"])
                        result = real_bounded(*args, **kwargs)
                        if args[1] == ("cat-file", "--batch"):
                            _final_batches.append(args[1])
                            _clock["value"] = kwargs["deadline"]
                        return result

                    def record_post_batch(
                        name,
                        operation,
                        *,
                        _final_batches=final_batches,
                        _post_batch_calls=post_batch_calls,
                    ):
                        def record(*args, **kwargs):
                            if _final_batches:
                                _post_batch_calls.append(name)
                            return operation(*args, **kwargs)

                        return record

                    def record_mkdir(
                        path,
                        *args,
                        _destination=destination,
                        _operations=destination_operations,
                        **kwargs,
                    ):
                        if path == _destination or _destination in path.parents:
                            _operations.append("mkdir")
                        return real_mkdir(path, *args, **kwargs)

                    def record_open(
                        path,
                        *args,
                        _destination=destination,
                        _operations=destination_operations,
                        **kwargs,
                    ):
                        if path == _destination or _destination in path.parents:
                            _operations.append("open")
                        return real_open(path, *args, **kwargs)

                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    failure = None
                    with (
                        mock.patch.object(
                            self.refresher.time,
                            "monotonic",
                            side_effect=lambda clock=clock: clock["value"],
                        ),
                        mock.patch.object(
                            self.refresher,
                            "_bounded_git_output",
                            side_effect=bounded_with_expiry,
                        ),
                        mock.patch.object(
                            self.refresher,
                            "_git_blob_sha1",
                            side_effect=record_post_batch(
                                "blob-hash", real_git_blob_sha1
                            ),
                        ),
                        mock.patch.object(
                            self.refresher.lineage,
                            "_tree_entry",
                            side_effect=record_post_batch(
                                "tree-entry", real_tree_entry
                            ),
                        ),
                        mock.patch.object(
                            self.refresher.lineage,
                            "_tree_entries_identity",
                            side_effect=record_post_batch(
                                "tree-identity", real_tree_identity
                            ),
                        ),
                        mock.patch.object(
                            Path,
                            "mkdir",
                            autospec=True,
                            side_effect=record_mkdir,
                        ),
                        mock.patch.object(
                            Path,
                            "open",
                            autospec=True,
                            side_effect=record_open,
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        try:
                            invoke(destination)
                        except self.refresher.lineage.LineageError as error:
                            failure = error

                    self.assertEqual(len(deadlines), phase_count)
                    self.assertEqual(set(deadlines), {130.0})
                    self.assertEqual(final_batches, [("cat-file", "--batch")])
                    self.assertEqual(
                        {
                            "diagnostic": None if failure is None else str(failure),
                            "destination_exists": destination.exists(),
                            "destination_operations": destination_operations,
                            "post_batch_calls": post_batch_calls,
                            "stderr": stderr.getvalue(),
                            "stdout": stdout.getvalue(),
                        },
                        {
                            "diagnostic": diagnostic,
                            "destination_exists": False,
                            "destination_operations": [],
                            "post_batch_calls": [],
                            "stderr": "",
                            "stdout": "",
                        },
                    )
                    self.assertIsNone(failure.__cause__)
                    self.assertEqual(
                        self.module.tree_identity(repository), repository_before
                    )
                    for private_value in (
                        str(temporary),
                        repository.name,
                        canary,
                        "Traceback",
                    ):
                        self.assertNotIn(
                            private_value,
                            stdout.getvalue() + stderr.getvalue() + str(failure),
                        )

    def test_check_validates_captured_generation_then_rejects_live_json_drift(
        self,
    ) -> None:
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        canary = "private-live-source-read-canary"

        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / SOURCE_MANIFEST
            before = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }
            mutated = before[SOURCE_MANIFEST] + b" "
            target_metadata = target.stat()
            target_identity = (target_metadata.st_dev, target_metadata.st_ino)
            real_capture = self.refresher._capture_checked_in_inputs
            original_read_bytes = Path.read_bytes
            captured_generations = []
            capture_budgets = []
            validated_generations = []

            def capture_then_mutate(view, **kwargs):
                captured, identity = real_capture(view, **kwargs)
                captured_generations.append(dict(captured))
                capture_budgets.append(kwargs.get("budget"))
                if len(captured_generations) == 1:
                    target.write_bytes(mutated)
                return captured, identity

            def reject_live_path_read(path):
                metadata = path.stat()
                if (metadata.st_dev, metadata.st_ino) == target_identity:
                    raise AssertionError(canary)
                return original_read_bytes(path)

            def record_validation(_repository, rendered):
                validated_generations.append(dict(rendered))

            stdout = io.StringIO()
            stderr = io.StringIO()
            observed = None
            try:
                with (
                    mock.patch.object(
                        self.refresher,
                        "_capture_checked_in_inputs",
                        side_effect=capture_then_mutate,
                    ) as capture,
                    mock.patch.object(
                        type(target),
                        "read_bytes",
                        autospec=True,
                        side_effect=reject_live_path_read,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_validate_rendered",
                        side_effect=record_validation,
                    ) as validate_rendered,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(
                        self.refresher.lineage.LineageError
                    ) as captured_error,
                ):
                    self.refresher.check(clone)

                self.assertEqual(
                    str(captured_error.exception),
                    "source-lineage inputs changed while rendered",
                )
                self.assertIsNone(captured_error.exception.__cause__)
                self.assertEqual(capture.call_count, 2)
                validate_rendered.assert_called_once()
            finally:
                try:
                    observed = {
                        relative: (clone / relative).read_bytes()
                        for relative in public_artifacts
                    }
                finally:
                    target.write_bytes(before[SOURCE_MANIFEST])

            expected = dict(before)
            expected[SOURCE_MANIFEST] = mutated
            self.assertEqual(observed, expected)
            self.assertEqual(captured_generations, [before, expected])
            self.assertEqual(validated_generations, [before])
            self.assertEqual(len(capture_budgets), 2)
            self.assertIsNotNone(capture_budgets[0])
            self.assertIs(capture_budgets[0], capture_budgets[1])
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            rendered = str(captured_error.exception)
            for private_value in (
                str(clone),
                str(target),
                target.name,
                canary,
                "Traceback",
            ):
                self.assertNotIn(private_value, rendered)

    def test_check_rejects_repository_evidence_drift_after_validation(self) -> None:
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            source_manifest = json.loads((clone / SOURCE_MANIFEST).read_bytes())
            local_license_receipts = {}
            identity_artifact_receipts = {}
            for source in source_manifest["sources"]:
                for snapshot_name in ("baseline", "current"):
                    snapshot = source[snapshot_name]
                    if snapshot.get("status") != "resolved":
                        continue
                    license_value = snapshot["license"]
                    if license_value.get("status") != "resolved":
                        continue
                    relative = license_value["evidence_ref"]
                    if relative.startswith("https://"):
                        continue
                    digest = license_value["evidence_sha256"]
                    if relative in local_license_receipts:
                        self.assertEqual(local_license_receipts[relative], digest)
                    local_license_receipts[relative] = digest
            for package in source_manifest["candidate"]["packages"]:
                for artifact in package["identity_artifacts"]:
                    relative = artifact["path"]
                    digest = artifact["sha256"]
                    if relative in identity_artifact_receipts:
                        self.assertEqual(identity_artifact_receipts[relative], digest)
                    identity_artifact_receipts[relative] = digest

            receipts = dict(self.module.CONTRIBUTION_EVIDENCE_RECEIPTS)
            self.assertEqual(len(receipts), 22)
            self.assertEqual(len(local_license_receipts), 6)
            self.assertEqual(len(identity_artifact_receipts), 7)
            for inventory in (
                local_license_receipts,
                identity_artifact_receipts,
            ):
                for relative, digest in inventory.items():
                    previous = receipts.get(relative)
                    if previous is not None:
                        self.assertEqual(previous, digest)
                    receipts[relative] = digest
            self.assertEqual(len(receipts), 35)
            self.assertEqual(
                receipts,
                {
                    relative: "sha256:"
                    + hashlib.sha256((clone / relative).read_bytes()).hexdigest()
                    for relative in receipts
                },
            )
            evidence_paths = tuple(Path(relative) for relative in sorted(receipts))
            public_before = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }
            for relative in evidence_paths:
                with self.subTest(relative=relative.as_posix()):
                    target = clone / relative
                    original = target.read_bytes()
                    original_metadata = target.stat()
                    original_mode = stat.S_IMODE(original_metadata.st_mode)
                    replacement = (b"x" if original[:1] != b"x" else b"y") + original[
                        1:
                    ]
                    real_validate_rendered = self.refresher._validate_rendered
                    mutation_calls = []

                    def validate_then_mutate(
                        repository,
                        rendered,
                        real_validation=real_validate_rendered,
                        mutation_target=target,
                        mutation_bytes=replacement,
                        mutation_mode=original_mode,
                        observed=mutation_calls,
                        evidence_path=relative,
                    ):
                        real_validation(repository, rendered)
                        mutation_target.write_bytes(mutation_bytes)
                        os.chmod(mutation_target, mutation_mode)
                        observed.append(evidence_path)

                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    try:
                        with (
                            mock.patch.object(
                                self.refresher,
                                "_validate_rendered",
                                side_effect=validate_then_mutate,
                            ) as validate_rendered,
                            contextlib.redirect_stdout(stdout),
                            contextlib.redirect_stderr(stderr),
                            self.assertRaises(
                                self.refresher.lineage.LineageError
                            ) as captured,
                        ):
                            self.refresher.check(clone)

                        validate_rendered.assert_called_once()
                        self.assertEqual(mutation_calls, [relative])
                        self.assertEqual(
                            str(captured.exception),
                            "source-lineage inputs changed while rendered",
                        )
                        self.assertIsNone(captured.exception.__cause__)
                        self.assertEqual(stdout.getvalue(), "")
                        self.assertEqual(stderr.getvalue(), "")
                        rendered = (
                            stdout.getvalue()
                            + stderr.getvalue()
                            + str(captured.exception)
                        )
                        for private_value in (
                            str(clone),
                            str(target),
                            relative.as_posix(),
                            target.name,
                            "Traceback",
                        ):
                            self.assertNotIn(private_value, rendered)
                        self.assertEqual(target.read_bytes(), replacement)
                        self.assertEqual(
                            {
                                artifact: (clone / artifact).read_bytes()
                                for artifact in public_artifacts
                            },
                            public_before,
                        )
                    finally:
                        target.write_bytes(original)
                        os.chmod(target, original_mode)

                    restored_metadata = target.stat()
                    self.assertEqual(target.read_bytes(), original)
                    self.assertEqual(
                        stat.S_IMODE(restored_metadata.st_mode), original_mode
                    )
                    self.assertEqual(
                        (restored_metadata.st_dev, restored_metadata.st_ino),
                        (original_metadata.st_dev, original_metadata.st_ino),
                    )

    def test_check_rejects_non_receipt_package_drift_after_validation(self) -> None:
        package_paths = (
            Path("plugins/artifact-customs/CHANGELOG.md"),
            Path("plugins/mergecraft/CHANGELOG.md"),
            Path("plugins/rolecasting/CHANGELOG.md"),
            Path("plugins/task-witness/client/task_witness_client.py"),
            Path("plugins/tricritical/CHANGELOG.md"),
            Path("plugins/versionkeeping/CHANGELOG.md"),
        )
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            source_manifest = json.loads((clone / SOURCE_MANIFEST).read_bytes())
            receipt_paths = set(self.module.CONTRIBUTION_EVIDENCE_RECEIPTS)
            for source in source_manifest["sources"]:
                for snapshot_name in ("baseline", "current"):
                    snapshot = source[snapshot_name]
                    if snapshot.get("status") != "resolved":
                        continue
                    license_value = snapshot["license"]
                    if license_value.get("status") != "resolved":
                        continue
                    evidence_ref = license_value["evidence_ref"]
                    if not evidence_ref.startswith("https://"):
                        receipt_paths.add(evidence_ref)
            for package in source_manifest["candidate"]["packages"]:
                receipt_paths.update(
                    artifact["path"] for artifact in package["identity_artifacts"]
                )
            public_before = {
                artifact: (clone / artifact).read_bytes()
                for artifact in public_artifacts
            }
            for index, relative in enumerate(package_paths):
                with self.subTest(relative=relative.as_posix()):
                    self.assertNotIn(relative.as_posix(), receipt_paths)
                    target = clone / relative
                    original = target.read_bytes()
                    original_metadata = target.stat()
                    original_mode = stat.S_IMODE(original_metadata.st_mode)
                    canary = f"private-package-drift-canary-{index}"
                    replacement = canary.encode("ascii") + b"\n"
                    self.assertNotEqual(replacement, original)
                    real_validate_rendered = self.refresher._validate_rendered
                    mutation_calls = []

                    def validate_then_mutate(
                        repository,
                        rendered,
                        real_validation=real_validate_rendered,
                        mutation_target=target,
                        mutation_bytes=replacement,
                        mutation_mode=original_mode,
                        observed=mutation_calls,
                    ):
                        real_validation(repository, rendered)
                        mutation_target.write_bytes(mutation_bytes)
                        os.chmod(mutation_target, mutation_mode)
                        observed.append(mutation_target)

                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    try:
                        with (
                            mock.patch.object(
                                self.refresher,
                                "_validate_rendered",
                                side_effect=validate_then_mutate,
                            ) as validate_rendered,
                            contextlib.redirect_stdout(stdout),
                            contextlib.redirect_stderr(stderr),
                            self.assertRaises(
                                self.refresher.lineage.LineageError
                            ) as captured,
                        ):
                            self.refresher.check(clone)

                        validate_rendered.assert_called_once()
                        self.assertEqual(mutation_calls, [target])
                        self.assertEqual(
                            str(captured.exception),
                            "source-lineage inputs changed while rendered",
                        )
                        self.assertIsNone(captured.exception.__cause__)
                        self.assertEqual(stdout.getvalue(), "")
                        self.assertEqual(stderr.getvalue(), "")
                        rendered = (
                            stdout.getvalue()
                            + stderr.getvalue()
                            + str(captured.exception)
                        )
                        for private_value in (
                            str(clone),
                            str(target),
                            relative.as_posix(),
                            target.name,
                            canary,
                            "Traceback",
                        ):
                            self.assertNotIn(private_value, rendered)
                        self.assertEqual(target.read_bytes(), replacement)
                        self.assertEqual(
                            {
                                artifact: (clone / artifact).read_bytes()
                                for artifact in public_artifacts
                            },
                            public_before,
                        )
                    finally:
                        target.write_bytes(original)
                        os.chmod(target, original_mode)

                    restored_metadata = target.stat()
                    self.assertEqual(target.read_bytes(), original)
                    self.assertEqual(
                        stat.S_IMODE(restored_metadata.st_mode), original_mode
                    )
                    self.assertEqual(
                        (restored_metadata.st_dev, restored_metadata.st_ino),
                        (original_metadata.st_dev, original_metadata.st_ino),
                    )

    def test_check_shares_one_budget_through_final_evidence_recapture(self) -> None:
        canary = "private-final-recapture-enumeration-canary"
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            before = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }
            real_new_budget = self.refresher.lineage._new_validation_budget
            real_capture = self.refresher._capture_checked_in_inputs
            real_leaf_recap = self.refresher._require_repository_evidence_receipts
            real_tree_recap = self.refresher._require_candidate_package_tree_receipts
            real_capture_tree_entries = self.refresher.lineage._capture_tree_entries
            observed_budgets = []
            enumeration_after_expiry = []
            expired = False

            def capture_with_budget(view, **kwargs):
                observed_budgets.append(("capture", kwargs.get("budget")))
                return real_capture(view, **kwargs)

            def leaf_recap_then_expire(view, receipts, budget):
                nonlocal expired
                observed_budgets.append(("leaf", budget))
                self.assertEqual(len(receipts), 35)
                result = real_leaf_recap(view, receipts, budget)
                budget.deadline = float("-inf")
                expired = True
                return result

            def tree_recap_with_budget(view, receipts, budget):
                observed_budgets.append(("tree", budget))
                self.assertEqual(len(receipts), 6)
                return real_tree_recap(view, receipts, budget)

            def reject_enumeration_after_expiry(*args, **kwargs):
                if expired:
                    enumeration_after_expiry.append(args)
                    raise AssertionError(canary)
                return real_capture_tree_entries(*args, **kwargs)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher.lineage,
                    "_new_validation_budget",
                    wraps=real_new_budget,
                ) as new_budget,
                mock.patch.object(
                    self.refresher,
                    "_capture_checked_in_inputs",
                    side_effect=capture_with_budget,
                ),
                mock.patch.object(self.refresher, "_validate_rendered"),
                mock.patch.object(
                    self.refresher,
                    "_stable_render",
                    side_effect=lambda _repository, captured: dict(captured),
                ),
                mock.patch.object(
                    self.refresher,
                    "_require_repository_evidence_receipts",
                    side_effect=leaf_recap_then_expire,
                ),
                mock.patch.object(
                    self.refresher,
                    "_require_candidate_package_tree_receipts",
                    side_effect=tree_recap_with_budget,
                ),
                mock.patch.object(
                    self.refresher.lineage,
                    "_capture_tree_entries",
                    side_effect=reject_enumeration_after_expiry,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.check(clone)

            self.assertEqual(new_budget.call_count, 1)
            self.assertEqual(
                [phase for phase, _budget in observed_budgets],
                ["capture", "capture", "leaf", "tree"],
            )
            shared_budget = observed_budgets[0][1]
            self.assertIsNotNone(shared_budget)
            for _phase, budget in observed_budgets[1:]:
                self.assertIs(budget, shared_budget)
            self.assertTrue(expired)
            self.assertEqual(enumeration_after_expiry, [])
            self.assertEqual(
                str(captured.exception),
                self.refresher.lineage.VALIDATION_LIMIT_DIAGNOSTIC,
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(
                {
                    relative: (clone / relative).read_bytes()
                    for relative in public_artifacts
                },
                before,
            )
            rendered = str(captured.exception)
            for private_value in (
                str(clone),
                canary,
                "Traceback",
            ):
                self.assertNotIn(private_value, rendered)

    def test_check_rejects_checkout_root_swap_after_lock_before_comparison(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            original_parent = temporary / "original-parent"
            replacement_parent = temporary / "replacement-parent"
            original_parent.mkdir()
            replacement_parent.mkdir()
            visible_root = self.clone_refresh_fixture(str(original_parent))
            replacement_root = self.clone_refresh_fixture(str(replacement_parent))
            locked_root = temporary / "locked-repository"

            def snapshot(root: Path) -> dict[str, object]:
                root_metadata = root.lstat()
                git_directory = root / ".git"
                git_metadata = git_directory.lstat()
                return {
                    "root_identity": (root_metadata.st_dev, root_metadata.st_ino),
                    "root_tree": self.module.tree_identity(root),
                    "git_identity": (git_metadata.st_dev, git_metadata.st_ino),
                    "git_tree": self.module.tree_identity(git_directory),
                }

            locked_before = snapshot(visible_root)
            replacement_before = snapshot(replacement_root)
            real_binding = self.refresher.lineage._require_lineage_view_binding
            swapped = False

            def bind_then_swap(*arguments) -> None:
                nonlocal swapped
                real_binding(*arguments)
                if not swapped:
                    visible_root.rename(locked_root)
                    replacement_root.rename(visible_root)
                    swapped = True

            with (
                mock.patch.object(
                    self.refresher.lineage,
                    "_require_lineage_view_binding",
                    side_effect=bind_then_swap,
                ),
                mock.patch.object(self.refresher, "_stable_render") as render,
                mock.patch.object(self.refresher, "_atomic_write") as write,
                mock.patch.object(self.refresher, "_rename_at") as replace,
                mock.patch.object(self.refresher, "_fsync_descriptor") as fsync,
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.check(visible_root)

            self.assertTrue(swapped)
            self.assertEqual(snapshot(locked_root), locked_before)
            self.assertEqual(snapshot(visible_root), replacement_before)
            for operation, label in (
                (render, "byte comparison"),
                (write, "artifact write"),
                (replace, "artifact rename"),
                (fsync, "directory fsync"),
            ):
                self.assertEqual(operation.call_count, 0, f"{label} reached")
            self.assertEqual(
                str(captured.exception), "source-lineage artifact tree drift"
            )
            self.assertNotIn(str(temporary), str(captured.exception))

    def test_receipt_rejects_persistent_checkout_root_swap_before_publication(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            original_parent = temporary / "original-parent"
            replacement_parent = temporary / "replacement-parent"
            original_parent.mkdir()
            replacement_parent.mkdir()
            visible_root = self.clone_refresh_fixture(str(original_parent))
            replacement_root = self.clone_refresh_fixture(str(replacement_parent))
            locked_root = temporary / "locked-repository"
            output = self.stable_receipt_output("root-swap")

            def snapshot(root: Path) -> dict[str, object]:
                root_metadata = root.lstat()
                git_directory = root / ".git"
                git_metadata = git_directory.lstat()
                return {
                    "root_identity": (root_metadata.st_dev, root_metadata.st_ino),
                    "root_tree": self.module.tree_identity(root),
                    "git_identity": (git_metadata.st_dev, git_metadata.st_ino),
                    "git_tree": self.module.tree_identity(git_directory),
                }

            locked_before = snapshot(visible_root)
            replacement_before = snapshot(replacement_root)
            real_binding = self.refresher.lineage._require_lineage_view_binding
            real_open = self.refresher.os.open
            swapped = False
            publication_opens = []

            def bind_then_swap(*arguments) -> None:
                nonlocal swapped
                real_binding(*arguments)
                if not swapped:
                    visible_root.rename(locked_root)
                    replacement_root.rename(visible_root)
                    swapped = True

            def record_open(path, flags, mode=0o777, *, dir_fd=None):
                if dir_fd is not None and os.fspath(path) == output.name:
                    publication_opens.append((path, flags, dir_fd))
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    self.refresher.lineage,
                    "_require_lineage_view_binding",
                    side_effect=bind_then_swap,
                ),
                mock.patch.object(self.refresher.os, "open", side_effect=record_open),
                mock.patch.object(
                    self.refresher, "_external_receipt_parent"
                ) as external_parent,
                mock.patch.object(self.refresher, "_publish_receipt_locked") as publish,
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.receipt(visible_root, output, "2026-08-18T00:00:00Z")

            self.assertTrue(swapped)
            self.assertEqual(snapshot(locked_root), locked_before)
            self.assertEqual(snapshot(visible_root), replacement_before)
            external_parent.assert_not_called()
            publish.assert_not_called()
            self.assertEqual(publication_opens, [])
            self.assertFalse(output.exists())
            self.assertEqual(
                str(captured.exception), "source-lineage artifact tree drift"
            )
            self.assertNotIn(str(temporary), str(captured.exception))

    def test_capture_host_rejects_persistent_checkout_root_swap_without_stdout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            original_parent = temporary / "original-parent"
            replacement_parent = temporary / "replacement-parent"
            original_parent.mkdir()
            replacement_parent.mkdir()
            visible_root = self.clone_refresh_fixture(str(original_parent))
            replacement_root = self.clone_refresh_fixture(str(replacement_parent))
            locked_root = temporary / "locked-repository"
            private_input = temporary / "private-host-input.json"
            private_input.write_text("{}\n", encoding="utf-8")

            def snapshot(root: Path) -> dict[str, object]:
                root_metadata = root.lstat()
                git_directory = root / ".git"
                git_metadata = git_directory.lstat()
                return {
                    "root_identity": (root_metadata.st_dev, root_metadata.st_ino),
                    "root_tree": self.module.tree_identity(root),
                    "git_identity": (git_metadata.st_dev, git_metadata.st_ino),
                    "git_tree": self.module.tree_identity(git_directory),
                }

            locked_before = snapshot(visible_root)
            replacement_before = snapshot(replacement_root)
            real_binding = self.refresher.lineage._require_lineage_view_binding
            swapped = False
            stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")

            def bind_then_swap(*arguments) -> None:
                nonlocal swapped
                real_binding(*arguments)
                if not swapped:
                    visible_root.rename(locked_root)
                    replacement_root.rename(visible_root)
                    swapped = True

            with (
                mock.patch.object(
                    self.refresher.lineage,
                    "_require_lineage_view_binding",
                    side_effect=bind_then_swap,
                ),
                mock.patch.object(self.refresher, "_load") as load,
                mock.patch.object(sys, "stdout", stdout),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.capture_host(visible_root, private_input)

            stdout.flush()
            self.assertTrue(swapped)
            self.assertEqual(snapshot(locked_root), locked_before)
            self.assertEqual(snapshot(visible_root), replacement_before)
            load.assert_not_called()
            self.assertEqual(stdout.buffer.getvalue(), b"")
            self.assertEqual(
                str(captured.exception), "source-lineage artifact tree drift"
            )
            self.assertNotIn(str(temporary), str(captured.exception))

    def test_write_rejects_checkout_root_swap_after_lock_before_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            original_parent = temporary / "original-parent"
            replacement_parent = temporary / "replacement-parent"
            original_parent.mkdir()
            replacement_parent.mkdir()
            visible_root = self.clone_refresh_fixture(str(original_parent))
            replacement_root = self.clone_refresh_fixture(str(replacement_parent))
            locked_root = temporary / "locked-repository"

            def snapshot(root: Path) -> dict[str, object]:
                root_metadata = root.lstat()
                git_directory = root / ".git"
                git_metadata = git_directory.lstat()
                return {
                    "root_identity": (root_metadata.st_dev, root_metadata.st_ino),
                    "root_tree": self.module.tree_identity(root),
                    "git_identity": (git_metadata.st_dev, git_metadata.st_ino),
                    "git_tree": self.module.tree_identity(git_directory),
                }

            locked_before = snapshot(visible_root)
            replacement_before = snapshot(replacement_root)
            real_binding = self.refresher.lineage._require_lineage_view_binding
            swapped = False

            def bind_then_swap(*arguments) -> None:
                nonlocal swapped
                real_binding(*arguments)
                if not swapped:
                    visible_root.rename(locked_root)
                    replacement_root.rename(visible_root)
                    swapped = True

            forbidden = self.refresher.lineage.LineageError(
                "transaction discovery reached"
            )
            with (
                mock.patch.object(
                    self.refresher.lineage,
                    "_require_lineage_view_binding",
                    side_effect=bind_then_swap,
                ),
                mock.patch.object(
                    self.refresher,
                    "_transaction_directories",
                    side_effect=forbidden,
                ) as discover,
                mock.patch.object(self.refresher, "_new_transaction") as create,
                mock.patch.object(self.refresher, "_rename_at") as replace,
                mock.patch.object(self.refresher, "_retain_transaction") as retain,
                mock.patch.object(self.refresher, "_fsync_descriptor") as fsync,
            ):
                try:
                    self.refresher.write(visible_root)
                except self.refresher.lineage.LineageError as error:
                    raised = error
                else:
                    raised = None

            self.assertTrue(swapped)
            self.assertEqual(snapshot(locked_root), locked_before)
            self.assertEqual(snapshot(visible_root), replacement_before)
            for operation, label in (
                (discover, "transaction discovery"),
                (create, "transaction creation"),
                (replace, "transaction rename"),
                (retain, "transaction retention"),
                (fsync, "directory fsync"),
            ):
                self.assertEqual(operation.call_count, 0, f"{label} reached")
            self.assertIs(type(raised), self.refresher.lineage.LineageError)
            self.assertEqual(str(raised), "source-lineage artifact tree drift")
            self.assertNotIn(str(temporary), str(raised))

    def test_write_root_drift_after_first_forward_rename_restores_original(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            original_parent = temporary / "original-parent"
            replacement_parent = temporary / "replacement-parent"
            original_parent.mkdir()
            replacement_parent.mkdir()
            visible_root = self.clone_refresh_fixture(str(original_parent))
            replacement_root = self.clone_refresh_fixture(str(replacement_parent))
            locked_root = temporary / "locked-repository"

            def snapshot(root: Path) -> dict[str, object]:
                metadata = root.lstat()
                return {
                    "identity": (metadata.st_dev, metadata.st_ino),
                    "tree": self.module.tree_identity(root),
                }

            original_public_before = snapshot(visible_root / LINEAGE_ROOT)
            replacement_before = snapshot(replacement_root)
            real_rename_noreplace = self.refresher._rename_noreplace_at
            swapped = False
            generation_moves = []

            def rename_then_substitute_root(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage artifact publication failed",
            ):
                nonlocal swapped
                if source_name in {
                    LINEAGE_ROOT.name,
                    "previous",
                } and destination_name in {
                    LINEAGE_ROOT.name,
                    "previous",
                }:
                    generation_moves.append((source_name, destination_name))
                result = real_rename_noreplace(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )
                if (
                    source_name == LINEAGE_ROOT.name
                    and destination_name == "previous"
                    and not swapped
                ):
                    visible_root.rename(locked_root)
                    replacement_root.rename(visible_root)
                    swapped = True
                return result

            with (
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=rename_then_substitute_root,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(visible_root)

            self.assertTrue(swapped)
            self.assertEqual(
                generation_moves,
                [
                    (LINEAGE_ROOT.name, "previous"),
                    ("previous", LINEAGE_ROOT.name),
                ],
            )
            self.assertEqual(
                snapshot(locked_root / LINEAGE_ROOT), original_public_before
            )
            self.assertEqual(snapshot(visible_root), replacement_before)
            self.assertEqual(
                str(captured.exception), "source-lineage artifact tree drift"
            )
            self.assertNotIn(str(temporary), str(captured.exception))

    def test_write_rejects_release_path_swap_after_lock_before_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            clone = self.clone_refresh_fixture(temporary_directory)
            visible_release = clone / "release"
            locked_release = temporary / "locked-release"
            replacement_release = temporary / "replacement-release"
            shutil.copytree(visible_release, replacement_release, symlinks=True)

            def snapshot(path: Path) -> tuple[int, int, dict]:
                metadata = path.lstat()
                return (
                    metadata.st_dev,
                    metadata.st_ino,
                    self.module.tree_identity(path),
                )

            locked_before = snapshot(visible_release)
            replacement_before = snapshot(replacement_release)
            real_binding = self.refresher.lineage._require_lineage_view_binding
            swapped = False

            def bind_then_swap(*arguments) -> None:
                nonlocal swapped
                real_binding(*arguments)
                if not swapped:
                    visible_release.rename(locked_release)
                    replacement_release.rename(visible_release)
                    swapped = True

            forbidden = self.refresher.lineage.LineageError(
                "transaction discovery reached"
            )
            with (
                mock.patch.object(
                    self.refresher.lineage,
                    "_require_lineage_view_binding",
                    side_effect=bind_then_swap,
                ),
                mock.patch.object(
                    self.refresher,
                    "_transaction_directories",
                    side_effect=forbidden,
                ) as discover,
                mock.patch.object(self.refresher, "_new_transaction") as create,
                mock.patch.object(self.refresher, "_rename_at") as replace,
                mock.patch.object(self.refresher, "_retain_transaction") as retain,
                mock.patch.object(self.refresher, "_fsync_descriptor") as fsync,
            ):
                try:
                    self.refresher.write(clone)
                except self.refresher.lineage.LineageError as error:
                    raised = error
                else:
                    raised = None

            self.assertTrue(swapped)
            self.assertEqual(snapshot(locked_release), locked_before)
            self.assertEqual(snapshot(visible_release), replacement_before)
            for operation, label in (
                (discover, "transaction discovery"),
                (create, "transaction creation"),
                (replace, "transaction rename"),
                (retain, "transaction retention"),
                (fsync, "directory fsync"),
            ):
                self.assertEqual(operation.call_count, 0, f"{label} reached")
            self.assertIs(type(raised), self.refresher.lineage.LineageError)
            self.assertEqual(str(raised), "source-lineage artifact tree drift")
            self.assertNotIn(str(temporary), str(raised))

    def test_write_rejects_private_report_before_creating_a_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            private_report = Path(temporary_directory) / "private-canary-report.md"
            private_report.write_text(
                "/Users/private-canary/source lineage\n", encoding="utf-8"
            )
            report = clone / RESEARCH_REPORT
            report.unlink()
            report.symlink_to(private_report)

            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "research report must be a regular file",
            ) as captured:
                self.refresher.write(clone)

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertTrue(report.is_symlink())
            self.assertEqual(
                list((clone / "release").glob(".source-lineage-transaction-*")), []
            )
            self.assertEqual(
                list((clone / "release").glob(".source-lineage-preparation-*")), []
            )

    def test_write_rejects_an_unmanaged_lineage_file_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            extra = clone / LINEAGE_ROOT / "private-canary-note.txt"
            extra.write_text("preserve me\n", encoding="utf-8")

            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "source-lineage artifact tree drift",
            ) as captured:
                self.refresher.write(clone)

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertEqual(extra.read_text(encoding="utf-8"), "preserve me\n")
            self.assertEqual(
                list((clone / "release").glob(".source-lineage-transaction-*")), []
            )

    def test_recovery_rejects_unowned_prefix_collisions_without_deleting_them(
        self,
    ) -> None:
        for with_canary in (False, True):
            with (
                self.subTest(with_canary=with_canary),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                clone = self.clone_refresh_fixture(temporary_directory)
                collision = clone / "release" / ".source-lineage-transaction-notes"
                collision.mkdir()
                canary = collision / "staged" / "private-canary-note.txt"
                if with_canary:
                    canary.parent.mkdir()
                    canary.write_text("preserve me\n", encoding="utf-8")

                with self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "source-lineage recovery state is unowned",
                ) as captured:
                    self.refresher.write(clone)

                self.assertNotIn("private-canary", str(captured.exception))
                self.assertTrue(collision.is_dir())
                if with_canary:
                    self.assertEqual(
                        canary.read_text(encoding="utf-8"), "preserve me\n"
                    )

    def test_recovery_namespace_scans_are_lazy_bounded_and_pathless(self) -> None:
        scan_canary = "private-recovery-scan-overconsumption-canary"
        prebuffer_canary = "private-recovery-listdir-canary"
        entry_limit = self.refresher.MAX_TREE_ENTRIES

        class Entry:
            def __init__(self, name: str):
                self.name = name

        class LazyEntries:
            def __init__(self, names):
                self.names = iter(names)
                self.consumed = []
                self.closed = False

            def __enter__(self):
                return self

            def __exit__(self, _type, _value, _traceback):
                self.close()
                return False

            def __iter__(self):
                return self

            def __next__(self):
                name = next(self.names)
                if name == scan_canary:
                    raise AssertionError(scan_canary)
                self.consumed.append(name)
                return Entry(name)

            def close(self):
                self.closed = True

        def snapshot(root: Path):
            observed = []
            for path in (root, *sorted(root.rglob("*"))):
                metadata = path.lstat()
                relative = "." if path == root else path.relative_to(root).as_posix()
                if stat.S_ISDIR(metadata.st_mode):
                    kind = "directory"
                    content = None
                elif stat.S_ISREG(metadata.st_mode):
                    kind = "file"
                    content = path.read_bytes()
                elif stat.S_ISLNK(metadata.st_mode):
                    kind = "symlink"
                    content = os.readlink(path)
                else:
                    kind = "special"
                    content = None
                observed.append(
                    (
                        relative,
                        kind,
                        stat.S_IMODE(metadata.st_mode),
                        metadata.st_size,
                        content,
                    )
                )
            return observed

        def descriptor_identity(path):
            if type(path) is not int:
                return None
            metadata = os.fstat(path)
            return metadata.st_dev, metadata.st_ino

        def create_transaction(release: Path, name: str) -> Path:
            transaction = release / name
            transaction.mkdir(mode=0o700)
            transaction.chmod(0o700)
            (transaction / self.refresher.TRANSACTION_MARKER).write_bytes(
                self.refresher._transaction_marker(name)
            )
            return transaction

        first_transaction = f"{self.refresher.TRANSACTION_PREFIX}first"
        second_transaction = f"{self.refresher.TRANSACTION_PREFIX}second"

        def over_limit_names():
            for index in range(entry_limit + 1):
                yield f"unrelated-release-entry-{index:05d}"
            yield scan_canary

        def two_transaction_names():
            for index in range(257):
                yield f"unrelated-release-entry-{index:03d}"
            yield first_transaction
            yield second_transaction
            yield scan_canary

        release_cases = (
            ("entry-limit", over_limit_names, (), entry_limit + 1),
            (
                "second-transaction",
                two_transaction_names,
                (first_transaction, second_transaction),
                259,
            ),
        )
        for name, names, transaction_names, expected_consumption in release_cases:
            with (
                self.subTest(name=name),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                clone = self.clone_refresh_fixture(temporary_directory)
                release = clone / "release"
                for transaction_name in transaction_names:
                    create_transaction(release, transaction_name)
                before = snapshot(release)
                release_metadata = release.stat()
                release_identity = (
                    release_metadata.st_dev,
                    release_metadata.st_ino,
                )
                scanner = LazyEntries(names())
                real_scandir = os.scandir
                real_listdir = os.listdir

                def scoped_scandir(
                    path,
                    target_identity=release_identity,
                    target_scanner=scanner,
                    fallback=real_scandir,
                ):
                    if descriptor_identity(path) == target_identity:
                        return target_scanner
                    return fallback(path)

                def reject_release_listdir(
                    path,
                    target_identity=release_identity,
                    fallback=real_listdir,
                ):
                    if descriptor_identity(path) == target_identity:
                        raise AssertionError(prebuffer_canary)
                    return fallback(path)

                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        self.refresher.os,
                        "scandir",
                        side_effect=scoped_scandir,
                    ),
                    mock.patch.object(
                        self.refresher.os,
                        "listdir",
                        side_effect=reject_release_listdir,
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(self.refresher.lineage.LineageError) as captured,
                ):
                    self.refresher.write(clone)

                self.assertEqual(
                    str(captured.exception),
                    "source-lineage recovery state is ambiguous",
                )
                self.assertIsNone(captured.exception.__cause__)
                self.assertEqual(len(scanner.consumed), expected_consumption)
                self.assertTrue(scanner.closed)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(snapshot(release), before)
                for private_value in (
                    str(clone),
                    scan_canary,
                    prebuffer_canary,
                    "Traceback",
                ):
                    self.assertNotIn(private_value, str(captured.exception))

        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            release = clone / "release"
            transaction_name = f"{self.refresher.TRANSACTION_PREFIX}owned"
            transaction = create_transaction(release, transaction_name)
            for child in ("failed", "previous", "staged"):
                (transaction / child).mkdir()
            unknown = "private-recovery-unknown-child"
            (transaction / unknown).mkdir()
            before = snapshot(release)
            transaction_metadata = transaction.stat()
            transaction_identity = (
                transaction_metadata.st_dev,
                transaction_metadata.st_ino,
            )

            def transaction_names():
                yield self.refresher.TRANSACTION_MARKER
                yield "failed"
                yield "previous"
                yield "staged"
                yield unknown
                yield scan_canary

            scanner = LazyEntries(transaction_names())
            real_scandir = os.scandir
            real_listdir = os.listdir

            def scoped_scandir(path):
                if descriptor_identity(path) == transaction_identity:
                    return scanner
                return real_scandir(path)

            def reject_transaction_listdir(path):
                if descriptor_identity(path) == transaction_identity:
                    raise AssertionError(prebuffer_canary)
                return real_listdir(path)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher.os,
                    "scandir",
                    side_effect=scoped_scandir,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "listdir",
                    side_effect=reject_transaction_listdir,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            self.assertEqual(
                str(captured.exception), "source-lineage recovery state is invalid"
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(len(scanner.consumed), 5)
            self.assertTrue(scanner.closed)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(snapshot(release), before)
            for private_value in (
                str(clone),
                unknown,
                scan_canary,
                prebuffer_canary,
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_recovery_rejects_a_persistently_replaced_owner_before_rename(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            transaction_name = f"{self.refresher.TRANSACTION_PREFIX}owner-race"
            transaction = clone / "release" / transaction_name
            previous = transaction / "previous"
            transaction.mkdir(mode=0o700)
            transaction.chmod(0o700)
            marker = transaction / self.refresher.TRANSACTION_MARKER
            expected_marker = self.refresher._transaction_marker(transaction_name)
            self.refresher._atomic_write(marker, expected_marker)
            marker_metadata = marker.stat()
            marker_identity = marker_metadata.st_dev, marker_metadata.st_ino
            invalid_marker = b"private-owner-marker-canary".ljust(
                len(expected_marker), b"x"
            )[: len(expected_marker)]
            self.assertEqual(len(invalid_marker), len(expected_marker))
            self.assertNotEqual(invalid_marker, expected_marker)
            shutil.copytree(target, previous)
            shutil.rmtree(target)
            real_read = self.refresher.os.read
            real_rename = self.refresher._rename_at
            replaced = False

            def replace_visible_marker_after_read(descriptor, count):
                nonlocal replaced
                chunk = real_read(descriptor, count)
                metadata = os.fstat(descriptor)
                if (
                    metadata.st_dev,
                    metadata.st_ino,
                ) == marker_identity and not replaced:
                    replacement = transaction / ".replacement-owner"
                    replacement.write_bytes(invalid_marker)
                    os.replace(replacement, marker)
                    replaced = True
                return chunk

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher.os,
                    "read",
                    side_effect=replace_visible_marker_after_read,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    wraps=real_rename,
                ) as rename,
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            self.assertTrue(replaced)
            self.assertEqual(
                str(captured.exception), "source-lineage recovery state is unowned"
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(rename.call_count, 0)
            self.assertFalse(target.exists())
            self.assertTrue(previous.is_dir())
            self.assertEqual(marker.read_bytes(), invalid_marker)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            for private_value in (
                str(clone),
                "private-owner-marker-canary",
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_recovery_rechecks_owner_after_read_and_before_first_rename(
        self,
    ) -> None:
        def exercise(replacement_stage):
            with tempfile.TemporaryDirectory() as temporary_directory:
                clone = self.clone_refresh_fixture(temporary_directory)
                target = clone / LINEAGE_ROOT
                transaction_name = (
                    f"{self.refresher.TRANSACTION_PREFIX}{replacement_stage}"
                )
                transaction = clone / "release" / transaction_name
                previous = transaction / "previous"
                transaction.mkdir(mode=0o700)
                transaction.chmod(0o700)
                marker = transaction / self.refresher.TRANSACTION_MARKER
                expected_marker = self.refresher._transaction_marker(transaction_name)
                self.refresher._atomic_write(marker, expected_marker)
                transaction_metadata = transaction.stat()
                transaction_identity = (
                    transaction_metadata.st_dev,
                    transaction_metadata.st_ino,
                )
                canary = f"private-owner-{replacement_stage}-canary"
                invalid_marker = canary.encode("ascii").ljust(
                    len(expected_marker), b"x"
                )[: len(expected_marker)]
                self.assertEqual(len(invalid_marker), len(expected_marker))
                self.assertNotEqual(invalid_marker, expected_marker)
                shutil.copytree(target, previous)
                shutil.rmtree(target)
                real_read_regular_at = self.refresher._read_regular_at
                real_directory_exists_at = self.refresher._directory_exists_at
                real_rename = self.refresher._rename_at
                replaced = False

                def is_transaction_descriptor(descriptor):
                    metadata = os.fstat(descriptor)
                    return (
                        metadata.st_dev,
                        metadata.st_ino,
                    ) == transaction_identity

                def replace_visible_marker():
                    nonlocal replaced
                    replacement = transaction / ".replacement-owner"
                    self.refresher._atomic_write(replacement, invalid_marker)
                    self.refresher._durable_replace(replacement, marker)
                    replaced = True

                def read_then_replace(
                    parent_descriptor,
                    name,
                    expected_size,
                    diagnostic,
                ):
                    raw = real_read_regular_at(
                        parent_descriptor,
                        name,
                        expected_size,
                        diagnostic,
                    )
                    if (
                        replacement_stage == "after-marker-read"
                        and not replaced
                        and name == self.refresher.TRANSACTION_MARKER
                        and is_transaction_descriptor(parent_descriptor)
                    ):
                        replace_visible_marker()
                    return raw

                def probe_then_replace(parent_descriptor, name):
                    exists = real_directory_exists_at(parent_descriptor, name)
                    if (
                        replacement_stage == "before-recovery-rename"
                        and not replaced
                        and name == "staged"
                        and is_transaction_descriptor(parent_descriptor)
                    ):
                        replace_visible_marker()
                    return exists

                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        self.refresher,
                        "_read_regular_at",
                        side_effect=read_then_replace,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_directory_exists_at",
                        side_effect=probe_then_replace,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_rename_at",
                        wraps=real_rename,
                    ) as rename,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(self.refresher.lineage.LineageError) as captured,
                ):
                    self.refresher.write(clone)

                self.assertTrue(replaced)
                self.assertEqual(
                    str(captured.exception),
                    "source-lineage recovery state is unowned",
                )
                self.assertIsNone(captured.exception.__cause__)
                self.assertEqual(rename.call_count, 0)
                self.assertFalse(target.exists())
                self.assertTrue(previous.is_dir())
                self.assertEqual(marker.read_bytes(), invalid_marker)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                for private_value in (
                    str(clone),
                    canary,
                    "Traceback",
                ):
                    self.assertNotIn(private_value, str(captured.exception))

        for replacement_stage in (
            "after-marker-read",
            "before-recovery-rename",
        ):
            with self.subTest(replacement_stage=replacement_stage):
                exercise(replacement_stage)

    def test_target_missing_recovery_preserves_a_new_public_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            release = clone / "release"
            target = clone / LINEAGE_ROOT
            transaction_name = f"{self.refresher.TRANSACTION_PREFIX}target-appeared"
            transaction = release / transaction_name
            previous = transaction / "previous"
            transaction.mkdir(mode=0o700)
            transaction.chmod(0o700)
            marker_bytes = self.refresher._transaction_marker(transaction_name)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                marker_bytes,
            )
            target.rename(previous)
            previous_metadata = previous.stat()
            previous_identity = previous_metadata.st_dev, previous_metadata.st_ino
            previous_tree = self.module.tree_identity(previous)
            release_metadata = release.stat()
            release_identity = release_metadata.st_dev, release_metadata.st_ino

            real_directory_exists_at = self.refresher._directory_exists_at
            real_open_directory_at = self.refresher._open_directory_at
            real_rename = self.refresher._rename_at
            real_rename_noreplace = self.refresher._rename_noreplace_at
            opened_handles = []
            rename_applied = []
            noreplace_attempts = []
            noreplace_applied = []
            injected = False
            unknown_identity = None
            unknown_tree = None

            def probe_then_insert_unknown(parent_descriptor, name):
                nonlocal injected, unknown_identity, unknown_tree
                exists = real_directory_exists_at(parent_descriptor, name)
                parent_metadata = os.fstat(parent_descriptor)
                parent_identity = parent_metadata.st_dev, parent_metadata.st_ino
                if (
                    not injected
                    and name == LINEAGE_ROOT.name
                    and parent_identity == release_identity
                    and not exists
                ):
                    target.mkdir()
                    metadata = target.stat()
                    unknown_identity = metadata.st_dev, metadata.st_ino
                    unknown_tree = self.module.tree_identity(target)
                    injected = True
                return exists

            def remember_handle(parent_descriptor, name, diagnostic):
                handle = real_open_directory_at(parent_descriptor, name, diagnostic)
                opened_handles.append(handle)
                return handle

            def record_ordinary_move(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage recovery state is ambiguous",
            ):
                def mark_applied():
                    rename_applied.append((source_name, destination_name))
                    if applied is not None:
                        applied()

                return real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=(
                        mark_applied
                        if source_name == "previous"
                        and destination_name == LINEAGE_ROOT.name
                        else applied
                    ),
                    diagnostic=diagnostic,
                )

            def record_noreplace_move(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage recovery state is ambiguous",
            ):
                noreplace_attempts.append((source_name, destination_name))

                def mark_applied():
                    noreplace_applied.append((source_name, destination_name))
                    if applied is not None:
                        applied()

                return real_rename_noreplace(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=mark_applied,
                    diagnostic=diagnostic,
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher,
                    "_directory_exists_at",
                    side_effect=probe_then_insert_unknown,
                ),
                mock.patch.object(
                    self.refresher,
                    "_open_directory_at",
                    side_effect=remember_handle,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=record_ordinary_move,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_noreplace_at",
                    side_effect=record_noreplace_move,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            self.assertTrue(injected)
            self.assertEqual(
                str(captured.exception),
                "source-lineage recovery state is ambiguous",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(rename_applied, [])
            self.assertEqual(
                noreplace_attempts,
                [("previous", LINEAGE_ROOT.name)],
            )
            self.assertEqual(noreplace_applied, [])
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            for handle in opened_handles:
                with self.assertRaises(OSError):
                    os.fstat(handle.descriptor)

            unknown_metadata = target.stat()
            self.assertEqual(
                (unknown_metadata.st_dev, unknown_metadata.st_ino),
                unknown_identity,
            )
            self.assertEqual(self.module.tree_identity(target), unknown_tree)
            self.assertFalse(transaction.exists())
            retained_transaction = (
                self.refresher._retention_root(clone) / transaction_name
            )
            self.assertTrue(retained_transaction.is_dir())
            retained_previous = retained_transaction / "previous"
            retained_previous_metadata = retained_previous.stat()
            self.assertEqual(
                (
                    retained_previous_metadata.st_dev,
                    retained_previous_metadata.st_ino,
                ),
                previous_identity,
            )
            self.assertEqual(
                self.module.tree_identity(retained_previous), previous_tree
            )
            self.assertEqual(
                (retained_transaction / self.refresher.TRANSACTION_MARKER).read_bytes(),
                marker_bytes,
            )

            retained_tree = self.module.tree_identity(retained_transaction)
            replay_stdout = io.StringIO()
            replay_stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(replay_stdout),
                contextlib.redirect_stderr(replay_stderr),
            ):
                self.refresher._recover_interrupted_write(clone)
            self.assertEqual(replay_stdout.getvalue(), "")
            self.assertEqual(replay_stderr.getvalue(), "")
            self.assertFalse(transaction.exists())
            replayed_unknown_metadata = target.stat()
            self.assertEqual(
                (
                    replayed_unknown_metadata.st_dev,
                    replayed_unknown_metadata.st_ino,
                ),
                unknown_identity,
            )
            self.assertEqual(self.module.tree_identity(target), unknown_tree)
            self.assertEqual(
                self.module.tree_identity(retained_transaction), retained_tree
            )
            for private_value in (
                str(clone),
                transaction_name,
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_target_missing_recovery_retracts_a_postbind_source_substitute(
        self,
    ) -> None:
        for compensation_collision in (False, True):
            with (
                self.subTest(compensation_collision=compensation_collision),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                clone = self.clone_refresh_fixture(temporary_directory)
                target = clone / LINEAGE_ROOT
                transaction_name = (
                    f"{self.refresher.TRANSACTION_PREFIX}missing-source-race-"
                    f"{int(compensation_collision)}"
                )
                transaction = clone / "release" / transaction_name
                previous = transaction / "previous"
                parked_previous = Path(temporary_directory) / "parked-previous"
                transaction.mkdir(mode=0o700)
                transaction.chmod(0o700)
                marker_bytes = self.refresher._transaction_marker(transaction_name)
                self.refresher._atomic_write(
                    transaction / self.refresher.TRANSACTION_MARKER,
                    marker_bytes,
                )
                target.rename(previous)
                previous_metadata = previous.stat()
                previous_identity = (
                    previous_metadata.st_dev,
                    previous_metadata.st_ino,
                )
                previous_tree = self.module.tree_identity(previous)

                real_open_directory_at = self.refresher._open_directory_at
                real_require_directory_name = self.refresher._require_directory_name
                real_rename = self.refresher._rename_at
                real_rename_noreplace = self.refresher._rename_noreplace_at
                opened_handles = []
                ordinary_applied = []
                noreplace_attempts = []
                noreplace_applied = []
                replaced = False
                substitute_identity = None
                substitute_tree = None
                occupant_identity = None
                occupant_tree = None

                def remember_handle(
                    parent_descriptor,
                    name,
                    diagnostic,
                    _real_open_directory_at=real_open_directory_at,
                    _opened_handles=opened_handles,
                ):
                    handle = _real_open_directory_at(
                        parent_descriptor,
                        name,
                        diagnostic,
                    )
                    _opened_handles.append(handle)
                    return handle

                def replace_previous_after_prebind(
                    parent_descriptor,
                    name,
                    handle,
                    diagnostic,
                    _real_require_directory_name=real_require_directory_name,
                    _previous=previous,
                    _parked_previous=parked_previous,
                    _previous_identity=previous_identity,
                    _previous_tree=previous_tree,
                ):
                    nonlocal replaced, substitute_identity, substitute_tree
                    result = _real_require_directory_name(
                        parent_descriptor,
                        name,
                        handle,
                        diagnostic,
                    )
                    if name == "previous" and not replaced:
                        self.assertEqual(handle.identity, _previous_identity)
                        _previous.rename(_parked_previous)
                        shutil.copytree(_parked_previous, _previous)
                        metadata = _previous.stat()
                        substitute_identity = metadata.st_dev, metadata.st_ino
                        substitute_tree = self.module.tree_identity(_previous)
                        self.assertNotEqual(
                            substitute_identity,
                            _previous_identity,
                        )
                        self.assertEqual(substitute_tree, _previous_tree)
                        replaced = True
                    return result

                def record_ordinary_move(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    *,
                    applied=None,
                    diagnostic="source-lineage recovery state is ambiguous",
                    _real_rename=real_rename,
                    _ordinary_applied=ordinary_applied,
                ):
                    def mark_applied():
                        _ordinary_applied.append((source_name, destination_name))
                        if applied is not None:
                            applied()

                    return _real_rename(
                        source_parent,
                        source_name,
                        destination_parent,
                        destination_name,
                        applied=(
                            mark_applied
                            if source_name in {"previous", LINEAGE_ROOT.name}
                            else applied
                        ),
                        diagnostic=diagnostic,
                    )

                def record_noreplace_move(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    *,
                    applied=None,
                    diagnostic="source-lineage recovery state is ambiguous",
                    _real_rename_noreplace=real_rename_noreplace,
                    _noreplace_attempts=noreplace_attempts,
                    _noreplace_applied=noreplace_applied,
                    _transaction=transaction,
                    _compensation_collision=compensation_collision,
                ):
                    nonlocal occupant_identity, occupant_tree
                    _noreplace_attempts.append((source_name, destination_name))

                    def mark_applied():
                        _noreplace_applied.append((source_name, destination_name))
                        if applied is not None:
                            applied()

                    result = _real_rename_noreplace(
                        source_parent,
                        source_name,
                        destination_parent,
                        destination_name,
                        applied=mark_applied,
                        diagnostic=diagnostic,
                    )
                    if (
                        _compensation_collision
                        and source_name == "previous"
                        and destination_name == LINEAGE_ROOT.name
                        and occupant_identity is None
                    ):
                        occupant = _transaction / "previous"
                        occupant.mkdir()
                        metadata = occupant.stat()
                        occupant_identity = metadata.st_dev, metadata.st_ino
                        occupant_tree = self.module.tree_identity(occupant)
                    return result

                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        self.refresher,
                        "_open_directory_at",
                        side_effect=remember_handle,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_require_directory_name",
                        side_effect=replace_previous_after_prebind,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_rename_at",
                        side_effect=record_ordinary_move,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_rename_noreplace_at",
                        side_effect=record_noreplace_move,
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(self.refresher.lineage.LineageError) as captured,
                ):
                    self.refresher.write(clone)

                self.assertTrue(replaced)
                self.assertEqual(
                    str(captured.exception),
                    "source-lineage recovery state is ambiguous",
                )
                self.assertIsNone(captured.exception.__cause__)
                self.assertEqual(ordinary_applied, [])
                expected_attempts = [
                    ("previous", LINEAGE_ROOT.name),
                    (LINEAGE_ROOT.name, "previous"),
                ]
                expected_applied = [
                    ("previous", LINEAGE_ROOT.name),
                    (LINEAGE_ROOT.name, "previous"),
                ]
                retained_name = "previous"
                if compensation_collision:
                    expected_attempts.append((LINEAGE_ROOT.name, "staged"))
                    expected_applied[-1] = (LINEAGE_ROOT.name, "staged")
                    retained_name = "staged"
                self.assertEqual(noreplace_attempts, expected_attempts)
                self.assertEqual(noreplace_applied, expected_applied)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                self.assertFalse(target.exists())
                self.assertFalse(transaction.exists())
                for handle in opened_handles:
                    with self.assertRaises(OSError):
                        os.fstat(handle.descriptor)

                parked_metadata = parked_previous.stat()
                self.assertEqual(
                    (parked_metadata.st_dev, parked_metadata.st_ino),
                    previous_identity,
                )
                self.assertEqual(
                    self.module.tree_identity(parked_previous), previous_tree
                )
                retained_transaction = (
                    self.refresher._retention_root(clone) / transaction_name
                )
                self.assertTrue(retained_transaction.is_dir())
                self.assertEqual(
                    (
                        retained_transaction / self.refresher.TRANSACTION_MARKER
                    ).read_bytes(),
                    marker_bytes,
                )
                retained_substitute = retained_transaction / retained_name
                substitute_metadata = retained_substitute.stat()
                self.assertEqual(
                    (substitute_metadata.st_dev, substitute_metadata.st_ino),
                    substitute_identity,
                )
                self.assertEqual(
                    self.module.tree_identity(retained_substitute), substitute_tree
                )
                if compensation_collision:
                    retained_occupant = retained_transaction / "previous"
                    occupant_metadata = retained_occupant.stat()
                    self.assertEqual(
                        (occupant_metadata.st_dev, occupant_metadata.st_ino),
                        occupant_identity,
                    )
                    self.assertEqual(
                        self.module.tree_identity(retained_occupant), occupant_tree
                    )

                retained_tree = self.module.tree_identity(retained_transaction)
                replay_stdout = io.StringIO()
                replay_stderr = io.StringIO()
                with (
                    contextlib.redirect_stdout(replay_stdout),
                    contextlib.redirect_stderr(replay_stderr),
                ):
                    self.refresher._recover_interrupted_write(clone)
                self.assertEqual(replay_stdout.getvalue(), "")
                self.assertEqual(replay_stderr.getvalue(), "")
                self.assertFalse(target.exists())
                self.assertEqual(
                    self.module.tree_identity(retained_transaction), retained_tree
                )
                for private_value in (
                    str(clone),
                    str(parked_previous),
                    transaction_name,
                    "Traceback",
                ):
                    self.assertNotIn(private_value, str(captured.exception))

    def test_target_missing_recovery_quarantines_a_postbind_nondirectory_swap(
        self,
    ) -> None:
        def recovery_snapshot(root):
            snapshot = {}
            for candidate in (root, *root.rglob("*")):
                metadata = candidate.lstat()
                relative = "." if candidate == root else candidate.relative_to(root)
                if stat.S_ISREG(metadata.st_mode):
                    content = candidate.read_bytes()
                elif stat.S_ISLNK(metadata.st_mode):
                    content = os.readlink(candidate)
                else:
                    content = None
                snapshot[str(relative)] = (
                    stat.S_IFMT(metadata.st_mode),
                    stat.S_IMODE(metadata.st_mode),
                    metadata.st_dev,
                    metadata.st_ino,
                    content,
                )
            return snapshot

        cases = (
            ("regular", b"private-postbind-regular-canary\n", None),
            ("symlink", "private-postbind-symlink-canary", None),
            ("fifo", "private-postbind-fifo-canary", None),
            ("socket", "private-postbind-socket-canary", None),
            (
                "regular-staged-file",
                b"private-postbind-collision-canary\n",
                b"private-staged-occupant-canary\n",
            ),
        )
        for kind, payload, staged_payload in cases:
            if kind == "fifo" and not hasattr(os, "mkfifo"):
                continue
            if kind == "socket" and not hasattr(socket, "AF_UNIX"):
                continue
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory(
                    prefix="sl-",
                    dir="/private/tmp",
                ) as temporary,
            ):
                clone = self.clone_refresh_fixture(temporary)
                target = clone / LINEAGE_ROOT
                transaction_name = f"{self.refresher.TRANSACTION_PREFIX}postbind-{kind}"
                transaction = clone / "release" / transaction_name
                previous = transaction / "previous"
                parked_previous = Path(temporary) / "parked-previous"
                transaction.mkdir(mode=0o700)
                transaction.chmod(0o700)
                marker_bytes = self.refresher._transaction_marker(transaction_name)
                self.refresher._atomic_write(
                    transaction / self.refresher.TRANSACTION_MARKER,
                    marker_bytes,
                )
                target.rename(previous)
                previous_metadata = previous.stat()
                previous_identity = (
                    previous_metadata.st_dev,
                    previous_metadata.st_ino,
                )
                previous_tree = self.module.tree_identity(previous)

                real_open_directory_at = self.refresher._open_directory_at
                real_require_directory_name = self.refresher._require_directory_name
                real_rename_noreplace = self.refresher._rename_noreplace_at
                opened_handles = []
                native_attempts = []
                replacement_state = {"replaced": False}
                substitute_identity = None
                staged_occupant_identity = None
                socket_listener = None

                def remember_handle(
                    parent_descriptor,
                    name,
                    diagnostic,
                    _real_open_directory_at=real_open_directory_at,
                    _opened_handles=opened_handles,
                ):
                    handle = _real_open_directory_at(
                        parent_descriptor,
                        name,
                        diagnostic,
                    )
                    _opened_handles.append(handle)
                    return handle

                def replace_previous_after_prebind(
                    parent_descriptor,
                    name,
                    handle,
                    diagnostic,
                    _real_require_directory_name=real_require_directory_name,
                    _previous_identity=previous_identity,
                    _previous=previous,
                    _parked_previous=parked_previous,
                    _kind=kind,
                    _payload=payload,
                    _transaction=transaction,
                    _staged_payload=staged_payload,
                    _replacement_state=replacement_state,
                ):
                    nonlocal socket_listener
                    nonlocal staged_occupant_identity, substitute_identity
                    result = _real_require_directory_name(
                        parent_descriptor,
                        name,
                        handle,
                        diagnostic,
                    )
                    if name == "previous" and not _replacement_state["replaced"]:
                        self.assertEqual(handle.identity, _previous_identity)
                        _previous.rename(_parked_previous)
                        if _kind.startswith("regular"):
                            _previous.write_bytes(_payload)
                        elif _kind == "symlink":
                            _previous.symlink_to(_payload)
                        elif _kind == "fifo":
                            os.mkfifo(_previous, 0o600)
                        else:
                            socket_listener = socket.socket(
                                socket.AF_UNIX,
                                socket.SOCK_STREAM,
                            )
                            self.addCleanup(socket_listener.close)
                            socket_listener.bind(os.fspath(_previous))
                        substitute_metadata = _previous.lstat()
                        substitute_identity = (
                            substitute_metadata.st_dev,
                            substitute_metadata.st_ino,
                        )
                        if _staged_payload is not None:
                            staged_occupant = _transaction / "staged"
                            staged_occupant.write_bytes(_staged_payload)
                            staged_metadata = staged_occupant.lstat()
                            staged_occupant_identity = (
                                staged_metadata.st_dev,
                                staged_metadata.st_ino,
                            )
                        _replacement_state["replaced"] = True
                    return result

                def record_noreplace_move(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    *,
                    applied=None,
                    diagnostic="source-lineage recovery state is ambiguous",
                    _real_rename_noreplace=real_rename_noreplace,
                    _native_attempts=native_attempts,
                    _replacement_state=replacement_state,
                ):
                    self.assertTrue(_replacement_state["replaced"])
                    _native_attempts.append((source_name, destination_name))
                    return _real_rename_noreplace(
                        source_parent,
                        source_name,
                        destination_parent,
                        destination_name,
                        applied=applied,
                        diagnostic=diagnostic,
                    )

                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        self.refresher,
                        "_open_directory_at",
                        side_effect=remember_handle,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_require_directory_name",
                        side_effect=replace_previous_after_prebind,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_rename_noreplace_at",
                        side_effect=record_noreplace_move,
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(self.refresher.lineage.LineageError) as captured,
                ):
                    self.refresher.write(clone)
                if socket_listener is not None:
                    socket_listener.close()

                self.assertTrue(replacement_state["replaced"])
                self.assertEqual(
                    native_attempts[0],
                    ("previous", LINEAGE_ROOT.name),
                )
                self.assertEqual(
                    str(captured.exception),
                    "source-lineage recovery state is ambiguous",
                )
                self.assertIsNone(captured.exception.__cause__)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                self.assertFalse(os.path.lexists(target))
                self.assertFalse(os.path.lexists(transaction))
                for handle in opened_handles:
                    with self.assertRaises(OSError):
                        os.fstat(handle.descriptor)

                parked_metadata = parked_previous.stat()
                self.assertEqual(
                    (parked_metadata.st_dev, parked_metadata.st_ino),
                    previous_identity,
                )
                self.assertEqual(
                    self.module.tree_identity(parked_previous),
                    previous_tree,
                )
                retained_transaction = (
                    self.refresher._retention_root(clone) / transaction_name
                )
                self.assertTrue(retained_transaction.is_dir())
                self.assertEqual(
                    (
                        retained_transaction / self.refresher.TRANSACTION_MARKER
                    ).read_bytes(),
                    marker_bytes,
                )
                retention_root = self.refresher._retention_root(clone)
                recovery_entries = list(retention_root.rglob("*"))
                substitute_locations = []
                for candidate in recovery_entries:
                    metadata = candidate.lstat()
                    if (metadata.st_dev, metadata.st_ino) == substitute_identity:
                        substitute_locations.append(candidate)
                self.assertEqual(len(substitute_locations), 1)
                retained_substitute = substitute_locations[0]
                retained_metadata = retained_substitute.lstat()
                if kind.startswith("regular"):
                    self.assertTrue(stat.S_ISREG(retained_metadata.st_mode))
                    self.assertEqual(retained_substitute.read_bytes(), payload)
                elif kind == "symlink":
                    self.assertTrue(stat.S_ISLNK(retained_metadata.st_mode))
                    self.assertEqual(os.readlink(retained_substitute), payload)
                elif kind == "fifo":
                    self.assertTrue(stat.S_ISFIFO(retained_metadata.st_mode))
                else:
                    self.assertTrue(stat.S_ISSOCK(retained_metadata.st_mode))
                if staged_payload is not None:
                    staged_locations = []
                    for candidate in recovery_entries:
                        metadata = candidate.lstat()
                        if (
                            metadata.st_dev,
                            metadata.st_ino,
                        ) == staged_occupant_identity:
                            staged_locations.append(candidate)
                    self.assertEqual(len(staged_locations), 1)
                    staged_occupant = staged_locations[0]
                    self.assertTrue(stat.S_ISREG(staged_occupant.lstat().st_mode))
                    self.assertEqual(staged_occupant.read_bytes(), staged_payload)

                retained_tree = recovery_snapshot(retention_root)
                replay_stdout = io.StringIO()
                replay_stderr = io.StringIO()
                with (
                    contextlib.redirect_stdout(replay_stdout),
                    contextlib.redirect_stderr(replay_stderr),
                ):
                    self.refresher._recover_interrupted_write(clone)
                self.assertEqual(replay_stdout.getvalue(), "")
                self.assertEqual(replay_stderr.getvalue(), "")
                self.assertFalse(os.path.lexists(target))
                self.assertFalse(os.path.lexists(transaction))
                self.assertEqual(
                    recovery_snapshot(retention_root),
                    retained_tree,
                )
                for private_value in (
                    str(clone),
                    str(parked_previous),
                    (
                        payload.decode("ascii").strip()
                        if isinstance(payload, bytes)
                        else payload
                    ),
                    (
                        staged_payload.decode("ascii").strip()
                        if staged_payload is not None
                        else "unused-staged-canary"
                    ),
                    transaction_name,
                    "Traceback",
                ):
                    self.assertNotIn(private_value, str(captured.exception))

    def test_recovery_compensates_if_owner_changes_after_restoring_target(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            transaction_name = f"{self.refresher.TRANSACTION_PREFIX}restore-race"
            transaction = clone / "release" / transaction_name
            previous = transaction / "previous"
            transaction.mkdir(mode=0o700)
            transaction.chmod(0o700)
            marker = transaction / self.refresher.TRANSACTION_MARKER
            expected_marker = self.refresher._transaction_marker(transaction_name)
            self.refresher._atomic_write(marker, expected_marker)
            shutil.copytree(target, previous)
            shutil.rmtree(target)
            previous_metadata = previous.stat()
            previous_identity = previous_metadata.st_dev, previous_metadata.st_ino
            previous_tree = self.module.tree_identity(previous)
            canary = "private-owner-after-recovery-restore-canary"
            invalid_marker = canary.encode("ascii").ljust(len(expected_marker), b"x")[
                : len(expected_marker)
            ]
            self.assertEqual(len(invalid_marker), len(expected_marker))
            self.assertNotEqual(invalid_marker, expected_marker)
            real_open_directory_at = self.refresher._open_directory_at
            real_rename_noreplace = self.refresher._rename_noreplace_at
            opened_handles = []
            rename_order = []
            replaced = False

            def remember_handle(parent_descriptor, name, diagnostic):
                handle = real_open_directory_at(parent_descriptor, name, diagnostic)
                opened_handles.append(handle)
                return handle

            def replace_owner_after_restore(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage recovery state is ambiguous",
            ):
                nonlocal replaced
                result = real_rename_noreplace(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )
                if source_name in {LINEAGE_ROOT.name, "previous"}:
                    rename_order.append((source_name, destination_name))
                if (
                    source_name == "previous"
                    and destination_name == LINEAGE_ROOT.name
                    and not replaced
                ):
                    replacement = transaction / ".replacement-owner"
                    self.refresher._atomic_write(replacement, invalid_marker)
                    self.refresher._durable_replace(replacement, marker)
                    replaced = True
                return result

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher,
                    "_open_directory_at",
                    side_effect=remember_handle,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_noreplace_at",
                    side_effect=replace_owner_after_restore,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            self.assertTrue(replaced)
            self.assertEqual(
                str(captured.exception),
                "source-lineage recovery state is unowned",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(
                rename_order,
                [
                    ("previous", LINEAGE_ROOT.name),
                    (LINEAGE_ROOT.name, "previous"),
                ],
            )
            for handle in opened_handles:
                with self.assertRaises(OSError):
                    os.fstat(handle.descriptor)
            self.assertFalse(target.exists())
            restored_metadata = previous.stat()
            self.assertEqual(
                (restored_metadata.st_dev, restored_metadata.st_ino),
                previous_identity,
            )
            self.assertEqual(self.module.tree_identity(previous), previous_tree)
            self.assertEqual(marker.read_bytes(), invalid_marker)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            for private_value in (
                str(clone),
                canary,
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_darwin_non_directory_open_race_to_fifo_is_bounded(self) -> None:
        if sys.platform != "darwin" or not hasattr(os, "mkfifo"):
            self.skipTest("Darwin FIFO creation is unavailable")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidate = root / "candidate"
            candidate.write_bytes(b"private-fifo-race-canary\n")
            parent_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_open = os.open
            observed_flags = []
            swapped = False
            stdout = io.StringIO()
            stderr = io.StringIO()

            def swap_to_fifo_before_open(
                path,
                flags,
                mode=0o777,
                *,
                dir_fd=None,
            ):
                nonlocal swapped
                if (
                    not swapped
                    and path == candidate.name
                    and dir_fd == parent_descriptor
                ):
                    candidate.unlink()
                    os.mkfifo(candidate, 0o600)
                    observed_flags.append(flags)
                    swapped = True
                    raise OSError("private-fifo-open-canary")
                return real_open(path, flags, mode, dir_fd=dir_fd)

            try:
                with (
                    mock.patch.object(
                        self.refresher.os,
                        "open",
                        side_effect=swap_to_fifo_before_open,
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(self.refresher.lineage.LineageError) as captured,
                ):
                    self.refresher._open_non_directory_at(
                        parent_descriptor,
                        candidate.name,
                        "source-lineage recovery state is ambiguous",
                    )
            finally:
                os.close(parent_descriptor)

            child_program = f"""
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile

sys.dont_write_bytecode = True
specification = importlib.util.spec_from_file_location(
    "fifo_race_refresher", {str(REFRESHER)!r}
)
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    candidate = root / "candidate"
    candidate.write_bytes(b"private-fifo-child-canary\\n")
    parent_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    real_open = os.open
    state = {{"swapped": False}}

    def raced_open(path, flags, mode=0o777, *, dir_fd=None):
        if (
            not state["swapped"]
            and path == candidate.name
            and dir_fd == parent_descriptor
        ):
            candidate.unlink()
            os.mkfifo(candidate, 0o600)
            state["swapped"] = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    module.os.open = raced_open
    handle = None
    try:
        try:
            handle = module._open_non_directory_at(
                parent_descriptor,
                candidate.name,
                "source-lineage recovery state is ambiguous",
            )
        except module.lineage.LineageError as error:
            if not state["swapped"]:
                raise SystemExit(4)
            sys.stderr.write(str(error) + "\\n")
            raise SystemExit(5)
        visible = os.stat(
            candidate.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not state["swapped"] or not stat.S_ISFIFO(visible.st_mode):
            raise SystemExit(6)
        if handle.descriptor is None:
            raise SystemExit(7)
        opened = os.fstat(handle.descriptor)
        if module._validator_identity(opened) != module._validator_identity(visible):
            raise SystemExit(8)
        if handle.identity != module._validator_identity(visible):
            raise SystemExit(9)
        sys.stdout.write("retained fifo binding\\n")
    finally:
        module._close_non_directory(
            handle,
            "source-lineage recovery state is ambiguous",
        )
        module.os.open = real_open
        os.close(parent_descriptor)
"""
            process = subprocess.Popen(
                [sys.executable, "-c", child_program],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            timed_out = False
            try:
                child_stdout, child_stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                child_stdout, child_stderr = process.communicate(timeout=5)

            event_only_flag = getattr(os, "O_EVTONLY", 0x00008000)
            with self.subTest(boundary="flags"):
                self.assertTrue(swapped)
                self.assertEqual(len(observed_flags), 1)
                required_flags = event_only_flag | os.O_NONBLOCK
                self.assertEqual(
                    observed_flags[0] & required_flags,
                    required_flags,
                )
                self.assertEqual(
                    str(captured.exception),
                    "source-lineage recovery state is ambiguous",
                )
                self.assertIsNone(captured.exception.__cause__)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                self.assertTrue(stat.S_ISFIFO(candidate.lstat().st_mode))
                for private_value in (
                    str(root),
                    "private-fifo-race-canary",
                    "private-fifo-open-canary",
                    "Traceback",
                ):
                    self.assertNotIn(private_value, str(captured.exception))

            with self.subTest(boundary="bounded-child-retained"):
                self.assertIsNotNone(process.returncode)
                self.assertFalse(timed_out)
                self.assertEqual(process.returncode, 0)
                self.assertEqual(
                    child_stdout,
                    "retained fifo binding\n",
                )
                self.assertEqual(child_stderr, "")

            with (
                self.subTest(boundary="socket-fallback"),
                tempfile.TemporaryDirectory(
                    prefix="sl-socket-",
                    dir="/private/tmp",
                ) as socket_temporary_directory,
            ):
                socket_root = Path(socket_temporary_directory)
                socket_candidate = socket_root / "candidate"
                socket_candidate.write_bytes(b"private-socket-race-canary\n")
                socket_parent_descriptor = os.open(
                    socket_root,
                    os.O_RDONLY | os.O_DIRECTORY,
                )
                socket_listener = None
                socket_handle = None
                socket_swapped = False
                socket_stdout = io.StringIO()
                socket_stderr = io.StringIO()

                def swap_to_socket_before_open(
                    path,
                    flags,
                    mode=0o777,
                    *,
                    dir_fd=None,
                ):
                    nonlocal socket_listener, socket_swapped
                    if (
                        not socket_swapped
                        and path == socket_candidate.name
                        and dir_fd == socket_parent_descriptor
                    ):
                        socket_candidate.unlink()
                        socket_listener = socket.socket(
                            socket.AF_UNIX,
                            socket.SOCK_STREAM,
                        )
                        socket_listener.bind(os.fspath(socket_candidate))
                        socket_swapped = True
                    return real_open(path, flags, mode, dir_fd=dir_fd)

                started = time.monotonic()
                try:
                    with (
                        mock.patch.object(
                            self.refresher.os,
                            "open",
                            side_effect=swap_to_socket_before_open,
                        ),
                        contextlib.redirect_stdout(socket_stdout),
                        contextlib.redirect_stderr(socket_stderr),
                    ):
                        socket_handle = self.refresher._open_non_directory_at(
                            socket_parent_descriptor,
                            socket_candidate.name,
                            "source-lineage recovery state is ambiguous",
                        )
                    self.assertLess(time.monotonic() - started, 1)
                    self.assertTrue(socket_swapped)
                    self.assertIsNone(socket_handle.descriptor)
                    socket_metadata = socket_candidate.lstat()
                    self.assertTrue(stat.S_ISSOCK(socket_metadata.st_mode))
                    self.assertEqual(
                        socket_handle.identity,
                        self.refresher._validator_identity(socket_metadata),
                    )
                    self.assertEqual(socket_stdout.getvalue(), "")
                    self.assertEqual(socket_stderr.getvalue(), "")
                finally:
                    self.refresher._close_non_directory(
                        socket_handle,
                        "source-lineage recovery state is ambiguous",
                    )
                    if socket_listener is not None:
                        socket_listener.close()
                    os.close(socket_parent_descriptor)

    def test_recovery_does_not_publish_a_replaced_previous_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            transaction_name = f"{self.refresher.TRANSACTION_PREFIX}previous-race"
            transaction = clone / "release" / transaction_name
            previous = transaction / "previous"
            retained_previous = Path(temporary_directory) / "retained-previous"
            transaction.mkdir(mode=0o700)
            transaction.chmod(0o700)
            marker = transaction / self.refresher.TRANSACTION_MARKER
            marker_bytes = self.refresher._transaction_marker(transaction_name)
            self.refresher._atomic_write(
                marker,
                marker_bytes,
            )
            shutil.copytree(target, previous)
            previous_metadata = previous.stat()
            previous_identity = previous_metadata.st_dev, previous_metadata.st_ino
            previous_tree = self.module.tree_identity(previous)

            rejected_canary = "private-rejected-generation-canary"
            (target / "private-rejected.txt").write_text(
                rejected_canary + "\n",
                encoding="utf-8",
            )
            rejected_metadata = target.stat()
            rejected_identity = rejected_metadata.st_dev, rejected_metadata.st_ino
            rejected_tree = self.module.tree_identity(target)

            substitute_canary = "private-substitute-previous-canary"
            real_open_directory_at = self.refresher._open_directory_at
            real_rename = self.refresher._rename_at
            opened_handles = []
            rename_order = []
            replacement_calls = []
            substitute_identity = None
            substitute_tree = None

            def remember_handle(parent_descriptor, name, diagnostic):
                handle = real_open_directory_at(parent_descriptor, name, diagnostic)
                opened_handles.append(handle)
                return handle

            def replace_previous_after_rejection(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage recovery state is ambiguous",
            ):
                nonlocal substitute_identity, substitute_tree
                result = real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )
                if source_name in {LINEAGE_ROOT.name, "previous"}:
                    rename_order.append((source_name, destination_name))
                if (
                    source_name == LINEAGE_ROOT.name
                    and destination_name == "failed"
                    and not replacement_calls
                ):
                    previous.rename(retained_previous)
                    shutil.copytree(retained_previous, previous)
                    (previous / "private-substitute.txt").write_text(
                        substitute_canary + "\n",
                        encoding="utf-8",
                    )
                    metadata = previous.stat()
                    substitute_identity = metadata.st_dev, metadata.st_ino
                    substitute_tree = self.module.tree_identity(previous)
                    replacement_calls.append(previous)
                return result

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher,
                    "_open_directory_at",
                    side_effect=remember_handle,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=replace_previous_after_rejection,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            self.assertEqual(replacement_calls, [previous])
            self.assertEqual(
                str(captured.exception),
                "source-lineage recovery state is ambiguous",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(
                rename_order,
                [(LINEAGE_ROOT.name, "failed")],
            )
            self.assertFalse(target.exists())
            self.assertFalse(transaction.exists())
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn(
                previous_identity, [handle.identity for handle in opened_handles]
            )
            for handle in opened_handles:
                with self.assertRaises(OSError):
                    os.fstat(handle.descriptor)

            restored_metadata = retained_previous.stat()
            self.assertEqual(
                (restored_metadata.st_dev, restored_metadata.st_ino),
                previous_identity,
            )
            self.assertEqual(
                self.module.tree_identity(retained_previous),
                previous_tree,
            )
            retention_root = self.refresher._retention_root(clone)
            retained_transaction = retention_root / transaction_name
            self.assertTrue(retained_transaction.is_dir())
            self.assertEqual(
                list((clone / "release").glob(f"{self.refresher.TRANSACTION_PREFIX}*")),
                [],
            )
            self.assertEqual(
                (retained_transaction / self.refresher.TRANSACTION_MARKER).read_bytes(),
                marker_bytes,
            )
            retained_substitute = retained_transaction / "previous"
            substitute_metadata = retained_substitute.stat()
            self.assertEqual(
                (substitute_metadata.st_dev, substitute_metadata.st_ino),
                substitute_identity,
            )
            self.assertNotEqual(substitute_identity, previous_identity)
            self.assertEqual(
                self.module.tree_identity(retained_substitute), substitute_tree
            )
            self.assertEqual(
                (retained_substitute / "private-substitute.txt").read_text(
                    encoding="utf-8"
                ),
                substitute_canary + "\n",
            )
            failed = retained_transaction / "failed"
            failed_metadata = failed.stat()
            self.assertEqual(
                (failed_metadata.st_dev, failed_metadata.st_ino),
                rejected_identity,
            )
            self.assertEqual(self.module.tree_identity(failed), rejected_tree)
            self.assertEqual(
                (failed / "private-rejected.txt").read_text(encoding="utf-8"),
                rejected_canary + "\n",
            )

            retained_tree = self.module.tree_identity(retained_transaction)
            replay_stdout = io.StringIO()
            replay_stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(replay_stdout),
                contextlib.redirect_stderr(replay_stderr),
            ):
                self.refresher._recover_interrupted_write(clone)
            self.assertEqual(replay_stdout.getvalue(), "")
            self.assertEqual(replay_stderr.getvalue(), "")
            self.assertFalse(target.exists())
            self.assertFalse(transaction.exists())
            self.assertEqual(
                self.module.tree_identity(retained_transaction), retained_tree
            )
            self.assertEqual(
                self.module.tree_identity(retained_substitute), substitute_tree
            )
            for private_value in (
                str(clone),
                str(retained_previous),
                rejected_canary,
                substitute_canary,
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_recovery_compensates_previous_substitution_after_prebind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            transaction_name = f"{self.refresher.TRANSACTION_PREFIX}post-bind-race"
            transaction = clone / "release" / transaction_name
            previous = transaction / "previous"
            parked_previous = Path(temporary_directory) / "parked-previous"
            transaction.mkdir(mode=0o700)
            transaction.chmod(0o700)
            marker_bytes = self.refresher._transaction_marker(transaction_name)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                marker_bytes,
            )
            shutil.copytree(target, previous)
            previous_metadata = previous.stat()
            previous_identity = previous_metadata.st_dev, previous_metadata.st_ino
            previous_tree = self.module.tree_identity(previous)

            rejected_canary = "private-post-bind-rejected-generation"
            (target / "private-rejected.txt").write_text(
                rejected_canary + "\n",
                encoding="utf-8",
            )
            rejected_metadata = target.stat()
            rejected_identity = rejected_metadata.st_dev, rejected_metadata.st_ino
            rejected_tree = self.module.tree_identity(target)

            real_open_directory_at = self.refresher._open_directory_at
            real_require_directory_name = self.refresher._require_directory_name
            real_rename = self.refresher._rename_at
            real_rename_noreplace = self.refresher._rename_noreplace_at
            opened_handles = []
            rename_order = []
            replaced = False
            substitute_identity = None
            substitute_tree = None

            def remember_handle(parent_descriptor, name, diagnostic):
                handle = real_open_directory_at(parent_descriptor, name, diagnostic)
                opened_handles.append(handle)
                return handle

            def replace_previous_after_prebind(
                parent_descriptor,
                name,
                handle,
                diagnostic,
            ):
                nonlocal replaced, substitute_identity, substitute_tree
                result = real_require_directory_name(
                    parent_descriptor,
                    name,
                    handle,
                    diagnostic,
                )
                if name == "previous" and not replaced:
                    self.assertEqual(handle.identity, previous_identity)
                    previous.rename(parked_previous)
                    shutil.copytree(parked_previous, previous)
                    metadata = previous.stat()
                    substitute_identity = metadata.st_dev, metadata.st_ino
                    substitute_tree = self.module.tree_identity(previous)
                    self.assertNotEqual(substitute_identity, previous_identity)
                    self.assertEqual(substitute_tree, previous_tree)
                    replaced = True
                return result

            def record_generation_move(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage recovery state is ambiguous",
            ):
                result = real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )
                if source_name in {LINEAGE_ROOT.name, "previous"}:
                    rename_order.append((source_name, destination_name))
                return result

            def record_noreplace_move(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage recovery state is ambiguous",
            ):
                result = real_rename_noreplace(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )
                if source_name in {LINEAGE_ROOT.name, "previous"}:
                    rename_order.append((source_name, destination_name))
                return result

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher,
                    "_open_directory_at",
                    side_effect=remember_handle,
                ),
                mock.patch.object(
                    self.refresher,
                    "_require_directory_name",
                    side_effect=replace_previous_after_prebind,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=record_generation_move,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_noreplace_at",
                    side_effect=record_noreplace_move,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            self.assertTrue(replaced)
            self.assertEqual(
                str(captured.exception),
                "source-lineage recovery state is ambiguous",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(
                rename_order,
                [
                    (LINEAGE_ROOT.name, "failed"),
                    ("previous", LINEAGE_ROOT.name),
                    (LINEAGE_ROOT.name, "previous"),
                ],
            )
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            self.assertFalse(target.exists())
            self.assertFalse(transaction.exists())
            self.assertIn(
                previous_identity, [handle.identity for handle in opened_handles]
            )
            for handle in opened_handles:
                with self.assertRaises(OSError):
                    os.fstat(handle.descriptor)

            parked_metadata = parked_previous.stat()
            self.assertEqual(
                (parked_metadata.st_dev, parked_metadata.st_ino), previous_identity
            )
            self.assertEqual(self.module.tree_identity(parked_previous), previous_tree)
            retained_transaction = (
                self.refresher._retention_root(clone) / transaction_name
            )
            self.assertTrue(retained_transaction.is_dir())
            self.assertEqual(
                (retained_transaction / self.refresher.TRANSACTION_MARKER).read_bytes(),
                marker_bytes,
            )
            retained_previous = retained_transaction / "previous"
            retained_previous_metadata = retained_previous.stat()
            self.assertEqual(
                (
                    retained_previous_metadata.st_dev,
                    retained_previous_metadata.st_ino,
                ),
                substitute_identity,
            )
            self.assertEqual(
                self.module.tree_identity(retained_previous), substitute_tree
            )
            retained_failed = retained_transaction / "failed"
            retained_failed_metadata = retained_failed.stat()
            self.assertEqual(
                (retained_failed_metadata.st_dev, retained_failed_metadata.st_ino),
                rejected_identity,
            )
            self.assertEqual(self.module.tree_identity(retained_failed), rejected_tree)

            retained_tree = self.module.tree_identity(retained_transaction)
            replay_stdout = io.StringIO()
            replay_stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(replay_stdout),
                contextlib.redirect_stderr(replay_stderr),
            ):
                self.refresher._recover_interrupted_write(clone)
            self.assertEqual(replay_stdout.getvalue(), "")
            self.assertEqual(replay_stderr.getvalue(), "")
            self.assertFalse(target.exists())
            self.assertFalse(transaction.exists())
            self.assertEqual(
                self.module.tree_identity(retained_transaction), retained_tree
            )
            self.assertEqual(
                self.module.tree_identity(retained_previous), substitute_tree
            )
            for private_value in (
                str(clone),
                str(parked_previous),
                rejected_canary,
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_recovery_noreplace_preserves_compensation_collisions(self) -> None:
        cases = (
            ("previous", ("previous",)),
            ("previous-and-staged", ("previous", "staged")),
            ("nested-child-collision", ("previous", "staged")),
            ("post-apply-fsync-failure", ()),
        )
        for case, collision_names in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                clone = self.clone_refresh_fixture(temporary)
                target = clone / LINEAGE_ROOT
                transaction_name = (
                    f"{self.refresher.TRANSACTION_PREFIX}noreplace-{case}"
                )
                transaction = clone / "release" / transaction_name
                previous = transaction / "previous"
                parked_previous = Path(temporary) / "parked-previous"
                transaction.mkdir(mode=0o700)
                transaction.chmod(0o700)
                marker_bytes = self.refresher._transaction_marker(transaction_name)
                self.refresher._atomic_write(
                    transaction / self.refresher.TRANSACTION_MARKER,
                    marker_bytes,
                )
                shutil.copytree(target, previous)
                previous_metadata = previous.stat()
                previous_identity = (
                    previous_metadata.st_dev,
                    previous_metadata.st_ino,
                )
                previous_tree = self.module.tree_identity(previous)

                rejected_canary = f"private-noreplace-rejected-{case}"
                (target / "private-rejected.txt").write_text(
                    rejected_canary + "\n",
                    encoding="utf-8",
                )
                rejected_metadata = target.stat()
                rejected_identity = (
                    rejected_metadata.st_dev,
                    rejected_metadata.st_ino,
                )
                rejected_tree = self.module.tree_identity(target)
                staged_occupant_canary = f"private-existing-staged-{case}"

                real_open_directory_at = self.refresher._open_directory_at
                real_require_directory_name = self.refresher._require_directory_name
                real_rename = self.refresher._rename_at
                real_rename_noreplace = self.refresher._rename_noreplace_at
                real_new_recovery_quarantine = (
                    self.refresher._new_recovery_quarantine_at
                )
                opened_handles = []
                rename_order = []
                noreplace_attempts = []
                quarantine_calls = []
                occupied = {}
                replaced = False
                substitute_identity = None
                substitute_tree = None
                post_apply_failure = False
                nested_collision_injected = False
                nested_collision_identity = None

                def remember_handle(
                    parent_descriptor,
                    name,
                    diagnostic,
                    _real_open_directory_at=real_open_directory_at,
                    _opened_handles=opened_handles,
                ):
                    handle = _real_open_directory_at(
                        parent_descriptor,
                        name,
                        diagnostic,
                    )
                    _opened_handles.append(handle)
                    return handle

                def replace_previous_after_prebind(
                    parent_descriptor,
                    name,
                    handle,
                    diagnostic,
                    _real_require_directory_name=real_require_directory_name,
                    _previous=previous,
                    _parked_previous=parked_previous,
                    _previous_identity=previous_identity,
                    _previous_tree=previous_tree,
                ):
                    nonlocal replaced, substitute_identity, substitute_tree
                    result = _real_require_directory_name(
                        parent_descriptor,
                        name,
                        handle,
                        diagnostic,
                    )
                    if name == "previous" and not replaced:
                        self.assertEqual(handle.identity, _previous_identity)
                        _previous.rename(_parked_previous)
                        shutil.copytree(_parked_previous, _previous)
                        metadata = _previous.stat()
                        substitute_identity = metadata.st_dev, metadata.st_ino
                        substitute_tree = self.module.tree_identity(_previous)
                        self.assertNotEqual(
                            substitute_identity,
                            _previous_identity,
                        )
                        self.assertEqual(substitute_tree, _previous_tree)
                        replaced = True
                    return result

                def occupy_compensation_names(
                    _collision_names=collision_names,
                    _transaction=transaction,
                    _occupied=occupied,
                    _staged_occupant_canary=staged_occupant_canary,
                ):
                    for name in _collision_names:
                        path = _transaction / name
                        path.mkdir()
                        sentinel_identity = None
                        if name == "staged":
                            sentinel = path / "preexisting.txt"
                            sentinel.write_text(
                                _staged_occupant_canary + "\n",
                                encoding="utf-8",
                            )
                            sentinel_metadata = sentinel.stat()
                            sentinel_identity = (
                                sentinel_metadata.st_dev,
                                sentinel_metadata.st_ino,
                            )
                        metadata = path.stat()
                        _occupied[name] = (
                            (metadata.st_dev, metadata.st_ino),
                            self.module.tree_identity(path),
                            sentinel_identity,
                        )

                def record_new_recovery_quarantine(
                    parent,
                    budget,
                    _real_new_recovery_quarantine=real_new_recovery_quarantine,
                    _quarantine_calls=quarantine_calls,
                ):
                    name, handle = _real_new_recovery_quarantine(parent, budget)
                    _quarantine_calls.append((budget, name, handle.identity))
                    return name, handle

                def record_generation_move(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    *,
                    applied=None,
                    diagnostic="source-lineage recovery state is ambiguous",
                    _real_rename=real_rename,
                    _rename_order=rename_order,
                ):
                    result = _real_rename(
                        source_parent,
                        source_name,
                        destination_parent,
                        destination_name,
                        applied=applied,
                        diagnostic=diagnostic,
                    )
                    if source_name in {LINEAGE_ROOT.name, "previous"}:
                        _rename_order.append((source_name, destination_name))
                    return result

                def record_noreplace_move(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    *,
                    applied=None,
                    diagnostic="source-lineage recovery state is ambiguous",
                    _noreplace_attempts=noreplace_attempts,
                    _real_rename_noreplace=real_rename_noreplace,
                    _case=case,
                    _quarantine_calls=quarantine_calls,
                    _rename_order=rename_order,
                    _occupied=occupied,
                    _collision_names=collision_names,
                    _occupy_compensation_names=occupy_compensation_names,
                ):
                    nonlocal nested_collision_identity, nested_collision_injected
                    nonlocal post_apply_failure
                    destination_metadata = os.fstat(destination_parent)
                    destination_identity = (
                        destination_metadata.st_dev,
                        destination_metadata.st_ino,
                    )
                    _noreplace_attempts.append(
                        (
                            source_name,
                            destination_name,
                            destination_identity,
                        )
                    )
                    if (
                        _case == "nested-child-collision"
                        and not nested_collision_injected
                        and source_name == LINEAGE_ROOT.name
                        and destination_name == LINEAGE_ROOT.name
                        and destination_identity
                        in {call[2] for call in _quarantine_calls}
                    ):
                        os.mkdir(
                            destination_name,
                            0o700,
                            dir_fd=destination_parent,
                        )
                        metadata = os.stat(
                            destination_name,
                            dir_fd=destination_parent,
                            follow_symlinks=False,
                        )
                        nested_collision_identity = (
                            metadata.st_dev,
                            metadata.st_ino,
                        )
                        nested_collision_injected = True

                    def mark_applied():
                        _rename_order.append((source_name, destination_name))
                        if applied is not None:
                            applied()

                    result = _real_rename_noreplace(
                        source_parent,
                        source_name,
                        destination_parent,
                        destination_name,
                        applied=mark_applied,
                        diagnostic=diagnostic,
                    )
                    if (
                        source_name == "previous"
                        and destination_name == LINEAGE_ROOT.name
                        and not _occupied
                        and _collision_names
                    ):
                        _occupy_compensation_names()
                    if (
                        _case == "post-apply-fsync-failure"
                        and source_name == LINEAGE_ROOT.name
                        and not post_apply_failure
                    ):
                        post_apply_failure = True
                        raise self.refresher.lineage.LineageError(diagnostic)
                    return result

                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        self.refresher,
                        "_open_directory_at",
                        side_effect=remember_handle,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_require_directory_name",
                        side_effect=replace_previous_after_prebind,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_rename_at",
                        side_effect=record_generation_move,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_rename_noreplace_at",
                        side_effect=record_noreplace_move,
                    ),
                    mock.patch.object(
                        self.refresher,
                        "_new_recovery_quarantine_at",
                        side_effect=record_new_recovery_quarantine,
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(self.refresher.lineage.LineageError) as captured,
                ):
                    self.refresher.write(clone)

                self.assertTrue(replaced)
                self.assertEqual(
                    str(captured.exception),
                    "source-lineage recovery state is ambiguous",
                )
                self.assertIsNone(captured.exception.__cause__)
                compensation_destination = {
                    "previous": "staged",
                    "previous-and-staged": LINEAGE_ROOT.name,
                    "nested-child-collision": LINEAGE_ROOT.name,
                    "post-apply-fsync-failure": "previous",
                }[case]
                self.assertEqual(
                    rename_order,
                    [
                        (LINEAGE_ROOT.name, "failed"),
                        ("previous", LINEAGE_ROOT.name),
                        (LINEAGE_ROOT.name, compensation_destination),
                    ],
                )
                self.assertNotEqual(noreplace_attempts, [])
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                self.assertFalse(target.exists())
                self.assertFalse(transaction.exists())
                for handle in opened_handles:
                    with self.assertRaises(OSError):
                        os.fstat(handle.descriptor)

                parked_metadata = parked_previous.stat()
                self.assertEqual(
                    (parked_metadata.st_dev, parked_metadata.st_ino),
                    previous_identity,
                )
                self.assertEqual(
                    self.module.tree_identity(parked_previous), previous_tree
                )
                retained_transaction = (
                    self.refresher._retention_root(clone) / transaction_name
                )
                self.assertTrue(retained_transaction.is_dir())
                self.assertEqual(
                    (
                        retained_transaction / self.refresher.TRANSACTION_MARKER
                    ).read_bytes(),
                    marker_bytes,
                )
                retained_failed = retained_transaction / "failed"
                retained_failed_metadata = retained_failed.stat()
                self.assertEqual(
                    (
                        retained_failed_metadata.st_dev,
                        retained_failed_metadata.st_ino,
                    ),
                    rejected_identity,
                )
                self.assertEqual(
                    self.module.tree_identity(retained_failed), rejected_tree
                )

                if case == "previous":
                    retained_occupant = retained_transaction / "previous"
                    occupant_metadata = retained_occupant.stat()
                    self.assertEqual(
                        (occupant_metadata.st_dev, occupant_metadata.st_ino),
                        occupied["previous"][0],
                    )
                    self.assertEqual(
                        self.module.tree_identity(retained_occupant),
                        occupied["previous"][1],
                    )
                    retained_substitute = retained_transaction / "staged"
                    substitute_metadata = retained_substitute.stat()
                    self.assertEqual(
                        (substitute_metadata.st_dev, substitute_metadata.st_ino),
                        substitute_identity,
                    )
                    self.assertEqual(
                        self.module.tree_identity(retained_substitute),
                        substitute_tree,
                    )
                elif case in {
                    "nested-child-collision",
                    "previous-and-staged",
                }:
                    retained_occupant = retained_transaction / "previous"
                    occupant_metadata = retained_occupant.stat()
                    self.assertEqual(
                        (occupant_metadata.st_dev, occupant_metadata.st_ino),
                        occupied["previous"][0],
                    )
                    self.assertEqual(
                        self.module.tree_identity(retained_occupant),
                        occupied["previous"][1],
                    )
                    retained_staged = retained_transaction / "staged"
                    staged_metadata = retained_staged.stat()
                    self.assertEqual(
                        (staged_metadata.st_dev, staged_metadata.st_ino),
                        occupied["staged"][0],
                    )
                    sentinel = retained_staged / "preexisting.txt"
                    sentinel_metadata = sentinel.stat()
                    self.assertEqual(
                        (sentinel_metadata.st_dev, sentinel_metadata.st_ino),
                        occupied["staged"][2],
                    )
                    self.assertEqual(
                        sentinel.read_text(encoding="utf-8"),
                        staged_occupant_canary + "\n",
                    )
                    wrappers = [
                        path
                        for path in retained_staged.iterdir()
                        if path.name != sentinel.name
                    ]
                    expected_wrapper_count = (
                        2 if case == "nested-child-collision" else 1
                    )
                    self.assertEqual(len(wrappers), expected_wrapper_count)
                    self.assertEqual(len(quarantine_calls), expected_wrapper_count)
                    self.assertEqual(
                        len({id(call[0]) for call in quarantine_calls}),
                        1,
                    )
                    self.assertEqual(
                        {path.name for path in wrappers},
                        {call[1] for call in quarantine_calls},
                    )
                    self.assertEqual(
                        {(path.stat().st_dev, path.stat().st_ino) for path in wrappers},
                        {call[2] for call in quarantine_calls},
                    )
                    for wrapper in wrappers:
                        wrapper_metadata = wrapper.lstat()
                        self.assertTrue(stat.S_ISDIR(wrapper_metadata.st_mode))
                        self.assertFalse(stat.S_ISLNK(wrapper_metadata.st_mode))
                        self.assertEqual(stat.S_IMODE(wrapper_metadata.st_mode), 0o700)
                    wrapper_identities = {call[2] for call in quarantine_calls}
                    nested_attempts = [
                        attempt
                        for attempt in noreplace_attempts
                        if attempt[2] in wrapper_identities
                    ]
                    if case == "nested-child-collision":
                        self.assertTrue(nested_collision_injected)
                        self.assertEqual(len(noreplace_attempts), 5)
                        self.assertEqual(len(nested_attempts), 2)
                        collision_children = [
                            child
                            for wrapper in wrappers
                            for child in wrapper.iterdir()
                            if (
                                child.stat().st_dev,
                                child.stat().st_ino,
                            )
                            == nested_collision_identity
                        ]
                        self.assertEqual(len(collision_children), 1)
                        self.assertEqual(list(collision_children[0].iterdir()), [])
                    else:
                        self.assertFalse(nested_collision_injected)
                        self.assertEqual(len(nested_attempts), 1)
                    quarantined = [
                        child
                        for wrapper in wrappers
                        for child in wrapper.iterdir()
                        if (
                            child.stat().st_dev,
                            child.stat().st_ino,
                        )
                        == substitute_identity
                    ]
                    self.assertEqual(len(quarantined), 1)
                    nested_metadata = quarantined[0].stat()
                    self.assertEqual(
                        (nested_metadata.st_dev, nested_metadata.st_ino),
                        substitute_identity,
                    )
                    self.assertEqual(
                        self.module.tree_identity(quarantined[0]), substitute_tree
                    )
                    substitute_locations = []
                    for path in retained_transaction.rglob("*"):
                        if not path.is_dir():
                            continue
                        metadata = path.stat()
                        if (
                            metadata.st_dev,
                            metadata.st_ino,
                        ) == substitute_identity:
                            substitute_locations.append(path)
                    self.assertEqual(
                        substitute_locations,
                        quarantined,
                    )
                    self.assertEqual(
                        {path.name for path in retained_transaction.iterdir()},
                        {
                            "failed",
                            self.refresher.TRANSACTION_MARKER,
                            "previous",
                            "staged",
                        },
                    )
                else:
                    self.assertTrue(post_apply_failure)
                    retained_substitute = retained_transaction / "previous"
                    substitute_metadata = retained_substitute.stat()
                    self.assertEqual(
                        (substitute_metadata.st_dev, substitute_metadata.st_ino),
                        substitute_identity,
                    )
                    self.assertEqual(
                        self.module.tree_identity(retained_substitute),
                        substitute_tree,
                    )

                retained_tree = self.module.tree_identity(retained_transaction)
                replay_stdout = io.StringIO()
                replay_stderr = io.StringIO()
                with (
                    contextlib.redirect_stdout(replay_stdout),
                    contextlib.redirect_stderr(replay_stderr),
                ):
                    self.refresher._recover_interrupted_write(clone)
                self.assertEqual(replay_stdout.getvalue(), "")
                self.assertEqual(replay_stderr.getvalue(), "")
                self.assertFalse(target.exists())
                self.assertEqual(
                    self.module.tree_identity(retained_transaction), retained_tree
                )
                for private_value in (
                    str(clone),
                    str(parked_previous),
                    rejected_canary,
                    staged_occupant_canary,
                    "Traceback",
                ):
                    self.assertNotIn(private_value, str(captured.exception))

    def test_rename_noreplace_fails_closed_on_an_unsupported_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_parent = root / "source-parent"
            destination_parent = root / "destination-parent"
            source_parent.mkdir()
            destination_parent.mkdir()
            source = source_parent / "source"
            source.mkdir()
            source_identity = source.stat().st_dev, source.stat().st_ino
            source_descriptor = os.open(source_parent, os.O_RDONLY)
            destination_descriptor = os.open(destination_parent, os.O_RDONLY)
            stdout = io.StringIO()
            stderr = io.StringIO()
            try:
                with (
                    mock.patch.object(self.refresher.sys, "platform", "linux"),
                    mock.patch.object(
                        self.refresher.platform,
                        "machine",
                        return_value="unsupported-private-abi",
                    ),
                    mock.patch.object(
                        self.refresher.ctypes,
                        "CDLL",
                        return_value=object(),
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(self.refresher.lineage.LineageError) as captured,
                ):
                    self.refresher._rename_noreplace_at(
                        source_descriptor,
                        "source",
                        destination_descriptor,
                        "destination",
                    )
            finally:
                os.close(source_descriptor)
                os.close(destination_descriptor)

            self.assertEqual(
                str(captured.exception),
                "source-lineage recovery state is ambiguous",
            )
            self.assertIsNone(captured.exception.__cause__)
            metadata = source.stat()
            self.assertEqual((metadata.st_dev, metadata.st_ino), source_identity)
            self.assertFalse((destination_parent / "destination").exists())
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            for private_value in (str(root), "unsupported-private-abi", "Traceback"):
                self.assertNotIn(private_value, str(captured.exception))

    def test_recovery_retention_failure_survives_collision_cleanup_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            transaction_name = f"{self.refresher.TRANSACTION_PREFIX}cleanup-precedence"
            transaction_path = clone / "release" / transaction_name
            transaction_path.mkdir(mode=0o700)
            transaction_path.chmod(0o700)
            self.refresher._atomic_write(
                transaction_path / self.refresher.TRANSACTION_MARKER,
                self.refresher._transaction_marker(transaction_name),
            )
            occupant_identities = set()
            for name in ("previous", "staged"):
                occupant = transaction_path / name
                occupant.mkdir()
                metadata = occupant.stat()
                occupant_identities.add((metadata.st_dev, metadata.st_ino))

            real_open_directory_at = self.refresher._open_directory_at
            real_close = os.close
            collision_handles = []
            collision_close_attempts = []
            close_failed = False
            canary = "private-collision-close-canary"
            stdout = io.StringIO()
            stderr = io.StringIO()

            def remember_collision_handle(parent_descriptor, name, diagnostic):
                handle = real_open_directory_at(parent_descriptor, name, diagnostic)
                if handle.identity in occupant_identities:
                    collision_handles.append(handle)
                return handle

            def fail_one_collision_close(descriptor):
                nonlocal close_failed
                collision_descriptors = {
                    handle.descriptor for handle in collision_handles
                }
                if descriptor in collision_descriptors:
                    collision_close_attempts.append(descriptor)
                    real_close(descriptor)
                    if not close_failed:
                        close_failed = True
                        raise OSError(canary)
                    return
                real_close(descriptor)

            def fail_retention(*_args, **_kwargs):
                raise self.refresher.lineage.LineageError(
                    "source-lineage recovery retention failed"
                ) from None

            with self.refresher.lineage._lineage_lock(
                clone,
                exclusive=True,
            ) as view:
                budget = self.refresher.lineage._new_tree_budget(
                    "source-lineage recovery state is ambiguous"
                )
                transaction = self.refresher._open_owned_transaction_at(
                    view.release_descriptor,
                    transaction_name,
                    budget,
                )
                generation = self.refresher._open_directory_at(
                    view.release_descriptor,
                    LINEAGE_ROOT.name,
                    "source-lineage recovery state is ambiguous",
                )
                try:
                    with (
                        mock.patch.object(
                            self.refresher,
                            "_open_directory_at",
                            side_effect=remember_collision_handle,
                        ),
                        mock.patch.object(
                            self.refresher,
                            "_retain_transaction",
                            side_effect=fail_retention,
                        ) as retain,
                        mock.patch.object(
                            self.refresher.os,
                            "close",
                            side_effect=fail_one_collision_close,
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                        self.assertRaises(
                            self.refresher.lineage.LineageError
                        ) as captured,
                    ):
                        self.refresher._retract_public_generation(
                            clone,
                            view,
                            transaction,
                            LINEAGE_ROOT.name,
                            generation,
                            budget,
                        )
                finally:
                    self.refresher._close_directories(
                        generation,
                        transaction.directory,
                        diagnostic="source-lineage recovery state is ambiguous",
                    )

            self.assertEqual(
                str(captured.exception),
                "source-lineage recovery retention failed",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(retain.call_count, 1)
            self.assertTrue(close_failed)
            self.assertEqual(len(collision_handles), 2)
            self.assertCountEqual(
                collision_close_attempts,
                [handle.descriptor for handle in collision_handles],
            )
            for handle in collision_handles:
                with self.assertRaises(OSError):
                    os.fstat(handle.descriptor)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            for private_value in (str(clone), canary, "Traceback"):
                self.assertNotIn(private_value, str(captured.exception))

    def test_recovery_does_not_overwrite_a_destination_appearing_after_rejection(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            transaction_name = f"{self.refresher.TRANSACTION_PREFIX}target-race"
            transaction = clone / "release" / transaction_name
            previous = transaction / "previous"
            transaction.mkdir(mode=0o700)
            transaction.chmod(0o700)
            marker_bytes = self.refresher._transaction_marker(transaction_name)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                marker_bytes,
            )
            shutil.copytree(target, previous)
            previous_metadata = previous.stat()
            previous_identity = previous_metadata.st_dev, previous_metadata.st_ino
            previous_tree = self.module.tree_identity(previous)

            rejected_canary = "private-rejected-destination-race"
            (target / "private-rejected.txt").write_text(
                rejected_canary + "\n",
                encoding="utf-8",
            )
            rejected_metadata = target.stat()
            rejected_identity = rejected_metadata.st_dev, rejected_metadata.st_ino
            rejected_tree = self.module.tree_identity(target)

            destination_canary = "private-unknown-public-destination"
            real_open_directory_at = self.refresher._open_directory_at
            real_rename = self.refresher._rename_at
            opened_handles = []
            rename_attempts = []
            destination_identity = None
            destination_tree = None

            def remember_handle(parent_descriptor, name, diagnostic):
                handle = real_open_directory_at(parent_descriptor, name, diagnostic)
                opened_handles.append(handle)
                return handle

            def insert_destination_after_rejection(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage recovery state is ambiguous",
            ):
                nonlocal destination_identity, destination_tree
                if source_name in {LINEAGE_ROOT.name, "previous"}:
                    rename_attempts.append((source_name, destination_name))
                result = real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )
                if (
                    source_name == LINEAGE_ROOT.name
                    and destination_name == "failed"
                    and destination_identity is None
                ):
                    target.mkdir()
                    (target / "private-destination.txt").write_text(
                        destination_canary + "\n",
                        encoding="utf-8",
                    )
                    metadata = target.stat()
                    destination_identity = metadata.st_dev, metadata.st_ino
                    destination_tree = self.module.tree_identity(target)
                return result

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher,
                    "_open_directory_at",
                    side_effect=remember_handle,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=insert_destination_after_rejection,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            self.assertEqual(
                str(captured.exception),
                "source-lineage recovery state is ambiguous",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(
                rename_attempts,
                [(LINEAGE_ROOT.name, "failed")],
            )
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            for handle in opened_handles:
                with self.assertRaises(OSError):
                    os.fstat(handle.descriptor)

            destination_metadata = target.stat()
            self.assertEqual(
                (destination_metadata.st_dev, destination_metadata.st_ino),
                destination_identity,
            )
            self.assertEqual(self.module.tree_identity(target), destination_tree)
            self.assertEqual(
                (target / "private-destination.txt").read_text(encoding="utf-8"),
                destination_canary + "\n",
            )
            self.assertFalse(transaction.exists())
            retained_transaction = (
                self.refresher._retention_root(clone) / transaction_name
            )
            self.assertTrue(retained_transaction.is_dir())
            self.assertEqual(
                (retained_transaction / self.refresher.TRANSACTION_MARKER).read_bytes(),
                marker_bytes,
            )
            retained_previous = retained_transaction / "previous"
            retained_previous_metadata = retained_previous.stat()
            self.assertEqual(
                (
                    retained_previous_metadata.st_dev,
                    retained_previous_metadata.st_ino,
                ),
                previous_identity,
            )
            self.assertEqual(
                self.module.tree_identity(retained_previous), previous_tree
            )
            retained_failed = retained_transaction / "failed"
            retained_failed_metadata = retained_failed.stat()
            self.assertEqual(
                (retained_failed_metadata.st_dev, retained_failed_metadata.st_ino),
                rejected_identity,
            )
            self.assertEqual(self.module.tree_identity(retained_failed), rejected_tree)
            for private_value in (
                str(clone),
                rejected_canary,
                destination_canary,
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_marker_interruption_leaves_ignored_preparation_and_next_write_works(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            real_atomic_write = self.refresher._atomic_write
            interrupted = False

            def interrupt_after_marker(path, content):
                nonlocal interrupted
                result = real_atomic_write(path, content)
                if (
                    Path(path).name == self.refresher.TRANSACTION_MARKER
                    and not interrupted
                ):
                    interrupted = True
                    raise KeyboardInterrupt
                return result

            with (
                mock.patch.object(
                    self.refresher,
                    "_atomic_write",
                    side_effect=interrupt_after_marker,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                self.refresher.write(clone)

            preparations = list(
                self.refresher._retention_root(clone).glob(
                    ".source-lineage-preparation-*"
                )
            )
            self.assertTrue(interrupted)
            self.assertEqual(len(preparations), 1)
            self.assertEqual(
                list((clone / "release").glob(".source-lineage-preparation-*")), []
            )
            self.assertEqual(
                list((clone / "release").glob(".source-lineage-transaction-*")), []
            )
            self.refresher.write(clone)
            self.module.validate_lineage(clone)
            self.assertTrue(preparations[0].is_dir())

    def test_recovery_root_rebinds_parent_durability_after_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            git_directory = Path(
                self.refresher._git(clone, "rev-parse", "--absolute-git-dir")
            )
            git_identity = self.refresher._directory_identity(git_directory.stat())
            real_fsync = self.refresher._fsync_descriptor
            failed = False

            def fail_git_directory_once(descriptor, diagnostic):
                nonlocal failed
                if (
                    self.refresher._directory_identity(os.fstat(descriptor))
                    == git_identity
                    and not failed
                ):
                    failed = True
                    raise OSError("private-canary-git-directory-fsync")
                real_fsync(descriptor, diagnostic)

            with (
                mock.patch.object(
                    self.refresher,
                    "_fsync_descriptor",
                    side_effect=fail_git_directory_once,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "source-lineage recovery store is unavailable",
                ) as captured,
            ):
                self.refresher._retention_root(clone)

            self.assertTrue(failed)
            self.assertNotIn("private-canary", str(captured.exception))
            root = self.refresher._retention_root(clone)
            self.assertEqual(root.parent, git_directory)
            self.assertEqual(stat.S_IMODE(root.lstat().st_mode), 0o700)

    def test_recovery_store_creation_stays_on_open_git_directory_after_root_swap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            original_parent = temporary / "original-parent"
            replacement_parent = temporary / "replacement-parent"
            original_parent.mkdir()
            replacement_parent.mkdir()
            visible_root = self.clone_refresh_fixture(str(original_parent))
            replacement_root = self.clone_refresh_fixture(str(replacement_parent))
            locked_root = temporary / "locked-repository"

            def identity(path: Path) -> tuple[int, int]:
                metadata = path.lstat()
                return metadata.st_dev, metadata.st_ino

            replacement_before = self.module.tree_identity(replacement_root)
            original_git_identity = identity(visible_root / ".git")
            replacement_git_identity = identity(replacement_root / ".git")
            real_open_git = self.refresher._open_git_directory
            real_mkdir = self.refresher.os.mkdir
            real_fsync = self.refresher._fsync_descriptor
            swapped = False
            mkdir_parents = []
            fsynced = []

            def open_git_then_substitute_root(repository, view=None):
                nonlocal swapped
                result = real_open_git(repository, view)
                self.assertFalse(swapped)
                visible_root.rename(locked_root)
                replacement_root.rename(visible_root)
                swapped = True
                return result

            def record_mkdir(path, mode=0o777, *, dir_fd=None):
                if dir_fd is not None:
                    mkdir_parents.append(
                        self.refresher._directory_identity(os.fstat(dir_fd))
                    )
                return real_mkdir(path, mode, dir_fd=dir_fd)

            def record_fsync(descriptor, diagnostic):
                fsynced.append(self.refresher._directory_identity(os.fstat(descriptor)))
                return real_fsync(descriptor, diagnostic)

            with (
                self.refresher.lineage._lineage_lock(
                    visible_root, exclusive=True, nonblocking=True
                ) as view,
                mock.patch.object(
                    self.refresher,
                    "_open_git_directory",
                    side_effect=open_git_then_substitute_root,
                ),
                mock.patch.object(self.refresher.os, "mkdir", side_effect=record_mkdir),
                mock.patch.object(
                    self.refresher, "_fsync_descriptor", side_effect=record_fsync
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._open_retention_root(view.root, view)

            self.assertTrue(swapped)
            self.assertEqual(mkdir_parents, [original_git_identity])
            self.assertEqual(fsynced, [original_git_identity])
            self.assertNotIn(replacement_git_identity, mkdir_parents)
            self.assertNotIn(replacement_git_identity, fsynced)
            self.assertTrue((locked_root / ".git/source-lineage-recovery").is_dir())
            self.assertFalse((visible_root / ".git/source-lineage-recovery").exists())
            self.assertEqual(
                self.module.tree_identity(visible_root), replacement_before
            )
            self.assertEqual(
                str(captured.exception), "source-lineage artifact tree drift"
            )
            self.assertNotIn(str(temporary), str(captured.exception))

    def test_write_preserves_an_unmanaged_file_added_at_the_first_rename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            canary = target / "private-canary-note.txt"
            real_rename = self.refresher._rename_at
            injected = False

            def inject_before_first_rename(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage artifact publication failed",
            ):
                nonlocal injected
                if (
                    source_name == LINEAGE_ROOT.name
                    and destination_name == "previous"
                    and not injected
                ):
                    injected = True
                    canary.write_text("preserve me\n", encoding="utf-8")
                return real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )

            with (
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=inject_before_first_rename,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "source-lineage artifact publication failed",
                ) as captured,
            ):
                self.refresher.write(clone)

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertTrue(injected)
            self.assertEqual(canary.read_text(encoding="utf-8"), "preserve me\n")
            self.assertEqual(
                list((clone / "release").glob(".source-lineage-transaction-*")), []
            )

    def test_write_rolls_back_when_owner_changes_after_the_first_rename(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            original_tree = self.module.tree_identity(target)
            original_metadata = target.stat()
            original_identity = original_metadata.st_dev, original_metadata.st_ino
            real_new_transaction = self.refresher._new_transaction
            real_open_directory_at = self.refresher._open_directory_at
            real_rename = self.refresher._rename_at
            transaction_state = {}
            opened_handles = []
            rename_order = []
            invalid_marker = None
            canary = "private-owner-after-first-rename-canary"

            def remember_transaction(repository, view, rendered):
                result = real_new_transaction(repository, view, rendered)
                transaction_state["handle"] = result[0]
                return result

            def remember_handle(parent_descriptor, name, diagnostic):
                handle = real_open_directory_at(parent_descriptor, name, diagnostic)
                opened_handles.append(handle)
                return handle

            def replace_owner_after_first_rename(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage artifact publication failed",
            ):
                nonlocal invalid_marker
                result = real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )
                if source_name in {LINEAGE_ROOT.name, "previous"}:
                    rename_order.append((source_name, destination_name))
                if (
                    source_name == LINEAGE_ROOT.name
                    and destination_name == "previous"
                    and invalid_marker is None
                ):
                    transaction = transaction_state["handle"]
                    transaction_path = clone / "release" / transaction.name
                    marker = transaction_path / self.refresher.TRANSACTION_MARKER
                    expected_marker = self.refresher._transaction_marker(
                        transaction.name
                    )
                    invalid_marker = canary.encode("ascii").ljust(
                        len(expected_marker), b"x"
                    )[: len(expected_marker)]
                    self.assertEqual(len(invalid_marker), len(expected_marker))
                    self.assertNotEqual(invalid_marker, expected_marker)
                    replacement = transaction_path / ".replacement-owner"
                    self.refresher._atomic_write(replacement, invalid_marker)
                    self.refresher._durable_replace(replacement, marker)
                return result

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher,
                    "_new_transaction",
                    side_effect=remember_transaction,
                ),
                mock.patch.object(
                    self.refresher,
                    "_open_directory_at",
                    side_effect=remember_handle,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=replace_owner_after_first_rename,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            self.assertEqual(
                str(captured.exception),
                "source-lineage artifact publication failed",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(
                rename_order,
                [
                    (LINEAGE_ROOT.name, "previous"),
                    ("previous", LINEAGE_ROOT.name),
                ],
            )
            for handle in opened_handles:
                with self.assertRaises(OSError):
                    os.fstat(handle.descriptor)
            restored_metadata = target.stat()
            self.assertEqual(
                (restored_metadata.st_dev, restored_metadata.st_ino),
                original_identity,
            )
            self.assertEqual(self.module.tree_identity(target), original_tree)
            transaction = transaction_state["handle"]
            transaction_path = clone / "release" / transaction.name
            self.assertTrue(transaction_path.is_dir())
            self.assertEqual(
                (transaction_path / self.refresher.TRANSACTION_MARKER).read_bytes(),
                invalid_marker,
            )
            self.assertTrue((transaction_path / "staged").is_dir())
            self.assertFalse((transaction_path / "previous").exists())
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            for private_value in (
                str(clone),
                canary,
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_write_rolls_back_if_owner_changes_before_retention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            original_tree = self.module.tree_identity(target)
            original_metadata = target.stat()
            original_identity = original_metadata.st_dev, original_metadata.st_ino
            real_open_directory_at = self.refresher._open_directory_at
            real_rename = self.refresher._rename_at
            real_retain = self.refresher._retain_transaction
            opened_handles = []
            rename_order = []
            transaction_state = {}
            invalid_marker = None
            canary = "private-owner-before-retention-canary"

            def remember_handle(parent_descriptor, name, diagnostic):
                handle = real_open_directory_at(parent_descriptor, name, diagnostic)
                opened_handles.append(handle)
                return handle

            def record_generation_move(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage artifact publication failed",
            ):
                result = real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )
                if source_name in {LINEAGE_ROOT.name, "previous"}:
                    rename_order.append((source_name, destination_name))
                return result

            def replace_owner_before_retention(repository, view, transaction):
                nonlocal invalid_marker
                self.assertIsNone(invalid_marker)
                transaction_state["handle"] = transaction
                transaction_path = clone / "release" / transaction.name
                marker = transaction_path / self.refresher.TRANSACTION_MARKER
                expected_marker = self.refresher._transaction_marker(transaction.name)
                invalid_marker = canary.encode("ascii").ljust(
                    len(expected_marker), b"x"
                )[: len(expected_marker)]
                self.assertEqual(len(invalid_marker), len(expected_marker))
                self.assertNotEqual(invalid_marker, expected_marker)
                replacement = transaction_path / ".replacement-owner"
                self.refresher._atomic_write(replacement, invalid_marker)
                self.refresher._durable_replace(replacement, marker)
                return real_retain(repository, view, transaction)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher,
                    "_open_directory_at",
                    side_effect=remember_handle,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=record_generation_move,
                ),
                mock.patch.object(
                    self.refresher,
                    "_retain_transaction",
                    side_effect=replace_owner_before_retention,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            self.assertEqual(
                str(captured.exception),
                "source-lineage artifact publication failed",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(
                rename_order,
                [
                    (LINEAGE_ROOT.name, "previous"),
                    (LINEAGE_ROOT.name, LINEAGE_ROOT.name),
                    (LINEAGE_ROOT.name, "failed"),
                    ("previous", LINEAGE_ROOT.name),
                ],
            )
            for handle in opened_handles:
                with self.assertRaises(OSError):
                    os.fstat(handle.descriptor)
            restored_metadata = target.stat()
            self.assertEqual(
                (restored_metadata.st_dev, restored_metadata.st_ino),
                original_identity,
            )
            self.assertEqual(self.module.tree_identity(target), original_tree)
            transaction = transaction_state["handle"]
            retention_root = self.refresher._retention_root(clone)
            candidates = (
                clone / "release" / transaction.name,
                retention_root / transaction.name,
            )
            retained = [candidate for candidate in candidates if candidate.is_dir()]
            self.assertEqual(len(retained), 1)
            self.assertEqual(
                (retained[0] / self.refresher.TRANSACTION_MARKER).read_bytes(),
                invalid_marker,
            )
            self.assertTrue((retained[0] / "failed").is_dir())
            self.assertFalse((retained[0] / "previous").exists())
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            for private_value in (
                str(clone),
                canary,
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_write_rejects_public_target_substitution_after_install_bind(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            original_tree = self.module.tree_identity(target)
            original_metadata = target.stat()
            original_identity = original_metadata.st_dev, original_metadata.st_ino
            real_new_transaction = self.refresher._new_transaction
            real_open_directory_at = self.refresher._open_directory_at
            real_validate_generation = self.refresher._validate_generation_at
            transaction_state = {}
            opened_handles = []
            substitution_calls = []
            staged_identity = None

            def remember_transaction(repository, view, rendered):
                result = real_new_transaction(repository, view, rendered)
                transaction_state["handle"] = result[0]
                return result

            def remember_handle(parent_descriptor, name, diagnostic):
                handle = real_open_directory_at(parent_descriptor, name, diagnostic)
                opened_handles.append(handle)
                return handle

            def substitute_previous_then_validate(
                repository, view, parent_descriptor, name
            ):
                nonlocal staged_identity
                self.assertEqual(substitution_calls, [])
                transaction = transaction_state["handle"]
                transaction_path = clone / "release" / transaction.name
                previous = transaction_path / "previous"
                failed = transaction_path / "failed"
                visible_metadata = target.stat()
                staged_identity = (
                    visible_metadata.st_dev,
                    visible_metadata.st_ino,
                )
                self.assertNotEqual(staged_identity, original_identity)
                target.rename(failed)
                previous.rename(target)
                substitution_calls.append((failed, target))
                return real_validate_generation(
                    repository,
                    view,
                    parent_descriptor,
                    name,
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher,
                    "_new_transaction",
                    side_effect=remember_transaction,
                ),
                mock.patch.object(
                    self.refresher,
                    "_open_directory_at",
                    side_effect=remember_handle,
                ),
                mock.patch.object(
                    self.refresher,
                    "_validate_generation_at",
                    side_effect=substitute_previous_then_validate,
                ) as validate_generation,
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            validate_generation.assert_called_once()
            self.assertEqual(len(substitution_calls), 1)
            self.assertEqual(
                str(captured.exception),
                "source-lineage artifact publication failed",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            for handle in opened_handles:
                with self.assertRaises(OSError):
                    os.fstat(handle.descriptor)

            restored_metadata = target.stat()
            self.assertEqual(
                (restored_metadata.st_dev, restored_metadata.st_ino),
                original_identity,
            )
            self.assertEqual(self.module.tree_identity(target), original_tree)
            self.assertNotEqual(
                (restored_metadata.st_dev, restored_metadata.st_ino),
                staged_identity,
            )

            transaction = transaction_state["handle"]
            retention_root = self.refresher._retention_root(clone)
            candidates = (
                clone / "release" / transaction.name,
                retention_root / transaction.name,
            )
            retained = [candidate for candidate in candidates if candidate.is_dir()]
            self.assertEqual(len(retained), 1)
            failed_metadata = (retained[0] / "failed").stat()
            self.assertEqual(
                (failed_metadata.st_dev, failed_metadata.st_ino),
                staged_identity,
            )
            self.assertFalse((retained[0] / "previous").exists())
            for private_value in (
                str(clone),
                transaction.name,
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_write_rejects_public_removal_after_final_retained_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            original_tree = self.module.tree_identity(target)
            original_metadata = target.stat()
            original_identity = original_metadata.st_dev, original_metadata.st_ino
            retention_root = self.refresher._retention_root(clone)
            real_new_transaction = self.refresher._new_transaction
            real_open_directory_at = self.refresher._open_directory_at
            real_retain_transaction = self.refresher._retain_transaction
            real_snapshot = self.refresher.lineage._lineage_snapshot_descriptor
            transaction_state = {}
            opened_handles = []
            retained = False
            removal_calls = []
            staged_identity = None

            def remember_transaction(repository, view, rendered):
                result = real_new_transaction(repository, view, rendered)
                transaction_state["handle"] = result[0]
                return result

            def remember_handle(parent_descriptor, name, diagnostic):
                handle = real_open_directory_at(parent_descriptor, name, diagnostic)
                opened_handles.append(handle)
                return handle

            def retain_then_mark(repository, view, transaction):
                nonlocal retained
                result = real_retain_transaction(repository, view, transaction)
                retained = True
                return result

            def snapshot_then_remove_visible(descriptor, *args, **kwargs):
                nonlocal staged_identity
                snapshot = real_snapshot(descriptor, *args, **kwargs)
                if retained and not removal_calls:
                    transaction = transaction_state["handle"]
                    failed = retention_root / transaction.name / "failed"
                    visible_metadata = target.stat()
                    staged_identity = (
                        visible_metadata.st_dev,
                        visible_metadata.st_ino,
                    )
                    opened_metadata = os.fstat(descriptor)
                    self.assertEqual(
                        (opened_metadata.st_dev, opened_metadata.st_ino),
                        staged_identity,
                    )
                    self.assertFalse(failed.exists())
                    target.rename(failed)
                    removal_calls.append(failed)
                return snapshot

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    self.refresher,
                    "_new_transaction",
                    side_effect=remember_transaction,
                ),
                mock.patch.object(
                    self.refresher,
                    "_open_directory_at",
                    side_effect=remember_handle,
                ),
                mock.patch.object(
                    self.refresher,
                    "_retain_transaction",
                    side_effect=retain_then_mark,
                ) as retain_transaction,
                mock.patch.object(
                    self.refresher.lineage,
                    "_lineage_snapshot_descriptor",
                    side_effect=snapshot_then_remove_visible,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher.write(clone)

            retain_transaction.assert_called_once()
            self.assertTrue(retained)
            self.assertEqual(len(removal_calls), 1)
            self.assertEqual(
                str(captured.exception),
                "source-lineage artifact publication failed",
            )
            self.assertIsNone(captured.exception.__cause__)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            for handle in opened_handles:
                with self.assertRaises(OSError):
                    os.fstat(handle.descriptor)

            restored_metadata = target.stat()
            self.assertEqual(
                (restored_metadata.st_dev, restored_metadata.st_ino),
                original_identity,
            )
            self.assertEqual(self.module.tree_identity(target), original_tree)
            self.assertNotEqual(
                (restored_metadata.st_dev, restored_metadata.st_ino),
                staged_identity,
            )

            transaction = transaction_state["handle"]
            retained_transaction = retention_root / transaction.name
            self.assertTrue(retained_transaction.is_dir())
            failed_metadata = (retained_transaction / "failed").stat()
            self.assertEqual(
                (failed_metadata.st_dev, failed_metadata.st_ino),
                staged_identity,
            )
            self.assertFalse((retained_transaction / "previous").exists())
            for private_value in (
                str(clone),
                transaction.name,
                "Traceback",
            ):
                self.assertNotIn(private_value, str(captured.exception))

    def test_write_retains_old_tree_bytes_added_after_the_final_identity_check(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            real_retain = self.refresher._retain_transaction
            injected = False

            def inject_before_retention(repository, view, transaction):
                nonlocal injected
                previous = clone / "release" / transaction.name / "previous"
                if previous.is_dir() and not injected:
                    injected = True
                    (previous / "private-canary-note.txt").write_text(
                        "preserve me\n", encoding="utf-8"
                    )
                return real_retain(repository, view, transaction)

            with mock.patch.object(
                self.refresher,
                "_retain_transaction",
                side_effect=inject_before_retention,
            ):
                self.refresher.write(clone)

            retained = list(
                self.refresher._retention_root(clone).glob(
                    ".source-lineage-transaction-*"
                )
            )
            self.assertTrue(injected)
            self.assertEqual(len(retained), 1)
            self.assertEqual(
                (retained[0] / "previous/private-canary-note.txt").read_text(
                    encoding="utf-8"
                ),
                "preserve me\n",
            )
            self.assertEqual(
                list((clone / "release").glob(".source-lineage-transaction-*")), []
            )

    def test_recovery_root_drift_during_target_validation_prevents_rollback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            original_parent = temporary / "original-parent"
            replacement_parent = temporary / "replacement-parent"
            original_parent.mkdir()
            replacement_parent.mkdir()
            visible_root = self.clone_refresh_fixture(str(original_parent))
            replacement_root = self.clone_refresh_fixture(str(replacement_parent))
            locked_root = temporary / "locked-repository"
            target = visible_root / LINEAGE_ROOT
            transaction = (
                visible_root / "release" / ".source-lineage-transaction-canary"
            )
            previous = transaction / "previous"
            transaction.mkdir(mode=0o700)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                self.refresher._transaction_marker(transaction),
            )
            shutil.copytree(target, previous)

            def snapshot(root: Path) -> dict[str, object]:
                root_metadata = root.lstat()
                git_directory = root / ".git"
                git_metadata = git_directory.lstat()
                return {
                    "root_identity": (root_metadata.st_dev, root_metadata.st_ino),
                    "root_tree": self.module.tree_identity(root),
                    "git_identity": (git_metadata.st_dev, git_metadata.st_ino),
                    "git_tree": self.module.tree_identity(git_directory),
                }

            locked_before = snapshot(visible_root)
            replacement_before = snapshot(replacement_root)
            real_validate = self.refresher._validate_generation_at
            swapped = False

            def substitute_root_then_validate(*arguments):
                nonlocal swapped
                self.assertFalse(swapped)
                visible_root.rename(locked_root)
                replacement_root.rename(visible_root)
                swapped = True
                return real_validate(*arguments)

            with (
                mock.patch.object(
                    self.refresher,
                    "_validate_generation_at",
                    side_effect=substitute_root_then_validate,
                ),
                mock.patch.object(self.refresher, "_rename_at") as replace,
                mock.patch.object(self.refresher, "_retain_transaction") as retain,
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._recover_interrupted_write(visible_root)

            self.assertTrue(swapped)
            replace.assert_not_called()
            retain.assert_not_called()
            self.assertEqual(snapshot(locked_root), locked_before)
            self.assertEqual(snapshot(visible_root), replacement_before)
            self.assertEqual(
                str(captured.exception), "source-lineage artifact tree drift"
            )
            self.assertNotIn(str(temporary), str(captured.exception))

    def test_recovery_root_drift_after_missing_target_discovery_prevents_restore(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            original_parent = temporary / "original-parent"
            replacement_parent = temporary / "replacement-parent"
            original_parent.mkdir()
            replacement_parent.mkdir()
            visible_root = self.clone_refresh_fixture(str(original_parent))
            replacement_root = self.clone_refresh_fixture(str(replacement_parent))
            locked_root = temporary / "locked-repository"
            target = visible_root / LINEAGE_ROOT
            transaction = (
                visible_root / "release" / ".source-lineage-transaction-canary"
            )
            previous = transaction / "previous"
            transaction.mkdir(mode=0o700)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                self.refresher._transaction_marker(transaction),
            )
            shutil.copytree(target, previous)
            shutil.rmtree(target)

            def snapshot(root: Path) -> dict[str, object]:
                root_metadata = root.lstat()
                git_directory = root / ".git"
                git_metadata = git_directory.lstat()
                return {
                    "root_identity": (root_metadata.st_dev, root_metadata.st_ino),
                    "root_tree": self.module.tree_identity(root),
                    "git_identity": (git_metadata.st_dev, git_metadata.st_ino),
                    "git_tree": self.module.tree_identity(git_directory),
                }

            locked_before = snapshot(visible_root)
            replacement_before = snapshot(replacement_root)
            real_exists = self.refresher._directory_exists_at
            swapped = False

            def discover_then_substitute_root(parent_descriptor, name):
                nonlocal swapped
                exists = real_exists(parent_descriptor, name)
                if name == "previous" and exists and not swapped:
                    visible_root.rename(locked_root)
                    replacement_root.rename(visible_root)
                    swapped = True
                return exists

            with (
                mock.patch.object(
                    self.refresher,
                    "_directory_exists_at",
                    side_effect=discover_then_substitute_root,
                ),
                mock.patch.object(self.refresher, "_rename_at") as replace,
                mock.patch.object(self.refresher, "_retain_transaction") as retain,
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._recover_interrupted_write(visible_root)

            self.assertTrue(swapped)
            replace.assert_not_called()
            retain.assert_not_called()
            self.assertEqual(snapshot(locked_root), locked_before)
            self.assertEqual(snapshot(visible_root), replacement_before)
            self.assertEqual(
                str(captured.exception), "source-lineage artifact tree drift"
            )
            self.assertNotIn(str(temporary), str(captured.exception))

    def test_recovery_post_apply_fsync_failure_restores_previous_generation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            transaction = clone / "release" / ".source-lineage-transaction-canary"
            previous = transaction / "previous"
            transaction.mkdir(mode=0o700)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                self.refresher._transaction_marker(transaction),
            )
            shutil.copytree(target, previous)
            previous_metadata = previous.lstat()
            previous_before = (
                previous_metadata.st_dev,
                previous_metadata.st_ino,
                self.module.tree_identity(previous),
            )
            canary = target / "private-canary-note.txt"
            canary.write_text("preserve me\n", encoding="utf-8")
            real_fsync = self.refresher.os.fsync
            failed = False

            def fail_first_rejection_parent_fsync(descriptor):
                nonlocal failed
                metadata = os.fstat(descriptor)
                names = (
                    set(os.listdir(descriptor))
                    if stat.S_ISDIR(metadata.st_mode)
                    else set()
                )
                if (
                    not failed
                    and {
                        self.refresher.TRANSACTION_MARKER,
                        "failed",
                        "previous",
                    }
                    <= names
                ):
                    failed = True
                    raise OSError("private-canary-rejection-fsync")
                real_fsync(descriptor)

            with (
                mock.patch.object(
                    self.refresher.os,
                    "fsync",
                    side_effect=fail_first_rejection_parent_fsync,
                ),
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._recover_interrupted_write(clone)

            target_metadata = target.lstat()
            self.assertTrue(failed)
            self.assertEqual(
                (
                    target_metadata.st_dev,
                    target_metadata.st_ino,
                    self.module.tree_identity(target),
                ),
                previous_before,
            )
            self.assertEqual(
                (transaction / "failed/private-canary-note.txt").read_text(
                    encoding="utf-8"
                ),
                "preserve me\n",
            )
            self.assertEqual(
                str(captured.exception), "source-lineage recovery state is ambiguous"
            )
            self.assertNotIn("private-canary", str(captured.exception))

    def test_recovery_root_drift_between_rejection_renames_restores_previous(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            original_parent = temporary / "original-parent"
            replacement_parent = temporary / "replacement-parent"
            original_parent.mkdir()
            replacement_parent.mkdir()
            visible_root = self.clone_refresh_fixture(str(original_parent))
            replacement_root = self.clone_refresh_fixture(str(replacement_parent))
            locked_root = temporary / "locked-repository"
            target = visible_root / LINEAGE_ROOT
            transaction = (
                visible_root / "release" / ".source-lineage-transaction-canary"
            )
            previous = transaction / "previous"
            transaction.mkdir(mode=0o700)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                self.refresher._transaction_marker(transaction),
            )
            shutil.copytree(target, previous)

            def snapshot(root: Path) -> dict[str, object]:
                metadata = root.lstat()
                return {
                    "identity": (metadata.st_dev, metadata.st_ino),
                    "tree": self.module.tree_identity(root),
                }

            previous_before = snapshot(previous)
            canary = target / "private-canary-note.txt"
            canary.write_text("preserve me\n", encoding="utf-8")
            replacement_before = snapshot(replacement_root)
            real_rename = self.refresher._rename_at
            real_rename_noreplace = self.refresher._rename_noreplace_at
            swapped = False
            generation_moves = []

            def rename_then_substitute_root(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage recovery state is ambiguous",
            ):
                nonlocal swapped
                if source_name == LINEAGE_ROOT.name and destination_name == "failed":
                    generation_moves.append((source_name, destination_name))
                result = real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )
                if (
                    source_name == LINEAGE_ROOT.name
                    and destination_name == "failed"
                    and not swapped
                ):
                    visible_root.rename(locked_root)
                    replacement_root.rename(visible_root)
                    swapped = True
                return result

            def record_noreplace_restore(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage recovery state is ambiguous",
            ):
                def mark_applied():
                    generation_moves.append((source_name, destination_name))
                    if applied is not None:
                        applied()

                return real_rename_noreplace(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=mark_applied,
                    diagnostic=diagnostic,
                )

            with (
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=rename_then_substitute_root,
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_noreplace_at",
                    side_effect=record_noreplace_restore,
                ),
                mock.patch.object(self.refresher, "_retain_transaction") as retain,
                self.assertRaises(self.refresher.lineage.LineageError) as captured,
            ):
                self.refresher._recover_interrupted_write(visible_root)

            self.assertTrue(swapped)
            self.assertEqual(
                generation_moves,
                [
                    (LINEAGE_ROOT.name, "failed"),
                    ("previous", LINEAGE_ROOT.name),
                ],
            )
            retain.assert_not_called()
            self.assertEqual(snapshot(locked_root / LINEAGE_ROOT), previous_before)
            self.assertEqual(snapshot(visible_root), replacement_before)
            self.assertEqual(
                (
                    locked_root
                    / "release"
                    / transaction.name
                    / "failed/private-canary-note.txt"
                ).read_text(encoding="utf-8"),
                "preserve me\n",
            )
            self.assertEqual(
                str(captured.exception), "source-lineage artifact tree drift"
            )
            self.assertNotIn(str(temporary), str(captured.exception))

    def test_recovery_retains_a_rejected_target_with_unmanaged_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            transaction = clone / "release" / ".source-lineage-transaction-canary"
            previous = transaction / "previous"
            transaction.mkdir(mode=0o700)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                self.refresher._transaction_marker(transaction),
            )
            shutil.copytree(target, previous)
            canary = target / "private-canary-note.txt"
            canary.write_text("preserve me\n", encoding="utf-8")
            real_fsync = os.fsync
            release_identity = self.refresher._directory_identity(
                (clone / "release").stat()
            )
            transaction_identity = self.refresher._directory_identity(
                transaction.stat()
            )
            fsynced = []

            def record_fsync(descriptor):
                metadata = os.fstat(descriptor)
                if stat.S_ISDIR(metadata.st_mode):
                    identity = self.refresher._directory_identity(metadata)
                    if identity == transaction_identity:
                        fsynced.append("transaction")
                    elif identity == release_identity:
                        fsynced.append("release")
                real_fsync(descriptor)

            with (
                mock.patch.object(self.refresher.os, "fsync", side_effect=record_fsync),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "source-lineage rejected generation was retained",
                ) as captured,
            ):
                self.refresher._recover_interrupted_write(clone)

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertEqual(
                fsynced[:4],
                ["transaction", "release", "release", "transaction"],
            )
            retained = self.refresher._retention_root(clone) / transaction.name
            self.assertEqual(
                (retained / "failed/private-canary-note.txt").read_text(
                    encoding="utf-8"
                ),
                "preserve me\n",
            )
            self.module.validate_lineage(clone)

    def test_recovery_retains_a_resurrected_duplicate_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            transaction = clone / "release" / ".source-lineage-transaction-canary"
            transaction.mkdir(mode=0o700)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                self.refresher._transaction_marker(transaction),
            )
            retention_root = self.refresher._retention_root(clone)
            retained = retention_root / transaction.name
            shutil.copytree(transaction, retained)

            self.refresher._recover_interrupted_write(clone)

            replayed = list(
                retention_root.glob(f".source-lineage-replay-*/{transaction.name}")
            )
            self.assertTrue(retained.is_dir())
            self.assertEqual(len(replayed), 1)
            self.assertEqual(
                self.module.tree_identity(retained),
                self.module.tree_identity(replayed[0]),
            )
            self.assertFalse(transaction.exists())
            self.refresher.write(clone)
            self.module.validate_lineage(clone)

    def test_retention_collision_root_drift_prevents_replay_directory_creation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            original_parent = temporary / "original-parent"
            replacement_parent = temporary / "replacement-parent"
            original_parent.mkdir()
            replacement_parent.mkdir()
            visible_root = self.clone_refresh_fixture(str(original_parent))
            replacement_root = self.clone_refresh_fixture(str(replacement_parent))
            locked_root = temporary / "locked-repository"
            transaction = (
                visible_root / "release" / ".source-lineage-transaction-canary"
            )
            transaction.mkdir(mode=0o700)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                self.refresher._transaction_marker(transaction),
            )
            retention_root = self.refresher._retention_root(visible_root)
            shutil.copytree(transaction, retention_root / transaction.name)
            original_before = self.module.tree_identity(visible_root)
            replacement_before = self.module.tree_identity(replacement_root)
            real_replay = self.refresher._new_replay_directory
            real_mkdir = self.refresher.os.mkdir
            swapped = False
            replay_mkdir = []

            def substitute_root_before_replay(root, view):
                nonlocal swapped
                self.assertFalse(swapped)
                visible_root.rename(locked_root)
                replacement_root.rename(visible_root)
                swapped = True
                return real_replay(root, view)

            def record_replay_mkdir(path, mode=0o777, *, dir_fd=None):
                if os.fspath(path).startswith(".source-lineage-replay-"):
                    replay_mkdir.append((path, dir_fd))
                return real_mkdir(path, mode, dir_fd=dir_fd)

            with self.refresher.lineage._lineage_lock(
                visible_root, exclusive=True, nonblocking=True
            ) as view:
                handle = self.refresher._open_owned_transaction_at(
                    view.release_descriptor, transaction.name
                )
                try:
                    with (
                        mock.patch.object(
                            self.refresher,
                            "_new_replay_directory",
                            side_effect=substitute_root_before_replay,
                        ),
                        mock.patch.object(
                            self.refresher.os,
                            "mkdir",
                            side_effect=record_replay_mkdir,
                        ),
                        self.assertRaises(
                            self.refresher.lineage.LineageError
                        ) as captured,
                    ):
                        self.refresher._retain_transaction(view.root, view, handle)
                finally:
                    self.refresher._close_directory(
                        handle.directory,
                        "source-lineage recovery state is ambiguous",
                    )

            self.assertTrue(swapped)
            self.assertEqual(replay_mkdir, [])
            self.assertEqual(
                list(
                    (locked_root / ".git/source-lineage-recovery").glob(
                        ".source-lineage-replay-*"
                    )
                ),
                [],
            )
            self.assertEqual(
                list(
                    (visible_root / ".git/source-lineage-recovery").glob(
                        ".source-lineage-replay-*"
                    )
                ),
                [],
            )
            self.assertEqual(self.module.tree_identity(locked_root), original_before)
            self.assertEqual(
                self.module.tree_identity(visible_root), replacement_before
            )
            self.assertEqual(
                str(captured.exception), "source-lineage artifact tree drift"
            )
            self.assertNotIn(str(temporary), str(captured.exception))

    def test_recovery_does_not_overwrite_an_invalid_public_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            transaction = clone / "release" / ".source-lineage-transaction-canary"
            previous = transaction / "previous"
            transaction.mkdir(mode=0o700)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                self.refresher._transaction_marker(transaction),
            )
            shutil.copytree(target, previous)
            shutil.rmtree(target)
            target.write_text("private-canary-target\n", encoding="utf-8")

            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "source-lineage recovery state is ambiguous",
            ) as captured:
                self.refresher._recover_interrupted_write(clone)

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertEqual(
                target.read_text(encoding="utf-8"), "private-canary-target\n"
            )
            self.assertTrue(previous.is_dir())

    def test_live_rollback_fsyncs_both_parents_before_retention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            real_fsync = os.fsync
            release_identity = self.refresher._directory_identity(
                (clone / "release").stat()
            )
            fsynced = []
            failure_injected = False

            def fail_final_validation(repository, view, parent_descriptor, name):
                nonlocal failure_injected
                failure_injected = True
                raise self.refresher.lineage.LineageError(
                    "private-canary-final-validation"
                )

            def record_fsync(descriptor):
                metadata = os.fstat(descriptor)
                if failure_injected and stat.S_ISDIR(metadata.st_mode):
                    identity = self.refresher._directory_identity(metadata)
                    if identity == release_identity:
                        fsynced.append("release")
                    else:
                        try:
                            names = set(os.listdir(descriptor))
                        except OSError:
                            names = set()
                        if self.refresher.TRANSACTION_MARKER in names:
                            fsynced.append("transaction")
                real_fsync(descriptor)

            with (
                mock.patch.object(
                    self.refresher,
                    "_validate_generation_at",
                    side_effect=fail_final_validation,
                ),
                mock.patch.object(self.refresher.os, "fsync", side_effect=record_fsync),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "source-lineage artifact publication failed",
                ) as captured,
            ):
                self.refresher.write(clone)

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertEqual(
                fsynced[:4],
                ["transaction", "release", "release", "transaction"],
            )
            self.module.validate_lineage(clone)

    def test_first_rename_fsync_failure_restores_the_public_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            before = self.module.tree_identity(clone / LINEAGE_ROOT)
            real_fsync = os.fsync
            failed = False

            def fail_first_transaction_fsync(descriptor):
                nonlocal failed
                try:
                    metadata = os.fstat(descriptor)
                    names = (
                        set(os.listdir(descriptor))
                        if stat.S_ISDIR(metadata.st_mode)
                        else set()
                    )
                except OSError:
                    names = set()
                if (
                    not failed
                    and {
                        self.refresher.TRANSACTION_MARKER,
                        "previous",
                    }
                    <= names
                    and not (clone / LINEAGE_ROOT).exists()
                ):
                    failed = True
                    raise OSError("private-canary-first-parent-fsync")
                real_fsync(descriptor)

            with (
                mock.patch.object(
                    self.refresher.os,
                    "fsync",
                    side_effect=fail_first_transaction_fsync,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "source-lineage artifact publication failed",
                ) as captured,
            ):
                self.refresher.write(clone)

            self.assertTrue(failed)
            self.assertNotIn("private-canary", str(captured.exception))
            self.assertEqual(before, self.module.tree_identity(clone / LINEAGE_ROOT))
            self.module.validate_lineage(clone)
            self.refresher.write(clone)
            self.module.validate_lineage(clone)

    def test_write_rolls_back_the_complete_set_after_publication_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            artifact_paths = (
                SOURCE_MANIFEST,
                CONTRIBUTION_LEDGER,
                *HOST_MANIFESTS,
            )
            before = {
                relative: (clone / relative).read_bytes() for relative in artifact_paths
            }
            real_rename = self.refresher._rename_at
            release_identity = self.refresher._directory_identity(
                (clone / "release").stat()
            )
            publication_failed = False

            def fail_new_directory_once(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage artifact publication failed",
            ):
                nonlocal publication_failed
                if (
                    source_name == LINEAGE_ROOT.name
                    and destination_name == LINEAGE_ROOT.name
                    and self.refresher._directory_identity(os.fstat(destination_parent))
                    == release_identity
                    and not publication_failed
                ):
                    publication_failed = True
                    raise OSError("private-canary-publication-failure")
                return real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )

            with (
                mock.patch.object(
                    self.refresher, "_rename_at", side_effect=fail_new_directory_once
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "source-lineage artifact publication failed",
                ) as captured,
            ):
                self.refresher.write(clone)

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertTrue((clone / LINEAGE_ROOT).is_dir())
            self.assertEqual(
                before,
                {
                    relative: (clone / relative).read_bytes()
                    for relative in artifact_paths
                },
            )
            self.module.validate_lineage(clone)
            self.refresher.write(clone)
            self.module.validate_lineage(clone)
            self.assertEqual(
                list((clone / "release").glob(".source-lineage-transaction-*")), []
            )

    def test_write_fsyncs_the_staged_directory_tree_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            real_fsync = self.refresher._fsync_directory
            real_rename = self.refresher._rename_at
            retention_root = self.refresher._retention_root(clone)
            fsynced = []
            publication_checked = False

            def record_fsync(path):
                fsynced.append(Path(path))
                real_fsync(path)

            def check_before_publication(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage artifact publication failed",
            ):
                nonlocal publication_checked
                if source_name.startswith(self.refresher.PREPARATION_PREFIX):
                    publication_checked = True
                    preparation = retention_root / source_name
                    staged_root = preparation / "staged"
                    expected_staged_directories = {staged_root}
                    expected_staged_directories.update(
                        path for path in staged_root.rglob("*") if path.is_dir()
                    )
                    self.assertLessEqual(expected_staged_directories, set(fsynced))
                    self.assertEqual(fsynced[-1], preparation)
                return real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )

            with (
                mock.patch.object(
                    self.refresher, "_fsync_directory", side_effect=record_fsync
                ),
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=check_before_publication,
                ),
            ):
                self.refresher.write(clone)

            self.assertTrue(publication_checked)
            self.module.validate_lineage(clone)

    def test_write_recovers_after_interruption_between_directory_renames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            artifact_paths = (
                SOURCE_MANIFEST,
                CONTRIBUTION_LEDGER,
                *HOST_MANIFESTS,
            )
            before = {
                relative: (clone / relative).read_bytes() for relative in artifact_paths
            }
            real_rename = self.refresher._rename_at
            interrupted = False

            def interrupt_new_directory_once(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage artifact publication failed",
            ):
                nonlocal interrupted
                if (
                    source_name == LINEAGE_ROOT.name
                    and destination_name == LINEAGE_ROOT.name
                    and not interrupted
                ):
                    interrupted = True
                    raise KeyboardInterrupt
                return real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )

            with (
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=interrupt_new_directory_once,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                self.refresher.write(clone)

            self.assertFalse((clone / LINEAGE_ROOT).exists())
            self.assertEqual(
                len(list((clone / "release").glob(".source-lineage-transaction-*"))),
                1,
            )
            self.refresher.write(clone)
            self.assertEqual(
                before,
                {
                    relative: (clone / relative).read_bytes()
                    for relative in artifact_paths
                },
            )
            self.module.validate_lineage(clone)
            self.assertEqual(
                list((clone / "release").glob(".source-lineage-transaction-*")), []
            )

    def test_write_recovers_after_interruption_following_staged_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            real_rename = self.refresher._rename_at
            interrupted = False

            def interrupt_after_new_directory(
                source_parent,
                source_name,
                destination_parent,
                destination_name,
                *,
                applied=None,
                diagnostic="source-lineage artifact publication failed",
            ):
                nonlocal interrupted
                result = real_rename(
                    source_parent,
                    source_name,
                    destination_parent,
                    destination_name,
                    applied=applied,
                    diagnostic=diagnostic,
                )
                if (
                    source_name == LINEAGE_ROOT.name
                    and destination_name == LINEAGE_ROOT.name
                    and not interrupted
                ):
                    interrupted = True
                    raise KeyboardInterrupt
                return result

            with (
                mock.patch.object(
                    self.refresher,
                    "_rename_at",
                    side_effect=interrupt_after_new_directory,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                self.refresher.write(clone)

            self.assertTrue((clone / LINEAGE_ROOT).is_dir())
            self.assertEqual(
                len(list((clone / "release").glob(".source-lineage-transaction-*"))),
                1,
            )
            self.refresher.write(clone)
            self.module.validate_lineage(clone)
            self.assertEqual(
                list((clone / "release").glob(".source-lineage-transaction-*")), []
            )

    def test_write_recovers_a_durable_destination_with_a_resurrected_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            target = clone / LINEAGE_ROOT
            transaction = clone / "release" / ".source-lineage-transaction-canary"
            previous = transaction / "previous"
            staged = transaction / "staged" / LINEAGE_ROOT
            transaction.mkdir(mode=0o700)
            self.refresher._atomic_write(
                transaction / self.refresher.TRANSACTION_MARKER,
                self.refresher._transaction_marker(transaction),
            )
            shutil.copytree(target, previous)
            shutil.copytree(target, staged)

            self.refresher._recover_interrupted_write(clone)

            self.assertTrue(target.is_dir())
            self.assertFalse(transaction.exists())
            self.module.validate_lineage(clone)

    def test_readers_wait_for_a_complete_generation_during_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            clone = self.clone_refresh_fixture(temporary_directory)
            marker = temporary / "publication-gap"
            release = temporary / "release-writer"
            helper = temporary / "pause_writer.py"
            helper.write_text(
                """\
import importlib.util
import os
import sys
import time
from pathlib import Path

repository = Path(sys.argv[1])
marker = Path(sys.argv[2])
release = Path(sys.argv[3])
path = repository / "scripts/refresh_source_skill_lineage.py"
specification = importlib.util.spec_from_file_location("lineage_writer", path)
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
real_rename = module._rename_at
paused = False

def rename(
    source_parent,
    source_name,
    destination_parent,
    destination_name,
    *,
    applied=None,
    diagnostic="source-lineage artifact publication failed",
):
    global paused
    if (
        source_name == module.lineage.LINEAGE_ROOT.name
        and destination_name == module.lineage.LINEAGE_ROOT.name
        and not paused
    ):
        paused = True
        marker.write_text("ready", encoding="utf-8")
        while not release.exists():
            time.sleep(0.01)
    return real_rename(
        source_parent,
        source_name,
        destination_parent,
        destination_name,
        applied=applied,
        diagnostic=diagnostic,
    )

module._rename_at = rename
module.write(repository)
""",
                encoding="utf-8",
            )
            environment = {"PYTHONDONTWRITEBYTECODE": "1"}
            writer = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(helper),
                    str(clone),
                    str(marker),
                    str(release),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            deadline = time.monotonic() + 30
            while not marker.exists() and writer.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("lineage writer did not reach the publication boundary")
                time.sleep(0.01)
            if writer.poll() is not None:
                stdout, stderr = writer.communicate()
                self.fail(f"lineage writer stopped early: {stdout}{stderr}")

            readers = [
                subprocess.Popen(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                )
                for command in (
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        str(clone / "scripts/validate_source_skill_lineage.py"),
                        str(clone),
                    ],
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        str(clone / "scripts/refresh_source_skill_lineage.py"),
                        "check",
                        str(clone),
                    ],
                )
            ]
            try:
                time.sleep(0.2)
                self.assertTrue(all(reader.poll() is None for reader in readers))
            finally:
                release.touch()

            writer_stdout, writer_stderr = writer.communicate(timeout=30)
            self.assertEqual(writer.returncode, 0, writer_stdout + writer_stderr)
            for reader in readers:
                stdout, stderr = reader.communicate(timeout=30)
                self.assertEqual(reader.returncode, 0, stdout + stderr)
            self.module.validate_lineage(clone)

    def test_external_receipt_is_create_new_and_binds_committed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: bind lineage artifacts",
                ],
                cwd=clone,
                check=True,
            )
            output = self.stable_receipt_output("bound-artifacts")
            command = [
                sys.executable,
                str(clone / "scripts/refresh_source_skill_lineage.py"),
                "receipt",
                str(clone),
                "--output",
                str(output),
                "--captured-at-utc",
                "2026-08-18T00:00:00Z",
            ]
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                set(receipt),
                {
                    "artifacts",
                    "candidate",
                    "captured_at_utc",
                    "content_sha256",
                    "contract",
                    "release_eligibility",
                    "schema_version",
                    "unresolved",
                    "validator",
                },
            )
            self.assertEqual(receipt["release_eligibility"], "not-asserted")
            self.assertEqual(
                receipt["unresolved"]["contribution_ids"],
                expected_unresolved_contribution_ids(clone),
            )
            self.assertEqual(
                receipt["unresolved"]["host_observation_ids"],
                expected_unresolved_host_observation_ids(clone),
            )
            self.assertEqual(
                receipt["unresolved"]["source_ids"],
                list(EXPECTED_UNRESOLVED_SOURCE_IDS),
            )
            self.assertNotIn(str(clone), output.read_text(encoding="utf-8"))

            second = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already exists", second.stderr)

    def test_receipt_hardens_create_new_mode_before_content_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.committed_receipt_fixture(temporary_directory)
            real_fchmod = self.refresher.os.fchmod
            real_fdopen = self.refresher.os.fdopen
            events = []

            def record_fchmod(descriptor, mode):
                events.append(("fchmod", descriptor, mode))
                return real_fchmod(descriptor, mode)

            def record_fdopen(descriptor, *arguments, **keywords):
                events.append(("fdopen", descriptor, None))
                return real_fdopen(descriptor, *arguments, **keywords)

            hardened_output = self.stable_receipt_output("restrictive-umask")
            previous_umask = os.umask(0o777)
            try:
                with (
                    mock.patch.object(
                        self.refresher.os, "fchmod", side_effect=record_fchmod
                    ),
                    mock.patch.object(
                        self.refresher.os, "fdopen", side_effect=record_fdopen
                    ),
                ):
                    self.refresher.receipt(
                        clone, hardened_output, "2026-08-18T00:00:00Z"
                    )
            finally:
                os.umask(previous_umask)

            self.assertEqual(stat.S_IMODE(hardened_output.stat().st_mode), 0o600)
            self.assertEqual(events[0][0], "fchmod")
            self.assertEqual(events[0][2], 0o600)
            self.assertEqual(events[1], ("fdopen", events[0][1], None))

    def test_receipt_mode_hardening_failures_retain_private_empty_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.committed_receipt_fixture(temporary_directory)
            real_fchmod = self.refresher.os.fchmod
            failed_output = self.stable_receipt_output("fchmod-failure")
            with (
                mock.patch.object(
                    self.refresher.os,
                    "fchmod",
                    side_effect=OSError("private-canary-fchmod"),
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    r"\Areconciliation receipt publication failed\Z",
                ) as failed,
            ):
                self.refresher.receipt(clone, failed_output, "2026-08-18T00:00:00Z")

            self.assertEqual(
                str(failed.exception), "reconciliation receipt publication failed"
            )
            self.assertTrue(failed_output.is_file())
            self.assertEqual(failed_output.read_bytes(), b"")
            self.assertEqual(stat.S_IMODE(failed_output.stat().st_mode) & 0o077, 0)

            mismatched_output = self.stable_receipt_output("fstat-mode-mismatch")
            real_fstat = self.refresher.os.fstat
            hardened_descriptors = set()

            def harden_then_record(descriptor, mode):
                result = real_fchmod(descriptor, mode)
                hardened_descriptors.add(descriptor)
                return result

            def report_widened_mode(descriptor):
                observed = real_fstat(descriptor)
                if descriptor not in hardened_descriptors:
                    return observed
                values = list(observed)
                values[0] = (values[0] & ~0o777) | 0o644
                return os.stat_result(values)

            with (
                mock.patch.object(
                    self.refresher.os, "fchmod", side_effect=harden_then_record
                ),
                mock.patch.object(
                    self.refresher.os, "fstat", side_effect=report_widened_mode
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    r"\Areconciliation receipt publication failed\Z",
                ) as mismatched,
            ):
                self.refresher.receipt(clone, mismatched_output, "2026-08-18T00:00:00Z")

            self.assertEqual(
                str(mismatched.exception),
                "reconciliation receipt publication failed",
            )
            self.assertTrue(mismatched_output.is_file())
            self.assertEqual(mismatched_output.read_bytes(), b"")
            self.assertEqual(stat.S_IMODE(mismatched_output.stat().st_mode), 0o600)

    def test_receipt_final_binding_rejects_mode_drift_after_creation(self) -> None:
        diagnostic = "reconciliation receipt publication failed"
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.committed_receipt_fixture(temporary_directory)
            public_before = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }
            real_fsync = self.refresher.os.fsync
            canary = "private-receipt-mode-drift-canary"

            for selected_phase in ("file", "parent"):
                with self.subTest(selected_phase=selected_phase):
                    output = self.stable_receipt_output(f"mode-drift-{selected_phase}")
                    widened = []

                    def widen_during_sync(
                        descriptor,
                        *,
                        phase=selected_phase,
                        output=output,
                        widened=widened,
                    ):
                        metadata = os.fstat(descriptor)
                        observed_phase = (
                            "file" if stat.S_ISREG(metadata.st_mode) else "parent"
                        )
                        if observed_phase == phase and not widened:
                            before = output.lstat()
                            os.chmod(output, 0o644)
                            after = output.lstat()
                            widened.append(
                                {
                                    "after_identity": (after.st_dev, after.st_ino),
                                    "after_mode": stat.S_IMODE(after.st_mode),
                                    "before_identity": (before.st_dev, before.st_ino),
                                    "before_mode": stat.S_IMODE(before.st_mode),
                                }
                            )
                        return real_fsync(descriptor)

                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    failure = None
                    with (
                        mock.patch.object(
                            self.refresher.os,
                            "fsync",
                            side_effect=widen_during_sync,
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        try:
                            self.refresher.receipt(
                                clone,
                                output,
                                "2026-08-18T00:00:00Z",
                            )
                        except self.refresher.lineage.LineageError as error:
                            failure = error

                    self.assertEqual(len(widened), 1)
                    self.assertEqual(
                        widened,
                        [
                            {
                                "after_identity": widened[0]["before_identity"],
                                "after_mode": 0o644,
                                "before_identity": widened[0]["before_identity"],
                                "before_mode": 0o600,
                            }
                        ],
                    )
                    self.assertTrue(output.is_file())
                    visible = output.lstat()
                    self.assertEqual(
                        (visible.st_dev, visible.st_ino),
                        widened[0]["before_identity"],
                    )
                    self.assertEqual(stat.S_IMODE(visible.st_mode), 0o644)
                    raw = output.read_bytes()
                    value = json.loads(raw)
                    self.assertEqual(raw, self.module.content_document(value))
                    self.assertEqual(
                        value["content_sha256"], self.module.content_sha256(value)
                    )
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(stderr.getvalue(), "")
                    self.assertIsNotNone(failure)
                    self.assertEqual(str(failure), diagnostic)
                    self.assertIsNone(failure.__cause__)
                    self.assertEqual(
                        {
                            relative: (clone / relative).read_bytes()
                            for relative in public_artifacts
                        },
                        public_before,
                    )
                    rendered = stdout.getvalue() + stderr.getvalue() + str(failure)
                    for private_value in (
                        str(output),
                        str(clone),
                        canary,
                        "Traceback",
                    ):
                        self.assertNotIn(private_value, rendered)

    def test_receipt_final_binding_rejects_same_inode_content_drift(self) -> None:
        diagnostic = "reconciliation receipt publication failed"
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        cases = (
            (
                "file",
                os.O_WRONLY | os.O_TRUNC,
                b"private-receipt-truncate-canary\n",
            ),
            (
                "parent",
                os.O_WRONLY | os.O_APPEND,
                b"\nprivate-receipt-append-canary\n",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.committed_receipt_fixture(temporary_directory)
            public_before = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }
            real_close = self.refresher.os.close
            real_fsync = self.refresher.os.fsync
            real_open = self.refresher.os.open
            real_path_read_bytes = Path.read_bytes
            real_pread = self.refresher.os.pread
            real_read = self.refresher.os.read
            real_write = self.refresher.os.write

            for selected_phase, writer_flags, canary in cases:
                with self.subTest(selected_phase=selected_phase):
                    output = self.stable_receipt_output(
                        f"content-drift-{selected_phase}"
                    )
                    descriptor_reads = []
                    mutations = []
                    publication_opens = []

                    def record_open(
                        candidate,
                        flags,
                        mode=0o777,
                        *,
                        dir_fd=None,
                        expected_output=output,
                        opened=publication_opens,
                    ):
                        descriptor = real_open(
                            candidate,
                            flags,
                            mode,
                            dir_fd=dir_fd,
                        )
                        if (
                            dir_fd is not None
                            and os.fspath(candidate) == expected_output.name
                            and flags & os.O_CREAT
                        ):
                            opened.append((descriptor, flags, dir_fd))
                        return descriptor

                    def record_pread(
                        descriptor, length, offset, reads=descriptor_reads
                    ):
                        reads.append(descriptor)
                        return real_pread(descriptor, length, offset)

                    def record_read(descriptor, length, reads=descriptor_reads):
                        reads.append(descriptor)
                        return real_read(descriptor, length)

                    def reject_path_only_receipt_read(
                        candidate, expected_output=output
                    ):
                        if candidate == expected_output:
                            raise AssertionError(
                                "receipt verification used a path-only read"
                            )
                        return real_path_read_bytes(candidate)

                    def mutate_after_sync(
                        descriptor,
                        *,
                        phase=selected_phase,
                        output_path=output,
                        flags=writer_flags,
                        payload=canary,
                        observed_mutations=mutations,
                    ):
                        result = real_fsync(descriptor)
                        metadata = os.fstat(descriptor)
                        observed_phase = (
                            "file" if stat.S_ISREG(metadata.st_mode) else "parent"
                        )
                        if observed_phase != phase or observed_mutations:
                            return result
                        before = output_path.lstat()
                        writer = real_open(
                            output_path,
                            flags | getattr(os, "O_NOFOLLOW", 0),
                        )
                        try:
                            self.assertEqual(real_write(writer, payload), len(payload))
                            real_fsync(writer)
                        finally:
                            real_close(writer)
                        after = output_path.lstat()
                        observed_mutations.append(
                            {
                                "after_identity": (after.st_dev, after.st_ino),
                                "after_mode": stat.S_IMODE(after.st_mode),
                                "before_identity": (before.st_dev, before.st_ino),
                                "before_mode": stat.S_IMODE(before.st_mode),
                            }
                        )
                        return result

                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    failure = None
                    with (
                        mock.patch.object(
                            self.refresher.os,
                            "open",
                            side_effect=record_open,
                        ),
                        mock.patch.object(
                            self.refresher.os,
                            "pread",
                            side_effect=record_pread,
                        ),
                        mock.patch.object(
                            self.refresher.os,
                            "read",
                            side_effect=record_read,
                        ),
                        mock.patch.object(
                            self.refresher.os,
                            "fsync",
                            side_effect=mutate_after_sync,
                        ),
                        mock.patch.object(
                            Path,
                            "read_bytes",
                            side_effect=reject_path_only_receipt_read,
                            autospec=True,
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        try:
                            self.refresher.receipt(
                                clone,
                                output,
                                "2026-08-18T00:00:00Z",
                            )
                        except self.refresher.lineage.LineageError as error:
                            failure = error

                    self.assertEqual(len(mutations), 1)
                    self.assertEqual(
                        mutations,
                        [
                            {
                                "after_identity": mutations[0]["before_identity"],
                                "after_mode": 0o600,
                                "before_identity": mutations[0]["before_identity"],
                                "before_mode": 0o600,
                            }
                        ],
                    )
                    self.assertTrue(output.is_file())
                    visible = output.lstat()
                    self.assertEqual(
                        (visible.st_dev, visible.st_ino),
                        mutations[0]["before_identity"],
                    )
                    self.assertEqual(stat.S_IMODE(visible.st_mode), 0o600)
                    if writer_flags & os.O_TRUNC:
                        self.assertEqual(output.read_bytes(), canary)
                    else:
                        self.assertTrue(output.read_bytes().endswith(canary))
                    self.assertIsNotNone(
                        failure,
                        "receipt publication accepted same-inode content drift",
                    )
                    self.assertEqual(str(failure), diagnostic)
                    self.assertIsNone(failure.__cause__)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertEqual(stderr.getvalue(), "")
                    self.assertEqual(len(publication_opens), 1)
                    publication_descriptor, flags, _parent_descriptor = (
                        publication_opens[0]
                    )
                    self.assertEqual(flags & os.O_ACCMODE, os.O_RDWR)
                    self.assertIn(publication_descriptor, descriptor_reads)
                    with self.assertRaises(OSError):
                        os.fstat(publication_descriptor)
                    self.assertEqual(
                        {
                            relative: (clone / relative).read_bytes()
                            for relative in public_artifacts
                        },
                        public_before,
                    )
                    rendered = stdout.getvalue() + stderr.getvalue() + str(failure)
                    for private_value in (
                        str(output),
                        str(clone),
                        canary.decode().strip(),
                        "Traceback",
                    ):
                        self.assertNotIn(private_value, rendered)

    def test_receipt_parent_close_preserves_publication_failure_precedence(
        self,
    ) -> None:
        diagnostic = "reconciliation receipt publication failed"
        public_artifacts = (
            RESEARCH_REPORT,
            SOURCE_MANIFEST,
            CONTRIBUTION_LEDGER,
            *HOST_MANIFESTS,
        )
        cases = (
            ("publication-failure", True),
            ("published-then-close-failure", False),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.committed_receipt_fixture(temporary_directory)
            public_before = {
                relative: (clone / relative).read_bytes()
                for relative in public_artifacts
            }
            real_close = self.refresher.os.close
            real_external_parent = self.refresher._external_receipt_parent
            real_fsync = self.refresher.os.fsync
            real_open = self.refresher.os.open
            parent_close_canary = "private-receipt-parent-close-canary"
            publication_canary = "private-receipt-publication-canary"

            for case_name, fail_parent_fsync in cases:
                for entrypoint in ("api", "cli"):
                    with self.subTest(case=case_name, entrypoint=entrypoint):
                        output = self.stable_receipt_output(
                            f"parent-close-{case_name}-{entrypoint}"
                        )
                        close_calls = []
                        leaf_at_close = []
                        parent_close_failures = []
                        parent_descriptors = []
                        publication_descriptors = []
                        publication_failures = []

                        def record_external_parent(
                            repository,
                            candidate,
                            *,
                            parents=parent_descriptors,
                        ):
                            result = real_external_parent(repository, candidate)
                            parents.append(result[0])
                            return result

                        def record_open(
                            candidate,
                            flags,
                            mode=0o777,
                            *,
                            dir_fd=None,
                            parents=parent_descriptors,
                            published=publication_descriptors,
                            output_path=output,
                        ):
                            descriptor = real_open(
                                candidate,
                                flags,
                                mode,
                                dir_fd=dir_fd,
                            )
                            if (
                                parents
                                and dir_fd == parents[0]
                                and os.fspath(candidate) == output_path.name
                                and flags & os.O_CREAT
                            ):
                                published.append(descriptor)
                            return descriptor

                        def fail_precise_publication(
                            descriptor,
                            *,
                            failures=publication_failures,
                            parents=parent_descriptors,
                            selected=fail_parent_fsync,
                        ):
                            result = real_fsync(descriptor)
                            if selected and parents and descriptor == parents[0]:
                                failures.append(descriptor)
                                raise OSError(publication_canary)
                            return result

                        def fail_parent_close(
                            descriptor,
                            *,
                            calls=close_calls,
                            failures=parent_close_failures,
                            parents=parent_descriptors,
                            retained=leaf_at_close,
                            output_path=output,
                        ):
                            calls.append(descriptor)
                            if parents and descriptor == parents[0]:
                                metadata = output_path.lstat()
                                retained.append(
                                    {
                                        "identity": (metadata.st_dev, metadata.st_ino),
                                        "mode": stat.S_IMODE(metadata.st_mode),
                                        "raw": output_path.read_bytes(),
                                    }
                                )
                                real_close(descriptor)
                                failures.append(descriptor)
                                raise OSError(parent_close_canary)
                            return real_close(descriptor)

                        stdout = io.StringIO()
                        stderr = io.StringIO()
                        failure = None
                        with (
                            mock.patch.object(
                                self.refresher,
                                "_external_receipt_parent",
                                side_effect=record_external_parent,
                            ),
                            mock.patch.object(
                                self.refresher.os,
                                "open",
                                side_effect=record_open,
                            ),
                            mock.patch.object(
                                self.refresher.os,
                                "fsync",
                                side_effect=fail_precise_publication,
                            ),
                            mock.patch.object(
                                self.refresher.os,
                                "close",
                                side_effect=fail_parent_close,
                            ),
                            contextlib.redirect_stdout(stdout),
                            contextlib.redirect_stderr(stderr),
                        ):
                            if entrypoint == "api":
                                try:
                                    self.refresher.receipt(
                                        clone,
                                        output,
                                        "2026-08-18T00:00:00Z",
                                    )
                                except self.refresher.lineage.LineageError as error:
                                    failure = error
                            else:
                                with mock.patch.object(
                                    self.refresher.sys,
                                    "argv",
                                    [
                                        str(REFRESHER),
                                        "receipt",
                                        str(clone),
                                        "--output",
                                        str(output),
                                        "--captured-at-utc",
                                        "2026-08-18T00:00:00Z",
                                    ],
                                ):
                                    self.assertEqual(self.refresher.main(), 1)

                        self.assertEqual(len(parent_descriptors), 1)
                        parent_descriptor = parent_descriptors[0]
                        self.assertEqual(parent_close_failures, [parent_descriptor])
                        self.assertEqual(len(leaf_at_close), 1)
                        self.assertEqual(
                            publication_failures,
                            [parent_descriptor] if fail_parent_fsync else [],
                        )
                        self.assertTrue(output.is_file())
                        visible = output.lstat()
                        self.assertEqual(stat.S_IMODE(visible.st_mode), 0o600)
                        self.assertEqual(
                            (visible.st_dev, visible.st_ino),
                            leaf_at_close[0]["identity"],
                        )
                        raw = output.read_bytes()
                        self.assertEqual(raw, leaf_at_close[0]["raw"])
                        value = json.loads(raw)
                        self.assertEqual(raw, self.module.content_document(value))
                        self.assertEqual(
                            value["content_sha256"],
                            self.module.content_sha256(value),
                        )
                        self.assertEqual(len(publication_descriptors), 1)
                        publication_descriptor = publication_descriptors[0]
                        self.assertIn(publication_descriptor, close_calls)
                        with self.assertRaises(OSError):
                            os.fstat(publication_descriptor)
                        with self.assertRaises(OSError):
                            os.fstat(parent_descriptor)

                        if entrypoint == "api":
                            self.assertIsNotNone(failure)
                            self.assertEqual(str(failure), diagnostic)
                            self.assertIsNone(failure.__cause__)
                            self.assertEqual(stderr.getvalue(), "")
                        else:
                            self.assertEqual(
                                stderr.getvalue(),
                                f"source-skill-lineage-refresh: {diagnostic}\n",
                            )
                        self.assertEqual(stdout.getvalue(), "")
                        rendered = (
                            stdout.getvalue()
                            + stderr.getvalue()
                            + ("" if failure is None else str(failure))
                        )
                        for private_value in (
                            str(output),
                            str(clone),
                            parent_close_canary,
                            publication_canary,
                            "source-skill-lineage-receipt-created",
                            "Traceback",
                        ):
                            self.assertNotIn(private_value, rendered)

                        retry_stdout = io.StringIO()
                        retry_stderr = io.StringIO()
                        with (
                            contextlib.redirect_stdout(retry_stdout),
                            contextlib.redirect_stderr(retry_stderr),
                            self.assertRaises(
                                self.refresher.lineage.LineageError
                            ) as retry,
                        ):
                            self.refresher.receipt(
                                clone,
                                output,
                                "2026-08-18T00:00:00Z",
                            )
                        self.assertEqual(
                            str(retry.exception),
                            "reconciliation receipt output already exists",
                        )
                        self.assertIsNone(retry.exception.__cause__)
                        self.assertEqual(retry_stdout.getvalue(), "")
                        self.assertEqual(retry_stderr.getvalue(), "")
                        self.assertEqual(output.read_bytes(), raw)
                        self.assertEqual(
                            {
                                relative: (clone / relative).read_bytes()
                                for relative in public_artifacts
                            },
                            public_before,
                        )

    def test_receipt_ignores_inherited_tmpdir_inside_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.committed_receipt_fixture(temporary_directory)
            common_git_directory = Path(
                self.refresher._git(
                    clone,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                )
            ).resolve(strict=True)
            hostile_tmp = common_git_directory / "hostile-tmp"
            hostile_tmp.mkdir()
            audit_log = Path(temporary_directory) / "tempfile-audit.jsonl"
            canary_root = Path(temporary_directory) / "audit-canary"
            canary_root.mkdir()
            output = self.stable_receipt_output("hostile-tmpdir")
            wrapper = (
                "import json, os, runpy, sys, tempfile\n"
                "script, repository, output, audit_log, canary_root, expected_tmp = "
                "sys.argv[1:]\n"
                "if os.environ.get('TMPDIR') != expected_tmp:\n"
                "    raise RuntimeError('inherited TMPDIR drift')\n"
                "def audit(event, arguments):\n"
                "    candidate = None\n"
                "    if event in {'tempfile.mkstemp', 'tempfile.mkdtemp', "
                "'os.mkdir'}:\n"
                "        candidate = arguments[0]\n"
                "    elif event == 'open' and isinstance(arguments[2], int) and "
                "arguments[2] & os.O_CREAT:\n"
                "        candidate = arguments[0]\n"
                "    if candidate is None:\n"
                "        return\n"
                "    try:\n"
                "        path = os.path.abspath(os.fsdecode(candidate))\n"
                "    except TypeError:\n"
                "        return\n"
                "    if path == audit_log:\n"
                "        return\n"
                "    with open(audit_log, 'a', encoding='utf-8') as stream:\n"
                "        stream.write(json.dumps([event, path]) + '\\n')\n"
                "sys.addaudithook(audit)\n"
                "canary = tempfile.mkdtemp(prefix='audit-canary-', dir=canary_root)\n"
                "os.rmdir(canary)\n"
                "sys.argv = [script, 'receipt', repository, '--output', output, "
                "'--captured-at-utc', '2026-08-18T00:00:00Z']\n"
                "runpy.run_path(script, run_name='__main__')\n"
            )
            environment = dict(os.environ)
            environment.update(
                {
                    "TEMP": str(hostile_tmp),
                    "TMP": str(hostile_tmp),
                    "TMPDIR": str(hostile_tmp),
                }
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    wrapper,
                    str(clone / "scripts/refresh_source_skill_lineage.py"),
                    str(clone),
                    str(output),
                    str(audit_log),
                    str(canary_root),
                    str(hostile_tmp),
                ],
                cwd=clone,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "source-skill-lineage-receipt-created\n")
            self.assertEqual(completed.stderr, "")
            self.assertTrue(output.is_file())
            records = [
                json.loads(line)
                for line in audit_log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(
                any(
                    event == "tempfile.mkdtemp"
                    and Path(path).parent == canary_root
                    and Path(path).name.startswith("audit-canary-")
                    for event, path in records
                )
            )
            hostile_creations = [
                (event, path)
                for event, path in records
                if Path(path) == hostile_tmp or hostile_tmp in Path(path).parents
            ]
            self.assertEqual(list(hostile_tmp.iterdir()), [])
            self.assertEqual(
                len(hostile_creations),
                0,
                f"inherited TMPDIR was used for {len(hostile_creations)} creations",
            )

    def test_receipt_materializer_rejects_case_colliding_git_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = temporary / "case-alias.git"
            subprocess.run(
                ["/usr/bin/git", "init", "--bare", "--quiet", str(repository)],
                check=True,
            )
            object_id = (
                subprocess.run(
                    [
                        "/usr/bin/git",
                        "-C",
                        str(repository),
                        "hash-object",
                        "-w",
                        "--stdin",
                    ],
                    input=b"validator bytes\n",
                    capture_output=True,
                    check=True,
                )
                .stdout.decode("ascii")
                .strip()
            )
            tree = (
                subprocess.run(
                    ["/usr/bin/git", "-C", str(repository), "mktree"],
                    input=(
                        f"100644 blob {object_id}\tValidator\n"
                        f"100644 blob {object_id}\tvalidator\n"
                    ).encode("ascii"),
                    capture_output=True,
                    check=True,
                )
                .stdout.decode("ascii")
                .strip()
            )
            upper_tree = (
                subprocess.run(
                    ["/usr/bin/git", "-C", str(repository), "mktree"],
                    input=f"100644 blob {object_id}\tupper\n".encode("ascii"),
                    capture_output=True,
                    check=True,
                )
                .stdout.decode("ascii")
                .strip()
            )
            lower_tree = (
                subprocess.run(
                    ["/usr/bin/git", "-C", str(repository), "mktree"],
                    input=f"100644 blob {object_id}\tlower\n".encode("ascii"),
                    capture_output=True,
                    check=True,
                )
                .stdout.decode("ascii")
                .strip()
            )
            directory_alias_tree = (
                subprocess.run(
                    ["/usr/bin/git", "-C", str(repository), "mktree"],
                    input=(
                        f"040000 tree {upper_tree}\tScripts\n"
                        f"040000 tree {lower_tree}\tscripts\n"
                    ).encode("ascii"),
                    capture_output=True,
                    check=True,
                )
                .stdout.decode("ascii")
                .strip()
            )
            for index, collision in enumerate((tree, directory_alias_tree)):
                with self.subTest(collision=collision):
                    destination = temporary / f"materialized-{index}"
                    with self.assertRaisesRegex(
                        self.refresher.lineage.LineageError, "filesystem alias"
                    ):
                        self.refresher._materialize_git_commit(
                            repository, collision, destination
                        )

                    self.assertFalse(destination.exists())

    def test_receipt_materializer_preflights_resource_limits_before_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(
                temporary, "materializer-limits.git"
            )
            first = self.write_git_blob(repository, b"aaaa")
            second = self.write_git_blob(repository, b"bbbbb")
            large = self.write_git_blob(repository, b"xxxxxxxxx")
            link = self.write_git_blob(repository, b"target")
            single_tree = self.write_git_tree(repository, [("100644", first, "single")])
            unique_tree = self.write_git_tree(
                repository,
                [("100644", first, "first"), ("100644", second, "second")],
            )
            repeated_tree = self.write_git_tree(
                repository,
                [("100644", first, "first"), ("100644", first, "second")],
            )
            large_tree = self.write_git_tree(repository, [("100644", large, "large")])
            link_tree = self.write_git_tree(repository, [("120000", link, "link")])
            listing_size = len(
                subprocess.run(
                    [
                        "/usr/bin/git",
                        "--no-replace-objects",
                        "-C",
                        str(repository),
                        "ls-tree",
                        "-r",
                        "-z",
                        single_tree,
                    ],
                    capture_output=True,
                    check=True,
                ).stdout
            )
            cases = (
                ("listing", single_tree, {"MAX_TREE_LISTING_BYTES": listing_size - 1}),
                ("entries", unique_tree, {"MAX_TREE_ENTRIES": 1}),
                ("blob", large_tree, {"MAX_BLOB_BYTES": 8}),
                (
                    "unique",
                    unique_tree,
                    {"MAX_BLOB_BYTES": 5, "MAX_UNIQUE_BLOB_BYTES": 8},
                ),
                (
                    "materialized",
                    repeated_tree,
                    {"MAX_UNIQUE_BLOB_BYTES": 4, "MAX_MATERIALIZED_BYTES": 7},
                ),
                ("symlink", link_tree, {"MAX_SYMLINK_BYTES": 5}),
            )
            for index, (name, tree, limits) in enumerate(cases):
                with self.subTest(name=name):
                    destination = temporary / f"rejected-{index}"
                    patches = [
                        mock.patch.object(self.refresher, key, value)
                        for key, value in limits.items()
                    ]
                    with (
                        contextlib.ExitStack() as stack,
                        mock.patch.object(
                            self.refresher,
                            "_bounded_git_output",
                            wraps=self.refresher._bounded_git_output,
                        ) as bounded,
                        self.assertRaisesRegex(
                            self.refresher.lineage.LineageError,
                            "^committed repository snapshot exceeds "
                            "materialization limits$",
                        ) as captured,
                    ):
                        for patch in patches:
                            stack.enter_context(patch)
                        self.refresher._materialize_git_commit(
                            repository, tree, destination
                        )

                    phases = [call.args[1] for call in bounded.call_args_list]
                    self.assertNotIn(("cat-file", "--batch"), phases)
                    self.assertFalse(destination.exists())
                    self.assertNotIn(str(temporary), str(captured.exception))

            exact_destination = temporary / "exact-limits"
            exact_listing_size = len(
                subprocess.run(
                    [
                        "/usr/bin/git",
                        "--no-replace-objects",
                        "-C",
                        str(repository),
                        "ls-tree",
                        "-r",
                        "-z",
                        unique_tree,
                    ],
                    capture_output=True,
                    check=True,
                ).stdout
            )
            with (
                mock.patch.object(
                    self.refresher, "MAX_TREE_LISTING_BYTES", exact_listing_size
                ),
                mock.patch.object(self.refresher, "MAX_TREE_ENTRIES", 2),
                mock.patch.object(self.refresher, "MAX_BLOB_BYTES", 5),
                mock.patch.object(self.refresher, "MAX_UNIQUE_BLOB_BYTES", 9),
                mock.patch.object(self.refresher, "MAX_MATERIALIZED_BYTES", 9),
            ):
                self.refresher._materialize_git_commit(
                    repository, unique_tree, exact_destination
                )
            self.assertEqual((exact_destination / "first").read_bytes(), b"aaaa")
            self.assertEqual((exact_destination / "second").read_bytes(), b"bbbbb")

            exact_link_destination = temporary / "exact-symlink-limit"
            with mock.patch.object(self.refresher, "MAX_SYMLINK_BYTES", 6):
                self.refresher._materialize_git_commit(
                    repository, link_tree, exact_link_destination
                )
            self.assertEqual(os.readlink(exact_link_destination / "link"), "target")

    def test_bounded_git_runner_caps_times_out_and_reaps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(temporary, "bounded-runner.git")
            blob = self.write_git_blob(repository, b"content")
            tree = self.write_git_tree(repository, [("100644", blob, "content")])
            real_popen = subprocess.Popen
            real_killpg = os.killpg

            def exercise(arguments, *, stdin, output_limit, deadline, message):
                processes = []
                invocations = []

                def record_popen(*popen_args, **popen_kwargs):
                    process = real_popen(*popen_args, **popen_kwargs)
                    processes.append(process)
                    invocations.append(popen_kwargs)
                    return process

                with (
                    mock.patch.object(
                        self.refresher.subprocess,
                        "Popen",
                        side_effect=record_popen,
                    ),
                    mock.patch.object(
                        self.refresher.os,
                        "killpg",
                        wraps=real_killpg,
                    ) as killpg,
                    self.assertRaisesRegex(
                        self.refresher.lineage.LineageError,
                        f"^{message}$",
                    ) as captured,
                ):
                    self.refresher._bounded_git_output(
                        repository,
                        arguments,
                        stdin=stdin,
                        output_limit=output_limit,
                        deadline=deadline,
                    )

                self.assertEqual(len(processes), 1)
                self.assertTrue(invocations[0]["start_new_session"])
                self.assertTrue(killpg.called)
                self.assertIsNotNone(processes[0].poll())
                self.assertTrue(processes[0].stdout.closed)
                self.assertNotIn(str(temporary), str(captured.exception))

            exercise(
                ("ls-tree", "-r", "-z", tree),
                stdin=None,
                output_limit=0,
                deadline=time.monotonic() + 5,
                message="committed repository snapshot exceeds materialization limits",
            )

            read_descriptor, write_descriptor = os.pipe()
            try:
                with os.fdopen(read_descriptor, "rb", closefd=True) as held_open:
                    exercise(
                        ("cat-file", "--batch"),
                        stdin=held_open,
                        output_limit=1024,
                        deadline=time.monotonic() + 0.2,
                        message="committed repository snapshot is unavailable",
                    )
            finally:
                os.close(write_descriptor)

    def test_bounded_git_runner_uses_raw_close_after_stream_close_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(temporary, "close-fallback.git")
            blob = self.write_git_blob(repository, b"content")
            processes = []
            raw_close_calls = []
            real_popen = subprocess.Popen
            real_close = os.close

            class FailingClose:
                def __init__(self, stream):
                    self.stream = stream
                    self.close_calls = 0

                def fileno(self):
                    return self.stream.fileno()

                def close(self):
                    self.close_calls += 1
                    raise OSError("private-canary-stdout-close")

            def record_popen(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                stream = process.stdout
                proxy = FailingClose(stream)
                process.stdout = proxy
                processes.append((process, stream, proxy, stream.fileno()))
                return process

            def record_raw_close(descriptor):
                raw_close_calls.append(descriptor)
                return real_close(descriptor)

            with (
                mock.patch.object(
                    self.refresher.subprocess,
                    "Popen",
                    side_effect=record_popen,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "close",
                    side_effect=record_raw_close,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "^committed repository snapshot is unavailable$",
                ) as captured,
            ):
                self.refresher._bounded_git_output(
                    repository,
                    ("cat-file", "blob", blob),
                    stdin=None,
                    output_limit=1024,
                    deadline=time.monotonic() + 5,
                )

            self.assertEqual(len(processes), 1)
            process, stream, proxy, descriptor = processes[0]
            self.assertEqual(proxy.close_calls, 1)
            self.assertIn(descriptor, raw_close_calls)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
            self.assertIsNotNone(process.poll())
            self.assertNotIn("private-canary", str(captured.exception))
            self.assertNotIn(str(temporary), str(captured.exception))
            process.stdout = None
            with contextlib.suppress(OSError):
                stream.close()

    def test_bounded_git_runner_environment_cleanup_overrides_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(temporary, "cleanup-failure.git")
            blob = self.write_git_blob(repository, b"content")
            arguments = ("cat-file", "blob", blob)

            def reap_without_failure(process):
                process.wait(timeout=5)

            with (
                mock.patch.object(
                    self.refresher,
                    "_kill_and_reap_bounded_process",
                    side_effect=reap_without_failure,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "^committed repository snapshot exceeds materialization limits$",
                ),
            ):
                self.refresher._bounded_git_output(
                    repository,
                    arguments,
                    stdin=None,
                    output_limit=0,
                    deadline=time.monotonic() + 5,
                )

            real_temporary_directory = tempfile.TemporaryDirectory
            environments = []

            class FailingCleanup:
                def __init__(self, *args, **kwargs):
                    self.directory = real_temporary_directory(*args, **kwargs)
                    self.name = self.directory.name
                    self.cleanup_calls = 0
                    environments.append(self)

                def cleanup(self):
                    self.cleanup_calls += 1
                    self.directory.cleanup()
                    raise OSError("private-canary-environment-cleanup")

            with (
                mock.patch.object(
                    self.refresher.tempfile,
                    "TemporaryDirectory",
                    FailingCleanup,
                ),
                mock.patch.object(
                    self.refresher,
                    "_kill_and_reap_bounded_process",
                    side_effect=reap_without_failure,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "^committed repository snapshot is unavailable$",
                ) as captured,
            ):
                self.refresher._bounded_git_output(
                    repository,
                    arguments,
                    stdin=None,
                    output_limit=0,
                    deadline=time.monotonic() + 5,
                )

            self.assertEqual(len(environments), 1)
            self.assertEqual(environments[0].cleanup_calls, 1)
            self.assertNotIn("private-canary", str(captured.exception))
            self.assertNotIn(str(temporary), str(captured.exception))
            self.assertNotIn(environments[0].name, str(captured.exception))

    def test_bounded_git_runner_ignores_hostile_inherited_git_environment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(temporary, "trusted.git")
            decoy = self.make_bare_git_repository(temporary, "private-canary.git")
            blob = self.write_git_blob(repository, b"trusted-content")
            hostile = {
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(decoy / "objects"),
                "GIT_COMMON_DIR": str(decoy),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_GLOBAL": str(temporary / "private-global-config"),
                "GIT_CONFIG_KEY_0": "core.abbrev",
                "GIT_CONFIG_PARAMETERS": "'core.abbrev=4'",
                "GIT_CONFIG_SYSTEM": str(temporary / "private-system-config"),
                "GIT_CONFIG_VALUE_0": "4",
                "GIT_DIR": str(decoy),
                "GIT_OBJECT_DIRECTORY": str(decoy / "objects"),
                "GIT_WORK_TREE": str(temporary / "private-work-tree"),
            }
            invocations = []
            real_popen = subprocess.Popen

            def record_popen(*args, **kwargs):
                invocations.append(kwargs)
                return real_popen(*args, **kwargs)

            with (
                mock.patch.dict(os.environ, hostile),
                mock.patch.object(
                    self.refresher.subprocess,
                    "Popen",
                    side_effect=record_popen,
                ),
            ):
                raw = self.refresher._bounded_git_output(
                    repository,
                    ("cat-file", "blob", blob),
                    stdin=None,
                    output_limit=1024,
                    deadline=time.monotonic() + 5,
                )

            self.assertEqual(bytes(raw), b"trusted-content")
            self.assertEqual(len(invocations), 1)
            child_environment = invocations[0].get("env")
            self.assertIsNotNone(child_environment)
            for name, value in hostile.items():
                self.assertNotEqual(child_environment.get(name), value)
            self.assertNotIn(str(temporary), bytes(raw).decode("utf-8"))

    def test_bounded_git_runner_kills_failed_groups_and_retries_reap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(temporary, "failed-git.git")
            processes = []
            leader_states = []
            real_popen = subprocess.Popen

            def record_popen(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                processes.append(process)
                return process

            def record_killpg(process_id, sent_signal):
                self.assertEqual(process_id, processes[0].pid)
                self.assertEqual(sent_signal, signal.SIGKILL)
                leader_states.append(processes[0].poll())
                raise ProcessLookupError

            with (
                mock.patch.object(
                    self.refresher.subprocess,
                    "Popen",
                    side_effect=record_popen,
                ),
                mock.patch.object(
                    self.refresher.os,
                    "killpg",
                    side_effect=record_killpg,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "^committed repository snapshot is unavailable$",
                ) as captured,
            ):
                self.refresher._bounded_git_output(
                    repository,
                    ("rev-parse", "--verify", "--end-of-options", "missing"),
                    stdin=None,
                    output_limit=1024,
                    deadline=time.monotonic() + 5,
                )

            self.assertEqual(len(processes), 1)
            self.assertEqual(len(leader_states), 1)
            self.assertIsNotNone(leader_states[0])
            self.assertIsNotNone(processes[0].poll())
            self.assertTrue(processes[0].stdout.closed)
            self.assertNotIn(str(temporary), str(captured.exception))

        class InterruptedReap:
            pid = 12345

            def __init__(self):
                self.reaped = False
                self.wait_calls = 0

            def poll(self):
                return -signal.SIGKILL if self.reaped else None

            def wait(self, *, timeout=None):
                self.wait_calls += 1
                if self.wait_calls == 1:
                    raise InterruptedError
                self.reaped = True
                return -signal.SIGKILL

        interrupted = InterruptedReap()
        with mock.patch.object(self.refresher.os, "killpg") as killpg:
            self.refresher._kill_and_reap_bounded_process(interrupted)

        killpg.assert_called_once_with(interrupted.pid, signal.SIGKILL)
        self.assertEqual(interrupted.wait_calls, 2)
        self.assertTrue(interrupted.reaped)

    def test_bounded_git_runner_rejects_unreapable_child_without_unbounded_wait(
        self,
    ) -> None:
        class UnreapableProcess:
            pid = 12345

            def __init__(self, stdout):
                self.stdout = stdout
                self.kill_calls = 0
                self.wait_timeouts = []

            def poll(self):
                return None

            def kill(self):
                self.kill_calls += 1

            def wait(self, *, timeout=None):
                self.wait_timeouts.append(timeout)
                if len(self.wait_timeouts) > 2:
                    raise AssertionError("private-reap-retry-canary")
                if timeout is None:
                    raise AssertionError("private-unbounded-wait-canary")
                raise subprocess.TimeoutExpired("git", timeout)

        for signal_path in ("group", "leader-fallback"):
            with self.subTest(signal_path=signal_path):
                read_descriptor, write_descriptor = os.pipe()
                os.close(write_descriptor)
                stdout = os.fdopen(read_descriptor, "rb", closefd=True)
                process = UnreapableProcess(stdout)
                killpg_failure = (
                    None if signal_path == "group" else ProcessLookupError()
                )
                try:
                    with (
                        tempfile.TemporaryDirectory() as temporary_directory,
                        mock.patch.object(
                            self.refresher.subprocess,
                            "Popen",
                            return_value=process,
                        ),
                        mock.patch.object(
                            self.refresher.os,
                            "killpg",
                            side_effect=killpg_failure,
                        ),
                        self.assertRaisesRegex(
                            self.refresher.lineage.LineageError,
                            r"\Acommitted repository snapshot is unavailable\Z",
                        ) as captured,
                    ):
                        self.refresher._bounded_git_output(
                            Path(temporary_directory),
                            ("status",),
                            stdin=None,
                            output_limit=1,
                            deadline=time.monotonic() + 1,
                        )
                finally:
                    with contextlib.suppress(OSError):
                        stdout.close()

                self.assertEqual(len(process.wait_timeouts), 2)
                self.assertTrue(
                    all(
                        type(timeout) in {int, float} and timeout > 0
                        for timeout in process.wait_timeouts
                    ),
                    f"unbounded reap waits: {process.wait_timeouts}",
                )
                self.assertEqual(
                    process.kill_calls,
                    0 if signal_path == "group" else 1,
                )
                self.assertTrue(stdout.closed)
                self.assertEqual(
                    str(captured.exception),
                    "committed repository snapshot is unavailable",
                )
                self.assertIsNone(captured.exception.__cause__)
                rendered = str(captured.exception)
                self.assertNotIn("private-unbounded-wait-canary", rendered)
                self.assertNotIn("private-reap-retry-canary", rendered)
                self.assertNotIn(temporary_directory, rendered)
                self.assertNotIn("Traceback", rendered)

    def test_git_blob_rejects_same_length_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(temporary, "blob-integrity.git")
            original = b"trusted-content"
            blob = self.write_git_blob(repository, original)
            tree = self.write_git_tree(repository, [("100644", blob, "artifact")])
            commit = self.write_git_commit(repository, tree)
            real_bounded = self.refresher._bounded_git_output
            corrupted = []

            def corrupt_blob(*args, **kwargs):
                raw = real_bounded(*args, **kwargs)
                if args[1] == ("cat-file", "blob", blob):
                    raw[0] ^= 1
                    corrupted.append(bytes(raw))
                return raw

            with (
                mock.patch.object(
                    self.refresher,
                    "_bounded_git_output",
                    side_effect=corrupt_blob,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "^committed lineage artifact is invalid$",
                ) as captured,
            ):
                self.refresher._git_blob(repository, commit, "artifact")

            self.assertEqual(len(corrupted), 1)
            self.assertEqual(len(corrupted[0]), len(original))
            self.assertNotEqual(self.refresher._git_blob_sha1(corrupted[0]), blob)
            self.assertNotIn(str(temporary), str(captured.exception))

    def test_git_path_preflight_and_capture_aliases_reject_before_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(temporary, "path-safety.git")
            deep_commit = self.write_git_commit_with_path(
                repository,
                "deep",
                "/".join(["d"] * (self.refresher.MAX_TREE_COMPONENTS + 1) + ["leaf"]),
                b"x",
            )
            component_commit = self.write_git_commit_with_path(
                repository,
                "component",
                "x" * (self.refresher.MAX_TREE_COMPONENT_BYTES + 1),
                b"x",
            )
            path_commit = self.write_git_commit_with_path(
                repository,
                "path-bytes",
                "/".join(["p" * self.refresher.MAX_TREE_COMPONENT_BYTES] * 17),
                b"x",
            )
            blob = self.write_git_blob(repository, b"content")
            aggregate_tree = self.write_git_tree(
                repository,
                [
                    ("100644", blob, "first"),
                    ("100644", blob, "fourth"),
                    ("100644", blob, "second"),
                    ("100644", blob, "third"),
                ],
            )
            aggregate_commit = self.write_git_commit(repository, aggregate_tree)
            selected_root_cases = (
                ("non-ascii", "\u00e9", "source Git tree contains an unsafe entry"),
                (
                    "component",
                    "x" * (self.refresher.MAX_TREE_COMPONENT_BYTES + 1),
                    "source Git tree exceeds capture limits",
                ),
                (
                    "depth",
                    "/".join(["d"] * (self.refresher.MAX_TREE_COMPONENTS + 1)),
                    "source Git tree exceeds capture limits",
                ),
            )
            for kind, root, diagnostic in selected_root_cases:
                with (
                    self.subTest(kind=f"selected-root-{kind}"),
                    mock.patch.object(
                        self.refresher,
                        "_bounded_git_output",
                    ) as bounded,
                    self.assertRaisesRegex(
                        self.refresher.lineage.LineageError,
                        f"^{diagnostic}$",
                    ) as captured,
                ):
                    self.refresher._git_tree_identity(
                        repository, aggregate_commit, root
                    )

                bounded.assert_not_called()
                self.assertNotIn(str(temporary), str(captured.exception))

            path_cases = (
                (deep_commit, {}),
                (component_commit, {}),
                (path_commit, {}),
                (aggregate_commit, {"MAX_TREE_TOTAL_COMPONENTS": 3}),
            )
            for index, (commit, limits) in enumerate(path_cases):
                patches = [
                    mock.patch.object(self.refresher, name, value)
                    for name, value in limits.items()
                ]
                with self.subTest(kind="path", commit=commit):
                    destination = temporary / f"rejected-path-{index}"
                    with (
                        contextlib.ExitStack() as stack,
                        mock.patch.object(
                            self.refresher,
                            "_bounded_git_output",
                            wraps=self.refresher._bounded_git_output,
                        ) as bounded,
                        self.assertRaisesRegex(
                            self.refresher.lineage.LineageError,
                            "^committed repository snapshot exceeds "
                            "materialization limits$",
                        ) as captured,
                    ):
                        for patch in patches:
                            stack.enter_context(patch)
                        self.refresher._materialize_git_commit(
                            repository, commit, destination
                        )

                    phases = [call.args[1] for call in bounded.call_args_list]
                    self.assertNotIn(("cat-file", "--batch"), phases)
                    self.assertFalse(destination.exists())
                    self.assertNotIn(str(temporary), str(captured.exception))

            for commit in (deep_commit, path_commit):
                with (
                    self.subTest(kind="capture-limit", commit=commit),
                    mock.patch.object(
                        self.refresher,
                        "_bounded_git_output",
                        wraps=self.refresher._bounded_git_output,
                    ) as bounded,
                    self.assertRaisesRegex(
                        self.refresher.lineage.LineageError,
                        "^source Git tree exceeds capture limits$",
                    ) as captured,
                ):
                    self.refresher._git_tree_identity(repository, commit, ".")

                phases = [call.args[1] for call in bounded.call_args_list]
                self.assertNotIn(("cat-file", "--batch"), phases)
                self.assertNotIn(str(temporary), str(captured.exception))

            ascii_alias_tree = self.write_git_tree(
                repository,
                [("100644", blob, "Skill"), ("100644", blob, "skill")],
            )
            ascii_alias_commit = self.write_git_commit(repository, ascii_alias_tree)
            with (
                mock.patch.object(
                    self.refresher,
                    "_bounded_git_output",
                    wraps=self.refresher._bounded_git_output,
                ) as bounded,
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "^source Git tree contains a filesystem alias$",
                ) as captured,
            ):
                self.refresher._git_tree_identity(repository, ascii_alias_commit, ".")

            phases = [call.args[1] for call in bounded.call_args_list]
            self.assertNotIn(("cat-file", "--batch"), phases)
            self.assertNotIn(str(temporary), str(captured.exception))

            unicode_trees = (
                self.write_git_tree(
                    repository,
                    [("100644", blob, "e\u0301"), ("100644", blob, "\u00e9")],
                ),
                self.write_git_tree(
                    repository,
                    [("100644", blob, "\U00010570")],
                ),
            )
            for index, tree in enumerate(unicode_trees):
                commit = self.write_git_commit(repository, tree)
                with (
                    self.subTest(kind="unsafe-capture", tree=tree),
                    mock.patch.object(
                        self.refresher,
                        "_bounded_git_output",
                        wraps=self.refresher._bounded_git_output,
                    ) as bounded,
                    self.assertRaisesRegex(
                        self.refresher.lineage.LineageError,
                        "^source Git tree contains an unsafe entry$",
                    ) as captured,
                ):
                    self.refresher._git_tree_identity(repository, commit, ".")

                phases = [call.args[1] for call in bounded.call_args_list]
                self.assertNotIn(("cat-file", "--batch"), phases)
                self.assertNotIn(str(temporary), str(captured.exception))

                destination = temporary / f"rejected-unicode-{index}"
                with (
                    self.subTest(kind="unsafe-materialize", tree=tree),
                    mock.patch.object(
                        self.refresher,
                        "_bounded_git_output",
                        wraps=self.refresher._bounded_git_output,
                    ) as bounded,
                    self.assertRaisesRegex(
                        self.refresher.lineage.LineageError,
                        "^committed repository snapshot contains an unsafe entry$",
                    ) as captured,
                ):
                    self.refresher._materialize_git_commit(
                        repository, commit, destination
                    )

                phases = [call.args[1] for call in bounded.call_args_list]
                self.assertNotIn(("cat-file", "--batch"), phases)
                self.assertFalse(destination.exists())
                self.assertNotIn(str(temporary), str(captured.exception))

    def test_receipt_materializer_binds_blob_bytes_and_prevalidates_symlinks(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            repository = self.make_bare_git_repository(
                temporary, "materializer-integrity.git"
            )
            blob = self.write_git_blob(repository, b"content")
            tree = self.write_git_tree(repository, [("100644", blob, "content")])
            real_bounded = self.refresher._bounded_git_output

            def corrupt_content(*args, **kwargs):
                result = real_bounded(*args, **kwargs)
                if args[1] == ("cat-file", "--batch"):
                    result[result.index(b"\n") + 1] ^= 1
                return result

            corrupted_destination = temporary / "corrupted"
            with (
                mock.patch.object(
                    self.refresher,
                    "_bounded_git_output",
                    side_effect=corrupt_content,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "^committed repository snapshot is invalid$",
                ),
            ):
                self.refresher._materialize_git_commit(
                    repository, tree, corrupted_destination
                )
            self.assertFalse(corrupted_destination.exists())

            unsafe_link = self.write_git_blob(repository, b"../private-canary")
            link_tree = self.write_git_tree(
                repository, [("120000", unsafe_link, "link")]
            )
            link_destination = temporary / "unsafe-link"
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "^tree symlink escapes its root$",
            ) as captured:
                self.refresher._materialize_git_commit(
                    repository, link_tree, link_destination
                )
            self.assertFalse(link_destination.exists())
            self.assertNotIn("private-canary", str(captured.exception))

    def test_receipt_summary_uses_the_same_committed_artifact_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: bind lineage artifacts",
                ],
                cwd=clone,
                check=True,
            )
            output = self.stable_receipt_output("generation-bound")
            with mock.patch.object(
                self.refresher.lineage,
                "validate_lineage",
                wraps=self.refresher.lineage.validate_lineage,
            ) as trusted_validator:
                self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")

            trusted_validator.assert_called_once()
            call = trusted_validator.call_args
            self.assertEqual(call.args[0], call.args[1])
            self.assertEqual(call.kwargs, {"acquire_lock": False})
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                receipt["unresolved"]["source_ids"],
                list(EXPECTED_UNRESOLVED_SOURCE_IDS),
            )

    def test_receipt_validates_support_bytes_from_the_committed_generation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            removed_path = clone / "plugins/artifact-customs/README.md"
            removed_path.unlink()
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "plugins/artifact-customs/README.md",
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: remove candidate support byte",
                ],
                cwd=clone,
                check=True,
            )
            output = self.stable_receipt_output("invalid-support")
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "committed lineage validation failed",
            ):
                self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")

            self.assertFalse(output.exists())
            self.assertFalse(removed_path.exists())
            self.assertEqual(
                subprocess.run(
                    ["/usr/bin/git", "status", "--porcelain=v1"],
                    cwd=clone,
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout,
                "",
            )

    def test_receipt_rejects_target_validator_drift_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            execution_marker = Path(temporary_directory) / "target-executed"
            validator = clone / "scripts/validate_source_skill_lineage.py"
            source = validator.read_text(encoding="utf-8")
            needle = "    arguments = parser.parse_args()\n    try:\n"
            replacement = (
                "    arguments = parser.parse_args()\n"
                "    if arguments.receipt_summary:\n"
                f"        Path({str(execution_marker)!r}).touch()\n"
                '        print("private-canary-target-validator", file=sys.stderr)\n'
                "        return 9\n"
                "    try:\n"
            )
            self.assertIn(needle, source)
            validator.write_text(source.replace(needle, replacement), encoding="utf-8")
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: use a distinct committed validator",
                ],
                cwd=clone,
                check=True,
            )
            output = self.stable_receipt_output("validator-drift")
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "committed lineage validation failed",
            ) as captured:
                self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertFalse(execution_marker.exists())
            self.assertFalse(output.exists())

    def test_receipt_rejects_target_drift_before_environment_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            execution_marker = Path(temporary_directory) / "environment-accessed"
            validator = clone / "scripts/validate_source_skill_lineage.py"
            source = validator.read_text(encoding="utf-8")
            needle = "    arguments = parser.parse_args()\n    try:\n"
            replacement = (
                "    arguments = parser.parse_args()\n"
                f"    Path({str(execution_marker)!r}).touch()\n"
                "    if arguments.receipt_summary and (\n"
                '        os.environ.get("PRIVATE_CANARY_TOKEN")\n'
                '        or os.environ.get("HOME") == "private-canary-home"\n'
                '        or os.environ.get("PATH") != "/usr/bin:/bin"\n'
                "        or Path.cwd() != Path(__file__).resolve().parents[1]\n"
                "    ):\n"
                '        print("private-canary-target-process", file=sys.stderr)\n'
                "        return 9\n"
                "    try:\n"
            )
            self.assertIn(needle, source)
            validator.write_text(source.replace(needle, replacement), encoding="utf-8")
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: inspect the committed validator process",
                ],
                cwd=clone,
                check=True,
            )
            output = self.stable_receipt_output("environment-drift")
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "HOME": "private-canary-home",
                        "PRIVATE_CANARY_TOKEN": "private-canary-secret",
                    },
                    clear=False,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "committed lineage validation failed",
                ),
            ):
                self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")

            self.assertFalse(execution_marker.exists())
            self.assertFalse(output.exists())

    def test_receipt_never_executes_target_output_or_child_code(self) -> None:
        for stream_name in ("stdout", "stderr"):
            with (
                self.subTest(stream=stream_name),
                tempfile.TemporaryDirectory() as root,
            ):
                clone = self.clone_refresh_fixture(root, include_tests=True)
                child_pid = Path(root) / f"{stream_name}-child.pid"
                validator = clone / "scripts/validate_source_skill_lineage.py"
                source = validator.read_text(encoding="utf-8")
                needle = "    arguments = parser.parse_args()\n    try:\n"
                replacement = (
                    "    arguments = parser.parse_args()\n"
                    "    if arguments.receipt_summary:\n"
                    "        import subprocess\n"
                    "        child = subprocess.Popen(\n"
                    "            [sys.executable, '-c', 'import time; time.sleep(60)'],\n"
                    "            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
                    "            stderr=subprocess.DEVNULL, start_new_session=True,\n"
                    "        )\n"
                    f"        Path({str(child_pid)!r}).write_text(\n"
                    "            str(child.pid), encoding='ascii'\n"
                    "        )\n"
                    f"        target = sys.{stream_name}.buffer\n"
                    "        while True:\n"
                    "            target.write(b'x' * 8192)\n"
                    "            target.flush()\n"
                    "    try:\n"
                )
                self.assertIn(needle, source)
                validator.write_text(
                    source.replace(needle, replacement), encoding="utf-8"
                )
                subprocess.run(
                    [
                        "/usr/bin/git",
                        "add",
                        "--",
                        str(LINEAGE_ROOT),
                        str(RESEARCH_REPORT),
                        "scripts",
                        "tests",
                    ],
                    cwd=clone,
                    check=True,
                )
                subprocess.run(
                    [
                        "/usr/bin/git",
                        "-c",
                        "user.name=Lineage Test",
                        "-c",
                        "user.email=lineage@example.invalid",
                        "commit",
                        "--quiet",
                        "-m",
                        "test: emit bounded committed-validator output",
                    ],
                    cwd=clone,
                    check=True,
                )
                output = self.stable_receipt_output(f"{stream_name}-bounded")
                with self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "committed lineage validation failed",
                ):
                    self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")

                self.assertFalse(output.exists())
                self.assertFalse(child_pid.exists())

    def test_receipt_never_executes_target_sleep_or_detached_child(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            clone = self.clone_refresh_fixture(root, include_tests=True)
            child_pid = Path(root) / "timeout-child.pid"
            validator = clone / "scripts/validate_source_skill_lineage.py"
            source = validator.read_text(encoding="utf-8")
            needle = "    arguments = parser.parse_args()\n    try:\n"
            replacement = (
                "    arguments = parser.parse_args()\n"
                "    if arguments.receipt_summary:\n"
                "        import subprocess\n"
                "        import time\n"
                "        child = subprocess.Popen(\n"
                "            [sys.executable, '-c', 'import time; time.sleep(60)'],\n"
                "            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
                "            stderr=subprocess.DEVNULL, start_new_session=True,\n"
                "        )\n"
                f"        Path({str(child_pid)!r}).write_text(\n"
                "            str(child.pid), encoding='ascii'\n"
                "        )\n"
                "        time.sleep(60)\n"
                "    try:\n"
            )
            self.assertIn(needle, source)
            validator.write_text(source.replace(needle, replacement), encoding="utf-8")
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: time out a committed validator",
                ],
                cwd=clone,
                check=True,
            )
            output = self.stable_receipt_output("timed-out")
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "committed lineage validation failed",
            ):
                self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")

            self.assertFalse(output.exists())
            self.assertFalse(child_pid.exists())

    def test_receipt_rejects_target_summary_code_without_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            validator = clone / "scripts/validate_source_skill_lineage.py"
            source = validator.read_text(encoding="utf-8")
            needle = "    arguments = parser.parse_args()\n    try:\n"
            replacement = (
                "    arguments = parser.parse_args()\n"
                "    if arguments.receipt_summary:\n"
                "        sys.stdout.write("
                "'{\"/Users/private-canary/receipt\":1,'"
                "'\"/Users/private-canary/receipt\":2}\\n')\n"
                "        return 0\n"
                "    try:\n"
            )
            self.assertIn(needle, source)
            validator.write_text(source.replace(needle, replacement), encoding="utf-8")
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: emit a private committed-validator summary",
                ],
                cwd=clone,
                check=True,
            )
            output = self.stable_receipt_output("private-summary")
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "committed lineage validation failed",
            ) as captured:
                self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertNotIn("/Users/", str(captured.exception))
            self.assertFalse(output.exists())

    def test_receipt_ignores_git_replacement_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: bind replacement-object receipt fixture",
                ],
                cwd=clone,
                check=True,
            )
            validator_path = clone / "scripts/validate_source_skill_lineage.py"
            validator_raw = validator_path.read_bytes()
            original_blob = subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(clone),
                    "rev-parse",
                    "HEAD:scripts/validate_source_skill_lineage.py",
                ],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            replacement_blob = (
                subprocess.run(
                    ["/usr/bin/git", "-C", str(clone), "hash-object", "-w", "--stdin"],
                    input=b"private-canary replacement validator\n",
                    capture_output=True,
                    check=True,
                )
                .stdout.decode("ascii")
                .strip()
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(clone),
                    "replace",
                    original_blob,
                    replacement_blob,
                ],
                check=True,
            )

            output = self.stable_receipt_output("replacement-object")
            self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                receipt["validator"]["sha256"],
                "sha256:" + hashlib.sha256(validator_raw).hexdigest(),
            )
            self.assertNotIn("private-canary", output.read_text(encoding="utf-8"))

    def test_receipt_rejects_target_validator_self_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            validator = clone / "scripts/validate_source_skill_lineage.py"
            source = validator.read_text(encoding="utf-8")
            needle = "    if arguments.receipt_summary:\n        receipt_summary = {\n"
            replacement = (
                "    if arguments.receipt_summary:\n"
                "        Path(__file__).write_text(\n"
                '            "private-canary-approved-validator\\n", encoding="utf-8"\n'
                "        )\n"
                "        receipt_summary = {\n"
            )
            self.assertIn(needle, source)
            validator.write_text(source.replace(needle, replacement), encoding="utf-8")
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: self-replace the committed validator",
                ],
                cwd=clone,
                check=True,
            )

            output = self.stable_receipt_output("self-replaced")
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "committed lineage validation failed",
            ) as captured:
                self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertFalse(output.exists())

    def test_receipt_cli_redacts_symlink_loop_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            external = temporary / "external"
            external.mkdir()
            repository_a = temporary / "ivans-work-macbook-repository-a"
            repository_b = temporary / "ivans-work-macbook-repository-b"
            repository_a.symlink_to(repository_b.name)
            repository_b.symlink_to(repository_a.name)
            output_a = temporary / "ivans-work-macbook-output-a"
            output_b = temporary / "ivans-work-macbook-output-b"
            output_a.symlink_to(output_b.name)
            output_b.symlink_to(output_a.name)
            cases = (
                (
                    repository_a,
                    external / "receipt.json",
                    "reconciliation receipt failed",
                ),
                (
                    REPOSITORY,
                    output_a / "receipt.json",
                    "reconciliation receipt output parent must be an existing directory",
                ),
            )
            for repository, output, message in cases:
                with self.subTest(message=message):
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(REFRESHER),
                            "receipt",
                            str(repository),
                            "--output",
                            str(output),
                            "--captured-at-utc",
                            "2026-08-18T00:00:00Z",
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )
                    self.assertEqual(completed.returncode, 1)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(
                        completed.stderr,
                        f"source-skill-lineage-refresh: {message}\n",
                    )
                    self.assertNotIn(str(temporary), completed.stderr)
                    self.assertNotIn("ivans-work-macbook", completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)
            self.assertEqual(list(temporary.rglob("receipt.json")), [])

    def test_receipt_rejects_candidate_and_git_metadata_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory)
            git_directory = Path(
                self.refresher._git(clone, "rev-parse", "--absolute-git-dir")
            )
            for output in (clone / "receipt.json", git_directory / "receipt.json"):
                with (
                    self.subTest(output_parent=output.parent.name),
                    self.assertRaisesRegex(
                        self.refresher.lineage.LineageError,
                        "output must be external to the candidate",
                    ),
                ):
                    self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")
                self.assertFalse(output.exists())
            linked = Path(temporary_directory) / "linked"
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(clone),
                    "worktree",
                    "add",
                    "--quiet",
                    "--detach",
                    str(linked),
                ],
                check=True,
                timeout=30,
            )
            common_git_directory = Path(
                self.refresher._git(
                    linked,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                )
            )
            linked_output = common_git_directory / "linked-receipt.json"
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "output must be external to the candidate",
            ):
                self.refresher.receipt(linked, linked_output, "2026-08-18T00:00:00Z")
            self.assertFalse(linked_output.exists())

    def test_receipt_rejects_open_parent_relocated_inside_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: bind lineage artifacts",
                ],
                cwd=clone,
                check=True,
            )
            linked = Path(temporary_directory) / "linked"
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(clone),
                    "worktree",
                    "add",
                    "--quiet",
                    "--detach",
                    str(linked),
                ],
                check=True,
                timeout=30,
            )
            common_git_directory = Path(
                self.refresher._git(
                    linked,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                )
            )
            real_external_parent = self.refresher._external_receipt_parent
            temporary = Path(temporary_directory)
            cases = (
                ("checkout", clone, clone / "relocated-receipt-parent"),
                (
                    "common-git-directory",
                    linked,
                    common_git_directory / "relocated-receipt-parent",
                ),
            )
            for name, repository, relocated_parent in cases:
                with self.subTest(destination=name):
                    external_parent = temporary / f"external-{name}"
                    external_parent.mkdir()
                    output = external_parent / "receipt.json"

                    def relocate_after_parent_open(
                        candidate_repository,
                        candidate,
                        *,
                        source_parent=external_parent,
                        destination_parent=relocated_parent,
                    ):
                        result = real_external_parent(candidate_repository, candidate)
                        source_parent.rename(destination_parent)
                        return result

                    with (
                        mock.patch.object(
                            self.refresher,
                            "_external_receipt_parent",
                            side_effect=relocate_after_parent_open,
                        ),
                        self.assertRaisesRegex(
                            self.refresher.lineage.LineageError,
                            r"\Areconciliation receipt publication failed\Z",
                        ) as captured,
                    ):
                        self.refresher.receipt(
                            repository, output, "2026-08-18T00:00:00Z"
                        )

                    self.assertEqual(
                        str(captured.exception),
                        "reconciliation receipt publication failed",
                    )
                    self.assertFalse(output.exists())
                    self.assertFalse((relocated_parent / "receipt.json").exists())

    def test_receipt_publication_and_cleanup_stay_bound_to_open_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: bind lineage artifacts",
                ],
                cwd=clone,
                check=True,
            )

            output = self.stable_receipt_output("descriptor-bound")
            stable_parent = output.parent.resolve(strict=True)
            real_external_parent = self.refresher._external_receipt_parent
            real_open = self.refresher.os.open
            opened_parent = []
            open_calls = []

            def record_open(path, flags, mode=0o777, *, dir_fd=None):
                open_calls.append((path, flags, dir_fd))
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            def record_external_parent(repository, candidate):
                result = real_external_parent(repository, candidate)
                opened_parent.append(result[0])
                return result

            with (
                mock.patch.object(self.refresher.os, "open", side_effect=record_open),
                mock.patch.object(
                    self.refresher,
                    "_external_receipt_parent",
                    side_effect=record_external_parent,
                ),
            ):
                self.refresher.receipt(clone, output, "2026-08-18T00:00:00Z")

            self.assertTrue(output.is_file())
            self.assertEqual(len(opened_parent), 1)
            self.assertTrue(
                any(
                    Path(path) == stable_parent
                    and flags & self.refresher.os.O_DIRECTORY
                    and flags & self.refresher.os.O_NOFOLLOW
                    and dir_fd is None
                    for path, flags, dir_fd in open_calls
                )
            )
            self.assertTrue(
                any(
                    path == output.name
                    and flags & self.refresher.os.O_NOFOLLOW
                    and dir_fd == opened_parent[0]
                    for path, flags, dir_fd in open_calls
                )
            )

            failed_output = self.stable_receipt_output("failed-file-fsync")
            failed_parent_descriptor = []
            real_fsync = self.refresher.os.fsync
            failed = False

            def record_failed_parent(repository, candidate):
                result = real_external_parent(repository, candidate)
                failed_parent_descriptor.append(result[0])
                return result

            def fail_file_fsync(descriptor):
                nonlocal failed
                if descriptor != failed_parent_descriptor[0] and not failed:
                    failed = True
                    raise OSError("private-canary-fsync")
                return real_fsync(descriptor)

            with (
                mock.patch.object(
                    self.refresher,
                    "_external_receipt_parent",
                    side_effect=record_failed_parent,
                ),
                mock.patch.object(
                    self.refresher.os, "fsync", side_effect=fail_file_fsync
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "reconciliation receipt publication failed",
                ) as captured,
            ):
                self.refresher.receipt(clone, failed_output, "2026-08-18T00:00:00Z")

            self.assertNotIn("private-canary", str(captured.exception))
            self.assertTrue(failed_output.is_file())

            replacement = b"private-canary-replacement\n"
            for swap_on in ("file", "parent"):
                with self.subTest(swap_on=swap_on):
                    leaf_output = self.stable_receipt_output(f"leaf-race-{swap_on}")
                    retained_receipt = self.stable_receipt_output(f"retained-{swap_on}")
                    replaced = False

                    def replace_leaf_after_sync(
                        descriptor,
                        *,
                        selected_phase=swap_on,
                        output_path=leaf_output,
                        retained_path=retained_receipt,
                    ):
                        nonlocal replaced
                        result = real_fsync(descriptor)
                        mode = os.fstat(descriptor).st_mode
                        selected = (
                            selected_phase == "file" and stat.S_ISREG(mode)
                        ) or (selected_phase == "parent" and stat.S_ISDIR(mode))
                        if selected and not replaced:
                            replaced = True
                            output_path.rename(retained_path)
                            output_path.write_bytes(replacement)
                        return result

                    with (
                        mock.patch.object(
                            self.refresher.os,
                            "fsync",
                            side_effect=replace_leaf_after_sync,
                        ),
                        self.assertRaisesRegex(
                            self.refresher.lineage.LineageError,
                            "reconciliation receipt publication failed",
                        ) as leaf_error,
                    ):
                        self.refresher.receipt(
                            clone, leaf_output, "2026-08-18T00:00:00Z"
                        )

                    self.assertNotIn("private-canary", str(leaf_error.exception))
                    self.assertEqual(leaf_output.read_bytes(), replacement)
                    self.assertTrue(retained_receipt.is_file())

    def test_receipt_derives_tree_from_commit_and_rejects_head_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            clone = self.clone_refresh_fixture(temporary_directory, include_tests=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "add",
                    "--",
                    str(LINEAGE_ROOT),
                    str(RESEARCH_REPORT),
                    "scripts",
                    "tests",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-c",
                    "user.name=Lineage Test",
                    "-c",
                    "user.email=lineage@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: bind lineage artifacts",
                ],
                cwd=clone,
                check=True,
            )
            commit = subprocess.run(
                ["/usr/bin/git", "rev-parse", "HEAD"],
                cwd=clone,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            real_git = self.refresher._git
            real_external_parent = self.refresher._external_receipt_parent
            real_fsync = self.refresher.os.fsync
            git_calls = []
            fsynced = []
            parent_descriptors = []

            def record_git(repository, *arguments):
                git_calls.append(arguments)
                return real_git(repository, *arguments)

            def record_external_parent(repository, output):
                result = real_external_parent(repository, output)
                parent_descriptors.append(result[0])
                return result

            def record_fsync(descriptor):
                fsynced.append(descriptor)
                return real_fsync(descriptor)

            bound_output = self.stable_receipt_output("bound-tree")
            with (
                mock.patch.object(self.refresher, "_git", side_effect=record_git),
                mock.patch.object(
                    self.refresher,
                    "_external_receipt_parent",
                    side_effect=record_external_parent,
                ),
                mock.patch.object(self.refresher.os, "fsync", side_effect=record_fsync),
            ):
                self.refresher.receipt(clone, bound_output, "2026-08-18T00:00:00Z")
            status_arguments = (
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "--work-tree",
                str(clone.resolve()),
                "status",
                "--porcelain=v2",
                "--branch",
                "--untracked-files=all",
            )
            self.assertIn(("rev-parse", f"{commit}^{{tree}}"), git_calls)
            self.assertNotIn(("rev-parse", "HEAD^{tree}"), git_calls)
            self.assertNotIn(("rev-parse", "HEAD"), git_calls)
            self.assertEqual(git_calls.count(status_arguments), 2)
            self.assertEqual(len(parent_descriptors), 1)
            self.assertIn(parent_descriptors[0], fsynced)

            status_calls = 0

            def advance_before_final_status(repository, *arguments):
                nonlocal status_calls
                if arguments == status_arguments:
                    status_calls += 1
                if arguments == status_arguments and status_calls == 2:
                    (clone / "unrelated.txt").write_text(
                        "unrelated\n", encoding="utf-8"
                    )
                    subprocess.run(
                        ["/usr/bin/git", "add", "--", "unrelated.txt"],
                        cwd=clone,
                        check=True,
                    )
                    subprocess.run(
                        [
                            "/usr/bin/git",
                            "-c",
                            "user.name=Lineage Test",
                            "-c",
                            "user.email=lineage@example.invalid",
                            "commit",
                            "--quiet",
                            "-m",
                            "test: advance unrelated head",
                        ],
                        cwd=clone,
                        check=True,
                    )
                return real_git(repository, *arguments)

            drift_output = self.stable_receipt_output("head-drift")
            with (
                mock.patch.object(
                    self.refresher, "_git", side_effect=advance_before_final_status
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "candidate changed while receipt was captured",
                ),
            ):
                self.refresher.receipt(clone, drift_output, "2026-08-18T00:00:00Z")
            self.assertEqual(status_calls, 2)
            self.assertFalse(drift_output.exists())

            missing_output = Path(temporary_directory) / "missing" / "receipt.json"
            with self.assertRaisesRegex(
                self.refresher.lineage.LineageError,
                "output parent must be an existing directory",
            ):
                self.refresher.receipt(clone, missing_output, "2026-08-18T00:00:00Z")
            self.assertFalse(missing_output.parent.exists())

            blocked_output = self.stable_receipt_output("blocked-open")
            real_open = self.refresher.os.open

            def fail_publication_open(path, flags, mode=0o777, *, dir_fd=None):
                if dir_fd is not None and os.fspath(path) == blocked_output.name:
                    raise OSError(13, "private-canary-open", str(blocked_output))
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    self.refresher.os,
                    "open",
                    side_effect=fail_publication_open,
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "reconciliation receipt publication failed",
                ) as captured,
                self.refresher.lineage._lineage_lock(clone, exclusive=False) as view,
            ):
                self.refresher._receipt_locked(
                    view.root,
                    blocked_output,
                    "2026-08-18T00:00:00Z",
                    view,
                )
            self.assertNotIn("private-canary", str(captured.exception))
            self.assertFalse(blocked_output.exists())

            failed_output = self.stable_receipt_output("failed-fsync")
            with (
                mock.patch.object(
                    self.refresher.os,
                    "fsync",
                    side_effect=OSError("private-canary-fsync"),
                ),
                self.assertRaisesRegex(
                    self.refresher.lineage.LineageError,
                    "reconciliation receipt publication failed",
                ) as captured,
            ):
                self.refresher.receipt(clone, failed_output, "2026-08-18T00:00:00Z")
            self.assertNotIn("private-canary", str(captured.exception))
            self.assertTrue(failed_output.is_file())
            self.assertEqual(stat.S_IMODE(failed_output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
