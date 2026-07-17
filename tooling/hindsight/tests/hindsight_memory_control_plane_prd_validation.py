#!/usr/bin/env python3
from __future__ import annotations

import copy
import re
import subprocess
import sys
import tempfile
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn


MAX_GIT_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_GIT_OBJECT_BYTES = 16 * 1024 * 1024
MAX_METADATA_OBJECT_BYTES = 1024 * 1024
MAX_INPUT_FILE_BYTES = 16 * 1024 * 1024
MAX_PUBLICATION_SCAN_BYTES = 256 * 1024 * 1024
MAX_PUBLICATION_COMMITS = 10_000
MAX_PUBLICATION_DIFF_RECORDS = 1_000_000
MAX_WILDCARD_MATCH_WORK = 64 * 1024 * 1024
CONFUSABLES = str.maketrans({
    "А": "a", "В": "b", "Е": "e", "К": "k", "М": "m", "Н": "h",
    "О": "o", "Р": "p", "С": "c", "Т": "t", "Х": "x", "У": "y",
    "а": "a", "е": "e", "і": "i", "ј": "j", "о": "o", "р": "p",
    "с": "c", "ѕ": "s", "х": "x", "у": "y", "Α": "a", "Β": "b",
    "Ε": "e", "Ι": "i", "Κ": "k", "Μ": "m", "Ν": "n", "Ο": "o",
    "Ρ": "p", "Τ": "t", "Υ": "y", "Χ": "x", "α": "a", "ι": "i",
    "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "y",
    "χ": "x",
})
UNAUDITED_SCRIPT_MARKER = "\x00"


PUBLIC_TARGET_MODELS = frozenset(
    {
        "operator-profile",
        "engineering-principles",
    }
)
REPOSITORY_ALIAS = re.compile(
    r"(?:[a-z][a-z0-9-]*:)?[a-z0-9][a-z0-9-]*\Z"
)


class ValidationError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ValidatedCatalog:
    forbidden_literals: tuple[str, ...]


@dataclass
class _WorkBudget:
    remaining: int
    rejection_code: str

    def consume(self, amount: int) -> None:
        if amount < 0 or amount > self.remaining:
            reject(self.rejection_code)
        self.remaining -= amount


def reject(code: str) -> NoReturn:
    raise ValidationError(code)


