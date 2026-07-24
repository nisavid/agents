#!/usr/bin/env python3
"""Plan one Graphite draft transport and emit an exact publisher handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

PUBLISHER_SCRIPTS = Path(__file__).parents[2] / "publishing-reviewable-prs/scripts"
WRITER_SCRIPTS = (
    Path(__file__).parents[2] / "writing-reviewable-pr-descriptions/scripts"
)
for scripts in (PUBLISHER_SCRIPTS, WRITER_SCRIPTS):
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

from change_navigation.review_input import (  # noqa: E402
    PR_NUMBER_TOKEN,
    ReviewInputError,
    bind_review_input,
    load_review_input,
)
from change_navigation.sensitive_content import suspected_secret_error  # noqa: E402
from publication_receipts import (  # noqa: E402
    creation_transaction_lock,
    prepare_receipt_store,
)
from reviewable_pr_state import (  # noqa: E402
    ExpectedIdentity,
    PublicationError,
    head_base_matches,
    identity_matches,
    open_prs,
    validate_identity_inputs,
)


SCHEMA_VERSION = 1
READ_TIMEOUT_SECONDS = 30
MUTATION_TIMEOUT_SECONDS = 300
VALIDATION_PR_NUMBER = 2_147_483_647
VALIDATOR = WRITER_SCRIPTS / "validate_change_navigation.py"
UPDATE = PUBLISHER_SCRIPTS / "update_reviewable_pr.py"
AUDIT = PUBLISHER_SCRIPTS / "audit_reviewable_pr.py"


class GraphiteTransportError(PublicationError):
    """The requested Graphite transport cannot be bound safely."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode("utf-8"))


def _exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise GraphiteTransportError(f"{label} has unsupported or missing fields")


