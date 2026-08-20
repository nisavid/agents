from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(relative: str, name: str):
    path = ROOT / relative
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def unexpected_boundary(*_args, **_kwargs):
    raise AssertionError("retired runtime reached a vulnerable boundary")


def test_native_qualification_fails_before_candidate_argument_parsing(monkeypatch):
    qualification = load(
        "scripts/run_task_witness_qualification.py", "qualification_containment"
    )
    monkeypatch.setattr(qualification, "parse_args", unexpected_boundary)

    assert qualification.main() == 1


def test_native_qualification_suite_fails_before_suite_selection(monkeypatch):
    suite = load(
        "scripts/run_task_witness_qualification_suite.py",
        "qualification_suite_containment",
    )
    monkeypatch.setattr(suite, "_suite_id_from_argv", unexpected_boundary)

    assert suite.entrypoint_main() == 1


def test_tw4_suite_inventory_is_explicitly_retired_at_the_exact_entrypoint():
    inventory = json.loads(
        (ROOT / "release/task-witness/tw4-suite-inventory.json").read_text()
    )
    assert inventory["runtime_status"] == "retired-source-stage"
    first = inventory["entries"][0]

    completed = subprocess.run(
        [sys.executable, *first["argv"]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "native candidate execution is unavailable" in completed.stderr


def test_task_witness_later_release_modes_reject_before_path_conversion(monkeypatch):
    validator = load("scripts/validate_task_witness.py", "task_witness_containment")
    observed_paths = []

    def observe_path(*args, **kwargs):
        observed_paths.append((args, kwargs))
        return unexpected_boundary(*args, **kwargs)

    monkeypatch.setattr(validator, "_absolute_path", observe_path)
    private_sentinel = "/private/owner-only/candidate"

    assert (
        validator.entrypoint_main(
            [
                "/public/repository",
                "--qualification",
                private_sentinel,
            ]
        )
        == 1
    )
    assert observed_paths == []
    assert (
        validator.entrypoint_main(
            [
                "/public/repository",
                "--final-release",
                "--candidate-root",
                private_sentinel,
                "--release-manifest",
                "/private/owner-only/manifest",
                "--macos-receipt",
                "/private/owner-only/macos",
                "--linux-receipt",
                "/private/owner-only/linux",
                "--review-evidence",
                "/private/owner-only/review",
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    ("relative", "name", "arguments"),
    (
        ("scripts/run_phase7_composed_matrix.py", "composed_containment", None),
        ("scripts/run_phase7_terminal_proof.py", "terminal_containment", []),
        (
            "scripts/run_phase7_production_integration.py",
            "production_containment",
            [],
        ),
    ),
)
def test_phase7_native_entrypoints_fail_before_argument_parsing(
    monkeypatch, relative, name, arguments
):
    module = load(relative, name)
    monkeypatch.setattr(module.argparse.ArgumentParser, "parse_args", unexpected_boundary)

    status = module.entrypoint_main()

    assert status == 1


def test_public_release_production_fails_before_argument_parsing(monkeypatch):
    validator = load("scripts/validate_public_release.py", "release_containment")
    monkeypatch.setattr(
        validator.argparse.ArgumentParser,
        "parse_args",
        unexpected_boundary,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_public_release.py",
            "/public/repository",
            "--private-producer-witness",
            "/private/owner-only/witness",
        ],
    )

    assert validator.entrypoint_main() == 1


def test_public_release_source_stage_reaches_the_validated_control(monkeypatch):
    validator = load("scripts/validate_public_release.py", "release_source_stage")
    monkeypatch.setattr(validator, "main", lambda: 0)
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_public_release.py", str(ROOT), "--source-stage"],
    )

    assert validator.entrypoint_main() == 0


def test_public_release_rejects_private_hidden_operand_before_parser(monkeypatch):
    validator = load("scripts/validate_public_release.py", "release_hidden_operand")
    monkeypatch.setattr(validator, "main", unexpected_boundary)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_public_release.py",
            str(ROOT),
            "--source-stage",
            "--prepared-supervisor-source-sha256",
            "/private/owner-only/not-a-source-identity",
        ],
    )

    assert validator.entrypoint_main() == 1


def test_current_task_witness_executable_fails_before_client_setup(monkeypatch):
    client = load(
        "plugins/task-witness/client/task_witness_client.py",
        "task_witness_client_containment",
    )
    monkeypatch.setattr(client, "InvocationState", unexpected_boundary)
    monkeypatch.setattr(client, "_parse_public_arguments", unexpected_boundary)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "task_witness_client.py",
            "validate",
            "--bundle",
            "/private/owner-only/review-bundle",
        ],
    )

    assert client.entrypoint_main() == client.EXIT_INSTALLATION


