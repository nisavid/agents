import hashlib
import json
import os
import shutil
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
CLI = SCRIPTS / "plan_git_publication.py"
sys.path.insert(0, str(SCRIPTS))

import git_publication.adapter as adapter  # noqa: E402
from git_publication.adapter import (  # noqa: E402
    MalformedRequest,
    parse_request,
    plan_repository,
)
from git_publication.execution import execute_repository  # noqa: E402


def git(repo, *args, env=None):
    merged = os.environ.copy()
    merged.update({"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com"})
    merged.update(
        {"GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}
    )
    if env:
        merged.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def commit(repo, name):
    (Path(repo) / name).write_text(name, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def raw_request(start, source, **overrides):
    value = {
        "schema_version": 1,
        "start_head": start,
        "source_sha": source,
        "task_owned_commits": [source] if source != start else [],
        "adopted_commits": [],
        "removal_authorized_commits": [],
        "explicit_destination": {"remote": "publish", "ref": "refs/heads/topic"},
        "allow_create": False,
        "creation_base_ref": None,
    }
    value.update(overrides)
    return value


class RequestTests(unittest.TestCase):
    def test_rejects_missing_extra_and_short_sha_fields(self):
        complete = raw_request("a" * 40, "b" * 40)
        for bad in (
            {key: value for key, value in complete.items() if key != "source_sha"},
            dict(complete, extra=True),
            dict(complete, source_sha="deadbeef"),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(MalformedRequest):
                    parse_request(bad)

    def test_rejects_option_injection_but_allows_leading_dash_remote(self):
        complete = raw_request("a" * 40, "b" * 40)
        with self.assertRaises(MalformedRequest):
            parse_request(
                dict(
                    complete,
                    explicit_destination={"remote": "bad\nname", "ref": "refs/heads/x"},
                )
            )
        parsed = parse_request(
            dict(
                complete,
                explicit_destination={"remote": "-publish", "ref": "refs/heads/x"},
            )
        )
        self.assertEqual(parsed.explicit_destination["remote"], "-publish")
        with self.assertRaises(MalformedRequest):
            parse_request(
                dict(
                    complete,
                    explicit_destination={
                        "remote": "publish",
                        "ref": "refs/heads/x:refs/heads/y",
                    },
                )
            )

    def test_credential_bearing_endpoint_never_enters_transport_argv(self):
        endpoint = "https://user:secret@example.invalid/repository"

        class RecordingRepository:
            def __init__(self):
                self.args = None
                self.env_overrides = None
                self.hooks_path = Path("/controlled/empty-hooks")

            def run(self, args, check=True, allowed=(), env_overrides=None):
                self.args = args
                self.env_overrides = env_overrides
                return subprocess.CompletedProcess(["git", *args], 0, "", "")

        repo = RecordingRepository()
        adapter._run_endpoint(repo, endpoint, ["ls-remote", "--heads"])

        self.assertNotIn(endpoint, repo.args)
        self.assertNotIn("secret", " ".join(repo.args))
        self.assertEqual(
            repo.args[:4],
            [
                "-c",
                "http.sslVerify=true",
                "-c",
                "core.hooksPath=/controlled/empty-hooks",
            ],
        )
        self.assertEqual(
            repo.env_overrides, {"VERSIONKEEPING_PUBLICATION_ENDPOINT": endpoint}
        )

    def test_remote_push_selection_and_digest_share_one_config_snapshot(self):
        class ChangingConfigRepository:
            def __init__(self):
                self.remote_push_reads = 0

            def output(self, args, allowed=()):
                if args == ["remote"]:
                    return "publish"
                if args == ["symbolic-ref", "-q", "HEAD"]:
                    return "refs/heads/topic"
                if args[0] == "for-each-ref":
                    return "publish\x00refs/heads/topic"
                raise AssertionError(args)

            def config_all(self, key):
                values = {
                    "branch.topic.pushRemote": ["publish"],
                    "remote.pushDefault": [],
                    "push.default": ["simple"],
                }
                if key == "remote.publish.push":
                    self.remote_push_reads += 1
                    return [
                        "refs/heads/topic"
                        if self.remote_push_reads == 1
                        else "refs/heads/topic:refs/heads/changed"
                    ]
                return values[key]

        repo = ChangingConfigRepository()
        request = parse_request(
            raw_request("a" * 40, "b" * 40, explicit_destination=None)
        )

        _, ref, selection = adapter._resolve_destination(repo, request)

        self.assertEqual(ref, "refs/heads/topic")
        self.assertEqual(selection["remote_push"], ["refs/heads/topic"])
        self.assertEqual(repo.remote_push_reads, 1)

    def test_request_json_rejects_duplicate_keys(self):
        from io import StringIO

        with self.assertRaises(MalformedRequest):
            adapter.load_request_json(
                StringIO('{"schema_version":1,"schema_version":1}')
            )

    def test_object_format_contract_rejects_unknown_mixed_and_wrong_width_ids(self):
        class FormatRepository:
            def __init__(self, value):
                self.value = value

            def output(self, args, allowed=()):
                self.assertEqual(args, ["rev-parse", "--show-object-format=storage"])
                return self.value

            def assertEqual(self, actual, expected):
                if actual != expected:
                    raise AssertionError((actual, expected))

        for value in ("unknown", "sha1\nsha256"):
            with self.subTest(value=value):
                with self.assertRaises(adapter.PolicyGate):
                    adapter._object_format(FormatRepository(value))

        request = parse_request(raw_request("a" * 40, "b" * 40))
        with self.assertRaises(adapter.PolicyGate):
            adapter._bind_request_object_format(
                request, adapter.GitObjectFormat("sha256", 64)
            )

    def test_probe_requires_one_lowercase_bound_tab_record(self):
        class ProbeRepository:
            def __init__(self, output):
                self.output = output
                self.hooks_path = Path("/controlled/empty-hooks")

            def run(self, *args, **kwargs):
                return subprocess.CompletedProcess(["git"], 0, self.output, "")

        reference = "refs/heads/topic"
        for output in (
            "A" * 64 + "\t" + reference + "\n",
            "a" * 40 + " " + reference + "\n",
            "a" * 64 + "\t" + reference + "\n\n",
            "a" * 64 + "\t" + reference + "\n" + "b" * 64 + "\t" + reference + "\n",
        ):
            with self.subTest(output=output):
                with self.assertRaises(adapter.PolicyGate):
                    adapter._probe_ref(
                        ProbeRepository(output),
                        "https://example.invalid/repo",
                        reference,
                        adapter.GitObjectFormat("sha256", 64),
                    )

        self.assertEqual(
            adapter._probe_ref(
                ProbeRepository("a" * 64 + "\t" + reference + "\n"),
                "https://example.invalid/repo",
                reference,
                adapter.GitObjectFormat("sha256", 64),
            ),
            "a" * 64,
        )


class RepositoryPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        self.remote = root / "remote.git"
        self.repo = root / "repo"
        git(root, "init", "--bare", str(self.remote))
        git(root, "init", "-b", "topic", str(self.repo))
        self.start = commit(self.repo, "base")
        git(self.repo, "remote", "add", "publish", str(self.remote))
        git(self.repo, "push", "publish", f"{self.start}:refs/heads/topic")

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, request):
        return plan_repository(self.repo, request)

    def test_existing_fast_forward_and_terminal_verified(self):
        source = commit(self.repo, "change")
        result = self.plan(raw_request(self.start, source))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            result["push"]["lease"],
            f"--force-with-lease=refs/heads/topic:{self.start}",
        )
        self.assertEqual(result["source_sha"], source)

        plan_bytes = (
            json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        digest = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
        execution = execute_repository(self.repo, plan_bytes, digest)
        self.assertEqual(execution["status"], "verified")
        verified = self.plan(raw_request(self.start, source))
        self.assertEqual(verified["status"], "verified")
        self.assertIsNone(verified["push"])

    def test_sha256_repository_uses_64_character_bound_object_ids(self):
        sha256_remote = self.root / "sha256-remote.git"
        sha256_repo = self.root / "sha256-repo"
        git(self.root, "init", "--bare", "--object-format=sha256", str(sha256_remote))
        git(
            self.root, "init", "-b", "topic", "--object-format=sha256", str(sha256_repo)
        )
        start = commit(sha256_repo, "base")
        git(sha256_repo, "remote", "add", "publish", str(sha256_remote))
        git(sha256_repo, "push", "publish", f"{start}:refs/heads/topic")
        source = commit(sha256_repo, "change")

        plan = plan_repository(sha256_repo, raw_request(start, source))

        self.assertEqual(len(start), 64)
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["source_sha"], source)
        plan_bytes = (
            json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        result = execute_repository(
            sha256_repo,
            plan_bytes,
            "sha256:" + hashlib.sha256(plan_bytes).hexdigest(),
        )
        self.assertEqual(result["status"], "verified")

    def test_source_sha_is_immutable_when_head_moves(self):
        source = commit(self.repo, "change")
        later = commit(self.repo, "later")
        result = self.plan(raw_request(self.start, source))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["push"]["refspec"], f"{source}:refs/heads/topic")
        self.assertNotIn(later, result["push"]["refspec"])

    def test_absent_target_requires_allow_create_and_advertised_start(self):
        source = commit(self.repo, "change")
        request = raw_request(
            self.start,
            source,
            explicit_destination={"remote": "publish", "ref": "refs/heads/new"},
        )
        self.assertEqual(self.plan(request)["status"], "blocked")
        result = self.plan(dict(request, allow_create=True))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["push"]["lease"], "--force-with-lease=refs/heads/new:")

    def test_non_explicit_push_remote_and_remote_push_must_agree_with_default(self):
        source = commit(self.repo, "change")
        git(self.repo, "config", "branch.topic.pushRemote", "publish")
        git(self.repo, "config", "branch.topic.remote", "publish")
        git(self.repo, "config", "branch.topic.merge", "refs/heads/topic")
        request = raw_request(self.start, source, explicit_destination=None)
        self.assertEqual(self.plan(request)["destination"]["ref"], "refs/heads/topic")

        git(
            self.repo,
            "config",
            "remote.publish.push",
            "refs/heads/topic:refs/heads/other",
        )
        blocked = self.plan(request)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["reasons"][0]["code"], "PUSH_TARGET_CONFLICT")

    def test_push_remote_diverging_from_upstream_blocks_simple_default(self):
        other = Path(self.temp.name) / "other.git"
        git(Path(self.temp.name), "init", "--bare", str(other))
        git(self.repo, "remote", "add", "other", str(other))
        git(self.repo, "config", "branch.topic.remote", "publish")
        git(self.repo, "config", "branch.topic.merge", "refs/heads/topic")
        git(self.repo, "config", "branch.topic.pushRemote", "other")
        source = commit(self.repo, "change")

        result = self.plan(raw_request(self.start, source, explicit_destination=None))

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"][0]["code"], "PUSH_DEFAULT_AMBIGUOUS")

    def test_fetch_and_push_url_divergence_uses_push_endpoint(self):
        push_remote = Path(self.temp.name) / "push.git"
        git(Path(self.temp.name), "init", "--bare", str(push_remote))
        git(self.repo, "push", str(push_remote), f"{self.start}:refs/heads/topic")
        git(self.repo, "remote", "set-url", "--push", "publish", str(push_remote))
        source = commit(self.repo, "change")

        result = self.plan(raw_request(self.start, source))

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["target"]["sha"], self.start)
        self.assertNotIn(str(push_remote), json.dumps(result))

    def test_narrow_fetch_refspec_does_not_hide_push_target(self):
        git(self.repo, "config", "--unset-all", "remote.publish.fetch")
        git(
            self.repo,
            "config",
            "remote.publish.fetch",
            "+refs/heads/main:refs/remotes/publish/main",
        )
        source = commit(self.repo, "change")
        result = self.plan(raw_request(self.start, source))
        self.assertEqual(result["status"], "ready")

    def test_leading_dash_remote_is_safe_after_option_terminator(self):
        git(self.repo, "config", "remote.-publish.url", str(self.remote))
        source = commit(self.repo, "change")
        request = raw_request(
            self.start,
            source,
            explicit_destination={"remote": "-publish", "ref": "refs/heads/topic"},
        )
        result = self.plan(request)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["destination"]["remote"], "-publish")
        self.assertNotIn("argv", result["push"])

    def test_multiple_push_urls_block_and_raw_urls_are_never_output(self):
        source = commit(self.repo, "change")
        git(
            self.repo,
            "remote",
            "set-url",
            "--add",
            "--push",
            "publish",
            "https://user:secret@example.invalid/repo",
        )
        result = self.plan(raw_request(self.start, source))
        serialized = json.dumps(result)
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("secret", serialized)
        self.assertNotIn(str(self.remote), serialized)

    def test_temporary_refs_and_fetch_head_are_untouched(self):
        source = commit(self.repo, "change")
        peer = Path(self.temp.name) / "peer"
        git(
            Path(self.temp.name),
            "clone",
            "--branch",
            "topic",
            str(self.remote),
            str(peer),
        )
        remote_only = commit(peer, "downloaded-only")
        git(peer, "push", "origin", f"{remote_only}:refs/heads/topic")
        self.assertNotEqual(
            subprocess.run(
                ["git", "cat-file", "-e", f"{remote_only}^{{commit}}"],
                cwd=self.repo,
                stderr=subprocess.DEVNULL,
            ).returncode,
            0,
        )
        fetch_head = Path(git(self.repo, "rev-parse", "--git-path", "FETCH_HEAD"))
        if not fetch_head.is_absolute():
            fetch_head = self.repo / fetch_head
        fetch_head.write_text("sentinel\n", encoding="utf-8")

        result = self.plan(raw_request(self.start, source))

        self.assertEqual(result["status"], "needs_reconciliation")
        self.assertEqual(fetch_head.read_text(encoding="utf-8"), "sentinel\n")
        self.assertEqual(
            git(
                self.repo,
                "for-each-ref",
                "--format=%(refname)",
                "refs/versionkeeping/publication",
            ),
            "",
        )
        # Exact-fetch objects may persist even though every temporary ref is removed.
        self.assertEqual(git(self.repo, "cat-file", "-t", remote_only), "commit")
        self.assertEqual(
            result["planner_effects"],
            {
                "local_mutation_possible": True,
                "bounded_object_fetch": True,
                "objects_may_persist": True,
                "temporary_ref_namespace": "refs/versionkeeping/publication/",
                "fetch_head_preserved": True,
            },
        )

    def test_every_git_subprocess_scrubs_hostile_routing_and_suppresses_prompts(self):
        source = commit(self.repo, "change")
        real_run = subprocess.run
        observed = []
        observed_commands = []
        observed_stdin = []
        hostile = {
            "GIT_DIR": "/hostile/git-dir",
            "GIT_WORK_TREE": "/hostile/work-tree",
            "GIT_INDEX_FILE": "/hostile/index",
            "GIT_OBJECT_DIRECTORY": "/hostile/objects",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/hostile/alternates",
            "GIT_CONFIG_GLOBAL": "/hostile/config",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "remote.publish.url",
            "GIT_CONFIG_VALUE_0": "https://attacker.invalid/repo",
            "GIT_SSL_NO_VERIFY": "true",
            "GIT_SSL_CAINFO": "/hostile/ca.pem",
            "GIT_SSL_CAPATH": "/hostile/ca-directory",
            "GIT_SSH_COMMAND": "ssh -F /hostile/config",
            "GIT_ASKPASS": "/hostile/askpass",
            "SSH_ASKPASS": "/hostile/ssh-askpass",
            "GIT_EDITOR": "/hostile/editor",
        }

        def recording_run(*args, **kwargs):
            command = args[0] if args else kwargs.get("args")
            if command and command[0] == "git" and kwargs.get("cwd") == str(self.repo):
                observed.append(kwargs["env"])
                observed_commands.append(command)
                observed_stdin.append(kwargs["stdin"])
            return real_run(*args, **kwargs)

        with mock.patch.dict(os.environ, hostile, clear=False):
            adapter.subprocess.run = recording_run
            try:
                result = self.plan(raw_request(self.start, source))
            finally:
                adapter.subprocess.run = real_run

        self.assertEqual(result["status"], "ready")
        self.assertTrue(observed)
        self.assertTrue(all(value == subprocess.DEVNULL for value in observed_stdin))
        self.assertTrue(
            all(
                env.get("GIT_NO_LAZY_FETCH") == "1"
                and env.get("GIT_NO_REPLACE_OBJECTS") == "1"
                and env.get("GIT_TERMINAL_PROMPT") == "0"
                and env.get("GCM_INTERACTIVE") == "never"
                and env.get("GIT_ASKPASS") == "false"
                and env.get("GIT_EDITOR") == "true"
                and "BatchMode=yes" in env.get("GIT_SSH_COMMAND", "")
                and "GIT_DIR" not in env
                and "GIT_WORK_TREE" not in env
                and "GIT_INDEX_FILE" not in env
                and "GIT_OBJECT_DIRECTORY" not in env
                and "GIT_ALTERNATE_OBJECT_DIRECTORIES" not in env
                and "GIT_CONFIG_GLOBAL" not in env
                and "GIT_CONFIG_COUNT" not in env
                and "GIT_CONFIG_KEY_0" not in env
                and "GIT_CONFIG_VALUE_0" not in env
                and "GIT_SSL_NO_VERIFY" not in env
                and "GIT_SSL_CAINFO" not in env
                and "GIT_SSL_CAPATH" not in env
                and "SSH_ASKPASS" not in env
                for env in observed
            )
        )
        transport_commands = [
            command
            for command in observed_commands
            if "ls-remote" in command or "fetch" in command
        ]
        self.assertTrue(transport_commands)
        self.assertTrue(
            all(
                str(self.remote) not in argument
                for command in transport_commands
                for argument in command
            )
        )

    def test_git_timeout_is_a_deterministic_gate_without_retry(self):
        source = commit(self.repo, "change")
        real_run = adapter.subprocess.run
        calls = 0

        def timeout_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        adapter.subprocess.run = timeout_once
        try:
            result = self.plan(raw_request(self.start, source))
        finally:
            adapter.subprocess.run = real_run

        self.assertEqual(calls, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"][0]["code"], "GIT_COMMAND_TIMEOUT")
        self.assertEqual(result["reasons"][0]["evidence"]["operation"], "rev-parse")

    def test_target_change_after_exact_fetch_gates_and_cleans_temp_ref(self):
        source = commit(self.repo, "change")
        original_probe = adapter._probe_ref
        target_probes = 0

        def delete_before_stability_probe(repo, endpoint, ref, object_format):
            nonlocal target_probes
            if ref == "refs/heads/topic":
                target_probes += 1
                if target_probes == 2:
                    git(self.repo, "push", "publish", f":{ref}")
            return original_probe(repo, endpoint, ref, object_format)

        adapter._probe_ref = delete_before_stability_probe
        try:
            result = self.plan(raw_request(self.start, source))
        finally:
            adapter._probe_ref = original_probe

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["reasons"][0]["code"], "REMOTE_REF_CHANGED_DURING_FETCH"
        )
        self.assertEqual(
            git(
                self.repo,
                "for-each-ref",
                "--format=%(refname)",
                "refs/versionkeeping/publication",
            ),
            "",
        )
        self.assertEqual(result["destination"]["remote"], "publish")
        self.assertEqual(result["target"], {"present": True, "sha": self.start})
        self.assertNotIn(str(self.remote), json.dumps(result))

    def test_target_creation_between_absence_probes_gates(self):
        source = commit(self.repo, "change")
        request = raw_request(
            self.start,
            source,
            explicit_destination={"remote": "publish", "ref": "refs/heads/new"},
            allow_create=True,
        )
        original_probe = adapter._probe_ref
        calls = 0

        def create_between_probes(repo, endpoint, ref, object_format):
            nonlocal calls
            value = original_probe(repo, endpoint, ref, object_format)
            if ref == "refs/heads/new":
                calls += 1
                if calls == 1:
                    git(self.repo, "push", "publish", f"{self.start}:{ref}")
            return value

        adapter._probe_ref = create_between_probes
        try:
            result = self.plan(request)
        finally:
            adapter._probe_ref = original_probe

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["reasons"][0]["code"], "REMOTE_REF_APPEARED_DURING_PROBE"
        )

    def test_divergence_requires_exact_target_only_removal_authorization(self):
        remote_only = commit(self.repo, "remote-only")
        git(self.repo, "push", "publish", f"{remote_only}:refs/heads/topic")
        git(self.repo, "reset", "--hard", self.start)
        source = commit(self.repo, "local-only")
        request = raw_request(self.start, source)

        blocked = self.plan(request)
        self.assertEqual(blocked["status"], "needs_reconciliation")
        ready = self.plan(dict(request, removal_authorized_commits=[remote_only]))
        self.assertEqual(ready["status"], "ready")
        self.assertTrue(ready["rewrite_required"])

    def test_explicit_creation_base_requires_adoption(self):
        git(self.repo, "push", "publish", f"{self.start}:refs/heads/main")
        git(self.repo, "push", "publish", ":refs/heads/topic")
        middle = commit(self.repo, "middle")
        source = commit(self.repo, "source")
        request = raw_request(
            middle,
            source,
            explicit_destination={"remote": "publish", "ref": "refs/heads/new"},
            allow_create=True,
            creation_base_ref="refs/heads/main",
        )

        blocked = self.plan(request)
        self.assertEqual(blocked["status"], "blocked")
        ready = self.plan(dict(request, adopted_commits=[middle]))
        self.assertEqual(ready["status"], "ready")

    def test_replace_ref_and_in_progress_operation_block(self):
        source = commit(self.repo, "change")
        git(self.repo, "replace", source, self.start)
        replaced = self.plan(raw_request(self.start, source))
        self.assertEqual(replaced["reasons"][0]["code"], "REPLACE_REFS_PRESENT")
        git(self.repo, "replace", "-d", source)

        merge_head = Path(git(self.repo, "rev-parse", "--git-path", "MERGE_HEAD"))
        if not merge_head.is_absolute():
            merge_head = self.repo / merge_head
        merge_head.write_text(self.start + "\n", encoding="ascii")
        in_progress = self.plan(raw_request(self.start, source))
        self.assertEqual(in_progress["reasons"][0]["code"], "GIT_OPERATION_IN_PROGRESS")

    def test_partial_clone_and_nonempty_grafts_block(self):
        source = commit(self.repo, "change")
        git(self.repo, "config", "remote.publish.promisor", "true")
        partial = self.plan(raw_request(self.start, source))
        self.assertEqual(
            partial["reasons"][0]["code"], "PARTIAL_OR_PROMISOR_REPOSITORY"
        )
        git(self.repo, "config", "--unset", "remote.publish.promisor")

        grafts = Path(git(self.repo, "rev-parse", "--git-path", "info/grafts"))
        if not grafts.is_absolute():
            grafts = self.repo / grafts
        grafts.parent.mkdir(parents=True, exist_ok=True)
        grafts.write_text(self.start + "\n", encoding="ascii")
        grafted = self.plan(raw_request(self.start, source))
        self.assertEqual(grafted["reasons"][0]["code"], "LEGACY_GRAFTS_PRESENT")

    def test_shallow_repository_blocks(self):
        shallow = Path(self.temp.name) / "shallow"
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/topic")
        git(
            Path(self.temp.name),
            "clone",
            "--depth=1",
            f"file://{self.remote}",
            str(shallow),
        )
        result = plan_repository(shallow, raw_request(self.start, self.start))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"][0]["code"], "SHALLOW_REPOSITORY")

    def test_cli_malformed_request_is_nonzero_json(self):
        request_file = Path(self.temp.name) / "request.json"
        request_file.write_text("{}", encoding="utf-8")
        run = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--repo",
                str(self.repo),
                "--request",
                str(request_file),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(run.returncode, 0)
        error = json.loads(run.stdout)
        self.assertEqual(error["schema_version"], 1)
        self.assertEqual(error["error"]["code"], "MALFORMED_REQUEST")

    def test_cli_duplicate_request_key_is_malformed(self):
        request_file = Path(self.temp.name) / "duplicate-request.json"
        request_file.write_text(
            '{"schema_version":1,"schema_version":1}', encoding="utf-8"
        )
        run = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--repo",
                str(self.repo),
                "--request",
                str(request_file),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["error"]["code"], "MALFORMED_REQUEST")

    def test_cli_non_finite_request_value_is_malformed(self):
        request_file = Path(self.temp.name) / "non-finite-request.json"
        request_file.write_text('{"schema_version":NaN}', encoding="utf-8")
        run = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--repo",
                str(self.repo),
                "--request",
                str(request_file),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["error"]["code"], "MALFORMED_REQUEST")

    def test_cli_missing_and_unknown_arguments_are_versioned_json(self):
        for argv in (
            [sys.executable, str(CLI)],
            [sys.executable, str(CLI), "--unknown"],
        ):
            with self.subTest(argv=argv):
                run = subprocess.run(
                    argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                self.assertNotEqual(run.returncode, 0)
                error = json.loads(run.stdout)
                self.assertEqual(error["schema_version"], 1)
                self.assertEqual(error["error"]["code"], "MALFORMED_INVOCATION")

    def test_simple_without_upstream_is_blocked_at_public_seam(self):
        source = commit(self.repo, "change")
        git(self.repo, "config", "branch.topic.pushRemote", "publish")
        result = self.plan(raw_request(self.start, source, explicit_destination=None))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"][0]["code"], "PUSH_DEFAULT_AMBIGUOUS")

    def test_invalid_configured_push_remote_is_a_repository_gate(self):
        source = commit(self.repo, "change")
        git(self.repo, "config", "branch.topic.pushRemote", "")

        result = self.plan(raw_request(self.start, source, explicit_destination=None))

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"][0]["code"], "DESTINATION_REMOTE_INVALID")

    def test_remote_push_without_colon_resolves_current_branch(self):
        source = commit(self.repo, "change")
        git(self.repo, "config", "branch.topic.pushRemote", "publish")
        git(self.repo, "config", "branch.topic.remote", "publish")
        git(self.repo, "config", "branch.topic.merge", "refs/heads/topic")
        git(self.repo, "config", "remote.publish.push", "refs/heads/topic")

        result = self.plan(raw_request(self.start, source, explicit_destination=None))

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["destination"]["ref"], "refs/heads/topic")

    def test_every_named_in_progress_operation_blocks_at_public_seam(self):
        source = commit(self.repo, "change")
        markers = {
            "MERGE_HEAD": False,
            "rebase-merge": True,
            "rebase-apply": True,
            "CHERRY_PICK_HEAD": False,
            "REVERT_HEAD": False,
            "BISECT_LOG": False,
            "sequencer": True,
        }
        for marker, is_directory in markers.items():
            path = Path(git(self.repo, "rev-parse", "--git-path", marker))
            if not path.is_absolute():
                path = self.repo / path
            if is_directory:
                path.mkdir(parents=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self.start + "\n", encoding="ascii")
            try:
                result = self.plan(raw_request(self.start, source))
                self.assertEqual(result["status"], "blocked", marker)
                self.assertEqual(
                    result["reasons"][0]["code"], "GIT_OPERATION_IN_PROGRESS"
                )
                self.assertIn(marker, result["reasons"][0]["evidence"]["markers"])
            finally:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)

    def test_temp_ref_collision_is_blocked_without_overwrite_or_cleanup(self):
        source = commit(self.repo, "change")
        token = "1" * 32
        temp_ref = f"refs/versionkeeping/publication/{token}"
        git(self.repo, "update-ref", temp_ref, source)
        original_token_hex = adapter.secrets.token_hex
        adapter.secrets.token_hex = lambda _size: token
        try:
            result = self.plan(raw_request(self.start, source))
        finally:
            adapter.secrets.token_hex = original_token_hex

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"][0]["code"], "TEMP_REF_COLLISION")
        self.assertEqual(git(self.repo, "rev-parse", temp_ref), source)

    def test_temp_ref_cleanup_failure_is_blocked_with_safe_observation_context(self):
        source = commit(self.repo, "change")
        original_run = adapter.GitRepository.run

        def fail_delete(repo, args, check=True, allowed=(), env_overrides=None):
            if args[:2] == ["update-ref", "-d"]:
                return subprocess.CompletedProcess(
                    ["git", *args], 1, "", "injected cleanup failure"
                )
            return original_run(
                repo,
                args,
                check=check,
                allowed=allowed,
                env_overrides=env_overrides,
            )

        adapter.GitRepository.run = fail_delete
        try:
            result = self.plan(raw_request(self.start, source))
        finally:
            adapter.GitRepository.run = original_run

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"][0]["code"], "TEMP_REF_CLEANUP_FAILED")
        self.assertEqual(result["destination"]["remote"], "publish")
        self.assertEqual(result["destination"]["ref"], "refs/heads/topic")
        self.assertTrue(
            result["destination"]["endpoint_fingerprint"].startswith("sha256:")
        )
        self.assertTrue(result["destination"]["config_digest"].startswith("sha256:"))
        self.assertEqual(result["target"], {"present": True, "sha": self.start})
        self.assertNotIn(str(self.remote), json.dumps(result))

    def test_existing_target_does_not_probe_unrelated_advertised_heads(self):
        source = commit(self.repo, "change")
        original_run_endpoint = adapter._run_endpoint

        def malformed_heads(repo, endpoint, prefix, suffix=(), **kwargs):
            if prefix == ["ls-remote", "--heads"]:
                return subprocess.CompletedProcess(
                    ["git", *prefix], 0, "malformed-output\n", ""
                )
            return original_run_endpoint(repo, endpoint, prefix, suffix, **kwargs)

        adapter._run_endpoint = malformed_heads
        try:
            result = self.plan(raw_request(self.start, source))
        finally:
            adapter._run_endpoint = original_run_endpoint

        self.assertEqual(result["status"], "ready")

    def test_absent_target_malformed_advertised_heads_is_a_stable_policy_gate(self):
        source = commit(self.repo, "change")
        original_run_endpoint = adapter._run_endpoint

        def malformed_heads(repo, endpoint, prefix, suffix=(), **kwargs):
            if prefix == ["ls-remote", "--heads"]:
                return subprocess.CompletedProcess(
                    ["git", *prefix], 0, "malformed-output\n", ""
                )
            return original_run_endpoint(repo, endpoint, prefix, suffix, **kwargs)

        adapter._run_endpoint = malformed_heads
        try:
            result = self.plan(
                raw_request(
                    self.start,
                    source,
                    explicit_destination={
                        "remote": "publish",
                        "ref": "refs/heads/new",
                    },
                    allow_create=True,
                )
            )
        finally:
            adapter._run_endpoint = original_run_endpoint

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"][0]["code"], "REMOTE_REF_PROBE_MALFORMED")
        self.assertEqual(result["destination"]["remote"], "publish")
        self.assertNotIn(str(self.remote), json.dumps(result))

    def test_push_plan_is_data_not_a_remote_name_actuator(self):
        source = commit(self.repo, "change")
        result = self.plan(raw_request(self.start, source))
        self.assertEqual(
            result["push"]["options"],
            ["--no-follow-tags", "--recurse-submodules=check"],
        )
        self.assertEqual(result["push"]["refspec"], f"{source}:refs/heads/topic")
        self.assertNotIn("argv", result["push"])
        self.assertNotIn("publish", json.dumps(result["push"]))


if __name__ == "__main__":
    unittest.main()
