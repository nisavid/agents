#!/usr/bin/env python3
"""Repository-only release validator for the Versionkeeping plugin contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

from agent_plugins_standard import (  # noqa: E402
    AgentPluginContractError,
    discover_direct_skills,
    load_agent_plugin_manifest,
    validate_skill_resource_links,
)
from evidence_transport import run_candidate_git  # noqa: E402
from refresh_transaction import replace_generated_artifacts  # noqa: E402

PLUGIN_RELATIVE = Path("plugins/versionkeeping")
EVAL_RELATIVE = Path("evals/versionkeeping")
CONTENT_LOCK_RELATIVE = Path("release/plugin-content-locks/versionkeeping.json")
CONTENT_LOCK_EXCLUSIONS = {"CHANGELOG.md", "LICENSE"}
SAFE_PLUGIN_REGULAR_FILE_MODES = frozenset({0o644, 0o755})
ROOT_FILES = {
    ".claude-plugin/plugin.json",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "plugin.json",
    "topology.json",
}
PUBLIC_SKILLS = (
    "checkpointing-and-publishing-git-work",
    "resolving-merge-conflicts",
    "using-persistent-git-worktrees",
    "syncing-forks-with-upstream",
)
EXPECTED_CALLS = {
    "checkpointing-and-publishing-git-work": [],
    "resolving-merge-conflicts": ["checkpointing-and-publishing-git-work"],
    "using-persistent-git-worktrees": ["checkpointing-and-publishing-git-work"],
    "syncing-forks-with-upstream": ["checkpointing-and-publishing-git-work"],
}
EXPECTED_OPERATIONS = {
    "checkpointing-and-publishing-git-work": [
        "git-ref-push",
        "remote-ref-deletion",
        "local-git-integration",
        "terminal-cleanup",
    ],
    "resolving-merge-conflicts": ["conflict-resolution"],
    "using-persistent-git-worktrees": ["persistent-worktree-lifecycle"],
    "syncing-forks-with-upstream": ["fork-synchronization"],
}
EXPECTED_OPERATION_OWNERS = {
    operation: skill
    for skill, operations in EXPECTED_OPERATIONS.items()
    for operation in operations
}
EXPECTED_TERMINAL_HANDOFF = {
    "target": "checkpointing-and-publishing-git-work",
    "resolver_owns": ["conflict-interpretation", "authorized-file-edits"],
    "checkpointing_owns": ["stage", "continue", "commit", "push", "abort"],
    "resolver_forbidden": ["stage", "continue", "commit", "push", "abort"],
}
CODEX_PROMPTS = {
    "checkpointing-and-publishing-git-work": (
        "Use $versionkeeping:checkpointing-and-publishing-git-work to checkpoint "
        "and publish the current Git task safely."
    ),
    "resolving-merge-conflicts": (
        "Use $versionkeeping:resolving-merge-conflicts to interpret and resolve "
        "this active Git conflict safely."
    ),
    "using-persistent-git-worktrees": (
        "Use $versionkeeping:using-persistent-git-worktrees to prepare an "
        "isolated worktree."
    ),
    "syncing-forks-with-upstream": (
        "Use $versionkeeping:syncing-forks-with-upstream to sync this fork safely."
    ),
}
REQUIRED_CHECKPOINT_TERMS = (
    "git --literal-pathspecs commit --only -- <owned paths>",
    "--force-with-lease",
    "target_only_shas",
    "removal_authorized_commits",
    "execute_git_publication.py",
    "--reviewed-plan-sha256",
    "exact reviewed plan bytes",
    "plan_git_remote_ref_deletion.py",
    "execute_git_remote_ref_deletion.py",
    "verified merge outcome",
    "expected target SHA",
    "POST_DELETION_STATE_UNKNOWN",
    "ambient Git hooks",
    "normal configured remote name is discovery metadata",
    "not a read-only command",
    "`default_branch_policy`",
    "`direct_push_permitted`",
    "observed default branch",
    "Git does not provide a lease for symbolic `HEAD`",
    "Server-enforced repository policy/protection",
)
REQUIRED_TERMINAL_TERMS = (
    "dirty, directly agent-created worktree has a retention-only route",
    "durable, operator-visible, same-filesystem quarantine",
    "retain its Git worktree registration and branch",
    "`quarantine <selected-quarantine-path>`",
    "Prove that the selected path is absent",
    "safe non-symlink directory",
    "never deletion of retained bytes, worktree registration, or branch",
    "stable, lexically sorted repository-relative path inventory",
    "every index, worktree, untracked, and ignored entry",
    "ignored top-level sentinel",
    "explicit absent marker",
    "Hash regular files by streaming their bytes, including large files",
    "top-level worktree or submodule root",
    "identity is unknown and the worktree is preserved in place",
    "same-path identity change, including changed bytes with the same path and status",
    "fresh exact `quarantine <selected-quarantine-path>` confirmation",
    "harness-created worktree",
    "directly agent-created worktree",
    "unknown-provenance worktree",
    "Never run global `git worktree prune`",
    "A file such as `late.bin` created before or during that atomic move travels",
    "A file created at the old path after the move is outside the moved worktree",
    "`git worktree repair <selected-quarantine-path>`",
    "reciprocal `.git`/common-directory `gitdir` records",
    "preserve the quarantined directory and branch in place",
    "Never run `git worktree remove --force`",
)
REQUIRED_RECURSIVE_SUBMODULE_TERMS = (
    "Define each submodule identity recursively as its recorded gitlink, worktree HEAD, and a recursive content-addressed dirty snapshot.",
    "stable, lexically sorted repository-relative path inventory of every index, worktree, untracked, and ignored entry",
    "Bind each entry's bytes, type, and executable mode",
    "top-level inventory, byte, type, executable-mode, absence-marker, symlink no-follow, and finite-budget rules unchanged",
    "Apply the top-level inventory, byte, type, executable-mode, absence-marker, symlink no-follow, and finite-budget rules unchanged to each nested submodule",
    "Observe symlinks as entries without following them outside the top-level worktree or submodule root",
    "unreadable or cannot be fully enumerated",
    "a path is unsafe or escapes its root",
    "recursion is cyclic",
    "the identity is unknown and the worktree is preserved in place",
    "same-HEAD, same-status nested byte change such as `vendor/x/notes.md` inside submodule `vendor/x`",
)
FORBIDDEN_TERMINAL_CONTRADICTIONS = (
    "recorded gitlink and worktree head are sufficient",
    "nested dirty bytes may be ignored",
    "ignore nested untracked entries",
)
DESTRUCTIVE_DISCARD_SEMANTIC_SHA256 = (
    "7f6c7bb02904affe83e951f440a8fe06a4ff7fb8ef48fbf0754b4478a5b3c809"
)
FORK_REPROBE_SEMANTIC_SHA256 = (
    "d34a7523dbc317741d1a0a6b83620b5b4e5ec8ce07e3a26e27112f239240a450"
)
CHECKPOINT_BEHAVIOR_EVALS_SEMANTIC_SHA256 = (
    "6aed75cd2f3d592d6d8f41058e3d8e134130b354904c2a5a0bc7124ddfd8e030"
)
CHECKPOINT_TRIGGER_EVALS_SEMANTIC_SHA256 = (
    "5365231d841e86057c92b93a5bedd4a979b781b955f8a8f7a4762613554377d3"
)
EXPECTED_DISCARD_EVAL_SCENARIOS = {
    "discard-reenumeration": {
        "id": "discard-reenumeration",
        "fixture_paths": ["versionkeeping/fixtures/discard-reenumeration.md"],
        "prompt": "Route the requested destructive discard using the supplied confirmation and current state.",
        "must_include": [
            "re-enumerate",
            "branch",
            "worktree",
            "commit",
            "dirty snapshot",
            "content-addressed",
            "quarantine",
            "same-filesystem",
            "registered worktree",
            "retained branch",
            "ignored top-level sentinel",
            "late.bin",
            "same-path identity change",
            "recursive submodule dirty snapshot",
            "stable lexical path inventory",
            "same-HEAD same-status nested change",
            "fresh exact quarantine confirmation",
        ],
        "must_not_include": [
            "stale confirmation is sufficient",
            "force remove the dirty worktree",
            "delete the retained branch",
            "delete late.bin at the old path",
            "gitlink and worktree HEAD are sufficient",
            "ignore nested untracked or ignored entries",
        ],
    },
    "discard-inventory-budget-exceeded": {
        "id": "discard-inventory-budget-exceeded",
        "fixture_paths": [
            "versionkeeping/fixtures/discard-inventory-budget-exceeded.md"
        ],
        "prompt": "Determine whether destructive cleanup remains authorized after the recursive inventory exceeds its finite bounds and keeps changing.",
        "must_include": [
            "identity is unknown",
            "cleanup is blocked",
            "16 submodule levels",
            "100,000 entries",
            "three complete attempts",
        ],
        "must_not_include": [
            "discard immediately",
            "truncate inventory",
            "sample entries",
        ],
    },
    "discard-content-budget-exceeded": {
        "id": "discard-content-budget-exceeded",
        "fixture_paths": ["versionkeeping/fixtures/discard-content-budget-exceeded.md"],
        "prompt": "Determine whether destructive cleanup remains authorized after nested content exceeds its finite byte bounds and keeps growing.",
        "must_include": [
            "identity is unknown",
            "cleanup is blocked",
            "1 GiB per-file",
            "8 GiB total",
            "three complete attempts",
        ],
        "must_not_include": [
            "discard immediately",
            "hash only a prefix",
            "skip the large file",
        ],
    },
}
EXPECTED_DISCARD_FIXTURE_SEMANTIC_SHA256 = {
    "versionkeeping/fixtures/discard-reenumeration.md": (
        "d3c3f0e40b5b14545e8d6d601167535ca665c54284e3de51474650075f9a3882"
    ),
    "versionkeeping/fixtures/discard-inventory-budget-exceeded.md": (
        "55dc30d71dd235a3f77a7971cc4a12e4252f20bb509a7db7cc1fa2fd91eba5f8"
    ),
    "versionkeeping/fixtures/discard-content-budget-exceeded.md": (
        "ce0b12bbfd0a06d27577dd9214b21ac3df3ccb8100b944ab98ed2ebebefd170f"
    ),
}
FORBIDDEN_OWNERS = (
    "Mergecraft",
    "publishing-reviewable-prs",
    "getting-prs-merged",
    "onboarding-forks-for-agent-maintenance",
    "writing-reviewable-pr-descriptions",
)
MACHINE_LOCAL_MARKERS = ("/Users/ivan", "chezmoi.wt", ".local/share/chezmoi")
RELEASE_VERSION = "1.0.0"
DIRECT_LOCAL_INTEGRATION = re.compile(
    r"(?m)^[ \t]*git[ \t]+(?:merge(?:[ \t]|$)|rebase(?:[ \t]|$)|"
    r"cherry-pick(?:[ \t]|$)|am(?:[ \t]|$))"
)


class ContractError(ValueError):
    """Raised for a stable, user-actionable plugin contract failure."""


def fail(message: str) -> None:
    raise ContractError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def normalized_semantic_sha256(content: str) -> str:
    return hashlib.sha256(" ".join(content.split()).encode()).hexdigest()


def strict_json(content: str, label: str):
    def build_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str):
        fail(f"non-finite JSON value in {label}: {value}")

    return json.loads(
        content,
        object_pairs_hook=build_object,
        parse_constant=reject_constant,
    )


def plugin_root(repo_root: Path) -> Path:
    require(not repo_root.is_symlink(), "repository root must not be a symlink")
    root = (repo_root / PLUGIN_RELATIVE).resolve(strict=True)
    resolved_repo = repo_root.resolve(strict=True)
    require(
        root.is_relative_to(resolved_repo) and root.is_dir(), "plugin root is invalid"
    )
    for parent in (repo_root / "plugins", repo_root / PLUGIN_RELATIVE):
        require(not parent.is_symlink(), "plugin root path must not contain symlinks")
    return root


def read(root: Path, relative: str) -> str:
    path = root / relative
    require(
        not path.is_symlink() and path.is_file(),
        f"required regular file is missing: {relative}",
    )
    return path.read_text(encoding="utf-8")


def read_repository_file(repo_root: Path, relative: Path) -> str:
    require(
        not relative.is_absolute() and ".." not in relative.parts,
        f"repository-relative path is invalid: {relative}",
    )
    path = repo_root / relative
    require(
        not path.is_symlink() and path.is_file(),
        f"required regular file is missing: {relative.as_posix()}",
    )
    return path.read_text(encoding="utf-8")


def validate_tree_entries(root: Path) -> None:
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        require(not path.is_symlink(), f"plugin tree contains a symlink: {relative}")
        require(
            path.is_dir() or path.is_file(),
            f"plugin tree contains a special entry: {relative}",
        )
        require(
            "__pycache__" not in relative.parts and path.suffix != ".pyc",
            f"plugin tree contains generated Python state: {relative}",
        )


def validate_root_inventory(root: Path) -> None:
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).parts[0] != "skills"
    }
    require(actual == ROOT_FILES, "plugin root file inventory drift")


def validate_all_json_documents(root: Path) -> None:
    for path in root.rglob("*.json"):
        relative = path.relative_to(root).as_posix()
        strict_json(read(root, relative), relative)


def validate_topology(root: Path) -> tuple[str, ...]:
    topology = strict_json(read(root, "topology.json"), "topology")
    require(
        isinstance(topology, dict) and "terminal_handoff" in topology,
        "terminal handoff metadata drift",
    )
    require(
        isinstance(topology, dict)
        and set(topology)
        == {
            "schema_version",
            "plugin",
            "skills",
            "operation_owners",
            "ownership",
            "terminal_handoff",
        },
        "topology fields drift",
    )
    require(
        type(topology["schema_version"]) is int
        and topology["schema_version"] == 2
        and topology["plugin"] == "versionkeeping",
        "topology identity drift",
    )
    require(
        topology["terminal_handoff"] == EXPECTED_TERMINAL_HANDOFF,
        "terminal handoff metadata drift",
    )
    skills = topology["skills"]
    require(isinstance(skills, list), "topology skills must be an array")
    names = tuple(component.get("name") for component in skills)
    require(names == PUBLIC_SKILLS, "topology public skill inventory drift")
    actual_skill_dirs = {
        path.name
        for path in (root / "skills").iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    require(actual_skill_dirs == set(names), "plugin skill directory inventory drift")
    for component in skills:
        require(
            set(component)
            == {
                "name",
                "entrypoint",
                "interface",
                "references",
                "scripts",
                "modules",
                "calls",
                "operations",
            },
            f"topology component fields drift: {component.get('name')}",
        )
        expected_entrypoint = f"skills/{component['name']}/SKILL.md"
        expected_interface = f"skills/{component['name']}/agents/openai.yaml"
        require(
            component["entrypoint"] == expected_entrypoint, "topology entrypoint drift"
        )
        require(
            component["interface"] == expected_interface, "topology interface drift"
        )
        name = component["name"]
        require(component["calls"] == EXPECTED_CALLS[name], f"call graph drift: {name}")
        require(
            component["operations"] == EXPECTED_OPERATIONS[name],
            f"skill operation drift: {name}",
        )
        for field in ("entrypoint", "interface"):
            read(root, component[field])
        for field in ("references", "scripts", "modules"):
            require(
                isinstance(component[field], list), f"topology {field} must be an array"
            )
            for relative in component[field]:
                require(
                    isinstance(relative, str)
                    and not Path(relative).is_absolute()
                    and ".." not in Path(relative).parts,
                    f"topology path is not plugin-relative: {relative}",
                )
                read(root, relative)
        declared_runtime = {
            component["entrypoint"],
            component["interface"],
            *component["references"],
            *component["scripts"],
            *component["modules"],
        }
        skill_root = root / "skills" / component["name"]
        actual_runtime = {
            path.relative_to(root).as_posix()
            for path in skill_root.rglob("*")
            if path.is_file()
            and path.relative_to(skill_root).parts[0] not in {"evals", "tests"}
        }
        require(
            actual_runtime == declared_runtime,
            f"topology runtime inventory drift: {component['name']}",
        )
    require(
        topology["operation_owners"] == EXPECTED_OPERATION_OWNERS,
        "operation owner drift",
    )
    declared_operations = {
        operation for component in skills for operation in component["operations"]
    }
    require(
        declared_operations == set(topology["operation_owners"]),
        "operation declaration drift",
    )
    for source, calls in EXPECTED_CALLS.items():
        require(source not in calls, f"self call in topology: {source}")
        require(
            all(target in PUBLIC_SKILLS for target in calls),
            f"unknown call target in topology: {source}",
        )
    terminal_handoff = topology["terminal_handoff"]
    require(
        terminal_handoff["target"] in EXPECTED_CALLS["resolving-merge-conflicts"]
        and terminal_handoff["resolver_owns"]
        == ["conflict-interpretation", "authorized-file-edits"]
        and terminal_handoff["checkpointing_owns"]
        == terminal_handoff["resolver_forbidden"],
        "terminal handoff topology consistency drift",
    )
    ownership = topology["ownership"]
    require(
        isinstance(ownership, dict) and set(ownership) == {"owned", "excluded"},
        "topology ownership fields drift",
    )
    require(
        "local Git integration through checkpointing-and-publishing-git-work"
        in ownership["owned"]
        and "conflict interpretation and authorized file edits" in ownership["owned"]
        and "history-preserving fork synchronization discovery and coordination"
        in ownership["owned"],
        "topology owned operations drift",
    )
    require(
        "Graphite operations" in ownership["excluded"]
        and "review and pull-request actuation" in ownership["excluded"]
        and "model and delegation policy" in ownership["excluded"],
        "topology excluded operations drift",
    )
    return names


def load_manifest(root: Path, relative: str) -> dict:
    payload = strict_json(read(root, relative), relative)
    require(isinstance(payload, dict), f"{relative} must contain an object")
    return payload


def validate_manifests(root: Path) -> None:
    codex = load_agent_plugin_manifest(root)
    claude = load_manifest(root, ".claude-plugin/plugin.json")
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
        set(codex) == identity_fields | {"$schema", "extensions"},
        "canonical Agent Plugin manifest keys drift",
    )
    require(
        set(claude) == identity_fields | {"displayName"},
        "Claude manifest keys drift",
    )
    for field in identity_fields:
        require(
            claude[field] == codex[field], f"Claude manifest projection drift: {field}"
        )
    require(codex["name"] == "versionkeeping", "canonical manifest name drift")
    require(codex["version"] == RELEASE_VERSION, "canonical manifest version drift")
    require(codex["license"] == "MIT", "canonical manifest license drift")
    require(
        codex["repository"] == "https://github.com/nisavid/agents",
        "canonical manifest repository drift",
    )
    require(claude["displayName"] == "Versionkeeping", "Claude displayName drift")
    extensions = codex["extensions"]
    require(
        isinstance(extensions, dict) and set(extensions) == {"com.openai"},
        "canonical manifest extension inventory drift",
    )
    openai = extensions["com.openai"]
    require(
        isinstance(openai, dict) and set(openai) == {"interface"},
        "Codex extension keys drift",
    )
    interface = openai["interface"]
    require(isinstance(interface, dict), "Codex interface must be an object")
    require(
        set(interface)
        == {
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
            "capabilities",
            "websiteURL",
            "brandColor",
            "defaultPrompt",
        },
        "Codex interface keys drift",
    )
    prompts = interface.get("defaultPrompt")
    require(prompts == list(CODEX_PROMPTS.values()), "Codex default prompts drift")
    discovered = discover_direct_skills(root)
    require(
        discovered == tuple(sorted(PUBLIC_SKILLS)),
        "Agent Plugins direct-child skill inventory drift",
    )
    validate_skill_resource_links(root, discovered)
    changelog = read(root, "CHANGELOG.md")
    require(
        re.search(rf"^## {re.escape(RELEASE_VERSION)}$", changelog, re.MULTILINE)
        is not None,
        "changelog release drift",
    )


def validate_skills(root: Path, skills: tuple[str, ...]) -> None:
    for skill in skills:
        content = read(root, f"skills/{skill}/SKILL.md")
        require(
            re.search(rf"^name: {re.escape(skill)}$", content, re.MULTILINE)
            is not None,
            f"skill frontmatter drift: {skill}",
        )
        adapter = read(root, f"skills/{skill}/agents/openai.yaml")
        require(
            CODEX_PROMPTS[skill] in adapter, f"namespaced adapter prompt drift: {skill}"
        )
    checkpoint = read(root, "skills/checkpointing-and-publishing-git-work/SKILL.md")
    publication = read(
        root,
        "skills/checkpointing-and-publishing-git-work/references/"
        "publication-execution.md",
    )
    publication_contract = checkpoint + "\n" + publication
    for term in REQUIRED_CHECKPOINT_TERMS:
        require(
            term in publication_contract,
            f"checkpoint safety contract missing: {term}",
        )
    for term in (
        "--reviewed-plan-sha256",
        "exact reviewed plan bytes",
        "core.hooksPath",
        "ambient Git hooks",
        "ordinary publication planner and executor never delete",
        "POST_DELETION_STATE_UNKNOWN",
    ):
        require(
            term in publication,
            f"publication execution contract missing: {term}",
        )
    require(
        "[publication execution](references/publication-execution.md)" in checkpoint,
        "checkpoint publication reference handoff missing",
    )
    normalized_checkpoint = " ".join(checkpoint.split())
    require(
        "resolving-merge-conflicts" in normalized_checkpoint
        and "this skill alone owns the resulting stage, continue, commit, abort, and push mechanics"
        in normalized_checkpoint
        and "Never interpret conflict intent or edit conflicted files"
        in normalized_checkpoint,
        "checkpoint conflict handoff boundary missing",
    )
    for boundary in (
        "Graphite stack operations",
        "pull-request creation",
        "merge actuation",
        "model and delegation policy",
        "local Git integration",
    ):
        require(
            boundary in normalized_checkpoint,
            f"checkpoint ownership boundary missing: {boundary}",
        )
    resolver = read(root, "skills/resolving-merge-conflicts/SKILL.md")
    normalized_resolver = " ".join(resolver.split())
    for term in (
        "conflict interpretation and authorized file edits",
        "stage, continue, commit, and push",
        "authorized abort",
        "never commits its own resolution",
        "Return exactly one terminal handoff",
    ):
        require(
            term in normalized_resolver,
            f"conflict resolver ownership boundary missing: {term}",
        )
    for forbidden in (
        "git add ",
        "git commit ",
        "git merge --continue",
        "git rebase --continue",
        "git cherry-pick --continue",
        "git revert --continue",
    ):
        require(
            forbidden not in resolver,
            "conflict resolver performs checkpoint-owned Git mechanics",
        )
    terminal = read(
        root,
        "skills/checkpointing-and-publishing-git-work/references/terminal-cleanup.md",
    )
    normalized_terminal = " ".join(terminal.split())
    for term in REQUIRED_TERMINAL_TERMS:
        require(
            term.lower() in normalized_terminal.lower(),
            f"terminal cleanup contract missing: {term}",
        )
    for term in REQUIRED_RECURSIVE_SUBMODULE_TERMS:
        require(
            term.lower() in normalized_terminal.lower(),
            f"recursive submodule identity contract missing: {term}",
        )
    for contradiction in FORBIDDEN_TERMINAL_CONTRADICTIONS:
        require(
            contradiction not in normalized_terminal.lower(),
            f"terminal cleanup contract contradiction: {contradiction}",
        )
    destructive_sections = terminal.split("## Destructive discard\n")
    require(
        len(destructive_sections) == 2
        and normalized_semantic_sha256(destructive_sections[1])
        == DESTRUCTIVE_DISCARD_SEMANTIC_SHA256,
        "destructive discard semantic contract drift",
    )
    for term in (
        "Pull-request merge actuation remains outside Versionkeeping",
        "explicit repository and operator authorization",
        "expected target SHA",
    ):
        require(
            term in terminal,
            f"terminal remote deletion contract missing: {term}",
        )
    worktrees = read(root, "skills/using-persistent-git-worktrees/SKILL.md")
    normalized_worktrees = " ".join(worktrees.split())
    require(
        "checkpointing skill is the sole terminal cleanup owner"
        in normalized_worktrees,
        "worktree terminal cleanup handoff missing",
    )
    for term in (
        "Graphite worktrees or stack operations",
        "review or pull-request actuation",
        "model and delegation policy",
        "scripts/validate_worktree_target.py",
        "--create",
        "current-user-owned and not group- or world-writable",
        "disables ambient Git hooks and terminal prompts",
        "credential-minimized environment",
        "exact symbolic branch",
        "after the mutator starts is `unknown`",
        "do not run concurrent worktree mutators",
    ):
        require(
            term in normalized_worktrees, f"worktree ownership boundary missing: {term}"
        )
    forks = read(root, "skills/syncing-forks-with-upstream/SKILL.md")
    normalized_forks = " ".join(forks.split())
    for term in (
        "<fork-remote>",
        "<upstream-remote>",
        "<target-ref>",
        "<upstream-ref>",
        (
            "Pull-request creation, text, readiness, review resolution, and merge "
            "actuation"
        ),
        "Graphite operations",
        "model or delegation policy",
        "Fork synchronization discovery and coordination are owned operations",
        "Local Git integration, including selecting and running the merge",
        "belongs only to `checkpointing-and-publishing-git-work`",
        "must not run local Git integration directly",
        "hand the exact repository and worktree identity, branch, `fork_target_sha`, `upstream_sha`, and merge intent",
        "Require checkpointing to return exact verified integration evidence",
        "synchronized_commit_sha",
        "exactly one tab-separated record",
        "--show-object-format=storage",
        "`sha1` requires 40 lowercase",
        "`sha256` requires 64",
        "Unknown or mixed formats",
        "Empty, multiple, malformed, symbolic, stale-variable, or otherwise unparseable",
        "equal `synchronized_commit_sha` exactly before checking that the freshly re-probed upstream SHA is its ancestor",
    ):
        require(term in normalized_forks, f"fork contract missing: {term}")
    for forbidden in ("git fetch origin upstream", "origin/main", "upstream/main"):
        require(
            forbidden not in forks,
            f"fork contract contains default literal: {forbidden}",
        )
    require(
        DIRECT_LOCAL_INTEGRATION.search(forks) is None,
        "fork contract performs direct local Git integration",
    )
    fork_verification_sections = forks.split("## Publish And Verify\n")
    require(
        len(fork_verification_sections) == 2
        and normalized_semantic_sha256(fork_verification_sections[1])
        == FORK_REPROBE_SEMANTIC_SHA256,
        "fork re-probe verification contract drift",
    )


def validate_portability_and_ownership(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in MACHINE_LOCAL_MARKERS:
            require(
                marker not in content,
                f"machine-local portability leak in {path.relative_to(root)}",
            )
        if path.suffix != ".py":
            for owner in FORBIDDEN_OWNERS:
                require(
                    owner not in content,
                    f"forbidden workflow owner in {path.relative_to(root)}",
                )


def validate_external_eval_tree(repo_root: Path) -> None:
    root = repo_root / EVAL_RELATIVE
    require(root.is_dir() and not root.is_symlink(), "external eval root is invalid")
    for path in root.rglob("*"):
        relative = path.relative_to(repo_root)
        require(
            not path.is_symlink(), f"external eval tree contains a symlink: {relative}"
        )
        require(
            path.is_file() or path.is_dir(),
            f"external eval tree contains a special entry: {relative}",
        )
        if path.is_file() and path.suffix == ".json":
            strict_json(path.read_text(encoding="utf-8"), relative.as_posix())
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for marker in MACHINE_LOCAL_MARKERS:
                require(
                    marker not in content,
                    f"machine-local portability leak in {relative}",
                )


def validate_checkpoint_external_evals(repo_root: Path) -> None:
    behavior_relative = (
        EVAL_RELATIVE / "skills/checkpointing-and-publishing-git-work/evals.json"
    )
    behavior_text = read_repository_file(repo_root, behavior_relative)
    behavior = strict_json(behavior_text, "checkpoint behavior evals")
    require(
        isinstance(behavior, dict)
        and set(behavior) == {"skill_name", "evals"}
        and behavior["skill_name"] == "checkpointing-and-publishing-git-work",
        "behavior eval contract schema drift",
    )
    evals = behavior["evals"]
    require(isinstance(evals, list) and evals, "behavior eval contract is empty")
    for evaluation in evals:
        require(
            isinstance(evaluation, dict)
            and set(evaluation)
            == {
                "id",
                "name",
                "prompt",
                "fixture_paths",
                "expected_output",
                "expectations",
            }
            and type(evaluation["id"]) is int
            and all(
                isinstance(evaluation[field], str) and evaluation[field]
                for field in ("name", "prompt", "expected_output")
            )
            and isinstance(evaluation["fixture_paths"], list)
            and evaluation["fixture_paths"]
            and all(
                isinstance(path, str) and path for path in evaluation["fixture_paths"]
            )
            and isinstance(evaluation["expectations"], list)
            and evaluation["expectations"],
            "behavior eval contract shape drift",
        )
        for expectation in evaluation["expectations"]:
            require(
                isinstance(expectation, dict)
                and set(expectation) == {"id", "text", "severity"}
                and all(
                    isinstance(expectation[field], str) and expectation[field]
                    for field in ("id", "text", "severity")
                ),
                "behavior eval contract expectation drift",
            )
    evaluation_seven = next(
        (evaluation for evaluation in evals if evaluation["id"] == 7),
        None,
    )
    require(evaluation_seven is not None, "behavior eval seven safety oracle drift")
    evaluation_seven_text = " ".join(
        [
            evaluation_seven["expected_output"],
            *(expectation["text"] for expectation in evaluation_seven["expectations"]),
        ]
    ).lower()
    for required in (
        "exact quarantine confirmation",
        "same-filesystem",
        "atomic move",
        "exact-target registration repair",
        "repaired worktree registration",
        "retained branch",
        "never force",
    ):
        require(
            required in evaluation_seven_text,
            "behavior eval seven safety oracle drift",
        )
    require(
        "dirty forced worktree removal" not in evaluation_seven_text
        and "git worktree remove --force" not in evaluation_seven_text,
        "behavior eval seven safety oracle drift",
    )
    require(
        normalized_semantic_sha256(behavior_text)
        == CHECKPOINT_BEHAVIOR_EVALS_SEMANTIC_SHA256,
        "behavior eval semantic contract drift",
    )

    trigger_relative = (
        EVAL_RELATIVE
        / "skills/checkpointing-and-publishing-git-work/trigger-evals.json"
    )
    trigger_text = read_repository_file(repo_root, trigger_relative)
    triggers = strict_json(trigger_text, "checkpoint trigger evals")
    require(
        isinstance(triggers, list) and triggers,
        "trigger eval contract is empty",
    )
    require(
        all(
            isinstance(trigger, dict)
            and set(trigger) == {"query", "should_trigger"}
            and isinstance(trigger["query"], str)
            and trigger["query"]
            and type(trigger["should_trigger"]) is bool
            for trigger in triggers
        ),
        "trigger eval contract shape drift",
    )
    trigger_polarity = {
        trigger["query"]: trigger["should_trigger"] for trigger in triggers
    }
    require(
        len(trigger_polarity) == len(triggers),
        "trigger eval contract contains duplicate queries",
    )
    for negative_query in (
        "Draft a birthday invitation in plain text.",
        "Explain what git push --force-with-lease means; take no action.",
        "Summarize this pasted diff without accessing a repository or taking action.",
        "Calculate these invoice totals from the pasted table.",
        "Explain conventional commit syntax but do not inspect or modify Git state.",
    ):
        require(
            trigger_polarity.get(negative_query) is False,
            "trigger eval semantic contract drift: known negative polarity",
        )
    require(
        any(trigger_polarity.values()) and not all(trigger_polarity.values()),
        "trigger eval semantic contract polarity drift",
    )
    require(
        normalized_semantic_sha256(trigger_text)
        == CHECKPOINT_TRIGGER_EVALS_SEMANTIC_SHA256,
        "trigger eval semantic contract drift",
    )


def validate_evals(repo_root: Path) -> None:
    corpus_relative = EVAL_RELATIVE / "corpus.json"
    corpus_text = read_repository_file(repo_root, corpus_relative)
    for marker in MACHINE_LOCAL_MARKERS:
        require(
            marker not in corpus_text, "machine-local portability leak in eval corpus"
        )
    corpus = strict_json(corpus_text, "eval corpus")
    require(
        isinstance(corpus, dict)
        and type(corpus.get("version")) is int
        and corpus.get("version") == 1,
        "eval corpus version drift",
    )
    scenarios = corpus.get("scenarios")
    require(
        isinstance(scenarios, list) and len(scenarios) == 11,
        "eval corpus scenario count drift",
    )
    expected_ids = {
        "ownership-reversal",
        "cleanup-provenance",
        "discard-reenumeration",
        "discard-inventory-budget-exceeded",
        "discard-content-budget-exceeded",
        "portable-resolution",
        "non-default-fork-sync",
        "persistent-worktree-containment",
        "ownership-reverse-edges",
        "merged-remote-ref-cleanup",
        "conflict-resolution-boundary",
    }
    require(
        {scenario.get("id") for scenario in scenarios if isinstance(scenario, dict)}
        == expected_ids,
        "eval corpus coverage drift",
    )
    for scenario in scenarios:
        require(
            set(scenario)
            == {"id", "fixture_paths", "prompt", "must_include", "must_not_include"},
            "eval corpus schema drift",
        )
        require(
            all(
                isinstance(scenario[key], list) and scenario[key]
                for key in ("must_include", "must_not_include")
            ),
            "eval corpus assertions drift",
        )
        require(
            isinstance(scenario["fixture_paths"], list)
            and all(isinstance(path, str) for path in scenario["fixture_paths"]),
            "eval corpus fixture paths drift",
        )
        for fixture in scenario["fixture_paths"]:
            fixture_relative = Path(fixture)
            require(
                not fixture_relative.is_absolute()
                and ".." not in fixture_relative.parts,
                f"eval fixture path is invalid: {fixture}",
            )
            read_repository_file(repo_root, Path("evals") / fixture_relative)
    scenarios_by_id = {scenario["id"]: scenario for scenario in scenarios}
    for scenario_id, expected in EXPECTED_DISCARD_EVAL_SCENARIOS.items():
        require(
            scenarios_by_id.get(scenario_id) == expected,
            f"discard eval scenario semantic contract drift: {scenario_id}",
        )
    discard_fixture = read_repository_file(
        repo_root, EVAL_RELATIVE / "fixtures/discard-reenumeration.md"
    )
    normalized_discard_fixture = " ".join(discard_fixture.split())
    for term in (
        "`notes.md`",
        "same path and status",
        "bytes",
        "content-addressed identity",
        "fresh exact `quarantine /workspace/project.quarantine/retry` confirmation",
        "exact-target `git worktree repair /workspace/project.quarantine/retry`",
        "reciprocal `gitdir` records",
    ):
        require(
            term in normalized_discard_fixture,
            f"discard re-enumeration eval contract missing: {term}",
        )
    for term in (
        "submodule `vendor/x`",
        "`vendor/x/notes.md`",
        "recorded gitlink",
        "worktree HEAD",
        "same nested path and status",
        "stable lexical path inventory",
        "recursive content-addressed dirty snapshot",
    ):
        require(
            term in normalized_discard_fixture,
            f"submodule discard eval contract missing: {term}",
        )
    require(
        ".cache/ignored-sentinel" in normalized_discard_fixture
        and "late.bin" in normalized_discard_fixture
        and "quarantine /workspace/project.quarantine/retry"
        in normalized_discard_fixture,
        "top-level discard eval contract missing",
    )
    for fixture, expected_digest in EXPECTED_DISCARD_FIXTURE_SEMANTIC_SHA256.items():
        fixture_content = read_repository_file(repo_root, Path("evals") / fixture)
        message = (
            "discard fixture semantic contract drift"
            if fixture.endswith("discard-reenumeration.md")
            else "discard budget fixture semantic contract drift"
        )
        require(
            normalized_semantic_sha256(fixture_content) == expected_digest,
            f"{message}: {fixture}",
        )


def semantic_release_paths(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and path.relative_to(root).as_posix() not in CONTENT_LOCK_EXCLUSIONS
        )
    )


@dataclass(frozen=True)
class InputEntry:
    """The content identity used to bind a lock refresh to validated inputs."""

    kind: str
    mode: int | None
    sha256: str | None


@dataclass(frozen=True)
class ContentLockWriteSnapshot:
    plugin_entries: tuple[tuple[str, InputEntry], ...]
    eval_entries: tuple[tuple[str, InputEntry], ...]
    lock_entries: tuple[tuple[str, InputEntry], ...]
    prior_lock_content: bytes | None


@dataclass(frozen=True)
class ContentLockRecoveryArtifact:
    """Private durable record of the lock state before a replacement attempt."""

    directory: Path


def input_metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def capture_input_entry(path: Path) -> tuple[InputEntry, bytes | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return InputEntry("missing", None, None), None
    if stat.S_ISREG(metadata.st_mode):
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            require(
                stat.S_ISREG(opened.st_mode),
                f"validated input is not a regular file: {path}",
            )
            with os.fdopen(descriptor, "rb", closefd=False) as file:
                content = file.read()
            read_complete = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        try:
            current = path.lstat()
        except FileNotFoundError as error:
            raise ContractError(
                f"validated input changed while snapshotting: {path}"
            ) from error
        require(
            input_metadata_identity(metadata)
            == input_metadata_identity(opened)
            == input_metadata_identity(read_complete)
            == input_metadata_identity(current),
            f"validated input changed while snapshotting: {path}",
        )
        return (
            InputEntry(
                "regular",
                stat.S_IMODE(opened.st_mode),
                hashlib.sha256(content).hexdigest(),
            ),
            content,
        )
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISDIR(metadata.st_mode):
        return InputEntry("directory", mode, None), None
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        current = path.lstat()
        require(
            input_metadata_identity(metadata) == input_metadata_identity(current),
            f"validated input changed while snapshotting: {path}",
        )
        return (
            InputEntry(
                "symlink",
                mode,
                hashlib.sha256(os.fsencode(target)).hexdigest(),
            ),
            None,
        )
    return InputEntry("special", mode, None), None


def snapshot_tree(root: Path) -> tuple[tuple[str, InputEntry], ...]:
    entries: dict[str, InputEntry] = {}

    def visit(path: Path, relative: str) -> None:
        entry, _ = capture_input_entry(path)
        entries[relative] = entry
        if entry.kind != "directory":
            return
        before = path.lstat()
        try:
            with os.scandir(path) as directory:
                children = sorted(item.name for item in directory)
        except FileNotFoundError as error:
            raise ContractError(
                f"validated input changed while snapshotting: {path}"
            ) from error
        current = path.lstat()
        require(
            input_metadata_identity(before) == input_metadata_identity(current),
            f"validated input changed while snapshotting: {path}",
        )
        for name in children:
            child_relative = name if relative == "." else f"{relative}/{name}"
            visit(path / name, child_relative)
        final = path.lstat()
        require(
            input_metadata_identity(before) == input_metadata_identity(final),
            f"validated input changed while snapshotting: {path}",
        )

    visit(root, ".")
    return tuple(sorted(entries.items()))


def snapshot_content_lock_inputs(
    repo_root: Path,
) -> tuple[tuple[tuple[str, InputEntry], ...], bytes | None]:
    relatives = (
        Path("plugins"),
        PLUGIN_RELATIVE,
        Path("evals"),
        EVAL_RELATIVE,
        Path("release"),
        CONTENT_LOCK_RELATIVE.parent,
        CONTENT_LOCK_RELATIVE,
    )
    entries: list[tuple[str, InputEntry]] = []
    prior_lock_content: bytes | None = None
    for relative in relatives:
        entry, content = capture_input_entry(repo_root / relative)
        entries.append((relative.as_posix(), entry))
        if relative == CONTENT_LOCK_RELATIVE:
            prior_lock_content = content
    return tuple(entries), prior_lock_content


def capture_content_lock_write_snapshot(repo_root: Path) -> ContentLockWriteSnapshot:
    lock_entries, prior_lock_content = snapshot_content_lock_inputs(repo_root)
    return ContentLockWriteSnapshot(
        plugin_entries=snapshot_tree(repo_root / PLUGIN_RELATIVE),
        eval_entries=snapshot_tree(repo_root / EVAL_RELATIVE),
        lock_entries=lock_entries,
        prior_lock_content=prior_lock_content,
    )


def require_content_lock_write_snapshot_unchanged(
    repo_root: Path,
    snapshot: ContentLockWriteSnapshot,
) -> None:
    current = capture_content_lock_write_snapshot(repo_root)
    require(
        current == snapshot,
        "validated inputs changed before semantic content lock replacement",
    )


def expected_snapshot_after_content_lock_replacement(
    snapshot: ContentLockWriteSnapshot,
    content: bytes,
    mode: int,
) -> ContentLockWriteSnapshot:
    lock_entries = dict(snapshot.lock_entries)
    lock_entries[CONTENT_LOCK_RELATIVE.as_posix()] = InputEntry(
        "regular",
        mode,
        hashlib.sha256(content).hexdigest(),
    )
    return ContentLockWriteSnapshot(
        plugin_entries=snapshot.plugin_entries,
        eval_entries=snapshot.eval_entries,
        lock_entries=tuple(lock_entries.items()),
        prior_lock_content=content,
    )


def require_content_lock_write_snapshot_after_replacement(
    repo_root: Path,
    snapshot: ContentLockWriteSnapshot,
    content: bytes,
    mode: int,
) -> None:
    require(
        capture_content_lock_write_snapshot(repo_root)
        == expected_snapshot_after_content_lock_replacement(snapshot, content, mode),
        "validated inputs changed during semantic content lock replacement",
    )


def content_lock_document_from_snapshot(snapshot: ContentLockWriteSnapshot) -> dict:
    files = {
        relative: {"sha256": entry.sha256, "mode": entry.mode}
        for relative, entry in snapshot.plugin_entries
        if relative != "."
        and entry.kind == "regular"
        and relative not in CONTENT_LOCK_EXCLUSIONS
    }
    require(
        all(
            entry["sha256"] is not None
            and entry["mode"] in SAFE_PLUGIN_REGULAR_FILE_MODES
            for entry in files.values()
        ),
        "semantic content lock snapshot regular-file digest or mode is invalid",
    )
    return {
        "schema_version": 2,
        "algorithm": "sha256",
        "files": files,
    }


def content_lock_document(root: Path) -> dict:
    entries = snapshot_tree(root)
    snapshot = ContentLockWriteSnapshot(entries, (), (), None)
    return content_lock_document_from_snapshot(snapshot)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_directory(path: Path) -> None:
    metadata = path.lstat()
    require(
        stat.S_ISDIR(metadata.st_mode) and not path.is_symlink(),
        "semantic content lock recovery storage is invalid",
    )
    require(
        metadata.st_uid == os.geteuid() and stat.S_IMODE(metadata.st_mode) == 0o700,
        "semantic content lock recovery storage is not private",
    )


def content_lock_recovery_root(repo_root: Path) -> Path:
    """Return the private, shared-Git-dir recovery root for this worktree."""
    repo_root = repo_root.resolve(strict=True)
    raw_common_dir = run_candidate_git(
        repo_root,
        ["rev-parse", "--git-common-dir"],
        error_factory=ContractError,
        operation="locate semantic content lock recovery storage",
    )
    try:
        common_dir_value = raw_common_dir.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise ContractError(
            "semantic content lock recovery storage is unavailable"
        ) from error
    require(
        bool(common_dir_value),
        "semantic content lock recovery storage is unavailable",
    )
    common_dir = Path(common_dir_value)
    if not common_dir.is_absolute():
        common_dir = repo_root / common_dir
    common_dir = common_dir.absolute()
    require(
        not common_dir.is_symlink(),
        "semantic content lock recovery storage is invalid",
    )
    common_dir = common_dir.resolve(strict=True)
    require(
        common_dir.is_dir() and not common_dir.is_symlink(),
        "semantic content lock recovery storage is invalid",
    )
    return (
        common_dir
        / "versionkeeping-content-lock-recovery"
        / hashlib.sha256(os.fsencode(str(repo_root))).hexdigest()
    )


def ensure_private_directory(path: Path) -> None:
    """Create or harden one owned recovery directory without following a link."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        path.mkdir(mode=0o700)
        _fsync_directory(path.parent)
    else:
        require(
            stat.S_ISDIR(metadata.st_mode)
            and not path.is_symlink()
            and metadata.st_uid == os.geteuid(),
            "semantic content lock recovery storage is invalid",
        )
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            os.chmod(path, 0o700)
    _private_directory(path)
    _fsync_directory(path)


