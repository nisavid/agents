from __future__ import annotations

import errno
import fcntl
import json
import os
import select
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Self

from . import test_receipt_staging as receipt_staging
from ._support import canonical_document, load_deployment_module, sha256

INSTALL_PROCESS_LOSS_CUTS = (
    "temp-create",
    "partial-write",
    "file-fsync",
    "link-finalization",
    "temp-unlink",
    "directory-fsync",
)
_INSTALL_PROCESS_LOSS_EXIT = 86
DIRECTORY_PROCESS_LOSS_CUTS = (
    "mkdir-return",
    "mode-normalization",
    "directory-fsync",
    "parent-fsync",
)
_DIRECTORY_PROCESS_LOSS_EXIT = 91
_REAL_SMOKE_OUTPUT_SCRIPT = r"""
import os
import signal
import sys
import time


def write_all(descriptor, raw):
    offset = 0
    while offset < len(raw):
        written = os.write(descriptor, raw[offset:])
        if written <= 0:
            raise RuntimeError("child output write made no progress")
        offset += written


pid_path = sys.argv[1]
stdout_bytes = int(sys.argv[2])
stderr_bytes = int(sys.argv[3])
linger_seconds = float(sys.argv[4])
descendant_pid_path = sys.argv[5]
ignore_sigterm = sys.argv[6] == "1"
if ignore_sigterm:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def write_pid(path, pid):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        write_all(descriptor, f"{pid}\n".encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


write_pid(pid_path, os.getpid())
if descendant_pid_path != "-":
    descendant = os.fork()
    if descendant == 0:
        time.sleep(linger_seconds)
        os._exit(0)
    write_pid(descendant_pid_path, descendant)
chunk = b"x" * 65536
for descriptor, total in ((1, stdout_bytes), (2, stderr_bytes)):
    remaining = total
    while remaining:
        emitted = chunk[:remaining]
        write_all(descriptor, emitted)
        remaining -= len(emitted)
time.sleep(linger_seconds)
"""


class InjectedActivationCrash(BaseException):
    """A test-only process-loss cut that bypasses ordinary error recovery."""


@dataclass(frozen=True)
class PreparedActivation:
    request: object
    authorization_raw: bytes
    staged: object
    verified: object
    canonical_root: Path
    activation_lock: Path


class FirstInstallActivationFixture:
    """Create one real TW2 stage for the public TW3 activation seam."""

    task_witness_candidate_inputs = (
        receipt_staging.ReceiptStagingTests.task_witness_candidate_inputs
    )
    first_install_request = receipt_staging.ReceiptStagingTests.first_install_request
    first_install_authorization_raw = (
        receipt_staging.ReceiptStagingTests.first_install_authorization_raw
    )
    runtime_qualification_raw = (
        receipt_staging.ReceiptStagingTests.runtime_qualification_raw
    )

    def __init__(self, root: Path) -> None:
        self.root = root

    def deployment(self) -> ModuleType:
        return load_deployment_module()

    def prepare(self) -> PreparedActivation:
        deployment = self.deployment()
        account_home = self.root / "account-home"
        canonical_root = account_home / ".local" / "libexec" / "task-witness"
        canonical_root.mkdir(parents=True, mode=0o700)
        for directory in (
            account_home,
            account_home / ".local",
            account_home / ".local" / "libexec",
            canonical_root,
        ):
            directory.chmod(0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)
        staged = deployment.stage_first_install(
            request,
            authorization_raw,
            self.root / "stage",
        )
        verified = deployment.verify_deployment_stage(staged.stage_path)
        return PreparedActivation(
            request=request,
            authorization_raw=authorization_raw,
            staged=staged,
            verified=verified,
            canonical_root=canonical_root,
            activation_lock=activation_lock,
        )

    def activation_request(self, prepared: PreparedActivation) -> object:
        deployment = self.deployment()
        return deployment.ActivationRequest(
            deployment=prepared.request,
            authorization_raw=prepared.authorization_raw,
            stage_receipt=prepared.staged.stage_path,
        )


def canonical_value(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw)
    if type(value) is not dict or canonical_document(value) != raw:
        raise AssertionError("expected one canonical JSON document")
    return value


def thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def tree_inventory(root: Path) -> tuple[tuple[str, str, int, int, str], ...]:
    inventory: list[tuple[str, str, int, int, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            inventory.append(
                (relative, "directory", stat.S_IMODE(metadata.st_mode), 0, "")
            )
        elif stat.S_ISREG(metadata.st_mode):
            raw = path.read_bytes()
            inventory.append(
                (
                    relative,
                    "file",
                    stat.S_IMODE(metadata.st_mode),
                    len(raw),
                    sha256(raw),
                )
            )
        else:
            inventory.append((relative, "other", stat.S_IMODE(metadata.st_mode), 0, ""))
    return tuple(inventory)


def exact_absent_inventory(
    activation_lock: Path,
) -> tuple[tuple[str, str, int, int, str], ...]:
    return ((activation_lock.name, "file", 0o600, 0, sha256(b"")),)


def exact_retained_result_inventory(
    result: object,
) -> tuple[tuple[str, str, int, int, str], ...]:
    raw = result.journal_raw
    transaction_id = result.transaction_id
    return (
        ("transaction-results", "directory", 0o700, 0, ""),
        (
            f"transaction-results/sha256-{transaction_id}.json",
            "file",
            0o600,
            len(raw),
            sha256(raw),
        ),
    )


def exact_restored_absent_inventory(
    activation_lock: Path,
    result: object,
) -> tuple[tuple[str, str, int, int, str], ...]:
    return tuple(
        sorted(
            (
                *exact_absent_inventory(activation_lock),
                *exact_retained_result_inventory(result),
            )
        )
    )


def real_smoke_output_command(
    pid_path: Path,
    *,
    stdout_bytes: int,
    stderr_bytes: int,
    linger_seconds: float = 0,
    descendant_pid_path: Path | None = None,
    ignore_sigterm: bool = False,
) -> tuple[str, ...]:
    return (
        sys.executable,
        "-I",
        "-S",
        "-c",
        _REAL_SMOKE_OUTPUT_SCRIPT,
        str(pid_path),
        str(stdout_bytes),
        str(stderr_bytes),
        str(linger_seconds),
        "-" if descendant_pid_path is None else str(descendant_pid_path),
        "1" if ignore_sigterm else "0",
    )


def assert_process_reaped(pid_path: Path) -> None:
    pid = int(pid_path.read_text(encoding="ascii").strip())
    deadline = time.monotonic() + 1
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"smoke child {pid} still exists after supervisor return"
            )
        time.sleep(0.01)


def filesystem_identity(path: Path) -> tuple[int, ...]:
    metadata = path.lstat()
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def process_descriptor_inventory() -> tuple[tuple[int, tuple[int, ...], int], ...]:
    inventory: list[tuple[int, tuple[int, ...], int]] = []
    for name in os.listdir("/dev/fd"):
        try:
            descriptor = int(name)
            metadata = os.fstat(descriptor)
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        except (OSError, ValueError):
            continue
        inventory.append(
            (
                descriptor,
                (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_mode,
                    metadata.st_uid,
                    metadata.st_nlink,
                ),
                flags,
            )
        )
    return tuple(sorted(inventory))


class IndependentActivationLockHolder:
    """Hold one canonical activation lock from an independent process."""

    def __init__(
        self,
        path: Path,
        operation: int,
        *,
        hold_seconds: float | None,
    ) -> None:
        if operation not in {fcntl.LOCK_SH, fcntl.LOCK_EX}:
            raise AssertionError("independent lock operation is unsupported")
        self.path = path
        self.operation = operation
        self.hold_seconds = hold_seconds
        self.pid = -1
        self._release_fd = -1

    def __enter__(self) -> Self:
        ready_read, ready_write = os.pipe()
        release_read, release_write = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(ready_read)
            os.close(release_write)
            descriptor = -1
            try:
                descriptor = os.open(
                    self.path,
                    os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                )
                fcntl.flock(descriptor, self.operation)
                os.write(ready_write, b"1")
                ready, _, _ = select.select(
                    [release_read],
                    [],
                    [],
                    self.hold_seconds,
                )
                if ready:
                    os.read(release_read, 1)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except (OSError, ValueError):
                os._exit(91)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                os.close(ready_write)
                os.close(release_read)
            os._exit(0)
        os.close(ready_write)
        os.close(release_read)
        self.pid = child
        self._release_fd = release_write
        ready, _, _ = select.select([ready_read], [], [], 2)
        try:
            if not ready or os.read(ready_read, 1) != b"1":
                raise AssertionError("independent activation lock holder was not ready")
        finally:
            os.close(ready_read)
        return self

    def release(self) -> None:
        if self._release_fd < 0:
            return
        try:
            os.write(self._release_fd, b"1")
        except BrokenPipeError:
            pass
        finally:
            os.close(self._release_fd)
            self._release_fd = -1

    def __exit__(self, *exc: object) -> None:
        del exc
        self.release()
        waited, status = os.waitpid(self.pid, 0)
        if waited != self.pid or not os.WIFEXITED(status):
            raise AssertionError("independent activation lock holder did not exit")
        if os.WEXITSTATUS(status) != 0:
            raise AssertionError(
                "independent activation lock holder exited unsuccessfully"
            )


def close_locked_activation_root(result: tuple[object, int]) -> None:
    root, lock_fd = result
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)
        os.close(root.fd)


