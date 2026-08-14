#!/usr/bin/env python3
"""Supervise one prepared public-release validation process group."""

from __future__ import annotations

import sys

if __name__ == "__main__" and (
    sys.implementation.name != "cpython"
    or sys.version_info < (3, 13)
    or not sys.flags.isolated
    or not sys.flags.dont_write_bytecode
):
    raise SystemExit(
        "supervise_prepared_release_validation.py must run with CPython 3.13+ "
        "and Python -I -B; the proof boundary begins at isolated interpreter startup"
    )

import hashlib
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import Enum, auto
from pathlib import Path
from types import FrameType

CANCELLATION_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
CANCELLATION_SIGNAL_SET = frozenset(CANCELLATION_SIGNALS)
OWNED_DISPOSITION_SIGNALS = (*CANCELLATION_SIGNALS, signal.SIGCHLD)
OWNED_SIGNAL_SET = frozenset(OWNED_DISPOSITION_SIGNALS)
TERMINATION_GRACE_SECONDS = 0.25
TERMINAL_WAIT_OPTIONS = os.WEXITED | os.WNOHANG | os.WNOWAIT
VALIDATION_CHILD_MODE = "--validation-child"
PREPARED_SUPERVISOR_SOURCE_OPTION = "--prepared-supervisor-source-sha256"
SOURCE_SHA256 = "07f591426c8ba604c4df5b117b60658244a42180ce7519aae446e89181768199"
MAX_SUPERVISOR_SOURCE_BYTES = 1024 * 1024
SignalHandler = int | Callable[[int, FrameType | None], None]


def _normalized_source_generation_sha256(source: bytes) -> str:
    pattern = re.compile(rb'^SOURCE_SHA256 = "[0-9a-f]{64}"$', re.MULTILINE)
    if len(pattern.findall(source)) != 1:
        raise SystemExit(
            "prepared release validation supervisor source generation is malformed"
        )
    normalized = pattern.sub(b'SOURCE_SHA256 = "' + (b"0" * 64) + b'"', source)
    return hashlib.sha256(normalized).hexdigest()


def _bind_loaded_supervisor_source() -> dict[str, object]:
    path = Path(os.path.abspath(__file__))
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        visible = os.lstat(path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (visible.st_dev, visible.st_ino) != (metadata.st_dev, metadata.st_ino)
            or metadata.st_size > MAX_SUPERVISOR_SOURCE_BYTES
        ):
            raise OSError
        source = os.pread(descriptor, metadata.st_size + 1, 0)
        after = os.fstat(descriptor)
        if (
            len(source) != metadata.st_size
            or (after.st_dev, after.st_ino, after.st_size)
            != (metadata.st_dev, metadata.st_ino, metadata.st_size)
            or _normalized_source_generation_sha256(source) != SOURCE_SHA256
        ):
            raise OSError
    except OSError as error:
        try:
            os.close(descriptor)
        except (NameError, OSError):
            pass
        raise SystemExit(
            "prepared release validation supervisor source generation mismatch"
        ) from error
    return {
        "descriptor": descriptor,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "path": path,
        "source": source,
        "source_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
    }


_LOADED_SUPERVISOR_SOURCE = _bind_loaded_supervisor_source()


def _loaded_supervisor_source_matches(path: Path) -> bool:
    expected = Path(os.path.abspath(os.fspath(path)))
    if expected != _LOADED_SUPERVISOR_SOURCE["path"]:
        return False
    descriptor = int(_LOADED_SUPERVISOR_SOURCE["descriptor"])
    try:
        metadata = os.fstat(descriptor)
        visible = os.lstat(expected)
        source = os.pread(descriptor, metadata.st_size + 1, 0)
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and (metadata.st_dev, metadata.st_ino)
        == (
            int(_LOADED_SUPERVISOR_SOURCE["device"]),
            int(_LOADED_SUPERVISOR_SOURCE["inode"]),
        )
        == (visible.st_dev, visible.st_ino)
        and source == _LOADED_SUPERVISOR_SOURCE["source"]
    )


