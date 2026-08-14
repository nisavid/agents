"""Intrinsic, side-effect-free validation for Task Witness activation smoke."""

from __future__ import annotations

from typing import Any

BUNDLE_CONTRACT = "task-witness-smoke-bundle-v1"
PROJECTION_CONTRACT = "task-witness-smoke-projection-v1"
CHALLENGE = "task-witness-activation-smoke-v1"


def _validate_bundle(bundle: Any, *, trust_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Accept only the controller-owned activation challenge document."""

    del trust_snapshot
    witness = globals().get("_TASK_WITNESS")
    if witness is None:
        raise RuntimeError("Task Witness smoke validator requires the witnessed runtime")
    manifest, _ = bundle.read_json("manifest.json", "Task Witness smoke manifest")
    manifest = witness.exact(
        manifest,
        {"schema_version", "contract", "producer", "challenge"},
        "Task Witness smoke manifest",
    )
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["contract"] != BUNDLE_CONTRACT
        or manifest["challenge"] != CHALLENGE
    ):
        raise witness.EvidenceError("Task Witness smoke challenge mismatch")
    witness.exact(
        manifest["producer"],
        {"producer_id", "contract", "implementation_sha256"},
        "Task Witness smoke producer",
    )
    return {
        "schema_version": 1,
        "contract": PROJECTION_CONTRACT,
        "challenge": CHALLENGE,
        "accepted": True,
    }
