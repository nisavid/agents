"""Separate, exact-lease remote-ref deletion planning and execution."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, IO

from .adapter import (
    GitRepository,
    MalformedRequest,
    PolicyGate,
    SHA_RE,
    _config_digest,
    _endpoint,
    _guard_repository,
    _object_format,
    _probe_ref,
    _run_endpoint,
    _validate_heads_ref,
    _validate_remote,
    _validate_sha,
)


DELETION_REQUEST_KEYS = {
    "schema_version",
    "remote",
    "ref",
    "expected_target_sha",
    "verified_merge",
    "authorization",
}
DELETION_PLAN_KEYS = {
    "schema_version",
    "status",
    "request",
    "planner_effects",
    "reasons",
    "destination",
    "target",
    "deletion",
    "postchecks",
}
DELETION_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class RemoteRefDeletionRequest:
    remote: str
    ref: str
    expected_target_sha: str
    verified_merge: dict
    authorization: dict


class MalformedDeletionPlan(ValueError):
    """The reviewed deletion plan is not an executable deletion plan."""


class ReviewedDeletionPlanDigestMismatch(MalformedDeletionPlan):
    """The exact deletion-plan bytes differ from the separately retained digest."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise MalformedDeletionPlan(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise MalformedDeletionPlan(f"non-finite JSON value: {value}")


def load_deletion_plan_json(stream: IO[str]) -> Any:
    """Decode strict reviewed deletion-plan JSON."""
    return json.loads(
        stream.read(),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MalformedDeletionPlan(message)


def _parse_verified_merge(value: Any, expected_target_sha: str) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "status",
        "merged_source_sha",
        "merged_result_sha",
    }:
        raise MalformedRequest("verified_merge fields differ")
    if value["status"] != "verified":
        raise MalformedRequest("verified_merge status must be verified")
    merged_source_sha = _validate_sha(
        value["merged_source_sha"], "verified_merge.merged_source_sha"
    )
    merged_result_sha = _validate_sha(
        value["merged_result_sha"], "verified_merge.merged_result_sha"
    )
    if merged_source_sha != expected_target_sha:
        raise MalformedRequest(
            "verified_merge merged_source_sha must equal expected_target_sha"
        )
    return {
        "status": "verified",
        "merged_source_sha": merged_source_sha,
        "merged_result_sha": merged_result_sha,
    }


def _parse_authorization(
    value: Any, remote: str, ref: str, expected_target_sha: str
) -> dict:
    if not isinstance(value, dict) or set(value) != {"repository", "operator"}:
        raise MalformedRequest("authorization must contain repository and operator")
    normalized = {}
    for authority in ("repository", "operator"):
        grant = value[authority]
        if not isinstance(grant, dict) or set(grant) != {
            "authorized",
            "remote",
            "ref",
            "expected_target_sha",
        }:
            raise MalformedRequest(f"authorization.{authority} fields differ")
        if grant["authorized"] is not True:
            raise MalformedRequest(f"authorization.{authority} must be explicit")
        grant_remote = _validate_remote(grant["remote"])
        grant_ref = _validate_heads_ref(grant["ref"], f"authorization.{authority}.ref")
        grant_sha = _validate_sha(
            grant["expected_target_sha"],
            f"authorization.{authority}.expected_target_sha",
        )
        if (grant_remote, grant_ref, grant_sha) != (remote, ref, expected_target_sha):
            raise MalformedRequest(
                f"authorization.{authority} must bind the exact remote, ref, and SHA"
            )
        normalized[authority] = {
            "authorized": True,
            "remote": remote,
            "ref": ref,
            "expected_target_sha": expected_target_sha,
        }
    return normalized


def parse_deletion_request(raw: Any) -> RemoteRefDeletionRequest:
    """Validate a deletion request with exact merge and authorization evidence."""
    if not isinstance(raw, dict) or set(raw) != DELETION_REQUEST_KEYS:
        raise MalformedRequest("deletion request fields differ")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise MalformedRequest("deletion request schema_version must equal 1")
    remote = _validate_remote(raw["remote"])
    ref = _validate_heads_ref(raw["ref"])
    expected_target_sha = _validate_sha(
        raw["expected_target_sha"], "expected_target_sha"
    )
    verified_merge = _parse_verified_merge(raw["verified_merge"], expected_target_sha)
    authorization = _parse_authorization(
        raw["authorization"], remote, ref, expected_target_sha
    )
    return RemoteRefDeletionRequest(
        remote=remote,
        ref=ref,
        expected_target_sha=expected_target_sha,
        verified_merge=verified_merge,
        authorization=authorization,
    )


