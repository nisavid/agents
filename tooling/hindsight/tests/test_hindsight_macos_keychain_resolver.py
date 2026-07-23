from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "native/macos-keychain-resolver/build.zsh"
BINARY = ROOT / "bin/hindsight-keychain-resolver"


@unittest.skipUnless(sys.platform == "darwin", "macOS Keychain contract")
class MacOSKeychainResolverTest(unittest.TestCase):
    def test_bundled_artifact_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "resolver-1"
            second = Path(temporary) / "resolver-2"
            developer_dir = subprocess.run(
                ["/usr/bin/xcode-select", "-p"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
                timeout=15,
            ).stdout.strip()
            sdk_version = subprocess.run(
                ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
                timeout=15,
            ).stdout.strip()
            build_environment = {
                **os.environ,
                "DEVELOPER_DIR": developer_dir,
                "HINDSIGHT_MACOS_SDK_VERSION": sdk_version,
            }
            subprocess.run(
                [BUILD, first],
                check=True,
                env=build_environment,
                timeout=60,
            )
            subprocess.run(
                [BUILD, second],
                check=True,
                env=build_environment,
                timeout=60,
            )
            subprocess.run(
                [
                    "/usr/bin/lipo",
                    first,
                    "-verify_arch",
                    "arm64",
                    "x86_64",
                ],
                check=True,
                timeout=15,
            )

            first_bytes = first.read_bytes()
            self.assertEqual(first_bytes, second.read_bytes())
            self.assertEqual(
                hashlib.sha256(first_bytes).hexdigest(),
                hashlib.sha256(BINARY.read_bytes()).hexdigest(),
            )

    def test_real_keychain_acl_denies_python_and_rejects_broad_acl(self) -> None:
        completed = subprocess.run(
            [BINARY, "--self-test-acl"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        if (
            completed.returncode != 0
            and "Keychain ACL setup status: 100001" in completed.stderr
        ):
            self.skipTest("Keychain mutation is unavailable in this sandbox")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "foreign_acl_rejected": True,
                "native_read": True,
                "python_denied": True,
                "retirement_works": True,
                "schema_version": 1,
                "separate_any_acl_rejected": True,
            },
        )
        self.assertEqual(completed.stderr, "")

    def test_request_protocol_rejects_duplicate_keys(self) -> None:
        completed = subprocess.run(
            [BINARY],
            input=(
                '{"credentials":[{"environment":"HINDSIGHT_DATA_PLANE_TOKEN",'
                '"locator":"keychain://io.nisavid.hindsight/data-plane"}],'
                '"schema_version":1,'
                '"schema_version":1}'
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "credential request is not canonical\n",
        )

    def test_request_protocol_rejects_fractional_schema_version(self) -> None:
        completed = subprocess.run(
            [BINARY],
            input=(
                '{"credentials":[{"environment":"HINDSIGHT_DATA_PLANE_TOKEN",'
                '"locator":"keychain://io.nisavid.hindsight/data-plane"}],'
                '"schema_version":1.9}'
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "credential request is invalid\n")


if __name__ == "__main__":
    unittest.main()