def test_current_task_witness_client_is_separate_from_the_frozen_bridge():
    validator = load(
        "scripts/validate_task_witness.py", "task_witness_current_boundary"
    )
    current = (ROOT / "plugins/task-witness/client/task_witness_client.py").read_bytes()
    bridge = (
        ROOT
        / "release/task-witness/migration/bridge/client/task_witness_client.py"
    ).read_bytes()

    assert current != bridge
    assert validator._validate_current_client_boundary(current) is None
    assert (
        validator.hashlib.sha256(bridge).hexdigest()
        == validator.EXPECTED_BRIDGE_IDENTITIES["bridge"]["client_sha256"]
    )

    with pytest.raises(ValueError, match="current client release profile drift"):
        validator._validate_current_client_boundary(
            current.replace(b'tw4-current', b'tw4-drift', 1)
        )


def test_mergecraft_required_review_fails_before_front_door_or_supervisor(monkeypatch):
    required_review = load(
        "plugins/mergecraft/skills/publishing-reviewable-prs/scripts/required_review.py",
        "required_review_containment",
    )
    monkeypatch.setattr(
        required_review,
        "_authenticated_front_door",
        unexpected_boundary,
    )
    monkeypatch.setattr(required_review, "_supervised_process", unexpected_boundary)

    with pytest.raises(required_review.PublicationError, match="unavailable"):
        required_review._invoke_task_witness(
            Path("/private/owner-only/review-bundle")
        )


def test_supervisor_rejects_later_release_modes_before_repository_or_launch(monkeypatch):
    supervisor = load(
        "scripts/supervise_prepared_release_validation.py",
        "prepared_supervisor_containment",
    )
    monkeypatch.setattr(supervisor, "supervisor_belongs_to_repository", unexpected_boundary)
    monkeypatch.setattr(supervisor, "run_prepared_validation", unexpected_boundary)

    assert (
        supervisor.entrypoint_main(
            [
                "public-release",
                str(ROOT),
                "--private-producer-witness",
                "/private/owner-only/witness",
            ]
        )
        == 2
    )
    assert (
        supervisor.entrypoint_main(["phase7-production", str(ROOT)])
        == 2
    )


def test_supervisor_child_bypass_rejects_before_exec(monkeypatch):
    supervisor = load(
        "scripts/supervise_prepared_release_validation.py",
        "prepared_supervisor_child_containment",
    )
    monkeypatch.setattr(supervisor.os, "execve", unexpected_boundary)
    private_sentinel = "/private/owner-only/evidence"
    command = [
        sys.executable,
        "-I",
        "-B",
        str(ROOT / "scripts/run_phase7_terminal_proof.py"),
        str(ROOT),
        "--private-evidence",
        private_sentinel,
        "unused",
    ]

    assert (
        supervisor.entrypoint_main(
            [supervisor.VALIDATION_CHILD_MODE, *command]
        )
        == 2
    )


def test_supervisor_retains_only_the_source_stage_control():
    supervisor = load(
        "scripts/supervise_prepared_release_validation.py",
        "prepared_supervisor_source_stage",
    )
    command = supervisor.validation_command("source-stage", ROOT, [])

    assert command[:3] == [sys.executable, "-I", "-B"]
    assert command[3] == str(ROOT / "scripts/validate_public_release.py")
    assert command[4:6] == [str(ROOT), "--source-stage"]
    assert not any("/private/owner-only" in argument for argument in command)


def test_shell_wrapper_rejects_later_release_before_starting_prepared_python(
    tmp_path: Path,
):
    marker = tmp_path / "prepared-python-started"
    prepared_python = tmp_path / "prepared-python"
    prepared_python.write_text(
        f'#!/bin/sh\nprintf started > "{marker!s}"\n'
    )
    prepared_python.chmod(0o700)
    wrapper = ROOT / "scripts/run_prepared_release_validation.sh"

    completed = subprocess.run(
        [
            "/bin/sh",
            str(wrapper),
            "phase7-production",
            str(prepared_python),
            str(ROOT),
            "--private-repository",
            "/private/owner-only/repository",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert not marker.exists()


def test_shell_wrapper_runs_only_the_source_stage_control(tmp_path: Path):
    marker = tmp_path / "prepared-python-arguments"
    prepared_python = tmp_path / "prepared-python"
    prepared_python.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(marker))}\n"
    )
    prepared_python.chmod(0o700)
    wrapper = ROOT / "scripts/run_prepared_release_validation.sh"

    completed = subprocess.run(
        [
            "/bin/sh",
            str(wrapper),
            "source-stage",
            str(prepared_python),
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert marker.read_text().splitlines() == [
        "-I",
        "-B",
        str(ROOT / "scripts/supervise_prepared_release_validation.py"),
        "source-stage",
        str(ROOT),
    ]
