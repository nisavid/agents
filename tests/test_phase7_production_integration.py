from __future__ import annotations

import importlib.util
import inspect
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import phase7_v4_fixture as fixture

REPO_ROOT = Path(__file__).parents[1]
COORDINATOR = REPO_ROOT / "scripts/run_phase7_production_integration.py"


def load_coordinator():
    specification = importlib.util.spec_from_file_location(
        "phase7_production_integration",
        COORDINATOR,
    )
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class Phase7ProductionIntegrationTests(unittest.TestCase):
    def test_readme_documents_the_pinned_node_release_contract(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        for required in (
            "scripts/validate_public_release.py",
            "--node /absolute/path/to/physical/node",
            "fixed system search path",
            "resolved physical executable",
            "main executable bytes and version",
            "dynamic-loader or shared-library bytes",
            "undetectable ABA swap",
            "pathname-resolution boundary is not bound",
            "scripts/run_phase7_production_integration.py",
            "--node-executable /absolute/path/to/physical/node",
        ):
            self.assertIn(required, readme)

    def test_checked_in_coordinator_owns_the_exact_inherited_fd_pipeline(self) -> None:
        self.assertTrue(COORDINATOR.is_file())
        coordinator = load_coordinator()

        self.assertEqual(
            coordinator.PIPELINE_STAGES,
            (
                "immutable-private-builder",
                "private-summary-store",
                "public-private-verification",
                "public-composition",
                "production-release-validation",
            ),
        )
        source = inspect.getsource(coordinator.coordinate)
        for required_call in (
            "launch_private_builder",
            "verify_private_evidence",
            "run_phase7_composed_matrix",
            "validate_public_release",
        ):
            self.assertIn(required_call, source)

    def test_launcher_executes_the_builder_from_the_selected_commit_export(
        self,
    ) -> None:
        coordinator = load_coordinator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository = root / "private"
            builder = repository / "scripts/build_phase7_private_evidence.py"
            builder.parent.mkdir(parents=True)
            builder.write_text(
                "import os,pathlib,sys\n"
                "def value(name): return sys.argv[sys.argv.index(name)+1]\n"
                f"names={sorted(coordinator.PRIVATE_ARTIFACTS)!r}\n"
                "output=pathlib.Path(value('--output-directory')); output.mkdir()\n"
                "for name in names:\n"
                " p=output/name; p.write_bytes(b'fixture\\n'); p.chmod(0o600)\n"
                "os.write(int(value('--replay-summary-fd')), b'{}\\n')\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "user.name=test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "selected builder",
                ],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            builder.write_text("raise SystemExit(99)\n", encoding="utf-8")
            capability = root / "capability.json"
            capability.write_text("{}\n", encoding="utf-8")
            private_output = root / "private-output"
            private_summary = root / "private-summary.json"

            coordinator.launch_private_builder(
                private_repository=repository,
                private_commit_oid=commit,
                reviewed_producer_sha256="sha256:" + "a" * 64,
                public_root=REPO_ROOT,
                public_candidate_sha256="b" * 64,
                capability_manifest=capability,
                private_output=private_output,
                private_summary_output=private_summary,
            )

            self.assertEqual(private_summary.read_bytes(), b"{}\n")
            self.assertEqual(
                {path.name for path in private_output.iterdir()},
                coordinator.PRIVATE_ARTIFACTS,
            )

    def test_coordinator_reaches_public_verify_compose_and_release_validation(
        self,
    ) -> None:
        coordinator = load_coordinator()
        compatibility = (
            REPO_ROOT / "tests/fixtures/phase7-v4-compatibility.json"
        ).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            fixture_root = root / "fixture"
            built = fixture.build_public_verifier_fixture(
                fixture_root,
                compatibility,
            )
            transient = built["summary"]["conformance"]
            opaque_conformance_sha256 = fixture.digest_bytes(
                fixture.canonical_bytes(transient)
            )
            built["summary"]["conformance"] = {
                "sha256": opaque_conformance_sha256,
                "sealed_root_count": len(transient["sealed_request_roots"]),
                "read_count": len(transient["read_inventory"]),
                "probe_count": len(transient["probe_results"]),
            }
            built["summary"]["runtime_isolation"]["conformance_sha256"] = (
                fixture.digest_bytes(
                    fixture.canonical_bytes(built["summary"]["conformance"])
                )
            )
            unsigned = {
                key: value
                for key, value in built["summary"].items()
                if key != "summary_sha256"
            }
            built["summary"]["summary_sha256"] = fixture.digest_bytes(
                fixture.canonical_bytes(unsigned)
            )
            fixture.write_private(
                built["summary_path"], fixture.json_file_bytes(built["summary"])
            )
            private_output = root / "private-output"
            private_summary = root / "private-summary.json"
            composed_output = root / "composed"
            release_receipt = root / "release-receipt.json"
            public_identity = coordinator.candidate_content_identity(
                REPO_ROOT,
                error_factory=RuntimeError,
            )

            def launch(**_arguments):
                private_output.mkdir()
                shutil.copy2(
                    built["registry_path"],
                    private_output / "private-producer-registry.json",
                )
                shutil.copy2(
                    built["witness_path"],
                    private_output / "private-producer-witness.tar",
                )
                shutil.copy2(built["summary_path"], private_summary)
                for name in coordinator.PRIVATE_ARTIFACTS - {
                    "private-producer-registry.json",
                    "private-producer-witness.tar",
                    "coordinator-frozen-identity.json",
                }:
                    path = private_output / name
                    path.write_bytes(b"fixture\n")
                    path.chmod(0o600)
                frozen = private_output / "coordinator-frozen-identity.json"
                frozen.write_bytes(b"fixture\n")
                frozen.chmod(0o600)

            with (
                mock.patch.object(
                    coordinator,
                    "launch_private_builder",
                    side_effect=launch,
                ),
                mock.patch.object(coordinator, "require_canonical_capability_manifest"),
                mock.patch.object(
                    coordinator,
                    "_digest_file",
                    side_effect=lambda path: (
                        built["summary"]["producer_registry_sha256"]
                        if path.name == "private-producer-registry.json"
                        else built["summary"]["frozen_identity_sha256"]
                    ),
                ),
                mock.patch.object(
                    coordinator.run_phase7_composed_matrix,
                    "_run_public_family",
                    return_value=(0, b"", b""),
                ),
                mock.patch.object(
                    coordinator,
                    "verify_private_evidence",
                    return_value=built["summary"],
                ),
                mock.patch.object(
                    coordinator.run_phase7_composed_matrix,
                    "verify_private_evidence",
                    return_value=built["summary"],
                ),
                mock.patch.object(
                    coordinator.validate_public_release,
                    "validate_release",
                    return_value={"terminal": "passed"},
                ) as release,
            ):
                result = coordinator.coordinate(
                    private_repository=root,
                    private_commit_oid=built["commit_oid"],
                    reviewed_producer_sha256=built["summary"][
                        "producer_registry_sha256"
                    ],
                    public_root=REPO_ROOT,
                    public_candidate_sha256=public_identity,
                    capability_manifest=root,
                    private_output=private_output,
                    private_summary_output=private_summary,
                    composed_output=composed_output,
                    routing_evidence=root,
                    plugin_eval_executable=Path(sys.executable),
                    node_executable=Path("/safe/node"),
                    release_receipt_output=release_receipt,
                )

            self.assertEqual(result, {"terminal": "passed"})
            self.assertTrue((composed_output / "phase7-composed-matrix.json").is_file())
            release.assert_called_once()
            self.assertEqual(
                release.call_args.kwargs["node_executable"], Path("/safe/node")
            )

    def test_main_requires_and_forwards_the_explicit_node_executable(self) -> None:
        coordinator = load_coordinator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            capability_manifest = root / "capability-manifest.json"
            capability_manifest.write_text("{}\n", encoding="utf-8")
            plugin_eval = root / "plugin-eval.js"
            plugin_eval.write_text("fixture\n", encoding="utf-8")
            node = root / "node"
            node.write_text("fixture\n", encoding="utf-8")

            with mock.patch.object(coordinator, "coordinate") as coordinate:
                result = coordinator.main(
                    [
                        "--private-repository",
                        str(root),
                        "--private-commit-oid",
                        "a" * 40,
                        "--reviewed-producer-sha256",
                        "sha256:" + "b" * 64,
                        "--public-root",
                        str(root),
                        "--public-candidate-sha256",
                        "sha256:" + "c" * 64,
                        "--capability-manifest",
                        str(capability_manifest),
                        "--private-output",
                        str(root / "private-output"),
                        "--private-summary-output",
                        str(root / "private-summary.json"),
                        "--composed-output",
                        str(root / "composed-output"),
                        "--routing-evidence",
                        str(root),
                        "--plugin-eval-executable",
                        str(plugin_eval),
                        "--node-executable",
                        str(node),
                        "--release-receipt-output",
                        str(root / "release-receipt.json"),
                    ]
                )

            self.assertEqual(result, 0)
            coordinate.assert_called_once()
            self.assertEqual(coordinate.call_args.kwargs["node_executable"], node)


if __name__ == "__main__":
    unittest.main()
