#!/usr/bin/env python3
"""Stateless validation of one task-evidence bundle and one trust snapshot."""

from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path
from typing import Any

_LAUNCH_CONTEXT = globals().get("_TASK_WITNESS_LAUNCH_CONTEXT")
if _LAUNCH_CONTEXT is None:
    raise RuntimeError(
        "Task Witness runtime must be launched by task_witness_launch.py"
    )


_CANONICAL = globals().get("_CANONICAL")
_BUNDLE_IO = globals().get("_BUNDLE_IO")
_TRUST = globals().get("_TRUST")
if _CANONICAL is None or _BUNDLE_IO is None or _TRUST is None:
    raise RuntimeError("Task Witness launch context is incomplete")

TRUST_CONTRACT = _CANONICAL.TRUST_CONTRACT
SCHEMA_VERSION = _CANONICAL.SCHEMA_VERSION
DISPATCH_PROJECTION_CONTRACT = _CANONICAL.DISPATCH_PROJECTION_CONTRACT
CANONICAL_PROJECTION_CONTRACT = _CANONICAL.CANONICAL_PROJECTION_CONTRACT
VALIDATOR_ARTIFACT_MANIFEST_CONTRACT = _CANONICAL.VALIDATOR_ARTIFACT_MANIFEST_CONTRACT
MAX_JSON_BYTES = _CANONICAL.MAX_JSON_BYTES
MAX_JSON_DEPTH = _CANONICAL.MAX_JSON_DEPTH
MAX_FILE_BYTES = _BUNDLE_IO.MAX_FILE_BYTES
MAX_BUNDLE_FILES = _BUNDLE_IO.MAX_BUNDLE_FILES
MAX_BUNDLE_BYTES = _BUNDLE_IO.MAX_BUNDLE_BYTES
MAX_VALIDATOR_MODULES = _TRUST.MAX_VALIDATOR_MODULES
EvidenceError = _CANONICAL.EvidenceError
canonical_bytes = _CANONICAL.canonical_bytes
digest = _CANONICAL.digest
pairs = _CANONICAL.pairs
constant = _CANONICAL.constant
exact = _CANONICAL.exact
text = _CANONICAL.text
token = _CANONICAL.token
sha = _CANONICAL.sha
token_list = _CANONICAL.token_list
text_list = _CANONICAL.text_list
identity = _CANONICAL.identity
document = _CANONICAL.document
validator_implementation_identity = _CANONICAL.validator_implementation_identity
RUNTIME_ARTIFACT_MANIFEST_CONTRACT = "task-witness-runtime-artifact-manifest-v2"
RUNTIME_CONTRACT = _LAUNCH_CONTEXT.runtime_contract


def _runtime_artifact_manifest() -> dict[str, Any]:
    payloads = []
    for role, relative_path, length, content_sha256 in _LAUNCH_CONTEXT.payload_specs:
        raw = _LAUNCH_CONTEXT.payloads[relative_path]
        actual = {
            "role": role,
            "relative_path": relative_path,
            "length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        expected = {
            "role": role,
            "relative_path": relative_path,
            "length": length,
            "sha256": content_sha256,
        }
        if actual != expected:
            raise RuntimeError("Task Witness runtime payload identity disagreement")
        payloads.append(actual)
    return {
        "contract": RUNTIME_ARTIFACT_MANIFEST_CONTRACT,
        "runtime_contract": RUNTIME_CONTRACT,
        "entrypoint_role": "entrypoint",
        "payloads": payloads,
    }


RUNTIME_ARTIFACT_MANIFEST = _runtime_artifact_manifest()
RUNTIME_IMPLEMENTATION_SHA256 = digest(RUNTIME_ARTIFACT_MANIFEST)
if RUNTIME_IMPLEMENTATION_SHA256 != _LAUNCH_CONTEXT.runtime_implementation_sha256:
    raise RuntimeError("Task Witness runtime implementation identity disagreement")

absolute = _BUNDLE_IO.absolute
open_chain = _BUNDLE_IO.open_chain
recheck_chain = _BUNDLE_IO.recheck_chain
close_chain = _BUNDLE_IO.close_chain
open_at = _BUNDLE_IO.open_at
read_descriptor = _BUNDLE_IO.read_descriptor
read_path = _BUNDLE_IO.read_path
json_object = _BUNDLE_IO.json_object
BundleView = _BUNDLE_IO.BundleView
open_bundle = _BUNDLE_IO.open_bundle
recheck_bundle = _BUNDLE_IO.recheck_bundle
close_bundle = _BUNDLE_IO.close_bundle

open_trust = _TRUST.open_trust
recheck_trust = _TRUST.recheck_trust
close_trust = _TRUST.close_trust
trusted = _TRUST.trusted
producer = _TRUST.producer
issuer = _TRUST.issuer
projection = _TRUST.projection
_recheck_artifacts = _TRUST.recheck_artifacts


def _recheck_runtime() -> None:
    try:
        _LAUNCH_CONTEXT.recheck()
    except KeyboardInterrupt:
        raise
    except BaseException as error:
        raise EvidenceError("Task Witness runtime artifact changed") from error


def _witness_module() -> Any:
    """Return this runtime when loaded normally or a compatible in-memory view."""

    module = sys.modules.get(__name__)
    if module is not None:
        return module
    return types.SimpleNamespace(**globals())


