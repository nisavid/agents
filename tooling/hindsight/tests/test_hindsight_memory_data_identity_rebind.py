from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"

sys.path.insert(0, str(LIB))
sys.path.insert(0, str(ROOT.parents[1]))

from hindsight_memory_control_plane.data_identity_rebind import (  # noqa: E402
    DataIdentityRebindError,
    create_rebind_plan,
    verify_rebind_backup_artifact,
    verify_rebind_evidence,
    verify_rebind_plan,
)
from hindsight_memory_control_plane.canonical import digest  # noqa: E402
from tooling.hindsight.tests.hindsight_data_identity_test_support import (  # noqa: E402
    build_rebind_evidence,
)


class DataIdentityRebindContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.artifact = self.root / "full-schema.dump.age"
        self.artifact.write_bytes(
            b"age-encryption.org/v1\nencrypted-full-schema-backup"
        )

    def evidence(self) -> dict[str, object]:
        return build_rebind_evidence(
            artifact=self.artifact,
            data_root=self.root / "data",
            data_root_device=11,
            data_root_inode=22,
            postgres_data_root=self.root / "data" / "data",
            postgres_data_device=11,
            postgres_data_inode=23,
            collected_at=950,
            expires_at=1200,
            backup_created_at=960,
            restored_at=980,
            postmaster_pid=100,
            postmaster_start_time=900,
        )

    def plan_arguments(self) -> dict[str, object]:
        return {
            "consumer_id": "hindsight-embed-service",
            "profile_id": "systalyze",
            "installation_state_digest": "1" * 64,
            "expected_post_state_digest": "2" * 64,
            "old_data_identity_digest": "3" * 64,
            "new_data_identity_digest": "4" * 64,
            "current_release_digest": "5" * 64,
            "binding_generation_digest": "6" * 64,
            "evidence": self.evidence(),
            "rollback_bundle_path": str(self.root / "rollback.json"),
            "authorization_receipt_path": str(self.root / "receipt.json"),
            "application_receipt_path": str(self.root / "application.json"),
            "verification_receipt_path": str(self.root / "verification.json"),
            "now": 1000,
        }

    def plan(self) -> dict[str, object]:
        return dict(create_rebind_plan(**self.plan_arguments()))

    def test_evidence_is_closed_and_cross_checked(self) -> None:
        evidence = self.evidence()

        verified = verify_rebind_evidence(evidence, now=1000)

        self.assertEqual(verified["database"]["codex_document_count"], 5)
        evidence["safety"]["target_bank_inspected"] = True
        with self.assertRaisesRegex(DataIdentityRebindError, "target-bank inspection"):
            verify_rebind_evidence(evidence, now=1000)

        evidence = self.evidence()
        evidence["schema_version"] = True
        with self.assertRaisesRegex(DataIdentityRebindError, "evidence is invalid"):
            verify_rebind_evidence(evidence, now=1000)
        evidence = self.evidence()
        evidence["database"]["bank_ids"] = [1, "codex"]
        with self.assertRaisesRegex(DataIdentityRebindError, "bank inventory"):
            verify_rebind_evidence(evidence, now=1000)

    def test_boolean_and_integer_fields_reject_python_subclass_aliases(self) -> None:
        evidence = self.evidence()
        evidence["backup"]["full_schema"] = 1
        with self.assertRaisesRegex(DataIdentityRebindError, "full-schema"):
            verify_rebind_evidence(evidence, now=1000)

        evidence = self.evidence()
        evidence["database"]["pending_operation_count"] = False
        with self.assertRaisesRegex(DataIdentityRebindError, "pending operation"):
            verify_rebind_evidence(evidence, now=1000)

    def test_database_and_backup_activity_guards_fail_closed(self) -> None:
        cases = (
            (
                "generation drift",
                lambda evidence: evidence["database"].update(
                    generation_after="generation-2"
                ),
                "generation changed",
            ),
            (
                "pending operation",
                lambda evidence: evidence["database"].update(pending_operation_count=1),
                "pending operations",
            ),
            (
                "generic import",
                lambda evidence: evidence["database"].update(
                    generic_import_receipt_count=1
                ),
                "generic import authority",
            ),
            (
                "duplicate banks",
                lambda evidence: evidence["database"].update(
                    bank_ids=["codex", "engineering", "engineering"]
                ),
                "bank inventory",
            ),
            (
                "unsorted banks",
                lambda evidence: evidence["database"].update(
                    bank_ids=["engineering", "codex"]
                ),
                "bank inventory",
            ),
            (
                "backup before collection",
                lambda evidence: evidence["backup"].update(created_at=949),
                "backup creation time",
            ),
            (
                "backup after observation",
                lambda evidence: evidence["backup"].update(created_at=1001),
                "backup creation time",
            ),
            (
                "restore after observation",
                lambda evidence: evidence["restore"].update(restored_at=1001),
                "restore verification",
            ),
        )
        for name, mutate, message in cases:
            evidence = self.evidence()
            mutate(evidence)
            with (
                self.subTest(case=name),
                self.assertRaisesRegex(DataIdentityRebindError, message),
            ):
                verify_rebind_evidence(evidence, now=1000)

    def test_backup_requires_age_encryption_and_matching_restore_identity(
        self,
    ) -> None:
        self.artifact.write_bytes(b"plaintext-database-dump")
        with self.assertRaisesRegex(DataIdentityRebindError, "not age ciphertext"):
            verify_rebind_evidence(self.evidence(), now=1000)

        self.artifact.write_bytes(
            b"age-encryption.org/v1\nencrypted-full-schema-backup"
        )
        evidence = self.evidence()
        evidence["backup"]["encryption_format"] = "plaintext"
        with self.assertRaisesRegex(DataIdentityRebindError, "encryption format"):
            verify_rebind_evidence(evidence, now=1000)

        evidence = self.evidence()
        evidence["backup"]["plaintext_disposed"] = False
        with self.assertRaisesRegex(DataIdentityRebindError, "plaintext disposal"):
            verify_rebind_evidence(evidence, now=1000)

        evidence = self.evidence()
        evidence["restore"]["decryption_recipient_digest"] = "e" * 64
        with self.assertRaisesRegex(
            DataIdentityRebindError, "disposable restore differs"
        ):
            verify_rebind_evidence(evidence, now=1000)

    def test_backup_artifact_and_restore_must_match_live_snapshot(self) -> None:
        evidence = self.evidence()
        size = self.artifact.stat().st_size
        header = b"age-encryption.org/v1\n"
        self.artifact.write_bytes(header + b"x" * (size - len(header)))
        with self.assertRaisesRegex(DataIdentityRebindError, "artifact digest"):
            verify_rebind_evidence(evidence, now=1000)

        evidence = self.evidence()
        evidence["backup"]["ciphertext_size"] += 1
        with self.assertRaisesRegex(DataIdentityRebindError, "artifact is unavailable"):
            verify_rebind_evidence(evidence, now=1000)

        evidence = self.evidence()
        evidence["restore"]["codex_manifest_digest"] = "d" * 64
        with self.assertRaisesRegex(DataIdentityRebindError, "restore differs"):
            verify_rebind_evidence(evidence, now=1000)

    def test_public_backup_verifier_rehashes_the_planned_artifact(self) -> None:
        plan = self.plan()
        self.assertEqual(
            verify_rebind_backup_artifact(plan["backup"]),
            plan["backup"],
        )
        size = self.artifact.stat().st_size
        header = b"age-encryption.org/v1\n"
        self.artifact.write_bytes(header + b"x" * (size - len(header)))
        with self.assertRaisesRegex(DataIdentityRebindError, "artifact digest"):
            verify_rebind_backup_artifact(plan["backup"])

    def test_artifact_probe_can_be_explicitly_skipped(self) -> None:
        evidence = self.evidence()
        self.artifact.unlink()
        with self.assertRaisesRegex(DataIdentityRebindError, "artifact is unavailable"):
            verify_rebind_evidence(evidence, now=1000)
        self.assertEqual(
            verify_rebind_evidence(
                evidence,
                now=1000,
                verify_artifact=False,
            )["profile_id"],
            "systalyze",
        )

    def test_closed_subschemas_and_canonical_json_fail_closed(self) -> None:
        evidence = self.evidence()
        evidence["backup"]["extra"] = "field"
        with self.assertRaisesRegex(DataIdentityRebindError, "backup evidence"):
            verify_rebind_evidence(evidence, now=1000)
        evidence = self.evidence()
        del evidence["restore"]["dropped"]
        with self.assertRaisesRegex(DataIdentityRebindError, "restore evidence"):
            verify_rebind_evidence(evidence, now=1000)
        with self.assertRaisesRegex(DataIdentityRebindError, "not canonical JSON"):
            verify_rebind_evidence({"invalid": object()}, now=1000)
        recursive: dict[str, object] = {}
        recursive["recursive"] = recursive
        with self.assertRaisesRegex(DataIdentityRebindError, "not canonical JSON"):
            verify_rebind_evidence(recursive, now=1000)

    def test_postgres_data_root_must_be_bound_to_data_root(self) -> None:
        evidence = self.evidence()
        evidence["postgres"]["postgres_data_root"] = str(self.root / "other")
        with self.assertRaisesRegex(DataIdentityRebindError, "not bound"):
            verify_rebind_evidence(evidence, now=1000)

    def test_external_and_parent_symlink_artifacts_are_rejected(self) -> None:
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        external_root = Path(external.name).resolve()
        outside = external_root / "outside.age"
        outside.write_bytes(self.artifact.read_bytes())
        evidence = self.evidence()
        evidence["backup"]["artifact_path"] = str(outside)
        with self.assertRaisesRegex(DataIdentityRebindError, "artifact is unavailable"):
            verify_rebind_evidence(evidence, now=1000)
        with self.assertRaisesRegex(DataIdentityRebindError, "artifact is unavailable"):
            verify_rebind_evidence(
                evidence,
                now=1000,
                verify_artifact=False,
            )

        outside_directory = external_root / "escaped"
        outside_directory.mkdir()
        escaped = outside_directory / "backup.age"
        escaped.write_bytes(self.artifact.read_bytes())
        link = self.root / "escape"
        try:
            link.symlink_to(outside_directory, target_is_directory=True)
        except (AttributeError, OSError) as error:
            self.skipTest(f"symlink setup unavailable: {error}")
        evidence = self.evidence()
        evidence["backup"]["artifact_path"] = str(link / "backup.age")
        with self.assertRaisesRegex(DataIdentityRebindError, "artifact is unavailable"):
            verify_rebind_evidence(evidence, now=1000)

    def test_artifact_root_device_and_inode_are_pinned(self) -> None:
        evidence = self.evidence()
        evidence["backup"]["artifact_root_device"] += 1
        with self.assertRaisesRegex(DataIdentityRebindError, "artifact is unavailable"):
            verify_rebind_evidence(evidence, now=1000)
        evidence = self.evidence()
        evidence["backup"]["artifact_root_inode"] += 1
        with self.assertRaisesRegex(DataIdentityRebindError, "artifact is unavailable"):
            verify_rebind_evidence(evidence, now=1000)

    def test_identity_binding_digests_are_enforced(self) -> None:
        evidence = self.evidence()
        evidence["database"]["codex_document_count"] = 6
        evidence["restore"]["codex_document_count"] = 6
        with self.assertRaisesRegex(DataIdentityRebindError, "snapshot digest differs"):
            verify_rebind_evidence(evidence, now=1000)

        evidence = self.evidence()
        evidence["postgres"]["postmaster_pid"] = 101
        with self.assertRaisesRegex(
            DataIdentityRebindError, "connection identity digest differs"
        ):
            verify_rebind_evidence(evidence, now=1000)

        evidence = self.evidence()
        evidence["restore"]["restore_identity_digest"] = evidence["postgres"][
            "connection_identity_digest"
        ]
        with self.assertRaisesRegex(DataIdentityRebindError, "matches live"):
            verify_rebind_evidence(evidence, now=1000)

        evidence = self.evidence()
        evidence["postgres"]["system_identifier"] = "not-decimal"
        with self.assertRaisesRegex(DataIdentityRebindError, "system identifier"):
            verify_rebind_evidence(evidence, now=1000)

    def test_backup_artifact_rejects_final_component_symlink(self) -> None:
        evidence = self.evidence()
        target = self.root / "actual-backup.age"
        target.write_bytes(self.artifact.read_bytes())
        self.artifact.unlink()
        try:
            self.artifact.symlink_to(target)
        except (AttributeError, OSError) as error:
            self.skipTest(f"symlink setup unavailable: {error}")

        with self.assertRaisesRegex(DataIdentityRebindError, "artifact is unavailable"):
            verify_rebind_evidence(evidence, now=1000)

    def test_backup_artifact_rejects_fifo_without_blocking(self) -> None:
        evidence = self.evidence()
        self.artifact.unlink()
        try:
            os.mkfifo(self.artifact)
        except (AttributeError, OSError) as error:
            self.skipTest(f"FIFO setup unavailable: {error}")

        with self.assertRaisesRegex(DataIdentityRebindError, "artifact is unavailable"):
            verify_rebind_evidence(evidence, now=1000)

    def test_plan_is_expiring_digest_bound_and_rejects_noop(self) -> None:
        plan = self.plan()
        self.assertEqual(verify_rebind_plan(plan, now=1000), plan)

        changed = dict(plan)
        changed["new_data_identity_digest"] = "7" * 64
        with self.assertRaisesRegex(DataIdentityRebindError, "digest differs"):
            verify_rebind_plan(changed, now=1000)
        with self.assertRaisesRegex(DataIdentityRebindError, "expired"):
            verify_rebind_plan(plan, now=1200)
        self.assertEqual(
            verify_rebind_plan(plan, now=1200, allow_expired=True),
            plan,
        )
        missing = dict(plan)
        del missing["action"]
        with self.assertRaisesRegex(DataIdentityRebindError, "plan is invalid"):
            verify_rebind_plan(missing, now=1000)
        extra = {**plan, "unexpected": True}
        with self.assertRaisesRegex(DataIdentityRebindError, "plan is invalid"):
            verify_rebind_plan(extra, now=1000)
        boolean_schema = {**plan, "schema_version": True}
        with self.assertRaisesRegex(DataIdentityRebindError, "plan is invalid"):
            verify_rebind_plan(boolean_schema, now=1000)
        noop = {
            **plan,
            "new_data_identity_digest": plan["old_data_identity_digest"],
        }
        noop["plan_digest"] = digest(
            {key: value for key, value in noop.items() if key != "plan_digest"}
        )
        with self.assertRaisesRegex(DataIdentityRebindError, "has not changed"):
            verify_rebind_plan(noop, now=1000)

        mismatched_backup = {
            **plan,
            "backup_artifact_digest": "9" * 64,
        }
        mismatched_backup["plan_digest"] = digest(
            {
                key: value
                for key, value in mismatched_backup.items()
                if key != "plan_digest"
            }
        )
        with self.assertRaisesRegex(
            DataIdentityRebindError, "plan backup artifact digest differs"
        ):
            verify_rebind_plan(mismatched_backup, now=1000)

        nondecimal_system_identifier = {
            **plan,
            "postgres_system_identifier": "not-decimal",
        }
        nondecimal_system_identifier["plan_digest"] = digest(
            {
                key: value
                for key, value in nondecimal_system_identifier.items()
                if key != "plan_digest"
            }
        )
        with self.assertRaisesRegex(DataIdentityRebindError, "system identifier"):
            verify_rebind_plan(nondecimal_system_identifier, now=1000)

        with self.assertRaisesRegex(DataIdentityRebindError, "has not changed"):
            create_rebind_plan(
                consumer_id="hindsight-embed-service",
                profile_id="systalyze",
                installation_state_digest="1" * 64,
                expected_post_state_digest="2" * 64,
                old_data_identity_digest="3" * 64,
                new_data_identity_digest="3" * 64,
                current_release_digest="5" * 64,
                binding_generation_digest="6" * 64,
                evidence=self.evidence(),
                rollback_bundle_path=str(self.root / "rollback.json"),
                authorization_receipt_path=str(self.root / "receipt.json"),
                application_receipt_path=str(self.root / "application.json"),
                verification_receipt_path=str(self.root / "verification.json"),
                now=1000,
            )

    def test_evidence_windows_profile_and_paths_are_bound(self) -> None:
        evidence = self.evidence()
        evidence["expires_at"] = evidence["collected_at"]
        with self.assertRaisesRegex(DataIdentityRebindError, "evidence is expired"):
            verify_rebind_evidence(evidence, now=1000)
        evidence = self.evidence()
        evidence["expires_at"] = evidence["collected_at"] + 3601
        with self.assertRaisesRegex(DataIdentityRebindError, "evidence is expired"):
            verify_rebind_evidence(evidence, now=1000)
        evidence = self.evidence()
        evidence["restore"]["restored_at"] = evidence["backup"]["created_at"] - 1
        with self.assertRaisesRegex(DataIdentityRebindError, "restore verification"):
            verify_rebind_evidence(evidence, now=1000)
        evidence = self.evidence()
        evidence["restore"]["restored_at"] = evidence["expires_at"] + 1
        with self.assertRaisesRegex(DataIdentityRebindError, "restore verification"):
            verify_rebind_evidence(evidence, now=1000)
        with self.assertRaisesRegex(DataIdentityRebindError, "evidence is expired"):
            verify_rebind_evidence(self.evidence(), now=949)

        arguments = self.plan_arguments()
        arguments["profile_id"] = "other"
        with self.assertRaisesRegex(DataIdentityRebindError, "profile differs"):
            create_rebind_plan(**arguments)
        arguments = self.plan_arguments()
        arguments["rollback_bundle_path"] = "relative.json"
        with self.assertRaisesRegex(DataIdentityRebindError, "rollback bundle path"):
            create_rebind_plan(**arguments)
        arguments = self.plan_arguments()
        arguments["rollback_bundle_path"] = str(self.root / "rollback\u0085bundle.json")
        with self.assertRaisesRegex(DataIdentityRebindError, "rollback bundle path"):
            create_rebind_plan(**arguments)
        arguments = self.plan_arguments()
        arguments["rollback_bundle_path"] = str(self.root / ".." / "rollback.json")
        with self.assertRaisesRegex(DataIdentityRebindError, "rollback bundle path"):
            create_rebind_plan(**arguments)

        long_lived = self.plan()
        long_lived["expires_at"] = long_lived["created_at"] + 3601
        long_lived["plan_digest"] = digest(
            {key: value for key, value in long_lived.items() if key != "plan_digest"}
        )
        for allow_expired in (False, True):
            with (
                self.subTest(allow_expired=allow_expired),
                self.assertRaisesRegex(DataIdentityRebindError, "plan is expired"),
            ):
                verify_rebind_plan(
                    long_lived,
                    now=1000,
                    allow_expired=allow_expired,
                )


if __name__ == "__main__":
    unittest.main()
