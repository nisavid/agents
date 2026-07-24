from __future__ import annotations

import base64
import copy
import sys
import tempfile
import unittest
from pathlib import Path

from tests import phase7_v4_fixture as fixture

REPO_ROOT = Path(__file__).parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import private_phase7_evidence as evidence

COMPATIBILITY_BYTES = (
    REPO_ROOT / "tests/fixtures/phase7-v4-compatibility.json"
).read_bytes()


def opaque_replay_summary(compatibility: bytes) -> dict[str, object]:
    summary = fixture.replay_summary(compatibility)
    transient = summary["conformance"]
    opaque_conformance_sha256 = fixture.digest_bytes(fixture.canonical_bytes(transient))
    summary["conformance"] = {
        "sha256": opaque_conformance_sha256,
        "sealed_root_count": len(transient["sealed_request_roots"]),
        "read_count": len(transient["read_inventory"]),
        "probe_count": len(transient["probe_results"]),
    }
    summary["runtime_isolation"]["conformance_sha256"] = fixture.digest_bytes(
        fixture.canonical_bytes(summary["conformance"])
    )
    unsigned = {key: value for key, value in summary.items() if key != "summary_sha256"}
    summary["summary_sha256"] = fixture.digest_bytes(fixture.canonical_bytes(unsigned))
    return summary


class PublicReplaySummaryTests(unittest.TestCase):
    def test_accepts_exact_canonical_public_safe_summary(self) -> None:
        summary = opaque_replay_summary(COMPATIBILITY_BYTES)

        validated = evidence.validate_public_replay_summary(
            fixture.json_file_bytes(summary)
        )

        self.assertEqual(validated, summary)
        self.assertEqual(
            base64.b64decode(validated["compatibility_bytes_base64"]),
            COMPATIBILITY_BYTES,
        )

    def test_rejects_v3_and_every_forbidden_private_field(self) -> None:
        forbidden = (
            "private_root",
            "private_path",
            "filename",
            "stdout",
            "stderr",
            "command",
            "argv",
            "environment",
            "timestamp",
            "duration",
            "pid",
            "random_name",
            "private_payload",
            "producer_bytes",
        )
        for field in forbidden:
            with self.subTest(field=field):
                summary = opaque_replay_summary(COMPATIBILITY_BYTES)
                summary[field] = "forbidden"
                with self.assertRaises(evidence.PrivateEvidenceError):
                    evidence.validate_public_replay_summary(
                        fixture.json_file_bytes(summary)
                    )
        legacy = opaque_replay_summary(COMPATIBILITY_BYTES)
        legacy["schema_version"] = 3
        legacy["contract"] = "phase7-private-family-evidence-v3"
        with self.assertRaises(evidence.PrivateEvidenceError):
            evidence.validate_public_replay_summary(fixture.json_file_bytes(legacy))

    def test_rejects_noncanonical_or_semantically_substituted_bytes(self) -> None:
        summary = opaque_replay_summary(COMPATIBILITY_BYTES)
        pretty = __import__("json").dumps(summary, indent=2).encode("utf-8") + b"\n"
        with self.assertRaises(evidence.PrivateEvidenceError):
            evidence.validate_public_replay_summary(pretty)

        substituted = copy.deepcopy(summary)
        compatibility = base64.b64decode(
            substituted["compatibility_bytes_base64"], validate=True
        )
        substituted["compatibility_bytes_base64"] = base64.b64encode(
            compatibility.rstrip(b"\n") + b" \n"
        ).decode("ascii")
        unsigned = {
            key: value for key, value in substituted.items() if key != "summary_sha256"
        }
        substituted["summary_sha256"] = fixture.digest_bytes(
            fixture.canonical_bytes(unsigned)
        )
        with self.assertRaises(evidence.PrivateEvidenceError):
            evidence.validate_public_replay_summary(
                fixture.json_file_bytes(substituted)
            )

    def test_rejects_check_role_and_runtime_identity_drift(self) -> None:
        mutations = (
            lambda summary: summary["checks"][0].__setitem__("id", "changed-check"),
            lambda summary: summary["checks"][0].__setitem__("exit_code", True),
            lambda summary: summary["checks"][0].__setitem__("terminal", "failed"),
            lambda summary: summary["role_payloads"][0].__setitem__(
                "role", "changed-role"
            ),
            lambda summary: summary["runtime_isolation"]["backend"].__setitem__(
                "binary", "/usr/bin/sandbox-exec"
            ),
            lambda summary: summary["runtime_isolation"].__setitem__(
                "local_path", "/private/path"
            ),
        )
        for mutate in mutations:
            summary = opaque_replay_summary(COMPATIBILITY_BYTES)
            mutate(summary)
            unsigned = {
                key: value for key, value in summary.items() if key != "summary_sha256"
            }
            summary["summary_sha256"] = fixture.digest_bytes(
                fixture.canonical_bytes(unsigned)
            )
            with self.assertRaises(evidence.PrivateEvidenceError):
                evidence.validate_public_replay_summary(
                    fixture.json_file_bytes(summary)
                )


