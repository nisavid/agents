"""Tests for the repository's Provingkit source-retirement boundary."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY / "scripts" / "validate_provingkit_retirement.py"

CANONICAL_REPOSITORY = "https://github.com/nisavid/provingkit"

FORBIDDEN_ACTIVE_PATHS = (
    ".claude-plugin/marketplace.json",
    ".github/workflows/source-skill-disposition.yml",
    ".github/workflows/versionkeeping-native-credentials.yml",
    "evals",
    "plugins",
    "release",
)

PRESERVED_OWNER_PATHS = (
    ".scratch/chatgpt-airgap-unlock",
    "tooling/chatgpt-ffs",
    "tooling/codex-ns-proxy",
    "tooling/hindsight",
)


class ProvingkitRetirementTests(unittest.TestCase):
    def run_validator(self, repository: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(repository)],
            check=False,
            capture_output=True,
            text=True,
        )

    def prepare_valid_fixture(self, repository: Path) -> None:
        for relative in PRESERVED_OWNER_PATHS:
            (repository / relative).mkdir(parents=True)

        scripts = repository / "scripts"
        scripts.mkdir()
        shutil.copyfile(VALIDATOR, scripts / VALIDATOR.name)

        tests = repository / "tests"
        tests.mkdir()
        (tests / Path(__file__).name).write_text("# retirement test\n", encoding="utf-8")

        (repository / "docs/superpowers/research").mkdir(parents=True)
        (repository / "docs/superpowers/specs").mkdir(parents=True)
        redirect = repository / "docs/plugin-system/design-principles.md"
        redirect.parent.mkdir(parents=True)
        redirect.write_text(
            f"{CANONICAL_REPOSITORY}/blob/main/"
            "docs/plugin-system/design-principles.md\n",
            encoding="utf-8",
        )
        (repository / "README.md").write_text(
            f"Canonical source: {CANONICAL_REPOSITORY}\n"
            "Base Loadout: https://github.com/nisavid/agents/issues/82\n",
            encoding="utf-8",
        )

    def test_repository_satisfies_retirement_contract(self) -> None:
        completed = self.run_validator(REPOSITORY)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "Provingkit source retirement valid\n")

    def test_each_retired_active_path_is_rejected(self) -> None:
        for relative in FORBIDDEN_ACTIVE_PATHS:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                repository = Path(directory)
                self.prepare_valid_fixture(repository)
                forbidden = repository / relative
                if Path(relative).suffix:
                    forbidden.parent.mkdir(parents=True, exist_ok=True)
                    forbidden.write_text("retired route\n", encoding="utf-8")
                else:
                    forbidden.mkdir(parents=True)

                completed = self.run_validator(repository)

                self.assertEqual(completed.returncode, 1)
                self.assertEqual(completed.stdout, "")
                self.assertIn(
                    f"active Provingkit path remains: {relative}", completed.stderr
                )

    def test_new_root_execution_surfaces_are_rejected(self) -> None:
        for relative, expected_error in (
            ("scripts/legacy_release.py", "unexpected active root script remains"),
            ("tests/test_legacy_release.py", "unexpected active root test remains"),
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                repository = Path(directory)
                self.prepare_valid_fixture(repository)
                unexpected = repository / relative
                unexpected.write_text("# retired route\n", encoding="utf-8")

                completed = self.run_validator(repository)

                self.assertEqual(completed.returncode, 1)
                self.assertIn(expected_error, completed.stderr)

    def test_directory_aliases_are_rejected(self) -> None:
        for relative in ("scripts", "tests", *PRESERVED_OWNER_PATHS):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                repository = Path(directory)
                self.prepare_valid_fixture(repository)
                original = repository / relative
                target = repository / f"aliased-{Path(relative).name}"
                original.rename(target)
                original.symlink_to(target, target_is_directory=True)

                completed = self.run_validator(repository)

                self.assertEqual(completed.returncode, 1)
                self.assertIn(
                    f"path must be a real directory: {relative}", completed.stderr
                )

    def test_directory_alias_ancestors_are_rejected(self) -> None:
        for ancestor, required in (
            (".scratch", ".scratch/chatgpt-airgap-unlock"),
            ("tooling", "tooling/chatgpt-ffs"),
            ("docs", "docs/superpowers/research"),
            ("docs/superpowers", "docs/superpowers/research"),
            ("docs/plugin-system", "docs/plugin-system"),
        ):
            with self.subTest(ancestor=ancestor), tempfile.TemporaryDirectory() as directory:
                repository = Path(directory)
                self.prepare_valid_fixture(repository)
                original = repository / ancestor
                target = repository / f"aliased-{Path(ancestor).name}"
                original.rename(target)
                original.symlink_to(target, target_is_directory=True)

                completed = self.run_validator(repository)

                self.assertEqual(completed.returncode, 1)
                self.assertIn(
                    f"path must be a real directory: {required}", completed.stderr
                )


if __name__ == "__main__":
    unittest.main()
