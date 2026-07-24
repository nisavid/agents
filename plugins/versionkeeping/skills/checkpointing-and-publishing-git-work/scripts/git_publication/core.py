"""Pure policy decisions for deterministic Git publication."""

from dataclasses import dataclass
from typing import Any, FrozenSet, Optional, Tuple, Union


@dataclass(frozen=True)
class PublicationRequest:
    schema_version: int
    start_head: str
    source_sha: str
    task_owned_commits: FrozenSet[str]
    adopted_commits: FrozenSet[str]
    removal_authorized_commits: FrozenSet[str]
    explicit_destination: Optional[dict]
    allow_create: bool
    creation_base_ref: Optional[str]


@dataclass(frozen=True)
class CreationBase:
    sha: str
    is_ancestor: bool
    to_start_shas: Tuple[str, ...]


@dataclass(frozen=True)
class PresentTarget:
    sha: str
    outgoing_shas: Tuple[str, ...]
    target_only_shas: Tuple[str, ...]
    is_ancestor: bool

    def __post_init__(self) -> None:
        if not isinstance(self.sha, str) or not self.sha:
            raise ValueError("present target requires a SHA")


@dataclass(frozen=True)
class AbsentTarget:
    outgoing_shas: Tuple[str, ...]
    start_advertised: bool
    creation_base: Optional[CreationBase]


TargetState = Union[PresentTarget, AbsentTarget]


@dataclass(frozen=True)
class RepositorySnapshot:
    remote: str
    ref: str
    endpoint_fingerprint: str
    config_digest: str
    target: TargetState
    start_is_ancestor: bool


def _reason(code: str, **evidence: Any) -> dict:
    return {"code": code, "evidence": evidence}


def request_document(request: PublicationRequest) -> dict:
    """Return the normalized request embedded in a reviewed execution plan."""
    return {
        "schema_version": request.schema_version,
        "start_head": request.start_head,
        "source_sha": request.source_sha,
        "task_owned_commits": sorted(request.task_owned_commits),
        "adopted_commits": sorted(request.adopted_commits),
        "removal_authorized_commits": sorted(request.removal_authorized_commits),
        "explicit_destination": request.explicit_destination,
        "allow_create": request.allow_create,
        "creation_base_ref": request.creation_base_ref,
    }


def planner_effects() -> dict:
    """Describe the planner's bounded local repository mutation surface."""
    return {
        "local_mutation_possible": True,
        "bounded_object_fetch": True,
        "objects_may_persist": True,
        "temporary_ref_namespace": "refs/versionkeeping/publication/",
        "fetch_head_preserved": True,
    }


def _base_result(request: PublicationRequest, snapshot: RepositorySnapshot) -> dict:
    present = isinstance(snapshot.target, PresentTarget)
    target_sha = snapshot.target.sha if present else None
    target_only_shas = snapshot.target.target_only_shas if present else ()
    rewrite = bool(target_only_shas)
    return {
        "schema_version": 1,
        "status": "blocked",
        "request": request_document(request),
        "planner_effects": planner_effects(),
        "reasons": [],
        "source_sha": request.source_sha,
        "destination": {
            "remote": snapshot.remote,
            "ref": snapshot.ref,
            "endpoint_fingerprint": snapshot.endpoint_fingerprint,
            "config_digest": snapshot.config_digest,
        },
        "target": {"present": present, "sha": target_sha},
        "outgoing_shas": list(snapshot.target.outgoing_shas),
        "target_only_shas": list(target_only_shas),
        "fast_forward": bool(present and snapshot.target.is_ancestor),
        "rewrite_required": rewrite,
        "push": None,
        "postchecks": [],
    }


def _append_common_gates(
    result: dict, request: PublicationRequest, snapshot: RepositorySnapshot
) -> None:
    if not snapshot.start_is_ancestor:
        result["reasons"].append(
            _reason("START_NOT_ANCESTOR_OF_SOURCE", start_head=request.start_head)
        )

    allowed = request.task_owned_commits | request.adopted_commits
    unowned = sorted(set(snapshot.target.outgoing_shas) - allowed)
    if unowned:
        result["reasons"].append(
            _reason("OUTGOING_COMMITS_NOT_OWNED_OR_ADOPTED", shas=unowned)
        )


