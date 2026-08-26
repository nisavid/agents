#!/usr/bin/env python3
"""Validate the first-viewport Stack and Diff disclosures in a PR body."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from change_navigation.badges import validate_badges
from change_navigation.diff import touched_file_count, validate_diff
from change_navigation.diff_files import (
    category_file_counts,
    file_operation_counts,
    manifest_rows,
    omission_record,
)
from change_navigation.metrics import category_metric_map
from change_navigation.model import alt_values
from change_navigation.parsing import (
    extract_leading_details,
    opaque_suffix,
    parse_navigation_prefix,
    source_lines,
    validate_reserved_suffix_signatures,
)
from change_navigation.review_input import (
    ReviewInputError,
    bind_review_input,
    load_review_input,
)
from change_navigation.sensitive_content import suspected_secret_error
from change_navigation.stack import validate_stack
from change_navigation.stack_inventory import (
    STACK_FILE_OPERATIONS_RE,
    current_item_file_operation_count,
    current_item_file_operations,
    current_item_identity,
    current_item_metrics,
    inventory,
)
from change_navigation.types import classify_disclosures


def _validate_markup(  # noqa: C901
    body: str, expected_repository: str, expected_pr: int, *, bounded: bool = False
) -> list[str]:
    """Return markup-only errors; this private helper is never publication proof."""
    errors: list[str] = []
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", expected_repository):
        errors.append("expected repository must use OWNER/REPO")
    if expected_pr < 1:
        errors.append("expected PR number must be positive")
    expected_identity = (expected_repository, expected_pr)
    lines = source_lines(body)
    prefix = parse_navigation_prefix(lines, errors)
    blocks = list(prefix.blocks) if prefix is not None else []
    labels = classify_disclosures(blocks)
    if labels not in (["DIFF"], ["STACK", "DIFF"]):
        errors.append(
            f"leading disclosure order must be [DIFF] or [STACK, DIFF], found {labels}"
        )
    if prefix is not None:
        validate_reserved_suffix_signatures(
            opaque_suffix(body, prefix.end_line),
            errors,
        )

    if labels and labels[0] == "STACK":
        validate_stack(blocks[0], errors)
        if len(blocks) < 2:
            errors.append("stacked PR is missing its Diff disclosure")
        else:
            validate_diff(blocks[1], errors, expected_identity, bounded=bounded)
            stack_metrics = current_item_metrics(blocks[0])
            diff_metrics = category_metric_map("\n".join(blocks[1][:2]))
            if stack_metrics != diff_metrics:
                errors.append(
                    "current Stack item category totals must match the Diff summary"
                )
            stack_files = current_item_file_operation_count(blocks[0])
            diff_files = touched_file_count(blocks[1])
            if (
                stack_files is not None
                and diff_files is not None
                and stack_files != diff_files
                and not bounded
            ):
                errors.append(
                    "current Stack item file-operation total must match Diff "
                    "touched files"
                )
            stack_operations = current_item_file_operations(blocks[0])
            diff_operations = file_operation_counts(blocks[1])
            if (
                stack_operations is not None
                and diff_operations.consistent
                and not bounded
            ):
                ordinary = sum(
                    stack_operations[kind] for kind in ("added", "modified", "removed")
                )
                if (
                    ordinary != diff_operations.ordinary
                    or stack_operations["moved"] != diff_operations.moved
                    or stack_operations["copied"] != diff_operations.copied
                ):
                    errors.append(
                        "current Stack item file-operation kinds must match Diff files"
                    )
        identity = current_item_identity(blocks[0])
        if identity != expected_identity:
            errors.append(
                f"current Stack item must be PR #{expected_pr} in {expected_repository}"
            )
    elif blocks:
        validate_diff(blocks[0], errors, expected_identity, bounded=bounded)
    else:
        errors.append("Diff disclosure is missing")

    recognized_blocks = blocks[:2] if labels == ["STACK", "DIFF"] else blocks[:1]
    navigation_text = "\n".join("\n".join(block) for block in recognized_blocks)
    validate_badges(navigation_text, errors)
    return errors


def _validate_manifest_semantics(
    body: str,
    blocks: list[list[str]],
    title: str,
    review_input_path: Path,
    repository: str,
    pr_number: int,
    template_body: str | None,
    git_repository: Path | None,
) -> list[str]:
    errors: list[str] = []
    try:
        review_input = load_review_input(review_input_path)
        bind_review_input(
            review_input,
            repository=repository,
            pr_number=pr_number,
            base=review_input.raw["base"]["ref"],
            base_oid=review_input.raw["base"]["oid"],
            head=review_input.raw["head"]["ref"],
            head_oid=review_input.raw["head"]["oid"],
            head_owner=review_input.raw["head"]["owner"],
            head_repository=review_input.raw["head"]["repository"],
            title=title,
            body=body,
            template_body=template_body,
            git_repository=git_repository,
        )
    except ReviewInputError as error:
        return [f"review input: {error}"]
    labels = classify_disclosures(blocks)
    if not blocks or (labels[:1] == ["STACK"] and len(blocks) < 2):
        return ["rendered Diff disclosure is missing"]
    diff_block = blocks[1] if labels[:2] == ["STACK", "DIFF"] else blocks[0]
    expected_category_totals: dict[str, tuple[int, int]] = {}
    expected_category_files: dict[str, set[str]] = {}
    for row in review_input.raw["diff"]:
        additions, deletions = expected_category_totals.get(row["category"], (0, 0))
        expected_category_totals[row["category"]] = (
            additions + row["additions"],
            deletions + row["deletions"],
        )
        expected_category_files.setdefault(row["category"], set()).add(
            row["target_path"]
        )
    expected_summary = {
        category: totals
        for category, totals in expected_category_totals.items()
        if totals != (0, 0)
    }
    rendered_summary = category_metric_map("\n".join(diff_block[:2]))
    if touched_file_count(diff_block) != len(review_input.raw["git_diff"]):
        errors.append("rendered Diff touched count does not match sealed git_diff")
    if rendered_summary != expected_summary:
        errors.append("rendered Diff category totals do not match sealed diff")
    if category_file_counts(diff_block) != {
        category: len(targets) for category, targets in expected_category_files.items()
    }:
        errors.append("rendered Diff category file counts do not match sealed diff")
    presentation = review_input.raw.get("presentation")
    expected_rows = review_input.raw["diff"]
    if presentation is not None:
        selected = set(presentation["selected_targets"])
        expected_rows = [row for row in expected_rows if row["target_path"] in selected]
        if omission_record(diff_block) != (
            presentation["omitted_count"],
            presentation["comparison_url"],
        ):
            errors.append("rendered Diff omission/comparison record is not canonical")
    elif omission_record(diff_block) is not None:
        errors.append("rendered Diff invents an omission/comparison record")
    if manifest_rows(diff_block) != expected_rows:
        errors.append("rendered Diff rows do not match the review input")
    stack = review_input.raw["stack"]
    if labels[:1] == ["STACK"]:
        actual = []
        for item in inventory(blocks[0]):
            operations = {}
            for badge in alt_values(item.metrics):
                match = STACK_FILE_OPERATIONS_RE.fullmatch(badge)
                if match:
                    operations = dict(
                        zip(
                            ("added", "modified", "removed", "moved", "copied"),
                            (int(value or 0) for value in match.groups()),
                        )
                    )
            actual.append(
                {
                    "number": item.number,
                    "title": item.destination_text.removeprefix(f"#{item.number} — "),
                    "url": f"https://github.com/{item.repository}/pull/{item.number}",
                    "current": item.current,
                    "metrics": {
                        key: list(value)
                        for key, value in category_metric_map(item.metrics).items()
                    },
                    "file_operations": operations,
                }
            )
        projected_stack = []
        for expected in stack:
            projected = dict(expected)
            if projected["number"] == "__PUBLISHING_REVIEWABLE_PRS_PR_NUMBER__":
                projected["number"] = pr_number
                projected["url"] = f"https://github.com/{repository}/pull/{pr_number}"
            projected_stack.append(projected)
        if len(actual) != len(projected_stack) or any(
            actual_item != expected
            for actual_item, expected in zip(actual, projected_stack)
        ):
            errors.append(
                "rendered Stack topology, titles, URLs, metrics, file operations, "
                "or current marker do not match the review input"
            )
    elif stack:
        errors.append("review input requires a Stack disclosure")
    return errors


def validate(
    body: str,
    expected_repository: str,
    expected_pr: int,
    *,
    title: str,
    review_input_path: Path,
    template_body: str | None = None,
    git_repository: Path | None = None,
) -> list[str]:
    """Validate review text against its content-addressed local review input."""
    if len(body) > 65536:
        return ["PR body exceeds GitHub's 65536-character limit"]
    secret_error = suspected_secret_error(body)
    if secret_error is not None:
        return [secret_error]
    try:
        review_input = load_review_input(review_input_path)
        bounded = review_input.raw.get("presentation") is not None
    except ReviewInputError:
        bounded = False
    errors = _validate_markup(body, expected_repository, expected_pr, bounded=bounded)
    blocks = extract_leading_details(source_lines(body))
    if not title.strip():
        errors.append("candidate title is empty")
    errors.extend(
        _validate_manifest_semantics(
            body,
            blocks,
            title,
            review_input_path,
            expected_repository,
            expected_pr,
            template_body,
            git_repository,
        )
    )
    return errors


def _read_utf8(path: Path) -> str:
    """Read exact source bytes without universal-newline normalization."""
    return path.read_bytes().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "body", type=Path, help="Markdown file containing the complete PR body"
    )
    parser.add_argument(
        "--template-body",
        type=Path,
        help="Original token-bearing body template for new-PR validation",
    )
    parser.add_argument("--repository", required=True, help="Expected OWNER/REPO")
    parser.add_argument("--pr", required=True, type=int, help="Expected PR number")
    parser.add_argument("--title", required=True, help="Complete candidate PR title")
    parser.add_argument(
        "--review-input",
        required=True,
        type=Path,
        help="Absolute review-input JSON manifest",
    )
    parser.add_argument(
        "--git-repository",
        type=Path,
        default=Path.cwd(),
        help="Exact clean Git worktree root containing the bound base/head objects",
    )
    args = parser.parse_args()
    errors = validate(
        _read_utf8(args.body),
        args.repository,
        args.pr,
        title=args.title,
        review_input_path=args.review_input,
        template_body=(
            _read_utf8(args.template_body)
            if args.template_body is not None
            else None
        ),
        git_repository=args.git_repository,
    )
    if errors:
        print("ERROR: Change navigation is invalid", file=sys.stderr)
        return 1
    print("Change navigation is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