def _parse_strict_json(value: str, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise GraphiteTransportError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise GraphiteTransportError(f"non-finite JSON value: {value}")

    try:
        parsed = json.loads(
            value,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise GraphiteTransportError(f"{label} is not strict JSON") from error
    if not isinstance(parsed, dict):
        raise GraphiteTransportError(f"{label} must be a JSON object")
    return parsed


def _strict_json(path: Path) -> dict[str, Any]:
    if not path.is_absolute():
        raise GraphiteTransportError("request and plan paths must be absolute")
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphiteTransportError(f"cannot read strict JSON: {error}") from error
    return _parse_strict_json(value, "JSON document")


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    if not path.is_absolute():
        raise GraphiteTransportError("output path must be absolute")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(_canonical(value) + b"\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()
    except OSError as error:
        raise GraphiteTransportError(f"cannot write output: {error}") from error


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in tuple(environment):
        upper = key.upper()
        if (
            upper in {"GH_HOST", "GITHUB_HOST"}
            or upper.startswith("GH_ENTERPRISE_")
            or upper.startswith("GITHUB_ENTERPRISE_")
        ):
            environment.pop(key, None)
    environment["GH_HOST"] = "github.com"
    environment["GITHUB_HOST"] = "github.com"
    return environment


def _run(
    arguments: list[str], *, cwd: Path, timeout: int = READ_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            env=_environment(),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GraphiteTransportError(
            f"command did not complete: {arguments[0]}"
        ) from error
    if result.returncode != 0:
        raise GraphiteTransportError(
            f"command failed with status {result.returncode}: {arguments[0]}"
        )
    return result


def _read_text(path: Path, label: str) -> str:
    if not path.is_absolute():
        raise GraphiteTransportError(f"{label} path must be absolute")
    try:
        value = path.read_text(encoding="utf-8")
    except OSError as error:
        raise GraphiteTransportError(f"cannot read {label}: {error}") from error
    if suspected_secret_error(value) is not None:
        raise GraphiteTransportError(
            f"{label} contains a suspected credential or secret"
        )
    return value


def _matching_prs(entry: dict[str, Any], repository: str) -> list[dict[str, Any]]:
    return [
        stored
        for stored in open_prs(repository, entry["base"], entry["head"])
        if head_base_matches(
            stored,
            base=entry["base"],
            head=entry["head"],
            head_owner=entry["head_owner"],
            head_repository=entry["head_repository"],
        )
    ]


def _candidate(entry: dict[str, Any], repository: str) -> dict[str, Any]:
    _exact_keys(
        entry,
        {
            "base",
            "base_oid",
            "head",
            "head_oid",
            "head_owner",
            "head_repository",
            "title",
            "body_source",
            "review_input",
        },
        "stack entry",
    )
    body_source = entry["body_source"]
    if not isinstance(body_source, dict):
        raise GraphiteTransportError("stack entry body_source must be an object")
    _exact_keys(body_source, {"mode", "path"}, "stack entry body_source")
    if body_source["mode"] not in {"file", "template"}:
        raise GraphiteTransportError("body_source mode must be file or template")
    if not isinstance(entry["title"], str) or not entry["title"].strip():
        raise GraphiteTransportError("stack entry title must be non-empty")
    if suspected_secret_error(entry["title"]) is not None:
        raise GraphiteTransportError("stack entry title contains a suspected secret")
    validate_identity_inputs(
        repository=repository,
        pr_number=None,
        base=entry["base"],
        base_oid=entry["base_oid"],
        head=entry["head"],
        head_oid=entry["head_oid"],
        head_owner=entry["head_owner"],
        head_repository=entry["head_repository"],
    )
    local_branch = entry["head"].split(":", 1)[1]
    source_path = Path(body_source["path"])
    review_input_path = Path(entry["review_input"])
    source = _read_text(source_path, "body source")
    try:
        review_input = load_review_input(review_input_path)
    except ReviewInputError as error:
        raise GraphiteTransportError(f"invalid review input: {error}") from error
    if body_source["mode"] == "template":
        if PR_NUMBER_TOKEN not in source:
            raise GraphiteTransportError(
                f"new-PR body template must contain {PR_NUMBER_TOKEN}"
            )
        if review_input.pr_number != PR_NUMBER_TOKEN:
            raise GraphiteTransportError(
                "template transport requires a new-PR review input"
            )
        rendered = source.replace(PR_NUMBER_TOKEN, str(VALIDATION_PR_NUMBER))
        template = source
        pr_number = VALIDATION_PR_NUMBER
    else:
        if type(review_input.pr_number) is not int:
            raise GraphiteTransportError(
                "existing transport requires a numbered review input"
            )
        rendered = source
        template = None
        pr_number = review_input.pr_number
    try:
        bind_review_input(
            review_input,
            repository=repository,
            pr_number=pr_number,
            base=entry["base"],
            base_oid=entry["base_oid"],
            head=entry["head"],
            head_oid=entry["head_oid"],
            head_owner=entry["head_owner"],
            head_repository=entry["head_repository"],
            title=entry["title"],
            body=rendered,
            template_body=template,
        )
    except ReviewInputError as error:
        raise GraphiteTransportError(f"review input drift: {error}") from error
    arguments = [
        sys.executable,
        str(VALIDATOR),
        "/dev/stdin",
        "--repository",
        repository,
        "--pr",
        str(pr_number),
        "--title",
        entry["title"],
        "--review-input",
        str(review_input_path),
    ]
    if template is not None:
        arguments.extend(["--template-body", str(source_path)])
    try:
        result = subprocess.run(
            arguments,
            input=rendered,
            text=True,
            capture_output=True,
            check=False,
            timeout=READ_TIMEOUT_SECONDS,
            env=_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GraphiteTransportError("candidate validation did not complete") from error
    if result.returncode != 0:
        raise GraphiteTransportError("candidate body failed canonical validation")
    return {
        **entry,
        "local_branch": local_branch,
        "body_source_sha256": _sha_text(source),
        "review_input_sha256": review_input.content_sha256,
        "review_input_pr": review_input.pr_number,
    }


def _git_graphite_snapshot(root: Path, current_branch: str) -> dict[str, Any]:
    resolved = Path(
        _run(["git", "rev-parse", "--show-toplevel"], cwd=root).stdout.strip()
    ).resolve()
    if resolved != root.resolve():
        raise GraphiteTransportError("repository root does not match Git")
    status = _run(["git", "status", "--porcelain=v1"], cwd=root).stdout
    if status:
        raise GraphiteTransportError("Graphite transport requires a clean worktree")
    branch = _run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=root
    ).stdout.strip()
    if branch != current_branch:
        raise GraphiteTransportError("current branch changed before transport")
    log_short = _run(["gt", "log", "short"], cwd=root).stdout
    trunk = _run(["gt", "trunk"], cwd=root).stdout
    return {
        "repository_root": str(root.resolve()),
        "current_branch": branch,
        "clean_status_sha256": _sha_text(status),
        "gt_log_short_sha256": _sha_text(log_short),
        "gt_trunk_sha256": _sha_text(trunk),
    }


def _live_preimage(entry: dict[str, Any], repository: str) -> dict[str, Any] | None:
    matches = _matching_prs(entry, repository)
    mode = entry["body_source"]["mode"]
    if mode == "template":
        if matches:
            raise GraphiteTransportError(
                "new-PR transport found an existing open PR for its head/base"
            )
        return None
    if len(matches) != 1:
        raise GraphiteTransportError(
            "existing transport requires exactly one open PR for its head/base"
        )
    stored = matches[0]
    if stored.get("number") != entry["review_input_pr"]:
        raise GraphiteTransportError("existing PR number differs from review input")
    title = stored.get("title")
    body = stored.get("body")
    is_draft = stored.get("isDraft")
    if (
        not isinstance(title, str)
        or not isinstance(body, str)
        or type(is_draft) is not bool
    ):
        raise GraphiteTransportError("existing PR preimage is unreadable")
    if (
        suspected_secret_error(title) is not None
        or suspected_secret_error(body) is not None
    ):
        raise GraphiteTransportError("existing PR contains a suspected secret")
    try:
        review_input = load_review_input(Path(entry["review_input"]))
        bind_review_input(
            review_input,
            repository=repository,
            pr_number=int(stored["number"]),
            base=entry["base"],
            base_oid=entry["base_oid"],
            head=entry["head"],
            head_oid=entry["head_oid"],
            head_owner=entry["head_owner"],
            head_repository=entry["head_repository"],
            title=entry["title"],
            body=_read_text(Path(entry["body_source"]["path"]), "body source"),
            stored_title=title,
            stored_body=body,
        )
    except ReviewInputError as error:
        raise GraphiteTransportError(f"existing PR baseline drift: {error}") from error
    return {
        "number": stored["number"],
        "url": stored["url"],
        "title_sha256": _sha_text(title),
        "body_sha256": _sha_text(body),
        "is_draft": is_draft,
    }


def build_plan(request: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(
        request,
        {"schema_version", "repository", "repository_root", "current_branch", "stack"},
        "request",
    )
    if request["schema_version"] != SCHEMA_VERSION:
        raise GraphiteTransportError(f"request schema_version must be {SCHEMA_VERSION}")
    if not isinstance(request["repository"], str):
        raise GraphiteTransportError("repository must be OWNER/REPO")
    root = Path(request["repository_root"])
    if not root.is_absolute() or not root.is_dir():
        raise GraphiteTransportError("repository_root must be an absolute directory")
    if not isinstance(request["current_branch"], str) or not request["current_branch"]:
        raise GraphiteTransportError("current_branch must be non-empty")
    if not isinstance(request["stack"], list) or not request["stack"]:
        raise GraphiteTransportError("stack must be a non-empty list")
    candidates = [_candidate(item, request["repository"]) for item in request["stack"]]
    if candidates[-1]["local_branch"] != request["current_branch"]:
        raise GraphiteTransportError("current branch must be the top stack entry")
    for index, entry in enumerate(candidates):
        local_oid = _run(
            ["git", "rev-parse", f"refs/heads/{entry['local_branch']}^{{commit}}"],
            cwd=root,
        ).stdout.strip()
        if local_oid != entry["head_oid"]:
            raise GraphiteTransportError("local stack head OID differs from request")
        base_oid = _run(
            ["git", "rev-parse", f"refs/heads/{entry['base']}^{{commit}}"],
            cwd=root,
        ).stdout.strip()
        if base_oid != entry["base_oid"]:
            raise GraphiteTransportError("local stack base OID differs from request")
        if index and entry["base"] != candidates[index - 1]["local_branch"]:
            raise GraphiteTransportError("stack entries are not a bottom-to-top chain")
    snapshot = _git_graphite_snapshot(root, request["current_branch"])
    preimages = [_live_preimage(entry, request["repository"]) for entry in candidates]
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "request": request,
        "candidates": candidates,
        "snapshot": snapshot,
        "preimages": preimages,
    }
    return {**unsigned, "content_sha256": _sha_bytes(_canonical(unsigned))}


def _load_plan(path: Path) -> dict[str, Any]:
    plan = _strict_json(path)
    _exact_keys(
        plan,
        {
            "schema_version",
            "request",
            "candidates",
            "snapshot",
            "preimages",
            "content_sha256",
        },
        "plan",
    )
    supplied = plan["content_sha256"]
    unsigned = dict(plan)
    del unsigned["content_sha256"]
    if not isinstance(supplied, str) or supplied != _sha_bytes(_canonical(unsigned)):
        raise GraphiteTransportError("plan content digest does not match")
    return plan


def _identity(
    entry: dict[str, Any], stored: dict[str, Any], repository: str
) -> ExpectedIdentity:
    number = stored.get("number")
    if type(number) is not int or number <= 0:
        raise GraphiteTransportError("transport PR has an invalid number")
    expected = ExpectedIdentity(
        repository=repository,
        pr_number=number,
        base=entry["base"],
        base_oid=entry["base_oid"],
        head=entry["head"],
        head_oid=entry["head_oid"],
        head_owner=entry["head_owner"],
        head_repository=entry["head_repository"],
    )
    if not identity_matches(stored, expected):
        raise GraphiteTransportError("transport PR identity or pushed OIDs differ")
    return expected


def _common_arguments(expected: ExpectedIdentity, entry: dict[str, Any]) -> list[str]:
    return [
        "--repository",
        expected.repository,
        "--pr",
        str(expected.pr_number),
        "--base",
        expected.base,
        "--base-oid",
        expected.base_oid,
        "--head",
        expected.head,
        "--head-oid",
        expected.head_oid,
        "--head-owner",
        expected.head_owner,
        "--head-repository",
        expected.head_repository,
        "--review-input",
        entry["review_input"],
    ]


def _handoff_entry(
    entry: dict[str, Any],
    preimage: dict[str, Any] | None,
    stored: dict[str, Any],
    repository: str,
) -> dict[str, Any]:
    expected = _identity(entry, stored, repository)
    title = stored.get("title")
    body = stored.get("body")
    is_draft = stored.get("isDraft")
    if (
        not isinstance(title, str)
        or not isinstance(body, str)
        or type(is_draft) is not bool
    ):
        raise GraphiteTransportError("transport PR state is unreadable")
    if (
        suspected_secret_error(title) is not None
        or suspected_secret_error(body) is not None
    ):
        raise GraphiteTransportError("transport PR contains a suspected secret")
    if preimage is None and not is_draft:
        raise GraphiteTransportError("new Graphite PR was not created as a draft")
    if preimage is not None and preimage["is_draft"] is True and not is_draft:
        raise GraphiteTransportError("Graphite unexpectedly marked a draft PR ready")
    source_path = Path(entry["body_source"]["path"])
    source = _read_text(source_path, "body source")
    target_body = (
        source.replace(PR_NUMBER_TOKEN, str(expected.pr_number))
        if entry["body_source"]["mode"] == "template"
        else source
    )
    common = _common_arguments(expected, entry)
    commands: list[list[str]] = []
    if title != entry["title"] or body != target_body:
        body_flag = (
            "--body-template"
            if entry["body_source"]["mode"] == "template"
            else "--body-file"
        )
        commands.append(
            [
                sys.executable,
                str(UPDATE),
                "text",
                *common,
                "--expected-title-sha256",
                _sha_text(title),
                "--expected-body-sha256",
                _sha_text(body),
                "--expected-state",
                "draft" if is_draft else "ready",
                "--text-scope",
                "title-body",
                "--title",
                entry["title"],
                body_flag,
                str(source_path),
            ]
        )
    if preimage is not None and preimage["is_draft"] is False and is_draft:
        commands.append(
            [
                sys.executable,
                str(UPDATE),
                "ready",
                *common,
                "--expected-title-sha256",
                _sha_text(entry["title"]),
                "--expected-body-sha256",
                _sha_text(target_body),
            ]
        )
    audit = [sys.executable, str(AUDIT), "audit", *common]
    reconcile = [sys.executable, str(AUDIT), "reconcile", *common]
    return {
        "repository": repository,
        "pr": expected.pr_number,
        "url": expected.url,
        "base": expected.base,
        "base_oid": expected.base_oid,
        "head": expected.head,
        "head_oid": expected.head_oid,
        "is_draft": is_draft,
        "transport_title_sha256": _sha_text(title),
        "transport_body_sha256": _sha_text(body),
        "target_title_sha256": _sha_text(entry["title"]),
        "target_body_sha256": _sha_text(target_body),
        "target_is_draft": is_draft,
        "target_review_input_sha256": entry["review_input_sha256"],
        "target_identity_epoch": {
            "repository": repository,
            "pr_number": expected.pr_number,
            "url": expected.url,
            "base": expected.base,
            "base_oid": expected.base_oid,
            "head": expected.head,
            "head_oid": expected.head_oid,
            "head_owner": expected.head_owner,
            "head_repository": expected.head_repository,
        },
        "publisher_commands": commands,
        "final_audit_command": audit,
        "no_transition_reconcile_command": reconcile,
    }


def execute(plan: dict[str, Any], output_path: Path) -> dict[str, Any]:
    receipt_root = prepare_receipt_store()
    root = Path(plan["request"]["repository_root"])
    lock_entries = sorted(
        plan["candidates"],
        key=lambda entry: (
            entry["base"],
            entry["head"],
            entry["head_owner"],
            entry["head_repository"],
        ),
    )
    with ExitStack() as stack:
        for entry in lock_entries:
            stack.enter_context(
                creation_transaction_lock(
                    receipt_root,
                    repository=plan["request"]["repository"],
                    base=entry["base"],
                    head=entry["head"],
                    head_owner=entry["head_owner"],
                    head_repository=entry["head_repository"],
                )
            )
        rebuilt = build_plan(plan["request"])
        if rebuilt != plan:
            raise GraphiteTransportError(
                "live state or candidate inputs drifted from plan"
            )
        command_error: str | None = None
        try:
            _run(
                [
                    "gt",
                    "submit",
                    "--stack",
                    "--draft",
                    "--no-edit",
                    "--no-ai",
                    "--no-interactive",
                ],
                cwd=root,
                timeout=MUTATION_TIMEOUT_SECONDS,
            )
        except GraphiteTransportError as error:
            command_error = str(error)
        handoffs: list[dict[str, Any]] = []
        failures: list[str] = []
        for index, entry in enumerate(plan["candidates"]):
            try:
                matches = _matching_prs(entry, plan["request"]["repository"])
                if len(matches) != 1:
                    raise GraphiteTransportError(
                        "transport did not yield exactly one open PR for the head/base"
                    )
                handoffs.append(
                    _handoff_entry(
                        entry,
                        plan["preimages"][index],
                        matches[0],
                        plan["request"]["repository"],
                    )
                )
            except GraphiteTransportError as error:
                failures.append(f"stack[{index}]: {error}")
    status = (
        "transport-complete-repair-required"
        if not failures
        else "transport-ambiguous-inspection-required"
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "plan_sha256": plan["content_sha256"],
        "repository_root": str(root.resolve()),
        "transport_command_error": command_error,
        "pull_requests": handoffs,
        "failures": failures,
    }
    unsigned = dict(result)
    result["content_sha256"] = _sha_bytes(_canonical(unsigned))
    _write_private_json(output_path, result)
    if failures:
        raise GraphiteTransportError(
            "Graphite transport is ambiguous; inspect the private handoff output"
        )
    return result


def _load_handoff(path: Path) -> dict[str, Any]:
    handoff = _strict_json(path)
    _exact_keys(
        handoff,
        {
            "schema_version",
            "status",
            "plan_sha256",
            "repository_root",
            "transport_command_error",
            "pull_requests",
            "failures",
            "content_sha256",
        },
        "handoff",
    )
    supplied = handoff["content_sha256"]
    unsigned = dict(handoff)
    del unsigned["content_sha256"]
    if not isinstance(supplied, str) or supplied != _sha_bytes(_canonical(unsigned)):
        raise GraphiteTransportError("handoff content digest does not match")
    if handoff["status"] != "transport-complete-repair-required":
        raise GraphiteTransportError(
            "only a complete transport handoff can be repaired"
        )
    if handoff["failures"] != [] or not isinstance(handoff["pull_requests"], list):
        raise GraphiteTransportError("transport handoff is incomplete")
    return handoff


def _checked_helper_command(command: Any, *, operation: str) -> list[str]:
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) or not item for item in command)
    ):
        raise GraphiteTransportError("publisher handoff command is invalid")
    executable = Path(command[0]).resolve()
    if executable != Path(sys.executable).resolve():
        raise GraphiteTransportError("publisher handoff executable drifted")
    expected_script = UPDATE if operation == "publish" else AUDIT
    if len(command) < 3 or Path(command[1]).resolve() != expected_script.resolve():
        raise GraphiteTransportError("publisher handoff script drifted")
    allowed_operations = (
        {"text", "ready"} if operation == "publish" else {"audit", "reconcile"}
    )
    if command[2] not in allowed_operations:
        raise GraphiteTransportError("publisher handoff operation is invalid")
    return command


def _run_json_command(
    command: list[str], *, cwd: Path, operation: str
) -> dict[str, Any]:
    checked = _checked_helper_command(command, operation=operation)
    result = _run(checked, cwd=cwd, timeout=MUTATION_TIMEOUT_SECONDS)
    return _parse_strict_json(result.stdout, "publisher helper output")


def _repair_checkpoint_path(output_path: Path) -> Path:
    return output_path.with_name(f".{output_path.name}.checkpoint.json")


def _repair_checkpoint(
    output_path: Path, handoff: dict[str, Any]
) -> tuple[Path, dict[str, Any]]:
    path = _repair_checkpoint_path(output_path)
    if not path.exists():
        return path, {
            "schema_version": SCHEMA_VERSION,
            "handoff_sha256": handoff["content_sha256"],
            "completed": [],
        }
    checkpoint = _strict_json(path)
    _exact_keys(
        checkpoint,
        {"schema_version", "handoff_sha256", "completed"},
        "repair checkpoint",
    )
    if (
        checkpoint["schema_version"] != SCHEMA_VERSION
        or checkpoint["handoff_sha256"] != handoff["content_sha256"]
        or not isinstance(checkpoint["completed"], list)
    ):
        raise GraphiteTransportError("repair checkpoint does not match handoff")
    return path, checkpoint


def _audit_summary(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        key: audit[key]
        for key in (
            "status",
            "receipt_id",
            "provenance",
            "sequence",
            "identity_epoch",
            "final",
            "review_input_sha256",
        )
        if key in audit
    }


def _audit_matches_target(audit: dict[str, Any], item: dict[str, Any]) -> bool:
    return (
        set(audit)
        == {
            "status",
            "receipt_id",
            "provenance",
            "sequence",
            "identity_epoch",
            "final",
            "review_input_sha256",
        }
        and audit.get("status") == "verified"
        and isinstance(audit.get("receipt_id"), str)
        and bool(audit["receipt_id"])
        and audit.get("provenance") in {"canonical", "reconciled-unreceipted"}
        and type(audit.get("sequence")) is int
        and audit["sequence"] > 0
        and audit.get("identity_epoch") == item.get("target_identity_epoch")
        and audit.get("final")
        == {
            "is_draft": item.get("target_is_draft"),
            "title_sha256": item.get("target_title_sha256"),
            "body_sha256": item.get("target_body_sha256"),
        }
        and audit.get("review_input_sha256") == item.get("target_review_input_sha256")
    )


def repair(handoff: dict[str, Any], output_path: Path) -> dict[str, Any]:
    root = Path(handoff["repository_root"])
    if not root.is_absolute() or not root.is_dir():
        raise GraphiteTransportError("handoff repository root is unavailable")
    checkpoint_path, checkpoint = _repair_checkpoint(output_path, handoff)
    completed = {
        item.get("pr"): item
        for item in checkpoint["completed"]
        if isinstance(item, dict) and type(item.get("pr")) is int
    }
    if len(completed) != len(checkpoint["completed"]):
        raise GraphiteTransportError("repair checkpoint PR inventory is invalid")
    repaired: list[dict[str, Any]] = []
    for item in handoff["pull_requests"]:
        if not isinstance(item, dict):
            raise GraphiteTransportError("handoff PR entry is invalid")
        commands = item.get("publisher_commands")
        if not isinstance(commands, list):
            raise GraphiteTransportError("handoff publisher commands are invalid")
        checked_commands = [
            _checked_helper_command(command, operation="publish")
            for command in commands
        ]
        audit_command = _checked_helper_command(
            item.get("final_audit_command"), operation="audit"
        )
        try:
            current_audit = _run_json_command(
                audit_command, cwd=root, operation="audit"
            )
        except GraphiteTransportError:
            current_audit = {"status": "unavailable"}
        prior = completed.get(item.get("pr"))
        if prior is not None:
            if (
                not _audit_matches_target(current_audit, item)
                or _audit_summary(current_audit) != prior.get("audit")
                or prior.get("target_title_sha256") != item.get("target_title_sha256")
                or prior.get("target_body_sha256") != item.get("target_body_sha256")
            ):
                raise GraphiteTransportError(
                    "checkpointed PR no longer has its exact verified target"
                )
            repaired.append(prior)
            continue
        command_results: list[dict[str, Any]] = []
        if _audit_matches_target(current_audit, item):
            audit = current_audit
        elif commands:
            command_results = [
                _run_json_command(command, cwd=root, operation="publish")
                for command in checked_commands
            ]
            audit = _run_json_command(audit_command, cwd=root, operation="audit")
        else:
            try:
                audit = _run_json_command(audit_command, cwd=root, operation="audit")
            except GraphiteTransportError:
                reconciliation = _run_json_command(
                    item.get("no_transition_reconcile_command"),
                    cwd=root,
                    operation="audit",
                )
                command_results.append(reconciliation)
                audit = _run_json_command(audit_command, cwd=root, operation="audit")
        if not _audit_matches_target(audit, item):
            raise GraphiteTransportError(
                "publisher audit did not verify the exact handoff target"
            )
        record = {
            "repository": item.get("repository"),
            "pr": item.get("pr"),
            "url": item.get("url"),
            "target_title_sha256": item.get("target_title_sha256"),
            "target_body_sha256": item.get("target_body_sha256"),
            "target_is_draft": item.get("target_is_draft"),
            "target_review_input_sha256": item.get("target_review_input_sha256"),
            "target_identity_epoch": item.get("target_identity_epoch"),
            "publisher_result_sha256": [
                _sha_bytes(_canonical(result)) for result in command_results
            ],
            "audit": _audit_summary(audit),
        }
        repaired.append(record)
        checkpoint["completed"].append(record)
        _write_private_json(checkpoint_path, checkpoint)
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "canonical-repair-complete",
        "handoff_sha256": handoff["content_sha256"],
        "pull_requests": repaired,
    }
    unsigned = dict(result)
    result["content_sha256"] = _sha_bytes(_canonical(unsigned))
    _write_private_json(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--request", required=True, type=Path)
    plan_parser.add_argument("--output", required=True, type=Path)
    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--plan", required=True, type=Path)
    execute_parser.add_argument("--output", required=True, type=Path)
    repair_parser = subparsers.add_parser("repair")
    repair_parser.add_argument("--handoff", required=True, type=Path)
    repair_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "plan":
            plan = build_plan(_strict_json(args.request))
            _write_private_json(args.output, plan)
            result = {
                "status": "ready",
                "plan_sha256": plan["content_sha256"],
                "output": str(args.output),
            }
        elif args.operation == "execute":
            result = execute(_load_plan(args.plan), args.output)
        else:
            result = repair(_load_handoff(args.handoff), args.output)
    except GraphiteTransportError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
