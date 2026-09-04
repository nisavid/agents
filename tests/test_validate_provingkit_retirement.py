"""Tests for the repository's Provingkit source-retirement boundary."""

import os
import py_compile
import shutil
import socket
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
    def run_validator(
        self,
        repository: Path,
        *,
        environment_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        if environment_overrides:
            environment.update(environment_overrides)
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(repository)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
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

    def initialize_git_index(self, repository: Path) -> None:
        subprocess.run(
            ["git", "init", "--quiet", str(repository)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "add", "."],
            check=True,
            capture_output=True,
            text=True,
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
            (
                "scripts/__pycache__/legacy_release.py",
                "unexpected active root script remains",
            ),
            ("tests/test_legacy_release.py", "unexpected active root test remains"),
            (
                "tests/__pycache__/test_legacy_release.py",
                "unexpected active root test remains",
            ),
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                repository = Path(directory)
                self.prepare_valid_fixture(repository)
                unexpected = repository / relative
                unexpected.parent.mkdir(parents=True, exist_ok=True)
                unexpected.write_text("# retired route\n", encoding="utf-8")

                completed = self.run_validator(repository)

                self.assertEqual(completed.returncode, 1)
                self.assertIn(expected_error, completed.stderr)

    def test_sourceless_bytecode_and_pyc_aliases_are_rejected(self) -> None:
        for relative, expected_error in (
            ("scripts/legacy_release.pyc", "unexpected active root script remains"),
            ("tests/test_legacy_release.pyc", "unexpected active root test remains"),
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                repository = Path(directory)
                self.prepare_valid_fixture(repository)
                source = repository / "legacy_release.py"
                source.write_text("print('active legacy bytecode')\n", encoding="utf-8")
                bytecode = repository / relative
                py_compile.compile(str(source), cfile=str(bytecode), doraise=True)
                source.unlink()

                completed = self.run_validator(repository)

                self.assertEqual(completed.returncode, 1)
                self.assertIn(expected_error, completed.stderr)

        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.prepare_valid_fixture(repository)
            target = repository / "legacy-payload"
            target.mkdir()
            (target / "legacy_release.py").write_text(
                "# retired route\n", encoding="utf-8"
            )
            (repository / "scripts/legacy_alias.pyc").symlink_to(
                target, target_is_directory=True
            )

            completed = self.run_validator(repository)

            self.assertEqual(completed.returncode, 1)
            self.assertIn("unexpected active root script remains", completed.stderr)

    def test_only_untracked_bytecode_cache_artifacts_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.prepare_valid_fixture(repository)
            self.initialize_git_index(repository)
            source = repository / "tests/test_validate_provingkit_retirement.py"
            cache = source.parent / "__pycache__" / (
                f"{source.stem}.{sys.implementation.cache_tag}.pyc"
            )
            cache.parent.mkdir()
            py_compile.compile(str(source), cfile=str(cache), doraise=True)

            completed = self.run_validator(repository)

            self.assertEqual(completed.returncode, 0, completed.stderr)

            subprocess.run(
                ["git", "-C", str(repository), "add", "--force", str(cache)],
                check=True,
                capture_output=True,
                text=True,
            )

            completed = self.run_validator(repository)

            self.assertEqual(completed.returncode, 1)
            self.assertIn("unexpected active root test remains", completed.stderr)

    def test_repo_relative_bytecode_cache_artifact_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.prepare_valid_fixture(repository)
            self.initialize_git_index(repository)
            source = Path("tests/test_validate_provingkit_retirement.py")
            environment = os.environ.copy()
            environment.pop("PYTHONPYCACHEPREFIX", None)
            subprocess.run(
                [sys.executable, "-m", "py_compile", source.as_posix()],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

            completed = self.run_validator(repository)

            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_non_bytecode_cache_impostors_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.prepare_valid_fixture(repository)
            self.initialize_git_index(repository)
            impostor = repository / "scripts/__pycache__" / (
                f"{VALIDATOR.stem}.{sys.implementation.cache_tag}.pyc"
            )
            impostor.parent.mkdir()
            impostor.write_text("# not Python bytecode\n", encoding="utf-8")

            completed = self.run_validator(repository)

            self.assertEqual(completed.returncode, 1)
            self.assertIn("unexpected active root script remains", completed.stderr)

    def test_non_regular_execution_entries_are_rejected(self) -> None:
        cache_name = f"{VALIDATOR.stem}.{sys.implementation.cache_tag}.pyc"
        for entry_kind, relative in (
            ("fifo", Path("scripts/legacy_release.py")),
            ("fifo", Path("scripts/__pycache__") / cache_name),
            ("socket", Path("scripts/__pycache__") / cache_name),
            ("directory", Path("scripts/__pycache__") / cache_name),
        ):
            with (
                self.subTest(entry_kind=entry_kind, relative=relative),
                tempfile.TemporaryDirectory() as directory,
            ):
                repository = Path(directory)
                self.prepare_valid_fixture(repository)
                self.initialize_git_index(repository)
                entry = repository / relative
                entry.parent.mkdir(parents=True, exist_ok=True)
                unix_socket = None
                if entry_kind == "fifo":
                    os.mkfifo(entry)
                elif entry_kind == "socket":
                    unix_socket = socket.socket(socket.AF_UNIX)
                    unix_socket.bind(str(entry))
                else:
                    entry.mkdir()
                try:
                    completed = self.run_validator(repository)
                finally:
                    if unix_socket is not None:
                        unix_socket.close()

                self.assertEqual(completed.returncode, 1)
                self.assertIn("unexpected active root script remains", completed.stderr)

    def test_allowed_execution_leaves_must_be_real_files(self) -> None:
        for relative in (
            Path("scripts/validate_provingkit_retirement.py"),
            Path("tests/test_validate_provingkit_retirement.py"),
        ):
            for entry_kind in (
                "directory",
                "fifo",
                "socket",
                "file-symlink",
                "directory-symlink",
            ):
                with (
                    self.subTest(relative=relative, entry_kind=entry_kind),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    repository = Path(directory)
                    self.prepare_valid_fixture(repository)
                    self.initialize_git_index(repository)
                    entry = repository / relative
                    entry.unlink()
                    unix_socket = None
                    if entry_kind == "directory":
                        entry.mkdir()
                    elif entry_kind == "fifo":
                        os.mkfifo(entry)
                    elif entry_kind == "socket":
                        unix_socket = socket.socket(socket.AF_UNIX)
                        unix_socket.bind(str(entry))
                    elif entry_kind == "file-symlink":
                        entry.symlink_to("../README.md")
                    else:
                        target = repository / "replacement-directory"
                        target.mkdir()
                        (target / "legacy.py").write_text(
                            "# active source\n", encoding="utf-8"
                        )
                        entry.symlink_to(target, target_is_directory=True)
                    if "symlink" in entry_kind:
                        subprocess.run(
                            ["git", "-C", str(repository), "add", "--all"],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                    try:
                        completed = self.run_validator(repository)
                    finally:
                        if unix_socket is not None:
                            unix_socket.close()

                    self.assertEqual(completed.returncode, 1)
                    self.assertIn(
                        f"allowed root execution path must be a real file: {relative}",
                        completed.stderr,
                    )

    def test_allowed_execution_leaves_must_be_tracked(self) -> None:
        for relative in (
            Path("scripts/validate_provingkit_retirement.py"),
            Path("tests/test_validate_provingkit_retirement.py"),
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                repository = Path(directory)
                self.prepare_valid_fixture(repository)
                self.initialize_git_index(repository)
                subprocess.run(
                    ["git", "-C", str(repository), "rm", "--cached", str(relative)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                completed = self.run_validator(repository)

                self.assertEqual(completed.returncode, 1)
                self.assertIn(
                    f"allowed root execution path must be tracked: {relative}",
                    completed.stderr,
                )

    def test_unreadable_cache_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.prepare_valid_fixture(repository)
            self.initialize_git_index(repository)
            cache_directory = repository / "scripts/__pycache__"
            cache_directory.mkdir()
            (cache_directory / "legacy_release.py").write_text(
                "# hidden active source\n", encoding="utf-8"
            )
            cache_directory.chmod(0)
            try:
                completed = self.run_validator(repository)
            finally:
                cache_directory.chmod(0o700)

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stdout, "")

    def test_tracked_gitlink_cache_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.prepare_valid_fixture(repository)
            self.initialize_git_index(repository)
            tree = subprocess.run(
                ["git", "-C", str(repository), "write-tree"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            commit = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit-tree",
                    tree,
                    "-m",
                    "fixture",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            cache_directory = repository / "scripts/__pycache__"
            cache_directory.mkdir()
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    "160000",
                    commit,
                    "scripts/__pycache__",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            completed = self.run_validator(repository)

            self.assertEqual(completed.returncode, 1)
            self.assertIn("unexpected active root script remains", completed.stderr)

    def test_git_environment_cannot_hide_tracked_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            self.prepare_valid_fixture(repository)
            self.initialize_git_index(repository)
            source = repository / "tests/test_validate_provingkit_retirement.py"
            cache = source.parent / "__pycache__" / (
                f"{source.stem}.{sys.implementation.cache_tag}.pyc"
            )
            cache.parent.mkdir()
            py_compile.compile(str(source), cfile=str(cache), doraise=True)
            subprocess.run(
                ["git", "-C", str(repository), "add", "--force", str(cache)],
                check=True,
                capture_output=True,
                text=True,
            )
            alternate_index = repository / "alternate-index"
            alternate_environment = os.environ.copy()
            alternate_environment["GIT_INDEX_FILE"] = str(alternate_index)
            subprocess.run(
                ["git", "-C", str(repository), "read-tree", "--empty"],
                check=True,
                capture_output=True,
                text=True,
                env=alternate_environment,
            )

            completed = self.run_validator(
                repository,
                environment_overrides={"GIT_INDEX_FILE": str(alternate_index)},
            )

            self.assertEqual(completed.returncode, 1)
            self.assertIn("unexpected active root test remains", completed.stderr)

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
