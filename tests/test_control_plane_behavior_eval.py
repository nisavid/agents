from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_control_plane_eval.py"
GATE = (
    ROOT
    / "plugins/versionkeeping/skills/checkpointing-and-publishing-git-work/scripts/check_eval_gate.py"
)
DEFINITION = ROOT / "evals/control-plane-matrix.json"


def load_runner():
    specification = importlib.util.spec_from_file_location("control_plane_eval", RUNNER)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class LocalProductionAdapter:
    name = "claude-cli"
    executor_model = "claude-sonnet-5"
    grader_model = "claude-opus-4-8"
    executor_effort = "high"
    grader_effort = "high"
    executor_system_prompt = "Respond to the supplied user message."
    grader_system_prompt = (
        "Assess every labeled response against every supplied criterion. Return strict "
        "JSON with one grades array preserving the supplied label order."
    )
    runtime_identity = {
        "path": "/fixture/bin/claude",
        "sha256": "sha256:" + "a" * 64,
        "version": "fixture-local-claude",
    }

    def __init__(self, runner):
        self.runner = runner

    def result(self, response, request, model_version, local_correlation_id):
        local_correlation_id = local_correlation_id or str(self.runner.uuid.uuid4())
        response_id = str(self.runner.uuid.uuid4())
        session_id = str(self.runner.uuid.uuid4())
        now = self.runner.timestamp()
        init_stream = {
            "agents": [],
            "mcp_servers": [],
            "plugins": [],
            "skills": [],
            "slash_commands": [],
            "tools": [],
        }
        events = [
            {
                "type": "system",
                "subtype": "init",
                "session_id": session_id,
                "model": model_version,
                **init_stream,
            },
            {
                "type": "assistant",
                "message": {
                    "id": response_id,
                    "model": model_version,
                    "content": [{"type": "text", "text": response.decode()}],
                },
            },
            {
                "type": "result",
                "result": response.decode(),
                "session_id": session_id,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        ]
        return self.runner.TransportResult(
            response=response,
            local_correlation_id=local_correlation_id,
            response_id=response_id,
            session_id=session_id,
            model_version=model_version,
            input_tokens=max(1, len(request) // 4),
            output_tokens=max(1, len(response) // 4),
            started_at=now,
            finished_at=now,
            init_stream=init_stream,
            raw_transport=b"\n".join(
                self.runner.canonical_bytes(event) for event in events
            ),
        )

    def execute(self, request, workspace, local_correlation_id=None):
        document = json.loads(request)
        response = (
            b"The supplied workflow governs this response; its required boundaries are satisfied."
            if document.get("bundle_files")
            else b"The raw scenario alone does not establish the governed workflow."
        )
        return self.result(
            response,
            request,
            self.executor_model,
            local_correlation_id,
        )

    def grade(self, request, workspace, local_correlation_id=None):
        document = json.loads(request)
        grades = []
        for output in document["outputs"]:
            passed = "required boundaries are satisfied" in output["response"]
            grades.append(
                {
                    "output_id": output["output_id"],
                    "expectations": [
                        {
                            "id": expectation["id"],
                            "passed": passed,
                            "evidence_sha256": self.runner.digest_bytes(
                                output["response"].encode()
                            ),
                        }
                        for expectation in document["rubric"]["expectations"]
                    ],
                }
            )
        response = self.runner.canonical_bytes({"grades": grades})
        return self.result(
            response,
            request,
            self.grader_model,
            local_correlation_id,
        )


def build_local_production_evidence(tmp_path: Path, monkeypatch):
    runner = load_runner()
    repository = tmp_path / "candidate"
    shutil.copytree(
        ROOT,
        repository,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Eval Fixture"], cwd=repository, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "eval-fixture@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "https://github.com/nisavid/agents.git",
        ],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "test: freeze production fixture"],
        cwd=repository,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    definition_path = repository / "evals/control-plane-matrix.json"
    definition = json.loads(definition_path.read_text())
    mapping = {
        "schema_version": 1,
        "skills": {},
    }
    for index, skill in enumerate(definition["skills"]):
        entrypoint = Path(skill["entrypoint"])
        incumbent_root = repository / Path(*entrypoint.parts[:2])
        mapping["skills"][skill["id"]] = {
            "declared_calls": skill["companions"],
            "entrypoints": [
                entrypoint.relative_to(Path(*entrypoint.parts[:2])).as_posix()
            ],
            "full_tree_lock_sha256": runner.full_tree_lock(incumbent_root),
            "repository": "fixture://incumbent",
            "revision": f"incumbent-{index}",
            "root": str(incumbent_root),
        }
    mapping_path = tmp_path / "incumbents.json"
    mapping_path.write_text(json.dumps(mapping))
    output = tmp_path / "production-evidence"
    monkeypatch.setattr(
        runner,
        "ClaudeCliAdapter",
        lambda *_args, **_kwargs: LocalProductionAdapter(runner),
    )
    args = SimpleNamespace(
        adapter="claude",
        candidate_repository="https://github.com/nisavid/agents",
        candidate_revision=head,
        claude_executable="claude",
        claude_timeout_seconds=300,
        definition=definition_path,
        executor_model="claude-sonnet-5",
        grader_model="claude-opus-4-8",
        incumbents=mapping_path,
        output=output,
        repo=repository,
        scenario_limit=None,
    )
    assert runner.run(args) == 0
    return runner, output


def incumbent_mapping(path: Path, skill_count: int) -> Path:
    definition = json.loads(DEFINITION.read_text())
    selected = definition["skills"][:skill_count]
    mapping = {
        "schema_version": 1,
        "skills": {
            skill["id"]: {
                "declared_calls": [],
                "entrypoints": [skill["entrypoint"]],
                "repository": "fixture://incumbent",
                "revision": f"incumbent-{index}",
                "root": str(ROOT),
            }
            for index, skill in enumerate(selected)
        },
    }
    path.write_text(json.dumps(mapping))
    return path


def test_definition_is_exact_public_inventory_and_scenario_map():
    definition = json.loads(DEFINITION.read_text())
    skills = definition["skills"]
    assert len(skills) == 21
    counts: dict[str, int] = {}
    for skill in skills:
        plugin = skill["id"].split(":", 1)[0]
        counts[plugin] = counts.get(plugin, 0) + 1
    assert counts == {
        "mergecraft": 9,
        "rolecasting": 2,
        "tricritical": 7,
        "versionkeeping": 3,
    }
    assert [skill["scenario"]["id"] for skill in skills] == [
        "cursor-grok-consequential-review",
        "insufficient-foreign-isolation-blocked",
        "critic-isolation",
        "intent-no-spec",
        "runtime-green-test-false-positive",
        "structure-smell-negative",
        "severity-calibration",
        "pre-edit-identity-mismatch",
        "loop-incomplete-successful-verification",
        "merged-remote-ref-cleanup",
        "non-default-fork-sync",
        "persistent-worktree-containment",
        "writer-owns-content",
        "publisher-owns-actuation",
        "graphite-transport-boundary",
        "feedback-natural-reply",
        "authorized-reply-and-resolution-boundary",
        "resume-selects-one-owner",
        "ready-after-verified-checkpoint",
        "merge-explicit-review-loop",
        "narrow-stacked-fixup",
    ]
    validated = subprocess.run(
        [sys.executable, str(RUNNER), "validate-definition"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(validated.stdout) == {"passed": True, "skills": 21}

    getting_prs_merged = next(
        skill for skill in skills if skill["id"] == "mergecraft:getting-prs-merged"
    )
    assert definition["runtime_dependencies"]["mergecraft:getting-prs-merged"] == [
        "plugins/mergecraft/skills/getting-prs-merged/scripts/post_coderabbit_comment.py",
        "plugins/mergecraft/skills/publishing-reviewable-prs/scripts/reviewable_pr_state.py",
    ]
    runner = load_runner()
    closure = runner.runtime_subtree_files(
        runner.BundleSource(
            ROOT,
            (getting_prs_merged["entrypoint"],),
            "fixture://candidate",
            "candidate-revision",
            runtime_dependencies=tuple(
                definition["runtime_dependencies"]["mergecraft:getting-prs-merged"]
            ),
        )
    )
    assert {
        path
        for path in closure
        if path.startswith("plugins/mergecraft/skills/publishing-reviewable-prs/")
    } == {
        "plugins/mergecraft/skills/publishing-reviewable-prs/scripts/reviewable_pr_state.py"
    }


def test_fixture_transport_builds_gate_passing_resumable_evidence(tmp_path: Path):
    output = tmp_path / "evidence"
    mapping = incumbent_mapping(tmp_path / "incumbents.json", 2)
    command = [
        sys.executable,
        str(RUNNER),
        "run",
        "--adapter",
        "fixture",
        "--incumbents",
        str(mapping),
        "--output",
        str(output),
        "--candidate-repository",
        "fixture://candidate",
        "--candidate-revision",
        "candidate-revision",
        "--scenario-limit",
        "2",
    ]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    manifest = output / "evidence-v2.json"
    matrix = output / "matrix-v2.json"
    first = json.loads(manifest.read_text())
    first_ids = [run["id"] for run in first["executor_runs"]]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    second = json.loads(manifest.read_text())
    assert [run["id"] for run in second["executor_runs"]] == first_ids
    assert len(first["executor_runs"]) == 24
    assert len(first["grader_runs"]) == 2
    assert all(run["condition_mapping_hidden"] for run in first["grader_runs"])
    assert first["invalidations"]["events"]
    isolation_scope = json.loads(
        (output / "artifacts/isolation-scope.json").read_text()
    )
    assert isolation_scope["os_sandbox"] == "not claimed"
    gate = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--manifest",
            str(manifest),
            "--matrix",
            str(matrix),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert gate.returncode == 0, gate.stdout + gate.stderr
    response_path = output / first["executor_runs"][0]["response_artifact_relpath"]
    response_path.write_text("tampered")
    rejected = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert rejected.returncode == 2
    assert "response drift" in rejected.stderr


@pytest.mark.parametrize("redirect", ("leaf", "parent"))
def test_fixture_runner_rejects_symlinked_output_components_without_writing_target(
    tmp_path: Path, redirect: str
):
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
    mapping = incumbent_mapping(tmp_path / "incumbents.json", 2)

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "run",
            "--adapter",
            "fixture",
            "--incumbents",
            str(mapping),
            "--output",
            str(output),
            "--candidate-repository",
            "fixture://candidate",
            "--candidate-revision",
            "candidate-revision",
            "--scenario-limit",
            "2",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert sentinel.read_bytes() == b"preserve me\n"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]


def test_fixture_transport_cannot_masquerade_as_production_matrix(tmp_path: Path):
    mapping = incumbent_mapping(tmp_path / "incumbents.json", 21)
    rejected = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "run",
            "--adapter",
            "fixture",
            "--incumbents",
            str(mapping),
            "--output",
            str(tmp_path / "evidence"),
            "--candidate-repository",
            "fixture://candidate",
            "--candidate-revision",
            "candidate-revision",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "proper focused-test scenario subset" in rejected.stderr


def test_executor_request_excludes_rubric_and_condition_mapping(tmp_path: Path):
    runner = load_runner()
    definition = json.loads(DEFINITION.read_text())
    scenario = runner.load_scenario(ROOT, definition["skills"][0])
    bundle = {
        "archive_relpath": "bundle.tar",
        "bundle_id": "sha256:" + "0" * 64,
        "declared_calls": [],
        "root_entrypoints": ["SKILL.md"],
        "target_skill": "fixture:skill",
    }
    archive = tmp_path / "bundle.tar"
    runner.write_tar(archive, {"SKILL.md": b"instructions"}, {"SKILL.md": 0o644})
    request = json.loads(runner.bundle_request(scenario, bundle, archive))
    assert set(request) == {
        "bundle_files",
        "declared_calls",
        "fixture",
        "prompt",
        "root_entrypoints",
        "target_skill",
    }
    serialized = json.dumps(request)
    assert "expectations" not in serialized
    assert "condition" not in request


def test_no_skill_request_has_no_bundle_or_route_cues(tmp_path: Path):
    runner = load_runner()
    definition = json.loads(DEFINITION.read_text())
    scenario = runner.load_scenario(ROOT, definition["skills"][0])
    archive = tmp_path / "empty.tar"
    runner.write_tar(archive, {}, {})
    request = json.loads(
        runner.bundle_request(
            scenario,
            {
                "kind": "no_skill",
                "target_skill": "must-not-appear",
                "root_entrypoints": [],
                "declared_calls": [],
            },
            archive,
        )
    )
    assert set(request) == {"fixture", "prompt"}
    assert "must-not-appear" not in json.dumps(request)


def test_runtime_subtree_includes_scripts_references_and_assets(tmp_path: Path):
    runner = load_runner()
    root = tmp_path / "snapshot"
    skill = root / "skills" / "example"
    (skill / "scripts").mkdir(parents=True)
    (skill / "references").mkdir()
    (skill / "assets").mkdir()
    (skill / "evals").mkdir()
    (skill / "SKILL.md").write_text("# Example\n")
    (skill / "scripts" / "publish.py").write_text("print('runtime')\n")
    (skill / "references" / "contract.md").write_text("runtime\n")
    (skill / "assets" / "template.json").write_text("{}\n")
    (skill / "evals" / "answers.md").write_text("must not be bundled\n")
    files = runner.runtime_subtree_files(
        runner.BundleSource(root, ("skills/example/SKILL.md",), "fixture", "rev")
    )
    assert set(files) == {
        "skills/example/SKILL.md",
        "skills/example/scripts/publish.py",
        "skills/example/references/contract.md",
        "skills/example/assets/template.json",
    }


def test_runtime_subtree_fails_closed_on_linked_evaluator_answers(tmp_path: Path):
    runner = load_runner()
    root = tmp_path / "snapshot"
    skill = root / "skills" / "example"
    (skill / "evals").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "# Example\n\nRead the [expected answer](evals/answers.md).\n"
    )
    (skill / "evals" / "answers.md").write_text("secret grader answer\n")

    try:
        runner.runtime_subtree_files(
            runner.BundleSource(root, ("skills/example/SKILL.md",), "fixture", "rev")
        )
    except runner.EvaluationError as error:
        assert "answer-isolation boundary" in str(error)
        assert "skills/example/evals/answers.md" in str(error)
    else:
        raise AssertionError("linked evaluator answer entered the model-visible bundle")

    (skill / "references").mkdir()
    (skill / "SKILL.md").write_text(
        "# Example\n\nRead the [expected answer](references/expected-answer.md).\n"
    )
    (skill / "references" / "expected-answer.md").write_text("secret grader answer\n")
    try:
        runner.runtime_subtree_files(
            runner.BundleSource(root, ("skills/example/SKILL.md",), "fixture", "rev")
        )
    except runner.EvaluationError as error:
        assert "answer-isolation boundary" in str(error)
        assert "skills/example/references/expected-answer.md" in str(error)
    else:
        raise AssertionError("linked expected answer entered the model-visible bundle")


def test_incumbent_full_tree_lock_detects_source_drift(tmp_path: Path):
    runner = load_runner()
    root = tmp_path / "incumbent"
    root.mkdir()
    (root / "SKILL.md").write_text("stable\n")
    mapping = {
        "schema_version": 1,
        "skills": {
            "skill": {
                "root": str(root),
                "entrypoints": ["SKILL.md"],
                "declared_calls": [],
                "repository": "fixture://incumbent",
                "revision": "rev",
                "full_tree_lock_sha256": runner.full_tree_lock(root),
            }
        },
    }
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps(mapping))
    assert runner.load_incumbents(mapping_path, ["skill"], True)
    (root / "SKILL.md").write_text("drift\n")
    try:
        runner.load_incumbents(mapping_path, ["skill"], True)
    except runner.EvaluationError as error:
        assert "full-tree lock drift" in str(error)
    else:
        raise AssertionError("drifted incumbent was accepted")


def test_incumbent_full_tree_lock_detects_mode_only_drift(tmp_path: Path):
    runner = load_runner()
    root = tmp_path / "incumbent"
    root.mkdir()
    skill = root / "SKILL.md"
    skill.write_text("stable\n")
    skill.chmod(0o644)
    original = runner.full_tree_lock(root)

    skill.chmod(0o755)

    assert runner.full_tree_lock(root) != original


def test_deterministic_tar_has_only_regular_sorted_entries(tmp_path: Path):
    runner = load_runner()
    files = {"z.txt": b"z", "a.txt": b"a"}
    modes = {"z.txt": 0o644, "a.txt": 0o600}
    first = tmp_path / "first.tar"
    second = tmp_path / "second.tar"
    runner.write_tar(first, files, modes)
    runner.write_tar(second, files, modes)
    assert first.read_bytes() == second.read_bytes()
    import tarfile

    with tarfile.open(first, "r:") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == ["a.txt", "z.txt"]
        assert all(member.isfile() for member in members)
        assert all(member.mtime == 0 for member in members)


def test_claude_adapter_empties_model_visible_capabilities(monkeypatch, tmp_path: Path):
    runner = load_runner()
    captured = {}
    runtime_identity = {
        "path": "/opt/claude/bin/claude",
        "sha256": "sha256:" + "1" * 64,
        "version": "2.1.215 (Claude Code)",
    }
    monkeypatch.setattr(
        runner,
        "resolve_claude_runtime_identity",
        lambda _executable: runtime_identity,
    )
    events = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": "session-1",
            "model": "claude-sonnet-5",
            "agents": ["claude", "Explore", "general-purpose", "Plan"],
            "tools": [],
            "mcp_servers": [],
            "plugins": [],
            "slash_commands": [],
            "skills": [],
            "capabilities": ["interrupt_receipt_v1", "msg_lifecycle_v1"],
            "analytics_disabled": True,
            "product_feedback_disabled": False,
            "uuid": "init-event-1",
            "memory_paths": {"auto": "/tmp/disabled-memory/"},
            "fast_mode_state": "off",
        },
        {
            "type": "assistant",
            "message": {
                "id": "response-1",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "answer"}],
            },
        },
        {
            "type": "result",
            "result": "answer",
            "session_id": "session-1",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    ]

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=b"\n".join(json.dumps(event).encode() for event in events),
            stderr=b"",
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    adapter = runner.ClaudeCliAdapter("claude", "claude-sonnet-5", "claude-opus-4-8")
    result = adapter.execute(b"{}", tmp_path)
    command = captured["command"]
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert command[command.index("--setting-sources") + 1] == ""
    assert command[command.index("--settings") + 1] == '{"enabledPlugins":{}}'
    assert command[command.index("--effort") + 1] == "high"
    assert "--safe-mode" in command
    assert "--disable-slash-commands" in command
    assert "--strict-mcp-config" in command
    assert "--no-session-persistence" in command
    assert captured["kwargs"]["cwd"] == tmp_path
    assert result.init_stream == {
        "agents": ["claude", "Explore", "general-purpose", "Plan"],
        "mcp_servers": [],
        "plugins": [],
        "skills": [],
        "slash_commands": [],
        "tools": [],
    }
    assert result.model_version == "claude-sonnet-5"
    events[1]["message"]["content"].append(
        {"type": "tool_use", "name": "forbidden", "input": {}}
    )
    try:
        adapter.execute(b"{}", tmp_path)
    except runner.EvaluationError as error:
        assert "forbidden tool events" in str(error)
    else:
        raise AssertionError("Claude tool event was accepted")

    events[1]["message"]["content"] = [{"type": "text", "text": "answer"}]
    events[2].pop("result")
    try:
        adapter.execute(b"{}", tmp_path)
    except runner.EvaluationError as error:
        assert "no string result field" in str(error)
    else:
        raise AssertionError("Claude assistant-content fallback was accepted")

    events[2]["result"] = "answer"
    events[0].pop("tools")
    try:
        adapter.execute(b"{}", tmp_path)
    except runner.EvaluationError as error:
        assert "declare every capability as a list" in str(error)
    else:
        raise AssertionError("Claude init event without a capability key was accepted")

    events[0]["tools"] = []
    events[0]["mcp_servers"] = [{"name": "forbidden"}]
    try:
        adapter.execute(b"{}", tmp_path)
    except runner.TransportFailure as error:
        assert "model-access capability surface is not empty" in str(error)
    else:
        raise AssertionError("Claude MCP surface was accepted")

    events[0]["mcp_servers"] = []
    events[0]["plugins"] = [{"name": "forbidden"}]
    try:
        adapter.execute(b"{}", tmp_path)
    except runner.TransportFailure as error:
        assert "model-access capability surface is not empty" in str(error)
    else:
        raise AssertionError("Claude plugin surface was accepted")

    events[0]["plugins"] = []
    events[0]["agents"] = ["claude", "Explore", "general-purpose", "forbidden"]
    try:
        adapter.execute(b"{}", tmp_path)
    except runner.TransportFailure as error:
        assert "agent discovery metadata is not the reviewed built-in set" in str(error)
    else:
        raise AssertionError("Claude custom agent metadata was accepted")

    events[0]["agents"] = ["Plan", "general-purpose", "Explore", "claude"]
    events[0]["future_capability"] = []
    try:
        adapter.execute(b"{}", tmp_path)
    except runner.TransportFailure as error:
        assert "unreviewed fields" in str(error)
    else:
        raise AssertionError("Claude init accepted an unreviewed field")

    events[0].pop("future_capability")
    events[2]["session_id"] = "different-session"
    try:
        adapter.execute(b"{}", tmp_path)
    except runner.EvaluationError as error:
        assert "session IDs differ" in str(error)
    else:
        raise AssertionError("Claude init/result session mismatch was accepted")


