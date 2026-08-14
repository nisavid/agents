from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[4]
SKILL_DIR = (
    REPOSITORY / "plugins/versionkeeping/skills/checkpointing-and-publishing-git-work"
)
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))

import git_publication.adapter as publication_adapter  # noqa: E402
from git_publication.adapter import MalformedRequest  # noqa: E402
from git_publication.deletion import (  # noqa: E402
    MalformedDeletionPlan,
    ReviewedDeletionPlanDigestMismatch,
    execute_remote_ref_deletion,
    plan_remote_ref_deletion,
)
from git_publication.execution import validate_ready_plan  # noqa: E402


def git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def ref_exists(repo: Path, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", ref],
            cwd=repo,
            check=False,
        ).returncode
        == 0
    )


def deletion_request(expected_target_sha: str, **overrides: object) -> dict:
    value = {
        "schema_version": 1,
        "remote": "publish",
        "ref": "refs/heads/topic",
        "expected_target_sha": expected_target_sha,
        "verified_merge": {
            "status": "verified",
            "merged_source_sha": expected_target_sha,
            "merged_result_sha": "f" * 40,
        },
        "authorization": {
            "repository": {
                "authorized": True,
                "remote": "publish",
                "ref": "refs/heads/topic",
                "expected_target_sha": expected_target_sha,
            },
            "operator": {
                "authorized": True,
                "remote": "publish",
                "ref": "refs/heads/topic",
                "expected_target_sha": expected_target_sha,
            },
        },
    }
    value.update(overrides)
    return value