def validate_catalog(catalog: dict[str, Any]) -> ValidatedCatalog:
    expected_top_level = {
        "schema_version",
        "contextual_models",
        "contextual_model_migrations",
        "repository_catalog",
        "workflow_catalog",
        "privacy",
    }
    version = catalog.get("schema_version")
    if type(version) is not int or version != 1:
        reject("schema_version")
    if set(catalog) != expected_top_level:
        reject("top_level_keys")

    models = catalog.get("contextual_models")
    if not isinstance(models, list) or not models:
        reject("contextual_models")
    model_ids: list[str] = []
    selector_tags: list[str] = []
    source_filter_tags: list[str] = []
    for model in models:
        if not isinstance(model, dict):
            reject("contextual_model_entry")
        if set(model) != {"id", "selector_tag", "source_filter_tags"}:
            reject("contextual_model_keys")
        model_id = model.get("id")
        selector_tag = model.get("selector_tag")
        filter_tags = model.get("source_filter_tags")
        if not isinstance(model_id, str) or not model_id:
            reject("contextual_model_id")
        if not isinstance(selector_tag, str) or not selector_tag:
            reject("contextual_model_selector")
        if (
            not isinstance(filter_tags, list)
            or not filter_tags
            or any(not isinstance(value, str) for value in filter_tags)
        ):
            reject("contextual_model_filter")
        if len(filter_tags) != len(set(filter_tags)):
            reject("contextual_model_filter_duplicates")
        model_ids.append(model_id)
        selector_tags.append(selector_tag)
        source_filter_tags.extend(filter_tags)
    if len(model_ids) != len(set(model_ids)):
        reject("contextual_model_id_duplicates")
    if len(selector_tags) != len(set(selector_tags)):
        reject("contextual_model_selector_duplicates")

    repositories = catalog.get("repository_catalog")
    if not isinstance(repositories, dict):
        reject("repository_catalog")
    if set(repositories) != {"canonical", "aliases", "drop_aliases"}:
        reject("repository_catalog_keys")
    canonical = repositories.get("canonical")
    aliases = repositories.get("aliases")
    drop_aliases = repositories.get("drop_aliases")
    if not isinstance(canonical, list) or not canonical:
        reject("canonical_repositories")
    if any(
        not isinstance(value, str)
        or not re.fullmatch(r"repo:[a-z0-9][a-z0-9-]*", value)
        for value in canonical
    ):
        reject("canonical_repository_form")
    if len(canonical) != len(set(canonical)):
        reject("canonical_repository_duplicates")
    if not isinstance(aliases, dict):
        reject("repository_aliases")
    if any(
        not isinstance(source, str)
        or REPOSITORY_ALIAS.fullmatch(source) is None
        or not isinstance(target, str)
        or not target
        for source, target in aliases.items()
    ):
        reject("repository_alias_form")
    if any(target not in canonical for target in aliases.values()):
        reject("repository_alias_target")
    if (
        not isinstance(drop_aliases, list)
        or any(
            not isinstance(value, str)
            or REPOSITORY_ALIAS.fullmatch(value) is None
            for value in drop_aliases
        )
    ):
        reject("repository_drop_aliases")
    if len(drop_aliases) != len(set(drop_aliases)):
        reject("repository_drop_alias_duplicates")
    alias_sources = set(aliases) | set(drop_aliases)
    if set(drop_aliases) & set(aliases):
        reject("repository_alias_disposition_conflict")
    if alias_sources & set(canonical):
        reject("canonical_repository_alias_source")

    workflows = catalog.get("workflow_catalog")
    if not isinstance(workflows, dict) or set(workflows) != {"controlled"}:
        reject("workflow_catalog_keys")
    controlled_workflows = workflows.get("controlled")
    if not isinstance(controlled_workflows, list):
        reject("controlled_workflows")
    if any(
        not isinstance(value, str)
        or not re.fullmatch(r"workflow:[a-z0-9][a-z0-9-]*", value)
        for value in controlled_workflows
    ):
        reject("controlled_workflow_form")
    if len(controlled_workflows) != len(set(controlled_workflows)):
        reject("controlled_workflow_duplicates")

    controlled_selectors = set(canonical) | set(controlled_workflows)
    if any(
        value not in controlled_selectors
        for value in selector_tags + source_filter_tags
    ):
        reject("contextual_model_selector_reference")

    migrations = catalog.get("contextual_model_migrations")
    if not isinstance(migrations, list) or not migrations:
        reject("contextual_model_migrations")
    migration_sources: list[str] = []
    private_successors: list[str] = []
    resolved_target_models = set(model_ids) | set(PUBLIC_TARGET_MODELS)
    for migration in migrations:
        if not isinstance(migration, dict):
            reject("migration_entry")
        source_id = migration.get("source_id")
        disposition = migration.get("disposition")
        target_id = migration.get("target_id")
        if (
            not isinstance(disposition, str)
            or disposition not in {"retain", "supersede", "retire"}
        ):
            reject("migration_disposition")
        expected_keys = (
            {"source_id", "disposition"}
            if disposition == "retire"
            else {"source_id", "disposition", "target_id"}
        )
        if set(migration) != expected_keys:
            reject("migration_keys")
        if not isinstance(source_id, str) or not source_id:
            reject("migration_source")
        if disposition == "retain" and target_id != source_id:
            reject("retain_target")
        if disposition == "supersede" and target_id == source_id:
            reject("supersede_same_id")
        if disposition in {"retain", "supersede"} and (
            not isinstance(target_id, str) or target_id not in resolved_target_models
        ):
            reject("migration_target_unresolved")
        if disposition in {"retain", "supersede"} and target_id not in PUBLIC_TARGET_MODELS:
            private_successors.append(target_id)
        migration_sources.append(source_id)
    if len(migration_sources) != len(set(migration_sources)):
        reject("migration_source_duplicates")

    privacy = catalog.get("privacy")
    if not isinstance(privacy, dict) or set(privacy) != {"public_forbidden_literals"}:
        reject("privacy_keys")
    forbidden = privacy.get("public_forbidden_literals")
    if (
        not isinstance(forbidden, list)
        or not forbidden
        or any(not isinstance(value, str) or not value for value in forbidden)
    ):
        reject("privacy_literals")
    if len(forbidden) != len(set(forbidden)):
        reject("privacy_literal_duplicates")
    if any(not value.isascii() for value in forbidden):
        reject("privacy_literals")
    required_private = (
        set(model_ids)
        | set(selector_tags)
        | set(source_filter_tags)
        | set(migration_sources)
        | set(private_successors)
        | set(canonical)
        | set(aliases)
        | set(aliases.values())
        | set(drop_aliases)
        | set(controlled_workflows)
    )
    if any(not value.isascii() for value in required_private):
        reject("private_guard_identifier")
    if not required_private.issubset(forbidden):
        reject("private_guard_incomplete")

    return ValidatedCatalog(tuple(forbidden))


def synthetic_catalog() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contextual_models": [
            {
                "id": "private-runbook-v2",
                "selector_tag": "repo:synthetic",
                "source_filter_tags": ["repo:synthetic"],
            }
        ],
        "contextual_model_migrations": [
            {
                "source_id": "private-runbook-v2",
                "disposition": "retain",
                "target_id": "private-runbook-v2",
            }
        ],
        "repository_catalog": {
            "canonical": ["repo:synthetic"],
            "aliases": {"project:synthetic": "repo:synthetic"},
            "drop_aliases": ["legacy-global-repository"],
        },
        "workflow_catalog": {"controlled": ["workflow:synthetic"]},
        "privacy": {
            "public_forbidden_literals": [
                "private-runbook-v2",
                "legacy-private-runbook",
                "repo:synthetic",
                "project:synthetic",
                "legacy-global-repository",
                "workflow:synthetic",
            ]
        },
    }


def expect_valid(catalog: dict[str, Any]) -> None:
    validate_catalog(catalog)


def expect_invalid(catalog: dict[str, Any], code: str) -> None:
    try:
        validate_catalog(catalog)
    except ValidationError as error:
        if error.code != code:
            reject("synthetic_case_wrong_rejection")
    else:
        reject("synthetic_case_unexpectedly_valid")


