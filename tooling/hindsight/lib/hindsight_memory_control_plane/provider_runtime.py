"""Data-driven provider resilience and its version-gated Hindsight adapter."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import heapq
import inspect
from importlib import metadata
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
from types import MappingProxyType
from typing import Any, AsyncIterator, Callable, Iterator, Mapping
from urllib.parse import urlsplit, urlunsplit


class ProviderRuntimeCompatibilityError(RuntimeError):
    """The provider policy cannot be applied safely to this runtime."""


class ProviderQueueTimeout(TimeoutError):
    """A managed provider request expired before gate admission."""

    def __init__(self) -> None:
        super().__init__("provider_queue_timeout")


class ProviderExecutionTimeout(TimeoutError):
    """A managed provider request expired after gate admission."""

    def __init__(self) -> None:
        super().__init__("provider_execution_timeout")


def _is_timeout_exception(error: BaseException) -> bool:
    """Recognize explicit timeouts, including wrapped transport failures."""
    try:
        import httpx
    except ImportError:
        timeout_types: tuple[type[BaseException], ...] = (TimeoutError,)
    else:
        timeout_types = (TimeoutError, httpx.TimeoutException)

    current: BaseException | None = error
    observed: set[int] = set()
    while current is not None and id(current) not in observed:
        observed.add(id(current))
        if isinstance(current, asyncio.CancelledError):
            return False
        if isinstance(current, timeout_types):
            return True
        current = current.__cause__ or current.__context__
    return False


POLICY_KEYS = {
    "schema_version",
    "hindsight_version",
    "default_usage_limit_cooldown_seconds",
    "failover_order",
    "members",
}
MEMBER_KEYS = {
    "id",
    "identity",
    "credential",
    "timeout_seconds",
    "max_retries",
    "max_concurrent",
    "operation_priorities",
    "quota_cooldown",
}
MEMBER_V2_KEYS = (MEMBER_KEYS - {"timeout_seconds"}) | {
    "queue_timeout_seconds",
    "execution_timeout_seconds",
}
IDENTITY_KEYS = {"provider", "model", "base_url", "credential_marker"}
CREDENTIAL_KEYS = {"mode", "locator"}
PRIORITY_KEYS = {"default", "reflect", "retain", "consolidation"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
SUPPORTED_HINDSIGHT_VERSIONS = frozenset(
    {"0.8.4", "0.9.0", "0.9.1", "0.9.2"}
)
SUPPORTED_RUNTIME_PROVIDERS_084 = frozenset(
    {
        "anthropic",
        "atlas",
        "bedrock",
        "claude-code",
        "deepseek",
        "fireworks",
        "gemini",
        "groq",
        "litellm",
        "litellmrouter",
        "llamacpp",
        "lmstudio",
        "minimax",
        "mock",
        "none",
        "nous",
        "ollama",
        "ollama-cloud",
        "openai",
        "openai-codex",
        "opencode-go",
        "openrouter",
        "requesty",
        "vertexai",
        "volcano",
        "zai",
    }
)
SUPPORTED_RUNTIME_PROVIDERS_091 = (
    SUPPORTED_RUNTIME_PROVIDERS_084 | {"openai-responses"}
)
STARTUP_LLM_VERIFICATION_TIMEOUT_SECONDS = 10.0
PROVIDER_CONNECT_TIMEOUT_SECONDS = 20.0
PROVIDER_POOL_TIMEOUT_SECONDS = 20.0
PROVIDER_WRITE_TIMEOUT_SECONDS = 60.0
_CODEX_ENVIRONMENT_LOCK = threading.Lock()
_EXACT_DRAIN_PROGRESS_RECORDER: Any | None = None
_PROVIDER_REQUEST_DIGEST: ContextVar[str | None] = ContextVar(
    "hindsight_provider_request_digest",
    default=None,
)
_CODEX_MODELS_WITHOUT_REASONING_SUMMARY = frozenset(
    {"gpt-5.3-codex-spark"}
)


class _CodexRequestCompatibilityClient:
    """Project supported Codex request shapes at the HTTP client boundary."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    async def post(self, url: Any, **kwargs: Any) -> Any:
        payload = kwargs.get("json")
        if isinstance(payload, Mapping):
            reasoning = payload.get("reasoning")
            if isinstance(reasoning, Mapping) and "summary" in reasoning:
                projected = dict(payload)
                projected_reasoning = dict(reasoning)
                projected_reasoning.pop("summary")
                projected["reasoning"] = projected_reasoning
                kwargs = {**kwargs, "json": projected}
        return await self._delegate.post(url, **kwargs)

    async def aclose(self) -> Any:
        return await self._delegate.aclose()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def set_exact_drain_progress_recorder(recorder: Any | None) -> None:
    """Install the run-owned payload-free recorder before provider activation."""
    global _EXACT_DRAIN_PROGRESS_RECORDER
    _EXACT_DRAIN_PROGRESS_RECORDER = recorder


def _closed(value: Mapping[str, Any], keys: set[str], label: str) -> None:
    actual = set(value)
    if actual != keys:
        raise ProviderRuntimeCompatibilityError(
            f"{label} keys are closed (missing={sorted(keys - actual)}, "
            f"unknown={sorted(actual - keys)})"
        )


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise ProviderRuntimeCompatibilityError(
            f"{label} must be a bounded identifier"
        )
    return value


