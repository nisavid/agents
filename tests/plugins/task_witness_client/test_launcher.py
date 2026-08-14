from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from tests.plugins.task_witness_deployment._support import load_deployment_module

REPOSITORY = Path(__file__).resolve().parents[3]
PLUGIN = REPOSITORY / "plugins" / "task-witness"
LAUNCHER_SOURCE = PLUGIN / "launcher" / "task_witness_launch.py"
LAUNCHER_MODULE_DRIVER = Path(__file__).resolve().parent / "_launcher_module_driver.py"
PAYLOAD_NAMES = (
    "task_witness.py",
    "canonical.py",
    "bundle_io.py",
    "trust.py",
)
SMOKE_CHALLENGE = "task-witness-activation-smoke-v1"
PAYLOAD_SPECS = (
    ("entrypoint", "task_witness.py"),
    ("canonical", "canonical.py"),
    ("bundle-io", "bundle_io.py"),
    ("trust", "trust.py"),
)


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        + b"\n"
    )


def sha(raw: bytes | str) -> str:
    return hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()


def document(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "content_sha256": sha(canonical(value)[:-1])}


def validator_identity(
    contract: str, entrypoint: str, modules: list[tuple[str, str]]
) -> str:
    return sha(
        canonical(
            {
                "contract": "task-witness-validator-artifact-manifest-v1",
                "validator_contract": contract,
                "entrypoint_module": entrypoint,
                "modules": [
                    {"name": name, "content_sha256": digest} for name, digest in modules
                ],
            }
        )[:-1]
    )


def runtime_identity(payloads: list[dict[str, Any]]) -> str:
    return sha(
        canonical(
            {
                "contract": "task-witness-runtime-artifact-manifest-v2",
                "runtime_contract": "task-witness-runtime-v1",
                "entrypoint_role": "entrypoint",
                "payloads": payloads,
            }
        )[:-1]
    )


def interpreter_identity() -> dict[str, object]:
    return {
        "executable": str(Path(sys.executable).resolve()),
        "implementation": "cpython",
        "version": {
            "major": sys.version_info.major,
            "minor": sys.version_info.minor,
            "micro": sys.version_info.micro,
        },
    }


class TaskWitnessLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.install = self.root / "install"
        self.launcher = self.install / "launcher" / "task_witness_launch.py"
        self.generation = self.install / "generations" / "pending-generation"
        self.generation.mkdir(parents=True)
        self.install.chmod(0o700)
        self.generation.parent.chmod(0o700)
        self.generation.chmod(0o700)
        self.launcher.parent.mkdir(parents=True)
        shutil.copy2(LAUNCHER_SOURCE, self.launcher)
        for name in PAYLOAD_NAMES:
            shutil.copy2(PLUGIN / "runtime" / name, self.generation / name)
            (self.generation / name).chmod(0o600)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.bundle.chmod(0o700)
        self.trust = self.root / "trust.json"
        self.validator = self.root / "validator.py"
        self.validator.write_text(
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle, *, trust_snapshot):\n"
            "    return {'projection_contract': 'demo-projection-v1'}\n"
        )
        self.write_inputs()
        self.write_active()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_inputs(
        self,
        *,
        modules: list[tuple[str, Path]] | None = None,
        entrypoint: str = "validator",
    ) -> None:
        modules = modules or [("validator", self.validator)]
        digests = [(name, sha(path.read_bytes())) for name, path in modules]
        implementation = validator_identity("demo-v1", entrypoint, digests)
        validator = {
            "validator_id": "demo-validator",
            "contract": "demo-v1",
            "implementation_sha256": implementation,
            "entrypoint": entrypoint,
            "modules": [
                {"name": name, "path": str(path), "sha256": digest}
                for (name, path), (_, digest) in zip(modules, digests)
            ],
            "state": "active",
            "usable_for_new_publication": True,
        }
        producer = {
            "producer_id": "demo-producer",
            "contract": "demo-v1",
            "implementation_sha256": sha("demo-producer"),
            "validator_id": "demo-validator",
            "validator_contract": "demo-v1",
            "validator_implementation_sha256": implementation,
            "state": "active",
            "usable_for_new_publication": True,
        }
        trust = document(
            {
                "schema_version": 1,
                "contract": "task-witness-trust-context-v2",
                "producers": [producer],
                "issuers": [
                    {
                        "issuer_id": "operator",
                        "contract": "operator-choice-v1",
                        "implementation_sha256": sha("operator"),
                        "capabilities": ["operator-choice"],
                        "state": "active",
                        "usable_for_new_publication": True,
                    }
                ],
                "validators": [validator],
            }
        )
        self.trust.write_bytes(canonical(trust))
        self.trust.chmod(0o600)
        (self.bundle / "manifest.json").write_bytes(
            canonical(
                {
                    "producer": {
                        "producer_id": producer["producer_id"],
                        "contract": producer["contract"],
                        "implementation_sha256": producer["implementation_sha256"],
                    }
                }
            )
        )
        (self.bundle / "manifest.json").chmod(0o600)

    def write_active(self) -> None:
        payloads = [
            {
                "role": role,
                "relative_path": relative_path,
                "length": (self.generation / relative_path).stat().st_size,
                "sha256": sha((self.generation / relative_path).read_bytes()),
            }
            for role, relative_path in PAYLOAD_SPECS
        ]
        expected_generation = (
            self.generation.parent / f"sha256-{runtime_identity(payloads)}"
        )
        if self.generation != expected_generation:
            self.generation.rename(expected_generation)
            self.generation = expected_generation
        active = document(
            {
                "schema_version": 1,
                "contract": "task-witness-launch-active-v1",
                "generation": self.generation.name,
                "runtime_contract": "task-witness-runtime-v1",
                "interpreter": interpreter_identity(),
                "public_release": {
                    "repository": "nisavid/agents",
                    "revision": "0" * 40,
                },
                "payloads": payloads,
            }
        )
        (self.install / "active.json").write_bytes(canonical(active))
        (self.install / "active.json").chmod(0o600)

    def launch(
        self,
        *,
        environment: dict[str, str] | None = None,
        historical: bool = False,
        optimize: int = 0,
        bundle: Path | None = None,
        interpreter_options: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            "-B",
            "-I",
            "-S",
            "-X",
            "disable-remote-debug",
            *(interpreter_options or []),
            *(["-O"] if optimize == 1 else ["-OO"] if optimize == 2 else []),
            str(LAUNCHER_MODULE_DRIVER),
            str(self.launcher),
            "installed-root",
            str(self.install),
            "validate",
            "--bundle",
            str(bundle or self.bundle),
            "--trust-context",
            str(self.trust),
        ]
        if historical:
            command.append("--historical")
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

    def launcher_module(self):
        specification = importlib.util.spec_from_file_location(
            "task_witness_launcher_fixture", self.launcher
        )
        assert specification is not None and specification.loader is not None
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        return module

    def install_intrinsic_smoke_context(self, challenge: str) -> tuple[object, dict]:
        deployment = load_deployment_module()
        deployment.materialize_intrinsic_smoke_provider(
            self.root / "retained-trust"
        )
        deployment.materialize_trust_context(
            [],
            self.root / "retained-trust",
        )
        context = deployment.compose_trust_context(
            [],
            self.root / "retained-trust",
        )
        producer = next(
            item
            for item in context.value["producers"]
            if item["producer_id"] == deployment.SMOKE_PRODUCER_NAME
        )
        self.trust = context.path
        (self.bundle / "manifest.json").write_bytes(
            canonical(
                {
                    "schema_version": 1,
                    "contract": deployment.SMOKE_BUNDLE_CONTRACT,
                    "producer": {
                        "producer_id": producer["producer_id"],
                        "contract": producer["contract"],
                        "implementation_sha256": producer[
                            "implementation_sha256"
                        ],
                    },
                    "challenge": challenge,
                }
            )
        )
        return context, producer

    def test_controller_context_opens_and_executes_intrinsic_smoke_validator(
        self,
    ) -> None:
        context, producer = self.install_intrinsic_smoke_context(
            SMOKE_CHALLENGE
        )

        result = self.launch()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        envelope = json.loads(result.stdout)
        self.assertEqual(
            envelope["anchor"]["trust_context_sha256"],
            context.sha256,
        )
        self.assertEqual(
            envelope["witness"]["producer"],
            {
                key: value
                for key, value in producer.items()
                if key not in {"state", "usable_for_new_publication"}
            },
        )
        self.assertEqual(
            envelope["witness"]["validator"],
            {
                "validator_id": producer["validator_id"],
                "contract": producer["validator_contract"],
                "implementation_sha256": producer[
                    "validator_implementation_sha256"
                ],
            },
        )
        self.assertEqual(
            envelope["witness"]["projection"],
            {
                "schema_version": 1,
                "contract": "task-witness-smoke-projection-v1",
                "challenge": SMOKE_CHALLENGE,
                "accepted": True,
            },
        )

    def test_intrinsic_smoke_validator_rejects_mismatching_challenge(self) -> None:
        self.install_intrinsic_smoke_context(
            "task-witness-activation-smoke-mismatch-v1"
        )

        result = self.launch()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "task witness launch rejected: Task Witness smoke challenge mismatch\n",
        )

    def test_copied_launcher_subprocess_rejects_its_arbitrary_root(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(self.launcher),
                "validate",
                "--bundle",
                str(self.bundle),
                "--trust-context",
                str(self.trust),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launcher path is not canonical", result.stderr)

    def test_optimized_interpreters_reject_before_emitting_an_envelope(self) -> None:
        for optimize in (1, 2):
            with self.subTest(optimize=optimize):
                result = self.launch(optimize=optimize)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("canonical executor", result.stderr)

    def test_copied_launcher_rejects_when_a_valid_canonical_tree_exists(self) -> None:
        canonical_install = self.root / ".local" / "libexec" / "task-witness"
        canonical_install.parent.mkdir(parents=True)
        shutil.copytree(self.install, canonical_install)
        launcher = self.launcher_module()

        with (
            mock.patch.object(
                launcher.pwd,
                "getpwuid",
                return_value=mock.Mock(pw_dir=str(self.root)),
            ),
            self.assertRaisesRegex(
                launcher.LaunchError, "launcher path is not canonical"
            ),
        ):
            launcher._validate(self.bundle, self.trust)

    def test_substituted_entrypoint_is_rejected_before_its_side_effect(self) -> None:
        marker = self.root / "executed"
        entrypoint = self.generation / "task_witness.py"
        official = entrypoint.read_bytes()
        entrypoint.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n"
            f"Path({str(entrypoint)!r}).write_bytes({official!r})\n"
        )

        result = self.launch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("payload digest mismatch", result.stderr)
        self.assertFalse(marker.exists())

    def test_pre_execution_snapshot_recheck_rejects_visible_payload_drift(
        self,
    ) -> None:
        marker = self.root / "validator-executed"
        self.validator.write_text(
            "from pathlib import Path\n"
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle, *, trust_snapshot):\n"
            f"    Path({str(marker)!r}).write_text('executed')\n"
            "    return {'projection_contract': 'demo-projection-v1'}\n"
        )
        self.write_inputs()
        launcher = self.launcher_module()
        original_open_file = launcher._open_file
        changed = False

        def open_file(parent, name, label, limit, **kwargs):
            nonlocal changed
            opened = original_open_file(parent, name, label, limit, **kwargs)
            if name == "trust.py" and not changed:
                changed = True
                target = self.generation / "canonical.py"
                target.write_bytes(target.read_bytes() + b"# changed after snapshot\n")
            return opened

        with (
            mock.patch.object(launcher, "_installed_root", return_value=self.install),
            mock.patch.object(launcher, "_open_file", side_effect=open_file),
            self.assertRaisesRegex(launcher.LaunchError, "runtime payload changed"),
        ):
            launcher._validate(self.bundle, self.trust)
        self.assertFalse(marker.exists())

    def test_rechecks_all_bundle_and_validator_descriptors_before_visible_paths(
        self,
    ) -> None:
        launcher = self.launcher_module()
        snapshot, active, payloads, _ = launcher._snapshot(self.install)
        try:
            runtime = launcher._execute(active, payloads, snapshot)
            bundle_io = runtime._BUNDLE_IO
            trust = runtime._TRUST
            (self.bundle / "supplement.json").write_bytes(canonical({"note": "two"}))
            (self.bundle / "supplement.json").chmod(0o600)
            helper = self.root / "helper.py"
            helper.write_text("VALUE = 'two validators'\n")
            self.write_inputs(
                modules=[("validator", self.validator), ("helper", helper)]
            )
            view = bundle_io.open_bundle(self.bundle, "task-evidence bundle")
            trust_snapshot = trust.open_trust(self.trust, True)
            try:
                validator = trust_snapshot["entries"]["validator"].popitem()[1]
                _, files, parents = trust.load_validator(validator)
                try:
                    bundle_reads = validator_reads = 0
                    bundle_read = bundle_io.read_descriptor
                    visible_stat = os.stat

                    def read_bundle(*args):
                        nonlocal bundle_reads
                        bundle_reads += 1
                        return bundle_read(*args)

                    def bundle_stat(*args, **kwargs):
                        self.assertEqual(bundle_reads, len(view.files))
                        return visible_stat(*args, **kwargs)

                    with (
                        mock.patch.object(bundle_io, "read_descriptor", read_bundle),
                        mock.patch.object(bundle_io.os, "stat", bundle_stat),
                    ):
                        bundle_io.recheck_bundle(view, "task-evidence bundle")

                    def read_validator(*args):
                        nonlocal validator_reads
                        validator_reads += 1
                        return bundle_read(*args)

                    def validator_stat(*args, **kwargs):
                        self.assertEqual(validator_reads, len(files))
                        return visible_stat(*args, **kwargs)

                    with (
                        mock.patch.object(bundle_io, "read_descriptor", read_validator),
                        mock.patch.object(trust.os, "stat", validator_stat),
                    ):
                        trust.recheck_artifacts(files, parents)
                    self.assertGreaterEqual(bundle_reads, 2)
                    self.assertGreaterEqual(validator_reads, 2)
                finally:
                    trust.close_artifacts(files, parents)
            finally:
                trust.close_trust(trust_snapshot)
                bundle_io.close_bundle(view)
        finally:
            snapshot.close()

    def test_runtime_bundle_privacy_uses_the_effective_uid(self) -> None:
        launcher = self.launcher_module()
        snapshot, active, payloads, _ = launcher._snapshot(self.install)
        try:
            runtime = launcher._execute(active, payloads, snapshot)
            bundle_io = runtime._BUNDLE_IO
            metadata = self.bundle.stat()
            with (
                mock.patch.object(
                    bundle_io.os,
                    "getuid",
                    return_value=metadata.st_uid + 1,
                ),
                mock.patch.object(
                    bundle_io.os,
                    "geteuid",
                    return_value=metadata.st_uid,
                ),
            ):
                bundle_io._private(metadata, "task-evidence bundle", True)
        finally:
            snapshot.close()

    def test_snapshot_rechecks_retained_descriptor_bytes_before_visible_paths(
        self,
    ) -> None:
        launcher = self.launcher_module()
        snapshot, *_ = launcher._snapshot(self.install)
        try:
            with (
                mock.patch.object(
                    launcher,
                    "_read",
                    side_effect=launcher.LaunchError("descriptor bytes first"),
                ),
                mock.patch.object(
                    launcher.os,
                    "stat",
                    side_effect=AssertionError("visible path checked first"),
                ),
                self.assertRaisesRegex(launcher.LaunchError, "descriptor bytes first"),
            ):
                snapshot.recheck()
        finally:
            snapshot.close()

    def test_protected_nodes_reject_group_or_other_permissions(self) -> None:
        for target, expected_message in (
            (self.install, "current-user private"),
            (self.install / "active.json", "current-user private"),
            (self.install / "generations", "current-user private"),
            (self.generation, "current-user private"),
            (self.generation / "canonical.py", "current-user private"),
            (self.bundle, "current user and private"),
            (self.bundle / "manifest.json", "current user and private"),
        ):
            original_mode = target.stat().st_mode & 0o777
            target.chmod(original_mode | 0o040)
            with self.subTest(target=target.name):
                result = self.launch()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_message, result.stderr)
            target.chmod(original_mode)

    def test_bundle_hard_link_to_installation_is_rejected(self) -> None:
        os.link(
            self.install / "active.json",
            self.bundle / "active-copy.json",
        )

        result = self.launch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe hard link", result.stderr)

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics required")
    def test_protected_nodes_reject_macos_allow_acl(self) -> None:
        for target in (
            self.install,
            self.install / "active.json",
            self.bundle,
            self.bundle / "manifest.json",
        ):
            with self.subTest(target=target):
                subprocess.run(
                    ["/bin/chmod", "+a", "everyone allow read", str(target)],
                    check=True,
                )
                try:
                    result = self.launch()
                finally:
                    subprocess.run(["/bin/chmod", "-N", str(target)], check=True)
                self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics required")
    def test_protected_nodes_reject_inherited_macos_allow_acl(self) -> None:
        acl_parent = self.root / "acl-parent"
        acl_parent.mkdir()
        acl_parent.chmod(0o700)
        subprocess.run(
            [
                "/bin/chmod",
                "+a",
                "everyone allow read,file_inherit,directory_inherit",
                str(acl_parent),
            ],
            check=True,
        )
        inherited_bundle = acl_parent / "bundle"
        try:
            inherited_bundle.mkdir()
            inherited_bundle.chmod(0o700)
            for source in self.bundle.iterdir():
                target = inherited_bundle / source.name
                shutil.copyfile(source, target)
                target.chmod(0o600)
            launcher = self.launcher_module()
            descriptor = os.open(inherited_bundle / "manifest.json", os.O_RDONLY)
            try:
                self.assertTrue(launcher._macos_descriptor_has_allow_acl(descriptor))
            finally:
                os.close(descriptor)

            result = self.launch(bundle=inherited_bundle)
        finally:
            subprocess.run(["/bin/chmod", "-RN", str(acl_parent)], check=True)

        self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics required")
    def test_protected_nodes_accept_macos_deny_only_acl(self) -> None:
        target = self.bundle / "manifest.json"
        subprocess.run(
            ["/bin/chmod", "+a", "everyone deny write", str(target)], check=True
        )
        try:
            result = self.launch()
        finally:
            subprocess.run(["/bin/chmod", "-N", str(target)], check=True)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_acl_lookup_failure_rejects_private_node(self) -> None:
        launcher = self.launcher_module()
        descriptor = os.open(self.bundle / "manifest.json", os.O_RDONLY)
        try:
            with (
                mock.patch.object(
                    launcher,
                    "_macos_descriptor_has_allow_acl",
                    side_effect=OSError("unavailable"),
                ),
                self.assertRaisesRegex(launcher.LaunchError, "ACL cannot be verified"),
            ):
                launcher._private(os.fstat(descriptor), "private node", descriptor)
        finally:
            os.close(descriptor)

    def test_runtime_bundle_file_limit_rejects_before_validator(self) -> None:
        marker = self.root / "validator-ran"
        self.validator.write_text(
            "from pathlib import Path\n"
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle, *, trust_snapshot):\n"
            f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
            "    return {'projection_contract': 'demo-projection-v1'}\n"
        )
        self.write_inputs()
        for index in range(256):
            extra = self.bundle / f"extra-{index:03d}"
            extra.write_bytes(b"")
            extra.chmod(0o600)

        result = self.launch()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("exceeds the file limit", result.stderr)
        self.assertFalse(marker.exists())

    def test_runtime_bundle_file_limit_accepts_exact_limit(self) -> None:
        marker = self.root / "validator-ran"
        self.validator.write_text(
            "from pathlib import Path\n"
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle, *, trust_snapshot):\n"
            f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
            "    return {'projection_contract': 'demo-projection-v1'}\n"
        )
        self.write_inputs()
        for index in range(255):
            extra = self.bundle / f"extra-{index:03d}"
            extra.write_bytes(b"")
            extra.chmod(0o600)

        result = self.launch()

        self.assertEqual(len(list(self.bundle.iterdir())), 256)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(result.stdout, "")
        self.assertEqual(marker.read_text(encoding="utf-8"), "ran")

    def test_registered_validator_runs_as_trusted_in_process_code(self) -> None:
        marker = self.root / "validator-side-effect"
        self.validator.write_text(
            "from pathlib import Path\n"
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle, *, trust_snapshot):\n"
            f"    Path({str(marker)!r}).write_text('executed')\n"
            "    return {'projection_contract': 'demo-projection-v1'}\n"
        )
        self.write_inputs()

        result = self.launch()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text(), "executed")

    def test_registered_validator_future_annotations_are_source_scoped(self) -> None:
        self.validator.write_text(
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle: 1 / 0, *, trust_snapshot: 1 / 0):\n"
            "    _validate_bundle.__annotations__\n"
            "    return {'projection_contract': 'demo-projection-v1'}\n"
        )
        self.write_inputs()

        entrypoint_without_future = self.launch()

        self.assertNotEqual(entrypoint_without_future.returncode, 0)
        self.assertEqual(entrypoint_without_future.stdout, "")

        helper = self.root / "helper.py"
        helper.write_text(
            "from __future__ import annotations\n"
            "def helper(value: 1 / 0) -> 1 / 0:\n"
            "    return value\n"
            "helper.__annotations__\n"
        )
        self.validator.write_text(
            "from __future__ import annotations\n"
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle: 1 / 0, *, trust_snapshot: 1 / 0):\n"
            "    _validate_bundle.__annotations__\n"
            "    return {'projection_contract': 'demo-projection-v1'}\n"
        )
        self.write_inputs(modules=[("validator", self.validator), ("helper", helper)])

        source_scoped = self.launch()

        self.assertEqual(source_scoped.returncode, 0, source_scoped.stderr)
        helper.write_text(
            "def helper(value: 1 / 0) -> 1 / 0:\n"
            "    return value\n"
            "helper.__annotations__\n"
        )
        self.write_inputs(modules=[("validator", self.validator), ("helper", helper)])

        inherited = self.launch()

        self.assertNotEqual(inherited.returncode, 0)
        self.assertEqual(inherited.stdout, "")

    def test_runtime_rechecks_reject_byte_identical_visible_replacements(self) -> None:
        launcher = self.launcher_module()
        snapshot, active, payloads, _ = launcher._snapshot(self.install)
        runtime = launcher._execute(active, payloads, snapshot)
        bundle_io = runtime._BUNDLE_IO
        trust = runtime._TRUST
        try:
            view = bundle_io.open_bundle(self.bundle, "task-evidence bundle")
            try:
                original_read = bundle_io.read_descriptor

                def replace_bundle(*args):
                    raw = original_read(*args)
                    replacement = self.bundle / "replacement.json"
                    replacement.write_bytes(raw)
                    replacement.chmod(0o600)
                    os.replace(replacement, self.bundle / "manifest.json")
                    return raw

                with (
                    mock.patch.object(bundle_io, "read_descriptor", replace_bundle),
                    self.assertRaisesRegex(bundle_io.C.EvidenceError, "changed"),
                ):
                    bundle_io.recheck_bundle(view, "task-evidence bundle")
            finally:
                bundle_io.close_bundle(view)

            trust_snapshot = trust.open_trust(self.trust, True)
            try:
                original_read = bundle_io.read_descriptor

                def replace_trust(*args):
                    raw = original_read(*args)
                    replacement = self.root / "replacement-trust.json"
                    replacement.write_bytes(raw)
                    replacement.chmod(0o600)
                    os.replace(replacement, self.trust)
                    return raw

                with (
                    mock.patch.object(bundle_io, "read_descriptor", replace_trust),
                    self.assertRaisesRegex(bundle_io.C.EvidenceError, "changed"),
                ):
                    trust.recheck_trust(trust_snapshot)
            finally:
                trust.close_trust(trust_snapshot)

            artifact_trust = trust.open_trust(self.trust, True)
            try:
                entry = artifact_trust["entries"]["validator"].popitem()[1]
                _, files, parents = trust.load_validator(entry)
                try:
                    original_read = bundle_io.read_descriptor

                    def replace_artifact(*args):
                        raw = original_read(*args)
                        replacement = self.root / "replacement-validator.py"
                        replacement.write_bytes(raw)
                        replacement.chmod(0o600)
                        os.replace(replacement, self.validator)
                        return raw

                    with (
                        mock.patch.object(
                            bundle_io, "read_descriptor", replace_artifact
                        ),
                        self.assertRaisesRegex(bundle_io.C.EvidenceError, "drifted"),
                    ):
                        trust.recheck_artifacts(files, parents)
                finally:
                    trust.close_artifacts(files, parents)
            finally:
                trust.close_trust(artifact_trust)
        finally:
            snapshot.close()

    def test_active_record_requires_the_full_typed_release_identity(self) -> None:
        active_path = self.install / "active.json"
        active = json.loads(active_path.read_text())
        for mutation in (
            {key: value for key, value in active.items() if key != "runtime_contract"},
            {
                **active,
                "schema_version": True,
            },
            {
                **active,
                "public_release": {"repository": "nisavid/agents"},
            },
            {
                **active,
                "runtime_contract": "task-witness-runtime-v2",
            },
            {
                **active,
                "payloads": [
                    {
                        "role": "canonical",
                        "relative_path": "canonical.py",
                        "length": active["payloads"][0]["length"],
                        "sha256": active["payloads"][0]["sha256"],
                    },
                    *active["payloads"][1:],
                ],
            },
        ):
            unsigned = {
                key: value for key, value in mutation.items() if key != "content_sha256"
            }
            mutation["content_sha256"] = sha(canonical(unsigned)[:-1])
            active_path.write_bytes(canonical(mutation))
            with self.subTest(mutation=mutation):
                result = self.launch()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("active record", result.stderr)
            self.write_active()

    def test_valid_launch_returns_a_canonical_anchor_bound_envelope(self) -> None:
        hostile = self.root / "hostile-pythonpath"
        hostile.mkdir()
        marker = self.root / "sitecustomize-ran"
        (hostile / "sitecustomize.py").write_text(
            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\n"
        )
        result = self.launch(
            environment={
                **os.environ,
                "PYTHONPATH": str(hostile),
                "PATH": str(self.root / "hostile-path"),
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["contract"], "task-witness-launch-envelope-v1")
        self.assertEqual(envelope["anchor"]["generation"], self.generation.name)
        self.assertEqual(
            envelope["anchor"]["active_record_sha256"],
            sha((self.install / "active.json").read_bytes()),
        )
        self.assertEqual(
            envelope["anchor"]["runtime_contract"],
            "task-witness-runtime-v1",
        )
        self.assertEqual(envelope["anchor"]["interpreter"], interpreter_identity())
        self.assertEqual(
            envelope["anchor"]["public_release"],
            {"repository": "nisavid/agents", "revision": "0" * 40},
        )
        self.assertEqual(
            envelope["anchor"]["trust_context_sha256"],
            sha(self.trust.read_bytes()),
        )
        self.assertFalse(envelope["anchor"]["historical"])
        self.assertEqual(
            envelope["anchor"]["bundle_sha256"], envelope["witness"]["bundle_sha256"]
        )
        self.assertEqual(
            envelope["witness"]["trust_context_sha256"],
            sha(self.trust.read_bytes()),
        )
        self.assertFalse(envelope["witness"]["historical"])
        self.assertEqual(
            envelope["witness"]["projection"],
            {"projection_contract": "demo-projection-v1"},
        )
        self.assertEqual(result.stdout.encode(), canonical(envelope))
        self.assertFalse(marker.exists())

    def test_public_release_identity_is_result_bound(self) -> None:
        first = self.launch()
        self.assertEqual(first.returncode, 0, first.stderr)
        active_path = self.install / "active.json"
        active = json.loads(active_path.read_text())
        active.pop("content_sha256")
        active["public_release"]["revision"] = "1" * 40
        active_path.write_bytes(canonical(document(active)))
        second = self.launch()

        self.assertEqual(second.returncode, 0, second.stderr)
        first_anchor = json.loads(first.stdout)["anchor"]
        second_anchor = json.loads(second.stdout)["anchor"]
        self.assertNotEqual(
            first_anchor["active_record_sha256"],
            second_anchor["active_record_sha256"],
        )
        self.assertEqual(
            second_anchor["public_release"],
            {"repository": "nisavid/agents", "revision": "1" * 40},
        )

    def test_interpreter_identity_is_exactly_record_bound_and_result_bound(
        self,
    ) -> None:
        first = self.launch()
        self.assertEqual(first.returncode, 0, first.stderr)
        active_path = self.install / "active.json"
        active = json.loads(active_path.read_text())
        active.pop("content_sha256")
        active["interpreter"]["implementation"] = "CPython"
        active_path.write_bytes(canonical(document(active)))

        second = self.launch()

        self.assertNotEqual(second.returncode, 0)
        self.assertIn("interpreter identity disagreement", second.stderr)
        self.assertEqual(
            json.loads(first.stdout)["anchor"]["interpreter"], interpreter_identity()
        )

    def test_historical_mode_is_passed_and_bound_into_the_result(self) -> None:
        result = self.launch(historical=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertTrue(envelope["anchor"]["historical"])
        self.assertTrue(envelope["witness"]["historical"])

    def test_launcher_rejects_a_payload_runtime_identity_false_claim(self) -> None:
        entrypoint = self.generation / "task_witness.py"
        entrypoint.write_text(
            entrypoint.read_text() + "\nRUNTIME_IMPLEMENTATION_SHA256 = '0' * 64\n"
        )
        self.write_active()

        result = self.launch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime implementation identity disagreement", result.stderr)

    def test_launcher_rejects_missing_or_malformed_runtime_bundle_identity(
        self,
    ) -> None:
        payload = self.generation / "task_witness.py"
        original = payload.read_text()
        cases = {
            "missing": original.replace(
                '            "bundle_sha256": _bundle_identity(view),\n', ""
            ),
            "malformed": original.replace(
                "_bundle_identity(view)", "'not-a-digest'", 1
            ),
        }
        for label, source in cases.items():
            with self.subTest(identity=label):
                payload.write_text(source)
                self.write_active()
                result = self.launch()

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("retained bundle", result.stderr)
                payload = self.generation / "task_witness.py"
        payload.write_text(original)
        self.write_active()

    def test_active_generation_must_name_the_retained_runtime_identity(self) -> None:
        wrong_generation = self.install / "generations" / ("sha256-" + "f" * 64)
        shutil.copytree(self.generation, wrong_generation)
        active_path = self.install / "active.json"
        active = json.loads(active_path.read_text())
        active.pop("content_sha256")
        active["generation"] = wrong_generation.name
        active_path.write_bytes(canonical(document(active)))

        result = self.launch()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "active generation does not match runtime identity", result.stderr
        )

    def test_bundle_identity_covers_each_retained_file(self) -> None:
        first = self.launch()
        self.assertEqual(first.returncode, 0, first.stderr)
        (self.bundle / "review-notes.txt").write_text("retained extra evidence\n")
        (self.bundle / "review-notes.txt").chmod(0o600)

        second = self.launch()

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(
            json.loads(first.stdout)["witness"]["bundle_sha256"],
            json.loads(second.stdout)["witness"]["bundle_sha256"],
        )

    def test_complete_anchor_rejects_a_different_valid_bundle(self) -> None:
        alternate = self.root / "alternate-bundle"
        shutil.copytree(self.bundle, alternate)
        alternate.chmod(0o700)
        (alternate / "extra-evidence.txt").write_text("different valid bundle\n")
        (alternate / "extra-evidence.txt").chmod(0o600)

        first = self.launch()
        second = self.launch(bundle=alternate)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        expected_anchor = json.loads(first.stdout)["anchor"]
        returned_anchor = json.loads(second.stdout)["anchor"]
        self.assertNotEqual(
            expected_anchor["bundle_sha256"], returned_anchor["bundle_sha256"]
        )
        self.assertNotEqual(expected_anchor, returned_anchor)

    def test_oversized_json_numbers_reject_before_the_validator(self) -> None:
        marker = self.root / "validator-ran"
        self.validator.write_text(
            "from pathlib import Path\n"
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle, *, trust_snapshot):\n"
            f"    Path({str(marker)!r}).write_text('ran')\n"
            "    return {'projection_contract': 'demo-projection-v1'}\n"
        )
        self.write_inputs()
        for label, number in (
            ("integer", "9" * 129),
            ("float", "1e" + "9" * 127),
        ):
            with self.subTest(number=label):
                (self.bundle / "manifest.json").write_text(
                    f'{{"oversized":{number}}}\n'
                )
                (self.bundle / "manifest.json").chmod(0o600)
                result = self.launch()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("numeric token", result.stderr)
                self.assertFalse(marker.exists())
        self.write_inputs()

    def test_oversized_active_record_numbers_reject_before_payload_execution(
        self,
    ) -> None:
        marker = self.root / "runtime-payload-ran"
        payload = self.generation / "task_witness.py"
        payload.write_text(
            payload.read_text().replace(
                "from __future__ import annotations\n",
                "from __future__ import annotations\n"
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\n",
                1,
            )
        )
        self.write_active()
        active = (self.install / "active.json").read_bytes()
        for label, number in (
            ("integer", "9" * 129),
            ("float", "1e" + "9" * 127),
        ):
            with self.subTest(number=label):
                (self.install / "active.json").write_text(f'{{"oversized":{number}}}\n')
                (self.install / "active.json").chmod(0o600)
                result = self.launch()

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("numeric token", result.stderr)
                self.assertFalse(marker.exists())
        (self.install / "active.json").write_bytes(active)

    def test_noncanonical_interpreter_options_reject_before_payloads(self) -> None:
        marker = self.root / "validator-ran"
        self.validator.write_text(
            "from pathlib import Path\n"
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle, *, trust_snapshot):\n"
            f"    Path({str(marker)!r}).write_text('ran')\n"
            "    return {'projection_contract': 'demo-projection-v1'}\n"
        )
        self.write_inputs()
        for option in (
            ["-W", "error"],
            ["-X", "dev"],
            ["-X", "utf8=1"],
            ["-X", "int_max_str_digits=0"],
        ):
            with self.subTest(option=option):
                result = self.launch(interpreter_options=option)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("canonical executor", result.stderr)
                self.assertFalse(marker.exists())

    def test_launcher_requires_exact_remote_debug_shutdown_option(self) -> None:
        for flags in (
            ["-B", "-I", "-S"],
            ["-B", "-I", "-S", "-X", "disable-remote-debug", "-X", "dev"],
        ):
            with self.subTest(flags=flags):
                result = subprocess.run(
                    [
                        sys.executable,
                        *flags,
                        str(LAUNCHER_MODULE_DRIVER),
                        str(self.launcher),
                        "installed-root",
                        str(self.install),
                        "validate",
                        "--bundle",
                        str(self.bundle),
                        "--trust-context",
                        str(self.trust),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("canonical executor", result.stderr)

    def test_launcher_exposes_no_public_validate_entrypoint(self) -> None:
        launcher = self.launcher_module()

        self.assertFalse(hasattr(launcher, "validate"))
        self.assertTrue(hasattr(launcher, "_validate"))

    def test_trust_identity_covers_the_exact_retained_context(self) -> None:
        first = self.launch()
        self.assertEqual(first.returncode, 0, first.stderr)
        trust = json.loads(self.trust.read_text())
        trust.pop("content_sha256")
        trust["issuers"].append(
            {
                "issuer_id": "unused-auditor",
                "contract": "unused-auditor-v1",
                "implementation_sha256": sha("unused-auditor"),
                "capabilities": ["unused-audit"],
                "state": "active",
                "usable_for_new_publication": True,
            }
        )
        self.trust.write_bytes(canonical(document(trust)))
        second = self.launch()

        self.assertEqual(second.returncode, 0, second.stderr)
        first_envelope = json.loads(first.stdout)
        second_envelope = json.loads(second.stdout)
        self.assertEqual(
            first_envelope["witness"]["projection"],
            second_envelope["witness"]["projection"],
        )
        self.assertNotEqual(
            first_envelope["anchor"]["trust_context_sha256"],
            second_envelope["anchor"]["trust_context_sha256"],
        )
        self.assertNotEqual(first.stdout, second.stdout)

    def test_historical_mode_admits_only_historically_usable_lifecycle_entries(
        self,
    ) -> None:
        trust = json.loads(self.trust.read_text())
        trust.pop("content_sha256")
        for category in ("producers", "validators"):
            trust[category][0]["state"] = "historical-usable"
            trust[category][0]["usable_for_new_publication"] = False
        self.trust.write_bytes(canonical(document(trust)))

        live = self.launch()
        historical = self.launch(historical=True)

        self.assertNotEqual(live.returncode, 0)
        self.assertEqual(historical.returncode, 0, historical.stderr)

    def test_validator_exit_or_output_cannot_produce_a_successful_envelope(
        self,
    ) -> None:
        for source in (
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle, *, trust_snapshot):\n"
            "    raise SystemExit(0)\n",
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle, *, trust_snapshot):\n"
            "    print('unframed payload output')\n"
            "    return {'projection_contract': 'demo-projection-v1'}\n",
        ):
            self.validator.write_text(source)
            self.write_inputs()
            with self.subTest(source=source):
                result = self.launch()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_payload_interrupt_becomes_the_standard_subprocess_interrupt_status(
        self,
    ) -> None:
        self.validator.write_text(
            "BUNDLE_CONTRACT = 'demo-v1'\n"
            "def _validate_bundle(bundle, *, trust_snapshot):\n"
            "    raise KeyboardInterrupt()\n"
        )
        self.write_inputs()

        result = self.launch()

        self.assertEqual(result.returncode, 130)
        self.assertEqual(result.stdout, "")

    def test_direct_runtime_import_and_execution_fail_without_launch_context(
        self,
    ) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                str(self.generation / "task_witness.py"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be launched by task_witness_launch.py", result.stderr)

    def test_symlink_fifo_and_record_disagreement_are_rejected(self) -> None:
        payload = self.generation / "canonical.py"
        payload.unlink()
        payload.symlink_to(self.generation / "trust.py")
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("regular", result.stderr)

        payload.unlink()
        os.mkfifo(payload)
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("regular", result.stderr)

        payload.unlink()
        shutil.copy2(PLUGIN / "runtime" / "canonical.py", payload)
        payload.chmod(0o600)
        payload.write_bytes(payload.read_bytes() + b"# disagreement\n")
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("payload digest mismatch", result.stderr)

    def test_post_snapshot_payload_and_active_record_mutation_are_rejected(
        self,
    ) -> None:
        for target in (self.generation / "canonical.py", self.install / "active.json"):
            with self.subTest(target=target.name):
                self.validator.write_text(
                    "from pathlib import Path\n"
                    "BUNDLE_CONTRACT = 'demo-v1'\n"
                    "def _validate_bundle(bundle, *, trust_snapshot):\n"
                    f"    target = Path({str(target)!r})\n"
                    "    target.write_bytes(target.read_bytes() + b'# mutation\\n')\n"
                    "    return {'projection_contract': 'demo-projection-v1'}\n"
                )
                self.write_inputs()

                result = self.launch()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("runtime artifact changed", result.stderr)
                for name in PAYLOAD_NAMES:
                    shutil.copy2(PLUGIN / "runtime" / name, self.generation / name)
                    (self.generation / name).chmod(0o600)
                self.write_active()

    def test_unselected_partial_rotation_leaves_old_active_generation_viable(
        self,
    ) -> None:
        partial = self.install / "generations" / "generation-b"
        partial.mkdir()
        (partial / "task_witness.py").write_text("incomplete\n")

        result = self.launch()

        self.assertEqual(result.returncode, 0, result.stderr)
