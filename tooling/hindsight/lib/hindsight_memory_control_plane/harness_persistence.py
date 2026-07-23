"""Private filesystem persistence for production harness activation surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any

from .canonical import DIGEST, canonical_bytes, digest
from .file_evidence import FileEvidenceError, open_trusted_parent
from .model import deep_thaw


class HarnessPersistenceError(RuntimeError):
    """A harness destination cannot be read or changed safely."""


_HARNESS_IDS = frozenset({"codex", "claude-code", "cursor"})
_MAX_DOCUMENT_BYTES = 1024 * 1024
_MAX_CONFIGURATION_BYTES = 4 * 1024 * 1024
_MAX_JOURNAL_BYTES = 8 * 1024 * 1024
_CONFIG_KEYS = frozenset(
    {
        "schema_version",
        "harness_id",
        "hooks_path",
        "settings_path",
        "tools_path",
        "rollback_root",
    }
)


def _absolute(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise HarnessPersistenceError(f"{label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise HarnessPersistenceError(f"{label} must be an absolute path")
    return path


def _private_directory(path: Path) -> None:
    try:
        descriptor = open_trusted_parent(
            path,
            unavailable_message="rollback root is unavailable",
            not_directory_message="rollback root is unavailable",
            owner_message="rollback root must be a private directory",
            writable_message="rollback root must be a private directory",
            create_missing=False,
        )
        metadata = os.fstat(descriptor)
    except (FileEvidenceError, OSError) as error:
        raise HarnessPersistenceError("rollback root is unavailable") from error
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise HarnessPersistenceError("rollback root must be a private directory")


def _open_parent(path: Path) -> int:
    try:
        return open_trusted_parent(
            path.parent,
            unavailable_message="harness destination parent is unavailable",
            not_directory_message="harness destination parent is unavailable",
            owner_message="harness destination parent is not protected",
            writable_message="harness destination parent is not protected",
            create_missing=False,
        )
    except (FileEvidenceError, OSError) as error:
        raise HarnessPersistenceError(
            "harness destination parent is unavailable"
        ) from error


def _read_document(
    path: Path,
    *,
    missing: bool = False,
    max_bytes: int = _MAX_DOCUMENT_BYTES,
) -> dict[str, Any]:
    parent = _open_parent(path)
    try:
        metadata = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        os.close(parent)
        if missing:
            return {}
        raise HarnessPersistenceError("harness destination is unavailable") from None
    except OSError as error:
        os.close(parent)
        raise HarnessPersistenceError("harness destination is unavailable") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_nlink != 1
        or metadata.st_size > max_bytes
    ):
        os.close(parent)
        raise HarnessPersistenceError(
            "harness destination must be a protected regular file"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent)
    except OSError as error:
        os.close(parent)
        raise HarnessPersistenceError("harness destination is unavailable") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise HarnessPersistenceError("harness destination changed while opening")
        payload = bytearray()
        while len(payload) <= max_bytes:
            chunk = os.read(descriptor, min(65536, max_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
    finally:
        os.close(descriptor)
        os.close(parent)
    if len(payload) > max_bytes:
        raise HarnessPersistenceError("harness destination is too large")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarnessPersistenceError("harness destination is invalid JSON") from error
    if not isinstance(value, dict):
        raise HarnessPersistenceError("harness destination must be a JSON object")
    return value


def _atomic_write(
    path: Path,
    value: Mapping[str, Any],
    *,
    mode: int = 0o600,
    max_bytes: int = _MAX_DOCUMENT_BYTES,
) -> None:
    parent = _open_parent(path)
    temporary = f".{path.name}.hindsight-{os.getpid()}-{secrets.token_hex(8)}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(temporary, flags, mode, dir_fd=parent)
        try:
            payload = canonical_bytes(value)
            if len(payload) > max_bytes:
                raise HarnessPersistenceError("harness destination is too large")
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        except Exception:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass
            raise
        finally:
            os.close(descriptor)
        try:
            os.replace(
                temporary,
                path.name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
            )
            os.fsync(parent)
        except Exception:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass
            raise
    finally:
        os.close(parent)


def _durable_unlink(path: Path) -> None:
    parent = _open_parent(path)
    try:
        os.unlink(path.name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def _entry_exists(path: Path) -> bool:
    parent = _open_parent(path)
    try:
        os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    finally:
        os.close(parent)


def _remove_orphaned_temporaries(path: Path) -> None:
    """Best-effort removal of private atomic-write remnants."""

    parent = -1
    try:
        parent = _open_parent(path)
        prefix = f".{path.name}.hindsight-"
        for name in os.listdir(parent):
            if not name.startswith(prefix):
                continue
            metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                stat.S_ISREG(metadata.st_mode)
                and metadata.st_uid == os.geteuid()
                and metadata.st_nlink == 1
            ):
                os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    except (HarnessPersistenceError, OSError):
        pass
    finally:
        if parent >= 0:
            os.close(parent)


@dataclass(frozen=True)
class NativeHarnessDestination:
    """Map one abstract native artifact onto real user configuration files."""

    harness_id: str
    hooks_path: Path
    settings_path: Path
    tools_path: Path
    rollback_root: Path

    @classmethod
    def load(cls, value: Mapping[str, Any]) -> "NativeHarnessDestination":
        if not isinstance(value, Mapping) or set(value) != _CONFIG_KEYS:
            raise HarnessPersistenceError("harness destination schema is closed")
        if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise HarnessPersistenceError(
                "harness destination schema_version must be 1"
            )
        harness_id = value.get("harness_id")
        if harness_id not in _HARNESS_IDS:
            raise HarnessPersistenceError("unsupported harness destination")
        paths = tuple(
            _absolute(value[key], key)
            for key in ("hooks_path", "settings_path", "tools_path", "rollback_root")
        )
        if len(set(paths)) != len(paths):
            raise HarnessPersistenceError("harness destination paths must be distinct")
        _private_directory(paths[3])
        return cls(harness_id, *paths)

    @property
    def _journal_path(self) -> Path:
        return self.rollback_root / f"{self.harness_id}.transaction.json"

    def to_record(self) -> dict[str, Any]:
        """Return the closed, secret-free destination identity."""

        return {
            "schema_version": 1,
            "harness_id": self.harness_id,
            "hooks_path": str(self.hooks_path),
            "settings_path": str(self.settings_path),
            "tools_path": str(self.tools_path),
            "rollback_root": str(self.rollback_root),
        }

    @property
    def destination_digest(self) -> str:
        return digest(self.to_record())

    @contextmanager
    def _locked(self):
        lock_path = self.rollback_root / f"{self.harness_id}.lock"
        parent = _open_parent(lock_path)
        try:
            descriptor = os.open(
                lock_path.name,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent,
            )
        finally:
            os.close(parent)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise HarnessPersistenceError("harness destination lock is unsafe")
            # Lock-holding code must use private helpers; public methods reacquire
            # this lock through a fresh descriptor and can self-deadlock.
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _documents(self) -> dict[str, dict[str, Any]]:
        return {
            "hooks": _read_document(self.hooks_path),
            "settings": _read_document(self.settings_path),
            "tools": _read_document(self.tools_path, missing=True),
        }

    def _configuration_from_documents(
        self, documents: Mapping[str, Mapping[str, Any]]
    ) -> dict[str, Any]:
        hooks_document = deep_thaw(documents["hooks"])
        hooks = (
            {"hooks": deep_thaw(hooks_document.get("hooks", {}))}
            if self.harness_id == "claude-code"
            else hooks_document
        )
        return {
            "hooks": hooks,
            "settings": deep_thaw(documents["settings"]),
            "tools": deep_thaw(documents["tools"]),
        }

    def _documents_from_configuration(
        self,
        configuration: Mapping[str, Any],
        current_documents: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(configuration, Mapping) or set(configuration) != {
            "hooks",
            "settings",
            "tools",
        }:
            raise HarnessPersistenceError("native harness configuration is invalid")
        if not all(isinstance(configuration[key], Mapping) for key in configuration):
            raise HarnessPersistenceError("native harness surfaces must be objects")
        hooks = deep_thaw(configuration["hooks"])
        if self.harness_id == "claude-code":
            if set(hooks) != {"hooks"} or not isinstance(hooks["hooks"], Mapping):
                raise HarnessPersistenceError("Claude hook surface is invalid")
            hooks_document = deep_thaw(current_documents["hooks"])
            hooks_document["hooks"] = deep_thaw(hooks["hooks"])
        else:
            hooks_document = hooks
        return {
            "hooks": hooks_document,
            "settings": deep_thaw(configuration["settings"]),
            "tools": deep_thaw(configuration["tools"]),
        }

    def _recover(self) -> None:
        for path in (
            self.hooks_path,
            self.settings_path,
            self.tools_path,
            self._journal_path,
        ):
            _remove_orphaned_temporaries(path)
        if not _entry_exists(self._journal_path):
            return
        journal = _read_document(self._journal_path, max_bytes=_MAX_JOURNAL_BYTES)
        if (
            set(journal) != {"schema_version", "phase", "original", "target"}
            or type(journal.get("schema_version")) is not int
            or journal["schema_version"] != 1
            or journal.get("phase") not in {"prepared", "committed"}
            or not isinstance(journal.get("original"), Mapping)
            or not isinstance(journal.get("target"), Mapping)
        ):
            raise HarnessPersistenceError("harness transaction journal is invalid")
        selected = (
            journal["original"] if journal["phase"] == "prepared" else journal["target"]
        )
        self._write_documents(selected)
        _durable_unlink(self._journal_path)

    def _write_documents(self, documents: Mapping[str, Mapping[str, Any]]) -> None:
        if set(documents) != {"hooks", "settings", "tools"}:
            raise HarnessPersistenceError("harness transaction documents are invalid")
        for name, path in (
            ("hooks", self.hooks_path),
            ("settings", self.settings_path),
            ("tools", self.tools_path),
        ):
            value = documents[name]
            if not isinstance(value, Mapping):
                raise HarnessPersistenceError("harness transaction document is invalid")
            _atomic_write(path, value)

    def read_configuration(self) -> dict[str, Any]:
        with self._locked():
            self._recover()
            return self._configuration_from_documents(self._documents())

    def write_configuration(
        self, expected_configuration_digest: str, configuration: Mapping[str, Any]
    ) -> dict[str, Any]:
        with self._locked():
            self._recover()
            current_documents = self._documents()
            current = self._configuration_from_documents(current_documents)
            if digest(current) != expected_configuration_digest:
                raise HarnessPersistenceError(
                    "harness destination changed before write"
                )
            target_documents = self._documents_from_configuration(
                configuration, current_documents
            )
            journal = {
                "schema_version": 1,
                "phase": "prepared",
                "original": current_documents,
                "target": target_documents,
            }
            _atomic_write(self._journal_path, journal, max_bytes=_MAX_JOURNAL_BYTES)
            try:
                self._write_documents(target_documents)
                journal["phase"] = "committed"
                _atomic_write(self._journal_path, journal, max_bytes=_MAX_JOURNAL_BYTES)
                _durable_unlink(self._journal_path)
            except Exception:
                self._recover()
                raise
            return self._configuration_from_documents(self._documents())

    def persist_rollback(self, configuration: Mapping[str, Any]) -> Path:
        if not isinstance(configuration, Mapping):
            raise HarnessPersistenceError("rollback configuration is invalid")
        path = self.rollback_root / f"{self.harness_id}.{digest(configuration)}.json"
        with self._locked():
            if _entry_exists(path):
                if (
                    _read_document(path, max_bytes=_MAX_CONFIGURATION_BYTES)
                    != configuration
                ):
                    raise HarnessPersistenceError(
                        "rollback configuration digest collision"
                    )
                return path
            _atomic_write(path, configuration, max_bytes=_MAX_CONFIGURATION_BYTES)
            return path

    def read_rollback(self, configuration_digest: str) -> dict[str, Any]:
        if (
            not isinstance(configuration_digest, str)
            or DIGEST.fullmatch(configuration_digest) is None
        ):
            raise HarnessPersistenceError("rollback digest is invalid")
        with self._locked():
            return _read_document(
                self.rollback_root / f"{self.harness_id}.{configuration_digest}.json",
                max_bytes=_MAX_CONFIGURATION_BYTES,
            )
