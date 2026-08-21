from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(relative: str, name: str):
    path = ROOT / relative
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def initialize_candidate(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=path, check=True)


def test_candidate_identity_distinguishes_newline_path_record_collision(
    tmp_path: Path,
) -> None:
    transport = load("scripts/evidence_transport.py", "security_identity_transport")
    one_file = tmp_path / "one-file"
    two_files = tmp_path / "two-files"
    initialize_candidate(one_file)
    initialize_candidate(two_files)

    first_name = "plugins/example/p"
    second_name = "plugins/example/q"
    first_content = b"same first file\n"
    second_content = b"behavior-changing second file\n"
    second_sha256 = hashlib.sha256(second_content).hexdigest()
    composite_name = f"{first_name}\n{second_sha256}  {second_name}"

    (one_file / composite_name).parent.mkdir(parents=True)
    (one_file / composite_name).write_bytes(first_content)
    (two_files / first_name).parent.mkdir(parents=True)
    (two_files / first_name).write_bytes(first_content)
    (two_files / second_name).write_bytes(second_content)
    subprocess.run(["git", "add", "--all"], cwd=one_file, check=True)
    subprocess.run(["git", "add", "--all"], cwd=two_files, check=True)

    one_file_identity = transport.candidate_content_identity(
        one_file, error_factory=RuntimeError
    )
    two_file_identity = transport.candidate_content_identity(
        two_files, error_factory=RuntimeError
    )

    assert transport.candidate_content_identity(
        one_file, error_factory=RuntimeError
    ) == one_file_identity
    assert one_file_identity != two_file_identity


def test_candidate_identity_binds_executable_mode(tmp_path: Path) -> None:
    transport = load("scripts/evidence_transport.py", "security_mode_transport")
    regular = tmp_path / "regular"
    executable = tmp_path / "executable"
    initialize_candidate(regular)
    initialize_candidate(executable)

    for candidate, mode in ((regular, 0o644), (executable, 0o755)):
        payload = candidate / "payload"
        payload.write_bytes(b"same bytes\n")
        payload.chmod(mode)
        subprocess.run(["git", "add", "payload"], cwd=candidate, check=True)

    assert transport.candidate_content_identity(
        regular, error_factory=RuntimeError
    ) != transport.candidate_content_identity(executable, error_factory=RuntimeError)


def test_candidate_identity_binds_missing_tracked_blob(tmp_path: Path) -> None:
    transport = load("scripts/evidence_transport.py", "security_index_transport")
    first = tmp_path / "first"
    second = tmp_path / "second"
    initialize_candidate(first)
    initialize_candidate(second)

    for candidate, content in ((first, b"first\n"), (second, b"second\n")):
        payload = candidate / "payload"
        payload.write_bytes(content)
        subprocess.run(["git", "add", "payload"], cwd=candidate, check=True)
        subprocess.run(
            ["git", "update-index", "--skip-worktree", "payload"],
            cwd=candidate,
            check=True,
        )
        payload.unlink()

    assert transport.candidate_content_identity(
        first, error_factory=RuntimeError
    ) != transport.candidate_content_identity(second, error_factory=RuntimeError)


def test_phase7_candidate_identity_never_executes_repository_fsmonitor(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    home = tmp_path / "home"
    repository.mkdir()
    home.mkdir()
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    subprocess.run(
        ["git", "-C", str(repository), "init", "--quiet"],
        env=environment,
        check=True,
    )
    (repository / "payload").write_bytes(b"candidate bytes\n")
    subprocess.run(
        ["git", "-C", str(repository), "add", "payload"],
        env=environment,
        check=True,
    )

    marker = tmp_path / "fsmonitor-invoked"
    fsmonitor = tmp_path / "fsmonitor"
    fsmonitor.write_text(
        '#!/bin/sh\nprintf invoked > "$1"\nprintf "2\\n"\n',
        encoding="utf-8",
    )
    fsmonitor.chmod(0o700)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "core.fsmonitor",
            f"{fsmonitor} {marker}",
        ],
        env=environment,
        check=True,
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/phase7_compatibility_projection.py"),
            "--public-root",
            str(repository),
            "--expected-public-candidate-sha256",
            "0" * 64,
        ],
        env=environment,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert marker.exists() is False


