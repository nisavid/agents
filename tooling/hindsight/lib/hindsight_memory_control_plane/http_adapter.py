"""Bounded, redacting HTTP implementation of the data-plane adapter contract."""

from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from copy import deepcopy
from datetime import datetime
from http.client import HTTPException
import json
import hashlib
import hmac
import math
import re
import socket
import ssl
import threading
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from .action_contracts import ACTION_METHODS, ARTIFACT_ACTION_KINDS
from .adapters import AdapterError, AuthenticationError, RollbackBundle, validate_runtime_request
from .canonical import StrictJsonError, canonical_bytes, digest, strict_json_loads
from .endpoint_host import is_bare_endpoint_host
from .inventory import _resolved_artifact
from .model import BankRef, EndpointIdentity, Inventory
from .planning import inventory_endpoint
from .planning import PlanError, _compatibility


MAX_VERIFIED_ROLLBACKS = 1024


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class _RuntimeInFlight:
    def __init__(self, request_digest: str) -> None:
        self.request_digest = request_digest
        self.event = threading.Event()
        self.result: Mapping[str, Any] | None = None
        self.error: BaseException | None = None


class HttpAdapter:
    ROLLBACK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
    READ_PATHS = {
        "schema_version": "/v1/schema",
        "read_config": "/v1/config",
        "read_stats": "/v1/stats",
        "read_tags": "/v1/tags",
        "read_scopes": "/v1/scopes",
        "read_documents": "/v1/documents",
        "read_models": "/v1/models",
        "read_directives": "/v1/directives",
        "read_operations": "/v1/operations",
        "read_invalidated_memories": "/v1/memories/invalidated",
        "read_compatibility": "/v1/compatibility",
    }
    MUTATION_PATHS = {
        "template_dry_run": ("POST", "/v1/templates/dry-run"),
        "import_template": ("POST", "/v1/templates/import"),
        "patch_config": ("PATCH", "/v1/config"),
        "upsert_model": ("PUT", "/v1/models"),
        "upsert_directive": ("PUT", "/v1/directives"),
        "transfer_documents": ("POST", "/v1/documents/transfer"),
        "reapply_invalidated_memories": ("POST", "/v1/memories/invalidated/reapply"),
        "delete_bank": ("DELETE", "/v1/banks"),
    }
    DIRECT_ACTION_PATHS = {
        "create_bank": ("POST", "/v1/banks"),
        "reload_profile": ("POST", "/v1/profiles/reload"),
        "report_unmanaged": ("POST", "/v1/unmanaged/report"),
    }
    PAGE_LIMIT = 1000
    MAX_DISCOVERY_ITEMS = 10_000
    MAX_DISCOVERY_PAGES = 10_000
    MAX_DISCOVERY_BYTES = 64 * 1024 * 1024
    SAFE_CONFIG_FIELDS = frozenset({
        "model", "provider", "recall_max_tokens", "retain_mission", "mission",
        "enable_observations", "entity_types", "max_tokens", "temperature",
        "top_p", "base_url", "host", "port", "revision", "dimensions",
    })
    DIGESTED_CONFIG_FIELDS = frozenset({"mission", "retain_mission"})
    SECRET_KEY_PARTS = frozenset(
        {"access", "authorization", "bearer", "credential", "key", "password", "secret", "token"}
    )
    SAFE_MIGRATION_TAG = re.compile(
        r"(?:agent|kind|lifecycle|repo|scope|source|workflow):"
        r"[a-z0-9][a-z0-9._-]{0,127}\Z"
    )
    SAFE_OBSERVATION_SCOPE = re.compile(
        r"(?:repo:[a-z0-9][a-z0-9-]{0,127}|scope:active)\Z"
    )
    SAFE_API_VERSION = re.compile(
        r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z"
    )
    MIGRATION_FEATURE_FIELDS = frozenset({
        "directives", "invalidated_memories", "mental_models",
        "migration_generation", "observations",
    })
    MIGRATION_STAT_FIELDS = frozenset({
        "total_directives", "total_documents", "total_entities",
        "total_invalidated_memories", "total_memories",
        "total_mental_models", "total_observations", "total_relationships",
    })
    SNAPSHOT_STATE_FIELDS = frozenset({
        "banks", "config", "directives", "documents",
        "invalidated_memories", "models", "profile_artifact_digest",
        "scopes", "stats", "tags", "template",
    })

    def __init__(self, *, inventory: Inventory, profile_id: str, token_resolver: Callable[[], str],
                 artifact_resolver: Callable[[Any], Mapping[str, Any]] | None = None,
                 external_action_reserver: Callable[[Mapping[str, Any]], None] | None = None,
                 timeout: float = 5.0, max_json_bytes: int = 1_048_576,
                 runtime_result_ttl_seconds: float = 300.0,
                 max_runtime_results: int = 1024,
                 max_runtime_result_bytes: int = 8_388_608,
                 max_runtime_inflight: int = 128,
                 max_recordings: int = 1024,
                 runtime_clock: Callable[[], float] = time.monotonic) -> None:
        if not isinstance(inventory, Inventory):
            raise AdapterError("validated inventory is required")
        raw = inventory.body()
        base_port = raw["machine"].get("base_port", 7979)
        machine_engineering_enabled = raw["machine"].get(
            "engineering_memory_enabled", False
        )
        artifact = _resolved_artifact(
            raw,
            base_port=base_port,
            machine_engineering_enabled=machine_engineering_enabled,
        )
        if digest(raw) != inventory.inventory_digest or digest(artifact) != inventory.artifact_digest:
            raise AdapterError("validated inventory digests do not match")
        self.endpoint = inventory_endpoint(inventory, profile_id)
        self._inventory = inventory
        self._token_resolver = token_resolver
        self._artifact_resolver = artifact_resolver
        self._external_action_reserver = external_action_reserver
        self.timeout = min(max(float(timeout), 0.1), 30.0)
        self.max_json_bytes = min(max(int(max_json_bytes), 1), 8_388_608)
        if (
            type(runtime_result_ttl_seconds) not in (int, float)
            or not math.isfinite(runtime_result_ttl_seconds)
            or not 0 < runtime_result_ttl_seconds <= 86_400
        ):
            raise AdapterError("runtime result TTL is invalid")
        if (
            type(max_runtime_results) is not int
            or not 1 <= max_runtime_results <= 10_000
        ):
            raise AdapterError("runtime result cache size is invalid")
        if (
            type(max_runtime_result_bytes) is not int
            or not 1 <= max_runtime_result_bytes <= 1_073_741_824
        ):
            raise AdapterError("runtime result cache byte budget is invalid")
        if (
            type(max_runtime_inflight) is not int
            or not 1 <= max_runtime_inflight <= 10_000
        ):
            raise AdapterError("runtime in-flight limit is invalid")
        if not callable(runtime_clock):
            raise AdapterError("runtime result clock is invalid")
        if type(max_recordings) is not int or not 0 <= max_recordings <= 10_000:
            raise AdapterError("recording buffer size is invalid")
        self._runtime_result_ttl_seconds = float(runtime_result_ttl_seconds)
        self._max_runtime_results = max_runtime_results
        self._max_runtime_result_bytes = max_runtime_result_bytes
        self._max_runtime_inflight = max_runtime_inflight
        self._runtime_result_bytes = 0
        self._runtime_clock = runtime_clock
        self.recordings: list[dict[str, Any]] = []
        self._max_recordings = max_recordings
        self._recordings_lock = threading.Lock()
        self._runtime_results: OrderedDict[
            str, tuple[str, Mapping[str, Any], float, int]
        ] = OrderedDict()
        self._runtime_results_lock = threading.Lock()
        self._runtime_inflight: dict[str, _RuntimeInFlight] = {}
        self._apply_binding_lock = threading.Lock()
        self._apply_binding: dict[str, Any] | None = None
        self._verified_rollbacks: OrderedDict[str, RollbackBundle] = (
            OrderedDict()
        )
        self._last_action_attestation: dict[str, Any] | None = None
        self._validate_endpoint()
        self._tls_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        self._tls_context.check_hostname = True
        self._tls_context.verify_mode = ssl.CERT_REQUIRED
        if hasattr(ssl, "TLSVersion"):
            self._tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._opener = build_opener(
            ProxyHandler({}),
            _RejectRedirectHandler(),
            HTTPSHandler(context=self._tls_context),
        )

    def __repr__(self) -> str:
        return f"HttpAdapter(endpoint={self.endpoint!r}, timeout={self.timeout!r}, max_json_bytes={self.max_json_bytes!r})"

    def _validate_endpoint(self) -> None:
        if not is_bare_endpoint_host(self.endpoint.host):
            raise AdapterError(
                "endpoint host must be a bare DNS name or IP literal"
            )
        loopback = self.endpoint.host in {"127.0.0.1", "::1"}
        if self.endpoint.scheme not in {"http", "https"}:
            raise AdapterError("endpoint scheme is not permitted")
        if self.endpoint.scheme == "http" and not loopback:
            raise AdapterError("plain HTTP is restricted to loopback endpoints")
        approved = self._inventory.policy.get("approved_tls_endpoints", [])
        if self.endpoint.scheme == "https" and not loopback and self.endpoint.to_dict() not in approved:
            raise AdapterError("TLS endpoint is not approved by inventory")

    def _encode(self, payload: Mapping[str, Any]) -> bytes:
        chunks: list[bytes] = []
        size = 0
        encoder = json.JSONEncoder(
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        try:
            for chunk in encoder.iterencode(payload):
                remaining = self.max_json_bytes - size
                if len(chunk) > remaining:
                    raise AdapterError("JSON request exceeds configured size limit")
                encoded = chunk.encode("utf-8")
                if len(encoded) > remaining:
                    raise AdapterError("JSON request exceeds configured size limit")
                chunks.append(encoded)
                size += len(encoded)
        except (TypeError, ValueError, UnicodeEncodeError):
            raise AdapterError("JSON request is invalid") from None
        return b"".join(chunks)

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        deadline: float | None = None,
    ) -> Any:
        request_deadline = time.monotonic() + self.timeout
        if deadline is not None:
            request_deadline = min(request_deadline, deadline)
        if request_deadline <= time.monotonic():
            raise AdapterError("endpoint request timed out")
        try:
            token = self._token_resolver()
        except Exception:
            raise AuthenticationError("bearer token resolution failed") from None
        if (
            not isinstance(token, str)
            or not token
            or "\r" in token
            or "\n" in token
        ):
            raise AuthenticationError("bearer token resolver returned an invalid token")
        body = None if payload is None else self._encode(payload)
        host = self.endpoint.host.strip("[]")
        url_host = f"[{host}]" if ":" in host else host
        url = f"{self.endpoint.scheme}://{url_host}:{self.endpoint.port}{path}"
        request = Request(url, data=body, method=method, headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            **({"Content-Type": "application/json"} if body is not None else {}),
        })
        if self._max_recordings:
            with self._recordings_lock:
                self.recordings.append({
                    "method": method,
                    "path": path,
                    "payload_keys": sorted(payload) if payload else [],
                })
                overflow = len(self.recordings) - self._max_recordings
                if overflow > 0:
                    del self.recordings[:overflow]
        try:
            with self._opener.open(
                request,
                timeout=max(0.001, request_deadline - time.monotonic()),
            ) as response:
                remaining = request_deadline - time.monotonic()
                if remaining <= 0:
                    raise AdapterError("endpoint request timed out")
                response_socket = self._response_socket(response)
                if response_socket is not None:
                    response_socket.settimeout(max(0.001, remaining))
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        raise AdapterError("endpoint returned invalid Content-Length") from None
                    if declared_length < 0 or declared_length > self.max_json_bytes:
                        raise AdapterError("JSON response exceeds configured size limit")
                read_result: Future[bytes] = Future()

                def read_response() -> None:
                    try:
                        read_result.set_result(
                            response.read(self.max_json_bytes + 1)
                        )
                    except BaseException as error:
                        read_result.set_exception(error)

                reader = threading.Thread(
                    target=read_response,
                    name="hindsight-http-response-read",
                    daemon=True,
                )
                reader.start()
                try:
                    raw = read_result.result(
                        timeout=max(0.0, request_deadline - time.monotonic())
                    )
                except FutureTimeout:
                    if response_socket is not None:
                        try:
                            response_socket.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    try:
                        response.close()
                    except OSError:
                        pass
                    raise AdapterError("endpoint request timed out")
                if time.monotonic() > request_deadline:
                    raise AdapterError("endpoint request timed out")
                if len(raw) > self.max_json_bytes:
                    raise AdapterError("JSON response exceeds configured size limit")
        except HTTPError as error:
            try:
                if error.code == 401:
                    raise AuthenticationError("endpoint authentication failed (HTTP 401)") from None
                if 300 <= error.code < 400:
                    raise AdapterError("endpoint redirect is not permitted") from None
                raise AdapterError(f"endpoint request failed (HTTP {error.code})") from None
            finally:
                error.close()
        except ValueError:
            raise AuthenticationError("bearer token is invalid") from None
        except (HTTPException, URLError, socket.timeout, TimeoutError, OSError):
            raise AdapterError("endpoint request failed") from None
        try:
            value = strict_json_loads(raw)
        except (StrictJsonError, UnicodeDecodeError, json.JSONDecodeError):
            raise AdapterError("endpoint returned invalid JSON") from None
        if not isinstance(value, dict):
            raise AdapterError("endpoint returned non-object JSON")
        return value

    @staticmethod
    def _response_socket(response: Any) -> Any | None:
        """Locate urllib's connected socket without depending on one wrapper type."""
        current = response
        for attribute in ("fp", "raw", "_sock"):
            current = getattr(current, attribute, None)
            if current is None:
                return None
        return current if callable(getattr(current, "settimeout", None)) else None

    def schema_version(self) -> int:
        value = self._request("GET", self.READ_PATHS["schema_version"])
        version = value.get("schema_version", value.get("version")) if isinstance(value, dict) else value
        if type(version) is not int:
            raise AdapterError("endpoint schema version is invalid")
        return version

    def endpoint_identity(self) -> EndpointIdentity:
        value = self._request("GET", "/v1/identity")
        try:
            identity = EndpointIdentity(**value)
        except (TypeError, KeyError):
            raise AdapterError("endpoint identity is invalid") from None
        if identity != self.endpoint:
            raise AdapterError("endpoint identity does not match selected inventory")
        return identity

    def snapshot(self) -> Mapping[str, Any]:
        raw_compatibility = self._request("GET", self.READ_PATHS["read_compatibility"])
        if not isinstance(raw_compatibility, Mapping) or set(raw_compatibility) != {"compatibility"}:
            raise AdapterError("compatibility response keys are closed")
        try:
            compatibility = [dict(item) for item in _compatibility(raw_compatibility["compatibility"])]
        except PlanError:
            raise AdapterError("compatibility response is invalid") from None
        raw_state = self._request("GET", "/v1/state")
        try:
            state = self._redact_snapshot_value(raw_state)
        except (StrictJsonError, TypeError, ValueError):
            raise AdapterError("endpoint state snapshot is invalid") from None
        return {
            "endpoint": self.endpoint_identity().to_dict(),
            "state": state,
            "operations": self.read_operations(),
            "compatibility": compatibility,
        }

    @classmethod
    def _safe_migration_tags(cls, value: Any) -> list[str]:
        if isinstance(value, Mapping) and set(value) == {"tags"}:
            value = value["tags"]
        if not isinstance(value, list):
            raise AdapterError("migration tags response is invalid")
        tags: set[str] = set()
        for item in value:
            if isinstance(item, Mapping):
                if not set(item) <= {"tag", "count"}:
                    raise AdapterError("migration tags response has unknown fields")
                count = item.get("count")
                if count is not None and (type(count) is not int or count < 0):
                    raise AdapterError("migration tags response is invalid")
                candidate = item.get("tag")
            else:
                candidate = item
            if (
                not isinstance(candidate, str)
                or cls.SAFE_MIGRATION_TAG.fullmatch(candidate) is None
            ):
                raise AdapterError("migration tags response is invalid")
            tags.add(candidate)
        return sorted(tags)

    @classmethod
    def _closed_content_records(
        cls, value: Any, *, collection: str, identity_label: str
    ) -> list[dict[str, str]]:
        if isinstance(value, Mapping) and set(value) == {collection}:
            value = value[collection]
        if not isinstance(value, list):
            raise AdapterError(f"{collection} response is invalid")
        result: list[dict[str, str]] = []
        identities: set[str] = set()
        for item in value:
            if not isinstance(item, Mapping):
                raise AdapterError(f"{collection} response is invalid")
            identifier = item.get(identity_label, item.get("id"))
            if (
                not isinstance(identifier, str)
                or cls.ROLLBACK_ID.fullmatch(identifier) is None
            ):
                raise AdapterError(f"{collection} response identity is invalid")
            if identifier in identities:
                raise AdapterError(f"{collection} response identity is duplicated")
            identities.add(identifier)
            result.append(
                {
                    identity_label: identifier,
                    "content_digest": digest(item),
                }
            )
        result.sort(key=lambda item: item[identity_label])
        return result

    @classmethod
    def _redact_snapshot_value(cls, value: Any, field: str = "") -> Any:
        if field and cls._secret_config_key(field):
            return {"redacted": True}
        if field in {"metadata", "document_metadata", "retain_params"}:
            return {"content_digest": digest(value)}
        if field == "tags":
            return cls._safe_migration_tags(value)
        if field == "models":
            return cls._closed_content_records(
                value, collection="models", identity_label="model_id"
            )
        if field == "directives":
            return cls._closed_content_records(
                value, collection="directives", identity_label="directive_id"
            )
        if field == "banks":
            return cls._planner_banks(value)
        if field == "profile_artifact_digest":
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
            ):
                raise AdapterError("profile artifact digest is invalid")
            return value
        if isinstance(value, Mapping):
            if field:
                return {"content_digest": digest(value)}
            known = {
                key: cls._redact_snapshot_value(value[key], key)
                for key in sorted(cls.SNAPSHOT_STATE_FIELDS & value.keys())
            }
            unknown = {
                str(key): item
                for key, item in value.items()
                if key not in cls.SNAPSHOT_STATE_FIELDS
                and key != "migration_generation"
            }
            if unknown:
                known["unknown_fields_digest"] = digest(unknown)
            return known
        if isinstance(value, list):
            return {"content_digest": digest(value)}
        if value is None or isinstance(value, (str, int, float, bool)):
            return {"content_digest": digest(value)}
        raise AdapterError("endpoint state snapshot is invalid")

    @classmethod
    def _planner_banks(cls, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, Mapping) and set(value) == {"banks"}:
            value = value["banks"]
        if not isinstance(value, list):
            raise AdapterError("snapshot banks response is invalid")
        result: list[dict[str, Any]] = []
        identities: set[tuple[str | None, str]] = set()
        for item in value:
            if not isinstance(item, Mapping):
                raise AdapterError("snapshot bank response is invalid")
            allowed_bank_keys = {
                "bank_id", "id", "profile_id", "profile", "artifact_digest",
                "enable_auto_consolidation", "memory_defense", "models",
                "directives",
            }
            unknown_bank_fields = {
                str(key): value
                for key, value in item.items()
                if key not in allowed_bank_keys
            }
            bank_id = item.get("bank_id", item.get("id"))
            profile_id = item.get("profile_id", item.get("profile"))
            if (
                not isinstance(bank_id, str)
                or cls.ROLLBACK_ID.fullmatch(bank_id) is None
                or profile_id is not None
                and (
                    not isinstance(profile_id, str)
                    or cls.ROLLBACK_ID.fullmatch(profile_id) is None
                )
                or (profile_id, bank_id) in identities
            ):
                raise AdapterError("snapshot bank identity is invalid")
            identities.add((profile_id, bank_id))
            bank: dict[str, Any] = {"id": bank_id}
            if unknown_bank_fields:
                bank["unknown_fields_digest"] = digest(unknown_bank_fields)
            if profile_id is not None:
                bank["profile_id"] = profile_id
            for key in ("artifact_digest",):
                if key in item:
                    candidate = item[key]
                    if (
                        not isinstance(candidate, str)
                        or re.fullmatch(r"[0-9a-f]{64}", candidate) is None
                    ):
                        raise AdapterError("snapshot bank artifact is invalid")
                    bank[key] = candidate
            if "enable_auto_consolidation" in item:
                enabled = item["enable_auto_consolidation"]
                if type(enabled) is not bool:
                    raise AdapterError("snapshot bank consolidation is invalid")
                bank["enable_auto_consolidation"] = enabled
            if "memory_defense" in item:
                defense = item["memory_defense"]
                if not isinstance(defense, str) or not defense:
                    raise AdapterError("snapshot bank memory defense is invalid")
                bank["memory_defense"] = defense
            for collection, identity_label in (
                ("models", "model_id"),
                ("directives", "directive_id"),
            ):
                if collection not in item:
                    continue
                raw_collection = item[collection]
                if not isinstance(raw_collection, list):
                    raise AdapterError(
                        f"snapshot bank {collection} response is invalid"
                    )
                entries: list[dict[str, Any]] = []
                seen: set[str] = set()
                for raw_entry in raw_collection:
                    if not isinstance(raw_entry, Mapping):
                        raise AdapterError(
                            f"snapshot bank {collection} response is invalid"
                        )
                    allowed_entry_keys = {
                        identity_label, "id", "artifact_digest"
                    }
                    if collection == "models":
                        allowed_entry_keys.add("revision")
                    unknown_entry_fields = {
                        str(key): value
                        for key, value in raw_entry.items()
                        if key not in allowed_entry_keys
                    }
                    identifier = raw_entry.get(identity_label, raw_entry.get("id"))
                    artifact = raw_entry.get("artifact_digest")
                    if (
                        not isinstance(identifier, str)
                        or cls.ROLLBACK_ID.fullmatch(identifier) is None
                        or identifier in seen
                        or not isinstance(artifact, str)
                        or re.fullmatch(r"[0-9a-f]{64}", artifact) is None
                    ):
                        raise AdapterError(
                            f"snapshot bank {collection} entry is invalid"
                        )
                    seen.add(identifier)
                    entry: dict[str, Any] = {
                        "id": identifier,
                        "artifact_digest": artifact,
                    }
                    if unknown_entry_fields:
                        entry["unknown_fields_digest"] = digest(
                            unknown_entry_fields
                        )
                    if collection == "models" and "revision" in raw_entry:
                        revision = raw_entry["revision"]
                        if (
                            not isinstance(revision, str)
                            or cls.ROLLBACK_ID.fullmatch(revision) is None
                        ):
                            raise AdapterError(
                                "snapshot bank model revision is invalid"
                            )
                        entry["revision"] = revision
                    entries.append(entry)
                bank[collection] = sorted(entries, key=lambda entry: entry["id"])
            result.append(bank)
        return sorted(result, key=lambda bank: (bank.get("profile_id", ""), bank["id"]))

    def _read(self, name: str): return self._request("GET", self.READ_PATHS[name])
    def read_config(self): return self._read("read_config")
    def read_stats(self): return self._read("read_stats")
    def read_tags(self): return self._read("read_tags")
    def read_scopes(self): return self._read("read_scopes")
    def read_documents(self): return self._read("read_documents")
    def read_models(self): return self._read("read_models")
    def read_directives(self): return self._read("read_directives")
    def read_operations(self): return self._read("read_operations")
    def read_invalidated_memories(self): return self._read("read_invalidated_memories")
    def invalidated_memory_inventory(self): return self.read_invalidated_memories()

    def read_migration_generation(self) -> str:
        try:
            value = self._request("GET", "/v1/migration/generation")
        except AuthenticationError:
            raise
        except AdapterError:
            raise AdapterError("migration generation response is invalid") from None
        if not isinstance(value, Mapping) or set(value) != {"generation"}:
            raise AdapterError("migration generation response is invalid")
        generation = value["generation"]
        try:
            encoded_generation = generation.encode("utf-8")
        except (AttributeError, UnicodeEncodeError):
            raise AdapterError("migration generation response is invalid") from None
        if (
            not isinstance(generation, str)
            or not generation
            or len(encoded_generation) > 256
            or not generation.isprintable()
        ):
            raise AdapterError("migration generation response is invalid")
        return generation

    @classmethod
    def _migration_versions(cls, value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise AdapterError("migration version response is invalid")
        api_version = value.get("api_version")
        features = value.get("features")
        if "api_version" not in value or not isinstance(features, Mapping):
            raise AdapterError("migration version response is invalid")
        safe_features: dict[str, bool] = {}
        for name in sorted(cls.MIGRATION_FEATURE_FIELDS & set(features)):
            enabled = features[name]
            if type(enabled) is not bool:
                raise AdapterError("migration feature response is invalid")
            safe_features[name] = enabled
        try:
            api_version_digest = digest(api_version)
            features_digest = digest(features)
            response_digest = digest(value)
        except (StrictJsonError, TypeError, ValueError):
            raise AdapterError("migration version response is invalid") from None
        return {
            "adapter": 1,
            "hindsight": (
                api_version
                if isinstance(api_version, str)
                and cls.SAFE_API_VERSION.fullmatch(api_version) is not None
                else None
            ),
            "hindsight_digest": api_version_digest,
            "features": safe_features,
            "features_digest": features_digest,
            "response_digest": response_digest,
        }

    @classmethod
    def _migration_stats(
        cls, value: Any, *, expected_bank_id: str
    ) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise AdapterError("migration stats response is invalid")
        if value.get("bank_id") != expected_bank_id:
            raise AdapterError("migration stats response identity drifted")
        if "total_documents" not in value:
            raise AdapterError("migration stats response is incomplete")
        result: dict[str, Any] = {"bank_id": expected_bank_id}
        for name in sorted(cls.MIGRATION_STAT_FIELDS & set(value)):
            count = value[name]
            if type(count) is not int or count < 0:
                raise AdapterError("migration stats response is invalid")
            result[name] = count
        try:
            result["response_digest"] = digest(value)
        except (StrictJsonError, TypeError, ValueError):
            raise AdapterError("migration stats response is invalid") from None
        return result

    @classmethod
    def _migration_scopes(cls, value: Any) -> tuple[list[str], str]:
        if not isinstance(value, Mapping) or not isinstance(
            value.get("scopes"), list
        ):
            raise AdapterError("observation scope response is invalid")
        scopes: set[str] = set()
        for item in value["scopes"]:
            if isinstance(item, str):
                scope = item
            elif isinstance(item, Mapping):
                scope = item.get("scope")
                count = item.get("count")
                if count is not None and (
                    type(count) is not int or count < 0
                ):
                    raise AdapterError(
                        "observation scope response is invalid"
                    )
            else:
                raise AdapterError("observation scope response is invalid")
            if (
                not isinstance(scope, str)
                or cls.SAFE_OBSERVATION_SCOPE.fullmatch(scope) is None
            ):
                raise AdapterError("observation scope response is invalid")
            scopes.add(scope)
        normalized = sorted(scopes)
        return normalized, digest({"scopes": normalized})

    def read_migration_inventory(self, source_bank: BankRef, candidate_bank: BankRef):
        if not isinstance(source_bank, BankRef) or not isinstance(candidate_bank, BankRef):
            raise AdapterError("migration inventory requires explicit bank references")
        if source_bank.profile_id != self.endpoint.profile_id or candidate_bank.profile_id != self.endpoint.profile_id:
            raise AdapterError("migration banks must use the selected profile")
        deadline = time.monotonic() + self.timeout
        versions = self._migration_versions(
            self._request("GET", "/version", deadline=deadline)
        )
        banks: dict[str, Any] = {}
        hooks: list[dict[str, Any]] = []
        schedules: list[dict[str, Any]] = []
        active_operations: list[dict[str, Any]] = []
        for role, bank in (("source", source_bank), ("candidate", candidate_bank)):
            bank_snapshot, bank_hooks, bank_schedules, bank_operations = self._read_migration_bank(
                role, bank, deadline=deadline
            )
            banks[role] = bank_snapshot
            hooks.extend(bank_hooks)
            schedules.extend(bank_schedules)
            active_operations.extend(bank_operations)
        active_operations.sort(key=lambda item: (item["bank_role"], item["operation_id"]))
        inventory = {
            "schema_version": 1,
            "endpoint": self.endpoint.to_dict(),
            "provider_identity": self._declared_provider_identity(),
            "versions": versions,
            "banks": banks,
            "operations": {"idle": not active_operations, "active": active_operations},
            "hooks": sorted(hooks, key=lambda item: (item["bank_role"], item["hook_id"])),
            "schedules": sorted(schedules, key=lambda item: (item["bank_role"], item["model_id"])),
        }
        self._validate_migration_inventory(inventory)
        return inventory

    @classmethod
    def _validate_migration_inventory(cls, value: Any) -> None:
        if (
            not isinstance(value, Mapping)
            or set(value)
            != {
                "schema_version", "endpoint", "provider_identity", "versions",
                "banks", "operations", "hooks", "schedules",
            }
            or value.get("schema_version") != 1
            or not isinstance(value.get("banks"), Mapping)
            or set(value["banks"]) != {"source", "candidate"}
            or not isinstance(value.get("operations"), Mapping)
            or set(value["operations"]) != {"idle", "active"}
            or type(value["operations"].get("idle")) is not bool
            or not isinstance(value["operations"].get("active"), list)
        ):
            raise AdapterError("migration inventory schema is invalid")
        bank_keys = {
            "bank_ref", "config", "stats", "scopes", "scopes_digest",
            "tags", "documents", "models", "directives",
            "invalidated_memories",
        }
        document_keys = {
            "document_id", "updated_at", "content_digest", "created_at",
            "text_length", "memory_unit_count", "tags",
            "document_metadata", "retain_params",
        }
        for bank in value["banks"].values():
            if (
                not isinstance(bank, Mapping)
                or set(bank) != bank_keys
                or not isinstance(bank.get("documents"), list)
                or any(
                    not isinstance(document, Mapping)
                    or set(document) != document_keys
                    for document in bank["documents"]
                )
            ):
                raise AdapterError("migration bank inventory schema is invalid")
            document_ids = [document["document_id"] for document in bank["documents"]]
            if len(document_ids) != len(set(document_ids)):
                raise AdapterError("migration bank document identity is duplicated")
        operation_keys = {
            "bank_role", "operation_id", "status", "updated_at"
        }
        if any(
            not isinstance(operation, Mapping)
            or set(operation) != operation_keys
            or operation.get("bank_role") not in {"source", "candidate"}
            or operation.get("status") not in {"pending", "processing"}
            or not isinstance(operation.get("operation_id"), str)
            or cls.ROLLBACK_ID.fullmatch(operation["operation_id"]) is None
            or not cls._aware_timestamp(operation.get("updated_at"))
            for operation in value["operations"]["active"]
        ):
            raise AdapterError("migration operation inventory schema is invalid")

    @classmethod
    def _secret_config_key(cls, key: Any) -> bool:
        separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
        parts = set(filter(None, re.split(r"[^a-z0-9]+", separated.lower())))
        return bool(parts & cls.SECRET_KEY_PARTS)

    @staticmethod
    def _secret_endpoint_value(name: str, value: Any) -> bool:
        if name == "base_url":
            if not isinstance(value, str):
                return True
            try:
                parsed = urlsplit(value)
                return (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or bool(parsed.query)
                    or bool(parsed.fragment)
                    or parsed.path not in {"", "/"}
                )
            except (TypeError, ValueError):
                return True
        if name == "host":
            return (
                not isinstance(value, str)
                or not value
                or any(marker in value for marker in ("@", "?", "#", "://"))
            )
        return False

    @classmethod
    def _safe_config_value(cls, value: Any, path: str, redacted: list[str]) -> Any:
        if isinstance(value, Mapping):
            safe: dict[str, Any] = {}
            for key, item in value.items():
                name = str(key)
                child_path = f"{path}.{name}" if path else name
                if name in cls.DIGESTED_CONFIG_FIELDS:
                    try:
                        safe[f"{name}_digest"] = digest(item)
                    except (StrictJsonError, TypeError, ValueError):
                        raise AdapterError(
                            "bank configuration response is invalid"
                        ) from None
                    redacted.append(child_path)
                elif cls._secret_config_key(name):
                    redacted.append(child_path)
                elif name not in cls.SAFE_CONFIG_FIELDS:
                    redacted.append(child_path)
                elif cls._secret_endpoint_value(name, item):
                    redacted.append(child_path)
                else:
                    safe[name] = cls._safe_config_value(item, child_path, redacted)
            return safe
        if isinstance(value, list):
            return [cls._safe_config_value(item, f"{path}[{index}]", redacted) for index, item in enumerate(value)]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise AdapterError("bank configuration response is invalid")

    @classmethod
    def _safe_config(cls, value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {"bank_id", "config", "overrides"}:
            raise AdapterError("bank configuration response is invalid")
        result: dict[str, Any] = {"bank_id": value["bank_id"]}
        redacted: list[str] = []
        for section in ("config", "overrides"):
            raw = value[section]
            if not isinstance(raw, Mapping):
                raise AdapterError("bank configuration response is invalid")
            result[section] = cls._safe_config_value(raw, section, redacted)
        result["redacted_keys"] = sorted(redacted)
        return result

    def _declared_provider_identity(self) -> Mapping[str, Any]:
        profiles = [profile for profile in self._inventory.profiles if profile.get("id") == self.endpoint.profile_id]
        if len(profiles) != 1:
            raise AdapterError("selected profile identity is unavailable")
        roles = profiles[0].get("roles", profiles[0].get("provider_roles", {}))
        if not isinstance(roles, Mapping):
            raise AdapterError("selected profile provider roles are invalid")
        providers = {provider.get("id"): provider for provider in self._inventory.providers}
        result: dict[str, Any] = {}
        for role, selected in sorted(roles.items()):
            if isinstance(selected, Mapping):
                identifiers = [
                    selected[field]
                    for field in ("current", "desired")
                    if field in selected
                ]
            else:
                identifiers = selected if isinstance(selected, (list, tuple)) else [selected]
            if not identifiers or any(
                not isinstance(identifier, str) or identifier not in providers
                for identifier in identifiers
            ):
                raise AdapterError("selected provider identity is unavailable")
            result[str(role)] = [
                {"provider_id": identifier, "provider_record_digest": digest(providers[identifier])}
                for identifier in identifiers
            ]
        return {
            "inventory_digest": self._inventory.inventory_digest,
            "profile_id": self.endpoint.profile_id,
            "roles": result,
        }

    def _bank_path(self, bank: BankRef, suffix: str = "") -> str:
        return (
            f"/v1/{quote(self.endpoint.tenant, safe='')}/banks/"
            f"{quote(bank.bank_id, safe='')}{suffix}"
        )

    def _read_items(
        self,
        path: str,
        *,
        collection: str = "items",
        total_required: bool = True,
        limit: int | None = None,
        deadline: float | None = None,
    ) -> list[Any]:
        page_limit = self.PAGE_LIMIT if limit is None else limit
        if type(page_limit) is not int or not 1 <= page_limit <= self.PAGE_LIMIT:
            raise AdapterError("pagination limit is invalid")
        items: list[Any] = []
        offset = 0
        pages = 0
        retained_bytes = 0
        expected_total: int | None = None
        while True:
            if pages >= self.MAX_DISCOVERY_PAGES:
                raise AdapterError("paginated discovery response exceeds page limit")
            if pages and retained_bytes >= self.MAX_DISCOVERY_BYTES:
                raise AdapterError("paginated discovery response exceeds byte limit")
            separator = "&" if "?" in path else "?"
            page_path = f"{path}{separator}{urlencode({'limit': page_limit, 'offset': offset})}"
            page = (
                self._request("GET", page_path)
                if deadline is None
                else self._request("GET", page_path, deadline=deadline)
            )
            pages += 1
            page_items = page.get(collection)
            if not isinstance(page_items, list):
                raise AdapterError("paginated discovery response is invalid")
            if page.get("offset", offset) != offset:
                raise AdapterError("paginated discovery response offset drifted")
            if "limit" in page and (
                type(page["limit"]) is not int
                or not len(page_items) <= page["limit"] <= self.PAGE_LIMIT
            ):
                raise AdapterError("paginated discovery response limit is invalid")
            try:
                page_bytes = len(canonical_bytes(page_items))
            except (StrictJsonError, TypeError, ValueError):
                raise AdapterError("paginated discovery response is invalid") from None
            if retained_bytes + page_bytes > self.MAX_DISCOVERY_BYTES:
                raise AdapterError("paginated discovery response exceeds byte limit")
            retained_bytes += page_bytes
            items.extend(page_items)
            if len(items) > self.MAX_DISCOVERY_ITEMS:
                raise AdapterError("paginated discovery response exceeds item limit")
            if total_required:
                total = page.get("total")
                if type(total) is not int or total < 0 or total > self.MAX_DISCOVERY_ITEMS or len(items) > total:
                    raise AdapterError("paginated discovery response total is invalid")
                if expected_total is None:
                    expected_total = total
                elif total != expected_total:
                    raise AdapterError("paginated discovery response total drifted")
                if len(items) == total:
                    return items
                if not page_items:
                    raise AdapterError("paginated discovery response is incomplete")
            elif not page_items:
                return items
            offset = len(items)

    @staticmethod
    def _migration_document(item: Any) -> Mapping[str, Any]:
        if not isinstance(item, Mapping):
            raise AdapterError("migration document response is invalid")
        identifier = item.get("id")
        updated_at = item.get("updated_at")
        content_hash = item.get("content_hash")
        if (
            not isinstance(identifier, str)
            or HttpAdapter.ROLLBACK_ID.fullmatch(identifier) is None
            or not HttpAdapter._aware_timestamp(updated_at)
            or not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
        ):
            raise AdapterError("migration document response is incomplete")
        created_at = item.get("created_at")
        if created_at is not None and not HttpAdapter._aware_timestamp(created_at):
            raise AdapterError("migration document timestamp is invalid")
        for field in ("text_length", "memory_unit_count"):
            value = item.get(field)
            if value is not None and (type(value) is not int or value < 0):
                raise AdapterError("migration document counter is invalid")
        return {
            "document_id": identifier,
            "updated_at": updated_at,
            "content_digest": content_hash,
            "created_at": created_at,
            "text_length": item.get("text_length"),
            "memory_unit_count": item.get("memory_unit_count"),
            "tags": HttpAdapter._safe_migration_tags(item.get("tags", [])),
            "document_metadata": {
                "content_digest": digest(item.get("document_metadata"))
            },
            "retain_params": {
                "content_digest": digest(item.get("retain_params"))
            },
        }

    @staticmethod
    def _aware_timestamp(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return False
        return parsed.tzinfo is not None and parsed.utcoffset() is not None

    @staticmethod
    def _migration_invalidation(item: Any) -> Mapping[str, Any]:
        if not isinstance(item, Mapping):
            raise AdapterError("invalidated memory response is invalid")
        identifier = item.get("id")
        document_id = item.get("document_id")
        content = item.get("text")
        reason = item.get("invalidation_reason")
        if (
            not isinstance(identifier, str)
            or HttpAdapter.ROLLBACK_ID.fullmatch(identifier) is None
            or not isinstance(document_id, str)
            or HttpAdapter.ROLLBACK_ID.fullmatch(document_id) is None
            or not isinstance(content, str)
            or not content
        ):
            raise AdapterError("invalidated memory response is incomplete")
        if reason is None:
            reason = ""
        if not isinstance(reason, str):
            raise AdapterError("invalidated memory response is invalid")
        return {
            "item_id": identifier,
            "source_document_id": document_id,
            "reason_digest": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            "content_digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    def _read_migration_bank(
        self, role: str, bank: BankRef, *, deadline: float
    ):
        base = self._bank_path(bank)
        config = self._safe_config(
            self._request("GET", f"{base}/config", deadline=deadline)
        )
        if config["bank_id"] != bank.bank_id:
            raise AdapterError("bank configuration response identity drifted")
        stats = self._migration_stats(
            self._request("GET", f"{base}/stats", deadline=deadline),
            expected_bank_id=bank.bank_id,
        )
        scopes, scopes_digest = self._migration_scopes(
            self._request(
                "GET", f"{base}/observations/scopes", deadline=deadline
            )
        )
        tags = self._safe_migration_tags(
            self._read_items(f"{base}/tags", deadline=deadline)
        )
        documents = [
            self._migration_document(item)
            for item in self._read_items(
                f"{base}/documents", deadline=deadline
            )
        ]
        document_ids = [item["document_id"] for item in documents]
        if len(document_ids) != len(set(document_ids)):
            raise AdapterError("migration document response identity is duplicated")
        raw_models = self._read_items(
            f"{base}/mental-models?detail=full",
            total_required=False,
            deadline=deadline,
        )
        models = self._closed_content_records(
            raw_models, collection="models", identity_label="model_id"
        )
        directives = self._closed_content_records(
            self._read_items(
                f"{base}/directives?active_only=false",
                total_required=False,
                deadline=deadline,
            ),
            collection="directives",
            identity_label="directive_id",
        )
        webhooks_response = self._request(
            "GET", f"{base}/webhooks", deadline=deadline
        )
        invalidations = [
            self._migration_invalidation(item)
            for item in self._read_items(
                f"{base}/memories/list?state=invalidated", deadline=deadline
            )
        ]
        invalidation_ids = [item["item_id"] for item in invalidations]
        if len(invalidation_ids) != len(set(invalidation_ids)):
            raise AdapterError("invalidated memory response identity is duplicated")
        active: list[dict[str, Any]] = []
        operation_ids: set[str] = set()
        for status in ("pending", "processing"):
            for operation in self._read_items(
                f"{base}/operations?{urlencode({'status': status})}",
                collection="operations",
                limit=100,
                deadline=deadline,
            ):
                if not isinstance(operation, Mapping) or not isinstance(operation.get("id"), str):
                    raise AdapterError("migration operation response is invalid")
                operation_id = operation["id"]
                updated_at = operation.get("updated_at")
                if (
                    self.ROLLBACK_ID.fullmatch(operation_id) is None
                    or not self._aware_timestamp(updated_at)
                    or operation_id in operation_ids
                ):
                    raise AdapterError("migration operation response is invalid")
                operation_ids.add(operation_id)
                active.append(
                    {
                        "bank_role": role,
                        "operation_id": operation_id,
                        "status": status,
                        "updated_at": updated_at,
                    }
                )
        if not isinstance(webhooks_response.get("items"), list):
            raise AdapterError("webhook response is invalid")
        hooks = []
        hook_ids: set[str] = set()
        for item in webhooks_response["items"]:
            if (
                not isinstance(item, Mapping)
                or not isinstance(item.get("id"), str)
                or self.ROLLBACK_ID.fullmatch(item["id"]) is None
                or item["id"] in hook_ids
                or not {"target", "activation", "config"} <= set(item)
            ):
                raise AdapterError("webhook response is invalid")
            hook_ids.add(item["id"])
            try:
                representation = {
                    "target_digest": digest(item["target"]),
                    "activation_digest": digest(item["activation"]),
                    "config_digest": digest(item["config"]),
                }
                registration_digest = digest(representation)
            except (StrictJsonError, TypeError, ValueError):
                raise AdapterError("webhook response is invalid") from None
            hooks.append({
                "bank_role": role,
                "hook_id": item["id"],
                "registration_digest": registration_digest,
                "registration": representation,
            })
        schedules = []
        for raw_item in raw_models:
            if not isinstance(raw_item, Mapping):
                raise AdapterError("mental model response is invalid")
            model_id = raw_item.get("model_id", raw_item.get("id"))
            if (
                not isinstance(model_id, str)
                or self.ROLLBACK_ID.fullmatch(model_id) is None
            ):
                raise AdapterError("mental model response identity is invalid")
            trigger = raw_item.get("trigger")
            if trigger is not None:
                if not isinstance(trigger, Mapping):
                    raise AdapterError("mental model trigger is invalid")
                schedules.append(
                    {
                        "bank_role": role,
                        "model_id": model_id,
                        "trigger_digest": digest(trigger),
                    }
                )
        return (
            {
                "bank_ref": bank.to_dict(),
                "config": config,
                "stats": stats,
                "scopes": scopes,
                "scopes_digest": scopes_digest,
                "tags": tags,
                "documents": documents,
                "models": models,
                "directives": directives,
                "invalidated_memories": invalidations,
            },
            hooks,
            schedules,
            active,
        )

    def export_template(self): return self._request("GET", "/v1/templates/export")

    def _mutate(self, name: str, value: Mapping[str, Any]):
        method, path = self.MUTATION_PATHS[name]
        return self._request(method, path, value)

    def template_dry_run(self, value): return self._mutate("template_dry_run", value)
    def import_template(self, value): return self._mutate("import_template", value)
    def patch_config(self, value): return self._mutate("patch_config", value)
    def upsert_model(self, value): return self._mutate("upsert_model", value)
    def upsert_directive(self, value): return self._mutate("upsert_directive", value)
    def transfer_documents(self, value): return self._mutate("transfer_documents", value)
    def reapply_invalidated_memories(self, value): return self._mutate("reapply_invalidated_memories", value)
    def delete_bank(self, value): return self._mutate("delete_bank", value)

    def bind_apply_plan(self, rollback: RollbackBundle) -> None:
        if (
            not isinstance(rollback, RollbackBundle)
            or not isinstance(rollback.plan_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", rollback.plan_digest) is None
            or not isinstance(rollback.prestate_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", rollback.prestate_digest) is None
            or not isinstance(rollback.endpoint_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", rollback.endpoint_digest) is None
            or not rollback.action_ids
        ):
            raise AdapterError("apply plan binding is invalid")
        with self._apply_binding_lock:
            active = self._apply_binding
            if active is not None and (
                active["action_ids"] or self._last_action_attestation is not None
            ):
                raise AdapterError("an apply plan binding is already active")
            if self._verified_rollbacks.get(rollback.rollback_id) != rollback:
                raise AdapterError(
                    "apply plan requires the exact verified rollback"
                )
            # Verification, current-state observation, and installation form a
            # single transition.  Apply/verify paths use the same lock, so no
            # action can start against the old binding during this snapshot.
            snapshot = self.snapshot()
            endpoint = (
                snapshot.get("endpoint")
                if isinstance(snapshot, Mapping) else None
            )
            state = (
                snapshot.get("state")
                if isinstance(snapshot, Mapping) else None
            )
            operations = (
                snapshot.get("operations")
                if isinstance(snapshot, Mapping) else None
            )
            if (
                not isinstance(endpoint, Mapping)
                or not isinstance(state, Mapping)
                or not isinstance(operations, Mapping)
                or digest(endpoint) != rollback.endpoint_digest
                or digest(state) != rollback.prestate_digest
            ):
                raise AdapterError(
                    "apply plan requires the current rollback endpoint and prestate"
                )
            self._apply_binding = {
                "plan_digest": rollback.plan_digest,
                "expected_state_digest": rollback.prestate_digest,
                "expected_endpoint_digest": rollback.endpoint_digest,
                "expected_operations_digest": digest(operations),
                "rollback": rollback,
                "action_ids": rollback.action_ids,
            }
            self._last_action_attestation = None

    @staticmethod
    def _action_control(
        action: Any, plan_digest: str, expected_state_digest: str,
        expected_endpoint_digest: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "plan_digest": plan_digest,
            "action_id": action.id,
            "action_kind": action.kind,
            "expected_state_digest": expected_state_digest,
            "expected_endpoint_digest": expected_endpoint_digest,
        }

    @staticmethod
    def _require_action_attestation(
        result: Any, control: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            raise AdapterError("mutation response attestation is invalid")
        attestation = result.get("control_attestation")
        if (
            not isinstance(attestation, Mapping)
            or set(attestation) != set(control) | {"poststate_digest"}
            or any(attestation.get(key) != value for key, value in control.items())
            or not isinstance(attestation.get("poststate_digest"), str)
            or len(attestation["poststate_digest"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in attestation["poststate_digest"]
            )
        ):
            raise AdapterError("mutation response attestation is not bound to the action")
        return dict(attestation)

    def apply_action(self, action) -> None:
        direct_path = self.DIRECT_ACTION_PATHS.get(action.kind)
        method_name = ACTION_METHODS.get(action.kind)
        if direct_path is None and method_name is None:
            raise AdapterError(f"unsupported apply action: {action.kind}")
        details = dict(action.details)
        if action.kind in ARTIFACT_ACTION_KINDS:
            expected = details.pop("artifact_digest", None)
            if self._artifact_resolver is None:
                raise AdapterError("desired artifact resolver is required")
            try:
                desired = self._artifact_resolver(action)
            except Exception:
                raise AdapterError("desired artifact resolution failed") from None
            try:
                if not isinstance(desired, Mapping):
                    raise TypeError
                desired_snapshot = deepcopy(dict(desired))
                desired_matches = digest(desired_snapshot) == expected
            except (StrictJsonError, TypeError, ValueError):
                raise AdapterError("resolved desired artifact is invalid") from None
            if not desired_matches:
                raise AdapterError("resolved desired artifact digest does not match")
            details["desired"] = desired_snapshot
        with self._apply_binding_lock:
            binding = self._apply_binding
            if (
                binding is None
                or not binding["action_ids"]
                or binding["action_ids"][0] != action.id
                or self._last_action_attestation is not None
            ):
                raise AdapterError("action is not bound to the approved plan sequence")
            snapshot = self.snapshot()
            operations = (
                snapshot.get("operations")
                if isinstance(snapshot, Mapping) else None
            )
            if (
                not isinstance(operations, Mapping)
                or not hmac.compare_digest(
                    digest(operations), binding["expected_operations_digest"]
                )
            ):
                raise AdapterError("action operations drifted")
            control = self._action_control(
                action,
                binding["plan_digest"],
                binding["expected_state_digest"],
                binding["expected_endpoint_digest"],
            )
            details["control"] = control
            if direct_path is not None:
                method, path = direct_path
                result = self._request(method, path, details)
            else:
                assert method_name is not None
                result = getattr(self, method_name)(details)
            attestation = self._require_action_attestation(result, control)
            binding["expected_state_digest"] = attestation["poststate_digest"]
            binding["action_ids"] = binding["action_ids"][1:]
            self._last_action_attestation = attestation

    def preflight_action(self, action) -> None:
        if (
            action.kind not in self.DIRECT_ACTION_PATHS
            and action.kind not in ACTION_METHODS
        ):
            raise AdapterError(f"unsupported apply action: {action.kind}")
        if action.kind in ARTIFACT_ACTION_KINDS:
            expected = action.details.get("artifact_digest")
            if self._artifact_resolver is None:
                raise AdapterError("desired artifact resolver is required")
            try:
                desired = self._artifact_resolver(action)
                desired_matches = (
                    isinstance(desired, Mapping) and digest(desired) == expected
                )
            except Exception:
                raise AdapterError("resolved desired artifact is invalid") from None
            if not desired_matches:
                raise AdapterError("resolved desired artifact digest does not match")

    def attest_external_action(
        self, action: Any, mutation: Callable[[], None]
    ) -> None:
        """Execute and attest an admin mutation under the apply-plan lock."""
        if not callable(mutation):
            raise AdapterError("external action mutation callback is required")
        with self._apply_binding_lock:
            binding = self._apply_binding
            if (
                binding is None
                or not binding["action_ids"]
                or binding["action_ids"][0] != action.id
                or self._last_action_attestation is not None
            ):
                raise AdapterError(
                    "external action is not bound to the approved plan sequence"
                )
            snapshot = self.snapshot()
            state = snapshot.get("state") if isinstance(snapshot, Mapping) else None
            endpoint = snapshot.get("endpoint") if isinstance(snapshot, Mapping) else None
            operations = (
                snapshot.get("operations")
                if isinstance(snapshot, Mapping) else None
            )
            if (
                not isinstance(state, Mapping)
                or not hmac.compare_digest(digest(state), binding["expected_state_digest"])
                or not hmac.compare_digest(digest(endpoint), binding["expected_endpoint_digest"])
                or not isinstance(operations, Mapping)
                or not hmac.compare_digest(
                    digest(operations), binding["expected_operations_digest"]
                )
            ):
                raise AdapterError("external action prestate drifted")
            control = self._action_control(
                action,
                binding["plan_digest"],
                binding["expected_state_digest"],
                binding["expected_endpoint_digest"],
            )
            if self._external_action_reserver is None:
                raise AdapterError(
                    "durable external action reservation is required"
                )
            try:
                self._external_action_reserver(control)
            except Exception:
                raise AdapterError(
                    "external action requires reconciliation"
                ) from None
            binding["action_ids"] = binding["action_ids"][1:]
            self._last_action_attestation = {
                **control,
                "poststate_digest": None,
                "status": "indeterminate",
            }
            mutation()
            snapshot = self.snapshot()
            state = snapshot.get("state") if isinstance(snapshot, Mapping) else None
            if not isinstance(state, Mapping):
                raise AdapterError("external action poststate is invalid")
            attestation = {
                **control,
                "poststate_digest": digest(state),
            }
            binding["expected_state_digest"] = attestation["poststate_digest"]
            self._last_action_attestation = attestation

    def verify_postcondition(self, action) -> bool:
        with self._apply_binding_lock:
            attestation = self._last_action_attestation
            if (
                attestation is None
                or attestation["action_id"] != action.id
                or attestation["action_kind"] != action.kind
            ):
                raise AdapterError("postcondition is not bound to the applied action")
            request = {
                "control": dict(attestation),
            }
            result = self._request(
                "POST", "/v1/postconditions/verify", request
            )
            response = result.get("control_attestation") if isinstance(result, Mapping) else None
            if (
                not isinstance(response, Mapping)
                or dict(response) != attestation
                or result.get("verified") is not True
            ):
                return False
            self._last_action_attestation = None
            return True

    def create_rollback_bundle(
        self,
        plan_digest: str,
        action_ids: tuple[str, ...],
        *,
        archive_digest: str | None = None,
        restore_evidence_digest: str | None = None,
    ) -> RollbackBundle:
        request = {"plan_digest": plan_digest, "action_ids": list(action_ids)}
        if (archive_digest is None) != (restore_evidence_digest is None):
            raise AdapterError("rollback archive bindings must be supplied together")
        if archive_digest is not None or restore_evidence_digest is not None:
            request.update(
                {
                    "archive_digest": archive_digest,
                    "restore_evidence_digest": restore_evidence_digest,
                }
            )
        value = self._request("POST", "/v1/rollbacks", request)
        required = set(RollbackBundle.__dataclass_fields__) - {
            "archive_digest", "restore_evidence_digest"
        }
        optional = {"archive_digest", "restore_evidence_digest"}
        if not required <= set(value) or set(value) - required - optional:
            raise AdapterError("rollback attestation schema is invalid")
        try:
            bundle = RollbackBundle(
                rollback_id=value["rollback_id"], plan_digest=value["plan_digest"],
                action_ids=tuple(value["action_ids"]), prestate_digest=value["prestate_digest"],
                endpoint_digest=value["endpoint_digest"], bundle_digest=value["bundle_digest"],
                restore_proof_digest=value["restore_proof_digest"],
                archive_digest=value.get("archive_digest"),
                restore_evidence_digest=value.get("restore_evidence_digest"),
            )
        except (KeyError, TypeError):
            raise AdapterError("rollback attestation schema is invalid") from None
        digests = (bundle.plan_digest, bundle.prestate_digest, bundle.endpoint_digest,
                   bundle.bundle_digest, bundle.restore_proof_digest)
        binding_digests = tuple(
            item
            for item in (bundle.archive_digest, bundle.restore_evidence_digest)
            if item is not None
        )
        if (
            not isinstance(bundle.rollback_id, str) or self.ROLLBACK_ID.fullmatch(bundle.rollback_id) is None
            or bundle.plan_digest != plan_digest or bundle.action_ids != action_ids
            or bundle.archive_digest != archive_digest
            or bundle.restore_evidence_digest != restore_evidence_digest
            or (bundle.archive_digest is None) != (bundle.restore_evidence_digest is None)
            or not all(isinstance(item, str) and len(item) == 64 and all(char in "0123456789abcdef" for char in item) for item in digests + binding_digests)
        ):
            raise AdapterError("rollback attestation is not bound to the request")
        with self._apply_binding_lock:
            self._verified_rollbacks.pop(bundle.rollback_id, None)
        return bundle

    def _rollback_path(self, rollback: RollbackBundle, operation: str) -> str:
        if not isinstance(rollback, RollbackBundle) or self.ROLLBACK_ID.fullmatch(rollback.rollback_id) is None:
            raise AdapterError("rollback attestation ID is invalid")
        return f"/v1/rollbacks/{rollback.rollback_id}/{operation}"

    def verify_rollback_bundle(self, rollback: RollbackBundle) -> bool:
        value = self._request("POST", self._rollback_path(rollback, "verify"), rollback.to_dict())
        self._verify_rollback_response(value, rollback, "verified")
        with self._apply_binding_lock:
            self._verified_rollbacks.pop(rollback.rollback_id, None)
            self._verified_rollbacks[rollback.rollback_id] = rollback
            while len(self._verified_rollbacks) > MAX_VERIFIED_ROLLBACKS:
                self._verified_rollbacks.popitem(last=False)
        return True

    @staticmethod
    def _verify_rollback_response(
        value: Any, rollback: RollbackBundle, outcome: str
    ) -> bool:
        if (
            not isinstance(value, Mapping)
            or set(value) != {
                outcome, "rollback_id", "bundle_digest", "prestate_digest"
            }
            or value[outcome] is not True
            or value["rollback_id"] != rollback.rollback_id
            or value["bundle_digest"] != rollback.bundle_digest
            or value["prestate_digest"] != rollback.prestate_digest
        ):
            raise AdapterError("rollback response attestation is invalid")
        return True

    def restore(self, rollback: RollbackBundle):
        path = self._rollback_path(rollback, "restore")
        with self._apply_binding_lock:
            binding = self._apply_binding
            if binding is not None and binding["rollback"] != rollback:
                raise AdapterError("rollback is not bound to the approved apply plan")
            value = self._request("POST", path, rollback.to_dict())
            self._verify_rollback_response(value, rollback, "restored")
            self._apply_binding = None
            self._last_action_attestation = None

    def disable_activation(self):
        value = self._request("POST", "/v1/activation/disable", {})
        if (
            not isinstance(value, Mapping)
            or set(value) != {"activation_enabled"}
            or value["activation_enabled"] is not False
        ):
            raise AdapterError("activation disable was not attested")
        return {"activation_enabled": False}

    def recall(self, request): return self._request("POST", "/v1/runtime/recall", validate_runtime_request("recall", request))
    def mental_model_fetch(self, request): return self._request("POST", "/v1/runtime/mental-model", validate_runtime_request("mental_model_fetch", request))
    def session_status(self, request): return self._request("POST", "/v1/runtime/session-status", validate_runtime_request("session_status", request))

    def _runtime_write(self, path: str, request: Mapping[str, Any]):
        method = {
            "/v1/runtime/transcript-checkpoint": "transcript_checkpoint",
            "/v1/runtime/outcome": "retain_outcome",
            "/v1/runtime/reflection": "reflect",
        }[path]
        request = validate_runtime_request(method, request)
        key = request["idempotency_key"]
        request_digest = digest(request)
        return self._runtime_write_cached(
            path,
            request,
            key,
            request_digest,
            time.monotonic() + self.timeout,
        )

    def _runtime_write_cached(
        self,
        path: str,
        request: Mapping[str, Any],
        key: str,
        request_digest: str,
        deadline: float,
    ):
        now = self._runtime_result_now()
        with self._runtime_results_lock:
            self._prune_runtime_results(now)
            if key in self._runtime_results:
                stored_digest, result, expires_at, size = self._runtime_results.pop(key)
                if stored_digest != request_digest:
                    self._runtime_results[key] = (
                        stored_digest, result, expires_at, size
                    )
                    raise AdapterError("runtime idempotency digest drift")
                self._runtime_results[key] = (
                    stored_digest, result, expires_at, size
                )
                return deepcopy(result)
            in_flight = self._runtime_inflight.get(key)
            if in_flight is None:
                if len(self._runtime_inflight) >= self._max_runtime_inflight:
                    raise AdapterError("runtime in-flight capacity is exhausted")
                in_flight = _RuntimeInFlight(request_digest)
                self._runtime_inflight[key] = in_flight
                leader = True
            else:
                if in_flight.request_digest != request_digest:
                    raise AdapterError("runtime idempotency digest drift")
                leader = False

        if not leader:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not in_flight.event.wait(remaining):
                raise AdapterError("runtime request timed out")
            if in_flight.error is not None:
                raise in_flight.error
            return deepcopy(in_flight.result)

        try:
            result = self._request("PUT", path, request)
            completed_at = self._runtime_result_now()
            expires_at = completed_at + self._runtime_result_ttl_seconds
            stored = deepcopy(result)
            stored_size = len(canonical_bytes(stored))
        except BaseException as error:
            with self._runtime_results_lock:
                in_flight.error = error
                self._runtime_inflight.pop(key, None)
                in_flight.event.set()
            raise
        with self._runtime_results_lock:
            self._prune_runtime_results(completed_at)
            if stored_size <= self._max_runtime_result_bytes:
                previous = self._runtime_results.pop(key, None)
                if previous is not None:
                    self._runtime_result_bytes -= previous[3]
                self._runtime_results[key] = (
                    request_digest, stored, expires_at, stored_size
                )
                self._runtime_result_bytes += stored_size
                while (
                    len(self._runtime_results) > self._max_runtime_results
                    or self._runtime_result_bytes
                    > self._max_runtime_result_bytes
                ):
                    _, evicted = self._runtime_results.popitem(last=False)
                    self._runtime_result_bytes -= evicted[3]
            in_flight.result = stored
            self._runtime_inflight.pop(key, None)
            in_flight.event.set()
        return deepcopy(stored)

    def _runtime_result_now(self) -> float:
        try:
            now = float(self._runtime_clock())
        except (TypeError, ValueError, OverflowError):
            raise AdapterError("runtime result clock is invalid") from None
        if not math.isfinite(now):
            raise AdapterError("runtime result clock is invalid")
        return now

    def _prune_runtime_results(self, now: float) -> None:
        for stored_key, (_digest, _result, expires_at, size) in tuple(
            self._runtime_results.items()
        ):
            if expires_at <= now:
                del self._runtime_results[stored_key]
                self._runtime_result_bytes -= size

    def transcript_checkpoint(self, request): return self._runtime_write("/v1/runtime/transcript-checkpoint", request)
    def retain_outcome(self, request): return self._runtime_write("/v1/runtime/outcome", request)
    def reflect(self, request): return self._runtime_write("/v1/runtime/reflection", request)
