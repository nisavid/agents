#!/usr/bin/env python3
"""Post one authorized top-level PR comment and verify its exact receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PUBLISHER_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "publishing-reviewable-prs" / "scripts"
)
sys.path.insert(0, str(PUBLISHER_SCRIPTS))

from reviewable_pr_state import (  # noqa: E402
    ExpectedIdentity,
    GITHUB_HOST,
    MutationAmbiguousError,
    PublicationError,
    StateReadError,
    identity_matches,
    run_mutation as _run_mutation,
    run_read as _run_read,
    stored_pr as _stored_pr,
    strict_json,
    validate_identity_inputs,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _comment_receipt(
    value: Any, expected: ExpectedIdentity, body: str, expected_login: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublicationError("comment receipt is not an object")
    identifier = value.get("id")
    url = value.get("html_url")
    user = value.get("user")
    created_at = value.get("created_at")
    expected_prefix = f"{expected.url}#issuecomment-"
    if (
        type(identifier) is not int
        or identifier <= 0
        or not isinstance(url, str)
        or not url.startswith(expected_prefix)
        or value.get("body") != body
        or not isinstance(user, dict)
        or user.get("login") != expected_login
        or not isinstance(created_at, str)
    ):
        raise PublicationError("comment receipt does not match exact PR/body authority")
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise PublicationError("comment receipt has an invalid created_at") from error
    if parsed.tzinfo is None:
        raise PublicationError("comment receipt has an invalid created_at")
    return value


def _active_login() -> str:
    result = _run_read(
        ["gh", "api", "--hostname", GITHUB_HOST, "--method", "GET", "user"]
    )
    value = strict_json(result.stdout, "authenticated login response")
    login = value.get("login") if isinstance(value, dict) else None
    if not isinstance(login, str) or not login:
        raise PublicationError("active authenticated login is unavailable")
    return login


def post_comment(
    *,
    expected: ExpectedIdentity,
    expected_authenticated_login: str,
    body: str,
    body_sha256: str,
) -> dict[str, Any]:
    if not body:
        raise PublicationError("comment body must be non-empty")
    if _digest(body) != body_sha256:
        raise PublicationError("comment body SHA-256 does not match supplied bytes")
    if not expected_authenticated_login:
        raise PublicationError("expected authenticated login must be non-empty")
    if _active_login() != expected_authenticated_login:
        raise PublicationError("active authenticated login does not match authority")
    before = _stored_pr(expected.repository, expected.pr_number)
    if not identity_matches(before, expected):
        raise PublicationError("PR identity or pushed head changed before comment")
    payload = json.dumps(
        {"body": body},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    result = _run_mutation(
        [
            "gh",
            "api",
            "--hostname",
            GITHUB_HOST,
            "--method",
            "POST",
            f"repos/{expected.repository}/issues/{expected.pr_number}/comments",
            "--input",
            "-",
        ],
        input_text=payload,
    )
    try:
        created = _comment_receipt(
            strict_json(result.stdout, "comment creation response"),
            expected,
            body,
            expected_authenticated_login,
        )
    except (PublicationError, StateReadError) as error:
        raise PublicationError(
            "comment mutation returned no trustworthy identity; state is ambiguous "
            "and must not be retried"
        ) from error
    identifier = int(created["id"])
    reread = _run_read(
        [
            "gh",
            "api",
            "--hostname",
            GITHUB_HOST,
            "--method",
            "GET",
            f"repos/{expected.repository}/issues/comments/{identifier}",
        ]
    )
    receipt = _comment_receipt(
        strict_json(reread.stdout, "comment reread response"),
        expected,
        body,
        expected_authenticated_login,
    )
    if any(receipt[key] != created[key] for key in ("id", "html_url", "created_at")):
        raise PublicationError("comment reread identity differs from creation receipt")
    after = _stored_pr(expected.repository, expected.pr_number)
    if not identity_matches(after, expected):
        raise PublicationError("PR identity or pushed head changed after comment")
    return receipt


def _read_body(path: Path) -> str:
    if not path.is_absolute():
        raise PublicationError("comment body path must be absolute")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise PublicationError(f"cannot read comment body: {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--base", required=True)
    parser.add_argument("--base-oid", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--head-oid", required=True)
    parser.add_argument("--head-owner", required=True)
    parser.add_argument("--head-repository", required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--body-sha256", required=True)
    parser.add_argument("--expected-authenticated-login", required=True)
    args = parser.parse_args()
    try:
        validate_identity_inputs(
            repository=args.repository,
            pr_number=args.pr,
            base=args.base,
            base_oid=args.base_oid,
            head=args.head,
            head_oid=args.head_oid,
            head_owner=args.head_owner,
            head_repository=args.head_repository,
        )
        expected = ExpectedIdentity(
            repository=args.repository,
            pr_number=args.pr,
            base=args.base,
            base_oid=args.base_oid,
            head=args.head,
            head_oid=args.head_oid,
            head_owner=args.head_owner,
            head_repository=args.head_repository,
        )
        receipt = post_comment(
            expected=expected,
            expected_authenticated_login=args.expected_authenticated_login,
            body=_read_body(args.body_file),
            body_sha256=args.body_sha256,
        )
    except (PublicationError, MutationAmbiguousError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
