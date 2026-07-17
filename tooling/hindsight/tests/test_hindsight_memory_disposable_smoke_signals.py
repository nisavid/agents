from __future__ import annotations

import subprocess
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "tests" / "hindsight-memory-disposable-smoke.zsh"


class DisposableSmokeSignalTests(unittest.TestCase):
    @staticmethod
    def _pg_cleanup_program() -> str:
        source = SMOKE.read_text(encoding="utf-8")
        marker = '"$HINDSIGHT_PYTHON" -c \'\n'
        start = source.index(marker, source.index("for name in $PG_NAMES")) + len(marker)
        end = source.index("\n' >/dev/null", start)
        return source[start:end]

    @staticmethod
    def _signal_fragment() -> str:
        source = SMOKE.read_text(encoding="utf-8")
        start = source.index("typeset CLEANED_UP=0")
        end = source.index("TRAPZERR()", start)
        return source[start:end]

    @staticmethod
    def _api_identity_fragment() -> str:
        source = SMOKE.read_text(encoding="utf-8")
        start = source.index("api_process_identity()")
        end = source.index("mkdir -p", start)
        return source[start:end]

    @staticmethod
    def _api_retry_fragment() -> str:
        source = SMOKE.read_text(encoding="utf-8")
        start = source.index("typeset STARTED_API_PORT=")
        end = source.index("hindsight_cli()", start)
        return source[start:end]

    def test_api_start_retry_guards_failure_before_reading_started_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = root / "events"
            harness = root / "api-retry-harness.zsh"
            harness.write_text(
                "#!/usr/bin/env zsh\n"
                "set -euo pipefail\n"
                "typeset STARTED_API_PID=stale\n"
                "typeset attempts=0\n"
                f"typeset events={str(events)!r}\n"
                "free_port() { print -r -- $(( 8001 + attempts )); }\n"
                "start_api() {\n"
                "  (( attempts += 1 ))\n"
                "  print -r -- start:$attempts >>$events\n"
                "  (( attempts >= 3 )) || return 1\n"
                "  STARTED_API_PID=4321\n"
                "}\n"
                "wait_for_api() {\n"
                "  print -r -- wait:$1:$2 >>$events\n"
                "  [[ $2 == 4321 ]]\n"
                "}\n"
                "terminate_api_pid() { exit 91; }\n"
                "typeset -a API_PIDS=()\n"
                "typeset -A API_IDENTITIES=()\n"
                + self._api_retry_fragment()
                + "\nstart_api_on_free_port db role log || exit 92\n"
                + "[[ $attempts == 3 && $STARTED_API_PORT == 8003 ]] || exit 93\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["/bin/zsh", str(harness)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                events.read_text(encoding="utf-8").splitlines(),
                ["start:1", "start:2", "start:3", "wait:8003:4321"],
            )

    def test_pg_cleanup_accepts_only_an_artifact_free_failed_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            smoke_root = root / "hindsight-memory-smoke.test"
            smoke_root.mkdir(mode=0o700)
            home = root / "home"
            home.mkdir(mode=0o700)
            (root / "pg0.py").write_text("def list_instances(): return []\n", encoding="utf-8")
            name = "hindsight-smoke-test-source-1"
            data = smoke_root / "missing-data"
            registration = home / ".pg0" / "instances" / name
            env = {
                **os.environ,
                "PYTHONPATH": str(root),
                "HOME": str(home),
                "PG0_NAME": name,
                "PG0_DATA_DIR": str(data),
                "PG0_RUN_ID": "test",
                "PG0_SMOKE_ROOT": str(smoke_root),
                "PG0_REGISTRATION": str(registration),
            }
            absent = subprocess.run(
                [sys.executable, "-c", self._pg_cleanup_program()],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(absent.returncode, 0, absent.stderr)
            data.mkdir()
            partial = subprocess.run(
                [sys.executable, "-c", self._pg_cleanup_program()],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(partial.returncode, 3, partial.stderr)

    def test_api_termination_refuses_changed_process_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "identity-harness.zsh"
            harness.write_text(
                "#!/bin/zsh\n"
                "typeset -A API_IDENTITIES=()\n"
                + self._api_identity_fragment()
                + "\n/bin/sleep 30 &\n"
                + "pid=$!\n"
                + "trap '/bin/kill -TERM $pid >/dev/null 2>&1 || true; "
                + "wait $pid >/dev/null 2>&1 || true' EXIT\n"
                + "API_IDENTITIES[$pid]='changed process identity'\n"
                + "terminate_api_pid $pid >/dev/null 2>&1 && exit 91\n"
                + "kill -0 $pid >/dev/null 2>&1 || exit 92\n"
                + "/bin/kill -TERM $pid\n"
                + "wait $pid >/dev/null 2>&1 || true\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["/bin/zsh", str(harness)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_api_termination_accepts_confirmed_pid_reuse_after_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            harness = Path(directory) / "identity-reuse-harness.zsh"
            harness.write_text(
                "#!/bin/zsh\n"
                "typeset -A API_IDENTITIES=()\n"
                + self._api_identity_fragment()
                + "\n/bin/sleep 30 &\n"
                + "pid=$!\n"
                + "trap '/bin/kill -TERM $pid >/dev/null 2>&1 || true; "
                + "wait $pid >/dev/null 2>&1 || true' EXIT\n"
                + "typeset signaled=0\n"
                + "api_process_identity() {\n"
                + "  if (( signaled )); then\n"
                + "    print -r -- '999 Tue Jan  2 00:00:00 2025'\n"
                + "  else\n"
                + "    print -r -- \"$$ Mon Jan  1 00:00:00 2024\"\n"
                + "  fi\n"
                + "}\n"
                + "kill() {\n"
                + "  if [[ $1 == -TERM ]]; then signaled=1; return 0; fi\n"
                + "  [[ $1 != -KILL ]] || exit 91\n"
                + "  /bin/kill $@\n"
                + "}\n"
                + "API_IDENTITIES[$pid]=\"$$ Mon Jan  1 00:00:00 2024\"\n"
                + "terminate_api_pid $pid || exit 92\n"
                + "[[ -z ${API_IDENTITIES[$pid]-} ]] || exit 93\n"
                + "/bin/kill -TERM $pid\n"
                + "wait $pid >/dev/null 2>&1 || true\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["/bin/zsh", str(harness)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_api_identity_capture_reattempts_then_reaps_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / "identity-failure-harness.zsh"
            attempts = root / "identity-attempts"
            harness.write_text(
                "#!/bin/zsh\n"
                "typeset -a API_PIDS=()\n"
                "typeset -A API_IDENTITIES=()\n"
                + self._api_identity_fragment()
                + "api_process_identity() {\n"
                + f"  print -r -- attempt >> {str(attempts)!r}\n"
                + f"  (( $(/usr/bin/wc -l < {str(attempts)!r}) > 1 )) || return 1\n"
                + "  print -r -- \"$$ Mon Jan  1 00:00:00 2024\"\n"
                + "}\n"
                + "\n/bin/sleep 30 &\n"
                + "pid=$!\n"
                + "trap '/bin/kill -TERM $pid >/dev/null 2>&1 || true; "
                + "wait $pid >/dev/null 2>&1 || true' EXIT\n"
                + "API_PIDS+=($pid)\n"
                + "terminate_started_api_pid $pid || exit 91\n"
                + f"(( $(/usr/bin/wc -l < {str(attempts)!r}) >= 3 )) || exit 94\n"
                + "(( ${#API_PIDS} == 0 )) || exit 92\n"
                + "kill -0 $pid >/dev/null 2>&1 && exit 93\n"
                + "exit 0\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["/bin/zsh", str(harness)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_identity_lookup_race_rechecks_absence_and_reaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / "identity-race-harness.zsh"
            reaped = root / "reaped"
            harness.write_text(
                "#!/bin/zsh\n"
                "typeset -A API_IDENTITIES=()\n"
                + self._api_identity_fragment()
                + "\ntypeset kill_checks=0\n"
                + "kill() {\n"
                + "  if [[ $1 == -0 ]]; then\n"
                + "    (( kill_checks += 1 ))\n"
                + "    (( kill_checks == 1 ))\n"
                + "    return\n"
                + "  fi\n"
                + "  return 0\n"
                + "}\n"
                + f"wait() {{ print -r -- reaped > {str(reaped)!r}; }}\n"
                + "api_process_identity() { return 1 }\n"
                + "pid=4242\n"
                + "API_IDENTITIES[$pid]='4241 Mon Jan  1 00:00:00 2024'\n"
                + "terminate_api_pid $pid || exit 91\n"
                + "[[ -z ${API_IDENTITIES[$pid]-} ]] || exit 92\n"
                + "(( kill_checks >= 2 )) || exit 93\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["/bin/zsh", str(harness)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(reaped.read_text(encoding="utf-8"), "reaped\n")

    def test_started_api_termination_refuses_unverified_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / "unverified-start-harness.zsh"
            harness.write_text(
                "#!/bin/zsh\n"
                "typeset -a API_PIDS=()\n"
                "typeset -A API_IDENTITIES=()\n"
                + self._api_identity_fragment()
                + "\n/bin/sleep 30 &\n"
                + "pid=$!\n"
                + "trap '/bin/kill -TERM $pid >/dev/null 2>&1 || true; "
                + "wait $pid >/dev/null 2>&1 || true' EXIT\n"
                + "API_PIDS+=($pid)\n"
                + "api_process_identity() { return 1 }\n"
                + "terminate_started_api_pid $pid >/dev/null 2>&1 && exit 91\n"
                + "kill -0 $pid >/dev/null 2>&1 || exit 92\n"
                + "/bin/kill -TERM $pid\n"
                + "wait $pid >/dev/null 2>&1 || true\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["/bin/zsh", str(harness)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_interrupt_and_termination_cleanup_once_then_exit(self) -> None:
        signal_fragment = self._signal_fragment()

        for signal, expected_status in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=signal), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "cleanup-count"
                survived = root / "survived"
                harness = root / "harness.zsh"
                harness.write_text(
                    signal_fragment
                    + "\ncleanup() {\n"
                    + "  (( CLEANED_UP )) && return 0\n"
                    + "  CLEANED_UP=1\n"
                    + f"  print -r -- cleanup >> {str(marker)!r}\n"
                    + "}\n"
                    + f"kill -{signal} $$\n"
                    + f"print -r -- survived > {str(survived)!r}\n",
                    encoding="utf-8",
                )

                result = subprocess.run(
                    ["/bin/zsh", str(harness)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )

                self.assertEqual(result.returncode, expected_status, result.stderr)
                self.assertEqual(marker.read_text(encoding="utf-8"), "cleanup\n")
                self.assertFalse(survived.exists())

    def test_signal_during_cleanup_defers_exit_until_api_and_postgres_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            phases = root / "cleanup-phases"
            survived = root / "survived"
            harness = root / "mid-cleanup-signal.zsh"
            harness.write_text(
                self._signal_fragment()
                + "\ncleanup() {\n"
                + "  (( CLEANED_UP )) && return 0\n"
                + "  (( CLEANUP_RUNNING )) && return 1\n"
                + "  CLEANUP_RUNNING=1\n"
                + f"  print -r -- api >> {str(phases)!r}\n"
                + "  kill -TERM $$\n"
                + f"  print -r -- postgres >> {str(phases)!r}\n"
                + "  CLEANED_UP=1\n"
                + "  finish_cleanup 0\n"
                + "}\n"
                + "cleanup\n"
                + f"print -r -- survived > {str(survived)!r}\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["/bin/zsh", str(harness)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(result.returncode, 143, result.stderr)
            self.assertEqual(phases.read_text(encoding="utf-8"), "api\npostgres\n")
            self.assertFalse(survived.exists())

    def test_command_substitution_does_not_trigger_exit_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "cleanup-count"
            survived = root / "survived"
            harness = root / "harness.zsh"
            harness.write_text(
                self._signal_fragment()
                + "\ncleanup() {\n"
                + "  (( CLEANED_UP )) && return 0\n"
                + "  CLEANED_UP=1\n"
                + f"  print -r -- cleanup >> {str(marker)!r}\n"
                + "}\n"
                + "typeset value=$(print -r -- ready)\n"
                + f"[[ ! -e {str(marker)!r} ]]\n"
                + "[[ $value == ready ]]\n"
                + f"print -r -- survived > {str(survived)!r}\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["/bin/zsh", str(harness)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "cleanup\n")
            self.assertTrue(survived.exists())

    def test_pg0_cleanup_failure_retains_root_and_remains_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            smoke_root = outer / "hindsight-memory-smoke.test"
            smoke_root.mkdir()
            (smoke_root / "data").mkdir()
            fake_python = outer / "fake-python.zsh"
            absent = outer / "instances-absent"
            result_path = outer / "result"
            fake_python.write_text(
                "#!/usr/bin/env zsh\n"
                "if [[ -n ${SMOKE_PG_NAMES:-} ]]; then\n"
                f"  [[ -e {str(absent)!r} ]] && print -r -- 0 || print -r -- 1\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o700)
            harness = outer / "cleanup-harness.zsh"
            harness.write_text(
                "#!/usr/bin/env zsh\n"
                f"typeset SMOKE_PARENT={str(outer)!r}\n"
                f"typeset SMOKE_ROOT={str(smoke_root)!r}\n"
                "typeset SMOKE_RUN_ID=test\n"
                f"typeset HINDSIGHT_PYTHON={str(fake_python)!r}\n"
                "typeset -a API_PIDS=() PG_NAMES=(run-owned)\n"
                "typeset -A PG_DATA_DIRS=(run-owned $SMOKE_ROOT/data)\n"
                + self._signal_fragment()
                + "\ncleanup && exit 91\n"
                + "[[ -d $SMOKE_ROOT && $CLEANED_UP == 0 ]] || exit 92\n"
                + f"touch {str(absent)!r}\n"
                + "cleanup || exit 93\n"
                + "[[ ! -e $SMOKE_ROOT && $CLEANED_UP == 1 ]] || exit 94\n"
                + f"print -r -- retry-succeeded > {str(result_path)!r}\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["/bin/zsh", str(harness)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result_path.read_text(encoding="utf-8"), "retry-succeeded\n"
            )

    def test_unknown_pg0_registration_retains_smoke_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            smoke_root = outer / "hindsight-memory-smoke.test"
            smoke_root.mkdir()
            (smoke_root / "data").mkdir()
            fake_python = outer / "fake-python.zsh"
            fake_python.write_text(
                "#!/usr/bin/env zsh\n"
                "if [[ -n ${SMOKE_PG_NAMES:-} ]]; then print -r -- 0; exit 0; fi\n"
                "exit 3\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o700)
            harness = outer / "cleanup-harness.zsh"
            harness.write_text(
                "#!/usr/bin/env zsh\n"
                f"typeset SMOKE_PARENT={str(outer)!r}\n"
                f"typeset SMOKE_ROOT={str(smoke_root)!r}\n"
                "typeset SMOKE_RUN_ID=test\n"
                f"typeset HINDSIGHT_PYTHON={str(fake_python)!r}\n"
                "typeset -a API_PIDS=() PG_NAMES=(run-owned)\n"
                "typeset -A PG_DATA_DIRS=(run-owned $SMOKE_ROOT/data)\n"
                + self._signal_fragment()
                + "\ncleanup && exit 91\n"
                + "[[ -d $SMOKE_ROOT && $CLEANED_UP == 0 ]] || exit 92\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["/bin/zsh", str(harness)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("retained", result.stderr)


if __name__ == "__main__":
    unittest.main()
