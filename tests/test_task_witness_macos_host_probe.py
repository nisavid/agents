from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        self.assertEqual(workflow.count(helper_blob_check), 1)
        self.assertEqual(workflow.count(helper_execution), 1)
        self.assertLess(
            workflow.index(helper_blob_check),
            workflow.index(test_command),
        )
        self.assertLess(workflow.index(test_command), workflow.index(helper_execution))

        upload = "      - name: Retain bounded macOS host probe diagnostics\n"
        terminal = "      - name: Require an eligible direct session\n"
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
        terminal_marker = "      - name: Require an eligible direct session\n"

        capture = workflow[workflow.index(capture_marker) : workflow.index(seal_marker)]
        seal = workflow[workflow.index(seal_marker) : workflow.index(upload_marker)]
        upload = workflow[
            workflow.index(upload_marker) : workflow.index(terminal_marker)
        ]
        terminal = workflow[workflow.index(terminal_marker) :]

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
        self.assertIn("        if: always()\n", terminal)
        self.assertIn(
            '          /bin/test "$(/bin/cat "$probe_root/probe.status")" = 0\n',
            terminal,
        )

    def test_workflow_uses_the_portable_macos_test_binary(self) -> None:
        workflow = WORKFLOW.read_text()

        self.assertNotIn("/usr/bin/test", workflow)
        self.assertEqual(workflow.count("          /bin/test "), 6)

    def test_linux_workflow_cannot_run_on_the_macos_probe_branch(self) -> None:
        workflow = LINUX_WORKFLOW.read_text()
        self.assertIn(f"      - {LINUX_HARNESS_BRANCH}\n", workflow)
        self.assertNotIn(MACOS_HARNESS_BRANCH, workflow)
        branch_block = f"    branches:\n      - {LINUX_HARNESS_BRANCH}\n    paths:\n"
        self.assertEqual(workflow.count(branch_block), 1)


if __name__ == "__main__":
    unittest.main()
