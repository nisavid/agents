#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shlex
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from hindsight_embed.daemon_embed_manager import DaemonEmbedManager


@dataclass(frozen=True)
class ControlPidRecord:
    pid: int
    port: int
    desired_state_dir: str


@dataclass(frozen=True)
class Target:
    kind: str
    port: int
    pid: int
    process_identity: str
    cleanup_path: Path | None = None
    cleanup_identity: tuple[int, int] | None = None
    cleanup_record: ControlPidRecord | None = None


class StopError(RuntimeError):
    pass


class InvalidControlPid(StopError):
    def __init__(self, path: Path, identity: tuple[int, int]):
        super().__init__(f"invalid control PID file: {path}")
        self.identity = identity


CONTROL_PID_LOCK_NAME = ".control-pid.lifecycle.lock"


def _open_control_pid_parent(path: Path) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path.parent, flags)
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        os.close(descriptor)
        raise OSError("unsafe control PID directory")
    return descriptor


@contextlib.contextmanager
def control_pid_lifecycle_lock(path: Path):
    parent: int | None = None
    descriptor: int | None = None
    try:
        try:
            parent = _open_control_pid_parent(path)
        except FileNotFoundError:
            yield False
            return
        descriptor = os.open(
            CONTROL_PID_LOCK_NAME,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise StopError("unsafe control PID lifecycle lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield True
    except StopError:
        raise
    except OSError as error:
        raise StopError("failed to lock control PID lifecycle") from error
    finally:
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def unlink_stale_pid(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        raise StopError(f"failed to remove stale PID file: {path}") from error


def read_control_pid(path: Path) -> tuple[ControlPidRecord, tuple[int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent: int | None = None
    descriptor: int | None = None
    try:
        parent = _open_control_pid_parent(path)
        descriptor = os.open(path.name, flags, dir_fd=parent)
    except FileNotFoundError:
        if parent is not None:
            os.close(parent)
        raise
    except OSError as error:
        if parent is not None:
            os.close(parent)
        raise StopError(f"failed to securely open control PID file: {path}") from error
    assert descriptor is not None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or metadata.st_size > 4096
        ):
            raise StopError(f"unsafe control PID file: {path}")
        payload = os.read(descriptor, 4097)
        if len(payload) > 4096:
            raise StopError(f"unsafe control PID file: {path}")
        identity = (metadata.st_dev, metadata.st_ino)
        try:
            value = json.loads(payload.decode("ascii"))
            if not isinstance(value, dict) or set(value) != {
                "desired_state_dir", "pid", "port"
            }:
                raise ValueError("unexpected control PID fields")
            pid = value["pid"]
            port = value["port"]
            desired_state_dir = value["desired_state_dir"]
            desired_path = Path(desired_state_dir)
            if (
                not isinstance(pid, int)
                or isinstance(pid, bool)
                or pid <= 0
                or not isinstance(port, int)
                or isinstance(port, bool)
                or not 1 <= port <= 65535
                or not isinstance(desired_state_dir, str)
                or not desired_path.is_absolute()
                or ".." in desired_path.parts
                or str(desired_path) != desired_state_dir
            ):
                raise ValueError("invalid control PID identity")
        except (json.JSONDecodeError, UnicodeError, ValueError) as error:
            raise InvalidControlPid(path, identity) from error
        return ControlPidRecord(pid, port, desired_state_dir), identity
    finally:
        os.close(descriptor)
        if parent is not None:
            os.close(parent)


def unlink_control_pid_identity(path: Path, identity: tuple[int, int]) -> None:
    parent: int | None = None
    descriptor: int | None = None
    try:
        parent = _open_control_pid_parent(path)
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        current = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False
        )
        if (
            (opened.st_dev, opened.st_ino) != identity
            or (current.st_dev, current.st_ino) != identity
            or opened.st_nlink != 1
        ):
            raise StopError(
                f"control PID file changed before cleanup: {path}"
            )
        opened_after = os.fstat(descriptor)
        current_after = os.stat(
            path.name, dir_fd=parent, follow_symlinks=False
        )
        if (
            (opened_after.st_dev, opened_after.st_ino) != identity
            or (current_after.st_dev, current_after.st_ino) != identity
            or opened_after.st_nlink != 1
        ):
            raise StopError(
                f"control PID file changed before cleanup: {path}"
            )
        os.unlink(path.name, dir_fd=parent)
        os.fsync(parent)
    except FileNotFoundError:
        return
    except OSError as error:
        raise StopError(f"failed to remove control PID file: {path}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def cleanup_control_pid(
    target: Target,
    expected_identity: tuple[int, int] | None = None,
    expected_record: ControlPidRecord | None = None,
) -> None:
    if target.cleanup_path is None:
        return
    expected_identity = expected_identity or target.cleanup_identity
    if expected_identity is None:
        raise StopError(f"control PID file identity was not preflighted: {target.cleanup_path}")
    path = target.cleanup_path
    try:
        record, identity = read_control_pid(path)
    except FileNotFoundError:
        # Once the verified process is stopped, another trusted cleanup path may
        # already have removed the marker. Absence is the only tolerated drift.
        return
    expected_record = expected_record or target.cleanup_record
    if (
        record.pid != target.pid
        or record.port != target.port
        or identity != expected_identity
        or (expected_record is not None and record != expected_record)
    ):
        raise StopError(f"control PID file changed before cleanup: {path}")
    unlink_control_pid_identity(path, identity)


def process_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["/bin/ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def process_args(pid: int) -> list[str]:
    command = process_command(pid)
    if not command:
        return []
    try:
        return shlex.split(command)
    except ValueError:
        return []


def run_lsof(*arguments: str):
    for executable in ("/usr/sbin/lsof", "/usr/bin/lsof"):
        try:
            return subprocess.run(
                [executable, *arguments],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def find_pids_on_port(manager: DaemonEmbedManager, port: int) -> list[int]:
    result = run_lsof("-nP", f"-tiTCP:{port}", "-sTCP:LISTEN")
    if result is None:
        if os.name == "nt":
            pid = manager._find_pid_on_port(port)
            return [] if pid is None else [pid]
        raise StopError(
            "listener discovery requires lsof at /usr/sbin/lsof or /usr/bin/lsof"
        )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    pids: list[int] = []
    for value in result.stdout.split():
        try:
            pid = int(value)
        except ValueError as error:
            raise StopError("lsof returned an invalid listener PID") from error
        if pid <= 0:
            raise StopError("lsof returned an invalid listener PID")
        if pid not in pids:
            pids.append(pid)
    return pids


def stable_process_identity(pid: int) -> str:
    try:
        result = subprocess.run(
            [
                "/bin/ps", "-ww", "-p", str(pid),
                "-o", "lstart=", "-o", "command=",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def verified_process_identity(pid: int, owns_process) -> str:
    before = stable_process_identity(pid)
    if not before or not owns_process():
        return ""
    after = stable_process_identity(pid)
    return before if before == after else ""


def process_is_absent(pid: int) -> bool:
    """Distinguish a missing process from inconclusive identity evidence."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def has_arg_value(argv: list[str], name: str, value: str) -> bool:
    for index, arg in enumerate(argv):
        if arg == name and index + 1 < len(argv) and argv[index + 1] == value:
            return True
        if arg == f"{name}={value}":
            return True
    return False


def process_has_open_file(pid: int, path: Path) -> bool:
    result = run_lsof("-Fn", "-p", str(pid))
    if result is None or result.returncode != 0:
        return False
    expected = str(path)
    for line in result.stdout.splitlines():
        if line.startswith("n") and line[1:] == expected:
            return True
    return False


def process_cwd(pid: int) -> Path | None:
    result = run_lsof("-Ffn", "-p", str(pid))
    if result is None or result.returncode != 0:
        return None
    lines = result.stdout.splitlines()
    for index, line in enumerate(lines[:-1]):
        if line == "fcwd" and lines[index + 1].startswith("n"):
            return Path(lines[index + 1][1:])
    return None


def has_hindsight_api_signature(argv: list[str]) -> bool:
    for index, argument in enumerate(argv):
        if Path(argument).name == "hindsight-api":
            return True
        if (
            index > 0
            and argv[index - 1] == "-m"
            and (
                argument == "hindsight_api"
                or argument.startswith("hindsight_api.")
            )
        ):
            return True
    return False


def owns_hindsight_api(pid: int, paths) -> bool:
    # The API command does not include a profile name, so require evidence tied
    # to this profile's daemon log in addition to an executable/module
    # signature before taking ownership of the PID.
    argv = process_args(pid)
    return (
        has_hindsight_api_signature(argv)
        and process_has_open_file(pid, paths.log)
    )


def owns_hindsight_ui(pid: int, port: int, paths, api_url: str) -> bool:
    argv = process_args(pid)
    exact_command = (
        len(argv) == 6
        and Path(argv[0]).name in {"node", "nodejs"}
        and "hindsight-control-plane" in Path(argv[1]).parts
        and argv[2:] == ["--port", str(port), "--api-url", api_url]
    )
    rewritten_title = (
        len(argv) == 2
        and argv[0] == "next-server"
        and argv[1].startswith("(v")
        and argv[1].endswith(")")
    )
    cwd = process_cwd(pid) if rewritten_title else None
    managed_cwd = (
        cwd is not None
        and cwd.name == "standalone"
        and "hindsight-control-plane" in cwd.parts
    )
    return (exact_command or managed_cwd) and process_has_open_file(
        pid, paths.ui_log
    )


def owns_hindsight_control(
    pid: int, port: int, desired_state_dir: str | None = None
) -> bool:
    argv = process_args(pid)
    if not argv:
        return False
    managed_wrapper = str(
        Path(__file__).resolve().with_name("hindsight-embed-control-server.py")
    )
    if Path(argv[0]).name.startswith("python"):
        upstream = argv[1:] == [
            "-m", "hindsight_embed.control_center.server",
            "--port", str(port),
        ]
        managed_args = argv[1:]
    else:
        upstream = False
        managed_args = argv
    managed = (
        len(managed_args) == 6
        and managed_args[:4] == [
            managed_wrapper, "serve", "--port", str(port)
        ]
        and managed_args[4] == "--desired-state-dir"
        and Path(managed_args[5]).is_absolute()
        and (
            desired_state_dir is None
            or managed_args[5] == desired_state_dir
        )
    )
    return upstream or managed


def fail_unverified(kind: str, port: int, pid: int) -> None:
    raise StopError(
        f"refusing to stop unverified listener on {kind} port {port} (pid {pid})"
    )


def find_owned_targets(manager: DaemonEmbedManager, paths, api_url: str, api_ports: set[int], ui_ports: set[int]) -> list[Target]:
    targets: list[Target] = []
    for port in sorted(ui_ports):
        for pid in find_pids_on_port(manager, port):
            identity = verified_process_identity(
                pid, lambda: owns_hindsight_ui(pid, port, paths, api_url)
            )
            if not identity:
                fail_unverified("UI", port, pid)
            targets.append(Target("UI", port, pid, identity))

    for port in sorted(api_ports):
        for pid in find_pids_on_port(manager, port):
            identity = verified_process_identity(
                pid, lambda: owns_hindsight_api(pid, paths)
            )
            if not identity:
                fail_unverified("API", port, pid)
            targets.append(Target("API", port, pid, identity))

    return targets


def find_control_target(
    manager: DaemonEmbedManager, port: int, *, inspect_pid_file: bool = True
) -> list[Target]:
    pid_path = Path.home() / ".hindsight" / "control.pid"
    pids = find_pids_on_port(manager, port)
    if len(pids) > 1:
        raise StopError(f"multiple control listeners found on port {port}")
    pid = pids[0] if pids else None
    if not inspect_pid_file:
        if pid is None:
            return []
        identity = verified_process_identity(
            pid, lambda: owns_hindsight_control(pid, port)
        )
        if not identity:
            fail_unverified("control", port, pid)
        return [Target("control", port, pid, identity)]
    if pid is None:
        try:
            record, cleanup_identity = read_control_pid(pid_path)
        except FileNotFoundError:
            return []
        except InvalidControlPid as error:
            unlink_control_pid_identity(pid_path, error.identity)
            return []
        identity = verified_process_identity(
            record.pid,
            lambda: record.port == port and owns_hindsight_control(
                record.pid, port, record.desired_state_dir
            ),
        )
        if identity:
            return [Target(
                "control", port, record.pid, identity, pid_path,
                cleanup_identity, record,
            )]
        if process_is_absent(record.pid):
            cleanup_control_pid(
                Target(
                    "control", port, record.pid, "",
                    pid_path, cleanup_identity, record,
                )
            )
            return []
        fail_unverified("control", port, record.pid)
    identity = verified_process_identity(
        pid, lambda: owns_hindsight_control(pid, port)
    )
    if not identity:
        fail_unverified("control", port, pid)
    try:
        record, cleanup_identity = read_control_pid(pid_path)
    except FileNotFoundError:
        return [Target("control", port, pid, identity)]
    except InvalidControlPid as error:
        unlink_control_pid_identity(pid_path, error.identity)
        return [Target("control", port, pid, identity)]
    if record.pid != pid or record.port != port:
        raise StopError(f"control PID file does not identify listener pid {pid}: {pid_path}")
    if not owns_hindsight_control(pid, port, record.desired_state_dir):
        raise StopError(f"control PID file identity does not match listener pid {pid}: {pid_path}")
    return [Target(
        "control", port, pid, identity, pid_path, cleanup_identity, record
    )]


def stop_targets(manager: DaemonEmbedManager, targets: list[Target]) -> None:
    killed: set[int] = set()
    # Validate every target before the first mutation. A late replacement must
    # not leave a partially stopped profile.
    preflighted: set[int] = set()
    cleanup_records: dict[
        tuple[Path, int], tuple[tuple[int, int], ControlPidRecord]
    ] = {}
    for target in targets:
        if target.cleanup_path is not None:
            try:
                record, cleanup_identity = read_control_pid(target.cleanup_path)
            except FileNotFoundError:
                cleanup_identity = None
            if cleanup_identity is not None:
                if record.pid != target.pid or record.port != target.port:
                    raise StopError(
                        f"control PID file changed before cleanup: {target.cleanup_path}"
                    )
                if (
                    target.cleanup_identity is not None
                    and cleanup_identity != target.cleanup_identity
                ):
                    raise StopError(
                        f"control PID file changed before cleanup: {target.cleanup_path}"
                    )
                if (
                    target.cleanup_record is not None
                    and record != target.cleanup_record
                ):
                    raise StopError(
                        f"control PID file changed before cleanup: {target.cleanup_path}"
                    )
                cleanup_records[(target.cleanup_path, target.pid)] = (
                    cleanup_identity, record
                )
        if target.pid in preflighted:
            continue
        if stable_process_identity(target.pid) != target.process_identity:
            raise StopError(
                f"refusing to stop replaced {target.kind} process on port "
                f"{target.port} (pid {target.pid})"
            )
        preflighted.add(target.pid)
    for target in targets:
        if target.pid in killed:
            continue
        if stable_process_identity(target.pid) != target.process_identity:
            raise StopError(
                f"refusing to stop replaced {target.kind} process on port "
                f"{target.port} (pid {target.pid})"
            )
        if not manager._kill_process(target.pid):
            raise StopError(f"failed to stop {target.kind} process on port {target.port} (pid {target.pid})")
        killed.add(target.pid)

    ports = {target.port for target in targets}
    for _ in range(30):
        if not any(manager._is_port_in_use(port) for port in ports):
            for target in targets:
                expected = (
                    cleanup_records.get((target.cleanup_path, target.pid))
                    if target.cleanup_path is not None
                    else None
                )
                if expected is not None:
                    cleanup_control_pid(target, *expected)
            return
        time.sleep(0.1)

    busy = [str(port) for port in sorted(ports) if manager._is_port_in_use(port)]
    raise StopError("ports still listening after stop: " + ", ".join(busy))


def resolve_targets(
    manager: DaemonEmbedManager,
    args: argparse.Namespace,
    *,
    inspect_control_pid: bool = True,
) -> list[Target]:
    if args.mode == "stop-control":
        if args.control_port is None:
            raise StopError("stop-control mode requires --control-port")
        return find_control_target(
            manager, args.control_port, inspect_pid_file=inspect_control_pid
        )

    profile_manager = manager._profile_manager

    profile_exists = profile_manager.profile_exists(args.profile)
    if not profile_exists:
        if args.allow_unregistered_profile:
            pass
        elif args.require_profile:
            raise StopError(f"profile does not exist: {args.profile or 'default'}")
        else:
            return []

    paths = profile_manager.resolve_profile_paths(args.profile)
    recorded_ui_port = manager._read_recorded_ui_port(paths)
    api_url = manager.get_url(args.profile)

    api_ports: set[int] = set()
    ui_ports: set[int] = set()

    if args.mode in {"stop", "stop-api"}:
        api_ports.add(paths.port)
        if args.desired_api_port is not None:
            api_ports.add(args.desired_api_port)
    if args.mode in {"stop", "stop-ui"}:
        ui_ports.add(paths.ui_port)
        if recorded_ui_port is not None:
            ui_ports.add(recorded_ui_port)
        if args.desired_ui_port is not None:
            ui_ports.add(args.desired_ui_port)
    if args.mode in {"stop", "stop-api", "stop-ui"}:
        return find_owned_targets(manager, paths, api_url, api_ports, ui_ports)

    desired_api_port = args.desired_api_port
    desired_ui_port = args.desired_ui_port
    if desired_api_port is None or desired_ui_port is None:
        raise StopError("normalize mode requires desired API and UI ports")

    api_changed = paths.port != desired_api_port
    if api_changed:
        api_ports.add(paths.port)

    # If the API port changes, the existing UI may still point at the old API
    # URL even when it already occupies the canonical UI port. Restart it.
    ui_ports.add(paths.ui_port)
    if recorded_ui_port is not None:
        ui_ports.add(recorded_ui_port)
    ui_ports.discard(desired_ui_port)
    if api_changed:
        ui_ports.add(desired_ui_port)

    targets = find_owned_targets(manager, paths, api_url, api_ports, ui_ports)

    # Preflight the desired API/UI ports so Hindsight's own start path never
    # gets a chance to reclaim an unrelated service on the canonical ports.
    for kind, port in (("API", desired_api_port), ("UI", desired_ui_port)):
        for pid in find_pids_on_port(manager, port):
            owned = (
                owns_hindsight_api(pid, paths)
                if kind == "API"
                else owns_hindsight_ui(pid, port, paths, api_url)
            )
            if not owned:
                fail_unverified(kind, port, pid)

    return targets


def parse_args(argv: list[str]) -> argparse.Namespace:
    def port(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("port must be an integer") from None
        if not 1 <= parsed <= 65535:
            raise argparse.ArgumentTypeError("port must be from 1 to 65535")
        return parsed

    parser = argparse.ArgumentParser(description="Safely stop Hindsight profile services.")
    parser.add_argument("--mode", choices=("normalize", "stop", "stop-api", "stop-ui", "stop-control"), required=True)
    parser.add_argument("--profile", default="")
    parser.add_argument("--desired-api-port", type=port)
    parser.add_argument("--desired-ui-port", type=port)
    parser.add_argument("--control-port", type=port)
    parser.add_argument("--require-profile", action="store_true")
    parser.add_argument("--allow-unregistered-profile", action="store_true")
    args = parser.parse_args(argv)
    if (
        args.mode == "normalize"
        and args.desired_api_port is not None
        and args.desired_api_port == args.desired_ui_port
    ):
        parser.error("normalize API and UI ports must be distinct")
    return args


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        if args.mode == "stop-control":
            pid_path = Path.home() / ".hindsight" / "control.pid"
            with control_pid_lifecycle_lock(pid_path) as locked:
                manager = DaemonEmbedManager()
                targets = resolve_targets(
                    manager, args, inspect_control_pid=locked
                )
                stop_targets(manager, targets)
        else:
            manager = DaemonEmbedManager()
            targets = resolve_targets(manager, args)
            stop_targets(manager, targets)
    except StopError as exc:
        print(f"hindsight-embed-stop-profile-services: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