class SupervisionError(Exception):
    """A fail-closed prepared-release supervision failure."""


class StartError(SupervisionError):
    """A prepared-release child creation failure."""


class InadmissibleSignalDisposition(SupervisionError):
    """An inherited signal disposition has already weakened supervision."""


class OwnershipLost(SupervisionError):
    """Exact ownership was externally revoked before an exact reap."""


class ChildState(Enum):
    OWNED_TERMINAL_UNKNOWN = auto()
    OWNED_TERMINAL_OBSERVED = auto()
    REAPED = auto()
    LOST = auto()


class OwnedChild:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self.state = ChildState.OWNED_TERMINAL_UNKNOWN

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def may_signal(self) -> bool:
        return self.state in {
            ChildState.OWNED_TERMINAL_UNKNOWN,
            ChildState.OWNED_TERMINAL_OBSERVED,
        }

    def observe_terminal(self) -> bool:
        if self.state is ChildState.OWNED_TERMINAL_OBSERVED:
            return True
        if self.state in {ChildState.REAPED, ChildState.LOST}:
            raise OwnershipLost
        try:
            terminal_status = os.waitid(
                os.P_PID,
                self.pid,
                TERMINAL_WAIT_OPTIONS,
            )
        except ChildProcessError as error:
            self.state = ChildState.LOST
            raise OwnershipLost from error
        except OSError as error:
            raise SupervisionError from error
        if terminal_status is None:
            return False
        if terminal_status.si_pid != self.pid:
            self.state = ChildState.LOST
            raise OwnershipLost
        self.state = ChildState.OWNED_TERMINAL_OBSERVED
        return True

    def reap(self) -> int:
        if self.state in {ChildState.REAPED, ChildState.LOST}:
            raise OwnershipLost
        try:
            returncode = self.process.wait()
        except ChildProcessError as error:
            self.state = ChildState.LOST
            raise OwnershipLost from error
        except OSError as error:
            raise SupervisionError from error
        self.state = ChildState.REAPED
        return returncode


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def private_environment(temporary_root: Path) -> dict[str, str]:
    environment = {
        "HOME": str(temporary_root / "home"),
        "TEMP": str(temporary_root / "tmp"),
        "TMP": str(temporary_root / "tmp"),
        "TMPDIR": str(temporary_root / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    if "__CF_USER_TEXT_ENCODING" in os.environ:
        environment["__CF_USER_TEXT_ENCODING"] = os.environ["__CF_USER_TEXT_ENCODING"]
    return environment


def validation_command(
    mode: str,
    repository: Path,
    arguments: list[str],
) -> list[str]:
    if any(
        argument == PREPARED_SUPERVISOR_SOURCE_OPTION
        or argument.startswith(f"{PREPARED_SUPERVISOR_SOURCE_OPTION}=")
        for argument in arguments
    ):
        raise ValueError("prepared supervisor source identity is entrypoint-owned")
    if mode == "public-release":
        entrypoint = repository / "scripts" / "validate_public_release.py"
        entrypoint_arguments = [
            str(repository),
            *arguments,
            PREPARED_SUPERVISOR_SOURCE_OPTION,
            str(_LOADED_SUPERVISOR_SOURCE["source_sha256"]),
        ]
    elif mode == "phase7-production":
        if any(
            argument == "--public-root" or argument.startswith("--public-root=")
            for argument in arguments
        ):
            raise ValueError(
                "phase7-production receives --public-root from the entrypoint"
            )
        entrypoint = repository / "scripts" / "run_phase7_production_integration.py"
        entrypoint_arguments = [
            "--public-root",
            str(repository),
            *arguments,
            PREPARED_SUPERVISOR_SOURCE_OPTION,
            str(_LOADED_SUPERVISOR_SOURCE["source_sha256"]),
        ]
    else:
        raise ValueError("mode must be public-release or phase7-production")

    if not entrypoint.is_file():
        raise ValueError("selected release entrypoint is missing")
    return [sys.executable, "-I", "-B", str(entrypoint), *entrypoint_arguments]


def supervisor_belongs_to_repository(repository: Path) -> bool:
    expected = repository / "scripts" / "supervise_prepared_release_validation.py"
    return _loaded_supervisor_source_matches(expected)


def consume_pending_cancellation_signal() -> int | None:
    pending_signals = signal.sigpending()
    selected_signal = next(
        (
            signal_number
            for signal_number in CANCELLATION_SIGNALS
            if signal_number in pending_signals
        ),
        None,
    )
    if selected_signal is None:
        return None
    return int(signal.sigwait({selected_signal}))


def normalize_supervision_dispositions() -> dict[int, SignalHandler]:
    original_dispositions = {
        signal_number: signal.getsignal(signal_number)
        for signal_number in OWNED_DISPOSITION_SIGNALS
    }
    for signal_number, disposition in original_dispositions.items():
        admissible = disposition == signal.SIG_DFL or (
            signal_number == signal.SIGINT and disposition is signal.default_int_handler
        )
        if not admissible:
            raise InadmissibleSignalDisposition
    for signal_number in OWNED_DISPOSITION_SIGNALS:
        signal.signal(signal_number, signal.SIG_DFL)
    return original_dispositions


def restore_supervision_dispositions(
    original_dispositions: dict[int, SignalHandler],
) -> None:
    for signal_number, disposition in original_dispositions.items():
        signal.signal(signal_number, disposition)


@contextmanager
def supervision_signal_scope() -> Iterator[None]:
    original_mask = signal.pthread_sigmask(
        signal.SIG_BLOCK,
        CANCELLATION_SIGNAL_SET,
    )
    try:
        original_dispositions = normalize_supervision_dispositions()
    except InadmissibleSignalDisposition:
        signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
        raise
    try:
        yield
    finally:
        restore_supervision_dispositions(original_dispositions)
        signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)


def signal_anchored_process_group(
    child: OwnedChild,
    signal_number: int,
) -> None:
    if not child.may_signal:
        raise OwnershipLost
    try:
        os.killpg(child.pid, signal_number)
    except (PermissionError, ProcessLookupError) as error:
        # Darwin reports a retained zombie-only group as unsignalable. That is
        # expected only after the exactly owned leader is observably terminal.
        if not child.observe_terminal():
            raise SupervisionError from error
    except OSError as error:
        raise SupervisionError from error


def terminate_process_group(
    child: OwnedChild,
    signal_number: int,
) -> tuple[int, bool]:
    signal_failure = False
    try:
        signal_anchored_process_group(child, signal_number)
    except OwnershipLost:
        raise
    except SupervisionError:
        signal_failure = True
    time.sleep(TERMINATION_GRACE_SECONDS)

    try:
        signal_anchored_process_group(child, signal.SIGKILL)
    except OwnershipLost:
        raise
    except SupervisionError:
        signal_failure = True
        if child.may_signal:
            try:
                os.kill(child.pid, signal.SIGKILL)
            except OSError:
                # `signal_failure` remains set, so this cannot become success.
                pass

    # The grace period bounds escalation, not OS termination. Exact ownership
    # requires an unbounded wait here rather than abandoning a live child.
    child.reap()
    return 128 + signal_number, signal_failure


def wait_for_terminal_boundary(child: OwnedChild) -> int | None:
    while True:
        cancellation_signal = consume_pending_cancellation_signal()
        if cancellation_signal is not None:
            return cancellation_signal

        if child.observe_terminal():
            # This empty snapshot is the terminal linearization point. Signals
            # arriving after it retain their original disposition.
            return consume_pending_cancellation_signal()

        time.sleep(0.01)


def validation_child_command(command: list[str]) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-B",
        str(Path(__file__).resolve()),
        VALIDATION_CHILD_MODE,
        *command,
    ]