def test_claude_adapter_rejects_mixed_assistant_identity_and_result_content(
    monkeypatch, tmp_path: Path
):
    runner = load_runner()
    runtime_identity = {
        "path": "/opt/claude/bin/claude",
        "sha256": "sha256:" + "1" * 64,
        "version": "2.1.215 (Claude Code)",
    }
    monkeypatch.setattr(
        runner,
        "resolve_claude_runtime_identity",
        lambda _executable: runtime_identity,
    )
    events = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": "session-1",
            "model": "claude-sonnet-5",
            "agents": [],
            "tools": [],
            "mcp_servers": [],
            "plugins": [],
            "slash_commands": [],
            "skills": [],
        },
        {
            "type": "assistant",
            "message": {
                "id": "response-sonnet",
                "model": "claude-opus-4-20250514",
                "content": [{"type": "text", "text": "unbound answer"}],
            },
        },
        {
            "type": "assistant",
            "message": {
                "id": "response-sonnet",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "answer"}],
            },
        },
        {
            "type": "result",
            "result": "answer",
            "session_id": "session-1",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    ]

    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"\n".join(json.dumps(event).encode() for event in events),
            stderr=b"",
        ),
    )
    adapter = runner.ClaudeCliAdapter("claude", "claude-sonnet-5", "claude-opus-4-8")

    try:
        adapter.execute(b"{}", tmp_path)
    except runner.TransportFailure as error:
        assert "assistant" in str(error) and "model" in str(error)
    else:
        raise AssertionError("mixed-model Claude assistant stream was accepted")

    events.pop(1)
    events[-1]["result"] = "different result"
    try:
        adapter.execute(b"{}", tmp_path)
    except runner.TransportFailure as error:
        assert "assistant" in str(error) and "result" in str(error)
    else:
        raise AssertionError(
            "Claude result detached from assistant content was accepted"
        )


