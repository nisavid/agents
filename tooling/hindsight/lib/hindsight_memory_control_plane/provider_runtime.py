"""Data-driven provider resilience and its version-gated Hindsight adapter."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
import heapq
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
IDENTITY_KEYS = {"provider", "model", "base_url", "credential_marker"}
CREDENTIAL_KEYS = {"mode", "locator"}
PRIORITY_KEYS = {"default", "reflect", "retain", "consolidation"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
SUPPORTED_HINDSIGHT_VERSIONS = frozenset({"0.8.4"})
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
_CODEX_ENVIRONMENT_LOCK = threading.Lock()


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
            base_url=str(getattr(member, "base_url", "")),
            credential_marker=getattr(member, "api_key", None),
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
            normalized_base_url = _base_url(base_url)
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
        if value["schema_version"] != 1:
            raise ProviderRuntimeCompatibilityError(
                "provider runtime schema_version must be 1"
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
        members = tuple(_load_member(item) for item in raw_members)
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


def _load_member(value: Any) -> ProviderMemberPolicy:
    if not isinstance(value, Mapping):
        raise ProviderRuntimeCompatibilityError("provider member must be an object")
    _closed(value, MEMBER_KEYS, "provider member")
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
    if runtime_provider not in SUPPORTED_RUNTIME_PROVIDERS_084:
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
    if quota_cooldown and mode != "oauth-home":
        raise ProviderRuntimeCompatibilityError(
            "quota cooldown requires an OAuth home identity"
        )
    return ProviderMemberPolicy(
        id=member_id,
        identity=provider_identity,
        credential_mode=mode,
        credential_locator=locator,
        timeout_seconds=_optional_bounded_int(
            value["timeout_seconds"], "timeout_seconds", 1, 600
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
            original_methods = {
                method_name: getattr(LLMProvider, method_name)
                for method_name in ("call", "call_with_tools")
            }
            original_codex_init = CodexLLM.__init__
            original_dispatch = MultiLLMProvider._dispatch
            targets = (
                original_llm_init,
                *original_methods.values(),
                original_codex_init,
                original_dispatch,
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
            original_llm_init(instance, *args, **kwargs)
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
            )

        policy_dispatch._hindsight_provider_policy = True  # type: ignore[attr-defined]
        MultiLLMProvider._dispatch = policy_dispatch
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
    reset = error.get("resets_at")
    if (
        isinstance(reset, (int, float))
        and not isinstance(reset, bool)
        and math.isfinite(float(reset))
        and reset > now
    ):
        return float(reset)
    remaining = error.get("resets_in_seconds")
    if (
        isinstance(remaining, (int, float))
        and not isinstance(remaining, bool)
        and math.isfinite(float(remaining))
        and remaining > 0
    ):
        return now + float(remaining)
    return now + default_cooldown


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
        self._cooldown_lock = threading.Lock()
        self._gate_lock = threading.Lock()
        self._gates: dict[str, _PriorityGate] = {}
        self._resolved_oauth_homes: dict[str, Path] = {}
        self._oauth_home_owners: dict[Path, str] = {}

    def prepare(self, runtime_member: Any) -> None:
        member = self.policy.match(runtime_member)
        if member is None:
            return
        if member.timeout_seconds is not None:
            runtime_member.timeout = member.timeout_seconds
        if member.max_retries is not None:
            runtime_member.max_retries = member.max_retries
        provider_impl = getattr(runtime_member, "_provider_impl", None)
        if provider_impl is not None and member.timeout_seconds is not None:
            provider_impl.timeout = member.timeout_seconds
            client = getattr(provider_impl, "_client", None)
            if client is not None and hasattr(client, "with_options"):
                provider_impl._client = client.with_options(
                    timeout=member.timeout_seconds
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
        if gate is None:
            return await operation(*args, **call_kwargs)
        async with gate.slot(member.priority(str(call_kwargs.get("scope", "")))):
            return await operation(*args, **call_kwargs)

    def _available(self, member_id: str, now: float) -> bool:
        with self._cooldown_lock:
            reset = self._cooldowns.get(member_id)
            if reset is None:
                return True
            if reset <= now:
                self._cooldowns.pop(member_id, None)
                return True
            return False

    async def dispatch(
        self,
        runtime_members: list[Any],
        method_name: str,
        kwargs: dict[str, Any],
        should_failover: Callable[[BaseException], bool],
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

        last_exc: BaseException | None = None
        attempted = 0
        for member_id in self.policy.failover_order:
            member_policy = self.policy.member(member_id)
            now = self._clock()
            if member_policy.quota_cooldown and not self._available(member_id, now):
                continue
            attempted += 1
            try:
                return await getattr(by_id[member_id], method_name)(**kwargs)
            except BaseException as exc:
                if not should_failover(exc):
                    raise
                last_exc = exc
                if member_policy.quota_cooldown:
                    reset = _usage_limit_reset_at(
                        exc,
                        now=now,
                        default_cooldown=self.policy.default_usage_limit_cooldown_seconds,
                    )
                    if reset is not None:
                        with self._cooldown_lock:
                            self._cooldowns[member_id] = max(
                                reset, self._cooldowns.get(member_id, 0.0)
                            )
                        self._logger.warning(
                            "LLM account %s reached its usage limit; "
                            "bypassing it until reset epoch %.0f",
                            member_id,
                            reset,
                        )
                self._logger.warning(
                    "LLM provider member %s failed on %s with %s",
                    member_id,
                    method_name,
                    type(exc).__name__,
                )
        if last_exc is not None:
            raise last_exc
        if attempted == 0:
            raise RuntimeError(
                "All LLM accounts are waiting for their reported quota reset"
            )
        raise RuntimeError("LLM failover chain completed without a result")
