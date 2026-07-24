#!/usr/bin/env python3
"""Private Phase 7 conformance inventory construction."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class ProducerError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProducerError(message)


def build_conformance_inventory(sealed_roots: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive the complete read inventory from sealed roots, never probe count."""

    _require(
        isinstance(sealed_roots, list) and sealed_roots, "sealed roots are required"
    )
    roots: list[dict[str, Any]] = []
    reads: list[dict[str, str]] = []
    root_names: set[str] = set()
    for seal in sealed_roots:
        _require(
            isinstance(seal, dict)
            and isinstance(seal.get("root"), str)
            and seal["root"]
            and seal["root"] not in root_names
            and isinstance(seal.get("entries"), list)
            and seal["entries"],
            "sealed root inventory drift",
        )
        root_names.add(seal["root"])
        roots.append(
            {
                "root": seal["root"],
                "tree_sha256": seal.get("tree_sha256"),
                "entry_count": seal.get("entry_count"),
            }
        )
        for entry in seal["entries"]:
            if entry.get("type") == "regular":
                reads.append({"root": seal["root"], "path": entry["path"]})
    reads.sort(key=lambda item: (item["root"], item["path"]))
    _require(reads, "sealed roots do not expose a readable inventory")
    return {"sealed_request_roots": roots, "read_inventory": reads}


def validate_probe_results(
    conformance: dict[str, Any], *, probes: list[dict[str, Any]]
) -> None:
    """Require one successful result for every retained read, in canonical order."""

    expected = (
        conformance.get("read_inventory") if isinstance(conformance, dict) else None
    )
    _require(
        isinstance(expected, list) and expected,
        "conformance read inventory is required",
    )
    _require(
        isinstance(probes, list) and len(probes) == len(expected),
        "probe inventory is incomplete",
    )
    for inventory, probe in zip(expected, probes):
        _require(
            isinstance(probe, dict)
            and set(probe) == {"root", "path", "result"}
            and probe["root"] == inventory["root"]
            and probe["path"] == inventory["path"]
            and probe["result"] == "passed",
            "probe result does not match sealed read inventory",
        )


def build_public_conformance_summary(
    transient: dict[str, Any],
) -> dict[str, Any]:
    """Project verified transient names and probes into an opaque public binding."""

    _require(
        isinstance(transient, dict)
        and set(transient)
        == {"sealed_request_roots", "read_inventory", "probe_results"}
        and isinstance(transient["sealed_request_roots"], list)
        and transient["sealed_request_roots"]
        and isinstance(transient["read_inventory"], list)
        and transient["read_inventory"],
        "transient conformance inventory drift",
    )
    validate_probe_results(
        {"read_inventory": transient["read_inventory"]},
        probes=transient["probe_results"],
    )
    canonical = json.dumps(
        transient,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "sha256": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "sealed_root_count": len(transient["sealed_request_roots"]),
        "read_count": len(transient["read_inventory"]),
        "probe_count": len(transient["probe_results"]),
    }
