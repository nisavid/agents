"""Test-only authenticated route-evidence verifier for Task Witness fixtures."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

_KEY = b"test-only-route-evidence-key"
_ISSUER = {
    "issuer_id": "test-route-evidence",
    "contract": "rolecasting-route-evidence-issuer-v1",
    "implementation_sha256": hashlib.sha256(
        b"test route-evidence verifier"
    ).hexdigest(),
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def validate_authenticated_route_evidence(value: Any) -> Any:
    if value["evidence_issuer"] != _ISSUER:
        raise ValueError("route-evidence issuer is not trusted")
    unsigned = {
        key: item
        for key, item in value.items()
        if key not in {"content_sha256", "route_authorization_sha256"}
    }
    digest = hashlib.sha256(_canonical(unsigned)).hexdigest()
    if value["content_sha256"] != digest:
        raise ValueError("route-evidence content digest mismatch")
    expected = hmac.new(_KEY, digest.encode(), hashlib.sha256).hexdigest()
    if value["route_authorization_sha256"] != expected:
        raise ValueError("route-evidence authentication failed")
    return value