def supervise_blocked(command: list[str], environment: dict[str, str]) -> int:
    try:
        process = subprocess.Popen(
            validation_child_command(command),
            env=environment,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            restore_signals=True,
            start_new_session=True,
        )
    except OSError as error:
        raise StartError from error
    child = OwnedChild(process)

    try:
        cancellation_signal = wait_for_terminal_boundary(child)
    except OwnershipLost:
        raise
    except SupervisionError as error:
        terminate_process_group(child, signal.SIGKILL)
        raise SupervisionError from error

    if cancellation_signal is not None:
        returncode, signal_failure = terminate_process_group(
            child,
            cancellation_signal,
        )
        if signal_failure:
            raise SupervisionError
        return returncode

    returncode = child.reap()
    if returncode < 0:
        return 128 - returncode
    return returncode


def supervise(command: list[str], environment: dict[str, str]) -> int:
    with supervision_signal_scope():
        cancellation_signal = consume_pending_cancellation_signal()
        if cancellation_signal is not None:
            return 128 + cancellation_signal
        return supervise_blocked(command, environment)


def _run_prepared_validation_with_owned_signals(command: list[str]) -> int:
    temporary_root = None
    returncode = 2
    diagnostic = None
    try:
        cancellation_signal = consume_pending_cancellation_signal()
        if cancellation_signal is not None:
            returncode = 128 + cancellation_signal
        else:
            temporary_root = Path(
                tempfile.mkdtemp(
                    prefix="prepared-release-validation.",
                    dir="/tmp",
                )
            )
            (temporary_root / "home").mkdir(mode=0o700)
            (temporary_root / "tmp").mkdir(mode=0o700)
            environment = private_environment(temporary_root)
            cancellation_signal = consume_pending_cancellation_signal()
            if cancellation_signal is not None:
                returncode = 128 + cancellation_signal
            else:
                try:
                    returncode = supervise_blocked(command, environment)
                except StartError:
                    diagnostic = "prepared release validation failed to start"
                except SupervisionError:
                    diagnostic = "prepared release validation supervision failed"
    except OSError:
        diagnostic = "prepared release validation private runtime setup failed"
    finally:
        if temporary_root is not None:
            try:
                shutil.rmtree(temporary_root)
            except OSError:
                diagnostic = "prepared release validation cleanup failed"
                returncode = 2

    if diagnostic is not None:
        return fail(diagnostic)
    return returncode


