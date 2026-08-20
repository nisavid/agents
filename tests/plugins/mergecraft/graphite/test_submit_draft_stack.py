from __future__ import annotations

import importlib.util
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[4]
SCRIPT = REPOSITORY / "plugins/mergecraft/skills/graphite/scripts/submit_draft_stack.py"
spec = importlib.util.spec_from_file_location("submit_draft_stack", SCRIPT)
assert spec and spec.loader
GRAPHITE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(GRAPHITE)


class GraphiteTransportTests(unittest.TestCase):
    repository = "acme/app"
    base_oid = "a" * 40
    head_oid = "b" * 40

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.template = Path(self.temporary.name) / "body.md"
        self.template.write_text(
            "PR __PUBLISHING_REVIEWABLE_PRS_PR_NUMBER__\n", encoding="utf-8"
        )
        self.review_input = Path(self.temporary.name) / "review-input.json"
        self.review_input.write_text("{}\n", encoding="utf-8")
        self.entry = {
            "base": "main",
            "base_oid": self.base_oid,
            "head": "acme:feature",
            "head_oid": self.head_oid,
            "head_owner": "acme",
            "head_repository": self.repository,
            "title": "feat: feature",
            "body_source": {"mode": "template", "path": str(self.template)},
            "review_input": str(self.review_input),
            "review_mode": "not-required",
            "review_bundle": None,
            "selected_specialists": [],
        }
        self.candidate = {
            **self.entry,
            "local_branch": "feature",
            "body_source_sha256": GRAPHITE._sha_text(self.template.read_text()),
            "review_input_raw_sha256": GRAPHITE._sha_bytes(
                self.review_input.read_bytes()
            ),
            "review_input_sha256": "c" * 64,
            "review_input_pr": GRAPHITE.PR_NUMBER_TOKEN,
        }
        candidate_builder = mock.patch.object(
            GRAPHITE,
            "build_publication_candidate",
            return_value=mock.Mock(content_sha256="f" * 64),
        )
        candidate_builder.start()
        self.addCleanup(candidate_builder.stop)

    def test_executable_git_configuration_classes_are_closed(self) -> None:
        cases = {
            "core.alternateRefsCommand": "core.alternateRefsCommand",
            "core.askPass": "core.askPass",
            "core.fsmonitor": "core.fsmonitor",
            "core.gitProxy": "core.gitProxy",
            "core.hooksPath": "core.hooksPath",
            "core.sshCommand": "core.sshCommand",
            "include.path": "include*.path",
            "includeIf.onbranch:topic.path": "include*.path",
            "protocol.ext.allow": "protocol.*.allow",
            "filter.attack.clean": "filter.*.(clean|smudge|process)",
            "filter.attack.smudge": "filter.*.(clean|smudge|process)",
            "filter.attack.process": "filter.*.(clean|smudge|process)",
            "hook.attack.command": "hook.*.command",
            "gpg.program": "gpg.program",
            "gpg.openpgp.program": "gpg.*.program",
            "gpg.ssh.defaultKeyCommand": "gpg.ssh.defaultKeyCommand",
            "credential.helper": "credential.*.helper",
            "credential.example.com.helper": "credential.*.helper",
            "diff.external": "diff.external",
            "diff.attack.command": "diff.*.(command|textconv)",
            "diff.attack.textconv": "diff.*.(command|textconv)",
            "difftool.attack.cmd": "difftool.*.cmd",
            "merge.attack.driver": "merge.*.driver",
            "mergetool.attack.cmd": "mergetool.*.cmd",
            "remote.origin.vcs": "remote.*.(vcs|uploadpack|receivepack)",
            "remote.origin.uploadpack": "remote.*.(vcs|uploadpack|receivepack)",
            "remote.origin.receivepack": "remote.*.(vcs|uploadpack|receivepack)",
            "url.ext::.insteadOf": "url.*.(insteadOf|pushInsteadOf)",
            "submodule.attack.update": "submodule.*.update",
        }
        for key, config_class in cases.items():
            with self.subTest(key=key):
                self.assertEqual(
                    GRAPHITE._unsafe_git_config_class(key),
                    config_class,
                )
        self.assertIsNone(GRAPHITE._unsafe_git_config_class("remote.origin.url"))

    def test_closed_environment_disables_implicit_signing(self) -> None:
        environment = GRAPHITE._environment()
        closed = {
            environment[f"GIT_CONFIG_KEY_{index}"]: environment[
                f"GIT_CONFIG_VALUE_{index}"
            ]
            for index in range(int(environment["GIT_CONFIG_COUNT"]))
        }
        self.assertEqual(closed["push.gpgSign"], "false")
        self.assertEqual(closed["commit.gpgSign"], "false")
        self.assertEqual(closed["tag.gpgSign"], "false")

    def test_transport_protocol_allowlist_preserves_https_and_ssh(self) -> None:
        for endpoint in (
            "https://example.com/acme/app.git",
            "ssh://git@example.com/acme/app.git",
            "git@example.com:acme/app.git",
        ):
            with self.subTest(endpoint=endpoint):
                GRAPHITE._validate_remote_endpoint(endpoint, self.root)

        for endpoint in (
            "ext::/tmp/helper",
            "git://example.com/acme/app.git",
            "https://user:secret@example.com/acme/app.git",
            "ssh://-oProxyCommand=helper/acme/app.git",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(GRAPHITE.GraphiteTransportError) as raised:
                    GRAPHITE._validate_remote_endpoint(endpoint, self.root)
                self.assertNotIn(endpoint, str(raised.exception))

    def test_repository_binding_rejects_a_safe_wrong_remote(self) -> None:
        def completed(value: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, value, "")

        def wrong_remote(arguments, **_kwargs):
            outputs = {
                ("gt", "--version"): "1.8.6\n",
                ("gt", "repo", "remote"): "origin\n",
                ("gt", "repo", "owner"): "acme\n",
                ("gt", "repo", "name"): "app\n",
                (
                    "git",
                    "remote",
                    "get-url",
                    "--all",
                    "--",
                    "origin",
                ): "https://github.com/acme/wrong.git\n",
                (
                    "git",
                    "remote",
                    "get-url",
                    "--push",
                    "--all",
                    "--",
                    "origin",
                ): "https://github.com/acme/wrong.git\n",
            }
            return completed(outputs[tuple(arguments)])

        with (
            mock.patch.object(GRAPHITE, "_run", side_effect=wrong_remote),
            self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError,
                "repository binding",
            ),
        ):
            GRAPHITE._graphite_repository_binding(
                self.root,
                [self.candidate],
                self.repository,
            )

    def test_repository_binding_accepts_exact_version_target_and_head(self) -> None:
        def completed(value: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, value, "")

        def exact_remote(arguments, **_kwargs):
            outputs = {
                ("gt", "--version"): "1.8.6\n",
                ("gt", "repo", "remote"): "origin\n",
                ("gt", "repo", "owner"): "acme\n",
                ("gt", "repo", "name"): "app\n",
                (
                    "git",
                    "remote",
                    "get-url",
                    "--all",
                    "--",
                    "origin",
                ): "git@github.com:acme/app.git\n",
                (
                    "git",
                    "remote",
                    "get-url",
                    "--push",
                    "--all",
                    "--",
                    "origin",
                ): "ssh://git@github.com/acme/app.git\n",
            }
            return completed(outputs[tuple(arguments)])

        with mock.patch.object(GRAPHITE, "_run", side_effect=exact_remote):
            binding = GRAPHITE._graphite_repository_binding(
                self.root,
                [self.candidate],
                self.repository,
            )

        self.assertEqual(binding["graphite_version"], "1.8.6")
        self.assertEqual(binding["target_repository"], self.repository)
        self.assertEqual(binding["head_repository"], self.repository)
        self.assertNotIn("github.com", json.dumps(binding))

    def test_repository_binding_rejects_unsupported_graphite_version(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "1.8.7\n", "")
        with (
            mock.patch.object(GRAPHITE, "_run", return_value=completed),
            self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError,
                "unsupported Graphite version",
            ),
        ):
            GRAPHITE._graphite_repository_binding(
                self.root,
                [self.candidate],
                self.repository,
            )

    def stored(
        self,
        *,
        number: int = 42,
        title: str = "feat: feature",
        body: str = "Graphite transport\n",
        draft: bool = True,
    ) -> dict[str, object]:
        return {
            "number": number,
            "url": f"https://github.com/acme/app/pull/{number}",
            "title": title,
            "body": body,
            "baseRefName": "main",
            "baseRefOid": self.base_oid,
            "headRefName": "feature",
            "headRefOid": self.head_oid,
            "headRepositoryOwner": {"login": "acme"},
            "headRepository": {"nameWithOwner": self.repository},
            "isDraft": draft,
            "state": "OPEN",
        }

    def request(self) -> dict[str, object]:
        return {
            "schema_version": GRAPHITE.SCHEMA_VERSION,
            "repository": self.repository,
            "repository_root": str(self.root),
            "current_branch": "feature",
            "stack": [self.entry],
        }

    def graphite_metadata(
        self,
        rows: list[tuple[str, str | None, str, str]],
        *,
        supported_schema: bool = True,
    ) -> Path:
        path = self.root / ".graphite_metadata.db"
        connection = sqlite3.connect(path)
        if supported_schema:
            connection.executescript(
                """
                CREATE TABLE "branch_metadata" (
                    "branch_name" text not null primary key,
                    "parent_branch_name" text,
                    "parent_branch_revision" text,
                    "last_submitted_version" text,
                    "state" text,
                    "children" text,
                    "branch_revision" text,
                    "validation_result" text,
                    "parent_head_revision" text
                );
                CREATE INDEX "idx_branch_metadata_parent"
                    on "branch_metadata" ("parent_branch_name");
                CREATE TABLE "kysely_migration" (
                    "name" varchar(255) not null primary key,
                    "timestamp" varchar(255) not null
                );
                CREATE TABLE "kysely_migration_lock" (
                    "id" varchar(255) not null primary key,
                    "is_locked" integer default 0 not null
                );
                """
            )
            connection.executemany(
                "INSERT INTO kysely_migration(name, timestamp) VALUES (?, ?)",
                [
                    ("20260211_initial_schema", "fixture"),
                    ("20260212_add_validation_columns", "fixture"),
                    ("20260220_add_parent_head_revision", "fixture"),
                ],
            )
            connection.execute(
                "INSERT INTO kysely_migration_lock(id, is_locked) VALUES (?, ?)",
                ("migration_lock", 0),
            )
            connection.executemany(
                """
                INSERT INTO branch_metadata(
                    branch_name,
                    parent_branch_name,
                    branch_revision,
                    validation_result
                ) VALUES (?, ?, ?, ?)
                """,
                rows,
            )
        else:
            connection.execute(
                "CREATE TABLE branch_metadata(branch_name TEXT PRIMARY KEY)"
            )
        connection.commit()
        connection.close()
        return path

    def initialize_git_repository(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull,
            }
        )
        subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            check=True,
            capture_output=True,
            env=environment,
        )

    def configure_git(self, key: str, value: str) -> None:
        subprocess.run(
            ["git", "-C", str(self.root), "config", "--local", key, value],
            check=True,
            capture_output=True,
        )

    def build_plan_with_metadata(self, metadata: Path) -> dict[str, object]:
        def completed(value: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, value, "")

        with (
            mock.patch.object(GRAPHITE, "_candidate", return_value=self.candidate),
            mock.patch.object(
                GRAPHITE,
                "_run",
                side_effect=[
                    completed(self.head_oid + "\n"),
                    completed(self.base_oid + "\n"),
                ],
            ),
            mock.patch.object(
                GRAPHITE,
                "_git_graphite_snapshot",
                return_value={"snapshot": "bound"},
            ),
            mock.patch.object(
                GRAPHITE,
                "_graphite_repository_binding",
                return_value={"binding": "bound"},
            ),
            mock.patch.object(GRAPHITE, "_live_preimage", return_value=None),
            mock.patch.object(
                GRAPHITE,
                "_graphite_metadata_path",
                return_value=metadata,
                create=True,
            ),
            mock.patch.object(
                GRAPHITE,
                "_establish_inert_git_policy",
                return_value="1" * 64,
                create=True,
            ),
        ):
            return GRAPHITE.build_plan(self.request())

    def audit_for(
        self,
        item: dict[str, object],
        *,
        receipt_id: str = "receipt-one",
        sequence: int = 1,
    ) -> dict[str, object]:
        return {
            "status": "verified",
            "receipt_id": receipt_id,
            "provenance": "canonical",
            "sequence": sequence,
            "identity_epoch": item["target_identity_epoch"],
            "final": {
                "is_draft": item["target_is_draft"],
                "title_sha256": item["target_title_sha256"],
                "body_sha256": item["target_body_sha256"],
            },
            "review_input_sha256": item["target_review_input_sha256"],
            "review": {
                "mode": item["target_review_mode"],
                "publication_candidate_sha256": item[
                    "target_publication_candidate_sha256"
                ],
                "observation": (
                    {"contract": "mergecraft-required-review-observation-v1"}
                    if item["target_review_mode"] == "required"
                    else None
                ),
            },
        }

    def required_item(self, *, body: str = "Graphite transport\n") -> dict[str, object]:
        bundle = Path(self.temporary.name) / "review-bundle"
        entry = {
            **self.candidate,
            "review_mode": "required",
            "review_bundle": str(bundle),
            "selected_specialists": ["security"],
        }
        return GRAPHITE._handoff_entry(
            entry,
            None,
            self.stored(body=body),
            self.repository,
        )

    def plan(self) -> dict[str, object]:
        unsigned = {
            "schema_version": GRAPHITE.SCHEMA_VERSION,
            "request": self.request(),
            "candidates": [self.candidate],
            "mutation_inventory": {
                "contract": "mergecraft-graphite-mutation-inventory-v1",
                "metadata_sha256": "1" * 64,
                "schema_sha256": "2" * 64,
                "trunk": {"branch": "main", "revision": self.base_oid},
                "branches": [
                    {
                        "branch": "feature",
                        "parent": "main",
                        "revision": self.head_oid,
                    }
                ],
                "repository_binding": {
                    "contract": "mergecraft-graphite-repository-binding-v1",
                    "graphite_version": "1.8.6",
                    "target_repository": self.repository,
                    "head_repository": self.repository,
                    "remote_sha256": "4" * 64,
                    "fetch_endpoint_sha256": "5" * 64,
                    "push_endpoint_sha256": "6" * 64,
                },
                "submit_scope": "current-and-downstack-only",
            },
            "snapshot": {
                "repository_root": str(self.root),
                "current_branch": "feature",
                "clean_status_sha256": GRAPHITE._sha_text(""),
                "git_config_sha256": "3" * 64,
                "gt_log_short_sha256": "d" * 64,
                "gt_trunk_sha256": "e" * 64,
            },
            "preimages": [None],
        }
        return {
            **unsigned,
            "content_sha256": GRAPHITE._sha_bytes(GRAPHITE._canonical(unsigned)),
        }

    def test_build_plan_binds_exact_local_refs_and_separates_candidates(self) -> None:
        def completed(value: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, value, "")

        with (
            mock.patch.object(GRAPHITE, "_candidate", return_value=self.candidate),
            mock.patch.object(
                GRAPHITE,
                "_run",
                side_effect=[
                    completed(self.head_oid + "\n"),
                    completed(self.base_oid + "\n"),
                ],
            ),
            mock.patch.object(
                GRAPHITE,
                "_git_graphite_snapshot",
                return_value={"snapshot": "bound"},
            ),
            mock.patch.object(
                GRAPHITE,
                "_graphite_mutation_inventory",
                return_value={"inventory": "bound"},
            ),
            mock.patch.object(
                GRAPHITE,
                "_graphite_repository_binding",
                return_value={"binding": "bound"},
            ),
            mock.patch.object(
                GRAPHITE,
                "_establish_inert_git_policy",
                return_value="1" * 64,
            ),
            mock.patch.object(GRAPHITE, "_live_preimage", return_value=None),
        ):
            plan = GRAPHITE.build_plan(self.request())

        self.assertEqual(plan["request"], self.request())
        self.assertEqual(plan["candidates"], [self.candidate])
        self.assertEqual(
            plan["mutation_inventory"]["repository_binding"],
            {"binding": "bound"},
        )
        self.assertNotEqual(plan["request"]["stack"], plan["candidates"])

    def test_plan_rejects_an_omitted_graphite_ancestor(self) -> None:
        metadata = self.graphite_metadata(
            [
                ("main", None, self.base_oid, "TRUNK"),
                ("middle", "main", "c" * 40, "VALID"),
                ("feature", "middle", self.head_oid, "VALID"),
            ]
        )

        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError,
            "exactly match",
        ):
            self.build_plan_with_metadata(metadata)

    def test_plan_rejects_metadata_revision_drift(self) -> None:
        metadata = self.graphite_metadata(
            [
                ("main", None, self.base_oid, "TRUNK"),
                ("feature", "main", "c" * 40, "VALID"),
            ]
        )

        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError,
            "revision",
        ):
            self.build_plan_with_metadata(metadata)

    def test_plan_fails_closed_on_unsupported_graphite_metadata(self) -> None:
        metadata = self.graphite_metadata([], supported_schema=False)

        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError,
            "unsupported Graphite metadata",
        ):
            self.build_plan_with_metadata(metadata)

    def test_plan_accepts_the_exact_metadata_chain(self) -> None:
        metadata = self.graphite_metadata(
            [
                ("main", None, self.base_oid, "TRUNK"),
                ("feature", "main", self.head_oid, "VALID"),
            ]
        )

        plan = self.build_plan_with_metadata(metadata)

        self.assertEqual(
            plan["mutation_inventory"]["branches"],
            [
                {
                    "branch": "feature",
                    "parent": "main",
                    "revision": self.head_oid,
                }
            ],
        )

    def test_graphite_blocks_a_smudge_filter_before_it_executes(self) -> None:
        self.initialize_git_repository()
        payload = self.root / "payload.txt"
        attributes = self.root / ".gitattributes"
        payload.write_text("payload\n", encoding="utf-8")
        attributes.write_text("payload.txt filter=attack\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.root), "add", ".gitattributes", "payload.txt"],
            check=True,
            capture_output=True,
        )
        marker = Path(self.temporary.name) / "smudge-executed"
        helper = Path(self.temporary.name) / "smudge.py"
        helper.write_text(
            """import pathlib
import sys

pathlib.Path(sys.argv[1]).write_text("executed", encoding="utf-8")
sys.stdout.buffer.write(sys.stdin.buffer.read())
""",
            encoding="utf-8",
        )
        self.configure_git(
            "filter.attack.smudge",
            shlex.join([sys.executable, str(helper), str(marker)]),
        )
        payload.unlink()

        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError,
            "unsafe repository Git configuration",
        ):
            GRAPHITE._run(
                ["git", "checkout-index", "--force", "--", "payload.txt"],
                cwd=self.root,
            )

        self.assertFalse(marker.exists())

    def test_graphite_blocks_helper_config_without_disclosing_its_value(
        self,
    ) -> None:
        self.initialize_git_repository()
        secret = "TOP-SECRET-MERGE-HELPER-VALUE"
        self.configure_git("mergetool.attack.cmd", f"printf {secret}")

        with self.assertRaises(GRAPHITE.GraphiteTransportError) as raised:
            GRAPHITE._run(["git", "status", "--short"], cwd=self.root)

        self.assertIn("unsafe repository Git configuration", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_graphite_blocks_signing_helper_before_gt_starts(self) -> None:
        self.initialize_git_repository()
        marker = Path(self.temporary.name) / "gt-signing-helper-ran"
        secret = "TOP-SECRET-GRAPHITE-SIGNER"
        self.configure_git("push.gpgSign", "true")
        self.configure_git("gpg.program", f"{marker}-{secret}")
        real_run = GRAPHITE.subprocess.run

        def execute_marker_if_gt_starts(arguments, **options):
            if Path(arguments[0]).name == "gt":
                marker.write_text("executed", encoding="utf-8")
                return subprocess.CompletedProcess(arguments, 0, "submitted\n", "")
            return real_run(arguments, **options)

        with (
            mock.patch.object(
                GRAPHITE.subprocess,
                "run",
                side_effect=execute_marker_if_gt_starts,
            ),
            self.assertRaises(GRAPHITE.GraphiteTransportError) as raised,
        ):
            GRAPHITE._run(["gt", "submit", "--no-stack"], cwd=self.root)

        self.assertFalse(marker.exists())
        self.assertNotIn(secret, str(raised.exception))

    def test_graphite_blocks_an_insecure_local_remote(self) -> None:
        self.initialize_git_repository()
        remote = Path(self.temporary.name) / "insecure.git"
        subprocess.run(
            ["git", "init", "--quiet", "--bare", str(remote)],
            check=True,
            capture_output=True,
        )
        remote.chmod(0o777)
        self.configure_git("remote.origin.url", str(remote))

        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError,
            "owner-secure",
        ):
            GRAPHITE._run(["git", "status", "--short"], cwd=self.root)

    def test_graphite_blocks_remote_beneath_other_owned_writable_parent(
        self,
    ) -> None:
        remote = Path(self.temporary.name) / "replaceable.git"
        subprocess.run(
            ["git", "init", "--quiet", "--bare", str(remote)],
            check=True,
            capture_output=True,
        )
        remote.chmod(0o700)
        parent = remote.parent.resolve()
        parent.chmod(0o777)
        real_lstat = Path.lstat

        def other_owned_parent(path: Path, *args, **kwargs):
            metadata = real_lstat(path, *args, **kwargs)
            if path == parent:
                fields = list(metadata)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return metadata

        try:
            with (
                mock.patch.object(Path, "lstat", other_owned_parent),
                self.assertRaisesRegex(
                    GRAPHITE.GraphiteTransportError,
                    "owner-secure",
                ),
            ):
                GRAPHITE._validate_local_remote(remote)
        finally:
            parent.chmod(0o700)

    def test_graphite_allows_an_owner_secure_local_remote(self) -> None:
        self.initialize_git_repository()
        remote = Path(self.temporary.name) / "secure.git"
        subprocess.run(
            ["git", "init", "--quiet", "--bare", str(remote)],
            check=True,
            capture_output=True,
        )
        remote.chmod(0o700)
        self.configure_git("remote.origin.url", str(remote))

        result = GRAPHITE._run(["git", "status", "--short"], cwd=self.root)

        self.assertEqual(result.returncode, 0)

    def test_stack_entry_requires_an_explicit_specialist_selection(self) -> None:
        entry = dict(self.entry)
        del entry["selected_specialists"]
        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError, "unsupported or missing fields"
        ):
            GRAPHITE._candidate(entry, self.repository)

    def test_schema_v1_requests_and_handoffs_are_not_silently_upgraded(self) -> None:
        request = {**self.request(), "schema_version": 1}
        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError, "schema_version must be 2"
        ):
            GRAPHITE.build_plan(request)

        unsigned = {
            "schema_version": 1,
            "status": "transport-complete-repair-required",
            "plan_sha256": "a" * 64,
            "repository_root": str(self.root),
            "transport_command_error": None,
            "pull_requests": [],
            "failures": [],
        }
        handoff = {
            **unsigned,
            "content_sha256": GRAPHITE._sha_bytes(GRAPHITE._canonical(unsigned)),
        }
        path = Path(self.temporary.name) / "legacy-handoff.json"
        path.write_text(json.dumps(handoff), encoding="utf-8")
        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError, "schema_version must be 2"
        ):
            GRAPHITE._load_handoff(path)

    def test_execute_runs_one_exact_transport_and_emits_publisher_handoff(self) -> None:
        plan = self.plan()
        output = Path(self.temporary.name) / "handoff.json"
        receipt_root = Path(self.temporary.name) / "receipts"
        completed = subprocess.CompletedProcess([], 0, "submitted\n", "")
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(
                GRAPHITE, "prepare_receipt_store", return_value=receipt_root
            ),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ) as transaction_lock,
            mock.patch.object(GRAPHITE, "_run", return_value=completed) as run,
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            result = GRAPHITE.execute(plan, output)

        self.assertEqual(result["status"], "transport-complete-repair-required")
        run.assert_called_once_with(
            [
                "gt",
                "submit",
                "--no-stack",
                "--draft",
                "--no-edit",
                "--no-ai",
                "--no-interactive",
            ],
            cwd=self.root,
            timeout=GRAPHITE.MUTATION_TIMEOUT_SECONDS,
        )
        transaction_lock.assert_called_once_with(
            receipt_root,
            repository=self.repository,
            base="main",
            head="acme:feature",
            head_owner="acme",
            head_repository=self.repository,
        )
        handoff = result["pull_requests"][0]
        self.assertEqual(handoff["pr"], 42)
        self.assertEqual(len(handoff["publisher_commands"]), 1)
        command = handoff["publisher_commands"][0]
        self.assertIn("--body-template", command)
        self.assertIn("--expected-title-sha256", command)
        self.assertEqual(command[command.index("--review-mode") + 1], "not-required")
        self.assertEqual(command[command.index("--selected-specialists") + 1], "[]")
        self.assertNotIn("--review-bundle", command)
        self.assertNotIn("gh", command)
        stored = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(stored["content_sha256"], result["content_sha256"])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_required_handoff_preserves_review_profile_and_candidate_digest(
        self,
    ) -> None:
        with mock.patch.object(
            GRAPHITE,
            "build_publication_candidate",
            return_value=mock.Mock(content_sha256="9" * 64),
        ) as build_candidate:
            item = self.required_item()

        command = item["publisher_commands"][0]
        self.assertEqual(command[command.index("--review-mode") + 1], "required")
        self.assertEqual(
            command[command.index("--review-bundle") + 1],
            item["target_review_bundle"],
        )
        self.assertEqual(
            command[command.index("--selected-specialists") + 1],
            '["security"]',
        )
        self.assertEqual(item["target_publication_candidate_sha256"], "9" * 64)
        build_candidate.assert_called_once()
        arguments = build_candidate.call_args.kwargs
        self.assertEqual(arguments["operation"], "update-text")
        self.assertEqual(arguments["review_mode"], "required")
        self.assertEqual(arguments["selected_specialists"], ["security"])

        bypass = list(command)
        bundle_index = bypass.index("--review-bundle")
        del bypass[bundle_index : bundle_index + 2]
        with self.assertRaisesRegex(
            GRAPHITE.GraphiteTransportError, "review bundle drifted"
        ):
            GRAPHITE._checked_publisher_command(bypass, item)

    def test_required_audit_accepts_only_exact_canonical_v3_review(self) -> None:
        item = self.required_item()
        exact = self.audit_for(item)
        self.assertTrue(GRAPHITE._audit_matches_target(exact, item))

        cases = {
            "legacy-v2": {
                **exact,
                "review": {"state": "legacy-unrecorded"},
            },
            "reconciliation": {
                **exact,
                "provenance": "reconciled-unreceipted",
                "review": {"state": "unwitnessed-reconciliation"},
            },
            "v3-not-required": {
                **exact,
                "review": {
                    **exact["review"],
                    "mode": "not-required",
                    "observation": None,
                },
            },
            "different-candidate": {
                **exact,
                "review": {
                    **exact["review"],
                    "publication_candidate_sha256": "0" * 64,
                },
            },
            "missing-observation": {
                **exact,
                "review": {**exact["review"], "observation": None},
            },
        }
        for label, audit in cases.items():
            with self.subTest(label=label):
                self.assertFalse(GRAPHITE._audit_matches_target(audit, item))

    def test_matching_live_target_is_not_upgraded_without_a_transition(
        self,
    ) -> None:
        item = self.required_item(body="PR 42\n")
        self.assertEqual(item["publisher_commands"], [])
        self.assertNotIn("no_transition_reconcile_command", item)
        handoff = {
            "repository_root": str(self.root),
            "content_sha256": "d" * 64,
            "pull_requests": [item],
        }
        legacy = {
            **self.audit_for(item),
            "review": {"state": "legacy-unrecorded"},
        }
        with mock.patch.object(
            GRAPHITE, "_run_json_command", return_value=legacy
        ) as run:
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError, "exact handoff target"
            ):
                GRAPHITE.repair(
                    handoff, Path(self.temporary.name) / "repair-no-upgrade.json"
                )
        run.assert_called_once_with(
            item["final_audit_command"], cwd=self.root, operation="audit"
        )

    def test_matching_live_target_accepts_existing_exact_required_receipt(
        self,
    ) -> None:
        item = self.required_item(body="PR 42\n")
        handoff = {
            "repository_root": str(self.root),
            "content_sha256": "d" * 64,
            "pull_requests": [item],
        }
        exact = self.audit_for(item)
        with mock.patch.object(
            GRAPHITE, "_run_json_command", return_value=exact
        ) as run:
            result = GRAPHITE.repair(
                handoff, Path(self.temporary.name) / "repair-exact.json"
            )
        self.assertEqual(result["status"], "canonical-repair-complete")
        self.assertEqual(result["pull_requests"][0]["publisher_result_sha256"], [])
        run.assert_called_once()

    def test_checkpoint_cannot_rebind_the_review_candidate(self) -> None:
        item = self.required_item(body="PR 42\n")
        handoff = {
            "repository_root": str(self.root),
            "content_sha256": "d" * 64,
            "pull_requests": [item],
        }
        exact = self.audit_for(item)
        output = Path(self.temporary.name) / "repair-checkpoint.json"
        with mock.patch.object(GRAPHITE, "_run_json_command", return_value=exact):
            GRAPHITE.repair(handoff, output)

        checkpoint_path = output.with_name(f".{output.name}.checkpoint.json")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint["completed"][0]["target_publication_candidate_sha256"] = "0" * 64
        GRAPHITE._write_private_json(checkpoint_path, checkpoint)
        with (
            mock.patch.object(GRAPHITE, "_run_json_command", return_value=exact),
            self.assertRaisesRegex(GRAPHITE.GraphiteTransportError, "checkpointed PR"),
        ):
            GRAPHITE.repair(handoff, output)

    def test_existing_ready_pr_is_never_drafted_by_handoff(self) -> None:
        body = Path(self.temporary.name) / "existing.md"
        body.write_text("Canonical body\n", encoding="utf-8")
        candidate = {
            **self.candidate,
            "body_source": {"mode": "file", "path": str(body)},
            "review_input_pr": 42,
        }
        preimage = {
            "number": 42,
            "url": "https://github.com/acme/app/pull/42",
            "title_sha256": "1" * 64,
            "body_sha256": "2" * 64,
            "is_draft": False,
        }
        handoff = GRAPHITE._handoff_entry(
            candidate,
            preimage,
            self.stored(body="Old body\n", draft=False),
            self.repository,
        )
        commands = handoff["publisher_commands"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(
            commands[0][commands[0].index("--expected-state") + 1], "ready"
        )
        self.assertNotIn("ready", commands[0][:3])

    def test_already_exact_ready_target_binds_mark_ready_candidate(self) -> None:
        body = Path(self.temporary.name) / "existing-ready.md"
        body.write_text("Canonical body\n", encoding="utf-8")
        candidate = {
            **self.candidate,
            "body_source": {"mode": "file", "path": str(body)},
            "review_input_pr": 42,
        }
        preimage = {
            "number": 42,
            "url": "https://github.com/acme/app/pull/42",
            "title_sha256": "1" * 64,
            "body_sha256": "2" * 64,
            "is_draft": False,
        }
        with mock.patch.object(
            GRAPHITE,
            "build_publication_candidate",
            return_value=mock.Mock(content_sha256="8" * 64),
        ) as build_candidate:
            handoff = GRAPHITE._handoff_entry(
                candidate,
                preimage,
                self.stored(body="Canonical body\n", draft=False),
                self.repository,
            )

        self.assertEqual(handoff["publisher_commands"], [])
        self.assertEqual(build_candidate.call_args.kwargs["operation"], "mark-ready")
        self.assertEqual(
            build_candidate.call_args.kwargs["body_source_kind"], "stored-body"
        )

    def test_existing_ready_pr_drafted_by_transport_is_restored_and_audited(
        self,
    ) -> None:
        body = Path(self.temporary.name) / "existing.md"
        body.write_text("Canonical body\n", encoding="utf-8")
        candidate = {
            **self.candidate,
            "body_source": {"mode": "file", "path": str(body)},
            "review_input_pr": 42,
        }
        preimage = {
            "number": 42,
            "url": "https://github.com/acme/app/pull/42",
            "title_sha256": "1" * 64,
            "body_sha256": "2" * 64,
            "is_draft": False,
        }
        item = GRAPHITE._handoff_entry(
            candidate,
            preimage,
            self.stored(body="Old body\n", draft=True),
            self.repository,
        )
        self.assertTrue(item["is_draft"])
        self.assertFalse(item["target_is_draft"])
        self.assertEqual(
            [command[2] for command in item["publisher_commands"]],
            ["text", "ready"],
        )
        handoff = {
            "schema_version": GRAPHITE.SCHEMA_VERSION,
            "repository_root": str(self.root),
            "pull_requests": [item],
            "content_sha256": "d" * 64,
        }
        output = Path(self.temporary.name) / "repair.json"
        final_audit = self.audit_for(item)
        calls = iter(
            [
                {"status": "unavailable"},
                {"status": "updated"},
                {"status": "ready"},
                final_audit,
            ]
        )
        with mock.patch.object(
            GRAPHITE,
            "_run_json_command",
            side_effect=lambda *_args, **_kwargs: next(calls),
        ):
            result = GRAPHITE.repair(handoff, output)

        self.assertEqual(result["status"], "canonical-repair-complete")
        self.assertFalse(result["pull_requests"][0]["target_is_draft"])
        self.assertEqual(
            result["pull_requests"][0]["audit"]["final"]["is_draft"], False
        )

    def test_ready_restoration_targets_the_final_mark_ready_candidate(self) -> None:
        body = Path(self.temporary.name) / "existing-crlf.md"
        body.write_bytes(b"Canonical body\r\n")
        candidate = {
            **self.candidate,
            "body_source": {"mode": "file", "path": str(body)},
            "review_input_pr": 42,
        }
        preimage = {
            "number": 42,
            "url": "https://github.com/acme/app/pull/42",
            "title_sha256": "1" * 64,
            "body_sha256": "2" * 64,
            "is_draft": False,
        }
        with mock.patch.object(
            GRAPHITE,
            "build_publication_candidate",
            return_value=mock.Mock(content_sha256="8" * 64),
        ) as build_candidate:
            item = GRAPHITE._handoff_entry(
                candidate,
                preimage,
                self.stored(body="Old body\n", draft=True),
                self.repository,
            )

        self.assertEqual(
            [command[2] for command in item["publisher_commands"]],
            ["text", "ready"],
        )
        self.assertEqual(item["target_publication_candidate_sha256"], "8" * 64)
        arguments = build_candidate.call_args.kwargs
        self.assertEqual(arguments["operation"], "mark-ready")
        self.assertEqual(arguments["body_source_kind"], "stored-body")
        self.assertEqual(arguments["body_source_raw"], b"Canonical body\r\n")
        self.assertEqual(arguments["published_body"], "Canonical body\r\n")

    def test_plan_drift_stops_before_transport(self) -> None:
        plan = self.plan()
        drifted = {**plan, "content_sha256": "0" * 64}
        output = Path(self.temporary.name) / "handoff.json"
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=drifted),
            mock.patch.object(GRAPHITE, "_run") as run,
        ):
            with self.assertRaisesRegex(GRAPHITE.GraphiteTransportError, "drifted"):
                GRAPHITE.execute(plan, output)
        run.assert_not_called()

    def test_partial_transport_writes_ambiguous_handoff_without_retry(self) -> None:
        plan = self.plan()
        output = Path(self.temporary.name) / "handoff.json"
        completed = subprocess.CompletedProcess([], 0, "submitted\n", "")
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(GRAPHITE, "_run", return_value=completed) as run,
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[]),
        ):
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError, "inspect the private handoff"
            ):
                GRAPHITE.execute(plan, output)
        self.assertEqual(run.call_count, 1)
        handoff = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(handoff["status"], "transport-ambiguous-inspection-required")
        self.assertEqual(len(handoff["failures"]), 1)

    def test_repair_executes_only_owned_publisher_commands_and_audits(self) -> None:
        plan = self.plan()
        transport_output = Path(self.temporary.name) / "handoff.json"
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                GRAPHITE,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, "submitted\n", ""),
            ),
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            handoff = GRAPHITE.execute(plan, transport_output)
        repair_output = Path(self.temporary.name) / "repair.json"
        publisher = json.dumps(
            {
                "status": "verified",
                "receipt_id": "receipt-one",
                "provenance": "canonical",
                "sequence": 1,
            }
        )
        audit = json.dumps(self.audit_for(handoff["pull_requests"][0]))
        with mock.patch.object(
            GRAPHITE,
            "_run",
            side_effect=[
                subprocess.CompletedProcess([], 0, json.dumps({"status": "drift"}), ""),
                subprocess.CompletedProcess([], 0, publisher, ""),
                subprocess.CompletedProcess([], 0, audit, ""),
            ],
        ) as run:
            result = GRAPHITE.repair(handoff, repair_output)
        self.assertEqual(result["status"], "canonical-repair-complete")
        self.assertEqual(run.call_count, 3)
        self.assertEqual(result["pull_requests"][0]["audit"]["provenance"], "canonical")

    def test_repair_rejects_latest_receipt_drift_after_older_match(self) -> None:
        plan = self.plan()
        transport_output = Path(self.temporary.name) / "handoff.json"
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                GRAPHITE,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, "submitted\n", ""),
            ),
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            handoff = GRAPHITE.execute(plan, transport_output)

        publisher = json.dumps(
            {
                "status": "verified",
                "receipt_id": "older-matching-receipt",
                "provenance": "canonical",
                "sequence": 3,
            }
        )
        latest_audit = json.dumps(
            {
                "status": "drift",
                "receipt_id": "authoritative-latest-receipt",
                "provenance": "canonical",
                "sequence": 4,
                "reason": (
                    "live PR state does not match the authoritative latest receipt"
                ),
            }
        )
        with mock.patch.object(
            GRAPHITE,
            "_run",
            side_effect=[
                subprocess.CompletedProcess([], 0, latest_audit, ""),
                subprocess.CompletedProcess([], 0, publisher, ""),
                subprocess.CompletedProcess([], 0, latest_audit, ""),
            ],
        ) as run:
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError,
                "audit did not verify the exact handoff target",
            ):
                GRAPHITE.repair(
                    handoff,
                    Path(self.temporary.name) / "repair.json",
                )

        self.assertEqual(run.call_count, 3)

    def test_two_pr_repair_resumes_after_first_checkpoint_without_duplicate_mutation(
        self,
    ) -> None:
        first = self.plan()
        transport_output = Path(self.temporary.name) / "handoff.json"
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=first),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                GRAPHITE,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, "submitted\n", ""),
            ),
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            handoff = GRAPHITE.execute(first, transport_output)
        second = json.loads(json.dumps(handoff["pull_requests"][0]))
        second["pr"] = 43
        second["url"] = "https://github.com/acme/app/pull/43"
        handoff["pull_requests"].append(second)
        unsigned = {
            key: value for key, value in handoff.items() if key != "content_sha256"
        }
        handoff["content_sha256"] = GRAPHITE._sha_bytes(GRAPHITE._canonical(unsigned))
        output = Path(self.temporary.name) / "repair.json"
        drift = {"status": "drift"}
        verified_one = self.audit_for(handoff["pull_requests"][0])
        verified_two = self.audit_for(
            handoff["pull_requests"][1], receipt_id="receipt-two"
        )
        publisher = {"status": "verified", "provenance": "canonical"}
        first_run = [
            drift,
            publisher,
            verified_one,
            drift,
            GRAPHITE.GraphiteTransportError("fail after first PR"),
        ]
        with mock.patch.object(
            GRAPHITE, "_run_json_command", side_effect=first_run
        ) as run:
            with self.assertRaisesRegex(GRAPHITE.GraphiteTransportError, "fail after"):
                GRAPHITE.repair(handoff, output)
        self.assertEqual(run.call_count, 5)
        checkpoint = output.with_name(f".{output.name}.checkpoint.json")
        self.assertTrue(checkpoint.is_file())

        second_run = [verified_one, drift, publisher, verified_two]
        with mock.patch.object(
            GRAPHITE, "_run_json_command", side_effect=second_run
        ) as run:
            result = GRAPHITE.repair(handoff, output)
        self.assertEqual(result["status"], "canonical-repair-complete")
        self.assertEqual(run.call_count, 4)
        self.assertEqual([item["pr"] for item in result["pull_requests"]], [42, 43])

    def test_restart_checkpoints_exact_target_audit_without_replaying_publisher(
        self,
    ) -> None:
        plan = self.plan()
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                GRAPHITE,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, "submitted\n", ""),
            ),
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            handoff = GRAPHITE.execute(
                plan, Path(self.temporary.name) / "handoff-interrupted.json"
            )
        output = Path(self.temporary.name) / "repair-interrupted.json"
        verified = self.audit_for(handoff["pull_requests"][0])
        publisher = {"status": "verified", "provenance": "canonical"}
        with (
            mock.patch.object(
                GRAPHITE,
                "_run_json_command",
                side_effect=[{"status": "drift"}, publisher, verified],
            ),
            mock.patch.object(
                GRAPHITE,
                "_write_private_json",
                side_effect=GRAPHITE.GraphiteTransportError(
                    "interrupted before checkpoint persistence"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError,
                "interrupted before checkpoint persistence",
            ):
                GRAPHITE.repair(handoff, output)

        with mock.patch.object(
            GRAPHITE, "_run_json_command", return_value=verified
        ) as restarted_run:
            result = GRAPHITE.repair(handoff, output)

        self.assertEqual(result["status"], "canonical-repair-complete")
        restarted_run.assert_called_once_with(
            handoff["pull_requests"][0]["final_audit_command"],
            cwd=Path(handoff["repository_root"]),
            operation="audit",
        )
        checkpoint_path = output.with_name(f".{output.name}.checkpoint.json")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["completed"][0]["audit"], verified)
        self.assertEqual(checkpoint["completed"][0]["publisher_result_sha256"], [])
        self.assertEqual(
            checkpoint["completed"][0]["target_review_mode"],
            handoff["pull_requests"][0]["target_review_mode"],
        )
        self.assertEqual(
            checkpoint["completed"][0]["target_publication_candidate_sha256"],
            handoff["pull_requests"][0]["target_publication_candidate_sha256"],
        )

    def test_repair_rejects_newer_verified_receipt_for_a_different_body_target(
        self,
    ) -> None:
        plan = self.plan()
        with (
            mock.patch.object(GRAPHITE, "build_plan", return_value=plan),
            mock.patch.object(GRAPHITE, "prepare_receipt_store"),
            mock.patch.object(
                GRAPHITE, "creation_transaction_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                GRAPHITE,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, "submitted\n", ""),
            ),
            mock.patch.object(GRAPHITE, "_matching_prs", return_value=[self.stored()]),
        ):
            handoff = GRAPHITE.execute(
                plan, Path(self.temporary.name) / "handoff-race.json"
            )
        item = handoff["pull_requests"][0]
        newer = self.audit_for(item, receipt_id="newer", sequence=2)
        newer["final"] = {
            **newer["final"],
            "body_sha256": "f" * 64,
        }
        with mock.patch.object(GRAPHITE, "_run_json_command", return_value=newer):
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError, "exact handoff target"
            ):
                GRAPHITE.repair(handoff, Path(self.temporary.name) / "repair-race.json")

    def test_repair_rejects_unowned_command_without_execution(self) -> None:
        handoff = {
            "repository_root": str(self.root),
            "content_sha256": "f" * 64,
            "pull_requests": [
                {
                    "repository": self.repository,
                    "pr": 42,
                    "url": "https://github.com/acme/app/pull/42",
                    "target_review_mode": "not-required",
                    "target_publication_candidate_sha256": "f" * 64,
                    "target_review_bundle": None,
                    "target_selected_specialists": [],
                    "publisher_commands": [["sh", "-c", "unexpected"]],
                    "final_audit_command": [
                        sys.executable,
                        str(GRAPHITE.AUDIT),
                        "audit",
                    ],
                }
            ],
        }
        with mock.patch.object(GRAPHITE, "_run") as run:
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError, "executable drifted"
            ):
                GRAPHITE.repair(handoff, Path(self.temporary.name) / "repair.json")
        run.assert_not_called()

    def test_publisher_output_rejects_duplicate_json_keys(self) -> None:
        command = [sys.executable, str(GRAPHITE.AUDIT), "audit"]
        duplicate = '{"status":"verified","status":"drift"}'
        with mock.patch.object(
            GRAPHITE,
            "_run",
            return_value=subprocess.CompletedProcess([], 0, duplicate, ""),
        ):
            with self.assertRaisesRegex(
                GRAPHITE.GraphiteTransportError, "duplicate JSON key"
            ):
                GRAPHITE._run_json_command(
                    command,
                    cwd=self.root,
                    operation="audit",
                )


if __name__ == "__main__":
    unittest.main()
