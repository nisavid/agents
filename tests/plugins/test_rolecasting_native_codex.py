from __future__ import annotations

import hashlib
import importlib.util
import unittest
from pathlib import Path
from typing import Any

from tests.plugins import test_rolecasting_model_transition as transition_contract

REPOSITORY = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    REPOSITORY
    / "plugins"
    / "rolecasting"
    / "skills"
    / "delegating-cross-agent-work"
    / "scripts"
    / "native_codex.py"
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "rolecasting_native_codex", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("native Codex binding cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    module.__dict__["_VERIFIED_MODULES"] = {
        "model-transition": transition_contract.load_module()
    }
    spec.loader.exec_module(module)
    return module


def intent(surface: str = "chatgpt-codex") -> dict[str, Any]:
    request_sha256 = sha("bounded worker request")
    event = transition_contract.event("new-subagent", predecessor=None)
    event["payload_sha256"] = request_sha256
    selected = transition_contract.selection()
    route = transition_contract.route(selected)
    route["target"]["surface"] = surface
    transition = transition_contract.load_module().authorize_model_transition(
        None,
        event,
        transition_contract.scope(),
        None,
        route,
    )
    return {
        "plan_sha256": sha("complete frozen dispatch plan"),
        "request_sha256": request_sha256,
        "dispatch_id": "reviewer-one",
        "surface": surface,
        "version": "2026.08",
        "executor": "codex",
        "context": "worktree:/candidate",
        "authority_intent": {
            "access": "read-only",
            "subdelegation": False,
            "external_action": False,
        },
        "assurance_minimum": {
            "target": "controller-observed",
            "model": "self-reported",
            "topology": "controller-observed",
            "authority": "self-reported",
            "execution_result": "controller-observed",
        },
        "model_transition": transition,
    }


def observation(
    frozen: Any,
    terminal_status: str = "completed",
    *,
    usable: Any = True,
    verification: str = "completed-and-verified",
) -> dict[str, Any]:
    return {
        "binding_sha256": frozen.binding_sha256,
        "plan_sha256": frozen.plan_sha256,
        "request_sha256": frozen.request_sha256,
        "dispatch_id": frozen.dispatch_id,
        "agent_id": "agent-42",
        "session_id": "session-9",
        "context": frozen.context,
        "launch_acknowledgement_sha256": sha("native launch acknowledgement"),
        "status_observations": (
            ("running", sha("native running event")),
            (terminal_status, sha(f"native {terminal_status} event")),
        ),
        "result_sha256": sha("raw native result envelope"),
        "verification_observation_sha256": sha(verification),
        "model_transition_sha256": frozen.model_transition["content_sha256"],
        "model_transition_authorization_sha256": frozen.model_transition[
            "authorization_sha256"
        ],
        "usable": usable,
    }


