"""Rollback-safe replacement support for generated repository artifacts.

Each destination remains a normal public file and every visible update uses an
atomic replacement in that file's directory.  A sequence of replacements at
independent paths cannot be crash-atomic without changing that public shape;
this module restores all pre-call bytes after detected failures by default, but
an abrupt process or power loss between replacements can still expose a
committed prefix.  A specialized single-file caller may instead supply durable
recovery callbacks and retain the committed value after an ambiguous failure.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
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
    identity: tuple[int, ...] | None


@dataclass(frozen=True)
class _Target:
    path: Path
    relative: Path
    parent_path: Path
    parent_components: tuple[str, ...]
    name: str
    parent_descriptor: int
    content: bytes
    mode: int


@dataclass(frozen=True)
class _Staged:
    target: _Target
    name: str
    path: Path
    descriptor: int
    identity: tuple[int, ...]


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
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


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise RefreshTransactionError(
            "generated artifact confinement requires O_DIRECTORY and O_NOFOLLOW"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    return flags | getattr(os, "O_CLOEXEC", 0)


def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)


def _open_parent_from_root(
    root_descriptor: int,
    components: tuple[str, ...],
) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for component in components:
            if component in {"", ".", ".."}:
                raise RefreshTransactionError(
                    "generated artifact parent path is invalid"
                )
            try:
                child = os.open(
                    component,
                    _directory_flags(),
                    dir_fd=descriptor,
                )
            except OSError:
                raise
            metadata = os.fstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(child)
                raise RefreshTransactionError(
                    "generated artifact parent path is invalid"
                )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_transaction(
    repository_root: Path,
    replacements: Mapping[Path, tuple[bytes, int]],
) -> tuple[Path, int, list[_Target]]:
    root = Path(os.path.abspath(repository_root))
    try:
        root_descriptor = os.open(root, _directory_flags())
    except OSError as error:
        raise RefreshTransactionError(
            f"generated artifact repository root is invalid: {root}"
        ) from error
    targets: list[_Target] = []
    seen: set[Path] = set()
    try:
        if not stat.S_ISDIR(os.fstat(root_descriptor).st_mode):
            raise RefreshTransactionError(
                f"generated artifact repository root is invalid: {root}"
            )
        for destination, (content, mode) in replacements.items():
            absolute = Path(os.path.abspath(destination))
            try:
                relative = absolute.relative_to(root)
            except ValueError as error:
                raise RefreshTransactionError(
                    f"generated artifact path escapes repository root: {destination}"
                ) from error
            if relative == Path(".") or relative in seen:
                raise RefreshTransactionError(
                    f"generated artifact path is not a unique repository file: {destination}"
                )
            seen.add(relative)
            try:
                parent_descriptor = _open_parent_from_root(
                    root_descriptor,
                    tuple(relative.parts[:-1]),
                )
            except (OSError, RefreshTransactionError) as error:
                raise RefreshTransactionError(
                    f"generated artifact parent is invalid: {absolute.parent}"
                ) from error
            targets.append(
                _Target(
                    path=absolute,
                    relative=relative,
                    parent_path=absolute.parent,
                    parent_components=tuple(relative.parts[:-1]),
                    name=relative.name,
                    parent_descriptor=parent_descriptor,
                    content=content,
                    mode=mode,
                )
            )
    except BaseException:
        for target in targets:
            os.close(target.parent_descriptor)
        os.close(root_descriptor)
        raise
    return root, root_descriptor, targets


def _require_parent_binding(
    root: Path,
    root_descriptor: int,
    target: _Target,
) -> None:
    try:
        visible_root = os.open(root, _directory_flags())
    except OSError as error:
        raise RefreshTransactionError(
            f"generated artifact parent changed: {target.parent_path}"
        ) from error
    try:
        if _directory_identity(os.fstat(visible_root)) != _directory_identity(
            os.fstat(root_descriptor)
        ):
            raise RefreshTransactionError(
                f"generated artifact parent changed: {target.parent_path}"
            )
    finally:
        os.close(visible_root)
    try:
        visible_parent = _open_parent_from_root(
            root_descriptor,
            target.parent_components,
        )
    except (OSError, RefreshTransactionError) as error:
        raise RefreshTransactionError(
            f"generated artifact parent changed: {target.parent_path}"
        ) from error
    try:
        if _directory_identity(os.fstat(visible_parent)) != _directory_identity(
            os.fstat(target.parent_descriptor)
        ):
            raise RefreshTransactionError(
                f"generated artifact parent changed: {target.parent_path}"
            )
    finally:
        os.close(visible_parent)


def _capture_preimage(
    root: Path,
    root_descriptor: int,
    target: _Target,
    *,
    require_binding: bool,
) -> _Preimage:
    if require_binding:
        _require_parent_binding(root, root_descriptor, target)
    try:
        before = os.stat(
            target.name,
            dir_fd=target.parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return _Preimage(None, None, None)
    if not stat.S_ISREG(before.st_mode):
        raise RefreshTransactionError(
            f"generated artifact is not a regular file: {target.path}"
        )
    if before.st_nlink != 1:
        raise RefreshTransactionError(
            f"generated artifact is hard-linked: {target.path}"
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(
            target.name,
            flags,
            dir_fd=target.parent_descriptor,
        )
    except OSError as error:
        raise RefreshTransactionError(
            f"generated artifact changed while it was observed: {target.path}"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise RefreshTransactionError(
                f"generated artifact changed while it was observed: {target.path}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as file:
            content = file.read()
        after_read = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        visible = os.stat(
            target.name,
            dir_fd=target.parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError as error:
        raise RefreshTransactionError(
            f"generated artifact changed while it was observed: {target.path}"
        ) from error
    identity = _metadata_identity(before)
    if not (
        identity
        == _metadata_identity(opened)
        == _metadata_identity(after_read)
        == _metadata_identity(visible)
    ):
        raise RefreshTransactionError(
            f"generated artifact changed while it was observed: {target.path}"
        )
    return _Preimage(content, stat.S_IMODE(opened.st_mode), identity)


def _stage(
    root: Path,
    root_descriptor: int,
    target: _Target,
    *,
    require_binding: bool = True,
) -> _Staged:
    if require_binding:
        _require_parent_binding(root, root_descriptor, target)
    descriptor = -1
    temporary_name = ""
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    for _attempt in range(128):
        temporary_name = f".{target.name}.{secrets.token_hex(16)}"
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=target.parent_descriptor,
            )
        except FileExistsError:
            continue
        break
    if descriptor < 0:
        raise RefreshTransactionError(
            f"generated artifact temporary file is unavailable: {target.path}"
        )
    try:
        remaining = memoryview(target.content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise RefreshTransactionError(
                    f"generated artifact staging failed: {target.path}"
                )
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, target.mode)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != target.mode
        ):
            raise RefreshTransactionError(
                f"generated artifact staging identity is invalid: {target.path}"
            )
        identity = _metadata_identity(metadata)
    except BaseException:
        try:
            try:
                os.unlink(temporary_name, dir_fd=target.parent_descriptor)
            except FileNotFoundError:
                pass
        finally:
            os.close(descriptor)
        raise
    return _Staged(
        target=target,
        name=temporary_name,
        path=root / target.relative.parent / temporary_name,
        descriptor=descriptor,
        identity=identity,
    )


def _require_staged(
    root: Path,
    root_descriptor: int,
    staged: _Staged,
) -> None:
    target = staged.target
    _require_parent_binding(root, root_descriptor, target)
    try:
        before = os.fstat(staged.descriptor)
        os.lseek(staged.descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = len(target.content)
        while remaining:
            chunk = os.read(staged.descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise RefreshTransactionError(
                    f"generated artifact staged file changed: {target.path}"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(staged.descriptor, 1):
            raise RefreshTransactionError(
                f"generated artifact staged file changed: {target.path}"
            )
        after = os.fstat(staged.descriptor)
        visible = os.stat(
            staged.name,
            dir_fd=target.parent_descriptor,
            follow_symlinks=False,
        )
    except (OSError, FileNotFoundError) as error:
        raise RefreshTransactionError(
            f"generated artifact staged file changed: {target.path}"
        ) from error
    if not (
        staged.identity
        == _metadata_identity(before)
        == _metadata_identity(after)
        == _metadata_identity(visible)
    ) or b"".join(chunks) != target.content:
        raise RefreshTransactionError(
            f"generated artifact staged file changed: {target.path}"
        )


def _require_preimage(
    root: Path,
    root_descriptor: int,
    target: _Target,
    expected: _Preimage,
) -> None:
    if (
        _capture_preimage(
            root,
            root_descriptor,
            target,
            require_binding=True,
        )
        != expected
    ):
        raise RefreshTransactionError(
            f"generated artifact changed before replacement: {target.path}"
        )


def _matches_content_and_mode(
    observed: _Preimage,
    content: bytes | None,
    mode: int | None,
) -> bool:
    return observed.content == content and observed.mode == mode


def _require_replacement(
    root: Path,
    root_descriptor: int,
    target: _Target,
    *,
    require_binding: bool = True,
) -> None:
    observed = _capture_preimage(
        root,
        root_descriptor,
        target,
        require_binding=require_binding,
    )
    if not _matches_content_and_mode(observed, target.content, target.mode):
        raise RefreshTransactionError(
            f"generated artifact replacement identity is invalid: {target.path}"
        )


def _require_staged_replacement(
    root: Path,
    root_descriptor: int,
    staged: _Staged,
    *,
    require_binding: bool,
) -> None:
    observed = _capture_preimage(
        root,
        root_descriptor,
        staged.target,
        require_binding=require_binding,
    )
    opened = os.fstat(staged.descriptor)
    if (
        observed.identity != _metadata_identity(opened)
        or not _matches_content_and_mode(
            observed,
            staged.target.content,
            staged.target.mode,
        )
    ):
        raise RefreshTransactionError(
            "generated artifact staged replacement identity is invalid: "
            f"{staged.target.path}"
        )


def _rollback(
    root: Path,
    root_descriptor: int,
    committed: list[_Staged],
    preimages: Mapping[_Target, _Preimage],
) -> None:
    rollback_temporaries: list[_Staged] = []
    try:
        for committed_stage in reversed(committed):
            target = committed_stage.target
            preimage = preimages[target]
            _require_staged_replacement(
                root,
                root_descriptor,
                committed_stage,
                require_binding=False,
            )
            if preimage.content is None:
                os.unlink(target.name, dir_fd=target.parent_descriptor)
            else:
                assert preimage.mode is not None
                rollback_target = _Target(
                    path=target.path,
                    relative=target.relative,
                    parent_path=target.parent_path,
                    parent_components=target.parent_components,
                    name=target.name,
                    parent_descriptor=target.parent_descriptor,
                    content=preimage.content,
                    mode=preimage.mode,
                )
                temporary = _stage(
                    root,
                    root_descriptor,
                    rollback_target,
                    require_binding=False,
                )
                rollback_temporaries.append(temporary)
                os.replace(
                    temporary.name,
                    target.name,
                    src_dir_fd=target.parent_descriptor,
                    dst_dir_fd=target.parent_descriptor,
                )
                _require_staged_replacement(
                    root,
                    root_descriptor,
                    temporary,
                    require_binding=False,
                )
            os.fsync(target.parent_descriptor)
        for committed_stage in committed:
            target = committed_stage.target
            observed = _capture_preimage(
                root,
                root_descriptor,
                target,
                require_binding=False,
            )
            preimage = preimages[target]
            if not _matches_content_and_mode(
                observed,
                preimage.content,
                preimage.mode,
            ):
                raise RefreshTransactionError(
                    f"generated artifact rollback identity is invalid: {target.path}"
                )
    except BaseException as error:
        raise RefreshTransactionError(
            "generated artifact rollback failed; changed generated paths may remain"
        ) from error
    finally:
        cleanup_error: BaseException | None = None
        for temporary in rollback_temporaries:
            try:
                os.unlink(
                    temporary.name,
                    dir_fd=temporary.target.parent_descriptor,
                )
            except FileNotFoundError:
                pass
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
            finally:
                os.close(temporary.descriptor)
        if cleanup_error is not None:
            raise cleanup_error


def replace_generated_artifacts(
    repository_root: Path,
    replacements: Mapping[Path, tuple[bytes, int]],
    *,
    recheck: Callable[[frozenset[Path]], None],
    verify: Callable[[], None],
    before_replace: Callable[[], None] | None = None,
    replacement_started: Callable[[], None] | None = None,
    rollback_on_failure: bool = True,
) -> None:
    """Commit descriptor-confined replacements with explicit failure recovery.

    The default restores every preimage after a detected failure.  Recovery
    callbacks and rollback opt-out are intentionally limited to one artifact;
    disabling rollback requires callbacks both before and immediately before
    the replacement attempt.
    """

    if not replacements:
        raise RefreshTransactionError("generated artifact plan must not be empty")
    if type(rollback_on_failure) is not bool:
        raise RefreshTransactionError("generated artifact plan is invalid")
    for callback in (before_replace, replacement_started):
        if callback is not None and not callable(callback):
            raise RefreshTransactionError("generated artifact plan is invalid")
    uses_single_target_controls = (
        before_replace is not None
        or replacement_started is not None
        or not rollback_on_failure
    )
    if uses_single_target_controls and len(replacements) != 1:
        raise RefreshTransactionError(
            "single generated artifact controls require exactly one replacement"
        )
    if not rollback_on_failure and (
        before_replace is None or replacement_started is None
    ):
        raise RefreshTransactionError(
            "non-rollback replacement requires both recovery callbacks"
        )
    for destination, (content, mode) in replacements.items():
        if not isinstance(content, bytes) or type(mode) is not int or mode < 0:
            raise RefreshTransactionError("generated artifact plan is invalid")

    root, root_descriptor, targets = _open_transaction(
        repository_root,
        replacements,
    )
    preimages: dict[_Target, _Preimage] = {}
    staged: list[_Staged] = []
    committed: list[_Staged] = []
    try:
        for target in targets:
            preimages[target] = _capture_preimage(
                root,
                root_descriptor,
                target,
                require_binding=True,
            )
        for target in targets:
            _require_preimage(root, root_descriptor, target, preimages[target])
        for target in targets:
            staged.append(_stage(root, root_descriptor, target))
        temporary_paths = frozenset(temporary.path for temporary in staged)
        try:
            for temporary in staged:
                target = temporary.target
                recheck(temporary_paths)
                _require_preimage(
                    root,
                    root_descriptor,
                    target,
                    preimages[target],
                )
                _require_staged(root, root_descriptor, temporary)
                if before_replace is not None:
                    before_replace()
                    _require_preimage(
                        root,
                        root_descriptor,
                        target,
                        preimages[target],
                    )
                    _require_staged(root, root_descriptor, temporary)
                if replacement_started is not None:
                    replacement_started()
                try:
                    os.replace(
                        temporary.name,
                        target.name,
                        src_dir_fd=target.parent_descriptor,
                        dst_dir_fd=target.parent_descriptor,
                    )
                except BaseException:
                    observed = _capture_preimage(
                        root,
                        root_descriptor,
                        target,
                        require_binding=False,
                    )
                    if _matches_content_and_mode(
                        observed,
                        target.content,
                        target.mode,
                    ) and observed.identity == _metadata_identity(
                        os.fstat(temporary.descriptor)
                    ):
                        committed.append(temporary)
                    raise
                committed.append(temporary)
                os.fsync(target.parent_descriptor)
                _require_parent_binding(root, root_descriptor, target)
                _require_staged_replacement(
                    root,
                    root_descriptor,
                    temporary,
                    require_binding=True,
                )
            for temporary in staged:
                _require_staged_replacement(
                    root,
                    root_descriptor,
                    temporary,
                    require_binding=True,
                )
            verify()
        except BaseException:
            if committed and rollback_on_failure:
                _rollback(root, root_descriptor, committed, preimages)
            raise
    finally:
        cleanup_error = None
        try:
            for temporary in staged:
                try:
                    os.unlink(
                        temporary.name,
                        dir_fd=temporary.target.parent_descriptor,
                    )
                except FileNotFoundError:
                    pass
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
                finally:
                    os.close(temporary.descriptor)
        finally:
            for target in targets:
                os.close(target.parent_descriptor)
            os.close(root_descriptor)
        if cleanup_error is not None:
            raise cleanup_error
