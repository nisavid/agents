from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "bin" / "hindsight-embed-uvx"
EXAMPLES = ROOT / "examples" / "portable-consumer"


class HindsightEmbedUvxTest(unittest.TestCase):
    def test_wrapper_pins_hindsight_embed_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "argv.json"
            fake_uvx = root / "uvx"
            fake_uvx.write_text(
                "#!/bin/sh\n"
                '"$PYTHON_FOR_TEST" -c \'import json, os, sys; '
                "open(os.environ[\"CAPTURE\"], \"w\").write(json.dumps(sys.argv[1:]))' "
                '"$@"\n',
                encoding="utf-8",
            )
            fake_uvx.chmod(0o700)

            environment = {
                "CAPTURE": str(capture),
                "HINDSIGHT_EMBED_UVX_EXECUTABLE": str(fake_uvx),
                "PATH": f"{root}:/usr/bin:/bin",
                "PYTHON_FOR_TEST": sys.executable,
            }
            result = subprocess.run(
                [
                    str(WRAPPER),
                    "hindsight-embed",
                    "--profile",
                    "core",
                    "daemon",
                    "status",
                ],
                check=False,
                capture_output=True,
                env=environment,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(capture.read_text(encoding="utf-8")),
                [
                    "--from",
                    "hindsight-embed==0.8.4",
                    "hindsight-embed",
                    "--profile",
                    "core",
                    "daemon",
                    "status",
                ],
            )

    def test_wrapper_ignores_path_without_a_configured_uvx(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_uvx = root / "uvx"
            fake_uvx.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_uvx.chmod(0o700)

            result = subprocess.run(
                [str(WRAPPER), "hindsight-embed", "daemon", "status"],
                check=False,
                capture_output=True,
                env={"PATH": f"{root}:/usr/bin:/bin"},
                text=True,
            )

            self.assertEqual(result.returncode, 69)
            self.assertIn("HINDSIGHT_EMBED_UVX_EXECUTABLE", result.stderr)

    def test_wrapper_rejects_other_uvx_commands(self) -> None:
        result = subprocess.run(
            [str(WRAPPER), "python"],
            check=False,
            capture_output=True,
            env={"PATH": "/usr/bin:/bin"},
            text=True,
        )

        self.assertEqual(result.returncode, 64)
        self.assertIn("expected hindsight-embed as the command", result.stderr)

    def test_portable_examples_use_the_release_owned_wrapper(self) -> None:
        for name in ("launchd-installation.json", "systemd-user-installation.json"):
            payload = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
            bindings = [
                service["environment"]["HINDSIGHT_EMBED_UVX"]
                for service in payload["services"]
            ] + [
                check["environment"]["HINDSIGHT_EMBED_UVX"]
                for check in payload["health_checks"]
            ]
            self.assertEqual(
                bindings,
                ["release://bin/hindsight-embed-uvx"] * len(bindings),
                name,
            )


if __name__ == "__main__":
    unittest.main()
