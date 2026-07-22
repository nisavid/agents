from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock


HINDSIGHT_ROOT = Path(__file__).resolve().parent.parent
LIB = HINDSIGHT_ROOT / "lib"
sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.provider_runtime import (  # noqa: E402
    HindsightProviderAdapter,
    ProviderRuntimeCompatibilityError,
    ProviderRuntimePolicy,
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
                "max_retries": None,
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
                "max_retries": None,
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
                "timeout_seconds": 300,
                "max_retries": 1,
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


class ProviderRuntimePolicyTest(unittest.TestCase):
    def test_repository_example_is_a_valid_secret_free_policy(self) -> None:
        example = json.loads(
            (HINDSIGHT_ROOT / "examples/provider-runtime-policy.json").read_text()
        )

        policy = ProviderRuntimePolicy.load(example)

        self.assertEqual(policy.hindsight_version, "0.8.4")
        self.assertEqual(policy.member("private-fallback").identity.provider, "lmstudio")
        self.assertNotIn("api_key", json.dumps(example).lower())

    @unittest.skipUnless(
        importlib.util.find_spec("hindsight_api"),
        "installed Hindsight runtime is unavailable",
    )
    def test_repository_example_fallback_matches_real_hindsight_runtime(self) -> None:
        from importlib import metadata

        from hindsight_api.engine.llm_wrapper import LLMProvider

        self.assertEqual(metadata.version("hindsight-api"), "0.8.4")
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
    def runtime_modules(self):
        class Client:
            def __init__(self, timeout: int) -> None:
                self.timeout = timeout

            def with_options(self, *, timeout: int):
                return Client(timeout)

        class ProviderImpl:
            def __init__(self, timeout: int) -> None:
                self.timeout = timeout
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
                self._provider_impl = ProviderImpl(timeout)
                self.operation = None

            async def call(self, **kwargs):
                if self.operation is not None:
                    return await self.operation(**kwargs)
                return kwargs

            async def call_with_tools(self, **kwargs):
                if self.operation is not None:
                    return await self.operation(**kwargs)
                return kwargs

        class CodexLLM:
            def __init__(self, **kwargs) -> None:
                self.codex_home = os.environ.get("CODEX_HOME")
                self.kwargs = kwargs

        class MultiLLMProvider:
            async def _dispatch(self, _method_name: str, **_kwargs):
                return None

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

    def install(self):
        modules, *classes = self.runtime_modules()
        homes = {
            "oauth-home:personal": "/tmp/personal-codex",
            "oauth-home:work": "/tmp/work-codex",
        }
        adapter = HindsightProviderAdapter(
            ProviderRuntimePolicy.load(policy_data()),
            credential_resolver=homes.__getitem__,
            version_resolver=lambda: "0.8.4",
        )
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertTrue(adapter.install())
        return classes

    def test_install_resolves_independent_oauth_homes_and_exact_provider_policy(self) -> None:
        LLMProvider, CodexLLM, _MultiLLMProvider = self.install()

        prior = "/tmp/original-codex"
        with mock.patch.dict(os.environ, {"CODEX_HOME": prior}, clear=False):
            personal = CodexLLM(
                provider="openai-codex",
                api_key="provider-policy:personal",
                base_url="",
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

        self.assertEqual((fallback.timeout, fallback.max_retries), (300, 1))
        self.assertEqual(fallback._provider_impl._client.timeout, 300)
        self.assertEqual((nearby.timeout, nearby.max_retries), (30, 7))
        self.assertEqual(
            asyncio.run(fallback.call(max_retries=7))["max_retries"], 1
        )
        self.assertEqual(
            asyncio.run(nearby.call(max_retries=7))["max_retries"], 7
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
