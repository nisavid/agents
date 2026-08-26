"""Recognize the strict leading Stack/Diff disclosure prefix.

This module is deliberately not a Markdown parser. Keep the suffix opaque: do
not add fence, list, quote, link, reference, table, comment, inline-code, or raw
HTML state here. A broader ambiguity rule needs either a narrow byte signature
or a separately reviewed standards-compliant dependency.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from .types import classify_disclosures


DETAILS_OPEN = "<details>"
DETAILS_CLOSE = "</details>"
STACK_FINGERPRINT = (
    "https://img.shields.io/badge/STACK-57606A?style=for-the-badge"
)
DIFF_FINGERPRINT = (
    "https://img.shields.io/badge/DIFF-57606A?style=for-the-badge"
)
STACK_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,6}[ \t]+stack[ \t]*#*[ \t]*$",
    re.IGNORECASE,
)
MARKDOWN_LINE_ENDING_RE = re.compile(r"\r\n|\r|\n")
RESERVED_FINGERPRINT_RE = re.compile(
    "(?:"
    + "|".join(re.escape(value) for value in (STACK_FINGERPRINT, DIFF_FINGERPRINT))
    + r")(?=$|[\s\\\"'<>`)\],.;:!?}#&])",
    re.IGNORECASE,
)


class NavigationPrefix(NamedTuple):
    blocks: tuple[list[str], ...]
    end_line: int


def source_lines(body: str) -> list[str]:
    """Split only on Markdown source line endings: LF, CRLF, or CR."""
    if not body:
        return []
    return MARKDOWN_LINE_ENDING_RE.split(body)


def parse_navigation_prefix(
    lines: list[str], errors: list[str]
) -> NavigationPrefix | None:
    """Parse only the writer-owned full-line disclosure prefix."""
    if not lines:
        errors.append("PR body is empty")
        return None
    if lines[0] != DETAILS_OPEN:
        errors.append(
            "PR body must start at byte zero with an exact full-line "
            "<details> Stack or Diff disclosure"
        )
        return None

    first = _parse_full_line_details(lines, 0, errors)
    if first is None:
        return None
    first_block, first_end = first
    blocks = [first_block]
    labels = classify_disclosures(blocks)
    end_line = first_end

    if labels == ["STACK"]:
        second_start = _require_blank_boundary(
            lines,
            first_end,
            errors,
            "between Stack and Diff disclosures",
        )
        if second_start >= len(lines) or lines[second_start] != DETAILS_OPEN:
            errors.append("stacked PR is missing its leading Diff disclosure")
        else:
            second = _parse_full_line_details(lines, second_start, errors)
            if second is not None:
                second_block, end_line = second
                blocks.append(second_block)

    _require_blank_boundary(
        lines,
        end_line,
        errors,
        "after the canonical navigation prefix",
    )
    return NavigationPrefix(tuple(blocks), end_line)


def extract_leading_details(lines: list[str]) -> list[list[str]]:
    """Return the strict writer-owned navigation blocks, if structurally parseable."""
    errors: list[str] = []
    prefix = parse_navigation_prefix(lines, errors)
    return list(prefix.blocks) if prefix is not None else []


def opaque_suffix(body: str, prefix_end_line: int) -> str:
    """Slice suffix bytes after the prefix without newline normalization."""
    if prefix_end_line < 0:
        return ""
    line_index = 0
    for line_ending in MARKDOWN_LINE_ENDING_RE.finditer(body):
        if line_index == prefix_end_line:
            return body[line_ending.end() :]
        line_index += 1
    return ""


def validate_reserved_suffix_signatures(suffix: str, errors: list[str]) -> None:
    """Fail closed on reserved navigation fingerprints without parsing Markdown."""
    if RESERVED_FINGERPRINT_RE.search(suffix):
        errors.append(
            "Stack and Diff disclosures must appear exactly once in the canonical "
            "prefix; the opaque suffix contains a reserved navigation fingerprint"
        )
    if any(STACK_HEADING_RE.fullmatch(line) for line in source_lines(suffix)):
        errors.append(
            "do not add a separate ## Stack section; the opaque suffix contains "
            "a reserved Stack-heading signature"
        )


def _parse_full_line_details(
    lines: list[str], start: int, errors: list[str]
) -> tuple[list[str], int] | None:
    if start >= len(lines) or lines[start] != DETAILS_OPEN:
        errors.append("navigation disclosure must open with an exact full-line <details>")
        return None
    for index in range(start + 1, len(lines)):
        if lines[index] == DETAILS_OPEN:
            errors.append("navigation disclosure cannot nest another full-line <details>")
            return None
        if lines[index] == DETAILS_CLOSE:
            return lines[start : index + 1], index
    errors.append("navigation disclosure is missing its exact full-line </details>")
    return None


def _require_blank_boundary(
    lines: list[str], after: int, errors: list[str], boundary: str
) -> int:
    following = after + 1
    if following >= len(lines):
        return following
    if lines[following] != "":
        errors.append(f"an empty source line is required {boundary}")
        return following
    while following < len(lines) and lines[following] == "":
        following += 1
    return following


def summary(block: list[str], errors: list[str], label: str) -> str:
    text = "\n".join(block)
    if text.count("<summary>") != 1 or text.count("</summary>") != 1:
        errors.append(f"{label} disclosure must contain exactly one summary pair")
        return ""
    summary_lines = [
        line for line in block if "<summary>" in line or "</summary>" in line
    ]
    if len(summary_lines) != 1:
        errors.append(f"{label} summary must occupy exactly one source line")
        return ""
    value = summary_lines[0].strip()
    if not (value.startswith("<summary>") and value.endswith("</summary>")):
        errors.append(f"{label} summary must open and close on the same line")
    return value