def _execute_validator(
    validator: dict[str, Any],
    view: BundleView,
    trust_snapshot: dict[str, Any],
) -> dict[str, Any]:
    loaded, files, parents = _TRUST.load_validator(validator)
    error = None
    try:
        modules: dict[str, Any] = {}
        for item in validator["modules"]:
            name = item["name"]
            if name == loaded["name"]:
                continue
            path, _, _, _, raw = files[name]
            namespace = {
                "__file__": str(path),
                "__name__": f"registered_task_validator_{name}",
                "_TASK_WITNESS": _witness_module(),
                "_VERIFIED_MODULES": modules,
            }
            exec(  # noqa: S102
                compile(raw, str(path), "exec", dont_inherit=True, optimize=0),
                namespace,
            )
            modules[name] = types.SimpleNamespace(**namespace)
        namespace = {
            "__file__": str(loaded["path"]),
            "__name__": "registered_task_validator",
            "_TASK_WITNESS": _witness_module(),
            "_VERIFIED_MODULES": modules,
        }
        exec(  # noqa: S102
            compile(
                loaded["raw"],
                str(loaded["path"]),
                "exec",
                dont_inherit=True,
                optimize=0,
            ),
            namespace,
        )
        if namespace.get("BUNDLE_CONTRACT") != validator["contract"]:
            raise EvidenceError("registered validator contract mismatch")
        recheck_bundle(view, "task-evidence bundle")
        recheck_trust(trust_snapshot)
        _recheck_runtime()
        result = _TRUST.projection(
            namespace["_validate_bundle"](view, trust_snapshot=trust_snapshot)
        )
        _recheck_artifacts(files, parents)
        return result
    except BaseException as caught:
        error = caught
        raise
    finally:
        cleanup = None
        try:
            _TRUST.close_artifacts(files, parents)
        except BaseException as caught:
            cleanup = caught
        if error is None and cleanup is not None:
            raise cleanup


def _bundle_identity(view: BundleView) -> str:
    """Hash the retained complete bundle inventory, not only its manifest."""

    return digest(
        {
            "contract": "task-witness-bundle-inventory-v1",
            "files": [
                {
                    "name": name,
                    "length": len(view.files[name][2]),
                    "sha256": hashlib.sha256(view.files[name][2]).hexdigest(),
                }
                for name in sorted(view.names)
            ],
        }
    )


def invoke_registered_validator(
    bundle: Path, trust_snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Execute the operator-trusted validator selected by the retained trust."""

    view = open_bundle(bundle, "task-evidence bundle")
    error = None
    try:
        manifest, _ = view.read_json("manifest.json", "task-evidence manifest")
        selected_producer = producer(
            manifest.get("producer"),
            trust_snapshot,
            "task-evidence producer",
        )
        validator = trusted(
            trust_snapshot,
            "validator",
            selected_producer["validator_id"],
            selected_producer["validator_contract"],
            selected_producer["validator_implementation_sha256"],
            "task-evidence selected validator",
        )
        recheck_bundle(view, "task-evidence bundle")
        recheck_trust(trust_snapshot)
        _recheck_runtime()
        result = _execute_validator(validator, view, trust_snapshot)
        recheck_bundle(view, "task-evidence bundle")
        recheck_trust(trust_snapshot)
        _recheck_runtime()
        return {
            "contract": CANONICAL_PROJECTION_CONTRACT,
            "bundle_sha256": _bundle_identity(view),
            "producer": selected_producer,
            "validator": {
                "validator_id": validator["validator_id"],
                "contract": validator["contract"],
                "implementation_sha256": validator["implementation_sha256"],
            },
            "projection": result,
        }
    except BaseException as caught:
        error = caught
        raise
    finally:
        cleanup = None
        try:
            close_bundle(view)
        except BaseException as caught:
            cleanup = caught
        if error is None and cleanup is not None:
            raise cleanup


def validate_bundle(
    bundle: Path,
    *,
    trust_context_path: Path,
    new_publication: bool = True,
    _trust_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one bundle using either a fresh or caller-retained trust snapshot."""

    snapshot = _trust_snapshot
    owns_snapshot = snapshot is None
    error = None
    try:
        if snapshot is None:
            snapshot = open_trust(trust_context_path, new_publication)
        elif snapshot["new_publication"] != new_publication:
            raise EvidenceError("trust snapshot publication mode mismatch")
        result = invoke_registered_validator(bundle, snapshot)
        recheck_trust(snapshot)
        _recheck_runtime()
        return {
            **result,
            "trust_context_sha256": hashlib.sha256(snapshot["raw"]).hexdigest(),
            "historical": not new_publication,
        }
    except EvidenceError as caught:
        error = caught
        raise
    except KeyboardInterrupt as caught:
        error = caught
        raise
    except BaseException as caught:
        error = caught
        raise EvidenceError("task evidence is malformed") from caught
    finally:
        cleanup = None
        try:
            if owns_snapshot and snapshot is not None:
                close_trust(snapshot)
        except BaseException as caught:
            cleanup = caught
        try:
            _recheck_runtime()
        except BaseException as caught:
            if cleanup is None:
                cleanup = caught
        if error is None and cleanup is not None:
            raise cleanup
