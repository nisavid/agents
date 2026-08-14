from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.plugins.task_witness_client._support import (
    CLIENT_ENVIRONMENT,
    INVOCATION_PROFILE_DRIVER_SOURCE,
    LAUNCHER_MODULE_DRIVER_SOURCE,
    SHIM_TEMPLATE,
    bundle_identity,
    shell_quote,
    write_configured_driver,
)

from . import test_receipt_staging as receipt_staging
from ._support import (
    canonical_document,
    load_deployment_module,
    sha256,
)


class StagedClientIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def deployment(self):
        return load_deployment_module()

    def task_witness_candidate_source(self):
        return receipt_staging.ReceiptStagingTests.task_witness_candidate_source(self)

    def task_witness_candidate_inputs(
        self,
        candidate_root: Path = receipt_staging.PLUGIN,
    ):
        return receipt_staging.ReceiptStagingTests.task_witness_candidate_inputs(
            self,
            candidate_root,
        )

    def first_install_request(self, canonical_root: Path):
        return receipt_staging.ReceiptStagingTests.first_install_request(
            self,
            canonical_root,
        )

    def first_install_authorization_raw(self, prepared) -> bytes:
        return receipt_staging.ReceiptStagingTests.first_install_authorization_raw(
            self,
            prepared,
        )

    def runtime_qualification_raw(self) -> bytes:
        return receipt_staging.ReceiptStagingTests.runtime_qualification_raw(self)

    def test_authorized_stage_runs_unchanged_after_byte_exact_installation(
        self,
    ) -> None:
        deployment = self.deployment()
        account_home = self.root / "account-home"
        account_home.mkdir(mode=0o700)
        canonical_root = (
            account_home / ".local" / "libexec" / "task-witness"
        )
        canonical_root.mkdir(parents=True, mode=0o700)
        for parent in (account_home / ".local", account_home / ".local" / "libexec"):
            parent.chmod(0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)

        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        plan = prepared.plan
        source = plan.source
        authorization_raw = self.first_install_authorization_raw(prepared)
        staged = deployment.stage_first_install(
            request,
            authorization_raw,
            self.root / "stage",
        )

        installed_directories = {canonical_root}
        for artifact in staged.artifacts:
            parent = artifact.installed_path.parent
            while parent != canonical_root:
                installed_directories.add(parent)
                parent = parent.parent
        for directory in sorted(installed_directories, key=lambda path: len(path.parts)):
            directory.mkdir(exist_ok=True)
            directory.chmod(0o700)
        for artifact in staged.artifacts:
            shutil.copyfile(artifact.staged_path, artifact.installed_path)
            artifact.installed_path.chmod(artifact.installed["mode"])
            self.assertEqual(artifact.installed_path.read_bytes(), artifact.raw)
            self.assertEqual(
                stat.S_IMODE(artifact.installed_path.stat().st_mode),
                artifact.installed["mode"],
            )
            self.assertNotEqual(
                (
                    artifact.staged_path.stat().st_dev,
                    artifact.staged_path.stat().st_ino,
                ),
                (
                    artifact.installed_path.stat().st_dev,
                    artifact.installed_path.stat().st_ino,
                ),
            )
            self.assertEqual(artifact.staged_path.stat().st_nlink, 1)
            self.assertEqual(artifact.installed_path.stat().st_nlink, 1)
        installed_inodes = {
            (
                artifact.installed_path.stat().st_dev,
                artifact.installed_path.stat().st_ino,
            )
            for artifact in staged.artifacts
        }
        self.assertEqual(len(installed_inodes), len(staged.artifacts))

        shim = next(item for item in staged.artifacts if item.role == "shim")
        expected_shim = (
            SHIM_TEMPLATE.read_text(encoding="utf-8")
            .replace(
                "@TASK_WITNESS_PYTHON@",
                shell_quote(str(Path(sys.executable).resolve(strict=True))),
            )
            .replace(
                "@TASK_WITNESS_CLIENT@",
                shell_quote(
                    str(canonical_root / "client" / "task_witness_client.py")
                ),
            )
            .encode("utf-8")
        )
        self.assertEqual(shim.installed_path.read_bytes(), expected_shim)
        # TW4 traverses the passwd-derived front-door shim. This test proves its
        # exact bytes while the existing drivers redirect only synthetic roots.

        trust = json.loads(plan.trust.context.raw)
        producer = next(
            item
            for item in trust["producers"]
            if item["producer_id"] == deployment.SMOKE_PRODUCER_NAME
        )
        bundle = self.root / "caller-bundle"
        bundle.mkdir(mode=0o700)
        self.assertFalse(bundle.is_relative_to(canonical_root))
        manifest_raw = canonical_document(
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
                "challenge": "task-witness-activation-smoke-v1",
            }
        )
        manifest = bundle / "manifest.json"
        manifest.write_bytes(manifest_raw)
        manifest.chmod(0o600)
        expected_bundle_sha256 = bundle_identity({"manifest.json": manifest_raw})

        launcher_driver = self.root / "launcher_module_driver.py"
        shutil.copyfile(LAUNCHER_MODULE_DRIVER_SOURCE, launcher_driver)
        client_driver = self.root / "configured_client_driver.py"
        write_configured_driver(
            client_driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {
                "scenario": "composed-client",
                "launcher_driver": str(launcher_driver),
            },
        )
        staged_before = {
            item.relative_path: sha256(item.staged_path.read_bytes())
            for item in staged.artifacts
        }
        installed_before = {
            item.relative_path: sha256(item.installed_path.read_bytes())
            for item in staged.artifacts
        }

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(client_driver),
                str(canonical_root / "client" / "task_witness_client.py"),
                str(canonical_root),
                "validate",
                "--bundle",
                str(bundle),
            ],
            text=False,
            capture_output=True,
            check=False,
            env=CLIENT_ENVIRONMENT,
            timeout=20,
        )

        expected_anchor = {
            "contract": "task-witness-complete-anchor-v1",
            "generation": plan.active.generation,
            "active_record_sha256": plan.active.sha256,
            "runtime_contract": "task-witness-runtime-v1",
            "interpreter": json.loads(plan.active.raw)["interpreter"],
            "public_release": {
                "repository": source.repository_id,
                "revision": source.revision,
            },
            "runtime_implementation_sha256": (
                plan.active.runtime_implementation_sha256
            ),
            "trust_context_sha256": plan.trust.context.sha256,
            "bundle_sha256": expected_bundle_sha256,
            "historical": False,
        }
        expected_witness = {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": expected_bundle_sha256,
            "producer": {
                key: value
                for key, value in producer.items()
                if key not in {"state", "usable_for_new_publication"}
            },
            "validator": {
                "validator_id": producer["validator_id"],
                "contract": producer["validator_contract"],
                "implementation_sha256": producer[
                    "validator_implementation_sha256"
                ],
            },
            "projection": {
                "schema_version": 1,
                "contract": "task-witness-smoke-projection-v1",
                "challenge": "task-witness-activation-smoke-v1",
                "accepted": True,
            },
            "trust_context_sha256": plan.trust.context.sha256,
            "historical": False,
        }
        expected_envelope = {
            "contract": "task-witness-launch-envelope-v1",
            "anchor": expected_anchor,
            "witness": expected_witness,
        }
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, canonical_document(expected_envelope))
        self.assertEqual(
            {
                item.relative_path: sha256(item.staged_path.read_bytes())
                for item in staged.artifacts
            },
            staged_before,
        )
        self.assertEqual(
            {
                item.relative_path: sha256(item.installed_path.read_bytes())
                for item in staged.artifacts
            },
            installed_before,
        )
        self.assertEqual(installed_before, staged_before)


if __name__ == "__main__":
    unittest.main()
