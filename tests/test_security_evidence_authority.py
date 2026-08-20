from __future__ import annotations

import hashlib
import importlib.util
import json
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
