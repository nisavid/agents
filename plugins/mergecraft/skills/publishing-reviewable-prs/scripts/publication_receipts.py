"""Ordered, private evidence for verified reviewable-PR publication."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping

from required_review import (
    PublicationCandidate,
    PublicationReview,
    parse_publication_review,
    validate_transition_candidate,
)
from reviewable_pr_state import (
    ExpectedIdentity,
    PublicationError,
    identity_matches,
    validate_identity_inputs,
)

SCHEMA_VERSION = 3
LEGACY_SCHEMA_VERSION = 2
PUBLISHER_NAME = "publishing-reviewable-prs"
PUBLISHER_VERSION = 1
POLICY_VERSION = 1
PROVENANCE_CANONICAL = "canonical"
PROVENANCE_RECONCILED_UNRECEIPTED = "reconciled-unreceipted"
CANONICAL_OPERATIONS = {"create", "update-text", "mark-ready"}
OPERATIONS = CANONICAL_OPERATIONS | {"reconcile"}
HEX_64_RE = re.compile(r"[0-9a-f]{64}")
UUID4_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")
RECEIPT_NAME_RE = re.compile(
    r"(?P<sequence>[0-9]{8})-(?P<content>[0-9a-f]{64})-"
    r"(?P<receipt>[0-9a-f-]{36})\.json"
)
TEMP_NAME_RE = re.compile(
    r"\.pending-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\.tmp"
)
MAX_FUTURE_SKEW = timedelta(minutes=5)


class ReceiptError(PublicationError):
    """Publication evidence cannot be prepared, stored, or verified safely."""


@dataclass
class LedgerLease:
    root: Path
    identity_key: str
    exclusive: bool
    active: bool = True


@dataclass(frozen=True)
class StateSnapshot:
    title_sha256: str
    body_sha256: str
    is_draft: bool
    state: Literal["OPEN"]

    def as_json(self) -> dict[str, Any]:
        return {
            "title_sha256": self.title_sha256,
            "body_sha256": self.body_sha256,
            "is_draft": self.is_draft,
            "state": self.state,
        }


@dataclass(frozen=True)
class _VerifiedTransition:
    expected: ExpectedIdentity
    operation: Literal["create", "update-text", "mark-ready", "reconcile"]
    preimage: StateSnapshot
    final_state: StateSnapshot
    review_input_schema_version: int
    review_input_sha256: str
    review: PublicationReview | None
    candidate: PublicationCandidate | None


@dataclass(frozen=True)
class PublicationReceipt:
    schema_version: int
    sequence: int
    receipt_id: str
    content_sha256: str
    predecessor_sha256: str | None
    provenance: Literal["canonical", "reconciled-unreceipted"]
    operation: Literal["create", "update-text", "mark-ready", "reconcile"]
    created_at: str
    expected: ExpectedIdentity
    review_input_schema_version: int
    review_input_sha256: str
    preimage: StateSnapshot
    final_state: StateSnapshot
    review: PublicationReview | None

    def unsigned_json(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "sequence": self.sequence,
            "receipt_id": self.receipt_id,
            "predecessor_sha256": self.predecessor_sha256,
            "provenance": self.provenance,
            "operation": self.operation,
            "created_at": self.created_at,
            "publisher": {"name": PUBLISHER_NAME, "version": PUBLISHER_VERSION},
            "policy": {"version": POLICY_VERSION},
            "identity": {
                "repository": self.expected.repository,
                "pr_number": self.expected.pr_number,
                "url": self.expected.url,
                "base": self.expected.base,
                "base_oid": self.expected.base_oid,
                "head": self.expected.head,
                "head_oid": self.expected.head_oid,
                "head_owner": self.expected.head_owner,
                "head_repository": self.expected.head_repository,
            },
            "review_input": {
                "schema_version": self.review_input_schema_version,
                "content_sha256": self.review_input_sha256,
            },
            "preimage": self.preimage.as_json(),
            "final_state": self.final_state.as_json(),
        }
        if self.schema_version >= SCHEMA_VERSION:
            value["review"] = self.review.as_json() if self.review is not None else None
        return value

    def as_json(self) -> dict[str, Any]:
        value = self.unsigned_json()
        value["content_sha256"] = self.content_sha256
        return value

    def summary(self, status: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": status,
            "receipt_id": self.receipt_id,
            "provenance": self.provenance,
            "sequence": self.sequence,
        }
        if self.provenance == PROVENANCE_RECONCILED_UNRECEIPTED:
            result["review"] = {"state": "unwitnessed-reconciliation"}
        elif self.schema_version == LEGACY_SCHEMA_VERSION:
            result["review"] = {"state": "legacy-unrecorded"}
        else:
            assert self.review is not None
            result["review"] = self.review.as_json()
        return result


@dataclass(frozen=True)
class AuditResult:
    status: Literal["verified", "drift", "unavailable"]
    receipt: PublicationReceipt | None
    reason: str | None = None

    def as_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": self.status}
        if self.receipt is not None:
            result.update(self.receipt.summary(self.status))
            result["identity_epoch"] = self.receipt.unsigned_json()["identity"]
            result["final"] = {
                "is_draft": self.receipt.final_state.is_draft,
                "title_sha256": self.receipt.final_state.title_sha256,
                "body_sha256": self.receipt.final_state.body_sha256,
            }
            result["review_input_sha256"] = self.receipt.review_input_sha256
        if self.reason is not None:
            result["reason"] = self.reason
        return result


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parsed_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


def _next_timestamp(receipts: list[PublicationReceipt]) -> str:
    now = datetime.now(timezone.utc)
    if receipts:
        predecessor = _parsed_timestamp(receipts[-1].created_at)
        if now <= predecessor:
            now = predecessor + timedelta(microseconds=1)
    return _timestamp(now)


def _new_uuid4() -> str:
    return str(uuid.uuid4())


def _identity_key(expected: ExpectedIdentity) -> str:
    return hashlib.sha256(
        f"{expected.repository}\0{expected.pr_number}".encode("utf-8")
    ).hexdigest()


def _creation_key(
    *,
    repository: str,
    base: str,
    head: str,
    head_owner: str,
    head_repository: str,
) -> str:
    return hashlib.sha256(
        "\0".join((repository, base, head, head_owner, head_repository)).encode("utf-8")
    ).hexdigest()


def resolve_receipt_root(
    override: Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve one canonical root; overrides exist only for tests and migration."""

    if override is not None:
        root = override
    else:
        values = os.environ if environment is None else environment
        xdg_state_home = values.get("XDG_STATE_HOME")
        if xdg_state_home:
            root = Path(xdg_state_home) / "mergecraft/pr-publication-receipts"
        else:
            root = (
                home or Path.home()
            ) / ".local/state/mergecraft/pr-publication-receipts"
    if not root.is_absolute():
        raise ReceiptError("receipt root must resolve to an absolute path")
    return root


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ReceiptError("cannot durably synchronize receipt storage") from error


