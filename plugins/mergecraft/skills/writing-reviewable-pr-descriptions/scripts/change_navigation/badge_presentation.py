"""Validate Shields style, height, and wrappers."""

from __future__ import annotations

import re
from html import escape, unescape
from urllib.parse import urlsplit

from .badge_colors import validate_color_and_label
from .categories import category_title
from .model import (
    CATEGORY_METRIC_SHAPE_RE,
    LINE_METRIC_TEXT_PATTERN,
    LINE_METRIC_TEXT_RE,
    SHIELD_IMAGE_RE,
    alt,
    attribute_values,
    parse_line_metric_counts,
    parse_line_metric_text,
    raw_attribute,
    source,
    title,
)


SUPPORTED_ALT_RE = re.compile(
    r"^(?:STACK|DIFF|STACK STATUS: TOP|BINARY|MOVED|COPIED|"
    r"STACK POSITION: \d+ OF \d+|"
    r"(?:BASE|DEP|NEXT): .+|"
    rf"(?:IMPL|TEST|DOC|GEN|OTHER): {LINE_METRIC_TEXT_PATTERN}|"
    r"FILES: (?:\d+ touched|"
    r"\d+ (?:implementation|test|documentation|generated|other) files?|"
    r"\d+ added, \d+ modified, \d+ removed"
    r"(?:, [1-9]\d* moved)?(?:, [1-9]\d* copied)?)|"
    rf"{LINE_METRIC_TEXT_PATTERN})$"
)


def validate_shields(text: str, errors: list[str]) -> None:
    for image in SHIELD_IMAGE_RE.findall(text):
        _validate_attribute_cardinality(image, errors)
        image_alt = alt(image)
        _validate_attribute_escaping(image, errors)
        _validate_line_metric_grammar(image_alt, errors)
        if not SUPPORTED_ALT_RE.fullmatch(image_alt):
            errors.append(f"unsupported or non-uppercase shield label: {image_alt}")
        source_url = source(image)
        expected_style = "for-the-badge" if image_alt in {"STACK", "DIFF"} else "flat"
        _validate_shields_url(source_url, image_alt, expected_style, errors)
        validate_color_and_label(image_alt, source_url, errors)


def _validate_attribute_cardinality(image: str, errors: list[str]) -> None:
    image_alt = alt(image)
    for attribute in ("alt", "src"):
        if len(attribute_values(image, attribute)) != 1:
            errors.append(
                f"shield must have exactly one real {attribute} attribute: "
                f"{image_alt or image}"
            )
    heights = attribute_values(image, "height")
    if heights != ["16"]:
        errors.append(f"shield must have exactly one 16px height: {image_alt or image}")

    titles = attribute_values(image, "title")
    category = CATEGORY_METRIC_SHAPE_RE.fullmatch(image_alt)
    title_required = bool(
        re.fullmatch(r"(?:BASE|DEP|NEXT): #\d+ — .+", image_alt)
        or LINE_METRIC_TEXT_RE.fullmatch(image_alt)
        or image_alt in {"BINARY", "MOVED", "COPIED"}
        or category
    )
    if title_required and (
        len(titles) != 1 or title(image) != _expected_title(image_alt)
    ):
        errors.append(f"{image_alt or 'shield'} needs exactly one matching title")
    elif not title_required and titles:
        errors.append(f"{image_alt or 'shield'} must not define a title")


def _expected_title(image_alt: str) -> str:
    category = CATEGORY_METRIC_SHAPE_RE.fullmatch(image_alt)
    if category:
        label, additions, deletions = category.groups()
        return category_title(label, int(additions), int(deletions))
    navigation = re.fullmatch(r"(?:BASE|DEP|NEXT): (#\d+ — .+)", image_alt)
    return navigation.group(1) if navigation else image_alt


def _validate_line_metric_grammar(image_alt: str, errors: list[str]) -> None:
    metric = (
        image_alt.split(": ", 1)[1]
        if CATEGORY_METRIC_SHAPE_RE.fullmatch(image_alt)
        else image_alt
    )
    if parse_line_metric_counts(metric) is not None and parse_line_metric_text(metric) is None:
        errors.append(
            f"metric badge has ungrammatical accessibility text: {image_alt}"
        )


def _validate_attribute_escaping(image: str, errors: list[str]) -> None:
    for attribute in ("alt", "title"):
        raw = raw_attribute(image, attribute)
        if not raw:
            continue
        semantic = unescape(raw)
        canonical = escape(semantic, quote=True).replace("&#x27;", "'")
        if raw != canonical:
            errors.append(f"shield {attribute} text must use canonical HTML escaping")


def _validate_shields_url(
    source_url: str, image_alt: str, expected_style: str, errors: list[str]
) -> None:
    try:
        parsed = urlsplit(source_url)
        port = parsed.port
    except ValueError:
        errors.append("shield URL is malformed")
        return
    if (
        parsed.scheme != "https"
        or parsed.hostname != "img.shields.io"
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.fragment
        or not parsed.path.startswith("/badge/")
    ):
        errors.append("shield URL must use the canonical Shields HTTPS badge endpoint")
        return
    expected_query = [("style", expected_style)]
    if LINE_METRIC_TEXT_RE.fullmatch(image_alt):
        expected_query.append(("labelColor", "1A7F37"))
    actual_query = (
        []
        if not parsed.query
        else [
            tuple(pair.split("=", 1)) if "=" in pair else (pair, "")
            for pair in parsed.query.split("&")
        ]
    )
    if actual_query != expected_query:
        errors.append(
            f"{image_alt or 'shield'} badge URL must use exactly ordered "
            "approved query keys"
        )
