from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"

sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.integration_upgrades import (  # noqa: E402
    IntegrationCatalogEntry,
    IntegrationUpgradeError,
    IntegrationUpgradeManager,
    IntegrationUpgradePlan,
    IntegrationUpdatePolicy,
    PackageManifest,
    read_integration_authority_set_digest,
)


def package_bytes(version: str = "1.2.3") -> bytes:
    return json.dumps(
        {"name": "upstream-hindsight-adapter", "version": version},
        sort_keys=True,
    ).encode()


def manifest_data(
    payload: bytes,
    *,
    version: str = "1.2.3",
    transport_mode: str = "broker-v1",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "harness_id": "codex",
        "version": version,
        "source_url": "https://releases.example.test/codex/1.2.3/package.json",
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "artifact_size": len(payload),
        "publisher": "upstream-hindsight",
        "transport_mode": transport_mode,
        "hook_schema_version": 1,
        "transcript_schema_version": 1,
    }


def passing_report(manifest: PackageManifest) -> dict[str, object]:
    return {
        "schema_version": 1,
        "harness_id": manifest.harness_id,
        "version": manifest.version,
        "artifact_sha256": manifest.artifact_sha256,
        "checks": {
            "disposable": True,
            "hook_schema": True,
            "transcript": True,
            "security": True,
            "broker_transport": manifest.transport_mode == "broker-v1",
        },
    }


def catalog_data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "catalog_id": "official-hindsight-integrations",
        "harness_id": "codex",
        "publisher": "upstream-hindsight",
        "source_origin": "https://releases.example.test",
        "manifest_url": "https://releases.example.test/codex/stable.json",
        "verifier_identity": "sigstore-upstream-hindsight",
        "allowed_transport_modes": ["broker-v1", "direct-only"],
    }


def policy_data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "harness_id": "codex",
        "catalog_id": "official-hindsight-integrations",
        "initial_version": "1.2.3",
        "channel": "stable",
        "allowed_major": 1,
        "update_policy": "automatic-compatible",
        "retained_generations": 2,
    }


def passing_attestation(manifest: PackageManifest) -> dict[str, object]:
    return {
        "schema_version": 1,
        "catalog_id": "official-hindsight-integrations",
        "harness_id": manifest.harness_id,
        "version": manifest.version,
        "artifact_sha256": manifest.artifact_sha256,
        "publisher": manifest.publisher,
        "source_url": manifest.source_url,
        "verifier_identity": "sigstore-upstream-hindsight",
        "verified": True,
    }


def raising_runner(error: BaseException):
    def run(_path: Path, _manifest: PackageManifest) -> bool:
        raise error

    return run


class IntegrationUpgradeManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "state"
        self.package = package_bytes()
        self.manifest = PackageManifest.load(manifest_data(self.package))
        self.catalog = IntegrationCatalogEntry.load(catalog_data())
        self.policy = IntegrationUpdatePolicy.load(policy_data())

    def manager(
        self,
        *,
        smoke=True,
        compatibility_runner=None,
        smoke_runner=None,
        source_verifier=None,
        policy=None,
        catalog=None,
        state=None,
    ):
        return IntegrationUpgradeManager(
            state or self.state,
            catalog=catalog or self.catalog,
            policy=policy or self.policy,
            source_verifier=source_verifier or passing_attestation,
            source_verifier_digest="1" * 64,
            compatibility_runner=(
                compatibility_runner
                or (lambda _path, manifest: passing_report(manifest))
            ),
            compatibility_runner_digest="2" * 64,
            smoke_runner=smoke_runner or (lambda _path, _manifest: smoke),
            smoke_runner_digest="3" * 64,
        )

    def _apply_second_version(
        self, manager: IntegrationUpgradeManager, version: str = "1.2.4"
    ) -> tuple[PackageManifest, IntegrationUpgradePlan]:
        package = package_bytes(version)
        manifest = PackageManifest.load(
            manifest_data(package, version=version)
        )
        plan = manager.plan(manifest, package=package)
        manager.apply(plan, approval_digest=plan.plan_digest)
        return manifest, plan

    def test_plan_stages_an_immutable_digest_bound_candidate(self) -> None:
        manager = self.manager()

        plan = manager.plan(self.manifest, package=self.package)

        self.assertEqual(plan.disposition, "activate")
        self.assertTrue(plan.memory_authority)
        self.assertRegex(plan.plan_digest, r"^[0-9a-f]{64}$")
        candidate = self.state / plan.candidate_path
        self.assertEqual(candidate.read_bytes(), self.package)
        self.assertEqual(candidate.stat().st_mode & 0o777, 0o400)
        self.assertEqual(manager.status("codex")["pending"]["plan_digest"], plan.plan_digest)

    def test_direct_only_release_can_update_without_memory_authority(self) -> None:
        manifest = PackageManifest.load(
            manifest_data(self.package, transport_mode="direct-only")
        )
        manager = self.manager()

        plan = manager.plan(manifest, package=self.package)
        outcome = manager.apply(plan, approval_digest=plan.plan_digest)

        self.assertEqual(plan.disposition, "select-without-authority")
        self.assertEqual(outcome["status"], "selected")
        current = manager.status("codex")["current"]
        self.assertEqual(current["version"], "1.2.3")
        self.assertFalse(current["memory_authority"])
        self.assertIsNone(manager.status("codex")["authority"])

    def test_incompatible_candidate_is_quarantined_and_cannot_apply(self) -> None:
        def incompatible(_path: Path, manifest: PackageManifest):
            report = passing_report(manifest)
            report["checks"]["security"] = False
            return report

        manager = self.manager(compatibility_runner=incompatible)

        plan = manager.plan(self.manifest, package=self.package)

        self.assertEqual(plan.disposition, "quarantine")
        self.assertFalse(plan.memory_authority)
        self.assertEqual(manager.status("codex")["quarantine"][0]["reason"], "compatibility")
        with self.assertRaisesRegex(IntegrationUpgradeError, "not activatable"):
            manager.apply(plan, approval_digest=plan.plan_digest)

    def test_smoke_failure_rolls_back_and_quarantines_candidate(self) -> None:
        first = self.manager()
        initial = first.plan(self.manifest, package=self.package)
        first.apply(initial, approval_digest=initial.plan_digest)

        next_package = package_bytes("1.2.4")
        next_manifest = PackageManifest.load(
            manifest_data(next_package, version="1.2.4")
        )
        failing = self.manager(smoke=False)
        plan = failing.plan(next_manifest, package=next_package)

        with self.assertRaisesRegex(IntegrationUpgradeError, "smoke test failed"):
            failing.apply(plan, approval_digest=plan.plan_digest)

        status = failing.status("codex")
        self.assertEqual(status["current"]["version"], "1.2.3")
        self.assertEqual(status["last_known_good"]["version"], "1.2.3")
        self.assertEqual(status["quarantine"][0]["version"], "1.2.4")

    def test_rollback_runner_exception_restores_current_and_clears_transaction(self) -> None:
        manager = self.manager()
        first = manager.plan(self.manifest, package=self.package)
        manager.apply(first, approval_digest=first.plan_digest)
        next_manifest, _second = self._apply_second_version(manager)
        failing = self.manager(
            smoke_runner=raising_runner(ValueError("runner failed"))
        )

        with self.assertRaisesRegex(IntegrationUpgradeError, "rollback smoke test failed"):
            failing.rollback(
                "codex",
                expected_current_artifact_sha256=next_manifest.artifact_sha256,
            )

        status = failing.status("codex")
        self.assertEqual(status["current"]["version"], "1.2.4")
        self.assertFalse(status["transaction_pending"])

    def test_every_created_state_directory_is_private_with_permissive_umask(self) -> None:
        isolated = Path(self.temporary.name) / "weak-umask" / "state"
        old_umask = os.umask(0)
        try:
            manager = self.manager(state=isolated)
            manager.plan(self.manifest, package=self.package)
        finally:
            os.umask(old_umask)

        for path in isolated.rglob("*"):
            if path.is_dir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o700, path)

    def test_rollback_is_current_digest_bound(self) -> None:
        manager = self.manager()
        first = manager.plan(self.manifest, package=self.package)
        manager.apply(first, approval_digest=first.plan_digest)
        _next_manifest, _second = self._apply_second_version(manager)
        current_digest = manager.status("codex")["current"]["artifact_sha256"]
        for path in (self.state / "plans").glob("*.json"):
            path.unlink()

        with self.assertRaisesRegex(IntegrationUpgradeError, "current digest changed"):
            manager.rollback(
                "codex", expected_current_artifact_sha256="0" * 64
            )

        outcome = manager.rollback(
            "codex", expected_current_artifact_sha256=current_digest
        )
        self.assertEqual(outcome["status"], "rolled-back")
        self.assertEqual(manager.status("codex")["current"]["version"], "1.2.3")

    def test_manifest_and_report_are_closed_and_credential_free(self) -> None:
        credential_url = manifest_data(self.package)
        credential_url["source_url"] = "https://token@example.test/package"
        with self.assertRaisesRegex(IntegrationUpgradeError, "credential-free HTTPS"):
            PackageManifest.load(credential_url)

        unknown = {**manifest_data(self.package), "api_key": "secret"}
        with self.assertRaisesRegex(IntegrationUpgradeError, "keys are closed"):
            PackageManifest.load(unknown)

        def secret_report(_path: Path, manifest: PackageManifest):
            return {**passing_report(manifest), "token": "secret"}

        manager = self.manager(compatibility_runner=secret_report)
        with self.assertRaisesRegex(IntegrationUpgradeError, "keys are closed"):
            manager.plan(self.manifest, package=self.package)
        quarantine = manager.status("codex")["quarantine"]
        self.assertEqual(quarantine[0]["reason"], "runner-failure")
        self.assertNotIn("secret", json.dumps(quarantine))

    def test_download_is_stream_bounded_and_digest_verified_before_publication(self) -> None:
        class Response(io.BytesIO):
            def geturl(self):
                return self.manifest.source_url

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        manager = self.manager()
        response = Response(self.package)
        response.manifest = self.manifest

        plan = manager.download_and_plan(
            self.manifest,
            opener=lambda _request, timeout: response,
        )

        self.assertEqual(plan.manifest.artifact_sha256, self.manifest.artifact_sha256)

        corrupt = Response(self.package + b"x")
        corrupt.manifest = self.manifest
        with self.assertRaisesRegex(IntegrationUpgradeError, "downloaded package size"):
            manager.download_and_plan(
                self.manifest,
                opener=lambda _request, timeout: corrupt,
            )

        same_size_corrupt = Response(bytes(byte ^ 1 for byte in self.package))
        same_size_corrupt.manifest = self.manifest
        with self.assertRaisesRegex(IntegrationUpgradeError, "downloaded package digest"):
            manager.download_and_plan(
                self.manifest,
                opener=lambda _request, timeout: same_size_corrupt,
            )

    def test_automatic_check_fetches_tests_and_activates_latest_manifest(self) -> None:
        class Response(io.BytesIO):
            def __init__(self, payload: bytes, url: str) -> None:
                super().__init__(payload)
                self.url = url

            def geturl(self):
                return self.url

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        manifest_payload = json.dumps(manifest_data(self.package)).encode()
        opened: list[str] = []

        def opener(request, timeout):
            self.assertGreater(timeout, 0)
            opened.append(request.full_url)
            if request.full_url == self.catalog.manifest_url:
                return Response(manifest_payload, self.catalog.manifest_url)
            return Response(self.package, self.manifest.source_url)

        outcome = self.manager().check_for_update(opener=opener)

        self.assertEqual(outcome["status"], "activated")
        self.assertEqual(outcome["version"], "1.2.3")
        self.assertTrue(outcome["memory_authority"])
        self.assertEqual(
            opened,
            [self.catalog.manifest_url, self.manifest.source_url],
        )

    def test_automatic_check_is_a_noop_for_the_active_artifact(self) -> None:
        class Response(io.BytesIO):
            def geturl(inner_self):
                return self.catalog.manifest_url

            def __enter__(inner_self):
                return inner_self

            def __exit__(inner_self, *_args):
                inner_self.close()

        manager = self.manager()
        plan = manager.plan(self.manifest, package=self.package)
        manager.apply(plan, approval_digest=plan.plan_digest)

        outcome = manager.check_for_update(
            opener=lambda _request, timeout: Response(
                json.dumps(manifest_data(self.package)).encode()
            )
        )

        self.assertEqual(outcome["status"], "current")
        self.assertEqual(outcome["artifact_sha256"], self.manifest.artifact_sha256)

    def test_manual_check_stages_but_does_not_activate(self) -> None:
        class Response(io.BytesIO):
            def __init__(self, payload: bytes, url: str) -> None:
                super().__init__(payload)
                self.url = url

            def geturl(self):
                return self.url

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        policy = IntegrationUpdatePolicy.load(
            {**policy_data(), "update_policy": "manual"}
        )

        def opener(request, timeout):
            del timeout
            if request.full_url == self.catalog.manifest_url:
                return Response(
                    json.dumps(manifest_data(self.package)).encode(),
                    self.catalog.manifest_url,
                )
            return Response(self.package, self.manifest.source_url)

        outcome = self.manager(policy=policy).check_for_update(opener=opener)

        self.assertEqual(outcome["status"], "planned")
        self.assertIsNone(self.manager(policy=policy).status("codex")["current"])

    def test_catalog_normalizes_the_default_https_port(self) -> None:
        catalog = IntegrationCatalogEntry.load(
            {
                **catalog_data(),
                "source_origin": "https://releases.example.test:443",
                "manifest_url": "https://releases.example.test/codex/stable.json",
            }
        )

        plan = self.manager(catalog=catalog).plan(
            self.manifest,
            package=self.package,
        )

        self.assertEqual(catalog.source_origin, "https://releases.example.test")
        self.assertEqual(plan.manifest.version, "1.2.3")

    def test_catalog_rejects_a_cross_origin_manifest_feed(self) -> None:
        with self.assertRaisesRegex(
            IntegrationUpgradeError,
            "manifest URL must use the catalog source origin",
        ):
            IntegrationCatalogEntry.load(
                {
                    **catalog_data(),
                    "manifest_url": "https://attacker.example/codex/stable.json",
                }
            )

    def test_manifest_fetch_is_bounded_strict_and_same_origin(self) -> None:
        class Response(io.BytesIO):
            def __init__(self, payload: bytes, url: str) -> None:
                super().__init__(payload)
                self.url = url

            def geturl(self):
                return self.url

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        manager = self.manager()
        with self.assertRaisesRegex(IntegrationUpgradeError, "exceeds limit"):
            manager.fetch_manifest(
                opener=lambda _request, timeout: Response(
                    b"x" * (1024 * 1024 + 1),
                    self.catalog.manifest_url,
                )
            )
        with self.assertRaisesRegex(IntegrationUpgradeError, "manifest is invalid"):
            manager.fetch_manifest(
                opener=lambda _request, timeout: Response(
                    b'{"schema_version":1,"schema_version":1}',
                    self.catalog.manifest_url,
                )
            )
        with self.assertRaisesRegex(IntegrationUpgradeError, "manifest redirect"):
            manager.fetch_manifest(
                opener=lambda _request, timeout: Response(
                    json.dumps(manifest_data(self.package)).encode(),
                    "https://attacker.example/codex/stable.json",
                )
            )

    def test_check_refuses_to_hide_an_interrupted_transaction(self) -> None:
        class Response(io.BytesIO):
            def geturl(inner_self):
                return self.catalog.manifest_url

            def __enter__(inner_self):
                return inner_self

            def __exit__(inner_self, *_args):
                inner_self.close()

        manager = self.manager()
        pointer = self.state / "pointers" / "codex"
        pointer.mkdir(parents=True, mode=0o700)
        (pointer / "transaction.json").write_text("{}")
        (pointer / "transaction.json").chmod(0o600)

        with self.assertRaisesRegex(IntegrationUpgradeError, "recovery is required"):
            manager.check_for_update(
                opener=lambda _request, timeout: Response(
                    json.dumps(manifest_data(self.package)).encode()
                )
            )

    def test_plan_and_compatibility_evidence_tamper_fail_closed(self) -> None:
        manager = self.manager()
        plan = manager.plan(self.manifest, package=self.package)
        report = (
            self.state
            / "reports"
            / "codex"
            / "1.2.3"
            / f"{self.manifest.artifact_sha256}.json"
        )
        report.chmod(0o600)
        value = json.loads(report.read_text())
        value["checks"]["security"] = False
        report.write_text(json.dumps(value))
        report.chmod(0o400)

        with self.assertRaisesRegex(IntegrationUpgradeError, "evidence changed"):
            manager.apply(plan, approval_digest=plan.plan_digest)

    def test_interrupted_activation_recovers_prestate_and_quarantines_candidate(self) -> None:
        manager = self.manager()
        first = manager.plan(self.manifest, package=self.package)
        manager.apply(first, approval_digest=first.plan_digest)
        next_package = package_bytes("1.2.4")
        next_manifest = PackageManifest.load(
            manifest_data(next_package, version="1.2.4")
        )
        interrupted = self.manager(
            smoke_runner=raising_runner(KeyboardInterrupt())
        )
        plan = interrupted.plan(next_manifest, package=next_package)

        with self.assertRaises(KeyboardInterrupt):
            interrupted.apply(plan, approval_digest=plan.plan_digest)

        recovered = self.manager()
        self.assertTrue(recovered.status("codex")["transaction_pending"])
        outcome = recovered.recover_pending("codex")
        self.assertEqual(outcome["status"], "recovered")
        status = recovered.status("codex")
        self.assertEqual(status["current"]["version"], "1.2.3")
        self.assertFalse(status["transaction_pending"])
        self.assertEqual(status["quarantine"][0]["reason"], "interrupted-activation")

    def test_interrupted_rollback_restores_prestate_without_quarantining_target(self) -> None:
        manager = self.manager()
        first = manager.plan(self.manifest, package=self.package)
        manager.apply(first, approval_digest=first.plan_digest)
        next_manifest, _second = self._apply_second_version(manager)
        interrupted = self.manager(
            smoke_runner=raising_runner(KeyboardInterrupt())
        )

        with self.assertRaises(KeyboardInterrupt):
            interrupted.rollback(
                "codex",
                expected_current_artifact_sha256=next_manifest.artifact_sha256,
            )

        recovered = self.manager()
        recovered.recover_pending("codex")
        status = recovered.status("codex")
        self.assertEqual(status["current"]["version"], "1.2.4")
        self.assertEqual(status["authority"]["version"], "1.2.4")
        self.assertEqual(status["quarantine"], [])

    def test_download_rejects_untrusted_origin_before_opening_connection(self) -> None:
        manifest = PackageManifest.load(
            {
                **manifest_data(self.package),
                "source_url": "https://untrusted.example.test/package.json",
            }
        )
        opened = False

        def opener(_request, timeout):
            nonlocal opened
            opened = True
            raise AssertionError("network opener must not be called")

        with self.assertRaisesRegex(IntegrationUpgradeError, "trusted catalog origin"):
            self.manager().download_and_plan(manifest, opener=opener)
        self.assertFalse(opened)

    def test_direct_only_selection_does_not_replace_certified_authority(self) -> None:
        manager = self.manager()
        broker = manager.plan(self.manifest, package=self.package)
        manager.apply(broker, approval_digest=broker.plan_digest)
        direct_package = package_bytes("1.2.4")
        direct_manifest = PackageManifest.load(
            manifest_data(
                direct_package,
                version="1.2.4",
                transport_mode="direct-only",
            )
        )

        direct = manager.plan(direct_manifest, package=direct_package)
        manager.apply(direct, approval_digest=direct.plan_digest)

        status = manager.status("codex")
        self.assertEqual(status["current"]["version"], "1.2.4")
        self.assertEqual(status["authority"]["version"], "1.2.3")
        self.assertEqual(status["last_known_good"]["version"], "1.2.3")

    def test_plan_binds_catalog_policy_and_verified_source_identity(self) -> None:
        manager = self.manager()

        plan = manager.plan(self.manifest, package=self.package)

        self.assertEqual(plan.catalog_digest, self.catalog.catalog_digest)
        self.assertEqual(plan.policy_digest, self.policy.policy_digest)
        self.assertEqual(plan.source_verifier_digest, "1" * 64)
        self.assertEqual(plan.compatibility_runner_digest, "2" * 64)
        self.assertEqual(plan.smoke_runner_digest, "3" * 64)

        unverified = self.manager(
            state=self.state / "unverified",
            source_verifier=lambda manifest: {
                **passing_attestation(manifest),
                "verified": False,
            },
        )
        with self.assertRaisesRegex(IntegrationUpgradeError, "source attestation"):
            unverified.plan(self.manifest, package=self.package)

    def test_update_policy_rejects_downgrade_and_same_version_digest_conflict(self) -> None:
        manager = self.manager()
        first = manager.plan(self.manifest, package=self.package)
        manager.apply(first, approval_digest=first.plan_digest)

        conflicting_package = b"different package at the same version"
        conflicting = PackageManifest.load(
            manifest_data(conflicting_package, version="1.2.3")
        )
        with self.assertRaisesRegex(IntegrationUpgradeError, "version digest conflict"):
            manager.plan(conflicting, package=conflicting_package)

        older_package = package_bytes("1.2.2")
        older = PackageManifest.load(manifest_data(older_package, version="1.2.2"))
        with self.assertRaisesRegex(IntegrationUpgradeError, "downgrade"):
            manager.plan(older, package=older_package)

    def test_manual_policy_does_not_allow_automatic_application(self) -> None:
        manual_policy = IntegrationUpdatePolicy.load(
            {**policy_data(), "update_policy": "manual"}
        )
        manager = self.manager(policy=manual_policy)
        plan = manager.plan(self.manifest, package=self.package)

        with self.assertRaisesRegex(IntegrationUpgradeError, "manual approval"):
            manager.apply(plan, approval_digest=plan.plan_digest, automatic=True)

    def test_authority_set_digest_is_derived_from_certified_state(self) -> None:
        manager = self.manager()
        controller_owned = read_integration_authority_set_digest(
            self.state, ["codex"]
        )
        plan = manager.plan(self.manifest, package=self.package)
        manager.apply(plan, approval_digest=plan.plan_digest)

        certified = read_integration_authority_set_digest(self.state, ["codex"])

        self.assertRegex(controller_owned, r"^[0-9a-f]{64}$")
        self.assertRegex(certified, r"^[0-9a-f]{64}$")
        self.assertNotEqual(certified, controller_owned)
        transaction_path = self.state / "pointers" / "codex" / "transaction.json"
        transaction_path.write_text("{}")
        transaction_path.chmod(0o600)
        with self.assertRaisesRegex(
            IntegrationUpgradeError, "transaction is in progress"
        ):
            read_integration_authority_set_digest(self.state, ["codex"])
        transaction_path.unlink()
        authority_path = self.state / "pointers" / "codex" / "authority.json"
        authority = json.loads(authority_path.read_text())
        authority["caller_supplied"] = True
        authority_path.write_text(json.dumps(authority))
        with self.assertRaisesRegex(
            IntegrationUpgradeError, "authority record is invalid"
        ):
            read_integration_authority_set_digest(self.state, ["codex"])

    def test_generation_retention_preserves_current_and_last_known_good(self) -> None:
        policy = IntegrationUpdatePolicy.load(
            {**policy_data(), "retained_generations": 0}
        )
        manager = self.manager(policy=policy)
        plans = []
        for version in ("1.2.3", "1.2.4", "1.2.5"):
            package = package_bytes(version)
            manifest = PackageManifest.load(
                manifest_data(package, version=version)
            )
            plan = manager.plan(manifest, package=package)
            manager.apply(plan, approval_digest=plan.plan_digest)
            plans.append(plan)

        first, second, third = plans
        self.assertFalse((self.state / first.candidate_path).exists())
        self.assertFalse(
            (
                self.state
                / "artifact-plans"
                / "codex"
                / f"{first.manifest.artifact_sha256}.json"
            ).exists()
        )
        self.assertFalse(
            (self.state / "plans" / f"{first.plan_digest}.json").exists()
        )
        self.assertTrue((self.state / second.candidate_path).is_file())
        self.assertTrue((self.state / third.candidate_path).is_file())


class IntegrationUpgradeCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.package = package_bytes()
        self.package_path = self.root / "package.json"
        self.package_path.write_bytes(self.package)
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps(manifest_data(self.package)))
        self.catalog_path = self.root / "catalog.json"
        self.catalog_path.write_text(json.dumps(catalog_data()))
        self.policy_path = self.root / "policy.json"
        self.policy_path.write_text(json.dumps(policy_data()))
        self.runner = self.root / "integration-runner"
        self.runner.write_text(
            """#!/usr/bin/env python3
import json
import sys
request = json.load(sys.stdin)
manifest = request["manifest"]
operation = request["operation"]
if operation == "verify-source":
    catalog = request["catalog"]
    result = {
        "schema_version": 1,
        "catalog_id": catalog["catalog_id"],
        "harness_id": manifest["harness_id"],
        "version": manifest["version"],
        "artifact_sha256": manifest["artifact_sha256"],
        "publisher": manifest["publisher"],
        "source_url": manifest["source_url"],
        "verifier_identity": catalog["verifier_identity"],
        "verified": True,
    }
elif operation == "test-compatibility":
    result = {
        "schema_version": 1,
        "harness_id": manifest["harness_id"],
        "version": manifest["version"],
        "artifact_sha256": manifest["artifact_sha256"],
        "checks": {
            "disposable": True,
            "hook_schema": True,
            "transcript": True,
            "security": True,
            "broker_transport": manifest["transport_mode"] == "broker-v1",
        },
    }
elif operation == "smoke":
    result = {
        "schema_version": 1,
        "artifact_sha256": manifest["artifact_sha256"],
        "passed": True,
    }
else:
    raise SystemExit(2)
json.dump(result, sys.stdout, sort_keys=True)
"""
        )
        self.runner.chmod(0o700)
        self.plan_path = self.root / "plan.json"

    def command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        common = (
            "--catalog",
            str(self.catalog_path),
            "--update-policy",
            str(self.policy_path),
            "--source-verifier",
            str(self.runner),
            "--compatibility-runner",
            str(self.runner),
            "--smoke-runner",
            str(self.runner),
        )
        if (
            (len(arguments) > 1 and arguments[1] == "status")
            or "--recover-pending" in arguments
        ):
            common = ()
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin" / "hindsight-memory"),
                "--state-dir",
                str(self.state),
                *arguments[:2],
                *common,
                *arguments[2:],
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            check=False,
            timeout=30,
        )

    def test_plan_apply_status_and_rollback_surfaces(self) -> None:
        planned = self.command(
            "integration-upgrade",
            "plan",
            "--manifest",
            str(self.manifest_path),
            "--package",
            str(self.package_path),
            "--output",
            str(self.plan_path),
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        plan = json.loads(self.plan_path.read_text())

        applied = self.command(
            "integration-upgrade",
            "apply",
            "--plan",
            str(self.plan_path),
            "--approval-digest",
            plan["plan_digest"],
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)

        status = self.command(
            "integration-upgrade", "status", "--harness", "codex"
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["authority"]["version"], "1.2.3")

        second_package = package_bytes("1.2.4")
        second_package_path = self.root / "package-1.2.4.json"
        second_package_path.write_bytes(second_package)
        second_manifest_path = self.root / "manifest-1.2.4.json"
        second_manifest_path.write_text(
            json.dumps(manifest_data(second_package, version="1.2.4"))
        )
        second_plan_path = self.root / "plan-1.2.4.json"
        planned_second = self.command(
            "integration-upgrade",
            "plan",
            "--manifest",
            str(second_manifest_path),
            "--package",
            str(second_package_path),
            "--output",
            str(second_plan_path),
        )
        self.assertEqual(planned_second.returncode, 0, planned_second.stderr)
        second_plan = json.loads(second_plan_path.read_text())
        applied_second = self.command(
            "integration-upgrade",
            "apply",
            "--plan",
            str(second_plan_path),
            "--approval-digest",
            second_plan["plan_digest"],
        )
        self.assertEqual(applied_second.returncode, 0, applied_second.stderr)
        second_status = self.command(
            "integration-upgrade", "status", "--harness", "codex"
        )
        self.assertEqual(second_status.returncode, 0, second_status.stderr)
        self.assertEqual(
            json.loads(second_status.stdout)["authority"]["version"], "1.2.4"
        )

        rollback = self.command(
            "integration-upgrade",
            "rollback",
            "--harness",
            "codex",
            "--expected-current-artifact-sha256",
            json.loads(second_status.stdout)["current"]["artifact_sha256"],
        )
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        after_rollback = self.command(
            "integration-upgrade", "status", "--harness", "codex"
        )
        self.assertEqual(after_rollback.returncode, 0, after_rollback.stderr)
        rolled_back = json.loads(after_rollback.stdout)
        expected_digest = hashlib.sha256(self.package).hexdigest()
        self.assertEqual(rolled_back["current"]["artifact_sha256"], expected_digest)
        self.assertEqual(rolled_back["authority"]["artifact_sha256"], expected_digest)

    def test_runner_output_is_rejected_at_the_transport_cap(self) -> None:
        self.runner.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdout.write('x' * (1024 * 1024 + 1))\n"
        )
        self.runner.chmod(0o700)

        result = self.command(
            "integration-upgrade",
            "plan",
            "--manifest",
            str(self.manifest_path),
            "--package",
            str(self.package_path),
            "--output",
            str(self.plan_path),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("runner output exceeds limit", result.stderr)

    def test_pending_recovery_does_not_require_runner_bindings(self) -> None:
        self.state.mkdir(mode=0o700)

        result = self.command(
            "integration-upgrade",
            "apply",
            "--harness",
            "codex",
            "--recover-pending",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"harness_id": "codex", "status": "clean"},
        )


if __name__ == "__main__":
    unittest.main()
