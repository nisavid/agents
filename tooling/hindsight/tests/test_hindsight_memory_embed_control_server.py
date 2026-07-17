import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "libexec/hindsight-embed-control-server.py"


def load_helper():
    spec = importlib.util.spec_from_file_location(
        "hindsight_embed_control_server", HELPER
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    needs_api_key: bool
    default_base_url: str | None = None


@dataclass(frozen=True)
class DaemonResult:
    ok: bool
    running: bool


@dataclass(frozen=True)
class UiResult:
    running: bool


class ControlServerHooksTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HELPER.exists(), "managed control wrapper is missing")
        self.module = load_helper()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state_dir = Path(self.temporary.name) / "desired"
        self.providers = SimpleNamespace(
            ProviderInfo=ProviderInfo,
            PROVIDER_CATALOG=(ProviderInfo("openai", "OpenAI", True),),
        )
        self.service = SimpleNamespace(
            start_daemon=lambda _name: DaemonResult(ok=True, running=True),
            restart_daemon=lambda _name: DaemonResult(ok=True, running=True),
            stop_daemon=lambda _name: DaemonResult(ok=True, running=False),
            start_ui=lambda _name: UiResult(running=True),
            restart_ui=lambda _name: UiResult(running=True),
            stop_ui=lambda _name: UiResult(running=False),
        )
        self.module.install_hooks(
            self.service, self.providers, self.state_dir
        )

    def test_control_log_open_is_nonblocking_before_regular_file_validation(self):
        log_path = Path(self.temporary.name) / "control.log"
        real_open = self.module.os.open
        observed_flags = []

        def record_open(path, flags, *args, **kwargs):
            if path == log_path.name and kwargs.get("dir_fd") is not None:
                observed_flags.append(flags)
            return real_open(path, flags, *args, **kwargs)

        with patch.object(self.module.os, "open", side_effect=record_open):
            descriptor = self.module._open_private_append(log_path)
        self.module.os.close(descriptor)
        self.assertTrue(observed_flags)
        self.assertTrue(observed_flags[0] & self.module.os.O_NONBLOCK)

    def test_control_pid_record_binds_process_port_and_desired_state(self):
        pid_path = Path(self.temporary.name) / "control.pid"
        desired = Path(self.temporary.name) / "desired-state"
        self.module._write_private_pid(pid_path, 1234, 7878, desired)
        self.assertEqual(
            json.loads(pid_path.read_text(encoding="ascii")),
            {
                "desired_state_dir": str(desired),
                "pid": 1234,
                "port": 7878,
            },
        )

    def test_rejects_group_writable_nonprivate_ancestor(self):
        ancestor = Path(self.temporary.name) / "group-writable"
        ancestor.mkdir(mode=0o700)
        ancestor.chmod(0o770)
        target = ancestor / "private-leaf"
        with self.assertRaises(ValueError):
            self.module._open_absolute_directory(
                target, create=True, private=True, label="test directory"
            )
        self.assertFalse(target.exists())

    def desired(self, profile, component):
        return (
            self.state_dir / "profiles" / profile / component
        ).read_text(encoding="utf-8").strip()

    def test_adds_subscription_providers_idempotently(self):
        self.module.install_provider_catalog(self.providers)
        catalog = {
            provider.id: provider
            for provider in self.providers.PROVIDER_CATALOG
        }
        self.assertEqual(
            set(catalog), {"openai", "openai-codex", "claude-code"}
        )
        self.assertFalse(catalog["openai-codex"].needs_api_key)
        self.assertFalse(catalog["claude-code"].needs_api_key)

    def test_lifecycle_hooks_preserve_intent_and_required_daemon(self):
        self.service.stop_daemon("example-profile")
        self.service.stop_ui("example-profile")
        self.assertEqual(self.desired("example-profile", "daemon"), "stopped")
        self.assertEqual(self.desired("example-profile", "ui"), "stopped")

        self.service.start_ui("example-profile")
        self.assertEqual(self.desired("example-profile", "daemon"), "running")
        self.assertEqual(self.desired("example-profile", "ui"), "running")

    def test_failed_stop_restores_running_intent(self):
        service = SimpleNamespace(**vars(self.service))
        service.stop_daemon = lambda _name: DaemonResult(
            ok=False, running=True
        )
        self.module.install_lifecycle_hooks(service, self.state_dir)

        service.stop_daemon("example-profile")

        self.assertEqual(self.desired("example-profile", "daemon"), "running")

    def test_desired_state_is_private_and_rejects_unsafe_names_and_paths(self):
        self.module.set_desired_state(
            self.state_dir, "example-profile", "daemon", "stopped"
        )
        profile = self.state_dir / "profiles" / "example-profile"
        self.assertEqual(os.stat(self.state_dir).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(profile).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(profile / "daemon").st_mode & 0o777, 0o600)

        for name in ("../outside", "bad/name"):
            with self.subTest(profile=name), self.assertRaises(ValueError):
                self.module.set_desired_state(
                    self.state_dir, name, "daemon", "stopped"
                )

        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        linked = Path(self.temporary.name) / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.module.set_desired_state(
                linked, "example-profile", "daemon", "stopped"
            )
        self.assertFalse((outside / "profiles").exists())

    def test_rejects_permissive_existing_root_and_symlinked_target(self):
        permissive = Path(self.temporary.name) / "permissive"
        permissive.mkdir(mode=0o700)
        permissive.chmod(0o777)
        with self.assertRaises(ValueError):
            self.module.set_desired_state(
                permissive, "example-profile", "daemon", "stopped"
            )

        profile = self.state_dir / "profiles" / "example-profile"
        profile.mkdir(parents=True, mode=0o700)
        outside = Path(self.temporary.name) / "outside-state"
        outside.write_text("unchanged\n", encoding="utf-8")
        (profile / "daemon").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.module.set_desired_state(
                self.state_dir, "example-profile", "daemon", "stopped"
            )
        self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged\n")

    def test_main_preserves_symlink_for_guarded_writer_and_validates_port(self):
        outside = Path(self.temporary.name) / "outside-main"
        outside.mkdir()
        linked = Path(self.temporary.name) / "linked-main"
        linked.symlink_to(outside, target_is_directory=True)
        captured = []
        args = SimpleNamespace(
            command="start", port=7878, desired_state_dir=linked
        )
        with (
            patch.object(self.module, "parse_args", return_value=args),
            patch.object(
                self.module,
                "start",
                side_effect=lambda port, root: captured.append((port, root)) or 0,
            ),
        ):
            self.assertEqual(self.module.main(), 0)
        self.assertEqual(captured, [(7878, linked.absolute())])

        for value in ("0", "65536", "not-a-port"):
            with self.subTest(port=value), self.assertRaises(SystemExit):
                self.module.parse_args(
                    [
                        "start",
                        "--port",
                        value,
                        "--desired-state-dir",
                        str(self.state_dir),
                    ]
                )


if __name__ == "__main__":
    unittest.main()
