"""Shared validation for endpoint authority host components."""

from __future__ import annotations

import ipaddress
import re
from typing import Any


DNS_LABEL = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)
LEGACY_IPV4_COMPONENT = re.compile(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)\Z")


def is_bare_endpoint_host(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 253:
        return False
    if (
        any(character.isspace() or ord(character) < 0x20 for character in value)
        or any(
            delimiter in value
            for delimiter in ("@", "/", "\\", "?", "#", "[", "]", "%")
        )
    ):
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        dns_host = value[:-1] if value.endswith(".") else value
        components = dns_host.split(".")
        if 1 <= len(components) <= 4 and all(
            LEGACY_IPV4_COMPONENT.fullmatch(component) is not None
            for component in components
        ):
            return False
        return bool(dns_host) and all(
            DNS_LABEL.fullmatch(label) is not None
            for label in dns_host.split(".")
        )


def canonical_endpoint_host(value: str) -> str:
    """Return the collision/approval identity for a validated bare host."""

    if not is_bare_endpoint_host(value):
        raise ValueError("endpoint host must be a bare DNS name or IP literal")
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return f"::ffff:{address.ipv4_mapped.compressed}"
        return address.compressed.lower()
    except ValueError:
        return value.rstrip(".").lower()
