from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from refresh_transaction import (  # noqa: E402
    RefreshTransactionError,
    replace_generated_artifacts,
)


class RefreshTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "repository"
        self.root.mkdir()
        self.destination = self.root / "generated.json"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def replace(self, content: bytes = b"replacement\n") -> None:
        replace_generated_artifacts(
            self.root,
            {self.destination: (content, 0o640)},
            recheck=lambda _temporary_paths: None,
            verify=lambda: None,
        )

    def test_rejects_hard_linked_leaf_before_changing_either_name(self) -> None:
        self.destination.write_bytes(b"original\n")
        outside_alias = self.root.parent / "outside-alias.json"
        try:
            os.link(self.destination, outside_alias)
        except OSError as error:
            self.skipTest(f"hard-link creation is unavailable: {error}")

        with self.assertRaisesRegex(RefreshTransactionError, "hard-linked"):
            self.replace()

        self.assertEqual(self.destination.read_bytes(), b"original\n")
        self.assertEqual(outside_alias.read_bytes(), b"original\n")

    def test_rejects_parent_drift_before_replacing_an_external_leaf(self) -> None:
        generated = self.root / "generated"
        generated.mkdir()
        self.destination = generated / "artifact.json"
        self.destination.write_bytes(b"original\n")
        self.destination.chmod(0o640)
        external = self.root.parent / "outside"
        external.mkdir()
        external_leaf = external / self.destination.name
        external_leaf.write_bytes(b"original\n")
        external_leaf.chmod(0o640)
        displaced = self.root / "displaced-generated"

        def replace_parent(temporary_paths: frozenset[Path]) -> None:
            generated.rename(displaced)
            generated.symlink_to(external, target_is_directory=True)
            temporary = next(iter(temporary_paths))
            external_temporary = external / temporary.name
            external_temporary.write_bytes(b"replacement\n")
            external_temporary.chmod(0o640)

        with self.assertRaisesRegex(RefreshTransactionError, "parent.*changed"):
            replace_generated_artifacts(
                self.root,
                {self.destination: (b"replacement\n", 0o640)},
                recheck=replace_parent,
                verify=lambda: None,
            )

        self.assertEqual(external_leaf.read_bytes(), b"original\n")

    def test_rejects_a_staged_symlink_swap_and_restores_the_leaf(self) -> None:
        self.destination.write_bytes(b"original\n")
        self.destination.chmod(0o640)
        external = self.root.parent / "outside.json"
        external.write_bytes(b"outside\n")

        def replace_staged_file(temporary_paths: frozenset[Path]) -> None:
            temporary = next(iter(temporary_paths))
            temporary.unlink()
            temporary.symlink_to(external)

        with self.assertRaisesRegex(
            RefreshTransactionError,
            "staged|staging",
        ):
            replace_generated_artifacts(
                self.root,
                {self.destination: (b"replacement\n", 0o640)},
                recheck=replace_staged_file,
                verify=lambda: None,
            )

        self.assertFalse(self.destination.is_symlink())
        self.assertEqual(self.destination.read_bytes(), b"original\n")
        self.assertEqual(external.read_bytes(), b"outside\n")

    def test_rejects_a_hard_link_added_to_the_staged_file(self) -> None:
        self.destination.write_bytes(b"original\n")
        self.destination.chmod(0o640)
        outside_alias = self.root.parent / "staged-alias.json"

        def hard_link_staged_file(temporary_paths: frozenset[Path]) -> None:
            try:
                os.link(next(iter(temporary_paths)), outside_alias)
            except OSError as error:
                self.skipTest(f"hard-link creation is unavailable: {error}")

        with self.assertRaisesRegex(RefreshTransactionError, "staged"):
            replace_generated_artifacts(
                self.root,
                {self.destination: (b"replacement\n", 0o640)},
                recheck=hard_link_staged_file,
                verify=lambda: None,
            )

        self.assertEqual(self.destination.read_bytes(), b"original\n")
        self.assertEqual(outside_alias.read_bytes(), b"replacement\n")

    def test_rejects_a_special_file_ancestor_before_staging(self) -> None:
        ancestor = self.root / "generated"
        try:
            os.mkfifo(ancestor)
        except OSError as error:
            self.skipTest(f"special-file creation is unavailable: {error}")
        self.destination = ancestor / "artifact.json"

        with self.assertRaisesRegex(RefreshTransactionError, "parent is invalid"):
            self.replace()

        self.assertEqual(tuple(self.root.iterdir()), (ancestor,))

    def test_relative_root_replaces_regular_leaf_atomically_with_its_mode(self) -> None:
        self.destination.write_bytes(b"original\n")
        self.destination.chmod(0o640)
        original_inode = self.destination.stat().st_ino
        observed_temporaries: list[Path] = []

        def recheck(temporary_paths: frozenset[Path]) -> None:
            self.assertEqual(len(temporary_paths), 1)
            temporary = next(iter(temporary_paths))
            self.assertTrue(temporary.parent.samefile(self.destination.parent))
            self.assertTrue(temporary.is_file())
            observed_temporaries.append(temporary)

        def verify() -> None:
            self.assertEqual(self.destination.read_bytes(), b"replacement\n")

        original_directory = Path.cwd()
        os.chdir(self.root.parent)
        try:
            replace_generated_artifacts(
                Path(self.root.name),
                {Path(self.root.name) / self.destination.name: (b"replacement\n", 0o640)},
                recheck=recheck,
                verify=verify,
            )
        finally:
            os.chdir(original_directory)

        self.assertNotEqual(self.destination.stat().st_ino, original_inode)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.destination.read_bytes(), b"replacement\n")
        self.assertTrue(observed_temporaries)
        self.assertFalse(any(path.exists() for path in observed_temporaries))

    def test_rejects_leaf_symlink_without_changing_its_external_target(self) -> None:
        external = self.root.parent / "outside.json"
        external.write_bytes(b"outside\n")
        try:
            self.destination.symlink_to(external)
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable: {error}")

        with self.assertRaisesRegex(RefreshTransactionError, "not a regular file"):
            self.replace()

        self.assertTrue(self.destination.is_symlink())
        self.assertEqual(external.read_bytes(), b"outside\n")

    def test_rejects_special_leaf_before_staging(self) -> None:
        try:
            os.mkfifo(self.destination)
        except OSError as error:
            self.skipTest(f"special-file creation is unavailable: {error}")

        with self.assertRaisesRegex(RefreshTransactionError, "not a regular file"):
            self.replace()

        self.assertEqual(tuple(self.root.iterdir()), (self.destination,))

    def test_rejects_repository_escape_without_changing_external_bytes(self) -> None:
        outside = self.root.parent / "outside.json"
        outside.write_bytes(b"outside\n")

        with self.assertRaisesRegex(RefreshTransactionError, "escapes repository"):
            replace_generated_artifacts(
                self.root,
                {outside: (b"replacement\n", 0o640)},
                recheck=lambda _temporary_paths: None,
                verify=lambda: None,
            )

        self.assertEqual(outside.read_bytes(), b"outside\n")

    def test_late_failure_rolls_back_every_replacement_and_mode(self) -> None:
        second = self.root / "second.json"
        originals = {
            self.destination: (b"first original\n", 0o640),
            second: (b"second original\n", 0o600),
        }
        for path, (content, mode) in originals.items():
            path.write_bytes(content)
            path.chmod(mode)

        def fail_verification() -> None:
            raise RuntimeError("late verification failure")

        with self.assertRaisesRegex(RuntimeError, "late verification failure"):
            replace_generated_artifacts(
                self.root,
                {
                    self.destination: (b"first replacement\n", 0o644),
                    second: (b"second replacement\n", 0o644),
                },
                recheck=lambda _temporary_paths: None,
                verify=fail_verification,
            )

        for path, (content, mode) in originals.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mode & 0o777, mode)
        self.assertEqual(set(self.root.iterdir()), set(originals))

    def test_single_target_controls_reject_multiple_replacements(self) -> None:
        second = self.root / "second.json"

        with self.assertRaisesRegex(
            RefreshTransactionError,
            "single generated artifact",
        ):
            replace_generated_artifacts(
                self.root,
                {
                    self.destination: (b"first replacement\n", 0o644),
                    second: (b"second replacement\n", 0o644),
                },
                recheck=lambda _temporary_paths: None,
                verify=lambda: None,
                before_replace=lambda: None,
            )

        self.assertFalse(self.destination.exists())
        self.assertFalse(second.exists())

    def test_disabling_rollback_requires_recovery_callbacks(self) -> None:
        with self.assertRaisesRegex(
            RefreshTransactionError,
            "recovery callbacks",
        ):
            replace_generated_artifacts(
                self.root,
                {self.destination: (b"replacement\n", 0o644)},
                recheck=lambda _temporary_paths: None,
                verify=lambda: None,
                rollback_on_failure=False,
            )

        self.assertFalse(self.destination.exists())

    def test_late_parent_drift_rolls_back_through_retained_directory(self) -> None:
        generated = self.root / "generated"
        generated.mkdir()
        self.destination = generated / "artifact.json"
        self.destination.write_bytes(b"original\n")
        self.destination.chmod(0o600)
        displaced = self.root / "displaced-generated"
        external = self.root.parent / "outside"
        external.mkdir()
        external_leaf = external / self.destination.name
        external_leaf.write_bytes(b"external\n")

        def fail_after_parent_drift() -> None:
            generated.rename(displaced)
            generated.symlink_to(external, target_is_directory=True)
            raise RuntimeError("late verification failure")

        with self.assertRaisesRegex(RuntimeError, "late verification failure"):
            replace_generated_artifacts(
                self.root,
                {self.destination: (b"replacement\n", 0o640)},
                recheck=lambda _temporary_paths: None,
                verify=fail_after_parent_drift,
            )

        restored = displaced / self.destination.name
        self.assertEqual(restored.read_bytes(), b"original\n")
        self.assertEqual(restored.stat().st_mode & 0o777, 0o600)
        self.assertEqual(external_leaf.read_bytes(), b"external\n")


if __name__ == "__main__":
    unittest.main()
