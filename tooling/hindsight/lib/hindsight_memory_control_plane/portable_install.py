"""Portable, transactional installation for the reusable Hindsight control plane."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import base64
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import pwd
import re
import signal
import shlex
import shutil
import stat
import subprocess
import tempfile
import sys
import time
from typing import Any

from .canonical import canonical_bytes, digest, strict_json_loads


SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,126}\Z")
ENVIRONMENT_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}\Z")
DAILY_AT = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
SECRET_NAME = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY|ACCESS_KEY|"
    r"PRIVATE_KEY|AUTHORIZATION|BEARER|KEY|APIKEY|ACCESSTOKEN|"
    r"CLIENTSECRET|ACCESSKEY|PRIVATEKEY)(?:_|$)|"
    r"(?:APIKEY|ACCESSTOKEN|CLIENTSECRET|ACCESSKEY|PRIVATEKEY)$"
)
SENSITIVE_ARGUMENT_PARTS = frozenset(
    {"token", "secret", "password", "credential", "authorization", "bearer"}
)
SENSITIVE_HEADER = re.compile(
    r"^\s*(?:authorization|proxy-authorization|x-api-key|api-key|"
    r"cookie|set-cookie)\s*:",
    re.IGNORECASE,
)
SERVICE_MANAGER_COMMAND_TIMEOUT_SECONDS = 360
SYSTEMD_STOP_TIMEOUT_SECONDS = 330
RESOLVER_ENVIRONMENT_BINDINGS = frozenset(
    {
        "HINDSIGHT_MEMORY_DATA_PLANE_TOKEN_ENV",
        "HINDSIGHT_MEMORY_MINT_AUTHORITY_ENV",
        "HINDSIGHT_MEMORY_UI_ACCESS_KEY_ENV",
    }
)
AUTHORIZED_CREDENTIAL_ENVIRONMENTS = frozenset(
    {
        "HINDSIGHT_API_KEY",
        "HINDSIGHT_DATA_PLANE_TOKEN",
        "HINDSIGHT_MINT_AUTHORITY",
        "HINDSIGHT_UI_ACCESS_KEY",
    }
)
LOCATOR = re.compile(r"[a-z][a-z0-9+.-]{1,31}://[^\s\x00]+\Z")


def _signal_process_group(process_group: int, signal_number: int) -> None:
    try:
        os.killpg(process_group, signal_number)
        return
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    try:
        completed = subprocess.run(
            ["/usr/bin/ps", "-axo", "pid=,pgid="],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if completed.returncode != 0:
        return
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not all(
            field.isascii() and field.isdigit() for field in fields
        ):
            continue
        pid, observed_group = map(int, fields)
        if observed_group != process_group or pid == os.getpid():
            continue
        try:
            os.kill(pid, signal_number)
        except (PermissionError, ProcessLookupError):
            pass


class PortableInstallError(ValueError):
    """A portable lifecycle contract was invalid or could not be completed."""


class _ManagedServiceCommandError(PortableInstallError):
    def __init__(self, returncode: int, command: str | None = None) -> None:
        self.returncode = returncode
        detail = f" ({command}, exit {returncode})" if command else ""
        super().__init__(f"managed service command failed{detail}")


@dataclass(frozen=True)
class BindingSnapshot:
    config_bytes: bytes
    inventory_bytes: bytes
    npx_alias: str
    config_file_digest: str
    config_digest: str
    inventory_digest: str
    generation_digest: str


def _closed(
    value: Mapping[str, Any], required: set[str], optional: set[str], label: str
) -> None:
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise PortableInstallError(
            f"{label} missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise PortableInstallError(
            f"{label} has unknown fields: {', '.join(sorted(unknown))}"
        )


def _strict_json_mapping(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = strict_json_loads(payload)
        wire_value = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as error:
        raise PortableInstallError(f"{label} is unreadable or invalid") from error
    if not isinstance(value, dict) or not isinstance(wire_value, dict):
        raise PortableInstallError(f"{label} is invalid")
    if "schema_version" in value and type(wire_value.get("schema_version")) is not int:
        raise PortableInstallError(f"{label} schema_version must be an integer")
    return value


def _absolute(value: Any, label: str) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "'" in value
        or any(character in value for character in "\x00\r\n")
    ):
        raise PortableInstallError(f"{label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute() or any(
        component in {".", ".."} for component in value.split("/")
    ):
        raise PortableInstallError(f"{label} must be an absolute path")
    return path


def _text(value: Any, label: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(character in value for character in "\x00\r\n")
    ):
        raise PortableInstallError(f"{label} is invalid")
    return value


def _identifier(value: Any, label: str) -> str:
    text = _text(value, label, maximum=127)
    if IDENTIFIER.fullmatch(text) is None:
        raise PortableInstallError(f"{label} is invalid")
    return text


def _entrypoint(value: Any, label: str) -> str:
    text = _text(value, label)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PortableInstallError(f"{label} must be a safe relative path")
    return text


def _argv(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 256:
        raise PortableInstallError(f"{label} must be a bounded string list")
    result = tuple(_text(item, f"{label} item", maximum=8192) for item in value)
    for index, item in enumerate(result):
        if "'" in item:
            raise PortableInstallError(f"{label} must not contain apostrophes")
        option, separator, option_value = item.partition("=")
        if re.search(r"\b(?:bearer|basic)\s+\S", item, re.IGNORECASE):
            raise PortableInstallError(f"{label} cannot contain credentials")
        attached_header = (
            item[2:] if item.startswith("-H") and not item.startswith("--") else None
        )
        if attached_header is not None and SENSITIVE_HEADER.search(attached_header):
            raise PortableInstallError(f"{label} cannot contain credentials")
        if option in {"-H", "--header"}:
            header = (
                option_value
                if separator
                else (result[index + 1] if index + 1 < len(result) else "")
            )
            if SENSITIVE_HEADER.search(header):
                raise PortableInstallError(f"{label} cannot contain credentials")
        if not option.startswith("-"):
            continue
        parts = tuple(part for part in re.split(r"[-_]", option.lstrip("-")) if part)
        compact = "".join(parts).lower()
        secret_option = bool(
            SENSITIVE_ARGUMENT_PARTS & {part.lower() for part in parts}
        )
        secret_option = secret_option or any(
            compact.endswith(suffix) for suffix in ("apikey", "accesskey", "privatekey")
        )
        if not secret_option:
            continue
        if option.lower().endswith(("-env", "_env")):
            environment = (
                option_value
                if separator
                else (result[index + 1] if index + 1 < len(result) else "")
            )
            if ENVIRONMENT_NAME.fullmatch(environment) is not None:
                continue
        raise PortableInstallError(f"{label} cannot contain credentials")
    return result


def _environment(value: Any, label: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping) or len(value) > 128:
        raise PortableInstallError(f"{label} must be an object")
    result: list[tuple[str, str]] = []
    for name, raw in value.items():
        if not isinstance(name, str) or ENVIRONMENT_NAME.fullmatch(name) is None:
            raise PortableInstallError(f"{label} name is invalid")
        if name == "PATH" and raw not in {
            "/usr/bin:/bin",
            "/usr/bin:/bin:/usr/sbin:/sbin",
        }:
            raise PortableInstallError(
                f"{label} PATH must contain only protected system directories"
            )
        if SECRET_NAME.search(name) and name not in RESOLVER_ENVIRONMENT_BINDINGS:
            raise PortableInstallError(
                f"credential environment {name} must use a protected locator"
            )
        text = _text(raw, f"{label} value", maximum=8192)
        if text.startswith("release://"):
            _entrypoint(text.removeprefix("release://"), f"{label} release path")
        result.append((name, text))
    return tuple(sorted(result))


@dataclass(frozen=True)
class CredentialBinding:
    environment: str
    locator: str

    @classmethod
    def load(cls, value: Any, label: str) -> "CredentialBinding":
        if not isinstance(value, Mapping):
            raise PortableInstallError(f"{label} must be an object")
        _closed(value, {"environment", "locator"}, set(), label)
        environment = value["environment"]
        if (
            not isinstance(environment, str)
            or ENVIRONMENT_NAME.fullmatch(environment) is None
        ):
            raise PortableInstallError(f"{label} environment is invalid")
        if environment not in AUTHORIZED_CREDENTIAL_ENVIRONMENTS:
            raise PortableInstallError(
                f"{label} environment is not an authorized secret destination"
            )
        locator = _text(value["locator"], f"{label} locator")
        if LOCATOR.fullmatch(locator) is None:
            raise PortableInstallError(f"{label} locator is invalid")
        return cls(environment=environment, locator=locator)

    def to_dict(self) -> dict[str, str]:
        return {"environment": self.environment, "locator": self.locator}


def _credentials(value: Any, label: str) -> tuple[CredentialBinding, ...]:
    if not isinstance(value, list) or len(value) > 64:
        raise PortableInstallError(f"{label} must be a bounded list")
    bindings = tuple(
        CredentialBinding.load(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    )
    names = [binding.environment for binding in bindings]
    if len(names) != len(set(names)):
        raise PortableInstallError(f"{label} environment names must be unique")
    return bindings


@dataclass(frozen=True)
class CredentialResolver:
    path: Path
    sha256: str

    @classmethod
    def load(cls, value: Any) -> "CredentialResolver":
        if not isinstance(value, Mapping):
            raise PortableInstallError("credential_resolver must be an object")
        _closed(value, {"path", "sha256"}, set(), "credential_resolver")
        checksum = value["sha256"]
        if not isinstance(checksum, str) or SHA256.fullmatch(checksum) is None:
            raise PortableInstallError("credential_resolver sha256 is invalid")
        return cls(_absolute(value["path"], "credential_resolver path"), checksum)

    def to_dict(self) -> dict[str, str]:
        return {"path": str(self.path), "sha256": self.sha256}


@dataclass(frozen=True)
class ServiceSpec:
    service_id: str
    label: str
    entrypoint: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    credentials: tuple[CredentialBinding, ...]
    restart: str

    @classmethod
    def load(cls, value: Any, index: int) -> "ServiceSpec":
        label = f"services[{index}]"
        if not isinstance(value, Mapping):
            raise PortableInstallError(f"{label} must be an object")
        _closed(
            value,
            {
                "service_id",
                "label",
                "entrypoint",
                "arguments",
                "environment",
                "credentials",
                "restart",
            },
            set(),
            label,
        )
        restart = value["restart"]
        if restart not in {"never", "on-failure", "always"}:
            raise PortableInstallError(f"{label} restart is invalid")
        environment = _environment(value["environment"], f"{label} environment")
        credentials = _credentials(value["credentials"], f"{label} credentials")
        if set(dict(environment)) & {item.environment for item in credentials}:
            raise PortableInstallError(f"{label} credential environment is duplicated")
        return cls(
            service_id=_identifier(value["service_id"], f"{label} service_id"),
            label=_identifier(value["label"], f"{label} label"),
            entrypoint=_entrypoint(value["entrypoint"], f"{label} entrypoint"),
            arguments=_argv(value["arguments"], f"{label} arguments"),
            environment=environment,
            credentials=credentials,
            restart=restart,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "label": self.label,
            "entrypoint": self.entrypoint,
            "arguments": list(self.arguments),
            "environment": dict(self.environment),
            "credentials": [item.to_dict() for item in self.credentials],
            "restart": self.restart,
        }


@dataclass(frozen=True)
class TimerSpec:
    timer_id: str
    label: str
    entrypoint: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    credentials: tuple[CredentialBinding, ...]
    daily_at: str

    @classmethod
    def load(cls, value: Any, index: int) -> "TimerSpec":
        label = f"timers[{index}]"
        if not isinstance(value, Mapping):
            raise PortableInstallError(f"{label} must be an object")
        _closed(
            value,
            {
                "timer_id",
                "label",
                "entrypoint",
                "arguments",
                "environment",
                "credentials",
                "daily_at",
            },
            set(),
            label,
        )
        daily_at = value["daily_at"]
        if not isinstance(daily_at, str) or DAILY_AT.fullmatch(daily_at) is None:
            raise PortableInstallError(f"{label} daily_at is invalid")
        environment = _environment(value["environment"], f"{label} environment")
        credentials = _credentials(value["credentials"], f"{label} credentials")
        if set(dict(environment)) & {item.environment for item in credentials}:
            raise PortableInstallError(f"{label} credential environment is duplicated")
        return cls(
            timer_id=_identifier(value["timer_id"], f"{label} timer_id"),
            label=_identifier(value["label"], f"{label} label"),
            entrypoint=_entrypoint(value["entrypoint"], f"{label} entrypoint"),
            arguments=_argv(value["arguments"], f"{label} arguments"),
            environment=environment,
            credentials=credentials,
            daily_at=daily_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timer_id": self.timer_id,
            "label": self.label,
            "entrypoint": self.entrypoint,
            "arguments": list(self.arguments),
            "environment": dict(self.environment),
            "credentials": [item.to_dict() for item in self.credentials],
            "daily_at": self.daily_at,
        }


@dataclass(frozen=True)
class HealthCheck:
    check_id: str
    entrypoint: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    credentials: tuple[CredentialBinding, ...]
    timeout_seconds: float

    @classmethod
    def load(cls, value: Any, index: int) -> "HealthCheck":
        label = f"health_checks[{index}]"
        if not isinstance(value, Mapping):
            raise PortableInstallError(f"{label} must be an object")
        _closed(
            value,
            {
                "check_id",
                "entrypoint",
                "arguments",
                "environment",
                "credentials",
                "timeout_seconds",
            },
            set(),
            label,
        )
        environment = _environment(value["environment"], f"{label} environment")
        credentials = _credentials(value["credentials"], f"{label} credentials")
        if set(dict(environment)) & {item.environment for item in credentials}:
            raise PortableInstallError(f"{label} credential environment is duplicated")
        timeout = value["timeout_seconds"]
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 < timeout <= 300
        ):
            raise PortableInstallError(f"{label} timeout_seconds is invalid")
        return cls(
            _identifier(value["check_id"], f"{label} check_id"),
            _entrypoint(value["entrypoint"], f"{label} entrypoint"),
            _argv(value["arguments"], f"{label} arguments"),
            environment,
            credentials,
            float(timeout),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "entrypoint": self.entrypoint,
            "arguments": list(self.arguments),
            "environment": dict(self.environment),
            "credentials": [item.to_dict() for item in self.credentials],
            "timeout_seconds": self.timeout_seconds,
        }


def _systemd_user_service_root(
    command_runner: Callable[[tuple[str, ...]], str | None],
) -> Path:
    try:
        output = command_runner(
            (
                "/usr/bin/systemctl",
                "--user",
                "show-environment",
            )
        )
    except Exception as error:
        raise PortableInstallError(
            "systemd user-manager environment is unavailable"
        ) from error
    if output is None or "\x00" in output or "\r" in output:
        raise PortableInstallError("systemd user-manager environment is invalid")
    values: dict[str, str] = {}
    for line in output.splitlines():
        if not line:
            continue
        name, separator, value = line.partition("=")
        if not separator or name in values:
            raise PortableInstallError("systemd user-manager environment is invalid")
        if name in {"XDG_CONFIG_HOME", "SYSTEMD_UNIT_PATH"}:
            values[name] = value
    if values.get("SYSTEMD_UNIT_PATH"):
        raise PortableInstallError(
            "custom SYSTEMD_UNIT_PATH requires an explicit installation plan"
        )
    try:
        account = pwd.getpwuid(os.geteuid())
    except KeyError as error:
        raise PortableInstallError("managed account identity is unavailable") from error
    raw_config_home = values.get("XDG_CONFIG_HOME") or str(
        Path(account.pw_dir) / ".config"
    )
    config_home = Path(raw_config_home)
    if not config_home.is_absolute() or any(
        part in {"", ".", ".."} for part in config_home.parts[1:]
    ):
        raise PortableInstallError("systemd XDG_CONFIG_HOME is invalid")
    return config_home / "systemd" / "user"


@dataclass(frozen=True)
class InstallationConfig:
    source_path: Path
    consumer_id: str
    platform: str
    installation_mode: str
    install_root: Path
    state_root: Path
    data_root: Path
    service_root: Path
    inventory_path: Path
    python_executable: Path
    npx_executable: Path
    uvx_executable: Path
    zsh_executable: Path
    credential_resolver: CredentialResolver
    services: tuple[ServiceSpec, ...]
    timers: tuple[TimerSpec, ...]
    health_checks: tuple[HealthCheck, ...]

    @classmethod
    def load(cls, value: Any, *, source_path: str | Path) -> "InstallationConfig":
        if not isinstance(value, Mapping):
            raise PortableInstallError("installation config must be an object")
        _closed(
            value,
            {
                "schema_version",
                "consumer_id",
                "platform",
                "installation_mode",
                "install_root",
                "state_root",
                "data_root",
                "service_root",
                "inventory_path",
                "python_executable",
                "npx_executable",
                "uvx_executable",
                "zsh_executable",
                "credential_resolver",
                "services",
                "timers",
                "health_checks",
            },
            set(),
            "installation config",
        )
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise PortableInstallError(
                "installation config schema_version is unsupported"
            )
        platform = value["platform"]
        if platform not in {"launchd", "systemd-user"}:
            raise PortableInstallError("installation platform is unsupported")
        mode = value["installation_mode"]
        if mode not in {"fresh", "adopt"}:
            raise PortableInstallError("installation_mode is invalid")
        services_value = value["services"]
        timers_value = value["timers"]
        checks_value = value["health_checks"]
        if not isinstance(services_value, list) or not services_value:
            raise PortableInstallError("services must be a non-empty list")
        if not isinstance(timers_value, list):
            raise PortableInstallError("timers must be a list")
        if not isinstance(checks_value, list) or not checks_value:
            raise PortableInstallError("health_checks must be a non-empty list")
        services = tuple(
            ServiceSpec.load(item, index) for index, item in enumerate(services_value)
        )
        timers = tuple(
            TimerSpec.load(item, index) for index, item in enumerate(timers_value)
        )
        checks = tuple(
            HealthCheck.load(item, index) for index, item in enumerate(checks_value)
        )
        ids = [item.service_id for item in services] + [
            item.timer_id for item in timers
        ]
        labels = [item.label for item in services] + [item.label for item in timers]
        if len(ids) != len(set(ids)) or len(labels) != len(set(labels)):
            raise PortableInstallError("service and timer identities must be unique")
        check_ids = [item.check_id for item in checks]
        if len(check_ids) != len(set(check_ids)):
            raise PortableInstallError("health check identities must be unique")
        source = _absolute(str(source_path), "installation config source_path")
        roots = {
            "install_root": _absolute(value["install_root"], "install_root"),
            "state_root": _absolute(value["state_root"], "state_root"),
            "data_root": _absolute(value["data_root"], "data_root"),
            "service_root": _absolute(value["service_root"], "service_root"),
        }
        resolved = [path.resolve(strict=False) for path in roots.values()]
        for index, left in enumerate(resolved):
            for right in resolved[index + 1 :]:
                if left == right or left in right.parents or right in left.parents:
                    raise PortableInstallError("installation roots must not overlap")
        inventory_path = _absolute(value["inventory_path"], "inventory_path")
        python_executable = _absolute(value["python_executable"], "python_executable")
        npx_executable = _absolute(value["npx_executable"], "npx_executable")
        uvx_executable = _absolute(value["uvx_executable"], "uvx_executable")
        zsh_executable = _absolute(value["zsh_executable"], "zsh_executable")
        credential_resolver = CredentialResolver.load(value["credential_resolver"])
        install_root = roots["install_root"].resolve(strict=False)
        preserved_inputs = {
            "installation config": source,
            "inventory": inventory_path,
            "credential resolver": credential_resolver.path,
            "managed Python": python_executable,
            "npx executable": npx_executable,
            "uvx executable": uvx_executable,
            "Zsh executable": zsh_executable,
        }
        for label, path in preserved_inputs.items():
            candidate = path.resolve(strict=False)
            if candidate == install_root or install_root in candidate.parents:
                raise PortableInstallError(f"{label} must remain outside install_root")
        return cls(
            source_path=source,
            consumer_id=_identifier(value["consumer_id"], "consumer_id"),
            platform=platform,
            installation_mode=mode,
            install_root=roots["install_root"],
            state_root=roots["state_root"],
            data_root=roots["data_root"],
            service_root=roots["service_root"],
            inventory_path=inventory_path,
            python_executable=python_executable,
            npx_executable=npx_executable,
            uvx_executable=uvx_executable,
            zsh_executable=zsh_executable,
            credential_resolver=credential_resolver,
            services=services,
            timers=timers,
            health_checks=checks,
        )

    @classmethod
    def read(cls, path: str | Path) -> "InstallationConfig":
        source = _absolute(str(path), "installation config path")
        try:
            value = _strict_json_mapping(
                _snapshot_regular_file(source, "installation config"),
                "installation config",
            )
        except (PortableInstallError, ValueError) as error:
            raise PortableInstallError(
                "installation config is unreadable or invalid"
            ) from error
        return cls.load(value, source_path=source)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "consumer_id": self.consumer_id,
            "platform": self.platform,
            "installation_mode": self.installation_mode,
            "install_root": str(self.install_root),
            "state_root": str(self.state_root),
            "data_root": str(self.data_root),
            "service_root": str(self.service_root),
            "inventory_path": str(self.inventory_path),
            "python_executable": str(self.python_executable),
            "npx_executable": str(self.npx_executable),
            "uvx_executable": str(self.uvx_executable),
            "zsh_executable": str(self.zsh_executable),
            "credential_resolver": self.credential_resolver.to_dict(),
            "services": [item.to_dict() for item in self.services],
            "timers": [item.to_dict() for item in self.timers],
            "health_checks": [item.to_dict() for item in self.health_checks],
        }

    @property
    def config_digest(self) -> str:
        return digest(self.to_dict())


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
    except OSError as error:
        raise PortableInstallError(f"cannot read file: {path}") from error
    return hasher.hexdigest()


def _snapshot_regular_file(path: Path, label: str) -> bytes:
    """Read one protected regular-file generation without reopening it by name."""
    descriptor = -1
    try:
        if path.is_symlink():
            raise PortableInstallError(f"{label} must not be a symlink")
        resolved = path.resolve(strict=True)
        current = Path(resolved.anchor)
        allowed_owners = {0, os.geteuid()}
        for part in resolved.parts[1:-1]:
            current /= part
            metadata = current.lstat()
            sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
            if current.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                raise PortableInstallError(f"{label} ancestry is not protected")
            if metadata.st_uid not in allowed_owners or (
                metadata.st_mode & 0o022 and not sticky_root
            ):
                raise PortableInstallError(f"{label} ancestry is not protected")
            _reject_extended_acl(current, f"{label} ancestry")
        observed = path.lstat()
        descriptor = os.open(
            resolved,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (observed.st_dev, observed.st_ino)
            or metadata.st_uid not in allowed_owners
            or metadata.st_mode & 0o022
        ):
            raise PortableInstallError(f"{label} is not protected")
        _reject_extended_acl(resolved, label)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 16 * 1024 * 1024:
                raise PortableInstallError(f"{label} is too large")
            chunks.append(chunk)
        return b"".join(chunks)
    except PortableInstallError:
        raise
    except OSError as error:
        raise PortableInstallError(f"{label} is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _regular_file(
    path: Path, label: str, *, executable: bool = False
) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PortableInstallError(f"{label} is unavailable") from error
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise PortableInstallError(f"{label} must be a regular non-symlink file")
    if executable and not metadata.st_mode & stat.S_IXUSR:
        raise PortableInstallError(f"{label} must be executable")
    return metadata


def _verify_owned_file(path: Path, label: str, *, mode: int) -> None:
    metadata = _regular_file(path, label)
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o777 != mode:
        raise PortableInstallError(f"{label} protection differs")
    _reject_extended_acl(path, label)


@functools.cache
def _darwin_acl_library() -> Any:
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    library.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
    library.acl_get_file.restype = ctypes.c_void_p
    library.acl_free.argtypes = [ctypes.c_void_p]
    library.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
    library.acl_to_text.restype = ctypes.c_void_p
    return library


def _reject_extended_acl(path: Path, label: str) -> None:
    if sys.platform == "darwin":
        library = _darwin_acl_library()
        ctypes.set_errno(0)
        acl = library.acl_get_file(os.fsencode(path), 0x00000100)
        if acl:
            length = ctypes.c_ssize_t()
            text_pointer = library.acl_to_text(acl, ctypes.byref(length))
            try:
                if not text_pointer:
                    raise PortableInstallError(f"{label} ACL state is unavailable")
                acl_text = ctypes.string_at(text_pointer, length.value).decode(
                    "utf-8", errors="strict"
                )
            finally:
                if text_pointer:
                    library.acl_free(text_pointer)
                library.acl_free(acl)
            for entry in acl_text.splitlines():
                if "allow" in entry.replace(":", " ").split():
                    raise PortableInstallError(
                        f"{label} must not grant extended ACL authority"
                    )
            return
        observed_errno = ctypes.get_errno()
        if observed_errno not in {0, errno.ENOENT}:
            raise PortableInstallError(f"{label} ACL state is unavailable")
        return
    listxattr = getattr(os, "listxattr", None)
    if listxattr is None:
        return
    try:
        attributes = set(listxattr(path, follow_symlinks=False))
    except OSError as error:
        raise PortableInstallError(f"{label} ACL state is unavailable") from error
    if attributes & {"system.posix_acl_access", "system.posix_acl_default"}:
        raise PortableInstallError(f"{label} must not grant extended ACL authority")


def _protected_executable_bytes(path: Path, label: str, expected_digest: str) -> bytes:
    if path.is_symlink():
        raise PortableInstallError(f"{label} must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
        current = Path(resolved.anchor)
        allowed_owners = {0, os.geteuid()}
        for part in resolved.parts[1:-1]:
            current /= part
            metadata = current.lstat()
            sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
            if current.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                raise PortableInstallError(f"{label} ancestry is not protected")
            if metadata.st_uid not in allowed_owners or (
                metadata.st_mode & 0o022 and not sticky_root
            ):
                raise PortableInstallError(f"{label} ancestry is not protected")
            _reject_extended_acl(current, f"{label} ancestry")
        descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise PortableInstallError(f"{label} is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        observed = resolved.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (observed.st_dev, observed.st_ino)
            or metadata.st_uid not in allowed_owners
            or metadata.st_mode & 0o022
            or not metadata.st_mode & 0o111
        ):
            raise PortableInstallError(f"{label} is not protected")
        _reject_extended_acl(resolved, label)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
    except OSError as error:
        raise PortableInstallError(f"{label} is unreadable") from error
    finally:
        os.close(descriptor)
    if hashlib.sha256(content).hexdigest() != expected_digest:
        raise PortableInstallError(f"{label} digest mismatch")
    return content


def _protected_executable_path(path: Path, label: str) -> Path:
    try:
        current = Path(path.anchor)
        allowed_owners = {0, os.geteuid()}
        for part in path.parts[1:-1]:
            current /= part
            metadata = current.lstat()
            sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
            if current.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                raise PortableInstallError(f"{label} ancestry is not protected")
            if metadata.st_uid not in allowed_owners or (
                metadata.st_mode & 0o022 and not sticky_root
            ):
                raise PortableInstallError(f"{label} ancestry is not protected")
            _reject_extended_acl(current, f"{label} ancestry")
        resolved = path.resolve(strict=True)
        current = Path(resolved.anchor)
        for part in resolved.parts[1:-1]:
            current /= part
            metadata = current.lstat()
            sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
            if current.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                raise PortableInstallError(f"{label} ancestry is not protected")
            if metadata.st_uid not in allowed_owners or (
                metadata.st_mode & 0o022 and not sticky_root
            ):
                raise PortableInstallError(f"{label} ancestry is not protected")
            _reject_extended_acl(current, f"{label} ancestry")
        descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise PortableInstallError(f"{label} is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        observed = resolved.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (observed.st_dev, observed.st_ino)
            or metadata.st_uid not in allowed_owners
            or metadata.st_mode & 0o022
            or not metadata.st_mode & 0o111
        ):
            raise PortableInstallError(f"{label} is not protected")
        _reject_extended_acl(resolved, label)
    except OSError as error:
        raise PortableInstallError(f"{label} is unavailable") from error
    finally:
        os.close(descriptor)
    return resolved


def _validated_python_runtime(path: Path) -> Path:
    resolved = _protected_executable_path(path, "managed Python")
    try:
        completed = subprocess.run(
            (
                str(resolved),
                "-I",
                "-c",
                (
                    "import sys; print('hindsight-managed-python:' + "
                    "':'.join(map(str, sys.version_info[:3])))"
                ),
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            timeout=10,
        )
        output = (completed.stdout or b"").decode("ascii", errors="strict").strip()
        prefix = "hindsight-managed-python:"
        version = tuple(int(part) for part in output.removeprefix(prefix).split(":"))
        if (
            completed.returncode != 0
            or not output.startswith(prefix)
            or len(version) != 3
            or version < (3, 11, 0)
        ):
            raise ValueError
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        raise PortableInstallError(
            "managed Python must be a working Python 3.11 or newer"
        ) from error
    return resolved


def _release_manifest(root: Path, version: str) -> dict[str, Any]:
    if VERSION.fullmatch(version) is None:
        raise PortableInstallError("release version is invalid")
    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise PortableInstallError("release root is unavailable") from error
    if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink():
        raise PortableInstallError("release root must be a non-symlink directory")
    entries: list[dict[str, Any]] = []
    try:
        paths = sorted(
            root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
        )
    except OSError as error:
        raise PortableInstallError("release tree is unreadable") from error
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if relative == ".hindsight-staging-owner":
            raise PortableInstallError("release tree uses a reserved path")
        try:
            metadata = path.lstat()
        except OSError as error:
            raise PortableInstallError("release tree is unreadable") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise PortableInstallError(f"release tree contains symlink: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise PortableInstallError(
                f"release tree contains unsupported entry: {relative}"
            )
        entries.append(
            {
                "path": relative,
                "sha256": _sha256(path),
                "size": metadata.st_size,
                "executable": bool(metadata.st_mode & stat.S_IXUSR),
            }
        )
    if not entries:
        raise PortableInstallError("release tree is empty")
    entrypoint = root / "bin" / "hindsight-memory"
    _regular_file(entrypoint, "release hindsight-memory entrypoint", executable=True)
    manifest = {"schema_version": 1, "version": version, "files": entries}
    return {**manifest, "release_digest": digest(manifest)}


def _release_record_root(install_root: Path, release: Mapping[str, Any]) -> Path:
    required = {"version", "release_digest", "release_path", "manifest"}
    if set(release) != required:
        raise PortableInstallError("release record is invalid")
    version = release.get("version")
    release_digest = release.get("release_digest")
    manifest = release.get("manifest")
    if (
        not isinstance(version, str)
        or VERSION.fullmatch(version) is None
        or not isinstance(release_digest, str)
        or SHA256.fullmatch(release_digest) is None
        or not isinstance(manifest, Mapping)
        or set(manifest) != {"schema_version", "version", "files", "release_digest"}
        or type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or manifest.get("version") != version
        or manifest.get("release_digest") != release_digest
    ):
        raise PortableInstallError("release record is invalid")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PortableInstallError("release record is invalid")
    observed_paths: set[str] = set()
    for entry in files:
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "sha256",
            "size",
            "executable",
        }:
            raise PortableInstallError("release record is invalid")
        path = entry.get("path")
        if (
            not isinstance(path, str)
            or path == ".hindsight-staging-owner"
            or PurePosixPath(path).is_absolute()
            or not PurePosixPath(path).parts
            or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
            or path in observed_paths
            or not isinstance(entry.get("sha256"), str)
            or SHA256.fullmatch(entry["sha256"]) is None
            or type(entry.get("size")) is not int
            or entry["size"] < 0
            or type(entry.get("executable")) is not bool
        ):
            raise PortableInstallError("release record is invalid")
        observed_paths.add(path)
    entrypoints = [
        entry
        for entry in files
        if entry["path"] == "bin/hindsight-memory" and entry["executable"]
    ]
    unsigned_manifest = {
        "schema_version": manifest["schema_version"],
        "version": manifest["version"],
        "files": files,
    }
    if len(entrypoints) != 1 or digest(unsigned_manifest) != release_digest:
        raise PortableInstallError("release record is invalid")
    expected = f"releases/{version}-{release_digest[:16]}"
    if release.get("release_path") != expected:
        raise PortableInstallError("release path is invalid")
    return install_root / expected


def _safe_directory(
    path: Path, label: str, *, create: bool, private_final: bool = False
) -> None:
    if not path.is_absolute():
        raise PortableInstallError(f"{label} must be absolute")
    allowed_owners = {0, os.geteuid()}
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current /= part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                if not create:
                    raise
                current.mkdir(mode=0o700)
                metadata = current.lstat()
            sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
            if stat.S_ISLNK(metadata.st_mode):
                if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                    raise PortableInstallError(f"{label} ancestry is unsafe")
                continue
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in allowed_owners
                or (metadata.st_mode & 0o022 and not sticky_root)
            ):
                raise PortableInstallError(f"{label} ancestry is unsafe")
            _reject_extended_acl(current, label)
        final = path.lstat()
        if stat.S_ISLNK(final.st_mode) or not stat.S_ISDIR(final.st_mode):
            raise PortableInstallError(f"{label} must be a non-symlink directory")
        if private_final and (final.st_uid != os.geteuid() or final.st_mode & 0o077):
            raise PortableInstallError(
                f"{label} must be owned by the current user and private"
            )
    except PortableInstallError:
        raise
    except OSError as error:
        raise PortableInstallError(f"{label} is unavailable") from error


def _mkdir_private(path: Path) -> None:
    try:
        _safe_directory(
            path,
            f"managed directory {path}",
            create=True,
            private_final=True,
        )
    except PortableInstallError as error:
        raise PortableInstallError(
            f"cannot create private directory: {path}: {error}"
        ) from error


def _atomic_write(
    path: Path, content: bytes, mode: int, *, private_parent: bool = True
) -> None:
    if private_parent:
        _mkdir_private(path.parent)
    else:
        _safe_directory(path.parent, f"managed directory {path.parent}", create=True)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        temporary = None
        parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError as error:
        raise PortableInstallError(f"cannot publish file: {path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_write(path, canonical_bytes(value) + b"\n", 0o600)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise PortableInstallError(f"{label} is unreadable or invalid") from error
    return _strict_json_mapping(payload, label)


def _copy_release(
    source: Path,
    destination: Path,
    manifest: Mapping[str, Any],
    *,
    temporary: Path | None = None,
) -> None:
    if destination.exists() or destination.is_symlink():
        existing = _release_manifest(destination, str(manifest["version"]))
        if existing != manifest:
            raise PortableInstallError("immutable release destination already differs")
        return
    temporary = temporary or (
        destination.parent / f".{destination.name}.candidate-{os.getpid()}"
    )
    staging_sidecar = temporary.parent / f"{temporary.name}.owner"
    if (
        temporary.exists()
        or temporary.is_symlink()
        or staging_sidecar.exists()
        or staging_sidecar.is_symlink()
    ):
        raise PortableInstallError("release staging path already exists")
    _mkdir_private(destination.parent)
    try:
        staging_identity = {
            "schema_version": 1,
            "release_digest": manifest["release_digest"],
            "staging_name": temporary.name,
        }
        _atomic_json(staging_sidecar, staging_identity)
        temporary.mkdir(mode=0o700)
        staging_marker = temporary / ".hindsight-staging-owner"
        _atomic_write(
            staging_marker,
            canonical_bytes(staging_identity) + b"\n",
            0o600,
        )
        for entry in manifest["files"]:
            source_file = source / entry["path"]
            target = temporary / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _regular_file(source_file, f"release file {entry['path']}")
            with (
                source_file.open("rb") as input_handle,
                target.open("xb") as output_handle,
            ):
                shutil.copyfileobj(input_handle, output_handle, 1024 * 1024)
                output_handle.flush()
                if _sha256(target) != entry["sha256"]:
                    raise PortableInstallError("release changed while copying")
                os.fchmod(
                    output_handle.fileno(),
                    0o500 if entry["executable"] else 0o400,
                )
                os.fsync(output_handle.fileno())
        staging_marker.unlink()
        _fsync_directory(temporary)
        directories = sorted(
            (item for item in temporary.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for directory in directories:
            directory.chmod(0o500)
            _fsync_directory(directory)
        temporary.chmod(0o500)
        _fsync_directory(temporary)
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
        staging_sidecar.unlink()
        _fsync_directory(destination.parent)
    except (OSError, shutil.Error, PortableInstallError) as error:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        try:
            staging_sidecar.unlink()
        except OSError:
            pass
        if isinstance(error, PortableInstallError):
            raise
        raise PortableInstallError("release publication failed") from error


RUNTIME_LIBRARY_END = "# HINDSIGHT_RUNTIME_LIBRARY_END"


WRAPPER = r"""import ctypes, hashlib, json, os, stat, sys
from pathlib import Path