def validate_synthetic_migration_cases() -> None:
    expect_valid(synthetic_catalog())

    non_ascii_forbidden = synthetic_catalog()
    non_ascii_forbidden["privacy"]["public_forbidden_literals"].append(
        "private-rünbook"
    )
    expect_invalid(non_ascii_forbidden, "privacy_literals")

    non_ascii_cases = []

    model_identifier = synthetic_catalog()
    model_identifier["contextual_models"][0]["id"] = "private-rünbook-v2"
    model_identifier["contextual_model_migrations"][0].update(
        source_id="private-rünbook-v2", target_id="private-rünbook-v2"
    )
    non_ascii_cases.append((model_identifier, "private_guard_identifier"))

    migration_source = synthetic_catalog()
    migration_source["contextual_model_migrations"] = [
        {"source_id": "legacy-rünbook", "disposition": "retire"}
    ]
    non_ascii_cases.append((migration_source, "private_guard_identifier"))

    alias_source = synthetic_catalog()
    alias_source["repository_catalog"]["aliases"]["project:répo"] = (
        "repo:synthetic"
    )
    non_ascii_cases.append((alias_source, "repository_alias_form"))

    dropped_alias = synthetic_catalog()
    dropped_alias["repository_catalog"]["drop_aliases"].append(
        "legacy-répository"
    )
    non_ascii_cases.append(
        (dropped_alias, "repository_drop_aliases")
    )

    selector = synthetic_catalog()
    selector["contextual_models"][0]["selector_tag"] = "repo:synthetíc"
    selector["contextual_models"][0]["source_filter_tags"] = [
        "repo:synthetíc"
    ]
    selector["repository_catalog"]["canonical"] = ["repo:synthetíc"]
    selector["repository_catalog"]["aliases"]["project:synthetic"] = (
        "repo:synthetíc"
    )
    non_ascii_cases.append((selector, "canonical_repository_form"))

    workflow = synthetic_catalog()
    workflow["workflow_catalog"]["controlled"] = ["workflow:réview"]
    non_ascii_cases.append((workflow, "controlled_workflow_form"))

    for catalog, code in non_ascii_cases:
        expect_invalid(catalog, code)

    unhashable_repository = synthetic_catalog()
    unhashable_repository["repository_catalog"]["canonical"] = [
        {"repo": "synthetic"}
    ]
    expect_invalid(unhashable_repository, "canonical_repository_form")

    unhashable_workflow = synthetic_catalog()
    unhashable_workflow["workflow_catalog"]["controlled"] = [
        ["workflow:synthetic"]
    ]
    expect_invalid(unhashable_workflow, "controlled_workflow_form")

    empty_optional_catalogs = synthetic_catalog()
    empty_optional_catalogs["repository_catalog"]["aliases"] = {}
    empty_optional_catalogs["repository_catalog"]["drop_aliases"] = []
    empty_optional_catalogs["workflow_catalog"]["controlled"] = []
    expect_valid(empty_optional_catalogs)

    empty_alias_source = synthetic_catalog()
    empty_alias_source["repository_catalog"]["aliases"] = {
        "": "repo:synthetic"
    }
    expect_invalid(empty_alias_source, "repository_alias_form")

    for malformed in ("project_name", "project.name"):
        malformed_alias_source = synthetic_catalog()
        malformed_alias_source["repository_catalog"]["aliases"] = {
            malformed: "repo:synthetic"
        }
        expect_invalid(malformed_alias_source, "repository_alias_form")

        malformed_drop_alias = synthetic_catalog()
        malformed_drop_alias["repository_catalog"]["drop_aliases"] = [
            malformed
        ]
        expect_invalid(
            malformed_drop_alias, "repository_drop_aliases"
        )


    boolean_version = synthetic_catalog()
    boolean_version["schema_version"] = True
    expect_invalid(boolean_version, "schema_version")

    float_version = synthetic_catalog()
    float_version["schema_version"] = 1.0
    expect_invalid(float_version, "schema_version")

    public_successor = synthetic_catalog()
    public_successor["contextual_model_migrations"] = [
        {
            "source_id": "legacy-private-runbook",
            "disposition": "supersede",
            "target_id": "engineering-principles",
        }
    ]
    expect_valid(public_successor)

    private_successor = synthetic_catalog()
    private_successor["contextual_model_migrations"] = [
        {
            "source_id": "legacy-private-runbook",
            "disposition": "supersede",
            "target_id": "private-runbook-v2",
        }
    ]
    expect_valid(private_successor)

    missing_private_guard = copy.deepcopy(private_successor)
    missing_private_guard["privacy"]["public_forbidden_literals"].remove(
        "private-runbook-v2"
    )
    expect_invalid(missing_private_guard, "private_guard_incomplete")

    dangling_successor = copy.deepcopy(public_successor)
    dangling_successor["contextual_model_migrations"][0]["target_id"] = (
        "missing-target-model"
    )
    expect_invalid(dangling_successor, "migration_target_unresolved")

    same_id_successor = copy.deepcopy(public_successor)
    same_id_successor["contextual_model_migrations"][0]["target_id"] = (
        "legacy-private-runbook"
    )
    expect_invalid(same_id_successor, "supersede_same_id")

    retire = synthetic_catalog()
    retire["contextual_model_migrations"] = [
        {"source_id": "legacy-private-runbook", "disposition": "retire"}
    ]
    expect_valid(retire)

    target_bearing_retire = copy.deepcopy(retire)
    target_bearing_retire["contextual_model_migrations"][0]["target_id"] = (
        "engineering-principles"
    )
    expect_invalid(target_bearing_retire, "migration_keys")

    unhashable_disposition = synthetic_catalog()
    unhashable_disposition["contextual_model_migrations"][0]["disposition"] = [
        "retain"
    ]
    expect_invalid(unhashable_disposition, "migration_disposition")

    canonical_map_source = synthetic_catalog()
    canonical_map_source["repository_catalog"]["aliases"] = {
        "repo:synthetic": "repo:synthetic"
    }
    expect_invalid(canonical_map_source, "canonical_repository_alias_source")

    canonical_drop_source = synthetic_catalog()
    canonical_drop_source["repository_catalog"]["drop_aliases"] = [
        "repo:synthetic"
    ]
    expect_invalid(canonical_drop_source, "canonical_repository_alias_source")


