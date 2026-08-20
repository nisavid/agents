from __future__ import annotations

import base64
import ctypes
from dataclasses import replace
from decimal import localcontext
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import pwd
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
import unittest
from contextlib import contextmanager, redirect_stderr
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
ZSH_EXECUTABLE = Path(shutil.which("zsh") or "/nonexistent/zsh").resolve()

sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.portable_install import (  # noqa: E402
    InstallationConfig,
    PortableInstallError,
    PortableInstallationManager,
    _ManagedServiceCommandError,
    _systemd_escape,
    _systemd_user_service_root,
)
import hindsight_memory_control_plane.portable_install as portable_install_module  # noqa: E402
from hindsight_memory_control_plane.canonical import digest  # noqa: E402
from hindsight_memory_control_plane.inventory import load_inventory  # noqa: E402
from tooling.hindsight.tests.hindsight_data_identity_test_support import (  # noqa: E402
    build_rebind_evidence,
    reseal_rebind_evidence,
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def directory_argument_matches(value: Path | int, path: Path) -> bool:
    if isinstance(value, int):
        descriptor_metadata = os.fstat(value)
        path_metadata = path.lstat()
        return (descriptor_metadata.st_dev, descriptor_metadata.st_ino) == (
            path_metadata.st_dev,
            path_metadata.st_ino,
        )
    return value == path


def runtime_library(source: str) -> str:
    marker = "\n" + portable_install_module.RUNTIME_LIBRARY_END + "\n"
    prefix, separator, suffix = source.partition(marker)
    if not separator or marker in suffix:
        raise AssertionError("runtime source must contain one library-end marker")
    return prefix


def managed_python_for_tests() -> Path:
    override = os.environ.get("HINDSIGHT_PORTABLE_TEST_MANAGED_PYTHON")
    if override:
        return Path(override).resolve(strict=True)
    uv = shutil.which("uv")
    if uv is None:
        raise unittest.SkipTest("a managed uv Python is required")
    completed = subprocess.run(
        [
            uv,
            "python",
            "find",
            "--managed-python",
            "--resolve-links",
            "--no-python-downloads",
            ">=3.11",
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise unittest.SkipTest("a managed uv Python >=3.11 is required")
    return Path(completed.stdout.strip()).resolve(strict=True)


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.launchd_jobs: dict[str, Path] = {}

    def __call__(self, argv: tuple[str, ...]) -> str | None:
        self.calls.append(argv)
        if argv[:2] == ("/bin/launchctl", "print"):
            label = argv[2].rsplit("/", 1)[-1]
            path = self.launchd_jobs.get(label)
            if path is None:
                raise _ManagedServiceCommandError(113)
            return f"path = {path}\nstate = running\n"
        if argv[:2] == ("/bin/launchctl", "bootstrap"):
            path = Path(argv[3])
            label = plistlib.loads(path.read_bytes())["Label"]
            self.launchd_jobs[label] = path
        elif argv[:2] == ("/bin/launchctl", "bootout"):
            label = argv[2].rsplit("/", 1)[-1]
            if self.launchd_jobs.pop(label, None) is None:
                raise _ManagedServiceCommandError(113)
        return None


class AbsentLaunchdRunner(RecordingRunner):
    def __call__(self, argv: tuple[str, ...]) -> None:
        super().__call__(argv)
        if argv[:2] == ("/bin/launchctl", "bootout"):
            raise _ManagedServiceCommandError(3)


class FailedLaunchdRunner(RecordingRunner):
    def __call__(self, argv: tuple[str, ...]) -> None:
        super().__call__(argv)
        if argv[:2] == ("/bin/launchctl", "bootout"):
            raise _ManagedServiceCommandError(5)


class MissingLaunchdRunner(RecordingRunner):
    def __call__(self, argv: tuple[str, ...]) -> None:
        super().__call__(argv)
        if argv[:2] == ("/bin/launchctl", "print"):
            raise _ManagedServiceCommandError(113)


class MissingSystemdRunner(RecordingRunner):
    def __call__(self, argv: tuple[str, ...]) -> str | None:
        self.calls.append(argv)
        if argv[:5] == (
            "/usr/bin/systemctl",
            "--user",
            "show",
            "--property=FragmentPath",
            "--value",
        ):
            raise _ManagedServiceCommandError(4)
        if argv[:3] == ("/usr/bin/systemctl", "--user", "show"):
            return "LoadState=not-found\nFragmentPath=\n"
        if argv[:3] in {
            ("/usr/bin/systemctl", "--user", "stop"),
            ("/usr/bin/systemctl", "--user", "disable"),
        }:
            raise AssertionError("missing systemd unit must not be mutated")
        return None


class EsrchBootoutRunner(RecordingRunner):
    def __call__(self, argv: tuple[str, ...]) -> None:
        super().__call__(argv)
        if argv[:2] == ("/bin/launchctl", "bootout"):
            raise _ManagedServiceCommandError(113)


class InactiveSystemdRunner(RecordingRunner):
    def __call__(self, argv: tuple[str, ...]) -> str | None:
        super().__call__(argv)
        if argv[:3] == ("/usr/bin/systemctl", "--user", "is-enabled"):
            return "disabled\n"
        if argv[:3] == ("/usr/bin/systemctl", "--user", "is-active"):
            return "inactive\n"
        return None


class InactiveLaunchdServiceRunner(RecordingRunner):
    def __call__(self, argv: tuple[str, ...]) -> str | None:
        result = super().__call__(argv)
        if (
            argv[:2] == ("/bin/launchctl", "print")
            and argv[2].endswith(
                "/io.nisavid.hindsight.synthetic.broker"
            )
        ):
            if result is None:
                return None
            return result.replace("state = running", "state = waiting")
        return result


class DelayedLaunchdServiceRunner(RecordingRunner):
    def __init__(self, delayed_snapshots: int = 2) -> None:
        super().__init__()
        self.delayed_snapshots = delayed_snapshots

    def __call__(self, argv: tuple[str, ...]) -> str | None:
        result = super().__call__(argv)
        if (
            argv[:2] == ("/bin/launchctl", "print")
            and argv[2].endswith("/io.nisavid.hindsight.synthetic.broker")
            and result is not None
            and self.delayed_snapshots > 0
        ):
            self.delayed_snapshots -= 1
            return result.replace(
                "state = running", "state = spawn scheduled"
            )
        return result


class ForeignManifestRunner(RecordingRunner):
    def __call__(self, argv: tuple[str, ...]) -> str | None:
        if argv[:2] == ("/bin/launchctl", "print"):
            self.calls.append(argv)
            return "path = /tmp/foreign.plist\n"
        if argv[:5] == (
            "/usr/bin/systemctl",
            "--user",
            "show",
            "--property=FragmentPath",
            "--value",
        ):
            self.calls.append(argv)
            return "/tmp/foreign.service\n"
        return super().__call__(argv)


@unittest.skipUnless(shutil.which("zsh"), "Zsh is required by the portable runtime")
class PortableInstallationManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.install_root = self.root / "install"
        self.state_root = self.root / "state"
        self.data_root = self.root / "data"
        self.service_root = self.root / "services"
        systemd_root = mock.patch.object(
            portable_install_module,
            "_systemd_user_service_root",
            return_value=self.service_root,
        )
        systemd_root.start()
        self.addCleanup(systemd_root.stop)
        self.inventory = self.root / "consumer" / "inventory.json"
        self.inventory.parent.mkdir(parents=True)
        self.managed_python = managed_python_for_tests()
        self.inventory.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "inventory_id": "synthetic",
                    "canonical_bank": "engineering",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.resolver = self.root / "consumer" / "resolve-credential"
        self.resolver.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.resolver.chmod(0o500)
        self.config_path = self.root / "consumer" / "installation.json"
        self.runner = RecordingRunner()

    def release(self, version: str, marker: str | None = None) -> Path:
        release = self.root / f"release-{version}"
        (release / "bin").mkdir(parents=True)
        executable = release / "bin" / "hindsight-memory"
        executable.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{marker or version}'\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        (release / "lib").mkdir()
        (release / "lib" / "release.txt").write_text(version, encoding="utf-8")
        return release

    def config_data(
        self,
        *,
        platform: str = "launchd",
        installation_mode: str = "fresh",
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "consumer_id": "synthetic",
            "platform": platform,
            "installation_mode": installation_mode,
            "install_root": str(self.install_root),
            "state_root": str(self.state_root),
            "data_root": str(self.data_root),
            "service_root": str(self.service_root),
            "inventory_path": str(self.inventory),
            "python_executable": str(self.managed_python),
            "npx_executable": "/usr/bin/true",
            "uvx_executable": "/usr/bin/true",
            "zsh_executable": str(ZSH_EXECUTABLE),
            "credential_resolver": {
                "path": str(self.resolver),
                "sha256": file_sha256(self.resolver),
            },
            "services": [
                {
                    "service_id": "broker",
                    "label": "io.nisavid.hindsight.synthetic.broker",
                    "entrypoint": "bin/hindsight-memory",
                    "arguments": [
                        "broker",
                        "serve",
                        "--inventory",
                        str(self.inventory),
                    ],
                    "environment": {"PATH": "/usr/bin:/bin"},
                    "credentials": [
                        {
                            "environment": "HINDSIGHT_API_KEY",
                            "locator": "pass://hindsight/data-plane",
                        }
                    ],
                    "restart": "on-failure",
                }
            ],
            "timers": [
                {
                    "timer_id": "integration-upgrades",
                    "label": "io.nisavid.hindsight.synthetic.integration-upgrades",
                    "entrypoint": "bin/hindsight-memory",
                    "arguments": [
                        "integration-upgrade",
                        "status",
                        "--harness",
                        "codex",
                    ],
                    "environment": {"PATH": "/usr/bin:/bin"},
                    "credentials": [],
                    "daily_at": "03:15",
                }
            ],
            "health_checks": [
                {
                    "check_id": "broker",
                    "entrypoint": "bin/hindsight-memory",
                    "arguments": [],
                    "environment": {"PATH": "/usr/bin:/bin"},
                    "credentials": [],
                    "timeout_seconds": 10,
                }
            ],
        }

    def manager(
        self,
        *,
        platform: str = "launchd",
        installation_mode: str = "fresh",
        health_runner=None,
    ) -> PortableInstallationManager:
        data = self.config_data(
            platform=platform,
            installation_mode=installation_mode,
        )
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        config = InstallationConfig.load(data, source_path=self.config_path)
        return PortableInstallationManager(
            config,
            command_runner=self.runner,
            health_runner=health_runner,
        )

    def upgrade(
        self,
        manager: PortableInstallationManager,
        release: Path,
        *,
        version: str,
    ) -> dict[str, object]:
        state = manager._load_state()
        assert state is not None
        return manager.upgrade(
            release,
            version=version,
            expected_current_binding_generation_digest=state[
                "binding_generation_digest"
            ],
        )

    def test_fresh_install_publishes_immutable_release_and_verifies_launchd(
        self,
    ) -> None:
        release = self.release("1.0.0")
        manager = self.manager()

        result = manager.install(release, version="1.0.0")

        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["version"], "1.0.0")
        verification = manager.verify()
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(verification["current"]["version"], "1.0.0")
        active = json.loads((self.install_root / "active.json").read_text())
        installed = (
            self.install_root / active["release_path"] / "bin" / "hindsight-memory"
        )
        self.assertEqual(
            installed.read_text(), (release / "bin" / "hindsight-memory").read_text()
        )
        self.assertEqual(installed.stat().st_mode & 0o222, 0)
        self.assertTrue((self.install_root / "bin" / "hindsight-memory").is_file())
        plist_path = self.service_root / "io.nisavid.hindsight.synthetic.broker.plist"
        plist = plistlib.loads(plist_path.read_bytes())
        rendered = plist_path.read_text(encoding="utf-8")
        self.assertNotIn("HINDSIGHT_API_KEY", rendered)
        self.assertNotIn("pass://hindsight/data-plane", rendered)
        self.assertEqual(plist["Label"], "io.nisavid.hindsight.synthetic.broker")
        self.assertEqual(plist["ExitTimeOut"], 330)
        self.assertIn("--service", plist["ProgramArguments"])
        self.assertTrue(
            any(call[0].endswith("launchctl") for call in self.runner.calls)
        )

    def test_fresh_install_waits_for_launchd_service_to_become_running(
        self,
    ) -> None:
        runner = DelayedLaunchdServiceRunner()
        manager = self.manager()
        manager._command_runner = runner

        with mock.patch.object(portable_install_module.time, "sleep"):
            result = manager.install(
                self.release("1.0.0"),
                version="1.0.0",
            )

        self.assertEqual(result["status"], "installed")
        service_prints = [
            call
            for call in runner.calls
            if call[:2] == ("/bin/launchctl", "print")
            and call[2].endswith(
                "/io.nisavid.hindsight.synthetic.broker"
            )
        ]
        self.assertGreaterEqual(len(service_prints), 3)

    def test_fresh_install_rolls_back_when_launchd_never_becomes_running(
        self,
    ) -> None:
        runner = InactiveLaunchdServiceRunner()
        manager = self.manager()
        manager._command_runner = runner

        with (
            mock.patch.object(
                portable_install_module,
                "LAUNCHD_SERVICE_START_TIMEOUT_SECONDS",
                0,
            ),
            self.assertRaisesRegex(
                PortableInstallError,
                "managed launchd job is not active",
            ),
        ):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertEqual(runner.launchd_jobs, {})
        self.assertFalse(self.install_root.exists())
        self.assertTrue(self.data_root.is_dir())
        self.assertEqual(list(self.service_root.glob("*")), [])

    def test_intentional_service_stop_preserves_installation_and_stays_stopped(
        self,
    ) -> None:
        release = self.release("1.0.0")
        manager = self.manager()
        manager.install(release, version="1.0.0")

        stopped = manager.stop_services()

        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["services"], {"broker": "stopped"})
        self.assertNotIn(
            "io.nisavid.hindsight.synthetic.broker",
            self.runner.launchd_jobs,
        )
        self.assertTrue((self.install_root / "install-state.json").is_file())
        self.assertTrue(
            (
                self.service_root
                / "io.nisavid.hindsight.synthetic.broker.plist"
            ).is_file()
        )

    def test_service_status_reports_intentional_stop_without_requiring_health(
        self,
    ) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(
            self.release("1.0.0"),
            version="1.0.0",
        )
        manager.stop_services()
        manager._health_runner = lambda _check, _release: False

        status = manager.service_status()

        self.assertEqual(status["status"], "stopped")
        self.assertEqual(status["services"], {"broker": "stopped"})
        self.assertEqual(
            status["timers"], {"integration-upgrades": "stopped"}
        )

    def test_explicit_service_start_recovers_an_intentionally_stopped_stack(
        self,
    ) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        manager.stop_services()

        started = manager.start_services()

        self.assertEqual(started["status"], "running")
        self.assertEqual(started["managed_health"], "healthy")
        self.assertEqual(started["services"], {"broker": "running"})
        self.assertEqual(
            started["timers"], {"integration-upgrades": "running"}
        )

    def test_service_start_is_idempotent_for_running_launchd_jobs(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        self.runner.calls.clear()

        started = manager.start_services()

        self.assertEqual(started["status"], "running")
        self.assertFalse(
            any(
                call[1] in {"bootout", "bootstrap", "kickstart"}
                for call in self.runner.calls
                if call[0] == "/bin/launchctl"
            )
        )

    def test_service_restart_replaces_loaded_jobs_and_requires_health(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        self.runner.calls.clear()

        restarted = manager.restart_services()

        self.assertEqual(restarted["status"], "running")
        self.assertEqual(restarted["managed_health"], "healthy")
        for label in (
            "io.nisavid.hindsight.synthetic.broker",
            "io.nisavid.hindsight.synthetic.integration-upgrades",
        ):
            self.assertIn(
                ("/bin/launchctl", "bootout", f"gui/{os.getuid()}/{label}"),
                self.runner.calls,
            )
        self.assertEqual(
            sum(
                call[:2] == ("/bin/launchctl", "bootstrap")
                for call in self.runner.calls
            ),
            2,
        )

    def test_service_restart_explicitly_resets_component_stop_intent(self) -> None:
        data = self.config_data()
        stack_state = self.state_root / "embed"
        data["services"][0]["environment"].update(
            {
                "HINDSIGHT_EMBED_STATE_DIR": str(stack_state),
                "HINDSIGHT_EMBED_FLEET_PROFILES": "work",
                "HINDSIGHT_EMBED_AUTOSTART_DAEMON": "true",
                "HINDSIGHT_EMBED_AUTOSTART_UI": "true",
            }
        )
        self.config_path.write_text(
            json.dumps(data, sort_keys=True), encoding="utf-8"
        )
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")
        desired = stack_state / "desired" / "profiles" / "work"
        desired.mkdir(parents=True, mode=0o700)
        for directory in (
            stack_state,
            stack_state / "desired",
            stack_state / "desired" / "profiles",
            desired,
        ):
            directory.chmod(0o700)
        for component in ("daemon", "ui"):
            (desired / component).write_text("stopped\n", encoding="ascii")
            (desired / component).chmod(0o600)

        manager.restart_services()

        for component in ("daemon", "ui"):
            self.assertEqual(
                (desired / component).read_text(encoding="ascii"),
                "running\n",
            )

    def test_failed_service_start_restores_the_intentional_stop(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        manager.stop_services()
        manager._health_runner = lambda _check, _release: False

        with self.assertRaisesRegex(
            PortableInstallError, "health verification failed"
        ):
            manager.start_services()

        self.assertEqual(manager.service_status()["status"], "stopped")

    def test_failed_systemd_start_restores_the_intentional_stop(self) -> None:
        class StatefulSystemdRunner:
            def __init__(self, service_root):
                self.service_root = service_root
                self.calls = []
                self.active = set()
                self.enabled = set()
                self.ignored_restore = None

            def __call__(self, argv):
                self.calls.append(argv)
                if argv[:5] == (
                    "/usr/bin/systemctl",
                    "--user",
                    "show",
                    "--property=FragmentPath",
                    "--value",
                ):
                    return str(self.service_root / argv[-1])
                if argv[:3] == (
                    "/usr/bin/systemctl",
                    "--user",
                    "is-active",
                ):
                    if argv[-1] not in self.active:
                        raise _ManagedServiceCommandError(3)
                    return "active"
                if argv[:3] == (
                    "/usr/bin/systemctl",
                    "--user",
                    "is-enabled",
                ):
                    if argv[-1] not in self.enabled:
                        raise _ManagedServiceCommandError(1)
                    return "enabled"
                if argv[:4] == (
                    "/usr/bin/systemctl",
                    "--user",
                    "enable",
                    "--now",
                ):
                    self.enabled.add(argv[-1])
                    self.active.add(argv[-1])
                elif argv[:3] == (
                    "/usr/bin/systemctl",
                    "--user",
                    "enable",
                ):
                    self.enabled.add(argv[-1])
                elif argv[:3] == (
                    "/usr/bin/systemctl",
                    "--user",
                    "restart",
                ):
                    self.active.add(argv[-1])
                elif argv[:4] == (
                    "/usr/bin/systemctl",
                    "--user",
                    "disable",
                    "--now",
                ):
                    self.enabled.discard(argv[-1])
                    self.active.discard(argv[-1])
                elif argv[:3] == (
                    "/usr/bin/systemctl",
                    "--user",
                    "disable",
                ):
                    if self.ignored_restore != ("disable", argv[-1]):
                        self.enabled.discard(argv[-1])
                elif argv[:3] == (
                    "/usr/bin/systemctl",
                    "--user",
                    "start",
                ):
                    self.active.add(argv[-1])
                elif argv[:3] == (
                    "/usr/bin/systemctl",
                    "--user",
                    "stop",
                ):
                    if self.ignored_restore != ("stop", argv[-1]):
                        self.active.discard(argv[-1])
                return None

        manager = self.manager(
            platform="systemd-user",
            health_runner=lambda _check, _release: True,
        )
        runner = StatefulSystemdRunner(self.service_root)
        manager._command_runner = runner
        manager.install(self.release("1.0.0"), version="1.0.0")
        manager.stop_services()
        manager._health_runner = lambda _check, _release: False
        runner.calls.clear()

        with self.assertRaisesRegex(
            PortableInstallError,
            "health verification failed",
        ):
            manager.start_services()

        label = "io.nisavid.hindsight.synthetic"
        for command in (
            (
                "/usr/bin/systemctl",
                "--user",
                "disable",
                f"{label}.integration-upgrades.timer",
            ),
            (
                "/usr/bin/systemctl",
                "--user",
                "stop",
                f"{label}.integration-upgrades.timer",
            ),
            (
                "/usr/bin/systemctl",
                "--user",
                "disable",
                f"{label}.broker.service",
            ),
            (
                "/usr/bin/systemctl",
                "--user",
                "stop",
                f"{label}.broker.service",
            ),
            ("/usr/bin/systemctl", "--user", "daemon-reload"),
        ):
            self.assertIn(command, runner.calls)
        self.assertEqual(manager.service_status()["status"], "stopped")

        manager._health_runner = lambda _check, _release: True
        manager.start_services()
        label = "io.nisavid.hindsight.synthetic"
        service_unit = f"{label}.broker.service"
        timer_unit = f"{label}.integration-upgrades.timer"
        runner.active.discard(service_unit)
        runner.enabled.discard(timer_unit)
        prior_active = set(runner.active)
        prior_enabled = set(runner.enabled)
        manager._health_runner = lambda _check, _release: False

        with self.assertRaisesRegex(
            PortableInstallError,
            "health verification failed",
        ):
            manager.start_services()

        self.assertEqual(runner.active, prior_active)
        self.assertEqual(runner.enabled, prior_enabled)

        runner.active.discard(service_unit)
        runner.ignored_restore = ("stop", service_unit)
        with self.assertRaisesRegex(
            PortableInstallError,
            "health verification failed",
        ) as raised:
            manager.start_services()
        self.assertIsInstance(
            raised.exception.__cause__,
            PortableInstallError,
        )
        self.assertIn(
            "service manager prestate restoration failed",
            str(raised.exception.__cause__),
        )

    def test_failed_service_start_restores_a_partial_prestate(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        manager.stop_services()
        service = manager.config.services[0]
        service_manifest = (
            manager.config.service_root / f"{service.label}.plist"
        )
        self.runner.launchd_jobs[service.label] = service_manifest
        self.assertEqual(manager.service_status()["status"], "partial")
        manager._health_runner = lambda _check, _release: False

        with self.assertRaisesRegex(
            PortableInstallError,
            "health verification failed",
        ):
            manager.start_services()

        status = manager.service_status()
        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["services"]["broker"], "running")
        self.assertEqual(status["timers"]["integration-upgrades"], "stopped")

    def test_failed_launchd_activation_restores_running_job_booted_out_first(
        self,
    ) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        expected_jobs = dict(self.runner.launchd_jobs)
        failed_once = False

        def fail_first_bootstrap(argv: tuple[str, ...]) -> str | None:
            nonlocal failed_once
            if argv[:2] == ("/bin/launchctl", "bootstrap") and not failed_once:
                failed_once = True
                self.runner.calls.append(argv)
                raise _ManagedServiceCommandError(22)
            return self.runner(argv)

        manager._command_runner = fail_first_bootstrap

        with self.assertRaises(_ManagedServiceCommandError):
            manager.restart_services()

        self.assertTrue(failed_once)
        self.assertEqual(self.runner.launchd_jobs, expected_jobs)
        self.assertEqual(manager.service_status()["status"], "running")

    def test_service_status_treats_loaded_inactive_launchd_service_as_stopped(
        self,
    ) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        inactive_runner = InactiveLaunchdServiceRunner()
        inactive_runner.launchd_jobs = dict(self.runner.launchd_jobs)
        manager._command_runner = inactive_runner

        status = manager.service_status()

        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["services"]["broker"], "stopped")
        self.assertEqual(status["timers"]["integration-upgrades"], "running")

    def test_service_status_treats_unknown_launchd_output_as_loaded_and_active(
        self,
    ) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        manager._command_runner = lambda _argv: None

        status = manager.service_status()

        self.assertEqual(status["status"], "running")
        self.assertEqual(status["services"]["broker"], "running")
        self.assertEqual(status["timers"]["integration-upgrades"], "running")

    def test_portable_consumer_examples_match_the_closed_schema(self) -> None:
        examples = ROOT / "examples" / "portable-consumer"

        launchd_path = examples / "launchd-installation.json"
        systemd_path = examples / "systemd-user-installation.json"
        launchd = InstallationConfig.load(
            json.loads(launchd_path.read_bytes()), source_path=launchd_path
        )
        with mock.patch.object(
            portable_install_module,
            "_systemd_user_service_root",
            return_value=Path("/home/example/.config/systemd/user"),
        ):
            systemd = InstallationConfig.load(
                json.loads(systemd_path.read_bytes()), source_path=systemd_path
            )
        inventory = load_inventory(examples / "inventory.json")

        self.assertEqual(launchd.platform, "launchd")
        self.assertEqual(systemd.platform, "systemd-user")
        self.assertEqual(inventory.machine["id"], "example-workstation")
        self.assertEqual(
            {binding["home_bank"]["bank_id"] for binding in inventory.harnesses},
            {"engineering"},
        )
        self.assertEqual(
            launchd.services[0].entrypoint, "bin/hindsight-embed-supervisor"
        )
        self.assertEqual(systemd.health_checks[0].arguments, ("--health",))
        for config in (launchd, systemd):
            serialized = json.dumps(config.to_dict(), sort_keys=True)
            self.assertNotIn("resolved-at-runtime", serialized)
            service_environment = dict(config.services[0].environment)
            health_environment = dict(config.health_checks[0].environment)
            for environment in (service_environment, health_environment):
                self.assertEqual(
                    environment["HINDSIGHT_API_AUDIT_LOG_ENABLED"], "false"
                )
                self.assertEqual(
                    environment["HINDSIGHT_API_LLM_TRACE_ENABLED"], "false"
                )
                self.assertEqual(
                    environment["HINDSIGHT_API_TENANT_EXTENSION"],
                    "hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension",
                )
                self.assertEqual(
                    environment["HINDSIGHT_API_WORKER_ID"],
                    "example-workstation-core",
                )
                self.assertEqual(
                    environment["HINDSIGHT_MEMORY_BROKER_WAIT_SECONDS"], "300"
                )
            self.assertEqual(
                config.timers[0].arguments[2:4],
                ("integration-upgrade", "check"),
            )

    def test_reinstalling_exact_release_is_idempotent(self) -> None:
        release = self.release("1.0.0")
        manager = self.manager()
        manager.install(release, version="1.0.0")
        call_count = len(self.runner.calls)

        result = manager.install(release, version="1.0.0")

        self.assertEqual(result["status"], "unchanged")
        self.assertTrue(
            all(
                call[:2] == ("/bin/launchctl", "print")
                for call in self.runner.calls[call_count:]
            )
        )
        self.assertEqual(manager.verify()["status"], "verified")

    def test_reinstalling_exact_release_still_requires_managed_health(self) -> None:
        release = self.release("1.0.0")
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(release, version="1.0.0")
        unhealthy = self.manager(health_runner=lambda _check, _release: False)

        with self.assertRaisesRegex(PortableInstallError, "health verification failed"):
            unhealthy.install(release, version="1.0.0")

    def test_upgrade_requires_current_binding_generation_cas(self) -> None:
        release = self.release("1.0.0")
        manager = self.manager()
        installed = manager.install(release, version="1.0.0")
        calls_before = len(self.runner.calls)

        with self.assertRaisesRegex(
            PortableInstallError, "binding generation digest changed"
        ):
            manager.upgrade(
                release,
                version="1.0.0",
                expected_current_binding_generation_digest="f" * 64,
            )

        self.assertEqual(len(self.runner.calls), calls_before)
        self.assertFalse(manager._transaction_path.exists())
        self.assertEqual(manager._load_state()["current"]["version"], "1.0.0")
        self.assertEqual(
            manager._load_state()["binding_generation_digest"],
            installed["binding_generation_digest"],
        )

    def test_upgrade_admits_config_and_inventory_generation_with_cas(self) -> None:
        release = self.release("1.0.0")
        manager = self.manager()
        installed = manager.install(release, version="1.0.0")
        data = self.config_data()
        data["services"][0]["environment"]["GENERATION"] = "two"
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        self.inventory.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "inventory_id": "synthetic-two",
                    "canonical_bank": "engineering",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        upgraded_manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )

        upgraded = upgraded_manager.upgrade(
            release,
            version="1.0.0",
            expected_current_binding_generation_digest=installed[
                "binding_generation_digest"
            ],
        )

        self.assertEqual(upgraded["status"], "upgraded")
        self.assertNotEqual(
            upgraded["binding_generation_digest"],
            installed["binding_generation_digest"],
        )
        managed_config = json.loads(
            (self.install_root / "managed-config.json").read_text()
        )
        self.assertEqual(
            managed_config["services"][0]["environment"]["GENERATION"], "two"
        )
        self.assertEqual(
            managed_config["python_executable"], str(self.managed_python.resolve())
        )
        self.assertEqual(
            managed_config["npx_executable"], str(Path("/usr/bin/true").resolve())
        )
        self.assertEqual(
            managed_config["uvx_executable"], str(Path("/usr/bin/true").resolve())
        )
        self.assertEqual(managed_config["zsh_executable"], str(ZSH_EXECUTABLE))
        self.assertEqual(
            (self.install_root / "managed-inventory.json").read_bytes(),
            self.inventory.read_bytes(),
        )
        self.assertEqual(
            upgraded_manager.verify()["binding_generation_digest"],
            upgraded["binding_generation_digest"],
        )

    def test_upgrade_admits_owned_service_topology_changes_with_cas(self) -> None:
        release = self.release("1.0.0")
        manager = self.manager()
        installed = manager.install(release, version="1.0.0")
        data = self.config_data()
        data["services"].append(
            {
                "service_id": "secondary",
                "label": "io.nisavid.hindsight.synthetic.secondary",
                "entrypoint": "bin/hindsight-memory",
                "arguments": [],
                "environment": {"PATH": "/usr/bin:/bin"},
                "credentials": [],
                "restart": "on-failure",
            }
        )
        data["timers"] = []
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        upgraded_manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )

        upgraded_manager.upgrade(
            release,
            version="1.0.0",
            expected_current_binding_generation_digest=installed[
                "binding_generation_digest"
            ],
        )

        self.assertEqual(upgraded_manager.verify()["status"], "verified")
        self.assertTrue(
            (
                self.service_root / "io.nisavid.hindsight.synthetic.secondary.plist"
            ).is_file()
        )
        self.assertFalse(
            (
                self.service_root
                / "io.nisavid.hindsight.synthetic.integration-upgrades.plist"
            ).exists()
        )
        self.assertNotIn(
            "io.nisavid.hindsight.synthetic.integration-upgrades",
            self.runner.launchd_jobs,
        )
        self.assertIn(
            "io.nisavid.hindsight.synthetic.secondary",
            self.runner.launchd_jobs,
        )

    def test_interrupted_launchd_topology_upgrade_stops_candidate_jobs(self) -> None:
        release = self.release("1.0.0")
        original_data = self.config_data()
        manager = self.manager()
        installed = manager.install(release, version="1.0.0")
        candidate_data = self.config_data()
        candidate_data["services"].append(
            {
                "service_id": "secondary",
                "label": "io.nisavid.hindsight.synthetic.secondary",
                "entrypoint": "bin/hindsight-memory",
                "arguments": [],
                "environment": {"PATH": "/usr/bin:/bin"},
                "credentials": [],
                "restart": "on-failure",
            }
        )
        self.config_path.write_text(
            json.dumps(candidate_data, sort_keys=True), encoding="utf-8"
        )
        health_calls = 0

        def interrupt_candidate(_check, _release):
            nonlocal health_calls
            health_calls += 1
            if health_calls > 1:
                raise KeyboardInterrupt
            return True

        candidate = PortableInstallationManager(
            InstallationConfig.load(candidate_data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=interrupt_candidate,
        )
        with self.assertRaises(KeyboardInterrupt):
            candidate.upgrade(
                release,
                version="1.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )

        self.assertIn(
            "io.nisavid.hindsight.synthetic.secondary", self.runner.launchd_jobs
        )
        self.config_path.write_text(
            json.dumps(original_data, sort_keys=True), encoding="utf-8"
        )
        recovered = PortableInstallationManager(
            InstallationConfig.load(original_data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )

        self.assertEqual(recovered.verify()["status"], "verified")
        self.assertNotIn(
            "io.nisavid.hindsight.synthetic.secondary", self.runner.launchd_jobs
        )
        self.assertFalse(
            (
                self.service_root / "io.nisavid.hindsight.synthetic.secondary.plist"
            ).exists()
        )

    def test_interrupted_systemd_topology_upgrade_stops_candidate_units(self) -> None:
        release = self.release("1.0.0")
        original_data = self.config_data(platform="systemd-user")
        self.config_path.write_text(
            json.dumps(original_data, sort_keys=True), encoding="utf-8"
        )
        manager = PortableInstallationManager(
            InstallationConfig.load(original_data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        installed = manager.install(release, version="1.0.0")
        candidate_data = self.config_data(platform="systemd-user")
        candidate_data["services"].append(
            {
                "service_id": "secondary",
                "label": "io.nisavid.hindsight.synthetic.secondary",
                "entrypoint": "bin/hindsight-memory",
                "arguments": [],
                "environment": {"PATH": "/usr/bin:/bin"},
                "credentials": [],
                "restart": "on-failure",
            }
        )
        self.config_path.write_text(
            json.dumps(candidate_data, sort_keys=True), encoding="utf-8"
        )
        health_calls = 0

        def interrupt_candidate(_check, _release):
            nonlocal health_calls
            health_calls += 1
            if health_calls > 1:
                raise KeyboardInterrupt
            return True

        candidate = PortableInstallationManager(
            InstallationConfig.load(candidate_data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=interrupt_candidate,
        )
        with self.assertRaises(KeyboardInterrupt):
            candidate.upgrade(
                release,
                version="1.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )

        self.runner.calls.clear()
        self.config_path.write_text(
            json.dumps(original_data, sort_keys=True), encoding="utf-8"
        )
        recovered = PortableInstallationManager(
            InstallationConfig.load(original_data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        self.assertEqual(recovered.verify()["status"], "verified")
        self.assertIn(
            (
                "/usr/bin/systemctl",
                "--user",
                "disable",
                "--now",
                "io.nisavid.hindsight.synthetic.secondary.service",
            ),
            self.runner.calls,
        )
        self.assertFalse(
            (
                self.service_root / "io.nisavid.hindsight.synthetic.secondary.service"
            ).exists()
        )

    def test_systemd_topology_upgrade_retires_removed_timer(self) -> None:
        manager = self.manager(platform="systemd-user")
        release = self.release("1.0.0")
        installed = manager.install(release, version="1.0.0")
        data = self.config_data(platform="systemd-user")
        data["timers"] = []
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        upgraded = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        self.runner.calls.clear()

        upgraded.upgrade(
            release,
            version="1.0.0",
            expected_current_binding_generation_digest=installed[
                "binding_generation_digest"
            ],
        )

        self.assertIn(
            (
                "/usr/bin/systemctl",
                "--user",
                "disable",
                "--now",
                "io.nisavid.hindsight.synthetic.integration-upgrades.timer",
            ),
            self.runner.calls,
        )

    def test_systemd_topology_upgrade_rejects_a_foreign_added_unit(self) -> None:
        manager = self.manager(platform="systemd-user")
        release = self.release("1.0.0")
        installed = manager.install(release, version="1.0.0")
        data = self.config_data(platform="systemd-user")
        data["services"].append(
            {
                "service_id": "secondary",
                "label": "io.nisavid.hindsight.synthetic.secondary",
                "entrypoint": "bin/hindsight-memory",
                "arguments": [],
                "environment": {"PATH": "/usr/bin:/bin"},
                "credentials": [],
                "restart": "on-failure",
            }
        )
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        calls: list[tuple[str, ...]] = []

        def runner(argv: tuple[str, ...]) -> str | None:
            calls.append(argv)
            if (
                argv[:5]
                == (
                    "/usr/bin/systemctl",
                    "--user",
                    "show",
                    "--property=LoadState",
                    "--property=FragmentPath",
                )
                and argv[5] == "io.nisavid.hindsight.synthetic.secondary.service"
            ):
                return "LoadState=loaded\nFragmentPath=/usr/lib/systemd/user/foreign.service\n"
            return self.runner(argv)

        upgraded = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=runner,
            health_runner=lambda _check, _release: True,
        )

        with self.assertRaisesRegex(PortableInstallError, "already exists"):
            upgraded.upgrade(
                release,
                version="1.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )

        self.assertFalse(
            any(call[2] in {"stop", "disable"} for call in calls if len(call) > 2)
        )

    def test_failed_binding_upgrade_restores_owned_generation(self) -> None:
        manager = self.manager()
        installed = manager.install(self.release("1.0.0"), version="1.0.0")
        prior_config = (self.install_root / "managed-config.json").read_bytes()
        prior_inventory = (self.install_root / "managed-inventory.json").read_bytes()
        data = self.config_data()
        data["services"][0]["environment"]["GENERATION"] = "two"
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        self.inventory.write_text(
            '{"schema_version":1,"inventory_id":"candidate"}\n',
            encoding="utf-8",
        )

        def interrupt(_check, release):
            if release["version"] == "2.0.0":
                raise KeyboardInterrupt
            return True

        candidate = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=interrupt,
        )
        with self.assertRaises(KeyboardInterrupt):
            candidate.upgrade(
                self.release("2.0.0"),
                version="2.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )

        with self.assertRaisesRegex(PortableInstallError, "consumer binding"):
            candidate.verify()
        state = candidate._load_state()
        self.assertEqual(state["current"]["version"], "1.0.0")
        self.assertEqual(
            state["binding_generation_digest"],
            installed["binding_generation_digest"],
        )
        self.assertEqual(
            (self.install_root / "managed-config.json").read_bytes(), prior_config
        )
        self.assertEqual(
            (self.install_root / "managed-inventory.json").read_bytes(),
            prior_inventory,
        )
        self.assertFalse(candidate._transaction_path.exists())
        candidate._verify_installed_locked(state)

    def test_fresh_install_refuses_preexisting_data(self) -> None:
        self.data_root.mkdir(mode=0o700)
        (self.data_root / "existing.db").write_text("existing", encoding="utf-8")
        manager = self.manager()

        with self.assertRaisesRegex(PortableInstallError, "fresh data root"):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertEqual((self.data_root / "existing.db").read_text(), "existing")
        self.assertFalse(self.install_root.exists())

    def test_adoption_preserves_existing_data_identity(self) -> None:
        self.data_root.mkdir(mode=0o700)
        sentinel = self.data_root / "existing.db"
        sentinel.write_bytes(b"existing-database")
        before = (sentinel.stat().st_dev, sentinel.stat().st_ino, sentinel.read_bytes())
        manager = self.manager(installation_mode="adopt")

        result = manager.install(self.release("1.0.0"), version="1.0.0")

        after = (sentinel.stat().st_dev, sentinel.stat().st_ino, sentinel.read_bytes())
        self.assertEqual(before, after)
        self.assertEqual(
            result["data_identity_digest"], manager.verify()["data_identity_digest"]
        )

    def test_launchd_verify_tolerates_data_root_device_reassignment(self) -> None:
        self.data_root.mkdir(mode=0o700)
        (self.data_root / "existing.db").write_bytes(b"existing-database")
        manager = self.manager(installation_mode="adopt")
        original_lstat = Path.lstat
        root_inode = original_lstat(self.data_root).st_ino
        device = 11

        class RemountedMetadata:
            def __init__(self, metadata):
                self._metadata = metadata
                self.st_dev = device
                self.st_birthtime = 1_786_203_815.3676865

            def __getattr__(self, name):
                return getattr(self._metadata, name)

        def remounted_lstat(path):
            metadata = original_lstat(path)
            if path == self.data_root:
                return RemountedMetadata(metadata)
            return metadata

        with mock.patch.object(Path, "lstat", remounted_lstat):
            installed = manager.install(self.release("1.0.0"), version="1.0.0")
            device = 12
            verified = manager.verify()

        self.assertEqual(verified["status"], "verified")
        self.assertEqual(
            verified["data_identity_digest"], installed["data_identity_digest"]
        )
        self.assertEqual(
            installed["data_identity_digest"],
            digest(
                {
                    "schema_version": 2,
                    "path": str(self.data_root),
                    "inode": root_inode,
                    "birthtime_ns": "1786203815367686500",
                }
            ),
        )

    def test_launchd_data_identity_owns_decimal_precision(self) -> None:
        self.data_root.mkdir(mode=0o700)
        (self.data_root / "existing.db").write_bytes(b"existing-database")
        manager = self.manager(installation_mode="adopt")

        with localcontext() as decimal_context:
            decimal_context.prec = 10
            installed = manager.install(self.release("1.0.0"), version="1.0.0")
        verified = manager.verify()

        self.assertEqual(
            installed["data_identity_digest"],
            verified["data_identity_digest"],
        )

    def test_launchd_verify_rejects_data_root_inode_or_birthtime_change(
        self,
    ) -> None:
        self.data_root.mkdir(mode=0o700)
        (self.data_root / "existing.db").write_bytes(b"existing-database")
        manager = self.manager(installation_mode="adopt")
        original_lstat = Path.lstat
        inode_delta = 0
        birthtime_delta = 0.0

        class ChangedMetadata:
            def __init__(self, metadata):
                self._metadata = metadata
                self.st_ino = metadata.st_ino + inode_delta
                self.st_birthtime = 1_786_203_815.3676865 + birthtime_delta

            def __getattr__(self, name):
                return getattr(self._metadata, name)

        def changed_lstat(path):
            metadata = original_lstat(path)
            if path == self.data_root:
                return ChangedMetadata(metadata)
            return metadata

        with mock.patch.object(Path, "lstat", changed_lstat):
            manager.install(self.release("1.0.0"), version="1.0.0")
            for inode_delta, birthtime_delta in ((1, 0.0), (0, 1.0)):
                with (
                    self.subTest(
                        inode_delta=inode_delta,
                        birthtime_delta=birthtime_delta,
                    ),
                    self.assertRaisesRegex(
                        PortableInstallError,
                        "data identity changed",
                    ),
                ):
                    manager.verify()

    def test_systemd_data_identity_keeps_legacy_device_inode_projection(
        self,
    ) -> None:
        self.data_root.mkdir(mode=0o700)
        (self.data_root / "existing.db").write_bytes(b"existing-database")
        metadata = self.data_root.lstat()
        manager = self.manager(
            platform="systemd-user",
            installation_mode="adopt",
        )

        installed = manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertEqual(
            installed["data_identity_digest"],
            digest(
                {
                    "path": str(self.data_root),
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                }
            ),
        )

    def test_launchd_data_identity_requires_a_finite_birthtime(self) -> None:
        self.data_root.mkdir(mode=0o700)
        (self.data_root / "existing.db").write_bytes(b"existing-database")
        original_lstat = Path.lstat
        release = self.release("1.0.0")

        class InvalidBirthtimeMetadata:
            def __init__(self, metadata, birthtime):
                self._metadata = metadata
                if birthtime is not None:
                    self.st_birthtime = birthtime

            def __getattr__(self, name):
                if name == "st_birthtime":
                    raise AttributeError(name)
                return getattr(self._metadata, name)

        for birthtime in (None, float("nan"), float("inf"), -1.0):
            with self.subTest(birthtime=birthtime):
                manager = self.manager(installation_mode="adopt")

                def invalid_birthtime_lstat(path):
                    metadata = original_lstat(path)
                    if path == self.data_root:
                        return InvalidBirthtimeMetadata(metadata, birthtime)
                    return metadata

                with (
                    mock.patch.object(Path, "lstat", invalid_birthtime_lstat),
                    self.assertRaisesRegex(
                        PortableInstallError,
                        "launchd data root birth time is unavailable",
                    ),
                ):
                    manager.install(release, version="1.0.0")

    def test_adoption_rechecks_the_bound_data_root_before_activation(self) -> None:
        self.data_root.mkdir(mode=0o700)
        (self.data_root / "existing.db").write_bytes(b"existing-database")
        manager = self.manager(installation_mode="adopt")
        original_install_launchers = manager._install_launchers

        def replace_data_root(payloads):
            owned = original_install_launchers(payloads)
            self.data_root.rename(self.root / "displaced-data")
            self.data_root.mkdir(mode=0o700)
            return owned

        with (
            mock.patch.object(
                manager, "_install_launchers", side_effect=replace_data_root
            ),
            self.assertRaisesRegex(PortableInstallError, "data identity changed"),
        ):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertFalse(
            any(
                call[:2] == ("/bin/launchctl", "bootstrap")
                for call in self.runner.calls
            )
        )

    def test_failed_upgrade_restores_verified_release_and_service_state(self) -> None:
        v1 = self.release("1.0.0")
        v2 = self.release("2.0.0")
        manager = self.manager(
            health_runner=lambda _check, release: release["version"] != "2.0.0"
        )
        manager.install(v1, version="1.0.0")
        before = manager.verify()["current"]

        with self.assertRaisesRegex(PortableInstallError, "health verification failed"):
            self.upgrade(manager, v2, version="2.0.0")

        verification = manager.verify()
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(verification["current"], before)
        self.assertFalse(verification["transaction_pending"])

    def test_failed_fresh_install_removes_owned_runtime_but_preserves_data(
        self,
    ) -> None:
        manager = self.manager(health_runner=lambda _check, _release: False)

        with (
            mock.patch(
                "hindsight_memory_control_plane.portable_install._fsync_directory",
                wraps=portable_install_module._fsync_directory,
            ) as fsync_directory,
            self.assertRaisesRegex(PortableInstallError, "health verification failed"),
        ):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertFalse(self.install_root.exists())
        self.assertTrue(self.data_root.is_dir())
        self.assertEqual(list(self.service_root.glob("*")), [])
        observed = [call.args[0] for call in fsync_directory.call_args_list]
        self.assertIn(self.service_root, observed)
        self.assertIn(self.install_root / "releases", observed)
        self.assertIn(self.install_root.parent, observed)
        self.assertEqual(observed[-1], self.state_root)

    def test_failed_fresh_systemd_install_reloads_after_manifest_restore(
        self,
    ) -> None:
        manager = self.manager(
            platform="systemd-user",
            health_runner=lambda _check, _release: False,
        )
        events: list[str] = []
        original_restore = manager._restore_manifests

        def restore(preimage):
            events.append("restore")
            original_restore(preimage)

        def runner(argv):
            if argv == ("/usr/bin/systemctl", "--user", "daemon-reload"):
                events.append("reload")
            return self.runner(argv)

        manager._restore_manifests = restore
        manager._command_runner = runner

        with self.assertRaisesRegex(
            PortableInstallError, "health verification failed"
        ):
            manager.install(self.release("1.0.0"), version="1.0.0")

        daemon_reload = ("/usr/bin/systemctl", "--user", "daemon-reload")
        self.assertEqual(self.runner.calls.count(daemon_reload), 3)
        self.assertEqual(events[-2:], ["restore", "reload"])

    def test_lifecycle_rejects_root_before_mutation(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        with (
            mock.patch.object(os, "getuid", return_value=0),
            mock.patch.object(os, "geteuid", return_value=0),
            self.assertRaisesRegex(PortableInstallError, "unprivileged"),
        ):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertFalse(self.install_root.exists())
        self.assertFalse(self.state_root.exists())
        self.assertEqual(self.runner.calls, [])

    def test_every_lifecycle_command_rejects_mismatched_user_identity(self) -> None:
        release_v1 = self.release("1.0.0")
        release_v2 = self.release("2.0.0")
        manager = self.manager(health_runner=lambda _check, _release: True)
        installed = manager.install(release_v1, version="1.0.0")
        calls_before = list(self.runner.calls)
        operations = {
            "install": lambda: manager.install(release_v1, version="1.0.0"),
            "upgrade": lambda: manager.upgrade(
                release_v2,
                version="2.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            ),
            "verify": manager.verify,
            "rollback": lambda: manager.rollback(
                expected_current_digest=installed["release_digest"]
            ),
            "uninstall": manager.uninstall,
        }

        for name, operation in operations.items():
            with (
                self.subTest(name=name),
                mock.patch.object(os, "getuid", return_value=501),
                mock.patch.object(os, "geteuid", return_value=502),
                self.assertRaisesRegex(PortableInstallError, "identity"),
            ):
                operation()

        self.assertEqual(self.runner.calls, calls_before)
        self.assertEqual(manager.verify()["current"]["version"], "1.0.0")

    def test_fresh_recovery_keeps_an_external_journal_until_cleanup_finishes(
        self,
    ) -> None:
        def interrupt(_check, _release):
            raise KeyboardInterrupt

        interrupted = self.manager(health_runner=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            interrupted.install(self.release("1.0.0"), version="1.0.0")

        self.assertEqual(interrupted._transaction_path.parent, self.state_root)
        for path in self.install_root.rglob("*"):
            if path.is_dir():
                path.chmod(0o700)
            elif path.is_file():
                path.chmod(0o600)
        shutil.rmtree(self.install_root)
        self.assertTrue(interrupted._transaction_path.is_file())

        with self.assertRaisesRegex(PortableInstallError, "installation is absent"):
            self.manager().verify()

        self.assertFalse(interrupted._transaction_path.exists())
        self.assertFalse(self.install_root.exists())

    def test_fresh_recovery_refuses_to_clean_up_after_a_real_bootout_failure(
        self,
    ) -> None:
        def interrupt(_check, _release):
            raise KeyboardInterrupt

        interrupted = self.manager(health_runner=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            interrupted.install(self.release("1.0.0"), version="1.0.0")

        recovered = self.manager()
        with (
            mock.patch.object(
                PortableInstallationManager,
                "_deactivate_services",
                side_effect=_ManagedServiceCommandError(5),
            ),
            self.assertRaisesRegex(
                PortableInstallError,
                "could not stop candidate services",
            ),
        ):
            recovered.verify()

        self.assertTrue(recovered._transaction_path.is_file())
        self.assertTrue(self.install_root.is_dir())

    def test_fresh_systemd_recovery_skips_units_that_were_never_published(
        self,
    ) -> None:
        interrupted = self.manager(platform="systemd-user")
        with (
            mock.patch.object(
                interrupted, "_publish_release_record", side_effect=KeyboardInterrupt
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            interrupted.install(self.release("1.0.0"), version="1.0.0")
        data = self.config_data(platform="systemd-user")
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        missing = MissingSystemdRunner()
        recovered = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=missing,
            health_runner=lambda _check, _release: True,
        )

        with self.assertRaisesRegex(PortableInstallError, "installation is absent"):
            recovered.verify()

        self.assertFalse(recovered._transaction_path.exists())
        self.assertFalse(self.install_root.exists())
        self.assertFalse(
            any(
                call[2] in {"stop", "disable"}
                for call in missing.calls
                if len(call) > 2
            )
        )

    def test_interrupted_upgrade_recovers_last_verified_generation(self) -> None:
        v1 = self.release("1.0.0")
        v2 = self.release("2.0.0")
        manager = self.manager()
        manager.install(v1, version="1.0.0")

        def interrupt(_check, release):
            if release["version"] == "2.0.0":
                raise KeyboardInterrupt
            return True

        interrupted = self.manager(health_runner=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.upgrade(interrupted, v2, version="2.0.0")

        recovered = self.manager()
        verification = recovered.verify()
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(verification["current"]["version"], "1.0.0")
        self.assertFalse(verification["transaction_pending"])

    def test_upgrade_journals_release_and_launcher_preimages_before_mutation(
        self,
    ) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        launcher = self.install_root / "launcher.py"
        launcher_preimage = launcher.read_bytes()
        releases_preimage = {
            path.name for path in (self.install_root / "releases").iterdir()
        }
        original_install_launchers = manager._install_launchers

        def interrupt_after_launcher_mutation(payloads):
            original_install_launchers(payloads)
            launcher.chmod(0o700)
            launcher.write_bytes(b"mutated launcher")
            launcher.chmod(0o500)
            raise KeyboardInterrupt

        with (
            mock.patch.object(
                manager,
                "_install_launchers",
                side_effect=interrupt_after_launcher_mutation,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.upgrade(manager, self.release("2.0.0"), version="2.0.0")

        recovered = self.manager()
        verification = recovered.verify()

        self.assertEqual(verification["current"]["version"], "1.0.0")
        self.assertEqual(launcher.read_bytes(), launcher_preimage)
        self.assertEqual(
            {path.name for path in (self.install_root / "releases").iterdir()},
            releases_preimage,
        )

    def test_upgrade_recovers_a_partially_staged_release(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")

        def interrupt_release_copy(_source, release, temporary):
            temporary.mkdir(parents=True)
            marker = temporary / ".hindsight-staging-owner"
            marker.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "release_digest": release["release_digest"],
                        "staging_name": temporary.name,
                    }
                ),
                encoding="utf-8",
            )
            marker.chmod(0o600)
            (temporary / "partial").write_bytes(b"partial")
            raise KeyboardInterrupt

        with (
            mock.patch.object(
                manager, "_publish_release_record", side_effect=interrupt_release_copy
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.upgrade(manager, self.release("2.0.0"), version="2.0.0")

        with mock.patch(
            "hindsight_memory_control_plane.portable_install._fsync_directory",
            wraps=portable_install_module._fsync_directory,
        ) as fsync_directory:
            verification = self.manager().verify()

        self.assertEqual(verification["current"]["version"], "1.0.0")
        self.assertEqual(
            list((self.install_root / "releases").glob(".*.candidate-*")), []
        )
        observed = [call.args[0] for call in fsync_directory.call_args_list]
        self.assertIn(self.install_root / "releases", observed)
        self.assertEqual(observed[-1], self.state_root)

    def test_upgrade_recovers_after_the_internal_staging_marker_is_removed(
        self,
    ) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        original_fsync = portable_install_module._fsync_directory

        def interrupt_after_marker_removal(path: Path) -> None:
            marker = path / ".hindsight-staging-owner"
            if (
                path.parent == self.install_root / "releases"
                and ".candidate-" in path.name
                and path.is_dir()
                and not marker.exists()
            ):
                raise KeyboardInterrupt
            original_fsync(path)

        with (
            mock.patch(
                "hindsight_memory_control_plane.portable_install._fsync_directory",
                side_effect=interrupt_after_marker_removal,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.upgrade(manager, self.release("2.0.0"), version="2.0.0")

        releases = self.install_root / "releases"
        self.assertNotEqual(list(releases.glob(".*.candidate-*.owner")), [])

        verification = self.manager().verify()

        self.assertEqual(verification["current"]["version"], "1.0.0")
        self.assertEqual(list(releases.glob(".*.candidate-*")), [])

    def test_recovery_refuses_a_symlink_in_release_staging(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")

        def interrupt_release_copy(_source, release, temporary):
            temporary.mkdir(parents=True)
            marker = temporary / ".hindsight-staging-owner"
            marker.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "release_digest": release["release_digest"],
                        "staging_name": temporary.name,
                    }
                ),
                encoding="utf-8",
            )
            marker.chmod(0o600)
            (temporary / "escape").symlink_to(outside, target_is_directory=True)
            raise KeyboardInterrupt

        with (
            mock.patch.object(
                manager, "_publish_release_record", side_effect=interrupt_release_copy
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.upgrade(manager, self.release("2.0.0"), version="2.0.0")

        with self.assertRaisesRegex(
            PortableInstallError, "release staging identity is invalid"
        ):
            self.manager().verify()

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_recovery_rejects_a_release_path_outside_the_install_root(self) -> None:
        manager = self.manager()
        installed = manager.install(self.release("1.0.0"), version="1.0.0")
        with (
            mock.patch.object(
                manager, "_publish_release_record", side_effect=KeyboardInterrupt
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.upgrade(
                self.release("2.0.0"),
                version="2.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )
        outside = self.root / "outside-release"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        journal = json.loads(manager._transaction_path.read_text(encoding="utf-8"))
        journal["candidate"]["release_path"] = str(outside)
        manager._transaction_path.write_text(
            json.dumps(journal, sort_keys=True), encoding="utf-8"
        )

        with self.assertRaisesRegex(PortableInstallError, "release path is invalid"):
            manager.verify()

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        self.assertTrue(manager._transaction_path.exists())

    def test_recovery_rejects_a_preimage_path_outside_managed_roots(self) -> None:
        manager = self.manager()
        installed = manager.install(self.release("1.0.0"), version="1.0.0")
        with (
            mock.patch.object(
                manager, "_publish_release_record", side_effect=KeyboardInterrupt
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.upgrade(
                self.release("2.0.0"),
                version="2.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )
        outside = self.root / "outside-preimage"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        journal = json.loads(manager._transaction_path.read_text(encoding="utf-8"))
        journal["manifest_preimage"][str(sentinel)] = None
        manager._transaction_path.write_text(
            json.dumps(journal, sort_keys=True), encoding="utf-8"
        )

        with self.assertRaisesRegex(PortableInstallError, "preimage is invalid"):
            manager.verify()

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        self.assertTrue(manager._transaction_path.exists())

    def test_recovery_rejects_corrupt_preimages_before_mutation(self) -> None:
        manager = self.manager()
        installed = manager.install(self.release("1.0.0"), version="1.0.0")
        with (
            mock.patch.object(
                manager, "_publish_release_record", side_effect=KeyboardInterrupt
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.upgrade(
                self.release("2.0.0"),
                version="2.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )
        original_journal = json.loads(
            manager._transaction_path.read_text(encoding="utf-8")
        )
        launcher = self.install_root / "launcher.py"
        manifest = next(self.service_root.glob("*.plist"))
        active = manager._active_path
        protected_bytes = {
            launcher: launcher.read_bytes(),
            manifest: manifest.read_bytes(),
            active: active.read_bytes(),
        }
        manifest_key = next(
            key
            for key, value in original_journal["manifest_preimage"].items()
            if value is not None
        )
        mutations = {
            "launcher": lambda journal: journal["install_preimage"].__setitem__(
                str(launcher), base64.b64encode(b"corrupt launcher").decode("ascii")
            ),
            "manifest": lambda journal: journal["manifest_preimage"].__setitem__(
                manifest_key, base64.b64encode(b"corrupt manifest").decode("ascii")
            ),
            "active": lambda journal: journal.__setitem__(
                "active_preimage", base64.b64encode(b"{}\n").decode("ascii")
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                journal = json.loads(json.dumps(original_journal))
                mutate(journal)
                manager._transaction_path.write_text(
                    json.dumps(journal, sort_keys=True), encoding="utf-8"
                )
                calls_before = list(self.runner.calls)

                with self.assertRaisesRegex(PortableInstallError, "preimage.*invalid"):
                    manager.verify()

                self.assertEqual(self.runner.calls, calls_before)
                for path, content in protected_bytes.items():
                    self.assertEqual(path.read_bytes(), content)

    def test_recovery_rejects_corrupt_candidate_record_before_mutation(self) -> None:
        manager = self.manager()
        with (
            mock.patch.object(
                manager, "_publish_manifests", side_effect=KeyboardInterrupt
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.install(self.release("1.0.0"), version="1.0.0")
        protected_paths = (
            manager._state_path,
            self.install_root / "launcher.py",
            self.install_root / "managed-config.json",
        )
        protected = {path: path.read_bytes() for path in protected_paths}
        journal = json.loads(manager._transaction_path.read_text(encoding="utf-8"))
        journal["candidate"]["manifest"]["files"][0]["sha256"] = "f" * 64
        manager._transaction_path.write_text(
            json.dumps(journal, sort_keys=True), encoding="utf-8"
        )
        calls_before = list(self.runner.calls)

        with self.assertRaisesRegex(PortableInstallError, "release record"):
            manager.verify()

        self.assertEqual(self.runner.calls, calls_before)
        for path, content in protected.items():
            self.assertEqual(path.read_bytes(), content)

    def test_recovery_rejects_unbound_staging_directory_before_mutation(self) -> None:
        manager = self.manager()
        with (
            mock.patch.object(
                manager, "_publish_release_record", side_effect=KeyboardInterrupt
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.install(self.release("1.0.0"), version="1.0.0")
        journal = json.loads(manager._transaction_path.read_text(encoding="utf-8"))
        candidate_root = self.install_root / journal["candidate"]["release_path"]
        unbound = candidate_root.parent / f".{candidate_root.name}.candidate-unbound"
        unbound.parent.mkdir(parents=True)
        unbound.mkdir()
        sentinel = unbound / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        journal["release_staging_path"] = str(unbound)
        manager._transaction_path.write_text(
            json.dumps(journal, sort_keys=True), encoding="utf-8"
        )
        calls_before = list(self.runner.calls)

        with self.assertRaisesRegex(PortableInstallError, "staging"):
            manager.verify()

        self.assertEqual(self.runner.calls, calls_before)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_recovery_rejects_unowned_candidate_manifests_before_mutation(
        self,
    ) -> None:
        manager = self.manager()
        installed = manager.install(self.release("1.0.0"), version="1.0.0")
        with (
            mock.patch.object(manager, "_write_state", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.upgrade(
                self.release("2.0.0"),
                version="2.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )
        sentinel = self.service_root / "unrelated.plist"
        sentinel.write_text("preserve", encoding="utf-8")
        sentinel.chmod(0o600)
        protected = {
            manager._state_path: manager._state_path.read_bytes(),
            manager._active_path: manager._active_path.read_bytes(),
            self.install_root / "launcher.py": (
                self.install_root / "launcher.py"
            ).read_bytes(),
            next(self.service_root.glob("io.*.plist")): next(
                self.service_root.glob("io.*.plist")
            ).read_bytes(),
            sentinel: sentinel.read_bytes(),
        }
        journal = json.loads(manager._transaction_path.read_text(encoding="utf-8"))
        journal["candidate_manifest_paths"].append(str(sentinel))
        journal["manifest_preimage"][str(sentinel)] = None
        manager._transaction_path.write_text(
            json.dumps(journal, sort_keys=True), encoding="utf-8"
        )
        calls_before = list(self.runner.calls)

        with self.assertRaisesRegex(PortableInstallError, "candidate manifest paths"):
            manager.verify()

        self.assertEqual(self.runner.calls, calls_before)
        for path, content in protected.items():
            self.assertEqual(path.read_bytes(), content)

    def test_recovery_validates_complete_prestate_before_mutation(self) -> None:
        manager = self.manager()
        installed = manager.install(self.release("1.0.0"), version="1.0.0")
        with (
            mock.patch.object(manager, "_write_state", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.upgrade(
                self.release("2.0.0"),
                version="2.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )
        protected_paths = (
            manager._state_path,
            manager._active_path,
            self.install_root / "launcher.py",
            self.install_root / "managed-config.json",
            next(self.service_root.glob("*.plist")),
        )
        protected = {path: path.read_bytes() for path in protected_paths}
        journal = json.loads(manager._transaction_path.read_text(encoding="utf-8"))
        journal["prior_state"]["config_digest"] = "f" * 64
        manager._transaction_path.write_text(
            json.dumps(journal, sort_keys=True), encoding="utf-8"
        )
        calls_before = list(self.runner.calls)

        with self.assertRaisesRegex(PortableInstallError, "binding generation"):
            manager.verify()

        self.assertEqual(self.runner.calls, calls_before)
        for path, content in protected.items():
            self.assertEqual(path.read_bytes(), content)

    def test_recovery_rejects_conflicting_transaction_journals(self) -> None:
        manager = self.manager()
        installed = manager.install(self.release("1.0.0"), version="1.0.0")
        with (
            mock.patch.object(
                manager, "_publish_release_record", side_effect=KeyboardInterrupt
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.upgrade(
                self.release("2.0.0"),
                version="2.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )
        manager._uninstall_transaction_path.write_text("{}", encoding="utf-8")
        protected = {
            manager._state_path: manager._state_path.read_bytes(),
            manager._active_path: manager._active_path.read_bytes(),
            next(self.service_root.glob("*.plist")): next(
                self.service_root.glob("*.plist")
            ).read_bytes(),
        }
        calls_before = list(self.runner.calls)

        with self.assertRaisesRegex(PortableInstallError, "conflicting"):
            manager.verify()

        self.assertEqual(self.runner.calls, calls_before)
        for path, content in protected.items():
            self.assertEqual(path.read_bytes(), content)

    def test_pending_candidate_cannot_restart_without_the_install_manager(
        self,
    ) -> None:
        data = self.config_data()
        data["services"][0]["credentials"] = []
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        config = InstallationConfig.load(data, source_path=self.config_path)
        manager = PortableInstallationManager(
            config,
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")

        def interrupt(_check, release):
            if release["version"] == "2.0.0":
                raise KeyboardInterrupt
            return True

        interrupted = PortableInstallationManager(
            config,
            command_runner=self.runner,
            health_runner=interrupt,
        )
        with self.assertRaises(KeyboardInterrupt):
            self.upgrade(interrupted, self.release("2.0.0"), version="2.0.0")

        completed = subprocess.run(
            interrupted._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            b"pending candidate has no live install manager", completed.stderr
        )

    def test_pending_candidate_rejects_a_substituted_lifecycle_lock(self) -> None:
        data = self.config_data()
        data["services"][0]["credentials"] = []
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        config = InstallationConfig.load(data, source_path=self.config_path)
        manager = PortableInstallationManager(
            config,
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")

        def interrupt(_check, release):
            if release["version"] == "2.0.0":
                raise KeyboardInterrupt
            return True

        interrupted = PortableInstallationManager(
            config, command_runner=self.runner, health_runner=interrupt
        )
        with self.assertRaises(KeyboardInterrupt):
            self.upgrade(interrupted, self.release("2.0.0"), version="2.0.0")
        lock = self.state_root / "portable-install.lock"
        lock.unlink()
        sentinel = self.root / "foreign-lock"
        sentinel.write_text("preserve", encoding="utf-8")
        lock.symlink_to(sentinel)

        completed = subprocess.run(
            interrupted._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_explicit_rollback_uses_last_known_good_release(self) -> None:
        v1 = self.release("1.0.0")
        v2 = self.release("2.0.0")
        manager = self.manager()
        first = manager.install(v1, version="1.0.0")
        second = self.upgrade(manager, v2, version="2.0.0")

        result = manager.rollback(expected_current_digest=second["release_digest"])

        self.assertEqual(result["status"], "rolled-back")
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(result["release_digest"], first["release_digest"])
        self.assertEqual(manager.verify()["current"]["version"], "1.0.0")

    def test_rollback_rejects_resolver_drift_before_mutation(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        current = self.upgrade(
            manager,
            self.release("2.0.0"),
            version="2.0.0",
        )
        calls_before = list(self.runner.calls)
        self.resolver.chmod(0o700)
        self.resolver.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
        self.resolver.chmod(0o500)

        with self.assertRaisesRegex(
            PortableInstallError,
            "credential resolver digest mismatch",
        ):
            manager.rollback(
                expected_current_digest=current["release_digest"],
            )

        self.assertEqual(self.runner.calls, calls_before)
        self.assertFalse(manager._transaction_path.exists())
        self.assertEqual(manager._load_state()["current"]["version"], "2.0.0")

    def test_rollback_disables_bound_harness_authority_before_quiesce(
        self,
    ) -> None:
        reconciler = self.root / "harness-reconcile"
        reconciler.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        reconciler.chmod(0o700)
        reconcile_config = self.root / "harness-reconcile.json"
        reconcile_config.write_text("{}\n", encoding="utf-8")
        reconcile_config.chmod(0o600)
        data = self.config_data()
        for collection in ("services", "timers", "health_checks"):
            for surface in data[collection]:
                surface["environment"].update(
                    {
                        "HINDSIGHT_MEMORY_HARNESS_RECONCILER": str(
                            reconciler
                        ),
                        "HINDSIGHT_MEMORY_HARNESS_RECONCILE_CONFIG": str(
                            reconcile_config
                        ),
                    }
                )
        self.config_path.write_text(
            json.dumps(data, sort_keys=True),
            encoding="utf-8",
        )
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")
        current = self.upgrade(
            manager,
            self.release("2.0.0"),
            version="2.0.0",
        )
        self.runner.calls.clear()

        manager.rollback(expected_current_digest=current["release_digest"])

        disable = (str(reconciler), "disable", str(reconcile_config))
        self.assertIn(disable, self.runner.calls)
        first_quiesce = next(
            index
            for index, call in enumerate(self.runner.calls)
            if call[:2] == ("/bin/launchctl", "bootout")
        )
        self.assertLess(self.runner.calls.index(disable), first_quiesce)

    def test_rollback_rejects_inconsistent_harness_authority_binding(
        self,
    ) -> None:
        reconciler = self.root / "harness-reconcile"
        reconciler.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        reconciler.chmod(0o700)
        data = self.config_data()
        data["services"][0]["environment"].update(
            {
                "HINDSIGHT_MEMORY_HARNESS_RECONCILER": str(reconciler),
                "HINDSIGHT_MEMORY_HARNESS_RECONCILE_CONFIG": str(
                    self.root / "one.json"
                ),
            }
        )
        self.config_path.write_text(
            json.dumps(data, sort_keys=True),
            encoding="utf-8",
        )
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")
        current = self.upgrade(
            manager,
            self.release("2.0.0"),
            version="2.0.0",
        )

        with self.assertRaisesRegex(
            PortableInstallError,
            "harness rollback authority binding is inconsistent",
        ):
            manager.rollback(
                expected_current_digest=current["release_digest"],
            )

    def test_rollback_uses_installed_harness_binding_not_caller_source(
        self,
    ) -> None:
        reconciler = self.root / "installed-harness-reconcile"
        reconciler.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        reconciler.chmod(0o700)
        reconcile_config = self.root / "installed-harness-reconcile.json"
        reconcile_config.write_text("{}\n", encoding="utf-8")
        reconcile_config.chmod(0o600)
        installed_data = self.config_data()
        for collection in ("services", "timers", "health_checks"):
            for surface in installed_data[collection]:
                surface["environment"].update(
                    {
                        "HINDSIGHT_MEMORY_HARNESS_RECONCILER": str(
                            reconciler
                        ),
                        "HINDSIGHT_MEMORY_HARNESS_RECONCILE_CONFIG": str(
                            reconcile_config
                        ),
                    }
                )
        self.config_path.write_text(
            json.dumps(installed_data, sort_keys=True),
            encoding="utf-8",
        )
        installed_manager = PortableInstallationManager(
            InstallationConfig.load(
                installed_data,
                source_path=self.config_path,
            ),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        installed_manager.install(
            self.release("1.0.0"),
            version="1.0.0",
        )
        current = self.upgrade(
            installed_manager,
            self.release("2.0.0"),
            version="2.0.0",
        )

        caller_data = self.config_data()
        self.config_path.write_text(
            json.dumps(caller_data, sort_keys=True),
            encoding="utf-8",
        )
        caller = PortableInstallationManager(
            InstallationConfig.load(
                caller_data,
                source_path=self.config_path,
            ),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        self.runner.calls.clear()

        caller.rollback(
            expected_current_digest=current["release_digest"],
        )

        self.assertIn(
            (str(reconciler), "disable", str(reconcile_config)),
            self.runner.calls,
        )

    def test_interrupted_rollback_recovers_the_prestate(self) -> None:
        v1 = self.release("1.0.0")
        v2 = self.release("2.0.0")
        manager = self.manager()
        manager.install(v1, version="1.0.0")
        current = self.upgrade(manager, v2, version="2.0.0")

        def interrupt(_check, release):
            if release["version"] == "1.0.0":
                raise KeyboardInterrupt
            return True

        interrupted = self.manager(health_runner=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            interrupted.rollback(expected_current_digest=current["release_digest"])

        verification = self.manager().verify()
        self.assertEqual(verification["current"]["version"], "2.0.0")
        self.assertFalse(verification["transaction_pending"])

    def test_interrupted_launchd_rollback_tolerates_absent_candidate_jobs(
        self,
    ) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        current = self.upgrade(manager, self.release("2.0.0"), version="2.0.0")

        def interrupt(_check, release):
            if release["version"] == "1.0.0":
                raise KeyboardInterrupt
            return True

        interrupted = self.manager(health_runner=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            interrupted.rollback(expected_current_digest=current["release_digest"])
        config = InstallationConfig.read(self.config_path)
        recovered = PortableInstallationManager(
            config,
            command_runner=AbsentLaunchdRunner(),
            health_runner=lambda _check, _release: True,
        )

        verification = recovered.verify()

        self.assertEqual(verification["current"]["version"], "2.0.0")

    def test_interrupted_launchd_rollback_tolerates_esrch_absence(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        manager._command_runner = EsrchBootoutRunner()

        manager._deactivate_services(absent_ok=True)

    def test_launchd_recovery_preserves_non_absence_bootout_failures(self) -> None:
        data = self.config_data()
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=(runner := FailedLaunchdRunner()),
            health_runner=lambda _check, _release: True,
        )
        self.service_root.mkdir()
        for path, content in manager._rendered_manifests().items():
            path.write_bytes(content)
            runner.launchd_jobs[plistlib.loads(content)["Label"]] = path

        with self.assertRaisesRegex(
            PortableInstallError, "managed service command failed"
        ):
            manager._deactivate_services(absent_ok=True)

    def test_verify_requires_managed_health(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")

        unhealthy = self.manager(health_runner=lambda _check, _release: False)

        with self.assertRaisesRegex(PortableInstallError, "health verification failed"):
            unhealthy.verify()

    def test_verify_requires_launchd_jobs_to_remain_loaded(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        manager._command_runner = MissingLaunchdRunner()

        with self.assertRaisesRegex(PortableInstallError, "launchd job is absent"):
            manager.verify()

    def test_verify_requires_systemd_units_to_be_enabled_and_active(self) -> None:
        manager = self.manager(platform="systemd-user")
        manager.install(self.release("1.0.0"), version="1.0.0")
        manager._command_runner = InactiveSystemdRunner()

        with self.assertRaisesRegex(
            PortableInstallError, "systemd unit is not enabled"
        ):
            manager.verify()

    def test_managed_health_uses_isolated_python_and_account_identity(self) -> None:
        installed = self.manager(health_runner=lambda _check, _release: True)
        installed.install(self.release("1.0.0"), version="1.0.0")
        manager = self.manager()
        expected_user = pwd.getpwuid(os.geteuid()).pw_name
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o700)
        bus = f"unix:path={runtime}/bus"

        def completed(argv, **_kwargs):
            stdout = None
            if "-c" in argv:
                stdout = b"hindsight-managed-python:3:14:0\n"
            return subprocess.CompletedProcess(argv, 0, stdout=stdout)

        health_process = mock.Mock()
        health_process.wait.return_value = 0
        health_process.pid = 12345

        with (
            mock.patch(
                "hindsight_memory_control_plane.portable_install.subprocess.run",
                side_effect=completed,
            ),
            mock.patch(
                "hindsight_memory_control_plane.portable_install.subprocess.Popen",
                return_value=health_process,
            ) as popen,
            mock.patch(
                "hindsight_memory_control_plane.portable_install.pwd.getpwuid",
                wraps=pwd.getpwuid,
            ) as account_lookup,
            mock.patch.dict(
                os.environ,
                {
                    "XDG_RUNTIME_DIR": str(runtime),
                    "DBUS_SESSION_BUS_ADDRESS": bus,
                    "SECRET_CANARY": "must-not-leak",
                },
            ),
        ):
            manager.verify()

        argv = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(argv[1], "-I")
        self.assertEqual(environment["USER"], expected_user)
        self.assertEqual(environment["LOGNAME"], expected_user)
        self.assertEqual(environment["XDG_RUNTIME_DIR"], str(runtime))
        self.assertEqual(environment["DBUS_SESSION_BUS_ADDRESS"], bus)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("SECRET_CANARY", environment)
        account_lookup.assert_called_once_with(os.geteuid())

    def test_default_health_retries_until_the_managed_stack_is_ready(self) -> None:
        installed = self.manager(health_runner=lambda _check, _release: True)
        installed.install(self.release("1.0.0"), version="1.0.0")
        manager = self.manager()
        starting = mock.Mock()
        starting.wait.return_value = 1
        starting.pid = 12345
        healthy = mock.Mock()
        healthy.wait.return_value = 0
        healthy.pid = 12346

        with mock.patch(
            "hindsight_memory_control_plane.portable_install.subprocess.Popen",
            side_effect=(starting, healthy),
        ) as popen, mock.patch(
            "hindsight_memory_control_plane.portable_install.time.sleep"
        ):
            result = manager._default_health_runner(
                manager.config.health_checks[0].to_dict(),
                manager._load_state()["current"],
            )

        self.assertTrue(result)
        self.assertEqual(popen.call_count, 2)

    def test_health_timeout_kills_the_credential_resolver_process_group(self) -> None:
        pid_path = self.root / "resolver.pid"
        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/bin/sh\n"
            f"/bin/sh -c 'trap \"\" TERM; exec /bin/sleep 30' &\n"
            f"printf '%s' \"$!\" > {str(pid_path)!r}\n"
            "wait\n",
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)
        data = self.config_data()
        data["credential_resolver"]["sha256"] = file_sha256(self.resolver)
        data["health_checks"][0]["credentials"] = [
            {
                "environment": "HINDSIGHT_API_KEY",
                "locator": "pass://hindsight/data-plane",
            }
        ]
        data["health_checks"][0]["timeout_seconds"] = 1
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertFalse(
            manager._default_health_runner(
                manager.config.health_checks[0].to_dict(),
                manager._load_state()["current"],
            )
        )
        resolver_pid = int(pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while True:
            try:
                observed = subprocess.run(
                    ["/bin/ps", "-o", "state=", "-p", str(resolver_pid)],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except PermissionError:
                self.skipTest("process-state inspection is sandbox-restricted")
            if observed.returncode != 0 or observed.stdout.strip().startswith("Z"):
                break
            if time.monotonic() >= deadline:
                self.fail("credential resolver survived its health process group")
            time.sleep(0.01)

    def test_default_health_launcher_supplies_the_effective_account(self) -> None:
        release = self.release("1.0.0")
        health = release / "bin" / "account-health"
        account = pwd.getpwuid(os.geteuid())
        health.write_text(
            "#!/bin/sh\n"
            f'[ "$USER" = {account.pw_name!r} ] || exit 11\n'
            f'[ "$LOGNAME" = {account.pw_name!r} ] || exit 12\n'
            f'[ "$HOME" = {account.pw_dir!r} ] || exit 13\n',
            encoding="utf-8",
        )
        health.chmod(0o755)
        data = self.config_data()
        data["health_checks"][0]["entrypoint"] = "bin/account-health"
        data["health_checks"][0]["environment"].update(
            {"HOME": "/wrong", "USER": "wrong", "LOGNAME": "wrong"}
        )
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
        )

        manager.install(release, version="1.0.0")

        self.assertEqual(manager.verify()["managed_health"], "healthy")

    def test_runtime_executes_the_exact_configured_credential_resolver(self) -> None:
        marker = self.root / "resolver-path"
        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/bin/sh\n"
            f"printf '%s' \"$0\" > {str(marker)!r}\n"
            "printf '%s\\n' "
            '\'{"schema_version":1,"values":{"HINDSIGHT_API_KEY":"canary"}}\'\n',
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)
        data = self.config_data()
        data["credential_resolver"]["sha256"] = file_sha256(self.resolver)
        data["health_checks"][0]["credentials"] = [
            {
                "environment": "HINDSIGHT_API_KEY",
                "locator": "pass://hindsight/data-plane",
            }
        ]
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertTrue(
            manager._default_health_runner(
                manager.config.health_checks[0].to_dict(),
                manager._load_state()["current"],
            )
        )
        self.assertEqual(marker.read_text(encoding="utf-8"), str(self.resolver))
        self.assertTrue((self.install_root / "credential-resolver").is_file())

    def test_runtime_rejects_credential_resolver_symlinked_ancestry(self) -> None:
        resolver_parent = self.root / "resolver-parent"
        resolver_parent.mkdir()
        resolver = resolver_parent / "resolver"
        resolver.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' "
            '\'{"schema_version":1,"values":{"HINDSIGHT_API_KEY":"canary"}}\'\n',
            encoding="utf-8",
        )
        resolver.chmod(0o500)
        data = self.config_data()
        data["credential_resolver"] = {
            "path": str(resolver),
            "sha256": file_sha256(resolver),
        }
        data["health_checks"][0]["credentials"] = [
            {
                "environment": "HINDSIGHT_API_KEY",
                "locator": "pass://hindsight/data-plane",
            }
        ]
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")
        relocated_parent = self.root / "relocated-resolver-parent"
        resolver_parent.rename(relocated_parent)
        resolver_parent.symlink_to(relocated_parent, target_is_directory=True)

        completed = subprocess.run(
            manager._launch_argv("health", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stderr,
            b"credential resolver is not protected\n",
        )

    def test_install_rejects_an_unprotected_managed_python(self) -> None:
        managed_python = self.root / "consumer" / "python"
        managed_python.write_bytes(Path(sys.executable).read_bytes())
        managed_python.chmod(0o522)
        data = self.config_data()
        data["python_executable"] = str(managed_python)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

        with self.assertRaisesRegex(PortableInstallError, "managed Python"):
            PortableInstallationManager(
                InstallationConfig.load(data, source_path=self.config_path),
                command_runner=self.runner,
                health_runner=lambda _check, _release: True,
            ).install(self.release("1.0.0"), version="1.0.0")

    def test_install_rejects_a_non_python_managed_runtime(self) -> None:
        data = self.config_data()
        data["python_executable"] = "/usr/bin/true"
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

        with self.assertRaisesRegex(PortableInstallError, "Python 3.11 or newer"):
            PortableInstallationManager(
                InstallationConfig.load(data, source_path=self.config_path),
                command_runner=self.runner,
                health_runner=lambda _check, _release: True,
            ).install(self.release("1.0.0"), version="1.0.0")

    def test_install_rejects_an_unsupported_python_version(self) -> None:
        old_python = self.inventory.parent / "old-python"
        old_python.write_text(
            "#!/bin/sh\nprintf '%s\\n' 'hindsight-managed-python:3:10:9'\n",
            encoding="utf-8",
        )
        old_python.chmod(0o500)
        data = self.config_data()
        data["python_executable"] = str(old_python)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

        with self.assertRaisesRegex(PortableInstallError, "Python 3.11 or newer"):
            PortableInstallationManager(
                InstallationConfig.load(data, source_path=self.config_path),
                command_runner=self.runner,
                health_runner=lambda _check, _release: True,
            ).install(self.release("1.0.0"), version="1.0.0")

    def test_install_rejects_an_unprotected_uvx_executable(self) -> None:
        uvx = self.root / "consumer" / "uvx"
        uvx.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        uvx.chmod(0o722)
        data = self.config_data()
        data["uvx_executable"] = str(uvx)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

        with self.assertRaisesRegex(PortableInstallError, "uvx executable"):
            PortableInstallationManager(
                InstallationConfig.load(data, source_path=self.config_path),
                command_runner=self.runner,
                health_runner=lambda _check, _release: True,
            ).install(self.release("1.0.0"), version="1.0.0")

    def test_install_rejects_an_unprotected_npx_executable(self) -> None:
        npx = self.root / "consumer" / "npx"
        npx.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        npx.chmod(0o722)
        data = self.config_data()
        data["npx_executable"] = str(npx)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

        with self.assertRaisesRegex(PortableInstallError, "npx executable"):
            PortableInstallationManager(
                InstallationConfig.load(data, source_path=self.config_path),
                command_runner=self.runner,
                health_runner=lambda _check, _release: True,
            ).install(self.release("1.0.0"), version="1.0.0")

    def test_install_rejects_a_uvx_alias_in_unprotected_ancestry(self) -> None:
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        uvx = unsafe / "uvx"
        uvx.symlink_to("/usr/bin/true")
        data = self.config_data()
        data["uvx_executable"] = str(uvx)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

        with self.assertRaisesRegex(PortableInstallError, "uvx executable ancestry"):
            PortableInstallationManager(
                InstallationConfig.load(data, source_path=self.config_path),
                command_runner=self.runner,
                health_runner=lambda _check, _release: True,
            ).install(self.release("1.0.0"), version="1.0.0")

    def test_install_rejects_a_npx_alias_in_unprotected_ancestry(self) -> None:
        unsafe = self.root / "unsafe-npx"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        npx = unsafe / "npx"
        npx.symlink_to("/usr/bin/true")
        data = self.config_data()
        data["npx_executable"] = str(npx)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

        with self.assertRaisesRegex(PortableInstallError, "npx executable ancestry"):
            PortableInstallationManager(
                InstallationConfig.load(data, source_path=self.config_path),
                command_runner=self.runner,
                health_runner=lambda _check, _release: True,
            ).install(self.release("1.0.0"), version="1.0.0")

    def test_install_persists_the_validated_executable_targets(self) -> None:
        aliases = self.inventory.parent.resolve() / "executables"
        aliases.mkdir()
        npx = aliases / "npx"
        uvx = aliases / "uvx"
        zsh = aliases / "zsh"
        npx.symlink_to("/usr/bin/true")
        uvx.symlink_to("/usr/bin/true")
        zsh.symlink_to(ZSH_EXECUTABLE)
        data = self.config_data()
        data["npx_executable"] = str(npx)
        data["uvx_executable"] = str(uvx)
        data["zsh_executable"] = str(zsh)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )

        manager.install(self.release("1.0.0"), version="1.0.0")

        installed = json.loads((self.install_root / "managed-config.json").read_text())
        state = json.loads((self.install_root / "install-state.json").read_text())
        self.assertEqual(state["npx_alias"], str(npx))
        self.assertEqual(installed["npx_executable"], str(npx.resolve()))
        self.assertEqual(installed["uvx_executable"], str(uvx.resolve()))
        self.assertEqual(installed["zsh_executable"], str(zsh.resolve()))

    def test_load_state_migrates_the_legacy_npx_binding(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        state = manager._load_state()
        assert state is not None
        managed_config_path = self.install_root / "managed-config.json"
        managed_config = json.loads(managed_config_path.read_text())
        managed_config.pop("npx_executable")
        legacy_config_bytes = portable_install_module.canonical_bytes(managed_config)
        managed_config_path.chmod(0o700)
        managed_config_path.write_bytes(legacy_config_bytes)
        managed_config_path.chmod(0o500)
        state.pop("npx_alias")
        state["config_digest"] = portable_install_module.digest(managed_config)
        state["config_file_digest"] = hashlib.sha256(
            legacy_config_bytes
        ).hexdigest()
        state["owned_install_files"][str(managed_config_path)] = state[
            "config_file_digest"
        ]
        state["binding_generation_digest"] = portable_install_module.digest(
            {
                "config_digest": state["config_digest"],
                "config_file_digest": state["config_file_digest"],
                "inventory_digest": state["inventory_digest"],
            }
        )
        manager._write_state(state)

        original_atomic_write = portable_install_module._atomic_write

        def interrupt_after_config(path, content, mode, **kwargs):
            if path == manager._state_path and manager._binding_migration_path.exists():
                raise PortableInstallError("simulated binding migration interruption")
            return original_atomic_write(path, content, mode, **kwargs)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_write",
                side_effect=interrupt_after_config,
            ),
            self.assertRaisesRegex(PortableInstallError, "simulated"),
        ):
            manager._load_state()
        self.assertTrue(manager._binding_migration_path.is_file())

        migrated = manager._load_state()

        assert migrated is not None
        self.assertEqual(migrated["npx_alias"], str(manager.config.npx_executable))
        migrated_config = json.loads(managed_config_path.read_text())
        self.assertEqual(
            migrated_config["npx_executable"],
            str(manager.config.npx_executable.resolve()),
        )
        self.assertFalse(manager._binding_migration_path.exists())
        self.assertEqual(manager.verify()["status"], "verified")
        persisted = json.loads((self.install_root / "install-state.json").read_text())
        self.assertEqual(persisted, migrated)

    def test_install_rejects_an_unprotected_zsh_executable(self) -> None:
        zsh = self.root / "consumer" / "zsh"
        zsh.write_bytes(Path("/bin/zsh").read_bytes())
        zsh.chmod(0o722)
        data = self.config_data()
        data["zsh_executable"] = str(zsh)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

        with self.assertRaisesRegex(PortableInstallError, "Zsh executable"):
            PortableInstallationManager(
                InstallationConfig.load(data, source_path=self.config_path),
                command_runner=self.runner,
                health_runner=lambda _check, _release: True,
            ).install(self.release("1.0.0"), version="1.0.0")

    def test_install_rejects_group_writable_executable_ancestry(self) -> None:
        unsafe = self.inventory.parent.resolve() / "group-writable"
        unsafe.mkdir(mode=0o770)
        unsafe.chmod(0o770)
        uvx = unsafe / "uvx"
        uvx.symlink_to("/usr/bin/true")
        data = self.config_data()
        data["uvx_executable"] = str(uvx)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

        with self.assertRaisesRegex(PortableInstallError, "uvx executable ancestry"):
            PortableInstallationManager(
                InstallationConfig.load(data, source_path=self.config_path),
                command_runner=self.runner,
                health_runner=lambda _check, _release: True,
            ).install(self.release("1.0.0"), version="1.0.0")

    def test_install_rejects_unprotected_config_and_inventory_sources(self) -> None:
        release = self.release("1.0.0")
        for label, path in (
            ("installation config", self.config_path),
            ("inventory", self.inventory),
        ):
            with self.subTest(label=label):
                manager = self.manager()
                path.chmod(0o666)
                self.addCleanup(path.chmod, 0o600)

                with self.assertRaisesRegex(PortableInstallError, label):
                    manager.install(release, version="1.0.0")

                path.chmod(0o600)

    def test_config_read_rejects_an_unprotected_source(self) -> None:
        self.manager()
        self.config_path.chmod(0o666)

        with self.assertRaisesRegex(PortableInstallError, "installation config"):
            InstallationConfig.read(self.config_path)

    def test_public_lifecycle_rejects_unprotected_config_before_locking(self) -> None:
        manager = self.manager()
        release = self.release("1.0.0")
        self.config_path.chmod(0o666)
        operations = {
            "install": lambda: manager.install(release, version="1.0.0"),
            "upgrade": lambda: manager.upgrade(
                release,
                version="1.0.0",
                expected_current_binding_generation_digest="a" * 64,
            ),
            "verify": manager.verify,
            "rollback": lambda: manager.rollback(expected_current_digest="a" * 64),
            "uninstall": manager.uninstall,
        }
        for name, operation in operations.items():
            with (
                self.subTest(operation=name),
                mock.patch.object(
                    manager, "_lock", side_effect=AssertionError("lock entered")
                ),
            ):
                with self.assertRaisesRegex(
                    PortableInstallError, "installation config"
                ):
                    operation()

    def test_config_keeps_preserved_inputs_outside_install_root(self) -> None:
        for field in (
            "source_path",
            "inventory_path",
            "credential_resolver",
            "python_executable",
            "npx_executable",
            "uvx_executable",
            "zsh_executable",
        ):
            with self.subTest(field=field):
                data = self.config_data()
                source_path = self.config_path
                candidate = self.install_root / field
                if field == "source_path":
                    source_path = candidate
                elif field == "credential_resolver":
                    data["credential_resolver"]["path"] = str(candidate)
                else:
                    data[field] = str(candidate)

                with self.assertRaisesRegex(
                    PortableInstallError, "must remain outside install_root"
                ):
                    InstallationConfig.load(data, source_path=source_path)

    def test_managed_launcher_ignores_hostile_ambient_python_controls(self) -> None:
        data = self.config_data()
        data["services"][0]["credentials"] = []
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")
        hostile = self.root / "hostile-python"
        hostile.mkdir()
        marker = self.root / "sitecustomize-ran"
        (hostile / "sitecustomize.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('unsafe')\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            manager._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(hostile),
                "PYTHONSTARTUP": str(hostile / "sitecustomize.py"),
            },
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertFalse(marker.exists())

    def test_managed_launcher_uses_pinned_zsh_instead_of_path(self) -> None:
        marker = self.root / "zsh-ran"
        hostile_marker = self.root / "hostile-zsh-ran"
        hostile = self.root / "hostile-bin"
        hostile.mkdir()
        fake_zsh = hostile / "zsh"
        fake_zsh.write_text(
            f"#!/bin/sh\nprintf hostile > {hostile_marker!s}\n",
            encoding="utf-8",
        )
        fake_zsh.chmod(0o755)
        release = self.release("1.0.0")
        probe = release / "bin" / "probe.zsh"
        probe.write_text(
            f"#!/usr/bin/env zsh\nprint -rn -- pinned > {marker!s}\n",
            encoding="utf-8",
        )
        probe.chmod(0o755)
        data = self.config_data()
        data["services"][0]["credentials"] = []
        data["services"][0]["entrypoint"] = "bin/probe.zsh"
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(release, version="1.0.0")

        completed = subprocess.run(
            manager._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": str(hostile)},
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(marker.read_text(), "pinned")
        self.assertFalse(hostile_marker.exists())

    def test_systemd_user_renders_service_and_daily_timer_without_secrets(self) -> None:
        manager = self.manager(platform="systemd-user")

        manager.install(self.release("1.0.0"), version="1.0.0")

        service = self.service_root / "io.nisavid.hindsight.synthetic.broker.service"
        timer_service = (
            self.service_root
            / "io.nisavid.hindsight.synthetic.integration-upgrades.service"
        )
        timer = (
            self.service_root
            / "io.nisavid.hindsight.synthetic.integration-upgrades.timer"
        )
        combined = service.read_text() + timer_service.read_text() + timer.read_text()
        self.assertIn("[Service]", service.read_text())
        self.assertIn("OnCalendar=*-*-* 03:15:00", timer.read_text())
        self.assertIn("OnStartupSec=2min", timer.read_text())
        self.assertIn("TimeoutStopSec=330s", service.read_text())
        self.assertNotIn("OnBootSec=", timer.read_text())
        self.assertNotIn("HINDSIGHT_API_KEY", combined)
        self.assertNotIn("pass://hindsight/data-plane", combined)
        self.assertTrue(
            any(
                call[:3] == ("/usr/bin/systemctl", "--user", "daemon-reload")
                for call in self.runner.calls
            )
        )
        self.assertTrue(
            any(
                call[:3] == ("/usr/bin/systemctl", "--user", "restart")
                for call in self.runner.calls
            )
        )
        self.assertIn(
            (
                "/usr/bin/systemctl",
                "--user",
                "restart",
                "io.nisavid.hindsight.synthetic.integration-upgrades.timer",
            ),
            self.runner.calls,
        )

    def test_systemd_user_rejects_a_service_root_outside_the_search_path(
        self,
    ) -> None:
        data = self.config_data(platform="systemd-user")
        expected = self.root / "actual-systemd-user-root"
        with (
            mock.patch.object(
                portable_install_module,
                "_systemd_user_service_root",
                return_value=expected,
            ),
            self.assertRaisesRegex(PortableInstallError, "systemd-user service_root"),
        ):
            manager = PortableInstallationManager(
                InstallationConfig.load(data, source_path=self.config_path),
                command_runner=self.runner,
                health_runner=lambda _check, _release: True,
            )
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertFalse(self.install_root.exists())

    def test_systemd_user_discovers_the_manager_xdg_config_home(self) -> None:
        expected = self.root / "custom-config" / "systemd" / "user"

        discovered = _systemd_user_service_root(
            lambda argv: (
                f"XDG_CONFIG_HOME={self.root / 'custom-config'}\n"
                if argv[2:] == ("show-environment",)
                else None
            )
        )

        self.assertEqual(discovered, expected)

    def test_systemd_user_fails_closed_on_a_custom_unit_path(self) -> None:
        with self.assertRaisesRegex(PortableInstallError, "SYSTEMD_UNIT_PATH"):
            _systemd_user_service_root(
                lambda _argv: "SYSTEMD_UNIT_PATH=/srv/user-units\n"
            )

    def test_systemd_execstart_disables_environment_expansion(self) -> None:
        data = self.config_data(platform="systemd-user")
        data["install_root"] = str(self.root / "$HOSTILE_EXPANSION" / "install")
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )

        manager.install(self.release("1.0.0"), version="1.0.0")

        service = self.service_root / "io.nisavid.hindsight.synthetic.broker.service"
        content = service.read_text(encoding="utf-8")
        self.assertIn("ExecStart=:", content)
        self.assertIn("$HOSTILE_EXPANSION", content)

    def test_systemd_renderer_rejects_apostrophes_in_arguments(self) -> None:
        with self.assertRaisesRegex(
            PortableInstallError, "systemd unit arguments must not contain apostrophes"
        ):
            _systemd_escape("/home/example/it's-ambiguous")

    def test_systemd_renderer_doubles_literal_backslashes(self) -> None:
        self.assertEqual(
            _systemd_escape(r"/home/example/tab\tpath"),
            r"'/home/example/tab\\tpath'",
        )

    def test_systemd_failure_diagnostic_includes_the_managed_verb(self) -> None:
        error = subprocess.CalledProcessError(
            7, ("/usr/bin/systemctl", "--user", "restart", "example.service")
        )
        with (
            mock.patch(
                "hindsight_memory_control_plane.portable_install.subprocess.run",
                side_effect=error,
            ) as run,
            self.assertRaisesRegex(
                PortableInstallError, r"systemctl --user restart, exit 7"
            ),
        ):
            PortableInstallationManager._default_command_runner(error.cmd)
        self.assertEqual(run.call_args.kwargs["timeout"], 360)

    def test_systemctl_user_preserves_only_a_validated_session_bus(self) -> None:
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o700)
        bus = f"unix:path={runtime}/bus"
        ambient = {
            "XDG_RUNTIME_DIR": str(runtime),
            "DBUS_SESSION_BUS_ADDRESS": bus,
            "UNRELATED_AMBIENT": "must-not-cross",
        }

        with (
            mock.patch.dict(os.environ, ambient, clear=True),
            mock.patch(
                "hindsight_memory_control_plane.portable_install.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, stdout=b""),
            ) as run,
        ):
            PortableInstallationManager._default_command_runner(
                ("/usr/bin/systemctl", "--user", "daemon-reload")
            )
            PortableInstallationManager._default_command_runner(
                ("/bin/launchctl", "print", f"gui/{os.getuid()}")
            )

        systemd_environment = run.call_args_list[0].kwargs["env"]
        launchd_environment = run.call_args_list[1].kwargs["env"]
        self.assertEqual(systemd_environment["XDG_RUNTIME_DIR"], str(runtime))
        self.assertEqual(systemd_environment["DBUS_SESSION_BUS_ADDRESS"], bus)
        self.assertNotIn("UNRELATED_AMBIENT", systemd_environment)
        self.assertNotIn("XDG_RUNTIME_DIR", launchd_environment)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", launchd_environment)

    def test_systemctl_user_rejects_an_untrusted_session_bus_binding(self) -> None:
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o777)
        ambient = {
            "XDG_RUNTIME_DIR": str(runtime),
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/unbound-bus",
        }

        with (
            mock.patch.dict(os.environ, ambient, clear=True),
            mock.patch(
                "hindsight_memory_control_plane.portable_install.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, stdout=b""),
            ) as run,
        ):
            PortableInstallationManager._default_command_runner(
                ("/usr/bin/systemctl", "--user", "daemon-reload")
            )

        environment = run.call_args.kwargs["env"]
        self.assertNotIn("XDG_RUNTIME_DIR", environment)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", environment)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze unavailable")
    def test_rendered_systemd_user_units_pass_systemd_analyze(self) -> None:
        manager = self.manager(platform="systemd-user")
        manager.install(self.release("1.0.0"), version="1.0.0")

        units = sorted(str(path) for path in self.service_root.glob("*"))
        completed = subprocess.run(
            [shutil.which("systemd-analyze"), "verify", *units],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())

    def test_launchd_upgrade_unloads_before_loading_new_generation(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        self.runner.calls.clear()

        self.upgrade(manager, self.release("2.0.0"), version="2.0.0")

        actions = [call[1] for call in self.runner.calls if call[0] == "/bin/launchctl"]
        self.assertIn("bootout", actions)
        self.assertLess(actions.index("bootout"), actions.index("bootstrap"))

    def test_launchd_refuses_to_bootout_a_foreign_loaded_plist(self) -> None:
        data = self.config_data()
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        runner = ForeignManifestRunner()
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=runner,
            health_runner=lambda _check, _release: True,
        )

        with self.assertRaisesRegex(PortableInstallError, "loaded plist"):
            manager._activate_services()

        self.assertFalse(any(call[1] == "bootout" for call in runner.calls))

    def test_fresh_launchd_rejects_same_path_loaded_job_before_publication(
        self,
    ) -> None:
        data = self.config_data()
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        calls: list[tuple[str, ...]] = []

        def runner(argv: tuple[str, ...]) -> str | None:
            calls.append(argv)
            if argv[:2] == ("/bin/launchctl", "print"):
                label = argv[2].rsplit("/", 1)[-1]
                return f"path = {self.service_root / f'{label}.plist'}\n"
            return None

        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=runner,
            health_runner=lambda _check, _release: True,
        )

        with self.assertRaisesRegex(PortableInstallError, "already exists"):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertFalse(self.service_root.exists())
        self.assertFalse(
            any(call[1] in {"bootout", "bootstrap", "kickstart"} for call in calls)
        )

    def test_launchd_upgrade_preflights_all_jobs_before_mutation(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        calls: list[tuple[str, ...]] = []

        def runner(argv: tuple[str, ...]) -> str | None:
            calls.append(argv)
            if argv[:2] == ("/bin/launchctl", "print") and argv[2].endswith(
                "integration-upgrades"
            ):
                return "path = /tmp/foreign.plist\n"
            return self.runner(argv)

        manager._command_runner = runner
        with self.assertRaisesRegex(PortableInstallError, "loaded plist"):
            self.upgrade(manager, self.release("2.0.0"), version="2.0.0")

        self.assertFalse(
            any(call[1] in {"bootout", "bootstrap", "kickstart"} for call in calls)
        )
        self.assertEqual(manager._load_state()["current"]["version"], "1.0.0")

    def test_launchd_accepts_a_canonical_alias_for_its_owned_plist(self) -> None:
        self.service_root.mkdir()
        expected = self.service_root / "owned.plist"
        expected.write_text("owned", encoding="utf-8")
        alias_root = self.root / "service-alias"
        alias_root.symlink_to(self.service_root, target_is_directory=True)

        manager = self.manager()
        manager._command_runner = lambda _argv: f"path = {alias_root / expected.name}\n"

        loaded = manager._launchd_loaded_manifest("owned", expected)

        self.assertEqual(loaded, alias_root / expected.name)

    def test_launchd_retries_transient_bootstrap_after_replacing_a_job(self) -> None:
        manager = self.manager()
        calls = 0

        def transient_runner(_argv: tuple[str, ...]) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _ManagedServiceCommandError(5)

        manager._command_runner = transient_runner

        manager._bootstrap_launchd(
            "gui/501",
            self.service_root / "owned.plist",
            replacing_loaded_job=True,
        )

        self.assertEqual(calls, 2)

    def test_launchd_retries_transient_bootstrap_after_an_explicit_stop(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        manager._deactivate_services()
        bootstrap_calls = 0

        def transient_runner(argv: tuple[str, ...]) -> str | None:
            nonlocal bootstrap_calls
            if argv[:2] == ("/bin/launchctl", "bootstrap"):
                bootstrap_calls += 1
                if bootstrap_calls == 1:
                    raise _ManagedServiceCommandError(5)
            return self.runner(argv)

        manager._command_runner = transient_runner

        manager._activate_services(retry_bootstrap_after_stop=True)

        self.assertEqual(bootstrap_calls, 3)
        self.assertEqual(manager.service_status()["status"], "running")

    def test_launchd_does_not_retry_a_fresh_invalid_bootstrap(self) -> None:
        manager = self.manager()
        manager._command_runner = lambda _argv: (_ for _ in ()).throw(
            _ManagedServiceCommandError(5)
        )

        with self.assertRaises(_ManagedServiceCommandError):
            manager._bootstrap_launchd(
                "gui/501",
                self.service_root / "owned.plist",
                replacing_loaded_job=False,
            )

    def test_fresh_install_does_not_retry_an_invalid_launchd_bootstrap(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        bootstrap_calls = 0

        def invalid_bootstrap_runner(argv: tuple[str, ...]) -> str | None:
            nonlocal bootstrap_calls
            if argv[:2] == ("/bin/launchctl", "bootstrap"):
                bootstrap_calls += 1
                raise _ManagedServiceCommandError(5)
            return self.runner(argv)

        manager._command_runner = invalid_bootstrap_runner

        with self.assertRaises(_ManagedServiceCommandError):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertEqual(bootstrap_calls, 1)

    def test_systemd_refuses_to_restart_or_disable_a_foreign_fragment(self) -> None:
        data = self.config_data(platform="systemd-user")
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        runner = ForeignManifestRunner()
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=runner,
            health_runner=lambda _check, _release: True,
        )

        with self.assertRaisesRegex(PortableInstallError, "fragment"):
            manager._activate_services()
        with self.assertRaisesRegex(PortableInstallError, "fragment"):
            manager._deactivate_services()

        actions = [
            call[2]
            for call in runner.calls
            if call[:2] == ("/usr/bin/systemctl", "--user")
        ]
        self.assertNotIn("enable", actions)
        self.assertNotIn("restart", actions)
        self.assertNotIn("disable", actions)

    def test_fresh_systemd_rejects_loaded_unit_before_publication(self) -> None:
        data = self.config_data(platform="systemd-user")
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        calls: list[tuple[str, ...]] = []

        def runner(argv: tuple[str, ...]) -> str | None:
            calls.append(argv)
            if argv[:5] == (
                "/usr/bin/systemctl",
                "--user",
                "show",
                "--property=LoadState",
                "--property=FragmentPath",
            ):
                return f"LoadState=loaded\nFragmentPath={self.service_root / argv[5]}\n"
            return None

        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=runner,
            health_runner=lambda _check, _release: True,
        )

        with self.assertRaisesRegex(PortableInstallError, "already exists"):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertFalse(self.service_root.exists())
        self.assertFalse(
            any(
                call[2] in {"stop", "enable", "restart", "disable"}
                for call in calls
                if call[:2] == ("/usr/bin/systemctl", "--user")
            )
        )

    def test_systemd_refuses_to_stop_a_foreign_timer_companion(self) -> None:
        manager = self.manager(platform="systemd-user")
        manager.install(self.release("1.0.0"), version="1.0.0")
        runner = ForeignManifestRunner()
        manager._command_runner = runner

        with self.assertRaisesRegex(PortableInstallError, "fragment"):
            self.upgrade(manager, self.release("2.0.0"), version="2.0.0")

        self.assertFalse(
            any(
                call[:3]
                in {
                    ("/usr/bin/systemctl", "--user", "stop"),
                    ("/usr/bin/systemctl", "--user", "disable"),
                }
                for call in runner.calls
            )
        )
        self.assertEqual(manager._load_state()["current"]["version"], "1.0.0")

    def test_systemd_journals_then_stops_timer_before_companion(self) -> None:
        manager = self.manager(platform="systemd-user")
        manager.install(self.release("1.0.0"), version="1.0.0")
        events: list[str] = []
        original_write_state = manager._write_state

        def runner(argv: tuple[str, ...]) -> None:
            self.runner(argv)
            if argv[:3] in {
                ("/usr/bin/systemctl", "--user", "stop"),
                ("/usr/bin/systemctl", "--user", "disable"),
            }:
                self.assertTrue(manager._transaction_path.is_file())
                events.append(f"{argv[2]}:{argv[-1]}")

        def write_state(state):
            events.append("write-state")
            original_write_state(state)

        manager._command_runner = runner
        with mock.patch.object(manager, "_write_state", side_effect=write_state):
            self.upgrade(manager, self.release("2.0.0"), version="2.0.0")

        timer_stop = "disable:io.nisavid.hindsight.synthetic.integration-upgrades.timer"
        companion_stop = (
            "stop:io.nisavid.hindsight.synthetic.integration-upgrades.service"
        )
        self.assertLess(events.index(timer_stop), events.index(companion_stop))
        self.assertLess(events.index(companion_stop), events.index("write-state"))
        self.assertIn(
            (
                "/usr/bin/systemctl",
                "--user",
                "disable",
                "--now",
                "io.nisavid.hindsight.synthetic.integration-upgrades.timer",
            ),
            self.runner.calls,
        )
        self.assertIn(
            (
                "/usr/bin/systemctl",
                "--user",
                "stop",
                "io.nisavid.hindsight.synthetic.integration-upgrades.service",
            ),
            self.runner.calls,
        )

    def test_launchd_journals_before_quiescing_timer(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        events: list[str] = []

        def runner(argv: tuple[str, ...]) -> str | None:
            if argv[:2] == ("/bin/launchctl", "bootout"):
                label = argv[2].rsplit("/", 1)[-1]
                if label.endswith("integration-upgrades"):
                    self.assertTrue(manager._transaction_path.is_file())
                    events.append("timer-bootout")
            return self.runner(argv)

        manager._command_runner = runner
        self.upgrade(manager, self.release("2.0.0"), version="2.0.0")

        self.assertEqual(events, ["timer-bootout"])

    def test_service_launcher_resolves_credentials_only_into_child_environment(
        self,
    ) -> None:
        capture = self.root / "capture.json"
        release = self.release("1.0.0")
        child = release / "bin" / "capture"
        child.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            'pathlib.Path(sys.argv[1]).write_text(json.dumps({"secret": os.environ.get("HINDSIGHT_API_KEY"), "ambient": os.environ.get("UNRELATED_AMBIENT"), "release_path": os.environ.get("CAPTURE_RELEASE"), "inventory": os.environ.get("HINDSIGHT_MEMORY_INVENTORY"), "uvx": os.environ.get("HINDSIGHT_EMBED_UVX_EXECUTABLE"), "npx": os.environ.get("HINDSIGHT_EMBED_NPX_EXECUTABLE"), "path": os.environ.get("PATH"), "isolated": sys.flags.isolated}))\n',
            encoding="utf-8",
        )
        child.chmod(0o755)
        self.resolver.chmod(0o700)
        resolver_source = (
            "#!/usr/bin/env python3\n"
            "import json\n"
            'print(json.dumps({"schema_version": 1, "values": {"HINDSIGHT_API_KEY": "test-canary-secret"}}))\n'
        )
        self.resolver.write_text(resolver_source, encoding="utf-8")
        self.resolver.chmod(0o500)
        data = self.config_data()
        npx_directory = self.inventory.parent / "node-bin"
        npx_directory.mkdir()
        npx = npx_directory / "npx"
        npx.symlink_to("/usr/bin/true")
        data["npx_executable"] = str(npx)
        data["credential_resolver"]["sha256"] = file_sha256(self.resolver)
        data["services"][0]["entrypoint"] = "bin/capture"
        data["services"][0]["arguments"] = [str(capture)]
        data["services"][0]["environment"]["CAPTURE_RELEASE"] = (
            "release://lib/release.txt"
        )
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        config = InstallationConfig.load(data, source_path=self.config_path)
        manager = PortableInstallationManager(config, command_runner=self.runner)
        manager.install(release, version="1.0.0")
        launcher = self.install_root / "launcher.py"

        environment = {
            "UNRELATED_AMBIENT": "must-not-cross",
            "PATH": os.environ["PATH"],
        }
        completed = subprocess.run(
            [
                sys.executable,
                str(launcher),
                "--config",
                str(self.install_root / "managed-config.json"),
                "--service",
                "broker",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        captured = json.loads(capture.read_text())
        self.assertEqual(captured["secret"], "test-canary-secret")
        self.assertIsNone(captured["ambient"])
        self.assertEqual(Path(captured["release_path"]).read_text(), "1.0.0")
        self.assertEqual(
            captured["inventory"],
            str((self.install_root / "managed-inventory.json").resolve()),
        )
        self.assertEqual(captured["uvx"], "/usr/bin/true")
        self.assertEqual(captured["npx"], str(npx))
        self.assertEqual(captured["path"], f"{npx_directory}:/usr/bin:/bin")
        self.assertEqual(captured["isolated"], 1)
        for path in self.service_root.glob("*"):
            self.assertNotIn("test-canary-secret", path.read_text())

        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/bin/sh\nprintf '%s\\n' 'source resolver was replaced' >&2\nexit 91\n",
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)
        drifted_resolver = subprocess.run(
            manager._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=10,
        )
        self.assertNotEqual(drifted_resolver.returncode, 0)
        self.assertEqual(
            drifted_resolver.stderr,
            b"credential resolver is not protected\n",
        )
        self.resolver.chmod(0o700)
        self.resolver.write_text(resolver_source, encoding="utf-8")
        self.resolver.chmod(0o500)

        original_config = self.config_path.read_text()
        self.config_path.chmod(0o600)
        self.config_path.write_text(original_config + "\n", encoding="utf-8")
        rejected = subprocess.run(
            [
                sys.executable,
                str(launcher),
                "--config",
                str(self.config_path),
                "--service",
                "broker",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=10,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn(b"test-canary-secret", rejected.stderr)

        state_path = self.install_root / "install-state.json"
        state = json.loads(state_path.read_text())
        del state["npx_alias"]
        state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        missing_npx_binding = subprocess.run(
            [
                sys.executable,
                str(launcher),
                "--config",
                str(self.install_root / "managed-config.json"),
                "--service",
                "broker",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=10,
        )
        self.assertNotEqual(missing_npx_binding.returncode, 0)
        self.assertEqual(
            missing_npx_binding.stderr,
            b"managed npx binding is not protected\n",
        )

    def test_service_launcher_uses_the_owned_validated_config_snapshot(self) -> None:
        capture = self.root / "owned-config-capture"
        release = self.release("1.0.0")
        original = release / "bin" / "original"
        original.write_text(
            f"#!/bin/sh\nprintf original > {capture!s}\n", encoding="utf-8"
        )
        original.chmod(0o755)
        replacement = release / "bin" / "replacement"
        replacement.write_text(
            f"#!/bin/sh\nprintf replacement > {capture!s}\n", encoding="utf-8"
        )
        replacement.chmod(0o755)
        data = self.config_data()
        data["services"][0]["credentials"] = []
        data["services"][0]["entrypoint"] = "bin/original"
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(release, version="1.0.0")
        data["services"][0]["entrypoint"] = "bin/replacement"
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

        completed = subprocess.run(
            manager._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/hostile"},
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(capture.read_text(), "original")
        state = json.loads((self.install_root / "install-state.json").read_text())
        owned_config = self.install_root / "managed-config.json"
        self.assertEqual(file_sha256(owned_config), state["config_file_digest"])
        self.assertIn(str(owned_config), state["owned_install_files"])

    def test_install_rejects_a_config_changed_after_initial_parsing(self) -> None:
        data = self.config_data()
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        changed = self.config_data()
        changed["services"][0]["arguments"] = ["unexpected"]
        self.config_path.write_text(
            json.dumps(changed, sort_keys=True), encoding="utf-8"
        )

        with self.assertRaisesRegex(
            PortableInstallError, "installation config changed"
        ):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertFalse(self.install_root.exists())

    def test_install_rechecks_config_generation_after_prelock_validation(
        self,
    ) -> None:
        data = self.config_data()
        original_bytes = json.dumps(data, sort_keys=True).encode()
        self.config_path.write_bytes(original_bytes)
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        changed = self.config_data()
        changed["services"][0]["arguments"] = ["unexpected"]
        replacement = self.config_path.with_suffix(".replacement")
        replacement.write_text(json.dumps(changed, sort_keys=True), encoding="utf-8")
        real_open = os.open
        config_opens = 0

        def replace_after_open(path, flags, *args, **kwargs):
            nonlocal config_opens
            descriptor = real_open(path, flags, *args, **kwargs)
            if Path(path) == self.config_path.resolve():
                config_opens += 1
                if config_opens == 1:
                    replacement.replace(self.config_path)
            return descriptor

        with mock.patch(
            "hindsight_memory_control_plane.portable_install.os.open",
            side_effect=replace_after_open,
        ):
            with self.assertRaisesRegex(
                PortableInstallError, "installation config changed"
            ):
                manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertEqual(config_opens, 2)
        self.assertFalse(self.install_root.exists())

    def test_installed_wrapper_and_python_target_use_isolated_configured_python(
        self,
    ) -> None:
        capture = self.root / "python-target.json"
        release = self.release("1.0.0")
        target = release / "bin" / "hindsight-memory"
        target.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            f"pathlib.Path({str(capture)!r}).write_text(json.dumps({{'isolated': sys.flags.isolated, 'pythonpath': os.environ.get('PYTHONPATH')}}))\n",
            encoding="utf-8",
        )
        target.chmod(0o755)
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(release, version="1.0.0")
        wrapper = self.install_root / "bin" / "hindsight-memory"
        hostile = self.root / "hostile"
        hostile.mkdir()
        (hostile / "sitecustomize.py").write_text(
            "raise RuntimeError('ambient Python controls crossed boundary')\n",
            encoding="utf-8",
        )

        completed = subprocess.run(
            [str(wrapper)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": str(hostile),
                "PYTHONPATH": str(hostile),
                "PYTHONHOME": str(hostile),
            },
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        observed = json.loads(capture.read_text())
        self.assertEqual(observed["isolated"], 1)
        self.assertIsNone(observed["pythonpath"])
        wrapper_text = wrapper.read_text(encoding="utf-8")
        self.assertIn(str(self.managed_python.resolve()), wrapper_text)
        self.assertIn(" -I ", wrapper_text)

    def test_installed_wrapper_runs_lifecycle_commands_during_pending_recovery(
        self,
    ) -> None:
        capture = self.root / "pending-lifecycle.json"

        def recovery_release(version: str, *, broken: bool = False) -> Path:
            release = self.release(version)
            target = release / "bin" / "hindsight-memory"
            target.write_text(
                (
                    "#!/bin/sh\nexit 97\n"
                    if broken
                    else "#!/usr/bin/env python3\n"
                    "import json, pathlib, sys\n"
                    f"pathlib.Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))\n"
                ),
                encoding="utf-8",
            )
            target.chmod(0o755)
            return release

        manager = self.manager(health_runner=lambda _check, _release: True)
        installed = manager.install(recovery_release("1.0.0"), version="1.0.0")
        manager.upgrade(
            recovery_release("2.0.0", broken=True),
            version="2.0.0",
            expected_current_binding_generation_digest=installed[
                "binding_generation_digest"
            ],
        )
        state = json.loads(manager._state_path.read_text(encoding="utf-8"))
        state["transaction"] = {
            "operation": "upgrade",
            "candidate_release_digest": state["current"]["release_digest"],
            "previous_release_digest": state["last_known_good"]["release_digest"],
        }
        manager._state_path.write_text(json.dumps(state), encoding="utf-8")
        manager._active_path.unlink()
        wrapper = self.install_root / "bin" / "hindsight-memory"

        completed = subprocess.run(
            [str(wrapper), "verify", "--config", str(self.config_path)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(
            json.loads(capture.read_text(encoding="utf-8")),
            ["verify", "--config", str(self.config_path)],
        )

    def test_installed_wrapper_rejects_runtime_commands_during_recovery(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        state = json.loads(manager._state_path.read_text(encoding="utf-8"))
        state["transaction"] = {
            "operation": "upgrade",
            "candidate_release_digest": state["current"]["release_digest"],
            "previous_release_digest": state["last_known_good"]["release_digest"],
        }
        manager._state_path.write_text(json.dumps(state), encoding="utf-8")

        completed = subprocess.run(
            [str(self.install_root / "bin" / "hindsight-memory"), "broker", "serve"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"lifecycle recovery", completed.stderr)

    def test_installed_wrapper_uses_the_pre_rollback_cli_for_recovery(self) -> None:
        capture = self.root / "rollback-recovery"

        def recovery_release(version: str) -> Path:
            release = self.release(version)
            target = release / "bin" / "hindsight-memory"
            target.write_text(
                f"#!/bin/sh\nprintf '%s' {version!r} > {str(capture)!r}\n",
                encoding="utf-8",
            )
            target.chmod(0o755)
            return release

        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(recovery_release("1.0.0"), version="1.0.0")
        self.upgrade(manager, recovery_release("2.0.0"), version="2.0.0")
        state = json.loads(manager._state_path.read_text(encoding="utf-8"))
        previous_current = state["current"]
        rollback_target = state["last_known_good"]
        state["current"] = rollback_target
        state["last_known_good"] = previous_current
        state["transaction"] = {
            "operation": "rollback",
            "candidate_release_digest": rollback_target["release_digest"],
            "previous_release_digest": previous_current["release_digest"],
        }
        manager._state_path.write_text(json.dumps(state), encoding="utf-8")

        completed = subprocess.run(
            [str(self.install_root / "bin" / "hindsight-memory"), "verify"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(capture.read_text(encoding="utf-8"), "2.0.0")

    def test_hook_authority_remains_on_candidate_after_runtime_rollback(
        self,
    ) -> None:
        capture = self.root / "hook-authority"
        arguments_capture = self.root / "hook-authority-arguments"

        def authority_release(version: str) -> Path:
            release = self.release(version)
            target = release / "bin" / "hindsight-memory"
            target.write_text(
                (
                    "#!/bin/sh\n"
                    f"printf '%s' {version!r} > {str(capture)!r}\n"
                    f"printf '%s\\n' \"$@\" > {str(arguments_capture)!r}\n"
                ),
                encoding="utf-8",
            )
            target.chmod(0o755)
            return release

        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(authority_release("1.0.0"), version="1.0.0")
        current = self.upgrade(
            manager,
            authority_release("2.0.0"),
            version="2.0.0",
        )

        manager.rollback(expected_current_digest=current["release_digest"])
        completed = subprocess.run(
            [
                str(
                    self.install_root
                    / "bin"
                    / "hindsight-memory-hook-authority"
                ),
                "harness-config",
                "status",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(capture.read_text(encoding="utf-8"), "2.0.0")

        state_scoped = subprocess.run(
            [
                str(
                    self.install_root
                    / "bin"
                    / "hindsight-memory-hook-authority"
                ),
                "--state-dir",
                str(self.state_root / "memory"),
                "harness-config",
                "disable",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )
        self.assertEqual(
            state_scoped.returncode,
            0,
            state_scoped.stderr.decode(),
        )
        self.assertEqual(
            arguments_capture.read_text(encoding="utf-8").splitlines(),
            [
                "--state-dir",
                str(self.state_root / "memory"),
                "harness-config",
                "disable",
            ],
        )
        wrong_state = subprocess.run(
            [
                str(
                    self.install_root
                    / "bin"
                    / "hindsight-memory-hook-authority"
                ),
                "--state-dir",
                str(self.root / "wrong-state"),
                "harness-config",
                "disable",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )
        self.assertNotEqual(wrong_state.returncode, 0)
        self.assertIn(
            b"hook authority state directory is invalid",
            wrong_state.stderr,
        )
        disallowed = subprocess.run(
            [
                str(
                    self.install_root
                    / "bin"
                    / "hindsight-memory-hook-authority"
                ),
                "--state-dir",
                str(self.state_root / "memory"),
                "harness-config",
                "uninstall",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )
        self.assertNotEqual(disallowed.returncode, 0)
        self.assertIn(
            b"hook authority permits only harness configuration commands",
            disallowed.stderr,
        )
        self.assertEqual(
            arguments_capture.read_text(encoding="utf-8").splitlines(),
            [
                "--state-dir",
                str(self.state_root / "memory"),
                "harness-config",
                "disable",
            ],
        )

        managed_config_path = self.install_root / "managed-config.json"
        state_path = self.install_root / "install-state.json"
        managed_config_preimage = managed_config_path.read_bytes()
        state_preimage = state_path.read_bytes()
        malformed_config = b'{"state_root":"/first","state_root":"/second"}\n'
        malformed_digest = hashlib.sha256(malformed_config).hexdigest()
        managed_config_path.chmod(0o700)
        managed_config_path.write_bytes(malformed_config)
        managed_config_path.chmod(0o500)
        malformed_state = json.loads(state_preimage)
        malformed_state["config_file_digest"] = malformed_digest
        malformed_state["owned_install_files"][
            str(managed_config_path)
        ] = malformed_digest
        state_path.write_text(
            json.dumps(malformed_state),
            encoding="utf-8",
        )
        malformed = subprocess.run(
            [
                str(
                    self.install_root
                    / "bin"
                    / "hindsight-memory-hook-authority"
                ),
                "--state-dir",
                str(self.state_root / "memory"),
                "harness-config",
                "disable",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )
        self.assertNotEqual(malformed.returncode, 0)
        self.assertEqual(
            malformed.stderr,
            b"managed config binding is invalid\n",
        )
        managed_config_path.chmod(0o700)
        managed_config_path.write_bytes(managed_config_preimage)
        managed_config_path.chmod(0o500)
        state_path.write_bytes(state_preimage)

        authority_path = self.install_root / "hook-authority.json"
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        authority["version"] = "1.0.0"
        authority_path.chmod(0o700)
        authority_path.write_text(
            json.dumps(authority),
            encoding="utf-8",
        )
        authority_path.chmod(0o500)
        drifted = subprocess.run(
            [
                str(
                    self.install_root
                    / "bin"
                    / "hindsight-memory-hook-authority"
                ),
                "harness-config",
                "status",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )
        self.assertNotEqual(drifted.returncode, 0)
        self.assertIn(b"hook authority digest mismatch", drifted.stderr)

    def test_rollback_launcher_uses_candidate_authority_and_omits_extension(
        self,
    ) -> None:
        runtime_capture = self.root / "runtime-capture.json"
        authority_capture = self.root / "authority-capture.json"

        def release_with_probes(version: str, *, authority: bool) -> Path:
            release = self.release(version)
            runtime = release / "bin" / "runtime-capture"
            runtime.write_text(
                (
                    "#!/usr/bin/env python3\n"
                    "import json, os, pathlib\n"
                    f"pathlib.Path({str(runtime_capture)!r}).write_text("
                    "json.dumps({"
                    f"'version': {version!r}, "
                    "'extension': os.environ.get("
                    "'HINDSIGHT_API_HTTP_EXTENSION')"
                    "}))\n"
                ),
                encoding="utf-8",
            )
            runtime.chmod(0o755)
            if authority:
                supervisor = (
                    release / "bin/hindsight-hook-authority-supervisor"
                )
                supervisor.write_text(
                    (
                        "#!/usr/bin/env python3\n"
                        "import json, os, pathlib\n"
                        f"pathlib.Path({str(authority_capture)!r}).write_text("
                        "json.dumps({"
                        "'release': os.environ.get("
                        "'HINDSIGHT_HOOK_AUTHORITY_RELEASE_DIGEST'), "
                        "'controller': os.environ.get("
                        "'HINDSIGHT_HOOK_AUTHORITY_CONTROLLER_SHA256')"
                        "}))\n"
                    ),
                    encoding="utf-8",
                )
                supervisor.chmod(0o755)
            return release

        base = release_with_probes("1.0.0", authority=False)
        old_data = self.config_data()
        old_data["services"][0].update(
            entrypoint="bin/runtime-capture",
            arguments=[],
            credentials=[],
        )
        self.config_path.write_text(
            json.dumps(old_data, sort_keys=True),
            encoding="utf-8",
        )
        old_manager = PortableInstallationManager(
            InstallationConfig.load(
                old_data,
                source_path=self.config_path,
            ),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        installed = old_manager.install(base, version="1.0.0")

        candidate = release_with_probes("2.0.0", authority=True)
        new_data = json.loads(json.dumps(old_data))
        extension = (
            "hindsight_memory_control_plane."
            "migration_generation_extension:"
            "MigrationGenerationHttpExtension"
        )
        new_data["services"][0]["environment"][
            "HINDSIGHT_API_HTTP_EXTENSION"
        ] = extension
        new_data["health_checks"][0]["environment"][
            "HINDSIGHT_API_HTTP_EXTENSION"
        ] = extension
        new_data["services"].append(
            {
                "service_id": "hook-authority",
                "label": "io.nisavid.hindsight.synthetic.hook-authority",
                "entrypoint": "bin/hindsight-hook-authority-supervisor",
                "arguments": [],
                "environment": {"PATH": "/usr/bin:/bin"},
                "credentials": [],
                "restart": "on-failure",
            }
        )
        self.config_path.write_text(
            json.dumps(new_data, sort_keys=True),
            encoding="utf-8",
        )
        manager = PortableInstallationManager(
            InstallationConfig.load(
                new_data,
                source_path=self.config_path,
            ),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        upgraded = manager.upgrade(
            candidate,
            version="2.0.0",
            expected_current_binding_generation_digest=installed[
                "binding_generation_digest"
            ],
        )
        manager.rollback(
            expected_current_digest=upgraded["release_digest"],
        )

        runtime = subprocess.run(
            manager._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )
        authority = subprocess.run(
            manager._launch_argv("service", "hook-authority"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertEqual(runtime.returncode, 0, runtime.stderr.decode())
        self.assertEqual(authority.returncode, 0, authority.stderr.decode())
        self.assertEqual(
            json.loads(runtime_capture.read_text()),
            {"version": "1.0.0", "extension": None},
        )
        authority_environment = json.loads(authority_capture.read_text())
        self.assertEqual(
            authority_environment["release"],
            upgraded["release_digest"],
        )
        self.assertEqual(
            authority_environment["controller"],
            next(
                entry["sha256"]
                for entry in json.loads(
                    (
                        self.install_root / "hook-authority.json"
                    ).read_text()
                )["manifest"]["files"]
                if entry["path"] == "bin/hindsight-memory"
            ),
        )

    def test_service_launcher_uses_owned_inventory_after_external_drift(self) -> None:
        data = self.config_data()
        data["services"][0]["credentials"] = []
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")
        self.inventory.write_text(
            '{"schema_version":1,"drift":true}\n', encoding="utf-8"
        )

        completed = subprocess.run(
            manager._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(completed.stdout.decode().strip(), "1.0.0")
        with self.assertRaisesRegex(PortableInstallError, "consumer binding"):
            manager.verify()

    def test_service_launcher_rejects_owned_inventory_drift(self) -> None:
        data = self.config_data()
        data["services"][0]["credentials"] = []
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")
        owned_inventory = self.install_root / "managed-inventory.json"
        owned_inventory.chmod(0o700)
        owned_inventory.write_text(
            '{"schema_version":1,"drift":true}\n', encoding="utf-8"
        )
        owned_inventory.chmod(0o500)

        completed = subprocess.run(
            manager._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"inventory binding", completed.stderr)

    def test_credential_resolver_receives_account_and_bound_user_bus(self) -> None:
        account = pwd.getpwuid(os.geteuid())
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o700)
        bus = f"unix:path={runtime}/bus"
        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            f"assert os.environ['HOME'] == {account.pw_dir!r}\n"
            f"assert os.environ['USER'] == {account.pw_name!r}\n"
            f"assert os.environ['LOGNAME'] == {account.pw_name!r}\n"
            "assert os.environ['PATH'] == '/usr/bin:/bin'\n"
            f"assert os.environ['XDG_RUNTIME_DIR'] == {str(runtime)!r}\n"
            f"assert os.environ['DBUS_SESSION_BUS_ADDRESS'] == {bus!r}\n"
            "request = json.load(sys.stdin)\n"
            "values = {item['environment']: 'canary' for item in request['credentials']}\n"
            "print(json.dumps({'schema_version': 1, 'values': values}))\n",
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)
        data = self.config_data()
        data["credential_resolver"]["sha256"] = file_sha256(self.resolver)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")

        completed = subprocess.run(
            manager._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": "/hostile",
                "HOME": "/hostile",
                "USER": "hostile",
                "LOGNAME": "hostile",
                "XDG_RUNTIME_DIR": str(runtime),
                "DBUS_SESSION_BUS_ADDRESS": bus,
            },
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(completed.stdout.decode().strip(), "1.0.0")

    def test_credential_resolver_io_is_concurrent_and_bounded(self) -> None:
        names = (
            "HINDSIGHT_API_KEY",
            "HINDSIGHT_DATA_PLANE_TOKEN",
            "HINDSIGHT_MINT_AUTHORITY",
            "HINDSIGHT_UI_ACCESS_KEY",
        )
        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "sys.stdout.write(' ' * (512 * 1024))\n"
            "sys.stdout.flush()\n"
            "request = json.load(sys.stdin)\n"
            "values = {item['environment']: 'canary' for item in request['credentials']}\n"
            "print(json.dumps({'schema_version': 1, 'values': values}))\n",
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)
        data = self.config_data()
        data["credential_resolver"]["sha256"] = file_sha256(self.resolver)
        data["services"][0]["credentials"] = [
            {"environment": name, "locator": "pass://" + "x" * 3990} for name in names
        ]
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")

        completed = subprocess.run(
            manager._launch_argv("service", "broker"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())

    def test_install_rejects_an_unprotected_credential_resolver(self) -> None:
        self.resolver.chmod(0o522)
        data = self.config_data()
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
        )

        with self.assertRaisesRegex(PortableInstallError, "credential resolver"):
            manager.install(self.release("1.0.0"), version="1.0.0")

    def test_install_rejects_credential_resolver_symlinked_ancestry(self) -> None:
        real_parent = self.root / "real-resolver-parent"
        real_parent.mkdir()
        real_resolver = real_parent / "resolver"
        real_resolver.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        real_resolver.chmod(0o500)
        linked_parent = self.root / "linked-resolver-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        configured_resolver = linked_parent / "resolver"
        data = self.config_data()
        data["credential_resolver"] = {
            "path": str(configured_resolver),
            "sha256": file_sha256(real_resolver),
        }
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )

        with self.assertRaisesRegex(
            PortableInstallError,
            "credential resolver ancestry",
        ):
            manager.install(self.release("1.0.0"), version="1.0.0")

    def test_install_rejects_symlink_managed_roots_and_lock(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        release = self.release("1.0.0")
        self.install_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(PortableInstallError, "install root|directory"):
            self.manager().install(release, version="1.0.0")
        self.install_root.unlink()

        self.state_root.mkdir(mode=0o700, exist_ok=True)
        sentinel = self.root / "sentinel-lock"
        sentinel.write_text("preserve", encoding="utf-8")
        lock = self.state_root / "portable-install.lock"
        lock.unlink()
        lock.symlink_to(sentinel)
        with self.assertRaisesRegex(PortableInstallError, "lock"):
            self.manager().install(release, version="1.0.0")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_install_rejects_nonprivate_or_foreign_managed_roots(self) -> None:
        for index, field in enumerate(("install_root", "state_root", "data_root")):
            with self.subTest(field=field):
                root = Path(self.config_data()[field])
                root.mkdir(mode=0o700, exist_ok=True)
                root.chmod(0o755)
                manager = self.manager(
                    installation_mode="adopt" if field == "data_root" else "fresh"
                )

                with self.assertRaisesRegex(
                    PortableInstallError, "current user and private"
                ):
                    version = f"1.0.{index}"
                    manager.install(self.release(version), version=version)

                root.chmod(0o700)

        self.data_root.mkdir(mode=0o700, exist_ok=True)
        manager = self.manager(installation_mode="adopt")
        real_lstat = Path.lstat

        def foreign_data_root(path: Path):
            metadata = real_lstat(path)
            if path == self.data_root:
                values = list(metadata)
                values[4] = os.geteuid() + 1
                return os.stat_result(values)
            return metadata

        with (
            mock.patch.object(Path, "lstat", foreign_data_root),
            self.assertRaisesRegex(PortableInstallError, "ancestry is unsafe"),
        ):
            manager.install(self.release("1.0.3"), version="1.0.3")

    def test_verify_and_installed_wrapper_reject_install_root_privacy_drift(
        self,
    ) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        self.install_root.chmod(0o755)
        self.addCleanup(self.install_root.chmod, 0o700)

        with self.assertRaisesRegex(PortableInstallError, "current user and private"):
            manager.verify()

        completed = subprocess.run(
            [str(self.install_root / "bin" / "hindsight-memory"), "verify"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            timeout=10,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"installed path protection differs", completed.stderr)

    def test_install_rejects_a_user_controlled_symlink_root_ancestor(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(outside, target_is_directory=True)
        data = self.config_data()
        data["install_root"] = str(linked_parent / "install")
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )

        with self.assertRaisesRegex(PortableInstallError, "ancestry"):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertEqual(list(outside.iterdir()), [])

    def test_install_preserves_an_existing_shared_service_root_mode(self) -> None:
        self.service_root.mkdir(mode=0o755)
        self.service_root.chmod(0o755)

        self.manager().install(self.release("1.0.0"), version="1.0.0")

        self.assertEqual(self.service_root.stat().st_mode & 0o777, 0o755)

    def test_verify_rejects_owned_protection_drift(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        state = manager._load_state()
        release_root = self.install_root / state["current"]["release_path"]
        cases = (
            (self.service_root, 0o777, 0o755, "ancestry is unsafe"),
            (
                next(self.service_root.glob("*.plist")),
                0o666,
                0o600,
                "protection differs",
            ),
            (
                self.install_root / "launcher.py",
                0o700,
                0o500,
                "protection differs",
            ),
            (
                release_root / "lib" / "release.txt",
                0o600,
                0o400,
                "protection differs",
            ),
            (release_root / "lib", 0o700, 0o500, "protection differs"),
        )
        for path, drift_mode, restored_mode, error in cases:
            with self.subTest(path=path):
                path.chmod(drift_mode)
                with self.assertRaisesRegex(PortableInstallError, error):
                    manager.verify()
                path.chmod(restored_mode)

        self.assertEqual(manager.verify()["status"], "verified")

    @unittest.skipUnless(sys.platform == "darwin", "Darwin ACL semantics required")
    def test_install_rejects_acl_authority_on_roots_and_resolver(self) -> None:
        self.service_root.mkdir(mode=0o755)
        subprocess.run(
            ["/bin/chmod", "+a", "everyone allow write", str(self.service_root)],
            check=True,
        )
        self.addCleanup(
            subprocess.run,
            ["/bin/chmod", "-a#", "0", str(self.service_root)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with self.assertRaisesRegex(PortableInstallError, "ACL"):
            self.manager().install(self.release("1.0.0"), version="1.0.0")

        subprocess.run(["/bin/chmod", "-a#", "0", str(self.service_root)], check=True)
        self.resolver.chmod(0o700)
        subprocess.run(
            ["/bin/chmod", "+a", "everyone allow write", str(self.resolver)],
            check=True,
        )
        self.resolver.chmod(0o500)
        self.addCleanup(
            subprocess.run,
            ["/bin/chmod", "-a#", "0", str(self.resolver)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with self.assertRaisesRegex(PortableInstallError, "ACL"):
            self.manager().install(self.release("2.0.0"), version="2.0.0")

    @unittest.skipUnless(sys.platform == "darwin", "Darwin ACL semantics required")
    def test_verify_rejects_acl_authority_on_owned_artifacts(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        state = manager._load_state()
        release_root = self.install_root / state["current"]["release_path"]
        targets = (
            next(self.service_root.glob("*.plist")),
            self.install_root / "launcher.py",
            release_root / "lib" / "release.txt",
            release_root / "lib",
        )
        for target in targets:
            with self.subTest(target=target):
                subprocess.run(
                    ["/bin/chmod", "+a", "everyone allow write", str(target)],
                    check=True,
                )
                try:
                    with self.assertRaisesRegex(PortableInstallError, "ACL"):
                        manager.verify()
                finally:
                    subprocess.run(["/bin/chmod", "-a#", "0", str(target)], check=True)

        self.assertEqual(manager.verify()["status"], "verified")

    @unittest.skipUnless(sys.platform == "darwin", "Darwin ACL semantics required")
    def test_runtime_launchers_reject_acl_authority(self) -> None:
        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' \'{"schema_version":1,"values":{"HINDSIGHT_API_KEY":"canary"}}\'\n',
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)
        data = self.config_data()
        data["credential_resolver"]["sha256"] = file_sha256(self.resolver)
        data["health_checks"][0]["credentials"] = [
            {
                "environment": "HINDSIGHT_API_KEY",
                "locator": "pass://hindsight/data-plane",
            }
        ]
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )
        manager.install(self.release("1.0.0"), version="1.0.0")
        runtime_commands = (
            (
                self.install_root,
                [str(self.install_root / "bin" / "hindsight-memory"), "verify"],
            ),
            (
                self.install_root / "wrapper.py",
                [str(self.install_root / "bin" / "hindsight-memory"), "verify"],
            ),
            (
                self.install_root / "launcher.py",
                manager._launch_argv("health", "broker"),
            ),
            (
                self.install_root / "managed-config.json",
                manager._launch_argv("health", "broker"),
            ),
            (
                self.resolver,
                manager._launch_argv("health", "broker"),
            ),
        )
        for target, command in runtime_commands:
            with self.subTest(target=target):
                baseline = subprocess.run(
                    command,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    env={"PATH": "/usr/bin:/bin"},
                    timeout=10,
                )
                self.assertEqual(baseline.returncode, 0, baseline.stderr.decode())
                subprocess.run(
                    ["/bin/chmod", "+a", "everyone allow write", str(target)],
                    check=True,
                )
                try:
                    refused = subprocess.run(
                        command,
                        check=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        env={"PATH": "/usr/bin:/bin"},
                        timeout=10,
                    )
                    self.assertNotEqual(refused.returncode, 0)
                finally:
                    subprocess.run(["/bin/chmod", "-a#", "0", str(target)], check=True)

    def test_runtime_acl_attestors_fail_closed_when_inspection_fails(self) -> None:
        class FakeFunction:
            def __init__(self, result=None, *, set_errno=None):
                self.result = result
                self.set_errno = set_errno
                self.argtypes = None
                self.restype = None

            def __call__(self, *_args):
                if self.set_errno is not None:
                    ctypes.set_errno(self.set_errno)
                return self.result

        fake_library = mock.Mock()
        fake_library.acl_get_file = FakeFunction(0, set_errno=errno.EACCES)
        fake_library.acl_free = FakeFunction()
        fake_library.acl_to_text = FakeFunction()
        sources = (
            (
                runtime_library(portable_install_module.WRAPPER),
                SystemExit,
            ),
            (
                runtime_library(portable_install_module.SERVICE_LAUNCHER),
                ValueError,
            ),
        )
        for source, expected_error in sources:
            with self.subTest(expected_error=expected_error.__name__):
                namespace: dict[str, object] = {}
                with (
                    mock.patch.object(sys, "platform", "darwin"),
                    mock.patch.object(ctypes, "CDLL", return_value=fake_library),
                ):
                    exec(compile(source, "<runtime-attestor>", "exec"), namespace)
                    with self.assertRaises(expected_error):
                        namespace["reject_acl"](self.root)

    def test_resolver_timeout_kills_the_complete_process_group(self) -> None:
        child_pid_path = self.root / "resolver-child.pid"
        resolver = self.root / "resolver-with-child"
        resolver.write_text(
            "#!/bin/sh\n"
            "/bin/sh -c 'trap \"\" TERM; exec /bin/sleep 60' &\n"
            f"printf '%s' \"$!\" > {str(child_pid_path)!r}\n"
            "wait\n",
            encoding="utf-8",
        )
        resolver.chmod(0o500)
        source = runtime_library(portable_install_module.SERVICE_LAUNCHER)
        namespace: dict[str, object] = {}
        exec(compile(source, "<resolver-runtime>", "exec"), namespace)

        with self.assertRaisesRegex(SystemExit, "credential resolution failed"):
            namespace["resolve_credentials"](
                resolver,
                b"{}",
                {"PATH": "/usr/bin:/bin"},
                timeout_seconds=1,
            )

        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while True:
            try:
                observed = subprocess.run(
                    ["/bin/ps", "-o", "state=", "-p", str(child_pid)],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except PermissionError:
                self.skipTest("process-state inspection is sandbox-restricted")
            if observed.returncode != 0 or observed.stdout.strip().startswith("Z"):
                break
            if time.monotonic() >= deadline:
                self.fail("credential resolver descendant survived timeout")
            time.sleep(0.01)

    def test_uninstall_removes_only_unchanged_owned_files_and_preserves_data(
        self,
    ) -> None:
        self.data_root.mkdir(mode=0o700)
        sentinel = self.data_root / "existing.db"
        sentinel.write_text("preserve", encoding="utf-8")
        manager = self.manager(installation_mode="adopt")
        manager.install(self.release("1.0.0"), version="1.0.0")

        result = manager.uninstall()

        self.assertEqual(result["status"], "uninstalled")
        self.assertEqual(sentinel.read_text(), "preserve")
        self.assertTrue(self.config_path.exists())
        self.assertTrue(self.inventory.exists())
        self.assertTrue(self.resolver.exists())
        self.assertFalse(self.install_root.exists())
        self.assertEqual(list(self.service_root.glob("*")), [])

    def test_uninstall_rejects_resolver_drift_before_mutation(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        calls_before = list(self.runner.calls)
        manifests = tuple(manager.config.service_root.iterdir())
        self.resolver.chmod(0o700)
        self.resolver.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
        self.resolver.chmod(0o500)

        with self.assertRaisesRegex(
            PortableInstallError,
            "credential resolver digest mismatch",
        ):
            manager.uninstall()

        self.assertEqual(self.runner.calls, calls_before)
        self.assertFalse(manager._uninstall_transaction_path.exists())
        self.assertTrue(self.install_root.is_dir())
        self.assertTrue(all(path.is_file() for path in manifests))

    def test_uninstall_rejects_external_owned_install_paths(self) -> None:
        manager = self.manager(health_runner=lambda _check, _release: True)
        manager.install(self.release("1.0.0"), version="1.0.0")
        external = self.root / "external-owned-file"
        external.write_text("preserve", encoding="utf-8")
        external.chmod(0o500)
        state = manager._load_state()
        state["owned_install_files"][str(external)] = file_sha256(external)
        manager._write_state(state)

        with self.assertRaisesRegex(PortableInstallError, "ownership differs"):
            manager.uninstall()

        self.assertEqual(external.read_text(encoding="utf-8"), "preserve")
        self.assertTrue(self.install_root.exists())

    def test_systemd_uninstall_reloads_after_owned_unit_deletion(self) -> None:
        manager = self.manager(platform="systemd-user")
        manager.install(self.release("1.0.0"), version="1.0.0")
        self.runner.calls.clear()

        manager.uninstall()

        reloads = [
            call
            for call in self.runner.calls
            if call == ("/usr/bin/systemctl", "--user", "daemon-reload")
        ]
        self.assertEqual(len(reloads), 2)
        self.assertEqual(list(self.service_root.glob("*")), [])

    def test_uninstall_fsyncs_each_mutated_namespace(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")

        with mock.patch(
            "hindsight_memory_control_plane.portable_install._fsync_directory",
            wraps=portable_install_module._fsync_directory,
        ) as fsync_directory:
            manager.uninstall()

        observed = [call.args[0] for call in fsync_directory.call_args_list]
        self.assertIn(self.service_root, observed)
        self.assertGreaterEqual(observed.count(self.install_root.parent), 2)
        self.assertIn(self.state_root, observed)

    def test_interrupted_uninstall_restores_the_verified_installation(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")

        with (
            mock.patch.object(Path, "rename", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.uninstall()

        self.assertTrue(manager._uninstall_transaction_path.is_file())
        verification = self.manager().verify()
        self.assertEqual(verification["current"]["version"], "1.0.0")
        self.assertFalse(manager._uninstall_transaction_path.exists())
        self.assertTrue(self.install_root.is_dir())

    def test_committed_uninstall_finishes_after_interrupted_tree_removal(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")

        with (
            mock.patch.object(
                manager,
                "_remove_uninstall_tombstone",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.uninstall()

        journal = json.loads(manager._uninstall_transaction_path.read_text())
        self.assertEqual(journal["phase"], "committed")
        self.assertFalse(self.install_root.exists())
        self.assertTrue(manager._uninstall_tombstone_path.is_dir())

        result = self.manager().uninstall()

        self.assertEqual(result["status"], "absent")
        self.assertFalse(manager._uninstall_transaction_path.exists())
        self.assertFalse(manager._uninstall_tombstone_path.exists())

    def test_committed_uninstall_refuses_a_symlink_in_the_tombstone(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        with (
            mock.patch.object(
                manager,
                "_remove_uninstall_tombstone",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.uninstall()
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        (manager._uninstall_tombstone_path / "escape").symlink_to(
            outside, target_is_directory=True
        )

        with self.assertRaisesRegex(
            PortableInstallError, "uninstall tombstone identity is invalid"
        ):
            self.manager().uninstall()

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_uninstall_refuses_to_delete_drifted_owned_manifest(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        service = self.service_root / "io.nisavid.hindsight.synthetic.broker.plist"
        service.write_text("operator change", encoding="utf-8")

        with self.assertRaisesRegex(PortableInstallError, "owned file drift"):
            manager.uninstall()

        self.assertEqual(service.read_text(), "operator change")
        self.assertTrue(self.install_root.exists())

    def _inactive_release_file(self, manager: PortableInstallationManager) -> Path:
        manager.install(self.release("1.0.0"), version="1.0.0")
        self.upgrade(manager, self.release("2.0.0"), version="2.0.0")
        state = manager._load_state()
        inactive = next(
            release
            for release in state["releases"].values()
            if release["version"] == "1.0.0"
        )
        return self.install_root / inactive["release_path"] / "lib" / "release.txt"

    def test_uninstall_refuses_inactive_release_content_drift(self) -> None:
        manager = self.manager()
        target = self._inactive_release_file(manager)
        target.chmod(0o600)
        target.write_text("drift", encoding="utf-8")

        with self.assertRaisesRegex(
            PortableInstallError, "installed release verification failed"
        ):
            manager.uninstall()

        self.assertTrue(self.install_root.exists())

    def test_uninstall_refuses_an_inactive_release_symlink(self) -> None:
        manager = self.manager()
        target = self._inactive_release_file(manager)
        target.parent.chmod(0o700)
        target.unlink()
        outside = self.root / "outside-release"
        outside.write_text("preserve", encoding="utf-8")
        target.symlink_to(outside)

        with self.assertRaises(PortableInstallError):
            manager.uninstall()

        self.assertEqual(outside.read_text(encoding="utf-8"), "preserve")
        self.assertTrue(self.install_root.exists())

    def test_uninstall_refuses_an_inactive_release_type_change(self) -> None:
        manager = self.manager()
        target = self._inactive_release_file(manager)
        target.parent.chmod(0o700)
        target.unlink()
        target.mkdir()

        with self.assertRaises(PortableInstallError):
            manager.uninstall()

        self.assertTrue(target.is_dir())
        self.assertTrue(self.install_root.exists())

    def test_plain_environment_rejects_secret_shaped_names(self) -> None:
        for name in (
            "API_TOKEN",
            "OPENAI_KEY",
            "OPENAI_APIKEY",
            "OPENAI_APIKEY_FILE",
            "CLIENTSECRET",
            "SERVICE_CLIENTSECRET_PATH",
            "ACCESSTOKEN",
            "SSH_PRIVATE_KEY",
            "AUTHORIZATION",
            "BEARER",
        ):
            with self.subTest(name=name):
                data = self.config_data()
                data["services"][0]["environment"][name] = "cleartext"

                with self.assertRaisesRegex(
                    PortableInstallError, "credential environment"
                ):
                    InstallationConfig.load(data, source_path=self.config_path)

        for name in ("MONKEY", "HOCKEY", "TURKEY"):
            with self.subTest(name=name):
                data = self.config_data()
                data["services"][0]["environment"][name] = "ordinary"
                InstallationConfig.load(data, source_path=self.config_path)

    def test_config_requires_exact_schema_version_and_protected_path(self) -> None:
        for schema_version in (True, 1.0, "1"):
            with self.subTest(schema_version=schema_version):
                data = self.config_data()
                data["schema_version"] = schema_version
                with self.assertRaisesRegex(PortableInstallError, "unsupported"):
                    InstallationConfig.load(data, source_path=self.config_path)

        data = self.config_data()
        data["services"][0]["environment"]["PATH"] = (
            "/home/example/.local/bin:/usr/bin:/bin"
        )
        with self.assertRaisesRegex(PortableInstallError, "protected system"):
            InstallationConfig.load(data, source_path=self.config_path)

        for field, value in (
            ("state_root", str(self.root / "state'root")),
            ("arguments", ["value'with-quote"]),
        ):
            with self.subTest(field=field):
                data = self.config_data()
                if field == "state_root":
                    data[field] = value
                else:
                    data["services"][0][field] = value
                with self.assertRaisesRegex(
                    PortableInstallError, "absolute path|apostrophes"
                ):
                    InstallationConfig.load(data, source_path=self.config_path)

    def test_arguments_reject_literal_credentials_but_allow_env_references(
        self,
    ) -> None:
        for arguments in (
            ["--authorization=Bearer canary"],
            ["--api-key", "canary"],
            ["--private_key=canary"],
            ["prefix", "Bearer canary"],
            ["--header", "X-API-Key: canary"],
            ["-H", "Authorization: Basic canary"],
            ["-HX-API-Key: canary"],
            ["-HCookie: session=canary"],
        ):
            with self.subTest(arguments=arguments):
                data = self.config_data()
                data["services"][0]["arguments"] = arguments
                with self.assertRaisesRegex(
                    PortableInstallError, "cannot contain credentials"
                ):
                    InstallationConfig.load(data, source_path=self.config_path)

        for arguments in (
            ["--data-plane-token-env", "HINDSIGHT_DATA_PLANE_TOKEN"],
            ["--api-key-env=HINDSIGHT_API_KEY"],
            ["--max-tokens", "4096"],
            ["--tokenizer", "cl100k_base"],
            ["--header", "X-Note: authorization: docs"],
        ):
            with self.subTest(arguments=arguments):
                data = self.config_data()
                data["services"][0]["arguments"] = arguments
                InstallationConfig.load(data, source_path=self.config_path)

    def test_persisted_schema_versions_require_json_integers(self) -> None:
        manager = self.manager()
        installed = manager.install(self.release("1.0.0"), version="1.0.0")
        state_path = manager._state_path
        original_state = json.loads(state_path.read_text(encoding="utf-8"))
        calls_before = list(self.runner.calls)

        for schema_version in (True, 1.0):
            with self.subTest(document="state", schema_version=schema_version):
                state = json.loads(json.dumps(original_state))
                state["schema_version"] = schema_version
                state_path.write_text(
                    json.dumps(state, sort_keys=True), encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    PortableInstallError, "state (?:identity|schema_version)"
                ):
                    manager.verify()
                self.assertEqual(self.runner.calls, calls_before)
        state_path.write_text(
            json.dumps(original_state, sort_keys=True), encoding="utf-8"
        )

        with (
            mock.patch.object(
                manager, "_publish_release_record", side_effect=KeyboardInterrupt
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.upgrade(
                self.release("2.0.0"),
                version="2.0.0",
                expected_current_binding_generation_digest=installed[
                    "binding_generation_digest"
                ],
            )
        journal_path = manager._transaction_path
        original_journal = json.loads(journal_path.read_text(encoding="utf-8"))
        calls_before = list(self.runner.calls)
        mutations = {
            "journal": lambda value: value.__setitem__("schema_version", 2.0),
            "prior": lambda value: value["prior_state"].__setitem__(
                "schema_version", True
            ),
        }
        for document, mutate in mutations.items():
            with self.subTest(document=document):
                journal = json.loads(json.dumps(original_journal))
                mutate(journal)
                journal_path.write_text(
                    json.dumps(journal, sort_keys=True), encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    PortableInstallError,
                    "transaction (?:identity|prestate|schema_version)",
                ):
                    manager.verify()
                self.assertEqual(self.runner.calls, calls_before)

    def test_uninstall_journal_schema_version_requires_a_json_integer(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        with (
            mock.patch.object(Path, "rename", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            manager.uninstall()
        journal = json.loads(manager._uninstall_transaction_path.read_text())
        journal["schema_version"] = 1.0
        manager._uninstall_transaction_path.write_text(
            json.dumps(journal, sort_keys=True), encoding="utf-8"
        )
        calls_before = list(self.runner.calls)

        with self.assertRaisesRegex(
            PortableInstallError,
            "uninstall transaction (?:identity|schema_version)",
        ):
            manager.verify()

        self.assertEqual(self.runner.calls, calls_before)
        self.assertTrue(self.install_root.is_dir())

    def test_service_launcher_requires_an_integer_resolver_schema(self) -> None:
        release = self.release("1.0.0")
        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            'print(json.dumps({"schema_version": True, "values": '
            '{"HINDSIGHT_API_KEY": "canary"}}))\n',
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)
        data = self.config_data()
        data["credential_resolver"]["sha256"] = file_sha256(self.resolver)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
        )
        manager.install(release, version="1.0.0")

        completed = subprocess.run(
            [
                sys.executable,
                str(self.install_root / "launcher.py"),
                "--config",
                str(self.config_path),
                "--service",
                "broker",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": os.environ["PATH"]},
            timeout=10,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn(b"canary", completed.stderr)

    def test_credentials_reject_process_control_environment_names(self) -> None:
        for name in (
            "PATH",
            "PYTHONPATH",
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
            "NODE_OPTIONS",
            "RUBYOPT",
            "PERL5OPT",
        ):
            with self.subTest(name=name):
                data = self.config_data()
                data["services"][0]["environment"].pop(name, None)
                data["services"][0]["credentials"] = [
                    {"environment": name, "locator": "pass://hindsight/value"}
                ]
                with self.assertRaisesRegex(
                    PortableInstallError, "not an authorized secret destination"
                ):
                    InstallationConfig.load(data, source_path=self.config_path)

    def test_config_rejects_an_empty_health_check_set(self) -> None:
        data = self.config_data()
        data["health_checks"] = []

        with self.assertRaisesRegex(
            PortableInstallError, "health_checks must be a non-empty list"
        ):
            InstallationConfig.load(data, source_path=self.config_path)

    def test_service_launcher_rejects_duplicate_and_oversized_resolver_output(
        self,
    ) -> None:
        release = self.release("1.0.0")
        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/usr/bin/env python3\n"
            'print(\'{"schema_version":1,"schema_version":1,"values":{"HINDSIGHT_API_KEY":"canary"}}\')\n',
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)
        data = self.config_data()
        data["credential_resolver"]["sha256"] = file_sha256(self.resolver)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        manager = PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
        )
        manager.install(release, version="1.0.0")
        launcher = self.install_root / "launcher.py"
        command = [
            sys.executable,
            str(launcher),
            "--config",
            str(self.config_path),
            "--service",
            "broker",
        ]

        duplicate = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": os.environ["PATH"]},
            timeout=10,
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertNotIn(b"canary", duplicate.stderr)

        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdout.write('x' * (1024 * 1024 + 1))\n",
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)
        data["credential_resolver"]["sha256"] = file_sha256(self.resolver)
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        state = json.loads((self.install_root / "install-state.json").read_text())
        state["config_file_digest"] = file_sha256(self.config_path)
        (self.install_root / "install-state.json").chmod(0o600)
        (self.install_root / "install-state.json").write_text(
            json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        oversized = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": os.environ["PATH"]},
            timeout=10,
        )
        self.assertNotEqual(oversized.returncode, 0)
        self.assertLess(len(oversized.stderr), 1024)

    def test_release_tree_rejects_symlinks(self) -> None:
        release = self.release("1.0.0")
        os.symlink("release.txt", release / "lib" / "alias.txt")
        manager = self.manager()

        with self.assertRaisesRegex(PortableInstallError, "symlink"):
            manager.install(release, version="1.0.0")

    def test_config_rejects_unknown_fields_and_relative_paths(self) -> None:
        unknown = self.config_data()
        unknown["surprise"] = True
        with self.assertRaisesRegex(PortableInstallError, "unknown fields"):
            InstallationConfig.load(unknown, source_path=self.config_path)

        relative = self.config_data()
        relative["install_root"] = "relative"
        with self.assertRaisesRegex(PortableInstallError, "absolute"):
            InstallationConfig.load(relative, source_path=self.config_path)

        for field in (
            "install_root",
            "state_root",
            "data_root",
            "service_root",
            "inventory_path",
            "python_executable",
            "npx_executable",
            "uvx_executable",
            "zsh_executable",
        ):
            with self.subTest(field=field, component="dot-dot"):
                noncanonical = self.config_data()
                noncanonical[field] = str(self.root / "stray" / ".." / field)
                with self.assertRaisesRegex(PortableInstallError, "absolute path"):
                    InstallationConfig.load(noncanonical, source_path=self.config_path)
                self.assertFalse((self.root / "stray").exists())

    def test_config_rejects_line_breaks_in_rendered_paths_and_text(self) -> None:
        mutations = (
            ("install_root", str(self.install_root) + "\nInjected=true"),
            ("inventory_path", str(self.inventory) + "\runsafe"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                data = self.config_data(platform="systemd-user")
                data[field] = value
                with self.assertRaisesRegex(PortableInstallError, "invalid|absolute"):
                    InstallationConfig.load(data, source_path=self.config_path)

        for field, value in (
            ("arguments", ["status\nEnvironment=UNSAFE=1"]),
            ("environment", {"PATH": "/usr/bin\r/bin"}),
        ):
            with self.subTest(field=field):
                data = self.config_data(platform="systemd-user")
                data["services"][0][field] = value
                with self.assertRaisesRegex(
                    PortableInstallError, "invalid|protected system"
                ):
                    InstallationConfig.load(data, source_path=self.config_path)

    def test_config_rejects_duplicate_health_check_identities(self) -> None:
        data = self.config_data()
        data["health_checks"].append(dict(data["health_checks"][0]))

        with self.assertRaisesRegex(
            PortableInstallError, "health check identities must be unique"
        ):
            InstallationConfig.load(data, source_path=self.config_path)

    def test_release_files_keep_executable_intent_but_are_not_mutable(self) -> None:
        manager = self.manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        active = json.loads((self.install_root / "active.json").read_text())
        executable = (
            self.install_root / active["release_path"] / "bin" / "hindsight-memory"
        )
        data = self.install_root / active["release_path"] / "lib" / "release.txt"

        self.assertTrue(executable.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(executable.stat().st_mode & 0o222, 0)
        self.assertEqual(data.stat().st_mode & 0o777, 0o400)

    def test_release_publication_fsyncs_immutable_files_and_directories(self) -> None:
        observed_modes: list[int] = []
        original_fsync = os.fsync
        manager = self.manager()
        original_publish = manager._publish_release_record

        def record_fsync(descriptor: int) -> None:
            observed_modes.append(os.fstat(descriptor).st_mode)
            original_fsync(descriptor)

        def observe_publish(source, release, temporary):
            with mock.patch(
                "hindsight_memory_control_plane.portable_install.os.fsync",
                side_effect=record_fsync,
            ):
                original_publish(source, release, temporary)

        with mock.patch.object(
            manager, "_publish_release_record", side_effect=observe_publish
        ):
            manager.install(self.release("1.0.0"), version="1.0.0")

        self.assertTrue(
            any(
                stat.S_ISREG(mode) and mode & 0o777 in {0o400, 0o500}
                for mode in observed_modes
            )
        )
        self.assertGreaterEqual(
            sum(stat.S_ISDIR(mode) for mode in observed_modes),
            2,
        )

    def test_cli_exposes_portable_lifecycle_without_an_ambient_state_dir(self) -> None:
        module = runpy.run_path(str(ROOT / "bin" / "hindsight-memory"))
        argument_parser = module["parser"]()
        commands = (
            [
                "install",
                "--config",
                str(self.config_path),
                "--release-root",
                str(self.root),
                "--version",
                "1.0.0",
            ],
            [
                "upgrade",
                "--config",
                str(self.config_path),
                "--release-root",
                str(self.root),
                "--version",
                "2.0.0",
                "--expected-current-binding-generation-digest",
                "b" * 64,
            ],
            ["verify", "--config", str(self.config_path)],
            [
                "rollback",
                "--config",
                str(self.config_path),
                "--expected-current-release-digest",
                "a" * 64,
            ],
            ["uninstall", "--config", str(self.config_path)],
        )
        for argv in commands:
            with self.subTest(argv=argv):
                parsed = argument_parser.parse_args(argv)
                module["_validate_state_directory_argument"](argument_parser, parsed)

        legacy = argument_parser.parse_args(
            ["validate", "--inventory", str(self.inventory)]
        )
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            module["_validate_state_directory_argument"](argument_parser, legacy)
        portable_with_ambient = argument_parser.parse_args(
            [
                "--state-dir",
                str(self.state_root),
                "verify",
                "--config",
                str(self.config_path),
            ]
        )
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            module["_validate_state_directory_argument"](
                argument_parser, portable_with_ambient
            )

    def test_cli_exposes_supported_data_identity_evidence_commands(self) -> None:
        module = runpy.run_path(str(ROOT / "bin" / "hindsight-memory"))
        argument_parser = module["parser"]()
        evidence = argument_parser.parse_args(
            [
                "data-identity",
                "evidence",
                "--config",
                str(self.config_path),
                "--candidate-release-root",
                str(self.root),
                "--candidate-release-identity",
                str(self.root / "identity.json"),
                "--artifact",
                str(self.root / "backup.age"),
                "--backup-attestation",
                str(self.root / "backup.json"),
                "--output",
                str(self.root / "evidence.json"),
                "--age",
                str(self.root / "age"),
                "--age-identity",
                str(self.root / "key.txt"),
                "--recipient",
                "age1example",
                "--postgres-bin-dir",
                str(self.root / "postgres"),
                "--run-root",
                "/private/tmp/hindsight-operation-recovery-"
                + "1" * 32,
                "--restore-run-root",
                "/private/tmp/hindsight-operation-recovery-"
                + "2" * 32,
                "--port",
                "55432",
            ]
        )
        observe = argument_parser.parse_args(
            [
                "data-identity",
                "observe",
                "--config",
                str(self.config_path),
                "--base-evidence",
                str(self.root / "evidence.json"),
                "--output",
                str(self.root / "observation.json"),
            ]
        )

        self.assertIs(
            evidence.run,
            module["data_identity_rebind_evidence_command"],
        )
        self.assertIs(
            observe.run,
            module["data_identity_rebind_observe_command"],
        )

    def test_cli_requires_upgrade_to_run_from_the_candidate_release(self) -> None:
        module = runpy.run_path(str(ROOT / "bin" / "hindsight-memory"))
        argument_parser = module["parser"]()
        mismatched = argument_parser.parse_args(
            [
                "upgrade",
                "--config",
                str(self.config_path),
                "--release-root",
                str(self.root),
                "--version",
                "2.0.0",
                "--expected-current-binding-generation-digest",
                "b" * 64,
            ]
        )
        with self.assertRaisesRegex(PortableInstallError, "candidate release's"):
            module["portable_upgrade_command"](mismatched)

        candidate = argument_parser.parse_args(
            [
                "upgrade",
                "--config",
                str(self.config_path),
                "--release-root",
                str(ROOT),
                "--version",
                "2.0.0",
                "--expected-current-binding-generation-digest",
                "b" * 64,
            ]
        )
        manager = mock.Mock()
        manager.upgrade.return_value = {"status": "upgraded"}
        function_globals = module["portable_upgrade_command"].__globals__
        with mock.patch.dict(
            function_globals,
            {
                "_portable_manager": lambda _args: manager,
                "_print_result": lambda _result: 0,
            },
        ):
            self.assertEqual(module["portable_upgrade_command"](candidate), 0)
        manager.upgrade.assert_called_once_with(
            str(ROOT),
            version="2.0.0",
            expected_current_binding_generation_digest="b" * 64,
        )

    def test_candidate_cli_does_not_mutate_release_with_bytecode(self) -> None:
        release = self.root / "candidate-release"
        shutil.copytree(
            ROOT,
            release,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

        completed = subprocess.run(
            [sys.executable, "-I", str(release / "bin" / "hindsight-memory"), "--help"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(list(release.rglob("__pycache__")), [])
        self.assertEqual(list(release.rglob("*.pyc")), [])

    def rebind_manager(
        self, *, fleet_profiles: str = "systalyze"
    ) -> PortableInstallationManager:
        data = self.config_data()
        data["services"][0]["environment"].update(
            {
                "HINDSIGHT_EMBED_STATE_DIR": str(self.state_root / "embed"),
                "HINDSIGHT_EMBED_FLEET_PROFILES": fleet_profiles,
                "HINDSIGHT_EMBED_AUTOSTART_DAEMON": "true",
                "HINDSIGHT_EMBED_AUTOSTART_UI": "true",
            }
        )
        self.config_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        return PortableInstallationManager(
            InstallationConfig.load(data, source_path=self.config_path),
            command_runner=self.runner,
            health_runner=lambda _check, _release: True,
        )

    def rebind_evidence(
        self,
        artifact: Path,
        *,
        now: int,
    ) -> dict[str, Any]:
        data_metadata = self.data_root.lstat()
        postgres_root = self.data_root / "data"
        postgres_metadata = postgres_root.lstat()
        return build_rebind_evidence(
            artifact=artifact,
            data_root=self.data_root,
            data_root_device=data_metadata.st_dev,
            data_root_inode=data_metadata.st_ino,
            postgres_data_root=postgres_root,
            postgres_data_device=postgres_metadata.st_dev,
            postgres_data_inode=postgres_metadata.st_ino,
            collected_at=now - 10,
            expires_at=now + 300,
            backup_created_at=now - 5,
            restored_at=now - 1,
            postmaster_pid=1234,
            postmaster_start_time=now - 100,
        )

    def rebind_inputs(
        self,
        *,
        fleet_profiles: str = "systalyze",
    ) -> tuple[
        PortableInstallationManager,
        dict[str, Any],
        bytes,
        Path,
    ]:
        manager = self.rebind_manager(fleet_profiles=fleet_profiles)
        manager.install(self.release("1.0.0"), version="1.0.0")
        displaced = self.root / "displaced-data"
        self.data_root.rename(displaced)
        self.data_root.mkdir(mode=0o700)
        (self.data_root / "data").mkdir(mode=0o700)
        sentinel = self.data_root / "data" / "database-sentinel"
        sentinel.write_bytes(b"database-unchanged")
        backup_root = self.state_root / "data-identity-rebind" / "backups"
        backup_root.mkdir(parents=True, mode=0o700)
        artifact = backup_root / "fresh-full-schema.dump.age"
        artifact.write_bytes(b"age-encryption.org/v1\nencrypted-full-schema")
        evidence = self.rebind_evidence(artifact, now=1000)
        prestate = (self.install_root / "install-state.json").read_bytes()
        return manager, evidence, prestate, sentinel

    def prepared_rebind(
        self,
    ) -> tuple[
        PortableInstallationManager,
        dict[str, Any],
        dict[str, Any],
        bytes,
        Path,
    ]:
        manager, evidence, prestate, sentinel = self.rebind_inputs()
        plan = manager.data_identity_rebind_plan(evidence, now=1000)
        return manager, evidence, dict(plan), prestate, sentinel

    def test_data_identity_rebind_migrates_same_launchd_root_to_v2(self) -> None:
        manager = self.rebind_manager()

        def legacy_identity(path, metadata, *, platform):
            self.assertEqual(platform, "launchd")
            return digest(
                {
                    "path": str(path.resolve(strict=True)),
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                }
            )

        with mock.patch.object(
            portable_install_module,
            "_data_root_identity_digest",
            side_effect=legacy_identity,
        ):
            installed = manager.install(self.release("1.0.0"), version="1.0.0")
        root_before = self.data_root.lstat()
        (self.data_root / "data").mkdir(mode=0o700)
        sentinel = self.data_root / "data" / "database-sentinel"
        sentinel.write_bytes(b"database-unchanged")
        backup_root = self.state_root / "data-identity-rebind" / "backups"
        backup_root.mkdir(parents=True, mode=0o700)
        artifact = backup_root / "fresh-full-schema.dump.age"
        artifact.write_bytes(b"age-encryption.org/v1\nencrypted-full-schema")
        evidence = self.rebind_evidence(artifact, now=1000)

        plan = manager.data_identity_rebind_plan(evidence, now=1000)
        self.assertEqual(
            plan["old_data_identity_digest"],
            installed["data_identity_digest"],
        )
        self.assertNotEqual(
            plan["new_data_identity_digest"],
            plan["old_data_identity_digest"],
        )
        manager.data_identity_rebind_apply(
            plan,
            approval_digest=plan["plan_digest"],
            pre_apply_evidence_value=self.pre_apply_evidence(evidence),
            now=1000,
        )
        verified = manager.data_identity_rebind_verify(
            plan,
            self.post_rebind_evidence(evidence),
            now=1001,
        )
        root_after = self.data_root.lstat()

        self.assertEqual(verified["status"], "verified")
        self.assertEqual(manager.verify()["status"], "verified")
        self.assertEqual(
            (root_after.st_dev, root_after.st_ino, root_after.st_birthtime),
            (root_before.st_dev, root_before.st_ino, root_before.st_birthtime),
        )
        self.assertEqual(sentinel.read_bytes(), b"database-unchanged")

    def test_data_identity_rebind_plan_refuses_evidence_profile_mismatch(
        self,
    ) -> None:
        manager, evidence, _prestate, _sentinel = self.rebind_inputs()
        evidence["profile_id"] = "other"
        with self.assertRaisesRegex(PortableInstallError, "profile differs"):
            manager.data_identity_rebind_plan(evidence, now=1000)

    def test_data_identity_rebind_plan_does_not_require_pre_repair_health(
        self,
    ) -> None:
        manager, evidence, _prestate, _sentinel = self.rebind_inputs()
        health_runner = mock.Mock(return_value=True)
        manager._health_runner = health_runner

        with mock.patch.object(
            PortableInstallationManager,
            "_verify_service_manager",
        ) as service_manager_check:
            plan = manager.data_identity_rebind_plan(evidence, now=1000)

        health_runner.assert_not_called()
        service_manager_check.assert_not_called()
        self.assertEqual(plan["action"], "rebind-data-identity")

    def test_data_identity_profile_refuses_incomplete_desired_state_binding(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            PortableInstallError, "environment value is invalid"
        ):
            self.rebind_manager(fleet_profiles="")
        incomplete_binding_manager = self.rebind_manager()
        service = incomplete_binding_manager.config.services[0]
        incomplete_binding_manager.config = replace(
            incomplete_binding_manager.config,
            services=(
                replace(
                    service,
                    environment=(
                        (
                            "HINDSIGHT_EMBED_STATE_DIR",
                            str(self.state_root / "embed"),
                        ),
                        ("HINDSIGHT_EMBED_FLEET_PROFILES", ""),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(PortableInstallError, "binding is incomplete"):
            incomplete_binding_manager._data_identity_profile()

    def test_data_identity_profile_refuses_invalid_identifier(self) -> None:
        with self.assertRaisesRegex(
            PortableInstallError, "fleet profile binding is invalid"
        ):
            self.rebind_manager(
                fleet_profiles="systalyze,not a profile"
            )._data_identity_profile()

    def test_data_identity_rebind_plan_refuses_multiple_profiles(self) -> None:
        manager, evidence, _prestate, _sentinel = self.rebind_inputs(
            fleet_profiles="systalyze,other"
        )
        with self.assertRaisesRegex(PortableInstallError, "exactly one"):
            manager.data_identity_rebind_plan(evidence, now=1000)

    def test_data_identity_rebind_plan_refuses_already_bound_root(self) -> None:
        manager = self.rebind_manager()
        manager.install(self.release("1.0.0"), version="1.0.0")
        (self.data_root / "data").mkdir(mode=0o700)
        backup_root = self.state_root / "data-identity-rebind" / "backups"
        backup_root.mkdir(parents=True, mode=0o700)
        artifact = backup_root / "fresh-full-schema.dump.age"
        artifact.write_bytes(b"age-encryption.org/v1\nencrypted-full-schema")
        evidence = self.rebind_evidence(artifact, now=1000)

        with self.assertRaisesRegex(PortableInstallError, "already bound"):
            manager.data_identity_rebind_plan(evidence, now=1000)

    def test_data_identity_replanning_uses_distinct_artifact_sets(self) -> None:
        manager, evidence, _prestate, _sentinel = self.rebind_inputs()

        first = manager.data_identity_rebind_plan(evidence, now=1000)
        second = manager.data_identity_rebind_plan(evidence, now=1001)

        self.assertNotEqual(first["plan_digest"], second["plan_digest"])
        for field in (
            "rollback_bundle_path",
            "authorization_receipt_path",
            "application_receipt_path",
            "verification_receipt_path",
        ):
            self.assertNotEqual(first[field], second[field])

    def assert_rebind_unmutated(self, plan: dict[str, Any], prestate: bytes) -> None:
        self.assertEqual(
            (self.install_root / "install-state.json").read_bytes(), prestate
        )
        self.assertFalse(Path(plan["rollback_bundle_path"]).exists())
        self.assertFalse(Path(plan["authorization_receipt_path"]).exists())

    @contextmanager
    def expire_plan_at_rebind_mutation_boundary(self):
        original_verify = portable_install_module.verify_rebind_plan
        original_backup_verify = portable_install_module.verify_rebind_backup_artifact
        boundary = {"backup_checked": False}

        def mark_backup_boundary(value):
            result = original_backup_verify(value)
            boundary["backup_checked"] = True
            return result

        def expire_at_mutation_boundary(value, **arguments):
            if boundary["backup_checked"]:
                raise portable_install_module.DataIdentityRebindError(
                    "data-identity rebind plan is expired"
                )
            return original_verify(value, **arguments)

        with (
            mock.patch.object(
                portable_install_module,
                "verify_rebind_plan",
                side_effect=expire_at_mutation_boundary,
            ),
            mock.patch.object(
                portable_install_module,
                "verify_rebind_backup_artifact",
                side_effect=mark_backup_boundary,
            ),
        ):
            yield boundary

    def test_data_identity_rebind_rejects_out_of_tree_artifacts(self) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        out_of_tree = {
            **plan,
            "rollback_bundle_path": str(self.root / "outside.json"),
        }
        out_of_tree["plan_digest"] = digest(
            {key: value for key, value in out_of_tree.items() if key != "plan_digest"}
        )
        with self.assertRaisesRegex(PortableInstallError, "outside controller state"):
            manager.data_identity_rebind_apply(
                out_of_tree,
                approval_digest=out_of_tree["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )
        self.assert_rebind_unmutated(plan, prestate)

    def test_data_identity_rebind_rejects_expired_plan(self) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        with self.assertRaisesRegex(PortableInstallError, "plan is expired"):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=plan["expires_at"],
            )
        self.assert_rebind_unmutated(plan, prestate)

    def test_data_identity_rebind_rechecks_expiry_after_lock_acquisition(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        with (
            self.expire_plan_at_rebind_mutation_boundary() as boundary,
            self.assertRaisesRegex(PortableInstallError, "plan is expired"),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assertTrue(boundary["backup_checked"])
        self.assertEqual(
            (self.install_root / "install-state.json").read_bytes(), prestate
        )
        self.assertTrue(Path(plan["rollback_bundle_path"]).is_file())
        self.assertTrue(Path(plan["authorization_receipt_path"]).is_file())

    def test_data_identity_rebind_apply_rehashes_backup_before_state_write(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        Path(plan["backup"]["artifact_path"]).unlink()

        with self.assertRaisesRegex(
            PortableInstallError, "backup artifact is unavailable"
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assertEqual(
            (self.install_root / "install-state.json").read_bytes(), prestate
        )

    def test_data_identity_rebind_apply_rechecks_database_continuity(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        changed = self.pre_apply_evidence(evidence)
        changed["database"]["generation_before"] = "generation-2"
        changed["database"]["generation_after"] = "generation-2"
        changed = reseal_rebind_evidence(changed)

        with self.assertRaisesRegex(
            PortableInstallError, "continuity evidence differs"
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=changed,
                now=1000,
            )

        self.assert_rebind_unmutated(plan, prestate)

    def test_data_identity_rebind_plan_refuses_pending_recovery_without_mutation(
        self,
    ) -> None:
        manager, evidence, prestate, _sentinel = self.rebind_inputs()
        pending = b'{"operator":"must-recover-explicitly"}\n'
        manager._transaction_path.write_bytes(pending)

        with self.assertRaisesRegex(
            PortableInstallError, "requires quiescent installer state"
        ):
            manager.data_identity_rebind_plan(evidence, now=1000)

        self.assertEqual(manager._transaction_path.read_bytes(), pending)
        self.assertEqual(
            (self.install_root / "install-state.json").read_bytes(), prestate
        )

    def test_data_identity_rebind_plan_refuses_external_binding_drift(
        self,
    ) -> None:
        manager, evidence, prestate, _sentinel = self.rebind_inputs()
        inventory = manager.config.inventory_path
        inventory.write_bytes(inventory.read_bytes() + b"\n")

        with self.assertRaisesRegex(
            PortableInstallError, "installed consumer binding differs"
        ):
            manager.data_identity_rebind_plan(evidence, now=1000)

        self.assertEqual(
            (self.install_root / "install-state.json").read_bytes(), prestate
        )

    def test_data_identity_rebind_apply_refuses_external_binding_drift(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        inventory = manager.config.inventory_path
        inventory.write_bytes(inventory.read_bytes() + b"\n")

        with self.assertRaisesRegex(
            PortableInstallError, "installed consumer binding differs"
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assert_rebind_unmutated(plan, prestate)

    def test_data_identity_rebind_apply_rechecks_binding_after_backup_hash(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        inventory = manager.config.inventory_path
        original_backup_verify = portable_install_module.verify_rebind_backup_artifact

        def drift_after_backup(value):
            result = original_backup_verify(value)
            inventory.write_bytes(inventory.read_bytes() + b"\n")
            return result

        with (
            mock.patch.object(
                portable_install_module,
                "verify_rebind_backup_artifact",
                side_effect=drift_after_backup,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "installed consumer binding differs"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assertEqual(
            (self.install_root / "install-state.json").read_bytes(),
            prestate,
        )

    def test_data_identity_rebind_preserves_concurrent_state_at_final_cas(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        concurrent_state = json.loads(prestate)
        concurrent_state["npx_alias"] = "/concurrent/installer-owned-alias"
        concurrent_bytes = (
            json.dumps(concurrent_state, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )
        original_backup_verify = portable_install_module.verify_rebind_backup_artifact

        def publish_concurrent_state(value):
            result = original_backup_verify(value)
            state_path.write_bytes(concurrent_bytes)
            return result

        with (
            mock.patch.object(
                portable_install_module,
                "verify_rebind_backup_artifact",
                side_effect=publish_concurrent_state,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "installation state changed after planning"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assertEqual(state_path.read_bytes(), concurrent_bytes)
        self.assertTrue(Path(plan["rollback_bundle_path"]).is_file())
        self.assertTrue(Path(plan["authorization_receipt_path"]).is_file())
        self.assertFalse(Path(plan["application_receipt_path"]).exists())

    def test_data_identity_rebind_rejects_unpaired_final_state_snapshot(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        original_snapshot = manager._require_quiescent_rebind_state_locked
        snapshot_calls = 0

        def return_unpaired_final_snapshot():
            nonlocal snapshot_calls
            snapshot_calls += 1
            snapshot = original_snapshot()
            if snapshot is None or snapshot_calls != 2:
                return snapshot
            state, state_bytes, state_identity, root_identity = snapshot
            return (
                {**state, "concurrent_unapproved_field": True},
                state_bytes,
                state_identity,
                root_identity,
            )

        with (
            mock.patch.object(
                manager,
                "_require_quiescent_rebind_state_locked",
                side_effect=return_unpaired_final_snapshot,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "poststate differs from approved plan"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assertEqual(
            (self.install_root / "install-state.json").read_bytes(), prestate
        )
        self.assertFalse(Path(plan["application_receipt_path"]).exists())

    def test_immutable_rebind_artifact_replacement_during_fsync_fails_closed(
        self,
    ) -> None:
        artifact_root = self.root / "immutable-artifacts"
        artifact_root.mkdir(mode=0o700)
        original_fsync = portable_install_module._fsync_directory
        foreign = b'{"foreign":true}\n'

        for role in ("rollback", "authorization", "application", "verification"):
            with self.subTest(role=role):
                artifact = artifact_root / f"{role}.json"
                replaced = False

                def replace_during_fsync(path):
                    nonlocal replaced
                    if (
                        directory_argument_matches(path, artifact_root)
                        and not replaced
                    ):
                        replaced = True
                        artifact.unlink()
                        artifact.write_bytes(foreign)
                    original_fsync(path)

                with (
                    mock.patch.object(
                        portable_install_module,
                        "_fsync_directory",
                        side_effect=replace_during_fsync,
                    ),
                    self.assertRaisesRegex(
                        PortableInstallError, "cannot create immutable artifact"
                    ),
                ):
                    portable_install_module._create_json(
                        artifact,
                        {"schema_version": 1, "role": role},
                    )

                self.assertTrue(replaced)
                self.assertEqual(artifact.read_bytes(), foreign)

    def test_immutable_rebind_artifact_replacement_on_fsync_error_is_preserved(
        self,
    ) -> None:
        artifact_root = self.root / "immutable-artifact-errors"
        artifact_root.mkdir(mode=0o700)
        foreign = b'{"foreign":true}\n'

        for role in ("rollback", "authorization", "application", "verification"):
            with self.subTest(role=role):
                artifact = artifact_root / f"{role}.json"
                replaced = False

                def replace_then_fail(path):
                    nonlocal replaced
                    if (
                        directory_argument_matches(path, artifact_root)
                        and not replaced
                    ):
                        replaced = True
                        artifact.unlink()
                        artifact.write_bytes(foreign)
                    raise OSError(errno.EIO, "simulated directory fsync failure")

                with (
                    mock.patch.object(
                        portable_install_module,
                        "_fsync_directory",
                        side_effect=replace_then_fail,
                    ),
                    self.assertRaisesRegex(
                        PortableInstallError, "cannot create immutable artifact"
                    ),
                ):
                    portable_install_module._create_json(
                        artifact,
                        {"schema_version": 1, "role": role},
                    )

                self.assertTrue(replaced)
                self.assertEqual(artifact.read_bytes(), foreign)

    def test_immutable_rebind_artifact_parent_swap_is_preserved_and_rejected(
        self,
    ) -> None:
        artifact_root = self.root / "immutable-parent-swap"
        artifact_root.mkdir(mode=0o700)
        prior = artifact_root / "prior.json"
        prior.write_bytes(b'{"prior":true}\n')
        prior.chmod(0o600)
        artifact = artifact_root / "new.json"
        displaced = self.root / "displaced-immutable-parent"
        original_fsync = portable_install_module._fsync_directory
        swapped = False

        def swap_parent_with_hardlink(path):
            nonlocal swapped
            if isinstance(path, int) and not swapped:
                swapped = True
                artifact_root.rename(displaced)
                artifact_root.mkdir(mode=0o700)
                os.link(displaced / artifact.name, artifact)
            original_fsync(path)

        with (
            mock.patch.object(
                portable_install_module,
                "_fsync_directory",
                side_effect=swap_parent_with_hardlink,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "cannot create immutable artifact"
            ),
        ):
            portable_install_module._create_json(
                artifact,
                {"schema_version": 1, "role": "authorization"},
            )

        self.assertTrue(swapped)
        self.assertEqual((displaced / "prior.json").read_bytes(), b'{"prior":true}\n')
        self.assertTrue((displaced / artifact.name).is_file())
        self.assertTrue(artifact.is_file())
        self.assertEqual(
            (displaced / artifact.name).stat().st_ino,
            artifact.stat().st_ino,
        )

    def test_immutable_rebind_artifact_mode_and_link_drift_fail_closed(
        self,
    ) -> None:
        artifact_root = self.root / "immutable-metadata-drift"
        artifact_root.mkdir(mode=0o700)
        original_fsync = portable_install_module._fsync_directory

        for drift in ("mode", "hardlink"):
            with self.subTest(drift=drift):
                artifact = artifact_root / f"{drift}.json"
                alias = artifact_root / f"{drift}.alias"
                changed = False

                def change_metadata(path):
                    nonlocal changed
                    if (
                        directory_argument_matches(path, artifact_root)
                        and not changed
                    ):
                        changed = True
                        if drift == "mode":
                            artifact.chmod(0o644)
                        else:
                            os.link(artifact, alias)
                    original_fsync(path)

                with (
                    mock.patch.object(
                        portable_install_module,
                        "_fsync_directory",
                        side_effect=change_metadata,
                    ),
                    self.assertRaisesRegex(
                        PortableInstallError, "cannot create immutable artifact"
                    ),
                ):
                    portable_install_module._create_json(
                        artifact,
                        {"schema_version": 1, "drift": drift},
                    )

                self.assertTrue(changed)
                self.assertFalse(artifact.exists())
                if drift == "hardlink":
                    self.assertTrue(alias.is_file())

    def test_data_identity_rebind_writer_failure_preserves_divergent_state(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        divergent = json.loads(prestate)
        divergent["npx_alias"] = "/concurrent/apply-writer"
        divergent_bytes = (
            json.dumps(divergent, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )

        def fail_before_exchange(_first: Path, _second: Path) -> None:
            state_path.write_bytes(divergent_bytes)
            raise OSError("simulated exchange failure")

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=fail_before_exchange,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "diverged before atomic exchange"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assertEqual(state_path.read_bytes(), divergent_bytes)
        self.assertFalse(Path(plan["application_receipt_path"]).exists())
        self.assertEqual(
            list(state_path.parent.glob(f".{state_path.name}.rebind.*")),
            [],
        )

    def test_data_identity_rebind_exchange_race_preserves_divergent_state(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        divergent = json.loads(prestate)
        divergent["npx_alias"] = "/concurrent/apply-exchange"
        divergent_bytes = (
            json.dumps(divergent, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        original_exchange = portable_install_module._atomic_exchange
        raced = False

        def exchange_after_divergence(first: Path, second: Path) -> None:
            nonlocal raced
            if not raced:
                raced = True
                state_path.write_bytes(divergent_bytes)
            original_exchange(first, second)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=exchange_after_divergence,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "installation state changed after planning"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assertEqual(state_path.read_bytes(), divergent_bytes)
        self.assertFalse(Path(plan["application_receipt_path"]).exists())
        self.assertEqual(
            list(state_path.parent.glob(f".{state_path.name}.rebind.*")),
            [],
        )

    def test_data_identity_rebind_install_root_swap_fails_uncertain(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        displaced_root = self.root / "displaced-install-root"
        original_exchange = portable_install_module._atomic_exchange
        swapped = False

        def swap_root_with_hardlinks(first, second) -> None:
            nonlocal swapped
            if not swapped:
                swapped = True
                self.install_root.rename(displaced_root)
                self.install_root.mkdir(mode=0o700)
                os.link(displaced_root / first.name, self.install_root / first.name)
                os.link(displaced_root / second.name, self.install_root / second.name)
            original_exchange(first, second)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=swap_root_with_hardlinks,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "exchange is uncertain; preserve"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assertTrue(swapped)
        displaced_stages = list(
            displaced_root.glob(f".{state_path.name}.rebind.*")
        )
        shadow_stages = list(
            self.install_root.glob(f".{state_path.name}.rebind.*")
        )
        self.assertEqual(len(displaced_stages), 1)
        self.assertEqual(len(shadow_stages), 1)
        self.assertEqual(state_path.read_bytes(), prestate)
        self.assertEqual(
            hashlib.sha256((displaced_root / state_path.name).read_bytes()).hexdigest(),
            plan["expected_post_state_digest"],
        )
        self.assertEqual(displaced_stages[0].read_bytes(), prestate)
        self.assertEqual(
            hashlib.sha256(shadow_stages[0].read_bytes()).hexdigest(),
            plan["expected_post_state_digest"],
        )
        self.assertFalse(Path(plan["application_receipt_path"]).exists())

    def test_data_identity_rebind_post_exchange_drift_preserves_both_entries(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        divergent = json.loads(prestate)
        divergent["npx_alias"] = "/concurrent/apply-post-exchange"
        divergent_bytes = (
            json.dumps(divergent, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        original_exchange = portable_install_module._atomic_exchange
        raced = False

        def drift_after_exchange(first: Path, second: Path) -> None:
            nonlocal raced
            original_exchange(first, second)
            if not raced:
                raced = True
                state_path.write_bytes(divergent_bytes)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=drift_after_exchange,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "exchange is uncertain; preserve"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        stages = list(state_path.parent.glob(f".{state_path.name}.rebind.*"))
        self.assertEqual(state_path.read_bytes(), divergent_bytes)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0].read_bytes(), prestate)
        self.assertFalse(Path(plan["application_receipt_path"]).exists())
        live_state = state_path.read_bytes()
        binding_generation = json.loads(live_state)["binding_generation_digest"]
        calls_before = list(self.runner.calls)
        for operation in (
            lambda: manager.data_identity_rebind_status(plan),
            lambda: manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1001,
            ),
            manager.verify,
            manager.stop_services,
            lambda: manager.upgrade(
                self.release("2.0.0"),
                version="2.0.0",
                expected_current_binding_generation_digest=binding_generation,
            ),
        ):
            with self.assertRaisesRegex(
                PortableInstallError, "unresolved installer-state exchange stage"
            ):
                operation()
        self.assertEqual(self.runner.calls, calls_before)
        self.assertEqual(state_path.read_bytes(), live_state)
        self.assertEqual(stages[0].read_bytes(), prestate)

    def test_data_identity_rebind_post_exchange_fsync_failure_is_uncertain(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        original_exchange = portable_install_module._atomic_exchange
        original_fsync = portable_install_module._fsync_directory
        exchanged = False

        def record_exchange(first: Path, second: Path) -> None:
            nonlocal exchanged
            original_exchange(first, second)
            exchanged = True

        def fail_after_exchange(path: Path) -> None:
            if exchanged:
                raise OSError("simulated post-exchange fsync failure")
            original_fsync(path)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=record_exchange,
            ),
            mock.patch.object(
                portable_install_module,
                "_fsync_directory",
                side_effect=fail_after_exchange,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "exchange is uncertain; preserve"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        stages = list(state_path.parent.glob(f".{state_path.name}.rebind.*"))
        self.assertEqual(
            hashlib.sha256(state_path.read_bytes()).hexdigest(),
            plan["expected_post_state_digest"],
        )
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0].read_bytes(), prestate)
        self.assertFalse(Path(plan["application_receipt_path"]).exists())

    def test_data_identity_rebind_exchange_back_fsync_preserves_stage(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        divergent = json.loads(prestate)
        divergent["npx_alias"] = "/concurrent/apply-exchange-back-fsync"
        divergent_bytes = (
            json.dumps(divergent, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        original_exchange = portable_install_module._atomic_exchange
        original_fsync = portable_install_module._fsync_directory
        exchange_count = 0

        def race_first_exchange(first: Path, second: Path) -> None:
            nonlocal exchange_count
            if exchange_count == 0:
                state_path.write_bytes(divergent_bytes)
            original_exchange(first, second)
            exchange_count += 1

        def fail_after_exchange_back(path: Path) -> None:
            if exchange_count == 2:
                raise OSError("simulated exchange-back fsync failure")
            original_fsync(path)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=race_first_exchange,
            ),
            mock.patch.object(
                portable_install_module,
                "_fsync_directory",
                side_effect=fail_after_exchange_back,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "exchange is uncertain; preserve"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        stages = list(state_path.parent.glob(f".{state_path.name}.rebind.*"))
        self.assertEqual(state_path.read_bytes(), divergent_bytes)
        self.assertEqual(len(stages), 1)
        self.assertEqual(
            hashlib.sha256(stages[0].read_bytes()).hexdigest(),
            plan["expected_post_state_digest"],
        )
        self.assertFalse(Path(plan["application_receipt_path"]).exists())
        for operation in (
            lambda: manager.data_identity_rebind_status(plan),
            lambda: manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1001,
            ),
        ):
            with self.assertRaisesRegex(
                PortableInstallError, "unresolved installer-state exchange stage"
            ):
                operation()

    def test_data_identity_rebind_exchange_back_failure_blocks_retry(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        divergent = json.loads(prestate)
        divergent["npx_alias"] = "/concurrent/apply-exchange-back-failure"
        divergent_bytes = (
            json.dumps(divergent, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        original_exchange = portable_install_module._atomic_exchange
        exchange_count = 0

        def fail_exchange_back(first: Path, second: Path) -> None:
            nonlocal exchange_count
            if exchange_count == 0:
                state_path.write_bytes(divergent_bytes)
                original_exchange(first, second)
                exchange_count += 1
                return
            raise OSError("simulated exchange-back failure")

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=fail_exchange_back,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "exchange is uncertain; preserve"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        stages = list(state_path.parent.glob(f".{state_path.name}.rebind.*"))
        self.assertEqual(
            hashlib.sha256(state_path.read_bytes()).hexdigest(),
            plan["expected_post_state_digest"],
        )
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0].read_bytes(), divergent_bytes)
        self.assertFalse(Path(plan["application_receipt_path"]).exists())
        for operation in (
            lambda: manager.data_identity_rebind_status(plan),
            lambda: manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1001,
            ),
        ):
            with self.assertRaisesRegex(
                PortableInstallError, "unresolved installer-state exchange stage"
            ):
                operation()

    def test_data_identity_rebind_cleanup_fsync_reports_committed_state(
        self,
    ) -> None:
        manager, evidence, plan, _prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        original_exchange = portable_install_module._atomic_exchange
        original_fsync = portable_install_module._fsync_directory
        exchanged = False
        post_exchange_fsyncs = 0

        def record_exchange(first: Path, second: Path) -> None:
            nonlocal exchanged
            original_exchange(first, second)
            exchanged = True

        def fail_cleanup_fsync(path: Path) -> None:
            nonlocal post_exchange_fsyncs
            if exchanged:
                post_exchange_fsyncs += 1
                if post_exchange_fsyncs == 2:
                    raise OSError("simulated committed cleanup fsync failure")
            original_fsync(path)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=record_exchange,
            ),
            mock.patch.object(
                portable_install_module,
                "_fsync_directory",
                side_effect=fail_cleanup_fsync,
            ),
        ):
            result = manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assertEqual(result["status"], "applied-cleanup-uncertain")
        self.assertEqual(
            hashlib.sha256(state_path.read_bytes()).hexdigest(),
            plan["expected_post_state_digest"],
        )
        self.assertTrue(Path(plan["application_receipt_path"]).is_file())
        self.assertEqual(
            list(state_path.parent.glob(f".{state_path.name}.rebind.*")),
            [],
        )

    def test_data_identity_rebind_stage_unlink_failure_preserves_pair(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        original_unlink = os.unlink

        def fail_stage_unlink(path, *args, **kwargs) -> None:
            if Path(path).name.startswith(f".{state_path.name}.rebind."):
                raise OSError("simulated stage unlink failure")
            original_unlink(path, *args, **kwargs)

        with (
            mock.patch.object(os, "unlink", new=fail_stage_unlink),
            self.assertRaisesRegex(
                PortableInstallError, "exchange is uncertain; preserve"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        stages = list(state_path.parent.glob(f".{state_path.name}.rebind.*"))
        self.assertEqual(
            hashlib.sha256(state_path.read_bytes()).hexdigest(),
            plan["expected_post_state_digest"],
        )
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0].read_bytes(), prestate)
        self.assertFalse(Path(plan["application_receipt_path"]).exists())
        with self.assertRaisesRegex(
            PortableInstallError, "unresolved installer-state exchange stage"
        ):
            manager.verify()

    def test_data_identity_rebind_exchange_back_unlink_preserves_stage(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        divergent = json.loads(prestate)
        divergent["npx_alias"] = "/concurrent/apply-exchange-back-unlink"
        divergent_bytes = (
            json.dumps(divergent, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        original_exchange = portable_install_module._atomic_exchange
        original_unlink = os.unlink
        raced = False

        def race_first_exchange(first: Path, second: Path) -> None:
            nonlocal raced
            if not raced:
                raced = True
                state_path.write_bytes(divergent_bytes)
            original_exchange(first, second)

        def fail_stage_unlink(path, *args, **kwargs) -> None:
            if Path(path).name.startswith(f".{state_path.name}.rebind."):
                raise OSError("simulated restored-stage unlink failure")
            original_unlink(path, *args, **kwargs)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=race_first_exchange,
            ),
            mock.patch.object(os, "unlink", new=fail_stage_unlink),
            self.assertRaisesRegex(
                PortableInstallError, "restored with cleanup pending; preserve"
            ),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        stages = list(state_path.parent.glob(f".{state_path.name}.rebind.*"))
        self.assertEqual(state_path.read_bytes(), divergent_bytes)
        self.assertEqual(len(stages), 1)
        self.assertEqual(
            hashlib.sha256(stages[0].read_bytes()).hexdigest(),
            plan["expected_post_state_digest"],
        )
        self.assertFalse(Path(plan["application_receipt_path"]).exists())

    def test_data_identity_rebind_rejects_foreign_authority(self) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        foreign_authority = {**plan, "consumer_id": "foreign"}
        foreign_authority["plan_digest"] = digest(
            {
                key: value
                for key, value in foreign_authority.items()
                if key != "plan_digest"
            }
        )
        with self.assertRaisesRegex(PortableInstallError, "authority differs"):
            manager.data_identity_rebind_apply(
                foreign_authority,
                approval_digest=foreign_authority["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )
        self.assert_rebind_unmutated(plan, prestate)

    def test_data_identity_rebind_rejects_diverged_prestate(self) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        diverged = json.loads(prestate)
        diverged["npx_alias"] = "/unexpected"
        (self.install_root / "install-state.json").write_text(
            json.dumps(diverged, sort_keys=True), encoding="utf-8"
        )
        self.assertEqual(
            manager.data_identity_rebind_status(plan)["status"], "diverged"
        )
        with self.assertRaisesRegex(PortableInstallError, "changed after planning"):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )
        (self.install_root / "install-state.json").write_bytes(prestate)
        self.assert_rebind_unmutated(plan, prestate)

    def test_data_identity_rebind_rejects_invalid_approval(self) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        with self.assertRaisesRegex(PortableInstallError, "approval digest is invalid"):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest="short",
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )
        with self.assertRaisesRegex(PortableInstallError, "approval digest differs"):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest="0" * 64,
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )
        self.assert_rebind_unmutated(plan, prestate)

    def applied_rebind(self):
        manager, evidence, plan, prestate, sentinel = self.prepared_rebind()
        applied = manager.data_identity_rebind_apply(
            plan,
            approval_digest=plan["plan_digest"],
            pre_apply_evidence_value=self.pre_apply_evidence(evidence),
            now=1000,
        )
        return manager, evidence, plan, prestate, sentinel, applied

    @staticmethod
    def pre_apply_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
        current = json.loads(json.dumps(evidence))
        current["database"]["observed_at"] = 1000
        return reseal_rebind_evidence(current)

    @staticmethod
    def post_rebind_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
        post_evidence = json.loads(json.dumps(evidence))
        post_evidence["postgres"]["postmaster_pid"] += 1
        post_evidence["database"]["observed_at"] = 1001
        return reseal_rebind_evidence(post_evidence)

    def test_data_identity_rebind_apply_changes_only_identity_and_preserves_data(
        self,
    ) -> None:
        _manager, _evidence, plan, prestate, sentinel, applied = self.applied_rebind()
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(sentinel.read_bytes(), b"database-unchanged")
        state = json.loads(
            (self.install_root / "install-state.json").read_text(encoding="utf-8")
        )
        prior = json.loads(prestate)
        self.assertEqual(
            {
                key: value
                for key, value in state.items()
                if key != "data_identity_digest"
            },
            {
                key: value
                for key, value in prior.items()
                if key != "data_identity_digest"
            },
        )
        self.assertEqual(
            state["data_identity_digest"], plan["new_data_identity_digest"]
        )
        self.assertTrue(Path(plan["rollback_bundle_path"]).is_file())
        self.assertTrue(Path(plan["authorization_receipt_path"]).is_file())
        self.assertTrue(Path(plan["application_receipt_path"]).is_file())

    def test_data_identity_rebind_receipt_records_first_apply_time(self) -> None:
        manager, evidence, plan, _prestate, _sentinel = self.prepared_rebind()
        manager.data_identity_rebind_apply(
            plan,
            approval_digest=plan["plan_digest"],
            pre_apply_evidence_value=self.pre_apply_evidence(evidence),
            now=1001,
        )
        receipt_path = Path(plan["authorization_receipt_path"])
        receipt = json.loads(receipt_path.read_bytes())
        self.assertEqual(receipt["authorized_at"], 1001)
        self.assertNotEqual(receipt["authorized_at"], plan["created_at"])

        self.assertEqual(
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1002,
            )["status"],
            "already-applied",
        )
        self.assertEqual(json.loads(receipt_path.read_bytes()), receipt)

    def test_data_identity_rebind_recovers_missing_application_receipt(
        self,
    ) -> None:
        manager, evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        application_path = Path(plan["application_receipt_path"])
        application_path.unlink()
        current = self.pre_apply_evidence(evidence)
        current["database"]["observed_at"] = 1001
        current = reseal_rebind_evidence(current)

        recovered = manager.data_identity_rebind_apply(
            plan,
            approval_digest=plan["plan_digest"],
            pre_apply_evidence_value=current,
            now=1001,
        )

        self.assertEqual(recovered["status"], "already-applied")
        self.assertTrue(application_path.is_file())
        self.assertEqual(
            json.loads(application_path.read_bytes())["applied_at"],
            1001,
        )

    def test_data_identity_rebind_resume_rejects_tampered_artifacts(self) -> None:
        manager, evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        authorization_path = Path(plan["authorization_receipt_path"])
        authorization_receipt = authorization_path.read_bytes()
        tampered_receipt = json.loads(authorization_receipt)
        tampered_receipt["plan_digest"] = "0" * 64
        authorization_path.write_text(
            json.dumps(tampered_receipt, sort_keys=True), encoding="utf-8"
        )
        with self.assertRaisesRegex(PortableInstallError, "receipt differs"):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )
        authorization_path.write_bytes(authorization_receipt)
        rollback_path = Path(plan["rollback_bundle_path"])
        rollback_bundle = rollback_path.read_bytes()
        tampered_bundle = json.loads(rollback_bundle)
        tampered_bundle["plan_digest"] = "0" * 64
        rollback_path.write_text(
            json.dumps(tampered_bundle, sort_keys=True), encoding="utf-8"
        )
        with self.assertRaisesRegex(PortableInstallError, "bundle is invalid"):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )
        rollback_path.write_bytes(rollback_bundle)
        self.assertEqual(
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )["status"],
            "already-applied",
        )

    def test_data_identity_rebind_rejects_symlinked_immutable_artifacts(
        self,
    ) -> None:
        manager, evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        authorization_path = Path(plan["authorization_receipt_path"])
        outside = self.root / "outside-authorization.json"
        outside.write_bytes(authorization_path.read_bytes())
        authorization_path.unlink()
        authorization_path.symlink_to(outside)

        with self.assertRaisesRegex(PortableInstallError, "must not be a symlink"):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        authorization_path.unlink()
        authorization_path.write_bytes(outside.read_bytes())
        rollback_path = Path(plan["rollback_bundle_path"])
        status = manager.data_identity_rebind_status(plan)
        outside_rollback = self.root / "outside-rollback.json"
        outside_rollback.write_bytes(rollback_path.read_bytes())
        rollback_path.unlink()
        rollback_path.symlink_to(outside_rollback)
        with self.assertRaisesRegex(PortableInstallError, "must not be a symlink"):
            manager.data_identity_rebind_rollback(
                plan,
                approval_digest=status["rollback_authorization_digest"],
            )

    def test_data_identity_rebind_verify_is_idempotent_and_reports_status(
        self,
    ) -> None:
        manager, evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        post_evidence = self.post_rebind_evidence(evidence)
        unsafe_evidence = json.loads(json.dumps(post_evidence))
        unsafe_evidence["safety"]["database_mutation_performed"] = True
        with self.assertRaisesRegex(PortableInstallError, "database mutation marker"):
            manager.data_identity_rebind_verify(
                plan,
                unsafe_evidence,
                now=1001,
            )
        verified = manager.data_identity_rebind_verify(
            plan,
            post_evidence,
            now=1001,
        )
        self.assertEqual(verified["status"], "verified")
        self.assertTrue(Path(plan["verification_receipt_path"]).is_file())
        verification_receipt = Path(plan["verification_receipt_path"]).read_bytes()
        repeated_verification = manager.data_identity_rebind_verify(
            plan,
            post_evidence,
            now=1002,
        )
        self.assertEqual(repeated_verification["status"], "verified")
        self.assertEqual(
            Path(plan["verification_receipt_path"]).read_bytes(),
            verification_receipt,
        )

        status = manager.data_identity_rebind_status(plan)
        self.assertEqual(status["status"], "applied")
        self.assertEqual(
            status["data_identity_digest"], plan["new_data_identity_digest"]
        )
        self.assertTrue(status["authorization_receipt_present"])
        self.assertTrue(status["rollback_bundle_present"])
        self.assertTrue(status["verification_receipt_present"])

    def test_data_identity_rebind_verify_rejects_expired_plan(self) -> None:
        manager, evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        with self.assertRaisesRegex(PortableInstallError, "plan is expired"):
            manager.data_identity_rebind_verify(
                plan,
                self.post_rebind_evidence(evidence),
                now=plan["expires_at"],
            )
        self.assertFalse(Path(plan["verification_receipt_path"]).exists())

    def test_data_identity_rebind_verify_rejects_preapplication_evidence(
        self,
    ) -> None:
        manager, evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        with self.assertRaisesRegex(PortableInstallError, "predates application"):
            manager.data_identity_rebind_verify(
                plan,
                self.pre_apply_evidence(evidence),
                now=1001,
            )
        self.assertFalse(Path(plan["verification_receipt_path"]).exists())

    def test_data_identity_rebind_verify_rechecks_expiry_after_lock_acquisition(
        self,
    ) -> None:
        manager, evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        with (
            self.expire_plan_at_rebind_mutation_boundary() as boundary,
            self.assertRaisesRegex(PortableInstallError, "plan is expired"),
        ):
            manager.data_identity_rebind_verify(
                plan,
                self.post_rebind_evidence(evidence),
                now=1001,
            )

        self.assertTrue(boundary["backup_checked"])
        self.assertFalse(Path(plan["verification_receipt_path"]).exists())

    def test_data_identity_rebind_verify_requires_apply_artifacts(self) -> None:
        manager, evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        post_evidence = self.post_rebind_evidence(evidence)
        authorization_path = Path(plan["authorization_receipt_path"])
        authorization = authorization_path.read_bytes()
        authorization_path.unlink()
        with self.assertRaisesRegex(
            PortableInstallError, "authorization receipt.*unavailable"
        ):
            manager.data_identity_rebind_verify(
                plan,
                post_evidence,
                now=1001,
            )

        authorization_path.write_bytes(authorization)
        rollback_path = Path(plan["rollback_bundle_path"])
        rollback = json.loads(rollback_path.read_bytes())
        rollback["plan_digest"] = "0" * 64
        rollback_path.write_text(json.dumps(rollback), encoding="utf-8")
        with self.assertRaisesRegex(PortableInstallError, "bundle is invalid"):
            manager.data_identity_rebind_verify(
                plan,
                post_evidence,
                now=1001,
            )

    def test_data_identity_rebind_verify_binds_post_evidence_to_data_root(
        self,
    ) -> None:
        manager, evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        post_evidence = self.post_rebind_evidence(evidence)
        other_root = self.root / "other-data-root"
        other_postgres = other_root / "data"
        other_postgres.mkdir(parents=True)
        root_metadata = other_root.lstat()
        postgres_metadata = other_postgres.lstat()
        post_evidence["postgres"].update(
            data_root=str(other_root),
            data_root_device=root_metadata.st_dev,
            data_root_inode=root_metadata.st_ino,
            postgres_data_root=str(other_postgres),
            postgres_data_device=postgres_metadata.st_dev,
            postgres_data_inode=postgres_metadata.st_ino,
        )
        post_evidence = reseal_rebind_evidence(post_evidence)

        with self.assertRaisesRegex(
            PortableInstallError, "does not describe the configured data root"
        ):
            manager.data_identity_rebind_verify(
                plan,
                post_evidence,
                now=1001,
            )

    def test_data_identity_rebind_rollback_restores_blocked_prestate(self) -> None:
        manager, _evidence, plan, prestate, sentinel, _applied = self.applied_rebind()
        status = manager.data_identity_rebind_status(plan)
        with self.assertRaisesRegex(PortableInstallError, "approval digest is invalid"):
            manager.data_identity_rebind_rollback(
                plan,
                approval_digest="short",
            )
        rolled_back = manager.data_identity_rebind_rollback(
            plan,
            approval_digest=status["rollback_authorization_digest"],
        )

        self.assertEqual(rolled_back["status"], "rolled-back")
        self.assertEqual(
            rolled_back["installation_health"],
            "blocked-pending-data-root-repair",
        )
        self.assertEqual(
            (self.install_root / "install-state.json").read_bytes(), prestate
        )
        self.assertEqual(sentinel.read_bytes(), b"database-unchanged")
        with self.assertRaisesRegex(PortableInstallError, "data identity changed"):
            manager.verify()

    def test_data_identity_rebind_receipt_failure_restores_prestate(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        state_path = self.install_root / "install-state.json"
        application_path = Path(plan["application_receipt_path"])
        original_create = manager._create_or_match_rebind_artifact

        def fail_application_receipt(path, value, *, label, mismatch_message):
            if path == application_path:
                raise PortableInstallError("simulated application receipt failure")
            return original_create(
                path,
                value,
                label=label,
                mismatch_message=mismatch_message,
            )

        with (
            mock.patch.object(
                manager,
                "_create_or_match_rebind_artifact",
                side_effect=fail_application_receipt,
            ),
            self.assertRaisesRegex(PortableInstallError, "receipt failure"),
        ):
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1000,
            )

        self.assertEqual(state_path.read_bytes(), prestate)
        self.assertTrue(Path(plan["rollback_bundle_path"]).is_file())
        self.assertTrue(Path(plan["authorization_receipt_path"]).is_file())
        self.assertEqual(
            manager.data_identity_rebind_apply(
                plan,
                approval_digest=plan["plan_digest"],
                pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                now=1001,
            )["status"],
            "applied",
        )

    def test_data_identity_rebind_rollback_exchange_race_preserves_divergence(
        self,
    ) -> None:
        manager, _evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        state_path = self.install_root / "install-state.json"
        status = manager.data_identity_rebind_status(plan)
        divergent = json.loads(state_path.read_bytes())
        divergent["npx_alias"] = "/concurrent/rollback-exchange"
        divergent_bytes = (
            json.dumps(divergent, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        original_exchange = portable_install_module._atomic_exchange
        raced = False

        def exchange_after_divergence(first: Path, second: Path) -> None:
            nonlocal raced
            if not raced:
                raced = True
                state_path.write_bytes(divergent_bytes)
            original_exchange(first, second)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=exchange_after_divergence,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "data-identity rollback prestate differs"
            ),
        ):
            manager.data_identity_rebind_rollback(
                plan,
                approval_digest=status["rollback_authorization_digest"],
            )

        self.assertEqual(state_path.read_bytes(), divergent_bytes)
        self.assertEqual(
            list(state_path.parent.glob(f".{state_path.name}.rebind.*")),
            [],
        )

    def test_data_identity_rebind_rollback_writer_failure_preserves_divergence(
        self,
    ) -> None:
        manager, _evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        state_path = self.install_root / "install-state.json"
        status = manager.data_identity_rebind_status(plan)
        divergent = json.loads(state_path.read_bytes())
        divergent["npx_alias"] = "/concurrent/rollback-writer"
        divergent_bytes = (
            json.dumps(divergent, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        def fail_before_exchange(_first: Path, _second: Path) -> None:
            state_path.write_bytes(divergent_bytes)
            raise OSError("simulated rollback exchange failure")

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=fail_before_exchange,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "diverged before atomic exchange"
            ),
        ):
            manager.data_identity_rebind_rollback(
                plan,
                approval_digest=status["rollback_authorization_digest"],
            )

        self.assertEqual(state_path.read_bytes(), divergent_bytes)
        self.assertEqual(
            list(state_path.parent.glob(f".{state_path.name}.rebind.*")),
            [],
        )

    def test_data_identity_rebind_rollback_post_exchange_fsync_is_uncertain(
        self,
    ) -> None:
        manager, _evidence, plan, prestate, _sentinel, _applied = self.applied_rebind()
        state_path = self.install_root / "install-state.json"
        poststate = state_path.read_bytes()
        status = manager.data_identity_rebind_status(plan)
        original_exchange = portable_install_module._atomic_exchange
        original_fsync = portable_install_module._fsync_directory
        exchanged = False

        def record_exchange(first: Path, second: Path) -> None:
            nonlocal exchanged
            original_exchange(first, second)
            exchanged = True

        def fail_after_exchange(path: Path) -> None:
            if exchanged:
                raise OSError("simulated rollback fsync failure")
            original_fsync(path)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=record_exchange,
            ),
            mock.patch.object(
                portable_install_module,
                "_fsync_directory",
                side_effect=fail_after_exchange,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "exchange is uncertain; preserve"
            ),
        ):
            manager.data_identity_rebind_rollback(
                plan,
                approval_digest=status["rollback_authorization_digest"],
            )

        stages = list(state_path.parent.glob(f".{state_path.name}.rebind.*"))
        self.assertEqual(state_path.read_bytes(), prestate)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0].read_bytes(), poststate)

    def test_data_identity_rebind_rollback_exchange_back_fsync_preserves_stage(
        self,
    ) -> None:
        manager, _evidence, plan, _prestate, _sentinel, _applied = self.applied_rebind()
        state_path = self.install_root / "install-state.json"
        poststate = state_path.read_bytes()
        status = manager.data_identity_rebind_status(plan)
        divergent = json.loads(poststate)
        divergent["npx_alias"] = "/concurrent/rollback-exchange-back-fsync"
        divergent_bytes = (
            json.dumps(divergent, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        original_exchange = portable_install_module._atomic_exchange
        original_fsync = portable_install_module._fsync_directory
        exchange_count = 0

        def race_first_exchange(first: Path, second: Path) -> None:
            nonlocal exchange_count
            if exchange_count == 0:
                state_path.write_bytes(divergent_bytes)
            original_exchange(first, second)
            exchange_count += 1

        def fail_after_exchange_back(path: Path) -> None:
            if exchange_count == 2:
                raise OSError("simulated rollback exchange-back fsync failure")
            original_fsync(path)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=race_first_exchange,
            ),
            mock.patch.object(
                portable_install_module,
                "_fsync_directory",
                side_effect=fail_after_exchange_back,
            ),
            self.assertRaisesRegex(
                PortableInstallError, "exchange is uncertain; preserve"
            ),
        ):
            manager.data_identity_rebind_rollback(
                plan,
                approval_digest=status["rollback_authorization_digest"],
            )

        stages = list(state_path.parent.glob(f".{state_path.name}.rebind.*"))
        self.assertEqual(state_path.read_bytes(), divergent_bytes)
        self.assertEqual(len(stages), 1)
        self.assertEqual(
            hashlib.sha256(stages[0].read_bytes()).hexdigest(),
            plan["installation_state_digest"],
        )

    def test_data_identity_rebind_rollback_cleanup_fsync_reports_commit(
        self,
    ) -> None:
        manager, _evidence, plan, prestate, _sentinel, _applied = self.applied_rebind()
        state_path = self.install_root / "install-state.json"
        status = manager.data_identity_rebind_status(plan)
        original_exchange = portable_install_module._atomic_exchange
        original_fsync = portable_install_module._fsync_directory
        exchanged = False
        post_exchange_fsyncs = 0

        def record_exchange(first: Path, second: Path) -> None:
            nonlocal exchanged
            original_exchange(first, second)
            exchanged = True

        def fail_cleanup_fsync(path: Path) -> None:
            nonlocal post_exchange_fsyncs
            if exchanged:
                post_exchange_fsyncs += 1
                if post_exchange_fsyncs == 2:
                    raise OSError("simulated rollback cleanup fsync failure")
            original_fsync(path)

        with (
            mock.patch.object(
                portable_install_module,
                "_atomic_exchange",
                side_effect=record_exchange,
            ),
            mock.patch.object(
                portable_install_module,
                "_fsync_directory",
                side_effect=fail_cleanup_fsync,
            ),
        ):
            result = manager.data_identity_rebind_rollback(
                plan,
                approval_digest=status["rollback_authorization_digest"],
            )

        self.assertEqual(result["status"], "rolled-back-cleanup-uncertain")
        self.assertEqual(state_path.read_bytes(), prestate)
        self.assertEqual(
            list(state_path.parent.glob(f".{state_path.name}.rebind.*")),
            [],
        )

    def test_data_identity_rebind_rollback_unlink_failure_preserves_pair(
        self,
    ) -> None:
        manager, _evidence, plan, prestate, _sentinel, _applied = self.applied_rebind()
        state_path = self.install_root / "install-state.json"
        poststate = state_path.read_bytes()
        status = manager.data_identity_rebind_status(plan)
        original_unlink = os.unlink

        def fail_stage_unlink(path, *args, **kwargs) -> None:
            if Path(path).name.startswith(f".{state_path.name}.rebind."):
                raise OSError("simulated rollback stage unlink failure")
            original_unlink(path, *args, **kwargs)

        with (
            mock.patch.object(os, "unlink", new=fail_stage_unlink),
            self.assertRaisesRegex(
                PortableInstallError, "exchange is uncertain; preserve"
            ),
        ):
            manager.data_identity_rebind_rollback(
                plan,
                approval_digest=status["rollback_authorization_digest"],
            )

        stages = list(state_path.parent.glob(f".{state_path.name}.rebind.*"))
        self.assertEqual(state_path.read_bytes(), prestate)
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0].read_bytes(), poststate)
        with self.assertRaisesRegex(
            PortableInstallError, "unresolved installer-state exchange stage"
        ):
            manager.verify()

    def test_data_identity_followups_refuse_pending_recovery_without_mutation(
        self,
    ) -> None:
        manager, evidence, plan, prestate, _sentinel = self.prepared_rebind()
        pending = b'{"operator":"must-recover-explicitly"}\n'
        manager._transaction_path.write_bytes(pending)
        calls = (
            (
                "apply",
                lambda: manager.data_identity_rebind_apply(
                    plan,
                    approval_digest=plan["plan_digest"],
                    pre_apply_evidence_value=self.pre_apply_evidence(evidence),
                    now=1000,
                ),
            ),
            ("status", lambda: manager.data_identity_rebind_status(plan)),
            (
                "verify",
                lambda: manager.data_identity_rebind_verify(
                    plan,
                    self.post_rebind_evidence(evidence),
                    now=1001,
                ),
            ),
            (
                "rollback",
                lambda: manager.data_identity_rebind_rollback(
                    plan,
                    approval_digest=manager._rebind_rollback_approval(plan),
                ),
            ),
        )
        for name, call in calls:
            with (
                self.subTest(command=name),
                self.assertRaisesRegex(
                    PortableInstallError,
                    "requires quiescent installer state",
                ),
            ):
                call()
            self.assertEqual(manager._transaction_path.read_bytes(), pending)
            self.assertEqual(
                (self.install_root / "install-state.json").read_bytes(),
                prestate,
            )

    def test_data_identity_followup_commands_gate_lifecycle_and_config(self) -> None:
        manager = self.rebind_manager()
        calls = (
            ("plan", lambda: manager.data_identity_rebind_plan({})),
            (
                "apply",
                lambda: manager.data_identity_rebind_apply(
                    {},
                    approval_digest="0" * 64,
                    pre_apply_evidence_value={},
                ),
            ),
            ("status", lambda: manager.data_identity_rebind_status({})),
            ("verify", lambda: manager.data_identity_rebind_verify({}, {})),
            (
                "rollback",
                lambda: manager.data_identity_rebind_rollback(
                    {}, approval_digest="0" * 64
                ),
            ),
        )
        for name, call in calls:
            preflight = mock.Mock(side_effect=PortableInstallError("preflight blocked"))
            with (
                self.subTest(gate="preflight", command=name),
                mock.patch.object(
                    manager,
                    "_preflight_lifecycle",
                    preflight,
                ),
                self.assertRaisesRegex(PortableInstallError, "preflight blocked"),
            ):
                call()
            preflight.assert_called_once_with()

        changed = json.loads(self.config_path.read_text(encoding="utf-8"))
        changed["consumer_id"] = "changed"
        self.config_path.write_text(
            json.dumps(changed, sort_keys=True), encoding="utf-8"
        )
        for name, call in calls:
            with (
                self.subTest(gate="config", command=name),
                mock.patch.object(
                    manager, "_lock", side_effect=AssertionError("lock entered")
                ),
                self.assertRaisesRegex(PortableInstallError, "config changed"),
            ):
                call()

    def test_data_identity_verify_cli_persists_create_only_result(self) -> None:
        module = runpy.run_path(str(ROOT / "bin" / "hindsight-memory"))
        output = self.root / "verification.json"
        args = module["parser"]().parse_args(
            [
                "data-identity",
                "verify",
                "--config",
                str(self.config_path),
                "--plan",
                str(self.root / "plan.json"),
                "--post-evidence",
                str(self.root / "post-evidence.json"),
                "--output",
                str(output),
            ]
        )
        result = {"status": "verified", "plan_digest": "a" * 64}
        manager = mock.Mock()
        manager.data_identity_rebind_verify.return_value = result
        write_private = mock.Mock()
        function_globals = module["data_identity_rebind_verify_command"].__globals__
        with mock.patch.dict(
            function_globals,
            {
                "_portable_manager": lambda _args: manager,
                "read_json": lambda path: {"path": str(path)},
                "write_private": write_private,
                "_print_result": lambda value: 0 if value == result else 1,
            },
        ):
            self.assertEqual(
                module["data_identity_rebind_verify_command"](args),
                0,
            )
        write_private.assert_called_once_with(
            str(output),
            result,
            create_only=True,
        )
        manager.data_identity_rebind_verify.assert_called_once_with(
            {"path": str(self.root / "plan.json")},
            {"path": str(self.root / "post-evidence.json")},
        )

    def test_data_identity_observe_cli_refreshes_without_renewing_window(
        self,
    ) -> None:
        self.data_root.mkdir(mode=0o700)
        module = runpy.run_path(str(ROOT / "bin" / "hindsight-memory"))
        output = self.root / "observation.json"
        base_path = self.root / "base-evidence.json"
        args = module["parser"]().parse_args(
            [
                "data-identity",
                "observe",
                "--config",
                str(self.config_path),
                "--base-evidence",
                str(base_path),
                "--output",
                str(output),
            ]
        )
        base = {
            "profile_id": "systalyze",
            "expires_at": 1200,
            "postgres": {"system_identifier": "7659746962107358086"},
            "safety": {"controller_authority_disabled": True},
        }
        postgres = {"data_root": str(self.data_root)}
        database = {"observed_at": 1000}
        refreshed = {**base, "postgres": postgres, "database": database}
        manager = mock.Mock()
        manager.config.data_root = self.data_root

        async def observe_live(**_keywords):
            return {}, postgres, database

        write_private = mock.Mock()
        verification_times = []
        refresh_times = []

        def verify(value, *, now):
            verification_times.append(now)
            return value

        def refresh(value, **keywords):
            refresh_times.append(keywords["now"])
            return refreshed

        function_globals = module[
            "data_identity_rebind_observe_command"
        ].__globals__
        with mock.patch.dict(
            function_globals,
            {
                "_portable_manager": lambda _args: manager,
                "read_json": lambda path: base if Path(path) == base_path else {},
                "verify_rebind_evidence": verify,
                "_read_live_data_identity_evidence": observe_live,
                "_data_identity_safety_evidence": lambda value: base["safety"],
                "refresh_rebind_evidence": refresh,
                "write_private": write_private,
                "_print_result": lambda value: 0,
                "time": mock.Mock(time=mock.Mock(side_effect=(1000, 1005))),
            },
        ):
            self.assertEqual(
                module["data_identity_rebind_observe_command"](args),
                0,
            )

        manager._preflight_lifecycle.assert_called_once_with()
        manager._validate_config_source.assert_called_once_with()
        self.assertEqual(verification_times, [1000])
        self.assertEqual(refresh_times, [1005])
        write_private.assert_called_once_with(
            str(output),
            refreshed,
            create_only=True,
        )

    def test_data_identity_evidence_cli_binds_backup_and_evidence_outputs(
        self,
    ) -> None:
        module = runpy.run_path(str(ROOT / "bin" / "hindsight-memory"))
        output = self.root / "evidence.json"
        backup_attestation = self.root / "backup.json"
        args = module["parser"]().parse_args(
            [
                "data-identity",
                "evidence",
                "--config",
                str(self.config_path),
                "--candidate-release-root",
                str(self.root),
                "--candidate-release-identity",
                str(self.root / "identity.json"),
                "--artifact",
                str(self.root / "backup.age"),
                "--backup-attestation",
                str(backup_attestation),
                "--output",
                str(output),
                "--age",
                str(self.root / "age"),
                "--age-identity",
                str(self.root / "key.txt"),
                "--recipient",
                "age1example",
                "--postgres-bin-dir",
                str(self.root / "postgres"),
                "--run-root",
                "/private/tmp/hindsight-operation-recovery-"
                + "1" * 32,
                "--restore-run-root",
                "/private/tmp/hindsight-operation-recovery-"
                + "2" * 32,
                "--port",
                "55432",
            ]
        )
        backup_live = mock.Mock(return_value=0)
        function_globals = module[
            "data_identity_rebind_evidence_command"
        ].__globals__
        with mock.patch.dict(
            function_globals,
            {
                "operation_recovery_backup_live_command": backup_live,
                "time": mock.Mock(time=lambda: 1000),
            },
        ):
            self.assertEqual(
                module["data_identity_rebind_evidence_command"](args),
                0,
            )

        backup_live.assert_called_once_with(args)
        self.assertEqual(args.data_identity_output, str(output))
        self.assertEqual(args.output, str(backup_attestation))
        self.assertEqual(args.data_identity_collected_at, 1000)

    def test_data_identity_evidence_cli_rejects_artifact_path_aliases(
        self,
    ) -> None:
        module = runpy.run_path(str(ROOT / "bin" / "hindsight-memory"))
        shared = self.root / "shared.json"
        args = module["parser"]().parse_args(
            [
                "data-identity",
                "evidence",
                "--config",
                str(self.config_path),
                "--candidate-release-root",
                str(self.root),
                "--candidate-release-identity",
                str(self.root / "identity.json"),
                "--artifact",
                str(self.root / "backup.age"),
                "--backup-attestation",
                str(shared),
                "--output",
                str(shared),
                "--age",
                str(self.root / "age"),
                "--age-identity",
                str(self.root / "key.txt"),
                "--recipient",
                "age1example",
                "--postgres-bin-dir",
                str(self.root / "postgres"),
                "--run-root",
                "/private/tmp/hindsight-operation-recovery-" + "1" * 32,
                "--restore-run-root",
                "/private/tmp/hindsight-operation-recovery-" + "2" * 32,
                "--port",
                "55432",
            ]
        )

        with self.assertRaisesRegex(
            PortableInstallError,
            "path aliases an artifact",
        ):
            module["data_identity_rebind_evidence_command"](args)

    def test_data_identity_safety_evidence_requires_all_authorities_absent(
        self,
    ) -> None:
        module = runpy.run_path(str(ROOT / "bin" / "hindsight-memory"))
        classify = module["_data_identity_safety_evidence"]
        globals_ = classify.__globals__
        absent = subprocess.CompletedProcess([], 113)
        with mock.patch.dict(
            globals_,
            {
                "subprocess": mock.Mock(
                    run=mock.Mock(side_effect=(absent, absent, absent)),
                    DEVNULL=subprocess.DEVNULL,
                    SubprocessError=subprocess.SubprocessError,
                )
            },
        ):
            self.assertEqual(
                classify({"generic_import_receipt_count": 0}),
                {
                    "hooks_disabled": True,
                    "controller_authority_disabled": True,
                    "no_serena_import_authority": True,
                    "target_bank_inspected": False,
                    "database_mutation_performed": False,
                },
            )

        for returncode, message in (
            (0, "is active"),
            (3, "is unavailable"),
        ):
            with (
                self.subTest(returncode=returncode),
                mock.patch.dict(
                    globals_,
                    {
                        "subprocess": mock.Mock(
                            run=mock.Mock(
                                return_value=subprocess.CompletedProcess(
                                    [], returncode
                                )
                            ),
                            DEVNULL=subprocess.DEVNULL,
                            SubprocessError=subprocess.SubprocessError,
                        )
                    },
                ),
                self.assertRaisesRegex(
                    module["DataIdentityRebindError"],
                    message,
                ),
            ):
                classify({"generic_import_receipt_count": 0})


if __name__ == "__main__":
    unittest.main()
