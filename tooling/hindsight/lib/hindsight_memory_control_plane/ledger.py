"""Closed-schema, content-free append-only controller decision ledger."""

import fcntl
from datetime import datetime
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Mapping

from .canonical import DIGEST, canonical_bytes, strict_json_loads
from .endpoint_host import canonical_endpoint_host, is_bare_endpoint_host
from .file_evidence import open_trusted_parent
from .model import deep_freeze


LEDGER_KEYS = {
    "schema_version", "action_id", "correlation_id", "source_bank",
    "target_bank", "policy_digest", "artifact_digest", "decision",
    "reason_code", "timestamp", "reversible_record_id",
}
BANK_KEYS = {"profile_id", "bank_id", "endpoint"}
ENDPOINT_KEYS = {"profile_id", "scheme", "host", "port", "tenant"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
REASON = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z\Z")
DECISIONS = {"allow", "apply", "deny", "fail", "rollback", "skip"}
MAX_LEDGER_RECORD_BYTES = 64 * 1024
TAIL_VALIDATION_BYTES = 2 * MAX_LEDGER_RECORD_BYTES + 3


class LedgerError(ValueError):
    pass


def _identifier(value: Any, label: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise LedgerError(f"{label} must be a bounded identifier")


def _endpoint(value: Any, profile_id: str) -> None:
    if not isinstance(value, Mapping) or set(value) != ENDPOINT_KEYS:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise LedgerError(f"endpoint keys are closed (missing={sorted(ENDPOINT_KEYS - actual)}, unknown={sorted(actual - ENDPOINT_KEYS)})")
    if value["profile_id"] != profile_id:
        raise LedgerError("endpoint profile_id must match bank profile_id")
    _identifier(value["profile_id"], "endpoint profile_id")
    if not isinstance(value["scheme"], str) or value["scheme"] not in {"http", "https"}:
        raise LedgerError("endpoint scheme must be http or https")
    if not is_bare_endpoint_host(value["host"]):
        raise LedgerError("endpoint host must be a bare DNS name or IP literal")
    canonical_host = canonical_endpoint_host(value["host"])
    if not hmac.compare_digest(canonical_host, value["host"]):
        raise LedgerError("endpoint host must use its canonical representation")
    if value["scheme"] == "http" and canonical_host not in {"127.0.0.1", "::1"}:
        raise LedgerError("plain HTTP endpoint host must be literal loopback")
    if type(value["port"]) is not int or not 1 <= value["port"] <= 65535:
        raise LedgerError("endpoint port must be an integer from 1 to 65535")
    _identifier(value["tenant"], "endpoint tenant")


def _bank(value: Any, label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != BANK_KEYS:
        actual = set(value) if isinstance(value, Mapping) else set()
        raise LedgerError(f"bank reference keys are closed (missing={sorted(BANK_KEYS - actual)}, unknown={sorted(actual - BANK_KEYS)})")
    _identifier(value["profile_id"], f"{label} profile_id")
    _identifier(value["bank_id"], f"{label} bank_id")
    _endpoint(value["endpoint"], value["profile_id"])


def validate_record(record: Mapping[str, Any]) -> None:
    if not isinstance(record, Mapping):
        raise LedgerError("ledger record must be an object")
    unknown = set(record) - LEDGER_KEYS
    missing = LEDGER_KEYS - set(record)
    if unknown:
        raise LedgerError(f"ledger record has unknown keys: {sorted(unknown)}")
    if missing:
        raise LedgerError(f"ledger record is missing keys: {sorted(missing)}")
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise LedgerError("ledger schema_version must be integer 1")
    _identifier(record["action_id"], "action_id")
    _identifier(record["correlation_id"], "correlation_id")
    _bank(record["source_bank"], "source_bank")
    _bank(record["target_bank"], "target_bank")
    for key in ("policy_digest", "artifact_digest"):
        if not isinstance(record[key], str) or not DIGEST.fullmatch(record[key]):
            raise LedgerError(f"{key} must be a lowercase SHA-256 digest")
    if not isinstance(record["decision"], str) or record["decision"] not in DECISIONS:
        raise LedgerError("decision is not a supported enum")
    if not isinstance(record["reason_code"], str) or not REASON.fullmatch(record["reason_code"]):
        raise LedgerError("reason_code must be an uppercase enum")
    if not isinstance(record["timestamp"], str) or not TIMESTAMP.fullmatch(record["timestamp"]):
        raise LedgerError("timestamp must be a UTC RFC 3339 timestamp")
    try:
        datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
    except ValueError:
        raise LedgerError("timestamp must be a real UTC RFC 3339 instant") from None
    reversible = record["reversible_record_id"]
    if reversible is not None:
        _identifier(reversible, "reversible_record_id")


def _open_ledger_parent(path: Path) -> int:
    return open_trusted_parent(
        path,
        unavailable_message="symlink-safe directory access is unavailable",
        not_directory_message="ledger parent must be a directory",
        owner_message="ledger parent must be owned by the current user or root",
        writable_message=(
            "ledger parent must not be group or world writable"
        ),
        require_absolute=False,
    )


def append_record(path: str | Path, record: Mapping[str, Any]) -> None:
    _append_record(path, record, idempotent=False)


def append_record_once(path: str | Path, record: Mapping[str, Any]) -> bool:
    """Append once by action/correlation identity, rejecting conflicting reuse."""
    return _append_record(path, record, idempotent=True)


def _decode_existing_record(line: bytes) -> Mapping[str, Any]:
    if not 0 < len(line) <= MAX_LEDGER_RECORD_BYTES:
        raise LedgerError("existing ledger record is invalid")
    try:
        candidate = strict_json_loads(line)
        if not isinstance(candidate, dict):
            raise LedgerError("existing ledger record is invalid")
        validate_record(candidate)
        if canonical_bytes(candidate) != line:
            raise LedgerError("existing ledger record is invalid")
    except (UnicodeDecodeError, ValueError, LedgerError):
        raise LedgerError("existing ledger record is invalid") from None
    return candidate


def _validate_or_recover_tail(descriptor: int) -> int:
    file_size = os.fstat(descriptor).st_size
    if file_size == 0:
        return 0
    start = max(0, file_size - TAIL_VALIDATION_BYTES)
    tail = os.pread(descriptor, file_size - start, start)
    if start:
        boundary = tail.find(b"\n")
        if boundary < 0:
            raise LedgerError("existing ledger record is invalid")
        tail = tail[boundary + 1 :]
        start += boundary + 1
    if tail.endswith(b"\n"):
        complete = tail[:-1].split(b"\n")
        if not complete or not complete[-1]:
            raise LedgerError("existing ledger record is invalid")
        _decode_existing_record(complete[-1])
        return file_size
    pieces = tail.split(b"\n")
    if start == 0 and len(pieces) == 1:
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        return 0
    if len(pieces) < 2 or not pieces[-2]:
        raise LedgerError("existing ledger record is invalid")
    _decode_existing_record(pieces[-2])
    recovery_offset = start + len(tail) - len(pieces[-1])
    os.ftruncate(descriptor, recovery_offset)
    os.fsync(descriptor)
    return recovery_offset


def _validate_complete_ledger(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    buffered = bytearray()
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        buffered.extend(chunk)
        while True:
            newline = buffered.find(b"\n")
            if newline < 0:
                break
            _decode_existing_record(bytes(buffered[:newline]))
            del buffered[: newline + 1]
        if len(buffered) > MAX_LEDGER_RECORD_BYTES:
            raise LedgerError("existing ledger record is invalid")
    if buffered:
        raise LedgerError("existing ledger record is invalid")


def _index_file_name(action_id: str, correlation_id: str) -> str:
    identity = canonical_bytes([action_id, correlation_id])
    return f"{hashlib.sha256(identity).hexdigest()}.json"


def _index_key(identity: tuple[str, str]) -> str:
    return _index_file_name(*identity)[:-5]


def _auth_node_name(key: str) -> str:
    return f".auth-{key}"


def _tree_priority(key: str) -> str:
    return hashlib.sha256(b"index-priority\0" + bytes.fromhex(key)).hexdigest()


def _write_tree_node(index: int, node: Mapping[str, Any]) -> str:
    value = canonical_bytes(node)
    _write_index_file(index, _auth_node_name(node["key"]), value)
    return hashlib.sha256(value).hexdigest()


def _read_tree_node(
    index: int, key: str, expected_digest: str
) -> dict[str, Any]:
    value = _read_index_file(index, _auth_node_name(key))
    if value is None or not hmac.compare_digest(
        hashlib.sha256(value).hexdigest(), expected_digest
    ):
        raise LedgerError(
            "ledger identity index authentication path does not match root"
        )
    try:
        node = strict_json_loads(value)
    except (UnicodeDecodeError, ValueError):
        raise LedgerError("ledger identity index authentication node is invalid") from None
    if (
        not isinstance(node, dict)
        or set(node) != {
            "key", "entry_digest", "left_key", "left_digest",
            "right_key", "right_digest",
        }
        or node["key"] != key
        or not isinstance(node["key"], str)
        or DIGEST.fullmatch(node["key"]) is None
        or not isinstance(node["entry_digest"], str)
        or DIGEST.fullmatch(node["entry_digest"]) is None
        or any(
            (node[key_name] is None) != (node[digest_name] is None)
            for key_name, digest_name in (
                ("left_key", "left_digest"),
                ("right_key", "right_digest"),
            )
        )
        or any(
            node[name] is not None
            and (not isinstance(node[name], str) or DIGEST.fullmatch(node[name]) is None)
            for name in ("left_key", "left_digest", "right_key", "right_digest")
        )
        or (
            node["left_key"] is not None
            and (
                node["left_key"] >= key
                or _tree_priority(node["left_key"]) < _tree_priority(key)
            )
        )
        or (
            node["right_key"] is not None
            and (
                node["right_key"] <= key
                or _tree_priority(node["right_key"]) < _tree_priority(key)
            )
        )
        or canonical_bytes(node) != value
    ):
        raise LedgerError("ledger identity index authentication node is invalid")
    return node


def _tree_find(
    index: int, key: str, root_key: str | None, root_digest: str | None
) -> str | None:
    current_key = root_key
    current_digest = root_digest
    while current_key is not None:
        if current_digest is None:
            raise LedgerError("ledger identity index authentication root is invalid")
        node = _read_tree_node(index, current_key, current_digest)
        if key == current_key:
            return node["entry_digest"]
        side = "left" if key < current_key else "right"
        current_key = node[f"{side}_key"]
        current_digest = node[f"{side}_digest"]
    return None


def _tree_put(
    index: int,
    root_key: str | None,
    root_digest: str | None,
    key: str,
    entry_digest: str,
) -> tuple[str, str]:
    if (root_key is None) != (root_digest is None):
        raise LedgerError("ledger identity index authentication root is invalid")
    path: list[tuple[dict[str, Any], str]] = []
    current_key, current_digest = root_key, root_digest
    while current_key is not None:
        assert current_digest is not None
        node = _read_tree_node(index, current_key, current_digest)
        if key == current_key:
            node["entry_digest"] = entry_digest
            current_key = node["key"]
            current_digest = _write_tree_node(index, node)
            break
        side = "left" if key < current_key else "right"
        path.append((node, side))
        current_key = node[f"{side}_key"]
        current_digest = node[f"{side}_digest"]
    else:
        node = {
            "key": key,
            "entry_digest": entry_digest,
            "left_key": None,
            "left_digest": None,
            "right_key": None,
            "right_digest": None,
        }
        current_key = key
        current_digest = _write_tree_node(index, node)

    while path:
        parent, side = path.pop()
        parent[f"{side}_key"] = current_key
        parent[f"{side}_digest"] = current_digest
        if _tree_priority(current_key) >= _tree_priority(parent["key"]):
            current_key = parent["key"]
            current_digest = _write_tree_node(index, parent)
            continue
        child = _read_tree_node(index, current_key, current_digest)
        opposite = "right" if side == "left" else "left"
        parent[f"{side}_key"] = child[f"{opposite}_key"]
        parent[f"{side}_digest"] = child[f"{opposite}_digest"]
        parent_digest = _write_tree_node(index, parent)
        child[f"{opposite}_key"] = parent["key"]
        child[f"{opposite}_digest"] = parent_digest
        current_key = child["key"]
        current_digest = _write_tree_node(index, child)
    return current_key, current_digest


def _open_identity_index(directory: int, ledger_name: str) -> int:
    index_name = ".hindsight-ledger-index-" + hashlib.sha256(
        ledger_name.encode("utf-8")
    ).hexdigest()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        os.mkdir(index_name, 0o700, dir_fd=directory)
        os.fsync(directory)
    except FileExistsError:
        pass
    descriptor = os.open(index_name, flags, dir_fd=directory)
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise OSError("ledger identity index permissions are unsafe")
    return descriptor


def _read_index_file(index: int, name: str) -> bytes | None:
    try:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=index
        )
    except FileNotFoundError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > 2048
        ):
            raise OSError("ledger identity index entry is unsafe")
        value = os.read(descriptor, 2049)
        if len(value) != metadata.st_size:
            raise OSError("ledger identity index entry changed while reading")
        return value
    finally:
        os.close(descriptor)


def _write_index_file(index: int, name: str, value: bytes) -> None:
    temporary = f".{name}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=index,
        )
    except FileExistsError:
        os.unlink(temporary, dir_fd=index)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=index,
        )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(value)
            stream.flush()
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.rename(temporary, name, src_dir_fd=index, dst_dir_fd=index)
    os.fsync(index)