def reject_acl(path):
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        library.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
        library.acl_get_file.restype = ctypes.c_void_p
        library.acl_free.argtypes = [ctypes.c_void_p]
        library.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
        library.acl_to_text.restype = ctypes.c_void_p
        acl = library.acl_get_file(os.fsencode(path), 0x00000100)
        if not acl:
            if ctypes.get_errno() not in {0, 2}:
                raise SystemExit("installed ACL state is unavailable")
            return
        length = ctypes.c_ssize_t()
        text_pointer = library.acl_to_text(acl, ctypes.byref(length))
        try:
            if not text_pointer:
                raise SystemExit("installed ACL state is unavailable")
            text = ctypes.string_at(text_pointer, length.value).decode("utf-8")
        finally:
            if text_pointer:
                library.acl_free(text_pointer)
            library.acl_free(acl)
        if any("allow" in entry.replace(":", " ").split() for entry in text.splitlines()):
            raise SystemExit("installed path grants extended ACL authority")
        return
    listxattr = getattr(os, "listxattr", None)
    if listxattr and set(listxattr(path, follow_symlinks=False)) & {"system.posix_acl_access", "system.posix_acl_default"}:
        raise SystemExit("installed path grants extended ACL authority")

def protected(path, mode, directory=False):
    metadata = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if path.is_symlink() or not expected_type(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o777 != mode:
        raise SystemExit("installed path protection differs")
    reject_acl(path)
    return metadata

# HINDSIGHT_RUNTIME_LIBRARY_END
root = Path(__file__).resolve().parent
protected(root, 0o700, directory=True)
protected(Path(__file__), 0o500)
protected(root / "install-state.json", 0o600)
state = json.loads((root / "install-state.json").read_text(encoding="utf-8"))
owned = state.get("owned_install_files", {})
if owned.get(str(Path(__file__))) != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
    raise SystemExit("installed wrapper digest mismatch")
current = state.get("current")
if not isinstance(current, dict):
    raise SystemExit("installation state is invalid")
identity = {key: current.get(key) for key in ("version", "release_digest", "release_path")}
transaction = state.get("transaction")
selected = current
if transaction is None:
    protected(root / "active.json", 0o600)
    active = json.loads((root / "active.json").read_text(encoding="utf-8"))
    if active != identity:
        raise SystemExit("installation state is not launchable")
else:
    lifecycle_commands = {"install", "upgrade", "verify", "rollback", "uninstall"}
    if not sys.argv[1:] or sys.argv[1] not in lifecycle_commands:
        raise SystemExit("installation permits only lifecycle recovery commands")
    if not isinstance(transaction, dict) or transaction.get("operation") not in {"install", "upgrade", "rollback"} or transaction.get("candidate_release_digest") != current.get("release_digest"):
        raise SystemExit("installation recovery state is invalid")
    if transaction.get("operation") in {"upgrade", "rollback"}:
        selected = state.get("last_known_good")
        if not isinstance(selected, dict) or selected.get("release_digest") != transaction.get("previous_release_digest"):
            raise SystemExit("installation recovery state is invalid")
releases = state.get("releases")
if not isinstance(releases, dict) or releases.get(selected.get("release_digest")) != selected:
    raise SystemExit("installation release state is invalid")
expected_release_path = f"releases/{selected.get('version')}-{str(selected.get('release_digest'))[:16]}"
if selected.get("release_path") != expected_release_path:
    raise SystemExit("installation release path is invalid")
release = (root / selected["release_path"]).resolve(strict=True)
if root.resolve() not in release.parents:
    raise SystemExit("active release is outside the installation root")
target = release / "bin" / "hindsight-memory"
for directory in (release, target.parent):
    protected(directory, 0o500, directory=True)
entry = [item for item in selected.get("manifest", {}).get("files", []) if item.get("path") == "bin/hindsight-memory"]
if len(entry) != 1:
    raise SystemExit("active entrypoint manifest is invalid")
protected(target, 0o500 if entry[0].get("executable") else 0o400)
value = hashlib.sha256(target.read_bytes()).hexdigest()
if value != entry[0].get("sha256"):
    raise SystemExit("active entrypoint digest mismatch")
with target.open("rb") as handle:
    first_line = handle.readline(256).lower()
shebang_words = first_line[2:].strip().split() if first_line.startswith(b"#!") else []
python_entrypoint = target.suffix == ".py" or any(
    Path(os.fsdecode(word)).name.startswith("python") for word in shebang_words[:3]
)
if python_entrypoint:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("PYTHON")
    }
    os.execve(sys.executable, [sys.executable, "-I", str(target), *sys.argv[1:]], environment)
