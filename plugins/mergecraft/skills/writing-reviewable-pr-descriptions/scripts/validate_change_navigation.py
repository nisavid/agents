#!/usr/bin/env python3
"""Validate the first-viewport Stack and Diff disclosures in a PR body."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from change_navigation.badges import validate_badges
from change_navigation.diff import touched_file_count, validate_diff
from change_navigation.diff_files import file_operation_counts, _groups
from change_navigation.diff_metrics import (
    atomic_totals,
    file_operation_kind,
    parse_file_link,
)
from change_navigation.metrics import category_metric_map
from change_navigation.model import alt_values
from change_navigation.parsing import (
    extract_details,
    extract_leading_details,
    first_nonempty_line,
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
    current_item_identity,
    current_item_file_operation_count,
    current_item_file_operations,
    current_item_metrics,
    inventory,
)
from change_navigation.types import classify_disclosures


def _validate_markup(  # noqa: C901
    body: str, expected_repository: str, expected_pr: int
) -> list[str]:
    """Return markup-only errors; this private helper is never publication proof."""
    errors: list[str] = []
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", expected_repository):
        errors.append("expected repository must use OWNER/REPO")
    if expected_pr < 1:
        errors.append("expected PR number must be positive")
    expected_identity = (expected_repository, expected_pr)
    lines = body.splitlines()
    first = first_nonempty_line(lines)
    if first < 0:
        return ["PR body is empty"]
    if lines[first].strip() != "<details>":
        errors.append("PR body must start with the Stack or Diff disclosure")

    blocks = extract_leading_details(lines)
    labels = classify_disclosures(blocks)
    has_canonical_diff_prefix = bool(
        labels
        and labels[0] == "DIFF"
        and all(label == "UNKNOWN" for label in labels[1:])
    )
    has_canonical_stack_prefix = bool(
        labels[:2] == ["STACK", "DIFF"]
        and all(label == "UNKNOWN" for label in labels[2:])
    )
    if not (has_canonical_diff_prefix or has_canonical_stack_prefix):
        errors.append(
            f"leading disclosure order must be [DIFF] or [STACK, DIFF], found {labels}"
        )
    all_labels = classify_disclosures(extract_details(lines))
    recognized_labels = [label for label in all_labels if label in {"STACK", "DIFF"}]
    expected_labels = ["STACK", "DIFF"] if labels[:1] == ["STACK"] else ["DIFF"]
    if recognized_labels != expected_labels:
        errors.append(
            "Stack and Diff disclosures must appear exactly once in the "
            "canonical prefix"
        )
    if _has_stack_heading(lines):
        errors.append("do not add a separate ## Stack section")

    if labels and labels[0] == "STACK":
        validate_stack(blocks[0], errors)
        if len(blocks) < 2:
            errors.append("stacked PR is missing its Diff disclosure")
        else:
            validate_diff(blocks[1], errors, expected_identity)
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
            ):
                errors.append(
                    "current Stack item file-operation total must match Diff "
                    "touched files"
                )
            stack_operations = current_item_file_operations(blocks[0])
            diff_operations = file_operation_counts(blocks[1])
            if stack_operations is not None and diff_operations.consistent:
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
        validate_diff(blocks[0], errors, expected_identity)
    else:
        errors.append("Diff disclosure is missing")

    recognized_blocks = blocks[:2] if labels[:2] == ["STACK", "DIFF"] else blocks[:1]
    navigation_text = "\n".join("\n".join(block) for block in recognized_blocks)
    validate_badges(navigation_text, errors)
    return errors


def _has_stack_heading(lines: list[str]) -> bool:
    fenced: tuple[str, int] | None = None
    for line in lines:
        stripped = line.lstrip()
        marker = None
        if stripped.startswith("```"):
            marker = ("`", len(stripped) - len(stripped.lstrip("`")))
        elif stripped.startswith("~~~"):
            marker = ("~", len(stripped) - len(stripped.lstrip("~")))
        if marker and (
            fenced is None or (marker[0] == fenced[0] and marker[1] >= fenced[1])
        ):
            fenced = None if fenced else marker
            continue
        if fenced is None and re.match(r"^\s{0,3}#{1,6}\s+stack\s*#*\s*$", line, re.I):
            return True
    return False


def _manifest_rows(block: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for group in _groups(block):
        for _, line in group.file_lines:
            link = parse_file_link(line)
            if not link:
                continue
            additions, deletions = atomic_totals([(0, line)])
            rows.append(
                {
                    "category": group.category,
                    "operation": file_operation_kind(line),
                    "source_path": link.source_path,
                    "target_path": link.target_path,
                    "additions": additions,
                    "deletions": deletions,
                }
            )
    return rows


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
    if _manifest_rows(diff_block) != review_input.raw["diff"]:
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
        if len(actual) != len(stack) or any(
            actual_item != expected for actual_item, expected in zip(actual, stack)
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
    """Validate publishable review text against its immutable review input."""
    secret_error = suspected_secret_error(body)
    if secret_error is not None:
        return [secret_error]
    errors = _validate_markup(body, expected_repository, expected_pr)
    blocks = extract_leading_details(body.splitlines())
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
        args.body.read_text(encoding="utf-8"),
        args.repository,
        args.pr,
        title=args.title,
        review_input_path=args.review_input,
        template_body=(
            args.template_body.read_text(encoding="utf-8")
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
