from __future__ import annotations

import asyncio
import copy
import importlib.metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock


HINDSIGHT_ROOT = Path(__file__).resolve().parent.parent
LIB = HINDSIGHT_ROOT / "lib"
sys.path.insert(0, str(LIB))

import hindsight_memory_control_plane.provider_runtime as provider_runtime  # noqa: E402
from hindsight_memory_control_plane.provider_runtime import (  # noqa: E402
    HindsightProviderAdapter,
    ProviderRuntimeCompatibilityError,
    ProviderRuntimePolicy,
)
from hindsight_memory_control_plane.operation_recovery_progress import (  # noqa: E402
    ExactDrainProgressRecorder,
    read_exact_drain_progress,
)


def policy_data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "hindsight_version": "0.8.4",
        "default_usage_limit_cooldown_seconds": 300,
        "failover_order": ["personal", "work", "fallback"],
        "members": [
            {
                "id": "personal",
                "identity": {
                    "provider": "openai-codex",
                    "model": "codex-model",
                    "base_url": "",
                    "credential_marker": "provider-policy:personal",
                },
                "credential": {
                    "mode": "oauth-home",
                    "locator": "oauth-home:personal",
                },
                "timeout_seconds": None,
                "max_retries": 0,
                "max_concurrent": None,
                "operation_priorities": {
                    "default": 0,
                    "reflect": 0,
                    "retain": 20,
                    "consolidation": 30,
                },
                "quota_cooldown": True,
            },
            {
                "id": "work",
                "identity": {
                    "provider": "openai-codex",
                    "model": "codex-model",
                    "base_url": "",
                    "credential_marker": "provider-policy:work",
                },
                "credential": {
                    "mode": "oauth-home",
                    "locator": "oauth-home:work",
                },
                "timeout_seconds": None,
                "max_retries": 0,
                "max_concurrent": None,
                "operation_priorities": {
                    "default": 0,
                    "reflect": 0,
                    "retain": 20,
                    "consolidation": 30,
                },
                "quota_cooldown": True,
            },
            {
                "id": "fallback",
                "identity": {
                    "provider": "lmstudio",
                    "model": "private-fallback-model",
                    "base_url": "http://inference.example.test:13305/v1",
                    "credential_marker": None,
                },
                "credential": {"mode": "none", "locator": None},
                "timeout_seconds": 1200,
                "max_retries": 0,
                "max_concurrent": 1,
                "operation_priorities": {
                    "default": 0,
                    "reflect": 0,
                    "retain": 20,
                    "consolidation": 30,
                },
                "quota_cooldown": False,
            },
        ],
    }


def split_timeout_policy_data() -> dict[str, object]:
    value = copy.deepcopy(policy_data())
    value["schema_version"] = 2
    for member in value["members"]:
        execution_timeout = member.pop("timeout_seconds")
        member["queue_timeout_seconds"] = 3_600
        member["execution_timeout_seconds"] = (
            3_600 if execution_timeout is None else execution_timeout
        )
    return value


def four_codex_policy_data() -> dict[str, object]:
    value = copy.deepcopy(policy_data())
    personal, work, fallback = value["members"]
    alt1 = copy.deepcopy(personal)
    alt1["id"] = "alt1"
    alt1["identity"]["credential_marker"] = "provider-policy:alt1"
    alt1["credential"]["locator"] = "oauth-home:alt1"
    alt2 = copy.deepcopy(personal)
    alt2["id"] = "alt2"
    alt2["identity"]["credential_marker"] = "provider-policy:alt2"
    alt2["credential"]["locator"] = "oauth-home:alt2"
    value["failover_order"] = ["work", "personal", "alt1", "alt2", "fallback"]
    value["members"] = [work, personal, alt1, alt2, fallback]
    return value


def four_codex_split_timeout_policy_data() -> dict[str, object]:
    value = four_codex_policy_data()
    value["schema_version"] = 2
    for member in value["members"]:
        execution_timeout = member.pop("timeout_seconds")
        member["queue_timeout_seconds"] = 3_600
        member["execution_timeout_seconds"] = (
            3_600 if execution_timeout is None else execution_timeout
        )
    return value


def six_member_split_timeout_policy_data() -> dict[str, object]:
    value = four_codex_split_timeout_policy_data()
    value["schema_version"] = 3
    value["hindsight_version"] = "0.9.1"
    luna = copy.deepcopy(value["members"][0])
    luna["id"] = "openai-luna"
    luna["identity"] = {
        "provider": "openai-responses",
        "model": "gpt-5.6-luna",
        "base_url": "",
        "credential_marker": "provider-policy:openai-luna",
    }
    luna["credential"] = {
        "mode": "api-key",
        "locator": "api-key:hindsight-openai",
    }
    luna["quota_cooldown"] = True
    value["failover_order"] = [
        "work",
        "personal",
        "alt1",
        "alt2",
        "fallback",
        "openai-luna",
    ]
    value["members"].append(luna)
    return value


def four_codex_homes() -> dict[str, str]:
    return {
        "oauth-home:work": "/tmp/work-codex",
        "oauth-home:personal": "/tmp/personal-codex",
        "oauth-home:alt1": "/tmp/alt1-codex",
        "oauth-home:alt2": "/tmp/alt2-codex",
    }


class StaticMember:
    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        result: str | BaseException,
    ) -> None:
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.result = result
        self.calls = 0

    async def call(self, **_kwargs):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def four_codex_members() -> dict[str, StaticMember]:
    return {
        member_id: StaticMember(
            "openai-codex",
            "codex-model",
            "",
            f"provider-policy:{member_id}",
            member_id,
        )
        for member_id in ("work", "personal", "alt1", "alt2")
    }


