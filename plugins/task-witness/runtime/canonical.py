"""Canonical document and validator-identity primitives for Task Witness."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable

if globals().get("_TASK_WITNESS_LAUNCH_CONTEXT") is None:
    raise RuntimeError(
        "Task Witness runtime must be launched by task_witness_launch.py"
    )


TRUST_CONTRACT = "task-witness-trust-context-v2"
SCHEMA_VERSION = 1
DISPATCH_PROJECTION_CONTRACT = "task-witness-dispatch-projection-v1"
CANONICAL_PROJECTION_CONTRACT = "task-witness-canonical-projection-v2"
VALIDATOR_ARTIFACT_MANIFEST_CONTRACT = "task-witness-validator-artifact-manifest-v1"
MAX_JSON_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 100
MAX_JSON_NUMBER_CHARACTERS = 128
HEX = re.compile(r"[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


class EvidenceError(ValueError):
    """A supplied task-evidence bundle or trust snapshot is not trustworthy."""


def canonical_bytes(value: object) -> bytes:
    """Encode a value in the sole byte form accepted for signed documents."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: object) -> str:
    """Return the SHA-256 digest of a canonical value frame."""

    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON object keys while preserving normal decoding."""

    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise EvidenceError("JSON object has duplicate keys")
        result[key] = value
    return result


def constant(_: str) -> None:
    """Reject non-finite JSON numbers."""

    raise EvidenceError("JSON contains an unsupported number")


def integer(token: str) -> int:
    """Decode one bounded JSON integer independently of interpreter defaults."""

    if len(token) > MAX_JSON_NUMBER_CHARACTERS:
        raise EvidenceError("JSON numeric token exceeds the limit")
    return int(token)


def floating(token: str) -> float:
    """Decode one finite, bounded JSON float independently of interpreter defaults."""

    if len(token) > MAX_JSON_NUMBER_CHARACTERS:
        raise EvidenceError("JSON numeric token exceeds the limit")
    value = float(token)
    if not math.isfinite(value):
        raise EvidenceError("JSON contains an unsupported number")
    return value


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    """Require an object with no omitted or surplus keys."""

    if not isinstance(value, dict) or set(value) != keys:
        raise EvidenceError(f"{label} schema drift")
    return value


def text(value: Any, label: str) -> str:
    """Require a non-empty string."""

    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{label} must be a non-empty string")
    return value


def token(value: Any, label: str) -> str:
    """Require a closed lower-case token."""

    value = text(value, label)
    if not TOKEN.fullmatch(value):
        raise EvidenceError(f"{label} is not a closed token")
    return value


def sha(value: Any, label: str) -> str:
    """Require a lower-case SHA-256 digest."""

    value = text(value, label)
    if not HEX.fullmatch(value):
        raise EvidenceError(f"{label} must be a SHA-256 digest")
    return value


def token_list(value: Any, label: str) -> list[str]:
    """Require a duplicate-free list of tokens."""

    if not isinstance(value, list):
        raise EvidenceError(f"{label} must be a list")
    result = [token(item, f"{label} item") for item in value]
    if len(result) != len(set(result)):
        raise EvidenceError(f"{label} contains duplicates")
    return result


def text_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    """Require a string list, optionally requiring at least one element."""

    if not isinstance(value, list) or (nonempty and not value):
        raise EvidenceError(f"{label} must be a valid list")
    return [text(item, f"{label} item") for item in value]


def identity(value: Any, label: str, *, absent: bool = False) -> dict[str, Any]:
    """Require one content-addressed identity or the explicit absent marker."""

    if absent and value == {"kind": "absent"}:
        return value
    value = exact(value, {"kind", "value", "content_sha256"}, label)
    text(value["kind"], f"{label}.kind")
    text(value["value"], f"{label}.value")
    sha(value["content_sha256"], f"{label}.content_sha256")
    return value


def document(value: Any, keys: set[str], label: str, contract: str) -> dict[str, Any]:
    """Require a canonical, content-addressed document for one contract."""

    value = exact(value, keys | {"schema_version", "contract", "content_sha256"}, label)
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or value["contract"] != contract
    ):
        raise EvidenceError(f"{label} contract mismatch")
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    if sha(value["content_sha256"], f"{label}.content_sha256") != digest(unsigned):
        raise EvidenceError(f"{label} content digest mismatch")
    return value


def validator_implementation_identity(
    contract: str,
    entrypoint_module: str,
    modules: Iterable[tuple[str, str]],
) -> str:
    """Frame a validator's logical artifact manifest into its implementation ID.

    Paths are intentionally absent. The ordered module names and their individual
    content digests prevent equal concatenations from representing the same
    validator implementation.
    """

    validator_contract = text(contract, "validator artifact manifest.contract")
    entrypoint = token(entrypoint_module, "validator artifact manifest.entrypoint")
    framed_modules = []
    names: set[str] = set()
    for index, (name, content_sha256) in enumerate(modules):
        module_name = token(name, f"validator artifact module {index}.name")
        if module_name in names:
            raise EvidenceError(
                "validator artifact manifest has duplicate module names"
            )
        names.add(module_name)
        framed_modules.append(
            {
                "name": module_name,
                "content_sha256": sha(
                    content_sha256,
                    f"validator artifact module {index}.content_sha256",
                ),
            }
        )
    if not framed_modules or framed_modules[0]["name"] != entrypoint:
        raise EvidenceError("validator entrypoint must be the first declared module")
    return digest(
        {
            "contract": VALIDATOR_ARTIFACT_MANIFEST_CONTRACT,
            "validator_contract": validator_contract,
            "entrypoint_module": entrypoint,
            "modules": framed_modules,
        }
    )
