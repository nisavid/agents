"""Fail-closed route-evidence verifier for the public Rolecasting package.

The package has no production route-evidence issuer, managed verification
capability, or authenticated status observer. Task Witness retains this module
in the registered validator closure so portable validation cannot fall back to
an issuer descriptor and unkeyed content digest. A later production release
must replace this denial-only implementation with a verifier backed by the
product-owned trust boundary.
"""

from __future__ import annotations

from typing import Any


class RouteEvidenceError(ValueError):
    """Authenticated route evidence is unavailable in this source release."""


def validate_authenticated_route_evidence(value: Any) -> Any:
    """Reject every record until a production trust capability is registered."""

    del value
    raise RouteEvidenceError("no production route-evidence verifier is registered")


__all__ = ["RouteEvidenceError", "validate_authenticated_route_evidence"]