def _append_present_target_gates(
    result: dict, request: PublicationRequest, target: PresentTarget
) -> None:
    unauthorized = sorted(
        set(target.target_only_shas) - request.removal_authorized_commits
    )
    if unauthorized:
        result["status"] = "needs_reconciliation"
        result["reasons"].append(
            _reason(
                "TARGET_ONLY_COMMITS_REQUIRE_REMOVAL_AUTHORIZATION",
                shas=unauthorized,
            )
        )


def _append_absent_target_gates(
    result: dict,
    request: PublicationRequest,
    snapshot: RepositorySnapshot,
    target: AbsentTarget,
) -> None:
    if not request.allow_create:
        result["reasons"].append(
            _reason("TARGET_ABSENT_CREATION_NOT_ALLOWED", ref=snapshot.ref)
        )
    if target.start_advertised:
        return
    if request.creation_base_ref is None:
        result["reasons"].append(
            _reason(
                "START_NOT_ADVERTISED_AT_DESTINATION",
                start_head=request.start_head,
            )
        )
    elif target.creation_base is None:
        result["reasons"].append(
            _reason("CREATION_BASE_REF_ABSENT", ref=request.creation_base_ref)
        )
    elif not target.creation_base.is_ancestor:
        result["reasons"].append(
            _reason(
                "CREATION_BASE_NOT_ANCESTOR_OF_START",
                ref=request.creation_base_ref,
            )
        )
    else:
        unadopted = sorted(
            set(target.creation_base.to_start_shas) - request.adopted_commits
        )
        if unadopted:
            result["reasons"].append(
                _reason("CREATION_BASE_TO_START_COMMITS_NOT_ADOPTED", shas=unadopted)
            )


def _finalize_blocked(result: dict) -> dict:
    if result["reasons"]:
        if result["status"] != "needs_reconciliation" or len(result["reasons"]) > 1:
            result["status"] = "blocked"
    return result


def _postcheck(request: PublicationRequest, snapshot: RepositorySnapshot) -> dict:
    return {
        "kind": "remote_ref_equals",
        "endpoint_fingerprint": snapshot.endpoint_fingerprint,
        "ref": snapshot.ref,
        "sha": request.source_sha,
    }


def _ready_result(
    result: dict, request: PublicationRequest, snapshot: RepositorySnapshot
) -> dict:
    target_present = isinstance(snapshot.target, PresentTarget)
    target_sha = snapshot.target.sha if target_present else None
    lease = (
        f"--force-with-lease={snapshot.ref}:{target_sha}"
        if target_present
        else f"--force-with-lease={snapshot.ref}:"
    )
    result["status"] = "ready"
    result["push"] = {
        "source_sha": request.source_sha,
        "ref": snapshot.ref,
        "refspec": f"{request.source_sha}:{snapshot.ref}",
        "lease": lease,
        "options": ["--no-follow-tags", "--recurse-submodules=check"],
        "config_overrides": {
            "push.followTags": "false",
            "push.recurseSubmodules": "check",
        },
        "expected_target": {
            "present": target_present,
            "sha": target_sha,
        },
    }
    result["postchecks"] = [_postcheck(request, snapshot)]
    return result


def plan_publication(request: PublicationRequest, snapshot: RepositorySnapshot) -> dict:
    """Return a publication decision from a complete, normalized graph snapshot."""
    result = _base_result(request, snapshot)
    _append_common_gates(result, request, snapshot)
    if isinstance(snapshot.target, PresentTarget):
        _append_present_target_gates(result, request, snapshot.target)
    else:
        _append_absent_target_gates(result, request, snapshot, snapshot.target)

    if result["reasons"]:
        return _finalize_blocked(result)
    if (
        isinstance(snapshot.target, PresentTarget)
        and snapshot.target.sha == request.source_sha
    ):
        result["status"] = "verified"
        result["postchecks"] = [_postcheck(request, snapshot)]
        return result
    return _ready_result(result, request, snapshot)
