from pathlib import Path
import argparse
from dataclasses import replace
import sys
import tempfile
import unittest
import runpy
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.canonical import digest  # noqa: E402
from hindsight_memory_control_plane.broker import (  # noqa: E402
    DEFAULT_SESSION_TTL_SECONDS,
    MAX_SESSION_TTL_SECONDS,
)
from hindsight_memory_control_plane.inventory import load_inventory  # noqa: E402
from hindsight_memory_control_plane.model import deep_thaw  # noqa: E402
from hindsight_memory_control_plane.runtime import (  # noqa: E402
    CONTROLLER_INTEGRATION_AUTHORITY_DIGEST,
    RuntimeConfigurationError,
    compile_runtime_configuration,
    compile_runtime_status,
)


def inventory_json(root=None):
    root = Path(root or tempfile.gettempdir())
    return {
        "schema_version": 1,
        "machine": {
            "id": "test-machine",
            "base_port": 7979,
            "engineering_memory_enabled": True,
        },
        "archetype": {"id": "trusted-workstation"},
        "profiles": [{
            "id": "core", "slot": 0, "enabled": True,
            "host": "127.0.0.1", "data_classes": ["engineering"],
            "roles": {},
        }],
        "providers": [],
        "banks": [{
            "id": "engineering", "profile_id": "core",
            "data_class": "engineering", "authority": "authoritative",
            "writable": True,
        }],
        "harnesses": [
            {
                "id": harness, "profile_id": "core",
                "home_bank": {"profile_id": "core", "bank_id": "engineering"},
                "write_bank": {"profile_id": "core", "bank_id": "engineering"},
            }
            for harness in ("claude-code", "codex", "cursor")
        ],
        "migration": {
            "artifact_dir": str(root / "hindsight-artifacts"),
            "proposal_log": str(root / "hindsight-proposals.md"),
        },
        "policy": {
            "engineering_memory_enabled": True,
            "allowed_placements": {"engineering": ["local"]},
        },
    }


class RecordingAdapter:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    def verify_runtime_compatibility(self):
        return None