def test_claude_adapter_rejects_executable_drift_after_provider_call(
    monkeypatch, tmp_path: Path
):
    runner = load_runner()
    baseline = {
        "path": "/opt/claude/bin/claude",
        "sha256": "sha256:" + "1" * 64,
        "version": "2.1.215 (Claude Code)",
    }
    drifted = {**baseline, "sha256": "sha256:" + "2" * 64}
    identities = iter((baseline, baseline, drifted))
    monkeypatch.setattr(
        runner,
        "resolve_claude_runtime_identity",
        lambda _executable: next(identities),
    )
    events = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": "session-1",
            "agents": [],
            "tools": [],
            "mcp_servers": [],
            "plugins": [],
            "slash_commands": [],
            "skills": [],
        },
        {
            "type": "assistant",
            "message": {
                "id": "response-1",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "answer"}],
            },
        },
        {
            "type": "result",
            "result": "answer",
            "session_id": "session-1",
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    ]
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=b"\n".join(json.dumps(event).encode() for event in events),
            stderr=b"",
        ),
    )

    adapter = runner.ClaudeCliAdapter("claude", "claude-sonnet-5", "claude-opus-4-8")
    try:
        adapter.execute(b"{}", tmp_path)
    except runner.TransportFailure as error:
        assert "executable identity drift" in str(error)
    else:
        raise AssertionError("Claude executable drift was accepted")


