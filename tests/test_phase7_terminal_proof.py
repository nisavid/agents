from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_phase7_terminal_proof as proof


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


class TargetProofTests(unittest.TestCase):
    def receipt(self) -> dict:
        return {
            "schema_version": proof.run_phase7_composed_matrix.COMPOSED_SCHEMA_VERSION,
            "contract": proof.run_phase7_composed_matrix.COMPOSED_CONTRACT,
            "passed": True,
            "public_candidate_identity": "a" * 64,
            "runtime_isolation": {
                "backend": {
                    "target": "macos-seatbelt",
                    "binary": "sandbox-exec",
                    "version": "macos-seatbelt-v1",
                    "sha256": digest("binary"),
                    "version_sha256": digest("version"),
                },
                "policy_sha256": digest("policy"),
                "capability_manifest_sha256": digest("{}\n"),
                "host_identity_sha256": digest("host"),
                "kernel_identity_sha256": digest("kernel"),
                "conformance_sha256": digest("conformance"),
            },
        }

    def test_derives_a_strict_v2_proof(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "capability.json"
            manifest.write_text("{}\n", encoding="utf-8")
            result = proof.derive_target_proof(
                self.receipt(),
                expected_target="macos-seatbelt",
                capability_manifest=manifest,
            )
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["target"], "macos-seatbelt")
        self.assertEqual(result["capability_manifest_sha256"], digest("{}\n"))

    def test_rejects_a_composed_target_substitution(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "capability.json"
            manifest.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(proof.TerminalProofError, "target"):
                proof.derive_target_proof(
                    self.receipt(),
                    expected_target="linux-bubblewrap",
                    capability_manifest=manifest,
                )

    def test_rejects_a_failed_composed_receipt(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "capability.json"
            manifest.write_text("{}\n", encoding="utf-8")
            receipt = self.receipt()
            receipt["passed"] = False
            with self.assertRaisesRegex(proof.TerminalProofError, "did not pass"):
                proof.derive_target_proof(
                    receipt,
                    expected_target="macos-seatbelt",
                    capability_manifest=manifest,
                )

    def test_rejects_a_capability_manifest_swapped_after_composition(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "capability.json"
            manifest.write_text("swapped\n", encoding="utf-8")
            with self.assertRaisesRegex(
                proof.TerminalProofError, "capability manifest"
            ):
                proof.derive_target_proof(
                    self.receipt(),
                    expected_target="macos-seatbelt",
                    capability_manifest=manifest,
                )


if __name__ == "__main__":
    unittest.main()
