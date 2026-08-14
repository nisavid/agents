"""Descriptor-bound input handling for Task Witness bundles and trust files."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

if globals().get("_TASK_WITNESS_LAUNCH_CONTEXT") is None:
    raise RuntimeError(
        "Task Witness runtime must be launched by task_witness_launch.py"
    )


C = globals().get("_CANONICAL")
if C is None:
    raise RuntimeError("Task Witness canonical primitives were not injected")
MACOS_DESCRIPTOR_HAS_ALLOW_ACL = globals().get("_MACOS_DESCRIPTOR_HAS_ALLOW_ACL")
if not callable(MACOS_DESCRIPTOR_HAS_ALLOW_ACL):
    raise RuntimeError("Task Witness ACL policy was not injected")


MAX_FILE_BYTES = 1024 * 1024
MAX_BUNDLE_FILES = 256
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
REQUIRED_OS_FLAGS = ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY")


def _flags(*names: str) -> int:
    missing = [name for name in REQUIRED_OS_FLAGS if not hasattr(os, name)]
    if missing:
        raise C.EvidenceError(
            f"required descriptor primitives unavailable: {', '.join(missing)}"
        )
    return os.O_RDONLY | sum(int(getattr(os, name)) for name in names)


def _private(
    metadata: os.stat_result,
    label: str,
    directory: bool,
    descriptor: int | None = None,
) -> None:
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise C.EvidenceError(f"{label} must be owned by the current user and private")
    is_right_type = (
        stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    )
    if not is_right_type:
        raise C.EvidenceError(f"{label} has the wrong file type")
    if not directory and metadata.st_nlink != 1:
        raise C.EvidenceError(f"{label} has an unsafe hard link")
    if descriptor is not None:
        try:
            has_allow_acl = MACOS_DESCRIPTOR_HAS_ALLOW_ACL(descriptor)
        except OSError as error:
            raise C.EvidenceError(f"{label} ACL cannot be verified") from error
        if has_allow_acl:
            raise C.EvidenceError(f"{label} has a permissive ACL entry")


def absolute(path: Path, label: str) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise C.EvidenceError(f"{label} must be absolute and traversal-free")
    return path


def descriptor_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(metadata, name) for name in ("st_dev", "st_ino", "st_mode"))


def open_chain(path: Path, label: str) -> list[tuple[int, tuple[int, ...], str]]:
    path = absolute(path, label)
    chain = []
    try:
        descriptor = os.open("/", _flags("O_CLOEXEC", "O_NOFOLLOW", "O_DIRECTORY"))
        chain.append((descriptor, descriptor_identity(os.fstat(descriptor)), ""))
        for part in path.parts[1:]:
            metadata = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode):
                raise C.EvidenceError(f"{label} contains a non-directory component")
            descriptor = os.open(
                part,
                _flags("O_CLOEXEC", "O_NOFOLLOW", "O_DIRECTORY"),
                dir_fd=descriptor,
            )
            chain.append((descriptor, descriptor_identity(os.fstat(descriptor)), part))
        return chain
    except BaseException as error:
        close_chain(chain)
        if isinstance(error, C.EvidenceError):
            raise
        raise C.EvidenceError(f"cannot open {label}") from error


def recheck_chain(chain: list[tuple[int, tuple[int, ...], str]], label: str) -> None:
    for index, (descriptor, identity, name) in enumerate(chain):
        if descriptor_identity(os.fstat(descriptor)) != identity:
            raise C.EvidenceError(f"{label} ancestry changed during validation")
    for index, (_, identity, name) in enumerate(chain):
        if (
            index
            and descriptor_identity(
                os.stat(name, dir_fd=chain[index - 1][0], follow_symlinks=False)
            )
            != identity
        ):
            raise C.EvidenceError(f"{label} ancestry changed during validation")


def close_chain(chain: list[tuple[int, tuple[int, ...], str]]) -> None:
    errors = []
    for descriptor, _, _ in reversed(chain):
        try:
            os.close(descriptor)
        except OSError as error:
            errors.append(error)
    if errors:
        raise errors[0]


def open_at(
    directory: int, name: str, label: str, *, private: bool = True
) -> tuple[int, tuple[int, ...]]:
    if "/" in name or name in {"", ".", ".."}:
        raise C.EvidenceError(f"{label} has an unsafe relative path")
    try:
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if private:
            _private(before, label, False)
        elif not stat.S_ISREG(before.st_mode):
            raise C.EvidenceError(f"{label} has the wrong file type")
        descriptor = os.open(
            name,
            _flags("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"),
            dir_fd=directory,
        )
        opened = os.fstat(descriptor)
        if private:
            _private(opened, label, False, descriptor)
        elif not stat.S_ISREG(opened.st_mode):
            raise C.EvidenceError(f"{label} has the wrong file type")
        expected = descriptor_identity(opened)
        if descriptor_identity(before) != expected:
            raise C.EvidenceError(f"{label} changed between preflight and open")
        return descriptor, expected
    except BaseException as error:
        if "descriptor" in locals():
            os.close(descriptor)
        if isinstance(error, C.EvidenceError):
            raise
        raise C.EvidenceError(f"cannot open {label}") from error


def read_descriptor(descriptor: int, expected: tuple[int, ...], label: str) -> bytes:
    if os.fstat(descriptor).st_size > MAX_FILE_BYTES:
        raise C.EvidenceError(f"{label} exceeds the byte limit")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while chunk := os.read(descriptor, min(1024 * 1024, MAX_FILE_BYTES - total + 1)):
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_FILE_BYTES:
            raise C.EvidenceError(f"{label} exceeds the byte limit")
    if descriptor_identity(os.fstat(descriptor)) != expected:
        raise C.EvidenceError(f"{label} changed during descriptor read")
    return b"".join(chunks)


def read_path(path: Path, label: str, *, private: bool = True) -> bytes:
    chain = open_chain(path.parent, f"{label} parent")
    try:
        descriptor, identity = open_at(chain[-1][0], path.name, label, private=private)
        try:
            return read_descriptor(descriptor, identity, label)
        finally:
            os.close(descriptor)
    finally:
        close_chain(chain)


def _json_depth(raw: bytes) -> None:
    depth = 0
    in_string = escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
        elif byte == ord('"'):
            in_string = True
        elif byte in (ord("{"), ord("[")):
            depth += 1
            if depth > C.MAX_JSON_DEPTH:
                raise C.EvidenceError("JSON nesting exceeds the depth limit")
        elif byte in (ord("}"), ord("]")):
            depth -= 1


def json_object(raw: bytes, label: str) -> dict[str, Any]:
    if len(raw) > C.MAX_JSON_BYTES:
        raise C.EvidenceError(f"{label} exceeds the byte limit")
    _json_depth(raw)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=C.pairs,
            parse_constant=C.constant,
            parse_int=C.integer,
            parse_float=C.floating,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise C.EvidenceError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict) or raw != C.canonical_bytes(value) + b"\n":
        raise C.EvidenceError(f"{label} must be a canonical JSON object")
    return value


def open_directory_at(
    parent: int, name: str, label: str
) -> tuple[int, tuple[int, ...]]:
    if "/" in name or name in {"", ".", ".."}:
        raise C.EvidenceError(f"{label} has an unsafe relative path")
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _private(before, label, True)
        descriptor = os.open(
            name,
            _flags("O_CLOEXEC", "O_NOFOLLOW", "O_DIRECTORY"),
            dir_fd=parent,
        )
        expected = descriptor_identity(os.fstat(descriptor))
        if expected != descriptor_identity(before):
            raise C.EvidenceError(f"{label} changed between preflight and open")
        _private(os.fstat(descriptor), label, True, descriptor)
        return descriptor, expected
    except BaseException as error:
        if "descriptor" in locals():
            os.close(descriptor)
        if isinstance(error, C.EvidenceError):
            raise
        raise C.EvidenceError(f"cannot open {label}") from error


class BundleView:
    """A stable descriptor-backed snapshot of a single flat evidence bundle."""

    def __init__(
        self,
        chain: list[tuple[int, tuple[int, ...], str]],
        root: int,
        root_identity: tuple[int, ...],
        root_name: str,
        files: dict[str, tuple[int, tuple[int, ...], bytes]],
    ) -> None:
        self.chain = chain
        self.parent = chain[-1][0]
        self.root = root
        self.root_identity = root_identity
        self.root_name = root_name
        self.files = files

    @property
    def names(self) -> set[str]:
        return set(self.files)

    def read_json(self, name: str, label: str) -> tuple[dict[str, Any], bytes]:
        item = self.files.get(name)
        if item is None:
            raise C.EvidenceError(f"{label} is absent from the captured bundle")
        return json_object(item[2], label), item[2]


def bundle_names(descriptor: int, label: str) -> frozenset[str]:
    names: set[str] = set()
    with os.scandir(descriptor) as entries:
        for entry in entries:
            names.add(entry.name)
            if len(names) > MAX_BUNDLE_FILES:
                raise C.EvidenceError(f"{label} exceeds the file limit")
    return frozenset(names)


def open_bundle(path: Path, label: str) -> BundleView:
    path = absolute(path, label)
    chain = open_chain(path.parent, f"{label} parent")
    parent = chain[-1][0]
    try:
        root, root_identity = open_directory_at(parent, path.name, label)
        files: dict[str, tuple[int, tuple[int, ...], bytes]] = {}
        try:
            names = bundle_names(root, label)
            total = 0
            for name in names:
                descriptor, identity = open_at(root, name, f"{label} child")
                try:
                    raw = read_descriptor(descriptor, identity, f"{label} child")
                except BaseException:
                    os.close(descriptor)
                    raise
                total += len(raw)
                if total > MAX_BUNDLE_BYTES:
                    os.close(descriptor)
                    raise C.EvidenceError(f"{label} exceeds the byte limit")
                files[name] = (descriptor, identity, raw)
            return BundleView(chain, root, root_identity, path.name, files)
        except BaseException:
            for descriptor, _, _ in files.values():
                os.close(descriptor)
            os.close(root)
            raise
    except BaseException:
        close_chain(chain)
        raise


def recheck_bundle(view: BundleView, label: str) -> None:
    for descriptor, identity, raw in view.files.values():
        _private(os.fstat(descriptor), f"{label} child", False, descriptor)
        if read_descriptor(descriptor, identity, f"{label} child") != raw:
            raise C.EvidenceError(f"{label} child changed during validation")
    _private(os.fstat(view.root), label, True, view.root)
    if descriptor_identity(os.fstat(view.root)) != view.root_identity:
        raise C.EvidenceError(f"{label} changed during validation")
    recheck_chain(view.chain, label)
    visible_root = os.stat(view.root_name, dir_fd=view.parent, follow_symlinks=False)
    try:
        current_names = bundle_names(view.root, label)
    except C.EvidenceError as error:
        raise C.EvidenceError(f"{label} inventory changed during validation") from error
    if descriptor_identity(
        visible_root
    ) != view.root_identity or current_names != frozenset(view.names):
        raise C.EvidenceError(f"{label} inventory changed during validation")
    for name, (_, identity, _) in view.files.items():
        if (
            descriptor_identity(os.stat(name, dir_fd=view.root, follow_symlinks=False))
            != identity
        ):
            raise C.EvidenceError(f"{label} child changed during validation")


def close_bundle(view: BundleView) -> None:
    errors = []
    for descriptor, _, _ in view.files.values():
        try:
            os.close(descriptor)
        except OSError as error:
            errors.append(error)
    try:
        os.close(view.root)
    except OSError as error:
        errors.append(error)
    try:
        close_chain(view.chain)
    except OSError as error:
        errors.append(error)
    if errors:
        raise errors[0]