def test_candidate_identity_closes_fsmonitor_without_config_environment_support(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transport = load("scripts/evidence_transport.py", "legacy_git_identity_transport")
    trusted_git = shutil.which("git", path="/usr/bin:/bin")
    assert trusted_git is not None
    repository = tmp_path / "repository"
    repository.mkdir()
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(
        [trusted_git, "-C", str(repository), "init", "--quiet"],
        env=environment,
        check=True,
    )
    (repository / "payload").write_bytes(b"candidate bytes\n")
    subprocess.run(
        [trusted_git, "-C", str(repository), "add", "payload"],
        env=environment,
        check=True,
    )
    marker = tmp_path / "legacy-git-fsmonitor-invoked"
    fsmonitor = tmp_path / "fsmonitor"
    fsmonitor.write_text(
        '#!/bin/sh\nprintf invoked > "$1"\nprintf "2\\n"\n',
        encoding="utf-8",
    )
    fsmonitor.chmod(0o700)
    subprocess.run(
        [
            trusted_git,
            "-C",
            str(repository),
            "config",
            "core.fsmonitor",
            f"{fsmonitor} {marker}",
        ],
        env=environment,
        check=True,
    )

    current_environment = transport._candidate_git_environment

    def without_config_environment() -> dict[str, str]:
        isolated = current_environment()
        for name in tuple(isolated):
            if name == "GIT_CONFIG_COUNT" or name.startswith(
                ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
            ):
                isolated.pop(name)
        return isolated

    monkeypatch.setattr(
        transport,
        "_candidate_git_environment",
        without_config_environment,
    )

    transport.candidate_content_identity(repository, error_factory=RuntimeError)

    assert marker.exists() is False


def test_phase7_candidate_identity_never_searches_candidate_for_git(
    tmp_path: Path,
) -> None:
    trusted_git = shutil.which("git", path="/usr/bin:/bin")
    assert trusted_git is not None
    repository = tmp_path / "repository"
    home = tmp_path / "home"
    repository.mkdir()
    home.mkdir()
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    subprocess.run(
        [trusted_git, "-C", str(repository), "init", "--quiet"],
        env=environment,
        check=True,
    )
    (repository / "payload").write_bytes(b"candidate bytes\n")
    subprocess.run(
        [trusted_git, "-C", str(repository), "add", "payload"],
        env=environment,
        check=True,
    )

    marker = home / "native-marker"
    candidate_git = repository / "git"
    candidate_git.write_text(
        '#!/bin/sh\nprintf invoked > "$HOME/native-marker"\nexit 1\n',
        encoding="utf-8",
    )
    candidate_git.chmod(0o700)
    environment["PATH"] = ".:/usr/bin:/bin"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/phase7_compatibility_projection.py"),
            "--public-root",
            str(repository),
            "--expected-public-candidate-sha256",
            "0" * 64,
        ],
        env=environment,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert marker.exists() is False


def test_unsigned_host_receipts_cannot_reach_task_witness_authority(
    monkeypatch,
) -> None:
    validator = load(
        "scripts/validate_task_witness.py", "security_task_witness_boundary"
    )
    observed = []

    def observe(*args, **kwargs):
        observed.append((args, kwargs))
        raise AssertionError("retired receipt authority was reached")

    monkeypatch.setattr(validator, "_validator_invocation", observe)

    status = validator.entrypoint_main(
        [
            "/public/repository",
            "--final-release",
            "--candidate-root",
            "/private/candidate",
            "--release-manifest",
            "/private/manifest",
            "--macos-receipt",
            "/private/macos-receipt",
            "--linux-receipt",
            "/private/linux-receipt",
            "--review-evidence",
            "/private/review-evidence",
        ]
    )

    assert status == 1
    assert observed == []


def test_self_authenticating_eval_cli_fails_before_evidence_read(
    monkeypatch,
    capsys,
) -> None:
    gate = load(
        "plugins/versionkeeping/skills/checkpointing-and-publishing-git-work/"
        "scripts/check_eval_gate.py",
        "security_eval_authority_boundary",
    )
    observed = []

    def observe(*args, **kwargs):
        observed.append((args, kwargs))
        raise AssertionError("self-authenticating evidence was read")

    monkeypatch.setattr(gate, "read_json", observe)

    status = gate.main(
        [
            "--manifest",
            "/private/self-asserted-evidence.json",
            "--matrix",
            "/private/self-asserted-matrix.json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status != 0
    assert observed == []
    assert payload["authority"] == "none"
    assert payload["passed"] is False
    assert payload["production_eligible"] is False


def test_combined_stream_locks_and_source_stage_ineligibility_are_bound() -> None:
    mergecraft_lock = json.loads(
        (ROOT / "release/plugin-content-locks/mergecraft.json").read_text()
    )
    versionkeeping_lock = json.loads(
        (ROOT / "release/plugin-content-locks/versionkeeping.json").read_text()
    )
    registration = json.loads(
        (ROOT / "release/task-witness/public-release-registration.json").read_text()
    )
    suite_inventory = json.loads(
        (ROOT / "release/task-witness/tw4-suite-inventory.json").read_text()
    )

    mergecraft_paths = (
        "skills/graphite/scripts/submit_draft_stack.py",
        "skills/publishing-reviewable-prs/scripts/required_review.py",
    )
    versionkeeping_paths = (
        (
            "skills/checkpointing-and-publishing-git-work/scripts/"
            "git_publication/adapter.py"
        ),
        "skills/checkpointing-and-publishing-git-work/scripts/check_eval_gate.py",
        "skills/using-persistent-git-worktrees/scripts/validate_worktree_target.py",
    )

    for relative in mergecraft_paths:
        content = ROOT / "plugins/mergecraft" / relative
        assert mergecraft_lock["files"][relative] == hashlib.sha256(
            content.read_bytes()
        ).hexdigest()
    for relative in versionkeeping_paths:
        content = ROOT / "plugins/versionkeeping" / relative
        assert versionkeeping_lock["files"][relative]["sha256"] == hashlib.sha256(
            content.read_bytes()
        ).hexdigest()
    assert registration["production_eligible"] is False
    assert suite_inventory["runtime_status"] == "retired-source-stage"
