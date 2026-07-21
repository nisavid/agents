from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hindsight_memory_control_plane.portable_install import (  # noqa: E402
    PortableInstallationManager,
    _systemd_user_service_root,
)

CLI = ROOT / "bin" / "hindsight-memory"
RUN_PLATFORM_ACCEPTANCE = (
    os.environ.get("HINDSIGHT_PORTABLE_PLATFORM_ACCEPTANCE") == "1"
)
SELECTED_PLATFORM = os.environ.get("HINDSIGHT_PORTABLE_ACCEPTANCE_PLATFORM", "")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _managed_python() -> Path:
    override = os.environ.get("HINDSIGHT_PORTABLE_ACCEPTANCE_MANAGED_PYTHON")
    if override:
        return Path(override).resolve(strict=True)
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("native acceptance requires uv and a managed Python")
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
        raise RuntimeError("native acceptance requires a managed uv Python >=3.11")
    return Path(completed.stdout.strip()).resolve(strict=True)


def _acceptance_subprocess_environment(platform_name: str) -> dict[str, str]:
    environment = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if platform_name != "systemd-user":
        return environment
    runtime_raw = os.environ.get("XDG_RUNTIME_DIR")
    bus_raw = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not runtime_raw or not bus_raw:
        return environment
    if any(character in runtime_raw + bus_raw for character in "\x00\r\n"):
        return environment
    runtime = Path(runtime_raw)
    if not runtime.is_absolute() or runtime.is_symlink():
        return environment
    try:
        metadata = runtime.lstat()
        resolved_runtime = runtime.resolve(strict=True)
    except OSError:
        return environment
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
        or not bus_raw.startswith("unix:path=")
    ):
        return environment
    bus = Path(bus_raw.removeprefix("unix:path="))
    try:
        bus_parent = bus.parent.resolve(strict=True)
    except OSError:
        return environment
    if not bus.is_absolute() or bus.name != "bus" or bus_parent != resolved_runtime:
        return environment
    environment["XDG_RUNTIME_DIR"] = runtime_raw
    environment["DBUS_SESSION_BUS_ADDRESS"] = bus_raw
    return environment


class PortablePlatformEnvironmentTest(unittest.TestCase):
    def test_native_acceptance_fails_when_managed_python_is_unavailable(self) -> None:
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(shutil, "which", return_value=None),
            self.assertRaisesRegex(RuntimeError, "requires uv"),
        ):
            _managed_python()

    def test_systemd_cli_environment_preserves_only_a_bound_user_bus(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            runtime.mkdir(mode=0o700)
            bus = f"unix:path={runtime}/bus"
            with mock.patch.dict(
                os.environ,
                {
                    "XDG_RUNTIME_DIR": str(runtime),
                    "DBUS_SESSION_BUS_ADDRESS": bus,
                    "UNRELATED": "excluded",
                },
                clear=True,
            ):
                environment = _acceptance_subprocess_environment("systemd-user")

        self.assertEqual(environment["XDG_RUNTIME_DIR"], str(runtime))
        self.assertEqual(environment["DBUS_SESSION_BUS_ADDRESS"], bus)
        self.assertNotIn("UNRELATED", environment)

    def test_cleanup_continues_after_subprocess_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "acceptance"
            root.mkdir()
            config = root / "installation.json"
            config.write_text("{}", encoding="utf-8")
            acceptance = object.__new__(PortablePlatformAcceptanceTest)
            acceptance.root = root
            acceptance.install_root = root / "install"
            acceptance.installed = True
            acceptance.config_path = config
            acceptance.platform = "launchd"
            acceptance.label = "io.nisavid.hindsight.acceptance.service"
            acceptance.timer_label = "io.nisavid.hindsight.acceptance.timer"
            with mock.patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(("probe",), 1),
            ) as run:
                acceptance._cleanup()

            self.assertEqual(run.call_count, 3)
            self.assertFalse(root.exists())


