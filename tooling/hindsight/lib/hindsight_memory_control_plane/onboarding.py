"""One-decision-at-a-time, content-free Hindsight onboarding state."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import re
from typing import Any, Callable, Mapping

from .canonical import digest
from .model import deep_freeze, deep_thaw


class OnboardingError(ValueError):
    pass


ONBOARDING_TOPICS = (
    "machine_archetype", "profiles", "providers", "credentials", "banks",
    "harnesses", "models", "activation", "import",
)
RATIONALE_CODES = frozenset({
    "accepted-recommendation",
    "environment-constraint",
    "operator-preference",
    "policy-requirement",
    "prerequisite-unavailable",
})


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    description: str
    operator_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    topic: str
    header: str
    question: str
    choices: tuple[Choice, ...]

    def widget_request(self) -> dict[str, Any]:
        return {
            "questions": [
                {
                    "header": self.header,
                    "id": self.topic,
                    "question": self.question,
                    "options": [
                        {"label": choice.label, "description": choice.description}
                        for choice in self.choices
                    ],
                }
            ]
        }

    def plain_prompt(self) -> str:
        options = "\n".join(f"- {choice.label}: {choice.description}" for choice in self.choices)
        return f"{self.question}\n\n{options}"


def _choices(*values: tuple[str, str, str, tuple[str, ...] | None]) -> tuple[Choice, ...]:
    return tuple(Choice(identifier, label, description, actions or ()) for identifier, label, description, actions in values)


DECISIONS = {
    "machine_archetype": Decision("machine_archetype", "Machine", "Which machine archetype should this installation use?", _choices(
        ("balanced-local", "Balanced local (Recommended)", "Prefer on-device services with explicit remote fallbacks.", None),
        ("remote-first", "Remote first", "Prefer approved remote providers to reduce local resource use.", None),
        ("local-only", "Local only", "Forbid third-party hosted providers.", None),
    )),
    "profiles": Decision("profiles", "Profiles", "Which runtime profile layout should be planned?", _choices(
        ("single-engineering", "Single engineering (Recommended)", "Use one ordinary engineering runtime profile.", None),
        ("engineering-personal", "Engineering and personal", "Separate engineering and personal runtime profiles.", None),
        ("disabled", "No profiles", "Leave Hindsight desired state disabled.", None),
    )),
    "providers": Decision("providers", "Providers", "Which provider posture should be planned?", _choices(
        ("current-compatible", "Current compatible (Recommended)", "Keep currently verified provider role bindings.", None),
        ("local-providers", "Local providers", "Plan only locally hosted inference roles.", None),
        ("remote-providers", "Remote providers", "Plan approved remote role bindings.", None),
        ("defer-providers", "Defer providers", "Plan no provider bindings while runtime profiles are disabled.", None),
    )),
    "credentials": Decision("credentials", "Credentials", "How should missing provider credentials be established?", _choices(
        ("official-login", "Official login (Recommended)", "Return the provider's official interactive login as an operator action.", ("Run the provider's official interactive login flow.",)),
        ("existing-locators", "Existing locators", "Use configured credential locators without reading or persisting values.", None),
        ("defer-credentials", "Defer credentials", "Keep dependent providers blocked.", None),
    )),
    "banks": Decision("banks", "Banks", "Which bank posture should be planned?", _choices(
        ("engineering-authority", "Engineering authority (Recommended)", "Plan one authoritative engineering write bank.", None),
        ("engineering-personal-banks", "Engineering and personal", "Add an explicit personal bank beside engineering.", None),
        ("no-banks", "No banks", "Leave all bank materialization disabled.", None),
    )),
    "harnesses": Decision("harnesses", "Harnesses", "Which harness bindings should be rendered?", _choices(
        ("codex-claude-cursor", "Codex, Claude, Cursor (Recommended)", "Render inactive bindings for the three supported harnesses.", None),
        ("codex-only", "Codex only", "Render only the Codex binding.", None),
        ("no-harnesses", "No harnesses", "Render no harness bindings.", None),
    )),
    "models": Decision("models", "Models", "Which model roster should be planned?", _choices(
        ("verified-current", "Verified current (Recommended)", "Keep the verified current roster and block ungated candidates.", None),
        ("minimal-roster", "Minimal roster", "Plan only required profile models.", None),
        ("defer-models", "Defer models", "Leave model installation and activation blocked.", None),
    )),
    "activation": Decision("activation", "Activation", "When should rendered bindings be activated?", _choices(
        ("plan-only", "Plan only (Recommended)", "Render inactive artifacts and require a later exact approval.", None),
        ("defer-activation", "Defer activation", "Do not create an activation proposal yet.", None),
    )),
    "import": Decision("import", "Import", "Which prior-memory import should be projected?", _choices(
        ("inspect-curated", "Inspect curated sources (Recommended)", "Project curated Codex, Claude, and portable sources without applying.", None),
        ("portable-only", "Portable only", "Inspect only portable Markdown or JSONL manifests.", None),
        ("skip-import", "Skip import", "Do not create an import projection.", None),
    )),
}

DISABLED_PROFILE_SELECTIONS = {
    "providers": "defer-providers",
    "credentials": "defer-credentials",
    "models": "defer-models",
    "import": "skip-import",
}


def _validate_selection_compatibility(
    selections: Mapping[str, str],
    *,
    require_complete: bool = False,
) -> None:
    profiles = selections.get("profiles")
    machine_archetype = selections.get("machine_archetype")
    providers = selections.get("providers")
    banks = selections.get("banks")
    harnesses = selections.get("harnesses")
    activation = selections.get("activation")
    if profiles == "disabled":
        for topic, deferred in DISABLED_PROFILE_SELECTIONS.items():
            if selections.get(topic) not in {None, deferred}:
                raise OnboardingError(
                    f"profiles=disabled requires {topic}={deferred}"
                )
    if (
        machine_archetype == "local-only"
        and providers not in {None, "local-providers"}
        and not (
            profiles == "disabled" and providers == "defer-providers"
        )
    ):
        raise OnboardingError(
            "machine_archetype=local-only requires providers=local-providers"
        )
    if profiles == "disabled" and banks not in {None, "no-banks"}:
        raise OnboardingError(
            "profiles=disabled requires banks=no-banks"
        )
    if profiles == "disabled" and harnesses not in {None, "no-harnesses"}:
        raise OnboardingError(
            "profiles=disabled requires harnesses=no-harnesses"
        )
    if banks == "no-banks" and (
        harnesses not in {None, "no-harnesses"}
        or (require_complete and harnesses != "no-harnesses")
    ):
        raise OnboardingError(
            "banks=no-banks requires harnesses=no-harnesses"
        )
    if (
        profiles == "disabled" or harnesses == "no-harnesses"
    ) and activation not in {None, "defer-activation"}:
        raise OnboardingError(
            "disabled profiles or harnesses require activation=defer-activation"
        )


@dataclass(frozen=True)
class OnboardingSession:
    selections: tuple[tuple[str, str], ...] = ()
    decision_log: tuple[Mapping[str, str], ...] = ()
    operator_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_log", tuple(deep_freeze(value) for value in self.decision_log))

    @property
    def desired_state(self) -> dict[str, str]:
        return dict(self.selections)

    def next_decision(self) -> Decision | None:
        selections = dict(self.selections)
        selected = set(selections)
        decision = next(
            (
                DECISIONS[topic]
                for topic in ONBOARDING_TOPICS
                if topic not in selected
            ),
            None,
        )
        if decision is None:
            return None
        compatible: list[Choice] = []
        for choice in decision.choices:
            candidate = {**selections, decision.topic: choice.id}
            try:
                _validate_selection_compatibility(candidate)
            except OnboardingError:
                continue
            compatible.append(choice)
        if not compatible:
            raise OnboardingError(
                f"no compatible choices remain for {decision.topic}"
            )
        deferred_id = (
            DISABLED_PROFILE_SELECTIONS.get(decision.topic)
            if selections.get("profiles") == "disabled"
            else None
        )
        if deferred_id is not None:
            compatible = [
                Choice(
                    choice.id,
                    f"{choice.label} (Recommended)",
                    choice.description,
                    choice.operator_actions,
                )
                if choice.id == deferred_id
                and not choice.label.endswith("(Recommended)")
                else choice
                for choice in compatible
            ]
        return Decision(
            decision.topic,
            decision.header,
            decision.question,
            tuple(compatible),
        )

    def record(self, choice_id: str, *, rationale_code: str) -> "OnboardingSession":
        decision = self.next_decision()
        if decision is None:
            raise OnboardingError("onboarding is already complete")
        choice = next((value for value in decision.choices if value.id == choice_id), None)
        if choice is None:
            raise OnboardingError("choice is not valid for the current decision")
        if not isinstance(rationale_code, str) or rationale_code not in RATIONALE_CODES:
            raise OnboardingError("rationale code must be an approved content-free identifier")
        entry = {"topic": decision.topic, "choice_id": choice.id, "rationale_code": rationale_code}
        selections = self.selections + ((decision.topic, choice.id),)
        _validate_selection_compatibility(dict(selections))
        return OnboardingSession(
            selections,
            self.decision_log + (entry,),
            self.operator_actions + choice.operator_actions,
        )


@dataclass(frozen=True)
class OnboardingPlan:
    schema_version: int
    desired_state: Mapping[str, str]
    decision_log: tuple[Mapping[str, str], ...]
    operator_actions: tuple[str, ...]
    controller_plan_digest: str
    plan_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "desired_state", deep_freeze(self.desired_state))
        object.__setattr__(self, "decision_log", tuple(deep_freeze(value) for value in self.decision_log))

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "desired_state": deep_thaw(self.desired_state),
            "decision_log": [deep_thaw(value) for value in self.decision_log],
            "operator_actions": list(self.operator_actions),
            "controller_plan_digest": self.controller_plan_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "plan_digest": self.plan_digest}


def _validate_complete_session(session: OnboardingSession) -> None:
    if not isinstance(session, OnboardingSession):
        raise OnboardingError("onboarding plan requires a complete validated session")
    if len(session.selections) != len(ONBOARDING_TOPICS):
        raise OnboardingError("onboarding plan requires every decision")
    if len(session.decision_log) != len(ONBOARDING_TOPICS):
        raise OnboardingError("onboarding decision log is incomplete")

    expected_actions: list[str] = []
    for index, topic in enumerate(ONBOARDING_TOPICS):
        selection = session.selections[index]
        if (
            not isinstance(selection, tuple)
            or len(selection) != 2
            or selection[0] != topic
        ):
            raise OnboardingError("onboarding selections are not canonical")
        choice = next(
            (value for value in DECISIONS[topic].choices if value.id == selection[1]),
            None,
        )
        if choice is None:
            raise OnboardingError("onboarding selection is invalid")
        entry = session.decision_log[index]
        if not isinstance(entry, Mapping) or set(entry) != {
            "topic",
            "choice_id",
            "rationale_code",
        }:
            raise OnboardingError("onboarding decision log is invalid")
        if entry["topic"] != topic or entry["choice_id"] != choice.id:
            raise OnboardingError("onboarding decision log does not match selections")
        if (
            not isinstance(entry["rationale_code"], str)
            or entry["rationale_code"] not in RATIONALE_CODES
        ):
            raise OnboardingError("onboarding rationale code is invalid")
        expected_actions.extend(choice.operator_actions)
    if tuple(expected_actions) != session.operator_actions:
        raise OnboardingError("onboarding operator actions do not match selections")
    _validate_selection_compatibility(
        dict(session.selections), require_complete=True
    )


def build_onboarding_plan(session: OnboardingSession, *, controller_plan_digest: str) -> OnboardingPlan:
    _validate_complete_session(session)
    if not isinstance(controller_plan_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", controller_plan_digest
    ):
        raise OnboardingError(
            "controller plan digest must be a lowercase SHA-256 digest"
        )
    body = {
        "schema_version": 1,
        "desired_state": session.desired_state,
        "decision_log": [deep_thaw(value) for value in session.decision_log],
        "operator_actions": list(session.operator_actions),
        "controller_plan_digest": controller_plan_digest,
    }
    return OnboardingPlan(1, session.desired_state, session.decision_log, session.operator_actions, controller_plan_digest, digest(body))


def apply_onboarding_plan(
    plan: OnboardingPlan,
    *,
    approved_plan_digest: str | None,
    controller_apply: Callable[[dict[str, Any]], Any],
) -> str:
    if (
        not isinstance(plan, OnboardingPlan)
        or type(plan.schema_version) is not int
        or plan.schema_version != 1
    ):
        raise OnboardingError("onboarding plan schema is invalid")
    if not isinstance(plan.desired_state, Mapping):
        raise OnboardingError("onboarding desired state is invalid")
    if set(plan.desired_state) != set(ONBOARDING_TOPICS):
        raise OnboardingError(
            "onboarding desired state must contain the exact topic key set"
        )
    canonical_desired_state = {
        topic: plan.desired_state[topic] for topic in ONBOARDING_TOPICS
    }
    _validate_complete_session(
        OnboardingSession(
            selections=tuple(canonical_desired_state.items()),
            decision_log=plan.decision_log,
            operator_actions=plan.operator_actions,
        )
    )
    if not isinstance(plan.controller_plan_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", plan.controller_plan_digest
    ):
        raise OnboardingError("controller plan digest must be a lowercase SHA-256 digest")
    if not isinstance(plan.plan_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", plan.plan_digest
    ):
        raise OnboardingError(
            "onboarding plan digest must be a lowercase SHA-256 digest"
        )
    computed_plan_digest = digest(plan.body())
    if (
        not hmac.compare_digest(computed_plan_digest, plan.plan_digest)
        or not isinstance(approved_plan_digest, str)
        or not hmac.compare_digest(approved_plan_digest, computed_plan_digest)
    ):
        raise OnboardingError("exact digest-bound onboarding plan approval is required")
    controller_apply(
        {
            **plan.to_dict(),
            "desired_state": canonical_desired_state,
        }
    )
    return computed_plan_digest
