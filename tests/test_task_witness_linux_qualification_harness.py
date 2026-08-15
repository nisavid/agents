from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[1]
HELPER = REPOSITORY / "scripts" / "prepare_task_witness_linux_qualification.py"
CANDIDATE_RUNNER = REPOSITORY / "scripts" / "run_task_witness_qualification.py"
LINUX_QUALIFICATION_WORKFLOW = (
    REPOSITORY / ".github" / "workflows" / "task-witness-linux-qualification.yml"
)
FROZEN_CANDIDATE_SHA = "a342f1468374933d3240b345ead000f529945990"


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "task_witness_linux_qualification_preparation",
        HELPER,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("qualification preparation helper cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_candidate_runner():
    specification = importlib.util.spec_from_file_location(
        "task_witness_qualification_runner",
        CANDIDATE_RUNNER,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("qualification runner cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class TaskWitnessLinuxQualificationHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = load_helper()

    def git_environment(self, home: Path) -> dict[str, str]:
        return {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(home),
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
        }

    def git(
        self,
        root: Path,
        *arguments: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        process = subprocess.run(
            ["/usr/bin/git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            env=self.git_environment(root.parent),
        )
        if check and process.returncode != 0:
            raise AssertionError(process.stderr.decode("utf-8", errors="replace"))
        return process

    def build_candidate_transport_repository(self, root: Path) -> str:
        root.mkdir()
        self.git(root, "init")
        self.git(root, "symbolic-ref", "HEAD", "refs/heads/main")
        candidate_runner = root / "scripts" / "run_task_witness_qualification.py"
        candidate_runner.parent.mkdir()
        candidate_runner.write_text("candidate runner\n")
        self.git(root, "add", str(candidate_runner.relative_to(root)))
        self.git(
            root,
            "-c",
            "user.name=Qualification Test",
            "-c",
            "user.email=qualification@example.invalid",
            "commit",
            "-m",
            "test candidate",
        )
        self.git(
            root,
            "remote",
            "add",
            "origin",
            "https://github.com/nisavid/agents",
        )
        self.git(root, "config", "--local", "gc.auto", "0")
        head = self.git(root, "rev-parse", "HEAD^{commit}").stdout.decode().strip()
        self.git(root, "update-ref", "refs/remotes/origin/main", head)
        return head

    def add_candidate_worktree_config_drift(self, root: Path) -> None:
        self.git(root, "config", "--local", "extensions.worktreeConfig", "true")
        self.git(
            root,
            "config",
            "--worktree",
            "core.hooksPath",
            str(root.parent / "hooks"),
        )

    def candidate_transport_state(self, root: Path) -> tuple[bytes, ...]:
        index_path = Path(
            self.git(
                root,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "index",
            )
            .stdout.decode()
            .strip()
        )
        return (
            self.git(root, "rev-parse", "HEAD^{commit}").stdout,
            self.git(root, "rev-parse", "HEAD^{tree}").stdout,
            index_path.read_bytes(),
            self.git(
                root,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ).stdout,
            b"\n".join(
                sorted(
                    self.git(
                        root,
                        "cat-file",
                        "--batch-all-objects",
                        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                    ).stdout.splitlines()
                )
            ),
            self.git(
                root,
                "for-each-ref",
                "--format=%(refname) %(objectname)",
            ).stdout,
        )

    def candidate_transport_config_files(
        self,
        root: Path,
    ) -> tuple[bytes | None, bytes | None]:
        paths = (root / ".git" / "config", root / ".git" / "config.worktree")
        return tuple(path.read_bytes() if path.exists() else None for path in paths)

    def detach_candidate_transport(
        self,
        root: Path,
        candidate_sha: str,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "detach-candidate-transport",
                "--candidate-root",
                str(root),
                "--candidate-sha",
                candidate_sha,
            ],
            check=False,
            capture_output=True,
            env=self.git_environment(root.parent),
        )

    def test_candidate_transport_detachment_preserves_repository(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve() / "candidate-stage"
            candidate_sha = self.build_candidate_transport_repository(root)
            before = self.candidate_transport_state(root)

            result = self.detach_candidate_transport(root, candidate_sha)

            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stderr, b"")
            self.assertEqual(self.candidate_transport_state(root), before)
            self.assertEqual(
                self.git(
                    root,
                    "config",
                    "--local",
                    "--get-regexp",
                    "^remote\\.",
                    check=False,
                ).returncode,
                1,
            )
            self.assertEqual(
                self.git(
                    root,
                    "config",
                    "--local",
                    "--get",
                    "gc.auto",
                ).stdout,
                b"0\n",
            )
            self.assertEqual(
                self.git(
                    root,
                    "show-ref",
                    "--verify",
                    "refs/remotes/origin/main",
                ).stdout,
                f"{candidate_sha} refs/remotes/origin/main\n".encode(),
            )

    @unittest.skipUnless(
        sys.version_info >= (3, 10),
        "the frozen candidate requires Python 3.10 or newer",
    )
    def test_detached_candidate_passes_frozen_candidate_config_check(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve() / "candidate-stage"
            candidate_sha = self.build_candidate_transport_repository(root)
            result = self.detach_candidate_transport(root, candidate_sha)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

            candidate_runner = load_candidate_runner()
            candidate_runner._require_safe_candidate_config(
                root,
                Path("/usr/bin/git"),
            )

    def test_candidate_transport_detachment_rejects_drift_before_mutation(
        self,
    ) -> None:
        mutations = {
            "missing-origin": lambda root: self.git(
                root, "config", "--local", "--remove-section", "remote.origin"
            ),
            "changed-url": lambda root: self.git(
                root,
                "config",
                "--local",
                "remote.origin.url",
                "https://example.invalid/agents",
            ),
            "duplicate-url": lambda root: self.git(
                root,
                "config",
                "--local",
                "--add",
                "remote.origin.url",
                "https://github.com/nisavid/agents",
            ),
            "changed-fetch": lambda root: self.git(
                root,
                "config",
                "--local",
                "remote.origin.fetch",
                "+refs/heads/main:refs/remotes/origin/main",
            ),
            "duplicate-fetch": lambda root: self.git(
                root,
                "config",
                "--local",
                "--add",
                "remote.origin.fetch",
                "+refs/heads/*:refs/remotes/origin/*",
            ),
            "extra-remote-key": lambda root: self.git(
                root,
                "config",
                "--local",
                "remote.origin.pushurl",
                "https://example.invalid/agents",
            ),
            "credential-header": lambda root: self.git(
                root,
                "config",
                "--local",
                "http.https://github.com/.extraheader",
                "AUTHORIZATION: basic redacted",
            ),
            "fetch-bundle-uri": lambda root: self.git(
                root,
                "config",
                "--local",
                "fetch.bundleURI",
                "https://example.invalid/candidate.bundle",
            ),
            "alternate-refs-command": lambda root: self.git(
                root,
                "config",
                "--local",
                "core.alternateRefsCommand",
                "/usr/bin/false",
            ),
            "branch-remote": lambda root: self.git(
                root,
                "config",
                "--local",
                "branch.main.remote",
                "https://example.invalid/agents",
            ),
            "branch-push-remote": lambda root: self.git(
                root,
                "config",
                "--local",
                "branch.main.pushRemote",
                "https://example.invalid/agents",
            ),
            "include": lambda root: self.git(
                root,
                "config",
                "--local",
                "include.path",
                str(root.parent / "missing-configuration"),
            ),
            "conditional-include": lambda root: self.git(
                root,
                "config",
                "--local",
                "includeIf.onbranch:main.path",
                str(root.parent / "missing-conditional-configuration"),
            ),
            "worktree-config": self.add_candidate_worktree_config_drift,
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root).resolve() / "candidate-stage"
                candidate_sha = self.build_candidate_transport_repository(root)
                mutate(root)
                before_config = self.candidate_transport_config_files(root)
                before_state = self.candidate_transport_state(root)

                result = self.detach_candidate_transport(root, candidate_sha)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.candidate_transport_config_files(root),
                    before_config,
                )
                self.assertEqual(self.candidate_transport_state(root), before_state)

    def test_candidate_transport_detachment_rejects_git_diagnostics_before_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve() / "candidate-stage"
            candidate_sha = self.build_candidate_transport_repository(root)
            broken_ref = root / ".git" / "refs" / "remotes" / "origin" / "broken"
            broken_ref.write_bytes(b"not-an-object-id\n")
            before_config = self.candidate_transport_config_files(root)
            before_ref = broken_ref.read_bytes()
            diagnostic = self.git(
                root,
                "for-each-ref",
                "--format=%(refname) %(objectname)",
            )
            self.assertEqual(diagnostic.returncode, 0)
            self.assertNotEqual(diagnostic.stderr, b"")
            self.assertNotIn(b"refs/remotes/origin/broken", diagnostic.stdout)

            result = self.detach_candidate_transport(root, candidate_sha)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                self.candidate_transport_config_files(root),
                before_config,
            )
            self.assertEqual(broken_ref.read_bytes(), before_ref)

    def test_candidate_transport_detachment_rejects_sha_mismatch_before_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve() / "candidate-stage"
            candidate_sha = self.build_candidate_transport_repository(root)
            wrong_sha = "0" * 40 if candidate_sha != "0" * 40 else "1" * 40
            before_config = self.candidate_transport_config_files(root)
            before_state = self.candidate_transport_state(root)

            result = self.detach_candidate_transport(root, wrong_sha)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                self.candidate_transport_config_files(root),
                before_config,
            )
            self.assertEqual(self.candidate_transport_state(root), before_state)

    def test_candidate_transport_detachment_rejects_hidden_runner_changes_before_mutation(
        self,
    ) -> None:
        hidden_flags = {
            "assume-unchanged": "--assume-unchanged",
            "skip-worktree": "--skip-worktree",
        }
        for name, flag in hidden_flags.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root).resolve() / "candidate-stage"
                candidate_sha = self.build_candidate_transport_repository(root)
                relative_runner = Path("scripts/run_task_witness_qualification.py")
                candidate_runner = root / relative_runner
                self.git(root, "update-index", flag, str(relative_runner))
                altered_runner = b"altered candidate runner\n"
                candidate_runner.write_bytes(altered_runner)
                self.assertEqual(
                    self.git(
                        root,
                        "status",
                        "--porcelain=v1",
                        "-z",
                        "--untracked-files=all",
                    ).stdout,
                    b"",
                )
                self.assertEqual(
                    self.git(root, "diff", "--quiet", check=False).returncode,
                    0,
                )
                hidden_entry = self.git(root, "ls-files", "-v", "-z").stdout
                self.assertFalse(hidden_entry.startswith(b"H "))
                before_config = self.candidate_transport_config_files(root)
                before_state = self.candidate_transport_state(root)

                result = self.detach_candidate_transport(root, candidate_sha)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.candidate_transport_config_files(root),
                    before_config,
                )
                self.assertEqual(self.candidate_transport_state(root), before_state)
                self.assertEqual(candidate_runner.read_bytes(), altered_runner)
                self.assertEqual(
                    self.git(root, "ls-files", "-v", "-z").stdout,
                    hidden_entry,
                )

    def test_candidate_transport_detachment_rejects_stat_cache_hidden_runner_change_before_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve() / "candidate-stage"
            candidate_sha = self.build_candidate_transport_repository(root)
            candidate_runner = root / "scripts" / "run_task_witness_qualification.py"
            old_timestamp_ns = 946_684_800_000_000_000
            os.utime(
                candidate_runner,
                ns=(old_timestamp_ns, old_timestamp_ns),
            )
            self.git(root, "update-index", "--refresh")
            original_metadata = candidate_runner.stat()
            original = candidate_runner.read_bytes()
            altered_runner = b"!" * (len(original) - 1) + b"\n"
            self.git(root, "config", "--local", "core.trustctime", "false")
            self.git(root, "config", "--local", "core.checkStat", "minimal")
            candidate_runner.write_bytes(altered_runner)
            os.utime(
                candidate_runner,
                ns=(original_metadata.st_atime_ns, original_metadata.st_mtime_ns),
            )
            self.assertEqual(
                self.git(
                    root,
                    "status",
                    "--porcelain=v1",
                    "-z",
                    "--untracked-files=all",
                ).stdout,
                b"",
            )
            self.assertEqual(
                self.git(root, "diff", "--quiet", check=False).returncode,
                0,
            )
            self.assertTrue(
                self.git(root, "ls-files", "-v", "-z").stdout.startswith(b"H ")
            )
            with self.assertRaises(self.helper.PreparationError):
                self.helper.require_candidate_visible_tree_matches_head(root)
            before_config = self.candidate_transport_config_files(root)
            before_state = self.candidate_transport_state(root)

            result = self.detach_candidate_transport(root, candidate_sha)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                self.candidate_transport_config_files(root),
                before_config,
            )
            self.assertEqual(self.candidate_transport_state(root), before_state)
            self.assertEqual(candidate_runner.read_bytes(), altered_runner)

    def test_candidate_transport_detachment_rejects_legacy_remotes_before_mutation(
        self,
    ) -> None:
        legacy_sources = {
            "remotes/origin": (
                b"URL: https://example.invalid/agents\n"
                b"Pull: refs/heads/*:refs/remotes/origin/*\n"
            ),
            "branches/origin": b"https://example.invalid/agents#main\n",
        }
        for relative, content in legacy_sources.items():
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve() / "candidate-stage"
                candidate_sha = self.build_candidate_transport_repository(root)
                legacy = root / ".git" / relative
                legacy.parent.mkdir(exist_ok=True)
                legacy.write_bytes(content)
                before_config = self.candidate_transport_config_files(root)
                before_state = self.candidate_transport_state(root)

                result = self.detach_candidate_transport(root, candidate_sha)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.candidate_transport_config_files(root),
                    before_config,
                )
                self.assertEqual(self.candidate_transport_state(root), before_state)
                self.assertEqual(legacy.read_bytes(), content)

    def test_candidate_transport_detachment_rejects_alternate_objects_before_mutation(
        self,
    ) -> None:
        for disposition in ("regular", "symlink"):
            with (
                self.subTest(disposition=disposition),
                tempfile.TemporaryDirectory() as raw,
            ):
                root = Path(raw).resolve() / "candidate-stage"
                candidate_sha = self.build_candidate_transport_repository(root)
                alternate_repository = root.parent / "alternate.git"
                clone = subprocess.run(
                    [
                        "/usr/bin/git",
                        "clone",
                        "--bare",
                        "--no-hardlinks",
                        str(root),
                        str(alternate_repository),
                    ],
                    check=False,
                    capture_output=True,
                    env=self.git_environment(root.parent),
                )
                self.assertEqual(clone.returncode, 0, clone.stderr.decode())

                alternates = root / ".git" / "objects" / "info" / "alternates"
                content = f"{alternate_repository / 'objects'}\n".encode()
                if disposition == "regular":
                    alternates.write_bytes(content)
                else:
                    alternate_list = root.parent / "alternate-object-path"
                    alternate_list.write_bytes(content)
                    alternates.symlink_to(alternate_list)
                before_config = self.candidate_transport_config_files(root)
                before_state = self.candidate_transport_state(root)

                result = self.detach_candidate_transport(root, candidate_sha)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.candidate_transport_config_files(root),
                    before_config,
                )
                self.assertEqual(self.candidate_transport_state(root), before_state)
                if disposition == "regular":
                    self.assertEqual(alternates.read_bytes(), content)
                else:
                    self.assertTrue(alternates.is_symlink())
                    self.assertEqual(os.readlink(alternates), str(alternate_list))

    def test_candidate_transport_detachment_rejects_external_object_directories_before_mutation(
        self,
    ) -> None:
        for disposition in ("objects", "loose-object-directory"):
            with (
                self.subTest(disposition=disposition),
                tempfile.TemporaryDirectory() as raw,
            ):
                root = Path(raw).resolve() / "candidate-stage"
                candidate_sha = self.build_candidate_transport_repository(root)
                objects = root / ".git" / "objects"
                if disposition == "objects":
                    object_directory = objects
                else:
                    object_directory = objects / candidate_sha[:2]
                external_objects = root.parent / f"external-{disposition}"
                object_directory.rename(external_objects)
                object_directory.symlink_to(external_objects, target_is_directory=True)
                before_config = self.candidate_transport_config_files(root)
                before_state = self.candidate_transport_state(root)

                result = self.detach_candidate_transport(root, candidate_sha)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.candidate_transport_config_files(root),
                    before_config,
                )
                self.assertEqual(self.candidate_transport_state(root), before_state)
                self.assertTrue(object_directory.is_symlink())
                self.assertEqual(os.readlink(object_directory), str(external_objects))

    def test_workflow_detaches_candidate_transport_after_exact_input_checks(
        self,
    ) -> None:
        workflow = LINUX_QUALIFICATION_WORKFLOW.read_text()
        preflight_and_detachment = """\
          candidate_sha=$(
            /usr/bin/git -C candidate-stage rev-parse HEAD^{commit}
          )
          test "$candidate_sha" = "$CANDIDATE_SHA"
          /usr/bin/git -C candidate-stage diff --quiet
          /usr/bin/git -C candidate-stage diff --cached --quiet
          test -z "$(/usr/bin/git -C candidate-stage status --porcelain)"
          /usr/bin/python3 \\
            harness/scripts/prepare_task_witness_linux_qualification.py \\
            detach-candidate-transport \\
            --candidate-root "$GITHUB_WORKSPACE/candidate-stage" \\
            --candidate-sha "$CANDIDATE_SHA"
"""
        copy = (
            '          sudo /usr/bin/cp -a -- "$GITHUB_WORKSPACE/candidate-stage" '
            '"$CANDIDATE_ROOT"\n'
        )
        self.assertEqual(workflow.count(preflight_and_detachment), 1)
        self.assertLess(workflow.index(preflight_and_detachment), workflow.index(copy))

    def test_workflow_binds_the_exact_frozen_candidate(self) -> None:
        workflow = LINUX_QUALIFICATION_WORKFLOW.read_text()
        self.assertEqual(workflow.count(FROZEN_CANDIDATE_SHA), 3)
        self.assertIn(
            "vars.TASK_WITNESS_LINUX_QUALIFICATION_CANDIDATE ==\n"
            f"          '{FROZEN_CANDIDATE_SHA}' &&\n",
            workflow,
        )
        self.assertIn(
            f"      CANDIDATE_SHA: {FROZEN_CANDIDATE_SHA}\n",
            workflow,
        )
        self.assertIn(
            f"          ref: {FROZEN_CANDIDATE_SHA}\n",
            workflow,
        )

    def write_elf(
        self,
        path: Path,
        interpreters: list[str],
        *,
        dynamic: bool = False,
    ) -> None:
        identifier = b"\x7fELF\x02\x01\x01" + (b"\0" * 9)
        program_count = len(interpreters) + int(dynamic)
        program_offset = 64 if program_count else 0
        segment_offset = 64 + (56 * program_count)
        program_headers: list[bytes] = []
        segments: list[bytes] = []
        if dynamic:
            program_headers.append(struct.pack("<IIQQQQQQ", 2, 4, 0, 0, 0, 0, 0, 8))
        for interpreter in interpreters:
            raw = interpreter.encode("utf-8") + b"\0"
            program_headers.append(
                struct.pack(
                    "<IIQQQQQQ",
                    3,
                    4,
                    segment_offset,
                    0,
                    0,
                    len(raw),
                    len(raw),
                    1,
                )
            )
            segments.append(raw)
            segment_offset += len(raw)
        header = struct.pack(
            "<16sHHIQQQIHHHHHH",
            identifier,
            3,
            62,
            1,
            0,
            program_offset,
            0,
            0,
            64,
            56,
            program_count,
            64,
            0,
            0,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(header + b"".join(program_headers) + b"".join(segments))

    def test_content_document_binds_exact_canonical_unsigned_bytes(self) -> None:
        unsigned = {
            "schema_version": 1,
            "contract": "example-v1",
            "nested": {"z": 2, "a": 1},
        }
        value = self.helper.content_document(unsigned)
        self.assertEqual(
            value["content_sha256"],
            hashlib.sha256(self.helper.canonical_bytes(unsigned)).hexdigest(),
        )
        self.assertEqual(
            json.loads(self.helper.canonical_bytes(value)),
            value,
        )

    def test_create_new_writer_preserves_an_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "evidence.json"
            path.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                self.helper.write_create_new(path, {"new": True})
            self.assertEqual(path.read_bytes(), b"existing")

    def test_build_evidence_projects_live_ext_family_label_for_candidate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            runtime_root = root / "runtime"
            runtime_executable = runtime_root / "bin" / "python"
            runtime_executable.parent.mkdir(parents=True)
            runtime_executable.write_bytes(b"python")
            runtime_executable.chmod(0o755)
            candidate_root = root / "candidate"
            candidate_root.mkdir()
            host_audit_root = root / "host-audit"
            host_audit_root.mkdir()
            context = root / "context.json"
            runtime_audit = root / "runtime-audit.json"
            output = root / "evidence"
            candidate_sha = "1" * 40
            self.helper.write_create_new(
                context,
                self.helper.content_document(
                    {"schema_version": 1, "contract": "test-context-v1"}
                ),
            )
            self.helper.write_create_new(
                runtime_audit,
                self.helper.content_document(
                    {
                        "schema_version": 1,
                        "contract": "test-runtime-audit-v1",
                        "disposition": "qualified",
                        "smoke": {
                            "executable": str(runtime_executable),
                            "implementation": "cpython",
                            "version": [3, 13, 7],
                        },
                    }
                ),
            )
            self.helper.write_create_new(
                host_audit_root / "provisioning-audit.json",
                self.helper.content_document(
                    {
                        "schema_version": 1,
                        "contract": "test-provisioning-audit-v1",
                        "disposition": "qualified",
                        "passwd_user": {
                            "name": "task-witness-qualification",
                            "uid": 1001,
                            "primary_gid": 1001,
                            "supplementary_gids": [],
                            "home": "/home/task-witness-qualification",
                        },
                    }
                ),
            )
            self.helper.write_create_new(
                host_audit_root / "native-host-audit.json",
                self.helper.content_document(
                    {
                        "schema_version": 1,
                        "contract": "test-native-host-audit-v1",
                        "disposition": "qualified",
                    }
                ),
            )
            filesystem_audit = self.helper.content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-linux-filesystem-audit-v1",
                    "authority": {
                        "kind": "cooperative-operator-owned-workflow",
                        "cryptographic_attestation": False,
                        "product_attestation": False,
                    },
                    "disposition": "qualified",
                    "filesystem": {
                        "type": "ext2/ext3",
                        "probe_root": (
                            "/home/task-witness-qualification/"
                            ".task-witness-filesystem-probe"
                        ),
                        "semantics": {
                            name: (
                                "C.UTF-8"
                                if name == "c-utf8-locale"
                                else (
                                    "task-witness-qualification"
                                    if name == "passwd-database"
                                    else True
                                )
                            )
                            for name in self.helper.FILESYSTEM_SEMANTICS
                        },
                    },
                }
            )
            self.helper.write_create_new(
                host_audit_root / "filesystem-audit.json",
                filesystem_audit,
            )

            def tool_record(identifier: str, _path: Path) -> dict[str, object]:
                invoked = f"/usr/bin/{identifier}"
                return {
                    "id": identifier,
                    "invoked_path": invoked,
                    "resolved_path": invoked,
                    "length": 1,
                    "sha256": "2" * 64,
                    "uid": 0,
                    "gid": 0,
                    "mode": 0o755,
                }

            args = SimpleNamespace(
                runtime_root=runtime_root,
                runtime_executable=runtime_executable,
                candidate_root=candidate_root,
                host_audit_root=host_audit_root,
                output_dir=output,
                context=context,
                runtime_audit=runtime_audit,
                candidate_sha=candidate_sha,
            )
            with (
                mock.patch.object(self.helper, "require_root"),
                mock.patch.object(self.helper, "validate_runtime_pyyaml_audit"),
                mock.patch.object(
                    self.helper,
                    "runtime_inventory_entries",
                    return_value=[],
                ),
                mock.patch.object(
                    self.helper,
                    "tool_record",
                    side_effect=tool_record,
                ),
                mock.patch.object(
                    self.helper,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        args=[],
                        returncode=0,
                        stdout=f"{candidate_sha}\n",
                        stderr="",
                    ),
                ),
            ):
                self.helper.build_evidence(args)

            retained_audit_raw = (output / "filesystem-audit.json").read_bytes()
            retained_audit = json.loads(retained_audit_raw)
            profile = json.loads(
                (output / "platform-profile.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                retained_audit_raw,
                self.helper.canonical_bytes(filesystem_audit),
            )
            self.assertEqual(retained_audit, filesystem_audit)
            self.assertEqual(
                retained_audit["filesystem"]["type"],
                "ext2/ext3",
            )
            self.assertEqual(profile["filesystem"]["type"], "ext2-ext3")
            self.assertEqual(
                profile["filesystem"]["evidence_sha256"],
                hashlib.sha256(retained_audit_raw).hexdigest(),
            )
            load_candidate_runner().parse_platform_profile(profile)

    def test_profile_filesystem_type_rejects_host_label_drift(self) -> None:
        candidate = load_candidate_runner()
        mapping = self.helper.PROFILE_FILESYSTEM_TYPE_BY_OBSERVATION
        self.assertEqual(mapping, {"ext2/ext3": "ext2-ext3"})
        self.assertEqual(len(set(mapping.values())), len(mapping))
        self.assertTrue(set(mapping).isdisjoint(mapping.values()))
        for token in mapping.values():
            self.assertIsNotNone(candidate.TOKEN_RE.fullmatch(token))

        unsupported = (
            "ext2-ext3",
            " ext2/ext3",
            "ext2/ext3 ",
            "EXT2/EXT3",
            "ext2/ext4",
            "ext2+ext3",
            "ext2\\ext3",
            "ext2\u2215ext3",
            "ext2\uff0fext3",
            "xfs",
            "",
            None,
            False,
            7,
            ["ext2/ext3"],
            {"label": "ext2/ext3"},
        )
        for value in unsupported:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    self.helper.PreparationError,
                    "filesystem type observation is unsupported",
                ),
            ):
                self.helper.profile_filesystem_type(value)

    @unittest.skipUnless(
        sys.platform.startswith("linux") and hasattr(os, "pipe2"),
        "requires the Linux filesystem-probe surface",
    )
    def test_filesystem_probes_observe_the_child_session_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            probe_root = Path(raw_root) / "filesystem-probes"

            results = self.helper.filesystem_probes(probe_root)

            self.assertTrue(results["process-session"])
            self.assertEqual(sorted(results), self.helper.FILESYSTEM_SEMANTICS)
            self.assertFalse(probe_root.exists())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and hasattr(os, "pipe2"),
        "requires the Linux filesystem-probe surface",
    )
    def test_filesystem_probes_cleanup_when_child_session_probe_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            probe_root = Path(raw_root) / "filesystem-probes"

            with (
                mock.patch.object(
                    self.helper.os,
                    "getsid",
                    side_effect=OSError("session unavailable"),
                ),
                self.assertRaisesRegex(
                    self.helper.PreparationError,
                    "process session probe failed",
                ),
            ):
                self.helper.filesystem_probes(probe_root)

            self.assertFalse(probe_root.exists())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and hasattr(os, "pipe2"),
        "requires the Linux filesystem-probe surface",
    )
    def test_filesystem_probes_cleanup_after_parent_session_read_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            probe_root = Path(raw_root) / "filesystem-probes"
            descriptors: list[int] = []
            children: list[int] = []
            pipe2 = self.helper.os.pipe2
            fork = self.helper.os.fork

            def observe_pipe2(flags: int) -> tuple[int, int]:
                observed = pipe2(flags)
                descriptors.extend(observed)
                return observed

            def observe_fork() -> int:
                observed = fork()
                if observed > 0:
                    children.append(observed)
                return observed

            with (
                mock.patch.object(
                    self.helper.os,
                    "pipe2",
                    side_effect=observe_pipe2,
                ),
                mock.patch.object(
                    self.helper.os,
                    "fork",
                    side_effect=observe_fork,
                ),
                mock.patch.object(
                    self.helper.os,
                    "read",
                    side_effect=OSError("parent read failed"),
                ),
                self.assertRaisesRegex(OSError, "parent read failed"),
            ):
                self.helper.filesystem_probes(probe_root)

            self.assertEqual(len(descriptors), 2)
            for descriptor in descriptors:
                with self.assertRaises(OSError) as caught:
                    os.fstat(descriptor)
                self.assertEqual(caught.exception.errno, self.helper.errno.EBADF)
            self.assertEqual(len(children), 1)
            with self.assertRaises(ChildProcessError):
                os.waitpid(children[0], os.WNOHANG)
            self.assertFalse(probe_root.exists())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and hasattr(os, "pipe2"),
        "requires the Linux filesystem-probe surface",
    )
    def test_filesystem_probes_do_not_retry_a_transferred_close(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            probe_root = Path(raw_root) / "filesystem-probes"
            parent = os.getpid()
            descriptors: list[int] = []
            children: list[int] = []
            replacements: list[int] = []
            pipe2 = self.helper.os.pipe2
            fork = self.helper.os.fork
            close = self.helper.os.close

            def observe_pipe2(flags: int) -> tuple[int, int]:
                observed = pipe2(flags)
                descriptors.extend(observed)
                return observed

            def observe_fork() -> int:
                observed = fork()
                if observed > 0:
                    children.append(observed)
                return observed

            def interrupt_parent_write_close(descriptor: int) -> None:
                if (
                    os.getpid() == parent
                    and len(descriptors) == 2
                    and descriptor == descriptors[1]
                    and not replacements
                ):
                    close(descriptor)
                    replacement = os.open(
                        "/dev/null",
                        os.O_RDONLY | os.O_CLOEXEC,
                    )
                    replacements.append(replacement)
                    if replacement != descriptor:
                        raise AssertionError("closed descriptor was not reused")
                    raise InterruptedError(
                        self.helper.errno.EINTR,
                        "parent close interrupted",
                    )
                close(descriptor)

            try:
                with (
                    mock.patch.object(
                        self.helper.os,
                        "pipe2",
                        side_effect=observe_pipe2,
                    ),
                    mock.patch.object(
                        self.helper.os,
                        "fork",
                        side_effect=observe_fork,
                    ),
                    mock.patch.object(
                        self.helper.os,
                        "close",
                        side_effect=interrupt_parent_write_close,
                    ),
                    self.assertRaisesRegex(
                        InterruptedError,
                        "parent close interrupted",
                    ),
                ):
                    self.helper.filesystem_probes(probe_root)

                self.assertEqual(replacements, [descriptors[1]])
                os.fstat(replacements[0])
                self.assertEqual(len(children), 1)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(children[0], os.WNOHANG)
                self.assertFalse(probe_root.exists())
            finally:
                for descriptor in replacements:
                    try:
                        close(descriptor)
                    except OSError:
                        pass

    def test_runtime_inventory_roles_main_and_loader_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            executable = root / "python3.13"
            executable.write_bytes(b"not-an-elf")
            executable.chmod(0o755)
            loader_root = root / "lib" / "task-witness-loader"
            loader_root.mkdir(parents=True)
            loader = loader_root / "libc.so.6"
            loader.write_bytes(b"not-an-elf-either")
            main = self.helper.live_entry(executable, root, executable)
            retained = self.helper.live_entry(loader, root, executable)
            self.assertEqual(main["role"], "main-executable")
            self.assertEqual(retained["role"], "runtime-resource")
            self.assertEqual(main["sha256"], hashlib.sha256(b"not-an-elf").hexdigest())

    def test_runtime_inventory_uses_serialized_path_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            runtime_root = Path(raw_root) / "runtime"
            executable = runtime_root / "bin" / "python"
            stdlib = runtime_root / "lib" / "python3.13"
            gdb_helper = runtime_root / "lib" / "python3.13-gdb.py"
            stdlib_module = stdlib / "os.py"
            executable.parent.mkdir(parents=True)
            stdlib.mkdir(parents=True)
            executable.write_bytes(b"python")
            executable.chmod(0o755)
            gdb_helper.write_bytes(b"gdb")
            stdlib_module.write_bytes(b"stdlib")

            entries = self.helper.runtime_inventory_entries(
                runtime_root,
                executable,
            )

            paths = [entry["path"] for entry in entries]
            self.assertEqual(paths, sorted(paths))
            self.assertEqual(len(paths), len(set(paths)))
            self.assertNotIn(str(runtime_root), paths)
            self.assertEqual(
                set(paths),
                {str(path) for path in runtime_root.rglob("*")},
            )
            self.assertLess(
                paths.index(str(gdb_helper)),
                paths.index(str(stdlib_module)),
            )

    @unittest.skipUnless(
        sys.version_info >= (3, 11),
        "the candidate qualification runner requires Python 3.11 or newer",
    )
    def test_runtime_inventory_matches_candidate_descendant_scan(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            runtime_root = Path(raw_root).resolve() / "runtime"
            executable = runtime_root / "bin" / "python"
            module = runtime_root / "lib" / "python3.13" / "module.py"
            executable.parent.mkdir(parents=True)
            module.parent.mkdir(parents=True)
            executable.write_bytes(b"python")
            executable.chmod(0o755)
            module.write_bytes(b"VALUE = 1\n")
            entries = self.helper.runtime_inventory_entries(
                runtime_root,
                executable,
            )
            evidence = {
                "main_executable": {"path": str(executable)},
                "closure": {
                    "roots": [{"path": str(runtime_root)}],
                    "entries": entries,
                },
            }
            candidate = load_candidate_runner()

            with mock.patch.object(
                candidate,
                "require_runtime_closure_immutable",
            ):
                observed = candidate.validate_runtime_observations(
                    executable,
                    evidence,
                )

            paths = [entry["path"] for entry in entries]
            expected_descendants = {str(path) for path in runtime_root.rglob("*")}
            self.assertEqual(paths, sorted(set(paths)))
            self.assertEqual(set(paths), expected_descendants)
            self.assertNotIn(str(runtime_root), paths)
            self.assertEqual(
                observed["main_executable_observation"]["path"],
                str(executable),
            )
            self.assertEqual(
                [item["path"] for item in observed["closure_observation"]["roots"]],
                [str(runtime_root)],
            )
            self.assertEqual(
                [item["path"] for item in observed["closure_observation"]["entries"]],
                paths,
            )

    def test_runtime_sealing_prunes_only_source_backed_bytecode_caches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            runtime_root = Path(raw_root).resolve() / "runtime"
            package = runtime_root / "lib" / "python3.13" / "package"
            cache = package / "__pycache__"
            cache.mkdir(parents=True)
            source = package / "module.py"
            source_raw = b"VALUE = 1\n"
            source.write_bytes(source_raw)
            retained = package / "data.txt"
            retained.write_bytes(b"runtime data")
            bytecode = {
                cache / "module.cpython-313.pyc": b"bytecode-default",
                cache / "module.cpython-313.opt-1.pyc": b"bytecode-optimized",
            }
            for path, raw in bytecode.items():
                path.write_bytes(raw)
            before_inventory = self.helper.runtime_pruning_inventory(runtime_root)
            before_by_path = {entry["path"]: entry for entry in before_inventory}
            source_bindings = [
                {
                    "bytecode_path": path.relative_to(runtime_root).as_posix(),
                    "source_path": source.relative_to(runtime_root).as_posix(),
                    "source_length": len(source_raw),
                    "source_sha256": hashlib.sha256(source_raw).hexdigest(),
                }
                for path, raw in sorted(bytecode.items(), key=lambda item: str(item[0]))
            ]

            audit = self.helper.prune_source_backed_bytecode_caches(runtime_root)

            after_inventory = self.helper.runtime_pruning_inventory(runtime_root)
            removed_paths = sorted(
                [
                    cache.relative_to(runtime_root).as_posix(),
                    *(path.relative_to(runtime_root).as_posix() for path in bytecode),
                ]
            )
            removed_inventory = [before_by_path[path] for path in removed_paths]
            self.assertEqual(
                audit,
                self.helper.content_document(
                    {
                        "schema_version": 1,
                        "contract": ("task-witness-cpython-bytecode-cache-pruning-v1"),
                        "cache_tag": "cpython-313",
                        "policy": (
                            "remove-only-source-backed-cpython-bytecode-caches-v1"
                        ),
                        "before_inventory": {
                            "entry_count": len(before_inventory),
                            "sha256": self.helper.framed_pruning_inventory_sha256(
                                "before-bytecode-cache-pruning",
                                before_inventory,
                            ),
                        },
                        "removed_inventory": {
                            "entry_count": 3,
                            "cache_directory_count": 1,
                            "bytecode_file_count": 2,
                            "bytecode_total_bytes": sum(map(len, bytecode.values())),
                            "sha256": self.helper.framed_pruning_inventory_sha256(
                                "removed-bytecode-cache-inventory",
                                removed_inventory,
                            ),
                            "entries": removed_inventory,
                        },
                        "source_bindings": {
                            "source_file_count": 1,
                            "sha256": self.helper.framed_pruning_inventory_sha256(
                                "retained-source-bindings",
                                source_bindings,
                            ),
                            "entries": source_bindings,
                        },
                        "after_inventory": {
                            "entry_count": len(after_inventory),
                            "sha256": self.helper.framed_pruning_inventory_sha256(
                                "after-bytecode-cache-pruning",
                                after_inventory,
                            ),
                        },
                        "disposition": "pruned-source-backed-bytecode-caches",
                    }
                ),
            )
            self.assertFalse(cache.exists())
            self.assertEqual(source.read_bytes(), source_raw)
            self.assertEqual(retained.read_bytes(), b"runtime data")
            imported = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    (
                        "import importlib.util,sys;"
                        "spec=importlib.util.spec_from_file_location('module',sys.argv[1]);"
                        "module=importlib.util.module_from_spec(spec);"
                        "spec.loader.exec_module(module);"
                        "assert module.VALUE == 1"
                    ),
                    str(source),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertFalse(cache.exists())

    def test_runtime_cache_pruning_reduces_complete_inventory_below_input_cap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            runtime_root = Path(raw_root).resolve() / "runtime"
            executable = runtime_root / "bin" / "python"
            stdlib = runtime_root / "lib" / "python3.13"
            cache = stdlib / "__pycache__"
            executable.parent.mkdir(parents=True)
            cache.mkdir(parents=True)
            executable.write_bytes(b"python")
            executable.chmod(0o755)
            for index in range(2100):
                stem = f"qualification_module_{index:04d}"
                (stdlib / f"{stem}.py").write_bytes(b"VALUE = 1\n")
                (cache / f"{stem}.cpython-313.pyc").write_bytes(b"bytecode")

            before = self.helper.canonical_bytes(
                self.helper.runtime_inventory_entries(runtime_root, executable)
            )
            self.assertGreater(len(before), 1024 * 1024)

            audit = self.helper.prune_source_backed_bytecode_caches(runtime_root)

            after = self.helper.canonical_bytes(
                self.helper.runtime_inventory_entries(runtime_root, executable)
            )
            self.assertEqual(
                audit["removed_inventory"]["bytecode_file_count"],
                2100,
            )
            self.assertLess(len(after), (1024 * 1024) - 200_000)

            oversized = {
                "entries": [
                    {
                        "path": f"residual-runtime-resource-{index:05d}",
                        "kind": "regular-file",
                        "sha256": "0" * 64,
                    }
                    for index in range(8000)
                ]
            }
            self.assertGreater(
                len(self.helper.canonical_bytes(oversized)),
                self.helper.RUNNER_INPUT_CAP_BYTES,
            )
            output = Path(raw_root).resolve() / "runtime-closure-evidence.json"
            with self.assertRaisesRegex(
                self.helper.PreparationError,
                "runtime closure evidence exceeds",
            ):
                self.helper.write_runtime_closure_evidence(output, oversized)
            self.assertFalse(output.exists())

    def test_runtime_cache_pruning_rejects_unsafe_or_sourceless_entries(
        self,
    ) -> None:
        cases = (
            "unexpected-entry",
            "missing-source",
            "wrong-cache-tag",
            "linked-bytecode",
            "symlink-source",
            "writable-bytecode",
            "writable-source",
            "linked-source",
            "legacy-bytecode",
            "special-bytecode",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_root:
                runtime_root = Path(raw_root).resolve() / "runtime"
                valid_package = runtime_root / "a"
                valid_cache = valid_package / "__pycache__"
                valid_cache.mkdir(parents=True)
                (valid_package / "valid.py").write_bytes(b"valid source")
                valid_bytecode = valid_cache / "valid.cpython-313.pyc"
                valid_bytecode.write_bytes(b"valid bytecode")

                package = runtime_root / "z"
                cache = package / "__pycache__"
                cache.mkdir(parents=True)
                source = package / "module.py"
                source.write_bytes(b"source")
                bytecode = cache / "module.cpython-313.pyc"
                bytecode.write_bytes(b"bytecode")
                if case == "unexpected-entry":
                    (cache / "README").write_bytes(b"not bytecode")
                elif case == "missing-source":
                    source.unlink()
                elif case == "wrong-cache-tag":
                    bytecode.rename(cache / "module.cpython-312.pyc")
                elif case == "linked-bytecode":
                    os.link(bytecode, runtime_root / "bytecode-alias")
                elif case == "symlink-source":
                    source.unlink()
                    outside = Path(raw_root).resolve() / "source-target"
                    outside.write_bytes(b"source")
                    source.symlink_to(outside)
                elif case == "writable-bytecode":
                    bytecode.chmod(0o666)
                elif case == "writable-source":
                    source.chmod(0o666)
                elif case == "linked-source":
                    os.link(source, runtime_root / "source-alias")
                elif case == "legacy-bytecode":
                    (runtime_root / "legacy.pyc").write_bytes(b"legacy")
                elif case == "special-bytecode":
                    bytecode.unlink()
                    os.mkfifo(bytecode)

                with self.assertRaisesRegex(
                    self.helper.PreparationError,
                    "runtime bytecode cache",
                ):
                    self.helper.prune_source_backed_bytecode_caches(runtime_root)

                self.assertTrue(valid_bytecode.is_file())
                self.assertTrue(valid_cache.is_dir())

    def test_retained_sources_compile_with_exact_unprivileged_runtime(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            runtime_root = Path(raw_root).resolve() / "runtime"
            executable = runtime_root / "bin" / "python"
            source = runtime_root / "lib" / "python3.13" / "module.py"
            executable.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            executable.write_bytes(b"python")
            executable.chmod(0o755)
            source.write_bytes(b"VALUE = 1\n")
            source_bindings = [
                {
                    "bytecode_path": (
                        "lib/python3.13/__pycache__/module.cpython-313.pyc"
                    ),
                    "source_path": "lib/python3.13/module.py",
                    "source_length": source.stat().st_size,
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ]
            observed = {
                "cache_tag": "cpython-313",
                "compiled_source_count": 1,
                "version": [3, 13, 7],
            }
            process = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=self.helper.canonical_bytes(observed).decode() + "\n",
                stderr="",
            )

            with mock.patch.object(
                self.helper,
                "run",
                return_value=process,
            ) as run:
                audit = self.helper.compile_retained_runtime_sources(
                    runtime_root,
                    executable,
                    source_bindings,
                    1001,
                    1002,
                )

            invoked = run.call_args.args[0]
            self.assertEqual(invoked[-5], str(executable))
            self.assertEqual(invoked[-4:-1], ["-I", "-B", "-c"])
            self.assertIn("--reuid=1001", invoked)
            self.assertIn("--regid=1002", invoked)
            self.assertEqual(
                json.loads(run.call_args.kwargs["input_text"]),
                [str(source)],
            )
            self.assertEqual(audit["compiled_source_count"], 1)
            self.assertEqual(audit["version"], [3, 13, 7])
            self.assertEqual(
                audit["disposition"],
                "compiled-in-memory-with-exact-runtime",
            )

    def test_retained_source_compilation_rejects_mismatch_and_cache_recreation(
        self,
    ) -> None:
        for case in ("mismatch", "cache-recreated", "unsafe-binding"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_root:
                runtime_root = Path(raw_root).resolve() / "runtime"
                executable = runtime_root / "bin" / "python"
                source = runtime_root / "lib" / "python3.13" / "module.py"
                executable.parent.mkdir(parents=True)
                source.parent.mkdir(parents=True)
                executable.write_bytes(b"python")
                source.write_bytes(b"VALUE = 1\n")
                source_bindings = [
                    {
                        "bytecode_path": (
                            "lib/python3.13/__pycache__/module.cpython-313.pyc"
                        ),
                        "source_path": (
                            "../escape.py"
                            if case == "unsafe-binding"
                            else "lib/python3.13/module.py"
                        ),
                        "source_length": source.stat().st_size,
                        "source_sha256": hashlib.sha256(
                            source.read_bytes()
                        ).hexdigest(),
                    }
                ]
                observed = {
                    "cache_tag": "cpython-313",
                    "compiled_source_count": 1,
                    "version": [3, 13, 8] if case == "mismatch" else [3, 13, 7],
                }
                process = subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=self.helper.canonical_bytes(observed).decode() + "\n",
                    stderr="",
                )

                def run_with_side_effect(
                    *_args,
                    observed_case=case,
                    observed_source=source,
                    observed_process=process,
                    **_kwargs,
                ):
                    if observed_case == "cache-recreated":
                        cache = observed_source.parent / "__pycache__"
                        cache.mkdir()
                        (cache / "module.cpython-313.pyc").write_bytes(b"cache")
                    return observed_process

                with (
                    mock.patch.object(
                        self.helper,
                        "run",
                        side_effect=run_with_side_effect,
                    ),
                    self.assertRaises(self.helper.PreparationError),
                ):
                    self.helper.compile_retained_runtime_sources(
                        runtime_root,
                        executable,
                        source_bindings,
                        1001,
                        1001,
                    )

    def test_patchelf_interpreter_requires_direct_elf_agreement(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            interpreter = "/opt/task-witness/runtime/lib/ld-linux-x86-64.so.2"
            executable = root / "executable"
            shared_object = root / "shared-object"
            multiple = root / "multiple"
            malformed = root / "malformed"
            self.write_elf(executable, [interpreter], dynamic=True)
            self.write_elf(shared_object, [], dynamic=True)
            self.write_elf(multiple, [interpreter, interpreter], dynamic=True)
            malformed.write_bytes(b"\x7fELFtruncated")

            success = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=f"{interpreter}\n",
                stderr="",
            )
            no_interpreter = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr=f"{self.helper.PATCHELF_NO_INTERPRETER_DIAGNOSTIC}\n",
            )
            with mock.patch.object(self.helper, "run", return_value=success):
                self.assertEqual(
                    self.helper.patchelf_interpreter(executable),
                    interpreter,
                )
            with mock.patch.object(
                self.helper,
                "run",
                return_value=no_interpreter,
            ):
                self.assertIsNone(self.helper.patchelf_interpreter(shared_object))
                with self.assertRaises(self.helper.PreparationError):
                    self.helper.patchelf_interpreter(executable)
            for path in (multiple, malformed):
                with (
                    self.subTest(path=path),
                    self.assertRaises(self.helper.PreparationError),
                ):
                    self.helper.patchelf_interpreter(path)

    def test_dynamic_elf_discovery_uses_headers_and_rejects_probe_errors(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            shared_object = root / "shared-object.so"
            static_object = root / "static-object"
            self.write_elf(shared_object, [], dynamic=True)
            self.write_elf(static_object, [])
            no_interpreter = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr=f"{self.helper.PATCHELF_NO_INTERPRETER_DIAGNOSTIC}\n",
            )
            unexpected = subprocess.CompletedProcess(
                args=[],
                returncode=2,
                stdout="",
                stderr="patchelf: unexpected inspection failure\n",
            )

            with mock.patch.object(
                self.helper,
                "run",
                return_value=no_interpreter,
            ) as run:
                self.assertEqual(
                    self.helper.dynamic_elf_files(root),
                    [shared_object],
                )
            run.assert_called_once_with(
                [
                    "/usr/bin/patchelf",
                    "--print-interpreter",
                    str(shared_object),
                ],
                check=False,
            )
            with (
                mock.patch.object(
                    self.helper,
                    "run",
                    return_value=unexpected,
                ),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.dynamic_elf_files(root)

    def test_ldd_bindings_preserve_a_needed_soname_alias_for_regular_copy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            executable = root / "runtime" / "lib" / "_bz2.so"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"elf")
            source = root / "host" / "libbz2.so.1.0.4"
            loader = root / "host" / "ld-linux-x86-64.so.2"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"libbz2-bytes")
            loader.write_bytes(b"loader-bytes")
            process = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    f"libbz2.so.1.0 => {source} (0x0000000000000001)\n"
                    f"{loader} (0x0000000000000002)\n"
                ),
                stderr="",
            )

            with mock.patch.object(self.helper, "run", return_value=process):
                needed, loader_needed, transitive, auxiliary, _output = (
                    self.helper.ldd_dependency_bindings(
                        executable,
                        1001,
                        1001,
                        ["libbz2.so.1.0"],
                    )
                )

            self.assertEqual(needed, {"libbz2.so.1.0": source.resolve()})
            self.assertEqual(loader_needed, {})
            self.assertEqual(transitive, {})
            self.assertEqual(auxiliary, {str(loader): loader.resolve()})
            retained_root = root / "retained"
            retained_root.mkdir()
            destination = self.helper.retain_dependency_alias(
                retained_root,
                "libbz2.so.1.0",
                source,
            )
            self.assertEqual(destination.name, "libbz2.so.1.0")
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertFalse(destination.is_symlink())
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.stat().st_mode & 0o222, 0)

    def test_patchelf_needed_rejects_unsafe_or_ambiguous_names(self) -> None:
        target = Path("/tmp/task-witness-needed-fixture")
        cases = {
            "parent-traversal": "../libescape.so\n",
            "slash": "nested/libescape.so\n",
            "absolute": "/lib/libescape.so\n",
            "duplicate": "libsame.so\nlibsame.so\n",
        }
        for name, stdout in cases.items():
            process = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=stdout,
                stderr="",
            )
            with (
                self.subTest(name=name),
                mock.patch.object(self.helper, "run", return_value=process),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.patchelf_needed(target)

    def test_ldd_bindings_support_transitive_rows_and_loader_needed_names(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            target = root / "apt"
            direct = root / "libapt.so.1"
            transitive = root / "libz.so.1"
            loader = root / "ld-linux-x86-64.so.2"
            for path in (target, direct, transitive, loader):
                path.write_bytes(b"fixture")
            process = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    f"libapt.so.1 => {direct} (0x1)\n"
                    f"{loader} (0x2)\n"
                    f"libz.so.1 => {transitive} (0x3)\n"
                ),
                stderr="",
            )

            with mock.patch.object(self.helper, "run", return_value=process):
                (
                    direct_bindings,
                    loader_needed_bindings,
                    transitive_bindings,
                    auxiliary,
                    _output,
                ) = self.helper.ldd_dependency_bindings(
                    target,
                    1001,
                    1001,
                    ["libapt.so.1", "ld-linux-x86-64.so.2"],
                )

            self.assertEqual(
                direct_bindings,
                {
                    "libapt.so.1": direct.resolve(),
                },
            )
            self.assertEqual(
                loader_needed_bindings,
                {"ld-linux-x86-64.so.2": loader.resolve()},
            )
            self.assertEqual(
                transitive_bindings,
                {"libz.so.1": transitive.resolve()},
            )
            self.assertEqual(auxiliary, {str(loader): loader.resolve()})
            self.helper.validate_dependency_alias_inventory(
                {"libapt.so.1", "ld-linux-x86-64.so.2", "libz.so.1"},
                set(direct_bindings)
                | set(loader_needed_bindings)
                | set(transitive_bindings),
                "fixture",
            )

    def test_retained_loader_trace_separates_loader_needed_binding(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            target = root / "libc.so.6"
            direct = root / "libc-helper.so.1"
            retained_loader = root / "ld-linux-x86-64.so.2"
            for path in (target, direct, retained_loader):
                path.write_bytes(b"fixture")
            process = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    "\t (0x1)\n"
                    f"libc-helper.so.1 => {direct} (0x2)\n"
                    f"/lib64/ld-linux-x86-64.so.2 => {retained_loader} (0x3)\n"
                    "linux-vdso.so.1 (0x4)\n"
                ),
                stderr="",
            )

            with mock.patch.object(self.helper, "run", return_value=process) as run:
                direct_bindings, loader_needed, transitive, auxiliary, _output = (
                    self.helper.ldd_dependency_bindings(
                        target,
                        1001,
                        1001,
                        ["libc-helper.so.1", "ld-linux-x86-64.so.2"],
                        tracer=retained_loader,
                        library_path=root,
                    )
                )

            self.assertEqual(
                direct_bindings,
                {"libc-helper.so.1": direct.resolve()},
            )
            self.assertEqual(
                loader_needed,
                {"ld-linux-x86-64.so.2": retained_loader.resolve()},
            )
            self.assertEqual(transitive, {})
            self.assertEqual(
                auxiliary,
                {"/lib64/ld-linux-x86-64.so.2": retained_loader.resolve()},
            )
            run.assert_called_once_with(
                self.helper.unprivileged_argv(
                    1001,
                    1001,
                    [
                        str(retained_loader),
                        "--inhibit-cache",
                        "--library-path",
                        str(root),
                        "--list",
                        str(target),
                    ],
                    home="/nonexistent-task-witness-ldd-home",
                ),
                check=False,
            )

            repeated_main = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="\t (0x1)\n\t (0x2)\n",
                stderr="",
            )
            with (
                mock.patch.object(self.helper, "run", return_value=repeated_main),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.ldd_dependency_bindings(
                    target,
                    1001,
                    1001,
                    [],
                    tracer=retained_loader,
                    library_path=root,
                )

    def test_retained_loader_self_trace_accepts_empty_dynamic_sentinel(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            retained_loader = root / "ld-linux-x86-64.so.2"
            self.write_elf(retained_loader, [], dynamic=True)
            statically_linked = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="statically linked\n",
                stderr="",
            )
            with mock.patch.object(
                self.helper,
                "run",
                return_value=statically_linked,
            ) as run:
                result = self.helper.ldd_dependency_bindings(
                    retained_loader,
                    1001,
                    1001,
                    [],
                    tracer=retained_loader,
                    library_path=root,
                )

            self.assertEqual(result[:4], ({}, {}, {}, {}))
            self.assertEqual(result[4], "statically linked\n")
            run.assert_called_once_with(
                self.helper.unprivileged_argv(
                    1001,
                    1001,
                    [
                        str(retained_loader),
                        "--inhibit-cache",
                        "--library-path",
                        str(root),
                        "--list",
                        str(retained_loader),
                    ],
                    home="/nonexistent-task-witness-ldd-home",
                ),
                check=False,
            )

    def test_dependency_free_dynamic_shared_object_accepts_empty_ldd_trace(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            shared_object = root / "_contextvars.cpython-313-x86_64-linux-gnu.so"
            retained_loader = root / "ld-linux-x86-64.so.2"
            self.write_elf(shared_object, [], dynamic=True)
            retained_loader.write_bytes(b"retained-loader")
            statically_linked = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="\tstatically linked\n",
                stderr="",
            )

            for name, tracer, trace_argv in (
                ("host-ldd", None, ["/usr/bin/ldd", str(shared_object)]),
                (
                    "retained-loader",
                    retained_loader,
                    [
                        str(retained_loader),
                        "--inhibit-cache",
                        "--library-path",
                        str(root),
                        "--list",
                        str(shared_object),
                    ],
                ),
            ):
                with (
                    self.subTest(name=name),
                    mock.patch.object(
                        self.helper,
                        "run",
                        return_value=statically_linked,
                    ) as run,
                ):
                    result = self.helper.ldd_dependency_bindings(
                        shared_object,
                        1001,
                        1001,
                        [],
                        tracer=tracer,
                        library_path=root if tracer is not None else None,
                    )
                self.assertEqual(result[:4], ({}, {}, {}, {}))
                self.assertEqual(result[4], "\tstatically linked\n")
                run.assert_called_once_with(
                    self.helper.unprivileged_argv(
                        1001,
                        1001,
                        trace_argv,
                        home="/nonexistent-task-witness-ldd-home",
                    ),
                    check=False,
                )

    def test_empty_ldd_trace_requires_dependency_free_dynamic_shared_object(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            shared_object = root / "shared-object.so"
            static_elf = root / "static-elf"
            interpreted_elf = root / "interpreted-elf"
            malformed_elf = root / "malformed-elf"
            self.write_elf(shared_object, [], dynamic=True)
            self.write_elf(static_elf, [])
            self.write_elf(
                interpreted_elf,
                ["/lib64/ld-linux-x86-64.so.2"],
                dynamic=True,
            )
            malformed_elf.write_bytes(b"\x7fELFmalformed")
            clean = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="statically linked\n",
                stderr="",
            )
            nonzero = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="statically linked\n",
                stderr="",
            )
            stderr = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="statically linked\n",
                stderr="unexpected\n",
            )
            extra_row = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="statically linked\nlinux-vdso.so.1 (0x1)\n",
                stderr="",
            )

            for name, path, expected, process in (
                ("no-pt-dynamic", static_elf, [], clean),
                ("has-interpreter", interpreted_elf, [], clean),
                ("claimed-needed", shared_object, ["libc.so.6"], clean),
                ("nonzero", shared_object, [], nonzero),
                ("stderr", shared_object, [], stderr),
                ("extra-row", shared_object, [], extra_row),
                ("malformed-elf", malformed_elf, [], clean),
            ):
                with (
                    self.subTest(name=name),
                    mock.patch.object(self.helper, "run", return_value=process),
                    self.assertRaises(self.helper.PreparationError),
                ):
                    self.helper.ldd_dependency_bindings(
                        path,
                        1001,
                        1001,
                        expected,
                    )

            for tracer, library_path in ((root / "loader", None), (None, root)):
                with self.assertRaises(self.helper.PreparationError):
                    self.helper.ldd_dependency_bindings(
                        shared_object,
                        1001,
                        1001,
                        [],
                        tracer=tracer,
                        library_path=library_path,
                    )

    def test_loader_needed_edges_stay_separate_from_labeled_recursive_graph(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            root_elf = root / "runtime" / "bin" / "python"
            loader_root = root / "runtime" / "lib" / "task-witness-loader"
            retained_libc = loader_root / "libc.so.6"
            retained_loader = loader_root / "ld-linux-x86-64.so.2"
            host_libc = root / "host" / "libc.so.6"
            host_loader = root / "host" / "ld-linux-x86-64.so.2"
            for path in (
                root_elf,
                retained_libc,
                retained_loader,
                host_libc,
                host_loader,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

            processes = [
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(f"libc.so.6 => {host_libc} (0x1)\n{host_loader} (0x2)\n"),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=f"{host_loader} (0x1)\n",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "\t (0x1)\n"
                        f"libc.so.6 => {retained_libc} (0x2)\n"
                        f"/lib64/ld-linux-x86-64.so.2 => {retained_loader} (0x3)\n"
                        "linux-vdso.so.1 (0x4)\n"
                    ),
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=(
                        "\t (0x1)\n"
                        f"/lib64/ld-linux-x86-64.so.2 => {retained_loader} (0x2)\n"
                        "linux-vdso.so.1 (0x3)\n"
                    ),
                    stderr="",
                ),
            ]
            with mock.patch.object(self.helper, "run", side_effect=processes):
                pre_root = self.helper.ldd_dependency_bindings(
                    root_elf,
                    1001,
                    1001,
                    ["libc.so.6"],
                )
                pre_libc = self.helper.ldd_dependency_bindings(
                    retained_libc,
                    1001,
                    1001,
                    ["ld-linux-x86-64.so.2"],
                )
                post_root = self.helper.ldd_dependency_bindings(
                    root_elf,
                    1001,
                    1001,
                    ["libc.so.6"],
                    tracer=retained_loader,
                    library_path=loader_root,
                )
                post_libc = self.helper.ldd_dependency_bindings(
                    retained_libc,
                    1001,
                    1001,
                    ["ld-linux-x86-64.so.2"],
                    tracer=retained_loader,
                    library_path=loader_root,
                )

            self.assertEqual(pre_root[0], {"libc.so.6": host_libc.resolve()})
            self.assertEqual(pre_root[1], {})
            self.assertEqual(
                pre_root[3],
                {str(host_loader): host_loader.resolve()},
            )
            self.assertEqual(
                pre_libc[1],
                {"ld-linux-x86-64.so.2": host_loader.resolve()},
            )
            self.assertEqual(post_root[0], {"libc.so.6": retained_libc.resolve()})
            self.assertEqual(post_root[1], {})
            self.assertEqual(
                post_root[3],
                {"/lib64/ld-linux-x86-64.so.2": retained_loader.resolve()},
            )
            self.assertEqual(
                post_libc[1],
                {"ld-linux-x86-64.so.2": retained_loader.resolve()},
            )

            for name, root_bindings, libc_bindings in (
                ("pre-rewrite", pre_root, pre_libc),
                ("post-rewrite", post_root, post_libc),
            ):
                with self.subTest(name=name):
                    rows = [
                        self.helper.labeled_dependency_graph_row(
                            root_elf,
                            root_bindings[0],
                            root_bindings[2],
                        ),
                        self.helper.labeled_dependency_graph_row(
                            retained_libc,
                            libc_bindings[0],
                            libc_bindings[2],
                        ),
                        self.helper.labeled_dependency_graph_row(
                            retained_loader,
                            [],
                            [],
                        ),
                    ]
                    self.helper.validate_dependency_graph(
                        rows,
                        loader_root,
                        name,
                    )

    def test_ldd_bindings_reject_unresolved_missing_and_ambiguous_rows(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            target = root / "target.so"
            target.write_bytes(b"elf")
            source = root / "libgood.so.1.2"
            other = root / "libother.so.9"
            loader = root / "ld-linux.so"
            source.write_bytes(b"good")
            other.write_bytes(b"other")
            loader.write_bytes(b"loader")
            cases = {
                "unresolved": "libgood.so.1 => not found\n",
                "missing": f"{loader} (0x1)\n",
                "ambiguous": (
                    f"libgood.so.1 => {source} (0x1)\nlibgood.so.1 => {other} (0x2)\n"
                ),
                "misleading-label": (
                    f"libgood.so.1 => {source} (0x1)\nlibother.so.9 (0x2)\n"
                ),
            }
            for name, stdout in cases.items():
                process = subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=stdout,
                    stderr="",
                )
                with (
                    self.subTest(name=name),
                    mock.patch.object(self.helper, "run", return_value=process),
                    self.assertRaises(self.helper.PreparationError),
                ):
                    self.helper.ldd_dependency_bindings(
                        target,
                        1001,
                        1001,
                        ["libgood.so.1"],
                    )

    def test_recursive_dependency_graph_rejects_missing_and_misleading_rows(
        self,
    ) -> None:
        loader_root = Path("/tmp/task-witness-loader-fixture")
        root_path = Path("/tmp/task-witness-runtime/bin/python")
        unrelated_root = Path("/tmp/task-witness-runtime/bin/unrelated")
        rows = [
            {
                "path": str(root_path),
                "direct_aliases": ["libdirect.so"],
                "observed_aliases": ["libdirect.so", "libtransitive.so"],
            },
            {
                "path": str(loader_root / "libdirect.so"),
                "direct_aliases": ["libtransitive.so"],
                "observed_aliases": ["libtransitive.so"],
            },
            {
                "path": str(loader_root / "libtransitive.so"),
                "direct_aliases": [],
                "observed_aliases": [],
            },
            {
                "path": str(unrelated_root),
                "direct_aliases": ["libunrelated.so"],
                "observed_aliases": ["libunrelated.so"],
            },
            {
                "path": str(loader_root / "libunrelated.so"),
                "direct_aliases": [],
                "observed_aliases": [],
            },
        ]
        self.helper.validate_dependency_graph(rows, loader_root, "fixture")

        misleading = [dict(row) for row in rows]
        misleading[0]["observed_aliases"] = [
            "libdirect.so",
            "libtransitive.so",
            "libunrelated.so",
        ]
        with self.assertRaises(self.helper.PreparationError):
            self.helper.validate_dependency_graph(
                misleading,
                loader_root,
                "fixture",
            )

        with self.assertRaises(self.helper.PreparationError):
            self.helper.validate_dependency_graph(
                [
                    {
                        "path": str(root_path),
                        "direct_aliases": ["libmissing.so"],
                        "observed_aliases": ["libmissing.so"],
                    }
                ],
                loader_root,
                "fixture",
            )

    def test_retained_alias_rejects_conflicting_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            retained_root = root / "retained"
            retained_root.mkdir()
            bindings = {}
            first = root / "libfirst.so.1.2"
            second = root / "libsecond.so.1.3"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            destination, created = self.helper.retain_dependency_binding(
                bindings,
                retained_root,
                "libshared.so.1",
                first,
            )
            self.assertTrue(created)
            self.assertEqual(destination.name, "libshared.so.1")
            with self.assertRaises(self.helper.PreparationError):
                self.helper.retain_dependency_binding(
                    bindings,
                    retained_root,
                    "libshared.so.1",
                    second,
                )

    def test_unsafe_dependency_source_reports_constant_disposition_fields(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            loader_root = root / "loader"
            loader_root.mkdir()
            source = root / "libunsafe.so.1.2"
            source.write_bytes(b"unsafe")
            source.chmod(0o666)
            metadata = source.stat()

            with self.assertRaises(self.helper.PreparationError) as raised:
                self.helper.retain_dependency_alias(
                    loader_root,
                    "libunsafe.so.1",
                    source,
                )

            self.assertEqual(
                str(raised.exception),
                (
                    "loader dependency source disposition is unsafe: "
                    "requested_name='libunsafe.so.1', "
                    f"source={str(source)!r}, "
                    f"uid={metadata.st_uid}, "
                    f"gid={metadata.st_gid}, "
                    f"mode={metadata.st_mode:#o}, "
                    f"nlink={metadata.st_nlink}"
                ),
            )

    def test_source_root_dependency_uses_qualified_runtime_relocation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            source_root = root / "source"
            runtime_root = root / "runtime"
            loader_root = runtime_root / "lib" / "task-witness-loader"
            observed = source_root / "lib" / "libpython3.13.so.1.0"
            relocated = runtime_root / "lib" / "libpython3.13.so.1.0"
            observed.parent.mkdir(parents=True)
            relocated.parent.mkdir(parents=True)
            loader_root.mkdir()
            observed.write_bytes(b"libpython")
            observed.chmod(0o777)
            relocated.write_bytes(b"libpython")
            relocated.chmod(0o755)

            copy_source, evidence = self.helper.qualify_dependency_source(
                observed,
                source_root,
                runtime_root,
                "libpython3.13.so.1.0",
            )
            bindings = {}
            destination, created = self.helper.retain_dependency_binding(
                bindings,
                loader_root,
                "libpython3.13.so.1.0",
                copy_source,
                source_evidence=evidence,
            )

            digest = hashlib.sha256(b"libpython").hexdigest()
            self.assertTrue(created)
            self.assertEqual(copy_source, relocated)
            self.assertEqual(destination.read_bytes(), b"libpython")
            self.assertEqual(
                bindings["libpython3.13.so.1.0"]["resolved_source"],
                str(observed),
            )
            self.assertEqual(
                bindings["libpython3.13.so.1.0"]["observed_source_sha256"],
                digest,
            )
            self.assertEqual(
                bindings["libpython3.13.so.1.0"]["qualified_relocation"],
                str(relocated),
            )
            self.assertEqual(
                bindings["libpython3.13.so.1.0"]["qualified_relocation_copy_sha256"],
                digest,
            )

    def test_source_root_relocation_rejects_unsafe_or_inexact_copies(self) -> None:
        cases = ("missing", "different", "symlink", "writable")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root).resolve()
                source_root = root / "source"
                runtime_root = root / "runtime"
                observed = source_root / "lib" / "libpython3.13.so.1.0"
                relocated = runtime_root / "lib" / "libpython3.13.so.1.0"
                observed.parent.mkdir(parents=True)
                relocated.parent.mkdir(parents=True)
                observed.write_bytes(b"observed")
                if case == "different":
                    relocated.write_bytes(b"different")
                    relocated.chmod(0o555)
                elif case == "symlink":
                    target = root / "relocation-target"
                    target.write_bytes(b"observed")
                    target.chmod(0o555)
                    relocated.symlink_to(target)
                elif case == "writable":
                    relocated.write_bytes(b"observed")
                    relocated.chmod(0o775)

                with self.assertRaises(self.helper.PreparationError):
                    self.helper.qualify_dependency_source(
                        observed,
                        source_root,
                        runtime_root,
                        "libpython3.13.so.1.0",
                    )

    def test_source_root_relocation_rejects_escape_and_source_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            source_root = root / "source"
            runtime_root = root / "runtime"
            observed = source_root / "lib" / "libpython3.13.so.1.0"
            relocated = runtime_root / "lib" / "libpython3.13.so.1.0"
            observed.parent.mkdir(parents=True)
            relocated.parent.mkdir(parents=True)
            observed.write_bytes(b"libpython")
            relocated.write_bytes(b"libpython")
            relocated.chmod(0o555)

            with self.assertRaises(self.helper.PreparationError):
                self.helper.qualify_dependency_source(
                    observed,
                    source_root / "child" / "..",
                    runtime_root,
                    "libpython3.13.so.1.0",
                )

            sibling_escape = root / "source-other" / "libfixture.so.1.0"
            sibling_escape.parent.mkdir()
            sibling_escape.write_bytes(b"escape")
            sibling_escape.chmod(0o777)
            with self.assertRaises(self.helper.PreparationError):
                self.helper.qualify_dependency_source(
                    sibling_escape,
                    source_root,
                    runtime_root,
                    "libfixture.so.1",
                )

            stable_binding = self.helper.stable_file_binding
            calls = 0

            def drift(metadata):
                nonlocal calls
                calls += 1
                binding = stable_binding(metadata)
                if calls == 2:
                    return (*binding[:-1], binding[-1] + 1)
                return binding

            with (
                mock.patch.object(self.helper, "stable_file_binding", drift),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.qualify_dependency_source(
                    observed,
                    source_root,
                    runtime_root,
                    "libpython3.13.so.1.0",
                )

    def test_non_source_root_dependency_keeps_owner_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root).resolve()
            source_root = root / "source"
            runtime_root = root / "runtime"
            external = root / "external" / "libfixture.so.1.0"
            source_root.mkdir()
            runtime_root.mkdir()
            external.parent.mkdir()
            external.write_bytes(b"external")
            external.chmod(0o555)

            with (
                mock.patch.object(
                    self.helper.os,
                    "geteuid",
                    return_value=os.geteuid() + 1,
                ),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.qualify_dependency_source(
                    external,
                    source_root,
                    runtime_root,
                    "libfixture.so.1",
                )

    def test_retained_binding_copies_internal_sources_and_rejects_origin_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            internal = root / "runtime" / "lib" / "libpython3.13.so.1.0"
            loader_root = root / "runtime" / "lib" / "task-witness-loader"
            same_bytes_elsewhere = root / "host" / "libpython3.13.so.1.0"
            internal.parent.mkdir(parents=True)
            loader_root.mkdir()
            same_bytes_elsewhere.parent.mkdir()
            internal.write_bytes(b"libpython")
            same_bytes_elsewhere.write_bytes(b"libpython")
            bindings = {}

            destination, created = self.helper.retain_dependency_binding(
                bindings,
                loader_root,
                "libpython3.13.so.1.0",
                internal,
            )
            self.assertTrue(created)
            self.assertEqual(destination.read_bytes(), internal.read_bytes())
            retained, created_again = self.helper.retain_dependency_binding(
                bindings,
                loader_root,
                "libpython3.13.so.1.0",
                destination,
            )
            self.assertEqual(retained, destination)
            self.assertFalse(created_again)
            with self.assertRaises(self.helper.PreparationError):
                self.helper.retain_dependency_binding(
                    bindings,
                    loader_root,
                    "libpython3.13.so.1.0",
                    same_bytes_elsewhere,
                )

    def test_finalized_binding_separates_copy_and_sealed_digests_and_rejects_tamper(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            loader_root = root / "loader"
            loader_root.mkdir()
            source = root / "libfixture.so.1.2"
            source.write_bytes(b"source-bytes")
            bindings = {}
            retained, _created = self.helper.retain_dependency_binding(
                bindings,
                loader_root,
                "libfixture.so.1",
                source,
            )
            copy_digest = hashlib.sha256(b"source-bytes").hexdigest()
            self.assertEqual(
                bindings["libfixture.so.1"]["retained_copy_sha256"],
                copy_digest,
            )

            retained.chmod(0o755)
            retained.write_bytes(b"sealed-bytes")
            retained.chmod(0o555)
            finalized = self.helper.finalize_dependency_bindings(
                bindings,
                loader_root,
            )
            self.assertEqual(
                finalized["libfixture.so.1"]["retained_copy_sha256"],
                copy_digest,
            )
            self.assertEqual(
                finalized["libfixture.so.1"]["sealed_sha256"],
                hashlib.sha256(b"sealed-bytes").hexdigest(),
            )

            retained.chmod(0o755)
            with self.assertRaises(self.helper.PreparationError):
                self.helper.finalize_dependency_bindings(bindings, loader_root)
            retained.chmod(0o555)
            unexpected = loader_root / "libunexpected.so"
            unexpected.write_bytes(b"unexpected")
            unexpected.chmod(0o555)
            with self.assertRaises(self.helper.PreparationError):
                self.helper.finalize_dependency_bindings(bindings, loader_root)

    def test_dependency_audit_accepts_only_the_exact_host_loader_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            runtime_root = root / "runtime"
            executable = runtime_root / "bin" / "python"
            retained_loader = (
                runtime_root / "lib" / "task-witness-loader" / "ld-linux-x86-64.so.2"
            )
            internal_library = retained_loader.with_name("libc.so.6")
            host_loader = root / "host" / "ld-linux-x86-64.so.2"
            for path in (
                executable,
                retained_loader,
                internal_library,
                host_loader,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            retained_loader.chmod(0o555)

            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=str(retained_loader),
                ),
                mock.patch.object(
                    self.helper,
                    "patchelf_needed",
                    return_value=["libc.so.6"],
                ),
                mock.patch.object(
                    self.helper,
                    "ldd_dependency_bindings",
                    return_value=(
                        {"libc.so.6": internal_library},
                        {},
                        {},
                        {"/lib64/ld-linux-x86-64.so.2": retained_loader},
                        "loader trace\n",
                    ),
                ),
            ):
                audit = self.helper.audit_runtime_elf_dependencies(
                    executable,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )

            self.assertEqual(audit["interpreter"], str(retained_loader))
            self.assertEqual(
                audit["trace_loader"],
                {
                    "path": str(retained_loader),
                    "sha256": hashlib.sha256(b"fixture").hexdigest(),
                    "library_path": str(retained_loader.parent),
                    "inhibit_cache": True,
                },
            )
            self.assertEqual(
                audit["resolved_paths"],
                sorted([str(retained_loader), str(internal_library)]),
            )
            self.assertEqual(
                audit["needed_bindings"],
                [
                    {
                        "requested_name": "libc.so.6",
                        "resolved_path": str(internal_library),
                    }
                ],
            )
            self.assertEqual(audit["transitive_bindings"], [])
            self.assertEqual(audit["loader_needed_bindings"], [])
            self.assertEqual(
                audit["auxiliary_loader_bindings"],
                [
                    {
                        "requested_path": "/lib64/ld-linux-x86-64.so.2",
                        "resolved_path": str(retained_loader),
                    }
                ],
            )

    def test_dependency_audit_binds_loader_needed_to_the_retained_tracer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            runtime_root = root / "runtime"
            libc = runtime_root / "lib" / "libc.so.6"
            retained_loader = (
                runtime_root / "lib" / "task-witness-loader" / "ld-linux-x86-64.so.2"
            )
            host_loader = root / "host" / "ld-linux-x86-64.so.2"
            for path in (libc, retained_loader, host_loader):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"loader-fixture")
            retained_loader.chmod(0o555)
            trace = mock.Mock(
                return_value=(
                    {},
                    {"ld-linux-x86-64.so.2": retained_loader},
                    {},
                    {"/lib64/ld-linux-x86-64.so.2": retained_loader},
                    "retained loader trace\n",
                )
            )

            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=None,
                ),
                mock.patch.object(
                    self.helper,
                    "patchelf_needed",
                    return_value=["ld-linux-x86-64.so.2"],
                ),
                mock.patch.object(self.helper, "ldd_dependency_bindings", trace),
            ):
                audit = self.helper.audit_runtime_elf_dependencies(
                    libc,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )

            digest = hashlib.sha256(b"loader-fixture").hexdigest()
            self.assertEqual(
                audit["loader_needed_bindings"],
                [
                    {
                        "requested_name": "ld-linux-x86-64.so.2",
                        "requested_path": "/lib64/ld-linux-x86-64.so.2",
                        "resolved_path": str(retained_loader),
                        "retained_sha256": digest,
                        "source_path": str(host_loader),
                        "source_sha256": digest,
                    }
                ],
            )
            self.assertEqual(audit["needed_bindings"], [])
            self.assertEqual(audit["resolved_paths"], [str(retained_loader)])
            trace.assert_called_once_with(
                libc,
                1001,
                1001,
                ["ld-linux-x86-64.so.2"],
                tracer=retained_loader,
                library_path=retained_loader.parent,
            )

    def test_dependency_audit_rejects_bad_interpreters_and_other_externals(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            runtime_root = root / "runtime"
            executable = runtime_root / "bin" / "python"
            retained_loader = (
                runtime_root / "lib" / "task-witness-loader" / "ld-linux-x86-64.so.2"
            )
            other_internal_loader = retained_loader.with_name("other-loader.so")
            host_loader = root / "host" / "ld-linux-x86-64.so.2"
            external_library = root / "host" / "libc.so.6"
            for path in (
                executable,
                retained_loader,
                other_internal_loader,
                host_loader,
                external_library,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            retained_loader.chmod(0o555)

            cases = {
                "relative-interpreter": (
                    "lib/task-witness-loader/ld-linux-x86-64.so.2",
                    {},
                ),
                "external-interpreter": (str(host_loader), {}),
                "unapproved-internal-interpreter": (
                    str(other_internal_loader),
                    {},
                ),
                "external-library": (
                    str(retained_loader),
                    {"libc.so.6": external_library},
                ),
                "labeled-exact-loader-artifact": (
                    str(retained_loader),
                    {"ld-linux-x86-64.so.2": host_loader},
                ),
            }
            for name, (interpreter, needed) in cases.items():
                with (
                    self.subTest(name=name),
                    mock.patch.object(
                        self.helper,
                        "patchelf_interpreter",
                        return_value=interpreter,
                    ),
                    mock.patch.object(
                        self.helper,
                        "patchelf_needed",
                        return_value=list(needed),
                    ),
                    mock.patch.object(
                        self.helper,
                        "ldd_dependency_bindings",
                        return_value=(
                            needed,
                            {},
                            {},
                            {"/lib64/ld-linux-x86-64.so.2": retained_loader},
                            "loader trace\n",
                        ),
                    ),
                    self.assertRaises(self.helper.PreparationError),
                ):
                    self.helper.audit_runtime_elf_dependencies(
                        executable,
                        runtime_root,
                        retained_loader,
                        host_loader,
                        1001,
                        1001,
                    )

            retained_loader.unlink()
            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=str(retained_loader),
                ),
                mock.patch.object(
                    self.helper,
                    "ldd_dependency_bindings",
                    return_value=(
                        {},
                        {},
                        {},
                        {"/lib64/ld-linux-x86-64.so.2": retained_loader},
                        "loader trace\n",
                    ),
                ),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.audit_runtime_elf_dependencies(
                    executable,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )

    def test_dependency_audit_rejects_interpreter_inspection_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            runtime_root = root / "runtime"
            executable = runtime_root / "bin" / "python"
            retained_loader = (
                runtime_root / "lib" / "task-witness-loader" / "ld-linux-x86-64.so.2"
            )
            host_loader = root / "host" / "ld-linux-x86-64.so.2"
            for path in (executable, retained_loader, host_loader):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            retained_loader.chmod(0o555)

            ldd = mock.Mock(
                return_value=(
                    {},
                    {},
                    {},
                    {"/lib64/ld-linux-x86-64.so.2": retained_loader},
                    "loader trace\n",
                )
            )
            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    side_effect=self.helper.PreparationError(
                        "interpreter inspection failed"
                    ),
                ),
                mock.patch.object(self.helper, "ldd_dependency_bindings", ldd),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.audit_runtime_elf_dependencies(
                    executable,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )
            ldd.assert_not_called()

    def test_shared_object_dependency_audit_keeps_loader_handling_exact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            runtime_root = root / "runtime"
            shared_object = runtime_root / "lib" / "libpython.so"
            retained_loader = (
                runtime_root / "lib" / "task-witness-loader" / "ld-linux-x86-64.so.2"
            )
            host_loader = root / "host" / "ld-linux-x86-64.so.2"
            lookalike_loader = root / "lookalike" / "ld-linux-x86-64.so.2"
            for path in (
                shared_object,
                retained_loader,
                host_loader,
                lookalike_loader,
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            retained_loader.chmod(0o555)

            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=None,
                ),
                mock.patch.object(
                    self.helper,
                    "patchelf_needed",
                    return_value=[],
                ),
                mock.patch.object(
                    self.helper,
                    "ldd_dependency_bindings",
                    return_value=(
                        {},
                        {},
                        {},
                        {},
                        "\tstatically linked\n",
                    ),
                ),
            ):
                dependency_free_audit = self.helper.audit_runtime_elf_dependencies(
                    shared_object,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )
            self.assertEqual(
                dependency_free_audit["trace_disposition"],
                "dependency-free-dynamic-elf",
            )
            self.assertEqual(dependency_free_audit["resolved_paths"], [])

            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=None,
                ),
                mock.patch.object(
                    self.helper,
                    "patchelf_needed",
                    return_value=[],
                ),
                mock.patch.object(
                    self.helper,
                    "ldd_dependency_bindings",
                    return_value=(
                        {},
                        {},
                        {},
                        {"/lib64/ld-linux-x86-64.so.2": retained_loader},
                        "loader trace\n",
                    ),
                ),
            ):
                audit = self.helper.audit_runtime_elf_dependencies(
                    shared_object,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )
            self.assertIsNone(audit["interpreter"])
            self.assertEqual(
                audit["trace_disposition"],
                "resolved-dynamic-dependency-closure",
            )

            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=None,
                ),
                mock.patch.object(
                    self.helper,
                    "patchelf_needed",
                    return_value=[],
                ),
                mock.patch.object(
                    self.helper,
                    "ldd_dependency_bindings",
                    return_value=(
                        {},
                        {},
                        {},
                        {"/lib64/ld-linux-x86-64.so.2": lookalike_loader},
                        "loader trace\n",
                    ),
                ),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.audit_runtime_elf_dependencies(
                    shared_object,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )

            with (
                mock.patch.object(
                    self.helper,
                    "patchelf_interpreter",
                    return_value=None,
                ),
                mock.patch.object(
                    self.helper,
                    "patchelf_needed",
                    return_value=[],
                ),
                mock.patch.object(
                    self.helper,
                    "ldd_dependency_bindings",
                    return_value=(
                        {},
                        {},
                        {},
                        {"/lib64/not-the-approved-loader.so": retained_loader},
                        "loader trace\n",
                    ),
                ),
                self.assertRaises(self.helper.PreparationError),
            ):
                self.helper.audit_runtime_elf_dependencies(
                    shared_object,
                    runtime_root,
                    retained_loader,
                    host_loader,
                    1001,
                    1001,
                )

    def build_installed_pyyaml(self, root: Path) -> Path:
        site_packages = root / "lib" / "python3.13" / "site-packages"
        files = {
            "_yaml/__init__.py": b"from yaml._yaml import *\n",
            "yaml/__init__.py": b"__version__ = '6.0.3'\n",
            "yaml/_yaml.cpython-313-x86_64-linux-gnu.so": b"\x7fELFupstream",
            "pyyaml-6.0.3.dist-info/METADATA": b"Name: PyYAML\nVersion: 6.0.3\n",
            "pyyaml-6.0.3.dist-info/WHEEL": (
                b"Wheel-Version: 1.0\n"
                b"Root-Is-Purelib: false\n"
                b"Tag: cp313-cp313-manylinux_2_17_x86_64\n"
                b"Tag: cp313-cp313-manylinux2014_x86_64\n"
                b"Tag: cp313-cp313-manylinux_2_28_x86_64\n"
            ),
        }
        for relative, raw in files.items():
            path = site_packages / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        record_relative = "pyyaml-6.0.3.dist-info/RECORD"
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        for relative, raw in sorted(files.items()):
            digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=")
            writer.writerow([relative, f"sha256={digest.decode()}", len(raw)])
        writer.writerow([record_relative, "", ""])
        (site_packages / record_relative).write_text(output.getvalue())
        return site_packages

    def test_pyyaml_record_validation_binds_every_installed_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            site_packages = self.build_installed_pyyaml(Path(raw_root))
            audit = self.helper.validate_installed_wheel(site_packages)
            self.assertEqual(audit["distribution"], "PyYAML")
            self.assertEqual(audit["version"], "6.0.3")
            self.assertEqual(
                audit["native_extensions"],
                ["yaml/_yaml.cpython-313-x86_64-linux-gnu.so"],
            )

    def test_pyyaml_record_validation_rejects_an_unhashed_member(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            site_packages = self.build_installed_pyyaml(Path(raw_root))
            record = site_packages / "pyyaml-6.0.3.dist-info" / "RECORD"
            rows = list(csv.reader(io.StringIO(record.read_text())))
            rows[0][1] = ""
            output = io.StringIO(newline="")
            csv.writer(output, lineterminator="\n").writerows(rows)
            record.write_text(output.getvalue())
            with self.assertRaises(self.helper.PreparationError):
                self.helper.validate_installed_wheel(site_packages)

    def test_pyyaml_record_validation_rejects_unsafe_duplicate_and_changed_rows(
        self,
    ) -> None:
        cases = ("unsafe", "duplicate", "hash-mismatch", "wrong-native-tag")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_root:
                site_packages = self.build_installed_pyyaml(Path(raw_root))
                record = site_packages / "pyyaml-6.0.3.dist-info" / "RECORD"
                rows = list(csv.reader(io.StringIO(record.read_text())))
                if case == "unsafe":
                    rows[0][0] = "../escape"
                elif case == "duplicate":
                    rows.insert(1, list(rows[0]))
                elif case == "hash-mismatch":
                    (site_packages / "yaml" / "__init__.py").write_bytes(b"changed")
                else:
                    original = "yaml/_yaml.cpython-313-x86_64-linux-gnu.so"
                    replacement = "yaml/_yaml.cpython-313-x86_64-linux-musl.so"
                    (site_packages / original).rename(site_packages / replacement)
                    for row in rows:
                        if row[0] == original:
                            row[0] = replacement
                            break
                output = io.StringIO(newline="")
                csv.writer(output, lineterminator="\n").writerows(rows)
                record.write_text(output.getvalue())
                with self.assertRaises(self.helper.PreparationError):
                    self.helper.validate_installed_wheel(site_packages)

    def test_pyyaml_record_rebind_changes_only_the_native_extension_row(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            site_packages = self.build_installed_pyyaml(Path(raw_root))
            before = self.helper.validate_installed_wheel(site_packages)
            native = site_packages / "yaml" / "_yaml.cpython-313-x86_64-linux-gnu.so"
            native.write_bytes(b"\x7fELFsealed")
            after = self.helper.finalize_installed_wheel(site_packages, before)
            self.assertEqual(
                after["transformed_paths"],
                [native.relative_to(site_packages).as_posix()],
            )
            self.assertNotEqual(
                after["upstream_record_sha256"],
                after["final_record_sha256"],
            )
            dispositions = {
                entry["path"]: entry["disposition"] for entry in after["entries"]
            }
            self.assertEqual(
                dispositions[native.relative_to(site_packages).as_posix()],
                "operator-rewritten-elf-and-record-rebound",
            )

    def test_parser_requires_an_explicit_subcommand(self) -> None:
        with self.assertRaises(SystemExit):
            self.helper.parser().parse_args([])

    def test_workflow_executes_verified_host_hardening_seam(self) -> None:
        workflow = LINUX_QUALIFICATION_WORKFLOW.read_text()
        self.assertIn(
            "      - scripts/harden_task_witness_linux_host.bash\n",
            workflow,
        )
        self.assertIn(
            "      - tests/test_harden_task_witness_linux_host.bash\n",
            workflow,
        )
        self.assertIn(
            """\
              harness/scripts/harden_task_witness_linux_host.bash \\
              qualify-host \\
              / \\
              "$GITHUB_WORKSPACE/harness/tests/test_harden_task_witness_linux_host.bash" \\
              "$GITHUB_RUN_ID" \\
              "$GITHUB_RUN_ATTEMPT" \\
              "$qual_uid" \\
              "$qual_gid"
""",
            workflow,
        )
        self.assertEqual(workflow.count("              qualify-host \\\n"), 1)
        self.assertNotIn("host_hardening_test_root=", workflow)
        self.assertNotIn(
            """\
              harness/scripts/harden_task_witness_linux_host.bash \\
              harden \\
              / \\
""",
            workflow,
        )

    def test_context_capture_preserves_the_explicit_clean_boundary(self) -> None:
        forwarded = {
            key: f"observed-{key.lower()}" for key in self.helper.GITHUB_CONTEXT_FIELDS
        }
        forwarded.update(
            {
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF": self.helper.EXPECTED_HARNESS_REF,
                "GITHUB_REPOSITORY": self.helper.EXPECTED_REPOSITORY,
                "GITHUB_SHA": "1" * 40,
                "ImageVersion": "",
                "RUNNER_ARCH": "X64",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
                "UNRELATED_AMBIENT_VALUE": "must-not-cross",
            }
        )
        expected = {
            key: forwarded[key]
            for key in self.helper.GITHUB_CONTEXT_FIELDS
            if forwarded[key]
        }

        with tempfile.TemporaryDirectory() as raw_root:
            output = Path(raw_root) / "github-actions-context.json"
            with mock.patch.object(self.helper, "require_root"):
                self.helper.capture_context(output, environment=forwarded)

            document = json.loads(output.read_bytes())
            self.assertEqual(document["context"], expected)
            self.assertEqual(
                document["content_sha256"],
                hashlib.sha256(
                    self.helper.canonical_bytes(
                        {
                            "schema_version": 1,
                            "contract": "task-witness-github-actions-context-v1",
                            "context": expected,
                        }
                    )
                ).hexdigest(),
            )

    def test_bootstrap_helper_survives_a_nontraversable_checkout_ancestor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            checkout = root / "private-checkout"
            source = checkout / "scripts" / HELPER.name
            source.parent.mkdir(parents=True)
            source.write_bytes(HELPER.read_bytes())
            source.chmod(0o555)
            bootstrap = root / "bootstrap"
            bootstrap.mkdir(mode=0o755)
            retained = bootstrap / HELPER.name

            stage = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(HELPER),
                    "stage-bootstrap-helper",
                    "--source",
                    str(source),
                    "--output",
                    str(retained),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(stage.returncode, 0, stage.stderr)
            self.assertEqual(retained.read_bytes(), source.read_bytes())
            self.assertEqual(retained.stat().st_uid, os.geteuid())
            self.assertEqual(retained.stat().st_gid, os.getegid())
            self.assertEqual(retained.stat().st_mode & 0o777, 0o555)

            retained_raw = retained.read_bytes()
            replacement = source.with_name("replacement.py")
            replacement.write_bytes(b"replacement")
            replacement.chmod(0o555)
            restage = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(HELPER),
                    "stage-bootstrap-helper",
                    "--source",
                    str(replacement),
                    "--output",
                    str(retained),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(restage.returncode, 0)
            self.assertEqual(retained.read_bytes(), retained_raw)

            checkout.chmod(0)
            try:
                with self.assertRaises(PermissionError):
                    subprocess.run(
                        [str(source), "--help"],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                runnable = subprocess.run(
                    [str(retained), "--help"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            finally:
                checkout.chmod(0o755)
            self.assertEqual(runnable.returncode, 0, runnable.stderr)

    def test_artifact_manifest_replays_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            workspace = Path(raw_root)
            artifact_root = workspace / "artifact"
            artifact_root.mkdir()
            (artifact_root / "nested").mkdir()
            (artifact_root / "a.txt").write_bytes(b"alpha")
            (artifact_root / "nested" / "b.txt").write_bytes(b"beta")
            manifest = workspace / "SHA256SUMS.external"

            self.helper.write_artifact_manifest(artifact_root, manifest)
            self.helper.verify_artifact_manifest(artifact_root, manifest)
            expected = (
                f"{hashlib.sha256(b'alpha').hexdigest()}  ./a.txt\n"
                f"{hashlib.sha256(b'beta').hexdigest()}  ./nested/b.txt\n"
            ).encode()
            self.assertEqual(manifest.read_bytes(), expected)

            retained_manifest = artifact_root / "SHA256SUMS"
            manifest.replace(retained_manifest)
            self.helper.verify_artifact_manifest(artifact_root, retained_manifest)
            (artifact_root / "a.txt").write_bytes(b"changed")
            with self.assertRaises(self.helper.PreparationError):
                self.helper.verify_artifact_manifest(
                    artifact_root,
                    retained_manifest,
                )

    def test_artifact_manifest_rejects_symlinks_and_self_inclusion(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            workspace = Path(raw_root)
            artifact_root = workspace / "artifact"
            artifact_root.mkdir()
            (artifact_root / "value").write_bytes(b"value")
            (artifact_root / "link").symlink_to("value")
            with self.assertRaises(self.helper.PreparationError):
                self.helper.write_artifact_manifest(
                    artifact_root,
                    workspace / "SHA256SUMS.external",
                )
            (artifact_root / "link").unlink()
            with self.assertRaises(self.helper.PreparationError):
                self.helper.write_artifact_manifest(
                    artifact_root,
                    artifact_root / "SHA256SUMS",
                )


if __name__ == "__main__":
    unittest.main()