def _index_anchor_name(index: int) -> str:
    metadata = os.fstat(index)
    return (
        ".hindsight-ledger-root-anchor-"
        f"{metadata.st_dev}-{metadata.st_ino}.json"
    )


def _index_parent(index: int) -> int:
    descriptor = os.open(
        "..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=index
    )
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise OSError("ledger identity index anchor parent is unsafe")
    return descriptor


def _index_anchor(index: int) -> dict[str, Any] | None:
    parent = _index_parent(index)
    try:
        value = _read_index_file(parent, _index_anchor_name(index))
    finally:
        os.close(parent)
    if value is None:
        return None
    try:
        anchor = strict_json_loads(value)
    except (UnicodeDecodeError, ValueError):
        raise LedgerError(
            "ledger identity index authentication anchor is invalid"
        ) from None
    if (
        not isinstance(anchor, dict)
        or set(anchor)
        != {
            "schema_version", "indexed_bytes", "ledger_chain_digest",
            "index_root_key", "index_root_digest",
        }
        or anchor["schema_version"] != 1
        or type(anchor["indexed_bytes"]) is not int
        or anchor["indexed_bytes"] < 0
        or not isinstance(anchor["ledger_chain_digest"], str)
        or DIGEST.fullmatch(anchor["ledger_chain_digest"]) is None
        or (anchor["index_root_key"] is None)
        != (anchor["index_root_digest"] is None)
        or any(
            item is not None
            and (not isinstance(item, str) or DIGEST.fullmatch(item) is None)
            for item in (
                anchor["index_root_key"], anchor["index_root_digest"]
            )
        )
        or canonical_bytes(anchor) != value
    ):
        raise LedgerError(
            "ledger identity index authentication anchor is invalid"
        )
    return anchor


