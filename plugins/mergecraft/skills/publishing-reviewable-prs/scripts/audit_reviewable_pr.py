#!/usr/bin/env python3
"""Read-only audit and reconciliation for reviewable-PR publication receipts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from publication_receipts import (
    AuditResult,
    audit_publication,
    prepare_receipt_ledger,
    prepare_receipt_store,
    receipt_ledger_lock,
    record_reconciliation,
    verified_transition,
)
from reviewable_pr_state import (
    ExpectedIdentity,
    PublicationError,
    run_read,
    stored_pr,
    validate_identity_inputs,
)

WRITER_SCRIPTS = (
    Path(__file__).parents[2] / "writing-reviewable-pr-descriptions/scripts"
)
if str(WRITER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WRITER_SCRIPTS))
from change_navigation.review_input import (  # noqa: E402
    ReviewInputError,
    bind_review_input,
    load_review_input,
)
from change_navigation.sensitive_content import suspected_secret_error  # noqa: E402


VALIDATOR = WRITER_SCRIPTS / "validate_change_navigation.py"


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


def audit(
    *, expected: ExpectedIdentity, receipt_directory: Path | None = None
) -> AuditResult:
    """Return verified, drift, or unavailable without mutating local or forge state."""

    return audit_publication(
        root=receipt_directory,
        expected=expected,
        read_live=lambda: stored_pr(expected.repository, expected.pr_number),
    )


def _validate_live_state(
    *, expected: ExpectedIdentity, title: str, body: str, review_input_path: Path
) -> tuple[int, str]:
    if not VALIDATOR.is_file():
        raise PublicationError(f"validator is missing: {VALIDATOR}")
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
            git_repository=Path.cwd(),
        )
        review_input_version = int(manifest.raw["version"])
        review_input_sha256 = manifest.content_sha256
    except ReviewInputError as error:
        raise PublicationError(f"review input drift: {error}") from error
    run_read(
        [
            sys.executable,
            str(VALIDATOR),
            "/dev/stdin",
            "--repository",
            expected.repository,
            "--pr",
            str(expected.pr_number),
            "--title",
            title,
            "--review-input",
            str(review_input_path),
        ],
        input_text=body,
    )
    return review_input_version, review_input_sha256


def reconcile(
    *,
    expected: ExpectedIdentity,
    receipt_directory: Path | None = None,
    review_input_path: Path,
):
    """Create an irreversible reconciled-unreceipted receipt for exact live state."""

    receipt_root = prepare_receipt_store(receipt_directory)
    prepare_receipt_ledger(receipt_root, expected)
    with receipt_ledger_lock(receipt_root, expected) as lease:
        first = stored_pr(expected.repository, expected.pr_number)
        body = first.get("body")
        if not isinstance(body, str):
            raise PublicationError("PR body is unreadable")
        title = first.get("title")
        if not isinstance(title, str):
            raise PublicationError("PR title is unreadable")
        if any(suspected_secret_error(value) is not None for value in (title, body)):
            raise PublicationError(
                "existing PR publication text contains a suspected credential or "
                "secret; reconciliation is blocked pending authorized removal and "
                "rotation"
            )
        review_input_schema_version, review_input_sha256 = _validate_live_state(
            expected=expected,
            title=title,
            body=body,
            review_input_path=review_input_path,
        )
        second = stored_pr(expected.repository, expected.pr_number)
        if second != first:
            raise PublicationError(
                "live PR state changed during reconciliation; no receipt was written"
            )
        transition = verified_transition(
            expected=expected,
            operation="reconcile",
            preimage=first,
            final_reread=second,
            review_input_schema_version=review_input_schema_version,
            review_input_sha256=review_input_sha256,
        )
        return record_reconciliation(
            root=receipt_root, transition=transition, lease=lease
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    audit_parser = subparsers.add_parser("audit")
    _add_common(audit_parser)
    reconcile_parser = subparsers.add_parser("reconcile")
    _add_common(reconcile_parser)
    reconcile_parser.add_argument("--review-input", required=True, type=Path)
    args = parser.parse_args()
    try:
        expected = _expected(args)
        if args.operation == "audit":
            result = audit(expected=expected)
            print(json.dumps(result.as_json(), sort_keys=True))
            return 0 if result.status == "verified" else 1
        receipt = reconcile(
            expected=expected,
            receipt_directory=None,
            review_input_path=args.review_input,
        )
    except PublicationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt.summary("reconciled-unreceipted"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