class RuntimeConfigurationTest(unittest.TestCase):
    def setUp(self):
        RecordingAdapter.instances = []
        self.temporary = tempfile.TemporaryDirectory()
        path = Path(self.temporary.name) / "inventory.json"
        import json
        path.write_text(
            json.dumps(inventory_json(self.temporary.name)), encoding="utf-8"
        )
        self.inventory = load_inventory(path)

    def tearDown(self):
        self.temporary.cleanup()

    def compile(self):
        return compile_runtime_configuration(
            inventory=self.inventory,
            profiles=("core",),
            token_resolver=lambda: "data-token",
            mint_authority_resolver=lambda: "control-capability",
            token_resolver_id="TEST_DATA_TOKEN",
            mint_authority_resolver_id="TEST_MINT_AUTHORITY",
            adapter_factory=RecordingAdapter,
        )

    def test_compiles_one_exact_bank_scoped_route_per_harness(self):
        configuration = self.compile()

        self.assertEqual(set(configuration.routes), {
            "claude-code", "codex", "cursor",
        })
        for harness_id, route in configuration.routes.items():
            self.assertEqual(
                route["bank"],
                {
                    "profile_id": "core", "bank_id": "engineering",
                    "endpoint": {
                        "profile_id": "core", "scheme": "http",
                        "host": "127.0.0.1", "port": 7979,
                        "tenant": "default",
                    },
                },
            )
            self.assertEqual(route["adapter"].kwargs["runtime_harness_id"], harness_id)
            self.assertEqual(route["adapter"].kwargs["runtime_bank_id"], "engineering")
            self.assertIs(route["adapter"].kwargs["inventory"], self.inventory)
        self.assertEqual(configuration.status["mode"], "active")
        self.assertEqual(configuration.status["profiles"], ("core",))
        self.assertEqual(configuration.status["routes"], (
            "claude-code", "codex", "cursor",
        ))
        for key in (
            "policy_digest", "artifact_digest", "route_digest",
            "profile_set_digest",
        ):
            self.assertRegex(configuration.status[key], r"^[0-9a-f]{64}$")

    def test_integration_authority_generation_is_bound_into_runtime_identity(self):
        baseline = self.compile()
        self.assertEqual(
            baseline.integration_authority_digest,
            CONTROLLER_INTEGRATION_AUTHORITY_DIGEST,
        )
        certified = compile_runtime_configuration(
            inventory=self.inventory,
            profiles=("core",),
            token_resolver=lambda: "data-token",
            mint_authority_resolver=lambda: "control-capability",
            token_resolver_id="TEST_DATA_TOKEN",
            mint_authority_resolver_id="TEST_MINT_AUTHORITY",
            integration_authority_digest="a" * 64,
            adapter_factory=RecordingAdapter,
        )

        self.assertEqual(certified.status["integration_authority_digest"], "a" * 64)
        self.assertEqual(
            compile_runtime_status(
                inventory=self.inventory,
                profiles=("core",),
                token_resolver_id="TEST_DATA_TOKEN",
                mint_authority_resolver_id="TEST_MINT_AUTHORITY",
                integration_authority_digest="a" * 64,
            ),
            certified.status,
        )
        for name in (
            "route_digest",
            "policy_digest",
            "artifact_digest",
            "profile_set_digest",
        ):
            self.assertNotEqual(getattr(baseline, name), getattr(certified, name))
        authorized = certified.mint_authorizer(
            "control-capability",
            {
                "session_id": "session-1",
                "companion_id": "bridge-1",
                "route": "codex",
            },
            60,
        )
        self.assertEqual(authorized["artifact_digest"], certified.artifact_digest)

        for invalid in (
            1,
            b"a" * 64,
            "",
            "a" * 63,
            "A" * 64,
            "a" * 64 + "\n",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                RuntimeConfigurationError, "integration authority digest is invalid"
            ):
                compile_runtime_status(
                    inventory=self.inventory,
                    profiles=("core",),
                    token_resolver_id="TEST_DATA_TOKEN",
                    mint_authority_resolver_id="TEST_MINT_AUTHORITY",
                    integration_authority_digest=invalid,
                )
            with self.subTest(invalid_configuration=invalid), self.assertRaisesRegex(
                RuntimeConfigurationError, "integration authority digest is invalid"
            ):
                compile_runtime_configuration(
                    inventory=self.inventory,
                    profiles=("core",),
                    token_resolver=lambda: "data-token",
                    mint_authority_resolver=lambda: "control-capability",
                    token_resolver_id="TEST_DATA_TOKEN",
                    mint_authority_resolver_id="TEST_MINT_AUTHORITY",
                    integration_authority_digest=invalid,
                    adapter_factory=RecordingAdapter,
                )

    def test_mint_authority_constructs_compiled_route_authority(self):
        configuration = self.compile()
        requested = {
            "session_id": "session-1",
            "companion_id": "bridge-1",
            "route": "codex",
        }

        authorized = configuration.mint_authorizer(
            "control-capability", requested, 60
        )
        self.assertEqual(authorized, {
            **requested,
            "harness_id": "codex",
            "home_bank": deep_thaw(configuration.routes["codex"]["bank"]),
            "trust_class": "local",
            "policy_digest": configuration.policy_digest,
            "artifact_digest": configuration.artifact_digest,
            "methods": list(configuration.methods),
        })
        self.assertEqual(
            configuration.mint_authorizer(
                "control-capability", requested, DEFAULT_SESSION_TTL_SECONDS
            ),
            authorized,
        )
        self.assertEqual(
            configuration.mint_authorizer(
                "control-capability", requested, MAX_SESSION_TTL_SECONDS
            ),
            authorized,
        )
        self.assertEqual(
            configuration.mint_authorizer(
                "control-capability", requested, MAX_SESSION_TTL_SECONDS + 1
            ),
            {},
        )
        for changed in (
            {**requested, "route": "uncompiled"},
            {
                **requested,
                "home_bank": {
                    "profile_id": "core", "bank_id": "engineering",
                },
            },
            {**requested, "methods": ["admin"]},
            {**requested, "policy_digest": digest({"drift": True})},
            {**requested, "artifact_digest": digest({"drift": True})},
            {**requested, "harness_id": "cursor"},
            {**requested, "trust_class": "remote"},
        ):
            with self.subTest(changed=changed):
                self.assertEqual(
                    configuration.mint_authorizer(
                        "control-capability", changed, 60
                    ),
                    {},
                )
        self.assertEqual(
            configuration.mint_authorizer("wrong", requested, 60), {}
        )
        self.assertEqual(
            configuration.mint_authorizer("contrôl", requested, 60), {}
        )
        self.assertEqual(
            configuration.mint_authorizer(
                "control-capability", requested, 10 ** 10_000
            ),
            {},
        )

    def test_runtime_rejects_a_shared_credential_resolver_locator(self):
        with self.assertRaisesRegex(
            RuntimeConfigurationError, "resolver locators must be distinct"
        ):
            compile_runtime_configuration(
                inventory=self.inventory,
                profiles=("core",),
                token_resolver=lambda: "data-token",
                mint_authority_resolver=lambda: "control-capability",
                token_resolver_id="SHARED_AUTHORITY",
                mint_authority_resolver_id="SHARED_AUTHORITY",
                adapter_factory=RecordingAdapter,
            )

    def test_runtime_rejects_non_environment_credential_resolver_locators(self):
        for invalid in (
            "lowercase",
            "1STARTS_WITH_A_DIGIT",
            "CONTAINS-DASH",
            "UNICODE_É",
            "A" * 129,
        ):
            with self.subTest(locator=invalid), self.assertRaisesRegex(
                RuntimeConfigurationError, "resolver locator is invalid"
            ):
                compile_runtime_configuration(
                    inventory=self.inventory,
                    profiles=("core",),
                    token_resolver=lambda: "data-token",
                    mint_authority_resolver=lambda: "control-capability",
                    token_resolver_id=invalid,
                    mint_authority_resolver_id="TEST_MINT_AUTHORITY",
                    adapter_factory=RecordingAdapter,
                )

    def test_mint_authority_resolver_failure_is_logged_without_secret_text(self):
        def failed_resolver():
            raise RuntimeError("private-mint-authority")

        configuration = compile_runtime_configuration(
            inventory=self.inventory,
            profiles=("core",),
            token_resolver=lambda: "data-token",
            mint_authority_resolver=failed_resolver,
            token_resolver_id="TEST_DATA_TOKEN",
            mint_authority_resolver_id="TEST_MINT_AUTHORITY",
            adapter_factory=RecordingAdapter,
        )
        requested = {
            "session_id": "session-1",
            "companion_id": "bridge-1",
            "route": "codex",
        }

        with self.assertLogs(
            "hindsight_memory_control_plane.runtime", level="WARNING"
        ) as logs:
            self.assertEqual(
                configuration.mint_authorizer("control-capability", requested, 60),
                {},
            )
        rendered = "\n".join(logs.output)
        self.assertIn("RuntimeError", rendered)
        self.assertNotIn("private-mint-authority", rendered)

    def test_compiled_routes_and_status_are_recursively_immutable(self):
        configuration = self.compile()

        with self.assertRaises(TypeError):
            configuration.routes["codex"] = {}
        with self.assertRaises(TypeError):
            configuration.routes["codex"]["bank"] = {}
        with self.assertRaises(TypeError):
            configuration.routes["codex"]["bank"]["bank_id"] = "other"
        with self.assertRaises(AttributeError):
            configuration.status["routes"].append("other")

    def test_requires_a_validated_selected_profile_and_harness_route(self):
        with self.assertRaisesRegex(RuntimeConfigurationError, "selected profile"):
            compile_runtime_configuration(
                inventory=self.inventory,
                profiles=("missing",),
                token_resolver=lambda: "token",
                mint_authority_resolver=lambda: "control",
                token_resolver_id="TEST_DATA_TOKEN",
                mint_authority_resolver_id="TEST_MINT_AUTHORITY",
                adapter_factory=RecordingAdapter,
            )

    def test_rejects_an_inventory_harness_without_a_supported_adapter(self):
        raw = inventory_json(self.temporary.name)
        raw["harnesses"][0]["id"] = "future-harness"
        path = Path(self.temporary.name) / "unsupported.json"
        import json
        path.write_text(json.dumps(raw), encoding="utf-8")
        unsupported = load_inventory(path)
        with self.assertRaisesRegex(
            RuntimeConfigurationError, "harness adapter is unsupported"
        ):
            compile_runtime_configuration(
                inventory=unsupported,
                profiles=("core",),
                token_resolver=lambda: "token",
                mint_authority_resolver=lambda: "control",
                token_resolver_id="TEST_DATA_TOKEN",
                mint_authority_resolver_id="TEST_MINT_AUTHORITY",
                adapter_factory=RecordingAdapter,
            )

    def test_rejects_noncanonical_automatic_write_banks(self):
        for bank_id, data_class in (
            ("personal", "personal"),
            ("engineering-shadow", "engineering"),
        ):
            with self.subTest(bank_id=bank_id):
                raw = inventory_json(self.temporary.name)
                alternate_bank = {
                    "id": bank_id,
                    "profile_id": "core",
                    "data_class": data_class,
                    "authority": "authoritative",
                    "writable": True,
                }
                if data_class == "engineering":
                    raw["banks"] = [alternate_bank]
                else:
                    raw["banks"].append(alternate_bank)
                for harness in raw["harnesses"]:
                    harness["home_bank"] = {
                        "profile_id": "core", "bank_id": bank_id,
                    }
                    harness["write_bank"] = {
                        "profile_id": "core", "bank_id": bank_id,
                    }
                path = Path(self.temporary.name) / f"{bank_id}.json"
                import json
                path.write_text(json.dumps(raw), encoding="utf-8")

                with self.assertRaisesRegex(
                    RuntimeConfigurationError, "canonical engineering bank"
                ):
                    compile_runtime_configuration(
                        inventory=load_inventory(path),
                        profiles=("core",),
                        token_resolver=lambda: "token",
                        mint_authority_resolver=lambda: "control",
                        token_resolver_id="TEST_DATA_TOKEN",
                        mint_authority_resolver_id="TEST_MINT_AUTHORITY",
                        adapter_factory=RecordingAdapter,
                    )

        canonical = deep_thaw(self.inventory.banks[0])
        for field, value in (
            ("data_class", "personal"),
            ("writable", False),
        ):
            with self.subTest(canonical_field=field):
                forged = replace(
                    self.inventory,
                    banks=({**canonical, field: value},),
                )
                with self.assertRaisesRegex(
                    RuntimeConfigurationError, "canonical engineering bank"
                ):
                    compile_runtime_configuration(
                        inventory=forged,
                        profiles=("core",),
                        token_resolver=lambda: "token",
                        mint_authority_resolver=lambda: "control",
                        token_resolver_id="TEST_DATA_TOKEN",
                        mint_authority_resolver_id="TEST_MINT_AUTHORITY",
                        adapter_factory=RecordingAdapter,
                    )

    def test_rejects_duplicate_harness_routes_in_a_forged_inventory(self):
        forged = replace(
            self.inventory,
            harnesses=(
                *self.inventory.harnesses,
                self.inventory.harnesses[0],
            ),
        )

        with self.assertRaisesRegex(
            RuntimeConfigurationError, "duplicate harness route"
        ):
            compile_runtime_configuration(
                inventory=forged,
                profiles=("core",),
                token_resolver=lambda: "token",
                mint_authority_resolver=lambda: "control",
                token_resolver_id="TEST_DATA_TOKEN",
                mint_authority_resolver_id="TEST_MINT_AUTHORITY",
                adapter_factory=RecordingAdapter,
            )

    def test_rejects_cross_profile_harness_bank_bindings(self):
        canonical_bank = deep_thaw(self.inventory.banks[0])
        cross_profile_bank = {
            **canonical_bank, "profile_id": "other",
        }
        harnesses = [deep_thaw(item) for item in self.inventory.harnesses]
        harnesses[0]["home_bank"] = {
            "profile_id": "other", "bank_id": "engineering",
        }
        harnesses[0]["write_bank"] = dict(harnesses[0]["home_bank"])
        forged = replace(
            self.inventory,
            banks=(*self.inventory.banks, cross_profile_bank),
            harnesses=tuple(harnesses),
        )

        with self.assertRaisesRegex(
            RuntimeConfigurationError, "profile-bound"
        ):
            compile_runtime_configuration(
                inventory=forged,
                profiles=("core",),
                token_resolver=lambda: "token",
                mint_authority_resolver=lambda: "control",
                token_resolver_id="TEST_DATA_TOKEN",
                mint_authority_resolver_id="TEST_MINT_AUTHORITY",
                adapter_factory=RecordingAdapter,
            )

    def test_profile_set_digest_binds_non_secret_resolver_locators(self):
        baseline = self.compile().profile_set_digest
        changed = compile_runtime_configuration(
            inventory=self.inventory,
            profiles=("core",),
            token_resolver=lambda: "data-token",
            mint_authority_resolver=lambda: "control-capability",
            token_resolver_id="OTHER_DATA_TOKEN",
            mint_authority_resolver_id="TEST_MINT_AUTHORITY",
            adapter_factory=RecordingAdapter,
        )
        self.assertNotEqual(changed.profile_set_digest, baseline)

    def test_status_compilation_matches_runtime_without_constructing_adapters(self):
        status = compile_runtime_status(
            inventory=self.inventory,
            profiles=("core",),
            token_resolver_id="TEST_DATA_TOKEN",
            mint_authority_resolver_id="TEST_MINT_AUTHORITY",
        )

        self.assertEqual(RecordingAdapter.instances, [])
        configuration = self.compile()
        self.assertEqual(status, configuration.status)

    def test_cli_status_selection_does_not_compile_runtime_adapters(self):
        module = runpy.run_path(str(ROOT / "bin/hindsight-memory"))
        path = Path(self.temporary.name) / "status-inventory.json"
        import json
        path.write_text(
            json.dumps(inventory_json(self.temporary.name)), encoding="utf-8"
        )
        args = argparse.Namespace(
            inactive=False,
            inventory=str(path),
            profile=["core"],
            data_plane_token_env="TEST_HINDSIGHT_DATA_TOKEN",
            mint_authority_env="TEST_HINDSIGHT_MINT_AUTHORITY",
        )

        with patch.dict(
            module,
            {
                "compile_runtime_configuration": Mock(
                    side_effect=AssertionError("constructed runtime adapters")
                )
            },
        ):
            status = module["_broker_selected_runtime_status"](args)

        self.assertEqual(status["mode"], "active")
        self.assertEqual(status["profiles"], ("core",))

    def test_cli_binds_secret_environment_locators_without_reporting_values(self):
        module = runpy.run_path(str(ROOT / "bin/hindsight-memory"))
        path = Path(self.temporary.name) / "inventory.json"
        import json
        path.write_text(
            json.dumps(inventory_json(self.temporary.name)), encoding="utf-8"
        )
        args = argparse.Namespace(
            inventory=str(path),
            profile=["core"],
            data_plane_token_env="TEST_HINDSIGHT_DATA_TOKEN",
            mint_authority_env="TEST_HINDSIGHT_MINT_AUTHORITY",
        )
        compile_configuration = module["compile_runtime_configuration"]

        def compile_without_network(**kwargs):
            return compile_configuration(
                **kwargs, adapter_factory=RecordingAdapter
            )

        with patch.dict(
            module["_broker_runtime_configuration"].__globals__,
            {"compile_runtime_configuration": compile_without_network},
        ):
            with patch.dict(
                module["os"].environ,
                {
                    "TEST_HINDSIGHT_DATA_TOKEN": "private-data-token",
                    "TEST_HINDSIGHT_MINT_AUTHORITY": "private-mint-authority",
                },
                clear=False,
            ):
                configuration = module["_broker_runtime_configuration"](args)
            rendered = json.dumps(dict(configuration.status), sort_keys=True)
            with patch.dict(
                module["os"].environ, {}, clear=False
            ) as scoped_env:
                scoped_env.pop("TEST_HINDSIGHT_DATA_TOKEN", None)
                scoped_env.pop("TEST_HINDSIGHT_MINT_AUTHORITY", None)
                with self.assertRaisesRegex(
                    module["BrokerError"],
                    "BROKER_DATA_PLANE_TOKEN_UNAVAILABLE",
                ):
                    module["_broker_runtime_configuration"](args)
        self.assertNotIn("private-data-token", rendered)
        self.assertNotIn("private-mint-authority", rendered)
        self.assertEqual(configuration.status["mode"], "active")

    def test_cli_rejects_distinct_resolvers_with_the_same_credential(self):
        module = runpy.run_path(str(ROOT / "bin/hindsight-memory"))
        path = Path(self.temporary.name) / "inventory.json"
        import json
        path.write_text(
            json.dumps(inventory_json(self.temporary.name)), encoding="utf-8"
        )
        args = argparse.Namespace(
            inventory=str(path),
            profile=["core"],
            data_plane_token_env="TEST_HINDSIGHT_DATA_TOKEN",
            mint_authority_env="TEST_HINDSIGHT_MINT_AUTHORITY",
        )
        compile_configuration = module["compile_runtime_configuration"]

        def compile_without_network(**kwargs):
            return compile_configuration(
                **kwargs, adapter_factory=RecordingAdapter
            )

        with patch.dict(
            module["_broker_runtime_configuration"].__globals__,
            {"compile_runtime_configuration": compile_without_network},
        ), patch.dict(
            module["os"].environ,
            {
                "TEST_HINDSIGHT_DATA_TOKEN": "shared-private-value",
                "TEST_HINDSIGHT_MINT_AUTHORITY": "shared-private-value",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(
                module["BrokerError"],
                "BROKER_RUNTIME_CREDENTIAL_SEPARATION_INVALID",
            ):
                module["_broker_runtime_configuration"](args)

    def test_cli_derives_integration_authority_from_certified_state(self):
        module = runpy.run_path(str(ROOT / "bin/hindsight-memory"))
        path = Path(self.temporary.name) / "authority-inventory.json"
        import json
        path.write_text(
            json.dumps(inventory_json(self.temporary.name)), encoding="utf-8"
        )
        args = argparse.Namespace(
            inventory=str(path),
            profile=["core"],
            data_plane_token_env="TEST_HINDSIGHT_DATA_TOKEN",
            mint_authority_env="TEST_HINDSIGHT_MINT_AUTHORITY",
            integration_upgrade_state=str(
                Path(self.temporary.name) / "integration-upgrades"
            ),
        )
        compile_configuration = module["compile_runtime_configuration"]

        def compile_without_network(**kwargs):
            return compile_configuration(
                **kwargs, adapter_factory=RecordingAdapter
            )

        authority_digest = "a" * 64
        authority_reader = Mock(return_value=authority_digest)
        with patch.dict(
            module["_broker_runtime_configuration"].__globals__,
            {
                "compile_runtime_configuration": compile_without_network,
                "read_integration_authority_set_digest": authority_reader,
            },
        ), patch.dict(
            module["os"].environ,
            {
                "TEST_HINDSIGHT_DATA_TOKEN": "private-data-token",
                "TEST_HINDSIGHT_MINT_AUTHORITY": "private-mint-authority",
            },
            clear=False,
        ):
            configuration = module["_broker_runtime_configuration"](args)

        authority_reader.assert_called_once_with(
            args.integration_upgrade_state,
            ("claude-code", "codex", "cursor"),
        )
        self.assertEqual(
            configuration.status["integration_authority_digest"],
            authority_digest,
        )

    def test_cli_parser_rejects_mixed_or_partial_runtime_bindings(self):
        module = runpy.run_path(str(ROOT / "bin/hindsight-memory"))
        argument_parser = module["parser"]()
        common = [
            "--state-dir", self.temporary.name,
            "broker", "status", "--socket", "/tmp/broker.sock",
            "--profile", "core",
        ]
        invalid = (
            [*common, "--inactive", "--inventory", "/tmp/inventory.json"],
            [*common, "--inventory", "/tmp/inventory.json"],
            [*common, "--data-plane-token-env", "DATA_TOKEN"],
        )
        for argv in invalid:
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                args = argument_parser.parse_args(argv)
                module["_validate_broker_binding_arguments"](
                    argument_parser, args
                )

        valid = argument_parser.parse_args([*common, "--inactive"])
        module["_validate_broker_binding_arguments"](
            argument_parser, valid
        )

        with self.assertRaises(SystemExit):
            argument_parser.parse_args(
                [*common, "--integration-authority-digest", "a" * 64]
            )
        invalid_state = argument_parser.parse_args(
            [
                *common,
                "--inactive",
                "--integration-upgrade-state",
                "a" * 64,
            ]
        )
        with self.assertRaises(SystemExit):
            module["_validate_broker_binding_arguments"](
                argument_parser, invalid_state
            )


if __name__ == "__main__":
    unittest.main()
