from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[4]
MODULE_PATH = (
    ROOT
    / "plugins/mergecraft/skills/writing-reviewable-pr-descriptions/scripts/change_navigation/git_observer.py"
)
SPEC = importlib.util.spec_from_file_location("git_observer", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class GitObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name) / "repository"
        self.repository.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.com")
        self.git("config", "user.name", "Fixture")
        self.write("keep.txt", b"copy source\n")
        self.write("rename.txt", b"rename me\n")
        self.write("delete.txt", b"delete me\n")
        self.write("modify.txt", b"before\n")
        self.write("binary.bin", b"\x00before")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()

        (self.repository / "rename.txt").rename(self.repository / "renamed.txt")
        shutil.copy2(self.repository / "keep.txt", self.repository / "copied.txt")
        (self.repository / "delete.txt").unlink()
        self.write("modify.txt", b"after\n")
        self.write("added.txt", b"added\n")
        self.write("binary.bin", b"\x00after")
        self.git("add", "-A")
        self.git("commit", "-qm", "head")
        self.head = self.git("rev-parse", "HEAD").stdout.strip()

    def git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True,
            text=True,
            capture_output=True,
        )

    def write(self, relative: str, content: bytes) -> None:
        (self.repository / relative).write_bytes(content)

    def test_observes_exact_added_modified_deleted_renamed_copied_and_binary_rows(
        self,
    ) -> None:
        rows = MODULE.observe_git_diff(
            self.repository, base_oid=self.base, head_oid=self.head
        )
        by_target = {row["target_path"]: row for row in rows}
        self.assertEqual(
            set(by_target),
            {
                "added.txt",
                "binary.bin",
                "copied.txt",
                "delete.txt",
                "modify.txt",
                "renamed.txt",
            },
        )
        self.assertEqual(by_target["added.txt"]["operation"], "added")
        self.assertEqual(by_target["delete.txt"]["operation"], "deleted")
        self.assertEqual(by_target["renamed.txt"]["operation"], "renamed")
        self.assertEqual(by_target["renamed.txt"]["source_path"], "rename.txt")
        self.assertEqual(by_target["copied.txt"]["operation"], "copied")
        self.assertEqual(by_target["copied.txt"]["source_path"], "keep.txt")
        self.assertEqual(by_target["modify.txt"]["additions"], 1)
        self.assertEqual(by_target["modify.txt"]["deletions"], 1)
        self.assertEqual(
            (
                by_target["binary.bin"]["additions"],
                by_target["binary.bin"]["deletions"],
                by_target["binary.bin"]["binary"],
            ),
            (None, None, True),
        )

    def test_observes_reviewer_visible_merge_base_diff_when_base_tip_diverged(
        self,
    ) -> None:
        self.git("switch", "-qc", "diverged-base", self.base)
        self.write("base-only.txt", b"base-side change\n")
        self.git("add", "base-only.txt")
        self.git("commit", "-qm", "base tip")
        diverged_base = self.git("rev-parse", "HEAD").stdout.strip()

        rows = MODULE.observe_git_diff(
            self.repository,
            base_oid=diverged_base,
            head_oid=self.head,
        )

        self.assertNotIn("base-only.txt", {row["target_path"] for row in rows})
        self.assertEqual(
            {row["target_path"] for row in rows},
            {
                "added.txt",
                "binary.bin",
                "copied.txt",
                "delete.txt",
                "modify.txt",
                "renamed.txt",
            },
        )

    def test_fails_closed_without_one_unique_merge_base(self) -> None:
        for output in (b"", b"a" * 40 + b"\n" + b"b" * 40 + b"\n"):
            with self.subTest(output=output):
                with mock.patch.object(MODULE, "_run", return_value=output):
                    with self.assertRaisesRegex(
                        MODULE.GitObservationError,
                        "one unique merge base",
                    ):
                        MODULE._review_diff_base(
                            self.repository,
                            self.base,
                            self.head,
                        )

    def test_fails_closed_for_missing_objects_wrong_root_and_dirty_ambiguity(
        self,
    ) -> None:
        with self.assertRaises(MODULE.GitObservationError):
            MODULE.observe_git_diff(
                self.repository, base_oid="f" * 40, head_oid=self.head
            )
        subdirectory = self.repository / "subdirectory"
        subdirectory.mkdir()
        with self.assertRaisesRegex(MODULE.GitObservationError, "exact worktree root"):
            MODULE.observe_git_diff(
                subdirectory, base_oid=self.base, head_oid=self.head
            )
        subdirectory.rmdir()
        self.write("dirty.txt", b"dirty\n")
        with self.assertRaisesRegex(
            MODULE.GitObservationError, "dirty-state ambiguity"
        ):
            MODULE.observe_git_diff(
                self.repository, base_oid=self.base, head_oid=self.head
            )