os.execv(str(target), [str(target), *sys.argv[1:]])
"""


SERVICE_LAUNCHER = r"""#!/usr/bin/env python3
import ctypes, fcntl, hashlib, json, os, pwd, selectors, signal, stat, subprocess, sys, time
from pathlib import Path, PurePosixPath

def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)

def reject_acl(path):
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        library.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
        library.acl_get_file.restype = ctypes.c_void_p
        library.acl_free.argtypes = [ctypes.c_void_p]
        library.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
        library.acl_to_text.restype = ctypes.c_void_p
        acl = library.acl_get_file(os.fsencode(path), 0x00000100)
        if not acl:
            if ctypes.get_errno() not in {0, 2}:
                raise ValueError("ACL state unavailable")
            return
        length = ctypes.c_ssize_t()
        text_pointer = library.acl_to_text(acl, ctypes.byref(length))
        try:
            if not text_pointer:
                raise ValueError("ACL state unavailable")
            text = ctypes.string_at(text_pointer, length.value).decode("utf-8")
        finally:
            if text_pointer:
                library.acl_free(text_pointer)
            library.acl_free(acl)
        if any("allow" in entry.replace(":", " ").split() for entry in text.splitlines()):
            raise ValueError("extended ACL authority")
        return
    listxattr = getattr(os, "listxattr", None)
    if listxattr and set(listxattr(path, follow_symlinks=False)) & {"system.posix_acl_access", "system.posix_acl_default"}:
        raise ValueError("extended ACL authority")

def protected(path, mode, directory=False):
    metadata = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if path.is_symlink() or not expected_type(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o777 != mode:
        raise ValueError("protection differs")
    reject_acl(path)
    return metadata

def protected_ancestry(directory, *, allow_root_symlinks):
    allowed_owners = {0, os.geteuid()}
    current = Path(directory.anchor)
    for part in directory.parts[1:]:
        current /= part
        metadata = current.lstat()
        sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
        if current.is_symlink():
            if not allow_root_symlinks or metadata.st_uid != 0:
                raise ValueError
        elif not stat.S_ISDIR(metadata.st_mode):
            raise ValueError
        if metadata.st_uid not in allowed_owners or (
            metadata.st_mode & 0o022 and not sticky_root
        ):
            raise ValueError
        reject_acl(current)

def protected_external_executable(alias, expected):
    if not alias.is_absolute():
        raise SystemExit("managed npx alias is invalid")
    allowed_owners = {0, os.geteuid()}
    try:
        resolved = alias.resolve(strict=True)
        if resolved != expected:
            raise ValueError
        for candidate in (alias.parent, resolved.parent):
            protected_ancestry(candidate, allow_root_symlinks=True)
        descriptor = os.open(
            resolved,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            metadata = os.fstat(descriptor)
            observed = resolved.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino)
                != (observed.st_dev, observed.st_ino)
                or metadata.st_uid not in allowed_owners
                or metadata.st_mode & 0o022
                or not metadata.st_mode & 0o111
            ):
                raise ValueError
            reject_acl(resolved)
        finally:
            os.close(descriptor)
    except (OSError, ValueError):
        raise SystemExit("managed npx binding is not protected") from None
    return alias.parent

def snapshot_json(path, mode):
    observed = path.lstat()
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (observed.st_dev, observed.st_ino)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o777 != mode
        ):
            raise ValueError("identity changed")
        reject_acl(path)
        content = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > 16 * 1024 * 1024:
                raise ValueError("oversized config")
    finally:
        os.close(descriptor)
    raw = bytes(content)
    return json.loads(raw, object_pairs_hook=pairs), hashlib.sha256(raw).hexdigest()

def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()

def sha256_fd(descriptor):
    value = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        value.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return value.hexdigest()

def protected_resolver(path, expected_digest):
    allowed_owners = {0, os.geteuid()}
    if path.is_symlink():
        raise ValueError("unsafe resolver executable")
    path = path.resolve(strict=True)
    protected_ancestry(path.parent, allow_root_symlinks=False)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        observed = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (observed.st_dev, observed.st_ino)
            or metadata.st_uid not in allowed_owners
            or metadata.st_mode & 0o022
            or not metadata.st_mode & 0o111
            or sha256_fd(descriptor) != expected_digest
        ):
            raise ValueError("unsafe resolver executable")
        reject_acl(path)
        return path
    finally:
        os.close(descriptor)

def resolver_environment(account):
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
    }
    raw_runtime = os.environ.get("XDG_RUNTIME_DIR")
    raw_bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not raw_runtime or not raw_bus or any(
        character in raw_runtime + raw_bus for character in "\x00\r\n"
    ):
        return environment
    runtime = Path(raw_runtime)
    try:
        metadata = runtime.lstat()
        resolved = runtime.resolve(strict=True)
    except OSError:
        return environment
    if (
        not runtime.is_absolute()
        or runtime.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
        or not raw_bus.startswith("unix:path=")
    ):
        return environment
    bus_path = Path(raw_bus.removeprefix("unix:path="))
    try:
        bound_parent = bus_path.parent.resolve(strict=True)
    except OSError:
        return environment
    if not bus_path.is_absolute() or bus_path.name != "bus" or bound_parent != resolved:
        return environment
    environment.update(
        {"XDG_RUNTIME_DIR": raw_runtime, "DBUS_SESSION_BUS_ADDRESS": raw_bus}
    )
    return environment

