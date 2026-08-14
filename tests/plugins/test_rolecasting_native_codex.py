from __future__ import annotations

import hashlib
import importlib.util
import unittest
from pathlib import Path
from typing import Any

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
    spec.loader.exec_module(module)
    return module


def intent(surface: str = "chatgpt-codex") -> dict[str, Any]:
    return {
        "plan_sha256": sha("complete frozen dispatch plan"),
        "request_sha256": sha("bounded worker request"),
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
