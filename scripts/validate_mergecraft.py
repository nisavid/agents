#!/usr/bin/env python3
"""Repository release validator for the public Mergecraft plugin."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

from agent_plugins_standard import (  # noqa: E402
    AgentPluginContractError,
    discover_direct_skills,
    load_agent_plugin_manifest,
    validate_skill_resource_links,
)

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

YAML_ERROR = yaml.YAMLError if yaml is not None else ValueError


PLUGIN_RELATIVE = Path("plugins/mergecraft")
EVAL_RELATIVE = Path("evals/mergecraft")
CONTENT_LOCK_RELATIVE = Path("release/plugin-content-locks/mergecraft.json")
RETIREMENT_FIXTURES_RELATIVE = EVAL_RELATIVE / "retirement-fixtures.json"
RETIREMENT_CORPUS_RELATIVE = EVAL_RELATIVE / "retirement-comparative-corpus.json"
RETIREMENT_DEFINITION_RELATIVE = EVAL_RELATIVE / "retirement-control-plane.json"
CONTROL_PLANE_EVALUATOR_RELATIVE = "scripts/run_control_plane_eval.py"
ATLAS_RELEASE_RELATIVE = Path("release/mergecraft")
RETIREMENT_LEDGER_RELATIVE = Path(
    "release/mergecraft-retirement-contribution-ledger.json"
)
ATLAS_RELEASE_FILES = {
    "review-atlas-contract.json",
    "review-atlas-contribution-ledger.json",
}
CONTENT_LOCK_EXCLUSIONS = {"CHANGELOG.md", "LICENSE"}
PUBLIC_SKILLS = (
    "writing-reviewable-pr-descriptions",
    "publishing-reviewable-prs",
    "graphite",
    "addressing-pr-review-feedback",
    "interacting-with-pr-review-feedback",
    "resuming-reviewed-prs",
    "getting-prs-ready-for-review",
    "getting-prs-merged",
    "stacking-pr-fixups",
)
ROOT_FILES = {
    ".claude-plugin/plugin.json",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "plugin.json",
    "topology.json",
}
WRITER_MODULE_NAMES = (
    "__init__",
    "badge_colors",
    "badge_links",
    "badge_presentation",
    "badge_wrappers",
    "badges",
    "categories",
    "diff",
    "diff_files",
    "diff_metrics",
    "git_observer",
    "html",
    "metrics",
    "model",
    "parsing",
    "review_input",
    "sensitive_content",
    "stack",
    "stack_inventory",
    "types",
    "urls",
)
ADDRESS_FIXTURES = (
    "blocked_merge_state.json",
    "clean_pr.json",
    "failed_check.json",
    "fork_pr.json",
    "mixed_top_level_page_1.json",
    "mixed_top_level_page_2.json",
    "outdated_thread.json",
    "paginated_comments_page_1.json",
    "paginated_comments_page_2.json",
    "paginated_page_1.json",
    "paginated_page_2.json",
    "paginated_requests_checks_page_1.json",
    "paginated_requests_checks_page_2.json",
    "requested_reviewer.json",
)
MERGE_EVAL_FIXTURES = (
    "coderabbit-skipped-review.md",
    "draft-pr-resume.md",
    "explicit-review-loop-before-merge.md",
    "external-review-budget.md",
    "gh-fix-ci-adapter-boundary.md",
    "latest-publication-receipt-drift.md",
    "local-policy-limits-autonomy.md",
    "new-branch-publish-and-closeout.md",
    "pr-description-only-near-miss.md",
    "pure-merge-current-state.md",
    "review-comments-to-merge.md",
    "review-only-near-miss.md",
)
RAW_SKILL_EVAL_FIXTURES = {
    "graphite": ("latest-publication-receipt-drift.md",),
    "interacting-with-pr-review-feedback": (
        "authorized-reply-and-resolution-boundary.md",
    ),
    "resuming-reviewed-prs": (
        "latest-publication-receipt-drift.md",
        "feedback-terminal-handoff.md",
        "readiness-terminal-handoff.md",
        "active-conflict-terminal-handoff.md",
        "required-actions-terminal-handoff.md",
        "status-only-read-only.md",
    ),
    "getting-prs-ready-for-review": (
        "ready-after-verified-checkpoint.md",
        "latest-publication-receipt-drift.md",
    ),
    "stacking-pr-fixups": (
        "narrow-stacked-fixup.md",
        "latest-publication-receipt-drift.md",
    ),
}
TERMINAL_INTERNAL_OPERATIONS = {"focused-ci"}
TERMINAL_OPERATION_HANDOFFS = {"focused-ci", "remote-ref-deletion"}
GITHUB_ALIAS_ACCESS = {
    "pr-create": "write",
    "repository-orientation": "read",
    "pr-orientation": "read",
    "issue-orientation": "read",
    "repository-summary": "read",
    "pr-summary": "read",
    "issue-summary": "read",
    "patch-inspection": "read",
    "top-level-comment-read": "read",
    "top-level-comment-write": "write",
    "review-comment-read": "read",
    "review-reply-write": "write",
    "labels-read": "read",
    "labels-write": "write",
    "reactions-read": "read",
    "reactions-write": "write",
    "review-submit-comment": "write",
    "review-submit-approve": "write",
    "review-submit-request-changes": "write",
    "review-thread-resolution": "write",
    "check-inspection": "read",
    "check-rerun": "write",
    "bot-review-request": "write",
    "pr-text-read": "read",
    "pr-text-write": "write",
    "pr-readiness-read": "read",
    "pr-readiness-write": "write",
    "merge-inspection": "read",
    "merge-write": "write",
}
COMMON_SKILL_FILES = {"SKILL.md", "agents/openai.yaml"}
EXPECTED_SKILL_FILES = {
    "writing-reviewable-pr-descriptions": COMMON_SKILL_FILES
    | {
        "references/body-contract.md",
        "references/change-navigation.md",
        "references/review-atlas-extension.json",
        "review-atlas-reference-design.md",
        "scripts/validate_change_navigation.py",
    }
    | {f"scripts/change_navigation/{name}.py" for name in WRITER_MODULE_NAMES},
    "publishing-reviewable-prs": COMMON_SKILL_FILES
    | {
        "scripts/audit_reviewable_pr.py",
        "scripts/create_reviewable_pr.py",
        "scripts/publication_receipts.py",
        "scripts/required_review.py",
        "scripts/reviewable_pr_state.py",
        "scripts/update_reviewable_pr.py",
    },
    "graphite": COMMON_SKILL_FILES | {"scripts/submit_draft_stack.py"},
    "addressing-pr-review-feedback": COMMON_SKILL_FILES
    | {
        "references/feedback-flow.md",
        "scripts/review_feedback_state.py",
    },
    "interacting-with-pr-review-feedback": COMMON_SKILL_FILES
    | {
        "references/interaction-authority.md",
    },
    "resuming-reviewed-prs": COMMON_SKILL_FILES,
    "getting-prs-ready-for-review": COMMON_SKILL_FILES,
    "getting-prs-merged": COMMON_SKILL_FILES
    | {
        "references/gh-fix-ci-adapter.md",
        "references/merge-actuator.md",
        "scripts/post_coderabbit_comment.py",
    },
    "stacking-pr-fixups": COMMON_SKILL_FILES,
}
CODEX_PROMPTS = {
    "writing-reviewable-pr-descriptions": (
        "Use $mergecraft:writing-reviewable-pr-descriptions to prepare the "
        "exact PR title and body."
    ),
    "publishing-reviewable-prs": (
        "Use $mergecraft:publishing-reviewable-prs to publish exact PR state."
    ),
    "graphite": "Use $mergecraft:graphite to manage this Graphite stack.",
    "addressing-pr-review-feedback": (
        "Use $mergecraft:addressing-pr-review-feedback to inspect or address "
        "the current pull request feedback."
    ),
    "interacting-with-pr-review-feedback": (
        "Use $mergecraft:interacting-with-pr-review-feedback for one authorized "
        "feedback interaction."
    ),
    "resuming-reviewed-prs": (
        "Use $mergecraft:resuming-reviewed-prs to resume this reviewed PR safely."
    ),
    "getting-prs-ready-for-review": (
        "Use $mergecraft:getting-prs-ready-for-review to make this PR ready for review."
    ),
    "getting-prs-merged": (
        "Use $mergecraft:getting-prs-merged to drive this PR through merge closeout."
    ),
    "stacking-pr-fixups": (
        "Use $mergecraft:stacking-pr-fixups to prepare a stacked PR fixup."
    ),
}
MANIFEST_PROMPTS = [
    (
        "Use $mergecraft:writing-reviewable-pr-descriptions to prepare the PR "
        "title and body."
    ),
    "Use $mergecraft:publishing-reviewable-prs to publish verified PR state.",
    "Use $mergecraft:getting-prs-merged to complete the merge lifecycle.",
]
ORIGINAL_ATLAS_HEADINGS = {
    "Contents",
    "Summary",
    "Context",
    "Goals",
    "Non-goals",
    "Design principles",
    "Architecture first, chronology second",
    "Guided exploration with bounded freedom",
    "Progressive disclosure",
    "Claims carry provenance",
    "Source systems remain authoritative",
    "Architecture",
    "Semantic manifest",
    "Temporal change model",
    "Published payload boundary",
    "Review contracts",
    "Claim provenance",
    "Ambiguity handling",
    "Adaptive lens decomposition",
    "Reviewer interaction model",
    "Workspace",
    "Layered focus",
    "Persona presets",
    "Temporal model",
    "Review handoff",
    "Current stack lens map",
    "PR-body delivery",
    "Publication safety",
    "Validation and error handling",
    "Gate 1: schema and referential integrity",
    "Gate 2: review-contract completeness",
    "Gate 3: visual and interaction budgets",
    "Gate 4: rendered routing",
    "Testing strategy",
    "Reference implementation boundaries",
    "Delivery sequence",
    "Success criteria",
    "Deferred product direction",
}
EXPECTED_ATLAS_CONTRIBUTIONS = [
    {
        "id": "document-navigation",
        "source_headings": ["Contents"],
        "disposition": "public-core",
        "destination_anchor": "purpose",
    },
    {
        "id": "purpose-context-goals",
        "source_headings": ["Summary", "Context", "Goals", "Non-goals"],
        "disposition": "public-core",
        "destination_anchor": "purpose",
    },
    {
        "id": "design-principles",
        "source_headings": [
            "Design principles",
            "Architecture first, chronology second",
            "Guided exploration with bounded freedom",
            "Progressive disclosure",
            "Claims carry provenance",
            "Source systems remain authoritative",
        ],
        "disposition": "public-core",
        "destination_anchor": "design-principles",
    },
    {
        "id": "semantic-architecture",
        "source_headings": [
            "Architecture",
            "Semantic manifest",
            "Temporal change model",
            "Published payload boundary",
            "Review contracts",
            "Claim provenance",
            "Ambiguity handling",
        ],
        "disposition": "public-core",
        "destination_anchor": "architecture",
    },
    {
        "id": "adaptive-lenses",
        "source_headings": ["Adaptive lens decomposition"],
        "disposition": "public-core",
        "destination_anchor": "adaptive-lens-decomposition",
    },
    {
        "id": "reviewer-interaction",
        "source_headings": [
            "Reviewer interaction model",
            "Workspace",
            "Layered focus",
            "Persona presets",
            "Temporal model",
            "Review handoff",
        ],
        "disposition": "public-core",
        "destination_anchor": "reviewer-interaction-model",
    },
    {
        "id": "worked-application-stack-map",
        "source_headings": ["Current stack lens map"],
        "disposition": "private-overlay",
        "private_overlay_category": "worked-application",
        "destination_anchor": None,
    },
    {
        "id": "delivery-core",
        "source_headings": ["PR-body delivery", "Publication safety"],
        "disposition": "public-core",
        "destination_anchor": "pr-body-delivery",
    },
    {
        "id": "concrete-private-delivery",
        "source_headings": ["PR-body delivery", "Publication safety"],
        "disposition": "private-overlay",
        "private_overlay_category": "concrete-routes-attachments-and-access",
        "destination_anchor": None,
    },
    {
        "id": "validation-gates",
        "source_headings": [
            "Validation and error handling",
            "Gate 1: schema and referential integrity",
            "Gate 2: review-contract completeness",
            "Gate 3: visual and interaction budgets",
            "Gate 4: rendered routing",
        ],
        "disposition": "public-core",
        "destination_anchor": "validation-and-error-handling",
    },
    {
        "id": "testing",
        "source_headings": ["Testing strategy"],
        "disposition": "public-core",
        "destination_anchor": "testing-strategy",
    },
    {
        "id": "implementation-delivery-success",
        "source_headings": [
            "Reference implementation boundaries",
            "Delivery sequence",
            "Success criteria",
            "Deferred product direction",
        ],
        "disposition": "public-core",
        "destination_anchor": "reference-implementation-boundaries",
    },
    {
        "id": "worked-instance-counts-and-routes",
        "source_headings": [
            "Delivery sequence",
            "Success criteria",
            "Deferred product direction",
        ],
        "disposition": "private-overlay",
        "private_overlay_category": "worked-instance-data",
        "destination_anchor": None,
    },
]
BEHAVIOR_SCENARIO_IDS = {
    "writer-owns-content",
    "writer-tiny-proportional",
    "writer-ordinary-proportional",
    "writer-stacked-navigation",
    "writer-preservation-sensitive",
    "writer-visual-needed",
    "writer-visual-not-needed",
    "writer-exceptional-atlas",
    "publisher-owns-actuation",
    "graphite-transport-boundary",
    "feedback-natural-reply",
    "resume-selects-one-owner",
    "resume-conflict-precedence",
    "resume-required-actions",
    "resume-status-only",
    "merge-explicit-review-loop",
    "merge-current-state-no-loop",
    "template-number-collision",
    "fork-head-identity",
    "ci-owner-boundary",
    "feedback-snapshot-orientation",
    "pr-copy-independent-review",
    "pr-body-secret-blocker",
    "reverse-edge-rejection",
    "issue-edit-negative-route",
    "non-pr-publication-negative-route",
    "pr-ship-positive-route",
    "comment-timeout",
    "atlas-no-overlay",
    "atlas-matching-overlay",
    "atlas-conflicting-overlay",
}
ATLAS_EXTENSION_CONTRACT = {
    "schema_version": 1,
    "default_overlay_path": "~/.config/mergecraft/review-atlas-overlay.md",
    "file_requirement": "regular-no-symlink",
    "load_condition": "present-and-scope-matches",
    "absence": "continue-public-core",
    "allowed_authority": ["instance-data", "stricter-local-policy"],
    "forbidden_authority": [
        "public-contract-redefinition",
        "weaker-policy",
        "scope-expansion",
    ],
    "precedence": "public-core",
}
EXPECTED_ATLAS_PROSE_SHA256 = {
    "design": "23b642b37ced3407c84ad2b1ca6da430d95dd68a674f7daceede3a1b297af441",
    "writer": "d52b65fc8e729fdd33c7e3f33173a45712068ff413b386c328508fbe18fd47bf",
    "body": "3c59ae1b4ec197b0cd4c41624a39c597744bea4fd5333b1ed81e7f2c8d6753c4",
    "navigation": "7d7cb0ea53a24b962f3298d73c38294f065c6e49543d00d2e8370cb490f68e3b",
}
RELEASE_VERSION = "1.0.0"
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PORTABILITY_MARKERS = (
    "/Users/",
    ".local/share/chezmoi",
    "chezmoi.wt",
    "systalyze/systalyze",
    "ivan/",
    "$HOME/.agents",
)
RETIRED_ROUTES = (
    "resolving-workflow-ownership",
    "orchestrating-pr-creation",
    "pr-review-orchestration",
)
RETIREMENT_FIXTURE_IDS = (
    "cold-repository-pr-orientation-summary",
    "unresolved-thread-acquisition-and-response-only",
    "mixed-worktree-checkpoint-push-and-draft-publication",
    "direct-connector-and-fill-collision-refusal",
    "gh-fix-ci-actions-only-updateability",
)
RETIREMENT_FIXTURE_ASSERTIONS = {
    "cold-repository-pr-orientation-summary": (
        (
            "ordinary GitHub read tools",
            "repository orientation",
            "pull request summary",
        ),
        ("pull request mutation", "implicit publication"),
    ),
    "unresolved-thread-acquisition-and-response-only": (
        (
            "addressing-pr-review-feedback",
            "interacting-with-pr-review-feedback",
            "unchanged resolution state",
        ),
        ("unverified resolution", "nested review loop"),
    ),
    "mixed-worktree-checkpoint-push-and-draft-publication": (
        (
            "checkpointing-and-publishing-git-work",
            "getting-prs-ready-for-review",
            "publishing-reviewable-prs",
        ),
        ("mixed ownership commit", "unverified draft"),
    ),
    "direct-connector-and-fill-collision-refusal": (
        ("publishing-reviewable-prs", "bound writer", "refuse collision"),
        ("direct connector creation", "gh pr create --fill"),
    ),
    "gh-fix-ci-actions-only-updateability": (
        ("github:gh-fix-ci", "GitHub Actions", "independently updateable"),
        ("general pull request publication", "retired with github"),
    ),
}
RETIREMENT_CASE_BINDINGS = {
    "cold-repository-pr-orientation-summary": (
        "mergecraft:resuming-reviewed-prs",
        "no_skill",
        (
            "github:repository-orientation",
            "github:pr-orientation",
            "github:repository-summary",
            "github:pr-summary",
        ),
    ),
    "unresolved-thread-acquisition-and-response-only": (
        "mergecraft:addressing-pr-review-feedback",
        "composed",
        ("feedback-acquisition", "feedback-interaction"),
    ),
    "mixed-worktree-checkpoint-push-and-draft-publication": (
        "mergecraft:getting-prs-ready-for-review",
        "composed",
        ("git-ref-push", "pr-creation", "pr-text-write", "pr-readiness-write"),
    ),
    "direct-connector-and-fill-collision-refusal": (
        "mergecraft:publishing-reviewable-prs",
        "candidate",
        ("pr-creation", "pr-content"),
    ),
    "gh-fix-ci-actions-only-updateability": (
        "mergecraft:getting-prs-merged",
        "composed",
        ("check-inspection", "focused-ci"),
    ),
}
RETIREMENT_CASE_STRATEGIES = {
    "cold-repository-pr-orientation-summary": "ordinary-tool",
    "unresolved-thread-acquisition-and-response-only": "absorb",
    "mixed-worktree-checkpoint-push-and-draft-publication": "absorb",
    "direct-connector-and-fill-collision-refusal": "discard-with-reason",
    "gh-fix-ci-actions-only-updateability": "retain",
}
RETIREMENT_CONTRIBUTIONS = (
    {
        "id": "github-cold-repository-orientation",
        "source_skill": "github",
        "contribution": "cold repository orientation",
        "disposition": "ordinary-tool",
        "destination_owner": "ordinary-github-read-tools",
        "fixture": "cold-repository-pr-orientation-summary",
    },
    {
        "id": "github-pr-summary",
        "source_skill": "github",
        "contribution": "pull request summary",
        "disposition": "ordinary-tool",
        "destination_owner": "ordinary-github-read-tools",
        "fixture": "cold-repository-pr-orientation-summary",
    },
    {
        "id": "github-direct-connector-pr-create",
        "source_skill": "github",
        "contribution": "direct connector pull request creation",
        "disposition": "discard-with-reason",
        "destination_owner": "publishing-reviewable-prs",
        "fixture": "direct-connector-and-fill-collision-refusal",
        "reason": (
            "The bound publisher owns pull request mutation and refuses unbound "
            "direct creation."
        ),
    },
    {
        "id": "github-gh-fix-ci-authority",
        "source_skill": "github",
        "contribution": "GitHub Actions failure diagnosis and repair",
        "disposition": "retain",
        "destination_owner": "github:gh-fix-ci",
        "fixture": "gh-fix-ci-actions-only-updateability",
    },
    {
        "id": "yeet-mixed-worktree-checkpoint-and-push",
        "source_skill": "yeet",
        "contribution": "mixed-worktree checkpoint and push",
        "disposition": "absorb",
        "destination_owner": "versionkeeping:checkpointing-and-publishing-git-work",
        "fixture": "mixed-worktree-checkpoint-push-and-draft-publication",
    },
    {
        "id": "yeet-draft-publication",
        "source_skill": "yeet",
        "contribution": "draft pull request publication",
        "disposition": "absorb",
        "destination_owner": "getting-prs-ready-for-review",
        "fixture": "mixed-worktree-checkpoint-push-and-draft-publication",
    },
    {
        "id": "yeet-fill-create",
        "source_skill": "yeet",
        "contribution": "filled raw gh pull request creation",
        "disposition": "discard-with-reason",
        "destination_owner": "publishing-reviewable-prs",
        "fixture": "direct-connector-and-fill-collision-refusal",
        "reason": (
            "The bound publisher owns generated review text and refuses raw filled "
            "creation routes."
        ),
    },
    {
        "id": "gh-address-comments-unresolved-thread-acquisition",
        "source_skill": "gh-address-comments",
        "contribution": "unresolved review thread acquisition",
        "disposition": "absorb",
        "destination_owner": "addressing-pr-review-feedback",
        "fixture": "unresolved-thread-acquisition-and-response-only",
    },
    {
        "id": "gh-address-comments-response-only-outcome",
        "source_skill": "gh-address-comments",
        "contribution": "authorized response-only review outcome",
        "disposition": "absorb",
        "destination_owner": "interacting-with-pr-review-feedback",
        "fixture": "unresolved-thread-acquisition-and-response-only",
    },
)
RETIREMENT_DESTINATION_OWNERS = {
    "ordinary-github-read-tools",
    "publishing-reviewable-prs",
    "github:gh-fix-ci",
    "versionkeeping:checkpointing-and-publishing-git-work",
    "getting-prs-ready-for-review",
    "addressing-pr-review-feedback",
    "interacting-with-pr-review-feedback",
}
COMPATIBILITY_SHIMS = {
    "acpx_trigger_eval.py",
    "claude_trigger_eval.py",
    "codex_trigger_eval.py",
    "cursor_trigger_eval.py",
    "prepare_behavior_evals.py",
    "trigger_eval_core.py",
}


class ContractError(ValueError):
    """A stable, user-actionable Mergecraft contract failure."""


def fail(message: str) -> None:
    raise ContractError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def strict_json(content: str, label: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                fail(f"duplicate JSON key in {label}: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        fail(f"non-finite JSON value in {label}: {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        require(math.isfinite(parsed), f"non-finite JSON value in {label}: {value}")
        return parsed

    return json.loads(
        content,
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )


def reject_non_finite_yaml(value: Any, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        fail(f"non-finite YAML value in {label}")
    if isinstance(value, dict):
        for key, item in value.items():
            reject_non_finite_yaml(key, label)
            reject_non_finite_yaml(item, label)
    elif isinstance(value, list):
        for item in value:
            reject_non_finite_yaml(item, label)


def strict_yaml(content: str, label: str) -> Any:
    if yaml is None:
        fail("PyYAML is required for strict adapter validation")

    class UniqueLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                fail(f"duplicate YAML key in {label}: {key}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    documents = list(yaml.load_all(content, Loader=UniqueLoader))
    require(len(documents) == 1, f"{label} must contain one YAML document")
    reject_non_finite_yaml(documents[0], label)
    return documents[0]


def locate_plugin(repo_root: Path) -> Path:
    require(not repo_root.is_symlink(), "repository root must not be a symlink")
    resolved_repo = repo_root.resolve(strict=True)
    require(resolved_repo.is_dir(), "repository root must be a directory")
    current = repo_root
    for part in PLUGIN_RELATIVE.parts:
        current /= part
        require(not current.is_symlink(), "plugin lexical path contains a symlink")
    root = current.resolve(strict=True)
    require(
        root.is_relative_to(resolved_repo) and root.is_dir(),
        "plugin root is invalid",
    )
    return root


def validate_tree_entries(root: Path) -> None:
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode), f"plugin tree contains a symlink: {relative}")
        require(
            stat.S_ISDIR(mode) or stat.S_ISREG(mode),
            f"plugin tree contains a special entry: {relative}",
        )
        require(
            "__pycache__" not in relative.parts and path.suffix != ".pyc",
            f"plugin tree contains generated Python state: {relative}",
        )


def read(root: Path, relative: str) -> str:
    path = root / relative
    require(
        path.is_file() and not path.is_symlink(),
        f"required regular file is missing: {relative}",
    )
    return path.read_text(encoding="utf-8")


def load_json(root: Path, relative: str) -> Any:
    return strict_json(read(root, relative), relative)


def read_repository_file(repo_root: Path, relative: Path) -> str:
    require(
        not relative.is_absolute() and ".." not in relative.parts,
        f"repository-relative path is invalid: {relative}",
    )
    path = repo_root / relative
    require(
        path.is_file() and not path.is_symlink(),
        f"required regular file is missing: {relative.as_posix()}",
    )
    return path.read_text(encoding="utf-8")


def load_repository_json(repo_root: Path, relative: Path) -> Any:
    return strict_json(read_repository_file(repo_root, relative), relative.as_posix())


def validate_external_eval_tree(repo_root: Path) -> None:
    root = repo_root / EVAL_RELATIVE
    require(root.is_dir() and not root.is_symlink(), "external eval root is invalid")
    for path in root.rglob("*"):
        relative = path.relative_to(repo_root)
        mode = path.lstat().st_mode
        require(
            not stat.S_ISLNK(mode), f"external eval tree contains a symlink: {relative}"
        )
        require(
            stat.S_ISREG(mode) or stat.S_ISDIR(mode),
            f"external eval tree contains a special entry: {relative}",
        )
        if stat.S_ISREG(mode) and path.suffix == ".json":
            strict_json(path.read_text(encoding="utf-8"), relative.as_posix())
        if stat.S_ISREG(mode):
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for marker in PORTABILITY_MARKERS:
                require(marker not in content, f"portability leak: {relative}")
            for route in RETIRED_ROUTES:
                require(route not in content, f"retired route in {relative}: {route}")


def validate_serialized_files(root: Path, *, source_stage: bool = False) -> None:
    for path in root.rglob("*.json"):
        strict_json(
            path.read_text(encoding="utf-8"),
            path.relative_to(root).as_posix(),
        )
    if not source_stage:
        for skill in PUBLIC_SKILLS:
            relative = f"skills/{skill}/agents/openai.yaml"
            strict_yaml(read(root, relative), relative)


def validate_topology(root: Path) -> None:
    topology = load_json(root, "topology.json")
    require(
        isinstance(topology, dict)
        and set(topology) == {"schema_version", "plugin", "skills", "operations"},
        "topology fields drift",
    )
    require(
        type(topology["schema_version"]) is int
        and topology["schema_version"] == 3
        and topology["plugin"] == "mergecraft",
        "topology identity drift",
    )
    skills = topology["skills"]
    require(isinstance(skills, list), "public skill inventory drift")
    names = tuple(
        component.get("name") if isinstance(component, dict) else None
        for component in skills
    )
    require(names == PUBLIC_SKILLS, "public skill inventory drift")
    actual_dirs = {
        path.name
        for path in (root / "skills").iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    require(actual_dirs == set(PUBLIC_SKILLS), "public skill inventory drift")
    operation_records = topology["operations"]
    require(isinstance(operation_records, list), "operation registry drift")
    operation_fields = {
        "semantic_id",
        "github_aliases",
        "surface",
        "owner",
        "implementation",
        "import",
        "access",
        "authority",
        "callers",
        "disposition",
    }
    operation_by_id: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    for record in operation_records:
        require(
            isinstance(record, dict)
            and set(record) == operation_fields
            and isinstance(record["semantic_id"], str)
            and record["semantic_id"]
            and isinstance(record["github_aliases"], list)
            and all(
                isinstance(alias, str) and alias for alias in record["github_aliases"]
            )
            and len(record["github_aliases"]) == len(set(record["github_aliases"]))
            and record["surface"] in {"workflow", "github", "git"}
            and isinstance(record["owner"], str)
            and record["owner"]
            and record["access"] in {"read", "write", "coordinate"}
            and isinstance(record["authority"], str)
            and record["authority"]
            and isinstance(record["callers"], list)
            and record["callers"] == sorted(set(record["callers"]))
            and all(caller in names for caller in record["callers"])
            and record["disposition"]
            in {
                "public-skill",
                "internal-helper",
                "imported-operation",
                "ordinary-tool",
                "owned-helper",
                "owned-adapter",
            },
            "operation registry drift",
        )
        semantic_id = record["semantic_id"]
        require(semantic_id not in operation_by_id, "duplicate operation export")
        operation_by_id[semantic_id] = record
        for alias in record["github_aliases"]:
            require(alias not in aliases, "GitHub operation alias collision")
            require(record["surface"] == "github", "GitHub operation surface drift")
            require(
                GITHUB_ALIAS_ACCESS.get(alias) == record["access"],
                "GitHub operation access drift",
            )
            aliases[alias] = semantic_id
        if record["surface"] == "github":
            require(record["github_aliases"], "GitHub operation alias drift")
        owner = record["owner"]
        imported = record["import"]
        implementation = record["implementation"]
        if imported is not None:
            require(
                record["disposition"] == "imported-operation"
                and imported == owner
                and implementation is None
                and ":" in owner
                and not owner.startswith("internal:"),
                "unresolved operation import",
            )
            plugin, skill = owner.split(":", 1)
            external_root = root.parents[1] / "plugins" / plugin
            require(external_root.is_dir(), "unresolved operation import")
            external = load_json(external_root, "topology.json")
            if isinstance(external.get("skills"), list):
                external_skills = {
                    item.get("name"): item
                    for item in external["skills"]
                    if isinstance(item, dict)
                }
                require(skill in external_skills, "unresolved operation import")
                declared = external_skills[skill].get("operations", [])
                require(semantic_id in declared, "unresolved operation import")
            else:
                require(
                    isinstance(external.get("skills"), dict)
                    and skill in external["skills"],
                    "unresolved operation import",
                )
        else:
            require(
                isinstance(implementation, str) and implementation,
                "operation implementation drift",
            )
            if implementation.startswith("skills/"):
                read(root, implementation)
            require(
                owner in names or owner.startswith("internal:"),
                "operation owner drift",
            )
    require(
        set(aliases) == set(GITHUB_ALIAS_ACCESS),
        "GitHub operation alias coverage drift",
    )
    exported_operations: set[str] = set()
    for component in skills:
        skill = component["name"]
        require(
            isinstance(component, dict)
            and set(component)
            == {
                "name",
                "entrypoint",
                "interface",
                "references",
                "scripts",
                "modules",
                "calls",
                "conditional_calls",
                "operations",
                "contract",
            },
            f"topology component fields drift: {skill}",
        )
        require(
            component["entrypoint"] == f"skills/{skill}/SKILL.md"
            and component["interface"] == f"skills/{skill}/agents/openai.yaml",
            f"topology runtime declaration drift: {skill}",
        )
        for field in ("entrypoint", "interface"):
            read(root, component[field])
        for field in ("references", "scripts", "modules"):
            require(
                isinstance(component[field], list),
                f"topology runtime declaration drift: {skill}",
            )
            for relative in component[field]:
                require(
                    isinstance(relative, str)
                    and not Path(relative).is_absolute()
                    and ".." not in Path(relative).parts
                    and relative.startswith(f"skills/{skill}/"),
                    f"topology runtime declaration drift: {skill}",
                )
                read(root, relative)
        if skill == "getting-prs-merged":
            require(
                component["contract"]["loop_owner"] is None
                and not any(call.endswith(":loop") for call in component["calls"]),
                "nested loop ownership",
            )
        for call in component["calls"]:
            require(
                isinstance(call, str)
                and call
                and (
                    call in names
                    or (
                        call.startswith("operation:")
                        and call.removeprefix("operation:") in operation_by_id
                    )
                ),
                f"unresolved topology call: {skill}",
            )
            if call.startswith("operation:"):
                operation = call.removeprefix("operation:")
                require(
                    operation_by_id[operation]["owner"] != skill,
                    f"self-actuator ownership cycle: {skill} -> {operation}",
                )
        require(
            isinstance(component["calls"], list)
            and len(component["calls"]) == len(set(component["calls"])),
            f"call graph drift: {skill}",
        )
        require(
            component["conditional_calls"] == {},
            f"conditional call authority drift: {skill}",
        )
        operations = component["operations"]
        require(
            isinstance(operations, list)
            and all(
                isinstance(operation, str) and operation for operation in operations
            )
            and len(operations) == len(set(operations)),
            f"skill operation drift: {skill}",
        )
        for operation in operations:
            require(
                operation in operation_by_id
                and operation_by_id[operation]["owner"] == skill,
                f"operation owner drift: {operation}",
            )
            require(operation not in exported_operations, "duplicate operation export")
            exported_operations.add(operation)
        contract = component["contract"]
        required_contract_fields = {
            "trigger",
            "modes",
            "authority",
            "inputs",
            "outputs",
            "forbidden_reverse_calls",
            "loop_owner",
            "terminal_statuses",
        }
        require(
            isinstance(contract, dict)
            and required_contract_fields <= set(contract)
            and set(contract) <= required_contract_fields | {"terminal_handoffs"}
            and isinstance(contract["trigger"], str)
            and contract["trigger"]
            and isinstance(contract["authority"], str)
            and contract["authority"]
            and all(
                isinstance(contract[field], list)
                and contract[field]
                and all(isinstance(item, str) and item for item in contract[field])
                for field in ("modes", "inputs", "outputs", "terminal_statuses")
            )
            and isinstance(contract["forbidden_reverse_calls"], list)
            and all(
                isinstance(item, str) and item
                for item in contract["forbidden_reverse_calls"]
            )
            and (
                contract["loop_owner"] is None
                or isinstance(contract["loop_owner"], str)
            ),
            f"caller/callee contract drift: {skill}",
        )
        require(
            not set(component["calls"]) & set(contract["forbidden_reverse_calls"]),
            f"forbidden reverse call: {skill}",
        )
        require(
            all(call.startswith("operation:") for call in component["calls"]),
            f"callee-wide operation fanout: {skill}",
        )
        handoffs = contract.get("terminal_handoffs", [])
        require(isinstance(handoffs, list), f"terminal handoff drift: {skill}")
        for handoff in handoffs:
            handoff_owner = handoff.get("owner") if isinstance(handoff, dict) else None
            terminal_operation = False
            if isinstance(handoff_owner, str) and handoff_owner.startswith(
                "operation:"
            ):
                semantic_id = handoff_owner.removeprefix("operation:")
                operation = operation_by_id.get(semantic_id)
                terminal_operation = (
                    semantic_id in TERMINAL_OPERATION_HANDOFFS
                    and operation is not None
                    and (
                        (
                            semantic_id in TERMINAL_INTERNAL_OPERATIONS
                            and operation["disposition"] == "internal-helper"
                            and operation["owner"].startswith("internal:")
                            and operation["surface"] == "github"
                        )
                        or operation["disposition"] == "imported-operation"
                    )
                )
            require(
                isinstance(handoff, dict)
                and set(handoff) == {"trigger", "owner", "resume"}
                and all(isinstance(value, str) and value for value in handoff.values())
                and (
                    handoff["owner"] in names
                    or any(
                        record["import"] == handoff["owner"]
                        for record in operation_records
                    )
                    or terminal_operation
                )
                and handoff["owner"] not in component["calls"],
                f"terminal handoff drift: {skill}",
            )
        if contract["loop_owner"] is not None:
            require(
                not any(
                    call.endswith(":loop") and call != contract["loop_owner"]
                    for call in component["calls"]
                ),
                "nested loop ownership",
            )
    skills_by_name = {component["name"]: component for component in skills}
    outcome_coordinators = {
        component["name"]
        for component in skills
        if any(operation.endswith("-outcome") for operation in component["operations"])
    } | {"resuming-reviewed-prs"}
    for coordinator in outcome_coordinators:
        called_coordinators = {
            operation_by_id[call.removeprefix("operation:")]["owner"]
            for call in skills_by_name[coordinator]["calls"]
            if operation_by_id[call.removeprefix("operation:")]["owner"]
            in outcome_coordinators
        }
        require(
            not called_coordinators,
            f"outcome coordinator call edge: {coordinator}",
        )
    resume_handoffs = skills_by_name["resuming-reviewed-prs"]["contract"][
        "terminal_handoffs"
    ]
    require(
        {handoff["owner"] for handoff in resume_handoffs}
        == {
            "addressing-pr-review-feedback",
            "getting-prs-ready-for-review",
            "getting-prs-merged",
            "operation:focused-ci",
            "versionkeeping:resolving-merge-conflicts",
        }
        and len(resume_handoffs) == 5
        and [(handoff["trigger"], handoff["owner"]) for handoff in resume_handoffs[:2]]
        == [
            (
                "active-git-conflict-operation",
                "versionkeeping:resolving-merge-conflicts",
            ),
            ("failed-required-github-actions", "operation:focused-ci"),
        ],
        "resume terminal handoff coverage drift",
    )
    resume = skills_by_name["resuming-reviewed-prs"]
    require(
        "status-only" in resume["contract"]["modes"]
        and "reported" in resume["contract"]["terminal_statuses"]
        and resume["calls"] == ["operation:publication-audit"],
        "resume read-only status boundary drift",
    )
    merge = skills_by_name["getting-prs-merged"]
    require(
        "operation:feedback-acquisition" in merge["calls"]
        and "addressing-pr-review-feedback" not in merge["calls"]
        and any(
            handoff["owner"] == "addressing-pr-review-feedback"
            and handoff["resume"]
            == "fresh-getting-prs-merged-invocation-after-feedback-outcome"
            for handoff in merge["contract"]["terminal_handoffs"]
        ),
        "merge feedback terminal handoff drift",
    )
    graphite = skills_by_name["graphite"]
    require(
        set(graphite["contract"]["modes"])
        == {
            "create",
            "track",
            "navigate",
            "reparent",
            "metadata-repair",
            "diagnose",
            "restack",
            "submit-draft",
        }
        and set(graphite["operations"]) == {"graphite-topology", "graphite-transport"},
        "Graphite mode coverage drift",
    )
    for skill in (skills_by_name["resuming-reviewed-prs"], merge):
        publisher_calls = {
            call
            for call in skill["calls"]
            if call.startswith("operation:")
            and operation_by_id[call.removeprefix("operation:")]["owner"]
            == "publishing-reviewable-prs"
        }
        require(
            publisher_calls == {"operation:publication-audit"},
            "publication audit call boundary drift",
        )
    feedback_acquisition = operation_by_id["feedback-acquisition"]
    require(
        feedback_acquisition["access"] == "read"
        and feedback_acquisition["callers"]
        == ["addressing-pr-review-feedback", "getting-prs-merged"],
        "feedback acquisition boundary drift",
    )
    require(
        any(
            handoff["trigger"] == "verified-merge-and-authorized-remote-ref-deletion"
            and handoff["owner"] == "operation:remote-ref-deletion"
            for handoff in merge["contract"]["terminal_handoffs"]
        )
        and "operation:remote-ref-deletion" not in merge["calls"],
        "merge cleanup terminal handoff drift",
    )
    public_records = {
        semantic_id
        for semantic_id, record in operation_by_id.items()
        if record["owner"] in names and record["import"] is None
    }
    require(
        exported_operations == public_records,
        "public operation owner is not declared by its skill",
    )
    for semantic_id, record in operation_by_id.items():
        expected_callers = {
            component["name"]
            for component in skills
            if semantic_id in component["operations"]
            or f"operation:{semantic_id}" in component["calls"]
        }
        require(
            record["callers"] == sorted(expected_callers),
            f"operation caller drift: {semantic_id}",
        )


def validate_inventories(root: Path) -> None:
    root_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).parts[0] != "skills"
    }
    require(root_files == ROOT_FILES, "plugin root file inventory drift")
    for skill in PUBLIC_SKILLS:
        skill_root = root / "skills" / skill
        actual = {
            path.relative_to(skill_root).as_posix()
            for path in skill_root.rglob("*")
            if path.is_file()
        }
        require(
            actual == EXPECTED_SKILL_FILES[skill],
            f"skill file inventory drift: {skill}",
        )


def validate_no_compatibility_shims(root: Path) -> None:
    found = {path.name for path in root.rglob("*.py")} & COMPATIBILITY_SHIMS
    require(not found, f"compatibility shim remains: {sorted(found)}")


def validate_manifests(root: Path) -> None:
    claude = load_json(root, ".claude-plugin/plugin.json")
    codex = load_agent_plugin_manifest(root)
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
        set(claude) == identity_fields | {"displayName"},
        "Claude manifest schema drift",
    )
    require(
        set(codex) == identity_fields | {"$schema", "extensions"},
        "canonical Agent Plugin manifest schema drift",
    )
    for field in identity_fields:
        require(
            claude[field] == codex[field],
            f"Claude manifest projection drift: {field}",
        )
    require(codex["name"] == "mergecraft", "canonical plugin name drift")
    require(codex["version"] == RELEASE_VERSION, "canonical version drift")
    require(codex["license"] == "MIT", "canonical license drift")
    require(
        codex["repository"] == "https://github.com/nisavid/agents",
        "canonical repository drift",
    )
    require(claude["displayName"] == "Mergecraft", "Claude displayName drift")
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
    require(
        isinstance(interface, dict)
        and set(interface)
        == {
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
            "capabilities",
            "websiteURL",
            "defaultPrompt",
        },
        "Codex interface schema drift",
    )
    require(
        interface["defaultPrompt"] == MANIFEST_PROMPTS,
        "Codex manifest prompts drift",
    )
    discovered = discover_direct_skills(root)
    require(
        discovered == tuple(sorted(PUBLIC_SKILLS)),
        "Agent Plugins direct-child skill inventory drift",
    )
    validate_skill_resource_links(root, discovered)


def validate_adapters_and_frontmatter(root: Path) -> None:
    for skill in PUBLIC_SKILLS:
        skill_content = read(root, f"skills/{skill}/SKILL.md")
        require(
            re.search(rf"^name: {re.escape(skill)}$", skill_content, re.MULTILINE)
            is not None,
            f"skill frontmatter drift: {skill}",
        )
        relative = f"skills/{skill}/agents/openai.yaml"
        adapter = strict_yaml(read(root, relative), relative)
        require(
            isinstance(adapter, dict) and set(adapter) == {"interface"},
            f"adapter schema drift: {skill}",
        )
        interface = adapter["interface"]
        require(
            isinstance(interface, dict)
            and set(interface)
            == {"display_name", "short_description", "default_prompt"},
            f"adapter interface schema drift: {skill}",
        )
        require(
            all(isinstance(value, str) and value for value in interface.values()),
            f"adapter string fields drift: {skill}",
        )
        require(
            interface["default_prompt"] == CODEX_PROMPTS[skill],
            f"namespaced Codex prompt drift: {skill}",
        )


OPERATION_REGISTRY_START = "<!-- BEGIN GENERATED OPERATION REGISTRY -->"
OPERATION_REGISTRY_END = "<!-- END GENERATED OPERATION REGISTRY -->"


def render_operation_registry(operations: list[dict[str, Any]]) -> str:
    lines = [
        OPERATION_REGISTRY_START,
        (
            "| Semantic ID | GitHub aliases | Surface | Access | Authority | "
            "Disposition | Owner | Implementation/import | Callers |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for operation in operations:
        implementation = operation["import"] or operation["implementation"]
        lines.append(
            "| "
            + " | ".join(
                [
                    operation["semantic_id"],
                    ", ".join(operation["github_aliases"]) or "-",
                    operation["surface"],
                    operation["access"],
                    operation["authority"],
                    operation["disposition"],
                    operation["owner"],
                    implementation,
                    ", ".join(operation["callers"]) or "-",
                ]
            )
            + " |"
        )
    lines.append(OPERATION_REGISTRY_END)
    return "\n".join(lines)


def validate_readme_projection(root: Path) -> None:
    readme = read(root, "README.md")
    topology = load_json(root, "topology.json")
    require(
        all(
            heading in readme
            for heading in (
                "## Public skills",
                "## Operation registry",
            )
        ),
        "README projection sections drift",
    )
    skill_projection = readme.split("## Public skills", 1)[1].split(
        "## Operation registry", 1
    )[0]
    for skill in PUBLIC_SKILLS:
        require(
            re.search(
                rf"^\| {re.escape(skill)} \|",
                skill_projection,
                re.MULTILINE,
            )
            is not None,
            f"README skill projection drift: {skill}",
        )
    require(
        readme.count(OPERATION_REGISTRY_START) == 1
        and readme.count(OPERATION_REGISTRY_END) == 1,
        "README operation registry markers drift",
    )
    projection = (
        OPERATION_REGISTRY_START
        + readme.split(OPERATION_REGISTRY_START, 1)[1].split(OPERATION_REGISTRY_END, 1)[
            0
        ]
        + OPERATION_REGISTRY_END
    )
    require(
        projection == render_operation_registry(topology["operations"]),
        "README operation registry stale projection",
    )


def _resolve_link(root: Path, source: Path, target: str) -> None:
    target = target.split("#", 1)[0]
    if (
        not target
        or target.startswith(("http://", "https://", "mailto:"))
        or re.fullmatch(r"[A-Z][A-Z0-9_]*", target)
    ):
        return
    target_path = Path(target)
    require(
        not target_path.is_absolute(),
        f"broken relative link in {source.relative_to(root)}: {target}",
    )
    resolved = (source.parent / target_path).resolve(strict=False)
    require(
        resolved.is_relative_to(root.resolve())
        and resolved.is_file()
        and not resolved.is_symlink(),
        f"broken relative link in {source.relative_to(root)}: {target}",
    )


def validate_links_and_call_projection(root: Path) -> None:
    for path in root.rglob("*.md"):
        content = path.read_text(encoding="utf-8")
        for target in MARKDOWN_LINK_RE.findall(content):
            _resolve_link(root, path, target)
    topology = load_json(root, "topology.json")
    for component in topology["skills"]:
        skill = component["name"]
        content = read(root, f"skills/{skill}/SKILL.md")
        for call in component["calls"]:
            if call in PUBLIC_SKILLS:
                require(
                    f"../{call}/SKILL.md" in content,
                    f"skill call projection drift: {skill} -> {call}",
                )
            elif call.startswith("operation:"):
                operation = call.removeprefix("operation:")
                require(
                    operation in re.sub(r"\s+", "-", content.lower()),
                    f"operation call projection drift: {skill} -> {operation}",
                )
            else:
                require(
                    call in content,
                    f"external call projection drift: {skill} -> {call}",
                )


def validate_portability(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in PORTABILITY_MARKERS:
            require(
                marker not in content,
                f"portability leak in {path.relative_to(root)}: {marker}",
            )
        for route in RETIRED_ROUTES:
            require(
                route not in content,
                f"retired route in {path.relative_to(root)}: {route}",
            )
        require(
            "--fill" not in content,
            f"generic generated-text route in {path.relative_to(root)}",
        )


def _heading_anchor(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9 -]", "", value)
    return re.sub(r"[ -]+", "-", value).strip("-")


def validate_atlas_release_tree(repo_root: Path) -> None:
    atlas_root = repo_root / ATLAS_RELEASE_RELATIVE
    require(
        atlas_root.is_dir() and not atlas_root.is_symlink(),
        "atlas release evidence root is invalid",
    )
    actual_files: set[str] = set()
    for entry in atlas_root.iterdir():
        mode = entry.lstat().st_mode
        require(
            stat.S_ISREG(mode) and not stat.S_ISLNK(mode),
            f"atlas release evidence contains a non-regular entry: {entry.name}",
        )
        actual_files.add(entry.name)
    require(
        actual_files == ATLAS_RELEASE_FILES,
        "atlas release evidence inventory drift",
    )


def validate_change_navigation_reference_example(navigation: str) -> None:
    """Reject a documentation example whose declared change totals disagree."""
    example_match = re.search(
        r"## Diff Disclosure\n.*?```md\n(?P<example>.*?)\n```",
        navigation,
        re.DOTALL,
    )
    require(
        example_match is not None,
        "change-navigation reference example is missing",
    )
    example = example_match.group("example")
    summary_match = re.search(r"<summary>(?P<summary>.*?)</summary>", example)
    require(
        summary_match is not None,
        "change-navigation reference example is missing its Diff summary",
    )
    metric_pattern = re.compile(
        r'alt="(?P<category>IMPL|TEST|DOC|GEN|OTHER): '
        r"(?P<additions>\d+) additions, (?P<deletions>\d+) deletions\""
    )
    atomic_metric_pattern = re.compile(
        r'alt="(?P<additions>\d+) additions, (?P<deletions>\d+) deletions"'
    )
    summary_metric_matches = list(
        metric_pattern.finditer(summary_match.group("summary"))
    )
    summary_metric_counts: dict[str, int] = {}
    for match in summary_metric_matches:
        category = match.group("category")
        summary_metric_counts[category] = summary_metric_counts.get(category, 0) + 1
    require(
        summary_metric_matches
        and all(count == 1 for count in summary_metric_counts.values()),
        "change-navigation reference example summary category badges must be unique",
    )
    summary_metrics = {
        match.group("category"): (
            int(match.group("additions")),
            int(match.group("deletions")),
        )
        for match in summary_metric_matches
    }
    summary_files_match = re.search(
        r'alt="FILES: (?P<count>\d+) touched"', summary_match.group("summary")
    )
    require(
        summary_metrics and summary_files_match is not None,
        "change-navigation reference example is missing summary metrics",
    )

    group_pattern = re.compile(
        r'^- <picture><img alt="(?P<category>IMPL|TEST|DOC|GEN|OTHER): '
        r"(?P<additions>\d+) additions, (?P<deletions>\d+) deletions\".*?"
        r'alt="FILES: (?P<files>\d+) [^"]+ files?".*?\n'
        r"(?P<rows>(?:  - .*\n?)+)",
        re.MULTILINE,
    )
    observed_paths: set[str] = set()
    group_metrics: dict[str, tuple[int, int]] = {}
    for group in group_pattern.finditer(example):
        category = group.group("category")
        require(
            category not in group_metrics,
            "change-navigation reference example repeats a category",
        )
        additions = int(group.group("additions"))
        deletions = int(group.group("deletions"))
        rows = group.group("rows").splitlines()
        require(
            len(rows) == int(group.group("files")),
            "change-navigation reference example group file count disagrees with rows",
        )
        row_totals = [
            (
                int(metric.group("additions")),
                int(metric.group("deletions")),
            )
            for row in rows
            for metric in atomic_metric_pattern.finditer(row)
        ]
        require(
            len(row_totals) == len(rows),
            "change-navigation reference example file metric is missing",
        )
        require(
            (additions, deletions)
            == (
                sum(total[0] for total in row_totals),
                sum(total[1] for total in row_totals),
            ),
            "change-navigation reference example category total disagrees with files",
        )
        for row in rows:
            path = re.search(r"\[`(?P<path>[^`]+)`\]", row)
            require(
                path is not None,
                "change-navigation reference example file path is missing",
            )
            observed_paths.add(path.group("path"))
        group_metrics[category] = (additions, deletions)

    require(
        group_metrics == summary_metrics,
        "change-navigation reference example summary totals disagree with categories",
    )
    require(
        len(observed_paths) == int(summary_files_match.group("count")),
        "change-navigation reference example touched-file total disagrees with files",
    )


def validate_atlas_split(repo_root: Path, root: Path) -> None:
    validate_atlas_release_tree(repo_root)
    contract = load_repository_json(
        repo_root,
        ATLAS_RELEASE_RELATIVE / "review-atlas-contract.json",
    )
    ledger = load_repository_json(
        repo_root,
        ATLAS_RELEASE_RELATIVE / "review-atlas-contribution-ledger.json",
    )
    require(
        isinstance(contract, dict)
        and set(contract)
        == {
            "schema_version",
            "artifacts",
            "contribution_ledger",
            "overlay",
            "preservation",
            "navigation",
            "firewall",
            "visual_budgets",
            "prose_sha256",
        }
        and type(contract["schema_version"]) is int
        and contract["schema_version"] == 1,
        "atlas canonical contract schema drift",
    )
    require(
        contract["artifacts"]
        == {
            "design": "review-atlas-reference-design.md",
            "writer": "SKILL.md",
            "body": "references/body-contract.md",
            "navigation": "references/change-navigation.md",
            "extension": "references/review-atlas-extension.json",
            "ledger": "references/review-atlas-contribution-ledger.json",
        },
        "atlas canonical artifacts drift",
    )
    require(
        isinstance(ledger, dict)
        and set(ledger) == {"schema_version", "source_line_count", "contributions"}
        and type(ledger["schema_version"]) is int
        and ledger["schema_version"] == 1
        and type(ledger["source_line_count"]) is int
        and ledger["source_line_count"] == 399,
        "atlas ledger schema drift",
    )
    contributions = ledger["contributions"]
    require(isinstance(contributions, list) and contributions, "atlas ledger is empty")
    require(
        contributions == EXPECTED_ATLAS_CONTRIBUTIONS,
        "atlas contribution mapping drift",
    )
    headings: list[str] = []
    atlas = read(
        root,
        "skills/writing-reviewable-pr-descriptions/review-atlas-reference-design.md",
    )
    atlas_anchors = {
        _heading_anchor(match.group(1))
        for match in re.finditer(r"^#{2,4} (.+)$", atlas, re.MULTILINE)
    }
    private_categories = set()
    contribution_ids = set()
    for contribution in contributions:
        require(
            isinstance(contribution, dict)
            and isinstance(contribution.get("id"), str)
            and contribution["id"]
            and contribution["id"] not in contribution_ids
            and contribution.get("disposition") in {"public-core", "private-overlay"},
            "atlas ledger contribution schema drift",
        )
        contribution_ids.add(contribution["id"])
        source_headings = contribution.get("source_headings")
        require(
            isinstance(source_headings, list)
            and source_headings
            and all(isinstance(item, str) for item in source_headings),
            "atlas contribution coverage drift",
        )
        headings.extend(source_headings)
        if contribution["disposition"] == "public-core":
            anchor = contribution.get("destination_anchor")
            require(
                isinstance(anchor, str) and anchor in atlas_anchors,
                "atlas public destination drift",
            )
            require(
                "private_overlay_category" not in contribution,
                "atlas public contribution has private fields",
            )
        else:
            require(
                contribution.get("destination_anchor") is None
                and isinstance(contribution.get("private_overlay_category"), str),
                "atlas private overlay ledger drift",
            )
            private_categories.add(contribution["private_overlay_category"])
    require(
        set(headings) == ORIGINAL_ATLAS_HEADINGS,
        "atlas contribution coverage drift",
    )
    require(
        private_categories
        == {
            "worked-application",
            "concrete-routes-attachments-and-access",
            "worked-instance-data",
        },
        "atlas private overlay categories drift",
    )
    require(
        ledger == contract["contribution_ledger"],
        "atlas ledger is not the canonical projection",
    )
    for marker in (
        "github.com/systalyze",
        "private attachment URL",
        "protected atlas",
        "1200-by-675",
        "#1341",
    ):
        require(marker not in atlas, f"private atlas detail imported: {marker}")
    required_sections = (
        "## Semantic manifest",
        "### Temporal change model",
        "### Published payload boundary",
        "### Review contracts",
        "### Claim provenance",
        "### Ambiguity handling",
        "## Adaptive lens decomposition",
        "## Reviewer interaction model",
        "### Publication safety",
        "## Testing strategy",
        "## Reference implementation boundaries",
    )
    for section in required_sections:
        require(section in atlas, f"public atlas contribution missing: {section}")
    normalized_atlas = " ".join(atlas.split())
    require(
        contract["visual_budgets"]
        == {
            "lane_centerline_min_css_px": 16,
            "clearance_min_css_px": 12,
            "desktop_viewports_css_px": ["1024x768", "1280x800", "1512x982"],
            "below_1024": "unsupported",
            "preview_width_css_px": 640,
            "preview_text_min_css_px": 12,
        },
        "atlas canonical visual budgets drift",
    )
    for budget in (
        "at least 16 CSS pixels between centerlines",
        "at least 12 CSS pixels of clearance",
        "1024-by-768, 1280-by-800, and 1512-by-982 CSS-pixel desktop viewports",
        "Viewports below 1024 CSS pixels are explicitly unsupported",
        "640 CSS pixels wide with rendered text at least 12 CSS pixels high",
    ):
        require(budget in normalized_atlas, f"atlas visual budget missing: {budget}")
    extension_relative = (
        "skills/writing-reviewable-pr-descriptions/references/"
        "review-atlas-extension.json"
    )
    require(
        load_json(root, extension_relative) == ATLAS_EXTENSION_CONTRACT
        and contract["overlay"]
        == {
            key: value
            for key, value in ATLAS_EXTENSION_CONTRACT.items()
            if key != "schema_version"
        },
        "atlas extension contract drift",
    )
    writer = read(root, "skills/writing-reviewable-pr-descriptions/SKILL.md")
    body_contract = read(
        root, "skills/writing-reviewable-pr-descriptions/references/body-contract.md"
    )
    navigation = read(
        root,
        "skills/writing-reviewable-pr-descriptions/references/change-navigation.md",
    )
    normalized_writer = " ".join(writer.split())
    normalized_navigation = " ".join(navigation.split())
    validate_change_navigation_reference_example(navigation)
    require(
        "[atlas extension contract](references/review-atlas-extension.json)" in writer,
        "atlas extension contract is not linked from the writer",
    )
    for requirement in (
        "Then read [the atlas design](review-atlas-reference-design.md)",
        "Only after visual-escalation selection",
        "Ordinary PRs do not require atlas previews.",
        "Resolve only its default path.",
        "Load a regular non-symlink overlay only for exact scope",
        "public core wins.",
        (
            "Keep atlas source, tests, docs, manifests, and generated assets outside "
            "application repositories."
        ),
        (
            "Protected deep links may rely on destination access control, but never "
            "embed bearer or signed credentials."
        ),
    ):
        require(
            requirement in normalized_writer,
            f"atlas writer contract drift: {requirement}",
        )
    require(
        "Do not mutate the forge or verify stored/rendered state" in writer,
        "writer content-only terminal boundary drift",
    )
    require(
        "bare `clean` receipt" in writer,
        "writer independent review gate drift",
    )
    for requirement in (
        "Preserve an unauthorized existing field byte-for-byte.",
        "suspected credential",
    ):
        require(
            requirement in body_contract,
            f"atlas body preservation contract drift: {requirement}",
        )
    require(
        "Never quote, echo, preserve, or republish it" in body_contract,
        "PR text secret blocker drift",
    )
    for requirement in (
        "# Change Navigation Reference",
        "## Stack Disclosure",
        "## Diff Disclosure",
        "## Validator Binding",
        (
            "Render exactly one Stack disclosure when stacked and exactly one Diff "
            "disclosure"
        ),
    ):
        require(
            requirement in normalized_navigation,
            f"atlas navigation contract drift: {requirement}",
        )
    require(
        contract["preservation"]
        == {
            "unauthorized_existing_field": "byte-for-byte",
            "suspected_credential": "never-quote-echo-preserve-or-republish",
        }
        and contract["navigation"]
        == {
            "stack_heading": "## Stack Disclosure",
            "diff_heading": "## Diff Disclosure",
            "validator_heading": "## Validator Binding",
            "leading_disclosures": (
                "exactly-one-stack-when-stacked-and-exactly-one-diff"
            ),
        }
        and contract["firewall"]
        == {
            "atlas_implementation": "outside-application-repositories",
            "application_repository": "authorized-review-surface-links-or-outputs-only",
        },
        "atlas canonical semantic mapping drift",
    )
    prose_paths = {
        "design": "/".join(
            (
                "skills",
                "writing-reviewable-pr-descriptions",
                "review-atlas-reference-design.md",
            )
        ),
        "writer": "skills/writing-reviewable-pr-descriptions/SKILL.md",
        "body": "skills/writing-reviewable-pr-descriptions/references/body-contract.md",
        "navigation": "/".join(
            (
                "skills",
                "writing-reviewable-pr-descriptions",
                "references",
                "change-navigation.md",
            )
        ),
    }
    actual_prose_sha256 = {
        name: hashlib.sha256(read(root, path).encode()).hexdigest()
        for name, path in prose_paths.items()
    }
    require(
        actual_prose_sha256 == EXPECTED_ATLAS_PROSE_SHA256,
        "atlas public prose bytes drift",
    )
    require(
        contract["prose_sha256"] == actual_prose_sha256,
        "atlas prose is not bound to the canonical contract",
    )


def validate_retirement_contribution_ledger(repo_root: Path, root: Path) -> None:
    """Validate comparative retirement evidence without turning it into routing."""
    ledger = load_repository_json(repo_root, RETIREMENT_LEDGER_RELATIVE)
    fixtures = load_repository_json(repo_root, RETIREMENT_FIXTURES_RELATIVE)
    corpus = load_repository_json(repo_root, RETIREMENT_CORPUS_RELATIVE)
    definition = load_repository_json(repo_root, RETIREMENT_DEFINITION_RELATIVE)
    require(
        isinstance(fixtures, dict)
        and set(fixtures)
        == {"schema_version", "control_plane_definition", "corpus", "fixtures"}
        and type(fixtures["schema_version"]) is int
        and fixtures["schema_version"] == 4
        and fixtures["control_plane_definition"] == str(RETIREMENT_DEFINITION_RELATIVE)
        and fixtures["corpus"] == str(RETIREMENT_CORPUS_RELATIVE)
        and isinstance(fixtures["fixtures"], list),
        "retirement fixture schema drift",
    )
    fixture_bindings: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    for fixture in fixtures["fixtures"]:
        require(
            isinstance(fixture, dict)
            and set(fixture)
            == {
                "evaluation_skill_id",
                "id",
                "owner_condition",
                "scenario_selector",
                "topology_operations",
            }
            and isinstance(fixture["id"], str)
            and fixture["id"]
            and fixture["id"] not in fixture_bindings
            and fixture["scenario_selector"] == fixture["id"]
            and isinstance(fixture["evaluation_skill_id"], str)
            and fixture["owner_condition"] in {"no_skill", "candidate", "composed"}
            and isinstance(fixture["topology_operations"], list)
            and tuple(fixture["topology_operations"]),
            "retirement fixture schema drift",
        )
        fixture_bindings[fixture["id"]] = (
            fixture["evaluation_skill_id"],
            fixture["owner_condition"],
            tuple(fixture["topology_operations"]),
        )
    require(
        fixture_bindings == RETIREMENT_CASE_BINDINGS,
        "retirement fixture coverage drift",
    )
    require(
        isinstance(corpus, dict)
        and set(corpus) == {"version", "scenarios"}
        and corpus["version"] == 1
        and isinstance(corpus["scenarios"], list),
        "retirement corpus schema drift",
    )
    corpus_assertions: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for scenario in corpus["scenarios"]:
        require(
            isinstance(scenario, dict)
            and set(scenario) == {"id", "prompt", "must_include", "must_not_include"}
            and isinstance(scenario["id"], str)
            and isinstance(scenario["prompt"], str)
            and all(
                isinstance(scenario[field], list)
                and scenario[field]
                and all(isinstance(value, str) and value for value in scenario[field])
                for field in ("must_include", "must_not_include")
            ),
            "retirement corpus schema drift",
        )
        corpus_assertions[scenario["id"]] = (
            tuple(scenario["must_include"]),
            tuple(scenario["must_not_include"]),
        )
    require(
        corpus_assertions == RETIREMENT_FIXTURE_ASSERTIONS,
        "retirement fixture assertion polarity drift",
    )
    topology = load_json(root, "topology.json")
    topology_operations = {
        operation["semantic_id"]: operation for operation in topology["operations"]
    }
    require(
        all(
            operation in topology_operations
            for _, _, operations in fixture_bindings.values()
            for operation in operations
        ),
        "retirement fixture topology mapping drift",
    )
    declarations = {
        item.get("id"): item
        for item in definition.get("skills", [])
        if isinstance(item, dict)
    }
    targets = definition.get("target_skill_ids")
    require(
        isinstance(definition, dict)
        and definition.get("schema_version") == 1
        and definition.get("evaluation_id") == "mergecraft-retirement-comparative-v1"
        and definition.get("conditions")
        == ["no_skill", "incumbent", "candidate", "composed"]
        and definition.get("repetitions") == 3
        and targets == [binding[0] for binding in RETIREMENT_CASE_BINDINGS.values()]
        and all(
            declarations.get(target, {}).get("scenario", {}).get("source")
            == str(RETIREMENT_CORPUS_RELATIVE)
            and declarations[target]["scenario"].get("selector") == fixture_id
            for fixture_id, (target, _, _) in RETIREMENT_CASE_BINDINGS.items()
        ),
        "retirement control-plane definition drift",
    )

    require(
        isinstance(ledger, dict)
        and set(ledger)
        == {
            "schema_version",
            "purpose",
            "scope",
            "limitations",
            "fixture_manifest",
            "fixtures",
            "contributions",
        }
        and type(ledger["schema_version"]) is int
        and ledger["schema_version"] == 2
        and ledger["purpose"] == "comparative-retirement-evidence"
        and ledger["limitations"]
        == (
            "This ledger records reviewed contribution decisions and comparative "
            "evaluation bindings; it is not a runtime router or exhaustive "
            "route-enforcement claim."
        )
        and ledger["fixture_manifest"] == str(RETIREMENT_FIXTURES_RELATIVE)
        and ledger["scope"]
        == {
            "retired_upstream_skills": ["github", "yeet", "gh-address-comments"],
            "retained_upstream_specialist": "github:gh-fix-ci",
        }
        and tuple(ledger["fixtures"]) == RETIREMENT_FIXTURE_IDS
        and isinstance(ledger["contributions"], list),
        "retirement contribution ledger schema drift",
    )
    contributions = ledger["contributions"]
    contribution_ids: set[str] = set()
    for contribution in contributions:
        require(isinstance(contribution, dict), "retirement contribution schema drift")
        disposition = contribution.get("disposition")
        fields = {
            "id",
            "source_skill",
            "contribution",
            "disposition",
            "destination_owner",
            "fixture",
            "topology_operations",
        }
        if disposition == "discard-with-reason":
            fields.add("reason")
        require(
            set(contribution) == fields
            and isinstance(contribution.get("id"), str)
            and contribution["id"]
            and contribution["id"] not in contribution_ids
            and contribution.get("source_skill")
            in {"github", "yeet", "gh-address-comments"}
            and isinstance(contribution.get("contribution"), str)
            and contribution["contribution"]
            and disposition
            in {"retain", "absorb", "ordinary-tool", "discard-with-reason"}
            and contribution.get("destination_owner") in RETIREMENT_DESTINATION_OWNERS
            and contribution.get("fixture") in fixture_bindings
            and isinstance(contribution.get("topology_operations"), list)
            and contribution["topology_operations"]
            and len(contribution["topology_operations"])
            == len(set(contribution["topology_operations"]))
            and all(
                operation in fixture_bindings[contribution["fixture"]][2]
                for operation in contribution["topology_operations"]
            )
            and (
                disposition != "discard-with-reason"
                or (
                    isinstance(contribution.get("reason"), str)
                    and contribution["reason"]
                )
            ),
            "retirement contribution schema drift",
        )
        contribution_ids.add(contribution["id"])
    expected_contributions = {item["id"]: item for item in RETIREMENT_CONTRIBUTIONS}
    require(
        tuple(item["id"] for item in contributions) == tuple(expected_contributions),
        "retirement contribution coverage drift",
    )
    for contribution in contributions:
        decision = {
            key: value
            for key, value in contribution.items()
            if key != "topology_operations"
        }
        require(
            decision == expected_contributions[contribution["id"]],
            "retirement contribution decision drift",
        )

    def canonical_owner(owner: str) -> str:
        if owner == "ordinary-github-read-tools" or ":" in owner:
            return owner
        return f"mergecraft:{owner}"

    def transitive_companions(skill_id: str) -> set[str]:
        expanded: set[str] = set()
        pending = list(declarations[skill_id]["companions"])
        while pending:
            companion = pending.pop(0)
            require(
                companion in declarations,
                "retirement control-plane companion drift",
            )
            if companion in expanded:
                continue
            expanded.add(companion)
            pending.extend(declarations[companion]["companions"])
        return expanded

    contributions_by_fixture = {
        fixture_id: [
            contribution
            for contribution in contributions
            if contribution["fixture"] == fixture_id
        ]
        for fixture_id in RETIREMENT_FIXTURE_IDS
    }
    for fixture_id, fixture_contributions in contributions_by_fixture.items():
        require(fixture_contributions, "retirement fixture has no ledger contribution")
        evaluation_skill, owner_condition, _ = fixture_bindings[fixture_id]
        comparison = declarations[evaluation_skill].get("comparison")
        strategy = RETIREMENT_CASE_STRATEGIES[fixture_id]
        require(
            isinstance(comparison, dict)
            and comparison.get("strategy") == strategy
            and {contribution["disposition"] for contribution in fixture_contributions}
            == {strategy},
            "retirement comparison strategy drift",
        )
        condition_owners = {
            "no_skill": set(),
            "candidate": {evaluation_skill},
            "composed": {evaluation_skill, *transitive_companions(evaluation_skill)},
        }
        if strategy == "ordinary-tool":
            require(
                set(comparison) == {"strategy", "owner"},
                "ordinary-tool comparison owner drift",
            )
            condition_owners["no_skill"].add(comparison["owner"])
        elif strategy == "retain":
            require(
                set(comparison) == {"strategy", "owner"},
                "retained comparison owner drift",
            )
            condition_owners["composed"].add(comparison["owner"])
        else:
            require(
                set(comparison) == {"strategy"},
                "retirement comparison owner drift",
            )
        require(
            all(
                canonical_owner(contribution["destination_owner"])
                in condition_owners[owner_condition]
                for contribution in fixture_contributions
            ),
            "retirement destination owner is absent from evaluated bundle",
        )


def validate_behavior_corpus(repo_root: Path) -> None:
    corpus = load_repository_json(repo_root, EVAL_RELATIVE / "corpus.json")
    require(
        isinstance(corpus, dict)
        and set(corpus) == {"version", "scenarios"}
        and type(corpus["version"]) is int
        and corpus["version"] == 1,
        "behavior corpus schema drift",
    )
    scenarios = corpus["scenarios"]
    require(
        isinstance(scenarios, list)
        and {item.get("id") for item in scenarios if isinstance(item, dict)}
        == BEHAVIOR_SCENARIO_IDS,
        "behavior corpus coverage drift",
    )
    for scenario in scenarios:
        require(
            isinstance(scenario, dict)
            and set(scenario) == {"id", "prompt", "must_include", "must_not_include"},
            "behavior corpus scenario schema drift",
        )
        require(
            isinstance(scenario["prompt"], str)
            and scenario["prompt"]
            and all(
                isinstance(scenario[key], list)
                and scenario[key]
                and all(isinstance(item, str) and item for item in scenario[key])
                for key in ("must_include", "must_not_include")
            ),
            "behavior corpus assertions drift",
        )


def validate_merge_eval_isolation(repo_root: Path) -> None:
    relative = EVAL_RELATIVE / "skills/getting-prs-merged/evals.json"
    document = load_repository_json(repo_root, relative)
    require(
        isinstance(document, dict)
        and set(document) == {"skill_name", "evals"}
        and document["skill_name"] == "getting-prs-merged",
        "merge eval manifest schema drift",
    )
    evals = document["evals"]
    require(
        isinstance(evals, list) and len(evals) == len(MERGE_EVAL_FIXTURES),
        "merge eval coverage drift",
    )
    expected_names = {Path(name).stem for name in MERGE_EVAL_FIXTURES}
    observed_names = set()
    observed_fixtures = set()
    for position, item in enumerate(evals):
        require(
            isinstance(item, dict)
            and set(item)
            == {
                "id",
                "name",
                "prompt",
                "expected_output",
                "files",
                "expectations",
            },
            f"merge eval item schema drift: {position}",
        )
        require(
            type(item["id"]) is int and item["id"] == position,
            "merge eval ids drift",
        )
        name = item["name"]
        require(
            isinstance(name, str) and name in expected_names,
            f"merge eval name drift: {position}",
        )
        require(
            isinstance(item["prompt"], str)
            and item["prompt"]
            and isinstance(item["expected_output"], str)
            and item["expected_output"],
            f"merge eval prose drift: {name}",
        )
        fixture = f"getting-prs-merged/fixtures/{name}.md"
        require(item["files"] == [fixture], f"merge eval fixture binding drift: {name}")
        require(
            isinstance(item["expectations"], list)
            and item["expectations"]
            and all(
                isinstance(expectation, str) and expectation
                for expectation in item["expectations"]
            ),
            f"merge eval expectations drift: {name}",
        )
        fixture_body = read_repository_file(
            repo_root, EVAL_RELATIVE / f"skills/{fixture}"
        ).lower()
        for marker in (
            "expected behavior",
            "expected_output",
            "expectations",
            "pass if",
            "must include",
            "must not include",
        ):
            require(
                marker not in fixture_body,
                f"merge eval grader answer leaked into fixture: {name}",
            )
        observed_names.add(name)
        observed_fixtures.add(Path(fixture).name)
    require(observed_names == expected_names, "merge eval name coverage drift")
    require(
        observed_fixtures == set(MERGE_EVAL_FIXTURES),
        "merge eval fixture coverage drift",
    )


def validate_raw_skill_eval_isolation(repo_root: Path) -> None:
    for skill, fixture_names in RAW_SKILL_EVAL_FIXTURES.items():
        relative = EVAL_RELATIVE / f"skills/{skill}/evals.json"
        document = load_repository_json(repo_root, relative)
        require(
            isinstance(document, dict)
            and set(document) == {"skill_name", "evals"}
            and document["skill_name"] == skill,
            f"raw eval manifest schema drift: {skill}",
        )
        evals = document["evals"]
        require(
            isinstance(evals, list) and len(evals) == len(fixture_names),
            f"raw eval coverage drift: {skill}",
        )
        for position, (item, fixture_name) in enumerate(zip(evals, fixture_names)):
            require(
                isinstance(item, dict)
                and set(item)
                == {
                    "id",
                    "name",
                    "prompt",
                    "expected_output",
                    "files",
                    "expectations",
                }
                and type(item["id"]) is int
                and item["id"] == position
                and item["name"] == Path(fixture_name).stem,
                f"raw eval item schema drift: {skill}",
            )
            fixture = f"{skill}/fixtures/{fixture_name}"
            require(
                item["files"] == [fixture],
                f"raw eval fixture binding drift: {skill}",
            )
            require(
                all(
                    isinstance(item[field], str) and item[field]
                    for field in ("name", "prompt", "expected_output")
                )
                and isinstance(item["expectations"], list)
                and item["expectations"]
                and all(
                    isinstance(expectation, str) and expectation
                    for expectation in item["expectations"]
                ),
                f"raw eval prose drift: {skill}",
            )
            fixture_body = read_repository_file(
                repo_root, EVAL_RELATIVE / f"skills/{fixture}"
            ).lower()
            for marker in (
                "expected behavior",
                "expected_output",
                "expectations",
                "pass if",
                "must include",
                "must not include",
            ):
                require(
                    marker not in fixture_body,
                    f"raw eval grader answer leaked into fixture: {skill}",
                )


def validate_runtime_contracts(root: Path) -> None:
    state = read(
        root,
        "skills/publishing-reviewable-prs/scripts/reviewable_pr_state.py",
    )
    create = read(
        root,
        "skills/publishing-reviewable-prs/scripts/create_reviewable_pr.py",
    )
    update = read(
        root,
        "skills/publishing-reviewable-prs/scripts/update_reviewable_pr.py",
    )
    graphite = read(
        root,
        "skills/graphite/scripts/submit_draft_stack.py",
    )
    audit = read(
        root,
        "skills/publishing-reviewable-prs/scripts/audit_reviewable_pr.py",
    )
    receipts = read(
        root,
        "skills/publishing-reviewable-prs/scripts/publication_receipts.py",
    )
    required_review = read(
        root,
        "skills/publishing-reviewable-prs/scripts/required_review.py",
    )
    comment = read(
        root,
        "skills/getting-prs-merged/scripts/post_coderabbit_comment.py",
    )
    review_input = read(
        root,
        "skills/writing-reviewable-pr-descriptions/scripts/"
        "change_navigation/review_input.py",
    )
    feedback = read(
        root,
        "skills/addressing-pr-review-feedback/scripts/review_feedback_state.py",
    )
    writer = read(root, "skills/writing-reviewable-pr-descriptions/SKILL.md")
    body_contract = read(
        root,
        "skills/writing-reviewable-pr-descriptions/references/body-contract.md",
    )
    publisher = read(root, "skills/publishing-reviewable-prs/SKILL.md")
    feedback_skill = read(root, "skills/addressing-pr-review-feedback/SKILL.md")
    ci_adapter = read(
        root,
        "skills/getting-prs-merged/references/gh-fix-ci-adapter.md",
    )
    merge_actuator = read(
        root,
        "skills/getting-prs-merged/references/merge-actuator.md",
    )
    normalized_writer = " ".join(writer.split())
    normalized_body_contract = " ".join(body_contract.split())
    normalized_ci_adapter = " ".join(ci_adapter.split())
    normalized_merge_actuator = " ".join(merge_actuator.split())
    require(
        (
            "return the complete validated title/body pair, authorized text surface, "
            "and review-input manifest"
        )
        in normalized_writer
        and "Do not mutate the forge or verify stored/rendered state"
        in normalized_writer,
        "writer content-only terminal boundary drift",
    )
    require(
        "tricritical:loop" in writer
        and "exact candidate title/body bytes" in normalized_writer
        and "bare `clean` receipt" in normalized_writer
        and "no forge or source authority" in normalized_writer,
        "writer independent review gate drift",
    )
    require(
        "hard security gate" in writer
        and "hard security gate" in normalized_body_contract
        and "Never quote, echo, preserve, or republish it" in normalized_body_contract
        and "suspected credential" in publisher,
        "PR text secret blocker drift",
    )
    require(
        "Snapshot/orientation" in feedback_skill
        and "stop before step 2" in feedback_skill
        and all(
            forbidden in feedback_skill
            for forbidden in (
                "no disposition",
                "adjudication",
                "revision",
                "checkpoint",
                "interaction",
                "publication",
                "mutation authority",
            )
        ),
        "feedback snapshot mode drift",
    )
    require(
        "upstream `github:gh-fix-ci`" in normalized_ci_adapter
        and "GitHub Actions only" in normalized_ci_adapter
        and "separate, explicit mutation authority" in normalized_ci_adapter
        and all(
            forbidden in normalized_ci_adapter
            for forbidden in (
                "PR title/body",
                "readiness changes",
                "comments",
                "thread resolution",
                "merge actuation",
                "Git/ref publication",
            )
        ),
        "gh-fix-ci adapter authority drift",
    )
    require(
        "`internal:merge-actuator` owns two separate leaves"
        in normalized_merge_actuator
        and "`merge-inspection` is read-only" in normalized_merge_actuator
        and "sole `merge-write` authority" in normalized_merge_actuator
        and "execute at most once" in normalized_merge_actuator
        and "never retry an ambiguous possible-mutation result"
        in normalized_merge_actuator
        and "reread, head-bound merge receipt" in normalized_merge_actuator
        and all(
            forbidden in normalized_merge_actuator
            for forbidden in (
                "PR text",
                "Git refs",
                "post comments",
                "resolve threads",
                "start review loops",
                "delete branches",
                "deploy",
            )
        ),
        "merge actuator authority drift",
    )
    for term in (
        "head_repository",
        "nameWithOwner",
        "def run_read(",
        "def run_mutation(",
        "def strict_json(",
        "timeout=READ_TIMEOUT_SECONDS",
        "timeout=MUTATION_TIMEOUT_SECONDS",
        "GH_PROMPT_DISABLED",
    ):
        require(term in state, f"publisher runtime contract missing: {term}")
    for term in (
        "VALIDATION_PR_NUMBER",
        "template_path",
        "_run_read",
        "_run_mutation",
        "head_repository",
    ):
        require(term in create, f"publisher create contract missing: {term}")
    publish = create[create.index("def publish(") :]
    first_validation = publish.index("_validate(")
    creation = publish.index("_create(")
    second_validation = publish.index("_validate(", first_validation + 1)
    require(
        first_validation < creation < second_validation,
        "publisher create validation order drift",
    )
    require(
        publish.index("prepare_receipt_store(") < creation
        and "prepare_receipt_ledger(receipt_root, expected)" in publish,
        "publisher receipt store must be prepared before forge mutation",
    )
    require(
        "_run_read" in update and "_run_mutation" in update,
        "publisher update timeout classification drift",
    )
    for term in (
        'choices=("body-only", "title-only", "title-body")',
        'text_scope == "body-only" and title != before["title"]',
        'text_scope == "title-only" and body != before["body"]',
    ):
        require(term in update, f"publisher text-scope contract missing: {term}")
    for term in (
        "text publication is a no-op",
        "prepare_receipt_store(receipt_directory)",
        "prepare_receipt_ledger(receipt_root, expected)",
        "verified_transition(",
        "preimage=before",
        "final_reread=after",
    ):
        require(term in update, f"publisher transition contract missing: {term}")
    for term in (
        "SCHEMA_VERSION = 3",
        "PUBLISHER_VERSION = 1",
        "POLICY_VERSION = 1",
        '"sequence"',
        '"predecessor_sha256"',
        '"content_sha256"',
        "RECEIPT_NAME_RE",
        "TEMP_NAME_RE",
        'uuid.UUID(value["receipt_id"]).version != 4',
        '"mergecraft/pr-publication-receipts"',
        "receipts[-1]",
        "actual state transition",
    ):
        require(term in receipts, f"publisher receipt-ledger contract missing: {term}")
    for term in (
        'CANDIDATE_CONTRACT = "mergecraft-publication-candidate-v1"',
        "".join(
            (
                "REQUIRED_PROFILE_CONTRACT = ",
                '"mergecraft-required-publication-review-profile-v2"',
            )
        ),
        'ENVELOPE_CONTRACT = "task-witness-launch-envelope-v1"',
        'WITNESS_CONTRACT = "task-witness-canonical-projection-v2"',
        'PROJECTION_CONTRACT = "tricritical-terminal-review-projection-v2"',
        'PRODUCER_ID = "tricritical-review-loop-v2"',
        "pwd.getpwuid(os.geteuid())",
        "class _AuthenticatedFrontDoorObservation",
        "_FRONT_DOOR_CALL_CAPABILITY = object()",
        "def _authenticated_front_door(",
        "def _supervised_process(",
        "canonical Task Witness supervisor requires its closed internal call shape",
        "envelope = _strict_envelope(stdout)",
        "def validate_transition_candidate(",
        '"gpt-5.6-sol"',
        '"reasoning_effort": "high"',
        '"surface": "chatgpt-codex"',
        '"execution_result": "product-attested"',
        '"assurance_minimum"',
        "Rolecasting publication execution assurance minimum drift",
        '"selected_specialists"',
        "def _make_publication_review(",
    ):
        require(
            term in required_review,
            f"publisher required-review contract missing: {term}",
        )
    for term in (
        "first = stored_pr(",
        "second = stored_pr(",
        "if second != first",
        "record_reconciliation(",
        "suspected_secret_error(value)",
    ):
        require(term in audit, f"publisher reconciliation contract missing: {term}")
    for term in (
        "def build_plan(",
        "def execute(",
        "def repair(",
        '"gt",',
        '"submit",',
        '"--stack",',
        '"--draft",',
        '"--no-edit",',
        '"--no-ai",',
        '"--no-interactive",',
        "prepare_receipt_store()",
        '"final_audit_command"',
        '"target_review_mode"',
        '"target_publication_candidate_sha256"',
        '"target_selected_specialists"',
        "SCHEMA_VERSION = 2",
        "not _audit_matches_target(audit, item)",
    ):
        require(term in graphite, f"Graphite helper contract missing: {term}")
    for term in (
        "copy.deepcopy",
        '"repository"',
        "head_repository",
        "exact ordered fragment derivation",
    ):
        require(term in review_input, f"review-input contract missing: {term}")
    for term in (
        "def post_comment(",
        "body_sha256",
        "_run_mutation",
        "_run_read",
        "must not be retried",
    ):
        require(term in comment, f"comment actuator contract missing: {term}")
    require(
        feedback.count("json.loads(") == 1
        and "object_pairs_hook=unique_object" in feedback
        and "timeout=READ_TIMEOUT_SECONDS" in feedback
        and "copy.deepcopy" in feedback
        and "headRepository" in feedback
        and "head_repo" in feedback
        and "head_owner" in feedback
        and "validate_head_repository_identity" in feedback,
        "feedback trust-boundary contract drift",
    )
    require(
        state.count("json.loads(") == 1,
        "publisher forge JSON must use one strict decoder",
    )
    require(
        review_input.count("json.loads(") == 1,
        "review input must use one strict decoder",
    )
    combined = (
        f"{state}\n{create}\n{update}\n{required_review}\n{comment}\n{review_input}"
    )
    for forbidden in ("$HOME/.agents", "run as _run"):
        require(
            forbidden not in combined,
            f"publisher runtime contract contains forbidden route: {forbidden}",
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


def content_lock_document(root: Path) -> dict:
    return {
        "schema_version": 1,
        "algorithm": "sha256",
        "files": {
            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in semantic_release_paths(root)
        },
    }


def write_content_lock(repo_root: Path, root: Path) -> None:
    path = repo_root / CONTENT_LOCK_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(content_lock_document(root), indent=2) + "\n",
        encoding="utf-8",
    )


def validate_content_lock(repo_root: Path, root: Path) -> None:
    lock = load_repository_json(repo_root, CONTENT_LOCK_RELATIVE)
    require(isinstance(lock, dict), "semantic content lock must be an object")
    require(
        set(lock) == {"schema_version", "algorithm", "files"},
        "semantic content lock fields drift",
    )
    require(
        type(lock["schema_version"]) is int and lock["schema_version"] == 1,
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
            isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
            for digest in files.values()
        ),
        "semantic content lock digest is invalid",
    )
    require(lock == content_lock_document(root), "semantic content lock mismatch")


def validate(
    repo_root: Path, selected_skill: str | None = None, *, source_stage: bool = False
) -> None:
    root = locate_plugin(repo_root)
    validate_tree_entries(root)
    validate_topology(root)
    validate_no_compatibility_shims(root)
    validate_inventories(root)
    validate_serialized_files(root, source_stage=source_stage)
    validate_manifests(root)
    # Source-stage Atlas validation proves the candidate's public source shape.
    # Adapter YAML is a release-only concern and may require an optional parser.
    if not source_stage:
        validate_adapters_and_frontmatter(root)
    validate_readme_projection(root)
    validate_links_and_call_projection(root)
    validate_portability(root)
    validate_atlas_split(repo_root, root)
    validate_external_eval_tree(repo_root)
    validate_retirement_contribution_ledger(repo_root, root)
    validate_behavior_corpus(repo_root)
    validate_merge_eval_isolation(repo_root)
    validate_raw_skill_eval_isolation(repo_root)
    validate_runtime_contracts(root)
    if not source_stage:
        validate_content_lock(repo_root, root)
    if selected_skill is not None:
        require(selected_skill in PUBLIC_SKILLS, "unknown public skill")
        print(f"Mergecraft quick validation passed: {selected_skill}")
    else:
        print("Mergecraft contract validation passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--skill", choices=PUBLIC_SKILLS)
    parser.add_argument("--write-content-lock", action="store_true")
    parser.add_argument(
        "--source-stage",
        action="store_true",
        help="validate an unpinned public candidate without accepting it as a release",
    )
    args = parser.parse_args()
    try:
        if args.write_content_lock:
            root = locate_plugin(args.repository)
            validate_tree_entries(root)
            write_content_lock(args.repository, root)
        validate(args.repository, args.skill, source_stage=args.source_stage)
    except (
        AgentPluginContractError,
        ContractError,
        FileNotFoundError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        YAML_ERROR,
    ) as error:
        print(f"Mergecraft contract validation failed: {error}", file=sys.stderr)
        return 1
    if args.write_content_lock:
        print("Mergecraft semantic content lock updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
