"""Immutable, content-addressed review-input manifests."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .git_observer import GitObservationError, observe_git_diff


VERSION = 3
PR_NUMBER_TOKEN = "__PUBLISHING_REVIEWABLE_PRS_PR_NUMBER__"
OID_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ReviewInputError(ValueError):
    """A review input is incomplete, malformed, or no longer bound."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewInputError(f"review input {name} must be an object")
    return value


def _exact_keys(value: dict[str, Any], keys: set[str], name: str) -> None:
    if set(value) != keys:
        raise ReviewInputError(f"review input {name} has unsupported or missing fields")


def _string(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        emptiness = "possibly empty" if allow_empty else "non-empty"
        raise ReviewInputError(f"review input {name} must be a {emptiness} string")
    return value


def _sha(value: Any, name: str) -> str:
    value = _string(value, name)
    if not SHA256_RE.fullmatch(value):
        raise ReviewInputError(f"review input {name} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True)
class ReviewInput:
    raw: dict[str, Any]
    content_sha256: str

    @property
    def repository(self) -> str:
        return self.raw["repository"]

    @property
    def pr_number(self) -> int | str:
        return self.raw["pr_number"]

    @property
    def candidate(self) -> dict[str, Any]:
        return self.raw["candidate"]

    @property
    def baseline(self) -> dict[str, Any]:
        return self.raw["baseline"]


def parse_review_input(value: Any) -> ReviewInput:  # noqa: C901
    raw = _object(value, "manifest")
    _exact_keys(
        raw,
        {
            "version",
            "content_sha256",
            "repository",
            "pr_number",
            "base",
            "head",
            "candidate",
            "git_diff",
            "diff",
            "stack",
            "baseline",
        },
        "manifest",
    )
    if type(raw["version"]) is not int or raw["version"] != VERSION:
        raise ReviewInputError(f"review input version must be {VERSION}")
    supplied = _sha(raw["content_sha256"], "content_sha256")
    unsigned = copy.deepcopy(raw)
    del unsigned["content_sha256"]
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != supplied:
        raise ReviewInputError(
            "review input content_sha256 does not match canonical content"
        )
    repository = _string(raw["repository"], "repository")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        raise ReviewInputError("review input repository must use OWNER/REPO")
    if (
        not (type(raw["pr_number"]) is int and raw["pr_number"] > 0)
        and raw["pr_number"] != PR_NUMBER_TOKEN
    ):
        raise ReviewInputError(
            "review input pr_number must be positive or the new-PR token"
        )
    base = _object(raw["base"], "base")
    _exact_keys(base, {"ref", "oid"}, "base")
    head = _object(raw["head"], "head")
    _exact_keys(head, {"ref", "oid", "owner", "repository"}, "head")
    for name, part in (("base", base), ("head", head)):
        _string(part["ref"], f"{name}.ref")
        if not OID_RE.fullmatch(_string(part["oid"], f"{name}.oid")):
            raise ReviewInputError(f"review input {name}.oid must be a 40-hex OID")
    owner = _string(head["owner"], "head.owner")
    head_repository = _string(head["repository"], "head.repository")
    if head["ref"].split(":", 1)[0] != owner or ":" not in head["ref"]:
        raise ReviewInputError("review input head ref/owner do not match")
    if (
        not re.fullmatch(r"[^/\s]+/[^/\s]+", head_repository)
        or head_repository.split("/", 1)[0] != owner
    ):
        raise ReviewInputError(
            "review input head repository must use the exact OWNER/REPO identity"
        )
    candidate = _object(raw["candidate"], "candidate")
    _exact_keys(candidate, {"title", "body_sha256"}, "candidate")
    _string(candidate["title"], "candidate.title")
    _sha(candidate["body_sha256"], "candidate.body_sha256")
    git_diff = raw["git_diff"]
    if not isinstance(git_diff, list) or not git_diff:
        raise ReviewInputError("review input git_diff must contain observed records")
    git_targets: set[str] = set()
    for index, row in enumerate(git_diff):
        row = _object(row, f"git_diff[{index}]")
        _exact_keys(
            row,
            {
                "source_path",
                "target_path",
                "operation",
                "additions",
                "deletions",
                "binary",
            },
            f"git_diff[{index}]",
        )
        if row["operation"] not in {
            "added",
            "modified",
            "deleted",
            "renamed",
            "copied",
            "type-changed",
        }:
            raise ReviewInputError(
                f"review input git_diff[{index}] operation is invalid"
            )
        if not isinstance(row["target_path"], str) or not row["target_path"]:
            raise ReviewInputError(
                f"review input git_diff[{index}] target path is invalid"
            )
        if row["target_path"] in git_targets:
            raise ReviewInputError("review input git_diff target paths must be unique")
        git_targets.add(row["target_path"])
        needs_source = row["operation"] in {"renamed", "copied"}
        if needs_source != isinstance(row["source_path"], str):
            raise ReviewInputError(
                f"review input git_diff[{index}] source path is invalid"
            )
        if not isinstance(row["binary"], bool):
            raise ReviewInputError(
                f"review input git_diff[{index}] binary flag is invalid"
            )
        if row["binary"]:
            if row["additions"] is not None or row["deletions"] is not None:
                raise ReviewInputError(
                    f"review input git_diff[{index}] binary metrics are invalid"
                )
        elif not all(
            type(row[key]) is int and row[key] >= 0
            for key in ("additions", "deletions")
        ):
            raise ReviewInputError(
                f"review input git_diff[{index}] metrics are invalid"
            )
    if git_diff != sorted(
        git_diff, key=lambda row: (row["target_path"], row["source_path"] or "")
    ):
        raise ReviewInputError(
            "review input git_diff must use deterministic path order"
        )
    if not isinstance(raw["diff"], list) or not raw["diff"]:
        raise ReviewInputError("review input diff must contain parsed records")
    category_targets: set[tuple[str, str]] = set()
    target_semantics: dict[str, tuple[str, str | None]] = {}
    for index, row in enumerate(raw["diff"]):
        row = _object(row, f"diff[{index}]")
        _exact_keys(
            row,
            {
                "category",
                "operation",
                "source_path",
                "target_path",
                "additions",
                "deletions",
            },
            f"diff[{index}]",
        )
        if row["category"] not in {"IMPL", "TEST", "DOC", "GEN", "OTHER"} or row[
            "operation"
        ] not in {"ATOMIC", "BINARY", "MOVED", "COPIED"}:
            raise ReviewInputError(
                f"review input diff[{index}] has invalid category or operation"
            )
        for key in ("source_path", "target_path"):
            if row[key] is not None and not isinstance(row[key], str):
                raise ReviewInputError(
                    f"review input diff[{index}].{key} must be string or null"
                )
            if isinstance(row[key], str) and ("\r" in row[key] or "\n" in row[key]):
                raise ReviewInputError("unsupported diff path contains CR or LF")
        if not isinstance(row["target_path"], str) or not row["target_path"]:
            raise ReviewInputError(
                f"review input diff[{index}].target_path must be non-empty"
            )
        category_target = (row["category"], row["target_path"])
        if category_target in category_targets:
            raise ReviewInputError(
                "review input diff target paths must be unique within a category"
            )
        category_targets.add(category_target)
        if row["operation"] in {"MOVED", "COPIED"} and not isinstance(
            row["source_path"], str
        ):
            raise ReviewInputError(
                f"review input diff[{index}] operation needs source_path"
            )
        if (
            row["operation"] not in {"MOVED", "COPIED"}
            and row["source_path"] is not None
        ):
            raise ReviewInputError(
                f"review input diff[{index}] has an unexpected source_path"
            )
        if (
            row["operation"] in {"MOVED", "COPIED"}
            and row["source_path"] == row["target_path"]
        ):
            raise ReviewInputError(
                f"review input diff[{index}] source and target paths must differ"
            )
        semantics = (row["operation"], row["source_path"])
        previous_semantics = target_semantics.get(row["target_path"])
        if previous_semantics is not None and previous_semantics != semantics:
            raise ReviewInputError(
                f"review input diff[{index}] must reuse one operation and source "
                "path across categories"
            )
        target_semantics[row["target_path"]] = semantics
        if not all(
            type(row[key]) is int and row[key] >= 0
            for key in ("additions", "deletions")
        ):
            raise ReviewInputError(
                f"review input diff[{index}] metrics must be non-negative integers"
            )
    raw_by_target = {row["target_path"]: row for row in git_diff}
    presented_targets = {row["target_path"] for row in raw["diff"]}
    if presented_targets != set(raw_by_target):
        raise ReviewInputError("review input categorized diff omits or adds Git paths")
    for target_path, git_row in raw_by_target.items():
        presented = [row for row in raw["diff"] if row["target_path"] == target_path]
        expected_operation = (
            "MOVED"
            if git_row["operation"] == "renamed"
            else "COPIED"
            if git_row["operation"] == "copied"
            else "BINARY"
            if git_row["binary"]
            else "ATOMIC"
        )
        if any(
            row["operation"] != expected_operation
            or row["source_path"] != git_row["source_path"]
            for row in presented
        ):
            raise ReviewInputError("review input categorized diff operation drift")
        if git_row["binary"]:
            if any(row["additions"] != 0 or row["deletions"] != 0 for row in presented):
                raise ReviewInputError("review input categorized binary metrics drift")
        elif (
            sum(row["additions"] for row in presented) != git_row["additions"]
            or sum(row["deletions"] for row in presented) != git_row["deletions"]
        ):
            raise ReviewInputError("review input categorized diff metrics drift")
    if not isinstance(raw["stack"], list):
        raise ReviewInputError("review input stack must be a list")
    for index, item in enumerate(raw["stack"]):
        item = _object(item, f"stack[{index}]")
        _exact_keys(
            item,
            {"number", "title", "url", "current", "metrics", "file_operations"},
            f"stack[{index}]",
        )
        if (
            type(item["number"]) is not int
            or item["number"] < 1
            or not isinstance(item["current"], bool)
        ):
            raise ReviewInputError(f"review input stack[{index}] has invalid identity")
        _string(item["title"], f"stack[{index}].title")
        _string(item["url"], f"stack[{index}].url")
        if not isinstance(item["metrics"], dict) or not isinstance(
            item["file_operations"], dict
        ):
            raise ReviewInputError(
                f"review input stack[{index}] metrics must be objects"
            )
    baseline = _object(raw["baseline"], "baseline")
    _exact_keys(
        baseline, {"mode", "title_sha256", "body_sha256", "fragments"}, "baseline"
    )
    if baseline["mode"] not in {"new", "existing"}:
        raise ReviewInputError("review input baseline.mode must be new or existing")
    if baseline["mode"] == "existing" and raw["pr_number"] == PR_NUMBER_TOKEN:
        raise ReviewInputError(
            "existing-PR review input cannot use the new-PR number token"
        )
    if baseline["mode"] == "new" and raw["pr_number"] != PR_NUMBER_TOKEN:
        raise ReviewInputError("new-PR review input requires the new-PR number token")
    if baseline["mode"] == "new":
        if (
            baseline["title_sha256"] is not None
            or baseline["body_sha256"] is not None
            or baseline["fragments"] != []
        ):
            raise ReviewInputError("new-PR review input cannot claim a prior baseline")
    else:
        _sha(baseline["title_sha256"], "baseline.title_sha256")
        _sha(baseline["body_sha256"], "baseline.body_sha256")
        if not isinstance(baseline["fragments"], list) or not baseline["fragments"]:
            raise ReviewInputError(
                "existing-PR review input must account for baseline fragments"
            )
        seen: set[str] = set()
        for index, fragment in enumerate(baseline["fragments"]):
            fragment = _object(fragment, f"baseline.fragments[{index}]")
            _exact_keys(
                fragment,
                {"id", "text", "sha256", "disposition", "replacement", "reason"},
                f"baseline.fragments[{index}]",
            )
            identifier = _string(fragment["id"], f"baseline.fragments[{index}].id")
            if identifier in seen:
                raise ReviewInputError("baseline fragment IDs must be unique")
            seen.add(identifier)
            text = _string(fragment["text"], f"baseline.fragments[{index}].text")
            if digest(text) != _sha(
                fragment["sha256"], f"baseline.fragments[{index}].sha256"
            ):
                raise ReviewInputError("baseline fragment digest does not match text")
            if fragment["disposition"] not in {"retain", "replace", "remove"}:
                raise ReviewInputError(
                    "baseline fragment needs retain, replace, or remove disposition"
                )
            replacement = fragment["replacement"]
            reason = fragment["reason"]
            if fragment["disposition"] == "retain" and (
                replacement is not None or reason is not None
            ):
                raise ReviewInputError(
                    "retained fragment cannot carry replacement or reason"
                )
            if fragment["disposition"] == "replace" and (
                not isinstance(replacement, str)
                or not replacement
                or not isinstance(reason, str)
                or not reason
            ):
                raise ReviewInputError(
                    "replaced fragment needs explicit replacement and reason"
                )
            if fragment["disposition"] == "remove" and (
                replacement is not None or not isinstance(reason, str) or not reason
            ):
                raise ReviewInputError("removed fragment needs an explicit reason")
    return ReviewInput(raw, supplied)


def load_review_input(path: Path) -> ReviewInput:
    if not path.is_absolute():
        raise ReviewInputError("review input path must be absolute")
    try:

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise ReviewInputError(
                        f"review input contains duplicate key: {key}"
                    )
                result[key] = item
            return result

        def reject_constant(value: str) -> None:
            raise ReviewInputError(
                f"review input contains non-finite JSON value: {value}"
            )

        return parse_review_input(
            json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
            )
        )
    except OSError as error:
        raise ReviewInputError(f"cannot read review input: {error}") from error
    except json.JSONDecodeError as error:
        raise ReviewInputError("review input is not JSON") from error


def bind_review_input(  # noqa: C901
    review_input: ReviewInput,
    *,
    repository: str,
    pr_number: int,
    base: str,
    base_oid: str,
    head: str,
    head_oid: str,
    head_owner: str,
    head_repository: str,
    title: str,
    body: str,
    stored_title: str | None = None,
    stored_body: str | None = None,
    template_body: str | None = None,
    git_repository: Path | None = None,
) -> None:
    raw = review_input.raw
    if (
        raw["repository"],
        raw["base"]["ref"],
        raw["base"]["oid"],
        raw["head"]["ref"],
        raw["head"]["oid"],
        raw["head"]["owner"],
        raw["head"]["repository"],
    ) != (
        repository,
        base,
        base_oid,
        head,
        head_oid,
        head_owner,
        head_repository,
    ):
        raise ReviewInputError(
            "review input repository or pushed base/head identity drifted"
        )
    if raw["pr_number"] not in {pr_number, PR_NUMBER_TOKEN}:
        raise ReviewInputError("review input PR number drifted")
    if raw["candidate"]["title"] != title:
        raise ReviewInputError("review input candidate title drifted")
    if git_repository is not None:
        try:
            observed = observe_git_diff(
                git_repository,
                base_oid=base_oid,
                head_oid=head_oid,
            )
        except GitObservationError as error:
            raise ReviewInputError(
                f"exact pushed Git diff is unavailable: {error}"
            ) from error
        if observed != raw["git_diff"]:
            raise ReviewInputError(
                "review input Git inventory differs from exact pushed diff"
            )
    if raw["pr_number"] == PR_NUMBER_TOKEN:
        if template_body is None:
            raise ReviewInputError(
                "new-PR review input requires the original token-bearing template"
            )
        if template_body.count(PR_NUMBER_TOKEN) < 1:
            raise ReviewInputError("new-PR template has no PR-number token")
        if body != template_body.replace(PR_NUMBER_TOKEN, str(pr_number)):
            raise ReviewInputError(
                "rendered new-PR body does not exactly derive from its template"
            )
        digest_body = template_body
    else:
        digest_body = body
    if digest(digest_body) != raw["candidate"]["body_sha256"]:
        raise ReviewInputError("review input candidate body drifted")
    baseline = raw["baseline"]
    if baseline["mode"] == "existing":
        if (stored_title is None) != (stored_body is None):
            raise ReviewInputError(
                "existing-PR review input requires both live baseline title/body"
            )
        if stored_title is not None and stored_body is not None:
            if (
                digest(stored_title) != baseline["title_sha256"]
                or digest(stored_body) != baseline["body_sha256"]
            ):
                raise ReviewInputError("review input live baseline drifted")
            source_body = "".join(
                fragment["text"] for fragment in baseline["fragments"]
            )
            if source_body != stored_body:
                raise ReviewInputError(
                    "existing-PR baseline fragments must exhaustively partition "
                    "the stored body"
                )
            candidate_parts: list[str] = []
            for fragment in baseline["fragments"]:
                disposition = fragment["disposition"]
                if disposition == "retain":
                    candidate_parts.append(fragment["text"])
                elif disposition == "replace":
                    candidate_parts.append(fragment["replacement"])
                else:
                    candidate_parts.append("")
            if "".join(candidate_parts) != body:
                raise ReviewInputError(
                    "candidate body is not the exact ordered fragment derivation"
                )
