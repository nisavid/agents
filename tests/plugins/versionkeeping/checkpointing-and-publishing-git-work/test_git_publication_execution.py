from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[4]
SKILL_DIR = (
    REPOSITORY / "plugins/versionkeeping/skills/checkpointing-and-publishing-git-work"
)
SCRIPTS = SKILL_DIR / "scripts"
CLI = SCRIPTS / "execute_git_publication.py"
sys.path.insert(0, str(SCRIPTS))

import git_publication.adapter as adapter  # noqa: E402
import git_publication.execution as execution  # noqa: E402
import execute_git_publication as execution_cli  # noqa: E402
from git_publication.adapter import plan_repository  # noqa: E402
from git_publication.execution import (  # noqa: E402
    MalformedPlan,
    ReviewedPlanDigestMismatch,
    execute_repository,
    load_plan_json,
    validate_ready_plan,
)


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


def request(start: str, source: str) -> dict:
    return {
        "schema_version": 2,
        "start_head": start,
        "source_sha": source,
        "task_owned_commits": [source],
        "adopted_commits": [],
        "removal_authorized_commits": [],
        "explicit_destination": {
            "remote": "publish",
            "ref": "refs/heads/topic",
        },
        "default_branch_policy": None,
        "allow_create": False,
        "creation_base_ref": None,
    }


