from __future__ import annotations

import contextlib
import copy
import ctypes
import fcntl
import gc
import hashlib
import importlib.util
import io
import json
import os
import platform
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[1]
RUNNER = REPOSITORY / "scripts" / "run_task_witness_qualification.py"
SUITE_DRIVER = REPOSITORY / "scripts" / "run_task_witness_qualification_suite.py"
CANDIDATE_SUPPORT_PATHS = (
    "docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md",
    "docs/superpowers/specs/2026-08-12-task-witness-tw4-migration-and-qualification-design.md",
    "release/task-witness/migration",
    "release/task-witness/source-shape-review.json",
    "release/task-witness/tw4-bridge-identity.json",
    "release/task-witness/tw4-bridge-provenance.json",
    "release/task-witness/tw4-suite-inventory.json",
    "scripts/run_task_witness_qualification.py",
    "scripts/run_task_witness_qualification_suite.py",
    "tests/plugins/task_witness_client",
    "tests/plugins/task_witness_deployment",
    "tests/test_task_witness_package.py",
    "tests/test_task_witness_qualification.py",
)


def load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "task_witness_qualification_under_test",
        RUNNER,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("qualification runner module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_suite_driver_module():
    spec = importlib.util.spec_from_file_location(
        "task_witness_qualification_suite_under_test",
        SUITE_DRIVER,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("qualification suite driver module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_retired_internal_main(
    path: Path,
    arguments: list[str],
    *,
    function: str = "main",
    cwd: Path | None = None,
    text: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Exercise retained design code without reopening its executable entrypoint."""

    script = (
        "import json, runpy, sys\n"
        "arguments = json.load(sys.stdin)\n"
        f"namespace = runpy.run_path({str(path)!r})\n"
        f"sys.argv = [{str(path)!r}, *arguments]\n"
        f"raise SystemExit(namespace[{function!r}]())\n"
    )
    payload = json.dumps(arguments)
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        cwd=cwd,
        input=payload if text else payload.encode(),
        capture_output=True,
        text=text,
        timeout=timeout,
        check=False,
    )


def content_document(value: dict[str, object]) -> dict[str, object]:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return {**value, "content_sha256": hashlib.sha256(raw).hexdigest()}


def canonical_document(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def create_synthetic_candidate(
    root: Path,
    *,
    validator_projection: object | None = None,
    mutate_validator: bool = False,
    mutate_administration: bool = False,
    attempt_process_escape: bool = False,
) -> Path:
    candidate = root / "candidate"
    candidate.mkdir()
    files: dict[str, bytes] = {
        ".gitignore": b"ignored\n",
        "outside.txt": b'{"name":"fixture"}\n',
        "release/public-release-runtime-packages.json": (
            b'{"runtime_packages":["task-witness"],"schema_version":1}\n'
        ),
        "scripts/agent_plugins_standard.py": b"MARKER = 'captured-agent-standard'\n",
    }
    for source_root in (
        REPOSITORY / "plugins/task-witness",
        REPOSITORY / "release/task-witness/migration",
    ):
        for source in source_root.rglob("*"):
            if source.is_file():
                relative = source.relative_to(REPOSITORY).as_posix()
                files[relative] = source.read_bytes()
    files["outside.txt"] = files["plugins/task-witness/plugin.json"]
    for relative in CANDIDATE_SUPPORT_PATHS:
        if relative.endswith(
            ("migration", "task_witness_client", "task_witness_deployment")
        ):
            if relative != "release/task-witness/migration":
                files[f"{relative}/fixture.txt"] = b"fixture\n"
        else:
            files[relative] = b"fixture\n"
    inventory_raw = canonical_document(suite_inventory_document())
    files["release/task-witness/tw4-suite-inventory.json"] = inventory_raw
    registration = {
        "production_eligible": False,
        "schema_version": 1,
        "source_stage_validator_flags": ["--source-stage"],
        "support_paths": list(CANDIDATE_SUPPORT_PATHS),
    }
    files["release/task-witness/public-release-registration.json"] = (
        json.dumps(registration, indent=2, sort_keys=True).encode() + b"\n"
    )
    identity_raw = (
        REPOSITORY / "release/task-witness/tw4-bridge-identity.json"
    ).read_bytes()
    provenance_raw = (
        REPOSITORY / "release/task-witness/tw4-bridge-provenance.json"
    ).read_bytes()
    identity = json.loads(identity_raw)
    files["release/task-witness/tw4-bridge-identity.json"] = identity_raw
    files["release/task-witness/tw4-bridge-provenance.json"] = provenance_raw
    bridge_history = {
        "bridge_identity_sha256": hashlib.sha256(identity_raw).hexdigest(),
        "bridge_provenance_sha256": hashlib.sha256(provenance_raw).hexdigest(),
        "freeze5": identity["freeze5"],
        "bridge": identity["bridge"],
    }
    projection = (
        bridge_history if validator_projection is None else validator_projection
    )
    mutations = []
    if mutate_validator:
        mutations.append("Path(__file__).write_text('mutated\\n', encoding='utf-8')")
    if mutate_administration:
        mutations.append(
            "(root / '.git/config').write_text('[core]\\nrepositoryformatversion = 0\\n', encoding='utf-8')"
        )
    process_escape_source = ""
    if attempt_process_escape:
        process_escape_source = (
            "import errno, os, resource, subprocess, sys\n"
            "if resource.getrlimit(resource.RLIMIT_NPROC) != (0, 0):\n"
            "    raise ValueError('process limit is not sealed')\n"
            "try:\n"
            "    child = os.fork()\n"
            "except OSError as error:\n"
            "    if error.errno != errno.EAGAIN:\n"
            "        raise\n"
            "else:\n"
            "    if child == 0:\n"
            "        os.setsid()\n"
            "        os._exit(0)\n"
            "    os.waitpid(child, 0)\n"
            "    raise ValueError('fork unexpectedly succeeded')\n"
            "spawn_argv = [sys.executable, '-I', '-B', '-c', 'pass']\n"
            "try:\n"
            "    child = os.posix_spawn(sys.executable, spawn_argv, os.environ)\n"
            "except OSError as error:\n"
            "    if error.errno != errno.EAGAIN:\n"
            "        raise\n"
            "else:\n"
            "    os.waitpid(child, 0)\n"
            "    raise ValueError('posix_spawn unexpectedly succeeded')\n"
            "try:\n"
            "    subprocess.run(spawn_argv, check=True)\n"
            "except OSError as error:\n"
            "    if error.errno != errno.EAGAIN:\n"
            "        raise\n"
            "else:\n"
            "    raise ValueError('subprocess unexpectedly succeeded')\n"
        )
        files["scripts/agent_plugins_standard.py"] = (
            process_escape_source + "MARKER = 'captured-agent-standard'\n"
        ).encode()
    files["scripts/validate_task_witness.py"] = (
        "from pathlib import Path\n"
        + process_escape_source
        + "import agent_plugins_standard\n"
        "if agent_plugins_standard.MARKER != 'captured-agent-standard':\n"
        "    raise ValueError('ambient dependency selected')\n"
        "def validate_candidate_source(root, include_suite_inventory):\n"
        "    if include_suite_inventory is not True:\n"
        "        raise ValueError('suite inventory was not requested')\n"
        + "".join(f"    {mutation}\n" for mutation in mutations)
        + ("    pass\n" if not mutations else "")
        + f"    return {projection!r}\n"
    ).encode()
    for relative, raw in files.items():
        path = candidate / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    git = shutil.which("git")
    if git is None:
        raise unittest.SkipTest("Git is unavailable")
    subprocess.run([git, "init", "--quiet", str(candidate)], check=True)
    subprocess.run([git, "-C", str(candidate), "add", "--all"], check=True)
    subprocess.run(
        [
            git,
            "-C",
            str(candidate),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture candidate",
        ],
        check=True,
    )
    return candidate


def bridge_raw_files(root: Path = REPOSITORY) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for source_root in (
        root / "plugins/task-witness",
        root / "release/task-witness/migration",
    ):
        for source in source_root.rglob("*"):
            if source.is_file():
                files[source.relative_to(root).as_posix()] = source.read_bytes()
    for relative in (
        "release/task-witness/tw4-bridge-identity.json",
        "release/task-witness/tw4-bridge-provenance.json",
    ):
        files[relative] = (root / relative).read_bytes()
    return files


def platform_profile_document() -> dict[str, object]:
    return content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-platform-profile-v1",
            "target": "macos-arm64",
            "execution_environment": "native",
            "platform": {
                "system": "darwin",
                "machine": "arm64",
                "qualified_filesystem_class": "local-private-filesystem",
            },
            "passwd_user": {
                "purpose": "task-witness-disposable-qualification-v1",
                "name": "task-witness-qualification",
                "uid": 502,
                "primary_gid": 20,
                "supplementary_gids": [],
                "home": "/Users/task-witness-qualification",
                "provisioning_evidence_sha256": "1" * 64,
            },
            "native_evidence": {
                "issuer": "operator-host-audit",
                "provenance": "native-host-inspection",
                "qualification_class": "task-witness-native-host-v1",
                "evidence_sha256": "2" * 64,
                "container": False,
                "emulation": False,
            },
            "filesystem": {
                "type": "apfs",
                "evidence_sha256": "3" * 64,
                "required_semantics": [
                    "advisory-flock-open-file-description",
                    "atomic-same-directory-replace",
                    "c-utf8-locale",
                    "directory-fsync",
                    "o-cloexec",
                    "o-nofollow",
                    "owner-mode",
                    "passwd-database",
                    "process-session",
                    "signal-mask-pending",
                    "waitid-wnowait",
                ],
            },
            "system_tools": [
                {
                    "id": tool_id,
                    "invoked_path": f"/usr/bin/{name}",
                    "resolved_path": f"/usr/bin/{name}",
                    "length": index + 1,
                    "sha256": str(index + 4) * 64,
                    "uid": 0,
                    "gid": 0,
                    "mode": 365,
                }
                for index, (tool_id, name) in enumerate(
                    (
                        ("environment-clearer", "env"),
                        ("git", "git"),
                        ("posix-shell", "sh"),
                    )
                )
            ],
        }
    )


def runtime_closure_document() -> dict[str, object]:
    entries = [
        {
            "path": "/opt/task-witness/python/bin/python3.13",
            "kind": "regular-file",
            "role": "main-executable",
            "length": 123,
            "sha256": "8" * 64,
            "uid": 0,
            "gid": 0,
            "mode": 365,
        },
        {
            "path": "/opt/task-witness/python/lib",
            "kind": "directory",
            "role": "stdlib-root",
            "uid": 0,
            "gid": 0,
            "mode": 365,
        },
    ]
    entries_sha256 = hashlib.sha256(
        json.dumps(
            entries,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-runtime-closure-evidence-v1",
            "authority": {
                "supplier": "python-build-standalone",
                "provenance": "qualified-relocation",
                "qualification_class": "task-witness-cpython-closure-v1",
                "issuer": "operator-runtime-audit",
                "disposition": "qualified",
                "evidence_sha256": "9" * 64,
            },
            "main_executable": {
                "path": "/opt/task-witness/python/bin/python3.13",
                "length": 123,
                "sha256": "8" * 64,
                "uid": 0,
                "gid": 0,
                "mode": 365,
                "implementation": "cpython",
                "version": {"major": 3, "minor": 13, "micro": 15},
            },
            "closure": {
                "inventory_contract": ("task-witness-runtime-closure-inventory-v1"),
                "roots": [
                    {
                        "path": "/opt/task-witness/python",
                        "role": "runtime-root",
                        "complete_inventory": True,
                    }
                ],
                "dependency_classes": [
                    "cpython-extension-modules",
                    "cpython-stdlib",
                    "loader-shared-libraries",
                ],
                "entries": entries,
                "entries_sha256": entries_sha256,
                "entry_count": 2,
                "total_regular_file_bytes": 123,
            },
        }
    )


def suite_inventory_document() -> dict[str, object]:
    suite_ids = [
        "client-common",
        "deployment-common",
        "package-contract",
        "qualification-runner-contract",
        "task-witness-source-stage",
        "public-release-source-stage",
        "forward-update",
        "authorized-downgrade-and-manual-rollback",
        "candidate-rejection-rollback",
        "candidate-source-disappearance",
        "provider-cache-deletion-and-movement",
        "literal-rendered-shim",
        "migration-freeze5-to-bridge",
        "migration-bridge-to-tw4",
        "macos-acl",
        "linux-process-supervision",
    ]
    expected_counts = {
        "client-common": 321,
        "deployment-common": 203,
        "package-contract": 71,
        "qualification-runner-contract": 7,
        "task-witness-source-stage": 1,
        "public-release-source-stage": 1,
        "forward-update": 53,
        "authorized-downgrade-and-manual-rollback": 18,
        "candidate-rejection-rollback": 11,
        "candidate-source-disappearance": 1,
        "provider-cache-deletion-and-movement": 1,
        "literal-rendered-shim": 1,
        "migration-freeze5-to-bridge": 11,
        "migration-bridge-to-tw4": 15,
        "macos-acl": 12,
        "linux-process-supervision": 3,
    }
    entries = []
    for index, suite_id in enumerate(suite_ids):
        if index < 6:
            phase = "common"
        elif index < 14:
            phase = "portable-vertical"
        else:
            phase = "platform-vertical"
        targets = {
            "macos-acl": ["macos-arm64"],
            "linux-process-supervision": ["linux-x86_64"],
        }.get(suite_id, ["macos-arm64", "linux-x86_64"])
        entries.append(
            {
                "argv": [
                    "-I",
                    "-B",
                    "scripts/run_task_witness_qualification_suite.py",
                    "--suite",
                    suite_id,
                ],
                "executor": {"kind": "qualified-cpython"},
                "expected_count": expected_counts[suite_id],
                "expected_terminal": "passed",
                "id": suite_id,
                "phase": phase,
                "targets": targets,
            }
        )
    counts = [
        {"expected_count": entry["expected_count"], "id": entry["id"]}
        for entry in entries
    ]
    return {
        "schema_version": 1,
        "contract": "task-witness-tw4-suite-inventory-v1",
        "runtime_status": "retired-source-stage",
        "entries": entries,
        "aggregates": {
            "counts_sha256": hashlib.sha256(canonical_document(counts)).hexdigest(),
            "entries_sha256": hashlib.sha256(canonical_document(entries)).hexdigest(),
            "entry_count": 16,
            "expected_count_total": sum(entry["expected_count"] for entry in entries),
        },
    }


def rehash_suite_inventory(value: dict[str, object]) -> None:
    entries = value["entries"]
    if not isinstance(entries, list):
        raise TypeError("suite fixture entries must be a list")
    aggregates = value["aggregates"]
    if not isinstance(aggregates, dict):
        raise TypeError("suite fixture aggregates must be an object")
    counts = [
        {"expected_count": entry["expected_count"], "id": entry["id"]}
        for entry in entries
    ]
    aggregates["counts_sha256"] = hashlib.sha256(canonical_document(counts)).hexdigest()
    aggregates["entries_sha256"] = hashlib.sha256(
        canonical_document(entries)
    ).hexdigest()
    aggregates["entry_count"] = len(entries)
    aggregates["expected_count_total"] = sum(
        entry["expected_count"] for entry in entries
    )


def suite_result_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": "task-witness-tw4-suite-result-v1",
        "id": "client-common",
        "observed_count": 7,
        "terminal": "passed",
        "detail_stdout_length": 0,
        "detail_stdout_sha256": hashlib.sha256(b"").hexdigest(),
        "detail_stderr_length": 0,
        "detail_stderr_sha256": hashlib.sha256(b"").hexdigest(),
    }


def rehash_content_document(value: dict[str, object]) -> None:
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    value["content_sha256"] = hashlib.sha256(canonical_document(unsigned)).hexdigest()


def current_platform_profile_document() -> dict[str, object]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    machine = {"aarch64": "arm64", "amd64": "x86_64"}.get(machine, machine)
    target = {("darwin", "arm64"): "macos-arm64", ("linux", "x86_64"): "linux-x86_64"}[
        (system, machine)
    ]
    entry = pwd.getpwuid(os.geteuid())
    tools = []
    for index, (tool_id, invoked) in enumerate(
        (
            ("environment-clearer", shutil.which("env")),
            ("git", shutil.which("git")),
            ("posix-shell", shutil.which("sh")),
        )
    ):
        if invoked is None:
            raise AssertionError(f"test host lacks {tool_id}")
        invoked_path = Path(invoked)
        resolved = invoked_path.resolve(strict=True)
        metadata = resolved.stat()
        raw = resolved.read_bytes()
        tools.append(
            {
                "id": tool_id,
                "invoked_path": str(invoked_path),
                "resolved_path": str(resolved),
                "length": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
                "mode": stat.S_IMODE(metadata.st_mode),
            }
        )
    return content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-platform-profile-v1",
            "target": target,
            "execution_environment": "native",
            "platform": {
                "system": system,
                "machine": machine,
                "qualified_filesystem_class": "local-private-filesystem",
            },
            "passwd_user": {
                "purpose": "task-witness-disposable-qualification-v1",
                "name": entry.pw_name,
                "uid": entry.pw_uid,
                "primary_gid": entry.pw_gid,
                "supplementary_gids": sorted(set(os.getgroups())),
                "home": str(Path(entry.pw_dir)),
                "provisioning_evidence_sha256": "a" * 64,
            },
            "native_evidence": {
                "issuer": "operator-host-audit",
                "provenance": "native-host-inspection",
                "qualification_class": "task-witness-native-host-v1",
                "evidence_sha256": "b" * 64,
                "container": False,
                "emulation": False,
            },
            "filesystem": {
                "type": "test-local-filesystem",
                "evidence_sha256": "c" * 64,
                "required_semantics": [
                    "advisory-flock-open-file-description",
                    "atomic-same-directory-replace",
                    "c-utf8-locale",
                    "directory-fsync",
                    "o-cloexec",
                    "o-nofollow",
                    "owner-mode",
                    "passwd-database",
                    "process-session",
                    "signal-mask-pending",
                    "waitid-wnowait",
                ],
            },
            "system_tools": tools,
        }
    )


def runtime_closure_document_for(runtime_root: Path) -> tuple[Path, dict[str, object]]:
    runtime = runtime_root / "python3.13"
    runtime.write_bytes(b"#!/bin/sh\nexit 0\n")
    runtime.chmod(0o500)
    metadata = runtime.stat()
    raw = runtime.read_bytes()
    entry = {
        "path": str(runtime),
        "kind": "regular-file",
        "role": "main-executable",
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }
    document = content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-runtime-closure-evidence-v1",
            "authority": {
                "supplier": "test-runtime-supplier",
                "provenance": "test-runtime-provenance",
                "qualification_class": "task-witness-test-runtime-v1",
                "issuer": "test-runtime-audit",
                "disposition": "qualified",
                "evidence_sha256": "d" * 64,
            },
            "main_executable": {
                **{
                    key: value
                    for key, value in entry.items()
                    if key not in {"kind", "role"}
                },
                "implementation": "cpython",
                "version": {"major": 3, "minor": 13, "micro": 15},
            },
            "closure": {
                "inventory_contract": "task-witness-runtime-closure-inventory-v1",
                "roots": [
                    {
                        "path": str(runtime_root),
                        "role": "runtime-root",
                        "complete_inventory": True,
                    }
                ],
                "dependency_classes": [
                    "cpython-extension-modules",
                    "cpython-stdlib",
                    "loader-shared-libraries",
                ],
                "entries": [entry],
                "entries_sha256": hashlib.sha256(
                    canonical_document([entry])
                ).hexdigest(),
                "entry_count": 1,
                "total_regular_file_bytes": len(raw),
            },
        }
    )
    return runtime, document


def stat_namespace(metadata: os.stat_result, **changes: int) -> SimpleNamespace:
    values = {
        name: getattr(metadata, name)
        for name in (
            "st_ctime_ns",
            "st_dev",
            "st_gid",
            "st_ino",
            "st_mode",
            "st_mtime_ns",
            "st_nlink",
            "st_size",
            "st_uid",
        )
    }
    values.update(changes)
    return SimpleNamespace(**values)


@contextmanager
def fail_opened_leaf_close(runner, message: str):
    original_open = runner.open_absolute_path_without_symlinks
    original_close = runner.os.close
    leaf_descriptors: set[int] = set()

    def recording_open(*args, **kwargs):
        descriptor, metadata = original_open(*args, **kwargs)
        leaf_descriptors.add(descriptor)
        return descriptor, metadata

    def failing_close(descriptor: int) -> None:
        if descriptor in leaf_descriptors:
            raise OSError(message)
        original_close(descriptor)

    with (
        mock.patch.object(
            runner,
            "open_absolute_path_without_symlinks",
            side_effect=recording_open,
        ),
        mock.patch.object(runner.os, "close", side_effect=failing_close),
    ):
        try:
            yield
        finally:
            for descriptor in leaf_descriptors:
                try:
                    original_close(descriptor)
                except OSError:
                    pass


class TaskWitnessQualificationTests(unittest.TestCase):
    def invoke_suite(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return run_retired_internal_main(
            SUITE_DRIVER,
            list(arguments),
            cwd=REPOSITORY,
        )

    def invoke(
        self,
        *,
        candidate: Path | str,
        runtime: Path | str,
        runtime_evidence: Path | str,
        platform_profile: Path | str,
        receipt: Path | str,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return run_retired_internal_main(
            RUNNER,
            [
                "--candidate-root",
                str(candidate),
                "--runtime-executable",
                str(runtime),
                "--runtime-closure-evidence",
                str(runtime_evidence),
                "--platform-profile",
                str(platform_profile),
                "--receipt-output",
                str(receipt),
            ],
            function="_retired_native_main",
            text=True,
            timeout=timeout,
        )

    def run_qualification(
        self,
        root: Path,
        *,
        platform_profile: bytes,
        runtime_evidence: bytes = b"{}",
        runtime: Path | None = None,
        receipt: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        candidate = root / "candidate"
        candidate.mkdir()
        runtime = (
            Path(sys.executable).resolve(strict=True) if runtime is None else runtime
        )
        runtime_evidence_path = root / "runtime-closure-evidence.json"
        runtime_evidence_path.write_bytes(runtime_evidence)
        platform_profile_path = root / "platform-profile.json"
        platform_profile_path.write_bytes(platform_profile)
        receipt = receipt or root / "receipt.json"
        result = self.invoke(
            candidate=candidate,
            runtime=runtime,
            runtime_evidence=runtime_evidence_path,
            platform_profile=platform_profile_path,
            receipt=receipt,
        )
        return result, receipt

    def test_suite_driver_runs_only_the_nonrecursive_runner_contract(self) -> None:
        process = self.invoke_suite(
            "--suite",
            "qualification-runner-contract",
        )

        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertEqual(process.stderr, b"")
        self.assertNotEqual(process.stdout[-1:], b"\n")
        value = json.loads(process.stdout)
        self.assertEqual(load_runner_module().parse_suite_result(value), value)
        self.assertEqual(value["id"], "qualification-runner-contract")
        self.assertEqual(value["observed_count"], 7)
        self.assertEqual(value["detail_stdout_length"], 0)
        self.assertEqual(value["detail_stderr_length"], 0)
        self.assertEqual(
            value["detail_stdout_sha256"],
            hashlib.sha256(b"").hexdigest(),
        )
        self.assertEqual(
            value["detail_stderr_sha256"],
            hashlib.sha256(b"").hexdigest(),
        )

    def test_suite_driver_runs_the_closed_client_common_contract(self) -> None:
        process = self.invoke_suite(
            "--suite",
            "client-common",
        )

        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertEqual(process.stderr, b"")
        self.assertNotEqual(process.stdout[-1:], b"\n")
        value = json.loads(process.stdout)
        self.assertEqual(load_runner_module().parse_suite_result(value), value)
        self.assertEqual(value["id"], "client-common")
        self.assertEqual(value["observed_count"], 321)
        self.assertEqual(value["detail_stdout_length"], 0)
        self.assertEqual(value["detail_stderr_length"], 0)
        self.assertEqual(
            value["detail_stdout_sha256"],
            hashlib.sha256(b"").hexdigest(),
        )
        self.assertEqual(
            value["detail_stderr_sha256"],
            hashlib.sha256(b"").hexdigest(),
        )

    def test_suite_driver_runs_the_closed_deployment_common_contract(self) -> None:
        process = self.invoke_suite(
            "--suite",
            "deployment-common",
        )

        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertEqual(process.stderr, b"")
        self.assertNotEqual(process.stdout[-1:], b"\n")
        value = json.loads(process.stdout)
        self.assertEqual(load_runner_module().parse_suite_result(value), value)
        self.assertEqual(value["id"], "deployment-common")
        self.assertEqual(value["observed_count"], 203)
        self.assertEqual(value["detail_stdout_length"], 0)
        self.assertEqual(value["detail_stderr_length"], 0)
        self.assertEqual(
            value["detail_stdout_sha256"],
            hashlib.sha256(b"").hexdigest(),
        )
        self.assertEqual(
            value["detail_stderr_sha256"],
            hashlib.sha256(b"").hexdigest(),
        )

    def test_suite_driver_rejects_unavailable_or_open_dispatch(self) -> None:
        invocations = (
            (),
            ("--help",),
            ("-h",),
            ("--s", "qualification-runner-contract"),
            ("--suite=qualification-runner-contract",),
            (
                "--suite",
                "qualification-runner-contract",
                "--suite",
                "qualification-runner-contract",
            ),
            ("--suite", "unregistered-suite"),
            ("--suite", "qualification-runner-contract", "unexpected"),
        )
        for arguments in invocations:
            process = self.invoke_suite(*arguments)
            with self.subTest(arguments=arguments):
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(process.stdout, b"")
                self.assertNotIn(b"Traceback", process.stderr)

    def test_suite_driver_rejects_every_non_success_terminal(self) -> None:
        driver = load_suite_driver_module()

        class Passing(unittest.TestCase):
            def runTest(self) -> None:
                return None

        class Failing(unittest.TestCase):
            def runTest(self) -> None:
                self.fail("expected failure")

        class Erroring(unittest.TestCase):
            def runTest(self) -> None:
                raise RuntimeError("expected error")

        class Skipping(unittest.TestCase):
            @unittest.skip("expected skip")
            def runTest(self) -> None:
                return None

        class ExpectedFailure(unittest.TestCase):
            @unittest.expectedFailure
            def runTest(self) -> None:
                self.fail("expected failure")

        class UnexpectedSuccess(unittest.TestCase):
            @unittest.expectedFailure
            def runTest(self) -> None:
                return None

        cases = (
            Failing(),
            Erroring(),
            Skipping(),
            ExpectedFailure(),
            UnexpectedSuccess(),
            unittest.TestSuite((Passing(), Passing())),
        )
        selectors = ("one",)
        for case in cases:
            suite = (
                case
                if isinstance(case, unittest.TestSuite)
                else unittest.TestSuite((case,))
            )
            with (
                self.subTest(case=type(case).__name__),
                mock.patch.object(
                    driver,
                    "SUITE_SELECTORS",
                    {"qualification-runner-contract": selectors},
                ),
                mock.patch.object(
                    driver,
                    "SUITE_EXPECTED_COUNTS",
                    {"qualification-runner-contract": 1},
                ),
                mock.patch.object(
                    driver,
                    "QUALIFICATION_RUNNER_SELECTORS",
                    selectors,
                ),
                mock.patch.object(
                    driver,
                    "_qualification_runner_suite",
                    return_value=suite,
                ),
                self.assertRaisesRegex(
                    driver.SuiteError,
                    "did not reach the exact terminal",
                ),
            ):
                driver._execute_suite("qualification-runner-contract", REPOSITORY)

    def test_suite_driver_binds_bounded_underlying_output(self) -> None:
        driver = load_suite_driver_module()
        captured: dict[str, bytes] = {}
        run_with_captured_descriptors = driver._run_with_captured_descriptors

        def observe_capture(suite):
            result, stdout_raw, stderr_raw = run_with_captured_descriptors(suite)
            captured["stdout"] = stdout_raw
            captured["stderr"] = stderr_raw
            return result, stdout_raw, stderr_raw

        class DeferredStderr:
            def __del__(self) -> None:
                os.write(2, b"prior-gc-stderr")

        class Writing(unittest.TestCase):
            def runTest(self) -> None:
                gc.collect()
                os.write(1, b"out")
                os.write(2, b"err")
                subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        "import os; os.write(1,b'child-out'); os.write(2,b'child-err')",
                    ],
                    check=True,
                )
                self.assertGreaterEqual(ctypes.CDLL(None).printf(b"native-out"), 0)

        collection_was_enabled = gc.isenabled()
        gc.disable()
        deferred = DeferredStderr()
        deferred.cycle = deferred
        del deferred
        try:
            self.assertGreaterEqual(ctypes.CDLL(None).printf(b"prior-native-out"), 0)
            with (
                mock.patch.object(
                    driver,
                    "QUALIFICATION_RUNNER_SELECTORS",
                    (selectors := ("one",)),
                ),
                mock.patch.object(
                    driver,
                    "SUITE_SELECTORS",
                    {"qualification-runner-contract": selectors},
                ),
                mock.patch.object(
                    driver,
                    "SUITE_EXPECTED_COUNTS",
                    {"qualification-runner-contract": 1},
                ),
                mock.patch.object(
                    driver,
                    "_qualification_runner_suite",
                    return_value=unittest.TestSuite((Writing(),)),
                ),
                mock.patch.object(
                    driver,
                    "_run_with_captured_descriptors",
                    side_effect=observe_capture,
                ),
            ):
                value = driver._execute_suite(
                    "qualification-runner-contract",
                    REPOSITORY,
                )
        finally:
            if collection_was_enabled:
                gc.enable()
        self.assertEqual(value["detail_stdout_length"], 22)
        self.assertEqual(captured["stdout"], b"outchild-outnative-out")
        self.assertEqual(
            value["detail_stdout_sha256"],
            hashlib.sha256(b"outchild-outnative-out").hexdigest(),
        )
        self.assertEqual(value["detail_stderr_length"], 12)
        self.assertEqual(captured["stderr"], b"errchild-err")
        self.assertEqual(
            value["detail_stderr_sha256"],
            hashlib.sha256(b"errchild-err").hexdigest(),
        )

        with (
            mock.patch.object(driver, "DETAIL_MAX_BYTES", 2),
            mock.patch.object(
                driver,
                "QUALIFICATION_RUNNER_SELECTORS",
                (selectors := ("one",)),
            ),
            mock.patch.object(
                driver,
                "SUITE_SELECTORS",
                {"qualification-runner-contract": selectors},
            ),
            mock.patch.object(
                driver,
                "SUITE_EXPECTED_COUNTS",
                {"qualification-runner-contract": 1},
            ),
            mock.patch.object(
                driver,
                "_qualification_runner_suite",
                return_value=unittest.TestSuite((Writing(),)),
            ),
            self.assertRaisesRegex(driver.SuiteError, "fixed detail bound"),
        ):
            driver._execute_suite("qualification-runner-contract", REPOSITORY)

    def test_suite_capture_is_disjoint_from_test_owned_fd3_and_fd4(self) -> None:
        driver = load_suite_driver_module()

        class RebindingProtocolDescriptors(unittest.TestCase):
            def runTest(self) -> None:
                self.assertGreaterEqual(sys.stdout.fileno(), 64)
                self.assertGreaterEqual(sys.stderr.fileno(), 64)
                backups: dict[int, int | None] = {}
                for target in (3, 4):
                    try:
                        backups[target] = fcntl.fcntl(
                            target,
                            fcntl.F_DUPFD_CLOEXEC,
                            64,
                        )
                    except OSError:
                        backups[target] = None
                    low_source = os.open(os.devnull, os.O_RDONLY)
                    try:
                        source = fcntl.fcntl(
                            low_source,
                            fcntl.F_DUPFD_CLOEXEC,
                            64,
                        )
                    finally:
                        os.close(low_source)
                    try:
                        os.dup2(source, target)
                    finally:
                        os.close(source)
                print("stdout while protocol descriptors are rebound", end="")
                print(
                    "stderr while protocol descriptors are rebound",
                    end="",
                    file=sys.stderr,
                )
                for target, backup in backups.items():
                    if backup is None:
                        os.close(target)
                    else:
                        os.dup2(backup, target)
                        os.close(backup)

        result, stdout_raw, stderr_raw = driver._run_with_captured_descriptors(
            unittest.TestSuite((RebindingProtocolDescriptors(),))
        )

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(stdout_raw, b"stdout while protocol descriptors are rebound")
        self.assertEqual(stderr_raw, b"stderr while protocol descriptors are rebound")

    def test_suite_capture_restoration_failure_matrix_is_exhaustive(self) -> None:
        driver = load_suite_driver_module()
        for primary in (False, True):
            for target in (1, 2):
                for restore_then_error in (False, True):
                    with self.subTest(
                        primary=primary,
                        target=target,
                        restore_then_error=restore_then_error,
                    ):
                        self._assert_capture_restore_failure(
                            driver,
                            primary=primary,
                            target=target,
                            restore_then_error=restore_then_error,
                        )

    def test_suite_capture_persistent_restoration_failure_is_bounded(self) -> None:
        child_source = """
import ctypes
import fcntl
import importlib.util
import os
import sys
from unittest import mock

driver_path = sys.argv[1]
target = int(sys.argv[2])
primary = sys.argv[3] == "primary"
spec = importlib.util.spec_from_file_location("qualification_driver", driver_path)
if spec is None or spec.loader is None:
    raise SystemExit(91)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
before = tuple(sorted(int(name) for name in os.listdir("/dev/fd")))
real_dup2 = driver.os.dup2
real_close = driver.os.close
outer_backup = fcntl.fcntl(target, fcntl.F_DUPFD_CLOEXEC, 256)
phase = {"cleanup": False}
target_close_calls = {"count": 0}


class CaptureAbort(BaseException):
    pass


class FailingNativeDup2:
    argtypes = None
    restype = None

    def __call__(self, _source, _target):
        ctypes.set_errno(5)
        return -1


class FailingNativeLibrary:
    dup2 = FailingNativeDup2()


abort = CaptureAbort("persistent restoration primary abort")


class ControlledSuite:
    def run(self, _result):
        phase["cleanup"] = True
        if primary:
            raise abort


def fail_selected_restore(source, destination):
    if phase["cleanup"] and destination == target:
        raise OSError(5, "persistent capture restoration failure")
    real_dup2(source, destination)


def record_close(descriptor):
    if phase["cleanup"] and descriptor == target:
        target_close_calls["count"] += 1
    real_close(descriptor)


observed = None
try:
    with (
        mock.patch.object(driver, "_flush_native_stdio", return_value=None),
        mock.patch.object(driver.os, "dup2", side_effect=fail_selected_restore),
        mock.patch.object(driver.os, "close", side_effect=record_close),
        mock.patch.object(
            driver.ctypes,
            "CDLL",
            return_value=FailingNativeLibrary(),
        ),
    ):
        try:
            driver._run_with_captured_descriptors(ControlledSuite())
        except BaseException as error:
            observed = error
finally:
    real_dup2(outer_backup, target)
    real_close(outer_backup)

after = tuple(sorted(int(name) for name in os.listdir("/dev/fd")))
if after != before:
    raise SystemExit(92)
if target_close_calls["count"] != 1:
    raise SystemExit(97)
if primary:
    if observed is not abort:
        raise SystemExit(93)
    if not any("cleanup also failed" in note for note in abort.__notes__):
        raise SystemExit(94)
else:
    if not isinstance(observed, driver.SuiteError):
        raise SystemExit(95)
    if str(observed) != "suite process descriptors cannot be restored":
        raise SystemExit(96)
"""
        for primary in (False, True):
            for target in (1, 2):
                with self.subTest(primary=primary, target=target):
                    process = subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            "-B",
                            "-c",
                            child_source,
                            str(SUITE_DRIVER),
                            str(target),
                            "primary" if primary else "no-primary",
                        ],
                        cwd=REPOSITORY,
                        stdin=subprocess.DEVNULL,
                        capture_output=True,
                        timeout=3,
                        check=False,
                    )
                    self.assertEqual(
                        process.returncode,
                        0,
                        (process.stdout, process.stderr),
                    )

    def _assert_capture_restore_failure(
        self,
        driver,
        *,
        primary: bool,
        target: int,
        restore_then_error: bool,
    ) -> None:
        real_dup2 = driver.os.dup2
        real_close = driver.os.close
        outer_stdout = fcntl.fcntl(1, fcntl.F_DUPFD_CLOEXEC, 256)
        outer_stderr = fcntl.fcntl(2, fcntl.F_DUPFD_CLOEXEC, 256)
        phase = {"cleanup": False}
        injected = 0
        successful_restores = 0

        class CaptureAbort(BaseException):
            pass

        abort = CaptureAbort("injected suite abort")

        class ControlledSuite:
            def run(self, _result):
                phase["cleanup"] = True
                if primary:
                    raise abort

        expected_identity = {descriptor: os.fstat(descriptor) for descriptor in (1, 2)}
        before = tuple(sorted(int(name) for name in os.listdir("/dev/fd")))

        def fail_selected_restore(source: int, destination: int) -> None:
            nonlocal injected, successful_restores
            if phase["cleanup"] and destination == target and injected == 0:
                injected += 1
                if restore_then_error:
                    real_dup2(source, destination)
                    successful_restores += 1
                raise OSError(5, "injected capture restoration failure")
            real_dup2(source, destination)
            if phase["cleanup"] and destination == target:
                successful_restores += 1

        try:
            with mock.patch.object(
                driver.os,
                "dup2",
                side_effect=fail_selected_restore,
            ):
                if primary:
                    with self.assertRaises(CaptureAbort) as raised:
                        driver._run_with_captured_descriptors(ControlledSuite())
                    self.assertIs(raised.exception, abort)
                    self.assertTrue(
                        any("cleanup also failed" in note for note in abort.__notes__)
                    )
                else:
                    with self.assertRaisesRegex(
                        driver.SuiteError,
                        "^suite process descriptors cannot be restored$",
                    ):
                        driver._run_with_captured_descriptors(ControlledSuite())

            self.assertEqual(injected, 1)
            self.assertEqual(successful_restores, 1)
            self.assertEqual(
                {descriptor: os.fstat(descriptor) for descriptor in (1, 2)},
                expected_identity,
            )
            self.assertEqual(
                tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
                before,
            )
        finally:
            real_dup2(outer_stdout, 1)
            real_dup2(outer_stderr, 2)
            real_close(outer_stdout)
            real_close(outer_stderr)

    def test_suite_capture_close_failure_matrix_is_exhaustive(self) -> None:
        driver = load_suite_driver_module()
        for primary in (False, True):
            for close_ordinal in (1, 2):
                for close_then_error in (False, True):
                    with self.subTest(
                        primary=primary,
                        close_ordinal=close_ordinal,
                        close_then_error=close_then_error,
                    ):
                        self._assert_capture_close_failure(
                            driver,
                            primary=primary,
                            close_ordinal=close_ordinal,
                            close_then_error=close_then_error,
                        )

    def _assert_capture_close_failure(
        self,
        driver,
        *,
        primary: bool,
        close_ordinal: int,
        close_then_error: bool,
    ) -> None:
        real_close = driver.os.close
        phase = {"cleanup": False}
        logical_descriptors: list[int] = []
        attempts: dict[int, int] = {}
        successful_closes: dict[int, int] = {}
        injected_descriptor: int | None = None

        class CaptureAbort(BaseException):
            pass

        abort = CaptureAbort("injected suite abort")

        class ControlledSuite:
            def run(self, _result):
                phase["cleanup"] = True
                if primary:
                    raise abort

        before = tuple(sorted(int(name) for name in os.listdir("/dev/fd")))

        def fail_selected_close(descriptor: int) -> None:
            nonlocal injected_descriptor
            if phase["cleanup"] and descriptor >= 64:
                if descriptor not in logical_descriptors:
                    logical_descriptors.append(descriptor)
                attempts[descriptor] = attempts.get(descriptor, 0) + 1
                if (
                    logical_descriptors.index(descriptor) + 1 == close_ordinal
                    and attempts[descriptor] == 1
                ):
                    injected_descriptor = descriptor
                    if close_then_error:
                        real_close(descriptor)
                        successful_closes[descriptor] = 1
                    raise OSError(5, "injected capture close failure")
                real_close(descriptor)
                successful_closes[descriptor] = successful_closes.get(descriptor, 0) + 1
                return
            real_close(descriptor)

        with mock.patch.object(
            driver.os,
            "close",
            side_effect=fail_selected_close,
        ):
            if primary:
                with self.assertRaises(CaptureAbort) as raised:
                    driver._run_with_captured_descriptors(ControlledSuite())
                self.assertIs(raised.exception, abort)
                self.assertTrue(
                    any("cleanup also failed" in note for note in abort.__notes__)
                )
            else:
                with self.assertRaisesRegex(
                    driver.SuiteError,
                    "^suite process descriptors cannot be restored$",
                ):
                    driver._run_with_captured_descriptors(ControlledSuite())

        self.assertEqual(len(logical_descriptors), 2)
        self.assertEqual(injected_descriptor, logical_descriptors[close_ordinal - 1])
        self.assertEqual(successful_closes, dict.fromkeys(logical_descriptors, 1))
        self.assertEqual(
            attempts[injected_descriptor],
            1 if close_then_error else 2,
        )
        self.assertEqual(
            tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
            before,
        )

    def test_suite_capture_closes_partial_high_descriptor_setup(self) -> None:
        driver = load_suite_driver_module()
        real_fcntl = driver.fcntl.fcntl

        for failure_ordinal in range(1, 9):
            with self.subTest(failure_ordinal=failure_ordinal):
                before = tuple(sorted(int(name) for name in os.listdir("/dev/fd")))
                duplicate_calls = 0
                created_high_descriptors: list[int] = []
                closed_descriptors: list[int] = []
                real_close = driver.os.close

                def fail_selected_duplicate(
                    *args,
                    selected_ordinal=failure_ordinal,
                    created_descriptors=created_high_descriptors,
                    **kwargs,
                ):
                    nonlocal duplicate_calls
                    duplicate_calls += 1
                    if duplicate_calls == selected_ordinal:
                        raise OSError(24, "injected descriptor exhaustion")
                    descriptor = real_fcntl(*args, **kwargs)
                    created_descriptors.append(descriptor)
                    return descriptor

                def record_close(
                    descriptor: int,
                    closed=closed_descriptors,
                    close=real_close,
                ) -> None:
                    closed.append(descriptor)
                    close(descriptor)

                with (
                    mock.patch.object(
                        driver.fcntl,
                        "fcntl",
                        side_effect=fail_selected_duplicate,
                    ),
                    mock.patch.object(
                        driver.os,
                        "close",
                        side_effect=record_close,
                    ),
                    self.assertRaisesRegex(
                        driver.SuiteError,
                        "^suite process descriptors cannot be captured$",
                    ),
                ):
                    driver._run_with_captured_descriptors(unittest.TestSuite())

                self.assertEqual(duplicate_calls, failure_ordinal)
                self.assertEqual(
                    len(created_high_descriptors),
                    failure_ordinal - 1,
                )
                for descriptor in created_high_descriptors:
                    self.assertEqual(closed_descriptors.count(descriptor), 1)
                self.assertEqual(
                    tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
                    before,
                )

    def test_suite_capture_preserves_base_exception_during_setup(self) -> None:
        driver = load_suite_driver_module()
        before = tuple(sorted(int(name) for name in os.listdir("/dev/fd")))
        real_fcntl = driver.fcntl.fcntl
        duplicate_calls = 0

        class SetupAbort(BaseException):
            pass

        abort = SetupAbort("injected setup abort")
        cleanup_failures = 0
        real_close = driver.os.close

        def abort_fourth_duplicate(*args, **kwargs):
            nonlocal duplicate_calls
            duplicate_calls += 1
            if duplicate_calls == 4:
                raise abort
            return real_fcntl(*args, **kwargs)

        def fail_once_during_cleanup(descriptor: int) -> None:
            nonlocal cleanup_failures
            real_close(descriptor)
            if duplicate_calls == 4 and descriptor >= 64 and cleanup_failures == 0:
                cleanup_failures += 1
                raise RuntimeError("injected cleanup failure")

        with (
            mock.patch.object(
                driver.fcntl,
                "fcntl",
                side_effect=abort_fourth_duplicate,
            ),
            mock.patch.object(
                driver.os,
                "close",
                side_effect=fail_once_during_cleanup,
            ),
            self.assertRaises(SetupAbort) as raised,
        ):
            driver._run_with_captured_descriptors(unittest.TestSuite())

        self.assertIs(raised.exception, abort)
        self.assertEqual(duplicate_calls, 4)
        self.assertEqual(cleanup_failures, 1)
        self.assertEqual(
            tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
            before,
        )

    def test_suite_capture_closes_high_descriptors_if_low_retirement_fails(
        self,
    ) -> None:
        driver = load_suite_driver_module()
        real_fcntl = driver.fcntl.fcntl
        real_close = driver.os.close

        for failure_ordinal in (1, 2):
            for close_before_error in (False, True):
                with self.subTest(
                    failure_ordinal=failure_ordinal,
                    close_before_error=close_before_error,
                ):
                    self._assert_low_retirement_failure_cleanup(
                        driver,
                        real_fcntl,
                        real_close,
                        failure_ordinal,
                        close_before_error,
                    )

    def _assert_low_retirement_failure_cleanup(
        self,
        driver,
        real_fcntl,
        real_close,
        failure_ordinal: int,
        close_before_error: bool,
    ) -> None:
        before = tuple(sorted(int(name) for name in os.listdir("/dev/fd")))
        retirement_calls = 0
        created_high_descriptors: list[int] = []
        closed_descriptors: list[int] = []

        def record_duplicate(
            *args,
            created=created_high_descriptors,
            **kwargs,
        ):
            descriptor = real_fcntl(*args, **kwargs)
            created.append(descriptor)
            return descriptor

        def fail_selected_retirement(
            descriptor: int,
            selected_ordinal=failure_ordinal,
            closed=closed_descriptors,
        ) -> None:
            nonlocal retirement_calls
            if 0 <= descriptor < 64 and retirement_calls < 2:
                retirement_calls += 1
                if retirement_calls == selected_ordinal:
                    if close_before_error:
                        closed.append(descriptor)
                        real_close(descriptor)
                    raise OSError(24, "injected low descriptor close failure")
            closed.append(descriptor)
            real_close(descriptor)

        with (
            mock.patch.object(
                driver.fcntl,
                "fcntl",
                side_effect=record_duplicate,
            ),
            mock.patch.object(
                driver.os,
                "close",
                side_effect=fail_selected_retirement,
            ),
            self.assertRaisesRegex(
                driver.SuiteError,
                "^suite process descriptors cannot be captured$",
            ),
        ):
            driver._run_with_captured_descriptors(unittest.TestSuite())

        self.assertEqual(retirement_calls, 2)
        self.assertEqual(len(created_high_descriptors), 2)
        for descriptor in created_high_descriptors:
            self.assertEqual(closed_descriptors.count(descriptor), 1)
        if close_before_error:
            self.assertEqual(
                len(closed_descriptors),
                len(set(closed_descriptors)),
                closed_descriptors,
            )
        self.assertEqual(
            tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
            before,
        )

    def test_client_common_selector_table_is_closed_and_exact(self) -> None:
        driver = load_suite_driver_module()

        self.assertEqual(len(driver.CLIENT_COMMON_TESTS), 10)
        self.assertEqual(len(driver.CLIENT_COMMON_SELECTORS), 321)
        self.assertEqual(len(set(driver.CLIENT_COMMON_SELECTORS)), 321)
        self.assertEqual(driver.SUITE_EXPECTED_COUNTS["client-common"], 321)
        self.assertIs(
            driver.SUITE_SELECTORS["client-common"],
            driver.CLIENT_COMMON_SELECTORS,
        )
        forbidden = {
            "test_bundle_rejects_macos_allow_acl_before_launcher",
            "test_bundle_accepts_macos_deny_only_acl",
            "test_bundle_rejects_inherited_macos_allow_acl_before_launcher",
            "test_protected_nodes_reject_macos_allow_acl",
            "test_protected_nodes_reject_inherited_macos_allow_acl",
            "test_protected_nodes_accept_macos_deny_only_acl",
            "test_non_darwin_deadline_allocation_retry_forces_and_reaps_real_child",
            "test_group_kill_retry_removes_real_same_session_descendants",
            "test_linux_threaded_descendant_quiesces_before_leader_reap",
            "test_rendered_shim_pins_the_client_process_and_preserves_passed_descriptors",
        }
        self.assertTrue(
            all(
                selector.rsplit(".", 1)[-1] not in forbidden
                for selector in driver.CLIENT_COMMON_SELECTORS
            )
        )
        self.assertIn(
            "tests.plugins.task_witness_client.test_invocation_profile."
            "InvocationProfileTests."
            "test_launcher_child_receives_fixed_profile_without_high_fd_leak",
            driver.CLIENT_COMMON_SELECTORS,
        )

    def test_client_common_rejects_selector_and_count_drift(self) -> None:
        driver = load_suite_driver_module()
        changed = (*driver.CLIENT_COMMON_SELECTORS[:-1], "changed.selector")
        with (
            mock.patch.object(
                driver,
                "SUITE_SELECTORS",
                {**driver.SUITE_SELECTORS, "client-common": changed},
            ),
            self.assertRaisesRegex(driver.SuiteError, "selector table drift"),
        ):
            driver._execute_suite("client-common", REPOSITORY)
        with (
            mock.patch.object(
                driver,
                "SUITE_EXPECTED_COUNTS",
                {**driver.SUITE_EXPECTED_COUNTS, "client-common": 320},
            ),
            self.assertRaisesRegex(driver.SuiteError, "selector count drift"),
        ):
            driver._execute_suite("client-common", REPOSITORY)

    def test_deployment_common_selector_table_is_closed_and_exact(self) -> None:
        driver = load_suite_driver_module()

        self.assertEqual(len(driver.DEPLOYMENT_COMMON_TESTS), 14)
        self.assertEqual(len(driver.DEPLOYMENT_COMMON_SELECTORS), 203)
        self.assertEqual(len(set(driver.DEPLOYMENT_COMMON_SELECTORS)), 203)
        self.assertEqual(driver.SUITE_EXPECTED_COUNTS["deployment-common"], 203)
        self.assertIs(
            driver.SUITE_SELECTORS["deployment-common"],
            driver.DEPLOYMENT_COMMON_SELECTORS,
        )
        self.assertEqual(
            hashlib.sha256(
                json.dumps(
                    driver.DEPLOYMENT_COMMON_SELECTORS,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "0ae69284ec6a896a382e88201368147a71b60da5c43d42d045f12441002edcfa",
        )
        forbidden = {
            "test_candidate_failure_restores_exact_absence_without_rollback_smoke",
            "test_public_candidate_rejection_restores_prior_complete_control_set",
            "test_public_recovery_uses_stage_after_candidate_source_disappears",
            "test_first_install_rejects_a_permissive_root_acl_without_mutation",
            "test_public_bridge_activation_commits_exact_tw4_transition",
        }
        self.assertTrue(
            all(
                selector.rsplit(".", 1)[-1] not in forbidden
                for selector in driver.DEPLOYMENT_COMMON_SELECTORS
            )
        )

    def test_package_contract_selector_table_is_closed_and_exact(self) -> None:
        driver = load_suite_driver_module()

        self.assertEqual(len(driver.PACKAGE_CONTRACT_TESTS), 1)
        self.assertEqual(len(driver.PACKAGE_CONTRACT_SELECTORS), 71)
        self.assertEqual(len(set(driver.PACKAGE_CONTRACT_SELECTORS)), 71)
        self.assertEqual(driver.SUITE_EXPECTED_COUNTS["package-contract"], 71)
        self.assertIs(
            driver.SUITE_SELECTORS["package-contract"],
            driver.PACKAGE_CONTRACT_SELECTORS,
        )
        self.assertEqual(
            hashlib.sha256(
                json.dumps(
                    driver.PACKAGE_CONTRACT_SELECTORS,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "fa4f889ab2b62237f2645cedb24b92007113d0bac94c0496057fe2da98bf9ce6",
        )

    def test_package_contract_loader_uses_only_fixed_captured_methods(self) -> None:
        driver = load_suite_driver_module()
        before_path = list(sys.path)
        before_modules = set(sys.modules)

        with driver._package_contract_suite(REPOSITORY) as suite:
            self.assertEqual(suite.countTestCases(), 71)
            self.assertEqual(
                tuple(test.id() for test in suite),
                driver.PACKAGE_CONTRACT_SELECTORS,
            )

        self.assertEqual(sys.path, before_path)
        self.assertEqual(set(sys.modules), before_modules)

    def test_package_contract_cli_emits_the_current_canonical_result(
        self,
    ) -> None:
        process = self.invoke_suite("--suite", "package-contract")

        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertEqual(process.stderr, b"")
        self.assertNotEqual(process.stdout[-1:], b"\n")
        value = json.loads(process.stdout)
        self.assertEqual(load_runner_module().parse_suite_result(value), value)
        expected = suite_result_document()
        expected.update(
            {
                "id": "package-contract",
                "observed_count": 71,
                "detail_stdout_length": 45,
                "detail_stdout_sha256": (
                    "26a6be57e0448efb627b1641ae11880dabe9e55daf7acad0ec8f302690597681"
                ),
            }
        )
        self.assertEqual(value, expected)

    def test_task_witness_source_stage_owns_one_exact_direct_command(self) -> None:
        driver = load_suite_driver_module()

        self.assertEqual(
            driver.TASK_WITNESS_SOURCE_STAGE_SELECTORS,
            ("direct.task-witness-source-stage",),
        )
        self.assertIs(
            driver.SUITE_SELECTORS["task-witness-source-stage"],
            driver.TASK_WITNESS_SOURCE_STAGE_SELECTORS,
        )
        self.assertEqual(driver.SUITE_EXPECTED_COUNTS["task-witness-source-stage"], 1)
        self.assertEqual(
            driver._task_witness_source_stage_argv(REPOSITORY),
            [
                sys.executable,
                "-I",
                "-B",
                "scripts/validate_task_witness.py",
                str(REPOSITORY),
                "--source-stage",
            ],
        )

    def test_direct_scenario_child_capture_is_bounded_and_exact(self) -> None:
        driver = load_suite_driver_module()
        command = [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import os; os.write(1,b'out'); os.write(2,b'err')",
        ]

        self.assertEqual(
            driver._run_bounded_child(command, REPOSITORY),
            (0, b"out", b"err"),
        )
        with (
            mock.patch.object(driver, "DETAIL_MAX_BYTES", 2),
            self.assertRaisesRegex(driver.SuiteError, "fixed detail bound"),
        ):
            driver._run_bounded_child(command, REPOSITORY)

    def test_protocol_descriptor_restoration_preserves_a_primary_abort(self) -> None:
        driver = load_suite_driver_module()
        outer_backups: dict[int, int | None] = {}
        try:
            for target in (3, 4):
                try:
                    outer_backups[target] = fcntl.fcntl(
                        target,
                        fcntl.F_DUPFD_CLOEXEC,
                        64,
                    )
                except OSError:
                    outer_backups[target] = None
                source = os.open(os.devnull, os.O_RDONLY)
                try:
                    os.dup2(source, target)
                finally:
                    if source != target:
                        os.close(source)
            expected = {target: os.fstat(target) for target in (3, 4)}
            before = tuple(sorted(int(name) for name in os.listdir("/dev/fd")))
            real_dup2 = driver.os.dup2
            dup2_targets: list[int] = []

            class ScenarioAbort(BaseException):
                pass

            abort = ScenarioAbort("primary scenario abort")

            def fail_first_restore(source: int, target: int) -> None:
                dup2_targets.append(target)
                if len(dup2_targets) == 3:
                    raise OSError(5, "injected first restoration failure")
                real_dup2(source, target)

            with (
                mock.patch.object(
                    driver,
                    "_run_bounded_child",
                    side_effect=abort,
                ),
                mock.patch.object(
                    driver.os,
                    "dup2",
                    side_effect=fail_first_restore,
                ),
                self.assertRaises(ScenarioAbort) as raised,
            ):
                driver._run_with_protocol_descriptors(
                    ["unused"],
                    REPOSITORY,
                    {},
                )

            self.assertIs(raised.exception, abort)
            self.assertEqual(dup2_targets, [3, 4, 3, 4])
            self.assertEqual(
                {target: os.fstat(target) for target in (3, 4)},
                expected,
            )
            self.assertEqual(
                tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
                before,
            )
        finally:
            for target, backup in outer_backups.items():
                if backup is None:
                    with contextlib.suppress(OSError):
                        os.close(target)
                else:
                    os.dup2(backup, target)
                    os.close(backup)

    def test_task_witness_source_stage_requires_its_exact_success_contract(
        self,
    ) -> None:
        driver = load_suite_driver_module()
        success = b"Task Witness source-stage validation passed\n"
        with mock.patch.object(
            driver,
            "_run_bounded_child",
            return_value=(0, success, b""),
        ) as run_child:
            value = driver._execute_suite("task-witness-source-stage", REPOSITORY)

        run_child.assert_called_once_with(
            driver._task_witness_source_stage_argv(REPOSITORY),
            REPOSITORY,
        )
        self.assertEqual(value["observed_count"], 1)
        self.assertEqual(value["detail_stdout_length"], 0)
        self.assertEqual(value["detail_stderr_length"], 0)

        for outcome in (
            (1, success, b""),
            (0, success + b"unexpected", b""),
            (0, success, b"unexpected"),
        ):
            with (
                self.subTest(outcome=outcome),
                mock.patch.object(
                    driver,
                    "_run_bounded_child",
                    return_value=outcome,
                ),
                self.assertRaisesRegex(
                    driver.SuiteError,
                    "did not reach the exact terminal",
                ),
            ):
                driver._execute_suite("task-witness-source-stage", REPOSITORY)

    def test_task_witness_source_stage_cli_emits_the_current_canonical_result(
        self,
    ) -> None:
        process = self.invoke_suite("--suite", "task-witness-source-stage")

        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertEqual(process.stderr, b"")
        self.assertNotEqual(process.stdout[-1:], b"\n")
        value = json.loads(process.stdout)
        self.assertEqual(load_runner_module().parse_suite_result(value), value)
        expected = suite_result_document()
        expected.update({"id": "task-witness-source-stage", "observed_count": 1})
        self.assertEqual(value, expected)

    def test_public_release_source_stage_owns_one_exact_direct_command(self) -> None:
        driver = load_suite_driver_module()

        self.assertEqual(
            driver.PUBLIC_RELEASE_SOURCE_STAGE_SELECTORS,
            ("direct.public-release-source-stage",),
        )
        self.assertIs(
            driver.SUITE_SELECTORS["public-release-source-stage"],
            driver.PUBLIC_RELEASE_SOURCE_STAGE_SELECTORS,
        )
        self.assertEqual(driver.SUITE_EXPECTED_COUNTS["public-release-source-stage"], 1)
        self.assertEqual(
            driver._public_release_source_stage_argv(REPOSITORY),
            [
                sys.executable,
                "-I",
                "-B",
                "scripts/supervise_prepared_release_validation.py",
                "source-stage",
                str(REPOSITORY),
            ],
        )

    def test_public_release_source_stage_requires_canonical_identity_success(
        self,
    ) -> None:
        driver = load_suite_driver_module()
        success = b'{\n  "plugins": {},\n  "schema_version": 1\n}\n'
        with mock.patch.object(
            driver,
            "_run_bounded_child",
            return_value=(0, success, b""),
        ) as run_child:
            value = driver._execute_suite("public-release-source-stage", REPOSITORY)

        run_child.assert_called_once_with(
            driver._public_release_source_stage_argv(REPOSITORY),
            REPOSITORY,
        )
        self.assertEqual(value["observed_count"], 1)
        self.assertEqual(value["detail_stdout_length"], 0)
        self.assertEqual(value["detail_stderr_length"], 0)

        invalid_outcomes = (
            (1, success, b""),
            (0, b'{"plugins":{},"schema_version":1}\n', b""),
            (
                0,
                b'{\n  "plugins": {},\n  "plugins": {},\n  "schema_version": 1\n}\n',
                b"",
            ),
            (0, b'{\n  "plugins": {},\n  "schema_version": true\n}\n', b""),
            (0, success, b"unexpected"),
        )
        for outcome in invalid_outcomes:
            with (
                self.subTest(outcome=outcome),
                mock.patch.object(
                    driver,
                    "_run_bounded_child",
                    return_value=outcome,
                ),
                self.assertRaisesRegex(
                    driver.SuiteError,
                    "did not reach the exact terminal",
                ),
            ):
                driver._execute_suite("public-release-source-stage", REPOSITORY)

    def test_public_release_source_stage_cli_emits_the_current_canonical_result(
        self,
    ) -> None:
        process = self.invoke_suite("--suite", "public-release-source-stage")

        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertEqual(process.stderr, b"")
        self.assertNotEqual(process.stdout[-1:], b"\n")
        value = json.loads(process.stdout)
        self.assertEqual(load_runner_module().parse_suite_result(value), value)
        expected = suite_result_document()
        expected.update({"id": "public-release-source-stage", "observed_count": 1})
        self.assertEqual(value, expected)

    def test_literal_rendered_shim_owns_one_direct_fixed_scenario(self) -> None:
        driver = load_suite_driver_module()

        self.assertEqual(
            driver.LITERAL_RENDERED_SHIM_SELECTORS,
            ("direct.literal-rendered-shim",),
        )
        self.assertIs(
            driver.SUITE_SELECTORS["literal-rendered-shim"],
            driver.LITERAL_RENDERED_SHIM_SELECTORS,
        )
        self.assertEqual(driver.SUITE_EXPECTED_COUNTS["literal-rendered-shim"], 1)

    def test_literal_rendered_shim_publishes_without_candidate_execution(
        self,
    ) -> None:
        driver = load_suite_driver_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            home = root / "home"
            workspace = root / "workspace"
            home.mkdir(mode=0o700)
            workspace.mkdir(mode=0o700)
            with (
                mock.patch.object(
                    driver.pwd,
                    "getpwuid",
                    return_value=SimpleNamespace(pw_dir=str(home)),
                ),
                mock.patch.dict(
                    driver.os.environ,
                    {"TASK_WITNESS_QUALIFICATION_WORKSPACE": str(workspace)},
                    clear=False,
                ),
            ):
                value = driver._execute_suite("literal-rendered-shim", REPOSITORY)

            sidecar = workspace / "literal-rendered-shim-observation.json"
            raw = sidecar.read_bytes()
            observed = json.loads(raw)
            self.assertEqual(
                set(observed),
                {
                    "client",
                    "contract",
                    "runtime_executable_path",
                    "shim",
                    "template",
                },
            )
            self.assertEqual(
                observed["contract"],
                "task-witness-rendered-shim-observation-v1",
            )
            self.assertEqual(observed["runtime_executable_path"], sys.executable)
            self.assertEqual(value["observed_count"], 1)
            self.assertEqual(value["detail_stdout_length"], len(raw))
            self.assertEqual(
                value["detail_stdout_sha256"],
                hashlib.sha256(raw).hexdigest(),
            )
            self.assertEqual(value["detail_stderr_length"], 0)

            installed_root = home / ".local/libexec/task-witness"
            installed_client = installed_root / "client/task_witness_client.py"
            installed_shim = installed_root / "task-witness"
            self.assertEqual(
                installed_client.read_bytes(),
                (
                    REPOSITORY / "plugins/task-witness/client/task_witness_client.py"
                ).read_bytes(),
            )
            self.assertEqual(stat.S_IMODE(installed_client.stat().st_mode), 0o500)
            self.assertEqual(stat.S_IMODE(installed_shim.stat().st_mode), 0o500)
            self.assertFalse(
                (workspace / "literal-rendered-shim-process-observation.json").exists()
            )

    def test_literal_rendered_shim_cleanup_is_descriptor_relative(self) -> None:
        driver = load_suite_driver_module()
        real_unlink = driver.os.unlink
        unlink_calls: list[tuple[object, int]] = []

        def require_directory_descriptor(path, *, dir_fd=None):
            self.assertIsNotNone(dir_fd)
            unlink_calls.append((path, dir_fd))
            real_unlink(path, dir_fd=dir_fd)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            home = root / "home"
            workspace = root / "workspace"
            home.mkdir(mode=0o700)
            workspace.mkdir(mode=0o700)
            with (
                mock.patch.object(
                    driver.pwd,
                    "getpwuid",
                    return_value=SimpleNamespace(pw_dir=str(home)),
                ),
                mock.patch.dict(
                    driver.os.environ,
                    {"TASK_WITNESS_QUALIFICATION_WORKSPACE": str(workspace)},
                    clear=False,
                ),
                mock.patch.object(
                    driver.Path,
                    "unlink",
                    side_effect=AssertionError("path-based cleanup is forbidden"),
                ),
                mock.patch.object(
                    driver.os,
                    "unlink",
                    side_effect=require_directory_descriptor,
                ),
            ):
                driver._execute_suite("literal-rendered-shim", REPOSITORY)

        self.assertEqual(len(unlink_calls), 4)

    def test_descriptor_relative_cleanup_rejects_a_symlink_target(self) -> None:
        driver = load_suite_driver_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            private = root / "private"
            private.mkdir(mode=0o700)
            outside = root / "outside"
            outside.write_bytes(b"preserve")
            link = private / "observation.json"
            link.symlink_to(outside)
            descriptor = os.open(
                private,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY,
            )
            try:
                with self.assertRaisesRegex(driver.SuiteError, "is unsafe"):
                    driver._unlink_owned_regular_at(
                        descriptor,
                        "observation.json",
                        expected_mode=None,
                    )
            finally:
                os.close(descriptor)

            self.assertTrue(link.is_symlink())
            self.assertEqual(outside.read_bytes(), b"preserve")

    def test_portable_deployment_vertical_selector_tables_are_closed_and_exact(
        self,
    ) -> None:
        driver = load_suite_driver_module()
        expected = {
            "forward-update": (
                "FORWARD_UPDATE_SELECTORS",
                53,
                "f56d39caff0650ffa2aac8a78df604cd6eaa73a65c35205c74e2b7aad5651def",
            ),
            "authorized-downgrade-and-manual-rollback": (
                "AUTHORIZED_DOWNGRADE_AND_MANUAL_ROLLBACK_SELECTORS",
                18,
                "7ae8ce0bf91b07fdc4b2e2f1b680f16583a1662074fbb3e52f83090b2a24e40f",
            ),
            "candidate-rejection-rollback": (
                "CANDIDATE_REJECTION_ROLLBACK_SELECTORS",
                11,
                "64c49de295d02a63d72d892317e931b2a0a200083be6e5a5520352e409a65a7c",
            ),
            "candidate-source-disappearance": (
                "CANDIDATE_SOURCE_DISAPPEARANCE_SELECTORS",
                1,
                "c4c92cc74e82788963d0e4675042197b9c286b1af47138a731618cf3ee8334e2",
            ),
            "provider-cache-deletion-and-movement": (
                "PROVIDER_CACHE_DELETION_AND_MOVEMENT_SELECTORS",
                1,
                "8c1b7415d062cc067ea444313922ecbe366828c92a0de12d96049371589310f9",
            ),
            "migration-freeze5-to-bridge": (
                "MIGRATION_FREEZE5_TO_BRIDGE_SELECTORS",
                11,
                "b71bbba6cc27e9baddca49de2291c82b7e9871998635b056983aeeabc5fea673",
            ),
            "migration-bridge-to-tw4": (
                "MIGRATION_BRIDGE_TO_TW4_SELECTORS",
                15,
                "e6fd71ba3a880cf08247664303184c123c34d2b71be82d0a55f27f4e53da0165",
            ),
        }
        observed_portable: list[str] = []
        for suite_id, (attribute, count, digest) in expected.items():
            with self.subTest(suite_id=suite_id):
                selectors = getattr(driver, attribute)
                self.assertIs(driver.SUITE_SELECTORS[suite_id], selectors)
                self.assertEqual(driver.SUITE_EXPECTED_COUNTS[suite_id], count)
                self.assertEqual(len(selectors), count)
                self.assertEqual(len(set(selectors)), count)
                self.assertEqual(
                    hashlib.sha256(
                        json.dumps(selectors, separators=(",", ":")).encode()
                    ).hexdigest(),
                    digest,
                )
                observed_portable.extend(selectors)

        self.assertEqual(len(observed_portable), 110)
        self.assertEqual(len(set(observed_portable)), 110)
        all_selectors = [
            selector
            for selectors in driver.SUITE_SELECTORS.values()
            for selector in selectors
        ]
        self.assertEqual(len(all_selectors), len(set(all_selectors)))

    def test_platform_vertical_selector_tables_are_closed_and_exact(self) -> None:
        driver = load_suite_driver_module()
        expected = {
            "macos-acl": (
                "MACOS_ACL_SELECTORS",
                12,
                "e341aed39e64d3d39ad9fed1cc8231073be4c8af7f6b3e65faeb7ab2a29b2fcc",
            ),
            "linux-process-supervision": (
                "LINUX_PROCESS_SUPERVISION_SELECTORS",
                3,
                "220434382d9d51ef9bbe02841302f4ab3efd9942fc00a387005cdcac1260ff02",
            ),
        }
        observed_platform: list[str] = []
        for suite_id, (attribute, count, digest) in expected.items():
            with self.subTest(suite_id=suite_id):
                groups, registered = driver.PLATFORM_VERTICALS[suite_id]
                selectors = getattr(driver, attribute)
                self.assertTrue(groups)
                self.assertIs(registered, selectors)
                self.assertIs(driver.SUITE_SELECTORS[suite_id], selectors)
                self.assertEqual(driver.SUITE_EXPECTED_COUNTS[suite_id], count)
                self.assertEqual(len(selectors), count)
                self.assertEqual(len(set(selectors)), count)
                self.assertEqual(
                    hashlib.sha256(
                        json.dumps(selectors, separators=(",", ":")).encode()
                    ).hexdigest(),
                    digest,
                )
                observed_platform.extend(selectors)

        self.assertEqual(len(observed_platform), 15)
        self.assertEqual(len(set(observed_platform)), 15)
        all_selectors = [
            selector
            for selectors in driver.SUITE_SELECTORS.values()
            for selector in selectors
        ]
        self.assertEqual(len(all_selectors), len(set(all_selectors)))

    def test_platform_vertical_preflight_normalizes_machine_aliases(self) -> None:
        driver = load_suite_driver_module()
        cases = (
            ("Darwin", "aarch64", ("darwin", "arm64")),
            ("Linux", "amd64", ("linux", "x86_64")),
        )
        for system, machine, expected in cases:
            with (
                self.subTest(system=system, machine=machine),
                mock.patch.object(driver.host_platform, "system", return_value=system),
                mock.patch.object(
                    driver.host_platform, "machine", return_value=machine
                ),
            ):
                self.assertEqual(driver.normalized_host_platform(), expected)

    def test_platform_vertical_wrong_host_rejects_before_loading_or_running(
        self,
    ) -> None:
        driver = load_suite_driver_module()
        before_path = list(sys.path)
        before_modules = set(sys.modules)
        with (
            mock.patch.object(driver.host_platform, "system", return_value="Darwin"),
            mock.patch.object(driver.host_platform, "machine", return_value="arm64"),
            mock.patch.object(
                driver,
                "_load_fixed_client_test_module",
                side_effect=AssertionError("module loaded before host preflight"),
            ) as load_module,
            mock.patch.object(
                driver,
                "_run_with_captured_descriptors",
                side_effect=AssertionError("suite ran before host preflight"),
            ) as run_suite,
            self.assertRaisesRegex(
                driver.SuiteError,
                "^linux-process-supervision requires linux-x86_64 host$",
            ),
        ):
            driver._execute_suite("linux-process-supervision", REPOSITORY)

        load_module.assert_not_called()
        run_suite.assert_not_called()
        self.assertEqual(sys.path, before_path)
        self.assertEqual(set(sys.modules), before_modules)

    @unittest.skipUnless(
        platform.system().lower() == "darwin"
        and {"aarch64": "arm64"}.get(
            platform.machine().lower(), platform.machine().lower()
        )
        == "arm64",
        "macOS arm64 qualification host required",
    )
    def test_public_macos_acl_vertical_emits_a_canonical_result(self) -> None:
        process = self.invoke_suite("--suite", "macos-acl")

        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertEqual(process.stderr, b"")
        self.assertNotEqual(process.stdout[-1:], b"\n")
        value = json.loads(process.stdout)
        self.assertEqual(load_runner_module().parse_suite_result(value), value)
        self.assertEqual(value["id"], "macos-acl")
        self.assertEqual(value["observed_count"], 12)
        self.assertEqual(value["detail_stdout_length"], 0)
        self.assertEqual(value["detail_stdout_sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(value["detail_stderr_length"], 0)
        self.assertEqual(value["detail_stderr_sha256"], hashlib.sha256(b"").hexdigest())

    @unittest.skipUnless(
        platform.system().lower() == "darwin",
        "Darwin host required for the public wrong-host assertion",
    )
    def test_public_linux_process_supervision_rejects_the_wrong_host(self) -> None:
        process = self.invoke_suite("--suite", "linux-process-supervision")

        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(process.stdout, b"")
        self.assertEqual(
            process.stderr,
            b"qualification suite failed: linux-process-supervision requires "
            b"linux-x86_64 host\n",
        )

    def test_public_portable_deployment_verticals_emit_canonical_results(
        self,
    ) -> None:
        expected_counts = {
            "forward-update": 53,
            "authorized-downgrade-and-manual-rollback": 18,
            "candidate-rejection-rollback": 11,
            "candidate-source-disappearance": 1,
            "provider-cache-deletion-and-movement": 1,
            "migration-freeze5-to-bridge": 11,
            "migration-bridge-to-tw4": 15,
        }
        for suite_id, expected_count in expected_counts.items():
            process = self.invoke_suite("--suite", suite_id)
            with self.subTest(suite_id=suite_id):
                self.assertEqual(process.returncode, 0, process.stderr.decode())
                self.assertEqual(process.stderr, b"")
                self.assertNotEqual(process.stdout[-1:], b"\n")
                value = json.loads(process.stdout)
                self.assertEqual(load_runner_module().parse_suite_result(value), value)
                self.assertEqual(value["id"], suite_id)
                self.assertEqual(value["observed_count"], expected_count)
                self.assertEqual(value["detail_stdout_length"], 0)
                self.assertEqual(
                    value["detail_stdout_sha256"],
                    hashlib.sha256(b"").hexdigest(),
                )
                self.assertEqual(value["detail_stderr_length"], 0)
                self.assertEqual(
                    value["detail_stderr_sha256"],
                    hashlib.sha256(b"").hexdigest(),
                )

    def test_deployment_common_rejects_selector_count_and_collision_drift(self) -> None:
        driver = load_suite_driver_module()
        changed = (*driver.DEPLOYMENT_COMMON_SELECTORS[:-1], "changed.selector")
        with (
            mock.patch.object(
                driver,
                "SUITE_SELECTORS",
                {**driver.SUITE_SELECTORS, "deployment-common": changed},
            ),
            self.assertRaisesRegex(driver.SuiteError, "selector table drift"),
        ):
            driver._execute_suite("deployment-common", REPOSITORY)
        with (
            mock.patch.object(
                driver,
                "SUITE_EXPECTED_COUNTS",
                {**driver.SUITE_EXPECTED_COUNTS, "deployment-common": 202},
            ),
            self.assertRaisesRegex(driver.SuiteError, "selector count drift"),
        ):
            driver._execute_suite("deployment-common", REPOSITORY)
        with (
            mock.patch.object(
                driver,
                "SUITE_SELECTORS",
                {
                    **driver.SUITE_SELECTORS,
                    "other-suite": (driver.DEPLOYMENT_COMMON_SELECTORS[0],),
                },
            ),
            self.assertRaisesRegex(driver.SuiteError, "selector collision"),
        ):
            driver._execute_suite("deployment-common", REPOSITORY)

    def test_deployment_common_rejects_module_path_and_collision_drift(self) -> None:
        driver = load_suite_driver_module()
        relative, module_name, _case_name, _methods = driver.DEPLOYMENT_COMMON_TESTS[0]
        with self.assertRaisesRegex(driver.SuiteError, "path disagrees"):
            driver._fixed_candidate_test_path(
                REPOSITORY,
                relative,
                module_name + "_changed",
                "deployment-common",
            )
        collision = ModuleType(module_name)
        with (
            mock.patch.dict(sys.modules, {module_name: collision}),
            self.assertRaisesRegex(driver.SuiteError, "module collision"),
        ):
            driver._load_fixed_client_test_module(
                REPOSITORY,
                relative,
                module_name,
                "deployment-common",
            )

    def test_client_common_rejects_module_path_and_collision_drift(self) -> None:
        driver = load_suite_driver_module()
        relative, module_name, _case_name, _methods = driver.CLIENT_COMMON_TESTS[0]
        with self.assertRaisesRegex(driver.SuiteError, "path disagrees"):
            driver._fixed_candidate_test_path(
                REPOSITORY,
                relative,
                module_name + "_changed",
            )
        collision = ModuleType(module_name)
        with (
            mock.patch.dict(sys.modules, {module_name: collision}),
            self.assertRaisesRegex(driver.SuiteError, "module collision"),
        ):
            driver._load_fixed_client_test_module(
                REPOSITORY,
                relative,
                module_name,
            )

    def test_fixed_suite_loader_executes_only_descriptor_captured_bytes(self) -> None:
        driver = load_suite_driver_module()
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            package = repository / "tests" / "plugins" / "task_witness_client"
            package.mkdir(parents=True)
            relative = package.relative_to(repository) / "test_race.py"
            path = repository / relative
            path.write_text("MARKER = 1\n", encoding="utf-8")
            replacement = path.with_suffix(".replacement")
            replacement.write_text("MARKER = 2\n", encoding="utf-8")
            real_read = driver.os.read
            replaced = False

            def replace_after_capture(*args, **kwargs):
                nonlocal replaced
                result = real_read(*args, **kwargs)
                if result == b"" and not replaced:
                    replaced = True
                    os.replace(replacement, path)
                return result

            with (
                mock.patch.object(
                    driver.os,
                    "read",
                    side_effect=replace_after_capture,
                ),
                self.assertRaisesRegex(driver.SuiteError, "identity drift"),
            ):
                driver._load_fixed_client_test_module(
                    repository,
                    relative,
                    "tests.plugins.task_witness_client.test_race",
                )

    def test_cli_requires_the_exact_ordered_five_input_invocation(self) -> None:
        runner = load_runner_module()
        values = (
            "/candidate",
            "/runtime/python",
            "/evidence/runtime.json",
            "/evidence/platform.json",
            "/evidence/receipt.json",
        )
        flags = (
            "--candidate-root",
            "--runtime-executable",
            "--runtime-closure-evidence",
            "--platform-profile",
            "--receipt-output",
        )
        canonical = [
            token for pair in zip(flags, values, strict=True) for token in pair
        ]

        parsed = runner.parse_invocation(canonical)

        self.assertEqual(
            tuple(
                str(getattr(parsed, field))
                for field in (
                    "candidate_root",
                    "runtime_executable",
                    "runtime_closure_evidence",
                    "platform_profile",
                    "receipt_output",
                )
            ),
            values,
        )
        invalid = (
            canonical[:-1],
            [*canonical, "/extra"],
            [canonical[2], canonical[3], canonical[0], canonical[1], *canonical[4:]],
            [*canonical[:2], canonical[0], canonical[1], *canonical[2:]],
            [f"{flags[0]}={values[0]}", *canonical[2:]],
            ["--candidate", values[0], *canonical[2:]],
            ["--help"],
        )
        for arguments in invalid:
            with (
                self.subTest(arguments=arguments),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "arguments are invalid",
                ),
            ):
                runner.parse_invocation(arguments)

        invalid_paths = (
            "relative",
            "//candidate",
            "/candidate/../alternate",
            "/candidate/./nested",
            "/candidate//nested",
            "/candidate/",
            "/candidate\ud800",
            "/" + ("x" * 4096),
        )
        for invalid_path in invalid_paths:
            arguments = canonical.copy()
            arguments[1] = invalid_path
            with (
                self.subTest(path=invalid_path),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "candidate root",
                ),
            ):
                runner.parse_invocation(arguments)

    def test_document_absolute_paths_enforce_the_retained_utf8_byte_cap(self) -> None:
        runner = load_runner_module()
        boundary = "/" + ("x" * (runner.MAX_PATH_BYTES - 1))

        self.assertEqual(
            runner.absolute_path_text(boundary, "retained path"),
            boundary,
        )
        for value in (
            boundary + "x",
            "/" + ("\N{SNOWMAN}" * ((runner.MAX_PATH_BYTES // 3) + 1)),
        ):
            with (
                self.subTest(value_length=len(value.encode("utf-8"))),
                self.assertRaisesRegex(runner.QualificationError, "is invalid"),
            ):
                runner.absolute_path_text(value, "retained path")

    def test_canonical_json_codec_enforces_uint64_float_surrogate_and_caps(
        self,
    ) -> None:
        runner = load_runner_module()
        maximum_integer = (1 << 64) - 1
        value = {"nested": [{"maximum": maximum_integer}], "zero": 0}
        raw = b'{"nested":[{"maximum":18446744073709551615}],"zero":0}'

        self.assertEqual(
            runner.canonical_json_bytes(
                value,
                maximum=len(raw),
                label="fixture document",
            ),
            raw,
        )
        self.assertEqual(
            runner.decode_canonical_json(
                raw,
                maximum=len(raw),
                expected_root=dict,
                label="fixture document",
            ),
            value,
        )
        invalid_values = (
            {"value": -1},
            {"value": maximum_integer + 1},
            {"value": 0.0},
            {"nested": [{"value": 1.5}]},
            {"value": "\ud800"},
            {"\udfff": "value"},
            {1: "non-string key"},
        )
        for invalid in invalid_values:
            with (
                self.subTest(encoded=invalid),
                self.assertRaises(runner.QualificationError),
            ):
                runner.canonical_json_bytes(
                    invalid,
                    maximum=runner.MAX_JSON_BYTES,
                    label="fixture document",
                )

        invalid_raw = (
            b'{"value":-1}',
            b'{"value":18446744073709551616}',
            b'{"value":0.0}',
            b'{"value":1e0}',
            b'{"value":NaN}',
            b'{"value":"\\ud800"}',
            b'{"nested":{"key":1,"key":2}}',
            b'{"zero":0,"nested":[{"maximum":18446744073709551615}]}',
        )
        for invalid in invalid_raw:
            with (
                self.subTest(decoded=invalid),
                self.assertRaises(runner.QualificationError),
            ):
                runner.decode_canonical_json(
                    invalid,
                    maximum=runner.MAX_JSON_BYTES,
                    expected_root=dict,
                    label="fixture document",
                )

        for operation in (
            lambda: runner.canonical_json_bytes(
                value,
                maximum=len(raw) - 1,
                label="fixture document",
            ),
            lambda: runner.decode_canonical_json(
                raw,
                maximum=len(raw) - 1,
                expected_root=dict,
                label="fixture document",
            ),
            lambda: runner.decode_canonical_json(
                b"[]",
                maximum=2,
                expected_root=dict,
                label="fixture document",
            ),
        ):
            with self.assertRaises(runner.QualificationError):
                operation()

    def test_recorded_git_uses_the_exact_candidate_safe_directory(self) -> None:
        runner = load_runner_module()
        candidate = Path("/qualified/candidate")
        git = Path("/qualified/tools/git")
        with mock.patch.object(
            runner,
            "_bounded_process",
            return_value=(0, b"result\n", b""),
        ) as bounded:
            result = runner.run_recorded_git(
                git,
                candidate,
                "rev-parse",
                "--show-toplevel",
            )

        self.assertEqual(result, b"result\n")
        argv = bounded.call_args.args[0]
        self.assertEqual(argv[0:2], [str(git), "--no-replace-objects"])
        self.assertIn(
            ["-c", f"safe.directory={candidate}"],
            [argv[index : index + 2] for index in range(len(argv) - 1)],
        )
        self.assertIn(
            ["-c", "core.attributesFile=/dev/null"],
            [argv[index : index + 2] for index in range(len(argv) - 1)],
        )
        self.assertEqual(
            argv[-4:],
            ["-C", str(candidate), "rev-parse", "--show-toplevel"],
        )
        environment = bounded.call_args.kwargs["env"]
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(environment["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(environment["GIT_CONFIG_SYSTEM"], "/dev/null")
        self.assertEqual(environment["GIT_ATTR_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_PAGER"], "/usr/bin/false")
        self.assertEqual(environment["PAGER"], "/usr/bin/false")

    def test_candidate_cleanliness_rejects_execution_driving_git_configuration(
        self,
    ) -> None:
        runner = load_runner_module()
        git = Path(shutil.which("git") or "").resolve(strict=True)
        with tempfile.TemporaryDirectory() as directory:
            candidate = create_synthetic_candidate(Path(directory).resolve())
            subprocess.run(
                [
                    str(git),
                    "-C",
                    str(candidate),
                    "config",
                    "filter.fixture.process",
                    "/bin/false",
                ],
                check=True,
            )
            with (
                mock.patch.object(runner, "require_paths_immutable"),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "execution-driving configuration",
                ),
            ):
                runner.observe_candidate(
                    candidate,
                    git,
                    runtime_executable=Path(sys.executable).resolve(strict=True),
                )

        with tempfile.TemporaryDirectory() as directory:
            candidate = create_synthetic_candidate(Path(directory).resolve())
            marker = candidate.parent / "filter-ran"
            attributes = candidate.parent / "external-attributes"
            attributes.write_text("*.json filter=fixture\n", encoding="utf-8")
            for arguments in (
                ("config", "extensions.worktreeConfig", "true"),
                ("config", "--worktree", "core.attributesFile", str(attributes)),
                (
                    "config",
                    "--worktree",
                    "filter.fixture.clean",
                    f"/bin/sh -c 'touch {marker}; cat'",
                ),
            ):
                subprocess.run(
                    [str(git), "-C", str(candidate), *arguments],
                    check=True,
                )
            with (
                mock.patch.object(runner, "require_paths_immutable"),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "execution-driving configuration",
                ),
            ):
                runner.observe_candidate(
                    candidate,
                    git,
                    runtime_executable=Path(sys.executable).resolve(strict=True),
                )
            self.assertFalse(marker.exists())

        with tempfile.TemporaryDirectory() as directory:
            candidate = create_synthetic_candidate(Path(directory).resolve())
            nested = candidate / "plugins/task-witness/runtime/.gitattributes"
            nested.write_text("* filter=fixture\n", encoding="utf-8")
            subprocess.run(
                [str(git), "-C", str(candidate), "add", str(nested)], check=True
            )
            subprocess.run(
                [
                    str(git),
                    "-C",
                    str(candidate),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "attributes",
                ],
                check=True,
            )
            with (
                mock.patch.object(runner, "require_paths_immutable"),
                self.assertRaisesRegex(runner.QualificationError, "attributes"),
            ):
                runner.observe_candidate(
                    candidate,
                    git,
                    runtime_executable=Path(sys.executable).resolve(strict=True),
                )

    def test_candidate_closure_rejects_before_retained_bytes_exceed_the_cap(
        self,
    ) -> None:
        runner = load_runner_module()
        with (
            mock.patch.object(
                runner,
                "MAX_CANDIDATE_RETAINED_BYTES",
                1,
            ),
            tempfile.TemporaryDirectory() as directory,
        ):
            candidate = create_synthetic_candidate(Path(directory).resolve())
            git = Path(shutil.which("git") or "").resolve(strict=True)
            with (
                mock.patch.object(runner, "require_paths_immutable"),
                mock.patch.object(runner, "_git_blob", wraps=runner._git_blob) as blob,
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "retained byte limit",
                ),
            ):
                runner.observe_candidate(
                    candidate,
                    git,
                    runtime_executable=Path(sys.executable).resolve(strict=True),
                )
            blob.assert_not_called()

    def test_candidate_observer_derives_the_exact_git_tree_and_release_closure(
        self,
    ) -> None:
        runner = load_runner_module()
        git = Path(shutil.which("git") or "").resolve(strict=True)
        with tempfile.TemporaryDirectory() as directory:
            candidate = create_synthetic_candidate(Path(directory).resolve())
            ambient = sys.modules.get("agent_plugins_standard")
            sys.modules["agent_plugins_standard"] = SimpleNamespace(
                MARKER="ambient-poison"
            )
            try:
                with (
                    mock.patch.object(runner, "require_paths_immutable") as immutable,
                    mock.patch.object(
                        runner,
                        "run_recorded_git",
                        wraps=runner.run_recorded_git,
                    ) as recorded_git,
                ):
                    observed = runner.observe_candidate(
                        candidate,
                        git,
                        runtime_executable=Path(sys.executable).resolve(strict=True),
                    )
            finally:
                if ambient is None:
                    sys.modules.pop("agent_plugins_standard", None)
                else:
                    sys.modules["agent_plugins_standard"] = ambient

            self.assertFalse(
                any(
                    call.args[2:3] == ("status",)
                    for call in recorded_git.call_args_list
                )
            )

            self.assertEqual(
                set(observed),
                {"candidate", "bridge_history", "suite_inventory"},
            )
            candidate_value = observed["candidate"]
            self.assertEqual(
                candidate_value["contract"],
                "task-witness-tw4-candidate-observation-v1",
            )
            self.assertEqual(candidate_value["root_path"], str(candidate))
            self.assertEqual(
                candidate_value["worktree"],
                {"tracked": "clean", "untracked": "none"},
            )
            identity = candidate_value["qualification_candidate"]
            commit = subprocess.run(
                [str(git), "-C", str(candidate), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            tree = subprocess.run(
                [str(git), "-C", str(candidate), "rev-parse", "HEAD^{tree}"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(identity["commit_sha1"], commit)
            self.assertEqual(identity["tree_sha1"], tree)
            inventory_raw = (
                candidate / "release/task-witness/tw4-suite-inventory.json"
            ).read_bytes()
            self.assertEqual(
                identity["suite_inventory_sha256"],
                hashlib.sha256(inventory_raw).hexdigest(),
            )
            closure = candidate_value["candidate_closure"]
            self.assertEqual(
                closure["source_shape_sha256"],
                hashlib.sha256(
                    (
                        candidate / "release/task-witness/source-shape-review.json"
                    ).read_bytes()
                ).hexdigest(),
            )
            self.assertGreater(closure["entry_count"], 18)
            self.assertEqual(
                observed["bridge_history"]["bridge_history"]["bridge_identity_sha256"],
                hashlib.sha256(
                    (
                        candidate / "release/task-witness/tw4-bridge-identity.json"
                    ).read_bytes()
                ).hexdigest(),
            )
            self.assertEqual(
                observed["suite_inventory"]["suite_inventory"]["entry_count"],
                16,
            )
            immutable_paths = {
                path for call in immutable.call_args_list for path in call.args[0]
            }
            self.assertIn(candidate, immutable_paths)
            self.assertTrue(any(path.name == "objects" for path in immutable_paths))

    def test_candidate_observer_rejects_hidden_or_mutated_checkout_state(self) -> None:
        runner = load_runner_module()
        git = Path(shutil.which("git") or "").resolve(strict=True)

        def git_call(candidate: Path, *arguments: str) -> None:
            subprocess.run(
                [str(git), "-C", str(candidate), *arguments],
                check=True,
                capture_output=True,
            )

        cases = ("untracked", "ignored", "assume-unchanged", "skip-worktree", "sparse")
        for case in cases:
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory() as directory,
            ):
                candidate = create_synthetic_candidate(Path(directory).resolve())
                if case == "untracked":
                    (candidate / "untracked.txt").write_text(
                        "drift\n", encoding="utf-8"
                    )
                elif case == "ignored":
                    (candidate / "ignored").write_text("drift\n", encoding="utf-8")
                elif case == "assume-unchanged":
                    path = "plugins/task-witness/plugin.json"
                    git_call(candidate, "update-index", "--assume-unchanged", path)
                    (candidate / path).write_text("changed\n", encoding="utf-8")
                elif case == "skip-worktree":
                    git_call(
                        candidate,
                        "update-index",
                        "--skip-worktree",
                        "plugins/task-witness/plugin.json",
                    )
                else:
                    git_call(candidate, "config", "core.sparseCheckout", "true")
                with (
                    mock.patch.object(runner, "require_paths_immutable"),
                    self.assertRaises(runner.QualificationError),
                ):
                    runner.observe_candidate(
                        candidate,
                        git,
                        runtime_executable=Path(sys.executable).resolve(strict=True),
                    )

        with tempfile.TemporaryDirectory() as directory:
            candidate = create_synthetic_candidate(Path(directory).resolve())
            plugin = candidate / "plugins/task-witness/plugin.json"
            outside = candidate / "outside.txt"
            outside.write_bytes(plugin.read_bytes())
            plugin.unlink()
            os.link(outside, plugin)
            with (
                mock.patch.object(runner, "require_paths_immutable"),
                self.assertRaisesRegex(runner.QualificationError, "hard-link alias"),
            ):
                runner.observe_candidate(
                    candidate,
                    git,
                    runtime_executable=Path(sys.executable).resolve(strict=True),
                )

    def test_candidate_observer_rechecks_captured_validator_after_execution(
        self,
    ) -> None:
        runner = load_runner_module()
        git = Path(shutil.which("git") or "").resolve(strict=True)
        with tempfile.TemporaryDirectory() as directory:
            candidate = create_synthetic_candidate(
                Path(directory).resolve(),
                mutate_validator=True,
            )
            with (
                mock.patch.object(runner, "require_paths_immutable"),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "binding changed",
                ),
            ):
                runner.observe_candidate(
                    candidate,
                    git,
                    runtime_executable=Path(sys.executable).resolve(strict=True),
                )

        with tempfile.TemporaryDirectory() as directory:
            candidate = create_synthetic_candidate(
                Path(directory).resolve(),
                validator_projection={"anything": 1},
            )
            with (
                mock.patch.object(runner, "require_paths_immutable"),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "bridge history",
                ),
            ):
                runner.observe_candidate(
                    candidate,
                    git,
                    runtime_executable=Path(sys.executable).resolve(strict=True),
                )

        def compare_bindings(
            paths: set[Path],
            label: str,
            *,
            expected_bindings=None,
        ) -> None:
            if expected_bindings is None:
                return
            for path in paths:
                if runner.stable_stat_binding(path.lstat()) != expected_bindings[path]:
                    raise runner.QualificationError(f"{label} immutable entry changed")

        with tempfile.TemporaryDirectory() as directory:
            candidate = create_synthetic_candidate(
                Path(directory).resolve(),
                mutate_administration=True,
            )
            with (
                mock.patch.object(
                    runner,
                    "require_paths_immutable",
                    side_effect=compare_bindings,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "administration.*changed",
                ),
            ):
                runner.observe_candidate(
                    candidate,
                    git,
                    runtime_executable=Path(sys.executable).resolve(strict=True),
                )

    def test_candidate_bridge_outer_projection_rejects_schema_and_structure_drift(
        self,
    ) -> None:
        runner = load_runner_module()
        raw_files = bridge_raw_files()
        identity_raw = raw_files["release/task-witness/tw4-bridge-identity.json"]
        provenance_raw = raw_files["release/task-witness/tw4-bridge-provenance.json"]
        identity = json.loads(identity_raw)
        projection = {
            "bridge_identity_sha256": hashlib.sha256(identity_raw).hexdigest(),
            "bridge_provenance_sha256": hashlib.sha256(provenance_raw).hexdigest(),
            "freeze5": identity["freeze5"],
            "bridge": identity["bridge"],
        }

        self.assertEqual(
            runner.validate_bridge_history_evidence(raw_files, projection),
            projection,
        )

        boolean_identity = dict(identity)
        boolean_identity["schema_version"] = True
        unsigned = {
            key: value
            for key, value in boolean_identity.items()
            if key != "content_sha256"
        }
        boolean_raw = (
            json.dumps(
                content_document(unsigned),
                indent=2,
            ).encode()
            + b"\n"
        )
        with self.assertRaisesRegex(runner.QualificationError, "contract"):
            runner.validate_bridge_history_evidence(
                {
                    **raw_files,
                    "release/task-witness/tw4-bridge-identity.json": boolean_raw,
                },
                projection,
            )

        provenance = json.loads(provenance_raw)
        objects = runner._decode_bridge_provenance_objects(provenance)
        freeze_tree_oid = identity["freeze5"]["tree_sha1"]
        freeze_tree = objects[freeze_tree_oid][1]
        root_entries = runner._bridge_tree_entries(freeze_tree, "fixture root")
        stable_oid = next(oid for _mode, name, oid in root_entries if name != "plugins")
        replacement_oid = ("0" if stable_oid[0] != "0" else "1") + stable_oid[1:]
        tampered_tree = freeze_tree.replace(
            bytes.fromhex(stable_oid),
            bytes.fromhex(replacement_oid),
            1,
        )
        tampered_objects = {
            **objects,
            freeze_tree_oid: ("tree", tampered_tree),
        }
        with (
            mock.patch.object(
                runner,
                "_decode_bridge_provenance_objects",
                return_value=tampered_objects,
            ),
            self.assertRaisesRegex(runner.QualificationError, "root transition"),
        ):
            runner.validate_bridge_history_evidence(raw_files, projection)

        freeze_commit_oid = identity["freeze5"]["commit_sha1"]
        freeze_commit = objects[freeze_commit_oid][1]
        duplicate_tree = b"tree " + freeze_tree_oid.encode() + b"\n"
        tampered_commit = freeze_commit.replace(
            b"\n\n",
            b"\n" + duplicate_tree + b"\n",
            1,
        )
        tampered_objects = {
            **objects,
            freeze_commit_oid: ("commit", tampered_commit),
        }
        with (
            mock.patch.object(
                runner,
                "_decode_bridge_provenance_objects",
                return_value=tampered_objects,
            ),
            self.assertRaisesRegex(runner.QualificationError, "commit tree"),
        ):
            runner.validate_bridge_history_evidence(raw_files, projection)

    def test_candidate_validator_runs_in_a_bounded_isolated_runtime_child(
        self,
    ) -> None:
        runner = load_runner_module()
        candidate = Path("/qualified/candidate")
        runtime = Path("/qualified/runtime/python")
        raw_files = {
            runner.CANDIDATE_AGENT_STANDARD: b"MARKER = 1\n",
            runner.CANDIDATE_VALIDATOR: b"raise SystemExit(99)\n",
        }
        bindings = {
            relative: {
                "path": str(candidate / relative),
                "stat": {},
                "length": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            for relative, raw in raw_files.items()
        }
        projection = {
            "bridge_identity_sha256": "1" * 64,
            "bridge_provenance_sha256": "2" * 64,
            "freeze5": {},
            "bridge": {},
        }
        with (
            mock.patch.object(
                runner,
                "_bounded_process",
                return_value=(0, canonical_document(projection), b""),
            ) as bounded,
            mock.patch.object(runner, "_capture_candidate_source") as capture,
            mock.patch.object(runner, "_recheck_captured_candidate_source"),
            mock.patch.object(runner, "close_descriptor"),
            mock.patch.object(
                runner,
                "parse_bridge_history_projection",
                return_value=projection,
            ),
        ):
            capture.side_effect = ((41, (1,) * 9), (42, (2,) * 9))
            observed = runner.run_captured_candidate_validator(
                candidate,
                runtime,
                raw_files,
                bindings,
            )

        self.assertEqual(observed, projection)
        argv = bounded.call_args.args[0]
        self.assertEqual(argv[:3], [str(runtime), "-I", "-B"])
        self.assertEqual(argv[3:5], ["-c", runner.CANDIDATE_VALIDATOR_BOOTSTRAP])
        self.assertNotIn(str(candidate / runner.CANDIDATE_VALIDATOR), argv)
        self.assertNotIn(str(candidate / runner.CANDIDATE_AGENT_STANDARD), argv)
        environment = bounded.call_args.kwargs["env"]
        self.assertEqual(environment["PYTHONHASHSEED"], "0")
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
        self.assertNotIn("PYTHONPATH", environment)

    def test_candidate_validator_seals_process_creation_before_candidate_execution(
        self,
    ) -> None:
        runner = load_runner_module()
        git = Path(shutil.which("git") or "").resolve(strict=True)
        with tempfile.TemporaryDirectory() as directory:
            candidate = create_synthetic_candidate(
                Path(directory).resolve(),
                attempt_process_escape=True,
            )
            with mock.patch.object(runner, "require_paths_immutable"):
                observed = runner.observe_candidate(
                    candidate,
                    git,
                    runtime_executable=Path(sys.executable).resolve(strict=True),
                )
        self.assertEqual(
            observed["candidate"]["contract"],
            "task-witness-tw4-candidate-observation-v1",
        )

    def test_bounded_process_times_out_while_a_child_refuses_stdin(self) -> None:
        runner = load_runner_module()
        runtime = Path(sys.executable).resolve(strict=True)
        with tempfile.TemporaryDirectory() as directory:
            process_id_path = Path(directory) / "child-pid"
            source = (
                "import os, pathlib, sys, time\n"
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), "
                "encoding='ascii')\n"
                "time.sleep(2)\n"
            )
            descriptors_before = tuple(
                sorted(int(name) for name in os.listdir("/dev/fd"))
            )
            started = time.monotonic()

            with self.assertRaisesRegex(runner.QualificationError, "timed out"):
                runner._bounded_process(
                    [
                        str(runtime),
                        "-I",
                        "-B",
                        "-c",
                        source,
                        str(process_id_path),
                    ],
                    env={"PATH": "/usr/bin:/bin"},
                    stdin=b"x" * (2 * 1024 * 1024),
                    stdout_maximum=128,
                    stderr_maximum=128,
                    label="non-reading child fixture",
                    timeout_seconds=0.1,
                    own_process_group=True,
                )

            elapsed = time.monotonic() - started
            process_id = int(process_id_path.read_text(encoding="ascii"))
            self.assertLess(elapsed, 1.5)
            with self.assertRaises(ProcessLookupError):
                os.kill(process_id, 0)
            with self.assertRaises(ProcessLookupError):
                os.killpg(process_id, 0)
            self.assertEqual(
                tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
                descriptors_before,
            )

    def test_bounded_process_drains_stdout_while_sending_stdin(self) -> None:
        runner = load_runner_module()
        runtime = Path(sys.executable).resolve(strict=True)
        input_raw = b"x" * (2 * 1024 * 1024)
        output_prefix = b"y" * (2 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as directory:
            process_id_path = Path(directory) / "child-pid"
            source = (
                "import os, pathlib, sys\n"
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), "
                "encoding='ascii')\n"
                "sys.stdout.buffer.write(b'y' * (2 * 1024 * 1024))\n"
                "sys.stdout.buffer.flush()\n"
                "data = sys.stdin.buffer.read()\n"
                "sys.stdout.buffer.write(b'\\n' + str(len(data)).encode('ascii') "
                "+ b'\\n')\n"
                "sys.stdout.buffer.flush()\n"
            )
            descriptors_before = tuple(
                sorted(int(name) for name in os.listdir("/dev/fd"))
            )

            status, stdout, stderr = runner._bounded_process(
                [
                    str(runtime),
                    "-I",
                    "-B",
                    "-c",
                    source,
                    str(process_id_path),
                ],
                env={"PATH": "/usr/bin:/bin"},
                stdin=input_raw,
                stdout_maximum=len(output_prefix) + 32,
                stderr_maximum=128,
                label="cross-pipe child fixture",
                timeout_seconds=3,
                own_process_group=True,
            )

            process_id = int(process_id_path.read_text(encoding="ascii"))
            self.assertEqual(status, 0)
            self.assertEqual(stderr, b"")
            self.assertEqual(stdout, output_prefix + b"\n2097152\n")
            with self.assertRaises(ProcessLookupError):
                os.kill(process_id, 0)
            with self.assertRaises(ProcessLookupError):
                os.killpg(process_id, 0)
            self.assertEqual(
                tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
                descriptors_before,
            )

    def test_bounded_process_settles_its_group_after_output_or_cancellation(
        self,
    ) -> None:
        runner = load_runner_module()
        runtime = Path(sys.executable).resolve(strict=True)

        class Cancelled(BaseException):
            pass

        for abort in ("output", "cancel"):
            with self.subTest(abort=abort), tempfile.TemporaryDirectory() as directory:
                process_id_path = Path(directory) / "child-pid"
                source = (
                    "import os, pathlib, sys, time\n"
                    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), "
                    "encoding='ascii')\n"
                    + (
                        "sys.stdout.buffer.write(b'x' * 129)\n"
                        "sys.stdout.buffer.flush()\n"
                        if abort == "output"
                        else ""
                    )
                    + "time.sleep(60)\n"
                )
                descriptors_before = tuple(
                    sorted(int(name) for name in os.listdir("/dev/fd"))
                )
                previous_handler = signal.getsignal(signal.SIGALRM)

                def cancel(_signal_number, _frame) -> None:
                    raise Cancelled("cancel bounded child")

                try:
                    if abort == "cancel":
                        signal.signal(signal.SIGALRM, cancel)
                        signal.setitimer(signal.ITIMER_REAL, 0.1)
                    expected_error = (
                        runner.QualificationError if abort == "output" else Cancelled
                    )
                    with self.assertRaises(expected_error):
                        runner._bounded_process(
                            [
                                str(runtime),
                                "-I",
                                "-B",
                                "-c",
                                source,
                                str(process_id_path),
                            ],
                            env={"PATH": "/usr/bin:/bin"},
                            stdout_maximum=128,
                            stderr_maximum=128,
                            label=f"{abort} abort child fixture",
                            timeout_seconds=2,
                            own_process_group=True,
                        )
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, previous_handler)

                process_id = int(process_id_path.read_text(encoding="ascii"))
                with self.assertRaises(ProcessLookupError):
                    os.kill(process_id, 0)
                with self.assertRaises(ProcessLookupError):
                    os.killpg(process_id, 0)
                self.assertEqual(
                    tuple(sorted(int(name) for name in os.listdir("/dev/fd"))),
                    descriptors_before,
                )

    def test_isolated_candidate_validator_reaps_a_term_resistant_descendant(
        self,
    ) -> None:
        runner = load_runner_module()
        runtime = Path(sys.executable).resolve(strict=True)
        with mock.patch.object(
            runner,
            "_signal_owned_group",
            wraps=runner._signal_owned_group,
        ) as signal_group:
            status, stdout, stderr = runner._bounded_process(
                [str(runtime), "-I", "-B", "-c", "print('complete')"],
                env={"PATH": "/usr/bin:/bin"},
                stdout_maximum=128,
                stderr_maximum=128,
                label="isolated child without descendants",
                timeout_seconds=2,
                own_process_group=True,
            )
        self.assertEqual((status, stdout, stderr), (0, b"complete\n", b""))
        signal_group.assert_not_called()

        source = (
            "import os, subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(60)'], stdin=subprocess.DEVNULL)\n"
            "print(os.getpid(), child.pid, flush=True)\n"
        )

        status, stdout, stderr = runner._bounded_process(
            [str(runtime), "-I", "-B", "-c", source],
            env={"PATH": "/usr/bin:/bin"},
            stdout_maximum=128,
            stderr_maximum=128,
            label="isolated child fixture",
            timeout_seconds=2,
            own_process_group=True,
        )

        self.assertEqual(status, 0)
        self.assertEqual(stderr, b"")
        process_group, _descendant = (int(value) for value in stdout.split())
        with self.assertRaises((ProcessLookupError, PermissionError)):
            os.killpg(process_group, 0)

    def test_platform_profile_parser_requires_exact_closed_v1_document(self) -> None:
        runner = load_runner_module()
        valid = platform_profile_document()

        parsed = runner.parse_platform_profile(valid)

        self.assertEqual(parsed, valid)
        invalid_documents = []
        missing = dict(valid)
        del missing["target"]
        invalid_documents.append(missing)
        extra = dict(valid)
        extra["unexpected"] = True
        invalid_documents.append(extra)
        wrong_digest = dict(valid)
        wrong_digest["content_sha256"] = "0" * 64
        invalid_documents.append(wrong_digest)
        for invalid in invalid_documents:
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(runner.QualificationError),
            ):
                runner.parse_platform_profile(invalid)

    def test_suite_inventory_parser_accepts_exact_closed_v1_document(self) -> None:
        runner = load_runner_module()
        valid = suite_inventory_document()

        self.assertEqual(runner.parse_suite_inventory(valid), valid)

        alternate_counts = copy.deepcopy(valid)
        alternate_counts["entries"][0]["expected_count"] += 7
        rehash_suite_inventory(alternate_counts)
        with self.assertRaises(runner.QualificationError):
            runner.parse_suite_inventory(alternate_counts)

    def test_suite_inventory_parser_rejects_schema_and_projection_drift(self) -> None:
        runner = load_runner_module()
        invalid_documents = []

        missing_root = suite_inventory_document()
        del missing_root["aggregates"]
        invalid_documents.append(missing_root)
        extra_root = suite_inventory_document()
        extra_root["unexpected"] = True
        invalid_documents.append(extra_root)
        boolean_version = suite_inventory_document()
        boolean_version["schema_version"] = True
        invalid_documents.append(boolean_version)

        def changed_entry(index: int, field: str, replacement: object):
            changed = copy.deepcopy(suite_inventory_document())
            changed["entries"][index][field] = replacement
            rehash_suite_inventory(changed)
            invalid_documents.append(changed)

        changed_entry(0, "id", "renamed-common")
        changed_entry(
            0,
            "argv",
            [
                "-I",
                "-B",
                "scripts/other.py",
                "--suite",
                "client-common",
            ],
        )
        changed_entry(0, "executor", {"kind": "qualified-cpython", "extra": True})
        changed_entry(0, "phase", "portable-vertical")
        changed_entry(0, "targets", ["linux-x86_64", "macos-arm64"])
        changed_entry(14, "targets", ["macos-arm64", "linux-x86_64"])
        changed_entry(0, "expected_terminal", "successful")

        missing_entry_key = copy.deepcopy(suite_inventory_document())
        del missing_entry_key["entries"][0]["executor"]
        rehash_suite_inventory(missing_entry_key)
        invalid_documents.append(missing_entry_key)
        extra_entry_key = copy.deepcopy(suite_inventory_document())
        extra_entry_key["entries"][0]["unexpected"] = True
        rehash_suite_inventory(extra_entry_key)
        invalid_documents.append(extra_entry_key)

        for invalid in invalid_documents:
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(runner.QualificationError),
            ):
                runner.parse_suite_inventory(invalid)

    def test_suite_inventory_parser_rejects_count_and_aggregate_drift(self) -> None:
        runner = load_runner_module()
        invalid_documents = []

        for invalid_count in (0, -1, True, 1.5):
            invalid = copy.deepcopy(suite_inventory_document())
            invalid["entries"][0]["expected_count"] = invalid_count
            rehash_suite_inventory(invalid)
            invalid_documents.append(invalid)

        missing_aggregate = suite_inventory_document()
        del missing_aggregate["aggregates"]["counts_sha256"]
        invalid_documents.append(missing_aggregate)
        extra_aggregate = suite_inventory_document()
        extra_aggregate["aggregates"]["unexpected"] = True
        invalid_documents.append(extra_aggregate)

        for field, replacement in (
            ("counts_sha256", "0" * 64),
            ("entries_sha256", "0" * 64),
            ("entry_count", 15),
            ("entry_count", True),
            ("expected_count_total", 135),
            ("expected_count_total", True),
        ):
            invalid = suite_inventory_document()
            invalid["aggregates"][field] = replacement
            invalid_documents.append(invalid)

        for invalid in invalid_documents:
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(runner.QualificationError),
            ):
                runner.parse_suite_inventory(invalid)

    def test_suite_result_parser_accepts_exact_closed_v1_document(self) -> None:
        runner = load_runner_module()
        valid = suite_result_document()

        self.assertEqual(runner.parse_suite_result(valid), valid)

        other_suite = dict(valid)
        other_suite["id"] = "linux-process-supervision"
        other_suite["observed_count"] = 2**63
        self.assertEqual(runner.parse_suite_result(other_suite), other_suite)

    def test_suite_result_parser_rejects_schema_and_value_drift(self) -> None:
        runner = load_runner_module()
        invalid_documents = []

        for missing_key in suite_result_document():
            invalid = suite_result_document()
            del invalid[missing_key]
            invalid_documents.append(invalid)
        extra = suite_result_document()
        extra["unexpected"] = True
        invalid_documents.append(extra)
        for field, replacement in (
            ("schema_version", True),
            ("schema_version", 2),
            ("contract", "task-witness-tw4-suite-result-v2"),
            ("id", "unregistered-suite"),
            ("observed_count", 0),
            ("observed_count", True),
            ("terminal", "failed"),
            ("detail_stdout_length", -1),
            ("detail_stdout_length", True),
            ("detail_stdout_sha256", "0" * 63),
            ("detail_stderr_length", -1),
            ("detail_stderr_length", True),
            ("detail_stderr_sha256", "g" * 64),
        ):
            invalid = suite_result_document()
            invalid[field] = replacement
            invalid_documents.append(invalid)

        for invalid in invalid_documents:
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(runner.QualificationError),
            ):
                runner.parse_suite_result(invalid)

    def test_runtime_closure_parser_requires_exact_closed_v1_document(self) -> None:
        runner = load_runner_module()
        valid = runtime_closure_document()

        parsed = runner.parse_runtime_closure_evidence(valid)

        self.assertEqual(parsed, valid)
        invalid_documents = []
        missing = dict(valid)
        del missing["authority"]
        invalid_documents.append(missing)
        extra = dict(valid)
        extra["unexpected"] = True
        invalid_documents.append(extra)
        wrong_digest = dict(valid)
        wrong_digest["content_sha256"] = "0" * 64
        invalid_documents.append(wrong_digest)
        for invalid in invalid_documents:
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(runner.QualificationError),
            ):
                runner.parse_runtime_closure_evidence(invalid)

    def test_runtime_closure_rejects_excess_roots_before_entry_traversal(self) -> None:
        runner = load_runner_module()
        evidence = runtime_closure_document()
        closure = evidence["closure"]
        closure["roots"] = [
            {
                "path": f"/opt/task-witness/root-{index:02d}",
                "role": "runtime-root",
                "complete_inventory": True,
            }
            for index in range(runner.MAX_RUNTIME_CLOSURE_ROOTS + 1)
        ]
        rehash_content_document(evidence)

        with (
            mock.patch.object(
                Path,
                "is_relative_to",
                side_effect=AssertionError("entry traversal must not begin"),
                autospec=True,
            ),
            self.assertRaisesRegex(
                runner.QualificationError,
                "runtime closure roots are invalid",
            ),
        ):
            runner.parse_runtime_closure_evidence(evidence)

    def test_runtime_closure_rejects_excess_directory_depth(self) -> None:
        runner = load_runner_module()
        boundary = runtime_closure_document()
        boundary_closure = boundary["closure"]
        boundary_root = boundary_closure["roots"][0]["path"]
        boundary_entry = {
            "path": f"{boundary_root}/"
            + "/".join(
                (
                    *(
                        f"d{index}"
                        for index in range(runner.MAX_RUNTIME_DIRECTORY_DEPTH)
                    ),
                    "leaf",
                )
            ),
            "kind": "regular-file",
            "role": "runtime-data",
            "length": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
            "uid": 0,
            "gid": 0,
            "mode": 292,
        }
        boundary_closure["entries"].append(boundary_entry)
        boundary_closure["entries"].sort(key=lambda item: item["path"])
        boundary_closure["entries_sha256"] = hashlib.sha256(
            canonical_document(boundary_closure["entries"])
        ).hexdigest()
        boundary_closure["entry_count"] = len(boundary_closure["entries"])
        rehash_content_document(boundary)

        self.assertEqual(runner.parse_runtime_closure_evidence(boundary), boundary)

        evidence = runtime_closure_document()
        closure = evidence["closure"]
        root = closure["roots"][0]["path"]
        deep_entry = {
            "path": f"{root}/"
            + "/".join(
                f"d{index}" for index in range(runner.MAX_RUNTIME_DIRECTORY_DEPTH + 1)
            ),
            "kind": "directory",
            "role": "runtime-data",
            "uid": 0,
            "gid": 0,
            "mode": 365,
        }
        closure["entries"].append(deep_entry)
        closure["entries"].sort(key=lambda item: item["path"])
        closure["entries_sha256"] = hashlib.sha256(
            canonical_document(closure["entries"])
        ).hexdigest()
        closure["entry_count"] = len(closure["entries"])
        rehash_content_document(evidence)

        with self.assertRaisesRegex(
            runner.QualificationError,
            "exceeds the directory depth limit",
        ):
            runner.parse_runtime_closure_evidence(evidence)

    def test_closed_paths_reject_double_leading_slash_aliases(self) -> None:
        runner = load_runner_module()
        runtime = runtime_closure_document()
        runtime["closure"]["roots"][0]["path"] = "//opt/task-witness/python"
        rehash_content_document(runtime)

        with self.assertRaisesRegex(
            runner.QualificationError,
            "runtime root 0.path is invalid",
        ):
            runner.parse_runtime_closure_evidence(runtime)

    def test_runtime_closure_rejects_filesystem_root_inventory(self) -> None:
        runner = load_runner_module()
        runtime = runtime_closure_document()
        runtime["closure"]["roots"][0]["path"] = "/"
        rehash_content_document(runtime)

        with self.assertRaisesRegex(
            runner.QualificationError,
            "runtime root 0.path cannot be the filesystem root",
        ):
            runner.parse_runtime_closure_evidence(runtime)

    def test_canonical_json_loader_enforces_the_byte_limit_before_reading(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            prefix = b'{"padding":"'
            suffix = b'"}'
            boundary_raw = (
                prefix
                + (b"x" * (runner.MAX_JSON_BYTES - len(prefix) - len(suffix)))
                + suffix
            )
            boundary = root / "boundary.json"
            boundary.write_bytes(boundary_raw)
            oversized = root / "oversized.json"
            oversized.write_bytes(boundary_raw + b" ")

            self.assertEqual(
                runner.load_canonical_json_object(boundary, "bounded evidence"),
                {"padding": "x" * (runner.MAX_JSON_BYTES - len(prefix) - len(suffix))},
            )
            with (
                mock.patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("unbounded read must not be used"),
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "bounded evidence exceeds the byte limit",
                ),
            ):
                runner.load_canonical_json_object(oversized, "bounded evidence")

            with (
                mock.patch.object(
                    runner.os,
                    "read",
                    side_effect=OSError("fixture read failure"),
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "bounded evidence cannot be read",
                ),
            ):
                runner.load_canonical_json_object(boundary, "bounded evidence")

            original_fstat = runner.os.fstat
            for failure_call in (1, 2):
                calls = 0

                def failing_fstat(descriptor: int, selected_call: int = failure_call):
                    nonlocal calls
                    calls += 1
                    if calls == selected_call:
                        raise OSError("fixture metadata failure")
                    return original_fstat(descriptor)

                with (
                    self.subTest(fstat_failure_call=failure_call),
                    mock.patch.object(
                        runner.os,
                        "fstat",
                        side_effect=failing_fstat,
                    ),
                    self.assertRaisesRegex(
                        runner.QualificationError,
                        "bounded evidence cannot be read",
                    ),
                ):
                    runner.load_canonical_json_object(boundary, "bounded evidence")

            with (
                fail_opened_leaf_close(runner, "fixture close failure"),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "bounded evidence cannot be closed",
                ),
            ):
                runner.load_canonical_json_object(boundary, "bounded evidence")

            original_fstat = runner.os.fstat
            calls = 0

            def drifting_fstat(descriptor: int):
                nonlocal calls
                calls += 1
                metadata = original_fstat(descriptor)
                if calls == 2:
                    return stat_namespace(
                        metadata,
                        st_ctime_ns=metadata.st_ctime_ns + 1,
                    )
                return metadata

            with (
                mock.patch.object(
                    runner.os,
                    "fstat",
                    side_effect=drifting_fstat,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "bounded evidence changed while it was read",
                ),
            ):
                runner.load_canonical_json_object(boundary, "bounded evidence")

            with (
                mock.patch.object(
                    runner.os,
                    "read",
                    side_effect=OSError("primary read failure"),
                ),
                fail_opened_leaf_close(runner, "secondary close failure"),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "bounded evidence cannot be read",
                ),
            ):
                runner.load_canonical_json_object(boundary, "bounded evidence")

            surrogate = root / "surrogate.json"
            surrogate.write_bytes(b'{"value":"\\ud800"}')
            with self.assertRaisesRegex(
                runner.QualificationError,
                "bounded evidence is not valid JSON",
            ):
                runner.load_canonical_json_object(surrogate, "bounded evidence")

            excessive_nesting = root / "excessive-nesting.json"
            excessive_nesting.write_bytes(
                b'{"value":' + (b"[" * 300000) + b"0" + (b"]" * 300000) + b"}"
            )
            oversized_integer = root / "oversized-integer.json"
            oversized_integer.write_bytes(b'{"value":' + (b"9" * 5000) + b"}")
            for invalid in (excessive_nesting, oversized_integer):
                with (
                    self.subTest(invalid=invalid),
                    self.assertRaisesRegex(
                        runner.QualificationError,
                        "bounded evidence is not valid JSON",
                    ),
                ):
                    runner.load_canonical_json_object(invalid, "bounded evidence")

    def test_canonical_json_loader_rejects_symlinked_ancestor(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            actual = root / "actual"
            actual.mkdir()
            evidence = actual / "evidence.json"
            evidence.write_bytes(b'{"value":"alternate"}')
            alias = root / "alias"
            alias.symlink_to(actual, target_is_directory=True)

            with self.assertRaisesRegex(
                runner.QualificationError,
                "bounded evidence cannot be opened without symlinks",
            ):
                runner.load_canonical_json_object(
                    alias / "evidence.json",
                    "bounded evidence",
                )

    def test_canonical_json_loader_rejects_visible_leaf_substitution(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            evidence = root / "evidence.json"
            replacement = root / "replacement.json"
            evidence.write_bytes(b'{"value":"captured"}')
            replacement.write_bytes(b'{"value":"replacement"}')
            real_read = runner.os.read
            replaced = False

            def replace_after_capture(descriptor: int, maximum: int) -> bytes:
                nonlocal replaced
                chunk = real_read(descriptor, maximum)
                if not chunk and not replaced:
                    replaced = True
                    os.replace(replacement, evidence)
                return chunk

            with (
                mock.patch.object(runner.os, "read", side_effect=replace_after_capture),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "bounded evidence changed while it was read",
                ),
            ):
                runner.load_canonical_json_object(evidence, "bounded evidence")

    def test_canonical_json_loader_normalizes_encoder_recursion(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            evidence = root / "encoder-recursion.json"
            evidence.write_bytes((b'{"a":' * 100_000) + b"0" + (b"}" * 100_000))

            with self.assertRaisesRegex(
                runner.QualificationError,
                "bounded evidence is not valid JSON",
            ):
                runner.load_canonical_json_object(evidence, "bounded evidence")

    def test_runtime_closure_parser_rejects_unowned_or_nonexecutable_entries(
        self,
    ) -> None:
        runner = load_runner_module()
        cases: list[dict[str, object]] = []
        outside = runtime_closure_document()
        outside["closure"]["entries"][1]["path"] = "/unowned/runtime/lib"
        cases.append(outside)
        wrong_role = runtime_closure_document()
        wrong_role["closure"]["entries"][0]["role"] = "ordinary-file"
        cases.append(wrong_role)
        nonexecutable = runtime_closure_document()
        nonexecutable["main_executable"]["mode"] = 292
        nonexecutable["closure"]["entries"][0]["mode"] = 292
        cases.append(nonexecutable)
        for invalid in cases:
            closure = invalid["closure"]
            closure["entries_sha256"] = hashlib.sha256(
                canonical_document(closure["entries"])
            ).hexdigest()
            unsigned = {
                key: value for key, value in invalid.items() if key != "content_sha256"
            }
            invalid["content_sha256"] = hashlib.sha256(
                canonical_document(unsigned)
            ).hexdigest()
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(runner.QualificationError),
            ):
                runner.parse_runtime_closure_evidence(invalid)

    def test_executable_profiles_reject_setid_modes(self) -> None:
        runner = load_runner_module()
        profile = platform_profile_document()
        profile["system_tools"][1]["mode"] = 0o4555
        rehash_content_document(profile)
        runtime = runtime_closure_document()
        runtime["main_executable"]["mode"] = 0o4555
        runtime["closure"]["entries"][0]["mode"] = 0o4555
        runtime["closure"]["entries_sha256"] = hashlib.sha256(
            canonical_document(runtime["closure"]["entries"])
        ).hexdigest()
        rehash_content_document(runtime)

        with self.assertRaisesRegex(
            runner.QualificationError,
            "system tool 1.mode is unsafe",
        ):
            runner.parse_platform_profile(profile)
        with self.assertRaisesRegex(
            runner.QualificationError,
            "runtime executable mode is unsafe",
        ):
            runner.parse_runtime_closure_evidence(runtime)

    def test_observed_executables_reject_setid_modes(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            tool = root / "tool"
            tool.write_bytes(b"#!/bin/sh\nexit 0\n")
            tool.chmod(0o555)
            tool_metadata = tool.stat()
            tool_raw = tool.read_bytes()
            profile = current_platform_profile_document()
            profile["system_tools"][1] = {
                "id": "git",
                "invoked_path": str(tool),
                "resolved_path": str(tool),
                "length": len(tool_raw),
                "sha256": hashlib.sha256(tool_raw).hexdigest(),
                "uid": tool_metadata.st_uid,
                "gid": tool_metadata.st_gid,
                "mode": 0o4555,
            }
            original_observation = runner.regular_file_observation

            def setid_tool_observation(path: Path, label: str, *, expected_length: int):
                metadata, length, digest = original_observation(
                    path,
                    label,
                    expected_length=expected_length,
                )
                if path == tool:
                    metadata = stat_namespace(
                        metadata,
                        st_mode=(metadata.st_mode & ~0o7777) | 0o4555,
                    )
                return metadata, length, digest

            with (
                mock.patch.object(
                    runner,
                    "detect_non_native_environment",
                    return_value=False,
                ),
                mock.patch.object(runner, "require_paths_immutable"),
                mock.patch.object(runner, "effective_access", return_value=True),
                mock.patch.object(
                    runner,
                    "regular_file_observation",
                    side_effect=setid_tool_observation,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "platform system tool git mode is unsafe",
                ),
            ):
                runner.validate_platform_observations(profile)

            runtime_root = root / "runtime-root"
            runtime_root.mkdir()
            runtime, evidence = runtime_closure_document_for(runtime_root)
            recorded = evidence["closure"]["entries"][0]
            recorded["mode"] = 0o4555

            def setid_runtime_observation(
                path: Path,
                label: str,
                *,
                expected_length: int,
            ):
                metadata, length, digest = original_observation(
                    path,
                    label,
                    expected_length=expected_length,
                )
                return (
                    stat_namespace(
                        metadata,
                        st_mode=(metadata.st_mode & ~0o7777) | 0o4555,
                    ),
                    length,
                    digest,
                )

            with (
                mock.patch.object(
                    runner,
                    "regular_file_observation",
                    side_effect=setid_runtime_observation,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime executable mode is unsafe",
                ),
            ):
                runner.runtime_entry_observation(runtime, recorded)

    def test_observed_platform_must_match_passwd_target_and_tool_bytes(self) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()

        with (
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(runner, "require_paths_immutable"),
        ):
            runner.validate_platform_observations(valid)

        cases: list[dict[str, object]] = []
        wrong_user = json.loads(canonical_document(valid))
        wrong_user["passwd_user"]["name"] += "-other"
        cases.append(wrong_user)
        wrong_target = json.loads(canonical_document(valid))
        if wrong_target["target"] == "macos-arm64":
            wrong_target["target"] = "linux-x86_64"
            wrong_target["platform"]["system"] = "linux"
            wrong_target["platform"]["machine"] = "x86_64"
        else:
            wrong_target["target"] = "macos-arm64"
            wrong_target["platform"]["system"] = "darwin"
            wrong_target["platform"]["machine"] = "arm64"
        cases.append(wrong_target)
        wrong_tool = json.loads(canonical_document(valid))
        wrong_tool["system_tools"][1]["sha256"] = "0" * 64
        cases.append(wrong_tool)
        for invalid in cases:
            rehash_content_document(invalid)
            runner.parse_platform_profile(invalid)
            with (
                self.subTest(invalid=invalid),
                mock.patch.object(
                    runner,
                    "detect_non_native_environment",
                    return_value=False,
                ),
                self.assertRaises(runner.QualificationError),
            ):
                runner.validate_platform_observations(invalid)

    def test_observed_platform_uses_one_passwd_home_leaf_observation(self) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        home = Path(valid["passwd_user"]["home"])
        original_observation = runner.require_directory_observation
        home_observations = 0

        def single_home_observation(path: Path, label: str):
            nonlocal home_observations
            if path == home:
                home_observations += 1
                if home_observations > 1:
                    raise OSError("fixture repeated home observation")
            return original_observation(path, label)

        with (
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(runner, "require_paths_immutable"),
            mock.patch.object(
                runner,
                "require_directory_observation",
                side_effect=single_home_observation,
            ),
        ):
            runner.validate_platform_observations(valid)
        self.assertEqual(home_observations, 1)

    def test_observed_platform_requires_system_tools_immutable_to_qualification_user(
        self,
    ) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        selected = valid["system_tools"][1]
        selected_path = Path(selected["resolved_path"])
        euid = os.geteuid()
        original_observation = runner.regular_file_observation
        original_lstat = Path.lstat

        def qualification_user_owned_observation(
            path: Path,
            label: str,
            *,
            expected_length: int,
        ):
            metadata, length, digest = original_observation(
                path,
                label,
                expected_length=expected_length,
            )
            if path == selected_path:
                metadata = stat_namespace(metadata, st_uid=euid)
            return metadata, length, digest

        def qualification_user_owned_path(path: Path):
            metadata = original_lstat(path)
            if path == selected_path:
                return stat_namespace(metadata, st_uid=euid)
            return metadata

        owned_tool = json.loads(canonical_document(valid))
        owned_tool["system_tools"][1]["uid"] = euid
        rehash_content_document(owned_tool)
        with (
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(
                runner,
                "regular_file_observation",
                side_effect=qualification_user_owned_observation,
            ),
            mock.patch.object(
                Path,
                "lstat",
                side_effect=qualification_user_owned_path,
                autospec=True,
            ),
            self.assertRaisesRegex(
                runner.QualificationError,
                "platform system tool.*mutable.*qualification user",
            ),
        ):
            runner.validate_platform_observations(owned_tool)

        invoked_parent = Path(selected["invoked_path"]).parent

        def qualification_user_owned_parent(path: Path):
            metadata = original_lstat(path)
            if path == invoked_parent:
                return stat_namespace(metadata, st_uid=euid)
            return metadata

        with (
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(
                Path,
                "lstat",
                side_effect=qualification_user_owned_parent,
                autospec=True,
            ),
            self.assertRaisesRegex(
                runner.QualificationError,
                "platform system tool.*mutable.*qualification user",
            ),
        ):
            runner.validate_platform_observations(valid)

    def test_observed_platform_joins_system_tool_identity_to_immutability(
        self,
    ) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        selected = Path(valid["system_tools"][1]["resolved_path"])
        original_lstat = Path.lstat
        original_require = runner.require_paths_immutable
        immutable_euid = os.geteuid() + 10000

        def replaced_at_immutability(path: Path):
            metadata = original_lstat(path)
            if path == selected:
                return stat_namespace(metadata, st_ino=metadata.st_ino + 1)
            return metadata

        def replacement_during_immutability(
            paths: set[Path],
            label: str,
            *,
            expected_bindings: dict[Path, tuple[int, ...]] | None = None,
        ) -> None:
            with (
                mock.patch.object(
                    Path,
                    "lstat",
                    side_effect=replaced_at_immutability,
                    autospec=True,
                ),
                mock.patch.object(runner.os, "geteuid", return_value=immutable_euid),
                mock.patch.object(runner, "effective_access", return_value=False),
            ):
                original_require(
                    paths,
                    label,
                    expected_bindings=expected_bindings,
                )

        with (
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(
                runner,
                "require_paths_immutable",
                side_effect=replacement_during_immutability,
            ),
            self.assertRaisesRegex(
                runner.QualificationError,
                "platform system tool git immutable entry identity changed",
            ),
        ):
            runner.validate_platform_observations(valid)

    def test_observed_platform_joins_system_tool_binding_to_immutability(
        self,
    ) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        selected = Path(valid["system_tools"][1]["resolved_path"])
        original_lstat = Path.lstat
        original_require = runner.require_paths_immutable
        immutable_euid = os.geteuid() + 10000

        def changed_in_place_at_immutability(path: Path):
            metadata = original_lstat(path)
            if path == selected:
                return stat_namespace(
                    metadata,
                    st_ctime_ns=metadata.st_ctime_ns + 1,
                    st_mtime_ns=metadata.st_mtime_ns + 1,
                )
            return metadata

        def drift_during_immutability(
            paths: set[Path],
            label: str,
            *,
            expected_bindings: dict[Path, tuple[int, ...]] | None = None,
        ) -> None:
            with (
                mock.patch.object(
                    Path,
                    "lstat",
                    side_effect=changed_in_place_at_immutability,
                    autospec=True,
                ),
                mock.patch.object(runner.os, "geteuid", return_value=immutable_euid),
                mock.patch.object(runner, "effective_access", return_value=False),
            ):
                original_require(
                    paths,
                    label,
                    expected_bindings=expected_bindings,
                )

        with (
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(
                runner,
                "require_paths_immutable",
                side_effect=drift_during_immutability,
            ),
            self.assertRaisesRegex(
                runner.QualificationError,
                "platform system tool git immutable entry binding changed",
            ),
        ):
            runner.validate_platform_observations(valid)

    def test_observed_platform_requires_executable_system_tools(self) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        selected = Path(valid["system_tools"][1]["resolved_path"])
        original_access = os.access
        access_calls: list[tuple[Path, int, bool]] = []

        def inaccessible_tool(
            path: Path,
            mode: int,
            *,
            effective_ids: bool = False,
        ) -> bool:
            access_calls.append((Path(path), mode, effective_ids))
            if Path(path) == selected and mode == os.X_OK:
                return False
            return original_access(path, mode, effective_ids=effective_ids)

        with (
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(runner, "require_paths_immutable"),
            mock.patch.object(runner.os, "access", side_effect=inaccessible_tool),
            self.assertRaisesRegex(
                runner.QualificationError,
                "platform system tool git is not executable",
            ),
        ):
            runner.validate_platform_observations(valid)
        self.assertTrue(access_calls)
        self.assertTrue(all(effective_ids for _, _, effective_ids in access_calls))

    def test_observed_platform_rejects_system_tool_resolution_drift(self) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        selected = Path(valid["system_tools"][1]["invoked_path"])
        recorded = Path(valid["system_tools"][1]["resolved_path"])
        original_resolution = runner.observe_path_resolution
        selected_calls = 0

        def drifting_resolution(path: Path, label: str):
            nonlocal selected_calls
            resolved, bindings = original_resolution(path, label)
            if path == selected:
                selected_calls += 1
                if selected_calls == 2:
                    return recorded.with_name(recorded.name + "-replacement"), bindings
            return resolved, bindings

        with (
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(runner, "require_paths_immutable"),
            mock.patch.object(
                runner,
                "observe_path_resolution",
                side_effect=drifting_resolution,
            ),
            self.assertRaisesRegex(
                runner.QualificationError,
                "platform system tool git resolution changed",
            ),
        ):
            runner.validate_platform_observations(valid)

    def test_observed_platform_rejects_mutable_intermediate_tool_symlink(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            trusted = root / "trusted"
            mutable = root / "mutable"
            home = root / "home"
            trusted.mkdir()
            mutable.mkdir()
            home.mkdir()
            final = trusted / "git-final"
            final.write_bytes(b"#!/bin/sh\nexit 0\n")
            final.chmod(0o500)
            selector = mutable / "git-selector"
            selector.symlink_to(final)
            invoked = trusted / "git"
            invoked.symlink_to(selector)
            final_metadata = final.stat()
            final_raw = final.read_bytes()

            valid = current_platform_profile_document()
            valid["system_tools"][1] = {
                "id": "git",
                "invoked_path": str(invoked),
                "resolved_path": str(final),
                "length": len(final_raw),
                "sha256": hashlib.sha256(final_raw).hexdigest(),
                "uid": final_metadata.st_uid,
                "gid": final_metadata.st_gid,
                "mode": stat.S_IMODE(final_metadata.st_mode),
            }
            qualification_uid = os.geteuid() + 10000
            qualification_gid = os.getegid()
            valid["passwd_user"] = {
                "purpose": "task-witness-disposable-qualification-v1",
                "name": "qualification-user",
                "uid": qualification_uid,
                "primary_gid": qualification_gid,
                "supplementary_gids": sorted(set(os.getgroups())),
                "home": str(home),
                "provisioning_evidence_sha256": "a" * 64,
            }
            original_lstat = Path.lstat
            original_directory_observation = runner.require_directory_observation

            def qualification_owned_paths(path: Path):
                metadata = original_lstat(path)
                if path in {home, selector, mutable}:
                    return stat_namespace(metadata, st_uid=qualification_uid)
                return metadata

            def qualification_owned_home(path: Path, label: str):
                observed_path, metadata = original_directory_observation(path, label)
                if path == home:
                    metadata = stat_namespace(metadata, st_uid=qualification_uid)
                return observed_path, metadata

            passwd_entry = SimpleNamespace(
                pw_name="qualification-user",
                pw_uid=qualification_uid,
                pw_gid=qualification_gid,
                pw_dir=str(home),
            )

            with (
                mock.patch.object(
                    runner,
                    "detect_non_native_environment",
                    return_value=False,
                ),
                mock.patch.object(runner.os, "geteuid", return_value=qualification_uid),
                mock.patch.object(runner.os, "getuid", return_value=qualification_uid),
                mock.patch.object(
                    runner.os,
                    "getresuid",
                    return_value=(qualification_uid,) * 3,
                    create=True,
                ),
                mock.patch.object(
                    runner.os,
                    "getresgid",
                    return_value=(qualification_gid,) * 3,
                    create=True,
                ),
                mock.patch.object(runner.pwd, "getpwuid", return_value=passwd_entry),
                mock.patch.object(
                    runner,
                    "require_directory_observation",
                    side_effect=qualification_owned_home,
                ),
                mock.patch.object(
                    Path,
                    "lstat",
                    side_effect=qualification_owned_paths,
                    autospec=True,
                ),
                mock.patch.object(
                    runner,
                    "effective_access",
                    side_effect=lambda _path, mode: mode == os.X_OK,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "platform system tool git is mutable by the qualification user",
                ),
            ):
                runner.validate_platform_observations(valid)

    def test_observed_platform_does_not_erase_missing_symlink_target_component(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            final = root / "git-final"
            final.write_bytes(b"#!/bin/sh\nexit 0\n")
            final.chmod(0o500)
            invoked = root / "git"
            invoked.symlink_to("missing/../git-final")
            final_metadata = final.stat()
            final_raw = final.read_bytes()
            valid = current_platform_profile_document()
            valid["system_tools"][1] = {
                "id": "git",
                "invoked_path": str(invoked),
                "resolved_path": str(final),
                "length": len(final_raw),
                "sha256": hashlib.sha256(final_raw).hexdigest(),
                "uid": final_metadata.st_uid,
                "gid": final_metadata.st_gid,
                "mode": stat.S_IMODE(final_metadata.st_mode),
            }

            with (
                mock.patch.object(
                    runner,
                    "detect_non_native_environment",
                    return_value=False,
                ),
                mock.patch.object(runner, "require_paths_immutable"),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "platform system tool git cannot be resolved",
                ),
            ):
                runner.validate_platform_observations(valid)

    def test_observed_platform_allows_repeated_symlink_with_shrinking_suffix(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            final = root / "git-final"
            final.write_bytes(b"#!/bin/sh\nexit 0\n")
            repeated = root / "self"
            repeated.symlink_to(".")
            invoked = repeated / "self" / final.name

            resolved, _bindings = runner.observe_path_resolution(
                invoked,
                "platform system tool git",
            )

            self.assertEqual(resolved, final)

    def test_observed_platform_preserves_terminal_directory_requirement(self) -> None:
        runner = load_runner_module()
        for target_suffix in ("/", "/."):
            with (
                self.subTest(target_suffix=target_suffix),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                final = root / "git-final"
                final.write_bytes(b"#!/bin/sh\nexit 0\n")
                invoked = root / "git"
                os.symlink(f"{final.name}{target_suffix}", invoked)

                with self.assertRaisesRegex(
                    runner.QualificationError,
                    "platform system tool git cannot be resolved",
                ):
                    runner.observe_path_resolution(
                        invoked,
                        "platform system tool git",
                    )

    def test_observed_platform_rejects_effective_group_identity_mismatch(
        self,
    ) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        unexpected_gid = pwd.getpwuid(os.geteuid()).pw_gid + 1

        with (
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(runner, "require_paths_immutable"),
            mock.patch.object(runner.os, "getegid", return_value=unexpected_gid),
            self.assertRaisesRegex(
                runner.QualificationError,
                "platform effective group identity disagrees",
            ),
        ):
            runner.validate_platform_observations(valid)

    def test_observed_platform_rejects_supplementary_group_identity_mismatch(
        self,
    ) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        observed_groups = sorted(set(os.getgroups()))
        unexpected_gid = max(observed_groups, default=0) + 1

        with (
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(runner, "require_paths_immutable"),
            mock.patch.object(
                runner.os,
                "getgroups",
                return_value=observed_groups + [unexpected_gid],
            ),
            self.assertRaisesRegex(
                runner.QualificationError,
                "platform supplementary group identity disagrees",
            ),
        ):
            runner.validate_platform_observations(valid)

    def test_observed_platform_rejects_retained_real_or_saved_credentials(
        self,
    ) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        euid = os.geteuid()
        egid = os.getegid()
        cases = (
            {
                "getuid": 0,
                "getgid": 0,
                "getresuid": (0, euid, euid),
                "getresgid": (0, egid, egid),
            },
            {
                "getuid": euid,
                "getgid": egid,
                "getresuid": (euid, euid, 0),
                "getresgid": (egid, egid, 0),
            },
        )
        for credentials in cases:
            with (
                self.subTest(credentials=credentials),
                mock.patch.object(
                    runner,
                    "detect_non_native_environment",
                    return_value=False,
                ),
                mock.patch.object(runner, "require_paths_immutable"),
                mock.patch.object(
                    runner.os,
                    "getuid",
                    return_value=credentials["getuid"],
                ),
                mock.patch.object(
                    runner.os,
                    "getgid",
                    return_value=credentials["getgid"],
                ),
                mock.patch.object(
                    runner.os,
                    "getresuid",
                    return_value=credentials["getresuid"],
                    create=True,
                ),
                mock.patch.object(
                    runner.os,
                    "getresgid",
                    return_value=credentials["getresgid"],
                    create=True,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "platform retained credential identity disagrees",
                ),
            ):
                runner.validate_platform_observations(valid)

    def test_observed_darwin_rejects_tainted_saved_credentials_without_getters(
        self,
    ) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        valid["target"] = "macos-arm64"
        valid["platform"]["system"] = "darwin"
        valid["platform"]["machine"] = "arm64"
        rehash_content_document(valid)
        taint = mock.Mock(return_value=True)

        with (
            mock.patch.object(
                runner,
                "normalized_host_platform",
                return_value=("darwin", "arm64"),
            ),
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(runner, "require_paths_immutable"),
            mock.patch.object(runner.os, "getresuid", None, create=True),
            mock.patch.object(runner.os, "getresgid", None, create=True),
            mock.patch.object(
                runner,
                "darwin_process_credentials_are_tainted",
                taint,
            ),
            self.assertRaisesRegex(
                runner.QualificationError,
                "platform retained credential identity disagrees",
            ),
        ):
            runner.validate_platform_observations(valid)
        taint.assert_called_once_with()

    def test_observed_linux_rejects_nonempty_process_capabilities(self) -> None:
        runner = load_runner_module()
        valid = current_platform_profile_document()
        valid["target"] = "linux-x86_64"
        valid["platform"]["system"] = "linux"
        valid["platform"]["machine"] = "x86_64"
        rehash_content_document(valid)
        capabilities = mock.Mock(return_value=False)
        euid = os.geteuid()
        egid = os.getegid()

        with (
            mock.patch.object(
                runner,
                "normalized_host_platform",
                return_value=("linux", "x86_64"),
            ),
            mock.patch.object(
                runner,
                "detect_non_native_environment",
                return_value=False,
            ),
            mock.patch.object(
                runner.os, "getresuid", return_value=(euid,) * 3, create=True
            ),
            mock.patch.object(
                runner.os, "getresgid", return_value=(egid,) * 3, create=True
            ),
            mock.patch.object(
                runner,
                "linux_process_capabilities_are_empty",
                capabilities,
            ),
            mock.patch.object(runner, "require_paths_immutable"),
            mock.patch.object(
                runner,
                "effective_access",
                side_effect=lambda _path, mode: mode == os.X_OK,
            ),
            self.assertRaisesRegex(
                runner.QualificationError,
                "platform Linux capability identity disagrees",
            ),
        ):
            runner.validate_platform_observations(valid)
        capabilities.assert_called_once_with()

    def test_regular_file_observation_normalizes_descriptor_read_errors(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "observed-file"
            path.write_bytes(b"stable bytes\n")
            with (
                mock.patch.object(
                    runner.os,
                    "read",
                    side_effect=OSError("fixture read failure"),
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "observed fixture cannot be read",
                ),
            ):
                runner.regular_file_observation(
                    path,
                    "observed fixture",
                    expected_length=path.stat().st_size,
                )

            with (
                fail_opened_leaf_close(runner, "fixture close failure"),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "observed fixture cannot be closed",
                ),
            ):
                runner.regular_file_observation(
                    path,
                    "observed fixture",
                    expected_length=path.stat().st_size,
                )

            with (
                mock.patch.object(
                    runner.os,
                    "read",
                    side_effect=OSError("primary read failure"),
                ),
                fail_opened_leaf_close(runner, "secondary close failure"),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "observed fixture cannot be read",
                ),
            ):
                runner.regular_file_observation(
                    path,
                    "observed fixture",
                    expected_length=path.stat().st_size,
                )

    def test_regular_file_observation_bounds_reads_to_recorded_length(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "observed-file"
            path.write_bytes(b"four")
            read_sizes: list[int] = []

            def endless_read(_descriptor: int, size: int) -> bytes:
                read_sizes.append(size)
                return b"x" * size

            with (
                mock.patch.object(runner.os, "read", side_effect=endless_read),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "observed fixture length disagrees",
                ),
            ):
                runner.regular_file_observation(
                    path,
                    "observed fixture",
                    expected_length=4,
                )
            self.assertEqual(read_sizes, [5])

    def test_regular_file_observation_rejects_post_open_path_replacement(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "observed-file"
            path.write_bytes(b"stable bytes\n")
            original_open = runner.open_absolute_path_without_symlinks
            observations = 0

            def replaced_path_metadata(
                observed: Path, label: str, *, expected_kind: str
            ):
                nonlocal observations
                descriptor, metadata = original_open(
                    observed,
                    label,
                    expected_kind=expected_kind,
                )
                if observed == path:
                    observations += 1
                    if observations == 2:
                        metadata = stat_namespace(
                            metadata,
                            st_ino=metadata.st_ino + 1,
                        )
                return descriptor, metadata

            with (
                mock.patch.object(
                    runner,
                    "open_absolute_path_without_symlinks",
                    side_effect=replaced_path_metadata,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "observed fixture path identity changed",
                ),
            ):
                runner.regular_file_observation(
                    path,
                    "observed fixture",
                    expected_length=path.stat().st_size,
                )

    def test_observed_runtime_requires_exact_complete_immutable_inventory(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, evidence = runtime_closure_document_for(runtime_root)

            with mock.patch.object(runner, "require_runtime_closure_immutable"):
                runner.validate_runtime_observations(runtime, evidence)

                extra = runtime_root / "unexpected"
                extra.write_bytes(b"unexpected\n")
                with self.assertRaises(runner.QualificationError):
                    runner.validate_runtime_observations(runtime, evidence)
                extra.unlink()
                runtime.chmod(0o700)
                runtime.write_bytes(b"changed\n")
                runtime.chmod(0o500)
                with self.assertRaises(runner.QualificationError):
                    runner.validate_runtime_observations(runtime, evidence)
                other = runtime_root / "other-python"
                other.write_bytes(b"#!/bin/sh\nexit 0\n")
                other.chmod(0o500)
                with self.assertRaises(runner.QualificationError):
                    runner.validate_runtime_observations(other, evidence)

    def test_runtime_inventory_rejects_entries_added_during_observation(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, evidence = runtime_closure_document_for(runtime_root)
            recorded = {Path(item["path"]) for item in evidence["closure"]["entries"]}
            late_entry = runtime_root / "late-entry"
            root_binding = runner.stable_stat_binding(runtime_root.stat())

            with (
                mock.patch.object(runner, "require_runtime_closure_immutable"),
                mock.patch.object(
                    runner,
                    "scan_runtime_root",
                    side_effect=(
                        (recorded, root_binding),
                        (recorded | {late_entry}, root_binding),
                    ),
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure complete inventory changed",
                ),
            ):
                runner.validate_runtime_observations(runtime, evidence)

    def test_runtime_inventory_rejects_root_binding_drift(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, evidence = runtime_closure_document_for(runtime_root)
            recorded = {Path(item["path"]) for item in evidence["closure"]["entries"]}
            root_binding = runner.stable_stat_binding(runtime_root.stat())
            root_observations = 0

            def drifting_root(*_args, **_kwargs):
                nonlocal root_observations
                root_observations += 1
                if root_observations == 2:
                    return (
                        recorded,
                        (*root_binding[:-1], root_binding[-1] + 1),
                    )
                return recorded, root_binding

            with (
                mock.patch.object(
                    runner,
                    "scan_runtime_root",
                    side_effect=drifting_root,
                ),
                mock.patch.object(runner, "require_runtime_closure_immutable"),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure root binding changed",
                ),
            ):
                runner.validate_runtime_observations(runtime, evidence)

    def test_runtime_scan_uses_descriptor_relative_child_traversal(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            _runtime, evidence = runtime_closure_document_for(runtime_root)
            child = runtime_root / "lib"
            child.mkdir(mode=0o500)
            child_metadata = child.stat()
            child_entry = {
                "path": str(child),
                "kind": "directory",
                "role": "stdlib-root",
                "uid": child_metadata.st_uid,
                "gid": child_metadata.st_gid,
                "mode": stat.S_IMODE(child_metadata.st_mode),
            }
            closure = evidence["closure"]
            closure["entries"].append(child_entry)
            closure["entries"].sort(key=lambda item: item["path"])
            closure["entries_sha256"] = hashlib.sha256(
                canonical_document(closure["entries"])
            ).hexdigest()
            closure["entry_count"] = len(closure["entries"])
            rehash_content_document(evidence)
            expected_paths = {
                Path(item["path"]) for item in evidence["closure"]["entries"]
            }
            original_open = runner.open_absolute_path_without_symlinks
            original_os_open = runner.os.open
            original_scandir = runner.os.scandir
            scandir_operands: list[object] = []
            relative_child_opens: list[tuple[object, int | None]] = []

            def recording_root_open(
                path: Path,
                label: str,
                *,
                expected_kind: str,
            ):
                return original_open(
                    path,
                    label,
                    expected_kind=expected_kind,
                )

            def recording_open(path, flags, mode=0o777, *, dir_fd=None):
                if dir_fd is not None and path == child.name:
                    relative_child_opens.append((path, dir_fd))
                return original_os_open(path, flags, mode, dir_fd=dir_fd)

            def recording_scandir(path):
                scandir_operands.append(path)
                return original_scandir(path)

            with (
                mock.patch.object(
                    runner,
                    "open_absolute_path_without_symlinks",
                    side_effect=recording_root_open,
                ),
                mock.patch.object(runner.os, "open", side_effect=recording_open),
                mock.patch.object(runner.os, "scandir", side_effect=recording_scandir),
            ):
                observed, _root_binding = runner.scan_runtime_root(
                    runtime_root,
                    expected_paths,
                )

            self.assertEqual(observed, expected_paths)
            self.assertTrue(scandir_operands)
            self.assertTrue(all(isinstance(item, int) for item in scandir_operands))
            self.assertTrue(relative_child_opens)

    def test_runtime_scan_rejects_child_replacement_before_openat(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            child = runtime_root / "lib"
            child.mkdir(mode=0o500)
            expected_paths = {child}
            original_open = runner.os.open
            replaced = False

            def replace_before_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal replaced
                if dir_fd is not None and path == child.name and not replaced:
                    prior = runtime_root / "lib-prior"
                    child.rename(prior)
                    child.mkdir(mode=0o500)
                    replaced = True
                return original_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(runner.os, "open", side_effect=replace_before_open),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure directory identity changed",
                ),
            ):
                runner.scan_runtime_root(runtime_root, expected_paths)

    def test_runtime_scan_checks_binding_before_creating_iterator(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            original_fstat = runner.os.fstat
            fstat_calls = 0

            def drifting_fstat(descriptor: int):
                nonlocal fstat_calls
                metadata = original_fstat(descriptor)
                fstat_calls += 1
                if fstat_calls == 2:
                    return stat_namespace(
                        metadata,
                        st_ctime_ns=metadata.st_ctime_ns + 1,
                    )
                return metadata

            with (
                mock.patch.object(runner.os, "fstat", side_effect=drifting_fstat),
                mock.patch.object(runner.os, "scandir") as scandir,
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure directory binding changed",
                ),
            ):
                runner.scan_runtime_root(runtime_root, set())
            scandir.assert_not_called()

    def test_runtime_scan_bounds_wide_directory_descriptors(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            expected_paths: set[Path] = set()
            for index in range(40):
                child = runtime_root / f"child-{index:02d}"
                child.mkdir(mode=0o500)
                expected_paths.add(child)
            original_open = runner.os.open
            original_close = runner.os.close
            live_descriptors: set[int] = set()
            maximum_live = 0

            def bounded_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal maximum_live
                descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
                live_descriptors.add(descriptor)
                maximum_live = max(maximum_live, len(live_descriptors))
                if maximum_live > 8:
                    raise OSError("fixture descriptor ceiling exceeded")
                return descriptor

            def recording_close(descriptor: int):
                live_descriptors.discard(descriptor)
                return original_close(descriptor)

            with (
                mock.patch.object(runner.os, "open", side_effect=bounded_open),
                mock.patch.object(runner.os, "close", side_effect=recording_close),
            ):
                observed, _root_binding = runner.scan_runtime_root(
                    runtime_root,
                    expected_paths,
                )

            self.assertEqual(observed, expected_paths)
            self.assertLessEqual(maximum_live, 8)

    def test_runtime_root_binding_is_joined_to_immutability(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, evidence = runtime_closure_document_for(runtime_root)
            original_lstat = Path.lstat
            root_lstats = 0

            def replaced_root_at_immutability(path: Path):
                nonlocal root_lstats
                metadata = original_lstat(path)
                if path == runtime_root:
                    root_lstats += 1
                    if root_lstats == 1:
                        return stat_namespace(
                            metadata,
                            st_ino=metadata.st_ino + 1,
                        )
                return metadata

            def immutable_access(_path: Path, mode: int) -> bool:
                if mode == os.X_OK:
                    return True
                if mode == os.W_OK:
                    return False
                raise AssertionError(f"unexpected access mode: {mode}")

            with (
                mock.patch.object(
                    Path,
                    "lstat",
                    side_effect=replaced_root_at_immutability,
                    autospec=True,
                ),
                mock.patch.object(
                    runner.os,
                    "geteuid",
                    return_value=os.geteuid() + 1,
                ),
                mock.patch.object(
                    runner,
                    "effective_access",
                    side_effect=immutable_access,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure immutable entry identity changed",
                ),
            ):
                runner.validate_runtime_observations(runtime, evidence)

    def test_runtime_inventory_rejects_unexpected_entry_without_consuming_tail(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            _runtime, evidence = runtime_closure_document_for(runtime_root)
            unexpected = runtime_root / "unexpected"
            unexpected.write_bytes(b"unexpected\n")
            expected_paths = {
                Path(item["path"]) for item in evidence["closure"]["entries"]
            }
            real_entries = list(os.scandir(runtime_root))
            selected = next(
                item for item in real_entries if Path(item.path) == unexpected
            )

            class FailAfterUnexpected:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def __iter__(self):
                    yield selected
                    raise AssertionError(
                        "scanner consumed beyond first unexpected entry"
                    )

            with (
                mock.patch.object(
                    runner.os,
                    "scandir",
                    return_value=FailAfterUnexpected(),
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure complete inventory disagrees",
                ),
            ):
                _observed, _root_binding = runner.scan_runtime_root(
                    runtime_root,
                    expected_paths,
                )

    def test_runtime_inventory_rejects_same_path_replacement_during_observation(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, evidence = runtime_closure_document_for(runtime_root)
            original_observation = runner.regular_file_observation
            observations = 0

            def replaced_descriptor_identity(
                path: Path,
                label: str,
                *,
                expected_length: int,
            ):
                nonlocal observations
                metadata, length, digest = original_observation(
                    path,
                    label,
                    expected_length=expected_length,
                )
                observations += 1
                if observations == 2:
                    metadata = stat_namespace(
                        metadata,
                        st_ino=metadata.st_ino + 1,
                    )
                return metadata, length, digest

            with (
                mock.patch.object(runner, "require_runtime_closure_immutable"),
                mock.patch.object(
                    runner,
                    "regular_file_observation",
                    side_effect=replaced_descriptor_identity,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure entry identity changed",
                ),
            ):
                runner.validate_runtime_observations(runtime, evidence)

    def test_runtime_inventory_joins_final_identity_to_immutability(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, evidence = runtime_closure_document_for(runtime_root)
            original_lstat = Path.lstat

            def replaced_at_immutability(path: Path):
                metadata = original_lstat(path)
                if path == runtime:
                    return stat_namespace(metadata, st_ino=metadata.st_ino + 1)
                return metadata

            def immutable_access(path: Path, mode: int) -> bool:
                if mode == os.X_OK:
                    return True
                if mode == os.W_OK:
                    return False
                raise AssertionError(f"unexpected access mode: {mode}")

            with (
                mock.patch.object(
                    Path,
                    "lstat",
                    side_effect=replaced_at_immutability,
                    autospec=True,
                ),
                mock.patch.object(
                    runner.os,
                    "geteuid",
                    return_value=os.geteuid() + 1,
                ),
                mock.patch.object(
                    runner,
                    "effective_access",
                    side_effect=immutable_access,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure immutable entry identity changed",
                ),
            ):
                runner.validate_runtime_observations(runtime, evidence)

    def test_runtime_regular_file_ownership_comes_from_the_stable_descriptor(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, evidence = runtime_closure_document_for(runtime_root)
            original_observation = runner.regular_file_observation

            def changed_descriptor_owner(
                path: Path,
                label: str,
                *,
                expected_length: int,
            ):
                metadata, length, digest = original_observation(
                    path,
                    label,
                    expected_length=expected_length,
                )
                return (
                    stat_namespace(metadata, st_uid=metadata.st_uid + 1),
                    length,
                    digest,
                )

            with (
                mock.patch.object(runner, "require_runtime_closure_immutable"),
                mock.patch.object(
                    runner,
                    "regular_file_observation",
                    side_effect=changed_descriptor_owner,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure entry ownership disagrees",
                ),
            ):
                runner.validate_runtime_observations(runtime, evidence)

    def test_runtime_symlink_observation_rejects_inode_replacement(self) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "target"
            target.write_bytes(b"target\n")
            link = root / "link"
            link.symlink_to(target.name)
            original_lstat = Path.lstat
            observations = 0

            def replaced_link_metadata(path: Path):
                nonlocal observations
                metadata = original_lstat(path)
                if path == link:
                    observations += 1
                    if observations == 2:
                        return stat_namespace(metadata, st_ino=metadata.st_ino + 1)
                return metadata

            with (
                mock.patch.object(
                    Path,
                    "lstat",
                    side_effect=replaced_link_metadata,
                    autospec=True,
                ),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure symlink identity changed",
                ),
            ):
                runner.symlink_observation(link, "runtime closure symlink")

    def test_observed_runtime_rejects_secondary_root_with_symlinked_ancestor(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime_root = root / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, evidence = runtime_closure_document_for(runtime_root)
            actual_parent = root / "actual"
            actual_parent.mkdir(mode=0o700)
            actual_library = actual_parent / "library-root"
            actual_library.mkdir(mode=0o700)
            library_file = actual_library / "libpython-qualified.dylib"
            library_raw = b"qualified loader bytes\n"
            library_file.write_bytes(library_raw)
            library_file.chmod(0o400)
            alias_parent = root / "alias"
            alias_parent.symlink_to(actual_parent, target_is_directory=True)
            recorded_library_root = alias_parent / "library-root"
            recorded_library_file = recorded_library_root / library_file.name
            metadata = recorded_library_file.lstat()
            library_entry = {
                "path": str(recorded_library_file),
                "kind": "regular-file",
                "role": "loader-shared-library",
                "length": len(library_raw),
                "sha256": hashlib.sha256(library_raw).hexdigest(),
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
                "mode": stat.S_IMODE(metadata.st_mode),
            }
            closure = evidence["closure"]
            closure["roots"].append(
                {
                    "path": str(recorded_library_root),
                    "role": "loader-library-root",
                    "complete_inventory": True,
                }
            )
            closure["roots"].sort(key=lambda item: item["path"])
            closure["entries"].append(library_entry)
            closure["entries"].sort(key=lambda item: item["path"])
            closure["entries_sha256"] = hashlib.sha256(
                canonical_document(closure["entries"])
            ).hexdigest()
            closure["entry_count"] = len(closure["entries"])
            closure["total_regular_file_bytes"] += len(library_raw)
            rehash_content_document(evidence)

            parsed = runner.parse_runtime_closure_evidence(evidence)
            with self.assertRaisesRegex(
                runner.QualificationError,
                "runtime closure root.*symlink",
            ):
                runner.validate_runtime_observations(runtime, parsed)

    def test_runtime_closure_rejects_symlink_targets_outside_complete_roots(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime_root = root / "runtime"
            runtime_root.mkdir(mode=0o700)
            _runtime, evidence = runtime_closure_document_for(runtime_root)
            outside = root / "outside-loader.dylib"
            outside.write_bytes(b"unqualified loader bytes\n")
            runtime_link = runtime_root / "libpython.dylib"
            runtime_link.symlink_to(outside)
            metadata = runtime_link.lstat()
            link_entry = {
                "path": str(runtime_link),
                "kind": "symlink",
                "role": "loader-shared-library",
                "target": str(outside),
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
            }
            closure = evidence["closure"]
            closure["entries"].append(link_entry)
            closure["entries"].sort(key=lambda item: item["path"])
            closure["entries_sha256"] = hashlib.sha256(
                canonical_document(closure["entries"])
            ).hexdigest()
            closure["entry_count"] = len(closure["entries"])
            rehash_content_document(evidence)

            for outside_raw in (
                b"unqualified loader bytes\n",
                b"substituted loader bytes\n",
            ):
                with self.subTest(outside_raw=outside_raw):
                    outside.write_bytes(outside_raw)
                    with self.assertRaisesRegex(
                        runner.QualificationError,
                        "runtime closure symlink target",
                    ):
                        runner.parse_runtime_closure_evidence(evidence)

    def test_runtime_closure_rejects_canceled_missing_symlink_component(self) -> None:
        runner = load_runner_module()
        evidence = runtime_closure_document()
        link_entry = {
            "path": "/opt/task-witness/python/runtime-link",
            "kind": "symlink",
            "role": "runtime-alias",
            "target": "missing/../bin/python3.13",
            "uid": 0,
            "gid": 0,
        }
        closure = evidence["closure"]
        closure["entries"].append(link_entry)
        closure["entries"].sort(key=lambda item: item["path"])
        closure["entries_sha256"] = hashlib.sha256(
            canonical_document(closure["entries"])
        ).hexdigest()
        closure["entry_count"] = len(closure["entries"])
        rehash_content_document(evidence)

        with self.assertRaisesRegex(
            runner.QualificationError,
            "runtime closure symlink target",
        ):
            runner.parse_runtime_closure_evidence(evidence)

    def test_runtime_closure_allows_repeated_symlink_with_shrinking_suffix(
        self,
    ) -> None:
        runner = load_runner_module()
        evidence = runtime_closure_document()
        closure = evidence["closure"]
        closure["entries"].extend(
            (
                {
                    "path": "/opt/task-witness/python/bin",
                    "kind": "directory",
                    "role": "executable-directory",
                    "uid": 0,
                    "gid": 0,
                    "mode": 365,
                },
                {
                    "path": "/opt/task-witness/python/self",
                    "kind": "symlink",
                    "role": "runtime-alias",
                    "target": ".",
                    "uid": 0,
                    "gid": 0,
                },
                {
                    "path": "/opt/task-witness/python/runtime-link",
                    "kind": "symlink",
                    "role": "runtime-alias",
                    "target": "self/self/bin/python3.13",
                    "uid": 0,
                    "gid": 0,
                },
            )
        )
        closure["entries"].sort(key=lambda item: item["path"])
        closure["entries_sha256"] = hashlib.sha256(
            canonical_document(closure["entries"])
        ).hexdigest()
        closure["entry_count"] = len(closure["entries"])
        rehash_content_document(evidence)

        self.assertEqual(runner.parse_runtime_closure_evidence(evidence), evidence)

    def test_runtime_closure_preserves_terminal_directory_requirement(self) -> None:
        runner = load_runner_module()
        for target_suffix in ("/", "/."):
            with self.subTest(target_suffix=target_suffix):
                evidence = runtime_closure_document()
                closure = evidence["closure"]
                closure["entries"].extend(
                    (
                        {
                            "path": "/opt/task-witness/python/terminal",
                            "kind": "regular-file",
                            "role": "runtime-data",
                            "length": 0,
                            "sha256": hashlib.sha256(b"").hexdigest(),
                            "uid": 0,
                            "gid": 0,
                            "mode": 292,
                        },
                        {
                            "path": "/opt/task-witness/python/runtime-link",
                            "kind": "symlink",
                            "role": "runtime-alias",
                            "target": f"terminal{target_suffix}",
                            "uid": 0,
                            "gid": 0,
                        },
                    )
                )
                closure["entries"].sort(key=lambda item: item["path"])
                closure["entries_sha256"] = hashlib.sha256(
                    canonical_document(closure["entries"])
                ).hexdigest()
                closure["entry_count"] = len(closure["entries"])
                rehash_content_document(evidence)

                with self.assertRaisesRegex(
                    runner.QualificationError,
                    "runtime closure symlink target cannot be traversed",
                ):
                    runner.parse_runtime_closure_evidence(evidence)

    def test_runtime_closure_rejects_oversized_symlink_target(self) -> None:
        runner = load_runner_module()
        evidence = runtime_closure_document()
        closure = evidence["closure"]
        closure["entries"].append(
            {
                "path": "/opt/task-witness/python/runtime-link",
                "kind": "symlink",
                "role": "runtime-alias",
                "target": "./" * (runner.MAX_SYMLINK_TARGET_COMPONENTS + 1),
                "uid": 0,
                "gid": 0,
            }
        )
        closure["entries"].sort(key=lambda item: item["path"])
        closure["entries_sha256"] = hashlib.sha256(
            canonical_document(closure["entries"])
        ).hexdigest()
        closure["entry_count"] = len(closure["entries"])
        rehash_content_document(evidence)

        with self.assertRaisesRegex(
            runner.QualificationError,
            "runtime closure entry.*target is invalid",
        ):
            runner.parse_runtime_closure_evidence(evidence)

    def test_symlink_target_byte_ceiling_includes_darwin_terminator(self) -> None:
        runner = load_runner_module()

        self.assertEqual(
            runner.symlink_target_components("a" * 1023, "runtime link"),
            (False, ("a" * 1023,)),
        )
        with self.assertRaisesRegex(
            runner.QualificationError,
            "runtime link is invalid",
        ):
            runner.symlink_target_components("a" * 1024, "runtime link")

    def test_runtime_closure_enforces_portable_symlink_hop_limit(self) -> None:
        runner = load_runner_module()

        def chained_evidence(link_count: int) -> dict[str, object]:
            evidence = runtime_closure_document()
            closure = evidence["closure"]
            closure["entries"].extend(
                {
                    "path": f"/opt/task-witness/python/link-{index}",
                    "kind": "symlink",
                    "role": "runtime-alias",
                    "target": (
                        f"link-{index + 1}" if index + 1 < link_count else "lib"
                    ),
                    "uid": 0,
                    "gid": 0,
                }
                for index in range(link_count)
            )
            closure["entries"].sort(key=lambda item: item["path"])
            closure["entries_sha256"] = hashlib.sha256(
                canonical_document(closure["entries"])
            ).hexdigest()
            closure["entry_count"] = len(closure["entries"])
            rehash_content_document(evidence)
            return evidence

        accepted = chained_evidence(32)
        self.assertEqual(runner.parse_runtime_closure_evidence(accepted), accepted)

        with self.assertRaisesRegex(
            runner.QualificationError,
            "runtime closure symlink target cycle",
        ):
            runner.parse_runtime_closure_evidence(chained_evidence(33))

    def test_observed_path_resolution_enforces_portable_symlink_hop_limit(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            final = root / "git-final"
            final.write_bytes(b"#!/bin/sh\nexit 0\n")
            for index in reversed(range(33)):
                target = final.name if index == 32 else f"link-{index + 1}"
                (root / f"link-{index}").symlink_to(target)

            with self.assertRaisesRegex(
                runner.QualificationError,
                "platform system tool git resolution contains a symlink cycle",
            ):
                runner.observe_path_resolution(
                    root / "link-0",
                    "platform system tool git",
                )

    def test_observed_runtime_requires_closure_immutable_to_qualification_user(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve() / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, evidence = runtime_closure_document_for(runtime_root)

            with self.assertRaisesRegex(
                runner.QualificationError,
                "runtime closure.*mutable.*qualification user",
            ):
                runner.validate_runtime_observations(runtime, evidence)

    def test_host_state_capture_retains_exact_platform_and_runtime_observations(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            profile_path = root / "platform-profile.json"
            profile = current_platform_profile_document()
            profile_raw = canonical_document(profile)
            profile_path.write_bytes(profile_raw)
            runtime_root = root / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, evidence = runtime_closure_document_for(runtime_root)
            evidence_path = root / "runtime-closure-evidence.json"
            evidence_raw = canonical_document(evidence)
            evidence_path.write_bytes(evidence_raw)

            with (
                mock.patch.object(
                    runner,
                    "detect_non_native_environment",
                    return_value=False,
                ),
                mock.patch.object(runner, "require_paths_immutable"),
            ):
                platform_receipt, platform_input = runner.capture_platform_state(
                    profile_path
                )
                runtime_receipt, runtime_input = runner.capture_runtime_state(
                    runtime,
                    evidence_path,
                )

            self.assertEqual(platform_receipt["profile"], profile)
            self.assertEqual(
                platform_receipt["profile_sha256"],
                hashlib.sha256(profile_raw).hexdigest(),
            )
            self.assertEqual(
                platform_receipt["system_tool_observation_sha256"],
                hashlib.sha256(
                    canonical_document(platform_input["system_tools"])
                ).hexdigest(),
            )
            self.assertEqual(
                platform_input["contract"],
                "task-witness-tw4-platform-observation-v1",
            )
            self.assertEqual(platform_input["profile_file"]["path"], str(profile_path))
            self.assertEqual(platform_input["profile_file"]["length"], len(profile_raw))
            self.assertEqual(
                platform_input["home"]["path"], profile["passwd_user"]["home"]
            )
            self.assertEqual(len(platform_input["system_tools"]), 3)

            self.assertEqual(runtime_receipt["evidence"], evidence)
            self.assertEqual(
                runtime_receipt["evidence_sha256"],
                hashlib.sha256(evidence_raw).hexdigest(),
            )
            self.assertEqual(
                runtime_receipt["closure_observation_sha256"],
                hashlib.sha256(
                    canonical_document(runtime_input["closure_observation"])
                ).hexdigest(),
            )
            self.assertEqual(
                runtime_input["contract"],
                "task-witness-tw4-runtime-observation-v1",
            )
            self.assertEqual(runtime_input["evidence_file"]["path"], str(evidence_path))
            self.assertEqual(
                runtime_input["main_executable_observation"],
                runtime_receipt["main_executable_observation"],
            )
            self.assertEqual(
                runtime_input["closure_observation"]["contract"],
                "task-witness-runtime-closure-observation-v1",
            )

    def test_applicable_suite_execution_is_exact_bounded_and_receipt_shaped(
        self,
    ) -> None:
        runner = load_runner_module()
        inventory = suite_inventory_document()
        candidate = Path("/srv/task-witness-candidate")
        runtime = Path("/opt/task-witness/python/bin/python3.13")
        workspace = Path("/Users/task-witness-qualification/.task-witness-workspace")
        home = Path("/Users/task-witness-qualification")
        observed_ids: list[str] = []

        def execute(argv: list[str], **kwargs):
            suite_id = argv[-1]
            observed_ids.append(suite_id)
            result = suite_result_document()
            result["id"] = suite_id
            result["observed_count"] = {
                entry["id"]: entry["expected_count"]
                for entry in inventory["entries"]
            }[suite_id]
            raw = canonical_document(result)
            self.assertEqual(argv[0], str(runtime))
            self.assertEqual(kwargs["cwd"], candidate)
            self.assertEqual(kwargs["timeout_seconds"], 900)
            self.assertTrue(kwargs["own_process_group"])
            self.assertEqual(kwargs["stdin"], None)
            self.assertEqual(
                kwargs["env"],
                {
                    "HOME": str(home),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONHASHSEED": "0",
                    "PYTHONNOUSERSITE": "1",
                    "TASK_WITNESS_QUALIFICATION_WORKSPACE": str(workspace),
                    "TZ": "UTC",
                },
            )
            return 0, raw, b""

        with mock.patch.object(runner, "_bounded_process", side_effect=execute):
            records = runner.run_applicable_suites(
                candidate,
                runtime,
                inventory,
                "macos-arm64",
                workspace,
                home,
            )

        expected_ids = [
            entry["id"]
            for entry in inventory["entries"]
            if "macos-arm64" in entry["targets"]
        ]
        self.assertEqual(observed_ids, expected_ids)
        self.assertEqual(len(records), 15)
        self.assertEqual([record["id"] for record in records], expected_ids)
        for record in records:
            raw = canonical_document(record["result"])
            self.assertEqual(record["process"]["exit_status"], 0)
            self.assertEqual(record["process"]["stdout_length"], len(raw))
            self.assertEqual(
                record["process"]["stdout_sha256"],
                hashlib.sha256(raw).hexdigest(),
            )
            self.assertEqual(record["process"]["stderr_length"], 0)
            self.assertEqual(
                record["process"]["stderr_sha256"], hashlib.sha256(b"").hexdigest()
            )

    def test_workspace_preparation_tracks_cleanup_and_rejects_substitution(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "home"
            home.mkdir(mode=0o700)
            local = home / ".local"
            local.mkdir(mode=0o700)

            resources = runner.prepare_qualification_workspace(home)
            workspace = resources["workspace_path"]
            self.assertTrue(workspace.is_dir())
            self.assertFalse(resources["created_local"])
            self.assertTrue(resources["created_libexec"])
            self.assertFalse((local / "libexec/task-witness").exists())

            runner.cleanup_qualification_workspace(resources)

            self.assertFalse(workspace.exists())
            self.assertTrue(local.is_dir())
            self.assertFalse((local / "libexec").exists())

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "home"
            home.mkdir(mode=0o700)
            resources = runner.prepare_qualification_workspace(home)
            workspace = resources["workspace_path"]
            displaced = home / "displaced-workspace"
            workspace.rename(displaced)
            workspace.mkdir(mode=0o700)

            with self.assertRaisesRegex(
                runner.QualificationError,
                "workspace identity changed",
            ):
                runner.cleanup_qualification_workspace(resources)

            self.assertTrue(workspace.is_dir())
            self.assertTrue(displaced.is_dir())

    def test_literal_shim_binding_requires_sidecar_detail_and_live_bytes_to_agree(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "home"
            home.mkdir(mode=0o700)
            candidate = root / "candidate"
            template_path = (
                candidate / "plugins/task-witness/client/task_witness_shim.sh.in"
            )
            client_source = (
                candidate / "plugins/task-witness/client/task_witness_client.py"
            )
            template_path.parent.mkdir(parents=True)
            template = (
                b"#!/bin/sh\nexec /usr/bin/env -i LANG=C.UTF-8 LC_ALL=C.UTF-8 "
                b"TZ=UTC @TASK_WITNESS_PYTHON@ -B -I -S -X disable-remote-debug "
                b"@TASK_WITNESS_CLIENT@ \"$@\"\n"
            )
            client = b"print('candidate client')\n"
            template_path.write_bytes(template)
            client_source.write_bytes(client)
            runtime = Path("/opt/task-witness/python/bin/python3.13")
            profile = current_platform_profile_document()
            profile["passwd_user"]["home"] = str(home)
            profile["passwd_user"]["name"] = pwd.getpwuid(os.geteuid()).pw_name
            profile["passwd_user"]["uid"] = os.geteuid()
            profile["passwd_user"]["primary_gid"] = os.getegid()
            profile["passwd_user"]["supplementary_gids"] = sorted(set(os.getgroups()))
            rehash_content_document(profile)
            resources = runner.prepare_qualification_workspace(home)
            install_root = home / ".local/libexec/task-witness"
            installed_client = install_root / "client/task_witness_client.py"
            install_root.mkdir(mode=0o700)
            installed_client.parent.mkdir(mode=0o700)
            installed_client.write_bytes(client)
            installed_client.chmod(0o500)
            installed_shim = install_root / "task-witness"

            def quote(value: str) -> str:
                return "'" + value.replace("'", "'\"'\"'") + "'"

            rendered = (
                template.decode()
                .replace("@TASK_WITNESS_PYTHON@", quote(str(runtime)))
                .replace("@TASK_WITNESS_CLIENT@", quote(str(installed_client)))
                .encode()
            )
            installed_shim.write_bytes(rendered)
            installed_shim.chmod(0o500)

            def installed(path: Path) -> dict[str, object]:
                metadata = path.stat()
                raw = path.read_bytes()
                return {
                    "path": str(path),
                    "length": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "uid": metadata.st_uid,
                    "gid": metadata.st_gid,
                    "mode": stat.S_IMODE(metadata.st_mode),
                    "nlink": metadata.st_nlink,
                }

            expected = {
                "contract": "task-witness-rendered-shim-observation-v1",
                "template": {
                    "path": "plugins/task-witness/client/task_witness_shim.sh.in",
                    "length": len(template),
                    "sha256": hashlib.sha256(template).hexdigest(),
                },
                "runtime_executable_path": str(runtime),
                "client": installed(installed_client),
                "shim": installed(installed_shim),
            }
            sidecar_raw = canonical_document(expected)
            sidecar = resources["workspace_path"] / "literal-rendered-shim-observation.json"
            sidecar.write_bytes(sidecar_raw)
            sidecar.chmod(0o600)
            record = {
                "id": "literal-rendered-shim",
                "result": {
                    **suite_result_document(),
                    "id": "literal-rendered-shim",
                    "observed_count": 1,
                    "detail_stdout_length": len(sidecar_raw),
                    "detail_stdout_sha256": hashlib.sha256(sidecar_raw).hexdigest(),
                },
            }

            observed = runner.bind_literal_rendered_shim(
                candidate,
                runtime,
                profile,
                resources,
                record,
            )

            self.assertEqual(observed, expected)
            for field, value in (
                ("detail_stdout_length", len(sidecar_raw) + 1),
                ("detail_stdout_sha256", "0" * 64),
            ):
                with (
                    self.subTest(field=field),
                    self.assertRaisesRegex(
                        runner.QualificationError,
                        "observation binding disagrees",
                    ),
                ):
                    mutated_record = copy.deepcopy(record)
                    mutated_record["result"][field] = value
                    runner.bind_literal_rendered_shim(
                        candidate,
                        runtime,
                        profile,
                        resources,
                        mutated_record,
                    )
            sidecar.write_bytes(
                canonical_document({**expected, "contract": "changed"})
            )
            with self.assertRaisesRegex(
                runner.QualificationError,
                "observation binding disagrees",
            ):
                runner.bind_literal_rendered_shim(
                    candidate,
                    runtime,
                    profile,
                    resources,
                    record,
                )
            sidecar.write_bytes(sidecar_raw)
            for path, original in (
                (installed_client, client),
                (installed_shim, rendered),
            ):
                path.chmod(0o700)
                path.write_bytes(b"mutated")
                path.chmod(0o500)
                with (
                    self.subTest(path=path),
                    self.assertRaisesRegex(
                        runner.QualificationError,
                        "live binding disagrees",
                    ),
                ):
                    runner.bind_literal_rendered_shim(
                        candidate,
                        runtime,
                        profile,
                        resources,
                        record,
                    )
                path.chmod(0o700)
                path.write_bytes(original)
                path.chmod(0o500)
            runner.cleanup_qualification_workspace(resources)
            self.assertFalse(install_root.exists())
            self.assertFalse((home / ".local").exists())

    def test_host_state_capture_and_receipt_construction_bind_two_exact_snapshots(
        self,
    ) -> None:
        runner = load_runner_module()
        candidate_root = Path("/srv/task-witness-candidate")
        runtime = Path("/opt/task-witness/python/bin/python3.13")
        evidence_path = Path("/evidence/runtime.json")
        profile_path = Path("/evidence/platform.json")
        inventory_path = candidate_root / "release/task-witness/tw4-suite-inventory.json"
        inventory_raw = b"inventory"
        inventory_sha256 = hashlib.sha256(inventory_raw).hexdigest()
        qualification_candidate = {
            "repository_id": "nisavid/agents",
            "commit_sha1": "1" * 40,
            "tree_sha1": "2" * 40,
            "plugin_subtree_sha256": "3" * 64,
            "suite_inventory_sha256": inventory_sha256,
        }
        candidate_closure = {
            "contract": "task-witness-qualification-candidate-closure-v1",
            "entry_count": 1,
            "projection_sha256": "5" * 64,
            "source_shape_sha256": "6" * 64,
        }
        bridge_history = {"contract": "bridge-history"}
        suite_summary = {
            "path": "release/task-witness/tw4-suite-inventory.json",
            "length": len(inventory_raw),
            "sha256": inventory_sha256,
            "counts_sha256": "7" * 64,
            "entries_sha256": "8" * 64,
            "entry_count": 16,
            "expected_count_total": 730,
        }
        inventory = {"entries": [{"id": "suite"}]}
        inventory_binding = {
            "path": str(inventory_path),
            "stat": {"device": 1},
            "length": len(inventory_raw),
            "sha256": inventory_sha256,
        }
        candidate_inputs = {
            "candidate": {
                "contract": "task-witness-tw4-candidate-observation-v1",
                "root_path": str(candidate_root),
                "root": {"path": str(candidate_root), "stat": {"device": 1}},
                "qualification_candidate": qualification_candidate,
                "candidate_closure": candidate_closure,
                "worktree": {"tracked": "clean", "untracked": "none"},
            },
            "bridge_history": {
                "contract": "task-witness-tw4-bridge-history-observation-v1",
                "bridge_history": bridge_history,
                "identity_file": {"path": "/identity"},
                "provenance_file": {"path": "/provenance"},
            },
            "suite_inventory": {
                "contract": "task-witness-tw4-suite-inventory-observation-v1",
                "file": inventory_binding,
                "suite_inventory": suite_summary,
            },
        }
        profile = {
            "target": "macos-arm64",
            "passwd_user": {"home": "/Users/task-witness"},
            "system_tools": [
                {"id": "git", "resolved_path": "/usr/bin/git"},
            ],
        }
        platform_receipt = {
            "profile_sha256": "9" * 64,
            "profile": profile,
            "credential_state": {"kind": "credential"},
            "system_tool_observation_sha256": "a" * 64,
        }
        runtime_receipt = {
            "evidence_sha256": "b" * 64,
            "evidence": {"main_executable": {"path": str(runtime)}},
            "main_executable_observation": {"path": str(runtime)},
            "closure_observation_sha256": "c" * 64,
        }
        platform_input = {"contract": "platform-input"}
        runtime_input = {"contract": "runtime-input"}
        with (
            mock.patch.object(
                runner,
                "capture_platform_state",
                return_value=(platform_receipt, platform_input),
            ),
            mock.patch.object(
                runner,
                "capture_runtime_state",
                return_value=(runtime_receipt, runtime_input),
            ),
            mock.patch.object(
                runner,
                "observe_candidate",
                return_value=candidate_inputs,
            ) as observe,
            mock.patch.object(
                runner,
                "load_canonical_json_document",
                return_value=(inventory, inventory_raw, inventory_binding),
            ),
            mock.patch.object(runner, "parse_suite_inventory", return_value=inventory),
        ):
            before = runner.capture_host_state(
                candidate_root,
                runtime,
                evidence_path,
                profile_path,
            )

        observe.assert_called_once_with(
            candidate_root,
            Path("/usr/bin/git"),
            runtime_executable=runtime,
        )
        self.assertEqual(before["inventory"], inventory)
        self.assertEqual(
            set(before["inputs"]),
            {"candidate", "bridge_history", "suite_inventory", "platform", "runtime"},
        )
        after = copy.deepcopy(before)
        rendered = {"contract": "task-witness-rendered-shim-observation-v1"}
        suite_results = [{"id": str(index)} for index in range(15)]
        receipt, receipt_raw = runner.construct_host_qualification_receipt(
            before,
            after,
            rendered,
            suite_results,
        )
        unsigned = dict(receipt)
        supplied_digest = unsigned.pop("content_sha256")
        self.assertEqual(
            supplied_digest,
            hashlib.sha256(canonical_document(unsigned)).hexdigest(),
        )
        self.assertEqual(receipt_raw, canonical_document(receipt))
        self.assertEqual(receipt["observations"]["before"], receipt["observations"]["after"])

        after["inputs"]["runtime"] = {"contract": "changed-runtime"}
        with self.assertRaisesRegex(runner.QualificationError, "host input changed"):
            runner.construct_host_qualification_receipt(
                before,
                after,
                rendered,
                suite_results,
            )

    def test_receipt_publication_is_descriptor_safe_create_new_and_preserves_races(
        self,
    ) -> None:
        runner = load_runner_module()
        raw = b'{"contract":"receipt"}'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / "receipt.json"

            runner.publish_receipt_create_new(receipt, raw)

            self.assertEqual(receipt.read_bytes(), raw)
            metadata = receipt.stat()
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            self.assertEqual(metadata.st_nlink, 1)
            with self.assertRaisesRegex(
                runner.QualificationError,
                "receipt output already exists",
            ):
                runner.publish_receipt_create_new(receipt, b"replacement")
            self.assertEqual(receipt.read_bytes(), raw)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / "receipt.json"
            real_link = runner.os.link

            def race_link(source, destination, **kwargs):
                destination_descriptor = kwargs["dst_dir_fd"]
                descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=destination_descriptor,
                )
                os.write(descriptor, b"operator-owned")
                os.close(descriptor)
                return real_link(source, destination, **kwargs)

            with (
                mock.patch.object(runner.os, "link", side_effect=race_link),
                self.assertRaisesRegex(
                    runner.QualificationError,
                    "receipt output already exists",
                ),
            ):
                runner.publish_receipt_create_new(receipt, raw)

            self.assertEqual(receipt.read_bytes(), b"operator-owned")
            self.assertEqual(
                [path.name for path in root.iterdir()],
                ["receipt.json"],
            )

    def test_constructed_receipt_is_accepted_by_the_independent_host_parser(
        self,
    ) -> None:
        from tests.test_task_witness_package import TaskWitnessPackageTests

        self.addCleanup(sys.modules.pop, "tests.test_task_witness_package", None)

        runner = load_runner_module()
        validator_spec = importlib.util.spec_from_file_location(
            "task_witness_validator_for_runner_test",
            REPOSITORY / "scripts/validate_task_witness.py",
        )
        if validator_spec is None or validator_spec.loader is None:
            self.fail("Task Witness validator cannot be loaded")
        validator = importlib.util.module_from_spec(validator_spec)
        validator_spec.loader.exec_module(validator)
        fixture_case = TaskWitnessPackageTests(
            "test_host_receipt_parser_accepts_both_complete_v1_target_shapes"
        )
        fixture_case.setUp()
        self.addCleanup(fixture_case.tearDown)
        for target in ("macos-arm64", "linux-x86_64"):
            with self.subTest(target=target):
                fixture = fixture_case.host_receipt_document(target)
                state = {
                    "target": fixture["target"],
                    "platform": fixture["platform"],
                    "runtime": fixture["runtime"],
                    "inputs": fixture["observations"]["inputs"],
                    "inventory": {"contract": "runner-internal-inventory"},
                }

                receipt, raw = runner.construct_host_qualification_receipt(
                    state,
                    copy.deepcopy(state),
                    fixture["rendered_shim"],
                    fixture["suite_results"],
                )

                self.assertEqual(receipt, fixture)
                self.assertEqual(raw, canonical_document(fixture))
                self.assertEqual(
                    validator.parse_host_qualification_receipt(receipt),
                    fixture,
                )

    def test_cli_enforces_closed_evidence_documents_before_host_execution(
        self,
    ) -> None:
        valid_profile = platform_profile_document()
        valid_runtime = runtime_closure_document()
        invalid_profile = dict(valid_profile)
        invalid_profile["unexpected"] = True
        invalid_profile = content_document(
            {
                key: value
                for key, value in invalid_profile.items()
                if key != "content_sha256"
            }
        )
        invalid_runtime = dict(valid_runtime)
        del invalid_runtime["authority"]
        invalid_runtime = content_document(
            {
                key: value
                for key, value in invalid_runtime.items()
                if key != "content_sha256"
            }
        )
        cases = (
            (
                canonical_document(invalid_runtime),
                canonical_document(valid_profile),
                "runtime closure evidence schema drift",
            ),
            (
                canonical_document(valid_runtime),
                canonical_document(invalid_profile),
                "platform profile schema drift",
            ),
        )
        for runtime_raw, profile_raw, expected in cases:
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                result, receipt = self.run_qualification(
                    Path(directory).resolve(),
                    runtime_evidence=runtime_raw,
                    platform_profile=profile_raw,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertFalse(receipt.exists())

    def test_missing_preflight_inputs_fail_without_creating_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / "receipt.json"
            result = self.invoke(
                candidate=root / "missing-candidate",
                runtime=root / "missing-runtime",
                runtime_evidence=root / "missing-runtime-evidence.json",
                platform_profile=root / "missing-platform-profile.json",
                receipt=receipt,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("candidate root", result.stderr)
            self.assertFalse(receipt.exists())

    def test_noncanonical_evidence_fails_without_creating_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result, receipt = self.run_qualification(
                root,
                runtime_evidence=b'{"runtime": "unsettled"}\n',
                platform_profile=b"{}",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "runtime closure evidence is not canonical JSON", result.stderr
            )
            self.assertFalse(receipt.exists())

    def test_container_and_emulated_profiles_fail_without_a_receipt(self) -> None:
        for environment in ("container", "emulated"):
            with (
                self.subTest(environment=environment),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                profile = platform_profile_document()
                profile = content_document(
                    {
                        **{
                            key: value
                            for key, value in profile.items()
                            if key != "content_sha256"
                        },
                        "execution_environment": environment,
                    }
                )
                result, receipt = self.run_qualification(
                    root,
                    runtime_evidence=canonical_document(runtime_closure_document()),
                    platform_profile=canonical_document(profile),
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("native host", result.stderr)
                self.assertFalse(receipt.exists())

    def test_preexisting_receipt_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / "receipt.json"
            receipt.write_bytes(b"operator-owned evidence\n")
            result, _ = self.run_qualification(
                root,
                runtime_evidence=b"{}",
                platform_profile=b'{"execution_environment":"native"}',
                receipt=receipt,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("receipt output already exists", result.stderr)
            self.assertEqual(receipt.read_bytes(), b"operator-owned evidence\n")

    def test_main_runs_the_closed_host_flow_and_cleans_after_publication(self) -> None:
        runner_module = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / "candidate"
            candidate.mkdir()
            runtime_root = root / "runtime"
            runtime_root.mkdir(mode=0o700)
            runtime, runtime_document = runtime_closure_document_for(runtime_root)
            runtime_evidence = root / "runtime-closure-evidence.json"
            runtime_evidence.write_bytes(canonical_document(runtime_document))
            platform_profile = root / "platform-profile.json"
            platform_profile.write_bytes(
                canonical_document(current_platform_profile_document())
            )
            receipt = root / "receipt.json"
            state = {
                "target": current_platform_profile_document()["target"],
                "platform": {"profile": current_platform_profile_document()},
                "runtime": {"evidence": runtime_document},
                "inputs": {},
                "inventory": {"entries": []},
            }
            records = [{"id": f"suite-{index}"} for index in range(14)] + [
                {"id": "literal-rendered-shim", "result": {}}
            ]
            resources = {
                "workspace_path": root / "workspace",
                "closed": False,
            }
            trace: list[str] = []
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    runner_module,
                    "parse_args",
                    return_value=SimpleNamespace(
                        candidate_root=str(candidate),
                        runtime_executable=str(runtime),
                        runtime_closure_evidence=str(runtime_evidence),
                        platform_profile=str(platform_profile),
                        receipt_output=str(receipt),
                    ),
                ),
                mock.patch.object(
                    runner_module, "validate_passwd_user"
                ),
                mock.patch.object(
                    runner_module,
                    "prepare_qualification_workspace",
                    side_effect=lambda _home: (trace.append("prepare") or resources),
                ),
                mock.patch.object(
                    runner_module,
                    "capture_host_state",
                    side_effect=lambda *_args: (
                        trace.append("capture") or copy.deepcopy(state)
                    ),
                ),
                mock.patch.object(
                    runner_module,
                    "run_applicable_suites",
                    side_effect=lambda *_args: (trace.append("suites") or records),
                ),
                mock.patch.object(
                    runner_module,
                    "bind_literal_rendered_shim",
                    side_effect=lambda *_args: (
                        trace.append("bind-shim") or {"contract": "rendered"}
                    ),
                ),
                mock.patch.object(
                    runner_module,
                    "_cleanup_literal_rendered_shim_install",
                    side_effect=lambda *_args: trace.append("cleanup-shim"),
                ),
                mock.patch.object(
                    runner_module,
                    "construct_host_qualification_receipt",
                    side_effect=lambda *_args: (
                        trace.append("construct") or ({"contract": "receipt"}, b"receipt")
                    ),
                ),
                mock.patch.object(
                    runner_module,
                    "publish_receipt_create_new",
                    side_effect=lambda *_args: trace.append("publish"),
                ) as publish,
                mock.patch.object(
                    runner_module,
                    "cleanup_qualification_workspace",
                    side_effect=lambda *_args: trace.append("cleanup-workspace"),
                ),
                redirect_stderr(stderr),
            ):
                result = runner_module._retired_native_main()

            self.assertEqual(result, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(
                trace,
                [
                    "prepare",
                    "capture",
                    "suites",
                    "bind-shim",
                    "cleanup-shim",
                    "capture",
                    "construct",
                    "publish",
                    "cleanup-workspace",
                ],
            )
            publish.assert_called_once_with(receipt, b"receipt")

    def test_host_execution_cleans_owned_state_after_suite_failure(self) -> None:
        runner = load_runner_module()
        profile = {"target": "macos-arm64"}
        evidence = {"contract": "runtime"}
        state = {
            "target": "macos-arm64",
            "platform": {"profile": profile},
            "runtime": {"evidence": evidence},
            "inputs": {},
            "inventory": {"entries": []},
        }
        resources = {
            "workspace_path": Path("/passwd/home/workspace"),
            "closed": False,
        }
        with (
            mock.patch.object(
                runner,
                "prepare_qualification_workspace",
                return_value=resources,
            ),
            mock.patch.object(runner, "capture_host_state", return_value=state),
            mock.patch.object(
                runner,
                "run_applicable_suites",
                side_effect=runner.QualificationError("suite failed"),
            ),
            mock.patch.object(runner, "cleanup_qualification_workspace") as cleanup,
            mock.patch.object(runner, "publish_receipt_create_new") as publish,
            self.assertRaisesRegex(runner.QualificationError, "suite failed"),
        ):
            runner.execute_host_qualification(
                receipt_output=Path("/evidence/receipt.json"),
                candidate_root=Path("/candidate"),
                runtime_executable=Path("/runtime/python"),
                runtime_evidence_path=Path("/evidence/runtime.json"),
                runtime_evidence=evidence,
                platform_profile_path=Path("/evidence/platform.json"),
                platform_profile=profile,
                home=Path("/passwd/home"),
            )

        cleanup.assert_called_once_with(resources)
        publish.assert_not_called()

    def test_receipt_output_must_be_external_to_candidate_and_runtime_roots(
        self,
    ) -> None:
        runner = load_runner_module()
        evidence = {
            "closure": {
                "roots": [
                    {"path": "/opt/task-witness/python"},
                    {"path": "/usr/lib/task-witness-runtime"},
                ]
            }
        }
        for output in (
            Path("/srv/candidate/receipt.json"),
            Path("/opt/task-witness/python/receipt.json"),
            Path("/usr/lib/task-witness-runtime/nested/receipt.json"),
        ):
            with (
                self.subTest(output=output),
                self.assertRaisesRegex(runner.QualificationError, "overlaps"),
            ):
                runner.reject_receipt_output_overlap(
                    output,
                    Path("/srv/candidate"),
                    evidence,
                )
        runner.reject_receipt_output_overlap(
            Path("/evidence/receipt.json"),
            Path("/srv/candidate"),
            evidence,
        )

    def test_symlinked_input_and_ancestor_paths_fail_without_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate_target = root / "candidate-target"
            candidate_target.mkdir()
            (candidate_target / "nested").mkdir()
            candidate_link = root / "candidate-link"
            candidate_link.symlink_to(candidate_target, target_is_directory=True)
            runtime = Path(sys.executable).resolve(strict=True)
            runtime_evidence = root / "runtime-closure-evidence.json"
            runtime_evidence.write_bytes(b"{}")
            platform_profile = root / "platform-profile.json"
            platform_profile.write_bytes(b'{"execution_environment":"native"}')

            for candidate in (candidate_link, candidate_link / "nested"):
                with self.subTest(candidate=candidate):
                    receipt = root / (candidate.name + "-receipt.json")
                    result = self.invoke(
                        candidate=candidate,
                        runtime=runtime,
                        runtime_evidence=runtime_evidence,
                        platform_profile=platform_profile,
                        receipt=receipt,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("symlink", result.stderr)
                    self.assertFalse(receipt.exists())

    def test_nondirectory_input_ancestor_fails_without_a_traceback_or_receipt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            blocking_file = root / "not-a-directory"
            blocking_file.write_bytes(b"ordinary file\n")
            receipt = root / "receipt.json"
            result = self.invoke(
                candidate=blocking_file / "candidate",
                runtime=Path(sys.executable).resolve(strict=True),
                runtime_evidence=root / "missing-runtime-evidence.json",
                platform_profile=root / "missing-platform-profile.json",
                receipt=receipt,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "candidate root cannot be opened without symlinks",
                result.stderr,
            )
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(receipt.exists())

            nested_receipt = blocking_file / "receipt.json"
            output_result = self.invoke(
                candidate=root,
                runtime=Path(sys.executable).resolve(strict=True),
                runtime_evidence=root / "missing-runtime-evidence.json",
                platform_profile=root / "missing-platform-profile.json",
                receipt=nested_receipt,
            )
            self.assertNotEqual(output_result.returncode, 0)
            self.assertIn("receipt output parent", output_result.stderr)
            self.assertNotIn("Traceback", output_result.stderr)
            self.assertFalse(nested_receipt.exists())

    def test_path_requirements_use_descriptor_walk_without_path_resolution(
        self,
    ) -> None:
        runner = load_runner_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            file_path = root / "evidence.json"
            file_path.write_bytes(b"{}")

            for path, requirement in (
                (root, runner.require_directory),
                (file_path, runner.require_file),
            ):
                with (
                    self.subTest(path=path),
                    mock.patch.object(
                        Path,
                        "lstat",
                        side_effect=AssertionError("pathname lstat must not run"),
                    ),
                    mock.patch.object(
                        Path,
                        "resolve",
                        side_effect=AssertionError("post-walk resolve must not run"),
                        autospec=True,
                    ),
                ):
                    self.assertEqual(requirement(path, "observed path"), path)

    def test_nonexecutable_runtime_fails_without_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / "candidate"
            candidate.mkdir()
            runtime = root / "python3.13"
            runtime.write_bytes(b"not executable\n")
            runtime.chmod(0o600)
            runtime_evidence = root / "runtime-closure-evidence.json"
            runtime_evidence.write_bytes(b"{}")
            platform_profile = root / "platform-profile.json"
            platform_profile.write_bytes(b'{"execution_environment":"native"}')
            receipt = root / "receipt.json"

            result = self.invoke(
                candidate=candidate,
                runtime=runtime,
                runtime_evidence=runtime_evidence,
                platform_profile=platform_profile,
                receipt=receipt,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("runtime executable is not executable", result.stderr)
            self.assertFalse(receipt.exists())

    def test_file_path_preflight_rejects_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / "candidate"
            candidate.mkdir()
            runtime = root / "runtime-fifo"
            os.mkfifo(runtime)
            runtime_evidence = root / "runtime-closure-evidence.json"
            runtime_evidence.write_bytes(b"{}")
            platform_profile = root / "platform-profile.json"
            platform_profile.write_bytes(b'{"execution_environment":"native"}')
            receipt = root / "receipt.json"
            try:
                result = self.invoke(
                    candidate=candidate,
                    runtime=runtime,
                    runtime_evidence=runtime_evidence,
                    platform_profile=platform_profile,
                    receipt=receipt,
                    timeout=1.0,
                )
            except subprocess.TimeoutExpired as error:
                self.fail(
                    "file-valued preflight blocked while opening a FIFO; "
                    f"stdout={error.stdout!r}, stderr={error.stderr!r}"
                )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "runtime executable must be a regular file",
                result.stderr,
            )
            self.assertFalse(receipt.exists())

    def test_symlinked_file_inputs_and_output_parent_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / "candidate"
            candidate.mkdir()
            runtime = Path(sys.executable).resolve(strict=True)
            runtime_link = root / "runtime-link"
            runtime_link.symlink_to(runtime)
            runtime_evidence = root / "runtime-closure-evidence.json"
            runtime_evidence.write_bytes(b"{}")
            runtime_evidence_link = root / "runtime-evidence-link"
            runtime_evidence_link.symlink_to(runtime_evidence)
            platform_profile = root / "platform-profile.json"
            platform_profile.write_bytes(b'{"execution_environment":"native"}')
            platform_profile_link = root / "platform-profile-link"
            platform_profile_link.symlink_to(platform_profile)
            receipt_parent = root / "receipt-parent"
            receipt_parent.mkdir()
            receipt_parent_link = root / "receipt-parent-link"
            receipt_parent_link.symlink_to(receipt_parent, target_is_directory=True)
            cases = (
                (runtime_link, runtime_evidence, platform_profile, root / "one.json"),
                (runtime, runtime_evidence_link, platform_profile, root / "two.json"),
                (runtime, runtime_evidence, platform_profile_link, root / "three.json"),
                (
                    runtime,
                    runtime_evidence,
                    platform_profile,
                    receipt_parent_link / "four.json",
                ),
            )

            for candidate_case in cases:
                with self.subTest(paths=candidate_case):
                    selected_runtime, evidence, profile, receipt = candidate_case
                    result = self.invoke(
                        candidate=candidate,
                        runtime=selected_runtime,
                        runtime_evidence=evidence,
                        platform_profile=profile,
                        receipt=receipt,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("symlink", result.stderr)
                    self.assertFalse(receipt.exists())

    def test_cli_paths_reject_double_leading_slash_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / "candidate"
            candidate.mkdir()
            runtime = Path(sys.executable).resolve(strict=True)
            runtime_evidence = root / "runtime-closure-evidence.json"
            runtime_evidence.write_bytes(b"{}")
            platform_profile = root / "platform-profile.json"
            platform_profile.write_bytes(b'{"execution_environment":"native"}')
            receipt = root / "receipt.json"
            aliased_candidate = Path(f"//{str(candidate).lstrip('/')}")

            result = self.invoke(
                candidate=aliased_candidate,
                runtime=runtime,
                runtime_evidence=runtime_evidence,
                platform_profile=platform_profile,
                receipt=receipt,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("candidate root must use one leading slash", result.stderr)
            self.assertFalse(receipt.exists())

    def test_cli_file_paths_preserve_terminal_directory_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / "candidate"
            candidate.mkdir()
            runtime = Path(sys.executable).resolve(strict=True)
            runtime_evidence = root / "runtime-closure-evidence.json"
            runtime_evidence.write_bytes(b"{}")
            platform_profile = root / "platform-profile.json"
            platform_profile.write_bytes(b'{"execution_environment":"native"}')
            receipt = root / "receipt.json"
            cases = (
                (f"{runtime}/", runtime_evidence, platform_profile, receipt),
                (runtime, f"{runtime_evidence}/.", platform_profile, receipt),
                (runtime, runtime_evidence, f"{platform_profile}/", receipt),
                (runtime, runtime_evidence, platform_profile, f"{receipt}/."),
            )

            for selected_runtime, evidence, profile, output in cases:
                with self.subTest(
                    runtime=selected_runtime,
                    evidence=evidence,
                    profile=profile,
                    output=output,
                ):
                    result = self.invoke(
                        candidate=candidate,
                        runtime=selected_runtime,
                        runtime_evidence=evidence,
                        platform_profile=profile,
                        receipt=output,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("terminal directory syntax", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertFalse(receipt.exists())


if __name__ == "__main__":
    unittest.main()