def _write_index_anchor(index: int, marker: Mapping[str, Any]) -> None:
    parent = _index_parent(index)
    try:
        _write_index_file(
            parent,
            _index_anchor_name(index),
            canonical_bytes(
                {
                    "schema_version": 1,
                    "indexed_bytes": marker["indexed_bytes"],
                    "ledger_chain_digest": marker["ledger_chain_digest"],
                    "index_root_key": marker["index_root_key"],
                    "index_root_digest": marker["index_root_digest"],
                }
            ),
        )
    finally:
        os.close(parent)


def _ledger_tail_digest(descriptor: int, indexed_bytes: int) -> str:
    length = min(indexed_bytes, TAIL_VALIDATION_BYTES)
    return hashlib.sha256(
        os.pread(descriptor, length, indexed_bytes - length)
    ).hexdigest()


def _ledger_prefix_digest(descriptor: int, indexed_bytes: int) -> str:
    checksum = hashlib.sha256()
    offset = 0
    while offset < indexed_bytes:
        chunk = os.pread(
            descriptor, min(1024 * 1024, indexed_bytes - offset), offset
        )
        if not chunk:
            raise LedgerError("ledger identity index could not read indexed prefix")
        checksum.update(chunk)
        offset += len(chunk)
    return checksum.hexdigest()