def _string(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise ProviderRuntimeCompatibilityError(f"{label} must be a string")
    return value


def _base_url(value: Any) -> str:
    base_url = _string(value, "runtime base_url", empty=True)
    if not base_url:
        return base_url
    if any(character.isspace() for character in base_url):
        raise ProviderRuntimeCompatibilityError(
            "base_url cannot contain whitespace"
        )
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderRuntimeCompatibilityError(
            "base_url must be an absolute HTTP or HTTPS URL"
        )
    if parsed.username is not None or parsed.password is not None:
        raise ProviderRuntimeCompatibilityError(
            "base_url cannot contain credentials"
        )
    if parsed.query or parsed.fragment:
        raise ProviderRuntimeCompatibilityError(
            "base_url cannot contain a query or fragment"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProviderRuntimeCompatibilityError("base_url port is invalid") from exc
    if port == 0:
        raise ProviderRuntimeCompatibilityError("base_url port cannot be zero")
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    return urlunsplit((scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _bounded_int(value: Any, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ProviderRuntimeCompatibilityError(
            f"{label} must be an integer from {low} through {high}"
        )
    return value


def _optional_bounded_int(
    value: Any, label: str, low: int, high: int
) -> int | None:
    if value is None:
        return None
    return _bounded_int(value, label, low, high)


def _split_timeout(timeout_seconds: int) -> Any:
    try:
        import httpx
    except ImportError as error:
        raise ProviderRuntimeCompatibilityError(
            "split provider timeouts require httpx"
        ) from error
    return httpx.Timeout(
        float(timeout_seconds),
        connect=min(PROVIDER_CONNECT_TIMEOUT_SECONDS, timeout_seconds),
        pool=min(PROVIDER_POOL_TIMEOUT_SECONDS, timeout_seconds),
        write=min(PROVIDER_WRITE_TIMEOUT_SECONDS, timeout_seconds),
    )


@dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    model: str
    base_url: str
    credential_marker: str | None

    def matches(self, member: Any) -> bool:
        return self.matches_values(
            provider=str(getattr(member, "provider", "")),
            model=getattr(member, "model", None),
            base_url=getattr(member, "base_url", ""),
            credential_marker=getattr(
                member,
                "_hindsight_provider_credential_marker",
                getattr(member, "api_key", None),
            ),
        )

    def matches_values(
        self,
        *,
        provider: str,
        model: Any,
        base_url: str,
        credential_marker: Any,
    ) -> bool:
        if provider.lower() != self.provider.lower():
            return False
        if model != self.model:
            return False
        try:
            normalized_base_url = _base_url(
                "" if base_url is None else base_url
            )
        except ProviderRuntimeCompatibilityError:
            return False
        if normalized_base_url != self.base_url:
            return False
        return (
            self.credential_marker is None
            or credential_marker == self.credential_marker
        )


@dataclass(frozen=True)
class ProviderMemberPolicy:
    id: str
    identity: ProviderIdentity
    credential_mode: str
    credential_locator: str | None
    timeout_seconds: int | None
    queue_timeout_seconds: int | None
    execution_timeout_seconds: int | None
    max_retries: int | None
    max_concurrent: int | None
    operation_priorities: Mapping[str, int] = field(hash=False, compare=True)
    quota_cooldown: bool

    def priority(self, scope: str) -> int:
        if scope.startswith("retain"):
            return self.operation_priorities["retain"]
        if scope.startswith("consolidation"):
            return self.operation_priorities["consolidation"]
        if scope.startswith("reflect"):
            return self.operation_priorities["reflect"]
        return self.operation_priorities["default"]


@dataclass(frozen=True)
class ProviderRuntimePolicy:
    schema_version: int
    hindsight_version: str
    default_usage_limit_cooldown_seconds: float
    failover_order: tuple[str, ...]
    members: tuple[ProviderMemberPolicy, ...]

    @classmethod
    def load(cls, value: Mapping[str, Any]) -> "ProviderRuntimePolicy":
        if not isinstance(value, Mapping):
            raise ProviderRuntimeCompatibilityError(
                "provider runtime policy must be an object"
            )
        _closed(value, POLICY_KEYS, "provider runtime policy")
        schema_version = value["schema_version"]
        if type(schema_version) is not int or schema_version not in {1, 2, 3}:
            raise ProviderRuntimeCompatibilityError(
                "provider runtime schema_version must be 1, 2, or 3"
            )
        hindsight_version = _string(value["hindsight_version"], "hindsight_version")
        cooldown = value["default_usage_limit_cooldown_seconds"]
        if (
            not isinstance(cooldown, (int, float))
            or isinstance(cooldown, bool)
            or not math.isfinite(float(cooldown))
            or cooldown <= 0
        ):
            raise ProviderRuntimeCompatibilityError(
                "default_usage_limit_cooldown_seconds must be finite and positive"
            )
        raw_members = value["members"]
        if not isinstance(raw_members, list) or not raw_members:
            raise ProviderRuntimeCompatibilityError("members must be a non-empty list")
        members = tuple(
            _load_member(item, schema_version=schema_version)
            for item in raw_members
        )
        if hindsight_version not in {"0.9.1", "0.9.2"} and any(
            member.identity.provider == "openai-responses"
            for member in members
        ):
            raise ProviderRuntimeCompatibilityError(
                "openai-responses requires Hindsight 0.9.1 or 0.9.2"
            )
        ids = [member.id for member in members]
        if len(ids) != len(set(ids)):
            raise ProviderRuntimeCompatibilityError("member ids must be unique")
        identities = [member.identity for member in members]
        if len(identities) != len(set(identities)):
            raise ProviderRuntimeCompatibilityError(
                "provider identities must be unique"
            )
        wildcard_identity_keys = {
            (
                member.identity.provider.lower(),
                member.identity.model,
                member.identity.base_url.rstrip("/"),
            )
            for member in members
            if member.identity.credential_marker is None
        }
        credential_scoped_identity_keys = {
            (
                member.identity.provider.lower(),
                member.identity.model,
                member.identity.base_url.rstrip("/"),
            )
            for member in members
            if member.identity.credential_marker is not None
        }
        if wildcard_identity_keys & credential_scoped_identity_keys:
            raise ProviderRuntimeCompatibilityError(
                "credential-free identity overlaps a credential-scoped identity"
            )
        oauth_home_locators = [
            member.credential_locator
            for member in members
            if member.credential_mode == "oauth-home"
        ]
        if len(oauth_home_locators) != len(set(oauth_home_locators)):
            raise ProviderRuntimeCompatibilityError(
                "OAuth home locators must be unique"
            )
        raw_order = value["failover_order"]
        if (
            not isinstance(raw_order, list)
            or any(not isinstance(item, str) for item in raw_order)
            or len(raw_order) != len(set(raw_order))
            or set(raw_order) != set(ids)
        ):
            raise ProviderRuntimeCompatibilityError(
                "failover_order must name every member exactly once"
            )
        return cls(
            schema_version=schema_version,
            hindsight_version=hindsight_version,
            default_usage_limit_cooldown_seconds=float(cooldown),
            failover_order=tuple(raw_order),
            members=members,
        )

    def member(self, member_id: str) -> ProviderMemberPolicy:
        for member in self.members:
            if member.id == member_id:
                return member
        raise ProviderRuntimeCompatibilityError(f"unknown provider member {member_id}")

    def match(self, runtime_member: Any) -> ProviderMemberPolicy | None:
        matches = tuple(
            member for member in self.members if member.identity.matches(runtime_member)
        )
        if len(matches) > 1:
            raise ProviderRuntimeCompatibilityError(
                "runtime member matches more than one provider policy"
            )
        return matches[0] if matches else None

    def member_for_marker(self, marker: str) -> ProviderMemberPolicy | None:
        matches = tuple(
            member
            for member in self.members
            if member.identity.credential_marker == marker
        )
        if len(matches) > 1:
            raise ProviderRuntimeCompatibilityError(
                "credential marker matches more than one provider policy"
            )
        return matches[0] if matches else None


def _load_member(
    value: Any,
    *,
    schema_version: int,
) -> ProviderMemberPolicy:
    if not isinstance(value, Mapping):
        raise ProviderRuntimeCompatibilityError("provider member must be an object")
    _closed(
        value,
        MEMBER_KEYS if schema_version == 1 else MEMBER_V2_KEYS,
        "provider member",
    )
    member_id = _identifier(value["id"], "provider member id")
    identity = value["identity"]
    if not isinstance(identity, Mapping):
        raise ProviderRuntimeCompatibilityError("provider identity must be an object")
    _closed(identity, IDENTITY_KEYS, "provider identity")
    marker = identity["credential_marker"]
    if marker is not None:
        marker = _identifier(marker, "credential marker")
        expected_marker = f"provider-policy:{member_id}"
        if marker != expected_marker:
            raise ProviderRuntimeCompatibilityError(
                f"credential marker must equal {expected_marker}"
            )
    runtime_provider = _identifier(identity["provider"], "runtime provider")
    supported_runtime_providers = (
        SUPPORTED_RUNTIME_PROVIDERS_091
        if schema_version == 3
        else SUPPORTED_RUNTIME_PROVIDERS_084
    )
    if runtime_provider not in supported_runtime_providers:
        raise ProviderRuntimeCompatibilityError(
            "runtime provider is not supported by Hindsight 0.8.4"
        )
    provider_identity = ProviderIdentity(
        provider=runtime_provider,
        model=_identifier(identity["model"], "runtime model"),
        base_url=_base_url(identity["base_url"]),
        credential_marker=marker,
    )
    credential = value["credential"]
    if not isinstance(credential, Mapping):
        raise ProviderRuntimeCompatibilityError("provider credential must be an object")
    _closed(credential, CREDENTIAL_KEYS, "provider credential")
    mode = credential["mode"]
    locator = credential["locator"]
    if mode == "oauth-home":
        if not isinstance(locator, str) or re.fullmatch(
            r"oauth-home:[a-z0-9][a-z0-9._-]*", locator
        ) is None:
            raise ProviderRuntimeCompatibilityError(
                "OAuth home locator shape is invalid"
            )
        if marker is None:
            raise ProviderRuntimeCompatibilityError(
                "OAuth home provider requires a credential marker"
            )
        if provider_identity.provider != "openai-codex":
            raise ProviderRuntimeCompatibilityError(
                "OAuth home credentials require the openai-codex provider"
            )
    elif mode == "api-key" and schema_version == 3:
        if not isinstance(locator, str) or re.fullmatch(
            r"api-key:[a-z0-9][a-z0-9._-]*", locator
        ) is None:
            raise ProviderRuntimeCompatibilityError(
                "API key locator shape is invalid"
            )
        if marker is None:
            raise ProviderRuntimeCompatibilityError(
                "API key provider requires a credential marker"
            )
        if provider_identity.provider not in {"openai", "openai-responses"}:
            raise ProviderRuntimeCompatibilityError(
                "API key credentials require an OpenAI provider"
            )
    elif mode == "none":
        if locator is not None or marker is not None:
            raise ProviderRuntimeCompatibilityError(
                "credential-free provider cannot declare a locator or marker"
            )
    else:
        raise ProviderRuntimeCompatibilityError("provider credential mode is invalid")
    priorities = value["operation_priorities"]
    if not isinstance(priorities, Mapping):
        raise ProviderRuntimeCompatibilityError("operation priorities must be an object")
    _closed(priorities, PRIORITY_KEYS, "operation priorities")
    normalized_priorities = {
        key: _bounded_int(priorities[key], f"{key} priority", -1000, 1000)
        for key in sorted(PRIORITY_KEYS)
    }
    maximum = value["max_concurrent"]
    if maximum is not None:
        maximum = _bounded_int(maximum, "max_concurrent", 1, 1024)
    quota_cooldown = value["quota_cooldown"]
    if type(quota_cooldown) is not bool:
        raise ProviderRuntimeCompatibilityError("quota_cooldown must be boolean")
    if quota_cooldown and mode not in {"oauth-home", "api-key"}:
        raise ProviderRuntimeCompatibilityError(
            "quota cooldown requires a managed credential identity"
        )
    return ProviderMemberPolicy(
        id=member_id,
        identity=provider_identity,
        credential_mode=mode,
        credential_locator=locator,
        timeout_seconds=(
            _optional_bounded_int(
                value["timeout_seconds"], "timeout_seconds", 1, 3600
            )
            if schema_version == 1
            else None
        ),
        queue_timeout_seconds=(
            None
            if schema_version == 1
            else _bounded_int(
                value["queue_timeout_seconds"],
                "queue_timeout_seconds",
                1,
                3600,
            )
        ),
        execution_timeout_seconds=(
            None
            if schema_version == 1
            else _bounded_int(
                value["execution_timeout_seconds"],
                "execution_timeout_seconds",
                1,
                3600,
            )
        ),
        max_retries=_optional_bounded_int(
            value["max_retries"], "max_retries", 0, 10
        ),
        max_concurrent=maximum,
        operation_priorities=MappingProxyType(normalized_priorities),
        quota_cooldown=quota_cooldown,
    )


class HindsightProviderAdapter:
    """Install provider policy only on an explicitly supported Hindsight build."""

    def __init__(
        self,
        policy: ProviderRuntimePolicy,
        *,
        credential_resolver: Callable[[str], str],
        version_resolver: Callable[[], str] | None = None,
    ) -> None:
        self.policy = policy
        self.credential_resolver = credential_resolver
        self.version_resolver = version_resolver or (
            lambda: metadata.version("hindsight-api")
        )

    def install(self) -> bool:
        try:
            version = self.version_resolver()
        except Exception as exc:
            raise ProviderRuntimeCompatibilityError(
                "could not verify Hindsight version"
            ) from exc
        if (
            version not in SUPPORTED_HINDSIGHT_VERSIONS
            or version != self.policy.hindsight_version
        ):
            raise ProviderRuntimeCompatibilityError(
                f"unsupported Hindsight version {version}; policy requires "
                f"{self.policy.hindsight_version}"
            )
        return self._install_supported()

    def _install_supported(self) -> bool:
        try:
            from hindsight_api.engine.llm_wrapper import LLMProvider
            from hindsight_api.engine.multi_llm import (
                MultiLLMProvider,
                _should_failover,
                logger,
            )
            from hindsight_api.engine.providers.codex_llm import CodexLLM
        except (ImportError, AttributeError) as exc:
            raise ProviderRuntimeCompatibilityError(
                "supported Hindsight provider interfaces are unavailable"
            ) from exc

        installed_policy = getattr(
            LLMProvider, "_hindsight_provider_runtime_policy", None
        )
        if installed_policy == self.policy:
            return False
        if installed_policy is not None:
            raise ProviderRuntimeCompatibilityError(
                "a different Hindsight provider policy is already installed"
            )

        try:
            original_llm_init = LLMProvider.__init__
            llm_init_signature = inspect.signature(original_llm_init)
            if not {
                "provider",
                "api_key",
                "base_url",
                "model",
            }.issubset(llm_init_signature.parameters):
                raise TypeError("provider constructor signature is unsupported")
            original_methods = {
                method_name: getattr(LLMProvider, method_name)
                for method_name in ("call", "call_with_tools")
            }
            original_codex_init = CodexLLM.__init__
            original_dispatch = MultiLLMProvider._dispatch
            original_verify_connection = MultiLLMProvider.verify_connection
            targets = (
                original_llm_init,
                *original_methods.values(),
                original_codex_init,
                original_dispatch,
                original_verify_connection,
                _should_failover,
            )
            if any(not callable(target) for target in targets):
                raise TypeError("provider interface target is not callable")
        except (AttributeError, TypeError) as exc:
            raise ProviderRuntimeCompatibilityError(
                "supported Hindsight provider interfaces are unavailable"
            ) from exc

        runtime = _ProviderRuntime(
            self.policy,
            credential_resolver=self.credential_resolver,
            logger=logger,
        )

        if getattr(original_llm_init, "_hindsight_provider_policy", False):
            raise ProviderRuntimeCompatibilityError(
                "Hindsight provider policy is already installed"
            )

        def policy_aware_init(instance: Any, *args: Any, **kwargs: Any) -> None:
            bound = llm_init_signature.bind(instance, *args, **kwargs)
            marker = bound.arguments.get("api_key")
            managed_api_key = runtime.managed_api_key(
                provider=str(bound.arguments.get("provider", "")),
                marker=marker,
                base_url=bound.arguments.get("base_url", ""),
                model=bound.arguments.get("model"),
            )
            if managed_api_key is None:
                original_llm_init(instance, *args, **kwargs)
            else:
                member_id, resolved_api_key = managed_api_key
                bound.arguments["api_key"] = resolved_api_key
                try:
                    original_llm_init(*bound.args, **bound.kwargs)
                except Exception:
                    raise ProviderRuntimeCompatibilityError(
                        "API key provider initialization failed for "
                        f"{member_id}"
                    ) from None
                instance._hindsight_provider_credential_marker = marker
                instance.api_key = marker
            runtime.prepare(instance)

        policy_aware_init._hindsight_provider_policy = True  # type: ignore[attr-defined]
        LLMProvider.__init__ = policy_aware_init

        for method_name in ("call", "call_with_tools"):
            original_method = original_methods[method_name]

            async def guarded_call(
                instance: Any,
                *args: Any,
                _original_method: Callable[..., Any] = original_method,
                **kwargs: Any,
            ) -> Any:
                async def invoke(*call_args: Any, **call_kwargs: Any) -> Any:
                    return await _original_method(instance, *call_args, **call_kwargs)

                return await runtime.call(instance, invoke, *args, **kwargs)

            guarded_call._hindsight_provider_policy = True  # type: ignore[attr-defined]
            setattr(LLMProvider, method_name, guarded_call)

        def oauth_home_init(
            instance: Any,
            provider: str,
            api_key: str,
            base_url: str,
            model: str,
            reasoning_effort: str = "low",
            **kwargs: Any,
        ) -> None:
            with runtime.codex_home(
                provider=provider,
                marker=api_key,
                base_url=base_url,
                model=model,
            ) as managed_member_id:
                try:
                    original_codex_init(
                        instance,
                        provider=provider,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        reasoning_effort=reasoning_effort,
                        **kwargs,
                    )
                    if (
                        managed_member_id is not None
                        and model in _CODEX_MODELS_WITHOUT_REASONING_SUMMARY
                    ):
                        client = getattr(instance, "_client", None)
                        if not callable(getattr(client, "post", None)) or not callable(
                            getattr(client, "aclose", None)
                        ):
                            raise ProviderRuntimeCompatibilityError(
                                "supported Codex HTTP client is unavailable"
                            )
                        instance._client = _CodexRequestCompatibilityClient(client)
                except Exception:
                    if managed_member_id is None:
                        raise
                    raise ProviderRuntimeCompatibilityError(
                        "Codex OAuth home initialization failed for "
                        f"{managed_member_id}"
                    ) from None

        oauth_home_init._hindsight_provider_policy = True  # type: ignore[attr-defined]
        CodexLLM.__init__ = oauth_home_init

        async def policy_dispatch(
            instance: Any, method_name: str, **kwargs: Any
        ) -> Any:
            return await runtime.dispatch(
                instance._members,
                method_name,
                kwargs,
                _should_failover,
                strategy_mode=str(getattr(instance._strategy, "mode", "")),
            )

        policy_dispatch._hindsight_provider_policy = True  # type: ignore[attr-defined]
        MultiLLMProvider._dispatch = policy_dispatch

        async def bounded_verify_connection(instance: Any) -> None:
            try:
                await asyncio.wait_for(
                    original_verify_connection(instance),
                    timeout=STARTUP_LLM_VERIFICATION_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning(
                    "LLM startup verification exceeded %.2fs; "
                    "continuing with request-time provider failover",
                    STARTUP_LLM_VERIFICATION_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                logger.warning(
                    "LLM startup verification failed with %s; "
                    "continuing with request-time provider failover",
                    type(exc).__name__,
                )

        bounded_verify_connection._hindsight_provider_policy = True  # type: ignore[attr-defined]
        MultiLLMProvider.verify_connection = bounded_verify_connection
        LLMProvider._hindsight_provider_runtime_policy = self.policy
        logger.info(
            "Installed version-gated Hindsight provider runtime policy for %s",
            self.policy.hindsight_version,
        )
        return True


class _PriorityGate:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._sequence = 0
        self._lock = threading.Lock()
        self._waiters: list[
            tuple[
                int,
                int,
                asyncio.AbstractEventLoop,
                asyncio.Future[None],
            ]
        ] = []

    async def acquire(self, priority: int) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._active < self._limit and not self._waiters:
                self._active += 1
                return
            waiter = loop.create_future()
            self._sequence += 1
            heapq.heappush(
                self._waiters,
                (priority, self._sequence, loop, waiter),
            )
        try:
            await waiter
        except BaseException:
            if waiter.done() and not waiter.cancelled():
                self.release()
            else:
                waiter.cancel()
            raise

    def _grant(self, waiter: asyncio.Future[None]) -> None:
        if waiter.cancelled():
            self.release()
        elif not waiter.done():
            waiter.set_result(None)

    def release(self) -> None:
        while True:
            with self._lock:
                while self._waiters:
                    _priority, _sequence, loop, waiter = heapq.heappop(
                        self._waiters
                    )
                    if not waiter.cancelled():
                        break
                else:
                    self._active -= 1
                    return
            try:
                loop.call_soon_threadsafe(self._grant, waiter)
            except RuntimeError:
                continue
            return

    @asynccontextmanager
    async def slot(self, priority: int) -> AsyncIterator[None]:
        await self.acquire(priority)
        try:
            yield
        finally:
            self.release()


def _response_payload(exc: BaseException) -> tuple[int | None, dict[str, Any] | None]:
    response = getattr(exc, "response", None)
    if response is None:
        return None, None
    status = getattr(response, "status_code", None)
    try:
        payload = response.json()
    except Exception:
        try:
            payload = json.loads(response.text)
        except Exception:
            payload = None
    return status, payload if isinstance(payload, dict) else None


def _usage_limit_reset_at(
    exc: BaseException, *, now: float, default_cooldown: float
) -> float | None:
    status, payload = _response_payload(exc)
    if status != 429 or payload is None:
        return None
    error = payload.get("error")
    if not isinstance(error, dict) or error.get("type") != "usage_limit_reached":
        return None
    probe_at = now + default_cooldown
    reset = error.get("resets_at")
    if (
        isinstance(reset, (int, float))
        and not isinstance(reset, bool)
        and math.isfinite(float(reset))
        and reset > now
    ):
        return min(float(reset), probe_at)
    remaining = error.get("resets_in_seconds")
    if (
        isinstance(remaining, (int, float))
        and not isinstance(remaining, bool)
        and math.isfinite(float(remaining))
        and remaining > 0
    ):
        return min(now + float(remaining), probe_at)
    return probe_at


class _ProviderRuntime:
    def __init__(
        self,
        policy: ProviderRuntimePolicy,
        *,
        credential_resolver: Callable[[str], str],
        logger: logging.Logger,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.policy = policy
        self._credential_resolver = credential_resolver
        self._logger = logger
        self._clock = clock
        self._cooldowns: dict[str, float] = {}
        self._quota_probes_in_flight: set[str] = set()
        self._cooldown_lock = threading.Lock()
        self._rotation_lock = threading.Lock()
        self._rotation_index = 0
        self._gate_lock = threading.Lock()
        self._gates: dict[str, _PriorityGate] = {}
        self._resolved_oauth_homes: dict[str, Path] = {}
        self._oauth_home_owners: dict[Path, str] = {}

    def managed_api_key(
        self,
        *,
        provider: str,
        marker: Any,
        base_url: Any,
        model: Any,
    ) -> tuple[str, str] | None:
        if not isinstance(marker, str) or not marker.startswith(
            "provider-policy:"
        ):
            return None
        member = self.policy.member_for_marker(marker)
        if member is None:
            raise ProviderRuntimeCompatibilityError(
                "unknown managed provider credential marker"
            )
        if member.credential_mode != "api-key":
            return None
        if not member.identity.matches_values(
            provider=provider,
            model=model,
            base_url=base_url,
            credential_marker=marker,
        ):
            raise ProviderRuntimeCompatibilityError(
                f"provider identity does not match managed marker {member.id}"
            )
        if member.credential_locator is None:
            raise ProviderRuntimeCompatibilityError(
                f"API key locator is absent for {member.id}"
            )
        try:
            resolved = self._credential_resolver(member.credential_locator)
        except Exception:
            raise ProviderRuntimeCompatibilityError(
                f"API key resolution failed for {member.id}"
            ) from None
        if (
            not isinstance(resolved, str)
            or not resolved
            or len(resolved) > 8192
            or resolved != resolved.strip()
            or any(character.isspace() for character in resolved)
            or resolved == marker
        ):
            raise ProviderRuntimeCompatibilityError(
                f"API key resolver returned an invalid value for {member.id}"
            )
        return member.id, resolved

    @property
    def _progress_recorder(self) -> Any | None:
        return _EXACT_DRAIN_PROGRESS_RECORDER

    def prepare(self, runtime_member: Any) -> None:
        member = self.policy.match(runtime_member)
        if member is None:
            return
        effective_timeout = (
            member.timeout_seconds
            if self.policy.schema_version == 1
            else member.execution_timeout_seconds
        )
        if effective_timeout is not None:
            runtime_member.timeout = effective_timeout
        if member.max_retries is not None:
            runtime_member.max_retries = member.max_retries
        provider_impl = getattr(runtime_member, "_provider_impl", None)
        if provider_impl is not None and effective_timeout is not None:
            provider_impl.timeout = effective_timeout
            client = getattr(provider_impl, "_client", None)
            if client is not None and hasattr(client, "with_options"):
                provider_impl._client = client.with_options(
                    timeout=_split_timeout(effective_timeout)
                )

    @contextmanager
    def codex_home(
        self, *, provider: str, marker: str, base_url: str, model: str
    ) -> Iterator[str | None]:
        member = self.policy.member_for_marker(marker)
        if member is None:
            if marker.startswith("provider-policy:"):
                raise ProviderRuntimeCompatibilityError(
                    "unknown managed provider credential marker"
                )
            yield None
            return
        if not member.identity.matches_values(
            provider=provider,
            model=model,
            base_url=base_url,
            credential_marker=marker,
        ):
            raise ProviderRuntimeCompatibilityError(
                f"provider identity does not match managed marker {member.id}"
            )
        if member.credential_mode != "oauth-home" or member.credential_locator is None:
            raise ProviderRuntimeCompatibilityError(
                "credential marker does not resolve to an OAuth home"
            )
        try:
            resolved = self._credential_resolver(member.credential_locator)
            home = Path(resolved)
        except Exception:
            raise ProviderRuntimeCompatibilityError(
                f"OAuth home resolution failed for {member.id}"
            ) from None
        if not home.is_absolute():
            raise ProviderRuntimeCompatibilityError(
                f"OAuth home resolver returned a non-absolute path for {member.id}"
            )
        canonical_home = home.resolve(strict=False)
        with _CODEX_ENVIRONMENT_LOCK:
            prior_home = self._resolved_oauth_homes.get(member.id)
            if prior_home is not None and prior_home != canonical_home:
                raise ProviderRuntimeCompatibilityError(
                    f"OAuth home resolution changed for {member.id}"
                )
            owner = self._oauth_home_owners.get(canonical_home)
            if owner is not None and owner != member.id:
                raise ProviderRuntimeCompatibilityError(
                    f"OAuth home is already bound to {owner}"
                )
            self._resolved_oauth_homes[member.id] = canonical_home
            self._oauth_home_owners[canonical_home] = member.id
            previous = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = str(home)
            try:
                yield member.id
            finally:
                if previous is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = previous

    def _gate(self, member: ProviderMemberPolicy) -> _PriorityGate | None:
        if member.max_concurrent is None:
            return None
        with self._gate_lock:
            gate = self._gates.get(member.id)
            if gate is None:
                gate = _PriorityGate(member.max_concurrent)
                self._gates[member.id] = gate
            return gate

    async def call(
        self,
        runtime_member: Any,
        operation: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        member = self.policy.match(runtime_member)
        if member is None:
            return await operation(*args, **kwargs)
        self.prepare(runtime_member)
        call_kwargs = dict(kwargs)
        if member.max_retries is not None:
            call_kwargs["max_retries"] = member.max_retries
        gate = self._gate(member)

        async def execute() -> Any:
            return await operation(*args, **call_kwargs)

        if self.policy.schema_version == 1:
            async def invoke_legacy() -> Any:
                if gate is None:
                    return await execute()
                async with gate.slot(
                    member.priority(str(call_kwargs.get("scope", "")))
                ):
                    return await execute()

            if member.timeout_seconds is None:
                return await invoke_legacy()
            async with asyncio.timeout(member.timeout_seconds):
                return await invoke_legacy()

        if gate is not None:
            queue_deadline = self._clock() + member.queue_timeout_seconds
            try:
                async with asyncio.timeout(member.queue_timeout_seconds):
                    await gate.acquire(
                        member.priority(str(call_kwargs.get("scope", "")))
                    )
            except TimeoutError as error:
                raise ProviderQueueTimeout() from error
            if self._clock() >= queue_deadline:
                gate.release()
                raise ProviderQueueTimeout()
        progress_recorder = self._progress_recorder
        request_digest = _PROVIDER_REQUEST_DIGEST.get()
        if progress_recorder is not None and request_digest is not None:
            progress_recorder.provider_executing(request_digest)
        try:
            execution_deadline = (
                self._clock() + member.execution_timeout_seconds
            )
            try:
                async with asyncio.timeout(member.execution_timeout_seconds):
                    result = await execute()
                if self._clock() >= execution_deadline:
                    raise ProviderExecutionTimeout()
                return result
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                if _is_timeout_exception(error):
                    raise ProviderExecutionTimeout() from error
                raise
        finally:
            if gate is not None:
                gate.release()

    def _claim_quota_member(self, member_id: str, now: float) -> tuple[bool, bool]:
        with self._cooldown_lock:
            reset = self._cooldowns.get(member_id)
            if reset is None:
                return True, False
            if reset > now or member_id in self._quota_probes_in_flight:
                return False, False
            self._quota_probes_in_flight.add(member_id)
            return True, True

    def _finish_quota_probe(
        self,
        member_id: str,
        *,
        usage_limited: bool,
    ) -> None:
        with self._cooldown_lock:
            self._quota_probes_in_flight.discard(member_id)
            if not usage_limited:
                self._cooldowns.pop(member_id, None)
                if self._progress_recorder is not None:
                    self._progress_recorder.clear_cooldown(member_id)

    async def dispatch(
        self,
        runtime_members: list[Any],
        method_name: str,
        kwargs: dict[str, Any],
        should_failover: Callable[[BaseException], bool],
        *,
        strategy_mode: str,
    ) -> Any:
        by_id: dict[str, Any] = {}
        for runtime_member in runtime_members:
            member = self.policy.match(runtime_member)
            if member is None:
                raise ProviderRuntimeCompatibilityError(
                    "Hindsight LLM member is absent from provider runtime policy"
                )
            if member.id in by_id:
                raise ProviderRuntimeCompatibilityError(
                    f"multiple Hindsight members match provider policy {member.id}"
                )
            by_id[member.id] = runtime_member
        if set(by_id) != set(self.policy.failover_order):
            missing = sorted(set(self.policy.failover_order) - set(by_id))
            raise ProviderRuntimeCompatibilityError(
                f"Hindsight provider failover membership is incomplete: {missing}"
            )

        if strategy_mode == "failover":
            member_order = self.policy.failover_order
        elif strategy_mode == "round-robin":
            primary_order = tuple(
                member_id
                for member_id in self.policy.failover_order
                if self.policy.member(member_id).credential_mode == "oauth-home"
                and self.policy.member(member_id).quota_cooldown
            )
            fallback_order = tuple(
                member_id
                for member_id in self.policy.failover_order
                if member_id not in primary_order
            )
            if not primary_order:
                primary_order = self.policy.failover_order
                fallback_order = ()
            with self._rotation_lock:
                start = self._rotation_index % len(primary_order)
                self._rotation_index += 1
            member_order = (
                primary_order[start:]
                + primary_order[:start]
                + fallback_order
            )
        else:
            raise ProviderRuntimeCompatibilityError(
                "Hindsight provider strategy must be failover or round-robin"
            )

        last_exc: BaseException | None = None
        attempted = 0
        prior_failed_provider: str | None = None
        for member_id in member_order:
            member_policy = self.policy.member(member_id)
            now = self._clock()
            quota_probe = False
            if member_policy.quota_cooldown:
                available, quota_probe = self._claim_quota_member(member_id, now)
                if not available:
                    continue
            attempted += 1
            usage_limited = False
            request_digest = None
            progress_recorder = self._progress_recorder
            if progress_recorder is not None:
                if prior_failed_provider is not None:
                    progress_recorder.provider_failed_over(
                        prior_failed_provider
                    )
                    prior_failed_provider = None
                request_digest = progress_recorder.provider_started(
                    member_id,
                    retry_attempt=1,
                    scope=str(kwargs.get("scope", "")),
                )
            try:
                request_context = _PROVIDER_REQUEST_DIGEST.set(
                    request_digest
                )
                try:
                    result = await getattr(
                        by_id[member_id], method_name
                    )(**kwargs)
                finally:
                    _PROVIDER_REQUEST_DIGEST.reset(request_context)
            except asyncio.CancelledError:
                if progress_recorder is not None and request_digest is not None:
                    progress_recorder.provider_cancelled(request_digest)
                raise
            except BaseException as exc:
                failover = should_failover(exc)
                if progress_recorder is not None and request_digest is not None:
                    progress_recorder.provider_finished(
                        request_digest,
                        outcome=(
                            "queue_timed_out"
                            if isinstance(exc, ProviderQueueTimeout)
                            else (
                                "execution_timed_out"
                                if isinstance(
                                    exc,
                                    ProviderExecutionTimeout,
                                )
                                else (
                                    "timed_out"
                                    if _is_timeout_exception(exc)
                                    else "failed"
                                )
                            )
                        ),
                    )
                if not failover:
                    raise
                last_exc = exc
                prior_failed_provider = member_id
                if member_policy.quota_cooldown:
                    reset = _usage_limit_reset_at(
                        exc,
                        now=now,
                        default_cooldown=self.policy.default_usage_limit_cooldown_seconds,
                    )
                    if reset is not None:
                        usage_limited = True
                        with self._cooldown_lock:
                            self._cooldowns[member_id] = max(
                                reset, self._cooldowns.get(member_id, 0.0)
                            )
                        if progress_recorder is not None:
                            progress_recorder.cooldown(
                                member_id,
                                until=reset,
                                reason="usage_limit",
                            )
                        self._logger.warning(
                            "LLM account %s reached its usage limit; "
                            "bypassing it until probe epoch %.0f",
                            member_id,
                            reset,
                        )
                self._logger.warning(
                    "LLM provider member %s failed on %s with %s",
                    member_id,
                    method_name,
                    type(exc).__name__,
                )
            else:
                if progress_recorder is not None and request_digest is not None:
                    progress_recorder.provider_finished(
                        request_digest,
                        outcome="succeeded",
                    )
                return result
            finally:
                if quota_probe:
                    self._finish_quota_probe(
                        member_id,
                        usage_limited=usage_limited,
                    )
        if last_exc is not None:
            raise last_exc
        if attempted == 0:
            raise RuntimeError(
                "All LLM accounts are waiting for their next quota probe"
            )
        raise RuntimeError("LLM failover chain completed without a result")