def ordered_activation_artifacts(prepared: PreparedActivation) -> list[object]:
    artifacts = list(prepared.verified.artifacts)
    return sorted(
        (artifact for artifact in artifacts if artifact.role != "shim"),
        key=lambda artifact: artifact.relative_path,
    ) + [next(artifact for artifact in artifacts if artifact.role == "shim")]


def activation_install_temporary_path(
    artifact: object,
    transaction_id: str,
    step_index: int,
) -> Path:
    return artifact.installed_path.parent / (
        f".task-witness-install-{transaction_id}-{step_index}.tmp"
    )


def replay_pending_install(
    deployment: ModuleType,
    prepared: PreparedActivation,
    journal: Mapping[str, Any],
) -> object:
    pending = journal["pending_step"]
    if not isinstance(pending, Mapping) or pending.get("operation") != "install":
        raise AssertionError("activation journal lacks one pending install step")
    artifact = ordered_activation_artifacts(prepared)[pending["index"]]
    if artifact.role != pending["role"]:
        raise AssertionError("pending install cursor does not identify its artifact")
    lock_fd = os.open(
        prepared.activation_lock,
        os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    root_fd = os.open(
        prepared.canonical_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        deployment._install_activation_artifact(
            root_fd,
            prepared.canonical_root,
            artifact,
            transaction_id=journal["transaction_id"],
            step_index=pending["index"],
        )
    finally:
        os.close(root_fd)
        os.close(lock_fd)
    return artifact


def run_activation_install_process_loss_cut(
    deployment: ModuleType,
    request: object,
    cut: str,
    *,
    artifact_index: int = 0,
) -> None:
    if cut not in INSTALL_PROCESS_LOSS_CUTS:
        raise AssertionError(f"unknown activation install cut: {cut}")
    if type(artifact_index) is not int or artifact_index < 0:
        raise AssertionError("activation install artifact index is invalid")
    child = os.fork()
    if child == 0:
        original_open = deployment.os.open
        original_write = deployment.os.write
        original_fsync = deployment.os.fsync
        original_fstat = deployment.os.fstat
        original_link = deployment.os.link
        original_unlink = deployment.os.unlink
        original_write_all = deployment._write_all
        state: dict[str, Any] = {
            "temp_fd": None,
            "temp_name": None,
            "temp_parent_identity": None,
            "temp_fsyncs": 0,
            "temp_unlinked": False,
        }

        def process_loss() -> None:
            os._exit(_INSTALL_PROCESS_LOSS_EXIT)

        def observed_open(*args: object, **kwargs: object) -> int:
            descriptor = original_open(*args, **kwargs)
            name = args[0] if args else kwargs.get("path")
            if (
                isinstance(name, str)
                and name.startswith(".task-witness-install-")
                and name.endswith(f"-{artifact_index}.tmp")
            ):
                state["temp_fd"] = descriptor
                state["temp_name"] = name
                parent_fd = kwargs.get("dir_fd")
                if type(parent_fd) is not int:
                    os._exit(90)
                parent = original_fstat(parent_fd)
                state["temp_parent_identity"] = (
                    parent.st_dev,
                    parent.st_ino,
                )
                if cut == "temp-create":
                    process_loss()
            return descriptor

        def observed_write_all(descriptor: int, raw: bytes, label: str) -> None:
            if descriptor == state["temp_fd"]:
                if cut == "partial-write":
                    partial = max(1, len(raw) // 2)
                    offset = 0
                    while offset < partial:
                        written = original_write(descriptor, raw[offset:partial])
                        if written <= 0:
                            os._exit(90)
                        offset += written
                    process_loss()
                original_write_all(descriptor, raw, label)
                return
            original_write_all(descriptor, raw, label)

        def observed_fsync(descriptor: int) -> None:
            original_fsync(descriptor)
            if descriptor == state["temp_fd"]:
                state["temp_fsyncs"] += 1
                if cut == "file-fsync" and state["temp_fsyncs"] == 2:
                    process_loss()
            if cut == "directory-fsync" and state["temp_unlinked"]:
                synchronized = original_fstat(descriptor)
                if (
                    synchronized.st_dev,
                    synchronized.st_ino,
                ) == state["temp_parent_identity"]:
                    process_loss()

        def observed_link(*args: object, **kwargs: object) -> None:
            original_link(*args, **kwargs)
            name = args[0] if args else kwargs.get("src")
            if cut == "link-finalization" and name == state["temp_name"]:
                process_loss()

        def observed_unlink(*args: object, **kwargs: object) -> None:
            original_unlink(*args, **kwargs)
            name = args[0] if args else kwargs.get("path")
            if name == state["temp_name"]:
                state["temp_unlinked"] = True
                if cut == "temp-unlink":
                    process_loss()

        deployment.os.open = observed_open
        deployment.os.fsync = observed_fsync
        deployment.os.link = observed_link
        deployment.os.unlink = observed_unlink
        deployment._write_all = observed_write_all

        def unexpected_smoke(*args: object, **kwargs: object) -> None:
            del args, kwargs
            os._exit(87)

        deployment._spawn_activation_smoke_child = unexpected_smoke
        try:
            deployment.activate_staged(request)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(88)
        os._exit(89)

    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError(f"activation install cut {cut} did not exit normally")
    exit_status = os.WEXITSTATUS(status)
    if exit_status != _INSTALL_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"activation install cut {cut} exited at unexpected boundary {exit_status}"
        )


def run_activation_directory_process_loss_cut(
    deployment: ModuleType,
    request: object,
    prepared: object,
    cut: str,
) -> None:
    if cut not in DIRECTORY_PROCESS_LOSS_CUTS:
        raise AssertionError(f"unknown activation directory cut: {cut}")
    artifact = ordered_activation_artifacts(prepared)[1]
    if artifact.role != "client" or Path(artifact.relative_path).parent.parts != (
        "client",
    ):
        raise AssertionError("activation directory cut requires index-1 client parent")
    canonical_root = prepared.canonical_root
    root_metadata = canonical_root.lstat()
    root_identity = (root_metadata.st_dev, root_metadata.st_ino)
    child = os.fork()
    if child == 0:
        original_mkdir = deployment.os.mkdir
        original_chmod = deployment.os.chmod
        original_open = deployment.os.open
        original_fsync = deployment.os.fsync
        original_fstat = deployment.os.fstat
        state: dict[str, Any] = {
            "directory_identity": None,
            "directory_synced": False,
        }

        def process_loss() -> None:
            os._exit(_DIRECTORY_PROCESS_LOSS_EXIT)

        def is_target(name: object, parent_fd: object) -> bool:
            if name != "client" or type(parent_fd) is not int:
                return False
            parent = original_fstat(parent_fd)
            return (parent.st_dev, parent.st_ino) == root_identity

        def observed_mkdir(*args: object, **kwargs: object) -> None:
            original_mkdir(*args, **kwargs)
            name = args[0] if args else kwargs.get("path")
            parent_fd = kwargs.get("dir_fd")
            if is_target(name, parent_fd):
                metadata = deployment.os.stat(
                    "client",
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                state["directory_identity"] = (
                    metadata.st_dev,
                    metadata.st_ino,
                )
                if cut == "mkdir-return":
                    process_loss()

        def observed_chmod(*args: object, **kwargs: object) -> None:
            original_chmod(*args, **kwargs)
            name = args[0] if args else kwargs.get("path")
            mode = args[1] if len(args) > 1 else kwargs.get("mode")
            parent_fd = kwargs.get("dir_fd")
            if (
                is_target(name, parent_fd)
                and mode == 0o700
                and cut == "mode-normalization"
            ):
                process_loss()

        def observed_open(*args: object, **kwargs: object) -> int:
            descriptor = original_open(*args, **kwargs)
            name = args[0] if args else kwargs.get("path")
            parent_fd = kwargs.get("dir_fd")
            if is_target(name, parent_fd):
                metadata = original_fstat(descriptor)
                state["directory_identity"] = (
                    metadata.st_dev,
                    metadata.st_ino,
                )
            return descriptor

        def observed_fsync(descriptor: int) -> None:
            original_fsync(descriptor)
            metadata = original_fstat(descriptor)
            identity = (metadata.st_dev, metadata.st_ino)
            if identity == state["directory_identity"]:
                state["directory_synced"] = True
                if cut == "directory-fsync":
                    process_loss()
                return
            if (
                state["directory_synced"]
                and identity == root_identity
                and cut == "parent-fsync"
            ):
                process_loss()

        deployment.os.mkdir = observed_mkdir
        deployment.os.chmod = observed_chmod
        deployment.os.open = observed_open
        deployment.os.fsync = observed_fsync

        def unexpected_smoke(*args: object, **kwargs: object) -> None:
            del args, kwargs
            os._exit(92)

        deployment._spawn_activation_smoke_child = unexpected_smoke
        os.umask(0o777)
        try:
            deployment.activate_staged(request)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(93)
        os._exit(94)

    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError(f"activation directory cut {cut} did not exit normally")
    exit_status = os.WEXITSTATUS(status)
    if exit_status != _DIRECTORY_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"activation directory cut {cut} exited at unexpected boundary "
            f"{exit_status}"
        )


def assert_exclusive_lock(activation_lock: Path) -> None:
    probe = os.open(
        activation_lock,
        os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        try:
            fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in {errno.EAGAIN, errno.EWOULDBLOCK}:
                raise
        else:
            fcntl.flock(probe, fcntl.LOCK_UN)
            raise AssertionError("activation boundary ran without exclusive lock")
    finally:
        os.close(probe)


def expected_smoke_envelope(staged: object) -> bytes:
    smoke = staged.deployment_value["smoke"]
    envelope = {
        "contract": "task-witness-launch-envelope-v1",
        "anchor": thaw_json(smoke["expected_anchor"]),
        "witness": {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": smoke["bundle"]["sha256"],
            "producer": thaw_json(smoke["producer"]),
            "validator": thaw_json(smoke["validator"]),
            "projection": thaw_json(smoke["expected_projection"]),
            "trust_context_sha256": smoke["trust_context"]["sha256"],
            "historical": False,
        },
    }
    raw = canonical_document(envelope)
    if sha256(raw) != smoke["expected_envelope_sha256"]:
        raise AssertionError("staged smoke envelope binding disagrees")
    return raw


class ActivationCutRecorder:
    """Wrap real persistence seams; never add a test-only production hook."""

    def __init__(
        self,
        canonical_root: Path,
        *,
        crash_before_first_artifact: bool = False,
        crash_after_first_artifact: bool = False,
    ) -> None:
        self.canonical_root = canonical_root
        self.crash_before_first_artifact = crash_before_first_artifact
        self.crash_after_first_artifact = crash_after_first_artifact
        self.events: list[str] = []
        self.snapshots: dict[str, tuple[tuple[str, str, int, int, str], ...]] = {}
        self.journals: list[dict[str, Any]] = []

    def _capture(self, event: str) -> None:
        self.events.append(event)
        self.snapshots[event] = tree_inventory(self.canonical_root)

    def wrap_journal_writer(
        self, original: Callable[..., object]
    ) -> Callable[..., object]:
        def wrapped(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            transaction = canonical_value(
                (self.canonical_root / "transaction.json").read_bytes()
            )
            self.journals.append(transaction)
            self._capture(f"journal:{transaction['phase']}:durable")
            return result

        return wrapped

    def wrap_artifact_installer(
        self,
        original: Callable[..., object],
    ) -> Callable[..., object]:
        def wrapped(*args: object, **kwargs: object) -> object:
            values = (*args, *kwargs.values())
            roles = [
                value.role
                for value in values
                if isinstance(getattr(value, "role", None), str)
            ]
            if len(roles) != 1:
                raise AssertionError("activation artifact boundary lacks one role")
            event = f"artifact:{roles[0]}:before"
            self._capture(event)
            if self.crash_before_first_artifact:
                self.crash_before_first_artifact = False
                raise InjectedActivationCrash(event)
            result = original(*args, **kwargs)
            event = f"artifact:{roles[0]}:after"
            self._capture(event)
            if self.crash_after_first_artifact:
                self.crash_after_first_artifact = False
                raise InjectedActivationCrash(event)
            return result

        return wrapped

    def wrap_journal_unlinker(
        self,
        original: Callable[..., object],
    ) -> Callable[..., object]:
        def wrapped(*args: object, **kwargs: object) -> object:
            self._capture("journal:before-unlink")
            result = original(*args, **kwargs)
            self._capture("journal:unlinked")
            return result

        return wrapped

    def __call__(self, event: str) -> None:
        """Remain directly useful as a test-side callback for future adapters."""

        if type(event) is not str:
            raise AssertionError("activation cut name must be text")
        self._capture(event)
        if (
            self.crash_before_first_artifact
            and event.startswith("artifact:")
            and event.endswith(":before")
        ):
            self.crash_before_first_artifact = False
            raise InjectedActivationCrash(event)


class ExclusiveLockBoundary:
    """Wrap one real rebinding function and prove it runs under the lock."""

    def __init__(self, activation_lock: Path, original: Callable[..., object]) -> None:
        self.activation_lock = activation_lock
        self.original = original
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> object:
        assert_exclusive_lock(self.activation_lock)
        self.calls += 1
        return self.original(*args, **kwargs)


class CallerFd3Binding:
    """Install a benign caller-owned FD 3 and restore prior process state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._original_dup = os.dup
        self._original_present = False
        self._original_flags = 0
        self._original_backup = -1
        self.expected_identity: tuple[int, ...] | None = None
        self.expected_flags = 0
        self.dup_attempts = 0

    @staticmethod
    def _identity(descriptor: int) -> tuple[int, ...]:
        metadata = os.fstat(descriptor)
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_nlink,
            metadata.st_size,
        )

    def __enter__(self) -> Self:
        try:
            self._original_flags = fcntl.fcntl(3, fcntl.F_GETFD)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
        else:
            self._original_present = True
            self._original_backup = self._original_dup(3)
        source = os.open(self.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        if source != 3:
            try:
                os.dup2(source, 3, inheritable=False)
            finally:
                os.close(source)
        else:
            fcntl.fcntl(3, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
        self.expected_identity = self._identity(3)
        self.expected_flags = fcntl.fcntl(3, fcntl.F_GETFD)
        return self

    def fail_backup_dup(self, descriptor: int) -> int:
        if descriptor != 3:
            return self._original_dup(descriptor)
        self.dup_attempts += 1
        raise OSError(errno.EMFILE, "injected caller FD 3 backup exhaustion")

    def assert_unchanged(self) -> None:
        if self.expected_identity is None:
            raise AssertionError("caller FD 3 binding was not entered")
        if self._identity(3) != self.expected_identity:
            raise AssertionError("caller FD 3 identity changed")
        if fcntl.fcntl(3, fcntl.F_GETFD) != self.expected_flags:
            raise AssertionError("caller FD 3 flags changed")

    def __exit__(self, *exc: object) -> None:
        del exc
        try:
            if self._original_present:
                os.dup2(
                    self._original_backup,
                    3,
                    inheritable=not bool(self._original_flags & fcntl.FD_CLOEXEC),
                )
                fcntl.fcntl(3, fcntl.F_SETFD, self._original_flags)
            else:
                try:
                    os.close(3)
                except OSError as error:
                    if error.errno != errno.EBADF:
                        raise
        finally:
            if self._original_backup >= 0:
                os.close(self._original_backup)


class AbsentFd3Binding:
    """Make FD 3 absent temporarily and restore the caller's prior state."""

    def __init__(self) -> None:
        self._original_dup = os.dup
        self._original_present = False
        self._original_flags = 0
        self._original_backup = -1

    def __enter__(self) -> Self:
        try:
            self._original_flags = fcntl.fcntl(3, fcntl.F_GETFD)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
        else:
            self._original_present = True
            self._original_backup = self._original_dup(3)
            os.close(3)
        self.assert_absent()
        return self

    @staticmethod
    def assert_absent() -> None:
        try:
            fcntl.fcntl(3, fcntl.F_GETFD)
        except OSError as error:
            if error.errno == errno.EBADF:
                return
            raise
        raise AssertionError("FD 3 unexpectedly exists")

    def __exit__(self, *exc: object) -> None:
        del exc
        if self._original_present:
            try:
                os.dup2(
                    self._original_backup,
                    3,
                    inheritable=not bool(self._original_flags & fcntl.FD_CLOEXEC),
                )
                fcntl.fcntl(3, fcntl.F_SETFD, self._original_flags)
            finally:
                os.close(self._original_backup)


class DirectSmokeChildBoundary:
    """Prove direct controller smoke receives only the intended lock on FD 3."""

    def __init__(self, canonical_root: Path, activation_lock: Path) -> None:
        self.canonical_root = canonical_root
        self.activation_lock = activation_lock
        self.calls = 0

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        pass_fds: tuple[int, ...],
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls += 1
        if argv != (str(self.canonical_root / "task-witness"), "activation-smoke"):
            raise AssertionError("direct activation smoke argv disagrees")
        if pass_fds != (3,):
            raise AssertionError("direct activation smoke descriptor set disagrees")
        inherited = os.fstat(3)
        lock = self.activation_lock.stat()
        if (inherited.st_dev, inherited.st_ino) != (lock.st_dev, lock.st_ino):
            raise AssertionError("direct activation smoke FD 3 binding disagrees")
        if fcntl.fcntl(3, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
            raise AssertionError("direct activation smoke FD 3 is not inheritable")
        return subprocess.CompletedProcess(argv, 0, stdout=b"accepted\n", stderr=b"")


class SmokeChildBoundary:
    """Mock only process creation while proving the inherited-lock handoff."""

    def __init__(
        self, prepared: PreparedActivation, stdout: bytes, *, returncode: int = 0
    ) -> None:
        self.prepared = prepared
        self.stdout = stdout
        self.returncode = returncode
        self.calls = 0

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        pass_fds: tuple[int, ...],
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls += 1
        expected_argv = (
            str(self.prepared.canonical_root / "task-witness"),
            "activation-smoke",
        )
        if argv != expected_argv:
            raise AssertionError(f"activation smoke argv disagrees: {argv!r}")
        if pass_fds != (3,):
            raise AssertionError(
                f"activation smoke descriptor set disagrees: {pass_fds!r}"
            )
        inherited = os.fstat(3)
        lock = self.prepared.activation_lock.stat()
        if (inherited.st_dev, inherited.st_ino) != (lock.st_dev, lock.st_ino):
            raise AssertionError("FD 3 does not identify canonical activation.lock")
        assert_exclusive_lock(self.prepared.activation_lock)
        transaction_raw = (
            self.prepared.canonical_root / "transaction.json"
        ).read_bytes()
        transaction = canonical_value(transaction_raw)
        smoke_handoff = transaction["smoke_handoff"]
        candidate_sha256 = sha256(self.prepared.staged.deployment_raw)
        if transaction["phase"] != "candidate-smoke":
            raise AssertionError("activation smoke transaction phase disagrees")
        if smoke_handoff["target_deployment_receipt_sha256"] != candidate_sha256:
            raise AssertionError("activation smoke target receipt disagrees")
        smoke = self.prepared.staged.deployment_value["smoke"]
        if smoke_handoff["smoke_bundle_sha256"] != smoke["bundle"]["sha256"]:
            raise AssertionError("activation smoke bundle binding disagrees")
        if (
            smoke_handoff["smoke_trust_context_sha256"]
            != smoke["trust_context"]["sha256"]
        ):
            raise AssertionError("activation smoke context binding disagrees")
        return subprocess.CompletedProcess(
            argv,
            self.returncode,
            stdout=self.stdout if self.returncode == 0 else b"",
            stderr=b"" if self.returncode == 0 else b"candidate rejected\n",
        )
