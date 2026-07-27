#!/usr/bin/env python3
"""Launch one descriptor-bound Task Witness runtime generation.

The launcher is deliberately small and stdlib-only.  Its installed copy must be
protected by deployment policy; same-UID filesystem replacement cannot be made
adversarially safe by a Python process that is allowed to read the replacement.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import pwd
import re
import stat
import sys
import tempfile
import types
from pathlib import Path
from types import MappingProxyType
from typing import Any

ACTIVE_CONTRACT = "task-witness-launch-active-v1"
ENVELOPE_CONTRACT = "task-witness-launch-envelope-v1"
RUNTIME_ARTIFACT_MANIFEST_CONTRACT = "task-witness-runtime-artifact-manifest-v2"
RUNTIME_CONTRACT = "task-witness-runtime-v1"
PAYLOAD_SPECS = (
    ("entrypoint", "task_witness.py"),
    ("canonical", "canonical.py"),
    ("bundle-io", "bundle_io.py"),
    ("trust", "trust.py"),
)
MAX_ACTIVE_BYTES = MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_JSON_NUMBER_CHARACTERS = 128
GENERATION = re.compile(r"sha256-[0-9a-f]{64}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
GIT_REVISION = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class LaunchError(ValueError):
    """The selected launcher generation cannot be executed safely."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def _number(token: str, parser: Any) -> Any:
    if len(token) > MAX_JSON_NUMBER_CHARACTERS:
        raise LaunchError("JSON numeric token exceeds the limit")
    value = parser(token)
    if type(value) is float and not math.isfinite(value):
        raise LaunchError("JSON contains an unsupported number")
    return value


def _interpreter_identity() -> dict[str, object]:
    try:
        executable = str(Path(sys.executable).resolve(strict=True))
    except OSError as error:
        raise LaunchError("launcher interpreter cannot be resolved") from error
    return {
        "executable": executable,
        "implementation": "cpython",
        "version": dict(zip(("major", "minor", "micro"), sys.version_info[:3])),
    }


def _private(metadata: os.stat_result, label: str) -> None:
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise LaunchError(f"{label} is not current-user private")


def _flags(*names: str) -> int:
    required = ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY")
    missing = [name for name in required if not hasattr(os, name)]
    if missing:
        raise LaunchError(
            f"required descriptor primitives unavailable: {', '.join(missing)}"
        )
    return os.O_RDONLY | sum(int(getattr(os, name)) for name in names)


def _read(
    descriptor: int, identity: tuple[int, int, int], label: str, limit: int
) -> bytes:
    if os.fstat(descriptor).st_size > limit:
        raise LaunchError(f"{label} exceeds the byte limit")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65536, limit - total + 1))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise LaunchError(f"{label} exceeds the byte limit")
    if _identity(os.fstat(descriptor)) != identity:
        raise LaunchError(f"{label} changed during descriptor read")
    return b"".join(chunks)


def _open_directory(
    parent: int, name: str, label: str, *, private: bool = False
) -> tuple[int, tuple[int, int, int]]:
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            raise LaunchError(f"{label} is not a directory")
        if private:
            _private(before, label)
        descriptor = os.open(
            name, _flags("O_CLOEXEC", "O_NOFOLLOW", "O_DIRECTORY"), dir_fd=parent
        )
        identity = _identity(os.fstat(descriptor))
        if identity != _identity(before):
            raise LaunchError(f"{label} changed during open")
        if private:
            _private(os.fstat(descriptor), label)
        return descriptor, identity
    except OSError as error:
        raise LaunchError(f"cannot open {label}") from error


def _open_file(
    parent: int, name: str, label: str, limit: int, *, private: bool = False
) -> tuple[int, tuple[int, int, int], bytes]:
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise LaunchError(f"{label} is not a regular file")
        if private:
            _private(before, label)
        descriptor = os.open(
            name, _flags("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"), dir_fd=parent
        )
        identity = _identity(os.fstat(descriptor))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or identity != _identity(
            before
        ):
            os.close(descriptor)
            raise LaunchError(f"{label} changed during open")
        if private:
            _private(os.fstat(descriptor), label)
        return descriptor, identity, _read(descriptor, identity, label, limit)
    except OSError as error:
        raise LaunchError(f"cannot open {label}") from error


def _json(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise LaunchError(f"{label} contains a duplicate key")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise LaunchError(f"{label} contains an unsupported number: {value}")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
            parse_int=lambda token: _number(token, int),
            parse_float=lambda token: _number(token, float),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise LaunchError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict) or raw != _canonical(value):
        raise LaunchError(f"{label} must be canonical JSON")
    return value