def git(
    repo_root: Path, *args: str, text: bool = True,
    max_bytes: int = MAX_GIT_OUTPUT_BYTES,
    inspection_budget: _WorkBudget | None = None,
) -> str | bytes:
    process = None
    try:
        process = subprocess.Popen(
            ["git", "--no-replace-objects", "-C", str(repo_root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        payload = process.stdout.read(max_bytes + 1)
        if len(payload) > max_bytes:
            process.kill()
            process.wait()
            reject("git_inspection_limit")
        if process.wait() != 0:
            reject("git_inspection")
        if inspection_budget is not None:
            inspection_budget.consume(len(payload))
        return payload.decode("utf-8") if text else payload
    except (OSError, UnicodeDecodeError):
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        reject("git_inspection")


def git_object(
    repo_root: Path,
    object_type: str,
    object_id: str,
    *,
    max_bytes: int,
    inspection_budget: _WorkBudget | None = None,
) -> bytes:
    rendered_size = git(
        repo_root,
        "cat-file",
        "-s",
        object_id,
        max_bytes=64,
        inspection_budget=inspection_budget,
    ).strip()
    if not rendered_size.isascii() or not rendered_size.isdigit():
        reject("git_inspection")
    if int(rendered_size) > max_bytes:
        reject("git_inspection_limit")
    return git(
        repo_root, "cat-file", object_type, object_id,
        text=False,
        max_bytes=max_bytes,
        inspection_budget=inspection_budget,
    )


def bounded_file_bytes(path: Path, *, max_bytes: int = MAX_INPUT_FILE_BYTES) -> bytes:
    try:
        if path.stat().st_size > max_bytes:
            reject("file_inspection_limit")
        with path.open("rb") as handle:
            payload = handle.read(max_bytes + 1)
    except OSError:
        raise
    if len(payload) > max_bytes:
        reject("file_inspection_limit")
    return payload


def contains_forbidden(
    payload: bytes | str,
    forbidden: tuple[str, ...],
    *,
    wildcard_budget: _WorkBudget | None = None,
) -> bool:
    if not payload:
        return False
    if wildcard_budget is None:
        wildcard_budget = _WorkBudget(
            MAX_WILDCARD_MATCH_WORK, "wildcard_match_limit"
        )

    def disclosure_normalize(value: str) -> str:
        def strip_ignorable_characters(text: str) -> str:
            return "".join(
                character
                for character in text
                if unicodedata.category(character) != "Cf"
                and not unicodedata.category(character).startswith("M")
            )

        compatibility = unicodedata.normalize("NFKD", value).translate(
            CONFUSABLES
        )
        separated = "".join(
            "-" if unicodedata.category(character)[0] in {"P", "Z"}
            else UNAUDITED_SCRIPT_MARKER
            if ord(character) > 127
            and unicodedata.category(character).startswith("L")
            else character
            for character in compatibility
        )
        stripped = strip_ignorable_characters(separated).casefold()
        return re.sub(r"-+", "-", stripped)

    needles = tuple(
        disclosure_normalize(value) for value in forbidden
    )

    def contains_with_markers(text: str, needle: str) -> bool:
        if not needle or len(text) < len(needle):
            return False
        wildcard_budget.consume(len(text))
        masks: dict[str, int] = {}
        for index, character in enumerate(needle):
            masks[character] = masks.get(character, 0) | (1 << index)
        all_positions = (1 << len(needle)) - 1
        terminal = 1 << (len(needle) - 1)
        state = 0
        unaudited = 0
        unaudited_limit = max(1, len(needle) // 4)
        for index, character in enumerate(text):
            if character == UNAUDITED_SCRIPT_MARKER:
                character_mask = all_positions
                unaudited += 1
            else:
                character_mask = masks.get(character, 0)
            if (
                index >= len(needle)
                and text[index - len(needle)] == UNAUDITED_SCRIPT_MARKER
            ):
                unaudited -= 1
            state = ((state << 1) | 1) & character_mask
            if (
                index >= len(needle) - 1
                and state & terminal
                and 0 < unaudited <= unaudited_limit
            ):
                return True
        return False

    def contains(text: str, *, allow_wildcards: bool = True) -> bool:
        normalized = disclosure_normalize(text)
        for needle in needles:
            if needle in normalized:
                return True
            if (
                not allow_wildcards
                or UNAUDITED_SCRIPT_MARKER not in normalized
            ):
                continue
            if contains_with_markers(normalized, needle):
                return True
        return False

    if isinstance(payload, str):
        return contains(payload)

    strict_decode_succeeded = False
    for encoding, width in (
        ("utf-8", 1),
        ("utf-16-le", 2),
        ("utf-16-be", 2),
        ("utf-32-le", 4),
        ("utf-32-be", 4),
    ):
        for offset in range(width):
            candidate = payload[offset:]
            if not candidate:
                continue
            try:
                text = candidate.decode(encoding, errors="strict")
                if offset == 0:
                    strict_decode_succeeded = True
            except (UnicodeDecodeError, UnicodeError):
                text = candidate.decode(encoding, errors="ignore")
            plausible_multibyte_text = (
                encoding == "utf-8"
                or candidate.count(b"\0") * 4 >= len(candidate)
            )
            if contains(
                text, allow_wildcards=plausible_multibyte_text
            ):
                return True
    return not strict_decode_succeeded


def validate_encoding_adversaries() -> None:
    forbidden = ("private-control-plane-marker",)
    if not contains_forbidden(forbidden[0].encode("utf-16"), forbidden):
        reject("utf16_publication_disclosure_bypass")
    if not contains_forbidden(
        "prívate-control-plane-marker", forbidden
    ):
        reject("precomposed_accent_publication_disclosure_bypass")
    if not contains_forbidden(b"\xff\xfe\xff", forbidden):
        reject("undecodable_publication_disclosure_bypass")
    format_obscured = "\u200b".join(forbidden[0])
    if not contains_forbidden(format_obscured, forbidden):
        reject("unicode_format_publication_disclosure_bypass")
    forbidden_with_format = ("private\u2060-control-plane-marker",)
    if not contains_forbidden(forbidden[0], forbidden_with_format):
        reject("unicode_format_forbidden_literal_normalization_bypass")
    if not contains_forbidden("prіvate-control-plane-marker", forbidden):
        reject("unicode_homoglyph_publication_disclosure_bypass")
    if not contains_forbidden("privaтe-control-plane-marker", forbidden):
        reject("unicode_unaudited_script_publication_disclosure_bypass")
    if not contains_forbidden("private\u2011control\uff0dplane-marker", forbidden):
        reject("unicode_separator_publication_disclosure_bypass")
    if not contains_forbidden(
        "private\u2011\u2003\uff0dcontrol-plane-marker", forbidden
    ):
        reject("unicode_separator_run_publication_disclosure_bypass")
    mark_obscured = "\u0301".join(forbidden[0])
    if not contains_forbidden(mark_obscured, forbidden):
        reject("unicode_mark_publication_disclosure_bypass")
    forbidden_with_mark = ("private\u20dd-control-plane-marker",)
    if not contains_forbidden(forbidden[0], forbidden_with_mark):
        reject("unicode_mark_forbidden_literal_normalization_bypass")
    for encoding in ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):
        mixed = b"\xff" + forbidden[0].encode(encoding)
        if not contains_forbidden(mixed, forbidden):
            reject(f"mixed_invalid_prefix_{encoding}_publication_disclosure_bypass")
    try:
        contains_forbidden(
            "privaтe-control-plane-marker",
            forbidden,
            wildcard_budget=_WorkBudget(1, "wildcard_match_limit"),
        )
    except ValidationError as error:
        if error.code != "wildcard_match_limit":
            reject("wildcard_match_wrong_rejection")
    else:
        reject("wildcard_match_limit_bypass")


def validate_publication_range(
    repo_root: Path,
    publication_base: str,
    forbidden: tuple[str, ...],
    *,
    inspection_budget: _WorkBudget | None = None,
    wildcard_budget: _WorkBudget | None = None,
) -> None:
    if inspection_budget is None:
        inspection_budget = _WorkBudget(
            MAX_PUBLICATION_SCAN_BYTES, "publication_scan_limit"
        )
    if wildcard_budget is None:
        wildcard_budget = _WorkBudget(
            MAX_WILDCARD_MATCH_WORK, "wildcard_match_limit"
        )
    base_sha = git(
        repo_root,
        "rev-parse",
        "--verify",
        f"{publication_base}^{{commit}}",
        inspection_budget=inspection_budget,
    ).strip()
    head_sha = git(
        repo_root,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
        inspection_budget=inspection_budget,
    ).strip()
    if base_sha == head_sha:
        reject("publication_base_equals_head")
    ancestry = subprocess.run(
        [
            "git", "--no-replace-objects", "-C", str(repo_root),
            "merge-base", "--is-ancestor",
            base_sha, head_sha,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if ancestry.returncode == 1:
        reject("publication_base_not_ancestor")
    if ancestry.returncode != 0:
        reject("git_inspection")
    commits = git(
        repo_root,
        "rev-list",
        "--reverse",
        f"{base_sha}..{head_sha}",
        inspection_budget=inspection_budget,
    ).splitlines()
    if len(commits) > MAX_PUBLICATION_COMMITS:
        reject("publication_scan_limit")
    scanned_blobs: set[str] = set()
    diff_records = 0
    for commit in commits:
        commit_object = git_object(
            repo_root,
            "commit",
            commit,
            max_bytes=MAX_METADATA_OBJECT_BYTES,
            inspection_budget=inspection_budget,
        )
        if contains_forbidden(
            commit_object, forbidden, wildcard_budget=wildcard_budget
        ):
            reject("publication_range_disclosure")
        changed = git(
            repo_root,
            "diff-tree",
            "--root",
            "-r",
            "-m",
            "--no-commit-id",
            "--raw",
            "-z",
            "--no-renames",
            "--diff-filter=AMT",
            commit,
            text=False,
            inspection_budget=inspection_budget,
        ).split(b"\0")
        if changed and changed[-1] == b"":
            changed.pop()
        if len(changed) % 2:
            reject("git_inspection")
        for index in range(0, len(changed), 2):
            diff_records += 1
            if diff_records > MAX_PUBLICATION_DIFF_RECORDS:
                reject("publication_scan_limit")
            metadata, raw_path = changed[index], changed[index + 1]
            try:
                old_mode, new_mode, _old_id, object_id, status = (
                    metadata.decode("ascii").split()
                )
            except (UnicodeDecodeError, ValueError):
                reject("git_inspection")
            if (
                not old_mode.startswith(":")
                or status not in {"A", "M", "T"}
                or new_mode not in {"100644", "100755", "120000", "160000"}
                or re.fullmatch(r"[0-9a-f]{40,64}", object_id) is None
            ):
                reject("git_inspection")
            if contains_forbidden(
                raw_path, forbidden, wildcard_budget=wildcard_budget
            ):
                reject("publication_range_disclosure")
            if new_mode == "160000" or object_id in scanned_blobs:
                continue
            scanned_blobs.add(object_id)
            blob = git_object(
                repo_root,
                "blob",
                object_id,
                max_bytes=MAX_GIT_OBJECT_BYTES,
                inspection_budget=inspection_budget,
            )
            if contains_forbidden(
                blob, forbidden, wildcard_budget=wildcard_budget
            ):
                reject("publication_range_disclosure")

    staged_paths = set(
        git(
            repo_root,
            "diff",
            "--cached",
            "--name-only",
            "--diff-filter=ACMRT",
            "-z",
            head_sha,
            text=False,
            inspection_budget=inspection_budget,
        ).split(b"\0")
    )
    staged_paths.discard(b"")
    index = git(
        repo_root,
        "ls-files",
        "--stage",
        "-z",
        text=False,
        inspection_budget=inspection_budget,
    )
    for record in index.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        if stage != "0":
            reject("working_tree_unmerged_index")
        if raw_path not in staged_paths:
            continue
        if contains_forbidden(
            raw_path, forbidden, wildcard_budget=wildcard_budget
        ):
            reject("working_tree_disclosure")
        if mode != "160000":
            blob = git_object(
                repo_root,
                "blob",
                object_id,
                max_bytes=MAX_GIT_OBJECT_BYTES,
                inspection_budget=inspection_budget,
            )
            if contains_forbidden(
                blob, forbidden, wildcard_budget=wildcard_budget
            ):
                reject("working_tree_disclosure")

    worktree_paths = set(
        git(
            repo_root,
            "diff",
            "--name-only",
            "--diff-filter=ACMRT",
            "-z",
            head_sha,
            text=False,
            inspection_budget=inspection_budget,
        ).split(b"\0")
    )
    worktree_paths.update(
        git(
            repo_root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            text=False,
            inspection_budget=inspection_budget,
        ).split(b"\0")
    )
    worktree_paths.discard(b"")
    for raw_path in worktree_paths:
        if contains_forbidden(
            raw_path, forbidden, wildcard_budget=wildcard_budget
        ):
            reject("working_tree_disclosure")
        path = raw_path.decode(sys.getfilesystemencoding(), errors="surrogateescape")
        candidate = repo_root / path
        if candidate.is_symlink():
            if contains_forbidden(
                str(candidate.readlink()),
                forbidden,
                wildcard_budget=wildcard_budget,
            ):
                reject("working_tree_disclosure")
        elif candidate.is_file():
            payload = bounded_file_bytes(candidate)
            inspection_budget.consume(len(payload))
            if contains_forbidden(
                payload,
                forbidden,
                wildcard_budget=wildcard_budget,
            ):
                reject("working_tree_disclosure")


def initialize_adversary_repo(root: str) -> tuple[Path, str]:
    repo = Path(root)
    subprocess.run(
        ["git", "init", "--quiet", str(repo)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Synthetic Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    empty_hooks = repo / ".git" / "hindsight-empty-hooks"
    empty_hooks.mkdir(mode=0o700)
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "core.hooksPath", ".git/hindsight-empty-hooks"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "commit.gpgSign", "false"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "tag.gpgSign", "false"],
        check=True,
    )
    (repo / "base.txt").write_text("public baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--", "base.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--quiet", "-m", "base"],
        check=True,
    )
    base = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    (repo / "range-anchor.txt").write_text(
        "public range anchor\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", "range-anchor.txt"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--quiet", "-m", "range anchor"],
        check=True,
    )
    return repo, base


def validate_age_suffix_adversaries() -> None:
    forbidden = ("synthetic-private-marker",)
    missed: list[str] = []
    for case in ("committed", "worktree"):
        with tempfile.TemporaryDirectory(prefix="hindsight-age-adversary-") as root:
            repo, base = initialize_adversary_repo(root)
            (repo / "disguised.age").write_text(
                f"{forbidden[0]}\n", encoding="utf-8"
            )
            expected_code = "working_tree_disclosure"
            if case == "committed":
                subprocess.run(
                    ["git", "-C", str(repo), "add", "--", "disguised.age"],
                    check=True,
                )
                subprocess.run(
                    ["git", "-C", str(repo), "commit", "--quiet", "-m", "leak"],
                    check=True,
                )
                expected_code = "publication_range_disclosure"
            try:
                validate_publication_range(repo, base, forbidden)
            except ValidationError as error:
                if error.code != expected_code:
                    reject("age_suffix_wrong_rejection")
            else:
                missed.append(case)
    if missed:
        reject("age_suffix_plaintext_bypass")


def validate_publication_metadata_adversaries() -> None:
    forbidden = ("synthetic-private-marker",)
    with tempfile.TemporaryDirectory(prefix="hindsight-metadata-adversary-") as root:
        repo, base = initialize_adversary_repo(root)
        (repo / "change.txt").write_text("public change\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", "change.txt"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--quiet", "-m", forbidden[0]],
            check=True,
        )
        try:
            validate_publication_range(repo, base, forbidden)
        except ValidationError as error:
            if error.code != "publication_range_disclosure":
                reject("metadata_wrong_rejection")
        else:
            reject("commit_metadata_disclosure_bypass")

    with tempfile.TemporaryDirectory(prefix="hindsight-path-adversary-") as root:
        repo, base = initialize_adversary_repo(root)
        (repo / f"{forbidden[0]}.txt").write_text("public change\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", f"{forbidden[0]}.txt"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--quiet", "-m", "public change"],
            check=True,
        )
        try:
            validate_publication_range(repo, base, forbidden)
        except ValidationError as error:
            if error.code != "publication_range_disclosure":
                reject("metadata_wrong_rejection")
        else:
            reject("tree_path_disclosure_bypass")


def validate_restored_blob_adversary() -> None:
    forbidden = ("synthetic-private-marker",)
    with tempfile.TemporaryDirectory(prefix="hindsight-restored-blob-adversary-") as root:
        repo, _initial = initialize_adversary_repo(root)
        restored = repo / "restored.txt"
        restored.write_text(f"{forbidden[0]}\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", restored.name], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--quiet", "-m", "historical blob"],
            check=True,
        )
        historical = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        restored.unlink()
        subprocess.run(
            ["git", "-C", str(repo), "add", "--update", "--", restored.name],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--quiet", "-m", "publication base"],
            check=True,
        )
        base = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            ["git", "-C", str(repo), "checkout", historical, "--", restored.name],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--quiet", "-m", "restore blob"],
            check=True,
        )
        try:
            validate_publication_range(repo, base, forbidden)
        except ValidationError as error:
            if error.code != "publication_range_disclosure":
                reject("restored_blob_wrong_rejection")
        else:
            reject("restored_blob_disclosure_bypass")


def validate_worktree_path_adversaries() -> None:
    forbidden = ("synthetic-private-marker",)
    with tempfile.TemporaryDirectory(prefix="hindsight-worktree-path-adversary-") as root:
        repo, base = initialize_adversary_repo(root)
        (repo / "leak\npart.age").write_text(f"{forbidden[0]}\n", encoding="utf-8")
        try:
            validate_publication_range(repo, base, forbidden)
        except ValidationError as error:
            if error.code != "working_tree_disclosure":
                reject("worktree_path_wrong_rejection")
        else:
            reject("worktree_path_disclosure_bypass")

    with tempfile.TemporaryDirectory(prefix="hindsight-worktree-link-adversary-") as root:
        repo, base = initialize_adversary_repo(root)
        (repo / "disguised-link").symlink_to(forbidden[0])
        try:
            validate_publication_range(repo, base, forbidden)
        except ValidationError as error:
            if error.code != "working_tree_disclosure":
                reject("worktree_path_wrong_rejection")
        else:
            reject("worktree_symlink_disclosure_bypass")

    for kind in ("symlink", "regular"):
        with tempfile.TemporaryDirectory(
            prefix=f"hindsight-index-{kind}-adversary-"
        ) as root:
            repo, base = initialize_adversary_repo(root)
            candidate = repo / "disguised-payload"
            if kind == "symlink":
                candidate.symlink_to(forbidden[0])
            else:
                candidate.write_text(f"{forbidden[0]}\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo), "add", "--", "disguised-payload"],
                check=True,
            )
            candidate.unlink()
            candidate.write_text("public replacement\n", encoding="utf-8")
            try:
                validate_publication_range(repo, base, forbidden)
            except ValidationError as error:
                if error.code != "working_tree_disclosure":
                    reject("worktree_path_wrong_rejection")
            else:
                reject("index_payload_disclosure_bypass")

    with tempfile.TemporaryDirectory(
        prefix="hindsight-unmerged-index-adversary-"
    ) as root:
        repo, base = initialize_adversary_repo(root)
        object_id = subprocess.check_output(
            ["git", "-C", str(repo), "hash-object", "-w", "base.txt"],
            text=True,
        ).strip()
        subprocess.run(
            ["git", "-C", str(repo), "update-index", "--index-info"],
            input=f"100644 {object_id} 1\tconflicted.txt\n",
            text=True,
            check=True,
        )
        try:
            validate_publication_range(repo, base, forbidden)
        except ValidationError as error:
            if error.code != "working_tree_unmerged_index":
                reject("unmerged_index_wrong_rejection")
        else:
            reject("unmerged_index_bypass")


def validate_publication_ancestry_adversary() -> None:
    forbidden = ("synthetic-private-marker",)
    with tempfile.TemporaryDirectory(prefix="hindsight-ancestry-adversary-") as root:
        repo, base = initialize_adversary_repo(root)
        (repo / "future.txt").write_text("public future\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", "future.txt"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--quiet", "-m", "future"],
            check=True,
        )
        future = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "--quiet", "--detach", base],
            check=True,
        )
        try:
            validate_publication_range(repo, future, forbidden)
        except ValidationError as error:
            if error.code != "publication_base_not_ancestor":
                reject("publication_ancestry_wrong_rejection")
        else:
            reject("publication_ancestry_bypass")


