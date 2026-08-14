"""Captured-endpoint execution for a reviewed Git publication plan."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any, IO

from .adapter import (
    GitRepository,
    MalformedRequest,
    PolicyGate,
    SHA_RE,
    _config_digest,
    _endpoint,
    _ensure_commits,
    _bind_request_object_format,
    _guard_repository,
    _object_format,
    _probe_default_branch,
    _probe_ref,
    _resolve_destination,
    _run_endpoint,
    parse_request,
)
from .core import planner_effects, request_document


PLAN_KEYS = {
    "schema_version",
    "status",
    "request",
    "planner_effects",
    "reasons",
    "source_sha",
    "destination",
    "target",
    "outgoing_shas",
    "target_only_shas",
    "fast_forward",
    "rewrite_required",
    "push",
    "postchecks",
}
REVIEWED_PLAN_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class MalformedPlan(ValueError):
    """The reviewed plan is not an executable planner result."""


class ReviewedPlanDigestMismatch(MalformedPlan):
    """The exact plan bytes differ from the separately reviewed digest."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise MalformedPlan(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise MalformedPlan(f"non-finite JSON value: {value}")


def _decode_plan_json(document: str) -> Any:
    return json.loads(
        document,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


def load_plan_json(stream: IO[str]) -> Any:
    """Decode strict reviewed-plan JSON."""
    return _decode_plan_json(stream.read())


def _load_reviewed_plan(plan_bytes: bytes, reviewed_plan_sha256: str) -> Any:
    """Verify exact reviewed bytes before decoding their plan document."""
    if not isinstance(plan_bytes, bytes):
        raise MalformedPlan("reviewed plan must be supplied as exact bytes")
    if (
        not isinstance(reviewed_plan_sha256, str)
        or REVIEWED_PLAN_DIGEST_RE.fullmatch(reviewed_plan_sha256) is None
    ):
        raise MalformedPlan("reviewed plan digest must be sha256:<64 lowercase hex>")
    observed = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
    if not hmac.compare_digest(observed, reviewed_plan_sha256):
        raise ReviewedPlanDigestMismatch(
            "exact reviewed plan bytes do not match digest"
        )
    try:
        document = plan_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MalformedPlan("reviewed plan is not UTF-8") from error
    return _decode_plan_json(document)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MalformedPlan(message)


def validate_ready_plan(raw: Any) -> tuple[dict, Any]:
    """Validate and return a ready plan plus its normalized request."""
    _require(isinstance(raw, dict) and set(raw) == PLAN_KEYS, "plan fields differ")
    _require(
        type(raw["schema_version"]) is int and raw["schema_version"] == 1,
        "plan schema_version must be integer 1",
    )
    _require(raw["status"] == "ready", "plan status must be ready")
    _require(raw["reasons"] == [], "ready plan must not contain reasons")
    _require(raw["planner_effects"] == planner_effects(), "planner effects drift")
    try:
        request = parse_request(raw["request"])
    except MalformedRequest as error:
        raise MalformedPlan(f"embedded request is invalid: {error}") from error
    _require(raw["request"] == request_document(request), "request normalization drift")
    _require(raw["source_sha"] == request.source_sha, "source SHA drift")

    destination = raw["destination"]
    _require(
        isinstance(destination, dict)
        and set(destination)
        == {
            "remote",
            "ref",
            "endpoint_fingerprint",
            "config_digest",
            "default_branch_ref",
        },
        "destination fields differ",
    )
    _require(
        all(
            isinstance(destination[field], str) and destination[field]
            for field in destination
        ),
        "destination values must be nonempty strings",
    )
    policy = request.default_branch_policy
    _require(
        policy is None or policy["ref"] == destination["default_branch_ref"],
        "default branch policy ref drift",
    )
    if destination["ref"] == destination["default_branch_ref"]:
        _require(
            policy is not None and policy["direct_push_permitted"],
            "default branch direct push permission is not verified",
        )
    target = raw["target"]
    _require(
        isinstance(target, dict) and set(target) == {"present", "sha"},
        "target fields differ",
    )
    _require(type(target["present"]) is bool, "target present must be boolean")
    _require(
        (
            target["present"]
            and isinstance(target["sha"], str)
            and SHA_RE.fullmatch(target["sha"]) is not None
        )
        or (not target["present"] and target["sha"] is None),
        "target SHA does not match presence",
    )
    for field in ("outgoing_shas", "target_only_shas"):
        values = raw[field]
        _require(
            isinstance(values, list)
            and all(
                isinstance(value, str) and SHA_RE.fullmatch(value) for value in values
            )
            and len(values) == len(set(values)),
            f"{field} must contain unique full SHAs",
        )
    _require(type(raw["fast_forward"]) is bool, "fast_forward must be boolean")
    _require(type(raw["rewrite_required"]) is bool, "rewrite_required must be boolean")

    push = raw["push"]
    _require(
        isinstance(push, dict)
        and set(push)
        == {
            "source_sha",
            "ref",
            "refspec",
            "lease",
            "options",
            "config_overrides",
            "expected_target",
        },
        "push fields differ",
    )
    expected_lease = (
        f"--force-with-lease={destination['ref']}:{target['sha']}"
        if target["present"]
        else f"--force-with-lease={destination['ref']}:"
    )
    _require(push["source_sha"] == request.source_sha, "push source SHA drift")
    _require(push["ref"] == destination["ref"], "push ref drift")
    _require(
        push["refspec"] == f"{request.source_sha}:{destination['ref']}",
        "push refspec drift",
    )
    _require(push["lease"] == expected_lease, "push lease drift")
    _require(
        push["options"] == ["--no-follow-tags", "--recurse-submodules=check"],
        "push options drift",
    )
    _require(
        push["config_overrides"]
        == {"push.followTags": "false", "push.recurseSubmodules": "check"},
        "push config overrides drift",
    )
    _require(push["expected_target"] == target, "push target drift")
    _require(
        raw["postchecks"]
        == [
            {
                "kind": "remote_ref_equals",
                "endpoint_fingerprint": destination["endpoint_fingerprint"],
                "ref": destination["ref"],
                "sha": request.source_sha,
            }
        ],
        "postcheck drift",
    )
    return raw, request


def _blocked(plan: dict, gate: PolicyGate, *, push_attempted: bool) -> dict:
    return {
        "schema_version": 1,
        "status": "blocked",
        "source_sha": plan["source_sha"],
        "destination": plan["destination"],
        "push_attempted": push_attempted,
        "reasons": [{"code": gate.code, "evidence": gate.evidence}],
        "postchecks": [],
    }


def _post_push_unknown(plan: dict, request: Any, failure_type: str) -> dict:
    gate = PolicyGate(
        "POST_PUSH_STATE_UNKNOWN",
        failure_type=failure_type,
        ref=plan["destination"]["ref"],
        expected_sha=request.source_sha,
        endpoint_fingerprint=plan["destination"]["endpoint_fingerprint"],
        reconciliation=(
            "Re-probe the exact captured endpoint and ref before deciding "
            "whether any retry is safe."
        ),
    )
    return _blocked(plan, gate, push_attempted=True)


def execute_repository(
    path: Path, plan_bytes: bytes, reviewed_plan_sha256: str
) -> dict:
    """Execute one reviewed plan against one captured endpoint without retry."""
    raw_plan = _load_reviewed_plan(plan_bytes, reviewed_plan_sha256)
    plan, request = validate_ready_plan(raw_plan)
    with GitRepository(Path(path)) as repo:
        push_attempted = False
        try:
            _guard_repository(repo)
            object_format = _object_format(repo)
            _bind_request_object_format(request, object_format)
            object_ids = [
                plan["source_sha"],
                *plan["outgoing_shas"],
                *plan["target_only_shas"],
            ]
            if plan["target"]["present"]:
                object_ids.append(plan["target"]["sha"])
            if not all(object_format.matches(object_id) for object_id in object_ids):
                raise PolicyGate(
                    "GIT_OBJECT_ID_FORMAT_MISMATCH",
                    object_format=object_format.name,
                )
            _ensure_commits(repo, request)
            remote, ref, selection = _resolve_destination(repo, request)
            endpoint, endpoint_fingerprint = _endpoint(repo, remote)
            destination = plan["destination"]
            if remote != destination["remote"] or ref != destination["ref"]:
                raise PolicyGate(
                    "REVIEWED_DESTINATION_CHANGED",
                    expected_remote=destination["remote"],
                    observed_remote=remote,
                    expected_ref=destination["ref"],
                    observed_ref=ref,
                )
            if endpoint_fingerprint != destination["endpoint_fingerprint"]:
                raise PolicyGate(
                    "REVIEWED_ENDPOINT_CHANGED",
                    expected_fingerprint=destination["endpoint_fingerprint"],
                    observed_fingerprint=endpoint_fingerprint,
                )
            default_branch_ref = _probe_default_branch(repo, endpoint, object_format)
            if default_branch_ref != destination["default_branch_ref"]:
                raise PolicyGate(
                    "REVIEWED_DEFAULT_BRANCH_CHANGED",
                    expected_ref=destination["default_branch_ref"],
                    observed_ref=default_branch_ref,
                )
            selection = dict(selection)
            selection["default_branch_ref"] = default_branch_ref
            config_digest = _config_digest(selection, endpoint_fingerprint)
            if config_digest != destination["config_digest"]:
                raise PolicyGate(
                    "REVIEWED_CONFIG_CHANGED",
                    expected_digest=destination["config_digest"],
                    observed_digest=config_digest,
                )

            observed_target = _probe_ref(repo, endpoint, ref, object_format)
            expected_target = plan["target"]["sha"]
            if observed_target != expected_target:
                raise PolicyGate(
                    "REVIEWED_LEASE_CHANGED",
                    expected_sha=expected_target,
                    observed_sha=observed_target,
                    ref=ref,
                )

            latest_default_branch_ref = _probe_default_branch(
                repo, endpoint, object_format
            )
            if latest_default_branch_ref != destination["default_branch_ref"]:
                raise PolicyGate(
                    "REVIEWED_DEFAULT_BRANCH_CHANGED",
                    expected_ref=destination["default_branch_ref"],
                    observed_ref=latest_default_branch_ref,
                )

            push = plan["push"]
            push_attempted = True
            result = _run_endpoint(
                repo,
                endpoint,
                [
                    "-c",
                    "push.followTags=false",
                    "-c",
                    "push.recurseSubmodules=check",
                    "push",
                    *push["options"],
                    push["lease"],
                ],
                [push["refspec"]],
            )
            if result.returncode != 0:
                raise PolicyGate("PUSH_FAILED", returncode=result.returncode)
            observed_after = _probe_ref(repo, endpoint, ref, object_format)
            if observed_after != request.source_sha:
                raise PolicyGate(
                    "POST_PUSH_REF_MISMATCH",
                    expected_sha=request.source_sha,
                    observed_sha=observed_after,
                    ref=ref,
                )
            return {
                "schema_version": 1,
                "status": "verified",
                "source_sha": request.source_sha,
                "destination": destination,
                "push_attempted": True,
                "reasons": [],
                "postchecks": plan["postchecks"],
            }
        except PolicyGate as gate:
            if push_attempted and gate.code != "POST_PUSH_REF_MISMATCH":
                return _post_push_unknown(plan, request, gate.code)
            return _blocked(plan, gate, push_attempted=push_attempted)
        except Exception as error:
            if not push_attempted:
                raise
            return _post_push_unknown(plan, request, type(error).__name__)
