"""Canonical JSON encoding shared by every digest-bearing artifact."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any


DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_SAFE_INTEGER = (1 << 53) - 1


class StrictJsonError(ValueError):
    """A JSON value is ambiguous or not interoperable."""


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise StrictJsonError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _reject_non_finite_constant(value: str) -> None:
    raise StrictJsonError(f"non-finite JSON constant: {value}")


def _safe_integer(value: int) -> int:
    if abs(value) > MAX_SAFE_INTEGER:
        raise StrictJsonError(
            f"JSON integer exceeds the interoperable safe integer range: {value}"
        )
    return value


def _normalize_float(parsed: float, source: str) -> int | float:
    if not math.isfinite(parsed):
        raise StrictJsonError(f"non-finite JSON number: {source}")
    if abs(parsed) > MAX_SAFE_INTEGER:
        raise StrictJsonError(
            f"JSON number exceeds the interoperable safe integer range: {source}"
        )
    if parsed == 0.0:
        return 0
    if parsed.is_integer():
        return _safe_integer(int(parsed))
    return parsed


def _parse_finite_float(value: str) -> int | float:
    try:
        exact = Decimal(value)
    except InvalidOperation:
        raise StrictJsonError(f"invalid JSON number: {value}") from None
    if not exact.is_finite():
        raise StrictJsonError(f"non-finite JSON number: {value}")
    parsed = float(exact)
    if not math.isfinite(parsed):
        raise StrictJsonError(f"non-finite JSON number: {value}")
    if abs(exact) > MAX_SAFE_INTEGER:
        raise StrictJsonError(
            f"JSON number exceeds the interoperable safe integer range: {value}"
        )
    if exact.is_zero():
        return 0
    if exact == exact.to_integral_value():
        return _safe_integer(int(exact))
    if parsed == 0.0 or Decimal(repr(parsed)) != exact:
        raise StrictJsonError(f"JSON number loses precision: {value}")
    return parsed


def _parse_safe_integer(value: str) -> int:
    return _safe_integer(int(value))


def strict_json_loads(value: str | bytes | bytearray) -> Any:
    """Parse interoperable JSON without ambiguous object keys or numbers."""
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise StrictJsonError("JSON bytes must be strict UTF-8") from error
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_non_finite_constant,
            parse_float=_parse_finite_float,
            parse_int=_parse_safe_integer,
        )
    except json.JSONDecodeError as error:
        raise StrictJsonError("invalid JSON") from error
    _validate_unicode_scalars(parsed)
    return parsed


def _validate_unicode_scalars(value: Any) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise StrictJsonError("JSON string contains a non-scalar Unicode value")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_unicode_scalars(key)
            _validate_unicode_scalars(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_unicode_scalars(item)


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise StrictJsonError("canonical JSON object keys must be strings")
            _validate_unicode_scalars(key)
            normalized[key] = _normalize(item)
        return normalized
    elif isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    elif isinstance(value, bool) or value is None:
        return value
    elif isinstance(value, str):
        _validate_unicode_scalars(value)
        return value
    elif isinstance(value, int):
        return _safe_integer(int(value))
    elif isinstance(value, float):
        return _normalize_float(float(value), repr(value))
    raise StrictJsonError(
        f"unsupported canonical JSON value type: {type(value).__name__}"
    )


def canonical_scalar(value: Any) -> str | bool | int | float | None:
    """Validate and normalize one scalar under the canonical JSON contract."""

    if isinstance(value, (Mapping, list, tuple)):
        raise StrictJsonError("canonical JSON scalar cannot be a collection")
    normalized = _normalize(value)
    assert isinstance(normalized, (str, bool, int, float)) or normalized is None
    return normalized


def canonical_bytes(value: Any) -> bytes:
    """Encode a value with the RFC 8785 JSON Canonicalization Scheme."""

    def utf16_key(item: str) -> bytes:
        return item.encode("utf-16-be", "surrogatepass")

    def number(item: int | float) -> str:
        if isinstance(item, int):
            return str(item)
        source = repr(item).lower()
        absolute = abs(item)
        if 1e-6 <= absolute < 1e21:
            return format(Decimal(source), "f")
        mantissa, exponent = source.split("e")
        exponent_value = int(exponent)
        sign = "+" if exponent_value >= 0 else ""
        return f"{mantissa}e{sign}{exponent_value}"

    def encode(item: Any) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, (int, float)):
            return number(item)
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if isinstance(item, list):
            return "[" + ",".join(encode(value) for value in item) + "]"
        if isinstance(item, Mapping):
            return "{" + ",".join(
                f"{encode(key)}:{encode(item[key])}"
                for key in sorted(item, key=utf16_key)
            ) + "}"
        raise AssertionError("canonical normalization returned an unsupported type")

    return encode(_normalize(value)).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
