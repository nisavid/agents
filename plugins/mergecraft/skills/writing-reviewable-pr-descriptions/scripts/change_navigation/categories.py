"""The single vocabulary for reviewer-facing change categories."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    visual_label: str
    semantic_name: str
    description: str
    color: str
    file_descriptor: str


CATEGORIES = (
    Category(
        "implementation",
        "IMPL",
        "Implementation",
        "non-test source and configuration",
        "0969DA",
        "implementation",
    ),
    Category("test", "TEST", "Tests", "automated verification", "6F5F9A", "test"),
    Category(
        "documentation",
        "DOC",
        "Documentation",
        "reviewer and user documentation",
        "3F7770",
        "documentation",
    ),
    Category(
        "generated", "GEN", "Generated", "generated artifacts", "76652F", "generated"
    ),
    Category(
        "other",
        "OTHER",
        "Other",
        "files outside the other categories",
        "57606A",
        "other",
    ),
)
CATEGORY_BY_LABEL = {category.visual_label: category for category in CATEGORIES}
CATEGORY_LABELS = tuple(category.visual_label for category in CATEGORIES)
CATEGORY_COLORS = {category.visual_label: category.color for category in CATEGORIES}


def category_title(label: str, additions: int, deletions: int) -> str:
    category = CATEGORY_BY_LABEL[label]
    return (
        f"{category.semantic_name}: {additions} additions, {deletions} deletions "
        f"({category.description})"
    )


TAXONOMY_NOTE = (
    "<sup>IMPL means non-test source and configuration. TEST means automated "
    "verification. DOC means reviewer and user documentation. GEN means generated "
    "artifacts. OTHER means files outside those categories. FILES shows added, "
    "modified, and removed files as +, ~, and −.</sup>"
)
