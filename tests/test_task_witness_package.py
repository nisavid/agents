from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY / "scripts" / "validate_task_witness.py"
AGENT_PLUGINS_STANDARD = REPOSITORY / "scripts" / "agent_plugins_standard.py"
PUBLIC_RELEASE_VALIDATOR = REPOSITORY / "scripts" / "validate_public_release.py"
AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
CANONICAL_MANIFEST_KEYS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
CANONICAL_IDENTITY_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)
SOURCE_SHAPE_RECORD = Path("release/task-witness/source-shape-review.json")
PUBLIC_RELEASE_REGISTRATION = Path(
    "release/task-witness/public-release-registration.json"
)
SUITE_INVENTORY = Path("release/task-witness/tw4-suite-inventory.json")
SUITE_PROJECTIONS = (
    ("client-common", "common", ("macos-arm64", "linux-x86_64")),
    ("deployment-common", "common", ("macos-arm64", "linux-x86_64")),
    ("package-contract", "common", ("macos-arm64", "linux-x86_64")),
    ("qualification-runner-contract", "common", ("macos-arm64", "linux-x86_64")),
    ("task-witness-source-stage", "common", ("macos-arm64", "linux-x86_64")),
    ("public-release-source-stage", "common", ("macos-arm64", "linux-x86_64")),
    ("forward-update", "portable-vertical", ("macos-arm64", "linux-x86_64")),
    (
        "authorized-downgrade-and-manual-rollback",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "candidate-rejection-rollback",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "candidate-source-disappearance",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "provider-cache-deletion-and-movement",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "literal-rendered-shim",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "migration-freeze5-to-bridge",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "migration-bridge-to-tw4",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    ("macos-acl", "platform-vertical", ("macos-arm64",)),
    ("linux-process-supervision", "platform-vertical", ("linux-x86_64",)),
)
SUITE_EXPECTED_COUNTS = {
    "client-common": 321,
    "deployment-common": 203,
    "package-contract": 71,
    "qualification-runner-contract": 7,
    "task-witness-source-stage": 1,
    "public-release-source-stage": 1,
    "forward-update": 53,
    "authorized-downgrade-and-manual-rollback": 18,
    "candidate-rejection-rollback": 11,
    "candidate-source-disappearance": 1,
    "provider-cache-deletion-and-movement": 1,
    "literal-rendered-shim": 1,
    "migration-freeze5-to-bridge": 11,
    "migration-bridge-to-tw4": 15,
    "macos-acl": 12,
    "linux-process-supervision": 3,
}
BRIDGE_IDENTITY = Path("release/task-witness/tw4-bridge-identity.json")
BRIDGE_PROVENANCE = Path("release/task-witness/tw4-bridge-provenance.json")
MIGRATION_SNAPSHOT_PATHS = (
    "release/task-witness/migration/freeze5/controller/task_witness_deploy.py",
    "release/task-witness/migration/freeze5/controller/policy.json",
    "release/task-witness/migration/freeze5/client/task_witness_client.py",
    "release/task-witness/migration/freeze5/.claude-plugin/plugin.json",
    "release/task-witness/migration/freeze5/.codex-plugin/plugin.json",
    "release/task-witness/migration/bridge/controller/task_witness_deploy.py",
    "release/task-witness/migration/bridge/client/task_witness_client.py",
    "release/task-witness/migration/bridge/.claude-plugin/plugin.json",
    "release/task-witness/migration/bridge/.codex-plugin/plugin.json",
)
RELEASE_VALIDATOR_PATHS = ("scripts/validate_task_witness.py",)
PUBLIC_RELEASE_REGISTRATION_PATHS = (PUBLIC_RELEASE_REGISTRATION.as_posix(),)
RELEASE_DOCUMENTATION_PATHS = (
    "docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md",
    "docs/superpowers/specs/2026-08-12-task-witness-tw4-migration-and-qualification-design.md",
)
TW4_MIGRATION_EVIDENCE_PATHS = tuple(
    sorted(
        (
            BRIDGE_IDENTITY.as_posix(),
            BRIDGE_PROVENANCE.as_posix(),
            *MIGRATION_SNAPSHOT_PATHS,
        )
    )
)
TW4_QUALIFICATION_CONTRACT_PATHS = (
    SUITE_INVENTORY.as_posix(),
    "scripts/run_task_witness_qualification.py",
    "scripts/run_task_witness_qualification_suite.py",
    "tests/test_task_witness_qualification.py",
)
RELEASE_INTEGRATION_TEST_PATHS = (
    "tests/test_task_witness_package.py",
    "tests/test_task_witness_qualification.py",
)
PUBLIC_RELEASE_SUPPORT_PATHS = (
    *RELEASE_DOCUMENTATION_PATHS,
    "release/task-witness/migration",
    SOURCE_SHAPE_RECORD.as_posix(),
    BRIDGE_IDENTITY.as_posix(),
    BRIDGE_PROVENANCE.as_posix(),
    SUITE_INVENTORY.as_posix(),
    "scripts/run_task_witness_qualification.py",
    "scripts/run_task_witness_qualification_suite.py",
    "tests/plugins/task_witness_client",
    "tests/plugins/task_witness_deployment",
    "tests/test_task_witness_package.py",
    "tests/test_task_witness_qualification.py",
)
TW0_SOURCE_PATHS = (
    "plugins/task-witness/launcher/task_witness_launch.py",
    "plugins/task-witness/runtime/bundle_io.py",
    "plugins/task-witness/runtime/canonical.py",
    "plugins/task-witness/runtime/task_witness.py",
    "plugins/task-witness/runtime/trust.py",
)
TW1_CLIENT_PATHS = (
    "plugins/task-witness/client/task_witness_client.py",
    "plugins/task-witness/client/task_witness_shim.sh.in",
)
TW2_CONTROL_PLANE_PATHS = (
    "plugins/task-witness/controller/policy.json",
    "plugins/task-witness/controller/task_witness_deploy.py",
    "plugins/task-witness/smoke/task_witness_smoke_validator.py",
)
CONTROL_SET_PATHS = tuple(
    sorted(TW0_SOURCE_PATHS + TW1_CLIENT_PATHS + TW2_CONTROL_PLANE_PATHS)
)
CLIENT_TEST_PATHS = (
    "tests/plugins/task_witness_client/__init__.py",
    "tests/plugins/task_witness_client/_activation_smoke_driver.py",
    "tests/plugins/task_witness_client/_client_driver.py",
    "tests/plugins/task_witness_client/_control_maintenance_smoke_support.py",
    "tests/plugins/task_witness_client/_invocation_profile_driver.py",
    "tests/plugins/task_witness_client/_launcher_behavior_driver.py",
    "tests/plugins/task_witness_client/_launcher_module_driver.py",
    "tests/plugins/task_witness_client/_process_supervision_driver.py",
    "tests/plugins/task_witness_client/_retained_state_driver.py",
    "tests/plugins/task_witness_client/_shim_observer_driver.py",
    "tests/plugins/task_witness_client/_support.py",
    "tests/plugins/task_witness_client/_terminal_output_driver.py",
    "tests/plugins/task_witness_client/_writer_guard_driver.py",
    "tests/plugins/task_witness_client/test_activation_smoke.py",
    "tests/plugins/task_witness_client/test_compatibility_policy_v2.py",
    "tests/plugins/task_witness_client/test_control_maintenance_smoke.py",
    "tests/plugins/task_witness_client/test_invocation_profile.py",
    "tests/plugins/task_witness_client/test_launcher.py",
    "tests/plugins/task_witness_client/test_process_supervision.py",
    "tests/plugins/task_witness_client/test_retained_state.py",
    "tests/plugins/task_witness_client/test_runtime.py",
    "tests/plugins/task_witness_client/test_runtime_acceptance.py",
    "tests/plugins/task_witness_client/test_shim.py",
    "tests/plugins/task_witness_client/test_terminal_output.py",
)
CLIENT_TEST_GROWTH_PATH = "tests/plugins/task_witness_client/test_invocation_profile.py"
DEPLOYMENT_TEST_PATHS = (
    "tests/plugins/task_witness_deployment/__init__.py",
    "tests/plugins/task_witness_deployment/_activation_recovery_support.py",
    "tests/plugins/task_witness_deployment/_activation_support.py",
    "tests/plugins/task_witness_deployment/_bridge_transition_activation_support.py",
    "tests/plugins/task_witness_deployment/_bridge_transition_prepare_support.py",
    "tests/plugins/task_witness_deployment/_bridge_transition_stage_support.py",
    "tests/plugins/task_witness_deployment/_control_maintenance_activation_support.py",
    "tests/plugins/task_witness_deployment/_control_maintenance_followup_support.py",
    "tests/plugins/task_witness_deployment/_control_maintenance_staged_client_support.py",
    "tests/plugins/task_witness_deployment/_control_maintenance_support.py",
    "tests/plugins/task_witness_deployment/_freeze5_upgrade_recovery_support.py",
    "tests/plugins/task_witness_deployment/_manual_rollback_support.py",
    "tests/plugins/task_witness_deployment/_provider_cache_deletion_and_movement_support.py",
    "tests/plugins/task_witness_deployment/_routine_activation_support.py",
    "tests/plugins/task_witness_deployment/_routine_staged_client_support.py",
    "tests/plugins/task_witness_deployment/_routine_support.py",
    "tests/plugins/task_witness_deployment/_source_evidence_support.py",
    "tests/plugins/task_witness_deployment/_source_recovery_support.py",
    "tests/plugins/task_witness_deployment/_source_transition_support.py",
    "tests/plugins/task_witness_deployment/_support.py",
    "tests/plugins/task_witness_deployment/test_activation_recovery.py",
    "tests/plugins/task_witness_deployment/test_activation_recovery_validation.py",
    "tests/plugins/task_witness_deployment/test_activation_transactions.py",
    "tests/plugins/task_witness_deployment/test_agent_plugins_source_receipts.py",
    "tests/plugins/task_witness_deployment/test_bridge_transition_activation.py",
    "tests/plugins/task_witness_deployment/test_bridge_transition_preparation.py",
    "tests/plugins/task_witness_deployment/test_bridge_transition_staging.py",
    "tests/plugins/task_witness_deployment/test_control_maintenance_activation.py",
    "tests/plugins/task_witness_deployment/test_control_maintenance_followup.py",
    "tests/plugins/task_witness_deployment/test_control_maintenance_staged_client_integration.py",
    "tests/plugins/task_witness_deployment/test_control_maintenance_staging.py",
    "tests/plugins/task_witness_deployment/test_freeze5_upgrade_recovery.py",
    "tests/plugins/task_witness_deployment/test_manual_rollback_activation.py",
    "tests/plugins/task_witness_deployment/test_manual_rollback_preparation.py",
    "tests/plugins/task_witness_deployment/test_manual_rollback_recovery.py",
    "tests/plugins/task_witness_deployment/test_provider_cache_deletion_and_movement.py",
    "tests/plugins/task_witness_deployment/test_provider_import.py",
    "tests/plugins/task_witness_deployment/test_receipt_staging.py",
    "tests/plugins/task_witness_deployment/test_routine_activation_recovery.py",
    "tests/plugins/task_witness_deployment/test_routine_staged_client_integration.py",
    "tests/plugins/task_witness_deployment/test_routine_transactions.py",
    "tests/plugins/task_witness_deployment/test_source_evidence_first_install.py",
    "tests/plugins/task_witness_deployment/test_source_evidence_recovery.py",
    "tests/plugins/task_witness_deployment/test_source_evidence_transitions.py",
    "tests/plugins/task_witness_deployment/test_staged_client_integration.py",
    "tests/plugins/task_witness_deployment/test_transaction_result_reconciliation.py",
)
DIRECT_TEST_PATHS = tuple(
    sorted(
        CLIENT_TEST_PATHS
        + DEPLOYMENT_TEST_PATHS
        + RELEASE_INTEGRATION_TEST_PATHS
    )
)
REVIEWED_PATHS = tuple(
    sorted(
        set(
            CONTROL_SET_PATHS
            + DIRECT_TEST_PATHS
            + RELEASE_VALIDATOR_PATHS
            + PUBLIC_RELEASE_REGISTRATION_PATHS
            + RELEASE_DOCUMENTATION_PATHS
            + TW4_MIGRATION_EVIDENCE_PATHS
            + TW4_QUALIFICATION_CONTRACT_PATHS
        )
    )
)


class TaskWitnessPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary.name).resolve() / "repository"
        self.plugin = self.repository / "plugins" / "task-witness"
        self.direct_test_package = self.repository / "tests/plugins/task_witness_client"
        self.deployment_test_package = (
            self.repository / "tests/plugins/task_witness_deployment"
        )
        self.neutral_test_package = self.repository / "tests/plugins/neutral_package"
        shutil.copytree(REPOSITORY / "plugins" / "task-witness", self.plugin)
        (self.repository / "scripts").mkdir(parents=True)
        shutil.copy2(VALIDATOR, self.repository / "scripts" / VALIDATOR.name)
        shutil.copy2(
            AGENT_PLUGINS_STANDARD,
            self.repository / "scripts" / AGENT_PLUGINS_STANDARD.name,
        )
        record = self.repository / SOURCE_SHAPE_RECORD
        record.parent.mkdir(parents=True)
        shutil.copy2(REPOSITORY / SOURCE_SHAPE_RECORD, record)
        registration = self.repository / PUBLIC_RELEASE_REGISTRATION
        registration.parent.mkdir(parents=True, exist_ok=True)
        registration.write_text(
            json.dumps(
                {
                    "production_eligible": False,
                    "schema_version": 1,
                    "source_stage_validator_flags": ["--source-stage"],
                    "support_paths": list(PUBLIC_RELEASE_SUPPORT_PATHS),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        for relative in (BRIDGE_IDENTITY, BRIDGE_PROVENANCE, *MIGRATION_SNAPSHOT_PATHS):
            target = self.repository / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY / relative, target)
        for relative in DIRECT_TEST_PATHS:
            target = self.repository / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY / relative, target)
        for relative in (
            *RELEASE_DOCUMENTATION_PATHS,
            "scripts/run_task_witness_qualification.py",
            "scripts/run_task_witness_qualification_suite.py",
        ):
            target = self.repository / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY / relative, target)
        self.write_suite_inventory()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(self.repository / "scripts" / VALIDATOR.name),
                str(self.repository),
                *flags,
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def write_suite_inventory(self) -> dict[str, object]:
        entries = [
            {
                "argv": [
                    "-I",
                    "-B",
                    "scripts/run_task_witness_qualification_suite.py",
                    "--suite",
                    suite_id,
                ],
                "executor": {"kind": "qualified-cpython"},
                "expected_count": SUITE_EXPECTED_COUNTS[suite_id],
                "expected_terminal": "passed",
                "id": suite_id,
                "phase": phase,
                "targets": list(targets),
            }
            for suite_id, phase, targets in SUITE_PROJECTIONS
        ]
        counts = [
            {"expected_count": entry["expected_count"], "id": entry["id"]}
            for entry in entries
        ]

        def canonical(value: object) -> bytes:
            return json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")

        value: dict[str, object] = {
            "schema_version": 1,
            "contract": "task-witness-tw4-suite-inventory-v1",
            "entries": entries,
            "aggregates": {
                "counts_sha256": hashlib.sha256(canonical(counts)).hexdigest(),
                "entries_sha256": hashlib.sha256(canonical(entries)).hexdigest(),
                "entry_count": len(entries),
                "expected_count_total": sum(
                    int(entry["expected_count"]) for entry in entries
                ),
            },
        }
        path = self.repository / SUITE_INVENTORY
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(value))
        return value

    def validate_with_reference(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                str(self.repository),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, expected: str) -> None:
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, result.stderr)

    def validate_direct_test_inventory(self) -> None:
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        validator["validate_direct_test_inventory"](self.repository)

    def validate_bridge_history(self) -> None:
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        validator["validate_bridge_history"](self.repository)

    def assert_migration_inventory_rejected(
        self,
        mutation: Callable[[Path], object],
        expected: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory).resolve() / "repository"
            shutil.copytree(self.repository, repository)
            original = self.repository
            self.repository = repository
            try:
                mutation(repository / "release/task-witness/migration")
                with self.assertRaisesRegex(ValueError, expected):
                    self.validate_bridge_history()
            finally:
                self.repository = original

    @staticmethod
    def content_document(value: dict[str, object]) -> dict[str, object]:
        return {
            **value,
            "content_sha256": hashlib.sha256(
                json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
        }

    def bridge_history_projection(self) -> dict[str, object]:
        identity_raw = (self.repository / BRIDGE_IDENTITY).read_bytes()
        provenance_raw = (self.repository / BRIDGE_PROVENANCE).read_bytes()
        identity = json.loads(identity_raw)
        return {
            "bridge_identity_sha256": hashlib.sha256(identity_raw).hexdigest(),
            "bridge_provenance_sha256": hashlib.sha256(provenance_raw).hexdigest(),
            "freeze5": identity["freeze5"],
            "bridge": identity["bridge"],
        }

    def release_manifest_document(self) -> dict[str, object]:
        return self.content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-tw4-release-manifest-v1",
                "qualification_candidate": {
                    "repository_id": "nisavid/agents",
                    "commit_sha1": "1" * 40,
                    "tree_sha1": "2" * 40,
                    "plugin_subtree_sha256": "3" * 64,
                    "suite_inventory_sha256": "4" * 64,
                },
                "targets": {
                    "linux-x86_64": "5" * 64,
                    "macos-arm64": "6" * 64,
                },
                "bridge_history": self.bridge_history_projection(),
                "canonical_review_evidence_sha256": "7" * 64,
                "final_public_release": {
                    "commit_sha1": "8" * 40,
                    "tree_sha1": "9" * 40,
                },
                "migration_edge": {
                    "from": "freeze5",
                    "source_mode": "harness_snapshot",
                    "to": "bridge",
                    "successor": "tw4",
                },
                "promotion_delta_sha256": "a" * 64,
                "disposition": "release-qualified",
            }
        )

    @staticmethod
    def stat_document(*, directory: bool = False) -> dict[str, object]:
        return {
            "device": 1,
            "inode": 2,
            "mode": (stat.S_IFDIR | 0o700) if directory else (stat.S_IFREG | 0o500),
            "uid": 502,
            "gid": 20,
            "nlink": 1,
            "size": 0,
            "mtime_ns": 3,
            "ctime_ns": 4,
        }

    @classmethod
    def directory_binding(cls, path: str) -> dict[str, object]:
        return {"path": path, "stat": cls.stat_document(directory=True)}

    @classmethod
    def regular_binding(
        cls,
        path: str,
        *,
        length: int,
        sha256: str,
        mode: int = 0o500,
    ) -> dict[str, object]:
        metadata = cls.stat_document()
        metadata["size"] = length
        metadata["mode"] = stat.S_IFREG | mode
        return {"path": path, "stat": metadata, "length": length, "sha256": sha256}

    def host_receipt_document(self, target: str = "macos-arm64") -> dict[str, object]:
        uid = 502
        gid = 20
        home = "/Users/task-witness-qualification"
        candidate_root = "/srv/task-witness-candidate"
        profile = self.content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-platform-profile-v1",
                "target": target,
                "execution_environment": "native",
                "platform": {
                    "system": "darwin" if target == "macos-arm64" else "linux",
                    "machine": "arm64" if target == "macos-arm64" else "x86_64",
                    "qualified_filesystem_class": "local-private-filesystem",
                },
                "passwd_user": {
                    "purpose": "task-witness-disposable-qualification-v1",
                    "name": "task-witness-qualification",
                    "uid": uid,
                    "primary_gid": gid,
                    "supplementary_gids": [],
                    "home": home,
                    "provisioning_evidence_sha256": "1" * 64,
                },
                "native_evidence": {
                    "issuer": "operator-host-audit",
                    "provenance": "native-host-inspection",
                    "qualification_class": "task-witness-native-host-v1",
                    "evidence_sha256": "2" * 64,
                    "container": False,
                    "emulation": False,
                },
                "filesystem": {
                    "type": "apfs" if target == "macos-arm64" else "ext4",
                    "evidence_sha256": "3" * 64,
                    "required_semantics": [
                        "advisory-flock-open-file-description",
                        "atomic-same-directory-replace",
                        "c-utf8-locale",
                        "directory-fsync",
                        "o-cloexec",
                        "o-nofollow",
                        "owner-mode",
                        "passwd-database",
                        "process-session",
                        "signal-mask-pending",
                        "waitid-wnowait",
                    ],
                },
                "system_tools": [
                    {
                        "id": tool_id,
                        "invoked_path": f"/usr/bin/{name}",
                        "resolved_path": f"/usr/bin/{name}",
                        "length": index + 1,
                        "sha256": str(index + 4) * 64,
                        "uid": 0,
                        "gid": 0,
                        "mode": 365,
                    }
                    for index, (tool_id, name) in enumerate(
                        (
                            ("environment-clearer", "env"),
                            ("git", "git"),
                            ("posix-shell", "sh"),
                        )
                    )
                ],
            }
        )
        runtime_path = "/opt/task-witness/python/bin/python3.13"
        runtime_entries = [
            {
                "path": runtime_path,
                "kind": "regular-file",
                "role": "main-executable",
                "length": 123,
                "sha256": "8" * 64,
                "uid": 0,
                "gid": 0,
                "mode": 365,
            },
            {
                "path": "/opt/task-witness/python/lib",
                "kind": "directory",
                "role": "stdlib-root",
                "uid": 0,
                "gid": 0,
                "mode": 365,
            },
        ]
        evidence = self.content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-runtime-closure-evidence-v1",
                "authority": {
                    "supplier": "python-build-standalone",
                    "provenance": "qualified-relocation",
                    "qualification_class": "task-witness-cpython-closure-v1",
                    "issuer": "operator-runtime-audit",
                    "disposition": "qualified",
                    "evidence_sha256": "9" * 64,
                },
                "main_executable": {
                    "path": runtime_path,
                    "length": 123,
                    "sha256": "8" * 64,
                    "uid": 0,
                    "gid": 0,
                    "mode": 365,
                    "implementation": "cpython",
                    "version": {"major": 3, "minor": 13, "micro": 15},
                },
                "closure": {
                    "inventory_contract": "task-witness-runtime-closure-inventory-v1",
                    "roots": [
                        {
                            "path": "/opt/task-witness/python",
                            "role": "runtime-root",
                            "complete_inventory": True,
                        }
                    ],
                    "dependency_classes": [
                        "cpython-extension-modules",
                        "cpython-stdlib",
                        "loader-shared-libraries",
                    ],
                    "entries": runtime_entries,
                    "entries_sha256": hashlib.sha256(
                        json.dumps(
                            runtime_entries,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest(),
                    "entry_count": 2,
                    "total_regular_file_bytes": 123,
                },
            }
        )
        credential: dict[str, object]
        if target == "macos-arm64":
            credential = {
                "kind": "darwin-issetugid-v1",
                "real_uid": uid,
                "effective_uid": uid,
                "real_gid": gid,
                "effective_gid": gid,
                "supplementary_gids": [],
                "issetugid": False,
            }
        else:
            credential = {
                "kind": "linux-res-id-capabilities-v1",
                "real_uid": uid,
                "effective_uid": uid,
                "saved_uid": uid,
                "real_gid": gid,
                "effective_gid": gid,
                "saved_gid": gid,
                "supplementary_gids": [],
                "capabilities": {
                    "ambient": 0,
                    "bounding": 0,
                    "effective": 0,
                    "inheritable": 0,
                    "permitted": 0,
                },
            }
        qualification_candidate = {
            "repository_id": "nisavid/agents",
            "commit_sha1": "1" * 40,
            "tree_sha1": "2" * 40,
            "plugin_subtree_sha256": "3" * 64,
            "suite_inventory_sha256": "4" * 64,
        }
        candidate_closure = {
            "contract": "task-witness-qualification-candidate-closure-v1",
            "entry_count": 42,
            "projection_sha256": "5" * 64,
            "source_shape_sha256": "6" * 64,
        }
        bridge_history = self.bridge_history_projection()
        inventory_counts = [
            {"expected_count": SUITE_EXPECTED_COUNTS[suite_id], "id": suite_id}
            for suite_id, _phase, _targets in SUITE_PROJECTIONS
        ]
        suite_inventory = {
            "path": "release/task-witness/tw4-suite-inventory.json",
            "length": 1000,
            "sha256": qualification_candidate["suite_inventory_sha256"],
            "counts_sha256": hashlib.sha256(
                json.dumps(
                    inventory_counts,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "entries_sha256": "a" * 64,
            "entry_count": 16,
            "expected_count_total": sum(SUITE_EXPECTED_COUNTS.values()),
        }
        tool_observations = []
        for tool in profile["system_tools"]:
            tool_path = str(tool["resolved_path"])
            binding = self.regular_binding(
                tool_path,
                length=int(tool["length"]),
                sha256=str(tool["sha256"]),
                mode=int(tool["mode"]),
            )
            binding["stat"]["uid"] = tool["uid"]
            binding["stat"]["gid"] = tool["gid"]
            tool_observations.append(
                {
                    "id": tool["id"],
                    "invoked_path": tool["invoked_path"],
                    "resolved_path": tool["resolved_path"],
                    "resolution": [
                        {"path": tool_path, "stat": copy.deepcopy(binding["stat"])}
                    ],
                    "file": binding,
                }
            )
        closure_observation = {
            "contract": "task-witness-runtime-closure-observation-v1",
            "roots": [self.directory_binding("/opt/task-witness/python")],
            "entries": [
                {
                    "kind": "regular-file",
                    **self.regular_binding(
                        runtime_path,
                        length=123,
                        sha256="8" * 64,
                        mode=365,
                    ),
                },
                {
                    "kind": "directory",
                    **self.directory_binding("/opt/task-witness/python/lib"),
                },
            ],
        }
        closure_observation["roots"][0]["stat"]["uid"] = 0
        closure_observation["roots"][0]["stat"]["gid"] = 0
        closure_observation["entries"][0]["stat"]["uid"] = 0
        closure_observation["entries"][0]["stat"]["gid"] = 0
        closure_observation["entries"][1]["stat"]["uid"] = 0
        closure_observation["entries"][1]["stat"]["gid"] = 0
        closure_observation["entries"][1]["stat"]["mode"] = stat.S_IFDIR | 365
        main_observation = {
            "path": runtime_path,
            "length": 123,
            "sha256": "8" * 64,
            "uid": 0,
            "gid": 0,
            "mode": 365,
            "nlink": 1,
        }
        rendered_shim = {
            "contract": "task-witness-rendered-shim-observation-v1",
            "template": {
                "path": "plugins/task-witness/client/task_witness_shim.sh.in",
                "length": 20,
                "sha256": "b" * 64,
            },
            "runtime_executable_path": runtime_path,
            "client": {
                "path": f"{home}/.local/libexec/task-witness/client/task_witness_client.py",
                "length": 30,
                "sha256": "c" * 64,
                "uid": uid,
                "gid": gid,
                "mode": 320,
                "nlink": 1,
            },
            "shim": {
                "path": f"{home}/.local/libexec/task-witness/task-witness",
                "length": 40,
                "sha256": "d" * 64,
                "uid": uid,
                "gid": gid,
                "mode": 320,
                "nlink": 1,
            },
        }
        inputs = {
            "candidate": {
                "contract": "task-witness-tw4-candidate-observation-v1",
                "root_path": candidate_root,
                "root": self.directory_binding(candidate_root),
                "qualification_candidate": qualification_candidate,
                "candidate_closure": candidate_closure,
                "worktree": {"tracked": "clean", "untracked": "none"},
            },
            "bridge_history": {
                "contract": "task-witness-tw4-bridge-history-observation-v1",
                "bridge_history": bridge_history,
                "identity_file": self.regular_binding(
                    f"{candidate_root}/{BRIDGE_IDENTITY}",
                    length=1560,
                    sha256=bridge_history["bridge_identity_sha256"],
                    mode=0o644,
                ),
                "provenance_file": self.regular_binding(
                    f"{candidate_root}/{BRIDGE_PROVENANCE}",
                    length=7290,
                    sha256=bridge_history["bridge_provenance_sha256"],
                    mode=0o644,
                ),
            },
            "suite_inventory": {
                "contract": "task-witness-tw4-suite-inventory-observation-v1",
                "file": self.regular_binding(
                    f"{candidate_root}/{SUITE_INVENTORY}",
                    length=suite_inventory["length"],
                    sha256=suite_inventory["sha256"],
                    mode=0o644,
                ),
                "suite_inventory": suite_inventory,
            },
            "platform": {
                "contract": "task-witness-tw4-platform-observation-v1",
                "profile_file": self.regular_binding(
                    "/evidence/platform.json",
                    length=len(
                        json.dumps(
                            profile, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ),
                    sha256=hashlib.sha256(
                        json.dumps(
                            profile, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                    mode=0o600,
                ),
                "home": self.directory_binding(home),
                "credential_state": credential,
                "system_tools": tool_observations,
            },
            "runtime": {
                "contract": "task-witness-tw4-runtime-observation-v1",
                "evidence_file": self.regular_binding(
                    "/evidence/runtime.json",
                    length=len(
                        json.dumps(
                            evidence, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ),
                    sha256=hashlib.sha256(
                        json.dumps(
                            evidence, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                    mode=0o600,
                ),
                "main_executable_observation": main_observation,
                "closure_observation": closure_observation,
            },
        }
        stability = {
            f"{name}_sha256": hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            for name, value in inputs.items()
        }
        applicable = [
            (suite_id, targets)
            for suite_id, _phase, targets in SUITE_PROJECTIONS
            if target in targets
        ]
        suite_results = []
        rendered_raw = json.dumps(
            rendered_shim,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        for suite_id, _targets in applicable:
            expected_count = SUITE_EXPECTED_COUNTS[suite_id]
            detail_stdout = rendered_raw if suite_id == "literal-rendered-shim" else b""
            result = {
                "schema_version": 1,
                "contract": "task-witness-tw4-suite-result-v1",
                "id": suite_id,
                "observed_count": expected_count,
                "terminal": "passed",
                "detail_stdout_length": len(detail_stdout),
                "detail_stdout_sha256": hashlib.sha256(detail_stdout).hexdigest(),
                "detail_stderr_length": 0,
                "detail_stderr_sha256": hashlib.sha256(b"").hexdigest(),
            }
            result_raw = json.dumps(
                result, sort_keys=True, separators=(",", ":")
            ).encode()
            suite_results.append(
                {
                    "id": suite_id,
                    "expected_count": expected_count,
                    "expected_terminal": "passed",
                    "process": {
                        "exit_status": 0,
                        "stdout_length": len(result_raw),
                        "stdout_sha256": hashlib.sha256(result_raw).hexdigest(),
                        "stderr_length": 0,
                        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                    },
                    "result": result,
                }
            )
        return self.content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-tw4-host-qualification-receipt-v1",
                "qualification_candidate": qualification_candidate,
                "candidate_closure": candidate_closure,
                "bridge_history": bridge_history,
                "suite_inventory": suite_inventory,
                "target": target,
                "platform": {
                    "profile_sha256": hashlib.sha256(
                        json.dumps(
                            profile, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                    "profile": profile,
                    "credential_state": credential,
                    "system_tool_observation_sha256": hashlib.sha256(
                        json.dumps(
                            tool_observations, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                },
                "runtime": {
                    "evidence_sha256": hashlib.sha256(
                        json.dumps(
                            evidence, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                    "evidence": evidence,
                    "main_executable_observation": main_observation,
                    "closure_observation_sha256": hashlib.sha256(
                        json.dumps(
                            closure_observation, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                },
                "rendered_shim": rendered_shim,
                "observations": {
                    "contract": "task-witness-tw4-host-input-stability-v1",
                    "inputs": inputs,
                    "before": stability,
                    "after": copy.deepcopy(stability),
                },
                "suite_results": suite_results,
                "disposition": "qualified",
            }
        )

    def rewrite_content_digest(self, relative: Path) -> dict:
        path = self.repository / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        content = dict(value)
        content.pop("content_sha256")
        value["content_sha256"] = hashlib.sha256(
            json.dumps(
                content,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return value

    def rebind_provenance_bytes(self) -> None:
        provenance = self.repository / BRIDGE_PROVENANCE
        identity_path = self.repository / BRIDGE_IDENTITY
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        identity["provenance_sha256"] = hashlib.sha256(
            provenance.read_bytes()
        ).hexdigest()
        identity_path.write_text(
            json.dumps(identity, indent=2) + "\n", encoding="utf-8"
        )
        self.rewrite_content_digest(BRIDGE_IDENTITY)

    def test_validates_carried_bridge_history_without_git_repository(self) -> None:
        self.assertFalse((self.repository / ".git").exists())
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        self.assertEqual(
            validator["validate_bridge_history"](self.repository),
            self.bridge_history_projection(),
        )

    def test_release_manifest_parser_matches_frozen_bridge_schema(self) -> None:
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        manifest = self.release_manifest_document()
        self.assertEqual(validator["parse_tw4_release_manifest"](manifest), manifest)
        from tests.plugins.task_witness_deployment._bridge_transition_prepare_support import (
            OutboundBridgePreparationFixture,
        )

        request = type(
            "BridgeRequestFixture",
            (),
            {
                "source_selection_raw": json.dumps(
                    {
                        "repository_id": "nisavid/agents",
                        "subtree_sha256": "3" * 64,
                        "revision": "8" * 40,
                    }
                ).encode()
            },
        )()
        bridge_raw = OutboundBridgePreparationFixture._release_manifest(
            request,
            self.bridge_history_projection(),
        )
        bridge_manifest = json.loads(bridge_raw)
        self.assertEqual(
            validator["parse_tw4_release_manifest"](bridge_manifest),
            bridge_manifest,
        )

    def test_release_manifest_parser_rejects_schema_and_binding_drift(self) -> None:
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        parser = validator["parse_tw4_release_manifest"]
        valid = self.release_manifest_document()
        cases: list[dict[str, object]] = []
        for mutation in (
            lambda value: value.pop("targets"),
            lambda value: value.update({"unexpected": None}),
            lambda value: value.update({"schema_version": True}),
            lambda value: value["qualification_candidate"].update(
                {"repository_id": "another/repository"}
            ),
            lambda value: value["targets"].update(
                {"macos-arm64": value["targets"]["linux-x86_64"]}
            ),
            lambda value: value["migration_edge"].update({"successor": "tw5"}),
            lambda value: value.update({"promotion_delta_sha256": "A" * 64}),
            lambda value: value.update({"content_sha256": "0" * 64}),
        ):
            case = copy.deepcopy(valid)
            mutation(case)
            cases.append(case)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                parser(case)
        parser_globals = parser.__globals__
        original_maximum = parser_globals["MAX_JSON_BYTES"]
        parser_globals["MAX_JSON_BYTES"] = 1
        try:
            with self.assertRaisesRegex(ValueError, "too large"):
                parser(valid)
        finally:
            parser_globals["MAX_JSON_BYTES"] = original_maximum

    def test_host_receipt_parser_accepts_both_complete_v1_target_shapes(self) -> None:
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        for target in ("macos-arm64", "linux-x86_64"):
            with self.subTest(target=target):
                receipt = self.host_receipt_document(target)
                self.assertEqual(
                    validator["parse_host_qualification_receipt"](receipt),
                    receipt,
                )

    def test_host_receipt_parser_rejects_the_static_mutation_matrix(self) -> None:
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        parser = validator["parse_host_qualification_receipt"]
        valid = self.host_receipt_document()
        mutations = (
            lambda value: value.pop("candidate_closure"),
            lambda value: value.update({"unexpected": None}),
            lambda value: value.update({"schema_version": True}),
            lambda value: value["candidate_closure"].update({"entry_count": True}),
            lambda value: value["candidate_closure"].update({"entry_count": 1 << 64}),
            lambda value: value["qualification_candidate"].update(
                {"suite_inventory_sha256": "0" * 64}
            ),
            lambda value: value["suite_inventory"].update({"length": "1000"}),
            lambda value: value["suite_inventory"].update({"counts_sha256": "0" * 64}),
            lambda value: value["suite_inventory"].update(
                {"expected_count_total": 729}
            ),
            lambda value: value.update({"target": "linux-x86_64"}),
            lambda value: value["platform"]["credential_state"].update(
                {"kind": "linux-res-id-capabilities-v1"}
            ),
            lambda value: value["observations"]["after"].update(
                {"candidate_sha256": "0" * 64}
            ),
            lambda value: value["observations"]["inputs"]["candidate"].update(
                {"qualification_candidate": {}}
            ),
            lambda value: value["observations"]["inputs"]["candidate"].update(
                {"root_path": "/srv/../candidate"}
            ),
            lambda value: value["platform"].update(
                {"system_tool_observation_sha256": "0" * 64}
            ),
            lambda value: value["runtime"].update(
                {"closure_observation_sha256": "0" * 64}
            ),
            lambda value: value["rendered_shim"]["shim"].update({"mode": 0o700}),
            lambda value: value["suite_results"].reverse(),
            lambda value: value["suite_results"][0].update({"expected_count": 2}),
            lambda value: value["suite_results"][0]["process"].update(
                {"stdout_sha256": "0" * 64}
            ),
            lambda value: value["candidate_closure"].update(
                {"projection_sha256": "a" * 5000}
            ),
            lambda value: value.update({"content_sha256": "0" * 64}),
        )
        for mutation in mutations:
            case = copy.deepcopy(valid)
            mutation(case)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                parser(case)

        rebound = copy.deepcopy(valid)
        rebound_item = rebound["suite_results"][0]
        rebound_item["expected_count"] += 1
        rebound_item["result"]["observed_count"] += 1
        rebound_result_raw = json.dumps(
            rebound_item["result"], sort_keys=True, separators=(",", ":")
        ).encode()
        rebound_item["process"].update(
            {
                "stdout_length": len(rebound_result_raw),
                "stdout_sha256": hashlib.sha256(rebound_result_raw).hexdigest(),
            }
        )
        rebound = self.content_document(
            {key: item for key, item in rebound.items() if key != "content_sha256"}
        )
        with self.assertRaises(ValueError):
            parser(rebound)

        linux = self.host_receipt_document("linux-x86_64")
        linux["platform"]["credential_state"]["capabilities"]["effective"] = 1
        with self.assertRaises(ValueError):
            parser(linux)

        parser_globals = parser.__globals__
        cap_cases = (
            ("HOST_RECEIPT_MAX_BYTES", None),
            ("HOST_RECEIPT_MEMBER_CAPS", "observations"),
            ("HOST_OBSERVATION_INPUT_CAPS", "runtime"),
            ("SYSTEM_TOOL_OBSERVATION_MAX_BYTES", None),
            ("RUNTIME_CLOSURE_OBSERVATION_MAX_BYTES", None),
            ("PROFILE_AND_EVIDENCE_MAX_BYTES", None),
        )
        for cap_name, member in cap_cases:
            with self.subTest(cap=cap_name, member=member):
                original = copy.deepcopy(parser_globals[cap_name])
                if member is None:
                    parser_globals[cap_name] = 1
                else:
                    parser_globals[cap_name][member] = 1
                try:
                    with self.assertRaisesRegex(ValueError, "too large"):
                        parser(valid)
                finally:
                    parser_globals[cap_name] = original

    def test_bridge_history_uses_one_captured_identity_and_provenance_pair(
        self,
    ) -> None:
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        capture = validator["_bounded_json_object_capture"]
        identity_path = self.repository / BRIDGE_IDENTITY
        original_identity = identity_path.read_bytes()
        calls = 0

        def capture_then_replace(*args: object, **kwargs: object) -> object:
            nonlocal calls
            result = capture(*args, **kwargs)
            calls += 1
            if calls == 1:
                identity_path.write_bytes(b"{}")
            return result

        validator["_bounded_json_object_capture"] = capture_then_replace
        try:
            self.assertEqual(
                validator["validate_bridge_history"](self.repository),
                self.bridge_history_projection_from_raw(
                    original_identity,
                    (self.repository / BRIDGE_PROVENANCE).read_bytes(),
                ),
            )
        finally:
            identity_path.write_bytes(original_identity)

    @staticmethod
    def bridge_history_projection_from_raw(
        identity_raw: bytes,
        provenance_raw: bytes,
    ) -> dict[str, object]:
        identity = json.loads(identity_raw)
        return {
            "bridge_identity_sha256": hashlib.sha256(identity_raw).hexdigest(),
            "bridge_provenance_sha256": hashlib.sha256(provenance_raw).hexdigest(),
            "freeze5": identity["freeze5"],
            "bridge": identity["bridge"],
        }

    def test_validator_argv_parser_accepts_only_the_closed_mode_grammars(self) -> None:
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        parse = validator["_validator_invocation"]
        root = self.repository.resolve()
        receipt = (Path(self.temporary.name) / "host-receipt.json").resolve()
        candidate = (Path(self.temporary.name) / "candidate").resolve()
        manifest = (Path(self.temporary.name) / "manifest.json").resolve()
        macos = (Path(self.temporary.name) / "macos.json").resolve()
        linux = (Path(self.temporary.name) / "linux.json").resolve()
        review = (
            Path(self.temporary.name) / "review-bundle" / "manifest.json"
        ).resolve()
        self.assertEqual(parse([]), (Path.cwd(), "package", {}))
        self.assertEqual(parse([str(root)]), (root, "package", {}))
        self.assertEqual(
            parse([str(root), "--source-stage"]),
            (root, "source-stage", {}),
        )
        self.assertEqual(
            parse([str(root), "--qualification", str(receipt)]),
            (root, "qualification", {"host_receipt": receipt}),
        )
        final_argv = [
            str(root),
            "--final-release",
            "--candidate-root",
            str(candidate),
            "--release-manifest",
            str(manifest),
            "--macos-receipt",
            str(macos),
            "--linux-receipt",
            str(linux),
            "--review-evidence",
            str(review),
        ]
        self.assertEqual(
            parse(final_argv),
            (
                root,
                "final-release",
                {
                    "candidate_root": candidate,
                    "release_manifest": manifest,
                    "macos_receipt": macos,
                    "linux_receipt": linux,
                    "review_evidence": review,
                },
            ),
        )
        invalid = (
            ["--source-stage"],
            [str(root), "--qual", str(receipt)],
            [str(root), f"--qualification={receipt}"],
            ["relative-root", "--source-stage"],
            [
                str(root),
                "--qualification",
                str(receipt),
                "--qualification",
                str(receipt),
            ],
            [str(root), "--source-stage", "--qualification", str(receipt)],
            [*final_argv[:4], "--macos-receipt", str(macos), *final_argv[4:]],
            final_argv[:-2],
            [*final_argv, "extra"],
        )
        for argv in invalid:
            with (
                self.subTest(argv=argv),
                self.assertRaisesRegex(
                    ValueError,
                    "arguments are invalid",
                ),
            ):
                parse(argv)

        private_document = (Path(self.temporary.name) / "private.json").resolve()
        private_document.write_bytes(b"{}")
        private_document.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private regular file"):
            validator["_bounded_external_canonical_json_object"](
                private_document,
                "Task Witness private evidence fixture",
                private=True,
            )
        private_document.chmod(0o600)
        self.assertEqual(
            validator["_bounded_external_canonical_json_object"](
                private_document,
                "Task Witness private evidence fixture",
                private=True,
            ),
            ({}, b"{}"),
        )
        external_capture = validator["_bounded_external_canonical_json_object"]
        external_globals = external_capture.__globals__
        original_capture = external_globals["_bounded_json_object_capture"]
        capture_arguments: list[tuple[Path, Path]] = []

        def record_absolute_walk(
            capture_root: Path,
            relative: Path,
            *_args: object,
            **_kwargs: object,
        ) -> tuple[dict, bytes]:
            capture_arguments.append((capture_root, relative))
            return {}, b"{}"

        external_globals["_bounded_json_object_capture"] = record_absolute_walk
        try:
            external_capture(private_document, "Task Witness evidence walk fixture")
        finally:
            external_globals["_bounded_json_object_capture"] = original_capture
        self.assertEqual(
            capture_arguments,
            [(Path(private_document.anchor), Path(*private_document.parts[1:]))],
        )

        load_runner = validator["_load_qualification_runner"]
        load_globals = load_runner.__globals__
        load_replaced = {
            name: load_globals[name]
            for name in (
                "verified_reviewed_path",
                "_require_path_outside_user_write_authority",
                "runpy",
            )
        }
        observed_distribution_roots: list[Path] = []
        runner_functions = {
            name: (lambda *_args: None)
            for name in (
                "_candidate_administration",
                "_require_candidate_administration_stable",
                "_require_safe_candidate_config",
                "_git_text",
                "_git_tree_entries",
                "_require_clean_candidate",
                "run_recorded_git",
                "_git_blob",
                "_bounded_process",
            )
        }

        def trusted_runner_path(distribution_root: Path, relative: str) -> Path:
            observed_distribution_roots.append(distribution_root)
            self.assertEqual(relative, "scripts/run_task_witness_qualification.py")
            return Path("/immutable/validator/run_task_witness_qualification.py")

        runpy_fixture = type(
            "RunpyFixture",
            (),
            {"run_path": staticmethod(lambda *_args: runner_functions)},
        )
        load_globals.update(
            {
                "verified_reviewed_path": trusted_runner_path,
                "_require_path_outside_user_write_authority": lambda *_args: None,
                "runpy": runpy_fixture,
            }
        )
        try:
            self.assertEqual(load_runner(), runner_functions)
        finally:
            load_globals.update(load_replaced)
        self.assertEqual(
            observed_distribution_roots,
            [validator["SCRIPT_DIRECTORY"].parent],
        )

        select_git = validator["_select_final_git_executable"]
        select_globals = select_git.__globals__
        original_local_git = select_globals["_local_git_executable"]
        select_globals["_local_git_executable"] = lambda: Path("/usr/bin/git")
        try:
            self.assertEqual(select_git(), Path("/usr/bin/git"))
        finally:
            select_globals["_local_git_executable"] = original_local_git

        qualification = self.validate("--qualification", str(receipt))
        self.assertNotEqual(qualification.returncode, 0)
        self.assertIn(
            "host qualification receipt is unavailable",
            qualification.stderr,
        )
        self.assertNotIn("validation is not yet available", qualification.stderr)

        receipt_value = self.host_receipt_document()
        receipt_value["observations"]["inputs"]["candidate"]["root_path"] = str(root)
        receipt_value["observations"]["inputs"]["candidate"]["root"]["path"] = str(root)
        candidate_evidence = {
            "qualification_candidate": copy.deepcopy(
                receipt_value["qualification_candidate"]
            ),
            "candidate_closure": copy.deepcopy(receipt_value["candidate_closure"]),
            "suite_inventory": copy.deepcopy(receipt_value["suite_inventory"]),
            "bridge_history": copy.deepcopy(receipt_value["bridge_history"]),
            "candidate_observation": copy.deepcopy(
                receipt_value["observations"]["inputs"]["candidate"]
            ),
            "bridge_identity_file": copy.deepcopy(
                receipt_value["observations"]["inputs"]["bridge_history"][
                    "identity_file"
                ]
            ),
            "bridge_provenance_file": copy.deepcopy(
                receipt_value["observations"]["inputs"]["bridge_history"][
                    "provenance_file"
                ]
            ),
            "suite_inventory_file": copy.deepcopy(
                receipt_value["observations"]["inputs"]["suite_inventory"]["file"]
            ),
            "template": copy.deepcopy(receipt_value["rendered_shim"]["template"]),
            "client": {
                "length": receipt_value["rendered_shim"]["client"]["length"],
                "sha256": receipt_value["rendered_shim"]["client"]["sha256"],
            },
        }
        validator["validate_qualification_candidate_binding"](
            root,
            receipt_value,
            candidate_evidence,
        )
        drifted_evidence = copy.deepcopy(candidate_evidence)
        drifted_evidence["qualification_candidate"]["commit_sha1"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "qualification candidate drift"):
            validator["validate_qualification_candidate_binding"](
                root,
                receipt_value,
                drifted_evidence,
            )

        inputs = receipt_value["observations"]["inputs"]
        inputs["bridge_history"]["identity_file"]["path"] = str(
            root / BRIDGE_IDENTITY
        )
        inputs["bridge_history"]["provenance_file"]["path"] = str(
            root / BRIDGE_PROVENANCE
        )
        inputs["suite_inventory"]["file"]["path"] = str(root / SUITE_INVENTORY)
        candidate_evidence["bridge_identity_file"] = copy.deepcopy(
            inputs["bridge_history"]["identity_file"]
        )
        candidate_evidence["bridge_provenance_file"] = copy.deepcopy(
            inputs["bridge_history"]["provenance_file"]
        )
        candidate_evidence["suite_inventory_file"] = copy.deepcopy(
            inputs["suite_inventory"]["file"]
        )
        for input_name, digest_name in (
            ("candidate", "candidate_sha256"),
            ("bridge_history", "bridge_history_sha256"),
            ("suite_inventory", "suite_inventory_sha256"),
            ("platform", "platform_sha256"),
            ("runtime", "runtime_sha256"),
        ):
            digest = hashlib.sha256(
                json.dumps(
                    inputs[input_name], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            receipt_value["observations"]["before"][digest_name] = digest
            receipt_value["observations"]["after"][digest_name] = digest
        receipt_value = self.content_document(
            {
                key: value
                for key, value in receipt_value.items()
                if key != "content_sha256"
            }
        )
        candidate_evidence["candidate_observation"] = copy.deepcopy(
            receipt_value["observations"]["inputs"]["candidate"]
        )
        validator_globals = validator["main"].__globals__
        replaced = {
            name: validator_globals[name]
            for name in (
                "_bounded_external_canonical_json_object",
                "_qualification_candidate_evidence",
                "validate_inventory",
                "validate_manifests",
                "validate_public_release_registration",
                "validate_suite_inventory",
                "validate_reviewed_sources",
                "validate_bridge_history",
            )
        }
        validator_globals.update(
            {
                "_bounded_external_canonical_json_object": lambda *_args, **_kwargs: (
                    receipt_value,
                    json.dumps(
                        receipt_value, sort_keys=True, separators=(",", ":")
                    ).encode(),
                ),
                "_qualification_candidate_evidence": lambda *_args: candidate_evidence,
                "validate_inventory": lambda *_args: None,
                "validate_manifests": lambda *_args: None,
                "validate_public_release_registration": lambda *_args: None,
                "validate_suite_inventory": lambda *_args: None,
                "validate_reviewed_sources": lambda *_args: None,
                "validate_bridge_history": lambda *_args: None,
            }
        )
        try:
            self.assertEqual(
                validator["main"](
                    [str(root), "--qualification", "/tmp/host-receipt.json"]
                ),
                0,
            )
        finally:
            validator_globals.update(replaced)

        promotion_paths = (
            ".claude-plugin/marketplace.json",
            "release/task-witness/public-release-registration.json",
        )
        candidate_registration = copy.deepcopy(
            validator["EXPECTED_PUBLIC_RELEASE_REGISTRATION"]
        )
        final_registration = {
            **candidate_registration,
            "production_eligible": True,
        }
        candidate_marketplace = {
            "name": "nisavid-agents",
            "owner": {"name": "Ivan D Vasin"},
            "description": "Personal agent tools.",
            "plugins": [
                {
                    "name": "rolecasting",
                    "source": "./plugins/rolecasting",
                    "category": "developer-tools",
                }
            ],
        }
        final_marketplace = copy.deepcopy(candidate_marketplace)
        final_marketplace["plugins"].append(
            {
                "name": "task-witness",
                "source": "./plugins/task-witness",
                "category": "developer-tools",
            }
        )

        def canonical(value: object) -> bytes:
            return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

        candidate_raw = {
            promotion_paths[0]: canonical(candidate_marketplace),
            promotion_paths[1]: canonical(candidate_registration),
        }
        final_raw = {
            promotion_paths[0]: canonical(final_marketplace),
            promotion_paths[1]: canonical(final_registration),
        }

        def blob_oid(raw: bytes) -> str:
            return hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw
            ).hexdigest()

        candidate_tree = {
            path: ("100644", "blob", blob_oid(candidate_raw[path]))
            for path in promotion_paths
        } | {
            ".claude-plugin": ("040000", "tree", "1" * 40),
            "release": ("040000", "tree", "2" * 40),
            "release/task-witness": ("040000", "tree", "3" * 40),
            "README.md": ("100644", "blob", "b" * 40),
        }
        final_tree = {
            path: ("100644", "blob", blob_oid(final_raw[path]))
            for path in promotion_paths
        } | {
            ".claude-plugin": ("040000", "tree", "4" * 40),
            "release": ("040000", "tree", "5" * 40),
            "release/task-witness": ("040000", "tree", "6" * 40),
            "README.md": ("100644", "blob", "b" * 40),
        }
        promotion = validator["validate_promotion_delta"](
            candidate_tree,
            final_tree,
            candidate_raw,
            final_raw,
        )
        self.assertEqual(
            [entry["path"] for entry in promotion["entries"]],
            list(promotion_paths),
        )
        with self.assertRaisesRegex(ValueError, "promotion path drift"):
            validator["validate_promotion_delta"](
                candidate_tree,
                {
                    **final_tree,
                    "README.md": ("100644", "blob", "c" * 40),
                },
                candidate_raw,
                final_raw,
            )
        with self.assertRaisesRegex(ValueError, "promotion path drift"):
            validator["validate_promotion_delta"](
                candidate_tree,
                {
                    **final_tree,
                    "unexpected-empty-tree": ("040000", "tree", "d" * 40),
                },
                candidate_raw,
                final_raw,
            )
        wrong_route_raw = copy.deepcopy(final_raw)
        wrong_marketplace = copy.deepcopy(final_marketplace)
        wrong_marketplace["plugins"][-1]["source"] = "./plugins/not-task-witness"
        wrong_route_raw[promotion_paths[0]] = canonical(wrong_marketplace)
        wrong_route_tree = {
            **final_tree,
            promotion_paths[0]: (
                "100644",
                "blob",
                blob_oid(wrong_route_raw[promotion_paths[0]]),
            ),
        }
        with self.assertRaisesRegex(ValueError, "marketplace promotion drift"):
            validator["validate_promotion_delta"](
                candidate_tree,
                wrong_route_tree,
                candidate_raw,
                wrong_route_raw,
            )
        wrong_registration_raw = copy.deepcopy(final_raw)
        wrong_registration = copy.deepcopy(final_registration)
        wrong_registration["unexpected"] = True
        wrong_registration_raw[promotion_paths[1]] = canonical(wrong_registration)
        wrong_registration_tree = {
            **final_tree,
            promotion_paths[1]: (
                "100644",
                "blob",
                blob_oid(wrong_registration_raw[promotion_paths[1]]),
            ),
        }
        with self.assertRaisesRegex(ValueError, "final public-release registration drift"):
            validator["validate_promotion_delta"](
                candidate_tree,
                wrong_registration_tree,
                candidate_raw,
                wrong_registration_raw,
            )
        validator["validate_final_receipt_candidate_binding"](
            receipt_value,
            candidate_evidence,
        )
        drifted_receipt = copy.deepcopy(receipt_value)
        drifted_receipt["bridge_history"]["bridge_identity_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "final-release bridge history drift"):
            validator["validate_final_receipt_candidate_binding"](
                drifted_receipt,
                candidate_evidence,
            )

        macos_receipt = receipt_value
        linux_receipt = self.host_receipt_document("linux-x86_64")
        macos_raw = canonical(macos_receipt)
        linux_raw = canonical(linux_receipt)
        final_manifest = self.release_manifest_document()
        final_manifest["qualification_candidate"] = copy.deepcopy(
            macos_receipt["qualification_candidate"]
        )
        final_manifest["targets"] = {
            "linux-x86_64": hashlib.sha256(linux_raw).hexdigest(),
            "macos-arm64": hashlib.sha256(macos_raw).hexdigest(),
        }
        final_manifest["bridge_history"] = copy.deepcopy(
            macos_receipt["bridge_history"]
        )
        final_manifest["canonical_review_evidence_sha256"] = "7" * 64
        final_manifest["final_public_release"] = {
            "commit_sha1": "8" * 40,
            "tree_sha1": "9" * 40,
        }
        final_manifest["promotion_delta_sha256"] = hashlib.sha256(
            canonical(promotion)
        ).hexdigest()
        final_manifest = self.content_document(
            {key: value for key, value in final_manifest.items() if key != "content_sha256"}
        )
        final_manifest_raw = canonical(final_manifest)
        fake_checkouts = {
            candidate: {
                "commit_sha1": macos_receipt["qualification_candidate"]["commit_sha1"],
                "tree_sha1": macos_receipt["qualification_candidate"]["tree_sha1"],
                "parents": (),
                "tree": candidate_tree,
            },
            root: {
                "commit_sha1": "8" * 40,
                "tree_sha1": "9" * 40,
                "parents": (
                    macos_receipt["qualification_candidate"]["commit_sha1"],
                ),
                "tree": final_tree,
            },
        }
        fake_raw = {candidate: candidate_raw, root: final_raw}
        final_globals = validator["validate_final_release_evidence"].__globals__
        final_replaced = {
            name: final_globals[name]
            for name in (
                "_select_final_git_executable",
                "_qualification_candidate_evidence",
                "_load_qualification_runner",
                "_checkout_projection",
                "_promotion_raw_bytes",
                "validate_inventory",
                "validate_manifests",
                "validate_public_release_registration",
                "validate_suite_inventory",
                "validate_reviewed_sources",
                "validate_bridge_history",
            )
        }
        final_globals.update(
            {
                "_select_final_git_executable": lambda: Path("/usr/bin/git"),
                "_qualification_candidate_evidence": lambda *_args: candidate_evidence,
                "_load_qualification_runner": lambda *_args: {},
                "_checkout_projection": lambda checkout, *_args: fake_checkouts[checkout],
                "_promotion_raw_bytes": lambda checkout, *_args: fake_raw[checkout],
                "validate_inventory": lambda *_args: None,
                "validate_manifests": lambda *_args: None,
                "validate_public_release_registration": lambda *_args: None,
                "validate_suite_inventory": lambda *_args: None,
                "validate_reviewed_sources": lambda *_args: None,
                "validate_bridge_history": lambda *_args: candidate_evidence[
                    "bridge_history"
                ],
            }
        )
        final_arguments = (
            root,
            candidate,
            final_manifest,
            final_manifest_raw,
            macos_receipt,
            macos_raw,
            linux_receipt,
            linux_raw,
            review,
        )
        try:
            with self.assertRaisesRegex(ValueError, "canonical byte identity drift"):
                validator["validate_final_release_evidence"](
                    *final_arguments[:3],
                    b"{}",
                    *final_arguments[4:],
                )
            with self.assertRaisesRegex(
                ValueError,
                "canonical review evidence manifest is unavailable",
            ):
                validator["validate_final_release_evidence"](*final_arguments)
            with self.assertRaisesRegex(
                ValueError,
                "canonical review evidence path must name manifest.json",
            ):
                validator["_validate_canonical_review_evidence"](
                    review.with_name("review.json"),
                    "7" * 64,
                    final_manifest["qualification_candidate"],
                )
            review.parent.mkdir(mode=0o700)
            review.write_bytes(b"{}")
            review.chmod(0o600)
            with self.assertRaisesRegex(
                ValueError,
                "canonical review evidence raw manifest digest drift",
            ):
                validator["_validate_canonical_review_evidence"](
                    review,
                    "7" * 64,
                    final_manifest["qualification_candidate"],
                )
            process_calls: list[tuple[list[str], dict[str, object]]] = []
            envelope = {
                "contract": "task-witness-launch-envelope-v1",
                "anchor": {},
                "witness": {},
            }
            envelope_raw = canonical(envelope) + b"\n"

            def bounded_process(
                argv: list[str], **kwargs: object
            ) -> tuple[int, bytes, bytes]:
                process_calls.append((argv, kwargs))
                return 0, envelope_raw, b""

            review_validator = validator["_run_canonical_task_witness"]
            review_globals = review_validator.__globals__
            review_replaced = {
                name: review_globals[name]
                for name in ("_load_qualification_runner", "_task_witness_front_door")
            }
            review_globals.update(
                {
                    "_load_qualification_runner": lambda: {
                        "_bounded_process": bounded_process
                    },
                    "_task_witness_front_door": lambda: Path(
                        "/Users/fixture/.local/libexec/task-witness/task-witness"
                    ),
                }
            )
            try:
                self.assertEqual(review_validator(review.parent), envelope)
                process_failures = (
                    ("nonzero", (1, b"", b""), "process failed"),
                    ("stderr", (0, envelope_raw, b"diagnostic"), "process failed"),
                    ("unframed", (0, canonical(envelope), b""), "framing drift"),
                    (
                        "multi-frame",
                        (0, envelope_raw + b"{}\n", b""),
                        "framing drift",
                    ),
                    ("invalid-json", (0, b"not-json\n", b""), "is invalid"),
                    (
                        "noncanonical-json",
                        (
                            0,
                            json.dumps(envelope, sort_keys=True).encode() + b"\n",
                            b"",
                        ),
                        "is invalid",
                    ),
                )
                for name, result, message in process_failures:
                    with self.subTest(process_failure=name):
                        review_globals["_load_qualification_runner"] = (
                            lambda result=result: {
                                "_bounded_process": lambda *_args, **_kwargs: result
                            }
                        )
                        with self.assertRaisesRegex(ValueError, message):
                            review_validator(review.parent)

                def failed_process(*_args: object, **_kwargs: object) -> object:
                    raise RuntimeError("injected bounded process failure")

                review_globals["_load_qualification_runner"] = lambda: {
                    "_bounded_process": failed_process
                }
                with self.assertRaisesRegex(ValueError, "process failed"):
                    review_validator(review.parent)
            finally:
                review_globals.update(review_replaced)
            self.assertEqual(
                process_calls,
                [
                    (
                        [
                            "/Users/fixture/.local/libexec/task-witness/task-witness",
                            "validate",
                            "--bundle",
                            str(review.parent),
                        ],
                        {
                            "env": {},
                            "stdout_maximum": validator["MAX_JSON_BYTES"],
                            "stderr_maximum": validator[
                                "TASK_WITNESS_REVIEW_STDERR_MAX_BYTES"
                            ],
                            "label": "Task Witness canonical review validation",
                            "stdin": None,
                            "timeout_seconds": validator[
                                "TASK_WITNESS_REVIEW_TIMEOUT_SECONDS"
                            ],
                            "own_process_group": True,
                            "cwd": Path("/"),
                        },
                    )
                ],
            )
            front_door = validator["_task_witness_front_door"]
            with (
                mock.patch.object(
                    front_door.__globals__["pwd"],
                    "getpwuid",
                    return_value=type(
                        "PasswdFixture", (), {"pw_dir": "/Users/passwd-fixture"}
                    )(),
                ),
                mock.patch.dict(os.environ, {"HOME": "/tmp/poisoned-home"}),
            ):
                self.assertEqual(
                    front_door(),
                    Path(
                        "/Users/passwd-fixture/.local/libexec/task-witness/task-witness"
                    ),
                )

            def identity(kind: str, value: str) -> dict[str, str]:
                return {
                    "kind": kind,
                    "value": value,
                    "content_sha256": hashlib.sha256(value.encode()).hexdigest(),
                }

            qualification_candidate = final_manifest["qualification_candidate"]
            candidate_identity = {
                "kind": "task-witness-tw4-qualification-candidate-v1",
                "value": (
                    f"{qualification_candidate['repository_id']}@"
                    f"{qualification_candidate['commit_sha1']}"
                ),
                "content_sha256": hashlib.sha256(
                    canonical(qualification_candidate)
                ).hexdigest(),
            }
            required_profile = {
                "contract": "task-witness-tw4-publication-review-profile-v2",
                "execution_mode": "independent",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "target": {
                    "product_family": "codex",
                    "surface": "chatgpt-codex",
                },
                "topology": {
                    "relationship": "child",
                    "ownership": "leader-owned",
                    "transport": "native-tool",
                },
                "assurance": {
                    "target": "product-attested",
                    "model": "product-attested",
                    "topology": "product-attested",
                    "authority": "product-attested",
                    "execution_result": "product-attested",
                },
                "required_axes": ["intent", "runtime", "structure"],
            }
            requirements_identity = {
                "kind": "task-witness-tw4-publication-review-profile-v2",
                "value": (
                    "chatgpt-codex-product-attested-sol-high-"
                    "independent-all-axes"
                ),
                "content_sha256": hashlib.sha256(
                    canonical(required_profile)
                ).hexdigest(),
            }
            subject = {
                "candidate": candidate_identity,
                "review_input": {
                    "kind": "task-witness-tw4-review-input-v1",
                    "value": (
                        f"{qualification_candidate['repository_id']}@"
                        f"{qualification_candidate['commit_sha1']}:"
                        f"{qualification_candidate['tree_sha1']}"
                    ),
                    "content_sha256": hashlib.sha256(
                        canonical(qualification_candidate)
                    ).hexdigest(),
                },
                "requirements": requirements_identity,
            }
            selected_specialists = ["security"]
            roles = (
                "critic-intent",
                "critic-runtime",
                "critic-structure",
                "specialist-security",
            )
            executions = {}
            for index, role in enumerate(roles):
                execution_id = f"execution-{index}"
                executions[execution_id] = {
                    "execution_id": execution_id,
                    "role": role,
                    "target": {
                        "product_family": "codex",
                        "surface": "chatgpt-codex",
                        "executor": "codex",
                        "version": "2026.08.13",
                    },
                    "topology": {
                        "relationship": "child",
                        "ownership": "leader-owned",
                        "transport": "native-tool",
                    },
                    "candidate": candidate_identity,
                    "scope": identity("review-scope-v1", role),
                    "request": identity("review-request-v1", role),
                    "return_contract": "tricritical-raw-report-v1",
                    "verification_contract": "review-verification-v1",
                    "stop_contract": "review-stop-v1",
                    "authority": {
                        "access": "read-only",
                        "subdelegation": False,
                        "external_action": False,
                        "evidence": identity("dispatch-authority-v1", role),
                    },
                    "user_authority": None,
                    "isolation": {
                        "session": f"session-{index}",
                        "context": f"context-{index}",
                        "enforceable": True,
                    },
                    "assurance": {
                        "target": "product-attested",
                        "model": "product-attested",
                        "topology": "product-attested",
                        "authority": "product-attested",
                        "execution_result": "product-attested",
                        "evidence": identity("product-attestation-v1", role),
                    },
                    "assurance_minimum": {
                        "target": "product-attested",
                        "model": "product-attested",
                        "topology": "product-attested",
                        "authority": "product-attested",
                        "execution_result": "product-attested",
                    },
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                    "dispatch_sha256": f"{index + 1:x}" * 64,
                    "model_sha256": f"{index + 5:x}" * 64,
                    "result_sha256": f"{index + 9:x}" * 64,
                    "model_issuer": identity("issuer-v1", f"model-{role}"),
                    "result_issuer": identity("issuer-v1", f"result-{role}"),
                    "returned": identity("tricritical-raw-report-v1", role),
                    "verification": identity("review-verification-v1", role),
                    "stop": identity("review-stop-v1", role),
                }
            dispatch = {
                "schema_version": 1,
                "contract": "rolecasting-dispatch-projection-v2",
                "evidence_contract": "rolecasting-dispatch-evidence-v2",
                "manifest_sha256": "b" * 64,
                "plan_sha256": "c" * 64,
                "subject": identity("review-subject-v1", "d" * 64),
                "producer": {
                    "producer_id": "rolecasting-bootstrap-dispatch-v2",
                    "contract": "rolecasting-dispatch-evidence-v2",
                    "implementation_sha256": "e" * 64,
                },
                "executions": executions,
            }
            dispatch["content_sha256"] = hashlib.sha256(canonical(dispatch)).hexdigest()
            review_manifest_sha256 = hashlib.sha256(review.read_bytes()).hexdigest()
            projection = {
                "schema_version": 1,
                "contract": "tricritical-terminal-review-projection-v2",
                "evidence_contract": "tricritical-terminal-review-evidence-v2",
                "manifest_sha256": review_manifest_sha256,
                "subject": subject,
                "review_profile": {
                    "contract": "tricritical-review-profile-v1",
                    "execution_mode": "independent",
                    "required_axes": ["intent", "runtime", "structure"],
                    "selected_specialists": selected_specialists,
                },
                "final_dispatch": dispatch,
                "terminal": {
                    "state": "clean",
                    "owner": "none",
                    "limitations": [],
                    "missing_executions": [],
                    "unresolved_actionable_findings": 0,
                    "verification": {
                        "status": "passed",
                        "candidate": candidate_identity,
                        "evidence": identity("verification-evidence-v1", "passed"),
                        "unchanged": True,
                    },
                },
            }
            projection["content_sha256"] = hashlib.sha256(
                canonical(projection)
            ).hexdigest()
            producer = {
                "producer_id": "tricritical-review-loop-v2",
                "contract": "tricritical-terminal-review-evidence-v2",
                "implementation_sha256": "1" * 64,
                "validator_id": "tricritical-terminal-review-evidence-validator-v2",
                "validator_contract": "tricritical-terminal-review-evidence-v2",
                "validator_implementation_sha256": "2" * 64,
            }
            validator_identity = {
                "validator_id": "tricritical-terminal-review-evidence-validator-v2",
                "contract": "tricritical-terminal-review-evidence-v2",
                "implementation_sha256": "2" * 64,
            }
            bundle_sha256 = "3" * 64
            trust_sha256 = "4" * 64
            launch_envelope = {
                "contract": "task-witness-launch-envelope-v1",
                "anchor": {
                    "contract": "task-witness-complete-anchor-v1",
                    "generation": "sha256-" + "5" * 64,
                    "active_record_sha256": "6" * 64,
                    "runtime_contract": "task-witness-runtime-v1",
                    "interpreter": {
                        "executable": "/usr/bin/python3",
                        "implementation": "cpython",
                        "version": {"major": 3, "minor": 13, "micro": 7},
                    },
                    "public_release": {
                        "repository": "nisavid/agents",
                        "revision": "7" * 40,
                    },
                    "runtime_implementation_sha256": "8" * 64,
                    "trust_context_sha256": trust_sha256,
                    "bundle_sha256": bundle_sha256,
                    "historical": False,
                },
                "witness": {
                    "contract": "task-witness-canonical-projection-v2",
                    "bundle_sha256": bundle_sha256,
                    "producer": producer,
                    "validator": validator_identity,
                    "projection": projection,
                    "trust_context_sha256": trust_sha256,
                    "historical": False,
                },
            }
            validator["_validate_canonical_review_launch_envelope"](
                launch_envelope,
                review_manifest_sha256,
                qualification_candidate,
            )

            def set_nested(
                value: dict[str, object], path: tuple[str, ...], item: object
            ) -> None:
                current = value
                for component in path[:-1]:
                    child = current[component]
                    assert isinstance(child, dict)
                    current = child
                current[path[-1]] = item

            envelope_mutations = (
                (("contract",), "task-witness-launch-envelope-v2", "envelope contract"),
                (
                    ("anchor", "contract"),
                    "task-witness-complete-anchor-v2",
                    "complete anchor drift",
                ),
                (("anchor", "historical"), True, "complete anchor drift"),
                (
                    ("anchor", "generation"),
                    "generation-5",
                    "complete anchor drift",
                ),
                (
                    ("anchor", "public_release", "repository"),
                    "someone/else",
                    "complete anchor drift",
                ),
                (
                    ("anchor", "interpreter", "implementation"),
                    "pypy",
                    "complete anchor drift",
                ),
                (
                    ("witness", "contract"),
                    "task-witness-canonical-projection-v1",
                    "witness binding drift",
                ),
                (("witness", "historical"), True, "witness binding drift"),
                (("witness", "bundle_sha256"), "9" * 64, "witness binding drift"),
                (
                    ("witness", "trust_context_sha256"),
                    "9" * 64,
                    "witness binding drift",
                ),
                (
                    ("witness", "producer", "producer_id"),
                    "fake-tricritical-producer-v1",
                    "registered authority drift",
                ),
                (
                    ("witness", "producer", "contract"),
                    "tricritical-terminal-review-evidence-v1",
                    "registered authority drift",
                ),
                (
                    ("witness", "producer", "validator_id"),
                    "fake-tricritical-validator-v1",
                    "registered authority drift",
                ),
                (
                    ("witness", "validator", "validator_id"),
                    "fake-tricritical-validator-v1",
                    "registered authority drift",
                ),
                (
                    ("witness", "validator", "implementation_sha256"),
                    "9" * 64,
                    "registered authority drift",
                ),
            )
            for path, replacement, message in envelope_mutations:
                mutated = copy.deepcopy(launch_envelope)
                set_nested(mutated, path, replacement)
                with (
                    self.subTest(review_envelope_path=path),
                    self.assertRaisesRegex(ValueError, message),
                ):
                    validator["_validate_canonical_review_launch_envelope"](
                        mutated,
                        review_manifest_sha256,
                        qualification_candidate,
                    )

            def refresh_review_projection(value: dict[str, object]) -> None:
                witness_value = value["witness"]
                assert isinstance(witness_value, dict)
                projection_value = witness_value["projection"]
                assert isinstance(projection_value, dict)
                dispatch_value = projection_value["final_dispatch"]
                assert isinstance(dispatch_value, dict)
                dispatch_value.pop("content_sha256", None)
                dispatch_value["content_sha256"] = hashlib.sha256(
                    canonical(dispatch_value)
                ).hexdigest()
                projection_value.pop("content_sha256", None)
                projection_value["content_sha256"] = hashlib.sha256(
                    canonical(projection_value)
                ).hexdigest()

            projection_mutations = (
                (
                    ("contract",),
                    "tricritical-terminal-review-projection-v1",
                    "terminal projection drift",
                ),
                (
                    ("evidence_contract",),
                    "tricritical-terminal-review-evidence-v1",
                    "terminal projection drift",
                ),
                (("manifest_sha256",), "9" * 64, "terminal projection drift"),
                (
                    ("subject", "candidate", "value"),
                    "nisavid/agents@" + "9" * 40,
                    "subject drift",
                ),
                (
                    ("subject", "candidate", "content_sha256"),
                    "9" * 64,
                    "subject drift",
                ),
                (
                    ("subject", "requirements", "value"),
                    "weaker-review",
                    "subject drift",
                ),
                (
                    ("subject", "review_input", "value"),
                    "nisavid/agents@" + "9" * 40 + ":" + "9" * 40,
                    "subject drift",
                ),
                (
                    ("review_profile", "execution_mode"),
                    "shared-context",
                    "profile drift",
                ),
                (
                    ("review_profile", "required_axes"),
                    ["intent", "runtime"],
                    "profile drift",
                ),
                (
                    ("review_profile", "selected_specialists"),
                    ["security", "security"],
                    "profile drift",
                ),
                (
                    ("final_dispatch", "contract"),
                    "rolecasting-dispatch-projection-v1",
                    "dispatch contract drift",
                ),
                (
                    ("final_dispatch", "evidence_contract"),
                    "rolecasting-dispatch-evidence-v1",
                    "dispatch contract drift",
                ),
                (
                    (
                        "final_dispatch",
                        "executions",
                        "execution-0",
                        "model",
                    ),
                    "gpt-5.6-terra",
                    "execution profile drift",
                ),
                (
                    (
                        "final_dispatch",
                        "executions",
                        "execution-0",
                        "assurance_minimum",
                    ),
                    {
                        "target": "controller-observed",
                        "model": "controller-observed",
                        "topology": "controller-observed",
                        "authority": "controller-observed",
                        "execution_result": "controller-observed",
                    },
                    "execution profile drift",
                ),
                (
                    (
                        "final_dispatch",
                        "executions",
                        "execution-0",
                        "reasoning_effort",
                    ),
                    "medium",
                    "execution profile drift",
                ),
                (
                    (
                        "final_dispatch",
                        "executions",
                        "execution-0",
                        "target",
                    ),
                    {
                        "product_family": "codex",
                        "surface": "codex-cli-tui",
                        "executor": "codex",
                        "version": "2026.08.13",
                    },
                    "execution profile drift",
                ),
                (
                    (
                        "final_dispatch",
                        "executions",
                        "execution-0",
                        "isolation",
                    ),
                    {
                        "session": "session-0",
                        "context": "context-0",
                        "enforceable": False,
                    },
                    "execution profile drift",
                ),
                (
                    (
                        "final_dispatch",
                        "executions",
                        "execution-0",
                        "assurance",
                    ),
                    {
                        "target": "controller-observed",
                        "model": "controller-observed",
                        "topology": "controller-observed",
                        "authority": "controller-observed",
                        "execution_result": "controller-observed",
                        "evidence": identity(
                            "controller-observation-v1", "execution-0"
                        ),
                    },
                    "execution profile drift",
                ),
                (
                    (
                        "final_dispatch",
                        "executions",
                        "execution-0",
                        "role",
                    ),
                    "critic-runtime",
                    "execution inventory drift",
                ),
                (("terminal", "state"), "blocked", "not bare clean"),
                (("terminal", "owner"), "operator", "not bare clean"),
                (("terminal", "limitations"), ["unknown"], "not bare clean"),
                (
                    ("terminal", "missing_executions"),
                    ["critic-intent"],
                    "not bare clean",
                ),
                (
                    ("terminal", "unresolved_actionable_findings"),
                    True,
                    "not bare clean",
                ),
                (
                    ("terminal", "unresolved_actionable_findings"),
                    1,
                    "not bare clean",
                ),
                (
                    ("terminal", "verification", "status"),
                    "failed",
                    "not bare clean",
                ),
                (
                    ("terminal", "verification", "candidate"),
                    identity("wrong-candidate-v1", "wrong"),
                    "not bare clean",
                ),
                (
                    ("terminal", "verification", "unchanged"),
                    False,
                    "not bare clean",
                ),
            )
            for path, replacement, message in projection_mutations:
                mutated = copy.deepcopy(launch_envelope)
                witness_value = mutated["witness"]
                assert isinstance(witness_value, dict)
                projection_value = witness_value["projection"]
                assert isinstance(projection_value, dict)
                set_nested(projection_value, path, replacement)
                refresh_review_projection(mutated)
                with (
                    self.subTest(review_projection_path=path),
                    self.assertRaisesRegex(ValueError, message),
                ):
                    validator["_validate_canonical_review_launch_envelope"](
                        mutated,
                        review_manifest_sha256,
                        qualification_candidate,
                    )

            bad_content = copy.deepcopy(launch_envelope)
            bad_content_witness = bad_content["witness"]
            assert isinstance(bad_content_witness, dict)
            bad_content_projection = bad_content_witness["projection"]
            assert isinstance(bad_content_projection, dict)
            bad_content_projection["content_sha256"] = "9" * 64
            with self.assertRaisesRegex(ValueError, "content digest drift"):
                validator["_validate_canonical_review_launch_envelope"](
                    bad_content,
                    review_manifest_sha256,
                    qualification_candidate,
                )

            historical = copy.deepcopy(launch_envelope)
            historical_anchor = historical["anchor"]
            historical_witness = historical["witness"]
            assert isinstance(historical_anchor, dict)
            assert isinstance(historical_witness, dict)
            historical_anchor["historical"] = True
            historical_witness["historical"] = True
            with self.assertRaisesRegex(ValueError, "complete anchor drift"):
                validator["_validate_canonical_review_launch_envelope"](
                    historical,
                    review_manifest_sha256,
                    qualification_candidate,
                )

            wrong_candidate = copy.deepcopy(qualification_candidate)
            wrong_candidate["commit_sha1"] = "9" * 40
            with self.assertRaisesRegex(ValueError, "subject drift"):
                validator["_validate_canonical_review_launch_envelope"](
                    launch_envelope,
                    review_manifest_sha256,
                    wrong_candidate,
                )

            from tests.plugins.task_witness_client import (
                test_launcher as task_witness_launcher,
            )

            installed = task_witness_launcher.TaskWitnessLauncherTests()
            installed.setUp()
            self.addCleanup(installed.tearDown)
            ineligible_contract = "tricritical-terminal-review-evidence-v2"
            ineligible_producer_implementation = "a" * 64
            ineligible_validator_id = (
                "tricritical-terminal-review-evidence-validator-v2"
            )
            installed.validator.write_text(
                f"BUNDLE_CONTRACT = {ineligible_contract!r}\n"
                "def _validate_bundle(bundle, *, trust_snapshot):\n"
                "    return {'contract': 'should-not-execute-v1'}\n",
                encoding="utf-8",
            )
            installed.validator.chmod(0o600)
            installed_validator_sha256 = hashlib.sha256(
                installed.validator.read_bytes()
            ).hexdigest()
            installed_validator_implementation = (
                task_witness_launcher.validator_identity(
                    ineligible_contract,
                    ineligible_validator_id,
                    [(ineligible_validator_id, installed_validator_sha256)],
                )
            )
            ineligible_lifecycle = {
                "state": "active",
                "usable_for_new_publication": False,
            }
            eligible_lifecycle = {
                "state": "active",
                "usable_for_new_publication": True,
            }
            installed_trust = task_witness_launcher.document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-trust-context-v2",
                    "producers": [
                        {
                            "producer_id": "tricritical-review-loop-v2",
                            "contract": ineligible_contract,
                            "implementation_sha256": (
                                ineligible_producer_implementation
                            ),
                            "validator_id": ineligible_validator_id,
                            "validator_contract": ineligible_contract,
                            "validator_implementation_sha256": (
                                installed_validator_implementation
                            ),
                            **ineligible_lifecycle,
                        }
                    ],
                    "issuers": [],
                    "validators": [
                        {
                            "validator_id": ineligible_validator_id,
                            "contract": ineligible_contract,
                            "implementation_sha256": (
                                installed_validator_implementation
                            ),
                            "entrypoint": ineligible_validator_id,
                            "modules": [
                                {
                                    "name": ineligible_validator_id,
                                    "path": str(installed.validator),
                                    "sha256": installed_validator_sha256,
                                }
                            ],
                            **eligible_lifecycle,
                        }
                    ],
                }
            )
            installed.trust.write_bytes(
                task_witness_launcher.canonical(installed_trust)
            )
            installed.trust.chmod(0o600)
            (installed.bundle / "manifest.json").write_bytes(
                task_witness_launcher.canonical(
                    {
                        "producer": {
                            "producer_id": "tricritical-review-loop-v2",
                            "contract": ineligible_contract,
                            "implementation_sha256": (
                                ineligible_producer_implementation
                            ),
                        }
                    }
                )
            )
            (installed.bundle / "manifest.json").chmod(0o600)
            ineligible_result = installed.launch()
            self.assertNotEqual(ineligible_result.returncode, 0)
            self.assertEqual(ineligible_result.stdout, "")
            review_entrypoint = validator["_validate_canonical_review_evidence"]
            review_entrypoint_globals = review_entrypoint.__globals__
            original_review_launch = review_entrypoint_globals[
                "_run_canonical_task_witness"
            ]
            observed_bundle_roots: list[Path] = []
            review_entrypoint_globals["_run_canonical_task_witness"] = (
                lambda bundle_root: (
                    observed_bundle_roots.append(bundle_root) or launch_envelope
                )
            )
            try:
                review_entrypoint(
                    review,
                    review_manifest_sha256,
                    qualification_candidate,
                )
            finally:
                review_entrypoint_globals["_run_canonical_task_witness"] = (
                    original_review_launch
                )
            self.assertEqual(observed_bundle_roots, [review.parent])

            def substitute_manifest(_bundle_root: Path) -> dict[str, object]:
                review.unlink()
                review.write_bytes(b'{"substituted":true}')
                review.chmod(0o600)
                substituted_envelope = copy.deepcopy(launch_envelope)
                substituted_witness = substituted_envelope["witness"]
                assert isinstance(substituted_witness, dict)
                substituted_projection = substituted_witness["projection"]
                assert isinstance(substituted_projection, dict)
                substituted_projection["manifest_sha256"] = hashlib.sha256(
                    review.read_bytes()
                ).hexdigest()
                refresh_review_projection(substituted_envelope)
                return substituted_envelope

            review_entrypoint_globals["_run_canonical_task_witness"] = (
                substitute_manifest
            )
            try:
                with self.assertRaisesRegex(ValueError, "terminal projection drift"):
                    review_entrypoint(
                        review,
                        review_manifest_sha256,
                        qualification_candidate,
                    )
            finally:
                review_entrypoint_globals["_run_canonical_task_witness"] = (
                    original_review_launch
                )
                review.write_bytes(b"{}")
                review.chmod(0o600)

            external_review = review.parent.parent / "external-review.json"
            external_review.write_bytes(b"{}")
            review.unlink()
            review.symlink_to(external_review)
            try:
                with self.assertRaisesRegex(ValueError, "manifest is unavailable"):
                    review_entrypoint(
                        review,
                        review_manifest_sha256,
                        qualification_candidate,
                    )
            finally:
                review.unlink()
                review.write_bytes(b"{}")
                review.chmod(0o600)
            original_review_validator = final_globals[
                "_validate_canonical_review_evidence"
            ]
            original_external_capture = final_globals[
                "_bounded_external_canonical_json_object"
            ]
            review_calls: list[tuple[object, ...]] = []
            final_globals["_validate_canonical_review_evidence"] = (
                lambda *_args: review_calls.append(_args)
            )
            external_documents = {
                manifest: (final_manifest, final_manifest_raw),
                macos: (macos_receipt, macos_raw),
                linux: (linux_receipt, linux_raw),
            }
            final_globals["_bounded_external_canonical_json_object"] = (
                lambda path, *_args, **_kwargs: external_documents[path]
            )
            try:
                drifted_linux_receipt = copy.deepcopy(linux_receipt)
                drifted_migration = next(
                    item
                    for item in drifted_linux_receipt["suite_results"]
                    if item["id"] == "migration-bridge-to-tw4"
                )
                drifted_migration["result"]["detail_stdout_length"] = 1
                drifted_migration["result"]["detail_stdout_sha256"] = hashlib.sha256(
                    b"x"
                ).hexdigest()
                drifted_result_raw = canonical(drifted_migration["result"])
                drifted_migration["process"]["stdout_length"] = len(drifted_result_raw)
                drifted_migration["process"]["stdout_sha256"] = hashlib.sha256(
                    drifted_result_raw
                ).hexdigest()
                drifted_linux_receipt = self.content_document(
                    {
                        key: value
                        for key, value in drifted_linux_receipt.items()
                        if key != "content_sha256"
                    }
                )
                drifted_linux_raw = canonical(drifted_linux_receipt)
                drifted_manifest = copy.deepcopy(final_manifest)
                drifted_manifest["targets"]["linux-x86_64"] = hashlib.sha256(
                    drifted_linux_raw
                ).hexdigest()
                drifted_manifest = self.content_document(
                    {
                        key: value
                        for key, value in drifted_manifest.items()
                        if key != "content_sha256"
                    }
                )

                def fail_if_review_seam_is_reached(*_args: object) -> None:
                    raise AssertionError(
                        "canonical review seam reached before migration drift rejection"
                    )

                final_globals["_validate_canonical_review_evidence"] = (
                    fail_if_review_seam_is_reached
                )
                with self.assertRaisesRegex(ValueError, "migration result drift"):
                    validator["validate_final_release_evidence"](
                        root,
                        candidate,
                        drifted_manifest,
                        canonical(drifted_manifest),
                        macos_receipt,
                        macos_raw,
                        drifted_linux_receipt,
                        drifted_linux_raw,
                        review,
                    )

                final_globals["_validate_canonical_review_evidence"] = (
                    lambda *_args: review_calls.append(_args)
                )
                validator["validate_final_release_evidence"](*final_arguments)
                self.assertEqual(
                    review_calls,
                    [
                        (
                            review,
                            "7" * 64,
                            final_manifest["qualification_candidate"],
                        )
                    ],
                )
                self.assertEqual(validator["main"](final_argv), 0)
                self.assertEqual(len(review_calls), 2)
            finally:
                final_globals["_validate_canonical_review_evidence"] = (
                    original_review_validator
                )
                final_globals["_bounded_external_canonical_json_object"] = (
                    original_external_capture
                )
        finally:
            final_globals.update(final_replaced)

        final_release = self.validate(*final_argv[1:])
        self.assertNotEqual(final_release.returncode, 0)
        self.assertIn(
            "TW4 release manifest is unavailable",
            final_release.stderr,
        )
        self.assertNotIn("validation is not yet available", final_release.stderr)

    def test_source_stage_cli_is_closed_and_requires_the_suite_inventory(self) -> None:
        unknown = self.validate("--not-a-real-mode")
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("Task Witness validator arguments are invalid", unknown.stderr)
        self.assertNotIn("reviewed source file path inventory drift", unknown.stderr)

        source_stage = self.validate("--source-stage")
        self.assertEqual(source_stage.returncode, 0, source_stage.stderr)
        self.assertIn("Task Witness source-stage validation passed", source_stage.stdout)

        (self.repository / SUITE_INVENTORY).unlink()
        source_stage = self.validate("--source-stage")
        self.assertNotEqual(source_stage.returncode, 0)
        self.assertIn(
            "Task Witness suite inventory is unavailable", source_stage.stderr
        )
        self.assertNotIn(
            "reviewed source file path inventory drift",
            source_stage.stderr,
        )

    def test_suite_inventory_parser_requires_the_exact_closed_document(self) -> None:
        expected = self.write_suite_inventory()
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        self.assertEqual(
            validator["validate_suite_inventory"](self.repository),
            expected,
        )

        path = self.repository / SUITE_INVENTORY
        cases: list[dict[str, object]] = []
        boolean_schema = copy.deepcopy(expected)
        boolean_schema["schema_version"] = True
        cases.append(boolean_schema)
        extra = copy.deepcopy(expected)
        extra["unexpected"] = True
        cases.append(extra)
        wrong_projection = copy.deepcopy(expected)
        entries = wrong_projection["entries"]
        assert isinstance(entries, list)
        assert isinstance(entries[0], dict)
        entries[0]["phase"] = "portable-vertical"
        cases.append(wrong_projection)
        wrong_aggregate = copy.deepcopy(expected)
        aggregates = wrong_aggregate["aggregates"]
        assert isinstance(aggregates, dict)
        aggregates["counts_sha256"] = "0" * 64
        cases.append(wrong_aggregate)
        rebound_wrong_count = copy.deepcopy(expected)
        rebound_entries = rebound_wrong_count["entries"]
        rebound_aggregates = rebound_wrong_count["aggregates"]
        assert isinstance(rebound_entries, list)
        assert isinstance(rebound_entries[0], dict)
        assert isinstance(rebound_aggregates, dict)
        rebound_entries[0]["expected_count"] = (
            int(rebound_entries[0]["expected_count"]) + 1
        )
        rebound_counts = [
            {"expected_count": entry["expected_count"], "id": entry["id"]}
            for entry in rebound_entries
            if isinstance(entry, dict)
        ]
        rebound_aggregates.update(
            {
                "counts_sha256": hashlib.sha256(
                    json.dumps(
                        rebound_counts,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "entries_sha256": hashlib.sha256(
                    json.dumps(
                        rebound_entries,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "expected_count_total": sum(
                    int(entry["expected_count"])
                    for entry in rebound_entries
                    if isinstance(entry, dict)
                ),
            }
        )
        cases.append(rebound_wrong_count)

        for invalid in cases:
            with self.subTest(invalid=invalid):
                path.write_text(
                    json.dumps(
                        invalid,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "suite inventory"):
                    validator["validate_suite_inventory"](self.repository)

        path.write_text(
            '{"schema_version":1,"schema_version":1}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "duplicate key"):
            validator["validate_suite_inventory"](self.repository)

        path.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not canonical JSON"):
            validator["validate_suite_inventory"](self.repository)

    def test_suite_inventory_capture_rejects_substitution_and_oversize(self) -> None:
        self.write_suite_inventory()
        path = self.repository / SUITE_INVENTORY
        external = Path(self.temporary.name).resolve() / "external-inventory.json"
        external.write_bytes(path.read_bytes())
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        original_open = os.open
        substituted = False

        def replace_before_leaf_open(
            target: object,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal substituted
            if (
                target == SUITE_INVENTORY.name
                and dir_fd is not None
                and not substituted
            ):
                substituted = True
                path.unlink()
                path.symlink_to(external)
            return original_open(target, flags, mode, dir_fd=dir_fd)

        with (
            mock.patch.object(validator["os"], "open", replace_before_leaf_open),
            self.assertRaisesRegex(ValueError, "suite inventory is unavailable"),
        ):
            validator["validate_suite_inventory"](self.repository)
        self.assertTrue(substituted)

        path.unlink()
        path.write_bytes(b" " * (validator["MAX_JSON_BYTES"] + 1))
        with self.assertRaisesRegex(ValueError, "suite inventory is too large"):
            validator["validate_suite_inventory"](self.repository)

    def test_suite_inventory_capture_preserves_primary_error_during_cleanup(
        self,
    ) -> None:
        self.write_suite_inventory()
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        real_close = os.close
        before = tuple(sorted(int(name) for name in os.listdir("/dev/fd")))
        closed: list[int] = []

        def close_then_error(descriptor: int) -> None:
            closed.append(descriptor)
            real_close(descriptor)
            if len(closed) == 1:
                raise OSError(5, "injected cleanup close failure")

        with (
            mock.patch.object(
                validator["os"],
                "read",
                side_effect=OSError(5, "injected inventory read failure"),
            ),
            mock.patch.object(
                validator["os"],
                "close",
                side_effect=close_then_error,
            ),
            self.assertRaisesRegex(ValueError, "suite inventory is unavailable"),
        ):
            validator["validate_suite_inventory"](self.repository)

        self.assertEqual(len(closed), len(set(closed)))
        self.assertEqual(
            tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
            before,
        )

    def test_suite_inventory_capture_reports_terminal_close_failure(self) -> None:
        self.write_suite_inventory()
        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        real_close = os.close

        for failure_ordinal in range(1, 5):
            for closes_before_error in (False, True):
                with self.subTest(
                    failure_ordinal=failure_ordinal,
                    closes_before_error=closes_before_error,
                ):
                    before = tuple(sorted(int(name) for name in os.listdir("/dev/fd")))
                    close_calls: list[int] = []
                    unclosed: int | None = None

                    def fail_selected_close(descriptor: int) -> None:
                        nonlocal unclosed
                        close_calls.append(descriptor)
                        if len(close_calls) == failure_ordinal:
                            if closes_before_error:
                                real_close(descriptor)
                            else:
                                unclosed = descriptor
                            raise OSError(5, "injected terminal close failure")
                        real_close(descriptor)

                    with (
                        mock.patch.object(
                            validator["os"],
                            "close",
                            side_effect=fail_selected_close,
                        ),
                        self.assertRaisesRegex(
                            ValueError,
                            "suite inventory cannot be closed",
                        ),
                    ):
                        validator["validate_suite_inventory"](self.repository)

                    self.assertEqual(len(close_calls), len(set(close_calls)))
                    if unclosed is not None:
                        real_close(unclosed)
                    self.assertEqual(
                        tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
                        before,
                    )

    def test_rejects_bridge_history_drift(self) -> None:
        def remove_directory(migration: Path) -> None:
            snapshot = migration / "bridge/.codex-plugin/plugin.json"
            snapshot.unlink()
            snapshot.parent.rmdir()

        def replace_with_symlink(migration: Path) -> None:
            snapshot = migration / "bridge/.claude-plugin/plugin.json"
            snapshot.unlink()
            snapshot.symlink_to("../.codex-plugin/plugin.json")

        def replace_with_fifo(migration: Path) -> None:
            snapshot = migration / "bridge/.claude-plugin/plugin.json"
            snapshot.unlink()
            os.mkfifo(snapshot)

        def add_external_hardlink(migration: Path) -> None:
            snapshot = migration / "bridge/.claude-plugin/plugin.json"
            os.link(snapshot, migration.parent / "migration-hardlink")

        cases = (
            (
                lambda migration: (migration / "unexpected").mkdir(),
                "migration inventory drift",
            ),
            (remove_directory, "migration inventory drift"),
            (
                lambda migration: (migration / "unexpected.json").write_bytes(b"{}"),
                "migration inventory drift",
            ),
            (
                lambda migration: (
                    migration / "bridge/.codex-plugin/plugin.json"
                ).unlink(),
                "migration inventory drift",
            ),
            (replace_with_symlink, "unsupported entry"),
            (replace_with_fifo, "unsupported entry"),
            (add_external_hardlink, "link count one"),
        )
        for mutation, expected in cases:
            with self.subTest(expected=expected):
                self.assert_migration_inventory_rejected(mutation, expected)

        bridge_controller = self.repository / MIGRATION_SNAPSHOT_PATHS[5]
        bridge_controller.write_bytes(bridge_controller.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "bridge controller snapshot drift"):
            self.validate_bridge_history()

    def test_rejects_boolean_bridge_history_schema_versions(self) -> None:
        for relative in (BRIDGE_IDENTITY, BRIDGE_PROVENANCE):
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as directory:
                    repository = Path(directory).resolve() / "repository"
                    shutil.copytree(self.repository, repository)
                    original = self.repository
                    self.repository = repository
                    try:
                        path = repository / relative
                        value = json.loads(path.read_text(encoding="utf-8"))
                        value["schema_version"] = True
                        path.write_text(
                            json.dumps(value, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        self.rewrite_content_digest(relative)
                        if relative == BRIDGE_PROVENANCE:
                            self.rebind_provenance_bytes()
                        with self.assertRaisesRegex(
                            ValueError,
                            "bridge (identity|provenance) contract drift",
                        ):
                            self.validate_bridge_history()
                    finally:
                        self.repository = original

    def test_rejects_nonminimal_bridge_provenance(self) -> None:
        path = self.repository / BRIDGE_PROVENANCE
        value = json.loads(path.read_text(encoding="utf-8"))
        value["objects"].append(copy.deepcopy(value["objects"][0]))
        value["objects"].sort(key=lambda item: item["sha1"])
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        self.rewrite_content_digest(BRIDGE_PROVENANCE)
        self.rebind_provenance_bytes()
        with self.assertRaisesRegex(ValueError, "provenance object inventory drift"):
            self.validate_bridge_history()

    def test_rejects_bridge_provenance_object_identity_drift(self) -> None:
        path = self.repository / BRIDGE_PROVENANCE
        value = json.loads(path.read_text(encoding="utf-8"))
        raw = base64.b64decode(value["objects"][0]["raw_base64"])
        value["objects"][0]["raw_base64"] = base64.b64encode(raw + b"x").decode()
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        self.rewrite_content_digest(BRIDGE_PROVENANCE)
        self.rebind_provenance_bytes()
        with self.assertRaisesRegex(ValueError, "provenance object identity drift"):
            self.validate_bridge_history()

    def test_rejects_bridge_client_derivation_drift(self) -> None:
        client = self.plugin / "client/task_witness_client.py"
        raw = client.read_bytes()
        self.assertEqual(raw.count(b'CLIENT_RELEASE_PROFILE = "tw4-current"'), 1)
        client.write_bytes(
            raw.replace(
                b'CLIENT_RELEASE_PROFILE = "tw4-current"',
                b'CLIENT_RELEASE_PROFILE = "tw4-drift"',
            )
        )
        with self.assertRaisesRegex(ValueError, "current client release profile drift"):
            self.validate_bridge_history()

    def assert_direct_test_rejected(self, expected: str) -> None:
        with self.assertRaisesRegex(ValueError, expected):
            self.validate_direct_test_inventory()

    def grow_reviewed_scope_to(
        self,
        paths: tuple[str, ...],
        target_lines: int,
        prefix: str,
    ) -> None:
        record = json.loads(
            (self.repository / SOURCE_SHAPE_RECORD).read_text(encoding="utf-8")
        )
        counts = record["reviewed_shape"]["file_nonblank_noncomment_lines"]
        file_tripwires = record["tripwires"]["file_nonblank_noncomment_lines"]
        remaining = target_lines - sum(counts[path] for path in paths)
        self.assertGreaterEqual(remaining, 0)
        for relative in paths:
            if remaining == 0:
                break
            available = file_tripwires[relative] - counts[relative]
            growth = min(available, remaining)
            if not growth:
                continue
            path = self.repository / relative

            def line(number: int, suffix=path.suffix) -> str:
                if suffix == ".py":
                    return f"{prefix}_{number} = None"
                return f": # {prefix}_{number}"

            path.write_text(
                path.read_text(encoding="utf-8")
                + "\n".join(line(number) for number in range(growth))
                + "\n",
                encoding="utf-8",
            )
            remaining -= growth
        self.assertEqual(
            remaining, 0, "scope cannot reach its target under file limits"
        )

    def test_allows_canonical_package_and_unrelated_suite_copy(self) -> None:
        self.validate_direct_test_inventory()
        self.assertFalse(list(self.plugin.rglob("__pycache__")))
        self.assertFalse(list(self.plugin.rglob("*.pyc")))
        shutil.copytree(self.direct_test_package, self.neutral_test_package)
        self.validate_direct_test_inventory()

    def test_rejects_extra_client_test_module(self) -> None:
        extra = self.direct_test_package / "test_extra.py"
        extra.write_text("def test_extra():\n    assert True\n", encoding="utf-8")

        self.assert_direct_test_rejected("direct-test inventory drift")

    def test_rejects_hard_link_alias_at_neutral_path(self) -> None:
        alias = self.repository / "tests" / "alias.py"
        os.link(self.direct_test_package / "test_launcher.py", alias)

        self.assert_direct_test_rejected("direct-test inventory drift")

    def test_rejects_nested_client_test_module_and_directory(self) -> None:
        nested = self.direct_test_package / "nested"
        nested.mkdir()
        extra = nested / "test_extra.py"
        extra.write_text("def test_extra():\n    assert True\n", encoding="utf-8")

        self.assert_direct_test_rejected("direct-test inventory drift")

    def test_rejects_symlinked_client_test_entry(self) -> None:
        fixture = self.direct_test_package / "_writer_guard_driver.py"
        replacement = self.direct_test_package / "writer_guard_driver.py"
        replacement.write_bytes(fixture.read_bytes())
        fixture.unlink()
        fixture.symlink_to(replacement)

        self.assert_direct_test_rejected("direct-test inventory drift")

    def test_rejects_missing_client_test_entry(self) -> None:
        (self.direct_test_package / "_writer_guard_driver.py").unlink()

        self.assert_direct_test_rejected("direct-test inventory drift")

    def test_rejects_extra_deployment_test_module(self) -> None:
        extra = self.deployment_test_package / "test_extra.py"
        extra.write_text("def test_extra():\n    assert True\n", encoding="utf-8")

        self.assert_direct_test_rejected("direct-test inventory drift")

    def test_rejects_missing_deployment_test_entry(self) -> None:
        (self.deployment_test_package / "_support.py").unlink()

        self.assert_direct_test_rejected("direct-test inventory drift")

    def test_uses_canonical_agent_plugins_v1_manifest(self) -> None:
        manifest = json.loads((self.plugin / "plugin.json").read_text())
        self.assertEqual(set(manifest), CANONICAL_MANIFEST_KEYS)
        self.assertEqual(manifest["$schema"], AGENT_PLUGIN_SCHEMA)
        self.assertEqual(manifest["name"], "task-witness")
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(set(manifest["extensions"]), {"com.openai"})
        self.assertEqual(set(manifest["extensions"]["com.openai"]), {"interface"})
        self.assertFalse((self.plugin / ".codex-plugin").exists())
        self.assertFalse((self.plugin / "skills").exists())
        self.assertFalse((self.plugin / ".mcp.json").exists())

    def test_claude_manifest_is_exact_canonical_projection(self) -> None:
        canonical = json.loads((self.plugin / "plugin.json").read_text())
        claude = json.loads(
            (self.plugin / ".claude-plugin" / "plugin.json").read_text()
        )
        self.assertEqual(set(claude), set(CANONICAL_IDENTITY_FIELDS) | {"displayName"})
        self.assertEqual(claude["displayName"], "Task Witness")
        self.assertEqual(
            {field: claude[field] for field in CANONICAL_IDENTITY_FIELDS},
            {field: canonical[field] for field in CANONICAL_IDENTITY_FIELDS},
        )

    def test_rejects_each_agent_plugins_v1_schema_drift(self) -> None:
        path = self.plugin / "plugin.json"
        original = json.loads(path.read_text())
        cases = (
            ("missing schema", lambda value: value.pop("$schema")),
            ("unknown field", lambda value: value.update({"skills": "./skills/"})),
            (
                "wrong schema",
                lambda value: value.update(
                    {
                        "$schema": (
                            "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json"
                        )
                    }
                ),
            ),
            ("invalid name", lambda value: value.update({"name": "task--witness"})),
            ("wrong version type", lambda value: value.update({"version": 1})),
            (
                "unknown author field",
                lambda value: value["author"].update({"handle": "nisavid"}),
            ),
            ("wrong keyword type", lambda value: value.update({"keywords": [1]})),
            (
                "wrong extension type",
                lambda value: value.update({"extensions": {"com.openai": []}}),
            ),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                manifest = copy.deepcopy(original)
                mutate(manifest)
                path.write_text(json.dumps(manifest, indent=2) + "\n")
                self.assert_rejected("Agent Plugins v1 manifest")

    def test_rejects_claude_manifest_projection_drift(self) -> None:
        path = self.plugin / ".claude-plugin" / "plugin.json"
        manifest = json.loads(path.read_text())
        manifest["version"] = "1.0.1"
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        self.assert_rejected("Claude manifest projection drift: version")

    def test_rejects_codex_extension_projection_drift(self) -> None:
        path = self.plugin / "plugin.json"
        manifest = json.loads(path.read_text())
        manifest["extensions"]["com.openai"]["interface"]["category"] = "Other"
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        self.assert_rejected("canonical Agent Plugin manifest drift")

    def test_rejects_current_legacy_codex_manifest(self) -> None:
        path = self.plugin / ".codex-plugin" / "plugin.json"
        path.parent.mkdir()
        path.write_text('{"name":"task-witness"}\n')
        self.assert_rejected("current legacy Codex manifest is unsupported")

    def test_rejects_mcp_surface(self) -> None:
        (self.plugin / ".mcp.json").write_text("{}\n")
        self.assert_rejected("code-only inventory drift")

    def test_rejects_duplicate_and_nonfinite_manifest_json(self) -> None:
        canonical = self.plugin / "plugin.json"
        canonical.write_text('{"name":"task-witness","name":"shadow"}')
        self.assert_rejected("duplicate key")

        shutil.copytree(
            REPOSITORY / "plugins" / "task-witness", self.plugin, dirs_exist_ok=True
        )
        claude = self.plugin / ".claude-plugin" / "plugin.json"
        claude.write_text('{"name":"task-witness","version":1e999}')
        self.assert_rejected("non-finite")

    def test_rejects_extra_code_only_inventory_entries(self) -> None:
        (self.plugin / "runtime" / "helper.py").write_text("pass\n")
        self.assert_rejected("code-only inventory drift")

    def test_rejects_skill_surface_and_generated_python_state(self) -> None:
        (self.plugin / "skills").mkdir()
        self.assert_rejected("code-only inventory drift")

        shutil.rmtree(self.plugin / "skills")
        (self.plugin / "runtime" / "task_witness.pyc").write_bytes(b"pyc")
        self.assert_rejected("code-only inventory drift")

    def test_rejects_symlinked_package_entries(self) -> None:
        runtime = self.plugin / "runtime" / "task_witness.py"
        linked = self.plugin / "runtime" / "runtime-link.py"
        linked.symlink_to(runtime)
        self.assert_rejected("code-only inventory drift")

    def test_rejects_source_module_that_exceeds_the_review_line_limit(self) -> None:
        runtime = self.plugin / "runtime" / "canonical.py"
        runtime.write_text(
            "\n".join(f"line_{number} = {number}" for number in range(1801)) + "\n"
        )
        self.assert_rejected("file source-line tripwire exceeded")

    def test_rejects_tw1_aggregate_growth_below_the_file_limits(self) -> None:
        record = json.loads(
            (self.repository / SOURCE_SHAPE_RECORD).read_text(encoding="utf-8")
        )
        limit = record["tripwires"]["aggregate_nonblank_noncomment_lines"]["tw1_client"]
        self.grow_reviewed_scope_to(
            TW1_CLIENT_PATHS,
            limit + 1,
            "tw1_aggregate_growth",
        )

        self.assert_rejected(
            "Task Witness tw1_client aggregate source-line tripwire exceeded"
        )

    def test_rejects_tw0_aggregate_growth_below_the_file_limits(self) -> None:
        record = json.loads(
            (self.repository / SOURCE_SHAPE_RECORD).read_text(encoding="utf-8")
        )
        limit = record["tripwires"]["aggregate_nonblank_noncomment_lines"]["tw0"]
        self.grow_reviewed_scope_to(
            TW0_SOURCE_PATHS,
            limit + 1,
            "tw0_aggregate_growth",
        )

        self.assert_rejected("Task Witness tw0 aggregate source-line tripwire exceeded")

    def test_rejects_tw2_control_plane_growth_below_the_file_limits(self) -> None:
        record = json.loads(
            (self.repository / SOURCE_SHAPE_RECORD).read_text(encoding="utf-8")
        )
        limit = record["tripwires"]["aggregate_nonblank_noncomment_lines"][
            "tw2_control_plane"
        ]
        self.grow_reviewed_scope_to(
            TW2_CONTROL_PLANE_PATHS,
            limit + 1,
            "tw2_control_plane_aggregate_growth",
        )

        self.assert_rejected(
            "Task Witness tw2_control_plane aggregate source-line tripwire exceeded"
        )

    def test_rejects_full_aggregate_growth_below_the_scoped_limits(self) -> None:
        record = json.loads(
            (self.repository / SOURCE_SHAPE_RECORD).read_text(encoding="utf-8")
        )
        aggregate_tripwires = record["tripwires"]["aggregate_nonblank_noncomment_lines"]
        self.grow_reviewed_scope_to(
            TW0_SOURCE_PATHS,
            aggregate_tripwires["tw0"],
            "tw0_full_aggregate_growth",
        )
        self.grow_reviewed_scope_to(
            TW1_CLIENT_PATHS,
            aggregate_tripwires["tw1_client"],
            "tw1_full_aggregate_growth",
        )
        self.grow_reviewed_scope_to(
            TW2_CONTROL_PLANE_PATHS,
            aggregate_tripwires["tw2_control_plane"],
            "tw2_control_plane_full_aggregate_growth",
        )

        self.assert_rejected(
            "Task Witness current_control_set aggregate source-line tripwire exceeded"
        )

    def test_rejects_direct_test_growth_above_the_recorded_tripwire(self) -> None:
        path = self.repository / CLIENT_TEST_GROWTH_PATH
        original = path.read_text(encoding="utf-8")
        path.write_text(
            original
            + "\n".join(f"tripwire_growth_{number} = {number}" for number in range(600))
            + "\n",
            encoding="utf-8",
        )

        self.assert_rejected(
            "direct_release_owned_tests aggregate source-line tripwire exceeded"
        )

    def test_rejects_source_reduction_without_a_new_review_record(self) -> None:
        runtime = self.plugin / "runtime" / "canonical.py"
        runtime.write_text("pass\n")
        self.assert_rejected("source-line measurement drift")

    def test_rejects_writer_guard_fixture_line_removal_without_remeasurement(
        self,
    ) -> None:
        fixture = (
            self.repository
            / "tests/plugins/task_witness_client/_writer_guard_driver.py"
        )
        fixture.write_text(
            fixture.read_text(encoding="utf-8").replace("injected = False\n", "", 1),
            encoding="utf-8",
        )

        self.assert_rejected("source-line measurement drift")

    def test_rejects_line_count_neutral_source_byte_drift(self) -> None:
        client = self.plugin / "client" / "task_witness_client.py"
        original = client.read_text(encoding="utf-8")
        client.write_text(
            original.replace("Task Witness", "TaskWitnesS", 1), encoding="utf-8"
        )
        self.assert_rejected("source byte identity drift")

    def test_rejects_writer_guard_fixture_line_neutral_byte_drift(self) -> None:
        fixture = (
            self.repository
            / "tests/plugins/task_witness_client/_writer_guard_driver.py"
        )
        fixture.write_text(
            fixture.read_text(encoding="utf-8").replace("accepted", "accePted", 1),
            encoding="utf-8",
        )

        self.assert_rejected("source byte identity drift")

    def test_rejects_invocation_profile_driver_line_neutral_byte_drift(self) -> None:
        fixture = (
            self.repository
            / "tests/plugins/task_witness_client/_invocation_profile_driver.py"
        )
        fixture.write_text(
            fixture.read_text(encoding="utf-8").replace(
                "Physical invocation-profile",
                "Physical invocation-profilE",
                1,
            ),
            encoding="utf-8",
        )

        self.assert_rejected("source byte identity drift")

    def test_rejects_malformed_source_shape_record(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        record.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        self.assert_rejected("source-shape review record contains a duplicate key")

    def test_record_preserves_the_reviewed_source_identity_contract(self) -> None:
        expected_sets = {
            "tw0": TW0_SOURCE_PATHS,
            "tw1_client": TW1_CLIENT_PATHS,
            "tw2_control_plane": TW2_CONTROL_PLANE_PATHS,
            "current_control_set": CONTROL_SET_PATHS,
            "direct_release_owned_tests": DIRECT_TEST_PATHS,
            "public_release_registration": PUBLIC_RELEASE_REGISTRATION_PATHS,
            "release_documentation": RELEASE_DOCUMENTATION_PATHS,
            "release_integration": tuple(
                sorted(
                    PUBLIC_RELEASE_REGISTRATION_PATHS + RELEASE_INTEGRATION_TEST_PATHS
                )
            ),
            "release_integration_tests": RELEASE_INTEGRATION_TEST_PATHS,
            "release_validator": RELEASE_VALIDATOR_PATHS,
            "tw4_migration_evidence": TW4_MIGRATION_EVIDENCE_PATHS,
            "tw4_qualification_contract": TW4_QUALIFICATION_CONTRACT_PATHS,
        }
        validator = runpy.run_path(str(VALIDATOR))
        self.assertEqual(validator["SOURCE_SHAPE_SETS"], expected_sets)
        self.assertEqual(validator["REVIEWED_SHAPE_PATHS"], REVIEWED_PATHS)
        self.assertEqual(len(REVIEWED_PATHS), 100)

        record = json.loads((REPOSITORY / SOURCE_SHAPE_RECORD).read_text())
        self.assertEqual(record["schema_version"], 4)
        self.assertEqual(
            record["measurement"],
            "nonblank-noncomment-lines-v2+ordered-source-byte-identity-v1",
        )
        self.assertEqual(
            record["rebaseline_requirement"], "independent-source-shape-review"
        )
        reviewed_shape = record["reviewed_shape"]
        self.assertEqual(
            set(reviewed_shape["file_nonblank_noncomment_lines"]),
            set(REVIEWED_PATHS),
        )
        self.assertEqual(set(reviewed_shape["sets"]), set(expected_sets))
        for name, paths in expected_sets.items():
            reviewed_set = reviewed_shape["sets"][name]
            self.assertEqual(reviewed_set["paths"], list(paths))
            self.assertEqual(
                reviewed_set["aggregate_nonblank_noncomment_lines"],
                sum(
                    reviewed_shape["file_nonblank_noncomment_lines"][path]
                    for path in paths
                ),
            )
        expected_file_tripwires = {
            "plugins/task-witness/client/task_witness_client.py": 8575,
            "plugins/task-witness/client/task_witness_shim.sh.in": 5,
            "plugins/task-witness/controller/policy.json": 5,
            "plugins/task-witness/controller/task_witness_deploy.py": 24650,
            "plugins/task-witness/launcher/task_witness_launch.py": 700,
            "plugins/task-witness/runtime/bundle_io.py": 340,
            "plugins/task-witness/runtime/canonical.py": 175,
            "plugins/task-witness/runtime/task_witness.py": 300,
            "plugins/task-witness/runtime/trust.py": 400,
            "plugins/task-witness/smoke/task_witness_smoke_validator.py": 45,
        }
        self.assertEqual(
            record["tripwires"]["file_nonblank_noncomment_lines"],
            expected_file_tripwires,
        )
        self.assertEqual(
            record["tripwires"]["aggregate_nonblank_noncomment_lines"],
            {
                "tw0": 1825,
                "tw1_client": 8575,
                "tw2_control_plane": 24675,
                "current_control_set": 35050,
                "direct_release_owned_tests": 60382,
                "public_release_registration": 25,
                "release_documentation": 3250,
                "release_integration": 10975,
                "release_integration_tests": 10950,
                "release_validator": 4550,
                "tw4_migration_evidence": 62375,
                "tw4_qualification_contract": 16150,
            },
        )
        aggregate_tripwires = record["tripwires"]["aggregate_nonblank_noncomment_lines"]
        self.assertLess(
            aggregate_tripwires["tw1_client"],
            sum(expected_file_tripwires[path] for path in TW1_CLIENT_PATHS),
        )
        self.assertLess(
            aggregate_tripwires["current_control_set"],
            aggregate_tripwires["tw0"]
            + aggregate_tripwires["tw1_client"]
            + aggregate_tripwires["tw2_control_plane"],
        )
        source_identity = record["source_byte_identity"]
        self.assertEqual(source_identity["algorithm"], "sha256")
        self.assertEqual(source_identity["framing"], "path-utf8-nul-sha256-hex-nul-v1")
        self.assertEqual(
            [entry["path"] for entry in source_identity["entries"]],
            list(REVIEWED_PATHS),
        )
        self.assertEqual(
            set(source_identity["aggregate_sha256_by_set"]),
            set(expected_sets),
        )
        self.assertTrue(
            all(
                len(digest) == 64
                for digest in source_identity["aggregate_sha256_by_set"].values()
            )
        )
        self.assertEqual(
            record["review_context"]["external_review"],
            {
                "required_evidence": "frozen-source-shape-review",
                "record_role": "source-shape-measurement-not-review-authentication",
            },
        )

    def test_public_contracts_are_documented_in_canonical_sources(self) -> None:
        readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")
        design = (
            REPOSITORY
            / "docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md"
        ).read_text(encoding="utf-8")
        normalized_readme = " ".join(readme.split())
        self.assertIn("externally qualified deployment TCB", normalized_readme)
        self.assertIn(
            "already-running client does not authenticate that code",
            normalized_readme,
        )
        bundle_privacy_contract = (
            "supplied bundle directory and every direct child are "
            "current-EUID-owned and inaccessible to group and other users"
        )
        self.assertIn(bundle_privacy_contract, normalized_readme)
        mount_alias_contract = (
            "Link-count-one enforcement rejects ordinary hard-link aliases; "
            "it does not detect bind-mount aliases"
        )
        self.assertIn(mount_alias_contract, normalized_readme)
        self.assertIn(
            "Deployment qualification must ensure caller bundles contain no "
            "mount alias to canonical installation state",
            normalized_readme,
        )
        self.assertIn("same-EUID deployment boundary", normalized_readme)
        self.assertIn(
            "without returning an accepted stage receipt",
            normalized_readme,
        )
        receipt_residue_contract = (
            "Fail-stop residue may include an unaccepted receipt-shaped file"
        )
        self.assertIn(receipt_residue_contract, normalized_readme)
        self.assertEqual(readme.count("- `plugins/task-witness/` contains"), 1)
        normalized_design = " ".join(design.split())
        self.assertIn(
            "docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md",
            readme,
        )
        self.assertIn(bundle_privacy_contract, normalized_design)
        self.assertIn(mount_alias_contract, normalized_design)
        self.assertIn(
            "Deployment qualification must ensure caller bundles contain no "
            "mount alias to canonical installation state",
            normalized_design,
        )
        self.assertIn("TW1–TW3 may freeze immutable stage-local candidates", design)
        self.assertIn("emits no accepted stage receipt", normalized_design)
        self.assertIn(receipt_residue_contract, normalized_design)
        self.assertIn(
            "Only TW4 may freeze a complete implementation and final public release",
            normalized_design,
        )
        implemented_activation_scope = (
            "The implemented activation surface includes first-install "
            "`absent`-to-active activation and routine active-to-active payload "
            "activation, plus derived active-to-active complete-control-set "
            "maintenance."
        )
        routine_selector_ordering = (
            "Routine A-to-B selection replaces `active.json` before "
            "`deployment.json`; B-to-A restoration replaces the prior "
            "`active.json` before the prior `deployment.json`."
        )
        recursive_stage_recovery = (
            "Routine preparation and recovery creation-disabled verify the exact "
            "recursive retained deployment and rollback receipt chain. After "
            "staging, recovery uses only the original `ActivationRequest`, exact "
            "expected current journal bytes, and the independently verified stage; "
            "it does not require the external candidate source."
        )
        routine_smoke_split = (
            "Candidate smoke is bound to the candidate receipt and runs only after "
            "candidate selectors are complete. After candidate rejection and exact "
            "selector restoration, rollback smoke is separately bound to the "
            "immediate-prior receipt; candidate smoke acceptance cannot authorize "
            "prior restoration."
        )
        routine_cleanup_order = (
            "After prior smoke acceptance, cleanup removes the candidate rollback "
            "receipt `R_B` first, then only transaction-owned files, then newly "
            "owned directories deepest-first, and the candidate deployment receipt "
            "`B` last. It leaves every shared prior file and the external stage "
            "unchanged."
        )
        routine_terminal_contract = (
            "If rollback smoke rejects, the durable terminal outcome is "
            "`recovery-required`; recovery returns that outcome without rerunning "
            "smoke and never cascades to an older retained unit."
        )
        routine_inventory_contract = (
            "Routine activation and recovery enforce a closed live-tree, receipt, "
            "and temporary inventory. Only the active baseline, the exact completed "
            "transaction prefix, the optional current step, and exact journal, "
            "install, and selector temporaries are admissible; any other file, "
            "directory, receipt, or temporary fails closed before mutation."
        )
        control_derivation_contract = (
            "Preparation derives complete-control-set maintenance internally when "
            "the exact candidate maintenance authority differs; callers continue "
            "to submit `DeploymentRequest` and cannot select the transaction class."
        )
        control_surface_contract = (
            "Compatibility-policy v2 declares one exact control-surface v1 with "
            "the supported process profile and complete client-interpreted contract "
            "catalog; deployment receipts carry the exact receipt-contract subset."
        )
        control_replacement_order = (
            "After transaction-owned additive artifacts are installed, maintenance "
            "replaces controller, policy, launcher, client, smoke bundle manifest, "
            "`active.json`, `deployment.json`, and the canonical shim in that exact "
            "order; the shim is always last."
        )
        control_recovery_contract = (
            "Process-loss recovery executes through a freshly loaded, exact staged "
            "prior controller and validates each mixed A/B prefix against the "
            "journal and independently authenticated prior and candidate policy "
            "epochs before mutation."
        )
        control_smoke_contract = (
            "Candidate smoke runs through the installed B client, policy, launcher, "
            "and receipt authority; after exact restoration, rollback smoke runs "
            "through the staged-and-restored A authority."
        )
        implemented_manual_scope = (
            "The implemented activation slice includes operator-selected exact-target "
            "`rollback_to` and K.1 post-unlink transaction-result reconciliation. It "
            "does not claim retained-history garbage collection or TW4 platform "
            "qualification."
        )
        manual_rollback_contract = (
            "`prepare_rollback_to(RollbackToRequest)` resolves one exact retained "
            "ancestor through its authenticated successor rollback edge, follows "
            "validated control-policy epochs, displays the exact current and target "
            "identities, and writes nothing. `rollback_to(...)` requires fresh exact "
            "authorization and a private external stage; it mints a new deployment "
            "receipt whose prior is the current receipt and whose endpoint authority "
            "equals the selected target, plus a rollback receipt preserving the "
            "complete current activation unit."
        )
        transaction_result_contract = (
            "Before unlinking a successful terminal transaction journal, the controller "
            "durably retains its exact bytes at "
            "`transaction-results/sha256-<transaction_id>.json`. "
            "`reconcile_transaction_result(ResultReconciliationRequest)` accepts only "
            "the original activation authority and exact expected terminal bytes, "
            "rederives the stage-bound intent and closed historical-result baseline, "
            "and verifies that the current live state still matches the outcome."
        )
        activation_lock_contract = (
            "The canonical root is a current-EUID-owned mode-`0700` directory, "
            "and the canonical `activation.lock` is an empty, current-EUID-owned, "
            "single-link regular file with exact mode `0600`; both reject "
            "permissive macOS extended `ALLOW` ACL entries, permit deny-only "
            "ACLs, and fail closed on ACL-inspection failure or observable drift "
            "in their complete filesystem identities."
        )
        directory_replay_contract = (
            "Every completed-prefix directory has exact mode `0700` and is "
            "opened with creation disabled. Only the exact validated "
            "`control-installing` pending artifact's new parent suffix may use "
            "final-path `mkdirat`; each newly observed directory must have an "
            "umask-derived mode subset of `0700`, be normalized to exact mode "
            "`0700`, and be synchronized with child-then-parent `fsync`. Recovery "
            "first performs a provisional audit, rechecks the exact lock and "
            "journal, reconciles that suffix with creation disabled, and then "
            "performs the ordinary full audit. Under the cooperative same-EUID "
            "and check-to-syscall nonclaim, a hidden child beneath an opaque "
            "mode-`000` pending directory can cause mode normalization before "
            "fail-stop, but no artifact installation, journal advance, smoke, or "
            "acceptance. Directory repair uses no temporary directory or "
            "process-global `umask` change and makes no arbitrary same-EUID "
            "authenticity claim."
        )
        for content in (normalized_readme, normalized_design):
            with self.subTest(contract="implemented activation and recovery scope"):
                self.assertIn(implemented_activation_scope, content)
                self.assertIn(routine_selector_ordering, content)
                self.assertIn(recursive_stage_recovery, content)
                self.assertIn(routine_smoke_split, content)
                self.assertIn(routine_cleanup_order, content)
                self.assertIn(routine_terminal_contract, content)
                self.assertIn(routine_inventory_contract, content)
                self.assertIn(control_derivation_contract, content)
                self.assertIn(control_surface_contract, content)
                self.assertIn(control_replacement_order, content)
                self.assertIn(control_recovery_contract, content)
                self.assertIn(control_smoke_contract, content)
                self.assertIn(implemented_manual_scope, content)
                self.assertIn(manual_rollback_contract, content)
                self.assertIn(transaction_result_contract, content)
                self.assertIn(activation_lock_contract, content)
                self.assertIn(directory_replay_contract, content)

        test_only_adapter_contract = (
            "The release-owned routine integration test uses a test-only "
            "passwd-root adapter below the real smoke supervisor. The adapter "
            "preserves `_spawn_activation_smoke_child`, exact inherited FD 3, a "
            "real child process, the byte-exact installed client and launcher, and "
            "selected installed runtime execution. It does not substitute a phase "
            "oracle or claim literal rendered-shim or host passwd-database coverage."
        )
        self.assertIn(test_only_adapter_contract, normalized_design)

    def test_source_shape_record_keeps_only_machine_enforced_review_context(
        self,
    ) -> None:
        record = json.loads(
            (REPOSITORY / SOURCE_SHAPE_RECORD).read_text(encoding="utf-8")
        )

        self.assertEqual(
            set(record["review_context"]),
            {"protocol", "external_review"},
        )
        self.assertEqual(
            record["review_context"]["protocol"],
            {
                "canonical_projection_contract": (
                    "task-witness-canonical-projection-v2"
                ),
                "complete_anchor_contract": "task-witness-complete-anchor-v1",
                "launch_envelope_contract": "task-witness-launch-envelope-v1",
            },
        )

    def test_launcher_behavior_driver_exports_only_runtime_behavior(self) -> None:
        source_path = (
            REPOSITORY
            / "tests"
            / "plugins"
            / "task_witness_client"
            / "_launcher_behavior_driver.py"
        )
        namespace = {"__file__": str(source_path)}
        exec(
            compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec"),
            namespace,
        )

        self.assertIn("_run_configured_launcher", namespace)
        self.assertFalse(
            {
                "LauncherFixture",
                "_encode_configuration",
                "configured_launcher_source",
                "install_launcher_behavior",
                "write_configured_launcher",
            }
            & set(namespace)
        )

    def test_task_witness_release_inventory_is_exact_and_measured(self) -> None:
        module = runpy.run_path(str(PUBLIC_RELEASE_VALIDATOR))
        expected_public_plugins = (
            "rolecasting",
            "versionkeeping",
            "mergecraft",
            "tricritical",
            "artifact-customs",
        )
        expected_source_stage_validated = expected_public_plugins + ("task-witness",)
        expected_package_support_paths = set(PUBLIC_RELEASE_SUPPORT_PATHS) | {
            "release/task-witness/public-release-registration.json",
            "scripts/validate_task_witness.py",
        }
        expected_registration = {
            "production_eligible": False,
            "schema_version": 1,
            "source_stage_validator_flags": ["--source-stage"],
            "support_paths": list(PUBLIC_RELEASE_SUPPORT_PATHS),
        }
        package_validator = runpy.run_path(str(VALIDATOR))
        self.assertEqual(
            package_validator["EXPECTED_PUBLIC_RELEASE_REGISTRATION"],
            expected_registration,
        )

        self.assertEqual(
            module["CONTROL_PLUGINS"],
            ("rolecasting", "versionkeeping", "mergecraft", "tricritical"),
        )
        self.assertEqual(module["SKILL_PLUGINS"], expected_public_plugins)
        self.assertEqual(module["REGISTERED_RUNTIME_PACKAGES"], ("task-witness",))
        self.assertEqual(module["PRODUCTION_RUNTIME_PACKAGES"], ())
        self.assertEqual(
            module["SOURCE_STAGE_VALIDATED_PLUGINS"],
            expected_source_stage_validated,
        )
        self.assertEqual(
            module["PRODUCTION_VALIDATED_PLUGINS"],
            expected_public_plugins,
        )
        self.assertNotIn("task-witness", module["SKILL_PLUGINS"])
        self.assertEqual(
            module["MARKETPLACE_PLUGINS"],
            {
                "tricritical": "./plugins/tricritical",
                "rolecasting": "./plugins/rolecasting",
                "versionkeeping": "./plugins/versionkeeping",
                "mergecraft": "./plugins/mergecraft",
                "artifact-customs": "./plugins/artifact-customs",
            },
        )
        self.assertEqual(
            set(module["VALIDATOR_PATHS"]),
            set(expected_source_stage_validated),
        )
        self.assertEqual(
            module["VALIDATOR_PATHS"]["task-witness"],
            "scripts/validate_task_witness.py",
        )
        registration = json.loads(
            (REPOSITORY / PUBLIC_RELEASE_REGISTRATION).read_text(encoding="utf-8")
        )
        self.assertEqual(
            registration,
            expected_registration,
        )
        self.assertEqual(
            module["PUBLIC_RELEASE_REGISTRATIONS"]["task-witness"],
            {
                "name": "task-witness",
                "package_kind": "runtime-package",
                "production_eligible": False,
                "schema_version": 1,
                "source_stage_validator_flags": ("--source-stage",),
                "support_paths": tuple(sorted(expected_package_support_paths)),
                "validator_path": "scripts/validate_task_witness.py",
                "registration_path": PUBLIC_RELEASE_REGISTRATION.as_posix(),
            },
        )
        self.assertEqual(module["PUBLIC_RELEASE_RUNTIME_PACKAGES"], ("task-witness",))
        self.assertEqual(
            module["PLUGIN_SUPPORT_PATHS"]["task-witness"],
            expected_package_support_paths,
        )
        catalog_path = "release/public-release-runtime-packages.json"
        self.assertIn(
            catalog_path,
            module["all_scope_paths"](module["SOURCE_STAGE_VALIDATED_PLUGINS"]),
        )
        self.assertNotIn(
            catalog_path,
            module["all_scope_paths"](module["PRODUCTION_VALIDATED_PLUGINS"]),
        )
        source_stage_identities = module["candidate_identities"](
            REPOSITORY,
            module["SOURCE_STAGE_VALIDATED_PLUGINS"],
        )
        production_identities = module["candidate_identities"](
            REPOSITORY,
            module["PRODUCTION_VALIDATED_PLUGINS"],
        )
        self.assertIn("task-witness", source_stage_identities["plugins"])
        self.assertNotIn("task-witness", production_identities["plugins"])
        self.assertNotIn("task-witness", module["MARKETPLACE_PLUGINS"])
        self.assertNotIn("task-witness", module["PRODUCTION_RUNTIME_PACKAGES"])
        with self.assertRaisesRegex(
            module["ReleaseError"],
            "expected identity plugin inventory drift",
        ):
            module["validate_expected_identities"](
                source_stage_identities,
                production_identities,
                module["PRODUCTION_VALIDATED_PLUGINS"],
            )
        record = json.loads(
            (REPOSITORY / SOURCE_SHAPE_RECORD).read_text(encoding="utf-8")
        )
        self.assertEqual(
            record["reviewed_shape"]["sets"]["release_integration_tests"]["paths"],
            list(RELEASE_INTEGRATION_TEST_PATHS),
        )
        self.assertEqual(
            record["reviewed_shape"]["sets"]["public_release_registration"]["paths"],
            list(PUBLIC_RELEASE_REGISTRATION_PATHS),
        )
        self.assertEqual(
            record["reviewed_shape"]["sets"]["release_integration"]["paths"],
            sorted(PUBLIC_RELEASE_REGISTRATION_PATHS + RELEASE_INTEGRATION_TEST_PATHS),
        )
        expected_scope_claim = (
            "While ineligible, Task Witness is omitted from the production-scoped "
            "plugin, validator, test, marketplace, plugin-eval, expected-identity, "
            "receipt plugin-identity, and scoped release-contract and release-scope "
            "inventories. Generic registration validation still binds"
        )
        expected_candidate_claim = (
            "the whole-repository Git candidate identity and release-receipt "
            "`candidate` field still bind its committed bytes"
        )
        for relative in (
            "README.md",
            "docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md",
        ):
            with self.subTest(relative=relative):
                content = " ".join(
                    (REPOSITORY / relative).read_text(encoding="utf-8").split()
                )
                self.assertIn(expected_scope_claim, content)
                self.assertIn(expected_candidate_claim, content)

    def test_design_states_cpython_runtime_trust_boundary(self) -> None:
        design = (
            REPOSITORY
            / "docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md"
        ).read_text(encoding="utf-8")
        normalized_design = " ".join(design.split())

        self.assertIn(
            "Deployment externally qualifies the complete CPython 3.13+ runtime "
            "closure: main executable, stdlib, extension modules, loader/shared "
            "libs, and selected site packages.",
            normalized_design,
        )
        self.assertIn(
            "Post-startup, the Python client checks only main-executable path and "
            "bytes plus implementation and version for drift.",
            normalized_design,
        )
        self.assertIn(
            "It does not claim to authenticate already-executing runtime code.",
            normalized_design,
        )
        self.assertIn(
            "The full CPython 3.13+ runtime closure is an externally qualified "
            "deployment TCB, not code authenticated by the already-running Python "
            "client.",
            normalized_design,
        )
        self.assertIn(
            "Full CPython runtime-closure authentication, including already-executing "
            "runtime code.",
            normalized_design,
        )
        self.assertIn(
            "Marketplace-friendly updates with no runtime discovery and explicit "
            "external CPython runtime-closure qualification.",
            normalized_design,
        )
        for stale_claim in (
            "The controller's exact source bytes, policy document, interpreter, and",
            "It records the exact control, runtime, interpreter, and trust-context bytes.",
            "compatibility-policy, interpreter-microversion, and receipt bytes may change",
            "The deployment receipt separately binds the interpreter executable SHA-256.",
            "Marketplace-friendly updates without dynamic runtime trust.",
        ):
            with self.subTest(stale_claim=stale_claim):
                self.assertNotIn(stale_claim, normalized_design)

    def test_shared_release_validator_consumes_package_registration(self) -> None:
        integration_root = Path(self.temporary.name) / "public-release-consumer"
        shutil.copytree(REPOSITORY / "scripts", integration_root / "scripts")
        catalog = integration_root / "release/public-release-runtime-packages.json"
        catalog.parent.mkdir(parents=True)
        shutil.copy2(
            REPOSITORY / "release/public-release-runtime-packages.json", catalog
        )
        registration_path = integration_root / PUBLIC_RELEASE_REGISTRATION
        registration_path.parent.mkdir(parents=True)
        shutil.copy2(REPOSITORY / PUBLIC_RELEASE_REGISTRATION, registration_path)
        registration = json.loads(registration_path.read_text(encoding="utf-8"))
        (integration_root / "plugins" / "task-witness").mkdir(parents=True)
        for relative in registration["support_paths"]:
            source = REPOSITORY / relative
            destination = integration_root / relative
            if source.is_dir():
                destination.mkdir(parents=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

        def observed_support_paths() -> set[str]:
            script = (
                "import json, runpy, sys; "
                "module = runpy.run_path(sys.argv[1]); "
                "print(json.dumps(sorted("
                "module['PLUGIN_SUPPORT_PATHS']['task-witness'])))"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-I",
                    "-S",
                    "-c",
                    script,
                    str(integration_root / "scripts" / PUBLIC_RELEASE_VALIDATOR.name),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return set(json.loads(result.stdout))

        self.assertEqual(
            observed_support_paths(),
            set(registration["support_paths"])
            | {
                PUBLIC_RELEASE_REGISTRATION.as_posix(),
                RELEASE_VALIDATOR_PATHS[0],
            },
        )

        sentinel = "tests/task_witness_registration_consumption_sentinel.py"
        (integration_root / sentinel).write_text("", encoding="utf-8")
        registration["support_paths"].append(sentinel)
        registration["support_paths"].sort()
        registration_path.write_text(
            json.dumps(registration, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self.assertIn(sentinel, observed_support_paths())

    def test_shared_release_requires_task_witness_source_stage_membership(self) -> None:
        integration_root = Path(self.temporary.name) / "removed-task-witness"
        shutil.copytree(REPOSITORY / "scripts", integration_root / "scripts")
        catalog = integration_root / "release/public-release-runtime-packages.json"
        catalog.parent.mkdir(parents=True)
        shutil.copy2(
            REPOSITORY / "release/public-release-runtime-packages.json", catalog
        )
        registration = integration_root / PUBLIC_RELEASE_REGISTRATION
        registration.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY / PUBLIC_RELEASE_REGISTRATION, registration)
        catalog_payload = json.loads(catalog.read_text(encoding="utf-8"))
        catalog_payload["runtime_packages"].remove("task-witness")
        catalog.write_text(
            json.dumps(catalog_payload, sort_keys=True) + "\n", encoding="utf-8"
        )
        registration.unlink()

        module = runpy.run_path(
            str(integration_root / "scripts" / PUBLIC_RELEASE_VALIDATOR.name)
        )
        with self.assertRaisesRegex(
            module["ReleaseError"],
            "required source-stage runtime package is missing: task-witness",
        ):
            module["validate_public_release_registration_inventory"](integration_root)

    def test_source_stage_copy_executes_the_package_contract_test(self) -> None:
        module = runpy.run_path(str(PUBLIC_RELEASE_VALIDATOR))
        snapshot = Path(self.temporary.name) / "source-stage-snapshot"
        module["copy_release_scope"](
            REPOSITORY,
            snapshot,
            module["SOURCE_STAGE_VALIDATED_PLUGINS"],
        )
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "unittest",
                "tests.test_task_witness_package.TaskWitnessPackageTests.test_shared_release_validator_consumes_package_registration",
            ],
            cwd=snapshot,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_public_release_registration_drift(self) -> None:
        registration_path = self.repository / PUBLIC_RELEASE_REGISTRATION
        original = registration_path.read_text(encoding="utf-8")
        cases = (
            lambda value: value["support_paths"].append(
                "tests/unmeasured_task_witness_release.py"
            ),
            lambda value: value.update({"production_eligible": True}),
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                registration = json.loads(original)
                mutation(registration)
                registration["support_paths"].sort()
                registration_path.write_text(
                    json.dumps(registration, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                self.assert_rejected("public-release registration drift")
                registration_path.write_text(original, encoding="utf-8")

    def test_rejects_missing_public_release_registration(self) -> None:
        (self.repository / PUBLIC_RELEASE_REGISTRATION).unlink()

        self.assert_rejected("public-release registration is missing")

    def test_rejects_symlinked_public_release_registration(self) -> None:
        registration = self.repository / PUBLIC_RELEASE_REGISTRATION
        replacement = self.repository / "public-release-registration.json"
        replacement.write_bytes(registration.read_bytes())
        registration.unlink()
        registration.symlink_to(replacement)

        self.assert_rejected("public-release registration must be a regular file")

    def test_rejects_compact_review_context_drift(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        original = record.read_text(encoding="utf-8")
        cases = {
            "protocol": lambda value: value["review_context"]["protocol"].__setitem__(
                "launch_envelope_contract", "task-witness-launch-envelope-v2"
            ),
            "review role": lambda value: value["review_context"][
                "external_review"
            ].__setitem__("record_role", "review-proof"),
            "extra field": lambda value: value["review_context"].__setitem__(
                "rationale", "unchecked prose"
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                value = json.loads(original)
                mutate(value)
                record.write_text(json.dumps(value), encoding="utf-8")
                self.assert_rejected("source-shape review context drift")
                record.write_text(original, encoding="utf-8")

    def test_rejects_source_byte_identity_order_drift(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        value = json.loads(record.read_text(encoding="utf-8"))
        value["source_byte_identity"]["entries"].reverse()
        record.write_text(json.dumps(value), encoding="utf-8")
        self.assert_rejected("ordered source-byte identity drift")

    def test_rejects_source_shape_record_contract_drift(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        original = record.read_text(encoding="utf-8")
        cases = (
            ("unknown root field", lambda value: value.__setitem__("extra", True)),
            (
                "bad measurement",
                lambda value: value.__setitem__("measurement", "lines-v0"),
            ),
            (
                "missing source byte identity",
                lambda value: value.pop("source_byte_identity"),
            ),
            (
                "missing review context",
                lambda value: value.pop("review_context"),
            ),
            (
                "non-integer schema version",
                lambda value: value.__setitem__("schema_version", 1.0),
            ),
            (
                "bad rebaseline requirement",
                lambda value: value.__setitem__(
                    "rebaseline_requirement", "self-approve"
                ),
            ),
            (
                "path inventory drift",
                lambda value: value["reviewed_shape"][
                    "file_nonblank_noncomment_lines"
                ].pop("plugins/task-witness/runtime/trust.py"),
            ),
            (
                "inconsistent aggregate",
                lambda value: value["reviewed_shape"]["sets"][
                    "current_control_set"
                ].__setitem__("aggregate_nonblank_noncomment_lines", 1),
            ),
            (
                "boolean integer",
                lambda value: value["tripwires"][
                    "file_nonblank_noncomment_lines"
                ].__setitem__(TW1_CLIENT_PATHS[0], True),
            ),
            (
                "reviewed shape above tripwire",
                lambda value: value["tripwires"][
                    "file_nonblank_noncomment_lines"
                ].__setitem__(TW1_CLIENT_PATHS[0], 1800),
            ),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                malformed = json.loads(original)
                mutate(malformed)
                record.write_text(json.dumps(malformed), encoding="utf-8")
                result = self.validate()
                self.assertNotEqual(result.returncode, 0)
                record.write_text(original, encoding="utf-8")

        aggregate = json.loads(original)["reviewed_shape"]["sets"][
            "current_control_set"
        ]["aggregate_nonblank_noncomment_lines"]
        aggregate_field = f'"aggregate_nonblank_noncomment_lines": {aggregate}'
        self.assertIn(aggregate_field, original)
        record.write_text(
            original.replace(
                aggregate_field,
                '"aggregate_nonblank_noncomment_lines": 1e999',
                1,
            ),
            encoding="utf-8",
        )
        self.assert_rejected("source-shape review record contains a non-finite number")

    def test_rejects_source_shape_record_symlink(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        replacement = self.repository / "source-shape-review.json"
        replacement.write_bytes(record.read_bytes())
        record.unlink()
        record.symlink_to(replacement)
        self.assert_rejected("source-shape review record must be a regular file")

    def test_rejects_symlinked_reviewed_path_components(self) -> None:
        cases = (
            ("scripts/validate_task_witness.py", False),
            ("tests/plugins/task_witness_client/test_launcher.py", False),
            ("tests/plugins", True),
        )
        for relative, directory in cases:
            with self.subTest(relative=relative):
                path = self.repository / relative
                replacement = Path(self.temporary.name) / path.name
                if directory:
                    shutil.copytree(path, replacement)
                    shutil.rmtree(path)
                else:
                    shutil.copy2(path, replacement)
                    path.unlink()
                path.symlink_to(replacement, target_is_directory=directory)
                expected_error = (
                    "Task Witness direct-test inventory drift"
                    if relative.startswith("tests/plugins/task_witness_client")
                    else "Task Witness reviewed source path"
                )
                self.assert_rejected(expected_error)
                path.unlink()
                if directory:
                    shutil.copytree(replacement, path)
                else:
                    shutil.copy2(replacement, path)

    def test_rejects_hard_linked_standalone_reviewed_path(self) -> None:
        reviewed = self.repository / "tests/test_task_witness_package.py"
        alias = Path(self.temporary.name) / "reviewed-test-alias.py"
        os.link(reviewed, alias)

        validator = runpy.run_path(str(self.repository / "scripts" / VALIDATOR.name))
        with self.assertRaisesRegex(
            ValueError,
            "Task Witness reviewed source path must have link count one",
        ):
            validator["verified_reviewed_path"](
                self.repository, "tests/test_task_witness_package.py"
            )

    def test_reference_validator_rejects_release_validator_source_reduction(
        self,
    ) -> None:
        validator = self.repository / "scripts" / VALIDATOR.name
        validator.write_text("pass\n", encoding="utf-8")

        result = self.validate_with_reference()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source-line measurement drift", result.stderr)

    def test_rejects_release_integration_test_closure_removal(self) -> None:
        record = self.repository / SOURCE_SHAPE_RECORD
        value = json.loads(record.read_text(encoding="utf-8"))
        value["reviewed_shape"]["sets"]["release_integration_tests"]["paths"] = []
        record.write_text(json.dumps(value), encoding="utf-8")

        self.assert_rejected("release_integration_tests path drift")

    def test_rejects_runtime_syntax_error(self) -> None:
        runtime = self.plugin / "runtime" / "task_witness.py"
        runtime.write_text("def broken(:\n")
        self.assert_rejected("syntax is invalid")

    def test_rejects_client_syntax_error(self) -> None:
        client = self.plugin / "client" / "task_witness_client.py"
        client.write_text("def broken(:\n", encoding="utf-8")
        self.assert_rejected("syntax is invalid")

    def test_rejects_controller_syntax_error(self) -> None:
        controller = self.plugin / "controller" / "task_witness_deploy.py"
        controller.write_text("def broken(:\n", encoding="utf-8")
        self.assert_rejected("syntax is invalid")