def _active_record(value: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    required = {
        "schema_version",
        "contract",
        "generation",
        "runtime_contract",
        "interpreter",
        "public_release",
        "payloads",
        "content_sha256",
    }
    if (
        set(value) != required
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["contract"] != ACTIVE_CONTRACT
    ):
        raise LaunchError("active record contract mismatch")
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    if (
        not isinstance(value["content_sha256"], str)
        or not HEX.fullmatch(value["content_sha256"])
        or value["content_sha256"] != _sha(_canonical(unsigned)[:-1])
    ):
        raise LaunchError("active record content digest mismatch")
    generation = value["generation"]
    if not isinstance(generation, str) or not GENERATION.fullmatch(generation):
        raise LaunchError("active record generation is invalid")
    if value["runtime_contract"] != RUNTIME_CONTRACT:
        raise LaunchError("active record runtime contract is invalid")
    interpreter = value["interpreter"]
    if (
        not isinstance(interpreter, dict)
        or set(interpreter) != {"executable", "implementation", "version"}
        or not isinstance(interpreter["executable"], str)
        or not isinstance(interpreter["implementation"], str)
        or not isinstance(interpreter["version"], dict)
        or set(interpreter["version"]) != {"major", "minor", "micro"}
        or any(
            type(interpreter["version"][part]) is not int
            for part in ("major", "minor", "micro")
        )
    ):
        raise LaunchError("active record interpreter identity is invalid")
    if interpreter != _interpreter_identity():
        raise LaunchError("active record interpreter identity disagreement")
    release = value["public_release"]
    if (
        not isinstance(release, dict)
        or set(release) != {"repository", "revision"}
        or not isinstance(release["repository"], str)
        or not REPOSITORY.fullmatch(release["repository"])
        or not isinstance(release["revision"], str)
        or not GIT_REVISION.fullmatch(release["revision"])
    ):
        raise LaunchError("active record public release is invalid")
    payload_specs = value["payloads"]
    if not isinstance(payload_specs, list) or len(payload_specs) != len(PAYLOAD_SPECS):
        raise LaunchError("active record payload order is invalid")
    for (expected_role, expected_path), item in zip(PAYLOAD_SPECS, payload_specs):
        if not isinstance(item, dict) or set(item) != {
            "role",
            "relative_path",
            "length",
            "sha256",
        }:
            raise LaunchError("active record payload schema is invalid")
        if (
            item["role"] != expected_role
            or item["relative_path"] != expected_path
            or type(item["length"]) is not int
            or not 0 <= item["length"] <= MAX_PAYLOAD_BYTES
            or not isinstance(item["sha256"], str)
            or not HEX.fullmatch(item["sha256"])
        ):
            raise LaunchError("active record payload order is invalid")
    return generation, payload_specs


def _runtime_implementation_identity(
    runtime_contract: str,
    payload_specs: list[dict[str, Any]],
    payloads: dict[str, bytes],
) -> str:
    framed_payloads = []
    for spec in payload_specs:
        raw = payloads[spec["relative_path"]]
        actual = {
            "role": spec["role"],
            "relative_path": spec["relative_path"],
            "length": len(raw),
            "sha256": _sha(raw),
        }
        if actual != spec:
            raise LaunchError("runtime payload identity disagreement")
        framed_payloads.append(actual)
    return _sha(
        _canonical(
            {
                "contract": RUNTIME_ARTIFACT_MANIFEST_CONTRACT,
                "runtime_contract": runtime_contract,
                "entrypoint_role": "entrypoint",
                "payloads": framed_payloads,
            }
        )[:-1]
    )


class _Snapshot:
    def __init__(self, root: Path) -> None:
        self.directories: list[tuple[int, tuple[int, int, int], str]] = []
        self.files: list[tuple[int, tuple[int, int, int], int, str, bytes]] = []
        self.root = root

    def close(self) -> None:
        for descriptor, _, _, _, _ in reversed(self.files):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self.files.clear()
        for descriptor, _, _ in reversed(self.directories):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self.directories.clear()

    def recheck(self) -> None:
        """Reread retained bytes before checking visible state.

        This lock-free check can let a prior verified generation finish.
        """
        try:
            for descriptor, identity, _, _, raw in self.files:
                if (
                    _read(
                        descriptor,
                        identity,
                        "retained runtime payload",
                        MAX_PAYLOAD_BYTES,
                    )
                    != raw
                ):
                    raise LaunchError("runtime payload changed during validation")
            for descriptor, identity, _ in self.directories:
                if _identity(os.fstat(descriptor)) != identity:
                    raise LaunchError("runtime ancestry changed during validation")
            for index, (_, identity, name) in enumerate(self.directories):
                if (
                    index
                    and _identity(
                        os.stat(
                            name,
                            dir_fd=self.directories[index - 1][0],
                            follow_symlinks=False,
                        )
                    )
                    != identity
                ):
                    raise LaunchError("runtime ancestry changed during validation")
            for _, identity, parent, name, _ in self.files:
                if (
                    _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    != identity
                ):
                    raise LaunchError("runtime payload changed during validation")
        except LaunchError:
            raise
        except OSError as error:
            raise LaunchError("runtime payload changed during validation") from error


class _LaunchContext:
    def __init__(
        self,
        snapshot: _Snapshot,
        active: dict[str, Any],
        payloads: dict[str, bytes],
        runtime_implementation_sha256: str,
    ) -> None:
        self._snapshot = snapshot
        self.runtime_contract = active["runtime_contract"]
        self.public_release = (
            active["public_release"]["repository"],
            active["public_release"]["revision"],
        )
        self.payload_specs = tuple(
            (
                item["role"],
                item["relative_path"],
                item["length"],
                item["sha256"],
            )
            for item in active["payloads"]
        )
        self.payloads = MappingProxyType(payloads.copy())
        self.runtime_implementation_sha256 = runtime_implementation_sha256

    def recheck(self) -> None:
        self._snapshot.recheck()


def _installed_root() -> Path:
    root = Path(pwd.getpwuid(os.geteuid()).pw_dir).joinpath(
        ".local", "libexec", "task-witness"
    )
    if (
        Path(__file__).resolve(strict=True)
        != root / "launcher" / "task_witness_launch.py"
    ):
        raise LaunchError("launcher path is not canonical")
    return root


def _snapshot(root: Path) -> tuple[_Snapshot, dict[str, Any], dict[str, bytes], str]:
    snapshot = _Snapshot(root)
    try:
        descriptor = os.open("/", _flags("O_CLOEXEC", "O_NOFOLLOW", "O_DIRECTORY"))
        snapshot.directories.append((descriptor, _identity(os.fstat(descriptor)), ""))
        for name in root.parts[1:]:
            descriptor, identity = _open_directory(
                snapshot.directories[-1][0], name, "launcher root"
            )
            snapshot.directories.append((descriptor, identity, name))
        root_descriptor = snapshot.directories[-1][0]
        _private(os.fstat(root_descriptor), "launcher root")
        active_descriptor, active_identity, active_raw = _open_file(
            root_descriptor,
            "active.json",
            "active record",
            MAX_ACTIVE_BYTES,
            private=True,
        )
        snapshot.files.append(
            (
                active_descriptor,
                active_identity,
                root_descriptor,
                "active.json",
                active_raw,
            )
        )
        active = _json(active_raw, "active record")
        generation, payload_specs = _active_record(active)
        generations_descriptor, generations_identity = _open_directory(
            root_descriptor, "generations", "generations", private=True
        )
        snapshot.directories.append(
            (generations_descriptor, generations_identity, "generations")
        )
        generation_descriptor, generation_identity = _open_directory(
            generations_descriptor, generation, "active generation", private=True
        )
        snapshot.directories.append(
            (generation_descriptor, generation_identity, generation)
        )
        payloads: dict[str, bytes] = {}
        for item in payload_specs:
            relative_path = item["relative_path"]
            descriptor, identity, raw = _open_file(
                generation_descriptor,
                relative_path,
                "runtime payload",
                MAX_PAYLOAD_BYTES,
                private=True,
            )
            snapshot.files.append(
                (descriptor, identity, generation_descriptor, relative_path, raw)
            )
            if len(raw) != item["length"] or _sha(raw) != item["sha256"]:
                raise LaunchError("runtime payload digest mismatch")
            payloads[relative_path] = raw
        runtime_identity = _runtime_implementation_identity(
            active["runtime_contract"], payload_specs, payloads
        )
        if generation != f"sha256-{runtime_identity}":
            raise LaunchError("active generation does not match runtime identity")
        return snapshot, active, payloads, _sha(active_raw)
    except BaseException:
        snapshot.close()
        raise


def _compile_runtime(raw: bytes, filename: str) -> types.CodeType:
    return compile(raw, filename, "exec", dont_inherit=True, optimize=0)


def _execute(
    active: dict[str, Any], payloads: dict[str, bytes], snapshot: _Snapshot
) -> types.ModuleType:
    runtime_identity = _runtime_implementation_identity(
        active["runtime_contract"], active["payloads"], payloads
    )
    context = _LaunchContext(snapshot, active, payloads, runtime_identity)
    snapshot.recheck()

    def module(name: str, injected: dict[str, Any]) -> types.ModuleType:
        runtime = types.ModuleType(f"task_witness_launch.{name}")
        filename = f"{name}.py"
        runtime.__file__ = str(
            snapshot.root / "generations" / active["generation"] / filename
        )
        runtime.__dict__.update({"_TASK_WITNESS_LAUNCH_CONTEXT": context, **injected})
        code = _compile_runtime(payloads[filename], runtime.__file__)
        exec(code, runtime.__dict__)  # noqa: S102
        return runtime

    canonical = module("canonical", {})
    bundle_io = module("bundle_io", {"_CANONICAL": canonical})
    trust = module("trust", {"_CANONICAL": canonical, "_BUNDLE_IO": bundle_io})
    return module(
        "task_witness",
        {"_CANONICAL": canonical, "_BUNDLE_IO": bundle_io, "_TRUST": trust},
    )


def _absolute(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise LaunchError(f"{label} must be absolute and traversal-free")
    return path


def _validate(
    bundle: Path, trust_context: Path, *, historical: bool = False
) -> dict[str, Any]:
    snapshot, active, payloads, active_digest = _snapshot(_installed_root())
    try:
        runtime = _execute(active, payloads, snapshot)
        witness = runtime.validate_bundle(
            bundle,
            trust_context_path=trust_context,
            new_publication=not historical,
        )
        try:
            snapshot.recheck()
        except LaunchError as error:
            raise LaunchError("runtime artifact changed") from error
        trust_context_sha256 = witness.get("trust_context_sha256")
        if not isinstance(trust_context_sha256, str) or not HEX.fullmatch(
            trust_context_sha256
        ):
            raise LaunchError("runtime did not bind the retained trust context")
        bundle_sha256 = witness.get("bundle_sha256")
        if not isinstance(bundle_sha256, str) or not HEX.fullmatch(bundle_sha256):
            raise LaunchError("runtime did not bind the retained bundle")
        runtime_implementation_sha256 = _runtime_implementation_identity(
            active["runtime_contract"], active["payloads"], payloads
        )
        if runtime.RUNTIME_IMPLEMENTATION_SHA256 != runtime_implementation_sha256:
            raise LaunchError("runtime implementation identity disagreement")
        return {
            "contract": ENVELOPE_CONTRACT,
            "anchor": {
                "generation": active["generation"],
                "active_record_sha256": active_digest,
                "runtime_contract": active["runtime_contract"],
                "interpreter": active["interpreter"],
                "public_release": active["public_release"],
                "runtime_implementation_sha256": runtime_implementation_sha256,
                "trust_context_sha256": trust_context_sha256,
                "bundle_sha256": bundle_sha256,
                "historical": historical,
            },
            "witness": witness,
        }
    finally:
        snapshot.close()


@contextlib.contextmanager
def _capture_payload_stdout() -> Any:
    sys.stdout.flush()
    saved = os.dup(sys.stdout.fileno())
    with tempfile.TemporaryFile() as captured:
        try:
            os.dup2(captured.fileno(), sys.stdout.fileno())
            yield captured
        finally:
            sys.stdout.flush()
            os.dup2(saved, sys.stdout.fileno())
            os.close(saved)


_CANONICAL_FLAGS = {
    "debug": 0,
    "inspect": 0,
    "interactive": 0,
    "optimize": 0,
    "dont_write_bytecode": 1,
    "no_user_site": 1,
    "no_site": 1,
    "ignore_environment": 1,
    "verbose": 0,
    "bytes_warning": 0,
    "quiet": 0,
    "hash_randomization": 1,
    "isolated": 1,
    "dev_mode": False,
    "utf8_mode": 0,
}


def _canonical_executor() -> bool:
    semantic = {name: getattr(sys.flags, name, None) for name in _CANONICAL_FLAGS}
    return (
        Path(sys.executable).is_absolute()
        and sys.implementation.name == "cpython"
        and not sys.warnoptions
        and not sys._xoptions
        and semantic == _CANONICAL_FLAGS
    )


def main(argv: list[str] | None = None) -> int:
    if not _canonical_executor():
        print(
            "task witness launch rejected: launcher requires a canonical executor",
            file=sys.stderr,
        )
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    validate_parser = command.add_parser("validate")
    validate_parser.add_argument("--bundle", required=True)
    validate_parser.add_argument("--trust-context", required=True)
    validate_parser.add_argument("--historical", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command != "validate":
            raise LaunchError("unsupported launcher command")
        with _capture_payload_stdout() as captured:
            envelope = _validate(
                _absolute(arguments.bundle, "bundle"),
                _absolute(arguments.trust_context, "trust context"),
                historical=arguments.historical,
            )
            sys.stdout.flush()
            captured.seek(0)
            if captured.read():
                raise LaunchError("runtime emitted unframed stdout")
        sys.stdout.buffer.write(_canonical(envelope))
    except KeyboardInterrupt:
        print("task witness launch interrupted", file=sys.stderr)
        return 130
    except BaseException as error:
        print(f"task witness launch rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
