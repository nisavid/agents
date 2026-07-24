from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import phase7_private_evidence_isolation as isolation
import phase7_private_evidence_producer as producer
import private_phase7_evidence as public_evidence
import run_phase7_production_integration as coordinator

from tests import phase7_v4_fixture as fixture


def opaque_replay_summary() -> dict[str, object]:
    compatibility = public_evidence.compatibility_bytes(REPO_ROOT)
    summary = copy.deepcopy(fixture.replay_summary(compatibility))
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


def fixture_digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def backend_release_evidence() -> dict[str, object]:
    targets = [
        {
            "schema_version": 2,
            "contract": "phase7-public-terminal-direct-proof-v2",
            "target": "macos-seatbelt",
            "binary": "sandbox-exec",
            "version": "macos-seatbelt-v1",
            "binary_sha256": "sha256:8290e4be7387a0df83cd1559e86afd880464f269450573d012795761fe298f16",
            "version_sha256": fixture_digest("macos-version"),
            "policy_sha256": fixture_digest("macos-policy"),
            "capability_manifest_sha256": fixture_digest("macos-capability"),
            "public_candidate_identity": "a" * 64,
            "host_identity_sha256": fixture_digest("macos-host"),
            "kernel_identity_sha256": fixture_digest("macos-kernel"),
            "conformance_sha256": fixture_digest("macos-conformance"),
        },
        {
            "schema_version": 2,
            "contract": "phase7-public-terminal-direct-proof-v2",
            "target": "linux-bubblewrap",
            "binary": "bwrap",
            "version": "bubblewrap-v1",
            "binary_sha256": "sha256:85580dd52ed366ece8844e90fa75ac7c4de8802963071344e123221fb9f6f11e",
            "version_sha256": "sha256:9d3b32565ddaece919cfc7d8fed50f5fe2a9ac9529cfbe9067c5eda7ccf0c530",
            "policy_sha256": fixture_digest("linux-policy"),
            "capability_manifest_sha256": fixture_digest("linux-capability"),
            "public_candidate_identity": "a" * 64,
            "host_identity_sha256": fixture_digest("non-final-linux-host"),
            "kernel_identity_sha256": fixture_digest("non-final-linux-kernel"),
            "conformance_sha256": fixture_digest("non-final-linux-conformance"),
        },
        {
            "schema_version": 2,
            "contract": "phase7-public-terminal-direct-proof-v2",
            "target": "wsl2-bubblewrap",
            "binary": "bwrap",
            "version": "bubblewrap-wsl2-v1",
            "binary_sha256": fixture_digest("test-only-wsl2-binary"),
            "version_sha256": fixture_digest("test-only-wsl2-version"),
            "policy_sha256": fixture_digest("wsl2-policy"),
            "capability_manifest_sha256": fixture_digest("wsl2-capability"),
            "public_candidate_identity": "a" * 64,
            "host_identity_sha256": fixture_digest("test-only-wsl2-host"),
            "kernel_identity_sha256": fixture_digest("test-only-wsl2-kernel"),
            "conformance_sha256": fixture_digest("test-only-wsl2-conformance"),
        },
    ]
    for target in targets:
        target["terminal_direct_proof_sha256"] = fixture.digest_bytes(
            fixture.canonical_bytes(target)
        )
    return {
        "schema_version": 2,
        "contract": "phase7-public-backend-release-evidence-v2",
        "public_candidate_identity": "a" * 64,
        "targets": targets,
    }