class NativeCodexBindingTests(unittest.TestCase):
    def test_freeze_then_record_binds_native_child_observations(self) -> None:
        native = load_module()

        for surface in ("chatgpt-codex", "codex-cli-tui"):
            with self.subTest(surface=surface):
                frozen = native.freeze_native_dispatch(intent(surface))
                self.assertEqual(frozen.target.product_family, "codex")
                self.assertEqual(frozen.target.surface, surface)
                self.assertEqual(frozen.topology.relationship, "child")
                self.assertEqual(frozen.topology.ownership, "leader-owned")
                self.assertEqual(frozen.topology.transport, "native-tool")
                self.assertFalse(frozen.authority_intent.subdelegation)
                self.assertFalse(frozen.authority_intent.external_action)
                self.assertEqual(
                    frozen.model_transition["authorization"]["status"],
                    "authorized",
                )

                recorded = native.record_native_observation(frozen, observation(frozen))
                self.assertEqual(recorded.agent_id, "agent-42")
                self.assertEqual(recorded.session_id, "session-9")
                self.assertEqual(recorded.context, "worktree:/candidate")
                self.assertEqual(recorded.terminal_status, "completed")
                self.assertEqual(
                    recorded.observed_assurance,
                    frozen.profile_maximum_assurance,
                )
                self.assertFalse(recorded.portable_evidence)
                self.assertFalse(recorded.product_attested)
                self.assertEqual(recorded.model, "gpt-5.6-sol")
                self.assertEqual(
                    recorded.model_transition_sha256,
                    frozen.model_transition["content_sha256"],
                )

    def test_profile_minimum_is_rejected_before_native_spawn(self) -> None:
        native = load_module()
        requested = intent()
        for field in ("model", "authority"):
            with self.subTest(field=field):
                requested = intent()
                requested["assurance_minimum"][field] = "controller-observed"
                with self.assertRaisesRegex(
                    native.NativeDispatchError,
                    f"{field} assurance minimum exceeds native profile",
                ):
                    native.freeze_native_dispatch(requested)

    def test_profile_rejects_executor_drift_before_native_spawn(self) -> None:
        native = load_module()
        requested = intent()
        requested["executor"] = "claimed-wrapper"

        with self.assertRaisesRegex(
            native.NativeDispatchError,
            "executor does not match native profile",
        ):
            native.freeze_native_dispatch(requested)

    def test_transition_denial_or_cross_binding_is_rejected_before_native_spawn(
        self,
    ) -> None:
        native = load_module()

        denied = intent()
        denied_event = transition_contract.event("new-subagent", predecessor=None)
        denied_event["payload_sha256"] = denied["request_sha256"]
        denied_route = transition_contract.route(transition_contract.selection())
        denied_route["capacity"] = "exhausted"
        denied["model_transition"] = (
            transition_contract.load_module().authorize_model_transition(
                None,
                denied_event,
                transition_contract.scope(),
                None,
                denied_route,
            )
        )
        self.assertEqual(
            denied["model_transition"]["authorization"]["reason"],
            "route-capacity-exhausted",
        )
        with self.assertRaisesRegex(
            native.NativeDispatchError,
            "model transition is not authorized",
        ):
            native.freeze_native_dispatch(denied)

        cross_bound = intent()
        cross_bound["request_sha256"] = sha("another payload")
        with self.assertRaisesRegex(
            native.NativeDispatchError,
            "bound to another request",
        ):
            native.freeze_native_dispatch(cross_bound)

    def test_first_dispatch_rejects_unfinished_route_preflight(self) -> None:
        native = load_module()
        requested = intent()
        transition_event = transition_contract.event(
            "new-subagent", predecessor=None
        )
        transition_event["payload_sha256"] = requested["request_sha256"]
        unfinished = transition_contract.preflight_route(
            transition_contract.selection(),
            inventory_status="stale",
        )
        requested["model_transition"] = (
            transition_contract.load_module().authorize_model_transition(
                None,
                transition_event,
                transition_contract.scope(),
                None,
                unfinished,
            )
        )

        self.assertEqual(
            requested["model_transition"]["authorization"]["reason"],
            "route-status-refresh-required",
        )
        with self.assertRaisesRegex(
            native.NativeDispatchError,
            "model transition is not authorized",
        ):
            native.freeze_native_dispatch(requested)

    def test_native_binding_rejects_user_task_transition(self) -> None:
        native = load_module()
        requested = intent()
        guard = transition_contract.load_module()
        task_event = transition_contract.event("new-task", predecessor=None)
        task_event["payload_sha256"] = requested["request_sha256"]
        requested["model_transition"] = guard.authorize_model_transition(
            None,
            task_event,
            transition_contract.scope(),
            None,
            transition_contract.route(transition_contract.selection()),
        )

        with self.assertRaisesRegex(
            native.NativeDispatchError,
            "cannot create a user task",
        ):
            native.freeze_native_dispatch(requested)

    def test_record_revalidates_constructible_frozen_state(self) -> None:
        native = load_module()
        frozen = native.freeze_native_dispatch(intent())
        forged = frozen._replace(
            profile_maximum_assurance=native.Assurance(
                target="product-attested",
                model="product-attested",
                topology="product-attested",
                authority="product-attested",
                execution_result="product-attested",
            )
        )

        with self.assertRaisesRegex(
            native.NativeDispatchError,
            "frozen native dispatch",
        ):
            native.record_native_observation(forged, observation(forged))

    def test_record_rejects_cross_binding_and_preserves_nonclean_results(self) -> None:
        native = load_module()
        frozen = native.freeze_native_dispatch(intent())

        mismatched = observation(frozen)
        mismatched["request_sha256"] = sha("different request")
        with self.assertRaisesRegex(native.NativeDispatchError, "cross-bound"):
            native.record_native_observation(frozen, mismatched)

        timed_out = native.record_native_observation(
            frozen,
            observation(frozen, terminal_status="timed-out", usable=False),
        )
        self.assertEqual(timed_out.terminal_status, "timed-out")
        self.assertFalse(timed_out.usable)

    def test_record_requires_host_shaped_launch_status_and_result_observations(
        self,
    ) -> None:
        native = load_module()
        frozen = native.freeze_native_dispatch(intent("codex-cli-tui"))

        for field in (
            "agent_id",
            "session_id",
            "launch_acknowledgement_sha256",
            "status_observations",
            "result_sha256",
            "verification_observation_sha256",
            "usable",
        ):
            with self.subTest(field=field):
                incomplete = observation(frozen)
                del incomplete[field]
                with self.assertRaisesRegex(
                    native.NativeDispatchError, "observation schema drift"
                ):
                    native.record_native_observation(frozen, incomplete)

    def test_completed_transport_can_remain_blocked_or_unverified(self) -> None:
        native = load_module()
        frozen = native.freeze_native_dispatch(intent())

        for outcome in ("completed-but-blocked", "completed-but-unverified"):
            with self.subTest(outcome=outcome):
                recorded = native.record_native_observation(
                    frozen,
                    observation(frozen, usable=False, verification=outcome),
                )
                self.assertEqual(recorded.terminal_status, "completed")
                self.assertEqual(
                    recorded.verification_observation_sha256,
                    sha(outcome),
                )
                self.assertFalse(recorded.usable)

    def test_record_rejects_malformed_or_nonboolean_usability(self) -> None:
        native = load_module()
        frozen = native.freeze_native_dispatch(intent())

        malformed = observation(frozen)
        malformed["verification_observation_sha256"] = "not-a-digest"
        with self.assertRaisesRegex(
            native.NativeDispatchError,
            "verification_observation_sha256 must be a SHA-256 digest",
        ):
            native.record_native_observation(frozen, malformed)

        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value):
                nonboolean = observation(frozen, usable=value)
                with self.assertRaisesRegex(
                    native.NativeDispatchError,
                    "usable must be a strict Boolean",
                ):
                    native.record_native_observation(frozen, nonboolean)

        failed_but_usable = observation(
            frozen,
            terminal_status="failed",
            usable=True,
            verification="failed-but-claimed-usable",
        )
        with self.assertRaisesRegex(
            native.NativeDispatchError,
            "non-completed native result cannot be usable",
        ):
            native.record_native_observation(frozen, failed_but_usable)


if __name__ == "__main__":
    unittest.main()
