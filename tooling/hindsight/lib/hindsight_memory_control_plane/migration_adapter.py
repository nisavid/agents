"""Compatibility-gated subprocess seam for the narrow hindsight-admin surface."""

from collections import OrderedDict
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from .canonical import DIGEST, canonical_bytes, digest, strict_json_loads
from .file_evidence import (
    FileEvidenceError,
    VerifiedFileSnapshot,
    file_identity,
    open_trusted_parent,
    reject_symlink_components,
    validate_trusted_regular_file,
    verified_file_snapshot,
)

_SUBPROCESS_RUN = subprocess.run

OPERATIONS = {"export-bank", "import-bank", "backup", "restore"}
BANK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
ADMIN_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "HINDSIGHT_API_DATABASE_URL",
        "LANG",
        "LC_ALL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TZ",
    }
)
VERSION_PROBE = (
    "import importlib.metadata as metadata; "
    "print(metadata.version('hindsight-api'))"
)
INTERPRETER_PREFIX_PROBE = """
# HINDSIGHT_INTERPRETER_PREFIX_V1
import sys
for value in (
    sys.prefix,
    sys.exec_prefix,
    sys.base_prefix,
    sys.base_exec_prefix,
    *sys.path,
):
    print(value.encode("utf-8").hex())
"""
RUNTIME_FILES_PROBE = """
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import site
import sys

PACKAGE_ROOTS = None
for package_root in (
    site.getsitepackages() if PACKAGE_ROOTS is None else PACKAGE_ROOTS
):
    if package_root not in sys.path:
        sys.path.append(package_root)
try:
    from packaging.requirements import InvalidRequirement, Requirement
except ImportError:
    from pip._vendor.packaging.requirements import InvalidRequirement, Requirement

# HINDSIGHT_RUNTIME_MANIFEST_V1
primary_distribution = metadata.distribution("hindsight-api")
source_root = Path(primary_distribution.locate_file("")).absolute()
distribution_root = Path(os.path.realpath(source_root))
pending = [primary_distribution]
distributions = []
seen = set()
while pending:
    distribution = pending.pop()
    distribution_name = re.sub(
        r"[-_.]+", "-", distribution.metadata["Name"]
    ).lower()
    if distribution_name in seen:
        continue
    seen.add(distribution_name)
    distributions.append(distribution)
    if len(distributions) > 256:
        raise SystemExit("hindsight-api runtime distribution closure is too large")
    for requirement_text in distribution.requires or ():
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement:
            raise SystemExit("hindsight-api dependency requirement is invalid")
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            pending.append(metadata.distribution(requirement.name))
        except metadata.PackageNotFoundError:
            raise SystemExit(
                "hindsight-api active dependency distribution is unavailable"
            )
paths = set()
for distribution in distributions:
    root = Path(os.path.realpath(distribution.locate_file("")))
    if root != distribution_root:
        raise SystemExit("hindsight-api dependency is outside its distribution root")
    for item in distribution.files or ():
        requested_path = Path(distribution.locate_file(item)).absolute()
        try:
            Path(os.path.realpath(requested_path)).relative_to(distribution_root)
        except ValueError:
            raise SystemExit(
                "hindsight-api runtime file is outside its distribution root"
            )
        paths.add(requested_path)
    top_level = distribution.read_text("top_level.txt") or ""
    package_names = [
        item.strip() for item in top_level.splitlines() if item.strip()
    ]
    for package_name in package_names:
        package_root = Path(distribution.locate_file(package_name)).absolute()
        try:
            Path(os.path.realpath(package_root)).relative_to(distribution_root)
        except ValueError:
            raise SystemExit(
                "hindsight-api package is outside its distribution root"
            )
        if package_root.is_file():
            paths.add(package_root)
        elif package_root.is_dir():
            paths.update(
                path.absolute()
                for path in package_root.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"}
            )
files = []
for path in sorted(paths):
    try:
        Path(os.path.realpath(path)).relative_to(distribution_root)
        relative = path.relative_to(source_root)
    except ValueError:
        raise SystemExit("hindsight-api runtime file is outside its distribution root")
    files.append({"path": str(path), "relative": str(relative)})
print(json.dumps({"root": str(distribution_root), "files": files}))
"""
ADMIN_SNAPSHOT_MAX_BYTES = 4096
RUNTIME_FILE_MAX_BYTES = 128 * 1024 * 1024
RUNTIME_FILE_MAX_COUNT = 16 * 1024
RUNTIME_TOTAL_MAX_BYTES = 512 * 1024 * 1024
ADMIN_OUTPUT_MAX_CHARS = 8 * 1024 * 1024
ADVISORY_LOCK_TIMEOUT_SECONDS = 5.0
ADVISORY_LOCK_POLL_SECONDS = 0.01
MAX_VERIFIED_ROLLBACK_IDENTITIES = 1024


class MigrationAdapterError(RuntimeError):
    pass


def _copy_exact_descriptor(
    source: int,
    destination: int | None,
    expected_size: int,
    label: str,
) -> str:
    """Copy the attested byte count and reject truncation or one-byte growth."""
    checksum = hashlib.sha256()
    remaining_bytes = expected_size
    while remaining_bytes:
        chunk = os.read(source, min(1024 * 1024, remaining_bytes))
        if not chunk:
            raise FileEvidenceError(f"{label} size changed")
        checksum.update(chunk)
        remaining_bytes -= len(chunk)
        if destination is not None:
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(destination, remaining)
                if written <= 0:
                    raise OSError("runtime snapshot write failed")
                remaining = remaining[written:]
    if os.read(source, 1):
        raise FileEvidenceError(f"{label} size changed")
    return checksum.hexdigest()


def _acquire_advisory_lock(descriptor: int, unavailable_message: str) -> None:
    """Acquire an exclusive advisory lock without an unbounded kernel wait."""
    deadline = time.monotonic() + ADVISORY_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MigrationAdapterError(unavailable_message)
        time.sleep(min(ADVISORY_LOCK_POLL_SECONDS, remaining))


