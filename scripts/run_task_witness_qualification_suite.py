#!/usr/bin/env python3
"""Run one closed Task Witness qualification-suite selector."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import platform as host_platform
import pwd
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType

SUITE_RESULT_CONTRACT = "task-witness-tw4-suite-result-v1"
DETAIL_MAX_BYTES = 1024 * 1024
DIRECT_CHILD_TIMEOUT_SECONDS = 180
DIRECT_CHILD_GROUP_OBSERVATION_MAX_BYTES = 64 * 1024 * 1024
QUALIFICATION_TEST = Path("tests/test_task_witness_qualification.py")
QUALIFICATION_RUNNER_SELECTORS = (
    "test_platform_profile_parser_requires_exact_closed_v1_document",
    "test_suite_inventory_parser_accepts_exact_closed_v1_document",
    "test_suite_inventory_parser_rejects_schema_and_projection_drift",
    "test_suite_inventory_parser_rejects_count_and_aggregate_drift",
    "test_suite_result_parser_accepts_exact_closed_v1_document",
    "test_suite_result_parser_rejects_schema_and_value_drift",
    "test_runtime_closure_parser_requires_exact_closed_v1_document",
)
PACKAGE_CONTRACT_TESTS = (
    (
        Path("tests/test_task_witness_package.py"),
        "tests.test_task_witness_package",
        "TaskWitnessPackageTests",
        (
            "test_validates_carried_bridge_history_without_git_repository",
            "test_release_manifest_parser_matches_frozen_bridge_schema",
            "test_release_manifest_parser_rejects_schema_and_binding_drift",
            "test_host_receipt_parser_accepts_both_complete_v1_target_shapes",
            "test_host_receipt_parser_rejects_the_static_mutation_matrix",
            "test_bridge_history_uses_one_captured_identity_and_provenance_pair",
            "test_validator_argv_parser_accepts_only_the_closed_mode_grammars",
            "test_source_stage_cli_is_closed_and_requires_the_suite_inventory",
            "test_suite_inventory_parser_requires_the_exact_closed_document",
            "test_suite_inventory_capture_rejects_substitution_and_oversize",
            "test_suite_inventory_capture_preserves_primary_error_during_cleanup",
            "test_suite_inventory_capture_reports_terminal_close_failure",
            "test_rejects_bridge_history_drift",
            "test_rejects_boolean_bridge_history_schema_versions",
            "test_rejects_nonminimal_bridge_provenance",
            "test_rejects_bridge_provenance_object_identity_drift",
            "test_rejects_bridge_client_derivation_drift",
            "test_allows_canonical_package_and_unrelated_suite_copy",
            "test_rejects_extra_client_test_module",
            "test_rejects_hard_link_alias_at_neutral_path",
            "test_rejects_nested_client_test_module_and_directory",
            "test_rejects_symlinked_client_test_entry",
            "test_rejects_missing_client_test_entry",
            "test_rejects_extra_deployment_test_module",
            "test_rejects_missing_deployment_test_entry",
            "test_uses_canonical_agent_plugins_v1_manifest",
            "test_claude_manifest_is_exact_canonical_projection",
            "test_rejects_each_agent_plugins_v1_schema_drift",
            "test_rejects_claude_manifest_projection_drift",
            "test_rejects_codex_extension_projection_drift",
            "test_rejects_current_legacy_codex_manifest",
            "test_rejects_mcp_surface",
            "test_rejects_duplicate_and_nonfinite_manifest_json",
            "test_rejects_extra_code_only_inventory_entries",
            "test_rejects_skill_surface_and_generated_python_state",
            "test_rejects_symlinked_package_entries",
            "test_rejects_source_module_that_exceeds_the_review_line_limit",
            "test_rejects_tw1_aggregate_growth_below_the_file_limits",
            "test_rejects_tw0_aggregate_growth_below_the_file_limits",
            "test_rejects_tw2_control_plane_growth_below_the_file_limits",
            "test_rejects_full_aggregate_growth_below_the_scoped_limits",
            "test_rejects_direct_test_growth_above_the_recorded_tripwire",
            "test_rejects_source_reduction_without_a_new_review_record",
            "test_rejects_writer_guard_fixture_line_removal_without_remeasurement",
            "test_rejects_line_count_neutral_source_byte_drift",
            "test_rejects_writer_guard_fixture_line_neutral_byte_drift",
            "test_rejects_invocation_profile_driver_line_neutral_byte_drift",
            "test_rejects_malformed_source_shape_record",
            "test_record_preserves_the_reviewed_source_identity_contract",
            "test_public_contract_documentation_ownership_is_local_and_registered",
            "test_source_shape_record_keeps_only_machine_enforced_review_context",
            "test_launcher_behavior_driver_exports_only_runtime_behavior",
            "test_task_witness_release_inventory_is_exact_and_measured",
            "test_design_states_cpython_runtime_trust_boundary",
            "test_shared_release_validator_consumes_package_registration",
            "test_shared_release_requires_task_witness_source_stage_membership",
            "test_source_stage_copy_executes_the_package_contract_test",
            "test_rejects_public_release_registration_drift",
            "test_rejects_missing_public_release_registration",
            "test_rejects_symlinked_public_release_registration",
            "test_rejects_compact_review_context_drift",
            "test_rejects_source_byte_identity_order_drift",
            "test_rejects_source_shape_record_contract_drift",
            "test_rejects_source_shape_record_symlink",
            "test_rejects_symlinked_reviewed_path_components",
            "test_rejects_hard_linked_standalone_reviewed_path",
            "test_reference_validator_rejects_release_validator_source_reduction",
            "test_rejects_release_integration_test_closure_removal",
            "test_rejects_runtime_syntax_error",
            "test_rejects_client_syntax_error",
            "test_rejects_controller_syntax_error",
        ),
    ),
)
PACKAGE_CONTRACT_SELECTORS = tuple(
    f"{module_name}.{case_name}.{method_name}"
    for _relative, module_name, case_name, method_names in PACKAGE_CONTRACT_TESTS
    for method_name in method_names
)
TASK_WITNESS_SOURCE_STAGE_SELECTORS = ("direct.task-witness-source-stage",)
PUBLIC_RELEASE_SOURCE_STAGE_SELECTORS = ("direct.public-release-source-stage",)
LITERAL_RENDERED_SHIM_SELECTORS = ("direct.literal-rendered-shim",)
CLIENT_COMMON_TESTS = (
    (
        Path("tests/plugins/task_witness_client/test_activation_smoke.py"),
        "tests.plugins.task_witness_client.test_activation_smoke",
        "ActivationSmokeTests",
        (
            "test_exact_inherited_lock_and_receipt_owned_smoke_inputs_are_accepted",
            "test_release_profiles_reject_wrong_bridge_smoke_phase",
            "test_routine_candidate_smoke_uses_b_as_authority_and_live_target",
            "test_recursive_routine_smoke_uses_c_authority_for_c_or_b_live_target",
            "test_recursive_routine_reactivates_prior_historical_trust",
            "test_recursive_routine_rollback_rejects_candidate_provider_substitution",
            "test_recursive_routine_rollback_rejects_candidate_intrinsic_substitution",
            "test_recursive_routine_rollback_rejects_candidate_source_revision_drift",
            "test_recursive_routine_candidate_source_projection_rejects_one_field_drift",
            "test_recursive_routine_deep_ancestor_source_projection_is_receipt_local",
            "test_recursive_routine_external_provider_rollback_uses_c_authority",
            "test_recursive_routine_reactivation_preserves_other_history",
            "test_routine_retained_receipt_cannot_list_its_active_trust_as_history",
            "test_recursive_routine_reactivation_requires_exact_history_rebound",
            "test_recursive_routine_rejects_missing_tampered_or_extra_ancestor",
            "test_routine_external_provider_declaration_binds_source_in_both_phases",
            "test_routine_provider_declaration_crossbind_rejects_substitutions",
            "test_routine_candidate_allows_new_trust_with_exact_a_history",
            "test_routine_candidate_requires_exact_prior_trust_history",
            "test_routine_candidate_rejects_control_drift_from_prior_before_launcher",
            "test_routine_candidate_rejects_a_authorization_substituted_for_b",
            "test_active_prior_rejects_control_maintenance_class_substitution",
            "test_routine_candidate_rejects_b_to_a_chain_mismatch",
            "test_routine_candidate_rejects_transaction_target_swaps",
            "test_routine_candidate_rejects_mixed_live_receipt_or_active_state",
            "test_routine_candidate_rejects_inactive_receipt_inventory_extra",
            "test_routine_rollback_smoke_uses_b_authority_for_live_a_target",
            "test_routine_rollback_requires_exact_retained_b_authority_digest",
            "test_routine_smoke_phase_must_match_the_live_target",
            "test_routine_rejects_rebound_rollback_authority_substitutions",
            "test_nonempty_inherited_activation_lock_is_rejected_before_launcher",
            "test_mode_0700_inherited_activation_lock_is_rejected_by_journal",
            "test_visible_activation_lock_full_identity_drift_is_rejected_after_smoke",
            "test_rollback_smoke_requires_receipt_authority_for_the_active_prior",
            "test_ordinary_caller_without_inherited_fd3_cannot_run_smoke",
            "test_public_validation_rejects_while_activation_transaction_exists",
            "test_public_validation_rechecks_transaction_absence_after_launcher",
            "test_activation_transaction_is_descriptor_pinned_through_post_child_checks",
            "test_inherited_fd3_without_live_transaction_cannot_run_smoke",
            "test_fd3_from_a_different_open_file_description_is_rejected",
            "test_handoff_target_must_match_the_phase_target_unit",
            "test_phase_target_unit_must_match_live_unit_after_parser_acceptance",
            "test_journal_generation_requires_the_previous_digest_after_sequence_one",
            "test_transaction_id_must_match_the_full_immutable_intent",
            "test_stage_must_be_an_exact_part_of_the_immutable_intent",
            "test_stage_plan_must_match_receipt_derived_smoke_authority",
            "test_stage_receipt_identity_is_recovery_evidence_not_smoke_authority",
            "test_maintenance_identity_must_match_receipt_smoke_authority",
            "test_rollback_manifest_must_match_receipt_smoke_authority",
            "test_non_target_unit_must_have_the_exact_active_unit_shape",
            "test_routine_payload_smoke_requires_an_active_prior_unit",
            "test_smoke_phase_rejects_a_pending_control_install_step",
            "test_smoke_phase_rejects_premature_candidate_acceptance",
            "test_smoke_phase_rejects_premature_rollback_acceptance",
            "test_smoke_phase_rejects_a_terminal_result",
            "test_smoke_producer_must_be_the_receipt_owned_intrinsic_role",
            "test_smoke_bundle_path_is_canonical_and_not_journal_selectable",
            "test_lock_proof_uses_two_fresh_probes_and_restores_fd3_cloexec",
            "test_launcher_cannot_inherit_fd3_while_parent_retains_exclusive_lock",
        ),
    ),
    (
        Path("tests/plugins/task_witness_client/test_compatibility_policy_v2.py"),
        "tests.plugins.task_witness_client.test_compatibility_policy_v2",
        "CompatibilityPolicyV2Tests",
        (
            "test_current_exact_surface_and_receipt_subset_are_accepted",
            "test_outer_and_nested_policy_shapes_are_strict",
            "test_complete_contract_catalog_is_exact",
            "test_process_profile_is_current_exact",
            "test_receipt_contract_subset_is_exact",
        ),
    ),
    (
        Path("tests/plugins/task_witness_client/test_control_maintenance_smoke.py"),
        "tests.plugins.task_witness_client.test_control_maintenance_smoke",
        "ControlMaintenanceSmokeTests",
        (
            "test_candidate_smoke_uses_stage_bound_candidate_controls",
            "test_rollback_smoke_uses_stage_bound_prior_controls",
            "test_swapped_candidate_and_prior_stage_authority_rejects_before_launch",
            "test_stage_receipt_identity_classification_and_order_are_exact",
            "test_stage_inventory_and_candidate_prior_receipts_are_exact",
            "test_control_preimage_order_and_content_are_exact",
            "test_phase_and_target_selection_are_exact",
            "test_current_and_terminal_receipt_contracts_are_validated",
        ),
    ),
    (
        Path("tests/plugins/task_witness_client/test_invocation_profile.py"),
        "tests.plugins.task_witness_client.test_invocation_profile",
        "InvocationProfileTests",
        (
            "test_receipt_requires_the_exact_exclusive_lock_budget",
            "test_valid_canonical_invocation_emits_the_exact_launcher_envelope",
            "test_loaded_client_rejects_a_newer_receipt_bound_source_generation",
            "test_client_accepts_the_real_launcher_runtime_and_validator",
            "test_shared_lock_contention_uses_the_resource_exit_class",
            "test_shared_lock_drift_during_contention_is_an_installation_error",
            "test_terminal_lock_drift_precedes_timeout_classification",
            "test_directory_open_failures_close_every_acquired_descriptor",
            "test_launcher_child_receives_fixed_profile_without_high_fd_leak",
            "test_changing_descriptor_inventory_fails_before_target_fork",
            "test_noncanonical_client_executor_rejects_before_installation_access",
            "test_cpython_before_3_13_rejects_before_installation_access",
            "test_noncanonical_signal_state_rejects_before_installation_access",
            "test_unobservable_signal_disposition_rejects_before_installation_access",
            "test_retained_instrumentation_rejects_before_installation_access",
            "test_self_erasing_pre_import_hook_is_outside_the_client_detector",
            "test_freed_global_monitoring_mask_is_version_sensitive",
            "test_freed_module_monitoring_mask_exits_without_returning_to_the_module",
            "test_handler_installation_failure_is_silent_and_restores_every_capture",
            "test_first_post_installation_memory_error_is_diagnostic_eligible",
            "test_long_ambient_process_timers_remain_observable_before_rejection",
            "test_short_inherited_real_timer_ends_before_profile_or_deployment_access",
            "test_unobservable_process_timers_reject_before_installation_access",
            "test_preexisting_direct_child_rejects_before_installation_access",
            "test_unknown_launcher_pid_after_fork_memory_error_is_reaped",
            "test_persistent_launcher_recovery_deadline_failure_prevents_fork",
            "test_launcher_context_preparation_failure_prevents_fork",
            "test_launcher_publishes_pid_before_the_first_parent_fallible_step",
            "test_launcher_pid_publication_failure_wildcard_reaps_without_release",
            "test_launcher_and_writer_wait_for_parent_gate_sequence",
            "test_unregistered_cpython_thread_rejects_before_installation_or_fork",
            "test_json_boundaries_reject_numeric_type_aliases",
            "test_canonical_receipt_accepts_exact_contract_and_rejects_stage_or_drift",
            "test_valid_large_envelope_within_stdout_limit_is_accepted",
            "test_stdout_limit_is_inclusive_for_one_valid_canonical_envelope",
            "test_keyboard_interrupt_uses_the_resource_exit_class",
            "test_cancellation_handlers_precede_process_preflight",
            "test_canonical_root_comes_from_the_effective_users_passwd_entry",
            "test_missing_runtime_payload_never_reaches_the_launcher",
            "test_receipt_bound_shim_with_wrong_mode_never_reaches_the_launcher",
            "test_installation_directories_require_exact_mode_0700",
            "test_owner_private_bundle_does_not_require_installation_mode",
            "test_bundle_rejects_group_or_other_permissions",
            "test_acl_lookup_failure_rejects_private_node",
            "test_bundle_inside_canonical_installation_never_reaches_launcher",
            "test_bundle_hard_link_to_installation_never_reaches_launcher",
            "test_shared_interpreter_parent_does_not_require_installation_mode",
        ),
    ),
    (
        Path("tests/plugins/task_witness_client/test_launcher.py"),
        "tests.plugins.task_witness_client.test_launcher",
        "TaskWitnessLauncherTests",
        (
            "test_controller_context_opens_and_executes_intrinsic_smoke_validator",
            "test_intrinsic_smoke_validator_rejects_mismatching_challenge",
            "test_copied_launcher_subprocess_rejects_its_arbitrary_root",
            "test_optimized_interpreters_reject_before_emitting_an_envelope",
            "test_copied_launcher_rejects_when_a_valid_canonical_tree_exists",
            "test_substituted_entrypoint_is_rejected_before_its_side_effect",
            "test_pre_execution_snapshot_recheck_rejects_visible_payload_drift",
            "test_rechecks_all_bundle_and_validator_descriptors_before_visible_paths",
            "test_runtime_bundle_privacy_uses_the_effective_uid",
            "test_snapshot_rechecks_retained_descriptor_bytes_before_visible_paths",
            "test_protected_nodes_reject_group_or_other_permissions",
            "test_bundle_hard_link_to_installation_is_rejected",
            "test_acl_lookup_failure_rejects_private_node",
            "test_runtime_bundle_file_limit_rejects_before_validator",
            "test_runtime_bundle_file_limit_accepts_exact_limit",
            "test_registered_validator_runs_as_trusted_in_process_code",
            "test_registered_validator_future_annotations_are_source_scoped",
            "test_runtime_rechecks_reject_byte_identical_visible_replacements",
            "test_active_record_requires_the_full_typed_release_identity",
            "test_valid_launch_returns_a_canonical_anchor_bound_envelope",
            "test_public_release_identity_is_result_bound",
            "test_interpreter_identity_is_exactly_record_bound_and_result_bound",
            "test_historical_mode_is_passed_and_bound_into_the_result",
            "test_launcher_rejects_a_payload_runtime_identity_false_claim",
            "test_launcher_rejects_missing_or_malformed_runtime_bundle_identity",
            "test_active_generation_must_name_the_retained_runtime_identity",
            "test_bundle_identity_covers_each_retained_file",
            "test_complete_anchor_rejects_a_different_valid_bundle",
            "test_oversized_json_numbers_reject_before_the_validator",
            "test_oversized_active_record_numbers_reject_before_payload_execution",
            "test_noncanonical_interpreter_options_reject_before_payloads",
            "test_launcher_requires_exact_remote_debug_shutdown_option",
            "test_launcher_exposes_no_public_validate_entrypoint",
            "test_trust_identity_covers_the_exact_retained_context",
            "test_historical_mode_admits_only_historically_usable_lifecycle_entries",
            "test_validator_exit_or_output_cannot_produce_a_successful_envelope",
            "test_payload_interrupt_becomes_the_standard_subprocess_interrupt_status",
            "test_direct_runtime_import_and_execution_fail_without_launch_context",
            "test_symlink_fifo_and_record_disagreement_are_rejected",
            "test_post_snapshot_payload_and_active_record_mutation_are_rejected",
            "test_unselected_partial_rotation_leaves_old_active_generation_viable",
        ),
    ),
    (
        Path("tests/plugins/task_witness_client/test_process_supervision.py"),
        "tests.plugins.task_witness_client.test_process_supervision",
        "ProcessSupervisionTests",
        (
            "test_missing_or_invalid_lifecycle_never_authorizes_signals",
            "test_cleanup_deadline_arming_is_once_only_for_graceful_and_forced_work",
            "test_lost_pid_deadline_arming_remains_independent_from_cleanup",
            "test_group_cleanup_shares_one_deadline_across_grace_force_probe_and_reap",
            "test_cleanup_deadline_failure_blocks_platform_cleanup_work",
            "test_direct_child_consume_gets_a_distinct_forced_cleanup_event",
            "test_group_signal_observation_fault_recovers_before_exact_reap",
            "test_persistent_group_observation_failure_is_bounded_and_ambiguous",
            "test_group_observation_echild_forbids_all_later_numeric_signals",
            "test_group_cleanup_recovers_term_and_kill_observation_faults_then_reaps",
            "test_exact_reap_does_not_rebase_or_wait_after_the_original_deadline",
            "test_darwin_leaf_probe_requires_time_and_exact_child_responsibility",
            "test_darwin_preterm_and_forced_group_state_retries_are_independent",
            "test_cleanup_signal_deadlines_block_retries_and_fallbacks",
            "test_darwin_probe_fault_survives_ownership_loss",
            "test_unbounded_launcher_stderr_is_terminated_at_the_byte_limit",
            "test_unbounded_launcher_stdout_is_terminated_at_the_byte_limit",
            "test_simultaneous_bounded_streams_do_not_deadlock",
            "test_exited_launcher_with_inherited_open_pipes_is_bounded",
            "test_terminal_launcher_outcomes_kill_ordinary_descendants",
            "test_ordinary_rejected_launcher_preserves_its_semantic_exit",
            "test_process_spawn_time_counts_against_the_validation_deadline",
            "test_spawn_waits_for_post_setup_session_acknowledgement",
            "test_pre_setsid_readiness_failure_uses_exact_pid_cleanup",
            "test_terminal_state_is_observed_before_the_group_leader_is_reaped",
            "test_real_darwin_echild_forbids_later_numeric_signals",
            "test_darwin_live_probe_then_kill_failure_rejects",
            "test_darwin_probe_checks_deadline_before_each_numeric_probe",
            "test_darwin_one_shot_cleanup_allocation_failure_reaps_the_leader",
            "test_post_sleep_profile_faults_force_and_reap_term_ignoring_children",
            "test_darwin_lost_ownership_never_signals_after_deadline_unavailable",
            "test_non_darwin_reaps_only_after_process_group_quiesces",
            "test_non_darwin_requires_a_successful_group_kill_before_reap",
            "test_linux_task_snapshot_rejects_a_missing_leader_task",
            "test_linux_task_snapshot_observes_deadline_during_directory_walk",
            "test_linux_proc_stat_parses_final_delimiter_and_rejects_bad_input",
            "test_unsupported_platform_rejects_before_installation_access",
            "test_persistent_darwin_wait_failure_is_bounded_and_state_unknown",
            "test_pregate_cleanup_failure_supersedes_stream_materialization_error",
            "test_pregate_exact_cleanup_preserves_stream_materialization_class",
            "test_launcher_gate_failure_keeps_diagnostics_conservative",
            "test_repeated_eintr_preserves_exact_and_wildcard_wait_deadlines",
            "test_wildcard_reap_preserves_the_first_wait_fault",
            "test_wildcard_reap_continues_after_a_post_zero_callback",
            "test_post_reap_callback_never_restores_numeric_signal_authority",
            "test_fork_callback_after_positive_pid_loses_publication_authority",
            "test_nonzero_or_malformed_monitoring_observation_rejects",
            "test_module_local_monitoring_mask_is_a_pure_rejection",
            "test_module_code_closure_includes_property_accessors",
            "test_lost_pid_cleanup_preserves_the_initiating_fork_error",
            "test_writer_kill_error_does_not_prevent_exact_pid_reap",
            "test_group_signal_error_does_not_prevent_owned_child_reap",
            "test_launcher_spawn_failure_is_not_retried",
            "test_post_fork_profile_errors_reap_unpublished_children",
            "test_launcher_resource_errnos_use_the_resource_exit_class",
            "test_descriptor_exhaustion_uses_the_resource_exit_class",
            "test_launcher_output_oserrors_use_the_resource_exit_class",
            "test_timeout_kills_and_reaps_once_without_retry",
            "test_cancellation_signals_kill_and_reap_once_without_retry",
            "test_cancellation_after_pipe_eof_kills_and_reaps_promptly",
        ),
    ),
    (
        Path("tests/plugins/task_witness_client/test_retained_state.py"),
        "tests.plugins.task_witness_client.test_retained_state",
        "RetainedStateTests",
        (
            "test_first_install_rollback_precondition_is_accepted",
            "test_first_install_rollback_precondition_schema_is_strict",
            "test_first_install_rollback_precondition_binds_pinned_identities",
            "test_first_install_rollback_root_history_is_not_live_state",
            "test_external_provider_and_intrinsic_smoke_reach_the_launcher",
            "test_two_external_providers_never_reach_the_launcher",
            "test_malformed_external_provider_identity_never_reaches_the_launcher",
            "test_external_provider_policy_identity_mismatch_never_reaches_the_launcher",
            "test_external_provider_policy_authority_mismatch_never_reaches_the_launcher",
            "test_unbound_source_with_external_provider_never_reaches_the_launcher",
            "test_bound_source_without_external_provider_never_reaches_the_launcher",
            "test_external_provider_module_cross_binding_never_reaches_the_launcher",
            "test_external_provider_module_ownership_swap_never_reaches_the_launcher",
            "test_external_provider_policy_cross_binding_never_reaches_the_launcher",
            "test_external_provider_policy_role_swap_never_reaches_the_launcher",
            "test_external_provider_issuer_ownership_swap_never_reaches_the_launcher",
            "test_unowned_external_trust_role_never_reaches_the_launcher",
            "test_external_producer_cannot_cross_provider_authority",
            "test_nonempty_activation_lock_is_rejected_before_launcher",
            "test_mode_0700_activation_lock_is_rejected_before_launcher",
            "test_mode_0400_activation_lock_is_rejected_before_open",
            "test_activation_lock_swap_between_preflight_and_open_is_rejected",
            "test_preexisting_activation_lock_replacement_is_rejected",
            "test_activation_lock_replacement_while_launcher_runs_is_rejected",
            "test_activation_lock_content_change_while_launcher_runs_is_rejected",
            "test_activation_lock_empty_aba_while_launcher_runs_is_rejected",
            "test_visible_activation_lock_full_identity_drift_is_rejected_after_launcher",
            "test_retained_input_replacement_while_launcher_runs_is_rejected",
            "test_transient_launcher_aba_is_rejected",
            "test_unrelated_ancestor_churn_does_not_invalidate_validation",
            "test_unrelated_interpreter_sibling_churn_does_not_invalidate_validation",
            "test_interpreter_sibling_churn_during_descriptor_open_is_accepted",
            "test_selected_interpreter_changes_during_validation_are_rejected",
            "test_selected_interpreter_parent_mapping_change_is_rejected",
            "test_bundle_child_swap_between_preflight_and_open_is_rejected",
            "test_retained_symlink_swap_before_open_is_rejected",
            "test_deployment_receipt_fifo_swap_between_preflight_and_open_is_rejected",
            "test_duplicate_bundle_option_rejects_before_installation_access",
            "test_valid_historical_form_reaches_installation",
            "test_unapproved_historical_context_never_reaches_the_launcher",
            "test_revoked_historical_context_never_reaches_the_launcher",
            "test_dormant_historical_registry_paths_require_canonical_text",
            "test_empty_historical_trust_context_never_reaches_the_launcher",
            "test_retained_trust_context_closure_is_strict",
            "test_intrinsic_smoke_identity_is_controller_derived",
            "test_retained_trust_role_authority_and_order_are_strict",
            "test_invalid_historical_forms_reject_before_installation_access",
        ),
    ),
    (
        Path("tests/plugins/task_witness_client/test_runtime.py"),
        "tests.plugins.task_witness_client.test_runtime",
        "TaskWitnessRuntimeTests",
        ("test_each_runtime_payload_rejects_direct_execution",),
    ),
    (
        Path("tests/plugins/task_witness_client/test_runtime_acceptance.py"),
        "tests.plugins.task_witness_client.test_runtime_acceptance",
        "RuntimeAcceptanceTests",
        (
            "test_bundle_file_limit_accepts_exact_limit",
            "test_bundle_file_limit_rejects_before_launcher",
            "test_bundle_growth_past_file_limit_while_launcher_runs_is_rejected",
            "test_launcher_configuration_remains_data_only",
            "test_runtime_generation_inventory_growth_while_launcher_runs_is_rejected",
            "test_historical_invocation_selects_the_receipt_authorized_context",
            "test_active_context_can_validate_its_own_historical_evidence",
            "test_launcher_witness_contract_drift_is_rejected",
            "test_launcher_witness_extra_field_is_rejected",
            "test_launcher_producer_extra_field_is_rejected",
            "test_launcher_missing_producer_validator_binding_is_rejected",
            "test_launcher_validator_extra_field_is_rejected",
            "test_launcher_producer_and_validator_disagreement_is_rejected",
            "test_launcher_witness_must_be_authorized_by_the_selected_context",
            "test_every_complete_anchor_field_is_bound",
            "test_launcher_nonobject_projection_is_rejected",
            "test_owner_defined_projection_remains_opaque",
            "test_launcher_fixed_identity_fields_are_strict",
            "test_launcher_envelope_excessive_nesting_is_rejected",
            "test_launcher_output_framing_is_strict",
            "test_launcher_lone_surrogate_uses_the_canonical_document_error",
            "test_malformed_launcher_output_is_not_retried",
            "test_excessive_launcher_numeric_token_is_rejected",
        ),
    ),
    (
        Path("tests/plugins/task_witness_client/test_terminal_output.py"),
        "tests.plugins.task_witness_client.test_terminal_output",
        "TerminalOutputTests",
        (
            "test_closed_stdout_returns_a_structured_resource_error",
            "test_file_size_limit_returns_a_structured_resource_error",
            "test_output_writer_fork_failure_is_a_resource_failure",
            "test_post_writer_result_fault_preserves_accepted_bytes_as_resource_failure",
            "test_unknown_writer_pid_after_real_fork_is_wildcard_reaped",
            "test_persistent_writer_recovery_deadline_failure_prevents_fork",
            "test_writer_context_preparation_failure_prevents_fork",
            "test_writer_publishes_pid_before_the_first_parent_fallible_step",
            "test_writer_pid_publication_failure_wildcard_reaps_without_output",
            "test_nonzero_writer_after_complete_write_never_proves_success",
            "test_reaped_writer_retains_the_first_context_fault_as_transport_cause",
            "test_full_open_stderr_cannot_delay_rejection_exit",
            "test_incomplete_large_envelope_write_exposes_no_accepted_proof",
            "test_sigterm_interrupts_accepted_output_to_a_full_open_pipe",
            "test_pre_gate_failure_does_not_claim_output_may_be_visible",
            "test_output_deadline_kills_and_reaps_a_blocked_writer",
            "test_consume_retries_one_deadline_fault_but_bounds_persistent_faults",
            "test_writer_wait_memory_error_requires_exact_reobservation_before_signal",
            "test_writer_wait_post_sleep_profile_fault_is_consumed_and_reaped",
            "test_writer_exact_wait_failure_is_retained_after_retry_reaps",
            "test_writer_persistent_signal_failure_is_retried_once_and_bounded",
            "test_accepted_output_budget_excludes_preflight_and_includes_handoff",
            "test_cancellation_after_accepted_output_prevents_exit_zero",
            "test_post_fork_success_handoff_failures_reap_the_writer",
            "test_known_writer_is_cleaned_across_post_fork_allocation_gaps",
            "test_writer_gate_false_releases_no_accepted_output_or_visibility_claim",
            "test_wait_failure_after_writer_reap_does_not_kill_its_pid",
            "test_exact_wait_echild_prevents_later_signal_to_writer_pid",
            "test_late_profile_drift_and_allocation_failures_are_classified",
            "test_success_restores_the_original_cancellation_disposition",
        ),
    ),
)
CLIENT_COMMON_SELECTORS = tuple(
    f"{module_name}.{case_name}.{method_name}"
    for _relative, module_name, case_name, method_names in CLIENT_COMMON_TESTS
    for method_name in method_names
)
DEPLOYMENT_COMMON_TESTS = (
    (
        Path("tests/plugins/task_witness_deployment/test_activation_recovery.py"),
        "tests.plugins.task_witness_deployment.test_activation_recovery",
        "ActivationRecoveryTests",
        (
            "test_public_activation_normalizes_new_directories_under_restrictive_umask",
            "test_public_recovery_converges_after_directory_process_loss",
            "test_recovery_converges_after_install_process_loss",
            "test_rollback_receipt_records_the_original_precondition",
            "test_recovery_continues_after_first_installed_artifact",
            "test_recovery_rejects_stale_expected_journal_before_mutation",
            "test_recovery_rejects_self_rebound_journal_authority",
            "test_recovery_returns_terminal_result_without_rerunning_smoke",
        ),
    ),
    (
        Path(
            "tests/plugins/task_witness_deployment/test_activation_recovery_validation.py"
        ),
        "tests.plugins.task_witness_deployment.test_activation_recovery_validation",
        "ActivationRecoveryValidationTests",
        (
            "test_recovery_reconciles_only_a_legal_next_journal_temporary",
            "test_recovery_reconciles_a_real_partial_journal_write",
            "test_recovery_preserves_an_invalid_journal_temporary",
            "test_recovery_preserves_misnamed_or_multiple_journal_temporaries",
            "test_recovery_preserves_a_legal_temp_when_live_state_contradicts",
            "test_recovery_rejects_a_sequence_not_derived_from_the_program",
            "test_recovery_rejects_a_rebound_previous_journal_digest",
            "test_recovery_rejects_a_cursor_not_derived_from_the_program",
            "test_public_recovery_requires_every_exact_journal_field",
            "test_public_recovery_normalizes_malformed_journal_types",
            "test_recovery_rejects_unexpected_live_inventory_before_replay",
            "test_recovery_rejects_a_missing_completed_install_prefix",
            "test_recovery_continues_from_each_preinstallation_phase",
            "test_recovery_obeys_durable_smoke_acceptance_and_direction",
            "test_public_recovery_is_crash_total_for_every_absence_step",
            "test_recovery_preserves_an_invalid_pending_install_temporary",
            "test_absence_audit_rejects_each_prefix_contradiction",
            "test_terminal_recovery_rejects_live_outcome_contradiction",
            "test_recovery_rejects_activation_lock_identity_drift",
            "test_recovery_rejects_a_nonempty_activation_lock_before_replay",
            "test_recovery_rejects_read_only_activation_lock_drift_before_replay",
            "test_activation_rejects_a_nonempty_lock_before_its_first_journal",
            "test_recovery_preserves_noncurrent_directory_mode_contradictions",
            "test_recovery_normalizes_only_the_opaque_current_directory_before_audit",
            "test_recovery_rechecks_the_visible_root_immediately_before_smoke",
            "test_activation_rechecks_the_visible_root_before_its_first_journal",
            "test_recorded_recovery_precondition_schema_is_closed",
        ),
    ),
    (
        Path("tests/plugins/task_witness_deployment/test_activation_transactions.py"),
        "tests.plugins.task_witness_deployment.test_activation_transactions",
        "ActivationTransactionTests",
        (
            "test_deployment_receipt_binds_exclusive_lock_deadline",
            "test_exclusive_lock_deadline_closes_descriptors_under_contention",
            "test_activation_lock_deadline_prevents_all_locked_activation_work",
            "test_exclusive_lock_acquisition_at_expiry_fails_closed",
            "test_exclusive_lock_contention_succeeds_when_holder_releases",
            "test_smoke_backup_exhaustion_preserves_benign_caller_fd3",
            "test_smoke_success_restores_present_caller_fd3_exactly",
            "test_smoke_success_restores_absent_caller_fd3_exactly",
            "test_smoke_parent_rejects_stdout_limit_plus_one_and_reaps_child",
            "test_smoke_parent_rejects_stderr_limit_plus_one_and_reaps_child",
            "test_smoke_parent_accepts_each_stream_at_its_exact_limit",
            "test_smoke_parent_preserves_ordinary_real_child_success",
            "test_smoke_parent_timeout_reaps_the_exact_process_group",
            "test_first_install_activation_is_journaled_and_installs_the_shim_last",
            "test_crash_before_first_artifact_write_retains_exact_pending_journal",
            "test_crash_after_artifact_write_retains_an_idempotent_replay_cursor",
            "test_pending_install_replays_after_every_publish_process_loss_cut",
            "test_pending_install_never_overwrites_an_unrelated_final_entry",
        ),
    ),
    (
        Path(
            "tests/plugins/task_witness_deployment/test_agent_plugins_source_receipts.py"
        ),
        "tests.plugins.task_witness_deployment.test_agent_plugins_source_receipts",
        "AgentPluginsSourceReceiptTests",
        (
            "test_public_first_install_accepts_agent_plugins_source_without_codex_projection",
            "test_public_first_install_rejects_claude_projection_drift",
            "test_public_first_install_rejects_legacy_codex_projection",
        ),
    ),
    (
        Path(
            "tests/plugins/task_witness_deployment/test_control_maintenance_activation.py"
        ),
        "tests.plugins.task_witness_deployment.test_control_maintenance_activation",
        "ControlMaintenanceActivationTests",
        (
            "test_public_activation_commits_candidate_complete_control_set",
            "test_public_recovery_replays_each_candidate_control_replacement",
            "test_public_recovery_replays_each_prior_control_replacement",
            "test_public_recovery_reconciles_candidate_control_replacement_persistence",
            "test_public_recovery_reconciles_prior_control_replacement_persistence",
            "test_public_recovery_reconciles_control_additive_persistence",
            "test_public_recovery_finishes_control_rollback_cleanup_persistence",
            "test_public_recovery_reconciles_control_journal_persistence",
            "test_public_recovery_respects_control_smoke_durability",
            "test_public_recovery_replays_restored_prior_terminal_before_unlink",
            "test_public_recovery_replays_control_maintenance_fail_stop_terminal",
        ),
    ),
    (
        Path(
            "tests/plugins/task_witness_deployment/test_control_maintenance_staged_client_integration.py"
        ),
        "tests.plugins.task_witness_deployment.test_control_maintenance_staged_client_integration",
        "ControlMaintenanceStagedClientIntegrationTests",
        (
            "test_public_control_maintenance_activation_accepts_b_through_installed_client_and_launcher",
        ),
    ),
    (
        Path(
            "tests/plugins/task_witness_deployment/test_control_maintenance_staging.py"
        ),
        "tests.plugins.task_witness_deployment.test_control_maintenance_staging",
        "ControlMaintenanceStagingTests",
        (
            "test_public_prepare_and_stage_derive_complete_control_maintenance",
            "test_public_prepare_rejects_legacy_v1_policy_without_writes",
            "test_public_prepare_rejects_missing_extra_or_malformed_control_surface",
            "test_public_prepare_rejects_unsupported_declared_control_surface",
            "test_public_prepare_rejects_no_op_source_with_maintenance_difference",
            "test_policy_change_is_authorized_maintenance_and_policy_is_rebound",
            "test_runtime_qualification_change_requires_real_maintenance_difference",
        ),
    ),
    (
        Path("tests/plugins/task_witness_deployment/test_provider_import.py"),
        "tests.plugins.task_witness_deployment.test_provider_import",
        "ProviderImportTests",
        (
            "test_materializes_one_multimodule_provider",
            "test_absent_provider_declaration_registers_nothing",
            "test_rejects_noncanonical_and_duplicate_key_json",
            "test_rejects_missing_extra_and_nondigest_top_level_schema",
            "test_rejects_invalid_tokens_capabilities_and_lifecycle",
            "test_rejects_entrypoint_that_is_not_the_first_module",
            "test_validator_aggregate_budget_accepts_exact_and_rejects_one_over",
            "test_controller_budgets_match_the_existing_consumer",
            "test_rejects_duplicate_module_names_and_paths",
            "test_rejects_unsorted_capabilities_and_role_inventories",
            "test_rejects_validator_implementation_identity_disagreement",
            "test_rejects_unsafe_module_paths",
            "test_rejects_module_length_disagreement",
            "test_rejects_module_content_digest_disagreement",
            "test_rejects_intermediate_symlink_in_module_path",
            "test_rejects_final_symlink_module",
            "test_rejects_special_file_module",
            "test_ignores_undeclared_sibling_files",
            "test_rewrites_retained_paths_and_returns_exact_immutable_inventory",
            "test_rechecks_source_path_mapping_after_retention",
            "test_accepts_a_hardlinked_manager_owned_source_file",
            "test_source_root_does_not_require_effective_user_ownership",
            "test_distinct_validators_may_share_one_retained_implementation",
            "test_accepts_an_identical_existing_validator_generation",
            "test_rejects_changed_bytes_under_an_existing_validator_identity",
            "test_rejects_extra_inventory_under_an_existing_validator_identity",
            "test_composition_is_deterministic_across_provider_input_order",
            "test_composition_uses_only_retained_bytes_after_import",
            "test_retained_paths_resolve_a_symlinked_trust_root_ancestor",
            "test_staged_trust_projects_installed_paths_without_rewriting_bytes",
            "test_rejects_retained_root_canonical_mapping_disagreement",
            "test_composition_includes_the_intrinsic_smoke_provider",
            "test_composition_rejects_an_external_collision_with_smoke",
            "test_composition_rejects_duplicate_external_identities",
            "test_composition_rejects_an_unregistered_producer_validator",
            "test_composition_does_not_recreate_a_missing_retained_generation",
            "test_composition_does_not_recreate_a_missing_smoke_generation",
            "test_context_is_compatible_with_task_witness_trust_context_v2",
            "test_trust_context_budget_accepts_exact_and_rejects_one_over",
            "test_oversized_composed_context_is_not_published",
            "test_accepts_an_identical_existing_context",
            "test_composition_does_not_recreate_a_missing_context",
            "test_composition_does_not_create_a_missing_context_store",
            "test_rejects_changed_bytes_under_an_existing_context_identity",
            "test_validator_identity_fixture_uses_the_existing_path_independent_frame",
        ),
    ),
    (
        Path("tests/plugins/task_witness_deployment/test_receipt_staging.py"),
        "tests.plugins.task_witness_deployment.test_receipt_staging",
        "ReceiptStagingTests",
        (
            "test_renderer_emits_exact_pinned_shim_bytes",
            "test_renderer_rejects_ambiguous_or_uninstalled_inputs",
            "test_source_selection_accepts_only_one_closed_mode_shape",
            "test_source_selection_rejects_cross_mode_or_noncanonical_fields",
            "test_candidate_tree_digest_binds_sorted_inventory_and_exact_bytes",
            "test_candidate_tree_rejects_symlink_or_special_inventory",
            "test_agent_plugin_manifest_and_claude_projection_are_strict_json",
            "test_manager_binding_binds_the_untouched_receipt_bytes",
            "test_candidate_source_cross_binds_every_shared_authority_field",
            "test_candidate_source_without_declaration_registers_no_provider",
            "test_provider_receipt_projection_binds_exact_role_ownership",
            "test_public_policy_is_canonical_and_covers_task_witness_source",
            "test_candidate_policy_cannot_self_authorize_first_install",
            "test_active_policy_classification_has_closed_precedence",
            "test_first_install_precondition_is_read_only_and_exact",
            "test_first_install_authorization_binds_every_external_fact",
            "test_runtime_qualification_and_active_record_bind_exact_payloads",
            "test_absolute_capture_ignores_only_shared_ancestor_change_tokens",
            "test_read_only_trust_plan_matches_two_root_materialization",
            "test_first_install_plan_is_read_only_and_binds_all_nonreceipt_bytes",
            "test_first_install_rejects_a_nonempty_activation_lock_without_mutation",
            "test_first_install_rejects_an_executable_activation_lock_without_mutation",
            "test_first_install_rejects_a_read_only_activation_lock_without_mutation",
            "test_first_install_rejects_when_root_acl_cannot_be_verified",
            "test_first_install_receipt_binds_controller_owned_smoke_bundle",
            "test_first_install_stage_rejects_receipt_process_profile_policy_disagreement",
            "test_first_install_stage_rejects_receipt_source_authority_outside_policy",
            "test_first_install_stage_rejects_receipt_contract_policy_disagreement",
            "test_stage_rejects_coherently_readdressed_smoke_context_tampering",
            "test_stage_rejects_coherently_readdressed_smoke_manifest_tampering",
            "test_stage_rejects_coherently_readdressed_smoke_identity_tampering",
            "test_stage_rejects_coherently_readdressed_smoke_anchor_tampering",
            "test_materialized_first_install_stage_is_complete_inert_and_repeatable",
            "test_materialization_rejects_a_stage_inside_the_candidate_source",
            "test_stage_rejects_canonical_ancestor_alias_before_writes",
            "test_stage_rejects_candidate_ancestor_alias_before_writes",
            "test_stage_rejects_external_move_into_canonical_root_before_writes",
            "test_stage_reports_possible_residue_when_same_euid_move_follows_precheck",
            "test_final_stage_receipt_move_leaves_only_unverifiable_residue",
            "test_prepare_records_physical_ancestor_aliased_candidate_root",
            "test_first_install_plan_rejects_installation_inside_candidate_source",
            "test_stage_recomputes_inputs_and_rejects_stale_authorization_before_writes",
        ),
    ),
    (
        Path(
            "tests/plugins/task_witness_deployment/test_source_evidence_first_install.py"
        ),
        "tests.plugins.task_witness_deployment.test_source_evidence_first_install",
        "FirstInstallSourceEvidenceTests",
        (
            "test_publisher_channel_prepare_accepts_exact_evidence_without_writes",
            "test_exact_release_prepare_accepts_explicit_empty_evidence_without_writes",
            "test_publisher_and_exact_stages_are_parseable_by_the_staged_client",
            "test_new_mode_receipt_source_rejects_cross_mode_missing_or_extra_fields",
            "test_retained_source_parsers_reject_empty_byte_identities_for_all_modes",
            "test_publisher_claims_cross_bind_before_candidate_inspection",
            "test_new_mode_authorization_binds_the_aggregate_source_evidence",
            "test_new_modes_activate_and_capture_their_retained_source",
            "test_request_surface_has_one_discriminated_source_evidence_slot",
            "test_source_evidence_variant_must_match_mode_before_candidate_inspection",
            "test_bound_source_evidence_requires_exact_bytes_before_candidate_inspection",
            "test_harness_evidence_rejects_empty_receipt_before_candidate_inspection",
            "test_source_evidence_constructors_reject_missing_extra_or_mixed_fields",
        ),
    ),
    (
        Path("tests/plugins/task_witness_deployment/test_source_evidence_recovery.py"),
        "tests.plugins.task_witness_deployment.test_source_evidence_recovery",
        "SourceEvidenceRecoveryTests",
        (
            "test_cross_mode_control_recovery_uses_preimage_engine_rehydrates_evidence_and_client_accepts",
        ),
    ),
    (
        Path(
            "tests/plugins/task_witness_deployment/test_source_evidence_transitions.py"
        ),
        "tests.plugins.task_witness_deployment.test_source_evidence_transitions",
        "SourceEvidenceTransitionTests",
        (
            "test_deployment_request_has_one_discriminated_source_evidence_slot",
            "test_exact_release_changed_pin_requires_source_boundary_approval",
            "test_exact_release_same_pin_is_rejected_as_a_no_op",
            "test_cross_mode_change_requires_source_authority_approval",
            "test_same_mode_source_authority_precedes_candidate_policy_change",
            "test_integrity_rejection_precedes_cross_mode_source_approval",
        ),
    ),
    (
        Path("tests/plugins/task_witness_deployment/test_staged_client_integration.py"),
        "tests.plugins.task_witness_deployment.test_staged_client_integration",
        "StagedClientIntegrationTests",
        ("test_authorized_stage_runs_unchanged_after_byte_exact_installation",),
    ),
    (
        Path(
            "tests/plugins/task_witness_deployment/test_transaction_result_reconciliation.py"
        ),
        "tests.plugins.task_witness_deployment.test_transaction_result_reconciliation",
        "TransactionResultReconciliationTests",
        (
            "test_routine_reconciliation_rejects_post_stage_retained_result_addition",
            "test_routine_recovery_rejects_post_stage_retained_result_addition",
            "test_recovery_rejects_stable_history_before_result_temp_normalization",
            "test_recovery_rejects_live_tree_drift_before_result_temp_normalization",
            "test_control_reconciliation_rejects_post_stage_retained_result_addition",
            "test_control_recovery_rejects_post_stage_retained_result_addition",
            "test_routine_reconciliation_rejects_missing_or_substituted_history",
            "test_restored_prior_next_transaction_invalidates_prior_reconciliation",
            "test_recovery_required_remains_live_journal_retained",
            "test_result_retention_normalizes_a_new_directory_under_restrictive_umask",
            "test_next_transaction_preserves_prior_result_and_closes_its_baseline",
            "test_first_install_after_restored_absent_preserves_prior_result",
            "test_completed_first_install_transaction_identity_is_single_use",
            "test_completed_routine_transaction_identity_is_single_use",
            "test_completed_control_transaction_identity_is_single_use",
            "test_recovery_converges_across_result_retention_process_loss",
            "test_recovery_rejects_mode_drift_on_preexisting_result_directory",
            "test_public_reconciliation_rejects_result_inventory_contradictions",
            "test_public_reconciliation_rejects_contradictory_live_selectors_without_mutation",
            "test_public_reconciliation_covers_control_maintenance_terminal_outcomes",
        ),
    ),
)
DEPLOYMENT_COMMON_SELECTORS = tuple(
    f"{module_name}.{case_name}.{method_name}"
    for _relative, module_name, case_name, method_names in DEPLOYMENT_COMMON_TESTS
    for method_name in method_names
)


def _deployment_vertical_group(
    module_leaf: str,
    case_name: str,
    method_names: tuple[str, ...],
) -> tuple[Path, str, str, tuple[str, ...]]:
    module_name = f"tests.plugins.task_witness_deployment.{module_leaf}"
    return (
        Path(*module_name.split(".")).with_suffix(".py"),
        module_name,
        case_name,
        method_names,
    )


def _deployment_vertical_selectors(
    groups: tuple[tuple[Path, str, str, tuple[str, ...]], ...],
) -> tuple[str, ...]:
    return tuple(
        f"{module_name}.{case_name}.{method_name}"
        for _relative, module_name, case_name, method_names in groups
        for method_name in method_names
    )


FORWARD_UPDATE_TESTS = (
    _deployment_vertical_group(
        "test_control_maintenance_followup",
        "ControlMaintenanceFollowupTests",
        (
            "test_installed_b_prepares_and_stages_followup_routine",
            "test_installed_b_prepares_and_stages_followup_control_maintenance",
        ),
    ),
    _deployment_vertical_group(
        "test_routine_activation_recovery",
        "RoutineActivationRecoveryTests",
        (
            "test_smoke_observer_rejects_rebound_b_transaction_authority",
            "test_public_routine_activation_commits_b_as_one_additive_unit",
            "test_public_recovery_converges_across_selector_persistence_cuts",
            "test_public_recovery_rejects_selector_temporary_contradictions",
            "test_public_recovery_rejects_stale_or_reversed_live_selectors",
            "test_public_recovery_converges_across_additive_persistence_cuts",
            "test_public_recovery_converges_across_journal_generation_cuts",
            "test_public_recovery_replays_terminal_before_unlink",
            "test_public_recovery_finishes_r_first_b_last_cleanup",
            "test_public_recovery_respects_durable_smoke_acceptance",
            "test_public_recovery_rejects_journal_generation_contradictions",
            "test_public_recovery_rejects_resealed_routine_journal_program_drift",
            "test_public_recovery_rejects_rollback_authority_or_handoff_swaps",
            "test_public_recovery_rejects_additive_temporary_contradictions",
            "test_public_recovery_rejects_unexpected_routine_artifact_residue",
            "test_public_recovery_rejects_receipt_authority_contradictions",
            "test_public_recovery_rejects_wrong_cleanup_receipt_order",
            "test_public_recovery_rejects_terminal_live_selector_mismatch",
            "test_public_recovery_rejects_stage_or_occ_authority_drift",
        ),
    ),
    _deployment_vertical_group(
        "test_routine_staged_client_integration",
        "RoutineStagedClientIntegrationTests",
        (
            "test_public_routine_activation_accepts_b_through_installed_client_and_launcher",
        ),
    ),
    _deployment_vertical_group(
        "test_routine_transactions",
        "RoutineTransactionTests",
        (
            "test_prepare_captures_the_exact_active_receipt_and_classifies_forward",
            "test_stage_binds_b_r_and_disjoint_prior_selector_preimages",
            "test_prepare_requires_a_bounded_shared_lock_before_capture",
            "test_prepare_rejects_transaction_or_selector_drift_during_capture",
            "test_prepare_rejects_a_damaged_active_runtime_payload",
            "test_prepare_rejects_a_missing_retained_rollback_receipt",
            "test_prepare_allows_compatible_forward_trust_bytes_to_change",
            "test_prepare_validates_the_exact_recursive_active_receipt_chain",
            "test_verify_routine_stage_rejects_rehashed_semantic_tampering_without_writes",
            "test_verify_stage_rejects_nonprivate_dispositions_without_writes",
            "test_prepare_pins_the_private_recursive_receipt_inventory",
            "test_creation_disabled_capture_closes_descriptors_on_inspection_failure",
            "test_verify_stage_cross_binds_external_and_intrinsic_provider_declarations",
            "test_prepare_cross_binds_retained_provider_declarations",
            "test_verify_routine_stage_does_not_resolve_installed_prior_authority",
            "test_verify_routine_stage_rejects_staged_prior_semantic_drift",
            "test_verify_routine_stage_requires_canonical_prior_role_inventories",
            "test_verify_routine_stage_requires_canonical_prior_issuer_capabilities",
            "test_verify_routine_stage_requires_closed_prior_provider_tokens_and_source",
            "test_verify_routine_stage_requires_candidate_receipt_source_mode",
            "test_verify_routine_stage_requires_candidate_compatible_forward_source_transition",
            "test_verify_routine_stage_accepts_candidate_compatible_forward_variants",
            "test_planner_classifies_source_authority_before_policy_change",
            "test_verify_routine_stage_requires_intrinsic_only_source_identity",
            "test_verify_routine_stage_enforces_provider_and_module_bounds",
            "test_verify_routine_stage_budgets_modules_per_validator",
            "test_prepare_validates_prior_source_projection_and_policy_coverage",
        ),
    ),
    _deployment_vertical_group(
        "test_source_evidence_transitions",
        "SourceEvidenceTransitionTests",
        (
            "test_publisher_forward_is_routine_compatible",
            "test_publisher_forward_activates_and_captures_retained_evidence",
            "test_routine_source_boundary_activates_with_its_exact_purpose",
        ),
    ),
    _deployment_vertical_group(
        "test_transaction_result_reconciliation",
        "TransactionResultReconciliationTests",
        ("test_public_reconciliation_returns_candidate_result_after_post_unlink_loss",),
    ),
)
FORWARD_UPDATE_SELECTORS = _deployment_vertical_selectors(FORWARD_UPDATE_TESTS)

AUTHORIZED_DOWNGRADE_AND_MANUAL_ROLLBACK_TESTS = (
    _deployment_vertical_group(
        "test_manual_rollback_activation",
        "ManualRollbackActivationTests",
        (
            "test_public_d_to_same_a_endpoint_mints_a_new_receipt",
            "test_manual_stage_rejects_reused_immutable_release_identity",
            "test_public_c_to_a_rollback_mints_d_and_preserves_full_lineage",
            "test_candidate_rejection_restores_only_c_and_removes_d_authority",
        ),
    ),
    _deployment_vertical_group(
        "test_manual_rollback_preparation",
        "ManualRollbackPreparationTests",
        (
            "test_prepare_c_to_a_displays_exact_target_and_writes_nothing",
            "test_prepare_rejects_missing_target_selector_authority_without_writes",
        ),
    ),
    _deployment_vertical_group(
        "test_manual_rollback_recovery",
        "ManualRollbackRecoveryTests",
        (
            "test_early_phase_recovery_runs_through_staged_c",
            "test_terminal_recovery_is_smoke_free_and_k1_idempotent",
            "test_recovery_replays_candidate_controller_replacement_through_c",
            "test_recovery_replays_prior_controller_replacement_through_c",
            "test_candidate_smoke_recovery_respects_durable_acceptance",
            "test_rollback_smoke_recovery_respects_durable_acceptance",
            "test_recovery_resumes_after_rollback_receipt_cleanup",
            "test_rollback_smoke_rejection_remains_recovery_required",
            "test_manual_journal_persistence_reconciles_through_staged_c",
        ),
    ),
    _deployment_vertical_group(
        "test_routine_transactions",
        "RoutineTransactionTests",
        ("test_stage_reactivates_exact_historical_trust_without_duplication",),
    ),
    _deployment_vertical_group(
        "test_source_evidence_recovery",
        "SourceEvidenceRecoveryTests",
        (
            "test_publisher_downgrade_recovers_from_exact_stage_without_candidate_reread",
        ),
    ),
    _deployment_vertical_group(
        "test_source_evidence_transitions",
        "SourceEvidenceTransitionTests",
        ("test_publisher_downgrade_requires_exact_source_boundary_approval",),
    ),
)
AUTHORIZED_DOWNGRADE_AND_MANUAL_ROLLBACK_SELECTORS = _deployment_vertical_selectors(
    AUTHORIZED_DOWNGRADE_AND_MANUAL_ROLLBACK_TESTS
)

CANDIDATE_REJECTION_ROLLBACK_TESTS = (
    _deployment_vertical_group(
        "test_activation_transactions",
        "ActivationTransactionTests",
        ("test_candidate_failure_restores_exact_absence_without_rollback_smoke",),
    ),
    _deployment_vertical_group(
        "test_control_maintenance_activation",
        "ControlMaintenanceActivationTests",
        ("test_public_candidate_rejection_restores_prior_complete_control_set",),
    ),
    _deployment_vertical_group(
        "test_control_maintenance_staged_client_integration",
        "ControlMaintenanceStagedClientIntegrationTests",
        (
            "test_public_control_maintenance_activation_rejects_b_and_accepts_restored_a_through_installed_client_and_launcher",
        ),
    ),
    _deployment_vertical_group(
        "test_routine_activation_recovery",
        "RoutineActivationRecoveryTests",
        (
            "test_candidate_rejection_restores_a_and_cleans_only_b_authority",
            "test_restored_a_can_activate_c_without_b_transaction_residue",
            "test_rollback_rejection_fail_stops_and_recovery_replays_terminal",
            "test_public_c_rejection_restores_only_immediate_prior_b",
            "test_public_c_rollback_rejection_never_cascades_to_a",
        ),
    ),
    _deployment_vertical_group(
        "test_routine_staged_client_integration",
        "RoutineStagedClientIntegrationTests",
        (
            "test_public_routine_activation_rejects_b_and_accepts_restored_a_through_installed_client_and_launcher",
        ),
    ),
    _deployment_vertical_group(
        "test_transaction_result_reconciliation",
        "TransactionResultReconciliationTests",
        (
            "test_public_reconciliation_returns_restored_prior_after_post_unlink_loss",
            "test_public_reconciliation_returns_restored_absent_after_post_unlink_loss",
        ),
    ),
)
CANDIDATE_REJECTION_ROLLBACK_SELECTORS = _deployment_vertical_selectors(
    CANDIDATE_REJECTION_ROLLBACK_TESTS
)

CANDIDATE_SOURCE_DISAPPEARANCE_TESTS = (
    _deployment_vertical_group(
        "test_routine_activation_recovery",
        "RoutineActivationRecoveryTests",
        ("test_public_recovery_uses_stage_after_candidate_source_disappears",),
    ),
)
CANDIDATE_SOURCE_DISAPPEARANCE_SELECTORS = _deployment_vertical_selectors(
    CANDIDATE_SOURCE_DISAPPEARANCE_TESTS
)

PROVIDER_CACHE_DELETION_AND_MOVEMENT_TESTS = (
    _deployment_vertical_group(
        "test_provider_cache_deletion_and_movement",
        "ProviderCacheDeletionAndMovementTests",
        (
            "test_installed_client_validates_active_and_historical_external_provider_after_cache_deletion_and_checkout_movement",
        ),
    ),
)
PROVIDER_CACHE_DELETION_AND_MOVEMENT_SELECTORS = _deployment_vertical_selectors(
    PROVIDER_CACHE_DELETION_AND_MOVEMENT_TESTS
)

MIGRATION_FREEZE5_TO_BRIDGE_TESTS = (
    _deployment_vertical_group(
        "test_freeze5_upgrade_recovery",
        "Freeze5UpgradeRecoveryTests",
        (
            "test_direct_freeze5_to_tw4_rejects_before_stage_or_live_mutation",
            "test_public_bridge_transition_request_has_exact_closed_surface",
            "test_bridge_preparation_rejects_non_bridge_predecessor_without_writes",
            "test_exact_freeze5_installs_bridge_shape_through_existing_program",
            "test_exact_freeze5_rejects_modified_bridge_authorization_before_stage",
            "test_first_hop_recovery_executes_exact_staged_freeze5_without_candidate",
            "test_installed_bridge_reconciles_exact_freeze5_candidate_result_after_post_unlink_loss",
            "test_first_hop_recovery_rejects_changed_prior_receipt_before_module_load",
            "test_first_hop_recovery_rejects_resealed_non_b1_client_before_module_load",
            "test_first_hop_recovery_rejects_stale_journal_before_module_load",
            "test_exact_freeze5_rejection_restores_only_freeze5",
        ),
    ),
)
MIGRATION_FREEZE5_TO_BRIDGE_SELECTORS = _deployment_vertical_selectors(
    MIGRATION_FREEZE5_TO_BRIDGE_TESTS
)

MIGRATION_BRIDGE_TO_TW4_TESTS = (
    _deployment_vertical_group(
        "test_bridge_transition_activation",
        "BridgeTransitionActivationTests",
        (
            "test_public_bridge_activation_commits_exact_tw4_transition",
            "test_public_bridge_candidate_runs_installed_client_over_mixed_receipts",
            "test_public_bridge_process_loss_recovers_through_exact_staged_b1",
            "test_public_bridge_reconciles_retained_result_through_exact_staged_b1",
            "test_public_bridge_rejection_restores_through_installed_clients",
            "test_public_bridge_success_supports_next_ordinary_precondition_capture",
            "test_public_bridge_rejects_cross_contract_manual_rollback_before_writes",
            "test_public_bridge_target_rejection_restores_exact_current_b1",
        ),
    ),
    _deployment_vertical_group(
        "test_bridge_transition_preparation",
        "BridgeTransitionPreparationTests",
        (
            "test_installed_bridge_prepares_current_candidate_without_writes",
            "test_bridge_preparation_rejects_resealed_non_b1_client_profile_first",
            "test_bridge_history_must_reproduce_exact_identity_record_before_candidate",
        ),
    ),
    _deployment_vertical_group(
        "test_bridge_transition_staging",
        "BridgeTransitionStagingTests",
        (
            "test_public_bridge_stage_binds_external_evidence_without_installing_it",
            "test_invalid_external_authority_rejects_before_stage_creation",
            "test_live_bridge_stage_retains_exact_completed_rehearsal",
            "test_ordinary_stage_values_expose_empty_transition_evidence",
        ),
    ),
)
MIGRATION_BRIDGE_TO_TW4_SELECTORS = _deployment_vertical_selectors(
    MIGRATION_BRIDGE_TO_TW4_TESTS
)

MACOS_ACL_TESTS = (
    (
        Path("tests/plugins/task_witness_client/test_invocation_profile.py"),
        "tests.plugins.task_witness_client.test_invocation_profile",
        "InvocationProfileTests",
        (
            "test_bundle_rejects_macos_allow_acl_before_launcher",
            "test_bundle_accepts_macos_deny_only_acl",
            "test_bundle_rejects_inherited_macos_allow_acl_before_launcher",
        ),
    ),
    (
        Path("tests/plugins/task_witness_client/test_launcher.py"),
        "tests.plugins.task_witness_client.test_launcher",
        "TaskWitnessLauncherTests",
        (
            "test_protected_nodes_reject_macos_allow_acl",
            "test_protected_nodes_reject_inherited_macos_allow_acl",
            "test_protected_nodes_accept_macos_deny_only_acl",
        ),
    ),
    _deployment_vertical_group(
        "test_activation_recovery_validation",
        "ActivationRecoveryValidationTests",
        (
            "test_recovery_rejects_a_permissive_root_acl_before_replay",
            "test_recovery_rejects_a_permissive_lock_acl_before_replay",
        ),
    ),
    _deployment_vertical_group(
        "test_bridge_transition_preparation",
        "BridgeTransitionPreparationTests",
        ("test_bridge_endpoint_rejects_a_permissive_retained_result_acl",),
    ),
    _deployment_vertical_group(
        "test_receipt_staging",
        "ReceiptStagingTests",
        (
            "test_first_install_rejects_a_permissive_root_acl_without_mutation",
            "test_first_install_rejects_a_permissive_lock_acl_without_mutation",
            "test_first_install_accepts_deny_only_acl",
        ),
    ),
)
MACOS_ACL_SELECTORS = _deployment_vertical_selectors(MACOS_ACL_TESTS)

LINUX_PROCESS_SUPERVISION_TESTS = (
    (
        Path("tests/plugins/task_witness_client/test_process_supervision.py"),
        "tests.plugins.task_witness_client.test_process_supervision",
        "ProcessSupervisionTests",
        (
            "test_non_darwin_deadline_allocation_retry_forces_and_reaps_real_child",
            "test_group_kill_retry_removes_real_same_session_descendants",
            "test_linux_threaded_descendant_quiesces_before_leader_reap",
        ),
    ),
)
LINUX_PROCESS_SUPERVISION_SELECTORS = _deployment_vertical_selectors(
    LINUX_PROCESS_SUPERVISION_TESTS
)

PORTABLE_DEPLOYMENT_VERTICALS = {
    "forward-update": (FORWARD_UPDATE_TESTS, FORWARD_UPDATE_SELECTORS),
    "authorized-downgrade-and-manual-rollback": (
        AUTHORIZED_DOWNGRADE_AND_MANUAL_ROLLBACK_TESTS,
        AUTHORIZED_DOWNGRADE_AND_MANUAL_ROLLBACK_SELECTORS,
    ),
    "candidate-rejection-rollback": (
        CANDIDATE_REJECTION_ROLLBACK_TESTS,
        CANDIDATE_REJECTION_ROLLBACK_SELECTORS,
    ),
    "candidate-source-disappearance": (
        CANDIDATE_SOURCE_DISAPPEARANCE_TESTS,
        CANDIDATE_SOURCE_DISAPPEARANCE_SELECTORS,
    ),
    "provider-cache-deletion-and-movement": (
        PROVIDER_CACHE_DELETION_AND_MOVEMENT_TESTS,
        PROVIDER_CACHE_DELETION_AND_MOVEMENT_SELECTORS,
    ),
    "migration-freeze5-to-bridge": (
        MIGRATION_FREEZE5_TO_BRIDGE_TESTS,
        MIGRATION_FREEZE5_TO_BRIDGE_SELECTORS,
    ),
    "migration-bridge-to-tw4": (
        MIGRATION_BRIDGE_TO_TW4_TESTS,
        MIGRATION_BRIDGE_TO_TW4_SELECTORS,
    ),
}

PLATFORM_VERTICALS = {
    "macos-acl": (MACOS_ACL_TESTS, MACOS_ACL_SELECTORS),
    "linux-process-supervision": (
        LINUX_PROCESS_SUPERVISION_TESTS,
        LINUX_PROCESS_SUPERVISION_SELECTORS,
    ),
}
PLATFORM_VERTICAL_HOSTS = {
    "macos-acl": ("darwin", "arm64"),
    "linux-process-supervision": ("linux", "x86_64"),
}

SUITE_SELECTORS = {
    "client-common": CLIENT_COMMON_SELECTORS,
    "deployment-common": DEPLOYMENT_COMMON_SELECTORS,
    "package-contract": PACKAGE_CONTRACT_SELECTORS,
    "qualification-runner-contract": QUALIFICATION_RUNNER_SELECTORS,
    "task-witness-source-stage": TASK_WITNESS_SOURCE_STAGE_SELECTORS,
    "public-release-source-stage": PUBLIC_RELEASE_SOURCE_STAGE_SELECTORS,
    "literal-rendered-shim": LITERAL_RENDERED_SHIM_SELECTORS,
    **{
        suite_id: selectors
        for suite_id, (_groups, selectors) in PORTABLE_DEPLOYMENT_VERTICALS.items()
    },
    **{
        suite_id: selectors
        for suite_id, (_groups, selectors) in PLATFORM_VERTICALS.items()
    },
}
SUITE_EXPECTED_COUNTS = {
    "client-common": 321,
    "deployment-common": 203,
    "package-contract": 71,
    "qualification-runner-contract": 7,
    "task-witness-source-stage": 1,
    "public-release-source-stage": 1,
    "literal-rendered-shim": 1,
    "forward-update": 53,
    "authorized-downgrade-and-manual-rollback": 18,
    "candidate-rejection-rollback": 11,
    "candidate-source-disappearance": 1,
    "provider-cache-deletion-and-movement": 1,
    "migration-freeze5-to-bridge": 11,
    "migration-bridge-to-tw4": 15,
    "macos-acl": 12,
    "linux-process-supervision": 3,
}


class SuiteError(ValueError):
    """One closed qualification suite could not produce a successful result."""


def normalized_host_platform() -> tuple[str, str]:
    system = host_platform.system().lower()
    machine = host_platform.machine().lower()
    machine = {"aarch64": "arm64", "amd64": "x86_64"}.get(machine, machine)
    return system, machine


def _require_platform_vertical_host(suite_id: str) -> None:
    required = PLATFORM_VERTICAL_HOSTS[suite_id]
    if normalized_host_platform() != required:
        raise SuiteError(f"{suite_id} requires {required[0]}-{required[1]} host")


class _BoundedFDDrain:
    """Drain one redirected process descriptor into bounded memory."""

    def __init__(self, descriptor: int, maximum: int) -> None:
        self._descriptor = descriptor
        self._maximum = maximum
        self._raw = bytearray()
        self._overflow = False
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        if self._thread.ident is None:
            raise RuntimeError("suite output drain did not start")

    def has_started(self) -> bool:
        return self._thread.ident is not None

    def relinquish_unstarted_descriptor(self) -> int:
        if self.has_started():
            return -1
        descriptor = self._descriptor
        self._descriptor = -1
        return descriptor

    def _run(self) -> None:
        try:
            stream = io.FileIO(self._descriptor, "rb", closefd=True)
            self._descriptor = -1
            with stream:
                while chunk := stream.read(64 * 1024):
                    remaining = self._maximum - len(self._raw)
                    self._raw.extend(chunk[: max(remaining, 0)])
                    if len(chunk) > remaining:
                        self._overflow = True
        except BaseException as error:  # noqa: BLE001 - surface worker failures
            self._error = error
        finally:
            if self._descriptor >= 0:
                try:
                    os.close(self._descriptor)
                except BaseException as error:  # noqa: BLE001 - retain cleanup failure
                    if self._error is None:
                        self._error = error
                    else:
                        self._error.add_note(
                            f"drain descriptor cleanup also failed: {error}"
                        )

    def finish(self, label: str) -> bytes:
        self._thread.join()
        if self._error is not None:
            raise SuiteError(f"{label} could not be captured") from self._error
        if self._overflow:
            raise SuiteError(f"{label} exceeded the fixed detail bound")
        return bytes(self._raw)


def _flush_native_stdio() -> None:
    """Flush C stdio while stdout and stderr still name the capture pipes."""

    try:
        libc = ctypes.CDLL(None)
        fflush = libc.fflush
        fflush.argtypes = [ctypes.c_void_p]
        fflush.restype = ctypes.c_int
        status = fflush(None)
    except (AttributeError, OSError, TypeError) as error:
        raise SuiteError("native suite output cannot be flushed") from error
    if status != 0:
        raise SuiteError("native suite output cannot be flushed")


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError) as error:
        raise SuiteError("suite result cannot be canonically encoded") from error


def _task_witness_source_stage_argv(repository: Path) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-B",
        "scripts/validate_task_witness.py",
        str(repository),
        "--source-stage",
    ]


def _public_release_source_stage_argv(repository: Path) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-B",
        "scripts/supervise_prepared_release_validation.py",
        "source-stage",
        str(repository),
    ]


class _DirectChildGroupCensusDeadlineExpired(TimeoutError):
    """The owned process-group census exhausted its absolute deadline."""


def _require_direct_child_group_census_time(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise _DirectChildGroupCensusDeadlineExpired


def _darwin_direct_child_process_selection_bytes(
    selector: int,
    value: int,
    deadline: float,
) -> int:
    _require_direct_child_group_census_time(deadline)
    libc = ctypes.CDLL(None, use_errno=True)
    sysctl = libc.sysctl
    sysctl.argtypes = (
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    )
    sysctl.restype = ctypes.c_int
    mib = (ctypes.c_int * 4)(1, 14, selector, value)
    required = ctypes.c_size_t()
    _require_direct_child_group_census_time(deadline)
    if sysctl(mib, 4, None, ctypes.byref(required), None, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    _require_direct_child_group_census_time(deadline)
    if required.value == 0 or required.value > DIRECT_CHILD_GROUP_OBSERVATION_MAX_BYTES:
        raise SuiteError("direct scenario child process group observation is invalid")
    storage = ctypes.create_string_buffer(required.value)
    actual = ctypes.c_size_t(required.value)
    _require_direct_child_group_census_time(deadline)
    if sysctl(mib, 4, storage, ctypes.byref(actual), None, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    _require_direct_child_group_census_time(deadline)
    return actual.value


def _direct_child_group_has_other_members(
    process_group: int,
    deadline: float,
) -> bool:
    _require_direct_child_group_census_time(deadline)
    if sys.platform == "darwin":
        leader_size = _darwin_direct_child_process_selection_bytes(
            1,
            process_group,
            deadline,
        )
        group_size = _darwin_direct_child_process_selection_bytes(
            2,
            process_group,
            deadline,
        )
        if group_size < leader_size or group_size % leader_size != 0:
            raise SuiteError(
                "direct scenario child process group observation is invalid"
            )
        return group_size > leader_size
    if sys.platform.startswith("linux"):
        _require_direct_child_group_census_time(deadline)
        try:
            entries = Path("/proc").iterdir()
        except OSError as error:
            raise SuiteError(
                "direct scenario child process group cannot be observed"
            ) from error
        member_count = 0
        leader_seen = False
        observed = 0
        try:
            for entry in entries:
                _require_direct_child_group_census_time(deadline)
                if not entry.name.isdecimal():
                    continue
                observed += 1
                if observed > 100_000:
                    raise SuiteError(
                        "direct scenario child process group observation is too large"
                    )
                try:
                    raw = (entry / "stat").read_bytes()
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise SuiteError(
                        "direct scenario child process group cannot be observed"
                    ) from error
                _require_direct_child_group_census_time(deadline)
                closing = raw.rfind(b") ")
                fields = raw[closing + 2 :].split() if closing >= 0 else []
                if len(fields) < 3:
                    raise SuiteError(
                        "direct scenario child process group observation is invalid"
                    )
                try:
                    observed_group = int(fields[2])
                except ValueError as error:
                    raise SuiteError(
                        "direct scenario child process group observation is invalid"
                    ) from error
                if observed_group != process_group:
                    continue
                member_count += 1
                if entry.name == str(process_group):
                    leader_seen = True
        except _DirectChildGroupCensusDeadlineExpired:
            raise
        except OSError as error:
            raise SuiteError(
                "direct scenario child process group cannot be observed"
            ) from error
        if not leader_seen or member_count == 0:
            raise SuiteError(
                "direct scenario child process group observation is invalid"
            )
        _require_direct_child_group_census_time(deadline)
        return member_count > 1
    raise SuiteError("direct scenario child process groups are unsupported")


class _DirectChildOwnershipLost(RuntimeError):
    """The original session leader is no longer an unreaped waitable child."""


def _direct_child_terminal_without_reaping(
    process: subprocess.Popen[bytes],
    deadline: float,
) -> bool:
    if process.returncode is not None:
        raise _DirectChildOwnershipLost(
            "direct scenario child ownership was lost before signaling completed"
        )
    while True:
        try:
            observation = os.waitid(
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except InterruptedError:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(
                    process.args,
                    DIRECT_CHILD_TIMEOUT_SECONDS,
                ) from None
            continue
        except ChildProcessError as error:
            raise _DirectChildOwnershipLost(
                "direct scenario child ownership was lost before signaling completed"
            ) from error
        return observation is not None


def _terminate_direct_child_group(
    process: subprocess.Popen[bytes],
    process_group: int,
    deadline: float,
) -> list[BaseException]:
    cleanup_errors: list[BaseException] = []
    deferred_terminal_signal_errors: list[BaseException] = []

    def observe_terminal() -> tuple[bool, bool]:
        try:
            return _direct_child_terminal_without_reaping(process, deadline), True
        except _DirectChildOwnershipLost as error:
            cleanup_errors.append(error)
        except BaseException as error:  # noqa: BLE001 - ownership is unproved
            cleanup_errors.append(error)
        return False, False

    def signal_group(
        signal_number: int,
        leader_terminal: bool,
        *,
        allow_after_deadline: bool,
    ) -> tuple[bool, bool, bool]:
        terminal_now, owned = observe_terminal()
        if not owned:
            return False, leader_terminal, False
        leader_terminal = leader_terminal or terminal_now
        deadline_expired = time.monotonic() >= deadline
        if deadline_expired and not allow_after_deadline:
            return True, leader_terminal, True
        try:
            os.killpg(process_group, signal_number)
        except (PermissionError, ProcessLookupError) as error:
            if leader_terminal:
                deferred_terminal_signal_errors.append(error)
            else:
                cleanup_errors.append(error)
        except BaseException as error:  # noqa: BLE001 - cleanup must continue
            cleanup_errors.append(error)
        return True, leader_terminal, deadline_expired

    def record_deadline_failure() -> None:
        cleanup_errors.append(
            subprocess.TimeoutExpired(
                process.args,
                DIRECT_CHILD_TIMEOUT_SECONDS,
            )
        )
        cleanup_errors.extend(deferred_terminal_signal_errors)
        deferred_terminal_signal_errors.clear()

    def wait_until(boundary: float) -> tuple[bool, bool]:
        while True:
            remaining = boundary - time.monotonic()
            if remaining <= 0:
                return False, True
            leader_terminal, owned = observe_terminal()
            if not owned or leader_terminal:
                return leader_terminal, owned
            remaining = boundary - time.monotonic()
            if remaining <= 0:
                return False, True
            time.sleep(min(remaining, 0.01))

    if process.returncode is not None:
        cleanup_errors.append(
            _DirectChildOwnershipLost(
                "direct scenario child ownership was lost before signaling completed"
            )
        )
        return cleanup_errors
    leader_terminal, owned = observe_terminal()
    if not owned:
        return cleanup_errors
    owned, leader_terminal, term_deadline_expired = signal_group(
        signal.SIGTERM,
        leader_terminal,
        allow_after_deadline=False,
    )
    if not owned:
        return cleanup_errors
    if term_deadline_expired:
        owned, leader_terminal, _kill_deadline_expired = signal_group(
            signal.SIGKILL,
            leader_terminal,
            allow_after_deadline=True,
        )
        if not owned:
            return cleanup_errors
        record_deadline_failure()
        return cleanup_errors
    cleanup_started = time.monotonic()
    if cleanup_started >= deadline:
        owned, leader_terminal, _kill_deadline_expired = signal_group(
            signal.SIGKILL,
            leader_terminal,
            allow_after_deadline=True,
        )
        if not owned:
            return cleanup_errors
        record_deadline_failure()
        return cleanup_errors
    remaining = max(deadline - cleanup_started, 0.0)
    termination_boundary = min(deadline, cleanup_started + (remaining / 2))
    if not leader_terminal:
        leader_terminal, owned = wait_until(termination_boundary)
        if not owned:
            return cleanup_errors
    owned, leader_terminal, kill_deadline_expired = signal_group(
        signal.SIGKILL,
        leader_terminal,
        allow_after_deadline=True,
    )
    if not owned:
        return cleanup_errors
    if kill_deadline_expired:
        record_deadline_failure()
        return cleanup_errors
    if not leader_terminal:
        leader_terminal, owned = wait_until(deadline)
        if not owned:
            return cleanup_errors
    if not leader_terminal:
        cleanup_errors.append(
            subprocess.TimeoutExpired(
                process.args,
                DIRECT_CHILD_TIMEOUT_SECONDS,
            )
        )
        return cleanup_errors

    group_quiescent = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            cleanup_errors.append(
                SuiteError("direct scenario child process group remained present")
            )
            break
        terminal_now, owned = observe_terminal()
        if not owned:
            break
        leader_terminal = leader_terminal or terminal_now
        if not leader_terminal:
            cleanup_errors.append(
                subprocess.TimeoutExpired(
                    process.args,
                    DIRECT_CHILD_TIMEOUT_SECONDS,
                )
            )
            break
        try:
            has_other_members = _direct_child_group_has_other_members(
                process_group,
                deadline,
            )
        except _DirectChildGroupCensusDeadlineExpired:
            cleanup_errors.append(
                SuiteError(
                    "direct scenario child process group census exceeded its deadline"
                )
            )
            break
        except BaseException as error:  # noqa: BLE001 - ownership stays retained
            cleanup_errors.append(error)
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            cleanup_errors.append(
                SuiteError(
                    "direct scenario child process group census exceeded its deadline"
                )
            )
            break
        if not has_other_members:
            group_quiescent = True
            break
        owned, leader_terminal, repeat_kill_deadline_expired = signal_group(
            signal.SIGKILL,
            leader_terminal,
            allow_after_deadline=False,
        )
        if not owned:
            break
        if repeat_kill_deadline_expired:
            cleanup_errors.append(
                SuiteError("direct scenario child process group remained present")
            )
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            cleanup_errors.append(
                SuiteError("direct scenario child process group remained present")
            )
            break
        time.sleep(min(remaining, 0.01))
    if not group_quiescent:
        cleanup_errors.extend(deferred_terminal_signal_errors)
        return cleanup_errors

    try:
        process.wait(timeout=0)
    except BaseException as error:  # noqa: BLE001 - group ownership is released here
        cleanup_errors.append(error)
    return cleanup_errors


def _retire_direct_child_pipe_handle(
    stream: io.BufferedReader,
    descriptor: int | None,
    expected_identity: tuple[int, ...] | None,
    cleanup_errors: list[BaseException],
) -> None:
    try:
        stream.close()
    except BaseException as error:  # noqa: BLE001 - close outcome is ambiguous
        cleanup_errors.append(error)
        if descriptor is None or expected_identity is None:
            try:
                type(stream).close(stream)
            except BaseException as fallback_error:  # noqa: BLE001
                cleanup_errors.append(fallback_error)
            return
        try:
            current_identity = _capture_descriptor_identity(descriptor)
        except OSError as inspection_error:
            if inspection_error.errno != 9:
                cleanup_errors.append(inspection_error)
            return
        except BaseException as inspection_error:  # noqa: BLE001
            cleanup_errors.append(inspection_error)
            return
        if current_identity != expected_identity:
            cleanup_errors.append(
                SuiteError("direct scenario child pipe descriptor identity changed")
            )
            return
        try:
            type(stream).close(stream)
        except BaseException as fallback_error:  # noqa: BLE001 - retain failure
            cleanup_errors.append(fallback_error)
        try:
            retained_identity = _capture_descriptor_identity(descriptor)
        except OSError as inspection_error:
            if inspection_error.errno != 9:
                cleanup_errors.append(inspection_error)
            return
        except BaseException as inspection_error:  # noqa: BLE001
            cleanup_errors.append(inspection_error)
            return
        if retained_identity != expected_identity:
            cleanup_errors.append(
                SuiteError("direct scenario child pipe descriptor identity changed")
            )
            return
        _close_capture_descriptor(descriptor, cleanup_errors, expected_identity)


def _run_bounded_child(
    argv: list[str],
    repository: Path,
    *,
    environment: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> tuple[int, bytes, bytes]:
    started = time.monotonic()
    deadline = started + DIRECT_CHILD_TIMEOUT_SECONDS
    cleanup_reserve = min(1.0, max(DIRECT_CHILD_TIMEOUT_SECONDS / 4, 0.01))
    execution_deadline = deadline - cleanup_reserve
    try:
        process = subprocess.Popen(
            argv,
            cwd=repository,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=True,
        )
    except OSError as error:
        raise SuiteError("direct scenario child could not be started") from error
    assert process.stdout is not None and process.stderr is not None
    stdout_descriptor = -1
    stderr_descriptor = -1
    stdout_descriptor_identity: tuple[int, ...] | None = None
    stderr_descriptor_identity: tuple[int, ...] | None = None
    original_stdout_descriptor: int | None = None
    original_stderr_descriptor: int | None = None
    original_stdout_identity: tuple[int, ...] | None = None
    original_stderr_identity: tuple[int, ...] | None = None
    try:
        original_stdout_descriptor = process.stdout.fileno()
        original_stderr_descriptor = process.stderr.fileno()
        original_stdout_identity = _capture_descriptor_identity(
            original_stdout_descriptor
        )
        original_stderr_identity = _capture_descriptor_identity(
            original_stderr_descriptor
        )
        stdout_descriptor = fcntl.fcntl(
            process.stdout.fileno(),
            fcntl.F_DUPFD_CLOEXEC,
            64,
        )
        stdout_descriptor_identity = _capture_descriptor_identity(stdout_descriptor)
        stderr_descriptor = fcntl.fcntl(
            process.stderr.fileno(),
            fcntl.F_DUPFD_CLOEXEC,
            64,
        )
        stderr_descriptor_identity = _capture_descriptor_identity(stderr_descriptor)
    except BaseException as primary_error:
        cleanup_errors: list[BaseException] = []
        _retire_direct_child_pipe_handle(
            process.stdout,
            original_stdout_descriptor,
            original_stdout_identity,
            cleanup_errors,
        )
        _retire_direct_child_pipe_handle(
            process.stderr,
            original_stderr_descriptor,
            original_stderr_identity,
            cleanup_errors,
        )
        cleanup_errors.extend(
            _terminate_direct_child_group(
                process,
                process.pid,
                deadline,
            )
        )
        for descriptor, identity in (
            (stdout_descriptor, stdout_descriptor_identity),
            (stderr_descriptor, stderr_descriptor_identity),
        ):
            if descriptor >= 0:
                _close_capture_descriptor(
                    descriptor,
                    cleanup_errors,
                    identity,
                )
        if isinstance(primary_error, OSError):
            capture_error = SuiteError(
                "direct scenario child output cannot be captured"
            )
            for cleanup_error in cleanup_errors:
                capture_error.add_note(
                    f"direct scenario child cleanup also failed: {cleanup_error}"
                )
            raise capture_error from primary_error
        for cleanup_error in cleanup_errors:
            primary_error.add_note(
                f"direct scenario child cleanup also failed: {cleanup_error}"
            )
        raise

    assert original_stdout_identity is not None
    assert original_stderr_identity is not None
    retirement_errors: list[BaseException] = []
    _retire_direct_child_pipe_handle(
        process.stdout,
        original_stdout_descriptor,
        original_stdout_identity,
        retirement_errors,
    )
    _retire_direct_child_pipe_handle(
        process.stderr,
        original_stderr_descriptor,
        original_stderr_identity,
        retirement_errors,
    )
    if retirement_errors:
        cleanup_errors = _terminate_direct_child_group(
            process,
            process.pid,
            deadline,
        )
        for descriptor, identity in (
            (stdout_descriptor, stdout_descriptor_identity),
            (stderr_descriptor, stderr_descriptor_identity),
        ):
            if descriptor >= 0:
                _close_capture_descriptor(
                    descriptor,
                    cleanup_errors,
                    identity,
                )
        capture_error = SuiteError("direct scenario child output cannot be captured")
        for cleanup_error in (*retirement_errors, *cleanup_errors):
            capture_error.add_note(
                f"direct scenario child cleanup also failed: {cleanup_error}"
            )
        first_retirement_error = retirement_errors[0]
        if isinstance(first_retirement_error, Exception):
            raise capture_error from first_retirement_error
        for cleanup_error in (*retirement_errors[1:], *cleanup_errors):
            first_retirement_error.add_note(
                f"direct scenario child cleanup also failed: {cleanup_error}"
            )
        raise first_retirement_error

    assert stdout_descriptor_identity is not None
    assert stderr_descriptor_identity is not None
    descriptors = {
        "stdout": stdout_descriptor,
        "stderr": stderr_descriptor,
    }
    descriptor_identities = {
        "stdout": stdout_descriptor_identity,
        "stderr": stderr_descriptor_identity,
    }
    buffers = {
        "stdout": bytearray(),
        "stderr": bytearray(),
    }
    overflows = dict.fromkeys(descriptors, False)
    capture_errors: dict[str, OSError] = {}
    selector: selectors.BaseSelector | None = None
    terminal_cleanup_errors: list[BaseException] = []
    child_cleanup_started = False

    def cleanup_child_once() -> list[BaseException]:
        nonlocal child_cleanup_started
        if child_cleanup_started:
            return []
        child_cleanup_started = True
        return _terminate_direct_child_group(
            process,
            process.pid,
            deadline,
        )

    returncode: int | None = None
    try:
        selector = selectors.DefaultSelector()
        for name, descriptor in descriptors.items():
            os.set_blocking(descriptor, False)
            selector.register(descriptor, selectors.EVENT_READ, name)
        while True:
            if not child_cleanup_started:
                leader_terminal = _direct_child_terminal_without_reaping(
                    process,
                    deadline,
                )
                if leader_terminal:
                    cleanup_errors = cleanup_child_once()
                    returncode = process.returncode
                    if cleanup_errors:
                        first_cleanup_error = cleanup_errors[0]
                        for cleanup_error in cleanup_errors[1:]:
                            first_cleanup_error.add_note(
                                "direct scenario child cleanup also failed: "
                                f"{cleanup_error}"
                            )
                        if isinstance(first_cleanup_error, Exception):
                            raise SuiteError(
                                "direct scenario child could not be cleaned up"
                            ) from first_cleanup_error
                        raise first_cleanup_error
                    if returncode is None:
                        raise SuiteError("direct scenario child could not be reaped")
            if child_cleanup_started and not selector.get_map():
                break
            active_deadline = deadline if child_cleanup_started else execution_deadline
            remaining = active_deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    argv,
                    DIRECT_CHILD_TIMEOUT_SECONDS,
                )
            if selector.get_map():
                events = selector.select(min(remaining, 0.05))
                for key, _mask in events:
                    name = key.data
                    try:
                        chunk = os.read(key.fd, 64 * 1024)
                    except BlockingIOError:
                        continue
                    except OSError as error:
                        capture_errors[name] = error
                        chunk = b""
                    if chunk:
                        available = DETAIL_MAX_BYTES - len(buffers[name])
                        buffers[name].extend(chunk[: max(available, 0)])
                        if len(chunk) > available:
                            overflows[name] = True
                        continue
                    selector.unregister(key.fd)
                    descriptors[name] = -1
                    retirement_errors = []
                    _close_capture_descriptor(
                        key.fd,
                        retirement_errors,
                        descriptor_identities[name],
                    )
                    if retirement_errors:
                        first_retirement_error = retirement_errors[0]
                        for cleanup_error in retirement_errors[1:]:
                            first_retirement_error.add_note(
                                "direct scenario child descriptor cleanup also "
                                f"failed: {cleanup_error}"
                            )
                        raise first_retirement_error
            else:
                time.sleep(min(remaining, 0.01))
    except subprocess.TimeoutExpired as error:
        deadline_error = SuiteError("direct scenario child exceeded its deadline")
        for cleanup_error in cleanup_child_once():
            deadline_error.add_note(
                f"direct scenario child cleanup also failed: {cleanup_error}"
            )
        raise deadline_error from error
    except OSError as error:
        capture_error = SuiteError("direct scenario child output cannot be captured")
        for cleanup_error in cleanup_child_once():
            capture_error.add_note(
                f"direct scenario child cleanup also failed: {cleanup_error}"
            )
        raise capture_error from error
    except BaseException as primary_error:
        for cleanup_error in cleanup_child_once():
            primary_error.add_note(
                f"direct scenario child cleanup also failed: {cleanup_error}"
            )
        raise
    finally:
        primary_error = sys.exception()
        if selector is not None:
            try:
                selector.close()
            except BaseException as error:  # noqa: BLE001 - preserve primary
                terminal_cleanup_errors.append(error)
        for name, descriptor in tuple(descriptors.items()):
            if descriptor >= 0:
                descriptors[name] = -1
                _close_capture_descriptor(
                    descriptor,
                    terminal_cleanup_errors,
                    descriptor_identities[name],
                )
        if primary_error is not None:
            for cleanup_error in terminal_cleanup_errors:
                primary_error.add_note(
                    "direct scenario child descriptor cleanup also failed: "
                    f"{cleanup_error}"
                )

    if terminal_cleanup_errors:
        first_cleanup_error = terminal_cleanup_errors[0]
        for cleanup_error in terminal_cleanup_errors[1:]:
            first_cleanup_error.add_note(
                f"direct scenario child descriptor cleanup also failed: {cleanup_error}"
            )
        if isinstance(first_cleanup_error, OSError):
            raise SuiteError(
                "direct scenario child output cannot be captured"
            ) from first_cleanup_error
        raise first_cleanup_error

    assert returncode is not None
    for name in ("stdout", "stderr"):
        if name in capture_errors:
            raise SuiteError(
                f"direct scenario child {name} could not be captured"
            ) from (capture_errors[name])
        if overflows[name]:
            raise SuiteError(
                f"direct scenario child {name} exceeded the fixed detail bound"
            )
    return returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"])


def _task_witness_source_stage_suite(repository: Path) -> unittest.TestSuite:
    def run_direct_scenario() -> None:
        returncode, stdout_raw, stderr_raw = _run_bounded_child(
            _task_witness_source_stage_argv(repository),
            repository,
        )
        if (
            returncode != 0
            or stdout_raw != b"Task Witness source-stage validation passed\n"
            or stderr_raw
        ):
            raise SuiteError("task-witness-source-stage child contract mismatch")

    return unittest.TestSuite((unittest.FunctionTestCase(run_direct_scenario),))


def _parse_public_source_stage_stdout(raw: bytes) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    def reject_constant(_value: str) -> object:
        raise ValueError("nonfinite JSON number")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
        canonical = (
            json.dumps(
                value,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, UnicodeDecodeError, ValueError) as error:
        raise SuiteError("public-release-source-stage output is invalid") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "plugins"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or not isinstance(value["plugins"], dict)
        or canonical != raw
    ):
        raise SuiteError("public-release-source-stage output is invalid")
    return value


def _public_release_source_stage_suite(repository: Path) -> unittest.TestSuite:
    def run_direct_scenario() -> None:
        returncode, stdout_raw, stderr_raw = _run_bounded_child(
            _public_release_source_stage_argv(repository),
            repository,
        )
        if returncode != 0 or stderr_raw:
            raise SuiteError("public-release-source-stage child contract mismatch")
        _parse_public_source_stage_stdout(stdout_raw)

    return unittest.TestSuite((unittest.FunctionTestCase(run_direct_scenario),))


def _capture_fixed_file(
    repository: Path,
    relative: Path,
    label: str,
) -> bytes:
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise SuiteError(f"{label} path disagrees")
    current = repository
    for part in relative.parts[:-1]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise SuiteError(f"{label} path is unavailable") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SuiteError(f"{label} path is unsafe")
    path = repository / relative
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > DETAIL_MAX_BYTES
        ):
            raise OSError
        raw = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            raw.extend(chunk)
            if len(raw) > DETAIL_MAX_BYTES:
                raise OSError
        after = os.fstat(descriptor)
        visible = path.stat(follow_symlinks=False)
    except OSError as error:
        raise SuiteError(f"{label} cannot be captured") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    def identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_nlink,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    if (
        identity(before) != identity(after)
        or identity(before) != identity(visible)
        or before.st_size != len(raw)
    ):
        raise SuiteError(f"{label} identity drift")
    return bytes(raw)


def _safe_child_name(name: str, label: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise SuiteError(f"{label} name is invalid")


def _open_private_directory(path: Path, label: str) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        visible = path.lstat()
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise SuiteError(f"{label} is unavailable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(visible.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (visible.st_dev, visible.st_ino)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise SuiteError(f"{label} is unsafe")
    return descriptor


def _open_private_child_directory(
    parent_descriptor: int,
    name: str,
    label: str,
    *,
    create: bool,
) -> int:
    _safe_child_name(name, label)
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        except OSError as error:
            raise SuiteError(f"{label} cannot be created") from error
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        metadata = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise SuiteError(f"{label} is unavailable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(visible.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (visible.st_dev, visible.st_ino)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise SuiteError(f"{label} is unsafe")
    return descriptor


def _write_new_file_at(
    directory_descriptor: int,
    name: str,
    raw: bytes,
    mode: int,
) -> None:
    _safe_child_name(name, "literal-rendered-shim file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            flags,
            mode,
            dir_fd=directory_descriptor,
        )
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError
            remaining = remaining[written:]
        os.fsync(descriptor)
    except OSError as error:
        raise SuiteError("literal-rendered-shim file publication failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        metadata = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise SuiteError("literal-rendered-shim file publication failed") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != mode
        or metadata.st_size != len(raw)
    ):
        raise SuiteError("literal-rendered-shim file publication failed")


def _capture_owned_regular_at(
    directory_descriptor: int,
    name: str,
    label: str,
) -> tuple[bytes, os.stat_result]:
    _safe_child_name(name, label)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or before.st_size > DETAIL_MAX_BYTES
        ):
            raise OSError
        raw = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            raw.extend(chunk)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise SuiteError(f"{label} is unsafe") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    def identity(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_nlink,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    if (
        identity(before) != identity(after)
        or identity(before) != identity(visible)
        or before.st_size != len(raw)
    ):
        raise SuiteError(f"{label} identity drift")
    return bytes(raw), before


def _unlink_owned_regular_at(
    directory_descriptor: int,
    name: str,
    *,
    expected_mode: int | None,
) -> None:
    _safe_child_name(name, "literal-rendered-shim cleanup")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        metadata = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or (
                expected_mode is not None
                and stat.S_IMODE(metadata.st_mode) != expected_mode
            )
            or (metadata.st_dev, metadata.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise OSError
        os.unlink(name, dir_fd=directory_descriptor)
        after = os.fstat(descriptor)
    except OSError as error:
        raise SuiteError("literal-rendered-shim cleanup target is unsafe") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if after.st_nlink != 0:
        raise SuiteError("literal-rendered-shim cleanup identity drift")


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _render_shim(template: bytes, runtime: Path, client: Path) -> bytes:
    try:
        text = template.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SuiteError("literal-rendered-shim template is invalid") from error
    if (
        text.count("@TASK_WITNESS_PYTHON@") != 1
        or text.count("@TASK_WITNESS_CLIENT@") != 1
        or not text.endswith("\n")
    ):
        raise SuiteError("literal-rendered-shim template is invalid")
    return (
        text.replace("@TASK_WITNESS_PYTHON@", _shell_quote(str(runtime)))
        .replace("@TASK_WITNESS_CLIENT@", _shell_quote(str(client)))
        .encode("utf-8")
    )


def _run_with_protocol_descriptors(
    argv: list[str],
    repository: Path,
    environment: dict[str, str],
) -> tuple[int, bytes, bytes]:
    saved: dict[int, tuple[int, tuple[int, ...], int] | None] = {}
    installed_identities: dict[int, tuple[int, ...]] = {}

    def capture_owned_identity(descriptor: int, label: str) -> tuple[int, ...]:
        try:
            return _capture_descriptor_identity(descriptor)
        except BaseException as primary_error:
            cleanup_errors: list[BaseException] = []
            _close_capture_descriptor(descriptor, cleanup_errors)
            for cleanup_error in cleanup_errors:
                primary_error.add_note(f"{label} cleanup also failed: {cleanup_error}")
            raise

    def capture_protocol_backup(
        target: int,
        backup: int,
    ) -> tuple[int, tuple[int, ...], int]:
        backup_identity = capture_owned_identity(
            backup,
            "literal-rendered-shim protocol backup",
        )
        try:
            original_flags = fcntl.fcntl(target, fcntl.F_GETFD)
            if _capture_descriptor_identity(target) != backup_identity:
                raise OSError("protocol backup identity disagrees")
        except BaseException as primary_error:
            cleanup_errors: list[BaseException] = []
            _close_capture_descriptor(backup, cleanup_errors, backup_identity)
            for cleanup_error in cleanup_errors:
                primary_error.add_note(
                    "literal-rendered-shim protocol backup cleanup also failed: "
                    f"{cleanup_error}"
                )
            raise
        return backup, backup_identity, original_flags

    def target_has_original_state(
        target: int,
        original_identity: tuple[int, ...],
        original_flags: int,
        errors: list[BaseException],
    ) -> bool:
        return _descriptor_has_exact_state(
            target,
            original_identity,
            original_flags,
            errors,
            label="protocol",
        )

    def restore_protocol_flags(
        target: int,
        original_identity: tuple[int, ...],
        original_flags: int,
        errors: list[BaseException],
    ) -> bool:
        return _restore_descriptor_flags(
            target,
            original_identity,
            original_flags,
            errors,
            label="protocol",
        )

    def restore() -> None:
        errors: list[BaseException] = []
        for target, saved_target in saved.items():
            if saved_target is None:
                expected_identity = installed_identities.get(target)
                if expected_identity is not None:
                    _close_capture_descriptor(target, errors, expected_identity)
                continue
            backup, backup_identity, original_flags = saved_target
            restored = False
            for _attempt in range(2):
                if _restore_capture_descriptor(
                    backup,
                    target,
                    errors,
                ) and restore_protocol_flags(
                    target,
                    backup_identity,
                    original_flags,
                    errors,
                ):
                    restored = True
                    break
            if restored:
                _close_capture_descriptor(backup, errors, backup_identity)
            else:
                try:
                    current_identity = _capture_descriptor_identity(target)
                except BaseException as error:  # noqa: BLE001 - fail closed
                    errors.append(error)
                else:
                    if current_identity == backup_identity:
                        _close_capture_descriptor(
                            target,
                            errors,
                            backup_identity,
                        )
                _close_capture_descriptor(backup, errors, backup_identity)
        if errors:
            raise SuiteError(
                "literal-rendered-shim protocol descriptors cannot be restored"
            ) from errors[0]

    try:
        for target in (3, 4):
            try:
                backup = fcntl.fcntl(target, fcntl.F_DUPFD_CLOEXEC, 64)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
                saved[target] = None
            else:
                saved[target] = capture_protocol_backup(
                    target,
                    backup,
                )
            source = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
            source_identity = capture_owned_identity(
                source,
                "literal-rendered-shim protocol source",
            )
            installed_identities[target] = source_identity
            try:
                os.dup2(source, target)
            finally:
                source_primary_error = sys.exception()
                if source != target:
                    source_cleanup_errors: list[BaseException] = []
                    _close_capture_descriptor(
                        source,
                        source_cleanup_errors,
                        source_identity,
                    )
                    if source_cleanup_errors:
                        if source_primary_error is not None:
                            for cleanup_error in source_cleanup_errors:
                                source_primary_error.add_note(
                                    "literal-rendered-shim protocol source cleanup "
                                    f"also failed: {cleanup_error}"
                                )
                        else:
                            source_error = source_cleanup_errors[0]
                            for cleanup_error in source_cleanup_errors[1:]:
                                source_error.add_note(
                                    "literal-rendered-shim source cleanup also failed: "
                                    f"{cleanup_error}"
                                )
                            raise SuiteError(
                                "literal-rendered-shim protocol source cannot be retired"
                            ) from source_error
        result = _run_bounded_child(
            argv,
            repository,
            environment=environment,
            pass_fds=(3, 4),
        )
    except BaseException as primary_error:
        try:
            restore()
        except BaseException as cleanup_error:  # noqa: BLE001 - preserve primary
            primary_error.add_note(
                "literal-rendered-shim protocol descriptor restoration also failed: "
                f"{cleanup_error}"
            )
        raise
    restore()
    return result


def _validate_shim_observation(
    value: object,
    *,
    arguments: list[str],
    runtime: Path,
) -> None:
    if not isinstance(value, dict):
        raise SuiteError("literal-rendered-shim process observation is invalid")
    environment = value.get("environment")
    if not isinstance(environment, dict):
        raise SuiteError("literal-rendered-shim process observation is invalid")
    environment = dict(environment)
    if sys.platform == "darwin":
        apple_text_encoding = environment.pop("__CF_USER_TEXT_ENCODING", None)
        if not isinstance(apple_text_encoding, str):
            raise SuiteError("literal-rendered-shim process observation is invalid")
    expected_remote_debug: dict[str, str]
    if sys.version_info >= (3, 14):
        expected_remote_debug = {
            "api": "available",
            "error": "Remote debugging is not enabled",
            "outcome": "disabled",
        }
    else:
        expected_remote_debug = {"api": "unavailable"}
    if (
        value.get("argv") != arguments
        or Path(str(value.get("executable"))).resolve() != runtime.resolve()
        or environment != {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
        or value.get("flags") != {"dont_write_bytecode": 1, "isolated": 1, "no_site": 1}
        or value.get("xoptions") != {"disable-remote-debug": True}
        or value.get("remote_debug") != expected_remote_debug
        or value.get("open_descriptors") != [3, 4]
    ):
        raise SuiteError("literal-rendered-shim process observation is invalid")


def _installed_file_observation_at(
    directory_descriptor: int,
    name: str,
    path: Path,
) -> dict[str, object]:
    if not path.is_absolute() or path.name != name:
        raise SuiteError("literal-rendered-shim installed file path is invalid")
    raw, metadata = _capture_owned_regular_at(
        directory_descriptor,
        name,
        "literal-rendered-shim installed file",
    )
    if stat.S_IMODE(metadata.st_mode) != 0o500:
        raise SuiteError("literal-rendered-shim installed file is unsafe")
    return {
        "path": str(path),
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "nlink": metadata.st_nlink,
    }


def _entry_exists_at(directory_descriptor: int, name: str, label: str) -> bool:
    _safe_child_name(name, label)
    try:
        os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    except OSError as error:
        raise SuiteError(f"{label} cannot be inspected") from error
    return True


def _literal_rendered_shim_scenario(repository: Path) -> None:
    workspace_value = os.environ.get("TASK_WITNESS_QUALIFICATION_WORKSPACE")
    if not isinstance(workspace_value, str) or "\0" in workspace_value:
        raise SuiteError("literal-rendered-shim workspace is unavailable")
    workspace = Path(workspace_value)
    if not workspace.is_absolute() or ".." in workspace.parts:
        raise SuiteError("literal-rendered-shim workspace is unavailable")
    sidecar = workspace / "literal-rendered-shim-observation.json"
    process_observation = workspace / "literal-rendered-shim-process-observation.json"

    template_relative = Path("plugins/task-witness/client/task_witness_shim.sh.in")
    client_relative = Path("plugins/task-witness/client/task_witness_client.py")
    observer_relative = Path(
        "tests/plugins/task_witness_client/_shim_observer_driver.py"
    )
    template = _capture_fixed_file(repository, template_relative, "shim template")
    candidate_client = _capture_fixed_file(
        repository,
        client_relative,
        "candidate client",
    )
    observer = _capture_fixed_file(
        repository,
        observer_relative,
        "shim observer",
    )

    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    if not home.is_absolute() or ".." in home.parts:
        raise SuiteError("literal-rendered-shim passwd home is invalid")
    local = home / ".local"
    libexec = local / "libexec"
    installed_root = libexec / "task-witness"
    client_directory = installed_root / "client"
    runtime = Path(sys.executable)
    installed_client = client_directory / "task_witness_client.py"
    installed_shim = installed_root / "task-witness"
    with contextlib.ExitStack() as descriptors:
        workspace_descriptor = _open_private_directory(
            workspace,
            "literal-rendered-shim workspace",
        )
        descriptors.callback(os.close, workspace_descriptor)
        if any(
            _entry_exists_at(
                workspace_descriptor,
                path.name,
                "literal-rendered-shim observation channel",
            )
            for path in (sidecar, process_observation)
        ):
            raise SuiteError("literal-rendered-shim observation channel is not empty")

        home_descriptor = _open_private_directory(
            home,
            "literal-rendered-shim passwd home",
        )
        descriptors.callback(os.close, home_descriptor)
        local_descriptor = _open_private_child_directory(
            home_descriptor,
            local.name,
            "literal-rendered-shim local directory",
            create=not _entry_exists_at(
                home_descriptor,
                local.name,
                "literal-rendered-shim local directory",
            ),
        )
        descriptors.callback(os.close, local_descriptor)
        libexec_descriptor = _open_private_child_directory(
            local_descriptor,
            libexec.name,
            "literal-rendered-shim libexec directory",
            create=not _entry_exists_at(
                local_descriptor,
                libexec.name,
                "literal-rendered-shim libexec directory",
            ),
        )
        descriptors.callback(os.close, libexec_descriptor)
        if _entry_exists_at(
            libexec_descriptor,
            installed_root.name,
            "literal-rendered-shim install root",
        ):
            raise SuiteError("literal-rendered-shim install root already exists")
        installed_root_descriptor = _open_private_child_directory(
            libexec_descriptor,
            installed_root.name,
            "literal-rendered-shim install root",
            create=True,
        )
        descriptors.callback(os.close, installed_root_descriptor)
        client_directory_descriptor = _open_private_child_directory(
            installed_root_descriptor,
            client_directory.name,
            "literal-rendered-shim client directory",
            create=True,
        )
        descriptors.callback(os.close, client_directory_descriptor)

        observer_program = (
            observer
            + b"\nobserve_shim("
            + repr(str(process_observation)).encode("utf-8")
            + b")\n"
        )
        _write_new_file_at(
            client_directory_descriptor,
            installed_client.name,
            observer_program,
            0o500,
        )
        rendered_shim = _render_shim(template, runtime, installed_client)
        _write_new_file_at(
            installed_root_descriptor,
            installed_shim.name,
            rendered_shim,
            0o500,
        )

        hostile_environment = {
            "HOME": "/attacker/home",
            "PATH": "/attacker/bin",
            "PYTHONPATH": "/attacker/python",
            "PYTHONWARNINGS": "error",
            "VIRTUAL_ENV": "/attacker/venv",
            "GIT_CONFIG_GLOBAL": "/attacker/gitconfig",
            "SSH_AUTH_SOCK": "/attacker/agent",
            "XDG_CONFIG_HOME": "/attacker/xdg",
            "HTTPS_PROXY": "http://attacker.invalid",
            "LC_ALL": "attacker",
        }
        arguments = [
            "validate",
            "--bundle",
            "/tmp/bundle with spaces",
            "",
            "--literal=*",
        ]
        returncode, stdout_raw, stderr_raw = _run_with_protocol_descriptors(
            [str(installed_shim), *arguments],
            repository,
            hostile_environment,
        )
        if returncode != 0 or stdout_raw or stderr_raw:
            raise SuiteError("literal-rendered-shim observer execution failed")
        observation_raw, _metadata = _capture_owned_regular_at(
            workspace_descriptor,
            process_observation.name,
            "literal-rendered-shim process observation",
        )
        try:
            observation_value = json.loads(observation_raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise SuiteError(
                "literal-rendered-shim process observation is invalid"
            ) from error
        _validate_shim_observation(
            observation_value,
            arguments=arguments,
            runtime=runtime,
        )

        unsafe_template = template.replace(
            b"/usr/bin/env -i LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC ",
            b"",
        )
        if unsafe_template == template:
            raise SuiteError("literal-rendered-shim sensitivity mutation failed")
        _unlink_owned_regular_at(
            installed_root_descriptor,
            installed_shim.name,
            expected_mode=0o500,
        )
        _write_new_file_at(
            installed_root_descriptor,
            installed_shim.name,
            _render_shim(unsafe_template, runtime, installed_client),
            0o500,
        )
        returncode, stdout_raw, stderr_raw = _run_with_protocol_descriptors(
            [str(installed_shim), "validate"],
            repository,
            {"TASK_WITNESS_SHIM_MUTATION": "exposed"},
        )
        if returncode != 0 or stdout_raw or stderr_raw:
            raise SuiteError("literal-rendered-shim sensitivity execution failed")
        mutated_raw, _metadata = _capture_owned_regular_at(
            workspace_descriptor,
            process_observation.name,
            "literal-rendered-shim sensitivity observation",
        )
        try:
            mutated_value = json.loads(mutated_raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise SuiteError(
                "literal-rendered-shim sensitivity observation failed"
            ) from error
        if (
            not isinstance(mutated_value, dict)
            or not isinstance(mutated_value.get("environment"), dict)
            or mutated_value["environment"].get("TASK_WITNESS_SHIM_MUTATION")
            != "exposed"
        ):
            raise SuiteError("literal-rendered-shim sensitivity observation failed")
        _unlink_owned_regular_at(
            workspace_descriptor,
            process_observation.name,
            expected_mode=None,
        )

        _unlink_owned_regular_at(
            installed_root_descriptor,
            installed_shim.name,
            expected_mode=0o500,
        )
        _write_new_file_at(
            installed_root_descriptor,
            installed_shim.name,
            rendered_shim,
            0o500,
        )
        _unlink_owned_regular_at(
            client_directory_descriptor,
            installed_client.name,
            expected_mode=0o500,
        )
        _write_new_file_at(
            client_directory_descriptor,
            installed_client.name,
            candidate_client,
            0o500,
        )
        if _entry_exists_at(
            workspace_descriptor,
            process_observation.name,
            "literal-rendered-shim process observation",
        ):
            raise SuiteError("literal-rendered-shim candidate was observed")

        observation = {
            "contract": "task-witness-rendered-shim-observation-v1",
            "template": {
                "path": template_relative.as_posix(),
                "length": len(template),
                "sha256": hashlib.sha256(template).hexdigest(),
            },
            "runtime_executable_path": str(runtime),
            "client": _installed_file_observation_at(
                client_directory_descriptor,
                installed_client.name,
                installed_client,
            ),
            "shim": _installed_file_observation_at(
                installed_root_descriptor,
                installed_shim.name,
                installed_shim,
            ),
        }
        raw = _canonical_bytes(observation)
        _write_new_file_at(
            workspace_descriptor,
            sidecar.name,
            raw,
            0o600,
        )
        remaining = memoryview(raw)
        while remaining:
            written = os.write(1, remaining)
            if written <= 0:
                raise SuiteError("literal-rendered-shim observation output failed")
            remaining = remaining[written:]


def _literal_rendered_shim_suite(repository: Path) -> unittest.TestSuite:
    return unittest.TestSuite(
        (
            unittest.FunctionTestCase(
                lambda: _literal_rendered_shim_scenario(repository)
            ),
        )
    )


def _load_test_module(repository: Path) -> ModuleType:
    path = repository / QUALIFICATION_TEST
    try:
        source = path.read_bytes()
    except OSError as error:
        raise SuiteError("qualification test source is unavailable") from error
    if not source:
        raise SuiteError("qualification test source is empty")
    module = ModuleType("_task_witness_qualification_suite_tests")
    module.__file__ = str(path)
    module.__package__ = ""
    try:
        code = compile(source, str(path), "exec", dont_inherit=True)
        exec(code, module.__dict__)  # noqa: S102 - execute exact reviewed test bytes
    except BaseException as error:
        raise SuiteError("qualification test source cannot be imported") from error
    return module


def _qualification_runner_suite(repository: Path) -> unittest.TestSuite:
    module = _load_test_module(repository)
    case = getattr(module, "TaskWitnessQualificationTests", None)
    if not isinstance(case, type) or not issubclass(case, unittest.TestCase):
        raise SuiteError("qualification test case is unavailable")
    if len(set(QUALIFICATION_RUNNER_SELECTORS)) != len(QUALIFICATION_RUNNER_SELECTORS):
        raise SuiteError("qualification selectors are not unique")
    tests = []
    for selector in QUALIFICATION_RUNNER_SELECTORS:
        method = getattr(case, selector, None)
        if not callable(method):
            raise SuiteError(f"qualification selector {selector!r} is unavailable")
        tests.append(case(selector))
    return unittest.TestSuite(tests)


def _fixed_candidate_test_path(
    repository: Path,
    relative: Path,
    module_name: str,
    suite_id: str = "client-common",
) -> Path:
    expected_relative = Path(*module_name.split(".")).with_suffix(".py")
    if (
        relative != expected_relative
        or relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise SuiteError(f"{suite_id} test module path disagrees")
    current = repository
    for part in relative.parts[:-1]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise SuiteError(f"{suite_id} test module path is unavailable") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SuiteError(f"{suite_id} test module path is unsafe")
    path = current / relative.name
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SuiteError(f"{suite_id} test module file is unavailable") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise SuiteError(f"{suite_id} test module file is unsafe")
    return path


def _load_fixed_client_test_module(
    repository: Path,
    relative: Path,
    module_name: str,
    suite_id: str = "client-common",
) -> ModuleType:
    existing = sys.modules.get(module_name)
    if existing is not None:
        marker = getattr(existing, "__task_witness_fixed_suite_capture__", None)
        if (
            not isinstance(marker, tuple)
            or len(marker) != 2
            or marker[0] != suite_id
            or getattr(existing, "__file__", None) != str(repository / relative)
        ):
            raise SuiteError(f"{suite_id} test module collision: {module_name}")
        return existing
    path = _fixed_candidate_test_path(
        repository,
        relative,
        module_name,
        suite_id,
    )
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        source = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            source.extend(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise SuiteError(f"{suite_id} test module cannot be captured") from error
    finally:
        if "descriptor" in locals():
            os.close(descriptor)

    def stable_binding(metadata: os.stat_result) -> tuple[int, ...]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_nlink,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    captured_binding = stable_binding(before)
    if (
        captured_binding != stable_binding(after)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size != len(source)
    ):
        raise SuiteError(f"{suite_id} test module identity drift")
    try:
        visible = path.stat(follow_symlinks=False)
    except OSError as error:
        raise SuiteError(f"{suite_id} test module identity drift") from error
    if stable_binding(visible) != captured_binding:
        raise SuiteError(f"{suite_id} test module identity drift")
    spec = importlib.util.spec_from_loader(module_name, loader=None, origin=str(path))
    if spec is None:
        raise SuiteError(f"{suite_id} test module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    module.__package__ = module_name.rpartition(".")[0]
    module.__task_witness_fixed_suite_capture__ = (
        suite_id,
        hashlib.sha256(source).hexdigest(),
    )
    sys.modules[module_name] = module
    try:
        code = compile(bytes(source), str(path), "exec", dont_inherit=True)
        exec(code, module.__dict__)  # noqa: S102 - execute exact descriptor-captured bytes
    except BaseException as error:
        sys.modules.pop(module_name, None)
        raise SuiteError(f"{suite_id} test module cannot be imported") from error
    try:
        visible = path.stat(follow_symlinks=False)
    except OSError as error:
        sys.modules.pop(module_name, None)
        raise SuiteError(f"{suite_id} test module identity drift") from error
    if stable_binding(visible) != captured_binding:
        sys.modules.pop(module_name, None)
        raise SuiteError(f"{suite_id} test module identity drift")
    if sys.modules.get(module_name) is not module or getattr(
        module, "__file__", None
    ) != str(path):
        sys.modules.pop(module_name, None)
        raise SuiteError(f"{suite_id} test module identity drift")
    return module


@contextlib.contextmanager
def _client_common_suite(repository: Path):
    if len(CLIENT_COMMON_SELECTORS) != SUITE_EXPECTED_COUNTS["client-common"]:
        raise SuiteError("client-common selector count drift")
    if len(set(CLIENT_COMMON_SELECTORS)) != len(CLIENT_COMMON_SELECTORS):
        raise SuiteError("client-common selectors are not unique")
    before_modules = set(sys.modules)
    repository_text = str(repository)
    before_path = list(sys.path)
    sys.path.insert(0, repository_text)
    tests = []
    try:
        for relative, module_name, case_name, method_names in CLIENT_COMMON_TESTS:
            module = _load_fixed_client_test_module(
                repository,
                relative,
                module_name,
            )
            case = getattr(module, case_name, None)
            if not isinstance(case, type) or not issubclass(case, unittest.TestCase):
                raise SuiteError("client-common test case is unavailable")
            for method_name in method_names:
                selector = f"{module_name}.{case_name}.{method_name}"
                if selector != CLIENT_COMMON_SELECTORS[len(tests)]:
                    raise SuiteError("client-common selector order drift")
                method = getattr(case, method_name, None)
                if not callable(method):
                    raise SuiteError(
                        f"client-common selector {selector!r} is unavailable"
                    )
                tests.append(case(method_name))
        if len(tests) != SUITE_EXPECTED_COUNTS["client-common"]:
            raise SuiteError("client-common loaded test count drift")
        yield unittest.TestSuite(tests)
    finally:
        sys.path[:] = before_path
        for module_name in tuple(sys.modules):
            if module_name not in before_modules and (
                module_name == "tests"
                or module_name == "tests.plugins"
                or module_name.startswith(
                    (
                        "tests.plugins.task_witness_client",
                        "tests.plugins.task_witness_deployment",
                    )
                )
            ):
                sys.modules.pop(module_name, None)


@contextlib.contextmanager
def _deployment_common_suite(repository: Path):
    if len(DEPLOYMENT_COMMON_SELECTORS) != SUITE_EXPECTED_COUNTS["deployment-common"]:
        raise SuiteError("deployment-common selector count drift")
    if len(set(DEPLOYMENT_COMMON_SELECTORS)) != len(DEPLOYMENT_COMMON_SELECTORS):
        raise SuiteError("deployment-common selectors are not unique")
    if set(DEPLOYMENT_COMMON_SELECTORS) & set(CLIENT_COMMON_SELECTORS):
        raise SuiteError("deployment-common selector collision")
    before_modules = set(sys.modules)
    repository_text = str(repository)
    before_path = list(sys.path)
    sys.path.insert(0, repository_text)
    tests = []
    try:
        receipt = next(
            item
            for item in DEPLOYMENT_COMMON_TESTS
            if item[1] == "tests.plugins.task_witness_deployment.test_receipt_staging"
        )
        _load_fixed_client_test_module(
            repository,
            receipt[0],
            receipt[1],
            "deployment-common",
        )
        for relative, module_name, case_name, method_names in DEPLOYMENT_COMMON_TESTS:
            module = _load_fixed_client_test_module(
                repository,
                relative,
                module_name,
                "deployment-common",
            )
            case = getattr(module, case_name, None)
            if not isinstance(case, type) or not issubclass(case, unittest.TestCase):
                raise SuiteError("deployment-common test case is unavailable")
            for method_name in method_names:
                selector = f"{module_name}.{case_name}.{method_name}"
                if selector != DEPLOYMENT_COMMON_SELECTORS[len(tests)]:
                    raise SuiteError("deployment-common selector order drift")
                method = getattr(case, method_name, None)
                if not callable(method):
                    raise SuiteError(
                        f"deployment-common selector {selector!r} is unavailable"
                    )
                tests.append(case(method_name))
        if len(tests) != SUITE_EXPECTED_COUNTS["deployment-common"]:
            raise SuiteError("deployment-common loaded test count drift")
        yield unittest.TestSuite(tests)
    finally:
        sys.path[:] = before_path
        for module_name in tuple(sys.modules):
            if module_name not in before_modules and (
                module_name == "tests"
                or module_name == "tests.plugins"
                or module_name.startswith(
                    (
                        "tests.plugins.task_witness_client",
                        "tests.plugins.task_witness_deployment",
                    )
                )
            ):
                sys.modules.pop(module_name, None)


@contextlib.contextmanager
def _package_contract_suite(repository: Path):
    expected_count = SUITE_EXPECTED_COUNTS["package-contract"]
    if (
        len(PACKAGE_CONTRACT_SELECTORS) != expected_count
        or len(set(PACKAGE_CONTRACT_SELECTORS)) != expected_count
    ):
        raise SuiteError("package-contract selector count drift")
    before_modules = set(sys.modules)
    before_path = list(sys.path)
    sys.path.insert(0, str(repository))
    tests = []
    try:
        for relative, module_name, case_name, method_names in PACKAGE_CONTRACT_TESTS:
            module = _load_fixed_client_test_module(
                repository,
                relative,
                module_name,
                "package-contract",
            )
            case = getattr(module, case_name, None)
            if not isinstance(case, type) or not issubclass(case, unittest.TestCase):
                raise SuiteError("package-contract test case is unavailable")
            for method_name in method_names:
                selector = f"{module_name}.{case_name}.{method_name}"
                if selector != PACKAGE_CONTRACT_SELECTORS[len(tests)]:
                    raise SuiteError("package-contract selector order drift")
                method = getattr(case, method_name, None)
                if not callable(method):
                    raise SuiteError(
                        f"package-contract selector {selector!r} is unavailable"
                    )
                tests.append(case(method_name))
        if len(tests) != expected_count:
            raise SuiteError("package-contract loaded test count drift")
        yield unittest.TestSuite(tests)
    finally:
        sys.path[:] = before_path
        for module_name in tuple(sys.modules):
            if module_name not in before_modules and (
                module_name == "tests"
                or module_name == "tests.test_task_witness_package"
            ):
                sys.modules.pop(module_name, None)


@contextlib.contextmanager
def _portable_deployment_vertical_suite(
    repository: Path,
    suite_id: str,
    groups: tuple[tuple[Path, str, str, tuple[str, ...]], ...],
    selectors: tuple[str, ...],
):
    expected_count = SUITE_EXPECTED_COUNTS[suite_id]
    if len(selectors) != expected_count or len(set(selectors)) != expected_count:
        raise SuiteError("qualification suite selector count drift")
    before_modules = set(sys.modules)
    before_path = list(sys.path)
    sys.path.insert(0, str(repository))
    tests = []
    try:
        if suite_id == "macos-acl":
            receipt = next(
                item
                for item in groups
                if item[1]
                == "tests.plugins.task_witness_deployment.test_receipt_staging"
            )
            _load_fixed_client_test_module(
                repository,
                receipt[0],
                receipt[1],
                suite_id,
            )
        for relative, module_name, case_name, method_names in groups:
            module = _load_fixed_client_test_module(
                repository,
                relative,
                module_name,
                suite_id,
            )
            case = getattr(module, case_name, None)
            if not isinstance(case, type) or not issubclass(case, unittest.TestCase):
                raise SuiteError(f"{suite_id} test case is unavailable")
            for method_name in method_names:
                selector = f"{module_name}.{case_name}.{method_name}"
                if selector != selectors[len(tests)]:
                    raise SuiteError("qualification suite selector order drift")
                method = getattr(case, method_name, None)
                if not callable(method):
                    raise SuiteError(f"{suite_id} selector {selector!r} is unavailable")
                tests.append(case(method_name))
        if len(tests) != expected_count:
            raise SuiteError("qualification suite loaded test count drift")
        yield unittest.TestSuite(tests)
    finally:
        sys.path[:] = before_path
        for module_name in tuple(sys.modules):
            if module_name not in before_modules and (
                module_name == "tests"
                or module_name == "tests.plugins"
                or module_name.startswith(
                    (
                        "tests.plugins.task_witness_client",
                        "tests.plugins.task_witness_deployment",
                    )
                )
            ):
                sys.modules.pop(module_name, None)


def _capture_descriptor_identity(descriptor: int) -> tuple[int, ...]:
    metadata = os.fstat(descriptor)
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_rdev,
    )


def _native_fcntl(descriptor: int, command: int, argument: int = 0) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    native_fcntl = libc.fcntl
    native_fcntl.argtypes = [ctypes.c_int, ctypes.c_int]
    native_fcntl.restype = ctypes.c_int
    result = native_fcntl(descriptor, command, argument)
    if result < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return result


def _descriptor_has_exact_state(
    descriptor: int,
    expected_identity: tuple[int, ...],
    expected_flags: int,
    cleanup_errors: list[BaseException],
    *,
    label: str,
) -> bool:
    try:
        observed_identity = _capture_descriptor_identity(descriptor)
    except BaseException as error:  # noqa: BLE001 - cleanup must continue
        cleanup_errors.append(error)
        return False
    try:
        observed_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
    except BaseException as error:  # noqa: BLE001 - preserve wrapper failure
        cleanup_errors.append(error)
        try:
            observed_flags = _native_fcntl(descriptor, fcntl.F_GETFD)
        except BaseException as native_error:  # noqa: BLE001 - fail closed
            cleanup_errors.append(native_error)
            return False
    if observed_identity != expected_identity:
        cleanup_errors.append(SuiteError(f"{label} restoration identity disagrees"))
        return False
    if observed_flags != expected_flags:
        cleanup_errors.append(SuiteError(f"{label} restoration flags disagree"))
        return False
    return True


def _restore_descriptor_flags(
    descriptor: int,
    expected_identity: tuple[int, ...],
    expected_flags: int,
    cleanup_errors: list[BaseException],
    *,
    label: str,
) -> bool:
    for _attempt in range(2):
        try:
            fcntl.fcntl(descriptor, fcntl.F_SETFD, expected_flags)
        except BaseException as error:  # noqa: BLE001 - ambiguous outcome
            cleanup_errors.append(error)
        if _descriptor_has_exact_state(
            descriptor,
            expected_identity,
            expected_flags,
            cleanup_errors,
            label=label,
        ):
            return True

    try:
        _native_fcntl(descriptor, fcntl.F_SETFD, expected_flags)
    except BaseException as error:  # noqa: BLE001 - ambiguous native outcome
        cleanup_errors.append(error)
    return _descriptor_has_exact_state(
        descriptor,
        expected_identity,
        expected_flags,
        cleanup_errors,
        label=label,
    )


def _restore_capture_descriptor(
    source: int,
    target: int,
    cleanup_errors: list[BaseException],
) -> bool:
    try:
        source_identity = _capture_descriptor_identity(source)
    except BaseException as error:  # noqa: BLE001 - cleanup must continue
        cleanup_errors.append(error)
        return False

    last_error: BaseException | None = None
    for _attempt in range(2):
        try:
            os.dup2(source, target)
        except BaseException as error:  # noqa: BLE001 - ambiguous dup2 outcome
            last_error = error
            cleanup_errors.append(error)
            try:
                if _capture_descriptor_identity(target) == source_identity:
                    return True
            except BaseException as inspection_error:  # noqa: BLE001
                cleanup_errors.append(inspection_error)
        else:
            return True

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        native_dup2 = libc.dup2
        native_dup2.argtypes = [ctypes.c_int, ctypes.c_int]
        native_dup2.restype = ctypes.c_int
        if native_dup2(source, target) != target:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
        if _capture_descriptor_identity(target) != source_identity:
            raise OSError("restored descriptor identity disagrees")
    except BaseException as error:  # noqa: BLE001 - retain every cleanup failure
        if last_error is None or error is not last_error:
            cleanup_errors.append(error)
        return False
    return True


def _install_capture_descriptor(
    source: int,
    target: int,
    original_identity: tuple[int, ...],
    capture_identity: tuple[int, ...],
) -> None:
    try:
        os.dup2(source, target)
    except BaseException as primary_error:
        try:
            observed_identity = _capture_descriptor_identity(target)
        except BaseException as inspection_error:  # noqa: BLE001
            primary_error.add_note(
                f"suite capture target inspection also failed: {inspection_error}"
            )
        else:
            if observed_identity not in {original_identity, capture_identity}:
                primary_error.add_note("suite capture target identity changed")
        raise
    if _capture_descriptor_identity(target) != capture_identity:
        raise OSError("suite capture target identity disagrees")


def _retire_failed_capture_target(
    descriptor: int,
    owned_identities: tuple[tuple[int, ...], ...],
    cleanup_errors: list[BaseException],
) -> None:
    try:
        current_identity = _capture_descriptor_identity(descriptor)
    except OSError as error:
        if error.errno != 9:
            cleanup_errors.append(error)
        return
    except BaseException as error:  # noqa: BLE001 - cleanup must continue
        cleanup_errors.append(error)
        return
    if current_identity not in owned_identities:
        cleanup_errors.append(SuiteError("suite capture target identity changed"))
        return
    _close_capture_descriptor(descriptor, cleanup_errors, current_identity)


def _close_capture_descriptor(
    descriptor: int,
    cleanup_errors: list[BaseException],
    expected_identity: tuple[int, ...] | None = None,
) -> None:
    try:
        current_identity = _capture_descriptor_identity(descriptor)
    except OSError as error:
        if error.errno != 9:
            cleanup_errors.append(error)
        return
    except BaseException as error:  # noqa: BLE001 - cleanup must continue
        cleanup_errors.append(error)
        return
    if expected_identity is None:
        expected_identity = current_identity
    elif current_identity != expected_identity:
        cleanup_errors.append(SuiteError("suite cleanup descriptor identity changed"))
        return

    last_error: BaseException | None = None
    for _attempt in range(2):
        try:
            os.close(descriptor)
        except BaseException as error:  # noqa: BLE001 - close outcome is ambiguous
            last_error = error
            cleanup_errors.append(error)
            try:
                current_identity = _capture_descriptor_identity(descriptor)
            except OSError as inspection_error:
                if inspection_error.errno == 9:
                    return
                cleanup_errors.extend((error, inspection_error))
                return
            except BaseException as inspection_error:  # noqa: BLE001
                cleanup_errors.extend((error, inspection_error))
                return
            if current_identity != expected_identity:
                cleanup_errors.append(
                    SuiteError("suite cleanup descriptor identity changed")
                )
                return
        else:
            return

    try:
        current_identity = _capture_descriptor_identity(descriptor)
    except OSError as error:
        if error.errno == 9:
            return
        cleanup_errors.append(error)
        return
    except BaseException as error:  # noqa: BLE001 - cleanup must continue
        cleanup_errors.append(error)
        return
    if current_identity != expected_identity:
        cleanup_errors.append(SuiteError("suite cleanup descriptor identity changed"))
        return
    os.closerange(descriptor, descriptor + 1)
    try:
        os.fstat(descriptor)
    except OSError as error:
        if error.errno == 9:
            return
        cleanup_errors.append(error)
    else:
        cleanup_errors.append(last_error or OSError("descriptor remained open"))


def _finish_capture_cleanup(
    *,
    redirected_stdout: bool,
    redirected_stderr: bool,
    saved_stdout: int,
    saved_stderr: int,
    original_stdout_identity: tuple[int, ...],
    original_stderr_identity: tuple[int, ...],
    original_stdout_flags: int,
    original_stderr_flags: int,
    stdout_capture_identity: tuple[int, ...],
    stderr_capture_identity: tuple[int, ...],
    remaining_descriptors: tuple[int, ...],
    primary_error: BaseException | None,
) -> list[BaseException]:
    cleanup_errors: list[BaseException] = []
    try:
        _flush_native_stdio()
    except BaseException as error:  # noqa: BLE001 - cleanup must continue
        cleanup_errors.append(error)
    stdout_restored = not redirected_stdout or (
        _restore_capture_descriptor(saved_stdout, 1, cleanup_errors)
        and _restore_descriptor_flags(
            1,
            original_stdout_identity,
            original_stdout_flags,
            cleanup_errors,
            label="suite stdout",
        )
    )
    if not stdout_restored:
        _retire_failed_capture_target(
            1,
            (stdout_capture_identity, original_stdout_identity),
            cleanup_errors,
        )
    stderr_restored = not redirected_stderr or (
        _restore_capture_descriptor(saved_stderr, 2, cleanup_errors)
        and _restore_descriptor_flags(
            2,
            original_stderr_identity,
            original_stderr_flags,
            cleanup_errors,
            label="suite stderr",
        )
    )
    if not stderr_restored:
        _retire_failed_capture_target(
            2,
            (stderr_capture_identity, original_stderr_identity),
            cleanup_errors,
        )
    for descriptor in (saved_stdout, saved_stderr, *remaining_descriptors):
        if descriptor >= 0:
            _close_capture_descriptor(descriptor, cleanup_errors)
    if primary_error is not None:
        for error in cleanup_errors:
            primary_error.add_note(f"suite descriptor cleanup also failed: {error}")
    return cleanup_errors


def _run_with_captured_descriptors(
    suite: unittest.TestSuite,
) -> tuple[unittest.TestResult, bytes, bytes]:
    def high_duplicate(descriptor: int) -> int:
        try:
            return fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 64)
        except OSError as error:
            raise SuiteError("suite process descriptors cannot be captured") from error

    def retire_low_descriptor(descriptor: int) -> None:
        identity = os.fstat(descriptor)
        try:
            os.close(descriptor)
        except BaseException:
            try:
                retained_identity = os.fstat(descriptor)
            except OSError:
                pass
            else:
                if retained_identity == identity:
                    with contextlib.suppress(BaseException):
                        os.close(descriptor)
            raise

    def high_pipe() -> tuple[int, int]:
        try:
            low_read, low_write = os.pipe()
            high_read = high_duplicate(low_read)
            high_write = high_duplicate(low_write)
            descriptor = low_read
            low_read = -1
            retire_low_descriptor(descriptor)
            descriptor = low_write
            low_write = -1
            retire_low_descriptor(descriptor)
        except BaseException:
            for descriptor in (
                locals().get("low_read"),
                locals().get("low_write"),
                locals().get("high_read"),
                locals().get("high_write"),
            ):
                if isinstance(descriptor, int) and descriptor >= 0:
                    with contextlib.suppress(BaseException):
                        os.close(descriptor)
            raise
        return high_read, high_write

    stdout_read = -1
    stdout_write = -1
    stderr_read = -1
    stderr_write = -1
    saved_stdout = -1
    saved_stderr = -1
    stdout_text_descriptor = -1
    stderr_text_descriptor = -1
    stdout_capture_identity: tuple[int, ...] | None = None
    stderr_capture_identity: tuple[int, ...] | None = None
    stdout_read_identity: tuple[int, ...] | None = None
    stderr_read_identity: tuple[int, ...] | None = None
    original_stdout_identity: tuple[int, ...] | None = None
    original_stderr_identity: tuple[int, ...] | None = None
    try:
        stdout_read, stdout_write = high_pipe()
        stderr_read, stderr_write = high_pipe()
        stdout_read_identity = _capture_descriptor_identity(stdout_read)
        stderr_read_identity = _capture_descriptor_identity(stderr_read)
        stdout_capture_identity = _capture_descriptor_identity(stdout_write)
        stderr_capture_identity = _capture_descriptor_identity(stderr_write)
        saved_stdout = high_duplicate(1)
        saved_stderr = high_duplicate(2)
        original_stdout_identity = _capture_descriptor_identity(saved_stdout)
        original_stderr_identity = _capture_descriptor_identity(saved_stderr)
        stdout_text_descriptor = high_duplicate(stdout_write)
        stderr_text_descriptor = high_duplicate(stderr_write)
        original_stdout_flags = fcntl.fcntl(1, fcntl.F_GETFD)
        original_stderr_flags = fcntl.fcntl(2, fcntl.F_GETFD)
        if (
            _capture_descriptor_identity(1) != original_stdout_identity
            or _capture_descriptor_identity(2) != original_stderr_identity
        ):
            raise OSError("suite process descriptor backup identity disagrees")
    except BaseException as error:
        for descriptor in {
            stdout_read,
            stdout_write,
            stderr_read,
            stderr_write,
            saved_stdout,
            saved_stderr,
            stdout_text_descriptor,
            stderr_text_descriptor,
        }:
            if descriptor >= 0:
                with contextlib.suppress(BaseException):
                    os.close(descriptor)
        if isinstance(error, OSError):
            raise SuiteError("suite process descriptors cannot be captured") from error
        raise

    assert stdout_capture_identity is not None
    assert stderr_capture_identity is not None
    assert stdout_read_identity is not None
    assert stderr_read_identity is not None
    assert original_stdout_identity is not None
    assert original_stderr_identity is not None
    assert original_stdout_flags is not None
    assert original_stderr_flags is not None

    stdout_drain: _BoundedFDDrain | None = None
    stderr_drain: _BoundedFDDrain | None = None
    redirected_stdout = False
    redirected_stderr = False
    result = unittest.TestResult()
    stdout_raw: bytes | None = None
    stderr_raw: bytes | None = None
    try:
        stdout_drain = _BoundedFDDrain(stdout_read, DETAIL_MAX_BYTES)
        stderr_drain = _BoundedFDDrain(stderr_read, DETAIL_MAX_BYTES)
        stdout_drain.start()
        stderr_drain.start()
        sys.stdout.flush()
        sys.stderr.flush()
        _flush_native_stdio()
        redirected_stdout = True
        _install_capture_descriptor(
            stdout_write,
            1,
            original_stdout_identity,
            stdout_capture_identity,
        )
        redirected_stderr = True
        _install_capture_descriptor(
            stderr_write,
            2,
            original_stderr_identity,
            stderr_capture_identity,
        )
        os.close(stdout_write)
        stdout_write = -1
        os.close(stderr_write)
        stderr_write = -1
        with contextlib.ExitStack() as stack:
            stdout = os.fdopen(
                stdout_text_descriptor,
                "w",
                encoding="utf-8",
                errors="strict",
                newline="",
            )
            stdout_text_descriptor = -1
            stack.enter_context(stdout)
            stderr = os.fdopen(
                stderr_text_descriptor,
                "w",
                encoding="utf-8",
                errors="strict",
                newline="",
            )
            stderr_text_descriptor = -1
            stack.enter_context(stderr)
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            suite.run(result)
    except OSError as error:
        raise SuiteError("suite process descriptors cannot be captured") from error
    finally:
        primary_error = sys.exception()
        stdout_drain_started = stdout_drain is not None and stdout_drain.has_started()
        stderr_drain_started = stderr_drain is not None and stderr_drain.has_started()
        cleanup_errors = _finish_capture_cleanup(
            redirected_stdout=redirected_stdout,
            redirected_stderr=redirected_stderr,
            saved_stdout=saved_stdout,
            saved_stderr=saved_stderr,
            original_stdout_identity=original_stdout_identity,
            original_stderr_identity=original_stderr_identity,
            original_stdout_flags=original_stdout_flags,
            original_stderr_flags=original_stderr_flags,
            stdout_capture_identity=stdout_capture_identity,
            stderr_capture_identity=stderr_capture_identity,
            remaining_descriptors=(
                stdout_write,
                stderr_write,
                stdout_text_descriptor,
                stderr_text_descriptor,
            ),
            primary_error=primary_error,
        )
        read_cleanup_error_start = len(cleanup_errors)
        for drain, descriptor, expected_identity in (
            (stdout_drain, stdout_read, stdout_read_identity),
            (stderr_drain, stderr_read, stderr_read_identity),
        ):
            if drain is not None:
                descriptor = drain.relinquish_unstarted_descriptor()
            if descriptor >= 0:
                _close_capture_descriptor(
                    descriptor,
                    cleanup_errors,
                    expected_identity,
                )
        if primary_error is not None:
            for error in cleanup_errors[read_cleanup_error_start:]:
                primary_error.add_note(f"suite descriptor cleanup also failed: {error}")
        descriptor_cleanup_failed = bool(cleanup_errors)
        drain_errors: list[BaseException] = []
        if stdout_drain_started:
            assert stdout_drain is not None
            try:
                stdout_raw = stdout_drain.finish("suite stdout")
            except BaseException as error:  # noqa: BLE001 - preserve active primary
                cleanup_errors.append(error)
                drain_errors.append(error)
                if primary_error is not None:
                    primary_error.add_note(f"suite stdout cleanup also failed: {error}")
        if stderr_drain_started:
            assert stderr_drain is not None
            try:
                stderr_raw = stderr_drain.finish("suite stderr")
            except BaseException as error:  # noqa: BLE001 - preserve active primary
                cleanup_errors.append(error)
                drain_errors.append(error)
                if primary_error is not None:
                    primary_error.add_note(f"suite stderr cleanup also failed: {error}")
        if primary_error is None and descriptor_cleanup_failed:
            raise SuiteError(
                "suite process descriptors cannot be restored"
            ) from cleanup_errors[0]
        if primary_error is None and drain_errors:
            first_drain_error = drain_errors[0]
            if isinstance(first_drain_error, SuiteError):
                raise first_drain_error
            raise SuiteError(
                "suite process descriptors cannot be captured"
            ) from first_drain_error

    assert stdout_raw is not None and stderr_raw is not None
    return (
        result,
        stdout_raw,
        stderr_raw,
    )


def _execute_suite(suite_id: str, repository: Path) -> dict[str, object]:
    selector_owners: dict[str, str] = {}
    for registered_id, registered_selectors in SUITE_SELECTORS.items():
        if len(set(registered_selectors)) != len(registered_selectors):
            raise SuiteError("qualification suite selectors are not unique")
        for registered_selector in registered_selectors:
            previous = selector_owners.setdefault(registered_selector, registered_id)
            if previous != registered_id:
                raise SuiteError("qualification suite selector collision")
    selectors = SUITE_SELECTORS.get(suite_id)
    if selectors is None:
        raise SuiteError("qualification suite is not implemented")
    expected_count = SUITE_EXPECTED_COUNTS.get(suite_id)
    if expected_count is None or len(selectors) != expected_count:
        raise SuiteError("qualification suite selector count drift")
    if suite_id in PLATFORM_VERTICALS:
        _require_platform_vertical_host(suite_id)
    if suite_id == "qualification-runner-contract":
        if selectors is not QUALIFICATION_RUNNER_SELECTORS:
            raise SuiteError("qualification suite selector table drift")
        suite = _qualification_runner_suite(repository)
        result, stdout_raw, stderr_raw = _run_with_captured_descriptors(suite)
    elif suite_id == "client-common":
        if selectors is not CLIENT_COMMON_SELECTORS:
            raise SuiteError("qualification suite selector table drift")
        with _client_common_suite(repository) as suite:
            result, stdout_raw, stderr_raw = _run_with_captured_descriptors(suite)
    elif suite_id == "deployment-common":
        if selectors is not DEPLOYMENT_COMMON_SELECTORS:
            raise SuiteError("qualification suite selector table drift")
        with _deployment_common_suite(repository) as suite:
            result, stdout_raw, stderr_raw = _run_with_captured_descriptors(suite)
    elif suite_id == "package-contract":
        if selectors is not PACKAGE_CONTRACT_SELECTORS:
            raise SuiteError("qualification suite selector table drift")
        with _package_contract_suite(repository) as suite:
            result, stdout_raw, stderr_raw = _run_with_captured_descriptors(suite)
    elif suite_id == "task-witness-source-stage":
        if selectors is not TASK_WITNESS_SOURCE_STAGE_SELECTORS:
            raise SuiteError("qualification suite selector table drift")
        suite = _task_witness_source_stage_suite(repository)
        result, stdout_raw, stderr_raw = _run_with_captured_descriptors(suite)
    elif suite_id == "public-release-source-stage":
        if selectors is not PUBLIC_RELEASE_SOURCE_STAGE_SELECTORS:
            raise SuiteError("qualification suite selector table drift")
        suite = _public_release_source_stage_suite(repository)
        result, stdout_raw, stderr_raw = _run_with_captured_descriptors(suite)
    elif suite_id == "literal-rendered-shim":
        if selectors is not LITERAL_RENDERED_SHIM_SELECTORS:
            raise SuiteError("qualification suite selector table drift")
        suite = _literal_rendered_shim_suite(repository)
        result, stdout_raw, stderr_raw = _run_with_captured_descriptors(suite)
    elif suite_id in PORTABLE_DEPLOYMENT_VERTICALS:
        groups, expected_selectors = PORTABLE_DEPLOYMENT_VERTICALS[suite_id]
        if selectors is not expected_selectors:
            raise SuiteError("qualification suite selector table drift")
        with _portable_deployment_vertical_suite(
            repository,
            suite_id,
            groups,
            selectors,
        ) as suite:
            result, stdout_raw, stderr_raw = _run_with_captured_descriptors(suite)
    elif suite_id in PLATFORM_VERTICALS:
        groups, expected_selectors = PLATFORM_VERTICALS[suite_id]
        if selectors is not expected_selectors:
            raise SuiteError("qualification suite selector table drift")
        with _portable_deployment_vertical_suite(
            repository,
            suite_id,
            groups,
            selectors,
        ) as suite:
            result, stdout_raw, stderr_raw = _run_with_captured_descriptors(suite)
    else:
        raise SuiteError("qualification suite dispatch is unavailable")
    if (
        result.testsRun != expected_count
        or result.failures
        or result.errors
        or result.skipped
        or result.expectedFailures
        or result.unexpectedSuccesses
        or not result.wasSuccessful()
    ):
        raise SuiteError("qualification suite did not reach the exact terminal")
    return {
        "schema_version": 1,
        "contract": SUITE_RESULT_CONTRACT,
        "id": suite_id,
        "observed_count": result.testsRun,
        "terminal": "passed",
        "detail_stdout_length": len(stdout_raw),
        "detail_stdout_sha256": hashlib.sha256(stdout_raw).hexdigest(),
        "detail_stderr_length": len(stderr_raw),
        "detail_stderr_sha256": hashlib.sha256(stderr_raw).hexdigest(),
    }


def _suite_id_from_argv(argv: list[str] | None) -> str:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2 or arguments[0] != "--suite":
        raise SuiteError("expected exactly --suite ID")
    return arguments[1]


def main(argv: list[str] | None = None) -> int:
    try:
        suite_id = _suite_id_from_argv(argv)
        repository = Path(__file__).resolve(strict=True).parents[1]
        value = _execute_suite(suite_id, repository)
    except SuiteError as error:
        print(f"qualification suite failed: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(_canonical_bytes(value))
    sys.stdout.buffer.flush()
    return 0


def entrypoint_main() -> int:
    print(
        "qualification suite failed: native candidate execution is unavailable "
        "without a host-owned content-pinned network-denied sandbox and prior "
        "review authorization",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint_main())