def preserve_prior_content_lock_recovery(
    repo_root: Path,
    snapshot: ContentLockWriteSnapshot,
) -> ContentLockRecoveryArtifact:
    """Persist the exact pre-replacement lock before the canonical path mutates."""
    root = content_lock_recovery_root(repo_root)
    parent = root.parent
    ensure_private_directory(parent)
    ensure_private_directory(root)
    artifact = Path(tempfile.mkdtemp(prefix="lock-", dir=root))
    os.chmod(artifact, 0o700)
    _private_directory(artifact)
    existing = dict(snapshot.lock_entries)[CONTENT_LOCK_RELATIVE.as_posix()]
    try:
        require(
            existing.kind in {"missing", "regular"},
            "prior semantic content lock is not recoverable",
        )
        prior = {
            "kind": existing.kind,
            "mode": existing.mode,
            "sha256": existing.sha256,
        }
        if existing.kind == "regular":
            require(
                snapshot.prior_lock_content is not None,
                "prior semantic content lock is not recoverable",
            )
            prior_path = artifact / "prior-lock.bin"
            with prior_path.open("xb") as file:
                file.write(snapshot.prior_lock_content)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(prior_path, existing.mode or 0o600)
            with prior_path.open("rb") as file:
                os.fsync(file.fileno())
        manifest = artifact / "manifest.json"
        with manifest.open("x", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "canonical_path": CONTENT_LOCK_RELATIVE.as_posix(),
                        "prior_lock": prior,
                        "rerun": "quiescent rerun required",
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            file.flush()
            os.fsync(file.fileno())
        _fsync_directory(artifact)
        _fsync_directory(root)
    except BaseException:
        shutil.rmtree(artifact)
        _fsync_directory(root)
        raise
    return ContentLockRecoveryArtifact(artifact)


def discard_content_lock_recovery(recovery: ContentLockRecoveryArtifact) -> None:
    root = recovery.directory.parent
    _private_directory(root)
    _private_directory(recovery.directory)
    shutil.rmtree(recovery.directory)
    _fsync_directory(root)


def replace_content_lock_atomically(
    repo_root: Path,
    path: Path,
    content: bytes,
    mode: int,
    snapshot: ContentLockWriteSnapshot,
) -> None:
    recovery: ContentLockRecoveryArtifact | None = None
    replacement_may_have_occurred = False

    def recheck(_temporary_paths: frozenset[Path]) -> None:
        require_content_lock_write_snapshot_unchanged(repo_root, snapshot)

    def preserve_recovery() -> None:
        nonlocal recovery
        recovery = preserve_prior_content_lock_recovery(repo_root, snapshot)
        try:
            require_content_lock_write_snapshot_unchanged(repo_root, snapshot)
        except BaseException:
            discard_content_lock_recovery(recovery)
            recovery = None
            raise

    def mark_replacement_started() -> None:
        nonlocal replacement_may_have_occurred
        replacement_may_have_occurred = True

    def verify_replacement() -> None:
        require_content_lock_write_snapshot_after_replacement(
            repo_root,
            snapshot,
            content,
            mode,
        )

    try:
        replace_generated_artifacts(
            repo_root,
            {path: (content, mode)},
            recheck=recheck,
            before_replace=preserve_recovery,
            replacement_started=mark_replacement_started,
            verify=verify_replacement,
            rollback_on_failure=False,
        )
    except BaseException as error:
        if replacement_may_have_occurred and recovery is not None:
            raise ContractError(
                "semantic content lock replacement outcome is unknown; canonical "
                f"value preserved and prior lock recovery retained at {recovery.directory}; "
                "quiescent rerun required"
            ) from error
        if recovery is not None:
            discard_content_lock_recovery(recovery)
        raise
    if recovery is not None:
        discard_content_lock_recovery(recovery)


def write_content_lock(
    repo_root: Path,
    snapshot: ContentLockWriteSnapshot,
) -> None:
    path = repo_root / CONTENT_LOCK_RELATIVE
    existing = dict(snapshot.lock_entries)[CONTENT_LOCK_RELATIVE.as_posix()]
    require(
        existing.kind in {"missing", "regular"},
        "semantic content lock is not a regular file",
    )
    content = (
        json.dumps(content_lock_document_from_snapshot(snapshot), indent=2) + "\n"
    ).encode()
    replace_content_lock_atomically(
        repo_root,
        path,
        content,
        existing.mode if existing.mode is not None else 0o644,
        snapshot,
    )


def validate_content_lock(repo_root: Path, root: Path) -> None:
    lock = strict_json(
        read_repository_file(repo_root, CONTENT_LOCK_RELATIVE),
        "semantic content lock",
    )
    require(
        isinstance(lock, dict)
        and set(lock) == {"schema_version", "algorithm", "files"},
        "semantic content lock fields drift",
    )
    require(
        type(lock["schema_version"]) is int and lock["schema_version"] == 2,
        "semantic content lock schema version drift",
    )
    require(lock["algorithm"] == "sha256", "semantic content lock algorithm drift")
    files = lock["files"]
    require(
        isinstance(files, dict) and set(files) == set(semantic_release_paths(root)),
        "semantic content lock inventory drift",
    )
    require(
        all(
            isinstance(entry, dict)
            and set(entry) == {"sha256", "mode"}
            and isinstance(entry["sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is not None
            and type(entry["mode"]) is int
            and entry["mode"] in SAFE_PLUGIN_REGULAR_FILE_MODES
            for entry in files.values()
        ),
        "semantic content lock digest or mode is invalid",
    )
    require(lock == content_lock_document(root), "semantic content lock mismatch")


def validate(repo_root: Path, *, check_content_lock: bool = True) -> Path:
    root = plugin_root(repo_root)
    validate_tree_entries(root)
    validate_all_json_documents(root)
    skills = validate_topology(root)
    validate_manifests(root)
    validate_skills(root, skills)
    validate_portability_and_ownership(root)
    validate_external_eval_tree(repo_root)
    validate_checkpoint_external_evals(repo_root)
    validate_evals(repo_root)
    validate_root_inventory(root)
    if check_content_lock:
        validate_content_lock(repo_root, root)
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", nargs="?", type=Path)
    parser.add_argument("--write-content-lock", action="store_true")
    arguments = parser.parse_args()
    repo_root = arguments.repository or Path(__file__).resolve().parents[1]
    try:
        if arguments.write_content_lock:
            snapshot = capture_content_lock_write_snapshot(repo_root)
            validate(repo_root, check_content_lock=False)
            require_content_lock_write_snapshot_unchanged(repo_root, snapshot)
            write_content_lock(repo_root, snapshot)
        else:
            validate(repo_root)
    except (
        AgentPluginContractError,
        ContractError,
        OSError,
        json.JSONDecodeError,
        TypeError,
    ) as error:
        print(f"Versionkeeping contract validation failed: {error}", file=sys.stderr)
        return 1
    print("Versionkeeping contract validation passed")
    if arguments.write_content_lock:
        print("Versionkeeping semantic content lock updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
