#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
from dataclasses import replace
import fcntl
import functools
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, NamedTuple
from urllib.parse import urlsplit


PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
COMPONENTS = frozenset({"daemon", "ui"})
STATES = frozenset({"running", "stopped"})
CONTROL_PID_LOCK_NAME = ".control-pid.lifecycle.lock"
PROVIDER_PRESET_ENV = {
    "id": "HINDSIGHT_EMBED_PROVIDER_PRESET_ID",
    "label": "HINDSIGHT_EMBED_PROVIDER_PRESET_LABEL",
    "runtime_provider": "HINDSIGHT_EMBED_PROVIDER_PRESET_RUNTIME_PROVIDER",
    "base_url": "HINDSIGHT_EMBED_PROVIDER_PRESET_BASE_URL",
    "model": "HINDSIGHT_EMBED_PROVIDER_PRESET_MODEL",
}


class ProviderPreset(NamedTuple):
    id: str
    label: str
    runtime_provider: str
    base_url: str
    model: str


def provider_preset_from_environment(
    environ: Mapping[str, str] = os.environ,
) -> ProviderPreset | None:
    values = {
        field: environ.get(variable, "").strip()
        for field, variable in PROVIDER_PRESET_ENV.items()
    }
    configured = {field for field, value in values.items() if value}
    if not configured:
        return None
    if configured != set(PROVIDER_PRESET_ENV):
        missing = sorted(set(PROVIDER_PRESET_ENV) - configured)
        variables = ", ".join(PROVIDER_PRESET_ENV[field] for field in missing)
        raise ValueError(f"incomplete provider preset; missing {variables}")
    if not PROFILE_PATTERN.fullmatch(values["id"]):
        raise ValueError("invalid provider preset ID")
    if not PROFILE_PATTERN.fullmatch(values["runtime_provider"]):
        raise ValueError("invalid provider preset runtime provider")
    if any(
        len(values[field]) > limit
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in values[field])
        for field, limit in (("label", 256), ("model", 512))
    ):
        raise ValueError("invalid provider preset display value")
    if len(values["base_url"]) > 2048 or any(
        character.isspace() for character in values["base_url"]
    ):
        raise ValueError("invalid provider preset base URL")
    try:
        endpoint = urlsplit(values["base_url"])
        port = endpoint.port
    except ValueError as error:
        raise ValueError("invalid provider preset base URL") from error
    try:
        host_is_loopback = ipaddress.ip_address(
            endpoint.hostname or ""
        ).is_loopback
    except ValueError:
        host_is_loopback = False
    if (
        endpoint.scheme not in {"http", "https"}
        or not endpoint.netloc
        or endpoint.hostname is None
        or endpoint.username is not None
        or endpoint.password is not None
        or endpoint.query
        or endpoint.fragment
        or (endpoint.scheme == "http" and not host_is_loopback)
        or port == 0
    ):
        raise ValueError("invalid provider preset base URL")
    return ProviderPreset(**values)


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


def install_provider_catalog(
    providers, preset: ProviderPreset | None = None
) -> None:
    existing = {provider.id for provider in providers.PROVIDER_CATALOG}
    reserved = existing | {"openai-codex", "claude-code"}
    if preset is not None and preset.id in reserved:
        raise ValueError(f"provider preset ID is reserved: {preset.id}")
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
    if preset is not None:
        additions.append(
            providers.ProviderInfo(
                preset.id, preset.label, False, preset.base_url
            )
        )
    providers.PROVIDER_CATALOG = (*providers.PROVIDER_CATALOG, *additions)


def _matches_provider_preset(config, preset: ProviderPreset) -> bool:
    return (
        config.provider == preset.runtime_provider
        and config.model == preset.model
        and config.base_url == preset.base_url
    )


def install_provider_alias(service, preset: ProviderPreset) -> None:
    original_get_profile_config = service.get_profile_config
    original_list_profiles = service.list_profiles
    original_save_llm_config = service.save_llm_config

    def display_config(config):
        if _matches_provider_preset(config, preset):
            return replace(config, provider=preset.id)
        return config

    def get_profile_config(name: str):
        return display_config(original_get_profile_config(name))

    def list_profiles():
        summaries = []
        for summary in original_list_profiles():
            config = get_profile_config(summary.name)
            if config.provider == preset.id:
                summary = replace(
                    summary, provider=preset.id, model=preset.model
                )
            summaries.append(summary)
        return summaries

    def save_llm_config(
        name: str,
        provider: str,
        api_key: str | None,
        model: str | None,
        base_url: str | None,
        api_port: str | None = None,
        ui_port: str | None = None,
        api_version: str | None = None,
        cp_version: str | None = None,
    ):
        current = original_get_profile_config(name)
        if provider == preset.id:
            provider = preset.runtime_provider
            api_key = ""
            model = preset.model
            base_url = preset.base_url
        elif base_url is None and _matches_provider_preset(current, preset):
            base_url = ""

        return display_config(
            original_save_llm_config(
                name=name,
                provider=provider,
                api_key=api_key,
                model=model,
                base_url=base_url,
                api_port=api_port,
                ui_port=ui_port,
                api_version=api_version,
                cp_version=cp_version,
            )
        )

    service.get_profile_config = get_profile_config
    service.list_profiles = list_profiles
    service.save_llm_config = save_llm_config


def install_hooks(service, providers, desired_state_dir: Path) -> None:
    preset = provider_preset_from_environment()
    install_provider_catalog(providers, preset)
    install_lifecycle_hooks(service, desired_state_dir)
    if preset is not None:
        install_provider_alias(service, preset)


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


def _read_private_pid(path: Path) -> dict[str, object] | None:
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
        if len(payload) > 4096:
            raise ValueError("control PID file is invalid")
        value = json.loads(payload)
        if (
            not isinstance(value, dict)
            or set(value) != {"desired_state_dir", "pid", "port"}
            or type(value["pid"]) is not int
            or value["pid"] <= 0
            or type(value["port"]) is not int
            or not 1 <= value["port"] <= 65535
            or not isinstance(value["desired_state_dir"], str)
            or not Path(value["desired_state_dir"]).is_absolute()
        ):
            raise ValueError("control PID file is invalid")
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError("control PID file changed")
        return value
    except FileNotFoundError:
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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
        try:
            existing = _read_private_pid(pid_path)
        except ValueError:
            print(
                "hindsight-embed-control-server: refusing invalid control PID file",
                file=sys.stderr,
            )
            return 1
        if existing is not None:
            existing_pid = int(existing["pid"])
            existing_port = int(existing["port"])
            existing_state = Path(str(existing["desired_state_dir"]))
            if _process_running(existing_pid):
                # A live wrapper on another port owns the singleton PID file.
                # Refuse replacement rather than orphaning or signaling a PID
                # whose complete process identity is not available here.
                return 1
            _remove_private_pid_if_matches(
                pid_path, existing_pid, existing_port, existing_state
            )

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
