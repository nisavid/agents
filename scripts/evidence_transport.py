"""Strict JSON and durable private artifact transport for public eval runners."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable, NoReturn, TypeVar


ErrorFactory = Callable[[str], Exception]
EXACT_CLAUDE_MODEL = re.compile(
    r"claude-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9]+(?:-[0-9]{8})?$"
)
Document = TypeVar("Document", bound=dict[str, Any])


class ProviderTransportFailure(RuntimeError):
    """One provider failure plus the exact streams observed by the producer."""

    def __init__(
        self,
        message: str,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
            raise TypeError("provider failure streams must be bytes")
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.timed_out = timed_out


def _raise(error_factory: ErrorFactory, message: str) -> NoReturn:
    raise error_factory(message)


def strict_json_bytes(
    content: bytes, *, label: str, error_factory: ErrorFactory
) -> Any:
    """Decode one finite UTF-8 JSON value while rejecting duplicate keys."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _raise(error_factory, f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> NoReturn:
        _raise(error_factory, f"non-finite JSON value in {label}: {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            _raise(error_factory, f"non-finite JSON value in {label}: {value}")
        return parsed

    try:
        return json.loads(
            content,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise error_factory(f"invalid JSON in {label}: {error}") from error


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def json_file_bytes(value: Any) -> bytes:
    return canonical_bytes(value) + b"\n"


def digest_bytes(content: bytes) -> str:
    # This is a content identity for evidence binding, not password verification.
    # codeql[py/weak-sensitive-data-hashing]
    return "sha256:" + hashlib.sha256(content).hexdigest()


def stream_identity(content: bytes) -> dict[str, int | str]:
    """Bind arbitrary executable streams without decoding or normalizing them."""

    return {"byte_count": len(content), "sha256": digest_bytes(content)}


def exact_claude_model(value: Any) -> bool:
    return isinstance(value, str) and EXACT_CLAUDE_MODEL.fullmatch(value) is not None


def resolve_executable_identity(
    executable: str,
    *,
    error_factory: ErrorFactory,
    display_name: str,
    version_validator: Callable[[str], bool] | None = None,
) -> dict[str, str]:
    located = shutil.which(executable)
    if located is None:
        _raise(error_factory, f"{display_name} executable was not found: {executable}")
    try:
        resolved = Path(located).resolve(strict=True)
    except OSError as error:
        raise error_factory(f"{display_name} executable cannot be resolved") from error
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        _raise(error_factory, f"{display_name} executable is not executable")
    try:
        completed = subprocess.run(
            [str(resolved), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise error_factory(f"{display_name} version probe failed") from error
    if completed.returncode != 0:
        _raise(error_factory, f"{display_name} version probe failed")
    try:
        version = completed.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise error_factory(f"{display_name} version probe is not UTF-8") from error
    if (
        not version
        or "\n" in version
        or (version_validator is not None and not version_validator(version))
    ):
        _raise(error_factory, f"{display_name} version identity is invalid")
    return {
        "path": str(resolved),
        "sha256": digest_bytes(resolved.read_bytes()),
        "version": version,
    }


def safe_relative_path(
    value: Any, *, label: str, error_factory: ErrorFactory
) -> PurePosixPath:
    if not isinstance(value, str):
        _raise(error_factory, f"{label} must be a path")
    relative = PurePosixPath(value)
    if (
        not value
        or relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        _raise(error_factory, f"unsafe relative path for {label}: {value!r}")
    return relative


def lexical_absolute_path(value: os.PathLike[str] | str) -> Path:
    """Make a path absolute without resolving any filesystem component."""

    return Path(os.path.abspath(os.fspath(value)))


def resolved_path_is_within(root: Path, candidate: Path) -> bool:
    """Check resolved containment without replacing either lexical path."""

    resolved_root = root.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    return resolved_candidate == resolved_root or resolved_candidate.is_relative_to(
        resolved_root
    )


def lexical_artifact_path(
    root: Path,
    relative_value: Any,
    *,
    label: str,
    error_factory: ErrorFactory,
) -> Path:
    """Return a lexical destination after separate lexical and resolved checks."""

    if not root.is_absolute():
        _raise(error_factory, f"{label} root must be absolute")
    relative = safe_relative_path(
        relative_value, label=label, error_factory=error_factory
    )
    candidate = root.joinpath(*relative.parts)
    if not resolved_path_is_within(root, candidate):
        _raise(error_factory, f"{label} resolves outside its output root")
    return candidate


def safe_regular_file(
    root: Path,
    relative_value: Any,
    *,
    label: str,
    error_factory: ErrorFactory,
) -> Path:
    relative = safe_relative_path(
        relative_value, label=label, error_factory=error_factory
    )
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise error_factory(f"{label} root is unavailable") from error
    if not resolved_root.is_dir() or root.is_symlink():
        _raise(error_factory, f"{label} root is unsafe")
    current = resolved_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            _raise(error_factory, f"{label} resolves through a symlink")
    try:
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise error_factory(f"{label} is missing or is not a file") from error
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        _raise(error_factory, f"{label} is missing or is not a file")
    return resolved


def normalize_repository_origin(
    origin: str,
    *,
    canonical: str,
    aliases: set[str],
    error_factory: ErrorFactory,
    error_message: str,
) -> str:
    if not isinstance(origin, str) or origin.strip() not in aliases:
        _raise(error_factory, error_message)
    return canonical


def failure_streams(error: BaseException) -> tuple[bytes, bytes]:
    stdout = getattr(error, "stdout", b"")
    stderr = getattr(error, "stderr", b"")
    if stdout is None:
        stdout = b""
    if stderr is None:
        stderr = b""
    if isinstance(stdout, str):
        stdout = stdout.encode()
    if isinstance(stderr, str):
        stderr = stderr.encode()
    if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
        raise TypeError("provider failure streams must be bytes")
    return stdout, stderr


class AttemptAllocation:
    """Lexical paths reserved for one transport attempt and its raw streams."""

    __slots__ = ("attempt_path", "attempt_relpath", "stream_paths", "stream_relpaths")

    def __init__(
        self,
        *,
        attempt_path: Path,
        attempt_relpath: str,
        stream_paths: dict[str, Path],
        stream_relpaths: dict[str, str],
    ) -> None:
        self.attempt_path = attempt_path
        self.attempt_relpath = attempt_relpath
        self.stream_paths = stream_paths
        self.stream_relpaths = stream_relpaths


class AttemptSuccess:
    """One successful invocation plus the exact streams and terminal fields."""

    __slots__ = ("fields", "finished_at", "streams", "value")

    def __init__(
        self,
        *,
        value: Any,
        streams: dict[str, bytes],
        fields: dict[str, Any] | None = None,
        finished_at: str | None = None,
    ) -> None:
        if not all(
            isinstance(name, str) and isinstance(data, bytes)
            for name, data in streams.items()
        ):
            raise TypeError("attempt success streams must be named bytes")
        self.value = value
        self.streams = streams
        self.fields = fields or {}
        self.finished_at = finished_at


def allocate_attempt_journal(
    root: Path,
    *,
    attempt_relpath: str,
    stream_relpaths: dict[str, str],
    error_factory: ErrorFactory,
) -> AttemptAllocation:
    """Reserve one collision-free lexical attempt layout under an output root."""

    attempt_path = lexical_artifact_path(
        root,
        attempt_relpath,
        label="attempt journal",
        error_factory=error_factory,
    )
    if attempt_path.exists() or attempt_path.is_symlink():
        _raise(
            error_factory, f"attempt journal allocation collision: {attempt_relpath}"
        )
    if not stream_relpaths or len(stream_relpaths) != len(
        set(stream_relpaths.values())
    ):
        _raise(error_factory, "attempt stream allocation is empty or colliding")
    stream_paths: dict[str, Path] = {}
    for name, relative in stream_relpaths.items():
        if not isinstance(name, str) or not name:
            _raise(error_factory, "attempt stream name is invalid")
        path = lexical_artifact_path(
            root,
            relative,
            label=f"attempt {name} stream",
            error_factory=error_factory,
        )
        if path.exists() or path.is_symlink():
            _raise(error_factory, f"attempt stream allocation collision: {relative}")
        stream_paths[name] = path
    return AttemptAllocation(
        attempt_path=attempt_path,
        attempt_relpath=attempt_relpath,
        stream_paths=stream_paths,
        stream_relpaths=dict(stream_relpaths),
    )


def run_attempt_journal(
    allocation: AttemptAllocation,
    *,
    initial: dict[str, Any],
    invoke: Callable[[], AttemptSuccess],
    document_writer: Callable[[Path, dict[str, Any]], None],
    artifact_writer: Callable[[Path, bytes], None],
    clock: Callable[[], str],
    status_names: dict[str, str],
    stream_fields: dict[str, tuple[str, str]],
    digest: Callable[[bytes], str],
    signer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    failure_fields: Callable[[BaseException, str], dict[str, Any]] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Persist, invoke, bind streams, and classify one transport attempt."""

    if set(status_names) != {"started", "success", "failure", "timeout"}:
        raise ValueError("attempt status mapping is incomplete")
    reserved = {"status", "started_at"}
    if reserved & set(initial):
        raise ValueError("attempt initial fields contain lifecycle-owned keys")
    if initial.get("finished_at") is not None:
        raise ValueError("attempt initial finished_at must be null when present")
    document = {
        **initial,
        "status": status_names["started"],
        "started_at": clock(),
    }
    persist_attempt_envelope(
        allocation.attempt_path,
        document,
        writer=document_writer,
        signer=signer,
    )

    def bind_streams(streams: dict[str, bytes]) -> dict[str, Any]:
        if not streams or not set(streams) <= set(stream_fields):
            raise ValueError("attempt terminal stream inventory mismatch")
        bindings: dict[str, Any] = {}
        for name, content in streams.items():
            if not isinstance(content, bytes) or name not in allocation.stream_paths:
                raise TypeError("attempt terminal streams must be allocated bytes")
            artifact_writer(allocation.stream_paths[name], content)
            relpath_field, digest_field = stream_fields[name]
            bindings[relpath_field] = allocation.stream_relpaths[name]
            bindings[digest_field] = digest(content)
        return bindings

    try:
        success = invoke()
        if not isinstance(success, AttemptSuccess):
            raise TypeError("attempt invocation did not return AttemptSuccess")
    except BaseException as error:
        stdout, stderr = failure_streams(error)
        classification = (
            "timeout"
            if isinstance(error, ProviderTransportFailure) and error.timed_out
            else "failure"
        )
        terminal = {
            **document,
            "status": status_names[classification],
            "finished_at": clock(),
            **bind_streams({"stdout": stdout, "stderr": stderr}),
        }
        if failure_fields is None:
            terminal.update(
                {
                    "error": f"{type(error).__name__}: {error}",
                    "returncode": getattr(error, "returncode", None),
                    "timed_out": classification == "timeout",
                }
            )
        else:
            terminal.update(failure_fields(error, classification))
        persist_attempt_envelope(
            allocation.attempt_path,
            terminal,
            writer=document_writer,
            signer=signer,
        )
        raise

    terminal = {
        **document,
        "status": status_names["success"],
        "finished_at": success.finished_at or clock(),
        **bind_streams(success.streams),
        **success.fields,
    }
    terminal = persist_attempt_envelope(
        allocation.attempt_path,
        terminal,
        writer=document_writer,
        signer=signer,
    )
    return success.value, terminal


def persist_attempt_envelope(
    path: Path,
    value: Document,
    *,
    writer: Callable[[Path, dict[str, Any]], None],
    signer: Callable[[dict[str, Any]], Document] | None = None,
) -> Document:
    document = signer(value) if signer is not None else value
    writer(path, document)
    return document


def next_attempt_envelope(
    directory: Path,
    *,
    pattern: re.Pattern[str],
    error_factory: ErrorFactory,
) -> int:
    existing = list(directory.glob("attempt-*.json")) if directory.exists() else []
    indexes: list[int] = []
    for path in existing:
        match = pattern.fullmatch(path.name)
        if match is None:
            _raise(error_factory, f"unreviewed attempt artifact: {path}")
        indexes.append(int(match.group(1)))
    return max(indexes, default=0) + 1


def attempt_history(
    root: Path,
    directory: Path,
    pattern: str,
    *,
    error_factory: ErrorFactory = RuntimeError,
) -> list[str]:
    history: list[str] = []
    for path in directory.glob(pattern):
        if path.is_symlink() or not path.is_file():
            _raise(error_factory, f"unreviewed attempt history artifact: {path}")
        try:
            history.append(path.relative_to(root).as_posix())
        except ValueError as error:
            raise error_factory("attempt history escapes its output root") from error
    return sorted(history)


def candidate_content_identity(root: Path, *, error_factory: ErrorFactory) -> str:
    """Bind every tracked or unignored candidate file by path and exact bytes."""

    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        _raise(error_factory, "cannot enumerate the candidate")
    files = sorted(item for item in result.stdout.split(b"\0") if item)
    digest = hashlib.sha256()
    for encoded in files:
        try:
            relative = encoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise error_factory("candidate path is not UTF-8") from error
        path = root / relative
        if not path.exists() and not path.is_symlink():
            digest.update(hashlib.sha256(b"deleted").hexdigest().encode())
            digest.update(b"  ")
            digest.update(encoded)
            digest.update(b"\n")
            continue
        if path.is_symlink() or not path.is_file():
            _raise(error_factory, "candidate contains an unsafe entry")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
        digest.update(b"  ")
        digest.update(encoded)
        digest.update(b"\n")
    return digest.hexdigest()


def semantic_tree_digest(
    root: Path,
    declarations: tuple[str, ...],
    *,
    error_factory: ErrorFactory,
) -> str:
    """Bind declared semantic roots, including their exact directory shape."""

    entries: dict[str, Path] = {}
    for declaration in declarations:
        path = root / declaration
        if not path.exists() or path.is_symlink():
            _raise(error_factory, f"semantic input is missing or unsafe: {declaration}")
        candidates = [path, *sorted(path.rglob("*"), key=lambda item: item.as_posix())]
        for candidate in candidates:
            if candidate.is_symlink() or not (
                candidate.is_file() or candidate.is_dir()
            ):
                _raise(error_factory, "semantic inputs contain an unsafe entry")
            entries[candidate.relative_to(root).as_posix()] = candidate
    digest = hashlib.sha256()
    for relative, path in sorted(entries.items()):
        kind = "directory" if path.is_dir() else "file"
        digest.update(f"{relative}\0{kind}\0".encode())
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _directory_handle(
    path: Path, *, create: bool, secure_final: bool, error_factory: ErrorFactory
) -> int:
    """Open a directory by walking every lexical component without symlinks."""

    if not path.is_absolute():
        _raise(error_factory, "artifact directory path must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path.anchor, flags)
    except OSError as error:
        raise error_factory(f"cannot open artifact filesystem root: {path}") from error
    try:
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            try:
                metadata = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise error_factory(
                        f"artifact directory is unavailable: {path}"
                    ) from None
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    metadata = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                except OSError as error:
                    raise error_factory(
                        f"cannot prepare private artifact directory: {path}"
                    ) from error
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                _raise(
                    error_factory,
                    "artifact directory is not private and owned; it resolves through "
                    f"a symlink or non-directory: {path}",
                )
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as error:
                raise error_factory(
                    f"cannot safely open artifact directory: {path}"
                ) from error
            opened = os.fstat(child)
            if (opened.st_dev, opened.st_ino, opened.st_mode) != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
            ):
                os.close(child)
                _raise(error_factory, f"artifact directory identity drift: {path}")
            os.close(descriptor)
            descriptor = child
            if final and secure_final:
                if opened.st_uid != os.getuid():
                    _raise(
                        error_factory,
                        f"artifact directory is not private and owned: {path}",
                    )
                if stat.S_IMODE(opened.st_mode) != 0o700:
                    try:
                        os.fchmod(descriptor, 0o700)
                    except OSError as error:
                        raise error_factory(
                            f"cannot secure private artifact directory: {path}"
                        ) from error
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def prepare_private_directory(path: Path, *, error_factory: ErrorFactory) -> None:
    descriptor = _directory_handle(
        path, create=True, secure_final=True, error_factory=error_factory
    )
    os.close(descriptor)


def fsync_directory(path: Path, *, error_factory: ErrorFactory) -> None:
    try:
        descriptor = _directory_handle(
            path, create=False, secure_final=False, error_factory=error_factory
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise error_factory(
            f"cannot durably synchronize artifact directory: {path}"
        ) from error


def private_atomic_write(
    path: Path, content: bytes, *, error_factory: ErrorFactory
) -> None:
    """Commit one private file with nofollow/exclusive creation and durable rename."""

    if not path.is_absolute():
        _raise(error_factory, "artifact output path must be absolute")
    temporary = f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    parent_descriptor = _directory_handle(
        path.parent, create=True, secure_final=True, error_factory=error_factory
    )
    replaced = False
    try:
        try:
            existing = os.stat(
                path.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode)
        ):
            _raise(error_factory, f"artifact output is a symlink or non-file: {path}")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(content)
            output.flush()
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        os.replace(
            temporary,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        replaced = True
        os.fsync(parent_descriptor)
    except OSError as error:
        raise error_factory(f"cannot durably write private artifact: {path}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not replaced:
            try:
                os.unlink(temporary, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        os.close(parent_descriptor)


def frozen_atomic_write(
    path: Path, content: bytes, *, error_factory: ErrorFactory
) -> None:
    """Persist immutable evidence once and reject any later byte drift."""

    parent_descriptor = _directory_handle(
        path.parent, create=True, secure_final=True, error_factory=error_factory
    )
    try:
        try:
            metadata = os.stat(
                path.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                _raise(error_factory, f"frozen evidence artifact drift: {path}")
            try:
                file_descriptor = os.open(
                    path.name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_descriptor,
                )
                with os.fdopen(file_descriptor, "rb") as source:
                    observed = source.read()
            except OSError as error:
                raise error_factory(
                    f"cannot read frozen evidence artifact: {path}"
                ) from error
            if observed != content:
                _raise(error_factory, f"frozen evidence artifact drift: {path}")
            return
    finally:
        os.close(parent_descriptor)
    private_atomic_write(path, content, error_factory=error_factory)


def frozen_no_replace_write(
    path: Path, content: bytes, *, error_factory: ErrorFactory
) -> None:
    """Atomically publish immutable evidence without replacing a raced pathname."""

    if not path.is_absolute():
        _raise(error_factory, "frozen evidence output path must be absolute")
    temporary = f".{path.name}.{uuid.uuid4().hex}.tmp"
    parent_descriptor = _directory_handle(
        path.parent, create=True, secure_final=True, error_factory=error_factory
    )
    temporary_descriptor: int | None = None
    linked = False
    try:

        def existing_matches() -> bool:
            try:
                metadata = os.stat(
                    path.name, dir_fd=parent_descriptor, follow_symlinks=False
                )
            except FileNotFoundError:
                return False
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                _raise(error_factory, f"frozen evidence artifact drift: {path}")
            try:
                descriptor = os.open(
                    path.name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_descriptor,
                )
                with os.fdopen(descriptor, "rb") as source:
                    observed = source.read()
            except OSError as error:
                raise error_factory(
                    f"cannot read frozen evidence artifact: {path}"
                ) from error
            if observed != content:
                _raise(error_factory, f"frozen evidence artifact drift: {path}")
            return True

        if existing_matches():
            return
        temporary_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        with os.fdopen(temporary_descriptor, "wb", closefd=False) as output:
            output.write(content)
            output.flush()
            os.fchmod(temporary_descriptor, 0o600)
            os.fsync(temporary_descriptor)
        try:
            os.link(
                temporary,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            linked = True
            os.fsync(parent_descriptor)
        except FileExistsError:
            if not existing_matches():
                _raise(error_factory, f"frozen evidence output race: {path}")
    except OSError as error:
        raise error_factory(f"cannot publish frozen evidence: {path}") from error
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if not linked:
            try:
                os.unlink(temporary, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        else:
            os.unlink(temporary, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
        os.close(parent_descriptor)


def read_strict_json(path: Path, *, label: str, error_factory: ErrorFactory) -> Any:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            _raise(error_factory, f"JSON artifact is not a regular file: {label}")
        content = path.read_bytes()
    except OSError as error:
        raise error_factory(f"cannot read {label}: {error}") from error
    return strict_json_bytes(content, label=label, error_factory=error_factory)