def deletion_request_document(request: RemoteRefDeletionRequest) -> dict:
    return {
        "schema_version": 1,
        "remote": request.remote,
        "ref": request.ref,
        "expected_target_sha": request.expected_target_sha,
        "verified_merge": request.verified_merge,
        "authorization": request.authorization,
    }


def deletion_planner_effects() -> dict:
    return {
        "local_mutation_possible": False,
        "remote_probe_only": True,
    }


def _selection(repo: GitRepository, request: RemoteRefDeletionRequest) -> dict:
    remotes = repo.output(["remote"]).splitlines()
    if request.remote not in remotes:
        raise PolicyGate("DELETION_REMOTE_NOT_CONFIGURED", remote=request.remote)
    return {
        "selection": "explicit_remote_ref_deletion",
        "remote": request.remote,
        "ref": request.ref,
        "remote_push": repo.config_all(f"remote.{request.remote}.push"),
    }


def _destination(repo: GitRepository, request: RemoteRefDeletionRequest) -> dict:
    selection = _selection(repo, request)
    endpoint, endpoint_fingerprint = _endpoint(repo, request.remote)
    return {
        "remote": request.remote,
        "ref": request.ref,
        "endpoint_fingerprint": endpoint_fingerprint,
        "config_digest": _config_digest(selection, endpoint_fingerprint),
        "_endpoint": endpoint,
    }


def _bind_deletion_request_object_format(
    request: RemoteRefDeletionRequest, object_format: object
) -> None:
    object_ids = (
        request.expected_target_sha,
        request.verified_merge["merged_source_sha"],
        request.verified_merge["merged_result_sha"],
        request.authorization["repository"]["expected_target_sha"],
        request.authorization["operator"]["expected_target_sha"],
    )
    if not all(object_format.matches(object_id) for object_id in object_ids):
        raise PolicyGate(
            "GIT_OBJECT_ID_FORMAT_MISMATCH",
            object_format=object_format.name,
        )


def _base_plan(request: RemoteRefDeletionRequest, destination: dict | None) -> dict:
    public_destination = (
        None
        if destination is None
        else {key: destination[key] for key in destination if key != "_endpoint"}
    )
    return {
        "schema_version": 1,
        "status": "blocked",
        "request": deletion_request_document(request),
        "planner_effects": deletion_planner_effects(),
        "reasons": [],
        "destination": public_destination,
        "target": {"present": None, "sha": None},
        "deletion": None,
        "postchecks": [],
    }


def _blocked_plan(
    request: RemoteRefDeletionRequest, gate: PolicyGate, destination: dict | None
) -> dict:
    plan = _base_plan(request, destination)
    plan["reasons"] = [{"code": gate.code, "evidence": gate.evidence}]
    return plan


def plan_remote_ref_deletion(path: Path, raw_request: Any) -> dict:
    """Plan one separate, exact-lease remote-ref deletion or verified absence."""
    request = (
        raw_request
        if isinstance(raw_request, RemoteRefDeletionRequest)
        else parse_deletion_request(raw_request)
    )
    destination = None
    with GitRepository(Path(path)) as repo:
        try:
            _guard_repository(repo)
            object_format = _object_format(repo)
            _bind_deletion_request_object_format(request, object_format)
            destination = _destination(repo, request)
            target_sha = _probe_ref(
                repo, destination["_endpoint"], request.ref, object_format
            )
            plan = _base_plan(request, destination)
            plan["target"] = {
                "present": target_sha is not None,
                "sha": target_sha,
            }
            public_destination = plan["destination"]
            if target_sha is None:
                plan["status"] = "verified"
                plan["postchecks"] = [
                    {
                        "kind": "remote_ref_absent",
                        "endpoint_fingerprint": public_destination[
                            "endpoint_fingerprint"
                        ],
                        "ref": request.ref,
                    }
                ]
                return plan
            if target_sha != request.expected_target_sha:
                plan["reasons"] = [
                    {
                        "code": "EXPECTED_REMOTE_REF_MOVED",
                        "evidence": {
                            "ref": request.ref,
                            "expected_sha": request.expected_target_sha,
                            "observed_sha": target_sha,
                        },
                    }
                ]
                return plan
            plan["status"] = "ready"
            plan["deletion"] = {
                "ref": request.ref,
                "refspec": f":{request.ref}",
                "lease": (
                    f"--force-with-lease={request.ref}:{request.expected_target_sha}"
                ),
                "options": ["--no-follow-tags", "--recurse-submodules=check"],
                "config_overrides": {
                    "push.followTags": "false",
                    "push.recurseSubmodules": "check",
                },
                "expected_target_sha": request.expected_target_sha,
            }
            plan["postchecks"] = [
                {
                    "kind": "remote_ref_absent",
                    "endpoint_fingerprint": public_destination["endpoint_fingerprint"],
                    "ref": request.ref,
                }
            ]
            return plan
        except PolicyGate as gate:
            return _blocked_plan(request, gate, destination)