class CapabilityTreeSealTests(unittest.TestCase):
    def build_root(self, root: Path) -> Path:
        root.mkdir(mode=0o700)
        nested = root / "nested"
        nested.mkdir(mode=0o750)
        payload = nested / "payload.txt"
        payload.write_bytes(b"sealed payload\n")
        payload.chmod(0o640)
        return root

    def test_rejects_empty_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "empty"
            root.mkdir()
            with self.assertRaisesRegex(isolation.IsolationError, "empty"):
                isolation.seal_capability_tree(root)

    def test_binds_directory_and_file_modes_and_detects_mode_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.build_root(Path(directory).resolve() / "request")
            seal = isolation.seal_capability_tree(root)
            self.assertEqual(
                [entry["path"] for entry in seal["entries"]],
                [".", "nested", "nested/payload.txt"],
            )
            self.assertEqual(seal["entries"][1]["mode"], "0750")
            self.assertEqual(seal["entries"][2]["mode"], "0640")

            os.chmod(root / "nested", 0o700)
            with self.assertRaisesRegex(isolation.IsolationError, "changed"):
                isolation.validate_capability_tree(root, seal)

    def test_rejects_symlinked_ancestor_and_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory).resolve()
            target = self.build_root(temporary / "target")
            ancestor = temporary / "ancestor"
            ancestor.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(isolation.IsolationError, "symlink"):
                isolation.seal_capability_tree(ancestor)

            root = self.build_root(temporary / "request")
            (root / "nested" / "link").symlink_to("payload.txt")
            with self.assertRaisesRegex(isolation.IsolationError, "symlink"):
                isolation.seal_capability_tree(root)

    def test_detects_content_and_path_races_after_sealing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.build_root(Path(directory).resolve() / "request")
            seal = isolation.seal_capability_tree(root)

            payload = root / "nested" / "payload.txt"
            payload.write_bytes(b"replaced content\n")
            with self.assertRaisesRegex(isolation.IsolationError, "changed"):
                isolation.validate_capability_tree(root, seal)
            payload.write_bytes(b"sealed payload\n")
            payload.rename(root / "nested" / "renamed.txt")
            with self.assertRaisesRegex(isolation.IsolationError, "changed"):
                isolation.validate_capability_tree(root, seal)

    def test_rejects_relative_root_before_any_traversal(self) -> None:
        with self.assertRaisesRegex(isolation.IsolationError, "absolute"):
            isolation.seal_capability_tree(Path("relative-request"))

    def test_detects_content_mutated_after_its_descriptor_was_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "request"
            root.mkdir()
            first = root / "a.txt"
            first.write_bytes(b"old bytes\n")
            (root / "b.txt").write_bytes(b"later bytes\n")
            original_read = isolation._read_regular_file

            def mutate_after_first(parent_fd: int, name: str, *arguments):
                observed = original_read(parent_fd, name, *arguments)
                if name == "a.txt":
                    first.write_bytes(b"new bytes\n")
                return observed

            with (
                mock.patch.object(
                    isolation, "_read_regular_file", side_effect=mutate_after_first
                ),
                self.assertRaisesRegex(isolation.IsolationError, "changed"),
            ):
                isolation.seal_capability_tree(root)

    def test_logical_root_does_not_change_the_physical_seal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.build_root(Path(directory).resolve() / "physical-name")
            seal = isolation.seal_capability_tree(root)
            seal["root"] = "request-1"

            isolation.validate_capability_tree(root, seal)


