from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
SHIM_TEMPLATE = (
    REPOSITORY / "plugins" / "task-witness" / "client" / "task_witness_shim.sh.in"
)
SHIM_OBSERVER_DRIVER = Path(__file__).resolve().parent / "_shim_observer_driver.py"


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def render_shim(template: str, interpreter: Path, client: Path) -> str:
    return template.replace(
        "@TASK_WITNESS_PYTHON@",
        shell_quote(str(interpreter)),
    ).replace(
        "@TASK_WITNESS_CLIENT@",
        shell_quote(str(client)),
    )


class TaskWitnessShimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_rendered_shim_pins_the_client_process_and_preserves_passed_descriptors(
        self,
    ) -> None:
        template = SHIM_TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(template.count("@TASK_WITNESS_PYTHON@"), 1)
        self.assertEqual(template.count("@TASK_WITNESS_CLIENT@"), 1)
        self.assertTrue(template.endswith("\n"))

        client_directory = self.root / "client's scripts"
        client_directory.mkdir()
        client = client_directory / "task_witness_client.py"
        interpreter = Path(sys.executable).resolve()
        observation = self.root / "observation.json"
        client.write_text(
            SHIM_OBSERVER_DRIVER.read_text(encoding="utf-8")
            + f"\nobserve_shim({str(observation)!r})\n",
            encoding="utf-8",
        )
        shim = self.root / "task-witness"
        shim.write_text(
            render_shim(
                template,
                interpreter,
                client,
            ),
            encoding="utf-8",
        )
        shim.chmod(0o500)
        syntax = subprocess.run(
            ["/bin/sh", "-n", str(shim)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

        saved_descriptors = {}
        try:
            for descriptor in (3, 4):
                try:
                    saved_descriptors[descriptor] = os.dup(descriptor)
                except OSError:
                    saved_descriptors[descriptor] = None
                source = os.open(os.devnull, os.O_RDONLY)
                try:
                    os.dup2(source, descriptor)
                finally:
                    if source != descriptor:
                        os.close(source)
            arguments = [
                "validate",
                "--bundle",
                "/tmp/bundle with spaces",
                "",
                "--literal=*",
            ]
            result = subprocess.run(
                [str(shim), *arguments],
                text=False,
                capture_output=True,
                check=False,
                env={
                    "HOME": "/attacker/home",
                    "PATH": "/attacker/bin",
                    "PYTHONPATH": "/attacker/python",
                    "PYTHONWARNINGS": "error",
                    "VIRTUAL_ENV": "/attacker/venv",
                    "GIT_CONFIG_GLOBAL": "/attacker/gitconfig",
                    "SSH_AUTH_SOCK": "/attacker/agent",
                    "XDG_CONFIG_HOME": "/attacker/xdg",
                    "HTTPS_PROXY": "http://attacker.invalid",
                    "LC_ALL": "attacker",
                },
                pass_fds=(3, 4),
                timeout=5,
            )
        finally:
            for descriptor, saved in saved_descriptors.items():
                if saved is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    os.dup2(saved, descriptor)
                    os.close(saved)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        observed = json.loads(observation.read_text(encoding="utf-8"))
        self.assertEqual(observed["argv"], arguments)
        self.assertEqual(Path(observed["executable"]).resolve(), interpreter)
        observed_environment = observed["environment"]
        if sys.platform == "darwin":
            apple_text_encoding = observed_environment.pop(
                "__CF_USER_TEXT_ENCODING",
                None,
            )
            self.assertRegex(
                apple_text_encoding or "", r"\A0x[0-9A-F]+:0x[0-9A-F]+:0x[0-9A-F]+\Z"
            )
        self.assertEqual(
            observed_environment,
            {
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TZ": "UTC",
            },
        )
        self.assertEqual(
            observed["flags"],
            {
                "dont_write_bytecode": 1,
                "isolated": 1,
                "no_site": 1,
            },
        )
        self.assertEqual(
            observed["xoptions"],
            {"disable-remote-debug": True},
        )
        if sys.version_info >= (3, 14):
            self.assertEqual(
                observed["remote_debug"],
                {
                    "api": "available",
                    "error": "Remote debugging is not enabled",
                    "outcome": "disabled",
                },
            )
        else:
            self.assertEqual(
                observed["remote_debug"],
                {"api": "unavailable"},
            )
        self.assertEqual(observed["open_descriptors"], [3, 4])

        unsafe_template = template.replace(
            "/usr/bin/env -i LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC ",
            "",
        )
        self.assertNotEqual(unsafe_template, template)
        shim.chmod(0o700)
        shim.write_text(
            render_shim(unsafe_template, interpreter, client),
            encoding="utf-8",
        )
        shim.chmod(0o500)
        mutated = subprocess.run(
            [str(shim), "validate"],
            capture_output=True,
            check=False,
            env={"TASK_WITNESS_SHIM_MUTATION": "exposed"},
            timeout=5,
        )
        self.assertEqual(mutated.returncode, 0, mutated.stderr.decode())
        self.assertEqual(
            json.loads(observation.read_text(encoding="utf-8"))["environment"][
                "TASK_WITNESS_SHIM_MUTATION"
            ],
            "exposed",
        )


if __name__ == "__main__":
    unittest.main()
