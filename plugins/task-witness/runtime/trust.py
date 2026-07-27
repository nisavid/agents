"""Trust lifecycle and registered-validator handling for Task Witness."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

if globals().get("_TASK_WITNESS_LAUNCH_CONTEXT") is None:
    raise RuntimeError(
        "Task Witness runtime must be launched by task_witness_launch.py"
    )


C = globals().get("_CANONICAL")
IO = globals().get("_BUNDLE_IO")
if C is None or IO is None:
    raise RuntimeError("Task Witness runtime dependencies were not injected")


MAX_VALIDATOR_MODULES = 32
MAX_VALIDATOR_ARTIFACT_BYTES = 1024 * 1024


def _lifecycle(value: Any, label: str, *, fields: set[str]) -> dict[str, Any]:
    value = C.exact(value, fields | {"state", "usable_for_new_publication"}, label)
    if (
        value["state"] not in {"active", "historical-usable", "revoked"}
        or type(value["usable_for_new_publication"]) is not bool
    ):
        raise C.EvidenceError(f"{label} lifecycle is invalid")
    return value


def _usable(entry: dict[str, Any], new_publication: bool) -> bool:
    return entry["state"] != "revoked" and (
        not new_publication
        or (entry["state"] == "active" and entry["usable_for_new_publication"])
    )


def _validate_validator_artifact_manifest(item: dict[str, Any], label: str) -> None:
    entrypoint = C.token(item["entrypoint"], f"{label}.entrypoint")
    modules = item["modules"]
    if not isinstance(modules, list) or not modules:
        raise C.EvidenceError("validator artifact manifest is missing modules")
    if len(modules) > MAX_VALIDATOR_MODULES:
        raise C.EvidenceError("validator artifact manifest exceeds the module limit")
    names: set[str] = set()
    paths: set[Path] = set()
    framed_modules: list[tuple[str, str]] = []
    for index, module in enumerate(modules):
        module = C.exact(
            module, {"name", "path", "sha256"}, "validator artifact module"
        )
        name = C.token(module["name"], "validator artifact module.name")
        path = IO.absolute(
            Path(C.text(module["path"], "validator artifact module.path")),
            "validator artifact module.path",
        )
        content_sha256 = C.sha(module["sha256"], "validator artifact module.sha256")
        if name in names or path in paths:
            raise C.EvidenceError(
                "validator artifact manifest has a duplicate module name or path"
            )
        if index == 0 and name != entrypoint:
            raise C.EvidenceError(
                "validator entrypoint must be the first declared module"
            )
        names.add(name)
        paths.add(path)
        framed_modules.append((name, content_sha256))
    if entrypoint not in names:
        raise C.EvidenceError(
            "validator entrypoint is absent from its artifact manifest"
        )
    expected = C.validator_implementation_identity(
        C.text(item["contract"], f"{label}.contract"),
        entrypoint,
        framed_modules,
    )
    if (
        C.sha(item["implementation_sha256"], f"{label}.implementation_sha256")
        != expected
    ):
        raise C.EvidenceError("validator artifact manifest identity mismatch")


def open_trust(path: Path, new_publication: bool) -> dict[str, Any]:
    """Capture and validate a private operator trust context."""

    path = IO.absolute(path, "trust context")
    chain = IO.open_chain(path.parent, "trust context parent")
    try:
        descriptor, expected = IO.open_at(chain[-1][0], path.name, "trust context")
    except BaseException:
        IO.close_chain(chain)
        raise
    try:
        raw = IO.read_descriptor(descriptor, expected, "trust context")
        trust = C.document(
            IO.json_object(raw, "trust context"),
            {"producers", "issuers", "validators"},
            "trust context",
            C.TRUST_CONTRACT,
        )
        entries: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {
            "producer": {},
            "issuer": {},
            "validator": {},
        }
        definitions = (
            (
                "producer",
                "producer_id",
                {
                    "producer_id",
                    "contract",
                    "implementation_sha256",
                    "validator_id",
                    "validator_contract",
                    "validator_implementation_sha256",
                },
            ),
            (
                "issuer",
                "issuer_id",
                {"issuer_id", "contract", "implementation_sha256", "capabilities"},
            ),
            (
                "validator",
                "validator_id",
                {
                    "validator_id",
                    "contract",
                    "implementation_sha256",
                    "entrypoint",
                    "modules",
                },
            ),
        )
        for category, identifier, fields in definitions:
            values = trust[f"{category}s"]
            if not isinstance(values, list) or not values:
                raise C.EvidenceError(f"trust context {category}s are missing")
            for index, item in enumerate(values):
                label = f"trust context.{category}[{index}]"
                item = _lifecycle(item, label, fields=fields)
                entry_id = C.token(item[identifier], f"{label}.{identifier}")
                contract = C.text(item["contract"], f"{label}.contract")
                implementation = C.sha(
                    item["implementation_sha256"], f"{label}.implementation_sha256"
                )
                if category == "producer":
                    C.token(item["validator_id"], f"{label}.validator_id")
                    C.text(item["validator_contract"], f"{label}.validator_contract")
                    C.sha(
                        item["validator_implementation_sha256"],
                        f"{label}.validator_implementation_sha256",
                    )
                elif category == "issuer":
                    capabilities = item["capabilities"]
                    if (
                        not isinstance(capabilities, list)
                        or not capabilities
                        or len(capabilities) != len(set(capabilities))
                    ):
                        raise C.EvidenceError(f"{label}.capabilities are invalid")
                    for capability in capabilities:
                        C.token(capability, f"{label}.capability")
                else:
                    _validate_validator_artifact_manifest(item, label)
                key = (entry_id, contract, implementation)
                if key in entries[category]:
                    raise C.EvidenceError("trust context has a duplicate identity")
                if _usable(item, new_publication):
                    entries[category][key] = item
        return {
            "chain": chain,
            "name": path.name,
            "descriptor": descriptor,
            "descriptor_identity": expected,
            "raw": raw,
            "entries": entries,
            "new_publication": new_publication,
        }
    except BaseException:
        os.close(descriptor)
        IO.close_chain(chain)
        raise


def recheck_trust(snapshot: dict[str, Any]) -> None:
    if (
        IO.read_descriptor(
            snapshot["descriptor"],
            snapshot["descriptor_identity"],
            "retained trust context",
        )
        != snapshot["raw"]
    ):
        raise C.EvidenceError("trust context changed during validation")
    IO.recheck_chain(snapshot["chain"], "trust context")
    if (
        IO.descriptor_identity(
            os.stat(
                snapshot["name"],
                dir_fd=snapshot["chain"][-1][0],
                follow_symlinks=False,
            )
        )
        != snapshot["descriptor_identity"]
    ):
        raise C.EvidenceError("trust context changed during validation")


def close_trust(snapshot: dict[str, Any]) -> None:
    error = None
    try:
        os.close(snapshot["descriptor"])
    except OSError as caught:
        error = caught
    try:
        IO.close_chain(snapshot["chain"])
    except OSError as caught:
        if error is None:
            error = caught
    if error is not None:
        raise error


def trusted(
    snapshot: dict[str, Any],
    category: str,
    identifier: str,
    contract: str,
    implementation: str,
    label: str,
    capability: str | None = None,
) -> dict[str, Any]:
    entry = (
        snapshot["entries"]
        .get(category, {})
        .get((identifier, contract, implementation))
    )
    if entry is None:
        raise C.EvidenceError(f"{label} is not accepted by the operator trust context")
    if capability is not None and capability not in entry.get("capabilities", []):
        raise C.EvidenceError(f"{label} is not authorized for {capability}")
    return entry


def producer(value: Any, snapshot: dict[str, Any], label: str) -> dict[str, Any]:
    value = C.exact(value, {"producer_id", "contract", "implementation_sha256"}, label)
    entry = trusted(
        snapshot,
        "producer",
        C.token(value["producer_id"], f"{label}.producer_id"),
        C.text(value["contract"], f"{label}.contract"),
        C.sha(value["implementation_sha256"], f"{label}.implementation_sha256"),
        label,
    )
    validator_contract = C.text(
        entry["validator_contract"], f"{label}.validator_contract"
    )
    validator_implementation = C.sha(
        entry["validator_implementation_sha256"],
        f"{label}.validator_implementation_sha256",
    )
    trusted(
        snapshot,
        "validator",
        entry["validator_id"],
        validator_contract,
        validator_implementation,
        f"{label} validator",
    )
    return {
        **value,
        "validator_id": entry["validator_id"],
        "validator_contract": validator_contract,
        "validator_implementation_sha256": validator_implementation,
    }


def issuer(
    value: Any, snapshot: dict[str, Any], label: str, capability: str
) -> dict[str, Any]:
    value = C.exact(value, {"issuer_id", "contract", "implementation_sha256"}, label)
    trusted(
        snapshot,
        "issuer",
        C.token(value["issuer_id"], f"{label}.issuer_id"),
        C.text(value["contract"], f"{label}.contract"),
        C.sha(value["implementation_sha256"], f"{label}.implementation_sha256"),
        label,
        capability,
    )
    return value


def load_validator(
    entry: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, tuple[Path, int, int, tuple[int, ...], bytes]],
    dict[Path, list[tuple[int, tuple[int, ...], str]]],
]:
    """Load descriptor-backed validator bytes from a prevalidated registry entry."""

    files: dict[str, tuple[Path, int, int, tuple[int, ...], bytes]] = {}
    parents: dict[Path, list[tuple[int, tuple[int, ...], str]]] = {}
    total = 0
    try:
        for item in entry["modules"]:
            name = item["name"]
            path = Path(item["path"])
            chain = parents.get(path.parent)
            if chain is None:
                chain = IO.open_chain(path.parent, "validator artifact parent")
                parents[path.parent] = chain
            parent = chain[-1][0]
            descriptor, identity = IO.open_at(
                parent,
                path.name,
                "registered validator artifact",
                private=False,
            )
            try:
                raw = IO.read_descriptor(
                    descriptor, identity, "registered validator artifact"
                )
            except BaseException:
                os.close(descriptor)
                raise
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                os.close(descriptor)
                raise C.EvidenceError("registered validator artifact drifted")
            total += len(raw)
            if total > MAX_VALIDATOR_ARTIFACT_BYTES:
                os.close(descriptor)
                raise C.EvidenceError(
                    "registered validator artifacts exceed the byte limit"
                )
            files[name] = (path, parent, descriptor, identity, raw)
        entrypoint = entry["entrypoint"]
        path, _, _, _, raw = files[entrypoint]
        return {"name": entrypoint, "path": path, "raw": raw}, files, parents
    except BaseException:
        close_artifacts(files, parents)
        raise


def recheck_artifacts(
    files: dict[str, tuple[Path, int, int, tuple[int, ...], bytes]],
    parents: dict[Path, list[tuple[int, tuple[int, ...], str]]],
) -> None:
    for _, _, descriptor, identity, raw in files.values():
        if (
            IO.read_descriptor(descriptor, identity, "registered validator artifact")
            != raw
        ):
            raise C.EvidenceError("registered validator artifact drifted")
    for path, parent, descriptor, identity, raw in files.values():
        IO.recheck_chain(parents[path.parent], "validator artifact parent")
        if (
            IO.descriptor_identity(
                os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            )
            != identity
        ):
            raise C.EvidenceError("registered validator artifact drifted")


def close_artifacts(
    files: dict[str, tuple[Path, int, int, tuple[int, ...], bytes]],
    parents: dict[Path, list[tuple[int, tuple[int, ...], str]]],
) -> None:
    errors = []
    for _, _, descriptor, _, _ in files.values():
        try:
            os.close(descriptor)
        except OSError as error:
            errors.append(error)
    for chain in parents.values():
        try:
            IO.close_chain(chain)
        except OSError as error:
            errors.append(error)
    if errors:
        raise errors[0]


def projection(value: Any) -> dict[str, Any]:
    """Canonicalize a registered validator's returned projection."""

    try:
        raw = C.canonical_bytes(value) + b"\n"
    except (RecursionError, TypeError, ValueError) as error:
        raise C.EvidenceError(
            "registered validator returned an unsafe projection"
        ) from error
    return IO.json_object(raw, "registered validator projection")
