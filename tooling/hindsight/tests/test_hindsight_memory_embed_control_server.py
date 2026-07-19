import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
import os
import sys
import tempfile
from types import ModuleType, SimpleNamespace
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


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    provider: str
    model: str | None
    base_url: str | None


@dataclass(frozen=True)
class ProfileSummary:
    name: str
    provider: str
    model: str | None


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

    def test_start_refuses_live_existing_control_server_on_different_port(self):
        pid_path = Path(self.temporary.name) / "control.pid"
        existing_state = Path(self.temporary.name) / "existing-state"
        self.module._write_private_pid(
            pid_path, os.getpid(), 7879, existing_state
        )
        lifecycle = SimpleNamespace(
            pid_file=lambda: pid_path,
            get_or_create_token=lambda: "token",
            control_status=lambda _port: SimpleNamespace(running=False),
        )
        package = ModuleType("hindsight_embed")
        control_center = ModuleType("hindsight_embed.control_center")
        control_center.lifecycle = lifecycle
        package.control_center = control_center
        with patch.dict(
            sys.modules,
            {
                "hindsight_embed": package,
                "hindsight_embed.control_center": control_center,
            },
        ), patch.object(self.module.subprocess, "Popen") as popen:
            self.assertEqual(self.module.start(7878, self.state_dir), 1)
        popen.assert_not_called()
        self.assertEqual(
            json.loads(pid_path.read_text(encoding="ascii"))["port"],
            7879,
        )

    def test_start_refuses_malformed_control_pid_without_replacing_it(self):
        pid_path = Path(self.temporary.name) / "control.pid"
        pid_path.write_text("{}", encoding="ascii")
        pid_path.chmod(0o600)
        lifecycle = SimpleNamespace(
            pid_file=lambda: pid_path,
            get_or_create_token=lambda: "token",
            control_status=lambda _port: SimpleNamespace(running=False),
        )
        package = ModuleType("hindsight_embed")
        control_center = ModuleType("hindsight_embed.control_center")
        control_center.lifecycle = lifecycle
        package.control_center = control_center
        with patch.dict(
            sys.modules,
            {
                "hindsight_embed": package,
                "hindsight_embed.control_center": control_center,
            },
        ), patch.object(self.module.subprocess, "Popen") as popen:
            self.assertEqual(self.module.start(7878, self.state_dir), 1)
        popen.assert_not_called()
        self.assertEqual(pid_path.read_text(encoding="ascii"), "{}")

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

    def test_provider_preset_catalog_and_alias_round_trip(self):
        runtime = ProfileConfig(
            "example-profile", "lmstudio", "example-model", "https://host/v1"
        )
        saved = []
        service = SimpleNamespace(
            get_profile_config=lambda _name: runtime,
            list_profiles=lambda: [
                ProfileSummary("example-profile", "lmstudio", "example-model")
            ],
            save_llm_config=lambda **kwargs: saved.append(kwargs) or runtime,
        )
        preset = self.module.ProviderPreset(
            id="private-remote",
            label="Private remote",
            runtime_provider="lmstudio",
            base_url="https://host/v1",
            model="example-model",
        )

        self.module.install_provider_catalog(self.providers, preset)
        self.module.install_provider_alias(service, preset)

        self.assertEqual(
            [item.id for item in self.providers.PROVIDER_CATALOG].count(
                "private-remote"
            ),
            1,
        )
        self.assertEqual(service.get_profile_config("example-profile").provider, "private-remote")
        self.assertEqual(service.list_profiles()[0].provider, "private-remote")
        displayed = service.save_llm_config(
            name="example-profile",
            provider="private-remote",
            api_key="ignored",
            model="ignored",
            base_url=None,
        )
        self.assertEqual(displayed.provider, "private-remote")
        self.assertEqual(
            saved[-1],
            {
                "name": "example-profile",
                "provider": "lmstudio",
                "api_key": "",
                "model": "example-model",
                "base_url": "https://host/v1",
                "api_port": None,
                "ui_port": None,
                "api_version": None,
                "cp_version": None,
            },
        )

        service.save_llm_config(
            name="example-profile",
            provider="openai",
            api_key="secret",
            model="other-model",
            base_url=None,
        )
        self.assertEqual(saved[-1]["base_url"], "")

    def test_provider_preset_environment_is_all_or_none(self):
        complete = {
            "HINDSIGHT_EMBED_PROVIDER_PRESET_ID": "private-remote",
            "HINDSIGHT_EMBED_PROVIDER_PRESET_LABEL": "Private remote",
            "HINDSIGHT_EMBED_PROVIDER_PRESET_RUNTIME_PROVIDER": "lmstudio",
            "HINDSIGHT_EMBED_PROVIDER_PRESET_BASE_URL": "https://host/v1",
            "HINDSIGHT_EMBED_PROVIDER_PRESET_MODEL": "example-model",
        }
        self.assertEqual(
            self.module.provider_preset_from_environment(complete),
            self.module.ProviderPreset(
                id="private-remote",
                label="Private remote",
                runtime_provider="lmstudio",
                base_url="https://host/v1",
                model="example-model",
            ),
        )
        with self.assertRaisesRegex(ValueError, "incomplete provider preset"):
            self.module.provider_preset_from_environment(
                {"HINDSIGHT_EMBED_PROVIDER_PRESET_ID": "private-remote"}
            )

        for unsafe_url in (
            "https://user:credential@host/v1",
            "https://host/v1?api_key=credential",
            "https://host/v1#credential",
            "http://192.0.2.1/v1",
            "http://127.0.0.1:0/v1",
            "file:///private/provider",
        ):
            with self.subTest(unsafe_url=unsafe_url), self.assertRaisesRegex(
                ValueError, "invalid provider preset base URL"
            ):
                self.module.provider_preset_from_environment(
                    {
                        **complete,
                        "HINDSIGHT_EMBED_PROVIDER_PRESET_BASE_URL": unsafe_url,
                    }
                )

        loopback = {
            **complete,
            "HINDSIGHT_EMBED_PROVIDER_PRESET_BASE_URL": "http://127.0.0.1:1234/v1",
        }
        self.assertEqual(
            self.module.provider_preset_from_environment(loopback).base_url,
            "http://127.0.0.1:1234/v1",
        )

    def test_provider_preset_rejects_reserved_and_existing_ids(self):
        preset = self.module.ProviderPreset(
            id="openai-codex",
            label="Conflicting provider",
            runtime_provider="lmstudio",
            base_url="https://host/v1",
            model="example-model",
        )
        original_ids = [
            provider.id for provider in self.providers.PROVIDER_CATALOG
        ]
        with self.assertRaisesRegex(ValueError, "provider preset ID is reserved"):
            self.module.install_provider_catalog(self.providers, preset)
        self.assertEqual(
            [provider.id for provider in self.providers.PROVIDER_CATALOG],
            original_ids,
        )

        preset = preset._replace(id="openai")
        with self.assertRaisesRegex(ValueError, "provider preset ID is reserved"):
            self.module.install_provider_catalog(self.providers, preset)

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