def reviewed_plan(plan: dict) -> tuple[bytes, str]:
    plan_bytes = (
        json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
    return plan_bytes, digest


def execute(repo: Path, plan: dict) -> dict:
    plan_bytes, digest = reviewed_plan(plan)
    return execute_repository(repo, plan_bytes, digest)


class PublicationExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        git(self.root, "init", "--bare", str(self.remote))
        git(self.root, "init", "-b", "topic", str(self.repo))
        self.start = commit(self.repo, "base")
        git(self.repo, "remote", "add", "publish", str(self.remote))
        git(self.repo, "push", "publish", f"{self.start}:refs/heads/topic")
        git(self.repo, "push", "publish", f"{self.start}:refs/heads/main")
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.source = commit(self.repo, "change")
        self.plan = plan_repository(self.repo, request(self.start, self.source))
        self.assertEqual(self.plan["status"], "ready")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_executes_once_through_captured_endpoint_alias_and_verifies(self) -> None:
        real_run = subprocess.run
        observed = []

        def recording_run(*args, **kwargs):
            command = args[0] if args else kwargs.get("args")
            if (
                command
                and command[0] == str(adapter.TRUSTED_SYSTEM_EXECUTABLES["git"])
                and kwargs.get("cwd") == str(self.repo)
            ):
                observed.append((command, kwargs["env"]))
            return real_run(*args, **kwargs)

        hostile = {
            "GIT_DIR": "/hostile/git-dir",
            "GIT_INDEX_FILE": "/hostile/index",
            "GIT_OBJECT_DIRECTORY": "/hostile/objects",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "remote.publish.url",
            "GIT_CONFIG_VALUE_0": "https://attacker.invalid/repo",
            "GIT_SSL_NO_VERIFY": "true",
            "GIT_SSL_CAINFO": "/hostile/ca.pem",
            "GIT_SSL_CAPATH": "/hostile/ca-directory",
            "GIT_ASKPASS": "/hostile/askpass",
            "GIT_SSH_COMMAND": "ssh -F /hostile/config",
        }
        with mock.patch.dict(os.environ, hostile, clear=False):
            with mock.patch.object(adapter.subprocess, "run", recording_run):
                result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "verified")
        push_commands = [item for item in observed if "push" in item[0]]
        self.assertEqual(len(push_commands), 1)
        command, env = push_commands[0]
        self.assertNotIn("publish", command)
        self.assertNotIn(str(self.remote), command)
        hook_overrides = [
            item for item in command if item.startswith("core.hooksPath=")
        ]
        self.assertEqual(len(hook_overrides), 1)
        self.assertIn("versionkeeping-empty-hooks-", hook_overrides[0])
        self.assertIn("http.sslVerify=true", command)
        boundary = command.index("--")
        alias = command[boundary + 1]
        self.assertTrue(alias.startswith("versionkeeping-publication-"))
        endpoint_keys = [key for key in env if key.startswith("VERSIONKEEPING_")]
        self.assertEqual(len(endpoint_keys), 1)
        self.assertEqual(env[endpoint_keys[0]], str(self.remote))
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_INDEX_FILE", env)
        self.assertNotIn("GIT_OBJECT_DIRECTORY", env)
        config_count = int(env["GIT_CONFIG_COUNT"])
        closed_config = {
            env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
            for index in range(config_count)
        }
        self.assertNotIn("remote.publish.url", closed_config)
        self.assertEqual(closed_config["protocol.allow"], "never")
        self.assertEqual(closed_config["protocol.ext.allow"], "never")
        self.assertEqual(closed_config["credential.helper"], "")
        self.assertEqual(closed_config["push.gpgSign"], "false")
        self.assertEqual(closed_config["commit.gpgSign"], "false")
        self.assertEqual(closed_config["tag.gpgSign"], "false")
        self.assertNotIn("GIT_SSL_NO_VERIFY", env)
        self.assertNotIn("GIT_SSL_CAINFO", env)
        self.assertNotIn("GIT_SSL_CAPATH", env)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_ASKPASS"], "false")
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.source)

    def test_signing_helper_is_blocked_before_push(self) -> None:
        marker = self.root / "signing-helper-ran"
        secret = "signer-secret-c72941"
        helper = self.root / f"signer-{secret}"
        helper.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
        helper.chmod(0o700)
        git(self.remote, "config", "receive.certNonceSeed", "test-seed")
        git(self.repo, "config", "push.gpgSign", "true")
        git(self.repo, "config", "gpg.program", str(helper))

        result = execute(self.repo, self.plan)

        self.assertFalse(marker.exists())
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(
            result["reasons"][0]["code"],
            "UNSAFE_GIT_CONFIGURATION",
        )
        self.assertNotIn(secret, json.dumps(result))

    def test_endpoint_change_after_review_blocks_before_push(self) -> None:
        changed = self.root / "changed.git"
        git(self.root, "init", "--bare", str(changed))
        git(self.repo, "remote", "set-url", "--push", "publish", str(changed))

        result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(result["reasons"][0]["code"], "REVIEWED_ENDPOINT_CHANGED")
        self.assertNotIn(str(changed), json.dumps(result))

    def test_default_branch_change_after_review_blocks_before_push(self) -> None:
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/topic")

        result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(
            result["reasons"][0]["code"], "REVIEWED_DEFAULT_BRANCH_CHANGED"
        )

    def test_default_branch_change_during_lease_probe_blocks_before_push(self) -> None:
        original_probe = execution._probe_ref
        changed = False

        def change_default_after_probe(repo, endpoint, ref, object_format):
            nonlocal changed
            observed = original_probe(repo, endpoint, ref, object_format)
            if not changed and ref == "refs/heads/topic":
                changed = True
                git(self.remote, "symbolic-ref", "HEAD", "refs/heads/topic")
            return observed

        with mock.patch.object(
            execution, "_probe_ref", side_effect=change_default_after_probe
        ):
            result = execute(self.repo, self.plan)

        self.assertTrue(changed)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(
            result["reasons"][0]["code"], "REVIEWED_DEFAULT_BRANCH_CHANGED"
        )
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.start)

    def test_config_change_after_review_blocks_before_push(self) -> None:
        git(self.repo, "config", "branch.topic.pushRemote", "publish")
        git(self.repo, "config", "branch.topic.remote", "publish")
        git(self.repo, "config", "branch.topic.merge", "refs/heads/topic")
        implicit_request = request(self.start, self.source)
        implicit_request["explicit_destination"] = None
        reviewed = plan_repository(self.repo, implicit_request)
        self.assertEqual(reviewed["status"], "ready")
        git(self.repo, "config", "--unset", "branch.topic.pushRemote")
        git(self.repo, "config", "remote.pushDefault", "publish")

        result = execute(self.repo, reviewed)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(result["reasons"][0]["code"], "REVIEWED_CONFIG_CHANGED")

    def test_target_lease_change_after_review_blocks_before_push(self) -> None:
        remote_only = commit(self.repo, "remote-only")
        git(self.repo, "push", "publish", f"{remote_only}:refs/heads/topic")
        git(self.repo, "reset", "--hard", self.source)

        result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(result["reasons"][0]["code"], "REVIEWED_LEASE_CHANGED")

    def test_remote_config_mutation_after_capture_cannot_redirect_push(self) -> None:
        redirected = self.root / "redirected.git"
        git(self.root, "init", "--bare", str(redirected))
        git(self.repo, "push", str(redirected), f"{self.start}:refs/heads/topic")
        original = execution._run_endpoint
        changed = False

        def mutate_then_run(repo, endpoint, prefix, suffix=(), **kwargs):
            nonlocal changed
            if "push" in prefix and not changed:
                changed = True
                git(
                    self.repo,
                    "remote",
                    "set-url",
                    "--push",
                    "publish",
                    str(redirected),
                )
            return original(repo, endpoint, prefix, suffix, **kwargs)

        with mock.patch.object(execution, "_run_endpoint", mutate_then_run):
            result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "verified")
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.source)
        self.assertEqual(git(redirected, "rev-parse", "refs/heads/topic"), self.start)

    def test_push_failure_is_not_retried(self) -> None:
        original = execution._run_endpoint
        push_calls = 0

        def fail_push(repo, endpoint, prefix, suffix=(), **kwargs):
            nonlocal push_calls
            if "push" in prefix:
                push_calls += 1
                return subprocess.CompletedProcess(["git", "push"], 1, "", "failed")
            return original(repo, endpoint, prefix, suffix, **kwargs)

        with mock.patch.object(execution, "_run_endpoint", fail_push):
            result = execute(self.repo, self.plan)

        self.assertEqual(push_calls, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["push_attempted"])
        reason = result["reasons"][0]
        self.assertEqual(reason["code"], "POST_PUSH_STATE_UNKNOWN")
        self.assertEqual(reason["evidence"]["failure_type"], "PUSH_FAILED")

    def test_push_timeout_is_classified_once_without_retry(self) -> None:
        real_run = subprocess.run
        push_calls = 0

        def timeout_push(*args, **kwargs):
            nonlocal push_calls
            command = args[0] if args else kwargs.get("args")
            if (
                command
                and command[0] == str(adapter.TRUSTED_SYSTEM_EXECUTABLES["git"])
                and "push" in command
            ):
                push_calls += 1
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return real_run(*args, **kwargs)

        with mock.patch.object(adapter.subprocess, "run", timeout_push):
            result = execute(self.repo, self.plan)

        self.assertEqual(push_calls, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["push_attempted"])
        reason = result["reasons"][0]
        self.assertEqual(reason["code"], "POST_PUSH_STATE_UNKNOWN")
        self.assertEqual(reason["evidence"]["failure_type"], "GIT_COMMAND_TIMEOUT")

    def test_unexpected_post_push_failure_returns_unknown_without_retry(self) -> None:
        original_probe = execution._probe_ref
        probe_calls = 0

        def fail_second_probe(repo, endpoint, ref, object_format):
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls == 2:
                raise RuntimeError("unexpected verifier failure")
            return original_probe(repo, endpoint, ref, object_format)

        with mock.patch.object(execution, "_probe_ref", side_effect=fail_second_probe):
            result = execute(self.repo, self.plan)

        self.assertEqual(probe_calls, 2)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["push_attempted"])
        reason = result["reasons"][0]
        self.assertEqual(reason["code"], "POST_PUSH_STATE_UNKNOWN")
        self.assertEqual(reason["evidence"]["expected_sha"], self.source)
        self.assertIn("Re-probe", reason["evidence"]["reconciliation"])
        self.assertNotIn("unexpected verifier failure", json.dumps(result))
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.source)

    def test_typed_post_push_probe_failure_returns_unknown(self) -> None:
        original_probe = execution._probe_ref
        probe_calls = 0

        def fail_second_probe(repo, endpoint, ref, object_format):
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls == 2:
                raise adapter.PolicyGate("PUSH_ENDPOINT_PROBE_FAILED")
            return original_probe(repo, endpoint, ref, object_format)

        with mock.patch.object(execution, "_probe_ref", side_effect=fail_second_probe):
            result = execute(self.repo, self.plan)

        reason = result["reasons"][0]
        self.assertEqual(reason["code"], "POST_PUSH_STATE_UNKNOWN")
        self.assertEqual(
            reason["evidence"]["failure_type"], "PUSH_ENDPOINT_PROBE_FAILED"
        )
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.source)

    def test_observed_post_push_ref_mismatch_remains_a_known_block(self) -> None:
        original_probe = execution._probe_ref
        probe_calls = 0

        def mismatch_second_probe(repo, endpoint, ref, object_format):
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls == 2:
                return self.start
            return original_probe(repo, endpoint, ref, object_format)

        with mock.patch.object(
            execution, "_probe_ref", side_effect=mismatch_second_probe
        ):
            result = execute(self.repo, self.plan)

        reason = result["reasons"][0]
        self.assertEqual(reason["code"], "POST_PUSH_REF_MISMATCH")
        self.assertEqual(reason["evidence"]["observed_sha"], self.start)

    def test_pre_push_typed_timeout_remains_a_deterministic_block(self) -> None:
        with mock.patch.object(
            execution,
            "_guard_repository",
            side_effect=adapter.PolicyGate(
                "GIT_COMMAND_TIMEOUT", operation="rev-parse", timeout_seconds=120
            ),
        ):
            result = execute(self.repo, self.plan)

        self.assertFalse(result["push_attempted"])
        self.assertEqual(result["reasons"][0]["code"], "GIT_COMMAND_TIMEOUT")

    def test_tampered_plan_is_rejected(self) -> None:
        tampered = json.loads(json.dumps(self.plan))
        tampered["push"]["refspec"] = f"{self.source}:refs/heads/other"

        with self.assertRaises(MalformedPlan):
            validate_ready_plan(tampered)

    def test_plan_json_rejects_duplicate_keys(self) -> None:
        with self.assertRaises(MalformedPlan):
            load_plan_json(StringIO('{"schema_version":1,"schema_version":1}'))

    def test_plan_json_rejects_non_finite_values_through_both_loaders(self) -> None:
        document = '{"schema_version": NaN}'
        with self.assertRaisesRegex(MalformedPlan, "non-finite JSON value"):
            load_plan_json(StringIO(document))
        plan_bytes = document.encode()
        digest = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
        with self.assertRaisesRegex(MalformedPlan, "non-finite JSON value"):
            execution._load_reviewed_plan(plan_bytes, digest)

    def test_ready_plan_rejects_boolean_and_float_schema_versions(self) -> None:
        for malformed in (True, 1.0):
            with self.subTest(malformed=malformed):
                plan = json.loads(json.dumps(self.plan))
                plan["schema_version"] = malformed
                with self.assertRaisesRegex(MalformedPlan, "integer 1"):
                    validate_ready_plan(plan)

    def test_cli_internal_failure_exit_is_distinct_from_typed_block(self) -> None:
        plan_path = self.root / "plan.json"
        plan_bytes, digest = reviewed_plan(self.plan)
        plan_path.write_bytes(plan_bytes)
        arguments = [
            "--repo",
            str(self.repo),
            "--plan",
            str(plan_path),
            "--reviewed-plan-sha256",
            digest,
        ]
        with mock.patch.object(
            execution_cli,
            "execute_repository",
            return_value={"status": "blocked"},
        ):
            blocked_returncode = execution_cli.main(arguments)
        with mock.patch.object(
            execution_cli, "execute_repository", side_effect=RuntimeError("internal")
        ):
            internal_returncode = execution_cli.main(arguments)
        self.assertEqual(blocked_returncode, 1)
        self.assertEqual(internal_returncode, 3)

    def test_reviewed_digest_rejects_unowned_descendant_plan_tampering(self) -> None:
        _, reviewed_digest = reviewed_plan(self.plan)
        unowned = commit(self.repo, "unowned")
        tampered = json.loads(json.dumps(self.plan))
        tampered["request"]["source_sha"] = unowned
        tampered["source_sha"] = unowned
        tampered["outgoing_shas"].append(unowned)
        tampered["push"]["source_sha"] = unowned
        tampered["push"]["refspec"] = f"{unowned}:refs/heads/topic"
        tampered["postchecks"][0]["sha"] = unowned
        validate_ready_plan(tampered)
        tampered_bytes, _ = reviewed_plan(tampered)

        with self.assertRaises(ReviewedPlanDigestMismatch):
            execute_repository(self.repo, tampered_bytes, reviewed_digest)

        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/topic"), self.start)
        fresh_request = request(self.start, unowned)
        fresh_request["task_owned_commits"] = [self.source]
        fresh = plan_repository(self.repo, fresh_request)
        self.assertEqual(fresh["status"], "blocked")
        self.assertEqual(
            fresh["reasons"][0]["code"],
            "OUTGOING_COMMITS_NOT_OWNED_OR_ADOPTED",
        )
        self.assertIn(unowned, fresh["reasons"][0]["evidence"]["shas"])

    def test_post_review_hooks_path_blocks_before_pre_push_hook(self) -> None:
        hooks = self.root / "ambient-hooks"
        hooks.mkdir()
        sentinel = self.root / "pre-push-environment"
        hook = hooks / "pre-push"
        hook.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$VERSIONKEEPING_PUBLICATION_ENDPOINT\" > "
            f"{shlex.quote(str(sentinel))}\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        git(self.repo, "config", "core.hooksPath", str(hooks))
        ambient_credential = "https://user:secret@example.invalid/repository"

        with mock.patch.dict(
            os.environ,
            {"VERSIONKEEPING_PUBLICATION_ENDPOINT": ambient_credential},
            clear=False,
        ):
            result = execute(self.repo, self.plan)

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["push_attempted"])
        self.assertEqual(
            result["reasons"],
            [
                {
                    "code": "UNSAFE_GIT_CONFIGURATION",
                    "evidence": {"config_classes": ["core.hooksPath"]},
                }
            ],
        )
        self.assertFalse(sentinel.exists())
        diagnostics = json.dumps(result)
        self.assertNotIn(str(hooks), diagnostics)
        self.assertNotIn(ambient_credential, diagnostics)

    def test_cli_duplicate_plan_key_is_malformed(self) -> None:
        plan_path = self.root / "duplicate-plan.json"
        plan_bytes = b'{"schema_version":1,"schema_version":1}'
        plan_path.write_bytes(plan_bytes)
        digest = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--repo",
                str(self.repo),
                "--plan",
                str(plan_path),
                "--reviewed-plan-sha256",
                digest,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "MALFORMED_PLAN")

    def test_cli_digest_mismatch_is_distinct_and_precedes_parsing(self) -> None:
        plan_path = self.root / "tampered-plan.json"
        plan_path.write_bytes(b"not json")
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--repo",
                str(self.repo),
                "--plan",
                str(plan_path),
                "--reviewed-plan-sha256",
                "sha256:" + "0" * 64,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)["error"]["code"],
            "REVIEWED_PLAN_DIGEST_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
