from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_skill_routing_eval.py"


def load_runner():
    specification = importlib.util.spec_from_file_location("skill_routing_eval", SCRIPT)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def frozen_copy(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "candidate"
    shutil.copytree(
        ROOT,
        repository,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Routing Fixture"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "routing@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "test: frozen routing candidate"],
        cwd=repository,
        check=True,
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    return repository, revision


def fixture_command(
    repository: Path, revision: str, output: Path, limit: int
) -> list[str]:
    return [
        sys.executable,
        str(SCRIPT),
        "--repo",
        str(repository),
        "--output",
        str(output),
        "--candidate-revision",
        revision,
        "--adapter",
        "fixture",
        "--case-limit",
        str(limit),
    ]


def test_routing_definition_has_the_complete_inventory():
    runner = load_runner()
    bundle = runner.load_definition(ROOT)
    cases = bundle.cases
    assert len(cases) == 113
    assert runner.case_counts(cases) == {
        "cold_start": 21,
        "explicit_invocation": 21,
        "trigger": 71,
        "total": 113,
    }
    assert (
        sum(
            case.expected_skills == (case.skill_name,)
            for case in cases
            if case.tier == "trigger"
        )
        == 37
    )
    assert (
        sum(
            case.expected_skills != (case.skill_name,)
            for case in cases
            if case.tier == "trigger"
        )
        == 34
    )
    assert {
        "versionkeeping:using-persistent-git-worktrees",
        "mergecraft:writing-reviewable-pr-descriptions",
        "mergecraft:publishing-reviewable-prs",
        "mergecraft:graphite",
        "mergecraft:addressing-pr-review-feedback",
        "mergecraft:resuming-reviewed-prs",
    } <= {case.target for case in cases}


def test_definition_rejects_a_negative_expectation_that_still_selects_target(
    tmp_path: Path,
):
    runner = load_runner()
    snapshot = tmp_path / "snapshot"
    shutil.copytree(ROOT, snapshot, ignore=shutil.ignore_patterns(".git"))
    definition_path = snapshot / "evals/skill-routing-matrix.json"
    definition = json.loads(definition_path.read_text())
    supplemental = definition["skills"][0]["supplemental"]
    supplemental["negative_expected_skills"] = [
        "choosing-agent-models",
        "delegating-cross-agent-work",
    ]
    definition_path.write_text(json.dumps(definition))

    with pytest.raises(runner.RoutingError, match="negative.*target"):
        runner.load_definition(snapshot)


def test_candidate_identity_canonicalizes_the_release_repository_origin(
    tmp_path: Path,
):
    runner = load_runner()
    repository, revision = frozen_copy(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:nisavid/agents.git"],
        cwd=repository,
        check=True,
    )

    candidate, _archive = runner.candidate_git_identity(repository, revision)

    assert candidate["repository"] == "https://github.com/nisavid/agents"


@pytest.mark.parametrize(
    "origin",
    (
        "https://secret-token@github.com/nisavid/agents.git",
        "https://github.com/fork/agents.git",
    ),
)
def test_candidate_identity_rejects_untrusted_origins_without_echoing_them(
    tmp_path: Path, origin: str
):
    runner = load_runner()
    repository, revision = frozen_copy(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", origin], cwd=repository, check=True
    )

    with pytest.raises(runner.RoutingError) as captured:
        runner.candidate_git_identity(repository, revision)

    assert "secret-token" not in str(captured.value)


def test_production_run_rejects_a_candidate_without_the_canonical_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = load_runner()
    repository = tmp_path / "candidate"
    repository.mkdir()
    candidate = {
        "revision": "1" * 40,
        "tree_oid": "2" * 40,
        "archive_sha256": "sha256:" + "3" * 64,
        "archive_relpath": "artifacts/candidate/tree.tar",
        "repository": "",
    }
    monkeypatch.setattr(
        runner, "candidate_git_identity", lambda *_args: (candidate, b"")
    )
    monkeypatch.setattr(
        runner,
        "resolve_claude_identity",
        lambda: (_ for _ in ()).throw(
            AssertionError("runtime identity must not be resolved")
        ),
    )
    arguments = runner.argparse.Namespace(
        repo=repository,
        output=tmp_path / "evidence",
        definition=runner.DEFINITION_DEFAULT,
        candidate_revision=candidate["revision"],
        adapter="claude-cli",
        model="claude-sonnet-5",
        case_limit=None,
        timeout_seconds=30,
    )

    with pytest.raises(runner.RoutingError, match="canonical.*repository"):
        runner.run(arguments)


@pytest.mark.parametrize("model", ("sonnet", "opus", "claude-test"))
def test_production_run_rejects_a_nonexact_requested_model_before_runtime_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str
):
    runner = load_runner()
    repository = tmp_path / "candidate"
    repository.mkdir()
    candidate = {
        "revision": "1" * 40,
        "tree_oid": "2" * 40,
        "archive_sha256": "sha256:" + "3" * 64,
        "archive_relpath": "artifacts/candidate/tree.tar",
        "repository": runner.CANONICAL_REPOSITORY_URL,
    }
    monkeypatch.setattr(
        runner, "candidate_git_identity", lambda *_args: (candidate, b"")
    )
    monkeypatch.setattr(
        runner,
        "resolve_claude_identity",
        lambda: (_ for _ in ()).throw(
            AssertionError("runtime identity must not be resolved")
        ),
    )
    arguments = runner.argparse.Namespace(
        repo=repository,
        output=tmp_path / "evidence",
        definition=runner.DEFINITION_DEFAULT,
        candidate_revision=candidate["revision"],
        adapter="claude-cli",
        model=model,
        case_limit=None,
        timeout_seconds=30,
    )

    with pytest.raises(runner.RoutingError, match="exact Claude model"):
        runner.run(arguments)


