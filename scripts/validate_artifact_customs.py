#!/usr/bin/env python3
"""Validate the portable, public Artifact Customs plugin contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

from agent_plugins_standard import (  # noqa: E402
    AgentPluginContractError,
    discover_direct_skills,
    load_agent_plugin_manifest,
    validate_skill_resource_links,
)

PLUGIN_RELATIVE = Path("plugins/artifact-customs")
CONTENT_LOCK_RELATIVE = Path("release/plugin-content-locks/artifact-customs.json")
PUBLIC_SKILLS = (
    "assessing-third-party-components",
    "adopting-third-party-components",
    "maintaining-third-party-components",
)
ROOT_FILES = {
    ".claude-plugin/plugin.json",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "plugin.json",
    "topology.json",
    "references/component-clearance-contract.md",
    "references/component-policy-contract.md",
    "references/invocation-envelope.json",
    "references/scheduler-adapters.json",
}
SKILL_FILES = {"SKILL.md", "agents/openai.yaml"}
SKILL_REFERENCES = {
    "assessing-third-party-components": {
        "component-clearance-contract.md",
        "component-policy-contract.md",
    },
    "adopting-third-party-components": {
        "component-clearance-contract.md",
        "component-policy-contract.md",
    },
    "maintaining-third-party-components": {
        "component-clearance-contract.md",
        "component-policy-contract.md",
        "invocation-envelope.json",
        "scheduler-adapters.json",
    },
}
OUTWARD_PLUGINS = ("rolecasting", "tricritical", "versionkeeping", "mergecraft")
PHASE7_CONTROL_PROJECTION = (
    "rolecasting",
    "versionkeeping",
    "mergecraft",
    "tricritical",
)
EXPECTED_SKILL_TOPOLOGY = {
    "assessing-third-party-components": {
        "role": "clearance",
        "authority": "read-only",
        "mutates_directly": False,
        "can_cause_mutation": False,
        "requires_matching_mutation_authority": False,
        "calls": [],
        "external_calls": [
            "rolecasting:delegating-cross-agent-work",
            "tricritical:review",
            "tricritical:adjudicate",
        ],
        "terminal_statuses": [
            "cleared",
            "no-go",
            "investigation-required",
            "operator-decision",
        ],
    },
    "adopting-third-party-components": {
        "role": "adoption",
        "authority": "explicit-new-or-revised-boundary",
        "mutates_directly": False,
        "can_cause_mutation": True,
        "requires_matching_mutation_authority": True,
        "calls": ["assessing-third-party-components"],
        "external_calls": [
            "rolecasting:delegating-cross-agent-work",
            "tricritical:loop",
            "versionkeeping:checkpointing-and-publishing-git-work",
            "mergecraft:publishing-reviewable-prs",
            "mergecraft:getting-prs-merged",
        ],
        "terminal_statuses": ["adopted", "no-go", "operator-decision", "blocked"],
    },
    "maintaining-third-party-components": {
        "role": "maintenance",
        "authority": "named-existing-policy",
        "mutates_directly": False,
        "can_cause_mutation": True,
        "requires_matching_mutation_authority": True,
        "calls": ["assessing-third-party-components"],
        "external_calls": [
            "rolecasting:delegating-cross-agent-work",
            "tricritical:loop",
            "versionkeeping:checkpointing-and-publishing-git-work",
            "mergecraft:publishing-reviewable-prs",
            "mergecraft:getting-prs-merged",
        ],
        "modes": ["update", "advisory", "replace", "seal", "retire", "pull-request"],
        "terminal_statuses": [
            "maintained",
            "no-go",
            "investigation-required",
            "operator-decision",
            "blocked",
        ],
    },
}
EXPECTED_TERMINAL_STATUS_VOCABULARY = {
    "cleared": "cleared",
    "no-go": "no-go",
    "investigation-required": "deeper autonomous investigation",
    "operator-decision": "operator decision",
    "adopted": "adopted",
    "blocked": "blocked",
    "maintained": "maintained",
}
EXPECTED_INVOCATION_ENVELOPE = {
    "schema_version": 1,
    "manual_invocation": "permanent-repository-and-pr-number",
    "request_required": [
        "repository",
        "pullRequest",
        "mode",
        "requestedLifecycleAction",
        "callerIdentity",
        "candidateIdentity",
        "policyIdentity",
        "authorityIdentity",
        "autonomyMode",
    ],
    "modes": ["assess", "adopt", "maintain"],
    "lock_owner": "artifact-customs",
    "receipt_owner": "artifact-customs",
    "candidate_binding": [
        "repository",
        "base",
        "headNamespace",
        "headCommit",
        "author",
        "changedPaths",
        "componentIdentity",
    ],
    "identity_binding": [
        "mode",
        "requestedLifecycleAction",
        "callerIdentity",
        "autonomyMode",
        "candidateIdentity",
        "policyIdentity",
        "authorityIdentity",
    ],
    "idempotence": "same-bound-invocation-candidate-policy-and-authority-converges-or-rejects",
    "lifecycle_actions": {
        "assess": ["clearance"],
        "adopt": ["adopt"],
        "maintain": [
            "update",
            "advisory",
            "replace",
            "seal",
            "retire",
            "pull-request",
        ],
    },
    "mode_authority": {
        "assess": {
            "authority": "read-only",
            "authority_identity": "read-only-clearance",
        },
        "adopt": {
            "authority": "explicit-new-or-revised-boundary",
            "authority_identity": "explicit-new-or-revised-boundary",
        },
        "maintain": {
            "authority": "named-existing-policy",
            "authority_identity": "named-existing-policy",
        },
    },
    "pre_write_rebind": {
        "resolves": [
            "candidateIdentity",
            "policyIdentity",
            "authorityIdentity",
        ],
        "before": [
            "source-write",
            "policy-write",
            "retained-evidence-write",
            "forge-close-or-reject",
            "forge-publish-approve-or-merge",
        ],
        "on_drift": "invalidate-and-reassess-before-any-mutation",
    },
    "autonomy_modes": [
        {
            "id": "report-only",
            "upgrade": "never-autonomous",
            "outcome": "research-report-recommend",
        },
        {
            "id": "high-confidence",
            "upgrade": "only-after-adequate-verification-security-review-and-high-confidence-safe-judgment",
            "otherwise": "escalate-with-comprehensive-report",
        },
        {
            "id": "confidence-forward-deferred",
            "upgrade": "when-confident",
            "otherwise": "defer-to-later-research-cycle-for-ecosystem-evidence",
        },
    ],
    "recommended_autonomy_mode": "high-confidence",
    "recommendation_is_consent": False,
    "autonomy_mode_selection": (
        "operator-selected-required-for-scheduled-invocation-and-deployment"
    ),
    "compromised_artifact": "fail-closed",
    "preferred_scheduler": None,
    "preferred_cadence": None,
}
EXPECTED_SCHEDULER_ADAPTERS = {
    "schema_version": 1,
    "onboarding": {
        "inspect_first": "existing-or-possible-dependency-or-component-maintenance-schedule-or-analogous-process",
        "found_or_suspected": "offer-best-effort-integration-or-alignment-from-available-evidence",
        "uncertain_suitability": "also-offer-context-sensitive-standalone-cadence",
        "none_found": "recommend-context-sensitive-cadence",
        "operator_selects": ["activation", "cadence", "autonomyMode"],
    },
    "native_adapters": {
        "codex-chatgpt": {
            "mechanism": "harness-native-scheduled-task",
            "invokes": "references/invocation-envelope.json",
            "semantic_owner": "artifact-customs",
            "activation": "user-selected",
            "cadence": "user-selected",
            "autonomyMode": "operator-selected-required",
            "deployment_state": {
                "autonomyMode": "operator-selected-required",
            },
        },
        "claude-desktop": {
            "mechanism": "harness-native-scheduled-task",
            "invokes": "references/invocation-envelope.json",
            "semantic_owner": "artifact-customs",
            "activation": "user-selected",
            "cadence": "user-selected",
            "autonomyMode": "operator-selected-required",
            "deployment_state": {
                "autonomyMode": "operator-selected-required",
            },
        },
    },
    "foreign_harness": "common-envelope-only",
    "semantic_owner": "artifact-customs",
    "preferred_scheduler": None,
    "preferred_cadence": None,
}
PORTABILITY_MARKERS = ("/Users/", "\\\\Users\\\\", "file://", "~/.")
FORBIDDEN_PARTS = {"evals", "tests", "fixtures", "release"}
FORBIDDEN_TERMS = ("tomlkit", "phase7", "private-evidence")
FORBIDDEN_SUFFIXES = (".whl", ".tar", ".tar.gz")
SHA256 = re.compile(r"[0-9a-f]{64}$")


class ContractError(ValueError):
    """Raised for a stable, user-actionable public contract failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def strict_json(content: str, label: str):
    def build_object(pairs):
        document = {}
        for key, value in pairs:
            require(key not in document, f"duplicate key in {label}: {key}")
            document[key] = value
        return document

    def reject_constant(value: str):
        raise ContractError(f"non-finite JSON value in {label}: {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        require(math.isfinite(parsed), f"non-finite JSON value in {label}: {value}")
        return parsed

    return json.loads(
        content,
        object_pairs_hook=build_object,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )


def plugin_root(repository: Path) -> Path:
    require(not repository.is_symlink(), "repository root must not be a symlink")
    require(repository.is_dir(), "repository root is invalid")
    for path in (repository / "plugins", repository / PLUGIN_RELATIVE):
        require(not path.is_symlink(), "plugin root path contains a symlink")
    root = repository / PLUGIN_RELATIVE
    require(root.is_dir(), "plugin root is invalid")
    return root


def read(root: Path, relative: str) -> str:
    path = root / relative
    require(
        path.is_file() and not path.is_symlink(),
        f"required regular file is missing: {relative}",
    )
    return path.read_text(encoding="utf-8")


def runtime_files(root: Path) -> dict[str, Path]:
    files = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        relative_text = relative.as_posix()
        metadata = path.lstat()
        require(
            not stat.S_ISLNK(metadata.st_mode),
            f"plugin tree contains a symlink: {relative_text}",
        )
        require(
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode),
            f"plugin tree contains a special entry: {relative_text}",
        )
        require(
            "__pycache__" not in relative.parts and path.suffix != ".pyc",
            f"runtime root contains generated Python state: {relative_text}",
        )
        lowered = relative_text.lower()
        require(
            not any(part in FORBIDDEN_PARTS for part in relative.parts)
            and not lowered.endswith(FORBIDDEN_SUFFIXES)
            and not any(term in lowered for term in FORBIDDEN_TERMS),
            f"runtime root contains a development or private artifact: {relative_text}",
        )
        if path.is_file():
            files[relative_text] = path
    return files