def _runtime_file_snapshot(
    value: str | Path, label: str, *, allow_hardlinks: bool = False
) -> tuple[str, tuple[int, ...], str, tuple[int, ...], str]:
    def validate(metadata: os.stat_result) -> None:
        if not allow_hardlinks:
            validate_trusted_regular_file(metadata, label)
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise FileEvidenceError(f"{label} must be a regular file")
        if metadata.st_uid not in {0, os.geteuid()}:
            raise FileEvidenceError(
                f"{label} must be owned by the current user or root"
            )
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise FileEvidenceError(
                f"{label} must not be group or world writable"
            )

    requested = Path(value)
    if not requested.is_absolute():
        raise FileEvidenceError(f"{label} path must be absolute")
    reject_symlink_components(requested.parent, label, allow_missing=False)
    requested_metadata = requested.lstat()
    if stat.S_ISLNK(requested_metadata.st_mode):
        if requested_metadata.st_uid not in {0, os.geteuid()}:
            raise FileEvidenceError(
                f"{label} symlink must be owned by the current user or root"
            )
        if requested_metadata.st_nlink != 1:
            raise FileEvidenceError(f"{label} symlink must not have hard links")
    path = requested.resolve(strict=True)
    reject_symlink_components(path, label, allow_missing=False)
    validate(path.lstat())
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        validate(before)
        if before.st_size > RUNTIME_FILE_MAX_BYTES:
            raise FileEvidenceError(f"{label} is too large")
        payload = hashlib.sha256()
        offset = 0
        while chunk := os.pread(descriptor, 65536, offset):
            payload.update(chunk)
            offset += len(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = path.lstat()
    current_requested = requested.lstat()
    if (
        file_identity(requested_metadata) != file_identity(current_requested)
        or file_identity(before) != file_identity(after)
        or file_identity(after) != file_identity(current)
    ):
        raise FileEvidenceError(f"{label} identity changed")
    return (
        str(requested),
        file_identity(requested_metadata),
        str(path),
        file_identity(after),
        payload.hexdigest(),
    )


def _trusted_interpreter(
    value: str | Path,
) -> tuple[str, tuple[int, ...], str, tuple[int, ...], str]:
    path = Path(value)
    if not path.is_absolute():
        raise FileEvidenceError("hindsight-admin interpreter is invalid")
    return _runtime_file_snapshot(
        path, "hindsight-admin interpreter", allow_hardlinks=True
    )


def _interpreter_prefix_bindings(
    output: str,
) -> tuple[
    tuple[str, ...],
    tuple[tuple[int, str, tuple[str, tuple[int, ...], str, tuple[int, ...], str]], ...],
    tuple[str, ...],
]:
    """Validate every importable stdlib/extension file in the probed prefix."""

    try:
        lines = output.splitlines()
        if len(lines) < 5 or len(lines) > RUNTIME_FILE_MAX_COUNT:
            raise ValueError
        values = tuple(bytes.fromhex(line).decode("utf-8") for line in lines)
        if any("\x00" in value for value in values):
            raise ValueError
        prefix, exec_prefix, base_prefix, base_exec_prefix = (
            Path(value) for value in values[:4]
        )
        if not all(
            path.is_absolute()
            for path in (prefix, exec_prefix, base_prefix, base_exec_prefix)
        ):
            raise ValueError
        homes = tuple(
            dict.fromkeys(
                str(path.resolve(strict=True))
                for path in (base_prefix, base_exec_prefix)
            )
        )
        home_paths = tuple(Path(path) for path in homes)
        files: dict[tuple[int, str], tuple[str, tuple[int, ...], str, tuple[int, ...], str]] = {}
        for raw_path in values[4:]:
            if not raw_path:
                continue
            candidate = Path(raw_path)
            if not candidate.is_absolute() or not candidate.exists():
                continue
            resolved = candidate.resolve(strict=True)
            destinations = []
            for index, home in enumerate(home_paths):
                try:
                    destinations.append((index, resolved.relative_to(home)))
                except ValueError:
                    continue
            if not destinations:
                raise ValueError
            index, relative_root = max(
                destinations, key=lambda item: len(home_paths[item[0]].parts)
            )
            candidates = (
                (candidate,) if candidate.is_file()
                else tuple(path for path in candidate.rglob("*") if path.is_file())
            )
            for path in candidates:
                relative = relative_root / path.relative_to(candidate)
                key = (index, str(relative))
                files[key] = _runtime_file_snapshot(
                    path, "hindsight-admin interpreter prefix file",
                    allow_hardlinks=True,
                )
        if not files:
            raise ValueError
        bindings = tuple(
            (index, relative, binding)
            for (index, relative), binding in sorted(files.items())
        )
        total = sum(binding[3][6] for _index, _relative, binding in bindings)
        if (
            len(bindings) > RUNTIME_FILE_MAX_COUNT
            or total > RUNTIME_TOTAL_MAX_BYTES
        ):
            raise ValueError
        package_roots = tuple(
            str(path)
            for root in dict.fromkeys((prefix, exec_prefix))
            for python_root in sorted((root / "lib").glob("python*"))
            for path in (
                python_root / "site-packages",
                python_root / "dist-packages",
            )
            if path.is_dir()
        )
        return homes, bindings, package_roots
    except (FileEvidenceError, OSError, RuntimeError, UnicodeError, ValueError):
        raise MigrationAdapterError(
            "hindsight-admin interpreter prefix is unavailable"
        ) from None


def _trusted_admin_executable(value: str | Path) -> tuple[str, tuple[int, ...], str]:
    path = Path(value)
    if not path.is_absolute():
        raise MigrationAdapterError("hindsight-admin executable path must be absolute")
    try:
        reject_symlink_components(path, "hindsight-admin executable", allow_missing=False)
        validate_trusted_regular_file(path.lstat(), "hindsight-admin executable")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            validate_trusted_regular_file(before, "hindsight-admin executable")
            if not before.st_mode & 0o111:
                raise FileEvidenceError("hindsight-admin executable must be executable")
            first_line = os.read(descriptor, 4096).splitlines()[0]
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = path.lstat()
    except (FileEvidenceError, OSError, IndexError) as error:
        message = str(error) if isinstance(error, FileEvidenceError) else "hindsight-admin executable is unavailable"
        raise MigrationAdapterError(message) from None
    identity = file_identity(before)
    if identity != file_identity(after) or identity != file_identity(current):
        raise MigrationAdapterError("hindsight-admin executable identity changed")
    if not first_line.startswith(b"#!"):
        raise MigrationAdapterError("hindsight-admin executable interpreter is invalid")
    try:
        interpreter = first_line[2:].decode("utf-8")
    except UnicodeDecodeError:
        raise MigrationAdapterError("hindsight-admin executable interpreter is invalid") from None
    if not interpreter or any(character.isspace() for character in interpreter) or not Path(interpreter).is_absolute():
        raise MigrationAdapterError("hindsight-admin executable interpreter is invalid")
    return str(path), identity, interpreter


def hindsight_admin_argv(
    executable: str, operation: str, archive: str, bank_id: str | None
) -> list[str]:
    if operation in {"export-bank", "import-bank"} and (
        not isinstance(bank_id, str) or BANK_ID.fullmatch(bank_id) is None
    ):
        raise MigrationAdapterError("bank ID is required")
    if operation in {"backup", "restore"} and bank_id is not None:
        raise MigrationAdapterError("bank ID is not permitted for schema operation")
    if operation == "export-bank":
        return [
            executable, operation, "--bank", bank_id,
            "--output", archive,
        ]
    if operation == "import-bank":
        return [
            executable, operation, "--archive", archive,
            "--target-bank", bank_id,
        ]
    if operation == "backup":
        return [executable, operation, archive, "--schema", "public"]
    if operation == "restore":
        return [
            executable, operation, archive, "--schema", "public", "--yes",
        ]
    raise MigrationAdapterError("unsupported hindsight-admin operation")


class AdminMigrationAdapter:
    WORKING_DIRECTORY = "/"

    def __init__(self, *, admin_executable: str,
                 argv_factory: Callable[[str, str, str, str | None], Sequence[str]],
                 runner: Callable[..., Any], environment: Mapping[str, str] | None = None,
                 supported_versions: frozenset[str] = frozenset({"0.8.4"})) -> None:
        executable, identity, interpreter = _trusted_admin_executable(admin_executable)
        if not callable(argv_factory) or not callable(runner):
            raise MigrationAdapterError("hindsight-admin process seams are required")
        source_environment = dict(environment or {})
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in source_environment.items()):
            raise MigrationAdapterError("hindsight-admin environment is invalid")
        self.admin_executable = executable
        self._executable_identity = identity
        self._interpreter_source = interpreter
        try:
            self._interpreter_binding = _trusted_interpreter(interpreter)
        except (FileEvidenceError, OSError, RuntimeError):
            raise MigrationAdapterError(
                "hindsight-admin interpreter is unavailable"
            ) from None
        self._interpreter = self._interpreter_binding[2]
        self._runtime_files: tuple[
            tuple[
                str,
                tuple[str, tuple[int, ...], str, tuple[int, ...], str],
            ],
            ...,
        ] = ()
        self._environment = {
            key: source_environment[key]
            for key in sorted(ADMIN_ENVIRONMENT_ALLOWLIST & source_environment.keys())
        }
        self.argv_factory = argv_factory
        self.runner = runner
        self.calls: list[dict[str, Any]] = []
        prefix_probe = self._invoke(
            [
                self._interpreter, "-I", "-S", "-c",
                INTERPRETER_PREFIX_PROBE,
            ],
            timeout=30,
        )
        prefix_output = self._result_field(prefix_probe, "stdout")
        if (
            self._result_field(prefix_probe, "returncode") != 0
            or not isinstance(prefix_output, str)
        ):
            raise MigrationAdapterError(
                "hindsight-admin interpreter prefix probe failed"
            )
        (
            self._interpreter_homes,
            self._interpreter_prefix_files,
            self._distribution_roots,
        ) = _interpreter_prefix_bindings(prefix_output)
        runtime_probe_source = RUNTIME_FILES_PROBE.replace(
            "PACKAGE_ROOTS = None",
            f"PACKAGE_ROOTS = {self._distribution_roots!r}",
            1,
        )
        with self._runtime_execution_snapshot() as (
            runtime_root,
            execution_target,
            python_home,
        ):
            runtime_probe = self._invoke(
                [
                    self._interpreter, "-I", "-S", "-c",
                    runtime_probe_source,
                ],
                timeout=30,
                runtime_root=runtime_root,
                execution_target=execution_target,
                python_home=python_home,
            )
        runtime_output = self._result_field(runtime_probe, "stdout")
        if (
            self._result_field(runtime_probe, "returncode") != 0
            or not isinstance(runtime_output, str)
        ):
            raise MigrationAdapterError("hindsight-admin runtime probe failed")
        try:
            manifest = json.loads(runtime_output)
            if not isinstance(manifest, dict) or set(manifest) != {"root", "files"}:
                raise ValueError("invalid runtime manifest")
            root = Path(manifest["root"])
            if not root.is_absolute():
                raise ValueError("invalid runtime manifest")
            files = manifest["files"]
            if (
                not isinstance(files, list)
                or not files
                or len(files) > RUNTIME_FILE_MAX_COUNT
                or not all(
                    isinstance(item, dict)
                    and set(item) == {"path", "relative"}
                    and isinstance(item["path"], str)
                    and isinstance(item["relative"], str)
                    for item in files
                )
            ):
                raise ValueError("invalid runtime manifest")
            relative_paths = [item["relative"] for item in files]
            if len(relative_paths) != len(set(relative_paths)):
                raise ValueError("invalid runtime manifest")
            for item in files:
                relative = Path(item["relative"])
                path = Path(item["path"])
                if (
                    relative.is_absolute()
                    or not relative.parts
                    or ".." in relative.parts
                    or path != root / relative
                ):
                    raise ValueError("invalid runtime manifest")
            runtime_files = tuple(
                sorted(
                    (
                        item["relative"],
                        _runtime_file_snapshot(
                            item["path"], "hindsight-api runtime file"
                        ),
                    )
                    for item in files
                )
            )
            # Account from the post-read descriptor identities returned by
            # _runtime_file_snapshot, not the path metadata observed before
            # verification.
            if sum(binding[3][6] for _relative, binding in runtime_files) > RUNTIME_TOTAL_MAX_BYTES:
                raise ValueError("invalid runtime manifest")
            self._runtime_files = runtime_files
        except (FileEvidenceError, OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError):
            raise MigrationAdapterError(
                "hindsight-admin runtime probe failed"
            ) from None
        self._require_executable_identity()
        try:
            with self._runtime_execution_snapshot() as (
                runtime_root,
                execution_target,
                python_home,
            ):
                probe = self._invoke(
                    [
                        self._interpreter,
                        "-I",
                        "-S",
                        "-c",
                        (
                            "import sys; "
                            f"sys.path.insert(0, {runtime_root!r}); "
                            f"{VERSION_PROBE}"
                        ),
                    ],
                    timeout=30,
                    runtime_root=runtime_root,
                    execution_target=execution_target,
                    python_home=python_home,
                )
        except MigrationAdapterError:
            raise
        version = self._result_field(probe, "stdout")
        if self._result_field(probe, "returncode") != 0 or not isinstance(version, str):
            raise MigrationAdapterError("hindsight-admin version probe failed")
        self.admin_version = version.strip()
        if self.admin_version not in supported_versions:
            raise MigrationAdapterError("unsupported hindsight-admin version")
        self._require_executable_identity()

    @staticmethod
    def _result_field(result: Any, field: str) -> Any:
        return result.get(field) if isinstance(result, Mapping) else getattr(result, field, None)

    def _invoke(
        self,
        argv: Sequence[str],
        *,
        timeout: int,
        execution_descriptor: int | None = None,
        inherited_descriptors: tuple[int, ...] = (),
        runtime_root: str | None = None,
        execution_target: str | None = None,
        python_home: str | None = None,
    ) -> Any:
        invocation = list(argv)
        execution_options: dict[str, Any] = {}
        pass_fds = list(inherited_descriptors)
        if execution_descriptor is not None:
            invocation = [
                self._interpreter,
                "-S",
                f"/dev/fd/{execution_descriptor}",
                *argv[1:],
            ]
            pass_fds.insert(0, execution_descriptor)
        if pass_fds:
            execution_options = {
                "pass_fds": tuple(dict.fromkeys(pass_fds)),
            }
        if execution_target is not None:
            execution_options["executable"] = execution_target
        captures = (bytearray(), bytearray())
        pipes = (os.pipe(), os.pipe())

        def drain(descriptor: int, captured: bytearray) -> None:
            try:
                while chunk := os.read(descriptor, 65536):
                    remaining = ADMIN_OUTPUT_MAX_CHARS + 1 - len(captured)
                    if remaining > 0:
                        captured.extend(chunk[:remaining])
            finally:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

        readers = tuple(
            threading.Thread(
                target=drain,
                args=(pipe[0], captured),
                name="hindsight-admin-output-capture",
                daemon=True,
            )
            for pipe, captured in zip(pipes, captures)
        )
        try:
            for reader in readers:
                reader.start()
            environment = dict(self._environment)
            if runtime_root is not None:
                environment["PYTHONPATH"] = runtime_root
                environment["PYTHONNOUSERSITE"] = "1"
            if python_home is not None:
                environment["PYTHONHOME"] = python_home
            runner_options = {
                "cwd": self.WORKING_DIRECTORY,
                "env": environment,
                "text": True,
                "stdout": pipes[0][1],
                "stderr": pipes[1][1],
                **execution_options,
            }
            if self.runner is _SUBPROCESS_RUN:
                process = subprocess.Popen(
                    invocation,
                    start_new_session=True,
                    **runner_options,
                )
                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._terminate_process_group(process)
                    raise
                result = subprocess.CompletedProcess(
                    invocation, returncode, None, None
                )
            else:
                result = self.runner(
                    invocation,
                    timeout=timeout,
                    check=False,
                    start_new_session=True,
                    **runner_options,
                )
        except MigrationAdapterError:
            raise
        except subprocess.TimeoutExpired:
            raise MigrationAdapterError("hindsight-admin operation timed out") from None
        except Exception:
            raise MigrationAdapterError("hindsight-admin operation failed") from None
        finally:
            for _read_descriptor, write_descriptor in pipes:
                try:
                    os.close(write_descriptor)
                except OSError:
                    pass
            for index, reader in enumerate(readers):
                if reader.ident is not None:
                    reader.join(timeout=5)
                    if reader.is_alive():
                        try:
                            os.close(pipes[index][0])
                        except OSError:
                            pass
                        reader.join(timeout=1)
                else:
                    os.close(pipes[index][0])
        try:
            normalized: dict[str, Any] | None = (
                dict(result) if isinstance(result, Mapping) else None
            )
            for index, field in enumerate(("stdout", "stderr")):
                output = self._result_field(result, field)
                if output is None:
                    output = bytes(captures[index]).decode("utf-8")
                if output is not None and (
                    not isinstance(output, (str, bytes))
                    or len(output) > ADMIN_OUTPUT_MAX_CHARS
                ):
                    raise MigrationAdapterError(
                        "hindsight-admin output exceeded the capture limit"
                    )
                if normalized is not None:
                    normalized[field] = output
                else:
                    setattr(result, field, output)
            return normalized if normalized is not None else result
        except MigrationAdapterError:
            raise
        except Exception:
            raise MigrationAdapterError("hindsight-admin operation failed") from None

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen) -> None:
        def group_exists() -> bool:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return False
            return True

        if group_exists():
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 1.0
        while group_exists() and time.monotonic() < deadline:
            process.poll()
            time.sleep(0.05)
        if group_exists():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                raise MigrationAdapterError(
                    "hindsight-admin process did not terminate"
                ) from None

    def _require_executable_identity(self) -> None:
        try:
            executable, identity, interpreter = _trusted_admin_executable(self.admin_executable)
            interpreter_binding = _trusted_interpreter(interpreter)
            runtime_files = tuple(
                sorted(
                    (
                        relative,
                        _runtime_file_snapshot(
                            binding[0], "hindsight-api runtime file"
                        ),
                    )
                    for relative, binding
                    in self._runtime_files
                )
            )
            interpreter_prefix_files = tuple(
                (
                    index,
                    relative,
                    _runtime_file_snapshot(
                        binding[0],
                        "hindsight-admin interpreter prefix file",
                        allow_hardlinks=True,
                    ),
                )
                for index, relative, binding
                in self._interpreter_prefix_files
            )
        except (FileEvidenceError, OSError, RuntimeError):
            raise MigrationAdapterError(
                "hindsight-admin runtime identity changed"
            ) from None
        if (
            executable != self.admin_executable
            or identity != self._executable_identity
            or interpreter != self._interpreter_source
            or interpreter_binding != self._interpreter_binding
            or interpreter_prefix_files != self._interpreter_prefix_files
            or runtime_files != self._runtime_files
        ):
            raise MigrationAdapterError(
                "hindsight-admin runtime identity changed"
            )

    @staticmethod
    def _copy_bound_file(
        binding: tuple[str, tuple[int, ...], str, tuple[int, ...], str],
        destination: Path,
        label: str,
    ) -> None:
        source_descriptor = os.open(
            binding[2],
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        destination_descriptor: int | None = None
        try:
            before = os.fstat(source_descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(before.st_mode) & 0o022
                or file_identity(before) != binding[3]
                or before.st_size > RUNTIME_FILE_MAX_BYTES
            ):
                raise FileEvidenceError(f"{label} identity changed")
            destination_descriptor = os.open(
                destination,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o400,
            )
            copied_digest = _copy_exact_descriptor(
                source_descriptor,
                destination_descriptor,
                binding[3][6],
                label,
            )
            os.fchmod(destination_descriptor, 0o400)
            os.fsync(destination_descriptor)
            after = os.fstat(source_descriptor)
            current = Path(binding[2]).lstat()
            if (
                file_identity(before) != file_identity(after)
                or file_identity(after) != file_identity(current)
                or not hmac.compare_digest(copied_digest, binding[4])
            ):
                raise FileEvidenceError(f"{label} identity changed")
        finally:
            os.close(source_descriptor)
            if destination_descriptor is not None:
                os.close(destination_descriptor)

    @contextmanager
    def _runtime_execution_snapshot(self):
        """Copy the verified interpreter prefix and distribution privately."""
        self._require_executable_identity()
        try:
            with tempfile.TemporaryDirectory(
                prefix=".hindsight-admin-runtime-"
            ) as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                home_destinations = tuple(
                    root / f".python-home-{index}"
                    for index, _home in enumerate(self._interpreter_homes)
                )
                for destination in home_destinations:
                    destination.mkdir(mode=0o700)
                for index, relative, binding in self._interpreter_prefix_files:
                    destination = home_destinations[index] / relative
                    destination.parent.mkdir(
                        parents=True, exist_ok=True, mode=0o700
                    )
                    current_parent = destination.parent
                    while current_parent != root:
                        current_parent.chmod(0o700)
                        current_parent = current_parent.parent
                    self._copy_bound_file(
                        binding,
                        destination,
                        "hindsight-admin interpreter prefix file",
                    )
                python_home = os.pathsep.join(
                    str(path) for path in home_destinations
                )
                interpreter_destination = root / ".hindsight-python"
                interpreter_descriptor = os.open(
                    self._interpreter_binding[2],
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                )
                destination_descriptor = None
                execution_target = str(interpreter_destination)
                try:
                    before = os.fstat(interpreter_descriptor)
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or before.st_uid not in {0, os.geteuid()}
                        or stat.S_IMODE(before.st_mode) & 0o022
                        or file_identity(before) != self._interpreter_binding[3]
                    ):
                        raise FileEvidenceError(
                            "hindsight-admin interpreter identity changed"
                        )
                    if before.st_uid == 0 and os.geteuid() != 0:
                        # A root-owned, non-writable interpreter is already an
                        # immutable execution target for this unprivileged
                        # process. macOS platform binaries also cannot be
                        # relocated without invalidating their platform seal.
                        execution_target = self._interpreter_binding[2]
                        copied_interpreter_digest = _copy_exact_descriptor(
                            interpreter_descriptor,
                            None,
                            self._interpreter_binding[3][6],
                            "hindsight-admin interpreter",
                        )
                    else:
                        destination_descriptor = os.open(
                            interpreter_destination,
                            os.O_WRONLY
                            | os.O_CREAT
                            | os.O_EXCL
                            | getattr(os, "O_CLOEXEC", 0)
                            | getattr(os, "O_NOFOLLOW", 0),
                            0o500,
                        )
                        copied_interpreter_digest = _copy_exact_descriptor(
                            interpreter_descriptor,
                            destination_descriptor,
                            self._interpreter_binding[3][6],
                            "hindsight-admin interpreter",
                        )
                        os.fchmod(destination_descriptor, 0o500)
                        os.fsync(destination_descriptor)
                    after = os.fstat(interpreter_descriptor)
                    current = Path(self._interpreter_binding[2]).lstat()
                    if (
                        file_identity(before) != file_identity(after)
                        or file_identity(after) != file_identity(current)
                        or not hmac.compare_digest(
                            copied_interpreter_digest,
                            self._interpreter_binding[4],
                        )
                    ):
                        raise FileEvidenceError(
                            "hindsight-admin interpreter identity changed"
                        )
                finally:
                    os.close(interpreter_descriptor)
                    if destination_descriptor is not None:
                        os.close(destination_descriptor)
                for relative, binding in self._runtime_files:
                    destination = root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    current = destination.parent
                    while current != root:
                        current.chmod(0o700)
                        current = current.parent
                    with verified_file_snapshot(
                        binding[2],
                        "hindsight-api runtime file",
                        binding[4],
                        max_bytes=RUNTIME_FILE_MAX_BYTES,
                    ) as verified:
                        source_descriptor = os.dup(verified.fileno())
                        destination_descriptor = os.open(
                            destination,
                            os.O_WRONLY
                            | os.O_CREAT
                            | os.O_EXCL
                            | getattr(os, "O_CLOEXEC", 0)
                            | getattr(os, "O_NOFOLLOW", 0),
                            0o400,
                        )
                        try:
                            copied_digest = _copy_exact_descriptor(
                                source_descriptor,
                                destination_descriptor,
                                binding[3][6],
                                "hindsight-api runtime file",
                            )
                            os.fchmod(destination_descriptor, 0o400)
                            os.fsync(destination_descriptor)
                        finally:
                            os.close(source_descriptor)
                            os.close(destination_descriptor)
                    if not hmac.compare_digest(copied_digest, binding[4]):
                        raise MigrationAdapterError(
                            "hindsight-admin runtime snapshot changed"
                        )
                directory_descriptor = os.open(
                    root,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
                self._require_executable_identity()
                yield str(root), execution_target, python_home
        except MigrationAdapterError:
            raise
        except (FileEvidenceError, OSError):
            raise MigrationAdapterError(
                "hindsight-admin runtime snapshot changed"
            ) from None

    @contextmanager
    def _execution_binding(self):
        """Execute an immutable anonymous byte stream of the validated executable."""
        self._require_executable_identity()
        source_descriptor: int | None = None
        snapshot_descriptor: int | None = None
        snapshot_writer: int | None = None
        try:
            reject_symlink_components(
                Path(self.admin_executable),
                "hindsight-admin executable",
                allow_missing=False,
            )
            validate_trusted_regular_file(
                Path(self.admin_executable).lstat(),
                "hindsight-admin executable",
            )
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            source_descriptor = os.open(self.admin_executable, flags)
            metadata = os.fstat(source_descriptor)
            validate_trusted_regular_file(metadata, "hindsight-admin executable")
            if not metadata.st_mode & 0o111:
                raise FileEvidenceError(
                    "hindsight-admin executable must be executable"
                )
            if file_identity(metadata) != self._executable_identity:
                raise MigrationAdapterError(
                    "hindsight-admin executable identity changed"
                )
            current = Path(self.admin_executable).lstat()
            if (current.st_dev, current.st_ino) != (
                metadata.st_dev,
                metadata.st_ino,
            ):
                raise MigrationAdapterError(
                    "hindsight-admin executable identity changed"
                )
            source_identity = file_identity(metadata)
            source_digest = self._descriptor_digest(source_descriptor)
            snapshot = self._descriptor_bytes(source_descriptor)
            snapshot_digest = hashlib.sha256(snapshot).digest()
            if (
                len(snapshot) > ADMIN_SNAPSHOT_MAX_BYTES
                or file_identity(os.fstat(source_descriptor)) != source_identity
                or self._descriptor_digest(source_descriptor) != source_digest
                or not hmac.compare_digest(snapshot_digest, source_digest)
            ):
                raise MigrationAdapterError(
                    "hindsight-admin executable snapshot changed"
                )
            snapshot_descriptor, snapshot_writer = os.pipe()
            written = 0
            while written < len(snapshot):
                count = os.write(snapshot_writer, snapshot[written:])
                if count <= 0:
                    raise OSError("hindsight-admin snapshot write failed")
                written += count
            os.close(snapshot_writer)
            snapshot_writer = None
            with self._runtime_execution_snapshot() as (
                runtime_root,
                execution_target,
                python_home,
            ):
                self._require_executable_identity()
                yield (
                    snapshot_descriptor,
                    runtime_root,
                    execution_target,
                    python_home,
                )
        except MigrationAdapterError:
            raise
        except (FileEvidenceError, OSError):
            raise MigrationAdapterError(
                "hindsight-admin executable identity changed"
            ) from None
        finally:
            if snapshot_writer is not None:
                os.close(snapshot_writer)
            if snapshot_descriptor is not None:
                os.close(snapshot_descriptor)
            if source_descriptor is not None:
                os.close(source_descriptor)

    @staticmethod
    def _descriptor_digest(descriptor: int) -> bytes:
        payload = hashlib.sha256()
        offset = 0
        while chunk := os.pread(descriptor, 65536, offset):
            payload.update(chunk)
            offset += len(chunk)
        return payload.digest()

    @staticmethod
    def _descriptor_bytes(descriptor: int) -> bytes:
        payload = bytearray()
        offset = 0
        while chunk := os.pread(descriptor, 65536, offset):
            payload.extend(chunk)
            offset += len(chunk)
            if len(payload) > ADMIN_SNAPSHOT_MAX_BYTES:
                break
        return bytes(payload)

    @staticmethod
    def _archive_recovery_name(
        archive_path: Path, archive_digest: str
    ) -> str:
        identity = hashlib.sha256(
            canonical_bytes(
                {"name": archive_path.name, "digest": archive_digest}
            )
        ).hexdigest()
        return f".hindsight-archive-{identity}.recovery"

    @staticmethod
    def _acquire_output_archive_lock(
        directory: int, archive_path: Path
    ) -> int:
        identity = hashlib.sha256(
            canonical_bytes({"name": archive_path.name})
        ).hexdigest()
        name = f".hindsight-output-{identity}.lock"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                name,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise MigrationAdapterError(
                    "hindsight-admin archive publication lock is unsafe"
                )
            _acquire_advisory_lock(
                descriptor,
                "hindsight-admin archive publication lock is unavailable",
            )
            return descriptor
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise

    @staticmethod
    def _release_output_archive_lock(descriptor: int | None) -> None:
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @staticmethod
    def _assert_archive_parent_identity(
        archive_parent: Path,
        expected: tuple[int, int],
    ) -> None:
        descriptor: int | None = None
        try:
            descriptor = open_trusted_parent(
                archive_parent,
                unavailable_message="hindsight-admin archive parent changed",
                not_directory_message="hindsight-admin archive parent changed",
                owner_message="hindsight-admin archive parent is untrusted",
                writable_message="hindsight-admin archive parent is untrusted",
                create_missing=False,
            )
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) != expected:
                raise MigrationAdapterError(
                    "hindsight-admin archive parent changed"
                )
        except MigrationAdapterError:
            raise
        except OSError:
            raise MigrationAdapterError(
                "hindsight-admin archive parent changed"
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _unlink_owned_archive_name(
        directory: int,
        name: str,
        identity: tuple[int, int],
    ) -> None:
        try:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (current.st_dev, current.st_ino) != identity:
            raise MigrationAdapterError(
                "hindsight-admin archive publication identity changed"
            )
        os.unlink(name, dir_fd=directory)

    def _recover_output_archive(
        self,
        directory_descriptor: int,
        archive_path: Path,
        archive_digest: str,
    ) -> bool:
        """Finish or clean a previously fsynced no-replace publication."""
        recovery_name = self._archive_recovery_name(
            archive_path, archive_digest
        )
        directory = os.dup(directory_descriptor)
        descriptor: int | None = None
        try:
            try:
                descriptor = os.open(
                    recovery_name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory,
                )
            except FileNotFoundError:
                return False
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) & 0o222
            ):
                raise MigrationAdapterError(
                    "hindsight-admin recovery archive is unsafe"
                )
            actual_digest = self._descriptor_digest(descriptor).hex()
            after = os.fstat(descriptor)
            if file_identity(before) != file_identity(after):
                raise MigrationAdapterError(
                    "hindsight-admin recovery archive is invalid"
                )
            if not hmac.compare_digest(actual_digest, archive_digest):
                # A crash can leave the explicitly owned recovery name before
                # its full verified payload is durable. It has never been
                # linked to the destination, so discard it and regenerate.
                if after.st_nlink != 1:
                    raise MigrationAdapterError(
                        "hindsight-admin recovery archive is invalid"
                    )
                self._unlink_owned_archive_name(
                    directory,
                    recovery_name,
                    (after.st_dev, after.st_ino),
                )
                os.fsync(directory)
                return False
            try:
                target = os.stat(
                    archive_path.name,
                    dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                target = None
            if target is None:
                if after.st_nlink != 1:
                    raise MigrationAdapterError(
                        "hindsight-admin recovery archive is unsafe"
                    )
                os.link(
                    recovery_name,
                    archive_path.name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                os.fsync(directory)
                linked = os.fstat(descriptor)
                if (
                    file_identity(after)[:-4] != file_identity(linked)[:-4]
                    or linked.st_nlink != 2
                ):
                    raise MigrationAdapterError(
                        "hindsight-admin recovery archive is invalid"
                    )
                after = linked
            else:
                if (
                    not stat.S_ISREG(target.st_mode)
                    or (target.st_dev, target.st_ino)
                    != (after.st_dev, after.st_ino)
                    or after.st_nlink != 2
                ):
                    raise MigrationAdapterError(
                        "hindsight-admin archive destination already exists"
                    )
            self._unlink_owned_archive_name(
                directory,
                recovery_name,
                (after.st_dev, after.st_ino),
            )
            os.fsync(directory)
            return True
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory)

    @staticmethod
    def _restore_evidence(
        evidence: Mapping[str, Any] | None,
        archive_digest: str,
        expected_evidence_digest: str,
    ) -> None:
        if not isinstance(evidence, Mapping) or set(evidence) != {
            "schema_version", "artifact_digest", "verification_receipt_digest",
        }:
            raise MigrationAdapterError("disposable restore evidence is required")
        if type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1:
            raise MigrationAdapterError("disposable restore evidence is not verified")
        if evidence["artifact_digest"] != archive_digest:
            raise MigrationAdapterError("disposable restore evidence digest does not match archive")
        receipt_digest = evidence["verification_receipt_digest"]
        if not isinstance(receipt_digest, str) or DIGEST.fullmatch(receipt_digest) is None:
            raise MigrationAdapterError("disposable restore evidence receipt is invalid")
        if (
            not isinstance(expected_evidence_digest, str)
            or DIGEST.fullmatch(expected_evidence_digest) is None
            or not hmac.compare_digest(digest(dict(evidence)), expected_evidence_digest)
        ):
            raise MigrationAdapterError("disposable restore evidence digest does not match plan")

    def _run(self, operation: str, archive: str, archive_digest: str,
             bank_id: str | None = None,
             expected_evidence_digest: str | None = None,
             evidence: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        if operation not in OPERATIONS:
            raise MigrationAdapterError("unsupported hindsight-admin operation")
        verified_input = (
            archive if isinstance(archive, VerifiedFileSnapshot) else None
        )
        archive_value = str(verified_input) if verified_input is not None else archive
        if not isinstance(archive_value, (str, Path)) or not Path(archive_value).is_absolute():
            raise MigrationAdapterError("archive path must be absolute")
        if not isinstance(archive_digest, str) or not DIGEST.fullmatch(archive_digest):
            raise MigrationAdapterError("archive digest is required")
        if operation in {"export-bank", "import-bank"}:
            if not isinstance(bank_id, str) or BANK_ID.fullmatch(bank_id) is None:
                raise MigrationAdapterError("bank ID is required")
        elif bank_id is not None:
            raise MigrationAdapterError("bank ID is not permitted for schema operation")
        if operation in {"import-bank", "restore"}:
            self._restore_evidence(
                evidence, archive_digest, str(expected_evidence_digest)
            )
        self.calls.append({
            "operation": operation,
            "archive_digest": archive_digest,
            **({"bank_id": bank_id} if bank_id is not None else {}),
        })
        output_operation = operation in {"export-bank", "backup"}
        archive_path = Path(archive_value)
        parent_descriptor: int | None = None
        parent_identity: tuple[int, int] | None = None
        output_lock_descriptor: int | None = None
        try:
            reject_symlink_components(
                archive_path.parent,
                "hindsight-admin archive parent",
                allow_missing=False,
            )
            archive_parent = archive_path.parent.resolve(strict=True)
            parent_descriptor = open_trusted_parent(
                archive_parent,
                unavailable_message="hindsight-admin archive parent is unavailable",
                not_directory_message="hindsight-admin archive parent is unavailable",
                owner_message="hindsight-admin archive parent is untrusted",
                writable_message="hindsight-admin archive parent is untrusted",
                create_missing=False,
            )
            parent_metadata = os.fstat(parent_descriptor)
            parent_identity = (parent_metadata.st_dev, parent_metadata.st_ino)
            parent_mode = stat.S_IMODE(parent_metadata.st_mode)
            sticky_shared_parent = bool(
                parent_mode & stat.S_ISVTX
                and parent_metadata.st_uid in {0, os.geteuid()}
            )
            if (
                not stat.S_ISDIR(parent_metadata.st_mode)
                or parent_metadata.st_uid not in {0, os.geteuid()}
                or (parent_mode & 0o022 and not sticky_shared_parent)
            ):
                raise FileEvidenceError(
                    "hindsight-admin archive parent is untrusted"
                )
            if output_operation:
                output_lock_descriptor = self._acquire_output_archive_lock(
                    parent_descriptor, archive_path
                )
            if output_operation and self._recover_output_archive(
                parent_descriptor, archive_path, archive_digest
            ):
                self._release_output_archive_lock(output_lock_descriptor)
                output_lock_descriptor = None
                os.close(parent_descriptor)
                parent_descriptor = None
                return {"completed": True}
            if output_operation and (
                archive_path.exists() or archive_path.is_symlink()
            ):
                try:
                    with verified_file_snapshot(
                        archive_path,
                        "hindsight-admin existing output archive",
                        archive_digest,
                    ):
                        pass
                except FileEvidenceError:
                    pass
                else:
                    self._release_output_archive_lock(
                        output_lock_descriptor
                    )
                    output_lock_descriptor = None
                    os.close(parent_descriptor)
                    parent_descriptor = None
                    return {"completed": True}
            if archive_path.exists() or archive_path.is_symlink():
                reject_symlink_components(
                    archive_path,
                    "hindsight-admin archive",
                    allow_missing=False,
                )
                validate_trusted_regular_file(
                    archive_path.lstat(), "hindsight-admin archive"
                )
        except (FileEvidenceError, OSError) as error:
            self._release_output_archive_lock(output_lock_descriptor)
            output_lock_descriptor = None
            if parent_descriptor is not None:
                os.close(parent_descriptor)
            raise MigrationAdapterError(
                "hindsight-admin archive destination is untrusted"
            ) from error
        except BaseException:
            self._release_output_archive_lock(output_lock_descriptor)
            output_lock_descriptor = None
            if parent_descriptor is not None:
                os.close(parent_descriptor)
            raise

        def invoke(
            operation_archive: str,
            archive_descriptor: int | None = None,
        ) -> None:
            argv = self.argv_factory(
                self.admin_executable, operation, operation_archive, bank_id
            )
            if (
                isinstance(argv, (str, bytes))
                or not isinstance(argv, Sequence)
                or not all(isinstance(arg, str) for arg in argv)
            ):
                raise MigrationAdapterError(
                    "argv factory must return an argument vector, not a shell string"
                )
            expected = hindsight_admin_argv(
                self.admin_executable, operation, operation_archive, bank_id
            )
            if list(argv) != expected:
                raise MigrationAdapterError(
                    "hindsight-admin argv shape is not permitted"
                )
            with self._execution_binding() as (
                descriptor,
                runtime_root,
                execution_target,
                python_home,
            ):
                result = self._invoke(
                    list(argv),
                    timeout=300,
                    execution_descriptor=descriptor,
                    inherited_descriptors=(archive_descriptor,)
                    if archive_descriptor is not None
                    else (),
                    runtime_root=runtime_root,
                    execution_target=execution_target,
                    python_home=python_home,
                )
            if self._result_field(result, "returncode") != 0:
                raise MigrationAdapterError("hindsight-admin operation failed")

        if not output_operation:
            if parent_descriptor is not None:
                os.close(parent_descriptor)
                parent_descriptor = None

            def invoke_verified(verified_archive: VerifiedFileSnapshot) -> None:
                descriptor = verified_archive.fileno()
                try:
                    metadata = os.fstat(descriptor)
                    validate_trusted_regular_file(
                        metadata, "hindsight-admin input archive"
                    )
                    actual_digest = self._descriptor_digest(descriptor).hex()
                except (FileEvidenceError, OSError):
                    raise FileEvidenceError(
                        "hindsight-admin input archive descriptor changed"
                    ) from None
                if not hmac.compare_digest(actual_digest, archive_digest):
                    raise FileEvidenceError(
                        "hindsight-admin input archive digest changed"
                    )
                invoke(f"/dev/fd/{descriptor}", descriptor)

            try:
                if verified_input is not None:
                    invoke_verified(verified_input)
                else:
                    with verified_file_snapshot(
                        archive_path,
                        "hindsight-admin input archive",
                        archive_digest,
                    ) as verified_archive:
                        invoke_verified(verified_archive)
            except FileEvidenceError:
                raise MigrationAdapterError(
                    "hindsight-admin input archive verification failed"
                ) from None
            return {"completed": True}

        recovery_name: str | None = None
        published_identity: tuple[int, int] | None = None

        def cleanup_recovery() -> None:
            if (
                parent_descriptor is not None
                and recovery_name is not None
                and published_identity is not None
            ):
                self._unlink_owned_archive_name(
                    parent_descriptor, recovery_name, published_identity
                )
                os.fsync(parent_descriptor)

        try:
            with tempfile.TemporaryDirectory(
                prefix=".hindsight-admin-output-",
            ) as temporary:
                private_parent = Path(temporary)
                private_parent.chmod(0o700)
                temporary_archive = private_parent / (
                    "archive" + (archive_path.suffix or ".archive")
                )
                invoke(str(temporary_archive))
                with verified_file_snapshot(
                    temporary_archive,
                    "hindsight-admin output archive",
                    archive_digest,
                ) as verified_archive:
                    recovery_name = self._archive_recovery_name(
                        archive_path, archive_digest
                    )
                    assert parent_descriptor is not None
                    assert parent_identity is not None
                    self._assert_archive_parent_identity(
                        archive_parent, parent_identity
                    )
                    source_descriptor = os.dup(verified_archive.fileno())
                    publish_descriptor = os.open(
                        recovery_name,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o400,
                        dir_fd=parent_descriptor,
                    )
                    published_metadata = os.fstat(publish_descriptor)
                    published_identity = (
                        published_metadata.st_dev,
                        published_metadata.st_ino,
                    )
                    published_digest = hashlib.sha256()
                    try:
                        while chunk := os.read(source_descriptor, 1024 * 1024):
                            published_digest.update(chunk)
                            remaining = memoryview(chunk)
                            while remaining:
                                written = os.write(publish_descriptor, remaining)
                                if written <= 0:
                                    raise OSError("short verified archive write")
                                remaining = remaining[written:]
                        os.fsync(publish_descriptor)
                        os.fchmod(publish_descriptor, 0o400)
                    finally:
                        os.close(source_descriptor)
                        os.close(publish_descriptor)
                    validate_trusted_regular_file(
                        published_metadata, "verified hindsight-admin archive"
                    )
                    if not hmac.compare_digest(
                        published_digest.hexdigest(), archive_digest
                    ):
                        raise MigrationAdapterError(
                            "hindsight-admin output archive verification failed"
                        )
                    # The verified recovery file is durable before the
                    # no-replace destination link. A crash at either side
                    # of the link is completed by _recover_output_archive.
                    os.fsync(parent_descriptor)
                    self._assert_archive_parent_identity(
                        archive_parent, parent_identity
                    )
                    try:
                        os.link(
                            recovery_name,
                            archive_path.name,
                            src_dir_fd=parent_descriptor,
                            dst_dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        self._unlink_owned_archive_name(
                            parent_descriptor,
                            recovery_name,
                            published_identity,
                        )
                        os.fsync(parent_descriptor)
                        raise
                    try:
                        self._assert_archive_parent_identity(
                            archive_parent, parent_identity
                        )
                    except BaseException:
                        self._unlink_owned_archive_name(
                            parent_descriptor,
                            archive_path.name,
                            published_identity,
                        )
                        self._unlink_owned_archive_name(
                            parent_descriptor,
                            recovery_name,
                            published_identity,
                        )
                        os.fsync(parent_descriptor)
                        raise
                os.fsync(parent_descriptor)
                self._unlink_owned_archive_name(
                    parent_descriptor,
                    recovery_name,
                    published_identity,
                )
                os.fsync(parent_descriptor)
        except MigrationAdapterError:
            cleanup_recovery()
            raise
        except (FileEvidenceError, OSError):
            raise MigrationAdapterError(
                "hindsight-admin output archive verification failed"
            ) from None
        except BaseException:
            try:
                cleanup_recovery()
            except Exception:
                # Never delete a name that no longer identifies the file this
                # invocation created, and preserve the original interruption.
                pass
            raise
        finally:
            self._release_output_archive_lock(output_lock_descriptor)
            if parent_descriptor is not None:
                os.close(parent_descriptor)
        return {"completed": True}

    def export_bank(self, archive: str, archive_digest: str, source_bank: str):
        return self._run("export-bank", archive, archive_digest, source_bank)
    def backup(self, archive: str, archive_digest: str): return self._run("backup", archive, archive_digest)
    def import_bank(self, archive: str, archive_digest: str, target_bank: str,
                    expected_evidence_digest: str,
                    disposable_restore_evidence=None):
        return self._run(
            "import-bank", archive, archive_digest, target_bank,
            expected_evidence_digest, disposable_restore_evidence,
        )
    def restore(self, archive: str, archive_digest: str,
                expected_evidence_digest: str, disposable_restore_evidence=None):
        return self._run(
            "restore", archive, archive_digest,
            expected_evidence_digest=expected_evidence_digest,
            evidence=disposable_restore_evidence,
        )


class MigrationApplyAdapter:
    """HTTP reconciliation plus digest-selected full-bank archive imports."""

    IMPORT_KINDS = frozenset({"import_bank", "migrate_bank", "replace_canonical_bank"})

    def __init__(self, *, data_plane: Any, admin: AdminMigrationAdapter,
                 archives: Mapping[str, str], restore_evidence: Mapping[str, Mapping[str, Any]],
                 rollback_archive: str, rollback_archive_digest: str,
                 rollback_restore_evidence_digest: str,
                 archive_verifier: Callable[[str, str], bool],
                 restore_lock_dir: str | None = None) -> None:
        if not isinstance(admin, AdminMigrationAdapter):
            raise MigrationAdapterError("admin migration adapter is required")
        if not isinstance(archives, Mapping) or not isinstance(restore_evidence, Mapping):
            raise MigrationAdapterError("migration archive inputs are invalid")
        if not Path(rollback_archive).is_absolute() or not DIGEST.fullmatch(rollback_archive_digest):
            raise MigrationAdapterError("rollback archive binding is invalid")
        if (
            not isinstance(rollback_restore_evidence_digest, str)
            or DIGEST.fullmatch(rollback_restore_evidence_digest) is None
        ):
            raise MigrationAdapterError("rollback restore evidence binding is invalid")
        if not callable(archive_verifier):
            raise MigrationAdapterError("rollback archive verifier is required")
        self.data_plane = data_plane
        self.admin = admin
        self.archives = dict(archives)
        self.restore_evidence = {
            key: dict(value) if isinstance(value, Mapping) else value
            for key, value in restore_evidence.items()
        }
        self.rollback_archive = rollback_archive
        self.rollback_archive_digest = rollback_archive_digest
        self.rollback_restore_evidence_digest = rollback_restore_evidence_digest
        self.archive_verifier = archive_verifier
        self.restore_lock_dir = Path(
            restore_lock_dir
            if restore_lock_dir is not None
            else Path(tempfile.gettempdir())
            / f"hindsight-memory-control-{os.geteuid()}"
            / "restore-locks"
        )
        if not self.restore_lock_dir.is_absolute():
            raise MigrationAdapterError("rollback restore lock directory is invalid")
        self._verified_rollback_identities: OrderedDict[str, None] = OrderedDict()
        self._verified_rollback_identities_lock = threading.Lock()
        self._restore_lock = threading.Lock()

    def _rollback_identity_is_verified(self, identity: str) -> bool:
        with self._verified_rollback_identities_lock:
            known = identity in self._verified_rollback_identities
            if known:
                self._verified_rollback_identities.move_to_end(identity)
            return known

    def _remember_verified_rollback_identity(self, identity: str) -> None:
        with self._verified_rollback_identities_lock:
            self._verified_rollback_identities[identity] = None
            self._verified_rollback_identities.move_to_end(identity)
            while (
                len(self._verified_rollback_identities)
                > MAX_VERIFIED_ROLLBACK_IDENTITIES
            ):
                self._verified_rollback_identities.popitem(last=False)

    def _restore_binding(self, rollback: Any) -> dict[str, Any]:
        binding = {
            "archive_digest": self.rollback_archive_digest,
            "bundle_digest": getattr(rollback, "bundle_digest", None),
            "endpoint_digest": getattr(rollback, "endpoint_digest", None),
            "plan_digest": getattr(rollback, "plan_digest", None),
            "prestate_digest": getattr(rollback, "prestate_digest", None),
            "restore_evidence_digest": self.rollback_restore_evidence_digest,
            "restore_proof_digest": getattr(
                rollback, "restore_proof_digest", None
            ),
            "rollback_id": getattr(rollback, "rollback_id", None),
        }
        if (
            not isinstance(binding["rollback_id"], str)
            or not binding["rollback_id"]
            or len(binding["rollback_id"].encode("utf-8")) > 256
            or any(
                not isinstance(binding[key], str)
                or DIGEST.fullmatch(binding[key]) is None
                for key in binding
                if key != "rollback_id"
            )
            or getattr(rollback, "archive_digest", None)
            != self.rollback_archive_digest
            or getattr(rollback, "restore_evidence_digest", None)
            != self.rollback_restore_evidence_digest
        ):
            raise MigrationAdapterError("rollback receipt binding is invalid")
        return binding

    def _restore_identity(self, rollback: Any) -> str:
        return hashlib.sha256(
            canonical_bytes(self._restore_binding(rollback))
        ).hexdigest()

    def _restore_receipt_name(self, rollback: Any) -> str:
        return f".hindsight-restore-{self._restore_identity(rollback)}.json"

    @contextmanager
    def _restore_guard(self, rollback: Any):
        binding = self._restore_binding(rollback)
        # The admin endpoint is the mutation target. Different rollback bundle
        # identities for that endpoint must not restore concurrently.
        name = (
            ".hindsight-restore-target-"
            f"{binding['endpoint_digest']}.lock"
        )
        directory: int | None = None
        descriptor = None
        try:
            directory = open_trusted_parent(
                self.restore_lock_dir,
                unavailable_message="rollback restore lock directory is unavailable",
                not_directory_message="rollback restore lock directory is invalid",
                owner_message="rollback restore lock directory owner is unsafe",
                writable_message="rollback restore lock directory permissions are unsafe",
                create_missing=True,
            )
            descriptor = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=directory,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise MigrationAdapterError("rollback restore lock is unsafe")
            _acquire_advisory_lock(
                descriptor, "rollback restore lock is unavailable"
            )
        except MigrationAdapterError:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)
            raise
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)
            raise MigrationAdapterError(
                "rollback restore lock is unavailable"
            ) from None
        try:
            yield
        finally:
            assert descriptor is not None
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
            assert directory is not None
            os.close(directory)

    def _read_restore_receipt(self, rollback: Any) -> Mapping[str, Any] | None:
        directory: int | None = None
        descriptor = None
        try:
            directory = open_trusted_parent(
                Path(self.rollback_archive).parent,
                unavailable_message="rollback receipt parent is unavailable",
                not_directory_message="rollback receipt parent is invalid",
                owner_message="rollback receipt parent owner is unsafe",
                writable_message="rollback receipt parent permissions are unsafe",
                create_missing=False,
            )
            try:
                descriptor = os.open(
                    self._restore_receipt_name(rollback),
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory,
                )
            except FileNotFoundError:
                return None
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > 4096
            ):
                raise MigrationAdapterError("rollback restore receipt is unsafe")
            chunks = bytearray()
            while len(chunks) <= 4096:
                chunk = os.read(descriptor, min(4097 - len(chunks), 4096))
                if not chunk:
                    break
                chunks.extend(chunk)
            body = bytes(chunks)
            receipt = strict_json_loads(body)
            if (
                not isinstance(receipt, Mapping)
                or set(receipt) != {"schema_version", "binding", "phase"}
                or receipt["schema_version"] != 1
                or receipt["binding"] != self._restore_binding(rollback)
                or receipt["phase"] not in {
                    "authorized", "admin_started", "admin_restored",
                    "data_plane_started", "completed"
                }
                or canonical_bytes(receipt) != body
            ):
                raise MigrationAdapterError("rollback restore receipt is invalid")
            return receipt
        except MigrationAdapterError:
            raise
        except Exception:
            raise MigrationAdapterError("rollback restore receipt is invalid") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)

    def _write_restore_receipt(self, rollback: Any, phase: str) -> None:
        phases = {
            "authorized": 1,
            "admin_started": 2,
            "admin_restored": 3,
            "data_plane_started": 4,
            "completed": 5,
        }
        if phase not in phases:
            raise MigrationAdapterError("rollback restore phase is invalid")
        current = self._read_restore_receipt(rollback)
        current_rank = phases[current["phase"]] if current is not None else 0
        requested_rank = phases[phase]
        if requested_rank < current_rank:
            return
        if requested_rank == current_rank:
            return
        if requested_rank != current_rank + 1:
            raise MigrationAdapterError("rollback restore phase is not monotonic")
        directory: int | None = None
        temporary = f".{self._restore_receipt_name(rollback)}.{secrets.token_hex(8)}"
        descriptor = None
        try:
            directory = open_trusted_parent(
                Path(self.rollback_archive).parent,
                unavailable_message="rollback receipt parent is unavailable",
                not_directory_message="rollback receipt parent is invalid",
                owner_message="rollback receipt parent owner is unsafe",
                writable_message="rollback receipt parent permissions are unsafe",
                create_missing=False,
            )
            body = canonical_bytes({
                "schema_version": 1,
                "binding": self._restore_binding(rollback),
                "phase": phase,
            })
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            written = 0
            while written < len(body):
                count = os.write(descriptor, body[written:])
                if count <= 0:
                    raise OSError("short rollback receipt write")
                written += count
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.rename(
                temporary,
                self._restore_receipt_name(rollback),
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
            os.fsync(directory)
        except Exception:
            if directory is not None:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            raise MigrationAdapterError("rollback restore receipt could not be persisted") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)

    def _data_plane_matches_rollback(self, rollback: Any) -> bool:
        try:
            snapshot = self.data_plane.snapshot()
            return (
                isinstance(snapshot, Mapping)
                and digest(snapshot.get("endpoint")) == rollback.endpoint_digest
                and digest(snapshot.get("state")) == rollback.prestate_digest
                and isinstance(snapshot.get("operations"), Mapping)
                and set(snapshot["operations"]) == {"idle", "active"}
                and snapshot["operations"]["idle"] is True
                and snapshot["operations"]["active"] == []
            )
        except Exception:
            return False

    def _require_archive_digest(self, archive: str, archive_digest: str) -> None:
        try:
            verified = self.archive_verifier(archive, archive_digest)
        except Exception:
            verified = False
        if verified is not True:
            raise MigrationAdapterError("archive digest does not match plan")

    def __getattr__(self, name: str) -> Any:
        return getattr(self.data_plane, name)

    def apply_action(self, action: Any) -> None:
        if action.kind not in self.IMPORT_KINDS:
            self.data_plane.apply_action(action)
            return
        archive_digest = action.details.get("archive_digest")
        archive = self.archives.get(archive_digest)
        evidence = self.restore_evidence.get(archive_digest)
        if archive is None or evidence is None:
            raise MigrationAdapterError("approved migration archive or restore evidence is unavailable")
        target_bank = action.details.get("target_bank")
        if (
            not isinstance(target_bank, Mapping)
            or not isinstance(target_bank.get("bank_id"), str)
            or BANK_ID.fullmatch(target_bank["bank_id"]) is None
        ):
            raise MigrationAdapterError("migration target bank is unavailable")
        self._require_archive_digest(archive, archive_digest)

        def mutation() -> None:
            try:
                with verified_file_snapshot(
                    archive, "migration archive", archive_digest,
                ) as snapshot:
                    self.admin.import_bank(
                        snapshot, archive_digest, target_bank.get("bank_id"),
                        action.details.get("restore_evidence_digest"), evidence,
                    )
            except FileEvidenceError:
                raise MigrationAdapterError(
                    "migration archive snapshot verification failed"
                ) from None

        attest = getattr(self.data_plane, "attest_external_action", None)
        if not callable(attest):
            raise MigrationAdapterError(
                "migration import action attestation is unavailable"
            )
        attest(action, mutation)

    def preflight_action(self, action: Any) -> None:
        if action.kind not in self.IMPORT_KINDS:
            preflight = getattr(self.data_plane, "preflight_action", None)
            if not callable(preflight):
                raise MigrationAdapterError("data-plane mutation preflight is unavailable")
            preflight(action)
            return
        archive_digest = action.details.get("archive_digest")
        archive = self.archives.get(archive_digest)
        evidence = self.restore_evidence.get(archive_digest)
        target_bank = action.details.get("target_bank")
        if (
            archive is None
            or evidence is None
            or not isinstance(target_bank, Mapping)
            or not isinstance(target_bank.get("bank_id"), str)
            or BANK_ID.fullmatch(target_bank["bank_id"]) is None
            or not callable(
                getattr(self.data_plane, "attest_external_action", None)
            )
        ):
            raise MigrationAdapterError(
                "approved migration preflight inputs are unavailable"
            )
        self._require_archive_digest(archive, archive_digest)
        AdminMigrationAdapter._restore_evidence(
            evidence,
            archive_digest,
            action.details.get("restore_evidence_digest"),
        )

    def create_rollback_bundle(
        self,
        plan_digest: str,
        action_ids: tuple[str, ...],
        *,
        archive_digest: str | None = None,
        restore_evidence_digest: str | None = None,
    ) -> Any:
        if archive_digest is None and restore_evidence_digest is None:
            archive_digest = self.rollback_archive_digest
            restore_evidence_digest = self.rollback_restore_evidence_digest
        if (
            archive_digest != self.rollback_archive_digest
            or restore_evidence_digest
            != self.rollback_restore_evidence_digest
        ):
            raise MigrationAdapterError(
                "rollback archive bindings do not match the approved plan"
            )
        evidence = self.restore_evidence.get(self.rollback_archive_digest)
        AdminMigrationAdapter._restore_evidence(
            evidence,
            self.rollback_archive_digest,
            self.rollback_restore_evidence_digest,
        )
        rollback_path = Path(self.rollback_archive)
        if rollback_path.exists() or rollback_path.is_symlink():
            self._require_archive_digest(
                self.rollback_archive, self.rollback_archive_digest,
            )
        else:
            self.admin.backup(
                self.rollback_archive, self.rollback_archive_digest
            )
        self._require_archive_digest(
            self.rollback_archive, self.rollback_archive_digest,
        )
        bundle = self.data_plane.create_rollback_bundle(
            plan_digest,
            action_ids,
            archive_digest=archive_digest,
            restore_evidence_digest=restore_evidence_digest,
        )
        with self._restore_guard(bundle):
            self._write_restore_receipt(bundle, "authorized")
        return bundle

    def verify_rollback_bundle(self, rollback: Any) -> bool:
        try:
            rollback_identity = self._restore_identity(rollback)
        except MigrationAdapterError:
            return False
        known = self._rollback_identity_is_verified(rollback_identity)
        if not known:
            try:
                if self._read_restore_receipt(rollback) is None:
                    return False
            except MigrationAdapterError:
                return False
        evidence = self.restore_evidence.get(self.rollback_archive_digest)
        try:
            if self.archive_verifier(
                self.rollback_archive, self.rollback_archive_digest
            ) is not True:
                return False
            AdminMigrationAdapter._restore_evidence(
                evidence,
                self.rollback_archive_digest,
                self.rollback_restore_evidence_digest,
            )
        except Exception:
            return False
        verified = self.data_plane.verify_rollback_bundle(rollback) is True
        if verified:
            self._remember_verified_rollback_identity(rollback_identity)
        return verified

    def restore(self, rollback: Any) -> None:
        with self._restore_lock:
            with self._restore_guard(rollback):
                self._restore_once(rollback)

    def _restore_once(self, rollback: Any) -> None:
        receipt = self._read_restore_receipt(rollback)
        self._require_archive_digest(
            self.rollback_archive, self.rollback_archive_digest,
        )
        try:
            with verified_file_snapshot(
                self.rollback_archive,
                "rollback archive",
                self.rollback_archive_digest,
            ):
                pass
        except FileEvidenceError:
            raise MigrationAdapterError(
                "rollback archive snapshot verification failed"
            ) from None
        try:
            rollback_identity = self._restore_identity(rollback)
        except MigrationAdapterError:
            known = False
        else:
            known = self._rollback_identity_is_verified(rollback_identity)
        if not known:
            try:
                verified = receipt is not None and self.verify_rollback_bundle(
                    rollback
                ) is True
            except Exception:
                verified = False
            if not verified:
                raise MigrationAdapterError("rollback bundle is not bound to the migration adapter")
        if receipt is not None and receipt["phase"] == "completed":
            if not self._data_plane_matches_rollback(rollback):
                raise MigrationAdapterError("completed rollback receipt does not match data plane")
            return
        evidence = self.restore_evidence.get(self.rollback_archive_digest)
        if receipt is not None and receipt["phase"] == "admin_started":
            raise MigrationAdapterError(
                "rollback admin restore outcome is indeterminate"
            )
        if receipt is None:
            try:
                with verified_file_snapshot(
                    self.rollback_archive,
                    "rollback archive",
                    self.rollback_archive_digest,
                ):
                    AdminMigrationAdapter._restore_evidence(
                        evidence,
                        self.rollback_archive_digest,
                        self.rollback_restore_evidence_digest,
                    )
            except FileEvidenceError:
                raise MigrationAdapterError(
                    "rollback archive snapshot verification failed"
                ) from None
            self._write_restore_receipt(rollback, "authorized")
            receipt = self._read_restore_receipt(rollback)
        if receipt is not None and receipt["phase"] == "authorized":
            try:
                with verified_file_snapshot(
                    self.rollback_archive,
                    "rollback archive",
                    self.rollback_archive_digest,
                ) as snapshot:
                    AdminMigrationAdapter._restore_evidence(
                        evidence,
                        self.rollback_archive_digest,
                        self.rollback_restore_evidence_digest,
                    )
                    self._write_restore_receipt(rollback, "admin_started")
                    self.admin.restore(
                        snapshot,
                        self.rollback_archive_digest,
                        self.rollback_restore_evidence_digest,
                        evidence,
                    )
            except FileEvidenceError:
                raise MigrationAdapterError(
                    "rollback archive snapshot verification failed"
                ) from None
            self._write_restore_receipt(rollback, "admin_restored")
            receipt = self._read_restore_receipt(rollback)
        if receipt is not None and receipt["phase"] == "data_plane_started":
            if self._data_plane_matches_rollback(rollback):
                self._write_restore_receipt(rollback, "completed")
                return
        self._write_restore_receipt(rollback, "data_plane_started")
        self.data_plane.restore(rollback)
        if not self._data_plane_matches_rollback(rollback):
            raise MigrationAdapterError("data-plane rollback restore did not reconcile")
        self._write_restore_receipt(rollback, "completed")
