from __future__ import annotations

import base64
import copy
import hashlib
import io
import importlib.util
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[4]
SKILL_DIR = (
    REPOSITORY / "plugins/versionkeeping/skills/checkpointing-and-publishing-git-work"
)
SCRIPT = SKILL_DIR / "scripts" / "check_eval_gate.py"


def load_gate():
    specification = importlib.util.spec_from_file_location("check_eval_gate", SCRIPT)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def byte_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


class EvalGateV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.matrix_path = self.root / "matrix-v2.json"
        self.manifest_path = self.root / "evidence-v2.json"
        self.init_agents: list[str] = []

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def matrix(
        self,
        scenario_count: int = 2,
        skill_count: int = 1,
        invalidation_event_policy: str = "allow_empty_for_focused_test",
    ) -> dict[str, object]:
        if skill_count > scenario_count:
            raise ValueError("skill_count cannot exceed scenario_count")
        skill_ids = [f"skill-{index:02d}" for index in range(1, skill_count + 1)]
        scenarios = []
        for index in range(1, scenario_count + 1):
            scenario_id = f"scenario-{index:02d}"
            rubric = self.rubric(scenario_id)
            scenarios.append(
                {
                    "id": scenario_id,
                    "skill_id": skill_ids[(index - 1) % skill_count],
                    "prompt_sha256": byte_digest(f"prompt:{scenario_id}"),
                    "fixture_sha256": byte_digest(f"fixture:{scenario_id}"),
                    "rubric_sha256": bytes_digest(canonical_bytes(rubric)),
                    "reverse_dependency_scenario_ids": (
                        ["scenario-02"] if scenario_count > 1 and index == 1 else []
                    ),
                    "expectations": [
                        {"id": "safe", "severity": "safety"},
                        {"id": "utility", "severity": "quality"},
                    ],
                }
            )
        return {
            "schema_version": 2,
            "evaluation_id": (
                "focused-test:mergecraft-phase-7"
                if invalidation_event_policy == "allow_empty_for_focused_test"
                else "mergecraft-phase-7"
            ),
            "expected_scenario_count": scenario_count,
            "invalidation_event_policy": invalidation_event_policy,
            "skill_inventory": skill_ids,
            "skills": [
                {"id": skill_id, "utility_expectation_ids": ["utility"]}
                for skill_id in skill_ids
            ],
            "scenarios": scenarios,
        }

    def rubric(self, scenario_id: str) -> dict[str, object]:
        return {
            "expectations": [
                {
                    "id": "safe",
                    "severity": "safety",
                    "text": f"The {scenario_id} response remains safe",
                },
                {
                    "id": "utility",
                    "severity": "quality",
                    "text": f"The {scenario_id} response is useful",
                },
            ]
        }

    def config(self, kind: str) -> dict[str, object]:
        system_prompt = (
            "Respond to the supplied user message."
            if kind == "executor"
            else "Assess every labeled response against every supplied criterion. "
            "Return strict JSON with one grades array preserving the supplied label order."
        )
        config = {
            "adapter": "fixture",
            "model": f"fixture-{kind}",
            "model_version": "fixture-1",
            "reasoning_effort": "fixture",
            "runtime": None,
            "system_prompt": system_prompt,
            "system_prompt_sha256": byte_digest(system_prompt),
            "transport": {
                "allowed": True,
                "kind": "model_api",
                "network_scope": "model_transport_only",
            },
        }
        return {**config, "config_sha256": canonical_digest(config)}

    def bundle(
        self,
        label: str,
        target_skill: str,
        files: list[tuple[str, str, str]],
    ) -> dict[str, object]:
        archive_relpath = f"bundles/{label}.tar"
        archive_path = self.root / archive_relpath
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "w") as archive:
            for logical_path, content, mode in sorted(files):
                encoded = content.encode()
                member = tarfile.TarInfo(logical_path)
                member.mode = int(mode, 8)
                member.size = len(encoded)
                archive.addfile(member, io.BytesIO(encoded))
        body = {
            "schema": 2,
            "kind": "no_skill" if not files else "skill_bundle",
            "target_skill": target_skill,
            "root_entrypoints": [] if not files else ["SKILL.md"],
            "source_provenance": {
                "repository": "nisavid/agents",
                "revision_sha256": byte_digest(f"revision:{label}"),
            },
            "declared_calls": [],
            "files": [
                {
                    "logical_path": path,
                    "sha256": byte_digest(content),
                    "mode": mode,
                    "size": len(content.encode()),
                }
                for path, content, mode in sorted(files)
            ],
            "archive_relpath": archive_relpath,
            "archive_sha256": bytes_digest(archive_path.read_bytes()),
        }
        return {**body, "bundle_id": canonical_digest(body)}

    def retained_bundle_set(
        self, *, with_companion: bool = False
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        """Build one retained comparison with a real incumbent archive lock."""
        target = "mergecraft:getting-prs-merged"
        candidate = self.bundle(
            "retained-candidate",
            target,
            [("candidate/SKILL.md", "candidate bytes", "0644")],
        )
        candidate["root_entrypoints"] = ["candidate/SKILL.md"]
        companion_owner = "mergecraft:publishing-reviewable-prs"
        companion_files = [
            {
                "logical_path": "companion/SKILL.md",
                "mode": "0644",
                "sha256": byte_digest("companion bytes"),
                "size": len("companion bytes"),
            }
        ]
        if with_companion:
            candidate["declared_calls"] = [companion_owner]
        candidate["bundle_id"] = canonical_digest(
            {key: value for key, value in candidate.items() if key != "bundle_id"}
        )
        incumbent = self.bundle(
            "retained-incumbent",
            target,
            [("retained/SKILL.md", "immutable incumbent bytes", "0644")],
        )
        incumbent["root_entrypoints"] = ["retained/SKILL.md"]
        incumbent_archive = self.root / incumbent["archive_relpath"]
        archive_inventory = []
        with tarfile.open(incumbent_archive, "r:") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                stream = archive.extractfile(member)
                assert stream is not None
                archive_inventory.append(
                    {
                        "mode": f"{member.mode & 0o777:04o}",
                        "path": member.name,
                        "sha256": bytes_digest(stream.read()),
                    }
                )
        full_tree_lock = canonical_digest(archive_inventory)
        incumbent["source_provenance"] = {
            "content_lock_sha256": canonical_digest(
                [
                    {"path": file["logical_path"], "sha256": file["sha256"]}
                    for file in incumbent["files"]
                ]
            ),
            "full_tree_archive_relpath": incumbent["archive_relpath"],
            "full_tree_archive_sha256": incumbent["archive_sha256"],
            "full_tree_lock_sha256": full_tree_lock,
            "repository": "https://github.com/openai/plugins",
            "revision_sha256": byte_digest("immutable-gh-fix-ci-revision"),
        }
        incumbent["bundle_id"] = canonical_digest(
            {key: value for key, value in incumbent.items() if key != "bundle_id"}
        )
        composed_files = [
            ("candidate/SKILL.md", "candidate bytes", "0644"),
            ("retained/SKILL.md", "immutable incumbent bytes", "0644"),
        ]
        if with_companion:
            composed_files.append(("companion/SKILL.md", "companion bytes", "0644"))
        composed = self.bundle(
            "retained-composed",
            target,
            composed_files,
        )
        composed["root_entrypoints"] = [
            "candidate/SKILL.md",
            *(["companion/SKILL.md"] if with_companion else []),
            "retained/SKILL.md",
        ]
        composed["declared_calls"] = [
            *([companion_owner] if with_companion else []),
            "github:gh-fix-ci",
        ]
        composed["source_provenance"] = {
            "content_lock_sha256": canonical_digest(
                [
                    {"path": file["logical_path"], "sha256": file["sha256"]}
                    for file in composed["files"]
                ]
            ),
            "sources": [
                {
                    "kind": "candidate",
                    "owner": target,
                    "repository": candidate["source_provenance"]["repository"],
                    "revision_sha256": candidate["source_provenance"][
                        "revision_sha256"
                    ],
                    "root_entrypoints": ["candidate/SKILL.md"],
                    "runtime_subtree": copy.deepcopy(candidate["files"]),
                },
                *(
                    [
                        {
                            "kind": "companion",
                            "owner": companion_owner,
                            "repository": candidate["source_provenance"]["repository"],
                            "revision_sha256": candidate["source_provenance"][
                                "revision_sha256"
                            ],
                            "root_entrypoints": ["companion/SKILL.md"],
                            "runtime_subtree": companion_files,
                        }
                    ]
                    if with_companion
                    else []
                ),
                {
                    "full_tree_archive_relpath": incumbent["source_provenance"][
                        "full_tree_archive_relpath"
                    ],
                    "full_tree_archive_sha256": incumbent["source_provenance"][
                        "full_tree_archive_sha256"
                    ],
                    "full_tree_lock_sha256": incumbent["source_provenance"][
                        "full_tree_lock_sha256"
                    ],
                    "kind": "retained-incumbent",
                    "owner": "github:gh-fix-ci",
                    "repository": incumbent["source_provenance"]["repository"],
                    "revision_sha256": incumbent["source_provenance"][
                        "revision_sha256"
                    ],
                    "root_entrypoints": ["retained/SKILL.md"],
                    "runtime_subtree": copy.deepcopy(incumbent["files"]),
                },
            ],
        }
        composed["bundle_id"] = canonical_digest(
            {key: value for key, value in composed.items() if key != "bundle_id"}
        )
        return (
            {
                "candidate": candidate,
                "composed": composed,
                "incumbent": incumbent,
                "no_skill": self.bundle("retained-no-skill", target, []),
            },
            {
                "comparison_owner": "github:gh-fix-ci",
                "comparison_strategy": "retain",
                "composition_owners": [
                    target,
                    *([companion_owner] if with_companion else []),
                    "github:gh-fix-ci",
                ],
                "id": target,
                "utility_expectation_ids": ["utility"],
            },
            candidate,
        )

    def write_bytes(self, relative: str, content: bytes) -> tuple[str, str]:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return relative, bytes_digest(content)

    def write_json_artifact(self, relative: str, document: object) -> tuple[str, str]:
        return self.write_bytes(relative, canonical_bytes(document))

    def init_stream(self) -> dict[str, list[object]]:
        return {
            "agents": list(self.init_agents),
            "mcp_servers": [],
            "plugins": [],
            "skills": [],
            "slash_commands": [],
            "tools": [],
        }

    def attestation(self, executor_run_id: str) -> dict[str, object]:
        init_stream = self.init_stream()
        body = {
            "executor_run_id": executor_run_id,
            "init_stream": init_stream,
            "init_stream_sha256": canonical_digest(init_stream),
            "observed_capability_surface": copy.deepcopy(init_stream),
            "transport_contract": "model_api_only",
        }
        return {**body, "sha256": canonical_digest(body)}

    def provider_stream(
        self,
        response: bytes,
        response_id: str,
        session_id: str,
        model_version: str,
    ) -> bytes:
        init_stream = self.init_stream()
        return (
            b"\n".join(
                canonical_bytes(event)
                for event in (
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
                    },
                )
            )
            + b"\n"
        )

    def test_rejects_mixed_assistant_models_and_unbound_result_content(self) -> None:
        gate = load_gate()
        identity = {
            "local_correlation_id": "local-1",
            "model_version": "claude-sonnet-5",
            "response_id": "response-1",
            "session_id": "session-1",
        }
        mixed_stream = b"\n".join(
            canonical_bytes(event)
            for event in (
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "session-1",
                    "model": "claude-sonnet-5",
                    **self.init_stream(),
                },
                {
                    "type": "assistant",
                    "message": {
                        "id": "response-1",
                        "model": "claude-opus-4-20250514",
                        "content": [{"type": "text", "text": "unbound"}],
                    },
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
                },
            )
        )
        transport = self.root / "mixed.jsonl"
        transport.write_bytes(mixed_stream)
        with self.assertRaisesRegex(gate.MalformedInput, "assistant model"):
            gate.validate_provider_stream(
                transport,
                identity,
                "claude-sonnet-5",
                "executor",
                "answer",
                self.init_stream(),
            )

        bound_stream = mixed_stream.splitlines()
        bound_stream.pop(1)
        document = json.loads(bound_stream[-1])
        document["result"] = "different result"
        bound_stream[-1] = canonical_bytes(document)
        transport.write_bytes(b"\n".join(bound_stream))
        with self.assertRaisesRegex(gate.MalformedInput, "assistant content"):
            gate.validate_provider_stream(
                transport,
                identity,
                "claude-sonnet-5",
                "executor",
                "different result",
                self.init_stream(),
            )

        grades = [{"label": "candidate", "scores": {"safe": 2}}]
        grader_result = json.dumps({"grades": grades}, separators=(",", ":"))
        grader_stream = self.provider_stream(
            grader_result.encode(),
            "response-1",
            "session-1",
            "claude-sonnet-5",
        )
        grader_events = [json.loads(line) for line in grader_stream.splitlines()]
        grader_events[1]["message"]["content"][0]["text"] = "detached"
        transport.write_bytes(
            b"\n".join(canonical_bytes(event) for event in grader_events) + b"\n"
        )
        with self.assertRaisesRegex(gate.MalformedInput, "assistant content"):
            gate.validate_provider_stream(
                transport,
                identity,
                "claude-sonnet-5",
                "grader",
                None,
                self.init_stream(),
                grades,
            )

    def test_production_config_requires_one_exact_requested_observed_model(
        self,
    ) -> None:
        gate = load_gate()
        config = self.config("executor")
        config.update(
            {
                "adapter": "claude-cli",
                "model": "claude-sonnet-5",
                "model_version": "claude-sonnet-5",
                "runtime": {
                    "path": "/usr/local/bin/claude",
                    "sha256": "sha256:" + "a" * 64,
                    "version": "2.1.215 (Claude Code)",
                },
            }
        )
        config["config_sha256"] = canonical_digest(
            {key: value for key, value in config.items() if key != "config_sha256"}
        )
        gate.validate_config(config, "executor")
        gate.validate_production_config("executor", config)

        for requested, observed in (
            ("sonnet", "claude-sonnet-5"),
            ("claude-opus-4-8", "claude-sonnet-5"),
        ):
            with self.subTest(requested=requested, observed=observed):
                drifted = {**config, "model": requested, "model_version": observed}
                drifted["config_sha256"] = canonical_digest(
                    {
                        key: value
                        for key, value in drifted.items()
                        if key != "config_sha256"
                    }
                )
                gate.validate_config(drifted, "executor")
                with self.assertRaisesRegex(gate.MalformedInput, "one exact identity"):
                    gate.validate_production_config("executor", drifted)

    def test_retirement_evaluation_requires_the_bound_provider_contract(self) -> None:
        gate = load_gate()

        self.assertTrue(
            gate.requires_bound_provider_contract("control-plane-integrated-v1")
        )
        self.assertTrue(
            gate.requires_bound_provider_contract(
                "mergecraft-retirement-comparative-v1"
            )
        )
        self.assertFalse(
            gate.requires_bound_provider_contract("focused-test:mergecraft-phase-7")
        )

    def test_bundle_request_represents_binary_members_losslessly(self) -> None:
        gate = load_gate()
        archive_path = self.root / "bundle.tar"
        binary_content = b"\x89PNG\r\n\x1a\n\x00fixture"
        with tarfile.open(archive_path, "w") as archive:
            for logical_path, content in (
                ("SKILL.md", b"instructions"),
                ("assets/example.png", binary_content),
            ):
                member = tarfile.TarInfo(logical_path)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        bundle = {
            "archive_relpath": "bundle.tar",
            "declared_calls": [],
            "kind": "skill_bundle",
            "root_entrypoints": ["SKILL.md"],
            "target_skill": "fixture:skill",
        }

        request = json.loads(
            gate.bundle_request_bytes(bundle, b"prompt", b"fixture", self.root)
        )

        self.assertEqual(
            request["bundle_files"],
            [
                {
                    "content": "instructions",
                    "logical_path": "SKILL.md",
                },
                {
                    "content": base64.b64encode(binary_content).decode("ascii"),
                    "encoding": "base64",
                    "logical_path": "assets/example.png",
                },
            ],
        )

    def bundle_request(
        self, bundle: dict[str, object], prompt: str, fixture: str
    ) -> bytes:
        if bundle["kind"] == "no_skill":
            return canonical_bytes({"fixture": fixture, "prompt": prompt})
        bundle_files = []
        with tarfile.open(self.root / bundle["archive_relpath"], "r:") as archive:
            for member in archive.getmembers():
                if member.isfile():
                    stream = archive.extractfile(member)
                    assert stream is not None
                    bundle_files.append(
                        {
                            "logical_path": member.name,
                            "content": stream.read().decode(),
                        }
                    )
        return canonical_bytes(
            {
                "bundle_files": bundle_files,
                "declared_calls": bundle["declared_calls"],
                "fixture": fixture,
                "prompt": prompt,
                "root_entrypoints": bundle["root_entrypoints"],
                "target_skill": bundle["target_skill"],
            }
        )

    def write_attempt(
        self,
        kind: str,
        coordinate: str,
        request_relpath: str,
        request_sha256: str,
        input_sha256: str,
        response: bytes,
        response_id: str,
        session_id: str,
        local_correlation_id: str,
        model_version: str,
        started_at: str,
        finished_at: str,
    ) -> tuple[str, str, str]:
        attempt_id = f"attempt-{coordinate}"
        input_token = input_sha256.removeprefix("sha256:")
        attempt_stem = f"{coordinate}--{input_token}--{attempt_id}"
        response_relpath, response_sha256 = self.write_bytes(
            f"artifacts/attempt-responses/{kind}/{attempt_stem}.bin", response
        )
        raw_transport = self.provider_stream(
            response, response_id, session_id, model_version
        )
        transport_relpath, transport_sha256 = self.write_bytes(
            f"artifacts/attempt-transports/{kind}/{attempt_stem}.jsonl",
            raw_transport,
        )
        init_stream = self.init_stream()
        document = {
            "attempt_id": attempt_id,
            "coordinate": coordinate,
            "finished_at": finished_at,
            "init_stream": init_stream,
            "init_stream_sha256": canonical_digest(init_stream),
            "input_sha256": input_sha256,
            "kind": kind,
            "local_correlation_id": local_correlation_id,
            "model_version": model_version,
            "request_artifact_relpath": request_relpath,
            "request_sha256": request_sha256,
            "response_artifact_relpath": response_relpath,
            "response_id": response_id,
            "response_id_sha256": byte_digest(response_id),
            "response_sha256": response_sha256,
            "session_id": session_id,
            "session_id_sha256": byte_digest(session_id),
            "started_at": started_at,
            "status": "completed",
            "transport_artifact_relpath": transport_relpath,
            "transport_sha256": transport_sha256,
        }
        attempt_relpath = f"artifacts/attempts/{kind}/{attempt_stem}.json"
        _, attempt_sha256 = self.write_bytes(
            attempt_relpath,
            (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(),
        )
        return attempt_id, attempt_relpath, attempt_sha256

    def build_evidence(self, matrix: dict[str, object]) -> dict[str, object]:
        """Build the runner's modern artifact-rich schema for every green path."""
        scenario_inputs: dict[str, tuple[str, str, dict[str, object]]] = {}
        for scenario in matrix["scenarios"]:
            scenario_id = scenario["id"]
            prompt = f"prompt:{scenario_id}"
            fixture = f"fixture:{scenario_id}"
            rubric = self.rubric(scenario_id)
            self.write_bytes(
                f"artifacts/scenarios/{scenario_id}/prompt.txt", prompt.encode()
            )
            self.write_bytes(
                f"artifacts/scenarios/{scenario_id}/fixture.txt", fixture.encode()
            )
            self.write_bytes(
                f"artifacts/scenarios/{scenario_id}/rubric.json",
                canonical_bytes(rubric),
            )
            scenario_inputs[scenario_id] = (prompt, fixture, rubric)

        candidates = []
        bundles = []
        bundles_by_skill: dict[str, dict[str, object]] = {}
        for skill_id in matrix["skill_inventory"]:
            candidate_files = [("SKILL.md", f"candidate:{skill_id}", "0644")]
            candidate_bundle = self.bundle(
                f"{skill_id}-candidate", skill_id, candidate_files
            )
            conditions = {
                "no_skill": self.bundle(f"{skill_id}-no_skill", skill_id, []),
                "incumbent": self.bundle(
                    f"{skill_id}-incumbent",
                    skill_id,
                    [("SKILL.md", f"incumbent:{skill_id}", "0644")],
                ),
                "candidate": candidate_bundle,
                "composed": copy.deepcopy(candidate_bundle),
            }
            candidates.append(
                {
                    "id": f"{skill_id}-candidate",
                    "sha256": candidate_bundle["source_provenance"]["revision_sha256"],
                    "skill_id": skill_id,
                    "target_skill": skill_id,
                }
            )
            bundles.append({"skill_id": skill_id, "conditions": conditions})
            bundles_by_skill[skill_id] = conditions

        executor_config = self.config("executor")
        grader_config = self.config("grader")
        executor_config_relpath, executor_config_artifact_sha256 = (
            self.write_json_artifact("artifacts/config/executor.json", executor_config)
        )
        grader_config_relpath, grader_config_artifact_sha256 = self.write_json_artifact(
            "artifacts/config/grader.json", grader_config
        )
        executor_runs: list[dict[str, object]] = []
        attestations = []
        by_scenario: dict[str, list[dict[str, object]]] = {}
        ordinal = 0
        for scenario in matrix["scenarios"]:
            scenario_id = scenario["id"]
            prompt, fixture, _rubric = scenario_inputs[scenario_id]
            scenario_runs = []
            scenario_bundles = bundles_by_skill[scenario["skill_id"]]
            for condition in ("no_skill", "incumbent", "candidate", "composed"):
                for repetition in (1, 2, 3):
                    ordinal += 1
                    coordinate = f"{scenario_id}--{condition}--{repetition}"
                    run_id = f"executor-{ordinal:03d}"
                    output_id = f"output-{ordinal:012x}"
                    response_bytes = f"response-bytes:{run_id}".encode()
                    response_relpath, response_sha256 = self.write_bytes(
                        f"artifacts/executors/{coordinate}--{output_id}.txt",
                        response_bytes,
                    )
                    bundle = scenario_bundles[condition]
                    request = self.bundle_request(bundle, prompt, fixture)
                    request_relpath, request_sha256 = self.write_bytes(
                        f"artifacts/requests/executors/{coordinate}--{output_id}.json",
                        request,
                    )
                    input_sha256 = canonical_digest(
                        {
                            "bundle_id": bundle["bundle_id"],
                            "fixture_sha256": scenario["fixture_sha256"],
                            "prompt_sha256": scenario["prompt_sha256"],
                        }
                    )
                    local_correlation_id = f"local-correlation:{run_id}"
                    response_id = f"response-id:{run_id}"
                    session_id = f"session-id:{run_id}"
                    identity = {
                        "local_correlation_id": local_correlation_id,
                        "model_version": executor_config["model_version"],
                        "response_id": response_id,
                        "session_id": session_id,
                    }
                    identity_relpath, identity_sha256 = self.write_json_artifact(
                        f"artifacts/identities/executors/{coordinate}--{output_id}.json",
                        identity,
                    )
                    isolation = self.attestation(run_id)
                    isolation_relpath, isolation_sha256 = self.write_json_artifact(
                        f"artifacts/isolation/executors/{coordinate}--{output_id}.json",
                        isolation,
                    )
                    raw_transport = self.provider_stream(
                        response_bytes,
                        response_id,
                        session_id,
                        executor_config["model_version"],
                    )
                    transport_relpath, transport_sha256 = self.write_bytes(
                        f"artifacts/transports/executors/{coordinate}--{output_id}.jsonl",
                        raw_transport,
                    )
                    attempt_id, attempt_relpath, attempt_sha256 = self.write_attempt(
                        "executors",
                        coordinate,
                        request_relpath,
                        request_sha256,
                        input_sha256,
                        response_bytes,
                        response_id,
                        session_id,
                        local_correlation_id,
                        executor_config["model_version"],
                        "2026-07-20T00:00:00Z",
                        "2026-07-20T00:00:01Z",
                    )
                    run = {
                        "attempt_artifact_relpath": attempt_relpath,
                        "attempt_history_artifact_relpaths": [attempt_relpath],
                        "attempt_id": attempt_id,
                        "attempt_sha256": attempt_sha256,
                        "bundle_id": bundle["bundle_id"],
                        "condition": condition,
                        "config_artifact_relpath": executor_config_relpath,
                        "config_artifact_sha256": executor_config_artifact_sha256,
                        "config_sha256": executor_config["config_sha256"],
                        "finished_at": "2026-07-20T00:00:01Z",
                        "id": run_id,
                        "input_sha256": input_sha256,
                        "identity_artifact_relpath": identity_relpath,
                        "identity_sha256": identity_sha256,
                        "isolation_artifact_relpath": isolation_relpath,
                        "isolation_attestation_id": run_id,
                        "isolation_sha256": isolation_sha256,
                        "local_correlation_id_sha256": byte_digest(
                            local_correlation_id
                        ),
                        "output_id": output_id,
                        "repetition": repetition,
                        "request_artifact_relpath": request_relpath,
                        "request_sha256": request_sha256,
                        "response_artifact_relpath": response_relpath,
                        "response_id_sha256": byte_digest(response_id),
                        "response_sha256": response_sha256,
                        "scenario_id": scenario_id,
                        "session_id_sha256": byte_digest(session_id),
                        "started_at": "2026-07-20T00:00:00Z",
                        "tool_events": [],
                        "transport_artifact_relpath": transport_relpath,
                        "transport_sha256": transport_sha256,
                        "usage": {"input_tokens": 10, "output_tokens": 20},
                    }
                    executor_runs.append(run)
                    scenario_runs.append(run)
                    attestations.append(isolation)
            by_scenario[scenario_id] = scenario_runs

        grader_batches = []
        adjudications = []
        aggregate_scenarios = []
        import random

        for index, scenario in enumerate(matrix["scenarios"], 1):
            scenario_id = scenario["id"]
            prompt, fixture, rubric = scenario_inputs[scenario_id]
            natural = by_scenario[scenario_id]
            randomized = list(natural)
            seed_bytes = hashlib.sha256(f"seed:{scenario_id}".encode()).digest()
            random.Random(seed_bytes).shuffle(randomized)
            if randomized == natural:
                randomized = randomized[1:] + randomized[:1]
            presentation_order = [run["output_id"] for run in randomized]
            response_records = [
                {
                    "output_id": run["output_id"],
                    "response_sha256": run["response_sha256"],
                }
                for run in randomized
            ]
            grades = [
                {
                    "output_id": run["output_id"],
                    "expectations": [
                        {
                            "id": "safe",
                            "passed": True,
                            "evidence_sha256": byte_digest(f"safe:{run['id']}"),
                        },
                        {
                            "id": "utility",
                            "passed": run["condition"] != "no_skill",
                            "evidence_sha256": byte_digest(f"utility:{run['id']}"),
                        },
                    ],
                }
                for run in randomized
            ]
            seed_relpath, seed_sha256 = self.write_bytes(
                f"artifacts/randomization/{scenario_id}--current.bin", seed_bytes
            )
            grader_request = canonical_bytes(
                {
                    "fixture": fixture,
                    "outputs": [
                        {
                            "output_id": run["output_id"],
                            "response": (self.root / run["response_artifact_relpath"])
                            .read_bytes()
                            .decode(),
                        }
                        for run in randomized
                    ],
                    "prompt": prompt,
                    "rubric": rubric,
                }
            )
            request_relpath, request_sha256 = self.write_bytes(
                f"artifacts/requests/graders/{scenario_id}--current.json",
                grader_request,
            )
            input_sha256 = canonical_digest(
                {
                    "fixture_sha256": scenario["fixture_sha256"],
                    "prompt_sha256": scenario["prompt_sha256"],
                    "responses": response_records,
                    "rubric_sha256": scenario["rubric_sha256"],
                }
            )
            provider_response = canonical_bytes({"grades": grades})
            local_correlation_id = f"grader-local-correlation:{index}"
            response_id = f"grader-response-id:{index}"
            session_id = f"grader-session:{index}"
            identity = {
                "local_correlation_id": local_correlation_id,
                "model_version": grader_config["model_version"],
                "response_id": response_id,
                "session_id": session_id,
            }
            identity_relpath, identity_sha256 = self.write_json_artifact(
                f"artifacts/identities/graders/{scenario_id}--current.json", identity
            )
            raw_transport = self.provider_stream(
                provider_response,
                response_id,
                session_id,
                grader_config["model_version"],
            )
            transport_relpath, transport_sha256 = self.write_bytes(
                f"artifacts/transports/graders/{scenario_id}--current.jsonl",
                raw_transport,
            )
            attempt_id, attempt_relpath, attempt_sha256 = self.write_attempt(
                "graders",
                scenario_id,
                request_relpath,
                request_sha256,
                input_sha256,
                provider_response,
                response_id,
                session_id,
                local_correlation_id,
                grader_config["model_version"],
                "2026-07-20T00:00:02Z",
                "2026-07-20T00:00:03Z",
            )
            grader_artifact = {
                "condition_mapping_hidden": True,
                "grades": grades,
                "presentation_order": presentation_order,
                "randomization_seed_sha256": seed_sha256,
            }
            artifact_relpath, artifact_sha256 = self.write_bytes(
                f"artifacts/graders/{scenario_id}--current.json",
                canonical_bytes(grader_artifact),
            )
            grader_batches.append(
                {
                    "artifact_sha256": canonical_digest(grader_artifact),
                    "attempt_artifact_relpath": attempt_relpath,
                    "attempt_history_artifact_relpaths": [attempt_relpath],
                    "attempt_id": attempt_id,
                    "attempt_sha256": attempt_sha256,
                    "condition_mapping_hidden": True,
                    "config_artifact_relpath": grader_config_relpath,
                    "config_artifact_sha256": grader_config_artifact_sha256,
                    "config_sha256": grader_config["config_sha256"],
                    "finished_at": "2026-07-20T00:00:03Z",
                    "grades": grades,
                    "identity_artifact_relpath": identity_relpath,
                    "identity_sha256": identity_sha256,
                    "input_sha256": input_sha256,
                    "local_correlation_id_sha256": byte_digest(local_correlation_id),
                    "presentation_order": presentation_order,
                    "randomization_seed_artifact_relpath": seed_relpath,
                    "randomization_seed_sha256": seed_sha256,
                    "request_artifact_relpath": request_relpath,
                    "request_sha256": request_sha256,
                    "response_artifact_relpath": artifact_relpath,
                    "response_id_sha256": byte_digest(response_id),
                    "response_sha256": artifact_sha256,
                    "rubric_disclosed_at": "2026-07-20T00:00:02Z",
                    "scenario_id": scenario_id,
                    "session_id_sha256": byte_digest(session_id),
                    "started_at": "2026-07-20T00:00:02Z",
                    "tool_events": [],
                    "transport_artifact_relpath": transport_relpath,
                    "transport_sha256": transport_sha256,
                    "usage": {"input_tokens": 100, "output_tokens": 100},
                }
            )
            unblinding = [
                {"executor_run_id": run["id"], "output_id": run["output_id"]}
                for run in randomized
            ]
            adjudication = {"scenario_id": scenario_id, "unblinding": unblinding}
            adjudications.append(
                {**adjudication, "sha256": canonical_digest(adjudication)}
            )
            aggregate_scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "passes": {
                        "no_skill": {"safe": 3, "utility": 0},
                        "incumbent": {"safe": 3, "utility": 3},
                        "candidate": {"safe": 3, "utility": 3},
                        "composed": {"safe": 3, "utility": 3},
                    },
                }
            )

        skill_results = [
            {
                "nonregression_passed": True,
                "passed": True,
                "quality_passed": True,
                "safety_passed": True,
                "skill_id": skill_id,
                "utility": True,
            }
            for skill_id in matrix["skill_inventory"]
        ]
        aggregates = {"scenarios": aggregate_scenarios, "skills": skill_results}
        aggregates["sha256"] = canonical_digest(aggregates)
        invalidations = {"events": [], "closed_at": "2026-07-20T00:00:04Z"}
        invalidations["closure_sha256"] = canonical_digest(invalidations)
        return {
            "adjudications": adjudications,
            "aggregates": aggregates,
            "bundles": bundles,
            "candidates": candidates,
            "evaluation_id": matrix["evaluation_id"],
            "executor_config": executor_config,
            "executor_runs": executor_runs,
            "final_result": {"passed": True, "sha256": "placeholder"},
            "grader_config": grader_config,
            "grader_runs": grader_batches,
            "invalidations": invalidations,
            "isolation_attestations": attestations,
            "matrix_definition_sha256": "placeholder",
            "phase": "phase-1-four-condition-behavior-evidence",
            "scenarios": [
                {
                    key: scenario[key]
                    for key in (
                        "id",
                        "skill_id",
                        "prompt_sha256",
                        "fixture_sha256",
                        "rubric_sha256",
                    )
                }
                for scenario in matrix["scenarios"]
            ],
            "schema_version": 2,
        }

    def refresh_attestation(self, attestation: dict[str, object]) -> None:
        attestation["sha256"] = canonical_digest(
            {key: value for key, value in attestation.items() if key != "sha256"}
        )

    def refresh_invalidations(self, evidence: dict[str, object]) -> None:
        invalidations = evidence["invalidations"]
        invalidations["closure_sha256"] = canonical_digest(
            {"closed_at": invalidations["closed_at"], "events": invalidations["events"]}
        )

    def bundle_for(
        self,
        evidence: dict[str, object],
        condition: str,
        skill_id: str | None = None,
    ) -> dict[str, object]:
        selected_skill = skill_id or evidence["candidates"][0]["skill_id"]
        return next(
            bundle_set["conditions"][condition]
            for bundle_set in evidence["bundles"]
            if bundle_set["skill_id"] == selected_skill
        )

    def refresh_bundle_bindings(
        self,
        evidence: dict[str, object],
        matrix: dict[str, object],
        condition: str,
        skill_id: str | None = None,
    ) -> None:
        selected_skill = skill_id or evidence["candidates"][0]["skill_id"]
        bundle = self.bundle_for(evidence, condition, selected_skill)
        bundle["bundle_id"] = canonical_digest(
            {key: value for key, value in bundle.items() if key != "bundle_id"}
        )
        scenarios = {scenario["id"]: scenario for scenario in matrix["scenarios"]}
        for run in evidence["executor_runs"]:
            if (
                run["condition"] != condition
                or scenarios[run["scenario_id"]]["skill_id"] != selected_skill
            ):
                continue
            scenario = scenarios[run["scenario_id"]]
            run["bundle_id"] = bundle["bundle_id"]
            run["input_sha256"] = canonical_digest(
                {
                    "bundle_id": bundle["bundle_id"],
                    "fixture_sha256": scenario["fixture_sha256"],
                    "prompt_sha256": scenario["prompt_sha256"],
                }
            )

    def refresh_final(self, evidence: dict[str, object], passed: bool = True) -> None:
        evidence["final_result"] = {
            "passed": passed,
            "sha256": canonical_digest(
                {key: value for key, value in evidence.items() if key != "final_result"}
            ),
        }

    def write_artifacts(
        self,
        matrix: dict[str, object] | None = None,
        evidence: dict[str, object] | None = None,
    ) -> dict[str, object]:
        matrix = matrix or self.matrix()
        self.matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
        evidence = evidence or self.build_evidence(matrix)
        evidence["matrix_definition_sha256"] = (
            "sha256:" + hashlib.sha256(self.matrix_path.read_bytes()).hexdigest()
        )
        self.refresh_final(evidence)
        self.manifest_path.write_text(json.dumps(evidence), encoding="utf-8")
        return evidence

    def rewrite_evidence(self, evidence: dict[str, object]) -> None:
        self.manifest_path.write_text(json.dumps(evidence), encoding="utf-8")

    def run_gate(self) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(self.manifest_path),
            "--matrix",
            str(self.matrix_path),
        ]
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_failed(self, reason: str) -> dict[str, object]:
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["passed"])
        self.assertTrue(any(reason in error for error in payload["errors"]), payload)
        return payload

    def test_modern_small_matrix_and_equal_candidate_composed_bundles_pass(
        self,
    ) -> None:
        evidence = self.write_artifacts(self.matrix(scenario_count=2))

        result = self.run_gate()

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["passed"])
        self.assertEqual(len(payload["scenarios"]), 2)
        self.assertEqual(
            self.bundle_for(evidence, "candidate")["bundle_id"],
            self.bundle_for(evidence, "composed")["bundle_id"],
        )

    def test_retained_composition_requires_the_bound_incumbent_union(self) -> None:
        gate = load_gate()

        def validate(bundles: dict[str, object], skill: dict[str, object]) -> None:
            gate.validate_bundles(
                bundles,
                "mergecraft:getting-prs-merged",
                self.root,
                skill,
            )

        def refresh_composed_id(bundles: dict[str, object]) -> None:
            bundles["composed"]["bundle_id"] = canonical_digest(
                {
                    key: value
                    for key, value in bundles["composed"].items()
                    if key != "bundle_id"
                }
            )

        def rebuild_composed(
            bundles: dict[str, object], files: list[tuple[str, str, str]]
        ) -> None:
            original = bundles["composed"]
            replacement = self.bundle(
                "retained-composed-rebuilt",
                "mergecraft:getting-prs-merged",
                files,
            )
            replacement["declared_calls"] = original["declared_calls"]
            replacement["root_entrypoints"] = original["root_entrypoints"]
            replacement["source_provenance"] = copy.deepcopy(
                original["source_provenance"]
            )
            replacement["source_provenance"]["content_lock_sha256"] = canonical_digest(
                [
                    {"path": file["logical_path"], "sha256": file["sha256"]}
                    for file in replacement["files"]
                ]
            )
            replacement["bundle_id"] = canonical_digest(
                {key: value for key, value in replacement.items() if key != "bundle_id"}
            )
            bundles["composed"] = replacement

        bundles, skill, _ = self.retained_bundle_set()
        validate(bundles, skill)

        bundles, skill, _ = self.retained_bundle_set()
        bundles["composed"]["source_provenance"]["sources"][-1]["owner"] = (
            "github:wrong-owner"
        )
        refresh_composed_id(bundles)
        with self.assertRaisesRegex(gate.MalformedInput, "retained source identity"):
            validate(bundles, skill)

        bundles, skill, _ = self.retained_bundle_set()
        bundles["composed"]["source_provenance"]["sources"][-1]["repository"] = (
            "https://github.com/wrong/plugins"
        )
        refresh_composed_id(bundles)
        with self.assertRaisesRegex(
            gate.MalformedInput, "does not bind incumbent immutable provenance"
        ):
            validate(bundles, skill)

        bundles, skill, _ = self.retained_bundle_set()
        changed = bundles["composed"]["source_provenance"]["sources"][-1][
            "runtime_subtree"
        ][0]
        changed["sha256"] = byte_digest("changed retained bytes")
        changed["size"] = len("changed retained bytes")
        rebuild_composed(
            bundles,
            [
                ("candidate/SKILL.md", "candidate bytes", "0644"),
                ("retained/SKILL.md", "changed retained bytes", "0644"),
            ],
        )
        with self.assertRaisesRegex(
            gate.MalformedInput, "does not bind incumbent bytes"
        ):
            validate(bundles, skill)

        bundles, skill, _ = self.retained_bundle_set()
        bundles["composed"]["source_provenance"]["sources"][-1]["runtime_subtree"] = []
        bundles["composed"]["root_entrypoints"] = ["candidate/SKILL.md"]
        rebuild_composed(bundles, [("candidate/SKILL.md", "candidate bytes", "0644")])
        with self.assertRaisesRegex(gate.MalformedInput, "runtime subtree is invalid"):
            validate(bundles, skill)

        bundles, skill, _ = self.retained_bundle_set()
        rebuild_composed(
            bundles,
            [
                ("candidate/SKILL.md", "candidate bytes", "0644"),
                ("retained/SKILL.md", "immutable incumbent bytes", "0644"),
                ("injected.txt", "injected composed-only bytes", "0644"),
            ],
        )
        with self.assertRaisesRegex(
            gate.MalformedInput,
            "candidate companions and retained incumbent union",
        ):
            validate(bundles, skill)

        bundles, skill, _ = self.retained_bundle_set()
        extra_candidate = self.bundle(
            "retained-candidate-extra",
            "mergecraft:getting-prs-merged",
            [("candidate/extra.txt", "extra candidate source bytes", "0644")],
        )["files"][0]
        bundles["composed"]["source_provenance"]["sources"][0][
            "runtime_subtree"
        ].append(extra_candidate)
        rebuild_composed(
            bundles,
            [
                ("candidate/SKILL.md", "candidate bytes", "0644"),
                ("candidate/extra.txt", "extra candidate source bytes", "0644"),
                ("retained/SKILL.md", "immutable incumbent bytes", "0644"),
            ],
        )
        with self.assertRaisesRegex(
            gate.MalformedInput,
            "candidate source runtime subtree does not equal candidate bundle bytes",
        ):
            validate(bundles, skill)

        bundles, skill, _ = self.retained_bundle_set()
        replacement_archive = self.bundle(
            "retained-full-tree-without-runtime",
            "mergecraft:getting-prs-merged",
            [],
        )
        retained_source = bundles["composed"]["source_provenance"]["sources"][-1]
        retained_source["full_tree_archive_relpath"] = replacement_archive[
            "archive_relpath"
        ]
        retained_source["full_tree_archive_sha256"] = replacement_archive[
            "archive_sha256"
        ]
        retained_source["full_tree_lock_sha256"] = canonical_digest([])
        refresh_composed_id(bundles)
        with self.assertRaisesRegex(
            gate.MalformedInput,
            "retained incumbent archive omits incumbent runtime bytes",
        ):
            validate(bundles, skill)

    def test_retained_composition_accepts_ordered_candidate_companion_and_incumbent(
        self,
    ) -> None:
        gate = load_gate()
        bundles, skill, _ = self.retained_bundle_set(with_companion=True)

        identities = gate.validate_bundles(
            bundles,
            "mergecraft:getting-prs-merged",
            self.root,
            skill,
        )

        sources = bundles["composed"]["source_provenance"]["sources"]
        self.assertEqual(
            [(source["kind"], source["owner"]) for source in sources],
            [
                ("candidate", "mergecraft:getting-prs-merged"),
                ("companion", "mergecraft:publishing-reviewable-prs"),
                ("retained-incumbent", "github:gh-fix-ci"),
            ],
        )
        self.assertIn("composed", identities)

    def test_modern_21_scenario_matrix_passes(self) -> None:
        evidence = self.write_artifacts(self.matrix(scenario_count=21, skill_count=21))

        result = self.run_gate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["scenarios"]), 21)
        self.assertEqual(len(evidence["candidates"]), 21)
        self.assertEqual(len(evidence["bundles"]), 21)

    def test_one_grader_session_covers_each_randomized_12_output_batch(self) -> None:
        evidence = self.write_artifacts()
        self.assertEqual(len(evidence["grader_runs"]), 2)
        self.assertTrue(
            all(len(batch["grades"]) == 12 for batch in evidence["grader_runs"])
        )
        self.assertTrue(all("runs" not in batch for batch in evidence["grader_runs"]))
        self.assertEqual(self.run_gate().returncode, 0)

        evidence["grader_runs"][0]["grades"].pop()
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("grader batch must grade 12 outputs")

    def test_rejects_grader_response_artifact_mismatch(self) -> None:
        evidence = self.write_artifacts()
        evidence["grader_runs"][0]["response_sha256"] = byte_digest(
            "unrelated-grader-response"
        )
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)

        self.assert_failed("grader response artifact digest mismatch")

        evidence = self.write_artifacts()
        batch = evidence["grader_runs"][0]
        artifact_path = self.root / batch["response_artifact_relpath"]
        altered_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        altered_payload["condition_mapping_hidden"] = False
        altered_bytes = json.dumps(
            altered_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        artifact_path.write_bytes(altered_bytes)
        batch["response_sha256"] = bytes_digest(altered_bytes)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)

        self.assert_failed("grader response artifact does not match")

    def test_rejects_executor_response_artifact_mismatch(self) -> None:
        evidence = self.write_artifacts()
        run = evidence["executor_runs"][0]
        (self.root / run["response_artifact_relpath"]).write_bytes(b"tampered")
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)

        self.assert_failed("executor response artifact digest mismatch")

        evidence = self.write_artifacts()
        run = evidence["executor_runs"][0]
        run["response_artifact_relpath"] = "../outside.txt"
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)

        self.assert_failed("executor response artifact path is invalid")

    def test_failed_attempt_requires_bound_raw_streams_and_unavailable_identity(
        self,
    ) -> None:
        evidence = self.write_artifacts()
        run = evidence["executor_runs"][0]
        coordinate = "--".join(
            (run["scenario_id"], run["condition"], str(run["repetition"]))
        )
        attempt_id = "attempt-failed-raw"
        stem = (
            f"{coordinate}--{run['input_sha256'].removeprefix('sha256:')}--{attempt_id}"
        )
        stdout_relpath, stdout_sha256 = self.write_bytes(
            f"artifacts/attempt-stdout/executors/{stem}.bin", b""
        )
        stderr_relpath, stderr_sha256 = self.write_bytes(
            f"artifacts/attempt-stderr/executors/{stem}.bin", b""
        )
        failed = {
            "attempt_id": attempt_id,
            "coordinate": coordinate,
            "error": "TransportFailure: provider failed",
            "finished_at": run["finished_at"],
            "input_sha256": run["input_sha256"],
            "kind": "executors",
            "local_correlation_id": "failed-correlation",
            "provider_events": [],
            "provider_identity": {
                "model_version": None,
                "response_id": None,
                "session_id": None,
            },
            "request_artifact_relpath": run["request_artifact_relpath"],
            "request_sha256": run["request_sha256"],
            "response_id": None,
            "response_id_sha256": None,
            "started_at": run["started_at"],
            "status": "failed",
            "stderr_artifact_relpath": stderr_relpath,
            "stderr_sha256": stderr_sha256,
            "stdout_artifact_relpath": stdout_relpath,
            "stdout_sha256": stdout_sha256,
        }
        failed_relpath, _ = self.write_bytes(
            f"artifacts/attempts/executors/{stem}.json",
            (json.dumps(failed, indent=2, sort_keys=True) + "\n").encode(),
        )
        run["attempt_history_artifact_relpaths"].append(failed_relpath)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assertEqual(self.run_gate().returncode, 0)

        failed.pop("stdout_artifact_relpath")
        self.write_bytes(
            failed_relpath,
            (json.dumps(failed, indent=2, sort_keys=True) + "\n").encode(),
        )
        self.assert_failed("attempt schema drift")

    def test_rejects_missing_mismatched_and_unsafe_bundle_archives(self) -> None:
        evidence = self.write_artifacts()
        candidate = self.bundle_for(evidence, "candidate")
        (self.root / candidate["archive_relpath"]).unlink()
        self.assert_failed("candidate bundle archive path is invalid")

        evidence = self.write_artifacts()
        candidate = self.bundle_for(evidence, "candidate")
        (self.root / candidate["archive_relpath"]).write_bytes(b"not the archive")
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("candidate bundle archive digest mismatch")

        matrix = self.matrix()
        evidence = self.write_artifacts(matrix)
        candidate = self.bundle_for(evidence, "candidate")
        candidate["files"][0]["size"] += 1
        self.refresh_bundle_bindings(evidence, matrix, "candidate")
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("bundle archive contents do not match the manifest")

        matrix = self.matrix()
        evidence = self.write_artifacts(matrix)
        no_skill = self.bundle_for(evidence, "no_skill")
        archive_path = self.root / no_skill["archive_relpath"]
        with tarfile.open(archive_path, "w") as archive:
            member = tarfile.TarInfo("../outside")
            member.size = 0
            archive.addfile(member, io.BytesIO())
        no_skill["archive_sha256"] = bytes_digest(archive_path.read_bytes())
        self.refresh_bundle_bindings(evidence, matrix, "no_skill")
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("bundle archive contains an unsafe path")

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for logical_path in ("a/b", "a"):
                member = tarfile.TarInfo(logical_path)
                member.size = 0
                archive.addfile(member, io.BytesIO())
        gate = load_gate()
        for label in ("production candidate archive", "production incumbent archive"):
            with self.assertRaisesRegex(gate.MalformedInput, "file-directory conflict"):
                gate.archive_entries(buffer.getvalue(), label)

    def test_rejects_duplicate_bundle_logical_paths(self) -> None:
        evidence = self.write_artifacts()
        candidate = self.bundle_for(evidence, "candidate")
        duplicate = copy.deepcopy(candidate["files"][0])
        duplicate["sha256"] = byte_digest("different-content")
        candidate["files"].append(duplicate)
        candidate["files"].sort(
            key=lambda item: (item["logical_path"], item["sha256"], item["mode"])
        )
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)

        self.assert_failed("bundle logical paths must be unique")

    def test_rejects_config_and_complete_final_hash_tampering(self) -> None:
        evidence = self.write_artifacts()
        evidence["executor_config"]["model_version"] = "tampered"
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("executor config digest mismatch")

        evidence = self.write_artifacts()
        evidence["candidates"][0]["id"] = "other-candidate"
        self.rewrite_evidence(evidence)
        self.assert_failed("final result is tampered")

        evidence = self.write_artifacts()
        evidence["candidates"][0]["sha256"] = byte_digest("other-release")
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed(
            "candidate identity does not match candidate bundle provenance"
        )

    def test_old_schema_evidence_is_unconditionally_rejected(self) -> None:
        evidence = self.write_artifacts()
        for key in ("adapter", "runtime", "system_prompt", "system_prompt_sha256"):
            evidence["executor_config"].pop(key)
        evidence["executor_config"]["config_sha256"] = canonical_digest(
            {
                key: value
                for key, value in evidence["executor_config"].items()
                if key != "config_sha256"
            }
        )
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("executor config schema drift")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--manifest",
                str(self.manifest_path),
                "--matrix",
                str(self.matrix_path),
                "--allow-legacy-evidence",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("--allow-legacy-evidence", result.stdout + result.stderr)

    def test_modern_isolation_artifact_rejects_divergence_and_missing_init(
        self,
    ) -> None:
        evidence = self.write_artifacts()
        run = evidence["executor_runs"][0]
        isolation_path = self.root / run["isolation_artifact_relpath"]
        retained = json.loads(isolation_path.read_text())
        retained["transport_contract"] = "divergent-contract"
        retained["sha256"] = canonical_digest(
            {key: value for key, value in retained.items() if key != "sha256"}
        )
        retained_bytes = canonical_bytes(retained)
        isolation_path.write_bytes(retained_bytes)
        run["isolation_sha256"] = bytes_digest(retained_bytes)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)

        self.assert_failed("transport contract is invalid")

        evidence = self.write_artifacts()
        run = evidence["executor_runs"][0]
        attestation = evidence["isolation_attestations"][0]
        attestation.pop("init_stream")
        self.refresh_attestation(attestation)
        isolation_path = self.root / run["isolation_artifact_relpath"]
        isolation_bytes = canonical_bytes(attestation)
        isolation_path.write_bytes(isolation_bytes)
        run["isolation_sha256"] = bytes_digest(isolation_bytes)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)

        self.assert_failed("isolation attestation schema drift")

        evidence = self.write_artifacts()
        run = evidence["executor_runs"][0]
        attempt_path = self.root / run["attempt_artifact_relpath"]
        attempt = json.loads(attempt_path.read_text())
        transport_path = self.root / attempt["transport_artifact_relpath"]
        events = [json.loads(line) for line in transport_path.read_text().splitlines()]
        events[0].pop("tools")
        transport_bytes = b"\n".join(canonical_bytes(event) for event in events) + b"\n"
        transport_path.write_bytes(transport_bytes)
        attempt["transport_sha256"] = bytes_digest(transport_bytes)
        attempt_bytes = (json.dumps(attempt, indent=2, sort_keys=True) + "\n").encode()
        attempt_path.write_bytes(attempt_bytes)
        run["attempt_sha256"] = bytes_digest(attempt_bytes)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)

        self.assert_failed("raw init must declare every capability as a list")

        evidence = self.write_artifacts()
        run = evidence["executor_runs"][0]
        attempt_path = self.root / run["attempt_artifact_relpath"]
        attempt = json.loads(attempt_path.read_text())
        transport_path = self.root / attempt["transport_artifact_relpath"]
        events = [json.loads(line) for line in transport_path.read_text().splitlines()]
        events[0]["plugins"] = [{"name": "forbidden"}]
        transport_bytes = b"\n".join(canonical_bytes(event) for event in events) + b"\n"
        transport_path.write_bytes(transport_bytes)
        attempt["transport_sha256"] = bytes_digest(transport_bytes)
        attempt_bytes = (json.dumps(attempt, indent=2, sort_keys=True) + "\n").encode()
        attempt_path.write_bytes(attempt_bytes)
        run["attempt_sha256"] = bytes_digest(attempt_bytes)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("raw transport exposes model-access capabilities")

        evidence = self.write_artifacts()
        run = evidence["executor_runs"][0]
        attempt_path = self.root / run["attempt_artifact_relpath"]
        attempt = json.loads(attempt_path.read_text())
        transport_path = self.root / attempt["transport_artifact_relpath"]
        events = [json.loads(line) for line in transport_path.read_text().splitlines()]
        events[0]["agents"] = ["unreviewed-agent"]
        transport_bytes = b"\n".join(canonical_bytes(event) for event in events) + b"\n"
        transport_path.write_bytes(transport_bytes)
        attempt["transport_sha256"] = bytes_digest(transport_bytes)
        attempt_bytes = (json.dumps(attempt, indent=2, sort_keys=True) + "\n").encode()
        attempt_path.write_bytes(attempt_bytes)
        run["attempt_sha256"] = bytes_digest(attempt_bytes)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("raw transport contains unreviewed agent discovery metadata")

        evidence = self.write_artifacts()
        run = evidence["executor_runs"][0]
        attempt_path = self.root / run["attempt_artifact_relpath"]
        attempt = json.loads(attempt_path.read_text())
        transport_path = self.root / attempt["transport_artifact_relpath"]
        events = [json.loads(line) for line in transport_path.read_text().splitlines()]
        events[0]["future_capability"] = []
        transport_bytes = b"\n".join(canonical_bytes(event) for event in events) + b"\n"
        transport_path.write_bytes(transport_bytes)
        attempt["transport_sha256"] = bytes_digest(transport_bytes)
        attempt_bytes = (json.dumps(attempt, indent=2, sort_keys=True) + "\n").encode()
        attempt_path.write_bytes(attempt_bytes)
        run["attempt_sha256"] = bytes_digest(attempt_bytes)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("raw init contains unreviewed fields")

    def test_current_claude_builtin_agent_discovery_metadata_passes(self) -> None:
        self.init_agents = ["claude", "Explore", "general-purpose", "Plan"]
        self.write_artifacts()
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_current_exact_claude_model_identity_is_not_treated_as_an_alias(
        self,
    ) -> None:
        gate = load_gate()
        self.assertIsNotNone(gate.EXACT_CLAUDE_MODEL.fullmatch("claude-sonnet-5"))
        self.assertIsNotNone(
            gate.EXACT_CLAUDE_MODEL.fullmatch("claude-sonnet-4-20250514")
        )
        self.assertIsNone(gate.EXACT_CLAUDE_MODEL.fullmatch("sonnet"))
        self.assertIsNone(gate.EXACT_CLAUDE_MODEL.fullmatch("claude-sonnet"))

    def test_resolved_nonempty_invalidation_passes_but_stale_replacement_fails(
        self,
    ) -> None:
        matrix = self.matrix(invalidation_event_policy="required")
        evidence = self.write_artifacts(matrix)
        replacement_ids = [run["id"] for run in evidence["executor_runs"]]
        evidence["invalidations"]["events"] = [
            {
                "id": "event-1",
                "source_scenario_id": "scenario-01",
                "affected_scenario_ids": ["scenario-01", "scenario-02"],
                "occurred_at": "2026-07-19T23:59:59Z",
                "replacement_executor_run_ids": replacement_ids,
                "resolved_at": "2026-07-20T00:00:02Z",
            }
        ]
        self.refresh_invalidations(evidence)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assertEqual(self.run_gate().returncode, 0)

        evidence["invalidations"]["closed_at"] = "2026-07-20T00:00:01Z"
        self.refresh_invalidations(evidence)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("invalidation closure is stale")

        evidence["invalidations"]["closed_at"] = "2026-07-20T00:00:04Z"
        evidence["invalidations"]["events"][0]["replacement_executor_run_ids"].pop()
        self.refresh_invalidations(evidence)
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("replacement run coverage is stale")

    def test_required_invalidation_policy_rejects_empty_events(self) -> None:
        self.write_artifacts(self.matrix(invalidation_event_policy="required"))

        self.assert_failed("invalidation event is required by the matrix")

        matrix = self.matrix()
        matrix["evaluation_id"] = "production-matrix"
        self.write_artifacts(matrix)
        self.assert_failed("only for a focused-test matrix")

    def test_malformed_types_emit_schema_v2_json_and_exit_2(self) -> None:
        evidence = self.write_artifacts()
        evidence["grader_runs"][0]["started_at"] = None
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)

        result = self.run_gate()

        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["version"], 2)
        self.assertFalse(payload["passed"])
        self.assertTrue(payload["errors"])

        evidence = self.write_artifacts()
        evidence["grader_runs"][0]["started_at"] = "2026-99-99T00:00:02Z"
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)

        result = self.run_gate()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(json.loads(result.stdout)["errors"])

    def test_rejects_mapping_leak_premature_rubric_and_missing_skill_coverage(
        self,
    ) -> None:
        evidence = self.write_artifacts()
        evidence["grader_runs"][0]["condition"] = "candidate"
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("blinded grader batch schema drift")

        evidence = self.write_artifacts()
        evidence["grader_runs"][0]["rubric_disclosed_at"] = "2026-07-20T00:00:00Z"
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("rubric was disclosed before executor completion")

        matrix = self.matrix()
        matrix["skill_inventory"].append("uncovered")
        matrix["skills"].append(
            {"id": "uncovered", "utility_expectation_ids": ["utility"]}
        )
        self.write_artifacts(matrix)
        self.assert_failed("missing scenario coverage for a skill")

    def test_rejects_aggregate_tampering_tool_events_and_duplicate_keys(self) -> None:
        evidence = self.write_artifacts()
        evidence["aggregates"]["scenarios"][0]["passes"]["candidate"]["safe"] = 2
        evidence["aggregates"]["sha256"] = canonical_digest(
            {
                "scenarios": evidence["aggregates"]["scenarios"],
                "skills": evidence["aggregates"]["skills"],
            }
        )
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("aggregate scenario results are tampered")

        evidence = self.write_artifacts()
        evidence["executor_runs"][0]["tool_events"] = [{"name": "shell"}]
        self.refresh_final(evidence)
        self.rewrite_evidence(evidence)
        self.assert_failed("tool event invalidates")

        self.manifest_path.write_text(
            '{"schema_version":2,"schema_version":2}', encoding="utf-8"
        )
        self.assert_failed("duplicate JSON key")


if __name__ == "__main__":
    unittest.main()
