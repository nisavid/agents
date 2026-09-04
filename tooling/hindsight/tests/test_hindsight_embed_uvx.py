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
ADOPTION = ROOT / "docs" / "adoption.md"


class HindsightEmbedUvxTest(unittest.TestCase):
    def test_wrapper_pins_hindsight_embed_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "argv.json"
            uvx_directory = root / "uv-bin"
            npx_directory = root / "node-bin"
            uvx_directory.mkdir()
            npx_directory.mkdir()
            fake_uvx = uvx_directory / "uvx"
            fake_npx = npx_directory / "npx"
            fake_node = npx_directory / "node"
            fake_uvx.write_text(
                "#!/bin/sh\n"
                '"$PYTHON_FOR_TEST" -c \'import json, os, shutil, sys; '
                "open(os.environ[\"CAPTURE\"], \"w\").write(json.dumps({"
                "\"argv\": sys.argv[1:], \"path\": os.environ[\"PATH\"], "
                "\"api_version\": os.environ.get(\"HINDSIGHT_EMBED_API_VERSION\"), "
                "\"nested_uvx\": shutil.which(\"uvx\"), "
                "\"nested_npx\": shutil.which(\"npx\"), "
                "\"nested_node\": shutil.which(\"node\")}))' "
                '"$@"\n',
                encoding="utf-8",
            )
            fake_uvx.chmod(0o700)
            fake_npx.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_npx.chmod(0o700)
            fake_node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_node.chmod(0o700)

            environment = {
                "CAPTURE": str(capture),
                "HINDSIGHT_EMBED_NPX_EXECUTABLE": str(fake_npx),
                "HINDSIGHT_EMBED_UVX_EXECUTABLE": str(fake_uvx),
                "PATH": "/usr/bin:/bin",
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
                json.loads(capture.read_text(encoding="utf-8"))["argv"],
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
            observed = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(
                observed["path"],
                f"{uvx_directory}:{npx_directory}:/usr/bin:/bin:/usr/sbin:/sbin",
            )
            self.assertEqual(observed["nested_uvx"], str(fake_uvx))
            self.assertEqual(observed["nested_npx"], str(fake_npx))
            self.assertEqual(observed["nested_node"], str(fake_node))
            self.assertEqual(observed["api_version"], "0.9.2")

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

    def test_wrapper_preserves_the_approved_explicit_api_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "api-version.txt"
            fake_uvx = root / "uvx"
            fake_npx = root / "npx"
            fake_uvx.write_text(
                "#!/bin/sh\n"
                'printf \'%s\\n\' "$HINDSIGHT_EMBED_API_VERSION" >"$CAPTURE"\n',
                encoding="utf-8",
            )
            fake_uvx.chmod(0o700)
            fake_npx.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_npx.chmod(0o700)

            result = subprocess.run(
                [str(WRAPPER), "hindsight-embed", "daemon", "status"],
                check=False,
                capture_output=True,
                env={
                    "CAPTURE": str(capture),
                    "HINDSIGHT_EMBED_API_VERSION": "0.9.2",
                    "HINDSIGHT_EMBED_NPX_EXECUTABLE": str(fake_npx),
                    "HINDSIGHT_EMBED_UVX_EXECUTABLE": str(fake_uvx),
                    "PATH": "/usr/bin:/bin",
                },
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(capture.read_text(encoding="utf-8"), "0.9.2\n")

    def test_wrapper_rejects_an_unapproved_explicit_api_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "uvx-ran"
            fake_uvx = root / "uvx"
            fake_npx = root / "npx"
            fake_uvx.write_text(
                "#!/bin/sh\n"
                'touch "$CAPTURE"\n',
                encoding="utf-8",
            )
            fake_uvx.chmod(0o700)
            fake_npx.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_npx.chmod(0o700)

            result = subprocess.run(
                [str(WRAPPER), "hindsight-embed", "daemon", "status"],
                check=False,
                capture_output=True,
                env={
                    "CAPTURE": str(capture),
                    "HINDSIGHT_EMBED_API_VERSION": "0.9.1",
                    "HINDSIGHT_EMBED_NPX_EXECUTABLE": str(fake_npx),
                    "HINDSIGHT_EMBED_UVX_EXECUTABLE": str(fake_uvx),
                    "PATH": "/usr/bin:/bin",
                },
                text=True,
            )

            self.assertEqual(result.returncode, 78)
            self.assertIn("unsupported Hindsight API version", result.stderr)
            self.assertFalse(capture.exists())

    def test_wrapper_rejects_a_colon_in_the_configured_uvx_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unsafe = Path(temporary) / "unsafe:path"
            unsafe.mkdir()
            fake_uvx = unsafe / "uvx"
            fake_uvx.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_uvx.chmod(0o700)

            result = subprocess.run(
                [str(WRAPPER), "hindsight-embed", "daemon", "status"],
                check=False,
                capture_output=True,
                env={
                    "HINDSIGHT_EMBED_NPX_EXECUTABLE": "/usr/bin/true",
                    "HINDSIGHT_EMBED_UVX_EXECUTABLE": str(fake_uvx),
                    "PATH": "/usr/bin:/bin",
                },
                text=True,
            )

            self.assertEqual(result.returncode, 69)
            self.assertIn("directory is not PATH-safe", result.stderr)

    def test_wrapper_requires_a_configured_npx(self) -> None:
        result = subprocess.run(
            [str(WRAPPER), "hindsight-embed", "daemon", "status"],
            check=False,
            capture_output=True,
            env={
                "HINDSIGHT_EMBED_UVX_EXECUTABLE": "/usr/bin/true",
                "PATH": "/usr/bin:/bin",
            },
            text=True,
        )

        self.assertEqual(result.returncode, 69)
        self.assertIn("HINDSIGHT_EMBED_NPX_EXECUTABLE", result.stderr)

    def test_wrapper_rejects_a_colon_in_the_configured_npx_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unsafe = Path(temporary) / "unsafe:path"
            unsafe.mkdir()
            fake_npx = unsafe / "npx"
            fake_npx.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_npx.chmod(0o700)

            result = subprocess.run(
                [str(WRAPPER), "hindsight-embed", "daemon", "status"],
                check=False,
                capture_output=True,
                env={
                    "HINDSIGHT_EMBED_NPX_EXECUTABLE": str(fake_npx),
                    "HINDSIGHT_EMBED_UVX_EXECUTABLE": "/usr/bin/true",
                    "PATH": "/usr/bin:/bin",
                },
                text=True,
            )

            self.assertEqual(result.returncode, 69)
            self.assertIn("npx directory is not PATH-safe", result.stderr)

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
            self.assertTrue(bindings, name)
            self.assertEqual(
                bindings,
                ["release://bin/hindsight-embed-uvx"] * len(bindings),
                name,
            )

    def test_fresh_install_commands_bind_both_package_runtimes(self) -> None:
        adoption = ADOPTION.read_text(encoding="utf-8")

        self.assertEqual(
            adoption.count(
                'HINDSIGHT_EMBED_UVX_EXECUTABLE="$uvx_executable" \\\n'
            ),
            5,
        )
        self.assertEqual(
            adoption.count(
                'HINDSIGHT_EMBED_NPX_EXECUTABLE="$npx_executable" \\\n'
            ),
            5,
        )


if __name__ == "__main__":
    unittest.main()
