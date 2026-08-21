from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parent.parent
SUPERVISOR = ROOT / "bin/hindsight-hook-authority-supervisor"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HookAuthoritySupervisorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.install = self.root / "install"
        self.authority_digest = "b" * 64
        self.base_digest = "a" * 64
        self.authority_release = (
            self.install
            / "releases"
            / f"2.0.0-{self.authority_digest[:16]}"
        )
        authority_bin = self.authority_release / "bin"
        authority_bin.mkdir(parents=True)
        self.supervisor = (
            authority_bin / "hindsight-hook-authority-supervisor"
        )
        shutil.copyfile(SUPERVISOR, self.supervisor)
        self.supervisor.chmod(0o500)
        self.controller = authority_bin / "hindsight-memory"
        self.controller.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.controller.chmod(0o500)
        self.controller_digest = _digest(self.controller)
        self.reconciler_log = self.root / "reconciler.log"
        self.reconciler_fail = self.root / "reconciler.fail"
        self.reconciler_fail_after_first = (
            self.root / "reconciler.fail-after-first"
        )
        self.reconciler = self.root / "harness-reconciler"
        self.reconciler.write_text(
            (
                "#!/bin/sh\n"
                'printf "%s|%s\\n" "$1" '
                '"$HINDSIGHT_HOOK_AUTHORITY_CONTROLLER" '
                f">>{str(self.reconciler_log)!r}\n"
                f"[ -e {str(self.reconciler_fail)!r} ] && exit 1\n"
                f"if [ -e {str(self.reconciler_fail_after_first)!r} ] "
                f"&& [ \"$(wc -l <{str(self.reconciler_log)!r})\" -gt 1 ]; "
                "then exit 1; fi\n"
            ),
            encoding="utf-8",
        )
        self.reconciler.chmod(0o700)
        self.reconcile_config = self.root / "reconcile.json"
        self.reconcile_config.write_text("{}\n", encoding="utf-8")
        self.reconcile_config.chmod(0o600)

        self.base = {
            "version": "1.0.0",
            "release_digest": self.base_digest,
            "release_path": f"releases/1.0.0-{self.base_digest[:16]}",
            "manifest": {"files": []},
        }
        self.authority = {
            "version": "2.0.0",
            "release_digest": self.authority_digest,
            "release_path": (
                f"releases/2.0.0-{self.authority_digest[:16]}"
            ),
            "manifest": {
                "files": [
                    {
                        "path": "bin/hindsight-memory",
                        "executable": True,
                        "sha256": self.controller_digest,
                    }
                ]
            },
        }
        self.active_path = self.install / "active.json"
        self.authority_path = self.install / "hook-authority.json"
        self.state_path = self.install / "install-state.json"
        self._write(
            self.active_path,
            {
                key: self.base[key]
                for key in ("version", "release_digest", "release_path")
            },
            0o600,
        )
        self._write(self.authority_path, self.authority, 0o500)
        self._write(
            self.state_path,
            {
                "current": self.base,
                "owned_install_files": {
                    str(self.authority_path): _digest(self.authority_path),
                },
                "releases": {
                    self.base_digest: self.base,
                    self.authority_digest: self.authority,
                },
            },
            0o600,
        )

    @staticmethod
    def _write(path: Path, value: object, mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        path.chmod(mode)

    def _start(self) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, "-I", str(self.supervisor)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "HINDSIGHT_EMBED_POLL_SECONDS": "1",
                "HINDSIGHT_HOOK_AUTHORITY_CONTROLLER_SHA256": (
                    self.controller_digest
                ),
                "HINDSIGHT_HOOK_AUTHORITY_RELEASE_DIGEST": (
                    self.authority_digest
                ),
                "HINDSIGHT_MEMORY_HARNESS_RECONCILER": str(
                    self.reconciler
                ),
                "HINDSIGHT_MEMORY_HARNESS_RECONCILE_CONFIG": str(
                    self.reconcile_config
                ),
                "HOME": str(self.root),
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            },
        )

    def _wait_for_calls(self, count: int) -> None:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.reconciler_log.exists():
                lines = self.reconciler_log.read_text().splitlines()
                if len(lines) >= count:
                    return
            time.sleep(0.05)
        self.fail(f"reconciler did not receive {count} calls")

    def _select_authority_release(self) -> None:
        self._write(
            self.active_path,
            {
                key: self.authority[key]
                for key in ("version", "release_digest", "release_path")
            },
            0o600,
        )
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["current"] = self.authority
        self._write(self.state_path, state, 0o600)

    def test_current_authority_stays_enabled_across_multiple_polls(self) -> None:
        self._select_authority_release()
        process = self._start()
        try:
            time.sleep(2.2)
            self.assertIsNone(process.poll())
            self.assertFalse(self.reconciler_log.exists())
            process.terminate()
            stdout, stderr = process.communicate(timeout=4)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(stdout, b"")
            self.assertEqual(stderr, b"")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=4)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def test_disable_failures_are_visible_for_each_supervision_path(
        self,
    ) -> None:
        cases = (
            (
                "transition",
                "during the legacy runtime transition",
            ),
            (
                "steady-state",
                "while the legacy runtime remains active",
            ),
            (
                "validation",
                "after authority validation failed",
            ),
        )
        for case, message in cases:
            with self.subTest(case=case):
                if case == "validation":
                    self._select_authority_release()
                    self.authority_path.chmod(0o700)
                if case == "steady-state":
                    self.reconciler_fail_after_first.touch()
                else:
                    self.reconciler_fail.touch()
                process = self._start()
                try:
                    _stdout, stderr = process.communicate(timeout=4)
                    self.assertEqual(process.returncode, 1)
                    self.assertIn(message.encode(), stderr)
                    self.assertIn(b"hooks may remain active", stderr)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=4)
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()
                self.reconciler_log.unlink(missing_ok=True)
                self.reconciler_fail.unlink(missing_ok=True)
                self.reconciler_fail_after_first.unlink(missing_ok=True)
                self.authority_path.chmod(0o700)
                self._write(
                    self.authority_path,
                    self.authority,
                    0o500,
                )
                self._write(
                    self.active_path,
                    {
                        key: self.base[key]
                        for key in (
                            "version",
                            "release_digest",
                            "release_path",
                        )
                    },
                    0o600,
                )
                state = json.loads(
                    self.state_path.read_text(encoding="utf-8")
                )
                state["current"] = self.base
                state["owned_install_files"][str(self.authority_path)] = (
                    _digest(self.authority_path)
                )
                self._write(self.state_path, state, 0o600)
    def test_live_record_drift_disables_through_anchored_candidate(
        self,
    ) -> None:
        for drift in ("active", "authority", "authority-mode"):
            with self.subTest(drift=drift):
                process = self._start()
                try:
                    self._wait_for_calls(1)
                    if drift == "active":
                        self.active_path.chmod(0o700)
                        self._write(
                            self.active_path,
                            {
                                key: self.authority[key]
                                for key in (
                                    "version",
                                    "release_digest",
                                    "release_path",
                                )
                            },
                            0o600,
                        )
                    elif drift == "authority":
                        self.authority_path.chmod(0o700)
                        changed = {**self.authority, "version": "2.0.1"}
                        self._write(self.authority_path, changed, 0o500)
                    else:
                        self.authority_path.chmod(0o700)
                    self._wait_for_calls(2)
                    self.assertEqual(process.wait(timeout=4), 1)
                    calls = self.reconciler_log.read_text().splitlines()
                    self.assertTrue(
                        all(
                            call == f"disable|{self.controller}"
                            for call in calls
                        )
                    )
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=4)
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()
                self.reconciler_log.unlink(missing_ok=True)
                self._write(
                    self.active_path,
                    {
                        key: self.base[key]
                        for key in (
                            "version",
                            "release_digest",
                            "release_path",
                        )
                    },
                    0o600,
                )
                self.authority_path.chmod(0o700)
                self._write(self.authority_path, self.authority, 0o500)


if __name__ == "__main__":
    unittest.main()
