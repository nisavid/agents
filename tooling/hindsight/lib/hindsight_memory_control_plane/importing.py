"""Deterministic, approval-gated projections for external memory sources."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from .canonical import StrictJsonError, digest, strict_json_loads
from .file_evidence import (
    FileEvidenceError,
    read_file_evidence_with_metadata,
)
from .model import BankRef, deep_freeze, deep_thaw


class ImportValidationError(ValueError):
    pass


SOURCE_TAGS = {
    "codex": "source:codex-memory-archive",
    "claude": "source:file-memory",
    "portable-markdown": "source:portable-import",
    "portable-jsonl": "source:portable-import",
}
COVERAGE_DISPOSITIONS = frozenset(
    {
        "proposed_novel",
        "proposed_duplicate",
        "proposed_conflict",
        "review_pending",
        "omitted",
    }
)
KINDS = frozenset(
    {
        "rule", "principle", "runbook", "decision", "incident", "state",
        "reference", "preference", "goal", "commitment", "relationship",
        "routine", "logistics", "project",
    }
)
RECORD_KEYS = {
    "source_locator", "source_native_id", "timestamp", "line_start", "line_end",
    "content", "kind", "intended_scope", "relationships",
    "coverage_disposition", "coverage_reason",
}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._/@+-]{0,255}\Z")
SCOPE = re.compile(r"(?:global|personal|repo:[a-z0-9][a-z0-9._-]*|workflow:[a-z0-9][a-z0-9._-]*)\Z")
RELATIONSHIP = re.compile(r"(?:repo|workflow|item|person):[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SECRET_SCAN_POLICY_VERSION = 1
SECRET_DETECTORS = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN (?:(?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY|"
            r"PGP PRIVATE KEY BLOCK)-----"
        ),
    ),
    (
        "credential-assignment",
        re.compile(
            r"\b(?:[A-Za-z0-9]+[_-])*(?:password|passwd|api[_-]?key|"
            r"access[_-]?(?:key|token)|auth[_-]?token|client[_-]?secret|"
            r"private[_-]?key|secret)\s*[:=]",
            re.IGNORECASE,
        ),
    ),
    (
        "provider-token",
        re.compile(
            r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|"
            r"\bgh[opusr]_[A-Za-z0-9_]{6,}\b|"
            r"\bsk-[A-Za-z0-9_-]{6,}\b|"
            r"\bxox[baprs]-[A-Za-z0-9-]{6,}\b|"
            r"\bAIza[0-9A-Za-z_-]{20,}\b"
        ),
    ),
    (
        "credential-url",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
    ),
    (
        "authorization-header",
        re.compile(
            r"\bauthorization\s*:\s*(?:bearer|basic)\s+\S+", re.IGNORECASE
        ),
    ),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"
        ),
    ),
)
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
DATE_HEADING = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
WIKI_RELATIONSHIP = re.compile(
    r"\[\[((?:repo|workflow|item|person):[A-Za-z0-9][A-Za-z0-9._-]{0,127})\]\]"
)
PORTABLE_MARKER = re.compile(
    r"^ {0,3}<!--\s*hindsight-memory:\s*(\{.*\})\s*-->\s*$"
)
PORTABLE_KEYS = {
    "id", "timestamp", "kind", "scope", "relationships", "disposition",
    "reason",
}
PORTABLE_JSONL_KEYS = PORTABLE_KEYS | {"content"}
MAX_SOURCE_BYTES = 4 * 1024 * 1024


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ImportValidationError(f"{label} must be a bounded identifier")
    return value


def _source_locator(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 4096
        or any(ord(character) < 32 for character in value)
    ):
        raise ImportValidationError("source locator must be a bounded path")
    canonical = os.path.abspath(os.path.expanduser(value))
    if len(canonical.encode("utf-8")) > 4096:
        raise ImportValidationError("source locator must be a bounded path")
    if value != canonical:
        raise ImportValidationError("source locator must already be canonical")
    return canonical


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ImportValidationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ImportValidationError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ImportValidationError("timestamp must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImportValidationError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(".000000+00:00", "Z").replace("+00:00", "Z")


@dataclass(frozen=True)
class ImportItem:
    item_id: str
    source_kind: str
    source_native_id: str = field(repr=False)
    timestamp: str
    provenance: Mapping[str, Any] = field(repr=False)
    content: str = field(repr=False)
    content_digest: str
    tags: tuple[str, ...] = field(repr=False)
    intended_scope: str = field(repr=False)
    relationships: tuple[str, ...] = field(repr=False)
    coverage_disposition: str
    coverage_reason: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", deep_freeze(self.provenance))

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "source_kind": self.source_kind,
            "source_native_id": self.source_native_id,
            "timestamp": self.timestamp,
            "provenance": deep_thaw(self.provenance),
            "content": self.content,
            "content_digest": self.content_digest,
            "tags": list(self.tags),
            "intended_scope": self.intended_scope,
            "relationships": list(self.relationships),
            "coverage": {
                "disposition": self.coverage_disposition,
                "reason": self.coverage_reason,
            },
        }


def import_item_digest(item: ImportItem) -> str:
    """Bind resume state to every canonical field of a validated item."""
    _validate_import_item(item)
    return digest(item.to_dict())


def record_novelty_review(
    item: ImportItem, *, review_evidence_digest: str
) -> ImportItem:
    """Promote a pending source item only from digest-bound review evidence."""
    _validate_import_item(item)
    if item.coverage_disposition != "review_pending":
        raise ImportValidationError("novelty review requires a pending item")
    evidence = _sha(review_evidence_digest, "review evidence digest")
    reviewed = replace(
        item,
        coverage_disposition="proposed_novel",
        coverage_reason=f"reviewed:{evidence}",
    )
    _validate_import_item(reviewed)
    return reviewed


@dataclass(frozen=True)
class ImportProjection:
    schema_version: int
    items: tuple[ImportItem, ...]
    pending_items: tuple[ImportItem, ...]
    skipped_item_ids: tuple[str, ...]
    skip_evidence: tuple[Mapping[str, str], ...]
    projection_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "skip_evidence",
            tuple(deep_freeze(value) for value in self.skip_evidence),
        )

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "items": [item.to_dict() for item in self.items],
            "pending_item_ids": [item.item_id for item in self.pending_items],
            "skipped_item_ids": list(self.skipped_item_ids),
            "skip_evidence": [deep_thaw(value) for value in self.skip_evidence],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "projection_digest": self.projection_digest}


@dataclass(frozen=True)
class ImportPlan:
    schema_version: int
    projection_digest: str
    coverage_digest: str
    controller_plan_digest: str
    target_bank: BankRef
    actions: tuple[Mapping[str, str], ...]
    plan_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "actions", tuple(deep_freeze(value) for value in self.actions))

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "projection_digest": self.projection_digest,
            "coverage_digest": self.coverage_digest,
            "controller_plan_digest": self.controller_plan_digest,
            "target_bank": self.target_bank.to_dict(),
            "actions": [deep_thaw(value) for value in self.actions],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "plan_digest": self.plan_digest}


@dataclass(frozen=True)
class ReconcileResult:
    complete: bool
    imported_item_ids: tuple[str, ...]
    missing_item_ids: tuple[str, ...]
    reconciliation_digest: str
    import_plan_digest: str
    target_bank: BankRef


def _read_lines(path: str | Path) -> tuple[Path, list[str], int]:
    source = Path(path).expanduser()
    source = Path(os.path.abspath(source))
    try:
        evidence = read_file_evidence_with_metadata(
            source,
            "import source",
            max_bytes=MAX_SOURCE_BYTES,
        )
        if evidence is None:
            raise ImportValidationError("import source is unavailable")
        text = evidence.content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ImportValidationError("import source must be UTF-8") from error
    except FileEvidenceError as error:
        raise ImportValidationError("import source is unavailable") from error
    return source, text.splitlines(), evidence.mtime_ns


def _secret_findings(content: str) -> tuple[str, ...]:
    return tuple(
        detector_id
        for detector_id, detector in SECRET_DETECTORS
        if detector.search(content) is not None
    )


def _reject_secret_metadata(values: Iterable[str]) -> None:
    if any(_secret_findings(value) for value in values):
        raise ImportValidationError("secret-like import metadata is not importable")


def _validate_import_item(item: ImportItem) -> None:
    if not isinstance(item, ImportItem):
        raise ImportValidationError("projection items are invalid")
    _sha(item.item_id, "item identity")
    if item.source_kind not in SOURCE_TAGS:
        raise ImportValidationError("source kind is not supported")
    native_id = _identifier(item.source_native_id, "source native identity")
    if not isinstance(item.provenance, Mapping) or set(item.provenance) != {
        "source_locator", "line_start", "line_end"
    }:
        raise ImportValidationError("item provenance keys are closed")
    locator = _source_locator(item.provenance["source_locator"])
    line_start = item.provenance["line_start"]
    line_end = item.provenance["line_end"]
    if (
        type(line_start) is not int
        or type(line_end) is not int
        or line_start < 1
        or line_end < line_start
    ):
        raise ImportValidationError("provenance lines must be a positive ordered range")
    if not hmac.compare_digest(
        digest({"source_locator": locator, "source_native_id": native_id}),
        item.item_id,
    ):
        raise ImportValidationError("item identity does not match its source identity")
    if _timestamp(item.timestamp) != item.timestamp:
        raise ImportValidationError("item timestamp must be normalized UTC")
    if (
        not isinstance(item.content, str)
        or not item.content.strip()
        or len(item.content.encode("utf-8")) > 65536
    ):
        raise ImportValidationError("content must be non-empty and bounded")
    _sha(item.content_digest, "content digest")
    if not hmac.compare_digest(digest(item.content), item.content_digest):
        raise ImportValidationError("content digest does not match content")
    if not isinstance(item.intended_scope, str) or not SCOPE.fullmatch(
        item.intended_scope
    ):
        raise ImportValidationError("intended scope is not supported")
    if not isinstance(item.relationships, tuple) or any(
        not isinstance(value, str) or not RELATIONSHIP.fullmatch(value)
        for value in item.relationships
    ):
        raise ImportValidationError("relationships must use the closed hint vocabulary")
    if item.relationships != tuple(sorted(set(item.relationships))):
        raise ImportValidationError("relationships must be uniquely and canonically ordered")
    if item.coverage_disposition not in COVERAGE_DISPOSITIONS:
        raise ImportValidationError("coverage disposition is not supported")
    coverage_reason = _identifier(item.coverage_reason, "coverage reason")
    if item.coverage_disposition == "proposed_novel" and not re.fullmatch(
        r"reviewed:[0-9a-f]{64}", coverage_reason
    ):
        raise ImportValidationError(
            "proposed novel coverage requires evidence from a completed review"
        )
    if not isinstance(item.tags, tuple):
        raise ImportValidationError("item tags must be a canonical tuple")
    kind_tags = tuple(tag for tag in item.tags if tag.startswith("kind:"))
    if len(kind_tags) != 1 or kind_tags[0][5:] not in KINDS:
        raise ImportValidationError("item kind tag is invalid")
    expected_tags = {
        SOURCE_TAGS[item.source_kind], kind_tags[0], "scope:active"
    }
    if item.intended_scope.startswith(("repo:", "workflow:")):
        expected_tags.add(item.intended_scope)
    if item.tags != tuple(sorted(expected_tags)):
        raise ImportValidationError("item tags do not match the closed derived schema")
    _reject_secret_metadata(
        (
            item.content,
            native_id,
            locator,
            coverage_reason,
            *item.relationships,
        )
    )


def _file_timestamp(mtime_ns: int, supplied: str | None) -> str:
    if supplied is not None:
        return _timestamp(supplied)
    try:
        value = datetime.fromtimestamp(mtime_ns / 1_000_000_000, timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ImportValidationError("import source timestamp is unavailable") from error
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug or len(slug) > 200:
        raise ImportValidationError("markdown heading cannot form a stable source identity")
    return slug


def _trimmed_body(lines: Sequence[str], start: int, end: int) -> tuple[str, int, int] | None:
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    if start == end:
        return None
    return "\n".join(lines[start:end]), start + 1, end


def _heading_timestamp(title: str) -> str | None:
    value = title.strip()
    if DATE_HEADING.fullmatch(value):
        return _timestamp(f"{value}T00:00:00Z")
    if "T" in value:
        try:
            return _timestamp(value)
        except ImportValidationError:
            return None
    return None


def _markdown_structure_mask(lines: Sequence[str]) -> tuple[bool, ...]:
    """Mark lines whose Markdown structure is outside fenced code blocks."""

    result: list[bool] = []
    fence_character: str | None = None
    fence_length = 0
    for line in lines:
        if fence_character is not None:
            closing = re.fullmatch(
                rf" {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*",
                line,
            )
            result.append(False)
            if closing is not None:
                fence_character = None
                fence_length = 0
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if opening is not None and not (
            opening.group(1).startswith("`") and "`" in opening.group(2)
        ):
            fence_character = opening.group(1)[0]
            fence_length = len(opening.group(1))
            result.append(False)
            continue
        result.append(True)
    return tuple(result)


def _curated_markdown_records(
    path: str | Path,
    *,
    timestamp: str | None,
) -> tuple[dict[str, Any], ...]:
    source, lines, mtime_ns = _read_lines(path)
    source_timestamp = _file_timestamp(mtime_ns, timestamp)
    structural = _markdown_structure_mask(lines)
    headings = [
        (index, len(match.group(1)), match.group(2))
        for index, line in enumerate(lines)
        if structural[index] and (match := HEADING.fullmatch(line)) is not None
    ]
    sections: list[tuple[tuple[str, ...], int, int, str]] = []
    if headings:
        if any(line.strip() for line in lines[: headings[0][0]]):
            raise ImportValidationError(
                "curated memory source contains content before its first heading"
            )
        heading_timestamps: dict[int, str] = {}
        ancestry: list[str] = []
        for position, (heading_line, level, title) in enumerate(headings):
            heading_timestamps = {
                ancestor_level: value
                for ancestor_level, value in heading_timestamps.items()
                if ancestor_level < level
            }
            if heading_timestamp := _heading_timestamp(title):
                heading_timestamps[level] = heading_timestamp
            ancestry = ancestry[: level - 1]
            ancestry.append(title)
            end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
            body = _trimmed_body(lines, heading_line + 1, end)
            if body is not None:
                _content, line_start, line_end = body
                sections.append(
                    (
                        tuple(ancestry),
                        line_start,
                        line_end,
                        heading_timestamps.get(
                            max(heading_timestamps), source_timestamp
                        )
                        if heading_timestamps
                        else source_timestamp,
                    )
                )
    else:
        body = _trimmed_body(lines, 0, len(lines))
        if body is not None:
            _content, line_start, line_end = body
            sections.append(((source.stem,), line_start, line_end, source_timestamp))
    if not sections:
        raise ImportValidationError("curated memory source contains no durable sections")

    identities: set[str] = set()
    records: list[dict[str, Any]] = []
    for ancestry, line_start, line_end, item_timestamp in sections:
        readable = "--".join(_slug(title) for title in ancestry)
        identity_suffix = digest(
            {"heading_ancestry": list(ancestry)}
        )[:16]
        native_id = f"{readable[:110].rstrip('-')}-{identity_suffix}"
        if native_id in identities:
            raise ImportValidationError("duplicate Markdown heading cannot form a stable source identity")
        identities.add(native_id)
        content = "\n".join(lines[line_start - 1 : line_end])
        relationship_content = "\n".join(
            line
            for index, line in enumerate(
                lines[line_start - 1 : line_end], line_start - 1
            )
            if structural[index]
        )
        relationships = sorted(
            set(WIKI_RELATIONSHIP.findall(relationship_content))
        )
        repository_scopes = tuple(
            value for value in relationships if value.startswith("repo:")
        )
        workflow_scopes = tuple(
            value for value in relationships if value.startswith("workflow:")
        )
        if len(repository_scopes) + len(workflow_scopes) > 1:
            raise ImportValidationError(
                "curated memory section has ambiguous inferred scope"
            )
        repository_scope = repository_scopes[0] if repository_scopes else None
        workflow_scope = workflow_scopes[0] if workflow_scopes else None
        records.append(
            {
                "source_locator": str(source),
                "source_native_id": native_id,
                "timestamp": item_timestamp,
                "line_start": line_start,
                "line_end": line_end,
                "content": content,
                "kind": "reference",
                "intended_scope": repository_scope or workflow_scope or "global",
                "relationships": relationships,
                "coverage_disposition": "review_pending",
                "coverage_reason": "coverage-review-required",
            }
        )
    return tuple(records)


def parse_codex_memory(
    path: str | Path, *, timestamp: str | None = None
) -> tuple[dict[str, Any], ...]:
    records = _curated_markdown_records(path, timestamp=timestamp)
    inspect_items("codex", records)
    return records


def parse_claude_memory(
    path: str | Path, *, timestamp: str | None = None
) -> tuple[dict[str, Any], ...]:
    records = _curated_markdown_records(path, timestamp=timestamp)
    inspect_items("claude", records)
    return records


def _portable_record(
    metadata: Any,
    *,
    content: Any,
    source: Path,
    line_start: int,
    line_end: int,
) -> dict[str, Any]:
    if not isinstance(metadata, dict) or set(metadata) != PORTABLE_KEYS:
        raise ImportValidationError("portable manifest metadata keys are closed")
    return {
        "source_locator": str(source),
        "source_native_id": metadata["id"],
        "timestamp": metadata["timestamp"],
        "line_start": line_start,
        "line_end": line_end,
        "content": content,
        "kind": metadata["kind"],
        "intended_scope": metadata["scope"],
        "relationships": metadata["relationships"],
        "coverage_disposition": metadata["disposition"],
        "coverage_reason": metadata["reason"],
    }


def parse_portable_markdown(path: str | Path) -> tuple[dict[str, Any], ...]:
    source, lines, _mtime_ns = _read_lines(path)
    structural = _markdown_structure_mask(lines)
    markers: list[tuple[int, dict[str, Any]]] = []
    for index, line in enumerate(lines):
        if not structural[index]:
            continue
        if len(line) - len(line.lstrip(" ")) >= 4 and PORTABLE_MARKER.fullmatch(
            line.lstrip(" ")
        ):
            raise ImportValidationError(
                "portable Markdown metadata markers may use at most three leading spaces"
            )
        match = PORTABLE_MARKER.fullmatch(line)
        if match is None:
            continue
        try:
            metadata = strict_json_loads(match.group(1))
        except (StrictJsonError, json.JSONDecodeError) as error:
            raise ImportValidationError("portable Markdown metadata must be JSON") from error
        markers.append((index, metadata))
    if not markers or any(line.strip() for line in lines[: markers[0][0]]):
        raise ImportValidationError("portable Markdown requires explicit item metadata")
    records = []
    for position, (marker_line, metadata) in enumerate(markers):
        end = markers[position + 1][0] if position + 1 < len(markers) else len(lines)
        body = _trimmed_body(lines, marker_line + 1, end)
        if body is None:
            raise ImportValidationError("portable Markdown item content is required")
        content, line_start, line_end = body
        records.append(
            _portable_record(
                metadata,
                content=content,
                source=source,
                line_start=line_start,
                line_end=line_end,
            )
        )
    inspect_items("portable-markdown", records)
    return tuple(records)


def parse_portable_jsonl(path: str | Path) -> tuple[dict[str, Any], ...]:
    source, lines, _mtime_ns = _read_lines(path)
    records = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = strict_json_loads(line)
        except (StrictJsonError, json.JSONDecodeError) as error:
            raise ImportValidationError("portable JSONL contains invalid JSON") from error
        if not isinstance(value, dict) or set(value) != PORTABLE_JSONL_KEYS:
            raise ImportValidationError("portable JSONL record keys are closed")
        metadata = {key: value[key] for key in PORTABLE_KEYS}
        records.append(
            _portable_record(
                metadata,
                content=value["content"],
                source=source,
                line_start=line_number,
                line_end=line_number,
            )
        )
    if not records:
        raise ImportValidationError("portable JSONL contains no records")
    inspect_items("portable-jsonl", records)
    return tuple(records)


def inspect_source(
    source_kind: str,
    path: str | Path,
    *,
    timestamp: str | None = None,
) -> tuple[ImportItem, ...]:
    if source_kind == "codex":
        records = parse_codex_memory(path, timestamp=timestamp)
    elif source_kind == "claude":
        records = parse_claude_memory(path, timestamp=timestamp)
    elif source_kind == "portable-markdown":
        if timestamp is not None:
            raise ImportValidationError("portable manifests carry their own timestamps")
        records = parse_portable_markdown(path)
    elif source_kind == "portable-jsonl":
        if timestamp is not None:
            raise ImportValidationError("portable manifests carry their own timestamps")
        records = parse_portable_jsonl(path)
    else:
        raise ImportValidationError("source kind is not supported")
    return inspect_items(source_kind, records)


def inspect_items(source_kind: str, records: Sequence[Mapping[str, Any]]) -> tuple[ImportItem, ...]:
    if source_kind not in SOURCE_TAGS:
        raise ImportValidationError("source kind is not supported")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ImportValidationError("source records must be an array")
    result: list[ImportItem] = []
    identities: set[str] = set()
    for raw in records:
        if not isinstance(raw, dict) or set(raw) != RECORD_KEYS:
            raise ImportValidationError("source record keys are closed")
        locator = _source_locator(raw["source_locator"])
        native_id = _identifier(raw["source_native_id"], "source native identity")
        item_id = digest({"source_locator": locator, "source_native_id": native_id})
        if item_id in identities:
            raise ImportValidationError("duplicate source identity")
        identities.add(item_id)
        line_start, line_end = raw["line_start"], raw["line_end"]
        if type(line_start) is not int or type(line_end) is not int or line_start < 1 or line_end < line_start:
            raise ImportValidationError("provenance lines must be a positive ordered range")
        content = raw["content"]
        if not isinstance(content, str) or not content.strip() or len(content.encode()) > 65536:
            raise ImportValidationError("content must be non-empty and bounded")
        if _secret_findings(content):
            raise ImportValidationError("secret-like content is not importable")
        kind = raw["kind"]
        if kind not in KINDS:
            raise ImportValidationError("kind is not in the closed durable vocabulary")
        intended_scope = raw["intended_scope"]
        if not isinstance(intended_scope, str) or not SCOPE.fullmatch(intended_scope):
            raise ImportValidationError("intended scope is not supported")
        relationships = raw["relationships"]
        if not isinstance(relationships, list) or any(not isinstance(value, str) or not RELATIONSHIP.fullmatch(value) for value in relationships):
            raise ImportValidationError("relationships must use the closed hint vocabulary")
        if len(set(relationships)) != len(relationships):
            raise ImportValidationError("relationships must be unique")
        disposition = raw["coverage_disposition"]
        if not isinstance(disposition, str) or disposition not in COVERAGE_DISPOSITIONS:
            raise ImportValidationError("coverage disposition is not supported")
        reason = _identifier(raw["coverage_reason"], "coverage reason")
        _reject_secret_metadata((native_id, locator, reason, *relationships))
        if disposition.startswith("proposed_"):
            disposition = "review_pending"
            reason = "coverage-review-required"
        tags = {SOURCE_TAGS[source_kind], f"kind:{kind}", "scope:active"}
        if intended_scope.startswith(("repo:", "workflow:")):
            tags.add(intended_scope)
        item = ImportItem(
                item_id=item_id,
                source_kind=source_kind,
                source_native_id=native_id,
                timestamp=_timestamp(raw["timestamp"]),
                provenance={"source_locator": locator, "line_start": line_start, "line_end": line_end},
                content=content,
                content_digest=digest(content),
                tags=tuple(sorted(tags)),
                intended_scope=intended_scope,
                relationships=tuple(sorted(relationships)),
                coverage_disposition=disposition,
                coverage_reason=reason,
            )
        _validate_import_item(item)
        result.append(item)
    return tuple(sorted(result, key=lambda item: item.item_id))


def project_import(items: Iterable[ImportItem], *, resume_state: Mapping[str, str] | None = None) -> ImportProjection:
    supplied = tuple(items)
    for item in supplied:
        _validate_import_item(item)
    ordered = tuple(sorted(supplied, key=lambda item: (item.timestamp, item.item_id)))
    if len({item.item_id for item in ordered}) != len(ordered):
        raise ImportValidationError("projection item identities must be unique")
    resume = dict(resume_state or {})
    for item_id, item_digest in resume.items():
        _sha(item_id, "resume item identity")
        _sha(item_digest, "resume item digest")
    known_item_ids = {item.item_id for item in ordered}
    unknown_resume_ids = set(resume) - known_item_ids
    if unknown_resume_ids:
        raise ImportValidationError(
            "resume state references unknown projection item identities"
        )
    skipped = tuple(
        item.item_id
        for item in ordered
        if item.coverage_disposition == "omitted"
        or resume.get(item.item_id) == import_item_digest(item)
    )
    skip_evidence = tuple(
        (
            {
                "item_id": item.item_id,
                "reason": item.coverage_reason,
            }
            if item.coverage_disposition == "omitted"
            else {
                "item_id": item.item_id,
                "item_digest": import_item_digest(item),
            }
        )
        for item in ordered
        if item.item_id in skipped
    )
    pending = tuple(item for item in ordered if item.item_id not in skipped)
    body = {
        "schema_version": 1,
        "items": [item.to_dict() for item in ordered],
        "pending_item_ids": [item.item_id for item in pending],
        "skipped_item_ids": list(skipped),
        "skip_evidence": [dict(value) for value in skip_evidence],
    }
    projection = ImportProjection(
        1, ordered, pending, skipped, skip_evidence, digest(body)
    )
    validate_projection(projection)
    return projection


def validate_projection(projection: ImportProjection) -> None:
    if not isinstance(projection, ImportProjection) or type(projection.schema_version) is not int or projection.schema_version != 1:
        raise ImportValidationError("projection schema_version must be integer 1")
    for item in projection.items:
        _validate_import_item(item)
    expected_order = tuple(sorted(projection.items, key=lambda item: (item.timestamp, item.item_id)))
    if projection.items != expected_order or len({item.item_id for item in projection.items}) != len(projection.items):
        raise ImportValidationError("projection items must be uniquely and canonically ordered")
    by_id = {item.item_id: item for item in projection.items}
    if any(by_id.get(item.item_id) != item for item in projection.pending_items):
        raise ImportValidationError("projection pending items must reference exact projection items")
    pending_ids = tuple(item.item_id for item in projection.pending_items)
    pending_id_set = frozenset(pending_ids)
    if len(pending_id_set) != len(pending_ids) or pending_ids != tuple(item.item_id for item in projection.items if item.item_id in pending_id_set):
        raise ImportValidationError("projection pending items must preserve canonical order")
    if any(
        item.coverage_disposition == "omitted"
        for item in projection.pending_items
    ):
        raise ImportValidationError(
            "projection pending items must exclude omitted items"
        )
    if tuple(item.item_id for item in projection.items if item.item_id not in pending_id_set) != projection.skipped_item_ids:
        raise ImportValidationError("projection skipped items must exactly complement pending items")
    if not isinstance(projection.skip_evidence, tuple):
        raise ImportValidationError("projection skip evidence must be canonical")
    if len(projection.skip_evidence) != len(projection.skipped_item_ids):
        raise ImportValidationError(
            "projection skipped items require exact skip evidence"
        )
    for skipped_id, evidence in zip(
        projection.skipped_item_ids, projection.skip_evidence, strict=True
    ):
        item = by_id[skipped_id]
        if not isinstance(evidence, Mapping) or evidence.get("item_id") != skipped_id:
            raise ImportValidationError(
                "projection skipped items require exact skip evidence"
            )
        if item.coverage_disposition == "omitted":
            reason = evidence.get("reason")
            if (
                set(evidence) != {"item_id", "reason"}
                or not isinstance(reason, str)
                or not hmac.compare_digest(reason, item.coverage_reason)
            ):
                raise ImportValidationError(
                    "non-importable projection items require their explicit omission reason"
                )
        else:
            item_digest = evidence.get("item_digest")
            if (
                set(evidence) != {"item_id", "item_digest"}
                or not isinstance(item_digest, str)
                or not hmac.compare_digest(
                    item_digest, import_item_digest(item)
                )
            ):
                raise ImportValidationError(
                    "resumed projection items require their canonical item digest"
                )
    _sha(projection.projection_digest, "projection digest")
    if not hmac.compare_digest(digest(projection.body()), projection.projection_digest):
        raise ImportValidationError("projection digest does not match projection body")


def build_import_plan(
    projection: ImportProjection,
    *,
    controller_plan_digest: str,
    target_bank: BankRef,
) -> ImportPlan:
    validate_projection(projection)
    _sha(controller_plan_digest, "controller plan digest")
    if not isinstance(target_bank, BankRef):
        raise ImportValidationError("target bank must be a canonical bank reference")
    coverage = [
        {"item_id": item.item_id, "disposition": item.coverage_disposition, "reason": item.coverage_reason}
        for item in projection.items
    ]
    actions = tuple(
        {
            "item_id": item.item_id,
            "item_digest": import_item_digest(item),
            "operation": "retain",
        }
        for item in projection.pending_items
        if item.coverage_disposition not in {"omitted", "review_pending"}
    )
    body = {
        "schema_version": 1,
        "projection_digest": projection.projection_digest,
        "coverage_digest": digest(coverage),
        "controller_plan_digest": controller_plan_digest,
        "target_bank": target_bank.to_dict(),
        "actions": [dict(value) for value in actions],
    }
    return ImportPlan(
        1,
        projection.projection_digest,
        body["coverage_digest"],
        controller_plan_digest,
        target_bank,
        actions,
        digest(body),
    )


def _validate_import_plan(plan: ImportPlan) -> None:
    if not isinstance(plan, ImportPlan) or type(plan.schema_version) is not int or plan.schema_version != 1:
        raise ImportValidationError("import plan schema is invalid")
    if not isinstance(plan.target_bank, BankRef):
        raise ImportValidationError("import plan target bank is invalid")
    for key in ("projection_digest", "coverage_digest", "controller_plan_digest", "plan_digest"):
        _sha(getattr(plan, key), f"import plan {key}")
    seen_item_ids: set[str] = set()
    for action in plan.actions:
        if (
            not isinstance(action, Mapping)
            or set(action) != {"item_id", "item_digest", "operation"}
        ):
            raise ImportValidationError("import plan action schema is closed")
        item_id = _sha(action["item_id"], "import plan action item identity")
        _sha(action["item_digest"], "import plan action item digest")
        if action["operation"] != "retain":
            raise ImportValidationError("import plan action operation must be retain")
        if item_id in seen_item_ids:
            raise ImportValidationError("import plan action item identity is duplicated")
        seen_item_ids.add(item_id)
    if not hmac.compare_digest(digest(plan.body()), plan.plan_digest):
        raise ImportValidationError("import plan digest does not match its body")


def apply_import_plan(
    plan: ImportPlan,
    *,
    projection: ImportProjection,
    approved_plan_digest: str | None,
    controller_apply: Callable[[dict[str, Any]], Any],
) -> str:
    _validate_import_plan(plan)
    expected = build_import_plan(
        projection,
        controller_plan_digest=plan.controller_plan_digest,
        target_bank=plan.target_bank,
    )
    if expected.to_dict() != plan.to_dict():
        raise ImportValidationError(
            "import plan does not match the inspected projection"
        )
    if approved_plan_digest is None or not hmac.compare_digest(approved_plan_digest, plan.plan_digest):
        raise ImportValidationError("exact digest-bound import plan approval is required")
    controller_apply(plan.to_dict())
    return plan.plan_digest


def reconcile_import(
    projection: ImportProjection,
    plan: ImportPlan,
    receipts: Sequence[Mapping[str, Any]],
    *,
    approved_plan_digest: str | None,
) -> ReconcileResult:
    validate_projection(projection)
    _validate_import_plan(plan)
    expected_plan = build_import_plan(
        projection,
        controller_plan_digest=plan.controller_plan_digest,
        target_bank=plan.target_bank,
    )
    if expected_plan.to_dict() != plan.to_dict():
        raise ImportValidationError(
            "import plan does not match the inspected projection"
        )
    if (
        approved_plan_digest is None
        or not hmac.compare_digest(approved_plan_digest, plan.plan_digest)
        or not hmac.compare_digest(projection.projection_digest, plan.projection_digest)
    ):
        raise ImportValidationError("exact approved import plan is required for reconciliation")
    expected = {
        item.item_id: import_item_digest(item)
        for item in projection.pending_items
        if item.coverage_disposition not in {"omitted", "review_pending"}
    }
    unresolved = {
        item.item_id
        for item in projection.pending_items
        if item.coverage_disposition == "review_pending"
    }
    seen: dict[str, str] = {}
    for raw in receipts:
        if not isinstance(raw, dict) or set(raw) != {
            "item_id", "item_digest", "status", "import_plan_digest", "target_bank"
        }:
            raise ImportValidationError("reconciliation receipt keys are closed")
        item_id = _sha(raw["item_id"], "receipt item identity")
        item_digest = _sha(raw["item_digest"], "receipt item digest")
        if (
            raw["status"] != "imported"
            or raw["import_plan_digest"] != plan.plan_digest
            or raw["target_bank"] != plan.target_bank.to_dict()
            or item_id not in expected
            or expected[item_id] != item_digest
            or item_id in seen
        ):
            raise ImportValidationError("reconciliation receipt does not match the projection")
        seen[item_id] = item_digest
    imported_ids = frozenset(seen)
    imported = tuple(sorted(imported_ids))
    missing = tuple(sorted((frozenset(expected) - imported_ids) | unresolved))
    body = {
        "projection_digest": projection.projection_digest,
        "import_plan_digest": plan.plan_digest,
        "target_bank": plan.target_bank.to_dict(),
        "imported_item_ids": list(imported),
        "missing_item_ids": list(missing),
    }
    return ReconcileResult(
        not missing,
        imported,
        missing,
        digest(body),
        plan.plan_digest,
        plan.target_bank,
    )
