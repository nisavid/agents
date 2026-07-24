from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
import re
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/evidence_transport.py"
SPEC = importlib.util.spec_from_file_location("evidence_transport_test", PATH)
assert SPEC and SPEC.loader
SUPPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUPPORT)


class SupportError(RuntimeError):
    pass


class EvidenceTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        os.chmod(self.root, 0o700)

    def parse(self, content: bytes):
        return SUPPORT.strict_json_bytes(
            content, label="fixture", error_factory=SupportError
        )

    def test_strict_json_rejects_duplicate_and_non_finite_numbers(self) -> None:
        for content in (
            b'{"key":1,"key":2}',
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b'{"value":-Infinity}',
            b'{"value":1e400}',
        ):
            with self.subTest(content=content):
                with self.assertRaises(SupportError):
                    self.parse(content)

    def test_atomic_write_is_private_durable_and_cleans_partial_temp(self) -> None:
        output = self.root / "nested" / "artifact.json"
        with mock.patch.object(
            SUPPORT.os, "fsync", wraps=SUPPORT.os.fsync
        ) as synchronize:
            SUPPORT.private_atomic_write(
                output, b'{"ok":true}\n', error_factory=SupportError
            )
        self.assertEqual(output.read_bytes(), b'{"ok":true}\n')
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertGreaterEqual(synchronize.call_count, 2)

        failed = self.root / "nested" / "failed.json"
        with mock.patch.object(SUPPORT.os, "replace", side_effect=OSError("fault")):
            with self.assertRaisesRegex(SupportError, "durably write"):
                SUPPORT.private_atomic_write(
                    failed, b"partial", error_factory=SupportError
                )
        self.assertFalse(failed.exists())
        self.assertEqual(list(failed.parent.glob(f".{failed.name}.*.tmp")), [])

    def test_symlink_output_or_parent_is_rejected(self) -> None:
        real = self.root / "real"
        real.mkdir(mode=0o700)
        linked = self.root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(SupportError, "not private and owned"):
            SUPPORT.private_atomic_write(
                linked / "artifact", b"x", error_factory=SupportError
            )

        target = real / "target"
        target.write_bytes(b"old")
        output = real / "output"
        output.symlink_to(target)
        with self.assertRaisesRegex(SupportError, "symlink"):
            SUPPORT.private_atomic_write(output, b"new", error_factory=SupportError)
        self.assertEqual(target.read_bytes(), b"old")

    def test_raw_stream_identity_preserves_exact_bytes(self) -> None:
        first = SUPPORT.stream_identity(b"line\x00\xff\n")
        second = SUPPORT.stream_identity(b"line\x00\xff\r\n")
        self.assertEqual(first["byte_count"], 7)
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_shared_runtime_validates_models_paths_origins_and_executables(
        self,
    ) -> None:
        self.assertTrue(SUPPORT.exact_claude_model("claude-sonnet-5"))
        self.assertFalse(SUPPORT.exact_claude_model("sonnet"))
        nested = self.root / "nested"
        nested.mkdir()
        artifact = nested / "artifact.json"
        artifact.write_text("{}\n", encoding="utf-8")
        self.assertEqual(
            SUPPORT.safe_regular_file(
                self.root,
                "nested/artifact.json",
                label="artifact",
                error_factory=SupportError,
            ),
            artifact,
        )
        with self.assertRaises(SupportError):
            SUPPORT.safe_relative_path(
                "../escape", label="artifact", error_factory=SupportError
            )
        canonical = "https://github.com/nisavid/agents"
        self.assertEqual(
            SUPPORT.normalize_repository_origin(
                "git@github.com:nisavid/agents.git",
                canonical=canonical,
                aliases={canonical, "git@github.com:nisavid/agents.git"},
                error_factory=SupportError,
                error_message="wrong origin",
            ),
            canonical,
        )
        executable = self.root / "claude"
        executable.write_bytes(b"runtime")
        executable.chmod(0o700)
        completed = subprocess.CompletedProcess(
            [str(executable), "--version"], 0, b"2.1.0 (Claude Code)\n", b""
        )
        with (
            mock.patch.object(SUPPORT.shutil, "which", return_value=str(executable)),
            mock.patch.object(SUPPORT.subprocess, "run", return_value=completed),
        ):
            identity = SUPPORT.resolve_executable_identity(
                "claude",
                error_factory=SupportError,
                display_name="Claude Code",
                version_validator=lambda value: value.endswith("(Claude Code)"),
            )
        self.assertEqual(identity["path"], str(executable))
        self.assertEqual(identity["sha256"], SUPPORT.digest_bytes(b"runtime"))

    def test_shared_runtime_owns_failure_and_attempt_envelopes(self) -> None:
        failure = SUPPORT.ProviderTransportFailure(
            "timed out", stdout=b"partial", stderr=b"warning", timed_out=True
        )
        self.assertEqual(SUPPORT.failure_streams(failure), (b"partial", b"warning"))
        self.assertTrue(failure.timed_out)

        attempts = self.root / "attempts"
        attempts.mkdir()
        writer = mock.Mock()
        document = SUPPORT.persist_attempt_envelope(
            attempts / "attempt-0001.json",
            {"status": "started"},
            writer=writer,
            signer=lambda value: value | {"sha256": "bound"},
        )
        self.assertEqual(document["sha256"], "bound")
        writer.assert_called_once_with(attempts / "attempt-0001.json", document)
        (attempts / "attempt-0001.json").write_text("{}\n", encoding="utf-8")
        self.assertEqual(
            SUPPORT.next_attempt_envelope(
                attempts,
                pattern=re.compile(r"attempt-(\d{4})\.json"),
                error_factory=SupportError,
            ),
            2,
        )

    def test_shared_attempt_journal_corpus_covers_both_runner_layouts(self) -> None:
        layouts = {
            "routing": {
                "statuses": {
                    "started": "running",
                    "success": "success",
                    "failure": "failed",
                    "timeout": "timeout",
                },
                "stream_fields": {
                    "stdout": ("stdout_relpath", "stdout_sha256"),
                    "stderr": ("stderr_relpath", "stderr_sha256"),
                },
            },
            "control": {
                "statuses": {
                    "started": "started",
                    "success": "completed",
                    "failure": "failed",
                    "timeout": "timeout",
                },
                "stream_fields": {
                    "stdout": ("stdout_artifact_relpath", "stdout_sha256"),
                    "stderr": ("stderr_artifact_relpath", "stderr_sha256"),
                },
            },
        }
        outcomes = ("success", "failure", "timeout")
        for layout_name, layout in layouts.items():
            for outcome in outcomes:
                with self.subTest(layout=layout_name, outcome=outcome):
                    root = self.root / layout_name / outcome
                    SUPPORT.prepare_private_directory(root, error_factory=SupportError)
                    allocation = SUPPORT.allocate_attempt_journal(
                        root,
                        attempt_relpath="attempts/attempt-0001.json",
                        stream_relpaths={
                            "stdout": "streams/stdout.bin",
                            "stderr": "streams/stderr.bin",
                        },
                        error_factory=SupportError,
                    )

                    def write_document(path, value):
                        SUPPORT.private_atomic_write(
                            path,
                            SUPPORT.json_file_bytes(value),
                            error_factory=SupportError,
                        )

                    def write_artifact(path, content):
                        SUPPORT.frozen_atomic_write(
                            path, content, error_factory=SupportError
                        )

                    clock_values = iter(("start", "finish"))

                    def invoke():
                        started = SUPPORT.read_strict_json(
                            allocation.attempt_path,
                            label="attempt",
                            error_factory=SupportError,
                        )
                        self.assertEqual(
                            started["status"], layout["statuses"]["started"]
                        )
                        if outcome == "success":
                            return SUPPORT.AttemptSuccess(
                                value="accepted",
                                streams={"stdout": b"answer", "stderr": b""},
                                fields={"returncode": 0},
                            )
                        raise SUPPORT.ProviderTransportFailure(
                            outcome,
                            stdout=b"partial",
                            stderr=b"diagnostic",
                            returncode=None if outcome == "timeout" else 7,
                            timed_out=outcome == "timeout",
                        )

                    if outcome == "success":
                        value, _document = SUPPORT.run_attempt_journal(
                            allocation,
                            initial={"schema_version": 1},
                            invoke=invoke,
                            document_writer=write_document,
                            artifact_writer=write_artifact,
                            clock=lambda: next(clock_values),
                            status_names=layout["statuses"],
                            stream_fields=layout["stream_fields"],
                            digest=SUPPORT.digest_bytes,
                        )
                        self.assertEqual(value, "accepted")
                    else:
                        with self.assertRaises(SUPPORT.ProviderTransportFailure):
                            SUPPORT.run_attempt_journal(
                                allocation,
                                initial={"schema_version": 1},
                                invoke=invoke,
                                document_writer=write_document,
                                artifact_writer=write_artifact,
                                clock=lambda: next(clock_values),
                                status_names=layout["statuses"],
                                stream_fields=layout["stream_fields"],
                                digest=SUPPORT.digest_bytes,
                            )
                    terminal = SUPPORT.read_strict_json(
                        allocation.attempt_path,
                        label="attempt",
                        error_factory=SupportError,
                    )
                    self.assertEqual(terminal["status"], layout["statuses"][outcome])
                    self.assertEqual(terminal["started_at"], "start")
                    self.assertEqual(terminal["finished_at"], "finish")
                    expected_stdout = b"answer" if outcome == "success" else b"partial"
                    expected_stderr = b"" if outcome == "success" else b"diagnostic"
                    self.assertEqual(
                        allocation.stream_paths["stdout"].read_bytes(), expected_stdout
                    )
                    self.assertEqual(
                        allocation.stream_paths["stderr"].read_bytes(), expected_stderr
                    )
                    self.assertEqual(
                        SUPPORT.attempt_history(
                            root,
                            root / "attempts",
                            "attempt-*.json",
                            error_factory=SupportError,
                        ),
                        ["attempts/attempt-0001.json"],
                    )

    def test_candidate_and_semantic_identities_bind_paths_and_bytes(self) -> None:
        subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            check=True,
            capture_output=True,
        )
        semantic = self.root / "semantic"
        semantic.mkdir()
        source = semantic / "source.txt"
        source.write_text("first\n", encoding="utf-8")
        candidate_before = SUPPORT.candidate_content_identity(
            self.root, error_factory=SupportError
        )
        semantic_before = SUPPORT.semantic_tree_digest(
            self.root, ("semantic",), error_factory=SupportError
        )

        source.write_text("second\n", encoding="utf-8")

        self.assertNotEqual(
            candidate_before,
            SUPPORT.candidate_content_identity(self.root, error_factory=SupportError),
        )
        self.assertNotEqual(
            semantic_before,
            SUPPORT.semantic_tree_digest(
                self.root, ("semantic",), error_factory=SupportError
            ),
        )


if __name__ == "__main__":
    unittest.main()
