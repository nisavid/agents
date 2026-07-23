"""Private per-session bridge and native harness payload adapters."""

from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import threading
import time
from typing import Any

from .canonical import canonical_bytes
from .broker import (
    BrokerError,
    DEFAULT_SESSION_TTL_SECONDS,
    MAX_DURABLE_QUEUE_BYTES,
    MAX_REQUEST_SEQUENCE,
    MAX_SESSION_ACTION_IDS,
    MAX_SESSION_TTL_SECONDS,
)
from .file_evidence import FileEvidenceError, open_trusted_parent


MAX_EVENTS = 1024
MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024
MAX_TRANSCRIPT_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_TRANSCRIPT_RECORD_BYTES = 16 * 1024 * 1024
MAX_INPUT_BYTES = 128 * 1024
MAX_BRIDGE_REQUEST_BYTES = MAX_TRANSCRIPT_BYTES + (64 * 1024)
MAX_CHECKPOINT_SEGMENT_BYTES = 48 * 1024
CHECKPOINT_QUEUE_RESERVE_BYTES = 512 * 1024
MAX_CONTEXT_BYTES = 16 * 1024
GUI_RESERVATION_SECONDS = 60
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
MEMORY_BLOCK = re.compile(
    r"<hindsight_memories\b[^>]*>.*?</hindsight_memories>",
    re.IGNORECASE | re.DOTALL,
)
MEMORY_TAG = re.compile(
    r"</?hindsight_memories\b[^>]*>", re.IGNORECASE
)
SYNTHETIC_BLOCK = re.compile(
    r"<(?:system-reminder|hook-context|memory-context|environment_context|"
    r"recommended_plugins|skill)\b[^>]*>.*?"
    r"</(?:system-reminder|hook-context|memory-context|environment_context|"
    r"recommended_plugins|skill)>",
    re.IGNORECASE | re.DOTALL,
)
DELEGATION_BLOCK = re.compile(
    r"<codex_delegation\b[^>]*>.*?<input>(.*?)</input>.*?</codex_delegation>",
    re.IGNORECASE | re.DOTALL,
)
AGENT_INSTRUCTIONS = re.compile(
    r"\A\s*#\s*AGENTS\.md instructions\b", re.IGNORECASE
)
FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "bank",
        "bank_id",
        "bearer",
        "capability",
        "credential",
        "credentials",
        "destination",
        "endpoint",
        "envelope",
        "home_bank",
        "route",
        "scope",
        "scopes",
        "secret",
        "signing_key",
        "tags",
        "token",
        "url",
    }
)
FORBIDDEN_INPUT_TOKENS = frozenset(
    re.sub(r"[^a-z0-9]", "", key.lower()) for key in FORBIDDEN_INPUT_KEYS
)
# This intentionally fails closed: benign nested keys normalize to a rejection
# when their token is also authority-bearing (for example scope, route, or URL).
class BridgeError(ValueError):
    """Content-free bridge rejection safe for visible hook diagnostics."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _identifier(value: Any, code: str = "INPUT_INVALID") -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise BridgeError(code)
    return value


def _bounded_text(value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise BridgeError("INPUT_INVALID")
    text = value.strip()
    if (not text and not allow_empty) or len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise BridgeError("INPUT_INVALID")
    return text


def _bounded_transcript(value: Any) -> str:
    if not isinstance(value, str):
        raise BridgeError("INPUT_INVALID")
    text = value.strip()
    if not text or len(text.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        raise BridgeError("INPUT_INVALID")
    return text


def _utf8_segments(value: str) -> list[str]:
    """Split complete transcript text without dropping or corrupting content."""

    overhead = len(canonical_bytes({"content": ""}))
    segments: list[str] = []
    current: list[str] = []
    current_bytes = overhead
    short_escapes = {"\b", "\t", "\n", "\f", "\r"}
    for character in value:
        codepoint = ord(character)
        if character in {'"', "\\"} or character in short_escapes:
            character_bytes = 2
        elif codepoint < 0x20:
            character_bytes = 6
        else:
            try:
                character_bytes = len(character.encode("utf-8"))
            except UnicodeEncodeError as error:
                raise BridgeError("INPUT_INVALID") from error
        if current and current_bytes + character_bytes > MAX_CHECKPOINT_SEGMENT_BYTES:
            segments.append("".join(current))
            current = []
            current_bytes = overhead
        if current_bytes + character_bytes > MAX_CHECKPOINT_SEGMENT_BYTES:
            raise BridgeError("INPUT_INVALID")
        current.append(character)
        current_bytes += character_bytes
    if current:
        segments.append("".join(current))
    return segments


def _forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                return True
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in FORBIDDEN_INPUT_TOKENS or _forbidden_key(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_forbidden_key(child) for child in value)
    return False


def _request_fingerprint(value: Mapping[str, Any]) -> str:
    try:
        return hashlib.sha256(canonical_bytes(value)).hexdigest()
    except (TypeError, ValueError) as error:
        raise BridgeError("INPUT_INVALID") from error


def _safe_response(response: Any, *, handle: str | None, capability: str | None) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        raise BridgeError("BROKER_RESPONSE_INVALID")
    selected = {
        "disposition": response.get("disposition"),
        "payload": deepcopy(response.get("payload")),
        "diagnostic": deepcopy(response.get("diagnostic")),
    }
    if not isinstance(selected["disposition"], str) or _forbidden_key(selected):
        raise BridgeError("BROKER_RESPONSE_INVALID")
    try:
        encoded = canonical_bytes(selected)
    except (TypeError, ValueError) as error:
        raise BridgeError("BROKER_RESPONSE_INVALID") from error
    if len(encoded) > MAX_INPUT_BYTES:
        raise BridgeError("BROKER_RESPONSE_INVALID")
    for private in (handle, capability):
        if private and private.encode("utf-8") in encoded:
            raise BridgeError("BROKER_RESPONSE_INVALID")
    return selected


def _validated_write_response(
    response: dict[str, Any], expected_watermark: list[int]
) -> dict[str, Any]:
    payload = response.get("payload")
    if (
        response.get("disposition") not in {"queued", "idempotent"}
        or not isinstance(payload, Mapping)
        or payload.get("watermark") != expected_watermark
    ):
        raise BridgeError("BROKER_RESPONSE_INVALID")
    return response


class SessionBridge:
    """Own one broker capability and serialize native hook operations."""

    def __init__(
        self,
        *,
        broker_client: Any,
        handle: str,
        session_id: str,
        harness_id: str,
    ) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", handle or ""):
            raise BridgeError("HANDLE_INVALID")
        self.broker_client = broker_client
        self._handle: str | None = handle
        self._capability: str | None = None
        self.session_id = _identifier(session_id, "SESSION_INVALID")
        if harness_id not in {"codex", "claude-code", "cursor"}:
            raise BridgeError("HARNESS_INVALID")
        self.harness_id = harness_id
        self._document_namespace = hashlib.sha256(
            self.session_id.encode("utf-8")
        ).hexdigest()[:32]
        self.sequence = 0
        self.epoch = 0
        self.checkpoint = 0
        self.outcome_checkpoint = 0
        self.closed = False
        self._sealed_transcript = ""
        self._epoch_content = ""
        self._epoch_segments: list[str] = []
        self._events: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._hook_adapter: HookAdapter | None = None

    def dispatch_native(self, request: Any) -> dict[str, Any]:
        if (
            not isinstance(request, Mapping)
            or set(request) != {"harness_id", "event", "payload"}
            or request.get("harness_id") != self.harness_id
            or not isinstance(request.get("event"), str)
            or not isinstance(request.get("payload"), Mapping)
        ):
            raise BridgeError("INPUT_INVALID")
        if self._hook_adapter is None:
            self._hook_adapter = HookAdapter(self.harness_id, self.dispatch)
        return self._hook_adapter.handle(request["event"], request["payload"])

    def _exchange(self) -> str:
        if self._capability is not None:
            return self._capability
        if self._handle is None:
            raise BridgeError("EXCHANGE_UNAVAILABLE")
        handle = self._handle
        response = self.broker_client.session_exchange(handle)
        if not isinstance(response, Mapping):
            raise BridgeError("EXCHANGE_UNAVAILABLE")
        payload = response.get("payload")
        if (
            not isinstance(payload, Mapping)
            or not isinstance(payload.get("capability"), str)
            or not payload["capability"]
        ):
            raise BridgeError("EXCHANGE_UNAVAILABLE")
        self._capability = payload["capability"]
        self._handle = None
        return self._capability

    @staticmethod
    def _validate_dispatch_request(request: Any) -> tuple[str, str, dict[str, Any]]:
        if (
            not isinstance(request, Mapping)
            or set(request) != {"event_id", "operation", "input"}
            or not isinstance(request.get("input"), Mapping)
            or _forbidden_key(request)
        ):
            raise BridgeError("INPUT_INVALID")
        event_id = _identifier(request["event_id"])
        operation = request["operation"]
        if operation not in {
            "recall",
            "model",
            "checkpoint",
            "outcome",
            "reflect",
            "status",
            "close",
        }:
            raise BridgeError("OPERATION_DENIED")
        value = deepcopy(dict(request["input"]))
        request_limit = (
            MAX_BRIDGE_REQUEST_BYTES
            if operation == "checkpoint"
            else MAX_INPUT_BYTES
        )
        try:
            encoded_length = len(canonical_bytes(value))
        except (TypeError, ValueError) as error:
            raise BridgeError("INPUT_INVALID") from error
        if encoded_length > request_limit:
            raise BridgeError("INPUT_INVALID")
        return event_id, operation, value

    def _broker_call(
        self,
        operation: str,
        value: Mapping[str, Any],
        *,
        sequence: int,
        action_id: str,
    ) -> tuple[dict[str, Any], int, dict[str, Any]]:
        capability = self._exchange()
        common = {"sequence": sequence, "action_id": action_id}
        updates: dict[str, Any] = {}
        if operation == "recall":
            if not {"query"} <= set(value) <= {"query", "limit", "depth"}:
                raise BridgeError("INPUT_INVALID")
            request: dict[str, Any] = {"query": _bounded_text(value["query"])}
            if "limit" in value:
                limit = value["limit"]
                if type(limit) is not int or not 1 <= limit <= 20:
                    raise BridgeError("INPUT_INVALID")
                request["limit"] = limit
            if "depth" in value:
                if value["depth"] not in {"routine", "deep"}:
                    raise BridgeError("INPUT_INVALID")
                request["depth"] = value["depth"]
            response = self.broker_client.recall(
                capability, request=request, timeout_seconds=2, **common
            )
        elif operation == "model":
            if set(value) != {"model_id"}:
                raise BridgeError("INPUT_INVALID")
            response = self.broker_client.mental_model_fetch(
                capability,
                request={"model_id": _identifier(value["model_id"])},
                timeout_seconds=2,
                **common,
            )
        elif operation == "checkpoint":
            if not {"content"} <= set(value) <= {"content", "seal_epoch"}:
                raise BridgeError("INPUT_INVALID")
            if "seal_epoch" in value and type(value["seal_epoch"]) is not bool:
                raise BridgeError("INPUT_INVALID")
            full_content = _bounded_transcript(value["content"])
            content = full_content
            sealed_transcript = self._sealed_transcript
            if self._sealed_transcript:
                if full_content == self._sealed_transcript:
                    content = ""
                else:
                    separator = self._sealed_transcript + "\n\n"
                    if full_content.startswith(separator):
                        content = full_content[len(separator) :]
                    else:
                        sealed_transcript = ""
            seal_epoch = value.get("seal_epoch") is True
            effective_epoch = self.epoch
            prior_segments = self._epoch_segments
            next_checkpoint = self.checkpoint + 1
            if (
                content
                and self._epoch_content
                and not content.startswith(self._epoch_content)
            ):
                effective_epoch += 1
                prior_segments = []
                next_checkpoint = 1
                sealed_transcript = ""
            segments = _utf8_segments(content) if content else []
            changed_segments = [
                (index, segment)
                for index, segment in enumerate(segments)
                if index >= len(prior_segments) or prior_segments[index] != segment
            ]
            if not changed_segments:
                response = {
                    "disposition": "ok",
                    "payload": {
                        "watermark": [effective_epoch, self.checkpoint],
                        "write_state": "unchanged",
                        "segments": len(segments),
                    },
                    "diagnostic": None,
                }
                completed_sequence = self.sequence
            else:
                completed_sequence = sequence + len(changed_segments) - 1
            requests = [
                {
                    "document_id": (
                        f"session-{self._document_namespace}:"
                        f"epoch:{effective_epoch}:segment:{index}"
                    ),
                    "epoch": effective_epoch,
                    "checkpoint": next_checkpoint,
                    "content": segment,
                }
                for index, segment in changed_segments
            ]
            if (
                sum(len(canonical_bytes(request)) for request in requests)
                > MAX_DURABLE_QUEUE_BYTES - CHECKPOINT_QUEUE_RESERVE_BYTES
            ):
                raise BridgeError("TRANSCRIPT_TOO_LARGE")
            if self.sequence + len(changed_segments) > MAX_SESSION_ACTION_IDS - 1:
                raise BridgeError("SESSION_ACTION_LIMIT")
            for action_offset, ((segment_index, _segment), request) in enumerate(
                zip(changed_segments, requests, strict=True)
            ):
                response = self.broker_client.transcript_checkpoint(
                    capability,
                    request=request,
                    sequence=sequence + action_offset,
                    action_id=f"{action_id}-segment-{segment_index}",
                )
                response = _safe_response(
                    response,
                    handle=self._handle,
                    capability=self._capability,
                )
                _validated_write_response(
                    response, [effective_epoch, next_checkpoint]
                )
            if changed_segments:
                response = deepcopy(response)
                payload = response.get("payload")
                if isinstance(payload, Mapping):
                    response["payload"] = {
                        **payload,
                        "segments": len(segments),
                        "segments_written": len(changed_segments),
                    }
            updates = {
                "epoch": effective_epoch,
                "checkpoint": (
                    next_checkpoint if changed_segments else self.checkpoint
                ),
                "sealed_transcript": sealed_transcript,
                "epoch_content": content,
                "epoch_segments": segments,
            }
            if seal_epoch:
                updates = {
                    "epoch": effective_epoch + 1,
                    "checkpoint": 0,
                    "sealed_transcript": full_content,
                    "epoch_content": "",
                    "epoch_segments": [],
                }
            return response, completed_sequence, updates
        elif operation == "outcome":
            if set(value) != {"outcome"}:
                raise BridgeError("INPUT_INVALID")
            checkpoint = self.outcome_checkpoint + 1
            response = self.broker_client.retain_outcome(
                capability,
                request={
                    "document_id": (
                        f"session-{self._document_namespace}:outcome:{checkpoint}"
                    ),
                    "epoch": self.epoch,
                    "checkpoint": checkpoint,
                    "outcome": _bounded_text(value["outcome"]),
                },
                **common,
            )
            response = _validated_write_response(
                _safe_response(
                    response,
                    handle=self._handle,
                    capability=self._capability,
                ),
                [self.epoch, checkpoint],
            )
        elif operation == "reflect":
            if set(value) != {"reflection"}:
                raise BridgeError("INPUT_INVALID")
            response = self.broker_client.reflect(
                capability,
                request={"reflection": _bounded_text(value["reflection"])},
                timeout_seconds=5,
                **common,
            )
        elif operation == "status":
            if value:
                raise BridgeError("INPUT_INVALID")
            response = self.broker_client.session_status(
                capability, timeout_seconds=2, **common
            )
        else:
            if value:
                raise BridgeError("INPUT_INVALID")
            response = self.broker_client.session_close(
                capability, timeout_seconds=5, **common
            )
        return (
            response
            if operation == "outcome"
            else _safe_response(
                response, handle=self._handle, capability=self._capability
            ),
            sequence,
            updates,
        )

    def _complete_event(self, event_id: str) -> dict[str, Any]:
        event = self._events[event_id]
        operation = event["operation"]
        try:
            response, completed_sequence, updates = self._broker_call(
                operation,
                event["value"],
                sequence=event["sequence"],
                action_id=event["action_id"],
            )
        except BridgeError as error:
            if error.code in {
                "INPUT_INVALID",
                "SESSION_ACTION_LIMIT",
                "TRANSCRIPT_TOO_LARGE",
            }:
                self._events.pop(event_id, None)
            raise
        except BrokerError as error:
            if error.code == "RESPONSE_INVALID":
                raise BridgeError("BROKER_RESPONSE_INVALID") from error
            if error.code in {
                "CAPABILITY_INVALID",
                "EXPIRED",
                "METHOD_DENIED",
                "REQUEST_TOO_LARGE",
                "REVOKED",
                "SCHEMA_INVALID",
                "SESSION_ACTION_LIMIT",
            }:
                self._events.pop(event_id, None)
            raise BridgeError(error.code) from error
        except Exception as error:
            raise BridgeError("BRIDGE_UPSTREAM_UNAVAILABLE") from error
        self.sequence = completed_sequence
        if operation == "checkpoint":
            self.epoch = updates["epoch"]
            self.checkpoint = updates["checkpoint"]
            self._sealed_transcript = updates["sealed_transcript"]
            self._epoch_content = updates["epoch_content"]
            self._epoch_segments = updates["epoch_segments"]
        elif operation == "outcome":
            self.outcome_checkpoint += 1
        elif operation == "close":
            self.closed = True
        event["response"] = deepcopy(response)
        return response

    def dispatch(self, request: Any) -> dict[str, Any]:
        event_id, operation, value = self._validate_dispatch_request(request)
        fingerprint = _request_fingerprint(
            {"event_id": event_id, "operation": operation, "input": value}
        )
        pending_error: BridgeError | None = None
        existing = self._events.get(event_id)
        if existing is not None:
            if existing["fingerprint"] != fingerprint:
                raise BridgeError("EVENT_CONFLICT")
            if existing["response"] is not None:
                return deepcopy(existing["response"])
            sequence = existing["sequence"]
            action_id = existing["action_id"]
        else:
            if self.closed:
                raise BridgeError("BRIDGE_CLOSED")
            pending = next(
                (
                    pending_id
                    for pending_id, event in self._events.items()
                    if event["response"] is None
                ),
                None,
            )
            if pending is not None:
                try:
                    resumed = self._complete_event(pending)
                except BridgeError as error:
                    if operation != "close":
                        raise
                    pending_error = error
                if self.closed:
                    if operation == "close":
                        return resumed
                    raise BridgeError("BRIDGE_CLOSED")
            if len(self._events) >= MAX_EVENTS:
                if operation != "close":
                    raise BridgeError("EVENT_LIMIT")
                evictable = next(
                    (
                        prior_id
                        for prior_id, event in self._events.items()
                        if event["response"] is not None
                        and event["operation"] != "close"
                    ),
                    None,
                )
                if evictable is None:
                    raise BridgeError("EVENT_LIMIT")
                self._events.pop(evictable)
            sequence = (
                MAX_REQUEST_SEQUENCE
                if pending_error is not None
                else self.sequence + 1
            )
            action_id = f"bridge-{sequence}-{fingerprint[:16]}"
            self._events[event_id] = {
                "fingerprint": fingerprint,
                "sequence": sequence,
                "action_id": action_id,
                "operation": operation,
                "value": value,
                "response": None,
            }
        response = self._complete_event(event_id)
        if pending_error is not None:
            response = {
                **response,
                "pending_write": {
                    "disposition": "unavailable",
                    "diagnostic": {
                        "code": pending_error.code,
                        "visible": True,
                    },
                },
            }
            self._events[event_id]["response"] = deepcopy(response)
        return response


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    text = []
    for block in value:
        if (
            isinstance(block, Mapping)
            and block.get("type") in {"text", "input_text", "output_text"}
        ):
            item = block.get("text")
            if isinstance(item, str):
                text.append(item)
    return "\n".join(text)


def _clean_text(value: str) -> str:
    value = DELEGATION_BLOCK.sub(lambda match: match.group(1), value)
    value = MEMORY_BLOCK.sub("", value)
    value = SYNTHETIC_BLOCK.sub("", value)
    value = "\n".join(line.rstrip() for line in value.splitlines()).strip()
    if AGENT_INSTRUCTIONS.match(value):
        return ""
    return value


def _clean_transcript_record(raw_line: bytes) -> tuple[str, str] | None:
    try:
        entry = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(entry, Mapping):
        return None
    if entry.get("type") == "response_item":
        message = entry.get("payload")
        if not isinstance(message, Mapping) or message.get("type") != "message":
            return None
        role = message.get("role")
        content = message.get("content")
    else:
        role = entry.get("role") or entry.get("type")
        message = entry.get("message")
        if isinstance(message, Mapping):
            role = message.get("role") or role
            content = message.get("content")
        else:
            content = entry.get("content")
    if role not in {"user", "assistant"}:
        return None
    text = _clean_text(_text_content(content))
    if not text:
        return None
    return role, text


@dataclass(frozen=True)
class _TranscriptCursor:
    device: int
    inode: int
    offset: int
    size: int
    mtime_ns: int
    records: tuple[tuple[str, str], ...]


def _flatten_transcript_records(records: list[tuple[str, str]]) -> str:
    return "\n\n".join(
        f"{'User' if role == 'user' else 'Assistant'}: {text}"
        for role, text in records
    )


def _read_clean_transcript(
    path: str | Path,
    cursor: _TranscriptCursor | None = None,
) -> tuple[list[tuple[str, str]], _TranscriptCursor]:
    """Incrementally read a trusted JSONL transcript into structured dialogue."""

    selected = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(selected, flags)
    except OSError as error:
        raise BridgeError("TRANSCRIPT_UNAVAILABLE") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
            or info.st_nlink != 1
            or info.st_size > MAX_TRANSCRIPT_SOURCE_BYTES
        ):
            raise BridgeError("TRANSCRIPT_INVALID")
        same_file = (
            cursor is not None
            and (cursor.device, cursor.inode) == (info.st_dev, info.st_ino)
            and info.st_size >= cursor.offset
            and not (
                info.st_size == cursor.size
                and info.st_mtime_ns != cursor.mtime_ns
            )
        )
        messages = list(cursor.records) if same_file and cursor is not None else []
        offset = cursor.offset if same_file and cursor is not None else 0
        cleaned_bytes = len(_flatten_transcript_records(messages).encode("utf-8"))
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            source.seek(offset)
            while True:
                line_start = source.tell()
                raw_line = source.readline(MAX_TRANSCRIPT_RECORD_BYTES + 1)
                if not raw_line:
                    break
                if len(raw_line) > MAX_TRANSCRIPT_RECORD_BYTES:
                    raise BridgeError("TRANSCRIPT_RECORD_TOO_LARGE")
                if not raw_line.endswith(b"\n"):
                    try:
                        json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        offset = line_start
                        break
                message = _clean_transcript_record(raw_line)
                if message is None:
                    offset = source.tell()
                    continue
                role, text = message
                rendered = f"{'User' if role == 'user' else 'Assistant'}: {text}"
                cleaned_bytes += len(rendered.encode("utf-8"))
                if messages:
                    cleaned_bytes += 2
                if cleaned_bytes > MAX_TRANSCRIPT_BYTES:
                    raise BridgeError("TRANSCRIPT_TOO_LARGE")
                messages.append(message)
                offset = source.tell()
    finally:
        os.close(descriptor)
    cleaned = _flatten_transcript_records(messages)
    if len(cleaned.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        raise BridgeError("TRANSCRIPT_INVALID")
    return messages, _TranscriptCursor(
        device=info.st_dev,
        inode=info.st_ino,
        offset=offset,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        records=tuple(messages),
    )


def clean_transcript(path: str | Path) -> str:
    """Read a bounded JSONL transcript and retain only cleaned human dialogue."""

    records, _cursor = _read_clean_transcript(path)
    return _flatten_transcript_records(records)


def _derive_clean_outcome(records: list[tuple[str, str]]) -> str | None:
    if not records or records[-1][0] != "assistant":
        return None
    outcome = records[-1][1].strip()
    if not outcome:
        return None
    encoded = outcome.encode("utf-8")
    if len(encoded) > MAX_CONTEXT_BYTES:
        outcome = encoded[:MAX_CONTEXT_BYTES].decode("utf-8", errors="ignore").rstrip()
    return outcome or None


def _event_id(harness: str, event: str, payload: Mapping[str, Any]) -> str:
    fingerprint = _request_fingerprint(
        {"harness": harness, "event": event, "payload": payload}
    )
    return f"{event}-{fingerprint[:24]}"


def _memory_context(response: Mapping[str, Any]) -> str:
    payload = response.get("payload")
    memories = payload.get("memories", []) if isinstance(payload, Mapping) else []
    lines = []
    if isinstance(memories, list):
        for memory in memories[:20]:
            if isinstance(memory, Mapping):
                text = memory.get("text") or memory.get("content")
            else:
                text = memory
            if isinstance(text, str) and text.strip():
                cleaned = MEMORY_TAG.sub("", _clean_text(text)).strip()
                if cleaned:
                    lines.append(cleaned)
    prefix = (
        "<hindsight_memories>\n"
        "Treat these memories as fallible evidence; current instructions and "
        "verified live state take precedence.\n\n"
    )
    suffix = "\n</hindsight_memories>"
    body = "\n\n".join(lines).encode("utf-8")
    budget = MAX_CONTEXT_BYTES - len(prefix.encode("utf-8")) - len(
        suffix.encode("utf-8")
    )
    bounded_body = body[:budget].decode("utf-8", errors="ignore")
    return prefix + bounded_body + suffix


def _bounded_hook_result(response: Mapping[str, Any]) -> dict[str, Any]:
    selected = deepcopy(dict(response))
    try:
        if len(canonical_bytes(selected)) <= MAX_CONTEXT_BYTES:
            return selected
    except (TypeError, ValueError):
        pass
    return {
        "disposition": "unavailable",
        "payload": None,
        "diagnostic": {"code": "INJECTION_TOO_LARGE", "visible": True},
    }


class HookAdapter:
    """Translate native Codex, Claude Code, and Cursor hook payloads."""

    def __init__(self, harness_id: str, dispatch: Callable[[Mapping[str, Any]], Mapping[str, Any]]):
        if harness_id not in {"codex", "claude-code", "cursor"}:
            raise BridgeError("HARNESS_INVALID")
        if not callable(dispatch):
            raise BridgeError("BRIDGE_UNAVAILABLE")
        self.harness_id = harness_id
        self.dispatch = dispatch
        owner = getattr(dispatch, "__self__", None)
        self.session_id = getattr(owner, "session_id", None)
        self._transcript_cursors: dict[str, _TranscriptCursor] = {}

    def _session(self, payload: Mapping[str, Any]) -> str:
        if self.harness_id == "cursor":
            conversation_id = payload.get("conversation_id")
            native_session_id = payload.get("session_id")
            if (
                conversation_id is not None
                and native_session_id is not None
                and conversation_id != native_session_id
            ):
                raise BridgeError("SESSION_MISMATCH")
            supplied = (
                conversation_id
                if conversation_id is not None
                else native_session_id
            )
        else:
            supplied = payload.get("session_id")
        if supplied is None and self.session_id is not None:
            session_id = self.session_id
        else:
            session_id = _identifier(supplied, "SESSION_INVALID")
        if self.session_id is not None and session_id != self.session_id:
            raise BridgeError("SESSION_MISMATCH")
        return session_id

    def _dispatch(self, event: str, operation: str, value: Mapping[str, Any], payload: Mapping[str, Any]):
        return self.dispatch(
            {
                "event_id": _event_id(self.harness_id, event, payload),
                "operation": operation,
                "input": dict(value),
            }
        )

    @staticmethod
    def _ambient(response: Mapping[str, Any]) -> Mapping[str, Any]:
        if response.get("disposition") == "unavailable":
            diagnostic = response.get("diagnostic")
            code = (
                diagnostic.get("code")
                if isinstance(diagnostic, Mapping)
                else None
            )
            raise BridgeError(
                code if isinstance(code, str) and code else "MEMORY_UNAVAILABLE"
            )
        return response

    def handle(self, event: str, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or _forbidden_key(payload):
            raise BridgeError("INPUT_INVALID")
        self._session(payload)
        if event in {"recall", "tool-recall"}:
            if event == "recall" and self.harness_id == "cursor":
                raise BridgeError("OPERATION_DENIED")
            prompt = (
                payload.get("query")
                if event == "tool-recall"
                else payload.get("prompt") or payload.get("user_prompt")
            )
            response = self._dispatch(
                event, "recall", {"query": _bounded_text(prompt)}, payload
            )
            if event == "tool-recall":
                return _bounded_hook_result(response)
            self._ambient(response)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": _memory_context(response),
                }
            }
        if event == "session-start":
            if self.harness_id != "cursor":
                return {}
            roots = payload.get("workspace_roots") or []
            if not isinstance(roots, list) or any(not isinstance(root, str) for root in roots):
                raise BridgeError("INPUT_INVALID")
            projects = [Path(root).name for root in roots[:8] if root]
            query = (
                "Current engineering context for " + ", ".join(projects)
                if projects else "Current engineering context and operator preferences"
            )
            response = self._dispatch(event, "recall", {"query": query}, payload)
            self._ambient(response)
            return {"additional_context": _memory_context(response)}
        if event in {"checkpoint", "pre-compact", "close"}:
            transcript_path = payload.get("transcript_path")
            checkpoint_error: BridgeError | None = None
            outcome_error: BridgeError | None = None
            content = ""
            records: list[tuple[str, str]] = []
            checkpoint_identity: dict[str, Any] | None = None
            try:
                if not isinstance(transcript_path, str):
                    raise BridgeError("TRANSCRIPT_UNAVAILABLE")
                cursor = self._transcript_cursors.get(transcript_path)
                records, cursor = _read_clean_transcript(transcript_path, cursor)
                self._transcript_cursors[transcript_path] = cursor
                content = _flatten_transcript_records(records)
                if content:
                    checkpoint_identity = {
                        **dict(payload),
                        "transcript_digest": hashlib.sha256(
                            content.encode("utf-8")
                        ).hexdigest(),
                    }
                    response = self._dispatch(
                        event + "-transcript",
                        "checkpoint",
                        {
                            "content": content,
                            "seal_epoch": event == "pre-compact",
                        },
                        checkpoint_identity,
                    )
                    self._ambient(response)
            except BridgeError as error:
                if event != "close":
                    raise
                checkpoint_error = error
            except Exception as error:
                if event != "close":
                    raise
                checkpoint_error = BridgeError("TRANSCRIPT_UNAVAILABLE")
                checkpoint_error.__cause__ = error
            if event == "checkpoint":
                outcome = _derive_clean_outcome(records)
                if outcome is not None and checkpoint_identity is not None:
                    try:
                        outcome_response = self._dispatch(
                            "checkpoint-outcome",
                            "outcome",
                            {"outcome": outcome},
                            checkpoint_identity,
                        )
                        self._ambient(outcome_response)
                    except BridgeError as error:
                        outcome_error = error
                if outcome_error is not None:
                    raise outcome_error
            if event == "close":
                close_response = dict(self._dispatch(event, "close", {}, payload))
                if checkpoint_error is not None:
                    close_response["checkpoint"] = {
                        "disposition": "unavailable",
                        "diagnostic": {
                            "code": checkpoint_error.code,
                            "visible": True,
                        },
                    }
                return close_response
            return {}
        if event == "reflect":
            return _bounded_hook_result(
                self._dispatch(
                    event,
                    "reflect",
                    {"reflection": _bounded_text(payload.get("reflection"))},
                    payload,
                )
            )
        if event == "model":
            return _bounded_hook_result(
                self._dispatch(
                    event,
                    "model",
                    {"model_id": _identifier(payload.get("model_id"))},
                    payload,
                )
            )
        if event == "status":
            return _bounded_hook_result(
                self._dispatch(event, "status", {}, payload)
            )
        raise BridgeError("EVENT_UNSUPPORTED")


def _private_socket_parent(path: Path) -> None:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise BridgeError("SOCKET_PATH_INVALID")
    descriptor: int | None = None
    try:
        descriptor = open_trusted_parent(
            path.parent,
            unavailable_message="socket directory is unavailable",
            not_directory_message="socket directory is unsafe",
            owner_message="socket directory is unsafe",
            writable_message="socket directory is unsafe",
            create_missing=False,
        )
        parent = os.fstat(descriptor)
    except (FileEvidenceError, OSError) as error:
        raise BridgeError("SOCKET_DIRECTORY_UNSAFE") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o077
    ):
        raise BridgeError("SOCKET_DIRECTORY_UNSAFE")
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise BridgeError("SOCKET_PATH_INVALID") from error
    raise BridgeError("SOCKET_PATH_INVALID")


class UnixBridgeServer:
    """One-session, user-only JSON server for controller-owned hook adapters."""

    def __init__(
        self,
        socket_path: str | Path,
        bridge: SessionBridge,
        *,
        lifetime_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
    ) -> None:
        self.socket_path = Path(socket_path)
        if not isinstance(bridge, SessionBridge):
            raise BridgeError("BRIDGE_INVALID")
        if (
            type(lifetime_seconds) not in (int, float)
            or not 0 < lifetime_seconds <= MAX_SESSION_TTL_SECONDS
        ):
            raise BridgeError("TIMEOUT_INVALID")
        self.bridge = bridge
        self._deadline = time.monotonic() + float(lifetime_seconds)
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stopping.is_set()

    def start(self) -> None:
        if self._listener is not None:
            raise BridgeError("BRIDGE_ALREADY_RUNNING")
        _private_socket_parent(self.socket_path)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous_umask = os.umask(0o177)
        try:
            listener.bind(str(self.socket_path))
        except Exception:
            listener.close()
            raise
        finally:
            os.umask(previous_umask)
        os.chmod(self.socket_path, 0o600)
        listener.listen(8)
        listener.settimeout(0.2)
        self._listener = listener
        self._thread = threading.Thread(
            target=self._serve, name="hindsight-session-bridge", daemon=True
        )
        self._thread.start()

    @staticmethod
    def _read(connection: socket.socket) -> Any:
        chunks = bytearray()
        while True:
            chunk = connection.recv(
                min(65536, MAX_BRIDGE_REQUEST_BYTES + 1 - len(chunks))
            )
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > MAX_BRIDGE_REQUEST_BYTES:
                raise BridgeError("INPUT_INVALID")
        try:
            return json.loads(chunks)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BridgeError("INPUT_INVALID") from error

    def _serve(self) -> None:
        listener = self._listener
        if listener is None:
            return
        try:
            while not self._stopping.is_set():
                if time.monotonic() >= self._deadline:
                    self._stopping.set()
                    break
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with connection:
                    connection.settimeout(5)
                    try:
                        request = self._read(connection)
                        result = self.bridge.dispatch_native(request)
                        response = {"ok": True, "result": result}
                    except BridgeError as error:
                        response = {"ok": False, "error": error.code}
                    except Exception:
                        response = {"ok": False, "error": "BRIDGE_INTERNAL"}
                    try:
                        connection.sendall(canonical_bytes(response))
                    except OSError:
                        pass
                if self.bridge.closed:
                    self._stopping.set()
        finally:
            try:
                listener.close()
            except OSError:
                pass
            self._listener = None
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def close(self) -> None:
        self._stopping.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


class JsonBridgeClient:
    """Bounded client for one private session bridge locator."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5) -> None:
        self.socket_path = Path(socket_path)
        if not self.socket_path.is_absolute():
            raise BridgeError("SOCKET_PATH_INVALID")
        if type(timeout) not in (int, float) or not 0 < timeout <= 30:
            raise BridgeError("TIMEOUT_INVALID")
        self.timeout = float(timeout)

    def call_raw(self, request: Mapping[str, Any]) -> dict[str, Any]:
        try:
            encoded = canonical_bytes(request)
        except (TypeError, ValueError) as error:
            raise BridgeError("INPUT_INVALID") from error
        operation = request.get("operation")
        request_limit = (
            MAX_BRIDGE_REQUEST_BYTES
            if operation == "checkpoint"
            else MAX_INPUT_BYTES
        )
        if len(encoded) > request_limit:
            raise BridgeError("INPUT_INVALID")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        try:
            connection.connect(str(self.socket_path))
            connection.sendall(encoded)
            connection.shutdown(socket.SHUT_WR)
            chunks = bytearray()
            while True:
                chunk = connection.recv(
                    min(65536, MAX_INPUT_BYTES + 1 - len(chunks))
                )
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) > MAX_INPUT_BYTES:
                    raise BridgeError("BROKER_RESPONSE_INVALID")
        except OSError as error:
            raise BridgeError("BRIDGE_UNAVAILABLE") from error
        finally:
            connection.close()
        try:
            response = json.loads(chunks)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BridgeError("BROKER_RESPONSE_INVALID") from error
        if (
            not isinstance(response, dict)
            or response.get("ok") not in {True, False}
            or set(response)
            != ({"ok", "result"} if response.get("ok") is True else {"ok", "error"})
        ):
            raise BridgeError("BROKER_RESPONSE_INVALID")
        return response

    def call(self, request: Mapping[str, Any]) -> dict[str, Any]:
        response = self.call_raw(request)
        if response["ok"] is not True:
            raise BridgeError(response["error"])
        result = response["result"]
        if not isinstance(result, dict):
            raise BridgeError("BROKER_RESPONSE_INVALID")
        return result

    def call_native(
        self, harness_id: str, event: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self.call(
            {
                "harness_id": harness_id,
                "event": event,
                "payload": dict(payload),
            }
        )


def bridge_process_arguments(
    *,
    executable: str,
    state_dir: str | Path,
    bridge_socket: str | Path,
    broker_socket: str | Path,
    handle_fd: int,
    session_id: str,
    harness_id: str,
    lifetime_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
) -> list[str]:
    """Build bridge argv without placing the session handle in process arguments."""

    if type(handle_fd) is not int or handle_fd < 3:
        raise BridgeError("HANDLE_FD_INVALID")
    _identifier(session_id, "SESSION_INVALID")
    if harness_id not in {"codex", "claude-code", "cursor"}:
        raise BridgeError("HARNESS_INVALID")
    if (
        type(lifetime_seconds) not in (int, float)
        or not 0 < lifetime_seconds <= MAX_SESSION_TTL_SECONDS
    ):
        raise BridgeError("TIMEOUT_INVALID")
    paths = [Path(value) for value in (executable, state_dir, bridge_socket, broker_socket)]
    if any(not path.is_absolute() for path in paths):
        raise BridgeError("SOCKET_PATH_INVALID")
    return [
        str(paths[0]),
        "--state-dir",
        str(paths[1]),
        "bridge",
        "serve",
        "--socket",
        str(paths[2]),
        "--broker-socket",
        str(paths[3]),
        "--handle-fd",
        str(handle_fd),
        "--session-id",
        session_id,
        "--harness",
        harness_id,
        "--lifetime-seconds",
        str(float(lifetime_seconds)),
    ]


def sanitized_harness_environment(
    environment: Mapping[str, str], socket_path: str | Path
) -> dict[str, str]:
    """Remove controller secrets before exposing only a bridge locator."""

    selected = Path(socket_path)
    if not selected.is_absolute():
        raise BridgeError("SOCKET_PATH_INVALID")
    result = _sanitized_environment(environment)
    result["HINDSIGHT_MEMORY_BRIDGE_LOCATOR"] = str(selected)
    return result


def _sanitized_environment(environment: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise BridgeError("ENVIRONMENT_INVALID")
        if "HINDSIGHT" in key.upper():
            continue
        result[key] = value
    return result


def _sanitized_controller_environment(
    environment: Mapping[str, str]
) -> dict[str, str]:
    return _sanitized_environment(environment)


def start_bridge_process(
    *,
    executable: str,
    state_dir: str | Path,
    bridge_socket: str | Path,
    broker_socket: str | Path,
    handle: str,
    session_id: str,
    harness_id: str,
    detached: bool = False,
    lifetime_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
    environment: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start a bridge and send its one-use handle only over an inherited pipe."""

    if re.fullmatch(r"[0-9a-f]{64}", handle or "") is None:
        raise BridgeError("HANDLE_INVALID")
    read_descriptor, write_descriptor = os.pipe()
    try:
        arguments = bridge_process_arguments(
            executable=executable,
            state_dir=state_dir,
            bridge_socket=bridge_socket,
            broker_socket=broker_socket,
            handle_fd=read_descriptor,
            session_id=session_id,
            harness_id=harness_id,
            lifetime_seconds=lifetime_seconds,
        )
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL if detached else None,
            close_fds=True,
            pass_fds=(read_descriptor,),
            start_new_session=detached,
            env=_sanitized_controller_environment(
                os.environ if environment is None else environment
            ),
        )
    except BaseException:
        os.close(write_descriptor)
        raise
    finally:
        os.close(read_descriptor)
    try:
        payload = handle.encode("ascii")
        offset = 0
        while offset < len(payload):
            offset += os.write(write_descriptor, payload[offset:])
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired as error:
                raise BridgeError("BRIDGE_SHUTDOWN_FAILED") from error
        raise
    finally:
        os.close(write_descriptor)
    return process


def wait_for_bridge(
    socket_path: str | Path,
    process: subprocess.Popen[Any],
    *,
    timeout: float = 5,
) -> None:
    selected = Path(socket_path)
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise BridgeError("TIMEOUT_INVALID")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise BridgeError("BRIDGE_START_FAILED")
        try:
            info = selected.lstat()
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        if (
            stat.S_ISSOCK(info.st_mode)
            and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o600
        ):
            return
        raise BridgeError("SOCKET_PATH_INVALID")
    raise BridgeError("BRIDGE_START_TIMEOUT")


def allocate_bridge_socket(
    bridge_dir: str | Path, *, session_id: str, harness_id: str
) -> Path:
    directory = Path(bridge_dir)
    _private_directory(directory, "SOCKET_DIRECTORY_UNSAFE")
    if harness_id not in {"codex", "claude-code", "cursor"}:
        raise BridgeError("HARNESS_INVALID")
    session = _identifier(session_id, "SESSION_INVALID")
    identity = hashlib.sha256(
        f"{harness_id}:{session}".encode("utf-8")
    ).hexdigest()[:16]
    selected = directory / f"bridge-{identity}-{os.urandom(8).hex()}.sock"
    if len(os.fsencode(selected)) >= 100:
        raise BridgeError("SOCKET_PATH_INVALID")
    return selected


def _locator_name(session_id: str) -> str:
    selected = _identifier(session_id, "SESSION_INVALID")
    return hashlib.sha256(selected.encode("utf-8")).hexdigest() + ".json"


def _private_directory(path: Path, code: str) -> None:
    if not path.is_absolute():
        raise BridgeError(code)
    descriptor: int | None = None
    try:
        descriptor = open_trusted_parent(
            path,
            unavailable_message=code,
            not_directory_message=code,
            owner_message=code,
            writable_message=code,
            create_missing=False,
        )
        info = os.fstat(descriptor)
    except (FileEvidenceError, OSError) as error:
        raise BridgeError(code) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise BridgeError(code)


def _envelope_name(session_id: str) -> str:
    return _locator_name(session_id).removesuffix(".json") + ".envelope.json"


def _reservation_name(session_id: str) -> str:
    return _locator_name(session_id).removesuffix(".json") + ".reservation"


def _gui_envelope_claims(
    directory: Path, name: str
) -> list[tuple[Path, int]]:
    pattern = re.compile(
        rf"\.{re.escape(name)}\.consuming\.([1-9][0-9]*)\.[0-9a-f]{{16}}\Z"
    )
    claims = []
    for candidate in directory.iterdir():
        match = pattern.fullmatch(candidate.name)
        if match is not None:
            claims.append((candidate, int(match.group(1))))
    return sorted(claims, key=lambda item: item[0].name)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _claim_gui_envelope(directory: Path, name: str) -> Path:
    claim = directory / (
        f".{name}.consuming.{os.getpid()}.{os.urandom(8).hex()}"
    )
    try:
        os.rename(directory / name, claim)
    except FileNotFoundError:
        existing = _gui_envelope_claims(directory, name)
        if len(existing) != 1 or _process_exists(existing[0][1]):
            raise BridgeError("ENVELOPE_UNAVAILABLE") from None
        try:
            os.rename(existing[0][0], claim)
        except OSError as error:
            raise BridgeError("ENVELOPE_UNAVAILABLE") from error
    except OSError as error:
        raise BridgeError("ENVELOPE_UNAVAILABLE") from error
    return claim


def reserve_gui_envelope_slot(
    locator_dir: str | Path, *, session_id: str
) -> Path:
    """Reserve one session identity before minting any broker authority."""

    directory = Path(locator_dir)
    _private_directory(directory, "LOCATOR_DIRECTORY_UNSAFE")
    remove_expired_gui_envelope(directory, session_id=session_id)
    envelope_name = _envelope_name(session_id)
    if (
        (directory / envelope_name).exists()
        or _gui_envelope_claims(directory, envelope_name)
    ):
        raise BridgeError("ENVELOPE_EXISTS")
    reservation = directory / _reservation_name(session_id)
    for attempt in range(2):
        try:
            descriptor = os.open(
                reservation,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError as error:
            if attempt:
                raise BridgeError("ENVELOPE_EXISTS") from error
            try:
                observed = reservation.lstat()
            except FileNotFoundError:
                continue
            if time.time() - observed.st_mtime <= GUI_RESERVATION_SECONDS:
                raise BridgeError("ENVELOPE_EXISTS") from error
            try:
                current = reservation.lstat()
            except FileNotFoundError:
                continue
            if (current.st_dev, current.st_ino) == (observed.st_dev, observed.st_ino):
                reservation.unlink()
            continue
        else:
            os.close(descriptor)
            if (
                (directory / envelope_name).exists()
                or _gui_envelope_claims(directory, envelope_name)
            ):
                reservation.unlink(missing_ok=True)
                raise BridgeError("ENVELOPE_EXISTS")
            return reservation
    raise BridgeError("ENVELOPE_EXISTS")


def release_gui_envelope_slot(reservation: str | Path) -> None:
    Path(reservation).unlink(missing_ok=True)


def write_gui_envelope(
    locator_dir: str | Path,
    *,
    session_id: str,
    harness_id: str,
    handle: str,
    state_dir: str | Path,
    broker_socket: str | Path,
    bridge_dir: str | Path,
    expires_at: float,
) -> Path:
    """Stage one controller-only capability envelope for first-hook exchange."""

    directory = Path(locator_dir)
    _private_directory(directory, "LOCATOR_DIRECTORY_UNSAFE")
    if harness_id not in {"codex", "claude-code", "cursor"}:
        raise BridgeError("HARNESS_INVALID")
    if not re.fullmatch(r"[0-9a-f]{64}", handle or ""):
        raise BridgeError("HANDLE_INVALID")
    paths = {
        "state_dir": Path(state_dir),
        "broker_socket": Path(broker_socket),
        "bridge_dir": Path(bridge_dir),
    }
    if any(not path.is_absolute() for path in paths.values()):
        raise BridgeError("ENVELOPE_INVALID")
    remaining = expires_at - time.time() if type(expires_at) in {int, float} else 0
    if not 0 < remaining <= MAX_SESSION_TTL_SECONDS:
        raise BridgeError("TIMEOUT_INVALID")
    name = _envelope_name(session_id)
    destination = directory / name
    record = {
        "schema_version": 1,
        "harness_id": harness_id,
        "session_digest": name.removesuffix(".envelope.json"),
        "handle": handle,
        **{key: str(value) for key, value in paths.items()},
        "expires_at": float(expires_at),
    }
    temporary = directory / f".{name}.{os.getpid()}.{os.urandom(8).hex()}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        payload = canonical_bytes(record)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise BridgeError("ENVELOPE_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
    except FileExistsError as error:
        temporary.unlink(missing_ok=True)
        raise BridgeError("ENVELOPE_EXISTS") from error
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def remove_expired_gui_envelope(
    locator_dir: str | Path, *, session_id: str
) -> bool:
    """Remove an abandoned envelope only when the same inode is expired."""

    directory = Path(locator_dir)
    _private_directory(directory, "LOCATOR_DIRECTORY_UNSAFE")
    name = _envelope_name(session_id)
    path = directory / name
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        claims = _gui_envelope_claims(directory, name)
        if len(claims) != 1 or _process_exists(claims[0][1]):
            return False
        path = claims[0][0]
        try:
            descriptor = os.open(
                path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
        except FileNotFoundError:
            return False
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > 8192
        ):
            raise BridgeError("ENVELOPE_INVALID")
        chunks = bytearray()
        while True:
            chunk = os.read(descriptor, min(8193 - len(chunks), 8192))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > 8192:
                raise BridgeError("ENVELOPE_INVALID")
        raw = bytes(chunks)
    finally:
        os.close(descriptor)
    try:
        record = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BridgeError("ENVELOPE_INVALID") from error
    expires_at = record.get("expires_at") if isinstance(record, Mapping) else None
    if type(expires_at) not in {int, float}:
        raise BridgeError("ENVELOPE_INVALID")
    if expires_at > time.time():
        return False
    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
        return False
    path.unlink()
    return True


@contextmanager
def consume_gui_envelope(
    locator_dir: str | Path, *, session_id: str, harness_id: str
) -> Iterator[dict[str, Any]]:
    """Claim one envelope until activation succeeds or the consumer exits."""

    directory = Path(locator_dir)
    _private_directory(directory, "LOCATOR_DIRECTORY_UNSAFE")
    name = _envelope_name(session_id)
    consuming = _claim_gui_envelope(directory, name)
    try:
        descriptor = os.open(
            consuming, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > 8192
            ):
                raise BridgeError("ENVELOPE_INVALID")
            chunks = bytearray()
            while True:
                chunk = os.read(descriptor, min(8193 - len(chunks), 8192))
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) > 8192:
                    raise BridgeError("ENVELOPE_INVALID")
        finally:
            os.close(descriptor)
        try:
            record = json.loads(chunks)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BridgeError("ENVELOPE_INVALID") from error
        required = {
            "schema_version", "harness_id", "session_digest", "handle",
            "state_dir", "broker_socket", "bridge_dir", "expires_at",
        }
        if (
            not isinstance(record, dict)
            or set(record) != required
            or record.get("schema_version") != 1
            or record.get("harness_id") != harness_id
            or record.get("session_digest") != name.removesuffix(".envelope.json")
            or not re.fullmatch(r"[0-9a-f]{64}", record.get("handle", ""))
            or any(
                not isinstance(record.get(key), str)
                or not Path(record[key]).is_absolute()
                for key in ("state_dir", "broker_socket", "bridge_dir")
            )
            or type(record.get("expires_at")) not in {int, float}
        ):
            raise BridgeError("ENVELOPE_INVALID")
        yield record
    finally:
        consuming.unlink(missing_ok=True)


def remove_bridge_locator(
    locator_dir: str | Path,
    *,
    session_id: str,
    harness_id: str | None = None,
    expected_socket: str | Path | None = None,
) -> None:
    directory = Path(locator_dir)
    _private_directory(directory, "LOCATOR_DIRECTORY_UNSAFE")
    path = directory / _locator_name(session_id)
    if expected_socket is not None:
        if harness_id not in {"codex", "claude-code", "cursor"}:
            raise BridgeError("HARNESS_INVALID")
        try:
            observed = read_bridge_locator(
                directory,
                session_id=session_id,
                harness_id=harness_id,
            )
        except BridgeError:
            return
        if observed != Path(expected_socket):
            return
    path.unlink(missing_ok=True)


def write_bridge_locator(
    locator_dir: str | Path,
    *,
    session_id: str,
    harness_id: str,
    socket_path: str | Path,
) -> Path:
    """Publish a non-secret GUI locator using an atomic user-only artifact."""

    directory = Path(locator_dir)
    _private_directory(directory, "LOCATOR_DIRECTORY_UNSAFE")
    if harness_id not in {"codex", "claude-code", "cursor"}:
        raise BridgeError("HARNESS_INVALID")
    selected_socket = Path(socket_path)
    if not selected_socket.is_absolute():
        raise BridgeError("SOCKET_PATH_INVALID")
    name = _locator_name(session_id)
    destination = directory / name
    record = {
        "schema_version": 1,
        "harness_id": harness_id,
        "session_digest": name.removesuffix(".json"),
        "bridge_locator": str(selected_socket),
    }
    temporary = directory / f".{name}.{os.getpid()}.{os.urandom(8).hex()}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        payload = canonical_bytes(record)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise BridgeError("LOCATOR_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError as error:
            raise BridgeError("LOCATOR_EXISTS") from error
        temporary.unlink()
        directory_descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return destination


def read_bridge_locator(
    locator_dir: str | Path, *, session_id: str, harness_id: str
) -> Path:
    """Resolve a GUI session locator without reading any envelope material."""

    directory = Path(locator_dir)
    _private_directory(directory, "LOCATOR_DIRECTORY_UNSAFE")
    name = _locator_name(session_id)
    path = directory / name
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError as error:
        raise BridgeError("LOCATOR_UNAVAILABLE") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > 4096
        ):
            raise BridgeError("LOCATOR_INVALID")
        chunks = bytearray()
        while True:
            chunk = os.read(descriptor, min(4097 - len(chunks), 4096))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > 4096:
                raise BridgeError("LOCATOR_INVALID")
        raw = bytes(chunks)
    finally:
        os.close(descriptor)
    try:
        record = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BridgeError("LOCATOR_INVALID") from error
    expected_digest = name.removesuffix(".json")
    if (
        not isinstance(record, dict)
        or set(record)
        != {"schema_version", "harness_id", "session_digest", "bridge_locator"}
        or record.get("schema_version") != 1
        or record.get("harness_id") != harness_id
        or record.get("session_digest") != expected_digest
        or not isinstance(record.get("bridge_locator"), str)
    ):
        raise BridgeError("LOCATOR_MISMATCH")
    selected = Path(record["bridge_locator"])
    if not selected.is_absolute():
        raise BridgeError("LOCATOR_INVALID")
    return selected