def validate_empty_publication_range_adversary() -> None:
    forbidden = ("synthetic-private-marker",)
    with tempfile.TemporaryDirectory(
        prefix="hindsight-empty-range-adversary-"
    ) as root:
        repo, _base = initialize_adversary_repo(root)
        head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        try:
            validate_publication_range(repo, head, forbidden)
        except ValidationError as error:
            if error.code != "publication_base_equals_head":
                reject("empty_publication_range_wrong_rejection")
        else:
            reject("empty_publication_range_bypass")


def validate_publication_scan_budget_adversary() -> None:
    forbidden = ("synthetic-private-marker",)
    with tempfile.TemporaryDirectory(
        prefix="hindsight-scan-budget-adversary-"
    ) as root:
        repo, base = initialize_adversary_repo(root)
        try:
            validate_publication_range(
                repo,
                base,
                forbidden,
                inspection_budget=_WorkBudget(
                    1, "publication_scan_limit"
                ),
            )
        except ValidationError as error:
            if error.code != "publication_scan_limit":
                reject("publication_scan_budget_wrong_rejection")
        else:
            reject("publication_scan_budget_bypass")


def validate_replace_object_adversary() -> None:
    forbidden = ("synthetic-private-marker",)
    with tempfile.TemporaryDirectory(
        prefix="hindsight-replace-object-adversary-"
    ) as root:
        repo, base = initialize_adversary_repo(root)
        candidate = repo / "candidate.txt"
        candidate.write_text(forbidden[0] + "\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", candidate.name],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--quiet", "-m", "candidate"],
            check=True,
        )
        forbidden_head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        base_tree = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", f"{base}^{{tree}}"],
            text=True,
        ).strip()
        clean_commit = subprocess.check_output(
            [
                "git", "-C", str(repo), "commit-tree", base_tree,
                "-p", base,
            ],
            input="public replacement\n",
            text=True,
        ).strip()
        subprocess.run(
            ["git", "-C", str(repo), "replace", forbidden_head, clean_commit],
            check=True,
        )
        candidate.write_text("public worktree replacement\n", encoding="utf-8")
        try:
            validate_publication_range(repo, base, forbidden)
        except ValidationError as error:
            if error.code != "publication_range_disclosure":
                reject("replace_object_wrong_rejection")
        else:
            reject("replace_object_disclosure_bypass")