def _extend_ledger_chain(current: str, record_line: bytes) -> str:
    return hashlib.sha256(
        b"ledger-record\0"
        + bytes.fromhex(current)
        + hashlib.sha256(record_line).digest()
    ).hexdigest()


def _ledger_chain_digest(descriptor: int, indexed_bytes: int) -> str:
    current = hashlib.sha256(b"empty-ledger-chain").hexdigest()
    offset = 0
    buffered = bytearray()
    while offset < indexed_bytes:
        chunk = os.pread(
            descriptor, min(64 * 1024, indexed_bytes - offset), offset
        )
        if not chunk:
            raise LedgerError("ledger identity index could not read indexed prefix")
        offset += len(chunk)
        buffered.extend(chunk)
        while True:
            newline = buffered.find(b"\n")
            if newline < 0:
                break
            line = bytes(buffered[:newline])
            del buffered[: newline + 1]
            current = _extend_ledger_chain(current, line)
    if buffered:
        raise LedgerError("existing ledger record is invalid")
    return current


def _index_manifest_digest(index: int) -> str:
    entries: list[list[str]] = []
    for name in sorted(os.listdir(index)):
        if name == "complete" or name.startswith(".auth-"):
            continue
        if re.fullmatch(r"[0-9a-f]{64}\.json", name) is None:
            raise LedgerError("ledger identity index manifest is invalid")
        value = _read_index_file(index, name)
        if value is None:
            raise LedgerError("ledger identity index changed while authenticating")
        entries.append([name, hashlib.sha256(value).hexdigest()])
    return hashlib.sha256(canonical_bytes(entries)).hexdigest()