@unittest.skipUnless(
    RUN_PLATFORM_ACCEPTANCE,
    "set HINDSIGHT_PORTABLE_PLATFORM_ACCEPTANCE=1 to run service-manager acceptance",
)
class PortablePlatformAcceptanceTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        host = platform.system()
        detected = {"Darwin": "launchd", "Linux": "systemd-user"}.get(host)
        if detected is None:
            self.skipTest(f"unsupported acceptance host: {host}")
        self.platform = SELECTED_PLATFORM or detected
        if self.platform != detected:
            self.skipTest(f"selected {self.platform} acceptance cannot run on {host}")

        parent = os.environ.get("HINDSIGHT_PORTABLE_ACCEPTANCE_TMPDIR")
        self.root = Path(
            tempfile.mkdtemp(
                prefix="hindsight-portable-platform-",
                dir=parent,
            )
        ).resolve()
        self.label = f"io.nisavid.hindsight.acceptance.{uuid.uuid4().hex}"
        self.install_root = self.root / "install"
        self.state_root = self.root / "state"
        self.data_root = self.root / "data"
        self.consumer_root = self.root / "consumer"
        self.marker = self.state_root / "service.json"
        self.timer_marker = self.state_root / "timer.json"
        self.config_path = self.consumer_root / "installation.json"
        self.inventory_path = self.consumer_root / "inventory.json"
        self.timer_label = f"{self.label}.timer"
        self.timer_not_before_ns = time.time_ns()
        systemd_timer_at = (
            datetime.now().astimezone() + timedelta(minutes=4)
        ).strftime("%H:%M")
        managed_python = _managed_python()
        zsh_executable = shutil.which("zsh")
        if zsh_executable is None:
            self.skipTest("Zsh is required by the portable runtime")
        self.zsh_executable = Path(zsh_executable).resolve()
        if self.platform == "launchd":
            self.service_root = self.root / "LaunchAgents"
            self.manifest = self.service_root / f"{self.label}.plist"
            self.timer_manifests = (self.service_root / f"{self.timer_label}.plist",)
        else:
            self.service_root = _systemd_user_service_root(
                PortableInstallationManager._default_command_runner
            )
            self.manifest = self.service_root / f"{self.label}.service"
            self.timer_manifests = (
                self.service_root / f"{self.timer_label}.service",
                self.service_root / f"{self.timer_label}.timer",
            )
        self.installed = False
        self.started_pids: set[int] = set()

        self.consumer_root.mkdir(mode=0o700)
        self.managed_python = managed_python
        self.inventory_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "inventory_id": "portable-platform-acceptance",
                    "canonical_bank": "engineering",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        resolver = self.consumer_root / "resolve-credential"
        resolver.write_text(
            "#!/bin/sh\nprintf '%s\\n' '{\"schema_version\":1,\"values\":{}}'\n",
            encoding="utf-8",
        )
        resolver.chmod(0o500)
        config = {
            "schema_version": 1,
            "consumer_id": f"acceptance-{uuid.uuid4().hex}",
            "platform": self.platform,
            "installation_mode": "fresh",
            "install_root": str(self.install_root),
            "state_root": str(self.state_root),
            "data_root": str(self.data_root),
            "service_root": str(self.service_root),
            "inventory_path": str(self.inventory_path),
            "python_executable": str(self.managed_python),
            "uvx_executable": "/usr/bin/true",
            "zsh_executable": str(self.zsh_executable),
            "credential_resolver": {
                "path": str(resolver),
                "sha256": _sha256(resolver),
            },
            "services": [
                {
                    "service_id": "acceptance-probe",
                    "label": self.label,
                    "entrypoint": "bin/acceptance-probe",
                    "arguments": ["serve", str(self.marker)],
                    "environment": {"PATH": "/usr/bin:/bin"},
                    "credentials": [],
                    "restart": "on-failure",
                }
            ],
            "timers": [
                {
                    "timer_id": "acceptance-timer",
                    "label": self.timer_label,
                    "entrypoint": "bin/acceptance-probe",
                    "arguments": ["tick", str(self.timer_marker)],
                    "environment": {"PATH": "/usr/bin:/bin"},
                    "credentials": [],
                    "daily_at": (
                        systemd_timer_at if self.platform == "systemd-user" else "03:15"
                    ),
                }
            ],
            "health_checks": [
                {
                    "check_id": "acceptance-probe",
                    "entrypoint": "bin/acceptance-probe",
                    "arguments": ["health", str(self.marker)],
                    "environment": {"PATH": "/usr/bin:/bin"},
                    "credentials": [],
                    "timeout_seconds": 10,
                }
            ],
        }
        self.config_path.write_text(
            json.dumps(config, sort_keys=True), encoding="utf-8"
        )
        self.addCleanup(self._cleanup)

    def _release(self, version: str) -> Path:
        release = self.root / f"release-{version}"
        executable = release / "bin" / "acceptance-probe"
        executable.parent.mkdir(parents=True)
        shutil.copy2(CLI, release / "bin" / "hindsight-memory")
        shutil.copytree(ROOT / "lib", release / "lib")
        portable_runtime = (
            release / "lib" / "hindsight_memory_control_plane" / "portable_install.py"
        )
        runtime_source = portable_runtime.read_text(encoding="utf-8")
        marker = f"# candidate-installer-runtime:{version}"
        runtime_source = runtime_source.replace(
            'SERVICE_LAUNCHER = r"""#!/usr/bin/env python3',
            f'SERVICE_LAUNCHER = r"""#!/usr/bin/env python3\n{marker}',
            1,
        )
        if marker not in runtime_source:
            raise AssertionError("candidate installer marker was not injected")
        portable_runtime.write_text(runtime_source, encoding="utf-8")
        executable.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, pathlib, signal, sys, time\n"
            f"VERSION = {version!r}\n"
            "operation, marker_raw = sys.argv[1:]\n"
            "marker = pathlib.Path(marker_raw)\n"
            "def read_marker():\n"
            "    try:\n"
            "        return json.loads(marker.read_text(encoding='utf-8'))\n"
            "    except (FileNotFoundError, json.JSONDecodeError, OSError):\n"
            "        return {}\n"
            "if operation == 'health':\n"
            "    deadline = time.monotonic() + 8\n"
            "    while time.monotonic() < deadline:\n"
            "        state = read_marker()\n"
            "        try:\n"
            "            pid = int(state.get('pid', 0))\n"
            "            os.kill(pid, 0)\n"
            "        except (ValueError, TypeError, OSError):\n"
            "            time.sleep(0.05)\n"
            "            continue\n"
            "        if state.get('version') == VERSION:\n"
            "            raise SystemExit(0)\n"
            "        time.sleep(0.05)\n"
            "    raise SystemExit(1)\n"
            "if operation == 'tick':\n"
            "    marker.parent.mkdir(parents=True, exist_ok=True)\n"
            "    temporary = marker.with_name(marker.name + f'.{os.getpid()}.tmp')\n"
            "    temporary.write_text(json.dumps({'pid': os.getpid(), 'version': VERSION, 'fired_at_ns': time.time_ns()}), encoding='utf-8')\n"
            "    os.replace(temporary, marker)\n"
            "    raise SystemExit(0)\n"
            "if operation != 'serve':\n"
            "    raise SystemExit(2)\n"
            "marker.parent.mkdir(parents=True, exist_ok=True)\n"
            "temporary = marker.with_name(marker.name + f'.{os.getpid()}.tmp')\n"
            "temporary.write_text(json.dumps({'pid': os.getpid(), 'version': VERSION}), encoding='utf-8')\n"
            "os.replace(temporary, marker)\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "signal.signal(signal.SIGINT, lambda *_: sys.exit(0))\n"
            "while True:\n"
            "    signal.pause()\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        return release

    def _cli(self, command: str, *arguments: str) -> dict[str, object]:
        installed_cli = self.install_root / "bin" / "hindsight-memory"
        cli = installed_cli if installed_cli.is_file() else CLI
        if command in {"install", "upgrade"} and "--release-root" in arguments:
            release_index = arguments.index("--release-root") + 1
            cli = Path(arguments[release_index]) / "bin" / "hindsight-memory"
        completed = subprocess.run(
            [
                str(cli),
                command,
                "--config",
                str(self.config_path),
                *arguments,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd="/",
            env=_acceptance_subprocess_environment(self.platform),
            timeout=90,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=(
                f"portable {command} failed ({completed.returncode})\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            ),
        )
        return json.loads(completed.stdout)

    def _update_config(self, **updates: object) -> None:
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config.update(updates)
        self.config_path.write_text(
            json.dumps(config, sort_keys=True), encoding="utf-8"
        )

    def _manager_active(self) -> bool:
        if self.platform == "launchd":
            command = [
                "/bin/launchctl",
                "print",
                f"gui/{os.getuid()}/{self.label}",
            ]
        else:
            command = [
                "/usr/bin/systemctl",
                "--user",
                "is-active",
                f"{self.label}.service",
            ]
        return (
            subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            ).returncode
            == 0
        )

    def _timer_manager_active(self) -> bool:
        if self.platform == "launchd":
            command = [
                "/bin/launchctl",
                "print",
                f"gui/{os.getuid()}/{self.timer_label}",
            ]
        else:
            command = [
                "/usr/bin/systemctl",
                "--user",
                "is-active",
                f"{self.timer_label}.timer",
            ]
        return (
            subprocess.run(
                command,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            ).returncode
            == 0
        )

    def _wait_for_timer_version(self, version: str) -> dict[str, object]:
        deadline = time.monotonic() + (270 if self.platform == "systemd-user" else 15)
        last: dict[str, object] = {}
        while time.monotonic() < deadline:
            try:
                value = json.loads(self.timer_marker.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                time.sleep(0.05)
                continue
            if isinstance(value, dict):
                last = value
            if last.get("version") == version and self._timer_manager_active():
                if self.platform == "systemd-user":
                    self.assertGreaterEqual(
                        int(last.get("fired_at_ns", 0)), self.timer_not_before_ns
                    )
                    trigger = subprocess.run(
                        [
                            "/usr/bin/systemctl",
                            "--user",
                            "show",
                            "--property=LastTriggerUSecRealtime",
                            "--value",
                            f"{self.timer_label}.timer",
                        ],
                        check=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=15,
                    )
                    self.assertEqual(trigger.returncode, 0, msg=trigger.stderr)
                    self.assertNotIn(trigger.stdout.strip().lower(), {"", "n/a"})
                    companion_deadline = time.monotonic() + 10
                    while True:
                        companion = subprocess.run(
                            [
                                "/usr/bin/systemctl",
                                "--user",
                                "is-active",
                                f"{self.timer_label}.service",
                            ],
                            check=False,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=15,
                        )
                        if companion.returncode != 0:
                            break
                        if time.monotonic() >= companion_deadline:
                            break
                        time.sleep(0.05)
                    self.assertNotEqual(companion.returncode, 0)
                return last
            time.sleep(0.05)
        self.fail(f"managed {self.platform} timer did not run {version}: {last}")

    def _wait_for_version(self, version: str) -> dict[str, object]:
        deadline = time.monotonic() + 15
        last: dict[str, object] = {}
        while time.monotonic() < deadline:
            try:
                value = json.loads(self.marker.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                time.sleep(0.05)
                continue
            if isinstance(value, dict):
                last = value
            try:
                pid = int(last.get("pid", 0))
                os.kill(pid, 0)
            except (TypeError, ValueError, OSError):
                time.sleep(0.05)
                continue
            if last.get("version") == version and self._manager_active():
                self.started_pids.add(pid)
                return last
            time.sleep(0.05)
        self.fail(f"managed {self.platform} service did not reach {version}: {last}")

    def _wait_for_absence(self) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            running = []
            for pid in self.started_pids:
                try:
                    os.kill(pid, 0)
                except OSError:
                    continue
                running.append(pid)
            if (
                not self._manager_active()
                and not self._timer_manager_active()
                and not running
            ):
                return
            time.sleep(0.05)
        self.fail(
            "managed service or one of its observed processes remained after uninstall"
        )

    @staticmethod
    def _run_best_effort(argv: list[str], **kwargs: object) -> None:
        try:
            subprocess.run(
                argv,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
        except subprocess.TimeoutExpired:
            pass

    def _cleanup(self) -> None:
        try:
            if self.installed and self.config_path.exists():
                installed_cli = self.install_root / "bin" / "hindsight-memory"
                cli = installed_cli if installed_cli.is_file() else CLI
                self._run_best_effort(
                    [str(cli), "uninstall", "--config", str(self.config_path)],
                    cwd="/",
                    env=_acceptance_subprocess_environment(self.platform),
                    timeout=60,
                )
            if self.platform == "launchd":
                for label in (self.label, self.timer_label):
                    self._run_best_effort(
                        [
                            "/bin/launchctl",
                            "bootout",
                            f"gui/{os.getuid()}/{label}",
                        ],
                        timeout=15,
                    )
            else:
                self._run_best_effort(
                    [
                        "/usr/bin/systemctl",
                        "--user",
                        "disable",
                        "--now",
                        f"{self.timer_label}.timer",
                        f"{self.label}.service",
                    ],
                    timeout=15,
                )
                for manifest in (self.manifest, *self.timer_manifests):
                    try:
                        manifest.unlink()
                    except FileNotFoundError:
                        pass
                self._run_best_effort(
                    ["/usr/bin/systemctl", "--user", "daemon-reload"],
                    timeout=15,
                )
        finally:
            shutil.rmtree(self.root, ignore_errors=True)

    def test_full_lifecycle_uses_the_real_user_service_manager(self) -> None:
        release_v1 = self._release("1.0.0")
        release_v2 = self._release("2.0.0")

        installed = self._cli(
            "install", "--release-root", str(release_v1), "--version", "1.0.0"
        )
        self.installed = True
        self.assertEqual(installed["status"], "installed")
        self.assertTrue(self.manifest.is_file())
        self.assertTrue(all(path.is_file() for path in self.timer_manifests))
        self.assertIn(
            "candidate-installer-runtime:1.0.0",
            (self.install_root / "launcher.py").read_text(encoding="utf-8"),
        )
        self._wait_for_version("1.0.0")
        self._wait_for_timer_version("1.0.0")

        sentinel = self.data_root / "preserve-me"
        sentinel.write_bytes(b"portable-platform-acceptance\n")
        before = (
            sentinel.stat().st_dev,
            sentinel.stat().st_ino,
            sentinel.read_bytes(),
        )

        unchanged = self._cli(
            "install", "--release-root", str(release_v1), "--version", "1.0.0"
        )
        self.assertEqual(unchanged["status"], "unchanged")
        self._wait_for_version("1.0.0")

        upgraded = self._cli(
            "upgrade",
            "--release-root",
            str(release_v2),
            "--version",
            "2.0.0",
            "--expected-current-binding-generation-digest",
            str(installed["binding_generation_digest"]),
        )
        self.assertEqual(upgraded["status"], "upgraded")
        self.assertIn(
            "candidate-installer-runtime:2.0.0",
            (self.install_root / "launcher.py").read_text(encoding="utf-8"),
        )
        self._wait_for_version("2.0.0")
        self.assertEqual(self._cli("verify")["managed_health"], "healthy")

        rolled_back = self._cli(
            "rollback",
            "--expected-current-release-digest",
            str(upgraded["release_digest"]),
        )
        self.assertEqual(rolled_back["status"], "rolled-back")
        self._wait_for_version("1.0.0")
        verification = self._cli("verify")
        self.assertEqual(verification["current"]["version"], "1.0.0")

        uninstalled = self._cli("uninstall")
        self.installed = False
        self.assertEqual(uninstalled["status"], "uninstalled")
        self.assertTrue(uninstalled["data_preserved"])
        self._wait_for_absence()
        self.assertFalse(self.manifest.exists())
        self.assertTrue(all(not path.exists() for path in self.timer_manifests))
        self.assertFalse(self.install_root.exists())
        after = (
            sentinel.stat().st_dev,
            sentinel.stat().st_ino,
            sentinel.read_bytes(),
        )
        self.assertEqual(after, before)

    def test_adoption_preserves_existing_data_identity(self) -> None:
        self.data_root.mkdir(mode=0o700)
        sentinel = self.data_root / "existing-data"
        sentinel.write_bytes(b"adoption-must-not-replace-this-root\n")
        before = (
            self.data_root.stat().st_dev,
            self.data_root.stat().st_ino,
            sentinel.stat().st_dev,
            sentinel.stat().st_ino,
            sentinel.read_bytes(),
        )
        self._update_config(installation_mode="adopt")

        installed = self._cli(
            "install",
            "--release-root",
            str(self._release("1.0.0")),
            "--version",
            "1.0.0",
        )
        self.installed = True
        self.assertEqual(installed["status"], "installed")
        self._wait_for_version("1.0.0")
        self._wait_for_timer_version("1.0.0")
        verification = self._cli("verify")
        self.assertEqual(verification["managed_health"], "healthy")

        uninstalled = self._cli("uninstall")
        self.installed = False
        self.assertEqual(uninstalled["status"], "uninstalled")
        self.assertTrue(uninstalled["data_preserved"])
        self._wait_for_absence()
        after = (
            self.data_root.stat().st_dev,
            self.data_root.stat().st_ino,
            sentinel.stat().st_dev,
            sentinel.stat().st_ino,
            sentinel.read_bytes(),
        )
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
