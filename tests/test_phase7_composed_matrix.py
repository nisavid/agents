from __future__ import annotations

import base64
import copy
import hashlib
import inspect
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import phase7_v4_fixture as fixture

REPO_ROOT = Path(__file__).parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import private_phase7_evidence as public_evidence
import run_phase7_composed_matrix as runner
import validate_public_release as release_validator

COMPATIBILITY_BYTES = runner.compatibility_bytes(REPO_ROOT)
PUBLIC_IDENTITY = runner.candidate_identity(REPO_ROOT)


class Phase7ComposedMatrixTests(unittest.TestCase):
    def replay_summary(self, target: str = "macos-seatbelt") -> dict[str, object]:
        summary = fixture.replay_summary(COMPATIBILITY_BYTES)
        transient = summary["conformance"]
        opaque_conformance_sha256 = fixture.digest_bytes(
            fixture.canonical_bytes(transient)
        )
        summary["conformance"] = {
            "sha256": opaque_conformance_sha256,
            "sealed_root_count": len(transient["sealed_request_roots"]),
            "read_count": len(transient["read_inventory"]),
            "probe_count": len(transient["probe_results"]),
        }
        summary["runtime_isolation"]["conformance_sha256"] = fixture.digest_bytes(
            fixture.canonical_bytes(summary["conformance"])
        )
        summary["runtime_isolation"]["backend"] = {
            "macos-seatbelt": {
                "target": "macos-seatbelt",
                "binary": "sandbox-exec",
                "version": "macos-seatbelt-v1",
                "sha256": "sha256:8290e4be7387a0df83cd1559e86afd880464f269450573d012795761fe298f16",
                "version_sha256": fixture.digest_bytes(b"macos-seatbelt-version"),
            },
            "linux-bubblewrap": {
                "target": "linux-bubblewrap",
                "binary": "bwrap",
                "version": "bubblewrap-v1",
                "sha256": "sha256:85580dd52ed366ece8844e90fa75ac7c4de8802963071344e123221fb9f6f11e",
                "version_sha256": fixture.digest_bytes(b"linux-bubblewrap-version"),
            },
            "wsl2-bubblewrap": {
                "target": "wsl2-bubblewrap",
                "binary": "bwrap",
                "version": "bubblewrap-wsl2-v1",
                "sha256": fixture.digest_bytes(b"test-only-wsl2-binary"),
                "version_sha256": fixture.digest_bytes(b"wsl2-bubblewrap-version"),
            },
        }[target]
        summary["frozen_identity_sha256"] = fixture.frozen_identity_sha256(summary)
        unsigned = {
            key: value for key, value in summary.items() if key != "summary_sha256"
        }
        summary["summary_sha256"] = fixture.digest_bytes(
            fixture.canonical_bytes(unsigned)
        )
        return summary

    def run_composition(
        self,
        output: Path,
        *,
        summary: dict[str, object] | None = None,
    ) -> dict[str, object]:
        replay = summary or self.replay_summary()
        with (
            mock.patch.object(
                runner,
                "verify_private_evidence",
                return_value=replay,
            ) as verify,
            mock.patch.object(
                runner,
                "_run_public_family",
                side_effect=lambda identifier, public_root, environment: (
                    0,
                    f"{identifier}\n".encode(),
                    b"",
                ),
            ),
        ):
            receipt = runner.run(
                replay_summary_path=Path("/external/private-replay-summary.json"),
                producer_witness_path=Path("/external/private-producer-witness.tar"),
                producer_registry_path=Path("/external/private-producer-registry.json"),
                expected_frozen_identity_sha256=replay["frozen_identity_sha256"],
                expected_commit_oid=replay["private_commit_oid"],
                expected_producer_package_sha256=replay["producer_package_sha256"],
                public_root=REPO_ROOT,
                public_identity=PUBLIC_IDENTITY,
                output=output,
            )
        verify.assert_called_once()
        return receipt

    def test_v4_api_and_cli_reject_every_v3_private_input(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(runner.run).parameters),
            (
                "replay_summary_path",
                "producer_witness_path",
                "producer_registry_path",
                "expected_frozen_identity_sha256",
                "expected_commit_oid",
                "expected_producer_package_sha256",
                "public_root",
                "public_identity",
                "output",
            ),
        )
        help_result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/run_phase7_composed_matrix.py"),
                "--help",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for required in (
            "--private-replay-summary",
            "--private-producer-witness",
            "--private-producer-registry",
            "--expected-frozen-private-identity-sha256",
            "--expected-private-commit-oid",
            "--expected-private-producer-package-sha256",
            "--public-root",
            "--public-candidate-sha256",
            "--output",
        ):
            self.assertIn(required, help_result.stdout)
        for forbidden in (
            "--private-receipt",
            "--private-trust-anchor",
            "--private-remote-observation",
            "--private-evidence-bundle",
            "--frozen-private-identity",
            "--expected-private-producer-sha256",
            "--private-source-archive",
        ):
            self.assertNotIn(forbidden, help_result.stdout)

    def test_composition_emits_public_safe_v7_receipt(self) -> None:
        replay = self.replay_summary()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "output"
            receipt = self.run_composition(output, summary=replay)

        self.assertEqual(receipt["schema_version"], 7)
        self.assertEqual(receipt["contract"], "phase7-composed-evidence-v7")
        self.assertTrue(receipt["passed"])
        self.assertEqual(
            receipt["compatibility_sha256"],
            fixture.digest_bytes(COMPATIBILITY_BYTES),
        )
        self.assertEqual(
            receipt["private_commit_oid"],
            fixture.COMMIT_OID,
        )
        self.assertEqual(
            receipt["private_replay_payload_sha256"],
            fixture.replay_payload_sha256(replay),
        )
        self.assertEqual(
            [check["id"] for check in receipt["checks"]],
            list(fixture.CHECK_ORDER),
        )
        serialized = fixture.json_file_bytes(receipt)
        for forbidden in (
            b"/external/",
            b"private_root",
            b"stdout",
            b"stderr",
            b"command",
            b"argv",
            b"environment",
            b"timestamp",
            b"duration",
            b"pid",
            b"producer_bytes",
            COMPATIBILITY_BYTES,
        ):
            self.assertNotIn(forbidden, serialized)

    def test_composed_receipt_passes_the_public_validator_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "output"
            receipt = self.run_composition(output)
            stored = output / "phase7-composed-matrix.json"

            self.assertEqual(stored.read_bytes(), fixture.json_file_bytes(receipt))
            self.assertEqual(
                release_validator.validate_composed_receipt(REPO_ROOT, stored), receipt
            )

    def test_three_target_opaque_replay_composer_and_release_schemas_agree(
        self,
    ) -> None:
        targets = (
            "macos-seatbelt",
            "linux-bubblewrap",
            "wsl2-bubblewrap",
        )
        summaries = {target: self.replay_summary(target) for target in targets}
        proofs = []
        for target, summary in summaries.items():
            runtime = summary["runtime_isolation"]
            runtime["host_identity_sha256"] = fixture.digest_bytes(
                f"{target}-host".encode()
            )
            runtime["kernel_identity_sha256"] = fixture.digest_bytes(
                f"{target}-kernel".encode()
            )
            backend = runtime["backend"]
            proof = {
                "schema_version": 2,
                "contract": "phase7-public-terminal-direct-proof-v2",
                "target": target,
                "binary": backend["binary"],
                "version": backend["version"],
                "binary_sha256": backend["sha256"],
                "version_sha256": fixture.digest_bytes(f"{target}-version".encode()),
                "policy_sha256": runtime["policy_sha256"],
                "capability_manifest_sha256": runtime["capability_manifest_sha256"],
                "public_candidate_identity": PUBLIC_IDENTITY,
                "host_identity_sha256": runtime["host_identity_sha256"],
                "kernel_identity_sha256": runtime["kernel_identity_sha256"],
                "conformance_sha256": runtime["conformance_sha256"],
            }
            proof["terminal_direct_proof_sha256"] = fixture.digest_bytes(
                fixture.canonical_bytes(proof)
            )
            proofs.append(proof)
            summary["frozen_identity_sha256"] = fixture.frozen_identity_sha256(summary)
            unsigned = {
                key: value for key, value in summary.items() if key != "summary_sha256"
            }
            summary["summary_sha256"] = fixture.digest_bytes(
                fixture.canonical_bytes(unsigned)
            )
        backend_evidence = {
            "schema_version": 2,
            "contract": "phase7-public-backend-release-evidence-v2",
            "public_candidate_identity": PUBLIC_IDENTITY,
            "targets": proofs,
        }

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory).resolve()
            evidence_path = temporary / "backend-release-evidence.json"
            evidence_path.write_bytes(fixture.json_file_bytes(backend_evidence))
            evidence_sha256 = (
                "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            )
            validated_proofs = release_validator.validate_backend_release_evidence_path(
                REPO_ROOT,
                evidence_path,
                evidence_sha256,
            )

            for target, summary in summaries.items():
                with self.subTest(target=target):
                    output = temporary / target
                    validated_summary = public_evidence.validate_public_replay_summary(
                        fixture.json_file_bytes(summary)
                    )
                    receipt = self.run_composition(output, summary=validated_summary)

                    self.assertEqual(
                        release_validator.validate_composed_receipt(
                            REPO_ROOT, output / "phase7-composed-matrix.json"
                        ),
                        receipt,
                    )
                    release_validator.validate_runtime_backend_release_evidence(
                        receipt["runtime_isolation"],
                        validated_proofs,
                        public_candidate_identity=PUBLIC_IDENTITY,
                    )
                    self.assertEqual(
                        receipt["runtime_isolation"]["backend"]["target"], target
                    )
                    serialized = fixture.json_file_bytes(receipt)
                    self.assertNotIn(b"request-1", serialized)
                    self.assertNotIn(b"payload.txt", serialized)

    def test_public_receipt_keeps_private_conformance_names_opaque(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.run_composition(Path(directory).resolve() / "output")

        encoded = fixture.json_file_bytes(receipt)
        self.assertNotIn(b"request-1", encoded)
        self.assertNotIn(b"payload.txt", encoded)
        self.assertEqual(
            receipt["runtime_isolation"]["conformance_sha256"],
            fixture.digest_bytes(fixture.canonical_bytes(receipt["conformance"])),
        )

    def test_release_validator_rejects_resigned_opaque_conformance_counts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "output"
            receipt = self.run_composition(output)
            for field in ("sealed_root_count", "read_count", "probe_count"):
                receipt["conformance"][field] += 1
            unsigned = {
                key: value for key, value in receipt.items() if key != "receipt_sha256"
            }
            receipt["receipt_sha256"] = fixture.digest_bytes(
                fixture.canonical_bytes(unsigned)
            )
            stored = output / "phase7-composed-matrix.json"
            stored.write_bytes(fixture.json_file_bytes(receipt))

            with self.assertRaisesRegex(
                release_validator.ReleaseError,
                "conformance",
            ):
                release_validator.validate_composed_receipt(REPO_ROOT, stored)

    def test_composition_requires_exact_private_and_public_compatibility_bytes(
        self,
    ) -> None:
        summary = self.replay_summary()
        semantically_equal = copy.deepcopy(summary)
        compatibility = base64.b64decode(
            semantically_equal["compatibility_bytes_base64"], validate=True
        )
        semantically_equal["compatibility_bytes_base64"] = base64.b64encode(
            compatibility.rstrip(b"\n") + b" \n"
        ).decode("ascii")
        semantically_equal["compatibility_sha256"] = fixture.digest_bytes(
            compatibility.rstrip(b"\n") + b" \n"
        )
        unsigned = {
            key: value
            for key, value in semantically_equal.items()
            if key != "summary_sha256"
        }
        semantically_equal["summary_sha256"] = fixture.digest_bytes(
            fixture.canonical_bytes(unsigned)
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(
                runner.ComposedEvidenceError,
                "compatibility bytes",
            ),
        ):
            self.run_composition(
                Path(directory).resolve() / "output",
                summary=semantically_equal,
            )

    def test_receipt_freshness_does_not_require_host_equality(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "output"
            receipt = self.run_composition(output)
            with (
                mock.patch.object(
                    runner,
                    "verify_private_evidence",
                    return_value=self.replay_summary(),
                ),
            ):
                self.assertTrue(
                    runner.receipt_is_current(
                        receipt,
                        replay_summary_path=Path("/external/summary.json"),
                        producer_witness_path=Path("/external/witness.tar"),
                        producer_registry_path=Path("/external/registry.json"),
                        expected_frozen_identity_sha256=fixture.replay_summary(
                            COMPATIBILITY_BYTES
                        )["frozen_identity_sha256"],
                        expected_commit_oid=fixture.COMMIT_OID,
                        expected_producer_package_sha256=fixture.DIGEST_C,
                        public_root=REPO_ROOT,
                        public_identity=PUBLIC_IDENTITY,
                        output=output,
                    )
                )

    def test_composition_rejects_temporary_candidate_mutation_during_each_family(
        self,
    ) -> None:
        replay = self.replay_summary()
        public_identity = runner.candidate_identity(REPO_ROOT)
        for attacked_family in runner.FAMILY_IDS:
            with (
                self.subTest(attacked_family=attacked_family),
                tempfile.TemporaryDirectory() as directory,
            ):
                output = Path(directory).resolve() / "output"

                def mutate_then_restore(
                    identifier: str,
                    public_root: Path,
                    _environment: dict[str, str],
                    attacked_family: str = attacked_family,
                ) -> tuple[int, bytes, bytes]:
                    if identifier == attacked_family:
                        target = public_root / "README.md"
                        original = target.read_bytes()
                        target.write_bytes(original + b"\ntemporary drift\n")
                        target.write_bytes(original)
                    return 0, b"", b""

                with (
                    mock.patch.object(
                        runner,
                        "verify_private_evidence",
                        return_value=replay,
                    ),
                    mock.patch.object(
                        runner,
                        "_run_public_family",
                        side_effect=mutate_then_restore,
                    ),
                    self.assertRaises(runner.ComposedEvidenceError),
                ):
                    runner.run(
                        replay_summary_path=Path("/external/summary.json"),
                        producer_witness_path=Path("/external/witness.tar"),
                        producer_registry_path=Path("/external/registry.json"),
                        expected_frozen_identity_sha256=replay[
                            "frozen_identity_sha256"
                        ],
                        expected_commit_oid=replay["private_commit_oid"],
                        expected_producer_package_sha256=replay[
                            "producer_package_sha256"
                        ],
                        public_root=REPO_ROOT,
                        public_identity=public_identity,
                        output=output,
                    )


if __name__ == "__main__":
    unittest.main()