def _index_marker(index: int) -> dict[str, Any] | None:
    value = _read_index_file(index, "complete")
    if value is None:
        return None
    try:
        marker = strict_json_loads(value)
    except (UnicodeDecodeError, ValueError):
        raise LedgerError("ledger identity index marker is invalid") from None
    if not isinstance(marker, dict) or canonical_bytes(marker) != value:
        raise LedgerError("ledger identity index marker is invalid")
    schema_version = marker.get("schema_version")
    digest_key = (
        "ledger_chain_digest" if schema_version == 6
        else "prefix_digest" if schema_version in {4, 5}
        else "tail_digest"
    )
    expected = {
        "schema_version", "indexed_bytes", "ledger_dev", "ledger_ino",
        digest_key,
    }
    if schema_version in {3, 4, 5, 6}:
        expected.update({"ledger_mtime_ns", "ledger_ctime_ns"})
    if schema_version == 5:
        expected.add("index_manifest_digest")
    if schema_version == 6:
        expected.update({"index_root_key", "index_root_digest"})
    if (
        schema_version not in {3, 4, 5, 6}
        or set(marker) != expected
        or type(marker["indexed_bytes"]) is not int
        or marker["indexed_bytes"] < 0
        or not isinstance(marker["ledger_dev"], str)
        or not marker["ledger_dev"].isdigit()
        or not isinstance(marker["ledger_ino"], str)
        or not marker["ledger_ino"].isdigit()
        or not isinstance(marker[digest_key], str)
        or DIGEST.fullmatch(marker[digest_key]) is None
        or (
            schema_version in {3, 4, 5, 6}
            and (
                not isinstance(marker["ledger_mtime_ns"], str)
                or not marker["ledger_mtime_ns"].isdigit()
                or not isinstance(marker["ledger_ctime_ns"], str)
                or not marker["ledger_ctime_ns"].isdigit()
            )
        )
        or (
            schema_version == 5
            and (
                not isinstance(marker["index_manifest_digest"], str)
                or DIGEST.fullmatch(marker["index_manifest_digest"]) is None
            )
        )
        or (
            schema_version == 6
            and (
                (marker["index_root_key"] is None)
                != (marker["index_root_digest"] is None)
                or any(
                    value is not None
                    and (
                        not isinstance(value, str)
                        or DIGEST.fullmatch(value) is None
                    )
                    for value in (
                        marker["index_root_key"], marker["index_root_digest"]
                    )
                )
            )
        )
    ):
        raise LedgerError("ledger identity index marker is invalid")
    if schema_version == 6:
        anchor = _index_anchor(index)
        if anchor is None or any(
            anchor[key] != marker[key]
            for key in (
                "indexed_bytes", "ledger_chain_digest", "index_root_key",
                "index_root_digest",
            )
        ):
            raise LedgerError(
                "ledger identity index authentication anchor does not match marker"
            )
    return marker


def _marker_matches_ledger(
    marker: Mapping[str, Any], descriptor: int, file_size: int, *,
    allow_metadata_change: bool = False,
) -> bool:
    if marker["schema_version"] not in {4, 5, 6}:
        return False
    indexed_bytes = marker["indexed_bytes"]
    if indexed_bytes > file_size:
        return False
    metadata = os.fstat(descriptor)
    same_identity = (
        marker["ledger_dev"] == str(metadata.st_dev)
        and marker["ledger_ino"] == str(metadata.st_ino)
    )
    metadata_matches = (
        marker["ledger_mtime_ns"] == str(metadata.st_mtime_ns)
        and marker["ledger_ctime_ns"] == str(metadata.st_ctime_ns)
    )
    if same_identity and not allow_metadata_change and not metadata_matches:
        raise LedgerError("ledger changed outside the append path")
    if not same_identity:
        return False
    if metadata_matches:
        return True
    if marker["schema_version"] == 6:
        return marker["ledger_chain_digest"] == _ledger_chain_digest(
            descriptor, indexed_bytes
        )
    return marker["prefix_digest"] == _ledger_prefix_digest(descriptor, indexed_bytes)


def _write_index_marker(
    index: int,
    descriptor: int,
    indexed_bytes: int,
    *,
    ledger_chain_digest: str | None = None,
    index_root_key: str | None = None,
    index_root_digest: str | None = None,
) -> None:
    metadata = os.fstat(descriptor)
    if metadata.st_size != indexed_bytes:
        raise LedgerError("ledger changed while authenticating indexed prefix")
    if ledger_chain_digest is None:
        ledger_chain_digest = _ledger_chain_digest(descriptor, indexed_bytes)
    if (index_root_key is None) != (index_root_digest is None):
        raise LedgerError("ledger identity index authentication root is invalid")
    verified_metadata = os.fstat(descriptor)
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
    verified_identity = (
        verified_metadata.st_dev,
        verified_metadata.st_ino,
        verified_metadata.st_size,
        verified_metadata.st_mtime_ns,
        verified_metadata.st_ctime_ns,
    )
    if identity != verified_identity:
        raise LedgerError("ledger changed while authenticating indexed prefix")
    marker = {
        "schema_version": 6,
        "indexed_bytes": indexed_bytes,
        "ledger_dev": str(metadata.st_dev),
        "ledger_ino": str(metadata.st_ino),
        "ledger_mtime_ns": str(metadata.st_mtime_ns),
        "ledger_ctime_ns": str(metadata.st_ctime_ns),
        "ledger_chain_digest": ledger_chain_digest,
        "index_root_key": index_root_key,
        "index_root_digest": index_root_digest,
    }
    _write_index_anchor(index, marker)
    _write_index_file(index, "complete", canonical_bytes(marker))


