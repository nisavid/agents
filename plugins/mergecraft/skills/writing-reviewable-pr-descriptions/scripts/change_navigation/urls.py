"""Strict, urllib-based URLs accepted in review navigation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class GitHubUrl:
    owner: str
    repository: str
    pr_number: int
    files: bool
    fragment: str


def parse_github_url(value: str, *, file_link: bool = False) -> GitHubUrl | None:
    """Accept exactly one public GitHub PR or Files-changed URL shape."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
    ):
        return None
    parts = parsed.path.split("/")
    expected_length = 6 if file_link else 5
    if len(parts) != expected_length or parts[0] or parts[3] != "pull":
        return None
    owner, repository, number = parts[1], parts[2], parts[4]
    if not NAME_RE.fullmatch(owner) or not NAME_RE.fullmatch(repository):
        return None
    if not number.isdecimal() or int(number) < 1:
        return None
    if file_link:
        if parts[5] != "files" or not re.fullmatch(
            r"diff-[0-9a-f]{64}", parsed.fragment
        ):
            return None
    elif parsed.fragment:
        return None
    return GitHubUrl(owner, repository, int(number), file_link, parsed.fragment)