class ProviderRuntimePolicyTest(unittest.TestCase):
    def test_api_key_member_is_a_closed_managed_fallback_identity(self) -> None:
        policy = ProviderRuntimePolicy.load(six_member_split_timeout_policy_data())

        luna = policy.member("openai-luna")
        self.assertEqual(luna.identity.provider, "openai-responses")
        self.assertEqual(luna.credential_mode, "api-key")
        self.assertEqual(luna.credential_locator, "api-key:hindsight-openai")
        self.assertTrue(luna.quota_cooldown)

        invalid = six_member_split_timeout_policy_data()
        invalid["members"][-1]["credential"]["locator"] = "/tmp/plaintext-key"
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "API key locator shape is invalid",
        ):
            ProviderRuntimePolicy.load(invalid)

        wrong_provider = six_member_split_timeout_policy_data()
        wrong_provider["members"][-1]["identity"]["provider"] = "lmstudio"
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "API key credentials require an OpenAI provider",
        ):
            ProviderRuntimePolicy.load(wrong_provider)

        older_runtime = six_member_split_timeout_policy_data()
        older_runtime["hindsight_version"] = "0.9.0"
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "openai-responses requires Hindsight 0.9.1",
        ):
            ProviderRuntimePolicy.load(older_runtime)

        historical_schema = six_member_split_timeout_policy_data()
        historical_schema["schema_version"] = 2
        historical_schema["members"][-1]["identity"]["provider"] = "openai"
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "credential mode is invalid",
        ):
            ProviderRuntimePolicy.load(historical_schema)

    def test_schema_two_closes_split_queue_and_execution_timeouts(self) -> None:
        policy = ProviderRuntimePolicy.load(split_timeout_policy_data())

        fallback = policy.member("fallback")
        self.assertEqual(policy.schema_version, 2)
        self.assertIsNone(fallback.timeout_seconds)
        self.assertEqual(fallback.queue_timeout_seconds, 3_600)
        self.assertEqual(fallback.execution_timeout_seconds, 1_200)

    def test_schema_one_keeps_total_wall_clock_timeout_semantics(self) -> None:
        policy = ProviderRuntimePolicy.load(policy_data())

        fallback = policy.member("fallback")
        self.assertEqual(policy.schema_version, 1)
        self.assertEqual(fallback.timeout_seconds, 1_200)
        self.assertIsNone(fallback.queue_timeout_seconds)
        self.assertIsNone(fallback.execution_timeout_seconds)
    def test_repository_example_is_a_valid_secret_free_policy(self) -> None:
        example = json.loads(
            (HINDSIGHT_ROOT / "examples/provider-runtime-policy.json").read_text()
        )

        policy = ProviderRuntimePolicy.load(example)

        self.assertEqual(policy.hindsight_version, "0.8.4")
        self.assertEqual(policy.member("private-fallback").identity.provider, "lmstudio")
        self.assertEqual(policy.member("private-fallback").max_concurrent, 2)
        self.assertNotIn("api_key", json.dumps(example).lower())

    @unittest.skipUnless(
        importlib.util.find_spec("hindsight_api"),
        "installed Hindsight runtime is unavailable",
    )
    def test_repository_example_fallback_matches_real_hindsight_runtime(self) -> None:
        from importlib import metadata

        from hindsight_api.engine.llm_wrapper import LLMProvider

        self.assertIn(
            metadata.version("hindsight-api"),
            provider_runtime.SUPPORTED_HINDSIGHT_VERSIONS,
        )
        example = json.loads(
            (HINDSIGHT_ROOT / "examples/provider-runtime-policy.json").read_text()
        )
        fallback = ProviderRuntimePolicy.load(example).member("private-fallback")

        runtime_member = LLMProvider(
            provider=fallback.identity.provider,
            api_key="non-secret-test-marker",
            base_url=fallback.identity.base_url,
            model=fallback.identity.model,
        )

        self.assertEqual(runtime_member.provider, fallback.identity.provider)
        self.assertEqual(runtime_member.model, fallback.identity.model)

    def test_loads_closed_data_driven_policy(self) -> None:
        policy = ProviderRuntimePolicy.load(policy_data())

        self.assertEqual(policy.failover_order, ("personal", "work", "fallback"))
        self.assertEqual(policy.member("fallback").max_concurrent, 1)
        self.assertEqual(policy.member("fallback").priority("retain_extract_facts"), 20)
        self.assertEqual(policy.member("fallback").priority("reflect"), 0)
        self.assertIsInstance(hash(policy.member("fallback")), int)
        with self.assertRaises(TypeError):
            policy.member("fallback").operation_priorities["reflect"] = 999

        invalid = {**policy_data(), "secret": "must-not-be-accepted"}
        with self.assertRaisesRegex(ProviderRuntimeCompatibilityError, "keys are closed"):
            ProviderRuntimePolicy.load(invalid)

    def test_matching_canonicalizes_host_case_and_default_port(self) -> None:
        value = policy_data()
        fallback = value["members"][2]
        fallback["identity"]["base_url"] = "https://example.test/v1"
        policy = ProviderRuntimePolicy.load(value)
        runtime_member = types.SimpleNamespace(
            provider="lmstudio",
            model="private-fallback-model",
            base_url="https://EXAMPLE.TEST:443/v1/",
            api_key="",
        )

        self.assertEqual(policy.match(runtime_member).id, "fallback")

    def test_matching_canonicalizes_an_unset_runtime_base_url(self) -> None:
        policy = ProviderRuntimePolicy.load(policy_data())
        runtime_member = types.SimpleNamespace(
            provider="openai-codex",
            model="codex-model",
            base_url=None,
            api_key="provider-policy:personal",
        )

        self.assertEqual(policy.match(runtime_member).id, "personal")

    def test_credential_markers_are_non_secret_member_references(self) -> None:
        invalid = policy_data()
        invalid["members"][0]["identity"]["credential_marker"] = "actual-secret-value"

        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "credential marker must equal provider-policy:personal",
        ):
            ProviderRuntimePolicy.load(invalid)

        resolved_path = policy_data()
        resolved_path["members"][0]["credential"]["locator"] = (
            "oauth-home:/Users/example/.codex"
        )
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "OAuth home locator shape is invalid",
        ):
            ProviderRuntimePolicy.load(resolved_path)

        duplicate_locator = policy_data()
        duplicate_locator["members"][1]["credential"]["locator"] = (
            "oauth-home:personal"
        )
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "OAuth home locators must be unique",
        ):
            ProviderRuntimePolicy.load(duplicate_locator)

        wrong_provider = policy_data()
        wrong_provider["members"][0]["identity"]["provider"] = "lmstudio"
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "OAuth home credentials require the openai-codex provider",
        ):
            ProviderRuntimePolicy.load(wrong_provider)

    def test_provider_identity_rejects_credential_bearing_urls(self) -> None:
        invalid = policy_data()
        invalid["members"][2]["identity"]["base_url"] = (
            "https://token@example.invalid/v1"
        )

        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "base_url cannot contain credentials",
        ):
            ProviderRuntimePolicy.load(invalid)

        unsupported_provider = policy_data()
        unsupported_provider["members"][2]["identity"]["provider"] = (
            "openai-compatible"
        )
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "runtime provider is not supported by Hindsight 0.8.4",
        ):
            ProviderRuntimePolicy.load(unsupported_provider)

    def test_non_finite_default_cooldowns_fail_closed(self) -> None:
        for value in (float("nan"), float("inf"), json.loads("1e400")):
            invalid = policy_data()
            invalid["default_usage_limit_cooldown_seconds"] = value
            with self.subTest(value=value), self.assertRaisesRegex(
                ProviderRuntimeCompatibilityError,
                "default_usage_limit_cooldown_seconds must be finite and positive",
            ):
                ProviderRuntimePolicy.load(invalid)

    def test_overlapping_exact_identities_fail_during_policy_load(self) -> None:
        invalid = policy_data()
        duplicate = dict(invalid["members"][2])
        duplicate["id"] = "other-fallback"
        invalid["members"].append(duplicate)
        invalid["failover_order"].append("other-fallback")

        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "provider identities must be unique",
        ):
            ProviderRuntimePolicy.load(invalid)

        wildcard_overlap = policy_data()
        scoped = wildcard_overlap["members"][0]
        wildcard_overlap["members"].append(
            {
                **scoped,
                "id": "wildcard-codex",
                "identity": {
                    **scoped["identity"],
                    "credential_marker": None,
                },
                "credential": {"mode": "none", "locator": None},
                "quota_cooldown": False,
            }
        )
        wildcard_overlap["failover_order"].append("wildcard-codex")
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "credential-free identity overlaps a credential-scoped identity",
        ):
            ProviderRuntimePolicy.load(wildcard_overlap)

        normalized_overlap = policy_data()
        fallback = normalized_overlap["members"][2]
        normalized_overlap["members"].append(
            {
                **fallback,
                "id": "slash-fallback",
                "identity": {
                    **fallback["identity"],
                    "base_url": f"{fallback['identity']['base_url']}/",
                },
            }
        )
        normalized_overlap["failover_order"].append("slash-fallback")
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "provider identities must be unique",
        ):
            ProviderRuntimePolicy.load(normalized_overlap)

    def test_unsupported_hindsight_version_fails_before_import_or_patch(self) -> None:
        policy = ProviderRuntimePolicy.load(policy_data())
        adapter = HindsightProviderAdapter(
            policy,
            credential_resolver=lambda _locator: "/tmp/unused",
            version_resolver=lambda: "0.9.0",
        )

        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "unsupported Hindsight version 0.9.0",
        ):
            adapter.install()

    def test_unverifiable_hindsight_version_fails_closed(self) -> None:
        policy = ProviderRuntimePolicy.load(policy_data())

        def missing_version() -> str:
            raise RuntimeError("package metadata unavailable")

        adapter = HindsightProviderAdapter(
            policy,
            credential_resolver=lambda _locator: "/tmp/unused",
            version_resolver=missing_version,
        )
        with self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "could not verify Hindsight version",
        ):
            adapter.install()


