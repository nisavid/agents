from __future__ import annotations

import json
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
EVAL_ROOT = REPOSITORY / "evals" / "artifact-customs"
CORPUS_PATH = EVAL_ROOT / "corpus.json"

SKILLS = {
    "assessing-third-party-components": "read-only",
    "adopting-third-party-components": "new-or-revised-trust-boundary",
    "maintaining-third-party-components": "existing-policy",
}
OUTWARD_COMPOSITION_ALLOWLIST = {
    "rolecasting",
    "tricritical",
    "versionkeeping",
    "mergecraft",
}


class ArtifactCustomsEvalCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        self.scenarios = {item["id"]: item for item in self.corpus["scenarios"]}

    def test_corpus_has_exact_schema_and_fixture_coverage(self) -> None:
        self.assertEqual(
            set(self.corpus),
            {"schema_version", "skills", "scheduler", "composition", "scenarios"},
        )
        self.assertEqual(self.corpus["schema_version"], 1)
        self.assertEqual(self.corpus["skills"], SKILLS)
        fixture_paths = {
            path.relative_to(EVAL_ROOT).as_posix()
            for path in (EVAL_ROOT / "fixtures").glob("*.md")
        }
        referenced = {item["fixture_path"] for item in self.corpus["scenarios"]}
        self.assertEqual(referenced, fixture_paths)
        self.assertEqual(len(self.scenarios), len(self.corpus["scenarios"]))
        for scenario in self.corpus["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                self.assertEqual(
                    set(scenario),
                    {
                        "id",
                        "skill",
                        "fixture_path",
                        "prompt",
                        "required_outcome",
                        "expectations",
                    },
                )
                if scenario["skill"] is not None:
                    self.assertIn(scenario["skill"], SKILLS)
                self.assertTrue((EVAL_ROOT / scenario["fixture_path"]).is_file())
                self.assertTrue(scenario["prompt"])
                self.assertTrue(scenario["expectations"])

    def test_three_skill_surface_is_exclusive_and_bound_to_its_authority(self) -> None:
        self.assertEqual(set(self.corpus["skills"]), set(SKILLS))
        self.assertEqual(
            self.scenarios["nonexistent-release-before-mutation"]["skill"],
            "assessing-third-party-components",
        )
        self.assertEqual(
            self.scenarios["new-trust-boundary-escalation"]["skill"],
            "adopting-third-party-components",
        )
        self.assertEqual(
            self.scenarios["deterministic-deviation-investigates"]["skill"],
            "maintaining-third-party-components",
        )

    def test_drift_and_cold_start_routing_contracts_are_explicit(self) -> None:
        self.assertEqual(
            self.corpus["scheduler"]["onboarding"]["operator_selects"],
            ["activation", "cadence", "autonomyMode"],
        )
        write_families = {
            "policy-revoked-before-write": "source_write_rebound",
            "policy-drift-before-policy-write": "policy_write_rebound",
            "authority-drift-before-retained-evidence-write": "retained_evidence_write_rebound",
            "head-drift-before-forge-close": "forge_close_rebound",
            "head-drift-before-forge-publish-approve-or-merge": "forge_publish_approve_or_merge_rebound",
        }
        for identifier, family_expectation in write_families.items():
            scenario = self.scenarios[identifier]
            self.assertEqual(
                scenario["required_outcome"], "no-mutation-after-rebind-drift"
            )
            self.assertIn("zero_mutation", scenario["expectations"])
            self.assertIn(family_expectation, scenario["expectations"])
        self.assertIn(
            "exact_forge_action_authority",
            self.scenarios["head-drift-before-forge-close"]["expectations"],
        )
        self.assertTrue(
            {
                "candidate_identity_rebound",
                "policy_identity_rebound",
                "authority_identity_rebound",
            }.issubset(self.scenarios["head-drift-before-forge-close"]["expectations"])
        )
        hard_no_go = self.scenarios["hard-no-go-without-forge-action-authority"]
        self.assertEqual(
            hard_no_go["required_outcome"],
            "no-forge-action-without-exact-authority",
        )
        self.assertTrue(
            {
                "hard_no_go",
                "separate_exact_forge_action_authority",
                "immediate_pre_forge_rebind",
                "zero_forge_mutation",
            }.issubset(hard_no_go["expectations"])
        )
        for identifier in (
            "governed-advisory-cold-start",
            "ungoverned-read-only-clearance-cold-start",
        ):
            self.assertIsNone(self.scenarios[identifier]["skill"])

    def test_evidence_dispositions_preserve_no_go_and_policy_limited_acceptance(
        self,
    ) -> None:
        required = {
            "nonexistent-release-before-mutation": ("no-go", "before_mutation"),
            "absent-attestation-policy-acceptance": (
                "corroborate-and-accept",
                "recorded_absence",
            ),
            "absent-attestation-without-policy": ("no-go", "policy_explicit"),
            "source-artifact-mismatch": ("no-go", "source_artifact_equivalence"),
            "malware-detection": ("no-go", "malware"),
            "incompatible-license": ("no-go", "license_compatibility"),
            "unresolved-regression": ("no-go", "regression_resolution"),
        }
        for identifier, (outcome, expectation) in required.items():
            with self.subTest(identifier=identifier):
                scenario = self.scenarios[identifier]
                self.assertEqual(scenario["required_outcome"], outcome)
                self.assertIn(expectation, scenario["expectations"])
        acceptance = self.scenarios["absent-attestation-policy-acceptance"]
        self.assertTrue(
            {
                "recorded_absence",
                "policy_explicit",
                "exact_registry_integrity",
                "canonical_source_tag_equivalence",
                "metadata_and_license",
                "conformance",
            }.issubset(acceptance["expectations"])
        )

    def test_incomplete_and_deterministic_deviations_require_deeper_autonomous_investigation(
        self,
    ) -> None:
        for identifier in (
            "incomplete-evidence-investigates",
            "deterministic-deviation-investigates",
        ):
            with self.subTest(identifier=identifier):
                scenario = self.scenarios[identifier]
                self.assertEqual(
                    scenario["required_outcome"], "deeper-autonomous-investigation"
                )
                self.assertIn("no_premature_escalation", scenario["expectations"])

    def test_only_authority_deadlocks_and_irreducible_ambiguity_escalate(self) -> None:
        required = {
            "new-trust-boundary-escalation": "undelegated_trust_legal_runtime_expansion",
            "critical-fix-deadlock-escalation": "critical_fix_deadlock",
            "irreducible-ambiguity-escalation": "full_investigation_complete",
        }
        for identifier, expectation in required.items():
            with self.subTest(identifier=identifier):
                scenario = self.scenarios[identifier]
                self.assertEqual(scenario["required_outcome"], "escalation")
                self.assertIn(expectation, scenario["expectations"])

    def test_write_path_rebinds_the_exact_candidate_before_mutation(self) -> None:
        scenario = self.scenarios["exact-candidate-rebind-before-writes"]
        self.assertEqual(scenario["skill"], "maintaining-third-party-components")
        self.assertEqual(scenario["required_outcome"], "rebind-before-writes")
        self.assertTrue(
            {
                "exact_candidate",
                "rebind_before_write",
                "no_stale_identity_write",
            }.issubset(scenario["expectations"])
        )

    def test_authorized_adoption_and_maintenance_keep_distinct_authority(self) -> None:
        adoption = self.scenarios["authorized-adoption"]
        self.assertEqual(adoption["skill"], "adopting-third-party-components")
        self.assertEqual(adoption["required_outcome"], "adopt-under-explicit-policy")
        self.assertTrue(
            {
                "assessment_consumed",
                "new_boundary_authority",
                "exact_component",
                "durable_policy",
            }.issubset(adoption["expectations"])
        )

        maintenance = self.scenarios["routine-existing-policy-update"]
        self.assertEqual(maintenance["skill"], "maintaining-third-party-components")
        self.assertEqual(
            maintenance["required_outcome"], "accepted-maintenance-handoff"
        )
        self.assertTrue(
            {
                "assessment_consumed",
                "existing_policy",
                "matching_mutation_authority",
                "exact_pr_binding",
            }.issubset(maintenance["expectations"])
        )

    def test_advisory_replace_and_retire_do_not_infer_authority(self) -> None:
        advisory = self.scenarios["advisory-only"]
        self.assertEqual(advisory["required_outcome"], "assessment-only")
        self.assertTrue(
            {"advisory_authority_only", "no_write", "no_authority_inference"}.issubset(
                advisory["expectations"]
            )
        )
        for identifier, outcome in (
            ("replace-component", "replace-under-policy"),
            ("retire-component", "retire-under-policy"),
        ):
            with self.subTest(identifier=identifier):
                scenario = self.scenarios[identifier]
                self.assertEqual(
                    scenario["skill"], "maintaining-third-party-components"
                )
                self.assertEqual(scenario["required_outcome"], outcome)
                self.assertTrue(
                    {
                        "existing_policy",
                        "matching_mutation_authority",
                        "retained_decision_evidence",
                    }.issubset(scenario["expectations"])
                )

    def test_manual_foreign_and_native_scheduler_routes_share_one_envelope(
        self,
    ) -> None:
        foreign = self.scenarios["foreign-harness-invocation"]
        self.assertEqual(foreign["required_outcome"], "bounded-envelope-invocation")
        self.assertTrue(
            {
                "manual_envelope",
                "no_transport_authority",
                "same_lock_and_receipts",
                "no_scheduler_preference",
            }.issubset(foreign["expectations"])
        )
        for identifier, adapter in (
            ("codex-current-harness-onboarding", "codex_chatgpt_adapter"),
            ("claude-current-harness-onboarding", "claude_desktop_adapter"),
        ):
            with self.subTest(identifier=identifier):
                scenario = self.scenarios[identifier]
                self.assertEqual(
                    scenario["required_outcome"], "offer-current-harness-schedule"
                )
                self.assertTrue(
                    {
                        adapter,
                        "user_selects_activation",
                        "user_selects_cadence",
                        "user_selects_autonomy_mode",
                        "manual_remains",
                        "no_scheduler_preference",
                        "inspect_existing_or_possible_maintenance_process_first",
                    }.issubset(scenario["expectations"])
                )

        forge = self.scenarios["head-drift-before-forge-publish-approve-or-merge"]
        self.assertTrue(
            {
                "candidate_identity_rebound",
                "policy_identity_rebound",
                "authority_identity_rebound",
                "forge_publish_approve_or_merge_rebound",
            }.issubset(forge["expectations"])
        )

    def test_onboarding_and_autonomy_scenarios_preserve_operator_control(self) -> None:
        required = {
            "codex-current-harness-onboarding": {
                "offer_best_effort_integration_or_alignment",
                "context_sensitive_standalone_cadence_when_uncertain",
            },
            "claude-current-harness-onboarding": {
                "recommend_context_sensitive_cadence_when_none_found",
            },
            "report-only-autonomy": {
                "never_autonomous_upgrade",
                "research_report_recommend",
            },
            "high-confidence-autonomy": {
                "adequate_verification",
                "security_review",
                "high_confidence_safe_judgment",
                "escalate_with_comprehensive_report_when_not_confident",
            },
            "confidence-forward-deferred-autonomy": {
                "upgrade_when_confident",
                "defer_when_uncertain",
                "later_research_cycle_for_ecosystem_evidence",
            },
            "compromised-artifact-fail-closed": {
                "compromised_artifact_fail_closed",
                "named_existing_policy_not_expanded_by_autonomy",
                "no_write",
            },
        }
        for identifier, expectations in required.items():
            with self.subTest(identifier=identifier):
                self.assertTrue(
                    expectations.issubset(self.scenarios[identifier]["expectations"])
                )

    def test_out_of_scope_cases_select_no_artifact_customs_skill(self) -> None:
        for identifier in (
            "out-of-scope-model-dataset",
            "out-of-scope-credentials-saas",
            "out-of-scope-release",
            "out-of-scope-generic-pr",
        ):
            with self.subTest(identifier=identifier):
                scenario = self.scenarios[identifier]
                self.assertIsNone(scenario["skill"])
                self.assertEqual(
                    scenario["required_outcome"], "no-artifact-customs-route"
                )

    def test_scheduler_contract_is_manual_permanent_and_harness_neutral(self) -> None:
        scheduler = self.corpus["scheduler"]
        self.assertEqual(
            scheduler,
            {
                "invocation": "permanent-manual-repository-and-pr-invocation",
                "envelope": "harness-neutral",
                "onboarding": {
                    "inspect_first": "existing-or-possible-dependency-or-component-maintenance-schedule-or-analogous-process",
                    "found_or_suspected": "offer-best-effort-integration-or-alignment-from-available-evidence",
                    "uncertain_suitability": "also-offer-context-sensitive-standalone-cadence",
                    "none_found": "recommend-context-sensitive-cadence",
                    "operator_selects": ["activation", "cadence", "autonomyMode"],
                },
                "autonomy_modes": [
                    "report-only",
                    "high-confidence",
                    "confidence-forward-deferred",
                ],
                "recommended_autonomy_mode": "high-confidence",
                "recommendation_is_consent": False,
                "scheduled_autonomy_mode": "operator-selected-required",
                "compromised_artifact": "fail-closed",
                "mode_authority": {
                    "assess": "read-only",
                    "adopt": "explicit-new-or-revised-boundary",
                    "maintain": "named-existing-policy-not-expanded-by-autonomy",
                },
                "lifecycle_actions": {
                    "assess": ["clearance"],
                    "adopt": ["adopt"],
                    "maintain": [
                        "update",
                        "advisory",
                        "replace",
                        "seal",
                        "retire",
                        "pull-request",
                    ],
                },
                "adapters": ["codex-chatgpt", "claude-desktop"],
                "foreign_harness": "simulation-required",
                "preferred_scheduler": None,
                "preferred_cadence": None,
            },
        )

    def test_outward_composition_is_allowlisted_and_has_no_reverse_edges(self) -> None:
        composition = self.corpus["composition"]
        self.assertEqual(
            set(composition["outward_plugins"]), OUTWARD_COMPOSITION_ALLOWLIST
        )
        self.assertEqual(
            len(composition["outward_plugins"]), len(OUTWARD_COMPOSITION_ALLOWLIST)
        )
        self.assertTrue(composition["reverse_edges_forbidden"])
        self.assertEqual(
            composition["phase7_control_projection"],
            ["rolecasting", "versionkeeping", "mergecraft", "tricritical"],
        )


if __name__ == "__main__":
    unittest.main()