def _index_entry(
    index: int,
    identity: tuple[str, str],
    *,
    expected_root_key: str | None = None,
    expected_root_digest: str | None = None,
) -> dict[str, Any] | None:
    value = _read_index_file(index, _index_file_name(*identity))
    authenticated_digest = _tree_find(
        index,
        _index_key(identity),
        expected_root_key,
        expected_root_digest,
    )
    actual_digest = hashlib.sha256(value).hexdigest() if value is not None else None
    if authenticated_digest != actual_digest:
        raise LedgerError(
            "ledger identity index authentication path does not match root"
        )
    if value is None:
        return None
    try:
        entry = strict_json_loads(value)
    except (UnicodeDecodeError, ValueError):
        raise LedgerError("ledger identity index entry is invalid") from None
    if (
        not isinstance(entry, dict)
        or set(entry)
        != {
            "schema_version",
            "action_id",
            "correlation_id",
            "record_digest",
            "conflicted",
        }
        or entry["schema_version"] != 1
        or (entry["action_id"], entry["correlation_id"]) != identity
        or not isinstance(entry["record_digest"], str)
        or not DIGEST.fullmatch(entry["record_digest"])
        or type(entry["conflicted"]) is not bool
        or canonical_bytes(entry) != value
    ):
        raise LedgerError("ledger identity index entry is invalid")
    return entry


def _put_index_entry(
    index: int,
    identity: tuple[str, str],
    record_digest: str,
    *,
    conflicted: bool = False,
    expected_root_key: str | None = None,
    expected_root_digest: str | None = None,
) -> tuple[str, str]:
    existing = _index_entry(
        index,
        identity,
        expected_root_key=expected_root_key,
        expected_root_digest=expected_root_digest,
    )
    if existing is not None:
        conflicted = (
            conflicted
            or existing["conflicted"]
            or existing["record_digest"] != record_digest
        )
        record_digest = existing["record_digest"]
    entry = {
        "schema_version": 1,
        "action_id": identity[0],
        "correlation_id": identity[1],
        "record_digest": record_digest,
        "conflicted": conflicted,
    }
    value = canonical_bytes(entry)
    _write_index_file(index, _index_file_name(*identity), value)
    return _tree_put(
        index,
        expected_root_key,
        expected_root_digest,
        _index_key(identity),
        hashlib.sha256(value).hexdigest(),
    )


