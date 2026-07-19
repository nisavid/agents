from dataclasses import replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.onboarding import (
    ONBOARDING_TOPICS,
    OnboardingError,
    OnboardingSession,
    apply_onboarding_plan,
    build_onboarding_plan,
)
from hindsight_memory_control_plane.canonical import digest


CONTROLLER_PLAN = {"schema_version": 1, "actions": []}


def complete_session():
    session = OnboardingSession()
    while (decision := session.next_decision()) is not None:
        session = session.record(
            decision.choices[0].id,
            rationale_code="accepted-recommendation",
        )
    return session


class OnboardingTest(unittest.TestCase):
    def test_covers_every_required_topic_one_decision_at_a_time(self):
        self.assertEqual(ONBOARDING_TOPICS, ("machine_archetype", "profiles", "providers", "credentials", "banks", "harnesses", "models", "activation", "import"))
        session = OnboardingSession()
        seen = []
        while (decision := session.next_decision()) is not None:
            seen.append(decision.topic)
            session = session.record(decision.choices[0].id, rationale_code="accepted-recommendation")
        self.assertEqual(tuple(seen), ONBOARDING_TOPICS)

    def test_each_decision_has_two_to_four_exclusive_choices_recommendation_first(self):
        session = OnboardingSession()
        for topic in ONBOARDING_TOPICS:
            decision = session.next_decision()
            self.assertEqual(decision.topic, topic)
            self.assertGreaterEqual(len(decision.choices), 2)
            self.assertLessEqual(len(decision.choices), 4)
            self.assertTrue(decision.choices[0].label.endswith("(Recommended)"))
            self.assertEqual(len({choice.id for choice in decision.choices}), len(decision.choices))
            session = session.record(decision.choices[0].id, rationale_code="accepted-recommendation")

    def test_widget_request_omits_timeout_and_plain_prompt_is_complete(self):
        decision = OnboardingSession().next_decision()
        widget = decision.widget_request()
        self.assertNotIn("autoResolutionMs", widget)
        self.assertNotIn("timeout", widget)
        prompt = decision.plain_prompt()
        self.assertIn(decision.question, prompt)
        for choice in decision.choices:
            self.assertIn(choice.label, prompt)

    def test_record_is_content_free_and_persists_only_non_secret_choice_ids(self):
        session = OnboardingSession()
        decision = session.next_decision()
        session = session.record(decision.choices[0].id, rationale_code="accepted-recommendation")
        entry = session.decision_log[0]
        self.assertEqual(set(entry), {"topic", "choice_id", "rationale_code"})
        self.assertNotIn(decision.question, str(entry))
        self.assertEqual(session.desired_state, {"machine_archetype": decision.choices[0].id})
        with self.assertRaises(OnboardingError):
            session.record("token:secret-value", rationale_code="manual")

    def test_credentials_return_official_operator_action_without_secret_state(self):
        session = OnboardingSession()
        while session.next_decision().topic != "credentials":
            session = session.record(session.next_decision().choices[0].id, rationale_code="accepted-recommendation")
        decision = session.next_decision()
        login = next(choice for choice in decision.choices if choice.operator_actions)
        session = session.record(login.id, rationale_code="operator-preference")
        self.assertEqual(session.desired_state["credentials"], login.id)
        self.assertEqual(session.operator_actions, login.operator_actions)
        self.assertNotIn("token", str(session.desired_state).lower())

    def test_invalid_out_of_order_or_unknown_choice_fails_closed(self):
        session = OnboardingSession()
        with self.assertRaises(OnboardingError):
            session.record("unknown", rationale_code="operator-preference")
        advanced = session.record(
            session.next_decision().choices[0].id,
            rationale_code="operator-preference",
        )
        future_choice = advanced.next_decision().choices[0].id
        with self.assertRaises(OnboardingError):
            session.record(future_choice, rationale_code="operator-preference")
        with self.assertRaises(OnboardingError):
            session.record(session.next_decision().choices[0].id, rationale_code="contains secret gho_example")
        with self.assertRaises(OnboardingError):
            session.record(session.next_decision().choices[0].id, rationale_code="manual")
        with self.assertRaisesRegex(OnboardingError, "rationale code"):
            session.record(
                session.next_decision().choices[0].id,
                rationale_code={"accepted-recommendation": True},
            )

    def test_plan_and_apply_use_controller_digest_gate(self):
        session = complete_session()
        plan = build_onboarding_plan(
            session, controller_plan=CONTROLLER_PLAN
        )
        calls = []
        with self.assertRaises(OnboardingError):
            apply_onboarding_plan(
                plan,
                approved_plan_digest=None,
                controller_plan=CONTROLLER_PLAN,
                controller_apply=calls.append,
            )
        with self.assertRaises(OnboardingError):
            apply_onboarding_plan(
                plan,
                approved_plan_digest="d" * 64,
                controller_plan=CONTROLLER_PLAN,
                controller_apply=calls.append,
            )
        self.assertEqual(calls, [])
        apply_onboarding_plan(
            plan,
            approved_plan_digest=plan.plan_digest,
            controller_plan=CONTROLLER_PLAN,
            controller_apply=calls.append,
        )
        self.assertEqual(calls, [CONTROLLER_PLAN])

    def test_cross_topic_incompatibilities_fail_with_actionable_choices(self):
        def record_until(session, topic, choices):
            while session.next_decision().topic != topic:
                decision = session.next_decision()
                choice_id = choices.get(
                    decision.topic, decision.choices[0].id
                )
                session = session.record(
                    choice_id,
                    rationale_code="operator-preference",
                )
            return session

        cases = (
            (
                {"machine_archetype": "local-only"},
                "providers",
                "current-compatible",
                "choice is not valid",
            ),
            (
                {"machine_archetype": "local-only"},
                "providers",
                "remote-providers",
                "choice is not valid",
            ),
            (
                {"profiles": "disabled"},
                "banks",
                "engineering-authority",
                "choice is not valid",
            ),
            (
                {"profiles": "disabled", "banks": "no-banks"},
                "harnesses",
                "codex-only",
                "choice is not valid",
            ),
            (
                {"banks": "no-banks"},
                "harnesses",
                "codex-only",
                "choice is not valid",
            ),
            (
                {
                    "profiles": "disabled",
                    "banks": "no-banks",
                    "harnesses": "no-harnesses",
                },
                "activation",
                "plan-only",
                "choice is not valid",
            ),
        )
        for choices, topic, choice_id, message in cases:
            session = record_until(OnboardingSession(), topic, choices)
            with self.subTest(choices=choices), self.assertRaisesRegex(
                OnboardingError, message
            ):
                session.record(
                    choice_id, rationale_code="operator-preference"
                )

        local_only = record_until(
            OnboardingSession(),
            "providers",
            {"machine_archetype": "local-only"},
        )
        local_only.record(
            "local-providers", rationale_code="operator-preference"
        )
        self.assertEqual(
            tuple(choice.id for choice in local_only.next_decision().choices),
            ("local-providers",),
        )

    def test_disabled_profiles_expose_only_deferred_dependent_choices(self):
        session = OnboardingSession()
        session = session.record(
            "local-only", rationale_code="operator-preference"
        )
        session = session.record(
            "disabled", rationale_code="operator-preference"
        )
        expected = {
            "providers": "defer-providers",
            "credentials": "defer-credentials",
            "models": "defer-models",
            "import": "skip-import",
        }
        while (decision := session.next_decision()) is not None:
            if decision.topic in expected:
                self.assertEqual(
                    tuple(choice.id for choice in decision.choices),
                    (expected[decision.topic],),
                )
            choice = decision.choices[0].id
            if decision.topic == "banks":
                choice = "no-banks"
            elif decision.topic == "harnesses":
                choice = "no-harnesses"
            elif decision.topic == "activation":
                choice = "defer-activation"
            session = session.record(
                choice, rationale_code="operator-preference"
            )
        self.assertEqual(session.operator_actions, ())
        for topic, choice in expected.items():
            self.assertEqual(session.desired_state[topic], choice)


    def test_plan_rejects_non_mapping_controller_payload(self):
        for value in (None, 1, b"payload"):
            with self.subTest(value=value), self.assertRaisesRegex(
                OnboardingError, "controller plan payload"
            ):
                build_onboarding_plan(
                    complete_session(), controller_plan=value
                )

        with self.assertRaisesRegex(OnboardingError, "not canonical"):
            build_onboarding_plan(
                complete_session(),
                controller_plan={"unsafe_number": 10**1000},
            )

    def test_apply_recomputes_digest_before_mutation(self):
        session = complete_session()
        plan = build_onboarding_plan(
            session, controller_plan=CONTROLLER_PLAN
        )
        tampered = replace(plan, controller_plan_digest="d" * 64)
        calls = []
        with self.assertRaises(OnboardingError):
            apply_onboarding_plan(
                tampered,
                approved_plan_digest=plan.plan_digest,
                controller_plan=CONTROLLER_PLAN,
                controller_apply=calls.append,
            )
        self.assertEqual(calls, [])

    def test_apply_rejects_a_different_controller_plan_payload(self):
        plan = build_onboarding_plan(
            complete_session(), controller_plan=CONTROLLER_PLAN
        )
        calls = []
        with self.assertRaisesRegex(OnboardingError, "not bound"):
            apply_onboarding_plan(
                plan,
                approved_plan_digest=plan.plan_digest,
                controller_plan={"schema_version": 1, "actions": ["changed"]},
                controller_apply=calls.append,
            )
        self.assertEqual(calls, [])

    def test_apply_rejects_boolean_schema_version_before_mutation(self):
        plan = build_onboarding_plan(
            complete_session(), controller_plan=CONTROLLER_PLAN
        )
        forged = replace(plan, schema_version=True)
        forged = replace(forged, plan_digest=digest(forged.body()))
        calls = []
        with self.assertRaisesRegex(OnboardingError, "schema"):
            apply_onboarding_plan(
                forged,
                approved_plan_digest=forged.plan_digest,
                controller_plan=CONTROLLER_PLAN,
                controller_apply=calls.append,
            )
        self.assertEqual(calls, [])

    def test_apply_revalidates_directly_constructed_plan_content(self):
        plan = build_onboarding_plan(
            complete_session(), controller_plan=CONTROLLER_PLAN
        )
        desired_state = dict(plan.desired_state)
        desired_state["profiles"] = "not-a-declared-choice"
        forged = replace(plan, desired_state=desired_state)
        forged = replace(forged, plan_digest=digest(forged.body()))
        calls = []
        with self.assertRaises(OnboardingError):
            apply_onboarding_plan(
                forged,
                approved_plan_digest=forged.plan_digest,
                controller_plan=CONTROLLER_PLAN,
                controller_apply=calls.append,
            )
        self.assertEqual(calls, [])

    def test_apply_reconstructs_canonical_desired_state_order(self):
        plan = build_onboarding_plan(
            complete_session(), controller_plan=CONTROLLER_PLAN
        )
        forged = replace(
            plan,
            desired_state=dict(reversed(tuple(plan.desired_state.items()))),
        )
        forged = replace(forged, plan_digest=digest(forged.body()))
        calls = []
        apply_onboarding_plan(
            forged,
            approved_plan_digest=forged.plan_digest,
            controller_plan=CONTROLLER_PLAN,
            controller_apply=calls.append,
        )
        self.assertEqual(calls, [CONTROLLER_PLAN])

    def test_apply_rejects_missing_or_extra_desired_state_topics(self):
        plan = build_onboarding_plan(
            complete_session(), controller_plan=CONTROLLER_PLAN
        )
        for desired_state in (
            {key: value for key, value in plan.desired_state.items() if key != "import"},
            {**dict(plan.desired_state), "extra": "choice"},
        ):
            forged = replace(plan, desired_state=desired_state)
            forged = replace(forged, plan_digest=digest(forged.body()))
            with self.assertRaisesRegex(OnboardingError, "exact topic key set"):
                apply_onboarding_plan(
                    forged,
                    approved_plan_digest=forged.plan_digest,
                    controller_plan=CONTROLLER_PLAN,
                    controller_apply=lambda _plan: None,
                )

    def test_plan_rejects_incomplete_or_forged_sessions(self):
        with self.assertRaisesRegex(OnboardingError, "every decision"):
            build_onboarding_plan(
                OnboardingSession(), controller_plan=CONTROLLER_PLAN
            )

        complete = complete_session()
        forged_cases = (
            replace(
                complete,
                selections=(
                    ("profiles", complete.selections[0][1]),
                    *complete.selections[1:],
                ),
            ),
            replace(
                complete,
                decision_log=(
                    {
                        **complete.decision_log[0],
                        "choice_id": "remote-first",
                    },
                    *complete.decision_log[1:],
                ),
            ),
            replace(
                complete,
                decision_log=(
                    {
                        **complete.decision_log[0],
                        "rationale_code": {"forged": "mapping"},
                    },
                    *complete.decision_log[1:],
                ),
            ),
            replace(complete, operator_actions=("unselected action",)),
        )
        for session in forged_cases:
            with self.subTest(session=session), self.assertRaises(OnboardingError):
                build_onboarding_plan(
                    session, controller_plan=CONTROLLER_PLAN
                )


if __name__ == "__main__":
    unittest.main()