def main() -> int:
    if len(sys.argv) != 5:
        print("private hindsight memory control plane PRD: invalid invocation", file=sys.stderr)
        return 2
    catalog_path = Path(sys.argv[1])
    prd_path = Path(sys.argv[2])
    repo_root = Path(sys.argv[3])
    publication_base = sys.argv[4]
    try:
        validate_synthetic_migration_cases()
        validate_age_suffix_adversaries()
        validate_publication_metadata_adversaries()
        validate_restored_blob_adversary()
        validate_worktree_path_adversaries()
        validate_publication_ancestry_adversary()
        validate_empty_publication_range_adversary()
        validate_publication_scan_budget_adversary()
        validate_replace_object_adversary()
        validate_encoding_adversaries()
    except ValidationError as error:
        print(
            "private hindsight memory control plane PRD: "
            f"validation failed ({error.code})",
            file=sys.stderr,
        )
        return 1
    except (OSError, subprocess.SubprocessError) as error:
        print(
            "private hindsight memory control plane PRD: "
            f"self-test harness failure ({error})",
            file=sys.stderr,
        )
        return 1
    try:
        catalog = tomllib.loads(bounded_file_bytes(catalog_path).decode("utf-8"))
        validated = validate_catalog(catalog)
        wildcard_budget = _WorkBudget(
            MAX_WILDCARD_MATCH_WORK, "wildcard_match_limit"
        )
        if contains_forbidden(
            bounded_file_bytes(prd_path),
            validated.forbidden_literals,
            wildcard_budget=wildcard_budget,
        ):
            reject("public_prd_disclosure")
        validate_publication_range(
            repo_root,
            publication_base,
            validated.forbidden_literals,
            wildcard_budget=wildcard_budget,
        )
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        print("private hindsight memory control plane PRD: catalog I/O failure", file=sys.stderr)
        return 1
    except ValidationError as error:
        print(
            f"private hindsight memory control plane PRD: validation failed ({error.code})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