class ConformanceInventoryTests(unittest.TestCase):
    def test_inventory_is_derived_from_the_sealed_root_not_probe_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "request"
            root.mkdir()
            (root / "first.txt").write_text("first\n", encoding="utf-8")
            (root / "second.txt").write_text("second\n", encoding="utf-8")
            seal = isolation.seal_capability_tree(root)
            conformance = producer.build_conformance_inventory([seal])

            self.assertEqual(
                conformance["read_inventory"],
                [
                    {"root": seal["root"], "path": "first.txt"},
                    {"root": seal["root"], "path": "second.txt"},
                ],
            )
            with self.assertRaisesRegex(producer.ProducerError, "inventory"):
                producer.validate_probe_results(conformance, probes=[])

    def test_transient_conformance_projects_to_an_opaque_public_summary(self) -> None:
        transient = fixture.replay_summary(
            public_evidence.compatibility_bytes(REPO_ROOT)
        )["conformance"]
        projected = producer.build_public_conformance_summary(transient)

        self.assertEqual(
            projected,
            {
                "sha256": fixture.digest_bytes(fixture.canonical_bytes(transient)),
                "sealed_root_count": 1,
                "read_count": 1,
                "probe_count": 1,
            },
        )
        encoded = fixture.json_file_bytes(projected)
        self.assertNotIn(b"request-1", encoded)
        self.assertNotIn(b"payload.txt", encoded)

    def test_public_summary_rejects_named_or_array_conformance(self) -> None:
        summary = opaque_replay_summary()
        summary["conformance"] = {
            "sealed_request_roots": [{"root": "secret-request"}],
            "read_inventory": [{"path": "secret.txt"}],
            "probe_results": [],
        }
        unsigned = {
            key: value for key, value in summary.items() if key != "summary_sha256"
        }
        summary["summary_sha256"] = fixture.digest_bytes(
            fixture.canonical_bytes(unsigned)
        )
        with self.assertRaisesRegex(
            public_evidence.PrivateEvidenceError, "conformance.*schema drift"
        ):
            public_evidence.validate_public_replay_summary(
                fixture.json_file_bytes(summary)
            )

    def test_public_summary_rejects_missing_resigned_counts_and_digest(self) -> None:
        mutations = (
            lambda summary: summary["conformance"].pop("probe_count"),
            lambda summary: summary["conformance"].__setitem__("probe_count", 0),
            lambda summary: summary["conformance"].__setitem__(
                "probe_count", summary["conformance"]["read_count"] + 1
            ),
            lambda summary: summary["conformance"].update(
                {
                    "sealed_root_count": summary["conformance"]["sealed_root_count"]
                    + 1,
                    "read_count": summary["conformance"]["read_count"] + 1,
                    "probe_count": summary["conformance"]["probe_count"] + 1,
                }
            ),
            lambda summary: summary["conformance"].__setitem__(
                "sha256", "sha256:" + "f" * 64
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                summary = opaque_replay_summary()
                mutation(summary)
                unsigned = {
                    key: value
                    for key, value in summary.items()
                    if key != "summary_sha256"
                }
                summary["summary_sha256"] = fixture.digest_bytes(
                    fixture.canonical_bytes(unsigned)
                )
                with self.assertRaisesRegex(
                    public_evidence.PrivateEvidenceError, "conformance"
                ):
                    public_evidence.validate_public_replay_summary(
                        fixture.json_file_bytes(summary)
                    )

    def test_public_coordinator_rejects_legacy_capability_schema_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manifest = root / "capability.json"
            manifest.write_text(
                '{"backend_target":"macos-seatbelt","capabilities":[],"request_roots":[]}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                coordinator.IntegrationError, "canonical v2"
            ):
                coordinator.require_canonical_capability_manifest(manifest)

    def test_public_coordinator_accepts_only_canonical_transport_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory).resolve()
            manifest = temporary / "capability.json"
            manifest.write_bytes(
                b'{"contract":"phase7-readonly-capabilities-v2","expected_backend":{"backend":"seatbelt","binary_sha256":"sha256:'
                + b"a" * 64
                + b'","version_sha256":"sha256:'
                + b"b" * 64
                + b'"},"roots":[],"schema_version":2}\n'
            )
            coordinator.require_canonical_capability_manifest(manifest)


class BackendAndPublishContractTests(unittest.TestCase):
    def test_independent_backend_contract_covers_all_release_targets(self) -> None:
        contracts = isolation.load_backend_contracts(
            REPO_ROOT / "scripts/phase7_private_evidence_backend_contracts.json"
        )
        self.assertEqual(
            tuple(contract["target"] for contract in contracts),
            ("macos-seatbelt", "linux-bubblewrap", "wsl2-bubblewrap"),
        )
        self.assertEqual(
            tuple(contract["execution"] for contract in contracts),
            (
                "direct-required",
                "external-release-required",
                "external-release-required",
            ),
        )
        self.assertTrue(all(contract["capabilities"] for contract in contracts))
        self.assertFalse(hasattr(isolation, "expected_backend"))
        self.assertTrue(
            all("expected_backend" not in contract for contract in contracts)
        )

    def test_contract_rejects_target_binary_and_placeholder_hash_mismatches(
        self,
    ) -> None:
        contract_path = (
            REPO_ROOT / "scripts/phase7_private_evidence_backend_contracts.json"
        )
        document = json.loads(contract_path.read_text(encoding="utf-8"))
        document["targets"][0]["binary"] = "bwrap"
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "contracts.json"
            candidate.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(isolation.IsolationError, "contract drift"):
                isolation.load_backend_contracts(candidate)

        document = json.loads(contract_path.read_text(encoding="utf-8"))
        document["targets"][0]["sha256"] = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "contracts.json"
            candidate.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(isolation.IsolationError, "contract drift"):
                isolation.load_backend_contracts(candidate)

    def test_private_registry_backend_schema_is_target_sensitive(self) -> None:
        contracts = isolation.load_backend_contracts(
            REPO_ROOT / "scripts/phase7_private_evidence_backend_contracts.json"
        )
        public_evidence._validate_backend_contracts(contracts)
        self.assertIn("sha256", contracts[0])
        self.assertNotIn("sha256", contracts[1])
        self.assertNotIn("sha256", contracts[2])

        mutations = (
            lambda value: value[0].pop("sha256"),
            lambda value: value[1].__setitem__(
                "sha256", fixture_digest("self-certified-linux")
            ),
            lambda value: value[2].__setitem__(
                "sha256", fixture_digest("self-certified-wsl2")
            ),
            lambda value: value[1].__setitem__("target", "wsl2-bubblewrap"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(contracts)
                mutation(changed)
                with self.assertRaisesRegex(
                    public_evidence.PrivateEvidenceError,
                    "backend contract",
                ):
                    public_evidence._validate_backend_contracts(changed)

    def test_replay_binding_accepts_each_target_sensitive_backend_contract(
        self,
    ) -> None:
        contracts = isolation.load_backend_contracts(
            REPO_ROOT / "scripts/phase7_private_evidence_backend_contracts.json"
        )
        summary = opaque_replay_summary()
        registry_checks = []
        for summary_check in summary["checks"]:
            tests = [f"tests/{summary_check['id']}.py"]
            summary_check["test_inventory_sha256"] = fixture.digest_bytes(
                fixture.canonical_bytes(tests)
            )
            registry_checks.append(
                {
                    "id": summary_check["id"],
                    "tests": tests,
                    "registry_entry_sha256": summary_check["registry_entry_sha256"],
                }
            )
        registry = {
            "checks": registry_checks,
            "backend_contracts": contracts,
        }
        runtime_hashes = {
            "macos-seatbelt": contracts[0]["sha256"],
            "linux-bubblewrap": "sha256:85580dd52ed366ece8844e90fa75ac7c4de8802963071344e123221fb9f6f11e",
            "wsl2-bubblewrap": fixture_digest("test-only-wsl2-binary"),
        }

        for contract in contracts:
            with self.subTest(target=contract["target"]):
                candidate = copy.deepcopy(summary)
                candidate["runtime_isolation"]["backend"] = {
                    "target": contract["target"],
                    "binary": contract["binary"],
                    "version": contract["version"],
                    "sha256": runtime_hashes[contract["target"]],
                }
                public_evidence._validate_summary_registry_binding(candidate, registry)

    def test_external_release_evidence_binds_target_binary_and_frozen_bytes(
        self,
    ) -> None:
        evidence = backend_release_evidence()
        encoded = json.dumps(evidence, sort_keys=True).encode("utf-8")
        expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
        proofs = isolation.validate_public_release_backend_evidence(
            encoded, expected_sha256=expected
        )
        for proof in proofs:
            runtime = {
                "backend": {
                    "target": proof["target"],
                    "binary": proof["binary"],
                    "version": proof["version"],
                    "sha256": proof["binary_sha256"],
                    "version_sha256": proof["version_sha256"],
                },
                "policy_sha256": proof["policy_sha256"],
                "capability_manifest_sha256": proof["capability_manifest_sha256"],
                "host_identity_sha256": proof["host_identity_sha256"],
                "kernel_identity_sha256": proof["kernel_identity_sha256"],
                "conformance_sha256": proof["conformance_sha256"],
            }
            isolation.validate_runtime_backend_release_evidence(
                runtime, proofs, public_candidate_identity="a" * 64
            )

        evidence["targets"][0]["binary"] = "bwrap"
        mismatched = json.dumps(evidence, sort_keys=True).encode("utf-8")
        with self.assertRaisesRegex(isolation.IsolationError, "identity mismatch"):
            isolation.validate_public_release_backend_evidence(
                mismatched, expected_sha256=expected
            )

        evidence = backend_release_evidence()
        evidence["targets"][1]["host_identity_sha256"] = fixture_digest(
            "substituted-linux-host"
        )
        resigned = json.dumps(evidence, sort_keys=True).encode("utf-8")
        with self.assertRaisesRegex(isolation.IsolationError, "terminal proof"):
            isolation.validate_public_release_backend_evidence(
                resigned,
                expected_sha256="sha256:" + hashlib.sha256(resigned).hexdigest(),
            )

        proofs[1]["target"] = "wsl2-bubblewrap"
        runtime = {
            "backend": {
                "target": "linux-bubblewrap",
                "binary": "bwrap",
                "version": "bubblewrap-v1",
                "sha256": proofs[1]["binary_sha256"],
                "version_sha256": proofs[1]["version_sha256"],
            },
            "policy_sha256": proofs[1]["policy_sha256"],
            "capability_manifest_sha256": proofs[1]["capability_manifest_sha256"],
            "host_identity_sha256": proofs[1]["host_identity_sha256"],
            "kernel_identity_sha256": proofs[1]["kernel_identity_sha256"],
            "conformance_sha256": proofs[1]["conformance_sha256"],
        }
        with self.assertRaisesRegex(isolation.IsolationError, "target"):
            isolation.validate_runtime_backend_release_evidence(
                runtime, proofs, public_candidate_identity="a" * 64
            )

    def test_renameat2_numbers_are_arch_specific_and_unknown_fails_closed(self) -> None:
        self.assertEqual(isolation.renameat2_syscall_number("arm64"), 276)
        self.assertEqual(isolation.renameat2_syscall_number("riscv64"), 276)
        with self.assertRaisesRegex(isolation.IsolationError, "unsupported"):
            isolation.renameat2_syscall_number("mips64")


class GitTreeReuseTests(unittest.TestCase):
    def test_repeated_tree_object_is_reused_without_a_cycle_failure(self) -> None:
        object_format = "sha1"
        subtree = b""
        subtree_oid = public_evidence._git_oid(object_format, "tree", subtree)
        root = (
            b"40000 first\0"
            + bytes.fromhex(subtree_oid)
            + b"40000 second\0"
            + bytes.fromhex(subtree_oid)
        )
        root_oid = public_evidence._git_oid(object_format, "tree", root)
        commit = f"tree {root_oid}\n\nreused subtree\n".encode("ascii")
        commit_oid = public_evidence._git_oid(object_format, "commit", commit)
        registry_sha256 = "sha256:" + "a" * 64
        package_sha256 = "sha256:" + "b" * 64
        manifest = {
            "schema_version": 1,
            "contract": public_evidence.COMPLETE_TREE_WITNESS_CONTRACT,
            "commit_oid": commit_oid,
            "root_tree_oid": root_oid,
            "registry_sha256": registry_sha256,
            "producer_package_sha256": package_sha256,
            "trees": [
                {
                    "oid": root_oid,
                    "sha256": fixture.digest_bytes(root),
                },
                {
                    "oid": subtree_oid,
                    "sha256": fixture.digest_bytes(subtree),
                },
            ],
        }
        witness = fixture.deterministic_tar(
            [
                ("manifest.json", fixture.json_file_bytes(manifest)),
                (f"objects/{commit_oid}", commit),
                (f"objects/{root_oid}", root),
                (f"objects/{subtree_oid}", subtree),
            ]
        )

        public_evidence._validate_witness(
            witness,
            expected_commit_oid=commit_oid,
            expected_registry_sha256=registry_sha256,
            expected_producer_package_sha256=package_sha256,
            package_members=[],
        )

    def test_repeated_nonempty_tree_materializes_package_members_at_each_prefix(
        self,
    ) -> None:
        object_format = "sha1"
        blob = b"producer\n"
        blob_oid = public_evidence._git_oid(object_format, "blob", blob)
        subtree = b"100644 producer.py\0" + bytes.fromhex(blob_oid)
        subtree_oid = public_evidence._git_oid(object_format, "tree", subtree)
        root = (
            b"40000 first\0"
            + bytes.fromhex(subtree_oid)
            + b"40000 second\0"
            + bytes.fromhex(subtree_oid)
        )
        root_oid = public_evidence._git_oid(object_format, "tree", root)
        commit = f"tree {root_oid}\n\nreused subtree\n".encode("ascii")
        commit_oid = public_evidence._git_oid(object_format, "commit", commit)
        registry_sha256 = "sha256:" + "a" * 64
        package_sha256 = "sha256:" + "b" * 64
        manifest = {
            "schema_version": 1,
            "contract": public_evidence.COMPLETE_TREE_WITNESS_CONTRACT,
            "commit_oid": commit_oid,
            "root_tree_oid": root_oid,
            "registry_sha256": registry_sha256,
            "producer_package_sha256": package_sha256,
            "trees": [
                {"oid": root_oid, "sha256": fixture.digest_bytes(root)},
                {"oid": subtree_oid, "sha256": fixture.digest_bytes(subtree)},
            ],
        }
        witness = fixture.deterministic_tar(
            [
                ("manifest.json", fixture.json_file_bytes(manifest)),
                (f"objects/{commit_oid}", commit),
                (f"objects/{root_oid}", root),
                (f"objects/{subtree_oid}", subtree),
            ]
        )
        members = [
            {
                "path": f"{prefix}/producer.py",
                "mode": "100644",
                "blob_oid": blob_oid,
                "size": len(blob),
                "sha256": fixture.digest_bytes(blob),
            }
            for prefix in ("first", "second")
        ]

        public_evidence._validate_witness(
            witness,
            expected_commit_oid=commit_oid,
            expected_registry_sha256=registry_sha256,
            expected_producer_package_sha256=package_sha256,
            package_members=members,
        )


if __name__ == "__main__":
    unittest.main()
