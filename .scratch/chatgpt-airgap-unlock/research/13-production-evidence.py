#!/usr/bin/env python3
"""Seal issue-13 staging trees and finalize guest evidence fail closed."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import tempfile
from typing import Any, Callable, Sequence


SCHEMA = 1
PREFLIGHT_FAILURE = 78
EVIDENCE_FAILURE = 70
RUN_NONCE_ENV = "PRODUCTION_EVIDENCE_RUN_NONCE"
STATE_PATH_ENV = "PRODUCTION_EVIDENCE_STATE_PATH"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:^|[_-])(authorization|credential|password|secret|token|api[_-]?key)(?:$|[_-])",
    re.IGNORECASE,
)
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_.-]{4,}"),
    re.compile(
        r"(?<!\S)(?:--?)?(?:authorization|credential|password|secret|token|"
        r"api[-_]?key|access[-_]?token|client[-_]?secret)(?:=|[ \t]+)\S+",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:authorization|credential|password|secret|token|api[_-]?key|"
        r"access[_-]?token|client[_-]?secret)=\S+",
        re.IGNORECASE,
    ),
)


class EvidenceError(ValueError):
    """A staging or evidence contract failed closed."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceError("artifact path must be a nonempty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise EvidenceError("artifact path must be normalized and relative")
    normalized = path.as_posix()
    if normalized != value:
        raise EvidenceError("artifact path must use normalized POSIX separators")
    return normalized


def _reject_sensitive_metadata(value: Any, location: str = "bindings") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise EvidenceError(f"{location} keys must be strings")
            normalized_key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
            if SENSITIVE_KEY_PATTERN.search(normalized_key):
                raise EvidenceError(f"{location} contains a forbidden sensitive key")
            _reject_sensitive_metadata(child, f"{location}.{key}")
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_metadata(child, location)
    elif isinstance(value, (str, int, bool)) or value is None:
        if isinstance(value, str) and any(
            pattern.search(value) for pattern in SENSITIVE_TEXT_PATTERNS
        ):
            raise EvidenceError(f"{location} contains secret-shaped text")
    else:
        raise EvidenceError(f"{location} contains a non-JSON value")


