"""Stable, bounded reads for local evidence files."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import hmac
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, BinaryIO, Iterator

from .canonical import DIGEST


class FileEvidenceError(ValueError):
    pass


class VerifiedFileSnapshot:
    """A descriptor-backed binary stream for one verified file snapshot."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    def fileno(self) -> int:
        return self._stream.fileno()

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        return self._stream.seek(offset, whence)

    @property
    def closed(self) -> bool:
        return self._stream.closed


@dataclass(frozen=True)
class FileEvidence:
    content: bytes
    digest: str
    mtime_ns: int


MAX_VERIFIED_SNAPSHOT_BYTES = 8 * 1024 * 1024 * 1024
_ACL_TYPE_EXTENDED = 0x100


def _macos_descriptor_has_allow_acl(descriptor: int) -> bool:
    """Return whether a pinned descriptor has an extended allow ACL."""
    if sys.platform != "darwin":
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.acl_get_fd_np.restype = ctypes.c_void_p
    libc.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
    libc.acl_to_text.restype = ctypes.c_void_p
    libc.acl_free.argtypes = [ctypes.c_void_p]
    libc.acl_free.restype = ctypes.c_int
    acl = libc.acl_get_fd_np(descriptor, _ACL_TYPE_EXTENDED)
    if not acl:
        error = ctypes.get_errno()
        if error in {0, errno.ENOENT, getattr(errno, "ENOATTR", 93)}:
            return False
        raise OSError(error, os.strerror(error))
    text_pointer = None
    try:
        length = ctypes.c_ssize_t()
        text_pointer = libc.acl_to_text(acl, ctypes.byref(length))
        if not text_pointer:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        rendered = ctypes.string_at(text_pointer, length.value)
        return any(b":allow:" in line for line in rendered.splitlines())
    finally:
        if text_pointer:
            libc.acl_free(text_pointer)
        libc.acl_free(acl)


def open_trusted_parent(
    path: Path,
    *,
    unavailable_message: str,
    not_directory_message: str,
    owner_message: str,
    writable_message: str,
    require_absolute: bool = True,
    create_missing: bool = True,
) -> int:
    """Open/create an absolute directory without following mutable names."""
    if (
        (require_absolute and (not path.is_absolute() or ".." in path.parts))
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise OSError(unavailable_message)
    absolute = Path(os.path.abspath(path))
    if sys.platform == "darwin" and len(absolute.parts) > 1:
        aliases = {
            "var": ("private", "var"),
            "tmp": ("private", "tmp"),
            "etc": ("private", "etc"),
        }
        replacement = aliases.get(absolute.parts[1])
        if replacement is not None:
            absolute = Path("/").joinpath(
                *replacement, *absolute.parts[2:]
            )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)

    def validate(current: int) -> None:
        metadata = os.fstat(current)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError(not_directory_message)
        if metadata.st_uid not in {0, os.geteuid()}:
            raise OSError(owner_message)
        writable = stat.S_IMODE(metadata.st_mode) & 0o022
        if writable and not metadata.st_mode & stat.S_ISVTX:
            raise OSError(writable_message)
        if _macos_descriptor_has_allow_acl(current):
            raise FileEvidenceError(writable_message)

    try:
        validate(descriptor)
        for component in absolute.parts[1:]:
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create_missing:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                else:
                    os.fsync(descriptor)
                child = os.open(component, flags, dir_fd=descriptor)
            try:
                validate(child)
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _validate_max_bytes(max_bytes: Any, label: str) -> int:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise FileEvidenceError(f"{label} size limit is invalid")
    return max_bytes


def _unsafe_directory(metadata: os.stat_result) -> bool:
    mode = stat.S_IMODE(metadata.st_mode)
    return metadata.st_uid not in {0, os.geteuid()} or bool(
        mode & 0o022 and not mode & stat.S_ISVTX
    )