class HindsightProviderAdapterTest(unittest.TestCase):
    def test_disconnected_fallback_uses_split_connect_and_read_budgets(self) -> None:
        import httpx

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        timeout = provider_runtime._split_timeout(1200)

        async def request() -> None:
            async with httpx.AsyncClient(timeout=timeout) as client:
                with self.assertRaises(httpx.ConnectError):
                    await client.get(f"http://127.0.0.1:{port}/v1")

        try:
            asyncio.run(request())
        finally:
            listener.close()

        self.assertEqual(timeout.connect, 20)
        self.assertEqual(timeout.pool, 20)
        self.assertEqual(timeout.write, 60)
        self.assertEqual(timeout.read, 1200)

    def runtime_modules(self):
        class Client:
            def __init__(self, timeout: int) -> None:
                self.timeout = timeout

            def with_options(self, *, timeout: int):
                return Client(timeout)

        class ProviderImpl:
            def __init__(self, timeout: int, api_key: str) -> None:
                self.timeout = timeout
                self.api_key = api_key
                self._client = Client(timeout)

        class LLMProvider:
            def __init__(
                self,
                provider: str,
                api_key: str,
                base_url: str,
                model: str,
                timeout: int = 30,
                max_retries: int = 7,
                **_kwargs,
            ) -> None:
                self.provider = provider
                self.api_key = api_key
                self.base_url = base_url
                self.model = model
                self.timeout = timeout
                self.max_retries = max_retries
                self.reasoning_effort = _kwargs.get("reasoning_effort")
                self._provider_impl = ProviderImpl(timeout, api_key)
                self.operation = None

            async def call(self, **kwargs):
                if self.operation is not None:
                    return await self.operation(**kwargs)
                return kwargs

            async def call_with_tools(self, **kwargs):
                if self.operation is not None:
                    return await self.operation(**kwargs)
                return kwargs

        class CodexClient:
            def __init__(self) -> None:
                self.requests = []

            async def post(self, url, **kwargs):
                self.requests.append((url, kwargs))
                return "accepted"

            async def aclose(self):
                return None

        class CodexLLM:
            def __init__(
                self,
                reasoning_effort="low",
                **kwargs,
            ) -> None:
                self.codex_home = os.environ.get("CODEX_HOME")
                self.kwargs = {
                    **kwargs,
                    "reasoning_effort": reasoning_effort,
                }
                self.model = kwargs["model"]
                self.reasoning_effort = reasoning_effort
                self.reasoning_summary = "auto"
                self._client = CodexClient()
                self.original_client = self._client

            async def call(self):
                return await self._client.post(
                    "https://chatgpt.com/backend-api/codex/responses",
                    json={
                        "model": self.model,
                        "reasoning": {
                            "effort": self.reasoning_effort,
                            "summary": self.reasoning_summary,
                        },
                    },
                )

            async def cleanup(self):
                await self._client.aclose()

        class MultiLLMProvider:
            def __init__(self) -> None:
                self._members = []
                self._strategy = types.SimpleNamespace(mode="failover")
                self.verification = None

            async def _dispatch(self, _method_name: str, **_kwargs):
                return None

            async def verify_connection(self) -> None:
                if self.verification is not None:
                    await self.verification()

        multi_module = types.ModuleType("hindsight_api.engine.multi_llm")
        multi_module.MultiLLMProvider = MultiLLMProvider
        multi_module._should_failover = lambda exc: isinstance(exc, Exception)
        multi_module.logger = logging.getLogger("test-provider-runtime")
        codex_module = types.ModuleType("hindsight_api.engine.providers.codex_llm")
        codex_module.CodexLLM = CodexLLM
        wrapper_module = types.ModuleType("hindsight_api.engine.llm_wrapper")
        wrapper_module.LLMProvider = LLMProvider
        modules = {
            "hindsight_api.engine.multi_llm": multi_module,
            "hindsight_api.engine.providers.codex_llm": codex_module,
            "hindsight_api.engine.llm_wrapper": wrapper_module,
        }
        return modules, LLMProvider, CodexLLM, MultiLLMProvider

    def install(
        self,
        *,
        policy_value: dict[str, object] | None = None,
        homes: dict[str, str] | None = None,
        hindsight_version: str = "0.8.4",
    ):
        modules, *classes = self.runtime_modules()
        homes = homes or {
            "oauth-home:personal": "/tmp/personal-codex",
            "oauth-home:work": "/tmp/work-codex",
        }
        adapter = HindsightProviderAdapter(
            ProviderRuntimePolicy.load(policy_value or policy_data()),
            credential_resolver=homes.__getitem__,
            version_resolver=lambda: hindsight_version,
        )
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertTrue(adapter.install())
        return classes

    def test_install_supports_current_real_hindsight_provider_interfaces(self) -> None:
        value = policy_data()
        value["hindsight_version"] = importlib.metadata.version(
            "hindsight-api"
        )
        fallback = value["members"][2]
        fallback["identity"] = {
            "provider": "mock",
            "model": "mock-model",
            "base_url": "",
            "credential_marker": None,
        }
        worker_python = (
            Path.home()
            / ".local/share/uv/tools/hindsight-api/bin/python3"
        )
        script = """
import asyncio
import importlib.metadata
import json
import socket
import sys

def reject_network(*_args, **_kwargs):
    raise RuntimeError("network use is forbidden in compatibility test")

socket.socket.connect = reject_network
socket.create_connection = reject_network
sys.path.insert(0, sys.argv[1])
from hindsight_memory_control_plane.provider_runtime import (
    HindsightProviderAdapter,
    ProviderRuntimePolicy,
)
policy = ProviderRuntimePolicy.load(json.loads(sys.argv[2]))
if importlib.metadata.version("hindsight-api") != policy.hindsight_version:
    raise RuntimeError("real Hindsight test runtime differs")
HindsightProviderAdapter(
    policy,
    credential_resolver=lambda _locator: "/private/tmp/unread-oauth-home",
).install()
from hindsight_api.engine.llm_wrapper import LLMProvider
member = LLMProvider(
    provider="mock",
    api_key="",
    base_url="",
    model="mock-model",
)
result = asyncio.run(
    member.call(
        [{"role": "user", "content": "compatibility probe"}],
        scope="retain",
        skip_validation=True,
    )
)
if result != "mock response":
    raise RuntimeError("real Hindsight dispatch differs")
print("accepted")
"""
        result = subprocess.run(
            [
                str(worker_python),
                "-I",
                "-c",
                script,
                str(LIB),
                json.dumps(value, sort_keys=True),
            ],
            check=False,
            cwd="/",
            env={
                "HOME": str(Path.home()),
                "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "accepted")

    def test_install_constructs_current_real_openai_responses_member(self) -> None:
        worker_python = (
            Path.home()
            / ".local/share/uv/tools/hindsight-api/bin/python3"
        )
        script = """
import asyncio
import importlib.metadata
import json
import socket
import sys

def reject_network(*_args, **_kwargs):
    raise RuntimeError("network use is forbidden in compatibility test")

socket.socket.connect = reject_network
socket.create_connection = reject_network
sys.path.insert(0, sys.argv[1])
from hindsight_memory_control_plane.provider_runtime import (
    HindsightProviderAdapter,
    ProviderRuntimePolicy,
)
policy = ProviderRuntimePolicy.load(json.loads(sys.argv[2]))
if importlib.metadata.version("hindsight-api") != policy.hindsight_version:
    raise RuntimeError("real Hindsight test runtime differs")
marker = "provider-policy:openai-luna"
secret = "sk-synthetic-compatibility-only"
HindsightProviderAdapter(
    policy,
    credential_resolver=lambda locator: (
        secret
        if locator == "api-key:hindsight-openai"
        else "/private/tmp/unread-oauth-home"
    ),
).install()
from hindsight_api.engine.llm_wrapper import LLMProvider
member = LLMProvider(
    provider="openai-responses",
    api_key=marker,
    base_url="",
    model="gpt-5.6-luna",
    reasoning_effort="medium",
)
if member.api_key != marker:
    raise RuntimeError("wrapper retained resolved credential")
if member._hindsight_provider_credential_marker != marker:
    raise RuntimeError("wrapper marker is absent")
if member._provider_impl.api_key != secret:
    raise RuntimeError("provider did not receive resolved credential")
if policy.match(member).id != "openai-luna":
    raise RuntimeError("provider policy no longer matches constructed member")
asyncio.run(member.cleanup())
print("accepted")
"""
        result = subprocess.run(
            [
                str(worker_python),
                "-I",
                "-c",
                script,
                str(LIB),
                json.dumps(
                    {
                        **six_member_split_timeout_policy_data(),
                        "hindsight_version": importlib.metadata.version(
                            "hindsight-api"
                        ),
                    },
                    sort_keys=True,
                ),
            ],
            check=False,
            cwd="/",
            env={
                "HOME": str(Path.home()),
                "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "accepted")

    def test_install_resolves_independent_oauth_homes_and_exact_provider_policy(self) -> None:
        LLMProvider, CodexLLM, _MultiLLMProvider = self.install()

        prior = "/tmp/original-codex"
        with mock.patch.dict(os.environ, {"CODEX_HOME": prior}, clear=False):
            personal = CodexLLM(
                provider="openai-codex",
                api_key="provider-policy:personal",
                base_url=None,
                model="codex-model",
                reasoning_effort="xhigh",
            )
            work = CodexLLM(
                provider="openai-codex",
                api_key="provider-policy:work",
                base_url="",
                model="codex-model",
            )
            with self.assertRaisesRegex(
                ProviderRuntimeCompatibilityError,
                "provider identity does not match managed marker personal",
            ):
                CodexLLM(
                    provider="openai-codex",
                    api_key="provider-policy:personal",
                    base_url="",
                    model="different-model",
                )
            with self.assertRaisesRegex(
                ProviderRuntimeCompatibilityError,
                "unknown managed provider credential marker",
            ):
                CodexLLM(
                    provider="openai-codex",
                    api_key="provider-policy:unknown",
                    base_url="",
                    model="codex-model",
                )
            self.assertEqual(os.environ["CODEX_HOME"], prior)

        self.assertEqual(personal.codex_home, "/tmp/personal-codex")
        self.assertEqual(personal.kwargs["reasoning_effort"], "xhigh")
        self.assertEqual(work.codex_home, "/tmp/work-codex")
        self.assertIs(personal._client, personal.original_client)
        self.assertIs(work._client, work.original_client)
        self.assertEqual(asyncio.run(personal.call()), "accepted")
        _url, request = personal._client.requests[0]
        self.assertEqual(
            request["json"]["reasoning"],
            {"effort": "xhigh", "summary": "auto"},
        )

        fallback = LLMProvider(
            provider="lmstudio",
            api_key="",
            base_url="http://inference.example.test:13305/v1",
            model="private-fallback-model",
        )
        nearby = LLMProvider(
            provider="lmstudio",
            api_key="",
            base_url="http://localhost:13305/v1",
            model="private-fallback-model",
        )

        self.assertEqual(fallback.max_retries, 0)
        self.assertEqual(fallback.timeout, 1200)
        self.assertEqual(fallback._provider_impl.timeout, 1200)
        self.assertEqual(fallback._provider_impl._client.timeout.connect, 20)
        self.assertEqual(fallback._provider_impl._client.timeout.pool, 20)
        self.assertEqual(fallback._provider_impl._client.timeout.write, 60)
        self.assertEqual(fallback._provider_impl._client.timeout.read, 1200)
        self.assertEqual((nearby.timeout, nearby.max_retries), (30, 7))
        self.assertEqual(
            asyncio.run(fallback.call(max_retries=7))["max_retries"], 0
        )
        self.assertEqual(
            asyncio.run(nearby.call(max_retries=7))["max_retries"], 7
        )

    def test_install_resolves_four_independent_codex_homes(self) -> None:
        homes = four_codex_homes()
        _LLMProvider, CodexLLM, _MultiLLMProvider = self.install(
            policy_value=four_codex_policy_data(),
            homes=homes,
        )

        resolved = {}
        for member_id in ("work", "personal", "alt1", "alt2"):
            resolved[member_id] = CodexLLM(
                provider="openai-codex",
                api_key=f"provider-policy:{member_id}",
                base_url="",
                model="codex-model",
            ).codex_home

        self.assertEqual(
            resolved,
            {
                "work": "/tmp/work-codex",
                "personal": "/tmp/personal-codex",
                "alt1": "/tmp/alt1-codex",
                "alt2": "/tmp/alt2-codex",
            },
        )

    def test_managed_api_key_is_resolved_only_for_provider_construction(self) -> None:
        marker = "provider-policy:openai-luna"
        secret = "sk-test-runtime-only"
        LLMProvider, _CodexLLM, _MultiLLMProvider = self.install(
            policy_value=six_member_split_timeout_policy_data(),
            homes={
                **four_codex_homes(),
                "api-key:hindsight-openai": secret,
            },
            hindsight_version="0.9.1",
        )

        with mock.patch.object(
            provider_runtime,
            "_split_timeout",
            return_value=1_200,
        ):
            luna = LLMProvider(
                provider="openai-responses",
                api_key=marker,
                base_url="",
                model="gpt-5.6-luna",
                reasoning_effort="medium",
            )

        self.assertEqual(luna.api_key, marker)
        self.assertEqual(
            luna._hindsight_provider_credential_marker,
            marker,
        )
        self.assertEqual(luna._provider_impl.api_key, secret)
        self.assertEqual(luna.reasoning_effort, "medium")
        policy = ProviderRuntimePolicy.load(
            six_member_split_timeout_policy_data()
        )
        self.assertEqual(policy.match(luna).id, "openai-luna")

    def test_managed_api_key_resolution_failure_is_redacted(self) -> None:
        modules, LLMProvider, _CodexLLM, _MultiLLMProvider = (
            self.runtime_modules()
        )

        def failing_resolver(_locator: str) -> str:
            raise RuntimeError("sk-sensitive-resolver-output")

        adapter = HindsightProviderAdapter(
            ProviderRuntimePolicy.load(six_member_split_timeout_policy_data()),
            credential_resolver=failing_resolver,
            version_resolver=lambda: "0.9.1",
        )
        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(adapter.install())
            with self.assertRaisesRegex(
                ProviderRuntimeCompatibilityError,
                "API key resolution failed for openai-luna",
            ) as raised:
                LLMProvider(
                    provider="openai-responses",
                    api_key="provider-policy:openai-luna",
                    base_url="",
                    model="gpt-5.6-luna",
                    reasoning_effort="medium",
                )

        self.assertNotIn("sensitive", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def test_managed_api_key_provider_initialization_failure_is_redacted(self) -> None:
        modules, LLMProvider, _CodexLLM, _MultiLLMProvider = (
            self.runtime_modules()
        )

        def failing_init(
            _instance,
            provider: str,
            api_key: str,
            base_url: str,
            model: str,
            **_kwargs,
        ) -> None:
            raise RuntimeError(
                f"constructor rejected {provider} {api_key} {base_url} {model}"
            )

        LLMProvider.__init__ = failing_init
        adapter = HindsightProviderAdapter(
            ProviderRuntimePolicy.load(six_member_split_timeout_policy_data()),
            credential_resolver=lambda _locator: "sk-sensitive-constructor",
            version_resolver=lambda: "0.9.1",
        )
        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(adapter.install())
            with self.assertRaisesRegex(
                ProviderRuntimeCompatibilityError,
                "API key provider initialization failed for openai-luna",
            ) as raised:
                LLMProvider(
                    provider="openai-responses",
                    api_key="provider-policy:openai-luna",
                    base_url="",
                    model="gpt-5.6-luna",
                    reasoning_effort="medium",
                )

        self.assertNotIn("sensitive", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def test_managed_codex_53_omits_unsupported_reasoning_summary(self) -> None:
        value = policy_data()
        for member in value["members"]:
            if member["identity"]["provider"] == "openai-codex":
                member["identity"]["model"] = "gpt-5.3-codex-spark"
        _LLMProvider, CodexLLM, _MultiLLMProvider = self.install(
            policy_value=value,
        )
        member = CodexLLM(
            provider="openai-codex",
            api_key="provider-policy:personal",
            base_url="",
            model="gpt-5.3-codex-spark",
            reasoning_effort="low",
        )

        self.assertEqual(asyncio.run(member.call()), "accepted")
        _url, request = member._client.requests[0]
        self.assertEqual(
            request["json"]["reasoning"],
            {"effort": "low"},
        )

    def test_reinstalling_the_same_policy_is_an_idempotent_noop(self) -> None:
        modules, *_classes = self.runtime_modules()
        policy = ProviderRuntimePolicy.load(policy_data())
        adapter = HindsightProviderAdapter(
            policy,
            credential_resolver=lambda locator: f"/tmp/{locator.split(':')[-1]}",
            version_resolver=lambda: "0.8.4",
        )
        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(adapter.install())
            self.assertFalse(adapter.install())

    def test_incomplete_supported_interface_is_rejected_before_any_patch(self) -> None:
        modules, LLMProvider, _CodexLLM, _MultiLLMProvider = self.runtime_modules()
        del LLMProvider.call_with_tools
        original_init = LLMProvider.__init__
        original_call = LLMProvider.call
        adapter = HindsightProviderAdapter(
            ProviderRuntimePolicy.load(policy_data()),
            credential_resolver=lambda _locator: "/tmp/unused",
            version_resolver=lambda: "0.8.4",
        )

        with mock.patch.dict(sys.modules, modules), self.assertRaisesRegex(
            ProviderRuntimeCompatibilityError,
            "supported Hindsight provider interfaces are unavailable",
        ):
            adapter.install()

        self.assertIs(LLMProvider.__init__, original_init)
        self.assertIs(LLMProvider.call, original_call)

    def test_managed_codex_initialization_sanitizes_resolved_home_errors(self) -> None:
        modules, _LLMProvider, CodexLLM, _MultiLLMProvider = self.runtime_modules()
        original_init = CodexLLM.__init__

        def failing_init(instance, **_kwargs) -> None:
            raise RuntimeError(
                f"failed to load {os.environ.get('CODEX_HOME')}/auth.json"
            )

        CodexLLM.__init__ = failing_init
        adapter = HindsightProviderAdapter(
            ProviderRuntimePolicy.load(policy_data()),
            credential_resolver=lambda _locator: "/tmp/resolved-sensitive-home",
            version_resolver=lambda: "0.8.4",
        )
        try:
            with mock.patch.dict(sys.modules, modules):
                self.assertTrue(adapter.install())
                with self.assertRaisesRegex(
                    ProviderRuntimeCompatibilityError,
                    "Codex OAuth home initialization failed for personal",
                ) as raised:
                    CodexLLM(
                        provider="openai-codex",
                        api_key="provider-policy:personal",
                        base_url="",
                        model="codex-model",
                    )
            self.assertNotIn("resolved-sensitive-home", str(raised.exception))
            self.assertTrue(raised.exception.__suppress_context__)
            self.assertIsNone(raised.exception.__cause__)
        finally:
            CodexLLM.__init__ = original_init

    def test_oauth_home_resolver_errors_do_not_expose_resolver_output(self) -> None:
        modules, _LLMProvider, CodexLLM, _MultiLLMProvider = self.runtime_modules()

        def failing_resolver(_locator: str) -> str:
            raise RuntimeError("resolved-sensitive-home")

        adapter = HindsightProviderAdapter(
            ProviderRuntimePolicy.load(policy_data()),
            credential_resolver=failing_resolver,
            version_resolver=lambda: "0.8.4",
        )
        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(adapter.install())
            with self.assertRaisesRegex(
                ProviderRuntimeCompatibilityError,
                "OAuth home resolution failed for personal",
            ) as raised:
                CodexLLM(
                    provider="openai-codex",
                    api_key="provider-policy:personal",
                    base_url="",
                    model="codex-model",
                )

        self.assertNotIn("resolved-sensitive-home", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertIsNone(raised.exception.__cause__)

    def test_distinct_oauth_locators_cannot_resolve_to_the_same_home(self) -> None:
        modules, _LLMProvider, CodexLLM, _MultiLLMProvider = self.runtime_modules()
        adapter = HindsightProviderAdapter(
            ProviderRuntimePolicy.load(policy_data()),
            credential_resolver=lambda _locator: "/tmp/shared-codex-home",
            version_resolver=lambda: "0.8.4",
        )
        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(adapter.install())
            CodexLLM(
                provider="openai-codex",
                api_key="provider-policy:personal",
                base_url="",
                model="codex-model",
            )
            with self.assertRaisesRegex(
                ProviderRuntimeCompatibilityError,
                "OAuth home is already bound to personal",
            ):
                CodexLLM(
                    provider="openai-codex",
                    api_key="provider-policy:work",
                    base_url="",
                    model="codex-model",
                )

    def test_dispatch_uses_declared_order_and_skips_quota_limited_account(self) -> None:
        _LLMProvider, _CodexLLM, MultiLLMProvider = self.install()

        class Response:
            status_code = 429

            @staticmethod
            def json():
                return {
                    "error": {
                        "type": "usage_limit_reached",
                        "resets_in_seconds": float("inf"),
                    }
                }

        class UsageLimit(Exception):
            response = Response()

        class Member:
            def __init__(
                self,
                provider: str,
                model: str,
                base_url: str,
                api_key: str,
                outcomes: list[object],
            ) -> None:
                self.provider = provider
                self.model = model
                self.base_url = base_url
                self.api_key = api_key
                self.outcomes = outcomes
                self.calls = 0

            async def call(self, **_kwargs):
                outcome = self.outcomes[self.calls]
                self.calls += 1
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        personal = Member(
            "openai-codex",
            "codex-model",
            "",
            "provider-policy:personal",
            [
                UsageLimit("credential-secret-must-not-be-logged"),
                "personal should be cooling down",
            ],
        )
        work = Member(
            "openai-codex",
            "codex-model",
            "",
            "provider-policy:work",
            ["work first", "work second"],
        )
        fallback = Member(
            "lmstudio",
            "private-fallback-model",
            "http://inference.example.test:13305/v1",
            "",
            ["fallback"],
        )
        provider = MultiLLMProvider()
        provider._members = [fallback, work, personal]

        with self.assertLogs("test-provider-runtime", level="WARNING") as logs:
            first = asyncio.run(provider._dispatch("call"))
            second = asyncio.run(provider._dispatch("call"))

        self.assertEqual((first, second), ("work first", "work second"))
        self.assertEqual((personal.calls, work.calls, fallback.calls), (1, 2, 0))
        self.assertNotIn("credential-secret-must-not-be-logged", "\n".join(logs.output))
        self.assertNotIn("inf", "\n".join(logs.output).lower())

    def test_round_robin_rotates_only_the_primary_codex_tier(self) -> None:
        _LLMProvider, _CodexLLM, MultiLLMProvider = self.install(
            policy_value=four_codex_policy_data(),
            homes=four_codex_homes(),
        )

        codex_members = four_codex_members()
        fallback = StaticMember(
            "lmstudio",
            "private-fallback-model",
            "http://inference.example.test:13305/v1",
            "",
            "fallback",
        )
        provider = MultiLLMProvider()
        provider._strategy.mode = "round-robin"
        provider._members = [
            fallback,
            codex_members["alt2"],
            codex_members["alt1"],
            codex_members["personal"],
            codex_members["work"],
        ]

        observed = [
            asyncio.run(provider._dispatch("call"))
            for _request in range(6)
        ]

        self.assertEqual(
            observed,
            ["work", "personal", "alt1", "alt2", "work", "personal"],
        )
        self.assertEqual(fallback.calls, 0)

    def test_round_robin_uses_hatchery_only_after_all_codex_members_fail(self) -> None:
        _LLMProvider, _CodexLLM, MultiLLMProvider = self.install(
            policy_value=four_codex_policy_data(),
            homes=four_codex_homes(),
        )

        codex_members = four_codex_members()
        for member in codex_members.values():
            member.result = ConnectionError("codex unavailable")
        fallback = StaticMember(
            "lmstudio",
            "private-fallback-model",
            "http://inference.example.test:13305/v1",
            "",
            "fallback",
        )
        provider = MultiLLMProvider()
        provider._strategy.mode = "round-robin"
        provider._members = [
            codex_members["personal"],
            fallback,
            codex_members["alt2"],
            codex_members["work"],
            codex_members["alt1"],
        ]

        with self.assertLogs("test-provider-runtime", level="WARNING"):
            self.assertEqual(asyncio.run(provider._dispatch("call")), "fallback")

        codex_members["personal"].result = "personal"
        self.assertEqual(asyncio.run(provider._dispatch("call")), "personal")

        self.assertEqual(
            (
                codex_members["work"].calls,
                codex_members["personal"].calls,
                codex_members["alt1"].calls,
                codex_members["alt2"].calls,
                fallback.calls,
            ),
            (1, 2, 1, 1, 1),
        )

    def test_round_robin_uses_luna_only_after_codex_and_hatchery_fail(self) -> None:
        _LLMProvider, _CodexLLM, MultiLLMProvider = self.install(
            policy_value=six_member_split_timeout_policy_data(),
            homes=four_codex_homes(),
            hindsight_version="0.9.1",
        )

        codex_members = four_codex_members()
        for member in codex_members.values():
            member.result = ConnectionError("codex unavailable")
        hatchery = StaticMember(
            "lmstudio",
            "private-fallback-model",
            "http://inference.example.test:13305/v1",
            "",
            "hatchery",
        )
        luna = StaticMember(
            "openai-responses",
            "gpt-5.6-luna",
            "",
            "provider-policy:openai-luna",
            "luna",
        )
        provider = MultiLLMProvider()
        provider._strategy.mode = "round-robin"
        provider._members = [
            luna,
            codex_members["personal"],
            hatchery,
            codex_members["alt2"],
            codex_members["work"],
            codex_members["alt1"],
        ]

        with self.assertLogs("test-provider-runtime", level="WARNING"):
            self.assertEqual(asyncio.run(provider._dispatch("call")), "hatchery")
        self.assertEqual(luna.calls, 0)

        hatchery.result = ConnectionError("hatchery unavailable")
        with self.assertLogs("test-provider-runtime", level="WARNING"):
            self.assertEqual(asyncio.run(provider._dispatch("call")), "luna")
        self.assertEqual((hatchery.calls, luna.calls), (2, 1))

    def test_usage_limit_reset_hint_is_capped_to_the_probe_cooldown(self) -> None:
        class Response:
            status_code = 429

            @staticmethod
            def json():
                return {
                    "error": {
                        "type": "usage_limit_reached",
                        "resets_at": 99_999,
                    }
                }

        class UsageLimit(Exception):
            response = Response()

        self.assertEqual(
            provider_runtime._usage_limit_reset_at(
                UsageLimit(),
                now=1_000,
                default_cooldown=300,
            ),
            1_300,
        )

    def test_long_running_provider_progress_is_queryable_before_completion(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            progress_path = Path(directory) / "progress.json"
            recorder = ExactDrainProgressRecorder(
                path=progress_path,
                plan_digest="a" * 64,
                worker_pid=os.getpid(),
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": "00000000-0000-4000-8000-000000000001",
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
            )
            provider_runtime.set_exact_drain_progress_recorder(recorder)
            self.addCleanup(
                provider_runtime.set_exact_drain_progress_recorder,
                None,
            )
            _LLMProvider, _CodexLLM, MultiLLMProvider = self.install(
                policy_value=four_codex_policy_data(),
                homes=four_codex_homes(),
            )
            members = four_codex_members()
            started = asyncio.Event()
            release = asyncio.Event()

            async def long_call(**_kwargs):
                started.set()
                await release.wait()
                return "work"

            members["work"].call = long_call
            fallback = StaticMember(
                "lmstudio",
                "private-fallback-model",
                "http://inference.example.test:13305/v1",
                "",
                "fallback",
            )
            provider = MultiLLMProvider()
            provider._strategy.mode = "round-robin"
            provider._members = [*members.values(), fallback]

            async def scenario():
                pending = asyncio.create_task(
                    provider._dispatch("call", scope="retain_extract_facts")
                )
                await started.wait()
                active = read_exact_drain_progress(
                    progress_path,
                    plan_digest="a" * 64,
                )
                release.set()
                result = await pending
                finished = read_exact_drain_progress(
                    progress_path,
                    plan_digest="a" * 64,
                )
                return active, result, finished

            active, result, finished = asyncio.run(scenario())

        self.assertEqual(result, "work")
        self.assertEqual(
            active["active_provider_requests"][0]["provider_id"],
            "work",
        )
        self.assertEqual(active["provider_counters"][0]["started"], 1)
        self.assertEqual(finished["active_provider_requests"], [])
        self.assertEqual(finished["provider_counters"][0]["succeeded"], 1)

    def test_managed_provider_timeout_is_a_total_wall_clock_deadline(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            progress_path = Path(directory) / "progress.json"
            recorder = ExactDrainProgressRecorder(
                path=progress_path,
                plan_digest="a" * 64,
                worker_pid=os.getpid(),
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": "00000000-0000-4000-8000-000000000001",
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
            )
            provider_runtime.set_exact_drain_progress_recorder(recorder)
            self.addCleanup(
                provider_runtime.set_exact_drain_progress_recorder,
                None,
            )
            value = policy_data()
            value["members"][2]["timeout_seconds"] = 1
            LLMProvider, _CodexLLM, MultiLLMProvider = self.install(
                policy_value=value,
            )

            members = [
                StaticMember(
                    "openai-codex",
                    "codex-model",
                    "",
                    "provider-policy:personal",
                    ConnectionError("personal unavailable"),
                ),
                StaticMember(
                    "openai-codex",
                    "codex-model",
                    "",
                    "provider-policy:work",
                    ConnectionError("work unavailable"),
                ),
                LLMProvider(
                    "lmstudio",
                    "",
                    "http://inference.example.test:13305/v1",
                    "private-fallback-model",
                ),
            ]

            async def never_finishes(**_kwargs):
                await asyncio.Event().wait()

            members[2].operation = never_finishes
            provider = MultiLLMProvider()
            provider._members = members

            async def scenario():
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(
                        provider._dispatch(
                            "call",
                            scope="retain_extract_facts",
                        ),
                        timeout=2.0,
                    )
                return read_exact_drain_progress(
                    progress_path,
                    plan_digest="a" * 64,
                )

            finished = asyncio.run(scenario())

        fallback = next(
            item
            for item in finished["provider_counters"]
            if item["provider_id"] == "fallback"
        )
        self.assertEqual(fallback["timed_out"], 1)
        self.assertEqual(finished["active_provider_requests"], [])

    def test_split_timeout_wait_does_not_consume_execution_budget(self) -> None:
        value = split_timeout_policy_data()
        value["members"][2]["queue_timeout_seconds"] = 2
        value["members"][2]["execution_timeout_seconds"] = 1
        LLMProvider, _CodexLLM, _MultiLLMProvider = self.install(
            policy_value=value,
        )
        member = LLMProvider(
            "lmstudio",
            "",
            "http://inference.example.test:13305/v1",
            "private-fallback-model",
        )
        entered = 0

        async def bounded_call(**_kwargs):
            nonlocal entered
            entered += 1
            await asyncio.sleep(0.6)
            return entered

        member.operation = bounded_call

        async def scenario():
            return await asyncio.wait_for(
                asyncio.gather(member.call(), member.call()),
                timeout=2.0,
            )

        self.assertEqual(asyncio.run(scenario()), [1, 2])

    def test_split_progress_distinguishes_one_executing_and_two_queued(self) -> None:
        value = split_timeout_policy_data()
        value["failover_order"] = ["fallback"]
        value["members"] = [value["members"][2]]
        value["members"][0]["queue_timeout_seconds"] = 2
        value["members"][0]["execution_timeout_seconds"] = 2
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            progress_path = Path(directory) / "progress.json"
            recorder = ExactDrainProgressRecorder(
                path=progress_path,
                plan_digest="a" * 64,
                worker_pid=os.getpid(),
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                progress_schema_version=4,
            )
            provider_runtime.set_exact_drain_progress_recorder(recorder)
            self.addCleanup(
                provider_runtime.set_exact_drain_progress_recorder,
                None,
            )
            LLMProvider, _CodexLLM, MultiLLMProvider = self.install(
                policy_value=value,
            )
            with mock.patch.object(
                provider_runtime,
                "_split_timeout",
                return_value=1_200,
            ):
                member = LLMProvider(
                    "lmstudio",
                    "",
                    "http://inference.example.test:13305/v1",
                    "private-fallback-model",
                )
            first_entered = asyncio.Event()
            release = asyncio.Event()

            async def blocked_call(**_kwargs):
                first_entered.set()
                await release.wait()
                return "done"

            member.operation = blocked_call
            provider = MultiLLMProvider()
            provider._members = [member]

            async def scenario():
                pending = [
                    asyncio.create_task(
                        provider._dispatch(
                            "call",
                            scope="retain_extract_facts",
                        )
                    )
                    for _index in range(3)
                ]
                await first_entered.wait()
                for _index in range(100):
                    active = read_exact_drain_progress(
                        progress_path,
                        plan_digest="a" * 64,
                        progress_schema_version=4,
                    )
                    if len(active["active_provider_requests"]) == 3:
                        break
                    await asyncio.sleep(0)
                else:
                    self.fail("provider requests did not reach the queue")
                release.set()
                results = await asyncio.gather(*pending)
                return active, results

            active, results = asyncio.run(
                asyncio.wait_for(scenario(), timeout=2.5)
            )

        self.assertEqual(results, ["done", "done", "done"])
        self.assertEqual(
            sorted(
                request["state"]
                for request in active["active_provider_requests"]
            ),
            ["executing", "queued", "queued"],
        )

    def test_split_progress_cancels_queued_request_without_provider_failure(self) -> None:
        value = split_timeout_policy_data()
        value["failover_order"] = ["fallback"]
        value["members"] = [value["members"][2]]
        value["members"][0]["max_concurrent"] = 2
        value["members"][0]["queue_timeout_seconds"] = 2
        value["members"][0]["execution_timeout_seconds"] = 2
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            progress_path = Path(directory) / "progress.json"
            recorder = ExactDrainProgressRecorder(
                path=progress_path,
                plan_digest="a" * 64,
                worker_pid=os.getpid(),
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                progress_schema_version=5,
            )
            provider_runtime.set_exact_drain_progress_recorder(recorder)
            self.addCleanup(
                provider_runtime.set_exact_drain_progress_recorder,
                None,
            )
            LLMProvider, _CodexLLM, MultiLLMProvider = self.install(
                policy_value=value,
            )
            with mock.patch.object(
                provider_runtime,
                "_split_timeout",
                return_value=1_200,
            ):
                member = LLMProvider(
                    "lmstudio",
                    "",
                    "http://inference.example.test:13305/v1",
                    "private-fallback-model",
                )
            entered = 0
            two_entered = asyncio.Event()
            release = asyncio.Event()

            async def blocked_call(**_kwargs):
                nonlocal entered
                entered += 1
                if entered == 2:
                    two_entered.set()
                await release.wait()
                return "done"

            member.operation = blocked_call
            provider = MultiLLMProvider()
            provider._members = [member]

            async def scenario():
                pending = [
                    asyncio.create_task(
                        provider._dispatch(
                            "call",
                            scope="retain_extract_facts",
                        )
                    )
                    for _index in range(3)
                ]
                await two_entered.wait()
                for _index in range(100):
                    active = read_exact_drain_progress(
                        progress_path,
                        plan_digest="a" * 64,
                        progress_schema_version=5,
                    )
                    states = sorted(
                        request["state"]
                        for request in active["active_provider_requests"]
                    )
                    if states == ["executing", "executing", "queued"]:
                        break
                    await asyncio.sleep(0)
                else:
                    self.fail("provider requests did not reach the queue")
                pending[2].cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending[2]
                after_cancel = read_exact_drain_progress(
                    progress_path,
                    plan_digest="a" * 64,
                    progress_schema_version=5,
                )
                release.set()
                results = await asyncio.gather(*pending[:2])
                finished = read_exact_drain_progress(
                    progress_path,
                    plan_digest="a" * 64,
                    progress_schema_version=5,
                )
                return after_cancel, finished, results

            with mock.patch.object(
                provider_runtime,
                "_split_timeout",
                return_value=1_200,
            ):
                after_cancel, finished, results = asyncio.run(
                    asyncio.wait_for(scenario(), timeout=2.5)
                )

        self.assertEqual(results, ["done", "done"])
        self.assertEqual(len(after_cancel["active_provider_requests"]), 2)
        self.assertEqual(after_cancel["provider_counters"][0]["failed"], 0)
        self.assertEqual(
            after_cancel["provider_counters"][0]["queue_cancelled"],
            1,
        )
        self.assertEqual(
            after_cancel["provider_counters"][0]["execution_cancelled"],
            0,
        )
        self.assertEqual(finished["active_provider_requests"], [])
        self.assertEqual(finished["provider_counters"][0]["started"], 3)
        self.assertEqual(finished["provider_counters"][0]["succeeded"], 2)
        self.assertEqual(
            finished["provider_counters"][0]["queue_cancelled"],
            1,
        )
        self.assertEqual(
            finished["provider_counters"][0]["execution_cancelled"],
            0,
        )
        self.assertEqual(finished["provider_counters"][0]["failed"], 0)

    def test_split_progress_cancels_executing_request_without_provider_failure(
        self,
    ) -> None:
        value = split_timeout_policy_data()
        value["failover_order"] = ["fallback"]
        value["members"] = [value["members"][2]]
        value["members"][0]["max_concurrent"] = 2
        value["members"][0]["queue_timeout_seconds"] = 2
        value["members"][0]["execution_timeout_seconds"] = 2
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            progress_path = Path(directory) / "progress.json"
            recorder = ExactDrainProgressRecorder(
                path=progress_path,
                plan_digest="a" * 64,
                worker_pid=os.getpid(),
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                progress_schema_version=5,
            )
            provider_runtime.set_exact_drain_progress_recorder(recorder)
            self.addCleanup(
                provider_runtime.set_exact_drain_progress_recorder,
                None,
            )
            LLMProvider, _CodexLLM, MultiLLMProvider = self.install(
                policy_value=value,
            )
            with mock.patch.object(
                provider_runtime,
                "_split_timeout",
                return_value=1_200,
            ):
                member = LLMProvider(
                    "lmstudio",
                    "",
                    "http://inference.example.test:13305/v1",
                    "private-fallback-model",
                )
            entered = asyncio.Event()

            async def blocked_call(**_kwargs):
                entered.set()
                await asyncio.Event().wait()

            member.operation = blocked_call
            provider = MultiLLMProvider()
            provider._members = [member]

            async def scenario():
                pending = asyncio.create_task(
                    provider._dispatch(
                        "call",
                        scope="retain_extract_facts",
                    )
                )
                await entered.wait()
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
                return read_exact_drain_progress(
                    progress_path,
                    plan_digest="a" * 64,
                    progress_schema_version=5,
                )

            with mock.patch.object(
                provider_runtime,
                "_split_timeout",
                return_value=1_200,
            ):
                after_cancel = asyncio.run(
                    asyncio.wait_for(scenario(), timeout=2.5)
                )

        self.assertEqual(after_cancel["active_provider_requests"], [])
        self.assertEqual(after_cancel["provider_counters"][0]["started"], 1)
        self.assertEqual(after_cancel["provider_counters"][0]["succeeded"], 0)
        self.assertEqual(after_cancel["provider_counters"][0]["failed"], 0)
        self.assertEqual(
            after_cancel["provider_counters"][0]["queue_cancelled"],
            0,
        )
        self.assertEqual(
            after_cancel["provider_counters"][0]["execution_cancelled"],
            1,
        )

    def test_split_concurrency_two_executes_two_and_queues_third(self) -> None:
        value = split_timeout_policy_data()
        value["members"][2]["max_concurrent"] = 2
        LLMProvider, _CodexLLM, _MultiLLMProvider = self.install(
            policy_value=value,
        )
        member = LLMProvider.__new__(LLMProvider)
        with mock.patch.object(
            provider_runtime,
            "_split_timeout",
            return_value=1_200,
        ):
            LLMProvider.__init__(
                member,
                "lmstudio",
                "",
                "http://inference.example.test:13305/v1",
                "private-fallback-model",
            )
        entered = 0
        two_entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_call(**_kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                two_entered.set()
            await release.wait()
            return "done"

        member.operation = blocked_call

        async def scenario():
            pending = [
                asyncio.create_task(member.call())
                for _index in range(3)
            ]
            await two_entered.wait()
            await asyncio.sleep(0)
            entered_before_release = entered
            release.set()
            return entered_before_release, await asyncio.gather(*pending)

        with mock.patch.object(
            provider_runtime,
            "_split_timeout",
            return_value=1_200,
        ):
            entered_before_release, results = asyncio.run(
                asyncio.wait_for(scenario(), timeout=2.5)
            )

        self.assertEqual(entered_before_release, 2)
        self.assertEqual(results, ["done", "done", "done"])

    def test_split_queue_timeout_never_enters_provider_operation(self) -> None:
        value = split_timeout_policy_data()
        value["members"][2]["queue_timeout_seconds"] = 1
        value["members"][2]["execution_timeout_seconds"] = 3
        LLMProvider, _CodexLLM, _MultiLLMProvider = self.install(
            policy_value=value,
        )
        member = LLMProvider(
            "lmstudio",
            "",
            "http://inference.example.test:13305/v1",
            "private-fallback-model",
        )
        first_entered = asyncio.Event()
        release = asyncio.Event()
        entered = 0

        async def blocked_call(**_kwargs):
            nonlocal entered
            entered += 1
            first_entered.set()
            await release.wait()
            return "done"

        member.operation = blocked_call

        async def scenario():
            first = asyncio.create_task(member.call())
            await first_entered.wait()
            with self.assertRaises(provider_runtime.ProviderQueueTimeout):
                await member.call()
            self.assertEqual(entered, 1)
            release.set()
            await first

        asyncio.run(asyncio.wait_for(scenario(), timeout=2.5))

    def test_split_transport_timeout_is_classified_as_execution_timeout(self) -> None:
        import httpx

        LLMProvider, _CodexLLM, _MultiLLMProvider = self.install(
            policy_value=split_timeout_policy_data(),
        )
        member = LLMProvider(
            "lmstudio",
            "",
            "http://inference.example.test:13305/v1",
            "private-fallback-model",
        )

        async def transport_timeout(**_kwargs):
            raise httpx.ReadTimeout("synthetic transport timeout")

        member.operation = transport_timeout

        with self.assertRaises(provider_runtime.ProviderExecutionTimeout):
            asyncio.run(member.call())

    def test_split_execution_deadline_rejects_late_completion(self) -> None:
        policy = ProviderRuntimePolicy.load(split_timeout_policy_data())
        clock_values = iter((100.0, 100.0, 100.0, 1_301.0))
        runtime = provider_runtime._ProviderRuntime(
            policy,
            credential_resolver=lambda _locator: "/tmp/oauth",
            logger=logging.getLogger("test-provider-runtime-deadline"),
            clock=lambda: next(clock_values),
        )
        member = StaticMember(
            "lmstudio",
            "private-fallback-model",
            "http://inference.example.test:13305/v1",
            "",
            "done-after-deadline",
        )

        async def completed(**_kwargs):
            return "done"

        with self.assertRaises(provider_runtime.ProviderExecutionTimeout):
            asyncio.run(runtime.call(member, completed))

    def test_fallback_verification_timeout_does_not_block_startup(self) -> None:
        _LLMProvider, _CodexLLM, MultiLLMProvider = self.install()

        provider = MultiLLMProvider()

        async def never_finishes() -> None:
            await asyncio.Event().wait()

        provider.verification = never_finishes
        with mock.patch.object(
            provider_runtime,
            "STARTUP_LLM_VERIFICATION_TIMEOUT_SECONDS",
            0.01,
        ), self.assertLogs("test-provider-runtime", level="WARNING") as logs:
            asyncio.run(asyncio.wait_for(provider.verify_connection(), timeout=0.1))

        self.assertIn(
            "LLM startup verification exceeded 0.01s",
            "\n".join(logs.output),
        )

    def test_fast_startup_verification_failure_does_not_block_startup(self) -> None:
        _LLMProvider, _CodexLLM, MultiLLMProvider = self.install()

        provider = MultiLLMProvider()

        async def fails_fast() -> None:
            raise ConnectionError("fallback DNS unavailable")

        provider.verification = fails_fast
        with self.assertLogs("test-provider-runtime", level="WARNING") as logs:
            asyncio.run(provider.verify_connection())

        rendered_logs = "\n".join(logs.output)
        self.assertIn(
            "LLM startup verification failed with ConnectionError",
            rendered_logs,
        )
        self.assertNotIn("fallback DNS unavailable", rendered_logs)

    def test_expired_cooldown_allows_only_one_concurrent_probe(self) -> None:
        policy = ProviderRuntimePolicy.load(policy_data())
        runtime = provider_runtime._ProviderRuntime(
            policy,
            credential_resolver=lambda locator: f"/tmp/{locator.split(':')[-1]}",
            logger=logging.getLogger("test-provider-runtime-probe"),
            clock=lambda: 1_300,
        )
        runtime._cooldowns["personal"] = 1_300

        personal_started = asyncio.Event()
        release_personal = asyncio.Event()

        class ProbeMember(StaticMember):
            async def call(self, **_kwargs):
                self.calls += 1
                personal_started.set()
                await release_personal.wait()
                return self.result

        personal = ProbeMember(
            "openai-codex",
            "codex-model",
            "",
            "provider-policy:personal",
            "personal",
        )
        work = StaticMember(
            "openai-codex",
            "codex-model",
            "",
            "provider-policy:work",
            "work",
        )
        fallback = StaticMember(
            "lmstudio",
            "private-fallback-model",
            "http://inference.example.test:13305/v1",
            "",
            "fallback",
        )

        async def scenario() -> tuple[str, str]:
            first = asyncio.create_task(
                runtime.dispatch(
                    [fallback, work, personal],
                    "call",
                    {},
                    lambda exc: isinstance(exc, Exception),
                    strategy_mode="failover",
                )
            )
            await personal_started.wait()
            second = asyncio.create_task(
                runtime.dispatch(
                    [personal, fallback, work],
                    "call",
                    {},
                    lambda exc: isinstance(exc, Exception),
                    strategy_mode="failover",
                )
            )
            await asyncio.sleep(0)
            release_personal.set()
            return await first, await second

        self.assertEqual(asyncio.run(scenario()), ("personal", "work"))
        self.assertEqual((personal.calls, work.calls), (1, 1))

    def test_member_gate_serializes_and_prioritizes_interactive_work(self) -> None:
        LLMProvider, _CodexLLM, _MultiLLMProvider = self.install()
        fallback = LLMProvider(
            provider="lmstudio",
            api_key="",
            base_url="http://inference.example.test:13305/v1",
            model="private-fallback-model",
        )

        async def scenario() -> list[str]:
            first_started = asyncio.Event()
            release_first = asyncio.Event()
            started: list[str] = []

            async def call(**kwargs):
                label = kwargs["label"]
                started.append(label)
                if label == "first":
                    first_started.set()
                    await release_first.wait()
                return kwargs

            fallback.operation = call
            first = asyncio.create_task(
                fallback.call(label="first", scope="retain_extract_facts")
            )
            await first_started.wait()
            bulk = asyncio.create_task(
                fallback.call(label="bulk", scope="retain_extract_facts")
            )
            await asyncio.sleep(0)
            reflect = asyncio.create_task(
                fallback.call(label="reflect", scope="reflect")
            )
            await asyncio.sleep(0)
            release_first.set()
            await asyncio.gather(first, bulk, reflect)
            return started

        self.assertEqual(asyncio.run(scenario()), ["first", "reflect", "bulk"])

    def test_member_gate_serializes_calls_across_event_loops(self) -> None:
        LLMProvider, _CodexLLM, _MultiLLMProvider = self.install()
        fallback = LLMProvider(
            provider="lmstudio",
            api_key="",
            base_url="http://inference.example.test:13305/v1",
            model="private-fallback-model",
        )
        first_entered = threading.Event()
        second_entered = threading.Event()
        release_first = threading.Event()
        errors: list[BaseException] = []

        async def call(**kwargs):
            if kwargs["label"] == "first":
                first_entered.set()
                release_first.wait(timeout=2)
            else:
                second_entered.set()
            return kwargs

        fallback.operation = call

        def invoke(label: str) -> None:
            try:
                asyncio.run(fallback.call(label=label, scope="reflect"))
            except BaseException as error:
                errors.append(error)

        first = threading.Thread(target=invoke, args=("first",), daemon=True)
        second = threading.Thread(target=invoke, args=("second",), daemon=True)
        first.start()
        self.assertTrue(first_entered.wait(timeout=1))
        second.start()
        try:
            self.assertFalse(second_entered.wait(timeout=0.2))
        finally:
            release_first.set()
            first.join(timeout=2)
            second.join(timeout=2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(second_entered.is_set())


if __name__ == "__main__":
    unittest.main()
