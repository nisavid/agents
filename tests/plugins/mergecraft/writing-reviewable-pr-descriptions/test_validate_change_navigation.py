from __future__ import annotations

import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Optional


REPOSITORY = Path(__file__).resolve().parents[4]
SCRIPT = (
    REPOSITORY
    / "plugins/mergecraft/skills/writing-reviewable-pr-descriptions/scripts"
    / "validate_change_navigation.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("validate_change_navigation", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PRODUCTION_VALIDATE = MODULE.validate


def validate_fixture(body: str) -> list[str]:
    return MODULE._validate_markup(body, "acme/app", 2)


MODULE.validate = validate_fixture


def badge(
    alt: str,
    path: str,
    *,
    style: str = "flat",
    title: Optional[str] = None,
    label_color: Optional[str] = None,
) -> str:
    category = __import__("re").fullmatch(
        r"(IMPL|TEST|DOC|GEN|OTHER): (\d+) additions, (\d+) deletions", alt
    )
    if category and title is None:
        from change_navigation.categories import category_title

        label, additions, deletions = category.groups()
        title = category_title(label, int(additions), int(deletions))
    query = f"style={style}"
    if label_color:
        query += f"&labelColor={label_color}"
    title_attribute = f' title="{title}"' if title else ""
    return (
        f'<picture><img alt="{alt}"{title_attribute} '
        f'src="https://img.shields.io/badge/{path}?{query}" height="16"></picture>'
    )


def linked_badge(pr_number: int, alt: str, path: str) -> str:
    destination = alt.split(": ", 1)[1]
    image = (
        badge(alt, path, title=destination)
        .removeprefix("<picture>")
        .removesuffix("</picture>")
    )
    return f'<a href="https://github.com/acme/app/pull/{pr_number}">{image}</a>'


def atomic_metric(additions: int, deletions: int) -> str:
    title = f"{additions} additions, {deletions} deletions"
    path = f"%2B{additions}-%E2%88%92{deletions}-CF222E"
    return badge(title, path, title=title, label_color="1A7F37")


def diff_body() -> str:
    anchor = hashlib.sha256(b"src/widget.ts").hexdigest()
    summary = " ".join(
        [
            badge("DIFF", "DIFF-57606A", style="for-the-badge"),
            badge("IMPL: 9 additions, 3 deletions", "IMPL-%2B9%20%E2%88%923-0969DA"),
            badge("FILES: 1 touched", "FILES-1-5F6B78"),
        ]
    ).replace("</picture> ", "</picture>&nbsp;", 1)
    category = " ".join(
        [
            badge("IMPL: 9 additions, 3 deletions", "IMPL-%2B9%20%E2%88%923-0969DA"),
            badge("FILES: 1 implementation file", "FILES-1-5F6B78"),
        ]
    )
    file_item = (
        "  - [`src/widget.ts`](https://github.com/acme/app/pull/2/files#diff-"
        f"{anchor}) " + atomic_metric(9, 3)
    )
    return "\n".join(
        [
            "<details>",
            f"<summary>{summary}</summary>",
            "",
            f"- {category}",
            file_item,
            "",
            "<sup>IMPL means non-test source and configuration. TEST means automated "
            "verification. DOC means reviewer and user documentation. "
            "GEN means generated "
            "artifacts. OTHER means files outside those categories. FILES shows added, "
            "modified, and removed files as +, ~, and −.</sup>",
            "",
            "</details>",
            "",
            "## Summary",
            "- Add the widget.",
            "",
        ]
    )


def stack_body() -> str:
    summary = " ".join(
        [
            badge("STACK", "STACK-57606A", style="for-the-badge"),
            badge("STACK POSITION: 2 OF 2", "2%20OF%202-5F6B78"),
            linked_badge(1, "BASE: #1 — feat: base", "BASE-%231-5F6B78"),
            badge("STACK STATUS: TOP", "TOP-5F6B78"),
        ]
    ).replace("</picture> ", "</picture>&nbsp;", 1)
    base_metrics = " ".join(
        [
            badge("IMPL: 1 additions, 0 deletions", "IMPL-%2B1%20%E2%88%920-0969DA"),
            badge(
                "FILES: 1 added, 0 modified, 0 removed",
                "FILES-%2B1%20~0%20%E2%88%920-5F6B78",
            ),
        ]
    )
    top_metrics = " ".join(
        [
            badge("IMPL: 9 additions, 3 deletions", "IMPL-%2B9%20%E2%88%923-0969DA"),
            badge(
                "FILES: 0 added, 1 modified, 0 removed",
                "FILES-%2B0%20~1%20%E2%88%920-5F6B78",
            ),
        ]
    )
    return "\n".join(
        [
            "<details>",
            f"<summary>{summary}</summary>",
            "",
            "- **[#1 — feat: base](https://github.com/acme/app/pull/1)**<br>"
            + base_metrics,
            "- **[#2 — feat: top](https://github.com/acme/app/pull/2)** "
            "**← this PR**<br>" + top_metrics,
            "",
            "<sup>IMPL means non-test source and configuration. TEST means automated "
            "verification. DOC means reviewer and user documentation. "
            "GEN means generated "
            "artifacts. OTHER means files outside those categories. FILES shows added, "
            "modified, and removed files as +, ~, and −.</sup>",
            "",
            "</details>",
            "",
        ]
    )


DIFF = diff_body()
STACK = stack_body() + "\n"


class ValidateChangeNavigationTests(unittest.TestCase):
    def test_rejects_secret_shaped_candidate_without_echoing_value(self) -> None:
        secret = "ghp_123456789012345678901234567890"

        error = MODULE.suspected_secret_error(f"Credential: {secret}")

        self.assertIsNotNone(error)
        self.assertNotIn(secret, error or "")

    def test_accepts_unstacked_diff(self) -> None:
        self.assertEqual(MODULE.validate(DIFF), [])

    def test_accepts_stack_then_diff(self) -> None:
        self.assertEqual(MODULE.validate(STACK + DIFF), [])

    def test_accepts_grammatical_singular_line_metrics(self) -> None:
        singular = DIFF.replace(
            "9 additions, 3 deletions",
            "1 addition, 0 deletions",
        ).replace(
            "%2B9%20%E2%88%923",
            "%2B1%20%E2%88%920",
        ).replace(
            "%2B9-%E2%88%923",
            "%2B1-%E2%88%920",
        )

        self.assertEqual(MODULE.validate(singular), [])

    def test_accepts_grammatical_singular_deletion_metric(self) -> None:
        singular = DIFF.replace(
            "9 additions, 3 deletions",
            "0 additions, 1 deletion",
        ).replace(
            "%2B9%20%E2%88%923",
            "%2B0%20%E2%88%921",
        ).replace(
            "%2B9-%E2%88%923",
            "%2B0-%E2%88%921",
        )

        self.assertEqual(MODULE.validate(singular), [])

    def test_rejects_singular_noun_for_non_singular_count(self) -> None:
        broken = DIFF.replace(
            "9 additions, 3 deletions",
            "9 addition, 3 deletions",
        )

        self.assertTrue(
            any("ungrammatical" in error for error in MODULE.validate(broken))
        )

    def test_requires_empty_source_line_before_suffix(self) -> None:
        broken = DIFF.replace("</details>\n\n## Summary", "</details>\n## Summary")

        self.assertTrue(
            any("empty source line" in error for error in MODULE.validate(broken))
        )

    def test_requires_empty_source_line_between_stack_and_diff(self) -> None:
        broken = STACK.rstrip("\n") + "\n" + DIFF

        self.assertTrue(
            any("between Stack and Diff" in error for error in MODULE.validate(broken))
        )

    def test_rejects_whitespace_only_prefix_boundary(self) -> None:
        broken = STACK.replace("</details>\n\n", "</details>\n \n", 1) + DIFF

        self.assertTrue(
            any("empty source line" in error for error in MODULE.validate(broken))
        )

    def test_requires_exact_full_line_disclosure_at_byte_zero(self) -> None:
        cases = (
            "\n" + DIFF,
            " " + DIFF,
            DIFF.replace("<details>", "<details open>", 1),
            DIFF.replace("<details>", "<DETAILS>", 1),
            "prefix " + DIFF,
        )
        for broken in cases:
            with self.subTest(first_line=broken.splitlines()[0]):
                self.assertTrue(
                    any(
                        "byte zero" in error or "exact full-line" in error
                        for error in MODULE.validate(broken)
                    )
                )

    def test_rejects_non_markdown_line_separators_in_prefix(self) -> None:
        for separator in ("\v", "\f", "\x85", "\u2028", "\u2029"):
            with self.subTest(separator=ascii(separator)):
                broken = DIFF.replace("<details>\n", "<details>" + separator, 1)
                self.assertTrue(
                    any(
                        "byte zero" in error or "exact full-line" in error
                        for error in MODULE.validate(broken)
                    )
                )

    def test_accepts_lf_crlf_and_cr_prefix_boundaries(self) -> None:
        prefix = DIFF.split("\n\n## Summary", 1)[0]
        for newline in ("\n", "\r\n", "\r"):
            with self.subTest(newline=ascii(newline)):
                body = prefix.replace("\n", newline) + newline * 2 + "## Summary"
                self.assertEqual(MODULE.validate(body), [])

    def test_requires_exact_full_line_closing_tag(self) -> None:
        broken = DIFF.replace("</details>", " </details>", 1)

        self.assertTrue(
            any("exact full-line </details>" in error for error in MODULE.validate(broken))
        )

    def test_rejects_nested_canonical_disclosure_in_prefix(self) -> None:
        broken = DIFF.replace("\n</details>", "\n<details>\n</details>", 1)

        self.assertTrue(
            any("cannot nest" in error for error in MODULE.validate(broken))
        )

    def test_rejects_stack_without_leading_diff(self) -> None:
        self.assertTrue(
            any(
                "missing its leading Diff" in error
                for error in MODULE.validate(STACK)
            )
        )

    def test_rejects_non_diff_block_after_stack(self) -> None:
        unrelated = "\n".join(
            [
                "<details>",
                "<summary>Additional evidence</summary>",
                "",
                "Opaque evidence.",
                "",
                "</details>",
                "",
            ]
        )

        self.assertTrue(
            any(
                "disclosure order" in error
                for error in MODULE.validate(STACK + unrelated)
            )
        )

    def test_accepts_arbitrary_opaque_suffix_bytes(self) -> None:
        prefix = DIFF.split("\n\n## Summary", 1)[0]
        suffix = "\r\n".join(
            [
                "## Summary",
                "- Keep `inline code`, [links][ref], and literal <details> bytes.",
                "",
                "[ref]: https://example.com/docs",
                "",
                "| surface | result |",
                "| --- | --- |",
                "| suffix | opaque |",
                "",
                "> quoted",
                "",
                "<div data-note=\"raw\">HTML</div>",
                "",
                "```md",
                "<details>",
                "<summary>Example without reserved navigation badges</summary>",
                "</details>",
                "```",
                "",
            ]
        )

        self.assertEqual(MODULE.validate(prefix + "\r\n\r\n" + suffix), [])

    def test_rejects_reserved_navigation_fingerprint_in_opaque_suffix(self) -> None:
        prefix = DIFF.split("\n\n## Summary", 1)[0]
        for label in ("STACK", "DIFF"):
            with self.subTest(label=label):
                example = badge(label, f"{label}-57606A", style="for-the-badge")
                body = prefix + "\n\n```html\n" + example + "\n```\n"
                self.assertTrue(
                    any(
                        "reserved navigation fingerprint" in error
                        for error in MODULE.validate(body)
                    )
                )

        escaped = (
            "https://img.shields.io/badge/DIFF-57606A?style=for-the-badge"
            + r'\"'
        )
        self.assertTrue(
            any(
                "reserved navigation fingerprint" in error
                for error in MODULE.validate(prefix + "\n\n" + escaped)
            )
        )
        fingerprint = (
            "https://img.shields.io/badge/DIFF-57606A?style=for-the-badge"
        )
        for extension in ("#copy", "&logo=github"):
            with self.subTest(extension=extension):
                self.assertTrue(
                    any(
                        "reserved navigation fingerprint" in error
                        for error in MODULE.validate(
                            prefix + "\n\n" + fingerprint + extension
                        )
                    )
                )

    def test_accepts_fingerprint_followed_by_token_continuation_in_opaque_suffix(
        self,
    ) -> None:
        prefix = DIFF.split("\n\n## Summary", 1)[0]
        # URL delimiters remain reserved; an identifier token may continue.
        suffix = (
            "https://img.shields.io/badge/DIFF-57606A?style=for-the-badger"
        )

        self.assertEqual(MODULE.validate(prefix + "\n\n" + suffix), [])

    def test_rejects_reserved_stack_heading_signature_in_opaque_suffix(self) -> None:
        prefix = DIFF.split("\n\n## Summary", 1)[0]
        for newline in ("\n", "\r\n", "\r"):
            with self.subTest(newline=ascii(newline)):
                body = prefix + newline * 2 + "## Stack" + newline
                self.assertTrue(
                    any(
                        "reserved Stack-heading" in error
                        for error in MODULE.validate(body)
                    )
                )

    def test_accepts_unrelated_disclosure_in_opaque_suffix(self) -> None:
        prefix = DIFF.split("\n\n## Summary", 1)[0]
        suffix = "\n".join(
            [
                "<details>",
                "<summary>Additional evidence</summary>",
                "",
                badge("EXTRA", "EXTRA-red"),
                "",
                "</details>",
                "",
            ]
        )

        self.assertEqual(MODULE.validate(prefix + "\n\n" + suffix), [])

    def test_rejects_split_file_metrics(self) -> None:
        broken = DIFF.replace(
            atomic_metric(9, 3),
            badge("9 additions", "%2B9-1A7F37")
            + " "
            + badge("3 deletions", "%E2%88%923-CF222E"),
        )
        self.assertTrue(any("atomic" in error for error in MODULE.validate(broken)))

    def test_rejects_unclosed_canonical_diff_disclosure(self) -> None:
        unclosed = DIFF.replace("\n</details>", "", 1)
        self.assertTrue(
            any(
                "missing its exact full-line </details>" in error
                for error in MODULE.validate(unclosed)
            )
        )

    def test_rejects_wrong_height(self) -> None:
        broken = DIFF.replace('height="16"', 'height="20"', 1)
        self.assertTrue(any("16px" in error for error in MODULE.validate(broken)))


if __name__ == "__main__":
    unittest.main()