def validate_trusted_regular_file(
    metadata: os.stat_result,
    label: str,
    *,
    descriptor: int | None = None,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise FileEvidenceError(f"{label} must be a regular file")
    if metadata.st_uid not in {0, os.geteuid()}:
        raise FileEvidenceError(f"{label} must be owned by the current user or root")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise FileEvidenceError(f"{label} must not be group or world writable")
    if metadata.st_nlink != 1:
        raise FileEvidenceError(f"{label} must not have hard links")
    if descriptor is not None and _macos_descriptor_has_allow_acl(descriptor):
        raise FileEvidenceError(f"{label} must not have permissive ACL entries")


def file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


@contextmanager
def verified_file_snapshot(
    value: str | Path,
    label: str,
    expected_digest: str,
    *,
    max_bytes: int = MAX_VERIFIED_SNAPSHOT_BYTES,
) -> Iterator[VerifiedFileSnapshot]:
    max_bytes = _validate_max_bytes(max_bytes, label)
    if not isinstance(value, (str, Path)):
        raise FileEvidenceError(f"{label} path must be absolute")
    if not isinstance(expected_digest, str) or DIGEST.fullmatch(expected_digest) is None:
        raise FileEvidenceError(f"{label} digest is invalid")
    source = Path(value)
    if not source.is_absolute():
        raise FileEvidenceError(f"{label} path must be absolute")
    reject_symlink_components(source, label, allow_missing=False)
    parent_descriptor: int | None = None
    try:
        parent_descriptor = open_trusted_parent(
            source.parent,
            unavailable_message=f"{label} is unavailable",
            not_directory_message=f"{label} is unavailable",
            owner_message=(
                f"{label} path must not contain an untrusted or writable ancestor"
            ),
            writable_message=(
                f"{label} path must not contain an untrusted or writable ancestor"
            ),
            create_missing=False,
        )
        validate_trusted_regular_file(
            os.stat(source.name, dir_fd=parent_descriptor, follow_symlinks=False),
            label,
        )
    except FileEvidenceError:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise
    except OSError:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise FileEvidenceError(f"{label} is unavailable") from None
    source_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        source_descriptor = os.open(
            source.name, source_flags, dir_fd=parent_descriptor
        )
    except OSError:
        os.close(parent_descriptor)
        raise FileEvidenceError(f"{label} is unavailable") from None
    yield_started = False
    try:
        before = os.fstat(source_descriptor)
        validate_trusted_regular_file(before, label, descriptor=source_descriptor)
        if before.st_size > max_bytes:
            raise FileEvidenceError(f"{label} is too large")
        with tempfile.TemporaryDirectory(
            prefix="hindsight-memory-verified-archive-"
        ) as temporary:
            snapshot_directory = Path(temporary)
            snapshot_directory.chmod(0o700)
            snapshot = snapshot_directory / "snapshot"
            snapshot_descriptor = os.open(
                snapshot,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o400,
            )
            artifact_hash = hashlib.sha256()
            size = 0
            try:
                while chunk := os.read(source_descriptor, 1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileEvidenceError(f"{label} is too large")
                    artifact_hash.update(chunk)
                    remaining = memoryview(chunk)
                    while remaining:
                        written = os.write(snapshot_descriptor, remaining)
                        if written <= 0:
                            raise OSError("snapshot write failed")
                        remaining = remaining[written:]
                os.fsync(snapshot_descriptor)
                os.fchmod(snapshot_descriptor, 0o400)
                snapshot_metadata = os.fstat(snapshot_descriptor)
            finally:
                os.close(snapshot_descriptor)
            after = os.fstat(source_descriptor)
            current = os.stat(
                source.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if file_identity(before) != file_identity(after) or (
                current.st_dev, current.st_ino
            ) != (after.st_dev, after.st_ino):
                raise FileEvidenceError(f"{label} changed while being snapshotted")
            if not hmac.compare_digest(artifact_hash.hexdigest(), expected_digest):
                raise FileEvidenceError(f"{label} digest does not match plan")
            verified_descriptor = os.open(
                snapshot,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            verified_metadata = os.fstat(verified_descriptor)
            validate_trusted_regular_file(
                verified_metadata, label, descriptor=verified_descriptor
            )
            if file_identity(snapshot_metadata) != file_identity(
                verified_metadata
            ):
                os.close(verified_descriptor)
                raise FileEvidenceError(f"{label} snapshot identity changed")
            snapshot.unlink()
            pinned_metadata = os.fstat(verified_descriptor)
            if (
                not stat.S_ISREG(pinned_metadata.st_mode)
                or pinned_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(pinned_metadata.st_mode) & 0o077
                or pinned_metadata.st_nlink != 0
                or (pinned_metadata.st_dev, pinned_metadata.st_ino)
                != (snapshot_metadata.st_dev, snapshot_metadata.st_ino)
            ):
                os.close(verified_descriptor)
                raise FileEvidenceError(f"{label} snapshot identity changed")
            with os.fdopen(verified_descriptor, "rb", closefd=True) as stream:
                verified = VerifiedFileSnapshot(stream)
                yield_started = True
                yield verified
                final_metadata = os.fstat(verified_descriptor)
                final_hash = hashlib.sha256()
                offset = 0
                while chunk := os.pread(
                    verified_descriptor, 1024 * 1024, offset
                ):
                    final_hash.update(chunk)
                    offset += len(chunk)
                if (
                    file_identity(pinned_metadata)
                    != file_identity(final_metadata)
                    or not hmac.compare_digest(
                        final_hash.hexdigest(), expected_digest
                    )
                ):
                    raise FileEvidenceError(f"{label} snapshot changed")
    except FileEvidenceError:
        raise
    except OSError:
        if yield_started:
            raise
        raise FileEvidenceError(f"{label} is unavailable") from None
    finally:
        try:
            os.close(source_descriptor)
        except OSError:
            pass
        os.close(parent_descriptor)


def _path_has_no_symlink_components(
    path: Path, label: str, *, allow_missing: bool
) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                return False
            raise FileEvidenceError(f"{label} is unavailable") from None
        except OSError:
            raise FileEvidenceError(f"{label} is unavailable") from None
        if stat.S_ISLNK(metadata.st_mode):
            if current.parent == Path("/") and metadata.st_uid == 0:
                try:
                    resolved_target = current.resolve(strict=True)
                except FileNotFoundError:
                    if allow_missing:
                        return False
                    raise FileEvidenceError(f"{label} is unavailable") from None
                except RuntimeError:
                    raise FileEvidenceError(
                        f"{label} path must not contain a symlink cycle"
                    ) from None
                except OSError:
                    raise FileEvidenceError(f"{label} is unavailable") from None
                if resolved_target == current:
                    raise FileEvidenceError(
                        f"{label} path must not contain a symlink cycle"
                    )
                _path_has_no_symlink_components(
                    resolved_target, label, allow_missing=False,
                )
                try:
                    target_metadata = resolved_target.lstat()
                except OSError:
                    raise FileEvidenceError(f"{label} is unavailable") from None
                if (
                    stat.S_ISDIR(target_metadata.st_mode)
                    and _unsafe_directory(target_metadata)
                ):
                    raise FileEvidenceError(
                        f"{label} path must not contain an untrusted or writable ancestor"
                    )
                continue
            raise FileEvidenceError(f"{label} path must not contain symlinks")
        if current != path and stat.S_ISDIR(metadata.st_mode):
            if _unsafe_directory(metadata):
                raise FileEvidenceError(
                    f"{label} path must not contain an untrusted or writable ancestor"
                )
    return True


def reject_symlink_components(path: Path, label: str, *, allow_missing: bool) -> None:
    _path_has_no_symlink_components(path, label, allow_missing=allow_missing)


def read_file_evidence_with_metadata(
    value: str | Path,
    label: str,
    *,
    allow_missing: bool = False,
    max_bytes: int = 1024 * 1024,
) -> FileEvidence | None:
    max_bytes = _validate_max_bytes(max_bytes, label)
    if not isinstance(value, (str, Path)):
        raise FileEvidenceError(f"{label} path must be absolute")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise FileEvidenceError(f"{label} path must be absolute")
    if not _path_has_no_symlink_components(
        path, label, allow_missing=allow_missing
    ):
        return None
    parent_descriptor: int | None = None
    try:
        parent_descriptor = open_trusted_parent(
            path.parent,
            unavailable_message=f"{label} is unavailable",
            not_directory_message=f"{label} is unavailable",
            owner_message=(
                f"{label} path must not contain an untrusted or writable ancestor"
            ),
            writable_message=(
                f"{label} path must not contain an untrusted or writable ancestor"
            ),
            create_missing=False,
        )
        validate_trusted_regular_file(
            os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False),
            label,
        )
    except FileEvidenceError:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise
    except FileNotFoundError:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        if allow_missing:
            return None
        raise FileEvidenceError(f"{label} is unavailable") from None
    except OSError:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise FileEvidenceError(f"{label} is unavailable") from None
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        os.close(parent_descriptor)
        if allow_missing:
            return None
        raise FileEvidenceError(f"{label} is unavailable") from None
    except OSError:
        os.close(parent_descriptor)
        raise FileEvidenceError(f"{label} is unavailable") from None
    try:
        before = os.fstat(descriptor)
        validate_trusted_regular_file(before, label, descriptor=descriptor)
        if before.st_size > max_bytes:
            raise FileEvidenceError(f"{label} is too large")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(65536, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise FileEvidenceError(f"{label} is too large")
        after = os.fstat(descriptor)
    except FileEvidenceError:
        os.close(parent_descriptor)
        raise
    except OSError:
        os.close(parent_descriptor)
        raise FileEvidenceError(f"{label} is unavailable") from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    try:
        current = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        os.close(parent_descriptor)
        raise FileEvidenceError(f"{label} changed while being read") from None
    os.close(parent_descriptor)
    if file_identity(before) != file_identity(after) or (
        current.st_dev, current.st_ino
    ) != (after.st_dev, after.st_ino):
        raise FileEvidenceError(f"{label} changed while being read")
    raw = b"".join(chunks)
    return FileEvidence(
        content=raw,
        digest=hashlib.sha256(raw).hexdigest(),
        mtime_ns=after.st_mtime_ns,
    )


def read_file_evidence(
    value: str | Path,
    label: str,
    *,
    allow_missing: bool = False,
    max_bytes: int = 1024 * 1024,
) -> tuple[bytes, str] | None:
    """Return the stable legacy content/digest pair for local evidence."""

    evidence = read_file_evidence_with_metadata(
        value,
        label,
        allow_missing=allow_missing,
        max_bytes=max_bytes,
    )
    if evidence is None:
        return None
    return evidence.content, evidence.digest