def _validate_bindings(
    bindings: Any, entries: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(bindings, dict) or set(bindings) != {"schema", "artifacts"}:
        raise EvidenceError("bindings must contain only schema and artifacts")
    if (
        bindings["schema"] != SCHEMA
        or not isinstance(bindings["artifacts"], list)
        or not bindings["artifacts"]
    ):
        raise EvidenceError("bindings schema is unsupported")
    _reject_sensitive_metadata(bindings)
    names: set[str] = set()
    paths: set[str] = set()
    for artifact in bindings["artifacts"]:
        if not isinstance(artifact, dict) or set(artifact) != {
            "name",
            "path",
            "sha256",
            "identity",
        }:
            raise EvidenceError(
                "each artifact binding must contain name, path, sha256, and identity"
            )
        name = artifact["name"]
        if not isinstance(name, str) or not name or name in names:
            raise EvidenceError(
                "artifact binding names must be unique nonempty strings"
            )
        names.add(name)
        path = _relative_path(artifact["path"])
        if path in paths:
            raise EvidenceError("artifact binding paths must be unique")
        paths.add(path)
        expected_sha256 = artifact["sha256"]
        if not isinstance(expected_sha256, str) or not SHA256_PATTERN.fullmatch(
            expected_sha256
        ):
            raise EvidenceError(
                "artifact binding SHA-256 must be lowercase hexadecimal"
            )
        identity = artifact["identity"]
        if not isinstance(identity, dict):
            raise EvidenceError("artifact binding identity must be an object")
        for required_field in ("role", "review"):
            value = identity.get(required_field)
            if not isinstance(value, str) or not value.strip():
                raise EvidenceError(
                    "artifact binding identity requires nonempty role and review"
                )
        entry = entries.get(path)
        if entry is None or entry["sha256"] != expected_sha256:
            raise EvidenceError(f"artifact binding does not match sealed entry: {path}")
    return bindings


def _scan_tree(stage_root: Path) -> list[dict[str, Any]]:
    try:
        root_stat = stage_root.lstat()
    except FileNotFoundError as error:
        raise EvidenceError("staging root does not exist") from error
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise EvidenceError("staging root must be a real directory")
    entries: list[dict[str, Any]] = []
    for path in sorted(
        stage_root.rglob("*"), key=lambda candidate: candidate.as_posix()
    ):
        metadata = path.lstat()
        relative = path.relative_to(stage_root).as_posix()
        if stat.S_ISLNK(metadata.st_mode):
            raise EvidenceError(f"staging tree contains a symlink: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise EvidenceError(f"staging tree contains a non-regular file: {relative}")
        entries.append(
            {
                "path": relative,
                "mode": stat.S_IMODE(metadata.st_mode),
                "size": metadata.st_size,
                "sha256": file_sha256(path),
            }
        )
    if not entries:
        raise EvidenceError("staging tree must contain at least one regular file")
    return entries


def build_manifest(stage_root: Path, bindings: Any) -> dict[str, Any]:
    entries = _scan_tree(stage_root)
    entries_by_path = {entry["path"]: entry for entry in entries}
    checked_bindings = _validate_bindings(bindings, entries_by_path)
    sealed = {"schema": SCHEMA, "bindings": checked_bindings, "entries": entries}
    return {**sealed, "tree_sha256": hashlib.sha256(canonical_json(sealed)).hexdigest()}


def validate_manifest(stage_root: Path, manifest: Any) -> None:
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema",
        "bindings",
        "entries",
        "tree_sha256",
    }:
        raise EvidenceError("staging manifest has unexpected fields")
    if manifest["schema"] != SCHEMA or not isinstance(manifest["tree_sha256"], str):
        raise EvidenceError("staging manifest schema is unsupported")
    rebuilt = build_manifest(stage_root, manifest["bindings"])
    if rebuilt != manifest:
        raise EvidenceError("staging tree does not match its sealed manifest")


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            if value and not value.endswith("\n"):
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as error:
        raise EvidenceError("required JSON input is missing or invalid") from error


def _load_owned_state(path: Path, expected_run_nonce: str | None) -> dict[str, Any]:
    if expected_run_nonce is None:
        raise EvidenceError("owned state was not initialized for this invocation")
    state = _load_json(path)
    if not isinstance(state, dict) or set(state) != {
        "schema",
        "run_nonce",
        "transition",
        "owned_pids",
        "owned_process_groups",
        "reserved_tcp_ports",
        "cleanup_steps",
    }:
        raise EvidenceError("owned state has unexpected fields")
    if state["schema"] != SCHEMA:
        raise EvidenceError("owned state schema is unsupported")
    if state["run_nonce"] != expected_run_nonce or state["transition"] != "completed":
        raise EvidenceError("owned state does not complete this invocation")
    pids = state["owned_pids"]
    process_groups = state["owned_process_groups"]
    ports = state["reserved_tcp_ports"]
    steps = state["cleanup_steps"]
    if (
        not isinstance(pids, list)
        or any(type(pid) is not int or pid <= 1 for pid in pids)
        or len(pids) != len(set(pids))
    ):
        raise EvidenceError("owned PIDs must be unique integers greater than one")
    if (
        not isinstance(process_groups, list)
        or any(type(pgid) is not int or pgid <= 1 for pgid in process_groups)
        or len(process_groups) != len(set(process_groups))
    ):
        raise EvidenceError(
            "owned process groups must be unique integers greater than one"
        )
    if (
        not isinstance(ports, list)
        or any(type(port) is not int or not 1 <= port <= 65535 for port in ports)
        or len(ports) != len(set(ports))
    ):
        raise EvidenceError("reserved TCP ports must be unique valid integers")
    if not isinstance(steps, list) or not steps:
        raise EvidenceError("owned state must contain at least one cleanup step")
    names: set[str] = set()
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"name", "completed"}:
            raise EvidenceError("cleanup steps must contain only name and completed")
        if (
            not isinstance(step["name"], str)
            or not step["name"]
            or step["name"] in names
            or type(step["completed"]) is not bool
        ):
            raise EvidenceError(
                "cleanup step names must be unique and completion must be boolean"
            )
        names.add(step["name"])
    return state