def _initialize_identity_index(index: int, descriptor: int) -> None:
    for name in os.listdir(index):
        metadata = os.stat(name, dir_fd=index, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("ledger identity index contains an unsafe entry")
        os.unlink(name, dir_fd=index)
    os.lseek(descriptor, 0, os.SEEK_SET)
    buffered = bytearray()
    index_root_key: str | None = None
    index_root_digest: str | None = None
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        buffered.extend(chunk)
        while True:
            newline = buffered.find(b"\n")
            if newline < 0:
                break
            line = bytes(buffered[:newline])
            del buffered[: newline + 1]
            record = _decode_existing_record(line)
            index_root_key, index_root_digest = _put_index_entry(
                index,
                (record["action_id"], record["correlation_id"]),
                hashlib.sha256(line).hexdigest(),
                expected_root_key=index_root_key,
                expected_root_digest=index_root_digest,
            )
        if len(buffered) > MAX_LEDGER_RECORD_BYTES:
            raise LedgerError("existing ledger record is invalid")
    if buffered:
        raise LedgerError("existing ledger record is invalid")
    _write_index_marker(
        index,
        descriptor,
        os.fstat(descriptor).st_size,
        index_root_key=index_root_key,
        index_root_digest=index_root_digest,
    )


def _sync_identity_index(
    index: int, descriptor: int, file_size: int, *,
    allow_metadata_change: bool = False,
) -> None:
    marker = _index_marker(index)
    if marker is None:
        _initialize_identity_index(index, descriptor)
        return
    if marker["schema_version"] == 3:
        metadata = os.fstat(descriptor)
        indexed_bytes = marker["indexed_bytes"]
        if (
            indexed_bytes > file_size
            or marker["ledger_dev"] != str(metadata.st_dev)
            or marker["ledger_ino"] != str(metadata.st_ino)
            or marker["tail_digest"]
            != _ledger_tail_digest(descriptor, indexed_bytes)
            or (
                marker["ledger_mtime_ns"] != str(metadata.st_mtime_ns)
                or marker["ledger_ctime_ns"] != str(metadata.st_ctime_ns)
            )
        ):
            raise LedgerError("legacy ledger identity index marker does not match ledger")
        _initialize_identity_index(index, descriptor)
        return
    if marker["schema_version"] == 4:
        metadata = os.fstat(descriptor)
        if (
            marker["indexed_bytes"] > file_size
            or marker["ledger_dev"] != str(metadata.st_dev)
            or marker["ledger_ino"] != str(metadata.st_ino)
            or marker["prefix_digest"]
            != _ledger_prefix_digest(descriptor, marker["indexed_bytes"])
        ):
            raise LedgerError(
                "legacy ledger identity index marker does not match ledger"
            )
        _initialize_identity_index(index, descriptor)
        return
    if marker["schema_version"] == 5:
        metadata = os.fstat(descriptor)
        indexed_bytes = marker["indexed_bytes"]
        if (
            indexed_bytes > file_size
            or marker["ledger_dev"] != str(metadata.st_dev)
            or marker["ledger_ino"] != str(metadata.st_ino)
            or not hmac.compare_digest(
                marker["prefix_digest"],
                _ledger_prefix_digest(descriptor, indexed_bytes),
            )
        ):
            raise LedgerError(
                "legacy ledger identity index marker does not match ledger"
            )
        if not hmac.compare_digest(
            marker["index_manifest_digest"], _index_manifest_digest(index)
        ):
            raise LedgerError("ledger identity index manifest does not match index")
        _initialize_identity_index(index, descriptor)
        return
    if not _marker_matches_ledger(
        marker,
        descriptor,
        file_size,
        allow_metadata_change=allow_metadata_change,
    ):
        raise LedgerError("ledger identity index marker does not match ledger")
    indexed_bytes = marker["indexed_bytes"]
    if indexed_bytes == file_size:
        if allow_metadata_change:
            _write_index_marker(
                index,
                descriptor,
                file_size,
                ledger_chain_digest=marker["ledger_chain_digest"],
                index_root_key=marker["index_root_key"],
                index_root_digest=marker["index_root_digest"],
            )
        return
    try:
        os.unlink("complete", dir_fd=index)
    except FileNotFoundError:
        pass
    os.fsync(index)
    os.lseek(descriptor, indexed_bytes, os.SEEK_SET)
    remaining = file_size - indexed_bytes
    buffered = bytearray()
    ledger_chain = marker["ledger_chain_digest"]
    index_root_key = marker["index_root_key"]
    index_root_digest = marker["index_root_digest"]
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            raise LedgerError("ledger identity index could not reach the ledger tail")
        remaining -= len(chunk)
        buffered.extend(chunk)
        while True:
            newline = buffered.find(b"\n")
            if newline < 0:
                break
            line = bytes(buffered[:newline])
            del buffered[: newline + 1]
            record = _decode_existing_record(line)
            ledger_chain = _extend_ledger_chain(ledger_chain, line)
            index_root_key, index_root_digest = _put_index_entry(
                index,
                (record["action_id"], record["correlation_id"]),
                hashlib.sha256(line).hexdigest(),
                expected_root_key=index_root_key,
                expected_root_digest=index_root_digest,
            )
        if len(buffered) > MAX_LEDGER_RECORD_BYTES:
            raise LedgerError("existing ledger record is invalid")
    if buffered:
        raise LedgerError("existing ledger record is invalid")
    _write_index_marker(
        index,
        descriptor,
        file_size,
        ledger_chain_digest=ledger_chain,
        index_root_key=index_root_key,
        index_root_digest=index_root_digest,
    )


def _append_record(
    path: str | Path, record: Mapping[str, Any], *, idempotent: bool
) -> bool:
    try:
        body = canonical_bytes(record)
        decoded = strict_json_loads(body)
    except (UnicodeDecodeError, ValueError):
        raise LedgerError("ledger record is not canonical JSON") from None
    if not isinstance(decoded, dict):
        raise LedgerError("ledger record must be an object")
    snapshot = deep_freeze(decoded)
    validate_record(snapshot)
    target = Path(path)
    if target.name in {"", ".", ".."}:
        raise OSError("ledger destination name is invalid")
    directory = _open_ledger_parent(target.parent)
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        for attempt in range(3):
            try:
                descriptor = os.open(
                    target.name, flags, 0o600, dir_fd=directory
                )
                break
            except FileNotFoundError:
                if attempt == 2:
                    raise
                time.sleep(0.001)
    except Exception:
        os.close(directory)
        raise
    original_size: int | None = None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError("ledger destination must be a regular file")
        if metadata.st_uid != os.geteuid():
            raise OSError("ledger destination must be owned by the current user")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise OSError("ledger destination permissions are unsafe")
        deadline = time.monotonic() + 2.0
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("ledger lock acquisition timed out")
                time.sleep(0.01)
        _require_locked_ledger_path(directory, target.name, descriptor)
        observed_size = os.fstat(descriptor).st_size
        index = _open_identity_index(directory, target.name)
        try:
            initial_marker = _index_marker(index)
        finally:
            os.close(index)
        if (
            initial_marker is not None
            and initial_marker["schema_version"] in {4, 5, 6}
            and not _marker_matches_ledger(
                initial_marker,
                descriptor,
                observed_size,
                allow_metadata_change=True,
            )
        ):
            raise LedgerError("ledger identity index marker does not match ledger")
        if initial_marker is None and observed_size:
            _validate_complete_ledger(descriptor)
            file_size = observed_size
        else:
            file_size = _validate_or_recover_tail(descriptor)
        identity = (snapshot["action_id"], snapshot["correlation_id"])
        record_digest = hashlib.sha256(body).hexdigest()
        index = _open_identity_index(directory, target.name)
        try:
            marker = _index_marker(index)
            metadata_change_is_authenticated = marker is not None and (
                marker["schema_version"] == 6
                or observed_size > marker["indexed_bytes"]
                or file_size > marker["indexed_bytes"]
            )
            if idempotent:
                _sync_identity_index(
                    index,
                    descriptor,
                    file_size,
                    allow_metadata_change=metadata_change_is_authenticated,
                )
                current_marker = _index_marker(index)
                if current_marker is None or current_marker["schema_version"] != 6:
                    raise LedgerError("ledger identity index marker is unavailable")
                existing = _index_entry(
                    index,
                    identity,
                    expected_root_key=current_marker["index_root_key"],
                    expected_root_digest=current_marker["index_root_digest"],
                )
                if existing is not None:
                    if (
                        not existing["conflicted"]
                        and existing["record_digest"] == record_digest
                    ):
                        return False
                    raise LedgerError(
                        "ledger idempotency identity conflicts with an existing record"
                    )
            elif marker is not None or file_size:
                _sync_identity_index(
                    index,
                    descriptor,
                    file_size,
                    allow_metadata_change=metadata_change_is_authenticated,
                )
            elif file_size == 0:
                _write_index_marker(index, descriptor, 0)
        finally:
            os.close(index)
        original_size = os.lseek(descriptor, 0, os.SEEK_END)
        index = _open_identity_index(directory, target.name)
        try:
            trusted_append_marker = _index_marker(index)
            if (
                trusted_append_marker is None
                or trusted_append_marker["schema_version"] != 6
                or trusted_append_marker["indexed_bytes"] != original_size
                or not _marker_matches_ledger(
                    trusted_append_marker,
                    descriptor,
                    original_size,
                )
            ):
                raise LedgerError(
                    "ledger identity index prefix is not authenticated before append"
                )
        finally:
            os.close(index)
        body += b"\n"
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written <= 0 or written > len(body) - offset:
                raise OSError("short ledger write")
            offset += written
        os.fsync(descriptor)
        os.fsync(directory)
        index = _open_identity_index(directory, target.name)
        try:
            current_marker = _index_marker(index)
            if current_marker != trusted_append_marker:
                raise LedgerError(
                    "ledger identity index changed during append"
                )
            try:
                os.unlink("complete", dir_fd=index)
            except FileNotFoundError:
                pass
            os.fsync(index)
            line = body[:-1]
            index_root_key, index_root_digest = _put_index_entry(
                index,
                identity,
                hashlib.sha256(line).hexdigest(),
                expected_root_key=trusted_append_marker["index_root_key"],
                expected_root_digest=trusted_append_marker[
                    "index_root_digest"
                ],
            )
            _write_index_marker(
                index,
                descriptor,
                original_size + len(body),
                ledger_chain_digest=_extend_ledger_chain(
                    trusted_append_marker["ledger_chain_digest"], line
                ),
                index_root_key=index_root_key,
                index_root_digest=index_root_digest,
            )
        finally:
            os.close(index)
        _require_locked_ledger_path(directory, target.name, descriptor)
        return True
    except Exception as error:
        if original_size is not None:
            try:
                os.ftruncate(descriptor, original_size)
                os.fsync(descriptor)
                index = _open_identity_index(directory, target.name)
                try:
                    try:
                        os.unlink("complete", dir_fd=index)
                    except FileNotFoundError:
                        pass
                    os.fsync(index)
                finally:
                    os.close(index)
            except OSError as rollback_error:
                raise OSError("ledger append rollback failed") from rollback_error
        raise
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)
        os.close(directory)


def _require_locked_ledger_path(
    directory: int, name: str, descriptor: int
) -> None:
    try:
        locked = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError:
        raise LedgerError("ledger destination changed while locked") from None
    if (
        not stat.S_ISREG(current.st_mode)
        or (current.st_dev, current.st_ino) != (locked.st_dev, locked.st_ino)
    ):
        raise LedgerError("ledger destination changed while locked")