@pytest.mark.parametrize("model", ("sonnet", "opus", "claude-test"))
def test_production_configuration_rejects_nonexact_model_identity(model: str):
    runner = load_runner()

    with pytest.raises(runner.RoutingError, match="exact Claude model"):
        runner.configuration("claude-cli", model)


def test_fixture_mode_retains_routing_events_and_isolation_for_a_proper_subset(
    tmp_path: Path,
):
    runner = load_runner()
    repository, revision = frozen_copy(tmp_path)
    output = tmp_path / "evidence"
    completed = subprocess.run(
        fixture_command(repository, revision, output, 7),
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((output / runner.MANIFEST_NAME).read_text())
    assert manifest["counts"] == {
        "cold_start": 2,
        "explicit_invocation": 2,
        "trigger": 3,
        "total": 7,
    }
    assert "observable Claude CLI Skill tool use" in manifest["claim"]
    assert (output / manifest["candidate"]["archive_relpath"]).is_file()
    assert (output / manifest["isolation_relpath"]).is_file()
    isolation = json.loads((output / manifest["isolation_relpath"]).read_text())
    assert isolation["xdg_state"] == "fresh"
    assert isolation["tmp"] == "fresh"
    assert manifest["attempts"]
    assert all(attempt["status"] == "success" for attempt in manifest["attempts"])
    assert all(
        record["selected_skills"] == record["expected_skills"]
        for record in manifest["records"]
    )
    assert all(record["usage"]["input_tokens"] > 0 for record in manifest["records"])
    assert all(record["total_cost_usd"] == 0 for record in manifest["records"])
    runner.validate_evidence(output, require_production=False)


@pytest.mark.parametrize("redirect", ("leaf", "parent"))
def test_fixture_runner_rejects_symlinked_output_components_without_writing_target(
    tmp_path: Path, redirect: str
):
    repository, revision = frozen_copy(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"preserve me\n")
    if redirect == "leaf":
        output = tmp_path / "evidence"
        output.symlink_to(outside, target_is_directory=True)
    else:
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(outside, target_is_directory=True)
        output = linked_parent / "evidence"

    completed = subprocess.run(
        fixture_command(repository, revision, output, 2),
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 2
    assert sentinel.read_bytes() == b"preserve me\n"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]


def test_evidence_validation_compares_the_declared_candidate_repository(
    tmp_path: Path,
):
    runner = load_runner()
    repository, revision = frozen_copy(tmp_path)
    output = tmp_path / "evidence"
    completed = subprocess.run(
        fixture_command(repository, revision, output, 1),
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((output / runner.MANIFEST_NAME).read_text())
    expected_candidate = {
        **manifest["candidate"],
        "repository": runner.CANONICAL_REPOSITORY_URL,
    }

    with pytest.raises(runner.RoutingError, match="candidate repository mismatch"):
        runner.validate_evidence(
            output,
            expected_candidate=expected_candidate,
            require_production=False,
        )


def test_fixture_mode_rejects_a_full_or_empty_matrix(tmp_path: Path):
    repository, revision = frozen_copy(tmp_path)
    for limit in (None, "113", "0"):
        command = [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repository),
            "--output",
            str(tmp_path / f"evidence-{limit}"),
            "--candidate-revision",
            revision,
            "--adapter",
            "fixture",
        ]
        if limit is not None:
            command.extend(("--case-limit", limit))
        completed = subprocess.run(command, text=True, capture_output=True)
        assert completed.returncode == 2
        assert "proper --case-limit subset" in completed.stderr


def test_observed_route_requires_exact_model_usage_cost_and_selected_skill(
    tmp_path: Path,
):
    runner = load_runner()
    case = runner.RoutingCase(
        "case", "trigger", "mergecraft:graphite", "query", ("graphite",)
    )
    stream = runner.fixture_stream(case, ["graphite"], tmp_path)
    observation = runner.observe_route(
        stream,
        case,
        ["graphite"],
        expected_cwd=tmp_path,
        expected_claude_version="fixture-1",
        expected_model="fixture-model",
    )
    assert observation["selected_skills"] == ["graphite"]
    assert observation["usage"] == {"input_tokens": 1, "output_tokens": 1}
    assert observation["total_cost_usd"] == 0

    wrong_model = stream.replace(b'"fixture-model"', b'"other-model"')
    with pytest.raises(runner.RoutingError, match="model"):
        runner.observe_route(
            wrong_model,
            case,
            ["graphite"],
            expected_cwd=tmp_path,
            expected_claude_version="fixture-1",
            expected_model="fixture-model",
        )

    missing_cost = (
        b"\n".join(line for line in stream.splitlines() if line).replace(
            b',"total_cost_usd":0.0', b""
        )
        + b"\n"
    )
    with pytest.raises(runner.RoutingError, match="cost"):
        runner.observe_route(
            missing_cost,
            case,
            ["graphite"],
            expected_cwd=tmp_path,
            expected_claude_version="fixture-1",
            expected_model="fixture-model",
        )


@pytest.mark.parametrize("alias", ("sonnet", "opus", "claude-test"))
def test_observed_route_rejects_nonexact_requested_and_observed_model_identity(
    tmp_path: Path, alias: str
):
    runner = load_runner()
    case = runner.RoutingCase(
        "case", "trigger", "mergecraft:graphite", "query", ("graphite",)
    )
    stream = runner.fixture_stream(case, ["graphite"], tmp_path).replace(
        b'"fixture-model"', json.dumps(alias).encode()
    )

    with pytest.raises(runner.RoutingError, match="exact Claude model"):
        runner.observe_route(
            stream,
            case,
            ["graphite"],
            expected_cwd=tmp_path,
            expected_claude_version="fixture-1",
            expected_model=alias,
        )


def test_observed_route_accepts_the_complete_reviewed_builtin_agent_inventory(
    tmp_path: Path,
):
    runner = load_runner()
    case = runner.RoutingCase(
        "case", "trigger", "mergecraft:graphite", "query", ("graphite",)
    )
    events = [
        json.loads(line)
        for line in runner.fixture_stream(case, ["graphite"], tmp_path).splitlines()
    ]
    events[0]["agents"] = ["Plan", "general-purpose", "Explore", "claude"]
    stream = b"\n".join(runner.canonical(event) for event in events) + b"\n"

    observation = runner.observe_route(
        stream,
        case,
        ["graphite"],
        expected_cwd=tmp_path,
        expected_claude_version="fixture-1",
        expected_model="fixture-model",
    )

    assert observation["init"]["agents"] == [
        "Plan",
        "general-purpose",
        "Explore",
        "claude",
    ]


@pytest.mark.parametrize(
    "agents",
    (
        ["claude", "Explore", "general-purpose"],
        ["claude", "Explore", "general-purpose", "Plan", "custom"],
        ["claude", "Explore", "general-purpose", "Plan", "Plan"],
        [
            {"name": "claude"},
            {"name": "Explore"},
            {"name": "general-purpose"},
            {"name": "Plan"},
        ],
    ),
)
def test_observed_route_rejects_nonexact_builtin_agent_inventory(
    tmp_path: Path, agents: list[object]
):
    runner = load_runner()
    case = runner.RoutingCase(
        "case", "trigger", "mergecraft:graphite", "query", ("graphite",)
    )
    events = [
        json.loads(line)
        for line in runner.fixture_stream(case, ["graphite"], tmp_path).splitlines()
    ]
    events[0]["agents"] = agents
    stream = b"\n".join(runner.canonical(event) for event in events) + b"\n"

    with pytest.raises(runner.RoutingError, match="agents|agent discovery"):
        runner.observe_route(
            stream,
            case,
            ["graphite"],
            expected_cwd=tmp_path,
            expected_claude_version="fixture-1",
            expected_model="fixture-model",
        )


def test_observed_route_rejects_mixed_assistant_response_ids(tmp_path: Path):
    runner = load_runner()
    case = runner.RoutingCase(
        "case", "trigger", "mergecraft:graphite", "query", ("graphite",)
    )
    events = [
        json.loads(line)
        for line in runner.fixture_stream(case, ["graphite"], tmp_path).splitlines()
    ]
    events.insert(
        2,
        {
            "type": "assistant",
            "message": {
                "id": "different-response",
                "model": "fixture-model",
                "content": [{"type": "text", "text": "fixture"}],
            },
        },
    )
    stream = b"\n".join(runner.canonical(event) for event in events) + b"\n"

    with pytest.raises(runner.RoutingError, match="assistant response IDs"):
        runner.observe_route(
            stream,
            case,
            ["graphite"],
            expected_cwd=tmp_path,
            expected_claude_version="fixture-1",
            expected_model="fixture-model",
        )


def test_observed_route_requires_final_assistant_text_to_bind_the_result(
    tmp_path: Path,
):
    runner = load_runner()
    case = runner.RoutingCase(
        "case", "trigger", "mergecraft:graphite", "query", ("graphite",)
    )
    events = [
        json.loads(line)
        for line in runner.fixture_stream(case, ["graphite"], tmp_path).splitlines()
    ]
    events[1]["message"]["content"].append({"type": "text", "text": "bound result"})
    events[2]["result"] = "different result"
    stream = b"\n".join(runner.canonical(event) for event in events) + b"\n"

    with pytest.raises(runner.RoutingError, match="assistant text.*result"):
        runner.observe_route(
            stream,
            case,
            ["graphite"],
            expected_cwd=tmp_path,
            expected_claude_version="fixture-1",
            expected_model="fixture-model",
        )


def test_claude_transport_routes_state_and_temporary_files_to_case_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = load_runner()
    case = runner.RoutingCase(
        "case", "trigger", "mergecraft:graphite", "query", ("graphite",)
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    isolation = {
        key: str(tmp_path / key)
        for key in (
            "home",
            "claude_config",
            "xdg_config",
            "xdg_cache",
            "xdg_data",
            "xdg_state",
            "tmp",
        )
    }
    for path in isolation.values():
        Path(path).mkdir()
    captured = {}
    runtime_identity = {
        "path": "/private/tmp/claude",
        "sha256": "sha256:" + "1" * 64,
        "version": "2.1.215 (Claude Code)",
    }
    monkeypatch.setattr(runner, "resolve_claude_identity", lambda: runtime_identity)

    def run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, b"stream", b"")

    monkeypatch.setattr(runner.subprocess, "run", run)
    runner.claude_transport(
        case,
        workspace,
        isolation,
        runtime_identity,
        "claude-sonnet-5",
        30,
    )
    environment = captured["environment"]
    assert environment["HOME"] == isolation["home"]
    assert environment["CLAUDE_CONFIG_DIR"] == isolation["claude_config"]
    assert environment["XDG_STATE_HOME"] == isolation["xdg_state"]
    assert environment["TMPDIR"] == isolation["tmp"]
    assert set(environment) == {
        "HOME",
        "CLAUDE_CONFIG_DIR",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
        "PATH",
        "LANG",
    }


def test_claude_runtime_identity_uses_the_exact_control_plane_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = load_runner()
    expected = {
        "path": str(tmp_path / "claude"),
        "sha256": runner.digest(b"frozen claude executable"),
        "version": "2.1.215 (Claude Code)",
    }

    def shared_resolver(executable, **arguments):
        assert executable == "claude"
        assert arguments["error_factory"] is runner.RoutingError
        assert arguments["display_name"] == "Claude Code"
        assert arguments["version_validator"](expected["version"])
        assert not arguments["version_validator"]("not Claude Code")
        return expected

    monkeypatch.setattr(runner, "resolve_executable_identity", shared_resolver)

    assert runner.resolve_claude_identity() == expected


def test_claude_transport_reprobes_runtime_before_and_after_the_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = load_runner()
    case = runner.RoutingCase(
        "case", "trigger", "mergecraft:graphite", "query", ("graphite",)
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    isolation = {
        key: str(tmp_path / key)
        for key in (
            "home",
            "claude_config",
            "xdg_config",
            "xdg_cache",
            "xdg_data",
            "xdg_state",
            "tmp",
        )
    }
    for path in isolation.values():
        Path(path).mkdir()
    baseline = {
        "path": "/private/tmp/claude",
        "sha256": "sha256:" + "1" * 64,
        "version": "2.1.215 (Claude Code)",
    }
    drifted = {**baseline, "sha256": "sha256:" + "2" * 64}
    observations = iter((baseline, drifted))
    monkeypatch.setattr(runner, "resolve_claude_identity", lambda: next(observations))
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, b"stream", b""
        ),
    )

    with pytest.raises(runner.TransportFailure, match="executable identity drift"):
        runner.claude_transport(
            case,
            workspace,
            isolation,
            baseline,
            "claude-sonnet-5",
            30,
        )


