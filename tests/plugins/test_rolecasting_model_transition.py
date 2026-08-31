from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    REPOSITORY
    / "plugins"
    / "rolecasting"
    / "skills"
    / "choosing-agent-models"
    / "scripts"
    / "model_transition.py"
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "rolecasting_model_transition", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("model-transition guard cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scope(classification: str = "consequential") -> dict[str, Any]:
    return {
        "classification": classification,
        "roles": [{"name": "implementer", "classification": classification}],
        "daybreak_required": classification == "security",
        "reclassification_sha256": None,
    }


def selection(
    role: str = "sol",
    model: str = "gpt-5.6-sol",
    qualified_classification: str = "consequential",
    provenance: str = "policy",
    operator_selection_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "role": role,
        "model": model,
        "reasoning_effort": "high",
        "qualified_classification": qualified_classification,
        "provenance": provenance,
        "operator_selection_sha256": operator_selection_sha256,
    }


def route(selected: dict[str, Any]) -> dict[str, Any]:
    return {
        "fresh": True,
        "eligible": True,
        "capacity": "available",
        "failure_disposition": "blocked",
        "target": {
            "product_family": "codex",
            "surface": "chatgpt-codex",
            "version": "2026.08",
            "executor": "codex",
        },
        "account_binding_sha256": sha("private account binding"),
        "route_authorization_sha256": sha("authorized route"),
        "selector_sha256": sha("fresh target selector"),
        "capability_sha256": sha("fresh exact capability"),
        "selection": selected,
    }


def event(
    kind: str,
    *,
    predecessor: str | None,
    operator_action: str = "preserve",
) -> dict[str, Any]:
    return {
        "kind": kind,
        "task_sha256": sha("same task"),
        "payload_sha256": sha(f"{kind} payload"),
        "predecessor_authorization_sha256": predecessor,
        "operator_action": operator_action,
    }


def operator(
    role: str = "sol",
    model: str = "gpt-5.6-sol",
    qualified_classification: str = "consequential",
) -> dict[str, Any]:
    value = {
        "role": role,
        "model": model,
        "reasoning_effort": "high",
        "qualified_classification": qualified_classification,
    }
    return {
        **value,
        "selection_sha256": hashlib.sha256(canonical(value)).hexdigest(),
    }


def operator_route(value: dict[str, Any]) -> dict[str, Any]:
    return route(
        selection(
            value["role"],
            value["model"],
            value["qualified_classification"],
            "operator",
            value["selection_sha256"],
        )
    )


def initial_decision(
    guard: Any,
    *,
    current_scope: dict[str, Any] | None = None,
    selected: dict[str, Any] | None = None,
    selected_operator: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return guard.authorize_model_transition(
        None,
        event(
            "new-subagent",
            predecessor=None,
            operator_action="select" if selected_operator else "preserve",
        ),
        current_scope or scope(),
        selected_operator,
        operator_route(selected_operator)
        if selected_operator
        else route(selected or selection()),
    )


class ModelTransitionGuardTests(unittest.TestCase):
    def test_initial_task_and_subagent_authorizations_are_content_addressed(
        self,
    ) -> None:
        guard = load_module()

        for kind in ("new-task", "new-subagent"):
            with self.subTest(kind=kind):
                decision = guard.authorize_model_transition(
                    None,
                    event(kind, predecessor=None),
                    scope(),
                    None,
                    route(selection(model="exact-catalog-slug")),
                )
                self.assertEqual(decision["authorization"]["status"], "authorized")
                self.assertEqual(decision["next_state"]["sequence"], 1)
                self.assertEqual(
                    guard.validate_authorized_transition(decision), decision
                )

    def test_capacity_recovery_rejects_terra_and_luna_below_carried_sol_floor(
        self,
    ) -> None:
        guard = load_module()
        initial = guard.authorize_model_transition(
            None,
            event("new-subagent", predecessor=None),
            scope(),
            None,
            route(selection()),
        )
        self.assertEqual(initial["authorization"]["status"], "authorized")

        for role, model, qualified in (
            ("terra", "gpt-5.6-terra", "recoverable"),
            ("luna", "gpt-5.6-luna", "clerical"),
        ):
            with self.subTest(role=role):
                decision = guard.authorize_model_transition(
                    initial["next_state"],
                    event(
                        "capacity-recovery",
                        predecessor=initial["authorization_sha256"],
                    ),
                    scope(),
                    None,
                    route(selection(role, model, qualified, "fallback")),
                )
                self.assertEqual(decision["authorization"]["status"], "denied")
                self.assertEqual(
                    decision["authorization"]["reason"],
                    "selection-below-judgment-floor",
                )

    def test_builtin_role_cannot_claim_classification_above_its_ceiling(
        self,
    ) -> None:
        guard = load_module()

        for role, model, qualified, current_scope in (
            ("luna", "gpt-5.6-luna", "recoverable", scope("recoverable")),
            ("terra", "gpt-5.6-terra", "consequential", scope()),
            ("sol", "gpt-5.6-sol", "security", scope()),
        ):
            with self.subTest(role=role):
                decision = guard.authorize_model_transition(
                    None,
                    event("new-subagent", predecessor=None),
                    current_scope,
                    None,
                    route(selection(role, model, qualified)),
                )
                self.assertEqual(decision["authorization"]["status"], "denied")
                self.assertEqual(
                    decision["authorization"]["reason"],
                    "selection-exceeds-role-ceiling",
                )

    def test_every_continuation_event_requires_fresh_same_task_authorization(
        self,
    ) -> None:
        guard = load_module()
        initial = initial_decision(guard)

        for kind in ("follow-up", "resume", "retry", "capacity-recovery"):
            with self.subTest(kind=kind):
                continued = guard.authorize_model_transition(
                    initial["next_state"],
                    event(kind, predecessor=initial["authorization_sha256"]),
                    scope(),
                    None,
                    route(selection()),
                )
                self.assertEqual(
                    continued["authorization"]["status"], "authorized"
                )
                self.assertEqual(continued["next_state"]["sequence"], 2)

                missing = guard.authorize_model_transition(
                    None,
                    event(kind, predecessor=None),
                    scope(),
                    None,
                    route(selection()),
                )
                self.assertEqual(
                    missing["authorization"]["reason"], "prior-state-required"
                )

    def test_stale_ineligible_or_capacity_failed_route_denies_without_fallback(
        self,
    ) -> None:
        guard = load_module()
        initial = initial_decision(guard)

        cases = (
            ("fresh", False, "route-evidence-is-stale"),
            ("eligible", False, "route-is-ineligible"),
            ("capacity", "exhausted", "route-capacity-unavailable"),
            ("capacity", "unknown", "route-capacity-unavailable"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field, value=value):
                candidate_route = route(selection())
                candidate_route[field] = value
                decision = guard.authorize_model_transition(
                    initial["next_state"],
                    event("resume", predecessor=initial["authorization_sha256"]),
                    scope(),
                    None,
                    candidate_route,
                )
                self.assertEqual(decision["authorization"]["status"], "denied")
                self.assertEqual(decision["authorization"]["reason"], reason)
                self.assertIsNone(decision["selection"])
                self.assertIsNone(decision["next_state"])

    def test_control_flow_denial_reasons_are_reachable(self) -> None:
        guard = load_module()
        initial = initial_decision(guard)

        initial_with_predecessor = guard.authorize_model_transition(
            initial["next_state"],
            event(
                "new-subagent",
                predecessor=initial["authorization_sha256"],
            ),
            scope(),
            None,
            route(selection()),
        )
        self.assertEqual(
            initial_with_predecessor["authorization"]["reason"],
            "initial-event-has-predecessor",
        )

        selected_operator = operator()
        unexpected_operator = guard.authorize_model_transition(
            None,
            event("new-subagent", predecessor=None),
            scope(),
            selected_operator,
            operator_route(selected_operator),
        )
        self.assertEqual(
            unexpected_operator["authorization"]["reason"],
            "unexpected-operator-selection",
        )

        invalid_select = guard.authorize_model_transition(
            None,
            event("new-subagent", predecessor=None, operator_action="select"),
            scope(),
            None,
            route(selection()),
        )
        self.assertEqual(
            invalid_select["authorization"]["reason"],
            "operator-selection-action-invalid",
        )

        invalid_replace = guard.authorize_model_transition(
            None,
            event("new-subagent", predecessor=None, operator_action="replace"),
            scope(),
            selected_operator,
            operator_route(selected_operator),
        )
        self.assertEqual(
            invalid_replace["authorization"]["reason"],
            "operator-replacement-action-invalid",
        )

        no_evidence = guard.authorize_model_transition(
            initial["next_state"],
            event(
                "reclassification",
                predecessor=initial["authorization_sha256"],
            ),
            scope("recoverable"),
            None,
            route(selection("terra", "gpt-5.6-terra", "recoverable")),
        )
        self.assertEqual(
            no_evidence["authorization"]["reason"],
            "reclassification-evidence-required",
        )

        unchanged_scope = scope()
        unchanged_scope["reclassification_sha256"] = sha("no-op reclassification")
        noop = guard.authorize_model_transition(
            initial["next_state"],
            event(
                "reclassification",
                predecessor=initial["authorization_sha256"],
            ),
            unchanged_scope,
            None,
            route(selection()),
        )
        self.assertEqual(
            noop["authorization"]["reason"],
            "reclassification-is-noop",
        )

        unexpected_scope_evidence = scope()
        unexpected_scope_evidence["reclassification_sha256"] = sha(
            "unexpected reclassification"
        )
        unexpected_evidence = guard.authorize_model_transition(
            initial["next_state"],
            event("resume", predecessor=initial["authorization_sha256"]),
            unexpected_scope_evidence,
            None,
            route(selection()),
        )
        self.assertEqual(
            unexpected_evidence["authorization"]["reason"],
            "unexpected-reclassification-evidence",
        )

        unproven_operator = guard.authorize_model_transition(
            None,
            event("new-subagent", predecessor=None),
            scope(),
            None,
            operator_route(selected_operator),
        )
        self.assertEqual(
            unproven_operator["authorization"]["reason"],
            "operator-selection-is-unproven",
        )

        nonfallback_change = guard.authorize_model_transition(
            initial["next_state"],
            event("follow-up", predecessor=initial["authorization_sha256"]),
            scope(),
            None,
            route(selection(model="gpt-5.6-sol-alternate")),
        )
        self.assertEqual(
            nonfallback_change["authorization"]["reason"],
            "selection-change-requires-fallback",
        )

        sticky_scope = scope()
        sticky_scope["daybreak_required"] = True
        daybreak_initial = initial_decision(
            guard,
            current_scope=sticky_scope,
            selected=selection(
                "daybreak", "gpt-daybreak-blue-latest", "security"
            ),
        )
        released_scope = scope()
        released_scope["reclassification_sha256"] = sha(
            "attempted Daybreak requirement release"
        )
        sticky_daybreak = guard.authorize_model_transition(
            daybreak_initial["next_state"],
            event(
                "reclassification",
                predecessor=daybreak_initial["authorization_sha256"],
            ),
            released_scope,
            None,
            route(selection()),
        )
        self.assertEqual(
            sticky_daybreak["authorization"]["reason"],
            "daybreak-requirement-is-sticky",
        )

    def test_explicit_operator_selection_is_sticky_until_explicit_replacement(
        self,
    ) -> None:
        guard = load_module()
        sol = operator()
        initial = initial_decision(guard, selected_operator=sol)
        terra = selection(
            "terra", "gpt-5.6-terra", "recoverable", "fallback"
        )

        denied = guard.authorize_model_transition(
            initial["next_state"],
            event("follow-up", predecessor=initial["authorization_sha256"]),
            scope(),
            None,
            route(terra),
        )
        self.assertEqual(
            denied["authorization"]["reason"], "selection-below-judgment-floor"
        )

        same_floor_fallback = selection(
            "sol", "gpt-5.6-sol-alternate", "consequential", "fallback"
        )
        sticky = guard.authorize_model_transition(
            initial["next_state"],
            event("follow-up", predecessor=initial["authorization_sha256"]),
            scope(),
            None,
            route(same_floor_fallback),
        )
        self.assertEqual(
            sticky["authorization"]["reason"], "operator-selection-is-sticky"
        )

        daybreak = operator(
            "daybreak", "gpt-daybreak-blue-latest", "security"
        )
        replaced = guard.authorize_model_transition(
            initial["next_state"],
            event(
                "follow-up",
                predecessor=initial["authorization_sha256"],
                operator_action="replace",
            ),
            scope(),
            daybreak,
            operator_route(daybreak),
        )
        self.assertEqual(replaced["authorization"]["status"], "authorized")
        self.assertEqual(
            replaced["next_state"]["operator_selection"], daybreak
        )

    def test_daybreak_requirement_has_no_sol_terra_or_luna_fallthrough(self) -> None:
        guard = load_module()
        security_scope = scope("security")

        for role, model, qualified in (
            ("sol", "gpt-5.6-sol", "consequential"),
            ("terra", "gpt-5.6-terra", "recoverable"),
            ("luna", "gpt-5.6-luna", "clerical"),
        ):
            with self.subTest(role=role):
                denied = guard.authorize_model_transition(
                    None,
                    event("new-task", predecessor=None),
                    security_scope,
                    None,
                    route(selection(role, model, qualified)),
                )
                self.assertEqual(denied["authorization"]["status"], "denied")

        daybreak = selection(
            "daybreak",
            "gpt-daybreak-blue-latest",
            "security",
        )
        authorized = guard.authorize_model_transition(
            None,
            event("new-task", predecessor=None),
            security_scope,
            None,
            route(daybreak),
        )
        self.assertEqual(authorized["authorization"]["status"], "authorized")

        no_capacity = route(daybreak)
        no_capacity["capacity"] = "exhausted"
        denied = guard.authorize_model_transition(
            None,
            event("new-task", predecessor=None),
            security_scope,
            None,
            no_capacity,
        )
        self.assertEqual(
            denied["authorization"]["reason"], "route-capacity-unavailable"
        )

    def test_continuation_preserves_target_account_and_predecessor_identity(self) -> None:
        guard = load_module()
        initial = initial_decision(guard)

        mutations = (
            (
                "target",
                lambda value: value["target"].update(surface="codex-cli-tui"),
                "continuation-target-changed",
            ),
            (
                "account",
                lambda value: value.update(
                    account_binding_sha256=sha("different account")
                ),
                "continuation-account-binding-changed",
            ),
        )
        for name, mutate, reason in mutations:
            with self.subTest(name=name):
                candidate_route = route(selection())
                mutate(candidate_route)
                denied = guard.authorize_model_transition(
                    initial["next_state"],
                    event("retry", predecessor=initial["authorization_sha256"]),
                    scope(),
                    None,
                    candidate_route,
                )
                self.assertEqual(denied["authorization"]["reason"], reason)

        wrong_predecessor = guard.authorize_model_transition(
            initial["next_state"],
            event("retry", predecessor=sha("wrong predecessor")),
            scope(),
            None,
            route(selection()),
        )
        self.assertEqual(
            wrong_predecessor["authorization"]["reason"],
            "predecessor-transition-mismatch",
        )

        changed_task = event(
            "retry", predecessor=initial["authorization_sha256"]
        )
        changed_task["task_sha256"] = sha("different task")
        denied = guard.authorize_model_transition(
            initial["next_state"],
            changed_task,
            scope(),
            None,
            route(selection()),
        )
        self.assertEqual(
            denied["authorization"]["reason"], "task-identity-changed"
        )

    def test_reclassification_is_explicit_and_security_floor_remains_sticky(self) -> None:
        guard = load_module()
        initial = initial_decision(guard)
        recoverable_scope = scope("recoverable")

        implicit = guard.authorize_model_transition(
            initial["next_state"],
            event("resume", predecessor=initial["authorization_sha256"]),
            recoverable_scope,
            None,
            route(selection("terra", "gpt-5.6-terra", "recoverable", "fallback")),
        )
        self.assertEqual(
            implicit["authorization"]["reason"], "reclassification-required"
        )

        recoverable_scope["reclassification_sha256"] = sha(
            "reviewed recoverable reclassification"
        )
        explicit = guard.authorize_model_transition(
            initial["next_state"],
            event(
                "reclassification",
                predecessor=initial["authorization_sha256"],
            ),
            recoverable_scope,
            None,
            route(selection("terra", "gpt-5.6-terra", "recoverable")),
        )
        self.assertEqual(explicit["authorization"]["status"], "authorized")

        security_initial = initial_decision(
            guard,
            current_scope=scope("security"),
            selected=selection(
                "daybreak", "gpt-daybreak-blue-latest", "security"
            ),
        )
        lower_scope = scope("consequential")
        lower_scope["reclassification_sha256"] = sha("attempted security downgrade")
        denied = guard.authorize_model_transition(
            security_initial["next_state"],
            event(
                "reclassification",
                predecessor=security_initial["authorization_sha256"],
            ),
            lower_scope,
            None,
            route(selection()),
        )
        self.assertEqual(
            denied["authorization"]["reason"], "security-floor-is-sticky"
        )

    def test_mixed_role_scope_uses_hardest_classification(self) -> None:
        guard = load_module()
        mixed = scope("recoverable")
        mixed["roles"].append(
            {"name": "security-reviewer", "classification": "security"}
        )

        with self.assertRaisesRegex(
            guard.ModelTransitionError,
            "does not match the hardest role",
        ):
            guard.authorize_model_transition(
                None,
                event("new-subagent", predecessor=None),
                mixed,
                None,
                route(selection()),
            )

        mixed["classification"] = "security"
        mixed["daybreak_required"] = True
        denied = guard.authorize_model_transition(
            None,
            event("new-subagent", predecessor=None),
            mixed,
            None,
            route(selection(qualified_classification="security")),
        )
        self.assertEqual(
            denied["authorization"]["reason"], "daybreak-route-required"
        )

    def test_inherited_fixed_selection_is_explicit_and_cannot_satisfy_daybreak(
        self,
    ) -> None:
        guard = load_module()
        inherited = {
            "role": "inherited-fixed",
            "model": None,
            "reasoning_effort": None,
            "qualified_classification": "consequential",
            "provenance": "inherited-fixed",
            "operator_selection_sha256": None,
        }
        authorized = guard.authorize_model_transition(
            None,
            event("new-subagent", predecessor=None),
            scope("recoverable"),
            None,
            route(inherited),
        )
        self.assertEqual(authorized["authorization"]["status"], "authorized")

        denied = guard.authorize_model_transition(
            None,
            event("new-subagent", predecessor=None),
            scope("security"),
            None,
            route(inherited),
        )
        self.assertEqual(denied["authorization"]["status"], "denied")

    def test_decision_and_predecessor_tampering_fail_closed(self) -> None:
        guard = load_module()
        decision = initial_decision(guard)
        decision["request"]["route_evidence"]["fresh"] = False

        with self.assertRaisesRegex(
            guard.ModelTransitionError,
            "decision content mismatch",
        ):
            guard.validate_authorized_transition(decision)

        state = initial_decision(guard)["next_state"]
        state["selection"]["model"] = "tampered-model"
        with self.assertRaisesRegex(
            guard.ModelTransitionError,
            "state content digest mismatch",
        ):
            guard.authorize_model_transition(
                state,
                event("resume", predecessor=state["authorization_sha256"]),
                scope(),
                None,
                route(selection()),
            )

    def test_decision_validation_uses_canonical_json_equality(self) -> None:
        guard = load_module()
        decision = initial_decision(guard)

        for field_path in (("schema_version",), ("next_state", "sequence")):
            with self.subTest(field_path=field_path):
                mutated = copy.deepcopy(decision)
                target = mutated
                for field in field_path[:-1]:
                    target = target[field]
                target[field_path[-1]] = True

                self.assertEqual(mutated, decision)
                self.assertNotEqual(canonical(mutated), canonical(decision))
                with self.assertRaisesRegex(
                    guard.ModelTransitionError,
                    "decision content mismatch",
                ):
                    guard.validate_authorized_transition(mutated)


if __name__ == "__main__":
    unittest.main()