class PublicVerifierBoundaryTests(unittest.TestCase):
    def retained_v1_evidence(self, root: Path) -> dict[str, object]:
        root.mkdir(mode=0o700)
        sample = fixture.frozen_private_sample()
        registry_path = root / "private-producer-registry.json"
        witness_path = root / "private-producer-witness.tar"
        fixture.write_private(registry_path, sample["registry_bytes"])
        fixture.write_private(witness_path, sample["witness_bytes"])
        summary = opaque_replay_summary(COMPATIBILITY_BYTES)
        summary["private_commit_oid"] = sample["manifest"]["commit_oid"]
        summary["producer_package_sha256"] = sample["binding"][
            "producer_package_sha256"
        ]
        summary["producer_registry_sha256"] = fixture.digest_bytes(
            sample["registry_bytes"]
        )
        summary["producer_witness_sha256"] = fixture.digest_bytes(
            sample["witness_bytes"]
        )
        summary["frozen_identity_sha256"] = fixture.frozen_identity_sha256(summary)
        summary["summary_sha256"] = fixture.digest_bytes(
            fixture.canonical_bytes(
                {
                    key: value
                    for key, value in summary.items()
                    if key != "summary_sha256"
                }
            )
        )
        summary_path = root / "private-replay-public-summary.json"
        fixture.write_private(summary_path, fixture.json_file_bytes(summary))
        return {
            "summary": summary,
            "summary_path": summary_path,
            "registry_path": registry_path,
            "witness_path": witness_path,
            "commit_oid": sample["manifest"]["commit_oid"],
            "producer_package_sha256": sample["binding"]["producer_package_sha256"],
        }

    def build(self, root: Path) -> dict[str, object]:
        built = fixture.build_public_verifier_fixture(
            root,
            COMPATIBILITY_BYTES,
        )
        summary = opaque_replay_summary(
            COMPATIBILITY_BYTES,
        )
        original = built["summary"]
        for field in (
            "private_commit_oid",
            "producer_package_sha256",
            "producer_registry_sha256",
            "producer_witness_sha256",
            "checks",
        ):
            summary[field] = copy.deepcopy(original[field])
        backend = built["registry"]["backend_contracts"][0]
        summary["runtime_isolation"]["backend"] = {
            key: backend[key] for key in ("target", "binary", "version", "sha256")
        }
        summary["frozen_identity_sha256"] = fixture.frozen_identity_sha256(summary)
        summary["summary_sha256"] = fixture.digest_bytes(
            fixture.canonical_bytes(
                {
                    key: value
                    for key, value in summary.items()
                    if key != "summary_sha256"
                }
            )
        )
        built["summary"] = summary
        fixture.write_private(built["summary_path"], fixture.json_file_bytes(summary))
        return built

    def resign_summary(
        self,
        built: dict[str, object],
        *,
        field: str,
        digest: str,
    ) -> None:
        summary = built["summary"]
        summary[field] = digest
        unsigned = {
            key: value for key, value in summary.items() if key != "summary_sha256"
        }
        summary["summary_sha256"] = fixture.digest_bytes(
            fixture.canonical_bytes(unsigned)
        )
        fixture.write_private(
            built["summary_path"],
            fixture.json_file_bytes(summary),
        )

    def verify(self, built: dict[str, object]) -> dict[str, object]:
        summary = built["summary"]
        return evidence.verify_private_evidence(
            replay_summary_path=built["summary_path"],
            producer_witness_path=built["witness_path"],
            producer_registry_path=built["registry_path"],
            expected_frozen_identity_sha256=summary["frozen_identity_sha256"],
            expected_commit_oid=built["commit_oid"],
            expected_producer_package_sha256=built["producer_package_sha256"],
        )

    def test_retained_v4_registry_and_witness_are_explicitly_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            built = self.retained_v1_evidence(Path(directory).resolve() / "evidence")
            with self.assertRaisesRegex(
                evidence.PrivateEvidenceError,
                "private producer registry schema drift",
            ):
                self.verify(built)

    def test_rejects_digest_rebound_non_witness_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            built = self.retained_v1_evidence(Path(directory).resolve() / "evidence")
            fixture.write_private(built["witness_path"], b"not a witness tar")
            self.resign_summary(
                built,
                field="producer_witness_sha256",
                digest=fixture.digest_bytes(b"not a witness tar"),
            )
            with self.assertRaises(evidence.PrivateEvidenceError):
                self.verify(built)

    def test_rejects_digest_rebound_incomplete_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            built = self.build(Path(directory).resolve() / "evidence")
            registry = copy.deepcopy(built["registry"])
            registry.pop("roles")
            content = fixture.json_file_bytes(registry)
            fixture.write_private(built["registry_path"], content)
            self.resign_summary(
                built,
                field="producer_registry_sha256",
                digest=fixture.digest_bytes(content),
            )
            with self.assertRaises(evidence.PrivateEvidenceError):
                self.verify(built)

    def test_rejects_summary_resigned_after_frozen_runtime_field_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            built = self.build(Path(directory).resolve() / "evidence")
            summary = built["summary"]
            summary["runtime_isolation"]["conformance_sha256"] = "sha256:" + "f" * 64
            unsigned = {
                key: value for key, value in summary.items() if key != "summary_sha256"
            }
            summary["summary_sha256"] = fixture.digest_bytes(
                fixture.canonical_bytes(unsigned)
            )
            fixture.write_private(
                built["summary_path"], fixture.json_file_bytes(summary)
            )
            with self.assertRaises(evidence.PrivateEvidenceError):
                self.verify(built)

    def test_retained_v4_projection_mutation_stays_invalidated_at_registry_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            built = self.retained_v1_evidence(Path(directory).resolve() / "evidence")
            summary = built["summary"]
            changed = b'{"contract":"changed-projection"}\n'
            summary["compatibility_bytes_base64"] = base64.b64encode(changed).decode(
                "ascii"
            )
            summary["compatibility_sha256"] = fixture.digest_bytes(changed)
            summary["frozen_identity_sha256"] = fixture.frozen_identity_sha256(summary)
            unsigned = {
                key: value for key, value in summary.items() if key != "summary_sha256"
            }
            summary["summary_sha256"] = fixture.digest_bytes(
                fixture.canonical_bytes(unsigned)
            )
            fixture.write_private(
                built["summary_path"], fixture.json_file_bytes(summary)
            )

            with self.assertRaisesRegex(
                evidence.PrivateEvidenceError,
                "private producer registry schema drift",
            ):
                self.verify(built)

    def test_fixture_registry_and_witness_cover_real_private_source_inventory(
        self,
    ) -> None:
        expected_sources = {
            "scripts/build_phase7_private_evidence.py",
            "scripts/phase7_private_evidence_isolation.py",
            "scripts/phase7_private_evidence_producer.py",
            "scripts/phase7_private_evidence_registry.py",
            "scripts/phase7_private_evidence_test_launcher.py",
            "tests/agent_plugin_pin_fixtures.py",
            "tests/test_agent_topology.py",
            "tests/test_modify_private_config.py",
            "tests/test_claude_settings_modifier.py",
            "tests/test_agent_control_plane_hook.py",
            "tests/test_plugin_deployment.py",
            "tests/test_review_atlas_overlay.py",
        }
        with tempfile.TemporaryDirectory() as directory:
            built = self.build(Path(directory).resolve() / "evidence")

        registry_paths = {
            member["path"] for member in built["registry"]["package_members"]
        }
        witness_paths = set(built["witness_package_paths"])
        self.assertEqual(registry_paths, expected_sources)
        self.assertEqual(witness_paths, expected_sources)
        self.assertEqual(
            [
                (check["id"], tuple(check["tests"]))
                for check in built["registry"]["checks"]
            ],
            list(fixture.PRIVATE_CHECKS),
        )


if __name__ == "__main__":
    unittest.main()
