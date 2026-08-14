from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
RUNTIME = REPOSITORY / "plugins" / "task-witness" / "runtime"
PAYLOADS = ("task_witness.py", "canonical.py", "bundle_io.py", "trust.py")


class TaskWitnessRuntimeTests(unittest.TestCase):
    def test_each_runtime_payload_rejects_direct_execution(self) -> None:
        for name in PAYLOADS:
            with self.subTest(name=name):
                result = subprocess.run(
                    [sys.executable, "-B", "-I", "-S", str(RUNTIME / name)],
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "must be launched by task_witness_launch.py", result.stderr
                )
