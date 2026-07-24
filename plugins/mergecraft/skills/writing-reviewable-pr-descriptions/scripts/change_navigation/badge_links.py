"""Validate linked PR badge accessibility metadata."""

from __future__ import annotations

import re

from .model import LINKED_SHIELD_RE, alt, title
from .urls import parse_github_url


NAVIGATION_PREFIXES = ("BASE: ", "DEP: ", "NEXT: ")


def validate_linked_badges(text: str, errors: list[str]) -> None:
    for href, image in LINKED_SHIELD_RE.findall(text):
        destination = parse_github_url(href)
        badge_alt = alt(image)
        badge_title = title(image)
        if not destination:
            errors.append(f"linked shield must navigate to a GitHub PR: {href}")
            continue
        pr_number = str(destination.pr_number)
        if not badge_alt or not badge_title:
            errors.append(f"linked PR badge #{pr_number} needs both alt and title")
            continue
        prefix = next(
            (item for item in NAVIGATION_PREFIXES if badge_alt.startswith(item)),
            "",
        )
        if not prefix:
            errors.append(f"linked PR badge #{pr_number} must be BASE, DEP, or NEXT")
            continue
        destination_text = badge_alt[len(prefix) :]
        if destination_text != badge_title:
            errors.append(f"linked PR badge #{pr_number} alt/title destinations differ")
        number_match = re.match(r"#(\d+) — ", destination_text)
        if not number_match or number_match.group(1) != pr_number:
            errors.append(f"linked PR badge #{pr_number} alt/title must name its PR")
        if " — " not in destination_text:
            errors.append(
                f"linked PR badge #{pr_number} alt/title must include a "
                "recognizable title"
            )