def reviewed_plan(plan: dict) -> tuple[bytes, str]:
    plan_bytes = (
        json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    return plan_bytes, "sha256:" + hashlib.sha256(plan_bytes).hexdigest()


def execute(repo: Path, plan: dict) -> dict:
    plan_bytes, digest = reviewed_plan(plan)
    return execute_remote_ref_deletion(repo, plan_bytes, digest)


class RemoteRefDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        git(self.root, "init", "--bare", str(self.remote))
        git(self.root, "init", "-b", "topic", str(self.repo))
        self.target = commit(self.repo, "topic")
        git(self.repo, "remote", "add", "publish", str(self.remote))
        git(self.repo, "push", "publish", f"{self.target}:refs/heads/topic")
        self.plan = plan_remote_ref_deletion(self.repo, deletion_request(self.target))
        self.assertEqual(self.plan["status"], "ready")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_deletes_exact_leased_ref_and_verifies_absence(self) -> None:
        result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["deletion_attempted"])
        self.assertFalse(ref_exists(self.remote, "refs/heads/topic"))

    def test_sha256_repository_binds_deletion_ids_and_null_width(self) -> None:
        remote = self.root / "sha256-remote.git"
        repo = self.root / "sha256-repo"
        git(self.root, "init", "--bare", "--object-format=sha256", str(remote))
        git(self.root, "init", "-b", "topic", "--object-format=sha256", str(repo))
        target = commit(repo, "topic")
        git(repo, "remote", "add", "publish", str(remote))
        git(repo, "push", "publish", f"{target}:refs/heads/topic")
        request = deletion_request(target)
        request["verified_merge"]["merged_result_sha"] = "f" * 64

        plan = plan_remote_ref_deletion(repo, request)
        result = execute(repo, plan)

        self.assertEqual(len(target), 64)
        self.assertEqual(result["status"], "verified")
        self.assertFalse(ref_exists(remote, "refs/heads/topic"))

    def test_absent_ref_is_terminal_verified_without_deletion_attempt(self) -> None:
        git(self.repo, "push", "publish", ":refs/heads/topic")

        plan = plan_remote_ref_deletion(self.repo, deletion_request(self.target))

        self.assertEqual(plan["status"], "verified")
        self.assertIsNone(plan["deletion"])
        self.assertEqual(plan["postchecks"][0]["kind"], "remote_ref_absent")

    def test_wrong_expected_sha_blocks_without_deleting(self) -> None:
        plan = plan_remote_ref_deletion(self.repo, deletion_request("a" * 40))

        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["reasons"][0]["code"], "EXPECTED_REMOTE_REF_MOVED")
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.target)

    def test_missing_exact_operator_authorization_blocks_planning(self) -> None:
        request = deletion_request(self.target)
        request["authorization"]["operator"]["expected_target_sha"] = "a" * 40

        with self.assertRaises(MalformedRequest):
            plan_remote_ref_deletion(self.repo, request)

        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.target)

    def test_endpoint_and_config_drift_block_before_deletion(self) -> None:
        changed = self.root / "changed.git"
        git(self.root, "init", "--bare", str(changed))
        git(self.repo, "remote", "set-url", "--push", "publish", str(changed))

        endpoint_result = execute(self.repo, self.plan)

        self.assertEqual(endpoint_result["status"], "blocked")
        self.assertFalse(endpoint_result["deletion_attempted"])
        self.assertEqual(
            endpoint_result["reasons"][0]["code"],
            "REVIEWED_DELETION_ENDPOINT_CHANGED",
        )

        git(self.repo, "remote", "set-url", "--push", "publish", str(self.remote))
        git(self.repo, "config", "remote.publish.push", "refs/heads/topic")
        config_plan = plan_remote_ref_deletion(self.repo, deletion_request(self.target))
        git(
            self.repo,
            "config",
            "remote.publish.push",
            "refs/heads/topic:refs/heads/other",
        )

        config_result = execute(self.repo, config_plan)

        self.assertEqual(config_result["status"], "blocked")
        self.assertFalse(config_result["deletion_attempted"])
        self.assertEqual(
            config_result["reasons"][0]["code"], "REVIEWED_DELETION_CONFIG_CHANGED"
        )
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.target)

    def test_tampered_reviewed_plan_bytes_block_before_parsing_or_deletion(
        self,
    ) -> None:
        plan_bytes, digest = reviewed_plan(self.plan)
        tampered = plan_bytes.replace(b"refs/heads/topic", b"refs/heads/other")

        with self.assertRaises(ReviewedDeletionPlanDigestMismatch):
            execute_remote_ref_deletion(self.repo, tampered, digest)

        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.target)

    def test_structurally_malformed_reviewed_plan_blocks_before_deletion(self) -> None:
        malformed = json.loads(json.dumps(self.plan))
        malformed["deletion"]["refspec"] = ":refs/heads/other"
        plan_bytes, digest = reviewed_plan(malformed)

        with self.assertRaises(MalformedDeletionPlan):
            execute_remote_ref_deletion(self.repo, plan_bytes, digest)

        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.target)

    def test_timeout_after_deletion_actuation_is_unknown_without_retry(self) -> None:
        real_run = subprocess.run
        deletion_calls = 0

        def timeout_delete(*args, **kwargs):
            nonlocal deletion_calls
            command = args[0] if args else kwargs.get("args")
            if (
                command
                and command[0] == "git"
                and "push" in command
                and ":refs/heads/topic" in command
            ):
                deletion_calls += 1
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return real_run(*args, **kwargs)

        with mock.patch.object(publication_adapter.subprocess, "run", timeout_delete):
            result = execute(self.repo, self.plan)

        self.assertEqual(deletion_calls, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["deletion_attempted"])
        self.assertEqual(result["reasons"][0]["code"], "POST_DELETION_STATE_UNKNOWN")

    def test_deletion_transport_scrubs_hooks_tls_and_hostile_environment(
        self,
    ) -> None:
        real_run = subprocess.run
        observed = []

        def recording_run(*args, **kwargs):
            command = args[0] if args else kwargs.get("args")
            if command and command[0] == "git" and kwargs.get("cwd") == str(self.repo):
                observed.append((command, kwargs["env"]))
            return real_run(*args, **kwargs)

        hostile = {
            "GIT_DIR": "/hostile/git-dir",
            "GIT_INDEX_FILE": "/hostile/index",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "remote.publish.url",
            "GIT_CONFIG_VALUE_0": "https://attacker.invalid/repo",
            "GIT_SSL_NO_VERIFY": "true",
            "GIT_SSL_CAINFO": "/hostile/ca.pem",
            "GIT_ASKPASS": "/hostile/askpass",
            "GIT_SSH_COMMAND": "ssh -F /hostile/config",
        }
        with mock.patch.dict(os.environ, hostile, clear=False):
            with mock.patch.object(
                publication_adapter.subprocess,
                "run",
                recording_run,
            ):
                result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "verified")
        deletion_commands = [
            item
            for item in observed
            if "push" in item[0] and ":refs/heads/topic" in item[0]
        ]
        self.assertEqual(len(deletion_commands), 1)
        command, env = deletion_commands[0]
        self.assertIn("http.sslVerify=true", command)
        self.assertTrue(any(item.startswith("core.hooksPath=") for item in command))
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_INDEX_FILE", env)
        self.assertNotIn("GIT_CONFIG_COUNT", env)
        self.assertNotIn("GIT_SSL_NO_VERIFY", env)
        self.assertEqual(env["GIT_ASKPASS"], "false")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])

    def test_normal_publication_executor_rejects_deletion_refspec(self) -> None:
        normal_plan = {
            "schema_version": 1,
            "status": "ready",
            "request": {
                "schema_version": 2,
                "start_head": self.target,
                "source_sha": self.target,
                "task_owned_commits": [],
                "adopted_commits": [],
                "removal_authorized_commits": [],
                "explicit_destination": {
                    "remote": "publish",
                    "ref": "refs/heads/topic",
                },
                "default_branch_policy": None,
                "allow_create": False,
                "creation_base_ref": None,
            },
            "planner_effects": {
                "local_mutation_possible": True,
                "bounded_object_fetch": True,
                "objects_may_persist": True,
                "temporary_ref_namespace": "refs/versionkeeping/publication/",
                "fetch_head_preserved": True,
            },
            "reasons": [],
            "source_sha": self.target,
            "destination": {
                "remote": "publish",
                "ref": "refs/heads/topic",
                "endpoint_fingerprint": "sha256:" + "a" * 64,
                "config_digest": "sha256:" + "b" * 64,
                "default_branch_ref": "refs/heads/main",
            },
            "target": {"present": True, "sha": self.target},
            "outgoing_shas": [],
            "target_only_shas": [],
            "fast_forward": True,
            "rewrite_required": False,
            "push": {
                "source_sha": self.target,
                "ref": "refs/heads/topic",
                "refspec": ":refs/heads/topic",
                "lease": f"--force-with-lease=refs/heads/topic:{self.target}",
                "options": ["--no-follow-tags", "--recurse-submodules=check"],
                "config_overrides": {
                    "push.followTags": "false",
                    "push.recurseSubmodules": "check",
                },
                "expected_target": {"present": True, "sha": self.target},
            },
            "postchecks": [
                {
                    "kind": "remote_ref_equals",
                    "endpoint_fingerprint": "sha256:" + "a" * 64,
                    "ref": "refs/heads/topic",
                    "sha": self.target,
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "push refspec drift"):
            validate_ready_plan(normal_plan)

        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.target)


if __name__ == "__main__":
    unittest.main()
