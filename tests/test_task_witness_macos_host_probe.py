from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
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
            generated_uid=str(
                self.helper.uuid.uuid5(self.helper.uuid.NAMESPACE_URL, label)
            ).upper(),
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
                    "generated_uid": plan.account.generated_uid,
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

    def launchctl_job(self, plan: object, state: dict[str, object]) -> str:
        arguments = [
            "/usr/bin/python3",
            "-I",
            "-B",
            str(plan.helper),
            "probe-launchd-user",
            "--candidate-sha",
            FROZEN_CANDIDATE_SHA,
            "--output",
            str(plan.account.home / "launchd-probe/probe.json"),
            "--status-output",
            str(plan.account.home / "launchd-probe/probe.status"),
        ]
        argument_lines = "\n".join(f"\t\t{item}" for item in arguments)
        plist = self.helper.build_launchd_user_plist(
            label=plan.label,
            user=plan.account.name,
            home=plan.account.home,
            helper=plan.helper,
            candidate_sha=FROZEN_CANDIDATE_SHA,
            environment=self.launchd_context(),
            ownership_marker=state["ownership_marker"],
        )
        environment = plist["EnvironmentVariables"]
        environment_lines = "\n".join(
            f"\t\t{name} => {value}" for name, value in sorted(environment.items())
        )
        return (
            f"system/{plan.label} = {{\n"
            "\tactive count = 0\n"
            f"\tpath = {plan.plist}\n"
            "\tstate = not running\n\n"
            "\tprogram = /usr/bin/python3\n"
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
        child_environment["XPC_SERVICE_NAME"] = context["TASK_WITNESS_LAUNCHD_LABEL"]

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
        child_environment["XPC_SERVICE_NAME"] = context["TASK_WITNESS_LAUNCHD_LABEL"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "launchd-probe"
            root.mkdir()
            output = root / "probe.json"
            status_output = root / "probe.status"
            with (
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
        with_xpc = {**expected, "XPC_SERVICE_NAME": label}
        self.helper._require_exact_launchd_child_environment(
            with_xpc,
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
            "wrong-xpc": {**expected, "XPC_SERVICE_NAME": "foreign"},
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

        self.assertEqual(plist["Program"], "/usr/bin/python3")
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
            plist["ProgramArguments"][:4],
            [
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

    def test_launchd_job_binding_requires_the_exact_child_environment(self) -> None:
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
            ["/usr/bin/dscl", ".", "-list", "/Users", "UniqueID"]
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
            generated_uid="01234567-89AB-CDEF-0123-456789ABCDEF",
        )
        record = {
            "AuthenticationAuthority": [";DisabledUser;"],
            "GeneratedUID": [expected.generated_uid],
            "IsHidden": ["1"],
            "NFSHomeDirectory": [str(expected.home)],
            "Password": ["*"],
            "PrimaryGroupID": [str(expected.gid)],
            "UniqueID": [str(expected.uid)],
            "UserShell": ["/usr/bin/false"],
        }
        self.helper.require_exact_account_record(record, expected)
        changed = dict(record)
        changed["GeneratedUID"] = ["AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"]
        with self.assertRaisesRegex(self.helper.ProbeError, "account-record-drift"):
            self.helper.require_exact_account_record(changed, expected)
        changed = dict(record)
        changed["UniqueID"] = ["-2"]
        with self.assertRaisesRegex(self.helper.ProbeError, "account-record-drift"):
            self.helper.require_exact_account_record(changed, expected)

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
        lifecycle = self.helper._document_with_digest(
            {
                "schema_version": 1,
                "contract": "task-witness-macos-launchd-lifecycle-v1",
                "candidate_sha1": FROZEN_CANDIDATE_SHA,
                "label": label,
                "kickstart_pid": observations["process"]["pid"],
                "probe_disposition": "launchd-user-eligible",
                "disposition": "launchd-user-eligible",
                "binding": binding,
            }
        )
        cleanup = self.helper._document_with_digest(
            {
                "schema_version": 1,
                "contract": "task-witness-macos-launchd-cleanup-v1",
                "candidate_sha1": FROZEN_CANDIDATE_SHA,
                "account": account,
                "label": label,
                "disposition": "cleaned",
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

            path_line = f"\tpath = {plan.plist}\n"
            program_line = "\tprogram = /usr/bin/python3\n"
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
                    "schema_version": 1,
                    "contract": "task-witness-macos-launchd-cleanup-v1",
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

        def capture(*, status: int, error_code: str | None) -> dict[str, bytes]:
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

        failure_payloads = capture(status=2, error_code="lifecycle-incomplete")
        failure = json.loads(failure_payloads["lifecycle.json"].decode("utf-8"))
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

        def command(argv: list[str], **_kwargs: object) -> str:
            if argv[:3] == ["/bin/launchctl", "bootstrap", "system"]:
                return ""
            if argv[:3] == ["/bin/launchctl", "kickstart", "-p"]:
                return "4321"
            if argv == [
                "/bin/launchctl",
                "bootout",
                f"system/{plan.label}",
            ]:
                return ""
            raise AssertionError(argv)

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
                return_value=None,
            ),
            mock.patch.object(self.helper, "_create_disposable_account"),
            mock.patch.object(self.helper, "_create_disposable_home"),
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
                side_effect=[raw_loaded, raw_loaded, None],
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
            mock.patch.object(self.helper, "_require_no_uid_processes"),
            mock.patch.object(self.helper, "_write_lifecycle_artifact") as write,
        ):
            status = self.helper.run_launchd_user_lifecycle(
                stage_root=plan.stage_root,
                artifact_root=Path("/private/tmp/artifact"),
                candidate_sha=FROZEN_CANDIDATE_SHA,
                runner_uid=501,
                runner_gid=20,
            )

        self.assertEqual(status, 0)
        write.assert_called_once()
        self.assertEqual(write.call_args.kwargs["binding"], expected_binding)
        self.assertEqual(write.call_args.kwargs["loaded"], loaded)
        self.assertEqual(write.call_args.kwargs["terminal"], loaded)
        self.assertNotIn(secret, repr(write.call_args.kwargs))

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
                return_value=None,
            ),
            mock.patch.object(self.helper, "_create_disposable_account"),
            mock.patch.object(self.helper, "_create_disposable_home"),
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

    def test_home_cleanup_preserves_everything_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            probe = home / "launchd-probe"
            probe.mkdir(parents=True, mode=0o700)
            for name in ("probe.json", "probe.status", "probe.stderr", "probe.stdout"):
                (probe / name).write_bytes(b"value")
            (probe / "foreign").write_bytes(b"preserve")

            with self.assertRaisesRegex(self.helper.ProbeError, "home-cleanup-drift"):
                self.helper.remove_exact_disposable_home(
                    home,
                    expected_uid=os.geteuid(),
                    expected_gid=os.getegid(),
                )
            self.assertTrue(home.is_dir())
            self.assertTrue((probe / "foreign").is_file())

    def test_disabled_account_creation_uses_exact_dscl_commands_and_readback(
        self,
    ) -> None:
        account = self.helper.DisposableAccount(
            name="twq-0123456789ab",
            uid=502,
            gid=20,
            home=Path("/Users/twq-0123456789ab"),
            generated_uid="01234567-89AB-CDEF-0123-456789ABCDEF",
        )
        record = "\n".join(
            (
                "AuthenticationAuthority: ;DisabledUser;",
                f"GeneratedUID: {account.generated_uid}",
                "IsHidden: 1",
                f"NFSHomeDirectory: {account.home}",
                "Password: *",
                "PrimaryGroupID: 20",
                "UniqueID: 502",
                "UserShell: /usr/bin/false",
            )
        )
        calls: list[tuple[str, ...]] = []
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
            if call == ("/usr/bin/dscl", ".", "-list", "/Users", "UniqueID"):
                list_count += 1
                suffix = f"\n{account.name} {account.uid}" if list_count == 2 else ""
                return 0, f"root 0\nrunner 501{suffix}", ""
            if call == ("/usr/bin/dscl", ".", "-list", "/Users"):
                return 0, "root\nrunner", ""
            if call[:4] == ("/usr/bin/dscl", ".", "-read", f"/Users/{account.name}"):
                return 0, record, ""
            return 0, "", ""

        with mock.patch.object(
            self.helper,
            "_run_lifecycle_command",
            side_effect=command,
        ):
            self.helper._create_disposable_account(account)

        creates = [call for call in calls if call[2] == "-create"]
        self.assertEqual(len(creates), 9)
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
        self.assertFalse(
            any("sysadminctl" in field for call in calls for field in call)
        )

    def test_account_creation_rolls_back_its_exact_name_on_early_or_late_failure(
        self,
    ) -> None:
        account = self.helper.DisposableAccount(
            name="twq-0123456789ab",
            uid=502,
            gid=20,
            home=Path("/Users/twq-0123456789ab"),
            generated_uid="01234567-89AB-CDEF-0123-456789ABCDEF",
        )
        for failure_index in (0, 8):
            calls: list[tuple[str, ...]] = []
            record_present = False
            create_index = 0

            def command(
                argv: list[str],
                observed_calls: list[tuple[str, ...]] = calls,
                failed_at: int = failure_index,
                **_kwargs: object,
            ) -> tuple[int, str, str]:
                nonlocal record_present, create_index
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
                    current_index = create_index
                    create_index += 1
                    if current_index == failed_at:
                        return 1, "", "synthetic failure"
                    return 0, "", ""
                raise AssertionError(call)

            with (
                self.subTest(failure_index=failure_index),
                mock.patch.object(
                    self.helper,
                    "_run_lifecycle_command",
                    side_effect=command,
                ),
                self.assertRaisesRegex(
                    self.helper.ProbeError,
                    "lifecycle-command-nonzero",
                ),
            ):
                self.helper._create_disposable_account(account)

            deletes = [call for call in calls if call[2] == "-delete"]
            self.assertEqual(
                deletes,
                [
                    (
                        "/usr/bin/dscl",
                        ".",
                        "-delete",
                        f"/Users/{account.name}",
                    )
                ],
            )
            self.assertFalse(record_present)
            self.assertEqual(create_index, failure_index + 1)

    def test_home_creation_rolls_back_only_exact_empty_created_directories(
        self,
    ) -> None:
        for label in ("first-chown", "final-metadata", "foreign-content"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / "twq-0123456789ab"
                probe = home / "launchd-probe"
                account = self.helper.DisposableAccount(
                    name="twq-0123456789ab",
                    uid=502,
                    gid=20,
                    home=home,
                    generated_uid="01234567-89AB-CDEF-0123-456789ABCDEF",
                )
                chown_count = 0

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

                metadata = (
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        return_value=False,
                    )
                    if label == "final-metadata"
                    else nullcontext()
                )
                expected_error = (
                    "home-create-new-preserved"
                    if label == "foreign-content"
                    else "home-create-new"
                )
                with (
                    mock.patch.object(self.helper.os, "chown", side_effect=chown),
                    metadata,
                    self.assertRaisesRegex(self.helper.ProbeError, expected_error),
                ):
                    self.helper._create_disposable_home(account)

                if label == "foreign-content":
                    self.assertTrue(home.is_dir())
                    self.assertEqual((probe / "foreign").read_bytes(), b"preserve")
                else:
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
        command.assert_called_once_with(["/bin/launchctl", "bootout", target])

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
                mock.patch.object(self.helper, "_write_root_file") as write,
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
                owned.replace("program = /usr/bin/python3", "program = /bin/false"),
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
                observed_events: list[str] = events,
            ) -> None:
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
                    return_value=None,
                ),
                mock.patch.object(self.helper, "_create_disposable_account"),
                mock.patch.object(self.helper, "_create_disposable_home"),
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
                return_value=None,
            ),
            mock.patch.object(self.helper, "_create_disposable_account"),
            mock.patch.object(self.helper, "_create_disposable_home"),
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

                with (
                    mock.patch.dict(os.environ, context, clear=True),
                    mock.patch.object(self.helper, "__file__", str(helper)),
                    mock.patch.object(
                        self.helper, "_metadata_matches", return_value=True
                    ),
                    mock.patch.object(
                        self.helper,
                        "_list_accounts",
                        return_value={"root": 0, "runner": 501},
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
                generated_uid="01234567-89AB-CDEF-0123-456789ABCDEF",
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

            def validate_stage(*_args: object, **_kwargs: object) -> None:
                events.append("validate-stage")

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
                mock.patch.object(self.helper, "_require_no_uid_processes"),
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
            launchctl_mutation.assert_not_called()

    def test_cleanup_preserves_foreign_live_job_and_all_local_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            for name in ("helper.py", "job.plist", "state.json", "ownership.json"):
                (stage / name).write_bytes(f"exact-{name}".encode())
            artifact = root / "artifact"
            artifact.mkdir()
            (artifact / "sentinel").write_bytes(b"preserve-artifact")
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
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

            def validate_stage(*_args: object, **_kwargs: object) -> dict:
                events.append("validate-stage")
                return marker

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
            for name in ("helper.py", "job.plist", "state.json", "ownership.json"):
                (stage / name).write_bytes(f"exact-{name}".encode())
            plan = self.lifecycle_plan(stage)
            state = self.lifecycle_state(plan)
            marker = self.helper._launchd_ownership_document(plan, state)
            owned = self.launchctl_job(plan, state)
            artifact = root / "artifact"
            events: list[str] = []

            def load_state(*_args: object, **_kwargs: object) -> tuple[object, dict]:
                events.append("load-state")
                return plan, state

            def validate_stage(*_args: object, **_kwargs: object) -> dict:
                events.append("validate-stage")
                return marker

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
                ["/bin/launchctl", "bootout", f"system/{plan.label}"]
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

            with mock.patch.object(
                self.helper,
                "_metadata_matches",
                return_value=True,
            ):
                self.assertEqual(
                    self.helper.validate_prestaged_helper(stage, expected),
                    b"trusted helper\n",
                )

            for label, expected_metadata in (
                ("owner", False),
                ("mode", False),
            ):
                with (
                    self.subTest(label=label),
                    mock.patch.object(
                        self.helper,
                        "_metadata_matches",
                        return_value=expected_metadata,
                    ),
                    self.assertRaises(self.helper.ProbeError),
                ):
                    self.helper.validate_prestaged_helper(stage, expected)

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


if __name__ == "__main__":
    unittest.main()
