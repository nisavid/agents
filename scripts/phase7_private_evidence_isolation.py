#!/usr/bin/env python3
"""Descriptor-safe capability sealing and portable no-replace publication."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import re
import stat
from pathlib import Path
from typing import Any


class IsolationError(RuntimeError):
    pass


PUBLIC_CANDIDATE_PATTERN = re.compile(r"[0-9a-f]{64}")


def _candidate_identity(value: Any) -> bool:
    return (
        isinstance(value, str) and PUBLIC_CANDIDATE_PATTERN.fullmatch(value) is not None
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IsolationError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _snapshot(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _same_identity(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        _snapshot(before) == _snapshot(after)
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _open_directory(path: Path) -> int:
    _require(path.is_absolute(), "capability root must be absolute")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            before = os.lstat(component, dir_fd=descriptor)
            _require(
                not stat.S_ISLNK(before.st_mode),
                "capability root has a symlinked ancestor",
            )
            _require(
                stat.S_ISDIR(before.st_mode),
                "capability root ancestor is not a directory",
            )
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            after = os.fstat(child)
            if not _same_identity(before, after):
                os.close(child)
                raise IsolationError("capability root changed while it was opened")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_regular_file(
    parent_fd: int,
    name: str,
    retained_descriptors: list[tuple[int, os.stat_result, bytes]],
) -> tuple[os.stat_result, bytes]:
    before = os.lstat(name, dir_fd=parent_fd)
    _require(
        not stat.S_ISLNK(before.st_mode), "capability tree contains a leaf symlink"
    )
    _require(
        stat.S_ISREG(before.st_mode), "capability tree contains an unsupported entry"
    )
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    retained = False
    try:
        opened = os.fstat(descriptor)
        _require(
            _same_identity(before, opened),
            "capability file changed while it was opened",
        )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        retained_descriptors.append((descriptor, opened, b"".join(chunks)))
        retained = True
    finally:
        if not retained:
            os.close(descriptor)
    _require(_same_identity(opened, after), "capability file changed while it was read")
    return after, b"".join(chunks)


def _revalidate_sealed_files(
    retained_descriptors: list[tuple[int, os.stat_result, bytes]],
) -> None:
    """Prove every retained descriptor still resolves to the bytes it sealed."""

    try:
        for descriptor, expected_metadata, expected_content in retained_descriptors:
            os.lseek(descriptor, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            observed_metadata = os.fstat(descriptor)
            _require(
                _same_identity(expected_metadata, observed_metadata)
                and b"".join(chunks) == expected_content,
                "capability file changed after it was sealed",
            )
    finally:
        for descriptor, _metadata, _content in retained_descriptors:
            os.close(descriptor)


def _seal_directory(
    descriptor: int,
    *,
    relative: str,
    entries: list[dict[str, Any]],
    retained_descriptors: list[tuple[int, os.stat_result, bytes]],
) -> None:
    before = os.fstat(descriptor)
    _require(stat.S_ISDIR(before.st_mode), "capability tree contains a non-directory")
    entries.append(
        {
            "path": relative,
            "type": "directory",
            "mode": f"{stat.S_IMODE(before.st_mode):04o}",
        }
    )
    names = sorted(os.listdir(descriptor))
    for name in names:
        _require(
            name not in {"", ".", ".."} and "/" not in name,
            "capability tree has an unsafe path",
        )
        child_relative = name if relative == "." else f"{relative}/{name}"
        metadata = os.lstat(name, dir_fd=descriptor)
        _require(
            not stat.S_ISLNK(metadata.st_mode),
            "capability tree contains a leaf symlink",
        )
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                opened = os.fstat(child)
                _require(
                    _same_identity(metadata, opened),
                    "capability directory changed while it was opened",
                )
                _seal_directory(
                    child,
                    relative=child_relative,
                    entries=entries,
                    retained_descriptors=retained_descriptors,
                )
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            observed, content = _read_regular_file(
                descriptor,
                name,
                retained_descriptors,
            )
            entries.append(
                {
                    "path": child_relative,
                    "type": "regular",
                    "mode": f"{stat.S_IMODE(observed.st_mode):04o}",
                    "size": len(content),
                    "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                }
            )
        else:
            raise IsolationError("capability tree contains an unsupported entry")
    after = os.fstat(descriptor)
    _require(
        _same_identity(before, after),
        "capability directory changed while it was sealed",
    )


def seal_capability_tree(root: Path) -> dict[str, Any]:
    """Seal every visible directory and regular file below one trusted root."""

    _require(root.is_absolute(), "capability root must be absolute")
    root = Path(os.path.abspath(os.fspath(root)))
    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise IsolationError("capability root is unavailable") from error
    _require(not stat.S_ISLNK(root_metadata.st_mode), "capability root is a symlink")
    _require(stat.S_ISDIR(root_metadata.st_mode), "capability root is not a directory")
    descriptor = _open_directory(root)
    try:
        entries: list[dict[str, Any]] = []
        retained_descriptors: list[tuple[int, os.stat_result, bytes]] = []
        try:
            _seal_directory(
                descriptor,
                relative=".",
                entries=entries,
                retained_descriptors=retained_descriptors,
            )
            _revalidate_sealed_files(retained_descriptors)
        except BaseException:
            for file_descriptor, _metadata, _content in retained_descriptors:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            raise
    finally:
        os.close(descriptor)
    _require(len(entries) > 1, "empty capability roots are not permitted")
    sealed_tree = {
        "entry_count": len(entries),
        "entries": entries,
    }
    seal = {
        "root": root.name,
        "physical_root": os.fspath(root),
        "entry_count": len(entries),
        "entries": entries,
    }
    seal["tree_sha256"] = (
        "sha256:" + hashlib.sha256(_canonical_bytes(sealed_tree)).hexdigest()
    )
    return seal


def validate_capability_tree(root: Path, expected: dict[str, Any]) -> None:
    observed = seal_capability_tree(root)
    _require(
        isinstance(expected, dict)
        and expected.get("physical_root") == observed["physical_root"]
        and expected.get("entry_count") == observed["entry_count"]
        and expected.get("entries") == observed["entries"]
        and expected.get("tree_sha256") == observed["tree_sha256"],
        "capability tree changed after sealing",
    )


def load_backend_contracts(path: Path) -> list[dict[str, Any]]:
    """Load separately reviewed backend pins; never derive them from the executable."""

    content = path.read_bytes()
    try:
        document = json.loads(content)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IsolationError("backend contract fixture is unreadable") from error
    _require(
        isinstance(document, dict)
        and set(document) == {"schema_version", "contract", "targets"}
        and document["schema_version"] == 2
        and document["contract"] == "phase7-private-isolation-backends-v2"
        and isinstance(document["targets"], list),
        "backend contract fixture drift",
    )
    expected_targets = (
        ("macos-seatbelt", "sandbox-exec", "direct-required"),
        ("linux-bubblewrap", "bwrap", "external-release-required"),
        ("wsl2-bubblewrap", "bwrap", "external-release-required"),
    )
    contracts: list[dict[str, Any]] = []
    for (target, binary, execution), contract in zip(
        expected_targets, document["targets"]
    ):
        fields = {
            "target",
            "binary",
            "version",
            "capabilities",
            "execution",
            "release_evidence_required",
        }
        if target == "macos-seatbelt":
            fields.add("sha256")
        _require(
            isinstance(contract, dict)
            and set(contract) == fields
            and contract["target"] == target
            and contract["binary"] == binary
            and isinstance(contract["version"], str)
            and contract["version"].strip() == contract["version"]
            and contract["version"]
            and isinstance(contract["capabilities"], list)
            and contract["capabilities"]
            and len(set(contract["capabilities"])) == len(contract["capabilities"])
            and contract["execution"] == execution
            and contract["release_evidence_required"] is (target != "macos-seatbelt"),
            "backend target contract drift",
        )
        if target == "macos-seatbelt":
            _require(
                contract["sha256"]
                == "sha256:8290e4be7387a0df83cd1559e86afd880464f269450573d012795761fe298f16",
                "backend target contract drift",
            )
        contracts.append(contract)
    _require(len(contracts) == len(expected_targets), "backend target inventory drift")
    return contracts


def validate_public_release_backend_evidence(
    content: bytes,
    *,
    expected_sha256: str,
) -> list[dict[str, Any]]:
    """Validate externally frozen direct-execution proof for every target."""

    _require(
        isinstance(expected_sha256, str)
        and expected_sha256 == "sha256:" + hashlib.sha256(content).hexdigest(),
        "backend release evidence identity mismatch",
    )
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IsolationError("backend release evidence is unreadable") from error
    _require(
        isinstance(document, dict)
        and set(document)
        == {"schema_version", "contract", "public_candidate_identity", "targets"}
        and document["schema_version"] == 2
        and document["contract"] == "phase7-public-backend-release-evidence-v2"
        and _candidate_identity(document["public_candidate_identity"])
        and isinstance(document["targets"], list),
        "backend release evidence drift",
    )
    expected_targets = (
        ("macos-seatbelt", "sandbox-exec"),
        ("linux-bubblewrap", "bwrap"),
        ("wsl2-bubblewrap", "bwrap"),
    )
    fields = {
        "schema_version",
        "contract",
        "target",
        "binary",
        "version",
        "binary_sha256",
        "version_sha256",
        "policy_sha256",
        "capability_manifest_sha256",
        "public_candidate_identity",
        "host_identity_sha256",
        "kernel_identity_sha256",
        "conformance_sha256",
        "terminal_direct_proof_sha256",
    }
    proofs: list[dict[str, Any]] = []
    for (target, binary), record in zip(expected_targets, document["targets"]):
        _require(
            isinstance(record, dict)
            and set(record) == fields
            and record["schema_version"] == 2
            and record["contract"] == "phase7-public-terminal-direct-proof-v2"
            and record["target"] == target
            and record["binary"] == binary
            and isinstance(record["version"], str)
            and record["version"].strip() == record["version"]
            and record["version"]
            and all(
                isinstance(record[field], str)
                and len(record[field]) == 71
                and record[field].startswith("sha256:")
                and all(
                    character in "0123456789abcdef" for character in record[field][7:]
                )
                for field in (
                    "binary_sha256",
                    "version_sha256",
                    "policy_sha256",
                    "capability_manifest_sha256",
                    "host_identity_sha256",
                    "kernel_identity_sha256",
                    "conformance_sha256",
                    "terminal_direct_proof_sha256",
                )
            )
            and all(
                len(set(record[field][7:])) > 1
                for field in (
                    "binary_sha256",
                    "version_sha256",
                    "host_identity_sha256",
                    "kernel_identity_sha256",
                    "conformance_sha256",
                    "terminal_direct_proof_sha256",
                )
            )
            and (
                record["public_candidate_identity"]
                == document["public_candidate_identity"]
                and (
                    target != "macos-seatbelt"
                    or record["binary_sha256"]
                    == "sha256:8290e4be7387a0df83cd1559e86afd880464f269450573d012795761fe298f16"
                )
            ),
            "backend release evidence drift",
        )
        terminal_payload = {
            field: value
            for field, value in record.items()
            if field != "terminal_direct_proof_sha256"
        }
        _require(
            record["terminal_direct_proof_sha256"]
            == "sha256:"
            + hashlib.sha256(_canonical_bytes(terminal_payload)).hexdigest(),
            "backend terminal proof identity mismatch",
        )
        proofs.append(record)
    _require(
        len(document["targets"]) == len(expected_targets),
        "backend release evidence inventory drift",
    )
    _require(
        len({proof["target"] for proof in proofs}) == len(proofs)
        and len({proof["host_identity_sha256"] for proof in proofs}) == len(proofs)
        and len({proof["kernel_identity_sha256"] for proof in proofs}) == len(proofs)
        and len({proof["terminal_direct_proof_sha256"] for proof in proofs})
        == len(proofs),
        "backend release evidence target substitution",
    )
    return proofs


def validate_runtime_backend_release_evidence(
    runtime: dict[str, Any],
    proofs: list[dict[str, Any]],
    *,
    public_candidate_identity: str,
) -> None:
    """Bind one runtime receipt to its target-specific terminal proof."""

    _require(isinstance(runtime, dict), "runtime backend release evidence drift")
    _require(
        _candidate_identity(public_candidate_identity),
        "runtime backend public candidate identity is malformed",
    )
    backend = runtime.get("backend")
    _require(
        isinstance(backend, dict) and isinstance(proofs, list),
        "runtime backend release evidence drift",
    )
    matching = [
        proof
        for proof in proofs
        if isinstance(proof, dict) and proof.get("target") == backend.get("target")
    ]
    _require(
        len(matching) == 1,
        "runtime backend target has no independent terminal proof",
    )
    proof = matching[0]
    _require(
        backend
        == {
            "target": proof["target"],
            "binary": proof["binary"],
            "version": proof["version"],
            "sha256": proof["binary_sha256"],
            "version_sha256": proof["version_sha256"],
        }
        and runtime.get("policy_sha256") == proof["policy_sha256"]
        and runtime.get("capability_manifest_sha256")
        == proof["capability_manifest_sha256"]
        and runtime.get("host_identity_sha256") == proof["host_identity_sha256"]
        and runtime.get("kernel_identity_sha256") == proof["kernel_identity_sha256"]
        and runtime.get("conformance_sha256") == proof["conformance_sha256"]
        and proof["public_candidate_identity"] == public_candidate_identity,
        "runtime backend does not match its target-specific terminal proof",
    )


def renameat2_syscall_number(machine: str | None = None) -> int:
    """Return the Linux ``renameat2`` number for an explicitly supported ABI."""

    normalized = (machine or platform.machine()).lower()
    numbers = {
        "x86_64": 316,
        "amd64": 316,
        "aarch64": 276,
        "arm64": 276,
        "riscv64": 276,
    }
    try:
        return numbers[normalized]
    except KeyError as error:
        raise IsolationError(f"unsupported Linux architecture: {normalized}") from error


def publish_no_replace(
    source: Path, destination: Path, *, machine: str | None = None
) -> None:
    """Atomically publish one file, resolving ABI support before any mutation."""

    number = renameat2_syscall_number(machine)
    _require(
        os.name == "posix" and platform.system() == "Linux", "renameat2 is unavailable"
    )
    library = ctypes.CDLL(None, use_errno=True)
    result = library.syscall(
        number,
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)
