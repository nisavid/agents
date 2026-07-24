"""Shared exact-state operations for guarded PR publication."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any


REPOSITORY_RE = re.compile(r"[^/\s]+/[^/\s]+")
GITHUB_HOST = "github.com"
OID_RE = re.compile(r"[0-9a-f]{40}")
PR_URL_RE = re.compile(
    r"https://github\.com/(?P<repository>[^/]+/[^/]+)/pull/(?P<pr>\d+)"
)
STORED_FIELDS = (
    "number,url,title,body,baseRefName,baseRefOid,headRefName,"
    "headRefOid,headRepository,headRepositoryOwner,isDraft,state"
)
READ_TIMEOUT_SECONDS = 30
MUTATION_TIMEOUT_SECONDS = 30


class PublicationError(RuntimeError):
    """A PR publication operation could not reach a verified result."""


class StateReadError(PublicationError):
    """The current PR state could not be established."""


class MutationAmbiguousError(PublicationError):
    """A possible mutation timed out and must not be retried blindly."""


@dataclass(frozen=True)
class ExpectedIdentity:
    repository: str
    pr_number: int
    base: str
    base_oid: str
    head: str
    head_oid: str
    head_owner: str
    head_repository: str

    @property
    def head_branch(self) -> str:
        return self.head.split(":", 1)[1]

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repository}/pull/{self.pr_number}"


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("GH_HOST", None)
    environment.pop("GH_REPO", None)
    environment.update(
        {
            "GH_PROMPT_DISABLED": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def github_repository(repository: str) -> str:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise PublicationError("repository must be OWNER/REPO")
    return f"{GITHUB_HOST}/{repository}"


def run_read(
    arguments: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            arguments,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=READ_TIMEOUT_SECONDS,
            env=_environment(),
        )
    except subprocess.TimeoutExpired as error:
        raise StateReadError(
            f"{arguments[0]} read timed out after {READ_TIMEOUT_SECONDS} seconds"
        ) from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise StateReadError(f"{arguments[0]} read failed: {detail}")
    return result


def run_mutation(
    arguments: list[str], *, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            arguments,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=MUTATION_TIMEOUT_SECONDS,
            env=_environment(),
        )
    except subprocess.TimeoutExpired as error:
        raise MutationAmbiguousError(
            f"{arguments[0]} timed out after a possible mutation; "
            "reread exact state and do not retry"
        ) from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise PublicationError(f"{arguments[0]} mutation failed: {detail}")
    return result


def strict_json(output: str, source: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise StateReadError(f"{source} contains duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise StateReadError(f"{source} contains non-finite JSON value: {value}")

    try:
        return json.loads(
            output,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise StateReadError(f"{source} returned invalid JSON") from error


def _json_object(output: str, source: str) -> dict[str, Any]:
    value = strict_json(output, source)
    if not isinstance(value, dict):
        raise StateReadError(f"{source} returned an unexpected value")
    return value


def stored_pr(repository: str, pr_number: int) -> dict[str, Any]:
    try:
        result = run_read(
            [
                "gh",
                "-R",
                github_repository(repository),
                "pr",
                "view",
                str(pr_number),
                "--json",
                STORED_FIELDS,
            ]
        )
    except PublicationError as error:
        raise StateReadError(f"cannot read PR state: {error}") from error
    stored = _json_object(result.stdout, "gh pr view")
    if type(stored.get("number")) is not int or stored["number"] <= 0:
        raise StateReadError("gh pr view returned a malformed PR identity")
    return stored


def open_prs(repository: str, base: str, head: str) -> list[dict[str, Any]]:
    try:
        result = run_read(
            [
                "gh",
                "api",
                "--hostname",
                GITHUB_HOST,
                "--method",
                "GET",
                "--paginate",
                "--slurp",
                f"repos/{repository}/pulls",
                "-f",
                "state=open",
                "-f",
                f"base={base}",
                "-f",
                f"head={head}",
                "-f",
                "per_page=100",
            ]
        )
    except PublicationError as error:
        raise StateReadError(f"cannot list matching PRs: {error}") from error
    value = strict_json(result.stdout, "GitHub pull request pagination")
    if not isinstance(value, list) or not all(isinstance(page, list) for page in value):
        raise StateReadError(
            "GitHub pull request pagination returned an unexpected value"
        )
    return [
        _stored_from_rest_pr(item, repository)
        for page in value
        for item in _validated_rest_page(page)
    ]


def _validated_rest_page(page: list[Any]) -> list[dict[str, Any]]:
    if not all(isinstance(item, dict) and item for item in page):
        raise StateReadError(
            "GitHub pull request pagination returned a malformed PR node"
        )
    return page


def _stored_from_rest_pr(item: dict[str, Any], repository: str) -> dict[str, Any]:
    try:
        base = item["base"]
        head = item["head"]
        base_repository = base["repo"]
        head_repository = head["repo"]
        head_owner = head_repository["owner"]["login"]
        head_repository_name = head_repository["full_name"]
        stored = {
            "number": item["number"],
            "url": item["html_url"],
            "title": item["title"],
            "body": item["body"] or "",
            "baseRefName": base["ref"],
            "baseRefOid": base["sha"],
            "headRefName": head["ref"],
            "headRefOid": head["sha"],
            "headRepository": {"nameWithOwner": head_repository_name},
            "headRepositoryOwner": {"login": head_owner},
            "isDraft": item["draft"],
            "state": item["state"].upper(),
        }
    except (KeyError, TypeError, AttributeError) as error:
        raise StateReadError(
            "GitHub pull request pagination returned a malformed PR node"
        ) from error

    if (
        not isinstance(base_repository, dict)
        or base_repository.get("full_name") != repository
    ):
        raise StateReadError(
            "GitHub pull request pagination drifted to another repository"
        )
    required_strings = (
        "url",
        "title",
        "baseRefName",
        "baseRefOid",
        "headRefName",
        "headRefOid",
        "state",
    )
    if (
        type(stored["number"]) is not int
        or stored["number"] <= 0
        or not isinstance(stored["isDraft"], bool)
        or not isinstance(head_owner, str)
        or not head_owner
        or not isinstance(head_repository_name, str)
        or REPOSITORY_RE.fullmatch(head_repository_name) is None
        or any(
            not isinstance(stored[field], str) or not stored[field]
            for field in required_strings
        )
        or not isinstance(stored["body"], str)
    ):
        raise StateReadError(
            "GitHub pull request pagination returned a malformed PR node"
        )
    return stored


def _head_repository_name(stored: dict[str, Any]) -> str | None:
    repository = stored.get("headRepository")
    if not isinstance(repository, dict):
        return None
    name_with_owner = repository.get("nameWithOwner")
    if isinstance(name_with_owner, str):
        return name_with_owner
    owner = repository.get("owner")
    name = repository.get("name")
    if isinstance(owner, dict):
        owner = owner.get("login")
    if isinstance(owner, str) and isinstance(name, str):
        return f"{owner}/{name}"
    return None


def validate_identity_inputs(
    *,
    repository: str,
    pr_number: int | None,
    base: str,
    base_oid: str,
    head: str,
    head_oid: str,
    head_owner: str,
    head_repository: str,
) -> None:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise PublicationError("repository must use OWNER/REPO")
    if REPOSITORY_RE.fullmatch(head_repository) is None:
        raise PublicationError("head repository must use OWNER/REPO")
    if pr_number is not None and (type(pr_number) is not int or pr_number <= 0):
        raise PublicationError("PR number must be positive")
    if not base.strip():
        raise PublicationError("base must be non-empty")
    if ":" not in head or not all(part.strip() for part in head.split(":", 1)):
        raise PublicationError("head must use OWNER:BRANCH")
    if head.split(":", 1)[0] != head_owner:
        raise PublicationError("head owner must exactly match the OWNER in head")
    if head_repository.split("/", 1)[0] != head_owner:
        raise PublicationError("head repository owner must match head owner")
    if OID_RE.fullmatch(base_oid) is None:
        raise PublicationError("base OID must be a lowercase 40-digit hex OID")
    if OID_RE.fullmatch(head_oid) is None:
        raise PublicationError("head OID must be a lowercase 40-digit hex OID")


def head_base_matches(
    stored: dict[str, Any],
    *,
    base: str,
    head: str,
    head_owner: str,
    head_repository: str,
) -> bool:
    owner = stored.get("headRepositoryOwner")
    stored_owner = owner.get("login") if isinstance(owner, dict) else None
    return (
        stored.get("baseRefName") == base
        and stored.get("headRefName") == head.split(":", 1)[1]
        and stored_owner == head_owner
        and _head_repository_name(stored) == head_repository
        and stored.get("state") == "OPEN"
    )


def identity_matches(stored: dict[str, Any], expected: ExpectedIdentity) -> bool:
    return (
        type(expected.pr_number) is int
        and type(stored.get("number")) is int
        and stored.get("number") == expected.pr_number
        and stored.get("url") == expected.url
        and head_base_matches(
            stored,
            base=expected.base,
            head=expected.head,
            head_owner=expected.head_owner,
            head_repository=expected.head_repository,
        )
        and stored.get("baseRefOid") == expected.base_oid
        and stored.get("headRefOid") == expected.head_oid
    )


def state_matches(
    stored: dict[str, Any],
    expected: ExpectedIdentity,
    *,
    title: str,
    body: str,
    is_draft: bool,
) -> bool:
    return (
        identity_matches(stored, expected)
        and stored.get("title") == title
        and stored.get("body") == body
        and stored.get("isDraft") is is_draft
    )
