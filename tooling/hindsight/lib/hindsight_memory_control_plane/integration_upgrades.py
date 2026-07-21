"""Versioned, compatibility-gated upstream harness integration upgrades."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from types import MappingProxyType
from typing import Any
from urllib.request import build_opener, HTTPRedirectHandler, Request
from urllib.parse import urlsplit

from .canonical import StrictJsonError, canonical_bytes, digest, strict_json_loads


SUPPORTED_HARNESSES = frozenset({"codex", "claude-code", "cursor"})
TRANSPORT_MODES = frozenset({"broker-v1", "direct-only"})
UPDATE_POLICIES = frozenset({"pinned", "manual", "automatic-compatible"})
REQUIRED_CHECKS = (
    "disposable",
    "hook_schema",
    "transcript",
    "security",
    "broker_transport",
)
MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "harness_id",
        "version",
        "source_url",
        "artifact_sha256",
        "artifact_size",
        "publisher",
        "transport_mode",
        "hook_schema_version",
        "transcript_schema_version",
    }
)
REPORT_KEYS = frozenset(
    {"schema_version", "harness_id", "version", "artifact_sha256", "checks"}
)
CATALOG_KEYS = frozenset(
    {
        "schema_version",
        "catalog_id",
        "harness_id",
        "publisher",
        "source_origin",
        "manifest_url",
        "verifier_identity",
        "allowed_transport_modes",
    }
)
POLICY_KEYS = frozenset(
    {
        "schema_version",
        "harness_id",
        "catalog_id",
        "initial_version",
        "channel",
        "allowed_major",
        "update_policy",
        "retained_generations",
    }
)
ATTESTATION_KEYS = frozenset(
    {
        "schema_version",
        "catalog_id",
        "harness_id",
        "version",
        "artifact_sha256",
        "publisher",
        "source_url",
        "verifier_identity",
        "verified",
    }
)
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}\Z")
SEMANTIC_VERSION = re.compile(
    r"(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)(?:[-+][0-9A-Za-z.-]+)?\Z"
)
IDENTIFIER = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}\Z")
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024


class IntegrationUpgradeError(RuntimeError):
    """An integration upgrade could not be proven safe or applied."""


def _closed(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise IntegrationUpgradeError(
            f"{label} keys are closed (missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)})"
        )


def _identifier(value: Any, label: str, pattern: re.Pattern[str] = IDENTIFIER) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise IntegrationUpgradeError(f"{label} is invalid")
    return value


def _https_url(value: Any) -> str:
    if not isinstance(value, str) or not value or any(item.isspace() for item in value):
        raise IntegrationUpgradeError("package source URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise IntegrationUpgradeError("package source URL must be credential-free HTTPS")
    try:
        port = parsed.port
    except ValueError as exc:
        raise IntegrationUpgradeError("package source URL port is invalid") from exc
    if port == 0:
        raise IntegrationUpgradeError("package source URL port cannot be zero")
    return value


def _origin(value: Any) -> str:
    url = _https_url(value)
    parsed = urlsplit(url)
    if parsed.path not in {"", "/"} or parsed.query:
        raise IntegrationUpgradeError("catalog source origin must not include a path or query")
    return _normalized_origin(parsed.hostname, parsed.port)


def _url_origin(value: Any) -> str:
    url = _https_url(value)
    parsed = urlsplit(url)
    return _normalized_origin(parsed.hostname, parsed.port)


def _normalized_origin(hostname: str | None, port: int | None) -> str:
    if hostname is None:
        raise IntegrationUpgradeError("package source URL is invalid")
    host = f"[{hostname}]" if ":" in hostname else hostname
    suffix = f":{port}" if port not in {None, 443} else ""
    return f"https://{host}{suffix}"


class _SameOriginRedirectHandler(HTTPRedirectHandler):
    def __init__(self, trusted_origin: str) -> None:
        super().__init__()
        self.trusted_origin = trusted_origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _url_origin(newurl) != self.trusted_origin:
            raise IntegrationUpgradeError(
                "package redirect crossed the trusted source origin"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _version_major(version: str) -> int:
    return _version_key(version)[0]


def _version_key(version: str) -> tuple[int, int, int]:
    matched = SEMANTIC_VERSION.fullmatch(version)
    if matched is None:
        raise IntegrationUpgradeError("package version must be semantic")
    return tuple(int(matched.group(name)) for name in ("major", "minor", "patch"))


@dataclass(frozen=True)
class PackageManifest:
    harness_id: str
    version: str
    source_url: str
    artifact_sha256: str
    artifact_size: int
    publisher: str
    transport_mode: str
    hook_schema_version: int
    transcript_schema_version: int

    @classmethod
    def load(cls, value: Mapping[str, Any]) -> "PackageManifest":
        if not isinstance(value, Mapping):
            raise IntegrationUpgradeError("package manifest must be an object")
        _closed(value, MANIFEST_KEYS, "package manifest")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise IntegrationUpgradeError("package manifest schema_version must be 1")
        harness_id = _identifier(value["harness_id"], "harness ID")
        if harness_id not in SUPPORTED_HARNESSES:
            raise IntegrationUpgradeError("package harness is unsupported")
        version = _identifier(value["version"], "package version", VERSION)
        _version_key(version)
        artifact_sha256 = value["artifact_sha256"]
        if not isinstance(artifact_sha256, str) or DIGEST.fullmatch(artifact_sha256) is None:
            raise IntegrationUpgradeError("package artifact digest is invalid")
        artifact_size = value["artifact_size"]
        if (
            type(artifact_size) is not int
            or artifact_size <= 0
            or artifact_size > MAX_ARTIFACT_BYTES
        ):
            raise IntegrationUpgradeError("package artifact size is invalid")
        transport_mode = value["transport_mode"]
        if transport_mode not in TRANSPORT_MODES:
            raise IntegrationUpgradeError("package transport mode is invalid")
        for key in ("hook_schema_version", "transcript_schema_version"):
            if type(value[key]) is not int or value[key] != 1:
                raise IntegrationUpgradeError(f"{key} is unsupported")
        return cls(
            harness_id=harness_id,
            version=version,
            source_url=_https_url(value["source_url"]),
            artifact_sha256=artifact_sha256,
            artifact_size=artifact_size,
            publisher=_identifier(value["publisher"], "package publisher"),
            transport_mode=transport_mode,
            hook_schema_version=value["hook_schema_version"],
            transcript_schema_version=value["transcript_schema_version"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "harness_id": self.harness_id,
            "version": self.version,
            "source_url": self.source_url,
            "artifact_sha256": self.artifact_sha256,
            "artifact_size": self.artifact_size,
            "publisher": self.publisher,
            "transport_mode": self.transport_mode,
            "hook_schema_version": self.hook_schema_version,
            "transcript_schema_version": self.transcript_schema_version,
        }


@dataclass(frozen=True)
class IntegrationCatalogEntry:
    catalog_id: str
    harness_id: str
    publisher: str
    source_origin: str
    manifest_url: str
    verifier_identity: str
    allowed_transport_modes: tuple[str, ...]

    @classmethod
    def load(cls, value: Mapping[str, Any]) -> "IntegrationCatalogEntry":
        if not isinstance(value, Mapping):
            raise IntegrationUpgradeError("integration catalog entry must be an object")
        _closed(value, CATALOG_KEYS, "integration catalog entry")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise IntegrationUpgradeError("integration catalog schema_version must be 1")
        harness_id = _identifier(value["harness_id"], "catalog harness ID")
        if harness_id not in SUPPORTED_HARNESSES:
            raise IntegrationUpgradeError("catalog harness is unsupported")
        modes = value["allowed_transport_modes"]
        if (
            not isinstance(modes, list)
            or not modes
            or any(mode not in TRANSPORT_MODES for mode in modes)
            or len(set(modes)) != len(modes)
        ):
            raise IntegrationUpgradeError("catalog transport modes are invalid")
        source_origin = _origin(value["source_origin"])
        manifest_url = _https_url(value["manifest_url"])
        if _url_origin(manifest_url) != source_origin:
            raise IntegrationUpgradeError(
                "catalog manifest URL must use the catalog source origin"
            )
        return cls(
            catalog_id=_identifier(value["catalog_id"], "catalog ID"),
            harness_id=harness_id,
            publisher=_identifier(value["publisher"], "catalog publisher"),
            source_origin=source_origin,
            manifest_url=manifest_url,
            verifier_identity=_identifier(
                value["verifier_identity"], "catalog verifier identity"
            ),
            allowed_transport_modes=tuple(sorted(modes)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "catalog_id": self.catalog_id,
            "harness_id": self.harness_id,
            "publisher": self.publisher,
            "source_origin": self.source_origin,
            "manifest_url": self.manifest_url,
            "verifier_identity": self.verifier_identity,
            "allowed_transport_modes": list(self.allowed_transport_modes),
        }

    @property
    def catalog_digest(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class IntegrationUpdatePolicy:
    harness_id: str
    catalog_id: str
    initial_version: str
    channel: str
    allowed_major: int
    update_policy: str
    retained_generations: int

    @classmethod
    def load(cls, value: Mapping[str, Any]) -> "IntegrationUpdatePolicy":
        if not isinstance(value, Mapping):
            raise IntegrationUpgradeError("integration update policy must be an object")
        _closed(value, POLICY_KEYS, "integration update policy")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise IntegrationUpgradeError("integration update policy schema_version must be 1")
        harness_id = _identifier(value["harness_id"], "policy harness ID")
        initial_version = _identifier(
            value["initial_version"], "initial integration version", VERSION
        )
        allowed_major = value["allowed_major"]
        if type(allowed_major) is not int or allowed_major < 0:
            raise IntegrationUpgradeError("allowed integration major is invalid")
        if _version_major(initial_version) != allowed_major:
            raise IntegrationUpgradeError("initial integration version violates allowed major")
        update_policy = value["update_policy"]
        if update_policy not in UPDATE_POLICIES:
            raise IntegrationUpgradeError("integration update policy mode is invalid")
        retained_generations = value["retained_generations"]
        if (
            type(retained_generations) is not int
            or not 0 <= retained_generations <= 32
        ):
            raise IntegrationUpgradeError(
                "integration retained generations is invalid"
            )
        return cls(
            harness_id=harness_id,
            catalog_id=_identifier(value["catalog_id"], "policy catalog ID"),
            initial_version=initial_version,
            channel=_identifier(value["channel"], "integration update channel"),
            allowed_major=allowed_major,
            update_policy=update_policy,
            retained_generations=retained_generations,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "harness_id": self.harness_id,
            "catalog_id": self.catalog_id,
            "initial_version": self.initial_version,
            "channel": self.channel,
            "allowed_major": self.allowed_major,
            "update_policy": self.update_policy,
            "retained_generations": self.retained_generations,
        }

    @property
    def policy_digest(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class IntegrationUpgradePlan:
    manifest: PackageManifest
    candidate_path: str
    report_digest: str
    source_attestation_digest: str
    catalog_digest: str
    policy_digest: str
    source_verifier_digest: str
    compatibility_runner_digest: str
    smoke_runner_digest: str
    disposition: str
    memory_authority: bool
    expected_current_digest: str
    plan_digest: str
    compatibility_checks: Mapping[str, bool] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "compatibility_checks",
            MappingProxyType(dict(self.compatibility_checks)),
        )

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "manifest": self.manifest.to_dict(),
            "candidate_path": self.candidate_path,
            "report_digest": self.report_digest,
            "source_attestation_digest": self.source_attestation_digest,
            "catalog_digest": self.catalog_digest,
            "policy_digest": self.policy_digest,
            "source_verifier_digest": self.source_verifier_digest,
            "compatibility_runner_digest": self.compatibility_runner_digest,
            "smoke_runner_digest": self.smoke_runner_digest,
            "disposition": self.disposition,
            "memory_authority": self.memory_authority,
            "expected_current_digest": self.expected_current_digest,
            "compatibility_checks": dict(self.compatibility_checks),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "plan_digest": self.plan_digest}

    @classmethod
    def load(cls, value: Mapping[str, Any]) -> "IntegrationUpgradePlan":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "manifest",
            "candidate_path",
            "report_digest",
            "source_attestation_digest",
            "catalog_digest",
            "policy_digest",
            "source_verifier_digest",
            "compatibility_runner_digest",
            "smoke_runner_digest",
            "disposition",
            "memory_authority",
            "expected_current_digest",
            "compatibility_checks",
            "plan_digest",
        }:
            raise IntegrationUpgradeError("upgrade plan shape is invalid")
        if value["schema_version"] != 1:
            raise IntegrationUpgradeError("upgrade plan schema is invalid")
        manifest = PackageManifest.load(value["manifest"])
        checks = value["compatibility_checks"]
        if not isinstance(checks, Mapping) or set(checks) != set(REQUIRED_CHECKS):
            raise IntegrationUpgradeError("upgrade plan checks are invalid")
        if any(type(checks[key]) is not bool for key in REQUIRED_CHECKS):
            raise IntegrationUpgradeError("upgrade plan checks are invalid")
        for key in (
            "report_digest",
            "source_attestation_digest",
            "catalog_digest",
            "policy_digest",
            "source_verifier_digest",
            "compatibility_runner_digest",
            "smoke_runner_digest",
            "expected_current_digest",
            "plan_digest",
        ):
            if not isinstance(value[key], str) or DIGEST.fullmatch(value[key]) is None:
                raise IntegrationUpgradeError(f"upgrade plan {key} is invalid")
        if type(value["memory_authority"]) is not bool:
            raise IntegrationUpgradeError("upgrade plan memory authority is invalid")
        plan = cls(
            manifest=manifest,
            candidate_path=_candidate_relative_path(manifest),
            report_digest=value["report_digest"],
            source_attestation_digest=value["source_attestation_digest"],
            catalog_digest=value["catalog_digest"],
            policy_digest=value["policy_digest"],
            source_verifier_digest=value["source_verifier_digest"],
            compatibility_runner_digest=value["compatibility_runner_digest"],
            smoke_runner_digest=value["smoke_runner_digest"],
            disposition=value["disposition"],
            memory_authority=value["memory_authority"],
            expected_current_digest=value["expected_current_digest"],
            plan_digest=value["plan_digest"],
            compatibility_checks={key: checks[key] for key in REQUIRED_CHECKS},
        )
        if value["candidate_path"] != plan.candidate_path or digest(plan.body()) != plan.plan_digest:
            raise IntegrationUpgradeError("upgrade plan digest is invalid")
        return plan


def _candidate_relative_path(manifest: PackageManifest) -> str:
    return str(
        Path("releases")
        / manifest.harness_id
        / manifest.version
        / manifest.artifact_sha256
        / "package"
    )


def _private_directory(path: Path) -> None:
    missing: list[Path] = []
    probe = path
    while True:
        try:
            probe.lstat()
        except FileNotFoundError:
            missing.append(probe)
            if probe == probe.parent:
                raise IntegrationUpgradeError(
                    "upgrade state directory ancestry is unavailable"
                )
            probe = probe.parent
            continue
        break
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise IntegrationUpgradeError("upgrade state directory is not private")
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise IntegrationUpgradeError("upgrade state directory is not private")


def _existing_private_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise IntegrationUpgradeError("upgrade state directory is unavailable") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise IntegrationUpgradeError("upgrade state directory is not private")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or info.st_size > 4 * 1024 * 1024
    ):
        raise IntegrationUpgradeError("upgrade state file is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IntegrationUpgradeError("upgrade state file is invalid") from exc
    if not isinstance(value, dict):
        raise IntegrationUpgradeError("upgrade state file must be an object")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    _private_directory(path.parent)
    payload = canonical_bytes(value) + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _remove(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise IntegrationUpgradeError("upgrade state entry is unsafe")
    path.unlink()


@contextmanager
def _lifecycle_lock(state_dir: Path):
    _private_directory(state_dir)
    lock_path = state_dir / "lifecycle.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            raise IntegrationUpgradeError("upgrade lifecycle lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _immutable_package(path: Path, payload: bytes, manifest: PackageManifest) -> None:
    if not isinstance(payload, bytes):
        raise IntegrationUpgradeError("package payload must be bytes")
    if len(payload) != manifest.artifact_size:
        raise IntegrationUpgradeError("package artifact size mismatch")
    if hashlib.sha256(payload).hexdigest() != manifest.artifact_sha256:
        raise IntegrationUpgradeError("package artifact digest mismatch")
    _private_directory(path.parent)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
    except FileExistsError:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_mode & 0o377
            or path.read_bytes() != payload
        ):
            raise IntegrationUpgradeError("staged package does not match immutable candidate")
        return
    with os.fdopen(descriptor, "wb", closefd=True) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _validated_report(
    value: Mapping[str, Any], manifest: PackageManifest
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntegrationUpgradeError("compatibility runner returned no report")
    _closed(value, REPORT_KEYS, "compatibility report")
    if (
        value["schema_version"] != 1
        or value["harness_id"] != manifest.harness_id
        or value["version"] != manifest.version
        or value["artifact_sha256"] != manifest.artifact_sha256
    ):
        raise IntegrationUpgradeError("compatibility report identity is invalid")
    checks = value["checks"]
    if not isinstance(checks, Mapping) or set(checks) != set(REQUIRED_CHECKS):
        raise IntegrationUpgradeError("compatibility report checks are invalid")
    if any(type(checks[key]) is not bool for key in REQUIRED_CHECKS):
        raise IntegrationUpgradeError("compatibility report checks are invalid")
    return {
        "schema_version": 1,
        "harness_id": manifest.harness_id,
        "version": manifest.version,
        "artifact_sha256": manifest.artifact_sha256,
        "checks": {key: checks[key] for key in REQUIRED_CHECKS},
    }


def _validated_attestation(
    value: Mapping[str, Any],
    manifest: PackageManifest,
    catalog: IntegrationCatalogEntry,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntegrationUpgradeError("source verifier returned no source attestation")
    _closed(value, ATTESTATION_KEYS, "source attestation")
    expected = {
        "schema_version": 1,
        "catalog_id": catalog.catalog_id,
        "harness_id": manifest.harness_id,
        "version": manifest.version,
        "artifact_sha256": manifest.artifact_sha256,
        "publisher": manifest.publisher,
        "source_url": manifest.source_url,
        "verifier_identity": catalog.verifier_identity,
        "verified": True,
    }
    if dict(value) != expected:
        raise IntegrationUpgradeError("source attestation identity or verification is invalid")
    return expected


def read_integration_upgrade_status(
    state_dir: str | Path, harness_id: str
) -> dict[str, Any]:
    """Read one integration generation without creating or repairing state."""

    root = Path(state_dir)
    if not root.is_absolute():
        raise IntegrationUpgradeError("upgrade state directory must be absolute")
    _existing_private_directory(root)
    harness_id = _identifier(harness_id, "harness ID")
    if harness_id not in SUPPORTED_HARNESSES:
        raise IntegrationUpgradeError("package harness is unsupported")
    pointer_dir = root / "pointers" / harness_id
    quarantine_dir = root / "quarantine" / harness_id
    quarantine: list[dict[str, Any]] = []
    if quarantine_dir.is_dir():
        for path in sorted(quarantine_dir.glob("*.json")):
            value = _read_json(path)
            if value is not None:
                quarantine.append(value)
    def pointer(name: str) -> Path:
        return pointer_dir / f"{name}.json"
    authority = _read_json(pointer("authority"))
    transaction = _read_json(pointer("transaction"))
    return {
        "schema_version": 1,
        "harness_id": harness_id,
        "current": _read_json(pointer("current")),
        "authority": authority,
        "authority_digest": digest(authority),
        "last_known_good": _read_json(pointer("last-known-good")),
        "pending": _read_json(pointer("pending")),
        "quarantine": quarantine,
        "transaction_pending": transaction is not None,
    }


def read_integration_authority_set_digest(
    state_dir: str | Path, harness_ids: tuple[str, ...] | list[str]
) -> str:
    """Derive one broker generation from certified per-harness state."""

    if not isinstance(harness_ids, (tuple, list)) or not harness_ids:
        raise IntegrationUpgradeError("integration authority harness set is invalid")
    entries: dict[str, dict[str, str]] = {}
    for harness_id in sorted(set(harness_ids)):
        status = read_integration_upgrade_status(state_dir, harness_id)
        if status["transaction_pending"]:
            raise IntegrationUpgradeError(
                "integration authority transaction is in progress"
            )
        current = status["current"]
        authority = status["authority"]
        if authority is None:
            if isinstance(current, Mapping) and current.get("memory_authority") is True:
                raise IntegrationUpgradeError(
                    "certified integration authority record is unavailable"
                )
            entries[harness_id] = {"mode": "controller-owned"}
            continue
        expected_keys = {
            "schema_version",
            "harness_id",
            "version",
            "artifact_sha256",
            "candidate_path",
            "transport_mode",
            "memory_authority",
            "report_digest",
        }
        if (
            not isinstance(authority, Mapping)
            or set(authority) != expected_keys
            or authority.get("schema_version") != 1
            or authority.get("harness_id") != harness_id
            or authority.get("memory_authority") is not True
            or authority.get("transport_mode") != "broker-v1"
            or not isinstance(authority.get("artifact_sha256"), str)
            or DIGEST.fullmatch(authority["artifact_sha256"]) is None
            or not isinstance(authority.get("report_digest"), str)
            or DIGEST.fullmatch(authority["report_digest"]) is None
            or authority.get("candidate_path")
            != str(
                Path("releases")
                / harness_id
                / str(authority.get("version", ""))
                / authority.get("artifact_sha256", "")
                / "package"
            )
        ):
            raise IntegrationUpgradeError(
                "certified integration authority record is invalid"
            )
        entries[harness_id] = {
            "mode": "certified-upstream",
            "authority_digest": status["authority_digest"],
        }
    return digest({"schema_version": 1, "harnesses": entries})


def recover_integration_upgrade(
    state_dir: str | Path, harness_id: str
) -> dict[str, Any]:
    """Explicitly restore one interrupted lifecycle transaction."""

    root = Path(state_dir)
    if not root.is_absolute():
        raise IntegrationUpgradeError("upgrade state directory must be absolute")
    _existing_private_directory(root)
    harness_id = _identifier(harness_id, "harness ID")
    if harness_id not in SUPPORTED_HARNESSES:
        raise IntegrationUpgradeError("package harness is unsupported")
    pointer_dir = root / "pointers" / harness_id

    def pointer(name: str) -> Path:
        return pointer_dir / f"{name}.json"

    with _lifecycle_lock(root):
        journal_path = pointer("transaction")
        journal = _read_json(journal_path)
        if journal is None:
            return {"status": "clean", "harness_id": harness_id}
        expected_keys = {
            "schema_version",
            "operation",
            "harness_id",
            "before_current",
            "before_authority",
            "before_last_known_good",
            "after",
        }
        if set(journal) != expected_keys or journal["harness_id"] != harness_id:
            raise IntegrationUpgradeError("upgrade transaction journal is invalid")
        if journal["schema_version"] != 1 or journal["operation"] not in {
            "apply",
            "rollback",
        }:
            raise IntegrationUpgradeError("upgrade transaction journal is invalid")
        quarantine_record: tuple[Path, dict[str, Any]] | None = None
        after = journal["after"]
        if journal["operation"] == "apply":
            if not isinstance(after, Mapping):
                raise IntegrationUpgradeError("upgrade transaction journal is invalid")
            artifact_sha256 = after.get("artifact_sha256")
            if not isinstance(artifact_sha256, str) or DIGEST.fullmatch(
                artifact_sha256
            ) is None:
                raise IntegrationUpgradeError("upgrade transaction journal is invalid")
            plan_value = _read_json(
                root
                / "artifact-plans"
                / harness_id
                / f"{artifact_sha256}.json"
            )
            if plan_value is None:
                raise IntegrationUpgradeError(
                    "interrupted integration manifest is unavailable"
                )
            plan = IntegrationUpgradePlan.load(plan_value)
            manifest = plan.manifest
            if manifest.harness_id != harness_id:
                raise IntegrationUpgradeError(
                    "interrupted integration manifest is invalid"
                )
            quarantine_record = (
                root
                / "quarantine"
                / harness_id
                / f"{manifest.version}-{manifest.artifact_sha256}.json",
                {
                    "schema_version": 1,
                    "harness_id": manifest.harness_id,
                    "version": manifest.version,
                    "artifact_sha256": manifest.artifact_sha256,
                    "candidate_path": str(after.get("candidate_path", "")),
                    "reason": "interrupted-activation",
                },
            )
        for name, key in (
            ("current", "before_current"),
            ("authority", "before_authority"),
            ("last-known-good", "before_last_known_good"),
        ):
            before = journal[key]
            if before is not None and not isinstance(before, Mapping):
                raise IntegrationUpgradeError("upgrade transaction prestate is invalid")
            if before is None:
                _remove(pointer(name))
            else:
                _atomic_json(pointer(name), before)
        if quarantine_record is not None:
            _atomic_json(*quarantine_record)
        _remove(journal_path)
        return {"status": "recovered", "harness_id": harness_id}


class IntegrationUpgradeManager:
    """Own immutable candidates, compatibility evidence, and activation pointers."""

    def __init__(
        self,
        state_dir: str | Path,
        *,
        catalog: IntegrationCatalogEntry,
        policy: IntegrationUpdatePolicy,
        source_verifier: Callable[[PackageManifest], Mapping[str, Any]],
        source_verifier_digest: str,
        compatibility_runner: Callable[[Path, PackageManifest], Mapping[str, Any]],
        compatibility_runner_digest: str,
        smoke_runner: Callable[[Path, PackageManifest], bool],
        smoke_runner_digest: str,
    ) -> None:
        self.state_dir = Path(state_dir)
        if not self.state_dir.is_absolute():
            raise IntegrationUpgradeError("upgrade state directory must be absolute")
        if not isinstance(catalog, IntegrationCatalogEntry):
            raise IntegrationUpgradeError("integration catalog entry is invalid")
        if not isinstance(policy, IntegrationUpdatePolicy):
            raise IntegrationUpgradeError("integration update policy is invalid")
        if catalog.harness_id != policy.harness_id or catalog.catalog_id != policy.catalog_id:
            raise IntegrationUpgradeError("integration catalog and update policy do not match")
        for label, value in (
            ("source verifier", source_verifier_digest),
            ("compatibility runner", compatibility_runner_digest),
            ("smoke runner", smoke_runner_digest),
        ):
            if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
                raise IntegrationUpgradeError(f"{label} digest is invalid")
        self.catalog = catalog
        self.policy = policy
        self.source_verifier = source_verifier
        self.source_verifier_digest = source_verifier_digest
        self.compatibility_runner = compatibility_runner
        self.compatibility_runner_digest = compatibility_runner_digest
        self.smoke_runner = smoke_runner
        self.smoke_runner_digest = smoke_runner_digest
        _private_directory(self.state_dir)

    def _pointer_dir(self, harness_id: str) -> Path:
        path = self.state_dir / "pointers" / harness_id
        _private_directory(path)
        return path

    def _pointer(self, harness_id: str, name: str) -> Path:
        return self._pointer_dir(harness_id) / f"{name}.json"

    def _current(self, harness_id: str) -> dict[str, Any] | None:
        return _read_json(self._pointer(harness_id, "current"))

    def _current_digest(self, harness_id: str) -> str:
        return digest(self._current(harness_id))

    def _record(self, plan: IntegrationUpgradePlan) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "harness_id": plan.manifest.harness_id,
            "version": plan.manifest.version,
            "artifact_sha256": plan.manifest.artifact_sha256,
            "candidate_path": plan.candidate_path,
            "transport_mode": plan.manifest.transport_mode,
            "memory_authority": plan.memory_authority,
            "report_digest": plan.report_digest,
        }

    def _validate_catalog_binding(self, manifest: PackageManifest) -> None:
        if (
            manifest.harness_id != self.catalog.harness_id
            or manifest.publisher != self.catalog.publisher
            or manifest.transport_mode not in self.catalog.allowed_transport_modes
        ):
            raise IntegrationUpgradeError("package manifest is outside the trusted catalog")
        if _url_origin(manifest.source_url) != self.catalog.source_origin:
            raise IntegrationUpgradeError(
                "package source is outside the trusted catalog origin"
            )

    def _validate_update_target(self, manifest: PackageManifest) -> None:
        self._validate_catalog_binding(manifest)
        if _version_major(manifest.version) != self.policy.allowed_major:
            raise IntegrationUpgradeError(
                "package version violates the configured update policy"
            )
        if (
            self.policy.update_policy == "pinned"
            and manifest.version != self.policy.initial_version
        ):
            raise IntegrationUpgradeError("pinned integration policy rejects this version")

    def _quarantine(
        self, manifest: PackageManifest, candidate_path: str, reason: str
    ) -> None:
        path = (
            self.state_dir
            / "quarantine"
            / manifest.harness_id
            / f"{manifest.version}-{manifest.artifact_sha256}.json"
        )
        _atomic_json(
            path,
            {
                "schema_version": 1,
                "harness_id": manifest.harness_id,
                "version": manifest.version,
                "artifact_sha256": manifest.artifact_sha256,
                "candidate_path": candidate_path,
                "reason": reason,
            },
        )

    def _artifact_plan(
        self, harness_id: str, artifact_sha256: str
    ) -> IntegrationUpgradePlan:
        value = _read_json(
            self.state_dir
            / "artifact-plans"
            / harness_id
            / f"{artifact_sha256}.json"
        )
        if value is None:
            raise IntegrationUpgradeError("integration artifact plan is unavailable")
        plan = IntegrationUpgradePlan.load(value)
        if (
            plan.manifest.harness_id != harness_id
            or plan.manifest.artifact_sha256 != artifact_sha256
        ):
            raise IntegrationUpgradeError("integration artifact plan identity is invalid")
        return plan

    @staticmethod
    def _pointer_artifact(value: Any) -> str | None:
        if not isinstance(value, Mapping):
            return None
        artifact_sha256 = value.get("artifact_sha256")
        if artifact_sha256 is None and isinstance(value.get("manifest"), Mapping):
            artifact_sha256 = value["manifest"].get("artifact_sha256")
        if not isinstance(artifact_sha256, str) or DIGEST.fullmatch(
            artifact_sha256
        ) is None:
            raise IntegrationUpgradeError(
                "integration generation pointer is invalid"
            )
        return artifact_sha256

    @staticmethod
    def _remove_empty_parents(path: Path, stop: Path) -> None:
        parent = path.parent
        while parent != stop and parent.is_dir():
            try:
                parent.rmdir()
            except OSError:
                return
            parent = parent.parent

    def _prune_generations(self, harness_id: str) -> None:
        protected = {
            artifact_sha256
            for name in (
                "current",
                "authority",
                "last-known-good",
                "pending",
            )
            if (
                artifact_sha256 := self._pointer_artifact(
                    _read_json(self._pointer(harness_id, name))
                )
            )
            is not None
        }
        artifact_plan_dir = self.state_dir / "artifact-plans" / harness_id
        plans: list[IntegrationUpgradePlan] = []
        if artifact_plan_dir.is_dir():
            for path in artifact_plan_dir.glob("*.json"):
                plan = IntegrationUpgradePlan.load(_read_json(path))
                if plan.manifest.harness_id != harness_id:
                    raise IntegrationUpgradeError(
                        "integration artifact plan identity is invalid"
                    )
                plans.append(plan)
        plans.sort(
            key=lambda plan: (
                _version_key(plan.manifest.version),
                plan.manifest.artifact_sha256,
            ),
            reverse=True,
        )
        retained = set(protected)
        recent = 0
        for plan in plans:
            artifact_sha256 = plan.manifest.artifact_sha256
            if artifact_sha256 in retained:
                continue
            if recent < self.policy.retained_generations:
                retained.add(artifact_sha256)
                recent += 1
        for plan in plans:
            manifest = plan.manifest
            if manifest.artifact_sha256 in retained:
                continue
            release = self.state_dir / plan.candidate_path
            release_directory = release.parent
            if release_directory.is_dir():
                shutil.rmtree(release_directory)
                self._remove_empty_parents(
                    release_directory,
                    self.state_dir / "releases" / harness_id,
                )
            for evidence_root in ("reports", "attestations"):
                evidence = (
                    self.state_dir
                    / evidence_root
                    / harness_id
                    / manifest.version
                    / f"{manifest.artifact_sha256}.json"
                )
                _remove(evidence)
                self._remove_empty_parents(
                    evidence, self.state_dir / evidence_root / harness_id
                )
            _remove(self.state_dir / "plans" / f"{plan.plan_digest}.json")
            _remove(
                artifact_plan_dir / f"{manifest.artifact_sha256}.json"
            )
            _remove(
                self.state_dir
                / "quarantine"
                / harness_id
                / f"{manifest.version}-{manifest.artifact_sha256}.json"
            )

    def plan(
        self, manifest: PackageManifest, *, package: bytes
    ) -> IntegrationUpgradePlan:
        with _lifecycle_lock(self.state_dir):
            return self._plan_unlocked(manifest, package=package)

    def _plan_unlocked(
        self, manifest: PackageManifest, *, package: bytes
    ) -> IntegrationUpgradePlan:
        if not isinstance(manifest, PackageManifest):
            raise IntegrationUpgradeError("package manifest is invalid")
        if _read_json(self._pointer(manifest.harness_id, "transaction")) is not None:
            raise IntegrationUpgradeError(
                "integration upgrade recovery is required before planning"
            )
        self._validate_update_target(manifest)
        current = self._current(manifest.harness_id)
        if current is not None and _version_key(manifest.version) < _version_key(
            current["version"]
        ):
            raise IntegrationUpgradeError("integration downgrade is not allowed")
        version_directory = (
            self.state_dir / "releases" / manifest.harness_id / manifest.version
        )
        if version_directory.is_dir() and any(
            path.name != manifest.artifact_sha256 for path in version_directory.iterdir()
        ):
            raise IntegrationUpgradeError("integration version digest conflict")
        try:
            attestation = _validated_attestation(
                self.source_verifier(manifest), manifest, self.catalog
            )
        except Exception as exc:
            if isinstance(exc, IntegrationUpgradeError):
                raise
            raise IntegrationUpgradeError("source verifier failed") from None
        source_attestation_digest = digest(attestation)
        attestation_path = (
            self.state_dir
            / "attestations"
            / manifest.harness_id
            / manifest.version
            / f"{manifest.artifact_sha256}.json"
        )
        _atomic_json(attestation_path, attestation)
        os.chmod(attestation_path, 0o400)
        candidate_path = _candidate_relative_path(manifest)
        candidate = self.state_dir / candidate_path
        _immutable_package(candidate, package, manifest)
        try:
            report = _validated_report(
                self.compatibility_runner(candidate, manifest), manifest
            )
        except Exception as exc:
            self._quarantine(manifest, candidate_path, "runner-failure")
            if isinstance(exc, IntegrationUpgradeError):
                raise
            raise IntegrationUpgradeError("compatibility runner failed") from None
        report_digest = digest(report)
        report_path = (
            self.state_dir
            / "reports"
            / manifest.harness_id
            / manifest.version
            / f"{manifest.artifact_sha256}.json"
        )
        _atomic_json(report_path, report)
        os.chmod(report_path, 0o400)
        checks = report["checks"]
        base_compatible = all(
            checks[key] for key in REQUIRED_CHECKS if key != "broker_transport"
        )
        if not base_compatible:
            disposition = "quarantine"
            memory_authority = False
            self._quarantine(manifest, candidate_path, "compatibility")
        elif manifest.transport_mode == "broker-v1" and checks["broker_transport"]:
            disposition = "activate"
            memory_authority = True
        elif manifest.transport_mode == "direct-only":
            disposition = "select-without-authority"
            memory_authority = False
        else:
            disposition = "quarantine"
            memory_authority = False
            self._quarantine(manifest, candidate_path, "broker-transport")
        body = {
            "schema_version": 1,
            "manifest": manifest.to_dict(),
            "candidate_path": candidate_path,
            "report_digest": report_digest,
            "source_attestation_digest": source_attestation_digest,
            "catalog_digest": self.catalog.catalog_digest,
            "policy_digest": self.policy.policy_digest,
            "source_verifier_digest": self.source_verifier_digest,
            "compatibility_runner_digest": self.compatibility_runner_digest,
            "smoke_runner_digest": self.smoke_runner_digest,
            "disposition": disposition,
            "memory_authority": memory_authority,
            "expected_current_digest": self._current_digest(manifest.harness_id),
            "compatibility_checks": dict(checks),
        }
        plan = IntegrationUpgradePlan(
            manifest=manifest,
            candidate_path=candidate_path,
            report_digest=report_digest,
            source_attestation_digest=source_attestation_digest,
            catalog_digest=self.catalog.catalog_digest,
            policy_digest=self.policy.policy_digest,
            source_verifier_digest=self.source_verifier_digest,
            compatibility_runner_digest=self.compatibility_runner_digest,
            smoke_runner_digest=self.smoke_runner_digest,
            disposition=disposition,
            memory_authority=memory_authority,
            expected_current_digest=body["expected_current_digest"],
            plan_digest=digest(body),
            compatibility_checks=checks,
        )
        _atomic_json(self.state_dir / "plans" / f"{plan.plan_digest}.json", plan.to_dict())
        _atomic_json(
            self.state_dir
            / "artifact-plans"
            / manifest.harness_id
            / f"{manifest.artifact_sha256}.json",
            plan.to_dict(),
        )
        _atomic_json(self._pointer(manifest.harness_id, "pending"), plan.to_dict())
        self._prune_generations(manifest.harness_id)
        return plan

    def download_and_plan(
        self,
        manifest: PackageManifest,
        *,
        opener: Callable[..., Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> IntegrationUpgradePlan:
        """Download one declared artifact and plan it only after exact verification."""

        if not isinstance(manifest, PackageManifest):
            raise IntegrationUpgradeError("package manifest is invalid")
        self._validate_catalog_binding(manifest)
        if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 120:
            raise IntegrationUpgradeError("package download timeout is invalid")
        request = Request(
            manifest.source_url,
            headers={"Accept": "application/octet-stream", "User-Agent": "hindsight-memory/1"},
            method="GET",
        )
        open_request = (
            build_opener(
                _SameOriginRedirectHandler(self.catalog.source_origin)
            ).open
            if opener is None
            else opener
        )
        try:
            with open_request(request, timeout=timeout_seconds) as response:
                final_url = _https_url(response.geturl())
                if _url_origin(final_url) != self.catalog.source_origin:
                    raise IntegrationUpgradeError(
                        "package redirect crossed the trusted source origin"
                    )
                payload = bytearray()
                while True:
                    chunk = response.read(min(64 * 1024, manifest.artifact_size + 1))
                    if not chunk:
                        break
                    payload.extend(chunk)
                    if len(payload) > manifest.artifact_size:
                        raise IntegrationUpgradeError("downloaded package size mismatch")
        except IntegrationUpgradeError:
            raise
        except Exception as exc:
            raise IntegrationUpgradeError("package download failed") from exc
        if len(payload) != manifest.artifact_size:
            raise IntegrationUpgradeError("downloaded package size mismatch")
        if hashlib.sha256(payload).hexdigest() != manifest.artifact_sha256:
            raise IntegrationUpgradeError("downloaded package digest mismatch")
        return self.plan(manifest, package=bytes(payload))

    def fetch_manifest(
        self,
        *,
        opener: Callable[..., Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> PackageManifest:
        """Fetch the catalog's bounded same-origin update manifest."""

        if (
            not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 120
        ):
            raise IntegrationUpgradeError("manifest download timeout is invalid")
        request = Request(
            self.catalog.manifest_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "hindsight-memory/1",
            },
            method="GET",
        )
        open_request = (
            build_opener(
                _SameOriginRedirectHandler(self.catalog.source_origin)
            ).open
            if opener is None
            else opener
        )
        try:
            with open_request(request, timeout=timeout_seconds) as response:
                final_url = _https_url(response.geturl())
                if _url_origin(final_url) != self.catalog.source_origin:
                    raise IntegrationUpgradeError(
                        "manifest redirect crossed the trusted source origin"
                    )
                payload = bytearray()
                while True:
                    chunk = response.read(min(64 * 1024, MAX_MANIFEST_BYTES + 1))
                    if not chunk:
                        break
                    payload.extend(chunk)
                    if len(payload) > MAX_MANIFEST_BYTES:
                        raise IntegrationUpgradeError(
                            "integration manifest exceeds limit"
                        )
        except IntegrationUpgradeError:
            raise
        except Exception as exc:
            raise IntegrationUpgradeError("integration manifest download failed") from exc
        try:
            value = strict_json_loads(payload)
        except (StrictJsonError, UnicodeDecodeError) as exc:
            raise IntegrationUpgradeError("integration manifest is invalid") from exc
        if not isinstance(value, Mapping):
            raise IntegrationUpgradeError("integration manifest must be an object")
        manifest = PackageManifest.load(value)
        self._validate_update_target(manifest)
        return manifest

    def check_for_update(
        self,
        *,
        opener: Callable[..., Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Fetch, test, and conditionally activate the catalog's current release."""

        manifest = self.fetch_manifest(
            opener=opener,
            timeout_seconds=timeout_seconds,
        )
        if _read_json(self._pointer(manifest.harness_id, "transaction")) is not None:
            raise IntegrationUpgradeError(
                "integration upgrade recovery is required before checking"
            )
        current = self._current(manifest.harness_id)
        if (
            current is not None
            and current.get("version") == manifest.version
            and current.get("artifact_sha256") == manifest.artifact_sha256
        ):
            return {
                "status": "current",
                "harness_id": manifest.harness_id,
                "version": manifest.version,
                "artifact_sha256": manifest.artifact_sha256,
                "memory_authority": current.get("memory_authority") is True,
            }
        plan = self.download_and_plan(
            manifest,
            opener=opener,
            timeout_seconds=timeout_seconds,
        )
        if plan.disposition == "quarantine":
            return {
                "status": "quarantined",
                "harness_id": manifest.harness_id,
                "version": manifest.version,
                "artifact_sha256": manifest.artifact_sha256,
                "memory_authority": False,
                "plan_digest": plan.plan_digest,
            }
        if self.policy.update_policy == "automatic-compatible":
            return self.apply(
                plan,
                approval_digest=plan.plan_digest,
                automatic=True,
            )
        return {
            "status": "planned",
            "harness_id": manifest.harness_id,
            "version": manifest.version,
            "artifact_sha256": manifest.artifact_sha256,
            "memory_authority": plan.memory_authority,
            "plan_digest": plan.plan_digest,
        }

    def _validate_plan(self, plan: IntegrationUpgradePlan) -> None:
        if not isinstance(plan, IntegrationUpgradePlan):
            raise IntegrationUpgradeError("upgrade plan is invalid")
        stored = _read_json(self.state_dir / "plans" / f"{plan.plan_digest}.json")
        pending = _read_json(self._pointer(plan.manifest.harness_id, "pending"))
        if stored != plan.to_dict() or pending != plan.to_dict():
            raise IntegrationUpgradeError("upgrade plan is not the exact pending plan")
        if (
            plan.catalog_digest != self.catalog.catalog_digest
            or plan.policy_digest != self.policy.policy_digest
            or plan.source_verifier_digest != self.source_verifier_digest
            or plan.compatibility_runner_digest != self.compatibility_runner_digest
            or plan.smoke_runner_digest != self.smoke_runner_digest
        ):
            raise IntegrationUpgradeError("upgrade plan authority inputs changed")
        candidate = self.state_dir / plan.candidate_path
        payload = candidate.read_bytes()
        if (
            len(payload) != plan.manifest.artifact_size
            or hashlib.sha256(payload).hexdigest() != plan.manifest.artifact_sha256
        ):
            raise IntegrationUpgradeError("upgrade candidate changed after planning")
        report_path = (
            self.state_dir
            / "reports"
            / plan.manifest.harness_id
            / plan.manifest.version
            / f"{plan.manifest.artifact_sha256}.json"
        )
        report = _read_json(report_path)
        if report is None or digest(report) != plan.report_digest:
            raise IntegrationUpgradeError("upgrade compatibility evidence changed")
        attestation_path = (
            self.state_dir
            / "attestations"
            / plan.manifest.harness_id
            / plan.manifest.version
            / f"{plan.manifest.artifact_sha256}.json"
        )
        attestation = _read_json(attestation_path)
        if attestation is None or digest(attestation) != plan.source_attestation_digest:
            raise IntegrationUpgradeError("upgrade source attestation changed")

    def apply(
        self,
        plan: IntegrationUpgradePlan,
        *,
        approval_digest: str,
        automatic: bool = False,
    ) -> dict[str, Any]:
        with _lifecycle_lock(self.state_dir):
            return self._apply_unlocked(
                plan,
                approval_digest=approval_digest,
                automatic=automatic,
            )

    def _apply_unlocked(
        self,
        plan: IntegrationUpgradePlan,
        *,
        approval_digest: str,
        automatic: bool = False,
    ) -> dict[str, Any]:
        if type(automatic) is not bool:
            raise IntegrationUpgradeError("automatic upgrade mode is invalid")
        if automatic and self.policy.update_policy != "automatic-compatible":
            raise IntegrationUpgradeError("integration update requires manual approval")
        self._validate_plan(plan)
        if approval_digest != plan.plan_digest:
            raise IntegrationUpgradeError("upgrade approval digest does not match plan")
        if plan.disposition == "quarantine":
            raise IntegrationUpgradeError("upgrade candidate is not activatable")
        harness_id = plan.manifest.harness_id
        if self._current_digest(harness_id) != plan.expected_current_digest:
            raise IntegrationUpgradeError("upgrade current state changed after planning")
        before = self._current(harness_id)
        before_authority = _read_json(self._pointer(harness_id, "authority"))
        before_last_known_good = _read_json(
            self._pointer(harness_id, "last-known-good")
        )
        after = self._record(plan)
        journal = {
            "schema_version": 1,
            "operation": "apply",
            "harness_id": harness_id,
            "before_current": before,
            "before_authority": before_authority,
            "before_last_known_good": before_last_known_good,
            "after": after,
        }
        journal_path = self._pointer(harness_id, "transaction")
        _atomic_json(journal_path, journal)
        _atomic_json(self._pointer(harness_id, "current"), after)
        candidate = self.state_dir / plan.candidate_path
        smoke_passed = False
        try:
            smoke_passed = self.smoke_runner(candidate, plan.manifest) is True
        except Exception:
            smoke_passed = False
        if not smoke_passed:
            self._restore_pointer(harness_id, "current", before)
            self._restore_pointer(harness_id, "authority", before_authority)
            self._restore_pointer(
                harness_id, "last-known-good", before_last_known_good
            )
            self._quarantine(plan.manifest, plan.candidate_path, "post-activation-smoke")
            _remove(journal_path)
            raise IntegrationUpgradeError("post-activation smoke test failed; rolled back")
        if plan.memory_authority:
            _atomic_json(self._pointer(harness_id, "authority"), after)
            _atomic_json(
                self._pointer(harness_id, "last-known-good"),
                before_authority if before_authority is not None else after,
            )
        _remove(journal_path)
        _remove(self._pointer(harness_id, "pending"))
        self._prune_generations(harness_id)
        return {
            "status": (
                "activated" if plan.memory_authority else "selected"
            ),
            "harness_id": harness_id,
            "version": plan.manifest.version,
            "artifact_sha256": plan.manifest.artifact_sha256,
            "memory_authority": plan.memory_authority,
            "plan_digest": plan.plan_digest,
        }

    def _restore_pointer(
        self, harness_id: str, name: str, value: Mapping[str, Any] | None
    ) -> None:
        path = self._pointer(harness_id, name)
        if value is None:
            _remove(path)
        else:
            _atomic_json(path, value)

    def rollback(
        self, harness_id: str, *, expected_current_artifact_sha256: str
    ) -> dict[str, Any]:
        with _lifecycle_lock(self.state_dir):
            return self._rollback_unlocked(
                harness_id,
                expected_current_artifact_sha256=(
                    expected_current_artifact_sha256
                ),
            )

    def _rollback_unlocked(
        self, harness_id: str, *, expected_current_artifact_sha256: str
    ) -> dict[str, Any]:
        harness_id = _identifier(harness_id, "harness ID")
        if harness_id not in SUPPORTED_HARNESSES:
            raise IntegrationUpgradeError("package harness is unsupported")
        current = self._current(harness_id)
        if (
            current is None
            or current.get("artifact_sha256")
            != expected_current_artifact_sha256
        ):
            raise IntegrationUpgradeError("upgrade current digest changed")
        target = _read_json(self._pointer(harness_id, "last-known-good"))
        if target is None:
            raise IntegrationUpgradeError("last-known-good integration is unavailable")
        target_plan = self._artifact_plan(harness_id, target["artifact_sha256"])
        journal_path = self._pointer(harness_id, "transaction")
        _atomic_json(
            journal_path,
            {
                "schema_version": 1,
                "operation": "rollback",
                "harness_id": harness_id,
                "before_current": current,
                "before_authority": _read_json(
                    self._pointer(harness_id, "authority")
                ),
                "before_last_known_good": target,
                "after": target,
            },
        )
        _atomic_json(self._pointer(harness_id, "current"), target)
        candidate = self.state_dir / target["candidate_path"]
        try:
            rollback_passed = (
                self.smoke_runner(candidate, target_plan.manifest) is True
            )
        except Exception:
            rollback_passed = False
        if not rollback_passed:
            _atomic_json(self._pointer(harness_id, "current"), current)
            _remove(journal_path)
            raise IntegrationUpgradeError("rollback smoke test failed; current restored")
        _atomic_json(self._pointer(harness_id, "authority"), target)
        _remove(journal_path)
        self._prune_generations(harness_id)
        return {
            "status": "rolled-back",
            "harness_id": harness_id,
            "version": target["version"],
            "artifact_sha256": target["artifact_sha256"],
            "memory_authority": target["memory_authority"],
        }

    def status(self, harness_id: str) -> dict[str, Any]:
        return read_integration_upgrade_status(self.state_dir, harness_id)

    def recover_pending(self, harness_id: str) -> dict[str, Any]:
        return recover_integration_upgrade(self.state_dir, harness_id)