def _load_reviewed_deletion_plan(plan_bytes: bytes, reviewed_plan_sha256: str) -> Any:
    if not isinstance(plan_bytes, bytes):
        raise MalformedDeletionPlan("reviewed deletion plan must be exact bytes")
    if (
        not isinstance(reviewed_plan_sha256, str)
        or DELETION_DIGEST_RE.fullmatch(reviewed_plan_sha256) is None
    ):
        raise MalformedDeletionPlan(
            "reviewed deletion plan digest must be sha256:<64 lowercase hex>"
        )
    observed = "sha256:" + hashlib.sha256(plan_bytes).hexdigest()
    if not hmac.compare_digest(observed, reviewed_plan_sha256):
        raise ReviewedDeletionPlanDigestMismatch(
            "exact reviewed deletion plan bytes do not match digest"
        )
    try:
        document = plan_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MalformedDeletionPlan("reviewed deletion plan is not UTF-8") from error
    return json.loads(
        document,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


def _require_sha(value: Any, field: str) -> None:
    _require(
        isinstance(value, str) and SHA_RE.fullmatch(value) is not None,
        f"{field} must be a full SHA",
    )


def validate_ready_deletion_plan(raw: Any) -> tuple[dict, RemoteRefDeletionRequest]:
    """Validate a ready remote-ref deletion plan before any repository access."""
    _require(
        isinstance(raw, dict) and set(raw) == DELETION_PLAN_KEYS,
        "deletion plan fields differ",
    )
    _require(
        type(raw["schema_version"]) is int and raw["schema_version"] == 1,
        "deletion plan schema_version must be integer 1",
    )
    _require(raw["status"] == "ready", "deletion plan status must be ready")
    _require(raw["reasons"] == [], "ready deletion plan must not contain reasons")
    _require(
        raw["planner_effects"] == deletion_planner_effects(),
        "deletion planner effects drift",
    )
    try:
        request = parse_deletion_request(raw["request"])
    except MalformedRequest as error:
        raise MalformedDeletionPlan(
            f"embedded deletion request is invalid: {error}"
        ) from error
    _require(
        raw["request"] == deletion_request_document(request),
        "deletion request normalization drift",
    )
    destination = raw["destination"]
    _require(
        isinstance(destination, dict)
        and set(destination)
        == {"remote", "ref", "endpoint_fingerprint", "config_digest"},
        "deletion destination fields differ",
    )
    _require(
        destination["remote"] == request.remote
        and destination["ref"] == request.ref
        and all(
            isinstance(destination[field], str) and destination[field]
            for field in ("endpoint_fingerprint", "config_digest")
        ),
        "deletion destination drift",
    )
    target = raw["target"]
    _require(
        target == {"present": True, "sha": request.expected_target_sha},
        "deletion target must be the exact expected SHA",
    )
    deletion = raw["deletion"]
    _require(
        isinstance(deletion, dict)
        and set(deletion)
        == {
            "ref",
            "refspec",
            "lease",
            "options",
            "config_overrides",
            "expected_target_sha",
        },
        "deletion fields differ",
    )
    _require_sha(deletion["expected_target_sha"], "deletion expected_target_sha")
    _require(
        deletion["ref"] == request.ref
        and deletion["refspec"] == f":{request.ref}"
        and deletion["lease"]
        == f"--force-with-lease={request.ref}:{request.expected_target_sha}"
        and deletion["expected_target_sha"] == request.expected_target_sha,
        "deletion lease or refspec drift",
    )
    _require(
        deletion["options"] == ["--no-follow-tags", "--recurse-submodules=check"],
        "deletion options drift",
    )
    _require(
        deletion["config_overrides"]
        == {"push.followTags": "false", "push.recurseSubmodules": "check"},
        "deletion config overrides drift",
    )
    _require(
        raw["postchecks"]
        == [
            {
                "kind": "remote_ref_absent",
                "endpoint_fingerprint": destination["endpoint_fingerprint"],
                "ref": request.ref,
            }
        ],
        "deletion postcheck drift",
    )
    return raw, request


def _deletion_result(
    plan: dict,
    *,
    status: str,
    deletion_attempted: bool,
    reasons: list[dict],
) -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "destination": plan["destination"],
        "expected_target_sha": plan["request"]["expected_target_sha"],
        "deletion_attempted": deletion_attempted,
        "reasons": reasons,
        "postchecks": plan["postchecks"] if status == "verified" else [],
    }


