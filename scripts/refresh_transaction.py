"""Rollback-safe replacement support for generated repository artifacts.

Each destination remains a normal public file and every visible update uses an
atomic replacement in that file's directory.  A sequence of replacements at
independent paths cannot be crash-atomic without changing that public shape;
this module restores all pre-call bytes after detected failures, but an abrupt
process or power loss between replacements can still expose a committed prefix.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple


class RefreshTransactionError(OSError):
    """A generated-artifact transaction could not preserve its contract."""


class InputEntry(NamedTuple):
    kind: str
    mode: int | None
    sha256: str | None


@dataclass(frozen=True)
class _Preimage:
    content: bytes | None
    mode: int | None


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _require_stable_input(
    condition: bool,
    message: str,
    error_type: type[Exception],
) -> None:
    if not condition:
        raise error_type(message)


def capture_input_entry(
    path: Path,
    *,
    error_type: type[Exception],
) -> InputEntry:
    """Capture one stable path identity using the caller's contract error."""

    try:
        before = path.lstat()
    except FileNotFoundError:
        return InputEntry("missing", None, None)
    mode = stat.S_IMODE(before.st_mode)
    if stat.S_ISDIR(before.st_mode):
        return InputEntry("directory", mode, None)
    if stat.S_ISLNK(before.st_mode):
        target = os.readlink(path)
        after = path.lstat()
        _require_stable_input(
            _metadata_identity(before) == _metadata_identity(after),
            f"validated input changed while snapshotting: {path}",
            error_type,
        )
        return InputEntry(
            "symlink",
            mode,
            hashlib.sha256(os.fsencode(target)).hexdigest(),
        )
    if not stat.S_ISREG(before.st_mode):
        return InputEntry("special", mode, None)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        _require_stable_input(
            stat.S_ISREG(opened.st_mode),
            f"validated input is not a regular file: {path}",
            error_type,
        )
        with os.fdopen(descriptor, "rb", closefd=False) as file:
            content = file.read()
        after_read = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        visible = path.lstat()
    except FileNotFoundError as error:
        raise error_type(
            f"validated input changed while snapshotting: {path}"
        ) from error
    _require_stable_input(
        _metadata_identity(before)
        == _metadata_identity(opened)
        == _metadata_identity(after_read)
        == _metadata_identity(visible),
        f"validated input changed while snapshotting: {path}",
        error_type,
    )
    return InputEntry(
        "regular",
        stat.S_IMODE(opened.st_mode),
        hashlib.sha256(content).hexdigest(),
    )


def snapshot_tree(
    root: Path,
    *,
    error_type: type[Exception],
) -> tuple[tuple[str, InputEntry], ...]:
    """Capture a stable, recursively sorted identity for a visible tree."""

    entries: dict[str, InputEntry] = {}

    def visit(path: Path, relative: str) -> None:
        entry = capture_input_entry(path, error_type=error_type)
        entries[relative] = entry
        if entry.kind != "directory":
            return
        before = path.lstat()
        try:
            with os.scandir(path) as directory:
                children = sorted(item.name for item in directory)
        except FileNotFoundError as error:
            raise error_type(
                f"validated input changed while snapshotting: {path}"
            ) from error
        after = path.lstat()
        _require_stable_input(
            _metadata_identity(before) == _metadata_identity(after),
            f"validated input changed while snapshotting: {path}",
            error_type,
        )
        for name in children:
            child_relative = name if relative == "." else f"{relative}/{name}"
            visit(path / name, child_relative)
        final = path.lstat()
        _require_stable_input(
            _metadata_identity(before) == _metadata_identity(final),
            f"validated input changed while snapshotting: {path}",
            error_type,
        )

    visit(root, ".")
    return tuple(sorted(entries.items()))


def _capture_preimage(path: Path) -> _Preimage:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return _Preimage(None, None)
    if not stat.S_ISREG(before.st_mode):
        raise RefreshTransactionError(
            f"generated artifact is not a regular file: {path}"
        )
    content = path.read_bytes()
    try:
        after = path.lstat()
    except FileNotFoundError as error:
        raise RefreshTransactionError(
            f"generated artifact changed while it was observed: {path}"
        ) from error
    if _metadata_identity(before) != _metadata_identity(after):
        raise RefreshTransactionError(
            f"generated artifact changed while it was observed: {path}"
        )
    return _Preimage(content, stat.S_IMODE(after.st_mode))


def _stage(path: Path, content: bytes, mode: int) -> Path:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise RefreshTransactionError(
            f"generated artifact parent is invalid: {parent}"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as file:
            descriptor = -1
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return temporary


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_preimage(path: Path, expected: _Preimage) -> None:
    if _capture_preimage(path) != expected:
        raise RefreshTransactionError(
            f"generated artifact changed before replacement: {path}"
        )


def _require_replacement(path: Path, content: bytes, mode: int) -> None:
    if _capture_preimage(path) != _Preimage(content, mode):
        raise RefreshTransactionError(
            f"generated artifact replacement identity is invalid: {path}"
        )


def _rollback(
    committed: list[Path], preimages: Mapping[Path, _Preimage]
) -> None:
    rollback_temporaries: list[Path] = []
    try:
        for destination in reversed(committed):
            preimage = preimages[destination]
            if preimage.content is None:
                try:
                    destination.unlink()
                except FileNotFoundError:
                    pass
            else:
                assert preimage.mode is not None
                temporary = _stage(
                    destination,
                    preimage.content,
                    preimage.mode,
                )
                rollback_temporaries.append(temporary)
                os.replace(temporary, destination)
            _fsync_directory(destination.parent)
        for destination in committed:
            _require_preimage(destination, preimages[destination])
    except BaseException as error:
        raise RefreshTransactionError(
            "generated artifact rollback failed; changed generated paths may remain"
        ) from error
    finally:
        for temporary in rollback_temporaries:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def replace_generated_artifacts(
    replacements: Mapping[Path, tuple[bytes, int]],
    *,
    recheck: Callable[[frozenset[Path]], None],
    verify: Callable[[], None],
) -> None:
    """Commit staged replacements and restore every preimage on detected failure."""

    if not replacements:
        raise RefreshTransactionError("generated artifact plan must not be empty")
    for destination, (content, mode) in replacements.items():
        if not destination.is_absolute():
            raise RefreshTransactionError(
                f"generated artifact path must be absolute: {destination}"
            )
        if not isinstance(content, bytes) or type(mode) is not int or mode < 0:
            raise RefreshTransactionError("generated artifact plan is invalid")

    preimages = {
        destination: _capture_preimage(destination) for destination in replacements
    }
    staged: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for destination, (content, mode) in replacements.items():
            staged[destination] = _stage(destination, content, mode)
        temporary_paths = frozenset(staged.values())
        try:
            for destination, (content, mode) in replacements.items():
                recheck(temporary_paths)
                _require_preimage(destination, preimages[destination])
                try:
                    os.replace(staged[destination], destination)
                except BaseException:
                    if _capture_preimage(destination) == _Preimage(content, mode):
                        committed.append(destination)
                    raise
                committed.append(destination)
                _fsync_directory(destination.parent)
            for destination, (content, mode) in replacements.items():
                _require_replacement(destination, content, mode)
            verify()
        except BaseException:
            if committed:
                _rollback(committed, preimages)
            raise
    finally:
        for temporary in staged.values():
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