def test_checkpoint_resume_rejects_claude_runtime_identity_drift(tmp_path: Path):
    runner = load_runner()
    baseline = {
        "path": "/opt/claude/bin/claude",
        "sha256": "sha256:" + "1" * 64,
        "version": "2.1.215 (Claude Code)",
    }
    adapter = SimpleNamespace(
        name="claude-cli",
        executor_model="sonnet",
        grader_model="opus",
        executor_effort="high",
        grader_effort="high",
        executor_system_prompt=runner.EXECUTOR_SYSTEM_PROMPT,
        grader_system_prompt=runner.GRADER_SYSTEM_PROMPT,
        runtime_identity=baseline,
    )
    output = tmp_path / "evidence"
    identity = {
        "local_correlation_id": "local-1",
        "model_version": "claude-sonnet-5",
        "response_id": "response-1",
        "session_id": "session-1",
    }
    identity_relpath, identity_sha256 = runner.frozen_json_artifact(
        output, "artifacts/identity.json", identity
    )
    config = runner.config_document(adapter, "executor", identity["model_version"])
    config_relpath, config_artifact_sha256 = runner.frozen_json_artifact(
        output, "artifacts/config.json", config
    )
    record = {
        "config_artifact_relpath": config_relpath,
        "config_artifact_sha256": config_artifact_sha256,
        "config_sha256": config["config_sha256"],
        "identity_artifact_relpath": identity_relpath,
        "identity_sha256": identity_sha256,
        "local_correlation_id_sha256": runner.digest_bytes(b"local-1"),
        "response_id_sha256": runner.digest_bytes(b"response-1"),
        "session_id_sha256": runner.digest_bytes(b"session-1"),
    }
    state = {"schema_version": 1}
    runner.validate_checkpoint_model_lock(output, state, adapter, "executor", record)

    adapter.runtime_identity = {
        **baseline,
        "sha256": "sha256:" + "2" * 64,
    }
    try:
        runner.validate_checkpoint_model_lock(
            output, state, adapter, "executor", record
        )
    except runner.EvaluationError as error:
        assert "checkpoint model/config binding drift" in str(error)
    else:
        raise AssertionError("checkpoint resumed after Claude runtime identity drift")


def test_successful_malformed_provider_stream_becomes_retained_transport_failure(
    monkeypatch, tmp_path: Path
):
    runner = load_runner()
    stdout = b'{"type":"system"}\n\xff\n'

    monkeypatch.setattr(
        runner,
        "resolve_claude_runtime_identity",
        lambda _executable: {
            "path": "/opt/claude/bin/claude",
            "sha256": "sha256:" + "1" * 64,
            "version": "2.1.215 (Claude Code)",
        },
    )

    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=stdout,
            stderr=b"provider diagnostic",
        ),
    )
    adapter = runner.ClaudeCliAdapter("claude", "claude-sonnet-5", "claude-opus-4-8")
    output = tmp_path / "evidence"
    request_relpath = "artifacts/requests/executors/example.json"
    request = runner.canonical_bytes({"fixture": "fixture", "prompt": "prompt"})
    runner.write_frozen(output / request_relpath, request)

    try:
        runner.run_transport_attempt(
            output,
            "executors",
            "example",
            request_relpath,
            runner.digest_bytes(request),
            "sha256:" + "1" * 64,
            lambda correlation_id: adapter.execute(b"{}", tmp_path, correlation_id),
        )
    except runner.TransportFailure as error:
        assert "utf-8" in str(error).lower()
    else:
        raise AssertionError("successful malformed stream was accepted")

    journal = json.loads(
        next((output / "artifacts/attempts/executors").glob("*.json")).read_text()
    )
    assert journal["status"] == "failed"
    assert (output / journal["stdout_artifact_relpath"]).read_bytes() == stdout
    assert (
        output / journal["stderr_artifact_relpath"]
    ).read_bytes() == b"provider diagnostic"
    assert journal["provider_events"] == [{"type": "system"}]


def test_failed_provider_attempt_retains_bound_raw_streams_and_unavailable_identity(
    tmp_path: Path,
):
    runner = load_runner()
    output = tmp_path / "evidence"
    request_relpath = "artifacts/requests/executors/example.json"
    request = runner.canonical_bytes({"fixture": "fixture", "prompt": "prompt"})
    runner.write_frozen(output / request_relpath, request)

    try:
        runner.run_transport_attempt(
            output,
            "executors",
            "example",
            request_relpath,
            runner.digest_bytes(request),
            "sha256:" + "1" * 64,
            lambda _correlation_id: (_ for _ in ()).throw(
                runner.TransportFailure("provider failed", b"", b"")
            ),
        )
    except runner.TransportFailure:
        pass
    else:
        raise AssertionError("provider failure was not propagated")

    journal = json.loads(
        next((output / "artifacts/attempts/executors").glob("*.json")).read_text()
    )
    assert journal["status"] == "failed"
    assert journal["provider_events"] == []
    assert journal["provider_identity"] == {
        "model_version": None,
        "response_id": None,
        "session_id": None,
    }
    for stream in ("stdout", "stderr"):
        artifact = output / journal[f"{stream}_artifact_relpath"]
        assert artifact.read_bytes() == b""
        assert journal[f"{stream}_sha256"] == runner.digest_bytes(b"")


def test_provider_timeout_classification_uses_typed_failure_flag(tmp_path: Path):
    runner = load_runner()
    output = tmp_path / "evidence"
    request_relpath = "artifacts/requests/executors/example.json"
    request = runner.canonical_bytes({"fixture": "fixture", "prompt": "prompt"})
    runner.write_frozen(output / request_relpath, request)

    with pytest.raises(runner.TransportFailure):
        runner.run_transport_attempt(
            output,
            "executors",
            "example",
            request_relpath,
            runner.digest_bytes(request),
            "sha256:" + "1" * 64,
            lambda _correlation_id: (_ for _ in ()).throw(
                runner.TransportFailure(
                    "provider became unavailable",
                    stdout=b"partial",
                    stderr=b"diagnostic",
                    timed_out=True,
                )
            ),
        )

    journal = json.loads(
        next((output / "artifacts/attempts/executors").glob("*.json")).read_text()
    )
    assert journal["status"] == "timeout"