def run_prepared_validation(command: list[str]) -> int:
    try:
        with supervision_signal_scope():
            return _run_prepared_validation_with_owned_signals(command)
    except InadmissibleSignalDisposition:
        return fail(
            "prepared release validation inherited signal disposition is inadmissible"
        )


def run_validation_child(command: list[str]) -> int:
    if len(command) < 4 or command[0] != sys.executable or command[1:3] != ["-I", "-B"]:
        return fail("prepared release validation child invocation is invalid")

    signal.pthread_sigmask(signal.SIG_BLOCK, OWNED_SIGNAL_SET)
    for signal_number in OWNED_DISPOSITION_SIGNALS:
        signal.signal(signal_number, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, OWNED_SIGNAL_SET)
    try:
        os.execve(command[0], command, dict(os.environ))
    except OSError:
        return fail("prepared release validation child exec failed")


def main(arguments: list[str]) -> int:
    if arguments[:1] == [VALIDATION_CHILD_MODE]:
        return run_validation_child(arguments[1:])
    if len(arguments) < 2:
        return fail(
            "usage: supervise_prepared_release_validation.py "
            "MODE REPOSITORY [ARGUMENT ...]"
        )

    mode, repository_argument, *validation_arguments = arguments
    repository = Path(repository_argument)
    if not repository.is_absolute() or not repository.is_dir():
        return fail("repository must be an absolute directory")
    if not supervisor_belongs_to_repository(repository):
        return fail(
            "prepared release validation supervisor does not belong to "
            "the selected repository"
        )

    try:
        command = validation_command(mode, repository, validation_arguments)
    except ValueError as error:
        return fail(str(error))
    returncode = run_prepared_validation(command)
    if not supervisor_belongs_to_repository(repository):
        return fail(
            "prepared release validation supervisor generation changed during validation"
        )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
