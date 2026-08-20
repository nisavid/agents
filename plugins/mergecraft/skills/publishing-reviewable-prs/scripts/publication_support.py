"""Shared internal support for reviewable-PR publication commands."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import IO

from reviewable_pr_state import (
    ExpectedIdentity,
    PublicationError,
    run_read,
    validate_identity_inputs,
)

WRITER_SCRIPTS = (
    Path(__file__).parents[2] / "writing-reviewable-pr-descriptions/scripts"
)
VALIDATOR = WRITER_SCRIPTS / "validate_change_navigation.py"


def validate_pr_content(
    body: str,
    repository: str,
    pr_number: int,
    title: str,
    review_input_path: Path,
    template_path: Path | None = None,
) -> None:
    """Validate one complete candidate title/body at the canonical CLI seam."""

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
    run_read(arguments, input_text=body)


def temporary_body(body: str) -> IO[str]:
    """Return a flushed private body file suitable for one forge command."""

    temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8")
    temporary.write(body)
    temporary.flush()
    return temporary


def expected_identity(
    *,
    repository: str,
    pr_number: int,
    base: str,
    base_oid: str,
    head: str,
    head_oid: str,
    head_owner: str,
    head_repository: str,
) -> ExpectedIdentity:
    """Validate and construct the exact PR identity shared by all commands."""

    validate_identity_inputs(
        repository=repository,
        pr_number=pr_number,
        base=base,
        base_oid=base_oid,
        head=head,
        head_oid=head_oid,
        head_owner=head_owner,
        head_repository=head_repository,
    )
    return ExpectedIdentity(
        repository=repository,
        pr_number=pr_number,
        base=base,
        base_oid=base_oid,
        head=head,
        head_oid=head_oid,
        head_owner=head_owner,
        head_repository=head_repository,
    )
