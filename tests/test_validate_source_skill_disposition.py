from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "validate_source_skill_disposition.py"
LINEAGE_ROOT = Path("release/source-skill-lineage")
DISPOSITION_ROOT = Path("release/source-skill-disposition")
LEDGER = DISPOSITION_ROOT / "disposition-ledger.json"
REFRESH = DISPOSITION_ROOT / "release-refresh-contract.json"
FINAL_RESCOUT_SCHEMA = (
    DISPOSITION_ROOT / "final-installed-library-rescout.schema.json"
)


def canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def content_sha256(value: dict[str, object]) -> str:
    return canonical_sha256(
        {key: item for key, item in value.items() if key != "content_sha256"}
    )


class SourceSkillDispositionValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name)
        shutil.copytree(REPO_ROOT / LINEAGE_ROOT, self.repository / LINEAGE_ROOT)
        shutil.copytree(REPO_ROOT / DISPOSITION_ROOT, self.repository / DISPOSITION_ROOT)
        ledger = self.load(LEDGER)
        contract = self.load(REFRESH)
        evidence_paths = {
            path
            for disposition in ledger["dispositions"]
            for path in disposition["evidence_paths"]
        }
        identity_paths = {
            path
            for distribution in contract["candidate_identity"]["distributions"]
            for path in distribution["identity_artifact_paths"]
        }
        derivation = contract["regeneration"]["affected_distribution_derivation"]
        closure_paths = set(derivation["common_dependency_paths"])
        for distribution in derivation["distribution_closure"]:
            closure_paths.update(
                [distribution["manifest_path"], distribution["package_root"]]
            )
            for field in (
                "documentation_paths",
                "evaluation_paths",
                "identity_artifact_paths",
                "test_paths",
                "topology_and_owner_paths",
                "validation_entrypoints",
            ):
                closure_paths.update(distribution[field])
        for relative in sorted(evidence_paths | identity_paths | closure_paths):
            source = REPO_ROOT / relative
            target = self.repository / relative
            if target.exists():
                continue
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    def run_validator(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.repository)],
            check=False,
            capture_output=True,
            text=True,
        )

    def load(self, relative: Path) -> dict[str, object]:
        return json.loads((self.repository / relative).read_text())

    def write(self, relative: Path, value: dict[str, object]) -> None:
        value["content_sha256"] = content_sha256(value)
        (self.repository / relative).write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )

    def assert_rejected(self, expected: str) -> None:
        completed = self.run_validator()
        self.assertEqual(completed.returncode, 1, completed)
        self.assertEqual(
            completed.stderr,
            f"source-skill-disposition: {expected}\n",
        )

    def test_committed_contract_is_valid(self) -> None:
        completed = self.run_validator()
        self.assertEqual(completed.returncode, 0, completed)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(completed.stdout, "source-skill-disposition-valid\n")

    def test_requires_exactly_one_disposition_per_evidence_contribution(self) -> None:
        ledger = self.load(LEDGER)
        ledger["dispositions"].pop()
        self.write(LEDGER, ledger)

        self.assert_rejected("contribution disposition coverage drift")

    def test_rejects_duplicate_contribution_dispositions(self) -> None:
        ledger = self.load(LEDGER)
        ledger["dispositions"].append(dict(ledger["dispositions"][0]))
        ledger["dispositions"].sort(key=lambda item: item["contribution_id"])
        self.write(LEDGER, ledger)

        self.assert_rejected("contribution dispositions must be sorted and unique")

    def test_rejects_source_identity_mismatch(self) -> None:
        ledger = self.load(LEDGER)
        ledger["dispositions"][0]["source_id"] = "cursor-thermos"
        self.write(LEDGER, ledger)

        self.assert_rejected("contribution disposition source drift")

    def test_rejects_unsettled_or_unknown_disposition(self) -> None:
        ledger = self.load(LEDGER)
        ledger["dispositions"][0]["disposition"] = "unresolved"
        self.write(LEDGER, ledger)

        self.assert_rejected("contribution disposition value drift")

    def test_rejects_a_rewritten_valid_disposition_decision(self) -> None:
        ledger = self.load(LEDGER)
        ledger["dispositions"][0]["disposition"] = "retain-side-by-side"
        self.write(LEDGER, ledger)

        self.assert_rejected("disposition inventory drift")

    def test_aggregate_managed_skill_decisions_cover_source_inventory(self) -> None:
        ledger = self.load(LEDGER)
        matt = next(
            item
            for item in ledger["dispositions"]
            if item["contribution_id"] == "matt-pocock-contribution-unresolved"
        )
        matt["skill_dispositions"].pop()
        self.write(LEDGER, ledger)

        self.assert_rejected("matt-pocock-skill-set skill disposition coverage drift")

    def test_matt_source_stays_immutable_while_the_versionkeeping_derivative_is_explicit(
        self,
    ) -> None:
        ledger = self.load(LEDGER)
        matt = next(
            item
            for item in ledger["dispositions"]
            if item["contribution_id"] == "matt-pocock-contribution-unresolved"
        )
        derivative = next(
            item
            for item in matt["skill_dispositions"]
            if item["skill_ids"] == ["resolving-merge-conflicts"]
        )
        derivative["disposition"] = "retain-side-by-side"
        self.write(LEDGER, ledger)

        self.assert_rejected("matt-pocock-skill-set skill disposition value drift")

    def test_matt_code_review_and_tricritical_remain_explicitly_side_by_side(
        self,
    ) -> None:
        ledger = self.load(LEDGER)
        matt = next(
            item
            for item in ledger["dispositions"]
            if item["contribution_id"] == "matt-pocock-contribution-unresolved"
        )
        code_review = next(
            item
            for item in matt["skill_dispositions"]
            if item["skill_ids"] == ["code-review"]
        )
        code_review["durable_owners"] = ["tricritical"]
        self.write(LEDGER, ledger)

        self.assert_rejected("matt-pocock code-review relationship drift")

    def test_review_atlas_public_core_and_private_overlay_stay_separate(self) -> None:
        ledger = self.load(LEDGER)
        review_atlas = next(
            item
            for item in ledger["dispositions"]
            if item["contribution_id"] == "review-atlas-lineage"
        )
        review_atlas["skill_dispositions"][0]["disposition"] = "absorb-or-refresh"
        self.write(LEDGER, ledger)

        self.assert_rejected("review-atlas component disposition drift")

    def test_unmapped_thermos_rules_remain_blocking_for_daybreak_issue_56(self) -> None:
        ledger = self.load(LEDGER)
        thermos = next(
            item
            for item in ledger["dispositions"]
            if item["contribution_id"] == "cursor-thermos-rule-lineage"
        )
        thermos["disposition"] = "absorb-or-refresh"
        self.write(LEDGER, ledger)

        self.assert_rejected("Thermos rule-level mapping disposition drift")

    def test_superpowers_retirement_does_not_grant_removal_authority(self) -> None:
        ledger = self.load(LEDGER)
        superpowers = next(
            item
            for item in ledger["dispositions"]
            if item["contribution_id"] == "superpowers-contribution-unresolved"
        )
        superpowers["authority"]["host_removal"] = "granted"
        self.write(LEDGER, ledger)

        self.assert_rejected("source disposition grants host removal authority")

    def test_source_evidence_bindings_are_raw_byte_exact(self) -> None:
        ledger = self.load(LEDGER)
        ledger["evidence_bindings"]["contribution_ledger"]["sha256"] = (
            "sha256:" + "0" * 64
        )
        self.write(LEDGER, ledger)

        self.assert_rejected("contribution ledger evidence binding drift")

    def test_refresh_contract_uses_the_agreed_twenty_four_hour_ceiling(self) -> None:
        contract = self.load(REFRESH)
        contract["freshness"]["maximum_age_seconds"] = 172800
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh maximum age drift")

    def test_refresh_contract_uses_the_exact_agreed_freshness_boundaries(self) -> None:
        mutations = {
            "measured_from": "start of upstream refresh",
            "measured_to": "start of candidate qualification",
        }
        for field, replacement in mutations.items():
            with self.subTest(field=field):
                contract = self.load(REFRESH)
                contract["freshness"][field] = replacement
                self.write(REFRESH, contract)
                self.assert_rejected(f"release refresh {field} boundary drift")
                shutil.copy2(REPO_ROOT / REFRESH, self.repository / REFRESH)

    def test_refresh_contract_rescouts_the_complete_installed_instruction_library(
        self,
    ) -> None:
        contract = self.load(REFRESH)
        contract["installed_library_rescout"]["surfaces"].remove(
            "recursively-referenced-supporting-materials"
        )
        self.write(REFRESH, contract)

        self.assert_rejected("installed instruction library rescout scope drift")

    def test_refresh_contract_invalidates_only_the_affected_dependency_closure(
        self,
    ) -> None:
        contract = self.load(REFRESH)
        contract["invalidation"]["requalification_scope"] = "blanket-all-plugins"
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh dependency closure drift")

    def test_refresh_contract_defines_the_complete_candidate_identity_tuple(
        self,
    ) -> None:
        contract = self.load(REFRESH)
        contract["candidate_identity"]["package_tuple_fields"].remove(
            "plugin_manifest_sha256"
        )
        self.write(REFRESH, contract)

        self.assert_rejected("candidate identity contract drift")

    def test_refresh_contract_defines_all_distribution_identity_artifacts(self) -> None:
        contract = self.load(REFRESH)
        contract["candidate_identity"]["distributions"].pop()
        self.write(REFRESH, contract)

        self.assert_rejected("candidate identity distribution contract drift")

    def test_refresh_contract_requires_a_versioned_final_rescout_artifact(self) -> None:
        contract = self.load(REFRESH)
        contract["final_rescout_artifact"]["schema"]["sha256"] = (
            "sha256:" + "0" * 64
        )
        self.write(REFRESH, contract)

        self.assert_rejected(
            "final installed-library rescout artifact contract drift"
        )

    def test_final_rescout_schema_is_raw_byte_bound(self) -> None:
        schema = self.load(FINAL_RESCOUT_SCHEMA)
        schema["title"] = "Incomplete rescout"
        (self.repository / FINAL_RESCOUT_SCHEMA).write_text(
            json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )

        self.assert_rejected(
            "final installed-library rescout schema binding drift"
        )

    def test_final_rescout_rejects_a_drop_without_a_rationale(self) -> None:
        schema = self.load(FINAL_RESCOUT_SCHEMA)
        entry_schema = {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            **schema["$defs"]["inventory_entry"],
        }
        entry = {
            "disposition": "drop-with-reason",
            "identity_sha256": "sha256:" + "0" * 64,
            "owner_id": "personal",
            "profile_id": "personal",
            "referenced_materials_sha256": "sha256:" + "0" * 64,
            "route_id": "obsolete-route",
            "source_kind": "standalone",
            "surface": "standalone-skills",
        }

        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validators.validator_for(entry_schema)(entry_schema).validate(
                entry
            )

        entry["rationale"] = ""
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validators.validator_for(entry_schema)(entry_schema).validate(
                entry
            )

        entry["rationale"] = "The route duplicates a stronger durable owner."
        jsonschema.validators.validator_for(entry_schema)(entry_schema).validate(entry)

    def test_refresh_contract_defines_the_complete_regeneration_fanout(self) -> None:
        contract = self.load(REFRESH)
        contract["regeneration"]["required_outputs"].remove(
            "routing-and-composed-compatibility-evidence-for-affected-closure"
        )
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh regeneration output drift")

    def test_every_invalidation_trigger_has_a_change_class(self) -> None:
        contract = self.load(REFRESH)
        del contract["regeneration"]["trigger_to_change_classes"][
            "instruction-discovery-route"
        ]
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh invalidation trigger fanout drift")

    def test_task_witness_identity_artifacts_trigger_candidate_invalidation(
        self,
    ) -> None:
        contract = self.load(REFRESH)
        contract["invalidation"]["immediate_triggers"][0] = (
            "candidate-content-lock"
        )
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh invalidation trigger drift")

    def test_identity_artifact_changes_do_not_claim_package_byte_changes(
        self,
    ) -> None:
        contract = self.load(REFRESH)
        contract["regeneration"]["change_classes"]["identity-artifact-change"][
            "changes_package_bytes"
        ] = True
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh change-class fanout drift")

    def test_every_change_class_regenerates_the_final_rescout_artifact(self) -> None:
        contract = self.load(REFRESH)
        contract["regeneration"]["change_classes"]["package-byte-change"][
            "required_steps"
        ].remove("regenerate-versioned-final-installed-library-rescout-artifact")
        self.write(REFRESH, contract)

        self.assert_rejected(
            "final installed-library rescout regeneration dependency drift"
        )

    def test_every_change_class_resolves_to_concrete_output_selectors(self) -> None:
        contract = self.load(REFRESH)
        contract["regeneration"]["change_classes"]["source-evidence-change"][
            "output_selectors"
        ].pop()
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh change-class output selector drift")

    def test_distribution_closure_uses_concrete_repository_paths(self) -> None:
        contract = self.load(REFRESH)
        contract["regeneration"]["affected_distribution_derivation"][
            "common_dependency_paths"
        ][0] = "plugin-topology-contracts"
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh affected-distribution derivation drift")

    def test_distribution_closure_has_explicit_dependency_edges(self) -> None:
        contract = self.load(REFRESH)
        contract["regeneration"]["affected_distribution_derivation"][
            "dependency_graph"
        ]["edges"].pop()
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh dependency graph drift")

    def test_transitive_dependency_closure_is_derived_from_edges(self) -> None:
        contract = self.load(REFRESH)
        contract["regeneration"]["affected_distribution_derivation"][
            "dependency_graph"
        ]["transitive_closures"]["tricritical"].remove("artifact-customs")
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh transitive dependency closure drift")

    def test_dependency_edges_are_supported_by_live_topology(self) -> None:
        topology_path = Path("plugins/artifact-customs/topology.json")
        topology = self.load(topology_path)
        topology["outward_plugins"].remove("tricritical")
        (self.repository / topology_path).write_text(
            json.dumps(topology, indent=2, ensure_ascii=False) + "\n"
        )

        self.assert_rejected("Artifact Customs dependency evidence drift")

    def test_refresh_contract_binds_both_installed_host_manifests(self) -> None:
        contract = self.load(REFRESH)
        contract["regeneration"]["receipt_artifact_bindings"] = [
            binding
            for binding in contract["regeneration"]["receipt_artifact_bindings"]
            if binding["binding_id"]
            != "installed-host-initial-work-macos-v1"
        ]
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh receipt binding drift")

    def test_refresh_contract_binds_the_future_final_rescout_artifact(self) -> None:
        contract = self.load(REFRESH)
        contract["regeneration"]["receipt_artifact_bindings"] = [
            binding
            for binding in contract["regeneration"]["receipt_artifact_bindings"]
            if binding["binding_id"] != "final-installed-library-rescout-v1"
        ]
        self.write(REFRESH, contract)

        self.assert_rejected("release refresh receipt binding drift")

    def test_refresh_contract_assigns_final_refresh_to_issue_45(self) -> None:
        contract = self.load(REFRESH)
        contract["workflow"]["final_refresh_owner_issue"] = 50
        self.write(REFRESH, contract)

        self.assert_rejected("final release refresh owner drift")

    def test_artifacts_reject_private_or_machine_local_paths(self) -> None:
        ledger = self.load(LEDGER)
        ledger["dispositions"][0]["evidence_paths"] = [
            "/Users/ivan/.agents/skills/example/SKILL.md"
        ]
        self.write(LEDGER, ledger)

        self.assert_rejected("disposition ledger contains a private or absolute path")

    def test_disposition_evidence_paths_must_exist(self) -> None:
        (self.repository / "plugins/tricritical/NOTICE").unlink()

        self.assert_rejected("disposition evidence path is missing")


if __name__ == "__main__":
    unittest.main()