def _private_directory(path: Path, *, create: bool, parent: Path | None = None) -> Path:
    created = False
    if create:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
            created = True
        except FileExistsError:
            pass
        except OSError as error:
            raise ReceiptError("cannot create receipt storage") from error
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ReceiptError("receipt storage is unavailable") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise ReceiptError("receipt storage must be a private owned directory")
    if created:
        _fsync_directory(parent or path.parent)
    return path


def _write_probe(root: Path) -> None:
    name = f".pending-{_new_uuid4()}.tmp"
    path = root / name
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.write(descriptor, b"receipt-store-probe\n")
        os.fsync(descriptor)
    except OSError as error:
        raise ReceiptError("receipt storage is not durably writable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise ReceiptError("cannot clean receipt storage probe") from error
    _fsync_directory(root)


def prepare_receipt_store(override: Path | None = None) -> Path:
    """Prepare and prove the private store before any forge mutation."""

    root = resolve_receipt_root(override)
    root = _private_directory(root, create=True)
    _write_probe(root)
    return root


def prepare_receipt_ledger(root: Path, expected: ExpectedIdentity) -> None:
    """Validate and prove the exact PR ledger before its forge mutation."""

    root = _private_directory(root, create=False)
    directory = _ledger_directory(root, expected, create=True)
    load_receipts(root, expected)
    _write_probe(directory)


@contextmanager
def _receipt_lock(
    root: Path, identity_key: str, *, exclusive: bool
) -> Iterator[LedgerLease]:
    root = _private_directory(root, create=False)
    locks = _private_directory(root / ".locks", create=True, parent=root)
    path = locks / f"{identity_key}.lock"
    descriptor: int | None = None
    lease = LedgerLease(
        root=root,
        identity_key=identity_key,
        exclusive=exclusive,
    )
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ReceiptError("receipt lock must be a private owned regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield lease
    except OSError as error:
        raise ReceiptError("cannot acquire publication receipt lock") from error
    finally:
        lease.active = False
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def receipt_ledger_lock(
    root: Path, expected: ExpectedIdentity, *, exclusive: bool = True
) -> Iterator[LedgerLease]:
    """Serialize one PR from final preflight through receipt append."""

    with _receipt_lock(
        root,
        f"ledger-{_identity_key(expected)}",
        exclusive=exclusive,
    ) as lease:
        yield lease


@contextmanager
def creation_transaction_lock(
    root: Path,
    *,
    repository: str,
    base: str,
    head: str,
    head_owner: str,
    head_repository: str,
) -> Iterator[None]:
    """Serialize create discovery and transport for one exact head/base."""

    key = _creation_key(
        repository=repository,
        base=base,
        head=head,
        head_owner=head_owner,
        head_repository=head_repository,
    )
    with _receipt_lock(root, f"create-{key}", exclusive=True):
        yield


def _existing_root(override: Path | None) -> Path:
    root = resolve_receipt_root(override)
    try:
        return _private_directory(root, create=False)
    except ReceiptError:
        if not root.exists():
            return root
        raise


def _ledger_directory(root: Path, expected: ExpectedIdentity, *, create: bool) -> Path:
    directory = root / _identity_key(expected)
    if not create and not directory.exists():
        return directory
    return _private_directory(directory, create=create, parent=root)


def _snapshot(stored: dict[str, Any], expected: ExpectedIdentity) -> StateSnapshot:
    title = stored.get("title")
    body = stored.get("body")
    is_draft = stored.get("isDraft")
    state = stored.get("state")
    if (
        not identity_matches(stored, expected)
        or not isinstance(title, str)
        or not isinstance(body, str)
        or type(is_draft) is not bool
        or state != "OPEN"
    ):
        raise ReceiptError("cannot bind an invalid or unverified PR state")
    return StateSnapshot(
        title_sha256=_digest(title),
        body_sha256=_digest(body),
        is_draft=is_draft,
        state="OPEN",
    )


def _validate_transition_shape(
    operation: str, before: StateSnapshot, after: StateSnapshot
) -> None:
    if operation in CANONICAL_OPERATIONS and before == after:
        raise ReceiptError("canonical publication requires an actual state transition")
    if operation == "create" and (
        not before.is_draft
        or not after.is_draft
        or before.title_sha256 != after.title_sha256
        or before.body_sha256 == after.body_sha256
    ):
        raise ReceiptError("create transition is inconsistent")
    if operation == "update-text" and (
        before.is_draft != after.is_draft
        or (
            before.title_sha256 == after.title_sha256
            and before.body_sha256 == after.body_sha256
        )
    ):
        raise ReceiptError("text transition is inconsistent")
    if operation == "mark-ready" and (
        not before.is_draft
        or after.is_draft
        or before.title_sha256 != after.title_sha256
        or before.body_sha256 != after.body_sha256
    ):
        raise ReceiptError("ready transition is inconsistent")
    if operation == "reconcile" and before != after:
        raise ReceiptError("reconciliation rereads are inconsistent")


def verified_transition(
    *,
    expected: ExpectedIdentity,
    operation: Literal["create", "update-text", "mark-ready", "reconcile"],
    preimage: dict[str, Any],
    final_reread: dict[str, Any],
    review_input_schema_version: int,
    review_input_sha256: str,
    review: PublicationReview | None,
    candidate: PublicationCandidate | None,
) -> _VerifiedTransition:
    """Bind exact before/after evidence before it can receive provenance."""

    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ReceiptError("unsupported publication operation")
    if (
        type(review_input_schema_version) is not int
        or review_input_schema_version <= 0
        or not isinstance(review_input_sha256, str)
        or HEX_64_RE.fullmatch(review_input_sha256) is None
    ):
        raise ReceiptError("review-input evidence is invalid")
    before = _snapshot(preimage, expected)
    after = _snapshot(final_reread, expected)
    _validate_transition_shape(operation, before, after)
    if operation in CANONICAL_OPERATIONS and not isinstance(review, PublicationReview):
        raise ReceiptError("canonical publication review choice is absent")
    if operation in CANONICAL_OPERATIONS and not isinstance(
        candidate, PublicationCandidate
    ):
        raise ReceiptError("canonical publication candidate is absent")
    if operation == "reconcile" and review is not None:
        raise ReceiptError("reconciliation cannot mint publication review provenance")
    if operation == "reconcile" and candidate is not None:
        raise ReceiptError("reconciliation cannot mint a publication candidate")
    if review is not None and candidate is not None:
        try:
            validate_transition_candidate(
                candidate=candidate,
                expected=expected,
                operation=operation,
                review_input_schema_version=review_input_schema_version,
                review_input_sha256=review_input_sha256,
                final_title_sha256=after.title_sha256,
                final_body_sha256=after.body_sha256,
                review=review,
            )
        except PublicationError as error:
            raise ReceiptError("publication candidate evidence is invalid") from error
    return _VerifiedTransition(
        expected=expected,
        operation=operation,
        preimage=before,
        final_state=after,
        review_input_schema_version=review_input_schema_version,
        review_input_sha256=review_input_sha256,
        review=review,
        candidate=candidate,
    )


def _strict_json(raw: bytes) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReceiptError("receipt contains a duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_: str) -> None:
        raise ReceiptError("receipt contains a non-finite JSON value")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReceiptError("receipt contains invalid JSON") from error
    if not isinstance(value, dict):
        raise ReceiptError("receipt must be a JSON object")
    return value


def _exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise ReceiptError(f"receipt {label} has an invalid schema")


def _parse_snapshot(value: Any, label: str) -> StateSnapshot:
    if not isinstance(value, dict):
        raise ReceiptError(f"receipt {label} has an invalid schema")
    _exact_keys(value, {"title_sha256", "body_sha256", "is_draft", "state"}, label)
    if (
        not isinstance(value["title_sha256"], str)
        or HEX_64_RE.fullmatch(value["title_sha256"]) is None
        or not isinstance(value["body_sha256"], str)
        or HEX_64_RE.fullmatch(value["body_sha256"]) is None
        or type(value["is_draft"]) is not bool
        or value["state"] != "OPEN"
    ):
        raise ReceiptError(f"receipt {label} has an invalid value")
    return StateSnapshot(
        title_sha256=value["title_sha256"],
        body_sha256=value["body_sha256"],
        is_draft=value["is_draft"],
        state="OPEN",
    )


def _parse_receipt(
    raw: bytes, path: Path, expected: ExpectedIdentity, *, now: datetime
) -> PublicationReceipt:
    value = _strict_json(raw)
    schema_version = value.get("schema_version")
    if type(schema_version) is not int or schema_version not in {
        LEGACY_SCHEMA_VERSION,
        SCHEMA_VERSION,
    }:
        raise ReceiptError("receipt has an unsupported schema version")
    root_keys = {
        "schema_version",
        "sequence",
        "receipt_id",
        "content_sha256",
        "predecessor_sha256",
        "provenance",
        "operation",
        "created_at",
        "publisher",
        "policy",
        "identity",
        "review_input",
        "preimage",
        "final_state",
    }
    if schema_version == SCHEMA_VERSION:
        root_keys.add("review")
    _exact_keys(
        value,
        root_keys,
        "root",
    )
    for label, keys in (
        ("publisher", {"name", "version"}),
        ("policy", {"version"}),
        (
            "identity",
            {
                "repository",
                "pr_number",
                "url",
                "base",
                "base_oid",
                "head",
                "head_oid",
                "head_owner",
                "head_repository",
            },
        ),
        ("review_input", {"schema_version", "content_sha256"}),
    ):
        if not isinstance(value[label], dict):
            raise ReceiptError(f"receipt {label} has an invalid schema")
        _exact_keys(value[label], keys, label)
    publisher = value["publisher"]
    policy = value["policy"]
    identity = value["identity"]
    review_input = value["review_input"]
    if (
        type(value["sequence"]) is not int
        or value["sequence"] <= 0
        or not isinstance(value["receipt_id"], str)
        or UUID4_RE.fullmatch(value["receipt_id"]) is None
        or str(uuid.UUID(value["receipt_id"])) != value["receipt_id"]
        or uuid.UUID(value["receipt_id"]).version != 4
        or not isinstance(value["content_sha256"], str)
        or HEX_64_RE.fullmatch(value["content_sha256"]) is None
        or value["predecessor_sha256"] is not None
        and (
            not isinstance(value["predecessor_sha256"], str)
            or HEX_64_RE.fullmatch(value["predecessor_sha256"]) is None
        )
        or not isinstance(value["provenance"], str)
        or value["provenance"]
        not in {PROVENANCE_CANONICAL, PROVENANCE_RECONCILED_UNRECEIPTED}
        or not isinstance(value["operation"], str)
        or value["operation"] not in OPERATIONS
        or not isinstance(value["created_at"], str)
        or TIMESTAMP_RE.fullmatch(value["created_at"]) is None
        or publisher != {"name": PUBLISHER_NAME, "version": PUBLISHER_VERSION}
        or policy != {"version": POLICY_VERSION}
        or type(identity["pr_number"]) is not int
        or identity["pr_number"] <= 0
        or any(
            not isinstance(identity[key], str) or not identity[key]
            for key in identity
            if key != "pr_number"
        )
        or type(review_input["schema_version"]) is not int
        or review_input["schema_version"] <= 0
        or not isinstance(review_input["content_sha256"], str)
        or HEX_64_RE.fullmatch(review_input["content_sha256"]) is None
    ):
        raise ReceiptError("receipt has an invalid value")
    try:
        created_at = datetime.strptime(
            value["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ReceiptError("receipt has an invalid timestamp") from error
    if created_at > now.astimezone(timezone.utc) + MAX_FUTURE_SKEW:
        raise ReceiptError("receipt timestamp is implausibly in the future")
    parsed_expected = ExpectedIdentity(
        repository=identity["repository"],
        pr_number=identity["pr_number"],
        base=identity["base"],
        base_oid=identity["base_oid"],
        head=identity["head"],
        head_oid=identity["head_oid"],
        head_owner=identity["head_owner"],
        head_repository=identity["head_repository"],
    )
    try:
        validate_identity_inputs(
            repository=parsed_expected.repository,
            pr_number=parsed_expected.pr_number,
            base=parsed_expected.base,
            base_oid=parsed_expected.base_oid,
            head=parsed_expected.head,
            head_oid=parsed_expected.head_oid,
            head_owner=parsed_expected.head_owner,
            head_repository=parsed_expected.head_repository,
        )
    except PublicationError as error:
        raise ReceiptError("receipt has an invalid identity") from error
    if (
        parsed_expected.repository != expected.repository
        or parsed_expected.pr_number != expected.pr_number
        or parsed_expected.head != expected.head
        or parsed_expected.head_owner != expected.head_owner
        or parsed_expected.head_repository != expected.head_repository
        or identity["url"] != parsed_expected.url
    ):
        raise ReceiptError("receipt identity does not match its ledger")
    if (
        value["provenance"] == PROVENANCE_CANONICAL
        and value["operation"] not in CANONICAL_OPERATIONS
    ) or (
        value["provenance"] == PROVENANCE_RECONCILED_UNRECEIPTED
        and value["operation"] != "reconcile"
    ):
        raise ReceiptError("receipt provenance does not match its operation")
    review: PublicationReview | None = None
    if schema_version == SCHEMA_VERSION:
        if value["provenance"] == PROVENANCE_CANONICAL:
            try:
                review = parse_publication_review(value["review"])
            except PublicationError as error:
                raise ReceiptError("receipt review evidence is invalid") from error
        elif value["review"] is not None:
            raise ReceiptError("reconciliation cannot contain review provenance")
    preimage = _parse_snapshot(value["preimage"], "preimage")
    final_state = _parse_snapshot(value["final_state"], "final state")
    _validate_transition_shape(value["operation"], preimage, final_state)
    unsigned = dict(value)
    del unsigned["content_sha256"]
    content_sha256 = hashlib.sha256(_canonical(unsigned)).hexdigest()
    if content_sha256 != value["content_sha256"]:
        raise ReceiptError("receipt content digest does not match its evidence")
    name_match = RECEIPT_NAME_RE.fullmatch(path.name)
    if (
        name_match is None
        or int(name_match.group("sequence")) != value["sequence"]
        or name_match.group("content") != content_sha256
        or name_match.group("receipt") != value["receipt_id"]
    ):
        raise ReceiptError("receipt filename does not match its evidence")
    if raw != _canonical(value) + b"\n":
        raise ReceiptError("receipt bytes are not canonical")
    return PublicationReceipt(
        schema_version=schema_version,
        sequence=value["sequence"],
        receipt_id=value["receipt_id"],
        content_sha256=content_sha256,
        predecessor_sha256=value["predecessor_sha256"],
        provenance=value["provenance"],
        operation=value["operation"],
        created_at=value["created_at"],
        expected=parsed_expected,
        review_input_schema_version=review_input["schema_version"],
        review_input_sha256=review_input["content_sha256"],
        preimage=preimage,
        final_state=final_state,
        review=review,
    )


def _read_private_file(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ReceiptError("cannot read receipt") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise ReceiptError("receipt must be a private owned regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(descriptor)


def load_receipts(
    root: Path | None, expected: ExpectedIdentity, *, now: datetime | None = None
) -> list[PublicationReceipt]:
    """Load and validate one complete ordered ledger; any fork or gap fails."""

    resolved = _existing_root(root)
    if not resolved.exists():
        return []
    directory = _ledger_directory(resolved, expected, create=False)
    if not directory.exists():
        return []
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as error:
        raise ReceiptError("cannot list receipt ledger") from error
    evidence: list[Path] = []
    for path in entries:
        if TEMP_NAME_RE.fullmatch(path.name):
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                # A concurrent preflight probe may disappear after it was
                # observed in the directory snapshot. It never constituted
                # receipt evidence.
                continue
            except OSError as error:
                raise ReceiptError("cannot inspect uncommitted receipt temp") from error
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
            ):
                raise ReceiptError("uncommitted receipt temp is not a private file")
            continue
        if RECEIPT_NAME_RE.fullmatch(path.name) is None:
            raise ReceiptError("receipt ledger contains an unexpected entry")
        evidence.append(path)
    receipts = [
        _parse_receipt(
            _read_private_file(path),
            path,
            expected,
            now=now or datetime.now(timezone.utc),
        )
        for path in evidence
    ]
    for index, receipt in enumerate(receipts, start=1):
        predecessor = receipts[index - 2].content_sha256 if index > 1 else None
        if receipt.sequence != index:
            raise ReceiptError("receipt ledger sequence has a gap or fork")
        if receipt.predecessor_sha256 != predecessor:
            raise ReceiptError("receipt ledger predecessor chain is invalid")
        if index > 1 and receipt.created_at <= receipts[index - 2].created_at:
            raise ReceiptError("receipt ledger timestamps are not strictly ordered")
        if (
            index > 1
            and receipts[index - 2].schema_version == SCHEMA_VERSION
            and receipt.schema_version == LEGACY_SCHEMA_VERSION
        ):
            raise ReceiptError("receipt ledger schema version moved backward")
    return receipts


def _append_receipt(
    *,
    root: Path,
    transition: _VerifiedTransition,
    provenance: Literal["canonical", "reconciled-unreceipted"],
    lease: LedgerLease,
) -> PublicationReceipt:
    root = _private_directory(root, create=False)
    if (
        not lease.active
        or not lease.exclusive
        or lease.root != root
        or lease.identity_key != f"ledger-{_identity_key(transition.expected)}"
    ):
        raise ReceiptError("an active exclusive ledger lease is required")
    receipts = load_receipts(root, transition.expected)
    sequence = len(receipts) + 1
    receipt_id = _new_uuid4()
    predecessor = receipts[-1].content_sha256 if receipts else None
    provisional = PublicationReceipt(
        schema_version=SCHEMA_VERSION,
        sequence=sequence,
        receipt_id=receipt_id,
        content_sha256="0" * 64,
        predecessor_sha256=predecessor,
        provenance=provenance,
        operation=transition.operation,
        created_at=_next_timestamp(receipts),
        expected=transition.expected,
        review_input_schema_version=transition.review_input_schema_version,
        review_input_sha256=transition.review_input_sha256,
        preimage=transition.preimage,
        final_state=transition.final_state,
        review=transition.review,
    )
    content_sha256 = hashlib.sha256(_canonical(provisional.unsigned_json())).hexdigest()
    receipt = PublicationReceipt(
        **{
            **provisional.__dict__,
            "content_sha256": content_sha256,
        }
    )
    directory = _ledger_directory(root, transition.expected, create=True)
    filename = f"{sequence:08d}-{content_sha256}-{receipt_id}.json"
    final_path = directory / filename
    temporary_path = directory / f".pending-{receipt_id}.tmp"
    descriptor: int | None = None
    linked = False
    try:
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        payload = _canonical(receipt.as_json()) + b"\n"
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(descriptor)
        os.link(temporary_path, final_path)
        linked = True
        temporary_path.unlink()
        _fsync_directory(directory)
    except OSError as error:
        raise ReceiptError("cannot atomically commit publication receipt") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        if not linked:
            try:
                final_path.unlink(missing_ok=True)
            except OSError:
                pass
    return receipt


def record_verified_publication(
    *, root: Path, transition: _VerifiedTransition, lease: LedgerLease
) -> PublicationReceipt:
    if transition.operation not in CANONICAL_OPERATIONS:
        raise ReceiptError(
            "only canonical publisher transitions can mint canonical provenance"
        )
    assert transition.review is not None
    assert transition.candidate is not None
    try:
        validate_transition_candidate(
            candidate=transition.candidate,
            expected=transition.expected,
            operation=transition.operation,
            review_input_schema_version=transition.review_input_schema_version,
            review_input_sha256=transition.review_input_sha256,
            final_title_sha256=transition.final_state.title_sha256,
            final_body_sha256=transition.final_state.body_sha256,
            review=transition.review,
        )
    except PublicationError as error:
        raise ReceiptError("publication candidate evidence is invalid") from error
    return _append_receipt(
        root=root,
        transition=transition,
        provenance=PROVENANCE_CANONICAL,
        lease=lease,
    )


def record_reconciliation(
    *, root: Path, transition: _VerifiedTransition, lease: LedgerLease
) -> PublicationReceipt:
    if transition.operation != "reconcile":
        raise ReceiptError(
            "reconciliation provenance requires a reconciliation transition"
        )
    receipts = load_receipts(root, transition.expected)
    if (
        receipts
        and receipts[-1].expected == transition.expected
        and receipts[-1].final_state == transition.final_state
    ):
        raise ReceiptError("authoritative latest receipt already matches live state")
    return _append_receipt(
        root=root,
        transition=transition,
        provenance=PROVENANCE_RECONCILED_UNRECEIPTED,
        lease=lease,
    )


def receipt_matches_live(receipt: PublicationReceipt, stored: dict[str, Any]) -> bool:
    try:
        return _snapshot(stored, receipt.expected) == receipt.final_state
    except ReceiptError:
        return False


def audit_publication(
    *,
    root: Path | None,
    expected: ExpectedIdentity,
    read_live: Callable[[], dict[str, Any]],
) -> AuditResult:
    try:
        receipts = load_receipts(root, expected)
    except ReceiptError:
        return AuditResult(
            status="unavailable",
            receipt=None,
            reason="receipt ledger is unavailable or invalid",
        )
    try:
        stored = read_live()
    except PublicationError:
        return AuditResult(
            status="unavailable",
            receipt=receipts[-1] if receipts else None,
            reason="live PR state is unavailable",
        )
    if not receipts:
        return AuditResult(
            status="drift",
            receipt=None,
            reason="no receipt exists for the exact PR identity",
        )
    latest = receipts[-1]
    if latest.expected != expected:
        return AuditResult(
            status="drift",
            receipt=latest,
            reason="latest receipt belongs to an older PR identity epoch",
        )
    if receipt_matches_live(latest, stored):
        return AuditResult(status="verified", receipt=latest)
    return AuditResult(
        status="drift",
        receipt=latest,
        reason="live PR state does not match the authoritative latest receipt",
    )
