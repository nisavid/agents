from __future__ import annotations

import ctypes
import errno
import hashlib
import importlib.util
import io
import json
import os
import plistlib
import re
import stat
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[1]
HELPER = REPOSITORY / "scripts" / "probe_task_witness_macos_host.py"
WORKFLOW = REPOSITORY / ".github" / "workflows" / "task-witness-macos-host-probe.yml"
LINUX_WORKFLOW = (
    REPOSITORY / ".github" / "workflows" / "task-witness-linux-qualification.yml"
)
FROZEN_CANDIDATE_SHA = "b47f03519068b858cf0c070b5d331ee053ef6b7b"
MACOS_HARNESS_BRANCH = "ivan/task-witness-macos-qualification-harness"
LINUX_HARNESS_BRANCH = "ivan/task-witness-linux-qualification-harness"


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "task_witness_macos_host_probe",
        HELPER,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("macOS host probe helper cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def workflow_step_script(name: str) -> str:
    lines = WORKFLOW.read_text().splitlines()
    marker = f"      - name: {name}"
    start = lines.index(marker)
    run = lines.index("        run: |", start)
    body = []
    for line in lines[run + 1 :]:
        if line.startswith("      - name: "):
            break
        if line and not line.startswith("          "):
            raise AssertionError(f"unexpected workflow script indentation: {line}")
        body.append(line[10:] if line else "")
    return "\n".join(body) + "\n"


def eligible_context() -> dict[str, str]:
    return {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": f"refs/heads/{MACOS_HARNESS_BRANCH}",
        "GITHUB_REPOSITORY": "nisavid/agents",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_RUN_ID": "123456789",
        "GITHUB_SHA": "1" * 40,
        "GITHUB_WORKFLOW_REF": (
            "nisavid/agents/.github/workflows/"
            "task-witness-macos-host-probe.yml@refs/heads/"
            f"{MACOS_HARNESS_BRANCH}"
        ),
        "GITHUB_WORKFLOW_SHA": "1" * 40,
        "ImageOS": "macos15",
        "ImageVersion": "20260801.1",
        "RUNNER_ARCH": "ARM64",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "macOS",
    }


def eligible_observations() -> dict[str, object]:
    return {
        "platform": {
            "system": "darwin",
            "machine": "arm64",
            "kernel_release": "24.6.0",
            "macos_product_version": "15.6",
            "macos_build_version": "24G84",
            "translated": False,
            "container_indicators": {
                "dockerenv": False,
                "run_containerenv": False,
                "container_environment": False,
            },
        },
        "credentials": {
            "real_uid": 501,
            "effective_uid": 501,
            "real_gid": 20,
            "effective_gid": 20,
            "supplementary_gids": [12, 20, 61],
            "passwd_group_gids": [12, 20, 61],
            "passwd": {
                "name": "runner",
                "uid": 501,
                "primary_gid": 20,
                "home": "/Users/runner",
                "shell": "/bin/zsh",
            },
            "issetugid": False,
            "admin_gid": 80,
            "admin_member": False,
        },
        "home": {
            "path": "/Users/runner",
            "kind": "directory",
            "uid": 501,
            "gid": 20,
            "mode": 0o700,
            "filesystem_type": "apfs",
            "symlink_components": [],
        },
        "provisioning_capability": {
            "passwordless_sudo": True,
            "tools": [
                {"id": "dscl", "path": "/usr/bin/dscl", "available": True},
                {
                    "id": "launchctl",
                    "path": "/bin/launchctl",
                    "available": True,
                },
                {
                    "id": "sysadminctl",
                    "path": "/usr/sbin/sysadminctl",
                    "available": True,
                },
            ],
        },
    }


class TaskWitnessMacOSHostProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = load_helper()

    def test_sha256_predicate_is_exact_and_value_free(self) -> None:
        class Sha256Subclass(str):
            pass

        exact = "0123456789abcdef" * 4
        cases = (
            ("exact", exact, True),
            ("str-subclass", Sha256Subclass(exact), True),
            ("short", exact[:-1], False),
            ("long", exact + "0", False),
            ("newline-suffix", exact + "\n", False),
            ("private-suffix", exact + "private-canary", False),
            ("uppercase", exact.upper(), False),
            ("nonhex", "g" * 64, False),
            ("bytes", exact.encode(), False),
            ("integer", 1, False),
            ("boolean", True, False),
            ("list", [exact], False),
            ("mapping", {"digest": exact}, False),
        )
        for label, value, expected in cases:
            with self.subTest(label=label):
                self.assertIs(self.helper._is_sha256(value), expected)

        with self.assertRaises(self.helper.ProbeError) as raised:
            self.helper._require_content_digest(
                {"content_sha256": exact + "private-canary"},
                "account-binding",
            )
        self.assertEqual(
            raised.exception.code,
            "invalid-account-binding-digest",
        )
        self.assertIsNone(raised.exception.secondary_code)
        self.assertNotIn("private-canary", str(raised.exception))

    def test_directory_match_forwards_non_root_ownership(self) -> None:
        path = Path("/Users/twq-0123456789ab/launchd-probe")
        with mock.patch.object(
            self.helper,
            "_metadata_matches",
            return_value=True,
        ) as metadata:
            self.assertTrue(
                self.helper._directory_matches(
                    path,
                    0o700,
                    502,
                    20,
                )
            )
        metadata.assert_called_once_with(
            path,
            kind="directory",
            mode=0o700,
            uid=502,
            gid=20,
        )

    def test_write_root_file_forwards_requested_mode(self) -> None:
        path = Path("/private/var/tmp/stage/job.plist")
        raw = b"exact plist bytes"
        with (
            mock.patch.object(self.helper, "write_create_new") as write,
            mock.patch.object(
                self.helper,
                "_root_file_matches",
                return_value=True,
            ) as matches,
        ):
            self.helper._write_root_file(path, raw, 0o644)
        write.assert_called_once_with(path, raw, 0o644)
        matches.assert_called_once_with(path, 0o644)

        with tempfile.TemporaryDirectory() as directory:
            rejected_path = Path(directory) / "job.plist"

            def create(path: Path, payload: bytes, mode: int) -> None:
                path.write_bytes(payload)
                path.chmod(mode)

            with (
                mock.patch.object(
                    self.helper,
                    "write_create_new",
                    side_effect=create,
                ) as rejected_write,
                mock.patch.object(
                    self.helper,
                    "_root_file_matches",
                    return_value=False,
                ) as rejected_match,
                mock.patch.object(
                    self.helper,
                    "_load_canonical_document",
                ) as downstream_load,
                mock.patch.object(Path, "unlink") as unlink,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._write_root_file(rejected_path, raw, 0o644)

            self.assertEqual(raised.exception.code, "root-file-disagrees")
            rejected_write.assert_called_once_with(rejected_path, raw, 0o644)
            rejected_match.assert_called_once_with(rejected_path, 0o644)
            downstream_load.assert_not_called()
            unlink.assert_not_called()
            self.assertEqual(rejected_path.read_bytes(), raw)

    def test_root_directory_normalizes_macos_parent_group_inheritance(self) -> None:
        path = Path("/private/tmp/task-witness-macos-launchd-user-probe")
        state = {"gid": 20, "mode": 0o600}

        def lstat() -> SimpleNamespace:
            return SimpleNamespace(
                st_dev=7,
                st_gid=state["gid"],
                st_ino=11,
                st_mode=stat.S_IFDIR | state["mode"],
                st_nlink=2,
                st_uid=0,
            )

        def fchown(descriptor: int, uid: int, gid: int) -> None:
            self.assertEqual((descriptor, uid, gid), (42, 0, 0))
            state["gid"] = 0

        def fchmod(descriptor: int, mode: int) -> None:
            self.assertEqual((descriptor, mode), (42, 0o700))
            state["mode"] = mode

        with (
            mock.patch.object(
                Path,
                "mkdir",
                side_effect=AssertionError("path-based mkdir is not identity-bound"),
            ),
            mock.patch.object(
                self.helper.os,
                "chown",
                side_effect=AssertionError("path-based chown is not identity-bound"),
            ),
            mock.patch.object(self.helper.os, "open", side_effect=[41, 42]) as opened,
            mock.patch.object(self.helper.os, "mkdir") as mkdir,
            mock.patch.object(self.helper.os, "fstat", side_effect=lambda _fd: lstat()),
            mock.patch.object(self.helper.os, "fchown", side_effect=fchown),
            mock.patch.object(self.helper.os, "fchmod", side_effect=fchmod),
            mock.patch.object(
                self.helper.os, "stat", side_effect=lambda *_a, **_k: lstat()
            ),
            mock.patch.object(self.helper.os, "close") as close,
        ):
            self.helper._create_root_directory(path, 0o700)

        self.assertEqual(opened.call_args_list[0].args[0], path.parent)
        self.assertEqual(opened.call_args_list[1].args[0], path.name)
        mkdir.assert_called_once_with(path.name, 0o700, dir_fd=41)
        self.assertEqual(state, {"gid": 0, "mode": 0o700})
        self.assertCountEqual(close.call_args_list, [mock.call(42), mock.call(41)])

    def test_root_directory_failure_rolls_back_only_the_created_identity(self) -> None:
        path = Path("/private/tmp/task-witness-macos-launchd-user-probe")

        def metadata(inode: int) -> SimpleNamespace:
            return SimpleNamespace(
                st_dev=7,
                st_gid=20,
                st_ino=inode,
                st_mode=stat.S_IFDIR | 0o700,
                st_nlink=2,
                st_uid=0,
            )

        for label, visible_inode, expected_error, removes in (
            ("same-created-directory", 11, "directory-create-new-failed", True),
            ("substituted-directory", 12, "directory-create-new-preserved", False),
        ):
            with (
                self.subTest(label=label),
                mock.patch.object(self.helper.os, "open", side_effect=[41, 42]),
                mock.patch.object(self.helper.os, "mkdir"),
                mock.patch.object(
                    self.helper.os,
                    "fstat",
                    side_effect=lambda _fd: metadata(11),
                ),
                mock.patch.object(
                    self.helper.os,
                    "fchown",
                    side_effect=OSError("synthetic normalization failure"),
                ),
                mock.patch.object(
                    self.helper.os,
                    "stat",
                    side_effect=lambda *_a, _inode=visible_inode, **_k: metadata(
                        _inode
                    ),
                ),
                mock.patch.object(self.helper.os, "listdir", return_value=[]),
                mock.patch.object(self.helper.os, "rmdir") as rmdir,
                mock.patch.object(self.helper.os, "close"),
                self.assertRaisesRegex(self.helper.ProbeError, expected_error),
            ):
                self.helper._create_root_directory(path, 0o700)

            if removes:
                rmdir.assert_called_once_with(path.name, dir_fd=41)
            else:
                rmdir.assert_not_called()

    def test_eligible_direct_session_emits_only_a_probe_claim(self) -> None:
        document = self.helper.build_probe_document(
            FROZEN_CANDIDATE_SHA,
            eligible_context(),
            eligible_observations(),
        )

        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(
            document["contract"],
            "task-witness-macos-github-host-probe-v1",
        )
        self.assertEqual(document["claim"], "host-prerequisite-probe-only")
        self.assertEqual(document["candidate_sha1"], FROZEN_CANDIDATE_SHA)
        self.assertEqual(document["disposition"], "direct-session-eligible")
        self.assertTrue(all(document["requirements"].values()))
        self.assertNotIn("qualified", self.helper.canonical_bytes(document).decode())
        self.assertNotIn("receipt", self.helper.canonical_bytes(document).decode())
        unsigned = {
            key: value for key, value in document.items() if key != "content_sha256"
        }
        self.assertEqual(
            document["content_sha256"],
            hashlib.sha256(self.helper.canonical_bytes(unsigned)).hexdigest(),
        )

    def test_each_frozen_direct_session_boundary_fails_closed(self) -> None:
        cases: dict[str, tuple[str, object]] = {
            "admin-member": ("credentials.admin_member", True),
            "root": ("credentials.effective_uid", 0),
            "home-mode": ("home.mode", 0o755),
            "home-owner": ("home.uid", 502),
            "home-filesystem": ("home.filesystem_type", "hfs"),
            "setugid": ("credentials.issetugid", True),
            "translated": ("platform.translated", True),
            "wrong-machine": ("platform.machine", "x86_64"),
            "group-view-drift": ("credentials.passwd_group_gids", [20, 61]),
            "home-symlink-ancestor": (
                "home.symlink_components",
                ["/Users/runner"],
            ),
        }
        for label, (path, replacement) in cases.items():
            observations = json.loads(json.dumps(eligible_observations()))
            parent_name, leaf_name = path.split(".")
            observations[parent_name][leaf_name] = replacement
            document = self.helper.build_probe_document(
                FROZEN_CANDIDATE_SHA,
                eligible_context(),
                observations,
            )
            with self.subTest(label=label):
                self.assertEqual(
                    document["disposition"],
                    "direct-session-ineligible",
                )
                self.assertFalse(all(document["requirements"].values()))

        for marker in (
            "container_environment",
            "dockerenv",
            "run_containerenv",
        ):
            observations = json.loads(json.dumps(eligible_observations()))
            observations["platform"]["container_indicators"][marker] = True
            document = self.helper.build_probe_document(
                FROZEN_CANDIDATE_SHA,
                eligible_context(),
                observations,
            )
            with self.subTest(container_marker=marker):
                self.assertEqual(
                    document["disposition"],
                    "direct-session-ineligible",
                )
                self.assertFalse(document["requirements"]["no_container_indicators"])

    def test_home_component_observation_detects_a_symlinked_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            actual = root / "actual"
            actual.mkdir()
            link = root / "link"
            link.symlink_to(actual.name, target_is_directory=True)
            nested = link / "nested"
            nested.mkdir()

            observed = self.helper._path_symlink_components(nested)
            self.assertIn(str(link), observed)
            self.assertEqual(observed[-1], str(link))

    def test_darwin_filesystem_type_decodes_mocked_statfs(self) -> None:
        helper = self.helper
        observed_paths: list[bytes] = []

        class FakeStatFs:
            argtypes = None
            restype = None

            def __call__(self, encoded_path, observed_pointer):
                observed_paths.append(encoded_path)
                observed = helper.ctypes.cast(
                    observed_pointer,
                    helper.ctypes.POINTER(helper._DarwinStatFs),
                ).contents
                observed.f_fstypename = b"MockFS"
                return 0

        fake_statfs = FakeStatFs()
        fake_libc = SimpleNamespace(statfs=fake_statfs)
        with mock.patch.object(
            helper.ctypes,
            "CDLL",
            return_value=fake_libc,
        ) as load_libc:
            self.assertEqual(
                helper._darwin_filesystem_type(Path("/synthetic/home"), "darwin"),
                "mockfs",
            )
            self.assertEqual(
                helper._darwin_filesystem_type(Path("/synthetic/home"), "linux"),
                "",
            )

        load_libc.assert_called_once_with(None, use_errno=True)
        self.assertEqual(observed_paths, [b"/synthetic/home"])
        self.assertEqual(
            fake_statfs.argtypes,
            [
                helper.ctypes.c_char_p,
                helper.ctypes.POINTER(helper._DarwinStatFs),
            ],
        )
        self.assertIs(fake_statfs.restype, helper.ctypes.c_int)

    def test_direct_arm64_process_is_not_rosetta_translated(self) -> None:
        with mock.patch.object(
            self.helper,
            "_run_bounded_command",
            side_effect=AssertionError("native arm64 must not need sysctl"),
        ):
            self.assertIs(
                self.helper._darwin_translation_state("darwin", "arm64"),
                False,
            )

    def test_context_is_exact_and_ambient_values_do_not_cross(self) -> None:
        environment = eligible_context()
        environment.update(
            {
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "must-not-cross",
                "GH_TOKEN": "must-not-cross",
                "GITHUB_TOKEN": "must-not-cross",
                "UNRELATED_AMBIENT_VALUE": "must-not-cross",
            }
        )
        document = self.helper.build_probe_document(
            FROZEN_CANDIDATE_SHA,
            environment,
            eligible_observations(),
        )
        raw = self.helper.canonical_bytes(document)

        self.assertNotIn(b"must-not-cross", raw)
        self.assertEqual(
            set(document["harness"]),
            {
                "commit_sha1",
                "ref",
                "repository",
                "run_attempt",
                "run_id",
                "workflow_ref",
                "workflow_sha1",
            },
        )
        self.assertEqual(
            set(document["runner"]),
            {"arch", "environment", "image_os", "image_version", "os"},
        )

    def test_wrong_repository_event_ref_or_candidate_is_a_probe_error(self) -> None:
        cases = {
            "repository": ("GITHUB_REPOSITORY", "example/other"),
            "event": ("GITHUB_EVENT_NAME", "pull_request"),
            "ref": ("GITHUB_REF", "refs/heads/main"),
            "harness-sha": ("GITHUB_SHA", "not-a-sha"),
        }
        for label, (name, value) in cases.items():
            context = eligible_context()
            context[name] = value
            with (
                self.subTest(label=label),
                self.assertRaises(self.helper.ProbeError),
            ):
                self.helper.build_probe_document(
                    FROZEN_CANDIDATE_SHA,
                    context,
                    eligible_observations(),
                )
        with self.assertRaises(self.helper.ProbeError):
            self.helper.build_probe_document(
                "0" * 40,
                eligible_context(),
                eligible_observations(),
            )

    def test_probe_document_cap_is_enforced_before_publication(self) -> None:
        observations = eligible_observations()
        observations["platform"]["kernel_release"] = "x" * (
            self.helper.MAX_PROBE_JSON_BYTES
        )
        with self.assertRaises(self.helper.ProbeError):
            self.helper.build_probe_document(
                FROZEN_CANDIDATE_SHA,
                eligible_context(),
                observations,
            )

    def test_expected_probe_failure_still_publishes_a_canonical_error_document(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            output = Path(raw_root) / "probe.json"
            with (
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(
                    self.helper,
                    "collect_observations",
                    side_effect=self.helper.ProbeError("host-command-failed"),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                status = self.helper.run_probe(output, FROZEN_CANDIDATE_SHA)

            self.assertEqual(status, 2)
            raw = output.read_bytes()
            document = json.loads(raw)
            self.assertEqual(raw, self.helper.canonical_bytes(document))
            self.assertEqual(document["disposition"], "probe-error")
            self.assertEqual(document["error"], {"code": "host-command-failed"})
            self.assertIsNone(document["observations"])
            self.assertIsNone(document["requirements"])
            self.assertNotIn(b"qualified", raw)
            self.assertNotIn(b"receipt", raw)

    def test_host_commands_are_clean_bounded_and_time_limited(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout=b"value\n", stderr=b"")
        with mock.patch.object(
            self.helper.subprocess,
            "run",
            return_value=completed,
        ) as run:
            self.assertEqual(
                self.helper._run_bounded_command(["/usr/bin/true"]),
                (0, "value", ""),
            )
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["timeout"], self.helper.COMMAND_TIMEOUT_SECONDS)
        self.assertEqual(
            kwargs["env"],
            {
                "HOME": "/var/empty",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            },
        )

        oversized = SimpleNamespace(
            returncode=0,
            stdout=b"x" * (self.helper.MAX_COMMAND_OUTPUT_BYTES + 1),
            stderr=b"",
        )
        with (
            mock.patch.object(
                self.helper.subprocess,
                "run",
                return_value=oversized,
            ),
            self.assertRaises(self.helper.ProbeError),
        ):
            self.helper._run_bounded_command(["/usr/bin/true"])

        with (
            mock.patch.object(
                self.helper.subprocess,
                "run",
                side_effect=self.helper.subprocess.TimeoutExpired(
                    ["/usr/bin/true"],
                    self.helper.COMMAND_TIMEOUT_SECONDS,
                ),
            ),
            self.assertRaises(self.helper.ProbeError),
        ):
            self.helper._run_bounded_command(["/usr/bin/true"])

    def test_create_new_output_preserves_existing_regular_and_symlink_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            output = root / "probe.json"
            self.helper.write_create_new(output, b"first")
            self.assertEqual(output.read_bytes(), b"first")
            self.assertEqual(
                [path.name for path in root.iterdir()],
                ["probe.json"],
            )
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(self.helper.ProbeError):
                self.helper.write_create_new(output, b"replacement")
            self.assertEqual(output.read_bytes(), b"first")

            link = root / "link.json"
            link.symlink_to(output.name)
            with self.assertRaises(self.helper.ProbeError):
                self.helper.write_create_new(link, b"replacement")
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), output.name)
            self.assertEqual(output.read_bytes(), b"first")
            self.assertEqual(
                sorted(path.name for path in root.iterdir()),
                ["link.json", "probe.json"],
            )

    def test_stage_create_new_uses_exclusive_atomic_rename(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            stage = root / "stage"
            stage.mkdir()
            output = stage / "home-cleanup.json"
            self.helper._write_stage_create_new(output, b"first", 0o600)
            self.assertEqual(output.read_bytes(), b"first")
            self.assertEqual(output.lstat().st_nlink, 1)
            self.assertEqual([entry.name for entry in stage.iterdir()], [output.name])
            self.assertEqual([entry.name for entry in root.iterdir()], [stage.name])

            with self.assertRaises(self.helper.ProbeError) as raised:
                self.helper._write_stage_create_new(output, b"replacement", 0o600)
            self.assertEqual(raised.exception.code, "output-create-new-failed")
            self.assertEqual(output.read_bytes(), b"first")
            self.assertEqual(output.lstat().st_nlink, 1)
            self.assertEqual([entry.name for entry in root.iterdir()], [stage.name])

    def test_manifest_is_fixed_bounded_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            artifact = root / "artifact"
            artifact.mkdir()
            files = {
                "probe.json": self.helper.canonical_bytes(
                    self.helper.build_probe_document(
                        FROZEN_CANDIDATE_SHA,
                        eligible_context(),
                        eligible_observations(),
                    )
                ),
                "probe.status": b"0\n",
                "probe.stderr": b"",
                "probe.stdout": b"direct-session-eligible\n",
            }
            for name, raw in files.items():
                (artifact / name).write_bytes(raw)
            external_manifest = root / "SHA256SUMS.external"

            self.helper.write_artifact_manifest(artifact, external_manifest)
            manifest = artifact / "SHA256SUMS"
            external_manifest.replace(manifest)
            self.helper.verify_artifact_manifest(artifact, manifest)
            expected = b"".join(
                hashlib.sha256(files[name]).hexdigest().encode()
                + b"  ./"
                + name.encode()
                + b"\n"
                for name in sorted(files)
            )
            self.assertEqual(manifest.read_bytes(), expected)

            (artifact / "probe.stdout").write_bytes(b"tampered\n")
            with self.assertRaises(self.helper.ProbeError):
                self.helper.verify_artifact_manifest(artifact, manifest)

    def test_manifest_rejects_missing_extra_symlink_and_oversized_files(self) -> None:
        def populate(artifact: Path) -> None:
            (artifact / "probe.json").write_bytes(b"{}")
            (artifact / "probe.status").write_bytes(b"1\n")
            (artifact / "probe.stderr").write_bytes(b"")
            (artifact / "probe.stdout").write_bytes(b"")

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            for label in ("missing", "extra", "symlink", "oversized"):
                artifact = root / label
                artifact.mkdir()
                populate(artifact)
                if label == "missing":
                    (artifact / "probe.stdout").unlink()
                elif label == "extra":
                    (artifact / "unexpected").write_bytes(b"extra")
                elif label == "symlink":
                    (artifact / "probe.stdout").unlink()
                    (artifact / "probe.stdout").symlink_to("probe.stderr")
                else:
                    (artifact / "probe.stderr").write_bytes(
                        b"x" * (self.helper.MAX_STDERR_BYTES + 1)
                    )
                with (
                    self.subTest(label=label),
                    self.assertRaises(self.helper.ProbeError),
                ):
                    self.helper.write_artifact_manifest(
                        artifact,
                        root / f"{label}.SHA256SUMS",
                    )

    def test_provisioner_accepts_only_the_exact_direct_probe_status_pair(self) -> None:
        observations = eligible_observations()
        observations["credentials"]["admin_member"] = True
        observations["home"]["mode"] = 0o750
        document = self.helper.build_probe_document(
            FROZEN_CANDIDATE_SHA,
            eligible_context(),
            observations,
        )
        self.assertEqual(document["disposition"], "direct-session-ineligible")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "probe.json").write_bytes(self.helper.canonical_bytes(document))
            (root / "probe.status").write_bytes(b"1\n")
            (root / "probe.stderr").write_bytes(b"")
            (root / "probe.stdout").write_bytes(b"direct-session-ineligible\n")
            external = root.parent / f"{root.name}.SHA256SUMS"
            self.helper.write_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with mock.patch.dict(os.environ, eligible_context(), clear=True):
                self.helper.verify_provisioning_capability(root)

            (root / "probe.status").write_bytes(b"0\n")
            (root / "SHA256SUMS").unlink()
            self.helper.write_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "provisioner-probe-contract-disagrees",
                ),
            ):
                self.helper.verify_provisioning_capability(root)

    def test_workflow_is_exactly_guarded_and_runs_verified_probe_bytes(self) -> None:
        workflow = WORKFLOW.read_text()

        self.assertIn("name: Task Witness macOS ARM64 host probe\n", workflow)
        self.assertIn(f"      - {MACOS_HARNESS_BRANCH}\n", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("pull_request_target:", workflow)
        self.assertNotIn("workflow_run:", workflow)
        self.assertIn("  contents: read\n", workflow)
        self.assertIn("    runs-on: macos-15\n", workflow)
        self.assertIn("    timeout-minutes: 15\n", workflow)
        self.assertIn("github.repository == 'nisavid/agents'", workflow)
        self.assertIn("github.event_name == 'push'", workflow)
        self.assertIn(
            f"github.ref ==\n          'refs/heads/{MACOS_HARNESS_BRANCH}'",
            workflow,
        )
        self.assertIn(
            "vars.TASK_WITNESS_MACOS_HOST_PROBE_CANDIDATE ==\n"
            f"          '{FROZEN_CANDIDATE_SHA}'",
            workflow,
        )
        self.assertIn(
            "vars.TASK_WITNESS_MACOS_HOST_PROBE_HARNESS == github.sha",
            workflow,
        )
        self.assertEqual(workflow.count(FROZEN_CANDIDATE_SHA), 2)
        self.assertIn(
            "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            workflow,
        )
        self.assertIn(
            "uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            workflow,
        )
        self.assertIn("          persist-credentials: false\n", workflow)
        self.assertNotIn("env.PROBE_ROOT", workflow)
        self.assertNotIn("secrets.", workflow.lower())
        self.assertNotIn("candidate-stage", workflow)
        self.assertNotIn("run_task_witness_qualification.py", workflow)
        self.assertNotIn("validate_task_witness.py", workflow)

        test_command = (
            "          /usr/bin/python3 -I -B \\\n"
            "            harness/tests/test_task_witness_macos_host_probe.py\n"
        )
        helper_blob_check = (
            "          helper_blob=$(\n"
            "            /usr/bin/git -C harness rev-parse \\\n"
            '              "$GITHUB_SHA:scripts/probe_task_witness_macos_host.py"\n'
            "          )\n"
        )
        helper_execution = (
            "            harness/scripts/probe_task_witness_macos_host.py \\\n"
            "              probe \\\n"
        )
        self.assertEqual(workflow.count(test_command), 1)
        self.assertEqual(workflow.count(helper_blob_check), 2)
        self.assertEqual(workflow.count(helper_execution), 1)
        self.assertLess(
            workflow.index(helper_blob_check),
            workflow.index(test_command),
        )
        self.assertLess(workflow.index(test_command), workflow.index(helper_execution))

        upload = "      - name: Retain bounded macOS host probe diagnostics\n"
        terminal = "      - name: Require a launchd-capable macOS provisioner\n"
        self.assertEqual(workflow.count(upload), 1)
        self.assertEqual(workflow.count(terminal), 1)
        self.assertLess(workflow.index(upload), workflow.index(terminal))
        self.assertIn("          retention-days: 14\n", workflow)
        self.assertIn("          if-no-files-found: error\n", workflow)

    def test_workflow_preprobe_self_test_is_host_eligibility_neutral(self) -> None:
        workflow = WORKFLOW.read_text()
        tests = Path(__file__).read_text()
        test_command = (
            "          /usr/bin/python3 -I -B \\\n"
            "            harness/tests/test_task_witness_macos_host_probe.py\n"
        )
        capture_marker = "      - name: Capture the bounded direct-session probe\n"
        live_home_call = "Path." + "home()"
        live_darwin_guard = "@unittest.skipUnless(" + "platform.system()"
        deterministic_test = (
            "def test_darwin_filesystem_type_" + "decodes_mocked_statfs"
        )

        self.assertLess(workflow.index(test_command), workflow.index(capture_marker))
        self.assertNotIn(live_home_call, tests)
        self.assertNotIn(live_darwin_guard, tests)
        self.assertIn(deterministic_test, tests)

    def test_workflow_uploads_only_successfully_sealed_diagnostics(self) -> None:
        workflow = WORKFLOW.read_text()
        capture_marker = "      - name: Capture the bounded direct-session probe\n"
        seal_marker = "      - name: Seal the bounded diagnostic artifact\n"
        upload_marker = "      - name: Retain bounded macOS host probe diagnostics\n"
        terminal_marker = "      - name: Require a launchd-capable macOS provisioner\n"
        launchd_marker = "      - name: Capture the disposable launchd-user probe\n"

        capture = workflow[workflow.index(capture_marker) : workflow.index(seal_marker)]
        seal = workflow[workflow.index(seal_marker) : workflow.index(upload_marker)]
        upload = workflow[
            workflow.index(upload_marker) : workflow.index(terminal_marker)
        ]
        terminal = workflow[
            workflow.index(terminal_marker) : workflow.index(launchd_marker)
        ]

        self.assertIn("          set +e\n", capture)
        self.assertIn("          probe_status=$?\n          set -e\n", capture)
        self.assertNotIn('          exit "$probe_status"\n', capture)
        self.assertIn("        id: seal\n", seal)
        self.assertIn("        if: always()\n", seal)
        self.assertIn(
            "        if: ${{ always() && steps.seal.outcome == 'success' }}\n",
            upload,
        )
        self.assertNotIn("probe.status", upload)
        self.assertIn(
            "              verify-provisioner \\\n",
            terminal,
        )
        self.assertNotIn("probe.status", terminal)

    def test_workflow_uses_the_portable_macos_test_binary(self) -> None:
        workflow = WORKFLOW.read_text()

        self.assertNotIn("/usr/bin/test", workflow)
        self.assertEqual(workflow.count("          /bin/test "), 28)

    def test_linux_workflow_cannot_run_on_the_macos_probe_branch(self) -> None:
        workflow = LINUX_WORKFLOW.read_text()
        self.assertIn(f"      - {LINUX_HARNESS_BRANCH}\n", workflow)
        self.assertNotIn(MACOS_HARNESS_BRANCH, workflow)
        branch_block = f"    branches:\n      - {LINUX_HARNESS_BRANCH}\n    paths:\n"
        self.assertEqual(workflow.count(branch_block), 1)


class TaskWitnessMacOSLaunchdUserProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = load_helper()
        self.xattr_patcher = mock.patch.object(
            self.helper,
            "_require_no_extended_attributes",
            return_value=None,
        )
        self.xattr_check = self.xattr_patcher.start()
        self.addCleanup(self.xattr_patcher.stop)
        self.file_xattr_state_patcher = mock.patch.object(
            self.helper,
            "_home_library_file_xattr_state",
            return_value=None,
        )
        self.file_xattr_state = self.file_xattr_state_patcher.start()
        self.addCleanup(self.file_xattr_state_patcher.stop)

    def launchd_context(self) -> dict[str, str]:
        context = eligible_context()
        _account_name, label = self.helper._launchd_identity(context)
        context["TASK_WITNESS_LAUNCHD_LABEL"] = label
        return context

    def launchd_observations(self) -> dict[str, object]:
        account_name, label = self.helper._launchd_identity(eligible_context())
        home = f"/Users/{account_name}"
        observations = eligible_observations()
        observations.pop("provisioning_capability")
        observations["credentials"] = {
            **observations["credentials"],
            "real_uid": 502,
            "effective_uid": 502,
            "real_gid": 20,
            "effective_gid": 20,
            "supplementary_gids": [20],
            "passwd_group_gids": [20],
            "passwd": {
                "name": account_name,
                "uid": 502,
                "primary_gid": 20,
                "home": home,
                "shell": "/usr/bin/false",
            },
        }
        observations["home"] = {
            **observations["home"],
            "path": home,
            "uid": 502,
            "gid": 20,
        }
        observations["environment_exact"] = True
        observations["passwordless_sudo"] = False
        observations["process"] = {
            "label": label,
            "parent_path": "/sbin/launchd",
            "pid": 1234,
            "ppid": 1,
        }
        return observations

    def lifecycle_plan(self, stage: Path) -> object:
        account_name, label = self.helper._launchd_identity(eligible_context())
        account = self.helper.DisposableAccount(
            name=account_name,
            uid=502,
            gid=20,
            home=Path("/Users") / account_name,
        )
        return self.helper.LaunchdPlan(
            account=account,
            label=label,
            stage_root=stage,
            helper=stage / "helper.py",
            plist=stage / "job.plist",
        )

    def lifecycle_state(self, plan: object) -> dict[str, object]:
        return self.helper._document_with_digest(
            {
                "schema_version": 1,
                "contract": "task-witness-macos-launchd-lifecycle-state-v1",
                "candidate_sha1": FROZEN_CANDIDATE_SHA,
                "account": {
                    "name": plan.account.name,
                    "uid": plan.account.uid,
                    "gid": plan.account.gid,
                    "home": str(plan.account.home),
                },
                "label": plan.label,
                "stage_root": str(plan.stage_root),
                "helper_sha256": "1" * 64,
                "plist_sha256": "2" * 64,
                "ownership_marker": "3" * 32,
                "runner_uid": 501,
                "runner_gid": 20,
            }
        )

    def write_extended_attribute(self, path: Path, name: str) -> None:
        subprocess.run(
            ["/usr/bin/xattr", "-w", name, "private-xattr-canary", str(path)],
            check=True,
            capture_output=True,
        )

    def guard_home_validation_mutations(self, stack: ExitStack) -> tuple:
        return (
            stack.enter_context(
                mock.patch.object(self.helper, "_write_stage_create_new")
            ),
            stack.enter_context(mock.patch.object(self.helper, "_renameat_exclusive")),
            stack.enter_context(mock.patch.object(self.helper.os, "unlink")),
            stack.enter_context(mock.patch.object(self.helper.os, "rmdir")),
            stack.enter_context(mock.patch.object(Path, "unlink")),
            stack.enter_context(mock.patch.object(Path, "rmdir")),
        )

    def bounded_library_cleanup_fixture(
        self,
        root: Path,
    ) -> tuple[object, dict[str, int], dict, tuple[tuple[str, ...], ...]]:
        stage = root / "stage"
        stage.mkdir()
        home = root / "home"
        probe = home / "launchd-probe"
        deep = home / "Library" / "Container" / "Deep"
        preferences = home / "Library" / "Preferences"
        probe.mkdir(parents=True, mode=0o700)
        deep.mkdir(parents=True)
        preferences.mkdir()
        home.chmod(0o700)
        probe.chmod(0o700)
        for name in self.helper.LAUNCHD_CHILD_FILES:
            (probe / name).write_bytes(b"value")
        relative_files = (
            ("Container", "Deep", "payload.bin"),
            ("Preferences", "settings.plist"),
        )
        (home / "Library").joinpath(*relative_files[0]).write_bytes(b"payload")
        (home / "Library").joinpath(*relative_files[1]).write_bytes(b"settings")
        account = self.helper.DisposableAccount(
            name="twq-0123456789ab",
            uid=os.geteuid(),
            gid=os.getegid(),
            home=home,
        )
        plan = self.helper.LaunchdPlan(
            account=account,
            label="io.nisavid.task-witness.macos-probe.0123456789ab",
            stage_root=stage,
            helper=stage / "helper.py",
            plist=stage / "job.plist",
        )
        state = self.lifecycle_state(plan)
        account_binding = self.helper._account_binding_document(
            plan,
            state,
            "01234567-89AB-4DEF-8123-456789ABCDEF",
        )
        bindings = self.helper.ValidatedStageBindings(account_binding, None)
        home_metadata = home.lstat()
        probe_metadata = probe.lstat()
        identity = {
            "home_device": home_metadata.st_dev,
            "home_inode": home_metadata.st_ino,
            "probe_device": probe_metadata.st_dev,
            "probe_inode": probe_metadata.st_ino,
        }
        inventory = self.helper._bounded_library_inventory(account)
        self.assertIsInstance(inventory, dict)
        authorization = self.helper._home_cleanup_document(
            plan,
            state,
            bindings,
            identity,
            inventory,
        )
        self.assertEqual(authorization["schema_version"], 2)
        self.assertEqual(authorization["library_inventory"], inventory)
        self.helper._require_content_digest(
            authorization,
            "home-cleanup-authorization",
        )
        return account, identity, authorization, relative_files

    def rename_library_portably(
        self,
        source_parent_descriptor: int,
        source_name: str,
        destination_parent_descriptor: int,
        destination_name: str,
    ) -> None:
        self.assertEqual(source_parent_descriptor, destination_parent_descriptor)
        self.assertEqual(source_name, self.helper.HOME_LIBRARY_NAME)
        self.assertEqual(
            destination_name,
            self.helper.HOME_LIBRARY_QUARANTINE_NAME,
        )
        os.rename(
            source_name,
            destination_name,
            src_dir_fd=source_parent_descriptor,
            dst_dir_fd=destination_parent_descriptor,
        )

    def launchctl_job(self, plan: object, state: dict[str, object]) -> str:
        plist = self.helper.build_launchd_user_plist(
            label=plan.label,
            user=plan.account.name,
            home=plan.account.home,
            helper=plan.helper,
            candidate_sha=FROZEN_CANDIDATE_SHA,
            environment=self.launchd_context(),
            ownership_marker=state["ownership_marker"],
        )
        arguments = plist["ProgramArguments"]
        argument_lines = "\n".join(f"\t\t{item}" for item in arguments)
        environment = plist["EnvironmentVariables"]
        environment_lines = "\n".join(
            f"\t\t{name} => {value}" for name, value in sorted(environment.items())
        )
        return (
            f"system/{plan.label} = {{\n"
            "\tactive count = 0\n"
            f"\tpath = {plan.plist}\n"
            "\tstate = not running\n\n"
            f"\tprogram = {plist['Program']}\n"
            "\targuments = {\n"
            f"{argument_lines}\n"
            "\t}\n\n"
            "\tenvironment = {\n"
            f"{environment_lines}\n"
            f"\t\tXPC_SERVICE_NAME => {plan.label}\n"
            "\t}\n\n"
            "\tdomain = system\n"
            f"\tusername = {plan.account.name}\n"
            "\truns = 1\n"
            "\tlast exit code = 0\n"
            "}\n"
        )

    def launchctl_job_with_unrelated_blocks(
        self,
        plan: object,
        state: dict[str, object],
        *,
        secret: str,
    ) -> str:
        return (
            self.launchctl_job(plan, state).removesuffix("}\n")
            + "\tdefault environment = {\n"
            "\t\tPATH => /usr/bin:/bin\n"
            f"\t\tUNRELATED_SECRET => {secret}\n"
            "\t}\n"
            "\tresource coalition = {\n"
            "\t\tstate = active\n"
            "\t\tactive count = 1\n"
            "\t}\n"
            "\tjetsam coalition = {\n"
            "\t\tstate = inactive\n"
            "\t\tactive count = 0\n"
            "\t}\n"
            "}\n"
        )

    def test_launchd_user_document_is_a_distinct_eligible_probe_claim(self) -> None:
        document = self.helper.build_launchd_user_probe_document(
            FROZEN_CANDIDATE_SHA,
            self.launchd_context(),
            self.launchd_observations(),
        )

        self.assertEqual(
            document["contract"],
            "task-witness-macos-github-launchd-user-probe-v1",
        )
        self.assertEqual(document["claim"], "host-prerequisite-probe-only")
        self.assertEqual(document["disposition"], "launchd-user-eligible")
        self.assertTrue(all(document["requirements"].values()))
        self.assertNotIn("provisioning_capability", document["observations"])
        self.assertNotIn("qualified", self.helper.canonical_bytes(document).decode())

    def test_launchd_child_security_observations_are_required_for_eligibility(
        self,
    ) -> None:
        observations = self.launchd_observations()
        observations["environment_exact"] = True
        observations["passwordless_sudo"] = False
        document = self.helper.build_launchd_user_probe_document(
            FROZEN_CANDIDATE_SHA,
            self.launchd_context(),
            observations,
        )
        self.assertTrue(document["requirements"]["child_environment_exact"])
        self.assertTrue(document["requirements"]["passwordless_sudo_absent"])
        self.assertEqual(document["disposition"], "launchd-user-eligible")

        observations["passwordless_sudo"] = True
        document = self.helper.build_launchd_user_probe_document(
            FROZEN_CANDIDATE_SHA,
            self.launchd_context(),
            observations,
        )
        self.assertFalse(document["requirements"]["passwordless_sudo_absent"])
        self.assertEqual(document["disposition"], "launchd-user-ineligible")

        observations["passwordless_sudo"] = False
        observations["environment_exact"] = False
        document = self.helper.build_launchd_user_probe_document(
            FROZEN_CANDIDATE_SHA,
            self.launchd_context(),
            observations,
        )
        self.assertFalse(document["requirements"]["child_environment_exact"])
        self.assertEqual(document["disposition"], "launchd-user-ineligible")

    def test_launchd_collector_records_only_status_derived_sudo_capability(
        self,
    ) -> None:
        context = self.launchd_context()
        marker = "3" * 32
        base = self.launchd_observations()
        base.pop("process")
        home = Path(base["home"]["path"])
        child_environment = self.helper.build_launchd_user_plist(
            label=context["TASK_WITNESS_LAUNCHD_LABEL"],
            user=base["credentials"]["passwd"]["name"],
            home=home,
            helper=Path("/private/var/tmp/task-witness-probe/helper.py"),
            candidate_sha=FROZEN_CANDIDATE_SHA,
            environment=context,
            ownership_marker=marker,
        )["EnvironmentVariables"]

        for sudo_status, expected in ((0, True), (1, False)):
            completed = SimpleNamespace(returncode=sudo_status)
            with (
                self.subTest(sudo_status=sudo_status),
                mock.patch.dict(os.environ, child_environment, clear=True),
                mock.patch.object(
                    self.helper,
                    "collect_observations",
                    return_value=json.loads(json.dumps(base)),
                ),
                mock.patch.object(
                    self.helper,
                    "_darwin_process_path",
                    return_value="/sbin/launchd",
                ),
                mock.patch.object(self.helper.os, "getpid", return_value=1234),
                mock.patch.object(self.helper.os, "getppid", return_value=1),
                mock.patch.object(
                    self.helper.subprocess,
                    "run",
                    return_value=completed,
                ) as run,
            ):
                observations = self.helper.collect_launchd_observations()

            self.assertIs(observations["passwordless_sudo"], expected)
            self.assertIs(observations["environment_exact"], True)
            self.assertNotIn(
                "stdout", self.helper.canonical_bytes(observations).decode()
            )
            self.assertNotIn(
                "stderr", self.helper.canonical_bytes(observations).decode()
            )
            run.assert_called_once_with(
                ["/usr/bin/sudo", "-n", "/usr/bin/true"],
                check=False,
                stdin=self.helper.subprocess.DEVNULL,
                stdout=self.helper.subprocess.DEVNULL,
                stderr=self.helper.subprocess.DEVNULL,
                timeout=self.helper.COMMAND_TIMEOUT_SECONDS,
                env={
                    "HOME": "/var/empty",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                    "TZ": "UTC",
                },
            )

        failures = (
            (
                "timeout",
                {
                    "side_effect": self.helper.subprocess.TimeoutExpired(
                        ["/usr/bin/sudo", "-n", "/usr/bin/true"],
                        self.helper.COMMAND_TIMEOUT_SECONDS,
                    )
                },
            ),
            ("oserror", {"side_effect": OSError("sudo-exec-failed")}),
            ("signal", {"return_value": SimpleNamespace(returncode=-9)}),
        )
        for failure, run_behavior in failures:
            with (
                self.subTest(failure=failure),
                mock.patch.dict(os.environ, child_environment, clear=True),
                mock.patch.object(
                    self.helper,
                    "collect_observations",
                    return_value=json.loads(json.dumps(base)),
                ),
                mock.patch.object(
                    self.helper,
                    "_darwin_process_path",
                    return_value="/sbin/launchd",
                ),
                mock.patch.object(self.helper.os, "getpid", return_value=1234),
                mock.patch.object(self.helper.os, "getppid", return_value=1),
                mock.patch.object(
                    self.helper.subprocess,
                    "run",
                    **run_behavior,
                ),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "passwordless-sudo-probe-failed",
                ),
            ):
                self.helper.collect_launchd_observations()

        for returncode in (True, None, 256):
            with (
                self.subTest(returncode=returncode),
                mock.patch.object(
                    self.helper.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=returncode),
                ),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "passwordless-sudo-probe-failed",
                ),
            ):
                self.helper._passwordless_sudo_available()

        with (
            mock.patch.object(
                self.helper.subprocess,
                "run",
                return_value=object(),
            ),
            self.assertRaisesRegex(
                self.helper.ProbeError,
                "passwordless-sudo-probe-failed",
            ),
        ):
            self.helper._passwordless_sudo_available()

    def test_inconclusive_sudo_probe_produces_only_probe_error_artifact(
        self,
    ) -> None:
        context = self.launchd_context()
        marker = "3" * 32
        base = self.launchd_observations()
        base.pop("process")
        home = Path(base["home"]["path"])
        child_environment = self.helper.build_launchd_user_plist(
            label=context["TASK_WITNESS_LAUNCHD_LABEL"],
            user=base["credentials"]["passwd"]["name"],
            home=home,
            helper=Path("/private/var/tmp/task-witness-probe/helper.py"),
            candidate_sha=FROZEN_CANDIDATE_SHA,
            environment=context,
            ownership_marker=marker,
        )["EnvironmentVariables"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "launchd-probe"
            root.mkdir()
            output = root / "probe.json"
            status_output = root / "probe.status"
            with (
                mock.patch.dict(os.environ, child_environment, clear=True),
                mock.patch.object(
                    self.helper,
                    "_validate_launchd_child_entry_home",
                ),
                mock.patch.object(
                    self.helper,
                    "collect_observations",
                    return_value=json.loads(json.dumps(base)),
                ),
                mock.patch.object(
                    self.helper,
                    "_darwin_process_path",
                    return_value="/sbin/launchd",
                ),
                mock.patch.object(self.helper.os, "getpid", return_value=1234),
                mock.patch.object(self.helper.os, "getppid", return_value=1),
                mock.patch.object(
                    self.helper.subprocess,
                    "run",
                    side_effect=self.helper.subprocess.TimeoutExpired(
                        ["/usr/bin/sudo", "-n", "/usr/bin/true"],
                        self.helper.COMMAND_TIMEOUT_SECONDS,
                    ),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                status = self.helper.run_launchd_user_probe(
                    output,
                    status_output,
                    FROZEN_CANDIDATE_SHA,
                )

            document = json.loads(output.read_text())
            self.assertEqual(status, 2)
            self.assertEqual(status_output.read_bytes(), b"2\n")
            self.assertEqual(document["disposition"], "probe-error")
            self.assertEqual(
                document["error"]["code"],
                "passwordless-sudo-probe-failed",
            )
            self.assertIsNone(document["observations"])
            self.assertIsNone(document["requirements"])
            self.assertNotIn("launchd-user-eligible", output.read_text())

    def test_launchd_child_entry_checkpoint_binds_exact_identity(self) -> None:
        context = self.launchd_context()
        marker = "3" * 32
        account_name, label = self.helper._launchd_identity(context)
        home = Path("/Users") / account_name
        child_environment = self.helper.build_launchd_user_plist(
            label=label,
            user=account_name,
            home=home,
            helper=Path("/private/var/tmp/task-witness-probe/helper.py"),
            candidate_sha=FROZEN_CANDIDATE_SHA,
            environment=context,
            ownership_marker=marker,
        )["EnvironmentVariables"]
        output = home / "launchd-probe/probe.json"
        status_output = home / "launchd-probe/probe.status"

        with (
            mock.patch.dict(os.environ, child_environment, clear=True),
            mock.patch.object(self.helper.os, "geteuid", return_value=550),
            mock.patch.object(self.helper.os, "getegid", return_value=20),
            mock.patch.object(
                self.helper,
                "_validate_disposable_home_root",
            ) as validate,
        ):
            self.helper._validate_launchd_child_entry_home(
                output,
                status_output,
            )

        validate.assert_called_once_with(
            self.helper.DisposableAccount(
                name=account_name,
                uid=550,
                gid=20,
                home=home,
            ),
            diagnostic_phase="child-entry",
        )

        for label_name, environment in (
            ("home", {**child_environment, "HOME": "/Users/foreign"}),
            (
                "marker",
                {
                    **child_environment,
                    "TASK_WITNESS_LAUNCHD_OWNERSHIP_MARKER": "invalid",
                },
            ),
        ):
            with (
                self.subTest(label=label_name),
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(
                    self.helper,
                    "_validate_disposable_home_root",
                ) as rejected_validate,
                self.assertRaises(self.helper.ProbeError),
            ):
                self.helper._validate_launchd_child_entry_home(
                    output,
                    status_output,
                )
            rejected_validate.assert_not_called()

        foreign_root = Path("/private/tmp/private-launchd-output-canary")
        for label_name, rejected_output, rejected_status in (
            (
                "output",
                foreign_root / "probe.json",
                status_output,
            ),
            (
                "status",
                output,
                foreign_root / "probe.status",
            ),
            (
                "both",
                foreign_root / "probe.json",
                foreign_root / "probe.status",
            ),
        ):
            with (
                self.subTest(label=label_name),
                mock.patch.dict(os.environ, child_environment, clear=True),
                mock.patch.object(
                    self.helper,
                    "_validate_disposable_home_root",
                ) as rejected_validate,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._validate_launchd_child_entry_home(
                    rejected_output,
                    rejected_status,
                )
            self.assertEqual(raised.exception.code, "invalid-launchd-output-paths")
            self.assertNotIn("private-launchd-output-canary", raised.exception.code)
            rejected_validate.assert_not_called()

    def test_launchd_child_entry_failure_precedes_observation_collection(
        self,
    ) -> None:
        context = self.launchd_context()
        code = "home-cleanup-child-entry-home-entry-known-library-owned-directory"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "launchd-probe"
            root.mkdir()
            output = root / "probe.json"
            status_output = root / "probe.status"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.dict(os.environ, context, clear=True),
                mock.patch.object(
                    self.helper,
                    "_validate_launchd_child_entry_home",
                    side_effect=self.helper.ProbeError(code),
                ) as validate,
                mock.patch.object(
                    self.helper,
                    "collect_launchd_observations",
                ) as collect,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                status = self.helper.run_launchd_user_probe(
                    output,
                    status_output,
                    FROZEN_CANDIDATE_SHA,
                )

            document = json.loads(output.read_text())
            self.assertEqual(status, 2)
            self.assertEqual(status_output.read_bytes(), b"2\n")
            self.assertEqual(document["disposition"], "probe-error")
            self.assertEqual(document["error"], {"code": code})
            self.assertIsNone(document["observations"])
            self.assertIsNone(document["requirements"])
            validate.assert_called_once_with(output, status_output)
            collect.assert_not_called()
            self.assertEqual(stdout.getvalue(), "probe-error\n")
            self.assertEqual(
                stderr.getvalue(),
                f"task-witness macOS launchd-user probe: {code}\n",
            )
            self.assertNotIn(str(root), self.helper.canonical_bytes(document).decode())
            self.assertNotIn(str(root), stdout.getvalue())
            self.assertNotIn(str(root), stderr.getvalue())

    def test_child_accepts_only_the_exact_plist_environment(self) -> None:
        context = self.launchd_context()
        marker = "3" * 32
        account_name, label = self.helper._launchd_identity(context)
        home = Path("/Users") / account_name
        expected = self.helper.build_launchd_user_plist(
            label=label,
            user=account_name,
            home=home,
            helper=Path("/private/var/tmp/task-witness-probe/helper.py"),
            candidate_sha=FROZEN_CANDIDATE_SHA,
            environment=context,
            ownership_marker=marker,
        )["EnvironmentVariables"]
        self.helper._require_exact_launchd_child_environment(
            expected,
            label=label,
            home=home,
            ownership_marker=marker,
        )

        mutations = {
            "missing": {
                name: value for name, value in expected.items() if name != "HOME"
            },
            "altered": {**expected, "HOME": "/Users/foreign"},
            "extra": {**expected, "UNEXPECTED_SECRET": "must-not-cross"},
            "launchd-xpc-name": {**expected, "XPC_SERVICE_NAME": label},
            "launchd-xpc-flags": {**expected, "XPC_FLAGS": "0x0"},
            "launchd-user": {**expected, "USER": account_name},
            "launchd-logname": {**expected, "LOGNAME": account_name},
            "launchd-shell": {**expected, "SHELL": "/usr/bin/false"},
            "launchd-tmpdir": {**expected, "TMPDIR": "/private/var/folders/x"},
            "launchd-cf-encoding": {
                **expected,
                "__CF_USER_TEXT_ENCODING": "0x1F6:0x0:0x0",
            },
        }
        for label_name, mutation in mutations.items():
            with (
                self.subTest(label=label_name),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-child-environment-invalid",
                ),
            ):
                self.helper._require_exact_launchd_child_environment(
                    mutation,
                    label=label,
                    home=home,
                    ownership_marker=marker,
                )

    def test_launchd_program_scrubs_inherited_environment_before_python(
        self,
    ) -> None:
        context = self.launchd_context()
        marker = "3" * 32
        account_name, label = self.helper._launchd_identity(context)
        home = Path("/Users") / account_name
        helper = Path("/private/var/tmp/task-witness-probe/helper.py")
        plist = self.helper.build_launchd_user_plist(
            label=label,
            user=account_name,
            home=home,
            helper=helper,
            candidate_sha=FROZEN_CANDIDATE_SHA,
            environment=context,
            ownership_marker=marker,
        )

        self.assertEqual(plist["Program"], "/usr/bin/env")
        self.assertEqual(
            plist["ProgramArguments"],
            [
                "/usr/bin/env",
                "-i",
                "/usr/bin/python3",
                "-I",
                "-B",
                str(helper),
                "probe-launchd-user",
                "--candidate-sha",
                FROZEN_CANDIDATE_SHA,
                "--output",
                str(home / "launchd-probe/probe.json"),
                "--status-output",
                str(home / "launchd-probe/probe.status"),
            ],
        )
        self.assertNotIn("/bin/sh", plist["ProgramArguments"])
        self.assertNotIn("-S", plist["ProgramArguments"])
        self.assertNotIn("-P", plist["ProgramArguments"])

        expected_environment = dict(plist["EnvironmentVariables"])
        raw = plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)
        self.assertEqual(
            self.helper._validated_launchd_child_environment_from_plist(
                raw,
                helper,
            ),
            expected_environment,
        )
        with (
            mock.patch.dict(
                os.environ,
                {"UNEXPECTED_SECRET": "must-not-cross"},
                clear=True,
            ),
            mock.patch.object(
                self.helper,
                "_load_staged_launchd_child_environment",
                return_value=expected_environment,
            ) as load_environment,
        ):
            self.helper._prepare_launchd_child_environment(helper)
            self.assertEqual(dict(os.environ), expected_environment)
        load_environment.assert_called_once_with(helper)

        mutations = {}
        extra_environment = json.loads(json.dumps(plist))
        extra_environment["EnvironmentVariables"]["UNEXPECTED_SECRET"] = (
            "must-not-cross"
        )
        mutations["extra-environment"] = extra_environment
        wrong_program = json.loads(json.dumps(plist))
        wrong_program["Program"] = "/usr/bin/python3"
        mutations["wrong-program"] = wrong_program
        wrong_helper = json.loads(json.dumps(plist))
        wrong_helper["ProgramArguments"][5] = "/private/var/tmp/foreign/helper.py"
        mutations["wrong-helper"] = wrong_helper
        wrong_candidate = json.loads(json.dumps(plist))
        wrong_candidate["ProgramArguments"][8] = "0" * 40
        mutations["wrong-candidate"] = wrong_candidate
        for label_name, mutation in mutations.items():
            with (
                self.subTest(label=label_name),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._validated_launchd_child_environment_from_plist(
                    plistlib.dumps(
                        mutation,
                        fmt=plistlib.FMT_XML,
                        sort_keys=True,
                    ),
                    helper,
                )
            self.assertEqual(
                raised.exception.code,
                "launchd-child-environment-invalid",
            )
            self.assertNotIn("must-not-cross", str(raised.exception))

    def test_launchd_probe_cli_prepares_the_scrubbed_environment_first(
        self,
    ) -> None:
        output = Path("/Users/twq-test/launchd-probe/probe.json")
        status_output = Path("/Users/twq-test/launchd-probe/probe.status")
        arguments = SimpleNamespace(
            command="probe-launchd-user",
            candidate_sha=FROZEN_CANDIDATE_SHA,
            output=output,
            status_output=status_output,
        )
        events = []
        parser = mock.Mock()
        parser.parse_args.return_value = arguments
        with (
            mock.patch.object(self.helper, "parser", return_value=parser),
            mock.patch.object(
                self.helper,
                "_prepare_launchd_child_environment",
                side_effect=lambda helper: events.append(("prepare", helper)),
            ) as prepare,
            mock.patch.object(
                self.helper,
                "run_launchd_user_probe",
                side_effect=lambda *args: events.append(("run", args)) or 0,
            ) as run,
        ):
            self.assertEqual(self.helper.main(), 0)

        prepare.assert_called_once_with(Path(self.helper.__file__))
        run.assert_called_once_with(output, status_output, FROZEN_CANDIDATE_SHA)
        self.assertEqual([event[0] for event in events], ["prepare", "run"])

    def test_staged_launchd_environment_requires_each_root_owned_component(
        self,
    ) -> None:
        helper = Path("/private/var/tmp/task-witness-macos-launchd-123-1/helper.py")
        stage_root = helper.parent
        plist = stage_root / "job.plist"
        expected_calls = [
            mock.call(
                stage_root,
                kind="directory",
                mode=0o755,
                uid=0,
                gid=0,
            ),
            mock.call(
                helper,
                kind="file",
                mode=0o555,
                uid=0,
                gid=0,
                nlink=1,
            ),
            mock.call(
                plist,
                kind="file",
                mode=0o644,
                uid=0,
                gid=0,
                nlink=1,
            ),
        ]
        cases = {
            "stage-root": [False],
            "helper": [True, False],
            "plist": [True, True, False],
        }
        for label, metadata_results in cases.items():
            with (
                self.subTest(label=label),
                mock.patch.object(
                    self.helper,
                    "_metadata_matches",
                    side_effect=metadata_results,
                ) as metadata_matches,
                mock.patch.object(
                    self.helper,
                    "_read_stable_regular_file",
                ) as read_plist,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._load_staged_launchd_child_environment(helper)
            self.assertEqual(
                raised.exception.code,
                "launchd-child-environment-invalid",
            )
            self.assertEqual(
                metadata_matches.call_args_list,
                expected_calls[: len(metadata_results)],
            )
            read_plist.assert_not_called()

    def test_staged_launchd_environment_rejects_unsafe_plist_before_scrub(
        self,
    ) -> None:
        context = self.launchd_context()
        marker = "3" * 32
        account_name, label = self.helper._launchd_identity(context)
        home = Path("/Users") / account_name
        cases = ("symlink", "hardlink", "drift")

        for case in cases:
            with tempfile.TemporaryDirectory() as directory, self.subTest(case=case):
                stage_root = Path(directory)
                helper = stage_root / "helper.py"
                helper.write_bytes(b"trusted helper")
                raw = plistlib.dumps(
                    self.helper.build_launchd_user_plist(
                        label=label,
                        user=account_name,
                        home=home,
                        helper=helper,
                        candidate_sha=FROZEN_CANDIDATE_SHA,
                        environment=context,
                        ownership_marker=marker,
                    ),
                    fmt=plistlib.FMT_XML,
                    sort_keys=True,
                )
                target = stage_root / "target.plist"
                target.write_bytes(raw)
                plist = stage_root / "job.plist"
                if case == "symlink":
                    plist.symlink_to(target.name)
                elif case == "hardlink":
                    os.link(target, plist)
                else:
                    plist.write_bytes(raw)

                original_fstat = os.fstat
                fstat_calls = 0

                def changed_fstat(
                    descriptor: int,
                    original=original_fstat,
                    active_case=case,
                ) -> object:
                    nonlocal fstat_calls
                    result = original(descriptor)
                    fstat_calls += 1
                    if active_case != "drift" or fstat_calls != 2:
                        return result
                    return SimpleNamespace(
                        st_dev=result.st_dev,
                        st_gid=result.st_gid,
                        st_ino=result.st_ino,
                        st_mode=result.st_mode,
                        st_mtime_ns=result.st_mtime_ns + 1,
                        st_nlink=result.st_nlink,
                        st_size=result.st_size,
                        st_uid=result.st_uid,
                    )

                expected_code = {
                    "symlink": "unreadable-staged-plist",
                    "hardlink": "unsafe-staged-plist",
                    "drift": "changed-staged-plist",
                }[case]
                with (
                    mock.patch.dict(
                        os.environ,
                        {"UNEXPECTED_SECRET": "must-remain-on-failure"},
                        clear=True,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        return_value=True,
                    ),
                    mock.patch.object(os, "fstat", side_effect=changed_fstat),
                ):
                    with self.assertRaises(self.helper.ProbeError) as raised:
                        self.helper._prepare_launchd_child_environment(helper)
                    self.assertEqual(raised.exception.code, expected_code)
                    self.assertEqual(
                        dict(os.environ),
                        {"UNEXPECTED_SECRET": "must-remain-on-failure"},
                    )

    def test_each_launchd_child_boundary_fails_closed(self) -> None:
        cases: dict[str, tuple[str, object]] = {
            "real-uid": ("credentials.real_uid", 0),
            "effective-uid": ("credentials.effective_uid", 0),
            "real-gid": ("credentials.real_gid", 80),
            "effective-gid": ("credentials.effective_gid", 80),
            "groups": ("credentials.passwd_group_gids", [20, 80]),
            "admin": ("credentials.admin_member", True),
            "issetugid": ("credentials.issetugid", True),
            "ppid": ("process.ppid", 2),
            "parent": ("process.parent_path", "/bin/sh"),
            "label": ("process.label", "io.nisavid.foreign"),
            "home-mode": ("home.mode", 0o750),
            "home-owner": ("home.uid", 503),
            "home-group": ("home.gid", 80),
            "home-filesystem": ("home.filesystem_type", "hfs"),
            "home-symlink": ("home.symlink_components", ["/Users/alias"]),
            "translated": ("platform.translated", True),
            "machine": ("platform.machine", "x86_64"),
        }
        for label, (path, replacement) in cases.items():
            observations = json.loads(json.dumps(self.launchd_observations()))
            parent, leaf = path.split(".")
            observations[parent][leaf] = replacement
            document = self.helper.build_launchd_user_probe_document(
                FROZEN_CANDIDATE_SHA,
                self.launchd_context(),
                observations,
            )
            with self.subTest(label=label):
                self.assertEqual(
                    document["disposition"],
                    "launchd-user-ineligible",
                )
                self.assertFalse(all(document["requirements"].values()))

    def test_launchd_child_rejects_root_group_identity_and_membership(self) -> None:
        baseline = self.helper.build_launchd_user_probe_document(
            FROZEN_CANDIDATE_SHA,
            self.launchd_context(),
            self.launchd_observations(),
        )
        self.assertTrue(baseline["requirements"]["nonroot_primary_gids"])
        self.assertTrue(baseline["requirements"]["root_group_absent"])

        cases = {
            "real-gid": ("credentials", "real_gid", 0, "nonroot_primary_gids"),
            "effective-gid": (
                "credentials",
                "effective_gid",
                0,
                "nonroot_primary_gids",
            ),
            "passwd-primary-gid": (
                "passwd",
                "primary_gid",
                0,
                "nonroot_primary_gids",
            ),
            "home-gid": ("home", "gid", 0, "nonroot_primary_gids"),
        }
        for label, (owner, field, value, requirement) in cases.items():
            observations = json.loads(json.dumps(self.launchd_observations()))
            target = (
                observations["credentials"]["passwd"]
                if owner == "passwd"
                else observations[owner]
            )
            target[field] = value
            document = self.helper.build_launchd_user_probe_document(
                FROZEN_CANDIDATE_SHA,
                self.launchd_context(),
                observations,
            )
            with self.subTest(label=label):
                self.assertEqual(document["disposition"], "launchd-user-ineligible")
                self.assertFalse(document["requirements"][requirement])

        observations = json.loads(json.dumps(self.launchd_observations()))
        observations["credentials"]["supplementary_gids"] = [0, 20]
        observations["credentials"]["passwd_group_gids"] = [0, 20]
        document = self.helper.build_launchd_user_probe_document(
            FROZEN_CANDIDATE_SHA,
            self.launchd_context(),
            observations,
        )
        self.assertTrue(document["requirements"]["group_views_agree"])
        self.assertFalse(document["requirements"]["root_group_absent"])
        self.assertEqual(document["disposition"], "launchd-user-ineligible")

    def test_launchd_plist_is_direct_system_domain_user_execution(self) -> None:
        context = self.launchd_context()
        home = Path("/Users/twq-0123456789ab")
        helper = Path("/private/var/tmp/task-witness-probe/helper.py")
        plist = self.helper.build_launchd_user_plist(
            label=context["TASK_WITNESS_LAUNCHD_LABEL"],
            user="twq-0123456789ab",
            home=home,
            helper=helper,
            candidate_sha=FROZEN_CANDIDATE_SHA,
            environment=context,
            ownership_marker="3" * 32,
        )

        self.assertEqual(plist["Program"], "/usr/bin/env")
        self.assertEqual(plist["UserName"], "twq-0123456789ab")
        self.assertTrue(plist["InitGroups"])
        self.assertNotIn("GroupName", plist)
        self.assertNotIn("RunAtLoad", plist)
        self.assertNotIn("KeepAlive", plist)
        self.assertEqual(plist["Umask"], 0o077)
        self.assertEqual(plist["WorkingDirectory"], str(home))
        self.assertEqual(
            plist["EnvironmentVariables"]["TASK_WITNESS_LAUNCHD_OWNERSHIP_MARKER"],
            "3" * 32,
        )
        self.assertEqual(
            plist["ProgramArguments"][:6],
            [
                "/usr/bin/env",
                "-i",
                "/usr/bin/python3",
                "-I",
                "-B",
                str(helper),
            ],
        )
        argv = "\0".join(plist["ProgramArguments"])
        self.assertNotIn("sudo", argv)
        self.assertNotIn("/bin/su", argv)
        self.assertNotIn("/bin/sh", argv)

    def test_launchd_job_binding_requires_exact_environment_and_runtime_shape(
        self,
    ) -> None:
        context = self.launchd_context()
        plan = self.lifecycle_plan(
            Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        )
        state = self.lifecycle_state(plan)
        loaded = self.launchctl_job(plan, state)
        expected = self.helper.build_launchd_user_plist(
            label=plan.label,
            user=plan.account.name,
            home=plan.account.home,
            helper=plan.helper,
            candidate_sha=FROZEN_CANDIDATE_SHA,
            environment=context,
            ownership_marker=state["ownership_marker"],
        )["EnvironmentVariables"]
        with mock.patch.dict(os.environ, context, clear=True):
            self.assertEqual(
                self.helper._validate_launchd_job_binding(loaded, plan, state),
                {"ownership_marker": state["ownership_marker"]},
            )
            pre_kickstart = loaded.replace(
                "\truns = 1\n\tlast exit code = 0\n",
                "\truns = 0\n\tlast exit code = (never exited)\n",
            )
            validated_pre_kickstart = self.helper._validated_launchd_job_snapshot(
                pre_kickstart,
                plan,
                state,
            )
            self.assertEqual(validated_pre_kickstart.sanitized, pre_kickstart)
            running_after_kickstart = (
                pre_kickstart.replace("active count = 0", "active count = 1", 1)
                .replace("state = not running", "state = running", 1)
                .replace("\truns = 0\n", "\tpid = 1234\n\truns = 1\n", 1)
            )
            validated_running = self.helper._validated_launchd_job_snapshot(
                running_after_kickstart,
                plan,
                state,
            )
            self.assertIn("\tstate = running\n", validated_running.sanitized)
            self.assertNotIn("\tpid = 1234\n", validated_running.sanitized)
            with self.assertRaises(self.helper.ProbeError) as raised:
                self.helper.parse_launchctl_terminal(
                    pre_kickstart,
                    expected_status=0,
                )
            self.assertEqual(raised.exception.code, "launchctl-terminal-invalid")
            for invalid_exit in ("(abandoned)", "(failed reap)", "never exited"):
                invalid_pre_kickstart = pre_kickstart.replace(
                    "(never exited)",
                    invalid_exit,
                )
                with self.subTest(last_exit_code=invalid_exit):
                    with self.assertRaises(self.helper.ProbeError) as raised:
                        self.helper._validated_launchd_job_snapshot(
                            invalid_pre_kickstart,
                            plan,
                            state,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "launchd-job-binding-invalid",
                    )
            secret = "default-environment-secret-canary"
            normal = self.launchctl_job_with_unrelated_blocks(
                plan,
                state,
                secret=secret,
            )
            validated = self.helper._validated_launchd_job_snapshot(
                normal,
                plan,
                state,
            )
            self.assertEqual(
                validated.binding,
                {"ownership_marker": state["ownership_marker"]},
            )
            self.assertEqual(validated.sanitized, loaded)
            self.assertNotIn(secret, validated.sanitized)

            marker_line = (
                "\t\tTASK_WITNESS_LAUNCHD_OWNERSHIP_MARKER => "
                f"{state['ownership_marker']}\n"
            )
            misplaced_marker = normal.replace(marker_line, "", 1).replace(
                "\t\tPATH => /usr/bin:/bin\n",
                "\t\tPATH => /usr/bin:/bin\n" + marker_line,
                1,
            )
            with self.assertRaisesRegex(
                self.helper.ProbeError,
                "launchd-job-binding-invalid",
            ):
                self.helper._validate_launchd_job_binding(
                    misplaced_marker,
                    plan,
                    state,
                )

            for name, value in expected.items():
                line = f"\t\t{name} => {value}\n"
                mutations = {
                    "missing": loaded.replace(line, "", 1),
                    "altered": loaded.replace(
                        line,
                        f"\t\t{name} => altered\n",
                        1,
                    ),
                    "duplicate": loaded.replace(line, line * 2, 1),
                    "outside-duplicate": loaded.replace(
                        "\tactive count = 0\n",
                        "\tactive count = 0\n" + line,
                        1,
                    ),
                }
                for mutation_name, mutation in mutations.items():
                    with (
                        self.subTest(name=name, mutation=mutation_name),
                        self.assertRaisesRegex(
                            self.helper.ProbeError,
                            "launchd-job-binding-invalid",
                        ),
                    ):
                        self.helper._validate_launchd_job_binding(
                            mutation,
                            plan,
                            state,
                        )

            environment_close = f"\t\tXPC_SERVICE_NAME => {plan.label}\n\t}}\n"
            for label_name, mutation in {
                "extra": loaded.replace(
                    environment_close,
                    "\t\tUNEXPECTED_SECRET => must-not-cross\n" + environment_close,
                    1,
                ),
                "wrong-xpc": loaded.replace(
                    f"XPC_SERVICE_NAME => {plan.label}",
                    "XPC_SERVICE_NAME => foreign",
                    1,
                ),
                "duplicate-xpc": loaded.replace(
                    f"\t\tXPC_SERVICE_NAME => {plan.label}\n",
                    f"\t\tXPC_SERVICE_NAME => {plan.label}\n" * 2,
                    1,
                ),
                "outside-xpc": loaded.replace(
                    f"\t\tXPC_SERVICE_NAME => {plan.label}\n",
                    "",
                    1,
                ).replace(
                    "\tactive count = 0\n",
                    f"\tactive count = 0\n\t\tXPC_SERVICE_NAME => {plan.label}\n",
                    1,
                ),
            }.items():
                with (
                    self.subTest(mutation=label_name),
                    self.assertRaisesRegex(
                        self.helper.ProbeError,
                        "launchd-job-binding-invalid",
                    ),
                ):
                    self.helper._validate_launchd_job_binding(mutation, plan, state)

    def test_disposable_uid_selection_and_account_readback_are_closed(self) -> None:
        realistic_list = (
            "_networkd 24\n"
            "nobody -2\n"
            "root 0\n"
            "runner 501\n"
            "occupied-a 502\n"
            "occupied-b 504\n"
        )
        with mock.patch.object(
            self.helper,
            "_require_command_success",
            return_value=realistic_list,
        ) as dscl:
            listed = self.helper._list_accounts()
        dscl.assert_called_once_with(
            ["/usr/bin/dscl", ".", "-list", "/Users", "UniqueID"],
            command_id="account-uid-list",
        )
        self.assertEqual(
            listed,
            {
                "_networkd": 24,
                "nobody": -2,
                "root": 0,
                "runner": 501,
                "occupied-a": 502,
                "occupied-b": 504,
            },
        )
        self.assertEqual(
            self.helper.choose_disposable_uid(set(listed.values())),
            503,
        )
        self.assertEqual(
            self.helper.choose_disposable_uid({-(1 << 31), -2, 0, (1 << 31) - 1}),
            502,
        )
        with self.assertRaisesRegex(self.helper.ProbeError, "uid-range-exhausted"):
            self.helper.choose_disposable_uid({-2, *range(502, 600)})

        for occupied in (
            {True},
            {"502"},
            {502.0},
            {-(1 << 31) - 1},
            {1 << 31},
        ):
            with (
                self.subTest(occupied=occupied),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "invalid-occupied-uids",
                ),
            ):
                self.helper.choose_disposable_uid(occupied)

    def test_process_table_drives_uid_selection_and_value_free_diagnostics(
        self,
    ) -> None:
        raw = (
            "0 1 0 1 Ss launchd\n"
            "0 200 1 200 Ss mds\n"
            "0 201 1 201 Ss rootbroker\n"
            "-2 0 0 0 I kernel_task\n"
            "4294967294 2 1 2 S nobody\n"
            "700 210 1 210 S broker-private-name\n"
            "502 120 1 120 S cfprefsd\n"
            "503 130 1 130 S distnoted\n"
            "504 140 1 140 Z launchd\n"
            "505 150 1 150 S unrelated-private-name\n"
            "506 160 1 160 S first\n"
            "506 161 1 161 S second\n"
            "507 170 200 170 S mdworker_shared\n"
            "508 180 201 180 S root-child-private-name\n"
            "509 190 210 190 S other-child-private-name\n"
            "510 195 999 195 S orphan-private-name"
            "\n512 220 1 220 S launchd"
            "\n512 221 220 220 S cfprefsd"
            "\n513 230 200 230 S mdworker_shared"
            "\n513 231 200 231 S mdworker_sizing"
            "\n514 240 1 240 S launchd"
            "\n514 241 240 240 S distnoted"
            "\n514 242 200 242 S mdworker"
            "\n515 250 1 250 S Python"
            "\n515 251 1 251 S private-companion-name"
        )
        records = self.helper.parse_process_list(raw)
        self.assertEqual(
            self.helper.process_occupied_uids(records),
            {
                -2,
                0,
                502,
                503,
                504,
                505,
                506,
                507,
                508,
                509,
                510,
                512,
                513,
                514,
                515,
                700,
                4294967294,
            },
        )
        expected_codes = {
            502: "disposable-user-cfprefsd-name-remains",
            503: "disposable-user-distnoted-name-remains",
            504: "disposable-user-zombie-only-remains",
            505: "disposable-user-pid1-parented-process-remains",
            506: "disposable-user-pid1-parented-processes-remain",
            507: "disposable-user-spotlight-worker-remains",
            508: "disposable-user-root-parented-process-remains",
            509: "disposable-user-other-uid-parented-process-remains",
            510: "disposable-user-parent-unobserved-process-remains",
            512: "disposable-user-background-agent-names-remain",
            513: "disposable-user-spotlight-workers-remain",
            514: "disposable-user-background-and-spotlight-names-remain",
            515: "disposable-user-probe-and-other-processes-remain",
        }
        for uid, expected_code in expected_codes.items():
            with self.subTest(uid=uid):
                self.assertEqual(
                    self.helper._process_survivor_code(records, uid),
                    expected_code,
                )
        self.assertIsNone(self.helper._process_survivor_code(records, 511))
        self.assertNotIn("unrelated-private-name", str(expected_codes))
        self.assertNotIn("root-child-private-name", str(expected_codes))
        self.assertNotIn("other-child-private-name", str(expected_codes))
        self.assertNotIn("orphan-private-name", str(expected_codes))
        self.assertNotIn("private-companion-name", str(expected_codes))

        with mock.patch.object(
            self.helper,
            "_require_command_success",
            return_value=raw,
        ) as process_command:
            self.assertEqual(self.helper._process_records(timeout=1.25), records)
        process_command.assert_called_once_with(
            [
                "/bin/ps",
                "-axo",
                "uid=,pid=,ppid=,pgid=,state=,ucomm=",
            ],
            command_id="process-list",
            maximum=self.helper.MAX_PROCESS_LIST_BYTES,
            timeout=1.25,
        )

        launchd_named = self.helper.parse_process_list(
            "0 1 0 1 Ss launchd\n502 1234 1 1234 S Python\n"
        )
        self.assertEqual(
            self.helper._process_survivor_code(launchd_named, 502),
            "disposable-user-probe-name-remains",
        )

        pid_one = self.helper.ProcessRecord(0, 1, 0, 1, "Ss", "launchd")
        root_parent = self.helper.ProcessRecord(0, 200, 1, 200, "Ss", "private-root")
        other_uid_parent = self.helper.ProcessRecord(
            700, 210, 1, 210, "S", "private-broker"
        )
        spotlight = self.helper.ProcessRecord(502, 290, 1, 290, "S", "mdworker_shared")
        topology_cases = {
            "pid-one": (
                (
                    pid_one,
                    self.helper.ProcessRecord(502, 300, 1, 300, "S", "private-a"),
                    self.helper.ProcessRecord(502, 301, 1, 301, "S", "private-b"),
                ),
                "disposable-user-pid1-parented-processes-remain",
            ),
            "pid-one-and-same-uid-tree": (
                (
                    pid_one,
                    self.helper.ProcessRecord(502, 300, 1, 300, "S", "private-a"),
                    self.helper.ProcessRecord(502, 301, 300, 301, "S", "private-child"),
                ),
                "disposable-user-same-uid-process-tree-remains",
            ),
            "root-parent": (
                (
                    pid_one,
                    root_parent,
                    self.helper.ProcessRecord(502, 300, 200, 300, "S", "private-a"),
                    self.helper.ProcessRecord(502, 301, 200, 301, "S", "private-b"),
                ),
                "disposable-user-root-parented-processes-remain",
            ),
            "other-uid-parent": (
                (
                    pid_one,
                    other_uid_parent,
                    self.helper.ProcessRecord(502, 300, 210, 300, "S", "private-a"),
                    self.helper.ProcessRecord(502, 301, 210, 301, "S", "private-b"),
                ),
                "disposable-user-other-uid-parented-processes-remain",
            ),
            "parent-unobserved": (
                (
                    pid_one,
                    self.helper.ProcessRecord(502, 300, 900, 300, "S", "private-a"),
                    self.helper.ProcessRecord(502, 301, 901, 301, "S", "private-b"),
                ),
                "disposable-user-parent-unobserved-processes-remain",
            ),
            "same-uid-tree": (
                (
                    pid_one,
                    self.helper.ProcessRecord(502, 300, 1, 300, "S", "launchd"),
                    self.helper.ProcessRecord(502, 301, 300, 301, "S", "private-a"),
                ),
                "disposable-user-same-uid-process-tree-remains",
            ),
            "mixed-parent-topologies": (
                (
                    pid_one,
                    root_parent,
                    self.helper.ProcessRecord(502, 300, 1, 300, "S", "private-a"),
                    self.helper.ProcessRecord(502, 301, 200, 301, "S", "private-b"),
                ),
                "disposable-user-mixed-parent-topologies-remain",
            ),
            "background-and-pid-one": (
                (
                    pid_one,
                    self.helper.ProcessRecord(502, 300, 1, 300, "S", "launchd"),
                    self.helper.ProcessRecord(502, 301, 1, 301, "S", "private-a"),
                ),
                "disposable-user-pid1-parented-processes-remain",
            ),
            "spotlight-and-pid-one": (
                (
                    pid_one,
                    spotlight,
                    self.helper.ProcessRecord(502, 300, 1, 300, "S", "private-a"),
                ),
                "disposable-user-pid1-parented-processes-remain",
            ),
            "spotlight-and-root-parent": (
                (
                    pid_one,
                    root_parent,
                    spotlight,
                    self.helper.ProcessRecord(502, 300, 200, 300, "S", "private-a"),
                ),
                "disposable-user-root-parented-processes-remain",
            ),
            "spotlight-and-other-uid-parent": (
                (
                    pid_one,
                    other_uid_parent,
                    spotlight,
                    self.helper.ProcessRecord(502, 300, 210, 300, "S", "private-a"),
                ),
                "disposable-user-other-uid-parented-processes-remain",
            ),
            "spotlight-and-parent-unobserved": (
                (
                    pid_one,
                    spotlight,
                    self.helper.ProcessRecord(502, 300, 900, 300, "S", "private-a"),
                ),
                "disposable-user-parent-unobserved-processes-remain",
            ),
            "spotlight-and-same-uid-tree": (
                (
                    pid_one,
                    spotlight,
                    self.helper.ProcessRecord(502, 300, 290, 300, "S", "private-a"),
                ),
                "disposable-user-same-uid-process-tree-remains",
            ),
        }
        for label, (topology, expected_code) in topology_cases.items():
            for order in (topology, tuple(reversed(topology))):
                with self.subTest(label=label, reversed=order is not topology):
                    code = self.helper._process_survivor_code(order, 502)
                    self.assertEqual(code, expected_code)
                    self.assertNotIn("private", code)
                    self.assertNotRegex(code, r"\b(?:200|210|300|301|900|901)\b")

    def test_process_table_parser_and_active_code_contract_fail_closed(
        self,
    ) -> None:
        invalid = {
            "missing-pid-one": "502 120 1 120 S cfprefsd\n",
            "duplicate-pid": "0 1 0 1 Ss launchd\n502 1 1 1 S cfprefsd\n",
            "nonnumeric": "0 1 0 1 Ss launchd\n502 pid 1 1 S cfprefsd\n",
            "empty-command": "0 1 0 1 Ss \n",
            "self-parent": "0 1 0 1 Ss launchd\n502 120 120 120 S private\n",
        }
        for label, value in invalid.items():
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(self.helper.ProbeError, "process-list-invalid"),
            ):
                self.helper.parse_process_list(value)
        with self.assertRaisesRegex(
            self.helper.ProbeError,
            "process-list-too-large",
        ):
            self.helper.parse_process_list(
                "0 1 0 1 Ss launchd\n" + " " * self.helper.MAX_PROCESS_LIST_BYTES
            )

        target = self.helper.ProcessRecord(
            uid=502,
            pid=120,
            ppid=1,
            pgid=120,
            state="S",
            command="cfprefsd",
        )
        with (
            mock.patch.object(
                self.helper,
                "_process_records",
                return_value=(target,),
            ),
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._require_disposable_uid_available(
                502,
                active_code="disposable-uid-active-before-bootstrap",
            )
        self.assertEqual(
            raised.exception.code,
            "disposable-uid-active-before-bootstrap",
        )

        with (
            mock.patch.object(self.helper, "_process_records") as process_scan,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._require_disposable_uid_available(
                502,
                active_code="secret-dynamic-code",
            )
        self.assertEqual(
            raised.exception.code,
            "invalid-disposable-uid-active-code",
        )
        process_scan.assert_not_called()

    def test_lifecycle_initialization_excludes_process_occupied_uids(self) -> None:
        stage = Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        process_record = self.helper.ProcessRecord(
            uid=502,
            pid=120,
            ppid=1,
            pgid=120,
            state="S",
            command="cfprefsd",
        )
        with (
            mock.patch.object(
                self.helper,
                "validate_prestaged_helper",
                return_value=b"trusted helper",
            ),
            mock.patch.object(
                self.helper,
                "_list_accounts",
                return_value={"root": 0, "runner": 501, "occupied": 503},
            ),
            mock.patch.object(
                self.helper,
                "_process_records",
                return_value=(process_record,),
            ) as process_scan,
            mock.patch.object(self.helper.time, "monotonic", return_value=100.0),
            mock.patch.object(self.helper, "_write_root_file"),
        ):
            plan = self.helper._initialize_lifecycle(
                stage_root=stage,
                expected_helper_sha256="1" * 64,
                runner_uid=501,
                runner_gid=20,
                environment=eligible_context(),
            )
        self.assertEqual(plan.account.uid, 504)
        process_scan.assert_called_once_with()

    def test_account_creation_rechecks_process_occupancy_before_ds_mutation(
        self,
    ) -> None:
        plan = self.lifecycle_plan(
            Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        )
        state = self.lifecycle_state(plan)
        with (
            mock.patch.object(
                self.helper,
                "_list_accounts",
                return_value={"root": 0, "runner": 501},
            ) as list_accounts,
            mock.patch.object(
                self.helper,
                "_account_exists",
                return_value=False,
            ) as account_exists,
            mock.patch.object(
                self.helper,
                "_require_disposable_uid_available",
                side_effect=self.helper.ProbeError(
                    "disposable-uid-active-before-create"
                ),
            ) as process_check,
            mock.patch.object(self.helper, "_require_command_success") as mutate,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._create_disposable_account(plan, state)
        self.assertEqual(
            raised.exception.code,
            "disposable-uid-active-before-create",
        )
        process_check.assert_called_once_with(plan.account.uid)
        list_accounts.assert_called_once_with()
        account_exists.assert_called_once_with(plan.account.name)
        mutate.assert_not_called()

    def test_precreate_uid_collision_is_visible_without_process_metadata(
        self,
    ) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        code = "disposable-uid-active-before-create"
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, eligible_context(), clear=True),
            mock.patch.object(self.helper, "_normalized_context"),
            mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
            mock.patch.object(self.helper, "_create_root_directory"),
            mock.patch.object(
                self.helper,
                "_load_lifecycle_state",
                return_value=(plan, state),
            ),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=self.helper.ValidatedStageBindings(None, None),
            ),
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_create_disposable_account",
                side_effect=self.helper.ProbeError(code),
            ),
            mock.patch.object(self.helper, "_create_disposable_home") as create_home,
            mock.patch.object(self.helper, "_write_lifecycle_artifact") as write,
            redirect_stderr(stderr),
        ):
            self.assertEqual(
                self.helper.run_launchd_user_lifecycle(
                    stage_root=plan.stage_root,
                    artifact_root=Path("/private/tmp/artifact"),
                    candidate_sha=FROZEN_CANDIDATE_SHA,
                    runner_uid=501,
                    runner_gid=20,
                ),
                2,
            )
        create_home.assert_not_called()
        self.assertEqual(write.call_args.kwargs["error_code"], code)
        self.assertEqual(
            stderr.getvalue(),
            f"task-witness macOS launchd-user lifecycle: {code}\n",
        )
        self.assertNotIn("pid", stderr.getvalue())
        self.assertNotIn("cfprefsd", stderr.getvalue())

    def test_process_appearance_before_bootstrap_stops_launchd_mutation(
        self,
    ) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        code = "disposable-uid-active-before-bootstrap"
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, eligible_context(), clear=True),
            mock.patch.object(self.helper, "_normalized_context"),
            mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
            mock.patch.object(self.helper, "_create_root_directory"),
            mock.patch.object(
                self.helper,
                "_load_lifecycle_state",
                return_value=(plan, state),
            ),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=self.helper.ValidatedStageBindings(None, None),
            ),
            mock.patch.object(self.helper, "_require_launchd_absent") as job_absent,
            mock.patch.object(self.helper, "_create_disposable_account"),
            mock.patch.object(self.helper, "_create_disposable_home"),
            mock.patch.object(self.helper, "_validate_disposable_home_root"),
            mock.patch.object(
                self.helper,
                "_require_disposable_uid_available",
                side_effect=self.helper.ProbeError(code),
            ) as process_check,
            mock.patch.object(self.helper, "_require_command_success") as mutate,
            mock.patch.object(self.helper, "_write_lifecycle_artifact") as write,
            redirect_stderr(stderr),
        ):
            self.assertEqual(
                self.helper.run_launchd_user_lifecycle(
                    stage_root=plan.stage_root,
                    artifact_root=Path("/private/tmp/artifact"),
                    candidate_sha=FROZEN_CANDIDATE_SHA,
                    runner_uid=501,
                    runner_gid=20,
                ),
                2,
            )
        job_absent.assert_called_once_with(plan.label)
        process_check.assert_called_once_with(
            plan.account.uid,
            active_code=code,
        )
        mutate.assert_not_called()
        self.assertEqual(write.call_args.kwargs["error_code"], code)
        self.assertEqual(
            stderr.getvalue(),
            f"task-witness macOS launchd-user lifecycle: {code}\n",
        )

    def test_dscl_uid_parser_and_account_record_validation_are_closed(self) -> None:
        self.assertEqual(
            self.helper.parse_dscl_uid_list(
                f"minimum {-(1 << 31)}\nmaximum {(1 << 31) - 1}\n"
            ),
            {"minimum": -(1 << 31), "maximum": (1 << 31) - 1},
        )
        invalid_lists = {
            "plus": "user +502\n",
            "leading-zero": "user 0502\n",
            "negative-zero": "user -0\n",
            "negative-leading-zero": "user -02\n",
            "below-platform": f"user {-(1 << 31) - 1}\n",
            "above-platform": f"user {1 << 31}\n",
            "duplicate-name": "user 501\nuser 502\n",
            "duplicate-uid": "first 501\nsecond 501\n",
        }
        for label, raw in invalid_lists.items():
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "account-list-invalid",
                ),
            ):
                self.helper.parse_dscl_uid_list(raw)

        expected = self.helper.DisposableAccount(
            name="twq-0123456789ab",
            uid=502,
            gid=20,
            home=Path("/Users/twq-0123456789ab"),
        )
        system_generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        record = {
            "AuthenticationAuthority": [";DisabledUser;"],
            "GeneratedUID": [system_generated_uid],
            "IsHidden": ["1"],
            "NFSHomeDirectory": [str(expected.home)],
            "Password": ["********"],
            "PrimaryGroupID": [str(expected.gid)],
            "UniqueID": [str(expected.uid)],
            "UserShell": ["/usr/bin/false"],
        }
        self.helper.require_exact_account_record(record, expected)
        for password_marker in ("*", "********"):
            changed = dict(record)
            changed["Password"] = [password_marker]
            self.helper.require_exact_account_record(changed, expected)
        for invalid_password in ([], ["**"], ["*", "********"], ["hash"]):
            changed = dict(record)
            changed["Password"] = invalid_password
            with self.subTest(password=invalid_password):
                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper.require_exact_account_record(changed, expected)
                self.assertEqual(
                    raised.exception.code,
                    "account-record-password-drift",
                )
                self.assertIsNone(raised.exception.secondary_code)
        changed = dict(record)
        changed["GeneratedUID"] = ["AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"]
        self.helper.require_exact_account_record(changed, expected)
        for invalid_generated_uid in (
            [system_generated_uid.lower()],
            ["00000000-0000-0000-0000-000000000000"],
            ["not-a-guid"],
            [system_generated_uid, "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"],
        ):
            changed = dict(record)
            changed["GeneratedUID"] = invalid_generated_uid
            with self.subTest(generated_uid=invalid_generated_uid):
                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper.require_exact_account_record(changed, expected)
                self.assertEqual(
                    raised.exception.code,
                    "account-record-generated-uid-drift",
                )
                self.assertIsNone(raised.exception.secondary_code)
        field_drifts = {
            "AuthenticationAuthority": (
                [";ShadowHash;"],
                "account-record-authentication-authority-drift",
            ),
            "IsHidden": (["0"], "account-record-hidden-drift"),
            "NFSHomeDirectory": (["/Users/other"], "account-record-home-drift"),
            "Password": (["**"], "account-record-password-drift"),
            "PrimaryGroupID": (["0"], "account-record-gid-drift"),
            "UniqueID": (["-2"], "account-record-uid-drift"),
            "UserShell": (["/bin/zsh"], "account-record-shell-drift"),
        }
        for field, (value, code) in field_drifts.items():
            changed = dict(record)
            changed[field] = value
            with self.subTest(field=field):
                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper.require_exact_account_record(changed, expected)
                self.assertEqual(raised.exception.code, code)
                self.assertIsNone(raised.exception.secondary_code)
        missing_codes = {
            "AuthenticationAuthority": "account-record-authentication-authority-missing",
            "GeneratedUID": "account-record-generated-uid-missing",
            "IsHidden": "account-record-hidden-missing",
            "NFSHomeDirectory": "account-record-home-missing",
            "Password": "account-record-password-missing",
            "PrimaryGroupID": "account-record-gid-missing",
            "UniqueID": "account-record-uid-missing",
            "UserShell": "account-record-shell-missing",
        }
        for field, code in missing_codes.items():
            changed = {name: value for name, value in record.items() if name != field}
            with self.subTest(missing=field):
                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper.require_exact_account_record(changed, expected)
                self.assertEqual(raised.exception.code, code)
                self.assertIsNone(raised.exception.secondary_code)

        changed = {**record, "RecordName": [expected.name]}
        with self.assertRaises(self.helper.ProbeError) as raised:
            self.helper.require_exact_account_record(changed, expected)
        self.assertEqual(raised.exception.code, "account-record-fields-unexpected")
        self.assertIsNone(raised.exception.secondary_code)

        changed.pop("Password")
        with self.assertRaises(self.helper.ProbeError) as raised:
            self.helper.require_exact_account_record(changed, expected)
        self.assertEqual(raised.exception.code, "account-record-fields-unexpected")
        self.assertIsNone(raised.exception.secondary_code)

        changed = dict(record)
        changed["GeneratedUID"] = [system_generated_uid.lower()]
        changed["Password"] = ["*"]
        with self.assertRaises(self.helper.ProbeError) as raised:
            self.helper.require_exact_account_record(changed, expected)
        self.assertEqual(
            raised.exception.code,
            "account-record-generated-uid-drift",
        )

    def test_generated_uid_read_uses_the_fixed_field_diagnostic(self) -> None:
        for label, raw in (
            ("wrong-field", "Password: ********"),
            ("malformed-guid", "GeneratedUID: not-a-guid"),
        ):
            with (
                self.subTest(label=label),
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                    return_value=raw,
                ),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._read_system_generated_uid("twq-0123456789ab")
            self.assertEqual(
                raised.exception.code,
                "account-record-generated-uid-drift",
            )
            self.assertIsNone(raised.exception.secondary_code)

    def test_dscl_record_parser_normalizes_qualified_attribute_names(self) -> None:
        self.assertEqual(
            self.helper.parse_dscl_record(
                "AuthenticationAuthority: ;DisabledUser;\n"
                "dsAttrTypeNative:IsHidden: 1\n"
            ),
            {
                "AuthenticationAuthority": [";DisabledUser;"],
                "IsHidden": ["1"],
            },
        )
        for label, raw in (
            (
                "duplicate-normalized-name",
                "IsHidden: 1\ndsAttrTypeNative:IsHidden: 1\n",
            ),
            ("empty-qualified-name", "dsAttrTypeNative:: 1\n"),
        ):
            with (
                self.subTest(label=label),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper.parse_dscl_record(raw)
            self.assertEqual(raised.exception.code, "account-record-invalid")

    def test_launchctl_terminal_parser_rejects_timeout_respawn_and_pid_drift(
        self,
    ) -> None:
        terminal = (
            "system/io.example = {\n"
            "\tactive count = 0\n"
            "\tstate = not running\n"
            "\truns = 1\n"
            "\tlast exit code = 0\n"
            "}\n"
        )
        self.assertEqual(
            self.helper.parse_launchctl_terminal(terminal, expected_status=0),
            {"last_exit_code": 0, "runs": 1, "state": "not running"},
        )
        for changed in (
            terminal.replace("runs = 1", "runs = 2"),
            terminal.replace("last exit code = 0", "last exit code = 1"),
            terminal.replace("state = not running", "state = running"),
            terminal.replace("}\n", "\tpid = 1234\n}\n"),
        ):
            with (
                self.subTest(changed=changed),
                self.assertRaises(self.helper.ProbeError),
            ):
                self.helper.parse_launchctl_terminal(changed, expected_status=0)

    def test_launchctl_terminal_parser_scopes_status_to_one_tab_fields(
        self,
    ) -> None:
        terminal = (
            "system/io.example = {\n"
            "\tactive count = 0\n"
            "\tstate = not running\n"
            "\tresource coalition = {\n"
            "\t\tstate = active\n"
            "\t\tactive count = 1\n"
            "\t}\n"
            "\tjetsam coalition = {\n"
            "\t\tstate = inactive\n"
            "\t\tactive count = 0\n"
            "\t}\n"
            "\truns = 1\n"
            "\tlast exit code = 0\n"
            "}\n"
        )
        self.assertEqual(
            self.helper.parse_launchctl_terminal(terminal, expected_status=0),
            {"last_exit_code": 0, "runs": 1, "state": "not running"},
        )

        invalid = {
            "missing-top-level": terminal.replace("\truns = 1\n", "", 1),
            "duplicate-top-level": terminal.replace(
                "\tstate = not running\n",
                "\tstate = not running\n\tstate = not running\n",
                1,
            ),
            "nested-only": terminal.replace("\tactive count = 0\n", "", 1).replace(
                "\tstate = not running\n",
                "",
                1,
            ),
        }
        for label, changed in invalid.items():
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchctl-terminal-invalid",
                ),
            ):
                self.helper.parse_launchctl_terminal(changed, expected_status=0)

    def test_launchd_artifact_is_fixed_bounded_and_symlink_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = {
                "cleanup.json": b"{}",
                "launchd.loaded": b"loaded",
                "launchd.terminal": b"terminal",
                "lifecycle.json": b"{}",
                "lifecycle.status": b"0\n",
                "probe.json": b"{}",
                "probe.status": b"0\n",
                "probe.stderr": b"",
                "probe.stdout": b"launchd-user-eligible\n",
            }
            for name, raw in payloads.items():
                (root / name).write_bytes(raw)
            self.assertEqual(
                self.helper.launchd_artifact_payloads(root, include_manifest=False),
                payloads,
            )
            (root / "extra").write_bytes(b"extra")
            with self.assertRaisesRegex(
                self.helper.ProbeError,
                "launchd-artifact-file-set-disagrees",
            ):
                self.helper.launchd_artifact_payloads(root, include_manifest=False)
            (root / "extra").unlink()
            (root / "probe.stdout").unlink()
            (root / "probe.stdout").symlink_to("probe.stderr")
            with self.assertRaisesRegex(self.helper.ProbeError, "unsafe-probe-stdout"):
                self.helper.launchd_artifact_payloads(root, include_manifest=False)

    def test_sealed_launchd_success_rebuilds_each_child_and_cleanup_claim(self) -> None:
        context = self.launchd_context()
        observations = self.launchd_observations()
        probe = self.helper.build_launchd_user_probe_document(
            FROZEN_CANDIDATE_SHA,
            context,
            observations,
        )
        label = observations["process"]["label"]
        account = observations["credentials"]["passwd"]["name"]
        stage = Path(
            "/private/var/tmp/task-witness-macos-launchd-"
            f"{probe['harness']['run_id']}-{probe['harness']['run_attempt']}"
        )
        plan = self.lifecycle_plan(stage)
        state = self.lifecycle_state(plan)
        loaded = self.launchctl_job(plan, state)
        binding = {"ownership_marker": state["ownership_marker"]}
        domain_reset = self.helper._domain_reset_evidence(None)
        home_cleanup = {
            "authorization_sha256": "5" * 64,
            "disposition": "performed",
        }
        lifecycle = self.helper._document_with_digest(
            {
                "schema_version": 2,
                "contract": "task-witness-macos-launchd-lifecycle-v2",
                "candidate_sha1": FROZEN_CANDIDATE_SHA,
                "label": label,
                "kickstart_pid": observations["process"]["pid"],
                "probe_disposition": "launchd-user-eligible",
                "disposition": "launchd-user-eligible",
                "binding": binding,
                "domain_reset": domain_reset,
            }
        )
        cleanup = self.helper._document_with_digest(
            {
                "schema_version": 3,
                "contract": "task-witness-macos-launchd-cleanup-v3",
                "candidate_sha1": FROZEN_CANDIDATE_SHA,
                "account": account,
                "label": label,
                "disposition": "cleaned",
                "domain_reset": domain_reset,
                "home_cleanup": home_cleanup,
            }
        )
        terminal = loaded.encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = {
                "cleanup.json": self.helper.canonical_bytes(cleanup),
                "launchd.loaded": loaded.encode("utf-8"),
                "launchd.terminal": terminal,
                "lifecycle.json": self.helper.canonical_bytes(lifecycle),
                "lifecycle.status": b"0\n",
                "probe.json": self.helper.canonical_bytes(probe),
                "probe.status": b"0\n",
                "probe.stderr": b"",
                "probe.stdout": b"launchd-user-eligible\n",
            }
            for name, raw in payloads.items():
                (root / name).write_bytes(raw)
            external = root.parent / f"{root.name}.SHA256SUMS"
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with mock.patch.dict(os.environ, context, clear=True):
                self.helper.verify_launchd_success(root)

            def reseal() -> None:
                manifest = root / "SHA256SUMS"
                if manifest.exists():
                    manifest.unlink()
                self.helper.write_launchd_artifact_manifest(root, external)
                external.replace(manifest)

            malformed_evidence = (
                (
                    "domain-reset-list",
                    "lifecycle.json",
                    lifecycle,
                    "domain_reset",
                    [],
                    "invalid-user-domain-reset-evidence",
                ),
                (
                    "domain-reset-mapping",
                    "lifecycle.json",
                    lifecycle,
                    "domain_reset",
                    {},
                    "invalid-user-domain-reset-evidence",
                ),
                (
                    "home-cleanup-list",
                    "cleanup.json",
                    cleanup,
                    "home_cleanup",
                    [],
                    "invalid-home-cleanup-evidence",
                ),
                (
                    "home-cleanup-mapping",
                    "cleanup.json",
                    cleanup,
                    "home_cleanup",
                    {},
                    "invalid-home-cleanup-evidence",
                ),
            )
            for (
                label,
                file_name,
                original,
                evidence_name,
                disposition,
                expected_code,
            ) in malformed_evidence:
                with self.subTest(label=label):
                    unsigned = {
                        key: value
                        for key, value in original.items()
                        if key != "content_sha256"
                    }
                    unsigned[evidence_name] = {
                        **unsigned[evidence_name],
                        "disposition": disposition,
                    }
                    (root / file_name).write_bytes(
                        self.helper.canonical_bytes(
                            self.helper._document_with_digest(unsigned)
                        )
                    )
                    reseal()
                    with (
                        mock.patch.dict(os.environ, context, clear=True),
                        self.assertRaises(self.helper.ProbeError) as malformed,
                    ):
                        self.helper.verify_launchd_success(root)
                    self.assertEqual(malformed.exception.code, expected_code)
                    (root / file_name).write_bytes(
                        self.helper.canonical_bytes(original)
                    )
                    reseal()

            for label, invalid_digest in (
                ("short", "4" * 63),
                ("uppercase", "A" * 64),
                ("private-suffix", "4" * 64 + "private-canary"),
            ):
                for evidence, disposition, expected_code in (
                    (
                        "domain-reset",
                        "performed",
                        "invalid-user-domain-reset-evidence",
                    ),
                    (
                        "domain-reset",
                        "recovered-to-stable-zero",
                        "invalid-user-domain-reset-evidence",
                    ),
                    (
                        "home-cleanup",
                        "performed",
                        "invalid-home-cleanup-evidence",
                    ),
                    (
                        "home-cleanup",
                        "recovered",
                        "invalid-home-cleanup-evidence",
                    ),
                ):
                    changed_lifecycle = {
                        key: value
                        for key, value in lifecycle.items()
                        if key != "content_sha256"
                    }
                    changed_cleanup = {
                        key: value
                        for key, value in cleanup.items()
                        if key != "content_sha256"
                    }
                    if evidence == "domain-reset":
                        invalid_evidence = {
                            "authorization_sha256": invalid_digest,
                            "capability": (
                                "github-hosted-ephemeral-user-domain-reset-v1"
                            ),
                            "disposition": disposition,
                            "precondition": (
                                "disposable-user-pid1-parented-processes-remain"
                            ),
                        }
                        changed_lifecycle["domain_reset"] = invalid_evidence
                        changed_cleanup["domain_reset"] = invalid_evidence
                    else:
                        changed_cleanup["home_cleanup"] = {
                            "authorization_sha256": invalid_digest,
                            "disposition": disposition,
                        }
                    (root / "lifecycle.json").write_bytes(
                        self.helper.canonical_bytes(
                            self.helper._document_with_digest(changed_lifecycle)
                        )
                    )
                    (root / "cleanup.json").write_bytes(
                        self.helper.canonical_bytes(
                            self.helper._document_with_digest(changed_cleanup)
                        )
                    )
                    reseal()
                    with (
                        self.subTest(
                            evidence=evidence,
                            disposition=disposition,
                            digest=label,
                        ),
                        mock.patch.dict(os.environ, context, clear=True),
                        self.assertRaises(self.helper.ProbeError) as invalid,
                    ):
                        self.helper.verify_launchd_success(root)
                    self.assertEqual(invalid.exception.code, expected_code)
                    self.assertNotIn("private-canary", str(invalid.exception))
                    (root / "lifecycle.json").write_bytes(
                        self.helper.canonical_bytes(lifecycle)
                    )
                    (root / "cleanup.json").write_bytes(
                        self.helper.canonical_bytes(cleanup)
                    )
                    reseal()

            performed = {
                "authorization_sha256": "4" * 64,
                "capability": "github-hosted-ephemeral-user-domain-reset-v1",
                "disposition": "performed",
                "precondition": ("disposable-user-pid1-parented-processes-remain"),
            }
            performed_lifecycle = {
                key: value
                for key, value in lifecycle.items()
                if key != "content_sha256"
            }
            performed_cleanup = {
                key: value for key, value in cleanup.items() if key != "content_sha256"
            }
            performed_lifecycle["domain_reset"] = performed
            performed_cleanup["domain_reset"] = performed
            (root / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(
                    self.helper._document_with_digest(performed_lifecycle)
                )
            )
            (root / "cleanup.json").write_bytes(
                self.helper.canonical_bytes(
                    self.helper._document_with_digest(performed_cleanup)
                )
            )
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with mock.patch.dict(os.environ, context, clear=True):
                self.helper.verify_launchd_success(root)
            recovered_home_cleanup = dict(performed_cleanup)
            recovered_home_cleanup["home_cleanup"] = {
                **home_cleanup,
                "disposition": "recovered",
            }
            (root / "cleanup.json").write_bytes(
                self.helper.canonical_bytes(
                    self.helper._document_with_digest(recovered_home_cleanup)
                )
            )
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaises(self.helper.ProbeError) as recovered_home,
            ):
                self.helper.verify_launchd_success(root)
            self.assertEqual(
                recovered_home.exception.code,
                "launchd-user-probe-ineligible",
            )
            recovered_evidence = {
                **performed,
                "disposition": "recovered-to-stable-zero",
            }
            recovered_lifecycle = dict(performed_lifecycle)
            recovered_lifecycle["domain_reset"] = recovered_evidence
            recovered_cleanup = dict(performed_cleanup)
            recovered_cleanup["domain_reset"] = recovered_evidence
            (root / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(
                    self.helper._document_with_digest(recovered_lifecycle)
                )
            )
            (root / "cleanup.json").write_bytes(
                self.helper.canonical_bytes(
                    self.helper._document_with_digest(recovered_cleanup)
                )
            )
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaises(self.helper.ProbeError) as recovered,
            ):
                self.helper.verify_launchd_success(root)
            self.assertEqual(
                recovered.exception.code,
                "launchd-user-probe-ineligible",
            )
            (root / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(lifecycle)
            )
            (root / "cleanup.json").write_bytes(self.helper.canonical_bytes(cleanup))
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")

            path_line = f"\tpath = {plan.plist}\n"
            program_line = "\tprogram = /usr/bin/env\n"
            user_line = f"\tusername = {plan.account.name}\n"
            domain_line = "\tdomain = system\n"
            arguments = self.helper._expected_launchd_program_arguments(plan)
            arguments_block = (
                "\targuments = {\n"
                + "".join(f"\t\t{argument}\n" for argument in arguments)
                + "\t}\n"
            )
            marker_line = (
                "\t\tTASK_WITNESS_LAUNCHD_OWNERSHIP_MARKER => "
                f"{state['ownership_marker']}\n"
            )
            expected_environment = self.helper.build_launchd_user_plist(
                label=plan.label,
                user=plan.account.name,
                home=plan.account.home,
                helper=plan.helper,
                candidate_sha=FROZEN_CANDIDATE_SHA,
                environment=context,
                ownership_marker=state["ownership_marker"],
            )["EnvironmentVariables"]
            mutations = {
                "headerless": "state = not running\nruns = 1\nlast exit code = 0\n",
                "missing-first-line": loaded.removeprefix(
                    f"system/{plan.label} = {{\n"
                ),
                "first-line": loaded.replace(
                    f"system/{plan.label} = {{\n",
                    "system/io.nisavid.foreign = {\n",
                    1,
                ),
                "premature-root-close": loaded.replace(
                    f"system/{plan.label} = {{\n",
                    f"system/{plan.label} = {{\n}}\n",
                    1,
                ),
                "last-line": loaded.removesuffix("}\n"),
                "missing-path": loaded.replace(path_line, "", 1),
                "altered-path": loaded.replace(
                    path_line,
                    "\tpath = /Library/LaunchDaemons/foreign.plist\n",
                    1,
                ),
                "duplicate-path": loaded.replace(path_line, path_line * 2, 1),
                "missing-program": loaded.replace(program_line, "", 1),
                "altered-program": loaded.replace(
                    program_line,
                    "\tprogram = /bin/false\n",
                    1,
                ),
                "duplicate-program": loaded.replace(
                    program_line,
                    program_line * 2,
                    1,
                ),
                "missing-user": loaded.replace(user_line, "", 1),
                "altered-user": loaded.replace(
                    user_line,
                    "\tusername = root\n",
                    1,
                ),
                "duplicate-user": loaded.replace(user_line, user_line * 2, 1),
                "missing-domain": loaded.replace(domain_line, "", 1),
                "altered-domain": loaded.replace(
                    domain_line,
                    "\tdomain = user\n",
                    1,
                ),
                "duplicate-domain": loaded.replace(
                    domain_line,
                    domain_line * 2,
                    1,
                ),
                "missing-arguments": loaded.replace(arguments_block, "", 1),
                "altered-arguments": loaded.replace("\t\t-I\n", "\t\t-E\n", 1),
                "reordered-arguments": loaded.replace(
                    "\t\t-I\n\t\t-B\n",
                    "\t\t-B\n\t\t-I\n",
                    1,
                ),
                "duplicate-arguments": loaded.replace(
                    arguments_block,
                    arguments_block * 2,
                    1,
                ),
                "missing-marker": loaded.replace(marker_line, "", 1),
                "altered-marker": loaded.replace(
                    marker_line,
                    marker_line.replace("3" * 32, "4" * 32),
                    1,
                ),
                "duplicate-marker": loaded.replace(
                    marker_line,
                    marker_line * 2,
                    1,
                ),
                "marker-outside-environment": loaded.replace(
                    "\tactive count = 0\n",
                    "\tactive count = 0\n" + marker_line,
                    1,
                ),
                "unsanitized-default-environment": (
                    self.launchctl_job_with_unrelated_blocks(
                        plan,
                        state,
                        secret="sealed-artifact-secret-canary",
                    )
                ),
            }
            for name, value in expected_environment.items():
                line = f"\t\t{name} => {value}\n"
                mutations.update(
                    {
                        f"missing-environment-{name}": loaded.replace(line, "", 1),
                        f"altered-environment-{name}": loaded.replace(
                            line,
                            f"\t\t{name} => altered\n",
                            1,
                        ),
                        f"duplicate-environment-{name}": loaded.replace(
                            line,
                            line * 2,
                            1,
                        ),
                        f"outside-environment-{name}": loaded.replace(
                            "\tactive count = 0\n",
                            "\tactive count = 0\n" + line,
                            1,
                        ),
                    }
                )
            xpc_line = f"\t\tXPC_SERVICE_NAME => {plan.label}\n"
            mutations.update(
                {
                    "extra-environment": loaded.replace(
                        xpc_line,
                        "\t\tUNEXPECTED_SECRET => must-not-cross\n" + xpc_line,
                        1,
                    ),
                    "altered-xpc": loaded.replace(
                        xpc_line,
                        "\t\tXPC_SERVICE_NAME => foreign\n",
                        1,
                    ),
                    "duplicate-xpc": loaded.replace(xpc_line, xpc_line * 2, 1),
                    "outside-xpc": loaded.replace(xpc_line, "", 1).replace(
                        "\tactive count = 0\n",
                        "\tactive count = 0\n" + xpc_line,
                        1,
                    ),
                }
            )
            for artifact_name in ("launchd.loaded", "launchd.terminal"):
                for label, mutation in mutations.items():
                    with self.subTest(artifact=artifact_name, label=label):
                        (root / artifact_name).write_text(mutation)
                        (root / "SHA256SUMS").unlink()
                        self.helper.write_launchd_artifact_manifest(root, external)
                        external.replace(root / "SHA256SUMS")
                        with (
                            mock.patch.dict(os.environ, context, clear=True),
                            self.assertRaisesRegex(
                                self.helper.ProbeError,
                                "launchd-job-binding-invalid",
                            ),
                        ):
                            self.helper.verify_launchd_success(root)
                (root / artifact_name).write_text(loaded)
                (root / "SHA256SUMS").unlink()
                self.helper.write_launchd_artifact_manifest(root, external)
                external.replace(root / "SHA256SUMS")

            nested_terminal = (
                loaded.replace("\tstate = not running\n", "", 1)
                .replace("\truns = 1\n", "", 1)
                .replace("\tlast exit code = 0\n", "", 1)
                .removesuffix("}\n")
                + "\tforeign = {\n"
                "\t\tstate = not running\n"
                "\t\truns = 1\n"
                "\t\tlast exit code = 0\n"
                "\t}\n"
                "}\n"
            )
            (root / "launchd.terminal").write_text(nested_terminal)
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                self.subTest(artifact="launchd.terminal", label="nested-status"),
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-job-binding-invalid",
                ),
            ):
                self.helper.verify_launchd_success(root)
            (root / "launchd.terminal").write_text(loaded)
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")

            (root / "launchd.terminal").write_text(
                loaded.replace("\tactive count = 0\n", "\tactive count = 1\n", 1)
            )
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                self.subTest(artifact="launchd.terminal", label="active-count"),
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchctl-terminal-invalid",
                ),
            ):
                self.helper.verify_launchd_success(root)
            (root / "launchd.terminal").write_text(loaded)
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")

            binding_mutations = {
                "missing-binding": {
                    name: value
                    for name, value in lifecycle.items()
                    if name not in {"binding", "content_sha256"}
                },
                "extra-binding-field": {
                    **{
                        name: value
                        for name, value in lifecycle.items()
                        if name != "content_sha256"
                    },
                    "binding": {**binding, "foreign": "value"},
                },
                "artifact-controlled-stage-root": {
                    **{
                        name: value
                        for name, value in lifecycle.items()
                        if name != "content_sha256"
                    },
                    "binding": {
                        **binding,
                        "stage_root": "/private/var/tmp/task-witness-macos-launchd-999-1",
                    },
                },
                "missing-ownership-marker": {
                    **{
                        name: value
                        for name, value in lifecycle.items()
                        if name != "content_sha256"
                    },
                    "binding": {},
                },
                "invalid-ownership-marker": {
                    **{
                        name: value
                        for name, value in lifecycle.items()
                        if name != "content_sha256"
                    },
                    "binding": {**binding, "ownership_marker": "invalid"},
                },
                "wrong-ownership-marker": {
                    **{
                        name: value
                        for name, value in lifecycle.items()
                        if name != "content_sha256"
                    },
                    "binding": {**binding, "ownership_marker": "4" * 32},
                },
            }
            for label, unsigned_mutation in binding_mutations.items():
                with self.subTest(label=label):
                    mutation = self.helper._document_with_digest(unsigned_mutation)
                    (root / "lifecycle.json").write_bytes(
                        self.helper.canonical_bytes(mutation)
                    )
                    (root / "SHA256SUMS").unlink()
                    self.helper.write_launchd_artifact_manifest(root, external)
                    external.replace(root / "SHA256SUMS")
                    with (
                        mock.patch.dict(os.environ, context, clear=True),
                        self.assertRaisesRegex(
                            self.helper.ProbeError,
                            "launchd-job-binding-invalid",
                        ),
                    ):
                        self.helper.verify_launchd_success(root)

            (root / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(lifecycle)
            )
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")

            foreign_stage = Path("/private/var/tmp/task-witness-macos-launchd-999-1")
            stage_lifecycle_unsigned = {
                name: value
                for name, value in lifecycle.items()
                if name != "content_sha256"
            }
            stage_lifecycle_unsigned["binding"] = {
                **binding,
                "stage_root": str(foreign_stage),
            }
            stage_lifecycle = self.helper._document_with_digest(
                stage_lifecycle_unsigned
            )
            stage_snapshot = loaded.replace(
                str(plan.stage_root),
                str(foreign_stage),
            )
            (root / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(stage_lifecycle)
            )
            (root / "launchd.loaded").write_text(stage_snapshot)
            (root / "launchd.terminal").write_text(stage_snapshot)
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                self.subTest(label="coordinated-stage-root"),
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-job-binding-invalid",
                ),
            ):
                self.helper.verify_launchd_success(root)

            foreign_account = "twq-ffffffffffff"
            foreign_label = "io.nisavid.task-witness.macos-probe.ffffffffffff"
            foreign_home = f"/Users/{foreign_account}"
            foreign_observations = json.loads(json.dumps(observations))
            foreign_credentials = foreign_observations["credentials"]
            foreign_credentials["passwd"]["name"] = foreign_account
            foreign_credentials["passwd"]["home"] = foreign_home
            foreign_observations["home"]["path"] = foreign_home
            foreign_observations["process"]["label"] = foreign_label
            foreign_probe = self.helper.build_launchd_user_probe_document(
                FROZEN_CANDIDATE_SHA,
                {**context, "TASK_WITNESS_LAUNCHD_LABEL": foreign_label},
                foreign_observations,
            )
            foreign_lifecycle_unsigned = {
                name: value
                for name, value in lifecycle.items()
                if name != "content_sha256"
            }
            foreign_lifecycle_unsigned["label"] = foreign_label
            foreign_lifecycle = self.helper._document_with_digest(
                foreign_lifecycle_unsigned
            )
            foreign_cleanup = self.helper._document_with_digest(
                {
                    "schema_version": 2,
                    "contract": "task-witness-macos-launchd-cleanup-v2",
                    "candidate_sha1": FROZEN_CANDIDATE_SHA,
                    "account": foreign_account,
                    "label": foreign_label,
                    "disposition": "cleaned",
                }
            )
            foreign_snapshot = loaded.replace(
                plan.account.name,
                foreign_account,
            ).replace(plan.label, foreign_label)
            (root / "probe.json").write_bytes(
                self.helper.canonical_bytes(foreign_probe)
            )
            (root / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(foreign_lifecycle)
            )
            (root / "cleanup.json").write_bytes(
                self.helper.canonical_bytes(foreign_cleanup)
            )
            (root / "launchd.loaded").write_text(foreign_snapshot)
            (root / "launchd.terminal").write_text(foreign_snapshot)
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                self.subTest(label="coordinated-account-label-home"),
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-user-probe-ineligible",
                ),
            ):
                self.helper.verify_launchd_success(root)

            ineligible_observations = json.loads(json.dumps(observations))
            ineligible_observations["credentials"]["admin_member"] = True
            ineligible_probe = self.helper.build_launchd_user_probe_document(
                FROZEN_CANDIDATE_SHA,
                context,
                ineligible_observations,
            )
            self.assertEqual(
                ineligible_probe["disposition"],
                "launchd-user-ineligible",
            )
            (root / "probe.json").write_bytes(
                self.helper.canonical_bytes(ineligible_probe)
            )
            (root / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(lifecycle)
            )
            (root / "cleanup.json").write_bytes(self.helper.canonical_bytes(cleanup))
            (root / "launchd.loaded").write_text(loaded)
            (root / "launchd.terminal").write_text(loaded)
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                self.subTest(label="coordinated-ineligible-observation"),
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-user-probe-ineligible",
                ),
            ):
                self.helper.verify_launchd_success(root)

            gid_zero_observations = json.loads(json.dumps(observations))
            gid_zero_credentials = gid_zero_observations["credentials"]
            gid_zero_credentials["real_gid"] = 0
            gid_zero_credentials["effective_gid"] = 0
            gid_zero_credentials["supplementary_gids"] = [0]
            gid_zero_credentials["passwd_group_gids"] = [0]
            gid_zero_credentials["passwd"]["primary_gid"] = 0
            gid_zero_observations["home"]["gid"] = 0
            gid_zero_probe = self.helper.build_launchd_user_probe_document(
                FROZEN_CANDIDATE_SHA,
                context,
                gid_zero_observations,
            )
            (root / "probe.json").write_bytes(
                self.helper.canonical_bytes(gid_zero_probe)
            )
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                self.subTest(label="coordinated-root-group-observation"),
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-user-probe-ineligible",
                ),
            ):
                self.helper.verify_launchd_success(root)
            self.assertEqual(
                gid_zero_probe["disposition"],
                "launchd-user-ineligible",
            )

            passwordless_observations = json.loads(json.dumps(observations))
            passwordless_observations["environment_exact"] = True
            passwordless_observations["passwordless_sudo"] = True
            passwordless_probe = self.helper.build_launchd_user_probe_document(
                FROZEN_CANDIDATE_SHA,
                context,
                passwordless_observations,
            )
            (root / "probe.json").write_bytes(
                self.helper.canonical_bytes(passwordless_probe)
            )
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                self.subTest(label="coordinated-passwordless-sudo"),
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-user-probe-ineligible",
                ),
            ):
                self.helper.verify_launchd_success(root)
            self.assertEqual(
                passwordless_probe["disposition"],
                "launchd-user-ineligible",
            )

            aliased_home_observations = json.loads(json.dumps(observations))
            aliased_home = f"{plan.account.home}/."
            aliased_home_observations["credentials"]["passwd"]["home"] = aliased_home
            aliased_home_observations["home"]["path"] = aliased_home
            aliased_home_probe = self.helper.build_launchd_user_probe_document(
                FROZEN_CANDIDATE_SHA,
                context,
                aliased_home_observations,
            )
            (root / "probe.json").write_bytes(
                self.helper.canonical_bytes(aliased_home_probe)
            )
            (root / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(lifecycle)
            )
            (root / "cleanup.json").write_bytes(self.helper.canonical_bytes(cleanup))
            (root / "launchd.loaded").write_text(loaded)
            (root / "launchd.terminal").write_text(loaded)
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                self.subTest(label="coordinated-aliased-home"),
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-user-probe-ineligible",
                ),
            ):
                self.helper.verify_launchd_success(root)

            foreign_uid_observations = json.loads(json.dumps(observations))
            foreign_uid_observations["credentials"]["real_uid"] = 700
            foreign_uid_observations["credentials"]["effective_uid"] = 700
            foreign_uid_observations["credentials"]["passwd"]["uid"] = 700
            foreign_uid_observations["home"]["uid"] = 700
            foreign_uid_probe = self.helper.build_launchd_user_probe_document(
                FROZEN_CANDIDATE_SHA,
                context,
                foreign_uid_observations,
            )
            (root / "probe.json").write_bytes(
                self.helper.canonical_bytes(foreign_uid_probe)
            )
            (root / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(lifecycle)
            )
            (root / "cleanup.json").write_bytes(self.helper.canonical_bytes(cleanup))
            (root / "launchd.loaded").write_text(loaded)
            (root / "launchd.terminal").write_text(loaded)
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                self.subTest(label="coordinated-out-of-range-uid"),
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-user-probe-ineligible",
                ),
            ):
                self.helper.verify_launchd_success(root)

            (root / "probe.json").write_bytes(self.helper.canonical_bytes(probe))
            (root / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(lifecycle)
            )
            (root / "cleanup.json").write_bytes(self.helper.canonical_bytes(cleanup))
            (root / "launchd.loaded").write_text(loaded)
            (root / "launchd.terminal").write_text(loaded)
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")

            changed = dict(lifecycle)
            changed["kickstart_pid"] = 9999
            unsigned = {
                name: value
                for name, value in changed.items()
                if name != "content_sha256"
            }
            changed["content_sha256"] = hashlib.sha256(
                self.helper.canonical_bytes(unsigned)
            ).hexdigest()
            (root / "lifecycle.json").write_bytes(self.helper.canonical_bytes(changed))
            (root / "SHA256SUMS").unlink()
            self.helper.write_launchd_artifact_manifest(root, external)
            external.replace(root / "SHA256SUMS")
            with (
                mock.patch.dict(os.environ, context, clear=True),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-user-probe-ineligible",
                ),
            ):
                self.helper.verify_launchd_success(root)

    def test_lifecycle_artifact_persists_only_validated_success_binding(self) -> None:
        context = self.launchd_context()
        plan = self.lifecycle_plan(
            Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        )
        state = self.lifecycle_state(plan)
        loaded = self.launchctl_job(plan, state)
        secret = "artifact-default-environment-secret-canary"
        raw_loaded = self.launchctl_job_with_unrelated_blocks(
            plan,
            state,
            secret=secret,
        )
        expected_binding = {"ownership_marker": state["ownership_marker"]}
        with mock.patch.dict(os.environ, context, clear=True):
            self.assertEqual(
                self.helper._validate_launchd_job_binding(loaded, plan, state),
                expected_binding,
            )
        probe = self.helper.build_launchd_user_probe_document(
            FROZEN_CANDIDATE_SHA,
            context,
            self.launchd_observations(),
        )
        child = {
            "probe.json": self.helper.canonical_bytes(probe),
            "probe.status": b"0\n",
            "probe.stderr": b"",
            "probe.stdout": b"launchd-user-eligible\n",
        }

        def capture(
            *,
            status: int,
            error_code: str | None,
            secondary_error_code: str | None = None,
        ) -> dict[str, bytes]:
            written: dict[str, bytes] = {}

            def record(path: Path, raw: bytes, _mode: int) -> None:
                written[path.name] = raw

            with (
                mock.patch.object(
                    self.helper,
                    "_read_launchd_child_payloads",
                    return_value=child,
                ),
                mock.patch.object(
                    self.helper,
                    "_write_root_file",
                    side_effect=record,
                ),
            ):
                self.helper._write_lifecycle_artifact(
                    artifact_root=Path("/private/tmp/artifact"),
                    plan=plan,
                    binding=expected_binding,
                    environment=context,
                    loaded=raw_loaded,
                    terminal=raw_loaded,
                    kickstart_pid=4321,
                    status=status,
                    error_code=error_code,
                    secondary_error_code=secondary_error_code,
                )
            return written

        success_payloads = capture(status=0, error_code=None)
        success = json.loads(success_payloads["lifecycle.json"].decode("utf-8"))
        self.assertEqual(success["binding"], expected_binding)
        self.assertEqual(set(success["binding"]), {"ownership_marker"})
        self.assertEqual(success_payloads["launchd.loaded"], loaded.encode("utf-8"))
        self.assertEqual(success_payloads["launchd.terminal"], loaded.encode("utf-8"))
        self.assertNotIn(
            secret.encode("utf-8"),
            b"".join(success_payloads.values()),
        )

        failure_payloads = capture(
            status=2,
            error_code="lifecycle-command-nonzero-launchd-bootstrap",
            secondary_error_code="lifecycle-command-nonzero-launchd-bootout",
        )
        failure = json.loads(failure_payloads["lifecycle.json"].decode("utf-8"))
        failure_probe = json.loads(failure_payloads["probe.json"].decode("utf-8"))
        expected_error = {
            "code": "lifecycle-command-nonzero-launchd-bootstrap",
            "secondary_code": "lifecycle-command-nonzero-launchd-bootout",
        }
        self.assertEqual(failure["error"], expected_error)
        self.assertEqual(failure_probe["error"], expected_error)
        self.assertEqual(failure_probe["disposition"], "probe-error")
        self.assertEqual(failure_payloads["probe.status"], b"2\n")
        self.assertNotIn("binding", failure)
        self.assertNotIn(
            secret.encode("utf-8"),
            b"".join(failure_payloads.values()),
        )

    def test_successful_lifecycle_passes_validated_binding_to_artifact_writer(
        self,
    ) -> None:
        plan = self.lifecycle_plan(
            Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        )
        state = self.lifecycle_state(plan)
        loaded = self.launchctl_job(plan, state)
        secret = "producer-default-environment-secret-canary"
        raw_loaded = self.launchctl_job_with_unrelated_blocks(
            plan,
            state,
            secret=secret,
        )
        expected_binding = {"ownership_marker": state["ownership_marker"]}
        reset_evidence = {
            "authorization_sha256": "4" * 64,
            "capability": "github-hosted-ephemeral-user-domain-reset-v1",
            "disposition": "performed",
            "precondition": "disposable-user-pid1-parented-processes-remain",
        }
        events: list[str] = []

        def command(argv: list[str], **_kwargs: object) -> str:
            if argv[:3] == ["/bin/launchctl", "bootstrap", "system"]:
                events.append("bootstrap")
                return ""
            if argv[:3] == ["/bin/launchctl", "kickstart", "-p"]:
                events.append("kickstart")
                return "4321"
            if argv == [
                "/bin/launchctl",
                "bootout",
                f"system/{plan.label}",
            ]:
                events.append("bootout")
                return ""
            raise AssertionError(argv)

        def create_home(_account: object) -> None:
            events.append("create-home")

        def validate_root(
            _account: object,
            *,
            diagnostic_phase: str,
        ) -> None:
            events.append(f"validate:{diagnostic_phase}")

        def write_marker(_plan: object, _state: object) -> None:
            events.append("write-marker")

        def poll_terminal(
            _plan: object,
            _state: object,
        ) -> tuple[str, int]:
            events.append("poll-terminal")
            return loaded, 0

        def load_child_probe(*_args: object, **_kwargs: object) -> dict[str, object]:
            events.append("load-child-probe")
            return {"observations": {"process": {"pid": 4321}}}

        def quiesce_user(*_args: object, **_kwargs: object) -> dict[str, str]:
            events.append("quiesce")
            return reset_evidence

        with (
            mock.patch.dict(os.environ, self.launchd_context(), clear=True),
            mock.patch.object(self.helper, "_normalized_context"),
            mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
            mock.patch.object(self.helper, "_create_root_directory"),
            mock.patch.object(
                self.helper,
                "_load_lifecycle_state",
                return_value=(plan, state),
            ),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=self.helper.ValidatedStageBindings(None, None),
            ),
            mock.patch.object(self.helper, "_create_disposable_account"),
            mock.patch.object(
                self.helper,
                "_create_disposable_home",
                side_effect=create_home,
            ),
            mock.patch.object(
                self.helper,
                "_validate_disposable_home_root",
                side_effect=validate_root,
            ) as validate_home_root,
            mock.patch.object(self.helper, "_require_disposable_uid_available"),
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_require_command_success",
                side_effect=command,
            ),
            mock.patch.object(
                self.helper,
                "_write_launchd_ownership_marker",
                side_effect=write_marker,
            ),
            mock.patch.object(
                self.helper,
                "_launchd_job_snapshot",
                side_effect=[raw_loaded, raw_loaded, None],
            ),
            mock.patch.object(
                self.helper,
                "_poll_launchd_terminal",
                side_effect=poll_terminal,
            ),
            mock.patch.object(
                self.helper,
                "_load_canonical_document",
                side_effect=load_child_probe,
            ),
            mock.patch.object(
                self.helper,
                "_quiesce_disposable_user",
                side_effect=quiesce_user,
            ) as quiesce,
            mock.patch.object(self.helper, "_write_lifecycle_artifact") as write,
        ):
            status = self.helper.run_launchd_user_lifecycle(
                stage_root=plan.stage_root,
                artifact_root=Path("/private/tmp/artifact"),
                candidate_sha=FROZEN_CANDIDATE_SHA,
                runner_uid=501,
                runner_gid=20,
                user_domain_reset_authorization="1" * 40,
            )

        self.assertEqual(status, 0)
        write.assert_called_once()
        self.assertEqual(write.call_args.kwargs["binding"], expected_binding)
        self.assertEqual(write.call_args.kwargs["loaded"], loaded)
        self.assertEqual(write.call_args.kwargs["terminal"], loaded)
        self.assertEqual(write.call_args.kwargs["domain_reset"], reset_evidence)
        self.assertTrue(quiesce.call_args.kwargs["allow_create"])
        self.assertEqual(quiesce.call_args.args[3], "1" * 40)
        self.assertEqual(
            events,
            [
                "create-home",
                "validate:post-home-create",
                "bootstrap",
                "validate:post-system-bootstrap",
                "write-marker",
                "kickstart",
                "poll-terminal",
                "load-child-probe",
                "validate:post-child-terminal",
                "bootout",
                "validate:post-system-bootout",
                "quiesce",
            ],
        )
        self.assertTrue(
            all(
                call.args == (plan.account,)
                for call in validate_home_root.call_args_list
            )
        )
        self.assertNotIn(secret, repr(write.call_args.kwargs))

    def test_lifecycle_home_checkpoint_failures_preserve_order_and_precedence(
        self,
    ) -> None:
        plan = self.lifecycle_plan(
            Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        )
        state = self.lifecycle_state(plan)
        loaded = self.launchctl_job(plan, state)
        reset_evidence = self.helper._domain_reset_evidence(None)
        success_prefix = [
            "create-home",
            "validate:post-home-create",
            "bootstrap",
            "validate:post-system-bootstrap",
            "write-marker",
            "kickstart",
            "poll-terminal",
            "load-child-probe",
            "validate:post-child-terminal",
            "bootout",
            "validate:post-system-bootout",
            "quiesce",
        ]
        expected_events = {
            "post-home-create": success_prefix[:2],
            "post-system-bootstrap": [
                *success_prefix[:4],
                "bootout",
                "validate:post-system-bootout",
                "quiesce",
            ],
            "post-child-terminal": success_prefix[:9] + success_prefix[9:],
            "post-system-bootout": success_prefix,
        }

        for failing_phase, events_expected in expected_events.items():
            with self.subTest(failing_phase=failing_phase):
                events: list[str] = []
                phase_code = (
                    f"home-cleanup-{failing_phase}-"
                    "home-entry-known-library-owned-directory"
                )

                def command(
                    argv: list[str],
                    _events: list[str] = events,
                    **_kwargs: object,
                ) -> str:
                    if argv[:3] == ["/bin/launchctl", "bootstrap", "system"]:
                        _events.append("bootstrap")
                        return ""
                    if argv[:3] == ["/bin/launchctl", "kickstart", "-p"]:
                        _events.append("kickstart")
                        return "4321"
                    raise AssertionError(argv)

                def validate_root(
                    _account: object,
                    *,
                    diagnostic_phase: str,
                    _events: list[str] = events,
                    _failing_phase: str = failing_phase,
                    _phase_code: str = phase_code,
                ) -> None:
                    _events.append(f"validate:{diagnostic_phase}")
                    if diagnostic_phase == _failing_phase:
                        raise self.helper.ProbeError(_phase_code)

                def poll_terminal(
                    _plan: object,
                    _state: object,
                    _events: list[str] = events,
                ) -> tuple[str, int]:
                    _events.append("poll-terminal")
                    return loaded, 0

                def load_child_probe(
                    *_args: object,
                    _events: list[str] = events,
                    **_kwargs: object,
                ) -> dict[str, object]:
                    _events.append("load-child-probe")
                    return {"observations": {"process": {"pid": 4321}}}

                def quiesce_user(
                    *_args: object,
                    _events: list[str] = events,
                    **_kwargs: object,
                ) -> dict[str, object]:
                    _events.append("quiesce")
                    return reset_evidence

                with ExitStack() as stack:
                    stack.enter_context(
                        mock.patch.dict(
                            os.environ,
                            self.launchd_context(),
                            clear=True,
                        )
                    )
                    for name in (
                        "_normalized_context",
                        "_validate_lifecycle_arguments",
                        "_create_root_directory",
                        "_create_disposable_account",
                        "_require_disposable_uid_available",
                        "_require_launchd_absent",
                        "_ensure_failed_child_files",
                    ):
                        stack.enter_context(mock.patch.object(self.helper, name))
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_load_lifecycle_state",
                            return_value=(plan, state),
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_validate_exact_stage",
                            return_value=self.helper.ValidatedStageBindings(None, None),
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_create_disposable_home",
                            side_effect=lambda _account, _events=events: _events.append(
                                "create-home"
                            ),
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_validate_disposable_home_root",
                            side_effect=validate_root,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_require_command_success",
                            side_effect=command,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_write_launchd_ownership_marker",
                            side_effect=lambda *_args, _events=events: _events.append(
                                "write-marker"
                            ),
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_launchd_job_snapshot",
                            return_value=loaded,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_poll_launchd_terminal",
                            side_effect=poll_terminal,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_load_canonical_document",
                            side_effect=load_child_probe,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_reconcile_in_process_bootstrap",
                            side_effect=lambda *_args, _events=events: _events.append(
                                "bootout"
                            ),
                        )
                    )
                    quiesce = stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_quiesce_disposable_user",
                            side_effect=quiesce_user,
                        )
                    )
                    write = stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_write_lifecycle_artifact",
                        )
                    )
                    stack.enter_context(redirect_stderr(io.StringIO()))
                    status = self.helper.run_launchd_user_lifecycle(
                        stage_root=plan.stage_root,
                        artifact_root=Path("/private/tmp/artifact"),
                        candidate_sha=FROZEN_CANDIDATE_SHA,
                        runner_uid=501,
                        runner_gid=20,
                        user_domain_reset_authorization="1" * 40,
                    )

                self.assertEqual(status, 2)
                self.assertEqual(events, events_expected)
                self.assertEqual(write.call_args.kwargs["error_code"], phase_code)
                self.assertIsNone(write.call_args.kwargs["secondary_error_code"])
                if failing_phase == "post-home-create":
                    quiesce.assert_not_called()
                else:
                    self.assertFalse(quiesce.call_args.kwargs["allow_create"])

        events = []
        primary = "passwordless-sudo-probe-failed"
        post_bootout = (
            "home-cleanup-post-system-bootout-home-entry-known-library-owned-directory"
        )

        def command_with_terminal_error(
            argv: list[str],
            **_kwargs: object,
        ) -> str:
            if argv[:3] == ["/bin/launchctl", "bootstrap", "system"]:
                events.append("bootstrap")
                return ""
            if argv[:3] == ["/bin/launchctl", "kickstart", "-p"]:
                events.append("kickstart")
                return "4321"
            raise AssertionError(argv)

        def validate_after_primary(
            _account: object,
            *,
            diagnostic_phase: str,
        ) -> None:
            events.append(f"validate:{diagnostic_phase}")
            if diagnostic_phase == "post-system-bootout":
                raise self.helper.ProbeError(post_bootout)

        with (
            mock.patch.dict(os.environ, self.launchd_context(), clear=True),
            mock.patch.object(self.helper, "_normalized_context"),
            mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
            mock.patch.object(self.helper, "_create_root_directory"),
            mock.patch.object(
                self.helper,
                "_load_lifecycle_state",
                return_value=(plan, state),
            ),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=self.helper.ValidatedStageBindings(None, None),
            ),
            mock.patch.object(self.helper, "_create_disposable_account"),
            mock.patch.object(
                self.helper,
                "_create_disposable_home",
                side_effect=lambda _account: events.append("create-home"),
            ),
            mock.patch.object(
                self.helper,
                "_validate_disposable_home_root",
                side_effect=validate_after_primary,
            ),
            mock.patch.object(self.helper, "_require_disposable_uid_available"),
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_require_command_success",
                side_effect=command_with_terminal_error,
            ),
            mock.patch.object(
                self.helper,
                "_write_launchd_ownership_marker",
                side_effect=lambda *_args: events.append("write-marker"),
            ),
            mock.patch.object(
                self.helper,
                "_launchd_job_snapshot",
                return_value=loaded,
            ),
            mock.patch.object(
                self.helper,
                "_poll_launchd_terminal",
                side_effect=lambda *_args: (
                    events.append("poll-terminal"),
                    (_ for _ in ()).throw(self.helper.ProbeError(primary)),
                )[1],
            ),
            mock.patch.object(
                self.helper,
                "_reconcile_in_process_bootstrap",
                side_effect=lambda *_args: events.append("bootout"),
            ),
            mock.patch.object(
                self.helper,
                "_quiesce_disposable_user",
                side_effect=lambda *_args, **_kwargs: (
                    events.append("quiesce"),
                    reset_evidence,
                )[1],
            ) as quiesce,
            mock.patch.object(self.helper, "_ensure_failed_child_files"),
            mock.patch.object(self.helper, "_write_lifecycle_artifact") as write,
            redirect_stderr(io.StringIO()),
        ):
            status = self.helper.run_launchd_user_lifecycle(
                stage_root=plan.stage_root,
                artifact_root=Path("/private/tmp/artifact"),
                candidate_sha=FROZEN_CANDIDATE_SHA,
                runner_uid=501,
                runner_gid=20,
                user_domain_reset_authorization="1" * 40,
            )

        self.assertEqual(status, 2)
        self.assertEqual(
            events,
            [
                "create-home",
                "validate:post-home-create",
                "bootstrap",
                "validate:post-system-bootstrap",
                "write-marker",
                "kickstart",
                "poll-terminal",
                "bootout",
                "validate:post-system-bootout",
                "quiesce",
            ],
        )
        self.assertEqual(write.call_args.kwargs["error_code"], primary)
        self.assertEqual(
            write.call_args.kwargs["secondary_error_code"],
            post_bootout,
        )
        self.assertFalse(quiesce.call_args.kwargs["allow_create"])

    def test_child_probe_error_remains_primary_through_reconciliation(self) -> None:
        context = self.launchd_context()
        plan = self.lifecycle_plan(
            Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        )
        state = self.lifecycle_state(plan)
        loaded = self.launchctl_job(plan, state)
        primary = "passwordless-sudo-probe-failed"
        secondary = "lifecycle-command-nonzero-launchd-bootout"
        child_probe = self.helper.build_launchd_user_probe_error_document(
            FROZEN_CANDIDATE_SHA,
            context,
            primary,
        )
        payloads: dict[str, bytes] = {}

        def record(path: Path, raw: bytes, _mode: int) -> None:
            payloads[path.name] = raw

        def command(argv: list[str], **_kwargs: object) -> str:
            if argv[:3] == ["/bin/launchctl", "bootstrap", "system"]:
                return ""
            if argv[:3] == ["/bin/launchctl", "kickstart", "-p"]:
                return "4321"
            raise AssertionError(argv)

        with (
            mock.patch.dict(os.environ, context, clear=True),
            mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
            mock.patch.object(self.helper, "_create_root_directory"),
            mock.patch.object(
                self.helper,
                "_load_lifecycle_state",
                return_value=(plan, state),
            ),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=self.helper.ValidatedStageBindings(None, None),
            ),
            mock.patch.object(self.helper, "_create_disposable_account"),
            mock.patch.object(self.helper, "_create_disposable_home"),
            mock.patch.object(self.helper, "_validate_disposable_home_root"),
            mock.patch.object(self.helper, "_require_disposable_uid_available"),
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_require_command_success",
                side_effect=command,
            ),
            mock.patch.object(self.helper, "_write_launchd_ownership_marker"),
            mock.patch.object(
                self.helper,
                "_launchd_job_snapshot",
                return_value=loaded,
            ),
            mock.patch.object(
                self.helper,
                "_poll_launchd_terminal",
                return_value=(loaded, 2),
            ),
            mock.patch.object(
                self.helper,
                "_load_canonical_document",
                return_value=child_probe,
            ),
            mock.patch.object(
                self.helper,
                "_reconcile_in_process_bootstrap",
                side_effect=self.helper.ProbeError(secondary),
            ),
            mock.patch.object(
                self.helper,
                "_write_root_file",
                side_effect=record,
            ),
        ):
            status = self.helper.run_launchd_user_lifecycle(
                stage_root=plan.stage_root,
                artifact_root=Path("/private/tmp/artifact"),
                candidate_sha=FROZEN_CANDIDATE_SHA,
                runner_uid=501,
                runner_gid=20,
            )

        self.assertEqual(status, 2)
        expected_error = {"code": primary, "secondary_code": secondary}
        lifecycle = json.loads(payloads["lifecycle.json"].decode("utf-8"))
        probe = json.loads(payloads["probe.json"].decode("utf-8"))
        self.assertEqual(lifecycle["error"], expected_error)
        self.assertEqual(probe["error"], expected_error)
        self.assertEqual(payloads["launchd.loaded"], b"")
        self.assertEqual(payloads["launchd.terminal"], b"")
        self.assertNotEqual(lifecycle["error"]["code"], "launchd-child-pid-disagrees")

    def test_unexpected_launchd_environment_is_not_passed_to_artifact_writer(
        self,
    ) -> None:
        context = self.launchd_context()
        plan = self.lifecycle_plan(
            Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        )
        state = self.lifecycle_state(plan)
        loaded = self.launchctl_job(plan, state)
        xpc_line = f"\t\tXPC_SERVICE_NAME => {plan.label}\n"
        unexpected = loaded.replace(
            xpc_line,
            "\t\tUNEXPECTED_SECRET => must-not-upload\n" + xpc_line,
            1,
        )

        with (
            mock.patch.dict(os.environ, context, clear=True),
            mock.patch.object(self.helper, "_normalized_context"),
            mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
            mock.patch.object(self.helper, "_create_root_directory"),
            mock.patch.object(
                self.helper,
                "_load_lifecycle_state",
                return_value=(plan, state),
            ),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=self.helper.ValidatedStageBindings(None, None),
            ),
            mock.patch.object(self.helper, "_create_disposable_account"),
            mock.patch.object(self.helper, "_create_disposable_home"),
            mock.patch.object(self.helper, "_validate_disposable_home_root"),
            mock.patch.object(self.helper, "_require_disposable_uid_available"),
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_require_command_success",
                return_value="",
            ),
            mock.patch.object(self.helper, "_write_launchd_ownership_marker"),
            mock.patch.object(
                self.helper,
                "_launchd_job_snapshot",
                return_value=unexpected,
            ),
            mock.patch.object(self.helper, "_reconcile_in_process_bootstrap"),
            mock.patch.object(self.helper, "_require_no_uid_processes"),
            mock.patch.object(self.helper, "_ensure_failed_child_files"),
            mock.patch.object(self.helper, "_write_lifecycle_artifact") as write,
        ):
            status = self.helper.run_launchd_user_lifecycle(
                stage_root=plan.stage_root,
                artifact_root=Path("/private/tmp/artifact"),
                candidate_sha=FROZEN_CANDIDATE_SHA,
                runner_uid=501,
                runner_gid=20,
            )

        self.assertEqual(status, 2)
        self.assertEqual(write.call_args.kwargs["loaded"], "")
        self.assertEqual(write.call_args.kwargs["terminal"], "")
        self.assertNotIn(
            "must-not-upload",
            repr(write.call_args.kwargs),
        )

    def test_library_activation_phase_policy_is_narrow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            library = home / "Library"
            probe.mkdir(parents=True, mode=0o700)
            library.mkdir()
            home.chmod(0o700)
            probe.chmod(0o700)
            for name in self.helper.LAUNCHD_CHILD_FILES:
                (probe / name).write_bytes(b"value")
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )

            for phase in ("post-home-create", "post-system-bootstrap"):
                with (
                    self.subTest(phase=phase),
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_disposable_home_root(
                        account,
                        diagnostic_phase=phase,
                    )
                self.assertEqual(
                    raised.exception.code,
                    f"home-cleanup-{phase}-home-entry-known-library-owned-directory",
                )

            self.helper._validate_disposable_home_root(
                account,
                diagnostic_phase="child-entry",
            )
            self.helper._validate_exact_disposable_home(
                home,
                expected_uid=account.uid,
                expected_gid=account.gid,
                diagnostic_phase="child-read",
            )
            self.helper._validate_exact_disposable_home(
                home,
                expected_uid=account.uid,
                expected_gid=account.gid,
                diagnostic_phase="pre-journal",
            )

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            canary = home / "private-owned-directory-canary"
            canary.mkdir()
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )

            with self.assertRaises(self.helper.ProbeError) as raised:
                self.helper._validate_disposable_home_root(
                    account,
                    diagnostic_phase="child-entry",
                )
            self.assertEqual(
                raised.exception.code,
                ("home-cleanup-child-entry-home-entry-single-owned-directory-other"),
            )
            self.assertTrue(canary.is_dir())
            self.assertNotIn(canary.name, raised.exception.code)

    def test_library_activation_rejects_foreign_anchor_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            library = home / "Library"
            probe.mkdir(parents=True, mode=0o700)
            library.mkdir()
            home.chmod(0o700)
            probe.chmod(0o700)
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            stable = self.helper._home_entry_observation_snapshot(
                home,
                expected_uid=account.uid,
                expected_gid=account.gid,
            )
            self.assertEqual(len(stable[2]), 1)

            for label, field in (
                ("uid", 7),
                ("gid", 8),
                ("device", 9),
            ):
                changed_entry = list(stable[2][0])
                changed_entry[field] = False
                changed = (stable[0], stable[1], (tuple(changed_entry),))
                with (
                    self.subTest(label=label),
                    mock.patch.object(
                        self.helper,
                        "_home_entry_observation_snapshot",
                        side_effect=(changed, changed),
                    ),
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_disposable_home_root(
                        account,
                        diagnostic_phase="child-entry",
                    )
                self.assertEqual(
                    raised.exception.code,
                    ("home-cleanup-child-entry-home-entry-unsafe-or-foreign"),
                )
                self.assertTrue(library.is_dir())

    def test_bounded_library_rejects_darwin_extended_acl_entry(self) -> None:
        calls: list[tuple[str, object]] = []

        class FakeFunction:
            argtypes = None
            restype = None

            def __init__(self, name: str, result: object) -> None:
                self.name = name
                self.result = result

            def __call__(self, *args: object) -> object:
                calls.append((self.name, args))
                return self.result

        acl_get_fd_np = FakeFunction("get-fd", 1)
        acl_get_entry = FakeFunction("get-entry", 0)
        acl_free = FakeFunction("free", 0)
        fake_libc = SimpleNamespace(
            acl_get_fd_np=acl_get_fd_np,
            acl_get_entry=acl_get_entry,
            acl_free=acl_free,
        )

        for cause in (
            "bound-home-acl",
            "root-acl",
            "directory-acl",
            "file-acl",
        ):
            calls.clear()
            with (
                self.subTest(cause=cause),
                mock.patch.object(
                    self.helper.ctypes,
                    "CDLL",
                    return_value=fake_libc,
                ),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._require_no_extended_acl(17, unsafe_cause=cause)

            self.assertEqual(raised.exception.code, "home-library-unsafe-entry")
            self.assertEqual(
                raised.exception.secondary_code,
                f"home-library-unsafe-entry-{cause}",
            )
            self.assertEqual(
                [name for name, _args in calls], ["get-fd", "get-entry", "free"]
            )
            self.assertEqual(calls[0][1], (17, self.helper.ACL_TYPE_EXTENDED))
            self.assertEqual(calls[1][1][1], self.helper.ACL_FIRST_ENTRY)

    def test_bounded_library_rejects_native_extended_attributes(self) -> None:
        native_helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (
                (
                    "file",
                    root / "file",
                    "com.example.task-witness",
                    "file-xattr",
                    ("nonapple-other", "mixed-other"),
                ),
                (
                    "directory",
                    root / "directory",
                    "com.example.task-witness",
                    "directory-xattr",
                    None,
                ),
                (
                    "root",
                    root / "root",
                    "com.example.task-witness",
                    "root-xattr",
                    None,
                ),
                (
                    "resource-fork",
                    root / "resource",
                    "com.apple.ResourceFork",
                    "file-xattr",
                    ("resource-fork", "apple-mixed"),
                ),
            )
            for label, path, attribute, cause, family in cases:
                with self.subTest(label=label):
                    if label in {"directory", "root"}:
                        path.mkdir()
                        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY
                    else:
                        path.write_bytes(b"value")
                        flags = os.O_RDONLY | os.O_CLOEXEC
                    self.write_extended_attribute(path, attribute)
                    self.assertIn(
                        attribute,
                        subprocess.run(
                            ["/usr/bin/xattr", str(path)],
                            check=True,
                            capture_output=True,
                            text=True,
                        ).stdout.splitlines(),
                    )
                    descriptor = os.open(path, flags)
                    try:
                        if family is None:
                            with self.assertRaises(native_helper.ProbeError) as raised:
                                native_helper._require_no_extended_attributes(
                                    descriptor,
                                    unsafe_cause=cause,
                                )
                        else:
                            observed = native_helper._home_library_file_xattr_state(
                                descriptor,
                                diagnostic_phase="journal-inventory",
                                diagnostic_path_family="direct",
                            )
                    finally:
                        os.close(descriptor)
                    if family is None:
                        self.assertEqual(
                            raised.exception.code,
                            "home-library-unsafe-entry",
                        )
                        self.assertEqual(
                            raised.exception.secondary_code,
                            f"home-library-unsafe-entry-{cause}",
                        )
                    else:
                        self.assertIn(observed, family)

        calls: list[tuple[object, ...]] = []

        class NoAttributes:
            argtypes = None
            restype = None

            def __call__(self, *args: object) -> int:
                calls.append(args)
                return 0

        with mock.patch.object(
            native_helper.ctypes,
            "CDLL",
            return_value=SimpleNamespace(flistxattr=NoAttributes()),
        ):
            native_helper._require_no_extended_attributes(
                17,
                unsafe_cause="root-xattr",
            )
        self.assertEqual(
            calls,
            [(17, None, 0, native_helper.XATTR_SHOWCOMPRESSION)],
        )

    def test_file_xattr_refined_diagnostic_contract_is_finite_and_value_free(
        self,
    ) -> None:
        primary = "home-library-unsafe-entry"
        phases = (
            "journal-inventory",
            "source-revalidation",
            "quarantine-revalidation",
            "delete-boundary",
        )
        path_families = (
            "direct",
            "preferences",
            "caches",
            "application-support",
            "containers",
            "group-containers",
            "metadata",
            "nested-other",
        )
        families = (
            "apple-provenance",
            "apple-quarantine",
            "apple-quarantine-stable-bounded",
            "apple-quarantine-unreadable",
            "apple-metadata",
            "apple-mixed",
            "apple-other",
            "compression",
            "resource-fork",
            "finder-info",
            "nonapple-other",
            "mixed-other",
            "overflow",
            "unstable",
            "unclassified",
        )
        self.assertEqual(tuple(self.helper._XATTR_PHASES), phases)
        self.assertEqual(tuple(self.helper._XATTR_PATH_FAMILIES), path_families)
        self.assertEqual(tuple(self.helper._XATTR_FAMILIES), families)
        constructor = getattr(
            self.helper,
            "_home_library_file_xattr_error",
            None,
        )
        self.assertIsNotNone(constructor)
        assert constructor is not None

        class ValueBearingToken:
            def __eq__(self, _other: object) -> bool:
                return True

            def __str__(self) -> str:
                return "private-equality-canary"

        class ValueBearingStr(str):
            def __format__(self, _spec: str) -> str:
                return "private-format-canary"

        first = constructor(
            "journal-inventory",
            "preferences",
            "apple-provenance",
        )
        expected_secondary = (
            "home-library-unsafe-entry-file-xattr-"
            "journal-inventory-preferences-apple-provenance"
        )
        self.assertEqual(
            (first.code, first.secondary_code),
            (primary, expected_secondary),
        )
        for phase in phases:
            for path_family in path_families:
                for family in families:
                    with self.subTest(
                        phase=phase,
                        path_family=path_family,
                        family=family,
                    ):
                        error = constructor(phase, path_family, family)
                        secondary = (
                            f"{primary}-file-xattr-{phase}-{path_family}-{family}"
                        )
                        self.assertEqual(error.code, primary)
                        self.assertEqual(error.secondary_code, secondary)
                        self.assertLessEqual(len(secondary), 128)
                        self.assertEqual(
                            self.helper._validated_probe_error(
                                error.code,
                                error.secondary_code,
                            ),
                            {
                                "code": primary,
                                "secondary_code": secondary,
                            },
                        )

        invalid = (
            ("private-phase-canary", "direct", "compression"),
            ("journal-inventory", "private-path-canary", "compression"),
            ("journal-inventory", "direct", "private-family-canary"),
            (None, "direct", "compression"),
            ("journal-inventory", ["direct"], "compression"),
            (ValueBearingToken(), "direct", "compression"),
            ("journal-inventory", ValueBearingToken(), "compression"),
            ("journal-inventory", "direct", ValueBearingToken()),
            (ValueBearingStr("journal-inventory"), "direct", "compression"),
            ("journal-inventory", ValueBearingStr("direct"), "compression"),
            ("journal-inventory", "direct", ValueBearingStr("compression")),
        )
        for phase, path_family, family in invalid:
            with self.subTest(invalid=(phase, path_family, family)):
                error = constructor(phase, path_family, family)
                self.assertEqual(error.code, primary)
                self.assertEqual(
                    error.secondary_code,
                    "home-library-diagnostic-invalid",
                )
                self.assertNotIn("canary", error.secondary_code)

    def test_file_xattr_path_family_uses_exact_first_library_component(
        self,
    ) -> None:
        classify = getattr(
            self.helper,
            "_home_library_file_xattr_path_family",
            None,
        )
        self.assertIsNotNone(classify)
        assert classify is not None
        self.assertEqual(
            self.helper._XATTR_PATH_FAMILY_BY_COMPONENT,
            {
                "Preferences": "preferences",
                "Caches": "caches",
                "Application Support": "application-support",
                "Containers": "containers",
                "Group Containers": "group-containers",
                "Metadata": "metadata",
            },
        )
        subclass_calls = []

        class ValueBearingStr(str):
            def __hash__(self) -> int:
                subclass_calls.append("private-hash-canary")
                return super().__hash__()

            def __eq__(self, other: object) -> bool:
                subclass_calls.append("private-equality-canary")
                return super().__eq__(other)

        class ValueBearingTuple(tuple):
            pass

        cases = (
            (("Library", "direct.bin"), "direct"),
            (("Library", "Preferences", "item"), "preferences"),
            (("Library", "Caches", "item"), "caches"),
            (("Library", "Application Support", "item"), "application-support"),
            (("Library", "Containers", "item"), "containers"),
            (("Library", "Group Containers", "item"), "group-containers"),
            (("Library", "Metadata", "item"), "metadata"),
            (("Library", "Preferences", "ByHost", "item"), "preferences"),
            (("Library", "Caches", "Deep", "item"), "caches"),
            (
                ("Library", "Application Support", "Vendor", "item"),
                "application-support",
            ),
            (("Library", "Containers", "id", "Data", "item"), "containers"),
            (
                ("Library", "Group Containers", "id", "Data", "item"),
                "group-containers",
            ),
            (("Library", "Metadata", "CoreSpotlight", "item"), "metadata"),
            (("Library", "Other", "Deep", "item"), "nested-other"),
            (("Library", "preferences", "private-canary"), "nested-other"),
            (("Library", "Caches ", "private-canary"), "nested-other"),
            (("Library", "ApplicationSupport", "private-canary"), "nested-other"),
            (("Library", "Container", "private-canary"), "nested-other"),
            (("Library", "Group containers", "private-canary"), "nested-other"),
            (("Library", "MetaData", "private-canary"), "nested-other"),
            (("Library", "Preferences"), "direct"),
            (("private-root-canary", "Preferences", "item"), "nested-other"),
            (["Library", "Preferences", "item"], "nested-other"),
            (
                ValueBearingTuple(("Library", "Preferences", "item")),
                "nested-other",
            ),
            ((), "nested-other"),
            (("Library",), "nested-other"),
            ((7, "Preferences", "item"), "nested-other"),
            (("Library", 7, "item"), "nested-other"),
            (("Library", "Preferences", 7), "nested-other"),
            (
                (ValueBearingStr("Library"), "Preferences", "item"),
                "nested-other",
            ),
            (
                ("Library", ValueBearingStr("Preferences"), "item"),
                "nested-other",
            ),
            (
                ("Library", "Preferences", ValueBearingStr("item")),
                "nested-other",
            ),
        )
        self.assertEqual(
            [classify(components) for components, _expected in cases],
            [expected for _components, expected in cases],
        )
        self.assertEqual(subclass_calls, [])

    def test_file_xattr_classifier_refines_apple_names_without_values(self) -> None:
        show = self.helper.XATTR_SHOWCOMPRESSION
        maximum = self.helper.MAX_HOME_LIBRARY_XATTR_LIST_BYTES

        def encoded(values: tuple[bytes, ...]) -> bytes:
            return b"" if not values else b"\0".join(values) + b"\0"

        class StableList:
            def __init__(
                self,
                normal: tuple[bytes, ...],
                shown: tuple[bytes, ...] | None = None,
            ) -> None:
                self.raw = {
                    0: encoded(normal),
                    show: encoded(normal if shown is None else shown),
                }
                self.calls: list[tuple[int, int, int]] = []

            def __call__(
                self,
                descriptor: int,
                buffer: object,
                size: int,
                options: int,
            ) -> int:
                self.calls.append((descriptor, size, options))
                raw = self.raw[options]
                ctypes.memmove(buffer, raw, len(raw))
                return len(raw)

        full_buffer_names = tuple(
            prefix + b"x" * (127 - len(prefix))
            for index in range(32)
            for prefix in (f"org.example.{index:02d}.".encode("ascii"),)
        )
        self.assertEqual(len(encoded(full_buffer_names)), maximum)
        cases = (
            ("provenance", (b"com.apple.provenance",), None, "apple-provenance"),
            ("quarantine", (b"com.apple.quarantine",), None, "apple-quarantine"),
            (
                "provenance-prefix-near-miss",
                (b"com.apple.provenance.private-canary",),
                None,
                "apple-other",
            ),
            (
                "quarantine-prefix-near-miss",
                (b"com.apple.quarantine.extra",),
                None,
                "apple-other",
            ),
            (
                "compression-name-near-miss",
                (b"com.apple.decmpfs.extra",),
                None,
                "apple-other",
            ),
            (
                "resource-fork-name-near-miss",
                (b"com.apple.ResourceFork.extra",),
                None,
                "apple-other",
            ),
            (
                "finder-info-name-near-miss",
                (b"com.apple.FinderInfo.extra",),
                None,
                "apple-other",
            ),
            (
                "metadata-single",
                (b"com.apple.metadata:where",),
                None,
                "apple-metadata",
            ),
            (
                "metadata-one-byte-suffix",
                (b"com.apple.metadata:x",),
                None,
                "apple-metadata",
            ),
            (
                "metadata-multiple",
                (b"com.apple.metadata:first", b"com.apple.metadata:second"),
                (b"com.apple.metadata:second", b"com.apple.metadata:first"),
                "apple-metadata",
            ),
            (
                "metadata-empty-suffix",
                (b"com.apple.metadata:",),
                None,
                "apple-other",
            ),
            (
                "metadata-prefix-near-miss",
                (b"com.apple.metadatax:private-canary",),
                None,
                "apple-other",
            ),
            (
                "apple-other-multiple",
                (b"com.apple.first", b"com.apple.second"),
                None,
                "apple-other",
            ),
            (
                "provenance-and-quarantine",
                (b"com.apple.provenance", b"com.apple.quarantine"),
                None,
                "apple-mixed",
            ),
            (
                "metadata-and-quarantine",
                (b"com.apple.metadata:where", b"com.apple.quarantine"),
                None,
                "apple-mixed",
            ),
            (
                "known-and-apple-other",
                (b"com.apple.ResourceFork", b"com.apple.private-canary"),
                None,
                "apple-mixed",
            ),
            (
                "hidden-compression-and-quarantine",
                (b"com.apple.quarantine",),
                (b"com.apple.quarantine", b"com.apple.decmpfs"),
                "apple-mixed",
            ),
            (
                "nonapple-only",
                (b"org.example.first", b"com.applex.second"),
                None,
                "nonapple-other",
            ),
            ("one-byte-name", (b"x",), None, "nonapple-other"),
            (
                "full-buffer-nonapple",
                full_buffer_names,
                None,
                "nonapple-other",
            ),
            (
                "apple-and-nonapple",
                (b"com.apple.quarantine", b"org.example.private-canary"),
                None,
                "mixed-other",
            ),
        )
        observed = []
        for label, normal, shown, expected in cases:
            fake = StableList(normal, shown)
            observed.append((label, self.helper._file_xattr_family(fake, 17)))
            self.assertEqual(
                fake.calls,
                [
                    (17, maximum, 0),
                    (17, maximum, show),
                    (17, maximum, 0),
                    (17, maximum, show),
                ],
            )
        self.assertEqual(
            observed,
            [(label, expected) for label, _normal, _shown, expected in cases],
        )
        self.assertNotIn("private", repr(observed))

    def test_file_xattr_observer_is_bounded_stable_and_name_only(self) -> None:
        native_helper = load_helper()
        maximum = getattr(
            native_helper,
            "MAX_HOME_LIBRARY_XATTR_LIST_BYTES",
            None,
        )
        self.assertIs(type(maximum), int)
        assert isinstance(maximum, int)
        self.assertEqual(maximum, 4096)
        show = native_helper.XATTR_SHOWCOMPRESSION
        self.assertEqual(show, 0x00000020)

        def names(*values: bytes) -> bytes:
            return b"" if not values else b"\0".join(values) + b"\0"

        def stable(raw: bytes) -> tuple[bytes, bytes]:
            return raw, raw

        class FListXattr:
            argtypes = None
            restype = None

            def __init__(
                self,
                normal: tuple[bytes, bytes],
                shown: tuple[bytes, bytes],
                *,
                erange: bool = False,
                error: int | None = None,
                oversized: bool = False,
                transition: tuple[bytes, bytes] | None = None,
            ) -> None:
                self.snapshots = {0: normal, show: shown}
                self.observations = {0: 0, show: 0}
                self.erange = erange
                self.erange_emitted = False
                self.error = error
                self.oversized = oversized
                self.transition = transition
                self.calls: list[tuple[int, bool, int, int]] = []

            def __call__(
                self,
                descriptor: int,
                buffer: object,
                size: int,
                options: int,
            ) -> int:
                self.calls.append((descriptor, buffer is None, size, options))
                if buffer is None:
                    return len(self.snapshots[options][0])
                index = self.observations[options]
                self.observations[options] += 1
                raw = (
                    self.transition[len(self.calls) > 2]
                    if self.transition is not None
                    else self.snapshots[options][index]
                )
                if self.error is not None:
                    ctypes.set_errno(self.error)
                    return -1
                if self.erange and not self.erange_emitted:
                    self.erange_emitted = True
                    ctypes.set_errno(errno.ERANGE)
                    return -1
                if self.oversized or size < len(raw):
                    ctypes.set_errno(errno.ERANGE)
                    return -1
                ctypes.memmove(buffer, raw, len(raw))
                return len(raw)

        expected_file_calls = [
            (17, False, maximum, 0),
            (17, False, maximum, show),
            (17, False, maximum, 0),
            (17, False, maximum, show),
        ]
        stable_cases = (
            (
                "compression",
                stable(b""),
                stable(names(b"com.apple.decmpfs")),
                "compression",
            ),
            (
                "visible-compression",
                stable(names(b"com.apple.decmpfs")),
                stable(names(b"com.apple.decmpfs")),
                "compression",
            ),
            (
                "shown-only-resource-fork",
                stable(b""),
                stable(names(b"com.apple.ResourceFork")),
                "unclassified",
            ),
            (
                "shown-only-nonapple",
                stable(b""),
                stable(names(b"org.example.other")),
                "unclassified",
            ),
            (
                "resource-fork",
                stable(names(b"com.apple.ResourceFork")),
                stable(names(b"com.apple.ResourceFork")),
                "resource-fork",
            ),
            (
                "finder-info",
                stable(names(b"com.apple.FinderInfo")),
                stable(names(b"com.apple.FinderInfo")),
                "finder-info",
            ),
            (
                "provenance",
                stable(names(b"com.apple.provenance")),
                stable(names(b"com.apple.provenance")),
                "apple-provenance",
            ),
            (
                "metadata-single",
                stable(names(b"com.apple.metadata:where")),
                stable(names(b"com.apple.metadata:where")),
                "apple-metadata",
            ),
            (
                "metadata-multiple",
                stable(
                    names(
                        b"com.apple.metadata:first",
                        b"com.apple.metadata:second",
                    )
                ),
                stable(
                    names(
                        b"com.apple.metadata:second",
                        b"com.apple.metadata:first",
                    )
                ),
                "apple-metadata",
            ),
            (
                "apple-category-mix",
                stable(names(b"com.apple.quarantine", b"com.apple.metadata:where")),
                stable(names(b"com.apple.quarantine", b"com.apple.metadata:where")),
                "apple-mixed",
            ),
            (
                "order-normalized",
                (
                    names(b"com.apple.first", b"com.apple.second"),
                    names(b"com.apple.second", b"com.apple.first"),
                ),
                (
                    names(b"com.apple.second", b"com.apple.first"),
                    names(b"com.apple.first", b"com.apple.second"),
                ),
                "apple-other",
            ),
            (
                "singleton-quarantine",
                stable(names(b"com.apple.quarantine")),
                stable(names(b"com.apple.quarantine")),
                "apple-quarantine",
            ),
            (
                "apple-prefix-boundary",
                stable(names(b"com.applex.quarantine")),
                stable(names(b"com.applex.quarantine")),
                "nonapple-other",
            ),
            (
                "nonapple-other",
                stable(names(b"com.example.first", b"org.example.second")),
                stable(names(b"com.example.first", b"org.example.second")),
                "nonapple-other",
            ),
            (
                "maximum-name",
                stable(names(b"x" * 127)),
                stable(names(b"x" * 127)),
                "nonapple-other",
            ),
            (
                "overlong-name",
                stable(names(b"x" * 128)),
                stable(names(b"x" * 128)),
                "unclassified",
            ),
            (
                "mixed-other",
                stable(names(b"com.apple.ResourceFork", b"private-other-canary")),
                stable(names(b"com.apple.ResourceFork", b"private-other-canary")),
                "mixed-other",
            ),
            (
                "known-plus-apple-other",
                stable(names(b"com.apple.ResourceFork", b"com.apple.quarantine")),
                stable(names(b"com.apple.ResourceFork", b"com.apple.quarantine")),
                "apple-mixed",
            ),
            (
                "compression-plus-apple-other",
                stable(names(b"com.apple.quarantine")),
                stable(names(b"com.apple.quarantine", b"com.apple.decmpfs")),
                "apple-mixed",
            ),
            (
                "shown-added-noncompression",
                stable(names(b"com.apple.quarantine")),
                stable(names(b"com.apple.quarantine", b"org.example.other")),
                "unclassified",
            ),
            (
                "two-known",
                stable(names(b"com.apple.ResourceFork", b"com.apple.FinderInfo")),
                stable(names(b"com.apple.ResourceFork", b"com.apple.FinderInfo")),
                "apple-mixed",
            ),
            (
                "apple-and-nonapple",
                stable(names(b"com.apple.quarantine", b"org.example.other")),
                stable(names(b"com.apple.quarantine", b"org.example.other")),
                "mixed-other",
            ),
            (
                "malformed",
                stable(b"private-malformed-canary"),
                stable(b"private-malformed-canary"),
                "unclassified",
            ),
            (
                "empty-component",
                stable(names(b"private-empty-canary", b"")),
                stable(names(b"private-empty-canary", b"")),
                "unclassified",
            ),
            (
                "leading-empty-component",
                stable(names(b"", b"private-leading-empty-canary")),
                stable(names(b"", b"private-leading-empty-canary")),
                "unclassified",
            ),
            (
                "singleton-empty-component",
                stable(names(b"")),
                stable(names(b"")),
                "unclassified",
            ),
            (
                "duplicate",
                stable(names(b"private-duplicate-canary", b"private-duplicate-canary")),
                stable(names(b"private-duplicate-canary", b"private-duplicate-canary")),
                "unclassified",
            ),
            (
                "invalid-utf8",
                stable(names(b"\xff")),
                stable(names(b"\xff")),
                "unclassified",
            ),
            (
                "inconsistent",
                stable(names(b"private-normal-canary")),
                stable(names(b"private-shown-canary")),
                "unclassified",
            ),
            (
                "normal-not-visible-with-show",
                stable(
                    names(
                        b"com.apple.quarantine",
                        b"private-normal-only-canary",
                    )
                ),
                stable(names(b"com.apple.quarantine")),
                "unclassified",
            ),
            (
                "unstable-normal",
                (
                    names(b"private-before-canary"),
                    names(b"private-after-canary"),
                ),
                stable(names(b"private-before-canary")),
                "unstable",
            ),
            (
                "unstable-shown",
                stable(names(b"private-before-canary")),
                (
                    names(b"private-before-canary"),
                    names(b"private-after-canary"),
                ),
                "unstable",
            ),
        )

        for label, normal, shown, family in stable_cases:
            fake = FListXattr(normal, shown)
            with (
                self.subTest(label=label),
                mock.patch.object(
                    native_helper.ctypes,
                    "CDLL",
                    return_value=SimpleNamespace(flistxattr=fake),
                ),
            ):
                observed = native_helper._home_library_file_xattr_state(
                    17,
                    diagnostic_phase="journal-inventory",
                    diagnostic_path_family="direct",
                )
            self.assertEqual(
                observed,
                (
                    "apple-quarantine-unreadable"
                    if family == "apple-quarantine"
                    else family
                ),
            )
            self.assertNotIn("private", repr(observed))
            self.assertEqual(fake.calls, expected_file_calls)

        for phase, path_family in (
            ("journal-inventory", "preferences"),
            ("source-revalidation", "caches"),
            ("quarantine-revalidation", "application-support"),
            ("delete-boundary", "containers"),
            ("journal-inventory", "group-containers"),
            ("source-revalidation", "metadata"),
            ("quarantine-revalidation", "nested-other"),
        ):
            fake = FListXattr(
                stable(names(b"com.apple.quarantine")),
                stable(names(b"com.apple.quarantine")),
            )
            with (
                self.subTest(phase=phase, path_family=path_family),
                mock.patch.object(
                    native_helper.ctypes,
                    "CDLL",
                    return_value=SimpleNamespace(flistxattr=fake),
                ),
            ):
                observed = native_helper._home_library_file_xattr_state(
                    17,
                    diagnostic_phase=phase,
                    diagnostic_path_family=path_family,
                )
            self.assertEqual(observed, "apple-quarantine-unreadable")
            self.assertEqual(fake.calls, expected_file_calls)

        precedence_cases = (
            (
                "overflow-before-unclassified",
                FListXattr(
                    stable(b"private-malformed-canary"),
                    stable(b"private-malformed-canary"),
                    erange=True,
                ),
                "overflow",
            ),
            (
                "unclassified-before-unstable",
                FListXattr(
                    (
                        b"private-malformed-canary",
                        names(b"private-normal-after-canary"),
                    ),
                    stable(names(b"private-shown-canary")),
                ),
                "unclassified",
            ),
            (
                "unstable-before-inconsistent",
                FListXattr(
                    (
                        names(b"private-normal-before-canary"),
                        names(b"private-normal-after-canary"),
                    ),
                    stable(names(b"private-shown-canary")),
                ),
                "unstable",
            ),
        )
        for label, fake, family in precedence_cases:
            with (
                self.subTest(precedence=label),
                mock.patch.object(
                    native_helper.ctypes,
                    "CDLL",
                    return_value=SimpleNamespace(flistxattr=fake),
                ),
            ):
                observed = native_helper._home_library_file_xattr_state(
                    17,
                    diagnostic_phase="journal-inventory",
                    diagnostic_path_family="direct",
                )
            self.assertEqual(observed, family)
            self.assertNotIn("private", repr(observed))
            self.assertEqual(fake.calls, expected_file_calls)

        empty = FListXattr((b"", b""), (b"", b""))
        with mock.patch.object(
            native_helper.ctypes,
            "CDLL",
            return_value=SimpleNamespace(flistxattr=empty),
        ):
            self.assertIsNone(
                native_helper._home_library_file_xattr_state(
                    17,
                    diagnostic_phase="journal-inventory",
                    diagnostic_path_family="direct",
                )
            )
        self.assertEqual(empty.calls, expected_file_calls)

        transition = FListXattr(
            stable(b""),
            stable(b""),
            transition=(b"", names(b"private-transition-canary")),
        )
        with (
            mock.patch.object(
                native_helper.ctypes,
                "CDLL",
                return_value=SimpleNamespace(flistxattr=transition),
            ),
        ):
            observed = native_helper._home_library_file_xattr_state(
                17,
                diagnostic_phase="journal-inventory",
                diagnostic_path_family="direct",
            )
        self.assertEqual(observed, "unstable")
        self.assertEqual(transition.calls, expected_file_calls)

        visible = names(b"private-overflow-canary")
        over_capacity = b"x" * maximum + b"\0"
        for label, fake in (
            (
                "size-overflow",
                FListXattr(
                    stable(over_capacity),
                    stable(over_capacity),
                ),
            ),
            (
                "erange",
                FListXattr(
                    (visible, visible),
                    (visible, visible),
                    erange=True,
                ),
            ),
        ):
            with (
                self.subTest(label=label),
                mock.patch.object(
                    native_helper.ctypes,
                    "CDLL",
                    return_value=SimpleNamespace(flistxattr=fake),
                ),
            ):
                observed = native_helper._home_library_file_xattr_state(
                    17,
                    diagnostic_phase="delete-boundary",
                    diagnostic_path_family="nested-other",
                )
            self.assertEqual(observed, "overflow")
            self.assertEqual(fake.calls, expected_file_calls)

        native_error = FListXattr(
            stable(visible),
            stable(visible),
            error=errno.EIO,
        )
        with (
            mock.patch.object(
                native_helper.ctypes,
                "CDLL",
                return_value=SimpleNamespace(flistxattr=native_error),
            ),
            self.assertRaises(native_helper.ProbeError) as raised,
        ):
            native_helper._home_library_file_xattr_state(
                17,
                diagnostic_phase="delete-boundary",
                diagnostic_path_family="nested-other",
            )
        self.assertEqual(raised.exception.code, "home-library-observation-failed")
        self.assertIsNone(raised.exception.secondary_code)

        for cause in ("root-xattr", "directory-xattr"):
            raw = names(b"private-legacy-canary")
            fake = FListXattr((raw, raw), (raw, raw))
            with (
                self.subTest(legacy=cause),
                mock.patch.object(
                    native_helper.ctypes,
                    "CDLL",
                    return_value=SimpleNamespace(flistxattr=fake),
                ),
                self.assertRaises(native_helper.ProbeError) as raised,
            ):
                native_helper._require_no_extended_attributes(17, cause)
            self.assertEqual(
                raised.exception.secondary_code,
                f"home-library-unsafe-entry-{cause}",
            )

        invalid_context_cases = (
            (
                "empty-missing",
                {},
                FListXattr(stable(b""), stable(b"")),
            ),
            (
                "native-error-missing",
                {},
                FListXattr(stable(b""), stable(b""), error=errno.EIO),
            ),
            (
                "missing-phase",
                {"diagnostic_path_family": "direct"},
                FListXattr(stable(b""), stable(b"")),
            ),
            (
                "missing-path-family",
                {"diagnostic_phase": "journal-inventory"},
                FListXattr(stable(b""), stable(b"")),
            ),
            (
                "invalid-phase",
                {
                    "diagnostic_phase": "private-phase-canary",
                    "diagnostic_path_family": "direct",
                },
                FListXattr(stable(b""), stable(b"")),
            ),
            (
                "invalid-path-family",
                {
                    "diagnostic_phase": "journal-inventory",
                    "diagnostic_path_family": "private-path-canary",
                },
                FListXattr(stable(b""), stable(b"")),
            ),
        )
        for label, context, fake in invalid_context_cases:
            with (
                self.subTest(invalid_context=label),
                mock.patch.object(
                    native_helper.ctypes,
                    "CDLL",
                    return_value=SimpleNamespace(flistxattr=fake),
                ),
                self.assertRaises(native_helper.ProbeError) as raised,
            ):
                native_helper._home_library_file_xattr_state(
                    17,
                    diagnostic_phase=context.get("diagnostic_phase"),
                    diagnostic_path_family=context.get("diagnostic_path_family"),
                )
            self.assertEqual(raised.exception.code, "home-library-unsafe-entry")
            self.assertEqual(
                raised.exception.secondary_code,
                "home-library-diagnostic-invalid",
            )
            self.assertEqual(fake.calls, [])

        raw = names(b"private-invalid-context-canary")
        invalid_context = FListXattr((raw, raw), (raw, raw))
        with (
            mock.patch.object(
                native_helper.ctypes,
                "CDLL",
                return_value=SimpleNamespace(flistxattr=invalid_context),
            ),
            self.assertRaises(native_helper.ProbeError) as raised,
        ):
            native_helper._home_library_file_xattr_state(
                17,
                diagnostic_phase=None,
                diagnostic_path_family=None,
            )
        self.assertEqual(raised.exception.code, "home-library-unsafe-entry")
        self.assertEqual(
            raised.exception.secondary_code,
            "home-library-diagnostic-invalid",
        )
        self.assertNotIn("private", raised.exception.secondary_code)

    def test_quarantine_value_observer_is_descriptor_bound_bounded_and_private(
        self,
    ) -> None:
        native_helper = load_helper()
        maximum = getattr(
            native_helper,
            "MAX_HOME_LIBRARY_QUARANTINE_VALUE_BYTES",
            None,
        )
        self.assertEqual(maximum, 4096)
        evidence_type = getattr(
            native_helper,
            "_HomeLibraryQuarantineEvidence",
            None,
        )
        self.assertIsNotNone(evidence_type)
        assert evidence_type is not None
        self.assertEqual(
            evidence_type._fields,
            ("value_length", "value_sha256"),
        )
        domain = getattr(
            native_helper,
            "_HOME_LIBRARY_QUARANTINE_VALUE_DIGEST_DOMAIN",
            None,
        )
        self.assertEqual(
            domain,
            b"task-witness-macos-home-library-quarantine-value-v1\0",
        )
        show = native_helper.XATTR_SHOWCOMPRESSION
        quarantine_name = b"com.apple.quarantine"
        quarantine_list = quarantine_name + b"\0"
        native_error = object()

        class RawNativeResult:
            def __init__(self, value: object) -> None:
                self.value = value

        class RaisedNativeResult:
            def __init__(self, error: BaseException) -> None:
                self.error = error

        class Native:
            def __init__(
                self,
                list_results: list[object],
                value_results: list[object],
            ) -> None:
                self.list_results = list(list_results)
                self.value_results = list(value_results)
                self.events: list[tuple[object, ...]] = []
                owner = self

                class FListXattr:
                    argtypes = None
                    restype = None

                    def __call__(
                        self,
                        descriptor: int,
                        buffer: object,
                        size: int,
                        options: int,
                    ) -> int:
                        owner.events.append(
                            ("list", descriptor, buffer is None, size, options)
                        )
                        result = owner.list_results.pop(0)
                        if isinstance(result, tuple):
                            ctypes.set_errno(result[1])
                            return -1
                        if isinstance(result, RaisedNativeResult):
                            raise result.error
                        assert isinstance(result, bytes)
                        if len(result) <= size:
                            ctypes.memmove(buffer, result, len(result))
                        return len(result)

                class FGetXattr:
                    argtypes = None
                    restype = None

                    def __call__(
                        self,
                        descriptor: int,
                        name: object,
                        buffer: object,
                        size: int,
                        position: int,
                        options: int,
                    ) -> object:
                        raw_name = (
                            name
                            if isinstance(name, bytes)
                            else ctypes.cast(name, ctypes.c_char_p).value
                        )
                        owner.events.append(
                            (
                                "get",
                                descriptor,
                                raw_name,
                                buffer is None,
                                size,
                                position,
                                options,
                            )
                        )
                        result = owner.value_results.pop(0)
                        if isinstance(result, tuple):
                            ctypes.set_errno(result[1])
                            return -1
                        if isinstance(result, RawNativeResult):
                            return result.value
                        if isinstance(result, RaisedNativeResult):
                            raise result.error
                        assert isinstance(result, bytes)
                        if len(result) <= size:
                            ctypes.memmove(buffer, result, len(result))
                        return len(result)

                self.flistxattr = FListXattr()
                self.fgetxattr = FGetXattr()

        def observe(native: Native):
            with mock.patch.object(
                native_helper.ctypes,
                "CDLL",
                return_value=SimpleNamespace(
                    flistxattr=native.flistxattr,
                    fgetxattr=native.fgetxattr,
                ),
            ) as load_libc:
                try:
                    return native_helper._home_library_file_xattr_state(
                        17,
                        diagnostic_phase="journal-inventory",
                        diagnostic_path_family="preferences",
                    )
                finally:
                    load_libc.assert_called_once_with(None, use_errno=True)

        for error_type in (AttributeError, TypeError, ValueError, OSError):
            error = (
                OSError(errno.EIO, "private-libc-load-canary")
                if error_type is OSError
                else error_type("private-libc-load-canary")
            )
            native = Native([], [])
            with (
                self.subTest(libc_load_error=error_type.__name__),
                mock.patch.object(
                    native_helper.ctypes,
                    "CDLL",
                    side_effect=error,
                ) as load_libc,
                self.assertRaises(native_helper.ProbeError) as raised,
            ):
                native_helper._home_library_file_xattr_state(
                    17,
                    diagnostic_phase="journal-inventory",
                    diagnostic_path_family="preferences",
                )
            self.assertEqual(
                (raised.exception.code, raised.exception.secondary_code),
                ("home-library-observation-failed", None),
            )
            self.assertNotIn("private", str(raised.exception))
            load_libc.assert_called_once_with(None, use_errno=True)
            self.assertEqual(native.events, [])
            self.assertEqual(native.list_results, [])
            self.assertEqual(native.value_results, [])

        expected_events = [
            ("list", 17, False, maximum, 0),
            ("list", 17, False, maximum, show),
            ("list", 17, False, maximum, 0),
            ("list", 17, False, maximum, show),
            (
                "get",
                17,
                quarantine_name,
                False,
                maximum,
                0,
                0,
            ),
            ("list", 17, False, maximum, 0),
            ("list", 17, False, maximum, show),
            (
                "get",
                17,
                quarantine_name,
                False,
                maximum,
                0,
                0,
            ),
            ("list", 17, False, maximum, 0),
            ("list", 17, False, maximum, show),
        ]
        stable_values = (
            ("empty", b""),
            ("one-byte", b"\0"),
            ("privacy", b"private-quarantine-value-canary"),
            ("opaque-binary", bytes(range(256))),
            ("at-cap", bytes(range(256)) * 16),
        )
        for label, value in stable_values:
            native = Native([quarantine_list] * 8, [value, value])
            with self.subTest(stable=label):
                evidence = observe(native)
            self.assertEqual(
                evidence,
                evidence_type(
                    len(value),
                    hashlib.sha256(
                        domain + len(value).to_bytes(4, "big") + value
                    ).hexdigest(),
                ),
            )
            self.assertEqual(native.events, expected_events)
            self.assertEqual(native.list_results, [])
            self.assertEqual(native.value_results, [])
            self.assertNotIn("private", repr(evidence))
            self.assertEqual(
                native.flistxattr.argtypes,
                [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int],
            )
            self.assertIs(native.flistxattr.restype, ctypes.c_ssize_t)
            self.assertEqual(
                native.fgetxattr.argtypes,
                [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_uint32,
                    ctypes.c_int,
                ],
            )
            self.assertIs(native.fgetxattr.restype, ctypes.c_ssize_t)

        no_value_cases = (
            ("empty", b"", None),
            ("other", b"com.apple.provenance\0", "apple-provenance"),
            (
                "apple-mixed",
                b"com.apple.quarantine\0com.apple.metadata:item\0",
                "apple-mixed",
            ),
            (
                "near-miss",
                b"com.apple.quarantine.private-canary\0",
                "apple-other",
            ),
            (
                "nonapple-mix",
                b"com.apple.quarantine\0org.example.private-canary\0",
                "mixed-other",
            ),
        )
        for label, raw_names, family in no_value_cases:
            native = Native([raw_names] * 4, [])
            with self.subTest(no_value=label):
                if family is None:
                    self.assertIsNone(observe(native))
                else:
                    self.assertEqual(observe(native), family)
            self.assertEqual(
                native.events,
                [
                    ("list", 17, False, maximum, 0),
                    ("list", 17, False, maximum, show),
                    ("list", 17, False, maximum, 0),
                    ("list", 17, False, maximum, show),
                ],
            )

        class FailingXattrBinding:
            def __init__(
                self,
                attribute: str,
                error: BaseException,
            ) -> None:
                self.attribute = attribute
                self.error = error
                self.calls = 0
                self._argtypes: object = None
                self._restype: object = None

            @property
            def argtypes(self) -> object:
                return self._argtypes

            @argtypes.setter
            def argtypes(self, value: object) -> None:
                if self.attribute == "argtypes":
                    raise self.error
                self._argtypes = value

            @property
            def restype(self) -> object:
                return self._restype

            @restype.setter
            def restype(self, value: object) -> None:
                if self.attribute == "restype":
                    raise self.error
                self._restype = value

            def __call__(self, *_args: object) -> int:
                self.calls += 1
                raise AssertionError("native xattr call must not be reached")

        for error_type in (AttributeError, TypeError, ValueError, OSError):
            for attribute in ("argtypes", "restype"):
                error = (
                    OSError(errno.EIO, "private-binding-error-canary")
                    if error_type is OSError
                    else error_type("private-binding-error-canary")
                )
                native = Native([], [])
                failing_binding = FailingXattrBinding(attribute, error)
                native.flistxattr = failing_binding
                with (
                    self.subTest(
                        flistxattr_binding_error=error_type.__name__,
                        attribute=attribute,
                    ),
                    self.assertRaises(native_helper.ProbeError) as raised,
                ):
                    observe(native)
                self.assertEqual(
                    (raised.exception.code, raised.exception.secondary_code),
                    ("home-library-observation-failed", None),
                )
                self.assertNotIn("private", str(raised.exception))
                self.assertEqual(native.events, [])
                self.assertEqual(native.list_results, [])
                self.assertEqual(native.value_results, [])
                self.assertEqual(failing_binding.calls, 0)

        for error_type in (TypeError, ValueError):
            for attribute in ("argtypes", "restype"):
                native = Native([quarantine_list] * 4, [])
                failing_binding = FailingXattrBinding(
                    attribute,
                    error_type("private-binding-error-canary"),
                )
                native.fgetxattr = failing_binding
                with self.subTest(
                    fgetxattr_binding_error=error_type.__name__,
                    attribute=attribute,
                ):
                    result = observe(native)
                self.assertEqual(result, "apple-quarantine-unreadable")
                self.assertNotIn("private", result)
                self.assertEqual(native.events, expected_events[:4])
                self.assertEqual(native.list_results, [])
                self.assertEqual(native.value_results, [])
                self.assertEqual(failing_binding.calls, 0)

        erange = (native_error, errno.ERANGE)
        disappearance_errors = tuple(
            (
                error_name.lower(),
                (native_error, getattr(errno, error_name)),
            )
            for error_name in ("ENOATTR", "ENODATA")
            if hasattr(errno, error_name)
        )
        self.assertTrue(disappearance_errors)
        enoattr = disappearance_errors[0][1]
        eio = (native_error, errno.EIO)
        malformed = b"private-malformed-name-canary"
        precedence_cases = (
            (
                "name-overflow-before-value-unreadable",
                [
                    quarantine_list,
                    quarantine_list,
                    quarantine_list,
                    quarantine_list,
                    erange,
                    quarantine_list,
                    quarantine_list,
                    quarantine_list,
                ],
                [eio, eio],
                "overflow",
            ),
            (
                "malformed-before-value-overflow",
                [quarantine_list] * 4
                + [malformed, quarantine_list, quarantine_list, quarantine_list],
                [erange, erange],
                "overflow",
            ),
            (
                "malformed-post-entry",
                [quarantine_list] * 4
                + [malformed, quarantine_list, quarantine_list, quarantine_list],
                [b"same", b"same"],
                "apple-quarantine-unreadable",
            ),
            (
                "name-transition",
                [quarantine_list] * 4 + [b"", b"", quarantine_list, quarantine_list],
                [b"same", b"same"],
                "unstable",
            ),
            (
                "value-erange",
                [quarantine_list] * 8,
                [erange, b"same"],
                "overflow",
            ),
            (
                "positive-over-cap-before-unequal",
                [quarantine_list] * 8,
                [b"x" * (maximum + 1), b"different"],
                "overflow",
            ),
            (
                "unreadable-before-enoattr-instability",
                [quarantine_list] * 8,
                [enoattr, eio],
                "apple-quarantine-unreadable",
            ),
            (
                "enoattr-instability",
                [quarantine_list] * 8,
                [enoattr, enoattr],
                "unstable",
            ),
            (
                "unequal-values",
                [quarantine_list] * 8,
                [b"before", b"AFTER!"],
                "unstable",
            ),
            (
                "native-unreadable",
                [quarantine_list] * 8,
                [eio, eio],
                "apple-quarantine-unreadable",
            ),
            (
                "eintr-is-not-retried",
                [quarantine_list] * 8,
                [(native_error, errno.EINTR), b"same"],
                "apple-quarantine-unreadable",
            ),
        )
        for label, list_results, value_results, family in precedence_cases:
            native = Native(list_results, value_results)
            with self.subTest(precedence=label):
                self.assertEqual(observe(native), family)
            self.assertEqual(native.events, expected_events)
            self.assertEqual(native.list_results, [])
            self.assertEqual(native.value_results, [])

        for list_index in range(4):
            list_results = [quarantine_list] * 4
            list_results[list_index] = erange
            native = Native(list_results, [])
            with self.subTest(preflight_overflow_position=list_index):
                result = observe(native)
            self.assertEqual(result, "overflow")
            self.assertNotIn("private", result)
            self.assertEqual(native.events, expected_events[:4])
            self.assertEqual(native.list_results, [])
            self.assertEqual(native.value_results, [])

        for list_index in range(4):
            list_results = [quarantine_list] * 4
            list_results[list_index] = malformed
            native = Native(list_results, [])
            with self.subTest(preflight_malformed_position=list_index):
                result = observe(native)
            self.assertEqual(result, "unclassified")
            self.assertNotIn("private", result)
            self.assertEqual(native.events, expected_events[:4])
            self.assertEqual(native.list_results, [])
            self.assertEqual(native.value_results, [])

        for topology_label, topology in (
            ("absent", b""),
            ("different-singleton", b"com.apple.provenance\0"),
            (
                "added-name",
                b"com.apple.quarantine\0com.apple.provenance\0",
            ),
        ):
            for list_index in range(4):
                list_results = [quarantine_list] * 4
                list_results[list_index] = topology
                native = Native(list_results, [])
                with self.subTest(
                    preflight_topology=topology_label,
                    list_index=list_index,
                ):
                    result = observe(native)
                self.assertEqual(result, "unstable")
                self.assertNotIn("private", result)
                self.assertEqual(native.events, expected_events[:4])
                self.assertEqual(native.list_results, [])
                self.assertEqual(native.value_results, [])

        for list_index in range(4):
            list_results = [quarantine_list] * 4
            list_results[list_index] = eio
            native = Native(list_results, [])
            with (
                self.subTest(preflight_eio_position=list_index),
                self.assertRaises(native_helper.ProbeError) as raised,
            ):
                observe(native)
            self.assertEqual(
                (raised.exception.code, raised.exception.secondary_code),
                ("home-library-observation-failed", None),
            )
            self.assertNotIn("private", str(raised.exception))
            self.assertEqual(native.events, expected_events[: list_index + 1])
            self.assertEqual(len(native.list_results), 3 - list_index)
            self.assertEqual(native.value_results, [])

        for label, list_index, value_index in (
            ("g1", None, 0),
            ("l1-normal", 4, None),
            ("l1-show", 5, None),
            ("g2", None, 1),
            ("l2-normal", 6, None),
            ("l2-show", 7, None),
        ):
            list_results = [quarantine_list] * 8
            value_results = [b"same", b"same"]
            if list_index is not None:
                list_results[list_index] = erange
            if value_index is not None:
                value_results[value_index] = erange
            native = Native(list_results, value_results)
            with self.subTest(single_overflow_position=label):
                self.assertEqual(observe(native), "overflow")
            self.assertEqual(native.events, expected_events)
            self.assertEqual(native.list_results, [])
            self.assertEqual(native.value_results, [])

        for failure_label, failure, family in (
            ("malformed", malformed, "apple-quarantine-unreadable"),
            ("eio", eio, "apple-quarantine-unreadable"),
            ("topology-absent", b"", "unstable"),
            ("topology-different-singleton", b"com.apple.provenance\0", "unstable"),
            (
                "topology-added-name",
                b"com.apple.quarantine\0com.apple.provenance\0",
                "unstable",
            ),
        ):
            for list_index in range(4, 8):
                list_results = [quarantine_list] * 8
                list_results[list_index] = failure
                native = Native(list_results, [b"same", b"same"])
                with self.subTest(
                    post_list_failure=failure_label,
                    list_index=list_index,
                ):
                    self.assertEqual(observe(native), family)
                self.assertEqual(native.events, expected_events)
                self.assertEqual(native.list_results, [])
                self.assertEqual(native.value_results, [])

        value_failure_cases = tuple(
            (label, failure, "unstable") for label, failure in disappearance_errors
        ) + (("eio", eio, "apple-quarantine-unreadable"),)
        for failure_label, failure, family in value_failure_cases:
            for value_index in range(2):
                value_results = [b"same", b"same"]
                value_results[value_index] = failure
                native = Native([quarantine_list] * 8, value_results)
                with self.subTest(
                    post_value_failure=failure_label,
                    value_index=value_index,
                ):
                    self.assertEqual(observe(native), family)
                self.assertEqual(native.events, expected_events)
                self.assertEqual(native.list_results, [])
                self.assertEqual(native.value_results, [])

        for result in (True, 1.0):
            for value_index in range(2):
                value_results: list[object] = [b"same", b"same"]
                value_results[value_index] = RawNativeResult(result)
                native = Native([quarantine_list] * 8, value_results)
                with self.subTest(
                    malformed_native_result=type(result).__name__,
                    value_index=value_index,
                ):
                    self.assertEqual(observe(native), "apple-quarantine-unreadable")
                self.assertEqual(native.events, expected_events)
                self.assertEqual(native.list_results, [])
                self.assertEqual(native.value_results, [])

        for error_type in (AttributeError, TypeError, ValueError, OSError):
            for value_index in range(2):
                error = (
                    OSError(errno.EIO, "private-native-error-canary")
                    if error_type is OSError
                    else error_type("private-native-error-canary")
                )
                value_results = [b"same", b"same"]
                value_results[value_index] = RaisedNativeResult(error)
                native = Native([quarantine_list] * 8, value_results)
                with self.subTest(
                    raised_native_error=error_type.__name__,
                    value_index=value_index,
                ):
                    result = observe(native)
                self.assertEqual(result, "apple-quarantine-unreadable")
                self.assertNotIn("private", result)
                self.assertEqual(native.events, expected_events)
                self.assertEqual(native.list_results, [])
                self.assertEqual(native.value_results, [])

        for error_type in (AttributeError, TypeError, ValueError, OSError):
            for list_index in range(4, 8):
                error = (
                    OSError(errno.EIO, "private-native-error-canary")
                    if error_type is OSError
                    else error_type("private-native-error-canary")
                )
                list_results = [quarantine_list] * 8
                list_results[list_index] = RaisedNativeResult(error)
                native = Native(list_results, [b"same", b"same"])
                with self.subTest(
                    raised_native_list_error=error_type.__name__,
                    list_index=list_index,
                ):
                    result = observe(native)
                self.assertEqual(result, "apple-quarantine-unreadable")
                self.assertNotIn("private", result)
                self.assertEqual(native.events, expected_events)
                self.assertEqual(native.list_results, [])
                self.assertEqual(native.value_results, [])

        for error_type in (AttributeError, TypeError, ValueError, OSError):
            for list_index in range(4):
                error = (
                    OSError(errno.EIO, "private-native-error-canary")
                    if error_type is OSError
                    else error_type("private-native-error-canary")
                )
                list_results = [quarantine_list] * 4
                list_results[list_index] = RaisedNativeResult(error)
                native = Native(list_results, [])
                with (
                    self.subTest(
                        raised_preflight_list_error=error_type.__name__,
                        list_index=list_index,
                    ),
                    self.assertRaises(native_helper.ProbeError) as raised,
                ):
                    observe(native)
                self.assertEqual(
                    (raised.exception.code, raised.exception.secondary_code),
                    ("home-library-observation-failed", None),
                )
                self.assertNotIn("private", str(raised.exception))
                self.assertEqual(native.events, expected_events[: list_index + 1])
                self.assertEqual(len(native.list_results), 3 - list_index)
                self.assertEqual(native.value_results, [])

    def test_quarantine_value_evidence_brackets_reads_without_authority(self) -> None:
        evidence_type = getattr(
            self.helper,
            "_HomeLibraryQuarantineEvidence",
            None,
        )
        self.assertIsNotNone(evidence_type)
        assert evidence_type is not None
        first = evidence_type(7, "1" * 64)
        changed = evidence_type(7, "2" * 64)
        phases = tuple(self.helper._XATTR_PHASES)
        path_families = tuple(self.helper._XATTR_PATH_FAMILIES)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "payload.bin"
            payload = b"private-quarantine-content-canary"
            path.write_bytes(payload)
            parent_descriptor = os.open(
                root,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY,
            )
            original_read = os.read
            try:
                for family in tuple(self.helper._XATTR_FAMILIES):
                    secondary = (
                        "home-library-unsafe-entry-file-xattr-"
                        f"journal-inventory-preferences-{family}"
                    )
                    with (
                        self.subTest(pre_read_family=family),
                        mock.patch.object(
                            self.helper,
                            "_home_library_file_xattr_state",
                            return_value=family,
                        ) as observer,
                        mock.patch.object(
                            self.helper.os,
                            "read",
                            side_effect=AssertionError("private-content-read-canary"),
                        ) as read,
                        self.assertRaises(self.helper.ProbeError) as raised,
                    ):
                        self.helper._read_home_library_file(
                            parent_descriptor,
                            path.name,
                            path.lstat(),
                            ("journal-inventory", "preferences"),
                        )
                    self.assertEqual(
                        (raised.exception.code, raised.exception.secondary_code),
                        (
                            "home-library-unsafe-entry",
                            secondary,
                        ),
                    )
                    observer.assert_called_once()
                    read.assert_not_called()
                    self.assertNotIn(payload.decode(), str(raised.exception))

                for phase in phases:
                    for path_family in path_families:
                        events: list[tuple[object, ...]] = []

                        def observe(
                            descriptor: int,
                            events: list[tuple[object, ...]] = events,
                            **context: object,
                        ):
                            events.append(
                                (
                                    "xattr",
                                    descriptor,
                                    os.fstat(descriptor).st_ino,
                                    context["diagnostic_phase"],
                                    context["diagnostic_path_family"],
                                )
                            )
                            return first

                        def read(
                            descriptor: int,
                            maximum: int,
                            events: list[tuple[object, ...]] = events,
                        ) -> bytes:
                            events.append(
                                ("read", descriptor, os.fstat(descriptor).st_ino)
                            )
                            return original_read(descriptor, maximum)

                        with (
                            self.subTest(phase=phase, path_family=path_family),
                            mock.patch.object(
                                self.helper,
                                "_home_library_file_xattr_state",
                                side_effect=observe,
                            ),
                            mock.patch.object(self.helper.os, "read", side_effect=read),
                            self.assertRaises(self.helper.ProbeError) as raised,
                        ):
                            self.helper._read_home_library_file(
                                parent_descriptor,
                                path.name,
                                path.lstat(),
                                (phase, path_family),
                            )
                        secondary = (
                            "home-library-unsafe-entry-file-xattr-"
                            f"{phase}-{path_family}-apple-quarantine-stable-bounded"
                        )
                        self.assertEqual(
                            (raised.exception.code, raised.exception.secondary_code),
                            ("home-library-unsafe-entry", secondary),
                        )
                        self.assertEqual(events[0][0], "xattr")
                        self.assertEqual(events[-1], events[0])
                        self.assertTrue(events[1:-1])
                        self.assertTrue(
                            all(event[0] == "read" for event in events[1:-1])
                        )
                        self.assertEqual(
                            {event[1] for event in events},
                            {events[0][1]},
                        )
                        self.assertEqual(
                            {event[2] for event in events},
                            {path.lstat().st_ino},
                        )
                        self.assertNotIn(payload.decode(), str(raised.exception))

                with mock.patch.object(
                    self.helper,
                    "_home_library_file_xattr_state",
                    return_value=None,
                ):
                    raw, _metadata = self.helper._read_home_library_file(
                        parent_descriptor,
                        path.name,
                        path.lstat(),
                        ("journal-inventory", "direct"),
                    )
                self.assertEqual(raw, payload)

                for before, after in (
                    (first, changed),
                    (None, first),
                    (first, None),
                ):
                    observations = iter((before, after))
                    with (
                        self.subTest(before=before, after=after),
                        mock.patch.object(
                            self.helper,
                            "_home_library_file_xattr_state",
                            side_effect=lambda *_args, observations=observations, **_kwargs: (
                                next(observations)
                            ),
                        ),
                        self.assertRaises(self.helper.ProbeError) as raised,
                    ):
                        self.helper._read_home_library_file(
                            parent_descriptor,
                            path.name,
                            path.lstat(),
                            ("delete-boundary", "nested-other"),
                        )
                    self.assertEqual(
                        raised.exception.secondary_code,
                        "home-library-unsafe-entry-file-xattr-"
                        "delete-boundary-nested-other-unstable",
                    )
                    self.assertNotIn("private", str(raised.exception))

                for family in (
                    "overflow",
                    "apple-quarantine-unreadable",
                    "unclassified",
                ):
                    for before in (first, None):
                        observations = iter((before, family))
                        with (
                            self.subTest(
                                post_read_family=family,
                                pre_read_state=type(before).__name__,
                            ),
                            mock.patch.object(
                                self.helper,
                                "_home_library_file_xattr_state",
                                side_effect=lambda *_args, observations=observations, **_kwargs: (
                                    next(observations)
                                ),
                            ) as observer,
                            self.assertRaises(self.helper.ProbeError) as raised,
                        ):
                            self.helper._read_home_library_file(
                                parent_descriptor,
                                path.name,
                                path.lstat(),
                                ("delete-boundary", "nested-other"),
                            )
                        self.assertEqual(
                            raised.exception.secondary_code,
                            "home-library-unsafe-entry-file-xattr-"
                            f"delete-boundary-nested-other-{family}",
                        )
                        self.assertEqual(observer.call_count, 2)
                        self.assertNotIn("private", str(raised.exception))

                for before in (None, first):
                    events: list[str] = []

                    def observe_then_fail(
                        *_args: object,
                        before: object = before,
                        events: list[str] = events,
                        **_kwargs: object,
                    ) -> object:
                        events.append("xattr")
                        if len(events) == 1:
                            return before
                        raise self.helper.ProbeError("home-library-observation-failed")

                    def record_read(
                        descriptor: int,
                        maximum: int,
                        events: list[str] = events,
                    ) -> bytes:
                        events.append("read")
                        return original_read(descriptor, maximum)

                    with (
                        self.subTest(
                            post_read_error="home-library-observation-failed",
                            pre_read_state=type(before).__name__,
                        ),
                        mock.patch.object(
                            self.helper,
                            "_home_library_file_xattr_state",
                            side_effect=observe_then_fail,
                        ) as observer,
                        mock.patch.object(
                            self.helper.os,
                            "read",
                            side_effect=record_read,
                        ),
                        self.assertRaises(self.helper.ProbeError) as raised,
                    ):
                        self.helper._read_home_library_file(
                            parent_descriptor,
                            path.name,
                            path.lstat(),
                            ("delete-boundary", "nested-other"),
                        )
                    self.assertEqual(
                        (
                            raised.exception.code,
                            raised.exception.secondary_code,
                        ),
                        ("home-library-observation-failed", None),
                    )
                    self.assertEqual(observer.call_count, 2)
                    self.assertEqual(events[0], "xattr")
                    self.assertEqual(events[-1], "xattr")
                    self.assertIn("read", events[1:-1])
                    self.assertNotIn(payload.decode(), str(raised.exception))

                ordinary_families = (
                    "apple-provenance",
                    "apple-metadata",
                    "apple-mixed",
                    "apple-other",
                    "compression",
                    "resource-fork",
                    "finder-info",
                    "nonapple-other",
                    "mixed-other",
                )
                for family in ordinary_families:
                    for before, expected in (
                        (first, "unstable"),
                        (None, family),
                    ):
                        observations = iter((before, family))
                        with (
                            self.subTest(
                                post_read_family=family,
                                pre_read_state=type(before).__name__,
                            ),
                            mock.patch.object(
                                self.helper,
                                "_home_library_file_xattr_state",
                                side_effect=lambda *_args, observations=observations, **_kwargs: (
                                    next(observations)
                                ),
                            ) as observer,
                            self.assertRaises(self.helper.ProbeError) as raised,
                        ):
                            self.helper._read_home_library_file(
                                parent_descriptor,
                                path.name,
                                path.lstat(),
                                ("delete-boundary", "nested-other"),
                            )
                        self.assertEqual(
                            raised.exception.secondary_code,
                            "home-library-unsafe-entry-file-xattr-"
                            f"delete-boundary-nested-other-{expected}",
                        )
                        self.assertEqual(observer.call_count, 2)
                        self.assertNotIn("private", str(raised.exception))
            finally:
                os.close(parent_descriptor)

    def test_quarantine_value_evidence_is_not_inventory_or_replay_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            payload = home / "Library" / "Preferences" / "payload.bin"
            probe.mkdir(parents=True, mode=0o700)
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"payload")
            home.chmod(0o700)
            probe.chmod(0o700)
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            inventory = self.helper._bounded_library_inventory(account)
        self.assertEqual(
            set(inventory),
            {
                "schema_version",
                "contract",
                "entry_count",
                "regular_file_bytes",
                "entries",
                "content_sha256",
            },
        )
        raw = self.helper.canonical_bytes(inventory)
        for forbidden in (
            b"xattr",
            b"quarantine",
            b"value_length",
            b"value_sha256",
        ):
            self.assertNotIn(forbidden, raw)

    def test_native_quarantine_value_is_only_bounded_diagnostic_evidence(
        self,
    ) -> None:
        if os.uname().sysname != "Darwin":
            self.skipTest("native descriptor xattr ABI is Darwin-only")
        native_helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "payload.bin"
            path.write_bytes(b"payload")
            cleared = subprocess.run(
                ["/usr/bin/xattr", "-c", str(path)],
                check=False,
                capture_output=True,
            )
            if cleared.returncode != 0:
                self.skipTest("host cannot clear a temporary file's attributes")
            self.write_extended_attribute(path, "com.apple.quarantine")
            names = subprocess.run(
                ["/usr/bin/xattr", str(path)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            if names != ["com.apple.quarantine"]:
                self.skipTest("host cannot fixture an exact quarantine singleton")
            parent_descriptor = os.open(
                root,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY,
            )
            try:
                with self.assertRaises(native_helper.ProbeError) as raised:
                    native_helper._read_home_library_file(
                        parent_descriptor,
                        path.name,
                        path.lstat(),
                        ("journal-inventory", "preferences"),
                    )
            finally:
                os.close(parent_descriptor)
            self.assertTrue(path.is_file())
        self.assertEqual(
            raised.exception.secondary_code,
            "home-library-unsafe-entry-file-xattr-"
            "journal-inventory-preferences-apple-quarantine-stable-bounded",
        )
        self.assertNotIn("private-xattr-canary", str(raised.exception))

    def test_file_xattr_path_family_threads_all_file_check_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            library = home / self.helper.HOME_LIBRARY_NAME
            probe = home / "launchd-probe"
            probe.mkdir(parents=True)
            paths = (
                ("direct", library / "direct.bin"),
                ("preferences", library / "Preferences" / "shallow.bin"),
                ("caches", library / "Caches" / "shallow.bin"),
                (
                    "application-support",
                    library / "Application Support" / "shallow.bin",
                ),
                ("containers", library / "Containers" / "shallow.bin"),
                (
                    "group-containers",
                    library / "Group Containers" / "shallow.bin",
                ),
                ("metadata", library / "Metadata" / "shallow.bin"),
                (
                    "preferences",
                    library / "Preferences" / "ByHost" / "item.bin",
                ),
                ("caches", library / "Caches" / "Deep" / "item.bin"),
                (
                    "application-support",
                    library / "Application Support" / "Vendor" / "item.bin",
                ),
                (
                    "containers",
                    library / "Containers" / "id" / "Data" / "item.bin",
                ),
                (
                    "group-containers",
                    library / "Group Containers" / "id" / "Data" / "item.bin",
                ),
                (
                    "metadata",
                    library / "Metadata" / "CoreSpotlight" / "item.bin",
                ),
                (
                    "nested-other",
                    library / "PrivateNestedCanary" / "Deep" / "item.bin",
                ),
            )
            inode_families = {}
            for path_family, path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(path_family.encode("ascii"))
                inode_families[path.lstat().st_ino] = path_family
            home.chmod(0o700)
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            home_metadata = home.lstat()
            identity = {
                "home_device": home_metadata.st_dev,
                "home_inode": home_metadata.st_ino,
                "probe_device": probe.lstat().st_dev,
                "probe_inode": probe.lstat().st_ino,
            }

            def capture(operation) -> tuple[object, list[tuple[int, str, str]]]:
                contexts: list[tuple[int, str, str]] = []

                def observe(
                    _descriptor: int,
                    **context: object,
                ) -> None:
                    self.assertEqual(
                        set(context),
                        {"diagnostic_phase", "diagnostic_path_family"},
                    )
                    contexts.append(
                        (
                            os.fstat(_descriptor).st_ino,
                            str(context["diagnostic_phase"]),
                            str(context["diagnostic_path_family"]),
                        )
                    )

                with mock.patch.object(
                    self.helper,
                    "_home_library_file_xattr_state",
                    side_effect=observe,
                ):
                    result = operation()
                return result, contexts

            inventory_value, contexts = capture(
                lambda: self.helper._bounded_library_inventory(account)
            )
            self.assertIsInstance(inventory_value, dict)
            assert isinstance(inventory_value, dict)
            self.assertEqual(
                sorted(contexts),
                sorted(
                    [
                        (inode, "journal-inventory", path_family)
                        for inode, path_family in inode_families.items()
                        for _ in range(4)
                    ]
                ),
            )

            authorization = {
                "home_identity": identity,
                "library_inventory": inventory_value,
            }
            with mock.patch.object(
                self.helper,
                "_renameat_exclusive",
                side_effect=self.rename_library_portably,
            ):
                _removed, contexts = capture(
                    lambda: self.helper._quarantine_and_remove_bounded_library(
                        account,
                        authorization,
                    )
                )
            self.assertEqual(
                sorted(contexts),
                sorted(
                    [
                        (inode, phase, path_family)
                        for inode, path_family in inode_families.items()
                        for phase, count in (
                            ("source-revalidation", 4),
                            ("quarantine-revalidation", 4),
                            ("delete-boundary", 2),
                        )
                        for _ in range(count)
                    ]
                ),
            )

    def test_file_xattr_path_family_checks_bracket_each_file_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "payload.bin"
            payload = b"payload"
            path.write_bytes(payload)
            parent_descriptor = os.open(
                root,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY,
            )
            original_read = os.read
            try:
                for phase, path_family in (
                    ("journal-inventory", "direct"),
                    ("source-revalidation", "preferences"),
                    ("quarantine-revalidation", "caches"),
                    ("delete-boundary", "application-support"),
                    ("journal-inventory", "containers"),
                    ("source-revalidation", "group-containers"),
                    ("quarantine-revalidation", "metadata"),
                    ("delete-boundary", "nested-other"),
                ):
                    events: list[tuple[object, ...]] = []

                    def observe(
                        descriptor: int,
                        events: list[tuple[object, ...]] = events,
                        **context: object,
                    ) -> None:
                        events.append(
                            (
                                "xattr",
                                descriptor,
                                context["diagnostic_phase"],
                                context.get("diagnostic_path_family"),
                            )
                        )

                    def read(
                        descriptor: int,
                        maximum: int,
                        events: list[tuple[object, ...]] = events,
                    ) -> bytes:
                        events.append(("read", descriptor))
                        return original_read(descriptor, maximum)

                    with (
                        self.subTest(phase=phase, path_family=path_family),
                        mock.patch.object(
                            self.helper,
                            "_home_library_file_xattr_state",
                            side_effect=observe,
                        ),
                        mock.patch.object(self.helper.os, "read", side_effect=read),
                    ):
                        raw, _metadata = self.helper._read_home_library_file(
                            parent_descriptor,
                            path.name,
                            path.lstat(),
                            (phase, path_family),
                        )
                    self.assertEqual(raw, payload)
                    self.assertEqual(events[0][0], "xattr")
                    descriptor = events[0][1]
                    self.assertEqual(
                        events[0],
                        ("xattr", descriptor, phase, path_family),
                    )
                    self.assertEqual(events[-1], events[0])
                    self.assertTrue(events[1:-1])
                    self.assertTrue(
                        all(event == ("read", descriptor) for event in events[1:-1])
                    )
            finally:
                os.close(parent_descriptor)

    def test_home_library_unsafe_diagnostic_constructor_is_finite_and_value_free(
        self,
    ) -> None:
        primary = "home-library-unsafe-entry"
        common_metadata = {
            "device-mismatch",
            "uid-mismatch",
            "gid-mismatch",
            "group-other-write",
            "flags-present",
            "owner-access",
        }
        expected_causes = frozenset(
            {
                "bound-home-acl",
                "duplicate-identity",
                "entry-stat-unsupported-kind",
                "file-stat-link-count",
                "path-component",
                "root-kind",
                *(
                    f"{scope}-{attribute}"
                    for scope in ("root", "directory", "file")
                    for attribute in ("acl", "xattr")
                ),
                *(
                    f"{scope}-stat-{invariant}"
                    for scope in ("root", "directory", "file")
                    for invariant in common_metadata
                ),
            }
        )
        self.assertEqual(
            self.helper.HOME_LIBRARY_UNSAFE_CAUSES,
            expected_causes,
        )

        for cause in expected_causes:
            with self.subTest(cause=cause):
                error = self.helper._home_library_unsafe_error(cause)
                expected_secondary = f"{primary}-{cause}"
                self.assertEqual(error.code, primary)
                self.assertEqual(error.secondary_code, expected_secondary)
                self.assertLessEqual(len(expected_secondary), 128)
                self.assertEqual(
                    self.helper._validated_probe_error(
                        error.code,
                        error.secondary_code,
                    ),
                    {
                        "code": primary,
                        "secondary_code": expected_secondary,
                    },
                )

        for invalid_cause in (
            "",
            "private-name-canary",
            "file-xattr-private-attribute-canary",
            None,
            ["root-acl"],
        ):
            with self.subTest(invalid_cause=invalid_cause):
                error = self.helper._home_library_unsafe_error(invalid_cause)
                self.assertEqual(error.code, primary)
                self.assertEqual(
                    error.secondary_code,
                    "home-library-diagnostic-invalid",
                )
                self.assertNotIn("canary", str(error.code))
                self.assertNotIn("canary", str(error.secondary_code))

    def test_home_library_path_component_diagnostic_is_value_free(self) -> None:
        canary = "private/path-component-canary"

        with self.assertRaises(self.helper.ProbeError) as raised:
            self.helper._home_library_path_sha256((canary,))

        self.assertEqual(raised.exception.code, "home-library-unsafe-entry")
        self.assertEqual(
            raised.exception.secondary_code,
            "home-library-unsafe-entry-path-component",
        )
        self.assertNotIn("private", raised.exception.code)
        self.assertNotIn("private", raised.exception.secondary_code)
        self.assertEqual(
            self.helper._home_library_path_sha256(("Library",)),
            self.helper._home_library_path_sha256(("Library",)),
        )

    def test_home_library_node_metadata_diagnostics_are_ordered_and_exact(
        self,
    ) -> None:
        account = self.helper.DisposableAccount(
            name="twq-0123456789ab",
            uid=502,
            gid=20,
            home=Path("/Users/twq-0123456789ab"),
        )
        primary = "home-library-unsafe-entry"
        unsupported = SimpleNamespace(
            st_dev=7,
            st_gid=account.gid,
            st_mode=stat.S_IFLNK | 0o700,
            st_nlink=1,
            st_uid=account.uid,
            st_flags=0,
        )
        with self.assertRaises(self.helper.ProbeError) as raised:
            self.helper._require_home_library_node(
                unsupported,
                account=account,
                home_device=7,
            )
        self.assertEqual(raised.exception.code, primary)
        self.assertEqual(
            raised.exception.secondary_code,
            f"{primary}-entry-stat-unsupported-kind",
        )

        for scope in ("root", "directory", "file"):
            mode = (
                stat.S_IFDIR | 0o700
                if scope in {"root", "directory"}
                else stat.S_IFREG | 0o600
            )
            changes_by_invariant = {
                "device-mismatch": {"st_dev": 8},
                "uid-mismatch": {"st_uid": 503},
                "gid-mismatch": {"st_gid": 21},
                "group-other-write": {"st_mode": mode | 0o020},
                "flags-present": {"st_flags": 1},
                "owner-access": {
                    "st_mode": (
                        stat.S_IFREG | 0o200
                        if scope == "file"
                        else stat.S_IFDIR | 0o600
                    )
                },
            }
            if scope == "file":
                changes_by_invariant["link-count"] = {"st_nlink": 2}
            diagnostic_scope = {"diagnostic_scope": "root"} if scope == "root" else {}
            for invariant, changes in changes_by_invariant.items():
                values = {
                    "st_dev": 7,
                    "st_gid": account.gid,
                    "st_mode": mode,
                    "st_nlink": 1,
                    "st_uid": account.uid,
                    "st_flags": 0,
                    **changes,
                }
                with (
                    self.subTest(scope=scope, invariant=invariant),
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._require_home_library_node(
                        SimpleNamespace(**values),
                        account=account,
                        home_device=7,
                        **diagnostic_scope,
                    )
                self.assertEqual(raised.exception.code, primary)
                self.assertEqual(
                    raised.exception.secondary_code,
                    f"{primary}-{scope}-stat-{invariant}",
                )

        precedence = {
            "st_dev": 8,
            "st_gid": 21,
            "st_mode": stat.S_IFREG | 0o220,
            "st_nlink": 2,
            "st_uid": 503,
            "st_flags": 1,
        }
        repairs = (
            ("device-mismatch", {"st_dev": 7}),
            ("uid-mismatch", {"st_uid": account.uid}),
            ("gid-mismatch", {"st_gid": account.gid}),
            ("group-other-write", {"st_mode": stat.S_IFREG | 0o200}),
            ("flags-present", {"st_flags": 0}),
            ("owner-access", {"st_mode": stat.S_IFREG | 0o600}),
            ("link-count", {"st_nlink": 1}),
        )
        for invariant, repair in repairs:
            with (
                self.subTest(precedence=invariant),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._require_home_library_node(
                    SimpleNamespace(**precedence),
                    account=account,
                    home_device=7,
                )
            self.assertEqual(
                raised.exception.secondary_code,
                f"home-library-unsafe-entry-file-stat-{invariant}",
            )
            precedence.update(repair)
        self.assertEqual(
            self.helper._require_home_library_node(
                SimpleNamespace(**precedence),
                account=account,
                home_device=7,
            ),
            "file",
        )

    def test_bounded_library_inventory_threads_scoped_acl_and_xattr_causes(
        self,
    ) -> None:
        cases = (
            ("home-acl", "home", "bound-home-acl"),
            ("root-acl", "root", "root-acl"),
            ("root-xattr", "root", "root-xattr"),
            ("directory-acl", "directory", "directory-acl"),
            ("directory-xattr", "directory", "directory-xattr"),
            ("file-acl", "file", "file-acl"),
            ("file-xattr", "file", "file-xattr"),
        )
        primary = "home-library-unsafe-entry"
        for label, target_kind, cause in cases:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                library = home / "Library"
                child_directory = library / "private-directory-canary"
                child_file = child_directory / "private-file-canary"
                child_directory.mkdir(parents=True)
                child_file.write_bytes(b"private-value-canary")
                home.chmod(0o700)
                targets = {
                    "home": home,
                    "root": library,
                    "directory": child_directory,
                    "file": child_file,
                }
                target = targets[target_kind]
                target_inode = target.lstat().st_ino
                observed_causes: list[object] = []

                def reject(
                    descriptor: int,
                    unsafe_cause: object = None,
                    target_inode: int = target_inode,
                    observed_causes: list[object] = observed_causes,
                    cause: str = cause,
                    **_context: object,
                ) -> None:
                    if os.fstat(descriptor).st_ino == target_inode:
                        observed_causes.append(unsafe_cause)
                        raise self.helper.ProbeError(
                            primary,
                            secondary_code=f"{primary}-{cause}",
                        )

                def reject_file_state(
                    descriptor: int,
                    target_inode: int = target_inode,
                    observed_causes: list[object] = observed_causes,
                    **_context: object,
                ) -> object:
                    if os.fstat(descriptor).st_ino == target_inode:
                        observed_causes.append("file-xattr")
                        return "apple-provenance"
                    return None

                account = self.helper.DisposableAccount(
                    name="twq-0123456789ab",
                    uid=os.geteuid(),
                    gid=os.getegid(),
                    home=home,
                )
                rejecting_acl = label.endswith("acl")
                with (
                    mock.patch.object(
                        self.helper,
                        "_require_no_extended_acl",
                        side_effect=reject if rejecting_acl else None,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_require_no_extended_attributes",
                        side_effect=(
                            reject
                            if not rejecting_acl and label != "file-xattr"
                            else None
                        ),
                        return_value=None,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_home_library_file_xattr_state",
                        side_effect=(
                            reject_file_state if label == "file-xattr" else None
                        ),
                        return_value=None,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_write_stage_create_new",
                    ) as publish,
                    mock.patch.object(
                        self.helper,
                        "_renameat_exclusive",
                    ) as rename,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._bounded_library_inventory(account)

                self.assertEqual(raised.exception.code, primary)
                self.assertEqual(
                    raised.exception.secondary_code,
                    (
                        f"{primary}-file-xattr-journal-inventory-"
                        "nested-other-apple-provenance"
                        if label == "file-xattr"
                        else f"{primary}-{cause}"
                    ),
                )
                self.assertEqual(observed_causes, [cause])
                self.assertNotIn("private", raised.exception.code)
                self.assertNotIn("private", raised.exception.secondary_code)
                publish.assert_not_called()
                rename.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                self.assertTrue(target.exists())

        for target_kind in ("root", "directory", "file"):
            with (
                self.subTest(precedence=target_kind),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                library = home / "Library"
                child_directory = library / "private-directory-canary"
                child_file = child_directory / "private-file-canary"
                child_directory.mkdir(parents=True)
                child_file.write_bytes(b"private-value-canary")
                home.chmod(0o700)
                target = {
                    "root": library,
                    "directory": child_directory,
                    "file": child_file,
                }[target_kind]
                target_inode = target.lstat().st_ino
                acl_causes: list[object] = []
                xattr_causes: list[object] = []

                def reject_acl(
                    descriptor: int,
                    unsafe_cause: object = None,
                    target_inode: int = target_inode,
                    acl_causes: list[object] = acl_causes,
                ) -> None:
                    if os.fstat(descriptor).st_ino == target_inode:
                        acl_causes.append(unsafe_cause)
                        raise self.helper._home_library_unsafe_error(unsafe_cause)

                def reject_xattr(
                    descriptor: int,
                    unsafe_cause: object = None,
                    target_inode: int = target_inode,
                    xattr_causes: list[object] = xattr_causes,
                    **_context: object,
                ) -> None:
                    if os.fstat(descriptor).st_ino == target_inode:
                        xattr_causes.append(unsafe_cause)
                        raise self.helper._home_library_unsafe_error(unsafe_cause)

                def reject_file_state(
                    descriptor: int,
                    target_inode: int = target_inode,
                    xattr_causes: list[object] = xattr_causes,
                    **context: object,
                ) -> object:
                    if os.fstat(descriptor).st_ino == target_inode:
                        xattr_causes.append(
                            (
                                "file-xattr",
                                context.get("diagnostic_phase"),
                                context.get("diagnostic_path_family"),
                            )
                        )
                        return "apple-provenance"
                    return None

                account = self.helper.DisposableAccount(
                    name="twq-0123456789ab",
                    uid=os.geteuid(),
                    gid=os.getegid(),
                    home=home,
                )
                with (
                    mock.patch.object(
                        self.helper,
                        "_require_no_extended_acl",
                        side_effect=reject_acl,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_require_no_extended_attributes",
                        side_effect=reject_xattr,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_home_library_file_xattr_state",
                        side_effect=reject_file_state,
                    ),
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._bounded_library_inventory(account)

                expected = f"{target_kind}-acl"
                self.assertEqual(
                    raised.exception.secondary_code,
                    f"home-library-unsafe-entry-{expected}",
                )
                self.assertEqual(acl_causes, [expected])
                self.assertEqual(xattr_causes, [])
                self.assertTrue(target.exists())

    def test_bounded_library_inventory_reports_root_kind_and_duplicate_identity(
        self,
    ) -> None:
        primary = "home-library-unsafe-entry"
        for scenario in (
            "root-file",
            "root-hardlink",
            "root-symlink",
            "root-fifo",
            "duplicate-identity",
        ):
            with (
                self.subTest(scenario=scenario),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                home.mkdir()
                home.chmod(0o700)
                library = home / "Library"
                preserved: tuple[Path, ...]
                open_patch = nullcontext()
                stat_patch = nullcontext()
                if scenario != "duplicate-identity":
                    if scenario == "root-file":
                        library.write_bytes(b"private-root-file-canary")
                    elif scenario == "root-hardlink":
                        source = home / "private-root-source-canary"
                        source.write_bytes(b"private-root-file-canary")
                        os.link(source, library)
                        preserved = (source, library)
                    elif scenario == "root-symlink":
                        library.symlink_to("private-missing-root-canary")
                    else:
                        os.mkfifo(library)
                    if scenario != "root-hardlink":
                        preserved = (library,)
                else:
                    first = library / "private-duplicate-a-canary"
                    second = library / "private-duplicate-b-canary"
                    first.mkdir(parents=True)
                    second.mkdir()
                    preserved = (first, second)
                    original_open = self.helper.os.open
                    original_stat = self.helper.os.stat

                    def duplicate_open(
                        path: object,
                        *args: object,
                        first: Path = first,
                        second: Path = second,
                        original_open: object = original_open,
                        **kwargs: object,
                    ) -> int:
                        selected = first.name if path == second.name else path
                        return original_open(selected, *args, **kwargs)

                    def duplicate_stat(
                        path: object,
                        *args: object,
                        first: Path = first,
                        second: Path = second,
                        original_stat: object = original_stat,
                        **kwargs: object,
                    ) -> os.stat_result:
                        selected = first.name if path == second.name else path
                        return original_stat(selected, *args, **kwargs)

                    open_patch = mock.patch.object(
                        self.helper.os,
                        "open",
                        side_effect=duplicate_open,
                    )
                    stat_patch = mock.patch.object(
                        self.helper.os,
                        "stat",
                        side_effect=duplicate_stat,
                    )
                account = self.helper.DisposableAccount(
                    name="twq-0123456789ab",
                    uid=os.geteuid(),
                    gid=os.getegid(),
                    home=home,
                )
                with (
                    open_patch,
                    stat_patch,
                    mock.patch.object(
                        self.helper,
                        "_write_stage_create_new",
                    ) as publish,
                    mock.patch.object(
                        self.helper,
                        "_renameat_exclusive",
                    ) as rename,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._bounded_library_inventory(account)

                self.assertEqual(raised.exception.code, primary)
                self.assertEqual(
                    raised.exception.secondary_code,
                    f"{primary}-"
                    f"{'duplicate-identity' if scenario == 'duplicate-identity' else 'root-kind'}",
                )
                self.assertNotIn("private", raised.exception.code)
                self.assertNotIn("private", raised.exception.secondary_code)
                publish.assert_not_called()
                rename.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                for path in preserved:
                    self.assertTrue(path.exists() or path.is_symlink())

    def test_bounded_library_inventory_is_canonical_and_value_free(self) -> None:
        inventory_builder = getattr(
            self.helper,
            "_bounded_library_inventory",
            None,
        )
        self.assertIsNotNone(inventory_builder)
        assert inventory_builder is not None

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            preferences = home / "Library" / "Preferences"
            caches = home / "Library" / "Caches"
            probe.mkdir(parents=True, mode=0o700)
            preferences.mkdir(parents=True)
            caches.mkdir()
            home.chmod(0o700)
            probe.chmod(0o700)
            name_canary = "private-library-name-canary.plist"
            empty_name_canary = "private-empty-library-canary"
            value_canary = b"private-library-value-canary"
            payload = preferences / name_canary
            empty_payload = caches / empty_name_canary
            payload.write_bytes(value_canary)
            empty_payload.write_bytes(b"")
            sentinel_mtime_ns = 1_600_000_000_123_456_789
            os.utime(
                payload,
                ns=(sentinel_mtime_ns, sentinel_mtime_ns),
                follow_symlinks=False,
            )
            payload_metadata = payload.lstat()
            self.assertNotEqual(
                payload_metadata.st_ctime_ns,
                payload_metadata.st_mtime_ns,
            )
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )

            first = inventory_builder(account)
            second = inventory_builder(account)
            self.assertEqual(first, second)
            self.assertEqual(
                set(first),
                {
                    "schema_version",
                    "contract",
                    "entry_count",
                    "regular_file_bytes",
                    "entries",
                    "content_sha256",
                },
            )
            self.assertEqual(first["schema_version"], 1)
            self.assertEqual(
                first["contract"],
                "task-witness-macos-bounded-library-inventory-v1",
            )
            self.assertEqual(first["entry_count"], len(first["entries"]))
            self.assertEqual(first["regular_file_bytes"], len(value_canary))
            self.assertEqual(
                [entry["path_sha256"] for entry in first["entries"]],
                sorted(entry["path_sha256"] for entry in first["entries"]),
            )
            for entry in first["entries"]:
                self.assertRegex(entry["path_sha256"], r"[0-9a-f]{64}")
            for path, kind in (
                (home / "Library", "directory"),
                (preferences, "directory"),
                (caches, "directory"),
                (payload, "file"),
                (empty_payload, "file"),
            ):
                metadata = path.lstat()
                matching = [
                    entry
                    for entry in first["entries"]
                    if entry["inode"] == metadata.st_ino
                ]
                self.assertEqual(len(matching), 1)
                record = matching[0]
                self.assertEqual(record["device"], metadata.st_dev)
                self.assertEqual(
                    record["flags"],
                    int(getattr(metadata, "st_flags", 0)),
                )
                self.assertEqual(record["gid"], metadata.st_gid)
                self.assertEqual(record["kind"], kind)
                self.assertEqual(record["mode"], metadata.st_mode)
                self.assertEqual(record["uid"], metadata.st_uid)
            file_entries = [
                entry for entry in first["entries"] if entry.get("kind") == "file"
            ]
            self.assertEqual(len(file_entries), 2)
            payload_entry = next(
                entry
                for entry in file_entries
                if entry["inode"] == payload_metadata.st_ino
            )
            self.assertEqual(
                payload_entry["ctime_ns"],
                payload_metadata.st_ctime_ns,
            )
            self.assertEqual(
                payload_entry["mtime_ns"],
                payload_metadata.st_mtime_ns,
            )
            self.assertEqual(
                payload_entry["link_count"],
                payload_metadata.st_nlink,
            )
            self.assertEqual(payload_entry["size"], payload_metadata.st_size)
            self.assertEqual(
                payload_entry["content_sha256"],
                hashlib.sha256(value_canary).hexdigest(),
            )
            empty_metadata = empty_payload.lstat()
            empty_entry = next(
                entry
                for entry in file_entries
                if entry["inode"] == empty_metadata.st_ino
            )
            self.assertEqual(empty_entry["size"], 0)
            self.assertEqual(
                empty_entry["content_sha256"],
                hashlib.sha256(b"").hexdigest(),
            )
            self.helper._require_content_digest(
                first,
                "bounded-library-inventory",
            )
            raw = self.helper.canonical_bytes(first)
            self.assertEqual(json.loads(raw.decode("utf-8")), first)
            for raw_name in (
                "Library",
                "Preferences",
                "Caches",
                name_canary,
                empty_name_canary,
            ):
                self.assertNotIn(raw_name.encode(), raw)
            self.assertNotIn(value_canary, raw)

            payload.write_bytes(b"changed-library-value-canary")
            changed = inventory_builder(account)
            self.assertNotEqual(changed, first)
            self.assertNotEqual(
                changed["content_sha256"],
                first["content_sha256"],
            )

    def test_paired_library_observation_rejects_directory_state_only_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            preferences = home / "Library" / "Preferences"
            probe.mkdir(parents=True, mode=0o700)
            preferences.mkdir(parents=True)
            home.chmod(0o700)
            probe.chmod(0o700)
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            original_observe = self.helper._observe_bounded_library
            observations: list[object] = []
            directory_metadata: list[os.stat_result] = []

            def observe(*args: object, **kwargs: object):
                result = original_observe(*args, **kwargs)
                observations.append(result)
                directory_metadata.append(preferences.lstat())
                if len(observations) == 1:
                    sentinel_ns = 1_600_000_000_123_456_789
                    os.utime(
                        preferences,
                        ns=(sentinel_ns, sentinel_ns),
                        follow_symlinks=False,
                    )
                return result

            with (
                mock.patch.object(
                    self.helper,
                    "_observe_bounded_library",
                    side_effect=observe,
                ),
                mock.patch.object(
                    self.helper,
                    "_write_stage_create_new",
                ) as publish,
                mock.patch.object(
                    self.helper,
                    "_renameat_exclusive",
                ) as rename,
                mock.patch.object(self.helper.os, "unlink") as unlink,
                mock.patch.object(self.helper.os, "rmdir") as rmdir,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._bounded_library_inventory(account)

            self.assertEqual(
                raised.exception.code,
                "home-library-observation-unstable",
            )
            self.assertIsNone(raised.exception.secondary_code)
            self.assertEqual(len(observations), 2)
            first, second = observations
            self.assertEqual(first.inventory, second.inventory)
            self.assertNotEqual(first.stability_sha256, second.stability_sha256)
            before, after = directory_metadata
            self.assertEqual(
                (
                    before.st_dev,
                    before.st_ino,
                    before.st_mode,
                    before.st_uid,
                    before.st_gid,
                    int(getattr(before, "st_flags", 0)),
                ),
                (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_uid,
                    after.st_gid,
                    int(getattr(after, "st_flags", 0)),
                ),
            )
            self.assertNotEqual(
                (before.st_ctime_ns, before.st_mtime_ns),
                (after.st_ctime_ns, after.st_mtime_ns),
            )
            publish.assert_not_called()
            rename.assert_not_called()
            unlink.assert_not_called()
            rmdir.assert_not_called()
            self.assertTrue(preferences.is_dir())

    def test_bounded_library_inventory_rejects_malformed_records_before_mutation(
        self,
    ) -> None:
        private_canary = "private-malformed-library-inventory-canary"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account, _identity, authorization, _relative_files = (
                self.bounded_library_cleanup_fixture(root)
            )
            inventory = authorization["library_inventory"]
            self.assertIsInstance(inventory, dict)
            plan = self.helper.LaunchdPlan(
                account=account,
                label=authorization["label"],
                stage_root=root / "stage",
                helper=root / "stage/helper.py",
                plist=root / "stage/job.plist",
            )
            state = self.lifecycle_state(plan)
            bindings = self.helper.ValidatedStageBindings(
                self.helper._account_binding_document(
                    plan,
                    state,
                    "01234567-89AB-4DEF-8123-456789ABCDEF",
                ),
                None,
            )
            root_record = next(
                entry for entry in inventory["entries"] if entry.get("depth") == 1
            )
            file_root_record = {
                **root_record,
                "content_sha256": "0" * 64,
                "ctime_ns": 1,
                "kind": "file",
                "link_count": 1,
                "mode": stat.S_IFREG | 0o600,
                "mtime_ns": 1,
                "size": 0,
            }

            cases = {
                "scalar-record": {
                    "entries": [private_canary],
                    "entry_count": 1,
                    "regular_file_bytes": 0,
                },
                "non-string-path": {
                    "entries": [{"path_sha256": 7}],
                    "entry_count": 1,
                    "regular_file_bytes": 0,
                },
                "list-kind": {
                    "entries": [{**root_record, "kind": []}],
                    "entry_count": 1,
                    "regular_file_bytes": 0,
                },
                "mapping-kind": {
                    "entries": [{**root_record, "kind": {}}],
                    "entry_count": 1,
                    "regular_file_bytes": 0,
                },
                "file-root": {
                    "entries": [file_root_record],
                    "entry_count": 1,
                    "regular_file_bytes": 0,
                },
                "boolean-schema": {"schema_version": True},
                "floating-schema": {"schema_version": 1.0},
            }
            for label, changes in cases.items():
                malformed = {
                    **inventory,
                    **changes,
                }
                malformed = self.helper._document_with_digest(
                    {
                        key: value
                        for key, value in malformed.items()
                        if key != "content_sha256"
                    }
                )
                changed_authorization = {
                    **authorization,
                    "library_inventory": malformed,
                }
                changed_authorization = self.helper._document_with_digest(
                    {
                        key: value
                        for key, value in changed_authorization.items()
                        if key != "content_sha256"
                    }
                )
                with (
                    self.subTest(label=label),
                    mock.patch.object(self.helper, "_renameat_exclusive") as rename,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._quarantine_and_remove_bounded_library(
                        account,
                        changed_authorization,
                    )

                self.assertEqual(
                    raised.exception.code,
                    "home-library-inventory-invalid",
                )
                self.assertNotIn(private_canary, raised.exception.code)
                rename.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                self.assertTrue((account.home / "Library").is_dir())

                with (
                    self.subTest(label=f"{label}-journal"),
                    mock.patch.object(
                        self.helper,
                        "_path_exists_no_follow",
                        return_value=True,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        return_value=True,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_load_canonical_document",
                        return_value=changed_authorization,
                    ),
                    self.assertRaises(self.helper.ProbeError) as journal_error,
                ):
                    self.helper._load_home_cleanup_authorization(
                        plan,
                        state,
                        bindings,
                    )
                self.assertEqual(
                    journal_error.exception.code,
                    "home-cleanup-authorization-drift",
                )
                self.assertNotIn(private_canary, journal_error.exception.code)

            outer_schema_drift = self.helper._document_with_digest(
                {
                    **{
                        key: value
                        for key, value in authorization.items()
                        if key != "content_sha256"
                    },
                    "schema_version": 2.0,
                }
            )
            with (
                mock.patch.object(
                    self.helper,
                    "_path_exists_no_follow",
                    return_value=True,
                ),
                mock.patch.object(
                    self.helper,
                    "_metadata_matches",
                    return_value=True,
                ),
                mock.patch.object(
                    self.helper,
                    "_load_canonical_document",
                    return_value=outer_schema_drift,
                ),
                self.assertRaises(self.helper.ProbeError) as outer_error,
            ):
                self.helper._load_home_cleanup_authorization(
                    plan,
                    state,
                    bindings,
                )
            self.assertEqual(
                outer_error.exception.code,
                "home-cleanup-authorization-drift",
            )

    def test_bounded_library_enumeration_stops_at_the_node_budget(self) -> None:
        class BoundedEntries:
            def __init__(self, count: int) -> None:
                self.count = count
                self.consumed = 0

            def __enter__(self) -> Iterator[SimpleNamespace]:
                return iter(self)

            def __exit__(self, *_args: object) -> None:
                return None

            def __iter__(self):
                for index in range(self.count):
                    self.consumed += 1
                    yield SimpleNamespace(name=f"private-entry-{index}")

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            library = home / "Library"
            probe.mkdir(parents=True, mode=0o700)
            library.mkdir()
            home.chmod(0o700)
            probe.chmod(0o700)
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            observed = BoundedEntries(self.helper.MAX_HOME_LIBRARY_NODES + 5)
            with (
                mock.patch.object(self.helper.os, "scandir", return_value=observed),
                mock.patch.object(
                    self.helper.os,
                    "listdir",
                    side_effect=AssertionError("unbounded directory materialization"),
                ),
                mock.patch.object(
                    self.helper,
                    "_require_no_extended_acl",
                ),
                mock.patch.object(
                    self.helper,
                    "_darwin_fstatfs_identity",
                    return_value=(1, 2, "apfs"),
                ),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._bounded_library_inventory(account)

            self.assertEqual(raised.exception.code, "home-library-bounds-exceeded")
            self.assertEqual(
                observed.consumed,
                self.helper.MAX_HOME_LIBRARY_NODES,
            )

        deleting = BoundedEntries(5)
        with (
            mock.patch.object(self.helper.os, "scandir", return_value=deleting),
            mock.patch.object(
                self.helper.os,
                "listdir",
                side_effect=AssertionError("unbounded directory materialization"),
            ),
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._delete_authorized_library_directory(
                17,
                (self.helper.HOME_LIBRARY_NAME,),
                {"0" * 64: {"kind": "directory"}},
            )
        self.assertEqual(raised.exception.code, "home-library-inventory-drift")
        self.assertEqual(deleting.consumed, 2)

    def test_home_and_probe_enumeration_stop_at_their_exact_budgets(self) -> None:
        class BoundedEntries:
            def __init__(self, count: int) -> None:
                self.count = count
                self.consumed = 0

            def __enter__(self) -> Iterator[SimpleNamespace]:
                return iter(self)

            def __exit__(self, *_args: object) -> None:
                return None

            def __iter__(self):
                for index in range(self.count):
                    self.consumed += 1
                    yield SimpleNamespace(name=f"private-entry-{index}")

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            excess_home = BoundedEntries(20)
            with (
                mock.patch.object(
                    self.helper.os,
                    "scandir",
                    return_value=excess_home,
                ),
                mock.patch.object(Path, "unlink") as unlink,
                mock.patch.object(Path, "rmdir") as rmdir,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._validate_disposable_home_root(
                    account,
                    diagnostic_phase="child-entry",
                )
            self.assertEqual(
                raised.exception.code,
                "home-cleanup-child-entry-home-entry-observation-unreadable",
            )
            self.assertEqual(
                excess_home.consumed,
                self.helper.MAX_DISPOSABLE_HOME_ENTRIES + 1,
            )
            unlink.assert_not_called()
            rmdir.assert_not_called()

            for name in self.helper.LAUNCHD_CHILD_FILES:
                (probe / name).write_bytes(b"value")
            excess_probe = BoundedEntries(20)
            original_scandir = os.scandir
            probe_inode = probe.lstat().st_ino

            def selective_scandir(descriptor: int):
                return (
                    excess_probe
                    if os.fstat(descriptor).st_ino == probe_inode
                    else original_scandir(descriptor)
                )

            with (
                mock.patch.object(
                    self.helper.os,
                    "scandir",
                    side_effect=selective_scandir,
                ),
                mock.patch.object(Path, "unlink") as unlink,
                mock.patch.object(Path, "rmdir") as rmdir,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._validate_exact_disposable_home(
                    home,
                    expected_uid=account.uid,
                    expected_gid=account.gid,
                    diagnostic_phase="pre-journal",
                )
            self.assertEqual(
                raised.exception.code,
                "home-cleanup-pre-journal-probe-entry-set-drift",
            )
            self.assertEqual(
                excess_probe.consumed,
                self.helper.MAX_LAUNCHD_PROBE_ENTRIES + 1,
            )
            unlink.assert_not_called()
            rmdir.assert_not_called()

    def test_bounded_library_inventory_rejects_unsafe_nested_nodes_without_deletion(
        self,
    ) -> None:
        inventory_builder = getattr(
            self.helper,
            "_bounded_library_inventory",
            None,
        )
        self.assertIsNotNone(inventory_builder)
        assert inventory_builder is not None

        def symlink(library: Path) -> tuple[Path, ...]:
            target = library / "target"
            target.write_bytes(b"preserve")
            link = library / "private-symlink-canary"
            link.symlink_to(target)
            return target, link

        def fifo(library: Path) -> tuple[Path, ...]:
            path = library / "private-fifo-canary"
            os.mkfifo(path)
            return (path,)

        def hardlink(library: Path) -> tuple[Path, ...]:
            source = library / "private-hardlink-source-canary"
            peer = library / "private-hardlink-peer-canary"
            source.write_bytes(b"preserve")
            os.link(source, peer)
            return source, peer

        def file_xattr(library: Path) -> tuple[Path, ...]:
            path = library / "private-file-xattr-canary"
            path.write_bytes(b"preserve")
            self.write_extended_attribute(path, "com.example.task-witness")
            return (path,)

        def directory_xattr(library: Path) -> tuple[Path, ...]:
            path = library / "private-directory-xattr-canary"
            path.mkdir()
            self.write_extended_attribute(path, "com.example.task-witness")
            return (path,)

        def resource_fork(library: Path) -> tuple[Path, ...]:
            path = library / "private-resource-fork-canary"
            path.write_bytes(b"preserve")
            self.write_extended_attribute(path, "com.apple.ResourceFork")
            return (path,)

        for label, arrange, expected_cause in (
            ("symlink", symlink, "entry-stat-unsupported-kind"),
            ("fifo", fifo, "entry-stat-unsupported-kind"),
            ("hardlink", hardlink, "file-stat-link-count"),
            ("file-xattr", file_xattr, "file-xattr"),
            ("directory-xattr", directory_xattr, "directory-xattr"),
            ("resource-fork", resource_fork, "file-xattr"),
        ):
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                probe = home / "launchd-probe"
                library = home / "Library"
                probe.mkdir(parents=True, mode=0o700)
                library.mkdir()
                home.chmod(0o700)
                probe.chmod(0o700)
                preserved = arrange(library)
                rejected_inodes = (
                    {path.lstat().st_ino for path in preserved}
                    if label in {"file-xattr", "directory-xattr", "resource-fork"}
                    else set()
                )

                def reject_extended_attributes(
                    descriptor: int,
                    unsafe_cause: object = None,
                    rejected: set[int] = rejected_inodes,
                    **_context: object,
                ) -> None:
                    if os.fstat(descriptor).st_ino in rejected:
                        raise self.helper._home_library_unsafe_error(unsafe_cause)

                refined_family = {
                    "file-xattr": "nonapple-other",
                    "resource-fork": "resource-fork",
                }.get(label)

                def reject_file_state(
                    descriptor: int,
                    rejected: set[int] = rejected_inodes,
                    refined_family: object = refined_family,
                    **_context: object,
                ) -> object:
                    if os.fstat(descriptor).st_ino in rejected:
                        return refined_family
                    return None

                account = self.helper.DisposableAccount(
                    name="twq-0123456789ab",
                    uid=os.geteuid(),
                    gid=os.getegid(),
                    home=home,
                )

                with (
                    mock.patch.object(
                        self.helper,
                        "_require_no_extended_attributes",
                        side_effect=(
                            reject_extended_attributes
                            if label == "directory-xattr"
                            else None
                        ),
                        return_value=None,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_home_library_file_xattr_state",
                        side_effect=(
                            reject_file_state if refined_family is not None else None
                        ),
                        return_value=None,
                    ),
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    inventory_builder(account)

                self.assertNotIn("private", raised.exception.code)
                self.assertEqual(
                    raised.exception.secondary_code,
                    (
                        "home-library-unsafe-entry-file-xattr-"
                        f"journal-inventory-direct-{refined_family}"
                        if refined_family is not None
                        else f"home-library-unsafe-entry-{expected_cause}"
                    ),
                )
                for path in preserved:
                    self.assertTrue(path.exists() or path.is_symlink())
                if label == "hardlink":
                    self.assertEqual(preserved[0].lstat().st_nlink, 2)
                    self.assertEqual(preserved[1].lstat().st_nlink, 2)

    def test_home_cleanup_v2_authorization_includes_bounded_library_inventory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            home = root / "home"
            probe = home / "launchd-probe"
            library = home / "Library"
            probe.mkdir(parents=True, mode=0o700)
            library.mkdir()
            home.chmod(0o700)
            probe.chmod(0o700)
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            plan = self.helper.LaunchdPlan(
                account=account,
                label="io.nisavid.task-witness.macos-probe.0123456789ab",
                stage_root=stage,
                helper=stage / "helper.py",
                plist=stage / "job.plist",
            )
            state = self.lifecycle_state(plan)
            account_binding = self.helper._account_binding_document(
                plan,
                state,
                "01234567-89AB-4DEF-8123-456789ABCDEF",
            )
            bindings = self.helper.ValidatedStageBindings(account_binding, None)
            home_metadata = home.lstat()
            probe_metadata = probe.lstat()
            home_identity = {
                "home_device": home_metadata.st_dev,
                "home_inode": home_metadata.st_ino,
                "probe_device": probe_metadata.st_dev,
                "probe_inode": probe_metadata.st_ino,
            }
            inventory = self.helper._bounded_library_inventory(account)
            self.assertIsInstance(inventory, dict)
            written: dict[str, object] = {}

            def write(path: Path, raw: bytes, mode: int) -> None:
                written.update(path=path, raw=raw, mode=mode)

            def readback(*_args: object, **_kwargs: object) -> dict:
                return json.loads(bytes(written["raw"]))

            with (
                mock.patch.object(
                    self.helper,
                    "_bounded_library_inventory",
                    create=True,
                    return_value=inventory,
                ) as bounded,
                mock.patch.object(
                    self.helper,
                    "_write_stage_create_new",
                    side_effect=write,
                ),
                mock.patch.object(self.helper, "_fsync_stage_directory"),
                mock.patch.object(
                    self.helper,
                    "_load_home_cleanup_authorization",
                    side_effect=readback,
                ),
            ):
                document = self.helper._write_home_cleanup_authorization(
                    plan,
                    state,
                    bindings,
                    home_identity,
                )

            self.assertEqual(document["schema_version"], 2)
            self.assertEqual(
                document["contract"],
                "task-witness-macos-home-cleanup-authorization-v2",
            )
            self.assertEqual(document["library_inventory"], inventory)
            bounded.assert_called_once_with(account)
            self.assertEqual(
                written,
                {
                    "path": stage / "home-cleanup.json",
                    "raw": self.helper.canonical_bytes(document),
                    "mode": 0o600,
                },
            )
            self.helper._require_content_digest(
                document,
                "home-cleanup-authorization",
            )

    def test_bounded_library_cleanup_rejects_extended_attributes_before_mutation(
        self,
    ) -> None:
        for label, relative_path, attribute, expected_cause in (
            (
                "root",
                (),
                "com.example.task-witness",
                "root-xattr",
            ),
            (
                "file",
                ("Preferences", "settings.plist"),
                "com.example.task-witness",
                "file-xattr",
            ),
            (
                "directory",
                ("Container",),
                "com.example.task-witness",
                "directory-xattr",
            ),
            (
                "resource-fork",
                ("Container", "Deep", "payload.bin"),
                "com.apple.ResourceFork",
                "file-xattr",
            ),
        ):
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                account, _identity, authorization, _relative_files = (
                    self.bounded_library_cleanup_fixture(root)
                )
                target = account.home.joinpath(
                    self.helper.HOME_LIBRARY_NAME,
                    *relative_path,
                )
                self.write_extended_attribute(target, attribute)
                self.assertIn(
                    attribute,
                    subprocess.run(
                        ["/usr/bin/xattr", str(target)],
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.splitlines(),
                )
                target_inode = target.lstat().st_ino

                def reject_extended_attributes(
                    descriptor: int,
                    unsafe_cause: object = None,
                    target: int = target_inode,
                    **_context: object,
                ) -> None:
                    if os.fstat(descriptor).st_ino == target:
                        raise self.helper._home_library_unsafe_error(unsafe_cause)

                refined = {
                    "file": ("preferences", "nonapple-other"),
                    "resource-fork": ("nested-other", "resource-fork"),
                }.get(label)

                def reject_file_state(
                    descriptor: int,
                    target: int = target_inode,
                    refined: object = refined,
                    **_context: object,
                ) -> object:
                    if os.fstat(descriptor).st_ino == target:
                        assert isinstance(refined, tuple)
                        return refined[1]
                    return None

                with (
                    mock.patch.object(
                        self.helper,
                        "_require_no_extended_attributes",
                        side_effect=(
                            reject_extended_attributes if refined is None else None
                        ),
                        return_value=None,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_home_library_file_xattr_state",
                        side_effect=(
                            reject_file_state if refined is not None else None
                        ),
                        return_value=None,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_renameat_exclusive",
                    ) as rename,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._quarantine_and_remove_bounded_library(
                        account,
                        authorization,
                    )

                self.assertEqual(raised.exception.code, "home-library-unsafe-entry")
                self.assertEqual(
                    raised.exception.secondary_code,
                    (
                        "home-library-unsafe-entry-file-xattr-"
                        f"source-revalidation-{refined[0]}-{refined[1]}"
                        if refined is not None
                        else f"home-library-unsafe-entry-{expected_cause}"
                    ),
                )
                self.assertNotIn("private", raised.exception.code)
                rename.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                self.assertTrue(target.exists())

    def test_quarantine_evidence_rejects_at_every_cleanup_mutation_boundary(
        self,
    ) -> None:
        evidence_type = getattr(
            self.helper,
            "_HomeLibraryQuarantineEvidence",
            None,
        )
        self.assertIsNotNone(evidence_type)
        assert evidence_type is not None
        evidence = evidence_type(7, "private-evidence-digest-canary")

        def observer_for(
            target_inode: int,
        ) -> tuple[list[tuple[str, str]], object]:
            contexts: list[tuple[str, str]] = []

            def observe(
                descriptor: int,
                **context: object,
            ) -> object:
                if os.fstat(descriptor).st_ino == target_inode:
                    contexts.append(
                        (
                            str(context["diagnostic_phase"]),
                            str(context["diagnostic_path_family"]),
                        )
                    )
                    return evidence
                return None

            return contexts, observe

        with tempfile.TemporaryDirectory() as directory:
            account, _identity, authorization, relative_files = (
                self.bounded_library_cleanup_fixture(Path(directory))
            )
            source = account.home / self.helper.HOME_LIBRARY_NAME
            target = source.joinpath(*relative_files[1])
            contexts, observe = observer_for(target.lstat().st_ino)
            with (
                mock.patch.object(
                    self.helper,
                    "_home_library_file_xattr_state",
                    side_effect=observe,
                ),
                mock.patch.object(self.helper, "_renameat_exclusive") as rename,
                mock.patch.object(self.helper.os, "unlink") as unlink,
                mock.patch.object(self.helper.os, "fsync") as fsync,
                mock.patch.object(self.helper.os, "rmdir") as rmdir,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._quarantine_and_remove_bounded_library(
                    account,
                    authorization,
                )
            expected_secondary = (
                "home-library-unsafe-entry-file-xattr-"
                "source-revalidation-preferences-"
                "apple-quarantine-stable-bounded"
            )
            self.assertEqual(
                (raised.exception.code, raised.exception.secondary_code),
                (
                    "home-library-unsafe-entry",
                    expected_secondary,
                ),
            )
            self.assertEqual(
                contexts,
                [("source-revalidation", "preferences")] * 2,
            )
            self.assertNotIn("private", str(raised.exception))
            rename.assert_not_called()
            unlink.assert_not_called()
            fsync.assert_not_called()
            rmdir.assert_not_called()
            self.assertTrue(target.is_file())

        with tempfile.TemporaryDirectory() as directory:
            account, _identity, authorization, relative_files = (
                self.bounded_library_cleanup_fixture(Path(directory))
            )
            source = account.home / self.helper.HOME_LIBRARY_NAME
            quarantine = account.home / self.helper.HOME_LIBRARY_QUARANTINE_NAME
            source.rename(quarantine)
            target = quarantine.joinpath(*relative_files[1])
            contexts, observe = observer_for(target.lstat().st_ino)
            original_fsync = os.fsync
            with (
                mock.patch.object(
                    self.helper,
                    "_home_library_file_xattr_state",
                    side_effect=observe,
                ),
                mock.patch.object(self.helper, "_renameat_exclusive") as rename,
                mock.patch.object(self.helper.os, "unlink") as unlink,
                mock.patch.object(
                    self.helper.os,
                    "fsync",
                    side_effect=original_fsync,
                ) as fsync,
                mock.patch.object(self.helper.os, "rmdir") as rmdir,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._quarantine_and_remove_bounded_library(
                    account,
                    authorization,
                )
            expected_secondary = (
                "home-library-unsafe-entry-file-xattr-"
                "quarantine-revalidation-preferences-"
                "apple-quarantine-stable-bounded"
            )
            self.assertEqual(
                (raised.exception.code, raised.exception.secondary_code),
                (
                    "home-library-unsafe-entry",
                    expected_secondary,
                ),
            )
            self.assertEqual(
                contexts,
                [("quarantine-revalidation", "preferences")] * 2,
            )
            self.assertNotIn("private", str(raised.exception))
            rename.assert_not_called()
            unlink.assert_not_called()
            self.assertEqual(fsync.call_count, 1)
            rmdir.assert_not_called()
            self.assertFalse(source.exists())
            self.assertTrue(target.is_file())

        with tempfile.TemporaryDirectory() as directory:
            account, _identity, authorization, relative_files = (
                self.bounded_library_cleanup_fixture(Path(directory))
            )
            source = account.home / self.helper.HOME_LIBRARY_NAME
            quarantine = account.home / self.helper.HOME_LIBRARY_QUARANTINE_NAME
            source.rename(quarantine)
            target = quarantine.joinpath(*relative_files[0])
            contexts, observe = observer_for(target.lstat().st_ino)
            entries = authorization["library_inventory"]["entries"]
            authorized_by_path = {
                item["path_sha256"]: item for item in entries if isinstance(item, dict)
            }
            descriptor = os.open(
                quarantine,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                with (
                    mock.patch.object(
                        self.helper,
                        "_home_library_file_xattr_state",
                        side_effect=observe,
                    ),
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "fsync") as fsync,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._delete_authorized_library_directory(
                        descriptor,
                        (self.helper.HOME_LIBRARY_NAME,),
                        authorized_by_path,
                    )
            finally:
                os.close(descriptor)
            expected_secondary = (
                "home-library-unsafe-entry-file-xattr-"
                "delete-boundary-nested-other-"
                "apple-quarantine-stable-bounded"
            )
            self.assertEqual(
                (raised.exception.code, raised.exception.secondary_code),
                (
                    "home-library-unsafe-entry",
                    expected_secondary,
                ),
            )
            self.assertEqual(
                contexts,
                [("delete-boundary", "nested-other")] * 2,
            )
            self.assertNotIn("private", str(raised.exception))
            unlink.assert_not_called()
            fsync.assert_not_called()
            rmdir.assert_not_called()
            self.assertTrue(target.is_file())

    def test_bounded_library_cleanup_rechecks_xattrs_at_each_delete_boundary(
        self,
    ) -> None:
        def exercise(
            relative_path: tuple[str, ...],
            expected_cause: str,
            reject_call: int,
        ) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                account, _identity, authorization, _relative_files = (
                    self.bounded_library_cleanup_fixture(root)
                )
                source_target = account.home.joinpath(
                    self.helper.HOME_LIBRARY_NAME,
                    *relative_path,
                )
                target_inode = source_target.lstat().st_ino
                after_quarantine_scan = False
                target_calls: list[object] = []
                stable_observed = self.helper._stable_observed_library

                def stable(*args: object, **kwargs: object) -> dict:
                    nonlocal after_quarantine_scan
                    observed = stable_observed(*args, **kwargs)
                    if args[1] == self.helper.HOME_LIBRARY_QUARANTINE_NAME:
                        after_quarantine_scan = True
                    return observed

                def reject_target_xattrs(
                    descriptor: int,
                    unsafe_cause: object = None,
                    **_context: object,
                ) -> None:
                    if (
                        after_quarantine_scan
                        and os.fstat(descriptor).st_ino == target_inode
                    ):
                        target_calls.append(unsafe_cause)
                        if len(target_calls) == reject_call:
                            raise self.helper._home_library_unsafe_error(unsafe_cause)

                def reject_file_state(
                    descriptor: int,
                    **_context: object,
                ) -> object:
                    if (
                        after_quarantine_scan
                        and os.fstat(descriptor).st_ino == target_inode
                    ):
                        target_calls.append("file-xattr")
                        if len(target_calls) == reject_call:
                            return "nonapple-other"
                    return None

                with (
                    mock.patch.object(
                        self.helper,
                        "_renameat_exclusive",
                        side_effect=self.rename_library_portably,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_stable_observed_library",
                        side_effect=stable,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_require_no_extended_attributes",
                        side_effect=(
                            reject_target_xattrs
                            if expected_cause != "file-xattr"
                            else None
                        ),
                        return_value=None,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_home_library_file_xattr_state",
                        side_effect=(
                            reject_file_state
                            if expected_cause == "file-xattr"
                            else None
                        ),
                        return_value=None,
                    ),
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._quarantine_and_remove_bounded_library(
                        account,
                        authorization,
                    )

                self.assertEqual(
                    raised.exception.code,
                    "home-library-unsafe-entry",
                )
                self.assertEqual(
                    raised.exception.secondary_code,
                    (
                        "home-library-unsafe-entry-file-xattr-"
                        "delete-boundary-preferences-nonapple-other"
                        if expected_cause == "file-xattr"
                        else f"home-library-unsafe-entry-{expected_cause}"
                    ),
                )
                self.assertEqual(
                    target_calls,
                    [expected_cause] * reject_call,
                )
                quarantine_target = account.home.joinpath(
                    self.helper.HOME_LIBRARY_QUARANTINE_NAME,
                    *relative_path,
                )
                self.assertTrue(quarantine_target.exists())

        cases = (
            ("file", ("Preferences", "settings.plist"), "file-xattr"),
            ("directory", ("Preferences",), "directory-xattr"),
            ("root", (), "root-xattr"),
        )
        for scope, relative_path, expected_cause in cases:
            for reject_call in (1, 2):
                with self.subTest(scope=scope, reject_call=reject_call):
                    exercise(relative_path, expected_cause, reject_call)

    def test_bounded_library_delete_rejects_jit_file_content_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            library = home / "Library"
            library.mkdir(parents=True)
            home.chmod(0o700)
            target = library / "private-jit-content-canary"
            authorized_content = b"authorized-content"
            changed_content = b"changed-content!!!"
            self.assertEqual(len(authorized_content), len(changed_content))
            target.write_bytes(authorized_content)
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            inventory = self.helper._bounded_library_inventory(account)
            self.assertIsNotNone(inventory)
            entries = inventory["entries"]
            authorized = {entry["path_sha256"]: entry for entry in entries}
            file_records = [entry for entry in entries if entry["kind"] == "file"]
            self.assertEqual(len(file_records), 1)
            self.assertEqual(
                file_records[0]["content_sha256"],
                hashlib.sha256(authorized_content).hexdigest(),
            )
            authorized_metadata = target.lstat()
            target.write_bytes(changed_content)
            changed_metadata = target.lstat()
            self.assertEqual(
                (
                    changed_metadata.st_dev,
                    changed_metadata.st_ino,
                    changed_metadata.st_nlink,
                    changed_metadata.st_size,
                ),
                (
                    authorized_metadata.st_dev,
                    authorized_metadata.st_ino,
                    authorized_metadata.st_nlink,
                    authorized_metadata.st_size,
                ),
            )
            file_records[0].update(
                {
                    "ctime_ns": changed_metadata.st_ctime_ns,
                    "link_count": changed_metadata.st_nlink,
                    "mtime_ns": changed_metadata.st_mtime_ns,
                    "size": changed_metadata.st_size,
                }
            )
            self.assertEqual(
                file_records[0]["content_sha256"],
                hashlib.sha256(authorized_content).hexdigest(),
            )

            descriptor = os.open(
                library,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                with (
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    mock.patch.object(self.helper.os, "fsync") as fsync,
                    mock.patch.object(
                        self.helper,
                        "_renameat_exclusive",
                    ) as rename,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._delete_authorized_library_directory(
                        descriptor,
                        (self.helper.HOME_LIBRARY_NAME,),
                        authorized,
                    )
            finally:
                os.close(descriptor)

            self.assertEqual(
                raised.exception.code,
                "home-library-inventory-drift",
            )
            self.assertIsNone(raised.exception.secondary_code)
            unlink.assert_not_called()
            rmdir.assert_not_called()
            fsync.assert_not_called()
            rename.assert_not_called()
            self.assertEqual(target.read_bytes(), changed_content)

    def test_bounded_library_delete_rejects_jit_directory_identity_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            library = home / "Library"
            target = library / "target-directory"
            target.mkdir(parents=True)
            home.chmod(0o700)
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            inventory = self.helper._bounded_library_inventory(account)
            self.assertIsNotNone(inventory)
            entries = inventory["entries"]
            authorized = {entry["path_sha256"]: entry for entry in entries}
            directory_records = [
                entry
                for entry in entries
                if entry["kind"] == "directory" and entry["depth"] == 2
            ]
            self.assertEqual(len(directory_records), 1)
            original_metadata = target.lstat()
            preserved = library / "zz-preserved-authorized-directory"
            target.rename(preserved)
            target.mkdir()
            replacement_metadata = target.lstat()
            self.assertEqual(
                (
                    replacement_metadata.st_dev,
                    replacement_metadata.st_mode,
                    replacement_metadata.st_uid,
                    replacement_metadata.st_gid,
                ),
                (
                    original_metadata.st_dev,
                    original_metadata.st_mode,
                    original_metadata.st_uid,
                    original_metadata.st_gid,
                ),
            )
            self.assertNotEqual(
                replacement_metadata.st_ino,
                original_metadata.st_ino,
            )

            descriptor = os.open(
                library,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                with (
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    mock.patch.object(self.helper.os, "fsync") as fsync,
                    mock.patch.object(
                        self.helper,
                        "_renameat_exclusive",
                    ) as rename,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._delete_authorized_library_directory(
                        descriptor,
                        (self.helper.HOME_LIBRARY_NAME,),
                        authorized,
                    )
            finally:
                os.close(descriptor)

            self.assertEqual(
                raised.exception.code,
                "home-library-inventory-drift",
            )
            self.assertIsNone(raised.exception.secondary_code)
            unlink.assert_not_called()
            rmdir.assert_not_called()
            fsync.assert_not_called()
            rename.assert_not_called()
            self.assertEqual(target.lstat().st_ino, replacement_metadata.st_ino)
            self.assertEqual(preserved.lstat().st_ino, original_metadata.st_ino)

    def test_bounded_library_delete_threads_file_and_directory_acl_causes(
        self,
    ) -> None:
        for kind in ("file", "directory"):
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                library = home / "Library"
                library.mkdir(parents=True)
                home.chmod(0o700)
                target = library / "private-acl-target-canary"
                if kind == "file":
                    target.write_bytes(b"private-acl-value-canary")
                else:
                    target.mkdir()
                account = self.helper.DisposableAccount(
                    name="twq-0123456789ab",
                    uid=os.geteuid(),
                    gid=os.getegid(),
                    home=home,
                )
                inventory = self.helper._bounded_library_inventory(account)
                self.assertIsNotNone(inventory)
                entries = inventory["entries"]
                authorized = {entry["path_sha256"]: entry for entry in entries}
                target_inode = target.lstat().st_ino
                causes: list[object] = []
                xattr_causes: list[object] = []

                def reject(
                    descriptor: int,
                    unsafe_cause: object = None,
                    target_inode: int = target_inode,
                    causes: list[object] = causes,
                ) -> None:
                    if os.fstat(descriptor).st_ino == target_inode:
                        causes.append(unsafe_cause)
                        raise self.helper._home_library_unsafe_error(unsafe_cause)

                def reject_xattr(
                    descriptor: int,
                    unsafe_cause: object = None,
                    target_inode: int = target_inode,
                    xattr_causes: list[object] = xattr_causes,
                    **_context: object,
                ) -> None:
                    if os.fstat(descriptor).st_ino == target_inode:
                        xattr_causes.append(unsafe_cause)
                        raise self.helper._home_library_unsafe_error(unsafe_cause)

                descriptor = os.open(
                    library,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                )
                try:
                    with (
                        mock.patch.object(
                            self.helper,
                            "_require_no_extended_acl",
                            side_effect=reject,
                        ),
                        mock.patch.object(
                            self.helper,
                            "_require_no_extended_attributes",
                            side_effect=reject_xattr,
                        ),
                        mock.patch.object(self.helper.os, "unlink") as unlink,
                        mock.patch.object(self.helper.os, "rmdir") as rmdir,
                        self.assertRaises(self.helper.ProbeError) as raised,
                    ):
                        self.helper._delete_authorized_library_directory(
                            descriptor,
                            (self.helper.HOME_LIBRARY_NAME,),
                            authorized,
                        )
                finally:
                    os.close(descriptor)

                expected = f"{kind}-acl"
                self.assertEqual(raised.exception.code, "home-library-unsafe-entry")
                self.assertEqual(
                    raised.exception.secondary_code,
                    f"home-library-unsafe-entry-{expected}",
                )
                self.assertEqual(causes, [expected])
                self.assertEqual(xattr_causes, [])
                unlink.assert_not_called()
                rmdir.assert_not_called()
                self.assertTrue(target.exists())

    def test_home_cleanup_authorization_rejects_home_acl_before_publication(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            home = root / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            plan = self.helper.LaunchdPlan(
                account=account,
                label="io.nisavid.task-witness.macos-probe.0123456789ab",
                stage_root=stage,
                helper=stage / "helper.py",
                plist=stage / "job.plist",
            )
            state = self.lifecycle_state(plan)
            account_binding = self.helper._account_binding_document(
                plan,
                state,
                "01234567-89AB-4DEF-8123-456789ABCDEF",
            )
            bindings = self.helper.ValidatedStageBindings(account_binding, None)
            home_metadata = home.lstat()
            probe_metadata = probe.lstat()
            home_identity = {
                "home_device": home_metadata.st_dev,
                "home_inode": home_metadata.st_ino,
                "probe_device": probe_metadata.st_dev,
                "probe_inode": probe_metadata.st_ino,
            }

            with (
                mock.patch.object(
                    self.helper,
                    "_require_no_extended_acl",
                    side_effect=self.helper.ProbeError("home-library-unsafe-entry"),
                ),
                mock.patch.object(
                    self.helper,
                    "_write_stage_create_new",
                ) as write,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._write_home_cleanup_authorization(
                    plan,
                    state,
                    bindings,
                    home_identity,
                )

            self.assertEqual(raised.exception.code, "home-library-unsafe-entry")
            write.assert_not_called()
            self.assertFalse((stage / "home-cleanup.json").exists())
            self.assertTrue(home.is_dir())

    def test_bounded_library_quarantine_is_exclusive_durable_and_descriptor_relative(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account, _identity, authorization, _relative_files = (
                self.bounded_library_cleanup_fixture(root)
            )
            home_inode = account.home.lstat().st_ino
            events: list[tuple[str, object, object]] = []
            original_fsync = os.fsync
            original_unlink = os.unlink
            original_rmdir = os.rmdir

            def rename(
                source_parent_descriptor: int,
                source_name: str,
                destination_parent_descriptor: int,
                destination_name: str,
            ) -> None:
                events.append(("rename", source_name, source_parent_descriptor))
                self.rename_library_portably(
                    source_parent_descriptor,
                    source_name,
                    destination_parent_descriptor,
                    destination_name,
                )

            def fsync(descriptor: int) -> None:
                events.append(("fsync", os.fstat(descriptor).st_ino, descriptor))
                original_fsync(descriptor)

            def unlink(name: str, *, dir_fd: int | None = None) -> None:
                self.assertIsNotNone(dir_fd)
                self.assertNotIn("/", name)
                events.append(("unlink", name, dir_fd))
                original_unlink(name, dir_fd=dir_fd)

            def rmdir(name: str, *, dir_fd: int | None = None) -> None:
                self.assertIsNotNone(dir_fd)
                self.assertNotIn("/", name)
                events.append(("rmdir", name, dir_fd))
                original_rmdir(name, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    self.helper,
                    "_renameat_exclusive",
                    side_effect=rename,
                ) as exclusive,
                mock.patch.object(
                    Path,
                    "unlink",
                    side_effect=AssertionError(
                        "Library cleanup must not use Path.unlink"
                    ),
                ),
                mock.patch.object(
                    Path,
                    "rmdir",
                    side_effect=AssertionError(
                        "Library cleanup must not use Path.rmdir"
                    ),
                ),
                mock.patch.object(self.helper.os, "fsync", side_effect=fsync),
                mock.patch.object(self.helper.os, "unlink", side_effect=unlink),
                mock.patch.object(self.helper.os, "rmdir", side_effect=rmdir),
            ):
                self.helper._quarantine_and_remove_bounded_library(
                    account,
                    authorization,
                )

            exclusive.assert_called_once()
            self.assertEqual(events[0][0], "rename")
            first_delete = next(
                index
                for index, event in enumerate(events)
                if event[0] in {"unlink", "rmdir"}
            )
            first_home_fsync = next(
                index
                for index, event in enumerate(events)
                if event[:2] == ("fsync", home_inode)
            )
            self.assertLess(0, first_home_fsync)
            self.assertLess(first_home_fsync, first_delete)
            mutations = [(event[0], event[1]) for event in events]
            self.assertLess(
                mutations.index(("unlink", "payload.bin")),
                mutations.index(("rmdir", "Deep")),
            )
            self.assertLess(
                mutations.index(("rmdir", "Deep")),
                mutations.index(("rmdir", "Container")),
            )
            self.assertLess(
                mutations.index(("rmdir", "Container")),
                mutations.index(("rmdir", self.helper.HOME_LIBRARY_QUARANTINE_NAME)),
            )
            self.assertFalse((account.home / "Library").exists())
            self.assertFalse(
                (account.home / self.helper.HOME_LIBRARY_QUARANTINE_NAME).exists()
            )
            self.assertTrue((account.home / "launchd-probe").is_dir())

    def test_bounded_library_quarantine_replays_exact_remaining_subset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account, _identity, authorization, relative_files = (
                self.bounded_library_cleanup_fixture(root)
            )
            home_inode = account.home.lstat().st_ino
            source = account.home / self.helper.HOME_LIBRARY_NAME
            quarantine = account.home / self.helper.HOME_LIBRARY_QUARANTINE_NAME
            source.rename(quarantine)
            removed = quarantine.joinpath(*relative_files[0])
            removed.unlink()
            events: list[tuple[str, object]] = []
            original_fsync = os.fsync
            original_unlink = os.unlink
            original_rmdir = os.rmdir

            def fsync(descriptor: int) -> None:
                events.append(("fsync", os.fstat(descriptor).st_ino))
                original_fsync(descriptor)

            def unlink(name: str, *, dir_fd: int | None = None) -> None:
                events.append(("unlink", name))
                original_unlink(name, dir_fd=dir_fd)

            def rmdir(name: str, *, dir_fd: int | None = None) -> None:
                events.append(("rmdir", name))
                original_rmdir(name, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    self.helper,
                    "_renameat_exclusive",
                ) as rename,
                mock.patch.object(
                    Path,
                    "unlink",
                    side_effect=AssertionError(
                        "Library replay must not use Path.unlink"
                    ),
                ),
                mock.patch.object(
                    Path,
                    "rmdir",
                    side_effect=AssertionError(
                        "Library replay must not use Path.rmdir"
                    ),
                ),
                mock.patch.object(self.helper.os, "fsync", side_effect=fsync),
                mock.patch.object(self.helper.os, "unlink", side_effect=unlink),
                mock.patch.object(self.helper.os, "rmdir", side_effect=rmdir),
            ):
                self.helper._quarantine_and_remove_bounded_library(
                    account,
                    authorization,
                )

            rename.assert_not_called()
            first_delete = next(
                index
                for index, event in enumerate(events)
                if event[0] in {"unlink", "rmdir"}
            )
            first_home_fsync = events.index(("fsync", home_inode))
            self.assertLess(first_home_fsync, first_delete)
            self.assertFalse(source.exists())
            self.assertFalse(quarantine.exists())
            self.assertFalse(removed.exists())
            self.assertTrue((account.home / "launchd-probe").is_dir())

    def test_bounded_library_quarantine_replay_fsync_failure_preserves_tree(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account, _identity, authorization, relative_files = (
                self.bounded_library_cleanup_fixture(root)
            )
            source = account.home / self.helper.HOME_LIBRARY_NAME
            quarantine = account.home / self.helper.HOME_LIBRARY_QUARANTINE_NAME
            source.rename(quarantine)
            canary = quarantine.joinpath(*relative_files[0])

            with (
                mock.patch.object(self.helper.os, "fsync", side_effect=OSError),
                mock.patch.object(self.helper.os, "unlink") as unlink,
                mock.patch.object(self.helper.os, "rmdir") as rmdir,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._quarantine_and_remove_bounded_library(
                    account,
                    authorization,
                )

            self.assertEqual(
                raised.exception.code,
                "home-library-quarantine-failed",
            )
            unlink.assert_not_called()
            rmdir.assert_not_called()
            self.assertFalse(source.exists())
            self.assertTrue(quarantine.is_dir())
            self.assertTrue(canary.is_file())

    def test_bounded_library_quarantine_preserves_conflicting_and_drifted_states(
        self,
    ) -> None:
        scenarios = (
            ("both", "home-library-quarantine-drift"),
            ("inserted-source", "home-library-inventory-drift"),
            ("inserted-quarantine", "home-library-inventory-drift"),
            ("replaced-symlink", "home-library-unsafe-entry"),
        )
        for scenario, expected_code in scenarios:
            with (
                self.subTest(scenario=scenario),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                account, _identity, authorization, relative_files = (
                    self.bounded_library_cleanup_fixture(root)
                )
                source = account.home / self.helper.HOME_LIBRARY_NAME
                quarantine = account.home / self.helper.HOME_LIBRARY_QUARANTINE_NAME
                canary: Path
                external = root / "external-preserve-canary"
                external.write_bytes(b"preserve")
                if scenario == "both":
                    quarantine.mkdir()
                    canary = quarantine
                elif scenario == "inserted-source":
                    canary = source / "private-inserted-canary"
                    canary.write_bytes(b"preserve")
                elif scenario == "inserted-quarantine":
                    source.rename(quarantine)
                    canary = quarantine / "private-inserted-canary"
                    canary.write_bytes(b"preserve")
                else:
                    canary = source.joinpath(*relative_files[0])
                    canary.unlink()
                    canary.symlink_to(external)

                with (
                    mock.patch.object(
                        self.helper,
                        "_renameat_exclusive",
                    ) as rename,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._quarantine_and_remove_bounded_library(
                        account,
                        authorization,
                    )

                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn("private", raised.exception.code)
                rename.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                self.assertTrue(canary.exists() or canary.is_symlink())
                self.assertEqual(external.read_bytes(), b"preserve")

    def test_bounded_library_quarantine_rejects_safe_mode_drift_without_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            account, _identity, authorization, relative_files = (
                self.bounded_library_cleanup_fixture(Path(directory))
            )
            source = account.home / self.helper.HOME_LIBRARY_NAME
            target = source.joinpath(*relative_files[1])
            target.chmod(0o600)

            with (
                mock.patch.object(self.helper, "_renameat_exclusive") as rename,
                mock.patch.object(self.helper.os, "unlink") as unlink,
                mock.patch.object(self.helper.os, "rmdir") as rmdir,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._quarantine_and_remove_bounded_library(
                    account,
                    authorization,
                )

            self.assertEqual(raised.exception.code, "home-library-inventory-drift")
            rename.assert_not_called()
            unlink.assert_not_called()
            rmdir.assert_not_called()
            self.assertTrue(target.is_file())
            self.assertEqual(stat.S_IMODE(target.lstat().st_mode), 0o600)

    def test_bounded_library_observer_requires_authorized_home_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account, identity, _authorization, _relative_files = (
                self.bounded_library_cleanup_fixture(root)
            )
            foreign = root / "foreign-home-identity"
            foreign.mkdir(mode=0o700)
            changed_identity = {
                **identity,
                "home_inode": foreign.lstat().st_ino,
            }

            with self.assertRaises(self.helper.ProbeError) as raised:
                self.helper._observe_bounded_library(
                    account,
                    self.helper.HOME_LIBRARY_NAME,
                    changed_identity,
                )

            self.assertEqual(raised.exception.code, "home-library-home-drift")
            self.assertTrue((account.home / self.helper.HOME_LIBRARY_NAME).is_dir())

    def test_bounded_library_cleanup_rejects_home_acl_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account, _identity, authorization, relative_files = (
                self.bounded_library_cleanup_fixture(root)
            )
            home_inode = account.home.lstat().st_ino

            def reject_home_acl(
                descriptor: int,
                unsafe_cause: object = None,
            ) -> None:
                if os.fstat(descriptor).st_ino == home_inode:
                    raise self.helper.ProbeError("home-library-unsafe-entry")

            with (
                mock.patch.object(
                    self.helper,
                    "_require_no_extended_acl",
                    side_effect=reject_home_acl,
                ),
                mock.patch.object(
                    self.helper,
                    "_renameat_exclusive",
                ) as rename,
                mock.patch.object(self.helper.os, "unlink") as unlink,
                mock.patch.object(self.helper.os, "rmdir") as rmdir,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._quarantine_and_remove_bounded_library(
                    account,
                    authorization,
                )

            self.assertEqual(raised.exception.code, "home-library-unsafe-entry")
            rename.assert_not_called()
            unlink.assert_not_called()
            rmdir.assert_not_called()
            library = account.home / self.helper.HOME_LIBRARY_NAME
            self.assertTrue(library.joinpath(*relative_files[0]).is_file())
            self.assertTrue(library.joinpath(*relative_files[1]).is_file())

    def test_bounded_library_cleanup_revalidates_home_before_each_mutation(
        self,
    ) -> None:
        for boundary in ("rename", "delete"):
            with (
                self.subTest(boundary=boundary),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                account, _identity, authorization, relative_files = (
                    self.bounded_library_cleanup_fixture(root)
                )
                source = account.home / self.helper.HOME_LIBRARY_NAME
                quarantine = account.home / self.helper.HOME_LIBRARY_QUARANTINE_NAME
                leaf_name = self.helper.HOME_LIBRARY_NAME
                if boundary == "delete":
                    source.rename(quarantine)
                    leaf_name = self.helper.HOME_LIBRARY_QUARANTINE_NAME
                authorized_home = root / "authorized-home"
                replacement_canary = account.home / "private-replacement-canary"
                original_stable = self.helper._stable_observed_library

                def observe_then_replace(
                    *args: object,
                    _original=original_stable,
                    _account=account,
                    _authorized_home=authorized_home,
                    _replacement_canary=replacement_canary,
                    **kwargs: object,
                ) -> dict:
                    observed = _original(*args, **kwargs)
                    _account.home.rename(_authorized_home)
                    _account.home.mkdir(mode=0o700)
                    _replacement_canary.write_bytes(b"preserve")
                    return observed

                with (
                    mock.patch.object(
                        self.helper,
                        "_stable_observed_library",
                        side_effect=observe_then_replace,
                    ) as stable,
                    mock.patch.object(
                        self.helper,
                        "_renameat_exclusive",
                    ) as rename,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._quarantine_and_remove_bounded_library(
                        account,
                        authorization,
                    )

                self.assertEqual(raised.exception.code, "home-library-home-drift")
                stable.assert_called_once_with(
                    account,
                    leaf_name,
                    authorization["library_inventory"],
                    authorization["home_identity"],
                    exact=boundary == "rename",
                    diagnostic_phase=(
                        "source-revalidation"
                        if boundary == "rename"
                        else "quarantine-revalidation"
                    ),
                )
                rename.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                self.assertTrue(replacement_canary.is_file())
                self.assertEqual(replacement_canary.read_bytes(), b"preserve")
                preserved_root = authorized_home / leaf_name
                self.assertTrue(preserved_root.is_dir())
                self.assertTrue(preserved_root.joinpath(*relative_files[0]).is_file())

    def test_bounded_library_quarantine_resumes_after_interruptions(self) -> None:
        for interrupt_at in ("rename", "nested-unlink"):
            with (
                self.subTest(interrupt_at=interrupt_at),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                account, _identity, authorization, _relative_files = (
                    self.bounded_library_cleanup_fixture(root)
                )
                source = account.home / self.helper.HOME_LIBRARY_NAME
                quarantine = account.home / self.helper.HOME_LIBRARY_QUARANTINE_NAME
                original_unlink = os.unlink
                unlinked = 0

                def rename_then_interrupt(
                    source_parent_descriptor: int,
                    source_name: str,
                    destination_parent_descriptor: int,
                    destination_name: str,
                ) -> None:
                    self.rename_library_portably(
                        source_parent_descriptor,
                        source_name,
                        destination_parent_descriptor,
                        destination_name,
                    )
                    raise KeyboardInterrupt

                def unlink_then_interrupt(
                    name: str,
                    *,
                    dir_fd: int | None = None,
                    operation=original_unlink,
                ) -> None:
                    nonlocal unlinked
                    operation(name, dir_fd=dir_fd)
                    unlinked += 1
                    if unlinked == 1:
                        raise KeyboardInterrupt

                rename_effect = (
                    rename_then_interrupt
                    if interrupt_at == "rename"
                    else self.rename_library_portably
                )
                with (
                    mock.patch.object(
                        self.helper,
                        "_renameat_exclusive",
                        side_effect=rename_effect,
                    ),
                    mock.patch.object(
                        Path,
                        "unlink",
                        side_effect=AssertionError(
                            "Library cleanup must not use Path.unlink"
                        ),
                    ),
                    mock.patch.object(
                        Path,
                        "rmdir",
                        side_effect=AssertionError(
                            "Library cleanup must not use Path.rmdir"
                        ),
                    ),
                    mock.patch.object(
                        self.helper.os,
                        "unlink",
                        side_effect=(
                            unlink_then_interrupt
                            if interrupt_at == "nested-unlink"
                            else original_unlink
                        ),
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    self.helper._quarantine_and_remove_bounded_library(
                        account,
                        authorization,
                    )

                self.assertFalse(source.exists())
                self.assertTrue(quarantine.is_dir())
                if interrupt_at == "nested-unlink":
                    self.assertEqual(unlinked, 1)

                with (
                    mock.patch.object(
                        self.helper,
                        "_renameat_exclusive",
                    ) as replay_rename,
                    mock.patch.object(
                        Path,
                        "unlink",
                        side_effect=AssertionError(
                            "Library replay must not use Path.unlink"
                        ),
                    ),
                    mock.patch.object(
                        Path,
                        "rmdir",
                        side_effect=AssertionError(
                            "Library replay must not use Path.rmdir"
                        ),
                    ),
                ):
                    self.helper._quarantine_and_remove_bounded_library(
                        account,
                        authorization,
                    )

                replay_rename.assert_not_called()
                self.assertFalse(quarantine.exists())
                self.assertEqual(
                    self.helper._home_cleanup_evidence(
                        authorization,
                        recovered=True,
                    )["disposition"],
                    "recovered",
                )

    def test_marker_bound_home_cleanup_keeps_library_deletion_descriptor_relative(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account, identity, authorization, _relative_files = (
                self.bounded_library_cleanup_fixture(root)
            )
            original_unlink = Path.unlink
            original_rmdir = Path.rmdir
            path_mutations: list[Path] = []

            def unlink(path: Path, *args: object, **kwargs: object) -> None:
                self.assertNotIn(self.helper.HOME_LIBRARY_NAME, path.parts)
                self.assertNotIn(
                    self.helper.HOME_LIBRARY_QUARANTINE_NAME,
                    path.parts,
                )
                path_mutations.append(path)
                original_unlink(path, *args, **kwargs)

            def rmdir(path: Path) -> None:
                self.assertNotIn(self.helper.HOME_LIBRARY_NAME, path.parts)
                self.assertNotIn(
                    self.helper.HOME_LIBRARY_QUARANTINE_NAME,
                    path.parts,
                )
                path_mutations.append(path)
                original_rmdir(path)

            with (
                mock.patch.object(
                    self.helper,
                    "_renameat_exclusive",
                    side_effect=self.rename_library_portably,
                ),
                mock.patch.object(Path, "unlink", unlink),
                mock.patch.object(Path, "rmdir", rmdir),
            ):
                self.helper._remove_marker_bound_disposable_home(
                    account,
                    identity,
                    authorization,
                )

            self.assertFalse(account.home.exists())
            self.assertEqual(
                {path.name for path in path_mutations},
                set(self.helper.LAUNCHD_CHILD_FILES) | {"launchd-probe", "home"},
            )

    def test_marker_bound_home_cleanup_rejects_home_acl_without_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            for name in self.helper.LAUNCHD_CHILD_FILES:
                (probe / name).write_bytes(b"preserve")
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            home_metadata = home.lstat()
            probe_metadata = probe.lstat()
            identity = {
                "home_device": home_metadata.st_dev,
                "home_inode": home_metadata.st_ino,
                "probe_device": probe_metadata.st_dev,
                "probe_inode": probe_metadata.st_ino,
            }
            authorization = {
                "disposition": "armed",
                "account": {
                    "name": account.name,
                    "uid": account.uid,
                    "gid": account.gid,
                    "home": str(account.home),
                },
                "home_identity": identity,
                "library_inventory": "none",
            }

            with (
                mock.patch.object(
                    self.helper,
                    "_require_no_extended_acl",
                    side_effect=self.helper.ProbeError("home-library-unsafe-entry"),
                ),
                mock.patch.object(Path, "unlink") as unlink,
                mock.patch.object(Path, "rmdir") as rmdir,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._remove_marker_bound_disposable_home(
                    account,
                    identity,
                    authorization,
                )

            self.assertEqual(raised.exception.code, "home-library-unsafe-entry")
            unlink.assert_not_called()
            rmdir.assert_not_called()
            self.assertTrue(home.is_dir())
            self.assertEqual(
                {entry.name for entry in probe.iterdir()},
                set(self.helper.LAUNCHD_CHILD_FILES),
            )

    def test_home_cleanup_preserves_everything_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            for name in ("probe.json", "probe.status", "probe.stderr", "probe.stdout"):
                (probe / name).write_bytes(b"value")
            (probe / "foreign").write_bytes(b"preserve")

            with self.assertRaises(self.helper.ProbeError) as raised:
                self.helper.remove_exact_disposable_home(
                    home,
                    expected_uid=os.geteuid(),
                    expected_gid=os.getegid(),
                )
            self.assertEqual(
                raised.exception.code,
                "home-cleanup-home-removal-probe-entry-set-drift",
            )
            self.assertTrue(home.is_dir())
            self.assertTrue((probe / "foreign").is_file())

    def test_exact_home_cleanup_rejects_partial_children_before_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            missing = min(self.helper.LAUNCHD_CHILD_FILES)
            for name in set(self.helper.LAUNCHD_CHILD_FILES) - {missing}:
                (probe / name).write_bytes(b"preserve")

            with ExitStack() as stack:
                mutations = self.guard_home_validation_mutations(stack)
                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper.remove_exact_disposable_home(
                        home,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                    )

            self.assertEqual(
                raised.exception.code,
                "home-cleanup-home-removal-probe-entry-set-drift",
            )
            for mutation in mutations:
                mutation.assert_not_called()
            self.assertTrue(home.is_dir())
            self.assertEqual(
                {entry.name for entry in probe.iterdir()},
                set(self.helper.LAUNCHD_CHILD_FILES) - {missing},
            )

    def test_home_drift_diagnostics_are_phase_bound_and_value_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            for name in self.helper.LAUNCHD_CHILD_FILES:
                (probe / name).write_bytes(b"value")
            home_metadata = home.lstat()
            probe_metadata = probe.lstat()
            identity = {
                "home_device": home_metadata.st_dev,
                "home_inode": home_metadata.st_ino,
                "probe_device": probe_metadata.st_dev,
                "probe_inode": probe_metadata.st_ino,
            }
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            canary = "private-home-entry-canary"
            (home / canary).mkdir()

            with self.assertRaises(self.helper.ProbeError) as exact:
                self.helper._validate_exact_disposable_home(
                    home,
                    expected_uid=account.uid,
                    expected_gid=account.gid,
                    diagnostic_phase="pre-reset-marker",
                )
            self.assertEqual(
                exact.exception.code,
                (
                    "home-cleanup-pre-reset-marker-"
                    "home-entry-single-owned-directory-other"
                ),
            )
            self.assertNotIn(canary, exact.exception.code)

            with self.assertRaises(self.helper.ProbeError) as bound:
                self.helper._validated_marker_bound_disposable_home(
                    account,
                    identity,
                    diagnostic_phase="post-reset",
                )
            self.assertEqual(
                bound.exception.code,
                "home-cleanup-post-reset-home-entry-single-owned-directory-other",
            )
            self.assertNotIn(canary, bound.exception.code)

    def test_home_entry_diagnostics_classify_fixed_root_families(self) -> None:
        def library(home: Path) -> None:
            (home / "Library").mkdir()

        def text_encoding(home: Path) -> None:
            (home / ".CFUserTextEncoding").write_bytes(b"0:0")

        def both(home: Path) -> None:
            library(home)
            text_encoding(home)

        def unknown_directory(home: Path) -> None:
            (home / "private-directory-canary").mkdir()

        def unknown_file(home: Path) -> None:
            (home / "private-file-canary").write_bytes(b"private")

        def mixed(home: Path) -> None:
            unknown_directory(home)
            unknown_file(home)

        def unsafe(home: Path) -> None:
            (home / "private-symlink-canary").symlink_to(home / "launchd-probe")

        scenarios = (
            (library, "home-entry-known-library-owned-directory"),
            (text_encoding, "home-entry-known-text-encoding-owned-file"),
            (both, "home-entry-known-library-and-text-encoding"),
            (unknown_directory, "home-entry-single-owned-directory-other"),
            (unknown_file, "home-entry-single-owned-file-other"),
            (mixed, "home-entry-multiple-or-mixed"),
            (unsafe, "home-entry-unsafe-or-foreign"),
        )
        for arrange, detail in scenarios:
            with (
                self.subTest(detail=detail),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                probe = home / "launchd-probe"
                probe.mkdir(parents=True, mode=0o700)
                home.chmod(0o700)
                probe.chmod(0o700)
                for name in self.helper.LAUNCHD_CHILD_FILES:
                    (probe / name).write_bytes(b"value")
                arrange(home)

                if detail == "home-entry-known-library-owned-directory":
                    self.helper._validate_exact_disposable_home(
                        home,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        diagnostic_phase="pre-reset-marker",
                    )
                    continue

                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper._validate_exact_disposable_home(
                        home,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        diagnostic_phase="pre-reset-marker",
                    )

                self.assertEqual(
                    raised.exception.code,
                    f"home-cleanup-pre-reset-marker-{detail}",
                )
                self.assertNotIn("private", raised.exception.code)

    def test_disposable_directory_metadata_compaction_seam_preserves_precedence(
        self,
    ) -> None:
        phase = "pre-reset-marker"
        uid, gid = os.geteuid(), os.getegid()
        for node in ("home", "probe"):
            states = (
                (
                    SimpleNamespace(
                        st_mode=stat.S_IFREG | 0o755,
                        st_uid=uid + 1,
                        st_gid=gid + 1,
                    ),
                    "kind-drift",
                ),
                (
                    SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o755,
                        st_uid=uid + 1,
                        st_gid=gid + 1,
                    ),
                    "mode-drift",
                ),
                (
                    SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o700,
                        st_uid=uid + 1,
                        st_gid=gid + 1,
                    ),
                    "owner-drift",
                ),
            )
            with self.subTest(node=node), ExitStack() as stack:
                mutations = self.guard_home_validation_mutations(stack)
                for metadata, detail in states:
                    with self.assertRaises(self.helper.ProbeError) as raised:
                        self.helper._require_disposable_directory_metadata(
                            node, metadata, uid, gid, phase
                        )
                    self.assertEqual(
                        raised.exception.code,
                        f"home-cleanup-{phase}-{node}-{detail}",
                    )
                self.assertIsNone(
                    self.helper._require_disposable_directory_metadata(
                        node,
                        SimpleNamespace(
                            st_mode=stat.S_IFDIR | 0o700,
                            st_uid=uid,
                            st_gid=gid,
                        ),
                        uid,
                        gid,
                        phase,
                    )
                )
                for mutation in mutations:
                    mutation.assert_not_called()

    def test_disposable_home_names_compaction_seam_preserves_validation_order(
        self,
    ) -> None:
        phase = "pre-reset-marker"
        uid, gid = os.geteuid(), os.getegid()
        home = Path("/private/tmp/private-home-name-canary")
        metadata = SimpleNamespace(st_dev=7, st_ino=8)
        cases = (
            (["Library", "launchd-probe"], None, ["enumerate", "allow"]),
            (
                ["Library", "private-extra-canary"],
                "probe-missing",
                ["enumerate"],
            ),
        )
        for listing, detail, expected_events in cases:
            events: list[str] = []

            def enumerate_names(
                *_args: object,
                _events: list[str] = events,
                _listing: list[str] = listing,
                **_kwargs: object,
            ) -> list[str]:
                _events.append("enumerate")
                return _listing

            def allow_names(
                *_args: object,
                _events: list[str] = events,
                **_kwargs: object,
            ) -> None:
                _events.append("allow")

            with self.subTest(detail=detail), ExitStack() as stack:
                mutations = self.guard_home_validation_mutations(stack)
                bounded = stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_bounded_path_directory_names",
                        side_effect=enumerate_names,
                    )
                )
                allowed = stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_require_allowed_disposable_home_names",
                        side_effect=allow_names,
                    )
                )
                if detail is None:
                    self.assertEqual(
                        self.helper._validated_disposable_home_names(
                            home, metadata, uid, gid, phase
                        ),
                        set(listing),
                    )
                    allowed.assert_called_once_with(
                        home,
                        set(listing),
                        expected_uid=uid,
                        expected_gid=gid,
                        diagnostic_phase=phase,
                    )
                else:
                    with self.assertRaises(self.helper.ProbeError) as raised:
                        self.helper._validated_disposable_home_names(
                            home, metadata, uid, gid, phase
                        )
                    self.assertEqual(
                        raised.exception.code,
                        f"home-cleanup-{phase}-{detail}",
                    )
                    self.assertNotIn("private", raised.exception.code)
                    allowed.assert_not_called()
                self.assertEqual(events, expected_events)
                bounded.assert_called_once_with(
                    home,
                    metadata,
                    self.helper.MAX_DISPOSABLE_HOME_ENTRIES,
                    limit_code=(
                        f"home-cleanup-{phase}-home-entry-observation-unreadable"
                    ),
                    failure_code=f"home-cleanup-{phase}-home-read-failed",
                )
                for mutation in mutations:
                    mutation.assert_not_called()

    def test_launchd_probe_children_compaction_seam_preserves_contract(
        self,
    ) -> None:
        phase = "pre-reset-marker"
        uid, gid = os.geteuid(), os.getegid()
        probe_root = Path("/private/tmp/private-probe-root-canary")
        probe_metadata = SimpleNamespace(st_dev=7, st_ino=8)
        expected = tuple(sorted(self.helper.LAUNCHD_CHILD_FILES))
        expected_set = set(expected)
        set_drift = f"home-cleanup-{phase}-probe-entry-set-drift"

        def valid_metadata(path: Path) -> SimpleNamespace:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_nlink=1,
                st_uid=uid,
                st_gid=gid,
                st_size=self.helper.LAUNCHD_CHILD_FILES[path.name],
            )

        def exercise(
            names: set[str],
            allow_subset: bool,
            bad: SimpleNamespace | None = None,
        ) -> tuple[object, list[str]]:
            target = expected[0]

            def metadata(path: Path) -> SimpleNamespace:
                return (
                    bad
                    if bad is not None and path.name == target
                    else valid_metadata(path)
                )

            with ExitStack() as stack:
                mutations = self.guard_home_validation_mutations(stack)
                bounded = stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_bounded_path_directory_names",
                        return_value=sorted(names, reverse=True),
                    )
                )
                lstat = stack.enter_context(
                    mock.patch.object(
                        Path,
                        "lstat",
                        autospec=True,
                        side_effect=metadata,
                    )
                )
                try:
                    outcome: object = self.helper._validated_launchd_probe_children(
                        probe_root,
                        probe_metadata,
                        uid,
                        gid,
                        phase,
                        allow_subset=allow_subset,
                    )
                except self.helper.ProbeError as error:
                    outcome = error
                bounded.assert_called_once_with(
                    probe_root,
                    probe_metadata,
                    self.helper.MAX_LAUNCHD_PROBE_ENTRIES,
                    limit_code=set_drift,
                    failure_code=f"home-cleanup-{phase}-probe-read-failed",
                )
                for mutation in mutations:
                    mutation.assert_not_called()
                return outcome, [call.args[0].name for call in lstat.call_args_list]

        for mask in range(1 << len(expected)):
            names = {name for index, name in enumerate(expected) if mask & (1 << index)}
            for allow_subset in (False, True):
                with self.subTest(
                    names=tuple(sorted(names)), allow_subset=allow_subset
                ):
                    outcome, observed = exercise(names, allow_subset)
                    if allow_subset or names == expected_set:
                        self.assertEqual(outcome, tuple(sorted(names)))
                        self.assertEqual(observed, sorted(names))
                    else:
                        self.assertIsInstance(outcome, self.helper.ProbeError)
                        self.assertEqual(outcome.code, set_drift)
                        self.assertEqual(observed, [])

        extra = "private-extra-canary"
        for names in ({extra}, expected_set | {extra}):
            for allow_subset in (False, True):
                outcome, observed = exercise(names, allow_subset)
                self.assertIsInstance(outcome, self.helper.ProbeError)
                self.assertEqual(outcome.code, set_drift)
                self.assertNotIn("private", outcome.code)
                self.assertEqual(observed, [])

        target = expected[0]
        limit = self.helper.LAUNCHD_CHILD_FILES[target]
        states = (
            (
                SimpleNamespace(
                    st_mode=stat.S_IFDIR | 0o700,
                    st_nlink=2,
                    st_uid=uid + 1,
                    st_gid=gid + 1,
                    st_size=limit + 1,
                ),
                "probe-child-kind-drift",
                "kind",
            ),
            (
                SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o600,
                    st_nlink=2,
                    st_uid=uid + 1,
                    st_gid=gid + 1,
                    st_size=limit + 1,
                ),
                "probe-child-link-count-drift",
                "link-count",
            ),
            (
                SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o600,
                    st_nlink=1,
                    st_uid=uid + 1,
                    st_gid=gid,
                    st_size=limit + 1,
                ),
                "probe-child-owner-drift",
                "uid",
            ),
            (
                SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o600,
                    st_nlink=1,
                    st_uid=uid,
                    st_gid=gid + 1,
                    st_size=limit + 1,
                ),
                "probe-child-owner-drift",
                "gid",
            ),
            (
                SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o600,
                    st_nlink=1,
                    st_uid=uid,
                    st_gid=gid,
                    st_size=limit + 1,
                ),
                "probe-child-size-drift",
                "size",
            ),
        )
        for metadata, detail, predicate in states:
            with self.subTest(predicate=predicate):
                outcome, observed = exercise(expected_set, False, metadata)
                self.assertIsInstance(outcome, self.helper.ProbeError)
                self.assertEqual(
                    outcome.code,
                    f"home-cleanup-{phase}-{detail}",
                )
                self.assertEqual(observed, [target])

    def test_disposable_home_root_validator_checks_real_anchor_state(self) -> None:
        def validate(
            arrange,
            expected_detail: str | None,
            *,
            uid_offset: int = 0,
            gid_offset: int = 0,
        ) -> None:
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / "home"
                probe = home / "launchd-probe"
                probe.mkdir(parents=True, mode=0o700)
                home.chmod(0o700)
                probe.chmod(0o700)
                arrange(home, probe)
                account = self.helper.DisposableAccount(
                    name="task-witness-home-root-test",
                    uid=os.geteuid() + uid_offset,
                    gid=os.getegid() + gid_offset,
                    home=home,
                )
                if expected_detail is None:
                    self.helper._validate_disposable_home_root(
                        account,
                        diagnostic_phase="post-home-create",
                    )
                    return
                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper._validate_disposable_home_root(
                        account,
                        diagnostic_phase="post-home-create",
                    )
                self.assertEqual(
                    raised.exception.code,
                    f"home-cleanup-post-home-create-{expected_detail}",
                )
                self.assertNotIn(account.name, raised.exception.code)

        scenarios = (
            ("valid", lambda _home, _probe: None, None, 0, 0),
            (
                "known-library",
                lambda home, _probe: (home / "Library").mkdir(),
                "home-entry-known-library-owned-directory",
                0,
                0,
            ),
            (
                "home-mode",
                lambda home, _probe: home.chmod(0o755),
                "home-mode-drift",
                0,
                0,
            ),
            (
                "home-uid",
                lambda _home, _probe: None,
                "home-owner-drift",
                1,
                0,
            ),
            (
                "home-gid",
                lambda _home, _probe: None,
                "home-owner-drift",
                0,
                1,
            ),
            (
                "probe-mode",
                lambda _home, probe: probe.chmod(0o755),
                "probe-mode-drift",
                0,
                0,
            ),
            (
                "probe-kind",
                lambda _home, probe: (probe.rmdir(), probe.write_bytes(b"private")),
                "probe-kind-drift",
                0,
                0,
            ),
            (
                "probe-missing",
                lambda _home, probe: probe.rmdir(),
                "probe-missing",
                0,
                0,
            ),
        )
        for label, arrange, detail, uid_offset, gid_offset in scenarios:
            with self.subTest(label=label):
                validate(
                    arrange,
                    detail,
                    uid_offset=uid_offset,
                    gid_offset=gid_offset,
                )

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            account = self.helper.DisposableAccount(
                name="task-witness-home-root-test",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            path_type = type(probe)
            original_lstat = path_type.lstat

            def drift_probe_owner(path: Path):
                metadata = original_lstat(path)
                if path != probe:
                    return metadata
                values = list(metadata)
                values[4] = metadata.st_uid + 1
                return os.stat_result(values)

            with (
                mock.patch.object(
                    path_type,
                    "lstat",
                    autospec=True,
                    side_effect=drift_probe_owner,
                ),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._validate_disposable_home_root(
                    account,
                    diagnostic_phase="post-home-create",
                )
            self.assertEqual(
                raised.exception.code,
                "home-cleanup-post-home-create-probe-owner-drift",
            )

    def test_home_entry_diagnostics_reject_unsafe_known_entry_metadata(
        self,
    ) -> None:
        scenarios = (
            ("library-uid", "Library", 7, False),
            ("library-gid", "Library", 8, False),
            ("library-device", "Library", 9, False),
            ("text-encoding-uid", ".CFUserTextEncoding", 7, False),
            ("text-encoding-gid", ".CFUserTextEncoding", 8, False),
            ("text-encoding-device", ".CFUserTextEncoding", 9, False),
            ("text-encoding-link-count", ".CFUserTextEncoding", 4, 2),
            (
                "text-encoding-size",
                ".CFUserTextEncoding",
                5,
                self.helper.MAX_HOME_ENTRY_OBSERVATION_FILE_BYTES + 1,
            ),
            ("text-encoding-content-proof", ".CFUserTextEncoding", 10, None),
        )
        for label, entry_name, field_index, replacement in scenarios:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                probe = home / "launchd-probe"
                probe.mkdir(parents=True, mode=0o700)
                home.chmod(0o700)
                probe.chmod(0o700)
                for name in self.helper.LAUNCHD_CHILD_FILES:
                    (probe / name).write_bytes(b"value")
                entry = home / entry_name
                if entry_name == "Library":
                    entry.mkdir()
                else:
                    entry.write_bytes(b"0:0")
                stable = self.helper._home_entry_observation_snapshot(
                    home,
                    expected_uid=os.geteuid(),
                    expected_gid=os.getegid(),
                )
                self.assertEqual(len(stable[2]), 1)
                mutated_entry = list(stable[2][0])
                mutated_entry[field_index] = replacement
                mutated = (stable[0], stable[1], (tuple(mutated_entry),))

                with (
                    mock.patch.object(
                        self.helper,
                        "_home_entry_observation_snapshot",
                        side_effect=(mutated, mutated),
                    ),
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_exact_disposable_home(
                        home,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        diagnostic_phase="pre-reset-marker",
                    )

                self.assertEqual(
                    raised.exception.code,
                    ("home-cleanup-pre-reset-marker-home-entry-unsafe-or-foreign"),
                )
                self.assertNotIn(label, raised.exception.code)

    def test_home_entry_snapshot_derives_unsafe_metadata_flags(self) -> None:
        scenarios = (
            ("private-uid-canary", 4),
            ("private-gid-canary", 5),
            ("private-device-canary", 2),
        )
        for label, stat_field in scenarios:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                probe = home / "launchd-probe"
                library = home / "Library"
                probe.mkdir(parents=True, mode=0o700)
                library.mkdir()
                home.chmod(0o700)
                probe.chmod(0o700)
                for name in self.helper.LAUNCHD_CHILD_FILES:
                    (probe / name).write_bytes(b"value")
                path_type = type(home)
                original_lstat = path_type.lstat

                def drift_library_metadata(
                    path: Path,
                    *,
                    field: int = stat_field,
                    operation=original_lstat,
                    target: Path = library,
                ):
                    metadata = operation(path)
                    if path != target:
                        return metadata
                    values = list(metadata)
                    values[field] += 1
                    return os.stat_result(values)

                with (
                    mock.patch.object(
                        path_type,
                        "lstat",
                        autospec=True,
                        side_effect=drift_library_metadata,
                    ),
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_exact_disposable_home(
                        home,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        diagnostic_phase="pre-reset-marker",
                    )

                self.assertEqual(
                    raised.exception.code,
                    ("home-cleanup-pre-reset-marker-home-entry-unsafe-or-foreign"),
                )
                self.assertNotIn(label, raised.exception.code)

    def test_home_entry_snapshot_does_not_read_foreign_file(self) -> None:
        scenarios = (
            ("private-uid-canary", 4),
            ("private-gid-canary", 5),
            ("private-device-canary", 2),
        )
        for label, stat_field in scenarios:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                probe = home / "launchd-probe"
                entry = home / ".CFUserTextEncoding"
                probe.mkdir(parents=True, mode=0o700)
                entry.write_bytes(b"0:0")
                home.chmod(0o700)
                probe.chmod(0o700)
                for name in self.helper.LAUNCHD_CHILD_FILES:
                    (probe / name).write_bytes(b"value")
                path_type = type(home)
                original_lstat = path_type.lstat

                def drift_entry_metadata(
                    path: Path,
                    *,
                    field: int = stat_field,
                    operation=original_lstat,
                    target: Path = entry,
                ):
                    metadata = operation(path)
                    if path != target:
                        return metadata
                    values = list(metadata)
                    values[field] += 1
                    return os.stat_result(values)

                with (
                    mock.patch.object(
                        path_type,
                        "lstat",
                        autospec=True,
                        side_effect=drift_entry_metadata,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_read_stable_regular_file",
                        wraps=self.helper._read_stable_regular_file,
                    ) as read,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_exact_disposable_home(
                        home,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        diagnostic_phase="pre-reset-marker",
                    )

                self.assertEqual(
                    raised.exception.code,
                    ("home-cleanup-pre-reset-marker-home-entry-unsafe-or-foreign"),
                )
                read.assert_not_called()
                self.assertNotIn(label, raised.exception.code)

    def test_stable_reader_rejects_replaced_metadata_before_read(self) -> None:
        scenarios = (
            ("uid", "st_uid"),
            ("gid", "st_gid"),
            ("device", "st_dev"),
        )
        for label, stat_field in scenarios:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                source = Path(directory) / "source"
                source.write_bytes(b"private-content-canary")
                expected_metadata = self.helper._stable_regular_file_metadata(
                    source.lstat()
                )
                original_fstat = os.fstat

                def drift_descriptor_metadata(
                    descriptor: int,
                    *,
                    field: str = stat_field,
                    operation=original_fstat,
                ):
                    metadata = operation(descriptor)
                    values = {
                        "st_dev": metadata.st_dev,
                        "st_ino": metadata.st_ino,
                        "st_mode": metadata.st_mode,
                        "st_nlink": metadata.st_nlink,
                        "st_size": metadata.st_size,
                        "st_mtime_ns": metadata.st_mtime_ns,
                        "st_uid": metadata.st_uid,
                        "st_gid": metadata.st_gid,
                    }
                    values[field] += 1
                    return SimpleNamespace(**values)

                with (
                    mock.patch.object(
                        os,
                        "fstat",
                        side_effect=drift_descriptor_metadata,
                    ),
                    mock.patch.object(os, "read", wraps=os.read) as read,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._read_stable_regular_file(
                        source,
                        1024,
                        "home-entry-observation",
                        expected_metadata=expected_metadata,
                    )

                self.assertEqual(
                    raised.exception.code,
                    "changed-home-entry-observation",
                )
                read.assert_not_called()
                self.assertNotIn("private", raised.exception.code)

    def test_stable_reader_rejects_metadata_drift_after_read(self) -> None:
        scenarios = tuple(
            ("descriptor", field)
            for field in ("st_uid", "st_gid", "st_mode", "st_nlink")
        ) + tuple(
            ("path", field)
            for field in (
                "st_uid",
                "st_gid",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
            )
        )
        for location, changed_field in scenarios:
            with (
                self.subTest(location=location, changed_field=changed_field),
                tempfile.TemporaryDirectory() as directory,
            ):
                source = Path(directory) / "source"
                source.write_bytes(b"private-content-canary")
                expected_metadata = self.helper._stable_regular_file_metadata(
                    source.lstat()
                )

                def changed(metadata, *, field: str = changed_field):
                    values = {
                        "st_dev": metadata.st_dev,
                        "st_ino": metadata.st_ino,
                        "st_mode": metadata.st_mode,
                        "st_nlink": metadata.st_nlink,
                        "st_size": metadata.st_size,
                        "st_mtime_ns": metadata.st_mtime_ns,
                        "st_uid": metadata.st_uid,
                        "st_gid": metadata.st_gid,
                    }
                    values[field] += 1
                    return SimpleNamespace(**values)

                original_fstat = os.fstat
                fstat_calls = 0

                def descriptor_after_read(
                    descriptor: int,
                    *,
                    operation=original_fstat,
                    transform=changed,
                ):
                    nonlocal fstat_calls
                    fstat_calls += 1
                    metadata = operation(descriptor)
                    return transform(metadata) if fstat_calls == 2 else metadata

                path_type = type(source)
                original_lstat = path_type.lstat

                def path_after_read(
                    path: Path,
                    *,
                    operation=original_lstat,
                    target: Path = source,
                    transform=changed,
                ):
                    metadata = operation(path)
                    return transform(metadata) if path == target else metadata

                with ExitStack() as stack:
                    if location == "descriptor":
                        stack.enter_context(
                            mock.patch.object(
                                os,
                                "fstat",
                                side_effect=descriptor_after_read,
                            )
                        )
                    else:
                        stack.enter_context(
                            mock.patch.object(
                                path_type,
                                "lstat",
                                autospec=True,
                                side_effect=path_after_read,
                            )
                        )
                    read = stack.enter_context(
                        mock.patch.object(os, "read", wraps=os.read)
                    )
                    with self.assertRaises(self.helper.ProbeError) as raised:
                        self.helper._read_stable_regular_file(
                            source,
                            1024,
                            "home-entry-observation",
                            expected_metadata=expected_metadata,
                        )

                self.assertEqual(
                    raised.exception.code,
                    "changed-home-entry-observation",
                )
                self.assertGreaterEqual(read.call_count, 1)
                self.assertNotIn("private", raised.exception.code)

    def test_home_entry_snapshot_rejects_unsafe_file_before_read(self) -> None:
        def hardlink(root: Path, entry: Path) -> None:
            entry.write_bytes(b"0:0")
            os.link(entry, root / "private-hardlink-canary")

        def oversized(_root: Path, entry: Path) -> None:
            entry.write_bytes(
                b"x" * (self.helper.MAX_HOME_ENTRY_OBSERVATION_FILE_BYTES + 1)
            )

        scenarios = (
            ("hardlink", hardlink),
            ("oversized", oversized),
        )
        for label, arrange in scenarios:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                home = root / "home"
                probe = home / "launchd-probe"
                entry = home / ".CFUserTextEncoding"
                probe.mkdir(parents=True, mode=0o700)
                home.chmod(0o700)
                probe.chmod(0o700)
                for name in self.helper.LAUNCHD_CHILD_FILES:
                    (probe / name).write_bytes(b"value")
                arrange(root, entry)

                with (
                    mock.patch.object(
                        self.helper,
                        "_read_stable_regular_file",
                        wraps=self.helper._read_stable_regular_file,
                    ) as read,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_exact_disposable_home(
                        home,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        diagnostic_phase="pre-reset-marker",
                    )

                self.assertEqual(
                    raised.exception.code,
                    ("home-cleanup-pre-reset-marker-home-entry-unsafe-or-foreign"),
                )
                read.assert_not_called()
                self.assertNotIn("private", raised.exception.code)

    def test_home_entry_snapshot_detects_metadata_replacement(self) -> None:
        scenarios = (
            (
                "private-home-identity-canary",
                "home",
                4,
                "st_ino",
                "home-entry-observation-unreadable",
            ),
            (
                "private-home-device-canary",
                "home",
                4,
                "st_dev",
                "home-entry-observation-unreadable",
            ),
            (
                "private-entry-identity-canary",
                "entry",
                2,
                "st_ino",
                "home-entry-observation-unstable",
            ),
            (
                "private-entry-mtime-canary",
                "entry",
                2,
                "st_mtime_ns",
                "home-entry-observation-unstable",
            ),
        )
        for (
            label,
            target_name,
            replace_on_call,
            changed_field,
            detail,
        ) in scenarios:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                probe = home / "launchd-probe"
                library = home / "Library"
                probe.mkdir(parents=True, mode=0o700)
                library.mkdir()
                home.chmod(0o700)
                probe.chmod(0o700)
                for name in self.helper.LAUNCHD_CHILD_FILES:
                    (probe / name).write_bytes(b"value")
                target = home if target_name == "home" else library
                path_type = type(home)
                original_lstat = path_type.lstat
                target_calls = 0

                def replace_identity(
                    path: Path,
                    *,
                    operation=original_lstat,
                    expected_target: Path = target,
                    replacement_call: int = replace_on_call,
                    field: str = changed_field,
                ):
                    nonlocal target_calls
                    metadata = operation(path)
                    if path != expected_target:
                        return metadata
                    target_calls += 1
                    if target_calls != replacement_call:
                        return metadata
                    values = {
                        "st_dev": metadata.st_dev,
                        "st_ino": metadata.st_ino,
                        "st_mode": metadata.st_mode,
                        "st_nlink": metadata.st_nlink,
                        "st_size": metadata.st_size,
                        "st_mtime_ns": metadata.st_mtime_ns,
                        "st_ctime_ns": metadata.st_ctime_ns,
                        "st_uid": metadata.st_uid,
                        "st_gid": metadata.st_gid,
                    }
                    values[field] += 1
                    return SimpleNamespace(**values)

                with (
                    mock.patch.object(
                        path_type,
                        "lstat",
                        autospec=True,
                        side_effect=replace_identity,
                    ),
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_exact_disposable_home(
                        home,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        diagnostic_phase="pre-reset-marker",
                    )

                self.assertEqual(target_calls, replace_on_call)
                self.assertEqual(
                    raised.exception.code,
                    f"home-cleanup-pre-reset-marker-{detail}",
                )
                self.assertNotIn(label, raised.exception.code)

    def test_home_entry_snapshot_normalizes_directory_iteration_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            library = home / "Library"
            text_encoding = home / ".CFUserTextEncoding"
            probe.mkdir(parents=True, mode=0o700)
            library.mkdir()
            text_encoding.write_bytes(b"0:0")
            home.chmod(0o700)
            probe.chmod(0o700)
            for name in self.helper.LAUNCHD_CHILD_FILES:
                (probe / name).write_bytes(b"value")
            home_listings = iter(
                (
                    (probe, library, text_encoding),
                    (text_encoding, probe, library),
                    (library, probe, text_encoding),
                )
            )
            observed_home_listings = 0

            def permuted_scandir(_descriptor: int):
                nonlocal observed_home_listings
                observed_home_listings += 1
                context = mock.MagicMock()
                context.__enter__.return_value = iter(
                    SimpleNamespace(name=path.name) for path in next(home_listings)
                )
                return context

            with (
                mock.patch.object(
                    self.helper.os,
                    "scandir",
                    side_effect=permuted_scandir,
                ),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._validate_exact_disposable_home(
                    home,
                    expected_uid=os.geteuid(),
                    expected_gid=os.getegid(),
                    diagnostic_phase="pre-reset-marker",
                )

            self.assertEqual(observed_home_listings, 3)
            self.assertEqual(
                raised.exception.code,
                (
                    "home-cleanup-pre-reset-marker-"
                    "home-entry-known-library-and-text-encoding"
                ),
            )
            self.assertNotIn("unstable", raised.exception.code)

    def test_home_entry_diagnostics_fail_closed_on_unreadable_or_unstable_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            for name in self.helper.LAUNCHD_CHILD_FILES:
                (probe / name).write_bytes(b"value")
            (home / "Library").mkdir()
            text_encoding_raw = b"0:0"
            (home / ".CFUserTextEncoding").write_bytes(text_encoding_raw)
            stable = self.helper._home_entry_observation_snapshot(
                home,
                expected_uid=os.geteuid(),
                expected_gid=os.getegid(),
            )
            stable_entries = list(stable[2])
            text_encoding_index = next(
                index
                for index, entry in enumerate(stable_entries)
                if entry[0] == ".CFUserTextEncoding"
            )
            text_encoding_entry = stable_entries[text_encoding_index]
            self.assertEqual(
                text_encoding_entry[10],
                hashlib.sha256(text_encoding_raw).hexdigest(),
            )
            changed_digest = (
                "0" * 64 if text_encoding_entry[10] != "0" * 64 else "1" * 64
            )
            stable_entries[text_encoding_index] = (
                *text_encoding_entry[:10],
                changed_digest,
            )
            digest_changed = (stable[0], stable[1], tuple(stable_entries))
            scenarios = (
                (
                    "unreadable-os-error",
                    mock.patch.object(
                        self.helper,
                        "_home_entry_observation_snapshot",
                        side_effect=OSError("private-read-canary"),
                    ),
                    "home-entry-observation-unreadable",
                ),
                (
                    "unreadable-probe-error",
                    mock.patch.object(
                        self.helper,
                        "_home_entry_observation_snapshot",
                        side_effect=self.helper.ProbeError(
                            "private-stable-read-canary"
                        ),
                    ),
                    "home-entry-observation-unreadable",
                ),
                (
                    "unstable",
                    mock.patch.object(
                        self.helper,
                        "_home_entry_observation_snapshot",
                        side_effect=(stable, digest_changed),
                    ),
                    "home-entry-observation-unstable",
                ),
            )
            for label, observation, detail in scenarios:
                with self.subTest(label=label), observation:
                    with self.assertRaises(self.helper.ProbeError) as raised:
                        self.helper._validate_exact_disposable_home(
                            home,
                            expected_uid=os.geteuid(),
                            expected_gid=os.getegid(),
                            diagnostic_phase="pre-reset-marker",
                        )
                    self.assertEqual(
                        raised.exception.code,
                        f"home-cleanup-pre-reset-marker-{detail}",
                    )
                    self.assertNotIn("private", raised.exception.code)

            (home / "private-outer-listing-canary").mkdir()
            with (
                mock.patch.object(
                    self.helper,
                    "_home_entry_observation_snapshot",
                    side_effect=(stable, stable),
                ),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._validate_exact_disposable_home(
                    home,
                    expected_uid=os.geteuid(),
                    expected_gid=os.getegid(),
                    diagnostic_phase="pre-reset-marker",
                )
            self.assertEqual(
                raised.exception.code,
                "home-cleanup-pre-reset-marker-home-entry-observation-unreadable",
            )
            self.assertNotIn("private", raised.exception.code)

    def test_home_drift_diagnostics_distinguish_exact_invariants(self) -> None:
        scenarios = (
            (
                "home-mode",
                lambda root, home, probe: home.chmod(0o755),
                "home-cleanup-pre-reset-marker-home-mode-drift",
            ),
            (
                "probe-mode",
                lambda root, home, probe: probe.chmod(0o755),
                "home-cleanup-pre-reset-marker-probe-mode-drift",
            ),
            (
                "probe-entry-set",
                lambda root, home, probe: (probe / "private-probe-canary").write_bytes(
                    b"preserve"
                ),
                "home-cleanup-pre-reset-marker-probe-entry-set-drift",
            ),
            (
                "probe-child-link-count",
                lambda root, home, probe: os.link(
                    probe / "probe.stdout",
                    root / "private-link-canary",
                ),
                "home-cleanup-pre-reset-marker-probe-child-link-count-drift",
            ),
        )
        for label, mutate, expected_code in scenarios:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                home = root / "home"
                probe = home / "launchd-probe"
                probe.mkdir(parents=True, mode=0o700)
                home.chmod(0o700)
                probe.chmod(0o700)
                for name in self.helper.LAUNCHD_CHILD_FILES:
                    (probe / name).write_bytes(b"value")
                mutate(root, home, probe)

                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper._validate_exact_disposable_home(
                        home,
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        diagnostic_phase="pre-reset-marker",
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn("private", raised.exception.code)

    def test_home_drift_diagnostics_distinguish_bound_invariants(self) -> None:
        scenarios = (
            (
                "home-mode",
                lambda root, home, probe, identity: home.chmod(0o755),
                "home-cleanup-post-reset-home-mode-drift",
            ),
            (
                "home-identity",
                lambda root, home, probe, identity: identity.update(
                    home_inode=identity["home_inode"] + 1
                ),
                "home-cleanup-post-reset-home-identity-drift",
            ),
            (
                "probe-mode",
                lambda root, home, probe, identity: probe.chmod(0o755),
                "home-cleanup-post-reset-probe-mode-drift",
            ),
            (
                "probe-identity",
                lambda root, home, probe, identity: identity.update(
                    probe_inode=identity["probe_inode"] + 1
                ),
                "home-cleanup-post-reset-probe-identity-drift",
            ),
            (
                "probe-entry-set",
                lambda root, home, probe, identity: (
                    probe / "private-probe-canary"
                ).write_bytes(b"preserve"),
                "home-cleanup-post-reset-probe-entry-set-drift",
            ),
            (
                "probe-child-link-count",
                lambda root, home, probe, identity: os.link(
                    probe / "probe.stdout",
                    root / "private-link-canary",
                ),
                "home-cleanup-post-reset-probe-child-link-count-drift",
            ),
        )
        for label, mutate, expected_code in scenarios:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                home = root / "home"
                probe = home / "launchd-probe"
                probe.mkdir(parents=True, mode=0o700)
                home.chmod(0o700)
                probe.chmod(0o700)
                for name in self.helper.LAUNCHD_CHILD_FILES:
                    (probe / name).write_bytes(b"value")
                home_metadata = home.lstat()
                probe_metadata = probe.lstat()
                identity = {
                    "home_device": home_metadata.st_dev,
                    "home_inode": home_metadata.st_ino,
                    "probe_device": probe_metadata.st_dev,
                    "probe_inode": probe_metadata.st_ino,
                }
                account = self.helper.DisposableAccount(
                    name="twq-0123456789ab",
                    uid=os.geteuid(),
                    gid=os.getegid(),
                    home=home,
                )
                mutate(root, home, probe, identity)

                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper._validated_marker_bound_disposable_home(
                        account,
                        identity,
                        diagnostic_phase="post-reset",
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn("private", raised.exception.code)

    def test_home_drift_diagnostics_cover_split_invariants(self) -> None:
        def remove_probe_children(probe: Path) -> None:
            for child in probe.iterdir():
                child.unlink()

        def home_kind(root: Path, home: Path, probe: Path):
            del root
            remove_probe_children(probe)
            probe.rmdir()
            home.rmdir()
            home.write_bytes(b"private-home-kind-canary")
            return nullcontext()

        def probe_missing(root: Path, home: Path, probe: Path):
            del root, home
            remove_probe_children(probe)
            probe.rmdir()
            return nullcontext()

        def probe_kind(root: Path, home: Path, probe: Path):
            del root, home
            remove_probe_children(probe)
            probe.rmdir()
            probe.write_bytes(b"private-probe-kind-canary")
            return nullcontext()

        def child_kind(root: Path, home: Path, probe: Path):
            del root, home
            child = probe / "probe.status"
            child.unlink()
            child.mkdir()
            return nullcontext()

        def child_size(root: Path, home: Path, probe: Path):
            del root, home
            (probe / "probe.status").write_bytes(b"x" * 17)
            return nullcontext()

        def lstat_failure(target: str):
            def prepare(root: Path, home: Path, probe: Path):
                del root
                targets = {
                    "home": home,
                    "probe": probe,
                    "child": probe / "probe.status",
                }
                selected = targets[target]
                original = Path.lstat

                def fail(path: Path):
                    if path == selected:
                        raise OSError("private-read-canary")
                    return original(path)

                return mock.patch.object(Path, "lstat", fail)

            return prepare

        def owner_drift(target: str):
            def prepare(root: Path, home: Path, probe: Path):
                del root
                targets = {
                    "home": home,
                    "probe": probe,
                    "child": probe / "probe.status",
                }
                selected = targets[target]
                original = Path.lstat

                def changed(path: Path):
                    metadata = original(path)
                    if path != selected:
                        return metadata
                    values = {
                        name: getattr(metadata, name)
                        for name in (
                            "st_dev",
                            "st_gid",
                            "st_ino",
                            "st_mode",
                            "st_nlink",
                            "st_size",
                            "st_uid",
                        )
                    }
                    values["st_uid"] += 1
                    return SimpleNamespace(**values)

                return mock.patch.object(Path, "lstat", changed)

            return prepare

        scenarios = (
            ("home-kind", home_kind, "home-kind-drift", ("exact", "bound")),
            (
                "home-owner",
                owner_drift("home"),
                "home-owner-drift",
                ("exact", "bound"),
            ),
            (
                "home-read",
                lstat_failure("home"),
                "home-read-failed",
                ("exact", "bound"),
            ),
            ("probe-missing", probe_missing, "probe-missing", ("exact",)),
            ("probe-kind", probe_kind, "probe-kind-drift", ("exact", "bound")),
            (
                "probe-owner",
                owner_drift("probe"),
                "probe-owner-drift",
                ("exact", "bound"),
            ),
            (
                "probe-read",
                lstat_failure("probe"),
                "probe-read-failed",
                ("exact", "bound"),
            ),
            (
                "child-read",
                lstat_failure("child"),
                "probe-child-read-failed",
                ("exact", "bound"),
            ),
            (
                "child-kind",
                child_kind,
                "probe-child-kind-drift",
                ("exact", "bound"),
            ),
            (
                "child-owner",
                owner_drift("child"),
                "probe-child-owner-drift",
                ("exact", "bound"),
            ),
            (
                "child-size",
                child_size,
                "probe-child-size-drift",
                ("exact", "bound"),
            ),
        )
        for label, prepare, detail, validators in scenarios:
            for validator in validators:
                with (
                    self.subTest(label=label, validator=validator),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    home = root / "home"
                    probe = home / "launchd-probe"
                    probe.mkdir(parents=True, mode=0o700)
                    home.chmod(0o700)
                    probe.chmod(0o700)
                    for name in self.helper.LAUNCHD_CHILD_FILES:
                        (probe / name).write_bytes(b"value")
                    home_metadata = home.lstat()
                    probe_metadata = probe.lstat()
                    identity = {
                        "home_device": home_metadata.st_dev,
                        "home_inode": home_metadata.st_ino,
                        "probe_device": probe_metadata.st_dev,
                        "probe_inode": probe_metadata.st_ino,
                    }
                    account = self.helper.DisposableAccount(
                        name="twq-0123456789ab",
                        uid=os.geteuid(),
                        gid=os.getegid(),
                        home=home,
                    )
                    phase = "pre-reset-marker" if validator == "exact" else "post-reset"
                    with (
                        prepare(root, home, probe),
                        self.assertRaises(self.helper.ProbeError) as raised,
                    ):
                        if validator == "exact":
                            self.helper._validate_exact_disposable_home(
                                home,
                                expected_uid=account.uid,
                                expected_gid=account.gid,
                                diagnostic_phase=phase,
                            )
                        else:
                            self.helper._validated_marker_bound_disposable_home(
                                account,
                                identity,
                                diagnostic_phase=phase,
                            )
                    self.assertEqual(
                        raised.exception.code,
                        f"home-cleanup-{phase}-{detail}",
                    )
                    self.assertNotIn("canary", raised.exception.code)

    def test_home_drift_diagnostic_vocabulary_is_bounded(self) -> None:
        for phase in self.helper.HOME_CLEANUP_DIAGNOSTIC_PHASES:
            for detail in self.helper.HOME_CLEANUP_DIAGNOSTIC_DETAILS:
                with self.subTest(phase=phase, detail=detail):
                    error = self.helper._home_cleanup_drift_error(phase, detail)
                    self.assertLessEqual(len(error.code), 128)
                    self.assertEqual(
                        self.helper._validated_probe_error(error.code),
                        {"code": error.code},
                    )
        for phase, detail in (
            ("private-phase-canary", "home-mode-drift"),
            ("post-reset", "private-detail-canary"),
        ):
            with self.subTest(phase=phase, detail=detail):
                error = self.helper._home_cleanup_drift_error(phase, detail)
                self.assertEqual(error.code, "home-cleanup-diagnostic-invalid")
                self.assertEqual(
                    self.helper._validated_probe_error(error.code),
                    {"code": "home-cleanup-diagnostic-invalid"},
                )
                self.assertNotIn("canary", error.code)

    def test_launchd_child_read_binds_home_drift_phase(self) -> None:
        account = self.helper.DisposableAccount(
            name="twq-0123456789ab",
            uid=502,
            gid=20,
            home=Path("/Users/twq-0123456789ab"),
        )

        def read(path: Path, _maximum: int, _label: str) -> bytes:
            return b"0\n" if path.name == "probe.status" else b""

        with (
            mock.patch.object(
                self.helper,
                "_validate_exact_disposable_home",
            ) as validate_home,
            mock.patch.object(
                self.helper,
                "_read_stable_regular_file",
                side_effect=read,
            ),
        ):
            payloads = self.helper._read_launchd_child_payloads(account)

        self.assertEqual(payloads["probe.status"], b"0\n")
        validate_home.assert_called_once_with(
            account.home,
            expected_uid=account.uid,
            expected_gid=account.gid,
            diagnostic_phase="child-read",
        )

    def test_marker_bound_home_cleanup_resumes_after_every_unlink(self) -> None:
        names = sorted(self.helper.LAUNCHD_CHILD_FILES)
        mutation_count = len(names) + 2
        for interrupt_after in range(1, mutation_count + 1):
            with (
                self.subTest(interrupt_after=interrupt_after),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                probe = home / "launchd-probe"
                probe.mkdir(parents=True, mode=0o700)
                home.chmod(0o700)
                probe.chmod(0o700)
                for name in names:
                    (probe / name).write_bytes(b"value")
                home_metadata = home.lstat()
                probe_metadata = probe.lstat()
                identity = {
                    "home_device": home_metadata.st_dev,
                    "home_inode": home_metadata.st_ino,
                    "probe_device": probe_metadata.st_dev,
                    "probe_inode": probe_metadata.st_ino,
                }
                account = self.helper.DisposableAccount(
                    name="twq-0123456789ab",
                    uid=os.geteuid(),
                    gid=os.getegid(),
                    home=home,
                )
                authorization = {
                    "disposition": "armed",
                    "account": {
                        "name": account.name,
                        "uid": account.uid,
                        "gid": account.gid,
                        "home": str(account.home),
                    },
                    "home_identity": identity,
                }
                observed_mutations = 0
                original_unlink = Path.unlink
                original_rmdir = Path.rmdir

                def interrupt(expected_mutations: int = interrupt_after) -> None:
                    nonlocal observed_mutations
                    observed_mutations += 1
                    if observed_mutations == expected_mutations:
                        raise KeyboardInterrupt

                def unlink(
                    path: Path,
                    *args: object,
                    operation: object = original_unlink,
                    **kwargs: object,
                ) -> None:
                    operation(path, *args, **kwargs)  # type: ignore[operator]
                    interrupt()

                def rmdir(path: Path, operation: object = original_rmdir) -> None:
                    operation(path)  # type: ignore[operator]
                    interrupt()

                with (
                    mock.patch.object(Path, "unlink", unlink),
                    mock.patch.object(Path, "rmdir", rmdir),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    self.helper._remove_marker_bound_disposable_home(
                        account,
                        identity,
                        authorization,
                    )

                self.assertEqual(observed_mutations, interrupt_after)
                self.helper._remove_marker_bound_disposable_home(
                    account,
                    identity,
                    authorization,
                )
                self.assertFalse(home.exists())

    def test_marker_bound_partial_home_preserves_unexpected_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            home_metadata = home.lstat()
            probe_metadata = probe.lstat()
            identity = {
                "home_device": home_metadata.st_dev,
                "home_inode": home_metadata.st_ino,
                "probe_device": probe_metadata.st_dev,
                "probe_inode": probe_metadata.st_ino,
            }
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            foreign = probe / "private-canary"
            foreign.write_bytes(b"preserve")

            with self.assertRaises(self.helper.ProbeError) as raised:
                self.helper._remove_marker_bound_disposable_home(
                    account,
                    identity,
                    {
                        "disposition": "armed",
                        "account": {
                            "name": account.name,
                            "uid": account.uid,
                            "gid": account.gid,
                            "home": str(account.home),
                        },
                        "home_identity": identity,
                    },
                )
            self.assertEqual(
                raised.exception.code,
                "home-cleanup-home-removal-probe-entry-set-drift",
            )
            self.assertEqual(foreign.read_bytes(), b"preserve")

    def test_marker_bound_probe_absent_home_preserves_residual_entries(self) -> None:
        scenarios = (
            (
                "known-library",
                lambda home: (home / "Library").mkdir(),
                "home-entry-known-library-owned-directory",
                "Library",
            ),
            (
                "unknown-file",
                lambda home: (home / "private-canary").write_bytes(b"preserve"),
                "home-entry-single-owned-file-other",
                "private-canary",
            ),
        )
        for label, arrange, detail, residual_name in scenarios:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "home"
                probe = home / "launchd-probe"
                probe.mkdir(parents=True, mode=0o700)
                home.chmod(0o700)
                probe.chmod(0o700)
                home_metadata = home.lstat()
                probe_metadata = probe.lstat()
                identity = {
                    "home_device": home_metadata.st_dev,
                    "home_inode": home_metadata.st_ino,
                    "probe_device": probe_metadata.st_dev,
                    "probe_inode": probe_metadata.st_ino,
                }
                account = self.helper.DisposableAccount(
                    name="twq-0123456789ab",
                    uid=os.geteuid(),
                    gid=os.getegid(),
                    home=home,
                )
                authorization = {
                    "disposition": "armed",
                    "account": {
                        "name": account.name,
                        "uid": account.uid,
                        "gid": account.gid,
                        "home": str(account.home),
                    },
                    "home_identity": identity,
                }
                probe.rmdir()
                arrange(home)

                with (
                    mock.patch.object(Path, "unlink") as unlink,
                    mock.patch.object(Path, "rmdir") as rmdir,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._remove_marker_bound_disposable_home(
                        account,
                        identity,
                        authorization,
                    )

                self.assertEqual(
                    raised.exception.code,
                    f"home-cleanup-home-removal-{detail}",
                )
                unlink.assert_not_called()
                rmdir.assert_not_called()
                self.assertTrue((home / residual_name).exists())
                self.assertNotIn("private", raised.exception.code)

    def test_marker_bound_inventory_preserves_diagnostic_precedence(self) -> None:
        def fixture(root: Path) -> tuple[object, dict[str, int], Path, Path]:
            home = root / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            home_metadata = home.lstat()
            probe_metadata = probe.lstat()
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            return (
                account,
                {
                    "home_device": home_metadata.st_dev,
                    "home_inode": home_metadata.st_ino,
                    "probe_device": probe_metadata.st_dev,
                    "probe_inode": probe_metadata.st_ino,
                },
                home,
                probe,
            )

        arrangements = (
            (
                "unexpected",
                lambda home: (home / "private-entry-canary").mkdir(),
            ),
            (
                "both-library-names",
                lambda home: (
                    (home / self.helper.HOME_LIBRARY_NAME).mkdir(),
                    (home / self.helper.HOME_LIBRARY_QUARANTINE_NAME).mkdir(),
                ),
            ),
        )
        for label, arrange in arrangements:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                account, identity, home, _probe = fixture(Path(directory))
                arrange(home)
                with ExitStack() as stack:
                    mutations = self.guard_home_validation_mutations(stack)
                    with self.assertRaises(self.helper.ProbeError) as raised:
                        self.helper._validated_marker_bound_disposable_home(
                            account,
                            identity,
                            diagnostic_phase="home-removal",
                            library_inventory="none",
                        )
                self.assertEqual(
                    raised.exception.code,
                    "home-library-inventory-drift",
                )
                self.assertNotIn("private", raised.exception.code)
                for mutation in mutations:
                    mutation.assert_not_called()

        with tempfile.TemporaryDirectory() as directory:
            account, identity, _home, _probe = fixture(Path(directory))
            with ExitStack() as stack:
                mutations = self.guard_home_validation_mutations(stack)
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_bounded_path_directory_names",
                        return_value=[],
                    )
                )
                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper._validated_marker_bound_disposable_home(
                        account,
                        identity,
                        diagnostic_phase="home-removal",
                        library_inventory="none",
                    )
            self.assertEqual(raised.exception.code, "home-library-inventory-drift")
            for mutation in mutations:
                mutation.assert_not_called()

        with tempfile.TemporaryDirectory() as directory:
            account, identity, home, _probe = fixture(Path(directory))
            library = home / self.helper.HOME_LIBRARY_NAME
            library.mkdir(mode=0o755)
            library.chmod(0o755)
            inventory = self.helper._bounded_library_inventory(account)
            self.assertIsInstance(inventory, dict)
            library.chmod(0o700)
            with ExitStack() as stack:
                mutations = self.guard_home_validation_mutations(stack)
                probe_children = stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_validated_launchd_probe_children",
                    )
                )
                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper._validated_marker_bound_disposable_home(
                        account,
                        identity,
                        diagnostic_phase="home-removal",
                        library_inventory=inventory,
                    )
            self.assertEqual(raised.exception.code, "home-library-inventory-drift")
            probe_children.assert_not_called()
            for mutation in mutations:
                mutation.assert_not_called()
            self.assertEqual(stat.S_IMODE(library.lstat().st_mode), 0o700)

        with tempfile.TemporaryDirectory() as directory:
            account, identity, home, probe = fixture(Path(directory))
            (home / self.helper.HOME_LIBRARY_NAME).mkdir()
            inventory = self.helper._bounded_library_inventory(account)
            self.assertIsInstance(inventory, dict)
            probe.rmdir()
            with ExitStack() as stack:
                mutations = self.guard_home_validation_mutations(stack)
                with self.assertRaises(self.helper.ProbeError) as raised:
                    self.helper._validated_marker_bound_disposable_home(
                        account,
                        identity,
                        diagnostic_phase="home-removal",
                        library_inventory=inventory,
                    )
            self.assertEqual(
                raised.exception.code,
                ("home-cleanup-home-removal-home-entry-known-library-owned-directory"),
            )
            for mutation in mutations:
                mutation.assert_not_called()
            self.assertTrue((home / self.helper.HOME_LIBRARY_NAME).is_dir())

    def test_marker_bound_home_listing_race_stops_before_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            home_metadata = home.lstat()
            probe_metadata = probe.lstat()
            identity = {
                "home_device": home_metadata.st_dev,
                "home_inode": home_metadata.st_ino,
                "probe_device": probe_metadata.st_dev,
                "probe_inode": probe_metadata.st_ino,
            }
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            authorization = {
                "disposition": "armed",
                "account": {
                    "name": account.name,
                    "uid": account.uid,
                    "gid": account.gid,
                    "home": str(account.home),
                },
                "home_identity": identity,
            }
            original_scandir = os.scandir
            home_observations = 0

            def raced_scandir(descriptor: int):
                nonlocal home_observations
                home_observations += 1
                if home_observations == 1:
                    context = mock.MagicMock()
                    context.__enter__.return_value = iter(())
                    return context
                return original_scandir(descriptor)

            with (
                mock.patch.object(
                    self.helper.os,
                    "scandir",
                    side_effect=raced_scandir,
                ),
                mock.patch.object(Path, "unlink") as unlink,
                mock.patch.object(Path, "rmdir") as rmdir,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._remove_marker_bound_disposable_home(
                    account,
                    identity,
                    authorization,
                )

            self.assertEqual(
                raised.exception.code,
                "home-cleanup-home-removal-home-entry-set-drift",
            )
            self.assertGreaterEqual(home_observations, 3)
            unlink.assert_not_called()
            rmdir.assert_not_called()
            self.assertTrue(probe.is_dir())

    def test_no_reset_cleanup_recovers_after_interrupted_home_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            home = root / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            probe.chmod(0o700)
            for name in self.helper.LAUNCHD_CHILD_FILES:
                (probe / name).write_bytes(b"value")
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            plan = self.helper.LaunchdPlan(
                account=account,
                label="io.nisavid.task-witness.macos-probe.0123456789ab",
                stage_root=stage,
                helper=stage / "helper.py",
                plist=stage / "job.plist",
            )
            state = self.lifecycle_state(plan)
            generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
            account_binding = self.helper._account_binding_document(
                plan,
                state,
                generated_uid,
            )
            bindings = self.helper.ValidatedStageBindings(account_binding, None)
            for name in ("helper.py", "job.plist", "state.json", "account.json"):
                (stage / name).write_bytes(f"exact-{name}".encode())
            artifact = root / "artifact"
            artifact.mkdir()
            account_live = True
            cleanup_writes: list[tuple[Path, bytes, int]] = []

            def validate_stage(*_args: object, **_kwargs: object) -> object:
                journal_path = stage / "home-cleanup.json"
                if not journal_path.exists():
                    return bindings
                return bindings._replace(
                    home_cleanup=json.loads(journal_path.read_bytes())
                )

            def account_exists(_name: str) -> bool:
                return account_live

            def list_accounts() -> dict[str, int]:
                return {account.name: account.uid} if account_live else {}

            def command(*_args: object, **_kwargs: object) -> str:
                nonlocal account_live
                account_live = False
                return ""

            def write_root(path: Path, raw: bytes, mode: int) -> None:
                cleanup_writes.append((path, raw, mode))

            with ExitStack() as stack:
                stack.enter_context(
                    mock.patch.dict(os.environ, eligible_context(), clear=True)
                )
                for name in (
                    "_normalized_context",
                    "_validate_lifecycle_arguments",
                    "_reconcile_owned_launchd_job",
                    "_require_no_uid_processes",
                    "_require_stable_no_uid_processes",
                    "_fsync_stage_directory",
                    "_validate_precleanup_artifact",
                    "launchd_artifact_payloads",
                ):
                    stack.enter_context(mock.patch.object(self.helper, name))
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_cleanup_helper_only_stage_before_state",
                        return_value=False,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_load_lifecycle_state",
                        return_value=(plan, state),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_validate_exact_stage",
                        side_effect=validate_stage,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_account_exists",
                        side_effect=account_exists,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_read_system_generated_uid",
                        return_value=generated_uid,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_list_accounts",
                        side_effect=list_accounts,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_require_command_success",
                        side_effect=command,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        return_value=True,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_write_root_file",
                        side_effect=write_root,
                    )
                )
                stack.enter_context(mock.patch.object(self.helper.os, "chown"))

                original_unlink = Path.unlink
                child_unlinked = False

                def interrupt_first_child(
                    path: Path,
                    *args: object,
                    operation: object = original_unlink,
                    **kwargs: object,
                ) -> None:
                    nonlocal child_unlinked
                    operation(path, *args, **kwargs)  # type: ignore[operator]
                    if path.parent == probe and not child_unlinked:
                        child_unlinked = True
                        raise KeyboardInterrupt

                with (
                    mock.patch.object(Path, "unlink", interrupt_first_child),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    self.helper.cleanup_launchd_user_lifecycle(
                        stage_root=stage,
                        artifact_root=artifact,
                        expected_helper_sha256="1" * 64,
                        runner_uid=501,
                        runner_gid=20,
                    )

                self.assertFalse(account_live)
                self.assertTrue((stage / "home-cleanup.json").is_file())
                self.assertEqual(len(list(probe.iterdir())), 3)
                self.assertEqual(cleanup_writes, [])

                self.assertEqual(
                    self.helper.cleanup_launchd_user_lifecycle(
                        stage_root=stage,
                        artifact_root=artifact,
                        expected_helper_sha256="1" * 64,
                        runner_uid=501,
                        runner_gid=20,
                    ),
                    0,
                )

            self.assertFalse(stage.exists())
            self.assertFalse(home.exists())
            self.assertEqual(len(cleanup_writes), 1)
            cleanup_path, cleanup_raw, cleanup_mode = cleanup_writes[0]
            self.assertEqual(cleanup_path, artifact / "cleanup.json")
            self.assertEqual(cleanup_mode, 0o600)
            cleanup = json.loads(cleanup_raw)
            self.assertEqual(cleanup["schema_version"], 3)
            self.assertEqual(
                cleanup["domain_reset"],
                self.helper._domain_reset_evidence(None),
            )
            self.assertEqual(cleanup["home_cleanup"]["disposition"], "recovered")

    def test_disabled_account_creation_uses_exact_dscl_commands_and_readback(
        self,
    ) -> None:
        account = self.helper.DisposableAccount(
            name="twq-0123456789ab",
            uid=502,
            gid=20,
            home=Path("/Users/twq-0123456789ab"),
        )
        plan = self.helper.LaunchdPlan(
            account=account,
            label="io.nisavid.task-witness.macos-probe.0123456789ab",
            stage_root=Path("/private/var/tmp/task-witness-macos-launchd-123456789-2"),
            helper=Path(
                "/private/var/tmp/task-witness-macos-launchd-123456789-2/helper.py"
            ),
            plist=Path(
                "/private/var/tmp/task-witness-macos-launchd-123456789-2/job.plist"
            ),
        )
        state = self.lifecycle_state(plan)
        system_generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        record = "\n".join(
            (
                "AuthenticationAuthority: ;DisabledUser;",
                f"GeneratedUID: {system_generated_uid}",
                "dsAttrTypeNative:IsHidden: 1",
                f"NFSHomeDirectory: {account.home}",
                "Password: *",
                "PrimaryGroupID: 20",
                "UniqueID: 502",
                "UserShell: /usr/bin/false",
            )
        )
        calls: list[tuple[str, ...]] = []
        events: list[tuple[str, ...] | str] = []
        list_count = 0

        def command(
            argv: list[str],
            *,
            maximum: int = self.helper.MAX_COMMAND_OUTPUT_BYTES,
            timeout: int = self.helper.COMMAND_TIMEOUT_SECONDS,
        ) -> tuple[int, str, str]:
            del maximum, timeout
            nonlocal list_count
            call = tuple(argv)
            calls.append(call)
            events.append(call)
            if call == ("/usr/bin/dscl", ".", "-list", "/Users", "UniqueID"):
                list_count += 1
                suffix = f"\n{account.name} {account.uid}" if list_count == 2 else ""
                return 0, f"root 0\nrunner 501{suffix}", ""
            if call == ("/usr/bin/dscl", ".", "-list", "/Users"):
                return 0, "root\nrunner", ""
            if call == (
                "/usr/bin/dscl",
                ".",
                "-read",
                f"/Users/{account.name}",
                "GeneratedUID",
            ):
                return 0, f"GeneratedUID: {system_generated_uid}", ""
            if call[:4] == ("/usr/bin/dscl", ".", "-read", f"/Users/{account.name}"):
                return 0, record, ""
            return 0, "", ""

        def write_account_binding(
            _plan: object,
            _state: object,
            _generated_uid: str,
        ) -> dict[str, str]:
            events.append("account-binding")
            return {"generated_uid": system_generated_uid}

        with (
            mock.patch.object(
                self.helper,
                "_run_lifecycle_command",
                side_effect=command,
            ),
            mock.patch.object(
                self.helper,
                "_require_disposable_uid_available",
            ),
            mock.patch.object(
                self.helper,
                "_write_account_binding",
                side_effect=write_account_binding,
            ) as write_binding,
        ):
            self.helper._create_disposable_account(plan, state)

        write_binding.assert_called_once_with(plan, state, system_generated_uid)

        creates = [call for call in calls if call[2] == "-create"]
        self.assertEqual(len(creates), 8)
        self.assertEqual(
            creates[0],
            (
                "/usr/bin/dscl",
                ".",
                "-create",
                f"/Users/{account.name}",
            ),
        )
        self.assertEqual(
            calls[calls.index(creates[0]) + 1],
            (
                "/usr/bin/dscl",
                ".",
                "-read",
                f"/Users/{account.name}",
                "GeneratedUID",
            ),
        )
        generated_uid_read = calls[calls.index(creates[0]) + 1]
        self.assertEqual(
            events[
                events.index(generated_uid_read) + 1 : events.index(generated_uid_read)
                + 3
            ],
            ["account-binding", creates[1]],
        )
        self.assertFalse(
            any(call[2] == "-create" and "GeneratedUID" in call for call in calls)
        )
        self.assertEqual(
            creates[1],
            (
                "/usr/bin/dscl",
                ".",
                "-create",
                f"/Users/{account.name}",
                "UserShell",
                "/usr/bin/false",
            ),
        )
        self.assertEqual(
            creates[2],
            (
                "/usr/bin/dscl",
                ".",
                "-create",
                f"/Users/{account.name}",
                "AuthenticationAuthority",
                ";DisabledUser;",
            ),
        )
        self.assertEqual(
            creates[3],
            (
                "/usr/bin/dscl",
                ".",
                "-create",
                f"/Users/{account.name}",
                "Password",
                "*",
            ),
        )
        self.assertFalse(
            any("sysadminctl" in field for call in calls for field in call)
        )

    def test_account_creation_reports_and_rolls_back_every_failed_step(
        self,
    ) -> None:
        account = self.helper.DisposableAccount(
            name="twq-0123456789ab",
            uid=502,
            gid=20,
            home=Path("/Users/twq-0123456789ab"),
        )
        plan = self.helper.LaunchdPlan(
            account=account,
            label="io.nisavid.task-witness.macos-probe.0123456789ab",
            stage_root=Path("/private/var/tmp/task-witness-macos-launchd-123456789-2"),
            helper=Path(
                "/private/var/tmp/task-witness-macos-launchd-123456789-2/helper.py"
            ),
            plist=Path(
                "/private/var/tmp/task-witness-macos-launchd-123456789-2/job.plist"
            ),
        )
        state = self.lifecycle_state(plan)
        command_ids = (
            "account-create-record",
            "account-generated-uid-read",
            "account-set-shell",
            "account-set-authentication-authority",
            "account-set-password",
            "account-set-hidden",
            "account-set-uid",
            "account-set-gid",
            "account-set-home",
        )
        for failure_index, command_id in enumerate(command_ids):
            calls: list[tuple[str, ...]] = []
            record_present = False
            phase_index = 0

            def command(
                argv: list[str],
                observed_calls: list[tuple[str, ...]] = calls,
                failed_at: int = failure_index,
                **_kwargs: object,
            ) -> tuple[int, str, str]:
                nonlocal record_present, phase_index
                call = tuple(argv)
                observed_calls.append(call)
                if call == ("/usr/bin/dscl", ".", "-list", "/Users", "UniqueID"):
                    return 0, "root 0\nrunner 501", ""
                if call == ("/usr/bin/dscl", ".", "-list", "/Users"):
                    suffix = f"\n{account.name}" if record_present else ""
                    return 0, f"root\nrunner{suffix}", ""
                if call[:4] == (
                    "/usr/bin/dscl",
                    ".",
                    "-delete",
                    f"/Users/{account.name}",
                ):
                    record_present = False
                    return 0, "", ""
                if call[:4] == (
                    "/usr/bin/dscl",
                    ".",
                    "-create",
                    f"/Users/{account.name}",
                ):
                    record_present = True
                    current_index = phase_index
                    phase_index += 1
                    if current_index == failed_at:
                        return 1, "", "synthetic failure"
                    return 0, "", ""
                if call == (
                    "/usr/bin/dscl",
                    ".",
                    "-read",
                    f"/Users/{account.name}",
                    "GeneratedUID",
                ):
                    current_index = phase_index
                    phase_index += 1
                    if current_index == failed_at:
                        return 1, "", "synthetic failure"
                    return 0, "GeneratedUID: 01234567-89AB-4DEF-8123-456789ABCDEF", ""
                raise AssertionError(call)

            with (
                self.subTest(failure_index=failure_index),
                mock.patch.object(
                    self.helper,
                    "_require_disposable_uid_available",
                ),
                mock.patch.object(
                    self.helper,
                    "_run_lifecycle_command",
                    side_effect=command,
                ),
                mock.patch.object(
                    self.helper,
                    "_write_account_binding",
                    return_value={
                        "generated_uid": "01234567-89AB-4DEF-8123-456789ABCDEF"
                    },
                ) as write_binding,
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    f"lifecycle-command-nonzero-{command_id}",
                ),
            ):
                self.helper._create_disposable_account(plan, state)

            deletes = [call for call in calls if call[2] == "-delete"]
            expected_deletes = []
            if failure_index >= 2:
                expected_deletes = [
                    (
                        "/usr/bin/dscl",
                        ".",
                        "-delete",
                        f"/Users/{account.name}",
                    )
                ]
            self.assertEqual(
                deletes,
                expected_deletes,
            )
            self.assertEqual(record_present, failure_index < 2)
            self.assertEqual(write_binding.call_count, int(failure_index >= 2))
            self.assertEqual(
                phase_index,
                failure_index + (2 if failure_index >= 2 else 1),
            )

    def test_account_rollback_requires_the_observed_immutable_guid(self) -> None:
        account = self.helper.DisposableAccount(
            name="twq-0123456789ab",
            uid=502,
            gid=20,
            home=Path("/Users/twq-0123456789ab"),
        )
        expected = "01234567-89AB-4DEF-8123-456789ABCDEF"
        different = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"
        with (
            mock.patch.object(self.helper, "_account_exists", return_value=True),
            mock.patch.object(
                self.helper,
                "_read_system_generated_uid",
                return_value=different,
            ),
            mock.patch.object(self.helper, "_require_command_success") as delete,
            self.assertRaisesRegex(
                self.helper.ProbeError,
                "account-record-generated-uid-drift",
            ),
        ):
            self.helper._rollback_disposable_account_creation(account, expected)
        delete.assert_not_called()

    def test_account_creation_rejects_a_changed_observed_guid(self) -> None:
        plan = self.lifecycle_plan(
            Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        )
        account = plan.account
        state = self.lifecycle_state(plan)
        observed = "01234567-89AB-4DEF-8123-456789ABCDEF"
        changed = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"
        with (
            mock.patch.object(
                self.helper,
                "_list_accounts",
                return_value={"root": 0},
            ),
            mock.patch.object(self.helper, "_account_exists", return_value=False),
            mock.patch.object(
                self.helper,
                "_require_disposable_uid_available",
            ),
            mock.patch.object(self.helper, "_require_command_success"),
            mock.patch.object(
                self.helper,
                "_read_system_generated_uid",
                return_value=observed,
            ),
            mock.patch.object(self.helper, "_write_account_binding"),
            mock.patch.object(
                self.helper,
                "_account_record",
                return_value={"GeneratedUID": [changed]},
            ),
            mock.patch.object(
                self.helper,
                "_rollback_disposable_account_creation",
            ) as rollback,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._create_disposable_account(plan, state)
        self.assertEqual(
            raised.exception.code,
            "account-record-generated-uid-drift",
        )
        self.assertIsNone(raised.exception.secondary_code)
        rollback.assert_called_once_with(account, observed)

    def test_account_binding_is_bounded_root_owned_and_state_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
            document = self.helper._account_binding_document(
                plan,
                state,
                generated_uid,
            )
            raw = self.helper.canonical_bytes(document)
            self.assertLessEqual(len(raw), self.helper.MAX_ACCOUNT_BINDING_BYTES)
            self.assertEqual(
                document["account"]["generated_uid"],
                generated_uid,
            )

            with (
                mock.patch.object(self.helper, "_write_root_file") as write,
                mock.patch.object(
                    self.helper,
                    "_load_account_binding",
                    return_value=document,
                ),
            ):
                self.assertEqual(
                    self.helper._write_account_binding(
                        plan,
                        state,
                        generated_uid,
                    ),
                    document,
                )
            write.assert_called_once_with(stage / "account.json", raw, 0o600)

            def create_partial(path: Path, payload: bytes, mode: int) -> None:
                path.write_bytes(payload)
                path.chmod(mode)

            with (
                mock.patch.object(
                    self.helper,
                    "_write_root_file",
                    side_effect=create_partial,
                ),
                mock.patch.object(
                    self.helper,
                    "_load_account_binding",
                    return_value={},
                ),
                mock.patch.object(
                    self.helper,
                    "_root_file_matches",
                    return_value=True,
                ) as rollback_matches,
                mock.patch.object(
                    self.helper,
                    "_read_stable_regular_file",
                    return_value=raw,
                ) as rollback_read,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._write_account_binding(plan, state, generated_uid)
            self.assertEqual(raised.exception.code, "account-binding-disagrees")
            rollback_matches.assert_called_once_with(stage / "account.json", 0o600)
            rollback_read.assert_called_once_with(
                stage / "account.json",
                len(raw),
                "partial-account-binding",
            )
            self.assertFalse((stage / "account.json").exists())

            changed_raw = b"x" + raw[1:]
            for label, metadata_matches, published_raw in (
                ("metadata-drift", False, raw),
                ("byte-drift", True, changed_raw),
            ):
                account_path = stage / "account.json"

                def publish_drifted(
                    path: Path,
                    _payload: bytes,
                    mode: int,
                    observed: bytes = published_raw,
                ) -> None:
                    path.write_bytes(observed)
                    path.chmod(mode)

                with (
                    self.subTest(label=label),
                    mock.patch.object(
                        self.helper,
                        "_write_root_file",
                        side_effect=publish_drifted,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_load_account_binding",
                        return_value={},
                    ),
                    mock.patch.object(
                        self.helper,
                        "_root_file_matches",
                        return_value=metadata_matches,
                    ) as preserve_matches,
                    mock.patch.object(
                        self.helper,
                        "_read_stable_regular_file",
                        return_value=published_raw,
                    ) as preserve_read,
                    mock.patch.object(Path, "unlink") as preserve_unlink,
                    self.assertRaises(self.helper.ProbeError) as preserved,
                ):
                    self.helper._write_account_binding(plan, state, generated_uid)

                self.assertEqual(preserved.exception.code, "account-binding-preserved")
                preserve_matches.assert_called_once_with(account_path, 0o600)
                if metadata_matches:
                    preserve_read.assert_called_once_with(
                        account_path,
                        len(raw),
                        "partial-account-binding",
                    )
                else:
                    preserve_read.assert_not_called()
                preserve_unlink.assert_not_called()
                self.assertEqual(account_path.read_bytes(), published_raw)
                account_path.unlink()

            (stage / "account.json").write_bytes(raw)
            with mock.patch.object(
                self.helper,
                "_metadata_matches",
                return_value=True,
            ) as metadata:
                self.assertEqual(
                    self.helper._load_account_binding(plan, state),
                    document,
                )
            metadata.assert_called_once_with(
                stage / "account.json",
                kind="file",
                mode=0o600,
                uid=0,
                gid=0,
                nlink=1,
            )

            changed_state = dict(state)
            changed_state["content_sha256"] = "f" * 64
            with (
                mock.patch.object(
                    self.helper,
                    "_metadata_matches",
                    return_value=True,
                ),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "account-binding-drift",
                ),
            ):
                self.helper._load_account_binding(plan, changed_state)

            for invalid in (
                "01234567-89ab-4def-8123-456789abcdef",
                "00000000-0000-0000-0000-000000000000",
                "not-a-guid",
            ):
                with (
                    self.subTest(invalid=invalid),
                    self.assertRaisesRegex(
                        self.helper.ProbeError,
                        "account-binding-invalid",
                    ),
                ):
                    self.helper._account_binding_document(plan, state, invalid)

    def test_root_stage_document_checks_metadata_before_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            cases = (
                (
                    "account.json",
                    self.helper.MAX_ACCOUNT_BINDING_BYTES,
                    "account-binding",
                ),
                (
                    "ownership.json",
                    self.helper.MAX_OWNERSHIP_BYTES,
                    "launchd-ownership-marker",
                ),
                (
                    "domain-reset.json",
                    self.helper.MAX_USER_DOMAIN_RESET_BYTES,
                    "user-domain-reset-authorization",
                ),
                (
                    "home-cleanup.json",
                    self.helper.MAX_HOME_CLEANUP_BYTES,
                    "home-cleanup-authorization",
                ),
            )

            def observed(event_log: list[str], tag: str, result: object):
                def observe(*_args: object, **_kwargs: object) -> object:
                    event_log.append(tag)
                    return result

                return observe

            def rejected(event_log: list[str], tag: str, code: str):
                def reject(*_args: object, **_kwargs: object) -> object:
                    event_log.append(tag)
                    raise self.helper.ProbeError(code)

                return reject

            for filename, maximum, label in cases:
                path = stage / filename
                document = {"content_sha256": "a" * 64}

                with self.subTest(filename=filename, state="missing"):
                    events: list[str] = []
                    with (
                        mock.patch.object(
                            self.helper,
                            "_path_exists_no_follow",
                            side_effect=observed(events, "exists", False),
                        ) as exists,
                        mock.patch.object(
                            self.helper,
                            "_root_file_matches",
                        ) as metadata,
                        mock.patch.object(
                            self.helper,
                            "_load_canonical_document",
                        ) as canonical,
                        mock.patch.object(
                            self.helper,
                            "_require_content_digest",
                        ) as digest,
                    ):
                        self.assertIsNone(
                            self.helper._load_root_stage_document(
                                path,
                                maximum,
                                label,
                            )
                        )
                    self.assertEqual(events, ["exists"])
                    exists.assert_called_once_with(path)
                    metadata.assert_not_called()
                    canonical.assert_not_called()
                    digest.assert_not_called()

                with self.subTest(filename=filename, state="unsafe-metadata"):
                    events = []
                    with (
                        mock.patch.object(
                            self.helper,
                            "_path_exists_no_follow",
                            side_effect=observed(events, "exists", True),
                        ),
                        mock.patch.object(
                            self.helper,
                            "_root_file_matches",
                            side_effect=observed(events, "metadata", False),
                        ) as metadata,
                        mock.patch.object(
                            self.helper,
                            "_load_canonical_document",
                            side_effect=rejected(
                                events,
                                "canonical",
                                "private-canonical-canary",
                            ),
                        ) as canonical,
                        mock.patch.object(
                            self.helper,
                            "_require_content_digest",
                        ) as digest,
                        self.assertRaises(self.helper.ProbeError) as raised,
                    ):
                        self.helper._load_root_stage_document(
                            path,
                            maximum,
                            label,
                        )
                    self.assertEqual(raised.exception.code, f"{label}-drift")
                    self.assertNotIn(
                        "private-canonical-canary",
                        str(raised.exception),
                    )
                    self.assertEqual(events, ["exists", "metadata"])
                    metadata.assert_called_once_with(path, 0o600)
                    canonical.assert_not_called()
                    digest.assert_not_called()

                with self.subTest(filename=filename, state="canonical-error"):
                    events = []
                    with (
                        mock.patch.object(
                            self.helper,
                            "_path_exists_no_follow",
                            side_effect=observed(events, "exists", True),
                        ),
                        mock.patch.object(
                            self.helper,
                            "_root_file_matches",
                            side_effect=observed(events, "metadata", True),
                        ),
                        mock.patch.object(
                            self.helper,
                            "_load_canonical_document",
                            side_effect=rejected(
                                events,
                                "canonical",
                                f"invalid-{label}",
                            ),
                        ) as canonical,
                        mock.patch.object(
                            self.helper,
                            "_require_content_digest",
                        ) as digest,
                        self.assertRaises(self.helper.ProbeError) as raised,
                    ):
                        self.helper._load_root_stage_document(
                            path,
                            maximum,
                            label,
                        )
                    self.assertEqual(raised.exception.code, f"invalid-{label}")
                    self.assertEqual(
                        events,
                        ["exists", "metadata", "canonical"],
                    )
                    canonical.assert_called_once_with(path, maximum, label)
                    digest.assert_not_called()

                with self.subTest(filename=filename, state="digest-error"):
                    events = []
                    with (
                        mock.patch.object(
                            self.helper,
                            "_path_exists_no_follow",
                            side_effect=observed(events, "exists", True),
                        ),
                        mock.patch.object(
                            self.helper,
                            "_root_file_matches",
                            side_effect=observed(events, "metadata", True),
                        ),
                        mock.patch.object(
                            self.helper,
                            "_load_canonical_document",
                            side_effect=observed(events, "canonical", document),
                        ),
                        mock.patch.object(
                            self.helper,
                            "_require_content_digest",
                            side_effect=rejected(
                                events,
                                "digest",
                                f"{label}-digest-disagrees",
                            ),
                        ) as digest,
                        self.assertRaises(self.helper.ProbeError) as raised,
                    ):
                        self.helper._load_root_stage_document(
                            path,
                            maximum,
                            label,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        f"{label}-digest-disagrees",
                    )
                    self.assertEqual(
                        events,
                        ["exists", "metadata", "canonical", "digest"],
                    )
                    digest.assert_called_once_with(document, label)

                with self.subTest(filename=filename, state="valid"):
                    events = []
                    with (
                        mock.patch.object(
                            self.helper,
                            "_path_exists_no_follow",
                            side_effect=observed(events, "exists", True),
                        ),
                        mock.patch.object(
                            self.helper,
                            "_root_file_matches",
                            side_effect=observed(events, "metadata", True),
                        ),
                        mock.patch.object(
                            self.helper,
                            "_load_canonical_document",
                            side_effect=observed(events, "canonical", document),
                        ),
                        mock.patch.object(
                            self.helper,
                            "_require_content_digest",
                            side_effect=observed(events, "digest", None),
                        ),
                    ):
                        self.assertEqual(
                            self.helper._load_root_stage_document(
                                path,
                                maximum,
                                label,
                            ),
                            document,
                        )
                    self.assertEqual(
                        events,
                        ["exists", "metadata", "canonical", "digest"],
                    )

    def test_root_stage_document_callers_forward_exact_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            account_binding = {"account": "exact"}
            ownership = {"ownership": "exact"}
            bindings = self.helper.ValidatedStageBindings(
                account_binding,
                ownership,
            )
            cases = (
                (
                    "account",
                    lambda: self.helper._load_account_binding(plan, state),
                    stage / "account.json",
                    self.helper.MAX_ACCOUNT_BINDING_BYTES,
                    "account-binding",
                ),
                (
                    "ownership",
                    lambda: self.helper._load_launchd_ownership_marker(
                        plan,
                        state,
                    ),
                    stage / "ownership.json",
                    self.helper.MAX_OWNERSHIP_BYTES,
                    "launchd-ownership-marker",
                ),
                (
                    "domain-reset",
                    lambda: self.helper._load_user_domain_reset_authorization(
                        plan,
                        state,
                        account_binding,
                        ownership,
                        "1" * 40,
                        eligible_context(),
                    ),
                    stage / "domain-reset.json",
                    self.helper.MAX_USER_DOMAIN_RESET_BYTES,
                    "user-domain-reset-authorization",
                ),
                (
                    "home-cleanup",
                    lambda: self.helper._load_home_cleanup_authorization(
                        plan,
                        state,
                        bindings,
                    ),
                    stage / "home-cleanup.json",
                    self.helper.MAX_HOME_CLEANUP_BYTES,
                    "home-cleanup-authorization",
                ),
            )
            for caller, operation, path, maximum, label in cases:
                with (
                    self.subTest(caller=caller),
                    mock.patch.object(
                        self.helper,
                        "_load_root_stage_document",
                        return_value=None,
                    ) as load,
                ):
                    self.assertIsNone(operation())
                load.assert_called_once_with(path, maximum, label)

    def test_lifecycle_command_diagnostics_are_fixed_and_do_not_persist_argv(
        self,
    ) -> None:
        secret_argv = [
            "/usr/bin/dscl",
            ".",
            "-create",
            "/Users/twq-secret-value",
        ]
        with mock.patch.object(
            self.helper,
            "_run_lifecycle_command",
            return_value=(0, "ok", ""),
        ) as command:
            self.assertEqual(
                self.helper._require_command_success(
                    secret_argv,
                    command_id="account-create-record",
                    timeout=1.25,
                ),
                "ok",
            )
        command.assert_called_once_with(
            secret_argv,
            maximum=self.helper.MAX_COMMAND_OUTPUT_BYTES,
            timeout=1.25,
        )

        with (
            mock.patch.object(
                self.helper,
                "_run_lifecycle_command",
                return_value=(1, "secret stdout", "secret stderr"),
            ),
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._require_command_success(
                secret_argv,
                command_id="account-create-record",
            )
        self.assertEqual(
            raised.exception.code,
            "lifecycle-command-nonzero-account-create-record",
        )
        self.assertNotIn("twq-secret-value", raised.exception.code)
        self.assertNotIn("secret stdout", raised.exception.code)
        self.assertNotIn("secret stderr", raised.exception.code)

        payloads: dict[str, bytes] = {}

        def record(path: Path, raw: bytes, _mode: int) -> None:
            payloads[path.name] = raw

        context = self.launchd_context()
        with mock.patch.object(
            self.helper,
            "_write_root_file",
            side_effect=record,
        ):
            self.helper._write_lifecycle_artifact(
                artifact_root=Path("/private/tmp/artifact"),
                plan=None,
                binding=None,
                environment=context,
                loaded="",
                terminal="",
                kickstart_pid=None,
                status=2,
                error_code=raised.exception.code,
            )
        lifecycle = json.loads(payloads["lifecycle.json"].decode("utf-8"))
        probe = json.loads(payloads["probe.json"].decode("utf-8"))
        expected_error = {"code": "lifecycle-command-nonzero-account-create-record"}
        self.assertEqual(lifecycle["error"], expected_error)
        self.assertEqual(probe["error"], expected_error)
        self.assertEqual(
            payloads["lifecycle.json"],
            self.helper.canonical_bytes(lifecycle),
        )
        self.assertEqual(payloads["probe.json"], self.helper.canonical_bytes(probe))
        persisted = b"".join(payloads.values())
        self.assertNotIn(b"twq-secret-value", persisted)
        self.assertNotIn(b"secret stdout", persisted)
        self.assertNotIn(b"secret stderr", persisted)

        with (
            mock.patch.object(
                self.helper,
                "_run_lifecycle_command",
                side_effect=self.helper.ProbeError("lifecycle-command-failed"),
            ),
            self.assertRaises(self.helper.ProbeError) as failed,
        ):
            self.helper._require_command_success(
                secret_argv,
                command_id="account-create-record",
            )
        self.assertEqual(
            failed.exception.code,
            "lifecycle-command-failed-account-create-record",
        )

        with (
            mock.patch.object(self.helper, "_run_lifecycle_command") as command,
            self.assertRaisesRegex(
                self.helper.ProbeError,
                "invalid-lifecycle-command-id",
            ),
        ):
            self.helper._require_command_success(
                secret_argv,
                command_id="twq-secret-value",
            )
        command.assert_not_called()

    def test_primary_command_error_survives_reconciliation_failure(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        primary = "lifecycle-command-nonzero-launchd-bootstrap"

        def capture(reconciliation_code: str) -> dict[str, object]:
            with (
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(self.helper, "_normalized_context"),
                mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                mock.patch.object(self.helper, "_create_root_directory"),
                mock.patch.object(
                    self.helper,
                    "_load_lifecycle_state",
                    return_value=(plan, state),
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    return_value=self.helper.ValidatedStageBindings(None, None),
                ),
                mock.patch.object(self.helper, "_require_launchd_absent"),
                mock.patch.object(self.helper, "_create_disposable_account"),
                mock.patch.object(self.helper, "_create_disposable_home"),
                mock.patch.object(self.helper, "_validate_disposable_home_root"),
                mock.patch.object(
                    self.helper,
                    "_require_disposable_uid_available",
                ),
                mock.patch.object(
                    self.helper,
                    "_run_lifecycle_command",
                    return_value=(1, "", "sensitive bootstrap stderr"),
                ),
                mock.patch.object(
                    self.helper,
                    "_reconcile_in_process_bootstrap",
                    side_effect=self.helper.ProbeError(reconciliation_code),
                ),
                mock.patch.object(self.helper, "_write_lifecycle_artifact") as write,
            ):
                self.assertEqual(
                    self.helper.run_launchd_user_lifecycle(
                        stage_root=plan.stage_root,
                        artifact_root=Path("/private/tmp/artifact"),
                        candidate_sha=FROZEN_CANDIDATE_SHA,
                        runner_uid=501,
                        runner_gid=20,
                    ),
                    2,
                )
            return write.call_args.kwargs

        distinct = capture("lifecycle-command-nonzero-launchd-bootout")
        self.assertEqual(distinct["error_code"], primary)
        self.assertEqual(
            distinct["secondary_error_code"],
            "lifecycle-command-nonzero-launchd-bootout",
        )
        duplicate = capture(primary)
        self.assertEqual(duplicate["error_code"], primary)
        self.assertIsNone(duplicate["secondary_error_code"])
        self.assertNotIn("sensitive bootstrap stderr", str(distinct))
        self.assertNotIn("sensitive bootstrap stderr", str(duplicate))

    def test_probe_error_merge_has_one_exact_precedence_rule(self) -> None:
        primary = "lifecycle-command-nonzero-launchd-bootstrap"
        secondary = "lifecycle-command-nonzero-launchd-bootout"
        tertiary = "lifecycle-command-nonzero-process-list"

        self.assertEqual(
            self.helper._merge_probe_error(
                None,
                None,
                self.helper.ProbeError(primary, secondary_code=secondary),
            ),
            (primary, secondary),
        )
        unsafe_primary = "home-library-unsafe-entry"
        unsafe_secondary = "home-library-unsafe-entry-file-xattr"
        unsafe = self.helper.ProbeError(
            unsafe_primary,
            secondary_code=unsafe_secondary,
        )
        self.assertEqual(
            self.helper._merge_probe_error(None, None, unsafe),
            (unsafe_primary, unsafe_secondary),
        )
        self.assertEqual(
            self.helper._merge_probe_error(primary, None, unsafe),
            (primary, unsafe_primary),
        )
        self.assertEqual(
            self.helper._merge_probe_error(primary, secondary, unsafe),
            (primary, secondary),
        )
        self.assertEqual(
            self.helper._merge_probe_error(
                primary,
                None,
                self.helper.ProbeError(secondary),
            ),
            (primary, secondary),
        )
        self.assertEqual(
            self.helper._merge_probe_error(
                primary,
                None,
                self.helper.ProbeError(primary),
            ),
            (primary, None),
        )
        self.assertEqual(
            self.helper._merge_probe_error(
                primary,
                secondary,
                self.helper.ProbeError(tertiary),
            ),
            (primary, secondary),
        )

    def test_account_rollback_failure_is_secondary_to_primary_command_error(
        self,
    ) -> None:
        account = self.helper.DisposableAccount(
            name="twq-0123456789ab",
            uid=502,
            gid=20,
            home=Path("/Users/twq-0123456789ab"),
        )
        primary = "lifecycle-command-nonzero-account-set-authentication-authority"
        secondary = "lifecycle-command-nonzero-account-delete"
        plan = self.helper.LaunchdPlan(
            account=account,
            label="io.nisavid.task-witness.macos-probe.0123456789ab",
            stage_root=Path("/private/var/tmp/task-witness-macos-launchd-123456789-2"),
            helper=Path(
                "/private/var/tmp/task-witness-macos-launchd-123456789-2/helper.py"
            ),
            plist=Path(
                "/private/var/tmp/task-witness-macos-launchd-123456789-2/job.plist"
            ),
        )
        state = self.lifecycle_state(plan)
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        with (
            mock.patch.object(self.helper, "_list_accounts", return_value={"root": 0}),
            mock.patch.object(self.helper, "_account_exists", return_value=False),
            mock.patch.object(
                self.helper,
                "_require_disposable_uid_available",
            ),
            mock.patch.object(
                self.helper,
                "_require_command_success",
                side_effect=["", self.helper.ProbeError(primary)],
            ),
            mock.patch.object(
                self.helper,
                "_read_system_generated_uid",
                return_value=generated_uid,
            ),
            mock.patch.object(self.helper, "_write_account_binding"),
            mock.patch.object(
                self.helper,
                "_rollback_disposable_account_creation",
                side_effect=self.helper.ProbeError(secondary),
            ),
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._create_disposable_account(plan, state)
        self.assertEqual(raised.exception.code, primary)
        self.assertEqual(raised.exception.secondary_code, secondary)

        payloads: dict[str, bytes] = {}

        def record(path: Path, raw: bytes, _mode: int) -> None:
            payloads[path.name] = raw

        with mock.patch.object(
            self.helper,
            "_write_root_file",
            side_effect=record,
        ):
            self.helper._write_lifecycle_artifact(
                artifact_root=Path("/private/tmp/artifact"),
                plan=None,
                binding=None,
                environment=self.launchd_context(),
                loaded="",
                terminal="",
                kickstart_pid=None,
                status=2,
                error_code=raised.exception.code,
                secondary_error_code=raised.exception.secondary_code,
            )
        expected_error = {"code": primary, "secondary_code": secondary}
        lifecycle = json.loads(payloads["lifecycle.json"].decode("utf-8"))
        probe = json.loads(payloads["probe.json"].decode("utf-8"))
        self.assertEqual(lifecycle["error"], expected_error)
        self.assertEqual(probe["error"], expected_error)

    def test_home_creation_rolls_back_only_exact_empty_created_directories(
        self,
    ) -> None:
        for label in (
            "first-chown",
            "home-metadata",
            "probe-metadata",
            "foreign-content",
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / "twq-0123456789ab"
                probe = home / "launchd-probe"
                account = self.helper.DisposableAccount(
                    name="twq-0123456789ab",
                    uid=502,
                    gid=20,
                    home=home,
                )
                chown_count = 0
                metadata_checks: list[tuple[Path, int, int, int]] = []

                def chown(
                    *_args: object,
                    case_label: str = label,
                    expected_probe: Path = probe,
                    **_kwargs: object,
                ) -> None:
                    nonlocal chown_count
                    chown_count += 1
                    if case_label == "first-chown" and chown_count == 1:
                        raise OSError("synthetic first chown failure")
                    if case_label == "foreign-content" and chown_count == 2:
                        (expected_probe / "foreign").write_bytes(b"preserve")
                        raise OSError("synthetic second chown failure")

                def directory_matches(
                    path: Path,
                    mode: int,
                    uid: int,
                    gid: int,
                    case_label: str = label,
                    expected_probe: Path = probe,
                    observed: list[tuple[Path, int, int, int]] = metadata_checks,
                ) -> bool:
                    observed.append((path, mode, uid, gid))
                    return case_label != "home-metadata" and not (
                        case_label == "probe-metadata" and path == expected_probe
                    )

                metadata = (
                    mock.patch.object(
                        self.helper,
                        "_directory_matches",
                        side_effect=directory_matches,
                    )
                    if label in {"home-metadata", "probe-metadata"}
                    else nullcontext()
                )
                with (
                    mock.patch.object(self.helper.os, "chown", side_effect=chown),
                    metadata,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._create_disposable_home(account)

                self.assertEqual(
                    raised.exception.code,
                    {
                        "first-chown": "home-create-new-failed",
                        "home-metadata": "home-create-new-disagrees",
                        "probe-metadata": "home-create-new-disagrees",
                        "foreign-content": "home-create-new-preserved",
                    }[label],
                )
                if label in {"home-metadata", "probe-metadata"}:
                    self.assertEqual(
                        metadata_checks,
                        [(home, 0o700, 502, 20)]
                        if label == "home-metadata"
                        else [
                            (home, 0o700, 502, 20),
                            (probe, 0o700, 502, 20),
                        ],
                    )

                if label == "foreign-content":
                    self.assertTrue(home.is_dir())
                    self.assertEqual((probe / "foreign").read_bytes(), b"preserve")
                else:
                    self.assertFalse(home.exists())

    def test_home_creation_checks_generated_owner_on_home_and_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "twq-0123456789ab"
            probe = home / "launchd-probe"
            account = self.helper.DisposableAccount(
                name=home.name,
                uid=502,
                gid=20,
                home=home,
            )
            checks: list[tuple[Path, int, int, int]] = []

            def directory_matches(
                path: Path,
                mode: int,
                uid: int,
                gid: int,
            ) -> bool:
                checks.append((path, mode, uid, gid))
                return True

            with (
                mock.patch.object(
                    self.helper,
                    "_require_new_directory_no_acl",
                ) as no_acl,
                mock.patch.object(self.helper.os, "chown") as chown,
                mock.patch.object(
                    self.helper,
                    "_directory_matches",
                    side_effect=directory_matches,
                ),
            ):
                self.helper._create_disposable_home(account)

            self.assertEqual(
                checks,
                [
                    (home, 0o700, 502, 20),
                    (probe, 0o700, 502, 20),
                ],
            )
            self.assertEqual(no_acl.call_count, 2)
            self.assertEqual(
                chown.call_args_list,
                [
                    mock.call(home, 502, 20, follow_symlinks=False),
                    mock.call(probe, 502, 20, follow_symlinks=False),
                ],
            )
            self.assertTrue(home.is_dir())
            self.assertTrue(probe.is_dir())

    def test_failed_child_writer_checks_generated_owner_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "twq-0123456789ab"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True)
            home.chmod(0o700)
            probe.chmod(0o700)
            probe_metadata = probe.lstat()
            account = self.helper.DisposableAccount(
                name=home.name,
                uid=502,
                gid=20,
                home=home,
            )
            checks: list[tuple[Path, int, int, int]] = []
            events: list[str] = []

            def directory_matches(
                path: Path,
                mode: int,
                uid: int,
                gid: int,
            ) -> bool:
                checks.append((path, mode, uid, gid))
                events.append("home-check" if path == home else "probe-check")
                return True

            def bounded_names(*_args: object, **_kwargs: object) -> tuple[str, ...]:
                events.append("enumerate")
                return ()

            def write_account_file(path: Path, *_args: object) -> None:
                events.append(f"write:{path.name}")

            with (
                mock.patch.object(
                    self.helper,
                    "_directory_matches",
                    side_effect=directory_matches,
                ),
                mock.patch.object(
                    self.helper,
                    "_bounded_path_directory_names",
                    side_effect=bounded_names,
                ) as names,
                mock.patch.object(
                    self.helper,
                    "_path_exists_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    self.helper,
                    "_write_account_file",
                    side_effect=write_account_file,
                ) as write,
            ):
                self.helper._ensure_failed_child_files(
                    account,
                    self.launchd_context(),
                    "synthetic-child-failure",
                    "synthetic-secondary",
                )

            self.assertEqual(
                checks,
                [
                    (home, 0o700, 502, 20),
                    (probe, 0o700, 502, 20),
                ],
            )
            self.assertEqual(events[:3], ["home-check", "probe-check", "enumerate"])
            self.assertTrue(all(event.startswith("write:") for event in events[3:]))
            names.assert_called_once_with(
                probe,
                probe_metadata,
                self.helper.MAX_LAUNCHD_PROBE_ENTRIES,
                limit_code="home-cleanup-drift",
                failure_code="home-cleanup-drift",
            )
            written = {call.args[0].name: call.args[1] for call in write.call_args_list}
            self.assertEqual(set(written), set(self.helper.LAUNCHD_CHILD_FILES))
            self.assertEqual(written["probe.status"], b"2\n")
            self.assertEqual(written["probe.stdout"], b"probe-error\n")
            self.assertEqual(
                written["probe.stderr"],
                b"task-witness macOS launchd-user probe: synthetic-child-failure\n",
            )
            probe_document = json.loads(written["probe.json"].decode())
            self.assertEqual(
                probe_document["error"],
                {
                    "code": "synthetic-child-failure",
                    "secondary_code": "synthetic-secondary",
                },
            )
            for call in write.call_args_list:
                self.assertIs(call.args[2], account)

            for rejected_check in range(2):
                failure_events: list[str] = []

                def reject_directory(
                    path: Path,
                    _mode: int,
                    _uid: int,
                    _gid: int,
                    observed: list[str] = failure_events,
                    rejected: int = rejected_check,
                ) -> bool:
                    observed.append("home-check" if path == home else "probe-check")
                    return len(observed) - 1 != rejected

                with (
                    self.subTest(rejected_check=rejected_check),
                    mock.patch.object(
                        self.helper,
                        "_directory_matches",
                        side_effect=reject_directory,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_bounded_path_directory_names",
                    ) as failed_names,
                    mock.patch.object(
                        self.helper,
                        "_write_account_file",
                    ) as failed_write,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._ensure_failed_child_files(
                        account,
                        self.launchd_context(),
                        "synthetic-child-failure",
                    )

                self.assertEqual(raised.exception.code, "home-cleanup-drift")
                self.assertEqual(
                    failure_events,
                    ["home-check"]
                    if rejected_check == 0
                    else ["home-check", "probe-check"],
                )
                failed_names.assert_not_called()
                failed_write.assert_not_called()

    def test_home_creation_rejects_an_inherited_extended_acl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "twq-0123456789ab"
            account = self.helper.DisposableAccount(
                name=home.name,
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )

            with (
                mock.patch.object(
                    self.helper,
                    "_require_no_extended_acl",
                    side_effect=self.helper.ProbeError("home-library-unsafe-entry"),
                ) as reject_acl,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._create_disposable_home(account)

            self.assertEqual(raised.exception.code, "home-create-new-disagrees")
            reject_acl.assert_called_once()
            self.assertFalse(home.exists())

    def test_launchd_poll_is_bounded_and_accepts_one_terminal_run(self) -> None:
        context = self.launchd_context()
        plan = self.lifecycle_plan(
            Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        )
        state = self.lifecycle_state(plan)
        terminal = self.launchctl_job(plan, state)
        secret = "terminal-default-environment-secret-canary"
        raw_terminal = self.launchctl_job_with_unrelated_blocks(
            plan,
            state,
            secret=secret,
        )
        running = raw_terminal.replace(
            "\tactive count = 0\n",
            "\tactive count = 1\n",
            1,
        ).replace(
            "\tstate = not running\n",
            "\tstate = running\n\tpid = 1234\n",
            1,
        )
        with (
            mock.patch.dict(os.environ, context, clear=True),
            mock.patch.object(
                self.helper,
                "_run_lifecycle_command",
                side_effect=[(0, running, ""), (0, raw_terminal, "")],
            ),
            mock.patch.object(self.helper, "_child_status", return_value=0),
            mock.patch.object(
                self.helper.time,
                "monotonic",
                side_effect=[0.0, 0.1, 0.15, 0.2],
            ),
            mock.patch.object(self.helper.time, "sleep") as sleep,
        ):
            result = self.helper._poll_launchd_terminal(plan, state)
        self.assertEqual(result, (terminal, 0))
        self.assertNotIn(secret, result[0])
        sleep.assert_called_once_with(self.helper.LAUNCHD_POLL_INTERVAL_SECONDS)

        with (
            mock.patch.dict(os.environ, context, clear=True),
            mock.patch.object(
                self.helper.time,
                "monotonic",
                side_effect=[0.0, 31.0],
            ),
            self.assertRaisesRegex(self.helper.ProbeError, "launchd-job-timeout"),
        ):
            self.helper._poll_launchd_terminal(plan, state)

        xpc_line = f"\t\tXPC_SERVICE_NAME => {plan.label}\n"
        unexpected = terminal.replace(
            xpc_line,
            "\t\tUNEXPECTED_SECRET => must-not-upload\n" + xpc_line,
            1,
        )
        with (
            mock.patch.dict(os.environ, context, clear=True),
            mock.patch.object(
                self.helper,
                "_run_lifecycle_command",
                return_value=(0, unexpected, ""),
            ),
            mock.patch.object(self.helper, "_child_status") as child_status,
            mock.patch.object(
                self.helper.time,
                "monotonic",
                side_effect=[0.0, 0.1],
            ),
            self.assertRaisesRegex(
                self.helper.ProbeError,
                "launchd-job-binding-invalid",
            ),
        ):
            self.helper._poll_launchd_terminal(plan, state)
        child_status.assert_not_called()

    def test_disposable_user_process_exit_wait_is_bounded_and_fail_closed(
        self,
    ) -> None:
        process_remains = self.helper.ProbeError(
            "disposable-user-pid1-parented-processes-remain"
        )
        with (
            mock.patch.object(
                self.helper,
                "_require_no_uid_processes",
                side_effect=[process_remains, None],
            ) as scan,
            mock.patch.object(
                self.helper.time,
                "monotonic",
                side_effect=[0.0, 0.1, 0.2, 0.3],
            ),
            mock.patch.object(self.helper.time, "sleep") as sleep,
        ):
            self.helper._wait_for_no_uid_processes(502)
        self.assertEqual(
            scan.call_args_list,
            [mock.call(502, timeout=10), mock.call(502, timeout=10)],
        )
        sleep.assert_called_once_with(self.helper.PROCESS_EXIT_POLL_INTERVAL_SECONDS)

        with (
            mock.patch.object(self.helper, "_require_no_uid_processes") as scan,
            mock.patch.object(
                self.helper.time,
                "monotonic",
                side_effect=[0.0, 25.0],
            ),
        ):
            self.helper._wait_for_no_uid_processes(502)
        scan.assert_called_once_with(502, timeout=5.0)

        with (
            mock.patch.object(self.helper, "_require_no_uid_processes") as scan,
            mock.patch.object(
                self.helper.time,
                "monotonic",
                side_effect=[0.0, 29.1],
            ),
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._wait_for_no_uid_processes(502)
        self.assertEqual(
            raised.exception.code,
            "disposable-user-process-observation-unavailable",
        )
        self.assertIsNone(raised.exception.secondary_code)
        scan.assert_not_called()

        with (
            mock.patch.object(
                self.helper,
                "_require_no_uid_processes",
                side_effect=process_remains,
            ) as scan,
            mock.patch.object(
                self.helper.time,
                "monotonic",
                side_effect=[0.0, 0.1, 29.9, 30.0],
            ),
            mock.patch.object(self.helper.time, "sleep") as sleep,
            mock.patch.object(self.helper.subprocess, "run") as subprocess_run,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._wait_for_no_uid_processes(502)
        self.assertEqual(
            raised.exception.code,
            "disposable-user-pid1-parented-processes-remain",
        )
        self.assertIsNone(raised.exception.secondary_code)
        scan.assert_called_once_with(502, timeout=10)
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.1)
        subprocess_run.assert_not_called()

        with (
            mock.patch.object(
                self.helper,
                "_require_no_uid_processes",
                side_effect=[
                    self.helper.ProbeError(
                        "disposable-user-pid1-parented-processes-remain"
                    ),
                    self.helper.ProbeError(
                        "disposable-user-root-parented-processes-remain"
                    ),
                ],
            ),
            mock.patch.object(
                self.helper.time,
                "monotonic",
                side_effect=[0.0, 0.1, 0.2, 0.3, 0.4, 30.0],
            ),
            mock.patch.object(self.helper.time, "sleep"),
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._wait_for_no_uid_processes(502)
        self.assertEqual(
            raised.exception.code,
            "disposable-user-process-observation-unstable",
        )
        self.assertIsNone(raised.exception.secondary_code)

        with (
            mock.patch.object(
                self.helper,
                "_require_no_uid_processes",
                side_effect=self.helper.ProbeError("process-list-invalid"),
            ) as scan,
            mock.patch.object(
                self.helper.time,
                "monotonic",
                side_effect=[0.0, 0.1],
            ),
            mock.patch.object(self.helper.time, "sleep") as sleep,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._wait_for_no_uid_processes(502)
        self.assertEqual(raised.exception.code, "process-list-invalid")
        scan.assert_called_once_with(502, timeout=10)
        sleep.assert_not_called()

    def test_owned_job_reconciliation_is_exact_and_absence_is_idempotent(
        self,
    ) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        marker = self.helper._launchd_ownership_document(plan, state)
        job = self.launchctl_job(plan, state)
        target = f"system/{plan.label}"

        with (
            mock.patch.dict(os.environ, self.launchd_context(), clear=True),
            mock.patch.object(
                self.helper,
                "_launchd_job_snapshot",
                side_effect=[job, None],
            ) as snapshot,
            mock.patch.object(
                self.helper,
                "_require_command_success",
            ) as command,
        ):
            self.helper._reconcile_owned_launchd_job(plan, state, marker)
        self.assertEqual(
            [call.args[0] for call in snapshot.call_args_list],
            [plan.label, plan.label],
        )
        command.assert_called_once_with(
            ["/bin/launchctl", "bootout", target],
            command_id="launchd-bootout",
        )

        with (
            mock.patch.dict(os.environ, self.launchd_context(), clear=True),
            mock.patch.object(
                self.helper,
                "_launchd_job_snapshot",
                return_value=None,
            ),
            mock.patch.object(
                self.helper,
                "_require_command_success",
            ) as command,
        ):
            self.helper._reconcile_owned_launchd_job(plan, state, None)
        command.assert_not_called()

    def test_ownership_marker_is_bounded_root_owned_and_state_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            document = self.helper._launchd_ownership_document(plan, state)
            raw = self.helper.canonical_bytes(document)
            self.assertLessEqual(len(raw), self.helper.MAX_OWNERSHIP_BYTES)

            with (
                mock.patch.object(
                    self.helper,
                    "_load_account_binding",
                    return_value=None,
                ),
                mock.patch.object(self.helper, "_write_root_file") as rejected_write,
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "account-binding-missing",
                ),
            ):
                self.helper._write_launchd_ownership_marker(plan, state)
            rejected_write.assert_not_called()

            with (
                mock.patch.object(self.helper, "_write_root_file") as write,
                mock.patch.object(
                    self.helper,
                    "_load_account_binding",
                    return_value={"account": "exact"},
                ),
                mock.patch.object(
                    self.helper,
                    "_load_launchd_ownership_marker",
                    return_value=document,
                ),
            ):
                self.assertEqual(
                    self.helper._write_launchd_ownership_marker(plan, state),
                    document,
                )
            write.assert_called_once_with(stage / "ownership.json", raw, 0o600)

            (stage / "ownership.json").write_bytes(raw)
            with mock.patch.object(
                self.helper,
                "_metadata_matches",
                return_value=True,
            ) as metadata:
                self.assertEqual(
                    self.helper._load_launchd_ownership_marker(plan, state),
                    document,
                )
            metadata.assert_called_once_with(
                stage / "ownership.json",
                kind="file",
                mode=0o600,
                uid=0,
                gid=0,
                nlink=1,
            )

            changed_unsigned = {
                name: value
                for name, value in document.items()
                if name != "content_sha256"
            }
            changed_unsigned["ownership_marker"] = "4" * 32
            changed = self.helper._document_with_digest(changed_unsigned)
            (stage / "ownership.json").write_bytes(self.helper.canonical_bytes(changed))
            with (
                mock.patch.object(
                    self.helper,
                    "_metadata_matches",
                    return_value=True,
                ),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "launchd-ownership-marker-drift",
                ),
            ):
                self.helper._load_launchd_ownership_marker(plan, state)

    def test_exact_stage_forwards_root_metadata_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            plan = self.lifecycle_plan(stage)
            helper_raw = b"exact helper\n"
            plist_raw = b"exact plist\n"
            plan.helper.write_bytes(helper_raw)
            plan.plist.write_bytes(plist_raw)
            unsigned_state = {
                name: value
                for name, value in self.lifecycle_state(plan).items()
                if name != "content_sha256"
            }
            unsigned_state["helper_sha256"] = hashlib.sha256(helper_raw).hexdigest()
            unsigned_state["plist_sha256"] = hashlib.sha256(plist_raw).hexdigest()
            state = self.helper._document_with_digest(unsigned_state)
            (stage / "state.json").write_bytes(self.helper.canonical_bytes(state))

            with (
                mock.patch.object(
                    self.helper,
                    "_directory_matches",
                    return_value=True,
                ) as directory_matches,
                mock.patch.object(
                    self.helper,
                    "_root_file_matches",
                    return_value=True,
                ) as root_file_matches,
                mock.patch.object(
                    self.helper,
                    "_load_account_binding",
                    return_value=None,
                ),
                mock.patch.object(
                    self.helper,
                    "_load_launchd_ownership_marker",
                    return_value=None,
                ),
            ):
                self.assertEqual(
                    self.helper._validate_exact_stage(plan, state),
                    self.helper.ValidatedStageBindings(None, None),
                )

            directory_matches.assert_called_once_with(stage, 0o755, 0, 0)
            self.assertEqual(
                root_file_matches.call_args_list,
                [
                    mock.call(plan.helper, 0o555),
                    mock.call(plan.plist, 0o644),
                ],
            )

            for label, directory_result, root_results, expected_root_calls in (
                ("unsafe-stage", False, (), []),
                ("unsafe-helper", True, (False,), [mock.call(plan.helper, 0o555)]),
                (
                    "unsafe-plist",
                    True,
                    (True, False),
                    [
                        mock.call(plan.helper, 0o555),
                        mock.call(plan.plist, 0o644),
                    ],
                ),
            ):
                with (
                    self.subTest(label=label),
                    mock.patch.object(
                        self.helper,
                        "_directory_matches",
                        return_value=directory_result,
                    ) as rejected_directory,
                    mock.patch.object(
                        self.helper,
                        "_root_file_matches",
                        side_effect=root_results,
                    ) as rejected_files,
                    mock.patch.object(
                        self.helper,
                        "_read_stable_regular_file",
                    ) as stable_read,
                    mock.patch.object(
                        self.helper,
                        "_load_account_binding",
                    ) as load_account,
                    mock.patch.object(
                        self.helper,
                        "_load_launchd_ownership_marker",
                    ) as load_ownership,
                    mock.patch.object(
                        self.helper,
                        "_load_user_domain_reset_authorization",
                    ) as load_reset,
                    mock.patch.object(
                        self.helper,
                        "_load_home_cleanup_authorization",
                    ) as load_home_cleanup,
                    mock.patch.object(Path, "unlink") as path_unlink,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    mock.patch.object(self.helper.os, "rename") as rename,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_exact_stage(plan, state)

                self.assertEqual(raised.exception.code, "stage-cleanup-drift")
                rejected_directory.assert_called_once_with(stage, 0o755, 0, 0)
                self.assertEqual(rejected_files.call_args_list, expected_root_calls)
                stable_read.assert_not_called()
                load_account.assert_not_called()
                load_ownership.assert_not_called()
                load_reset.assert_not_called()
                load_home_cleanup.assert_not_called()
                path_unlink.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                rename.assert_not_called()

    def test_lifecycle_state_checks_root_metadata_before_content(self) -> None:
        stage = Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        state_path = stage / "state.json"
        with (
            mock.patch.object(
                self.helper,
                "_root_file_matches",
                return_value=False,
            ) as matches,
            mock.patch.object(
                self.helper,
                "_load_canonical_document",
            ) as canonical,
            mock.patch.object(
                self.helper,
                "_require_content_digest",
            ) as digest,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._load_lifecycle_state(
                stage,
                runner_uid=501,
                runner_gid=20,
                environment=eligible_context(),
            )
        self.assertEqual(raised.exception.code, "lifecycle-state-drift")
        self.assertIsNone(raised.exception.secondary_code)
        matches.assert_called_once_with(state_path, 0o600)
        canonical.assert_not_called()
        digest.assert_not_called()

    def test_precleanup_artifact_forwards_root_metadata_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "artifact"
            artifact.mkdir()
            expected = set(self.helper.LAUNCHD_ARTIFACT_FILES) - {"cleanup.json"}
            for name in expected:
                (artifact / name).write_bytes(b"")

            with (
                mock.patch.object(
                    self.helper,
                    "_directory_matches",
                    return_value=True,
                ) as directory_matches,
                mock.patch.object(
                    self.helper,
                    "_root_file_matches",
                    return_value=True,
                ) as root_file_matches,
            ):
                self.helper._validate_precleanup_artifact(artifact)

            directory_matches.assert_called_once_with(artifact, 0o700, 0, 0)
            self.assertCountEqual(
                root_file_matches.call_args_list,
                [mock.call(artifact / name, 0o600) for name in expected],
            )

            before = {path.name: path.read_bytes() for path in artifact.iterdir()}
            for label, directory_result, rejected_name in (
                ("unsafe-root", False, None),
                *((f"unsafe-{name}", True, name) for name in sorted(expected)),
            ):

                def file_matches(
                    path: Path,
                    _mode: int,
                    selected: str | None = rejected_name,
                ) -> bool:
                    return path.name != selected

                with (
                    self.subTest(label=label),
                    mock.patch.object(
                        self.helper,
                        "_directory_matches",
                        return_value=directory_result,
                    ) as rejected_directory,
                    mock.patch.object(
                        self.helper,
                        "_root_file_matches",
                        side_effect=file_matches,
                    ) as rejected_files,
                    mock.patch.object(self.helper, "_write_root_file") as write,
                    mock.patch.object(self.helper.os, "chown") as chown,
                    mock.patch.object(Path, "unlink") as path_unlink,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    mock.patch.object(self.helper.os, "rename") as rename,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_precleanup_artifact(artifact)

                self.assertEqual(
                    raised.exception.code,
                    "launchd-artifact-precleanup-drift",
                )
                rejected_directory.assert_called_once_with(artifact, 0o700, 0, 0)
                if rejected_name is None:
                    rejected_files.assert_not_called()
                else:
                    self.assertEqual(
                        rejected_files.call_args_list[-1],
                        mock.call(artifact / rejected_name, 0o600),
                    )
                    self.assertEqual(
                        sum(
                            call == mock.call(artifact / rejected_name, 0o600)
                            for call in rejected_files.call_args_list
                        ),
                        1,
                    )
                write.assert_not_called()
                chown.assert_not_called()
                path_unlink.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                rename.assert_not_called()
                self.assertEqual(
                    {path.name: path.read_bytes() for path in artifact.iterdir()},
                    before,
                )

    def test_stage_inventory_requires_account_binding_before_launchd_ownership(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            plan = self.lifecycle_plan(stage)
            helper_raw = b"exact helper\n"
            plist_raw = b"exact plist\n"
            (stage / "helper.py").write_bytes(helper_raw)
            (stage / "job.plist").write_bytes(plist_raw)
            unsigned_state = {
                name: value
                for name, value in self.lifecycle_state(plan).items()
                if name != "content_sha256"
            }
            unsigned_state["helper_sha256"] = hashlib.sha256(helper_raw).hexdigest()
            unsigned_state["plist_sha256"] = hashlib.sha256(plist_raw).hexdigest()
            state = self.helper._document_with_digest(unsigned_state)
            (stage / "state.json").write_bytes(self.helper.canonical_bytes(state))
            generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
            account_binding = self.helper._account_binding_document(
                plan,
                state,
                generated_uid,
            )
            ownership = self.helper._launchd_ownership_document(plan, state)

            with mock.patch.object(
                self.helper,
                "_metadata_matches",
                return_value=True,
            ):
                self.assertEqual(
                    self.helper._validate_exact_stage(plan, state),
                    self.helper.ValidatedStageBindings(None, None),
                )

                (stage / "state.json").unlink()
                with (
                    mock.patch.object(
                        self.helper,
                        "_read_stable_regular_file",
                    ) as stable_read,
                    mock.patch.object(
                        self.helper,
                        "_load_account_binding",
                    ) as load_account,
                    mock.patch.object(
                        self.helper,
                        "_load_launchd_ownership_marker",
                    ) as load_ownership,
                    mock.patch.object(
                        self.helper,
                        "_load_user_domain_reset_authorization",
                    ) as load_reset,
                    mock.patch.object(
                        self.helper,
                        "_load_home_cleanup_authorization",
                    ) as load_home_cleanup,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    mock.patch.object(Path, "unlink") as path_unlink,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_exact_stage(plan, state)
                self.assertEqual(raised.exception.code, "stage-cleanup-drift")
                stable_read.assert_not_called()
                load_account.assert_not_called()
                load_ownership.assert_not_called()
                load_reset.assert_not_called()
                load_home_cleanup.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                path_unlink.assert_not_called()
                (stage / "state.json").write_bytes(self.helper.canonical_bytes(state))

                unknown = stage / "private-unknown-stage-canary"
                unknown.write_bytes(b"preserve")
                with (
                    mock.patch.object(
                        self.helper,
                        "_read_stable_regular_file",
                    ) as stable_read,
                    mock.patch.object(
                        self.helper,
                        "_load_account_binding",
                    ) as load_account,
                    mock.patch.object(
                        self.helper,
                        "_load_launchd_ownership_marker",
                    ) as load_ownership,
                    mock.patch.object(
                        self.helper,
                        "_load_user_domain_reset_authorization",
                    ) as load_reset,
                    mock.patch.object(
                        self.helper,
                        "_load_home_cleanup_authorization",
                    ) as load_home_cleanup,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    mock.patch.object(Path, "unlink") as path_unlink,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._validate_exact_stage(plan, state)
                self.assertEqual(raised.exception.code, "stage-cleanup-drift")
                stable_read.assert_not_called()
                load_account.assert_not_called()
                load_ownership.assert_not_called()
                load_reset.assert_not_called()
                load_home_cleanup.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                path_unlink.assert_not_called()
                self.assertEqual(unknown.read_bytes(), b"preserve")
                unknown.unlink()

                (stage / "ownership.json").write_bytes(
                    self.helper.canonical_bytes(ownership)
                )
                with self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "stage-cleanup-drift",
                ):
                    self.helper._validate_exact_stage(plan, state)
                (stage / "ownership.json").unlink()

                (stage / "account.json").write_bytes(
                    self.helper.canonical_bytes(account_binding)
                )
                self.assertEqual(
                    self.helper._validate_exact_stage(plan, state),
                    self.helper.ValidatedStageBindings(account_binding, None),
                )

                (stage / "ownership.json").write_bytes(
                    self.helper.canonical_bytes(ownership)
                )
                self.assertEqual(
                    self.helper._validate_exact_stage(plan, state),
                    self.helper.ValidatedStageBindings(account_binding, ownership),
                )

                reset = {"content_sha256": "4" * 64}
                (stage / "domain-reset.json").write_bytes(b"exact reset marker")
                with mock.patch.object(
                    self.helper,
                    "_load_user_domain_reset_authorization",
                    return_value=reset,
                ) as load_reset:
                    self.assertEqual(
                        self.helper._validate_exact_stage(
                            plan,
                            state,
                            user_domain_reset_authorization="1" * 40,
                            environment=eligible_context(),
                        ),
                        self.helper.ValidatedStageBindings(
                            account_binding,
                            ownership,
                            reset,
                        ),
                    )
                load_reset.assert_called_once()

                journal = {"content_sha256": "5" * 64}
                (stage / "home-cleanup.json").write_bytes(b"exact home cleanup marker")
                with (
                    mock.patch.object(
                        self.helper,
                        "_load_user_domain_reset_authorization",
                        return_value=reset,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_load_home_cleanup_authorization",
                        return_value=journal,
                    ) as load_home_cleanup,
                ):
                    self.assertEqual(
                        self.helper._validate_exact_stage(
                            plan,
                            state,
                            user_domain_reset_authorization="1" * 40,
                            environment=eligible_context(),
                        ),
                        self.helper.ValidatedStageBindings(
                            account_binding,
                            ownership,
                            reset,
                            journal,
                        ),
                    )
                load_home_cleanup.assert_called_once()

                (stage / "domain-reset.json").unlink()
                with mock.patch.object(
                    self.helper,
                    "_load_home_cleanup_authorization",
                    return_value=journal,
                ):
                    self.assertEqual(
                        self.helper._validate_exact_stage(plan, state),
                        self.helper.ValidatedStageBindings(
                            account_binding,
                            ownership,
                            None,
                            journal,
                        ),
                    )

                (stage / "ownership.json").unlink()
                with mock.patch.object(
                    self.helper,
                    "_load_home_cleanup_authorization",
                    return_value=journal,
                ):
                    self.assertEqual(
                        self.helper._validate_exact_stage(plan, state),
                        self.helper.ValidatedStageBindings(
                            account_binding,
                            None,
                            None,
                            journal,
                        ),
                    )

                (stage / "domain-reset.json").write_bytes(
                    b"private-reset-without-ownership-canary"
                )
                (stage / "account.json").write_bytes(
                    b"private-malformed-account-canary"
                )
                for home_cleanup_present in (True, False):
                    with self.subTest(
                        reset_without_ownership=True,
                        home_cleanup_present=home_cleanup_present,
                    ):
                        if not home_cleanup_present:
                            (stage / "home-cleanup.json").unlink()
                        with (
                            mock.patch.object(
                                self.helper,
                                "_load_account_binding",
                                return_value=account_binding,
                            ) as load_account,
                            mock.patch.object(
                                self.helper,
                                "_load_launchd_ownership_marker",
                                return_value=None,
                            ) as load_ownership,
                            mock.patch.object(
                                self.helper,
                                "_load_user_domain_reset_authorization",
                                return_value=reset,
                            ) as load_reset,
                            mock.patch.object(
                                self.helper,
                                "_load_home_cleanup_authorization",
                                return_value=journal,
                            ) as load_home_cleanup,
                            self.assertRaises(self.helper.ProbeError) as raised,
                        ):
                            self.helper._validate_exact_stage(
                                plan,
                                state,
                                user_domain_reset_authorization="1" * 40,
                                environment=eligible_context(),
                            )
                        self.assertEqual(
                            raised.exception.code,
                            "stage-cleanup-drift",
                        )
                        load_account.assert_not_called()
                        load_ownership.assert_not_called()
                        load_reset.assert_not_called()
                        load_home_cleanup.assert_not_called()
                (stage / "domain-reset.json").unlink()
                (stage / "home-cleanup.json").write_bytes(b"exact home cleanup marker")

                (stage / "account.json").unlink()
                with self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "stage-cleanup-drift",
                ):
                    self.helper._validate_exact_stage(plan, state)

    def test_foreign_or_unknown_launchd_job_is_never_booted_out(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        marker = self.helper._launchd_ownership_document(plan, state)
        owned = self.launchctl_job(plan, state)
        mutations = {
            "missing-marker": (owned, None),
            "foreign-path": (
                owned.replace(str(plan.plist), "/Library/LaunchDaemons/foreign.plist"),
                marker,
            ),
            "foreign-program": (
                owned.replace("program = /usr/bin/env", "program = /bin/false"),
                marker,
            ),
            "foreign-argument": (
                owned.replace("\t\t-I\n", "\t\t-E\n"),
                marker,
            ),
            "foreign-user": (
                owned.replace(
                    f"username = {plan.account.name}",
                    "username = root",
                ),
                marker,
            ),
            "foreign-marker": (
                owned.replace("3" * 32, "4" * 32),
                marker,
            ),
            "duplicate-path": (
                owned.replace(
                    f"\tpath = {plan.plist}\n",
                    f"\tpath = {plan.plist}\n\tpath = {plan.plist}\n",
                ),
                marker,
            ),
        }
        for label, (job, observed_marker) in mutations.items():
            with (
                self.subTest(label=label),
                mock.patch.object(
                    self.helper,
                    "_launchd_job_snapshot",
                    return_value=job,
                ),
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                ) as command,
                self.assertRaises(self.helper.ProbeError),
            ):
                self.helper._reconcile_owned_launchd_job(
                    plan,
                    state,
                    observed_marker,
                )
            command.assert_not_called()

    def test_bootstrap_attempt_reconciles_observed_binding_not_command_status(
        self,
    ) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        owned = self.launchctl_job(plan, state)
        foreign = owned.replace(
            str(plan.plist),
            "/Library/LaunchDaemons/foreign.plist",
        )
        for label, bootstrap_result, live_job, expected_events in (
            (
                "bootstrap-nonzero-absent",
                self.helper.ProbeError("lifecycle-command-nonzero"),
                None,
                [
                    "require-absent",
                    "require-absent",
                    "bootstrap",
                    "observe-job",
                    "require-no-processes",
                ],
            ),
            (
                "bootstrap-nonzero-owned-live",
                self.helper.ProbeError("lifecycle-command-nonzero"),
                owned,
                [
                    "require-absent",
                    "require-absent",
                    "bootstrap",
                    "observe-job",
                    "bootout",
                    "observe-job",
                    "require-no-processes",
                ],
            ),
            (
                "bootstrap-timeout-owned-live",
                self.helper.ProbeError("lifecycle-command-failed"),
                owned,
                [
                    "require-absent",
                    "require-absent",
                    "bootstrap",
                    "observe-job",
                    "bootout",
                    "observe-job",
                    "require-no-processes",
                ],
            ),
            (
                "bootstrap-nonzero-foreign-live",
                self.helper.ProbeError("lifecycle-command-nonzero"),
                foreign,
                [
                    "require-absent",
                    "require-absent",
                    "bootstrap",
                    "observe-job",
                ],
            ),
            (
                "owned-marker-write-failure",
                "",
                owned,
                [
                    "require-absent",
                    "require-absent",
                    "bootstrap",
                    "write-marker",
                    "observe-job",
                    "bootout",
                    "observe-job",
                    "require-no-processes",
                ],
            ),
            (
                "foreign-marker-write-failure",
                "",
                foreign,
                [
                    "require-absent",
                    "require-absent",
                    "bootstrap",
                    "write-marker",
                    "observe-job",
                ],
            ),
        ):
            events: list[str] = []

            def require_absent(
                _label: str,
                observed_events: list[str] = events,
            ) -> None:
                observed_events.append("require-absent")

            def command(
                argv: list[str],
                bootstrap_outcome: str | Exception = bootstrap_result,
                observed_events: list[str] = events,
                **_kwargs: object,
            ) -> str:
                if argv[:3] == ["/bin/launchctl", "bootstrap", "system"]:
                    observed_events.append("bootstrap")
                    if isinstance(bootstrap_outcome, Exception):
                        raise bootstrap_outcome
                    return bootstrap_outcome
                if argv == [
                    "/bin/launchctl",
                    "bootout",
                    f"system/{plan.label}",
                ]:
                    observed_events.append("bootout")
                    return ""
                raise AssertionError(argv)

            def write_marker(
                *_args: object,
                observed_events: list[str] = events,
            ) -> dict:
                observed_events.append("write-marker")
                raise self.helper.ProbeError("root-file-disagrees")

            def snapshot(
                _label: str,
                observed_events: list[str] = events,
                expected_job: str | None = live_job,
            ) -> str | None:
                observed_events.append("observe-job")
                if expected_job == owned and observed_events.count("observe-job") == 2:
                    return None
                return expected_job

            def require_no_processes(
                _uid: int,
                *,
                timeout: float = self.helper.COMMAND_TIMEOUT_SECONDS,
                observed_events: list[str] = events,
            ) -> None:
                self.assertGreater(timeout, 0)
                observed_events.append("require-no-processes")

            with (
                self.subTest(label=label),
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(self.helper, "_normalized_context"),
                mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                mock.patch.object(self.helper, "_create_root_directory"),
                mock.patch.object(
                    self.helper,
                    "_load_lifecycle_state",
                    return_value=(plan, state),
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    return_value=self.helper.ValidatedStageBindings(None, None),
                ),
                mock.patch.object(self.helper, "_create_disposable_account"),
                mock.patch.object(self.helper, "_create_disposable_home"),
                mock.patch.object(self.helper, "_validate_disposable_home_root"),
                mock.patch.object(
                    self.helper,
                    "_require_disposable_uid_available",
                ),
                mock.patch.object(
                    self.helper,
                    "_require_launchd_absent",
                    side_effect=require_absent,
                ),
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                    side_effect=command,
                ),
                mock.patch.object(
                    self.helper,
                    "_write_launchd_ownership_marker",
                    side_effect=write_marker,
                ),
                mock.patch.object(
                    self.helper,
                    "_launchd_job_snapshot",
                    side_effect=snapshot,
                ),
                mock.patch.object(
                    self.helper,
                    "_require_no_uid_processes",
                    side_effect=require_no_processes,
                ),
                mock.patch.object(self.helper, "_write_lifecycle_artifact"),
            ):
                status = self.helper.run_launchd_user_lifecycle(
                    stage_root=plan.stage_root,
                    artifact_root=Path("/private/tmp/artifact"),
                    candidate_sha=FROZEN_CANDIDATE_SHA,
                    runner_uid=501,
                    runner_gid=20,
                )
                self.assertEqual(status, 2)
                self.assertEqual(events, expected_events)

    def test_prebootstrap_collision_has_no_in_process_cleanup_authority(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        with (
            mock.patch.dict(os.environ, eligible_context(), clear=True),
            mock.patch.object(self.helper, "_normalized_context"),
            mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
            mock.patch.object(self.helper, "_create_root_directory"),
            mock.patch.object(
                self.helper,
                "_load_lifecycle_state",
                return_value=(plan, state),
            ),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=self.helper.ValidatedStageBindings(None, None),
            ),
            mock.patch.object(self.helper, "_create_disposable_account"),
            mock.patch.object(self.helper, "_create_disposable_home"),
            mock.patch.object(self.helper, "_validate_disposable_home_root"),
            mock.patch.object(self.helper, "_require_disposable_uid_available"),
            mock.patch.object(
                self.helper,
                "_require_launchd_absent",
                side_effect=[
                    None,
                    self.helper.ProbeError("launchd-label-already-loaded"),
                ],
            ),
            mock.patch.object(
                self.helper,
                "_require_command_success",
            ) as launchctl_mutation,
            mock.patch.object(
                self.helper,
                "_launchd_job_snapshot",
            ) as observe_job,
            mock.patch.object(
                self.helper,
                "_require_no_uid_processes",
            ) as require_no_processes,
            mock.patch.object(self.helper, "_write_lifecycle_artifact"),
        ):
            status = self.helper.run_launchd_user_lifecycle(
                stage_root=plan.stage_root,
                artifact_root=Path("/private/tmp/artifact"),
                candidate_sha=FROZEN_CANDIDATE_SHA,
                runner_uid=501,
                runner_gid=20,
            )
        self.assertEqual(status, 2)
        launchctl_mutation.assert_not_called()
        observe_job.assert_not_called()
        require_no_processes.assert_not_called()

    def test_lifecycle_command_has_a_scrubbed_bounded_process_boundary(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout=b"ok\n", stderr=b"")
        with mock.patch.object(
            self.helper.subprocess,
            "run",
            return_value=completed,
        ) as run:
            self.assertEqual(
                self.helper._run_lifecycle_command(["/bin/launchctl", "print", "x"]),
                (0, "ok", ""),
            )
        arguments = run.call_args
        self.assertEqual(arguments.args[0], ["/bin/launchctl", "print", "x"])
        self.assertFalse(arguments.kwargs["check"])
        self.assertEqual(arguments.kwargs["timeout"], 10)
        self.assertEqual(
            arguments.kwargs["env"],
            {
                "HOME": "/var/empty",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "TZ": "UTC",
            },
        )

    def test_stable_helper_reader_rejects_symlinks_and_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.py"
            source.write_bytes(b"print('exact')\n")
            self.assertEqual(
                self.helper._read_stable_regular_file(source, 1024, "source"),
                b"print('exact')\n",
            )
            hardlink = root / "hardlink.py"
            os.link(source, hardlink)
            with self.assertRaisesRegex(self.helper.ProbeError, "unsafe-source"):
                self.helper._read_stable_regular_file(source, 1024, "source")
            hardlink.unlink()
            symlink = root / "symlink.py"
            symlink.symlink_to(source)
            with self.assertRaisesRegex(self.helper.ProbeError, "unreadable-source"):
                self.helper._read_stable_regular_file(symlink, 1024, "source")

    def test_initialize_rejects_root_runner_gid_before_account_planning(self) -> None:
        with (
            mock.patch.object(
                self.helper,
                "validate_prestaged_helper",
                return_value=b"trusted helper",
            ) as validate_helper,
            mock.patch.object(
                self.helper,
                "_list_accounts",
                return_value={"root": 0, "runner": 501},
            ) as list_accounts,
            mock.patch.object(
                self.helper,
                "_write_root_file",
                side_effect=self.helper.ProbeError("unexpected-stage-write"),
            ),
            self.assertRaisesRegex(
                self.helper.ProbeError,
                "invalid-lifecycle-arguments",
            ),
        ):
            self.helper._initialize_lifecycle(
                stage_root=Path("/private/var/tmp/task-witness-macos-launchd-123-1"),
                expected_helper_sha256="1" * 64,
                runner_uid=501,
                runner_gid=0,
                environment=eligible_context(),
            )
        validate_helper.assert_not_called()
        list_accounts.assert_not_called()

    def test_run_rejects_root_runner_gid_before_artifact_or_account_mutation(
        self,
    ) -> None:
        with (
            mock.patch.dict(os.environ, eligible_context(), clear=True),
            mock.patch.object(self.helper, "_normalized_context"),
            mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
            mock.patch.object(self.helper, "_create_root_directory") as create_root,
            mock.patch.object(self.helper, "_load_lifecycle_state") as load_state,
            self.assertRaisesRegex(
                self.helper.ProbeError,
                "invalid-lifecycle-arguments",
            ),
        ):
            self.helper.run_launchd_user_lifecycle(
                stage_root=Path("/private/var/tmp/task-witness-macos-launchd-123-1"),
                artifact_root=Path(
                    "/private/tmp/task-witness-macos-launchd-user-probe"
                ),
                candidate_sha=FROZEN_CANDIDATE_SHA,
                runner_uid=501,
                runner_gid=0,
            )
        create_root.assert_not_called()
        load_state.assert_not_called()

    def test_root_runner_gid_retains_exact_helper_only_cleanup_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir(mode=0o755)
            helper = stage / "helper.py"
            helper_raw = b"trusted staged helper\n"
            helper.write_bytes(helper_raw)
            helper.chmod(0o555)
            helper_sha256 = hashlib.sha256(helper_raw).hexdigest()
            artifact = root / "task-witness-macos-launchd-user-probe"
            context = eligible_context()
            account_name, label = self.helper._launchd_identity(context)
            home = Path("/Users") / account_name
            observed_paths: list[Path] = []

            def absent(path: Path) -> bool:
                observed_paths.append(path)
                return False

            with (
                mock.patch.dict(os.environ, context, clear=True),
                mock.patch.object(self.helper, "__file__", str(helper)),
                mock.patch.object(self.helper.os, "geteuid", return_value=0),
                mock.patch.object(
                    self.helper,
                    "LAUNCHD_STAGE_RE",
                    re.compile(re.escape(str(stage))),
                ),
                mock.patch.object(
                    self.helper,
                    "_metadata_matches",
                    return_value=True,
                ),
                mock.patch.object(
                    self.helper,
                    "_launchd_job_snapshot",
                    return_value=None,
                ) as observe_job,
                mock.patch.object(
                    self.helper,
                    "_list_accounts",
                    return_value={"root": 0, "runner": 501},
                ),
                mock.patch.object(
                    self.helper,
                    "_account_exists",
                    return_value=False,
                ),
                mock.patch.object(
                    self.helper,
                    "_path_exists_no_follow",
                    side_effect=absent,
                ),
                mock.patch.object(self.helper, "_load_lifecycle_state") as load_state,
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                ) as mutate,
            ):
                status = self.helper.cleanup_launchd_user_lifecycle(
                    stage_root=stage,
                    artifact_root=artifact,
                    expected_helper_sha256=helper_sha256,
                    runner_uid=501,
                    runner_gid=0,
                )

            self.assertEqual(status, 2)
            self.assertFalse(stage.exists())
            self.assertFalse(artifact.exists())
            self.assertIn(home, observed_paths)
            self.assertIn(artifact, observed_paths)
            observe_job.assert_called_once_with(label)
            load_state.assert_not_called()
            mutate.assert_not_called()

    def test_cleanup_removes_only_exact_helper_after_initialization_failure(
        self,
    ) -> None:
        for failure_point in (
            "before-state",
            "job-write-post-create",
            "state-write-rollback",
            "state-write-post-create",
        ):
            with (
                self.subTest(failure_point=failure_point),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                stage = root / "stage"
                stage.mkdir(mode=0o755)
                helper = stage / "helper.py"
                helper_raw = b"trusted staged helper\n"
                helper.write_bytes(helper_raw)
                helper.chmod(0o555)
                helper_sha256 = hashlib.sha256(helper_raw).hexdigest()
                artifact = root / "artifact"
                context = eligible_context()
                account_name, label = self.helper._launchd_identity(context)
                home = Path("/Users") / account_name
                writes: list[str] = []
                root_file_checks: list[tuple[str, int]] = []

                def fail_initialization(
                    path: Path,
                    raw: bytes,
                    mode: int,
                    *,
                    observed_writes: list[str] = writes,
                    selected_failure_point: str = failure_point,
                ) -> None:
                    observed_writes.append(path.name)
                    if path.name == "job.plist" and selected_failure_point.startswith(
                        "state-write-"
                    ):
                        path.write_bytes(raw)
                        path.chmod(mode)
                        return
                    if selected_failure_point.endswith("post-create"):
                        path.write_bytes(raw)
                        path.chmod(mode)
                    raise self.helper.ProbeError("synthetic-initialization-failure")

                def root_file_matches(
                    path: Path,
                    mode: int,
                    observed_checks: list[tuple[str, int]] = root_file_checks,
                ) -> bool:
                    observed_checks.append((path.name, mode))
                    return True

                with (
                    mock.patch.dict(os.environ, context, clear=True),
                    mock.patch.object(self.helper, "__file__", str(helper)),
                    mock.patch.object(
                        self.helper, "_metadata_matches", return_value=True
                    ),
                    mock.patch.object(
                        self.helper,
                        "_root_file_matches",
                        side_effect=root_file_matches,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_list_accounts",
                        return_value={"root": 0, "runner": 501},
                    ),
                    mock.patch.object(
                        self.helper,
                        "_process_records",
                        return_value=(
                            self.helper.ProcessRecord(
                                uid=0,
                                pid=1,
                                ppid=0,
                                pgid=1,
                                state="Ss",
                                command="launchd",
                            ),
                        ),
                    ),
                    mock.patch.object(
                        self.helper,
                        "_write_root_file",
                        side_effect=fail_initialization,
                    ),
                    self.assertRaisesRegex(
                        self.helper.ProbeError,
                        "synthetic-initialization-failure",
                    ),
                ):
                    self.helper._initialize_lifecycle(
                        stage_root=stage,
                        expected_helper_sha256=helper_sha256,
                        runner_uid=501,
                        runner_gid=20,
                        environment=context,
                    )

                self.assertEqual(
                    writes,
                    ["job.plist"]
                    if failure_point in {"before-state", "job-write-post-create"}
                    else ["job.plist", "state.json"],
                )
                self.assertEqual(
                    [
                        check
                        for check in root_file_checks
                        if check[0] in {"job.plist", "state.json"}
                    ],
                    {
                        "before-state": [],
                        "job-write-post-create": [("job.plist", 0o644)],
                        "state-write-rollback": [("job.plist", 0o644)],
                        "state-write-post-create": [
                            ("state.json", 0o600),
                            ("job.plist", 0o644),
                        ],
                    }[failure_point],
                )
                self.assertEqual(
                    {entry.name for entry in stage.iterdir()}, {"helper.py"}
                )

                observed_paths: list[Path] = []

                def absent(
                    path: Path,
                    *,
                    paths: list[Path] = observed_paths,
                ) -> bool:
                    paths.append(path)
                    return False

                with (
                    mock.patch.dict(os.environ, context, clear=True),
                    mock.patch.object(self.helper, "__file__", str(helper)),
                    mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                    mock.patch.object(
                        self.helper, "_metadata_matches", return_value=True
                    ),
                    mock.patch.object(
                        self.helper,
                        "_launchd_job_snapshot",
                        return_value=None,
                    ) as observe_job,
                    mock.patch.object(
                        self.helper,
                        "_list_accounts",
                        return_value={"root": 0, "runner": 501},
                    ),
                    mock.patch.object(
                        self.helper,
                        "_account_exists",
                        return_value=False,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_path_exists_no_follow",
                        side_effect=absent,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_load_lifecycle_state",
                    ) as load_state,
                    mock.patch.object(
                        self.helper,
                        "_require_command_success",
                    ) as mutate,
                    mock.patch.object(
                        self.helper,
                        "_create_disposable_account",
                    ) as create_account,
                    mock.patch.object(
                        self.helper,
                        "remove_exact_disposable_home",
                    ) as remove_home,
                    mock.patch.object(
                        self.helper,
                        "_write_root_file",
                    ) as write_artifact,
                ):
                    status = self.helper.cleanup_launchd_user_lifecycle(
                        stage_root=stage,
                        artifact_root=artifact,
                        expected_helper_sha256=helper_sha256,
                        runner_uid=501,
                        runner_gid=20,
                    )

                self.assertEqual(status, 2)
                self.assertFalse(stage.exists())
                self.assertFalse(artifact.exists())
                self.assertIn(home, observed_paths)
                self.assertIn(artifact, observed_paths)
                observe_job.assert_called_once_with(label)
                load_state.assert_not_called()
                mutate.assert_not_called()
                create_account.assert_not_called()
                remove_home.assert_not_called()
                write_artifact.assert_not_called()

    def test_initialization_rollback_preserves_metadata_or_byte_drift(self) -> None:
        for label, metadata_matches in (
            ("metadata-drift", False),
            ("byte-drift", True),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                stage = Path(directory) / "stage"
                stage.mkdir()
                published_bytes: list[bytes] = []
                published_maximum: list[int] = []

                def fail_after_publish(
                    path: Path,
                    raw: bytes,
                    mode: int,
                    selected_label: str = label,
                    observed_bytes: list[bytes] = published_bytes,
                    observed_maximum: list[int] = published_maximum,
                ) -> None:
                    observed = (
                        raw if selected_label == "metadata-drift" else b"x" + raw[1:]
                    )
                    path.write_bytes(observed)
                    path.chmod(mode)
                    observed_bytes.append(observed)
                    observed_maximum.append(len(raw))
                    raise self.helper.ProbeError("synthetic-initialization-failure")

                def read_published(
                    *_args: object,
                    observed_bytes: list[bytes] = published_bytes,
                    **_kwargs: object,
                ) -> bytes:
                    return observed_bytes[0]

                account_name = "twq-0123456789ab"
                launchd_label = "io.nisavid.task-witness.macos-probe.0123456789ab"
                with (
                    mock.patch.object(self.helper, "_require_nonroot_runner_gid"),
                    mock.patch.object(
                        self.helper,
                        "validate_prestaged_helper",
                        return_value=b"trusted helper",
                    ),
                    mock.patch.object(self.helper, "_list_accounts", return_value={}),
                    mock.patch.object(self.helper, "_process_records", return_value=()),
                    mock.patch.object(
                        self.helper,
                        "choose_disposable_uid",
                        return_value=502,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_launchd_identity",
                        return_value=(account_name, launchd_label),
                    ),
                    mock.patch.object(
                        self.helper,
                        "_write_root_file",
                        side_effect=fail_after_publish,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_root_file_matches",
                        return_value=metadata_matches,
                    ) as rollback_matches,
                    mock.patch.object(
                        self.helper,
                        "_read_stable_regular_file",
                        side_effect=read_published,
                    ) as rollback_read,
                    mock.patch.object(Path, "unlink") as rollback_unlink,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper._initialize_lifecycle(
                        stage_root=stage,
                        expected_helper_sha256="1" * 64,
                        runner_uid=501,
                        runner_gid=20,
                        environment=self.launchd_context(),
                    )

                path = stage / "job.plist"
                self.assertEqual(
                    raised.exception.code,
                    "stage-initialization-preserved",
                )
                rollback_matches.assert_called_once_with(path, 0o644)
                if metadata_matches:
                    rollback_read.assert_called_once_with(
                        path,
                        published_maximum[0],
                        "partial-stage-file",
                    )
                else:
                    rollback_read.assert_not_called()
                rollback_unlink.assert_not_called()
                self.assertEqual(path.read_bytes(), published_bytes[0])

    def test_helper_only_cleanup_preserves_every_unproven_boundary(self) -> None:
        cases = (
            "digest-drift",
            "executable-path-drift",
            "live-label",
            "uid-list-account",
            "name-list-account",
            "home-present",
            "artifact-present",
            "extra-state",
            "helper-symlink",
            "helper-special",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stage = root / "stage"
                stage.mkdir(mode=0o755)
                helper = stage / "helper.py"
                trusted = b"trusted staged helper\n"
                helper.write_bytes(trusted)
                helper.chmod(0o555)
                if case == "extra-state":
                    (stage / "state.json").write_bytes(b"foreign state")
                elif case == "helper-symlink":
                    source = root / "source"
                    source.write_bytes(trusted)
                    helper.unlink()
                    helper.symlink_to(source)
                elif case == "helper-special":
                    helper.unlink()
                    os.mkfifo(helper, mode=0o555)
                artifact = root / "artifact"
                if case == "artifact-present":
                    artifact.mkdir()
                    (artifact / "sentinel").write_bytes(b"preserve")
                context = eligible_context()
                account_name, label = self.helper._launchd_identity(context)
                home = Path("/Users") / account_name
                expected_sha256 = hashlib.sha256(trusted).hexdigest()
                if case == "digest-drift":
                    expected_sha256 = "0" * 64

                def metadata(
                    path: Path,
                    *,
                    selected_case: str = case,
                    selected_helper: Path = helper,
                    selected_stage: Path = stage,
                    **_kwargs: object,
                ) -> bool:
                    if selected_case == "helper-special" and path == selected_helper:
                        return False
                    return not (
                        selected_case == "executable-path-drift"
                        and path == selected_stage
                    )

                def path_exists(
                    path: Path,
                    *,
                    selected_case: str = case,
                    expected_home: Path = home,
                    expected_artifact: Path = artifact,
                ) -> bool:
                    if path == expected_home:
                        return selected_case == "home-present"
                    if path == expected_artifact:
                        return selected_case == "artifact-present"
                    return path.exists()

                executable = helper
                if case == "executable-path-drift":
                    executable = root / "different-helper.py"
                with (
                    mock.patch.object(self.helper, "__file__", str(executable)),
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        side_effect=metadata,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_launchd_job_snapshot",
                        return_value="foreign job" if case == "live-label" else None,
                    ) as observe_job,
                    mock.patch.object(
                        self.helper,
                        "_list_accounts",
                        return_value=(
                            {account_name: 502}
                            if case == "uid-list-account"
                            else {"root": 0, "runner": 501}
                        ),
                    ) as list_accounts,
                    mock.patch.object(
                        self.helper,
                        "_account_exists",
                        return_value=case == "name-list-account",
                    ) as account_exists,
                    mock.patch.object(
                        self.helper,
                        "_path_exists_no_follow",
                        side_effect=path_exists,
                    ),
                ):
                    if case == "extra-state":
                        self.assertFalse(
                            self.helper._cleanup_helper_only_stage_before_state(
                                stage_root=stage,
                                artifact_root=artifact,
                                expected_helper_sha256=expected_sha256,
                                environment=context,
                            )
                        )
                    else:
                        with self.assertRaises(self.helper.ProbeError):
                            self.helper._cleanup_helper_only_stage_before_state(
                                stage_root=stage,
                                artifact_root=artifact,
                                expected_helper_sha256=expected_sha256,
                                environment=context,
                            )

                self.assertTrue(stage.exists())
                self.assertEqual(
                    {entry.name for entry in stage.iterdir()},
                    {"helper.py", "state.json"}
                    if case == "extra-state"
                    else {"helper.py"},
                )
                if case == "artifact-present":
                    self.assertEqual(
                        (artifact / "sentinel").read_bytes(),
                        b"preserve",
                    )
                if case in {
                    "digest-drift",
                    "executable-path-drift",
                    "extra-state",
                    "helper-symlink",
                    "helper-special",
                }:
                    observe_job.assert_not_called()
                    list_accounts.assert_not_called()
                    account_exists.assert_not_called()
                else:
                    observe_job.assert_called_once_with(label)

    def test_cleanup_removes_exact_stage_before_artifact_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            for name in ("helper.py", "job.plist", "state.json"):
                (stage / name).write_bytes(name.encode())
            artifact = root / "missing-artifact"
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=502,
                gid=os.getegid(),
                home=root / "absent-home",
            )
            plan = self.helper.LaunchdPlan(
                account=account,
                label="io.nisavid.task-witness.macos-probe.0123456789ab",
                stage_root=stage,
                helper=stage / "helper.py",
                plist=stage / "job.plist",
            )
            events: list[str] = []

            def load_state(*_args: object, **_kwargs: object) -> tuple[object, dict]:
                events.append("load-state")
                return plan, {}

            def validate_stage(
                *_args: object, **_kwargs: object
            ) -> self.helper.ValidatedStageBindings:
                events.append("validate-stage")
                return self.helper.ValidatedStageBindings(None, None)

            def snapshot(_label: str) -> None:
                events.append("observe-absent")

            with (
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(self.helper, "_normalized_context"),
                mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                mock.patch.object(
                    self.helper,
                    "_launchd_identity",
                    return_value=(account.name, plan.label),
                ),
                mock.patch.object(
                    self.helper,
                    "_load_lifecycle_state",
                    side_effect=load_state,
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    side_effect=validate_stage,
                ),
                mock.patch.object(
                    self.helper,
                    "_launchd_job_snapshot",
                    side_effect=snapshot,
                ),
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                ) as launchctl_mutation,
                mock.patch.object(self.helper, "_list_accounts", return_value={}),
                mock.patch.object(self.helper, "_account_exists", return_value=False),
                mock.patch.object(
                    self.helper,
                    "_wait_for_no_uid_processes",
                ) as wait_for_processes,
                mock.patch.object(
                    self.helper,
                    "_validate_precleanup_artifact",
                    side_effect=self.helper.ProbeError(
                        "launchd-artifact-root-unreadable"
                    ),
                ),
            ):
                status = self.helper.cleanup_launchd_user_lifecycle(
                    stage_root=stage,
                    artifact_root=artifact,
                    expected_helper_sha256="1" * 64,
                    runner_uid=os.geteuid(),
                    runner_gid=os.getegid(),
                )
            self.assertEqual(status, 2)
            self.assertFalse(stage.exists())
            self.assertEqual(
                events[:3],
                ["load-state", "validate-stage", "observe-absent"],
            )
            wait_for_processes.assert_not_called()
            launchctl_mutation.assert_not_called()

    def test_cleanup_preserves_account_or_home_without_account_binding(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/task-witness-stage"))
        state = self.lifecycle_state(plan)
        for account_present, home_present in ((True, False), (False, True)):
            with (
                self.subTest(
                    account_present=account_present,
                    home_present=home_present,
                ),
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(self.helper, "_normalized_context"),
                mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                mock.patch.object(
                    self.helper,
                    "_cleanup_helper_only_stage_before_state",
                    return_value=False,
                ),
                mock.patch.object(
                    self.helper,
                    "_load_lifecycle_state",
                    return_value=(plan, state),
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    return_value=self.helper.ValidatedStageBindings(None, None),
                ),
                mock.patch.object(self.helper, "_reconcile_owned_launchd_job"),
                mock.patch.object(
                    self.helper,
                    "_account_exists",
                    return_value=account_present,
                ),
                mock.patch.object(
                    self.helper,
                    "_path_exists_no_follow",
                    return_value=home_present,
                ),
                mock.patch.object(self.helper, "_list_accounts") as list_accounts,
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                ) as mutate_account,
                mock.patch.object(
                    self.helper,
                    "remove_exact_disposable_home",
                ) as remove_home,
            ):
                self.assertEqual(
                    self.helper.cleanup_launchd_user_lifecycle(
                        stage_root=plan.stage_root,
                        artifact_root=Path("/private/tmp/task-witness-artifact"),
                        expected_helper_sha256="1" * 64,
                        runner_uid=501,
                        runner_gid=20,
                    ),
                    2,
                )
            list_accounts.assert_not_called()
            mutate_account.assert_not_called()
            remove_home.assert_not_called()

    def test_cleanup_preserves_replaced_account_with_different_guid(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/task-witness-stage"))
        state = self.lifecycle_state(plan)
        expected = "01234567-89AB-4DEF-8123-456789ABCDEF"
        different = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"
        binding = self.helper._account_binding_document(plan, state, expected)
        with (
            mock.patch.dict(os.environ, eligible_context(), clear=True),
            mock.patch.object(self.helper, "_normalized_context"),
            mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
            mock.patch.object(
                self.helper,
                "_cleanup_helper_only_stage_before_state",
                return_value=False,
            ),
            mock.patch.object(
                self.helper,
                "_load_lifecycle_state",
                return_value=(plan, state),
            ),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=self.helper.ValidatedStageBindings(binding, None),
            ),
            mock.patch.object(self.helper, "_reconcile_owned_launchd_job"),
            mock.patch.object(self.helper, "_account_exists", return_value=True),
            mock.patch.object(
                self.helper,
                "_path_exists_no_follow",
                return_value=False,
            ),
            mock.patch.object(
                self.helper,
                "_read_system_generated_uid",
                return_value=different,
            ),
            mock.patch.object(
                self.helper,
                "_require_command_success",
            ) as mutate_account,
            mock.patch.object(
                self.helper,
                "remove_exact_disposable_home",
            ) as remove_home,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(
                self.helper.cleanup_launchd_user_lifecycle(
                    stage_root=plan.stage_root,
                    artifact_root=Path("/private/tmp/task-witness-artifact"),
                    expected_helper_sha256="1" * 64,
                    runner_uid=501,
                    runner_gid=20,
                ),
                2,
            )
        mutate_account.assert_not_called()
        remove_home.assert_not_called()
        self.assertEqual(
            stderr.getvalue(),
            "task-witness macOS launchd-user cleanup: "
            "account-record-generated-uid-drift\n",
        )

    def test_cleanup_deletes_guid_bound_partial_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            for name in ("helper.py", "job.plist", "state.json", "account.json"):
                (stage / name).write_bytes(f"exact-{name}".encode())
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
            binding = self.helper._account_binding_document(
                plan,
                state,
                generated_uid,
            )
            delete_call = [
                "/usr/bin/dscl",
                ".",
                "-delete",
                f"/Users/{plan.account.name}",
            ]
            with (
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(self.helper, "_normalized_context"),
                mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                mock.patch.object(
                    self.helper,
                    "_cleanup_helper_only_stage_before_state",
                    return_value=False,
                ),
                mock.patch.object(
                    self.helper,
                    "_load_lifecycle_state",
                    return_value=(plan, state),
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    return_value=self.helper.ValidatedStageBindings(binding, None),
                ),
                mock.patch.object(self.helper, "_reconcile_owned_launchd_job"),
                mock.patch.object(
                    self.helper,
                    "_account_exists",
                    side_effect=[True, False],
                ),
                mock.patch.object(
                    self.helper,
                    "_path_exists_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    self.helper,
                    "_read_system_generated_uid",
                    return_value=generated_uid,
                ),
                mock.patch.object(
                    self.helper,
                    "_require_no_uid_processes",
                ) as process_scan,
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                    return_value="",
                ) as command,
                mock.patch.object(self.helper, "_list_accounts", return_value={}),
                mock.patch.object(self.helper, "_account_record") as full_record,
                mock.patch.object(self.helper, "_validate_precleanup_artifact"),
                mock.patch.object(self.helper, "_write_root_file"),
                mock.patch.object(self.helper, "launchd_artifact_payloads"),
                mock.patch.object(self.helper.os, "chown"),
            ):
                self.assertEqual(
                    self.helper.cleanup_launchd_user_lifecycle(
                        stage_root=stage,
                        artifact_root=Path(directory) / "artifact",
                        expected_helper_sha256="1" * 64,
                        runner_uid=501,
                        runner_gid=20,
                    ),
                    0,
                )
            command.assert_called_once_with(
                delete_call,
                command_id="account-delete",
            )
            self.assertEqual(process_scan.call_count, 5)
            self.assertEqual(
                process_scan.call_args_list[-2:],
                [mock.call(plan.account.uid), mock.call(plan.account.uid)],
            )
            full_record.assert_not_called()
            self.assertFalse(stage.exists())

    def test_cleanup_process_wait_failure_preserves_all_owned_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            for name in ("helper.py", "job.plist", "state.json", "account.json"):
                (stage / name).write_bytes(f"exact-{name}".encode())
            artifact = root / "artifact"
            artifact.mkdir()
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
            binding = self.helper._account_binding_document(
                plan,
                state,
                generated_uid,
            )
            stage_before = {entry.name: entry.read_bytes() for entry in stage.iterdir()}
            stderr = io.StringIO()

            def path_exists(path: Path) -> bool:
                if path == plan.account.home:
                    return True
                if path == artifact / "cleanup.json":
                    return False
                raise AssertionError(path)

            with (
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(self.helper, "_normalized_context"),
                mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                mock.patch.object(
                    self.helper,
                    "_cleanup_helper_only_stage_before_state",
                    return_value=False,
                ),
                mock.patch.object(
                    self.helper,
                    "_load_lifecycle_state",
                    return_value=(plan, state),
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    return_value=self.helper.ValidatedStageBindings(binding, None),
                ),
                mock.patch.object(self.helper, "_reconcile_owned_launchd_job"),
                mock.patch.object(self.helper, "_account_exists", return_value=True),
                mock.patch.object(
                    self.helper,
                    "_path_exists_no_follow",
                    side_effect=path_exists,
                ),
                mock.patch.object(
                    self.helper,
                    "_read_system_generated_uid",
                    return_value=generated_uid,
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_disposable_home",
                ) as validate_home,
                mock.patch.object(
                    self.helper,
                    "_wait_for_no_uid_processes",
                    side_effect=self.helper.ProbeError(
                        "disposable-user-pid1-parented-processes-remain",
                    ),
                ) as wait,
                mock.patch.multiple(
                    self.helper,
                    _require_command_success=mock.DEFAULT,
                    _list_accounts=mock.DEFAULT,
                    remove_exact_disposable_home=mock.DEFAULT,
                    launchd_artifact_payloads=mock.DEFAULT,
                ) as mutations,
                mock.patch.object(
                    self.helper,
                    "_directory_matches",
                    return_value=True,
                ) as artifact_matches,
                mock.patch.object(self.helper, "_write_root_file") as write_cleanup,
                mock.patch.object(self.helper.os, "chown") as transfer_artifact,
                redirect_stderr(stderr),
            ):
                self.assertEqual(
                    self.helper.cleanup_launchd_user_lifecycle(
                        stage_root=stage,
                        artifact_root=artifact,
                        expected_helper_sha256="1" * 64,
                        runner_uid=501,
                        runner_gid=20,
                    ),
                    2,
                )

            wait.assert_called_once_with(plan.account.uid)
            validate_home.assert_called_once_with(
                plan.account.home,
                expected_uid=plan.account.uid,
                expected_gid=plan.account.gid,
                diagnostic_phase="cleanup-entry",
            )
            mutations["_require_command_success"].assert_not_called()
            mutations["_list_accounts"].assert_not_called()
            mutations["remove_exact_disposable_home"].assert_not_called()
            mutations["launchd_artifact_payloads"].assert_not_called()
            transfer_artifact.assert_not_called()
            artifact_matches.assert_called_once_with(artifact, 0o700, 0, 0)
            self.assertEqual(
                {entry.name: entry.read_bytes() for entry in stage.iterdir()},
                stage_before,
            )
            self.assertEqual(list(artifact.iterdir()), [])
            write_cleanup.assert_called_once()
            cleanup_path, cleanup_raw, cleanup_mode = write_cleanup.call_args.args
            self.assertEqual(cleanup_path, artifact / "cleanup.json")
            self.assertEqual(cleanup_mode, 0o600)
            cleanup = json.loads(cleanup_raw)
            self.assertEqual(cleanup["disposition"], "preserved-on-drift")
            self.assertEqual(
                cleanup["error"],
                {"code": "disposable-user-pid1-parented-processes-remain"},
            )
            self.assertEqual(
                stderr.getvalue(),
                "task-witness macOS launchd-user cleanup: "
                "disposable-user-pid1-parented-processes-remain\n",
            )

    def test_cleanup_failure_record_requires_safe_root_and_absent_record(self) -> None:
        for label, root_matches, cleanup_exists in (
            ("unsafe-root", False, False),
            ("preexisting-cleanup", True, True),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stage = root / "stage"
                artifact = root / "artifact"
                stage.mkdir()
                artifact.mkdir()
                preserved = artifact / (
                    "cleanup.json" if cleanup_exists else "private-canary"
                )
                preserved.write_bytes(b"preserve")
                before = {path.name: path.read_bytes() for path in artifact.iterdir()}
                stderr = io.StringIO()

                with (
                    mock.patch.dict(os.environ, eligible_context(), clear=True),
                    mock.patch.object(self.helper, "_normalized_context"),
                    mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                    mock.patch.object(
                        self.helper,
                        "_cleanup_helper_only_stage_before_state",
                        return_value=False,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_load_lifecycle_state",
                        side_effect=self.helper.ProbeError("synthetic-cleanup-failure"),
                    ),
                    mock.patch.object(
                        self.helper,
                        "_directory_matches",
                        return_value=root_matches,
                    ) as artifact_matches,
                    mock.patch.object(
                        self.helper,
                        "_path_exists_no_follow",
                        return_value=cleanup_exists,
                    ) as cleanup_probe,
                    mock.patch.object(self.helper, "_write_root_file") as write,
                    mock.patch.object(self.helper.os, "chown") as chown,
                    mock.patch.object(Path, "unlink") as path_unlink,
                    mock.patch.object(self.helper.os, "unlink") as unlink,
                    mock.patch.object(self.helper.os, "rmdir") as rmdir,
                    mock.patch.object(self.helper.os, "rename") as rename,
                    redirect_stderr(stderr),
                ):
                    self.assertEqual(
                        self.helper.cleanup_launchd_user_lifecycle(
                            stage_root=stage,
                            artifact_root=artifact,
                            expected_helper_sha256="1" * 64,
                            runner_uid=501,
                            runner_gid=20,
                        ),
                        2,
                    )

                artifact_matches.assert_called_once_with(artifact, 0o700, 0, 0)
                if root_matches:
                    cleanup_probe.assert_called_once_with(artifact / "cleanup.json")
                else:
                    cleanup_probe.assert_not_called()
                write.assert_not_called()
                chown.assert_not_called()
                path_unlink.assert_not_called()
                unlink.assert_not_called()
                rmdir.assert_not_called()
                rename.assert_not_called()
                self.assertEqual(
                    {path.name: path.read_bytes() for path in artifact.iterdir()},
                    before,
                )
                self.assertEqual(
                    stderr.getvalue(),
                    "task-witness macOS launchd-user cleanup: "
                    "synthetic-cleanup-failure\n",
                )

    def test_cleanup_revalidates_uid_immediately_before_account_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            for name in (
                "helper.py",
                "job.plist",
                "state.json",
                "account.json",
                "ownership.json",
            ):
                (stage / name).write_bytes(f"exact-{name}".encode())
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
            binding = self.helper._account_binding_document(
                plan,
                state,
                generated_uid,
            )
            ownership = self.helper._launchd_ownership_document(plan, state)
            with (
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(self.helper, "_normalized_context"),
                mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                mock.patch.object(
                    self.helper,
                    "_cleanup_helper_only_stage_before_state",
                    return_value=False,
                ),
                mock.patch.object(
                    self.helper,
                    "_load_lifecycle_state",
                    return_value=(plan, state),
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    return_value=self.helper.ValidatedStageBindings(
                        binding,
                        ownership,
                    ),
                ),
                mock.patch.object(self.helper, "_reconcile_owned_launchd_job"),
                mock.patch.object(
                    self.helper,
                    "_account_exists",
                    side_effect=[True, False],
                ),
                mock.patch.object(
                    self.helper,
                    "_path_exists_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    self.helper,
                    "_read_system_generated_uid",
                    return_value=generated_uid,
                ),
                mock.patch.object(self.helper, "_require_no_uid_processes"),
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                    return_value="",
                ) as delete,
                mock.patch.object(
                    self.helper,
                    "_list_accounts",
                    return_value={},
                ),
            ):
                self.assertEqual(
                    self.helper.cleanup_launchd_user_lifecycle(
                        stage_root=stage,
                        artifact_root=Path(directory) / "artifact",
                        expected_helper_sha256="1" * 64,
                        runner_uid=501,
                        runner_gid=20,
                    ),
                    2,
                )
            delete.assert_not_called()
            self.assertTrue(stage.is_dir())

    def test_cleanup_revalidates_guid_immediately_before_account_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            for name in ("helper.py", "job.plist", "state.json", "account.json"):
                (stage / name).write_bytes(f"exact-{name}".encode())
            artifact = root / "artifact"
            artifact.mkdir()
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
            replacement_uid = "FEDCBA98-7654-4ABC-8DEF-0123456789AB"
            binding = self.helper._account_binding_document(
                plan,
                state,
                generated_uid,
            )
            with (
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(self.helper, "_normalized_context"),
                mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                mock.patch.object(
                    self.helper,
                    "_cleanup_helper_only_stage_before_state",
                    return_value=False,
                ),
                mock.patch.object(
                    self.helper,
                    "_load_lifecycle_state",
                    return_value=(plan, state),
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    return_value=self.helper.ValidatedStageBindings(binding, None),
                ),
                mock.patch.object(self.helper, "_reconcile_owned_launchd_job"),
                mock.patch.object(self.helper, "_account_exists", return_value=True),
                mock.patch.object(
                    self.helper,
                    "_path_exists_no_follow",
                    return_value=False,
                ),
                mock.patch.object(
                    self.helper,
                    "_read_system_generated_uid",
                    side_effect=[generated_uid, replacement_uid],
                ) as read_guid,
                mock.patch.object(self.helper, "_wait_for_no_uid_processes"),
                mock.patch.object(self.helper, "_require_no_uid_processes") as scan,
                mock.patch.object(
                    self.helper,
                    "_list_accounts",
                    return_value={plan.account.name: plan.account.uid},
                ),
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                ) as delete,
                mock.patch.object(self.helper, "_metadata_matches", return_value=True),
                mock.patch.object(self.helper, "_write_root_file"),
            ):
                self.assertEqual(
                    self.helper.cleanup_launchd_user_lifecycle(
                        stage_root=stage,
                        artifact_root=artifact,
                        expected_helper_sha256="1" * 64,
                        runner_uid=501,
                        runner_gid=20,
                    ),
                    2,
                )
            scan.assert_called_once_with(plan.account.uid)
            self.assertEqual(read_guid.call_count, 2)
            delete.assert_not_called()
            self.assertTrue(stage.is_dir())

    def test_cleanup_revalidates_full_record_before_marker_bound_delete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            for name in (
                "helper.py",
                "job.plist",
                "state.json",
                "account.json",
                "ownership.json",
                "domain-reset.json",
            ):
                (stage / name).write_bytes(f"exact-{name}".encode())
            artifact = root / "artifact"
            artifact.mkdir()
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
            account = self.helper._account_binding_document(
                plan,
                state,
                generated_uid,
            )
            ownership = self.helper._launchd_ownership_document(plan, state)
            marker = {
                "content_sha256": "4" * 64,
                "home_identity": {
                    "home_device": 1,
                    "home_inode": 2,
                    "probe_device": 1,
                    "probe_inode": 3,
                },
            }
            bindings = self.helper.ValidatedStageBindings(
                account,
                ownership,
                marker,
            )
            with ExitStack() as stack:
                stack.enter_context(
                    mock.patch.dict(os.environ, eligible_context(), clear=True)
                )
                for name in (
                    "_normalized_context",
                    "_validate_lifecycle_arguments",
                    "_reconcile_owned_launchd_job",
                    "_wait_for_no_uid_processes",
                    "_require_stable_no_uid_processes",
                    "_require_no_uid_processes",
                    "_validate_precleanup_artifact",
                    "_write_root_file",
                    "launchd_artifact_payloads",
                ):
                    stack.enter_context(mock.patch.object(self.helper, name))
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_cleanup_helper_only_stage_before_state",
                        return_value=False,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_load_lifecycle_state",
                        return_value=(plan, state),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_validate_exact_stage",
                        return_value=bindings,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_require_durable_user_domain_reset_authorization",
                        return_value=marker,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_account_exists",
                        side_effect=[True, False],
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_path_exists_no_follow",
                        return_value=False,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_read_system_generated_uid",
                        return_value=generated_uid,
                    )
                )
                validate = stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_validate_reset_bindings_and_resources",
                        side_effect=[
                            (True, False),
                            (True, False),
                            (True, False),
                            self.helper.ProbeError("account-record-home-drift"),
                        ],
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_cleanup_domain_reset_evidence",
                        return_value=self.helper._recovered_domain_reset_evidence(
                            marker
                        ),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_list_accounts",
                        side_effect=[
                            {plan.account.name: plan.account.uid},
                            {},
                        ],
                    )
                )
                delete = stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_require_command_success",
                        return_value="",
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        return_value=False,
                    )
                )
                stack.enter_context(mock.patch.object(self.helper.os, "chown"))
                self.assertEqual(
                    self.helper.cleanup_launchd_user_lifecycle(
                        stage_root=stage,
                        artifact_root=artifact,
                        expected_helper_sha256="1" * 64,
                        runner_uid=501,
                        runner_gid=20,
                        user_domain_reset_authorization=(
                            eligible_context()["GITHUB_SHA"]
                        ),
                    ),
                    2,
                )
            self.assertEqual(validate.call_count, 4)
            delete.assert_not_called()
            self.assertTrue(stage.is_dir())

    def test_cleanup_revalidates_home_identity_immediately_before_removal(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            for name in (
                "helper.py",
                "job.plist",
                "state.json",
                "account.json",
                "ownership.json",
                "domain-reset.json",
            ):
                (stage / name).write_bytes(f"exact-{name}".encode())
            artifact = root / "artifact"
            artifact.mkdir()
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
            account = self.helper._account_binding_document(
                plan,
                state,
                generated_uid,
            )
            ownership = self.helper._launchd_ownership_document(plan, state)
            home_identity = {
                "home_device": 1,
                "home_inode": 2,
                "probe_device": 1,
                "probe_inode": 3,
            }
            marker = {
                "content_sha256": "4" * 64,
                "home_identity": home_identity,
            }
            journal = {
                "content_sha256": "5" * 64,
                "disposition": "armed",
                "account": {
                    "name": plan.account.name,
                    "uid": plan.account.uid,
                },
                "home_identity": home_identity,
            }
            bindings = self.helper.ValidatedStageBindings(
                account,
                ownership,
                marker,
            )

            def path_exists(path: Path) -> bool:
                if path == plan.account.home:
                    return True
                if path == artifact / "cleanup.json":
                    return False
                raise AssertionError(path)

            with ExitStack() as stack:
                stack.enter_context(
                    mock.patch.dict(os.environ, eligible_context(), clear=True)
                )
                for name in (
                    "_normalized_context",
                    "_validate_lifecycle_arguments",
                    "_reconcile_owned_launchd_job",
                    "_validate_exact_disposable_home",
                    "_wait_for_no_uid_processes",
                    "_require_stable_no_uid_processes",
                    "_write_root_file",
                ):
                    stack.enter_context(mock.patch.object(self.helper, name))
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_cleanup_helper_only_stage_before_state",
                        return_value=False,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_load_lifecycle_state",
                        return_value=(plan, state),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_validate_exact_stage",
                        return_value=bindings,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_require_durable_user_domain_reset_authorization",
                        return_value=marker,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_account_exists",
                        return_value=False,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_path_exists_no_follow",
                        side_effect=path_exists,
                    )
                )
                validate = stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_validate_reset_bindings_and_resources",
                        return_value=(False, True),
                    )
                )
                scan = stack.enter_context(
                    mock.patch.object(self.helper, "_require_no_uid_processes")
                )
                stack.enter_context(
                    mock.patch.object(self.helper, "_list_accounts", return_value={})
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_cleanup_domain_reset_evidence",
                        return_value=self.helper._recovered_domain_reset_evidence(
                            marker
                        ),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_validated_marker_bound_disposable_home",
                        side_effect=[
                            (
                                True,
                                True,
                                tuple(sorted(self.helper.LAUNCHD_CHILD_FILES)),
                            ),
                            self.helper.ProbeError("home-cleanup-drift"),
                        ],
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_write_home_cleanup_authorization",
                        return_value=journal,
                    )
                )
                remove_home = stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_remove_marker_bound_disposable_home",
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        return_value=True,
                    )
                )
                self.assertEqual(
                    self.helper.cleanup_launchd_user_lifecycle(
                        stage_root=stage,
                        artifact_root=artifact,
                        expected_helper_sha256="1" * 64,
                        runner_uid=501,
                        runner_gid=20,
                        user_domain_reset_authorization=(
                            eligible_context()["GITHUB_SHA"]
                        ),
                    ),
                    2,
                )
            self.assertEqual(validate.call_count, 3)
            scan.assert_called_once_with(plan.account.uid)
            remove_home.assert_not_called()
            self.assertTrue(stage.is_dir())

    def test_cleanup_preserves_foreign_live_job_and_all_local_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            for name in (
                "helper.py",
                "job.plist",
                "state.json",
                "account.json",
                "ownership.json",
            ):
                (stage / name).write_bytes(f"exact-{name}".encode())
            artifact = root / "artifact"
            artifact.mkdir()
            (artifact / "sentinel").write_bytes(b"preserve-artifact")
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            account_binding = self.helper._account_binding_document(
                plan,
                state,
                "01234567-89AB-4DEF-8123-456789ABCDEF",
            )
            marker = self.helper._launchd_ownership_document(plan, state)
            foreign = self.launchctl_job(plan, state).replace(
                str(plan.plist),
                "/Library/LaunchDaemons/foreign.plist",
            )
            stage_before = {entry.name: entry.read_bytes() for entry in stage.iterdir()}
            events: list[str] = []

            def load_state(*_args: object, **_kwargs: object) -> tuple[object, dict]:
                events.append("load-state")
                return plan, state

            def validate_stage(
                *_args: object, **_kwargs: object
            ) -> self.helper.ValidatedStageBindings:
                events.append("validate-stage")
                return self.helper.ValidatedStageBindings(account_binding, marker)

            def snapshot(_label: str) -> str:
                events.append("observe-live")
                return foreign

            with (
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(self.helper, "_normalized_context"),
                mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                mock.patch.object(
                    self.helper,
                    "_load_lifecycle_state",
                    side_effect=load_state,
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    side_effect=validate_stage,
                ),
                mock.patch.object(
                    self.helper,
                    "_launchd_job_snapshot",
                    side_effect=snapshot,
                ),
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                ) as launchctl_mutation,
                mock.patch.object(self.helper, "_list_accounts") as list_accounts,
                mock.patch.object(
                    self.helper,
                    "remove_exact_disposable_home",
                ) as remove_home,
            ):
                status = self.helper.cleanup_launchd_user_lifecycle(
                    stage_root=stage,
                    artifact_root=artifact,
                    expected_helper_sha256="1" * 64,
                    runner_uid=501,
                    runner_gid=20,
                )

            self.assertEqual(status, 2)
            self.assertEqual(events, ["load-state", "validate-stage", "observe-live"])
            launchctl_mutation.assert_not_called()
            list_accounts.assert_not_called()
            remove_home.assert_not_called()
            self.assertEqual(
                {entry.name: entry.read_bytes() for entry in stage.iterdir()},
                stage_before,
            )
            self.assertEqual(
                {entry.name: entry.read_bytes() for entry in artifact.iterdir()},
                {"sentinel": b"preserve-artifact"},
            )

    def test_cleanup_boots_out_exact_owned_interrupted_job_then_cleans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            for name in (
                "helper.py",
                "job.plist",
                "state.json",
                "account.json",
                "ownership.json",
            ):
                (stage / name).write_bytes(f"exact-{name}".encode())
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            account_binding = self.helper._account_binding_document(
                plan,
                state,
                "01234567-89AB-4DEF-8123-456789ABCDEF",
            )
            marker = self.helper._launchd_ownership_document(plan, state)
            owned = self.launchctl_job(plan, state)
            artifact = root / "artifact"
            events: list[str] = []

            def load_state(*_args: object, **_kwargs: object) -> tuple[object, dict]:
                events.append("load-state")
                return plan, state

            def validate_stage(
                *_args: object, **_kwargs: object
            ) -> self.helper.ValidatedStageBindings:
                events.append("validate-stage")
                return self.helper.ValidatedStageBindings(account_binding, marker)

            def snapshot(_label: str) -> str | None:
                events.append("observe-job")
                return owned if events.count("observe-job") == 1 else None

            with (
                mock.patch.dict(os.environ, eligible_context(), clear=True),
                mock.patch.object(self.helper, "_normalized_context"),
                mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
                mock.patch.object(
                    self.helper,
                    "_load_lifecycle_state",
                    side_effect=load_state,
                ),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    side_effect=validate_stage,
                ),
                mock.patch.object(
                    self.helper,
                    "_launchd_job_snapshot",
                    side_effect=snapshot,
                ),
                mock.patch.object(
                    self.helper,
                    "_require_command_success",
                    return_value="",
                ) as command,
                mock.patch.object(self.helper, "_list_accounts", return_value={}),
                mock.patch.object(self.helper, "_account_exists", return_value=False),
                mock.patch.object(
                    self.helper,
                    "_path_exists_no_follow",
                    return_value=False,
                ),
                mock.patch.object(self.helper, "_require_no_uid_processes"),
                mock.patch.object(self.helper, "_validate_precleanup_artifact"),
                mock.patch.object(self.helper, "_write_root_file"),
                mock.patch.object(self.helper, "launchd_artifact_payloads"),
                mock.patch.object(self.helper.os, "chown"),
            ):
                status = self.helper.cleanup_launchd_user_lifecycle(
                    stage_root=stage,
                    artifact_root=artifact,
                    expected_helper_sha256="1" * 64,
                    runner_uid=501,
                    runner_gid=20,
                )

            self.assertEqual(status, 0)
            self.assertFalse(stage.exists())
            self.assertEqual(
                events,
                ["load-state", "validate-stage", "observe-job", "observe-job"],
            )
            command.assert_called_once_with(
                ["/bin/launchctl", "bootout", f"system/{plan.label}"],
                command_id="launchd-bootout",
            )

    def test_cleanup_failure_logs_only_the_bounded_command_id(self) -> None:
        stage = Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        artifact = Path("/private/tmp/task-witness-macos-launchd-user-probe")
        plan = self.lifecycle_plan(stage)
        state = self.lifecycle_state(plan)
        code = "lifecycle-command-nonzero-launchd-bootout"
        stderr = io.StringIO()

        with (
            mock.patch.dict(os.environ, eligible_context(), clear=True),
            mock.patch.object(self.helper, "_normalized_context"),
            mock.patch.object(self.helper, "_validate_lifecycle_arguments"),
            mock.patch.object(
                self.helper,
                "_cleanup_helper_only_stage_before_state",
                return_value=False,
            ),
            mock.patch.object(
                self.helper,
                "_load_lifecycle_state",
                return_value=(plan, state),
            ),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=self.helper.ValidatedStageBindings(
                    {
                        "account": {
                            "generated_uid": "01234567-89AB-4DEF-8123-456789ABCDEF"
                        }
                    },
                    {"ownership": "exact"},
                ),
            ),
            mock.patch.object(
                self.helper,
                "_reconcile_owned_launchd_job",
                side_effect=self.helper.ProbeError(code),
            ),
            mock.patch.object(self.helper, "_metadata_matches", return_value=False),
            redirect_stderr(stderr),
        ):
            status = self.helper.cleanup_launchd_user_lifecycle(
                stage_root=stage,
                artifact_root=artifact,
                expected_helper_sha256="1" * 64,
                runner_uid=501,
                runner_gid=20,
            )

        self.assertEqual(status, 2)
        self.assertEqual(
            stderr.getvalue(),
            f"task-witness macOS launchd-user cleanup: {code}\n",
        )

    def test_cli_exposes_only_explicit_launchd_lifecycle_operations(self) -> None:
        choices = self.helper.parser()._subparsers._group_actions[0].choices
        self.assertEqual(
            set(choices),
            {
                "probe",
                "write-artifact-manifest",
                "verify-artifact-manifest",
                "probe-launchd-user",
                "verify-provisioner",
                "initialize-launchd-user-lifecycle",
                "run-launchd-user-lifecycle",
                "cleanup-launchd-user-lifecycle",
                "write-launchd-artifact-manifest",
                "verify-launchd-artifact-manifest",
                "verify-launchd-success",
            },
        )

    def test_prestaged_helper_is_source_independent_and_rejects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            source = root / "source.py"
            source.write_bytes(b"trusted helper\n")
            helper = stage / "helper.py"
            helper.write_bytes(source.read_bytes())
            helper.chmod(0o555)
            expected = hashlib.sha256(helper.read_bytes()).hexdigest()
            source.write_bytes(b"post-check replacement\n")

            with (
                mock.patch.object(
                    self.helper,
                    "_directory_matches",
                    return_value=True,
                ) as directory_matches,
                mock.patch.object(
                    self.helper,
                    "_root_file_matches",
                    return_value=True,
                ) as root_file_matches,
            ):
                self.assertEqual(
                    self.helper.validate_prestaged_helper(stage, expected),
                    b"trusted helper\n",
                )
            directory_matches.assert_called_once_with(stage, 0o755, 0, 0)
            root_file_matches.assert_called_once_with(helper, 0o555)

            for label, stage_matches, helper_matches in (
                ("unsafe-stage", False, True),
                ("unsafe-helper", True, False),
            ):
                with (
                    self.subTest(label=label),
                    mock.patch.object(
                        self.helper,
                        "_directory_matches",
                        return_value=stage_matches,
                    ) as directory_matches,
                    mock.patch.object(
                        self.helper,
                        "_root_file_matches",
                        return_value=helper_matches,
                    ) as root_file_matches,
                    mock.patch.object(
                        self.helper,
                        "_read_stable_regular_file",
                    ) as read_helper,
                    self.assertRaises(self.helper.ProbeError) as raised,
                ):
                    self.helper.validate_prestaged_helper(stage, expected)
                self.assertEqual(
                    raised.exception.code,
                    "staged-helper-metadata-drift",
                )
                directory_matches.assert_called_once_with(stage, 0o755, 0, 0)
                if stage_matches:
                    root_file_matches.assert_called_once_with(helper, 0o555)
                else:
                    root_file_matches.assert_not_called()
                read_helper.assert_not_called()
                self.assertEqual(helper.read_bytes(), b"trusted helper\n")

            with (
                mock.patch.object(
                    self.helper,
                    "_metadata_matches",
                    return_value=True,
                ),
                self.assertRaisesRegex(self.helper.ProbeError, "digest"),
            ):
                self.helper.validate_prestaged_helper(stage, "0" * 64)

            hardlink = root / "hardlink.py"
            os.link(helper, hardlink)
            with (
                mock.patch.object(
                    self.helper,
                    "_metadata_matches",
                    return_value=True,
                ),
                self.assertRaisesRegex(self.helper.ProbeError, "unsafe-staged-helper"),
            ):
                self.helper.validate_prestaged_helper(stage, expected)
            hardlink.unlink()

            helper.unlink()
            helper.symlink_to(source)
            with (
                mock.patch.object(
                    self.helper,
                    "_metadata_matches",
                    return_value=True,
                ),
                self.assertRaises(self.helper.ProbeError),
            ):
                self.helper.validate_prestaged_helper(stage, expected)

    def test_workflow_uses_fixed_root_tools_before_staged_python(self) -> None:
        workflow = WORKFLOW.read_text()
        provisioner = workflow_step_script(
            "Require a launchd-capable macOS provisioner"
        )
        nonce_script = workflow_step_script(
            "Generate the launchd staging ownership nonce"
        )
        stage_marker = "      - name: Stage the exact root-owned launchd helper\n"
        capture_marker = "      - name: Capture the disposable launchd-user probe\n"
        cleanup_marker = "      - name: Clean the disposable launchd-user probe\n"
        self.assertEqual(workflow.count(stage_marker), 1)
        stage = workflow[workflow.index(stage_marker) : workflow.index(capture_marker)]
        capture = workflow[
            workflow.index(capture_marker) : workflow.index(cleanup_marker)
        ]
        self.assertNotIn("/usr/bin/install", stage)
        self.assertNotIn("/bin/cp", stage)
        self.assertNotIn("tempfile", stage)
        self.assertIn("os.urandom(32).hex()", nonce_script)
        self.assertIn("'nonce=%s\\n'", nonce_script)
        self.assertIn('>>"$GITHUB_OUTPUT"', nonce_script)
        self.assertEqual(stage.count("/usr/bin/sudo -n /usr/bin/python3 -I -B -c"), 3)
        self.assertNotRegex(stage, r">+\s*[\"']?\$partial_helper")
        self.assertNotRegex(
            stage,
            r"/usr/bin/sudo[^\n]*(?:/bin/sh|/bin/bash)(?:\s|$)",
        )
        for required in (
            '          /usr/bin/sudo -n /bin/test ! -e "$stage_root"\n',
            '          /usr/bin/sudo -n /bin/test ! -L "$source_helper"\n',
            "source_metadata_before=",
            '"Regular File:${runner_uid}:${runner_gid}:755:1"',
            "source_identity_before=",
            "source_digest_before=",
            'ownership_nonce="${{ steps.launchd_stage_nonce.outputs.nonce }}"',
            '/usr/bin/sudo -n /usr/bin/python3 -I -B -c \'exec("""',
            "os.O_EXCL",
            "os.O_NOFOLLOW",
            "os.mkdir(stage_path, 0o755)",
            'ownership_name = f".task-witness-stage-owner-{ownership_nonce}"',
            "os.fchmod(ownership_descriptor, 0o600)",
            "os.fsync(stage_descriptor)",
            "target_descriptor = os.open(",
            '"helper.py.partial",',
            "dir_fd=stage_descriptor",
            "256 * 1024",
            '"Regular File:0:0:444:1"',
            "source_metadata_after=",
            "source_identity_after=",
            "source_digest_after=",
            '          /bin/test "$source_identity_after" = "$source_identity_before"\n',
            "destination_digest=",
            '"$HELPER_SHA256  $partial_helper"',
            '"$HELPER_SHA256  $stage_root/helper.py"',
            '          /usr/bin/sudo -n /bin/chmod 0555 "$partial_helper"\n',
            '          /usr/bin/sudo -n /bin/mv "$partial_helper" "$stage_root/helper.py"\n',
            '"Regular File:0:0:555:1"',
            "/usr/bin/printf 'created=true\\n' >>\"$GITHUB_OUTPUT\"",
            "stage_output_published=true",
            "staged-helper-finalizer-owner-invalid",
            "stage_transaction_complete=false",
            "trap cleanup_unpublished_stage EXIT",
            "stage_transaction_complete=true",
        ):
            self.assertIn(required, stage)
        self.assertLess(
            stage.index("trap cleanup_unpublished_stage EXIT"),
            stage.index("os.mkdir(stage_path, 0o755)"),
        )
        self.assertLess(
            stage.index("os.mkdir(stage_path, 0o755)"),
            stage.index("target_descriptor = os.open("),
        )
        self.assertLess(
            stage.index('ownership_name = f".task-witness-stage-owner-'),
            stage.index("target_descriptor = os.open("),
        )
        self.assertLess(
            stage.index("target_descriptor = os.open("),
            stage.index("/usr/bin/printf 'created=true\\n'"),
        )
        self.assertLess(
            stage.index("/usr/bin/printf 'created=true\\n'"),
            stage.index("source_metadata_after="),
        )
        self.assertLess(
            stage.index('/bin/mv "$partial_helper" "$stage_root/helper.py"'),
            stage.index("staged-helper-finalizer-not-root"),
        )
        helper_sha = re.search(
            r"^      HELPER_SHA256: ([0-9a-f]{64})$",
            workflow,
            re.MULTILINE,
        )
        self.assertIsNotNone(helper_sha)
        self.assertEqual(
            helper_sha.group(1),
            hashlib.sha256(HELPER.read_bytes()).hexdigest(),
        )
        self.assertLessEqual(
            len(HELPER.read_bytes()),
            self.helper.MAX_HELPER_BYTES,
        )
        helper_source = HELPER.read_text(encoding="utf-8")
        self.assertEqual(helper_source.count('f"user/{uid}"'), 1)
        self.assertNotIn('"print", f"user/', helper_source)
        self.assertNotIn('"kill"', helper_source)
        self.assertNotIn('"pkill"', helper_source)
        self.assertNotIn("--source-helper", capture)
        self.assertNotIn(
            '"$GITHUB_WORKSPACE/harness/scripts/probe_task_witness_macos_host.py"',
            capture,
        )
        self.assertEqual(capture.count('"$stage_root/helper.py" \\\n'), 2)
        self.assertIn("              initialize-launchd-user-lifecycle \\\n", capture)
        self.assertIn("runner_gid=$(/usr/bin/id -g)", provisioner)
        self.assertIn('/bin/test "$runner_gid" -gt 0', provisioner)

    def test_workflow_preserves_preexisting_exact_partial_without_attempt_proof(
        self,
    ) -> None:
        rollback = workflow_step_script("Rollback partial root-owned launchd helper")
        stage_assignment = (
            'stage_root="/private/var/tmp/task-witness-macos-launchd-'
            '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"\n'
        )
        synthetic = (
            rollback.replace(
                stage_assignment,
                'stage_root="$SYNTHETIC_STAGE_ROOT"\n',
            )
            .replace(
                'created_proof="${{ steps.launchd_stage.outputs.created }}"',
                'created_proof="$SYNTHETIC_CREATED_PROOF"',
            )
            .replace(
                'ownership_nonce="${{ steps.launchd_stage_nonce.outputs.nonce }}"',
                'ownership_nonce="$SYNTHETIC_OWNERSHIP_NONCE"',
            )
            .replace(
                "/usr/bin/sudo -n ",
                "",
            )
            .replace(
                "if os.geteuid() != 0 or os.getegid() != 0:",
                "if False:",
            )
        )
        nonce = "ab" * 32
        marker_name = f".task-witness-stage-owner-{nonce}"

        for label in ("unmarked", "exact-marker"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                stage = Path(directory) / "stage"
                stage.mkdir(mode=0o755)
                partial = stage / "helper.py.partial"
                partial.write_bytes(b"exact-shaped foreign partial\n")
                partial.chmod(0o444)
                if label == "exact-marker":
                    marker = stage / marker_name
                    marker.write_text(nonce)
                    marker.chmod(0o600)
                before = {
                    entry.name: (
                        entry.lstat().st_ino,
                        stat.S_IMODE(entry.lstat().st_mode),
                        entry.read_bytes(),
                    )
                    for entry in stage.iterdir()
                }
                stage_inode = stage.stat().st_ino
                environment = dict(os.environ)
                environment.update(
                    {
                        "HELPER_SHA256": hashlib.sha256(
                            partial.read_bytes()
                        ).hexdigest(),
                        "SYNTHETIC_CREATED_PROOF": "false",
                        "SYNTHETIC_OWNERSHIP_NONCE": nonce,
                        "SYNTHETIC_STAGE_ROOT": str(stage),
                    }
                )

                completed = subprocess.run(
                    ["/bin/bash", "-c", synthetic],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )

                self.assertNotEqual(completed.returncode, 0)
                self.assertTrue(stage.is_dir())
                self.assertEqual(stage.stat().st_ino, stage_inode)
                after = {
                    entry.name: (
                        entry.lstat().st_ino,
                        stat.S_IMODE(entry.lstat().st_mode),
                        entry.read_bytes(),
                    )
                    for entry in stage.iterdir()
                }
                self.assertEqual(after, before)

    def test_workflow_rolls_back_only_recognized_partial_root_stage(self) -> None:
        workflow = WORKFLOW.read_text()
        rollback_name = "Rollback partial root-owned launchd helper"
        rollback_marker = f"      - name: {rollback_name}\n"
        capture_marker = "      - name: Capture the disposable launchd-user probe\n"
        self.assertEqual(workflow.count(rollback_marker), 1)
        rollback_block = workflow[
            workflow.index(rollback_marker) : workflow.index(capture_marker)
        ]
        self.assertIn(
            "              steps.launchd_stage.outcome == 'failure' &&\n",
            rollback_block,
        )
        self.assertIn(
            "              steps.launchd_stage.outputs.created == 'true' }}\n",
            rollback_block,
        )
        capture_block = workflow[
            workflow.index(capture_marker) : workflow.index(
                "      - name: Clean the disposable launchd-user probe\n"
            )
        ]
        self.assertIn(
            "        if: ${{ steps.launchd_stage.outcome == 'success' }}\n",
            capture_block,
        )

        rollback = workflow_step_script(rollback_name)
        self.assertNotRegex(rollback, r"/bin/rm(?:\s|$)")
        self.assertNotRegex(
            rollback,
            r"/usr/bin/sudo[^\n]*(?:/bin/sh|/bin/bash)(?:\s|$)",
        )
        self.assertEqual(
            rollback.count("/usr/bin/sudo -n /usr/bin/python3 -I -B -c"),
            1,
        )
        for required in (
            'created_proof="${{ steps.launchd_stage.outputs.created }}"',
            'ownership_nonce="${{ steps.launchd_stage_nonce.outputs.nonce }}"',
            '/bin/test "$created_proof" = true',
            'ownership_name = f".task-witness-stage-owner-{ownership_nonce}"',
            "os.O_DIRECTORY",
            "os.O_NOFOLLOW",
            "os.O_NONBLOCK",
            "set(os.listdir(stage_descriptor))",
            "ownership_metadata.st_nlink != 1",
            "helper_metadata.st_nlink != 1",
            "os.unlink(helper_name, dir_fd=stage_descriptor)",
            "os.unlink(ownership_name, dir_fd=stage_descriptor)",
            "os.rmdir(stage_path)",
        ):
            self.assertIn(required, rollback)
        self.assertLess(
            rollback.index("names = set(os.listdir(stage_descriptor))"),
            rollback.index("os.unlink(helper_name, dir_fd=stage_descriptor)"),
        )

        stage_assignment = (
            'stage_root="/private/var/tmp/task-witness-macos-launchd-'
            '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"\n'
        )
        self.assertIn(stage_assignment, rollback)
        stage_script = workflow_step_script("Stage the exact root-owned launchd helper")
        copy_program = re.search(
            r"/usr/bin/python3 -I -B -c 'exec\(\"\"\"\n"
            r"(?P<program>.*?)\n\"\"\"\)' \\\n",
            stage_script,
            re.DOTALL,
        )
        inline_programs = re.findall(
            r"/usr/bin/python3 -I -B -c 'exec\(\"\"\"\n"
            r"(.*?)\n\"\"\"\)'(?: \\\n| )",
            stage_script,
            re.DOTALL,
        )
        self.assertIsNotNone(copy_program)
        self.assertEqual(len(inline_programs), 2)
        self.assertEqual(copy_program.group("program").count("os.O_CREAT"), 2)
        self.assertEqual(copy_program.group("program").count("os.O_EXCL"), 2)
        self.assertEqual(
            copy_program.group("program").count("target_descriptor = os.open("),
            1,
        )
        self.assertIn('"helper.py.partial"', copy_program.group("program"))
        self.assertNotIn("INS@", copy_program.group("program"))
        synthetic = (
            rollback.replace(
                stage_assignment,
                'stage_root="$SYNTHETIC_STAGE_ROOT"\n',
            )
            .replace(
                'created_proof="${{ steps.launchd_stage.outputs.created }}"',
                'created_proof="$SYNTHETIC_CREATED_PROOF"',
            )
            .replace(
                'ownership_nonce="${{ steps.launchd_stage_nonce.outputs.nonce }}"',
                'ownership_nonce="$SYNTHETIC_OWNERSHIP_NONCE"',
            )
            .replace(
                "/usr/bin/sudo -n ",
                "",
            )
            .replace(
                "if os.geteuid() != 0 or os.getegid() != 0:",
                "if False:",
            )
        )
        trusted = b"exact helper\n"
        expected_sha256 = hashlib.sha256(trusted).hexdigest()
        nonce = "cd" * 32
        marker_name = f".task-witness-stage-owner-{nonce}"

        def write_marker(stage: Path, raw: str = nonce) -> Path:
            marker = stage / marker_name
            marker.write_text(raw)
            marker.chmod(0o600)
            return marker

        def run_rollback(
            stage: Path,
            helper_sha256: str = expected_sha256,
            created_proof: str = "true",
        ) -> subprocess.CompletedProcess[str]:
            environment = dict(os.environ)
            environment.update(
                {
                    "SYNTHETIC_CREATED_PROOF": created_proof,
                    "SYNTHETIC_OWNERSHIP_NONCE": nonce,
                    "HELPER_SHA256": helper_sha256,
                    "SYNTHETIC_STAGE_ROOT": str(stage),
                }
            )
            return subprocess.run(
                ["/bin/bash", "-c", synthetic],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        with tempfile.TemporaryDirectory() as directory:
            absent = Path(directory) / "absent"
            completed = run_rollback(absent)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(absent.exists())

        for label in ("after-marker", "after-copy", "after-rename"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                stage = Path(directory) / "stage"
                stage.mkdir(mode=0o755)
                write_marker(stage)
                if label == "after-copy":
                    partial = stage / "helper.py.partial"
                    partial.write_bytes(trusted)
                    partial.chmod(0o444)
                elif label == "after-rename":
                    helper = stage / "helper.py"
                    helper.write_bytes(trusted)
                    helper.chmod(0o555)
                completed = run_rollback(stage)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertFalse(stage.exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.py"
            source_raw = bytes(range(256)) * 384
            source.write_bytes(source_raw)
            source.chmod(0o755)
            stage = root / "stage"
            program = copy_program.group("program")
            program = program.replace(
                "if os.geteuid() != 0 or os.getegid() != 0:",
                "if False:",
            )
            interruption_point = "        view = view[written:]\n"
            self.assertIn(interruption_point, program)
            program = program.replace(
                interruption_point,
                interruption_point + "        raise SystemExit(97)\n",
                1,
            )
            source_sha256 = hashlib.sha256(source_raw).hexdigest()
            interrupted = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    program,
                    str(source),
                    str(stage),
                    source_sha256,
                    str(os.geteuid()),
                    str(os.getegid()),
                    nonce,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(interrupted.returncode, 97, interrupted.stderr)
            self.assertFalse(stage.exists())

            for label, point, indentation in (
                (
                    "after-mkdir",
                    "    stage_created = True\n",
                    "    ",
                ),
                (
                    "partial-marker",
                    "        ownership_view = ownership_view[written:]\n",
                    "        ",
                ),
                (
                    "marker-before-source",
                    "    os.fsync(stage_descriptor)\n",
                    "    ",
                ),
                (
                    "empty-partial",
                    "    os.fchown(target_descriptor, privileged_uid, privileged_gid)\n",
                    "    ",
                ),
            ):
                with self.subTest(label=label):
                    interrupted_program = copy_program.group("program").replace(
                        "if os.geteuid() != 0 or os.getegid() != 0:",
                        "if False:",
                    )
                    self.assertIn(point, interrupted_program)
                    interrupted_program = interrupted_program.replace(
                        point,
                        point + indentation + "raise SystemExit(98)\n",
                        1,
                    )
                    failed_stage = root / f"stage-{label}"
                    failed = subprocess.run(
                        [
                            "/usr/bin/python3",
                            "-I",
                            "-B",
                            "-c",
                            interrupted_program,
                            str(source),
                            str(failed_stage),
                            source_sha256,
                            str(os.geteuid()),
                            str(os.getegid()),
                            nonce,
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(failed.returncode, 98, failed.stderr)
                    self.assertFalse(failed_stage.exists())

            completed_stage = root / "stage-complete"
            completed_copy = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    copy_program.group("program").replace(
                        "if os.geteuid() != 0 or os.getegid() != 0:",
                        "if False:",
                    ),
                    str(source),
                    str(completed_stage),
                    source_sha256,
                    str(os.geteuid()),
                    str(os.getegid()),
                    nonce,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed_copy.returncode, 0, completed_copy.stderr)
            partial = completed_stage / "helper.py.partial"
            partial.chmod(0o555)
            partial.rename(completed_stage / "helper.py")
            finalizer = inline_programs[1].replace(
                "if os.geteuid() != 0 or os.getegid() != 0:",
                "if False:",
            )
            finalized = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    finalizer,
                    str(completed_stage),
                    nonce,
                    source_sha256,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            self.assertEqual(
                {entry.name for entry in completed_stage.iterdir()},
                {"helper.py"},
            )
            self.assertEqual(
                (completed_stage / "helper.py").read_bytes(),
                source_raw,
            )

        def snapshot(stage: Path) -> object:
            if stage.is_symlink():
                return ("symlink", os.readlink(stage))
            entries = {}
            for entry in stage.iterdir():
                metadata = entry.lstat()
                payload: bytes | str | None = None
                if stat.S_ISREG(metadata.st_mode):
                    payload = entry.read_bytes()
                elif stat.S_ISLNK(metadata.st_mode):
                    payload = os.readlink(entry)
                entries[entry.name] = (
                    metadata.st_ino,
                    metadata.st_mode,
                    metadata.st_nlink,
                    metadata.st_size,
                    payload,
                )
            return (stage.stat().st_ino, entries)

        for label in (
            "unexpected",
            "install-temp",
            "newline-name",
            "leaf-symlink",
            "special",
            "foreign-helper",
            "wrong-marker",
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stage = root / "stage"
                if label == "leaf-symlink":
                    target = root / "target"
                    target.mkdir(mode=0o755)
                    stage.symlink_to(target, target_is_directory=True)
                else:
                    stage.mkdir(mode=0o755)
                    write_marker(stage)
                    if label == "unexpected":
                        partial = stage / "helper.py.partial"
                        partial.write_bytes(trusted)
                        partial.chmod(0o444)
                        (stage / "foreign").write_bytes(b"preserve")
                    elif label == "install-temp":
                        partial = stage / "helper.py.partial"
                        partial.write_bytes(trusted)
                        partial.chmod(0o444)
                        (stage / "INS@ABCDEF").write_bytes(b"preserve")
                    elif label == "newline-name":
                        partial = stage / "helper.py.partial"
                        partial.write_bytes(trusted)
                        partial.chmod(0o444)
                        (stage / "foreign\nhelper.py").write_bytes(b"preserve")
                    elif label == "special":
                        os.mkfifo(stage / "helper.py.partial", mode=0o444)
                    elif label == "foreign-helper":
                        helper = stage / "helper.py"
                        helper.write_bytes(b"foreign helper\n")
                        helper.chmod(0o555)
                    else:
                        (stage / marker_name).write_text("ef" * 32)
                        partial = stage / "helper.py.partial"
                        partial.write_bytes(trusted)
                        partial.chmod(0o444)
                before = snapshot(stage)
                completed = run_rollback(stage)
                self.assertNotEqual(completed.returncode, 0)
                self.assertTrue(stage.is_symlink() or stage.is_dir())
                self.assertEqual(snapshot(stage), before)

    def test_workflow_cleans_helper_only_initialization_failure_and_still_fails(
        self,
    ) -> None:
        workflow = WORKFLOW.read_text()
        cleanup_marker = "      - name: Clean the disposable launchd-user probe\n"
        seal_marker = "      - name: Seal the bounded launchd-user artifact\n"
        terminal_marker = "      - name: Require an eligible disposable launchd user\n"
        cleanup_block = workflow[
            workflow.index(cleanup_marker) : workflow.index(seal_marker)
        ]
        terminal_block = workflow[workflow.index(terminal_marker) :]
        self.assertIn(
            "        if: ${{ always() && steps.launchd_stage.outcome == 'success' }}\n",
            cleanup_block,
        )
        self.assertIn(
            '              --expected-helper-sha256 "$HELPER_SHA256" \\\n',
            cleanup_block,
        )
        self.assertIn("        if: always()\n", terminal_block)

        capture = workflow_step_script("Capture the disposable launchd-user probe")
        cleanup = workflow_step_script("Clean the disposable launchd-user probe")
        terminal = workflow_step_script("Require an eligible disposable launchd user")
        stage_assignment = (
            'stage_root="/private/var/tmp/task-witness-macos-launchd-'
            '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"\n'
        )
        artifact_assignment = (
            'artifact_root="$RUNNER_TEMP/task-witness-macos-launchd-user-probe"\n'
        )

        for failure_point in ("before-state", "state-write-rollback"):
            with (
                self.subTest(failure_point=failure_point),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                stage = root / "stage"
                stage.mkdir(mode=0o755)
                helper = stage / "helper.py"
                helper_raw = b"trusted staged helper\n"
                helper.write_bytes(helper_raw)
                helper.chmod(0o555)
                helper_sha256 = hashlib.sha256(helper_raw).hexdigest()
                artifact = root / "artifact"
                event_log = root / "events"
                mutation_log = root / "mutation"
                synthetic_python = root / "synthetic-python"
                synthetic_python.write_text(
                    f"""#!/usr/bin/python3
import hashlib
import sys
from pathlib import Path

stage = Path({str(stage)!r})
event_log = Path({str(event_log)!r})
mutation_log = Path({str(mutation_log)!r})
failure_point = {failure_point!r}
command = sys.argv[4]
with event_log.open("a", encoding="utf-8") as stream:
    stream.write(command + "\\n")
if command == "initialize-launchd-user-lifecycle":
    if failure_point == "state-write-rollback":
        plist = stage / "job.plist"
        plist.write_bytes(b"rolled back plist")
        plist.unlink()
    raise SystemExit(2)
if command == "run-launchd-user-lifecycle":
    mutation_log.write_text("unexpected lifecycle mutation", encoding="utf-8")
    raise SystemExit(0)
if command == "cleanup-launchd-user-lifecycle":
    arguments = sys.argv[5:]
    if "--expected-helper-sha256" not in arguments:
        raise SystemExit(91)
    index = arguments.index("--expected-helper-sha256")
    expected = arguments[index + 1]
    helper = stage / "helper.py"
    if hashlib.sha256(helper.read_bytes()).hexdigest() != expected:
        raise SystemExit(92)
    helper.unlink()
    stage.rmdir()
    raise SystemExit(2)
if command == "verify-launchd-success":
    raise SystemExit(2)
raise SystemExit(93)
"""
                )
                synthetic_python.chmod(0o755)

                def synthetic(
                    script: str,
                    *,
                    stage_path: Path = stage,
                    artifact_path: Path = artifact,
                    python_path: Path = synthetic_python,
                ) -> str:
                    return (
                        script.replace(
                            stage_assignment,
                            f'stage_root="{stage_path}"\n',
                        )
                        .replace(
                            artifact_assignment,
                            f'artifact_root="{artifact_path}"\n',
                        )
                        .replace("/usr/bin/sudo -n ", "")
                        .replace("/usr/bin/python3", str(python_path))
                    )

                environment = dict(os.environ)
                environment.update(eligible_context())
                environment.update(
                    {
                        "CANDIDATE_SHA": FROZEN_CANDIDATE_SHA,
                        "HELPER_SHA256": helper_sha256,
                        "RUNNER_TEMP": str(root),
                        "USER_DOMAIN_RESET_AUTHORIZATION_SHA": "1" * 40,
                    }
                )
                captured = subprocess.run(
                    ["/bin/bash", "-c", synthetic(capture)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(captured.returncode, 2, captured.stderr)
                self.assertEqual(
                    {entry.name for entry in stage.iterdir()}, {"helper.py"}
                )

                cleaned = subprocess.run(
                    ["/bin/bash", "-c", synthetic(cleanup)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(cleaned.returncode, 2, cleaned.stderr)
                self.assertFalse(stage.exists())
                self.assertFalse(artifact.exists())
                self.assertFalse(mutation_log.exists())

                required = subprocess.run(
                    ["/bin/bash", "-c", synthetic(terminal)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(required.returncode, 2, required.stderr)
                self.assertEqual(
                    event_log.read_text().splitlines(),
                    [
                        "initialize-launchd-user-lifecycle",
                        "cleanup-launchd-user-lifecycle",
                        "verify-launchd-success",
                    ],
                )

    def test_workflow_runs_launchd_phase_in_same_job_and_cleans_before_upload(
        self,
    ) -> None:
        workflow = WORKFLOW.read_text()
        direct_capture = "      - name: Capture the bounded direct-session probe\n"
        provisioner = "      - name: Require a launchd-capable macOS provisioner\n"
        launchd_capture = "      - name: Capture the disposable launchd-user probe\n"
        cleanup = "      - name: Clean the disposable launchd-user probe\n"
        seal = "      - name: Seal the bounded launchd-user artifact\n"
        upload = "      - name: Retain bounded launchd-user probe diagnostics\n"
        terminal = "      - name: Require an eligible disposable launchd user\n"
        for marker in (
            direct_capture,
            provisioner,
            launchd_capture,
            cleanup,
            seal,
            upload,
            terminal,
        ):
            self.assertEqual(workflow.count(marker), 1)
        self.assertLess(workflow.index(direct_capture), workflow.index(provisioner))
        self.assertLess(workflow.index(provisioner), workflow.index(launchd_capture))
        self.assertLess(workflow.index(launchd_capture), workflow.index(cleanup))
        self.assertLess(workflow.index(cleanup), workflow.index(seal))
        self.assertLess(workflow.index(seal), workflow.index(upload))
        self.assertLess(workflow.index(upload), workflow.index(terminal))
        self.assertNotIn("      - name: Require an eligible direct session\n", workflow)
        cleanup_block = workflow[workflow.index(cleanup) : workflow.index(seal)]
        launchd_block = workflow[
            workflow.index(launchd_capture) : workflow.index(cleanup)
        ]
        upload_block = workflow[workflow.index(upload) : workflow.index(terminal)]
        stage_command = "              initialize-launchd-user-lifecycle \\\n"
        staged_execution = (
            '              "$stage_root/helper.py" \\\n'
            "              run-launchd-user-lifecycle \\\n"
        )
        self.assertEqual(launchd_block.count(stage_command), 1)
        self.assertEqual(launchd_block.count(staged_execution), 1)
        self.assertLess(
            launchd_block.index(stage_command),
            launchd_block.index(staged_execution),
        )
        self.assertIn(
            "        if: ${{ always() && steps.launchd_stage.outcome == 'success' }}\n",
            cleanup_block,
        )
        self.assertIn("steps.launchd_cleanup.outcome == 'success'", upload_block)
        self.assertIn("steps.launchd_seal.outcome == 'success'", upload_block)

    def test_workflow_requires_exact_user_domain_reset_authorization(self) -> None:
        workflow = WORKFLOW.read_text()
        capture = workflow_step_script("Capture the disposable launchd-user probe")
        cleanup = workflow_step_script("Clean the disposable launchd-user probe")
        self.assertIn(
            "vars.TASK_WITNESS_MACOS_HOST_PROBE_RESET == github.sha",
            workflow,
        )
        self.assertIn(
            "USER_DOMAIN_RESET_AUTHORIZATION_SHA: "
            "${{ vars.TASK_WITNESS_MACOS_HOST_PROBE_RESET }}",
            workflow,
        )
        self.assertEqual(
            capture.count(
                "    --user-domain-reset-authorization \\\n"
                '    "$USER_DOMAIN_RESET_AUTHORIZATION_SHA"'
            ),
            1,
        )
        self.assertEqual(
            cleanup.count(
                "    --user-domain-reset-authorization \\\n"
                '    "$USER_DOMAIN_RESET_AUTHORIZATION_SHA"'
            ),
            1,
        )

    def test_user_domain_reset_runner_is_exact_bounded_and_output_free(self) -> None:
        completed = SimpleNamespace(returncode=0)
        with mock.patch.object(
            self.helper.subprocess,
            "run",
            return_value=completed,
        ) as run:
            self.helper._run_user_domain_reset(502)
        run.assert_called_once_with(
            ["/bin/launchctl", "bootout", "user/502"],
            check=False,
            stdin=self.helper.subprocess.DEVNULL,
            stdout=self.helper.subprocess.DEVNULL,
            stderr=self.helper.subprocess.DEVNULL,
            timeout=self.helper.COMMAND_TIMEOUT_SECONDS,
            env={
                "HOME": "/var/empty",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "TZ": "UTC",
            },
        )

        for status, code in (
            (113, "launchd-user-domain-bootout-nonzero"),
            (-1, "launchd-user-domain-bootout-status-invalid"),
            (True, "launchd-user-domain-bootout-status-invalid"),
            (None, "launchd-user-domain-bootout-status-invalid"),
            (256, "launchd-user-domain-bootout-status-invalid"),
        ):
            with (
                self.subTest(status=status),
                mock.patch.object(
                    self.helper.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=status),
                ),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._run_user_domain_reset(502)
            self.assertEqual(raised.exception.code, code)

        with (
            mock.patch.object(
                self.helper.subprocess,
                "run",
                side_effect=self.helper.subprocess.TimeoutExpired(
                    ["/bin/launchctl", "bootout", "user/502"],
                    self.helper.COMMAND_TIMEOUT_SECONDS,
                ),
            ),
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._run_user_domain_reset(502)
        self.assertEqual(
            raised.exception.code,
            "launchd-user-domain-bootout-timeout",
        )

    def test_user_domain_reset_requires_exact_hosted_capability(self) -> None:
        context = eligible_context()
        authorization = context["GITHUB_SHA"]
        harness, runner = self.helper._require_user_domain_reset_capability(
            authorization,
            context,
        )
        self.assertEqual(harness["commit_sha1"], authorization)
        self.assertEqual(runner["environment"], "github-hosted")

        mutations = {
            "authorization": ("2" * 40, context),
            "environment": (
                authorization,
                {**context, "RUNNER_ENVIRONMENT": "self-hosted"},
            ),
            "os": (authorization, {**context, "RUNNER_OS": "Linux"}),
            "arch": (authorization, {**context, "RUNNER_ARCH": "X64"}),
            "image": (authorization, {**context, "ImageOS": "macos14"}),
            "image-version": (authorization, {**context, "ImageVersion": ""}),
        }
        for label, (candidate, environment) in mutations.items():
            with (
                self.subTest(label=label),
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._require_user_domain_reset_capability(
                    candidate,
                    environment,
                )
            self.assertEqual(
                raised.exception.code,
                "user-domain-reset-capability-unavailable",
            )

    def test_user_domain_reset_is_armed_before_the_exact_mutation(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        account = self.helper._account_binding_document(plan, state, generated_uid)
        ownership = self.helper._launchd_ownership_document(plan, state)
        bindings = self.helper.ValidatedStageBindings(account, ownership)
        authorization = eligible_context()["GITHUB_SHA"]
        marker = {"content_sha256": "4" * 64}
        events: list[str] = []

        def wait(_uid: int) -> None:
            events.append("wait")
            if events.count("wait") == 1:
                raise self.helper.ProbeError(
                    "disposable-user-pid1-parented-processes-remain"
                )

        def arm(*_args: object, **_kwargs: object) -> dict:
            events.append("arm")
            return marker

        def reset(_uid: int) -> None:
            events.append("reset")

        def stable(_uid: int) -> None:
            events.append("stable-zero")

        def validate(*_args: object, **kwargs: object) -> None:
            events.append(f"binding:{kwargs['home_drift_phase']}")

        with (
            mock.patch.object(
                self.helper,
                "_wait_for_no_uid_processes",
                side_effect=wait,
            ),
            mock.patch.object(
                self.helper,
                "_write_user_domain_reset_authorization",
                side_effect=arm,
            ),
            mock.patch.object(
                self.helper,
                "_run_user_domain_reset",
                side_effect=reset,
            ),
            mock.patch.object(
                self.helper,
                "_require_stable_no_uid_processes",
                side_effect=stable,
            ),
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_validate_reset_bindings_and_resources",
                side_effect=validate,
            ),
        ):
            evidence = self.helper._quiesce_disposable_user(
                plan,
                state,
                bindings,
                authorization,
                eligible_context(),
                allow_create=True,
            )

        self.assertEqual(
            events,
            [
                "wait",
                "arm",
                "binding:pre-reset",
                "reset",
                "wait",
                "stable-zero",
                "binding:post-reset",
            ],
        )
        self.assertEqual(
            evidence,
            {
                "authorization_sha256": "4" * 64,
                "capability": "github-hosted-ephemeral-user-domain-reset-v1",
                "disposition": "performed",
                "precondition": ("disposable-user-pid1-parented-processes-remain"),
            },
        )

    def test_post_reset_home_drift_reaches_lifecycle_boundary_exactly(self) -> None:
        context = self.launchd_context()
        plan = self.lifecycle_plan(
            Path("/private/var/tmp/task-witness-macos-launchd-123456789-2")
        )
        state = self.lifecycle_state(plan)
        loaded = self.launchctl_job(plan, state)
        marker = {"content_sha256": "4" * 64}
        code = "home-cleanup-post-reset-home-entry-set-drift"
        phases: list[str] = []
        stderr = io.StringIO()

        def command(argv: list[str], **_kwargs: object) -> str:
            if argv[:3] == ["/bin/launchctl", "bootstrap", "system"]:
                return ""
            if argv[:3] == ["/bin/launchctl", "kickstart", "-p"]:
                return "4321"
            raise AssertionError(argv)

        wait_count = 0

        def wait(_uid: int) -> None:
            nonlocal wait_count
            wait_count += 1
            if wait_count == 1:
                raise self.helper.ProbeError(
                    "disposable-user-pid1-parented-processes-remain"
                )

        def validate(*_args: object, **kwargs: object) -> None:
            phase = str(kwargs["home_drift_phase"])
            phases.append(phase)
            if phase == "post-reset":
                raise self.helper.ProbeError(code)

        with (
            mock.patch.dict(os.environ, context, clear=True),
            mock.patch.multiple(
                self.helper,
                _normalized_context=mock.DEFAULT,
                _validate_lifecycle_arguments=mock.DEFAULT,
                _create_root_directory=mock.DEFAULT,
                _create_disposable_account=mock.DEFAULT,
                _create_disposable_home=mock.DEFAULT,
                _validate_disposable_home_root=mock.DEFAULT,
                _require_disposable_uid_available=mock.DEFAULT,
                _require_launchd_absent=mock.DEFAULT,
                _write_launchd_ownership_marker=mock.DEFAULT,
                _reconcile_in_process_bootstrap=mock.DEFAULT,
                _run_user_domain_reset=mock.DEFAULT,
                _require_stable_no_uid_processes=mock.DEFAULT,
            ),
            mock.patch.object(
                self.helper,
                "_load_lifecycle_state",
                return_value=(plan, state),
            ),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=self.helper.ValidatedStageBindings(None, None),
            ),
            mock.patch.object(
                self.helper,
                "_require_command_success",
                side_effect=command,
            ),
            mock.patch.object(
                self.helper,
                "_launchd_job_snapshot",
                return_value=loaded,
            ),
            mock.patch.object(
                self.helper,
                "_poll_launchd_terminal",
                return_value=(loaded, 0),
            ),
            mock.patch.object(
                self.helper,
                "_load_canonical_document",
                return_value={"observations": {"process": {"pid": 4321}}},
            ),
            mock.patch.object(
                self.helper,
                "_wait_for_no_uid_processes",
                side_effect=wait,
            ),
            mock.patch.object(
                self.helper,
                "_write_user_domain_reset_authorization",
                return_value=marker,
            ),
            mock.patch.object(
                self.helper,
                "_validate_reset_bindings_and_resources",
                side_effect=validate,
            ),
            mock.patch.object(self.helper, "_write_lifecycle_artifact") as write,
            redirect_stderr(stderr),
        ):
            status = self.helper.run_launchd_user_lifecycle(
                stage_root=plan.stage_root,
                artifact_root=Path("/private/tmp/artifact"),
                candidate_sha=FROZEN_CANDIDATE_SHA,
                runner_uid=501,
                runner_gid=20,
                user_domain_reset_authorization=context["GITHUB_SHA"],
            )

        self.assertEqual(status, 2)
        self.assertEqual(
            phases,
            ["pre-reset", "post-reset"],
            repr(write.call_args),
        )
        self.assertEqual(write.call_args.kwargs["error_code"], code)
        self.assertIsNone(write.call_args.kwargs["secondary_error_code"])
        self.assertEqual(
            stderr.getvalue(),
            f"task-witness macOS launchd-user lifecycle: {code}\n",
        )

    def test_same_uid_process_tree_never_authorizes_domain_reset(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        account = self.helper._account_binding_document(
            plan,
            state,
            "01234567-89AB-4DEF-8123-456789ABCDEF",
        )
        ownership = self.helper._launchd_ownership_document(plan, state)
        bindings = self.helper.ValidatedStageBindings(account, ownership)
        records = (
            self.helper.ProcessRecord(0, 1, 0, 1, "Ss", "launchd"),
            self.helper.ProcessRecord(
                plan.account.uid,
                300,
                1,
                300,
                "S",
                "private-parent",
            ),
            self.helper.ProcessRecord(
                plan.account.uid,
                301,
                300,
                301,
                "S",
                "private-child",
            ),
        )

        def wait(uid: int) -> None:
            code = self.helper._process_survivor_code(records, uid)
            if code is not None:
                raise self.helper.ProbeError(code)

        with (
            mock.patch.object(
                self.helper,
                "_wait_for_no_uid_processes",
                side_effect=wait,
            ),
            mock.patch.object(
                self.helper,
                "_write_user_domain_reset_authorization",
                return_value={"content_sha256": "4" * 64},
            ) as arm,
            mock.patch.object(
                self.helper,
                "_validate_reset_bindings_and_resources",
            ),
            mock.patch.object(self.helper, "_run_user_domain_reset") as reset,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._quiesce_disposable_user(
                plan,
                state,
                bindings,
                eligible_context()["GITHUB_SHA"],
                eligible_context(),
                allow_create=True,
            )
        self.assertEqual(
            raised.exception.code,
            "disposable-user-same-uid-process-tree-remains",
        )
        arm.assert_not_called()
        reset.assert_not_called()

    def test_user_domain_reset_writer_is_durable_before_bootout(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        account = self.helper._account_binding_document(plan, state, generated_uid)
        ownership = self.helper._launchd_ownership_document(plan, state)
        bindings = self.helper.ValidatedStageBindings(account, ownership)
        authorization = eligible_context()["GITHUB_SHA"]
        home_identity = {
            "home_device": 1,
            "home_inode": 2,
            "probe_device": 1,
            "probe_inode": 3,
        }
        events: list[str] = []
        written: dict[str, object] = {}

        def wait(_uid: int) -> None:
            events.append("wait")
            if events.count("wait") == 1:
                raise self.helper.ProbeError(
                    "disposable-user-pid1-parented-processes-remain"
                )

        def write(path: Path, raw: bytes, mode: int) -> None:
            events.append("write")
            written.update(path=path, raw=raw, mode=mode)

        def readback(*_args: object, **_kwargs: object) -> dict:
            events.append("readback")
            return json.loads(bytes(written["raw"]))

        with (
            mock.patch.object(
                self.helper,
                "_wait_for_no_uid_processes",
                side_effect=wait,
            ),
            mock.patch.object(
                self.helper,
                "_read_system_generated_uid",
                return_value=generated_uid,
            ),
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_load_account_binding",
                return_value=account,
            ),
            mock.patch.object(
                self.helper,
                "_exact_disposable_home_identity",
                return_value=home_identity,
            ) as read_home_identity,
            mock.patch.object(
                self.helper,
                "_write_stage_create_new",
                side_effect=write,
            ),
            mock.patch.object(
                self.helper,
                "_fsync_stage_directory",
                side_effect=lambda _stage: events.append("fsync"),
            ) as sync,
            mock.patch.object(
                self.helper,
                "_load_user_domain_reset_authorization",
                side_effect=readback,
            ) as load,
            mock.patch.object(
                self.helper,
                "_validate_reset_bindings_and_resources",
                side_effect=lambda *_args, **_kwargs: events.append("binding"),
            ),
            mock.patch.object(
                self.helper,
                "_run_user_domain_reset",
                side_effect=lambda _uid: events.append("reset"),
            ),
            mock.patch.object(
                self.helper,
                "_require_stable_no_uid_processes",
                side_effect=lambda _uid: events.append("stable-zero"),
            ),
        ):
            evidence = self.helper._quiesce_disposable_user(
                plan,
                state,
                bindings,
                authorization,
                eligible_context(),
                allow_create=True,
            )

        self.assertEqual(
            events,
            [
                "wait",
                "write",
                "fsync",
                "readback",
                "binding",
                "reset",
                "wait",
                "stable-zero",
                "binding",
            ],
        )
        self.assertEqual(written["path"], plan.stage_root / "domain-reset.json")
        self.assertEqual(written["mode"], 0o600)
        read_home_identity.assert_called_once_with(
            plan.account,
            diagnostic_phase="pre-reset-marker",
        )
        sync.assert_called_once_with(plan.stage_root)
        load.assert_called_once_with(
            plan,
            state,
            account,
            ownership,
            authorization,
            eligible_context(),
        )
        self.assertEqual(
            evidence["authorization_sha256"],
            json.loads(bytes(written["raw"]))["content_sha256"],
        )

    def test_user_domain_reset_publication_interruptions_leave_retryable_stage(
        self,
    ) -> None:
        authorization = eligible_context()["GITHUB_SHA"]
        home_identity = {
            "home_device": 1,
            "home_inode": 2,
            "probe_device": 1,
            "probe_inode": 3,
        }
        for cut in ("before-rename", "after-rename"):
            with self.subTest(cut=cut), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stage = root / "stage"
                stage.mkdir()
                plan = self.lifecycle_plan(stage)
                helper_raw = b"exact helper\n"
                plist_raw = b"exact plist\n"
                plan.helper.write_bytes(helper_raw)
                plan.plist.write_bytes(plist_raw)
                unsigned_state = {
                    name: value
                    for name, value in self.lifecycle_state(plan).items()
                    if name != "content_sha256"
                }
                unsigned_state["helper_sha256"] = hashlib.sha256(helper_raw).hexdigest()
                unsigned_state["plist_sha256"] = hashlib.sha256(plist_raw).hexdigest()
                state = self.helper._document_with_digest(unsigned_state)
                (stage / "state.json").write_bytes(self.helper.canonical_bytes(state))
                generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
                account = self.helper._account_binding_document(
                    plan,
                    state,
                    generated_uid,
                )
                ownership = self.helper._launchd_ownership_document(plan, state)
                (stage / "account.json").write_bytes(
                    self.helper.canonical_bytes(account)
                )
                (stage / "ownership.json").write_bytes(
                    self.helper.canonical_bytes(ownership)
                )
                bindings = self.helper.ValidatedStageBindings(account, ownership)

                def interrupt(
                    source: Path,
                    destination: Path,
                    selected: str = cut,
                ) -> None:
                    if selected == "after-rename":
                        os.rename(source, destination)
                    raise KeyboardInterrupt

                with (
                    mock.patch.object(
                        self.helper,
                        "_read_system_generated_uid",
                        return_value=generated_uid,
                    ),
                    mock.patch.object(self.helper, "_require_launchd_absent"),
                    mock.patch.object(
                        self.helper,
                        "_exact_disposable_home_identity",
                        return_value=home_identity,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        return_value=True,
                    ),
                    mock.patch.object(self.helper, "_fsync_stage_directory"),
                    mock.patch.object(
                        self.helper,
                        "_rename_exclusive",
                        side_effect=interrupt,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    self.helper._write_user_domain_reset_authorization(
                        plan,
                        state,
                        bindings,
                        authorization,
                        eligible_context(),
                    )

                expected_names = {
                    "helper.py",
                    "job.plist",
                    "state.json",
                    "account.json",
                    "ownership.json",
                }
                if cut == "after-rename":
                    expected_names.add("domain-reset.json")
                self.assertEqual(
                    {entry.name for entry in stage.iterdir()},
                    expected_names,
                )
                self.assertEqual({entry.name for entry in root.iterdir()}, {"stage"})

                with (
                    mock.patch.object(
                        self.helper,
                        "_read_system_generated_uid",
                        return_value=generated_uid,
                    ),
                    mock.patch.object(self.helper, "_require_launchd_absent"),
                    mock.patch.object(
                        self.helper,
                        "_exact_disposable_home_identity",
                        return_value=home_identity,
                    ),
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        return_value=True,
                    ),
                    mock.patch.object(self.helper, "_fsync_stage_directory"),
                ):
                    if cut == "before-rename":
                        marker = self.helper._write_user_domain_reset_authorization(
                            plan,
                            state,
                            bindings,
                            authorization,
                            eligible_context(),
                        )
                    else:
                        marker = self.helper._load_user_domain_reset_authorization(
                            plan,
                            state,
                            account,
                            ownership,
                            authorization,
                            eligible_context(),
                        )
                    self.assertIsNotNone(marker)
                    self.assertEqual(
                        (stage / "domain-reset.json").lstat().st_nlink,
                        1,
                    )
                    observed = self.helper._validate_exact_stage(
                        plan,
                        state,
                        user_domain_reset_authorization=authorization,
                        environment=eligible_context(),
                    )
                    self.assertEqual(observed.domain_reset, marker)

                    with (
                        mock.patch.object(
                            self.helper,
                            "_validate_reset_bindings_and_resources",
                        ),
                        mock.patch.object(
                            self.helper,
                            "_wait_for_no_uid_processes",
                            side_effect=[
                                self.helper.ProbeError(
                                    "disposable-user-pid1-parented-processes-remain"
                                ),
                                None,
                            ],
                        ),
                        mock.patch.object(
                            self.helper,
                            "_run_user_domain_reset",
                        ) as reset,
                        mock.patch.object(
                            self.helper,
                            "_require_stable_no_uid_processes",
                        ),
                    ):
                        evidence = self.helper._quiesce_disposable_user(
                            plan,
                            state,
                            observed,
                            authorization,
                            eligible_context(),
                            allow_create=False,
                        )
                    reset.assert_called_once_with(plan.account.uid)
                    self.assertEqual(
                        evidence,
                        self.helper._domain_reset_evidence(marker),
                    )

    def test_user_domain_reset_writer_failure_prevents_bootout(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        account = self.helper._account_binding_document(plan, state, generated_uid)
        ownership = self.helper._launchd_ownership_document(plan, state)
        bindings = self.helper.ValidatedStageBindings(account, ownership)
        authorization = eligible_context()["GITHUB_SHA"]
        home_identity = {
            "home_device": 1,
            "home_inode": 2,
            "probe_device": 1,
            "probe_inode": 3,
        }
        for failure, code in (
            ("fsync", "user-domain-reset-stage-fsync-failed"),
            ("readback", "user-domain-reset-authorization-drift"),
            ("disagree", "user-domain-reset-authorization-disagrees"),
        ):
            with self.subTest(failure=failure):
                events: list[str] = []
                written: dict[str, object] = {}

                def write(
                    path: Path,
                    raw: bytes,
                    mode: int,
                    observed: list[str] = events,
                    output: dict[str, object] = written,
                ) -> None:
                    observed.append("write")
                    output.update(path=path, raw=raw, mode=mode)

                def fsync(
                    _stage: Path,
                    observed: list[str] = events,
                    selected: str = failure,
                    error_code: str = code,
                ) -> None:
                    observed.append("fsync")
                    if selected == "fsync":
                        raise self.helper.ProbeError(error_code)

                def readback(
                    *_args: object,
                    observed: list[str] = events,
                    selected: str = failure,
                    error_code: str = code,
                    output: dict[str, object] = written,
                    **_kwargs: object,
                ) -> dict:
                    observed.append("readback")
                    if selected == "readback":
                        raise self.helper.ProbeError(error_code)
                    if selected == "disagree":
                        return {}
                    return json.loads(bytes(output["raw"]))

                with ExitStack() as stack:
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_wait_for_no_uid_processes",
                            side_effect=self.helper.ProbeError(
                                "disposable-user-pid1-parented-processes-remain"
                            ),
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_read_system_generated_uid",
                            return_value=generated_uid,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(self.helper, "_require_launchd_absent")
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_load_account_binding",
                            return_value=account,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_exact_disposable_home_identity",
                            return_value=home_identity,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_write_stage_create_new",
                            side_effect=write,
                        )
                    )
                    sync = stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_fsync_stage_directory",
                            side_effect=fsync,
                        )
                    )
                    load = stack.enter_context(
                        mock.patch.object(
                            self.helper,
                            "_load_user_domain_reset_authorization",
                            side_effect=readback,
                        )
                    )
                    reset = stack.enter_context(
                        mock.patch.object(self.helper, "_run_user_domain_reset")
                    )
                    with self.assertRaises(self.helper.ProbeError) as raised:
                        self.helper._quiesce_disposable_user(
                            plan,
                            state,
                            bindings,
                            authorization,
                            eligible_context(),
                            allow_create=True,
                        )
                self.assertEqual(raised.exception.code, code)
                reset.assert_not_called()
                sync.assert_called_once_with(plan.stage_root)
                if failure == "fsync":
                    load.assert_not_called()
                else:
                    load.assert_called_once_with(
                        plan,
                        state,
                        account,
                        ownership,
                        authorization,
                        eligible_context(),
                    )
                self.assertEqual(
                    events,
                    ["write", "fsync"]
                    + (["readback"] if failure in {"readback", "disagree"} else []),
                )

    def test_cleanup_reestablishes_reset_authorization_after_failed_fsync(
        self,
    ) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        account = self.helper._account_binding_document(plan, state, generated_uid)
        ownership = self.helper._launchd_ownership_document(plan, state)
        bindings = self.helper.ValidatedStageBindings(account, ownership)
        authorization = eligible_context()["GITHUB_SHA"]
        home_identity = {
            "home_device": 1,
            "home_inode": 2,
            "probe_device": 1,
            "probe_inode": 3,
        }
        events: list[str] = []
        written: dict[str, object] = {}

        def write(path: Path, raw: bytes, mode: int) -> None:
            events.append("write")
            written.update(path=path, raw=raw, mode=mode)

        def fsync(_stage: Path) -> None:
            events.append("fsync")
            if events.count("fsync") == 1:
                raise self.helper.ProbeError("user-domain-reset-stage-fsync-failed")

        def readback(*_args: object, **_kwargs: object) -> dict:
            events.append("readback")
            return json.loads(bytes(written["raw"]))

        def wait(_uid: int) -> None:
            events.append("wait")
            if events.count("wait") == 1:
                raise self.helper.ProbeError(
                    "disposable-user-pid1-parented-processes-remain"
                )

        with (
            mock.patch.object(
                self.helper,
                "_read_system_generated_uid",
                return_value=generated_uid,
            ),
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_load_account_binding",
                return_value=account,
            ),
            mock.patch.object(
                self.helper,
                "_exact_disposable_home_identity",
                return_value=home_identity,
            ),
            mock.patch.object(
                self.helper,
                "_write_stage_create_new",
                side_effect=write,
            ),
            mock.patch.object(
                self.helper,
                "_fsync_stage_directory",
                side_effect=fsync,
            ) as sync,
            mock.patch.object(
                self.helper,
                "_load_user_domain_reset_authorization",
                side_effect=readback,
            ) as load,
            mock.patch.object(
                self.helper,
                "_validate_reset_bindings_and_resources",
                side_effect=lambda *_args, **_kwargs: events.append("binding"),
            ),
            mock.patch.object(
                self.helper,
                "_wait_for_no_uid_processes",
                side_effect=wait,
            ),
            mock.patch.object(
                self.helper,
                "_run_user_domain_reset",
                side_effect=lambda _uid: events.append("reset"),
            ) as reset,
            mock.patch.object(
                self.helper,
                "_require_stable_no_uid_processes",
                side_effect=lambda _uid: events.append("stable-zero"),
            ),
        ):
            with self.assertRaises(self.helper.ProbeError) as raised:
                self.helper._write_user_domain_reset_authorization(
                    plan,
                    state,
                    bindings,
                    authorization,
                    eligible_context(),
                )
            self.assertEqual(
                raised.exception.code,
                "user-domain-reset-stage-fsync-failed",
            )
            marker = json.loads(bytes(written["raw"]))
            recovered = self.helper._quiesce_disposable_user(
                plan,
                state,
                self.helper.ValidatedStageBindings(account, ownership, marker),
                authorization,
                eligible_context(),
                allow_create=False,
            )

        self.assertEqual(
            events,
            [
                "write",
                "fsync",
                "fsync",
                "readback",
                "binding",
                "wait",
                "binding",
                "reset",
                "wait",
                "stable-zero",
                "binding",
            ],
        )
        self.assertEqual(sync.call_count, 2)
        load.assert_called_once_with(
            plan,
            state,
            account,
            ownership,
            authorization,
            eligible_context(),
        )
        reset.assert_called_once_with(plan.account.uid)
        self.assertEqual(
            recovered["authorization_sha256"],
            marker["content_sha256"],
        )

    def test_cleanup_refuses_reset_when_marker_durability_cannot_be_restored(
        self,
    ) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        marker = {"content_sha256": "4" * 64}
        bindings = self.helper.ValidatedStageBindings(
            {"account": "exact"},
            {"ownership": "exact"},
            marker,
        )
        with (
            mock.patch.object(
                self.helper,
                "_fsync_stage_directory",
                side_effect=self.helper.ProbeError(
                    "user-domain-reset-stage-fsync-failed"
                ),
            ) as sync,
            mock.patch.object(
                self.helper,
                "_load_user_domain_reset_authorization",
            ) as load,
            mock.patch.object(
                self.helper,
                "_wait_for_no_uid_processes",
            ) as wait,
            mock.patch.object(self.helper, "_run_user_domain_reset") as reset,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._quiesce_disposable_user(
                plan,
                state,
                bindings,
                eligible_context()["GITHUB_SHA"],
                eligible_context(),
                allow_create=False,
            )
        self.assertEqual(
            raised.exception.code,
            "user-domain-reset-stage-fsync-failed",
        )
        sync.assert_called_once_with(plan.stage_root)
        load.assert_not_called()
        wait.assert_not_called()
        reset.assert_not_called()

    def test_user_domain_reset_marker_is_canonical_and_fully_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
            account = self.helper._account_binding_document(
                plan,
                state,
                generated_uid,
            )
            ownership = self.helper._launchd_ownership_document(plan, state)
            identity = {
                "home_device": 1,
                "home_inode": 2,
                "probe_device": 1,
                "probe_inode": 3,
            }
            authorization = eligible_context()["GITHUB_SHA"]
            with (
                mock.patch.object(
                    self.helper,
                    "_load_account_binding",
                    return_value=account,
                ),
                mock.patch.object(
                    self.helper,
                    "_exact_disposable_home_identity",
                    return_value=identity,
                ),
            ):
                document = self.helper._user_domain_reset_document(
                    plan,
                    state,
                    account,
                    ownership,
                    authorization,
                    eligible_context(),
                )
            self.assertEqual(document["target"], "user/502")
            self.assertEqual(
                document["authorized_survivor_code"],
                "disposable-user-pid1-parented-processes-remain",
            )
            self.assertEqual(document["home_identity"], identity)
            self.assertEqual(
                document["account_binding_sha256"],
                account["content_sha256"],
            )
            self.assertLessEqual(
                len(self.helper.canonical_bytes(document)),
                self.helper.MAX_USER_DOMAIN_RESET_BYTES,
            )
            path = stage / "domain-reset.json"
            path.write_bytes(self.helper.canonical_bytes(document))
            with (
                mock.patch.object(
                    self.helper,
                    "_metadata_matches",
                    return_value=True,
                ) as metadata,
                mock.patch.object(
                    self.helper,
                    "_load_account_binding",
                    return_value=account,
                ),
                mock.patch.object(
                    self.helper,
                    "_exact_disposable_home_identity",
                    side_effect=AssertionError(
                        "loading an armed reset must not require a live home"
                    ),
                ) as current_home,
            ):
                self.assertEqual(
                    self.helper._load_user_domain_reset_authorization(
                        plan,
                        state,
                        account,
                        ownership,
                        authorization,
                        eligible_context(),
                    ),
                    document,
                )
            current_home.assert_not_called()
            metadata.assert_called_once_with(
                path,
                kind="file",
                mode=0o600,
                uid=0,
                gid=0,
                nlink=1,
            )

    def test_home_cleanup_authorization_is_durable_before_first_unlink(
        self,
    ) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        account = self.helper._account_binding_document(plan, state, generated_uid)
        ownership = self.helper._launchd_ownership_document(plan, state)
        marker = {
            "content_sha256": "4" * 64,
            "home_identity": {
                "home_device": 1,
                "home_inode": 2,
                "probe_device": 1,
                "probe_inode": 3,
            },
        }
        bindings = self.helper.ValidatedStageBindings(account, ownership, marker)
        events: list[str] = []
        written: dict[str, object] = {}

        def write(path: Path, raw: bytes, mode: int) -> None:
            events.append("write")
            written.update(path=path, raw=raw, mode=mode)

        def readback(*_args: object, **_kwargs: object) -> dict:
            events.append("readback")
            return json.loads(bytes(written["raw"]))

        def validate_home(*_args: object, **_kwargs: object) -> None:
            events.append("validate-home")

        def inventory(*_args: object, **_kwargs: object) -> None:
            events.append("inventory")

        with (
            mock.patch.object(
                self.helper,
                "_require_bound_home_path",
                side_effect=validate_home,
            ) as validate,
            mock.patch.object(
                self.helper,
                "_bounded_library_inventory",
                side_effect=inventory,
            ) as read_inventory,
            mock.patch.object(
                self.helper,
                "_write_stage_create_new",
                side_effect=write,
            ),
            mock.patch.object(
                self.helper,
                "_fsync_stage_directory",
                side_effect=lambda _stage: events.append("fsync"),
            ) as sync,
            mock.patch.object(
                self.helper,
                "_load_home_cleanup_authorization",
                side_effect=readback,
            ) as load,
        ):
            document = self.helper._write_home_cleanup_authorization(
                plan,
                state,
                bindings,
                marker["home_identity"],
            )

        self.assertEqual(
            events,
            [
                "validate-home",
                "inventory",
                "validate-home",
                "write",
                "fsync",
                "readback",
            ],
        )
        self.assertEqual(
            validate.call_args_list,
            [
                mock.call(plan.account, marker["home_identity"]),
                mock.call(plan.account, marker["home_identity"]),
            ],
        )
        read_inventory.assert_called_once_with(plan.account)
        self.assertEqual(
            written,
            {
                "path": plan.stage_root / "home-cleanup.json",
                "raw": self.helper.canonical_bytes(document),
                "mode": 0o600,
            },
        )
        sync.assert_called_once_with(plan.stage_root)
        load.assert_called_once_with(plan, state, bindings)
        self.assertEqual(document["domain_reset_sha256"], "4" * 64)
        self.assertEqual(document["home_identity"], marker["home_identity"])

    def test_home_cleanup_authorization_round_trips_without_reset_or_ownership(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            account = self.helper._account_binding_document(
                plan,
                state,
                "01234567-89AB-4DEF-8123-456789ABCDEF",
            )
            bindings = self.helper.ValidatedStageBindings(account, None)
            identity = {
                "home_device": 1,
                "home_inode": 2,
                "probe_device": 1,
                "probe_inode": 3,
            }
            with (
                mock.patch.object(
                    self.helper,
                    "_metadata_matches",
                    return_value=True,
                ),
                mock.patch.object(self.helper, "_require_bound_home_path"),
                mock.patch.object(
                    self.helper,
                    "_bounded_library_inventory",
                    return_value=None,
                ),
                mock.patch.object(self.helper, "_fsync_stage_directory"),
            ):
                document = self.helper._write_home_cleanup_authorization(
                    plan,
                    state,
                    bindings,
                    identity,
                )
                self.assertEqual(
                    self.helper._load_home_cleanup_authorization(
                        plan,
                        state,
                        bindings,
                    ),
                    document,
                )
            self.assertEqual(document["ownership_sha256"], "none")
            self.assertEqual(document["domain_reset_sha256"], "none")
            self.assertEqual(
                document["account"],
                {
                    "name": plan.account.name,
                    "uid": plan.account.uid,
                    "gid": plan.account.gid,
                    "home": str(plan.account.home),
                },
            )

    def test_home_cleanup_authorization_fsync_failure_prevents_readback(
        self,
    ) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        account = self.helper._account_binding_document(
            plan,
            state,
            "01234567-89AB-4DEF-8123-456789ABCDEF",
        )
        ownership = self.helper._launchd_ownership_document(plan, state)
        bindings = self.helper.ValidatedStageBindings(
            account,
            ownership,
            {
                "content_sha256": "4" * 64,
                "home_identity": {
                    "home_device": 1,
                    "home_inode": 2,
                    "probe_device": 1,
                    "probe_inode": 3,
                },
            },
        )
        with (
            mock.patch.object(self.helper, "_require_bound_home_path"),
            mock.patch.object(
                self.helper,
                "_bounded_library_inventory",
                return_value=None,
            ),
            mock.patch.object(self.helper, "_write_stage_create_new"),
            mock.patch.object(
                self.helper,
                "_fsync_stage_directory",
                side_effect=self.helper.ProbeError(
                    "user-domain-reset-stage-fsync-failed"
                ),
            ),
            mock.patch.object(
                self.helper,
                "_load_home_cleanup_authorization",
            ) as load,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._write_home_cleanup_authorization(
                plan,
                state,
                bindings,
                bindings.domain_reset["home_identity"],
            )
        self.assertEqual(
            raised.exception.code,
            "user-domain-reset-stage-fsync-failed",
        )
        load.assert_not_called()

    def test_home_cleanup_publication_interruptions_leave_retryable_stage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage"
            stage.mkdir()
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            account = self.helper._account_binding_document(
                plan,
                state,
                "01234567-89AB-4DEF-8123-456789ABCDEF",
            )
            ownership = self.helper._launchd_ownership_document(plan, state)
            bindings = self.helper.ValidatedStageBindings(
                account,
                ownership,
                {
                    "content_sha256": "4" * 64,
                    "home_identity": {
                        "home_device": 1,
                        "home_inode": 2,
                        "probe_device": 1,
                        "probe_inode": 3,
                    },
                },
            )
            document = self.helper._home_cleanup_document(
                plan,
                state,
                bindings,
                bindings.domain_reset["home_identity"],
            )
            raw = self.helper.canonical_bytes(document)
            destination = stage / "home-cleanup.json"

            with (
                mock.patch.object(
                    self.helper,
                    "_rename_exclusive",
                    side_effect=KeyboardInterrupt,
                ),
                mock.patch.object(
                    self.helper.tempfile,
                    "mkstemp",
                    wraps=tempfile.mkstemp,
                ) as create,
                self.assertRaises(KeyboardInterrupt),
            ):
                self.helper._write_stage_create_new(destination, raw, 0o600)
            self.assertEqual(list(stage.iterdir()), [])
            self.assertEqual(create.call_args.kwargs["dir"], stage.parent)

            def rename_then_interrupt(source: Path, target: Path) -> None:
                os.rename(source, target)
                raise KeyboardInterrupt

            with (
                mock.patch.object(
                    self.helper,
                    "_rename_exclusive",
                    side_effect=rename_then_interrupt,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                self.helper._write_stage_create_new(destination, raw, 0o600)
            self.assertEqual(
                {entry.name for entry in stage.iterdir()}, {destination.name}
            )
            self.assertEqual(destination.lstat().st_nlink, 1)
            self.assertEqual(destination.read_bytes(), raw)
            with mock.patch.object(
                self.helper,
                "_metadata_matches",
                return_value=True,
            ):
                self.assertEqual(
                    self.helper._load_home_cleanup_authorization(
                        plan,
                        state,
                        bindings,
                    ),
                    document,
                )

    def test_cleanup_cannot_arm_reset_without_lifecycle_authorization(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        account = self.helper._account_binding_document(plan, state, generated_uid)
        ownership = self.helper._launchd_ownership_document(plan, state)
        bindings = self.helper.ValidatedStageBindings(account, ownership)
        with (
            mock.patch.object(
                self.helper,
                "_wait_for_no_uid_processes",
                side_effect=self.helper.ProbeError(
                    "disposable-user-pid1-parented-processes-remain"
                ),
            ),
            mock.patch.object(
                self.helper,
                "_write_user_domain_reset_authorization",
            ) as arm,
            mock.patch.object(self.helper, "_run_user_domain_reset") as reset,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._quiesce_disposable_user(
                plan,
                state,
                bindings,
                eligible_context()["GITHUB_SHA"],
                eligible_context(),
                allow_create=False,
            )
        self.assertEqual(
            raised.exception.code,
            "disposable-user-pid1-parented-processes-remain",
        )
        arm.assert_not_called()
        reset.assert_not_called()

    def test_cleanup_can_retry_only_an_exact_existing_reset_authorization(
        self,
    ) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        marker = {"content_sha256": "4" * 64}
        bindings = self.helper.ValidatedStageBindings(
            {"account": "exact"},
            {"ownership": "exact"},
            marker,
        )
        events: list[str] = []

        def wait(_uid: int) -> None:
            events.append("wait")
            if events.count("wait") == 1:
                raise self.helper.ProbeError(
                    "disposable-user-pid1-parented-processes-remain"
                )

        with (
            mock.patch.object(
                self.helper,
                "_require_durable_user_domain_reset_authorization",
                return_value=marker,
            ),
            mock.patch.object(
                self.helper,
                "_wait_for_no_uid_processes",
                side_effect=wait,
            ),
            mock.patch.object(
                self.helper,
                "_write_user_domain_reset_authorization",
            ) as arm,
            mock.patch.object(
                self.helper,
                "_run_user_domain_reset",
                side_effect=lambda _uid: events.append("reset"),
            ) as reset,
            mock.patch.object(
                self.helper,
                "_require_stable_no_uid_processes",
                side_effect=lambda _uid: events.append("stable-zero"),
            ),
            mock.patch.object(
                self.helper,
                "_validate_reset_bindings_and_resources",
            ),
        ):
            evidence = self.helper._quiesce_disposable_user(
                plan,
                state,
                bindings,
                eligible_context()["GITHUB_SHA"],
                eligible_context(),
                allow_create=False,
            )
        arm.assert_not_called()
        reset.assert_called_once_with(plan.account.uid)
        self.assertEqual(
            events,
            ["wait", "reset", "wait", "stable-zero"],
        )
        self.assertEqual(evidence["authorization_sha256"], "4" * 64)

    def test_cleanup_reset_revalidates_resources_before_bootout(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        marker = {"content_sha256": "4" * 64}
        bindings = self.helper.ValidatedStageBindings(
            {"account": "exact"},
            {"ownership": "exact"},
            marker,
        )
        with (
            mock.patch.object(
                self.helper,
                "_require_durable_user_domain_reset_authorization",
                return_value=marker,
            ),
            mock.patch.object(
                self.helper,
                "_validate_reset_bindings_and_resources",
                side_effect=[None, self.helper.ProbeError("account-record-drift")],
            ) as validate,
            mock.patch.object(
                self.helper,
                "_wait_for_no_uid_processes",
                side_effect=self.helper.ProbeError(
                    "disposable-user-pid1-parented-processes-remain"
                ),
            ) as wait,
            mock.patch.object(
                self.helper,
                "_run_user_domain_reset",
            ) as reset,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._quiesce_disposable_user(
                plan,
                state,
                bindings,
                eligible_context()["GITHUB_SHA"],
                eligible_context(),
                allow_create=False,
            )
        self.assertEqual(raised.exception.code, "account-record-drift")
        self.assertEqual(validate.call_count, 2)
        wait.assert_called_once_with(plan.account.uid)
        reset.assert_not_called()

    def test_cleanup_recovers_armed_reset_without_success_lifecycle_evidence(
        self,
    ) -> None:
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        authorization = eligible_context()["GITHUB_SHA"]
        for lifecycle_state in ("failed", "missing"):
            for account_present, home_present, journal_preexisting in (
                (True, True, False),
                (False, True, True),
                (False, False, True),
            ):
                with (
                    self.subTest(
                        lifecycle_state=lifecycle_state,
                        account_present=account_present,
                        home_present=home_present,
                        journal_preexisting=journal_preexisting,
                    ),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    stage = root / "stage"
                    stage.mkdir()
                    stage_names = [
                        "helper.py",
                        "job.plist",
                        "state.json",
                        "account.json",
                        "ownership.json",
                        "domain-reset.json",
                    ]
                    if journal_preexisting:
                        stage_names.append("home-cleanup.json")
                    for name in stage_names:
                        (stage / name).write_bytes(f"exact-{name}".encode())
                    artifact = root / "artifact"
                    artifact.mkdir()
                    plan = self.lifecycle_plan(stage)
                    state = self.lifecycle_state(plan)
                    account = self.helper._account_binding_document(
                        plan,
                        state,
                        generated_uid,
                    )
                    ownership = self.helper._launchd_ownership_document(plan, state)
                    home_identity = {
                        "home_device": 1,
                        "home_inode": 2,
                        "probe_device": 1,
                        "probe_inode": 3,
                    }
                    marker = {
                        "content_sha256": "4" * 64,
                        "home_identity": home_identity,
                    }
                    journal = {
                        "content_sha256": "5" * 64,
                        "disposition": "armed",
                        "account": {
                            "name": plan.account.name,
                            "uid": plan.account.uid,
                            "gid": plan.account.gid,
                            "home": str(plan.account.home),
                        },
                        "home_identity": home_identity,
                    }
                    bindings = self.helper.ValidatedStageBindings(
                        account,
                        ownership,
                        marker,
                        journal if journal_preexisting else None,
                    )
                    if lifecycle_state == "failed":
                        lifecycle = self.helper._document_with_digest(
                            {
                                "schema_version": 2,
                                "contract": ("task-witness-macos-launchd-lifecycle-v2"),
                                "candidate_sha1": FROZEN_CANDIDATE_SHA,
                                "label": plan.label,
                                "kickstart_pid": 123,
                                "probe_disposition": "probe-error",
                                "disposition": "probe-error",
                                "error": {
                                    "code": "launchd-user-domain-bootout-nonzero"
                                },
                            }
                        )
                        (artifact / "lifecycle.json").write_bytes(
                            self.helper.canonical_bytes(lifecycle)
                        )
                    cleanup_writes: list[tuple[Path, bytes, int]] = []

                    def write_cleanup(
                        path: Path,
                        raw: bytes,
                        mode: int,
                        writes: list[tuple[Path, bytes, int]] = cleanup_writes,
                    ) -> None:
                        writes.append((path, raw, mode))

                    events: list[str] = []

                    def wait_for_processes(
                        _uid: int,
                        observed: list[str] = events,
                    ) -> None:
                        observed.append("wait")
                        if observed.count("wait") == 1:
                            raise self.helper.ProbeError(
                                "disposable-user-pid1-parented-processes-remain"
                            )

                    def validate_reset(
                        *_args: object,
                        observed: list[str] = events,
                        expected_account: bool = account_present,
                        expected_home: bool = home_present,
                        **kwargs: object,
                    ) -> tuple[bool, bool]:
                        observed.append(f"binding:{kwargs['home_drift_phase']}")
                        return expected_account, expected_home

                    def write_home_authorization(
                        *_args: object,
                        observed: list[str] = events,
                        expected_stage: Path = stage,
                        expected_journal: dict = journal,
                        **_kwargs: object,
                    ) -> dict:
                        observed.append("arm-home")
                        (expected_stage / "home-cleanup.json").write_bytes(b"exact")
                        return expected_journal

                    account_presence = [True, False] if account_present else [False]
                    with ExitStack() as stack:
                        stack.enter_context(
                            mock.patch.dict(
                                os.environ,
                                eligible_context(),
                                clear=True,
                            )
                        )
                        for name in (
                            "_normalized_context",
                            "_validate_lifecycle_arguments",
                            "_reconcile_owned_launchd_job",
                            "_validate_exact_disposable_home",
                            "launchd_artifact_payloads",
                        ):
                            stack.enter_context(mock.patch.object(self.helper, name))
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_cleanup_helper_only_stage_before_state",
                                return_value=False,
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_load_lifecycle_state",
                                return_value=(plan, state),
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_validate_exact_stage",
                                return_value=bindings,
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_require_durable_user_domain_reset_authorization",
                                return_value=marker,
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_require_durable_home_cleanup_authorization",
                                return_value=journal,
                            )
                        )
                        arm_home = stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_write_home_cleanup_authorization",
                                side_effect=write_home_authorization,
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_account_exists",
                                side_effect=account_presence,
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_path_exists_no_follow",
                                return_value=home_present,
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_read_system_generated_uid",
                                return_value=generated_uid,
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_wait_for_no_uid_processes",
                                side_effect=wait_for_processes,
                            )
                        )
                        arm = stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_write_user_domain_reset_authorization",
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_run_user_domain_reset",
                                side_effect=lambda _uid, observed=events: (
                                    observed.append("reset")
                                ),
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_require_stable_no_uid_processes",
                                side_effect=lambda _uid, observed=events: (
                                    observed.append("stable-zero")
                                ),
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_validate_reset_bindings_and_resources",
                                side_effect=validate_reset,
                            )
                        )
                        process_scan = stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_require_no_uid_processes",
                            )
                        )
                        command = stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_require_command_success",
                                return_value="",
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_list_accounts",
                                return_value={},
                            )
                        )
                        read_home = stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_validated_marker_bound_disposable_home",
                                return_value=(
                                    home_present,
                                    home_present,
                                    tuple(sorted(self.helper.LAUNCHD_CHILD_FILES))
                                    if home_present
                                    else (),
                                ),
                            )
                        )
                        read_home_identity = stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_exact_disposable_home_identity",
                                return_value=home_identity,
                            )
                        )
                        remove_home = stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_remove_marker_bound_disposable_home",
                                side_effect=lambda *_args, observed=events: (
                                    observed.append("remove-home")
                                ),
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_write_root_file",
                                side_effect=write_cleanup,
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_validate_precleanup_artifact",
                                side_effect=(
                                    None
                                    if lifecycle_state == "failed"
                                    else self.helper.ProbeError(
                                        "launchd-artifact-precleanup-drift"
                                    )
                                ),
                            )
                        )
                        stack.enter_context(
                            mock.patch.object(
                                self.helper,
                                "_metadata_matches",
                                return_value=False,
                            )
                        )
                        stack.enter_context(mock.patch.object(self.helper.os, "chown"))
                        status = self.helper.cleanup_launchd_user_lifecycle(
                            stage_root=stage,
                            artifact_root=artifact,
                            expected_helper_sha256="1" * 64,
                            runner_uid=501,
                            runner_gid=20,
                            user_domain_reset_authorization=authorization,
                        )
                    self.assertEqual(
                        status,
                        0 if lifecycle_state == "failed" else 2,
                    )
                    self.assertFalse(stage.exists())
                    arm.assert_not_called()
                    self.assertEqual(
                        arm_home.call_count,
                        int(not journal_preexisting),
                    )
                    expected_events = [
                        "binding:marker-replay",
                        "wait",
                        "binding:pre-reset",
                        "reset",
                        "wait",
                        "stable-zero",
                        "binding:post-reset",
                        "binding:pre-account-delete",
                    ]
                    if account_present:
                        expected_events.append("binding:pre-account-delete")
                    expected_events.append("stable-zero")
                    if not journal_preexisting:
                        expected_events.append("arm-home")
                    expected_events.append("stable-zero")
                    if home_present:
                        expected_events.append("remove-home")
                    self.assertEqual(events, expected_events)
                    self.assertEqual(
                        command.call_count,
                        int(account_present),
                    )
                    self.assertEqual(
                        process_scan.call_count,
                        1 + int(account_present),
                    )
                    self.assertEqual(
                        [
                            call.kwargs["diagnostic_phase"]
                            for call in read_home.call_args_list
                        ],
                        (
                            ["cleanup-entry", "pre-home-removal"]
                            if home_present and journal_preexisting
                            else ["pre-home-removal"]
                        ),
                    )
                    if home_present and not journal_preexisting:
                        read_home_identity.assert_called_once_with(
                            plan.account,
                            diagnostic_phase="pre-journal",
                        )
                    else:
                        read_home_identity.assert_not_called()
                    self.assertEqual(remove_home.call_count, int(home_present))
                    self.assertEqual(
                        len(cleanup_writes),
                        int(lifecycle_state == "failed"),
                    )
                    if lifecycle_state == "missing":
                        continue
                    cleanup_path, cleanup_raw, cleanup_mode = cleanup_writes[0]
                    self.assertEqual(cleanup_path, artifact / "cleanup.json")
                    self.assertEqual(cleanup_mode, 0o600)
                    cleanup = json.loads(cleanup_raw.decode("utf-8"))
                    self.assertEqual(cleanup["disposition"], "cleaned")
                    self.assertEqual(
                        cleanup["domain_reset"],
                        {
                            "authorization_sha256": "4" * 64,
                            "capability": (
                                "github-hosted-ephemeral-user-domain-reset-v1"
                            ),
                            "disposition": "recovered-to-stable-zero",
                            "precondition": (
                                "disposable-user-pid1-parented-processes-remain"
                            ),
                        },
                    )

    def test_cleanup_propagates_bounded_quarantine_evidence_from_inventory(
        self,
    ) -> None:
        primary = "home-library-unsafe-entry"
        secondary = (
            "home-library-unsafe-entry-file-xattr-"
            "journal-inventory-preferences-apple-quarantine-stable-bounded"
        )
        evidence_type = getattr(
            self.helper,
            "_HomeLibraryQuarantineEvidence",
            None,
        )
        self.assertIsNotNone(evidence_type)
        assert evidence_type is not None
        evidence = evidence_type(28, "1" * 64)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            artifact = root / "artifact"
            home = root / "home"
            library = home / "Library"
            preferences = library / "Preferences"
            stage.mkdir()
            artifact.mkdir()
            preferences.mkdir(parents=True)
            home.chmod(0o700)
            canary = preferences / "private-hosted-inventory-canary"
            canary.write_bytes(b"private-hosted-value-canary")
            account = self.helper.DisposableAccount(
                name="twq-0123456789ab",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=home,
            )
            plan = self.helper.LaunchdPlan(
                account=account,
                label="io.nisavid.task-witness.macos-probe.0123456789ab",
                stage_root=stage,
                helper=stage / "helper.py",
                plist=stage / "job.plist",
            )
            state = self.lifecycle_state(plan)
            binding = self.helper._account_binding_document(
                plan,
                state,
                "01234567-89AB-4DEF-8123-456789ABCDEF",
            )
            bindings = self.helper.ValidatedStageBindings(
                binding,
                {"ownership": "exact"},
            )
            identity = {
                "home_device": home.lstat().st_dev,
                "home_inode": home.lstat().st_ino,
                "probe_device": home.lstat().st_dev,
                "probe_inode": home.lstat().st_ino + 1,
            }
            payloads: list[tuple[Path, bytes, int]] = []
            xattr_contexts: list[tuple[int, object, object]] = []

            def exists(path: Path) -> bool:
                return path in {home, library}

            def record(path: Path, raw: bytes, mode: int) -> None:
                payloads.append((path, raw, mode))

            def observe_xattrs(
                descriptor: int,
                **context: object,
            ):
                xattr_contexts.append(
                    (
                        os.fstat(descriptor).st_ino,
                        context.get("diagnostic_phase"),
                        context.get("diagnostic_path_family"),
                    )
                )
                return evidence

            stderr = io.StringIO()
            with ExitStack() as stack:
                stack.enter_context(
                    mock.patch.dict(os.environ, eligible_context(), clear=True)
                )
                for name in (
                    "_normalized_context",
                    "_validate_lifecycle_arguments",
                    "_reconcile_owned_launchd_job",
                    "_validate_exact_disposable_home",
                    "_quiesce_disposable_user",
                    "_require_no_uid_processes",
                    "_require_stable_no_uid_processes",
                ):
                    stack.enter_context(mock.patch.object(self.helper, name))
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_cleanup_helper_only_stage_before_state",
                        return_value=False,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_load_lifecycle_state",
                        return_value=(plan, state),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_validate_exact_stage",
                        return_value=bindings,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_account_exists",
                        return_value=False,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_path_exists_no_follow",
                        side_effect=exists,
                    )
                )
                stack.enter_context(
                    mock.patch.object(self.helper, "_list_accounts", return_value={})
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_exact_disposable_home_identity",
                        return_value=identity,
                    )
                )
                bound_home = stack.enter_context(
                    mock.patch.object(self.helper, "_require_bound_home_path")
                )
                inventory = stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_bounded_library_inventory",
                        wraps=self.helper._bounded_library_inventory,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_require_bound_home_descriptor",
                        return_value=home.lstat(),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_darwin_fstatfs_identity",
                        return_value=(1, 2, "apfs"),
                    )
                )
                stack.enter_context(
                    mock.patch.object(self.helper, "_require_no_extended_acl")
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_home_library_file_xattr_state",
                        side_effect=observe_xattrs,
                    )
                )
                publish = stack.enter_context(
                    mock.patch.object(self.helper, "_write_stage_create_new")
                )
                rename = stack.enter_context(
                    mock.patch.object(self.helper, "_renameat_exclusive")
                )
                unlink = stack.enter_context(
                    mock.patch.object(self.helper.os, "unlink")
                )
                rmdir = stack.enter_context(mock.patch.object(self.helper.os, "rmdir"))
                path_unlink = stack.enter_context(mock.patch.object(Path, "unlink"))
                path_rmdir = stack.enter_context(mock.patch.object(Path, "rmdir"))
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        side_effect=lambda path, **_kwargs: path == artifact,
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        self.helper,
                        "_write_root_file",
                        side_effect=record,
                    )
                )
                stack.enter_context(redirect_stderr(stderr))
                status = self.helper.cleanup_launchd_user_lifecycle(
                    stage_root=stage,
                    artifact_root=artifact,
                    expected_helper_sha256="1" * 64,
                    runner_uid=501,
                    runner_gid=20,
                )

            self.assertEqual(status, 2)
            self.assertEqual(
                stderr.getvalue(),
                (
                    "task-witness macOS launchd-user cleanup: "
                    f"{primary}\n"
                    "task-witness macOS launchd-user cleanup secondary: "
                    f"{secondary}\n"
                ),
            )
            bound_home.assert_called_once_with(account, identity)
            inventory.assert_called_once_with(account)
            self.assertEqual(
                xattr_contexts,
                [
                    (
                        canary.lstat().st_ino,
                        "journal-inventory",
                        "preferences",
                    ),
                    (
                        canary.lstat().st_ino,
                        "journal-inventory",
                        "preferences",
                    ),
                ],
            )
            publish.assert_not_called()
            rename.assert_not_called()
            unlink.assert_not_called()
            rmdir.assert_not_called()
            path_unlink.assert_not_called()
            path_rmdir.assert_not_called()
            self.assertTrue(stage.is_dir())
            self.assertTrue(home.is_dir())
            self.assertFalse((stage / "home-cleanup.json").exists())
            self.assertTrue(canary.is_file())
            self.assertEqual(len(payloads), 1)
            cleanup_path, cleanup_raw, cleanup_mode = payloads[0]
            self.assertEqual(cleanup_path, artifact / "cleanup.json")
            self.assertEqual(cleanup_mode, 0o600)
            cleanup = json.loads(cleanup_raw)
            self.assertEqual(cleanup["disposition"], "preserved-on-drift")
            self.assertEqual(
                cleanup["error"],
                {"code": primary, "secondary_code": secondary},
            )
            self.assertNotIn("private", stderr.getvalue())
            self.assertNotIn("private", cleanup_raw.decode("utf-8"))
            self.assertNotIn("value_length", cleanup_raw.decode("utf-8"))
            self.assertNotIn("value_sha256", cleanup_raw.decode("utf-8"))

    def test_reset_binding_validation_recovers_partial_cleanup_states(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        account = self.helper._account_binding_document(plan, state, generated_uid)
        ownership = self.helper._launchd_ownership_document(plan, state)
        identity = {
            "home_device": 1,
            "home_inode": 2,
            "probe_device": 1,
            "probe_inode": 3,
        }
        marker = {"content_sha256": "4" * 64, "home_identity": identity}
        bindings = self.helper.ValidatedStageBindings(account, ownership, marker)
        exact_record = {
            "AuthenticationAuthority": [";DisabledUser;"],
            "GeneratedUID": [generated_uid],
            "IsHidden": ["1"],
            "NFSHomeDirectory": [str(plan.account.home)],
            "Password": ["*"],
            "PrimaryGroupID": [str(plan.account.gid)],
            "UniqueID": [str(plan.account.uid)],
            "UserShell": ["/usr/bin/false"],
        }
        for account_present, home_state, home_cleanup in (
            (True, "complete", None),
            (False, "complete", None),
            (False, "partial", {"journal": "exact"}),
            (False, "absent", {"journal": "exact"}),
        ):
            home_present = home_state != "absent"
            observed_files = (
                tuple(sorted(self.helper.LAUNCHD_CHILD_FILES))
                if home_state == "complete"
                else ("probe.stdout",)
                if home_state == "partial"
                else ()
            )
            observed_bindings = bindings._replace(home_cleanup=home_cleanup)
            with (
                self.subTest(
                    account_present=account_present,
                    home_state=home_state,
                ),
                mock.patch.object(self.helper, "_require_launchd_absent"),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    return_value=observed_bindings,
                ),
                mock.patch.object(
                    self.helper,
                    "_account_exists",
                    return_value=account_present,
                ),
                mock.patch.object(
                    self.helper,
                    "_list_accounts",
                    return_value=(
                        {plan.account.name: plan.account.uid} if account_present else {}
                    ),
                ),
                mock.patch.object(
                    self.helper,
                    "_account_record",
                    return_value=exact_record,
                ) as read_record,
                mock.patch.object(
                    self.helper,
                    "_validated_marker_bound_disposable_home",
                    return_value=(
                        home_present,
                        home_present,
                        observed_files,
                    ),
                ) as read_home,
            ):
                self.assertEqual(
                    self.helper._validate_reset_bindings_and_resources(
                        plan,
                        state,
                        marker,
                        eligible_context()["GITHUB_SHA"],
                        eligible_context(),
                        home_cleanup,
                        home_drift_phase="marker-replay",
                    ),
                    (account_present, home_present),
                )
            self.assertEqual(read_record.call_count, int(account_present))
            read_home.assert_called_once_with(
                plan.account,
                identity,
                diagnostic_phase="marker-replay",
            )

        with (
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=bindings,
            ),
            mock.patch.object(
                self.helper,
                "_account_exists",
                return_value=False,
            ),
            mock.patch.object(
                self.helper,
                "_list_accounts",
                return_value={},
            ),
            mock.patch.object(
                self.helper,
                "_validated_marker_bound_disposable_home",
                return_value=(True, True, ("probe.stdout",)),
            ),
            self.assertRaises(self.helper.ProbeError) as unjournaled,
        ):
            self.helper._validate_reset_bindings_and_resources(
                plan,
                state,
                marker,
                eligible_context()["GITHUB_SHA"],
                eligible_context(),
                home_drift_phase="marker-replay",
            )
        self.assertEqual(
            unjournaled.exception.code,
            "home-cleanup-marker-replay-home-state-drift",
        )

        with (
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=bindings,
            ),
            mock.patch.object(
                self.helper,
                "_account_exists",
                return_value=False,
            ),
            mock.patch.object(
                self.helper,
                "_list_accounts",
                return_value={"foreign": plan.account.uid},
            ),
            self.assertRaises(self.helper.ProbeError) as reused,
        ):
            self.helper._validate_reset_bindings_and_resources(
                plan,
                state,
                marker,
                eligible_context()["GITHUB_SHA"],
                eligible_context(),
                home_drift_phase="marker-replay",
            )
        self.assertEqual(reused.exception.code, "account-record-drift")

        with (
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=bindings,
            ),
            mock.patch.object(
                self.helper,
                "_account_exists",
                return_value=True,
            ),
            mock.patch.object(
                self.helper,
                "_list_accounts",
                return_value={plan.account.name: plan.account.uid},
            ),
            mock.patch.object(
                self.helper,
                "_account_record",
                return_value=exact_record,
            ),
            mock.patch.object(
                self.helper,
                "_validated_marker_bound_disposable_home",
                side_effect=self.helper.ProbeError(
                    "home-cleanup-marker-replay-home-identity-drift"
                ),
            ),
            self.assertRaises(self.helper.ProbeError) as replaced,
        ):
            self.helper._validate_reset_bindings_and_resources(
                plan,
                state,
                marker,
                eligible_context()["GITHUB_SHA"],
                eligible_context(),
                home_drift_phase="marker-replay",
            )
        self.assertEqual(
            replaced.exception.code,
            "home-cleanup-marker-replay-home-identity-drift",
        )

    def test_reset_rejects_foreign_uid_collision_before_bootout(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        account = self.helper._account_binding_document(plan, state, generated_uid)
        ownership = self.helper._launchd_ownership_document(plan, state)
        identity = {
            "home_device": 1,
            "home_inode": 2,
            "probe_device": 1,
            "probe_inode": 3,
        }
        marker = {"content_sha256": "4" * 64, "home_identity": identity}
        bindings = self.helper.ValidatedStageBindings(account, ownership, marker)
        with (
            mock.patch.object(
                self.helper,
                "_require_durable_user_domain_reset_authorization",
                return_value=marker,
            ),
            mock.patch.object(self.helper, "_require_launchd_absent"),
            mock.patch.object(
                self.helper,
                "_validate_exact_stage",
                return_value=bindings,
            ),
            mock.patch.object(
                self.helper,
                "_account_exists",
                return_value=True,
            ),
            mock.patch.object(
                self.helper,
                "_list_accounts",
                return_value={
                    plan.account.name: plan.account.uid,
                    "foreign": plan.account.uid,
                },
            ),
            mock.patch.object(
                self.helper,
                "_read_system_generated_uid",
                return_value=generated_uid,
            ),
            mock.patch.object(
                self.helper,
                "_path_exists_no_follow",
                return_value=True,
            ),
            mock.patch.object(
                self.helper,
                "_exact_disposable_home_identity",
                return_value=identity,
            ),
            mock.patch.object(
                self.helper,
                "_wait_for_no_uid_processes",
                side_effect=self.helper.ProbeError(
                    "disposable-user-pid1-parented-processes-remain"
                ),
            ),
            mock.patch.object(self.helper, "_run_user_domain_reset") as reset,
            self.assertRaises(self.helper.ProbeError) as raised,
        ):
            self.helper._quiesce_disposable_user(
                plan,
                state,
                bindings,
                eligible_context()["GITHUB_SHA"],
                eligible_context(),
                allow_create=False,
            )
        self.assertEqual(raised.exception.code, "account-record-drift")
        reset.assert_not_called()

    def test_reset_rejects_mutable_account_drift_before_bootout(self) -> None:
        plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
        state = self.lifecycle_state(plan)
        generated_uid = "01234567-89AB-4DEF-8123-456789ABCDEF"
        account = self.helper._account_binding_document(plan, state, generated_uid)
        ownership = self.helper._launchd_ownership_document(plan, state)
        identity = {
            "home_device": 1,
            "home_inode": 2,
            "probe_device": 1,
            "probe_inode": 3,
        }
        marker = {"content_sha256": "4" * 64, "home_identity": identity}
        bindings = self.helper.ValidatedStageBindings(account, ownership, marker)
        for code in ("account-record-home-drift", "account-record-gid-drift"):
            with (
                self.subTest(code=code),
                mock.patch.object(
                    self.helper,
                    "_require_durable_user_domain_reset_authorization",
                    return_value=marker,
                ),
                mock.patch.object(self.helper, "_require_launchd_absent"),
                mock.patch.object(
                    self.helper,
                    "_validate_exact_stage",
                    return_value=bindings,
                ),
                mock.patch.object(
                    self.helper,
                    "_account_exists",
                    return_value=True,
                ),
                mock.patch.object(
                    self.helper,
                    "_list_accounts",
                    return_value={plan.account.name: plan.account.uid},
                ),
                mock.patch.object(
                    self.helper,
                    "_account_record",
                    side_effect=self.helper.ProbeError(code),
                ) as account_record,
                mock.patch.object(
                    self.helper,
                    "_read_system_generated_uid",
                    return_value=generated_uid,
                ),
                mock.patch.object(
                    self.helper,
                    "_path_exists_no_follow",
                    return_value=True,
                ),
                mock.patch.object(
                    self.helper,
                    "_exact_disposable_home_identity",
                    return_value=identity,
                ),
                mock.patch.object(
                    self.helper,
                    "_wait_for_no_uid_processes",
                    side_effect=self.helper.ProbeError(
                        "disposable-user-pid1-parented-processes-remain"
                    ),
                ),
                mock.patch.object(self.helper, "_run_user_domain_reset") as reset,
                self.assertRaises(self.helper.ProbeError) as raised,
            ):
                self.helper._quiesce_disposable_user(
                    plan,
                    state,
                    bindings,
                    eligible_context()["GITHUB_SHA"],
                    eligible_context(),
                    allow_create=False,
                )
            self.assertEqual(raised.exception.code, code)
            account_record.assert_called_once_with(plan.account)
            reset.assert_not_called()

    def test_cleanup_reset_evidence_requires_exact_eligible_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory)
            plan = self.lifecycle_plan(Path("/private/var/tmp/stage"))
            state = self.lifecycle_state(plan)
            marker = {"content_sha256": "4" * 64}
            performed = self.helper._domain_reset_evidence(marker)
            lifecycle = self.helper._document_with_digest(
                {
                    "schema_version": 2,
                    "contract": "task-witness-macos-launchd-lifecycle-v2",
                    "candidate_sha1": FROZEN_CANDIDATE_SHA,
                    "label": plan.label,
                    "kickstart_pid": 123,
                    "probe_disposition": "launchd-user-eligible",
                    "disposition": "launchd-user-eligible",
                    "binding": {
                        "ownership_marker": state["ownership_marker"],
                    },
                    "domain_reset": performed,
                }
            )
            (artifact / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(lifecycle)
            )
            self.assertEqual(
                self.helper._cleanup_domain_reset_evidence(
                    artifact,
                    plan,
                    state,
                    marker,
                ),
                performed,
            )
            self.assertEqual(
                self.helper._cleanup_domain_reset_evidence(
                    artifact,
                    plan,
                    state,
                    marker,
                    force_recovered=True,
                )["disposition"],
                "recovered-to-stable-zero",
            )
            lifecycle["disposition"] = "probe-error"
            (artifact / "lifecycle.json").write_bytes(
                self.helper.canonical_bytes(
                    self.helper._document_with_digest(
                        {
                            key: value
                            for key, value in lifecycle.items()
                            if key != "content_sha256"
                        }
                    )
                )
            )
            self.assertEqual(
                self.helper._cleanup_domain_reset_evidence(
                    artifact,
                    plan,
                    state,
                    marker,
                )["disposition"],
                "recovered-to-stable-zero",
            )


if __name__ == "__main__":
    unittest.main()