def test_production_evidence_accepts_only_the_exact_runtime_identity_schema():
    runner = load_runner()
    runtime_identity = {
        "path": "/private/tmp/claude",
        "sha256": "sha256:" + "1" * 64,
        "version": "2.1.215 (Claude Code)",
    }

    assert runner.validate_binary_identity(runtime_identity, True) == runtime_identity


def test_production_configuration_binds_the_exact_runtime_identity():
    runner = load_runner()
    runtime_identity = {
        "path": "/private/tmp/claude",
        "sha256": "sha256:" + "1" * 64,
        "version": "2.1.215 (Claude Code)",
    }

    configuration = runner.configuration(
        "claude-cli", "claude-sonnet-5", runtime_identity
    )

    assert configuration["runtime"] == runtime_identity


def test_resume_adopts_checkpoint_written_before_run_state_update(tmp_path: Path):
    runner = load_runner()
    repository, revision = frozen_copy(tmp_path)
    output = tmp_path / "evidence"
    command = fixture_command(repository, revision, output, 2)
    first = subprocess.run(command, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr

    state = runner.validate_signature(
        runner.read_json(output / "run-state.json"), "run-state"
    )
    cases = state["run_contract"]["selected_case_ids"]
    runner.write_json(
        output / "run-state.json",
        runner.state_document(state["run_contract"], cases[:1]),
    )
    attempts_before = sorted(
        path.relative_to(output)
        for path in output.glob("attempts/**/*")
        if path.is_file()
    )

    resumed = subprocess.run(command, text=True, capture_output=True)
    assert resumed.returncode == 0, resumed.stderr
    attempts_after = sorted(
        path.relative_to(output)
        for path in output.glob("attempts/**/*")
        if path.is_file()
    )
    assert attempts_after == attempts_before
    resumed_state = runner.validate_signature(
        runner.read_json(output / "run-state.json"), "run-state"
    )
    assert resumed_state["completed_case_ids"] == cases


def test_manifest_seals_typed_timeout_attempts_and_raw_streams(tmp_path: Path):
    runner = load_runner()
    repository, revision = frozen_copy(tmp_path)
    output = tmp_path / "evidence"

    original_transport = runner.FIXTURE_TRANSPORT
    calls = 0

    def fail_once(case, skills, cwd):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise runner.TransportFailure(
                "fixture interruption",
                stdout=b'{"partial":true}\n',
                stderr=b"fixture stderr\n",
                returncode=9,
                timed_out=True,
            )
        return original_transport(case, skills, cwd)

    runner.FIXTURE_TRANSPORT = fail_once
    bundle = runner.load_definition(repository)
    candidate, archive = runner.candidate_git_identity(repository, revision)
    snapshot = tmp_path / "snapshot"
    runner.extract_archive(archive, snapshot)
    selected = bundle.cases[:1]
    config = runner.configuration("fixture", None)
    isolation = runner.isolation_contract()
    contract = {
        "schema_version": 2,
        "candidate": candidate,
        "definition": bundle.evidence(),
        "adapter": "fixture",
        "binary_identity": {"adapter": "fixture"},
        "requested_model": None,
        "configuration": config,
        "isolation": isolation,
        "timeout_seconds": 300,
        "selected_case_ids": [selected[0].id],
    }
    runner.load_or_create_state(output, contract)
    runner.ensure_artifact(output, candidate["archive_relpath"], archive)
    runner.ensure_artifact(
        output, "artifacts/configuration.json", runner.json_file_bytes(config)
    )
    runner.ensure_artifact(
        output, "artifacts/isolation.json", runner.json_file_bytes(isolation)
    )
    with pytest.raises(runner.TransportFailure):
        runner.execute_case(
            output,
            snapshot,
            bundle,
            selected[0],
            "fixture",
            {"adapter": "fixture"},
            None,
            300,
        )
    runner.execute_case(
        output,
        snapshot,
        bundle,
        selected[0],
        "fixture",
        {"adapter": "fixture"},
        None,
        300,
    )
    runner.write_json(
        output / "run-state.json", runner.state_document(contract, [selected[0].id])
    )
    manifest = runner.build_manifest(output, contract, bundle, selected)
    assert [attempt["status"] for attempt in manifest["attempts"]] == [
        "timeout",
        "success",
    ]
    runner.validate_evidence(output, require_production=False)

    failed_raw = output / manifest["attempts"][0]["stdout_relpath"]
    failed_raw.unlink()
    with pytest.raises(runner.RoutingError, match="attempt.*stdout"):
        runner.validate_evidence(output, require_production=False)
