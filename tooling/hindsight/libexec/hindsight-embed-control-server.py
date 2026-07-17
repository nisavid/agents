#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path


PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
COMPONENTS = frozenset({"daemon", "ui"})
STATES = frozenset({"running", "stopped"})
CONTROL_PID_LOCK_NAME = ".control-pid.lifecycle.lock"


def normalize_profile(profile: str) -> str:
    normalized = "default" if profile in ("", "default") else profile
    if not PROFILE_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid profile name: {profile!r}")
    return normalized


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _validate_directory(
    descriptor: int, *, private: bool, label: str
) -> None:
    metadata = os.fstat(descriptor)
    mode = stat.S_IMODE(metadata.st_mode)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"refusing unsafe {label}")
    if metadata.st_uid not in {0, os.geteuid()}:
        raise ValueError(f"refusing unsafe {label}")
    if mode & 0o022 and not (
        metadata.st_uid == 0 and mode & stat.S_ISVTX
    ):
        raise ValueError(f"refusing unsafe {label}")
    if private and (metadata.st_uid != os.geteuid() or mode & 0o077):
        raise ValueError(f"refusing unsafe {label}")


def _open_absolute_directory(
    path: Path, *, create: bool, private: bool, label: str
) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"refusing unsafe {label}")

    # macOS exposes root-owned compatibility aliases such as /var ->
    # /private/var. Resolve only that trusted first component; all remaining
    # components, including the caller-supplied leaf, stay no-follow.
    if len(path.parts) > 1:
        alias = Path("/") / path.parts[1]
        try:
            alias_metadata = os.lstat(alias)
        except OSError:
            alias_metadata = None
        if (
            alias_metadata is not None
            and stat.S_ISLNK(alias_metadata.st_mode)
            and alias_metadata.st_uid == 0
        ):
            target = Path(os.readlink(alias))
            if not target.is_absolute():
                target = Path("/") / target
            path = target.joinpath(*path.parts[2:])

    descriptor = os.open("/", _directory_flags())
    try:
        parts = [part for part in path.parts if part not in ("/", "")]
        for index, part in enumerate(parts):
            try:
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise ValueError(f"refusing unsafe {label}") from error
                try:
                    child = os.open(part, _directory_flags(), dir_fd=descriptor)
                except OSError as error:
                    raise ValueError(f"refusing unsafe {label}") from error
            except OSError as error:
                raise ValueError(f"refusing unsafe {label}") from error

            try:
                _validate_directory(
                    child,
                    private=private and index == len(parts) - 1,
                    label=label,
                )
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        if not parts and private:
            _validate_directory(descriptor, private=True, label=label)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_private_child(parent: int, name: str, label: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    except OSError as error:
        raise ValueError(f"refusing unsafe {label}") from error
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    except OSError as error:
        raise ValueError(f"refusing unsafe {label}") from error
    try:
        _validate_directory(descriptor, private=True, label=label)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _validate_private_file(metadata: os.stat_result, label: str) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ValueError(f"refusing unsafe {label}")


def _validate_replace_target(parent: int, name: str, label: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ValueError(f"refusing unsafe {label}") from error
    _validate_private_file(metadata, label)


def _atomic_private_write(parent: int, name: str, payload: bytes, label: str) -> None:
    _validate_replace_target(parent, name, label)
    temporary = f".{name}.{os.getpid()}.{time.monotonic_ns()}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        written = 0
        while written < len(payload):
            chunk = os.write(descriptor, payload[written:])
            if chunk == 0:
                raise OSError("short write to private state file")
            written += chunk
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


def set_desired_state(root: Path, profile: str, component: str, state: str) -> None:
    profile = normalize_profile(profile)
    if component not in COMPONENTS:
        raise ValueError(f"invalid component: {component!r}")
    if state not in STATES:
        raise ValueError(f"invalid desired state: {state!r}")

    root_descriptor = _open_absolute_directory(
        root, create=True, private=True, label="desired-state root"
    )
    profiles_descriptor: int | None = None
    profile_descriptor: int | None = None
    try:
        profiles_descriptor = _open_private_child(
            root_descriptor, "profiles", "desired-state directory"
        )
        profile_descriptor = _open_private_child(
            profiles_descriptor, profile, "desired-state profile directory"
        )
        _atomic_private_write(
            profile_descriptor,
            component,
            f"{state}\n".encode("utf-8"),
            "desired-state file",
        )
    finally:
        if profile_descriptor is not None:
            os.close(profile_descriptor)
        if profiles_descriptor is not None:
            os.close(profiles_descriptor)
        os.close(root_descriptor)


def _running_action(action, root: Path, components: tuple[str, ...]):
    @functools.wraps(action)
    def wrapped(profile: str):
        for component in components:
            set_desired_state(root, profile, component, "running")
        return action(profile)

    return wrapped


def _stopping_action(action, root: Path, component: str):
    @functools.wraps(action)
    def wrapped(profile: str):
        set_desired_state(root, profile, component, "stopped")
        try:
            result = action(profile)
        except Exception:
            set_desired_state(root, profile, component, "running")
            raise
        if not getattr(result, "ok", True) or getattr(result, "running", False):
            set_desired_state(root, profile, component, "running")
        return result

    return wrapped


def install_lifecycle_hooks(service, desired_state_dir: Path) -> None:
    service.start_daemon = _running_action(
        service.start_daemon, desired_state_dir, ("daemon",)
    )
    service.restart_daemon = _running_action(
        service.restart_daemon, desired_state_dir, ("daemon",)
    )
    service.stop_daemon = _stopping_action(
        service.stop_daemon, desired_state_dir, "daemon"
    )
    service.start_ui = _running_action(
        service.start_ui, desired_state_dir, ("daemon", "ui")
    )
    service.restart_ui = _running_action(
        service.restart_ui, desired_state_dir, ("daemon", "ui")
    )
    service.stop_ui = _stopping_action(
        service.stop_ui, desired_state_dir, "ui"
    )


def install_provider_catalog(providers) -> None:
    existing = {provider.id for provider in providers.PROVIDER_CATALOG}
    additions = []
    if "openai-codex" not in existing:
        additions.append(
            providers.ProviderInfo(
                "openai-codex", "OpenAI Codex (subscription)", False
            )
        )
    if "claude-code" not in existing:
        additions.append(
            providers.ProviderInfo(
                "claude-code", "Claude Code (subscription)", False
            )
        )
    providers.PROVIDER_CATALOG = (*providers.PROVIDER_CATALOG, *additions)


def install_hooks(service, providers, desired_state_dir: Path) -> None:
    install_lifecycle_hooks(service, desired_state_dir)
    install_provider_catalog(providers)


def serve(port: int, desired_state_dir: Path) -> int:
    from hindsight_embed.control_center import providers, server, service

    install_hooks(service, providers, desired_state_dir)
    server.serve(port)
    return 0


def _open_private_append(path: Path) -> int:
    parent = _open_absolute_directory(
        path.parent, create=True, private=True, label="control log directory"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.name,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        _validate_private_file(os.fstat(descriptor), "control log file")
        return descriptor
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(parent)


def _write_private_pid(
    path: Path, pid: int, port: int, desired_state_dir: Path
) -> None:
    parent = _open_absolute_directory(
        path.parent, create=True, private=True, label="control PID directory"
    )
    try:
        _atomic_private_write(
            parent,
            path.name,
            json.dumps(
                {
                    "desired_state_dir": str(desired_state_dir),
                    "pid": pid,
                    "port": port,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii"),
            "control PID file",
        )
    finally:
        os.close(parent)


@contextlib.contextmanager
def _control_pid_lifecycle_lock(path: Path):
    parent = _open_absolute_directory(
        path.parent, create=True, private=True, label="control PID directory"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            CONTROL_PID_LOCK_NAME,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        _validate_private_file(os.fstat(descriptor), "control PID lifecycle lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        os.close(parent)


def _remove_private_pid_if_matches(
    path: Path, pid: int, port: int, desired_state_dir: Path
) -> None:
    parent = _open_absolute_directory(
        path.parent, create=False, private=True, label="control PID directory"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        metadata = os.fstat(descriptor)
        _validate_private_file(metadata, "control PID file")
        payload = os.read(descriptor, 4097)
        expected = json.dumps(
            {
                "desired_state_dir": str(desired_state_dir),
                "pid": pid,
                "port": port,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(payload) > 4096 or payload.decode("ascii").strip() != expected:
            return
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
            return
        os.unlink(path.name, dir_fd=parent)
        os.fsync(parent)
    except FileNotFoundError:
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _terminate_and_reap(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        process.wait()
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def start(port: int, desired_state_dir: Path) -> int:
    from hindsight_embed.control_center import lifecycle

    lifecycle.get_or_create_token()
    pid_path = lifecycle.pid_file()
    with _control_pid_lifecycle_lock(pid_path):
        if lifecycle.control_status(port).running:
            return 0

        log_descriptor = _open_private_append(lifecycle.log_file())
        command = [
            sys.executable,
            str(Path(__file__).absolute()),
            "serve",
            "--port",
            str(port),
            "--desired-state-dir",
            str(desired_state_dir),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_descriptor,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            os.close(log_descriptor)

        try:
            _write_private_pid(
                pid_path, process.pid, port, desired_state_dir
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if lifecycle.control_status(port).running:
                    return 0
                if process.poll() is not None:
                    process.wait()
                    _remove_private_pid_if_matches(
                        pid_path, process.pid, port, desired_state_dir
                    )
                    return 1
                time.sleep(0.25)
            _terminate_and_reap(process)
            _remove_private_pid_if_matches(
                pid_path, process.pid, port, desired_state_dir
            )
            return 1
        except Exception:
            _terminate_and_reap(process)
            _remove_private_pid_if_matches(
                pid_path, process.pid, port, desired_state_dir
            )
            raise


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the managed Hindsight Embed Control Center."
    )
    parser.add_argument("command", choices=("start", "serve"))
    parser.add_argument("--port", required=True, type=_port)
    parser.add_argument("--desired-state-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    desired_state_dir = Path(
        os.path.abspath(args.desired_state_dir.expanduser())
    )
    if args.command == "serve":
        return serve(args.port, desired_state_dir)
    return start(args.port, desired_state_dir)


if __name__ == "__main__":
    raise SystemExit(main())