def _standard_process_snapshot() -> tuple[int, str]:
    collector = subprocess.Popen(
        ["/bin/ps", "-axo", "pid,ppid,pgid,lstart,state,etime,command"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = collector.communicate()
    visible = "\n".join(
        line
        for line in stdout.splitlines()
        if not line.lstrip().startswith(f"{collector.pid} ")
    )
    if stdout.endswith("\n"):
        visible += "\n"
    return collector.returncode, visible + stderr


def _standard_socket_snapshot() -> tuple[int, str]:
    completed = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-iTCP", "-iUDP"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout + completed.stderr


def _standard_execute(command: list[str], environment: dict[str, str]) -> int:
    return subprocess.run(command, check=False, env=environment).returncode


def _redact(value: str) -> tuple[str, bool]:
    redacted = value
    changed = False
    for pattern in SENSITIVE_TEXT_PATTERNS:
        replaced, count = pattern.subn("[REDACTED]", redacted)
        redacted = replaced
        changed = changed or count > 0
    return redacted, changed


def _snapshot_processes(snapshot: str) -> dict[int, tuple[int, int, str]]:
    result: dict[int, tuple[int, int, str]] = {}
    for line in snapshot.splitlines():
        fields = line.split(maxsplit=10)
        if len(fields) >= 3 and all(field.isdigit() for field in fields[:3]):
            pid = int(fields[0])
            if len(fields) >= 11:
                started = " ".join(fields[3:8])
                command = fields[10]
            else:
                started = "legacy-snapshot"
                command = " ".join(fields[3:])
            identity = hashlib.sha256(
                f"{pid}\0{started}\0{command}".encode("utf-8")
            ).hexdigest()
            result[pid] = (int(fields[1]), int(fields[2]), identity)
    return result


def _surviving_owned_processes(
    snapshot: str, state: dict[str, Any]
) -> tuple[list[int], list[int], list[int]]:
    processes = _snapshot_processes(snapshot)
    owned_pids = set(state["owned_pids"])
    owned_process_groups = set(state["owned_process_groups"])
    surviving_owned_pids = sorted(owned_pids & processes.keys())
    surviving_group_pids = sorted(
        pid
        for pid, (_ppid, pgid, _identity) in processes.items()
        if pgid in owned_process_groups
    )
    descendants: set[int] = set()
    while True:
        discovered = {
            pid
            for pid, (ppid, _pgid, _identity) in processes.items()
            if ppid in owned_pids or ppid in descendants
        }
        if discovered <= descendants:
            break
        descendants.update(discovered)
    return surviving_owned_pids, surviving_group_pids, sorted(descendants)


def _surviving_run_created_pids(baseline: str, final: str) -> list[int]:
    baseline_processes = _snapshot_processes(baseline)
    final_processes = _snapshot_processes(final)
    return sorted(
        pid
        for pid, (_ppid, _pgid, identity) in final_processes.items()
        if pid not in baseline_processes or baseline_processes[pid][2] != identity
    )


def _listening_ports(snapshot: str) -> set[int]:
    return {int(value) for value in re.findall(r":(\d+)\s+\(LISTEN\)", snapshot)}


def _safe_collect(collector: Callable[[], tuple[int, str]]) -> tuple[int, str]:
    try:
        code, snapshot = collector()
        if type(code) is not int or not isinstance(snapshot, str):
            return 127, "snapshot collector returned an invalid result\n"
        return code, snapshot
    except Exception:
        return 127, "snapshot collector raised an exception\n"


def _finalize(
    evidence_dir: Path,
    state_path: Path,
    *,
    manifest_preflight_valid: bool,
    manifest_postflight_valid: bool,
    manifest_tree_sha256: str | None,
    expected_run_nonce: str | None,
    command_started: bool,
    command_exit_code: int,
    baseline_process_code: int,
    baseline_processes: str,
    errors: list[str],
    collect_processes: Callable[[], tuple[int, str]],
    collect_sockets: Callable[[], tuple[int, str]],
) -> bool:
    try:
        state = _load_owned_state(state_path, expected_run_nonce)
        state_valid = True
    except EvidenceError:
        state = {
            "schema": SCHEMA,
            "run_nonce": None,
            "transition": "invalid",
            "owned_pids": [],
            "owned_process_groups": [],
            "reserved_tcp_ports": [],
            "cleanup_steps": [],
        }
        state_valid = False
        errors.append("owned-state-invalid")

    process_code, raw_processes = _safe_collect(collect_processes)
    socket_code, raw_sockets = _safe_collect(collect_sockets)
    processes, process_redacted = _redact(raw_processes)
    sockets, socket_redacted = _redact(raw_sockets)
    sensitive_data_redacted = process_redacted or socket_redacted
    process_snapshot_captured = process_code == 0
    process_baseline_captured = command_started and baseline_process_code == 0
    socket_snapshot_captured = socket_code in {0, 1}
    if not process_snapshot_captured:
        errors.append("process-snapshot-failed")
    if not socket_snapshot_captured:
        errors.append("socket-snapshot-failed")
    if sensitive_data_redacted:
        errors.append("sensitive-data-redacted")

    observed_ports = _listening_ports(sockets) if socket_snapshot_captured else set()
    if process_snapshot_captured:
        surviving_pids, surviving_group_pids, surviving_descendant_pids = (
            _surviving_owned_processes(raw_processes, state)
        )
    else:
        surviving_pids, surviving_group_pids, surviving_descendant_pids = [], [], []
    if process_baseline_captured and process_snapshot_captured:
        surviving_run_created_pids = _surviving_run_created_pids(
            baseline_processes, raw_processes
        )
    else:
        surviving_run_created_pids = []
    surviving_ports = sorted(set(state["reserved_tcp_ports"]) & observed_ports)
    owned_process_groups_exited = process_snapshot_captured and not surviving_group_pids
    owned_descendants_exited = (
        process_snapshot_captured and not surviving_descendant_pids
    )
    run_created_processes_exited = (
        process_baseline_captured
        and process_snapshot_captured
        and not surviving_run_created_pids
    )
    owned_processes_exited = (
        process_snapshot_captured
        and not surviving_pids
        and owned_process_groups_exited
        and owned_descendants_exited
        and run_created_processes_exited
    )
    owned_listeners_closed = socket_snapshot_captured and not surviving_ports
    cleanup_steps_complete = state_valid and all(
        step["completed"] for step in state["cleanup_steps"]
    )

    cleanup = {
        "schema": SCHEMA,
        "run_nonce": state["run_nonce"],
        "transition": state["transition"],
        "state_valid": state_valid,
        "cleanup_steps": state["cleanup_steps"],
        "cleanup_steps_complete": cleanup_steps_complete,
        "owned_pids": state["owned_pids"],
        "owned_process_groups": state["owned_process_groups"],
        "reserved_tcp_ports": state["reserved_tcp_ports"],
        "surviving_owned_pids": surviving_pids,
        "surviving_owned_process_group_pids": surviving_group_pids,
        "surviving_owned_descendant_pids": surviving_descendant_pids,
        "surviving_run_created_pids": surviving_run_created_pids,
        "surviving_reserved_tcp_ports": surviving_ports,
        "process_baseline_captured": process_baseline_captured,
        "owned_processes_exited": owned_processes_exited,
        "owned_process_groups_exited": owned_process_groups_exited,
        "owned_descendants_exited": owned_descendants_exited,
        "run_created_processes_exited": run_created_processes_exited,
        "owned_listeners_closed": owned_listeners_closed,
    }
    passed = all(
        (
            manifest_preflight_valid,
            manifest_postflight_valid,
            command_started,
            command_exit_code == 0,
            state_valid,
            cleanup_steps_complete,
            process_baseline_captured,
            process_snapshot_captured,
            socket_snapshot_captured,
            owned_processes_exited,
            owned_process_groups_exited,
            owned_descendants_exited,
            run_created_processes_exited,
            owned_listeners_closed,
            not sensitive_data_redacted,
            not errors,
        )
    )
    verdict = {
        "schema": SCHEMA,
        "run_nonce": expected_run_nonce,
        "passed": passed,
        "manifest_valid": manifest_preflight_valid and manifest_postflight_valid,
        "manifest_preflight_valid": manifest_preflight_valid,
        "manifest_postflight_valid": manifest_postflight_valid,
        "manifest_tree_sha256": manifest_tree_sha256,
        "command_started": command_started,
        "command_exit_code": command_exit_code,
        "cleanup_state_valid": state_valid,
        "cleanup_steps_complete": cleanup_steps_complete,
        "process_baseline_captured": process_baseline_captured,
        "process_snapshot_captured": process_snapshot_captured,
        "socket_snapshot_captured": socket_snapshot_captured,
        "owned_processes_exited": owned_processes_exited,
        "owned_process_groups_exited": owned_process_groups_exited,
        "owned_descendants_exited": owned_descendants_exited,
        "run_created_processes_exited": run_created_processes_exited,
        "owned_listeners_closed": owned_listeners_closed,
        "sensitive_data_redacted": sensitive_data_redacted,
        "errors": sorted(set(errors)),
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(evidence_dir / "cleanup-final.json", cleanup)
    atomic_write_text(evidence_dir / "processes-final.txt", processes)
    atomic_write_text(evidence_dir / "sockets-final.txt", sockets)
    atomic_write_json(evidence_dir / "verdict.json", verdict)
    return passed


def run_guarded(
    stage_root: Path,
    manifest_path: Path,
    evidence_dir: Path,
    state_path: Path,
    command: Sequence[str],
    *,
    execute: Callable[[list[str], dict[str, str]], int] = _standard_execute,
    collect_processes: Callable[[], tuple[int, str]] = _standard_process_snapshot,
    collect_sockets: Callable[[], tuple[int, str]] = _standard_socket_snapshot,
) -> int:
    for run_owned_path in (manifest_path, evidence_dir, state_path):
        _ensure_output_outside_stage(stage_root, run_owned_path)
    try:
        evidence_path_invalid = evidence_dir.is_symlink() or (
            evidence_dir.exists()
            and (
                not evidence_dir.is_dir()
                or next(evidence_dir.iterdir(), None) is not None
            )
        )
    except OSError as error:
        raise EvidenceError("terminal evidence directory is not accessible") from error
    if evidence_path_invalid:
        raise EvidenceError("terminal evidence directory must be fresh")
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise EvidenceError("terminal evidence directory cannot be created") from error

    manifest: Any = None
    manifest_preflight_valid = False
    manifest_postflight_valid = False
    manifest_tree_sha256: str | None = None
    expected_run_nonce: str | None = None
    command_started = False
    command_exit_code = PREFLIGHT_FAILURE
    baseline_process_code = 127
    baseline_processes = ""
    errors: list[str] = []
    try:
        manifest = _load_json(manifest_path)
        validate_manifest(stage_root, manifest)
        manifest_preflight_valid = True
        manifest_tree_sha256 = manifest["tree_sha256"]
    except Exception:
        errors.append("staging-manifest-invalid")

    if manifest_preflight_valid:
        if not command:
            errors.append("guarded-command-empty")
        elif state_path.exists() or state_path.is_symlink():
            errors.append("owned-state-not-fresh")
        else:
            expected_run_nonce = secrets.token_hex(16)
            try:
                atomic_write_json(
                    state_path,
                    {
                        "schema": SCHEMA,
                        "run_nonce": expected_run_nonce,
                        "transition": "prepared",
                        "owned_pids": [],
                        "owned_process_groups": [],
                        "reserved_tcp_ports": [],
                        "cleanup_steps": [],
                    },
                )
            except OSError:
                expected_run_nonce = None
                errors.append("owned-state-initialize-failed")
            else:
                environment = {
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                    "HOME": "/var/empty",
                    "TMPDIR": "/private/tmp",
                    "LANG": "C",
                    "LC_ALL": "C",
                }
                environment[RUN_NONCE_ENV] = expected_run_nonce
                environment[STATE_PATH_ENV] = str(state_path.resolve())
                baseline_process_code, baseline_processes = _safe_collect(
                    collect_processes
                )
                if baseline_process_code != 0:
                    errors.append("process-baseline-failed")
                command_started = True
                try:
                    command_exit_code = execute(list(command), environment)
                    if type(command_exit_code) is not int:
                        raise TypeError("guarded command returned a non-integer status")
                except Exception:
                    command_exit_code = EVIDENCE_FAILURE
                    errors.append("guarded-command-execution-failed")

    if manifest_preflight_valid:
        try:
            validate_manifest(stage_root, manifest)
            manifest_postflight_valid = True
        except Exception:
            errors.append("staging-manifest-postflight-invalid")

    passed = _finalize(
        evidence_dir,
        state_path,
        manifest_preflight_valid=manifest_preflight_valid,
        manifest_postflight_valid=manifest_postflight_valid,
        manifest_tree_sha256=manifest_tree_sha256,
        expected_run_nonce=expected_run_nonce,
        command_started=command_started,
        command_exit_code=command_exit_code,
        baseline_process_code=baseline_process_code,
        baseline_processes=baseline_processes,
        errors=errors,
        collect_processes=collect_processes,
        collect_sockets=collect_sockets,
    )
    if command_exit_code != 0:
        return command_exit_code
    return 0 if passed else EVIDENCE_FAILURE


def _load_bindings(path: Path) -> Any:
    return _load_json(path)


def _ensure_output_outside_stage(stage_root: Path, output: Path) -> None:
    root = stage_root.resolve()
    target = output.resolve(strict=False)
    if target == root or root in target.parents:
        raise EvidenceError("run-owned paths must be outside the sealed tree")


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    build = subparsers.add_parser("manifest-build")
    build.add_argument("--stage-root", type=Path, required=True)
    build.add_argument("--bindings", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("manifest-validate")
    validate.add_argument("--stage-root", type=Path, required=True)
    validate.add_argument("--manifest", type=Path, required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--stage-root", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--evidence-dir", type=Path, required=True)
    run.add_argument("--owned-state", type=Path, required=True)
    run.add_argument("guarded_command", nargs=argparse.REMAINDER)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    options = parse_arguments(arguments)
    try:
        if options.command_name == "manifest-build":
            _ensure_output_outside_stage(options.stage_root, options.output)
            manifest = build_manifest(
                options.stage_root, _load_bindings(options.bindings)
            )
            atomic_write_json(options.output, manifest)
            print(
                json.dumps({"schema": SCHEMA, "tree_sha256": manifest["tree_sha256"]})
            )
            return 0
        if options.command_name == "manifest-validate":
            manifest = _load_json(options.manifest)
            validate_manifest(options.stage_root, manifest)
            print(json.dumps({"schema": SCHEMA, "valid": True}))
            return 0
        guarded_command = list(options.guarded_command)
        if guarded_command[:1] == ["--"]:
            guarded_command = guarded_command[1:]
        return run_guarded(
            options.stage_root,
            options.manifest,
            options.evidence_dir,
            options.owned_state,
            guarded_command,
        )
    except EvidenceError as error:
        print(f"production-evidence: {error}", file=os.sys.stderr)
        return PREFLIGHT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
