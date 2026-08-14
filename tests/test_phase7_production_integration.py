from __future__ import annotations

import hashlib
import importlib.util
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


def install_clean_public_candidate(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    for command in (
        ["git", "init", "--quiet", str(destination)],
        [
            "git",
            "-C",
            str(destination),
            "remote",
            "add",
            "origin",
            "https://github.com/nisavid/agents",
        ],
        ["git", "-C", str(destination), "add", "--force", "--all"],
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "clean public candidate",
        ],
    ):
        subprocess.run(command, check=True, capture_output=True)


class Phase7ProductionIntegrationTests(unittest.TestCase):
    def test_readme_documents_the_pinned_node_release_contract(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        for required in (
            "scripts/run_prepared_release_validation.sh",
            "public-release",
            "phase7-production",
            "/absolute/path/to/qualified/cpython",
            "--node /absolute/path/to/physical/node",
            "fixed system search path",
            "resolved physical executable",
            "main executable bytes and version",
            "dynamic-loader or shared-library bytes",
            "undetectable ABA swap",
            "pathname-resolution boundary is not bound",
            "--node-executable /absolute/path/to/physical/node",
            "--expected-public-candidate-sha256 '<bare-64-hex-digest>'",
            "--public-candidate-sha256 '<bare-64-hex-digest>'",
        ):
            self.assertIn(required, readme)
        self.assertEqual(readme.count("run_prepared_release_validation.sh"), 2)
        self.assertNotIn("scripts/run_phase7_production_integration.py", readme)
        self.assertNotIn("uv --no-config run", readme)
        self.assertNotIn("uv run --with PyYAML --with pytest", readme)
        self.assertNotIn("--expected-public-candidate-sha256 'sha256:<digest>'", readme)
        self.assertNotIn("--public-candidate-sha256 'sha256:<digest>'", readme)

    def test_public_candidate_identity_uses_bare_lowercase_sha256(self) -> None:
        coordinator = load_coordinator()
        bare = "a" * 64

        self.assertEqual(coordinator.parse_public_candidate_sha256(bare), bare)
        for malformed in (
            "sha256:" + bare,
            "A" * 64,
            "a" * 63,
            "a" * 65,
        ):
            with (
                self.subTest(malformed=malformed),
                self.assertRaises(coordinator.argparse.ArgumentTypeError),
            ):
                coordinator.parse_public_candidate_sha256(malformed)
        with self.assertRaisesRegex(
            coordinator.IntegrationError,
            "prepared public candidate identity must be bare lowercase 64-hex",
        ):
            coordinator.PreparedPublicCandidate(
                snapshot=REPO_ROOT,
                repository=REPO_ROOT,
                semantic_sha256="sha256:" + bare,
                git_candidate=coordinator.GitCandidate(
                    revision="b" * 40,
                    tree_oid="c" * 40,
                    archive_sha256="sha256:" + "d" * 64,
                    repository="https://github.com/nisavid/agents",
                ),
                supervisor_source_sha256="sha256:" + "e" * 64,
            )

    def test_cli_guard_rejects_unsupported_cpython_before_coordinator_imports(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            coordinator = root / "run_phase7_production_integration.py"
            coordinator.write_text(
                COORDINATOR.read_text(encoding="utf-8"), encoding="utf-8"
            )
            marker = root / "imported"
            (root / "run_phase7_composed_matrix.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            for implementation, version in (("pypy", (3, 14)), ("cpython", (3, 12))):
                with self.subTest(implementation=implementation, version=version):
                    script = (
                        "import runpy, sys, types; "
                        "sys.implementation = types.SimpleNamespace("
                        f"name={implementation!r}, cache_tag=sys.implementation.cache_tag); "
                        f"sys.version_info = {version!r}; "
                        f"sys.argv = [{str(coordinator)!r}, '--help']; "
                        f"runpy.run_path({str(coordinator)!r}, run_name='__main__')"
                    )
                    completed = subprocess.run(
                        [sys.executable, "-I", "-B", "-c", script],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("CPython 3.13+", completed.stderr)
                    self.assertFalse(marker.exists())

    def test_supported_cli_help_does_not_import_candidate_modules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            coordinator = root / "run_phase7_production_integration.py"
            coordinator.write_bytes(COORDINATOR.read_bytes())
            marker = root / "candidate-module-imported"
            (root / "run_phase7_composed_matrix.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, "-I", "-B", str(coordinator), "--help"],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(marker.exists())

    def test_loaded_coordinator_generation_must_match_frozen_snapshot(self) -> None:
        coordinator = load_coordinator()
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory).resolve()
            frozen = snapshot / "scripts/run_phase7_production_integration.py"
            frozen.parent.mkdir(parents=True)
            shutil.copy2(COORDINATOR, frozen)
            coordinator.require_loaded_coordinator_generation(snapshot)
            frozen.write_bytes(
                frozen.read_bytes().replace(
                    b"Run the complete Phase 7 private-to-production integration gate",
                    b"Run a different Phase 7 private-to-production integration gate",
                    1,
                )
            )

            with self.assertRaisesRegex(
                coordinator.IntegrationError,
                "loaded Phase 7 coordinator differs from the frozen candidate",
            ):
                coordinator.require_loaded_coordinator_generation(snapshot)

    def test_entrypoint_rejects_candidate_from_a_different_coordinator_generation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            loaded_coordinator = root / "loaded-coordinator.py"
            shutil.copy2(COORDINATOR, loaded_coordinator)
            repository = root / "candidate"
            frozen_coordinator = (
                repository / "scripts/run_phase7_production_integration.py"
            )
            frozen_coordinator.parent.mkdir(parents=True)
            frozen_coordinator.write_bytes(
                COORDINATOR.read_bytes().replace(
                    b"Run the complete Phase 7 private-to-production integration gate",
                    b"Run a different Phase 7 private-to-production integration gate",
                    1,
                )
            )
            subprocess.run(
                ["git", "init", "--quiet", str(repository)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/nisavid/agents",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "add", "."],
                check=True,
                capture_output=True,
            )
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
                    "different coordinator generation",
                ],
                check=True,
                capture_output=True,
            )
            capability_manifest = root / "capability-manifest.json"
            capability_manifest.write_text("{}\n", encoding="utf-8")
            receipt = root / "release-receipt.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(loaded_coordinator),
                    "--private-repository",
                    str(root),
                    "--private-commit-oid",
                    "a" * 40,
                    "--reviewed-producer-sha256",
                    "sha256:" + "b" * 64,
                    "--public-root",
                    str(repository),
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
                    sys.executable,
                    "--node-executable",
                    "/usr/bin/true",
                    "--release-receipt-output",
                    str(receipt),
                    "--prepared-supervisor-source-sha256",
                    "sha256:" + "c" * 64,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertFalse(receipt.exists())

    def test_phase7_support_loader_uses_only_frozen_source_paths(self) -> None:
        probe = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import importlib.util, pathlib, sys; "
                f"coordinator_path = {str(COORDINATOR)!r}; "
                "spec = importlib.util.spec_from_file_location('phase7_probe', coordinator_path); "
                "coordinator = importlib.util.module_from_spec(spec); "
                "sys.modules[spec.name] = coordinator; "
                "spec.loader.exec_module(coordinator); "
                "[sys.modules.pop(name, None) for name, _ in coordinator.PHASE7_SUPPORT_SOURCES]; "
                f"snapshot = pathlib.Path({str(REPO_ROOT)!r}); "
                "coordinator._install_frozen_phase7_support(snapshot); "
                "assert all(pathlib.Path(sys.modules[name].__file__).is_relative_to(snapshot) "
                "for name, _ in coordinator.PHASE7_SUPPORT_SOURCES)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(probe.returncode, 0, probe.stderr)

    def test_frozen_execution_rejects_preloaded_candidate_support(self) -> None:
        coordinator = load_coordinator()

        with self.assertRaisesRegex(
            coordinator.IntegrationError,
            "must begin before candidate support is loaded",
        ):
            with coordinator.frozen_public_execution(REPO_ROOT, "sha256:" + "a" * 64):
                self.fail("preloaded support reached frozen execution")

    def test_coordinator_executes_the_declared_inherited_evidence_pipeline(
        self,
    ) -> None:
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            private_output = root / "private-output"
            private_summary = root / "private-summary.json"
            composed_output = root / "composed"
            release_receipt = root / "release-receipt.json"
            registry_raw = b'{"package_members":[]}\n'
            registry_path = private_output / "private-producer-registry.json"
            witness_path = private_output / "private-producer-witness.tar"
            frozen_path = private_output / "coordinator-frozen-identity.json"
            reviewed_producer_sha256 = (
                "sha256:" + hashlib.sha256(registry_raw).hexdigest()
            )
            frozen_identity_sha256 = (
                "sha256:"
                + hashlib.sha256(b"coordinator-frozen-identity.json\n").hexdigest()
            )
            producer_package_sha256 = "sha256:" + hashlib.sha256(b"[]").hexdigest()
            private_commit_oid = "f" * 40
            public_identity = "a" * 64
            events: list[str] = []

            def launch(**_arguments: object) -> None:
                events.append("immutable-private-builder")
                private_output.mkdir()
                registry_path.write_bytes(registry_raw)
                for path in (witness_path, frozen_path):
                    path.write_bytes(f"{path.name}\n".encode())
                private_summary.write_bytes(b"{}\n")
                events.append("private-summary-store")

            verify = mock.Mock(
                side_effect=lambda **_arguments: events.append(
                    "public-private-verification"
                )
            )
            compose = mock.Mock(
                side_effect=lambda **_arguments: events.append("public-composition")
            )
            terminal = {"terminal": "passed"}
            release = mock.Mock(
                side_effect=lambda *_args, **_kwargs: (
                    events.append("production-release-validation") or terminal
                )
            )
            candidate = coordinator.PreparedPublicCandidate(
                REPO_ROOT,
                REPO_ROOT,
                public_identity,
                coordinator.GitCandidate(
                    "b" * 40,
                    "c" * 40,
                    "sha256:" + "d" * 64,
                    "https://github.com/nisavid/agents",
                ),
                "sha256:" + "e" * 64,
            )
            with (
                mock.patch.object(coordinator, "require_canonical_capability_manifest"),
                mock.patch.object(
                    coordinator,
                    "launch_private_builder",
                    side_effect=launch,
                ),
                mock.patch.object(
                    coordinator,
                    "verify_private_evidence",
                    verify,
                ),
                mock.patch.object(
                    coordinator.run_phase7_composed_matrix,
                    "run",
                    compose,
                ),
                mock.patch.object(
                    coordinator.validate_public_release,
                    "validate_release",
                    release,
                ),
            ):
                result = coordinator.coordinate(
                    private_repository=root,
                    private_commit_oid=private_commit_oid,
                    reviewed_producer_sha256=reviewed_producer_sha256,
                    public_candidate=candidate,
                    capability_manifest=root / "capability.json",
                    private_output=private_output,
                    private_summary_output=private_summary,
                    composed_output=composed_output,
                    routing_evidence=root,
                    plugin_eval_executable=Path(sys.executable),
                    node_executable=Path("/safe/node"),
                    release_receipt_output=release_receipt,
                )

            self.assertEqual(events, list(coordinator.PIPELINE_STAGES))
            self.assertIs(result, terminal)
            compose.assert_called_once_with(
                replay_summary_path=private_summary,
                producer_witness_path=witness_path,
                producer_registry_path=registry_path,
                expected_frozen_identity_sha256=frozen_identity_sha256,
                expected_commit_oid=private_commit_oid,
                expected_producer_package_sha256=producer_package_sha256,
                public_root=REPO_ROOT,
                public_identity=public_identity,
                output=composed_output,
            )
            self.assertEqual(
                (
                    verify.call_args.kwargs["replay_summary_path"],
                    verify.call_args.kwargs["producer_witness_path"],
                    verify.call_args.kwargs["producer_registry_path"],
                    release.call_args.kwargs["composed_receipt"],
                    release.call_args.kwargs["private_producer_witness"],
                    release.call_args.kwargs["private_producer_registry"],
                ),
                (
                    private_summary,
                    witness_path,
                    registry_path,
                    composed_output / "phase7-composed-matrix.json",
                    witness_path,
                    registry_path,
                ),
            )
            self.assertEqual(
                (
                    verify.call_args.kwargs["expected_frozen_identity_sha256"],
                    compose.call_args.kwargs["expected_frozen_identity_sha256"],
                    release.call_args.kwargs["expected_frozen_private_identity_sha256"],
                    verify.call_args.kwargs["expected_commit_oid"],
                    compose.call_args.kwargs["expected_commit_oid"],
                    release.call_args.kwargs["expected_private_commit_oid"],
                    verify.call_args.kwargs["expected_producer_package_sha256"],
                    compose.call_args.kwargs["expected_producer_package_sha256"],
                    release.call_args.kwargs[
                        "expected_private_producer_package_sha256"
                    ],
                    compose.call_args.kwargs["public_identity"],
                    release.call_args.kwargs["expected_public_candidate_sha256"],
                ),
                (
                    frozen_identity_sha256,
                    frozen_identity_sha256,
                    frozen_identity_sha256,
                    private_commit_oid,
                    private_commit_oid,
                    private_commit_oid,
                    producer_package_sha256,
                    producer_package_sha256,
                    producer_package_sha256,
                    public_identity,
                    public_identity,
                ),
            )

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

    def test_coordinator_rejects_retained_v4_private_evidence_for_live_v5_projection(
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
            git_candidate = coordinator.GitCandidate(
                revision="a" * 40,
                tree_oid="b" * 40,
                archive_sha256="sha256:" + "c" * 64,
                repository="https://github.com/nisavid/agents",
            )
            prepared_candidate = coordinator.PreparedPublicCandidate(
                snapshot=REPO_ROOT,
                repository=REPO_ROOT,
                semantic_sha256=public_identity,
                git_candidate=git_candidate,
                supervisor_source_sha256="sha256:" + "d" * 64,
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
                with self.assertRaisesRegex(
                    coordinator.run_phase7_composed_matrix.ComposedEvidenceError,
                    "private and public compatibility bytes do not match exactly",
                ):
                    coordinator.coordinate(
                        private_repository=root,
                        private_commit_oid=built["commit_oid"],
                        reviewed_producer_sha256=built["summary"][
                            "producer_registry_sha256"
                        ],
                        public_candidate=prepared_candidate,
                        capability_manifest=root,
                        private_output=private_output,
                        private_summary_output=private_summary,
                        composed_output=composed_output,
                        routing_evidence=root,
                        plugin_eval_executable=Path(sys.executable),
                        node_executable=Path("/safe/node"),
                        release_receipt_output=release_receipt,
                    )

            release.assert_not_called()
            self.assertFalse(
                (composed_output / "phase7-composed-matrix.json").exists()
            )
            self.assertFalse(release_receipt.exists())

    def test_successful_main_forwards_one_prepared_candidate_and_node(self) -> None:
        coordinator = load_coordinator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            capability_manifest = root / "capability-manifest.json"
            capability_manifest.write_text("{}\n", encoding="utf-8")
            plugin_eval = root / "plugin-eval.js"
            plugin_eval.write_text("fixture\n", encoding="utf-8")
            node = root / "node"
            node.write_text("fixture\n", encoding="utf-8")
            supervisor_sha256 = "sha256:" + "d" * 64
            git_candidate = coordinator.GitCandidate(
                revision="a" * 40,
                tree_oid="b" * 40,
                archive_sha256="sha256:" + "c" * 64,
                repository="https://github.com/nisavid/agents",
            )
            prepared_candidate = coordinator.PreparedPublicCandidate(
                snapshot=root / "frozen",
                repository=root,
                semantic_sha256="e" * 64,
                git_candidate=git_candidate,
                supervisor_source_sha256=supervisor_sha256,
            )
            frozen_execution = mock.MagicMock()
            frozen_execution.__enter__.return_value = prepared_candidate
            frozen_execution.__exit__.return_value = False

            with (
                mock.patch.object(
                    coordinator,
                    "frozen_public_execution",
                    return_value=frozen_execution,
                ) as freeze,
                mock.patch.object(coordinator, "coordinate") as coordinate,
            ):
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
                        prepared_candidate.semantic_sha256,
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
                        "--prepared-supervisor-source-sha256",
                        supervisor_sha256,
                    ]
                )

            self.assertEqual(result, 0)
            freeze.assert_called_once_with(root, supervisor_sha256)
            coordinate.assert_called_once()
            self.assertIs(
                coordinate.call_args.kwargs["public_candidate"], prepared_candidate
            )
            self.assertEqual(coordinate.call_args.kwargs["node_executable"], node)

    def test_successful_main_materializes_archive_before_coordinate(self) -> None:
        coordinator = load_coordinator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository = root / "candidate"
            install_clean_public_candidate(REPO_ROOT, repository)
            capability_manifest = root / "capability-manifest.json"
            capability_manifest.write_text("{}\n", encoding="utf-8")
            plugin_eval = root / "plugin-eval.js"
            plugin_eval.write_text("fixture\n", encoding="utf-8")
            node = root / "node"
            node.write_text("fixture\n", encoding="utf-8")
            supervisor_sha256 = (
                "sha256:"
                + hashlib.sha256(
                    (
                        repository / "scripts/supervise_prepared_release_validation.py"
                    ).read_bytes()
                ).hexdigest()
            )
            expected_revision = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            expected_public_identity = coordinator.candidate_content_identity(
                repository,
                error_factory=RuntimeError,
            )
            observed_candidates = []

            def observe_coordinate(**arguments):
                public_candidate = arguments["public_candidate"]
                self.assertTrue((public_candidate.snapshot / ".git").is_dir())
                self.assertEqual(public_candidate.repository, repository)
                self.assertEqual(
                    public_candidate.git_candidate.revision, expected_revision
                )
                self.assertEqual(
                    public_candidate.supervisor_source_sha256, supervisor_sha256
                )
                self.assertEqual(
                    coordinator.candidate_content_identity(
                        public_candidate.snapshot,
                        error_factory=RuntimeError,
                    ),
                    public_candidate.semantic_sha256,
                )
                observed_candidates.append(public_candidate)

            for module_name, _relative_path in coordinator.PHASE7_SUPPORT_SOURCES:
                sys.modules.pop(module_name, None)
            coordinator._PHASE7_SUPPORT_BOUND = False
            try:
                with mock.patch.object(
                    coordinator, "coordinate", side_effect=observe_coordinate
                ):
                    result = coordinator.main(
                        [
                            "--private-repository",
                            str(root),
                            "--private-commit-oid",
                            "a" * 40,
                            "--reviewed-producer-sha256",
                            "sha256:" + "b" * 64,
                            "--public-root",
                            str(repository),
                            "--public-candidate-sha256",
                            expected_public_identity,
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
                            "--prepared-supervisor-source-sha256",
                            supervisor_sha256,
                        ]
                    )
            finally:
                for module_name, _relative_path in coordinator.PHASE7_SUPPORT_SOURCES:
                    sys.modules.pop(module_name, None)

            self.assertEqual(result, 0)
            self.assertEqual(len(observed_candidates), 1)

    def test_main_rejects_abbreviated_options(self) -> None:
        coordinator = load_coordinator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            capability_manifest = root / "capability-manifest.json"
            capability_manifest.write_text("{}\n", encoding="utf-8")
            plugin_eval = root / "plugin-eval.js"
            plugin_eval.write_text("fixture\n", encoding="utf-8")
            node = root / "node"
            node.write_text("fixture\n", encoding="utf-8")

            with (
                mock.patch.object(coordinator, "coordinate") as coordinate,
                mock.patch("sys.stderr"),
                self.assertRaises(SystemExit) as raised,
            ):
                coordinator.main(
                    [
                        "--private-repository",
                        str(root),
                        "--private-commit-oid",
                        "a" * 40,
                        "--reviewed-producer-sha256",
                        "sha256:" + "b" * 64,
                        "--public-r",
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

            self.assertEqual(raised.exception.code, 2)
            coordinate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
