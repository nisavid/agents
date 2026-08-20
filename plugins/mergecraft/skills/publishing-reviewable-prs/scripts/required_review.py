"""Bind one PR publication candidate to canonical Task Witness review evidence."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import pwd
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Literal, Mapping

WRITER_SCRIPTS = (
    Path(__file__).parents[2] / "writing-reviewable-pr-descriptions/scripts"
)
if str(WRITER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WRITER_SCRIPTS))
from change_navigation.review_input import (  # noqa: E402
    PR_NUMBER_TOKEN,
    ReviewInputError,
    parse_review_input,
)
from reviewable_pr_state import ExpectedIdentity, PublicationError  # noqa: E402

CANDIDATE_CONTRACT = "mergecraft-publication-candidate-v1"
PUBLICATION_PROFILE_CONTRACT = "mergecraft-publication-profile-v1"
REQUIRED_PROFILE_CONTRACT = "mergecraft-required-publication-review-profile-v2"
OBSERVATION_CONTRACT = "mergecraft-required-review-observation-v1"
ENVELOPE_CONTRACT = "task-witness-launch-envelope-v1"
WITNESS_CONTRACT = "task-witness-canonical-projection-v2"
PROJECTION_CONTRACT = "tricritical-terminal-review-projection-v2"
EVIDENCE_CONTRACT = "tricritical-terminal-review-evidence-v2"
PRODUCER_ID = "tricritical-review-loop-v2"
VALIDATOR_ID = "tricritical-terminal-review-evidence-validator-v2"
REVIEW_MODES = {"required", "not-required"}
OPERATIONS = {"create", "update-text", "mark-ready"}
BODY_SOURCE_KINDS = {"template", "body", "stored-body"}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SHA1_RE = re.compile(r"[0-9a-f]{40}")
REPOSITORY_RE = re.compile(r"[^/\s]+/[^/\s]+")
MAX_STDOUT_BYTES = 4 * 1024 * 1024
MAX_STDERR_BYTES = 256 * 1024
TIMEOUT_SECONDS = 60
SETTLEMENT_SECONDS = 65
TERMINATION_GRACE_SECONDS = 2
IO_CHUNK_BYTES = 65_536
# This closes the internal call shape; it is not a security boundary.
_FRONT_DOOR_CALL_CAPABILITY = object()


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise PublicationError("required-review value is not canonical JSON") from error


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PublicationError(f"{label} schema drift")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise PublicationError(f"{label} is not a SHA-256 digest")
    return value


def _identity(value: Any, label: str) -> dict[str, str]:
    value = _exact(value, {"kind", "value", "content_sha256"}, label)
    if not isinstance(value["kind"], str) or not value["kind"]:
        raise PublicationError(f"{label} kind is invalid")
    if not isinstance(value["value"], str) or not value["value"]:
        raise PublicationError(f"{label} value is invalid")
    _sha256(value["content_sha256"], f"{label} content")
    return value


def _identity_for(kind: str, value: Any) -> dict[str, str]:
    digest = _sha(_canonical(value))
    return {"kind": kind, "value": f"sha256:{digest}", "content_sha256": digest}


def _required_profile(selected_specialists: list[str]) -> dict[str, Any]:
    return {
        "contract": REQUIRED_PROFILE_CONTRACT,
        "execution_mode": "independent",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "target": {
            "product_family": "codex",
            "surface": "chatgpt-codex",
            "executor": "codex",
        },
        "topology": {
            "relationship": "child",
            "ownership": "leader-owned",
            "transport": "native-tool",
        },
        "assurance": {
            "target": "product-attested",
            "model": "product-attested",
            "topology": "product-attested",
            "authority": "product-attested",
            "execution_result": "product-attested",
        },
        "authority": {
            "access": "read-only",
            "subdelegation": False,
            "external_action": False,
        },
        "isolation": {"enforceable": True},
        "required_axes": ["intent", "runtime", "structure"],
        "selected_specialists": selected_specialists,
        "terminal": "bare-clean",
    }


@dataclass(frozen=True)
class PublicationCandidate:
    value: dict[str, Any]
    content_sha256: str
    candidate_identity: dict[str, str]
    review_input_identity: dict[str, str]
    requirements_identity: dict[str, str]
    body_source_raw: bytes
    published_body: str


@dataclass(frozen=True)
class RequiredReviewObservation:
    launch_envelope_sha256: str
    generation: str
    active_record_sha256: str
    trust_context_sha256: str
    bundle_sha256: str
    tricritical_manifest_sha256: str
    tricritical_projection_sha256: str

    def as_json(self) -> dict[str, Any]:
        return {
            "contract": OBSERVATION_CONTRACT,
            "launch_envelope_sha256": self.launch_envelope_sha256,
            "task_witness": {
                "generation": self.generation,
                "active_record_sha256": self.active_record_sha256,
                "trust_context_sha256": self.trust_context_sha256,
                "bundle_sha256": self.bundle_sha256,
            },
            "tricritical": {
                "manifest_sha256": self.tricritical_manifest_sha256,
                "projection_content_sha256": self.tricritical_projection_sha256,
            },
        }


_REVIEW_CONSTRUCTION_TOKEN = object()


@dataclass(frozen=True, init=False)
class PublicationReview:
    mode: Literal["required", "not-required"]
    publication_candidate_sha256: str
    observation: RequiredReviewObservation | None
    _transition_validated: bool = dataclass_field(repr=False, compare=False)

    def __init__(
        self,
        mode: Literal["required", "not-required"],
        publication_candidate_sha256: str,
        observation: RequiredReviewObservation | None,
        *,
        _construction_token: object | None = None,
        _transition_validated: bool = False,
    ) -> None:
        if _construction_token is not _REVIEW_CONSTRUCTION_TOKEN:
            raise PublicationError(
                "publication reviews must come from the canonical review factory"
            )
        object.__setattr__(self, "mode", mode)
        object.__setattr__(
            self, "publication_candidate_sha256", publication_candidate_sha256
        )
        object.__setattr__(self, "observation", observation)
        object.__setattr__(self, "_transition_validated", _transition_validated)

    def as_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "publication_candidate_sha256": self.publication_candidate_sha256,
            "observation": (
                self.observation.as_json() if self.observation is not None else None
            ),
        }


def _make_publication_review(
    mode: Literal["required", "not-required"],
    publication_candidate_sha256: str,
    observation: RequiredReviewObservation | None,
    *,
    transition_validated: bool,
) -> PublicationReview:
    return PublicationReview(
        mode,
        publication_candidate_sha256,
        observation,
        _construction_token=_REVIEW_CONSTRUCTION_TOKEN,
        _transition_validated=transition_validated,
    )


def _strict_review_input(raw: bytes) -> tuple[int, str]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PublicationError("review input contains a duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_: str) -> None:
        raise PublicationError("review input contains a non-finite JSON value")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
        parsed = parse_review_input(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ReviewInputError) as error:
        raise PublicationError("review input is unavailable or invalid") from error
    return int(parsed.raw["version"]), parsed.content_sha256


def _read_exact(path: Path, label: str) -> bytes:
    if not path.is_absolute():
        raise PublicationError(f"{label} path must be absolute")
    try:
        return path.read_bytes()
    except OSError as error:
        raise PublicationError(f"cannot read {label}") from error


def build_candidate(
    *,
    operation: str,
    repository: str,
    pr_number: int | str,
    base: str,
    base_oid: str,
    head: str,
    head_oid: str,
    head_owner: str,
    head_repository: str,
    title: str,
    body_source_kind: str,
    body_source_raw: bytes,
    published_body: str,
    review_input_path: Path,
    review_mode: str,
    selected_specialists: list[str],
) -> PublicationCandidate:
    """Freeze the exact Mergecraft-owned publication candidate."""

    if operation not in OPERATIONS:
        raise PublicationError("publication operation is invalid")
    if review_mode not in REVIEW_MODES:
        raise PublicationError("review mode must be required or not-required")
    if (
        not isinstance(selected_specialists, list)
        or not all(
            isinstance(specialist, str) and specialist
            for specialist in selected_specialists
        )
        or selected_specialists != sorted(set(selected_specialists))
    ):
        raise PublicationError("selected review specialists must be sorted and unique")
    if body_source_kind not in BODY_SOURCE_KINDS:
        raise PublicationError("publication body source kind is invalid")
    expected_kind = {
        "create": {"template"},
        "update-text": {"template", "body"},
        "mark-ready": {"stored-body"},
    }[operation]
    if body_source_kind not in expected_kind:
        raise PublicationError("publication body source does not match the operation")
    if operation == "create":
        if pr_number != PR_NUMBER_TOKEN:
            raise PublicationError("create review must use the new-PR token")
    elif type(pr_number) is not int or pr_number <= 0:
        raise PublicationError("existing-PR review needs a positive PR number")
    if (
        REPOSITORY_RE.fullmatch(repository) is None
        or REPOSITORY_RE.fullmatch(head_repository) is None
        or not isinstance(base, str)
        or not base
        or not isinstance(head, str)
        or ":" not in head
        or head.split(":", 1)[0] != head_owner
        or head_repository.split("/", 1)[0] != head_owner
        or SHA1_RE.fullmatch(base_oid) is None
        or SHA1_RE.fullmatch(head_oid) is None
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(body_source_raw, bytes)
        or not isinstance(published_body, str)
    ):
        raise PublicationError("publication candidate identity is invalid")
    review_input_raw = _read_exact(review_input_path, "review input")
    review_input_version, review_input_content = _strict_review_input(review_input_raw)
    review_input = {
        "schema_version": review_input_version,
        "raw_sha256": _sha(review_input_raw),
        "content_sha256": review_input_content,
    }
    profile = {
        "contract": PUBLICATION_PROFILE_CONTRACT,
        "review_mode": review_mode,
        "selected_specialists": selected_specialists,
    }
    value = {
        "schema_version": 1,
        "contract": CANDIDATE_CONTRACT,
        "operation": operation,
        "repository": repository,
        "pr_number": pr_number,
        "base": {"ref": base, "oid": base_oid},
        "head": {
            "ref": head,
            "oid": head_oid,
            "owner": head_owner,
            "repository": head_repository,
        },
        "title": title,
        "body_source": body_source_binding(
            kind=body_source_kind,
            raw=body_source_raw,
            published=published_body,
            render_contract=(
                "mergecraft-pr-number-token-render-v1"
                if body_source_kind == "template"
                else "literal-utf8-v1"
            ),
        ),
        "review_input": review_input,
        "publication_profile": profile,
    }
    digest = _sha(_canonical(value))
    requirements = _required_profile(selected_specialists)
    return PublicationCandidate(
        value=value,
        content_sha256=digest,
        candidate_identity={
            "kind": CANDIDATE_CONTRACT,
            "value": f"sha256:{digest}",
            "content_sha256": digest,
        },
        review_input_identity=_identity_for("mergecraft-review-input-v1", review_input),
        requirements_identity=_identity_for(REQUIRED_PROFILE_CONTRACT, requirements),
        body_source_raw=body_source_raw,
        published_body=published_body,
    )


def body_source_binding(
    *, kind: str, raw: bytes, published: str, render_contract: str
) -> dict[str, Any]:
    """Bind source-file bytes separately from exact target publication bytes."""

    if kind not in BODY_SOURCE_KINDS or not isinstance(raw, bytes):
        raise PublicationError("publication body source is invalid")
    if not isinstance(published, str):
        raise PublicationError("publication body target is invalid")
    expected_render = (
        "mergecraft-pr-number-token-render-v1"
        if kind == "template"
        else "literal-utf8-v1"
    )
    if render_contract != expected_render:
        raise PublicationError("publication body render contract is invalid")
    published_raw = published.encode("utf-8")
    return {
        "kind": kind,
        "raw_byte_length": len(raw),
        "raw_sha256": _sha(raw),
        "published_byte_length": len(published_raw),
        "published_sha256": _sha(published_raw),
        "render_contract": render_contract,
    }


def validate_create_rendering(
    *,
    candidate: PublicationCandidate,
    template: str,
    rendered_body: str,
    pr_number: int,
) -> None:
    """Prove an assigned create body is the reviewed token-template rendering."""

    if (
        candidate.value.get("operation") != "create"
        or candidate.value.get("pr_number") != PR_NUMBER_TOKEN
        or type(pr_number) is not int
        or pr_number <= 0
        or template.count(PR_NUMBER_TOKEN) < 1
    ):
        raise PublicationError("reviewed create token rendering is invalid")
    expected_source = body_source_binding(
        kind="template",
        raw=template.encode("utf-8"),
        published=template,
        render_contract="mergecraft-pr-number-token-render-v1",
    )
    if candidate.value.get("body_source") != expected_source:
        raise PublicationError("reviewed create template bytes drifted")
    if rendered_body != template.replace(PR_NUMBER_TOKEN, str(pr_number)):
        raise PublicationError(
            "assigned PR body is not the unique token-only rendering"
        )


def validate_review_input_binding(
    candidate: PublicationCandidate,
    schema_version: int,
    content_sha256: str,
    review_input_path: Path,
) -> None:
    binding = candidate.value.get("review_input")
    raw = _read_exact(review_input_path, "review input")
    if (
        not isinstance(binding, dict)
        or binding.get("schema_version") != schema_version
        or binding.get("content_sha256") != content_sha256
        or binding.get("raw_sha256") != _sha(raw)
    ):
        raise PublicationError(
            "review input changed after publication candidate freeze"
        )


def validate_transition_candidate(
    *,
    candidate: PublicationCandidate,
    expected: ExpectedIdentity,
    operation: str,
    review_input_schema_version: int,
    review_input_sha256: str,
    final_title_sha256: str,
    final_body_sha256: str,
    review: PublicationReview,
) -> None:
    """Revalidate the full frozen candidate against one canonical transition."""

    value = _exact(
        candidate.value,
        {
            "schema_version",
            "contract",
            "operation",
            "repository",
            "pr_number",
            "base",
            "head",
            "title",
            "body_source",
            "review_input",
            "publication_profile",
        },
        "publication candidate",
    )
    digest = _sha(_canonical(value))
    if candidate.content_sha256 != digest:
        raise PublicationError("publication candidate canonical digest drift")
    expected_identity = {
        "kind": CANDIDATE_CONTRACT,
        "value": f"sha256:{digest}",
        "content_sha256": digest,
    }
    if candidate.candidate_identity != expected_identity:
        raise PublicationError("publication candidate identity drift")
    base = _exact(value["base"], {"ref", "oid"}, "publication candidate base")
    head = _exact(
        value["head"],
        {"ref", "oid", "owner", "repository"},
        "publication candidate head",
    )
    expected_pr: int | str = (
        PR_NUMBER_TOKEN if operation == "create" else expected.pr_number
    )
    if (
        value["schema_version"] != 1
        or value["contract"] != CANDIDATE_CONTRACT
        or value["operation"] != operation
        or value["repository"] != expected.repository
        or value["pr_number"] != expected_pr
        or base != {"ref": expected.base, "oid": expected.base_oid}
        or head
        != {
            "ref": expected.head,
            "oid": expected.head_oid,
            "owner": expected.head_owner,
            "repository": expected.head_repository,
        }
    ):
        raise PublicationError("publication candidate transition identity drift")
    review_input = _exact(
        value["review_input"],
        {"schema_version", "raw_sha256", "content_sha256"},
        "publication candidate review input",
    )
    _sha256(review_input["raw_sha256"], "publication candidate review input raw")
    if (
        review_input["schema_version"] != review_input_schema_version
        or review_input["content_sha256"] != review_input_sha256
    ):
        raise PublicationError("publication candidate review input drift")
    profile = _exact(
        value["publication_profile"],
        {"contract", "review_mode", "selected_specialists"},
        "publication candidate profile",
    )
    if (
        profile["contract"] != PUBLICATION_PROFILE_CONTRACT
        or profile["review_mode"] != review.mode
        or not isinstance(profile["selected_specialists"], list)
        or not all(
            isinstance(specialist, str) and specialist
            for specialist in profile["selected_specialists"]
        )
        or profile["selected_specialists"]
        != sorted(set(profile["selected_specialists"]))
        or review.publication_candidate_sha256 != digest
        or review._transition_validated is not True
        or parse_publication_review(review.as_json()) != review
    ):
        raise PublicationError("publication candidate review provenance drift")
    if candidate.review_input_identity != _identity_for(
        "mergecraft-review-input-v1", review_input
    ) or candidate.requirements_identity != _identity_for(
        REQUIRED_PROFILE_CONTRACT,
        _required_profile(profile["selected_specialists"]),
    ):
        raise PublicationError("publication candidate derived identity drift")
    kind = (
        value["body_source"].get("kind")
        if isinstance(value["body_source"], dict)
        else None
    )
    render_contract = (
        "mergecraft-pr-number-token-render-v1"
        if kind == "template"
        else "literal-utf8-v1"
    )
    if value["body_source"] != body_source_binding(
        kind=kind,
        raw=candidate.body_source_raw,
        published=candidate.published_body,
        render_contract=render_contract,
    ):
        raise PublicationError("publication candidate body source drift")
    published_body = candidate.published_body
    if operation == "create":
        if published_body.count(PR_NUMBER_TOKEN) < 1:
            raise PublicationError("publication candidate create token is absent")
        published_body = published_body.replace(
            PR_NUMBER_TOKEN, str(expected.pr_number)
        )
    if (
        _sha(str(value["title"]).encode("utf-8")) != final_title_sha256
        or _sha(published_body.encode("utf-8")) != final_body_sha256
    ):
        raise PublicationError("publication candidate final publication drift")


def _task_witness_front_door() -> Path:
    try:
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    except (KeyError, OSError) as error:
        raise PublicationError("canonical Task Witness home is unavailable") from error
    return home / ".local/libexec/task-witness/task-witness"


def _install_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _private_owned(metadata: os.stat_result, *, directory: bool, mode: int) -> bool:
    kind = (
        stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    )
    return (
        kind
        and stat.S_IMODE(metadata.st_mode) == mode
        and metadata.st_uid == os.geteuid()
        and metadata.st_gid == os.getegid()
        and (directory or metadata.st_nlink == 1)
    )


@dataclass(frozen=True)
class _AuthenticatedFrontDoorObservation:
    path: Path
    descriptor: int
    identity: tuple[int, ...]
    capability: object = dataclass_field(repr=False, compare=False)


@contextmanager
def _authenticated_front_door() -> Any:
    path = _task_witness_front_door()
    descriptors: list[int] = []
    try:
        home = path.parents[3]
        home_metadata = os.stat(home, follow_symlinks=False)
        if not _private_owned(home_metadata, directory=True, mode=0o700):
            raise PublicationError("canonical Task Witness home is not private")
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        current = os.open(home, directory_flags)
        descriptors.append(current)
        if _install_identity(os.fstat(current)) != _install_identity(home_metadata):
            raise PublicationError("canonical Task Witness home identity drift")
        for component in (".local", "libexec", "task-witness"):
            current = os.open(component, directory_flags, dir_fd=current)
            descriptors.append(current)
            if not _private_owned(os.fstat(current), directory=True, mode=0o700):
                raise PublicationError(
                    "canonical Task Witness install root is not private"
                )
        leaf = os.open(
            "task-witness",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current,
        )
        descriptors.append(leaf)
        metadata = os.fstat(leaf)
        if not _private_owned(metadata, directory=False, mode=0o500):
            raise PublicationError("canonical Task Witness front door is not authentic")
        path_metadata = os.stat(path, follow_symlinks=False)
        identity = _install_identity(metadata)
        if _install_identity(path_metadata) != identity:
            raise PublicationError("canonical Task Witness front door identity drift")
        try:
            yield _AuthenticatedFrontDoorObservation(
                path=path,
                descriptor=leaf,
                identity=identity,
                capability=_FRONT_DOOR_CALL_CAPABILITY,
            )
        finally:
            if (
                _install_identity(os.fstat(leaf)) != identity
                or _install_identity(os.stat(path, follow_symlinks=False)) != identity
            ):
                raise PublicationError(
                    "canonical Task Witness front door changed during validation"
                )
    except OSError as error:
        raise PublicationError(
            "canonical Task Witness front door is unavailable"
        ) from error
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


@dataclass(frozen=True)
class _ProcessIdentity:
    pid: int
    start_seconds: int
    start_microseconds: int


def _descendant_identities(
    root_pid: int,
    census: Mapping[int, tuple[int, _ProcessIdentity]],
) -> set[_ProcessIdentity]:
    children: dict[int, list[int]] = {}
    for pid, (parent, _identity_value) in census.items():
        children.setdefault(parent, []).append(pid)
    pending = list(children.get(root_pid, []))
    result: set[_ProcessIdentity] = set()
    while pending:
        pid = pending.pop()
        entry = census.get(pid)
        if entry is not None and entry[1] not in result:
            result.add(entry[1])
            pending.extend(children.get(pid, []))
    return result


class _DarwinProcessBsdInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("command", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("open_files", ctypes.c_uint32),
        ("process_group", ctypes.c_uint32),
        ("job_control", ctypes.c_uint32),
        ("controlling_device", ctypes.c_uint32),
        ("terminal_process_group", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_seconds", ctypes.c_uint64),
        ("start_microseconds", ctypes.c_uint64),
    ]


def _darwin_library() -> Any:
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_listpids.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.proc_listpids.restype = ctypes.c_int
    library.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.proc_pidinfo.restype = ctypes.c_int
    library.proc_pidfdinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.proc_pidfdinfo.restype = ctypes.c_int
    return library


def _darwin_process_census(
    deadline: float,
) -> dict[int, tuple[int, _ProcessIdentity]] | None:
    """Read the process tree without invoking an ambient command on macOS."""

    if sys.platform != "darwin":
        return None
    try:
        if time.monotonic() >= deadline:
            raise PublicationError("canonical Task Witness settlement deadline expired")
        library = _darwin_library()
        required = library.proc_listpids(1, 0, None, 0)
        if time.monotonic() >= deadline:
            raise PublicationError("canonical Task Witness settlement deadline expired")
        if required <= 0:
            return None
        capacity = required // ctypes.sizeof(ctypes.c_int) + 256
        pids = (ctypes.c_int * capacity)()
        written = library.proc_listpids(1, 0, pids, ctypes.sizeof(pids))
        if time.monotonic() >= deadline:
            raise PublicationError("canonical Task Witness settlement deadline expired")
        if written <= 0:
            return None
        count = written // ctypes.sizeof(ctypes.c_int)
        census: dict[int, tuple[int, _ProcessIdentity]] = {}
        for pid in pids[:count]:
            if time.monotonic() >= deadline:
                raise PublicationError(
                    "canonical Task Witness settlement deadline expired"
                )
            if pid <= 0:
                continue
            info = _DarwinProcessBsdInfo()
            size = library.proc_pidinfo(
                pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)
            )
            if time.monotonic() >= deadline:
                raise PublicationError(
                    "canonical Task Witness settlement deadline expired"
                )
            if size == ctypes.sizeof(info) and info.pid == pid:
                census[pid] = (
                    int(info.ppid),
                    _ProcessIdentity(
                        pid,
                        int(info.start_seconds),
                        int(info.start_microseconds),
                    ),
                )
        return census
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _linux_process_census(
    deadline: float,
) -> dict[int, tuple[int, _ProcessIdentity]] | None:
    proc = Path("/proc")
    if sys.platform != "linux" or not proc.is_dir():
        return None
    census: dict[int, tuple[int, _ProcessIdentity]] = {}
    try:
        paths = list(proc.iterdir())
    except OSError:
        return None
    for path in paths:
        if time.monotonic() >= deadline:
            raise PublicationError("canonical Task Witness settlement deadline expired")
        if not path.name.isdigit():
            continue
        try:
            value = (path / "stat").read_text(encoding="ascii")
            if time.monotonic() >= deadline:
                raise PublicationError(
                    "canonical Task Witness settlement deadline expired"
                )
            fields = value[value.rfind(")") + 2 :].split()
            pid = int(path.name)
            census[pid] = (
                int(fields[1]),
                _ProcessIdentity(pid, int(fields[19]), 0),
            )
        except (IndexError, OSError, ValueError):
            continue
    return census


def _process_census(
    deadline: float,
) -> dict[int, tuple[int, _ProcessIdentity]] | None:
    return _darwin_process_census(deadline) or _linux_process_census(deadline)


def _active_identities(
    identities: set[_ProcessIdentity],
    census: Mapping[int, tuple[int, _ProcessIdentity]],
) -> set[_ProcessIdentity]:
    return {
        identity
        for identity in identities
        if census.get(identity.pid, (None, None))[1] == identity
    }


def _signal_identities(
    identities: set[_ProcessIdentity], action: int, *, deadline: float
) -> None:
    census = _process_census(deadline)
    if census is None:
        raise PublicationError("canonical Task Witness process census is unavailable")
    for identity in _active_identities(identities, census):
        if time.monotonic() >= deadline:
            raise PublicationError("canonical Task Witness settlement deadline expired")
        try:
            os.kill(identity.pid, action)
        except ProcessLookupError:
            pass
        except PermissionError as error:
            raise PublicationError(
                "canonical Task Witness descendant could not be settled"
            ) from error


def _sample_descendants(
    root_pid: int, retained: set[_ProcessIdentity], *, deadline: float
) -> None:
    census = _process_census(deadline)
    if census is None:
        raise PublicationError("canonical Task Witness process census is unavailable")
    if time.monotonic() >= deadline:
        raise PublicationError("canonical Task Witness settlement deadline expired")
    retained.update(_descendant_identities(root_pid, census))


def _active_retained(
    retained: set[_ProcessIdentity], *, deadline: float
) -> set[_ProcessIdentity]:
    census = _process_census(deadline)
    if census is None:
        raise PublicationError("canonical Task Witness process census is unavailable")
    if time.monotonic() >= deadline:
        raise PublicationError("canonical Task Witness settlement deadline expired")
    return _active_identities(retained, census)


def _settle(
    process: subprocess.Popen[bytes],
    retained: set[_ProcessIdentity],
    *,
    deadline: float,
) -> None:
    _sample_descendants(process.pid, retained, deadline=deadline)
    if time.monotonic() >= deadline:
        raise PublicationError("canonical Task Witness settlement deadline expired")
    if process.poll() is None:
        try:
            process.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass
    term_deadline = min(deadline, time.monotonic() + TERMINATION_GRACE_SECONDS)
    _signal_identities(retained, signal.SIGTERM, deadline=deadline)
    while time.monotonic() < term_deadline:
        _sample_descendants(process.pid, retained, deadline=deadline)
        if process.poll() is not None and not _active_retained(
            retained, deadline=deadline
        ):
            break
        remaining = term_deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))
    if time.monotonic() >= deadline:
        raise PublicationError("canonical Task Witness settlement deadline expired")
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError as error:
            raise PublicationError(
                "canonical Task Witness root could not be settled"
            ) from error
    _signal_identities(retained, signal.SIGKILL, deadline=deadline)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PublicationError("canonical Task Witness settlement deadline expired")
    try:
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        raise PublicationError("canonical Task Witness validation did not settle")
    while True:
        active = _active_retained(retained, deadline=deadline)
        if not active:
            break
        _signal_identities(active, signal.SIGKILL, deadline=deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PublicationError("canonical Task Witness descendants did not settle")
        time.sleep(min(0.01, remaining))


def _retired_supervised_process(
    observation: object, bundle_root: Path
) -> tuple[int, bytes, bytes]:
    # Retained for source-stage design validation only. The current-version
    # boundary below never selects this implementation.
    if (
        type(observation) is not _AuthenticatedFrontDoorObservation
        or observation.capability is not _FRONT_DOOR_CALL_CAPABILITY
        or not isinstance(bundle_root, Path)
        or not bundle_root.is_absolute()
    ):
        raise PublicationError(
            "canonical Task Witness supervisor requires its closed internal call shape"
        )
    arguments = [
        str(observation.path),
        "validate",
        "--bundle",
        str(bundle_root),
    ]
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    retained: set[_ProcessIdentity] = set()
    failure: str | None = None
    primary_error: BaseException | None = None
    started = time.monotonic()
    outer_deadline = started + SETTLEMENT_SECONDS
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
            env={},
            cwd=Path("/"),
        )
        assert process.stdout is not None and process.stderr is not None
        for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if now - started >= TIMEOUT_SECONDS:
                failure = "canonical Task Witness validation timed out"
                break
            if now >= outer_deadline:
                failure = "canonical Task Witness validation did not settle"
                break
            _sample_descendants(process.pid, retained, deadline=outer_deadline)
            for key, _ in selector.select(timeout=0.05):
                try:
                    chunk = os.read(key.fileobj.fileno(), IO_CHUNK_BYTES)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = stdout if key.data == "stdout" else stderr
                maximum = MAX_STDOUT_BYTES if key.data == "stdout" else MAX_STDERR_BYTES
                remaining = maximum - len(target)
                if remaining > 0:
                    target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    failure = "canonical Task Witness output exceeded its bound"
                    break
            if failure is not None:
                break
        if failure is not None:
            raise PublicationError(failure)
        status = process.wait()
        return status, bytes(stdout), bytes(stderr)
    except OSError as error:
        unavailable = PublicationError(
            "canonical Task Witness validation is unavailable"
        )
        primary_error = unavailable
        raise unavailable from error
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        if process is not None:
            try:
                _settle(process, retained, deadline=outer_deadline)
            except BaseException as error:
                cleanup_errors.append(error)
        for resource in (
            selector,
            process.stdout if process is not None else None,
            process.stderr if process is not None else None,
        ):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:
                    cleanup_errors.append(error)
        if cleanup_errors:
            first_cleanup_error = cleanup_errors[0]
            for later_error in cleanup_errors[1:]:
                first_cleanup_error.add_note(
                    f"Additional Task Witness cleanup failure: {later_error!r}"
                )
            if primary_error is None:
                cleanup_failure = PublicationError(
                    "canonical Task Witness process cleanup failed"
                )
                for cleanup_error in cleanup_errors:
                    cleanup_failure.add_note(
                        f"Task Witness cleanup failure: {cleanup_error!r}"
                    )
                raise cleanup_failure from first_cleanup_error
            for cleanup_error in cleanup_errors:
                primary_error.add_note(
                    f"Task Witness cleanup also failed: {cleanup_error!r}"
                )


def _supervised_process(
    observation: object, bundle_root: Path
) -> tuple[int, bytes, bytes]:
    raise PublicationError(
        "canonical Task Witness native validation is unavailable in this "
        "source-stage release"
    )


def _retired_invoke_task_witness(bundle_root: Path) -> bytes:
    # Retained for source-stage design validation only. The current-version
    # boundary below never selects this implementation.
    with _authenticated_front_door() as observation:
        status, stdout, stderr = _retired_supervised_process(
            observation, bundle_root
        )
        if status != 0 or stderr:
            raise PublicationError("canonical Task Witness validation did not succeed")
        envelope = _strict_envelope(stdout)
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"contract", "anchor", "witness"}
            or envelope.get("contract") != ENVELOPE_CONTRACT
        ):
            raise PublicationError("canonical Task Witness envelope framing drift")
    return stdout


def _invoke_task_witness(bundle_root: Path) -> bytes:
    raise PublicationError(
        "canonical Task Witness native validation is unavailable in this "
        "source-stage release"
    )


def _strict_envelope(raw: bytes) -> dict[str, Any]:
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise PublicationError("canonical Task Witness envelope framing drift")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PublicationError(
                    "canonical Task Witness envelope has duplicate keys"
                )
            result[key] = value
        return result

    def reject_constant(_: str) -> None:
        raise PublicationError("canonical Task Witness envelope has non-finite JSON")

    try:
        envelope = json.loads(
            raw[:-1].decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicationError("canonical Task Witness envelope is invalid") from error
    if raw != _canonical(envelope) + b"\n":
        raise PublicationError("canonical Task Witness envelope is not canonical")
    return envelope


def _validate_envelope(
    envelope: Any, raw: bytes, candidate: PublicationCandidate
) -> RequiredReviewObservation:
    envelope = _exact(
        envelope, {"contract", "anchor", "witness"}, "Task Witness envelope"
    )
    if envelope["contract"] != ENVELOPE_CONTRACT:
        raise PublicationError("Task Witness envelope contract drift")
    anchor = _exact(
        envelope["anchor"],
        {
            "contract",
            "generation",
            "active_record_sha256",
            "runtime_contract",
            "interpreter",
            "public_release",
            "runtime_implementation_sha256",
            "trust_context_sha256",
            "bundle_sha256",
            "historical",
        },
        "Task Witness anchor",
    )
    generation = anchor["generation"]
    if (
        anchor["contract"] != "task-witness-complete-anchor-v1"
        or anchor["runtime_contract"] != "task-witness-runtime-v1"
        or anchor["historical"] is not False
        or not isinstance(generation, str)
        or not generation.startswith("sha256-")
        or SHA256_RE.fullmatch(generation.removeprefix("sha256-")) is None
    ):
        raise PublicationError("Task Witness current complete anchor is unavailable")
    for digest_field in (
        "active_record_sha256",
        "runtime_implementation_sha256",
        "trust_context_sha256",
        "bundle_sha256",
    ):
        _sha256(anchor[digest_field], f"Task Witness anchor {digest_field}")
    _exact(
        anchor["interpreter"],
        {"executable", "implementation", "version"},
        "Task Witness interpreter",
    )
    release = _exact(
        anchor["public_release"],
        {"repository", "revision"},
        "Task Witness public release",
    )
    if (
        not isinstance(release["repository"], str)
        or SHA1_RE.fullmatch(release["revision"]) is None
    ):
        raise PublicationError("Task Witness public release identity is invalid")

    witness = _exact(
        envelope["witness"],
        {
            "contract",
            "bundle_sha256",
            "producer",
            "validator",
            "projection",
            "trust_context_sha256",
            "historical",
        },
        "Task Witness witness",
    )
    if (
        witness["contract"] != WITNESS_CONTRACT
        or witness["historical"] is not False
        or witness["bundle_sha256"] != anchor["bundle_sha256"]
        or witness["trust_context_sha256"] != anchor["trust_context_sha256"]
    ):
        raise PublicationError("Task Witness current witness binding drift")
    producer = _exact(
        witness["producer"],
        {
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
        },
        "Task Witness producer",
    )
    validator = _exact(
        witness["validator"],
        {"validator_id", "contract", "implementation_sha256"},
        "Task Witness validator",
    )
    if (
        producer["producer_id"] != PRODUCER_ID
        or producer["contract"] != EVIDENCE_CONTRACT
        or producer["validator_id"] != VALIDATOR_ID
        or producer["validator_contract"] != EVIDENCE_CONTRACT
        or validator["validator_id"] != producer["validator_id"]
        or validator["contract"] != producer["validator_contract"]
        or validator["implementation_sha256"]
        != producer["validator_implementation_sha256"]
    ):
        raise PublicationError("Task Witness registered producer chain drift")
    for value in (
        producer["implementation_sha256"],
        producer["validator_implementation_sha256"],
    ):
        _sha256(value, "Task Witness producer implementation")

    projection = _exact(
        witness["projection"],
        {
            "schema_version",
            "contract",
            "evidence_contract",
            "manifest_sha256",
            "subject",
            "review_profile",
            "final_dispatch",
            "terminal",
            "content_sha256",
        },
        "Tricritical terminal projection",
    )
    if (
        type(projection["schema_version"]) is not int
        or projection["schema_version"] != 1
        or projection["contract"] != PROJECTION_CONTRACT
        or projection["evidence_contract"] != EVIDENCE_CONTRACT
    ):
        raise PublicationError("Tricritical terminal projection contract drift")
    manifest_sha256 = _sha256(projection["manifest_sha256"], "Tricritical manifest")
    projection_sha256 = _sha256(projection["content_sha256"], "Tricritical projection")
    unsigned_projection = dict(projection)
    del unsigned_projection["content_sha256"]
    if _sha(_canonical(unsigned_projection)) != projection_sha256:
        raise PublicationError("Tricritical terminal projection content drift")
    subject = _exact(
        projection["subject"],
        {"candidate", "review_input", "requirements"},
        "Tricritical subject",
    )
    if (
        _identity(subject["candidate"], "Tricritical candidate")
        != candidate.candidate_identity
        or _identity(subject["review_input"], "Tricritical review input")
        != candidate.review_input_identity
        or _identity(subject["requirements"], "Tricritical requirements")
        != candidate.requirements_identity
    ):
        raise PublicationError("Tricritical publication subject drift")
    profile = _exact(
        projection["review_profile"],
        {"contract", "execution_mode", "required_axes", "selected_specialists"},
        "Tricritical review profile",
    )
    specialists = profile["selected_specialists"]
    if (
        profile["contract"] != "tricritical-review-profile-v1"
        or profile["execution_mode"] != "independent"
        or profile["required_axes"] != ["intent", "runtime", "structure"]
        or not isinstance(specialists, list)
        or not all(isinstance(item, str) and item for item in specialists)
        or specialists != sorted(set(specialists))
    ):
        raise PublicationError("Tricritical publication review profile drift")
    if specialists != candidate.value["publication_profile"]["selected_specialists"]:
        raise PublicationError("Tricritical selected specialist inventory drift")
    _validate_dispatch(projection["final_dispatch"], candidate, specialists)
    terminal = _exact(
        projection["terminal"],
        {
            "state",
            "owner",
            "limitations",
            "missing_executions",
            "unresolved_actionable_findings",
            "verification",
        },
        "Tricritical terminal",
    )
    verification = _exact(
        terminal["verification"],
        {"status", "candidate", "evidence", "unchanged"},
        "Tricritical verification",
    )
    if (
        terminal["state"] != "clean"
        or terminal["owner"] != "none"
        or terminal["limitations"] != []
        or terminal["missing_executions"] != []
        or type(terminal["unresolved_actionable_findings"]) is not int
        or terminal["unresolved_actionable_findings"] != 0
        or verification["status"] != "passed"
        or verification["candidate"] != candidate.candidate_identity
        or verification["unchanged"] is not True
    ):
        raise PublicationError("Tricritical publication review is not bare clean")
    _identity(verification["evidence"], "Tricritical verification evidence")
    return RequiredReviewObservation(
        launch_envelope_sha256=_sha(raw),
        generation=generation,
        active_record_sha256=anchor["active_record_sha256"],
        trust_context_sha256=anchor["trust_context_sha256"],
        bundle_sha256=anchor["bundle_sha256"],
        tricritical_manifest_sha256=manifest_sha256,
        tricritical_projection_sha256=projection_sha256,
    )


def _validate_dispatch(
    value: Any, candidate: PublicationCandidate, specialists: list[str]
) -> None:
    if not isinstance(value, dict):
        raise PublicationError("Rolecasting publication dispatch is unavailable")
    if (
        value.get("contract") != "rolecasting-dispatch-projection-v2"
        or value.get("evidence_contract") != "rolecasting-dispatch-evidence-v2"
    ):
        raise PublicationError("Rolecasting publication dispatch contract drift")
    executions = value.get("executions")
    if not isinstance(executions, dict):
        raise PublicationError("Rolecasting publication execution inventory drift")
    expected_roles = {
        "critic-intent",
        "critic-runtime",
        "critic-structure",
        *(f"specialist-{specialist}" for specialist in specialists),
    }
    roles: list[str] = []
    sessions: list[str] = []
    contexts: list[str] = []
    for execution_id, execution in executions.items():
        if not isinstance(execution_id, str) or not isinstance(execution, dict):
            raise PublicationError("Rolecasting publication execution inventory drift")
        role = execution.get("role")
        target = execution.get("target")
        topology = execution.get("topology")
        assurance = execution.get("assurance")
        assurance_minimum = execution.get("assurance_minimum")
        authority = execution.get("authority")
        isolation = execution.get("isolation")
        if (
            execution.get("execution_id") != execution_id
            or not isinstance(role, str)
            or execution.get("candidate") != candidate.candidate_identity
            or execution.get("model") != "gpt-5.6-sol"
            or execution.get("reasoning_effort") != "high"
            or execution.get("user_authority") is not None
            or execution.get("return_contract") != "tricritical-raw-report-v1"
            or not isinstance(target, dict)
            or not isinstance(topology, dict)
            or not isinstance(assurance, dict)
            or not isinstance(assurance_minimum, dict)
            or not isinstance(authority, dict)
            or not isinstance(isolation, dict)
        ):
            raise PublicationError("Rolecasting publication execution profile drift")
        if set(target) != {"product_family", "surface", "executor", "version"} or (
            target["product_family"] != "codex"
            or target["surface"] != "chatgpt-codex"
            or target["executor"] != "codex"
            or not isinstance(target["version"], str)
            or not target["version"]
        ):
            raise PublicationError("Rolecasting publication execution target drift")
        if topology != {
            "relationship": "child",
            "ownership": "leader-owned",
            "transport": "native-tool",
        }:
            raise PublicationError("Rolecasting publication execution topology drift")
        if set(assurance) != {
            "target",
            "model",
            "topology",
            "authority",
            "execution_result",
            "evidence",
        } or any(
            assurance[field] != "product-attested"
            for field in (
                "target",
                "model",
                "topology",
                "authority",
                "execution_result",
            )
        ):
            raise PublicationError("Rolecasting publication execution assurance drift")
        _identity(assurance["evidence"], "Rolecasting execution assurance evidence")
        if assurance_minimum != {
            "target": "product-attested",
            "model": "product-attested",
            "topology": "product-attested",
            "authority": "product-attested",
            "execution_result": "product-attested",
        }:
            raise PublicationError(
                "Rolecasting publication execution assurance minimum drift"
            )
        _identity(execution.get("scope"), "Rolecasting execution scope")
        _identity(execution.get("request"), "Rolecasting execution request")
        if set(authority) != {
            "access",
            "subdelegation",
            "external_action",
            "evidence",
        } or (
            authority["access"] != "read-only"
            or authority["subdelegation"] is not False
            or authority["external_action"] is not False
        ):
            raise PublicationError("Rolecasting publication execution authority drift")
        _identity(authority["evidence"], "Rolecasting execution authority evidence")
        if set(isolation) != {"session", "context", "enforceable"} or (
            not isinstance(isolation["session"], str)
            or not isolation["session"]
            or not isinstance(isolation["context"], str)
            or not isolation["context"]
            or isolation["enforceable"] is not True
        ):
            raise PublicationError("Rolecasting publication execution isolation drift")
        if (
            execution.get("verification_contract") != "review-verification-v1"
            or execution.get("stop_contract") != "review-stop-v1"
            or execution.get("usable") is not True
        ):
            raise PublicationError("Rolecasting publication execution return drift")
        returned = _identity(execution.get("returned"), "Rolecasting execution return")
        verification = _identity(
            execution.get("verification"), "Rolecasting execution verification"
        )
        stopped = _identity(execution.get("stop"), "Rolecasting execution stop")
        if (
            returned["kind"] != execution["return_contract"]
            or verification["kind"] != execution["verification_contract"]
            or stopped["kind"] != execution["stop_contract"]
        ):
            raise PublicationError("Rolecasting publication execution return drift")
        roles.append(role)
        sessions.append(isolation["session"])
        contexts.append(isolation["context"])
    if (
        set(roles) != expected_roles
        or len(roles) != len(expected_roles)
        or len(sessions) != len(set(sessions))
        or len(contexts) != len(set(contexts))
    ):
        raise PublicationError("Rolecasting publication execution inventory drift")


def validate_required_review(
    *,
    review_mode: str,
    review_bundle_root: Path | None,
    candidate: PublicationCandidate,
    expected_observation: RequiredReviewObservation | None = None,
) -> PublicationReview:
    """Return the explicit publication-review choice and canonical evidence binding."""

    if review_mode not in REVIEW_MODES:
        raise PublicationError("review mode must be required or not-required")
    candidate_mode = candidate.value["publication_profile"]["review_mode"]
    if candidate_mode != review_mode:
        raise PublicationError("publication candidate review mode drift")
    if review_mode == "not-required":
        if review_bundle_root is not None or expected_observation is not None:
            raise PublicationError("not-required review mode cannot claim evidence")
        return _make_publication_review(
            "not-required",
            candidate.content_sha256,
            None,
            transition_validated=True,
        )
    if review_bundle_root is None or not review_bundle_root.is_absolute():
        raise PublicationError("required review needs an absolute evidence bundle root")
    raw = _invoke_task_witness(review_bundle_root)
    observation = validate_required_review_envelope(raw, candidate)
    if expected_observation is not None and observation != expected_observation:
        raise PublicationError(
            "required review evidence changed under publication lease"
        )
    return _make_publication_review(
        "required",
        candidate.content_sha256,
        observation,
        transition_validated=True,
    )


def validate_required_review_envelope(
    raw: bytes, candidate: PublicationCandidate
) -> RequiredReviewObservation:
    """Validate canonical Task Witness output against one frozen candidate."""

    return _validate_envelope(_strict_envelope(raw), raw, candidate)


def parse_publication_review(value: Any) -> PublicationReview:
    """Parse one redacted v3 canonical-receipt review binding."""

    value = _exact(
        value,
        {"mode", "publication_candidate_sha256", "observation"},
        "receipt review",
    )
    mode = value["mode"]
    if mode not in REVIEW_MODES:
        raise PublicationError("receipt review mode is invalid")
    candidate_sha256 = _sha256(
        value["publication_candidate_sha256"], "receipt publication candidate"
    )
    if mode == "not-required":
        if value["observation"] is not None:
            raise PublicationError("not-required receipt cannot claim review evidence")
        return _make_publication_review(
            "not-required",
            candidate_sha256,
            None,
            transition_validated=False,
        )
    observation = _exact(
        value["observation"],
        {"contract", "launch_envelope_sha256", "task_witness", "tricritical"},
        "receipt required-review observation",
    )
    if observation["contract"] != OBSERVATION_CONTRACT:
        raise PublicationError("receipt required-review observation contract drift")
    task_witness = _exact(
        observation["task_witness"],
        {"generation", "active_record_sha256", "trust_context_sha256", "bundle_sha256"},
        "receipt Task Witness observation",
    )
    tricritical = _exact(
        observation["tricritical"],
        {"manifest_sha256", "projection_content_sha256"},
        "receipt Tricritical observation",
    )
    generation = task_witness["generation"]
    if (
        not isinstance(generation, str)
        or not generation.startswith("sha256-")
        or SHA256_RE.fullmatch(generation.removeprefix("sha256-")) is None
    ):
        raise PublicationError("receipt Task Witness generation is invalid")
    parsed = RequiredReviewObservation(
        launch_envelope_sha256=_sha256(
            observation["launch_envelope_sha256"], "receipt launch envelope"
        ),
        generation=generation,
        active_record_sha256=_sha256(
            task_witness["active_record_sha256"], "receipt active record"
        ),
        trust_context_sha256=_sha256(
            task_witness["trust_context_sha256"], "receipt trust context"
        ),
        bundle_sha256=_sha256(task_witness["bundle_sha256"], "receipt bundle"),
        tricritical_manifest_sha256=_sha256(
            tricritical["manifest_sha256"], "receipt Tricritical manifest"
        ),
        tricritical_projection_sha256=_sha256(
            tricritical["projection_content_sha256"],
            "receipt Tricritical projection",
        ),
    )
    return _make_publication_review(
        "required",
        candidate_sha256,
        parsed,
        transition_validated=False,
    )
