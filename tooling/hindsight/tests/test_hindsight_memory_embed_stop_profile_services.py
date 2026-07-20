import importlib.util
import json
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "libexec/hindsight-embed-stop-profile-services.py"


def write_control_pid(path: Path, pid: int, port: int = 7977) -> None:
    path.write_text(
        json.dumps(
            {
                "desired_state_dir": "/tmp/hindsight-desired",
                "pid": pid,
                "port": port,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="ascii",
    )


def load_helper():
    module_names = (
        "hindsight_embed",
        "hindsight_embed.daemon_embed_manager",
        "hindsight_embed_stop_profile_services",
    )
    previous = {name: sys.modules.get(name) for name in module_names}
    package = types.ModuleType("hindsight_embed")
    manager_module = types.ModuleType("hindsight_embed.daemon_embed_manager")
    manager_module.DaemonEmbedManager = type("DaemonEmbedManager", (), {})
    package.daemon_embed_manager = manager_module
    sys.modules["hindsight_embed"] = package
    sys.modules["hindsight_embed.daemon_embed_manager"] = manager_module
    spec = importlib.util.spec_from_file_location(
        "hindsight_embed_stop_profile_services", HELPER
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in previous.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class Manager:
    def __init__(self, *, busy_ports=()):
        self.killed = []
        self.busy_ports = set(busy_ports)
        self.checked_ports = []
        self.events = []

    def _kill_process(self, pid):
        self.events.append(("kill", pid))
        self.killed.append(pid)
        return True

    def _is_port_in_use(self, port):
        self.events.append(("port", port))
        self.checked_ports.append(port)
        return port in self.busy_ports

    def _find_pid_on_port(self, _port):
        return None


class StopProfileServicesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def test_listener_discovery_does_not_depend_on_caller_path(self):
        manager = Manager()
        completed = types.SimpleNamespace(returncode=0, stdout="1234\n")
        with patch.object(
            self.helper.subprocess, "run", return_value=completed
        ) as run:
            self.assertEqual(self.helper.find_pids_on_port(manager, 7878), [1234])
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/sbin/lsof", "-nP", "-tiTCP:7878", "-sTCP:LISTEN"],
        )

    def test_listener_discovery_collects_every_unique_pid(self):
        manager = Manager()
        completed = types.SimpleNamespace(
            returncode=0, stdout="1234\n5678\n1234\n"
        )
        with patch.object(self.helper.subprocess, "run", return_value=completed):
            self.assertEqual(
                self.helper.find_pids_on_port(manager, 7878), [1234, 5678]
            )

    def test_lsof_runner_falls_back_to_usr_bin(self):
        completed = types.SimpleNamespace(returncode=0, stdout="1234\n")
        with patch.object(
            self.helper.subprocess,
            "run",
            side_effect=[FileNotFoundError, completed],
        ) as run:
            self.assertIs(
                self.helper.run_lsof("-nP", "-tiTCP:7878", "-sTCP:LISTEN"),
                completed,
            )
        self.assertEqual(run.call_args_list[0].args[0][0], "/usr/sbin/lsof")
        self.assertEqual(run.call_args_list[1].args[0][0], "/usr/bin/lsof")

    def test_process_cwd_uses_usr_bin_lsof_fallback(self):
        completed = types.SimpleNamespace(
            returncode=0,
            stdout="p1234\nfcwd\nn/tmp/hindsight-control-plane/standalone\n",
        )
        with patch.object(
            self.helper.subprocess,
            "run",
            side_effect=[FileNotFoundError, completed],
        ) as run:
            self.assertEqual(
                self.helper.process_cwd(1234),
                Path("/tmp/hindsight-control-plane/standalone"),
            )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/usr/bin/lsof", "-Ffn", "-p", "1234"],
        )

    def test_open_file_identity_preserves_spaces_in_lsof_field_output(self):
        path = Path("/tmp/profile log with spaces.log")
        completed = types.SimpleNamespace(
            returncode=0,
            stdout=f"p1234\nfcwd\nn/tmp\nf7\nn{path}\n",
        )
        with patch.object(self.helper.subprocess, "run", return_value=completed) as run:
            self.assertTrue(self.helper.process_has_open_file(1234, path))
        self.assertEqual(run.call_args.args[0], ["/usr/sbin/lsof", "-Fn", "-p", "1234"])

    def test_stop_control_missing_state_directory_still_checks_control_port(self):
        manager = Manager()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(Path, "home", return_value=Path(directory)),
            patch.object(self.helper, "DaemonEmbedManager", return_value=manager),
            patch.object(
                self.helper,
                "read_control_pid",
                side_effect=AssertionError("unlocked path must not inspect PID state"),
            ),
            patch.object(
                self.helper, "find_pids_on_port", return_value=[]
            ) as find_pids,
        ):
            self.assertEqual(
                self.helper.main(
                    ["--mode", "stop-control", "--control-port", "7977"]
                ),
                0,
            )
        find_pids.assert_called_once_with(manager, 7977)

    def test_stop_control_parent_open_errors_are_stop_errors(self):
        with (
            patch.object(
                self.helper,
                "_open_control_pid_parent",
                side_effect=PermissionError("denied"),
            ),
            redirect_stderr(StringIO()) as stderr,
        ):
            self.assertEqual(
                self.helper.main(
                    ["--mode", "stop-control", "--control-port", "7977"]
                ),
                1,
            )
        self.assertIn("failed to lock control PID lifecycle", stderr.getvalue())

    def test_open_file_identity_requires_exact_name_field(self):
        with patch.object(
            self.helper.subprocess,
            "run",
            return_value=types.SimpleNamespace(
                returncode=0, stdout="p1234\nn/tmp/profile log with spaces.log.extra\n"
            ),
        ):
            self.assertFalse(
                self.helper.process_has_open_file(
                    1234, Path("/tmp/profile log with spaces.log")
                )
            )

    def test_api_ownership_requires_hindsight_signature_and_profile_log(self):
        paths = types.SimpleNamespace(log=Path("/tmp/profile.log"))
        cases = (
            (["/usr/bin/python3", "/opt/bin/hindsight-api"], True, True),
            (["/usr/bin/python3", "-m", "hindsight_api.main"], True, True),
            (["/usr/bin/python3", "/tmp/unrelated-api"], True, False),
            (["/usr/bin/python3", "/tmp/hindsight-api-helper"], True, False),
            (["/usr/bin/python3", "-m", "hindsight_api_evil.main"], True, False),
            (["/usr/bin/python3", "/opt/bin/hindsight-api"], False, False),
        )
        for argv, has_log, expected in cases:
            with self.subTest(argv=argv, has_log=has_log), patch.object(
                self.helper, "process_args", return_value=argv
            ), patch.object(
                self.helper, "process_has_open_file", return_value=has_log
            ):
                self.assertEqual(
                    self.helper.owns_hindsight_api(1234, paths), expected
                )

    def test_ui_log_fallback_still_requires_control_plane_signature(self):
        paths = types.SimpleNamespace(ui_log=Path("/tmp/profile-ui.log"))
        cases = (
            (["node", "/opt/hindsight-control-plane/server.js", "--port", "9999", "--api-url", "http://127.0.0.1:7979"], True),
            (["node", "/opt/not-hindsight-control-plane/server.js"], False),
            (["node", "/tmp/unrelated-ui"], False),
            ([], False),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv), patch.object(
                self.helper, "process_args", return_value=argv
            ), patch.object(
                self.helper, "process_has_open_file", return_value=True
            ):
                self.assertEqual(
                    self.helper.owns_hindsight_ui(
                        1234, 9999, paths, "http://127.0.0.1:7979"
                    ),
                    expected,
                )

    def test_ui_ownership_requires_one_exact_port_and_api_binding(self):
        paths = types.SimpleNamespace(ui_log=Path("/tmp/profile-ui.log"))
        valid = [
            "node", "/opt/hindsight-control-plane/server.js",
            "--port", "9999", "--api-url", "http://127.0.0.1:7979",
        ]
        invalid = (
            ["node", "/opt/hindsight-control-plane/server.js", "prefix", *valid[2:]],
            [*valid, "--port", "9999"],
            [*valid, "--api-url", "http://attacker.invalid"],
            ["node", "/opt/hindsight-control-plane/server.js", "--port=9999", "--api-url=http://127.0.0.1:7979"],
        )
        with (
            patch.object(self.helper, "process_args", return_value=valid),
            patch.object(self.helper, "process_has_open_file", return_value=True),
        ):
            self.assertTrue(self.helper.owns_hindsight_ui(
                1234, 9999, paths, "http://127.0.0.1:7979"
            ))
        for argv in invalid:
            with (
                self.subTest(argv=argv),
                patch.object(self.helper, "process_args", return_value=argv),
                patch.object(self.helper, "process_has_open_file", return_value=True),
            ):
                self.assertFalse(self.helper.owns_hindsight_ui(
                    1234, 9999, paths, "http://127.0.0.1:7979"
                ))

    def test_ui_ownership_after_next_server_title_requires_managed_cwd_and_log(self):
        paths = types.SimpleNamespace(ui_log=Path("/tmp/profile-ui.log"))
        managed_cwd = Path(
            "/tmp/node_modules/@vectorize-io/hindsight-control-plane/standalone"
        )
        cases = (
            (managed_cwd, True, True),
            (managed_cwd, False, False),
            (Path("/tmp/node_modules/unrelated/standalone"), True, False),
            (Path("/tmp/node_modules/hindsight-control-plane/server"), True, False),
            (None, True, False),
        )
        for cwd, has_log, expected in cases:
            with (
                self.subTest(cwd=cwd, has_log=has_log),
                patch.object(
                    self.helper,
                    "process_command",
                    return_value="next-server (v16.2.9)",
                ),
                patch.object(self.helper, "process_cwd", return_value=cwd),
                patch.object(
                    self.helper, "process_has_open_file", return_value=has_log
                ),
            ):
                self.assertEqual(
                    self.helper.owns_hindsight_ui(
                        1234, 9999, paths, "http://127.0.0.1:7979"
                    ),
                    expected,
                )

    def test_control_ownership_accepts_only_upstream_or_exact_managed_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            wrapper = str(
                Path(self.helper.__file__).resolve().with_name(
                    "hindsight-embed-control-server.py"
                )
            )
            cases = (
                (
                    [
                        "/usr/bin/python3",
                        "-m",
                        "hindsight_embed.control_center.server",
                        "--port",
                        "7878",
                    ],
                    True,
                ),
                (
                    [
                        "/usr/bin/python3",
                        wrapper,
                        "serve",
                        "--port",
                        "7878",
                        "--desired-state-dir",
                        str(home / "desired"),
                    ],
                    True,
                ),
                (
                    [
                        "/usr/bin/python3",
                        "/tmp/hindsight-embed-control-server.py",
                        "serve",
                        "--port",
                        "7878",
                    ],
                    False,
                ),
                (
                    [
                        "/tmp/unrelated",
                        wrapper,
                        "serve",
                        "--port",
                        "7878",
                    ],
                    False,
                ),
            )
            for argv, expected in cases:
                with (
                    self.subTest(argv=argv),
                    patch.object(Path, "home", return_value=home),
                    patch.object(self.helper, "process_args", return_value=argv),
                ):
                    self.assertEqual(
                        self.helper.owns_hindsight_control(1234, 7878),
                        expected,
                    )

    def test_control_ownership_rejects_embedded_or_duplicated_signatures(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            wrapper = str(
                Path(self.helper.__file__).resolve().with_name(
                    "hindsight-embed-control-server.py"
                )
            )
            cases = (
                ["/usr/bin/python3", "-c", "noop", "hindsight_embed.control_center.server", "--port", "7878"],
                ["/usr/bin/python3", "-m", "hindsight_embed.control_center.server", "--port", "7878", "--port", "7878"],
                ["/usr/bin/python3", wrapper, "serve", "--port", "7878"],
                ["/usr/bin/python3", wrapper, "serve", "--port", "7878", "--desired-state-dir", "relative"],
            )
            for argv in cases:
                with (
                    self.subTest(argv=argv),
                    patch.object(Path, "home", return_value=home),
                    patch.object(self.helper, "process_args", return_value=argv),
                ):
                    self.assertFalse(self.helper.owns_hindsight_control(1234, 7878))

    def test_stop_refuses_pid_reuse_after_target_discovery(self):
        manager = Manager()
        target = self.helper.Target("API", 7979, 1234, "original")
        with patch.object(
            self.helper, "stable_process_identity", return_value="replacement"
        ):
            with self.assertRaisesRegex(self.helper.StopError, "replaced API"):
                self.helper.stop_targets(manager, [target])
        self.assertEqual(manager.killed, [])

    def test_stop_preflights_every_target_before_first_signal(self):
        manager = Manager()
        targets = [
            self.helper.Target("API", 7979, 1234, "first"),
            self.helper.Target("UI", 17979, 5678, "second"),
        ]
        with patch.object(
            self.helper,
            "stable_process_identity",
            side_effect=("first", "replacement"),
        ):
            with self.assertRaisesRegex(self.helper.StopError, "replaced UI"):
                self.helper.stop_targets(manager, targets)
        self.assertEqual(manager.killed, [])

    def test_stop_revalidates_and_removes_cleanup_only_after_port_release(self):
        manager = Manager()
        with tempfile.TemporaryDirectory() as directory:
            cleanup_path = Path(directory) / "control.pid"
            write_control_pid(cleanup_path, 1234)
            target = self.helper.Target(
                "control", 7977, 1234, "stable", cleanup_path
            )
            with patch.object(
                self.helper, "stable_process_identity", return_value="stable"
            ):
                self.helper.stop_targets(manager, [target])
            self.assertEqual(manager.killed, [1234])
            self.assertFalse(cleanup_path.exists())
            self.assertEqual(set(manager.checked_ports), {7977})
            self.assertLess(
                manager.events.index(("kill", 1234)),
                manager.events.index(("port", 7977)),
            )

    def test_stop_checks_configured_port_instead_of_default(self):
        manager = Manager()
        target = self.helper.Target("control", 18777, 1234, "stable")
        with patch.object(
            self.helper, "stable_process_identity", return_value="stable"
        ):
            self.helper.stop_targets(manager, [target])
        self.assertEqual(manager.checked_ports, [18777])

    def test_stop_accepts_verified_process_that_exits_after_manager_timeout(self):
        manager = Manager()
        target = self.helper.Target("API", 7979, 1234, "stable")
        with (
            patch.object(
                self.helper, "stable_process_identity", return_value="stable"
            ),
            patch.object(manager, "_kill_process", return_value=False) as kill,
            patch.object(
                self.helper, "process_is_absent", side_effect=(False, True)
            ),
            patch.object(self.helper.time, "sleep") as sleep,
        ):
            self.helper.stop_targets(manager, [target])
        kill.assert_called_once_with(1234)
        sleep.assert_called_once_with(0.1)

    def test_stop_rejects_pid_reuse_during_late_exit_wait(self):
        manager = Manager()
        target = self.helper.Target("API", 7979, 1234, "stable")
        with (
            patch.object(
                self.helper,
                "stable_process_identity",
                side_effect=("stable", "stable", "replacement"),
            ),
            patch.object(manager, "_kill_process", return_value=False) as kill,
            patch.object(self.helper, "process_is_absent", return_value=False),
            patch.object(self.helper.time, "sleep") as sleep,
            self.assertRaisesRegex(self.helper.StopError, "replaced API"),
        ):
            self.helper.stop_targets(manager, [target])
        kill.assert_called_once_with(1234)
        sleep.assert_not_called()

    def test_stop_fails_closed_after_extended_grace_period(self):
        manager = Manager()
        target = self.helper.Target("API", 7979, 1234, "stable")
        with (
            patch.object(
                self.helper, "stable_process_identity", return_value="stable"
            ),
            patch.object(manager, "_kill_process", return_value=False) as kill,
            patch.object(self.helper, "process_is_absent", return_value=False),
            patch.object(self.helper.time, "sleep") as sleep,
            self.assertRaisesRegex(self.helper.StopError, "failed to stop API"),
        ):
            self.helper.stop_targets(manager, [target])
        self.assertEqual(kill.call_args_list, [call(1234), call(1234)])
        self.assertEqual(sleep.call_count, 599)

    def test_stop_retries_verified_graceful_signal_after_extended_wait(self):
        manager = Manager()
        target = self.helper.Target("API", 7979, 1234, "stable")
        with (
            patch.object(
                self.helper, "stable_process_identity", return_value="stable"
            ),
            patch.object(
                manager, "_kill_process", side_effect=(False, True)
            ) as kill,
            patch.object(
                self.helper, "wait_for_verified_process_exit", return_value=False
            ),
        ):
            self.helper.stop_targets(manager, [target])
        self.assertEqual(kill.call_args_list, [call(1234), call(1234)])

    def test_stop_refuses_second_signal_after_pid_reuse(self):
        manager = Manager()
        target = self.helper.Target("API", 7979, 1234, "stable")
        with (
            patch.object(
                self.helper,
                "stable_process_identity",
                side_effect=("stable", "stable", "replacement"),
            ),
            patch.object(manager, "_kill_process", return_value=False) as kill,
            patch.object(
                self.helper, "wait_for_verified_process_exit", return_value=False
            ),
            self.assertRaisesRegex(self.helper.StopError, "replaced API"),
        ):
            self.helper.stop_targets(manager, [target])
        kill.assert_called_once_with(1234)

    def test_stop_accepts_disappearance_before_second_signal(self):
        manager = Manager()
        target = self.helper.Target("API", 7979, 1234, "stable")
        with (
            patch.object(
                self.helper,
                "stable_process_identity",
                side_effect=("stable", "stable", ""),
            ),
            patch.object(manager, "_kill_process", return_value=False) as kill,
            patch.object(
                self.helper, "wait_for_verified_process_exit", return_value=False
            ),
            patch.object(self.helper, "process_is_absent", return_value=True),
        ):
            self.helper.stop_targets(manager, [target])
        kill.assert_called_once_with(1234)

    def test_stop_allows_pid_marker_already_removed_after_verified_stop(self):
        manager = Manager()
        with tempfile.TemporaryDirectory() as directory:
            cleanup_path = Path(directory) / "control.pid"
            write_control_pid(cleanup_path, 1234)
            target = self.helper.Target(
                "control", 7977, 1234, "stable", cleanup_path
            )

            def remove_marker(_pid):
                cleanup_path.unlink()
                manager.killed.append(1234)
                return True

            with (
                patch.object(
                    self.helper, "stable_process_identity", return_value="stable"
                ),
                patch.object(manager, "_kill_process", side_effect=remove_marker),
            ):
                self.helper.stop_targets(manager, [target])
            self.assertEqual(manager.killed, [1234])

    def test_stop_preserves_control_pid_file_if_recorded_target_changes(self):
        manager = Manager()
        with tempfile.TemporaryDirectory() as directory:
            cleanup_path = Path(directory) / "control.pid"
            write_control_pid(cleanup_path, 5678)
            target = self.helper.Target(
                "control", 7977, 1234, "stable", cleanup_path
            )
            with (
                patch.object(
                    self.helper, "stable_process_identity", return_value="stable"
                ),
                self.assertRaisesRegex(
                    self.helper.StopError, "PID file changed before cleanup"
                ),
            ):
                self.helper.stop_targets(manager, [target])
            self.assertEqual(manager.killed, [])
            self.assertEqual(json.loads(cleanup_path.read_text())["pid"], 5678)

    def test_busy_port_after_kill_preserves_pid_cleanup_and_raises(self):
        manager = Manager(busy_ports={18777})
        with tempfile.TemporaryDirectory() as directory:
            cleanup_path = Path(directory) / "control.pid"
            write_control_pid(cleanup_path, 1234, 18777)
            target = self.helper.Target(
                "control", 18777, 1234, "stable", cleanup_path
            )
            with (
                patch.object(
                    self.helper, "stable_process_identity", return_value="stable"
                ),
                patch.object(self.helper.time, "sleep"),
                self.assertRaisesRegex(
                    self.helper.StopError,
                    "ports still listening after stop: 18777",
                ),
            ):
                self.helper.stop_targets(manager, [target])
            self.assertEqual(manager.killed, [1234])
            self.assertTrue(cleanup_path.exists())

    def test_discovery_rejects_identity_change_during_ownership_check(self):
        with patch.object(
            self.helper,
            "stable_process_identity",
            side_effect=("first", "replacement"),
        ) as stable_identity:
            self.assertEqual(
                self.helper.verified_process_identity(1234, lambda: True), ""
            )
        self.assertEqual(stable_identity.call_count, 2)

    def test_idle_control_port_still_targets_live_owned_recorded_process(self):
        manager = Manager()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            pid_path = home / ".hindsight" / "control.pid"
            pid_path.parent.mkdir()
            write_control_pid(pid_path, 1234)
            with (
                patch.object(Path, "home", return_value=home),
                patch.object(
                    self.helper, "stable_process_identity", return_value="live-process"
                ),
                patch.object(
                    self.helper, "owns_hindsight_control", return_value=True
                ),
            ):
                targets = self.helper.find_control_target(manager, 7977)
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0].kind, "control")
            self.assertEqual(targets[0].pid, 1234)
            self.assertEqual(targets[0].cleanup_path, pid_path)
            self.assertIsNotNone(targets[0].cleanup_identity)
            self.assertTrue(pid_path.exists())

    def test_idle_control_port_preserves_pid_file_when_identity_is_inconclusive(self):
        manager = Manager()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            pid_path = home / ".hindsight" / "control.pid"
            pid_path.parent.mkdir()
            write_control_pid(pid_path, 1234)
            with (
                patch.object(Path, "home", return_value=home),
                patch.object(
                    self.helper, "stable_process_identity", return_value="reused"
                ),
                patch.object(
                    self.helper, "owns_hindsight_control", return_value=False
                ),
                patch.object(
                    self.helper, "process_is_absent", return_value=False
                ),
                self.assertRaisesRegex(
                    self.helper.StopError, "unverified listener"
                ),
            ):
                self.helper.find_control_target(manager, 7977)
            self.assertTrue(pid_path.exists())
            self.assertEqual(manager.killed, [])

    def test_idle_control_port_removes_pid_file_only_for_stale_recorded_process(self):
        manager = Manager()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            pid_path = home / ".hindsight" / "control.pid"
            pid_path.parent.mkdir()
            write_control_pid(pid_path, 1234)
            with (
                patch.object(Path, "home", return_value=home),
                patch.object(self.helper, "stable_process_identity", return_value=""),
                patch.object(
                    self.helper, "process_is_absent", return_value=True
                ),
            ):
                self.assertEqual(self.helper.find_control_target(manager, 7977), [])
            self.assertFalse(pid_path.exists())

    def test_idle_control_port_removes_malformed_and_nonpositive_pid_files(self):
        manager = Manager()
        for payload in (b"not-a-pid\n", b"\xff\n", b"0\n", b"-1\n", b"\n"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                pid_path = home / ".hindsight" / "control.pid"
                pid_path.parent.mkdir()
                pid_path.write_bytes(payload)
                with patch.object(Path, "home", return_value=home):
                    self.assertEqual(self.helper.find_control_target(manager, 7977), [])
                self.assertFalse(pid_path.exists())

    def test_busy_owned_control_port_removes_malformed_pid_and_returns_target(self):
        manager = Manager()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            pid_path = home / ".hindsight" / "control.pid"
            pid_path.parent.mkdir()
            pid_path.write_text("not-a-pid\n", encoding="ascii")
            with (
                patch.object(Path, "home", return_value=home),
                patch.object(
                    self.helper, "find_pids_on_port", return_value=[1234]
                ),
                patch.object(
                    self.helper,
                    "stable_process_identity",
                    return_value="owned-control",
                ),
                patch.object(
                    self.helper, "owns_hindsight_control", return_value=True
                ),
            ):
                targets = self.helper.find_control_target(manager, 7977)

            self.assertEqual(
                targets,
                [self.helper.Target("control", 7977, 1234, "owned-control")],
            )
            self.assertFalse(pid_path.exists())

    def test_multiple_control_listeners_fail_before_ownership_or_signal(self):
        manager = Manager()
        with (
            patch.object(
                self.helper, "find_pids_on_port", return_value=[1234, 5678]
            ),
            patch.object(
                self.helper,
                "owns_hindsight_control",
                side_effect=AssertionError("must fail before ownership mutation"),
            ),
            self.assertRaisesRegex(
                self.helper.StopError, "multiple control listeners"
            ),
        ):
            self.helper.find_control_target(manager, 7977)
        self.assertEqual(manager.killed, [])

    def test_idle_control_port_reports_unexpected_pid_read_errors(self):
        manager = Manager()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            pid_path = home / ".hindsight" / "control.pid"
            pid_path.parent.mkdir()
            write_control_pid(pid_path, 1234)
            original_open = self.helper.os.open

            def fail_selected(path, *args, **kwargs):
                if (
                    Path(path) == pid_path
                    or (
                        path == pid_path.name
                        and kwargs.get("dir_fd") is not None
                    )
                ):
                    raise PermissionError("denied")
                return original_open(path, *args, **kwargs)

            with (
                patch.object(Path, "home", return_value=home),
                patch.object(self.helper.os, "open", side_effect=fail_selected),
                self.assertRaisesRegex(self.helper.StopError, "securely open control PID file"),
            ):
                self.helper.find_control_target(manager, 7977)

    def test_parse_rejects_invalid_and_colliding_normalize_ports(self):
        invalid = ("0", "65536", "not-a-port")
        for value in invalid:
            with self.subTest(value=value), redirect_stderr(
                StringIO()
            ), self.assertRaises(SystemExit):
                self.helper.parse_args([
                    "--mode", "stop-control", "--control-port", value,
                ])
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            self.helper.parse_args([
                "--mode", "normalize",
                "--desired-api-port", "7979",
                "--desired-ui-port", "7979",
            ])

    def test_stale_pid_unlink_failures_are_stop_errors(self):
        manager = Manager()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            pid_path = home / ".hindsight" / "control.pid"
            pid_path.parent.mkdir()
            write_control_pid(pid_path, 1234)
            with (
                patch.object(Path, "home", return_value=home),
                patch.object(self.helper, "stable_process_identity", return_value=""),
                patch.object(self.helper, "process_is_absent", return_value=True),
                patch.object(self.helper.os, "unlink", side_effect=OSError("denied")),
                self.assertRaisesRegex(self.helper.StopError, "remove control PID file"),
            ):
                self.helper.find_control_target(manager, 7977)


if __name__ == "__main__":
    unittest.main()
