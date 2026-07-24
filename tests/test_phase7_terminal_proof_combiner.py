from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import combine_phase7_terminal_proofs as combiner
from evidence_transport import canonical_bytes, json_file_bytes


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def proof(target: str, binary: str) -> dict:
    result = {
        "schema_version": 2,
        "contract": "phase7-public-terminal-direct-proof-v2",
        "target": target,
        "binary": binary,
        "version": f"{target}-v1",
        "binary_sha256": digest(f"{target}-binary"),
        "version_sha256": digest(f"{target}-version"),
        "policy_sha256": digest(f"{target}-policy"),
        "capability_manifest_sha256": digest(f"{target}-capability"),
        "public_candidate_identity": "a" * 64,
        "host_identity_sha256": digest(f"{target}-host"),
        "kernel_identity_sha256": digest(f"{target}-kernel"),
        "conformance_sha256": digest(f"{target}-conformance"),
    }
    result["terminal_direct_proof_sha256"] = (
        "sha256:" + hashlib.sha256(canonical_bytes(result)).hexdigest()
    )
    return result


class CombineTerminalProofsTests(unittest.TestCase):
    def write(self, root: Path, index: int, value: dict) -> Path:
        path = root / f"{index}.json"
        path.write_bytes(json_file_bytes(value))
        return path

    def test_requires_exact_target_order_and_unique_terminal_identities(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            root.chmod(0o700)
            paths = [
                self.write(root, index, proof(target, binary))
                for index, (target, binary) in enumerate(combiner.TARGETS)
            ]
            result = combiner.combine(paths, root / "combined.json")
            self.assertEqual(
                result["contract"], "phase7-public-backend-release-evidence-v2"
            )
            self.assertEqual(result, combiner.combine(paths, root / "combined.json"))
            altered = proof(*combiner.TARGETS[0])
            altered["version"] = "altered-v1"
            unsigned = {
                field: value
                for field, value in altered.items()
                if field != "terminal_direct_proof_sha256"
            }
            altered["terminal_direct_proof_sha256"] = (
                "sha256:" + hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
            )
            paths[0].write_bytes(json_file_bytes(altered))
            with self.assertRaisesRegex(combiner.CombineError, "frozen evidence"):
                combiner.combine(paths, root / "combined.json")
            with self.assertRaisesRegex(combiner.CombineError, "schema|target"):
                combiner.combine([paths[1], paths[0], paths[2]], root / "other.json")


if __name__ == "__main__":
    unittest.main()
