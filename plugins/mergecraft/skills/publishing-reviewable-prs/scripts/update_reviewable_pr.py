#!/usr/bin/env python3
"""Guard one existing-PR text or ready mutation with exact state checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import IO, Any

WRITER_SCRIPTS = (
    Path(__file__).parents[2] / "writing-reviewable-pr-descriptions/scripts"
)
if str(WRITER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WRITER_SCRIPTS))
from change_navigation.review_input import (  # noqa: E402
    PR_NUMBER_TOKEN,
    ReviewInputError,
    bind_review_input,
    load_review_input,
)
from change_navigation.sensitive_content import suspected_secret_error  # noqa: E402
from publication_receipts import (  # noqa: E402
    LedgerLease,
    load_receipts,
    prepare_receipt_ledger,
    prepare_receipt_store,
    receipt_ledger_lock,
    record_verified_publication,
    resolve_receipt_root,
    verified_transition,
)
from required_review import (  # noqa: E402
    PublicationCandidate,
    validate_review_input_binding,
)
from required_review import (  # noqa: E402
    build_candidate as _build_candidate,
)
from required_review import (  # noqa: E402
    validate_required_review as _validate_required_review,
)
from reviewable_pr_state import (  # noqa: E402
    ExpectedIdentity,
    MutationAmbiguousError,
    PublicationError,
    github_repository,
    identity_matches,
    state_matches,
    validate_identity_inputs,
)
from reviewable_pr_state import (  # noqa: E402
    run_mutation as _run_mutation,
)
from reviewable_pr_state import (  # noqa: E402
    run_read as _run_read,
)
from reviewable_pr_state import (  # noqa: E402
    stored_pr as _stored_pr,
)

VALIDATOR = WRITER_SCRIPTS / "validate_change_navigation.py"
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_body(path: Path) -> tuple[str, bytes]:
    if not path.is_absolute():
        raise PublicationError("body file path must be absolute")
    try:
        raw = path.read_bytes()
        body = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise PublicationError(f"cannot read body file: {error}") from error
    if suspected_secret_error(body) is not None:
        raise PublicationError(
            "PR body contains a suspected credential or secret; publication is blocked"
        )
    return body, raw


def _read_body_source(
    *, body_path: Path | None, template_path: Path | None, pr_number: int
) -> tuple[str, str | None, bytes, str]:
    if (body_path is None) == (template_path is None):
        raise PublicationError(
            "provide exactly one of body file or new-PR body template"
        )
    if body_path is not None:
        body, raw = _read_body(body_path)
        return body, None, raw, "body"
    assert template_path is not None
    template, raw = _read_body(template_path)
    if PR_NUMBER_TOKEN not in template:
        raise PublicationError(f"body template must contain {PR_NUMBER_TOKEN}")
    return template.replace(PR_NUMBER_TOKEN, str(pr_number)), template, raw, "template"


def _reject_secret_text(*values: str) -> None:
    if any(suspected_secret_error(value) is not None for value in values):
        raise PublicationError(
            "PR publication text contains a suspected credential or secret; "
            "publication is blocked pending authorized removal and rotation"
        )


def _validate_body(
    body: str,
    repository: str,
    pr_number: int,
    title: str,
    review_input_path: Path,
    template_path: Path | None = None,
) -> None:
    if not VALIDATOR.is_file():
        raise PublicationError(f"validator is missing: {VALIDATOR}")
    arguments = [
        sys.executable,
        str(VALIDATOR),
        "/dev/stdin",
        "--repository",
        repository,
        "--pr",
        str(pr_number),
        "--title",
        title,
        "--review-input",
        str(review_input_path),
    ]
    if template_path is not None:
        arguments.extend(["--template-body", str(template_path)])
    _run_read(arguments, input_text=body)


def _preflight(
    *,
    expected: ExpectedIdentity,
    expected_title_sha256: str,
    expected_body_sha256: str,
    expected_draft: bool,
) -> dict[str, Any]:
    if SHA256_RE.fullmatch(expected_title_sha256) is None:
        raise PublicationError("expected title SHA-256 must be 64 lowercase hex digits")
    if SHA256_RE.fullmatch(expected_body_sha256) is None:
        raise PublicationError("expected body SHA-256 must be 64 lowercase hex digits")
    stored = _stored_pr(expected.repository, expected.pr_number)
    if not identity_matches(stored, expected):
        raise PublicationError(
            "PR identity or pushed base/head changed before mutation"
        )
    title = stored.get("title")
    body = stored.get("body")
    if not isinstance(title, str) or not isinstance(body, str):
        raise PublicationError("PR title/body preimage is unreadable")
    _reject_secret_text(title, body)
    if (
        _digest(title) != expected_title_sha256
        or _digest(body) != expected_body_sha256
        or stored.get("isDraft") is not expected_draft
    ):
        raise PublicationError("PR title/body/draft preimage changed before mutation")
    return stored


def _bind_review_input(
    review_input_path: Path,
    expected: ExpectedIdentity,
    title: str,
    body: str,
    stored_title: str | None = None,
    stored_body: str | None = None,
    template_body: str | None = None,
) -> tuple[int, str]:
    try:
        manifest = load_review_input(review_input_path)
        bind_review_input(
            manifest,
            repository=expected.repository,
            pr_number=expected.pr_number,
            base=expected.base,
            base_oid=expected.base_oid,
            head=expected.head,
            head_oid=expected.head_oid,
            head_owner=expected.head_owner,
            head_repository=expected.head_repository,
            title=title,
            body=body,
            stored_title=stored_title,
            stored_body=stored_body,
            template_body=template_body,
            git_repository=Path.cwd(),
        )
        return int(manifest.raw["version"]), manifest.content_sha256
    except ReviewInputError as error:
        raise PublicationError(f"review input drift: {error}") from error


def _write_temporary_body(body: str) -> IO[str]:
    temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8")
    temporary.write(body)
    temporary.flush()
    return temporary


def _update_text_locked(
    *,
    expected: ExpectedIdentity,
    expected_title_sha256: str,
    expected_body_sha256: str,
    expected_draft: bool,
    title: str,
    body: str,
    template_body: str | None,
    review_input_path: Path,
    text_scope: str,
    receipt_root: Path,
    lease: LedgerLease,
    candidate: PublicationCandidate,
    review_mode: str,
    review_bundle_root: Path | None,
    selected_specialists: list[str],
) -> dict[str, Any]:
    before = _preflight(
        expected=expected,
        expected_title_sha256=expected_title_sha256,
        expected_body_sha256=expected_body_sha256,
        expected_draft=expected_draft,
    )
    if text_scope not in {"body-only", "title-only", "title-body"}:
        raise PublicationError(
            "text scope must be body-only, title-only, or title-body"
        )
    if text_scope == "body-only" and title != before["title"]:
        raise PublicationError("body-only edit changed the live title")
    if text_scope == "title-only" and body != before["body"]:
        raise PublicationError("title-only edit changed the live body")
    if title == before["title"] and body == before["body"]:
        raise PublicationError("text publication is a no-op; no mutation was attempted")
    review_input_schema_version, review_input_sha256 = _bind_review_input(
        review_input_path,
        expected,
        title,
        body,
        str(before["title"]),
        str(before["body"]),
        template_body,
    )
    validate_review_input_binding(
        candidate,
        review_input_schema_version,
        review_input_sha256,
        review_input_path,
    )
    review = _validate_required_review(
        review_mode=review_mode,
        review_bundle_root=review_bundle_root,
        candidate=candidate,
    )
    final_before = before
    if review.mode == "required":
        final_before = _preflight(
            expected=expected,
            expected_title_sha256=expected_title_sha256,
            expected_body_sha256=expected_body_sha256,
            expected_draft=expected_draft,
        )
    if final_before != before:
        raise PublicationError("PR state changed during required-review validation")
    before = final_before
    command_error: PublicationError | None = None
    with _write_temporary_body(body) as body_file:
        try:
            _run_mutation(
                [
                    "gh",
                    "-R",
                    github_repository(expected.repository),
                    "pr",
                    "edit",
                    str(expected.pr_number),
                    "--title",
                    title,
                    "--body-file",
                    body_file.name,
                ]
            )
        except PublicationError as error:
            command_error = error
    after = _stored_pr(expected.repository, expected.pr_number)
    if command_error is not None:
        if isinstance(command_error, MutationAmbiguousError):
            raise MutationAmbiguousError(
                "PR text command was ambiguous; a matching reread cannot prove "
                "causality, so canonical provenance was not minted; do not retry"
            ) from command_error
        raise PublicationError(
            "PR text command failed; a matching reread cannot prove causality, "
            "so canonical provenance was not minted"
        ) from command_error
    if state_matches(
        after,
        expected,
        title=title,
        body=body,
        is_draft=expected_draft,
    ):
        transition = verified_transition(
            expected=expected,
            operation="update-text",
            preimage=before,
            final_reread=after,
            review_input_schema_version=review_input_schema_version,
            review_input_sha256=review_input_sha256,
            review=review,
            candidate=candidate,
        )
        record_verified_publication(
            root=receipt_root, transition=transition, lease=lease
        )
        return after
    if after == before:
        raise PublicationError("PR text mutation was not stored")
    raise PublicationError(
        "PR text mutation has an ambiguous result; no retry or rollback was attempted"
    )


def _mark_ready_locked(
    *,
    expected: ExpectedIdentity,
    expected_title_sha256: str,
    expected_body_sha256: str,
    review_input_path: Path,
    receipt_root: Path,
    lease: LedgerLease,
    review_mode: str,
    review_bundle_root: Path | None,
    selected_specialists: list[str],
) -> dict[str, Any]:
    before = _preflight(
        expected=expected,
        expected_title_sha256=expected_title_sha256,
        expected_body_sha256=expected_body_sha256,
        expected_draft=True,
    )
    title = str(before["title"])
    body = str(before["body"])
    review_input_schema_version, review_input_sha256 = _bind_review_input(
        review_input_path,
        expected,
        title,
        body,
        str(before["title"]),
        str(before["body"]),
    )
    candidate = _build_candidate(
        operation="mark-ready",
        repository=expected.repository,
        pr_number=expected.pr_number,
        base=expected.base,
        base_oid=expected.base_oid,
        head=expected.head,
        head_oid=expected.head_oid,
        head_owner=expected.head_owner,
        head_repository=expected.head_repository,
        title=title,
        body_source_kind="stored-body",
        body_source_raw=body.encode("utf-8"),
        published_body=body,
        review_input_path=review_input_path,
        review_mode=review_mode,
        selected_specialists=selected_specialists,
    )
    validate_review_input_binding(
        candidate,
        review_input_schema_version,
        review_input_sha256,
        review_input_path,
    )
    review = _validate_required_review(
        review_mode=review_mode,
        review_bundle_root=review_bundle_root,
        candidate=candidate,
    )
    final_before = before
    if review.mode == "required":
        final_before = _preflight(
            expected=expected,
            expected_title_sha256=expected_title_sha256,
            expected_body_sha256=expected_body_sha256,
            expected_draft=True,
        )
    if final_before != before:
        raise PublicationError("PR state changed during required-review validation")
    before = final_before
    command_error: PublicationError | None = None
    try:
        _run_mutation(
            [
                "gh",
                "-R",
                github_repository(expected.repository),
                "pr",
                "ready",
                str(expected.pr_number),
            ]
        )
    except PublicationError as error:
        command_error = error
    after = _stored_pr(expected.repository, expected.pr_number)
    if command_error is not None:
        if isinstance(command_error, MutationAmbiguousError):
            raise MutationAmbiguousError(
                "ready command was ambiguous; a matching reread cannot prove "
                "causality, so canonical provenance was not minted; do not retry"
            ) from command_error
        raise PublicationError(
            "ready command failed; a matching reread cannot prove causality, "
            "so canonical provenance was not minted"
        ) from command_error
    if state_matches(after, expected, title=title, body=body, is_draft=False):
        transition = verified_transition(
            expected=expected,
            operation="mark-ready",
            preimage=before,
            final_reread=after,
            review_input_schema_version=review_input_schema_version,
            review_input_sha256=review_input_sha256,
            review=review,
            candidate=candidate,
        )
        record_verified_publication(
            root=receipt_root, transition=transition, lease=lease
        )
        return after
    if after == before:
        raise PublicationError("PR remains a verified canonical draft")
    raise PublicationError(
        "ready mutation has an ambiguous result; no retry or rollback was attempted"
    )


def update_text(
    *,
    expected: ExpectedIdentity,
    expected_title_sha256: str,
    expected_body_sha256: str,
    expected_draft: bool,
    title: str,
    body_path: Path | None = None,
    body_template_path: Path | None = None,
    review_input_path: Path,
    review_mode: str,
    review_bundle_root: Path | None,
    selected_specialists: list[str],
    text_scope: str = "title-body",
    receipt_directory: Path | None = None,
) -> dict[str, Any]:
    if not title.strip():
        raise PublicationError("title must be non-empty")
    body, template_body, body_source_raw, body_source_kind = _read_body_source(
        body_path=body_path,
        template_path=body_template_path,
        pr_number=expected.pr_number,
    )
    _reject_secret_text(title, body)
    candidate = _build_candidate(
        operation="update-text",
        repository=expected.repository,
        pr_number=expected.pr_number,
        base=expected.base,
        base_oid=expected.base_oid,
        head=expected.head,
        head_oid=expected.head_oid,
        head_owner=expected.head_owner,
        head_repository=expected.head_repository,
        title=title,
        body_source_kind=body_source_kind,
        body_source_raw=body_source_raw,
        published_body=body,
        review_input_path=review_input_path,
        review_mode=review_mode,
        selected_specialists=selected_specialists,
    )
    validation_arguments = (
        body,
        expected.repository,
        expected.pr_number,
        title,
        review_input_path,
    )
    if body_template_path is None:
        _validate_body(*validation_arguments)
    else:
        _validate_body(*validation_arguments, body_template_path)
    early = _preflight(
        expected=expected,
        expected_title_sha256=expected_title_sha256,
        expected_body_sha256=expected_body_sha256,
        expected_draft=expected_draft,
    )
    if text_scope not in {"body-only", "title-only", "title-body"}:
        raise PublicationError(
            "text scope must be body-only, title-only, or title-body"
        )
    if text_scope == "body-only" and title != early["title"]:
        raise PublicationError("body-only edit changed the live title")
    if text_scope == "title-only" and body != early["body"]:
        raise PublicationError("title-only edit changed the live body")
    if title == early["title"] and body == early["body"]:
        raise PublicationError("text publication is a no-op; no mutation was attempted")
    receipt_root = prepare_receipt_store(receipt_directory)
    prepare_receipt_ledger(receipt_root, expected)
    with receipt_ledger_lock(receipt_root, expected) as lease:
        return _update_text_locked(
            expected=expected,
            expected_title_sha256=expected_title_sha256,
            expected_body_sha256=expected_body_sha256,
            expected_draft=expected_draft,
            title=title,
            body=body,
            template_body=template_body,
            review_input_path=review_input_path,
            text_scope=text_scope,
            receipt_root=receipt_root,
            lease=lease,
            candidate=candidate,
            review_mode=review_mode,
            review_bundle_root=review_bundle_root,
            selected_specialists=selected_specialists,
        )


def mark_ready(
    *,
    expected: ExpectedIdentity,
    expected_title_sha256: str,
    expected_body_sha256: str,
    review_input_path: Path,
    review_mode: str,
    review_bundle_root: Path | None,
    selected_specialists: list[str],
    receipt_directory: Path | None = None,
) -> dict[str, Any]:
    validated = _preflight(
        expected=expected,
        expected_title_sha256=expected_title_sha256,
        expected_body_sha256=expected_body_sha256,
        expected_draft=True,
    )
    title = str(validated["title"])
    body = str(validated["body"])
    _validate_body(
        body, expected.repository, expected.pr_number, title, review_input_path
    )
    receipt_root = prepare_receipt_store(receipt_directory)
    prepare_receipt_ledger(receipt_root, expected)
    with receipt_ledger_lock(receipt_root, expected) as lease:
        return _mark_ready_locked(
            expected=expected,
            expected_title_sha256=expected_title_sha256,
            expected_body_sha256=expected_body_sha256,
            review_input_path=review_input_path,
            receipt_root=receipt_root,
            lease=lease,
            review_mode=review_mode,
            review_bundle_root=review_bundle_root,
            selected_specialists=selected_specialists,
        )


def _expected(args: argparse.Namespace) -> ExpectedIdentity:
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
    return ExpectedIdentity(
        repository=args.repository,
        pr_number=args.pr,
        base=args.base,
        base_oid=args.base_oid,
        head=args.head,
        head_oid=args.head_oid,
        head_owner=args.head_owner,
        head_repository=args.head_repository,
    )


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--base", required=True)
    parser.add_argument("--base-oid", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--head-oid", required=True)
    parser.add_argument("--head-owner", required=True)
    parser.add_argument("--head-repository", required=True)
    parser.add_argument("--expected-title-sha256", required=True)
    parser.add_argument("--expected-body-sha256", required=True)
    parser.add_argument("--review-input", required=True, type=Path)
    parser.add_argument(
        "--review-mode", choices=("required", "not-required"), required=True
    )
    parser.add_argument("--review-bundle", type=Path)
    parser.add_argument(
        "--selected-specialists",
        required=True,
        help="sorted unique specialist names as a JSON array; use [] for none",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    text_parser = subparsers.add_parser("text")
    _add_common(text_parser)
    text_parser.add_argument(
        "--expected-state", choices=("draft", "ready"), required=True
    )
    text_parser.add_argument(
        "--text-scope",
        choices=("body-only", "title-only", "title-body"),
        required=True,
    )
    text_parser.add_argument("--title", required=True)
    body_source = text_parser.add_mutually_exclusive_group(required=True)
    body_source.add_argument("--body-file", type=Path)
    body_source.add_argument("--body-template", type=Path)
    ready_parser = subparsers.add_parser("ready")
    _add_common(ready_parser)
    args = parser.parse_args()
    try:
        try:
            selected_specialists = json.loads(args.selected_specialists)
        except json.JSONDecodeError as error:
            raise PublicationError(
                "selected review specialists must be a JSON array"
            ) from error
        expected = _expected(args)
        if args.operation == "text":
            stored = update_text(
                expected=expected,
                expected_title_sha256=args.expected_title_sha256,
                expected_body_sha256=args.expected_body_sha256,
                expected_draft=args.expected_state == "draft",
                title=args.title,
                body_path=args.body_file,
                body_template_path=args.body_template,
                review_input_path=args.review_input,
                review_mode=args.review_mode,
                review_bundle_root=args.review_bundle,
                selected_specialists=selected_specialists,
                text_scope=args.text_scope,
                receipt_directory=None,
            )
        else:
            stored = mark_ready(
                expected=expected,
                expected_title_sha256=args.expected_title_sha256,
                expected_body_sha256=args.expected_body_sha256,
                review_input_path=args.review_input,
                review_mode=args.review_mode,
                review_bundle_root=args.review_bundle,
                selected_specialists=selected_specialists,
                receipt_directory=None,
            )
    except PublicationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    receipt = load_receipts(resolve_receipt_root(), expected)[-1]
    print(
        json.dumps(
            {
                "repository": expected.repository,
                "pr": expected.pr_number,
                "url": expected.url,
                "is_draft": stored["isDraft"],
                **receipt.summary("verified"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