def validate_inventory(root: Path, files: dict[str, Path]) -> None:
    root_files = {relative for relative in files if not relative.startswith("skills/")}
    require(root_files == ROOT_FILES, "plugin root file inventory drift")
    expected = ROOT_FILES | {
        f"skills/{skill}/{relative}"
        for skill in PUBLIC_SKILLS
        for relative in SKILL_FILES
    }
    expected |= {
        f"skills/{skill}/references/{reference}"
        for skill, references in SKILL_REFERENCES.items()
        for reference in references
    }
    require(set(files) == expected, "skill inventory drift")
    skill_directories = {
        path.name
        for path in (root / "skills").iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    require(skill_directories == set(PUBLIC_SKILLS), "skill inventory drift")


def validate_portability(files: dict[str, Path]) -> None:
    for relative, path in files.items():
        content = path.read_text(encoding="utf-8")
        require(
            not any(marker in content for marker in PORTABILITY_MARKERS),
            f"portability leak in {relative}",
        )


def validate_manifests(root: Path) -> None:
    canonical = load_agent_plugin_manifest(root)
    claude = strict_json(read(root, ".claude-plugin/plugin.json"), "Claude manifest")
    identity_fields = {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
    }
    require(
        set(canonical) == identity_fields | {"$schema", "extensions"}
        and canonical["name"] == "artifact-customs"
        and canonical["version"] == "1.0.0",
        "canonical Agent Plugin manifest drift",
    )
    require(
        isinstance(claude, dict)
        and set(claude) == identity_fields | {"displayName"}
        and claude["displayName"] == "Artifact Customs"
        and all(claude[field] == canonical[field] for field in identity_fields),
        "Claude manifest projection drift",
    )
    extensions = canonical["extensions"]
    require(
        isinstance(extensions, dict) and set(extensions) == {"com.openai"},
        "canonical Agent Plugin extension inventory drift",
    )
    openai = extensions["com.openai"]
    require(
        isinstance(openai, dict) and set(openai) == {"interface"},
        "Codex extension keys drift",
    )
    interface = openai["interface"]
    prompts = interface.get("defaultPrompt") if isinstance(interface, dict) else None
    require(
        isinstance(prompts, list) and len(prompts) == len(PUBLIC_SKILLS),
        "manifest prompt inventory drift",
    )
    observed = {
        token
        for prompt in prompts
        if isinstance(prompt, str)
        for token in prompt.split()
        if token.startswith("$artifact-customs:")
    }
    require(
        observed == {f"$artifact-customs:{skill}" for skill in PUBLIC_SKILLS},
        "manifest prompt inventory drift",
    )
    discovered = discover_direct_skills(root)
    require(
        discovered == tuple(sorted(PUBLIC_SKILLS)),
        "Agent Plugins direct-child skill inventory drift",
    )
    validate_skill_resource_links(root, discovered)
    for skill in PUBLIC_SKILLS:
        require(
            f"$artifact-customs:{skill}"
            in read(root, f"skills/{skill}/agents/openai.yaml"),
            f"skill interface drift: {skill}",
        )


def validate_reference_projections(root: Path) -> None:
    for skill, references in SKILL_REFERENCES.items():
        for reference in references:
            authored = root / "references" / reference
            projection = root / "skills" / skill / "references" / reference
            require(
                projection.read_bytes() == authored.read_bytes(),
                f"skill reference projection drift: {skill}/{reference}",
            )


def validate_topology(root: Path) -> None:
    topology = strict_json(read(root, "topology.json"), "topology")
    require(
        isinstance(topology, dict)
        and set(topology)
        == {
            "schema_version",
            "plugin",
            "skills",
            "outward_plugins",
            "reverse_edges_forbidden",
            "terminal_status_vocabulary",
            "phase7_control_projection",
        }
        and topology["schema_version"] == 1
        and topology["plugin"] == "artifact-customs",
        "topology identity drift",
    )
    skills = topology["skills"]
    require(
        isinstance(skills, dict) and set(skills) == set(PUBLIC_SKILLS),
        "skill inventory drift",
    )
    assessment = skills["assessing-third-party-components"]
    require(
        isinstance(assessment, dict)
        and assessment.get("authority") == "read-only"
        and assessment.get("mutates_directly") is False
        and assessment.get("can_cause_mutation") is False
        and assessment.get("requires_matching_mutation_authority") is False
        and assessment.get("calls") == [],
        "assessment authority drift",
    )
    expected_authorities = {
        "adopting-third-party-components": "explicit-new-or-revised-boundary",
        "maintaining-third-party-components": "named-existing-policy",
    }
    for skill, authority in expected_authorities.items():
        component = skills[skill]
        require(
            isinstance(component, dict)
            and component.get("authority") == authority
            and component.get("mutates_directly") is False
            and component.get("can_cause_mutation") is True
            and component.get("requires_matching_mutation_authority") is True
            and component.get("calls") == ["assessing-third-party-components"],
            f"mutation authority drift: {skill}",
        )
    outward = topology["outward_plugins"]
    require(
        isinstance(outward, list)
        and tuple(outward) == OUTWARD_PLUGINS
        and topology["reverse_edges_forbidden"] is True,
        "outward composition drift",
    )
    require(
        topology["phase7_control_projection"] == list(PHASE7_CONTROL_PROJECTION),
        "Phase 7 control projection drift",
    )
    require(
        topology["terminal_status_vocabulary"] == EXPECTED_TERMINAL_STATUS_VOCABULARY,
        "terminal status vocabulary drift",
    )
    observed_terminal_statuses = {
        status
        for skill in skills.values()
        for status in skill.get("terminal_statuses", [])
    }
    require(
        set(topology["terminal_status_vocabulary"]) == observed_terminal_statuses,
        "terminal status vocabulary inventory drift",
    )
    for skill in skills.values():
        for external_call in skill["external_calls"]:
            plugin, separator, name = external_call.partition(":")
            require(
                separator == ":" and plugin in OUTWARD_PLUGINS and name,
                f"external call syntax is invalid: {external_call}",
            )
            target = root.parent
            for part in (plugin, "skills", name, "SKILL.md"):
                target /= part
                require(
                    not target.is_symlink(),
                    f"external call traverses a symlink: {external_call}",
                )
            require(
                target.is_file(),
                f"external call does not resolve to a callable skill: {external_call}",
            )
    require(skills == EXPECTED_SKILL_TOPOLOGY, "skill topology drift")


def validate_references(root: Path) -> None:
    envelope = strict_json(
        read(root, "references/invocation-envelope.json"), "invocation envelope"
    )
    require(envelope == EXPECTED_INVOCATION_ENVELOPE, "invocation envelope drift")
    adapters = strict_json(
        read(root, "references/scheduler-adapters.json"), "scheduler adapters"
    )
    require(adapters == EXPECTED_SCHEDULER_ADAPTERS, "scheduler adapter drift")


def validate_content_lock(repository: Path, files: dict[str, Path]) -> None:
    lock_path = repository / CONTENT_LOCK_RELATIVE
    require(
        lock_path.is_file() and not lock_path.is_symlink(),
        "external content lock is missing",
    )
    lock = strict_json(lock_path.read_text(encoding="utf-8"), "content lock")
    require(
        isinstance(lock, dict)
        and set(lock) == {"schema_version", "algorithm", "files"}
        and lock["schema_version"] == 1
        and lock["algorithm"] == "sha256"
        and isinstance(lock["files"], dict)
        and set(lock["files"]) == set(files),
        "content lock drift",
    )
    for relative, digest in lock["files"].items():
        require(
            isinstance(digest, str)
            and SHA256.fullmatch(digest)
            and hashlib.sha256(files[relative].read_bytes()).hexdigest() == digest,
            f"content lock digest mismatch: {relative}",
        )


def content_lock_document(files: dict[str, Path]) -> dict:
    return {
        "schema_version": 1,
        "algorithm": "sha256",
        "files": {
            relative: hashlib.sha256(path.read_bytes()).hexdigest()
            for relative, path in sorted(files.items())
        },
    }


def write_content_lock(repository: Path) -> None:
    root = plugin_root(repository)
    document = content_lock_document(runtime_files(root))
    destination = repository / CONTENT_LOCK_RELATIVE
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def validate(repository: Path, *, source_stage: bool) -> str:
    require(type(source_stage) is bool, "validation stage must be explicit")
    validation_stage = "source-stage" if source_stage else "release-stage"
    root = plugin_root(repository)
    files = runtime_files(root)
    validate_inventory(root, files)
    validate_portability(files)
    validate_manifests(root)
    validate_topology(root)
    validate_references(root)
    validate_reference_projections(root)
    validate_content_lock(repository, files)
    return validation_stage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--source-stage", action="store_true")
    parser.add_argument("--write-content-lock", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        repository = Path(os.path.abspath(arguments.repository.expanduser()))
        if arguments.write_content_lock:
            write_content_lock(repository)
        validation_stage = validate(repository, source_stage=arguments.source_stage)
    except (
        AgentPluginContractError,
        ContractError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        print(f"Artifact Customs contract failed: {error}", file=sys.stderr)
        return 1
    print(f"Artifact Customs {validation_stage} contract passed")
    if arguments.write_content_lock:
        print("Artifact Customs external content lock updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
