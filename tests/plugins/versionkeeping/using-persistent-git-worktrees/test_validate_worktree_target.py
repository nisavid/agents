from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[4]
SKILL_ROOT = REPOSITORY / "plugins/versionkeeping/skills/using-persistent-git-worktrees"
VALIDATOR = SKILL_ROOT / "scripts" / "validate_worktree_target.py"


def load_validator_module():
    specification = importlib.util.spec_from_file_location("worktree_target", VALIDATOR)
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


class ValidateWorktreeTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.clone = self.root / "project"
        self.clone.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_validator(
        self,
        *,
        name: str = "feature",
        branch: str = "topic/feature",
        create: bool = False,
        existing_branch: bool = False,
    ):
        arguments = [
            sys.executable,
            str(VALIDATOR),
            "--main-clone",
            str(self.clone),
            "--name",
            name,
            "--branch",
            branch,
        ]
        if create:
            arguments.append("--create")
        if existing_branch:
            arguments.append("--existing-branch")
        return subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_returns_exact_sibling_target(self) -> None:
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "valid")
        self.assertEqual(payload["worktree_root"], str(self.root / "project.wt"))
        self.assertEqual(
            payload["worktree_path"], str(self.root / "project.wt" / "feature")
        )
        self.assertEqual(payload["branch"], "topic/feature")

    def test_rejects_absolute_traversal_and_option_like_names(self) -> None:
        for name in ("/tmp/feature", "../feature", "nested/feature", "-feature"):
            with self.subTest(name=name):
                result = self.run_validator(name=name)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["status"], "blocked")

    def test_rejects_unsafe_branch_refs(self) -> None:
        for branch in (
            "/topic",
            "../topic",
            "-topic",
            "topic..other",
            "topic@{1}",
            "topic.lock",
            ".hidden/topic",
            "topic//other",
        ):
            with self.subTest(branch=branch):
                result = self.run_validator(branch=branch)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["status"], "blocked")

    def test_rejects_symlinked_clone_and_sibling_root(self) -> None:
        real_clone = self.root / "real-project"
        self.clone.rename(real_clone)
        self.clone.symlink_to(real_clone, target_is_directory=True)
        self.assertEqual(self.run_validator().returncode, 2)

        self.clone.unlink()
        real_clone.rename(self.clone)
        external = self.root / "external"
        external.mkdir()
        (self.root / "project.wt").symlink_to(external, target_is_directory=True)
        self.assertEqual(self.run_validator().returncode, 2)

    def test_rejects_symlinked_target_outside_sibling_root(self) -> None:
        sibling_root = self.root / "project.wt"
        sibling_root.mkdir()
        external = self.root / "external"
        external.mkdir()
        (sibling_root / "feature").symlink_to(external, target_is_directory=True)
        result = self.run_validator()
        self.assertEqual(result.returncode, 2)
        self.assertIn("symlink", json.loads(result.stdout)["reason"])

    def test_creation_rejects_root_replacement_after_receipt(self) -> None:
        module = load_validator_module()
        receipt = module.validated_target(self.clone, "feature", "topic/feature")
        sibling_root = self.root / "project.wt"
        sibling_root.mkdir(mode=0o700)
        external = self.root / "external"
        external.mkdir()
        sibling_root.rmdir()
        sibling_root.symlink_to(external, target_is_directory=True)
        calls = []

        def runner(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("Git must not run for a replaced root")

        with self.assertRaises(module.InvalidTarget):
            module.create_worktree(receipt, runner=runner)
        self.assertEqual(calls, [])

    def test_creation_rejects_boolean_or_float_receipt_schema(self) -> None:
        module = load_validator_module()
        for malformed in (True, 1.0):
            with self.subTest(malformed=malformed):
                receipt = module.validated_target(
                    self.clone, f"feature-{malformed}", "topic/feature"
                )
                receipt["schema_version"] = malformed
                with self.assertRaisesRegex(
                    module.InvalidTarget, "receipt schema is invalid"
                ):
                    module.create_worktree(receipt)

    def test_creation_uses_one_guarded_git_flow_and_verifies_result(self) -> None:
        module = load_validator_module()
        receipt = module.validated_target(self.clone, "feature", "topic/feature")
        target = self.root / "project.wt" / "feature"
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            if "show-ref" in command:
                return subprocess.CompletedProcess(command, 1, "", "")
            if "worktree" in command and "add" in command:
                target.mkdir()
                return subprocess.CompletedProcess(command, 0, "created\n", "")
            if command[-2:] == ["list", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command, 0, f"worktree {target}\nHEAD deadbeef\n", ""
                )
            if "symbolic-ref" in command:
                return subprocess.CompletedProcess(command, 0, "topic/feature\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.dict(os.environ, {"GH_TOKEN": "secret"}):
            result = module.create_worktree(receipt, runner=runner)
        self.assertEqual(result["status"], "created")
        commands = [command for command, _ in calls]
        add_command = next(command for command in commands if "add" in command)
        self.assertEqual(
            add_command[3:],
            [
                "-C",
                str(self.clone),
                "worktree",
                "add",
                "-b",
                "topic/feature",
                "--",
                str(target),
            ],
        )
        self.assertTrue(add_command[2].startswith("core.hooksPath="))
        self.assertEqual(len(commands), 5)
        for _, options in calls:
            self.assertIs(options["stdin"], subprocess.DEVNULL)
            self.assertEqual(options["timeout"], module.GIT_TIMEOUT_SECONDS)
            self.assertEqual(options["env"]["GIT_TERMINAL_PROMPT"], "0")
            self.assertNotIn("GH_TOKEN", options["env"])

    def test_failed_or_timed_out_mutation_is_unknown_without_a_target(self) -> None:
        module = load_validator_module()

        for outcome in ("failed", "timeout"):
            with self.subTest(outcome=outcome):
                receipt = module.validated_target(
                    self.clone, f"feature-{outcome}", f"topic/{outcome}"
                )

                def runner(command, **kwargs):
                    if "show-ref" in command:
                        return subprocess.CompletedProcess(command, 1, "", "")
                    if outcome == "timeout":
                        raise subprocess.TimeoutExpired(
                            command, module.GIT_TIMEOUT_SECONDS
                        )
                    return subprocess.CompletedProcess(command, 1, "", "failed")

                result = module.create_worktree(receipt, runner=runner)
                self.assertEqual(result["status"], "unknown")
                self.assertFalse(Path(receipt["worktree_path"]).exists())

    def test_existing_branch_requires_and_verifies_exact_local_branch(self) -> None:
        module = load_validator_module()
        receipt = module.validated_target(self.clone, "feature", "topic/existing")
        target = Path(receipt["worktree_path"])
        commands = []

        def runner(command, **kwargs):
            commands.append(command)
            if "show-ref" in command:
                return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
            if "worktree" in command and "add" in command:
                target.mkdir()
                return subprocess.CompletedProcess(command, 0, "created\n", "")
            if command[-2:] == ["list", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command, 0, f"worktree {target}\nHEAD {'a' * 40}\n", ""
                )
            if "symbolic-ref" in command:
                return subprocess.CompletedProcess(command, 0, "topic/existing\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        result = module.create_worktree(receipt, existing_branch=True, runner=runner)
        self.assertEqual(result["status"], "created")
        add_command = next(command for command in commands if "add" in command)
        self.assertNotIn("-b", add_command)

        absent_receipt = module.validated_target(self.clone, "other", "topic/tag-only")

        def absent_runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "")

        with self.assertRaises(module.InvalidTarget):
            module.create_worktree(
                absent_receipt, existing_branch=True, runner=absent_runner
            )

    def test_real_creation_suppresses_ambient_post_checkout_hook(self) -> None:
        subprocess.run(
            ["git", "init", str(self.clone)], check=True, capture_output=True
        )
        tracked = self.clone / "tracked.txt"
        tracked.write_text("tracked\n")
        subprocess.run(
            ["git", "-C", str(self.clone), "add", "tracked.txt"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.clone),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "test",
            ],
            check=True,
            capture_output=True,
        )
        marker = self.root / "hook-ran"
        hook = self.clone / ".git/hooks/post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        os.chmod(hook, 0o755)

        result = self.run_validator(create=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "created")
        self.assertFalse(marker.exists())
        self.assertTrue((self.root / "project.wt/feature").is_dir())

    def test_creation_rejects_insecure_sibling_parent(self) -> None:
        module = load_validator_module()
        receipt = module.validated_target(self.clone, "feature", "topic/feature")
        self.root.chmod(0o777)
        try:
            with self.assertRaises(module.InvalidTarget):
                module.create_worktree(receipt)
        finally:
            self.root.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
