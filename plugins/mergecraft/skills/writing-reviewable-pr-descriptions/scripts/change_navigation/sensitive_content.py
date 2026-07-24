"""Detect secret-shaped content without returning or logging the matched value."""

from __future__ import annotations

import re


SUSPECTED_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
        r"[A-Za-z0-9_-]{8,}\b"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"https://[^/@\s:]+:[^/@\s]+@"),
    re.compile(
        r"(?i)\b(?:api[ _-]?key|access[ _-]?token|client[ _-]?secret|password)"
        r"\s*[:=]\s*[`'\"]?(?!redacted\b|example\b|placeholder\b)"
        r"[A-Za-z0-9._~+/=-]{8,}"
    ),
)


def contains_suspected_secret(content: str) -> bool:
    return any(
        pattern.search(content) is not None for pattern in SUSPECTED_SECRET_PATTERNS
    )


def suspected_secret_error(content: str) -> str | None:
    if contains_suspected_secret(content):
        return (
            "PR body contains a suspected credential or secret; do not echo or "
            "republish it, and require authorized removal and rotation"
        )
    return None