def _blocked_deletion(plan: dict, gate: PolicyGate, *, attempted: bool) -> dict:
    return _deletion_result(
        plan,
        status="blocked",
        deletion_attempted=attempted,
        reasons=[{"code": gate.code, "evidence": gate.evidence}],
    )


def _unknown_remote_outcome(plan: dict, failure_type: str) -> dict:
    destination = plan["destination"]
    gate = PolicyGate(
        "POST_DELETION_STATE_UNKNOWN",
        failure_type=failure_type,
        ref=destination["ref"],
        expected_sha=plan["request"]["expected_target_sha"],
        endpoint_fingerprint=destination["endpoint_fingerprint"],
        reconciliation=(
            "Re-probe the exact captured endpoint and ref before deciding "
            "whether any retry is safe."
        ),
    )
    return _blocked_deletion(plan, gate, attempted=True)


def execute_remote_ref_deletion(
    path: Path, plan_bytes: bytes, reviewed_plan_sha256: str
) -> dict:
    """Delete one exact remote ref once, or return verified absence, without retry."""
    raw_plan = _load_reviewed_deletion_plan(plan_bytes, reviewed_plan_sha256)
    plan, request = validate_ready_deletion_plan(raw_plan)
    with GitRepository(Path(path)) as repo:
        deletion_attempted = False
        try:
            _guard_repository(repo)
            object_format = _object_format(repo)
            _bind_deletion_request_object_format(request, object_format)
            destination = plan["destination"]
            selection = _selection(repo, request)
            endpoint, endpoint_fingerprint = _endpoint(repo, request.remote)
            config_digest = _config_digest(selection, endpoint_fingerprint)
            if endpoint_fingerprint != destination["endpoint_fingerprint"]:
                raise PolicyGate(
                    "REVIEWED_DELETION_ENDPOINT_CHANGED",
                    expected_fingerprint=destination["endpoint_fingerprint"],
                    observed_fingerprint=endpoint_fingerprint,
                )
            if config_digest != destination["config_digest"]:
                raise PolicyGate(
                    "REVIEWED_DELETION_CONFIG_CHANGED",
                    expected_digest=destination["config_digest"],
                    observed_digest=config_digest,
                )
            observed_target = _probe_ref(repo, endpoint, request.ref, object_format)
            if observed_target is None:
                return _deletion_result(
                    plan,
                    status="verified",
                    deletion_attempted=False,
                    reasons=[],
                )
            if observed_target != request.expected_target_sha:
                raise PolicyGate(
                    "REVIEWED_DELETION_LEASE_CHANGED",
                    expected_sha=request.expected_target_sha,
                    observed_sha=observed_target,
                    ref=request.ref,
                )
            deletion = plan["deletion"]
            deletion_attempted = True
            result = _run_endpoint(
                repo,
                endpoint,
                [
                    "-c",
                    "push.followTags=false",
                    "-c",
                    "push.recurseSubmodules=check",
                    "push",
                    *deletion["options"],
                    deletion["lease"],
                ],
                [deletion["refspec"]],
            )
            if result.returncode != 0:
                raise PolicyGate("DELETION_PUSH_FAILED", returncode=result.returncode)
            observed_after = _probe_ref(repo, endpoint, request.ref, object_format)
            if observed_after is not None:
                raise PolicyGate(
                    "POST_DELETION_REF_PRESENT",
                    ref=request.ref,
                    observed_sha=observed_after,
                )
            return _deletion_result(
                plan,
                status="verified",
                deletion_attempted=True,
                reasons=[],
            )
        except PolicyGate as gate:
            if deletion_attempted and gate.code != "POST_DELETION_REF_PRESENT":
                return _unknown_remote_outcome(plan, gate.code)
            return _blocked_deletion(plan, gate, attempted=deletion_attempted)
        except Exception as error:
            if not deletion_attempted:
                raise
            return _unknown_remote_outcome(plan, type(error).__name__)
