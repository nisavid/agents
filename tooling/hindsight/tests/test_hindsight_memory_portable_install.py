from __future__ import annotations

import base64
import ctypes
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
import unittest
from contextlib import redirect_stderr
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
from hindsight_memory_control_plane.inventory import load_inventory  # noqa: E402


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        self.assertIn("--service", plist["ProgramArguments"])
        self.assertTrue(
            any(call[0].endswith("launchctl") for call in self.runner.calls)
        )

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

    def test_install_persists_the_validated_executable_targets(self) -> None:
        aliases = self.inventory.parent.resolve() / "executables"
        aliases.mkdir()
        uvx = aliases / "uvx"
        zsh = aliases / "zsh"
        uvx.symlink_to("/usr/bin/true")
        zsh.symlink_to(ZSH_EXECUTABLE)
        data = self.config_data()
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
        self.assertEqual(installed["uvx_executable"], str(uvx.resolve()))
        self.assertEqual(installed["zsh_executable"], str(zsh.resolve()))

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
            'pathlib.Path(sys.argv[1]).write_text(json.dumps({"secret": os.environ.get("HINDSIGHT_API_KEY"), "ambient": os.environ.get("UNRELATED_AMBIENT"), "release_path": os.environ.get("CAPTURE_RELEASE"), "inventory": os.environ.get("HINDSIGHT_MEMORY_INVENTORY"), "uvx": os.environ.get("HINDSIGHT_EMBED_UVX_EXECUTABLE"), "isolated": sys.flags.isolated}))\n',
            encoding="utf-8",
        )
        child.chmod(0o755)
        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            'print(json.dumps({"schema_version": 1, "values": {"HINDSIGHT_API_KEY": "test-canary-secret"}}))\n',
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)
        data = self.config_data()
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
        self.resolver.chmod(0o700)
        self.resolver.write_text(
            "#!/bin/sh\nprintf '%s\\n' 'source resolver was replaced' >&2\nexit 91\n",
            encoding="utf-8",
        )
        self.resolver.chmod(0o500)

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
        self.assertEqual(captured["isolated"], 1)
        for path in self.service_root.glob("*"):
            self.assertNotIn("test-canary-secret", path.read_text())

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
                self.install_root / "credential-resolver",
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


if __name__ == "__main__":
    unittest.main()