def test_failed_provider_attempt_retains_raw_stdout_and_parseable_jsonl_events(
    tmp_path: Path,
):
    runner = load_runner()
    output = tmp_path / "evidence"
    request_relpath = "artifacts/requests/executors/example.json"
    request = runner.canonical_bytes({"fixture": "fixture", "prompt": "prompt"})
    runner.write_frozen(output / request_relpath, request)
    event = {
        "message": {"id": "response-1", "model": "provider-model-1"},
        "session_id": "session-1",
        "type": "result",
    }
    event_line = json.dumps(event, separators=(",", ":"), sort_keys=True).encode()
    stdout = b"provider preamble\n" + event_line + b"\n{broken\n"

    try:
        runner.run_transport_attempt(
            output,
            "executors",
            "example",
            request_relpath,
            runner.digest_bytes(request),
            "sha256:" + "1" * 64,
            lambda _correlation_id: (_ for _ in ()).throw(
                runner.TransportFailure("provider failed", stdout, b"diagnostic")
            ),
        )
    except runner.TransportFailure:
        pass
    else:
        raise AssertionError("provider failure was not propagated")

    journal = json.loads(
        next((output / "artifacts/attempts/executors").glob("*.json")).read_text()
    )
    assert (output / journal["stdout_artifact_relpath"]).read_bytes() == stdout
    assert journal["provider_events"] == [event]
    assert journal["provider_identity"] == {
        "model_version": "provider-model-1",
        "response_id": "response-1",
        "session_id": "session-1",
    }


def test_production_candidate_requires_clean_exact_git_head(tmp_path: Path):
    runner = load_runner()
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Eval Fixture"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "eval-fixture@example.invalid"],
        cwd=repository,
        check=True,
    )
    (repository / "file.txt").write_text("frozen\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "test: freeze fixture"],
        cwd=repository,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    runner.validate_production_candidate(repository, tmp_path / "evidence", head)
    (repository / "file.txt").write_text("dirty\n")
    try:
        runner.validate_production_candidate(repository, tmp_path / "evidence", head)
    except runner.EvaluationError as error:
        assert "not a frozen clean checkout" in str(error)
    else:
        raise AssertionError("dirty production candidate was accepted")


def test_candidate_snapshot_uses_safe_python39_extraction_and_retains_git_evidence(
    tmp_path: Path,
):
    runner = load_runner()
    repository = tmp_path / "repository"
    output = tmp_path / "evidence"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Eval Fixture"], cwd=repository, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "eval-fixture@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "git@github.com:nisavid/agents.git",
        ],
        cwd=repository,
        check=True,
    )
    (repository / "nested").mkdir()
    (repository / "nested/file.txt").write_text("frozen\n")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "test: snapshot fixture"], cwd=repository, check=True
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    temporary, snapshot, source = runner.materialize_candidate_snapshot(
        repository, output, revision, "https://github.com/nisavid/agents"
    )
    try:
        assert (snapshot / "nested/file.txt").read_text() == "frozen\n"
        assert (output / source["git_objects_artifact_relpath"]).is_file()
        assert source["git_objects_sha256"].startswith("sha256:")
        assert source["repository"] == "https://github.com/nisavid/agents"
    finally:
        temporary.cleanup()


def test_production_candidate_origin_accepts_only_canonical_credential_free_aliases():
    runner = load_runner()

    for alias in (
        "https://github.com/nisavid/agents",
        "https://github.com/nisavid/agents.git",
        "git@github.com:nisavid/agents.git",
        "ssh://git@github.com/nisavid/agents.git",
    ):
        assert (
            runner.canonical_repository_origin(alias)
            == "https://github.com/nisavid/agents"
        )

    for invalid in (
        "https://github.com/fork/agents.git",
        "https://user@github.com/nisavid/agents.git",
        "https://token@github.com/nisavid/agents.git",
        "ssh://ivan@github.com/nisavid/agents.git",
        "file:///tmp/nisavid/agents",
        "/tmp/nisavid/agents",
        "github.com/nisavid/agents",
        "git@github.com:nisavid/agents",
        "https://github.com/nisavid/agents/",
        "https://github.com/nisavid/agents.git/extra",
        "",
    ):
        try:
            runner.canonical_repository_origin(invalid)
        except runner.EvaluationError as error:
            assert "origin is not nisavid/agents" in str(error)
        else:
            raise AssertionError(
                f"unsafe production candidate origin was accepted: {invalid}"
            )


