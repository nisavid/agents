#!/usr/bin/env python3
"""Create a nonce-tagged draft PR and install its canonical body safely."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any

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
from publication_receipts import (  # noqa: E402
    creation_transaction_lock,
    load_receipts,
    prepare_receipt_ledger,
    prepare_receipt_store,
    receipt_ledger_lock,
    record_verified_publication,
    resolve_receipt_root,
    verified_transition,
)
from required_review import (  # noqa: E402
    build_candidate as _build_candidate,
)
from required_review import (  # noqa: E402
    validate_create_rendering,
    validate_review_input_binding,
)
from required_review import (  # noqa: E402
    validate_required_review as _validate_required_review,
)
from reviewable_pr_state import (  # noqa: E402
    PR_URL_RE,
    ExpectedIdentity,
    MutationAmbiguousError,
    PublicationError,
    github_repository,
    head_base_matches,
    state_matches,
    validate_identity_inputs,
)
from reviewable_pr_state import (  # noqa: E402
    open_prs as _open_prs,
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

PR_NUMBER_TOKEN = "__PUBLISHING_REVIEWABLE_PRS_PR_NUMBER__"
VALIDATION_PR_NUMBER = 2_147_483_647
VALIDATOR = WRITER_SCRIPTS / "validate_change_navigation.py"


def _transport_body(nonce: str) -> str:
    return (
        "<!-- publishing-reviewable-prs: canonical body pending GitHub PR identity; "
        f"transaction={nonce} -->\n"
    )


def _new_nonce() -> str:
    return secrets.token_hex(16)


def _validate(
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


def _body_template(path: Path) -> tuple[str, bytes]:
    if not path.is_absolute():
        raise PublicationError("body template path must be absolute")
    try:
        raw = path.read_bytes()
        template = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise PublicationError(f"cannot read body template: {error}") from error
    if suspected_secret_error(template) is not None:
        raise PublicationError(
            "PR body contains a suspected credential or secret; publication is blocked"
        )
    if PR_NUMBER_TOKEN not in template:
        raise PublicationError(f"body template must contain {PR_NUMBER_TOKEN}")
    return template, raw


def _reject_secret_text(*values: str) -> None:
    if any(suspected_secret_error(value) is not None for value in values):
        raise PublicationError(
            "PR publication text contains a suspected credential or secret; "
            "publication is blocked"
        )


def _review_input(
    path: Path,
    *,
    repository: str,
    base: str,
    base_oid: str,
    head: str,
    head_oid: str,
    head_owner: str,
    head_repository: str,
    title: str,
    body: str,
    pr_number: int,
    template_body: str | None = None,
) -> tuple[int, str]:
    try:
        manifest = load_review_input(path)
        bind_review_input(
            manifest,
            repository=repository,
            pr_number=pr_number,
            base=base,
            base_oid=base_oid,
            head=head,
            head_oid=head_oid,
            head_owner=head_owner,
            head_repository=head_repository,
            title=title,
            body=body,
            template_body=template_body,
            git_repository=Path.cwd(),
        )
        return int(manifest.raw["version"]), manifest.content_sha256
    except ReviewInputError as error:
        raise PublicationError(f"review input drift: {error}") from error


def _write_temporary_body(body: str) -> tempfile.NamedTemporaryFile[str]:
    temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8")
    temporary.write(body)
    temporary.flush()
    return temporary


def _matching_head_prs(
    *,
    repository: str,
    base: str,
    head: str,
    head_owner: str,
    head_repository: str,
) -> list[dict[str, Any]]:
    return [
        stored
        for stored in _open_prs(repository, base, head)
        if head_base_matches(
            stored,
            base=base,
            head=head,
            head_owner=head_owner,
            head_repository=head_repository,
        )
    ]


def _recover_created(
    *,
    repository: str,
    base: str,
    base_oid: str,
    head: str,
    head_oid: str,
    head_owner: str,
    head_repository: str,
    title: str,
    transport_body: str,
) -> tuple[int, str] | None:
    matches: list[dict[str, Any]] = []
    for stored in _matching_head_prs(
        repository=repository,
        base=base,
        head=head,
        head_owner=head_owner,
        head_repository=head_repository,
    ):
        number = stored.get("number")
        if type(number) is not int or number <= 0:
            continue
        expected = ExpectedIdentity(
            repository=repository,
            pr_number=number,
            base=base,
            base_oid=base_oid,
            head=head,
            head_oid=head_oid,
            head_owner=head_owner,
            head_repository=head_repository,
        )
        if state_matches(
            stored,
            expected,
            title=title,
            body=transport_body,
            is_draft=True,
        ):
            matches.append(stored)
    if len(matches) != 1:
        return None
    return int(matches[0]["number"]), str(matches[0]["url"])


def _create(
    *,
    repository: str,
    base: str,
    base_oid: str,
    head: str,
    head_oid: str,
    head_owner: str,
    head_repository: str,
    title: str,
    nonce: str,
) -> tuple[int, str]:
    existing = _matching_head_prs(
        repository=repository,
        base=base,
        head=head,
        head_owner=head_owner,
        head_repository=head_repository,
    )
    if existing:
        urls = ", ".join(str(item.get("url", "unknown URL")) for item in existing)
        raise PublicationError(f"an open PR already exists for this head/base: {urls}")

    transport_body = _transport_body(nonce)
    create_error: PublicationError | None = None
    result = None
    with _write_temporary_body(transport_body) as body_file:
        try:
            result = _run_mutation(
                [
                    "gh",
                    "-R",
                    github_repository(repository),
                    "pr",
                    "create",
                    "--base",
                    base,
                    "--head",
                    head,
                    "--title",
                    title,
                    "--body-file",
                    body_file.name,
                    "--draft",
                ]
            )
        except PublicationError as error:
            create_error = error

    if result is not None:
        match = PR_URL_RE.search(result.stdout)
        if match is not None and match.group("repository") == repository:
            return int(match.group("pr")), match.group(0)
        create_error = PublicationError("gh pr create returned no expected PR URL")

    recovered = _recover_created(
        repository=repository,
        base=base,
        base_oid=base_oid,
        head=head,
        head_oid=head_oid,
        head_owner=head_owner,
        head_repository=head_repository,
        title=title,
        transport_body=transport_body,
    )
    if recovered is not None:
        return recovered
    raise PublicationError(
        f"create outcome is ambiguous and no unique nonce-tagged draft was found: "
        f"{create_error}"
    ) from create_error


def _install_canonical_draft(
    *,
    expected: ExpectedIdentity,
    title: str,
    transport_body: str,
    body: str,
    before: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not state_matches(
        before,
        expected,
        title=title,
        body=transport_body,
        is_draft=True,
    ):
        raise PublicationError(
            "canonical body was not written because the created PR no longer has "
            "the exact nonce-tagged transport state"
        )

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
                "canonical edit command was ambiguous; a matching reread cannot "
                "prove causality, so canonical provenance was not minted; do not retry"
            ) from command_error
        raise PublicationError(
            "canonical edit command failed; a matching reread cannot prove "
            "causality, so canonical provenance was not minted"
        ) from command_error
    if state_matches(after, expected, title=title, body=body, is_draft=True):
        return before, after
    if state_matches(
        after,
        expected,
        title=title,
        body=transport_body,
        is_draft=True,
    ):
        raise PublicationError("canonical edit was not stored")
    raise PublicationError(
        "canonical edit has an ambiguous result; observed state was preserved and "
        "requires operator inspection"
    )


def publish(
    *,
    repository: str,
    base: str,
    base_oid: str,
    head: str,
    head_oid: str,
    head_owner: str,
    head_repository: str,
    title: str,
    template_path: Path,
    review_input_path: Path,
    review_mode: str,
    review_bundle_root: Path | None,
    selected_specialists: list[str],
    receipt_directory: Path | None = None,
) -> dict[str, Any]:
    validate_identity_inputs(
        repository=repository,
        pr_number=None,
        base=base,
        base_oid=base_oid,
        head=head,
        head_oid=head_oid,
        head_owner=head_owner,
        head_repository=head_repository,
    )
    if not title.strip():
        raise PublicationError("title must be non-empty")

    template, template_raw = _body_template(template_path)
    _reject_secret_text(title, template)
    validation_body = template.replace(PR_NUMBER_TOKEN, str(VALIDATION_PR_NUMBER))
    review_input_schema_version, review_input_sha256 = _review_input(
        review_input_path,
        repository=repository,
        base=base,
        base_oid=base_oid,
        head=head,
        head_oid=head_oid,
        head_owner=head_owner,
        head_repository=head_repository,
        title=title,
        body=validation_body,
        pr_number=VALIDATION_PR_NUMBER,
        template_body=template,
    )
    _validate(
        validation_body,
        repository,
        VALIDATION_PR_NUMBER,
        title,
        review_input_path,
        template_path,
    )
    candidate = _build_candidate(
        operation="create",
        repository=repository,
        pr_number=PR_NUMBER_TOKEN,
        base=base,
        base_oid=base_oid,
        head=head,
        head_oid=head_oid,
        head_owner=head_owner,
        head_repository=head_repository,
        title=title,
        body_source_kind="template",
        body_source_raw=template_raw,
        published_body=template,
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
    receipt_root = prepare_receipt_store(receipt_directory)
    with creation_transaction_lock(
        receipt_root,
        repository=repository,
        base=base,
        head=head,
        head_owner=head_owner,
        head_repository=head_repository,
    ):
        initial_review = _validate_required_review(
            review_mode=review_mode,
            review_bundle_root=review_bundle_root,
            candidate=candidate,
        )
        nonce = _new_nonce()
        transport_body = _transport_body(nonce)
        pr_number, url = _create(
            repository=repository,
            base=base,
            base_oid=base_oid,
            head=head,
            head_oid=head_oid,
            head_owner=head_owner,
            head_repository=head_repository,
            title=title,
            nonce=nonce,
        )
        expected = ExpectedIdentity(
            repository,
            pr_number,
            base,
            base_oid,
            head,
            head_oid,
            head_owner,
            head_repository,
        )
        prepare_receipt_ledger(receipt_root, expected)
        body = template.replace(PR_NUMBER_TOKEN, str(pr_number))
        try:
            with receipt_ledger_lock(receipt_root, expected) as lease:
                created = _stored_pr(repository, pr_number)
                if not state_matches(
                    created,
                    expected,
                    title=title,
                    body=transport_body,
                    is_draft=True,
                ):
                    raise PublicationError(
                        "created PR does not have the exact nonce-tagged "
                        "transport state"
                    )
                review_input_schema_version, review_input_sha256 = _review_input(
                    review_input_path,
                    repository=repository,
                    base=base,
                    base_oid=base_oid,
                    head=head,
                    head_oid=head_oid,
                    head_owner=head_owner,
                    head_repository=head_repository,
                    title=title,
                    body=body,
                    pr_number=pr_number,
                    template_body=template,
                )
                _validate(
                    body,
                    repository,
                    pr_number,
                    title,
                    review_input_path,
                    template_path,
                )
                validate_review_input_binding(
                    candidate,
                    review_input_schema_version,
                    review_input_sha256,
                    review_input_path,
                )
                validate_create_rendering(
                    candidate=candidate,
                    template=template,
                    rendered_body=body,
                    pr_number=pr_number,
                )
                review = _validate_required_review(
                    review_mode=review_mode,
                    review_bundle_root=review_bundle_root,
                    candidate=candidate,
                    expected_observation=initial_review.observation,
                )
                final_preflight = _stored_pr(repository, pr_number)
                if not state_matches(
                    final_preflight,
                    expected,
                    title=title,
                    body=transport_body,
                    is_draft=True,
                ):
                    raise PublicationError(
                        "canonical body was not written because the created PR no "
                        "longer has the exact nonce-tagged transport state after review"
                    )
                before, stored = _install_canonical_draft(
                    expected=expected,
                    title=title,
                    transport_body=transport_body,
                    body=body,
                    before=final_preflight,
                )
                transition = verified_transition(
                    expected=expected,
                    operation="create",
                    preimage=before,
                    final_reread=stored,
                    review_input_schema_version=review_input_schema_version,
                    review_input_sha256=review_input_sha256,
                    review=review,
                    candidate=candidate,
                )
                record_verified_publication(
                    root=receipt_root, transition=transition, lease=lease
                )
                return stored
        except MutationAmbiguousError as error:
            raise MutationAmbiguousError(
                f"PR {url} requires inspection; no automatic retry or rollback was "
                f"attempted: {error}"
            ) from error
        except PublicationError as error:
            raise PublicationError(
                f"PR {url} requires inspection; no automatic retry or rollback was "
                f"attempted: {error}"
            ) from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--base-oid", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--head-oid", required=True)
    parser.add_argument("--head-owner", required=True)
    parser.add_argument("--head-repository", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body-template", required=True, type=Path)
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
    args = parser.parse_args()
    try:
        try:
            selected_specialists = json.loads(args.selected_specialists)
        except json.JSONDecodeError as error:
            raise PublicationError(
                "selected review specialists must be a JSON array"
            ) from error
        stored = publish(
            repository=args.repository,
            base=args.base,
            base_oid=args.base_oid,
            head=args.head,
            head_oid=args.head_oid,
            head_owner=args.head_owner,
            head_repository=args.head_repository,
            title=args.title,
            template_path=args.body_template,
            review_input_path=args.review_input,
            review_mode=args.review_mode,
            review_bundle_root=args.review_bundle,
            selected_specialists=selected_specialists,
            receipt_directory=None,
        )
    except PublicationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    expected = ExpectedIdentity(
        repository=args.repository,
        pr_number=int(stored["number"]),
        base=args.base,
        base_oid=args.base_oid,
        head=args.head,
        head_oid=args.head_oid,
        head_owner=args.head_owner,
        head_repository=args.head_repository,
    )
    receipt = load_receipts(resolve_receipt_root(), expected)[-1]
    print(
        json.dumps(
            {
                "repository": args.repository,
                "pr": stored["number"],
                "url": stored["url"],
                "is_draft": stored["isDraft"],
                **receipt.summary("verified"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