def resolve_credentials(path, request, environment, timeout_seconds=30):
    limit = 1024 * 1024
    process = subprocess.Popen(
        [str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd="/", env=environment,
        start_new_session=True,
    )
    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    def signal_group(signum):
        try:
            os.killpg(process.pid, signum)
            return
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        try:
            observed = subprocess.run(
                ["/usr/bin/ps", "-axo", "pid=,pgid="],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return
        for line in observed.stdout.splitlines() if observed.returncode == 0 else ():
            fields = line.split()
            if len(fields) != 2 or not all(field.isascii() and field.isdigit() for field in fields):
                continue
            pid, process_group = map(int, fields)
            if process_group != process.pid or pid == os.getpid():
                continue
            try:
                os.kill(pid, signum)
            except (PermissionError, ProcessLookupError):
                pass
    def terminate_group():
        signal_group(signal.SIGTERM)
        grace_deadline = time.monotonic() + 1
        while time.monotonic() < grace_deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                pass
            time.sleep(0.02)
        else:
            signal_group(signal.SIGKILL)
        try:
            process.wait(timeout=1)
        except subprocess.SubprocessError:
            pass
    def relay_signal(signum, _frame):
        terminate_group()
        raise SystemExit(128 + signum)
    previous_handlers = {
        signum: signal.signal(signum, relay_signal)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        with selectors.DefaultSelector() as selector:
            input_descriptor = process.stdin.fileno()
            output_descriptor = process.stdout.fileno()
            os.set_blocking(input_descriptor, False)
            os.set_blocking(output_descriptor, False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "input")
            selector.register(process.stdout, selectors.EVENT_READ, "output")
            input_offset = 0
            output_open = True
            while output_open or not process.stdin.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                events = selector.select(remaining)
                if not events:
                    raise TimeoutError
                for key, _mask in events:
                    if key.data == "input":
                        try:
                            written = os.write(input_descriptor, request[input_offset:])
                        except BrokenPipeError:
                            written = 0
                            input_offset = len(request)
                        except BlockingIOError:
                            continue
                        else:
                            input_offset += written
                        if input_offset == len(request):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        try:
                            chunk = os.read(
                                output_descriptor,
                                min(65536, limit + 1 - len(output)),
                            )
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(process.stdout)
                            output_open = False
                        else:
                            output.extend(chunk)
                            if len(output) > limit:
                                raise ValueError("oversized response")
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise ValueError("resolver failed")
        return bytes(output)
    except (BrokenPipeError, OSError, subprocess.SubprocessError, TimeoutError, ValueError):
        terminate_group()
        raise SystemExit("credential resolution failed") from None
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if process.stdout is not None:
            process.stdout.close()

# HINDSIGHT_RUNTIME_LIBRARY_END
if len(sys.argv) != 5 or sys.argv[1] != "--config" or sys.argv[3] not in {"--service", "--timer", "--health"}:
    raise SystemExit("invalid launcher arguments")
root = Path(__file__).resolve().parent
try:
    protected(root, 0o700, directory=True)
    protected(Path(__file__), 0o500)
except (OSError, UnicodeDecodeError, ValueError):
    raise SystemExit("managed launcher is not protected") from None
config_path = Path(sys.argv[2])
if config_path != root / "managed-config.json":
    raise SystemExit("managed config path is invalid")
try:
    config, config_digest = snapshot_json(config_path, 0o500)
except (OSError, UnicodeDecodeError, ValueError):
    raise SystemExit("managed config is invalid")
collection = {"--service": "services", "--timer": "timers", "--health": "health_checks"}.get(sys.argv[3])
identity_key = {"services": "service_id", "timers": "timer_id", "health_checks": "check_id"}.get(collection)
if collection is None or identity_key is None:
    raise SystemExit("invalid managed launch kind")
matches = [item for item in config[collection] if item.get(identity_key) == sys.argv[4]]
if len(matches) != 1:
    raise SystemExit("unknown managed launch identity")
spec = matches[0]
try:
    active, _active_digest = snapshot_json(root / "active.json", 0o600)
    state, _state_digest = snapshot_json(root / "install-state.json", 0o600)
except (OSError, UnicodeDecodeError, ValueError):
    raise SystemExit("installation state is not protected") from None
if state.get("owned_install_files", {}).get(str(Path(__file__))) != sha256(Path(__file__)):
    raise SystemExit("managed launcher digest mismatch")
current = state.get("current")
transaction = state.get("transaction")
if not isinstance(current, dict):
    raise SystemExit("installation state is not launchable")
if transaction is not None and (
    not isinstance(transaction, dict)
    or transaction.get("candidate_release_digest") != current.get("release_digest")
):
    raise SystemExit("installation transaction binding is invalid")
if transaction is not None:
    lock_path = Path(config["state_root"]) / "portable-install.lock"
    try:
        lock_observed = lock_path.lstat()
        lock_fd = os.open(
            lock_path,
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            lock_metadata = os.fstat(lock_fd)
            if (
                not stat.S_ISREG(lock_metadata.st_mode)
                or (lock_metadata.st_dev, lock_metadata.st_ino)
                != (lock_observed.st_dev, lock_observed.st_ino)
                or lock_metadata.st_uid != os.geteuid()
                or lock_metadata.st_mode & 0o077
            ):
                raise SystemExit("portable installation lock identity is invalid")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            raise SystemExit("pending candidate has no live install manager")
        finally:
            os.close(lock_fd)
    except OSError:
        raise SystemExit("portable installation lock identity is invalid") from None
active_identity = {key: current.get(key) for key in ("version", "release_digest", "release_path")}
if active != active_identity:
    raise SystemExit("active release pointer differs from verified state")
if config_digest != state.get("config_file_digest"):
    raise SystemExit("managed config digest mismatch")
inventory_path = root / "managed-inventory.json"
try:
    inventory_observed = inventory_path.lstat()
    inventory_fd = os.open(
        inventory_path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        inventory_metadata = os.fstat(inventory_fd)
        if (
            inventory_path.is_symlink()
            or not stat.S_ISREG(inventory_metadata.st_mode)
            or (inventory_metadata.st_dev, inventory_metadata.st_ino)
            != (inventory_observed.st_dev, inventory_observed.st_ino)
            or inventory_metadata.st_uid != os.geteuid()
            or inventory_metadata.st_mode & 0o777 != 0o500
            or sha256_fd(inventory_fd) != state.get("inventory_digest")
        ):
            raise ValueError
        reject_acl(inventory_path)
    finally:
        os.close(inventory_fd)
except (OSError, ValueError):
    raise SystemExit("managed inventory binding is invalid") from None
release = (root / active["release_path"]).resolve(strict=True)
if root.resolve() not in release.parents:
    raise SystemExit("active release is outside the installation root")
relative = PurePosixPath(spec["entrypoint"])
if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
    raise SystemExit("managed entrypoint is invalid")
target = release.joinpath(*relative.parts)
metadata = target.lstat()
entry_matches = [item for item in current.get("manifest", {}).get("files", []) if item.get("path") == relative.as_posix()]
expected_mode = 0o500 if len(entry_matches) == 1 and entry_matches[0].get("executable") else 0o400
try:
    protected(release, 0o500, directory=True)
    current_directory = target.parent
    while current_directory != release:
        protected(current_directory, 0o500, directory=True)
        current_directory = current_directory.parent
    protected(target, expected_mode)
except (OSError, ValueError):
    raise SystemExit("managed entrypoint is invalid")
if len(entry_matches) != 1 or sha256(target) != entry_matches[0].get("sha256"):
    raise SystemExit("managed entrypoint digest mismatch")
resolver = config["credential_resolver"]
resolver_path = root / "credential-resolver"
try:
    resolver_path = protected_resolver(resolver_path, resolver["sha256"])
except (OSError, ValueError):
    raise SystemExit("credential resolver is not protected") from None
credentials = spec.get("credentials", [])
values = {}
try:
    account = pwd.getpwuid(os.geteuid())
except KeyError:
    raise SystemExit("managed account identity is unavailable") from None
account_environment = {"HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name}
if credentials:
    request = json.dumps({"schema_version": 1, "credentials": credentials}, sort_keys=True, separators=(",", ":")).encode()
    try:
        response = json.loads(
            resolve_credentials(resolver_path, request, resolver_environment(account)), object_pairs_hook=pairs
        )
    except (UnicodeDecodeError, ValueError):
        raise SystemExit("credential resolver response is invalid") from None
    if set(response) != {"schema_version", "values"} or type(response["schema_version"]) is not int or response["schema_version"] != 1 or not isinstance(response["values"], dict):
        raise SystemExit("credential resolver response is invalid")
    expected = {item["environment"] for item in credentials}
    if set(response["values"]) != expected or not all(isinstance(value, str) and value and "\x00" not in value for value in response["values"].values()):
        raise SystemExit("credential resolver response is incomplete")
    values = response["values"]
environment = {name: os.environ[name] for name in ("TMPDIR", "LANG", "LC_ALL") if name in os.environ}
for name, value in spec.get("environment", {}).items():
    if value.startswith("release://"):
        relative_value = PurePosixPath(value.removeprefix("release://"))
        if relative_value.is_absolute() or any(part in {"", ".", ".."} for part in relative_value.parts):
            raise SystemExit("managed release environment path is invalid")
        value = str(release.joinpath(*relative_value.parts))
    environment[name] = value
environment.update(values)
environment.update(account_environment)
environment["HINDSIGHT_MEMORY_INVENTORY"] = str(inventory_path)
environment["HINDSIGHT_EMBED_UVX_EXECUTABLE"] = config["uvx_executable"]
try:
    npx_directory = str(
        protected_external_executable(
            Path(state["npx_alias"]), Path(config["npx_executable"])
        )
    )
except (KeyError, TypeError):
    raise SystemExit("managed npx binding is not protected") from None
path_entries = environment.get("PATH", "/usr/bin:/bin").split(":")
environment["PATH"] = ":".join(
    [npx_directory, *(entry for entry in path_entries if entry != npx_directory)]
)
with target.open("rb") as handle:
    first_line = handle.readline(256).lower()
shebang_words = first_line[2:].strip().split() if first_line.startswith(b"#!") else []
python_entrypoint = relative.suffix == ".py" or any(
    Path(os.fsdecode(word)).name.startswith("python") for word in shebang_words[:3]
)
if python_entrypoint:
    argv = [sys.executable, "-I", str(target), *spec.get("arguments", [])]
    os.execve(sys.executable, argv, environment)
zsh_entrypoint = relative.suffix == ".zsh" or any(
    Path(os.fsdecode(word)).name == "zsh" for word in shebang_words[:3]
)
if zsh_entrypoint:
    zsh_executable = config["zsh_executable"]
    argv = [zsh_executable, "-f", str(target), *spec.get("arguments", [])]
    os.execve(zsh_executable, argv, environment)
argv = [str(target), *spec.get("arguments", [])]
os.execve(str(target), argv, environment)
"""


def _systemd_escape(value: str) -> str:
    if "'" in value:
        raise PortableInstallError(
            "systemd unit arguments must not contain apostrophes"
        )
    return shlex.quote(value.replace("\\", "\\\\")).replace("%", "%%")


def _bound_user_environment() -> dict[str, str]:
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    raw_runtime = os.environ.get("XDG_RUNTIME_DIR")
    raw_bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not raw_runtime or not raw_bus:
        return environment
    if any(character in raw_runtime + raw_bus for character in "\x00\r\n"):
        return environment
    runtime = Path(raw_runtime)
    try:
        metadata = runtime.lstat()
        resolved = runtime.resolve(strict=True)
    except OSError:
        return environment
    if (
        not runtime.is_absolute()
        or runtime.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
    ):
        return environment
    if not raw_bus.startswith("unix:path="):
        return environment
    bus_path = Path(raw_bus.removeprefix("unix:path="))
    try:
        bound_parent = bus_path.parent.resolve(strict=True)
    except OSError:
        return environment
    if not bus_path.is_absolute() or bus_path.name != "bus" or bound_parent != resolved:
        return environment
    environment.update(
        {
            "XDG_RUNTIME_DIR": raw_runtime,
            "DBUS_SESSION_BUS_ADDRESS": raw_bus,
        }
    )
    return environment


class PortableInstallationManager:
    """Install and operate one consumer's immutable Hindsight releases."""

    def __init__(
        self,
        config: InstallationConfig,
        *,
        command_runner: Callable[[tuple[str, ...]], str | None] | None = None,
        health_runner: Callable[[Mapping[str, Any], Mapping[str, Any]], bool]
        | None = None,
    ) -> None:
        self.config = config
        self._command_runner = command_runner or self._default_command_runner
        self._health_runner = health_runner

    def _preflight_lifecycle(self) -> None:
        if os.getuid() != os.geteuid() or os.geteuid() == 0:
            raise PortableInstallError(
                "portable lifecycle requires a matching unprivileged user identity"
            )
        if self.config.platform != "systemd-user":
            return
        discovered = _systemd_user_service_root(self._command_runner).resolve(
            strict=False
        )
        configured = self.config.service_root.resolve(strict=False)
        if configured != discovered:
            raise PortableInstallError(
                "systemd-user service_root is not searched by the user manager"
            )

    @property
    def _state_path(self) -> Path:
        return self.config.install_root / "install-state.json"

    @property
    def _active_path(self) -> Path:
        return self.config.install_root / "active.json"

    @property
    def _transaction_path(self) -> Path:
        return self.config.state_root / "portable-install-transaction.json"

    @property
    def _uninstall_transaction_path(self) -> Path:
        return self.config.state_root / "portable-uninstall-transaction.json"

    @property
    def _binding_migration_path(self) -> Path:
        return self.config.state_root / "portable-binding-migration.json"

    @property
    def _uninstall_tombstone_path(self) -> Path:
        suffix = hashlib.sha256(self.config.consumer_id.encode()).hexdigest()[:16]
        return self.config.install_root.with_name(
            f".{self.config.install_root.name}.uninstall-{suffix}"
        )

    @property
    def _lock_path(self) -> Path:
        return self.config.state_root / "portable-install.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        _mkdir_private(self.config.state_root)
        descriptor = -1
        try:
            descriptor = os.open(
                self._lock_path,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            metadata = os.fstat(descriptor)
            observed = self._lock_path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino)
                != (observed.st_dev, observed.st_ino)
                or metadata.st_uid != os.geteuid()
            ):
                raise PortableInstallError(
                    "portable installation lock identity is invalid"
                )
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except PortableInstallError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            raise PortableInstallError(
                "portable installation lock is unavailable"
            ) from error
        try:
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _default_command_runner(argv: tuple[str, ...]) -> str:
        environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
        if argv[:2] == ("/usr/bin/systemctl", "--user"):
            environment = _bound_user_environment()
        try:
            completed = subprocess.run(
                argv,
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd="/",
                env=environment,
                timeout=SERVICE_MANAGER_COMMAND_TIMEOUT_SECONDS,
            )
            return completed.stdout.decode("utf-8", errors="strict")
        except subprocess.CalledProcessError as error:
            command = Path(argv[0]).name
            if command == "systemctl" and argv[1:2] == ("--user",):
                if len(argv) > 2:
                    command += f" --user {argv[2]}"
                else:
                    command += " --user"
            elif len(argv) > 1:
                command += f" {argv[1]}"
            raise _ManagedServiceCommandError(error.returncode, command) from error
        except (OSError, UnicodeError, subprocess.SubprocessError) as error:
            raise PortableInstallError("managed service command failed") from error

    def _launchd_loaded_manifest(
        self, label: str, expected: Path, *, require_running: bool = False
    ) -> Path | None:
        domain = f"gui/{os.getuid()}"
        try:
            output = self._command_runner(
                ("/bin/launchctl", "print", f"{domain}/{label}")
            )
        except _ManagedServiceCommandError as error:
            if error.returncode in {3, 113}:
                return None
            raise
        if output is None:
            return expected
        match = re.search(r"^\s*path = (.+?)\s*$", output, re.MULTILINE)
        if match is None:
            raise PortableInstallError("launchd loaded plist identity is unavailable")
        loaded = Path(match.group(1).strip().strip('"'))
        try:
            loaded_identity = loaded.resolve(strict=True)
            expected_identity = expected.resolve(strict=True)
        except OSError as error:
            raise PortableInstallError(
                "launchd loaded plist identity is unavailable"
            ) from error
        if loaded_identity != expected_identity:
            raise PortableInstallError("launchd loaded plist is not owned")
        if (
            require_running
            and re.search(r"^\s*state = running\s*$", output, re.MULTILINE) is None
        ):
            raise PortableInstallError(f"managed launchd job is not active: {label}")
        return loaded

    def _assert_systemd_fragment(
        self, unit: str, expected: Path, *, absent_ok: bool = False
    ) -> bool:
        try:
            output = self._command_runner(
                (
                    "/usr/bin/systemctl",
                    "--user",
                    "show",
                    "--property=FragmentPath",
                    "--value",
                    unit,
                )
            )
        except _ManagedServiceCommandError as error:
            if absent_ok and error.returncode == 4:
                return False
            raise
        if output is None:
            return True
        observed = Path(output.strip()) if output.strip() else None
        if observed is None and absent_ok:
            return False
        if observed != expected:
            raise PortableInstallError("systemd loaded fragment is not owned")
        return True

    def _assert_systemd_status(self, unit: str, status: str) -> None:
        try:
            output = self._command_runner(
                ("/usr/bin/systemctl", "--user", f"is-{status}", unit)
            )
        except _ManagedServiceCommandError as error:
            raise PortableInstallError(
                f"systemd unit is not {status}: {unit}"
            ) from error
        if output is not None and output.strip() != status:
            raise PortableInstallError(f"systemd unit is not {status}: {unit}")

    def _verify_service_manager(self) -> None:
        if self.config.platform == "launchd":
            for item in self.config.services:
                manifest = self.config.service_root / f"{item.label}.plist"
                if (
                    self._launchd_loaded_manifest(
                        item.label, manifest, require_running=True
                    )
                    is None
                ):
                    raise PortableInstallError(
                        f"managed launchd job is absent: {item.label}"
                    )
            for item in self.config.timers:
                manifest = self.config.service_root / f"{item.label}.plist"
                if self._launchd_loaded_manifest(item.label, manifest) is None:
                    raise PortableInstallError(
                        f"managed launchd job is absent: {item.label}"
                    )
            return
        for service in self.config.services:
            unit = f"{service.label}.service"
            self._assert_systemd_fragment(unit, self.config.service_root / unit)
            self._assert_systemd_status(unit, "enabled")
            self._assert_systemd_status(unit, "active")
        for timer in self.config.timers:
            companion = f"{timer.label}.service"
            unit = f"{timer.label}.timer"
            self._assert_systemd_fragment(
                companion, self.config.service_root / companion
            )
            self._assert_systemd_fragment(unit, self.config.service_root / unit)
            self._assert_systemd_status(unit, "enabled")
            self._assert_systemd_status(unit, "active")

    def _preflight_service_manager(
        self, *, require_absent: bool = False, absent_ok: bool = False
    ) -> None:
        if require_absent and absent_ok:
            raise PortableInstallError("service-manager preflight mode is invalid")
        if self.config.platform == "launchd":
            domain = f"gui/{os.getuid()}"
            for item in (*self.config.services, *self.config.timers):
                manifest = self.config.service_root / f"{item.label}.plist"
                if require_absent:
                    try:
                        self._command_runner(
                            ("/bin/launchctl", "print", f"{domain}/{item.label}")
                        )
                    except _ManagedServiceCommandError as error:
                        if error.returncode in {3, 113}:
                            continue
                        raise
                    raise PortableInstallError(
                        f"launchd job already exists: {item.label}"
                    )
                loaded = self._launchd_loaded_manifest(item.label, manifest)
                if loaded is None and not absent_ok:
                    raise PortableInstallError(
                        f"managed launchd job is absent: {item.label}"
                    )
            return
        units = [
            *(f"{service.label}.service" for service in self.config.services),
            *(
                unit
                for timer in self.config.timers
                for unit in (f"{timer.label}.service", f"{timer.label}.timer")
            ),
        ]
        for unit in units:
            expected = self.config.service_root / unit
            if require_absent:
                try:
                    output = self._command_runner(
                        (
                            "/usr/bin/systemctl",
                            "--user",
                            "show",
                            "--property=LoadState",
                            "--property=FragmentPath",
                            unit,
                        )
                    )
                except _ManagedServiceCommandError as error:
                    if error.returncode == 4:
                        continue
                    raise
                if output is not None:
                    properties = dict(
                        line.split("=", 1)
                        for line in output.splitlines()
                        if "=" in line
                    )
                    if properties != {
                        "LoadState": "not-found",
                        "FragmentPath": "",
                    }:
                        raise PortableInstallError(
                            f"systemd unit already exists: {unit}"
                        )
                continue
            self._assert_systemd_fragment(unit, expected, absent_ok=absent_ok)

    def _preflight_added_service_manager(
        self, prior_config: InstallationConfig
    ) -> None:
        if self.config.platform == "launchd":
            prior_labels = {
                item.label for item in (*prior_config.services, *prior_config.timers)
            }
            domain = f"gui/{os.getuid()}"
            for item in (*self.config.services, *self.config.timers):
                if item.label in prior_labels:
                    continue
                try:
                    self._command_runner(
                        ("/bin/launchctl", "print", f"{domain}/{item.label}")
                    )
                except _ManagedServiceCommandError as error:
                    if error.returncode in {3, 113}:
                        continue
                    raise
                raise PortableInstallError(f"launchd job already exists: {item.label}")
            return
        prior_units = {
            *(f"{service.label}.service" for service in prior_config.services),
            *(
                unit
                for timer in prior_config.timers
                for unit in (f"{timer.label}.service", f"{timer.label}.timer")
            ),
        }
        desired_units = {
            *(f"{service.label}.service" for service in self.config.services),
            *(
                unit
                for timer in self.config.timers
                for unit in (f"{timer.label}.service", f"{timer.label}.timer")
            ),
        }
        for unit in sorted(desired_units - prior_units):
            try:
                output = self._command_runner(
                    (
                        "/usr/bin/systemctl",
                        "--user",
                        "show",
                        "--property=LoadState",
                        "--property=FragmentPath",
                        unit,
                    )
                )
            except _ManagedServiceCommandError as error:
                if error.returncode == 4:
                    continue
                raise
            if output is not None:
                properties = dict(
                    line.split("=", 1) for line in output.splitlines() if "=" in line
                )
                if properties != {"LoadState": "not-found", "FragmentPath": ""}:
                    raise PortableInstallError(f"systemd unit already exists: {unit}")

    def _quiesce_scheduled_jobs(self) -> None:
        if not self.config.timers:
            return
        self._preflight_service_manager()
        if self.config.platform == "launchd":
            domain = f"gui/{os.getuid()}"
            for timer in self.config.timers:
                self._command_runner(
                    ("/bin/launchctl", "bootout", f"{domain}/{timer.label}")
                )
            return
        for timer in self.config.timers:
            self._command_runner(
                (
                    "/usr/bin/systemctl",
                    "--user",
                    "stop",
                    f"{timer.label}.timer",
                )
            )
            self._command_runner(
                (
                    "/usr/bin/systemctl",
                    "--user",
                    "stop",
                    f"{timer.label}.service",
                )
            )

    def _bootstrap_launchd(
        self,
        domain: str,
        plist: Path,
        *,
        replacing_loaded_job: bool,
    ) -> None:
        deadline = time.monotonic() + 3.0
        while True:
            try:
                self._command_runner(
                    ("/bin/launchctl", "bootstrap", domain, str(plist))
                )
                return
            except _ManagedServiceCommandError as error:
                if (
                    not replacing_loaded_job
                    or error.returncode != 5
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(0.1)

    def _default_health_runner(
        self, check: Mapping[str, Any], _release: Mapping[str, Any]
    ) -> bool:
        try:
            account = pwd.getpwuid(os.geteuid())
            environment = _bound_user_environment()
            environment.update(
                {
                    "HOME": account.pw_dir,
                    "USER": account.pw_name,
                    "LOGNAME": account.pw_name,
                }
            )
            argv = tuple(self._launch_argv("health", str(check["check_id"])))
            deadline = time.monotonic() + float(check["timeout_seconds"])
        except (KeyError, OSError, TypeError, ValueError):
            return False

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            process: subprocess.Popen[bytes] | None = None
            try:
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd="/",
                    env=environment,
                    start_new_session=True,
                )
                returncode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    _signal_process_group(process.pid, signal.SIGTERM)
                except OSError:
                    pass
                grace_deadline = time.monotonic() + 1
                while time.monotonic() < grace_deadline:
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        break
                    except PermissionError:
                        pass
                    time.sleep(0.02)
                else:
                    _signal_process_group(process.pid, signal.SIGKILL)
                try:
                    process.wait(timeout=5)
                except subprocess.SubprocessError:
                    pass
                return False
            except (OSError, subprocess.SubprocessError):
                return False
            if returncode == 0:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.5, remaining))

    def _validate_external_bindings(self) -> BindingSnapshot:
        inventory_snapshot = _snapshot_regular_file(
            self.config.inventory_path, "inventory"
        )
        self._validate_config_source()

        python_executable = _validated_python_runtime(self.config.python_executable)
        npx_executable = _protected_executable_path(
            self.config.npx_executable, "npx executable"
        )
        uvx_executable = _protected_executable_path(
            self.config.uvx_executable, "uvx executable"
        )
        zsh_executable = _protected_executable_path(
            self.config.zsh_executable, "Zsh executable"
        )
        _protected_executable_bytes(
            self.config.credential_resolver.path,
            "credential resolver",
            self.config.credential_resolver.sha256,
        )
        effective_config = self.config.to_dict()
        effective_config.update(
            {
                "python_executable": str(python_executable),
                "npx_executable": str(npx_executable),
                "uvx_executable": str(uvx_executable),
                "zsh_executable": str(zsh_executable),
            }
        )
        config_snapshot = canonical_bytes(effective_config)
        inventory_digest = hashlib.sha256(inventory_snapshot).hexdigest()
        config_file_digest = hashlib.sha256(config_snapshot).hexdigest()
        config_digest = digest(effective_config)
        return BindingSnapshot(
            config_bytes=config_snapshot,
            inventory_bytes=inventory_snapshot,
            npx_alias=str(self.config.npx_executable),
            config_file_digest=config_file_digest,
            config_digest=config_digest,
            inventory_digest=inventory_digest,
            generation_digest=digest(
                {
                    "config_digest": config_digest,
                    "config_file_digest": config_file_digest,
                    "inventory_digest": inventory_digest,
                    "npx_alias": str(self.config.npx_executable),
                }
            ),
        )

    def _validate_config_source(self) -> bytes:
        config_snapshot = _snapshot_regular_file(
            self.config.source_path, "installation config"
        )
        try:
            observed_config = InstallationConfig.load(
                strict_json_loads(config_snapshot),
                source_path=self.config.source_path,
            )
        except (PortableInstallError, ValueError) as error:
            raise PortableInstallError(
                "installation config changed after initial parsing"
            ) from error
        if observed_config.to_dict() != self.config.to_dict():
            raise PortableInstallError(
                "installation config changed after initial parsing"
            )
        return config_snapshot

    def _prepare_data_identity(self, *, initial: bool) -> str:
        path = self.config.data_root
        if initial and self.config.installation_mode == "fresh":
            if path.exists() or path.is_symlink():
                try:
                    metadata = path.lstat()
                except OSError as error:
                    raise PortableInstallError(
                        "fresh data root is unavailable"
                    ) from error
                if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
                    raise PortableInstallError(
                        "fresh data root must be an empty directory"
                    )
                try:
                    if any(path.iterdir()):
                        raise PortableInstallError("fresh data root must be empty")
                except OSError as error:
                    raise PortableInstallError(
                        "fresh data root is unavailable"
                    ) from error
            else:
                _mkdir_private(path)
        elif initial and self.config.installation_mode == "adopt":
            try:
                metadata = path.lstat()
            except OSError as error:
                raise PortableInstallError(
                    "adoption data root must already exist"
                ) from error
            if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
                raise PortableInstallError(
                    "adoption data root must be a non-symlink directory"
                )
        _safe_directory(path, "data root", create=False, private_final=True)
        try:
            metadata = path.lstat()
        except OSError as error:
            raise PortableInstallError("data root is unavailable") from error
        if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
            raise PortableInstallError("data root identity is invalid")
        return digest(
            {
                "path": str(path.resolve(strict=True)),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }
        )

    def _plan_release_record(self, source: Path, version: str) -> dict[str, Any]:
        manifest = _release_manifest(source, version)
        release_name = f"{version}-{manifest['release_digest'][:16]}"
        destination = self.config.install_root / "releases" / release_name
        return {
            "version": version,
            "release_digest": manifest["release_digest"],
            "release_path": destination.relative_to(
                self.config.install_root
            ).as_posix(),
            "manifest": manifest,
        }

    def _publish_release_record(
        self, source: Path, release: Mapping[str, Any], temporary: Path
    ) -> None:
        destination = self.config.install_root / str(release["release_path"])
        _copy_release(source, destination, release["manifest"], temporary=temporary)

    def _launcher_payloads(self, snapshot: BindingSnapshot) -> dict[Path, bytes]:
        python = _validated_python_runtime(self.config.python_executable)
        wrapper = (
            "#!/bin/sh\nexec "
            + shlex.quote(str(python))
            + " -I "
            + shlex.quote(str(self.config.install_root / "wrapper.py"))
            + ' "$@"\n'
        ).encode()
        return {
            self.config.install_root / "launcher.py": SERVICE_LAUNCHER.encode(),
            self.config.install_root / "wrapper.py": WRAPPER.encode(),
            self.config.install_root / "bin" / "hindsight-memory": wrapper,
            self.config.install_root / "managed-config.json": snapshot.config_bytes,
            self.config.install_root
            / "managed-inventory.json": snapshot.inventory_bytes,
            self.config.install_root
            / "credential-resolver": _protected_executable_bytes(
                self.config.credential_resolver.path,
                "credential resolver",
                self.config.credential_resolver.sha256,
            ),
        }

    def _install_preimage(
        self, payloads: Mapping[Path, bytes], previous_owned: Mapping[str, str]
    ) -> dict[str, bytes | None]:
        preimage: dict[str, bytes | None] = {}
        for path in payloads:
            raw_path = str(path)
            if raw_path in previous_owned:
                _regular_file(path, "owned install file")
                if _sha256(path) != previous_owned[raw_path]:
                    raise PortableInstallError(f"owned file drift: {path}")
                preimage[raw_path] = path.read_bytes()
            elif path.exists() or path.is_symlink():
                raise PortableInstallError(f"install file is not owned: {path}")
            else:
                preimage[raw_path] = None
        return preimage

    def _restore_install_files(self, preimage: Mapping[str, bytes | None]) -> None:
        for raw_path, content in preimage.items():
            path = Path(raw_path)
            if content is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            else:
                _atomic_write(path, content, 0o500)

    def _install_launchers(
        self, payloads: Mapping[Path, bytes] | None = None
    ) -> dict[str, str]:
        if payloads is None:
            raise PortableInstallError("launcher payload snapshot is required")
        selected = payloads
        for path, content in selected.items():
            _atomic_write(path, content, 0o500)
        return {str(path): _sha256(path) for path in selected}

    def _launch_argv(self, kind: str, identity: str) -> list[str]:
        return [
            str(
                _protected_executable_path(
                    self.config.python_executable, "managed Python"
                )
            ),
            "-I",
            str(self.config.install_root / "launcher.py"),
            "--config",
            str(self.config.install_root / "managed-config.json"),
            f"--{kind}",
            identity,
        ]

    def _render_launchd(self) -> dict[Path, bytes]:
        rendered: dict[Path, bytes] = {}
        for service in self.config.services:
            keep_alive: bool | dict[str, bool]
            if service.restart == "always":
                keep_alive = True
            elif service.restart == "on-failure":
                keep_alive = {"SuccessfulExit": False}
            else:
                keep_alive = False
            payload = {
                "Label": service.label,
                "ProgramArguments": self._launch_argv("service", service.service_id),
                "RunAtLoad": True,
                "KeepAlive": keep_alive,
                "WorkingDirectory": str(self.config.state_root),
                "ProcessType": "Background",
            }
            rendered[self.config.service_root / f"{service.label}.plist"] = (
                plistlib.dumps(payload, sort_keys=True)
            )
        for timer in self.config.timers:
            hour, minute = (int(part) for part in timer.daily_at.split(":"))
            payload = {
                "Label": timer.label,
                "ProgramArguments": self._launch_argv("timer", timer.timer_id),
                "RunAtLoad": True,
                "StartCalendarInterval": {"Hour": hour, "Minute": minute},
                "WorkingDirectory": str(self.config.state_root),
                "ProcessType": "Background",
            }
            rendered[self.config.service_root / f"{timer.label}.plist"] = (
                plistlib.dumps(payload, sort_keys=True)
            )
        return rendered

    def _render_systemd(self) -> dict[Path, bytes]:
        rendered: dict[Path, bytes] = {}
        for service in self.config.services:
            restart = {"never": "no", "on-failure": "on-failure", "always": "always"}[
                service.restart
            ]
            command = " ".join(
                _systemd_escape(item)
                for item in self._launch_argv("service", service.service_id)
            )
            content = (
                "[Unit]\nDescription=Managed Hindsight " + service.service_id + "\n\n"
                "[Service]\nType=simple\nExecStart=:" + command + "\n"
                "WorkingDirectory="
                + _systemd_escape(str(self.config.state_root))
                + "\n"
                "Restart="
                + restart
                + f"\nTimeoutStopSec={SYSTEMD_STOP_TIMEOUT_SECONDS}s"
                + "\n\n[Install]\nWantedBy=default.target\n"
            )
            rendered[self.config.service_root / f"{service.label}.service"] = (
                content.encode()
            )
        for timer in self.config.timers:
            command = " ".join(
                _systemd_escape(item)
                for item in self._launch_argv("timer", timer.timer_id)
            )
            service_content = (
                "[Unit]\nDescription=Managed Hindsight " + timer.timer_id + "\n\n"
                "[Service]\nType=oneshot\nExecStart=:" + command + "\n"
                "WorkingDirectory="
                + _systemd_escape(str(self.config.state_root))
                + "\n"
            )
            timer_content = (
                "[Unit]\nDescription=Daily managed Hindsight " + timer.timer_id + "\n\n"
                "[Timer]\nOnStartupSec=2min\nOnCalendar=*-*-* "
                + timer.daily_at
                + ":00\nPersistent=true\n"
                "Unit="
                + timer.label
                + ".service\n\n[Install]\nWantedBy=timers.target\n"
            )
            rendered[self.config.service_root / f"{timer.label}.service"] = (
                service_content.encode()
            )
            rendered[self.config.service_root / f"{timer.label}.timer"] = (
                timer_content.encode()
            )
        return rendered

    def _rendered_manifests(self) -> dict[Path, bytes]:
        return (
            self._render_launchd()
            if self.config.platform == "launchd"
            else self._render_systemd()
        )

    def _manifest_preimage(
        self,
        rendered: Mapping[Path, bytes],
        previous_owned: Mapping[str, str],
    ) -> dict[str, bytes | None]:
        preimage: dict[str, bytes | None] = {}
        desired_paths = {str(path) for path in rendered}
        for old_path, checksum in previous_owned.items():
            path = Path(old_path)
            if not path.exists():
                raise PortableInstallError("owned service manifest is missing")
            if _sha256(path) != checksum:
                raise PortableInstallError(f"owned file drift: {path}")
            preimage[old_path] = path.read_bytes()
        for path in rendered:
            if str(path) not in previous_owned and (path.exists() or path.is_symlink()):
                raise PortableInstallError(f"service manifest is not owned: {path}")
            preimage.setdefault(str(path), path.read_bytes() if path.exists() else None)
        for old_path in set(previous_owned) - desired_paths:
            preimage.setdefault(old_path, Path(old_path).read_bytes())
        return preimage

    def _publish_manifests(
        self,
        rendered: Mapping[Path, bytes],
        previous_owned: Mapping[str, str],
        preimage: Mapping[str, bytes | None],
    ) -> dict[str, str]:
        _safe_directory(
            self.config.service_root,
            "service root",
            create=True,
        )
        desired_paths = {str(path) for path in rendered}
        owned: dict[str, str] = {}
        try:
            for old_path in set(previous_owned) - desired_paths:
                Path(old_path).unlink()
                _fsync_directory(Path(old_path).parent)
            for path, content in rendered.items():
                _atomic_write(path, content, 0o600, private_parent=False)
                owned[str(path)] = _sha256(path)
        except Exception:
            self._restore_manifests(preimage)
            raise
        return owned

    def _restore_manifests(self, preimage: Mapping[str, bytes | None]) -> None:
        for raw_path, content in preimage.items():
            path = Path(raw_path)
            if content is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                else:
                    _fsync_directory(path.parent)
            else:
                _atomic_write(path, content, 0o600, private_parent=False)

    def _activate_services(self) -> None:
        if self.config.platform == "launchd":
            domain = f"gui/{os.getuid()}"
            self._preflight_service_manager(absent_ok=True)
            for item in (*self.config.services, *self.config.timers):
                plist = self.config.service_root / f"{item.label}.plist"
                replacing_loaded_job = (
                    self._launchd_loaded_manifest(item.label, plist) is not None
                )
                if replacing_loaded_job:
                    try:
                        self._command_runner(
                            ("/bin/launchctl", "bootout", f"{domain}/{item.label}")
                        )
                    except _ManagedServiceCommandError as error:
                        if error.returncode not in {3, 113}:
                            raise
                self._bootstrap_launchd(
                    domain,
                    plist,
                    replacing_loaded_job=replacing_loaded_job,
                )
                self._command_runner(
                    ("/bin/launchctl", "kickstart", "-k", f"{domain}/{item.label}")
                )
        else:
            self._command_runner(("/usr/bin/systemctl", "--user", "daemon-reload"))
            self._preflight_service_manager(absent_ok=True)
            for service in self.config.services:
                self._assert_systemd_fragment(
                    f"{service.label}.service",
                    self.config.service_root / f"{service.label}.service",
                )
                self._command_runner(
                    (
                        "/usr/bin/systemctl",
                        "--user",
                        "enable",
                        f"{service.label}.service",
                    )
                )
                self._command_runner(
                    (
                        "/usr/bin/systemctl",
                        "--user",
                        "restart",
                        f"{service.label}.service",
                    )
                )
            for timer in self.config.timers:
                self._assert_systemd_fragment(
                    f"{timer.label}.service",
                    self.config.service_root / f"{timer.label}.service",
                )
                self._assert_systemd_fragment(
                    f"{timer.label}.timer",
                    self.config.service_root / f"{timer.label}.timer",
                )
                self._command_runner(
                    ("/usr/bin/systemctl", "--user", "enable", f"{timer.label}.timer")
                )
                self._command_runner(
                    ("/usr/bin/systemctl", "--user", "restart", f"{timer.label}.timer")
                )

    def _deactivate_services(self, *, absent_ok: bool = False) -> None:
        self._preflight_service_manager(absent_ok=absent_ok)
        if self.config.platform == "launchd":
            domain = f"gui/{os.getuid()}"
            for item in (*self.config.services, *self.config.timers):
                plist = self.config.service_root / f"{item.label}.plist"
                if self._launchd_loaded_manifest(item.label, plist) is None:
                    if absent_ok:
                        continue
                    raise PortableInstallError("managed launchd job is absent")
                try:
                    self._command_runner(
                        ("/bin/launchctl", "bootout", f"{domain}/{item.label}")
                    )
                except _ManagedServiceCommandError as error:
                    if not absent_ok or error.returncode not in {3, 113}:
                        raise
        else:
            for timer in self.config.timers:
                timer_unit = f"{timer.label}.timer"
                companion_unit = f"{timer.label}.service"
                if self._assert_systemd_fragment(
                    timer_unit,
                    self.config.service_root / timer_unit,
                    absent_ok=absent_ok,
                ):
                    self._command_runner(
                        (
                            "/usr/bin/systemctl",
                            "--user",
                            "disable",
                            "--now",
                            timer_unit,
                        )
                    )
                if self._assert_systemd_fragment(
                    companion_unit,
                    self.config.service_root / companion_unit,
                    absent_ok=absent_ok,
                ):
                    self._command_runner(
                        (
                            "/usr/bin/systemctl",
                            "--user",
                            "stop",
                            companion_unit,
                        )
                    )
            for service in self.config.services:
                if not self._assert_systemd_fragment(
                    f"{service.label}.service",
                    self.config.service_root / f"{service.label}.service",
                    absent_ok=absent_ok,
                ):
                    continue
                self._command_runner(
                    (
                        "/usr/bin/systemctl",
                        "--user",
                        "disable",
                        "--now",
                        f"{service.label}.service",
                    )
                )
            for timer in self.config.timers:
                if not self._assert_systemd_fragment(
                    f"{timer.label}.timer",
                    self.config.service_root / f"{timer.label}.timer",
                    absent_ok=absent_ok,
                ):
                    continue
                self._command_runner(
                    (
                        "/usr/bin/systemctl",
                        "--user",
                        "disable",
                        "--now",
                        f"{timer.label}.timer",
                    )
                )
            self._command_runner(("/usr/bin/systemctl", "--user", "daemon-reload"))

    def _health(self, release: Mapping[str, Any]) -> bool:
        runner = self._health_runner or self._default_health_runner
        return all(
            runner(check.to_dict(), release) for check in self.config.health_checks
        )

    def _recover_binding_migration(self) -> None:
        if not self._binding_migration_path.exists():
            return
        try:
            journal = _read_json(
                self._binding_migration_path, "binding migration transaction"
            )
            if set(journal) != {
                "schema_version",
                "consumer_id",
                "legacy_config_digest",
                "legacy_state_digest",
                "desired_config",
                "desired_state",
            } or journal.get("schema_version") != 1 or journal.get(
                "consumer_id"
            ) != self.config.consumer_id:
                raise ValueError
            desired_config = base64.b64decode(
                journal["desired_config"], validate=True
            )
            desired_state = base64.b64decode(journal["desired_state"], validate=True)
            config_path = self.config.install_root / "managed-config.json"
            current_config = _snapshot_regular_file(config_path, "managed config")
            current_state = _snapshot_regular_file(
                self._state_path, "installation state"
            )
            config_digests = {
                journal["legacy_config_digest"],
                hashlib.sha256(desired_config).hexdigest(),
            }
            state_digests = {
                journal["legacy_state_digest"],
                hashlib.sha256(desired_state).hexdigest(),
            }
            if (
                hashlib.sha256(current_config).hexdigest() not in config_digests
                or hashlib.sha256(current_state).hexdigest() not in state_digests
            ):
                raise ValueError
            strict_json_loads(desired_config)
            strict_json_loads(desired_state)
        except (KeyError, OSError, PortableInstallError, ValueError) as error:
            raise PortableInstallError(
                "legacy npx binding migration is invalid; reinstall required"
            ) from error
        _atomic_write(config_path, desired_config, 0o500)
        _atomic_write(self._state_path, desired_state, 0o600)
        self._binding_migration_path.unlink()
        _fsync_directory(self._binding_migration_path.parent)

    def _migrate_legacy_npx_binding(
        self, state: dict[str, Any]
    ) -> dict[str, Any]:
        legacy_generation_digest = digest(
            {
                "config_digest": state.get("config_digest"),
                "config_file_digest": state.get("config_file_digest"),
                "inventory_digest": state.get("inventory_digest"),
            }
        )
        if state.get("binding_generation_digest") != legacy_generation_digest:
            raise PortableInstallError("installation state identity is invalid")
        npx_alias = str(self.config.npx_executable)
        config_path = self.config.install_root / "managed-config.json"
        try:
            config_bytes = _snapshot_regular_file(config_path, "managed config")
            config_value = strict_json_loads(config_bytes)
            if not isinstance(config_value, Mapping):
                raise ValueError
        except (PortableInstallError, ValueError) as error:
            raise PortableInstallError(
                "legacy installation lacks a migratable npx binding; reinstall required"
            ) from error

        migrated = dict(state)
        migrated["npx_alias"] = npx_alias
        if "npx_executable" not in config_value:
            try:
                resolved_npx = _protected_executable_path(
                    self.config.npx_executable, "npx executable"
                )
                migrated_config_value = dict(config_value)
                migrated_config_value["npx_executable"] = str(resolved_npx)
                migrated_config = InstallationConfig.load(
                    migrated_config_value, source_path=self.config.source_path
                )
            except (PortableInstallError, ValueError) as error:
                raise PortableInstallError(
                    "legacy installation lacks a migratable npx binding; reinstall required"
                ) from error
            migrated_config_bytes = canonical_bytes(migrated_config_value)
            migrated["config_digest"] = migrated_config.config_digest
            migrated["config_file_digest"] = hashlib.sha256(
                migrated_config_bytes
            ).hexdigest()
            owned = migrated.get("owned_install_files")
            if (
                not isinstance(owned, Mapping)
                or owned.get(str(config_path))
                != hashlib.sha256(config_bytes).hexdigest()
            ):
                raise PortableInstallError(
                    "legacy installation lacks a migratable npx binding; reinstall required"
                )
            migrated["owned_install_files"] = {
                **owned,
                str(config_path): migrated["config_file_digest"],
            }
        else:
            migrated_config_bytes = config_bytes
        migrated["binding_generation_digest"] = digest(
            {
                "config_digest": migrated.get("config_digest"),
                "config_file_digest": migrated.get("config_file_digest"),
                "inventory_digest": migrated.get("inventory_digest"),
                "npx_alias": npx_alias,
            }
        )
        if migrated_config_bytes == config_bytes:
            self._write_state(migrated)
            return migrated

        legacy_state_bytes = _snapshot_regular_file(
            self._state_path, "installation state"
        )
        migrated_state_bytes = canonical_bytes(migrated) + b"\n"
        _atomic_json(
            self._binding_migration_path,
            {
                "schema_version": 1,
                "consumer_id": self.config.consumer_id,
                "legacy_config_digest": hashlib.sha256(config_bytes).hexdigest(),
                "legacy_state_digest": hashlib.sha256(legacy_state_bytes).hexdigest(),
                "desired_config": base64.b64encode(migrated_config_bytes).decode(),
                "desired_state": base64.b64encode(migrated_state_bytes).decode(),
            },
        )
        self._recover_binding_migration()
        return migrated

    def _load_state(self) -> dict[str, Any] | None:
        if self.config.install_root.exists() or self.config.install_root.is_symlink():
            _safe_directory(
                self.config.install_root,
                "install root",
                create=False,
                private_final=True,
            )
        if not self._state_path.exists():
            return None
        self._recover_binding_migration()
        state = _read_json(self._state_path, "installation state")
        if (
            type(state.get("schema_version")) is not int
            or state.get("schema_version") != 1
            or state.get("consumer_id") != self.config.consumer_id
        ):
            raise PortableInstallError("installation state identity is invalid")
        if "npx_alias" not in state:
            state = self._migrate_legacy_npx_binding(state)
        if (
            not isinstance(state.get("npx_alias"), str)
            or not Path(state["npx_alias"]).is_absolute()
        ):
            raise PortableInstallError("installation state identity is invalid")
        return state

    @staticmethod
    def _encode_preimage(preimage: Mapping[str, bytes | None]) -> dict[str, str | None]:
        return {
            path: None if content is None else base64.b64encode(content).decode("ascii")
            for path, content in preimage.items()
        }

    def _managed_install_paths(self) -> set[str]:
        return {
            str(self.config.install_root / relative)
            for relative in (
                "launcher.py",
                "wrapper.py",
                "bin/hindsight-memory",
                "managed-config.json",
                "managed-inventory.json",
                "credential-resolver",
            )
        }

    def _validate_service_paths(self, value: Any, label: str) -> set[str]:
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise PortableInstallError(f"{label} is invalid")
        paths = set(value)
        if len(paths) != len(value):
            raise PortableInstallError(f"{label} is invalid")
        suffixes = (
            {".plist"}
            if self.config.platform == "launchd"
            else {
                ".service",
                ".timer",
            }
        )
        for raw_path in paths:
            path = Path(raw_path)
            if (
                not path.is_absolute()
                or str(path) != raw_path
                or path.parent != self.config.service_root
                or path.suffix not in suffixes
                or not path.stem
            ):
                raise PortableInstallError(f"{label} is invalid")
        return paths

    @staticmethod
    def _decode_preimage(
        preimage: Any, *, allowed_paths: set[str]
    ) -> dict[str, bytes | None]:
        if not isinstance(preimage, Mapping):
            raise PortableInstallError("installation transaction preimage is invalid")
        if set(preimage) != allowed_paths:
            raise PortableInstallError("installation transaction preimage is invalid")
        decoded: dict[str, bytes | None] = {}
        for raw_path, value in preimage.items():
            if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
                raise PortableInstallError("installation transaction path is invalid")
            if value is None:
                decoded[raw_path] = None
                continue
            if not isinstance(value, str):
                raise PortableInstallError(
                    "installation transaction preimage is invalid"
                )
            try:
                decoded[raw_path] = base64.b64decode(value, validate=True)
            except ValueError as error:
                raise PortableInstallError(
                    "installation transaction preimage is invalid"
                ) from error
        return decoded

    def _remove_release_record(self, release: Mapping[str, Any]) -> None:
        root = _release_record_root(self.config.install_root, release)
        if not root.exists() and not root.is_symlink():
            return
        self._verify_release(release)
        directories = [root, *(path for path in root.rglob("*") if path.is_dir())]
        try:
            for directory in sorted(directories, key=lambda path: len(path.parts)):
                directory.chmod(0o700)
            shutil.rmtree(root)
            _fsync_directory(root.parent)
        except OSError as error:
            raise PortableInstallError("candidate release rollback failed") from error

    def _validate_release_staging(
        self, raw_path: Any, candidate: Mapping[str, Any]
    ) -> Path | None:
        if raw_path is None:
            return None
        if not isinstance(raw_path, str):
            raise PortableInstallError("release staging identity is invalid")
        staging = Path(raw_path)
        candidate_root = _release_record_root(self.config.install_root, candidate)
        if (
            not staging.is_absolute()
            or staging.parent != candidate_root.parent
            or not staging.name.startswith(f".{candidate_root.name}.candidate-")
        ):
            raise PortableInstallError("release staging identity is invalid")
        sidecar = staging.parent / f"{staging.name}.owner"
        staging_exists = staging.exists() or staging.is_symlink()
        sidecar_exists = sidecar.exists() or sidecar.is_symlink()
        if not staging_exists and not sidecar_exists:
            return staging
        try:
            expected_marker = {
                "schema_version": 1,
                "release_digest": candidate["release_digest"],
                "staging_name": staging.name,
            }
            identities: list[Path] = []
            if sidecar_exists:
                _verify_owned_file(sidecar, "release staging sidecar", mode=0o600)
                identities.append(sidecar)
            if staging_exists:
                metadata = staging.lstat()
                if not stat.S_ISDIR(metadata.st_mode) or staging.is_symlink():
                    raise PortableInstallError("release staging identity is invalid")
                marker = staging / ".hindsight-staging-owner"
                if marker.exists() or marker.is_symlink():
                    _verify_owned_file(marker, "release staging marker", mode=0o600)
                    identities.append(marker)
            if not identities or any(
                _read_json(path, "release staging identity") != expected_marker
                for path in identities
            ):
                raise PortableInstallError("release staging identity is invalid")
            if staging_exists:
                for path in staging.rglob("*"):
                    metadata = path.lstat()
                    if path.is_symlink() or not (
                        stat.S_ISDIR(metadata.st_mode)
                        or stat.S_ISREG(metadata.st_mode)
                    ):
                        raise PortableInstallError(
                            "release staging identity is invalid"
                        )
            return staging
        except (OSError, PortableInstallError) as error:
            raise PortableInstallError("release staging identity is invalid") from error

    def _remove_release_staging(
        self, raw_path: Any, candidate: Mapping[str, Any]
    ) -> None:
        staging = self._validate_release_staging(raw_path, candidate)
        if staging is None:
            return
        sidecar = staging.parent / f"{staging.name}.owner"
        try:
            mutated = False
            if staging.exists():
                directories = [
                    staging,
                    *(path for path in staging.rglob("*") if path.is_dir()),
                ]
                for directory in sorted(
                    directories, key=lambda path: len(path.parts)
                ):
                    directory.chmod(0o700)
                shutil.rmtree(staging)
                mutated = True
            if sidecar.exists():
                sidecar.unlink()
                mutated = True
            if mutated:
                _fsync_directory(staging.parent)
        except PortableInstallError:
            raise
        except OSError as error:
            raise PortableInstallError("release staging rollback failed") from error

    def _recover_pending_locked(self) -> None:
        if (
            self._transaction_path.exists()
            and self._uninstall_transaction_path.exists()
        ):
            raise PortableInstallError("conflicting installation transactions exist")
        self._recover_uninstall_locked()
        if not self._transaction_path.exists():
            return
        journal = _read_json(self._transaction_path, "installation transaction")
        if (
            set(journal)
            != {
                "schema_version",
                "consumer_id",
                "operation",
                "candidate",
                "candidate_install_paths",
                "candidate_manifest_paths",
                "prior_state",
                "active_preimage",
                "manifest_preimage",
                "install_preimage",
                "release_preexisting",
                "release_staging_path",
            }
            or type(journal.get("schema_version")) is not int
            or journal.get("schema_version") != 2
            or journal.get("consumer_id") != self.config.consumer_id
            or journal.get("operation") not in {"install", "upgrade", "rollback"}
            or not isinstance(journal.get("candidate"), Mapping)
            or not isinstance(journal.get("release_preexisting"), bool)
        ):
            raise PortableInstallError("installation transaction identity is invalid")
        current_state = self._load_state()
        candidate = journal["candidate"]
        candidate_root = _release_record_root(self.config.install_root, candidate)
        if candidate_root.exists() or candidate_root.is_symlink():
            self._verify_release(candidate)
        if (
            current_state is not None
            and current_state.get("transaction") is not None
            and current_state.get("current") != candidate
        ):
            raise PortableInstallError("installation transaction candidate differs")
        self._validate_release_staging(journal["release_staging_path"], candidate)
        if (
            current_state is not None
            and current_state.get("transaction") is None
            and current_state.get("current", {}).get("release_digest")
            == candidate.get("release_digest")
        ):
            self._verify_installed_locked(current_state)
            self._clear_transaction()
            return
        prior = journal["prior_state"]
        if prior is not None and not isinstance(prior, Mapping):
            raise PortableInstallError("installation transaction prestate is invalid")
        candidate_install_paths = journal["candidate_install_paths"]
        expected_candidate_install_paths = (
            set()
            if journal["operation"] == "rollback"
            else self._managed_install_paths()
        )
        if (
            not isinstance(candidate_install_paths, list)
            or set(candidate_install_paths) != expected_candidate_install_paths
            or len(candidate_install_paths) != len(set(candidate_install_paths))
        ):
            raise PortableInstallError("candidate install paths are invalid")
        candidate_manifest_paths = self._validate_service_paths(
            journal["candidate_manifest_paths"], "candidate manifest paths"
        )
        candidate_manager = self
        if (
            current_state is not None
            and current_state.get("transaction") is not None
            and current_state.get("current", {}).get("release_digest")
            == candidate.get("release_digest")
        ):
            candidate_manager = self._installed_manager(current_state)
        expected_candidate_manifest_paths = (
            set()
            if journal["operation"] == "rollback"
            else {str(path) for path in candidate_manager._rendered_manifests()}
        )
        if candidate_manifest_paths != expected_candidate_manifest_paths:
            raise PortableInstallError("candidate manifest paths are invalid")
        prior_install_paths: set[str] = set()
        prior_manifest_paths: set[str] = set()
        prior_install: Mapping[str, Any] = {}
        prior_services: Mapping[str, Any] = {}
        if prior is not None:
            prior_install = prior.get("owned_install_files")
            prior_services = prior.get("owned_service_files")
            if (
                type(prior.get("schema_version")) is not int
                or prior.get("schema_version") != 1
                or prior.get("consumer_id") != self.config.consumer_id
                or prior.get("transaction") is not None
                or not isinstance(prior_install, Mapping)
                or set(prior_install) != self._managed_install_paths()
                or not isinstance(prior_services, Mapping)
            ):
                raise PortableInstallError(
                    "installation transaction prestate is invalid"
                )
            prior_install_paths = set(prior_install)
            prior_manifest_paths = self._validate_service_paths(
                list(prior_services), "prior manifest paths"
            )
            releases = prior.get("releases")
            current = prior.get("current")
            if (
                not isinstance(releases, Mapping)
                or not isinstance(current, Mapping)
                or releases.get(current.get("release_digest")) != current
            ):
                raise PortableInstallError(
                    "installation transaction prestate is invalid"
                )
            for release_digest, release in releases.items():
                if (
                    not isinstance(release_digest, str)
                    or not isinstance(release, Mapping)
                    or release.get("release_digest") != release_digest
                ):
                    raise PortableInstallError(
                        "installation transaction prestate is invalid"
                    )
                _release_record_root(self.config.install_root, release)
        preimage = self._decode_preimage(
            journal.get("manifest_preimage"),
            allowed_paths=(
                set()
                if journal["operation"] == "rollback"
                else candidate_manifest_paths | prior_manifest_paths
            ),
        )
        install_preimage = self._decode_preimage(
            journal.get("install_preimage"),
            allowed_paths=(
                set()
                if journal["operation"] == "rollback"
                else set(candidate_install_paths) | prior_install_paths
            ),
        )

        def validate_preimage_content(
            values: Mapping[str, bytes | None], owned: Mapping[str, Any]
        ) -> None:
            for raw_path, content in values.items():
                checksum = owned.get(raw_path)
                if checksum is None:
                    if content is not None:
                        raise PortableInstallError(
                            "installation transaction preimage content is invalid"
                        )
                    continue
                if (
                    not isinstance(checksum, str)
                    or SHA256.fullmatch(checksum) is None
                    or content is None
                    or hashlib.sha256(content).hexdigest() != checksum
                ):
                    raise PortableInstallError(
                        "installation transaction preimage content is invalid"
                    )

        validate_preimage_content(preimage, prior_services)
        validate_preimage_content(install_preimage, prior_install)
        prior_manager: PortableInstallationManager | None = None
        if prior is not None:
            recovery_install_preimage = install_preimage
            recovery_manifest_preimage = preimage
            if journal["operation"] == "rollback":
                recovery_install_preimage = {}
                for raw_path, checksum in prior_install.items():
                    path = Path(raw_path)
                    _verify_owned_file(path, "owned install file", mode=0o500)
                    content = path.read_bytes()
                    if hashlib.sha256(content).hexdigest() != checksum:
                        raise PortableInstallError("owned install file drift")
                    recovery_install_preimage[raw_path] = content
                recovery_manifest_preimage = {}
                for raw_path, checksum in prior_services.items():
                    path = Path(raw_path)
                    _verify_owned_file(path, "owned service manifest", mode=0o600)
                    content = path.read_bytes()
                    if hashlib.sha256(content).hexdigest() != checksum:
                        raise PortableInstallError("owned service manifest drift")
                    recovery_manifest_preimage[raw_path] = content
            prior_manager = self._validate_recovery_prestate(
                prior,
                install_preimage=recovery_install_preimage,
                manifest_preimage=recovery_manifest_preimage,
            )
        active_value = journal["active_preimage"]
        active_bytes: bytes | None = None
        if prior is None:
            if active_value is not None:
                raise PortableInstallError(
                    "installation transaction active preimage is invalid"
                )
        elif not isinstance(active_value, str):
            raise PortableInstallError(
                "installation transaction active preimage is invalid"
            )
        else:
            try:
                active_bytes = base64.b64decode(active_value, validate=True)
            except ValueError as error:
                raise PortableInstallError(
                    "installation transaction active preimage is invalid"
                ) from error
            expected_active = (
                canonical_bytes(
                    {
                        key: prior["current"][key]
                        for key in ("version", "release_digest", "release_path")
                    }
                )
                + b"\n"
            )
            if active_bytes != expected_active:
                raise PortableInstallError(
                    "installation transaction active preimage is invalid"
                )
        if candidate_manager is not self:
            if journal["operation"] != "rollback" and candidate_manifest_paths != {
                str(path) for path in candidate_manager._rendered_manifests()
            }:
                raise PortableInstallError("candidate manifest paths are invalid")
        try:
            candidate_manager._deactivate_services(absent_ok=True)
        except Exception as error:
            raise PortableInstallError(
                "interrupted installation could not stop candidate services"
            ) from error
        self._restore_manifests(preimage)
        if self.config.platform == "systemd-user":
            self._command_runner(("/usr/bin/systemctl", "--user", "daemon-reload"))
        self._restore_install_files(install_preimage)
        self._remove_release_staging(journal["release_staging_path"], candidate)
        if active_bytes is None:
            try:
                self._active_path.unlink()
            except FileNotFoundError:
                pass
        else:
            _atomic_write(self._active_path, active_bytes, 0o600)
        if prior is None:
            if current_state is not None:
                try:
                    self._state_path.unlink()
                except FileNotFoundError:
                    pass
            if not journal["release_preexisting"]:
                self._remove_release_record(candidate)
            self._remove_fresh_install_root()
            self._clear_transaction()
            return
        self._write_state(prior)
        if not journal["release_preexisting"] and candidate.get(
            "release_digest"
        ) not in prior.get("releases", {}):
            self._remove_release_record(candidate)
        if prior_manager is None:
            raise PortableInstallError("installation transaction prestate is invalid")
        prior_manager._activate_services()
        prior_manager._verify_service_manager()
        if not prior_manager._health(prior["current"]):
            raise PortableInstallError(
                "interrupted installation recovery health check failed"
            )
        prior_manager._verify_installed_locked(prior)
        self._clear_transaction()

    def _clear_transaction(self) -> None:
        try:
            self._transaction_path.unlink()
        except FileNotFoundError:
            return
        _fsync_directory(self._transaction_path.parent)

    def _remove_fresh_install_root(self) -> None:
        if not self.config.install_root.exists():
            return
        try:
            directories = sorted(
                (path for path in self.config.install_root.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            )
            for directory in directories:
                directory.rmdir()
            self.config.install_root.rmdir()
            _fsync_directory(self.config.install_root.parent)
        except OSError as error:
            raise PortableInstallError(
                "failed installation left unowned paths"
            ) from error

    def _write_state(self, state: Mapping[str, Any]) -> None:
        _atomic_json(self._state_path, state)

    def _transition(
        self,
        release_root: Path,
        version: str,
        *,
        operation: str,
        expected_current_binding_generation_digest: str | None = None,
    ) -> dict[str, Any]:
        self._validate_external_bindings()
        with self._lock():
            self._recover_pending_locked()
            prior = self._load_state()
            if operation == "install" and prior is not None:
                if prior["current"]["version"] == version:
                    candidate = _release_manifest(release_root, version)
                    if (
                        candidate["release_digest"]
                        == prior["current"]["release_digest"]
                    ):
                        self._verify_locked(prior)
                        self._verify_service_manager()
                        if not self._health(prior["current"]):
                            raise PortableInstallError("health verification failed")
                        return {
                            "status": "unchanged",
                            **prior["current"],
                            "data_identity_digest": prior["data_identity_digest"],
                            "binding_generation_digest": prior[
                                "binding_generation_digest"
                            ],
                        }
                raise PortableInstallError("installation already exists; use upgrade")
            if operation == "upgrade" and prior is None:
                raise PortableInstallError("upgrade requires an existing installation")
            external_bindings = self._validate_external_bindings()
            data_identity = self._prepare_data_identity(initial=prior is None)
            if prior is not None:
                self._verify_installed_locked(prior)
                installed_manager = self._installed_manager(prior)
                if expected_current_binding_generation_digest != prior.get(
                    "binding_generation_digest"
                ):
                    raise PortableInstallError(
                        "current binding generation digest changed"
                    )
                if data_identity != prior["data_identity_digest"]:
                    raise PortableInstallError("data identity changed")
                installed_manager._preflight_service_manager()
                self._preflight_added_service_manager(installed_manager.config)
                installed_manager._verify_service_manager()
                if not installed_manager._health(prior["current"]):
                    raise PortableInstallError("health verification failed")
            else:
                self._preflight_service_manager(require_absent=True)
            if prior is None:
                if self.config.install_root.exists() and any(
                    self.config.install_root.iterdir()
                ):
                    raise PortableInstallError("install root contains unowned files")
                _mkdir_private(self.config.install_root)
            release = self._plan_release_record(release_root, version)
            release_destination = self.config.install_root / release["release_path"]
            release_preexisting = (
                release_destination.exists() or release_destination.is_symlink()
            )
            if release_preexisting:
                self._verify_release(release)
            release_staging = release_destination.parent / (
                f".{release_destination.name}.candidate-{os.getpid()}"
            )
            if release_staging.exists() or release_staging.is_symlink():
                raise PortableInstallError("release staging path already exists")
            launcher_payloads = self._launcher_payloads(external_bindings)
            launcher_owned = {
                str(path): hashlib.sha256(content).hexdigest()
                for path, content in launcher_payloads.items()
            }
            previous_install = prior.get("owned_install_files", {}) if prior else {}
            install_preimage = self._install_preimage(
                launcher_payloads, previous_install
            )
            previous_services = prior.get("owned_service_files", {}) if prior else {}
            rendered = self._rendered_manifests()
            preimage = self._manifest_preimage(rendered, previous_services)
            active_preimage = (
                self._active_path.read_bytes() if self._active_path.exists() else None
            )
            candidate_state = {
                "schema_version": 1,
                "consumer_id": self.config.consumer_id,
                "platform": self.config.platform,
                "config_digest": external_bindings.config_digest,
                "config_file_digest": external_bindings.config_file_digest,
                "inventory_digest": external_bindings.inventory_digest,
                "binding_generation_digest": external_bindings.generation_digest,
                "npx_alias": external_bindings.npx_alias,
                "data_identity_digest": data_identity,
                "current": release,
                "last_known_good": prior["current"] if prior else release,
                "releases": {
                    **(prior.get("releases", {}) if prior else {}),
                    release["release_digest"]: release,
                },
                "owned_install_files": launcher_owned,
                "owned_service_files": {},
                "transaction": {
                    "operation": operation,
                    "candidate_release_digest": release["release_digest"],
                    "previous_release_digest": prior["current"]["release_digest"]
                    if prior
                    else None,
                },
            }
            journal = {
                "schema_version": 2,
                "consumer_id": self.config.consumer_id,
                "operation": operation,
                "candidate": release,
                "candidate_install_paths": sorted(
                    str(path) for path in launcher_payloads
                ),
                "candidate_manifest_paths": sorted(str(path) for path in rendered),
                "prior_state": prior,
                "active_preimage": (
                    None
                    if active_preimage is None
                    else base64.b64encode(active_preimage).decode("ascii")
                ),
                "manifest_preimage": self._encode_preimage(preimage),
                "install_preimage": self._encode_preimage(install_preimage),
                "release_preexisting": release_preexisting,
                "release_staging_path": str(release_staging),
            }
            _atomic_json(self._transaction_path, journal)
            try:
                if prior is not None:
                    installed_manager._deactivate_services()
                self._publish_release_record(release_root, release, release_staging)
                if self._install_launchers(launcher_payloads) != launcher_owned:
                    raise PortableInstallError("installed launcher digest mismatch")
                self._write_state(candidate_state)
                service_owned = self._publish_manifests(
                    rendered, previous_services, preimage
                )
                _atomic_json(
                    self._active_path,
                    {
                        key: release[key]
                        for key in ("version", "release_digest", "release_path")
                    },
                )
                if self._prepare_data_identity(initial=False) != data_identity:
                    raise PortableInstallError("data identity changed")
                self._activate_services()
                self._verify_service_manager()
                if not self._health(release):
                    raise PortableInstallError("health verification failed")
                candidate_state["owned_service_files"] = service_owned
                candidate_state["transaction"] = None
                self._write_state(candidate_state)
                self._verify_locked(
                    candidate_state, external_bindings=external_bindings
                )
                self._clear_transaction()
                return {
                    "status": "installed" if prior is None else "upgraded",
                    **release,
                    "data_identity_digest": data_identity,
                    "binding_generation_digest": external_bindings.generation_digest,
                }
            except Exception as error:
                try:
                    self._recover_pending_locked()
                except Exception as rollback_error:
                    raise PortableInstallError(
                        "installation rollback failed"
                    ) from rollback_error
                if isinstance(error, PortableInstallError):
                    raise
                raise PortableInstallError("installation transition failed") from error

    def install(self, release_root: str | Path, *, version: str) -> dict[str, Any]:
        self._preflight_lifecycle()
        return self._transition(Path(release_root), version, operation="install")

    def upgrade(
        self,
        release_root: str | Path,
        *,
        version: str,
        expected_current_binding_generation_digest: str,
    ) -> dict[str, Any]:
        self._preflight_lifecycle()
        if SHA256.fullmatch(expected_current_binding_generation_digest) is None:
            raise PortableInstallError(
                "expected current binding generation digest is invalid"
            )
        return self._transition(
            Path(release_root),
            version,
            operation="upgrade",
            expected_current_binding_generation_digest=(
                expected_current_binding_generation_digest
            ),
        )

    def _verify_release(self, release: Mapping[str, Any]) -> None:
        root = _release_record_root(self.config.install_root, release)
        observed = _release_manifest(root, release["version"])
        if (
            observed != release["manifest"]
            or observed["release_digest"] != release["release_digest"]
        ):
            raise PortableInstallError("installed release verification failed")
        directories = [root, *(path for path in root.rglob("*") if path.is_dir())]
        for directory in directories:
            metadata = directory.lstat()
            if (
                directory.is_symlink()
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o777 != 0o500
            ):
                raise PortableInstallError("installed release protection differs")
            _reject_extended_acl(directory, "installed release directory")
        for entry in release["manifest"]["files"]:
            _verify_owned_file(
                root / entry["path"],
                "installed release file",
                mode=0o500 if entry["executable"] else 0o400,
            )

    def _manager_from_binding_snapshots(
        self,
        state: Mapping[str, Any],
        *,
        config_bytes: bytes,
        inventory_bytes: bytes,
    ) -> "PortableInstallationManager":
        try:
            installed_config = InstallationConfig.load(
                strict_json_loads(config_bytes), source_path=self.config.source_path
            )
        except (PortableInstallError, ValueError) as error:
            raise PortableInstallError("managed config is invalid") from error
        immutable = (
            "consumer_id",
            "platform",
            "installation_mode",
            "install_root",
            "state_root",
            "data_root",
            "service_root",
        )
        if any(
            getattr(installed_config, field) != getattr(self.config, field)
            for field in immutable
        ):
            raise PortableInstallError("immutable installation identity differs")
        config_file_digest = hashlib.sha256(config_bytes).hexdigest()
        inventory_digest = hashlib.sha256(inventory_bytes).hexdigest()
        generation_digest = digest(
            {
                "config_digest": installed_config.config_digest,
                "config_file_digest": config_file_digest,
                "inventory_digest": inventory_digest,
                "npx_alias": state.get("npx_alias"),
            }
        )
        if (
            not isinstance(state.get("npx_alias"), str)
            or not Path(state["npx_alias"]).is_absolute()
            or state.get("config_digest") != installed_config.config_digest
            or state.get("config_file_digest") != config_file_digest
            or state.get("inventory_digest") != inventory_digest
            or state.get("binding_generation_digest") != generation_digest
        ):
            raise PortableInstallError("installed binding generation differs")
        return PortableInstallationManager(
            installed_config,
            command_runner=self._command_runner,
            health_runner=self._health_runner,
        )

    def _validate_recovery_prestate(
        self,
        state: Mapping[str, Any],
        *,
        install_preimage: Mapping[str, bytes | None],
        manifest_preimage: Mapping[str, bytes | None],
    ) -> "PortableInstallationManager":
        expected_keys = {
            "schema_version",
            "consumer_id",
            "platform",
            "config_digest",
            "config_file_digest",
            "inventory_digest",
            "binding_generation_digest",
            "npx_alias",
            "data_identity_digest",
            "current",
            "last_known_good",
            "releases",
            "owned_install_files",
            "owned_service_files",
            "transaction",
        }
        digest_fields = (
            "config_digest",
            "config_file_digest",
            "inventory_digest",
            "binding_generation_digest",
            "data_identity_digest",
        )
        owned_install = state.get("owned_install_files")
        owned_services = state.get("owned_service_files")
        if (
            set(state) != expected_keys
            or type(state.get("schema_version")) is not int
            or state.get("schema_version") != 1
            or state.get("consumer_id") != self.config.consumer_id
            or state.get("transaction") is not None
            or not isinstance(state.get("npx_alias"), str)
            or not Path(state["npx_alias"]).is_absolute()
            or any(
                not isinstance(state.get(field), str)
                or SHA256.fullmatch(state[field]) is None
                for field in digest_fields
            )
            or not isinstance(owned_install, Mapping)
            or set(owned_install) != self._managed_install_paths()
            or not isinstance(owned_services, Mapping)
        ):
            raise PortableInstallError("installation transaction prestate is invalid")
        config_path = str(self.config.install_root / "managed-config.json")
        inventory_path = str(self.config.install_root / "managed-inventory.json")
        config_bytes = install_preimage.get(config_path)
        inventory_bytes = install_preimage.get(inventory_path)
        if not isinstance(config_bytes, bytes) or not isinstance(
            inventory_bytes, bytes
        ):
            raise PortableInstallError("installation transaction prestate is invalid")
        manager = self._manager_from_binding_snapshots(
            state,
            config_bytes=config_bytes,
            inventory_bytes=inventory_bytes,
        )
        if state.get("platform") != manager.config.platform:
            raise PortableInstallError("installation transaction prestate is invalid")
        releases = state.get("releases")
        current = state.get("current")
        last_known_good = state.get("last_known_good")
        if (
            not isinstance(releases, Mapping)
            or not isinstance(current, Mapping)
            or not isinstance(last_known_good, Mapping)
            or releases.get(current.get("release_digest")) != current
            or releases.get(last_known_good.get("release_digest")) != last_known_good
        ):
            raise PortableInstallError("installation transaction prestate is invalid")
        for release_digest, release in releases.items():
            if (
                not isinstance(release_digest, str)
                or not isinstance(release, Mapping)
                or release.get("release_digest") != release_digest
            ):
                raise PortableInstallError(
                    "installation transaction prestate is invalid"
                )
            manager._verify_release(release)
        expected_manifests = manager._rendered_manifests()
        if set(owned_services) != {str(path) for path in expected_manifests}:
            raise PortableInstallError("installation transaction prestate is invalid")
        for path, expected_content in expected_manifests.items():
            content = manifest_preimage.get(str(path))
            checksum = owned_services.get(str(path))
            if (
                not isinstance(content, bytes)
                or content != expected_content
                or not isinstance(checksum, str)
                or hashlib.sha256(content).hexdigest() != checksum
            ):
                raise PortableInstallError(
                    "installation transaction prestate is invalid"
                )
        if manager._prepare_data_identity(initial=False) != state.get(
            "data_identity_digest"
        ):
            raise PortableInstallError("installation transaction prestate is invalid")
        return manager

    def _installed_manager(
        self, state: Mapping[str, Any]
    ) -> "PortableInstallationManager":
        config_path = self.config.install_root / "managed-config.json"
        inventory_path = self.config.install_root / "managed-inventory.json"
        owned = state.get("owned_install_files", {})
        for path in (config_path, inventory_path):
            checksum = owned.get(str(path))
            if not isinstance(checksum, str) or SHA256.fullmatch(checksum) is None:
                raise PortableInstallError("installed binding ownership differs")
            _verify_owned_file(path, "installed binding", mode=0o500)
            if _sha256(path) != checksum:
                raise PortableInstallError(f"owned file drift: {path}")
        config_bytes = _snapshot_regular_file(config_path, "managed config")
        inventory_bytes = _snapshot_regular_file(inventory_path, "managed inventory")
        return self._manager_from_binding_snapshots(
            state,
            config_bytes=config_bytes,
            inventory_bytes=inventory_bytes,
        )

    def _verify_installed_locked(self, state: Mapping[str, Any]) -> dict[str, Any]:
        if state.get("transaction") is not None:
            raise PortableInstallError("installation transaction is pending")
        installed = self._installed_manager(state)
        _safe_directory(
            installed.config.service_root,
            "service root",
            create=False,
        )
        if (
            state.get("platform") != installed.config.platform
            or state.get("config_digest") != installed.config.config_digest
        ):
            raise PortableInstallError("installed consumer configuration differs")
        data_identity = installed._prepare_data_identity(initial=False)
        if data_identity != state.get("data_identity_digest"):
            raise PortableInstallError("data identity changed")
        current = state.get("current")
        if not isinstance(current, Mapping):
            raise PortableInstallError("current release record is invalid")
        installed._verify_release(current)
        active = _read_json(installed._active_path, "active release pointer")
        if active != {
            key: current[key] for key in ("version", "release_digest", "release_path")
        }:
            raise PortableInstallError("active release pointer differs")
        owned_install_files = state.get("owned_install_files")
        if (
            not isinstance(owned_install_files, Mapping)
            or set(owned_install_files) != installed._managed_install_paths()
        ):
            raise PortableInstallError("owned install file ownership differs")
        for raw_path, checksum in owned_install_files.items():
            path = Path(raw_path)
            _verify_owned_file(path, "owned install file", mode=0o500)
            if _sha256(path) != checksum:
                raise PortableInstallError(f"owned file drift: {path}")
        expected_manifests = installed._rendered_manifests()
        owned_services = state.get("owned_service_files", {})
        if set(owned_services) != {str(path) for path in expected_manifests}:
            raise PortableInstallError("service manifest ownership differs")
        for path, content in expected_manifests.items():
            _verify_owned_file(path, "owned service manifest", mode=0o600)
            checksum = _sha256(path)
            if (
                checksum != owned_services[str(path)]
                or checksum != hashlib.sha256(content).hexdigest()
            ):
                raise PortableInstallError(f"owned file drift: {path}")
        return {
            "status": "verified",
            "current": {
                key: current[key]
                for key in ("version", "release_digest", "release_path")
            },
            "last_known_good": {
                key: state["last_known_good"][key]
                for key in ("version", "release_digest", "release_path")
            },
            "data_identity_digest": data_identity,
            "binding_generation_digest": state["binding_generation_digest"],
            "transaction_pending": False,
        }

    def _verify_locked(
        self,
        state: Mapping[str, Any],
        *,
        external_bindings: BindingSnapshot | None = None,
    ) -> dict[str, Any]:
        verification = self._verify_installed_locked(state)
        bindings = external_bindings or self._validate_external_bindings()
        if state.get("binding_generation_digest") != bindings.generation_digest:
            raise PortableInstallError("installed consumer binding differs")
        return verification

    def verify(self) -> dict[str, Any]:
        self._preflight_lifecycle()
        self._validate_config_source()
        with self._lock():
            self._recover_pending_locked()
            state = self._load_state()
            if state is None:
                raise PortableInstallError("installation is absent")
            verification = self._verify_locked(state)
            installed = self._installed_manager(state)
            installed._verify_service_manager()
            if not installed._health(state["current"]):
                raise PortableInstallError("health verification failed")
            return {**verification, "managed_health": "healthy"}

    def rollback(self, *, expected_current_digest: str) -> dict[str, Any]:
        self._preflight_lifecycle()
        if SHA256.fullmatch(expected_current_digest) is None:
            raise PortableInstallError("expected current release digest is invalid")
        self._validate_config_source()
        with self._lock():
            self._recover_pending_locked()
            state = self._load_state()
            if state is None:
                raise PortableInstallError("installation is absent")
            self._verify_installed_locked(state)
            installed = self._installed_manager(state)
            current = state["current"]
            target = state["last_known_good"]
            if current["release_digest"] != expected_current_digest:
                raise PortableInstallError("current release digest changed")
            if target["release_digest"] == current["release_digest"]:
                raise PortableInstallError("no distinct last-known-good release exists")
            installed._verify_release(target)
            installed._preflight_service_manager()
            active_preimage = self._active_path.read_bytes()
            prior_state = strict_json_loads(canonical_bytes(state))
            pending_state = strict_json_loads(canonical_bytes(state))
            pending_state["current"] = target
            pending_state["last_known_good"] = current
            pending_state["transaction"] = {
                "operation": "rollback",
                "candidate_release_digest": target["release_digest"],
                "previous_release_digest": current["release_digest"],
            }
            _atomic_json(
                self._transaction_path,
                {
                    "schema_version": 2,
                    "consumer_id": self.config.consumer_id,
                    "operation": "rollback",
                    "candidate": target,
                    "candidate_install_paths": [],
                    "candidate_manifest_paths": [],
                    "prior_state": prior_state,
                    "active_preimage": base64.b64encode(active_preimage).decode(
                        "ascii"
                    ),
                    "manifest_preimage": {},
                    "install_preimage": {},
                    "release_preexisting": True,
                    "release_staging_path": None,
                },
            )
            try:
                installed._quiesce_scheduled_jobs()
                self._write_state(pending_state)
                _atomic_json(
                    self._active_path,
                    {
                        key: target[key]
                        for key in ("version", "release_digest", "release_path")
                    },
                )
                installed._activate_services()
                installed._verify_service_manager()
                if not installed._health(target):
                    raise PortableInstallError("rollback health verification failed")
                pending_state["transaction"] = None
                self._write_state(pending_state)
                installed._verify_installed_locked(pending_state)
                self._clear_transaction()
                return {"status": "rolled-back", **target}
            except Exception as error:
                try:
                    self._recover_pending_locked()
                except Exception as rollback_error:
                    raise PortableInstallError(
                        "rollback recovery failed"
                    ) from rollback_error
                if isinstance(error, PortableInstallError):
                    raise
                raise PortableInstallError("rollback failed") from error

    def _owned_install_tree(self, state: Mapping[str, Any]) -> set[Path]:
        owned = {
            self._active_path,
            self._state_path,
            *(Path(path) for path in state.get("owned_install_files", {})),
        }
        for release in state.get("releases", {}).values():
            root = self.config.install_root / release["release_path"]
            owned.add(root)
            for entry in release["manifest"]["files"]:
                owned.add(root / entry["path"])
        paths = list(owned)
        for path in paths:
            parent = path.parent
            while parent != self.config.install_root.parent:
                owned.add(parent)
                if parent == self.config.install_root:
                    break
                parent = parent.parent
        return owned

    def _audit_owned_install_tree(self, state: Mapping[str, Any]) -> None:
        owned_tree = self._owned_install_tree(state)
        try:
            observed = {
                self.config.install_root,
                *self.config.install_root.rglob("*"),
            }
        except OSError as error:
            raise PortableInstallError("install tree cannot be audited") from error
        unexpected = observed - owned_tree
        if unexpected:
            raise PortableInstallError(
                "install tree contains unowned paths: "
                + ", ".join(str(path) for path in sorted(unexpected))
            )

    def _verify_all_releases(self, state: Mapping[str, Any]) -> None:
        releases = state.get("releases")
        if not isinstance(releases, Mapping) or not releases:
            raise PortableInstallError("installed release inventory is invalid")
        for release_digest, release in releases.items():
            if (
                not isinstance(release_digest, str)
                or not isinstance(release, Mapping)
                or release.get("release_digest") != release_digest
            ):
                raise PortableInstallError("installed release inventory is invalid")
            self._verify_release(release)

    def _remove_uninstall_tombstone(self) -> None:
        tombstone = self._uninstall_tombstone_path
        if not tombstone.exists() and not tombstone.is_symlink():
            _fsync_directory(tombstone.parent)
            return
        try:
            metadata = tombstone.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or tombstone.is_symlink():
                raise PortableInstallError("uninstall tombstone identity is invalid")
            directories = [tombstone]
            for path in tombstone.rglob("*"):
                metadata = path.lstat()
                if path.is_symlink() or not (
                    stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
                ):
                    raise PortableInstallError(
                        "uninstall tombstone identity is invalid"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    directories.append(path)
            for directory in sorted(directories, key=lambda path: len(path.parts)):
                directory.chmod(0o700)
            shutil.rmtree(tombstone)
            _fsync_directory(tombstone.parent)
        except PortableInstallError:
            raise
        except OSError as error:
            raise PortableInstallError(
                "owned uninstall tombstone could not be removed"
            ) from error

    def _recover_uninstall_locked(self) -> None:
        journal_path = self._uninstall_transaction_path
        if not journal_path.exists():
            if self._uninstall_tombstone_path.exists() or (
                self._uninstall_tombstone_path.is_symlink()
            ):
                raise PortableInstallError("unowned uninstall tombstone exists")
            return
        journal = _read_json(journal_path, "uninstall transaction")
        required = {
            "schema_version",
            "consumer_id",
            "phase",
            "install_root",
            "tombstone_path",
            "manifest_preimage",
        }
        if (
            set(journal) != required
            or type(journal.get("schema_version")) is not int
            or journal.get("schema_version") != 1
            or journal.get("consumer_id") != self.config.consumer_id
            or journal.get("phase") not in {"prepared", "committed"}
            or journal.get("install_root") != str(self.config.install_root)
            or journal.get("tombstone_path") != str(self._uninstall_tombstone_path)
        ):
            raise PortableInstallError("uninstall transaction identity is invalid")
        install_exists = self.config.install_root.exists() or (
            self.config.install_root.is_symlink()
        )
        tombstone_exists = self._uninstall_tombstone_path.exists() or (
            self._uninstall_tombstone_path.is_symlink()
        )
        if journal["phase"] == "committed":
            if install_exists:
                raise PortableInstallError(
                    "committed uninstall unexpectedly retained install root"
                )
            self._remove_uninstall_tombstone()
            journal_path.unlink()
            _fsync_directory(journal_path.parent)
            return
        if install_exists and tombstone_exists:
            raise PortableInstallError("uninstall recovery roots conflict")
        if tombstone_exists:
            try:
                metadata = self._uninstall_tombstone_path.lstat()
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or self._uninstall_tombstone_path.is_symlink()
                ):
                    raise PortableInstallError(
                        "uninstall tombstone identity is invalid"
                    )
                self._uninstall_tombstone_path.rename(self.config.install_root)
                _fsync_directory(self.config.install_root.parent)
            except PortableInstallError:
                raise
            except OSError as error:
                raise PortableInstallError(
                    "uninstall recovery could not restore install root"
                ) from error
        elif not install_exists:
            raise PortableInstallError("uninstall recovery install tree is missing")
        _safe_directory(
            self.config.install_root, "uninstall recovery install root", create=False
        )
        state = self._load_state()
        if state is None:
            raise PortableInstallError("uninstall recovery state is missing")
        owned_services = state.get("owned_service_files")
        if not isinstance(owned_services, Mapping):
            raise PortableInstallError(
                "uninstall transaction manifest ownership is invalid"
            )
        allowed_manifest_paths = self._validate_service_paths(
            list(owned_services), "uninstall manifest paths"
        )
        preimage = self._decode_preimage(
            journal["manifest_preimage"], allowed_paths=allowed_manifest_paths
        )
        for raw_path, content in preimage.items():
            checksum = owned_services[raw_path]
            if (
                content is None
                or not isinstance(checksum, str)
                or SHA256.fullmatch(checksum) is None
                or hashlib.sha256(content).hexdigest() != checksum
            ):
                raise PortableInstallError(
                    "uninstall transaction manifest preimage is invalid"
                )
        self._restore_manifests(preimage)
        self._verify_installed_locked(state)
        installed = self._installed_manager(state)
        installed._activate_services()
        installed._verify_service_manager()
        if not installed._health(state["current"]):
            raise PortableInstallError("uninstall recovery health check failed")
        installed._verify_installed_locked(state)
        journal_path.unlink()
        _fsync_directory(journal_path.parent)

    def uninstall(self) -> dict[str, Any]:
        self._preflight_lifecycle()
        self._validate_config_source()
        with self._lock():
            self._recover_pending_locked()
            state = self._load_state()
            if state is None:
                return {"status": "absent", "data_preserved": True}
            self._verify_installed_locked(state)
            installed = self._installed_manager(state)
            installed._verify_all_releases(state)
            self._audit_owned_install_tree(state)
            tombstone = self._uninstall_tombstone_path
            if tombstone.exists() or tombstone.is_symlink():
                raise PortableInstallError("unowned uninstall tombstone exists")
            manifest_preimage = {
                raw_path: Path(raw_path).read_bytes()
                for raw_path in state["owned_service_files"]
            }
            journal = {
                "schema_version": 1,
                "consumer_id": self.config.consumer_id,
                "phase": "prepared",
                "install_root": str(self.config.install_root),
                "tombstone_path": str(tombstone),
                "manifest_preimage": self._encode_preimage(manifest_preimage),
            }
            _atomic_json(self._uninstall_transaction_path, journal)
            try:
                installed._deactivate_services()
                for raw_path, checksum in state["owned_service_files"].items():
                    path = Path(raw_path)
                    if _sha256(path) != checksum:
                        raise PortableInstallError(f"owned file drift: {path}")
                    path.unlink()
                _fsync_directory(installed.config.service_root)
                if installed.config.platform == "systemd-user":
                    installed._command_runner(
                        ("/usr/bin/systemctl", "--user", "daemon-reload")
                    )
                self.config.install_root.rename(tombstone)
                _fsync_directory(self.config.install_root.parent)
                journal["phase"] = "committed"
                _atomic_json(self._uninstall_transaction_path, journal)
                self._remove_uninstall_tombstone()
                self._uninstall_transaction_path.unlink()
                _fsync_directory(self._uninstall_transaction_path.parent)
            except Exception as error:
                try:
                    self._recover_uninstall_locked()
                except Exception as recovery_error:
                    raise PortableInstallError(
                        "uninstall recovery failed"
                    ) from recovery_error
                if isinstance(error, PortableInstallError):
                    raise
                raise PortableInstallError("uninstall failed") from error
            return {
                "status": "uninstalled",
                "data_preserved": True,
                "data_root": str(self.config.data_root),
                "state_root": str(self.config.state_root),
            }