def test_invalidation_journal_recovers_after_interrupted_move(
    monkeypatch, tmp_path: Path
):
    runner = load_runner()
    output = tmp_path / "evidence"
    affected = ["source", "consumer"]
    for scenario_id in affected:
        for condition in runner.CONDITIONS:
            for repetition in runner.REPETITIONS:
                path = (
                    output
                    / "checkpoints/executors"
                    / f"{scenario_id}--{condition}--{repetition}.json"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
        for kind in ("graders", "grader-plans"):
            path = output / "checkpoints" / kind / f"{scenario_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
    state = {"schema_version": 1}
    moves = 0
    original_move = runner.shutil.move

    def interrupted_move(source, destination):
        nonlocal moves
        moves += 1
        if moves == 8:
            raise RuntimeError("simulated interruption")
        return original_move(source, destination)

    monkeypatch.setattr(runner.shutil, "move", interrupted_move)
    try:
        runner.invalidate(output, state, "source", affected)
    except RuntimeError as error:
        assert "simulated interruption" in str(error)
    else:
        raise AssertionError("invalidation move interruption was not propagated")
    assert state["invalidation_journal"]["status"] == "moving"
    monkeypatch.setattr(runner.shutil, "move", original_move)
    runner.invalidate(output, state, "source", affected)
    assert state["invalidation_journal"]["status"] == "completed"
    assert len(state["invalidation"]["superseded_checkpoint_artifacts"]) == 28
    assert not list((output / "checkpoints").rglob("*.json"))


def test_full_run_restart_finishes_interrupted_invalidation(
    monkeypatch, tmp_path: Path
):
    runner = load_runner()
    output = tmp_path / "evidence"
    mapping = incumbent_mapping(tmp_path / "incumbents.json", 2)
    args = SimpleNamespace(
        adapter="fixture",
        candidate_repository="fixture://candidate",
        candidate_revision="candidate-revision",
        claude_executable="claude",
        claude_timeout_seconds=300,
        definition=DEFINITION,
        executor_model="sonnet",
        grader_model="opus",
        incumbents=mapping,
        output=output,
        repo=ROOT,
        scenario_limit=2,
    )
    move_count = 0
    original_move = runner.shutil.move

    def interrupted_move(source, destination):
        nonlocal move_count
        move_count += 1
        if move_count == 8:
            raise RuntimeError("simulated full-run interruption")
        return original_move(source, destination)

    monkeypatch.setattr(runner.shutil, "move", interrupted_move)
    try:
        runner.run(args)
    except RuntimeError as error:
        assert "simulated full-run interruption" in str(error)
    else:
        raise AssertionError("full runner interruption was not propagated")
    interrupted_state = json.loads((output / "state.json").read_text())
    assert interrupted_state["invalidation_journal"]["status"] == "moving"

    monkeypatch.setattr(runner.shutil, "move", original_move)
    assert runner.run(args) == 0
    recovered_state = json.loads((output / "state.json").read_text())
    assert recovered_state["invalidation_journal"]["status"] == "completed"
    gate = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--manifest",
            str(output / "evidence-v2.json"),
            "--matrix",
            str(output / "matrix-v2.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert gate.returncode == 0, gate.stdout + gate.stderr


def test_full_run_resume_rehydrates_checkpoint_model_locks(monkeypatch, tmp_path: Path):
    runner = load_runner()
    output = tmp_path / "evidence"
    mapping = incumbent_mapping(tmp_path / "incumbents.json", 2)
    args = SimpleNamespace(
        adapter="fixture",
        candidate_repository="fixture://candidate",
        candidate_revision="candidate-revision",
        claude_executable="claude",
        claude_timeout_seconds=300,
        definition=DEFINITION,
        executor_model="sonnet",
        grader_model="opus",
        incumbents=mapping,
        output=output,
        repo=ROOT,
        scenario_limit=2,
    )
    original_write_json = runner.write_json
    checkpoint_written = False

    def crash_after_checkpoint_before_outer_state(path, value):
        nonlocal checkpoint_written
        if checkpoint_written and path == output / "state.json":
            raise RuntimeError("simulated checkpoint/state crash window")
        original_write_json(path, value)
        if path.parent == output / "checkpoints/executors":
            checkpoint_written = True

    monkeypatch.setattr(runner, "write_json", crash_after_checkpoint_before_outer_state)
    try:
        runner.run(args)
    except RuntimeError as error:
        assert "checkpoint/state crash window" in str(error)
    else:
        raise AssertionError("checkpoint/state crash window was not exercised")

    state_path = output / "state.json"
    interrupted_state = json.loads(state_path.read_text())
    assert interrupted_state["executor_model_version"] == "fixture-1"
    assert len(list((output / "checkpoints/executors").glob("*.json"))) == 1

    # Simulate an older crash window whose checkpoint landed without the lock.
    # Resume must recover the exact observed value from identity/config evidence.
    interrupted_state.pop("executor_model_version")
    original_write_json(state_path, interrupted_state)
    monkeypatch.setattr(runner, "write_json", original_write_json)

    assert runner.run(args) == 0
    recovered_state = json.loads(state_path.read_text())
    assert recovered_state["executor_model_version"] == "fixture-1"
    assert recovered_state["grader_model_version"] == "fixture-1"

    recovered_state.pop("executor_model_version")
    recovered_state.pop("grader_model_version")
    original_write_json(state_path, recovered_state)
    assert runner.run(args) == 0
    rehydrated_state = json.loads(state_path.read_text())
    assert rehydrated_state["executor_model_version"] == "fixture-1"
    assert rehydrated_state["grader_model_version"] == "fixture-1"


def test_crash_after_provider_success_retains_and_gates_completed_retries(
    monkeypatch, tmp_path: Path
):
    runner = load_runner()
    output = tmp_path / "evidence"
    mapping = incumbent_mapping(tmp_path / "incumbents.json", 2)
    args = SimpleNamespace(
        adapter="fixture",
        candidate_repository="fixture://candidate",
        candidate_revision="candidate-revision",
        claude_executable="claude",
        claude_timeout_seconds=300,
        definition=DEFINITION,
        executor_model="sonnet",
        grader_model="opus",
        incumbents=mapping,
        output=output,
        repo=ROOT,
        scenario_limit=2,
    )
    original_write_json = runner.write_json
    interrupted = False

    def crash_before_first_executor_checkpoint(path, value):
        nonlocal interrupted
        if not interrupted and path.parent == output / "checkpoints/executors":
            interrupted = True
            raise RuntimeError("simulated provider-success/checkpoint crash window")
        original_write_json(path, value)

    monkeypatch.setattr(runner, "write_json", crash_before_first_executor_checkpoint)
    try:
        runner.run(args)
    except RuntimeError as error:
        assert "provider-success/checkpoint crash window" in str(error)
    else:
        raise AssertionError(
            "provider-success/checkpoint crash window was not exercised"
        )

    attempts = list((output / "artifacts/attempts/executors").glob("*.json"))
    assert len(attempts) == 1
    first_attempt = json.loads(attempts[0].read_text())
    assert first_attempt["status"] == "completed"
    assert (output / first_attempt["response_artifact_relpath"]).is_file()
    assert (output / first_attempt["transport_artifact_relpath"]).is_file()

    monkeypatch.setattr(runner, "write_json", original_write_json)
    assert runner.run(args) == 0
    manifest_path = output / "evidence-v2.json"
    matrix_path = output / "matrix-v2.json"
    manifest = json.loads(manifest_path.read_text())
    superseded_executor = next(
        artifact
        for artifact in manifest["invalidations"]["events"][0][
            "superseded_checkpoint_artifacts"
        ]
        if "/executors/" in artifact["relpath"]
        and len(
            json.loads((output / artifact["relpath"]).read_text())["run"][
                "attempt_history_artifact_relpaths"
            ]
        )
        == 2
    )
    superseded_run = json.loads((output / superseded_executor["relpath"]).read_text())[
        "run"
    ]
    assert len(superseded_run["attempt_history_artifact_relpaths"]) == 2
    assert all(
        json.loads((output / relpath).read_text())["status"] == "completed"
        for relpath in superseded_run["attempt_history_artifact_relpaths"]
    )

    def run_gate():
        return subprocess.run(
            [
                sys.executable,
                str(GATE),
                "--manifest",
                str(manifest_path),
                "--matrix",
                str(matrix_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    gated = run_gate()
    assert gated.returncode == 0, gated.stdout + gated.stderr

    nonwinning_relpath = next(
        relpath
        for relpath in superseded_run["attempt_history_artifact_relpaths"]
        if relpath != superseded_run["attempt_artifact_relpath"]
    )
    nonwinning = json.loads((output / nonwinning_relpath).read_text())
    (output / nonwinning["transport_artifact_relpath"]).write_bytes(b"tampered\n")
    rejected = run_gate()
    assert rejected.returncode == 2
    assert "completed attempt transport digest mismatch" in rejected.stdout


def test_definition_rejects_topology_companion_omission(tmp_path: Path):
    runner = load_runner()
    definition = json.loads(DEFINITION.read_text())
    graphite = next(
        skill for skill in definition["skills"] if skill["id"] == "mergecraft:graphite"
    )
    graphite["companions"] = ["mergecraft:publishing-reviewable-prs"]
    try:
        runner.validate_definition(ROOT, definition)
    except runner.EvaluationError as error:
        assert "companion declaration does not match topology calls" in str(error)
    else:
        raise AssertionError("topology companion omission was accepted")


def test_definition_accepts_a_noncanonical_comparative_inventory():
    runner = load_runner()
    definition = json.loads(DEFINITION.read_text())
    definition["evaluation_id"] = "comparative-review-lifecycle-v1"
    definition["skills"] = [definition["skills"][0]]
    definition["target_skill_ids"] = [definition["skills"][0]["id"]]
    definition["runtime_dependencies"] = {}
    definition["invalidation_source_scenario_id"] = definition["skills"][0]["scenario"][
        "id"
    ]

    runner.validate_definition(ROOT, definition)


def aggregate_comparison(runner, strategy: str, pass_counts: dict[str, int]):
    skill = {
        "id": "mergecraft:comparison-fixture",
        "utility_expectation_ids": ["quality"],
    }
    if strategy != "standard":
        skill["comparison_strategy"] = strategy
    matrix = {
        "skill_inventory": [skill["id"]],
        "skills": [skill],
        "scenarios": [
            {
                "id": "comparison-scenario",
                "skill_id": skill["id"],
                "expectations": [{"id": "quality", "severity": "quality"}],
            }
        ],
    }
    runs = []
    grades = []
    output_ids = []
    for condition in runner.CONDITIONS:
        for repetition in runner.REPETITIONS:
            output_id = f"{condition}-{repetition}"
            output_ids.append(output_id)
            runs.append(
                {
                    "condition": condition,
                    "id": f"run-{output_id}",
                    "output_id": output_id,
                }
            )
            grades.append(
                {
                    "output_id": output_id,
                    "expectations": [
                        {
                            "id": "quality",
                            "passed": repetition <= pass_counts.get(condition, 0),
                        }
                    ],
                }
            )
    grader_runs = [
        {
            "scenario_id": "comparison-scenario",
            "presentation_order": list(reversed(output_ids)),
            "grades": grades,
        }
    ]
    return runner.aggregate(matrix, runs, grader_runs)[1]["skills"][0]


def test_ordinary_tool_comparison_uses_no_skill_as_the_intended_owner():
    runner = load_runner()
    counts = {"no_skill": 3}
    ordinary = aggregate_comparison(runner, "ordinary-tool", counts)
    generic = aggregate_comparison(runner, "standard", counts)
    assert ordinary["passed"] is True
    assert ordinary["utility"] is True
    assert generic["passed"] is False


def test_retained_comparison_uses_adapter_plus_incumbent_composition():
    runner = load_runner()
    counts = {"incumbent": 2, "composed": 3}
    retained = aggregate_comparison(runner, "retain", counts)
    generic = aggregate_comparison(runner, "standard", counts)
    assert retained["passed"] is True
    assert retained["nonregression_passed"] is True
    assert generic["passed"] is False


def test_retained_bundle_contains_immutable_incumbent_bytes(tmp_path: Path):
    runner = load_runner()
    definition = json.loads(
        (ROOT / "evals/mergecraft/retirement-control-plane.json").read_text()
    )
    declarations = {skill["id"]: skill for skill in definition["skills"]}
    retained = declarations["mergecraft:getting-prs-merged"]
    incumbent_root = tmp_path / "incumbent"
    incumbent_root.mkdir()
    (incumbent_root / "SKILL.md").write_text("retained gh-fix-ci bytes\n")
    output = tmp_path / "bundles"
    output.mkdir()
    incumbent_archive = output / "incumbent-tree.tar"
    incumbent_bytes = (incumbent_root / "SKILL.md").read_bytes()
    runner.write_tar(
        incumbent_archive, {"SKILL.md": incumbent_bytes}, {"SKILL.md": 0o644}
    )
    incumbents = {
        retained["id"]: runner.BundleSource(
            incumbent_root,
            ("SKILL.md",),
            "https://github.com/openai/plugins",
            "immutable-gh-fix-ci-revision",
            (),
            runner.full_tree_lock(incumbent_root),
            archive_relpath="incumbent-tree.tar",
            archive_sha256=runner.digest_bytes(incumbent_archive.read_bytes()),
        )
    }
    _, bundle_sets = runner.build_bundles(
        ROOT,
        output,
        [retained],
        declarations,
        incumbents,
        "https://github.com/nisavid/agents",
        "candidate-revision",
        definition["runtime_dependencies"],
    )
    composed = bundle_sets[0]["conditions"]["composed"]
    assert "github:gh-fix-ci" in composed["declared_calls"]
    retained_source = composed["source_provenance"]["sources"][-1]
    assert retained_source["owner"] == "github:gh-fix-ci"
    assert retained_source["full_tree_lock_sha256"] == runner.full_tree_lock(
        incumbent_root
    )
    assert retained_source["runtime_subtree"] == [
        {
            "logical_path": "SKILL.md",
            "mode": "0644",
            "sha256": runner.digest_bytes(incumbent_bytes),
            "size": len(incumbent_bytes),
        }
    ]


def test_gate_rejects_grader_request_that_leaks_condition_after_rehash(tmp_path: Path):
    output = tmp_path / "evidence"
    mapping = incumbent_mapping(tmp_path / "incumbents.json", 2)
    command = [
        sys.executable,
        str(RUNNER),
        "run",
        "--adapter",
        "fixture",
        "--incumbents",
        str(mapping),
        "--output",
        str(output),
        "--candidate-repository",
        "fixture://candidate",
        "--candidate-revision",
        "candidate-revision",
        "--scenario-limit",
        "2",
    ]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    manifest_path = output / "evidence-v2.json"
    manifest = json.loads(manifest_path.read_text())
    batch = manifest["grader_runs"][0]
    request_path = output / batch["request_artifact_relpath"]
    request = json.loads(request_path.read_text())
    request["outputs"][0]["response"] += "\ncondition=candidate"
    request_bytes = json.dumps(
        request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    request_path.write_bytes(request_bytes)
    import hashlib

    batch["request_sha256"] = "sha256:" + hashlib.sha256(request_bytes).hexdigest()
    manifest["final_result"]["sha256"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {
                    key: value
                    for key, value in manifest.items()
                    if key != "final_result"
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
    manifest_path.write_text(json.dumps(manifest))
    rejected = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--manifest",
            str(manifest_path),
            "--matrix",
            str(output / "matrix-v2.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "grader request bytes do not match bound inputs" in rejected.stdout


def test_gate_rejects_rehashed_winning_attempt_response_identity(tmp_path: Path):
    runner = load_runner()
    output = tmp_path / "evidence"
    mapping = incumbent_mapping(tmp_path / "incumbents.json", 2)
    command = [
        sys.executable,
        str(RUNNER),
        "run",
        "--adapter",
        "fixture",
        "--incumbents",
        str(mapping),
        "--output",
        str(output),
        "--candidate-repository",
        "fixture://candidate",
        "--candidate-revision",
        "candidate-revision",
        "--scenario-limit",
        "2",
    ]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    manifest_path = output / "evidence-v2.json"
    manifest = json.loads(manifest_path.read_text())
    run = manifest["executor_runs"][0]
    identity_path = output / run["identity_artifact_relpath"]
    identity = json.loads(identity_path.read_text())
    identity["response_id"] = "rehashed-but-inconsistent-response"
    identity_bytes = runner.canonical_bytes(identity)
    identity_path.write_bytes(identity_bytes)
    run["identity_sha256"] = runner.digest_bytes(identity_bytes)
    run["response_id_sha256"] = runner.digest_bytes(identity["response_id"].encode())
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))

    rejected = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--manifest",
            str(manifest_path),
            "--matrix",
            str(output / "matrix-v2.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "winning attempt response identity drift" in rejected.stdout


def test_gate_rejects_rehashed_superseded_checkpoint_input(tmp_path: Path):
    runner = load_runner()
    output = tmp_path / "evidence"
    mapping = incumbent_mapping(tmp_path / "incumbents.json", 2)
    command = [
        sys.executable,
        str(RUNNER),
        "run",
        "--adapter",
        "fixture",
        "--incumbents",
        str(mapping),
        "--output",
        str(output),
        "--candidate-repository",
        "fixture://candidate",
        "--candidate-revision",
        "candidate-revision",
        "--scenario-limit",
        "2",
    ]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    manifest_path = output / "evidence-v2.json"
    manifest = json.loads(manifest_path.read_text())
    event = manifest["invalidations"]["events"][0]
    superseded = next(
        artifact
        for artifact in event["superseded_checkpoint_artifacts"]
        if "/executors/" in artifact["relpath"]
    )
    checkpoint_path = output / superseded["relpath"]
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["run"]["input_sha256"] = runner.digest_bytes(
        b"syntactically-valid-but-unrelated-input"
    )
    checkpoint_bytes = json.dumps(checkpoint, indent=2, sort_keys=True).encode() + b"\n"
    checkpoint_path.write_bytes(checkpoint_bytes)
    superseded["sha256"] = runner.digest_bytes(checkpoint_bytes)
    manifest["invalidations"]["closure_sha256"] = runner.canonical_digest(
        {
            "closed_at": manifest["invalidations"]["closed_at"],
            "events": manifest["invalidations"]["events"],
        }
    )
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))

    rejected = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--manifest",
            str(manifest_path),
            "--matrix",
            str(output / "matrix-v2.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "superseded executor input digest mismatch" in rejected.stdout

    output = tmp_path / "grader-evidence"
    command[command.index("--output") + 1] = str(output)
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    manifest_path = output / "evidence-v2.json"
    manifest = json.loads(manifest_path.read_text())
    event = manifest["invalidations"]["events"][0]
    superseded = next(
        artifact
        for artifact in event["superseded_checkpoint_artifacts"]
        if "/graders/" in artifact["relpath"]
    )
    checkpoint_path = output / superseded["relpath"]
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["input_sha256"] = runner.digest_bytes(
        b"syntactically-valid-but-unrelated-grader-input"
    )
    checkpoint_bytes = json.dumps(checkpoint, indent=2, sort_keys=True).encode() + b"\n"
    checkpoint_path.write_bytes(checkpoint_bytes)
    superseded["sha256"] = runner.digest_bytes(checkpoint_bytes)
    manifest["invalidations"]["closure_sha256"] = runner.canonical_digest(
        {
            "closed_at": manifest["invalidations"]["closed_at"],
            "events": manifest["invalidations"]["events"],
        }
    )
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))

    rejected = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--manifest",
            str(manifest_path),
            "--matrix",
            str(output / "matrix-v2.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "superseded grader input digest mismatch" in rejected.stdout


def test_fully_local_production_evidence_passes_gate(tmp_path: Path, monkeypatch):
    runner, output = build_local_production_evidence(tmp_path, monkeypatch)

    manifest_path = output / "evidence-v2.json"
    matrix_path = output / "matrix-v2.json"

    def run_gate():
        return subprocess.run(
            [
                sys.executable,
                str(GATE),
                "--manifest",
                str(manifest_path),
                "--matrix",
                str(matrix_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    gated = run_gate()
    assert gated.returncode == 0, gated.stdout + gated.stderr
    assert json.loads(gated.stdout)["passed"] is True

    original_manifest = manifest_path.read_bytes()
    manifest = json.loads(original_manifest)
    manifest["candidate_source"]["repository"] = "https://github.com/fork/agents"
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))
    rejected = run_gate()
    assert rejected.returncode == 2
    assert "production candidate repository is not canonical" in rejected.stdout

    manifest_path.write_bytes(original_manifest)
    manifest = json.loads(original_manifest)
    executor_config = manifest["executor_config"]
    executor_config["model_version"] = "sonnet"
    executor_config["config_sha256"] = runner.canonical_digest(
        {key: value for key, value in executor_config.items() if key != "config_sha256"}
    )
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))
    rejected = run_gate()
    assert rejected.returncode == 2
    assert "requested and observed models must be one exact identity" in rejected.stdout

    manifest_path.write_bytes(original_manifest)
    manifest = json.loads(original_manifest)
    manifest["invalidations"]["events"][0]["superseded_checkpoint_artifacts"].pop()
    manifest["invalidations"]["closure_sha256"] = runner.canonical_digest(
        {
            "closed_at": manifest["invalidations"]["closed_at"],
            "events": manifest["invalidations"]["events"],
        }
    )
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))
    rejected = run_gate()
    assert rejected.returncode == 2
    assert "superseded checkpoint closure is incomplete" in rejected.stdout

    manifest_path.write_bytes(original_manifest)
    manifest = json.loads(original_manifest)
    event = manifest["invalidations"]["events"][0]
    superseded_executor = next(
        artifact
        for artifact in event["superseded_checkpoint_artifacts"]
        if "/executors/" in artifact["relpath"]
    )
    checkpoint_path = output / superseded_executor["relpath"]
    original_checkpoint = checkpoint_path.read_bytes()
    checkpoint = json.loads(original_checkpoint)
    checkpoint["run"]["input_sha256"] = runner.digest_bytes(
        b"unrelated-production-preimage"
    )
    checkpoint_bytes = json.dumps(checkpoint, indent=2, sort_keys=True).encode() + b"\n"
    checkpoint_path.write_bytes(checkpoint_bytes)
    superseded_executor["sha256"] = runner.digest_bytes(checkpoint_bytes)
    manifest["invalidations"]["closure_sha256"] = runner.canonical_digest(
        {
            "closed_at": manifest["invalidations"]["closed_at"],
            "events": manifest["invalidations"]["events"],
        }
    )
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))
    rejected = run_gate()
    assert rejected.returncode == 2
    assert "superseded executor input digest mismatch" in rejected.stdout

    checkpoint_path.write_bytes(original_checkpoint)
    manifest_path.write_bytes(original_manifest)
    manifest = json.loads(original_manifest)
    event = manifest["invalidations"]["events"][0]
    grader_plan_artifact = next(
        artifact
        for artifact in event["superseded_checkpoint_artifacts"]
        if "/grader-plans/" in artifact["relpath"]
    )
    grader_plan_path = output / grader_plan_artifact["relpath"]
    original_grader_plan = grader_plan_path.read_bytes()
    grader_plan = json.loads(original_grader_plan)
    grader_plan["seed_hex"] = "00" * 32
    grader_plan_bytes = (
        json.dumps(grader_plan, indent=2, sort_keys=True).encode() + b"\n"
    )
    grader_plan_path.write_bytes(grader_plan_bytes)
    grader_plan_artifact["sha256"] = runner.digest_bytes(grader_plan_bytes)
    manifest["invalidations"]["closure_sha256"] = runner.canonical_digest(
        {
            "closed_at": manifest["invalidations"]["closed_at"],
            "events": manifest["invalidations"]["events"],
        }
    )
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))
    rejected = run_gate()
    assert rejected.returncode == 2
    assert "superseded grader randomization drift" in rejected.stdout

    grader_plan_path.write_bytes(original_grader_plan)
    manifest_path.write_bytes(original_manifest)
    manifest = json.loads(original_manifest)
    batch = manifest["grader_runs"][0]
    identity_path = output / batch["identity_artifact_relpath"]
    original_identity = identity_path.read_bytes()
    identity = json.loads(original_identity)
    identity["session_id"] = "unbound-grader-session"
    identity_bytes = runner.canonical_bytes(identity)
    identity_path.write_bytes(identity_bytes)
    batch["identity_sha256"] = runner.digest_bytes(identity_bytes)
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))
    rejected = run_gate()
    assert rejected.returncode == 2
    assert "grader identity artifact does not bind" in rejected.stdout

    identity_path.write_bytes(original_identity)
    manifest_path.write_bytes(original_manifest)
    manifest = json.loads(original_manifest)
    batch = manifest["grader_runs"][0]
    identity = json.loads(original_identity)
    identity["local_correlation_id"] = "rehashed-but-unbound-correlation"
    identity_bytes = runner.canonical_bytes(identity)
    identity_path.write_bytes(identity_bytes)
    batch["identity_sha256"] = runner.digest_bytes(identity_bytes)
    batch["local_correlation_id_sha256"] = runner.digest_bytes(
        identity["local_correlation_id"].encode()
    )
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))
    rejected = run_gate()
    assert rejected.returncode == 2
    assert "winning attempt correlation drift" in rejected.stdout

    identity_path.write_bytes(original_identity)
    manifest_path.write_bytes(original_manifest)
    manifest = json.loads(original_manifest)
    incumbent = manifest["bundles"][0]["conditions"]["incumbent"]
    provenance = incumbent["source_provenance"]
    incumbent_archive = output / provenance["full_tree_archive_relpath"]
    import tarfile

    files = {}
    modes = {}
    with tarfile.open(incumbent_archive, "r:") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            stream = archive.extractfile(member)
            assert stream is not None
            files[member.name] = stream.read()
            modes[member.name] = member.mode & 0o777
    first_path = sorted(modes)[0]
    modes[first_path] = 0o755 if modes[first_path] != 0o755 else 0o644
    changed_archive = output / "mode-only-incumbent.tar"
    runner.write_tar(changed_archive, files, modes)
    incumbent_archive.write_bytes(changed_archive.read_bytes())
    provenance["full_tree_archive_sha256"] = runner.digest_bytes(
        incumbent_archive.read_bytes()
    )
    incumbent["bundle_id"] = runner.canonical_digest(
        {key: value for key, value in incumbent.items() if key != "bundle_id"}
    )
    manifest["final_result"]["sha256"] = runner.canonical_digest(
        {key: value for key, value in manifest.items() if key != "final_result"}
    )
    manifest_path.write_text(json.dumps(manifest))
    rejected = run_gate()
    assert rejected.returncode == 2
    assert "full-tree lock does not match retained archive" in rejected.stdout
